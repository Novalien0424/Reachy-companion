"""Solo pause-then-decide barge-in with false-interruption rollback (Task 8).

Solo mode used to hand barge-in to the server: `interrupt_response=true` meant
any speech-start killed the reply mid-word, so a cough, a 「嗯」 or someone else's
sentence across the room silenced Reachy for good. Task 8 gives the client the
decision instead — the reply is *paused*, and either confirmed as a real
interruption (sustained speech, or a substantive transcript) or rolled back and
resumed as if nothing happened.

Everything here is behind `REALTIME_SOLO_CLIENT_BARGE` (default on); with `0`
the pre-Task-8 path must come back byte for byte.
"""

import time
import base64
import asyncio
from types import SimpleNamespace
from typing import Any
from collections import deque
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

# `tests/` has no __init__.py, so pytest's prepend import mode puts the
# directory itself on sys.path — import the sibling harnesses by bare name.
from test_huggingface_realtime import _FakeEvent, _make_fake_realtime_client
from test_openai_realtime_config import _emit_ready_handler

from reachy_companion import huggingface_realtime as hf_mod
from reachy_companion.hanova import audio_drain, music_hooks
from reachy_companion.console import LocalStream
from reachy_companion.streaming import AdditionalOutputs
from reachy_companion.openai_realtime import ROBOT_RATE, OpenAIRealtimeHandler, _turn_detection
from reachy_companion.tools.core_tools import ToolDependencies
from reachy_companion.conversation_mode import ConversationMode
from reachy_companion.huggingface_realtime import (
    HuggingFaceRealtimeHandler,
    _barge_confirm_s,
    _party_confirm_s,
    _vad_silence_duration_ms,
)


def _install_barge_state(handler: OpenAIRealtimeHandler) -> None:
    """Give a `__new__`-built handler the Task 8 fields `__init__` would set."""
    handler._barge_paused = False
    handler._barge_pending = False
    handler._barge_speech_open = False
    handler._barge_confirm_task = None
    handler._barge_rollback_task = None
    handler._barge_watchdog_task = None
    handler._barge_cooldown_until = 0.0
    handler._barge_response_seen = False
    handler._barge_paused_response_id = None
    handler._barge_partial_committed_item = None
    handler._barge_resumed_response_id = None
    handler._barge_late_eligible = False
    # D-032 T2c: which utterance the pause belongs to, and which utterance the
    # repair watchdog has already answered.
    handler._barge_utterance_item_id = None
    handler._barge_watchdog_answered_item = None
    # D-032 T2d: late eligibility stamped per input item.
    handler._barge_late_eligibles = {}
    handler._held_audio = deque()
    # Task 5 (truncate accounting): the item currently coming out of the
    # speaker, and the pair stashed when a pause began.
    handler._audio_item_id = None
    handler._audio_item_enqueued_ms = 0.0
    handler._barge_paused_item_id = None
    handler._barge_paused_heard_ms = 0
    # Task 10: `on_external_interrupt` clears the commentary suppression list.
    handler._commentary_item_ids = deque(maxlen=8)
    # `_pause_playback` captures the live response id; a handler built for the
    # emit path alone has no party state, so fill it in without clobbering a
    # caller that set a real id.
    if not hasattr(handler, "_active_response_id"):
        handler._active_response_id = None


def _solo_handler() -> OpenAIRealtimeHandler:
    """Return a solo-mode handler with only the barge-relevant state, no __init__."""
    h = OpenAIRealtimeHandler.__new__(OpenAIRealtimeHandler)
    h._conversation_mode = ConversationMode.ONE_ON_ONE
    # Task 8: `set_conversation_mode` closes every open toolbox on a flip, and
    # two tests here flip mid-pause.
    h._open_toolboxes = set()
    h._party_last_accept_at = None
    h._party_speech_open = False
    h._party_utterance_seq = 0
    h._party_barge_task = None
    h._active_response_id = "resp_123"
    h._cancelled_response_ids = deque(maxlen=8)
    h._response_done_event = asyncio.Event()
    h._response_done_event.set()
    h.output_queue = asyncio.Queue()
    h._pending_responses = asyncio.Queue()
    # On the OpenAI handler `_clear_queue` is a wrapping property; the mock
    # lands in `_clear_queue_callback` and that is what the asserts read.
    h._clear_queue = MagicMock()
    h.connection = SimpleNamespace(response=SimpleNamespace(cancel=AsyncMock()))
    h.deps = SimpleNamespace(reachy_mini=MagicMock(), movement_manager=MagicMock())
    _install_barge_state(h)
    return h


def _make_audible() -> int:
    """Open a drain generation with two seconds of queued audio, as a live reply has."""
    generation = audio_drain.begin_response()
    audio_drain.note_enqueued(generation, sample_count=48000, sample_rate=24000)
    return generation


@pytest.fixture(autouse=True)
def _clean_barge_env(monkeypatch: pytest.MonkeyPatch):
    """Run every test against the shipped defaults and clean shared audio state."""
    for name in (
        "REALTIME_SOLO_CLIENT_BARGE",
        "REALTIME_BARGE_CONFIRM_MS",
        "REALTIME_PARTY_BARGE_CONFIRM_MS",
        "REALTIME_BARGE_ROLLBACK_TIMEOUT_S",
        "REALTIME_BARGE_COOLDOWN_MS",
        "REALTIME_DEFAULT_MODE",
        "REALTIME_PARTY_DEFAULT",
        "REALTIME_MIN_TURN_CHARS",
        "REALTIME_ONSET_RAMP_MS",
        "REALTIME_VAD_TYPE",
        "REALTIME_VAD_SILENCE_DURATION_MS",
        "REALTIME_SOLO_NAME_GATE",
        "REALTIME_ONE_ON_ONE_ANSWER_GATE",
        "REALTIME_BARGE_MAX_PAUSE_MS",
    ):
        monkeypatch.delenv(name, raising=False)
    audio_drain.reset()
    music_hooks.reset_for_tests()
    yield
    audio_drain.reset()
    music_hooks.reset_for_tests()


# --------------------------------------------------------------------------
# Pausing instead of flushing
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_solo_speech_start_pauses_instead_of_flushing() -> None:
    """A voice while Reachy is audible pauses the reply; nothing is thrown away yet."""
    h = _solo_handler()
    _make_audible()
    h._response_done_event.clear()

    h._solo_speech_started()

    assert h._barge_paused is True
    assert h._barge_pending is True
    assert h._barge_speech_open is True
    assert h._barge_confirm_task is not None
    h._clear_queue_callback.assert_not_called()
    h.connection.response.cancel.assert_not_awaited()
    assert audio_drain.is_audible() is True
    h.on_external_interrupt()


@pytest.mark.asyncio
async def test_sustained_speech_confirms_and_cancels(monkeypatch: pytest.MonkeyPatch) -> None:
    """Speech that outlasts the confirm window is a real interruption.

    Gate-OFF semantics (`REALTIME_SOLO_NAME_GATE=0`): sustained speech is only
    proof of a barge while address is not required. Under the gate the same
    timer is a max pause that rolls back — pinned by
    `test_sustained_unaddressed_speech_resumes_at_max_pause`.
    """
    monkeypatch.setenv("REALTIME_SOLO_NAME_GATE", "0")
    monkeypatch.setenv("REALTIME_BARGE_CONFIRM_MS", "30")
    h = _solo_handler()
    _make_audible()
    h._response_done_event.clear()

    h._solo_speech_started()
    await asyncio.sleep(0.1)

    h.connection.response.cancel.assert_awaited_once()
    h._clear_queue_callback.assert_called_once()
    assert "resp_123" in h._cancelled_response_ids
    assert h._barge_paused is False and h._barge_pending is False
    assert h._barge_cooldown_until > time.monotonic()
    assert h._barge_watchdog_task is not None
    h.on_external_interrupt()


def test_the_confirm_window_outlasts_the_vad_silence_window() -> None:
    """The rollback path is only reachable if the confirm timer can lose the race.

    Review round, finding 1. `_confirm_solo_barge` confirms iff
    `_barge_speech_open` is still True when it fires, and only `speech_stopped`
    clears that flag — which the server cannot send until its whole silence
    window has elapsed. A confirm window at or below the silence window
    therefore confirms EVERY onset, including a 100 ms cough, and the rollback,
    backchannel and timeout branches become dead code. This pins the
    relationship, not the number.

    The race it describes only exists with `REALTIME_SOLO_NAME_GATE=0`, where
    the confirm timer still commits; under the gate the same timer is a max
    pause. The relationship stays pinned because the legacy path stays shipped.
    """
    assert _barge_confirm_s() * 1000 > _vad_silence_duration_ms()


def test_the_patience_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """The 2026-08-30 patience numbers, pinned as numbers.

    The operator's ask was "don't rush me": a Mandarin mid-sentence pause of
    about a second must not commit the turn. 1000 ms is the server's 500 ms
    default nearly doubled and still under the ~1100 ms knee where the robot
    starts feeling sluggish; the confirm window is bumped in step so it keeps
    its ≥400 ms margin over the silence window.
    """
    monkeypatch.delenv("REALTIME_VAD_SILENCE_DURATION_MS", raising=False)
    monkeypatch.delenv("REALTIME_BARGE_CONFIRM_MS", raising=False)

    assert _vad_silence_duration_ms() == 1000
    assert _barge_confirm_s() == pytest.approx(1.6)


def test_the_party_confirm_window_outlasts_the_vad_silence_window() -> None:
    """The room window carries the same hard invariant as the solo one.

    Final review, C2. `_party_barge_confirm` cancels iff `_party_speech_open`
    is still True when it fires, and only `speech_stopped` clears that flag —
    which the server cannot send until its whole silence window has elapsed. A
    party confirm window at or below the silence window therefore confirms
    EVERY onset, and since GROUP became the boot default that is the shipped
    behavior of the shipped mode: any VAD-detected noise cuts a playing reply
    mid-sentence. This pins the relationship, not the number.
    """
    assert _party_confirm_s() * 1000 > _vad_silence_duration_ms()


