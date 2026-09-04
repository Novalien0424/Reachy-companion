"""The three conversation modes (2026-08-31 plan).

One boolean — `HuggingFaceRealtimeHandler._party_mode` — used to be the whole
mode system. It answered a single question ("a room, or one person?") and had
no room for a third posture. This module is the shared vocabulary the handler,
the prompts, the tools and the record log all read.

A leaf module on purpose: `tools/set_conversation_mode.py` imports it, and a
tool module must not import `huggingface_realtime` (that module imports
`tools.core_tools`, so the edge would close a cycle).
"""

from __future__ import annotations
from enum import Enum
from typing import Final


class ConversationMode(str, Enum):
    """How Reachy participates while it STAYS awake (never about sleeping)."""

    ONE_ON_ONE = "one_on_one"
    GROUP = "group"
    RECORD = "record"


# The mode a fresh handler boots into (operator instruction, 2026-09-04,
# reversing the 2026-08-31 amendment). The robot is mostly used by one person
# talking to it directly, and every session was opening with the same spoken
# switch out of 多人聊天模式 — so boot into 一對一聊天模式 and let the room
# posture be the one spoken sentence away. D-029 decision 5 (amended).
DEFAULT_MODE: Final[ConversationMode] = ConversationMode.ONE_ON_ONE

# Declaration order, used for the tool schema's enum. The boot default is
# `DEFAULT_MODE` above, not the first entry here.
MODE_VALUES: Final[tuple[str, ...]] = tuple(mode.value for mode in ConversationMode)

# Spoken labels, so a log line, a tool result and the model's confirmation
# sentence all name the mode the way the operator does.
MODE_LABELS: Final[dict[ConversationMode, str]] = {
    ConversationMode.ONE_ON_ONE: "一對一聊天模式",
    ConversationMode.GROUP: "多人聊天模式",
    ConversationMode.RECORD: "紀錄模式",
}

# The tool schema is not the only caller: an operator `.env`, a JSON-RPC call
# and the model's own argument all reach `parse_mode`, and `party`/`solo` are
# the words this codebase used until today.
_ALIASES: Final[dict[str, ConversationMode]] = {
    "one_on_one": ConversationMode.ONE_ON_ONE,
    "one-on-one": ConversationMode.ONE_ON_ONE,
    "solo": ConversationMode.ONE_ON_ONE,
    "group": ConversationMode.GROUP,
    "party": ConversationMode.GROUP,
    "record": ConversationMode.RECORD,
}


def parse_mode(value: str) -> ConversationMode | None:
    """Return the mode named by *value*, or None when it names nothing."""
    return _ALIASES.get(value.strip().lower().replace(" ", "_").replace("-", "_"))
