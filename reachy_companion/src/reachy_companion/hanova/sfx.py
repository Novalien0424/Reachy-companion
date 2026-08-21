"""Cached sound-effect clips for the two gags (D-018, R2).

Upstream pushed these onto a Voice-PE puck through Home Assistant, using a URL
under HA's own web root. The robot has a speaker, so the clip is simply cached
once by video id in the `sfx` media directory and played locally -- no Home
Assistant, no LAN URL, and no home network required.

Playback goes through the shared `MusicPlayer`, so `stop_music` stops a gag and
user speech ducks it, with no code specific to gags (R7).

**Review finding 1: yt-dlp's own words never leave this module.** Its failure
text quotes the video URL and id, the local output path, and whatever the
network layer said -- and a gag's error is read aloud by the robot and sent to
OpenAI, so forwarding it would publish a configuration value the model must
never see (`settings.py:626-628`). `play_music.py:55-68` set the convention for
this exact function: log the *shape* through `redact.text` at INFO, return one
fixed, identifier-free reason. Fixing it here rather than in each tool means
both gags inherit it, and so does anything that calls `ensure_clip` later.
"""

from __future__ import annotations
import asyncio
import logging
from typing import Any, Dict
from pathlib import Path

from reachy_companion.hanova import ytdlp, redact, media_store
from reachy_companion.hanova.music_player import PLAYER


logger = logging.getLogger(__name__)

# The one thing a caller is ever told about a failed fetch. Fixed, speakable,
# and free of anything that identifies the clip, the host or the filesystem.
_FETCH_FAILED = "the clip could not be fetched right now"


async def ensure_clip(video_id: str, instance_path: str | Path | None) -> Dict[str, Any]:
    """Return the cached clip for *video_id*, downloading it once if needed."""
    sfx_dir = media_store.media_dir("sfx", instance_path)
    result = await asyncio.to_thread(ytdlp.download_audio, video_id, sfx_dir)
    if not result["ok"]:
        # Finding 1: the length is the diagnostic an operator gets; the text
        # itself is not ours to publish, to the log or to the model.
        logger.info("A gag clip could not be fetched: %s", redact.text(result["error"] or ""))
        return {"ok": False, "path": None, "error": _FETCH_FAILED}
    return {"ok": True, "path": result["path"], "error": None}


async def play_clip(
    deps: Any,
    video_id: str,
    title: str,
    instance_path: str | Path | None,
) -> Dict[str, Any]:
    """Fetch (once) and play a gag clip on the robot's speaker."""
    clip = await ensure_clip(video_id, instance_path)
    if not clip["ok"]:
        return {"ok": False, "error": clip["error"]}
    return await PLAYER.play(deps, video_id=video_id, title=title, source_path=Path(str(clip["path"])))
