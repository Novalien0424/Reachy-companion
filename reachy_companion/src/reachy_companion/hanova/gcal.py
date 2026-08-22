"""Google Calendar v3 calls, adapted from upstream `gcal.py` (D-018).

Upstream reached these through a subprocess CLI behind a localhost HTTP shim
(`host-tools.py:79-97`); the shim and the subprocess are both deleted here. The
default calendar id is configuration, never a literal.

Synchronous by design (it wraps `gauth`); tools call it via `asyncio.to_thread`.
"""

from __future__ import annotations
import logging
from typing import Any, Dict, List
from datetime import datetime, timezone, timedelta

from reachy_companion.hanova import gauth


logger = logging.getLogger(__name__)

CAL_BASE = "https://www.googleapis.com/calendar/v3"


def create_event(
    calendar_id: str,
    summary: str,
    start: str,
    end: str,
    timezone_name: str,
    location: str | None = None,
    description: str | None = None,
) -> Dict[str, Any]:
    """Create one timed event. *start* and *end* are ISO-8601 with an offset."""
    body: Dict[str, Any] = {
        "summary": summary,
        "start": {"dateTime": start, "timeZone": timezone_name},
        "end": {"dateTime": end, "timeZone": timezone_name},
    }
    if location:
        body["location"] = location
    if description:
        body["description"] = description
    return gauth.api_call("POST", f"{CAL_BASE}/calendars/{calendar_id}/events", body=body)


def list_events(
    calendar_id: str,
    time_min: str,
    time_max: str,
    limit: int = 25,
    search: str | None = None,
) -> List[Dict[str, Any]]:
    """Return the expanded events in a window, ordered by start time."""
    query: Dict[str, Any] = {
        "timeMin": time_min,
        "timeMax": time_max,
        "singleEvents": "true",
        "orderBy": "startTime",
        "maxResults": limit,
    }
    if search:
        query["q"] = search
    response = gauth.api_call("GET", f"{CAL_BASE}/calendars/{calendar_id}/events", query=query)
    items = response.get("items", [])
    return list(items) if isinstance(items, list) else []


def delete_event(calendar_id: str, event_id: str) -> None:
    """Delete one event by id."""
    gauth.api_call("DELETE", f"{CAL_BASE}/calendars/{calendar_id}/events/{event_id}")


def event_when(event: Dict[str, Any]) -> str:
    """Return a short human-readable start for a confirmation read-back."""
    start = event.get("start") or {}
    return str(start.get("dateTime") or start.get("date") or "")


def find_event(
    calendar_id: str,
    match: str,
    window_days: int,
) -> tuple[Dict[str, Any] | None, List[Dict[str, Any]], str | None]:
    """Resolve *match* to exactly one event, or report not_found / ambiguous.

    The window is UTC (`now - 1 day` .. `now + window_days`), which Google accepts
    regardless of the calendar's own timezone, so no local tz database is needed.
    """
    now = datetime.now(timezone.utc)
    time_min = (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    time_max = (now + timedelta(days=window_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    needle = match.strip().lower()
    events = list_events(calendar_id, time_min, time_max, limit=100)
    matches = [event for event in events if needle in str(event.get("summary") or "").lower()]
    if not matches:
        return None, [], "not_found"
    if len(matches) > 1:
        return None, matches, "ambiguous"
    return matches[0], [], None
