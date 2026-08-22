"""Google OAuth for Calendar and Tasks, adapted from upstream `gauth.py` (D-018).

Upstream hardcoded the account address at `bin/google/gauth.py:22`; here it comes
from `HANOVA_GOOGLE_ACCOUNT`, and the credentials directory from
`GOOGLE_CREDS_DIR` (upstream's own env name, kept per manifest section D).

The credentials file is **rewritten** whenever the access token is refreshed, so
the robot needs its own writable copy -- which is why it lives in the app
instance directory and is part of the deploy backup ritual.

Everything here is synchronous. Tools must call it via `asyncio.to_thread`.
"""

from __future__ import annotations
import os
import json
import logging
import tempfile
import threading
import contextlib
import urllib.parse
from typing import Any, Dict
from pathlib import Path
from datetime import datetime, timezone, timedelta

from reachy_companion.hanova import settings, sync_http


logger = logging.getLogger(__name__)

TOKEN_URI = "https://oauth2.googleapis.com/token"
_TOKEN_SLACK_SECONDS = 60
_TIMEOUT_S = 15

# Finding 14: Calendar and Tasks share one credentials file and run as separate
# worker threads, so every read-refresh-write cycle is serialised per path.
_LOCK_REGISTRY: Dict[str, threading.Lock] = {}
_LOCK_REGISTRY_LOCK = threading.Lock()


class GoogleApiError(RuntimeError):
    """A non-2xx response from a Google API.

    The parsed body is kept on the exception for callers that need it, but the
    string form deliberately carries **only** the method, the path shape and the
    status: Google's error messages quote the request back, including event
    titles, addresses and file names (review finding 7). Use `friendly_message()`
    for anything the model will say out loud.
    """

    def __init__(self, status: int, body: Dict[str, Any], url: str, method: str) -> None:
        """Record the status and the parsed error body for the caller."""
        self.status = status
        self.body = body
        host = urllib.parse.urlsplit(url).netloc
        super().__init__(f"{method} {host} -> HTTP {status}")


_STATUS_MESSAGES = {
    401: "the Google account needs to be re-authorised on the robot",
    403: "Google refused that request for this account",
    404: "Google could not find that item",
    429: "Google is rate-limiting this account right now",
}


def friendly_message(exc: BaseException) -> str:
    """Return a fixed, identifier-free reason the model may say out loud (finding 7)."""
    status = getattr(exc, "status", None)
    if isinstance(status, int):
        return _STATUS_MESSAGES.get(status, f"Google returned an error (HTTP {status})")
    return "the Google request could not be completed"


# A 5xx, a rate limit or a socket error may be retried on the same confirmation;
# a 4xx means the resolved action itself is wrong, so the authorisation is spent
# (review finding 4).
_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})


def is_transient(exc: BaseException) -> bool:
    """Return whether a gated tool may retry on the same confirmation."""
    status = getattr(exc, "status", None)
    if isinstance(status, int):
        return status in _RETRYABLE_STATUSES
    return isinstance(exc, OSError)


def _creds_path(account: str) -> Path:
    creds_dir = settings.google_creds_dir()
    if creds_dir is None:
        raise FileNotFoundError("GOOGLE_CREDS_DIR is not set; Google credentials cannot be located.")
    return creds_dir / f"{account}.json"


def _read_creds(account: str) -> Dict[str, Any]:
    path = _creds_path(account)
    if not path.is_file():
        raise FileNotFoundError(f"No Google credentials for {account} at {path}.")
    parsed: Dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return parsed


def _creds_lock(path: Path) -> threading.Lock:
    """Return the process-wide lock guarding one credentials file (finding 14)."""
    key = str(path.resolve() if path.parent.exists() else path)
    with _LOCK_REGISTRY_LOCK:
        lock = _LOCK_REGISTRY.get(key)
        if lock is None:
            lock = threading.Lock()
            _LOCK_REGISTRY[key] = lock
        return lock


