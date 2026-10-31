"""Whether a video Reachy put on the TV is still on it (2026-10-31 plan, W3).

While one is, every conversation mode answers only a turn that names Reachy or
says a control phrase: the room is watching, and the soundtrack and the people
watching are not talking to Reachy (operator, 2026-10-31).

This is not conversation-scoped, unlike the trip playlist in `hanova.nas`: a
realtime reconnect keeps it, because the video goes on playing. It starts on a
successful cast (`play_nas_video`, `nas_skip`, `play_video`) and ends on
`music stop`, on something else replacing the video (`show_on_tv`), at app
shutdown, or when the TV reads back as no longer in playback. Paused counts as
playing: a paused video is still on the screen.

A clip that ends on its own would otherwise keep Reachy quiet, so the gate asks
`confirm_still_playing` before refusing an unnamed turn once the last look is
older than `STALE_AFTER_S`. Named turns never pay for that read.

`mark_started` also records what is on the TV (title, place, and what to
re-cast), which `camera` and `look_around` hand back as `video_on_tv`: asked
「我們在哪裡？」 during a clip, mini reached for the camera 2 of 3 times.

**The bookmark** (2026-10-31, follow-up): 「暫停」 with a clip playing went to
`music stop` 4 of 6 times after two wording revisions, so `stop` now keeps the
place of a video Reachy started (`save_bookmark`) and `music resume` re-casts it
and seeks there. The bookmark outlives `mark_ended` -- that is its point -- and
a new video (`mark_started`) or `BOOKMARK_MAX_AGE_S` ends it.
"""

from __future__ import annotations
import time
import logging
import threading
from typing import Any, Dict

from reachy_companion.hanova import settings, ha_client


logger = logging.getLogger(__name__)

STALE_AFTER_S = 20.0
READ_TIMEOUT_S = 1.5
# A stopped video's place is kept this long; after that 「繼續」 is a new request.
BOOKMARK_MAX_AGE_S = 6 * 3600.0

_LOCK = threading.Lock()
_kind: str | None = None
_looked_at: float = 0.0  # monotonic time of the start or of the last read that saw it playing
_now: Dict[str, Any] = {}  # title / place / recast of the video `_kind` names
_bookmark: Dict[str, Any] | None = None


def mark_started(kind: str, *, title: str | None = None, place: str | None = None, recast: str | None = None) -> None:
    """Record that a video of `kind` ("nas", "youtube", or "tv" for one Reachy did not cast) is on the TV.

    `recast` is what plays it again: a NAS clip's URL, a YouTube video id. A new
    video ends any bookmark: 「繼續」 after it means this one.
    """
    global _kind, _looked_at, _now, _bookmark
    with _LOCK:
        _kind = kind
        _looked_at = time.monotonic()
        _now = {"title": title, "place": place, "recast": recast}
        _bookmark = None
    logger.info("video session: started (%s)", kind)


def mark_ended(reason: str) -> None:
    """Forget the video; a no-op when none is recorded. A saved bookmark stays."""
    global _kind, _now
    with _LOCK:
        was = _kind
        _kind = None
        _now = {}
    if was is not None:
        logger.info("video session: ended (%s)", reason)


def now_playing() -> Dict[str, Any] | None:
    """Return `{kind, title, place, recast}` of the video on the TV, or None."""
    with _LOCK:
        return None if _kind is None else {"kind": _kind, **_now}


def on_tv() -> Dict[str, Any] | None:
    """Return the `video_on_tv` field for a picture result: title and place, or None when no video is on."""
    playing = now_playing()
    if playing is None:
        return None
    return {key: playing[key] for key in ("title", "place") if playing.get(key)}


def save_bookmark(*, kind: str, recast: str, title: str | None, place: str | None, position_s: float) -> None:
    """Keep where a stopped video was, so `music resume` can play it on from there."""
    global _bookmark
    with _LOCK:
        _bookmark = {
            "kind": kind,
            "recast": recast,
            "title": title,
            "place": place,
            "position_s": max(0.0, float(position_s)),
            "saved_at": time.monotonic(),
        }
    logger.info("video session: place kept (%s, %.0f s in)", kind, position_s)


def bookmark(now: float | None = None) -> Dict[str, Any] | None:
    """Return the bookmark when it is younger than `BOOKMARK_MAX_AGE_S`; an older one is dropped."""
    global _bookmark
    with _LOCK:
        if _bookmark is None:
            return None
        if ((time.monotonic() if now is None else now) - _bookmark["saved_at"]) > BOOKMARK_MAX_AGE_S:
            _bookmark = None
            return None
        return dict(_bookmark)


def clear_bookmark() -> None:
    """Forget the bookmark."""
    global _bookmark
    with _LOCK:
        _bookmark = None


def reset_for_tests() -> None:
    """Forget the video, what it was, and any bookmark (**tests only**)."""
    global _kind, _now, _bookmark
    with _LOCK:
        _kind = None
        _now = {}
        _bookmark = None


def active() -> bool:
    """Return whether a video Reachy started is recorded as on the TV."""
    with _LOCK:
        return _kind is not None


def kind() -> str | None:
    """Return the recorded video's kind, or None."""
    with _LOCK:
        return _kind


def is_stale(now: float | None = None) -> bool:
    """Return whether the session is active and its last look is older than `STALE_AFTER_S`."""
    with _LOCK:
        if _kind is None:
            return False
        return ((time.monotonic() if now is None else now) - _looked_at) > STALE_AFTER_S


async def confirm_still_playing(timeout_s: float = READ_TIMEOUT_S) -> bool:
    """Read the TV once and return whether the session is still active.

    `idle`, `off` or any other non-playback state ends the session. An
    unreadable TV (HA down, no entity, a timeout) keeps it: staying quiet
    unless called is the safer error while the room may still be watching, and
    the name still reaches Reachy.
    """
    global _looked_at
    if not active():
        return False
    entity = settings.cast_entity()
    if not entity:
        return True
    reply = await ha_client.ha_get_state(entity, timeout_s=timeout_s)
    if not reply.get("ok"):
        logger.info("video session: the TV could not be read; keeping it")
        return True
    state, _title = ha_client.media_state_and_title(reply)
    if ha_client.is_playback_state(state):
        with _LOCK:
            _looked_at = time.monotonic()
        return True
    mark_ended(f"the TV reads {state}")
    return False
