"""Play a maniacal-laughter clip on the robot's speaker (D-018). Filename == Tool.name."""

from __future__ import annotations
import logging
from typing import Any, Dict

from reachy_companion.hanova import sfx, settings
from reachy_companion.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


class MadLaugh(Tool):
    """Play the maniacal-laughter clip."""

    name = "mad_laugh"
    description = "Play a maniacal laugh out loud. 用於開玩笑、耍壞、假裝反派。"
    parameters_schema: Dict[str, Any] = {"type": "object", "properties": {}, "required": []}

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Play the cached laugh clip on the robot's speaker."""
        # Finding 10: the clip id is this tool's own prerequisite, not the
        # family's -- "music enabled" said nothing about whether a gag can play.
        available, reason = settings.tool_status(self.name)
        if not available:
            return settings.unavailable(reason)

        logger.info("Tool call: mad_laugh")
        return await sfx.play_clip(deps, settings.mad_laugh_yt_id(), "mad laugh", deps.instance_path)