def test_a_party_confirm_window_inside_the_vad_window_warns(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The startup advisory covers the room window too, gate on or off.

    Final review, C2. The check used to return early whenever the solo name
    gate was on — its default — so the party window, which the boot mode
    actually uses, was never compared to anything.
    """
    monkeypatch.setattr(hf_mod, "_BARGE_CONFIRM_WARNED", False)
    monkeypatch.setenv("REALTIME_PARTY_BARGE_CONFIRM_MS", "400")
    monkeypatch.setenv("REALTIME_VAD_SILENCE_DURATION_MS", "1000")
    with caplog.at_level("WARNING"):
        hf_mod.warn_if_barge_confirm_races_vad()
    assert "REALTIME_PARTY_BARGE_CONFIRM_MS" in caplog.text
    assert "rollback can never run" in caplog.text

    # Warned once, not once per session update.
    caplog.clear()
    with caplog.at_level("WARNING"):
        hf_mod.warn_if_barge_confirm_races_vad()
    assert caplog.text == ""


def test_a_confirm_window_inside_the_vad_window_warns(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A confirm window that can never lose the race is a silent misconfiguration.

    Gate-OFF semantics: the confirm-commit branch the warning is about only
    exists with `REALTIME_SOLO_NAME_GATE=0`.
    """
    monkeypatch.setattr(hf_mod, "_BARGE_CONFIRM_WARNED", False)
    monkeypatch.setenv("REALTIME_SOLO_NAME_GATE", "0")
    monkeypatch.setenv("REALTIME_BARGE_CONFIRM_MS", "250")
    with caplog.at_level("WARNING"):
        hf_mod.warn_if_barge_confirm_races_vad()
    assert "REALTIME_BARGE_CONFIRM_MS" in caplog.text
    assert "rollback can never run" in caplog.text

    # Warned once, not once per session update.
    caplog.clear()
    with caplog.at_level("WARNING"):
        hf_mod.warn_if_barge_confirm_races_vad()
    assert caplog.text == ""


def test_the_shipped_defaults_do_not_warn(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The defaults we ship must be a configuration we would not warn about.

    Pinned to gate OFF so it keeps testing the numbers rather than the gate's
    early return (`test_the_gate_silences_the_confirm_race_warning` covers that).
    """
    monkeypatch.setattr(hf_mod, "_BARGE_CONFIRM_WARNED", False)
    monkeypatch.setenv("REALTIME_SOLO_NAME_GATE", "0")
    with caplog.at_level("WARNING"):
        hf_mod.warn_if_barge_confirm_races_vad()
    assert caplog.text == ""


@pytest.mark.asyncio
async def test_a_cough_rolls_back_with_the_real_event_ordering(monkeypatch: pytest.MonkeyPatch) -> None:
    """The live API's ordering — not a test-only one — must reach the rollback.

    Review round, finding 1: `speech_stopped` trails the cough by the VAD's
    silence window, so the confirm timer has to still be pending when it lands.
    Timings are scaled down but keep the real relationship
    (confirm > silence > blip). Pinned to gate OFF, where that race is a real
    one: under the gate the timer waits `REALTIME_BARGE_MAX_PAUSE_MS` instead.
    """
    monkeypatch.setenv("REALTIME_SOLO_NAME_GATE", "0")
    monkeypatch.setenv("REALTIME_BARGE_CONFIRM_MS", "140")
    monkeypatch.setenv("REALTIME_VAD_SILENCE_DURATION_MS", "80")
    monkeypatch.setenv("REALTIME_BARGE_ROLLBACK_TIMEOUT_S", "0.05")
    h = _solo_handler()
    _make_audible()
    h._response_done_event.clear()

    h._solo_speech_started()  # the cough
    await asyncio.sleep(0.09)  # the blip plus the VAD silence window
    assert h._barge_paused is True, "the confirm timer must not have fired yet"
    h._solo_speech_stopped()  # what the server sends after its silence window
    await asyncio.sleep(0.09)

    assert h._barge_paused is False and h._barge_pending is False
    h.connection.response.cancel.assert_not_awaited()
    h._clear_queue_callback.assert_not_called()
    assert audio_drain.is_audible() is True
    h.on_external_interrupt()


@pytest.mark.asyncio
async def test_a_control_phrase_confirms_even_though_it_is_too_short() -> None:
    """A robot you cannot silence is worse than any false positive.

    Review round, finding 2: 「停」 is one character, so `is_substantive` rejects
    it against REALTIME_MIN_TURN_CHARS=2 and the reply would have rolled back
    and kept talking over the person telling it to stop.
    """
    for phrase in ("停", "閉嘴", "stop"):
        h = _solo_handler()
        _make_audible()
        h._response_done_event.clear()
        h._solo_speech_started()
        h._solo_speech_stopped()

        handled = await h._resolve_solo_barge(phrase)

        assert handled is False, f"{phrase} must be a real interruption"
        h.connection.response.cancel.assert_awaited_once()
        h._clear_queue_callback.assert_called_once()
        assert h._barge_paused is False
        h.on_external_interrupt()
        audio_drain.reset()


@pytest.mark.asyncio
async def test_a_commit_cancels_a_newer_response_the_user_talked_over(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """D-032 T2b: an interruption stops whatever is speaking, not only the paused reply.

    Inverts the pre-D-032 pin. Review round finding 4 read a live response with
    a different id as "the answer to this very turn, do not kill it" — but the
    flush has already dropped its audio, so keeping it generating produces a
    gap and then the REST of that reply. Under the operator's rule that
    response is precisely what the user is talking over: an earlier turn's
    reply, a tool-batch follow-up, a wake greeting. Cancel it, and arm the
    watchdog, because there is now no live answer to rely on.
    """
    h = _solo_handler()
    _make_audible()
    h._response_done_event.clear()
    h._solo_speech_started()
    assert h._barge_paused_response_id == "resp_123"
    h._solo_speech_stopped()
    # The paused reply finished and something else started speaking in its place.
    h._active_response_id = "resp_answer"

    with caplog.at_level("INFO"):
        handled = await h._resolve_solo_barge("幫我開燈")

    assert handled is False
    h.connection.response.cancel.assert_awaited_once()
    assert "resp_answer" in h._cancelled_response_ids
    assert "solo barge: cancelling a newer response (resp_answer) the user talked over" in caplog.text
    h._clear_queue_callback.assert_called_once()
    assert not h._held_audio
    assert h._barge_watchdog_task is not None, "nothing is left speaking; the turn needs its answer"
    h.on_external_interrupt()


@pytest.mark.asyncio
async def test_a_silent_floor_still_arms_the_watchdog(monkeypatch: pytest.MonkeyPatch) -> None:
    """No response live at all is not "the answer already started".

    The paused reply may have ended without the turn's auto-response being
    accepted — the exact silence the watchdog exists to repair — so the `None`
    case must not be mistaken for finding 4's keep-the-answer case. Pinned to
    the pre-name-gate rule so 「幫我開燈」 still reaches the commit path.
    """
    monkeypatch.setenv("REALTIME_SOLO_NAME_GATE", "0")
    h = _solo_handler()
    _make_audible()
    h._response_done_event.clear()
    h._solo_speech_started()
    h._solo_speech_stopped()
    h._active_response_id = None  # the paused reply ended; nothing replaced it

    await h._resolve_solo_barge("幫我開燈")

    h.connection.response.cancel.assert_not_awaited()  # nothing to cancel
    assert h._barge_watchdog_task is not None
    assert h._barge_response_seen is False
    h.on_external_interrupt()


@pytest.mark.asyncio
async def test_a_commit_still_cancels_the_reply_it_paused(monkeypatch: pytest.MonkeyPatch) -> None:
    """The other side of finding 4: the paused reply is still cancelled normally.

    Pinned to the pre-name-gate rule; the name-gated commit path is covered by
    `test_resolve_commits_on_name`.
    """
    monkeypatch.setenv("REALTIME_SOLO_NAME_GATE", "0")
    h = _solo_handler()
    _make_audible()
    h._response_done_event.clear()
    h._solo_speech_started()
    h._solo_speech_stopped()

    await h._resolve_solo_barge("幫我開燈")

    h.connection.response.cancel.assert_awaited_once()
    assert "resp_123" in h._cancelled_response_ids
    h.on_external_interrupt()


def test_off_loop_cancellation_is_marshalled_onto_the_task_loop() -> None:
    """`Task.cancel()` off-loop is delayed and raises under debug mode.

    Review round, finding 3: `on_external_interrupt()` is reached from the
    JSON-RPC thread, so the cancel has to go through the task's own loop.
    """
    marshalled: list[object] = []

    class _FakeLoop:
        def call_soon_threadsafe(self, callback: object) -> None:
            marshalled.append(callback)

    class _FakeTask:
        def __init__(self) -> None:
            self.cancelled = False

        def done(self) -> bool:
            return False

        def get_loop(self) -> _FakeLoop:
            return _FakeLoop()

        def cancel(self) -> None:
            self.cancelled = True

    task = _FakeTask()
    hf_mod._cancel_barge_task(task, None)  # no running loop here: this test is sync

    assert task.cancelled is False, "an off-loop cancel must not be called directly"
    assert marshalled == [task.cancel]


@pytest.mark.asyncio
async def test_on_loop_cancellation_stays_direct() -> None:
    """On the handler's own loop the cancel is immediate, as before."""
    h = _solo_handler()
    _make_audible()
    h._response_done_event.clear()
    h._solo_speech_started()
    confirm = h._barge_confirm_task

    h.on_external_interrupt()
    await asyncio.sleep(0)

    assert confirm.cancelled() or confirm.done()


@pytest.mark.asyncio
async def test_confirm_keeps_speech_open_through_the_real_console_flush(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Our own flush must not wipe the speech state the watchdog reads.

    Fix round, finding 1. In production `_clear_queue` is the real
    `console.clear_audio_queue`, which calls `on_external_interrupt()` — a full
    barge-state reset. At confirm time the user is by definition still
    mid-sentence, so letting that reset clear `_barge_speech_open` made the
    watchdog's "never answer over a talking user" guard inert: any interrupting
    utterance longer than the watchdog delay got a response fired at it
    mid-sentence. Wired through the REAL console here, not a bare mock.

    Gate OFF: the confirm timer only reaches a commit — the flush under test —
    on the legacy path.
    """
    monkeypatch.setenv("REALTIME_SOLO_NAME_GATE", "0")
    monkeypatch.setenv("REALTIME_BARGE_CONFIRM_MS", "30")
    monkeypatch.setattr(hf_mod, "_BARGE_RESPONSE_WATCHDOG_S", 0.01)
    h = _solo_handler()
    audio = SimpleNamespace(clear_player=MagicMock())
    robot = SimpleNamespace(media=SimpleNamespace(audio=audio))
    LocalStream(h, robot)  # installs the real clear_audio_queue as _clear_queue
    _make_audible()
    h._response_done_event.clear()

    h._solo_speech_started()
    await asyncio.sleep(0.1)

    audio.clear_player.assert_called_once()  # the real console flush really ran
    assert h._barge_speech_open is True, "the user is still mid-sentence"

    # ... and the watchdog therefore stands down instead of talking over them.
    h._response_done_event.set()
    h._barge_response_seen = False
    await h._barge_response_watchdog(h._party_utterance_seq)
    assert h._pending_responses.qsize() == 0
    h.on_external_interrupt()


@pytest.mark.asyncio
async def test_the_watchdog_answers_once_the_user_has_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    """The guard is about a *talking* user, not a permanent veto."""
    monkeypatch.setattr(hf_mod, "_BARGE_RESPONSE_WATCHDOG_S", 0.01)
    h = _solo_handler()
    h._barge_speech_open = True
    h._barge_response_seen = False

    await h._barge_response_watchdog(h._party_utterance_seq)
    assert h._pending_responses.qsize() == 0

    h._solo_speech_stopped()
    await h._barge_response_watchdog(h._party_utterance_seq)
    assert h._pending_responses.qsize() == 1


@pytest.mark.asyncio
async def test_a_pause_is_resolved_only_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """A transcript landing inside the cancel round trip must not commit twice.

    Fix round, finding 2: `_barge_pending` used to survive the whole
    `response.cancel` await, and the event loop runs inside that await. Driven
    from the confirm timer, so gate OFF.
    """
    monkeypatch.setenv("REALTIME_SOLO_NAME_GATE", "0")
    monkeypatch.setenv("REALTIME_BARGE_CONFIRM_MS", "10")
    h = _solo_handler()
    _make_audible()
    h._response_done_event.clear()
    gate = asyncio.Event()

    async def slow_cancel() -> None:
        await gate.wait()

    h.connection.response.cancel = AsyncMock(side_effect=slow_cancel)

    h._solo_speech_started()
    await asyncio.sleep(0.05)  # the confirm timer is now parked in response.cancel

    assert h._barge_pending is False, "the pause is claimed on entry, not after the round trip"
    # This is exactly the guard the event loop uses before calling _resolve_solo_barge.
    gate.set()
    await asyncio.sleep(0.02)
    assert h.connection.response.cancel.await_count == 1
    assert h._clear_queue_callback.call_count == 1
    h.on_external_interrupt()


@pytest.mark.asyncio
async def test_a_cancelled_commit_cannot_strand_the_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    """A commit cancelled mid-round-trip must still end the pause it claimed.

    Gate OFF: the commit under test is the confirm timer's, which only commits
    on the legacy path.
    """
    monkeypatch.setenv("REALTIME_SOLO_NAME_GATE", "0")
    monkeypatch.setenv("REALTIME_BARGE_CONFIRM_MS", "10")
    h = _solo_handler()
    _make_audible()
    h._response_done_event.clear()
    gate = asyncio.Event()

    async def slow_cancel() -> None:
        await gate.wait()

    h.connection.response.cancel = AsyncMock(side_effect=slow_cancel)
    h._solo_speech_started()
    h._held_audio.append((ROBOT_RATE, np.zeros((1, 160), dtype=np.int16)))
    await asyncio.sleep(0.05)

    h._barge_confirm_task.cancel()
    await asyncio.sleep(0.02)

    assert h._barge_paused is False and h._barge_pending is False
    assert not h._held_audio
    audio_drain.note_cleared()
    assert audio_drain.is_audible() is False, "audio_drain must not be left paused"


@pytest.mark.asyncio
async def test_short_blip_rolls_back_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """A blip with no transcript at all resumes the reply and re-arms the onset ramp.

    Gate OFF: `REALTIME_BARGE_CONFIRM_MS` is what the rollback timer has to
    beat here, and it only governs the timer on the legacy path.
    """
    monkeypatch.setenv("REALTIME_SOLO_NAME_GATE", "0")
    monkeypatch.setenv("REALTIME_BARGE_CONFIRM_MS", "300")
    monkeypatch.setenv("REALTIME_BARGE_ROLLBACK_TIMEOUT_S", "0.05")
    h = _solo_handler()
    _make_audible()
    h._response_done_event.clear()
    h._notify_response_started = MagicMock()

    h._solo_speech_started()
    confirm_task = h._barge_confirm_task
    h._solo_speech_stopped()
    assert h._barge_rollback_task is not None
    await asyncio.sleep(0.12)

    assert h._barge_paused is False and h._barge_pending is False
    h.connection.response.cancel.assert_not_awaited()
    h._clear_queue_callback.assert_not_called()
    h._notify_response_started.assert_called_once()
    assert confirm_task.cancelled() or confirm_task.done()
    # The pause faked nothing: the reply's audio is still accounted for.
    assert audio_drain.is_audible() is True
    h.on_external_interrupt()


@pytest.mark.asyncio
async def test_backchannel_transcript_rolls_back() -> None:
    """「嗯嗯」 is agreement, not an interruption: the reply comes back."""
    h = _solo_handler()
    _make_audible()
    h._response_done_event.clear()
    h._solo_speech_started()
    h._solo_speech_stopped()

    handled = await h._resolve_solo_barge("嗯嗯")

    assert handled is True, "the loop must skip its normal turn bookkeeping"
    assert h._barge_paused is False and h._barge_pending is False
    h.connection.response.cancel.assert_not_awaited()
    h._clear_queue_callback.assert_not_called()
    assert h._barge_rollback_task is None
    # The console still sees what was said, exactly like a party-mode deny.
    surfaced = h.output_queue.get_nowait()
    assert isinstance(surfaced, AdditionalOutputs)
    assert surfaced.args[0] == {"role": "user", "content": "嗯嗯"}


@pytest.mark.asyncio
async def test_empty_transcript_rolls_back_instead_of_leaking_the_pause() -> None:
    """An empty transcript resolves the barge before the loop's early `continue`."""
    h = _solo_handler()
    _make_audible()
    h._response_done_event.clear()
    h._solo_speech_started()
    h._solo_speech_stopped()

    handled = await h._resolve_solo_barge("")

    assert handled is True
    assert h._barge_paused is False and h._barge_pending is False
    assert h.output_queue.empty(), "an empty transcript must not be surfaced"
    assert audio_drain.is_audible() is True


@pytest.mark.asyncio
async def test_substantive_transcript_confirms(monkeypatch: pytest.MonkeyPatch) -> None:
    """Real content means the user really was talking to the robot.

    The pre-name-gate rule, kept under `REALTIME_SOLO_NAME_GATE=0`: with the
    gate on, unaddressed content rolls back instead
    (`test_resolve_rolls_back_unaddressed_substantive_transcript`).
    """
    monkeypatch.setenv("REALTIME_SOLO_NAME_GATE", "0")
    h = _solo_handler()
    _make_audible()
    h._response_done_event.clear()
    h._solo_speech_started()
    h._solo_speech_stopped()

    handled = await h._resolve_solo_barge("幫我開燈")

    assert handled is False, "a real turn continues down the normal path"
    h.connection.response.cancel.assert_awaited_once()
    h._clear_queue_callback.assert_called_once()
    assert h._barge_paused is False and h._barge_pending is False
    assert h._barge_cooldown_until > time.monotonic()
    assert h._barge_watchdog_task is not None
    h.on_external_interrupt()


@pytest.mark.asyncio
async def test_transcription_failure_rolls_back() -> None:
    """No transcript will ever arrive; the pause must not outlive the turn."""
    h = _solo_handler()
    _make_audible()
    h._response_done_event.clear()
    h._solo_speech_started()
    h._solo_speech_stopped()

    h._resolve_solo_barge_failure("item_1")

    assert h._barge_paused is False and h._barge_pending is False
    h.connection.response.cancel.assert_not_awaited()


@pytest.mark.asyncio
async def test_cooldown_swallows_immediate_retrigger() -> None:
    """Right after a confirmed barge, the robot's own tail must not re-trigger one."""
    h = _solo_handler()
    _make_audible()
    h._response_done_event.clear()
    h._barge_cooldown_until = time.monotonic() + 5.0

    h._solo_speech_started()

    assert h._barge_paused is False and h._barge_pending is False
    assert h._barge_confirm_task is None
    assert h._barge_speech_open is True, "the speech is still tracked, only the pause is skipped"


@pytest.mark.asyncio
async def test_legacy_env_restores_old_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """REALTIME_SOLO_CLIENT_BARGE=0 brings back the immediate server-side flush."""
    monkeypatch.setenv("REALTIME_SOLO_CLIENT_BARGE", "0")
    started: list[object] = []
    monkeypatch.setattr(hf_mod, "on_user_speech_started", lambda deps: started.append(deps))
    h = _solo_handler()
    _make_audible()
    h._response_done_event.clear()

    h._solo_speech_started()

    h._clear_queue_callback.assert_called_once()
    assert started == [h.deps]
    assert h._barge_paused is False and h._barge_pending is False
    assert h._barge_confirm_task is None
    assert _turn_detection(False)["interrupt_response"] is True
    # The legacy flag restores server-side INTERRUPTION only; since 2026-08-31
    # answering is the client's job in every mode (mode plan, Task 2).
    assert _turn_detection(False)["create_response"] is False


def test_solo_turn_detection_hands_the_decision_to_the_client() -> None:
    """With the new default the server must not interrupt: the client owns it."""
    td = _turn_detection(party=False)
    assert td["interrupt_response"] is False
    assert td["create_response"] is False, "solo answers through the client's answer gate"


# --------------------------------------------------------------------------
# Pause mechanics: the emit path and the drain tracker
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_paused_emit_holds_audio_but_passes_additional_outputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pause must hold audio back without starving the mixed output queue."""
    monkeypatch.setenv("REALTIME_ONSET_RAMP_MS", "0")  # keep the buffers identity-comparable
    h = _emit_ready_handler()
    _install_barge_state(h)
    first = np.zeros((1, 320), dtype=np.int16)
    second = np.ones((1, 320), dtype=np.int16)
    text = AdditionalOutputs({"role": "assistant", "content": "hi"})

    h._pause_playback()
    await h.output_queue.put((ROBOT_RATE, first))
    await h.output_queue.put((ROBOT_RATE, second))
    await h.output_queue.put(text)

    assert await h.emit() is None, "audio is held, not emitted"
    assert await h.emit() is None
    assert await h.emit() is text, "AdditionalOutputs must still get through"
    assert len(h._held_audio) == 2

    h._resume_playback(rolled_back=True)

    out_first = await h.emit()
    out_second = await h.emit()
    assert isinstance(out_first, tuple) and out_first[1] is first
    assert isinstance(out_second, tuple) and out_second[1] is second
    assert not h._held_audio


@pytest.mark.asyncio
async def test_a_confirmed_barge_drops_the_held_audio() -> None:
    """The paused audio belongs to the reply that was just cancelled."""
    h = _solo_handler()
    _install_barge_state(h)
    h._pause_playback()
    h._held_audio.append((ROBOT_RATE, np.zeros((1, 160), dtype=np.int16)))

    h._resume_playback(rolled_back=False)

    assert not h._held_audio
    assert h._barge_paused is False


@pytest.mark.asyncio
async def test_audio_drain_paused_keeps_audible_and_blocks_drain() -> None:
    """While paused the play loop's queue-empty marks are lies and must not land."""
    generation = audio_drain.begin_response()
    audio_drain.note_enqueued(generation, sample_count=2400, sample_rate=24000)
    audio_drain.note_chunk(sample_count=2400, sample_rate=24000)
    audio_drain.close_response(generation)

    audio_drain.note_paused(True)
    audio_drain.note_queue_empty()
    assert audio_drain.is_audible() is True
    assert await audio_drain.wait_drained(generation, timeout_s=0.05) is False, (
        "music must not resume in the middle of a barge pause"
    )

    audio_drain.note_paused(False)
    audio_drain.note_queue_empty()
    assert await audio_drain.wait_drained(generation, timeout_s=0.5) is True

    audio_drain.note_paused(True)
    audio_drain.reset()
    assert audio_drain.is_audible() is False, "reset() must clear the paused flag too"


# --------------------------------------------------------------------------
# External interrupts, mode flips and session boundaries
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_external_interrupt_clears_held_audio_and_barge_state() -> None:
    """An operator RPC mid-pause owns the turn; nothing may survive it."""
    h = _solo_handler()
    _make_audible()
    h._response_done_event.clear()
    h._solo_speech_started()
    h._held_audio.append((ROBOT_RATE, np.zeros((1, 160), dtype=np.int16)))
    h._barge_rollback_task = asyncio.ensure_future(h._rollback_timer(h._party_utterance_seq))
    h._barge_watchdog_task = asyncio.ensure_future(h._barge_response_watchdog(h._party_utterance_seq))
    tasks = (h._barge_confirm_task, h._barge_rollback_task, h._barge_watchdog_task)

    h.on_external_interrupt()
    await asyncio.sleep(0)

    assert all(task.cancelled() or task.done() for task in tasks)
    assert h._barge_confirm_task is None
    assert h._barge_rollback_task is None
    assert h._barge_watchdog_task is None
    assert not h._held_audio
    assert h._barge_paused is False and h._barge_pending is False and h._barge_speech_open is False
    audio_drain.note_cleared()
    assert audio_drain.is_audible() is False, "the drain tracker must be unpaused"


@pytest.mark.asyncio
async def test_session_teardown_cannot_leave_a_pause_open() -> None:
    """A session that ends mid-pause must not strand held audio or a paused tracker."""
    h = _solo_handler()
    _make_audible()
    h._response_done_event.clear()
    h._solo_speech_started()
    confirm = h._barge_confirm_task
    h._held_audio.append((ROBOT_RATE, np.zeros((1, 160), dtype=np.int16)))

    await h._barge_shutdown()

    assert confirm.cancelled() or confirm.done()
    assert h._barge_paused is False and h._barge_pending is False
    assert not h._held_audio
    audio_drain.note_cleared()
    assert audio_drain.is_audible() is False


def test_clear_audio_queue_tells_the_handler_first() -> None:
    """The console flush must reach the handler before it drains the queue."""
    handler = MagicMock()
    handler.output_queue = asyncio.Queue()
    handler.output_queue.put_nowait((24000, np.zeros(4, dtype=np.int16)))
    order: list[str] = []
    handler.on_external_interrupt = MagicMock(side_effect=lambda: order.append("handler"))
    audio = SimpleNamespace(clear_player=MagicMock(side_effect=lambda: order.append("flush")))
    robot = SimpleNamespace(media=SimpleNamespace(audio=audio))
    stream = LocalStream(handler, robot)

    stream.clear_audio_queue()

    assert order == ["handler", "flush"]


@pytest.mark.asyncio
async def test_party_mode_flip_mid_pause_resumes_the_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    """Flipping to party mode while paused must not strand the pause forever."""
    # A real connection, so the truncate assertion below is not vacuous; the
    # session update the flip schedules is stubbed out (it would rebuild the
    # whole session config on a `__new__`-built handler).
    monkeypatch.setattr(OpenAIRealtimeHandler, "_push_mode_update", AsyncMock(return_value=True))
    h = _truncating_handler()
    truncate = h.connection.conversation.item.truncate
    _make_audible()
    h._audio_item_id = "item_abc"
    h._audio_item_enqueued_ms = 2000.0
    h._response_done_event.clear()
    h._solo_speech_started()
    assert h._barge_paused is True

    await h.set_conversation_mode("group")

    assert h._barge_paused is False and h._barge_pending is False
    # The flip rolls back rather than commits, and rollbacks never truncate:
    # truncation is irreversible and this reply is resuming.
    truncate.assert_not_awaited()
    # Fix round, finding 3: the solo speech flag is maintained by a branch that
    # stops running the moment the mode flips, so the flip must clear it — a
    # stale True would keep the watchdog standing down for the whole session.
    assert h._barge_speech_open is False
    # Task 4 fix round 2: the late-eligibility flag is written only by the solo
    # speech-start branch, so a flip mid-utterance must clear it too.
    assert h._barge_late_eligible is False
    # Final review: the rollback the flip performs records a resumed response
    # id, and no solo-loop branch will ever run to clear it — left set, it
    # would suppress the first legitimate late interrupt after party→solo.
    assert h._barge_resumed_response_id is None
    audio_drain.note_cleared()
    assert audio_drain.is_audible() is False


@pytest.mark.asyncio
async def test_a_flip_back_to_solo_clears_late_eligibility() -> None:
    """The direction that matters: an utterance begun in party mode is not solo-judged.

    Task 4 fix round 2. `_barge_late_eligible` is set only in
    `_solo_speech_started`, which party speech never reaches, so a party→solo
    flip landing mid-utterance would otherwise hand the completed-transcript
    handler a value recorded during some earlier, unrelated solo turn.
    """
    h = _solo_handler()
    h.connection = None  # no session update to schedule here
    h._conversation_mode = ConversationMode.GROUP
    h._barge_late_eligible = True  # stale, from a solo turn before party mode

    await h.set_conversation_mode("one_on_one")

    assert h._party_mode is False
    assert h._barge_late_eligible is False


@pytest.mark.asyncio
async def test_session_start_resets_barge_state() -> None:
    """A reconnect mid-pause must not carry held audio or a cooldown into it."""
    h = _solo_handler()
    _make_audible()
    h._response_done_event.clear()
    h._solo_speech_started()
    h._held_audio.append((ROBOT_RATE, np.zeros((1, 160), dtype=np.int16)))
    h._barge_cooldown_until = time.monotonic() + 5.0
    h._barge_response_seen = True

    h._barge_reset_for_new_session()

    assert h._barge_paused is False and h._barge_pending is False
    assert not h._held_audio
    assert h._barge_cooldown_until == 0.0
    assert h._barge_response_seen is False
    audio_drain.note_cleared()
    assert audio_drain.is_audible() is False


@pytest.mark.asyncio
async def test_a_stale_confirm_timer_never_fires(monkeypatch: pytest.MonkeyPatch) -> None:
    """A newer utterance owns the floor; the old timer must stand down.

    Gate OFF so the timer really would commit if the sequence guard failed —
    under the gate it would roll back and prove nothing.
    """
    monkeypatch.setenv("REALTIME_SOLO_NAME_GATE", "0")
    monkeypatch.setenv("REALTIME_BARGE_CONFIRM_MS", "30")
    h = _solo_handler()
    _make_audible()
    h._response_done_event.clear()
    h._solo_speech_started()
    h._party_utterance_seq += 1  # superseded

    await asyncio.sleep(0.1)

    h.connection.response.cancel.assert_not_awaited()
    h._clear_queue_callback.assert_not_called()
    h.on_external_interrupt()


@pytest.mark.asyncio
async def test_the_watchdog_asks_for_the_reply_the_server_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """After a barge the user's own turn can lose its auto-response; repair it."""
    monkeypatch.setattr(hf_mod, "_BARGE_RESPONSE_WATCHDOG_S", 0.03)
    h = _solo_handler()
    h._barge_response_seen = False

    await h._barge_response_watchdog(h._party_utterance_seq)

    assert h._pending_responses.qsize() == 1


@pytest.mark.asyncio
async def test_the_watchdog_stands_down_when_a_response_arrived(monkeypatch: pytest.MonkeyPatch) -> None:
    """A reply that did start must never be doubled by the watchdog."""
    monkeypatch.setattr(hf_mod, "_BARGE_RESPONSE_WATCHDOG_S", 0.03)
    h = _solo_handler()
    h._barge_note_response_created()

    await h._barge_response_watchdog(h._party_utterance_seq)

    assert h._barge_response_seen is True
    assert h._pending_responses.qsize() == 0


# --------------------------------------------------------------------------
# Name gate (2026-08-30 plan, Task 1)
# --------------------------------------------------------------------------


def test_the_solo_name_gate_defaults_off() -> None:
    """D-032 (2026-09-05): in 一對一聊天模式 any real sentence stops the reply.

    The operator's ruling flips D-028's default: with the env unset the
    interruption gate is OFF, so a paused reply is decided by the substantive
    rule rather than by the robot's name. `=1` restores the story-telling
    posture as a knob.
    """
    assert hf_mod._solo_name_gate() is False


def test_the_solo_name_gate_is_still_a_knob(monkeypatch: pytest.MonkeyPatch) -> None:
    """`REALTIME_SOLO_NAME_GATE=1` brings D-028's address requirement back."""
    monkeypatch.setenv("REALTIME_SOLO_NAME_GATE", "1")
    assert hf_mod._solo_name_gate() is True


def test_gate_text_accepts_name_and_control() -> None:
    """Names and control phrases pass; substantive unaddressed speech does not."""
    assert hf_mod._gate_text_accepts("瑞奇你說錯了") == (True, "name")
    assert hf_mod._gate_text_accepts("Hey Reachy, stop there") == (True, "control phrase")
    assert hf_mod._gate_text_accepts("停") == (True, "control phrase")
    accepted, reason = hf_mod._gate_text_accepts("我們晚餐要吃什麼呢")
    assert not accepted and reason == "unaddressed"


@pytest.mark.parametrize(
    ("gate", "transcript", "expected"),
    [
        # Gate ON (`=1`): D-028's address rule, `_gate_text_accepts` verbatim.
        ("1", "停", (True, "control phrase")),
        ("1", "瑞奇你等一下", (True, "name")),
        ("1", "我們晚餐要吃什麼呢這麼晚了", (False, "unaddressed")),
        ("1", "嗯", (False, "unaddressed")),
        ("1", "", (False, "unaddressed")),
        # Gate OFF (the D-032 default): control phrases first, then any real
        # sentence. A backchannel or an empty transcript still rolls back.
        ("0", "停", (True, "control phrase")),
        ("0", "瑞奇你等一下", (True, "substantive")),
        ("0", "我們晚餐要吃什麼呢這麼晚了", (True, "substantive")),
        ("0", "嗯", (False, "backchannel")),
        ("0", "", (False, "backchannel")),
    ],
)
def test_the_solo_interrupt_verdict_table(
    monkeypatch: pytest.MonkeyPatch, gate: str, transcript: str, expected: tuple[bool, str]
) -> None:
    """One verdict decides both halves of an interruption: the pause and the late path."""
    monkeypatch.setenv("REALTIME_SOLO_NAME_GATE", gate)
    assert hf_mod._solo_interrupt_verdict(transcript) == expected


@pytest.mark.asyncio
async def test_resolve_rolls_back_unaddressed_substantive_transcript(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gate ON (`=1`, no longer the default): substantive speech without a name resumes."""
    monkeypatch.setenv("REALTIME_SOLO_NAME_GATE", "1")
    h = _solo_handler()
    _make_audible()
    h._response_done_event.clear()
    h._solo_speech_started()
    assert h._barge_pending
    resumed = await h._resolve_solo_barge("我們晚餐要吃什麼呢這麼晚了")
    assert resumed is True
    assert not h._barge_paused
    h.connection.response.cancel.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolve_commits_on_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gate ON (`=1`): the robot's name in the transcript commits the barge."""
    monkeypatch.setenv("REALTIME_SOLO_NAME_GATE", "1")
    h = _solo_handler()
    _make_audible()
    h._response_done_event.clear()
    h._solo_speech_started()
    resumed = await h._resolve_solo_barge("瑞奇我想先問一件事")
    assert resumed is False
    h.connection.response.cancel.assert_awaited_once()


@pytest.mark.asyncio
async def test_gate_off_restores_substantive_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    """REALTIME_SOLO_NAME_GATE=0: substantive speech commits, as before."""
    monkeypatch.setenv("REALTIME_SOLO_NAME_GATE", "0")
    h = _solo_handler()
    _make_audible()
    h._response_done_event.clear()
    h._solo_speech_started()
    resumed = await h._resolve_solo_barge("我們晚餐要吃什麼呢這麼晚了")
    assert resumed is False
    h.connection.response.cancel.assert_awaited_once()


# --------------------------------------------------------------------------
# Partial-transcript fast commit (2026-08-30 plan, Task 2)
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_partial_transcript_with_name_commits_early(monkeypatch: pytest.MonkeyPatch) -> None:
    """A delta containing the name resolves the pause without waiting for completed.

    Pinned with the gate ON (`=1`) because that is the posture the name path was
    built for; `test_a_partial_name_commits_with_the_gate_off` pins the same
    behaviour on the shipped default (D-032, T3).
    """
    monkeypatch.setenv("REALTIME_SOLO_NAME_GATE", "1")
    h = _solo_handler()
    _make_audible()
    h._response_done_event.clear()
    h._solo_speech_started()
    assert h._barge_pending
    await h._maybe_commit_on_partial("欸瑞奇", "item_1")
    assert not h._barge_pending
    assert h._barge_partial_committed_item == "item_1"
    h.connection.response.cancel.assert_awaited_once()
    # A later delta must not double-commit.
    await h._maybe_commit_on_partial("欸瑞奇你聽我說", "item_1")
    h.connection.response.cancel.assert_awaited_once()


@pytest.mark.asyncio
async def test_partial_transcript_without_name_keeps_pause() -> None:
    """Unaddressed speech proves nothing: the pause stays pending for `completed`."""
    h = _solo_handler()
    _make_audible()
    h._response_done_event.clear()
    h._solo_speech_started()
    await h._maybe_commit_on_partial("我們晚餐", "item_1")
    assert h._barge_pending
    h.connection.response.cancel.assert_not_awaited()


@pytest.mark.asyncio
async def test_partial_control_phrase_commits_even_with_the_gate_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A robot you cannot silence is worse than any false positive: 「停」 always commits."""
    monkeypatch.setenv("REALTIME_SOLO_NAME_GATE", "0")
    h = _solo_handler()
    _make_audible()
    h._response_done_event.clear()
    h._solo_speech_started()
    await h._maybe_commit_on_partial("停", "item_1")
    assert not h._barge_pending
    assert h._barge_partial_committed_item == "item_1"
    h.connection.response.cancel.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_partial_name_commits_with_the_gate_off(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """D-032 T3: a name in a partial proves address in any mode, gate or no gate.

    Inverts the pre-D-032 pin. The old restriction — "the name path is
    gate-mode only" — was a latency-lever scoping, never a safety property:
    somebody saying 「欸瑞奇」 over a talking robot means to stop it whichever
    rule decides the pause. Known risk (Codex round 1, finding 8):
    `_gate_text_accepts` substring-matches a provisional partial the completed
    transcript may correct, and the cost of that false positive is a cut reply
    whose heard part is preserved by the truncate — never lost context.
    """
    monkeypatch.setenv("REALTIME_SOLO_NAME_GATE", "0")
    h = _solo_handler()
    _make_audible()
    h._response_done_event.clear()
    h._solo_speech_started()
    with caplog.at_level("INFO"):
        await h._maybe_commit_on_partial("欸瑞奇", "item_1")
    assert not h._barge_pending
    assert h._barge_partial_committed_item == "item_1"
    h.connection.response.cancel.assert_awaited_once()
    assert "solo barge-in confirmed by partial transcript (name)" in caplog.text
    h.on_external_interrupt()  # cleanup: the watchdog task is real


@pytest.mark.asyncio
async def test_a_partial_backchannel_never_commits() -> None:
    """No substantive-on-partial: 「嗯嗯」 can still grow into 「嗯嗯好」.

    A partial can prove address; it cannot prove substantiveness, so the
    completed transcript keeps that half of the decision.
    """
    h = _solo_handler()
    _make_audible()
    h._response_done_event.clear()
    h._solo_speech_started()
    await h._maybe_commit_on_partial("我們晚餐要吃什麼呢這麼晚了", "item_1")
    assert h._barge_pending
    assert h._barge_partial_committed_item is None
    h.connection.response.cancel.assert_not_awaited()


@pytest.mark.asyncio
async def test_partial_commit_is_inert_in_party_mode() -> None:
    """Party mode owns its own barge decision; the partial path must not touch it."""
    h = _solo_handler()
    _make_audible()
    h._response_done_event.clear()
    h._solo_speech_started()
    h._conversation_mode = ConversationMode.GROUP
    await h._maybe_commit_on_partial("欸瑞奇", "item_1")
    assert h._barge_pending
    h.connection.response.cancel.assert_not_awaited()


@pytest.mark.asyncio
async def test_incremental_deltas_accumulate_and_commit_split_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """GA deltas are incremental: 瑞 + 奇 across two deltas must still match (round 2, finding 3)."""
    monkeypatch.setenv("REALTIME_SOLO_NAME_GATE", "1")
    h = _solo_handler()
    _make_audible()
    h._response_done_event.clear()
    h._solo_speech_started()
    chunks = hf_mod.InputTranscriptChunksByItem(item_id=None, deltas=[])
    h._record_partial_transcript_delta(chunks, "item_1", "欸瑞")
    h._record_partial_transcript_delta(chunks, "item_1", "奇你聽我說")
    joined = "".join(chunks.deltas)
    assert joined == "欸瑞奇你聽我說"
    await h._maybe_commit_on_partial(joined, "item_1")
    assert not h._barge_pending
    assert h._barge_partial_committed_item == "item_1"
    # A new item resets the accumulator.
    h._record_partial_transcript_delta(chunks, "item_2", "另一句")
    assert chunks.deltas == ["另一句"]


def test_base_handler_partial_deltas_stay_snapshots() -> None:
    """The HF-compatible server sends snapshots; only the OpenAI subclass appends."""
    chunks = hf_mod.InputTranscriptChunksByItem(item_id=None, deltas=[])
    base = HuggingFaceRealtimeHandler.__new__(HuggingFaceRealtimeHandler)
    base._record_partial_transcript_delta(chunks, "item_1", "欸瑞")
    base._record_partial_transcript_delta(chunks, "item_1", "欸瑞奇")
    assert chunks.deltas == ["欸瑞奇"]


@pytest.mark.asyncio
async def test_partial_commit_survives_the_flush_that_resets_barge_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In production `_clear_queue` IS `console.clear_audio_queue`, which resets everything.

    `_commit_solo_barge` flushes through it, so the committed item can only be
    recorded *after* that call returns — recording it first would be wiped.
    """
    monkeypatch.setenv("REALTIME_SOLO_NAME_GATE", "1")
    h = _solo_handler()
    h._clear_queue = h.on_external_interrupt
    _make_audible()
    h._response_done_event.clear()
    h._solo_speech_started()
    await h._maybe_commit_on_partial("欸瑞奇", "item_1")
    assert h._barge_partial_committed_item == "item_1"


def test_partial_committed_item_is_cleared_by_an_external_interrupt() -> None:
    """An operator RPC taking over the turn must not leave a stale committed item."""
    h = _solo_handler()
    h._barge_partial_committed_item = "item_1"
    h.on_external_interrupt()
    assert h._barge_partial_committed_item is None


# --------------------------------------------------------------------------
# Confirm timer as a bounded max pause (2026-08-30 plan, Task 3)
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sustained_unaddressed_speech_resumes_at_max_pause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gate ON (`=1`): long speech with no name rolls the pause back instead of committing."""
    monkeypatch.setenv("REALTIME_SOLO_NAME_GATE", "1")
    monkeypatch.setenv("REALTIME_BARGE_MAX_PAUSE_MS", "10")
    h = _solo_handler()
    _make_audible()
    h._response_done_event.clear()
    h._solo_speech_started()
    assert h._barge_confirm_task is not None
    await asyncio.wait_for(h._barge_confirm_task, timeout=1.0)
    assert not h._barge_paused and not h._barge_pending
    h.connection.response.cancel.assert_not_awaited()
    # The reply really came back: its audio is still accounted for, and nothing
    # is left holding the pause open.
    assert audio_drain.is_audible() is True
    assert h._barge_speech_open is True, "the room is still talking; Reachy talks on"
    h.on_external_interrupt()


@pytest.mark.asyncio
async def test_sustained_speech_still_commits_with_gate_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gate OFF: the same timer keeps its pre-plan meaning — sustained speech commits."""
    monkeypatch.setenv("REALTIME_SOLO_NAME_GATE", "0")
    monkeypatch.setenv("REALTIME_BARGE_CONFIRM_MS", "10")
    h = _solo_handler()
    _make_audible()
    h._response_done_event.clear()
    h._solo_speech_started()
    await asyncio.wait_for(h._barge_confirm_task, timeout=1.0)
    h.connection.response.cancel.assert_awaited_once()
    h.on_external_interrupt()


@pytest.mark.asyncio
async def test_a_rolled_back_max_pause_arms_no_orphan_timer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After the cap fires, `speech_stopped` must not arm a second, orphan pause.

    The rollback already ended the pause, so `_solo_speech_stopped` has nothing
    left to decide — it must return without arming a rollback timer that would
    later resume a reply nobody paused. The cap is gate-on-only code, so the
    knob is set explicitly (D-032 flipped the default off).
    """
    monkeypatch.setenv("REALTIME_SOLO_NAME_GATE", "1")
    monkeypatch.setenv("REALTIME_BARGE_MAX_PAUSE_MS", "10")
    h = _solo_handler()
    _make_audible()
    h._response_done_event.clear()
    h._solo_speech_started()
    await asyncio.wait_for(h._barge_confirm_task, timeout=1.0)

    h._solo_speech_stopped()

    assert h._barge_rollback_task is None
    assert h._barge_confirm_task is None
    assert h._barge_speech_open is False


def test_the_max_pause_default_outlasts_a_sentence() -> None:
    """The cap is patience, not a second confirm window: seconds, not milliseconds."""
    assert hf_mod._barge_max_pause_s() == 4.0
    assert hf_mod._barge_max_pause_s() > _barge_confirm_s()


# --------------------------------------------------------------------------
# Late interrupt on an addressed committed turn (2026-08-30 plan, Task 4)
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_late_addressed_transcript_silences_resumed_reply() -> None:
    """Name in a committed turn while the reply is audible → cancel + flush + watchdog."""
    h = _solo_handler()
    _make_audible()
    h._response_done_event.clear()
    h._solo_speech_started()
    # Max-pause rollback happened; the reply is audible again.
    h._barge_pending = False
    h._resume_playback(rolled_back=True)
    assert h._barge_resumed_response_id == "resp_123"
    await h._late_solo_interrupt()
    h.connection.response.cancel.assert_awaited_once()
    h._clear_queue_callback.assert_called()
    assert h._barge_watchdog_task is not None  # the addressed turn must get an answer
    h.on_external_interrupt()  # cleanup: the watchdog task is real


@pytest.mark.asyncio
async def test_late_interrupt_with_no_resumed_id_still_silences() -> None:
    """Cooldown swallowed the pause: no resumed id, but the name must still stop the reply."""
    h = _solo_handler()
    _make_audible()
    h._response_done_event.clear()
    assert h._barge_resumed_response_id is None
    await h._late_solo_interrupt()
    h.connection.response.cancel.assert_awaited_once()
    h._clear_queue_callback.assert_called()
    h.on_external_interrupt()  # cleanup: the watchdog task is real


@pytest.mark.asyncio
async def test_late_interrupt_cancels_a_newer_response(caplog: pytest.LogCaptureFixture) -> None:
    """D-032 T2b: the late path stops a newer response too — it is what is being talked over.

    Inverts the pre-D-032 pin. The one exception is a response the barge
    watchdog requested for THIS same utterance; that guard lives in the
    completed handler, above this call.
    """
    h = _solo_handler()
    _make_audible()
    h._response_done_event.clear()
    h._barge_resumed_response_id = "resp_old"
    h._active_response_id = "resp_new"
    with caplog.at_level("INFO"):
        await h._late_solo_interrupt()
    h.connection.response.cancel.assert_awaited_once()
    assert "resp_new" in h._cancelled_response_ids
    assert "solo barge: cancelling a newer response (resp_new) the user talked over" in caplog.text
    h._clear_queue_callback.assert_called_once()


@pytest.mark.asyncio
async def test_a_transcript_decided_rollback_clears_the_resumed_id() -> None:
    """`_resolve_solo_barge` fully decides the utterance, so it owns the cleanup.

    Its caller `continue`s before the completed handler's trailing clear, so a
    resumed id left behind here would sit through later turns and suppress the
    next real late interrupt.

    Driven on the shipped default (gate off, D-032), where the transcript that
    rolls a pause back is a backchannel rather than an unaddressed sentence.
    """
    h = _solo_handler()
    _make_audible()
    h._response_done_event.clear()
    h._solo_speech_started()
    assert await h._resolve_solo_barge("嗯") is True
    assert h._barge_resumed_response_id is None


def test_transcription_failure_clears_the_late_interrupt_state() -> None:
    """A failed transcript decides nothing, so both late-path fields must reset.

    The clears sit ahead of the `_barge_pending` guard on purpose: the common
    case here is a max-pause rollback that already ended the pause, and its
    resumed id would otherwise outlive the turn that produced it.
    """
    h = _solo_handler()
    h._barge_resumed_response_id = "resp_123"
    h._barge_partial_committed_item = "item_1"
    assert h._barge_pending is False
    h._resolve_solo_barge_failure("item_1")
    assert h._barge_resumed_response_id is None
    assert h._barge_partial_committed_item is None


def test_another_turns_transcription_failure_keeps_the_partial_marker() -> None:
    """T4 m5: the committed-item marker names one turn; only that turn may consume it.

    The resumed id and the eligibility flag are session-wide, so a failure
    clears them whichever turn failed. The marker is not — cleared by an
    unrelated turn's failure, the marked turn's own completed transcript would
    no longer be recognised as already-interrupted and would interrupt twice.
    """
    h = _solo_handler()
    h._barge_resumed_response_id = "resp_123"
    h._barge_partial_committed_item = "item_1"

    h._resolve_solo_barge_failure("item_2")

    assert h._barge_partial_committed_item == "item_1"
    assert h._barge_resumed_response_id is None
    assert h._barge_late_eligible is False


def test_an_id_less_transcription_failure_keeps_the_partial_marker() -> None:
    """An event with no item id names no turn, so it may not consume the marker."""
    h = _solo_handler()
    h._barge_partial_committed_item = "item_1"

    h._resolve_solo_barge_failure(None)

    assert h._barge_partial_committed_item == "item_1"


def test_external_interrupt_clears_the_resumed_id() -> None:
    """An operator RPC takes over the turn: no late interrupt may fire for it."""
    h = _solo_handler()
    h._barge_resumed_response_id = "resp_123"
    h.on_external_interrupt()
    assert h._barge_resumed_response_id is None


def test_external_interrupt_clears_late_eligibility() -> None:
    """Whatever took the turn over, this utterance's onset no longer decides anything."""
    h = _solo_handler()
    h._barge_late_eligible = True
    h.on_external_interrupt()
    assert h._barge_late_eligible is False


# --- eligibility at speech onset (fix round 1, finding 1) ------------------


@pytest.mark.asyncio
async def test_an_idle_start_is_not_late_eligible() -> None:
    """A turn begun in silence must never cancel the answer being made for it.

    Fix round 1, finding 1: `response.created` for that answer routinely
    precedes the turn's own `transcription.completed`, so a named question from
    silence would otherwise reach the late path with a live response that IS
    its answer — cut, then repeated by the watchdog 1.5 s later.
    """
    h = _solo_handler()  # `_response_done_event` set, no queued audio: silent
    h._solo_speech_started()
    assert h._barge_late_eligible is False


@pytest.mark.asyncio
async def test_speech_over_a_talking_robot_is_late_eligible() -> None:
    """The three cases the late path exists for all begin over an audible reply."""
    h = _solo_handler()
    _make_audible()
    h._response_done_event.clear()
    h._solo_speech_started()
    assert h._barge_late_eligible is True
    h.on_external_interrupt()


@pytest.mark.asyncio
async def test_a_cooldown_suppressed_onset_is_still_late_eligible() -> None:
    """Eligibility is recorded ahead of the cooldown return, which owns a real case.

    A name spoken inside the post-barge cooldown never gets a pause at all, so
    the late path is the only thing that can silence the reply for it.
    """
    h = _solo_handler()
    _make_audible()
    h._response_done_event.clear()
    h._barge_cooldown_until = time.monotonic() + 5
    h._solo_speech_started()
    assert h._barge_pending is False, "the cooldown suppressed the pause"
    assert h._barge_late_eligible is True


@pytest.mark.asyncio
async def test_late_interrupt_keeps_speech_open_through_the_real_console_flush(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The late flush must not wipe the speech state its own watchdog reads.

    Fix round 1, finding 2 — the analogue of
    `test_confirm_keeps_speech_open_through_the_real_console_flush`. In
    production `_clear_queue` is the real `console.clear_audio_queue`, which
    calls `on_external_interrupt()`. If that reset takes `_barge_speech_open`
    with it, the watchdog armed moments later has nothing left to stop it from
    firing a response at a user who is still talking. Wired through the REAL
    console, not a bare mock.
    """
    monkeypatch.setattr(hf_mod, "_BARGE_RESPONSE_WATCHDOG_S", 0.01)
    h = _solo_handler()
    audio = SimpleNamespace(clear_player=MagicMock())
    robot = SimpleNamespace(media=SimpleNamespace(audio=audio))
    LocalStream(h, robot)  # installs the real clear_audio_queue as _clear_queue
    _make_audible()
    h._response_done_event.clear()
    h._barge_speech_open = True  # the user is talking on, past their committed turn

    await h._late_solo_interrupt()

    audio.clear_player.assert_called_once()  # the real console flush really ran
    assert h._barge_speech_open is True, "the user is still mid-sentence"

    # ... and the watchdog therefore stands down instead of talking over them.
    h._response_done_event.set()
    h._barge_response_seen = False
    await h._barge_response_watchdog(h._party_utterance_seq)
    assert h._pending_responses.qsize() == 0
    h.on_external_interrupt()


# --- the decision, driven through the real event loop (fix round 1, finding 3)


def _loop_handler(events: tuple[_FakeEvent, ...]) -> HuggingFaceRealtimeHandler:
    """Build a real handler whose realtime session replays `events` (music-barge harness)."""
    handler = HuggingFaceRealtimeHandler(ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock()))
    # This file is the SOLO machine, and since the 2026-08-31 mode wave a real
    # handler boots into 多人聊天模式 — which routes speech through the room
    # branch and never reaches the code under test. Pin the mode explicitly so
    # these loop tests stay about what they say they are about.
    handler._conversation_mode = ConversationMode.ONE_ON_ONE
    handler.client = _make_fake_realtime_client(events=events)
    return handler


def _quiet_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutralise everything a session touches except the barge decision."""
    monkeypatch.setattr(hf_mod, "get_session_instructions", lambda _instance_path=None: "test")
    monkeypatch.setattr(hf_mod, "get_session_voice", lambda default="cedar": default)
    monkeypatch.setattr(hf_mod, "get_tool_specs", lambda exclusion_list=None: [])
    monkeypatch.setattr(hf_mod, "on_assistant_turn_ended", lambda _deps, _live=None: None)
    monkeypatch.setattr(hf_mod, "on_response_created", lambda: None)
    monkeypatch.setattr(hf_mod, "on_user_speech_candidate", lambda _deps: None)
    monkeypatch.setattr(hf_mod, "on_turn_without_response", lambda _deps: None)


def _state_at_speech_start(
    *,
    audible: bool = True,
    eligible: bool = True,
    party: bool = False,
    active_id: str | None = "resp_A",
    partial_item: str | None = None,
    watchdog_answered: str | None = None,
    response_seen: bool = False,
):
    """Stand in for `_solo_speech_started`, planting the state under test.

    The session-boundary reset runs inside `_run_realtime_session`, so the state
    a completed transcript is judged against has to be installed from *within*
    the loop — the same monkeypatch seam
    `test_the_loop_routes_solo_speech_through_the_barge_hooks` uses.
    """

    def _apply(self: HuggingFaceRealtimeHandler, item_id: str | None = None) -> None:
        if audible:
            self._response_done_event.clear()
        else:
            self._response_done_event.set()
        self._barge_late_eligible = eligible
        self._conversation_mode = ConversationMode.GROUP if party else ConversationMode.ONE_ON_ONE
        self._active_response_id = active_id
        self._barge_partial_committed_item = partial_item
        self._barge_watchdog_answered_item = watchdog_answered
        self._barge_response_seen = response_seen

    return _apply


async def _run_late_path(
    monkeypatch: pytest.MonkeyPatch,
    *,
    transcript: str = "瑞奇你等一下",
    **state: Any,
) -> tuple[list[str], HuggingFaceRealtimeHandler]:
    """Replay speech-start → committed transcript; report whether the late path fired."""
    _quiet_session(monkeypatch)
    fired: list[str] = []

    async def _record(self: HuggingFaceRealtimeHandler) -> None:
        fired.append("late")

    monkeypatch.setattr(hf_mod.HuggingFaceRealtimeHandler, "_solo_speech_started", _state_at_speech_start(**state))
    monkeypatch.setattr(hf_mod.HuggingFaceRealtimeHandler, "_late_solo_interrupt", _record)
    handler = _loop_handler(
        (
            _FakeEvent("input_audio_buffer.speech_started"),
            _FakeEvent(
                "conversation.item.input_audio_transcription.completed",
                transcript=transcript,
                item_id="item_1",
            ),
        )
    )
    await handler._run_realtime_session()
    return fired, handler


@pytest.mark.asyncio
async def test_the_loop_late_interrupts_an_addressed_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole point, reached through the real event loop rather than by hand.

    Gate ON (`=1`): the name is what makes this turn addressed. The gate-off
    default has its own pin — a plain sentence with no name (D-032, T2).
    """
    monkeypatch.setenv("REALTIME_SOLO_NAME_GATE", "1")
    fired, handler = await _run_late_path(monkeypatch)
    assert fired == ["late"]
    assert handler._barge_resumed_response_id is None  # the turn is decided
    assert handler._barge_late_eligible is False


@pytest.mark.asyncio
async def test_the_loop_does_not_late_interrupt_an_idle_start(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fix round 1, finding 1: a named question from silence keeps its own answer."""
    fired, handler = await _run_late_path(monkeypatch, eligible=False)
    assert fired == []
    assert handler._active_response_id == "resp_A", "the answer to this very turn is untouched"


@pytest.mark.asyncio
async def test_the_loop_does_not_late_interrupt_a_partial_committed_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A partial-committed turn already interrupted; the reply now playing is its answer."""
    fired, handler = await _run_late_path(monkeypatch, partial_item="item_1")
    assert fired == []
    assert handler._barge_partial_committed_item is None, "the marker is consumed, not left to rot"


@pytest.mark.asyncio
async def test_the_loop_does_not_late_interrupt_a_silent_robot(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing to talk over: the reply drained before the transcript landed."""
    fired, _ = await _run_late_path(monkeypatch, audible=False, active_id=None)
    assert fired == []


@pytest.mark.asyncio
async def test_the_loop_does_not_late_interrupt_in_party_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Party mode owns its own gate and its own barge timer; the solo path stays out."""
    fired, _ = await _run_late_path(monkeypatch, party=True)
    assert fired == []


@pytest.mark.asyncio
async def test_the_loop_does_not_late_interrupt_an_unaddressed_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gate ON (`=1`): audible and eligible, but nobody said the name — Reachy talks on."""
    monkeypatch.setenv("REALTIME_SOLO_NAME_GATE", "1")
    fired, _ = await _run_late_path(monkeypatch, transcript="我們晚餐要吃什麼呢這麼晚了")
    assert fired == []


@pytest.mark.asyncio
async def test_the_loop_late_interrupts_a_plain_sentence_with_the_gate_off(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """D-032: on the shipped default a plain sentence over a talking robot stops it.

    RCA Finding 3's other half. The pause for this utterance was rolled back by
    the 2 s rollback timer before its transcript existed, so without the late
    path the answer would queue up *behind* the reply the user talked over.
    """
    with caplog.at_level("INFO"):
        fired, _ = await _run_late_path(monkeypatch, transcript="我們晚餐要吃什麼呢這麼晚了")
    assert fired == ["late"]
    assert "late solo interrupt (substantive) on committed turn" in caplog.text


@pytest.mark.asyncio
async def test_the_loop_never_late_interrupts_on_a_backchannel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """「嗯」 is not an interruption under either gate; the one-on-one gate denies it first."""
    fired, _ = await _run_late_path(monkeypatch, transcript="嗯")
    assert fired == []


@pytest.mark.asyncio
async def test_the_loop_late_interrupts_a_control_phrase_with_the_gate_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A robot you cannot silence is worse than any false positive: 「停」 beats the flag."""
    monkeypatch.setenv("REALTIME_SOLO_NAME_GATE", "0")
    fired, _ = await _run_late_path(monkeypatch, transcript="停")
    assert fired == ["late"]


@pytest.mark.asyncio
async def test_the_loop_never_late_interrupts_on_the_legacy_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REALTIME_SOLO_CLIENT_BARGE=0 is the pre-plan wiring, untouched."""
    monkeypatch.setenv("REALTIME_SOLO_CLIENT_BARGE", "0")
    fired, _ = await _run_late_path(monkeypatch)
    assert fired == []


@pytest.mark.asyncio
async def test_the_loop_clears_the_resumed_id_when_that_reply_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The resumed reply ending naturally is the bounded cleanup for its id."""
    _quiet_session(monkeypatch)

    def _plant(self: HuggingFaceRealtimeHandler, item_id: str | None = None) -> None:
        self._barge_resumed_response_id = "resp_A"

    monkeypatch.setattr(hf_mod.HuggingFaceRealtimeHandler, "_solo_speech_started", _plant)
    handler = _loop_handler(
        (
            _FakeEvent("input_audio_buffer.speech_started"),
            _FakeEvent("response.done", response=SimpleNamespace(id="resp_other")),
            _FakeEvent("response.done", response=SimpleNamespace(id="resp_A")),
        )
    )
    await handler._run_realtime_session()
    assert handler._barge_resumed_response_id is None


def test_the_late_interrupt_runs_before_the_turn_clears_its_state() -> None:
    """Source ordering the behavioral tests above cannot see.

    The trailing clear must stay *below* the late block: moved above it, the
    newer-answer guard would read a field this same handler had just wiped.
    """
    import inspect

    source = inspect.getsource(HuggingFaceRealtimeHandler._run_realtime_session)
    assert source.index("await self._late_solo_interrupt()") < source.rindex("self._barge_resumed_response_id = None")


def test_the_gate_silences_the_confirm_race_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """With the gate on (`=1`) there is no confirm-commit branch left to warn about."""
    monkeypatch.setenv("REALTIME_SOLO_NAME_GATE", "1")
    monkeypatch.setattr(hf_mod, "_BARGE_CONFIRM_WARNED", False)
    monkeypatch.setenv("REALTIME_BARGE_CONFIRM_MS", "250")
    with caplog.at_level("WARNING"):
        hf_mod.warn_if_barge_confirm_races_vad()
    assert caplog.text == ""


def test_semantic_vad_silences_the_confirm_race_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The warning compares against a `server_vad` knob semantic VAD ignores.

    Recorded known edge (`progress.md`): under `REALTIME_VAD_TYPE=semantic_vad`
    the server never reads `REALTIME_VAD_SILENCE_DURATION_MS`, so warning about
    the confirm window racing it was noise about a value with no effect.
    """
    monkeypatch.setattr(hf_mod, "_BARGE_CONFIRM_WARNED", False)
    monkeypatch.setenv("REALTIME_SOLO_NAME_GATE", "0")
    monkeypatch.setenv("REALTIME_VAD_TYPE", "Semantic_VAD")
    monkeypatch.setenv("REALTIME_BARGE_CONFIRM_MS", "250")
    with caplog.at_level("WARNING"):
        hf_mod.warn_if_barge_confirm_races_vad()
    assert caplog.text == ""


def test_barge_state_defaults_exist_on_the_base_handler() -> None:
    """The real __init__ must define every field the loop and tests touch."""
    import inspect

    source = inspect.getsource(HuggingFaceRealtimeHandler.__init__)
    for field in (
        "_barge_paused",
        "_barge_pending",
        "_barge_speech_open",
        "_barge_confirm_task",
        "_barge_rollback_task",
        "_barge_watchdog_task",
        "_barge_cooldown_until",
        "_barge_response_seen",
        "_barge_partial_committed_item",
        "_barge_resumed_response_id",
        "_barge_late_eligible",
        "_held_audio",
        "_audio_item_id",
        "_audio_item_enqueued_ms",
        "_barge_paused_item_id",
        "_barge_paused_heard_ms",
        "_barge_utterance_item_id",
        "_barge_watchdog_answered_item",
        "_barge_late_eligibles",
    ):
        assert field in source, field


# --------------------------------------------------------------------------
# conversation.item.truncate on committed interruptions (Task 5)
# --------------------------------------------------------------------------


def _truncating_handler() -> OpenAIRealtimeHandler:
    """Return a solo handler whose connection records `conversation.item.truncate`."""
    h = _solo_handler()
    h.connection = SimpleNamespace(
        response=SimpleNamespace(cancel=AsyncMock()),
        conversation=SimpleNamespace(item=SimpleNamespace(truncate=AsyncMock())),
    )
    return h


def _production_flush(handler: OpenAIRealtimeHandler):
    """Return a `_clear_queue` that destroys what the real flush destroys.

    In the app `_clear_queue` IS `console.clear_audio_queue`: it calls
    `on_external_interrupt()` (which forgets the audio item and the pause stash)
    and `audio_drain.note_cleared()` (which zeroes `outstanding` and the
    device-buffer estimate). A `MagicMock()` does neither, so a truncate capture
    that had drifted below the flush would keep passing — the mutation the
    capture-before-flush ordering exists to prevent.
    """

    def _flush() -> None:
        handler.on_external_interrupt()
        audio_drain.note_cleared()

    return _flush


@pytest.mark.asyncio
async def test_commit_sends_truncate_with_heard_ms() -> None:
    """A committed barge truncates the paused item at the heard position."""
    h = _truncating_handler()
    truncate = h.connection.conversation.item.truncate
    generation = audio_drain.begin_response()
    h._audio_item_id = "item_abc"
    # 2000 ms enqueued for the item, 500 ms still outstanding → heard ≈ 1200 ms.
    h._audio_item_enqueued_ms = 2000.0
    audio_drain.note_enqueued(generation, sample_count=12000, sample_rate=24000)
    h._response_done_event.clear()
    h._solo_speech_started()
    await h._commit_solo_barge()
    truncate.assert_awaited_once()
    kwargs = truncate.await_args.kwargs
    assert kwargs["item_id"] == "item_abc"
    assert kwargs["content_index"] == 0
    assert 0 < kwargs["audio_end_ms"] <= 1200
    h.on_external_interrupt()  # cleanup: the watchdog task is real


@pytest.mark.asyncio
async def test_rollback_never_truncates() -> None:
    """Truncation deletes server-side transcript; a resumed reply keeps all of it."""
    h = _truncating_handler()
    truncate = h.connection.conversation.item.truncate
    _make_audible()
    h._audio_item_id = "item_abc"
    h._audio_item_enqueued_ms = 2000.0
    h._response_done_event.clear()
    h._solo_speech_started()
    await h._resolve_solo_barge("嗯")
    truncate.assert_not_awaited()


@pytest.mark.asyncio
async def test_max_pause_rollback_never_truncates(monkeypatch: pytest.MonkeyPatch) -> None:
    """The patience cap resumes the reply, so its transcript must stay whole.

    Gate-on-only code (`=1`) since D-032 flipped the default off.
    """
    monkeypatch.setenv("REALTIME_SOLO_NAME_GATE", "1")
    monkeypatch.setenv("REALTIME_BARGE_MAX_PAUSE_MS", "10")
    h = _truncating_handler()
    truncate = h.connection.conversation.item.truncate
    generation = audio_drain.begin_response()
    audio_drain.note_enqueued(generation, sample_count=12000, sample_rate=24000)
    h._audio_item_id = "item_abc"
    h._audio_item_enqueued_ms = 4000.0  # plenty heard: only the path forbids it
    h._response_done_event.clear()
    h._solo_speech_started()
    await asyncio.sleep(0.05)
    assert h._barge_pending is False
    assert h._barge_paused is False
    truncate.assert_not_awaited()


@pytest.mark.asyncio
async def test_timer_rollback_never_truncates(monkeypatch: pytest.MonkeyPatch) -> None:
    """No transcript ever arrived: the reply plays on, whole."""
    monkeypatch.setenv("REALTIME_BARGE_ROLLBACK_TIMEOUT_S", "0.01")
    h = _truncating_handler()
    truncate = h.connection.conversation.item.truncate
    generation = audio_drain.begin_response()
    audio_drain.note_enqueued(generation, sample_count=12000, sample_rate=24000)
    h._audio_item_id = "item_abc"
    h._audio_item_enqueued_ms = 4000.0
    h._response_done_event.clear()
    h._solo_speech_started()
    h._solo_speech_stopped()
    await asyncio.sleep(0.05)
    assert h._barge_pending is False
    truncate.assert_not_awaited()


@pytest.mark.asyncio
async def test_transcription_failure_rollback_never_truncates() -> None:
    """A failed transcript decides nothing — and decides nothing to delete either."""
    h = _truncating_handler()
    truncate = h.connection.conversation.item.truncate
    generation = audio_drain.begin_response()
    audio_drain.note_enqueued(generation, sample_count=12000, sample_rate=24000)
    h._audio_item_id = "item_abc"
    h._audio_item_enqueued_ms = 4000.0
    h._response_done_event.clear()
    h._solo_speech_started()
    h._resolve_solo_barge_failure("item_abc")
    assert h._barge_pending is False
    truncate.assert_not_awaited()


@pytest.mark.asyncio
async def test_zero_heard_ms_skips_truncate() -> None:
    """If nothing measurably played, do not send a truncate the server may reject."""
    h = _truncating_handler()
    truncate = h.connection.conversation.item.truncate
    generation = audio_drain.begin_response()
    h._audio_item_id = "item_abc"
    h._audio_item_enqueued_ms = 400.0
    audio_drain.note_enqueued(generation, sample_count=9600, sample_rate=24000)  # all outstanding
    h._response_done_event.clear()
    h._solo_speech_started()
    await h._commit_solo_barge()
    truncate.assert_not_awaited()
    h.on_external_interrupt()  # cleanup: the watchdog task is real


@pytest.mark.asyncio
async def test_commit_truncates_the_paused_item_not_a_newer_one() -> None:
    """The tail that was dropped belongs to the item that was PAUSED.

    By commit time `_audio_item_id` can already name a newer response's item;
    truncating that one would delete text the user is still hearing.
    """
    h = _truncating_handler()
    truncate = h.connection.conversation.item.truncate
    generation = audio_drain.begin_response()
    h._audio_item_id = "item_paused"
    h._audio_item_enqueued_ms = 2000.0
    audio_drain.note_enqueued(generation, sample_count=12000, sample_rate=24000)
    h._response_done_event.clear()
    h._solo_speech_started()
    # A newer response started speaking while the decision was pending.
    h._audio_item_id = "item_newer"
    h._audio_item_enqueued_ms = 50.0
    await h._commit_solo_barge()
    assert truncate.await_args.kwargs["item_id"] == "item_paused"
    h.on_external_interrupt()  # cleanup: the watchdog task is real


@pytest.mark.asyncio
async def test_commit_cancels_and_truncates_when_a_newer_response_is_live() -> None:
    """D-032 T2b: the newer response is cancelled, and the paused item still loses its tail.

    Inverts the pre-D-032 pin, which expected no cancel. The live audio item is
    the paused one here, so there is exactly one truncate.
    """
    h = _truncating_handler()
    truncate = h.connection.conversation.item.truncate
    generation = audio_drain.begin_response()
    h._audio_item_id = "item_paused"
    h._audio_item_enqueued_ms = 2000.0
    audio_drain.note_enqueued(generation, sample_count=12000, sample_rate=24000)
    h._response_done_event.clear()
    h._solo_speech_started()
    h._active_response_id = "resp_answer"  # a different, newer response is live
    await h._commit_solo_barge()
    h.connection.response.cancel.assert_awaited_once()
    truncate.assert_awaited_once()
    assert truncate.await_args.kwargs["item_id"] == "item_paused"
    h.on_external_interrupt()  # cleanup: the watchdog task is real


@pytest.mark.asyncio
async def test_commit_truncates_both_the_paused_item_and_the_live_one() -> None:
    """D-032 T2b: two items lost audio, so two items lose their unheard tail.

    The paused item is cut at the position stashed when the pause began; the
    item a newer response was speaking is cut at its own heard position,
    measured BEFORE the flush zeroes the drain counters behind it.
    """
    h = _truncating_handler()
    truncate = h.connection.conversation.item.truncate
    generation = audio_drain.begin_response()
    h._audio_item_id = "item_paused"
    h._audio_item_enqueued_ms = 2000.0
    audio_drain.note_enqueued(generation, sample_count=12000, sample_rate=24000)  # 500 ms outstanding
    h._response_done_event.clear()
    h._solo_speech_started()
    # A newer response took the floor while the decision was pending.
    h._active_response_id = "resp_newer"
    h._audio_item_id = "item_live"
    h._audio_item_enqueued_ms = 2000.0

    await h._commit_solo_barge()

    h.connection.response.cancel.assert_awaited_once()
    truncated = [call.kwargs["item_id"] for call in truncate.await_args_list]
    assert truncated == ["item_paused", "item_live"]
    assert all(0 < call.kwargs["audio_end_ms"] <= 1200 for call in truncate.await_args_list)
    h.on_external_interrupt()  # cleanup: the watchdog task is real


@pytest.mark.asyncio
async def test_late_interrupt_truncates_the_live_item() -> None:
    """The late path has no pause, so it measures the item that is speaking now.

    `_clear_queue` gets its production semantics here on purpose: in the app it
    IS `console.clear_audio_queue`, which runs `on_external_interrupt()` (the
    item id goes) and `audio_drain.note_cleared()` (the drain counters go). With
    a bare mock, a capture moved below the flush would still pass while
    production silently lost the truncate.
    """
    h = _truncating_handler()
    h._clear_queue = _production_flush(h)
    truncate = h.connection.conversation.item.truncate
    generation = audio_drain.begin_response()
    audio_drain.note_enqueued(generation, sample_count=12000, sample_rate=24000)  # 500 ms
    h._audio_item_id = "item_live"
    h._audio_item_enqueued_ms = 2000.0
    h._response_done_event.clear()
    await h._late_solo_interrupt()
    truncate.assert_awaited_once()
    kwargs = truncate.await_args.kwargs
    assert kwargs["item_id"] == "item_live"
    assert kwargs["content_index"] == 0
    assert 0 < kwargs["audio_end_ms"] <= 1200
    h.on_external_interrupt()  # cleanup: the watchdog task is real


def test_the_commit_truncate_runs_below_the_watchdog_arm() -> None:
    """Source ordering the behavioral tests cannot see (fix round 1, finding 3).

    The truncate is the only `await` between the flush and the watchdog arm.
    Run above the arm, the receive loop can process a whole short reply —
    `response.created` and `response.done` — inside it; the
    `_barge_response_seen = False` below would then erase the proof that reply
    existed, and the watchdog would ask for a second one. Reachy speaking
    unprompted is a worse failure than a truncate sent a few milliseconds late.
    """
    import inspect

    source = inspect.getsource(HuggingFaceRealtimeHandler._commit_solo_barge)
    assert source.index("self._arm_barge_watchdog()") < source.index("await self._truncate_heard_audio(")
    # D-032 T5: the whole chain, in the order the operator hears it — the reply
    # stops generating, the queued audio goes, the turn's answer is insured,
    # and only then is the server's copy cut at what actually reached the ear.
    assert (
        source.index("await self._cancel_active_response()")
        < source.index("self._clear_queue()")
        < source.index("self._arm_barge_watchdog()")
        < source.index("await self._truncate_heard_audio(")
    )


@pytest.mark.asyncio
async def test_late_interrupt_truncates_the_newer_response_it_cancels() -> None:
    """D-032 T2b: the newer response is cancelled, so its unheard tail must go too.

    Inverts the pre-D-032 pin, which expected no truncate because nothing was
    cancelled.
    """
    h = _truncating_handler()
    truncate = h.connection.conversation.item.truncate
    generation = audio_drain.begin_response()
    audio_drain.note_enqueued(generation, sample_count=12000, sample_rate=24000)  # 500 ms
    h._response_done_event.clear()
    h._barge_resumed_response_id = "resp_old"
    h._active_response_id = "resp_new"
    h._audio_item_id = "item_live"
    h._audio_item_enqueued_ms = 2000.0
    await h._late_solo_interrupt()
    truncate.assert_awaited_once()
    assert truncate.await_args.kwargs["item_id"] == "item_live"
    h.on_external_interrupt()  # cleanup: the watchdog task is real


@pytest.mark.asyncio
async def test_truncate_survives_a_refused_stale_item() -> None:
    """A finished/deleted item is a benign race: log at debug, never raise."""
    h = _truncating_handler()
    h.connection.conversation.item.truncate.side_effect = RuntimeError("no such item")
    await h._truncate_heard_audio("item_gone", 900)


@pytest.mark.asyncio
async def test_truncate_is_skipped_without_a_connection() -> None:
    """Session teardown can leave the paths above running with no socket."""
    h = _truncating_handler()
    truncate = h.connection.conversation.item.truncate
    h.connection = None
    await h._truncate_heard_audio("item_abc", 900)
    truncate.assert_not_awaited()


@pytest.mark.asyncio
async def test_heard_audio_ms_subtracts_the_device_buffer() -> None:
    """Audio handed to the sink can still be inside the device buffer, unheard."""
    h = _truncating_handler()
    generation = audio_drain.begin_response()
    h._audio_item_id = "item_abc"
    h._audio_item_enqueued_ms = 2000.0
    audio_drain.note_enqueued(generation, sample_count=48000, sample_rate=24000)  # 2 s
    audio_drain.note_chunk(sample_count=24000, sample_rate=24000)  # 1 s to the sink
    # 2000 enqueued − 1000 outstanding − ~1000 device-buffered − 300 slack < 0.
    assert h._heard_audio_ms() == 0


def test_heard_audio_ms_is_zero_without_an_item() -> None:
    """No item id means nothing addressable to truncate."""
    h = _truncating_handler()
    h._audio_item_enqueued_ms = 5000.0
    assert h._heard_audio_ms() == 0


def test_external_interrupt_forgets_the_audio_item() -> None:
    """A stale id surviving a reconnect must never be truncated in a later session."""
    h = _truncating_handler()
    h._audio_item_id = "item_abc"
    h._audio_item_enqueued_ms = 2000.0
    h._barge_paused_item_id = "item_abc"
    h._barge_paused_heard_ms = 1200
    h.on_external_interrupt()
    assert h._audio_item_id is None
    assert h._audio_item_enqueued_ms == 0.0
    assert h._barge_paused_item_id is None
    assert h._barge_paused_heard_ms == 0


@pytest.mark.asyncio
async def test_resume_clears_the_paused_stash_on_both_branches() -> None:
    """A stash outliving its pause would truncate the wrong item on a later commit."""
    for rolled_back in (True, False):
        h = _truncating_handler()
        _make_audible()
        h._audio_item_id = "item_abc"
        h._audio_item_enqueued_ms = 4000.0
        h._response_done_event.clear()
        h._solo_speech_started()
        assert h._barge_paused_item_id == "item_abc"
        h._resume_playback(rolled_back=rolled_back)
        assert h._barge_paused_item_id is None, rolled_back
        assert h._barge_paused_heard_ms == 0, rolled_back
        h.on_external_interrupt()
        audio_drain.reset()


def _audio_item_probe(
    monkeypatch: pytest.MonkeyPatch, events: tuple[_FakeEvent, ...]
) -> tuple[HuggingFaceRealtimeHandler, list[tuple[Any, float]]]:
    """Replay *events* and sample the audio-item tally at each `response.done`.

    Sampled mid-session on purpose: the session teardown runs
    `on_external_interrupt()`, which clears both fields, so reading them after
    `_run_realtime_session` returns would pass no matter what the loop did.
    """
    seen: list[tuple[Any, float]] = []
    _quiet_session(monkeypatch)
    monkeypatch.setattr(hf_mod, "on_response_audio", lambda sample_count, sample_rate: None)
    monkeypatch.setattr(
        hf_mod,
        "on_assistant_turn_ended",
        lambda _deps, _live=None: seen.append((handler._audio_item_id, handler._audio_item_enqueued_ms)),
    )
    handler = _loop_handler(events)
    return handler, seen


# 240 frames at the base handler's 16 kHz = 15 ms of audio per delta.
_DELTA_15MS = base64.b64encode(b"\x00\x00" * 240).decode("ascii")


@pytest.mark.asyncio
async def test_the_loop_accumulates_enqueued_ms_per_audio_item(monkeypatch: pytest.MonkeyPatch) -> None:
    """Per-item accounting: a new item id restarts the tally, deltas add to it."""
    handler, seen = _audio_item_probe(
        monkeypatch,
        (
            _FakeEvent("response.output_audio.delta", delta=_DELTA_15MS, item_id="item_1"),
            _FakeEvent("response.output_audio.delta", delta=_DELTA_15MS, item_id="item_1"),
            _FakeEvent("response.done", response=SimpleNamespace(id="resp_A")),
            _FakeEvent("response.output_audio.delta", delta=_DELTA_15MS, item_id="item_2"),
            _FakeEvent("response.done", response=SimpleNamespace(id="resp_A")),
        ),
    )
    await handler._run_realtime_session()
    assert seen == [("item_1", pytest.approx(30.0, abs=0.01)), ("item_2", pytest.approx(15.0, abs=0.01))]


@pytest.mark.asyncio
async def test_the_loop_skips_accounting_for_an_id_less_audio_delta(monkeypatch: pytest.MonkeyPatch) -> None:
    """A delta naming no item must not inflate the live item's tally (D-028 §5).

    `_audio_item_enqueued_ms` is the numerator of the `conversation.item.truncate`
    position, and an `audio_end_ms` above the item's real duration is a server
    error — the one failure mode this accounting has. Frames we cannot attribute
    are therefore dropped from the per-item total (they still play, and still
    count toward the drain accounting): under-counting only under-truncates,
    which is the safe direction.
    """
    handler, seen = _audio_item_probe(
        monkeypatch,
        (
            _FakeEvent("response.output_audio.delta", delta=_DELTA_15MS, item_id="item_1"),
            _FakeEvent("response.output_audio.delta", delta=_DELTA_15MS),
            _FakeEvent("response.output_audio.delta", delta=_DELTA_15MS, item_id="item_1"),
            _FakeEvent("response.done", response=SimpleNamespace(id="resp_A")),
        ),
    )
    await handler._run_realtime_session()
    # Two attributable 15 ms deltas, not three: the id-less one is skipped, and
    # it neither reset the item nor took ownership of the tally.
    assert seen == [("item_1", pytest.approx(30.0, abs=0.01))]


@pytest.mark.asyncio
async def test_the_loop_keeps_the_audio_item_across_a_new_response(monkeypatch: pytest.MonkeyPatch) -> None:
    """`response.created` is NOT the moment the previous reply stops being heard.

    Fix round 1, finding 2. A tool turn creates its follow-up response while the
    first reply's PCM is still coming out of the speaker, so resetting the item
    there would leave a barge in that window with nothing to truncate.
    """
    handler, seen = _audio_item_probe(
        monkeypatch,
        (
            _FakeEvent("response.output_audio.delta", delta=_DELTA_15MS, item_id="item_1"),
            _FakeEvent("response.created", response=SimpleNamespace(id="resp_B")),
            _FakeEvent("response.done", response=SimpleNamespace(id="resp_B")),
        ),
    )
    await handler._run_realtime_session()
    assert seen == [("item_1", pytest.approx(15.0, abs=0.01))]


@pytest.mark.asyncio
async def test_the_loop_truncates_across_a_tool_follow_up_response(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole reachable hole finding 2 closed, end to end through the loop.

    R1 speaks item A and finishes; the tool result asks for R2, whose
    `response.created` lands while A is still audible; the user barges in and
    says 「停」. The truncate must still name item A — a reset at
    `response.created` would have stashed `(None, 0)` and sent nothing, leaving
    every unheard word of A in the model's context.
    """
    _quiet_session(monkeypatch)
    monkeypatch.setattr(hf_mod, "on_response_audio", lambda sample_count, sample_rate: None)
    sent: list[tuple[str, int]] = []

    async def _record(_self: HuggingFaceRealtimeHandler, item_id: str, audio_end_ms: int) -> None:
        sent.append((item_id, audio_end_ms))

    monkeypatch.setattr(hf_mod.HuggingFaceRealtimeHandler, "_truncate_heard_audio", _record)

    # 100 deltas x 15 ms = 1500 ms of item A, none of it left outstanding
    # (`on_response_audio` is stubbed out), so heard = 1500 − 300 slack.
    deltas = tuple(
        _FakeEvent("response.output_audio.delta", delta=_DELTA_15MS, item_id="item_A") for _ in range(100)
    )
    handler = _loop_handler(
        deltas
        + (
            _FakeEvent("response.done", response=SimpleNamespace(id="resp_1")),
            _FakeEvent("response.created", response=SimpleNamespace(id="resp_2")),
            _FakeEvent("input_audio_buffer.speech_started"),
            _FakeEvent(
                "conversation.item.input_audio_transcription.completed",
                item_id="user_1",
                transcript="停",
            ),
        )
    )
    await handler._run_realtime_session()
    assert sent == [("item_A", 1200)]


# --- the answer gate's effect on the barge lifecycle (2026-08-31, Task 2 fixes)


@pytest.mark.asyncio
async def test_the_loop_closes_the_barge_lifecycle_on_a_denied_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A denied turn decides its utterance too, so it must clear what it owned.

    Review item 1. The accept path clears `_barge_resumed_response_id` and
    `_barge_late_eligible`; before this fix the answer gate's `continue` jumped
    straight over that clear. A stale resumed id then makes the NEXT
    `_late_solo_interrupt`'s `answer_already_live` guard suppress a real
    「瑞奇停」, and stale late-eligibility credits a later utterance with an
    onset over a talking robot that it never had.

    Observed from INSIDE the loop, through the music hook the deny branch calls
    right after the clear: session teardown runs `on_external_interrupt()`,
    which clears both fields anyway, so a post-session assertion would pass
    with or without the fix.
    """
    _quiet_session(monkeypatch)
    holder: dict[str, HuggingFaceRealtimeHandler] = {}
    baseline: list[int] = []
    seen: list[tuple[Any, Any, Any]] = []

    def _plant(self: HuggingFaceRealtimeHandler, item_id: str | None = None) -> None:
        self._response_done_event.set()
        self._conversation_mode = ConversationMode.ONE_ON_ONE
        self._barge_late_eligible = True
        self._barge_resumed_response_id = "resp_rolled_back"
        # The session's own boot greeting is already queued by now; the claim
        # below is that the DENIED TURN adds nothing to it.
        baseline.append(self._pending_responses.qsize())

    monkeypatch.setattr(hf_mod.HuggingFaceRealtimeHandler, "_solo_speech_started", _plant)
    monkeypatch.setattr(
        hf_mod,
        "on_turn_without_response",
        lambda _deps: seen.append(
            (
                holder["h"]._barge_resumed_response_id,
                holder["h"]._barge_late_eligible,
                holder["h"]._pending_responses.qsize(),
            )
        ),
    )
    handler = _loop_handler(
        (
            _FakeEvent("input_audio_buffer.speech_started"),
            # A backchannel: substantive-nothing, so the one-on-one gate denies it.
            _FakeEvent(
                "conversation.item.input_audio_transcription.completed",
                transcript="嗯嗯",
                item_id="item_1",
            ),
        )
    )
    holder["h"] = handler

    await handler._run_realtime_session()

    assert seen == [(None, False, baseline[0])], "the denied turn left barge lifecycle state behind"


@pytest.mark.asyncio
async def test_the_loop_stands_the_watchdog_down_on_a_denied_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`SOLO_NAME_GATE=0` + `ONE_ON_ONE_ANSWER_GATE=name_only` must still stay silent.

    Review item 3. With the name gate off, sustained speech alone confirms a
    barge and arms the repair watchdog; name-only answering then denies that
    same turn an answer. Without the stand-down the watchdog speaks 1.5 s later
    — exactly the unprompted reply the gate refused.
    """
    monkeypatch.setenv("REALTIME_SOLO_NAME_GATE", "0")
    monkeypatch.setenv("REALTIME_ONE_ON_ONE_ANSWER_GATE", "name_only")
    _quiet_session(monkeypatch)
    holder: dict[str, HuggingFaceRealtimeHandler] = {}
    baseline: list[int] = []
    seen: list[Any] = []

    def _plant(self: HuggingFaceRealtimeHandler, item_id: str | None = None) -> None:
        self._response_done_event.set()
        self._conversation_mode = ConversationMode.ONE_ON_ONE
        # Stand in for the confirmed barge that armed the repair watchdog.
        self._barge_response_seen = False
        self._arm_barge_watchdog()
        baseline.append(self._pending_responses.qsize())

    monkeypatch.setattr(hf_mod.HuggingFaceRealtimeHandler, "_solo_speech_started", _plant)
    monkeypatch.setattr(
        hf_mod,
        "on_turn_without_response",
        lambda _deps: seen.append((holder["h"]._barge_watchdog_task, holder["h"]._pending_responses.qsize())),
    )
    handler = _loop_handler(
        (
            _FakeEvent("input_audio_buffer.speech_started"),
            # Substantive but unaddressed: accepted by the barge gate with the
            # name gate off, denied an answer by name-only answering.
            _FakeEvent(
                "conversation.item.input_audio_transcription.completed",
                transcript="我們晚餐要吃什麼呢",
                item_id="item_1",
            ),
        )
    )
    holder["h"] = handler

    await handler._run_realtime_session()

    assert seen == [(None, baseline[0])], "the denied turn left its repair watchdog armed"


@pytest.mark.asyncio
async def test_the_loop_stands_the_watchdog_down_on_an_accepted_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An answered turn asks for its own reply, so its repair watchdog must go.

    Final review, C1. The barge that produced this turn armed the watchdog; the
    accept path then calls `_safe_response_create()` itself. Leave the watchdog
    armed and every one of its guards passes whenever `response.created` takes
    longer than 1.5 s — nothing seen, nothing speaking, the floor quiet — so it
    enqueues a SECOND request and Reachy answers the same sentence twice.

    Observed from INSIDE the loop, at the `record_transcript` seam, which sits
    after the stand-down and before the answer is requested: that is what makes
    the claim independent of `response.created` timing. A post-session
    assertion would prove nothing, because session teardown
    (`_barge_shutdown`) cancels the watchdog either way.
    """
    _quiet_session(monkeypatch)
    holder: dict[str, HuggingFaceRealtimeHandler] = {}
    baseline: list[int] = []
    seen: list[tuple[Any, int]] = []

    def _plant(self: HuggingFaceRealtimeHandler, item_id: str | None = None) -> None:
        # A silent robot, so the late-interrupt path stays out of this test.
        self._response_done_event.set()
        self._conversation_mode = ConversationMode.ONE_ON_ONE
        # Stand in for the confirmed barge that armed the repair watchdog.
        self._barge_response_seen = False
        self._arm_barge_watchdog()
        baseline.append(self._pending_responses.qsize())

    monkeypatch.setattr(hf_mod.HuggingFaceRealtimeHandler, "_solo_speech_started", _plant)
    monkeypatch.setattr(
        hf_mod,
        "record_transcript",
        lambda _deps, _role, _text: seen.append(
            (holder["h"]._barge_watchdog_task, holder["h"]._pending_responses.qsize())
        ),
    )
    handler = _loop_handler(
        (
            _FakeEvent("input_audio_buffer.speech_started"),
            # Substantive, so the open one-on-one answer gate accepts it.
            _FakeEvent(
                "conversation.item.input_audio_transcription.completed",
                transcript="我們晚餐要吃什麼呢",
                item_id="item_1",
            ),
        )
    )
    holder["h"] = handler

    await handler._run_realtime_session()

    assert seen == [(None, baseline[0])], "the accepted turn kept its repair watchdog armed"


@pytest.mark.asyncio
async def test_a_stood_down_watchdog_never_asks_for_a_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    """The seam itself: a cancelled watchdog produces no `response.create`."""
    monkeypatch.setattr(hf_mod, "_BARGE_RESPONSE_WATCHDOG_S", 0.01)
    h = _solo_handler()
    h._barge_response_seen = False
    h._arm_barge_watchdog()
    armed = h._barge_watchdog_task
    assert armed is not None

    h._stand_down_barge_watchdog()

    assert h._barge_watchdog_task is None
    await asyncio.sleep(0.05)
    assert armed.cancelled()
    assert h._pending_responses.qsize() == 0
    # Not a lie about what happened: no response was seen, so the next commit
    # re-arms a watchdog that can still do its job.
    assert h._barge_response_seen is False


# --- one answer per interrupting turn (D-032 T2c) --------------------------


@pytest.mark.asyncio
async def test_the_watchdog_marks_the_utterance_it_answered(monkeypatch: pytest.MonkeyPatch) -> None:
    """The repair request is stamped with the item it answered, not a session flag.

    With the name gate off a sustained-speech commit precedes the turn's own
    transcript, so the watchdog can answer an utterance whose
    `transcription.completed` is still to come. The marker is what lets that
    completed transcript recognise its own answer — and only its own.
    """
    monkeypatch.setattr(hf_mod, "_BARGE_RESPONSE_WATCHDOG_S", 0.01)
    h = _solo_handler()
    h._barge_utterance_item_id = "item_1"
    h._barge_response_seen = False
    h._arm_barge_watchdog()
    assert h._barge_watchdog_task is not None

    await asyncio.wait_for(h._barge_watchdog_task, timeout=1.0)

    assert h._pending_responses.qsize() == 1
    assert h._barge_watchdog_answered_item == "item_1"


@pytest.mark.asyncio
async def test_arming_the_watchdog_forgets_the_previous_utterances_answer() -> None:
    """A new pause is a new turn: the previous turn's marker must not survive it."""
    h = _solo_handler()
    h._barge_watchdog_answered_item = "item_old"
    h._arm_barge_watchdog()
    assert h._barge_watchdog_answered_item is None
    h.on_external_interrupt()


def test_external_interrupt_clears_the_watchdog_answer_marker() -> None:
    """A session reset owns no item id, so it drops the whole marker."""
    h = _solo_handler()
    h._barge_watchdog_answered_item = "item_1"
    h._barge_utterance_item_id = "item_1"

    h.on_external_interrupt()

    assert h._barge_watchdog_answered_item is None
    assert h._barge_utterance_item_id is None


async def _run_watchdog_answered_turn(
    monkeypatch: pytest.MonkeyPatch,
    *,
    marker: str | None,
    response_seen: bool,
    item_id: str = "item_1",
) -> tuple[int, HuggingFaceRealtimeHandler]:
    """Replay speech-start → accepted transcript; count the responses THIS turn asked for."""
    monkeypatch.setenv("REALTIME_COMMIT_HOLDOFF_MS", "0")
    _quiet_session(monkeypatch)
    created: list[dict[str, Any]] = []
    baseline: list[int] = []

    async def _count(self: HuggingFaceRealtimeHandler, *, cycle: Any = None, **kwargs: Any) -> None:
        created.append(kwargs)

    def _plant(self: HuggingFaceRealtimeHandler, item_id: str | None = None) -> None:
        # A silent robot: the late-interrupt path stays out of this test.
        self._response_done_event.set()
        self._conversation_mode = ConversationMode.ONE_ON_ONE
        # Stand in for a watchdog that already fired for `marker`'s utterance.
        self._barge_watchdog_answered_item = marker
        self._barge_response_seen = response_seen
        baseline.append(len(created))

    monkeypatch.setattr(hf_mod.HuggingFaceRealtimeHandler, "_solo_speech_started", _plant)
    monkeypatch.setattr(hf_mod.HuggingFaceRealtimeHandler, "_safe_response_create", _count)
    handler = _loop_handler(
        (
            _FakeEvent("input_audio_buffer.speech_started", item_id=item_id),
            _FakeEvent(
                "conversation.item.input_audio_transcription.completed",
                transcript="我們晚餐要吃什麼呢",
                item_id=item_id,
            ),
        )
    )
    await handler._run_realtime_session()
    return len(created) - baseline[0], handler


@pytest.mark.asyncio
async def test_a_watchdog_answered_turn_is_not_answered_twice(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Round 1, finding 1: the watchdog answered this utterance, so the accept path must not.

    Gate off, a sustained-speech commit precedes the transcript; the watchdog
    fires 1.5 s later and asks for the reply. When the transcript then lands
    and the answer gate accepts it, requesting a second response would make
    Reachy answer the same sentence twice.
    """
    with caplog.at_level("INFO"):
        requested, handler = await _run_watchdog_answered_turn(monkeypatch, marker="item_1", response_seen=True)
    assert requested == 0
    assert "accepted turn already answered by the barge watchdog" in caplog.text


@pytest.mark.asyncio
async def test_a_watchdog_request_the_server_never_created_is_asked_for_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Round 2, finding 4: `_safe_response_create` only enqueues, so `response.created` is the proof.

    Without a `response.created` since the arm there is no evidence the
    watchdog's request produced anything, and a silent turn is worse than the
    narrow overlap the sender loop's one-active-response handling covers.
    """
    requested, _handler = await _run_watchdog_answered_turn(monkeypatch, marker="item_1", response_seen=False)
    assert requested == 1


@pytest.mark.asyncio
async def test_another_utterances_watchdog_answer_does_not_silence_this_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Round 2, finding 1: the marker is per input item, not a session bool.

    `transcription.completed` can land after the NEXT utterance has started, so
    a session-wide "already answered" flag would swallow the answer to a turn
    the watchdog never spoke for.
    """
    requested, _handler = await _run_watchdog_answered_turn(monkeypatch, marker="item_other", response_seen=True)
    assert requested == 1


async def _marker_at_turn_exit(
    monkeypatch: pytest.MonkeyPatch, *, event: _FakeEvent
) -> list[tuple[str, str | None]]:
    """Replay speech-start → *event*, reporting the marker as the turn leaves the loop.

    Observed from INSIDE the loop, at `_answer_owed_holdoff`, which both exits
    call last: session teardown runs `on_external_interrupt()` and would clear
    the marker anyway, so a post-session assertion would pass either way.
    """
    _quiet_session(monkeypatch)
    seen: list[tuple[str, str | None]] = []

    def _plant(self: HuggingFaceRealtimeHandler, item_id: str | None = None) -> None:
        self._response_done_event.set()
        self._conversation_mode = ConversationMode.ONE_ON_ONE
        self._barge_watchdog_answered_item = "item_1"

    async def _spy(self: HuggingFaceRealtimeHandler, reason: str) -> bool:
        seen.append((reason, self._barge_watchdog_answered_item))
        return False

    monkeypatch.setattr(hf_mod.HuggingFaceRealtimeHandler, "_solo_speech_started", _plant)
    monkeypatch.setattr(hf_mod.HuggingFaceRealtimeHandler, "_answer_owed_holdoff", _spy)
    handler = _loop_handler((_FakeEvent("input_audio_buffer.speech_started", item_id="item_1"), event))
    await handler._run_realtime_session()
    return seen


@pytest.mark.asyncio
async def test_an_empty_transcript_releases_the_watchdog_answer_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Round 2, finding 3: the marker must not outlive the turn it describes."""
    seen = await _marker_at_turn_exit(
        monkeypatch,
        event=_FakeEvent(
            "conversation.item.input_audio_transcription.completed", transcript="", item_id="item_1"
        ),
    )
    assert seen == [("empty transcript", None)]


@pytest.mark.asyncio
async def test_a_failed_transcription_releases_the_watchdog_answer_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same, on the exit where no transcript will ever arrive."""
    seen = await _marker_at_turn_exit(
        monkeypatch,
        event=_FakeEvent("conversation.item.input_audio_transcription.failed", item_id="item_1"),
    )
    assert seen == [("transcription failed", None)]


def test_taking_the_watchdog_answer_is_scoped_to_one_item() -> None:
    """The marker is consumed by its own turn and by nobody else's.

    The loop assertions above can only count requests: session teardown runs
    `on_external_interrupt()`, which clears the marker either way, so the
    consume/leave-alone half of the contract is pinned here at the seam.
    """
    h = _solo_handler()
    h._barge_watchdog_answered_item = "item_1"

    assert h._take_barge_watchdog_answer("item_2") is False
    assert h._barge_watchdog_answered_item == "item_1", "another turn's marker is untouched"
    assert h._take_barge_watchdog_answer(None) is False, "an event with no id can match nothing"
    assert h._take_barge_watchdog_answer("item_1") is True
    assert h._barge_watchdog_answered_item is None, "consumed, not left to rot"
    assert h._take_barge_watchdog_answer("item_1") is False


# --- T2b: an interruption stops whatever speaks, except this turn's own repair


@pytest.mark.asyncio
async def test_the_loop_does_not_late_interrupt_this_turns_watchdog_answer(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """D-032 T2b exception: the watchdog's reply for THIS utterance is its answer.

    A sustained-speech commit leaves `pause_committed` False by the time the
    transcript lands, so without this guard the late path would cancel the very
    reply the barge watchdog asked for on behalf of the same turn.
    """
    with caplog.at_level("INFO"):
        fired, _ = await _run_late_path(
            monkeypatch,
            transcript="我們晚餐要吃什麼呢這麼晚了",
            watchdog_answered="item_1",
            response_seen=True,
        )
    assert fired == []
    assert "late solo interrupt held: the barge watchdog already answered this turn" in caplog.text


@pytest.mark.asyncio
async def test_the_loop_late_interrupts_another_turns_watchdog_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exception is per item: a repair for a DIFFERENT utterance is fair game."""
    fired, _ = await _run_late_path(
        monkeypatch,
        transcript="我們晚餐要吃什麼呢這麼晚了",
        watchdog_answered="item_other",
        response_seen=True,
    )
    assert fired == ["late"]


# --- T2b stale-tool rule (Codex round 2, finding 5) ------------------------


def _tool_probe(handler: HuggingFaceRealtimeHandler) -> list[str]:
    """Replace the tool manager with a recorder, so no real tool ever runs."""
    started: list[str] = []

    async def _start_tool(*, call_id: str, tool_call_routine: Any, is_idle_tool_call: bool) -> Any:
        started.append(call_id)
        return SimpleNamespace(tool_id=f"tool_{call_id}")

    handler.tool_manager = SimpleNamespace(
        start_up=lambda tool_callbacks=None: None,
        shutdown=AsyncMock(),
        start_tool=_start_tool,
    )
    return started


async def _run_tool_call(
    monkeypatch: pytest.MonkeyPatch, *, response_id: str, cancelled: str
) -> tuple[list[str], HuggingFaceRealtimeHandler]:
    """Replay a tool call whose response may or may not have been cancelled."""
    _quiet_session(monkeypatch)
    hooked: list[str] = []
    monkeypatch.setattr(hf_mod, "on_tool_call_started", lambda call_id: hooked.append(call_id))

    def _plant(self: HuggingFaceRealtimeHandler, item_id: str | None = None) -> None:
        self._cancelled_response_ids.append(cancelled)

    monkeypatch.setattr(hf_mod.HuggingFaceRealtimeHandler, "_solo_speech_started", _plant)
    handler = _loop_handler(
        (
            _FakeEvent("input_audio_buffer.speech_started", item_id="item_1"),
            _FakeEvent(
                "response.function_call_arguments.done",
                response_id=response_id,
                name="look_around",
                arguments="{}",
                call_id="call_1",
            ),
        )
    )
    started = _tool_probe(handler)
    await handler._run_realtime_session()
    assert hooked == started, "the music hook and the tool manager must agree"
    return started, handler


@pytest.mark.asyncio
async def test_a_tool_call_from_a_cancelled_response_starts_nothing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Round 2, finding 5: cancelling a response must not leave its tools to run.

    The follow-up that tool call belongs to was cancelled by the barge, so
    running it would post an output nobody asked for, start a music tool phase
    that never ends, and leave `_in_flight_tool_calls` holding a call id whose
    turn is over.
    """
    with caplog.at_level("INFO"):
        started, handler = await _run_tool_call(monkeypatch, response_id="resp_dead", cancelled="resp_dead")
    assert started == []
    assert handler._in_flight_tool_calls == set()
    assert handler._tool_batch_needs_response is False
    assert "ignoring tool call from cancelled response resp_dead" in caplog.text


@pytest.mark.asyncio
async def test_a_tool_call_from_a_live_response_still_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    """The filter is scoped to the cancelled ids; every other tool call runs as before."""
    started, handler = await _run_tool_call(monkeypatch, response_id="resp_live", cancelled="resp_dead")
    assert started == ["call_1"]
    assert "call_1" in handler._in_flight_tool_calls


# --- T2d: late eligibility per input item (Codex round 1, finding 4) -------


@pytest.mark.asyncio
async def test_out_of_order_completions_each_use_their_own_eligibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`transcription.completed` can land after the NEXT utterance already started.

    One session flag would then describe the wrong turn: here the second
    utterance began in silence (not eligible) and its transcript lands FIRST,
    so a session flag would be False by the time the first utterance's
    transcript — the one that really did begin over a talking robot — arrives,
    and the reply it interrupted would keep playing.

    Observed in order at the `record_transcript` seam, which runs just after the
    late block for each turn.
    """
    _quiet_session(monkeypatch)
    seen: list[str] = []

    def _plant(self: HuggingFaceRealtimeHandler, item_id: str | None = None) -> None:
        self._response_done_event.clear()  # audible
        self._conversation_mode = ConversationMode.ONE_ON_ONE
        self._active_response_id = "resp_A"
        # The real stamping: only `item_1` began over a talking robot.
        self._stamp_late_eligible(item_id, item_id == "item_1")

    async def _record(self: HuggingFaceRealtimeHandler) -> None:
        seen.append("late")

    monkeypatch.setattr(hf_mod.HuggingFaceRealtimeHandler, "_solo_speech_started", _plant)
    monkeypatch.setattr(hf_mod.HuggingFaceRealtimeHandler, "_late_solo_interrupt", _record)
    monkeypatch.setattr(hf_mod, "record_transcript", lambda _deps, _role, text: seen.append(f"said:{text}"))
    handler = _loop_handler(
        (
            _FakeEvent("input_audio_buffer.speech_started", item_id="item_1"),
            _FakeEvent("input_audio_buffer.speech_started", item_id="item_2"),
            _FakeEvent(
                "conversation.item.input_audio_transcription.completed",
                transcript="第二句話從安靜裡開始的",
                item_id="item_2",
            ),
            _FakeEvent(
                "conversation.item.input_audio_transcription.completed",
                transcript="第一句話蓋在回答上面",
                item_id="item_1",
            ),
        )
    )

    await handler._run_realtime_session()

    assert seen == ["said:第二句話從安靜裡開始的", "late", "said:第一句話蓋在回答上面"]


def test_late_eligibility_falls_back_to_the_session_flag_without_an_id() -> None:
    """An event with no item id still gets an answer: the last onset's own verdict."""
    h = _solo_handler()
    h._stamp_late_eligible(None, True)
    assert h._take_late_eligible(None) is True
    h._stamp_late_eligible(None, False)
    assert h._take_late_eligible("item_unknown") is False, "an unstamped id falls back too"


def test_late_eligibility_is_popped_by_its_own_item() -> None:
    """One entry per utterance, consumed by that utterance and nobody else."""
    h = _solo_handler()
    h._stamp_late_eligible("item_1", True)
    h._stamp_late_eligible("item_2", False)
    assert h._take_late_eligible("item_2") is False
    assert h._take_late_eligible("item_1") is True
    assert h._barge_late_eligibles == {}


def test_late_eligibility_stamps_stay_bounded() -> None:
    """Transcripts that never arrive must not grow the map without bound."""
    h = _solo_handler()
    for index in range(hf_mod._TURN_MODE_MAX_ITEMS + 5):
        h._stamp_late_eligible(f"item_{index}", True)
    assert len(h._barge_late_eligibles) == hf_mod._TURN_MODE_MAX_ITEMS


def test_external_interrupt_clears_every_late_eligibility_stamp() -> None:
    """Round 2, finding 6: the session-reset path owns no item id, so it drops them all."""
    h = _solo_handler()
    h._stamp_late_eligible("item_1", True)

    h.on_external_interrupt()

    assert h._barge_late_eligibles == {}
    assert h._barge_late_eligible is False


# --- T4: name the reason a late interrupt was declined ---------------------


@pytest.mark.asyncio
async def test_a_declined_late_interrupt_names_a_silent_robot(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """RCA Finding 3's open case: the journal could not say which guard refused.

    A committed turn that began over a talking robot and is NOT honoured now
    logs one line naming both inputs — here the reply drained before the
    transcript landed, so there was nothing left to interrupt.
    """
    with caplog.at_level("INFO"):
        fired, _ = await _run_late_path(monkeypatch, audible=False, active_id=None)
    assert fired == []
    assert "late solo interrupt declined (audible=False, verdict=substantive)" in caplog.text


@pytest.mark.asyncio
async def test_a_declined_late_interrupt_names_an_unaddressed_verdict(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Gate ON (`=1`): audible, eligible, but the verdict refused — say so."""
    monkeypatch.setenv("REALTIME_SOLO_NAME_GATE", "1")
    with caplog.at_level("INFO"):
        fired, _ = await _run_late_path(monkeypatch, transcript="我們晚餐要吃什麼呢這麼晚了")
    assert fired == []
    assert "late solo interrupt declined (audible=True, verdict=unaddressed)" in caplog.text


@pytest.mark.asyncio
async def test_a_backchannel_declines_at_the_answer_gate_and_says_so(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Codex round 1, finding 5: in one-on-one a backchannel exits BEFORE the late block.

    The line therefore has to be emitted from the answer-gate denial too, or
    the most common declined turn would still leave no evidence.
    """
    with caplog.at_level("INFO"):
        fired, _ = await _run_late_path(monkeypatch, transcript="嗯")
    assert fired == []
    assert "late solo interrupt declined (audible=True, verdict=backchannel)" in caplog.text


@pytest.mark.asyncio
async def test_an_empty_transcript_declines_with_verdict_empty(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The empty-transcript exit is the third `continue`; it names itself."""
    with caplog.at_level("INFO"):
        fired, _ = await _run_late_path(monkeypatch, transcript="")
    assert fired == []
    assert "late solo interrupt declined (audible=True, verdict=empty)" in caplog.text


@pytest.mark.asyncio
async def test_a_turn_that_began_in_silence_logs_no_declined_line(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """One line per turn that could have interrupted, and none for any other."""
    with caplog.at_level("INFO"):
        fired, _ = await _run_late_path(monkeypatch, eligible=False)
    assert fired == []
    assert "late solo interrupt declined" not in caplog.text


@pytest.mark.asyncio
async def test_a_refused_truncate_is_visible_in_the_journal(caplog: pytest.LogCaptureFixture) -> None:
    """Codex round 1, finding 6: context preservation is best-effort, so say when it failed.

    A swallowed refusal at DEBUG is exactly the case where the unheard tail
    survives in the model's context and nothing in the journal explains it.
    """
    h = _truncating_handler()
    h.connection.conversation.item.truncate.side_effect = RuntimeError("no such item")
    with caplog.at_level("INFO"):
        await h._truncate_heard_audio("item_abc", 1200)
    assert "conversation.item.truncate refused: no such item" in caplog.text


@pytest.mark.asyncio
async def test_a_committed_barge_still_emits_the_turn_and_asks_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """D-032 T5, the commit path end to end on the shipped default.

    A plain sentence over a talking robot commits the pause, and the turn that
    interrupted is then an ordinary accepted turn: its transcript is emitted
    and recorded, and exactly ONE response is requested for it — the commit
    arms a repair watchdog, and a second request here is Reachy answering the
    same sentence twice.
    """
    monkeypatch.setenv("REALTIME_COMMIT_HOLDOFF_MS", "0")
    _quiet_session(monkeypatch)
    created: list[dict[str, Any]] = []
    said: list[str] = []
    baseline: list[int] = []

    async def _count(self: HuggingFaceRealtimeHandler, *, cycle: Any = None, **kwargs: Any) -> None:
        created.append(kwargs)

    def _plant(self: HuggingFaceRealtimeHandler, item_id: str | None = None) -> None:
        self._response_done_event.clear()  # a reply is speaking
        self._conversation_mode = ConversationMode.ONE_ON_ONE
        self._active_response_id = "resp_reply"
        # The pause `_solo_speech_started` would have opened.
        self._barge_pending = True
        self._barge_paused = True
        self._barge_paused_response_id = "resp_reply"
        self._stamp_late_eligible(item_id, True)
        baseline.append(len(created))

    monkeypatch.setattr(hf_mod.HuggingFaceRealtimeHandler, "_solo_speech_started", _plant)
    monkeypatch.setattr(hf_mod.HuggingFaceRealtimeHandler, "_safe_response_create", _count)
    monkeypatch.setattr(hf_mod, "record_transcript", lambda _deps, _role, text: said.append(text))
    handler = _loop_handler(
        (
            _FakeEvent("input_audio_buffer.speech_started", item_id="item_1"),
            _FakeEvent(
                "conversation.item.input_audio_transcription.completed",
                transcript="我們晚餐要吃什麼呢這麼晚了",
                item_id="item_1",
            ),
        )
    )

    with caplog.at_level("INFO"):
        await handler._run_realtime_session()

    assert "solo barge-in confirmed by transcript (substantive," in caplog.text
    assert said == ["我們晚餐要吃什麼呢這麼晚了"], "the interrupting turn is still part of the conversation"
    assert len(created) - baseline[0] == 1, "exactly one response for the interrupting turn"
    assert handler._barge_paused is False


@pytest.mark.asyncio
async def test_a_turn_with_no_watchdog_repair_asks_for_its_own_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The T2c control: no marker at all is the ordinary accepted turn."""
    requested, _handler = await _run_watchdog_answered_turn(monkeypatch, marker=None, response_seen=True)
    assert requested == 1
