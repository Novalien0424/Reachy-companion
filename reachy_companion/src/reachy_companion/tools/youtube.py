"""YouTube on the living-room TV, named for its source. Filename == Tool.name.

D-038: the media tools are split by where a video comes from -- `youtube` here,
the family's own archive in `nas` -- because the old `tv` façade was named for
where it goes, and every home-video request also says "on the TV". The work is
`play_video`'s, unchanged: its prerequisite row, its home-network verdicts and
its tests all still apply.
"""

from __future__ import annotations
from typing import Any, Dict, ClassVar

from reachy_companion.tools.core_tools import Tool, ToolDependencies
from reachy_companion.tools.play_video import PlayVideo
from reachy_companion.tools.tool_family import forward_to


class Youtube(Tool):
    """Search YouTube and play the result on the TV."""

    name = "youtube"
    DELEGATE: ClassVar[Tool] = PlayVideo()
    description = (
        "YouTube on the living-room TV: search YouTube and play the best match. Use when: the user names "
        "YouTube or the internet, or wants something public — an MV, a trailer, a show, a how-to — like "
        "「放周杰倫的 MV」. Ask first when: the request gives only a place, a trip, a year or an event, like "
        "「放花蓮的影片」 — ask 影片庫 or YouTube and wait for the answer, then do what they asked. Do NOT use "
        "when: the user names the NAS or the 影片庫, or means the family's own recordings — that is nas; or "
        "wants sound only — that is music. To pause or stop a video, use music."
    )
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "What to search YouTube for: a title, an artist, a topic or a channel.",
            },
        },
        "required": ["query"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Search and cast through the tool that has always done it."""
        return await forward_to(name=self.name, delegate=self.DELEGATE, deps=deps, kwargs=kwargs)
