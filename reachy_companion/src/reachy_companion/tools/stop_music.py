"""Stop the music on Reachy's speaker (D-018, R2). Filename == Tool.name.

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

from reachy_companion.tools.core_tools import Tool, ToolDependencies
from reachy_companion.hanova.music_player import PLAYER


logger = logging.getLogger(__name__)


class StopMusic(Tool):
    """Stop whatever music is playing on the robot's speaker."""

    name = "stop_music"
    description = "Stop the music Reachy is playing. 用於停止、關掉、別放了。"
    parameters_schema: Dict[str, Any] = {"type": "object", "properties": {}, "required": []}

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Stop the robot-speaker music session."""
        logger.info("Tool call: stop_music")
        return await PLAYER.stop(deps)
