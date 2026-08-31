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
from reachy_companion.huggingface_realtime import (
    HuggingFaceRealtimeHandler,
    _barge_confirm_s,
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
    handler._held_audio = deque()
    # `_pause_playback` captures the live response id; a handler built for the
    # emit path alone has no party state, so fill it in without clobbering a
    # caller that set a real id.
    if not hasattr(handler, "_active_response_id"):
        handler._active_response_id = None


def _solo_handler() -> OpenAIRealtimeHandler:
    """Return a solo-mode handler with only the barge-relevant state, no __init__."""
    h = OpenAIRealtimeHandler.__new__(OpenAIRealtimeHandler)
    h._party_mode = False
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
        "REALTIME_BARGE_ROLLBACK_TIMEOUT_S",
        "REALTIME_BARGE_COOLDOWN_MS",
        "REALTIME_PARTY_DEFAULT",
        "REALTIME_MIN_TURN_CHARS",
        "REALTIME_ONSET_RAMP_MS",
        "REALTIME_VAD_TYPE",
        "REALTIME_VAD_SILENCE_DURATION_MS",
        "REALTIME_SOLO_NAME_GATE",
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
async def test_a_commit_never_cancels_the_answer_to_the_barged_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    """The reply the barge asked for must survive the barge that asked for it.

    Review round, finding 4: when the paused reply ends and the server accepts
    the barged turn's own auto-response before the transcript reaches us,
    `_active_response_id` is the ANSWER, not the reply being interrupted.

    Pinned to the pre-name-gate rule: what is under test is the commit path's
    response-id bookkeeping, and 「幫我開燈」 only reaches it while a substantive
    transcript is enough to commit.
    """
    monkeypatch.setenv("REALTIME_SOLO_NAME_GATE", "0")
    h = _solo_handler()
    _make_audible()
    h._response_done_event.clear()
    h._solo_speech_started()
    assert h._barge_paused_response_id == "resp_123"
    h._solo_speech_stopped()
    # The paused reply finished and the answer to this very turn started.
    h._active_response_id = "resp_answer"

    handled = await h._resolve_solo_barge("幫我開燈")

    assert handled is False
    h.connection.response.cancel.assert_not_awaited(), "that id is the answer, not the old reply"
    assert "resp_answer" not in h._cancelled_response_ids
    h._clear_queue_callback.assert_called_once()  # the old reply's audio still goes
    assert not h._held_audio
    assert h._barge_watchdog_task is None, "the answer exists; asking again would duplicate it"
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

    h._resolve_solo_barge_failure()

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
    assert "create_response" not in _turn_detection(False)


def test_solo_turn_detection_hands_the_decision_to_the_client() -> None:
    """With the new default the server must not interrupt: the client owns it."""
    td = _turn_detection(party=False)
    assert td["interrupt_response"] is False
    assert "create_response" not in td, "solo still relies on the server auto-answering"


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
async def test_party_mode_flip_mid_pause_resumes_the_reply() -> None:
    """Flipping to party mode while paused must not strand the pause forever."""
    h = _solo_handler()
    h.connection = None  # set_party_mode must not schedule a session update here
    _make_audible()
    h._response_done_event.clear()
    h._solo_speech_started()
    assert h._barge_paused is True

    h.set_party_mode(True)

    assert h._barge_paused is False and h._barge_pending is False
    # Fix round, finding 3: the solo speech flag is maintained by a branch that
    # stops running the moment the mode flips, so the flip must clear it — a
    # stale True would keep the watchdog standing down for the whole session.
    assert h._barge_speech_open is False
    # Task 4 fix round 2: the late-eligibility flag is written only by the solo
    # speech-start branch, so a flip mid-utterance must clear it too.
    assert h._barge_late_eligible is False
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
    h._party_mode = True
    h._barge_late_eligible = True  # stale, from a solo turn before party mode

    h.set_party_mode(False)

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


def test_gate_text_accepts_name_and_control() -> None:
    """Names and control phrases pass; substantive unaddressed speech does not."""
    assert hf_mod._gate_text_accepts("瑞奇你說錯了") == (True, "name")
    assert hf_mod._gate_text_accepts("Hey Reachy, stop there") == (True, "control phrase")
    assert hf_mod._gate_text_accepts("停") == (True, "control phrase")
    accepted, reason = hf_mod._gate_text_accepts("我們晚餐要吃什麼呢")
    assert not accepted and reason == "unaddressed"


@pytest.mark.asyncio
async def test_resolve_rolls_back_unaddressed_substantive_transcript(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gate ON: substantive speech without a name resumes the reply."""
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
    """Gate ON: the robot's name in the transcript commits the barge."""
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
async def test_partial_transcript_with_name_commits_early() -> None:
    """A delta containing the name resolves the pause without waiting for completed."""
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
async def test_partial_name_does_not_commit_with_the_gate_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gate OFF: the name path is gate-mode only, so the pause waits for the transcript."""
    monkeypatch.setenv("REALTIME_SOLO_NAME_GATE", "0")
    h = _solo_handler()
    _make_audible()
    h._response_done_event.clear()
    h._solo_speech_started()
    await h._maybe_commit_on_partial("欸瑞奇", "item_1")
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
    h._party_mode = True
    await h._maybe_commit_on_partial("欸瑞奇", "item_1")
    assert h._barge_pending
    h.connection.response.cancel.assert_not_awaited()


@pytest.mark.asyncio
async def test_incremental_deltas_accumulate_and_commit_split_name() -> None:
    """GA deltas are incremental: 瑞 + 奇 across two deltas must still match (round 2, finding 3)."""
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
async def test_partial_commit_survives_the_flush_that_resets_barge_state() -> None:
    """In production `_clear_queue` IS `console.clear_audio_queue`, which resets everything.

    `_commit_solo_barge` flushes through it, so the committed item can only be
    recorded *after* that call returns — recording it first would be wiped.
    """
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
    """Gate ON: long speech with no name rolls the pause back instead of committing."""
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
    later resume a reply nobody paused.
    """
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
async def test_late_interrupt_keeps_a_newer_response() -> None:
    """A live response newer than the resumed one IS the answer — do not kill it."""
    h = _solo_handler()
    _make_audible()
    h._response_done_event.clear()
    h._barge_resumed_response_id = "resp_old"
    h._active_response_id = "resp_new"
    await h._late_solo_interrupt()
    h.connection.response.cancel.assert_not_awaited()
    h._clear_queue_callback.assert_not_called()


@pytest.mark.asyncio
async def test_a_transcript_decided_rollback_clears_the_resumed_id() -> None:
    """`_resolve_solo_barge` fully decides the utterance, so it owns the cleanup.

    Its caller `continue`s before the completed handler's trailing clear, so a
    resumed id left behind here would sit through later turns and suppress the
    next real late interrupt.
    """
    h = _solo_handler()
    _make_audible()
    h._response_done_event.clear()
    h._solo_speech_started()
    assert await h._resolve_solo_barge("我們晚餐要吃什麼呢這麼晚了") is True
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
    h._resolve_solo_barge_failure()
    assert h._barge_resumed_response_id is None
    assert h._barge_partial_committed_item is None


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
    handler.client = _make_fake_realtime_client(events=events)
    return handler


def _quiet_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutralise everything a session touches except the barge decision."""
    monkeypatch.setattr(hf_mod, "get_session_instructions", lambda _instance_path=None: "test")
    monkeypatch.setattr(hf_mod, "get_session_voice", lambda default="cedar": default)
    monkeypatch.setattr(hf_mod, "get_tool_specs", lambda: [])
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
):
    """Stand in for `_solo_speech_started`, planting the state under test.

    The session-boundary reset runs inside `_run_realtime_session`, so the state
    a completed transcript is judged against has to be installed from *within*
    the loop — the same monkeypatch seam
    `test_the_loop_routes_solo_speech_through_the_barge_hooks` uses.
    """

    def _apply(self: HuggingFaceRealtimeHandler) -> None:
        if audible:
            self._response_done_event.clear()
        else:
            self._response_done_event.set()
        self._barge_late_eligible = eligible
        self._party_mode = party
        self._active_response_id = active_id
        self._barge_partial_committed_item = partial_item

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
    """The whole point, reached through the real event loop rather than by hand."""
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
    """Audible and eligible, but nobody said the name: Reachy talks on."""
    fired, _ = await _run_late_path(monkeypatch, transcript="我們晚餐要吃什麼呢這麼晚了")
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

    def _plant(self: HuggingFaceRealtimeHandler) -> None:
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
    """With the gate on there is no confirm-commit branch left to warn about."""
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
    ):
        assert field in source, field
