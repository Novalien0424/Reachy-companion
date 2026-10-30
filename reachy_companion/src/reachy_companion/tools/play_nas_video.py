"""Find a home video and cast it to the TV, in one call (D-018, R2/R4/R6). Filename == Tool.name.

The clip is copied off the NAS into the LAN-served media cache and its URL is
cast, because a Chromecast dereferences the URL itself. The rest of the clip's
trip becomes the session playlist, so `nas_skip` can move through it.

2026-10-30: this also plays a whole trip (it absorbed `nas_play_folder`). Live,
the mini model searched first in every session and twice stopped between the
search and the play, so the first call it reaches for has to finish the job.
"""

from __future__ import annotations
import logging
from typing import Any, Dict

from reachy_companion.hanova import nas, redact, settings
from reachy_companion.home_net import AWAY, HOME, home_state, home_unknown, away_from_home
from reachy_companion.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


class PlayNasVideo(Tool):
    """Find and play a home video, or a whole trip, on the TV."""

    name = "play_nas_video"
    description = "Find a family home video and play it on the TV. 用於找出並播放家庭影片。"
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            # 2026-08-31 tool diet: these reach the model through the `nas` family
            # schema, so they must name a call the model can still make.
            "path": {"type": "string", "description": "A clip's path, from an earlier result."},
            "top_folder": {"type": "string", "description": "A trip's top_folder, from an earlier result."},
            "year": {"type": "integer", "description": "Year."},
            "place": {"type": "string", "description": "Place or trip, in English."},
            "keyword": {"type": "string", "description": "Any English word to match."},
        },
        "required": [],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Resolve one clip (or a trip's first), stage it on the LAN, and cast it."""
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
        top_folder = str(kwargs.get("top_folder") or "").strip()
        filters = {key: kwargs.get(key) for key in ("year", "year_from", "year_to", "place", "keyword")}
        if not path and not top_folder and all(value in (None, "") for value in filters.values()):
            return {"ok": False, "error": "say what to play: a place, year or keyword, or a path or top_folder"}
        if path:
            matches = [video for video in index.get("videos", []) if video.get("path") == path]
        elif top_folder:
            matches = nas.folder_playlist(index, top_folder)
        else:
            matches = nas.filter_index(
                index,
                year=filters["year"],
                year_from=filters["year_from"],
                year_to=filters["year_to"],
                place=str(filters["place"]) if filters["place"] else None,
                keyword=str(filters["keyword"]) if filters["keyword"] else None,
            )
        if not matches:
            # Only the free-text words can be a translation miss: real folder
            # names and paths contain Chinese.
            words = [] if path or top_folder else [filters["place"], filters["keyword"]]
            return nas.search_miss(index, words)

        ready = [video for video in matches if video.get("cast_ready")]
        if not ready:
            return {"ok": False, "error": "not_ready"}
        trips = nas.trip_summary(ready)
        if len(trips) > 1:
            # Nothing is cast on a guess: which trip is the user's call.
            return {
                "ok": False,
                "error": "several trips match; ask which one, then play it by top_folder",
                "candidates": trips,
            }
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
            # The trip it belongs to, so the one Reachy names comes from here (live
            # 2026-10-29: a Taiwan clip was announced as the trip asked for).
            "top_folder": video.get("top_folder"),
            "clips_left": nas.remaining(),
        }
