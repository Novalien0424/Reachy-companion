"""The household video archive as one action-enum tool. Filename == Tool.name.

Façade over `nas_video_query` / `play_nas_video` / `nas_play_folder` /
`nas_skip`, which keep their modules, their names, their `settings.tool_status`
prerequisite rows and their home-network verdict branches. See `tool_family.py`
for why.

Naming: this is `reachy_companion.tools.nas`, not `reachy_companion.hanova.nas`
-- the latter is the index/playlist layer the delegates use, and absolute
imports keep the two apart.
"""

from __future__ import annotations
from typing import Any, Dict, Mapping, ClassVar

from reachy_companion.tools.nas_skip import NasSkip
from reachy_companion.tools.core_tools import Tool, ToolDependencies
from reachy_companion.tools.tool_family import dispatch_family
from reachy_companion.tools.play_nas_video import PlayNasVideo
from reachy_companion.tools.nas_play_folder import NasPlayFolder
from reachy_companion.tools.nas_video_query import NasVideoQuery


class Nas(Tool):
    """Search and play the family's own recorded videos through one tool."""

    name = "nas"
    ACTIONS: ClassVar[Mapping[str, Tool]] = {
        "query": NasVideoQuery(),
        "play": PlayNasVideo(),
        "play_folder": NasPlayFolder(),
        "skip": NasSkip(),
    }
    description = (
        "The family's own video library (家裡的影片庫) on the NAS: search it, play one clip or a whole trip on the "
        "living-room TV, or skip to the next clip. "
        "Use when: the user names the NAS or the video library, or means videos the family recorded, by year, "
        "place, trip or subject — 「幫我從 NAS 找」「從影片庫找二零一九年在花蓮拍的」「放我們拍的那部」「播那個資料夾」"
        "「下一部」「play our old videos」「skip」. "
        "Ask first when: the request gives only a place, a trip, a year or an event — 「放花蓮的影片」 could be the "
        "family's own trip or a travel video online — so ask whether they mean the 影片庫 or YouTube, and call "
        "neither tool until they answer; then do what they first asked — if it was to put something on, play "
        "it. "
        "Do NOT use when: the user names YouTube or the internet, or wants something public such as an MV or a "
        "trailer — that is youtube; an answer from the web is the search tool. "
        "Do NOT use when: the user wants music rather than video — that is music. "
        "Both this and youtube play on the TV, so 電視 decides nothing: the source does. "
        "To put videos on the TV, play in one call: `play_folder` with `place` (or `year`) starts a trip from its "
        "first clip and queues the rest, answering with `candidates` instead of guessing when several trips "
        "match; `play` with `keyword` or `place` puts on the one clip that matches. Pass `path` or `top_folder` "
        "only when a result gave you one. `query` is for when the user wants to know what is there or to choose "
        "— not a step before playing. `skip` moves to the next clip of the running trip. These clips exist only "
        "here, so play them here, never through youtube. "
        "The archive is catalogued in English, so search with English words: translate a place or subject "
        "first (巴黎 → Paris, 生日 → birthday). `query` lists every matching trip in `trips`. When nothing "
        "matches, the result carries `all_trips`, the whole catalogue: pick the trip the user means from it "
        "and search or play that one, because a miss is usually a wording miss rather than a missing video. "
        "When no trip there fits either, say the 影片庫 does not have it and offer YouTube; search YouTube only "
        "once they agree."
    )
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["play", "play_folder", "query", "skip"],
                "description": (
                    "query 搜尋家庭影片；play 播一段影片；play_folder 播整個資料夾（整趟旅行）；"
                    "skip 播下一段。"
                ),
            },
            # `query` goes last on purpose. `year`, `place` and `keyword` are
            # shared by three of these four and the last spread wins;
            # NasVideoQuery describes them plainly ("Exact year to match.")
            # where the two play tools qualify them with their own fallback
            # ("Year, when no folder name is known."), which reads as a
            # restriction the family does not have. `path` and `top_folder`
            # are unique to one action each and survive either way.
            **PlayNasVideo.parameters_schema["properties"],
            **NasPlayFolder.parameters_schema["properties"],
            **NasSkip.parameters_schema["properties"],
            **NasVideoQuery.parameters_schema["properties"],
        },
        "required": ["action"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Forward one NAS action to the tool that has always handled it."""
        return await dispatch_family(
            family=self.name,
            action=kwargs.get("action"),
            actions=self.ACTIONS,
            deps=deps,
            kwargs=kwargs,
        )
