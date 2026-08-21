"""Cast a video to the living-room TV (D-018, R2/R4). Filename == Tool.name.

This is upstream's "Path A": yt-dlp resolves the query to a YouTube id, and the
id alone is handed to a Home Assistant script that launches the TV's own YouTube
app. No bytes and no URL of ours ever leave the robot, so this works identically
from a Raspberry Pi as it did from the operator's Mac -- as long as the robot is
on the home LAN, which is what `home_state()` decides.
"""

from __future__ import annotations
import asyncio
import logging
from typing import Any, Dict

from reachy_companion.hanova import ytdlp, redact, settings
from reachy_companion.home_net import AWAY, HOME, home_state, home_unknown, away_from_home
from reachy_companion.hanova.ha_client import ha_run_script
from reachy_companion.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


class PlayVideo(Tool):
    """Search YouTube and cast the result to the TV."""

    name = "play_video"
    description = "Cast a video to the living-room TV. 用於在電視上播放影片。"
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "What to watch: a title, topic, or channel.",
            },
        },
        "required": ["query"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Resolve the query to a video id and ask Home Assistant to cast it."""
        available, reason = settings.tool_status(self.name)
        if not available:
            return settings.unavailable(reason)
        # Finding 12 + round 2 finding 3: three verdicts, three branches.
        # AWAY is proven absence and says so. UNKNOWN (401, HA down, a VPN, a
        # refused port) means we cannot tell -- so nothing is resolved and
        # nothing is cast, and the answer is its own status rather than a lie in
        # either direction.
        verdict = await home_state()
        if verdict == AWAY:
            return away_from_home()
        if verdict != HOME:
            # Round 2, finding 3: UNKNOWN is not permission. Nothing is
            # resolved, downloaded, generated, staged or cast on this path.
            return home_unknown()

        query = str(kwargs.get("query", "")).strip()
        if not query:
            return {"ok": False, "error": "query is required"}

        logger.info("Tool call: play_video query=%s", redact.text(query))
        found = await asyncio.to_thread(ytdlp.search, query, None)
        if not found["ok"]:
            logger.info("play_video search failed: %s", redact.text(found["error"] or ""))
            return {"ok": False, "error": "no playable result for that request"}

        fields: Dict[str, Any] = {"youtube_id": found["id"]}
        entity = settings.cast_entity()
        if entity:
            fields["entity_id"] = entity
        cast = await ha_run_script(settings.ha_script_youtube(), fields)
        if not cast["ok"]:
            logger.info("play_video cast failed: %s", redact.text(cast.get("error") or ""))
            return {"ok": False, "error": "Home Assistant could not start the video on the TV"}
        return {"ok": True, "status": "casting", "title": found["title"], "video_id": found["id"]}
