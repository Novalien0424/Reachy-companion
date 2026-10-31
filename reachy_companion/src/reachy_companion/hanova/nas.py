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
import io
import os
import re
import json
import time
import uuid
import shutil
import asyncio
import hashlib
import logging
import posixpath
import threading
from typing import IO, Any, Dict, List
from pathlib import Path
from dataclasses import dataclass
from urllib.parse import quote

import httpx

from reachy_companion.hanova import redact, settings, media_store
from reachy_companion.hanova.confirm import GATE
from reachy_companion.hanova.ha_client import ha_run_script, tv_not_responding, confirm_cast_started


logger = logging.getLogger(__name__)

_SESSION_LOCK = threading.Lock()
# `generation` (round 2, finding 11) is what a cursor token compares against. It
# is distinct from the confirmation epoch: a trip started by `play_nas_video` inside one
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
# `stage_and_cast` awaits the copy through `asyncio.to_thread`, whose work cannot
# be cancelled -- so with nothing bounding it, a stall here would be a tool call
# that never returns, a single-flight lock held forever, and a `_FETCH_LOCKS`
# entry pinned for the life of the process. The four numbers below are the only
# thing standing between a spun-down NAS and that state, which is what upstream's
# `gtimeout` wrapper was for. With the outer fence added in fix round 2 the tool
# call itself always returns; what a stall still costs is the residual note.
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
# `_FETCH_LOCKS` entry both stay held. Locks are keyed per destination, so every
# other clip is unaffected. If the leaked worker does eventually finish, it stages
# the clip correctly and the next play reuses it -- a late success, never a
# corrupt or partial file.
#
# Final review, F3: a *retry of the same clip* used to make that strictly worse.
# It blocked in an untimed `Lock.acquire` on the held per-destination lock, inside
# `asyncio.to_thread` -- so each retry permanently occupied one of the default
# executor's threads (8 on a CM4) until the stuck SMB call unblocked, and enough
# retries would starve every other thread-offloaded tool in the app. The acquire
# now carries `_fetch_fence_s()` as its own timeout and raises the allow-listed
# timeout `NasError` on expiry, so a retry of a wedged clip costs one fence slot
# and then gives the thread back.


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


# --- index refresh from the NAS copy (2026-10-31) ---------------------------
# The operator's nightly Mac pass (`~/HomeAssistant/bin/nas-video/run-batch.sh`)
# rebuilds the index, makes TV copies of new files, and mirrors the index to
# `<HANOVA_NAS_SUBPATH>/.index.json`. Each app start reads that copy on a daemon
# thread and, when it is newer and sane, swaps it in for the instance copy, so a
# new trip folder reaches Reachy without anyone copying files to the robot. The
# instance copy stays the source of truth whenever the NAS copy is missing,
# unreachable, older, or looks like a broken listing.
_MIRROR_NAME = ".index.json"
_MIRROR_MAX_BYTES = 64 * 1024 * 1024
_MIRROR_READ_BUDGET_S = 60.0
# A copy listing fewer than half the clips the robot already knows is treated
# as a listing that went wrong, not as half the archive being deleted overnight.
_MIRROR_SHRINK_FLOOR = 0.5


def _mirror_problem(candidate: Any, current: Dict[str, Any] | None) -> str | None:
    """Return why *candidate* must not replace *current*, or None when it may."""
    if not isinstance(candidate, dict):
        return "it is not a JSON object"
    videos = candidate.get("videos")
    if not isinstance(videos, list) or not videos:
        return "it lists no videos"
    if not isinstance(candidate.get("folders"), dict):
        return "it has no folders"
    have = current.get("videos") if current else None
    if isinstance(have, list) and len(videos) < len(have) * _MIRROR_SHRINK_FLOOR:
        return f"it lists {len(videos)} videos against {len(have)} on the robot"
    return None


def _generated_at(index: Dict[str, Any] | None) -> float | None:
    value = index.get("generated_at") if index else None
    return float(value) if isinstance(value, (int, float)) else None


