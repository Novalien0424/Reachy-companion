import time
import asyncio
import logging
from types import SimpleNamespace
from typing import Any, Callable
from collections import deque
from unittest.mock import AsyncMock, MagicMock

import pytest

# `tests/` has no __init__.py, so pytest's prepend import mode puts the
# directory itself on sys.path -- import the sibling harness by bare name.
from test_huggingface_realtime import _FakeEvent

import reachy_companion.huggingface_realtime as hf_mod
from reachy_companion.tools.core_tools import ToolDependencies
from reachy_companion.conversation_mode import ConversationMode
from reachy_companion.huggingface_realtime import HuggingFaceRealtimeHandler


class _Noop:
    async def update(self, **_kwargs: Any) -> None:
        pass

    async def append(self, **_kwargs: Any) -> None:
        pass

    async def create(self, **_kwargs: Any) -> None:
        pass

    async def cancel(self, **_kwargs: Any) -> None:
        pass


class _TimedConnection:
    session = _Noop()
    input_audio_buffer = _Noop()
    conversation = SimpleNamespace(item=_Noop())
    response = _Noop()

    def __init__(
        self,
        events: tuple[tuple[float, _FakeEvent], ...],
        *,
        tail_delay_s: float = 0.0,
        probes: dict[int, Callable[[], None]] | None = None,
    ) -> None:
        self._events = deque(events)
        self._tail_delay_s = tail_delay_s
        self._probes = probes or {}
        self._index = 0

    async def __aenter__(self) -> "_TimedConnection":
        return self

    async def __aexit__(self, *_args: Any) -> bool:
        return False

    async def close(self) -> None:
        pass

    def __aiter__(self) -> "_TimedConnection":
        return self

    async def __anext__(self) -> _FakeEvent:
        if self._events:
            delay_s, event = self._events.popleft()
            if delay_s > 0.0:
                await asyncio.sleep(delay_s)
            probe = self._probes.get(self._index)
            if probe is not None:
                probe()
            self._index += 1
            return event
        if self._tail_delay_s > 0.0:
            tail_delay_s, self._tail_delay_s = self._tail_delay_s, 0.0
            await asyncio.sleep(tail_delay_s)
        raise StopAsyncIteration


class _TimedClient:
    def __init__(
        self,
        events: tuple[tuple[float, _FakeEvent], ...],
        *,
        tail_delay_s: float = 0.0,
        probes: dict[int, Callable[[], None]] | None = None,
    ) -> None:
        self._events = events
        self._tail_delay_s = tail_delay_s
        self._probes = probes

    @property
    def realtime(self) -> "_TimedClient":
        return self

    def connect(self, **_kwargs: Any) -> _TimedConnection:
        return _TimedConnection(self._events, tail_delay_s=self._tail_delay_s, probes=self._probes)


class _FakeClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def monotonic(self) -> float:
        return self.now

    def set(self, now: float) -> None:
        self.now = now


@pytest.fixture(autouse=True)
def _clean_holdoff_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "REALTIME_COMMIT_HOLDOFF_MS",
        "REALTIME_DEFAULT_MODE",
        "REALTIME_ONE_ON_ONE_ANSWER_GATE",
        "REALTIME_PARTY_DEFAULT",
        "REALTIME_SOLO_NAME_GATE",
    ):
        monkeypatch.delenv(name, raising=False)


def _patch_quiet_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hf_mod, "get_session_instructions", lambda _instance_path=None: "test")
    monkeypatch.setattr(hf_mod, "get_session_voice", lambda default="cedar": default)
    monkeypatch.setattr(hf_mod, "get_tool_specs", lambda exclusion_list=None: [])
    monkeypatch.setattr(hf_mod, "record_transcript", lambda _deps, _role, _text: None)
    monkeypatch.setattr(hf_mod, "on_session_started", AsyncMock(return_value=1))
    monkeypatch.setattr(hf_mod, "on_session_shutdown", AsyncMock())
    monkeypatch.setattr(hf_mod, "on_assistant_turn_ended", lambda _deps, _live=None: None)
    monkeypatch.setattr(hf_mod, "on_response_created", lambda: None)
    monkeypatch.setattr(hf_mod, "on_user_speech_candidate", lambda _deps: None)

    async def _park_response_sender(_self: HuggingFaceRealtimeHandler) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(HuggingFaceRealtimeHandler, "_response_sender_loop", _park_response_sender)


