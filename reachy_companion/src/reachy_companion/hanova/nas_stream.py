"""Range-capable HTTP endpoint that proxies one NAS clip straight to the TV.

Latency work, 2026-08-22. `nas.stage_and_cast` used to copy the whole clip off
the share into the LAN media cache before the TV was given a URL. The share
measures ~7 MB/s over the robot's Wi-Fi, so a 300 MB home video cost ~44 s of
silence between "play it" and the first frame -- and the cache then held a copy
of a file that already exists on the NAS. This serves the clip instead: the
Chromecast fetches `/hanova-media/nas-stream/<digest>.<ext>` from the app's own
settings server, and each request reads only the byte range the TV asked for,
directly off SMB. Playback starts in about a second and nothing is staged.

The default receiver needs exactly three things, and all three are here:
`Accept-Ranges: bytes`, a `Content-Length` on every response, and `206` with a
`Content-Range` when a `Range` header is present (which is how it seeks).

**Residual risk.** A staged copy was durable: once the file was in the cache, a
NAS that spun down or dropped the link did not interrupt playback. A stream is
not. If the share stops answering mid-clip, the read blocks -- `smbclient` puts
its socket back into blocking mode after connect (`transport.py:69`) and no
`read` timeout is reachable from here -- and what stalls is **that one HTTP
response and its worker thread**, not the conversation, not the tool call, and
not any other request. The TV shows a stalled video; the user asks again.
`HANOVA_NAS_STREAM=0` restores the stage-then-serve path for a share that turns
out to be unreliable.

Nothing here ever logs a path, a share, a host or a served filename: the log
lines carry a status and `redact.error(...)`, same contract as `nas.py`.
"""

from __future__ import annotations
import logging
import posixpath
import threading
from typing import Any, Iterator, NamedTuple
from collections import OrderedDict

from starlette.routing import Route
from starlette.requests import Request
from starlette.responses import Response, PlainTextResponse, StreamingResponse

from reachy_companion.hanova import nas, redact, settings, media_store


logger = logging.getLogger(__name__)

# The path segment under `media_store.MEDIA_URL_PREFIX`. Deliberately **not** one
# of `media_store.KINDS`: a StaticFiles mount at `/hanova-media/nas` matches
# `/hanova-media/nas/...` only, so the dynamic route and the staged-file mount
# cannot shadow each other whichever order they are registered in.
URL_SEGMENT = "nas-stream"

# One SMB read per yielded block. The same size `nas._copy_within_budget` uses:
# big enough that a 300 MB clip is ~300 reads, small enough that a seek costs
# at most one wasted block.
_BLOCK_BYTES = 1024 * 1024

# How many clips stay streamable at once. A trip playlist is tens of clips, so
# this holds every clip anyone could still be watching, and it is a hard bound
# on a table that is fed by index entries rather than by the operator.
_REGISTRY_MAX = 64

# `validate_cast_path` already refused everything else (`nas.ALLOWED_EXTENSIONS`),
# so this table is total over what can reach the handler.
_CONTENT_TYPES = {".mp4": "video/mp4", ".m4v": "video/x-m4v", ".mov": "video/quicktime"}

_UNKNOWN_CLIP = "unknown clip"
_NOT_CONFIGURED = "the NAS is not configured"
_NAS_UNREACHABLE = "the NAS did not answer"

_REGISTRY_LOCK = threading.Lock()
# filename -> validated share-relative path. Insertion-ordered; the oldest
# registration is evicted at the cap. Serving does not renew an entry: a clip
# that is 64 registrations old is not one anybody is still watching, and making
# playback renew it would let one long clip pin the table.
_REGISTRY: OrderedDict[str, str] = OrderedDict()


class _Span(NamedTuple):
    """One resolved byte range plus how the response has to present it.

    `end` is inclusive, as in HTTP. `partial` is False for the whole-file
    answer (200) and True for a range answer (206 or 416); `satisfiable` is
    False only when the requested start is past the end of the file.
    """

    start: int
    end: int
    partial: bool
    satisfiable: bool


def register(cast_path: str) -> str:
    """Make one index clip streamable and return the filename it is served under.

    The path is validated exactly as the staged path validates it, so an index
    entry still cannot name anything outside `HANOVA_NAS_CAST_SUBPATH` and the
    served name is still the digest that leaks no folder names. Raises
    `nas.NasError` on anything else -- the caller reports it the same way it
    reports a rejected staged clip.
    """
    validated = nas.validate_cast_path(cast_path)
    filename = nas.cast_filename(validated)
    with _REGISTRY_LOCK:
        # Re-registering an already-known clip must not age it out early, so the
        # entry is rewritten at the newest position rather than left in place.
        _REGISTRY.pop(filename, None)
        _REGISTRY[filename] = validated
        while len(_REGISTRY) > _REGISTRY_MAX:
            _REGISTRY.popitem(last=False)
    return filename


def lookup(filename: str) -> str | None:
    """Return the validated share path a served filename maps to, or None."""
    with _REGISTRY_LOCK:
        return _REGISTRY.get(filename)


