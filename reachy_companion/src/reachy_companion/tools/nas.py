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
        "The household video archive on the NAS: search it, play one video or a whole folder on the TV, or skip "
        "to the next one. "
        "Use when: the user asks about their own recorded videos, by year, place or keyword — 「二零一九年在花蓮"
        "拍的影片」「放家裡那部影片」「播那個資料夾」「下一部」「play our old videos」「skip」. "
        "Do NOT use when: the user wants something from the internet — that is tv (YouTube) or the search tool. "
        "Do NOT use when: the user wants music rather than video — that is music. "
        "Pick `action`: `query` searches the index and returns matches; `play` plays one path or the best match; "
        "`play_folder` plays a whole folder; `skip` moves to the next video in a running folder."
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