def refresh_index_from_nas() -> str:
    """Swap in the NAS copy of the index when it is newer and sane. Synchronous.

    Returns the outcome, also logged once: `updated`, `current`, `refused`,
    `unavailable` or `not_configured`. Never raises; on every outcome but
    `updated` the instance copy is left exactly as it was. The previous copy is
    kept beside it as `<name>.prev`, and the new one is renamed into place, so a
    reader never sees a half-written index.
    """
    path = settings.nas_index_path()
    host, user, password = settings.nas_host(), settings.nas_user(), settings.nas_password()
    share, subpath = settings.nas_share(), settings.nas_subpath().strip("/")
    if path is None or not (host and user and password and share and subpath):
        logger.info("NAS index refresh: not configured; using the robot's copy")
        return "not_configured"
    current = load_index()
    remote = "\\\\" + host + "\\" + share + "\\" + subpath.replace("/", "\\") + "\\" + _MIRROR_NAME
    try:
        import smbclient

        smbclient.register_session(
            host, username=user, password=password, connection_timeout=_SMB_CONNECT_TIMEOUT_S
        )
        if smbclient.stat(remote).st_size > _MIRROR_MAX_BYTES:
            logger.warning("NAS index refresh: the NAS copy is over %d MB; kept the robot's copy",
                           _MIRROR_MAX_BYTES // (1024 * 1024))
            return "refused"
        buffer = io.BytesIO()
        with smbclient.open_file(remote, mode="rb") as source:
            _copy_within_budget(source, buffer, _MIRROR_READ_BUDGET_S)
        raw = buffer.getvalue()
    except Exception as exc:  # noqa: BLE001 - smbprotocol raises a wide family
        # The SMB error text carries the share path (round 2, finding 6).
        logger.info("NAS index refresh: the NAS copy is unavailable (%s); using the robot's copy",
                    redact.error(exc))
        return "unavailable"
    try:
        candidate = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        # Not the exception text: it quotes the offending line, a video filename.
        logger.warning("NAS index refresh: the NAS copy is not valid JSON; kept the robot's copy")
        return "refused"
    new_at, have_at = _generated_at(candidate if isinstance(candidate, dict) else None), _generated_at(current)
    if new_at is not None and have_at is not None and new_at <= have_at:
        logger.info("NAS index refresh: the robot's copy is current")
        return "current"
    problem = _mirror_problem(candidate, current)
    if problem is not None:
        logger.warning("NAS index refresh: kept the robot's copy; the NAS copy was refused (%s)", problem)
        return "refused"
    partial = path.with_name(f"{path.name}.{uuid.uuid4().hex[:8]}{PART_SUFFIX}")
    try:
        with open(partial, "wb") as target:
            target.write(raw)
            target.flush()
            os.fsync(target.fileno())
        os.chmod(partial, 0o600)
        if path.is_file():
            shutil.copy2(path, path.with_name(path.name + ".prev"))
        os.replace(partial, path)
    except OSError as exc:
        partial.unlink(missing_ok=True)
        logger.warning("NAS index refresh: could not write the new copy (%s); kept the robot's copy",
                       redact.error(exc))
        return "unavailable"
    before = len(current.get("videos", [])) if current else 0
    logger.info("NAS index refresh: updated from the NAS copy (%d -> %d videos)", before, len(candidate["videos"]))
    return "updated"


def start_index_refresh() -> threading.Thread | None:
    """Run `refresh_index_from_nas` on a daemon thread; startup never waits on it."""
    if not settings.nas_index_refresh_enabled():
        return None
    thread = threading.Thread(target=refresh_index_from_nas, name="nas-index-refresh", daemon=True)
    thread.start()
    return thread


def _hay(value: Any) -> str:
    if value is None:
        return ""
    return str(value).lower()


# Separators the catalogue spells inconsistently: "2024 Chiang Mai", "2025SriLanka",
# "2015_Boracay", "Tokyo & Okinawa". CJK text has none of them and passes through.
_SEPARATORS = re.compile(r"[\s_\-+&.,;:/()'\u2019\u3001\uff0c]+")
_PLACE_FIELDS = ("place", "label", "top_folder", "country")
_KEYWORD_FIELDS = ("place", "label", "top_folder", "name", "country", "category", "year")
_LEADING_NUMBER = re.compile(r"^[\d\s_\-.]+")


def _squash(value: Any) -> str:
    return _SEPARATORS.sub("", _hay(value))


# A short Latin word ("UK", "NY", "USA") is matched as a whole token, because as
# a substring it is everywhere: on 2026-10-31 "UK" matched inside three other
# trips' place and clip names, so `nas list` answered four trips and `nas play` asked
# instead of playing. Tokens also split at letter/digit seams, so "2025UK" still
# holds "uk". Longer words and CJK keep the squashed-substring rule above.
_SHORT_WORD_MAX = 3
_TOKEN = re.compile(r"[a-z]+|\d+")
# 2026-10-31 (1.35.0 regression): a camelCase label is also split at its case
# seams before lowering, so "OpeningNY" holds "opening" and "ny", and "NYTrip"
# holds "ny" and "trip"; an all-caps run ("USA") stays one token. These are
# added to the lowered tokens above, never instead of them.
_CASE_TOKEN = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")

# Chinese place names (plan W5.6): the operator's index builder may carry
# `place_zh`, a space-separated list of Chinese names for a trip's places, in
# traditional and simplified forms where they differ, on each video and in the
# `folders` map. Mini sometimes searches in Chinese first, and in phrases, so a
# term counts when it occurs anywhere in the query text (「英國旅行」 holds 「英國」).
_PLACE_ZH_MIN = 2
_HAN_RUN = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]+")
_ALNUM = re.compile(r"[0-9a-z]")


