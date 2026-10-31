"""Sleep-time engagement memory: record the visit, write one 上次聊天 fact per person.

`record_transcript` is called by the realtime handler at its final-text push
sites; `write_sleep_summaries` (Task 4) runs once at handler shutdown. A visit
it could not summarize is kept and summarized at the next start
(`start_pending_summary`, from `main.run`).
"""

from __future__ import annotations
import os
import json
import time
import asyncio
import logging
import threading
from typing import Any, Final
from pathlib import Path

from reachy_companion.memory import normalize_memory_text
from reachy_companion.people import (
    MAX_FACTS_PER_PERSON,
    add_person_fact,
    facts_for_person,
    forget_person_fact,
)
from reachy_companion.audio.envparse import env_bool, env_float
from reachy_companion.tools.core_tools import ToolDependencies


logger = logging.getLogger(__name__)

# Bound on the visit tail held in ToolDependencies.session_transcript. The deque
# is built there with a literal maxlen — core_tools cannot import this module
# without a cycle (Tool classes import core_tools) — so keep the two in step.
TRANSCRIPT_MAX_ITEMS: Final[int] = 40
LAST_CHAT_PREFIX: Final[str] = "上次聊天"

_SYSTEM_PROMPT: Final[str] = (
    "你是機器人 Reachy 的記憶整理員。根據這次的對話記錄，為列出的每個人各寫一句"
    "「上次聊天」摘要（不超過 50 字，臺灣繁體中文）。優先寫：聊了什麼主題、"
    "還沒有結果的事（考試、計畫、承諾）。只根據記錄，不要編造。"
    "記錄裡看不出是誰說的：多人在場時寫「大家聊了什麼」的主題摘要，"
    "除非記錄裡明白寫出名字，否則不要把某句話歸給特定的人。"
    '只輸出 JSON 物件：{"人名": "摘要"}；名單外的人不要出現；沒有內容可寫的人給空字串。'
)


def touch_presence(deps: ToolDependencies, stamp: float) -> None:
    """Refresh the current speaker's sighting stamp, because talking is presence.

    Without it, `write_sleep_summaries`' window would exclude the commonest case
    it should keep: one person recognized at the boot greeting who then talks
    past 40 lines, pushing their own recognition out of the retained tail. Only a
    person already on the guest list is refreshed — `current_person` is a label,
    and a label alone is not a sighting.

    The label is not always there to follow. `_run_realtime_session` clears it on
    **every** session, reconnects included (`huggingface_realtime.py:1982`), and
    the wake checks that set it are one-shot per handler — so a dropped websocket
    leaves a live visit with no label at all. When exactly ONE person is known
    this run, the talking can only be theirs, so the heartbeat follows them
    instead. With two or more known and no label it refreshes nobody: guessing
    whose line this is would recreate the attribution leak the window closes.

    The one heartbeat, called from both recorders. `record_transcript` below is
    the ordinary path; `record_mode.record_room_transcript` is 紀錄模式's, where
    the answer gate denies meeting speech and this function would otherwise never
    run for a whole meeting — a room full of talking would look like an empty
    room to the sleep summary.
    """
    speaker = deps.current_person
    if speaker is None and len(deps.recognized_at) == 1:
        speaker = next(iter(deps.recognized_at))
    if speaker is not None and speaker in deps.recognized_at:
        deps.recognized_at[speaker] = stamp


def record_transcript(deps: ToolDependencies, role: str, text: str) -> None:
    """Append one finalized utterance, stamped, to the bounded session tail.

    Recording also refreshes the speaker's sighting stamp (`touch_presence`).
    """
    cleaned = text.strip()
    if not cleaned or cleaned.startswith("[error]"):
        return
    stamp = time.monotonic()
    deps.session_transcript.append((role, cleaned, stamp))
    touch_presence(deps, stamp)


def _default_model() -> str:
    """Return the summarizer model — a small one; this runs after the visit ends."""
    return os.getenv("MEMORY_LAST_CHAT_MODEL", "").strip() or "gpt-5-mini"


