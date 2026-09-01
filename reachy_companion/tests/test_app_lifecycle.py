import time
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, call

import numpy as np
import pytest

from reachy_mini.reachy_mini import SLEEP_HEAD_POSE
from reachy_companion import app_lifecycle
from reachy_companion.tools.core_tools import ToolDependencies


def test_request_stop_current_app_posts_to_daemon(monkeypatch) -> None:
    """The app stop request should call the connected Reachy daemon endpoint."""

    class FakeResponse:
        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, *_args: object) -> None:
            pass

        def read(self) -> bytes:
            return b"{}"

    def fake_urlopen(request, timeout):
        assert request.full_url == "http://192.168.1.42:8000/api/apps/stop-current-app"
        assert request.get_method() == "POST"
        assert timeout == 2.0
        return FakeResponse()

    monkeypatch.setattr(app_lifecycle.urllib.request, "urlopen", fake_urlopen)
    robot = SimpleNamespace(client=SimpleNamespace(host="192.168.1.42", port=8000))

    assert app_lifecycle.request_stop_current_app(robot, MagicMock())


def test_wake_up_if_sleeping_enables_motors_before_wake_up() -> None:
    """Startup should enable sleeping motors before playing the wake-up movement."""
    robot = MagicMock()
    robot.get_current_head_pose.return_value = SLEEP_HEAD_POSE.copy()

    assert app_lifecycle.wake_up_if_sleeping(robot, MagicMock())

    robot.get_current_joint_positions.assert_not_called()
    assert robot.method_calls == [
        call.get_current_head_pose(),
        call.enable_motors(),
        call.wake_up(),
    ]


def test_wake_up_if_sleeping_skips_non_sleep_head_pose() -> None:
    """Startup should leave an already-awake robot alone."""
    robot = MagicMock()
    robot.get_current_head_pose.return_value = np.eye(4)

    assert not app_lifecycle.wake_up_if_sleeping(robot, MagicMock())

    robot.get_current_joint_positions.assert_not_called()
    robot.enable_motors.assert_not_called()
    robot.wake_up.assert_not_called()


def test_run_lifecycle_sleep_silences_then_poses_directly() -> None:
    """Inactivity and shutdown have no model turn, so they never wait for a goodbye.

    Since the instructing wave the `go_to_sleep` TOOL only silences the inputs and
    hands the turn back for a spoken farewell. These paths have nobody to speak, so
    they silence and pose themselves (Codex round 1, critical catch 3).
    """
    order: list[str] = []
    expected = {"status": "sleeping"}
    deps = ToolDependencies(
        reachy_mini=MagicMock(),
        movement_manager=MagicMock(),
        begin_sleep=lambda: order.append("silence"),
        go_to_sleep=lambda: (order.append("sleep"), expected)[1],
    )

    result = app_lifecycle.run_lifecycle_sleep(deps, MagicMock())

    assert order == ["silence", "sleep"]
    assert result == expected


def test_run_lifecycle_sleep_still_poses_when_silencing_fails() -> None:
    """Best-effort input quiesce must not prevent the actual sleep pose."""
    order: list[str] = []
    expected = {"status": "sleeping"}

    def _boom() -> None:
        order.append("silence")
        raise RuntimeError("stream gone")

    deps = ToolDependencies(
        reachy_mini=MagicMock(),
        movement_manager=MagicMock(),
        begin_sleep=_boom,
        go_to_sleep=lambda: (order.append("sleep"), expected)[1],
    )

    result = app_lifecycle.run_lifecycle_sleep(deps, MagicMock())

    assert order == ["silence", "sleep"]
    assert result == expected


def test_run_lifecycle_sleep_reports_an_unwired_runtime() -> None:
    """No sleep callback means no sleep — say so instead of pretending."""
    deps = ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())

    assert app_lifecycle.run_lifecycle_sleep(deps, MagicMock()) == {
        "error": "go_to_sleep is unavailable in this runtime"
    }


def test_run_lifecycle_sleep_survives_a_failing_pose() -> None:
    """A raising closure must not kill the inactivity thread."""

    def _boom() -> dict[str, object]:
        raise RuntimeError("motors offline")

    deps = ToolDependencies(
        reachy_mini=MagicMock(),
        movement_manager=MagicMock(),
        go_to_sleep=_boom,
    )

    result = app_lifecycle.run_lifecycle_sleep(deps, MagicMock())

    assert result == {"error": "go_to_sleep failed: RuntimeError: motors offline"}


# --------------------------------------------------------------------------
# Sleep quiesce (2026-08-31 plan, Task 9)
# --------------------------------------------------------------------------


def _quiesce_stream(handler: object | None = None) -> SimpleNamespace:
    return SimpleNamespace(_mic_muted=False, handler=handler)


