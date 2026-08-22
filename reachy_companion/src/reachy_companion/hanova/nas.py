"""NAS home-video library: index queries, SMB staging, and cast session state.

Adapted from upstream `nasvideo/query.py` (pure filtering, ported nearly as-is)
and `nasvideo/smb.py` (rewritten). Three upstream dependencies are gone:

* `/opt/homebrew/bin/smbclient` wrapped in `gtimeout` (`nasvideo/smb.py:20-21`)
  becomes `smbprotocol`, which is a pure-Python pip wheel.
* Staging into Home Assistant's `www/` directory becomes the LAN-served media
  cache, because the Chromecast fetches the URL from its own network position.
* The index path and every NAS credential come from configuration; upstream read
  them from files under the operator's home directory.

**Auto-advance is deliberately not ported.** Upstream ran an unbounded 1 Hz
daemon polling Home Assistant forever and prefetching the next clip
(`server.py:1976-2058`). Here the session holds the trip playlist and its
position, and `nas_skip` advances it on request -- the same user-visible
capability with no background task to own or leak.
"""

from __future__ import annotations
import os
import json
import time
import uuid
import asyncio
import hashlib
import logging
import posixpath
import threading
from typing import IO, Any, Dict, List
from pathlib import Path
from dataclasses import dataclass

from reachy_companion.hanova import redact, settings, media_store
from reachy_companion.hanova.confirm import GATE
from reachy_companion.hanova.ha_client import ha_run_script


logger = logging.getLogger(__name__)

_SESSION_LOCK = threading.Lock()
# `generation` (round 2, finding 11) is what a cursor token compares against. It
# is distinct from the confirmation epoch: a `nas_play_folder` inside one
# conversation starts a new *playlist* without starting a new conversation.
_SESSION: Dict[str, Any] = {"playlist": [], "index": -1, "epoch": "", "generation": 0}

# Round 2, finding 10: one lock per staged destination, so two callers racing for
# the same clip serialise instead of writing over each other.
_FETCH_LOCKS_LOCK = threading.Lock()
_FETCH_LOCKS: Dict[str, threading.Lock] = {}

# Finding 15: only these ever get served, and the served name always ends in one
# of them, whatever the index says.
ALLOWED_EXTENSIONS = frozenset({".mp4", ".m4v", ".mov"})

# Round 2, finding 10: re-exported from `media_store`, which owns it because
# `prune` has to skip these and the reverse import would be a cycle. The LRU
# therefore cannot delete a staging file out from under an active copy.
PART_SUFFIX = media_store.PART_SUFFIX

# Review finding 1: nothing above this module bounds an SMB fetch. The background
# tool manager's own cap is a day (`background_tool_manager.py:105`), and
# `stage_and_cast` awaits the copy through `asyncio.to_thread`, which cannot be
# cancelled -- so a stall here is a tool call that never returns, a single-flight
# lock held forever, and a `_FETCH_LOCKS` entry pinned for the life of the
# process. These three numbers are the only thing standing between a spun-down
# NAS and that state, which is what upstream's `gtimeout` wrapper was for.
#
# 20 s to connect: a NAS on the same LAN completes the TCP connect and SMB
# negotiate in well under a second, and 20 s still tolerates a sleeping disk
# spinning up. The library's own default is 60 s, which is 60 s of held lock.
_SMB_CONNECT_TIMEOUT_S = 20.0
# 2 minutes for the whole copy: these are pre-transcoded, cast-ready clips, so
# on a home LAN a copy is seconds and this is pure headroom for a slow WiFi link
# on a CM4. It is deliberately not generous -- a user who asked to watch a home
# video will not wait two minutes either, and nothing else will ever stop this.
_SMB_COPY_BUDGET_S = 120.0
# One deadline check per chunk, so the budget is observed to within a chunk.
_SMB_CHUNK_BYTES = 1024 * 1024
# Fix round 2: added to the copy budget to form the outer fence. See
# `_fetch_fence_s` and the residual note below for what it does and does not buy.
_SMB_FENCE_HEADROOM_S = 15.0

_TIMEOUT_MESSAGE = "the NAS copy took too long and was abandoned"

