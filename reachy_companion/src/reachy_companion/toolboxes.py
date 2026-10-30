"""The session's tool surface: one static list per conversation mode, no boxes.

Before the 2026-08-31 tool diet, 41 tools were sent at the start of every turn.
OpenAI's own function-calling guide asks for "fewer than 20 functions available
at the start of a turn", and the realtime prompting docs say a focused list
"prevents the model from misselecting tools" (research doc §A1). That diet
consolidated CRUD tools into action-enum families (`tools/tool_family.py`) and
loaded the rest on demand through an `open_toolbox` router.

The mini context diet (2026-10-30, docs/plans/2026-10-30-mini-context-diet-plan.md)
removed the boxes: every box was a forced extra call, and the mini model fails
between calls, not at picking them. Calendar, tasks and Notion went, nine body
and memory tools became four families, and everything left fits under the
"fewer than 20" line at once.

Result at the start of every turn: 19 tools in 一對一 and 多人, and 4 local tools
in 紀錄模式. Every count is "plus any `EXTRA_TOOLS`": MCP tool spaces are never
hidden in any mode, so they sit on top of these. The phase rule OpenAI gives
("only what's relevant to the active phase") is kept where the APP knows the
phase: the conversation mode.
"""

from __future__ import annotations
import logging
from typing import Final

from reachy_companion.record_mode import RECORD_TOOL_ALLOWLIST
from reachy_companion.tools.core_tools import EXTRA_TOOLS, get_tools
from reachy_companion.conversation_mode import ConversationMode


logger = logging.getLogger(__name__)

# Always in `session.tools` outside 紀錄模式. `task_status` and `task_cancel`
# are deliberately absent: they are `SystemTool` entries the background tool
# manager injects into every profile (`core_tools.py:_read_profile_tool_names`)
# and keeps using, so they stay REGISTERED, but cancelling needs a `tool_id`
# from status first — a two-call chain nobody made in the journal — so the
# model is not sent them.
CORE_TOOL_NAMES: Final[frozenset[str]] = frozenset(
    {
        "camera",
        "look_around",
        "play_emotion",
        "dance",
        "head",
        "face",
        "memory",
        "home_control",
        # The music family carries `stop_music`, the safety lane that must
        # answer even when nothing else can
        # (`settings.TOOL_PREREQS["stop_music"] == ()`, `stop_music.py:8`).
        "music",
        "pollen_robotics_reachy_mini_search_tool__search_web",
        "set_conversation_mode",
        "summarize_conversation",
        "wait_for_user",
        "go_to_sleep",
        "youtube",
        "nas",
        "draw_picture",
        "drive",
        "email_send",
    }
)


def session_tool_exclusions(mode: ConversationMode) -> list[str]:
    """Tool names to hide from the session in *mode*.

    Expressed through the registry's existing `exclusion_list` seam
    (`tools.core_tools.get_tool_specs`), so nothing else in the tool pipeline has to
    learn about modes. Computed against the LIVE registry rather than a
    literal, because MCP and external tools join it at runtime.

    Out-of-band tools (`EXTRA_TOOLS` — the MCP tool spaces, D-004) are never
    hidden: an operator installed them deliberately, and no list here names
    them, so hiding one would make it unreachable for the whole visit.
    """
    registered = set(get_tools())
    # The invariant holds in EVERY mode, RECORD included (Codex round 1, P2-8).
    allowed = set(EXTRA_TOOLS)
    if mode is ConversationMode.RECORD:
        allowed |= set(RECORD_TOOL_ALLOWLIST)
    else:
        allowed |= set(CORE_TOOL_NAMES)
    return sorted(registered - allowed)
