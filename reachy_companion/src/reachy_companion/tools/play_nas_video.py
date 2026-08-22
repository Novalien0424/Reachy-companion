"""Cast one home video to the TV (D-018, R2/R4/R6). Filename == Tool.name.

The clip is copied off the NAS into the LAN-served media cache and its URL is
cast, because a Chromecast dereferences the URL itself. The rest of the clip's
trip becomes the session playlist, so `nas_skip` can move through it.
"""

from __future__ import annotations
import logging
from typing import Any, Dict

from reachy_companion.hanova import nas, redact, settings
from reachy_companion.home_net import AWAY, HOME, home_state, home_unknown, away_from_home
from reachy_companion.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


class PlayNasVideo(Tool):
    """Play one home video on the TV."""

    name = "play_nas_video"
    description = "Play one family home video on the TV. 用於播放某一段家庭影片。"
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Exact clip path from nas_video_query. Preferred."},
            "year": {"type": "integer", "description": "Year, when no path is known."},
            "place": {"type": "string", "description": "Place or trip name, when no path is known."},
            "keyword": {"type": "string", "description": "Any text to narrow the match."},
        },
        "required": [],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Resolve one clip, stage it on the LAN, and cast it."""
        available, reason = settings.tool_status(self.name)
        if not available:
            return settings.unavailable(reason)
        # Finding 12 + round 2 finding 3: three verdicts, three branches. On
        # UNKNOWN nothing is read, staged or cast -- "I cannot tell where I am"
        # is not permission, and it is not absence either.
        verdict = await home_state()
        if verdict == AWAY:
            return away_from_home()
        if verdict != HOME:
            return home_unknown()

        index = nas.load_index()
        if index is None:
            # The prerequisite said the file exists; it is unreadable or malformed.
            return settings.unavailable("NAS_INDEX_FILE")

        path = str(kwargs.get("path") or "").strip()
        if path:
            matches = [video for video in index.get("videos", []) if video.get("path") == path]
        else:
            matches = nas.filter_index(
                index,
                year=kwargs.get("year"),
                place=str(kwargs.get("place")) if kwargs.get("place") else None,
                keyword=str(kwargs.get("keyword")) if kwargs.get("keyword") else None,
            )
        if not matches:
            return {"ok": False, "error": "no_match"}

        ready = [video for video in matches if video.get("cast_ready")]
        if not ready:
            return {"ok": False, "error": "not_ready"}
        video = ready[0]

        logger.info("Tool call: play_nas_video %s", redact.ident(video.get("path")))
        result = await nas.stage_and_cast(video, deps.instance_path)
        if not result["ok"]:
            # Finding 16: a failed play leaves whatever was on the TV alone, and
            # therefore leaves the trip session alone too.
            return {"ok": False, "error": result["error"]}

        playlist = nas.folder_playlist(index, str(video.get("top_folder") or ""))
        position = next(
            (i for i, item in enumerate(playlist) if item.get("path") == video.get("path")),
            -1,
        )
        if position >= 0:
            nas.start_session(playlist, position)
        else:
            nas.clear_session()

        return {
            "ok": True,
            "status": "casting",
            "title": result["title"],
            "ambiguous": len(ready) > 1,
            "remaining": nas.remaining(),
        }
