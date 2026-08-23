"""Play music on Reachy's own speaker (D-018, R2). Filename == Tool.name.

Upstream had three music tools -- cast-to-TV, cast-to-puck, and a local one.
Only one survives here, and it always plays on the robot: a desk robot asked for
music is asked for *its* music, and that path needs no Home Assistant, no LAN
URL and no home network at all.
"""

from __future__ import annotations
import asyncio
import logging
import functools
from typing import Any, Dict
from pathlib import Path

from reachy_companion.hanova import nas, ytdlp, redact, settings, media_store
from reachy_companion.tools.core_tools import Tool, ToolDependencies
from reachy_companion.hanova.music_player import PLAYER


logger = logging.getLogger(__name__)

# Whole tracks get downloaded, so cap the length the way upstream did.
_MAX_TRACK_SECONDS = 900


class PlayMusic(Tool):
    """Search for a track and play it on the robot's speaker."""

    name = "play_music"
    description = "Play music on Reachy's own speaker. 用於任何放音樂、播首歌的請求。"
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Song, artist, or mood to search for.",
            },
        },
        "required": ["query"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Resolve the query, cache the audio, and play it on the robot."""
        available, reason = settings.tool_status(self.name)
        if not available:
            return settings.unavailable(reason)

        query = str(kwargs.get("query", "")).strip()
        if not query:
            return {"ok": False, "error": "query is required"}

        # Finding 7: metadata only. What the user asked for is not log material.
        logger.info("Tool call: play_music query=%s", redact.text(query))
        found = await asyncio.to_thread(ytdlp.search, query, _MAX_TRACK_SECONDS)
        if not found["ok"]:
            # yt-dlp's stderr echoes the query back, so it is summarised, never
            # forwarded (finding 7). The model gets a fixed, speakable reason.
            # `redact.error` on a plain string renders the constant "error" and
            # nothing else, so the shape of the failure was lost; `redact.text`
            # is the renderer for free text nobody vouched for (Task 4 review).
            logger.info("play_music search failed: %s", redact.text(found["error"] or ""))
            return {"ok": False, "error": "no playable result for that request"}

        music_dir = media_store.media_dir("music", deps.instance_path)
        # No mp3 re-encode for music: the daemon's playbin decodes the native
        # stream, and skipping ffmpeg was measured at 15.9 s -> 4.1 s per song.
        downloaded = await asyncio.to_thread(
            functools.partial(ytdlp.download_audio, found["id"], music_dir, transcode_mp3=False)
        )
        if not downloaded["ok"]:
            logger.info("play_music download failed: %s", redact.text(downloaded["error"] or ""))
            return {"ok": False, "error": "the audio could not be fetched right now"}

        result = await PLAYER.play(
            deps,
            video_id=str(found["id"]),
            title=str(found["title"]),
            source_path=Path(str(downloaded["path"])),
        )
        media_store.prune("music", deps.instance_path, settings.music_keep())
        if result.get("ok"):
            # D-018 / finding 16: this supersedes whatever trip was on the TV, so the
            # nas_skip playlist must not silently continue afterwards.
            nas.clear_session()
        return result
