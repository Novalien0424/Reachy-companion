"""List one Drive folder (D-018). Filename == Tool.name."""

from __future__ import annotations
import asyncio
import logging
from typing import Any, Dict

from reachy_companion.hanova import gdrive, redact, settings
from reachy_companion.hanova.gdrive import DriveError, friendly_message
from reachy_companion.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200


class DriveList(Tool):
    """List what is in the configured Drive folder."""

    name = "drive_list"
    description = "List files in the shared Drive folder. 用於查雲端硬碟裡有什麼檔案。"
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "How many entries to return. Default 50.",
                "minimum": 1,
                "maximum": _MAX_LIMIT,
            },
        },
        "required": [],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Return a compact listing of one folder level."""
        available, reason = settings.tool_status(self.name)
        if not available:
            return settings.unavailable(reason)

        try:
            limit = int(kwargs.get("limit") or _DEFAULT_LIMIT)
        except (TypeError, ValueError):
            limit = _DEFAULT_LIMIT
        limit = max(1, min(_MAX_LIMIT, limit))

        logger.info("Tool call: drive_list limit=%d", limit)
        try:
            files = await asyncio.to_thread(gdrive.list_files, settings.drive_parent_id(), limit, False)
        except (DriveError, OSError, ValueError, KeyError) as exc:
            logger.warning("drive_list failed: %s", redact.error(exc))
            return {"ok": False, "error": friendly_message(exc)}

        compact = [
            {
                "id": entry.get("id"),
                "name": entry.get("name"),
                "is_folder": entry.get("mimeType") == gdrive.FOLDER_MIME,
                "modified": entry.get("modifiedTime"),
            }
            for entry in files
        ]
        return {"ok": True, "count": len(compact), "files": compact}
