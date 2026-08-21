"""Read the to-do lists (D-018). Filename == Tool.name."""

from __future__ import annotations
import asyncio
import logging
from typing import Any, Dict, List

from reachy_companion.hanova import gtasks, redact, settings
from reachy_companion.hanova.gauth import GoogleApiError, friendly_message
from reachy_companion.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)

_PER_LIST_LIMIT = 50


class TaskList(Tool):
    """List outstanding to-do items, grouped by list."""

    name = "task_list"
    description = "List outstanding to-do tasks. 用於查待辦事項、還有什麼沒做。"
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "include_completed": {
                "type": "boolean",
                "description": "Include tasks already finished. Default false.",
            },
        },
        "required": [],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Return every list with its tasks."""
        available, reason = settings.tool_status(self.name)
        if not available:
            return settings.unavailable(reason)

        include_completed = bool(kwargs.get("include_completed"))
        logger.info("Tool call: task_list include_completed=%s", include_completed)

        def collect() -> List[Dict[str, Any]]:
            """Walk every list once, on the worker thread, and compact the result."""
            grouped: List[Dict[str, Any]] = []
            for task_list in gtasks.list_task_lists():
                list_id = str(task_list.get("id") or "")
                if not list_id:
                    continue
                tasks = gtasks.list_tasks(list_id, limit=_PER_LIST_LIMIT, show_completed=include_completed)
                grouped.append(
                    {
                        "id": list_id,
                        "title": task_list.get("title"),
                        "tasks": [
                            {"id": task.get("id"), "title": task.get("title"), "due": task.get("due")}
                            for task in tasks
                        ],
                    }
                )
            return grouped

        try:
            lists = await asyncio.to_thread(collect)
        except (GoogleApiError, OSError, ValueError, KeyError) as exc:
            logger.warning("task_list failed: %s", redact.error(exc))
            return {"ok": False, "error": friendly_message(exc)}
        return {"ok": True, "count": sum(len(entry["tasks"]) for entry in lists), "lists": lists}