def _resolve_span(header: str | None, size: int) -> _Span:
    """Turn a Range header into the byte span to answer with. Never raises.

    Only the two forms a Chromecast actually sends are honoured, `bytes=N-` and
    `bytes=N-M`. A suffix range, a multi-range, a malformed value and a missing
    header all resolve to the whole file with a 200, which is a legal answer to
    any of them and is the one shape that cannot fail a receiver. A start past
    the end of the file is the single unsatisfiable case (416).
    """
    whole = _Span(0, max(0, size - 1), False, True)
    if not header:
        return whole
    value = header.strip()
    if not value.lower().startswith("bytes=") or "," in value:
        return whole
    first, separator, last = value[len("bytes=") :].strip().partition("-")
    if not separator or not first.strip():
        return whole
    try:
        start = int(first)
        end = int(last) if last.strip() else size - 1
    except ValueError:
        return whole
    if start < 0:
        return whole
    if start >= size:
        return _Span(start, max(0, size - 1), True, False)
    end = min(end, size - 1)
    if end < start:
        return whole
    return _Span(start, end, True, True)


def _remote_path(validated: str, host: str, share: str) -> str:
    """Build the UNC path for a validated share-relative clip path."""
    return "\\\\" + host + "\\" + share + "\\" + validated.replace("/", "\\")


def _iter_span(remote: str, start: int, length: int) -> Iterator[bytes]:
    """Yield *length* bytes from *start*, one SMB block at a time.

    Synchronous on purpose: Starlette iterates a sync body generator in its
    threadpool, so the blocking SMB reads never touch the event loop.

    A failure here happens **after** the response headers are on the wire, so
    there is no status left to change: the stream simply ends short. It is
    swallowed and logged in shape only rather than raised, because an exception
    escaping an ASGI response body is logged by the server as a traceback --
    and a traceback here carries the UNC path in a local variable.
    """
    import smbclient

    remaining = length
    try:
        with smbclient.open_file(remote, mode="rb") as handle:
            if start:
                handle.seek(start)
            while remaining > 0:
                chunk: bytes = handle.read(min(_BLOCK_BYTES, remaining))
                if not chunk:
                    return
                remaining -= len(chunk)
                yield chunk
    except Exception as exc:  # noqa: BLE001 - smbprotocol raises a wide family
        logger.warning("NAS stream ended early with %d bytes unsent: %s", remaining, redact.error(exc))


def _serve(request: Request) -> Response:
    """Answer one GET or HEAD for a registered clip, honouring Range.

    A sync endpoint on purpose: Starlette runs it in its threadpool, so the
    `register_session` and `stat` round trips block a worker thread rather than
    the event loop the realtime conversation runs on.
    """
    filename = str(request.path_params.get("filename", ""))
    validated = lookup(filename)
    if validated is None:
        # An expired registration or a stale URL the TV kept. Nothing to say
        # about it -- the name is a digest and the reason is not the caller's.
        return PlainTextResponse(_UNKNOWN_CLIP, status_code=404)

    host = settings.nas_host()
    user = settings.nas_user()
    password = settings.nas_password()
    share = settings.nas_share()
    if not (host and user and password and share):
        return PlainTextResponse(_NOT_CONFIGURED, status_code=503)

    try:
        import smbclient
    except ImportError as exc:  # pragma: no cover - the wheel is a hard dependency
        logger.warning("NAS stream cannot import the SMB client: %s", redact.error(exc))
        return PlainTextResponse(_NOT_CONFIGURED, status_code=503)

    remote = _remote_path(validated, host, share)
    try:
        # The same connect bound the staged path uses; a stream has no copy
        # budget to share, but a spun-down NAS must still fail rather than hang.
        smbclient.register_session(
            host,
            username=user,
            password=password,
            connection_timeout=nas._SMB_CONNECT_TIMEOUT_S,
        )
        size = int(smbclient.stat(remote).st_size)
    except Exception as exc:  # noqa: BLE001 - smbprotocol raises a wide family
        # The SMB error text carries the full share path (`nas.py`, finding 7).
        logger.warning("NAS stream could not read the clip's size: %s", redact.error(exc))
        return PlainTextResponse(_NAS_UNREACHABLE, status_code=502)

    span = _resolve_span(request.headers.get("range"), size)
    if not span.satisfiable:
        return PlainTextResponse(
            "",
            status_code=416,
            headers={"Accept-Ranges": "bytes", "Content-Range": f"bytes */{size}"},
        )

    length = span.end - span.start + 1 if size else 0
    headers = {"Accept-Ranges": "bytes", "Content-Length": str(length)}
    if span.partial:
        headers["Content-Range"] = f"bytes {span.start}-{span.end}/{size}"
    status = 206 if span.partial else 200
    media_type = _CONTENT_TYPES.get(posixpath.splitext(validated)[1].lower(), "application/octet-stream")
    logger.info("NAS stream: serving %d of %d bytes (status %d)", length, size, status)
    if request.method == "HEAD":
        # Same headers, no body: this is the request the receiver uses to learn
        # the length and the container before it commits to playing anything.
        return Response(status_code=status, headers=headers, media_type=media_type)
    return StreamingResponse(
        _iter_span(remote, span.start, length),
        status_code=status,
        headers=headers,
        media_type=media_type,
    )


def mount_stream_route(app: Any) -> None:
    """Register the streaming endpoint on the app that serves the media cache.

    Called by `media_store.mount_media_routes` **before** it mounts the static
    kind directories: Starlette matches routes in registration order, so a
    dynamic route added first can never be shadowed by a mount at the same
    prefix. (It could not be here in any case -- see `URL_SEGMENT` -- but the
    ordering is what makes that a property of the code rather than of the two
    names happening to differ.)
    """
    app.router.routes.append(
        Route(
            f"{media_store.MEDIA_URL_PREFIX}/{URL_SEGMENT}/{{filename}}",
            _serve,
            methods=["GET", "HEAD"],
            name=f"hanova-media-{URL_SEGMENT}",
        )
    )


__all__ = ["URL_SEGMENT", "lookup", "mount_stream_route", "register"]
