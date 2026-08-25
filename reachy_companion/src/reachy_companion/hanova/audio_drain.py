"""Decide when this turn's audio has finished coming out of the speaker.

Review round 1, finding 1. `response.done` does not answer that question -- the
real handler says so in its own comment -- and neither does
`response.output_audio.done`: at that instant `console.play_loop` still has
queued PCM, and the device buffer behind it holds up to a second more. Resuming
music on either event puts the track back over the top of Reachy's own voice.

**Review round 2, finding 1 rebuilt this module.** The first version began in the
"drained" state and only ever learned about audio that had *already been played*:
`_QUEUE_EMPTY` started `True`, `_DRAINED_AT` started `0.0`, and nothing but
`play_loop` dequeuing ever changed them. So on a real turn the sequence was
`response.output_audio.done` -> `wait_drained()` -> "yes, drained" -> resume, with
the whole reply still sitting in the queue. The fix has two halves:

* **Pending is the default, and it is set before the audio exists.**
  `begin_response()` opens a generation that `wait_drained` refuses until
  `close_response()` is called. There is no window in which a live response looks
  finished.
* **Audio is counted at enqueue time, not at play time.** The event receiver
  calls `note_enqueued()` *before* each delta goes into the playback queue;
  `play_loop` calls `note_chunk()` when the samples reach the sink, which retires
  that much outstanding audio and pushes out the device-buffer estimate.

`wait_drained(generation)` is therefore four conditions, none true by default:
the generation is closed, nothing it enqueued is outstanding, the local queue is
empty, and the device-buffer estimate has expired. The estimate is deliberately
conservative and capped: a wrong estimate should cost a short extra silence,
never a hung resume.
"""

from __future__ import annotations
import time
import asyncio
import logging
import threading
from typing import Set, Dict


logger = logging.getLogger(__name__)

# The device buffer is not observable through the SDK, so the drain time is
# estimated from the samples handed over. This cap stops a bad sample-rate from
# parking a resume for minutes.
_MAX_PENDING_S = 10.0
_POLL_S = 0.02
# 2026-08-24, observed on-robot: the playback loop hands the sink fixed-size
# chunks and keeps the final partial one buffered until later audio pushes it
# out, so a 0.04-0.08 s enqueue-vs-sink residue survives every real drain and
# parked the music resume forever ("still waiting ... 0.08s outstanding"). With
# the local queue empty, a residue below one sink chunk is that measurement
# artifact, not speech still to play; anything larger is real pending audio.
_RESIDUE_SLACK_S = 0.25

_LOCK = threading.Lock()
_GENERATION = 0
# generation -> whether the model has finished emitting audio for it. A
# generation absent from this map is treated as closed, so a `wait_drained` for
# a turn that never opened one cannot hang.
_CLOSED: Dict[int, bool] = {}
# Fix round, finding 1: the generations a queue flush truncated. `note_cleared()`
# closes every generation it knows about, which is how a barge-in stops a resume
# from waiting for audio that will never play -- but "closed because the audio is
# gone" and "closed because the model finished speaking" mean opposite things to
# the turn-end hook, and only this set tells them apart.
_INTERRUPTED: Set[int] = set()
# Seconds of audio enqueued but not yet handed to the sink.
_OUTSTANDING_S = 0.0
_QUEUE_EMPTY = True
_DRAINED_AT = 0.0
# Task 8: a solo barge-in pause is in progress. The playback queue has been
# starved deliberately -- the handler is holding the reply's audio back while it
# decides whether the voice it heard was a real interruption -- so every
# queue-empty mark the idling play loop generates during that window is a lie.
# While this is set the tracker answers as if the reply were still playing,
# which it is about to be again if the barge rolls back.
_PAUSED = False


def reset() -> None:
    """Forget every generation and all pending audio. Session start and tests."""
    global _GENERATION, _OUTSTANDING_S, _QUEUE_EMPTY, _DRAINED_AT, _PAUSED
    with _LOCK:
        _GENERATION += 1
        _CLOSED.clear()
        _INTERRUPTED.clear()
        _OUTSTANDING_S = 0.0
        _QUEUE_EMPTY = True
        _DRAINED_AT = 0.0
        _PAUSED = False


def begin_response() -> int:
    """Open a new response generation, **pending by definition** (finding 1).

    Returns the generation token the turn-end hook must close and wait on.
    """
    global _GENERATION
    with _LOCK:
        _GENERATION += 1
        _CLOSED[_GENERATION] = False
        # Bound the bookkeeping: only the last few generations can matter.
        for stale in sorted(_CLOSED)[:-4]:
            _CLOSED.pop(stale, None)
        _INTERRUPTED.intersection_update(_CLOSED)
        return _GENERATION


