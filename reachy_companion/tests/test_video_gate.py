"""While a video plays, Reachy answers only when called (2026-10-31 plan, W3).

The video session lives in `hanova.tv_session` (its own tests are in
`tests/test_tv_session.py`); this file pins how the answer gate reads it in
each conversation mode, and the one TV read a stale session buys before an
unnamed turn is refused.
"""

import time
import asyncio
import logging
from typing import Any

import pytest

# `tests/` has no __init__.py, so pytest's prepend import mode puts the
# directory itself on sys.path — import the sibling harnesses by bare name.
from test_solo_barge import _loop_handler, _quiet_session
from test_conversation_modes import _mode_handler
from test_huggingface_realtime import _FakeEvent

from reachy_companion import huggingface_realtime as hf_mod
from reachy_companion.hanova import settings, ha_client, tv_session
from reachy_companion.conversation_mode import ConversationMode
from reachy_companion.huggingface_realtime import _solo_interrupt_verdict


ONE = ConversationMode.ONE_ON_ONE
GROUP = ConversationMode.GROUP
RECORD = ConversationMode.RECORD


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Start every test with no video on the TV and the shipped gate defaults."""
    for name in (
        "REALTIME_ONE_ON_ONE_ANSWER_GATE",
        "REALTIME_PARTY_FACE_GATE",
        "REALTIME_PARTY_FOLLOWUP_S",
        "REALTIME_PARTY_ADDRESS_NAMES",
        "REALTIME_PARTY_BARGE",
        "REALTIME_SOLO_NAME_GATE",
    ):
        monkeypatch.delenv(name, raising=False)
    tv_session.mark_ended("test setup")
    yield
    tv_session.mark_ended("test teardown")


def _install_tv(monkeypatch: pytest.MonkeyPatch, state: str | Exception) -> list[str]:
    """Answer every HA read of the TV with *state* (or raise it); return the reads."""
    reads: list[str] = []

    async def get_state(entity_id: str, timeout_s: float = 15.0) -> dict[str, Any]:
        reads.append(entity_id)
        if isinstance(state, Exception):
            raise state
        return {"ok": True, "result": {"state": state, "attributes": {}}}

    monkeypatch.setattr(settings, "cast_entity", lambda: "SENTINEL_TV_ENTITY")
    monkeypatch.setattr(ha_client, "ha_get_state", get_state)
    return reads


def _make_stale() -> None:
    """Age the session past `STALE_AFTER_S` without sleeping through it."""
    tv_session.mark_started("nas")
    tv_session._looked_at = time.monotonic() - tv_session.STALE_AFTER_S - 1.0


# --------------------------------------------------------------------------
# The gate, per mode
# --------------------------------------------------------------------------


def test_one_on_one_answers_only_when_called_while_a_video_plays() -> None:
    """A substantive sentence is the soundtrack's as often as anyone's: the name decides."""
    h = _mode_handler(ONE)
    assert h._answer_gate_accepts("我們晚餐要吃什麼呢", ONE)
    tv_session.mark_started("nas")
    assert not h._answer_gate_accepts("我們晚餐要吃什麼呢", ONE)
    assert h._answer_gate_accepts("瑞奇，我們在哪裡", ONE)
    assert h._answer_gate_accepts("停", ONE)
    tv_session.mark_ended("stop")
    assert h._answer_gate_accepts("我們晚餐要吃什麼呢", ONE)


def test_group_ignores_its_follow_up_window_while_a_video_plays() -> None:
    """An open follow-up window must not let the soundtrack in."""
    h = _mode_handler(GROUP)
    h._party_last_accept_at = time.monotonic()
    assert h._answer_gate_accepts("然後呢？", GROUP)
    tv_session.mark_started("youtube")
    assert not h._answer_gate_accepts("然後呢？", GROUP)
    assert h._answer_gate_accepts("瑞奇然後呢", GROUP)
    assert h._answer_gate_accepts("安靜一點", GROUP)


def test_group_ignores_an_engaged_face_while_a_video_plays(monkeypatch: pytest.MonkeyPatch) -> None:
    """The family faces the TV and the robot alike; a face is no address during a clip."""
    monkeypatch.setenv("REALTIME_PARTY_FACE_GATE", "1")
    h = _mode_handler(GROUP)
    face = type("Face", (), {"detected": True, "x": 0.0, "y": 0.0, "roll": 0.0, "ts": time.monotonic()})()
    h.deps.reachy_mini.get_tracked_face = lambda wait: face
    assert h._answer_gate_accepts("可以幫我開燈嗎", GROUP)
    tv_session.mark_started("nas")
    assert not h._answer_gate_accepts("可以幫我開燈嗎", GROUP)


def test_record_is_unchanged_by_a_video() -> None:
    """紀錄模式 already answers only a name or a control phrase."""
    h = _mode_handler(RECORD)
    for active in (False, True):
        if active:
            tv_session.mark_started("nas")
        assert not h._answer_gate_accepts("那我們下週三再開一次", RECORD)
        assert h._answer_gate_accepts("瑞奇幫我總結一下", RECORD)
        assert h._answer_gate_accepts("停", RECORD)


def test_the_video_gate_reads_the_turns_own_mode() -> None:
    """The mode the utterance began in decides, as for every other gate (P1-2)."""
    h = _mode_handler(RECORD)
    tv_session.mark_started("nas")
    assert not h._answer_gate_accepts("我們晚餐要吃什麼呢", ONE)
    assert hf_mod._video_gate_applies(ONE) and hf_mod._video_gate_applies(GROUP)
    assert not hf_mod._video_gate_applies(RECORD)


def test_the_solo_interrupt_verdict_takes_the_name_rule_while_a_video_plays(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A substantive soundtrack line must not stop a one-on-one reply either (W4)."""
    assert _solo_interrupt_verdict("我們晚餐要吃什麼呢這麼晚了") == (True, "substantive")
    tv_session.mark_started("nas")
    assert _solo_interrupt_verdict("我們晚餐要吃什麼呢這麼晚了") == (False, "unaddressed")
    assert _solo_interrupt_verdict("瑞奇等一下") == (True, "name")
    assert _solo_interrupt_verdict("停") == (True, "control phrase")
    monkeypatch.setenv("REALTIME_PARTY_BARGE", "persistence")
    assert _solo_interrupt_verdict("我們晚餐要吃什麼呢這麼晚了") == (True, "substantive")


