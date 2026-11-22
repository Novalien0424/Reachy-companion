"""The room's follow-up window starts when Reachy stops talking (2026-11-22 plan, N4).

In 多人聊天模式 an unnamed turn is answered inside `REALTIME_PARTY_FOLLOWUP_S`
(20 s) of the last accepted one. Counted from the accept, a reply that ran 15 s
after a few seconds of thinking left the caller no time to answer it: on
2026-11-21 every reply had used the window up before it ended, and each ended
on a question. The window now counts from the end of Reachy's voice when that
is later.
"""

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

import reachy_companion.huggingface_realtime as hf_mod
from reachy_companion.hanova import audio_drain
from reachy_companion.tools.core_tools import ToolDependencies
from reachy_companion.conversation_mode import ConversationMode
from reachy_companion.huggingface_realtime import HuggingFaceRealtimeHandler


_FOLLOW_UP = "你再看一次前面"


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Shipped window, shipped gates, and a drain clock that starts silent."""
    for name in ("REALTIME_PARTY_FOLLOWUP_S", "REALTIME_PARTY_FACE_GATE", "REALTIME_DEFAULT_MODE"):
        monkeypatch.delenv(name, raising=False)
    audio_drain.reset()
    yield
    audio_drain.reset()


def _room(
    monkeypatch: pytest.MonkeyPatch, *, now: float, accepted_at: float | None, spoke_until: float, audible: bool = False
) -> HuggingFaceRealtimeHandler:
    """Build a 多人 handler at *now*, with the last accept and the end of Reachy's voice set."""
    handler = HuggingFaceRealtimeHandler(ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock()))
    handler._conversation_mode = ConversationMode.GROUP
    handler._party_last_accept_at = accepted_at
    monkeypatch.setattr(hf_mod, "time", SimpleNamespace(monotonic=lambda: now, perf_counter=lambda: now))
    monkeypatch.setattr(hf_mod.audio_drain, "spoke_until", lambda: spoke_until)
    monkeypatch.setattr(handler, "_robot_audible", lambda: audible)
    return handler


def test_an_unnamed_turn_ten_seconds_after_the_reply_is_answered(monkeypatch: pytest.MonkeyPatch) -> None:
    """Accepted at 0, the reply ended at 17: 27 is 10 s into the caller's window."""
    handler = _room(monkeypatch, now=27.0, accepted_at=0.0, spoke_until=17.0)
    assert handler._answer_gate_accepts(_FOLLOW_UP, ConversationMode.GROUP) is True


def test_the_window_still_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    """25 s after the reply ended, the room has moved on."""
    handler = _room(monkeypatch, now=42.0, accepted_at=0.0, spoke_until=17.0)
    assert handler._answer_gate_accepts(_FOLLOW_UP, ConversationMode.GROUP) is False


def test_talk_over_a_long_reply_is_not_an_answer_to_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """While Reachy is still talking, the accept time alone decides, as before."""
    handler = _room(monkeypatch, now=25.0, accepted_at=0.0, spoke_until=30.0, audible=True)
    assert handler._answer_gate_accepts(_FOLLOW_UP, ConversationMode.GROUP) is False


def test_an_accept_after_the_last_reply_is_the_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    """An older reply does not shorten the window of a newer accepted turn."""
    handler = _room(monkeypatch, now=58.0, accepted_at=40.0, spoke_until=17.0)
    assert handler._answer_gate_accepts(_FOLLOW_UP, ConversationMode.GROUP) is True


def test_reachy_speaking_first_opens_no_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """After the greeting nobody has been accepted yet, so the name is still needed."""
    handler = _room(monkeypatch, now=5.0, accepted_at=None, spoke_until=4.0)
    assert handler._answer_gate_accepts(_FOLLOW_UP, ConversationMode.GROUP) is False


def test_a_backchannel_is_never_a_follow_up(monkeypatch: pytest.MonkeyPatch) -> None:
    """The wider window does not let 「嗯」 through: that filter runs first."""
    handler = _room(monkeypatch, now=18.0, accepted_at=0.0, spoke_until=17.0)
    assert handler._answer_gate_accepts("嗯", ConversationMode.GROUP) is False


def test_the_drain_clock_remembers_when_the_voice_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    """`spoke_until` follows the audio handed to the sink, and a flush ends it at once."""
    clock = {"now": 100.0}
    monkeypatch.setattr(audio_drain, "time", SimpleNamespace(monotonic=lambda: clock["now"]))
    audio_drain.reset()
    assert audio_drain.spoke_until() == 0.0

    audio_drain.note_chunk(sample_count=48000, sample_rate=24000)  # 2 s of speech
    assert audio_drain.spoke_until() == pytest.approx(102.0)

    # A reply that drained on its own keeps its end time.
    clock["now"] = 110.0
    assert audio_drain.spoke_until() == pytest.approx(102.0)

    # A reply cut off mid-way ended at the flush, not at its planned end.
    audio_drain.note_chunk(sample_count=240000, sample_rate=24000)  # 10 s more, until 120
    clock["now"] = 113.0
    audio_drain.note_cleared()
    assert audio_drain.spoke_until() == pytest.approx(113.0)
