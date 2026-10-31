"""Stop what Reachy is playing: speaker music, else the TV video (D-018, R2). Filename == Tool.name.

2026-10-31: 「停下你的播放」 reached this tool while a NAS clip played on the TV,
got `nothing_playing`, and Reachy said it had stopped while the video went on.
So when the speaker is silent, the stop goes to the TV: `stop_tv_playback`
stops whatever the cast target is playing and reads the TV back, and the result
names what was stopped, or says plainly that nothing was.

This tool must always answer. Upstream's single-threaded server could not stop
music while a download was in flight; here every tool is its own asyncio task
(`huggingface_realtime.py:1011`), so this one is never starved.

It is also the one ported tool with **no prerequisites at all**
(`settings.TOOL_PREREQS["stop_music"] == ()`, review finding 10): a robot that
cannot be silenced by voice is a safety defect, so this tool stays reachable even
when yt-dlp is missing and nothing could have started the music in the first
place. That is a deliberate exemption, not a missing family check.
"""

from __future__ import annotations
import logging
from typing import Any, Dict

from reachy_companion.hanova import nas, settings
from reachy_companion.hanova.ha_client import ha_get_state, stop_tv_playback, media_state_and_title
from reachy_companion.tools.core_tools import Tool, ToolDependencies
from reachy_companion.hanova.music_player import PLAYER


logger = logging.getLogger(__name__)


def _tv_entity() -> str:
    """Return the cast target, or "" when Home Assistant is not set up to reach it."""
    if not (settings.ha_url() and settings.ha_token()):
        return ""
    return settings.cast_entity()


class StopMusic(Tool):
    """Stop the speaker music, or, when none plays, the video on the TV."""

    name = "stop_music"
    description = "Stop what Reachy is playing: the music on its speaker, else the video on the TV."
    parameters_schema: Dict[str, Any] = {"type": "object", "properties": {}, "required": []}

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Stop the speaker if it plays; otherwise stop the TV and read it back."""
        logger.info("Tool call: stop_music")
        speaker = await PLAYER.stop(deps)
        entity = _tv_entity()
        if speaker.get("status") != "nothing_playing":
            if speaker.get("status") == "stopped" and entity:
                tv_state, _ = media_state_and_title(await ha_get_state(entity, timeout_s=5.0))
                if tv_state in ("playing", "buffering", "paused"):
                    return {
                        **speaker,
                        "stopped": "speaker",
                        "tv_still_playing": True,
                        "note": "a video is still playing on the TV; if the person wanted that stopped "
                        "too, call stop again yourself",
                    }
            return {**speaker, "stopped": "speaker"} if speaker.get("status") == "stopped" else speaker
        if not entity:
            return {**speaker, "detail": "nothing is playing on your speaker, so nothing was stopped"}
        tv = await stop_tv_playback(entity)
        logger.info("stop_music: speaker idle, TV stop %s", tv["result"])
        if tv["result"] == "stopped":
            # The trip is over: `skip` must not quietly carry it on.
            nas.clear_session()
            # Live 2026-10-31: asked to 暫停, Reachy said the video was waiting to go on.
            return {
                "ok": True,
                "status": "stopped",
                "stopped": "tv",
                "title": tv["title"],
                "note": "stopped, not paused: playing it again starts it from the beginning",
            }
        if tv["result"] == "not_playing":
            return {
                "ok": True,
                "status": "nothing_playing",
                "detail": "nothing is playing on your speaker or on the TV, so nothing was stopped; "
                "say so, and do not say you stopped something",
            }
        if tv["result"] == "still_playing":
            return {
                "ok": False,
                "error": f"the video on the TV did not stop (it still reads {tv['state']}). Tell the person "
                "it is still playing and that the remote can stop it; do not say it stopped.",
            }
        return {
            "ok": False,
            "error": "nothing was playing on your speaker, and Home Assistant did not answer about the TV, "
            "so a video there could not be stopped. Tell the person to stop it with the remote.",
        }
