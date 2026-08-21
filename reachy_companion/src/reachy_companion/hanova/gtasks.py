"""Google Tasks v1 calls, adapted from upstream `gtasks.py` (D-018).

Upstream's cross-list task resolver spawned one cold Python interpreter per task
list (`host-tools.py:231-271`, `:357-390`). In-process it is one HTTP call per
list, on one worker thread, with no interpreter startup at all.

**Two things the Task 8 review changed.**

*Finding 1 -- `showCompleted` is always stated.* Google's `tasks.list` defaults
`showCompleted` to **true** (`showHidden` to false), so omitting the flag asked
for completed tasks. `complete_task` PATCHes `status` and cannot set the
read-only `hidden` flag, so anything ticked off through this app came straight
back in the next listing: `task_list` reported finished work as outstanding, and
`find_task` could resolve to an already-completed item or call a genuinely unique
match ambiguous because of its own completed duplicate. The flag is now sent on
every request, in both directions; `showHidden` stays conditional.

*Finding 2 -- one page is not a list.* Both collection calls now follow
`nextPageToken` up to a hard page cap and report whether they stopped with more
still available. This matters most for the ambiguity decision: a truncated walk
proves neither uniqueness nor absence, so a second "milk" sitting on the page
nobody read would have turned into a confident, unique match that a gated tool
then armed on. `find_task` refuses to declare an unambiguous match on a
truncated walk, and the gated tools turn that into an honest "I could not search
all of it" instead of a guess.

The caps bound the worst case; they do not describe the normal one. A page after
the first is fetched **only** when the previous page came back full, so a
realistic account -- a handful of lists, tens of tasks each -- still costs
exactly one request per list, as it did before pagination existed. The
pathological bound is `_MAX_LIST_PAGES + _MAX_LISTS * _MAX_TASK_PAGES` requests,
and that account gets a `truncated` refusal rather than a wrong answer.

Synchronous by design (it wraps `gauth`); tools call it via `asyncio.to_thread`.
"""

from __future__ import annotations
import logging
from typing import Any, Dict, List, Tuple

from reachy_companion.hanova import gauth


logger = logging.getLogger(__name__)

T_BASE = "https://tasks.googleapis.com/tasks/v1"

# Review finding 2: page size x page cap, named so the ceiling is visible rather
# than implied. Both ceilings are far above any real account and far below a
# walk that would out-wait a voice turn.
_LISTS_PAGE_SIZE = 100
_MAX_LIST_PAGES = 2
_MAX_LISTS = _LISTS_PAGE_SIZE * _MAX_LIST_PAGES

_TASKS_PAGE_SIZE = 100
_MAX_TASK_PAGES = 3
_FIND_TASK_LIMIT = _TASKS_PAGE_SIZE * _MAX_TASK_PAGES


def _paged(
    url: str,
    query: Dict[str, Any],
    limit: int,
    page_size: int,
    max_pages: int,
) -> Tuple[List[Dict[str, Any]], bool]:
    """Collect up to *limit* items across `nextPageToken`, capped at *max_pages*.

    Returns the items and whether the walk stopped while more were still
    available -- either because the item limit was reached or because the page
    cap was. A caller that cannot act safely on a partial answer must check it.
    """
    items: List[Dict[str, Any]] = []
    token: str | None = None
    for _ in range(max_pages):
        page_query = dict(query)
        page_query["maxResults"] = max(1, min(limit - len(items), page_size))
        if token:
            page_query["pageToken"] = token
        response = gauth.api_call("GET", url, query=page_query)
        batch = response.get("items", [])
        if isinstance(batch, list):
            items.extend(batch)
        token = response.get("nextPageToken") or None
        if token is None or len(items) >= limit:
            break
    return items[:limit], token is not None or len(items) > limit


def list_task_lists_page() -> Tuple[List[Dict[str, Any]], bool]:
    """Return every task list on the account, plus whether the walk was capped."""
    return _paged(
        f"{T_BASE}/users/@me/lists",
        {},
        limit=_MAX_LISTS,
        page_size=_LISTS_PAGE_SIZE,
        max_pages=_MAX_LIST_PAGES,
    )


def list_task_lists() -> List[Dict[str, Any]]:
    """Return every task list on the account, up to the paging cap."""
    return list_task_lists_page()[0]


def list_tasks_page(
    list_id: str,
    limit: int = 50,
    show_completed: bool = False,
) -> Tuple[List[Dict[str, Any]], bool]:
    """Return the tasks in one list, plus whether the walk was capped."""
    # Finding 1: stated in both directions. Silence means "true" to Google.
    query: Dict[str, Any] = {"showCompleted": "true" if show_completed else "false"}
    if show_completed:
        # Completing a task hides it; only a caller that wants completed tasks
        # wants the hidden ones too.
        query["showHidden"] = "true"
    return _paged(
        f"{T_BASE}/lists/{list_id}/tasks",
        query,
        limit=limit,
        page_size=_TASKS_PAGE_SIZE,
        max_pages=_MAX_TASK_PAGES,
    )


def list_tasks(list_id: str, limit: int = 50, show_completed: bool = False) -> List[Dict[str, Any]]:
    """Return the tasks in one list, up to *limit*."""
    return list_tasks_page(list_id, limit=limit, show_completed=show_completed)[0]


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

    *error* is `"ambiguous"`, `"truncated"`, `"not_found"`, or None for a unique
    match. `"ambiguous"` outranks `"truncated"` because both refuse and the
    ambiguous one hands back a usable choice; `"truncated"` outranks both a lone
    match and `"not_found"` because a capped walk (review finding 2) can prove
    neither that the one thing it saw is the only one nor that a thing it never
    saw is absent. Whatever partial matches were found still come back as
    candidates, so the model can name them while admitting it did not finish.
    """
    needle = match.strip().lower()
    matches: List[Dict[str, Any]] = []
    task_lists, truncated = list_task_lists_page()
    for task_list in task_lists:
        list_id = str(task_list.get("id") or "")
        if not list_id:
            continue
        tasks, list_truncated = list_tasks_page(
            list_id,
            limit=_FIND_TASK_LIMIT,
            show_completed=include_completed,
        )
        truncated = truncated or list_truncated
        for task in tasks:
            if needle in str(task.get("title") or "").lower():
                enriched = dict(task)
                enriched["list_id"] = list_id
                enriched["list_title"] = task_list.get("title")
                matches.append(enriched)
    if len(matches) > 1:
        return None, matches, "ambiguous"
    if truncated:
        return None, matches, "truncated"
    if not matches:
        return None, [], "not_found"
    return matches[0], [], None
