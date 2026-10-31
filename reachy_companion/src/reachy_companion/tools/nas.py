"""The household video archive as one action-enum tool. Filename == Tool.name.

Façade over `play_nas_video` / `nas_video_query` / `nas_skip`, which keep their
modules, their names, their `settings.tool_status` prerequisite rows and their
home-network verdict branches. See `tool_family.py` for why.

2026-10-30: three actions, by intent. `play` finds and plays in one call (it
absorbed `play_folder`), `list` only tells what is there. Live, the mini model
searched first in every session and sometimes stopped there, and a 2,000-char
description of rules did not change that.

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
from reachy_companion.tools.nas_video_query import NasVideoQuery


class Nas(Tool):
    """Search and play the family's own recorded videos through one tool."""

    name = "nas"
    ACTIONS: ClassVar[Mapping[str, Tool]] = {
        "play": PlayNasVideo(),
        "list": NasVideoQuery(),
        "skip": NasSkip(),
    }
    description = (
        "The family's own videos (家裡的影片庫) on the NAS, played on the living-room TV. Use when: the user "
        "names the NAS or the 影片庫, or means the family's own recordings, like 「放我們去花蓮玩的影片」"
        ". Ask first when: the request gives only a place, a trip, a year or an event "
        "and doesn't say whose, like 「放花蓮的影片」 — ask 影片庫 or YouTube and wait for the answer, then do "
        "what they asked. Do NOT use when: the user names YouTube or wants something public, like an MV — "
        "that is youtube; or music — that is music. `play` "
        "finds a clip or a trip and plays it, in one call; `list` tells what is there and plays nothing; "
        "`skip` plays the next clip when asked; to pause or stop it, use music. Search each new trip request "
        "afresh, in English words, as the archive is catalogued. If nothing "
        "in the 影片庫 fits, say so and offer YouTube."
    )
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "play", "skip"],
                "description": "play 找出來並放到電視上；list 只說有哪些，不播放；skip 播下一段。",
            },
            # `year`, `place` and `keyword` are shared by `play` and `list`, and
            # the last spread wins, so the model reads `list`'s wording, which
            # carries the translation examples. `path` and `top_folder` belong to
            # `play` alone.
            **PlayNasVideo.parameters_schema["properties"],
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
