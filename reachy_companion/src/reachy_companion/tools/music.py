"""Speaker music as one action-enum tool. Filename == Tool.name.

Façade over `play_music` / `stop_music`, which keep their modules, their names
and their prerequisite rows -- including `stop_music`'s documented exemption
(`settings.TOOL_PREREQS["stop_music"] == ()`: stopping must work even when
playing never could). See `tool_family.py` for why.
"""

from __future__ import annotations
from typing import Any, Dict, Mapping, ClassVar

from reachy_companion.tools.core_tools import Tool, ToolDependencies
from reachy_companion.tools.play_music import PlayMusic
from reachy_companion.tools.stop_music import StopMusic
from reachy_companion.tools.tool_family import dispatch_family


class Music(Tool):
    """Play and stop the robot's own speaker music through one tool."""

    name = "music"
    ACTIONS: ClassVar[Mapping[str, Tool]] = {
        "play": PlayMusic(),
        "stop": StopMusic(),
    }
    description = (
        "Play or stop audio on your own speaker. Use when: the user wants a song, an artist or a mood "
        "played, like 「放周杰倫的歌」 — `play` with `query`; or the speaker stopped — `stop`, which always "
        "works, even when play failed. Do NOT use when: the user wants a video on the TV — that is "
        "youtube or nas; or only wants you quiet — that is wait_for_user. Before `play`, say a short "
        "lead-in, because finding a track takes a moment (示範語氣，不是觸發條件：「我找一下來播」); `stop` needs none, "
        "because the person wants silence now."
    )
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["play", "stop"],
                "description": "play 放音樂（要帶 query）；stop 停止播放。",
            },
            **PlayMusic.parameters_schema["properties"],
            **StopMusic.parameters_schema["properties"],
        },
        "required": ["action"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Forward one music action to the tool that has always handled it."""
        return await dispatch_family(
            family=self.name,
            action=kwargs.get("action"),
            actions=self.ACTIONS,
            deps=deps,
            kwargs=kwargs,
        )
