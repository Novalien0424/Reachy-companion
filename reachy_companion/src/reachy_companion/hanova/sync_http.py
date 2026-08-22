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
import http.client
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

    Every *other* outcome leaves here as an `OSError`. That is the seam's half of
    the contract, and it is what makes each caller's
    `except (GoogleApiError, OSError, ValueError, KeyError)` complete.
    """
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            return int(response.getcode()), bytes(response.read())
    except urllib.error.HTTPError as exc:
        body = exc.read() if exc.fp is not None else b""
        return int(exc.code), body or b"{}"
    except http.client.HTTPException as exc:
        # Round 3 review, Important: a flaky link makes `urlopen` (or the
        # `read()` inside the `with`) raise `IncompleteRead`, `BadStatusLine` or
        # `LineTooLong`. Those descend from `HTTPException`, **not** from
        # `OSError`, so without this they escape un-normalized -- and the six
        # service modules that route through this one function all assume
        # otherwise. `calendar_add` would hand the model an exception instead of
        # `{"ok": False}`, and `calendar_delete` would spend the user's
        # confirmation through its unexpected-failure fallback on what is in
        # fact the most retryable fault there is. Converting to `OSError` puts it
        # back on the branch every caller already handles, and makes
        # `gauth.is_transient` answer True for it.
        #
        # Only the exception *class* is carried into the message: a status line
        # or a header is written by the far end, and finding 7 keeps far-end text
        # out of anything loggable. The original stays reachable as `__cause__`.
        raise OSError(f"HTTP transport failure: {type(exc).__name__}") from exc
