"""Robot-speaker music session, with barge-in ducking (D-018, R2/R7).

Music plays through the daemon's own sound path and *not* through the realtime
audio queue, so it mixes with Reachy's voice at the GStreamer sink instead of
competing for the same buffer.

**Both directions go straight to the daemon REST API.** The media API is exactly
`POST /api/media/play_sound {file}` and `POST /api/media/stop_sound`
(`daemon/app/routers/media.py:77-115`). The client `MediaManager` does not expose
`stop_sound` at all, and its `play_sound` **swallows a non-2xx**, so using it
would let this module report `playing` when nothing played (review round 1,
finding 2). We call both endpoints ourselves and check the status code.

**Pause is synthesised, because the daemon has none.** `POST /api/volume/set`
changes *system* volume and plays a test beep, so it cannot duck one stream. So
`pause_for_speech()` stops the sound and banks the elapsed offset, and
`resume_after_speech()` re-cuts the cached mp3 from that offset with the bundled
ffmpeg and plays the tail.

**Every transition is serialized and generation-checked** (finding 2). Each
public coroutine takes a generation number *before* it queues on the lock, so a
later request always supersedes an earlier one even while the earlier one is
mid-I/O; the number is re-checked after every await -- **including the lock
acquire itself**, which is where a queued transition spends most of its life --
and a superseded transition undoes what it started (it stops the sound it just
began) rather than leaving the speaker running or overwriting newer state.
`stop()` is the one documented exemption: see its docstring.
"""

from __future__ import annotations
import time
import asyncio
import logging
import weakref
import threading
from typing import Any, Dict
from pathlib import Path
from dataclasses import dataclass

import httpx

from reachy_companion.hanova import ytdlp


logger = logging.getLogger(__name__)

_DAEMON_FALLBACK_URL = "http://127.0.0.1:8000"
_DAEMON_TIMEOUT_S = 5.0
# Below this many seconds, restarting the track is indistinguishable from
# resuming it, and skips one ffmpeg round trip.
_MIN_RESUME_OFFSET_S = 0.5


@dataclass
class MusicState:
    """What is on the robot's speaker right now."""

    video_id: str
    title: str
    source_path: Path
    started_at: float
    offset_s: float
    paused: bool
    generation: int


def daemon_base_url(deps: Any) -> str:
    """Return the daemon's HTTP base URL, falling back to localhost:8000."""
    robot = getattr(deps, "reachy_mini", None)
    url = getattr(robot, "_daemon_http_url", "") if robot is not None else ""
    return str(url).rstrip("/") or _DAEMON_FALLBACK_URL


async def _daemon_post(deps: Any, path: str, payload: Dict[str, Any] | None = None) -> bool:
    """POST one daemon media command and return whether it was acknowledged."""
    url = f"{daemon_base_url(deps)}{path}"
    try:
        async with httpx.AsyncClient(timeout=_DAEMON_TIMEOUT_S) as client:
            response = await client.post(url, json=payload)
    except httpx.HTTPError:
        logger.warning("Daemon media command %s failed at the transport layer.", path)
        return False
    acknowledged = 200 <= response.status_code < 300
    if not acknowledged:
        logger.warning("Daemon media command %s returned HTTP %d.", path, response.status_code)
    return acknowledged


async def daemon_stop_sound(deps: Any) -> bool:
    """Ask the daemon to stop the current sound file. Returns the ack."""
    return await _daemon_post(deps, "/api/media/stop_sound")


async def daemon_play_sound(deps: Any, path: str) -> bool:
    """Ask the daemon to play one file. Returns the ack, not just "we asked"."""
    return await _daemon_post(deps, "/api/media/play_sound", {"file": path})


def _superseded() -> Dict[str, Any]:
    """Return the verdict of a transition that a newer request overtook.

    Not a success, and not a fault either -- just a request that lost its race.
    """
    return {"ok": False, "status": "superseded"}


