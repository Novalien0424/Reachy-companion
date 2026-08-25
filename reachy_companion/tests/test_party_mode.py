"""Party mode: debounced barge-in and the address gate (2026-08-24).

Unit tests for the multi-person hardening in docs/plans/party-mode-plan.md.
The realtime event loop stays thin; everything it calls is tested here.
"""

import time
import asyncio
from types import SimpleNamespace
from collections import deque
from unittest.mock import AsyncMock, MagicMock

import pytest
from test_solo_barge import _install_barge_state

from reachy_companion.hanova import audio_drain, music_hooks
from reachy_companion.openai_realtime import OpenAIRealtimeHandler, _turn_detection
from reachy_companion.tools.party_mode import PartyMode
from reachy_companion.huggingface_realtime import HuggingFaceRealtimeHandler


def _party_handler() -> OpenAIRealtimeHandler:
    """Return a handler with only the party-relevant state, __init__ skipped."""
    h = OpenAIRealtimeHandler.__new__(OpenAIRealtimeHandler)
    h._party_mode = True
    h._party_last_accept_at = None
    h._party_speech_open = False
    h._party_utterance_seq = 0
    h._party_barge_task = None
    h._active_response_id = None
    h._cancelled_response_ids = deque(maxlen=8)
    h._response_done_event = asyncio.Event()
    h._response_done_event.set()
    # Task 8 state: `set_party_mode` has to resolve a solo pause that is live
    # when the mode flips, so even a party-only handler needs these fields.
    _install_barge_state(h)
    # On the OpenAI handler `_clear_queue` is a wrapping property; the mock
    # lands in `_clear_queue_callback` and that is what the asserts read.
    h._clear_queue = MagicMock()
    h.connection = SimpleNamespace(response=SimpleNamespace(cancel=AsyncMock()))
    # No face in frame by default: the face-engagement gate path (Task 7) is
    # opt-in per test via `h.deps.reachy_mini.get_tracked_face`.
    h.deps = SimpleNamespace(
        reachy_mini=SimpleNamespace(
            get_tracked_face=lambda wait: SimpleNamespace(detected=False, x=None, y=None, roll=None, ts=None)
        ),
        movement_manager=MagicMock(),
    )
    return h


@pytest.fixture(autouse=True)
def _clean_party_env(monkeypatch: pytest.MonkeyPatch):
    for name in (
        "REALTIME_PARTY_DEFAULT",
        "REALTIME_PARTY_BARGE_CONFIRM_MS",
        "REALTIME_PARTY_FOLLOWUP_S",
        "REALTIME_PARTY_ADDRESS_NAMES",
        "REALTIME_VAD_TYPE",
        "REALTIME_SOLO_CLIENT_BARGE",
    ):
        monkeypatch.delenv(name, raising=False)
    audio_drain.reset()
    yield
    audio_drain.reset()


# --------------------------------------------------------------------------
# Turn detection config
# --------------------------------------------------------------------------


def test_solo_turn_detection_keeps_the_server_answering(monkeypatch: pytest.MonkeyPatch):
    """Solo differs from party in exactly one way: the server still auto-answers.

    Since Task 8 the interrupt decision is the client's in both modes (see
    `tests/test_solo_barge.py`); `REALTIME_SOLO_CLIENT_BARGE=0` is what restores
    the pre-party config byte for byte.
    """
    td = _turn_detection(party=False)
    assert td["interrupt_response"] is False
    assert "create_response" not in td

    monkeypatch.setenv("REALTIME_SOLO_CLIENT_BARGE", "0")
    legacy = _turn_detection(party=False)
    assert legacy["interrupt_response"] is True
    assert "create_response" not in legacy


def test_party_turn_detection_disables_server_autonomy():
    """Party: the server neither interrupts nor auto-answers; the client decides."""
    td = _turn_detection(party=True)
    assert td["interrupt_response"] is False
    assert td["create_response"] is False


def test_party_turn_detection_covers_semantic_vad(monkeypatch: pytest.MonkeyPatch):
    """Semantic VAD gets the same party flags as server VAD."""
    monkeypatch.setenv("REALTIME_VAD_TYPE", "semantic_vad")
    td = _turn_detection(party=True)
    assert td["type"] == "semantic_vad"
    assert td["interrupt_response"] is False and td["create_response"] is False


# --------------------------------------------------------------------------
# The address gate
# --------------------------------------------------------------------------


