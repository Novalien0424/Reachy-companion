"""Sleep-time engagement memory: record the visit, write one 上次聊天 fact per person.

`record_transcript` is called by the realtime handler at its final-text push
sites; `write_sleep_summaries` (Task 4) runs once at handler shutdown.
"""

from __future__ import annotations
import os
import json
import time
import asyncio
import logging
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


def record_transcript(deps: ToolDependencies, role: str, text: str) -> None:
    """Append one finalized utterance, stamped, to the bounded session tail.

    Recording also refreshes the current person's sighting stamp, because talking
    is presence. Without that, `write_sleep_summaries`' window would exclude the
    commonest case it should keep: one person recognized at the boot greeting who
    then talks past 40 lines, pushing their own recognition out of the retained
    tail. Only a person already on the guest list is refreshed — `current_person`
    is a label, and a label alone is not a sighting.
    """
    cleaned = text.strip()
    if not cleaned or cleaned.startswith("[error]"):
        return
    stamp = time.monotonic()
    deps.session_transcript.append((role, cleaned, stamp))
    if deps.current_person is not None and deps.current_person in deps.recognized_at:
        deps.recognized_at[deps.current_person] = stamp


def _default_model() -> str:
    """Return the summarizer model — a small one; this runs after the visit ends."""
    return os.getenv("MEMORY_LAST_CHAT_MODEL", "").strip() or "gpt-5-mini"


def format_last_chat_fact(summary: str) -> str:
    """`上次聊天（M月D日）：<summary>` — the prefix is the supersession key."""
    stamp = time.strftime("%m月%d日").lstrip("0").replace("月0", "月")
    return f"{LAST_CHAT_PREFIX}（{stamp}）：{summary.strip()}"


def _people_in_window(deps: ToolDependencies, transcript: list[tuple[str, str, float]]) -> list[str]:
    """Return the guests the retained transcript can honestly speak for.

    `recognized_people` spans the whole app run; `session_transcript` holds only
    its last `TRANSCRIPT_MAX_ITEMS` lines. Summarizing the second against the
    first attributes whoever is here tonight to whoever was here this morning —
    their 上次聊天 fact gets somebody else's topics, and the next recognized
    greeting reads it back to them.

    So the filter is the tail's own reach. While **nothing has scrolled out** the
    tail *is* the whole run and nobody is filtered: every guest's own lines are
    still in it. Once lines have been evicted, a guest is kept only if last seen
    at-or-after the oldest surviving line — everything they said is otherwise
    gone, and there is nothing left to summarize *for them*.

    Two deliberate softenings, both fail-open:
      - `record_transcript` refreshes the current person's stamp, so the visit's
        own speaker is never filtered out by the length of their own
        conversation — the boot recognition scrolls out of a long visit, they do
        not;
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
    """Summarize the visit for everyone the retained tail still covers. Never raises."""
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
        if client is None:
            from reachy_companion.hanova.images import build_client

            client = build_client()
        if client is None:
            logger.info("Sleep summary skipped: no OpenAI client available.")
            return 0
        lines = "\n".join(f"{'user' if role == 'user' else 'reachy'}: {text}" for role, text, _ in transcript)
        user_prompt = f"在場的人：{'、'.join(names)}\n\n對話記錄：\n{lines}"
        timeout_s = env_float("MEMORY_LAST_CHAT_TIMEOUT_S", 8.0, lo=1.0, hi=30.0)
        async with client:
            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model=_default_model(),
                    messages=[
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    response_format={"type": "json_object"},
                ),
                timeout=timeout_s,
            )
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
            written += await asyncio.to_thread(_replace_last_chat_fact, deps.instance_path, name, summary)
        return written
    except Exception as exc:  # noqa: BLE001 — memory must never break shutdown
        logger.warning("Sleep summary failed: %s", type(exc).__name__)
        return 0


def _forget_key(text: str) -> str:
    """Return the exact key `forget_person_fact` matches on (people.py:394, 405)."""
    return normalize_memory_text(text).lower()


def _replace_last_chat_fact(instance_path: str | Path | None, name: str, summary: str) -> int:
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
    new_text = format_last_chat_fact(summary)  # stamped once: never re-read the clock here
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
