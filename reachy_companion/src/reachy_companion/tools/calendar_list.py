"""Read upcoming calendar events (D-018). Filename == Tool.name."""

from __future__ import annotations
import asyncio
import logging
from typing import Any, Dict
from datetime import datetime, timezone, timedelta

from reachy_companion.hanova import gcal, redact, settings
from reachy_companion.hanova.gauth import GoogleApiError, friendly_message
from reachy_companion.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


class CalendarList(Tool):
    """List upcoming events on the configured calendar."""

    name = "calendar_list"
    description = "List upcoming calendar events. 用於查行程、最近有什麼安排。"
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "days": {
                "type": "integer",
                "description": "How many days ahead to look. Default 7.",
                "minimum": 1,
                "maximum": 365,
            },
            "search": {"type": "string", "description": "Optional text to filter titles by."},
        },
        "required": [],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Return a compact list of upcoming events."""
        available, reason = settings.tool_status(self.name)
        if not available:
            return settings.unavailable(reason)

        try:
            days = int(kwargs.get("days") or 7)
        except (TypeError, ValueError):
            days = 7
        days = max(1, min(365, days))

        now = datetime.now(timezone.utc)
        logger.info("Tool call: calendar_list days=%d", days)
        try:
            events = await asyncio.to_thread(
                gcal.list_events,
                calendar_id=settings.gcal_calendar_id(),
                time_min=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                time_max=(now + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                limit=25,
                search=str(kwargs.get("search") or "") or None,
            )
        except (GoogleApiError, OSError, ValueError, KeyError) as exc:
            logger.warning("calendar_list failed: %s", redact.error(exc))
            return {"ok": False, "error": friendly_message(exc)}

        compact = [
            {
                "id": event.get("id"),
                "summary": event.get("summary"),
                "when": gcal.event_when(event),
                "location": event.get("location"),
            }
            for event in events
        ]
        return {"ok": True, "count": len(compact), "events": compact}
