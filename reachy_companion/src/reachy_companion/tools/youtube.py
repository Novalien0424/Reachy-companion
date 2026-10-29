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
        "YouTube, played on the living-room TV: search YouTube and put the best match on the screen. "
        "Use when: the user names YouTube or the internet, or wants something public — an MV, a trailer, a show, "
        "a channel, a how-to — 「幫我從 YouTube 找」「上網找一部做蛋糕的影片放到電視上」「電視上放周杰倫的 MV」"
        "「search YouTube for a cat video」. "
        "Do NOT use when: the user names the NAS or the video library (影片庫), or means the family's own "
        "recordings (「我們拍的」「家裡的影片」, or a clip nas found) — those are not on YouTube, and nas plays them "
        "on this same TV. "
        "Do NOT use when: the user wants sound only — that is music. "
        "Both this and nas play on the TV, so 電視 alone does not decide: the source does. When the user names "
        "YouTube, search YouTube, whatever the words. When the request names no source and nothing shows whether "
        "it is the family's own or something public, ask which one they mean."
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
