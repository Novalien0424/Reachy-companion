"""Google Drive v3 calls, adapted from upstream `gdrive.py` (D-018).

This uses a *different* OAuth grant from `gauth.py` -- Drive's own secret file,
whose scope is full `https://www.googleapis.com/auth/drive`. Copying it to the
robot grants the robot full Drive, which is why the drive family is off by
default and why both write tools are confirmation-gated.

Two upstream defaults are reversed here on purpose: nothing is ever made
anyone-with-link readable, and `files.delete` is not exposed at all.
"""

from __future__ import annotations
import json
import uuid
import logging
import urllib.parse
from typing import Any, Dict, List

from reachy_companion.hanova import settings, sync_http


logger = logging.getLogger(__name__)

DRIVE_API = "https://www.googleapis.com/drive/v3"
UPLOAD_API = "https://www.googleapis.com/upload/drive/v3"
TOKEN_URI = "https://oauth2.googleapis.com/token"
FOLDER_MIME = "application/vnd.google-apps.folder"
_TIMEOUT_S = 60
_UPLOAD_TIMEOUT_S = 600


class DriveError(RuntimeError):
    """A Drive call that could not be made or did not succeed.

    Finding 7: Drive error bodies quote file names and ids back. The status goes
    on the exception; the body does not go into the message.
    """

    def __init__(self, message: str, status: int | None = None, body: Dict[str, Any] | None = None) -> None:
        """Record the status and body without putting either into the message."""
        self.status = status
        self.body = body or {}
        super().__init__(message)


_STATUS_MESSAGES = {
    401: "the Drive credential needs to be re-authorised on the robot",
    403: "Drive refused that request for this account",
    404: "Drive could not find that item",
    429: "Drive is rate-limiting this account right now",
}
_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})


def friendly_message(exc: BaseException) -> str:
    """Return a fixed, identifier-free reason the model may say out loud (finding 7)."""
    status = getattr(exc, "status", None)
    if isinstance(status, int):
        return _STATUS_MESSAGES.get(status, f"Drive returned an error (HTTP {status})")
    return "the Drive request could not be completed"


def is_transient(exc: BaseException) -> bool:
    """Return whether a gated Drive tool may retry on the same confirmation."""
    status = getattr(exc, "status", None)
    if isinstance(status, int):
        return status in _RETRYABLE_STATUSES
    return isinstance(exc, OSError)


def _load_oauth() -> Dict[str, str]:
    path = settings.drive_secrets_path()
    if path is None or not path.is_file():
        raise DriveError("HERMES_DRIVE_SECRETS is not set or the Drive OAuth secret file is missing.")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DriveError(f"Could not read the Drive OAuth secret: {exc}") from exc
    # Every shape problem leaves here as a DriveError. A bare `raw.get(...)` on a
    # secret file whose top level is a list -- or whose `gmail` key is a string --
    # would raise AttributeError instead, and AttributeError is in no tool's
    # `except (DriveError, OSError, ValueError, KeyError)` clause, so it would
    # escape past the tool as an unhandled exception.
    if not isinstance(raw, dict):
        raise DriveError("The Drive OAuth secret is not a JSON object.")
    secret: Any = raw
    if "refreshToken" not in raw:
        nested = raw.get("gmail")
        secret = nested.get("oauth") if isinstance(nested, dict) else None
    if not isinstance(secret, dict) or not secret.get("refreshToken"):
        raise DriveError("The Drive OAuth secret has no refreshToken.")
    return {str(key): str(value) for key, value in secret.items()}


def drive_token() -> str:
    """Mint a fresh Drive access token from the stored refresh token."""
    secret = _load_oauth()
    payload = urllib.parse.urlencode(
        {
            "client_id": secret["clientId"],
            "client_secret": secret["clientSecret"],
            "refresh_token": secret["refreshToken"],
            "grant_type": "refresh_token",
        }
    ).encode()
    status, raw = sync_http.request_bytes(
        "POST", TOKEN_URI, {"Content-Type": "application/x-www-form-urlencoded"}, payload, _TIMEOUT_S
    )
    if not (200 <= status < 300):
        raise DriveError(f"Drive token refresh failed: HTTP {status}", status=status)
    try:
        return str(json.loads(raw)["access_token"])
    except (json.JSONDecodeError, KeyError) as exc:
        raise DriveError(f"Drive token response was unreadable: {exc}") from exc


