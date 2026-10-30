"""Faces you know, as one action-enum tool. Filename == Tool.name.

Façade over `who_is_this` (`identify`) and `remember_face` (`remember`), which
keep their modules: the recognizer seam, the hold-still burst, the snapshot
and the verbatim envelope all stay where they were (mini context diet,
2026-10-30). See `tool_family.py` for why. Neither delegate opts out of the
spoken follow-up, so neither does the family.
"""

from __future__ import annotations
from typing import Any, Dict, Mapping, ClassVar

from reachy_companion.tools.core_tools import Tool, ToolDependencies
from reachy_companion.tools.tool_family import dispatch_family
from reachy_companion.tools.who_is_this import WhoIsThis
from reachy_companion.tools.remember_face import RememberFace


class Face(Tool):
    """Identify the person in front of the camera, or learn their face."""

    name = "face"
    ACTIONS: ClassVar[Mapping[str, Tool]] = {
        "identify": WhoIsThis(),
        "remember": RememberFace(),
    }
    description = (
        "Faces you know. `identify` looks at the person in front of the camera and checks whether you know them. "
        "Use it, not camera, for any question about who someone is — 「你認得我嗎」「我是誰」「誰來了」"
        "「do you remember me」. It returns a status and no picture: recognized (with the name and short facts), "
        "unknown, ambiguous, no_face, too_far or unavailable. If it is not recognized, say so and never guess a "
        "name. Say the name and facts exactly as returned; when `require_repeat_verbatim` is true, say "
        "`response_text` word for word first, then go on naturally. `remember` learns the face of the one person "
        "in front of you under the name they gave — 「記住我」「我是小明，記住我的臉」. Before calling it, ask them "
        "to look at you and hold still for two seconds. It refuses unless exactly one person is in frame; then ask "
        "them to face you alone. Only the name, a face signature and one snapshot are stored."
    )
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["identify", "remember"],
            },
            "name": {
                "type": "string",
                "description": "For remember: the name they gave. One person per call.",
            },
        },
        "required": ["action"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Forward one face action to the tool that has always handled it."""
        return await dispatch_family(
            family=self.name,
            action=kwargs.get("action"),
            actions=self.ACTIONS,
            deps=deps,
            kwargs=kwargs,
        )