def test_gate_accepts_the_robot_name_in_any_case():
    """Any configured name, any case, anywhere in the transcript passes."""
    h = _party_handler()
    assert h._party_gate_accepts("Richie 你可以放首歌嗎")
    assert h._party_gate_accepts("瑞奇你在嗎")
    assert h._party_gate_accepts("REACHY, what time is it")


def test_gate_accepts_control_phrases_unconditionally():
    """A robot you cannot silence is worse than any false positive."""
    h = _party_handler()
    assert h._party_gate_accepts("閉嘴啦")
    assert h._party_gate_accepts("安静一点")
    assert h._party_gate_accepts("stop stop stop")


def test_gate_accepts_followups_inside_the_window():
    """Recent engagement keeps the floor without re-addressing by name."""
    h = _party_handler()
    h._party_last_accept_at = time.monotonic()
    assert h._party_gate_accepts("然後呢？")


def test_gate_denies_ambient_chatter():
    """Laughter and third-person talk about the robot are not for the robot."""
    h = _party_handler()
    assert not h._party_gate_accepts("哈哈哈")
    assert not h._party_gate_accepts("我剛剛問他為什麼他耳朵這麼長")


def test_gate_denies_after_the_window_expires(monkeypatch: pytest.MonkeyPatch):
    """The follow-up window closes; ambient speech goes back to denied."""
    monkeypatch.setenv("REALTIME_PARTY_FOLLOWUP_S", "1")
    h = _party_handler()
    h._party_last_accept_at = time.monotonic() - 5.0
    assert not h._party_gate_accepts("然後呢？")


def test_gate_names_are_configurable(monkeypatch: pytest.MonkeyPatch):
    """REALTIME_PARTY_ADDRESS_NAMES replaces the default name list."""
    monkeypatch.setenv("REALTIME_PARTY_ADDRESS_NAMES", "小白")
    h = _party_handler()
    assert h._party_gate_accepts("小白你好")
    assert not h._party_gate_accepts("Reachy 你好")


def test_gate_denies_backchannel_even_in_followup_window():
    """Agreement noise inside a live follow-up window must not restart the robot."""
    h = _party_handler()
    h._party_last_accept_at = time.monotonic()
    assert h._party_gate_accepts("嗯嗯") is False
    assert h._party_gate_accepts("哈哈哈") is False


def test_gate_control_phrase_beats_backchannel_filter():
    """A robot you cannot silence is worse than any false positive.

    The control check must run before any content filter gets a chance to
    suppress it.
    """
    h = _party_handler()
    assert h._party_gate_accepts("停") is True


def test_gate_accepts_substantive_speech_from_engaged_face():
    """A centered, fresh face plus real content accepts without a name or window."""
    h = _party_handler()
    face = SimpleNamespace(detected=True, x=0.1, y=0.0, roll=0.0, ts=time.monotonic())
    h.deps.reachy_mini.get_tracked_face = lambda wait: face
    assert h._party_gate_accepts("可以幫我開燈嗎") is True
    assert h._party_gate_accepts("嗯嗯") is False  # backchannel still denied


def test_gate_face_signal_ignores_stale_or_offcenter():
    """A face reading must be both recent and centered to count as engaged."""
    h = _party_handler()
    stale = SimpleNamespace(detected=True, x=0.1, y=0.0, roll=0.0, ts=time.monotonic() - 60)
    h.deps.reachy_mini.get_tracked_face = lambda wait: stale
    assert h._party_gate_accepts("可以幫我開燈嗎") is False
    off = SimpleNamespace(detected=True, x=0.9, y=0.0, roll=0.0, ts=time.monotonic())
    h.deps.reachy_mini.get_tracked_face = lambda wait: off
    assert h._party_gate_accepts("可以幫我開燈嗎") is False


def test_gate_face_signal_env_off(monkeypatch: pytest.MonkeyPatch):
    """REALTIME_PARTY_FACE_GATE=0 disables the face-engagement path entirely."""
    monkeypatch.setenv("REALTIME_PARTY_FACE_GATE", "0")
    h = _party_handler()
    face = SimpleNamespace(detected=True, x=0.0, y=0.0, roll=0.0, ts=time.monotonic())
    h.deps.reachy_mini.get_tracked_face = lambda wait: face
    assert h._party_gate_accepts("可以幫我開燈嗎") is False