def _json_call(
    method: str,
    url: str,
    payload: Dict[str, Any] | None = None,
    timeout_s: int = _TIMEOUT_S,
) -> Dict[str, Any]:
    headers = {"Authorization": f"Bearer {drive_token()}"}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json; charset=UTF-8"
    status, raw = sync_http.request_bytes(method, url, headers, data, timeout_s)
    try:
        parsed = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        parsed = {"raw": raw.decode("utf-8", "replace")}
    if not (200 <= status < 300):
        # Finding 7: the body stays on the exception, out of the message.
        raise DriveError(f"Drive {method} -> HTTP {status}", status=status, body=parsed)
    return parsed if isinstance(parsed, dict) else {}


def list_files(parent_id: str, limit: int = 50, include_trashed: bool = False) -> List[Dict[str, Any]]:
    """List one folder level: files and folders, newest first."""
    clauses = [f"'{parent_id}' in parents"]
    if not include_trashed:
        clauses.append("trashed = false")
    query = urllib.parse.quote(" and ".join(clauses))
    order = urllib.parse.quote("folder,modifiedTime desc")
    url = (
        f"{DRIVE_API}/files?q={query}"
        "&fields=files(id,name,mimeType,modifiedTime,size,trashed,webViewLink)"
        f"&pageSize={min(max(1, limit), 1000)}&orderBy={order}"
    )
    response = _json_call("GET", url)
    files = response.get("files", [])
    return list(files) if isinstance(files, list) else []


def get_file(file_id: str) -> Dict[str, Any]:
    """Read one file's metadata, so a confirmation can name it."""
    fields = "id,name,mimeType,webViewLink,trashed,modifiedTime,size"
    return _json_call("GET", f"{DRIVE_API}/files/{urllib.parse.quote(file_id, safe='')}?fields={fields}")


def set_trashed(file_id: str, trashed: bool = True) -> Dict[str, Any]:
    """Move a file or folder to Trash (recoverable ~30 days), or restore it.

    The `trashed=False` direction is an **approved non-goal** (review finding 17):
    no tool exposes it, because restoring is two clicks in the Drive UI and a
    voice-driven restore would add a second fuzzy match over the trash namespace.
    The parameter stays only because it is the same API call either way.
    """
    url = f"{DRIVE_API}/files/{urllib.parse.quote(file_id, safe='')}?fields=id,name,trashed,mimeType"
    return _json_call("PATCH", url, {"trashed": trashed})


def upload_bytes(data: bytes, name: str, mime: str, parent_id: str) -> Dict[str, Any]:
    """Upload in-memory bytes as a new file. The result is private by default."""
    boundary = f"==={uuid.uuid4().hex}=="
    metadata = {"name": name, "parents": [parent_id]}
    body = (
        f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode()
        + json.dumps(metadata).encode()
        + f"\r\n--{boundary}\r\nContent-Type: {mime}\r\n\r\n".encode()
        + data
        + f"\r\n--{boundary}--\r\n".encode()
    )
    headers = {
        "Authorization": f"Bearer {drive_token()}",
        "Content-Type": f"multipart/related; boundary={boundary}",
        "Content-Length": str(len(body)),
    }
    url = f"{UPLOAD_API}/files?uploadType=multipart&fields=id,name,mimeType,webViewLink,size,parents"
    status, raw = sync_http.request_bytes("POST", url, headers, body, _UPLOAD_TIMEOUT_S)
    try:
        parsed = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        parsed = {"raw": raw.decode("utf-8", "replace")}
    if not (200 <= status < 300):
        raise DriveError(f"Drive upload -> HTTP {status}", status=status, body=parsed)
    return parsed if isinstance(parsed, dict) else {}
