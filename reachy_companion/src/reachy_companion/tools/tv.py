"""The living-room television as one action-enum tool. Filename == Tool.name.

Façade over `play_video` / `show_on_tv`, which keep their modules, their names,
their `settings.tool_status` prerequisite rows and their three-way home-network
verdict branches. See `tool_family.py` for why.
"""

from __future__ import annotations
from typing import Any, Dict, Mapping, ClassVar

from reachy_companion.tools.core_tools import Tool, ToolDependencies
from reachy_companion.tools.play_video import PlayVideo
from reachy_companion.tools.show_on_tv import ShowOnTv
from reachy_companion.tools.tool_family import dispatch_family


class Tv(Tool):
    """Cast a video or a generated picture to the TV through one tool."""

    name = "tv"
    ACTIONS: ClassVar[Mapping[str, Tool]] = {
        "play_video": PlayVideo(),
        "show": ShowOnTv(),
    }
    description = (
        "The television: play a video from the internet on it, or put a generated picture on the screen. "
        "Use when: the user asks to watch something or to see something on the big screen — 「電視上放那個 MV」"
        "「幫我在電視上播」「畫一張圖放到電視上」「put it on the TV」「show me that on screen」. "
        "Do NOT use when: the user means their own recorded videos from the NAS — that is nas. "
        "Do NOT use when: the user wants sound only — that is music. "
        "Pick `action`: `play_video` needs query (what to search for and play); `show` needs request (what "
        "picture to generate and display)."
    )
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["play_video", "show"],
                "description": "play_video 在電視上播影片（要帶 query）；show 產生一張圖放到電視上（要帶 request）。",
            },
            **PlayVideo.parameters_schema["properties"],
            **ShowOnTv.parameters_schema["properties"],
        },
        "required": ["action"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Forward one TV action to the tool that has always handled it."""
        return await dispatch_family(
            family=self.name,
            action=kwargs.get("action"),
            actions=self.ACTIONS,
            deps=deps,
            kwargs=kwargs,
        )
