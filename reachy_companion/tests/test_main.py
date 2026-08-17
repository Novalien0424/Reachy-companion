"""Tests for app-level runtime behavior."""

import time
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import reachy_companion.main as main_mod
from reachy_companion import config as config_mod


@pytest.fixture
def stubbed_run(monkeypatch):
    """Run `main.run` end to end against mocked robot, movement, stream and handler."""
    movement_manager = MagicMock(name="MovementManager instance")
    handler_cls = MagicMock(name="OpenAIRealtimeHandler")
    stream_cls = MagicMock(name="LocalStream")

    monkeypatch.setattr("reachy_companion.moves.MovementManager", MagicMock(return_value=movement_manager))
    monkeypatch.setattr("reachy_companion.console.LocalStream", stream_cls)
    monkeypatch.setattr("reachy_companion.openai_realtime.OpenAIRealtimeHandler", handler_cls)
    monkeypatch.setattr("reachy_companion.tools.core_tools.initialize_tools", MagicMock())
    # Keep the inactivity watchdog and the shutdown settle out of the test.
    monkeypatch.setenv(config_mod.APP_TIMEOUT_MINUTES_ENV, "0")
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)

    args = SimpleNamespace(debug=False, robot_name=None, no_camera=True, ui=False, command=None)
    main_mod.run(args, robot=MagicMock(name="ReachyMini"))

    return SimpleNamespace(movement_manager=movement_manager, handler_cls=handler_cls, stream_cls=stream_cls)


def test_run_builds_the_openai_realtime_handler(stubbed_run) -> None:
    """The handler factory constructs the OpenAI backend, and the stream gets it."""
    stubbed_run.handler_cls.assert_called_once()
    assert stubbed_run.stream_cls.call_args.args[0] is stubbed_run.handler_cls.return_value


def test_run_enables_head_tracking_at_startup(stubbed_run) -> None:
    """Head tracking is on from startup with no model tool call, right after the manager starts (US-02)."""
    method_names = [name for name, _args, _kwargs in stubbed_run.movement_manager.method_calls]

    assert ("set_head_tracking", (True,), {}) in stubbed_run.movement_manager.method_calls
    assert method_names.index("start") < method_names.index("set_head_tracking")


def test_inactivity_timeout_thread_goes_to_sleep() -> None:
    """The watchdog should use the shared sleep shutdown path once activity is too old."""
    stream_manager = SimpleNamespace(seconds_since_activity=lambda: 10.0, close=MagicMock())
    go_to_sleep = MagicMock(return_value={"status": "sleeping"})

    thread = main_mod._start_inactivity_timeout_thread(
        timeout_minutes=0.0001,
        stream_manager=stream_manager,
        logger=MagicMock(),
        app_stop_event=threading.Event(),
        go_to_sleep=go_to_sleep,
    )

    thread.join(timeout=1.0)
    assert not thread.is_alive()
    go_to_sleep.assert_called_once_with()
    stream_manager.close.assert_not_called()


def test_inactivity_timeout_thread_closes_stream_manager_without_sleep_callback() -> None:
    """The watchdog should still close the stream when no sleep callback is available."""
    stream_manager = SimpleNamespace(seconds_since_activity=lambda: 10.0, close=MagicMock())

    thread = main_mod._start_inactivity_timeout_thread(
        timeout_minutes=0.0001,
        stream_manager=stream_manager,
        logger=MagicMock(),
        app_stop_event=threading.Event(),
    )

    thread.join(timeout=1.0)
    assert not thread.is_alive()
    stream_manager.close.assert_called_once_with()