def test_begin_sleep_quiesce_mutes_the_mic_and_disarms_the_barge_machine():
    """Step 2 and 3, and they come BEFORE any waiting (Codex round 2, 2a-6).

    Waiting for `response.done` with the microphone still live would leave up to
    ten seconds in which a repeated 「睡覺吧」 or the goodbye's own echo opens a
    turn the robot will never answer.
    """
    from reachy_companion import app_lifecycle

    calls: list[str] = []
    stream = _quiesce_stream(SimpleNamespace(on_external_interrupt=lambda: calls.append("disarm")))
    app_lifecycle.begin_sleep_quiesce(stream, logging.getLogger("test"))
    assert stream._mic_muted is True
    assert calls == ["disarm"]


def test_begin_sleep_quiesce_never_flushes_the_player():
    """`clear_audio_queue` would kill the very goodbye we are protecting."""
    from reachy_companion import app_lifecycle

    flushed: list[str] = []
    stream = _quiesce_stream(SimpleNamespace(on_external_interrupt=lambda: None))
    stream.clear_audio_queue = lambda: flushed.append("flush")
    app_lifecycle.begin_sleep_quiesce(stream, logging.getLogger("test"))
    assert flushed == []


def test_begin_sleep_quiesce_tolerates_a_missing_stream_and_handler():
    """Silencing is best-effort: no stream, or a handler without the seam."""
    from reachy_companion import app_lifecycle

    app_lifecycle.begin_sleep_quiesce(None, logging.getLogger("test"))
    app_lifecycle.begin_sleep_quiesce(_quiesce_stream(object()), logging.getLogger("test"))


def test_wait_for_speaker_quiet_stops_as_soon_as_it_is_quiet(monkeypatch):
    """The goodbye finishes playing, then we stop waiting."""
    from reachy_companion import app_lifecycle
    from reachy_companion.hanova import audio_drain

    audible = iter([True, True, False, False, False])
    monkeypatch.setattr(audio_drain, "is_audible", lambda: next(audible, False))
    monkeypatch.setattr(app_lifecycle, "SLEEP_DRAIN_POLL_S", 0.001)
    waited = app_lifecycle.wait_for_speaker_quiet(logging.getLogger("test"))
    assert waited >= 0.0
    assert next(audible, "exhausted") != "exhausted"  # the loop stopped early


def test_wait_for_speaker_quiet_is_bounded_by_the_cap(monkeypatch, caplog):
    """A stuck drain estimate must not hold the robot awake forever.

    And the outcome is logged honestly: the cap expiring with audio still
    playing is not "speaker quiet" (Codex round 1, P2-11).
    """
    from reachy_companion import app_lifecycle
    from reachy_companion.hanova import audio_drain

    monkeypatch.setenv("SLEEP_GOODBYE_DRAIN_CAP_S", "0.05")
    monkeypatch.setattr(audio_drain, "is_audible", lambda: True)
    monkeypatch.setattr(app_lifecycle, "SLEEP_DRAIN_POLL_S", 0.001)
    with caplog.at_level(logging.INFO):
        waited = app_lifecycle.wait_for_speaker_quiet(logging.getLogger("test"))
    assert 0.04 <= waited <= 1.0
    assert "drain cap reached" in caplog.text
    assert "speaker quiet" not in caplog.text


def test_wait_for_speaker_quiet_reports_a_real_drain_as_quiet(monkeypatch, caplog):
    """Already quiet: the journal says so, and says nothing about a cap."""
    from reachy_companion import app_lifecycle
    from reachy_companion.hanova import audio_drain

    monkeypatch.setattr(audio_drain, "is_audible", lambda: False)
    with caplog.at_level(logging.INFO):
        app_lifecycle.wait_for_speaker_quiet(logging.getLogger("test"))
    assert "speaker quiet" in caplog.text
    assert "drain cap reached" not in caplog.text


@pytest.mark.asyncio
async def test_the_sleep_tool_silences_first_then_waits_then_sleeps() -> None:
    """The whole ordering claim, as one assertion (Codex round 2, 2a-6).

    Silence the inputs, THEN wait for the goodbye to finish generating, THEN
    hand off to the thread that drains and poses. Waiting first leaves the mic
    live; draining first measures audio that does not exist yet.
    """
    from reachy_companion.tools.go_to_sleep import GoToSleep

    order: list[str] = []

    async def _wait() -> bool:
        order.append("wait")
        return True

    deps = SimpleNamespace(
        begin_sleep=lambda: order.append("silence"),
        wait_for_reply_finished=_wait,
        go_to_sleep=lambda: (order.append("sleep"), {"status": "sleeping"})[1],
    )
    result = await GoToSleep()(deps)
    assert order == ["silence", "wait", "sleep"]
    assert result == {"status": "sleeping"}


@pytest.mark.asyncio
async def test_the_sleep_tool_still_sleeps_if_the_wait_times_out() -> None:
    """A reply that never ends must not leave the robot permanently awake."""
    from reachy_companion.tools.go_to_sleep import GoToSleep

    async def _wait() -> bool:
        return False

    calls: list[str] = []
    deps = SimpleNamespace(
        begin_sleep=lambda: None,
        wait_for_reply_finished=_wait,
        go_to_sleep=lambda: (calls.append("sleep"), {"status": "sleeping"})[1],
    )
    assert (await GoToSleep()(deps))["status"] == "sleeping"
    assert calls == ["sleep"]


