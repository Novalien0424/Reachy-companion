"""Tests for app-level runtime behavior."""

import time
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import reachy_companion.main as main_mod
from reachy_companion import config as config_mod


def _run_app(monkeypatch, *, no_camera=True, instance_path=None):
    """Run `main.run` end to end against mocked robot, movement, stream, handler and recognizer."""
    movement_manager = MagicMock(name="MovementManager instance")
    handler_cls = MagicMock(name="OpenAIRealtimeHandler")
    stream_cls = MagicMock(name="LocalStream")
    recognizer_cls = MagicMock(name="FaceRecognizer")

    monkeypatch.setattr("reachy_companion.moves.MovementManager", MagicMock(return_value=movement_manager))
    monkeypatch.setattr("reachy_companion.console.LocalStream", stream_cls)
    monkeypatch.setattr("reachy_companion.openai_realtime.OpenAIRealtimeHandler", handler_cls)
    monkeypatch.setattr("reachy_companion.tools.core_tools.initialize_tools", MagicMock())
    # Never let a test build the real ONNX sessions: that would download 37 MB
    # and spawn a warm-up thread inside the suite.
    monkeypatch.setattr("reachy_companion.face_id.FaceRecognizer", recognizer_cls)
    # Keep the inactivity watchdog and the shutdown settle out of the test.
    monkeypatch.setenv(config_mod.APP_TIMEOUT_MINUTES_ENV, "0")
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)

    args = SimpleNamespace(debug=False, robot_name=None, no_camera=no_camera, ui=False, command=None)
    main_mod.run(args, robot=MagicMock(name="ReachyMini"), instance_path=instance_path)

    return SimpleNamespace(
        movement_manager=movement_manager,
        handler_cls=handler_cls,
        stream_cls=stream_cls,
        recognizer_cls=recognizer_cls,
        deps=handler_cls.call_args.args[0],
    )


@pytest.fixture
def stubbed_run(monkeypatch):
    """Run `main.run` end to end against mocked robot, movement, stream and handler."""
    return _run_app(monkeypatch)


def test_run_builds_the_openai_realtime_handler(stubbed_run) -> None:
    """The handler factory constructs the OpenAI backend, and the stream gets it."""
    stubbed_run.handler_cls.assert_called_once()
    assert stubbed_run.stream_cls.call_args.args[0] is stubbed_run.handler_cls.return_value


def test_run_enables_head_tracking_at_startup(stubbed_run) -> None:
    """Head tracking is on from startup with no model tool call, right after the manager starts (US-02)."""
    method_names = [name for name, _args, _kwargs in stubbed_run.movement_manager.method_calls]

    assert ("set_head_tracking", (True,), {}) in stubbed_run.movement_manager.method_calls
    assert method_names.index("start") < method_names.index("set_head_tracking")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, main_mod.DEFAULT_DAEMON_PORT),
        ("", main_mod.DEFAULT_DAEMON_PORT),
        ("8001", 8001),
        (" 8001 ", 8001),
        ("not-a-port", main_mod.DEFAULT_DAEMON_PORT),  # degrades, never raises
        ("0", 1),  # clamped into the valid TCP range
        ("70000", 65535),
    ],
)
def test_daemon_port_reads_the_env_override(monkeypatch, raw, expected) -> None:
    """REACHY_DAEMON_PORT selects the daemon, and a bad value cannot stop startup (D-008)."""
    if raw is None:
        monkeypatch.delenv("REACHY_DAEMON_PORT", raising=False)
    else:
        monkeypatch.setenv("REACHY_DAEMON_PORT", raw)

    assert main_mod._daemon_port() == expected


def test_run_connects_to_the_daemon_port_from_the_env(monkeypatch) -> None:
    """The override reaches the ReachyMini construction site, the app's only one."""
    reachy_mini_cls = MagicMock(name="ReachyMini")
    monkeypatch.setattr(main_mod, "ReachyMini", reachy_mini_cls)
    monkeypatch.setattr("reachy_companion.moves.MovementManager", MagicMock())
    monkeypatch.setattr("reachy_companion.console.LocalStream", MagicMock())
    monkeypatch.setattr("reachy_companion.openai_realtime.OpenAIRealtimeHandler", MagicMock())
    monkeypatch.setattr("reachy_companion.tools.core_tools.initialize_tools", MagicMock())
    monkeypatch.setenv(config_mod.APP_TIMEOUT_MINUTES_ENV, "0")
    monkeypatch.setenv("REACHY_DAEMON_PORT", "8001")
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)

    args = SimpleNamespace(debug=False, robot_name=None, no_camera=True, ui=False, command=None)
    main_mod.run(args)

    assert reachy_mini_cls.call_args.kwargs["port"] == 8001


def test_run_wires_face_memory_and_warms_it_up(monkeypatch, tmp_path) -> None:
    """The recognizer is built once, injected into the tools, and warmed before the first session (D-013)."""
    monkeypatch.delenv("FACE_MEMORY_ENABLED", raising=False)

    result = _run_app(monkeypatch, no_camera=False, instance_path=str(tmp_path))

    result.recognizer_cls.assert_called_once_with(str(tmp_path), enabled=True)
    assert result.deps.face_recognizer is result.recognizer_cls.return_value
    result.recognizer_cls.return_value.start_warmup.assert_called_once_with()


def test_face_memory_kill_switch_skips_the_model_entirely(monkeypatch, tmp_path) -> None:
    """FACE_MEMORY_ENABLED=0 must load no model and start no warm-up thread (correction 3)."""
    monkeypatch.setenv("FACE_MEMORY_ENABLED", "0")

    result = _run_app(monkeypatch, no_camera=False, instance_path=str(tmp_path))

    result.recognizer_cls.assert_called_once_with(str(tmp_path), enabled=False)
    result.recognizer_cls.return_value.start_warmup.assert_not_called()
    assert result.deps.face_recognizer is result.recognizer_cls.return_value


def test_face_memory_does_not_warm_up_without_a_camera(monkeypatch, tmp_path) -> None:
    """`--no-camera` has no frames to recognize, so the 37 MB model is never read."""
    result = _run_app(monkeypatch, no_camera=True, instance_path=str(tmp_path))

    result.recognizer_cls.return_value.start_warmup.assert_not_called()


def test_run_names_the_persona_source_at_startup(monkeypatch, tmp_path) -> None:
    """Startup logs which persona is in play, for the instance it was given (D-016)."""
    recorded = []

    def record(instance_path=None) -> str:
        """Stand in for the persona source logger and record its argument."""
        recorded.append(instance_path)
        return "built-in locked profile"

    monkeypatch.setattr("reachy_companion.persona.log_persona_source", record)

    _run_app(monkeypatch, instance_path=str(tmp_path))

    assert recorded == [str(tmp_path)]


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