def _handler(
    monkeypatch: pytest.MonkeyPatch,
    events: tuple[tuple[float, _FakeEvent], ...],
    *,
    tail_delay_s: float = 0.0,
    probes: dict[int, Callable[[], None]] | None = None,
) -> HuggingFaceRealtimeHandler:
    _patch_quiet_session(monkeypatch)
    handler = HuggingFaceRealtimeHandler(ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock()))
    handler._conversation_mode = ConversationMode.ONE_ON_ONE
    handler._startup_greeting_sent = True
    handler.client = _TimedClient(events, tail_delay_s=tail_delay_s, probes=probes)
    monkeypatch.setattr(type(handler.tool_manager), "start_up", MagicMock())
    monkeypatch.setattr(type(handler.tool_manager), "shutdown", AsyncMock())
    return handler


def _speech_started(item_id: str) -> _FakeEvent:
    return _FakeEvent("input_audio_buffer.speech_started", item_id=item_id)


def _speech_stopped(item_id: str) -> _FakeEvent:
    return _FakeEvent("input_audio_buffer.speech_stopped", item_id=item_id)


def _accepted(item_id: str, transcript: str = "今天晚餐要吃什麼") -> _FakeEvent:
    return _FakeEvent(
        "conversation.item.input_audio_transcription.completed",
        item_id=item_id,
        transcript=transcript,
    )


def _failed(item_id: str) -> _FakeEvent:
    return _FakeEvent("conversation.item.input_audio_transcription.failed", item_id=item_id)


def _assert_owed_answer_logged(caplog: pytest.LogCaptureFixture, reason: str) -> None:
    line = f"turn hold-off: continuation produced no turn ({reason}); answering the held turn"
    assert line in caplog.text


def _patch_fake_monotonic(monkeypatch: pytest.MonkeyPatch, clock: _FakeClock) -> None:
    monkeypatch.setattr(
        hf_mod,
        "time",
        SimpleNamespace(monotonic=clock.monotonic, perf_counter=time.perf_counter),
    )


def _messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [record.getMessage() for record in caplog.records]


async def _wait_until(predicate: Callable[[], bool], *, timeout_s: float = 1.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.001)
    raise AssertionError("condition was not met before timeout")


def test_commit_holdoff_default_is_700(monkeypatch: pytest.MonkeyPatch) -> None:
    """The shipped accepted-turn hold-off is 700 ms."""
    monkeypatch.delenv("REALTIME_COMMIT_HOLDOFF_MS", raising=False)

    assert hf_mod._commit_holdoff_ms() == 700


def test_commit_holdoff_zero_disables(monkeypatch: pytest.MonkeyPatch) -> None:
    """A zero hold-off restores immediate response requests."""
    monkeypatch.setenv("REALTIME_COMMIT_HOLDOFF_MS", "0")

    assert hf_mod._commit_holdoff_ms() == 0


def test_commit_holdoff_clamps_to_supported_range(monkeypatch: pytest.MonkeyPatch) -> None:
    """The hold-off parser clamps to the supported [0, 3000] ms range."""
    monkeypatch.setenv("REALTIME_COMMIT_HOLDOFF_MS", "-1")
    assert hf_mod._commit_holdoff_ms() == 0

    monkeypatch.setenv("REALTIME_COMMIT_HOLDOFF_MS", "3001")
    assert hf_mod._commit_holdoff_ms() == 3000


def test_commit_holdoff_malformed_uses_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Malformed hold-off values fall back to the shipped default."""
    monkeypatch.setenv("REALTIME_COMMIT_HOLDOFF_MS", "later")

    assert hf_mod._commit_holdoff_ms() == 700


@pytest.mark.asyncio
async def test_holdoff_expiry_in_session_enqueues_one_response(monkeypatch: pytest.MonkeyPatch) -> None:
    """A quiet in-session window expires into exactly one queued response."""
    monkeypatch.setenv("REALTIME_COMMIT_HOLDOFF_MS", "20")
    handler = _handler(
        monkeypatch,
        ((0.0, _speech_started("item_1")), (0.0, _accepted("item_1"))),
        tail_delay_s=0.06,
    )

    await handler._run_realtime_session()

    assert handler._pending_responses.qsize() == 1


@pytest.mark.asyncio
async def test_speech_started_inside_holdoff_skips_response_and_logs(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Renewed speech inside the window skips the pending response."""
    monkeypatch.setenv("REALTIME_COMMIT_HOLDOFF_MS", "60")
    handler = _handler(
        monkeypatch,
        (
            (0.0, _speech_started("item_1")),
            (0.0, _accepted("item_1")),
            (0.01, _speech_started("item_2")),
        ),
        tail_delay_s=0.08,
    )

    with caplog.at_level(logging.INFO, logger="reachy_companion.huggingface_realtime"):
        await handler._run_realtime_session()

    assert handler._pending_responses.qsize() == 0
    assert "turn hold-off: awaiting continuation (" in caplog.text