def format_last_chat_fact(summary: str, when: float | None = None) -> str:
    """`上次聊天（M月D日）：<summary>` — the prefix is the supersession key.

    *when* (epoch seconds) dates the fact by the visit rather than by today: a
    visit kept by a failed sleep is summarized at the next start, which may be
    the next day (W5.4).
    """
    stamp = time.strftime("%m月%d日", time.localtime(when)).lstrip("0").replace("月0", "月")
    return f"{LAST_CHAT_PREFIX}（{stamp}）：{summary.strip()}"


def _people_in_window(deps: ToolDependencies, transcript: list[tuple[str, str, float]]) -> list[str]:
    """Return the guests the retained transcript can honestly speak for.

    `recognized_people` spans the whole app run; `session_transcript` holds only
    its last `TRANSCRIPT_MAX_ITEMS` lines. Summarizing the second against the
    first attributes whoever is here tonight to whoever was here this morning —
    their 上次聊天 fact gets somebody else's topics, and the next recognized
    greeting reads it back to them.

    So the filter is the tail's own reach. While the tail is **not yet full** it
    covers the whole run and nobody is filtered: every guest's own lines are
    still in it. Once it is full, a guest is kept only if last seen at-or-after
    the oldest retained line — everything they said is otherwise about to be, or
    already, gone. The test is `len(transcript) == maxlen`, which is one line
    *ahead* of the first real eviction: at exactly `maxlen` nothing has dropped
    yet, but the next line will drop one, and starting to filter a line early is
    the fail-closed direction — the mistake it avoids is somebody else's evening
    written into a person's memory.

    Two deliberate softenings, both fail-open:
      - `record_transcript` refreshes the speaker's stamp, so the visit's own
        speaker is never filtered out by the length of their own conversation —
        the boot recognition scrolls out of a long visit, they do not;
      - a name in the set with no stamp behind it is kept: no stamp is no
        evidence of staleness, and every production site stamps through
        `ToolDependencies.record_recognition`.
    """
    maxlen = deps.session_transcript.maxlen
    if maxlen is None or len(transcript) < maxlen:
        return sorted(deps.recognized_people)
    oldest = transcript[0][2]
    return sorted(name for name in deps.recognized_people if deps.recognized_at.get(name, oldest) >= oldest)


async def write_sleep_summaries(deps: ToolDependencies, *, client: Any | None = None) -> int:
    """Summarize the visit for everyone the retained tail still covers. Never raises.

    When the summarizer cannot be reached — both attempts time out, or the
    network fails outright — the visit is kept in the instance dir and
    summarized at the next start (`summarize_pending_visits`), so a flaky
    network at bedtime no longer costs the visit its 上次聊天.
    """
    try:
        if not env_bool("MEMORY_LAST_CHAT_ENABLED", True):
            logger.info("Sleep summary disabled by MEMORY_LAST_CHAT_ENABLED.")
            return 0
        transcript = list(deps.session_transcript)
        if not transcript:
            return 0
        names = _people_in_window(deps, transcript)
        if len(names) < len(deps.recognized_people):
            # Said out loud, because the on-robot check for this is a journal
            # read: a visitor from earlier today going unsummarized is the guard
            # working, and it must not look like a lost fact.
            logger.info(
                "Sleep summary: %d of %d recognized people are outside the retained transcript window.",
                len(deps.recognized_people) - len(names),
                len(deps.recognized_people),
            )
        if not names:
            return 0
        lines = [(role, text) for role, text, _ in transcript]
        visited_at = time.time()
        written = await _summarize_visit(deps.instance_path, names, lines, visited_at, client=client)
        if written is None:
            await asyncio.to_thread(_save_pending_visit, deps.instance_path, names, lines, visited_at)
            return 0
        return written
    except Exception as exc:  # noqa: BLE001 — memory must never break shutdown
        logger.warning("Sleep summary failed: %s", type(exc).__name__)
        return 0