# --- what is bounded here, and what is not (fix round 2) --------------------
#
# Bounded, and the worker thread genuinely terminates:
#
# * the TCP connect and SMB negotiate -- `_SMB_CONNECT_TIMEOUT_S`, enforced by
#   the library itself;
# * the file copy as a whole, and any stall landing at a chunk boundary --
#   `_SMB_COPY_BUDGET_S`, enforced by `_copy_within_budget`.
#
# Bounded only for the *caller*, with the worker thread left running:
#
# * `smbclient.open_file` -- the tree-connect and create round trips. Both call
#   `Connection.receive(request)` with no timeout (`open.py:1264`, `tree.py:250`)
#   on a socket the library put back into blocking mode after connect
#   (`transport.py:69`), and no argument on `open_file`, `get_smb_tree` or
#   `register_session` reaches them: `connection_timeout` is documented as, and
#   only used for, "the initial connection". `Session.disconnect` does pass a
#   timeout through, so the library can -- it just does not here.
# * a single `read()` that never returns, for the same reason.
#
# For those two, `_fetch_fence_s()` bounds `stage_and_cast`'s await, so the tool
# call returns and the user hears a failure instead of the conversation hanging.
# It does **not** kill the thread: `asyncio.to_thread` work cannot be cancelled.
# Until the underlying call unblocks, that worker thread and that destination's
# `_FETCH_LOCKS` entry both stay held, so every later request *for the same clip*
# waits. Locks are keyed per destination, so every other clip is unaffected. If
# the leaked worker does eventually finish, it stages the clip correctly and the
# next play reuses it -- a late success, never a corrupt or partial file.


def _fetch_fence_s() -> float:
    """Return the wall-clock fence for one staged fetch: copy budget + headroom.

    Derived rather than a separate constant so the fence can never be
    accidentally set below the in-thread copy budget it is meant to outlive --
    which would make the fence fire on healthy slow copies and mask the budget.
    """
    return _SMB_COPY_BUDGET_S + _SMB_FENCE_HEADROOM_S


class NasError(RuntimeError):
    """The NAS could not be reached, or the file could not be copied."""


# Round 2, finding 6: the only NasError texts a caller may ever see. Forwarding
# the exception's own text relied on every message in this module staying
# path-free forever, which nothing enforced; an allow-list does enforce it, and
# a message added later without being listed degrades to the generic sentence
# rather than leaking whatever it happens to interpolate.
_NAS_MESSAGES = frozenset(
    {
        "the NAS host, credentials or share are not all configured",
        "that clip path is not a relative path inside the share",
        "that clip path escapes the configured folder",
        "that clip path is outside the configured folder",
        "that clip is not one of the playable video types",
        "the clip copied off the NAS was empty",
        "the video could not be copied from the NAS",
        _TIMEOUT_MESSAGE,
        "smbprotocol is not installed",
        "HANOVA_NAS_SUBPATH is not set.",
        "HANOVA_NAS_CAST_SUBPATH is not set.",
    }
)


def nas_message(exc: BaseException) -> str:
    """Return an allow-listed NasError sentence, or a generic fallback."""
    text = f"{exc}"
    return text if text in _NAS_MESSAGES else "that home video could not be prepared"


@dataclass(frozen=True)
class CursorToken:
    """A reservation on one advance of the trip playlist (round 2, finding 11).

    `generation` identifies the playlist this was taken against and
    `expected_index` the cursor position that was observed. `commit_next` moves
    the cursor only if both still hold, which is what stops two concurrent skips
    from consuming two clips for one request, and stops a cast that outlived its
    session from advancing somebody else's playlist.
    """

    generation: int
    expected_index: int
    next_index: int


# --- index -----------------------------------------------------------------
def load_index() -> Dict[str, Any] | None:
    """Read the operator-supplied video index, or None when it is absent."""
    path = settings.nas_index_path()
    if path is None or not path.is_file():
        return None
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        # Round 2, finding 6: the path is the instance directory and the
        # JSONDecodeError message quotes the offending line of the index --
        # which is a home-video filename.
        logger.warning("Could not read the NAS index: %s", redact.error(exc))
        return None
    return parsed if isinstance(parsed, dict) else None


def _hay(value: Any) -> str:
    if value is None:
        return ""
    return str(value).lower()


