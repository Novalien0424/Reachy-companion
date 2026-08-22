"""Create a calendar event (D-018). Filename == Tool.name."""

from __future__ import annotations
import asyncio
import logging
from typing import Any, Dict

from reachy_companion.hanova import gcal, redact, settings
from reachy_companion.hanova.gauth import GoogleApiError, friendly_message
from reachy_companion.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


class CalendarAdd(Tool):
    """Add an event to the configured calendar."""

    name = "calendar_add"
    description = "Add an event to the calendar. 用於新增行程、約會、提醒事項。"
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "Event title."},
            "start": {
                "type": "string",
                "description": "Start time, ISO-8601 with an offset, e.g. 2026-09-02T19:00:00+08:00.",
            },
            "end": {
                "type": "string",
                "description": "End time, ISO-8601 with an offset, e.g. 2026-09-02T20:30:00+08:00.",
            },
            "location": {"type": "string", "description": "Optional location."},
        },
        "required": ["summary", "start", "end"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Create the event and report its real id."""
        available, reason = settings.tool_status(self.name)
        if not available:
            return settings.unavailable(reason)

        summary = str(kwargs.get("summary", "")).strip()
        start = str(kwargs.get("start", "")).strip()
        end = str(kwargs.get("end", "")).strip()
        if not (summary and start and end):
            return {"ok": False, "error": "summary, start and end are all required"}

        calendar_id = settings.gcal_calendar_id()
        logger.info("Tool call: calendar_add summary=%s", redact.text(summary))
        try:
            created = await asyncio.to_thread(
                gcal.create_event,
                calendar_id=calendar_id,
                summary=summary,
                start=start,
                end=end,
                timezone_name=settings.timezone_name(),
                location=str(kwargs.get("location") or "") or None,
            )
        except (GoogleApiError, OSError, ValueError, KeyError) as exc:
            # Finding 7: a Google error body quotes the event back at us.
            logger.warning("calendar_add failed: %s", redact.error(exc))
            return {"ok": False, "error": friendly_message(exc)}
        return {
            "ok": True,
            "event_id": created.get("id"),
            "summary": created.get("summary"),
            "link": created.get("htmlLink"),
        }