async def _summarize_visit(
    instance_path: str | Path | None,
    names: list[str],
    lines: list[tuple[str, str]],
    visited_at: float,
    *,
    client: Any | None,
) -> int | None:
    """Ask the summarizer once (plus one timeout retry) and write the facts.

    The one code path for both a visit that just ended and one kept from an
    earlier sleep. Returns the number of facts written, or None when the
    summarizer could not be reached (both attempts timed out, or a transient
    network error), i.e. when the visit is worth keeping for a later try.
    Anything else raises to the caller, which logs it and gives up.
    """
    if client is None:
        from reachy_companion.hanova.images import build_client

        client = build_client()
    if client is None:
        logger.info("Sleep summary skipped: no OpenAI client available.")
        return 0
    joined = "\n".join(f"{'user' if role == 'user' else 'reachy'}: {text}" for role, text in lines)
    user_prompt = f"在場的人：{'、'.join(names)}\n\n對話記錄：\n{joined}"
    timeout_s = env_float("MEMORY_LAST_CHAT_TIMEOUT_S", 8.0, lo=1.0, hi=30.0)
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    async with client:
        try:
            try:
                response = await asyncio.wait_for(
                    client.chat.completions.create(
                        model=_default_model(),
                        messages=messages,
                        response_format={"type": "json_object"},
                    ),
                    timeout=timeout_s,
                )
            except TimeoutError:
                retry_timeout_s = 4.0
                # plan rev 3 C3: retry only the transient wait_for timeout, with fixed added delay.
                logger.info(
                    "Sleep summary attempt 1/2 timed out with TimeoutError; "
                    "retrying attempt 2/2 with %.1fs timeout.",
                    retry_timeout_s,
                )
                try:
                    response = await asyncio.wait_for(
                        client.chat.completions.create(
                            model=_default_model(),
                            messages=messages,
                            response_format={"type": "json_object"},
                        ),
                        timeout=retry_timeout_s,
                    )
                except TimeoutError:
                    logger.warning("Sleep summary attempt 2/2 timed out with TimeoutError; giving up.")
                    return None
        except _TRANSIENT_ERRORS as exc:
            logger.warning("Sleep summary could not reach the summarizer (%s); giving up.", type(exc).__name__)
            return None
    raw = response.choices[0].message.content or ""
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        logger.warning("Sleep summary: model returned non-object JSON; skipping.")
        return 0
    written = 0
    for name in names:
        summary = parsed.get(name)
        if not isinstance(summary, str) or not summary.strip():
            continue
        written += await asyncio.to_thread(_replace_last_chat_fact, instance_path, name, summary, visited_at)
    return written


def _forget_key(text: str) -> str:
    """Return the exact key `forget_person_fact` matches on (people.py:394, 405)."""
    return normalize_memory_text(text).lower()


def _replace_last_chat_fact(
    instance_path: str | Path | None, name: str, summary: str, visited_at: float | None = None
) -> int:
    """Leave exactly one 上次聊天 fact for this person: today's.

    The default order is ADD first, then forget the stale copies — a failure
    between the two leaves a duplicate 上次聊天 fact, never zero of them.

    Every guard below is keyed through `_forget_key`, because that is what
    `forget_person_fact` itself matches on: a case-insensitive, whitespace-
    collapsed SUBSTRING test that removes the NEWEST candidate
    (people.py:394, 405). Comparing raw text instead misses case- and
    whitespace-only variants, and the post-add forget then deletes the fact we
    just wrote.
    """
    facts = facts_for_person(instance_path, name)
    old_texts = [f.text for f in facts if f.text.startswith(LAST_CHAT_PREFIX)]
    new_text = format_last_chat_fact(summary, visited_at)  # stamped once: never re-read the clock here
    new_key = _forget_key(new_text)
    # Two cases force forget-FIRST (tiny failure window; worst loss = last week's
    # callback, never a real fact):
    #  - at the 20-fact cap, add-first would evict a REAL fact;
    #  - an old fact whose key is contained in the new one (same-day re-sleep,
    #    identical text being the degenerate case) would make the post-add
    #    forget delete the NEW fact instead of the old.
    forget_first = bool(old_texts) and (
        len(facts) >= MAX_FACTS_PER_PERSON or any(_forget_key(old) in new_key for old in old_texts)
    )
    if forget_first:
        for old in old_texts:
            forget_person_fact(instance_path, name, query=old)
    stored = add_person_fact(instance_path, name, new_text)
    if stored is None:
        return 0
    if not forget_first:
        stored_key = _forget_key(stored.text)
        for old in old_texts:
            if _forget_key(old) not in stored_key:
                forget_person_fact(instance_path, name, query=old)
    return 1


