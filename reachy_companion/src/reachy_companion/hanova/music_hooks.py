"""The realtime loop's music call sites (D-018, R7).

Review round 1, findings 1 and 3; review round 2, findings 1 and 8.

`huggingface_realtime.py` imports only these names, so the wiring is testable on
its own and the handler never touches player internals.

Three rules the earlier drafts broke:

* **Nothing here awaits I/O on behalf of the event receiver.** A pause request
  carries a five-second daemon timeout; awaiting it inside the receive loop
  stalls every event queued behind it, including the next `speech_started`. The
  speech, audio and turn-end hooks are therefore plain `def`s that *schedule*
  work.
* **A turn is over only when its audio has drained and nothing else is in
  flight.** A tool-calling turn emits `response.done` and is then followed by a
  second response that speaks the result; resuming in between talks over it.
  Round 2, finding 1: "drained" is now decided per response generation by
  `audio_drain`, which is told about audio *before* it is queued.
* **A tool batch that needs no follow-up response still ends the turn.** Round 2,
  finding 1: `on_tool_call_finished()` used to schedule nothing at all, so a
  final batch with `needs_response=False` -- which produces no further
  `response.created` -- left the music paused for the rest of the conversation.
  The flag is now a parameter and the last tool of such a batch closes the turn
  and schedules the resume itself.

Round 2, finding 8: both session boundaries **invalidate and stop** the player
rather than merely forgetting its state, and they run from
`_run_realtime_session()`'s `finally`, so a dropped connection cleans up even
when the handler never shuts down.

Round 3 added two more:

* **finding 1 -- the drain wait is unbounded.** A 12-second cap on
  `_resume_when_drained()` was a 12-second cap on how long a reply may take
  before the music stays down forever. The interval is now purely diagnostic;
  the wait ends only on a real event: drained, session over, or superseded.
* **finding 2 -- the session has a token.** `_DEPS` alone could not tell a
  cleanup from the *previous* connection apart from one belonging to the live
  one, so a late `finally` from a replaced connection tore down its successor.
  `on_session_started()` mints `_SESSION_TOKEN`; `on_session_shutdown()` refuses
  anything else.

The fix round added two more, both of them about state that could get *stuck*:

* **finding 1 -- an interrupted turn does not schedule a resume.** A barge-in
  flushes the playback queue, which satisfies every drain condition at once, so
  the `response.done` the backend sends for the response it just cancelled put
  the track straight back under the user's voice -- and left it there for the
  whole reply, since nothing re-ducks between two responses. `audio_drain` now
  marks the generations a flush truncated, and this module declines to schedule
  for one. The resume comes from the end of the turn that answers the user.
* **finding 2 -- tool calls are tracked by id, not counted.** A tool the
  manager times out is cancelled, and a cancelled tool never reports back, so a
  counter leaked upwards and stranded the music for the rest of the session.
  `on_assistant_turn_ended` reconciles the ids against the handler's own
  in-flight set, which the handler empties on every completed user transcript.
"""

from __future__ import annotations
import asyncio
import logging
from typing import Any, Set, Collection

from reachy_companion.hanova import audio_drain
from reachy_companion.hanova.confirm import GATE
from reachy_companion.hanova.music_player import PLAYER


logger = logging.getLogger(__name__)

# Round 3, finding 1: how often the resume waiter reports that it is still
# waiting. It is a LOGGING interval, not a deadline -- the previous version
# treated the same number as a give-up point and stranded the music.
_DRAIN_REPORT_EVERY_S = 12.0

_TASKS: Set[asyncio.Task[Any]] = set()
_RESUME_TASK: asyncio.Task[Any] | None = None
# Fix round, finding 2: the call ids of the tool calls this turn is waiting on,
# never a bare counter. A tool the manager times out is cancelled inside
# `_run_tool`, whose `except Exception` does not catch `CancelledError`, so it
# never reports back and a counter would stay above zero for the rest of the
# session -- every later turn end returning early with the track still ducked.
# Ids can be reconciled against the handler's own `_in_flight_tool_calls`; a
# number cannot.
_TOOL_CALLS_IN_FLIGHT: Set[str] = set()
_RESPONSE_IN_FLIGHT = False
# The generation `audio_drain` handed us for the response currently being
# spoken. 0 means "no response has been created in this session".
_RESPONSE_GENERATION = 0
# The deps of the current session, captured at session start. `on_tool_call_
# finished` is called from a completion path that must not have to thread deps
# through the background tool manager, and it is the one hook that can need to
# schedule work (round 2, finding 1).
_DEPS: Any = None
# Round 3, finding 2: the identity of the live realtime session. `_SESSION_SEQ`
# only ever grows; `_SESSION_TOKEN` is the live value, or 0 when no session is
# open. A cleanup presenting anything else belongs to a connection that has
# already been replaced, and tearing down on its behalf would dismantle the
# session that replaced it.
_SESSION_SEQ = 0
_SESSION_TOKEN = 0