# --------------------------------------------------------------------------
# Staleness: one TV read before an unnamed turn is refused
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_stale_session_the_tv_reads_as_idle_reopens_the_normal_rules(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A clip that ended on its own must not keep Reachy quiet."""
    reads = _install_tv(monkeypatch, "idle")
    h = _mode_handler(ONE)
    _make_stale()
    with caplog.at_level(logging.INFO):
        await h._recheck_stale_video("我們晚餐要吃什麼呢", ONE)
    assert reads == ["SENTINEL_TV_ENTITY"]
    assert not tv_session.active()
    assert h._answer_gate_accepts("我們晚餐要吃什麼呢", ONE)
    assert "the video has ended" in caplog.text


@pytest.mark.asyncio
async def test_a_stale_session_still_playing_stays_quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    """The TV still in playback keeps the gate closed, and resets the stale clock."""
    reads = _install_tv(monkeypatch, "playing")
    h = _mode_handler(GROUP)
    _make_stale()
    await h._recheck_stale_video("然後呢", GROUP)
    assert reads == ["SENTINEL_TV_ENTITY"]
    assert tv_session.active() and not tv_session.is_stale()
    assert not h._answer_gate_accepts("然後呢", GROUP)


@pytest.mark.asyncio
@pytest.mark.parametrize("transcript", ["瑞奇我們在哪裡", "停"])
async def test_named_turns_never_pay_for_the_read(monkeypatch: pytest.MonkeyPatch, transcript: str) -> None:
    """A name or a control phrase is accepted either way, so nothing is read."""
    reads = _install_tv(monkeypatch, "idle")
    h = _mode_handler(ONE)
    _make_stale()
    await h._recheck_stale_video(transcript, ONE)
    assert reads == []
    assert tv_session.active()


@pytest.mark.asyncio
async def test_a_fresh_session_is_not_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inside `STALE_AFTER_S` the recorded session is trusted as is."""
    reads = _install_tv(monkeypatch, "idle")
    h = _mode_handler(ONE)
    tv_session.mark_started("nas")
    await h._recheck_stale_video("我們晚餐要吃什麼呢", ONE)
    assert reads == []


@pytest.mark.asyncio
async def test_record_never_reads_the_tv(monkeypatch: pytest.MonkeyPatch) -> None:
    """紀錄模式 is unchanged, including the read."""
    reads = _install_tv(monkeypatch, "idle")
    h = _mode_handler(RECORD)
    _make_stale()
    await h._recheck_stale_video("那我們下週三再開一次", RECORD)
    assert reads == []


@pytest.mark.asyncio
async def test_a_failed_read_keeps_the_session(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """An HA error is logged and the gate stays closed; the name still reaches Reachy."""
    _install_tv(monkeypatch, RuntimeError("HA down"))
    h = _mode_handler(ONE)
    _make_stale()
    with caplog.at_level(logging.WARNING):
        await h._recheck_stale_video("我們晚餐要吃什麼呢", ONE)
    assert tv_session.active()
    assert "TV read-back failed (RuntimeError)" in caplog.text


@pytest.mark.asyncio
async def test_a_hung_read_is_cut_by_the_belt(monkeypatch: pytest.MonkeyPatch) -> None:
    """The receive loop never waits on HA longer than the belt."""

    async def hang(timeout_s: float = 1.5) -> bool:
        await asyncio.sleep(10)
        return False

    monkeypatch.setattr(hf_mod, "_VIDEO_RECHECK_BELT_S", 0.05)
    monkeypatch.setattr(tv_session, "confirm_still_playing", hang)
    h = _mode_handler(ONE)
    _make_stale()
    started = time.monotonic()
    await h._recheck_stale_video("我們晚餐要吃什麼呢", ONE)
    assert time.monotonic() - started < 1.0
    assert tv_session.active()


# --------------------------------------------------------------------------
# Through the real receive loop
# --------------------------------------------------------------------------


async def _run_turn(monkeypatch: pytest.MonkeyPatch, mode: ConversationMode, transcript: str) -> Any:
    """Replay speech-start → committed transcript in *mode*; return the handler."""
    _quiet_session(monkeypatch)
    handler = _loop_handler(
        (
            _FakeEvent("input_audio_buffer.speech_started", item_id="item_1"),
            _FakeEvent(
                "conversation.item.input_audio_transcription.completed",
                transcript=transcript,
                item_id="item_1",
            ),
        )
    )
    handler._conversation_mode = mode
    await handler._run_realtime_session()
    return handler


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [ONE, GROUP])
async def test_the_loop_denies_an_unnamed_turn_with_the_video_line(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, mode: ConversationMode
) -> None:
    """The journal names the video gate, not the mode's own deny line."""
    tv_session.mark_started("nas")
    requested: list[str] = []

    async def record_request(self: Any, item_id: str | None, *, already_answered: bool = False) -> None:
        requested.append(str(item_id))

    monkeypatch.setattr(hf_mod.HuggingFaceRealtimeHandler, "_request_accepted_turn_response", record_request)
    with caplog.at_level(logging.INFO):
        await _run_turn(monkeypatch, mode, "我們晚餐要吃什麼呢")
    assert requested == []
    assert "video gate: no answer while a video plays" in caplog.text
    assert "one-on-one gate" not in caplog.text and "party gate" not in caplog.text


@pytest.mark.asyncio
async def test_the_loop_answers_a_named_turn_while_a_video_plays(monkeypatch: pytest.MonkeyPatch) -> None:
    """「瑞奇…」 over the soundtrack gets its answer."""
    tv_session.mark_started("nas")
    requested: list[str] = []

    async def record_request(self: Any, item_id: str | None, *, already_answered: bool = False) -> None:
        requested.append(str(item_id))

    monkeypatch.setattr(hf_mod.HuggingFaceRealtimeHandler, "_request_accepted_turn_response", record_request)
    await _run_turn(monkeypatch, GROUP, "瑞奇我們在哪裡")
    assert requested == ["item_1"]


@pytest.mark.asyncio
async def test_the_loop_reads_a_stale_session_and_answers_once_the_video_ended(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The read-back ends a clip that finished on its own; one-on-one answers as usual."""
    reads = _install_tv(monkeypatch, "off")
    _make_stale()
    requested: list[str] = []

    async def record_request(self: Any, item_id: str | None, *, already_answered: bool = False) -> None:
        requested.append(str(item_id))

    monkeypatch.setattr(hf_mod.HuggingFaceRealtimeHandler, "_request_accepted_turn_response", record_request)
    await _run_turn(monkeypatch, ONE, "我們晚餐要吃什麼呢")
    assert reads == ["SENTINEL_TV_ENTITY"]
    assert not tv_session.active()
    assert requested == ["item_1"]