def test_face_query_failure_is_a_quiet_no():
    """A daemon-side error while reading the tracked face must never raise."""
    h = _party_handler()

    def boom(wait: bool) -> SimpleNamespace:
        raise RuntimeError("daemon gone")

    h.deps.reachy_mini.get_tracked_face = boom
    assert h._face_engaged() is False


def test_session_start_resets_party_state():
    """A reconnect must not carry a stale follow-up window into the new session.

    `_run_realtime_session` calls this reset seam at session start; exercised
    directly here rather than through the full fake-connection harness because
    it is a plain state reset with no I/O of its own.
    """
    h = _party_handler()
    h._party_last_accept_at = time.monotonic()
    h._party_speech_open = True
    seq_before = h._party_utterance_seq
    h._party_reset_for_new_session()
    assert h._party_last_accept_at is None
    assert h._party_speech_open is False
    assert h._party_utterance_seq == seq_before + 1


# --------------------------------------------------------------------------
# Mode switching
# --------------------------------------------------------------------------


def test_set_party_mode_opens_the_followup_window_on_enable():
    """The person who toggled the mode is engaged; they keep the floor."""
    h = _party_handler()
    h._party_mode = False
    h.connection = None
    out = h.set_party_mode(True)
    assert out == {"ok": True, "status": "party_on", "party_mode": True}
    assert h._party_last_accept_at is not None
    assert h._party_gate_accepts("放首歌吧")


def test_set_party_mode_off_clears_the_window_and_invalidates_timers():
    """Leaving party mode closes the window and stales any timer."""
    h = _party_handler()
    h.connection = None
    h._party_last_accept_at = time.monotonic()
    seq = h._party_utterance_seq
    out = h.set_party_mode(False)
    assert out["status"] == "party_off"
    assert h._party_last_accept_at is None
    assert h._party_utterance_seq == seq + 1


def test_set_party_mode_is_idempotent():
    """Setting the mode it is already in changes nothing."""
    h = _party_handler()
    h.connection = None
    assert h.set_party_mode(True) == {"ok": True, "status": "unchanged", "party_mode": True}


# --------------------------------------------------------------------------
# Debounced barge-in
# --------------------------------------------------------------------------


def _make_audible():
    generation = audio_drain.begin_response()
    audio_drain.note_enqueued(generation, sample_count=48000, sample_rate=24000)
    return generation


@pytest.mark.asyncio
async def test_a_blip_does_not_cancel_the_reply(monkeypatch: pytest.MonkeyPatch):
    """speech_stopped before the confirm delay: Reachy keeps talking."""
    monkeypatch.setenv("REALTIME_PARTY_BARGE_CONFIRM_MS", "30")
    h = _party_handler()
    _make_audible()
    h._response_done_event.clear()
    h._party_speech_open = True
    h._party_utterance_seq = 7
    h._start_party_barge_timer()
    h._party_speech_open = False  # the blip ended
    await asyncio.sleep(0.08)
    h.connection.response.cancel.assert_not_awaited()
    h._clear_queue_callback.assert_not_called()


@pytest.mark.asyncio
async def test_sustained_speech_cancels_and_flushes(monkeypatch: pytest.MonkeyPatch):
    """Speech outlasting the debounce while audible is a real barge-in."""
    monkeypatch.setenv("REALTIME_PARTY_BARGE_CONFIRM_MS", "30")
    h = _party_handler()
    _make_audible()
    h._response_done_event.clear()
    h._active_response_id = "resp_123"
    h._party_speech_open = True
    h._party_utterance_seq = 7
    h._start_party_barge_timer()
    await asyncio.sleep(0.08)
    h.connection.response.cancel.assert_awaited_once()
    h._clear_queue_callback.assert_called_once()
    assert "resp_123" in h._cancelled_response_ids


@pytest.mark.asyncio
async def test_a_stale_timer_never_fires(monkeypatch: pytest.MonkeyPatch):
    """A newer utterance owns the floor; the old timer must stand down."""
    monkeypatch.setenv("REALTIME_PARTY_BARGE_CONFIRM_MS", "30")
    h = _party_handler()
    _make_audible()
    h._response_done_event.clear()
    h._party_speech_open = True
    h._party_utterance_seq = 7
    h._start_party_barge_timer()
    h._party_utterance_seq = 8  # superseded
    await asyncio.sleep(0.08)
    h.connection.response.cancel.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_cancel_when_the_robot_is_silent(monkeypatch: pytest.MonkeyPatch):
    """Nothing to interrupt: silent robot means no cancel and no flush."""
    monkeypatch.setenv("REALTIME_PARTY_BARGE_CONFIRM_MS", "30")
    h = _party_handler()  # drain state is reset: nothing audible
    h._party_speech_open = True
    h._party_utterance_seq = 3
    h._start_party_barge_timer()
    await asyncio.sleep(0.08)
    h.connection.response.cancel.assert_not_awaited()
    h._clear_queue_callback.assert_not_called()


