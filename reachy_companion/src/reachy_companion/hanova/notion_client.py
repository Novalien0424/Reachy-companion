"""Notion note writer, adapted from upstream `notion.py` (D-018).

This is a *schema-specific* writer for one notes database, and is deliberately
separate from the app's general remote Notion MCP lane (`NOTION_MCP_URL` /
`NOTION_MCP_TOKEN`): different auth, different surface, different keys.

Two things upstream did are not ported. The data-source id came from a cache
file committed to a public repo (`bin/notion/.cache/data_sources.json`); here it
is `HANOVA_NOTION_DATA_SOURCE_ID`. And the `Owner` select's options were real
people's names, so that property is dropped entirely.

The API version is pinned to the data-sources release. Do not bump it blind.
"""

from __future__ import annotations
import json
import logging
from typing import Any, Dict, List

from reachy_companion.hanova import settings, sync_http


logger = logging.getLogger(__name__)

API_BASE = "https://api.notion.com/v1"
API_VERSION = "2025-09-03"
# The API hard limit is 2000 characters per rich-text run; leave headroom.
BLOCK_LIMIT = 1900
_TIMEOUT_S = 30

TYPE_OPTIONS: tuple[str, ...] = ("購物", "待辦", "備忘", "聯絡", "其他")
STATUS_OPTIONS: tuple[str, ...] = ("待辦", "進行中", "完成")
# Upstream's rule: action-oriented rows start as pending so a status filter finds them.
_ACTIONABLE_TYPES = ("購物", "待辦", "聯絡")


class NotionError(RuntimeError):
    """A Notion API call that did not succeed.

    The parsed body is kept as `.body` for callers that need it, but the string
    form carries only the method, the path and the status: Notion echoes the
    submitted properties back inside a validation error, which would otherwise
    put the note the user dictated into the log (review finding 7).
    """

    def __init__(self, message: str, status: int | None = None, body: Dict[str, Any] | None = None) -> None:
        """Record the status and body without putting either into the message."""
        self.status = status
        self.body = body or {}
        super().__init__(message)


def friendly_message(exc: BaseException) -> str:
    """Return a fixed, identifier-free reason the model may say out loud (finding 7)."""
    status = getattr(exc, "status", None)
    if status == 401:
        return "the Notion integration token is not accepted"
    if status == 404:
        return "the Notion database could not be found for this integration"
    if isinstance(status, int):
        return f"Notion returned an error (HTTP {status})"
    return "the note could not be saved to Notion"


def notion_request(method: str, path: str, body: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Make one authenticated Notion API call. Raises NotionError on failure."""
    token = settings.notion_token()
    if not token:
        raise NotionError("HANOVA_NOTION_TOKEN is not set.")

    headers = {"Authorization": f"Bearer {token}", "Notion-Version": API_VERSION}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    status, raw = sync_http.request_bytes(method, f"{API_BASE}{path}", headers, data, _TIMEOUT_S)
    try:
        parsed = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        parsed = {"raw": raw.decode("utf-8", "replace")}
    if not (200 <= status < 300):
        # Finding 7: the body stays on the exception, out of the message.
        raise NotionError(f"HTTP {status} on {method} {path}", status=status, body=parsed)
    return parsed if isinstance(parsed, dict) else {}


def chunk_text(text: str, limit: int = BLOCK_LIMIT) -> List[str]:
    """Split *text* into runs no longer than *limit* characters."""
    if not text:
        return []
    return [text[index : index + limit] for index in range(0, len(text), limit)]


def _blocks(text: str) -> List[Dict[str, Any]]:
    return [
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": chunk}}]},
        }
        for chunk in chunk_text(text)
    ]


def add_page(
    title: str,
    type_: str | None = None,
    status: str | None = None,
    tags: str | None = None,
    body: str | None = None,
) -> Dict[str, Any]:
    """Create one row in the configured notes data source."""
    data_source_id = settings.notion_data_source_id()
    if not data_source_id:
        raise NotionError("HANOVA_NOTION_DATA_SOURCE_ID is not set.")

    effective_status = status
    if effective_status is None and type_ in _ACTIONABLE_TYPES:
        effective_status = "待辦"

    properties: Dict[str, Any] = {
        settings.notion_title_prop(): {"title": [{"type": "text", "text": {"content": title}}]},
    }
    if type_:
        properties["Type"] = {"select": {"name": type_}}
    if effective_status:
        properties["Status"] = {"select": {"name": effective_status}}
    if tags:
        names = [tag.strip() for tag in tags.split(",") if tag.strip()]
        if names:
            properties["Tags"] = {"multi_select": [{"name": name} for name in names]}

    payload: Dict[str, Any] = {"parent": {"data_source_id": data_source_id}, "properties": properties}
    if body:
        payload["children"] = _blocks(body)
    return notion_request("POST", "/pages", payload)