def _spawn(coro: Any) -> asyncio.Task[Any]:
    """Run *coro* detached, keeping a strong reference so it is not collected."""
    task: asyncio.Task[Any] = asyncio.ensure_future(coro)
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return task


def _cancel_pending_resume() -> None:
    global _RESUME_TASK
    if _RESUME_TASK is not None and not _RESUME_TASK.done():
        _RESUME_TASK.cancel()
    _RESUME_TASK = None


def _clear_phase() -> None:
    global _RESPONSE_IN_FLIGHT, _RESPONSE_GENERATION
    _cancel_pending_resume()
    _TOOL_CALLS_IN_FLIGHT.clear()
    _RESPONSE_IN_FLIGHT = False
    _RESPONSE_GENERATION = 0
    audio_drain.reset()


# --- session lifecycle ------------------------------------------------------
async def on_session_started(deps: Any) -> int:
    """Open a realtime session: new epoch, silenced speaker, clean audio state.

    Round 2, finding 8: the player's test-only state-forgetting helper used to
    stand here (naming it in full would trip the grep test that keeps it out of
    production code). It dropped the state snapshot without advancing the
    generation or stopping the daemon, so a transition still in flight from the
    previous connection finished afterwards and wrote its state back -- audio
    surviving a reconnect. `invalidate()` supersedes those transitions and
    `stop()` actually silences the device.

    Round 3, finding 2: returns the **session token**. The caller keeps it and
    hands it back to `on_session_shutdown`, which is how a cleanup arriving late
    from a replaced connection is told apart from the live one's.
    """
    global _DEPS, _SESSION_SEQ, _SESSION_TOKEN
    _SESSION_SEQ += 1
    _SESSION_TOKEN = _SESSION_SEQ
    token = _SESSION_TOKEN
    _DEPS = deps
    _clear_phase()
    PLAYER.invalidate()
    await PLAYER.stop(deps)
    if token != _SESSION_TOKEN:
        # A newer session opened while we were silencing the daemon. It owns the
        # gate and the deps now; finishing our start would clobber both.
        logger.info("realtime session %d was superseded while starting", token)
        return token
    GATE.begin_session()
    # Task 13, Step 6b adds `nas.clear_session()` here once that module exists.
    return token


async def on_session_shutdown(deps: Any, token: int) -> None:
    """Stop the speaker and close the confirmation session (findings 1, 3, 8).

    Round 3, finding 2: *token* must be the live session's. The handler can open
    a replacement connection before the previous connection's `finally` runs, and
    that stale cleanup used to clear the **new** session's drain state, gate
    epoch, NAS trip and deps. A stale token is now a no-op, which also keeps the
    hook idempotent: the second of two cleanups for the same session presents a
    token that is no longer live.
    """
    global _DEPS, _SESSION_TOKEN
    if not token or token != _SESSION_TOKEN:
        logger.debug("ignoring a stale realtime-session cleanup (token %s, live %s)", token, _SESSION_TOKEN)
        return
    _SESSION_TOKEN = 0
    _clear_phase()
    PLAYER.invalidate()
    await PLAYER.stop(deps)
    GATE.end_session()
    # Task 13, Step 6b adds `nas.clear_session()` here once that module exists.
    _DEPS = None


# --- turn phase -------------------------------------------------------------
def on_response_created() -> None:
    """Record that a response started; any pending resume is now wrong."""
    global _RESPONSE_IN_FLIGHT, _RESPONSE_GENERATION
    _RESPONSE_IN_FLIGHT = True
    _cancel_pending_resume()
    # Round 2, finding 1: opening the generation here is what makes the turn
    # "pending" before a single byte of its audio exists.
    _RESPONSE_GENERATION = audio_drain.begin_response()


def on_response_audio(sample_count: int, sample_rate: int) -> None:
    """Report audio the receiver is about to enqueue (round 2, finding 1)."""
    if _RESPONSE_GENERATION:
        audio_drain.note_enqueued(_RESPONSE_GENERATION, sample_count, sample_rate)


def on_tool_call_started(call_id: str) -> None:
    """Record a tool call in flight, so a follow-up response is still to come.

    Fix round, finding 2: *call_id* is the same id the handler puts into its own
    `_in_flight_tool_calls`, which is what lets `on_assistant_turn_ended`
    reconcile the two and drop a call that died without reporting back.
    """
    _TOOL_CALLS_IN_FLIGHT.add(call_id)
    _cancel_pending_resume()


def on_tool_call_finished(call_id: str, needs_response: bool) -> None:
    """One tool call finished. Close the turn when nothing else will.

    Round 2, finding 1: when this is the **last** tool of the batch and the batch
    wants no follow-up response, there will never be another
    `response.created` / `response.done` pair, so nothing else would ever
    schedule the resume. This path closes the turn itself.
    """
    _TOOL_CALLS_IN_FLIGHT.discard(call_id)
    if _TOOL_CALLS_IN_FLIGHT or needs_response:
        return
    if _DEPS is None:
        logger.debug("music resume not scheduled: no session deps captured yet")
        return
    logger.debug("final tool batch wants no follow-up response; closing the turn")
    on_assistant_turn_ended(_DEPS)