def _tokens(video: Dict[str, Any], fields: tuple[str, ...]) -> set[str]:
    """Return the record's whole Latin and digit tokens, split at case seams too."""
    tokens: set[str] = set()
    for field in fields:
        value = video.get(field)
        if value is None:
            continue
        tokens.update(_TOKEN.findall(_hay(value)))
        tokens.update(token.lower() for token in _CASE_TOKEN.findall(str(value)))
    return tokens


def _place_zh_terms(value: Any) -> List[str]:
    """Return the `place_zh` names long enough to count (shorter ones match too much)."""
    if not isinstance(value, str):
        return []
    return [term for term in value.split() if len(term) >= _PLACE_ZH_MIN]


def _words_match(
    needle: str, video: Dict[str, Any], fields: tuple[str, ...], place_zh: Any = None
) -> bool:
    """Whether every word of `needle` occurs in the record, in any order (ours, 2026-10-29).

    Case, spacing and punctuation are ignored on both sides, so a single word
    matches wherever the old plain substring did, and "tokyo okinawa" or
    "chiang mai" no longer depend on how the folder happened to be typed. A
    short Latin word must be a whole token (2026-10-31, see `_SHORT_WORD_MAX`).

    When one of the record's `place_zh` names occurs in the needle, that name
    answers the needle's Chinese text, and only its Latin and digit words
    still have to match: 「2025 英國」 asks for the year as well.
    """
    hay = _hay(needle)
    named_in_chinese = any(_squash(term) in _squash(hay) for term in _place_zh_terms(place_zh))
    if named_in_chinese:
        hay = _HAN_RUN.sub(" ", hay)
    record = "".join(_squash(video.get(field)) for field in fields)
    tokens: set[str] | None = None
    for word in (_squash(word) for word in _SEPARATORS.split(hay)):
        if not word or (named_in_chinese and not _ALNUM.search(word)):
            continue
        if len(word) <= _SHORT_WORD_MAX and word.isascii() and word.isalpha():
            if tokens is None:
                tokens = _tokens(video, fields)
            if word not in tokens:
                return False
        elif word not in record:
            return False
    return True


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
    folders = index.get("folders")
    folders = folders if isinstance(folders, dict) else {}
    for video in index.get("videos", []):
        if year is not None and video.get("year") != year:
            continue
        if year_from is not None and (video.get("year") or 0) < year_from:
            continue
        if year_to is not None and (video.get("year") or 9999) > year_to:
            continue
        # The builder writes `place_zh` on the video and in its folder; either serves.
        folder = folders.get(str(video.get("top_folder") or ""))
        place_zh = video.get("place_zh") or (folder.get("place_zh") if isinstance(folder, dict) else None)
        if place is not None and not _words_match(place, video, _PLACE_FIELDS, place_zh):
            continue
        if keyword is not None and not _words_match(keyword, video, _KEYWORD_FIELDS, place_zh):
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


