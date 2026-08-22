"""Write one row into the shared notes database (D-018). Filename == Tool.name."""

from __future__ import annotations
import asyncio
import logging
from typing import Any, Dict

from reachy_companion.hanova import redact, settings, notion_client
from reachy_companion.tools.core_tools import Tool, ToolDependencies
from reachy_companion.hanova.notion_client import NotionError, friendly_message


logger = logging.getLogger(__name__)


class NotionAdd(Tool):
    """Add a note, shopping item or reminder to the shared notes database."""

    name = "notion_add"
    description = "Save a note to the shared notes database. 用於記錄備忘、購物、待辦。"
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "The note itself, in one line."},
            "type": {
                "type": "string",
                "enum": list(notion_client.TYPE_OPTIONS),
                "description": "What kind of note this is.",
            },
            "status": {
                "type": "string",
                "enum": list(notion_client.STATUS_OPTIONS),
                "description": "Optional status; actionable types default to pending.",
            },
            "tags": {"type": "string", "description": "Optional comma-separated tags."},
            "body": {"type": "string", "description": "Optional longer detail."},
        },
        "required": ["title"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Create the row and report the real page id and URL."""
        available, reason = settings.tool_status(self.name)
        if not available:
            return settings.unavailable(reason)

        title = str(kwargs.get("title", "")).strip()
        if not title:
            return {"ok": False, "error": "title is required"}

        logger.info("Tool call: notion_add title=%s", redact.text(title))
        try:
            page = await asyncio.to_thread(
                notion_client.add_page,
                title=title,
                type_=str(kwargs.get("type") or "") or None,
                status=str(kwargs.get("status") or "") or None,
                tags=str(kwargs.get("tags") or "") or None,
                body=str(kwargs.get("body") or "") or None,
            )
        except (NotionError, OSError, ValueError, KeyError) as exc:
            logger.warning("notion_add failed: %s", redact.error(exc))
            return {"ok": False, "error": friendly_message(exc)}
        return {"ok": True, "page_id": page.get("id"), "url": page.get("url"), "title": title}