# --- the audio hooks --------------------------------------------------------
def on_user_speech_started(deps: Any) -> None:
    """Duck the music because the user just started talking. Never blocks."""
    _cancel_pending_resume()
    audio_drain.note_cleared()
    _spawn(PLAYER.pause_for_speech(deps))


def on_assistant_turn_ended(deps: Any, live_tool_calls: Collection[str] | None = None) -> None:
    """Close this turn's audio generation and schedule the drain-then-resume.

    Fix round, finding 2: *live_tool_calls* is the handler's own
    `_in_flight_tool_calls` at the moment the turn ended, and it is authoritative
    -- it is emptied on every completed user transcript, so a tool that the
    manager cancelled without ever reporting back disappears from it. Any id we
    are still holding that it does not list belongs to a call nothing will ever
    finish, and keeping it would defer every later resume for the whole session.
    The tool-completion path calls this without a set, having just maintained the
    ids itself.
    """
    global _RESPONSE_IN_FLIGHT, _RESUME_TASK
    _RESPONSE_IN_FLIGHT = False
    if live_tool_calls is not None:
        _TOOL_CALLS_IN_FLIGHT.intersection_update(live_tool_calls)
    generation = _RESPONSE_GENERATION
    if generation:
        # No more audio is coming for this response. Outstanding audio still in
        # the queue keeps `wait_drained` False until it has actually played.
        audio_drain.close_response(generation)
        if audio_drain.was_interrupted(generation):
            # Fix round, finding 1: this `response.done` belongs to the response
            # the barge-in cancelled, and the same barge-in flushed the queue --
            # so the drain wait would succeed instantly and put the track back
            # under the user, who is still talking. The resume is not dropped,
            # it is deferred: the turn that answers the user ends normally and
            # schedules it then.
            _cancel_pending_resume()
            logger.debug("music resume deferred: this turn was interrupted by the user")
            return
    if _TOOL_CALLS_IN_FLIGHT:
        # A tool is still running; its result will produce another response, or
        # `on_tool_call_finished(needs_response=False)` will come back here.
        return
    _cancel_pending_resume()
    _RESUME_TASK = _spawn(_resume_when_drained(deps, generation, _SESSION_TOKEN))


async def _resume_when_drained(deps: Any, generation: int, session: int) -> None:
    """Wait for this generation's drain signal, re-check the phase, then resume.

    **Round 3, finding 1: this wait is not bounded.** The previous version gave
    `wait_drained` a 12-second timeout and returned on expiry, so one long queued
    response or one slow sink left the track paused for the rest of the
    conversation. There are exactly three ways out now:

    1. the generation drains -- resume;
    2. the realtime session that scheduled this resume has ended (*session* is no
       longer the live token) -- there is nothing left to resume into;
    3. a newer response superseded this turn -- the resume it schedules is the
       right one, and this one must not race it.

    Everything else is a log line. A cancellation (what `on_response_created`
    and `_clear_phase` actually raise here) still returns immediately.
    """
    waited_s = 0.0
    try:
        while True:
            if await audio_drain.wait_drained(generation, _DRAIN_REPORT_EVERY_S):
                break
            waited_s += _DRAIN_REPORT_EVERY_S
            if session != _SESSION_TOKEN:
                logger.info("music resume abandoned after %.0fs: the session ended", waited_s)
                return
            if generation != _RESPONSE_GENERATION:
                logger.info("music resume abandoned after %.0fs: a newer response superseded it", waited_s)
                return
            # Diagnostic only -- the loop continues (round 3, finding 1).
            logger.info(
                "music resume still waiting for the turn's audio to drain: %.0fs elapsed, %.2fs outstanding",
                waited_s,
                audio_drain.outstanding_s(),
            )
    except asyncio.CancelledError:
        return
    if session != _SESSION_TOKEN:
        logger.debug("music resume skipped: the session ended while the audio drained")
        return
    if _RESPONSE_IN_FLIGHT or _TOOL_CALLS_IN_FLIGHT:
        logger.debug("music resume skipped: another response or tool call started")
        return
    await PLAYER.resume_after_speech(deps)


# --- test support -----------------------------------------------------------
def reset_for_tests() -> None:
    """Cancel every scheduled hook and clear the phase counters."""
    global _DEPS, _SESSION_TOKEN
    _clear_phase()
    for task in list(_TASKS):
        task.cancel()
    _TASKS.clear()
    _DEPS = None
    # Round 3, finding 2: back to "no session open". `_SESSION_SEQ` is
    # deliberately NOT reset -- a token must never be reused across tests.
    _SESSION_TOKEN = 0


async def drain_pending_for_tests() -> None:
    """Await every scheduled hook so a test can assert on its effects."""
    pending = [task for task in list(_TASKS) if not task.done()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