@pytest.mark.asyncio
async def test_the_sleep_tool_works_without_the_new_seams() -> None:
    """Older construction sites keep working with both seams simply absent."""
    from reachy_companion.tools.go_to_sleep import GoToSleep

    deps = SimpleNamespace(
        begin_sleep=None, wait_for_reply_finished=None, go_to_sleep=lambda: {"status": "sleeping"}
    )
    assert (await GoToSleep()(deps))["status"] == "sleeping"


def test_wait_for_reply_finished_is_safe_from_another_loop() -> None:
    """The wait seam handles callers outside the handler's event loop.

    Awaiting the handler's `asyncio.Event` directly from another loop is
    undefined (Codex round 2, 2a-5). The seam must marshal, or give up cleanly.
    """
    import asyncio as _asyncio
    import threading

    from reachy_companion.openai_realtime import OpenAIRealtimeHandler

    handler = OpenAIRealtimeHandler.__new__(OpenAIRealtimeHandler)
    handler.connection = object()  # a live session; see the dead-session test below
    results: list[bool] = []
    ready = threading.Event()
    stop = threading.Event()

    def _run_handler_loop() -> None:
        loop = _asyncio.new_event_loop()
        _asyncio.set_event_loop(loop)

        async def _session() -> None:
            # Built and signalled from INSIDE the running loop, exactly as the
            # real handler does. Signalling before `run_until_complete` would
            # release the caller while the loop was still stopped, and the
            # `is_running()` guard would then answer from the dead-session
            # branch — passing this test without ever marshalling anything.
            handler._response_done_event = _asyncio.Event()
            handler._handler_loop = loop
            ready.set()
            await _asyncio.sleep(0.05)
            handler._response_done_event.set()  # `response.done` arrives
            await _asyncio.sleep(0.15)

        loop.run_until_complete(_session())
        stop.set()
        loop.close()

    thread = threading.Thread(target=_run_handler_loop, daemon=True)
    thread.start()
    ready.wait(timeout=2.0)
    # A DIFFERENT loop from the handler's loop.
    started = time.monotonic()
    results.append(_asyncio.run(handler.wait_for_reply_finished()))
    waited = time.monotonic() - started
    stop.wait(timeout=2.0)
    thread.join(timeout=2.0)
    assert results == [True]
    # It really marshalled and really waited: the handler's loop does not set
    # the event until 50 ms in. A short-circuit would return in microseconds.
    assert waited >= 0.04


def test_wait_for_reply_finished_gives_up_when_the_loop_is_gone() -> None:
    """No handler loop at all: report success rather than hang or raise."""
    import asyncio as _asyncio

    from reachy_companion.openai_realtime import OpenAIRealtimeHandler

    handler = OpenAIRealtimeHandler.__new__(OpenAIRealtimeHandler)
    handler.connection = object()
    handler._response_done_event = _asyncio.Event()
    handler._handler_loop = None
    assert _asyncio.run(handler.wait_for_reply_finished()) is True


@pytest.mark.asyncio
async def test_wait_for_reply_finished_does_not_wait_on_a_dead_session() -> None:
    """A live loop, an unset event, and no connection: still instant.

    `_response_done_event` is cleared by `response.created` and set by
    `response.done`; a session that dies mid-response leaves it clear forever,
    and without this the shutdown path pays the full ten seconds every time
    (Codex round 3, finding 2).
    """
    import asyncio as _asyncio

    from reachy_companion.openai_realtime import OpenAIRealtimeHandler

    handler = OpenAIRealtimeHandler.__new__(OpenAIRealtimeHandler)
    handler.connection = None
    handler._response_done_event = _asyncio.Event()  # deliberately NOT set
    handler._handler_loop = _asyncio.get_running_loop()
    started = _asyncio.get_running_loop().time()
    assert await handler.wait_for_reply_finished() is True
    assert _asyncio.get_running_loop().time() - started < 1.0


def test_sleep_drain_cap_clamps(monkeypatch):
    """The knob defaults to 6.0s and clamps into 0.0-15.0."""
    from reachy_companion import app_lifecycle

    monkeypatch.delenv("SLEEP_GOODBYE_DRAIN_CAP_S", raising=False)
    assert app_lifecycle.sleep_drain_cap_s() == 6.0
    monkeypatch.setenv("SLEEP_GOODBYE_DRAIN_CAP_S", "999")
    assert app_lifecycle.sleep_drain_cap_s() == 15.0
    monkeypatch.setenv("SLEEP_GOODBYE_DRAIN_CAP_S", "nonsense")
    assert app_lifecycle.sleep_drain_cap_s() == 6.0
