"""Speaker music, and the playback controls for whatever is playing, as one action-enum tool.

Filename == Tool.name. Since 2026-10-31 `stop` ends a video on the TV too,
because mini reached for it there first, and it ends the speaker and the TV in
the same call ("stop stops everything", plan W1). `pause` and `resume` joined
the same day (plan W2) and act on both halves the same way.

Four actions, one more than the skill's family size: the exception is recorded
in the plan, because mini's first call for 「停止」 and 「暫停」 was `music` 6/6 and
the four are one verb family; the alternative was a 20th tool.

Façade over `play_music` / `stop_music` and its siblings `PausePlayback` /
`ResumePlayback`, which keep their modules, their names and their prerequisite
rows -- including `stop_music`'s documented exemption
(`settings.TOOL_PREREQS["stop_music"] == ()`: stopping must work even when
playing never could). See `tool_family.py` for why.
"""

from __future__ import annotations
from typing import Any, Dict, Mapping, ClassVar

from reachy_companion.tools.core_tools import Tool, ToolDependencies
from reachy_companion.tools.play_music import PlayMusic
from reachy_companion.tools.stop_music import StopMusic, PausePlayback, ResumePlayback
from reachy_companion.tools.tool_family import dispatch_family


class Music(Tool):
    """Play the robot's own speaker music, and pause, resume or stop what plays, through one tool."""

    name = "music"
    ACTIONS: ClassVar[Mapping[str, Tool]] = {
        "play": PlayMusic(),
        "pause": PausePlayback(),
        "resume": ResumePlayback(),
        "stop": StopMusic(),
    }
    description = (
        "Play music on your own speaker, and pause, resume or stop what is playing. Use when: the user wants "
        "a song, an artist or a mood played, like 「放周杰倫的歌」 — `play` with `query`; or wants playback paused, "
        "resumed or stopped — `pause`, `resume` or `stop`, which act on everything playing, the music on your "
        "speaker and the video on the TV alike, and always work, even when play failed. `pause` keeps the "
        "place, so `resume` goes on from it; `stop` ends playback, so it starts over when played again. Do NOT use when: the "
        "user wants a video played on the TV — that is youtube or nas; or only wants you quiet — that is "
        "wait_for_user. Before `play`, say a short lead-in, because finding a track takes a moment "
        "(示範語氣，不是觸發條件：「我找一下來播」); `pause`, `resume` and `stop` need none, because the person wants "
        "them done now."
    )
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["play", "pause", "resume", "stop"],
                "description": "play 放音樂（要帶 query）；pause 暫停，停在原處；resume 繼續播放，從暫停的地方接著播；"
                "stop 停止播放，之後要從頭播。pause、resume、stop 都作用於喇叭的音樂和電視上的影片。",
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
