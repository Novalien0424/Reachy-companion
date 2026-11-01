"""YouTube on the living-room TV, named for its source. Filename == Tool.name.

D-038: the media tools are split by where a video comes from -- `youtube` here,
the family's own archive in `nas` -- because the old `tv` façade was named for
where it goes, and every home-video request also says "on the TV". The work is
`play_video`'s, unchanged: its prerequisite row, its home-network verdicts and
its tests all still apply.

D-047: after a 影片庫 miss, YouTube waits for the user's yes. The question is
the model's to ask; this refusal is the rail for when it doesn't (live on
1.37.0, mini went straight to YouTube in 1 of 3 misses).
"""

from __future__ import annotations
import logging
from typing import Any, Dict, ClassVar

from reachy_companion.hanova.nas import ASK_YOUTUBE
from reachy_companion.tools.core_tools import Tool, ToolDependencies
from reachy_companion.tools.play_video import PlayVideo
from reachy_companion.tools.tool_family import forward_to


logger = logging.getLogger(__name__)

class Youtube(Tool):
    """Search YouTube and play the result on the TV."""

    name = "youtube"
    DELEGATE: ClassVar[Tool] = PlayVideo()
    description = (
        "YouTube on the living-room TV: search YouTube and play the best match. Use when: the user names "
        "YouTube or the internet, or wants something public — an MV, a trailer, a show, a how-to — like "
        "「放周杰倫的 MV」, or agreed to YouTube after the 影片庫 had nothing. Do NOT use when: the user names "
        "the NAS or the 影片庫, or asks for a video of a place, a trip, a year or an event, or of the family — "
        "that is nas, looked at first; or wants sound only — that is music. To pause or stop a video, use music."
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
        """Search and cast through the tool that has always done it, once the user may want YouTube."""
        if getattr(deps, "youtube_needs_consent", False):
            logger.info("youtube: waiting for the user's yes after a 影片庫 miss")
            return {
                "ok": False,
                "error": "not yet: the 影片庫 had nothing, and the user has not said yes to YouTube. "
                "Ask them the ask_user question, and call youtube only after they agree.",
                "ask_user": ASK_YOUTUBE,
            }
        return await forward_to(name=self.name, delegate=self.DELEGATE, deps=deps, kwargs=kwargs)
