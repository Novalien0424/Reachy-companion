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
import time
import logging
from typing import Final

from reachy_companion.sleep_summary import touch_presence
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