def note_enqueued(generation: int, sample_count: int, sample_rate: int) -> None:
    """Record audio that is about to enter the playback queue (finding 1).

    Called by the **event receiver**, before the append. This is the accounting
    that makes "the response is done but nothing has played yet" expressible.
    """
    global _OUTSTANDING_S, _QUEUE_EMPTY
    if sample_count <= 0 or sample_rate <= 0:
        return
    duration_s = sample_count / float(sample_rate)
    with _LOCK:
        # Counted regardless of which generation it belongs to: there is one
        # sink, and a late delta from a superseded turn still occupies it. The
        # *generation* only decides who is allowed to stop waiting.
        _OUTSTANDING_S += duration_s
        _QUEUE_EMPTY = False


def note_chunk(sample_count: int, sample_rate: int) -> None:
    """Record that *sample_count* frames were handed to the audio sink."""
    global _OUTSTANDING_S, _QUEUE_EMPTY, _DRAINED_AT
    if sample_count <= 0 or sample_rate <= 0:
        return
    duration_s = min(sample_count / float(sample_rate), _MAX_PENDING_S)
    now = time.monotonic()
    with _LOCK:
        _OUTSTANDING_S = max(0.0, _OUTSTANDING_S - duration_s)
        _QUEUE_EMPTY = False
        base = max(_DRAINED_AT, now)
        _DRAINED_AT = min(base + duration_s, now + _MAX_PENDING_S)


def note_queue_empty() -> None:
    """Record that the local playback queue has nothing left in it.

    A no-op during a barge-in pause (Task 8): the queue is empty because the
    handler is withholding the reply's audio, not because the speaker has
    finished with it.
    """
    global _QUEUE_EMPTY
    with _LOCK:
        if _PAUSED:
            return
        _QUEUE_EMPTY = True


def note_paused(paused: bool) -> None:
    """Hold the drain accounting still while a solo barge decision is pending.

    Task 8. `_pause_playback()` sets this, and every exit from the pause --
    rollback, confirmed barge, external interrupt, session restart, shutdown --
    clears it, so the flag can never be left stuck on.
    """
    global _PAUSED
    with _LOCK:
        _PAUSED = bool(paused)


def close_response(generation: int) -> None:
    """No further audio will be enqueued for *generation*."""
    with _LOCK:
        if generation in _CLOSED:
            _CLOSED[generation] = True


def note_cleared() -> None:
    """Record a queue flush: nothing outstanding will ever play.

    A barge-in also ends every open turn, so every generation is closed here --
    otherwise a resume scheduled for the interrupted turn would wait out its
    whole timeout for audio that no longer exists.

    Fix round, finding 1: every generation closed this way is also marked
    **interrupted**. Closing them is what makes `wait_drained` stop waiting;
    without the mark, that instant satisfaction is indistinguishable from a turn
    that finished speaking, and the interrupted response's own `response.done`
    then scheduled a resume that fired under the user's voice. Generations that
    had already closed normally are marked too: the flush truncated their tail
    just the same, and their `response.done` is still to come.
    """
    global _OUTSTANDING_S, _QUEUE_EMPTY, _DRAINED_AT
    with _LOCK:
        for generation in list(_CLOSED):
            _CLOSED[generation] = True
            _INTERRUPTED.add(generation)
        _OUTSTANDING_S = 0.0
        _QUEUE_EMPTY = True
        _DRAINED_AT = 0.0


def was_interrupted(generation: int) -> bool:
    """Report whether a queue flush truncated *generation* (fix round, finding 1)."""
    with _LOCK:
        return generation in _INTERRUPTED


def outstanding_s() -> float:
    """Seconds of audio enqueued but not yet handed to the sink. For tests."""
    with _LOCK:
        return _OUTSTANDING_S


def is_audible() -> bool:
    """Whether queued or device-buffered assistant audio may still be heard.

    Party mode's debounced barge-in keys on this rather than on response
    lifecycle: queued PCM outlives `response.done` (Codex round 1, finding 6).

    Task 8: a paused reply is still "audible" — it is queued in the handler
    rather than in the player, and a rollback puts it straight back.
    """
    with _LOCK:
        if _PAUSED:
            return True
        if not _QUEUE_EMPTY:
            return True
        if _OUTSTANDING_S > _RESIDUE_SLACK_S:
            return True
        return time.monotonic() < _DRAINED_AT


def _is_drained(generation: int) -> bool:
    with _LOCK:
        if _PAUSED:
            # Task 8 (Codex round 2, finding 3): music_hooks' `_resume_when_drained`
            # waits here and never consults `is_audible()`, so without this a
            # barge-in pause would look exactly like a finished reply and put
            # the track back on top of the paused one.
            return False
        if not _CLOSED.get(generation, True):
            return False
        if not _QUEUE_EMPTY:
            return False
        if _OUTSTANDING_S > _RESIDUE_SLACK_S:
            return False
        return time.monotonic() >= _DRAINED_AT


async def wait_drained(generation: int, timeout_s: float) -> bool:
    """Wait until *generation* is closed and all of its audio has been played."""
    deadline = time.monotonic() + max(0.0, timeout_s)
    while True:
        if _is_drained(generation):
            return True
        if time.monotonic() >= deadline:
            logger.debug("audio drain wait timed out after %.2fs", timeout_s)
            return False
        await asyncio.sleep(_POLL_S)
