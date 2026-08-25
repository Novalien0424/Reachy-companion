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
from collections import deque
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest
from test_openai_realtime_config import _emit_ready_handler

from reachy_companion import huggingface_realtime as hf_mod
from reachy_companion.hanova import audio_drain, music_hooks
from reachy_companion.console import LocalStream
from reachy_companion.streaming import AdditionalOutputs
from reachy_companion.openai_realtime import ROBOT_RATE, OpenAIRealtimeHandler, _turn_detection
from reachy_companion.huggingface_realtime import HuggingFaceRealtimeHandler


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
    handler._held_audio = deque()


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
    """Speech that outlasts the confirm window is a real interruption."""
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
    """
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
    `response.cancel` await, and the event loop runs inside that await.
    """
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
    """A commit cancelled mid-round-trip must still end the pause it claimed."""
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
    """A blip with no transcript at all resumes the reply and re-arms the onset ramp."""
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
async def test_substantive_transcript_confirms() -> None:
    """Real content means the user really was talking to the robot."""
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
    audio_drain.note_cleared()
    assert audio_drain.is_audible() is False


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
    """A newer utterance owns the floor; the old timer must stand down."""
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
        "_held_audio",
    ):
        assert field in source, field