def _write_creds(account: str, creds: Dict[str, Any]) -> None:
    """Replace the credentials file atomically, never widening its permissions.

    Review finding 14: a fixed `<account>.json.tmp` filename made two concurrent
    refreshes clobber each other, and `chmod` *after* `write_text` left the
    refresh token world-readable in between. `mkstemp` creates the file 0600 in
    one step, in the same directory so `os.replace` stays atomic, and the fsync
    means a power cut cannot leave a truncated credential behind.
    """
    path = _creds_path(account)
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)
    handle, tmp_name = tempfile.mkstemp(dir=str(directory), prefix=".creds-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(creds, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def _expired(expiry_iso: str | None) -> bool:
    if not expiry_iso:
        return True
    try:
        expiry = datetime.fromisoformat(expiry_iso.replace("Z", "+00:00"))
    except ValueError:
        return True
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    return (expiry - datetime.now(timezone.utc)).total_seconds() < _TOKEN_SLACK_SECONDS


def _refresh(creds: Dict[str, Any]) -> Dict[str, Any]:
    payload = urllib.parse.urlencode(
        {
            "client_id": creds["client_id"],
            "client_secret": creds["client_secret"],
            "refresh_token": creds["refresh_token"],
            "grant_type": "refresh_token",
        }
    ).encode()
    status, raw = sync_http.request_bytes(
        "POST",
        str(creds.get("token_uri") or TOKEN_URI),
        {"Content-Type": "application/x-www-form-urlencoded"},
        payload,
        _TIMEOUT_S,
    )
    if not (200 <= status < 300):
        raise GoogleApiError(status, _parse(raw), url=TOKEN_URI, method="POST")
    body = _parse(raw)
    creds["token"] = body["access_token"]
    expires_in = int(body.get("expires_in", 3600))
    creds["expiry"] = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).strftime("%Y-%m-%dT%H:%M:%S")
    return creds


def _parse(raw: bytes) -> Dict[str, Any]:
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"_raw": raw.decode("utf-8", "replace")}
    return parsed if isinstance(parsed, dict) else {"_raw": parsed}


def get_access_token(account: str | None = None) -> str:
    """Return a valid access token, refreshing and rewriting the file if needed.

    The whole read-check-refresh-write cycle runs under the per-path lock, and
    the file is re-read *inside* the lock: whichever caller loses the race then
    finds a fresh token already on disk and issues no second refresh at all
    (review finding 14).
    """
    resolved = account or settings.google_account()
    with _creds_lock(_creds_path(resolved)):
        creds = _read_creds(resolved)
        if not _expired(creds.get("expiry")):
            return str(creds["token"])
        creds = _refresh(creds)
        _write_creds(resolved, creds)
        return str(creds["token"])


def force_refresh(account: str | None = None) -> str:
    """Refresh unconditionally, under the same lock. Used on a 401 retry."""
    resolved = account or settings.google_account()
    with _creds_lock(_creds_path(resolved)):
        creds = _refresh(_read_creds(resolved))
        _write_creds(resolved, creds)
        return str(creds["token"])


def api_call(
    method: str,
    url: str,
    body: Dict[str, Any] | None = None,
    query: Dict[str, Any] | None = None,
    account: str | None = None,
) -> Dict[str, Any]:
    """Make one authenticated Google API call, refreshing once on a 401."""
    resolved = account or settings.google_account()
    if query:
        pairs = {key: value for key, value in query.items() if value is not None}
        if pairs:
            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}{urllib.parse.urlencode(pairs)}"

    data = json.dumps(body).encode() if body is not None else None

    def call(token: str) -> tuple[int, bytes]:
        headers = {"Authorization": f"Bearer {token}"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        return sync_http.request_bytes(method, url, headers, data, _TIMEOUT_S)

    status, raw = call(get_access_token(resolved))
    if status == 401:
        logger.info("Google API returned 401; forcing a token refresh and retrying once.")
        status, raw = call(force_refresh(resolved))

    parsed = _parse(raw)
    if not (200 <= status < 300):
        raise GoogleApiError(status, parsed, url=url, method=method)
    return parsed
