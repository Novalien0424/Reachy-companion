"""Dance moves, and stopping any body motion, as one action-enum tool. Filename == Tool.name.

Façade over upstream's `Dance` (moved unchanged to `dance_moves.py`, because
this module must now hold the family) and the two upstream stop tools,
`stop_dance` and `stop_emotion`, which keep their modules. See `tool_family.py`
for why.

`stop` is the one composite (mini context diet, 2026-10-30): it calls BOTH
stop delegates, so the model never has to know whether a dance or an emotion
is the motion running. Every delegate sets `needs_response = False` (motion is
body language, not something to talk about), so the family does too; an error
result still gets its spoken follow-up, as for every tool.
"""

from __future__ import annotations
from typing import Any, Dict, Mapping, ClassVar

from reachy_companion.tools.core_tools import Tool, ToolDependencies
from reachy_companion.tools.stop_dance import StopDance
from reachy_companion.tools.dance_moves import Dance as DanceMoves
from reachy_companion.tools.tool_family import dispatch_family
from reachy_companion.tools.stop_emotion import StopEmotion


class _StopAllMotion(Tool):
    """`dance stop`: end whatever dance or emotion motion is running.

    Never registered on its own (`_auto_register = False`): it exists only as
    the family's `stop` action.
    """

    _auto_register: ClassVar[bool] = False
    needs_response = False
    name = "dance_stop"
    description = "Stop any dance or emotion motion."
    parameters_schema: Dict[str, Any] = {"type": "object", "properties": {}, "required": []}
    DELEGATES: ClassVar[tuple[Tool, ...]] = (StopDance(), StopEmotion())

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Call each stop delegate in turn and merge their answers into one."""
        results = [await delegate(deps) for delegate in self.DELEGATES]
        errors = [str(result["error"]) for result in results if "error" in result]
        if errors:
            return {"error": "; ".join(errors)}
        return {"status": "stop_queued", "stopped": [result.get("stopped") for result in results]}


class Dance(Tool):
    """Play a dance move, or stop any body motion, through one tool."""

    name = "dance"
    needs_response = False
    ACTIONS: ClassVar[Mapping[str, Tool]] = {
        "play": DanceMoves(),
        "stop": _StopAllMotion(),
    }
    description = (
        "Reachy's dance moves, and stopping any body motion. Use when: the user wants a dance — `play`; "
        "leave `move` out for a random one — or wants you to stop moving — `stop`, which ends any dance "
        "or emotion motion; call it with no preamble, because they want it ended now. With `play`, say your "
        "reply as usual in the same turn, like congratulations on good news: nothing is said after the "
        "dance starts. Do NOT use when: a "
        "short reaction to a feeling fits — that is play_emotion; or the music should stop — that is "
        "music with action=stop."
    )
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["play", "stop"],
                "description": "play: dance. stop: end any dance or emotion motion now.",
            },
            # The move NAMES are upstream's own enum; only the per-move
            # description list is left out, because the model picks by name.
            "move": {
                "type": "string",
                "enum": DanceMoves.parameters_schema["properties"]["move"]["enum"],
                "description": "Which dance; leave out for a random one.",
            },
            "repeat": DanceMoves.parameters_schema["properties"]["repeat"],
        },
        "required": ["action"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Forward one dance action to the tool that has always handled it."""
        return await dispatch_family(
            family=self.name,
            action=kwargs.get("action"),
            actions=self.ACTIONS,
            deps=deps,
            kwargs=kwargs,
        )
