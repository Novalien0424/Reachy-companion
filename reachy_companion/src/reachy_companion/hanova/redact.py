"""The one place a ported tool turns private data into something loggable.

Review round 1, finding 7 (scoped by the controller): every log line and every
free-text error surface produced by the 22 new tools is **metadata only** --
status, counts, durations, family, tool name. Never a title, a query, a subject,
a recipient, an id, a path or a URL, and never a raw API error body.

Scope note: this covers the *new* tool surface. The existing framework's
model-visible tool-result and assistant-content logging in
`huggingface_realtime.py` is explicitly out of scope for this plan.

Review round 3, finding 3: `error()` no longer looks at the raw message at all.
See its docstring for why tokenizing an error body can never be made safe.
"""

from __future__ import annotations
import os
import errno
import hashlib
from typing import Any


# A per-process salt so a digest cannot be reversed by rainbow table across
# deployments, while staying stable inside one run so two log lines about the
# same object correlate.
_SALT = os.urandom(16)

SAFE_LOG_FIELDS = frozenset({"status", "count", "duration_ms", "family", "tool", "http_status", "cached", "ok"})

# Errno names that describe transport shape and can never carry content. They
# are matched against `errno.errorcode`, a closed stdlib table -- never against
# anything an API wrote (round 3, finding 3).
#
# The `WSAE*` half is the same closed table on Windows: `errno.ECONNREFUSED` is
# 111 on the robot and 10061 on a Windows dev box, and `errno.errorcode` spells
# the latter `WSAECONNREFUSED`. Listing both spellings keeps this a literal,
# hand-checked allow-list (it is never derived from a value an API returned)
# while making the helper behave the same on both platforms. Every name here was
# read out of `errno.errorcode` itself; a name Windows does not define (EAGAIN,
# EISDIR, ENOENT, ENOSPC, ENOTDIR, EPERM, EPIPE) has no `WSAE` twin to list.
_ERRNO_ALLOWED = frozenset(
    {
        "EACCES",
        "EAFNOSUPPORT",
        "EAGAIN",
        "EBADF",
        "ECONNABORTED",
        "ECONNREFUSED",
        "ECONNRESET",
        "EHOSTUNREACH",
        "EINVAL",
        "EISDIR",
        "EMFILE",
        "ENETDOWN",
        "ENETUNREACH",
        "ENOENT",
        "ENOSPC",
        "ENOTDIR",
        "EPERM",
        "EPIPE",
        "ETIMEDOUT",
        "WSAEACCES",
        "WSAEAFNOSUPPORT",
        "WSAEBADF",
        "WSAECONNABORTED",
        "WSAECONNREFUSED",
        "WSAECONNRESET",
        "WSAEHOSTUNREACH",
        "WSAEINVAL",
        "WSAEMFILE",
        "WSAENETDOWN",
        "WSAENETUNREACH",
        "WSAETIMEDOUT",
    }
)


def count(value: Any) -> str:
    """Render a collection as its size. Never renders an element."""
    try:
        size = len(value)
    except TypeError:
        return "<uncountable>"
    return "<empty>" if size == 0 else f"<{size} items>"


def text(value: Any) -> str:
    """Render free text as its length. Never renders the text."""
    if value is None:
        return "<none>"
    if not isinstance(value, str):
        return "<text:unprintable>"
    return "<empty>" if not value else f"<text:{len(value)} chars>"


def ident(value: Any) -> str:
    """Render an identifier as a stable salted digest. Never renders the id."""
    if value is None:
        return "<none>"
    if not isinstance(value, str):
        value = repr(value)
    digest = hashlib.blake2s(_SALT + value.encode("utf-8"), digest_size=4).hexdigest()
    return f"<id:{digest}>"


def _http_status(exc: BaseException) -> int | None:
    """Read an HTTP status off the exception OBJECT. Never parses a message."""
    response = getattr(exc, "response", None)
    for candidate in (
        getattr(exc, "status_code", None),
        getattr(exc, "status", None),
        getattr(response, "status_code", None),
        getattr(response, "status", None),
        getattr(exc, "code", None),  # urllib.error.HTTPError
    ):
        if isinstance(candidate, bool) or not isinstance(candidate, int):
            continue
        if 100 <= candidate <= 599:
            return candidate
    return None


def _errno_name(exc: BaseException) -> str | None:
    """Map the exception's own `errno` through the stdlib code table."""
    raw = getattr(exc, "errno", None)
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None
    return errno.errorcode.get(raw)


def error(exc_or_text: BaseException | str, *, allow_errno: tuple[str, ...] = ()) -> str:
    r"""Render a failure as its class plus structure taken from the object itself.

    The raw message is dropped: Google, Notion, Drive and SMTP all echo request
    content back inside their error bodies, and that body would otherwise land
    in the log and in the tool result the model reads aloud.

    **Round 3, finding 3.** The previous version tokenized the raw text and kept
    any token matching `^(?:[45]\d{2}|E[A-Z]+)$` plus a caller-supplied word
    list. That is not a whitelist of *structure*, it is a whitelist of *shape* --
    every all-caps token starting with `E` passed, so an echoed identifier like
    `EVENTID_...` walked straight through the redactor, and so did a bare `404`
    that happened to sit inside somebody's note title. Raw text is now **never
    tokenized, never scanned and never returned**. Structure is read off the
    exception's own attributes:

    * an HTTP status from `status_code` / `status` / `response.status_code` /
      `code`, accepted only in the 100-599 range;
    * an errno **name** from `errno`, resolved through `errno.errorcode` -- a
      closed stdlib table -- and emitted only if it is in `_ERRNO_ALLOWED` or in
      the caller's *allow_errno*.

    A bare string has no attributes, so it renders as exactly `"error"`. That is
    the only safe rendering of text nobody has vouched for; a caller that wants
    shape from free text logs `redact.text(...)` beside it instead.
    """
    if not isinstance(exc_or_text, BaseException):
        return "error"
    label = type(exc_or_text).__name__
    parts: list[str] = []
    status = _http_status(exc_or_text)
    if status is not None:
        parts.append(str(status))
    name = _errno_name(exc_or_text)
    if name is not None and (name in _ERRNO_ALLOWED or name in allow_errno):
        parts.append(name)
    return f"{label}({' '.join(parts)})" if parts else label


__all__ = ["SAFE_LOG_FIELDS", "count", "error", "ident", "text"]
