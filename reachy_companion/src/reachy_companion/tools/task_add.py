"""Add a task to the configured Google Tasks list (D-018). Filename == Tool.name."""

from __future__ import annotations
import asyncio
import logging
from typing import Any, Dict

from reachy_companion.hanova import gtasks, redact, settings
from reachy_companion.hanova.gauth import GoogleApiError, friendly_message
from reachy_companion.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


class TaskAdd(Tool):
    """Add one to-do item."""

    name = "task_add"
    description = "Add a to-do task. 用於新增待辦事項、記得要做的事。"
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "What needs doing."},
            "due": {"type": "string", "description": "Optional RFC3339 due date, e.g. 2026-09-02T00:00:00Z."},
            "notes": {"type": "string", "description": "Optional extra detail."},
        },
        "required": ["title"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Create the task and report its real id."""
        available, reason = settings.tool_status(self.name)
        if not available:
            return settings.unavailable(reason)

        title = str(kwargs.get("title", "")).strip()
        if not title:
            return {"ok": False, "error": "title is required"}
        list_id = settings.gtasks_list_id()
        if not list_id:
            # Unreachable while `HANOVA_GTASKS_LIST_ID` is a `tool_status`
            # prerequisite; kept so this tool can never POST to `/lists//tasks`
            # if that table is ever re-shaped.
            return {"ok": False, "error": "HANOVA_GTASKS_LIST_ID is not set; there is no list to write to."}

        logger.info("Tool call: task_add title=%s", redact.text(title))
        try:
            created = await asyncio.to_thread(
                gtasks.create_task,
                list_id=list_id,
                title=title,
                notes=str(kwargs.get("notes") or "") or None,
                due=str(kwargs.get("due") or "") or None,
            )
        except (GoogleApiError, OSError, ValueError, KeyError) as exc:
            # Finding 7: a Google error body quotes the task title back at us.
            logger.warning("task_add failed: %s", redact.error(exc))
            return {"ok": False, "error": friendly_message(exc)}
        return {"ok": True, "task_id": created.get("id"), "title": created.get("title"), "due": created.get("due")}