class MusicPlayer:
    """Holds the single music session and mediates every speaker transition."""

    def __init__(self) -> None:
        """Create an idle player."""
        # One transition lock **per event loop**, not one per process. An
        # `asyncio.Lock` binds itself to the first loop that actually contends
        # it and raises `RuntimeError: ... is bound to a different event loop`
        # for every later one. `PLAYER` is process-wide and outlives any single
        # loop -- the settings web server runs its own loop on its own thread --
        # so one shared instance would turn the second loop's first contended
        # transition into a crash. Same fix and same reasoning as
        # `home_net._probe_lock` (Task 2, review round 1 finding 1): weak-keyed
        # so a finished loop's entry disappears with it, guarded by a
        # `threading.Lock` because two OS threads can mint their first lock at
        # the same instant.
        self._loop_locks: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = (
            weakref.WeakKeyDictionary()
        )
        self._loop_locks_guard = threading.Lock()
        self._state_lock = threading.Lock()  # guards the snapshot itself
        self._state: MusicState | None = None
        self._generation = 0

    def _transition_lock(self) -> asyncio.Lock:
        """Return the transition lock belonging to the running event loop."""
        loop = asyncio.get_running_loop()
        with self._loop_locks_guard:
            lock = self._loop_locks.get(loop)
            if lock is None:
                lock = asyncio.Lock()
                self._loop_locks[loop] = lock
            return lock

    # --- state access ------------------------------------------------------
    def current(self) -> MusicState | None:
        """Return the active session, or None when nothing is playing."""
        with self._state_lock:
            return self._state

    def generation(self) -> int:
        """Return the current transition generation. Used by tests and logs."""
        with self._state_lock:
            return self._generation

    def reset(self) -> None:
        """Forget the session without touching the speaker (**tests only**).

        Round 2, finding 8: this neither advances the generation nor stops the
        daemon, so a `play` or `resume` still in flight will happily finish and
        write its state back afterwards. At a session boundary that resurrects
        audio across a reconnect -- use `invalidate()` there.
        """
        with self._state_lock:
            self._state = None

    def invalidate(self) -> int:
        """Supersede every in-flight transition and drop the state (finding 8).

        Bumping the generation *inside* the state lock, in the same critical
        section that clears the snapshot, is what makes this safe: any
        transition that wakes up after this point fails its `_is_current` check,
        undoes its own side effect, and returns `superseded` instead of
        repopulating state that belongs to a session which no longer exists.

        Returns the new generation, so a caller can log or assert on it.
        """
        with self._state_lock:
            self._generation += 1
            self._state = None
            return self._generation

    def _next_generation(self) -> int:
        with self._state_lock:
            self._generation += 1
            return self._generation

    def _is_current(self, generation: int) -> bool:
        with self._state_lock:
            return generation == self._generation

    def _store(self, state: MusicState | None) -> None:
        with self._state_lock:
            self._state = state

    # --- transitions -------------------------------------------------------
    async def play(self, deps: Any, *, video_id: str, title: str, source_path: Path) -> Dict[str, Any]:
        """Start *source_path* on the robot's speaker, superseding anything playing."""
        generation = self._next_generation()
        async with self._transition_lock():
            if not self._is_current(generation):
                return _superseded()

            # Only pre-stop a session we know about. The daemon's own
            # `play_sound` tears the previous playbin down before it starts the
            # new one (`media/audio_gstreamer.py:545-546`), so an unconditional
            # stop here buys nothing and would make a first play cost two round
            # trips to a daemon that has nothing to stop.
            if self.current() is not None:
                await daemon_stop_sound(deps)
                if not self._is_current(generation):
                    return _superseded()

            started = await daemon_play_sound(deps, str(source_path))
            if not self._is_current(generation):
                # A newer request arrived while the daemon was starting this
                # file. Undo our own side effect rather than leave it audible.
                await daemon_stop_sound(deps)
                self._store(None)
                return _superseded()

            if not started:
                self._store(None)
                return {
                    "ok": False,
                    "status": "failed",
                    "error": "the robot's speaker did not accept the file",
                }

            self._store(
                MusicState(
                    video_id=video_id,
                    title=title,
                    source_path=Path(source_path),
                    started_at=time.monotonic(),
                    offset_s=0.0,
                    paused=False,
                    generation=generation,
                )
            )
            logger.info("Music playing on the robot speaker (generation %d)", generation)
            return {"ok": True, "status": "playing", "title": title, "video_id": video_id}

    async def stop(self, deps: Any) -> Dict[str, Any]:
        """Stop the music and clear the session. Always safe to call.

        This is the safety lane: it never checks the generation after its await,
        because a stop must win against anything queued behind it. Whatever runs
        next re-establishes its own state.
        """
        self._next_generation()
        async with self._transition_lock():
            state = self.current()
            acknowledged = await daemon_stop_sound(deps)
            if not acknowledged:
                return {
                    "ok": False,
                    "status": "stop_failed",
                    "error": "the robot's speaker did not acknowledge the stop",
                }
            self._store(None)
            if state is None:
                return {"ok": True, "status": "nothing_playing"}
            logger.info("Music stopped")
            return {"ok": True, "status": "stopped", "title": state.title}

    async def pause_for_speech(self, deps: Any) -> Dict[str, Any]:
        """Duck the music because the user started talking (R7). No-op when idle."""
        generation = self._next_generation()
        async with self._transition_lock():
            if not self._is_current(generation):
                return _superseded()
            state = self.current()
            if state is None or state.paused:
                return {"ok": True, "status": "nothing_to_pause"}

            acknowledged = await daemon_stop_sound(deps)
            if not self._is_current(generation):
                return _superseded()
            if not acknowledged:
                # Finding 2: do NOT mark it paused. The music is still playing,
                # and a resume later would then start a second stream.
                logger.warning("Music pause: the daemon refused the stop; state left playing.")
                return {
                    "ok": False,
                    "status": "pause_failed",
                    "error": "the robot's speaker did not acknowledge the stop",
                }

            with self._state_lock:
                live = self._state
                if live is not None:
                    live.offset_s += max(0.0, time.monotonic() - live.started_at)
                    live.paused = True
            logger.debug("Music paused for user speech (generation %d)", generation)
            return {"ok": True, "status": "paused"}

    async def resume_after_speech(self, deps: Any) -> Dict[str, Any]:
        """Resume ducked music once the turn's audio has fully drained. No-op when idle."""
        generation = self._next_generation()
        async with self._transition_lock():
            if not self._is_current(generation):
                return _superseded()
            state = self.current()
            if state is None or not state.paused:
                return {"ok": True, "status": "nothing_to_resume"}
            source = state.source_path
            offset = state.offset_s

            if offset <= _MIN_RESUME_OFFSET_S:
                playback_path = source
            else:
                playback_path = source.with_suffix(".resume.mp3")
                trimmed = await asyncio.to_thread(ytdlp.cut_from, source, offset, playback_path)
                if not self._is_current(generation):
                    return _superseded()
                if not trimmed:
                    logger.warning("Could not resume music; leaving it stopped.")
                    self._store(None)
                    return {"ok": False, "status": "resume_failed"}

            started = await daemon_play_sound(deps, str(playback_path))
            if not self._is_current(generation):
                await daemon_stop_sound(deps)
                self._store(None)
                return _superseded()
            if not started:
                self._store(None)
                return {"ok": False, "status": "resume_failed"}

            with self._state_lock:
                live = self._state
                if live is not None:
                    live.paused = False
                    live.started_at = time.monotonic()
            logger.debug("Music resumed (generation %d)", generation)
            return {"ok": True, "status": "resumed"}


PLAYER = MusicPlayer()
