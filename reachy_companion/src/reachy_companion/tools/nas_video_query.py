"""Search the family home-video index (D-018, R4). Filename == Tool.name.

Read-only and entirely local -- no SMB, no network, no binary. It is still
house-bound, because everything it can lead to (casting a clip) is.
"""

from __future__ import annotations
import logging
from typing import Any, Dict

from reachy_companion.hanova import nas, settings
from reachy_companion.home_net import AWAY, HOME, home_state, home_unknown, away_from_home
from reachy_companion.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)

_DEFAULT_LIMIT = 40


class NasVideoQuery(Tool):
    """Search the home-video library by year, place, or keyword."""

    name = "nas_video_query"
    description = "Search the family home-video library. 用於找以前拍的家庭影片。"
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "year": {"type": "integer", "description": "Exact year to match."},
            "year_from": {"type": "integer", "description": "Earliest year to include."},
            "year_to": {"type": "integer", "description": "Latest year to include."},
            "place": {"type": "string", "description": "Place or trip name."},
            "keyword": {"type": "string", "description": "Any text to search the records for."},
            "limit": {"type": "integer", "description": "Maximum clips to return. Default 40."},
        },
        "required": [],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Return matching clips, or a folder overview when no filter is given."""
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

        filters = {key: kwargs.get(key) for key in ("year", "year_from", "year_to", "place", "keyword")}
        # Finding 7: the filters are the user's own words about their own family.
        logger.info(
            "Tool call: nas_video_query filters=%d",
            sum(1 for value in filters.values() if value not in (None, "")),
        )
        if not any(value is not None and str(value).strip() != "" for value in filters.values()):
            return {"ok": True, "folders": nas.summarize_folders(index)}

        try:
            limit = int(kwargs.get("limit") or _DEFAULT_LIMIT)
        except (TypeError, ValueError):
            limit = _DEFAULT_LIMIT

        videos = nas.filter_index(
            index,
            year=filters["year"],
            year_from=filters["year_from"],
            year_to=filters["year_to"],
            place=str(filters["place"]) if filters["place"] else None,
            keyword=str(filters["keyword"]) if filters["keyword"] else None,
            limit=max(1, min(200, limit)),
        )
        return {
            "ok": True,
            "count": len(videos),
            "videos": [
                {
                    "path": video.get("path"),
                    "title": nas.video_title(video),
                    "year": video.get("year"),
                    "place": video.get("place"),
                    "ready": bool(video.get("cast_ready")),
                }
                for video in videos
            ],
        }
