"""Sleep-time engagement memory: record the visit, write one 上次聊天 fact per person.

`record_transcript` is called by the realtime handler at its final-text push
sites; `write_sleep_summaries` (Task 4) runs once at handler shutdown.
"""

from __future__ import annotations
import logging
from typing import Final

from reachy_companion.tools.core_tools import ToolDependencies


logger = logging.getLogger(__name__)

# Bound on the visit tail held in ToolDependencies.session_transcript. The deque
# is built there with a literal maxlen — core_tools cannot import this module
# without a cycle (Tool classes import core_tools) — so keep the two in step.
TRANSCRIPT_MAX_ITEMS: Final[int] = 40
LAST_CHAT_PREFIX: Final[str] = "上次聊天"


def record_transcript(deps: ToolDependencies, role: str, text: str) -> None:
    """Append one finalized utterance to the bounded session tail."""
    cleaned = text.strip()
    if not cleaned or cleaned.startswith("[error]"):
        return
    deps.session_transcript.append((role, cleaned))
