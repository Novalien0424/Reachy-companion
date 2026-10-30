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
            "year": {"type": "integer", "description": "Year."},
            "year_from": {"type": "integer", "description": "First year of a range."},
            "year_to": {"type": "integer", "description": "Last year of a range."},
            "place": {"type": "string", "description": "Place or trip, in English (巴黎 → Paris)."},
            "keyword": {"type": "string", "description": "Any English word to match, e.g. 生日 → birthday."},
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

        matches = nas.filter_index(
            index,
            year=filters["year"],
            year_from=filters["year_from"],
            year_to=filters["year_to"],
            place=str(filters["place"]) if filters["place"] else None,
            keyword=str(filters["keyword"]) if filters["keyword"] else None,
        )
        if not matches:
            return nas.search_miss(index, [filters["place"], filters["keyword"]])
        # Every matching trip, even when the clips are capped (2026-10-29).
        trips = nas.trip_summary(matches)
        result: dict[str, Any] = {"ok": True, "count": len(matches), "trips": trips}
        if len(trips) == 1:
            # One trip: its clips, as the path `play` takes and a title; the trip
            # row already carries year and place. Several trips: the trip rows
            # alone. The clips of nine trips were ~2.5k tokens that stayed in the
            # conversation for the rest of the visit (2026-10-30 context diet).
            result["videos"] = [
                {"path": video.get("path"), "title": nas.video_title(video)}
                | ({} if video.get("cast_ready") else {"ready": False})
                for video in matches[: max(1, min(200, limit))]
            ]
        return result
