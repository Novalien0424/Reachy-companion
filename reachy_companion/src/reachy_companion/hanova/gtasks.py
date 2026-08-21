"""Google Tasks v1 calls, adapted from upstream `gtasks.py` (D-018).

Upstream's cross-list task resolver spawned one cold Python interpreter per task
list (`host-tools.py:231-271`, `:357-390`). In-process it is one HTTP call per
list, on one worker thread, with no interpreter startup at all.

Synchronous by design (it wraps `gauth`); tools call it via `asyncio.to_thread`.
"""

from __future__ import annotations
import logging
from typing import Any, Dict, List

from reachy_companion.hanova import gauth


logger = logging.getLogger(__name__)

T_BASE = "https://tasks.googleapis.com/tasks/v1"
_MAX_LISTS = 100


def list_task_lists() -> List[Dict[str, Any]]:
    """Return every task list on the account."""
    response = gauth.api_call("GET", f"{T_BASE}/users/@me/lists", query={"maxResults": _MAX_LISTS})
    items = response.get("items", [])
    return list(items) if isinstance(items, list) else []


def list_tasks(list_id: str, limit: int = 50, show_completed: bool = False) -> List[Dict[str, Any]]:
    """Return the tasks in one list."""
    query: Dict[str, Any] = {"maxResults": limit}
    if show_completed:
        query["showCompleted"] = "true"
        query["showHidden"] = "true"
    response = gauth.api_call("GET", f"{T_BASE}/lists/{list_id}/tasks", query=query)
    items = response.get("items", [])
    return list(items) if isinstance(items, list) else []


def create_task(list_id: str, title: str, notes: str | None = None, due: str | None = None) -> Dict[str, Any]:
    """Create one task in *list_id*."""
    body: Dict[str, Any] = {"title": title}
    if notes:
        body["notes"] = notes
    if due:
        body["due"] = due
    return gauth.api_call("POST", f"{T_BASE}/lists/{list_id}/tasks", body=body)


def complete_task(list_id: str, task_id: str) -> Dict[str, Any]:
    """Mark one task completed (reversible in the Tasks UI)."""
    return gauth.api_call("PATCH", f"{T_BASE}/lists/{list_id}/tasks/{task_id}", body={"status": "completed"})


def delete_task(list_id: str, task_id: str) -> None:
    """Delete one task permanently."""
    gauth.api_call("DELETE", f"{T_BASE}/lists/{list_id}/tasks/{task_id}")


def find_task(
    match: str,
    include_completed: bool = False,
) -> tuple[Dict[str, Any] | None, List[Dict[str, Any]], str | None]:
    """Resolve *match* to exactly one task across every list.

    The returned task and each candidate carry two extra keys, `list_id` and
    `list_title`, so a caller can act on the result and name it to the user
    without a second lookup.
    """
    needle = match.strip().lower()
    matches: List[Dict[str, Any]] = []
    for task_list in list_task_lists():
        list_id = str(task_list.get("id") or "")
        if not list_id:
            continue
        for task in list_tasks(list_id, limit=50, show_completed=include_completed):
            if needle in str(task.get("title") or "").lower():
                enriched = dict(task)
                enriched["list_id"] = list_id
                enriched["list_title"] = task_list.get("title")
                matches.append(enriched)
    if not matches:
        return None, [], "not_found"
    if len(matches) > 1:
        return None, matches, "ambiguous"
    return matches[0], [], None
