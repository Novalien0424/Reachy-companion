"""The one blocking-HTTP call every synchronous service module makes (D-018).

Google, Notion and Drive are ported from stdlib `urllib` code and stay
synchronous; tools call them through `asyncio.to_thread`. Routing all of them
through this single function means one monkeypatch in a test covers every
service, and there is exactly one place where a timeout or a header policy
changes.

Callers MUST reach it through the module (`sync_http.request_bytes(...)`), not
`from ... import request_bytes`, or monkeypatching will miss their binding.
"""

from __future__ import annotations
import logging
import urllib.error
import urllib.request
from typing import Dict


logger = logging.getLogger(__name__)


def request_bytes(
    method: str,
    url: str,
    headers: Dict[str, str],
    data: bytes | None = None,
    timeout_s: int = 15,
) -> tuple[int, bytes]:
    """Perform one HTTP request and return (status_code, body_bytes).

    An HTTP error status is returned like any other status -- it is data, not an
    exception -- so callers decide what a 401 or a 404 means for them.
    """
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            return int(response.getcode()), bytes(response.read())
    except urllib.error.HTTPError as exc:
        body = exc.read() if exc.fp is not None else b""
        return int(exc.code), body or b"{}"