# --- a visit the sleep could not summarize (2026-10-31 plan, W5.4) -----------
# On 2026-10-31 the summarizer timed out twice at bedtime and the visit's
# 上次聊天 was simply lost. Such a visit is now kept in the instance dir — the
# same people and lines the summarizer was sent, bounded — and summarized in the
# background at the next start, then deleted. Personal data: it never leaves
# the instance dir, the file is 0600, and the journal gets counts only.
PENDING_SUMMARY_FILE: Final[str] = "pending_sleep_summary.json"
PENDING_MAX_VISITS: Final[int] = 3
PENDING_MAX_LINES: Final[int] = 200
PENDING_MAX_TEXT_BYTES: Final[int] = 32 * 1024
PENDING_MAX_AGE_S: Final[float] = 7 * 24 * 3600.0
# How long after startup the pending summary waits, so the wake greeting and
# the first realtime connection have the network to themselves.
PENDING_START_DELAY_S: Final[float] = 20.0
_PENDING_LOCK = threading.Lock()


def _transient_errors() -> tuple[type[BaseException], ...]:
    """Return the failures a later retry can cure: the network, not the request."""
    import httpx
    import openai

    return (
        TimeoutError,
        ConnectionError,
        httpx.TransportError,
        openai.APIConnectionError,
        openai.RateLimitError,
        openai.InternalServerError,
    )


_TRANSIENT_ERRORS: Final[tuple[type[BaseException], ...]] = _transient_errors()


def _pending_path(instance_path: str | Path) -> Path:
    return Path(instance_path) / PENDING_SUMMARY_FILE


