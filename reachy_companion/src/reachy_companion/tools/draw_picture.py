"""Draw a new picture and put it on the TV. Filename == Tool.name.

D-038: the `show` half of the retired `tv` façade, named for what it does. The
name carries no "tv": that word is in every home-video request too, and a video
request must never land here. The work is `show_on_tv`'s, unchanged.
"""

from __future__ import annotations
from typing import Any, Dict, ClassVar

from reachy_companion.tools.core_tools import Tool, ToolDependencies
from reachy_companion.tools.show_on_tv import ShowOnTv
from reachy_companion.tools.tool_family import forward_to


class DrawPicture(Tool):
    """Generate a picture and show it on the TV."""

    name = "draw_picture"
    DELEGATE: ClassVar[Tool] = ShowOnTv()
    description = (
        "Draw a new picture with AI and put it on the living-room TV. Use when: the user asks you to "
        "draw, illustrate or imagine something for the big screen. Do NOT use when: the user wants a "
        "video or an existing photo or clip — that is youtube or nas; this only makes a new image."
    )
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "request": {
                "type": "string",
                "description": "What the picture should show.",
            },
        },
        "required": ["request"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Generate and cast through the tool that has always done it."""
        return await forward_to(name=self.name, delegate=self.DELEGATE, deps=deps, kwargs=kwargs)