def filter_index(
    index: Dict[str, Any],
    year: int | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    place: str | None = None,
    keyword: str | None = None,
    limit: int | None = None,
) -> List[Dict[str, Any]]:
    """Filter the index in memory. Ground truth only -- never fabricate a record."""
    out: List[Dict[str, Any]] = []
    for video in index.get("videos", []):
        if year is not None and video.get("year") != year:
            continue
        if year_from is not None and (video.get("year") or 0) < year_from:
            continue
        if year_to is not None and (video.get("year") or 9999) > year_to:
            continue
        if place is not None:
            needle = _hay(place)
            haystacks = (video.get("place"), video.get("label"), video.get("top_folder"), video.get("country"))
            if not any(needle in _hay(field) for field in haystacks):
                continue
        if keyword is not None:
            needle = _hay(keyword)
            blob = " ".join(_hay(video.get(field)) for field in ("place", "label", "top_folder", "name", "country"))
            if needle not in blob:
                continue
        out.append(video)
    out.sort(
        key=lambda video: (
            video.get("year") or 0,
            str(video.get("top_folder") or ""),
            video.get("seq") if video.get("seq") is not None else 9999,
        )
    )
    return out[:limit] if limit else out


def summarize_folders(index: Dict[str, Any]) -> List[Dict[str, Any]]:
    """One row per top-level folder, for a "what do we have?" overview."""
    rows = [
        {
            "top_folder": name,
            "year": meta.get("year"),
            "place": meta.get("place"),
            "country": meta.get("country"),
            "count": meta.get("count"),
        }
        for name, meta in (index.get("folders") or {}).items()
    ]
    rows.sort(key=lambda row: (row["year"] or 0, str(row["place"] or "")))
    return rows


def folder_playlist(index: Dict[str, Any], top_folder: str) -> List[Dict[str, Any]]:
    """Return the cast-ready clips of one folder, in recorded order."""
    videos = [
        video for video in index.get("videos", []) if video.get("top_folder") == top_folder and video.get("cast_ready")
    ]
    videos.sort(
        key=lambda video: (
            video.get("seq") if video.get("seq") is not None else 9999,
            str(video.get("name") or ""),
        )
    )
    return videos


def video_title(video: Dict[str, Any]) -> str:
    """Return a short spoken title for one clip."""
    parts = [video.get("year"), video.get("place"), video.get("label")]
    return " ".join(str(part) for part in parts if part).strip()


def _validate_inside(raw_path: str, subpath: str, key_name: str) -> str:
    """Normalise a path and prove it resolves inside *subpath*. Raises NasError."""
    if not subpath:
        raise NasError(f"{key_name} is not set.")
    raw = str(raw_path or "").replace("\\", "/").strip()
    if not raw or raw.startswith("/") or ":" in raw:
        raise NasError("that clip path is not a relative path inside the share")
    normalised = posixpath.normpath(raw)
    root = posixpath.normpath(subpath)
    if normalised in (".", "..") or normalised.startswith("../"):
        raise NasError("that clip path escapes the configured folder")
    if normalised != root and not normalised.startswith(root + "/"):
        raise NasError("that clip path is outside the configured folder")
    if posixpath.splitext(normalised)[1].lower() not in ALLOWED_EXTENSIONS:
        raise NasError("that clip is not one of the playable video types")
    return normalised


def validate_cast_path(cast_path: str) -> str:
    """Normalise an index path and prove it stays inside the cast subtree.

    Review finding 15: an index entry went straight into an SMB path with no
    checking, so a bad -- or hostile -- index could reach anywhere on the share.
    The configured `HANOVA_NAS_CAST_SUBPATH` is now actually used, as the root
    the normalised path must resolve inside. Raises `NasError` on anything else.
    """
    return _validate_inside(cast_path, settings.nas_cast_subpath(), "HANOVA_NAS_CAST_SUBPATH")


def validate_source_path(path: str) -> str:
    """Prove an index entry's *original* path stays inside `HANOVA_NAS_SUBPATH`.

    Round 2, finding 12: that key was a mandatory prerequisite of all three
    casting tools and **nothing read it**, so a fresh deployment could be blocked
    on a value with no behaviour attached to it -- either a dead switch or a
    missing check, and it was the second. It is the same bound as
    `validate_cast_path`, applied to the field the index calls `path`.
    """
    return _validate_inside(path, settings.nas_subpath(), "HANOVA_NAS_SUBPATH")


