"""The session's tool surface: a small static core plus two on-demand boxes.

Before this, 41 tools were sent at the start of every turn. OpenAI's own
function-calling guide asks for "fewer than 20 functions available at the start
of a turn", the realtime prompting docs say a focused list "prevents the model
from misselecting tools", and the measured effect is largest in exactly our
case — the right tool present but not ranked first (research doc §A1). The
observed symptom was `move_head` losing to `camera` on 「轉到右邊去看看有誰」.

Three mechanisms, cheapest first: consolidate (18 CRUD tools → 6 families,
`tools/tool_family.py`), delete what nobody calls, and load the rest on demand
through `open_toolbox` — the cookbook's Dynamic Conversation Flow pattern.

Result at the start of a turn: 22 tools, 27 while the productivity box is open,
24 while the media box is, 29 with both (they accumulate within a mode — design
decision 8), and 6 local tools in 紀錄模式. Every count is "plus any
`EXTRA_TOOLS`": MCP tool spaces belong to no box and are never hidden in any
mode, so they sit on top of all of these.
"""

from __future__ import annotations
import logging
from typing import Final
from collections.abc import Iterable

from reachy_companion.record_mode import RECORD_TOOL_ALLOWLIST
from reachy_companion.tools.core_tools import EXTRA_TOOLS, get_tools
from reachy_companion.conversation_mode import ConversationMode


logger = logging.getLogger(__name__)

# Always in `session.tools`. The rule for membership: anything the robot might
# need in the FIRST second of a turn, with no chance to load something first —
# its senses, its body, who it is talking to, the lights, the web, the
# conversation's own controls — plus the two `SystemTool` entries the
# background tool manager injects into every profile.
CORE_TOOL_NAMES: Final[frozenset[str]] = frozenset(
    {
        "camera",
        "look_around",
        "move_head",
        "play_emotion",
        "dance",
        "stop_dance",
        "stop_emotion",
        "head_tracking",
        "who_is_this",
        "remember_face",
        "remember",
        "forget",
        "home_control",
        # The music family is core, not boxed: it carries `stop_music`, the
        # safety lane that must answer even when nothing else can
        # (`settings.TOOL_PREREQS["stop_music"] == ()`, `stop_music.py:8`).
        # Behind a toolbox, "音樂關掉" would first have to load the tools for
        # turning the music off (Codex round 1, P2-7).
        "music",
        "pollen_robotics_reachy_mini_search_tool__search_web",
        "go_to_sleep",
        "set_conversation_mode",
        "wait_for_user",
        "summarize_conversation",
        "open_toolbox",
        "task_status",
        "task_cancel",
    }
)

# Loaded on demand, one `session.update` per open. Both families are things the
# user asks for in a sentence that can afford one extra hop — "add it to my
# calendar", "put that on the TV" — never something the robot needs mid-reflex,
# and never a way to make the robot stop doing something.
TOOLBOXES: Final[dict[str, tuple[str, ...]]] = {
    "productivity": ("calendar", "tasks", "drive", "email_send", "notion_add"),
    "media": ("tv", "nas"),
}

TOOLBOX_CATEGORIES: Final[tuple[str, ...]] = tuple(sorted(TOOLBOXES))


def session_tool_exclusions(mode: ConversationMode, open_boxes: Iterable[str]) -> list[str]:
    """Tool names to hide from the session, given the mode and the open boxes.

    Expressed through the registry's existing `exclusion_list` seam
    (`tools/core_tools.py:537`), so nothing else in the tool pipeline has to
    learn about modes or boxes. Computed against the LIVE registry rather than a
    literal, because MCP and external tools join it at runtime.

    Out-of-band tools (`EXTRA_TOOLS` — the MCP tool spaces, D-004) are never
    hidden: an operator installed them deliberately, they belong to no box, and
    a box that cannot be opened for them would make them unreachable forever.
    """
    registered = set(get_tools())
    # The invariant holds in EVERY mode, RECORD included (Codex round 1, P2-8):
    # an MCP tool belongs to no toolbox, so there is no `open_toolbox` category
    # that could bring it back, and hiding it strands it for the whole meeting.
    allowed = set(EXTRA_TOOLS)
    if mode is ConversationMode.RECORD:
        allowed |= set(RECORD_TOOL_ALLOWLIST)
    else:
        allowed |= set(CORE_TOOL_NAMES)
        for box in open_boxes:
            allowed |= set(TOOLBOXES.get(box, ()))
    return sorted(registered - allowed)
