"""Head movement without looking, as one action-enum tool. Filename == Tool.name.

Façade over upstream's `move_head` (`turn`) and `head_tracking` (`follow` /
`stop_following`), which keep their modules (mini context diet, 2026-10-30).
`follow` and `stop_following` bind `head_tracking`'s `enabled` to True and
False, so the model picks an action instead of passing a boolean. See
`tool_family.py` for why. Both delegates set `needs_response = False`, so the
family does too.
"""

from __future__ import annotations
from typing import Any, Dict, Mapping, ClassVar

from reachy_companion.tools.move_head import DIRECTIONS, MoveHead
from reachy_companion.tools.core_tools import Tool, ToolDependencies
from reachy_companion.tools.tool_family import BoundAction, dispatch_family
from reachy_companion.tools.head_tracking import HeadTracking


class Head(Tool):
    """Turn the head once, or start or stop following the user's face."""

    name = "head"
    needs_response = False
    ACTIONS: ClassVar[Mapping[str, Tool]] = {
        "turn": MoveHead(),
        "follow": BoundAction(HeadTracking(), enabled=True),
        "stop_following": BoundAction(HeadTracking(), enabled=False),
    }
    description = (
        "Moves the head without looking. `turn` points it one way for a moment as body language — "
        "「抬頭」「低頭」「頭轉過去」「看鏡頭」; it takes no picture, and face-following resumes by itself "
        "afterwards. `follow` keeps the head on the user's face until told otherwise — 「一直看著我」「跟著我轉」; "
        "`stop_following` ends that. Do NOT use when: the user wants to know who or what is in a direction — "
        "that is look_around; or what is in front of you — that is camera. After `turn`, never say you saw "
        "anything; say you turned that way only when `direction_requested` came back with that direction."
    )
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["turn", "follow", "stop_following"],
                "description": (
                    "turn: point the head once. follow / stop_following: start or stop following the user's face."
                ),
            },
            "direction": {
                "type": "string",
                "enum": list(DIRECTIONS),
                "description": "For turn: left 左邊、right 右邊、up 上面、down 下面、front 正前方。",
            },
        },
        "required": ["action"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Forward one head action to the tool that has always handled it."""
        return await dispatch_family(
            family=self.name,
            action=kwargs.get("action"),
            actions=self.ACTIONS,
            deps=deps,
            kwargs=kwargs,
        )