@pytest.mark.asyncio
async def test_cancel_without_an_active_response_is_a_no_op():
    """No active response id: cancelling must do nothing at all."""
    h = _party_handler()
    h._active_response_id = None
    await h._cancel_active_response()
    h.connection.response.cancel.assert_not_awaited()


# --------------------------------------------------------------------------
# audio_drain.is_audible
# --------------------------------------------------------------------------


def test_is_audible_tracks_the_queue_and_buffer():
    """Queued audio is audible; a cleared queue is not."""
    assert audio_drain.is_audible() is False
    generation = audio_drain.begin_response()
    audio_drain.note_enqueued(generation, sample_count=24000, sample_rate=24000)
    assert audio_drain.is_audible() is True
    audio_drain.note_cleared()
    assert audio_drain.is_audible() is False


# --------------------------------------------------------------------------
# Music hooks: candidate speech and no-response turns
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_speech_candidate_ducks_without_marking_the_queue_cleared(monkeypatch: pytest.MonkeyPatch):
    """Finding 5: a candidate must not fake a queue flush while Reachy talks."""
    generation = audio_drain.begin_response()
    audio_drain.note_enqueued(generation, sample_count=24000, sample_rate=24000)
    paused = []

    async def fake_pause(deps):
        paused.append(deps)

    monkeypatch.setattr(music_hooks.PLAYER, "pause_for_speech", fake_pause)
    music_hooks.on_user_speech_candidate(deps="deps")
    await music_hooks.drain_pending_for_tests()
    assert paused == ["deps"]
    assert audio_drain.outstanding_s() > 0.5, "the queued reply must still be accounted for"
    music_hooks.reset_for_tests()


@pytest.mark.asyncio
async def test_a_denied_turn_still_resumes_the_music(monkeypatch: pytest.MonkeyPatch):
    """Finding 4: gate-deny produces no response; the duck must still lift."""
    resumed = []

    async def fake_resume(deps):
        resumed.append(deps)

    monkeypatch.setattr(music_hooks.PLAYER, "resume_after_speech", fake_resume)
    # No network, ever: session start/stop silence the daemon over HTTP.
    monkeypatch.setattr("reachy_companion.hanova.music_player.daemon_stop_sound", AsyncMock(return_value=True))
    token = await music_hooks.on_session_started(SimpleNamespace())
    try:
        music_hooks.on_turn_without_response(deps="deps")
        for _ in range(50):
            await asyncio.sleep(0.02)
            if resumed:
                break
        assert resumed == ["deps"]
    finally:
        await music_hooks.on_session_shutdown(SimpleNamespace(), token)
        music_hooks.reset_for_tests()


# --------------------------------------------------------------------------
# The tool
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_party_mode_tool_flips_through_the_deps_seam():
    """The tool forwards enabled to the injected handler seam."""
    seen = []
    deps = SimpleNamespace(set_party_mode=lambda enabled: (seen.append(enabled), {"ok": True, "status": "party_on", "party_mode": enabled})[1])
    out = await PartyMode()(deps, enabled=True)
    assert out["ok"] is True and seen == [True]


@pytest.mark.asyncio
async def test_party_mode_tool_reports_a_missing_seam():
    """A build without the seam reports failure instead of raising."""
    deps = SimpleNamespace(set_party_mode=None)
    out = await PartyMode()(deps, enabled=True)
    assert out["ok"] is False


def test_party_state_defaults_exist_on_the_base_handler():
    """The real __init__ must define every field the loop and tests touch."""
    import inspect

    source = inspect.getsource(HuggingFaceRealtimeHandler.__init__)
    for field in (
        "_party_mode",
        "_party_last_accept_at",
        "_party_speech_open",
        "_party_utterance_seq",
        "_party_barge_task",
        "_active_response_id",
        "_cancelled_response_ids",
    ):
        assert field in source, field
