"""紀錄模式: the room transcript log (2026-08-31 conversation-modes plan).

Deliberately NOT `deps.session_transcript`. That deque is `maxlen=40`,
accepted-turns-only, and exists to feed the D-027 sleep summary — a per-person
「上次聊天」 callback. A meeting record is the opposite on both axes: it wants
every line anyone said, including the ones the answer gate declined, and forty
lines is a few minutes.

In memory, for the length of one visit. Cleared when the mode is left and again
at the sleep that ends the visit; never written to disk, never exported (PRD
non-goal: long-term memory).
"""

from __future__ import annotations
import os
import time
import asyncio
import logging
from typing import Any, Final

from reachy_companion.sleep_summary import touch_presence
from reachy_companion.audio.envparse import env_float
from reachy_companion.tools.core_tools import ToolDependencies


logger = logging.getLogger(__name__)

# Bound on the room log held in `ToolDependencies.record_log`. The deque is
# built there with a literal maxlen — core_tools cannot import this module
# without a cycle (Tool classes import core_tools) — so keep the two in step.
RECORD_LOG_MAX_ITEMS: Final[int] = 2000


def record_room_transcript(deps: ToolDependencies, role: str, text: str) -> None:
    """Append one finalized utterance, stamped, to the room log.

    Same skip rules as `sleep_summary.record_transcript`: an empty line carries
    nothing, and a tool's own `[error] …` text is plumbing, not conversation.

    A user line also beats the D-027 presence heartbeat (`touch_presence`), which
    the ordinary path cannot do here: in 紀錄模式 the answer gate declines the
    meeting's speech before `record_transcript` is ever reached, so a room that
    talked for an hour would look to `write_sleep_summaries` like a room whose
    people were last seen at the boot greeting. An assistant line does not — the
    robot's own voice is no evidence that anybody is still in front of it.
    """
    cleaned = text.strip()
    if not cleaned or cleaned.startswith("[error]"):
        return
    stamp = time.monotonic()
    deps.record_log.append((role, cleaned, stamp))
    if role == "user":
        touch_presence(deps, stamp)


def clear_record_log(deps: ToolDependencies) -> None:
    """Drop the room log. Called on mode exit and at the sleep that ends the visit."""
    if deps.record_log:
        logger.info("record log cleared (%d lines)", len(deps.record_log))
    deps.record_log.clear()


# What 紀錄模式 leaves on the table: SIX local names — the four the model uses
# plus two structural ones. `task_status` and `task_cancel` are `SystemTool`
# values the background tool manager injects into every profile, and the model
# needs them to follow up a long-running call, so hiding them would break the
# tools that ARE allowed. MCP extras (`EXTRA_TOOLS`) are additionally always
# kept — see `toolboxes.session_tool_exclusions` (Codex round 1, P2-8).
RECORD_TOOL_ALLOWLIST: Final[frozenset[str]] = frozenset(
    {
        "set_conversation_mode",
        "summarize_conversation",
        "go_to_sleep",
        "wait_for_user",
        "task_status",
        "task_cancel",
    }
)


RECORD_EMPTY_SUMMARY: Final[str] = "還沒有記錄到內容。"
RECORD_SUMMARY_FAILED: Final[str] = "剛剛的記錄整理失敗了，要不要再說一次？"

# The last two sentences are about a log that is NOT guaranteed to be one
# contiguous meeting: a settings/backend restart mid-visit keeps the log (it is
# not a sleep) but drops the mode back to the boot default, so recording stops
# until 紀錄模式 is re-entered and the next stretch is appended to the previous
# one with an unmarked gap between them. Rather than guess where the seams are
# from timestamps, the prompt is told the truth — summarize what was captured,
# in order, newest topics last — which is also the honest thing to read aloud.
_SUMMARY_SYSTEM_PROMPT: Final[str] = (
    "你是會議記錄整理員。根據下面這段對話記錄，用臺灣繁體中文整理出重點，"
    "念出來就能聽懂的口語段落，不要用條列符號、不要用 Markdown。"
    "優先寫：講了哪些主題、做了什麼決定、誰要做什麼、還沒有結論的事。"
    "只根據記錄，不要編造，也不要把某句話歸給記錄裡沒有寫出名字的人。"
    "記錄很短的時候就簡短講完，不要硬湊。"
    "記錄是照時間先後排的，但中間可能有沒記到的空檔，不一定是連續的同一段會議；"
    "就照記錄到的內容整理，較晚講的放後面，不要假設中間沒有漏掉東西。"
)
# The summarizer runs after the fact on a much bigger input than the sleep
# summary's forty lines, so it gets its own, longer budget (the plan's ~20 s).
_SUMMARY_TIMEOUT_DEFAULT_S: Final[float] = 20.0


def _summary_model() -> str:
    """Return the summarizer model — the same small one the sleep summary uses."""
    return os.getenv("MEMORY_LAST_CHAT_MODEL", "").strip() or "gpt-5-mini"


async def summarize_record_log(deps: ToolDependencies, *, client: Any | None = None) -> str:
    """Summarize the room log in Traditional Chinese. Never raises.

    Same client/model/timeout shape as `sleep_summary.write_sleep_summaries`
    (`sleep_summary.py:133-190`): a plain Chat Completions call on a small
    model, the client used as `async with` so its pool closes, and the whole
    body inside one `try` — a summary that fails must cost a sentence, never the
    conversation.

    Returns the text for the model to read aloud verbatim, so every failure mode
    also has to return something sayable.

    The log is what was captured, not necessarily one unbroken meeting: a
    settings or backend restart mid-visit leaves the lines in place while
    dropping the mode, so recording stops until 紀錄模式 is re-entered and the
    next stretch lands directly after the previous one. The prompt says so
    rather than pretending otherwise; there is no gap-detection heuristic here.
    """
    lines = list(deps.record_log)
    if not lines:
        return RECORD_EMPTY_SUMMARY
    if client is None:
        from reachy_companion.hanova.images import build_client

        client = build_client()
    if client is None:
        logger.info("Record summary skipped: no OpenAI client available.")
        return RECORD_SUMMARY_FAILED
    rendered = "\n".join(f"{'user' if role == 'user' else 'reachy'}: {text}" for role, text, _ in lines)
    timeout_s = env_float("RECORD_SUMMARY_TIMEOUT_S", _SUMMARY_TIMEOUT_DEFAULT_S, lo=1.0, hi=60.0)
    try:
        async with client:
            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model=_summary_model(),
                    messages=[
                        {"role": "system", "content": _SUMMARY_SYSTEM_PROMPT},
                        {"role": "user", "content": f"對話記錄（共 {len(lines)} 句）：\n{rendered}"},
                    ],
                ),
                timeout=timeout_s,
            )
        summary = (response.choices[0].message.content or "").strip()
    except Exception as exc:  # noqa: BLE001 — a summary must never break the turn
        logger.warning("Record summary failed: %s", type(exc).__name__)
        return RECORD_SUMMARY_FAILED
    if not summary:
        return RECORD_SUMMARY_FAILED
    logger.info("Record summary written from %d logged lines.", len(lines))
    return summary
