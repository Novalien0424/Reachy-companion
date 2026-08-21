"""Play a whole home-video trip in order (D-018, R2/R4/R6). Filename == Tool.name."""

from __future__ import annotations
import logging
from typing import Any, Dict

from reachy_companion.hanova import nas, redact, settings
from reachy_companion.home_net import AWAY, HOME, home_state, home_unknown, away_from_home
from reachy_companion.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


class NasPlayFolder(Tool):
    """Start a whole trip folder from its first clip."""

    name = "nas_play_folder"
    description = "Play a whole home-video trip in order. 用於播整趟旅行的影片。"
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "top_folder": {"type": "string", "description": "Folder name from nas_video_query. Preferred."},
            "year": {"type": "integer", "description": "Year, when no folder name is known."},
            "place": {"type": "string", "description": "Place or trip name, when no folder name is known."},
        },
        "required": [],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Resolve a trip folder and cast its first clip."""
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

        top_folder = str(kwargs.get("top_folder") or "").strip()
        if not top_folder:
            matches = nas.filter_index(
                index,
                year=kwargs.get("year"),
                place=str(kwargs.get("place")) if kwargs.get("place") else None,
            )
            folders = sorted({str(video.get("top_folder") or "") for video in matches if video.get("top_folder")})
            if not folders:
                return {"ok": False, "error": "no_match"}
            if len(folders) > 1:
                return {"ok": False, "error": "ambiguous", "candidates": folders}
            top_folder = folders[0]

        playlist = nas.folder_playlist(index, top_folder)
        if not playlist:
            return {"ok": False, "error": "no_match"}

        logger.info("Tool call: nas_play_folder %s (%d clips)", redact.ident(top_folder), len(playlist))
        result = await nas.stage_and_cast(playlist[0], deps.instance_path)
        if not result["ok"]:
            # Finding 16: nothing reached the TV, so no trip session is opened.
            return {"ok": False, "error": result["error"]}

        nas.start_session(playlist, 0)
        return {
            "ok": True,
            "status": "casting",
            "top_folder": top_folder,
            "title": result["title"],
            "remaining": nas.remaining(),
        }