def _bounded_lines(lines: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Keep the newest lines that fit `PENDING_MAX_LINES` and `PENDING_MAX_TEXT_BYTES`."""
    kept: list[tuple[str, str]] = []
    total = 0
    for role, text in reversed(lines[-PENDING_MAX_LINES:]):
        size = len(text.encode("utf-8"))
        if total + size > PENDING_MAX_TEXT_BYTES:
            break
        kept.append((role, text))
        total += size
    kept.reverse()
    return kept


def _read_pending_visits(path: Path) -> list[dict[str, Any]] | None:
    """Return the kept visits, [] when there is no file, None when it is unreadable."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError:
        return None
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None
    visits = parsed.get("visits") if isinstance(parsed, dict) else None
    if not isinstance(visits, list):
        return None
    good: list[dict[str, Any]] = []
    for visit in visits:
        if not isinstance(visit, dict):
            continue
        names, lines, saved_at = visit.get("names"), visit.get("lines"), visit.get("saved_at")
        if not isinstance(saved_at, (int, float)) or not isinstance(names, list) or not isinstance(lines, list):
            continue
        if not all(isinstance(name, str) for name in names):
            continue
        if not all(isinstance(line, list) and len(line) == 2 and all(isinstance(p, str) for p in line) for line in lines):
            continue
        good.append({"saved_at": float(saved_at), "names": list(names), "lines": [tuple(line) for line in lines]})
    return good


def _write_pending_visits(path: Path, visits: list[dict[str, Any]]) -> None:
    """Replace the file atomically (0600 `.part`, then rename), or delete it when empty."""
    if not visits:
        path.unlink(missing_ok=True)
        return
    payload = {
        "version": 1,
        "visits": [
            {"saved_at": v["saved_at"], "names": v["names"], "lines": [list(line) for line in v["lines"]]}
            for v in visits
        ],
    }
    part = path.with_name(path.name + ".part")
    try:
        fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
        # O_CREAT's mode only applies to a new file; a `.part` left by a crash keeps its own.
        os.chmod(part, 0o600)
        os.replace(part, path)
    except BaseException:
        part.unlink(missing_ok=True)
        raise


def _save_pending_visit(
    instance_path: str | Path | None, names: list[str], lines: list[tuple[str, str]], saved_at: float
) -> bool:
    """Keep a visit the sleep could not summarize. Never raises; True when kept."""
    if instance_path is None:
        logger.info("Sleep summary: no instance dir, so the unsummarized visit cannot be kept.")
        return False
    try:
        path = _pending_path(instance_path)
        with _PENDING_LOCK:
            visits = _read_pending_visits(path) or []
            visits.append({"saved_at": saved_at, "names": sorted(names), "lines": _bounded_lines(lines)})
            visits = visits[-PENDING_MAX_VISITS:]
            _write_pending_visits(path, visits)
        logger.info(
            "Sleep summary: kept the visit (%d people, %d lines) to summarize at the next start.",
            len(names),
            len(visits[-1]["lines"]),
        )
        return True
    except Exception as exc:  # noqa: BLE001 — memory must never break shutdown
        logger.warning("Sleep summary: could not keep the unsummarized visit: %s", type(exc).__name__)
        return False


def _load_pending_visits(path: Path) -> list[dict[str, Any]]:
    """Read the kept visits under the lock; an unreadable file is deleted."""
    with _PENDING_LOCK:
        visits = _read_pending_visits(path)
        if visits is None:
            logger.warning("Sleep summary: the kept visit file is unreadable; deleting it.")
            path.unlink(missing_ok=True)
            return []
        return visits


def _drop_pending_visits(path: Path, done: set[float]) -> None:
    """Remove the finished visits, re-reading first: a sleep may have kept one meanwhile."""
    with _PENDING_LOCK:
        current = _read_pending_visits(path) or []
        _write_pending_visits(path, [v for v in current if v["saved_at"] not in done])


async def summarize_pending_visits(
    instance_path: str | Path | None, *, client: Any | None = None, now: float | None = None
) -> int:
    """Summarize the visits earlier sleeps kept, and drop each once done. Never raises.

    A visit is dropped when it is summarized (even to zero facts: the model
    answered), when it is older than `PENDING_MAX_AGE_S`, or when the file is
    unreadable. One the summarizer still cannot be reached for, or that fails
    otherwise, stays for the next start. Returns the number of facts written.
    """
    if instance_path is None:
        return 0
    try:
        path = _pending_path(instance_path)
        visits = await asyncio.to_thread(_load_pending_visits, path)
        if not visits:
            return 0
        clock = time.time() if now is None else now
        fresh = [v for v in visits if clock - v["saved_at"] <= PENDING_MAX_AGE_S]
        expired = {v["saved_at"] for v in visits} - {v["saved_at"] for v in fresh}
        if expired:
            logger.info("Sleep summary: %d kept visit(s) older than 7 days dropped.", len(expired))
        summarized: set[float] = set()
        written = 0
        if not env_bool("MEMORY_LAST_CHAT_ENABLED", True):
            logger.info("Sleep summary disabled by MEMORY_LAST_CHAT_ENABLED; kept visits left as they are.")
        else:
            for visit in fresh:
                try:
                    result = await _summarize_visit(
                        instance_path, visit["names"], visit["lines"], visit["saved_at"], client=client
                    )
                except Exception as exc:  # noqa: BLE001 — kept for the next start
                    logger.warning("Sleep summary of a kept visit failed: %s", type(exc).__name__)
                    continue
                if result is not None:
                    written += result
                    summarized.add(visit["saved_at"])
        if expired or summarized:
            await asyncio.to_thread(_drop_pending_visits, path, expired | summarized)
        logger.info(
            "Sleep summary: %d kept visit(s) summarized at startup, %d fact(s) written, %d still kept.",
            len(summarized),
            written,
            len(fresh) - len(summarized),
        )
        return written
    except Exception as exc:  # noqa: BLE001 — memory must never break startup
        logger.warning("Sleep summary of kept visits failed: %s", type(exc).__name__)
        return 0


def start_pending_summary(
    instance_path: str | Path | None, *, delay_s: float = PENDING_START_DELAY_S
) -> threading.Thread | None:
    """Summarize kept visits on a daemon thread after startup; None when there are none."""
    if instance_path is None or not _pending_path(instance_path).exists():
        return None

    def _run() -> None:
        time.sleep(delay_s)
        asyncio.run(summarize_pending_visits(instance_path))

    thread = threading.Thread(target=_run, name="pending-sleep-summary", daemon=True)
    thread.start()
    logger.info("Sleep summary: a kept visit will be summarized in the background.")
    return thread