def cast_filename(cast_path: str) -> str:
    """Derive one collision-free served filename from a validated path.

    Review finding 15: the old flatten-and-truncate mapped two different clips
    onto the same served name whenever their paths agreed for the first 150
    characters -- which, for `<trip>/<date>/<camera>/clipNN.mp4`, they routinely
    do. A digest of the *whole* validated path cannot collide that way, and it
    also stops the served name from leaking folder names onto the LAN.
    """
    validated = validate_cast_path(cast_path)
    extension = posixpath.splitext(validated)[1].lower()
    digest = hashlib.blake2s(validated.encode("utf-8"), digest_size=8).hexdigest()
    return f"{digest}{extension}"


# --- SMB -------------------------------------------------------------------
def _fetch_lock(destination: Path) -> threading.Lock:
    """Return the process-wide lock guarding one staged destination (finding 10).

    Two `play_nas_video` calls for the same clip used to open the same
    deterministic `.part` file with `"wb"` at the same time: the second
    truncated the first, and whichever finished first renamed a half-written
    file into place. An atomic `os.replace` does not help when both writers
    share the staging file. This makes the second caller wait and then find the
    clip already staged.
    """
    key = str(destination)
    with _FETCH_LOCKS_LOCK:
        lock = _FETCH_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _FETCH_LOCKS[key] = lock
        return lock


def _copy_within_budget(source: Any, target: IO[bytes], budget_s: float) -> None:
    """Stream *source* into *target* in chunks, abandoning it past *budget_s*.

    Review finding 1: this replaces `shutil.copyfileobj`, which reads until EOF
    with no deadline of any kind. `smbclient` puts its socket back into blocking
    mode once connected (`transport.py:69`) and neither `open_file` nor
    `Open.read` accepts a timeout, so a NAS that stops answering mid-file used to
    stall the copy thread forever -- taking the destination's single-flight lock
    and the tool call with it.

    The deadline is checked between chunks, so this bounds the copy as a whole
    and any stall that lands at a chunk boundary -- which is what makes the
    ordinary failures (a slow link, a disk spinning up, a NAS that trickles)
    terminate this thread instead of wedging it. A single `read()` that never
    returns is not interruptible from here; that case, and the equally unbounded
    `open_file`, are covered by `_fetch_fence_s()` at the caller and are set out
    in full in the residual note at the top of this module.
    """
    deadline = time.monotonic() + budget_s
    while True:
        if time.monotonic() >= deadline:
            raise NasError(_TIMEOUT_MESSAGE)
        chunk = source.read(_SMB_CHUNK_BYTES)
        if not chunk:
            return
        target.write(chunk)


