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
        "Play or stop audio through the robot's own speaker. "
        "Use when: the user wants audio played through the robot, including a song, an artist, "
        "background music, or YouTube playback — 「放首歌」「放周杰倫」「play some music」; "
        "choose `action=play` with `query`. "
        "Use when: the user wants the robot's speaker playback stopped — 「音樂關掉」「停止播放」「stop the music」; "
        "choose `action=stop` and pass no extra fields; `stop` ALWAYS works, even when playing does not. "
        "Do NOT use when: the user wants a video shown on the TV; that is tv or nas. "
        "Do NOT use when: the user just wants you to be quiet for a moment; that is wait_for_user. "
        "Preamble sample for `play`: 「我找一下來播」 (示範語氣，不是觸發條件). "
        "Say a brief lead-in in the conversation's language before `play` because finding playable media can take "
        "a moment, then call the tool. "
        "`stop` is instant and should have no preamble because the person wants silence now."
    )
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["play", "stop"],
                "description": "play 放音樂（要帶 query）；stop 停止播放（不用帶任何欄位）。",
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