_HAN = re.compile(r"[㐀-鿿豈-﫿]")


def search_miss(index: Dict[str, Any], words: List[Any]) -> Dict[str, Any]:
    """Answer a search that matched nothing (2026-10-30, live).

    The catalogue is in English. A miss on Chinese search words is a
    translation miss, and the one thing that fixes it is searching again in
    English -- live, a bare zero plus the whole catalogue was answered with
    「查不到」. Any other miss hands over every trip, in case the one meant is
    named differently.
    """
    untranslated = [str(word) for word in words if word and _HAN.search(str(word))]
    if untranslated:
        quoted = "、".join(f"「{word}」" for word in untranslated)
        return {
            "ok": False,
            "error": f"nothing is catalogued under {quoted}: the archive is in English; call again yourself with the English words, without asking the user",
        }
    return {"ok": False, "error": "no_match", "all_trips": summarize_folders(index)}


def trip_summary(videos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One row per trip among `videos`, so a capped clip list cannot hide a whole trip."""
    trips: Dict[str, Dict[str, Any]] = {}
    for video in videos:
        name = str(video.get("top_folder") or "")
        row = trips.setdefault(
            name,
            {
                "top_folder": name,
                "year": video.get("year"),
                "place": video.get("place"),
                "country": video.get("country"),
                "clips": 0,
            },
        )
        row["clips"] += 1
    return sorted(trips.values(), key=lambda row: (row["year"] or 0, str(row["top_folder"])))


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


def clips_by_file(index: Dict[str, Any], text: str) -> List[Dict[str, Any]]:
    """Return the clips `text` points at by NAS path or file name (ours, 2026-10-29).

    For the YouTube search, which must not be handed one of the archive's
    files -- live, `tv play_video` was given a clip's path that `nas query` had
    just returned. Only file references count: a path, or a file name with its
    extension, alone or inside a longer phrase, with or without its leading clip
    number (live: "11 x.mpg" came back as "第11 clips x.mpg"). Words -- a place,
    a subject, even a clip's spoken title -- point at no file: which source they
    are meant for is the user's call, and an explicit YouTube request with them
    goes to YouTube (D-038).
    """
    needle = _squash(text)
    if not needle:
        return []
    exact, within = [], []
    for video in index.get("videos", []):
        path = str(video.get("path") or "")
        filename = path.rsplit("/", 1)[-1]
        stem, dot, extension = filename.rpartition(".")
        if not (dot and stem):
            continue
        file_names = {_squash(filename)}
        unnumbered = _LEADING_NUMBER.sub("", stem)
        if _squash(unnumbered):
            file_names.add(_squash(f"{unnumbered}.{extension}"))
        if needle in {_squash(path), _squash(filename)}:
            exact.append(video)
        elif any(name in needle for name in file_names):
            within.append(video)
    # A full path contains its bare file name, which other trips may reuse.
    return exact or within


def _validate_inside(raw_path: str, subpath: str, key_name: str, *, require_playable: bool = True) -> str:
    """Normalise a path and prove it resolves inside *subpath*. Raises NasError.

    `require_playable` gates the extension allowlist: it belongs only to the
    cast copy, which is the file actually fetched and served. An *original*
    path is bounded but never played, and DVD-era originals are legitimately
    .mpg/.mts with a transcoded .mp4 `cast_path` beside them.
    """
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
    if require_playable and posixpath.splitext(normalised)[1].lower() not in ALLOWED_EXTENSIONS:
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
    `validate_cast_path`, applied to the field the index calls `path` --
    containment only: the original is never fetched or served, so its
    extension is not gated (DVD-era trips are .mpg/.mts originals with a
    transcoded .mp4 `cast_path`, and gating them made every such trip
    unplayable while its cast copy sat ready).
    """
    return _validate_inside(path, settings.nas_subpath(), "HANOVA_NAS_SUBPATH", require_playable=False)


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


def _touch_staged(path: Path) -> None:
    """Refresh a cached clip's mtime so the LRU orders by use, not by download date.

    Final review, F2: `media_store.prune` sorts by mtime and `stage_and_cast`
    prunes on **every** play, cache hit included. A clip served straight from the
    cache is therefore the oldest entry in the directory, and with more than
    `keep` clips staged the prune that runs right after the cast deletes the very
    file whose URL was just handed to the TV -- the cast reports ok and the
    Chromecast fetches a 404. Same bug, same fix as the music cache
    (`ytdlp.download_audio`).
    """
    try:
        os.utime(path, None)
    except OSError as exc:
        # A read-only cache must still be castable; the mtime is an
        # optimisation, not a precondition.
        logger.debug("Could not refresh the staged clip's mtime: %s", redact.error(exc))


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
    else already staged this", and that must cost one `stat` and one `utime`, not
    a second copy. Final review, F3: the lock is acquired **with the fence as its
    timeout**, because the holder may be a worker already wedged in an SMB call --
    waiting on it forever would park this thread too.

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
    # Final review, F3: bounded, because the holder may be a worker wedged in an
    # SMB call the fence has already given up on. An untimed acquire here parked
    # the retry's executor thread for as long as that lasted; see the residual
    # note at the top of this module.
    lock = _fetch_lock(destination)
    if not lock.acquire(timeout=_fetch_fence_s()):
        raise NasError(_TIMEOUT_MESSAGE)
    try:
        if destination.is_file() and destination.stat().st_size > 0:
            # Another caller staged it while we waited for the lock. F2: the
            # prune that follows this in `stage_and_cast` orders by mtime.
            _touch_staged(destination)
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
    finally:
        lock.release()


# --- the NAS's own web server (2026-10-31 plan, W6) ------------------------
# The robot-served routes below die with the app: when Reachy sleeps, the app
# stops, and a clip on the TV played ~30 s from its buffer and then went idle.
# With `HANOVA_NAS_HTTP_BASE` set, the TV is handed the NAS's own URL for the
# cast copy instead, so the clip no longer depends on Reachy being awake. A
# quick probe first, so a NAS whose web share is off or unreachable still plays
# through the robot route rather than casting a dead URL.
_NAS_HTTP_PROBE_S = 2.0
_NAS_HTTP_OK = frozenset({200, 206})


def nas_http_url(cast_path: str) -> str | None:
    """Return the NAS web URL for a cast copy, or None when `HANOVA_NAS_HTTP_BASE` is unset. Raises NasError.

    The path is validated exactly as every other route validates it, then
    taken relative to `HANOVA_NAS_CAST_SUBPATH` -- the folder the NAS's web
    share serves at the base -- with each segment percent-encoded, because
    folder names carry spaces, `&`, `+` and Chinese.
    """
    base = settings.nas_http_base()
    if not base:
        return None
    validated = validate_cast_path(cast_path)
    relative = validated[len(posixpath.normpath(settings.nas_cast_subpath())) + 1 :]
    return base + "/" + "/".join(quote(segment, safe="") for segment in relative.split("/"))


async def _probe_nas_http(url: str) -> int:
    """Ask the NAS for the clip without fetching it; return the HTTP status."""
    async with httpx.AsyncClient(timeout=_NAS_HTTP_PROBE_S) as client:
        response = await client.head(url)
        if response.status_code != 405:
            return response.status_code
        # Some servers refuse HEAD; one byte of a GET answers the same question,
        # and the body is never read past it.
        async with client.stream("GET", url, headers={"Range": "bytes=0-0"}) as streamed:
            return streamed.status_code


async def nas_serves(url: str) -> bool:
    """Return whether the NAS's web server answers for *url* (200 or 206) within the probe budget.

    Never raises, and never logs the URL: it names the house's NAS and a trip
    folder. A failure is logged in shape only and the caller falls back.
    """
    try:
        status = await asyncio.wait_for(_probe_nas_http(url), timeout=_NAS_HTTP_PROBE_S)
    except Exception as exc:  # noqa: BLE001 - any failure means "use the robot route"
        logger.warning("NAS web server did not answer (%s); the robot serves the clip instead", redact.error(exc))
        return False
    if status in _NAS_HTTP_OK:
        return True
    logger.warning("NAS web server answered HTTP %d; the robot serves the clip instead", status)
    return False


# --- staging + casting -----------------------------------------------------
async def _cast_prepared_url(url: str | None, title: str, *, confirm_cast: bool = True) -> Dict[str, Any]:
    """Hand one LAN URL to the TV script and shape the tool result.

    Shared by both routes below so the streamed clip and the staged clip cast
    through the same fields and report the same result shape; the only thing
    the route decides is which URL the TV is given.
    """
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
    if confirm_cast:
        # 2026-08-24: dispatch is not display (operator saw "success" on a dark
        # TV). Session-starting casts verify the target; a mid-trip skip keeps
        # its 0.3 s path — the TV is already playing when a skip makes sense.
        check = await confirm_cast_started(entity, timeout_s=settings.cast_confirm_timeout_s())
        if check["confirmed"] is False:
            return {**tv_not_responding(check["state"]), "url": url, "title": title}
    return {"ok": True, "url": url, "title": title, "error": None}


async def stage_and_cast(
    video: Dict[str, Any],
    instance_path: str | Path | None,
    *,
    confirm_cast: bool = True,
) -> Dict[str, Any]:
    """Cast a clip, streaming it off the NAS or staging it first (D-018, R6).

    Latency work, 2026-08-22: with `HANOVA_NAS_STREAM` on -- the default -- the
    TV is handed a Range-capable URL and pulls the clip off the share as it
    plays, instead of waiting out a full copy at the ~7 MB/s the link measures.
    Turning the switch off restores the copy-then-serve path below unchanged.

    2026-10-31 (W6): with `HANOVA_NAS_HTTP_BASE` set and the NAS answering for
    the clip, the TV is handed the NAS's own URL first; both robot routes are
    then only the fallback.
    """
    # Imported here rather than at module scope: `nas_stream` imports this module
    # for path validation, so a module-level import in both directions is a cycle.
    from reachy_companion.hanova import nas_stream

    title = video_title(video)
    cast_path = str(video.get("cast_path") or "")
    if not cast_path:
        return {"ok": False, "url": None, "title": title, "error": "not_ready"}

    streaming = settings.nas_stream_enabled()
    try:
        # Round 2, finding 12: `HANOVA_NAS_SUBPATH` is a prerequisite of this
        # tool, so it must bound something. It bounds the subtree an index
        # entry's original path may name -- the same guarantee `cast_path` has
        # had since finding 15, applied to the field that had none.
        validate_source_path(str(video.get("path") or ""))
        # W6: None unless `HANOVA_NAS_HTTP_BASE` is set.
        nas_url = nas_http_url(cast_path)
        # `register` validates the cast path and derives the served name exactly
        # as `cast_filename` does -- it only also records the mapping the stream
        # endpoint resolves the name back through.
        filename = nas_stream.register(cast_path) if streaming else cast_filename(cast_path)
    except NasError as exc:
        # A bad index entry is a configuration fault, not a user fault. Round 2,
        # finding 6: forwarding the exception's own text verbatim relied on every
        # NasError message *staying* path-free, which is an invariant nothing
        # enforced. `nas_message` allow-lists the fixed sentences instead.
        logger.warning("NAS index entry rejected: %s", redact.error(exc))
        return {"ok": False, "url": None, "title": title, "error": nas_message(exc)}

    if nas_url is not None and await nas_serves(nas_url):
        # W6: the TV fetches the clip from the NAS itself, so it survives Reachy's sleep.
        return await _cast_prepared_url(nas_url, title, confirm_cast=confirm_cast)

    if streaming:
        # Nothing is copied and nothing is pruned: the served URL resolves back
        # to the share on every request. `media_url` is the same join over
        # `HANOVA_MEDIA_HTTP_BASE`, so an unset base is still the same failure.
        return await _cast_prepared_url(
            media_store.media_url(nas_stream.URL_SEGMENT, filename), title, confirm_cast=confirm_cast
        )

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
    else:
        # Final review, F2: a cache hit rewrites nothing, so without this the
        # replayed clip is the OLDEST file in the directory and the prune right
        # below deletes the one whose URL is about to be cast.
        _touch_staged(local)
    media_store.prune("nas", instance_path, settings.nas_cast_keep())

    return await _cast_prepared_url(media_store.media_url("nas", filename), title, confirm_cast=confirm_cast)


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