def fetch_cast_file(cast_path: str, destination: Path) -> None:
    """Copy one pre-transcoded MP4 off the NAS. Synchronous; raises NasError.

    The copy lands in a **uniquely named** private `.part` sibling and is renamed
    into place only when it is complete, so a Chromecast that fetches mid-copy
    gets a 404 rather than a truncated video (review finding 15), and two
    concurrent fetches cannot share a staging file (round 2, finding 10).

    The whole body runs under a per-destination single-flight lock, and re-checks
    the destination after acquiring it: the common concurrent case is "someone
    else already staged this", and that must cost one `stat`, not a second copy.

    Review finding 1: the connect gets `_SMB_CONNECT_TIMEOUT_S` and the copy
    `_SMB_COPY_BUDGET_S`; on expiry the `.part` file is removed and a `NasError`
    propagates, which releases the lock rather than pinning it. The `open_file`
    call between them cannot be bounded from inside this thread at all --
    `stage_and_cast` fences it instead, and the module's residual note says what
    that does and does not buy.
    """
    host = settings.nas_host()
    user = settings.nas_user()
    password = settings.nas_password()
    share = settings.nas_share()
    if not (host and user and password and share):
        raise NasError("the NAS host, credentials or share are not all configured")

    validated = validate_cast_path(cast_path)

    try:
        import smbclient
    except ImportError as exc:  # pragma: no cover - the wheel is a hard dependency
        raise NasError("smbprotocol is not installed") from exc

    remote = "\\\\" + host + "\\" + share + "\\" + validated.replace("/", "\\")
    with _fetch_lock(destination):
        if destination.is_file() and destination.stat().st_size > 0:
            # Another caller staged it while we waited for the lock.
            return
        # Round 2, finding 10: a unique name per attempt. Even without the lock
        # two writers could then not collide, and a crashed attempt leaves a
        # file that pruning skips and the next success does not depend on.
        partial = destination.with_name(f"{destination.name}.{uuid.uuid4().hex[:8]}{PART_SUFFIX}")
        try:
            # Finding 1: the library default is 60 s; ours is the one the held
            # single-flight lock can actually afford.
            smbclient.register_session(
                host,
                username=user,
                password=password,
                connection_timeout=_SMB_CONNECT_TIMEOUT_S,
            )
            with smbclient.open_file(remote, mode="rb") as source:
                with open(partial, "wb") as target:
                    _copy_within_budget(source, target, _SMB_COPY_BUDGET_S)
                    target.flush()
                    os.fsync(target.fileno())
            if partial.stat().st_size == 0:
                raise NasError("the clip copied off the NAS was empty")
            os.replace(partial, destination)
        except NasError:
            partial.unlink(missing_ok=True)
            raise
        except Exception as exc:  # noqa: BLE001 - smbprotocol raises a wide family
            partial.unlink(missing_ok=True)
            # Finding 7: the SMB error text carries the full share path.
            logger.warning("NAS copy failed: %s", redact.error(exc))
            raise NasError("the video could not be copied from the NAS") from exc


# --- staging + casting -----------------------------------------------------
async def stage_and_cast(video: Dict[str, Any], instance_path: str | Path | None) -> Dict[str, Any]:
    """Copy a clip into the LAN media cache (if needed) and cast its URL."""
    title = video_title(video)
    cast_path = str(video.get("cast_path") or "")
    if not cast_path:
        return {"ok": False, "url": None, "title": title, "error": "not_ready"}

    try:
        # Round 2, finding 12: `HANOVA_NAS_SUBPATH` is a prerequisite of this
        # tool, so it must bound something. It bounds the subtree an index
        # entry's original path may name -- the same guarantee `cast_path` has
        # had since finding 15, applied to the field that had none.
        validate_source_path(str(video.get("path") or ""))
        filename = cast_filename(cast_path)
    except NasError as exc:
        # A bad index entry is a configuration fault, not a user fault. Round 2,
        # finding 6: forwarding the exception's own text verbatim relied on every
        # NasError message *staying* path-free, which is an invariant nothing
        # enforced. `nas_message` allow-lists the fixed sentences instead.
        logger.warning("NAS index entry rejected: %s", redact.error(exc))
        return {"ok": False, "url": None, "title": title, "error": nas_message(exc)}

    nas_dir = media_store.media_dir("nas", instance_path)
    local = nas_dir / filename

    if not local.is_file() or local.stat().st_size == 0:
        try:
            # Fix round 2: the outer fence. `fetch_cast_file` bounds its own
            # connect and copy, but `smbclient.open_file` sits between them with
            # no timeout available at any layer, so a NAS that authenticates and
            # then stops answering used to hang this await forever -- and with
            # it the tool call and the whole conversation. This converts that
            # into a spoken failure. It does not kill the worker thread; see the
            # residual note at the top of this module for exactly what leaks.
            await asyncio.wait_for(
                asyncio.to_thread(fetch_cast_file, cast_path, local),
                timeout=_fetch_fence_s(),
            )
        except NasError as exc:
            return {"ok": False, "url": None, "title": title, "error": nas_message(exc)}
        except asyncio.TimeoutError:
            # Metadata only (finding 7): which clip is not ours to log.
            logger.warning(
                "NAS fetch exceeded its %.0fs fence; abandoning the wait. The worker thread and "
                "this clip's staging lock stay held until the NAS call unblocks.",
                _fetch_fence_s(),
            )
            return {"ok": False, "url": None, "title": title, "error": _TIMEOUT_MESSAGE}
    media_store.prune("nas", instance_path, settings.nas_cast_keep())

    url = media_store.media_url("nas", filename)
    if url is None:
        return {
            "ok": False,
            "url": None,
            "title": title,
            "error": "HANOVA_MEDIA_HTTP_BASE is not set; the TV has no URL to fetch.",
        }

    fields: Dict[str, Any] = {"url": url, "title": title}
    entity = settings.cast_entity()
    if entity:
        fields["entity_id"] = entity
    cast = await ha_run_script(settings.ha_script_video_url(), fields)
    if not cast["ok"]:
        logger.info("NAS cast failed: %s", redact.error(cast.get("error") or ""))
        return {"ok": False, "url": url, "title": title, "error": "the TV did not accept the video"}
    return {"ok": True, "url": url, "title": title, "error": None}