@pytest.mark.asyncio
async def test_speech_started_holdoff_cancel_logs_gap_and_held_from_fake_clock(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A renewed speech start journals the observed gap and held window age."""
    monkeypatch.setenv("REALTIME_COMMIT_HOLDOFF_MS", "1000")
    clock = _FakeClock()
    _patch_fake_monotonic(monkeypatch, clock)
    handler = _handler(
        monkeypatch,
        (
            (0.0, _speech_started("item_1")),
            (0.0, _speech_stopped("item_1")),
            (0.0, _accepted("item_1")),
            (0.01, _speech_started("item_2")),
        ),
        tail_delay_s=0.02,
        probes={
            0: lambda: clock.set(10.0),
            1: lambda: clock.set(10.5),
            2: lambda: clock.set(10.75),
            3: lambda: clock.set(11.0),
        },
    )

    with caplog.at_level(logging.INFO, logger="reachy_companion.huggingface_realtime"):
        await handler._run_realtime_session()

    assert handler._pending_responses.qsize() == 0
    assert "turn hold-off: awaiting continuation (speech_started) gap=500 held=250" in _messages(caplog)


@pytest.mark.asyncio
async def test_cancelled_window_empty_continuation_answers_held_turn(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A cough/empty continuation inside the window must not eat the accepted turn."""
    monkeypatch.setenv("REALTIME_COMMIT_HOLDOFF_MS", "20")
    handler = _handler(
        monkeypatch,
        (
            (0.0, _speech_started("item_1")),
            (0.0, _accepted("item_1")),
            (0.005, _speech_started("item_2")),
            (0.0, _accepted("item_2", "   ")),
        ),
        tail_delay_s=0.06,
    )

    with caplog.at_level(logging.INFO, logger="reachy_companion.huggingface_realtime"):
        await handler._run_realtime_session()

    assert handler._pending_responses.qsize() == 1
    _assert_owed_answer_logged(caplog, "empty transcript")


@pytest.mark.asyncio
async def test_cancelled_window_failed_continuation_answers_held_turn(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A failed continuation transcript must release the held accepted answer."""
    monkeypatch.setenv("REALTIME_COMMIT_HOLDOFF_MS", "20")
    handler = _handler(
        monkeypatch,
        (
            (0.0, _speech_started("item_1")),
            (0.0, _accepted("item_1")),
            (0.005, _speech_started("item_2")),
            (0.0, _failed("item_2")),
        ),
        tail_delay_s=0.06,
    )

    with caplog.at_level(logging.INFO, logger="reachy_companion.huggingface_realtime"):
        await handler._run_realtime_session()

    assert handler._pending_responses.qsize() == 1
    _assert_owed_answer_logged(caplog, "transcription failed")


@pytest.mark.asyncio
async def test_cancelled_window_gate_denied_continuation_answers_held_turn_without_music_no_response(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A denied continuation stays as context but must not close music when the held turn answers."""
    monkeypatch.setenv("REALTIME_COMMIT_HOLDOFF_MS", "20")
    monkeypatch.setenv("REALTIME_ONE_ON_ONE_ANSWER_GATE", "name_only")
    without_response = MagicMock()
    monkeypatch.setattr(hf_mod, "on_turn_without_response", without_response)
    handler = _handler(
        monkeypatch,
        (
            (0.0, _speech_started("item_1")),
            (0.0, _accepted("item_1", "瑞奇今天晚餐吃什麼")),
            (0.005, _speech_started("item_2")),
            (0.0, _accepted("item_2", "我們晚餐要吃什麼呢")),
        ),
        tail_delay_s=0.06,
    )

    with caplog.at_level(logging.INFO, logger="reachy_companion.huggingface_realtime"):
        await handler._run_realtime_session()

    assert handler._pending_responses.qsize() == 1
    without_response.assert_not_called()
    _assert_owed_answer_logged(caplog, "gate denied")


@pytest.mark.asyncio
async def test_cancelled_window_rollback_continuation_answers_held_turn(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A solo-barge rollback continuation must not swallow the held answer."""
    monkeypatch.setenv("REALTIME_COMMIT_HOLDOFF_MS", "20")
    monkeypatch.setattr(hf_mod, "on_turn_without_response", lambda _deps: None)
    calls = 0

    def _plant_rollback_on_continuation(self: HuggingFaceRealtimeHandler) -> None:
        nonlocal calls
        calls += 1
        if calls != 2:
            return
        self._barge_pending = True
        self._barge_paused = True
        self._barge_paused_response_id = "resp_held"

    monkeypatch.setattr(HuggingFaceRealtimeHandler, "_solo_speech_started", _plant_rollback_on_continuation)
    handler = _handler(
        monkeypatch,
        (
            (0.0, _speech_started("item_1")),
            (0.0, _accepted("item_1")),
            (0.005, _speech_started("item_2")),
            (0.0, _accepted("item_2", "嗯")),
        ),
        tail_delay_s=0.06,
    )

    with caplog.at_level(logging.INFO, logger="reachy_companion.huggingface_realtime"):
        await handler._run_realtime_session()

    assert handler._pending_responses.qsize() == 1
    _assert_owed_answer_logged(caplog, "solo barge rollback")


@pytest.mark.asyncio
async def test_later_segment_already_started_at_acceptance_skips_immediately(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A later speech-start already observed at acceptance skips immediately."""
    monkeypatch.setenv("REALTIME_COMMIT_HOLDOFF_MS", "20")
    handler = _handler(
        monkeypatch,
        (
            (0.0, _speech_started("item_1")),
            (0.0, _speech_started("item_2")),
            (0.0, _accepted("item_1")),
        ),
        tail_delay_s=0.05,
    )

    with caplog.at_level(logging.INFO, logger="reachy_companion.huggingface_realtime"):
        await handler._run_realtime_session()

    assert handler._pending_responses.qsize() == 0
    assert "turn hold-off: awaiting continuation (" in caplog.text


@pytest.mark.asyncio
async def test_later_segment_acceptance_skip_logs_gap_from_fake_clock(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The acceptance-time skip journals the continuation gap."""
    monkeypatch.setenv("REALTIME_COMMIT_HOLDOFF_MS", "20")
    clock = _FakeClock()
    _patch_fake_monotonic(monkeypatch, clock)
    handler = _handler(
        monkeypatch,
        (
            (0.0, _speech_started("item_1")),
            (0.0, _speech_stopped("item_1")),
            (0.0, _speech_started("item_2")),
            (0.0, _accepted("item_1")),
        ),
        tail_delay_s=0.03,
        probes={
            0: lambda: clock.set(20.0),
            1: lambda: clock.set(20.5),
            2: lambda: clock.set(20.75),
            3: lambda: clock.set(21.0),
        },
    )

    with caplog.at_level(logging.INFO, logger="reachy_companion.huggingface_realtime"):
        await handler._run_realtime_session()

    assert handler._pending_responses.qsize() == 0
    assert "turn hold-off: awaiting continuation (later speech already started) gap=250" in _messages(caplog)


@pytest.mark.asyncio
async def test_immediate_skip_empty_continuation_answers_held_turn(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """If later speech already started, an empty continuation still releases the held answer."""
    monkeypatch.setenv("REALTIME_COMMIT_HOLDOFF_MS", "20")
    handler = _handler(
        monkeypatch,
        (
            (0.0, _speech_started("item_1")),
            (0.0, _speech_started("item_2")),
            (0.0, _accepted("item_1")),
            (0.0, _accepted("item_2", "")),
        ),
        tail_delay_s=0.06,
    )

    with caplog.at_level(logging.INFO, logger="reachy_companion.huggingface_realtime"):
        await handler._run_realtime_session()

    assert handler._pending_responses.qsize() == 1
    _assert_owed_answer_logged(caplog, "empty transcript")


@pytest.mark.asyncio
async def test_newer_accepted_turn_supersedes_pending_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """A newer accepted item owns the answer window for consecutive turns."""
    monkeypatch.setenv("REALTIME_COMMIT_HOLDOFF_MS", "50")
    handler = _handler(
        monkeypatch,
        (
            (0.0, _speech_started("item_1")),
            (0.0, _accepted("item_1")),
            (0.01, _accepted("item_2")),
        ),
        tail_delay_s=0.08,
    )

    await handler._run_realtime_session()

    assert handler._pending_responses.qsize() == 1


@pytest.mark.asyncio
async def test_accepted_continuation_answers_once_after_cancelling_prior_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An accepted continuation owns the single merged answer request."""
    monkeypatch.setenv("REALTIME_COMMIT_HOLDOFF_MS", "20")
    handler = _handler(
        monkeypatch,
        (
            (0.0, _speech_started("item_1")),
            (0.0, _accepted("item_1")),
            (0.005, _speech_started("item_2")),
            (0.0, _accepted("item_2", "那如果吃麵呢")),
        ),
        tail_delay_s=0.06,
    )

    await handler._run_realtime_session()

    assert handler._pending_responses.qsize() == 1


@pytest.mark.asyncio
async def test_teardown_window_does_not_enqueue_into_next_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """A pending hold-off from a dead session cannot answer in the next one."""
    monkeypatch.setenv("REALTIME_COMMIT_HOLDOFF_MS", "50")
    handler = _handler(
        monkeypatch,
        ((0.0, _speech_started("item_1")), (0.0, _accepted("item_1"))),
    )

    await handler._run_realtime_session()
    handler.client = _TimedClient((), tail_delay_s=0.08)
    await handler._run_realtime_session()

    assert handler._pending_responses.qsize() == 0


@pytest.mark.asyncio
async def test_external_interrupt_clears_owed_holdoff_before_empty_continuation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An external interrupt takes ownership, so the held turn must stay silent afterward."""
    monkeypatch.setenv("REALTIME_COMMIT_HOLDOFF_MS", "60")
    handler = _handler(
        monkeypatch,
        (
            (0.0, _speech_started("item_1")),
            (0.0, _accepted("item_1")),
            (0.005, _speech_started("item_2")),
            (0.04, _accepted("item_2", "")),
        ),
        tail_delay_s=0.08,
    )

    session = asyncio.create_task(handler._run_realtime_session())
    await _wait_until(lambda: bool(getattr(handler, "_holdoff_owed", False)))
    handler.on_external_interrupt()
    await session

    assert handler._pending_responses.qsize() == 0


@pytest.mark.asyncio
async def test_external_interrupt_cancels_pending_holdoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """External interrupts cancel a pending accepted-turn hold-off."""
    monkeypatch.setenv("REALTIME_COMMIT_HOLDOFF_MS", "60")
    handler = _handler(
        monkeypatch,
        ((0.0, _speech_started("item_1")), (0.0, _accepted("item_1"))),
        tail_delay_s=0.08,
    )

    session = asyncio.create_task(handler._run_realtime_session())
    await _wait_until(lambda: getattr(handler, "_holdoff_task", None) is not None)
    handler.on_external_interrupt()
    await session

    assert handler._pending_responses.qsize() == 0


@pytest.mark.asyncio
async def test_late_continuation_after_expired_window_logs_once_within_probe_window(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A start just after expiry is the plan rev 3 A1 calibration signal."""
    monkeypatch.setenv("REALTIME_COMMIT_HOLDOFF_MS", "20")
    clock = _FakeClock()
    _patch_fake_monotonic(monkeypatch, clock)
    handler = _handler(
        monkeypatch,
        (
            (0.0, _speech_started("item_1")),
            (0.0, _speech_stopped("item_1")),
            (0.0, _accepted("item_1")),
            (0.15, _speech_started("item_2")),
            (0.0, _speech_started("item_3")),
        ),
        tail_delay_s=0.02,
        probes={
            0: lambda: clock.set(50.0),
            1: lambda: clock.set(50.5),
            2: lambda: clock.set(51.0),
        },
    )

    with caplog.at_level(logging.INFO, logger="reachy_companion.huggingface_realtime"):
        session = asyncio.create_task(handler._run_realtime_session())
        await _wait_until(lambda: getattr(handler, "_holdoff_task", None) is not None)
        clock.set(51.02)
        await _wait_until(lambda: getattr(handler, "_holdoff_fired_at", None) is not None)
        clock.set(51.145)
        await session

    line = "turn hold-off: late continuation 125 ms after the window (window=20 ms)"
    assert _messages(caplog).count(line) == 1


@pytest.mark.asyncio
async def test_late_continuation_after_expired_window_beyond_probe_window_is_quiet(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A start beyond the calibration window does not journal a too-short signal."""
    monkeypatch.setenv("REALTIME_COMMIT_HOLDOFF_MS", "20")
    clock = _FakeClock()
    _patch_fake_monotonic(monkeypatch, clock)
    handler = _handler(
        monkeypatch,
        (
            (0.0, _speech_started("item_1")),
            (0.0, _speech_stopped("item_1")),
            (0.0, _accepted("item_1")),
            (0.15, _speech_started("item_2")),
        ),
        tail_delay_s=0.02,
        probes={
            0: lambda: clock.set(60.0),
            1: lambda: clock.set(60.5),
            2: lambda: clock.set(61.0),
        },
    )

    with caplog.at_level(logging.INFO, logger="reachy_companion.huggingface_realtime"):
        session = asyncio.create_task(handler._run_realtime_session())
        await _wait_until(lambda: getattr(handler, "_holdoff_task", None) is not None)
        clock.set(61.02)
        await _wait_until(lambda: getattr(handler, "_holdoff_fired_at", None) is not None)
        clock.set(63.021)
        await session

    assert "turn hold-off: late continuation" not in caplog.text


@pytest.mark.asyncio
async def test_external_interrupt_clears_expired_holdoff_marker(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """External ownership clears the fired-window calibration marker."""
    monkeypatch.setenv("REALTIME_COMMIT_HOLDOFF_MS", "20")
    clock = _FakeClock()
    _patch_fake_monotonic(monkeypatch, clock)
    handler = _handler(
        monkeypatch,
        (
            (0.0, _speech_started("item_1")),
            (0.0, _speech_stopped("item_1")),
            (0.0, _accepted("item_1")),
            (0.15, _speech_started("item_2")),
        ),
        tail_delay_s=0.02,
        probes={
            0: lambda: clock.set(70.0),
            1: lambda: clock.set(70.5),
            2: lambda: clock.set(71.0),
        },
    )

    with caplog.at_level(logging.INFO, logger="reachy_companion.huggingface_realtime"):
        session = asyncio.create_task(handler._run_realtime_session())
        await _wait_until(lambda: getattr(handler, "_holdoff_task", None) is not None)
        clock.set(71.02)
        await _wait_until(lambda: getattr(handler, "_holdoff_fired_at", None) is not None)
        handler.on_external_interrupt()
        assert getattr(handler, "_holdoff_fired_at", None) is None
        clock.set(71.145)
        await session

    assert "turn hold-off: late continuation" not in caplog.text


@pytest.mark.asyncio
async def test_holdoff_skip_never_calls_turn_without_response(monkeypatch: pytest.MonkeyPatch) -> None:
    """A hold-off skip is not the denied-turn music-resume path."""
    monkeypatch.setenv("REALTIME_COMMIT_HOLDOFF_MS", "20")
    without_response = MagicMock()
    monkeypatch.setattr(hf_mod, "on_turn_without_response", without_response)
    handler = _handler(
        monkeypatch,
        (
            (0.0, _speech_started("item_1")),
            (0.0, _speech_started("item_2")),
            (0.0, _accepted("item_1")),
        ),
        tail_delay_s=0.05,
    )

    await handler._run_realtime_session()

    without_response.assert_not_called()
    assert handler._pending_responses.qsize() == 0


@pytest.mark.asyncio
async def test_zero_holdoff_enqueues_synchronously(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the knob at zero, the request is queued before the next event."""
    monkeypatch.setenv("REALTIME_COMMIT_HOLDOFF_MS", "0")
    seen: list[tuple[int, object | None]] = []
    handler: HuggingFaceRealtimeHandler | None = None

    def _probe_after_acceptance() -> None:
        assert handler is not None
        seen.append((handler._pending_responses.qsize(), getattr(handler, "_holdoff_task", None)))

    handler = _handler(
        monkeypatch,
        (
            (0.0, _speech_started("item_1")),
            (0.0, _accepted("item_1")),
            (0.0, _FakeEvent("session.updated")),
        ),
        probes={2: _probe_after_acceptance},
    )

    await handler._run_realtime_session()

    assert seen == [(1, None)]
    assert handler._pending_responses.qsize() == 1