# --- session (conversation-scoped, review finding 16; token per round 2 #11) -
def start_session(playlist: List[Dict[str, Any]], index: int) -> None:
    """Remember the trip playlist, which clip is on screen, and whose it is.

    Round 2, finding 11: this also mints a new **session generation**. A cursor
    token taken against the old playlist is then refused by `commit_next`, so a
    cast that was still in flight when a new trip started cannot advance the new
    one.
    """
    with _SESSION_LOCK:
        _SESSION["playlist"] = list(playlist)
        _SESSION["index"] = index
        _SESSION["epoch"] = GATE.epoch()
        _SESSION["generation"] = int(_SESSION["generation"]) + 1


def clear_session() -> None:
    """Forget any trip playlist.

    Called on realtime session start and shutdown, and by every action that
    supersedes the trip on the TV or the speaker: `play_music`, `play_video`,
    `show_on_tv`, and a `play_nas_video` that resolves outside the current
    playlist (finding 16). The generation advances here too, so a token taken
    before the clear can never commit (round 2, finding 11).
    """
    with _SESSION_LOCK:
        _SESSION["playlist"] = []
        _SESSION["index"] = -1
        _SESSION["epoch"] = ""
        _SESSION["generation"] = int(_SESSION["generation"]) + 1


def _live_session() -> tuple[List[Dict[str, Any]], int]:
    """Return the playlist and cursor, or an empty session across an epoch.

    Callers must already hold `_SESSION_LOCK`.
    """
    playlist: List[Dict[str, Any]] = _SESSION["playlist"]
    position: int = _SESSION["index"]
    if not playlist or position < 0:
        return [], -1
    if _SESSION["epoch"] != GATE.epoch():
        # Finding 16: a trip from a previous conversation is not this one's.
        return [], -1
    return playlist, position


def peek_next() -> tuple[Dict[str, Any] | None, CursorToken | None, str | None]:
    """Reserve the next clip **without moving the cursor** (round 2, finding 11).

    Returns `(video, token, error)`. The token records which playlist generation
    and which cursor position this advance was computed against; `commit_next`
    refuses to move unless both still hold. Two concurrent skips therefore
    produce two tokens for the same index, and only the first can commit --
    which is what stops one user request from consuming two clips.
    """
    with _SESSION_LOCK:
        playlist, position = _live_session()
        if not playlist:
            return None, None, "nothing_playing"
        if position + 1 >= len(playlist):
            return None, None, "last_clip"
        token = CursorToken(
            generation=int(_SESSION["generation"]),
            expected_index=position,
            next_index=position + 1,
        )
        return playlist[position + 1], token, None


def commit_next(token: CursorToken) -> bool:
    """Compare-and-swap the cursor forward. Only after the cast succeeded.

    Round 2, finding 11: returns False and changes nothing when the playlist
    generation has moved on (a new trip, a clear, a superseding media action) or
    when the cursor is no longer where the token observed it (another skip won).
    """
    with _SESSION_LOCK:
        playlist, position = _live_session()
        if not playlist:
            return False
        if int(_SESSION["generation"]) != token.generation:
            logger.info("nas cursor: stale generation, refusing to advance")
            return False
        if position != token.expected_index:
            logger.info("nas cursor: another advance won, refusing to advance again")
            return False
        if token.next_index >= len(playlist):
            return False
        _SESSION["index"] = token.next_index
        return True


def remaining() -> int:
    """How many clips are left after the current one."""
    with _SESSION_LOCK:
        playlist, position = _live_session()
        if not playlist:
            return 0
        return max(0, len(playlist) - position - 1)
