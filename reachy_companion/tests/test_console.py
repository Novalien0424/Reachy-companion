"""Tests for the headless console stream."""

import time
import asyncio
import logging
import threading
from types import SimpleNamespace
from typing import Any
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from collections.abc import Callable

import numpy as np
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import reachy_companion.console as console_mod
from reachy_companion.config import HF_AVAILABLE_VOICES, config
from reachy_companion.console import LocalStream
from reachy_companion.startup_settings import (
    StartupSettings,
    load_startup_settings_into_runtime,
)
from reachy_companion.personality_routes import (
    RouteError,
    build_personality_ops,
)


def _rpc_call(app: FastAPI, method: str, params: Any = None) -> dict[str, Any]:
    """Send one JSON-RPC request over /rpc and return the response envelope."""
    with TestClient(app).websocket_connect("/rpc") as ws:
        ws.send_json({"jsonrpc": "2.0", "id": "1", "method": method, "params": params or {}})
        return ws.receive_json()


async def _wait_until(predicate: Any, timeout: float = 1.0) -> None:
    """Wait until a test predicate becomes true."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("Timed out waiting for condition")


def test_clear_audio_queue_prefers_clear_player() -> None:
    """clear_player() is the canonical flush and is used whenever available."""
    handler = MagicMock()
    handler.output_queue = asyncio.Queue()
    handler.output_queue.put_nowait((24000, np.zeros(4, dtype=np.int16)))
    audio = SimpleNamespace(
        clear_player=MagicMock(),
        clear_output_buffer=MagicMock(),
    )
    robot = SimpleNamespace(media=SimpleNamespace(audio=audio))
    stream = LocalStream(handler, robot)

    stream.clear_audio_queue()

    audio.clear_player.assert_called_once()
    audio.clear_output_buffer.assert_not_called()
    assert handler.output_queue.empty()


def test_clear_audio_queue_falls_back_to_output_buffer() -> None:
    """Older SDKs without clear_player() still flush via clear_output_buffer()."""
    handler = MagicMock()
    handler.output_queue = asyncio.Queue()
    audio = SimpleNamespace(clear_output_buffer=MagicMock())  # no clear_player
    robot = SimpleNamespace(media=SimpleNamespace(audio=audio))
    stream = LocalStream(handler, robot)

    stream.clear_audio_queue()

    audio.clear_output_buffer.assert_called_once()
    assert handler.output_queue.empty()


def test_clear_audio_queue_drains_queue_in_place() -> None:
    """The output queue is drained in place, not replaced with a new object."""
    handler = MagicMock()
    queue: asyncio.Queue[Any] = asyncio.Queue()
    queue.put_nowait((24000, np.zeros(4, dtype=np.int16)))
    queue.put_nowait((24000, np.zeros(4, dtype=np.int16)))
    handler.output_queue = queue
    audio = SimpleNamespace(clear_player=MagicMock())
    robot = SimpleNamespace(media=SimpleNamespace(audio=audio))
    stream = LocalStream(handler, robot)

    stream.clear_audio_queue()

    assert handler.output_queue is queue  # same object, not replaced
    assert queue.empty()


def test_mic_reports_and_toggles_mute_state_over_rpc() -> None:
    """The mic starts live; conversation.mic exposes and flips the pause state."""
    app = FastAPI()
    robot = SimpleNamespace(media=SimpleNamespace(audio=None, backend=None))
    stream = LocalStream(MagicMock(), robot, settings_app=app)
    stream._init_settings_ui_if_needed()

    assert _rpc_call(app, "conversation.mic")["result"] == {"muted": False}
    assert _rpc_call(app, "conversation.mic", {"muted": True})["result"] == {"muted": True}
    assert stream._mic_muted is True
    assert _rpc_call(app, "conversation.mic", {"muted": False})["result"] == {"muted": False}
    assert stream._mic_muted is False

    # headless streams keep the mic live
    assert LocalStream(MagicMock(), robot)._mic_muted is False


def test_rest_api_is_removed_in_favor_of_rpc() -> None:
    """The /api/v1 REST + SSE surface is gone; control is JSON-RPC over /rpc."""
    app = FastAPI()
    robot = SimpleNamespace(media=SimpleNamespace(audio=None, backend=None))
    stream = LocalStream(MagicMock(), robot, settings_app=app)
    stream._init_settings_ui_if_needed()
    client = TestClient(app)

    for path in (
        "/api/v1/status",
        "/api/v1/mic",
        "/api/v1/personalities",
        "/api/v1/voices",
        "/api/v1/tool_spaces",
        "/api/v1/profile_tools",
    ):
        assert client.get(path).status_code == 404
    assert client.get("/api/v1/conversation_events").status_code == 404

    # ...but /rpc drives it fine.
    assert _rpc_call(app, "conversation.status")["result"]["backend"]


def test_settings_ui_detaches_framework_catch_all_before_own_routes() -> None:
    """Framework fallback routes should not shadow the UI or the /rpc endpoint."""
    app = FastAPI()

    @app.get("/{path:path}")
    def _framework_fallback(path: str) -> None:
        raise HTTPException(status_code=404)

    robot = SimpleNamespace(media=SimpleNamespace(audio=None, backend=None))
    stream = LocalStream(MagicMock(), robot, settings_app=app)
    stream._init_settings_ui_if_needed()
    client = TestClient(app)

    assert client.get("/").status_code == 200
    assert client.get("/static/js/api.js").status_code == 200
    assert _rpc_call(app, "conversation.status")["result"]["backend"]


@pytest.mark.asyncio
async def test_activity_from_rebuilt_handler_reaches_rpc_clients() -> None:
    """Activity from a rebuilt handler must still reach /rpc subscribers."""

    class FakeHandler:
        def __init__(self) -> None:
            self.observer: Any = None

        def set_activity_observer(self, observer: Any) -> None:
            self.observer = observer

    rebuilt = FakeHandler()
    app = FastAPI()
    robot = SimpleNamespace(media=SimpleNamespace(audio=None, backend=None))
    stream = LocalStream(FakeHandler(), robot, settings_app=app, handler_factory=lambda voice: rebuilt)
    stream._init_settings_ui_if_needed()
    stream._build_handler_for_current_backend()  # rebuild re-wires the observer

    with TestClient(app).websocket_connect("/rpc") as ws:
        rebuilt.observer("assistant_audio_delta")
        # First frame is conversation.activity (raw reason).
        msg = ws.receive_json()
    assert msg["method"] == "conversation.activity"
    assert msg["params"] == {"reason": "assistant_audio_delta"}


def test_backend_config_requests_in_process_restart_with_handler_factory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rebuild-capable LocalStream should reconnect in process after a connection change."""
    monkeypatch.setattr(config, "HF_REALTIME_CONNECTION_MODE", "deployed")
    monkeypatch.setattr(config, "HF_REALTIME_WS_URL", None)
    monkeypatch.delenv("HF_REALTIME_CONNECTION_MODE", raising=False)
    monkeypatch.delenv("HF_REALTIME_WS_URL", raising=False)

    app = FastAPI()
    handler = MagicMock()
    handler.shutdown = AsyncMock()
    robot = SimpleNamespace(media=SimpleNamespace(audio=None, backend=None))
    stream = LocalStream(
        handler,
        robot,
        settings_app=app,
        instance_path=str(tmp_path),
        handler_factory=lambda _voice: handler,
    )
    stream._init_settings_ui_if_needed()

    data = _rpc_call(app, "backend.config", {"hf_mode": "local", "hf_host": "localhost", "hf_port": 8765})["result"]

    assert data["ok"] is True
    assert data["message"] == "Connection saved. Reconnecting backend."
    assert data["backend"] == "huggingface"
    assert data["requires_restart"] is False
    assert data["can_proceed"] is True
    assert data["backend_connection_state"] == "connecting"
    assert stream._restart_requested.is_set()


def test_backend_config_persists_local_hf_selection_and_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Settings API should persist a direct Hugging Face websocket target."""
    monkeypatch.setattr(config, "HF_REALTIME_CONNECTION_MODE", "deployed")
    monkeypatch.setattr(config, "HF_REALTIME_SESSION_URL", None)
    monkeypatch.setattr(config, "HF_REALTIME_WS_URL", None)
    monkeypatch.delenv("HF_REALTIME_CONNECTION_MODE", raising=False)
    monkeypatch.delenv("HF_REALTIME_SESSION_URL", raising=False)
    monkeypatch.delenv("HF_REALTIME_WS_URL", raising=False)

    app = FastAPI()
    robot = SimpleNamespace(media=SimpleNamespace(audio=None, backend=None))
    stream = LocalStream(MagicMock(), robot, settings_app=app, instance_path=str(tmp_path))
    stream._init_settings_ui_if_needed()

    data = _rpc_call(app, "backend.config", {"hf_mode": "local", "hf_host": "localhost", "hf_port": 8765})["result"]

    assert data["ok"] is True
    assert data["backend"] == "huggingface"
    assert data["has_hf_ws_url"] is True
    assert data["has_hf_connection"] is True
    assert data["hf_connection_mode"] == "local"
    assert data["hf_direct_host"] == "localhost"
    assert data["hf_direct_port"] == 8765

    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "HF_REALTIME_CONNECTION_MODE=local" in env_text
    assert "HF_REALTIME_WS_URL=ws://localhost:8765/v1/realtime" in env_text


def test_backend_config_persists_deployed_mode_without_clearing_local_hf_ws_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Saving deployed mode should make env selection explicit and remove stale allocator URLs."""
    env_path = tmp_path / ".env"
    env_path.write_text(
        "HF_REALTIME_SESSION_URL=https://lb.example.test/session\n"
        "HF_REALTIME_WS_URL=ws://localhost:8765/v1/realtime\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(config, "HF_REALTIME_CONNECTION_MODE", "deployed")
    monkeypatch.setattr(config, "HF_REALTIME_SESSION_URL", "https://lb.example.test/session")
    monkeypatch.setattr(config, "HF_REALTIME_WS_URL", "ws://localhost:8765/v1/realtime")
    monkeypatch.delenv("HF_REALTIME_CONNECTION_MODE", raising=False)
    monkeypatch.setenv("HF_REALTIME_SESSION_URL", "https://lb.example.test/session")
    monkeypatch.setenv("HF_REALTIME_WS_URL", "ws://localhost:8765/v1/realtime")

    app = FastAPI()
    robot = SimpleNamespace(media=SimpleNamespace(audio=None, backend=None))
    stream = LocalStream(MagicMock(), robot, settings_app=app, instance_path=str(tmp_path))
    stream._init_settings_ui_if_needed()

    data = _rpc_call(app, "backend.config", {"hf_mode": "deployed"})["result"]

    assert data["ok"] is True
    assert data["has_hf_session_url"] is True
    assert data["has_hf_ws_url"] is True
    assert data["hf_connection_mode"] == "deployed"

    env_text = env_path.read_text(encoding="utf-8")
    assert "HF_REALTIME_CONNECTION_MODE=deployed" in env_text
    assert "HF_REALTIME_SESSION_URL=" not in env_text
    assert "HF_REALTIME_WS_URL=ws://localhost:8765/v1/realtime" in env_text


def test_backend_config_switches_to_saved_local_hf_connection_without_payload_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Switching back to a saved local Hugging Face backend should reuse the persisted target."""
    env_path = tmp_path / ".env"
    env_path.write_text(
        "HF_REALTIME_CONNECTION_MODE=local\nHF_REALTIME_WS_URL=ws://192.168.1.42:8766/v1/realtime\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(config, "HF_REALTIME_CONNECTION_MODE", "local")
    monkeypatch.setattr(config, "HF_REALTIME_SESSION_URL", None)
    monkeypatch.setattr(config, "HF_REALTIME_WS_URL", "ws://192.168.1.42:8766/v1/realtime")
    monkeypatch.setenv("HF_REALTIME_CONNECTION_MODE", "local")
    monkeypatch.setenv("HF_REALTIME_WS_URL", "ws://192.168.1.42:8766/v1/realtime")

    app = FastAPI()
    robot = SimpleNamespace(media=SimpleNamespace(audio=None, backend=None))
    stream = LocalStream(MagicMock(), robot, settings_app=app, instance_path=str(tmp_path))
    stream._init_settings_ui_if_needed()

    data = _rpc_call(app, "backend.config", {})["result"]

    assert data["ok"] is True
    assert data["backend"] == "huggingface"
    assert data["hf_connection_mode"] == "local"
    assert data["hf_direct_host"] == "192.168.1.42"
    assert data["hf_direct_port"] == 8766

    env_text = env_path.read_text(encoding="utf-8")
    assert "HF_REALTIME_CONNECTION_MODE=local" in env_text
    assert "HF_REALTIME_WS_URL=ws://192.168.1.42:8766/v1/realtime" in env_text


def test_backend_config_rejects_invalid_hf_port_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Settings API should reject invalid local Hugging Face ports from direct callers."""
    monkeypatch.setattr(config, "HF_REALTIME_CONNECTION_MODE", "deployed")
    monkeypatch.setattr(config, "HF_REALTIME_SESSION_URL", None)
    monkeypatch.setattr(config, "HF_REALTIME_WS_URL", None)

    app = FastAPI()
    robot = SimpleNamespace(media=SimpleNamespace(audio=None, backend=None))
    stream = LocalStream(MagicMock(), robot, settings_app=app, instance_path=str(tmp_path))
    stream._init_settings_ui_if_needed()

    resp = _rpc_call(
        app,
        "backend.config",
        {"backend": "huggingface", "hf_mode": "local", "hf_host": "localhost", "hf_port": 0},
    )

    assert resp["error"]["data"]["reason"] == "invalid_hf_port"


def test_status_reports_direct_hf_ws_url_as_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Settings API should treat a direct Hugging Face websocket as a valid configuration."""
    monkeypatch.setattr(config, "HF_REALTIME_CONNECTION_MODE", "local")
    monkeypatch.setattr(config, "HF_REALTIME_SESSION_URL", None)
    monkeypatch.setattr(config, "HF_REALTIME_WS_URL", "ws://127.0.0.1:8765/v1/realtime")

    app = FastAPI()
    robot = SimpleNamespace(media=SimpleNamespace(audio=None, backend=None))
    stream = LocalStream(MagicMock(), robot, settings_app=app, instance_path=str(tmp_path))
    stream._init_settings_ui_if_needed()

    data = _rpc_call(app, "conversation.status")["result"]

    assert data["backend"] == "huggingface"
    assert data["has_hf_session_url"] is False
    assert data["has_hf_ws_url"] is True
    assert data["has_hf_connection"] is True
    assert data["hf_connection_mode"] == "local"
    assert data["can_proceed_with_hf"] is True


def test_status_reports_backend_connection_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Settings API should expose backend connection failures without hiding controls."""
    monkeypatch.setattr(config, "HF_REALTIME_CONNECTION_MODE", "local")
    monkeypatch.setattr(config, "HF_REALTIME_SESSION_URL", None)
    monkeypatch.setattr(config, "HF_REALTIME_WS_URL", "ws://127.0.0.1:8765/v1/realtime")

    app = FastAPI()
    handler = MagicMock()
    handler.connection = None
    robot = SimpleNamespace(media=SimpleNamespace(audio=None, backend=None))
    stream = LocalStream(handler, robot, settings_app=app, instance_path=str(tmp_path))
    stream._set_backend_connection_state("disconnected", RuntimeError("connect failed"))
    stream._init_settings_ui_if_needed()

    data = _rpc_call(app, "conversation.status")["result"]
    assert data["backend"] == "huggingface"
    assert data["backend_connected"] is False
    assert data["backend_connection_state"] == "disconnected"
    assert data["backend_error"] == "RuntimeError: connect failed"
    assert data["can_proceed"] is True
    assert data["can_proceed_with_hf"] is True


def test_backend_startup_failure_is_recorded_without_raising(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Backend startup failures should become status state instead of killing LocalStream."""
    monkeypatch.setattr(config, "HF_REALTIME_CONNECTION_MODE", "local")
    monkeypatch.setattr(config, "HF_REALTIME_SESSION_URL", None)
    monkeypatch.setattr(config, "HF_REALTIME_WS_URL", "ws://127.0.0.1:8765/v1/realtime")

    app = FastAPI()
    handler = MagicMock()
    handler.connection = None
    handler.shutdown = AsyncMock()
    media = SimpleNamespace(
        audio=None,
        backend=None,
        start_recording=MagicMock(),
        start_playing=MagicMock(),
    )
    robot = SimpleNamespace(media=media)
    stream = LocalStream(handler, robot, settings_app=app, instance_path=str(tmp_path))
    stream._backend_retry_delay = 0
    stream.record_loop = AsyncMock(return_value=None)  # type: ignore[method-assign]
    stream.play_loop = AsyncMock(return_value=None)  # type: ignore[method-assign]
    monkeypatch.setattr("reachy_companion.console.apply_audio_startup_config", MagicMock())

    async def fail_and_stop() -> None:
        stream._stop_event.set()
        raise RuntimeError("local server unavailable")

    handler.start_up = AsyncMock(side_effect=fail_and_stop)

    try:
        stream.launch()
    finally:
        asyncio.set_event_loop(asyncio.new_event_loop())

    handler.start_up.assert_awaited_once()
    data = _rpc_call(app, "conversation.status")["result"]
    assert data["backend_connected"] is False
    assert data["backend_connection_state"] == "disconnected"
    assert data["backend_error"] == "RuntimeError: local server unavailable"


def test_media_warmup_overlaps_audio_startup_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Audio configuration should run while the media pipelines warm up."""
    monkeypatch.setattr("reachy_companion.console.has_hf_realtime_target", lambda: True)

    handler = MagicMock()
    handler.shutdown = AsyncMock()
    media = SimpleNamespace(
        audio=None,
        backend=None,
        start_recording=MagicMock(),
        start_playing=MagicMock(),
    )
    stream = LocalStream(handler, SimpleNamespace(media=media))
    stream.record_loop = AsyncMock(return_value=None)  # type: ignore[method-assign]
    stream.play_loop = AsyncMock(return_value=None)  # type: ignore[method-assign]

    startup_barrier = threading.Barrier(2)

    async def wait_for_audio_config(_delay: float) -> None:
        await asyncio.to_thread(startup_barrier.wait, 5.0)

    def apply_audio_config(*_args: Any, **_kwargs: Any) -> bool:
        startup_barrier.wait(5.0)
        return True

    async def start_and_stop() -> None:
        stream._stop_event.set()

    handler.start_up = AsyncMock(side_effect=start_and_stop)
    monkeypatch.setattr("reachy_companion.console.asyncio.sleep", wait_for_audio_config)
    monkeypatch.setattr("reachy_companion.console.apply_audio_startup_config", apply_audio_config)

    try:
        stream.launch()
    finally:
        asyncio.set_event_loop(asyncio.new_event_loop())


def _audio_robot(**media_attrs: Any) -> SimpleNamespace:
    """Return a robot whose media exposes only the attributes a test drives."""
    return SimpleNamespace(media=SimpleNamespace(audio=None, backend=None, **media_attrs))


def test_launch_reads_microphone_while_audio_is_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """The microphone is drained during audio configuration, so no backlog builds up before live input."""
    monkeypatch.setattr(console_mod, "has_hf_realtime_target", lambda: True)
    configuring = threading.Event()
    read_while_configuring = threading.Event()

    def read_microphone() -> None:
        if configuring.is_set():
            read_while_configuring.set()

    def configure_audio(*_args: object, **_kwargs: object) -> None:
        configuring.set()
        read_while_configuring.wait(1)
        stream.close()

    media = SimpleNamespace(
        audio=None,
        backend=None,
        start_recording=MagicMock(),
        start_playing=MagicMock(),
        stop_recording=MagicMock(),
        stop_playing=MagicMock(),
        get_input_audio_samplerate=MagicMock(return_value=16000),
        get_audio_sample=read_microphone,
    )
    stream = LocalStream(MagicMock(shutdown=AsyncMock()), SimpleNamespace(media=media))
    monkeypatch.setattr(stream, "_run_handler_startup_loop", AsyncMock())
    monkeypatch.setattr(console_mod, "apply_audio_startup_config", configure_audio)
    try:
        stream.launch()
    finally:
        asyncio.set_event_loop(asyncio.new_event_loop())

    assert read_while_configuring.is_set()


@pytest.mark.asyncio
async def test_record_loop_discards_warmup_audio() -> None:
    """Frames read while the audio board warms up never reach the handler."""
    warmup_frame = np.full(4, 1, dtype=np.int16)
    live_frame = np.full(4, 2, dtype=np.int16)
    reads: list[bool] = []
    handler = MagicMock()
    robot = _audio_robot(get_input_audio_samplerate=MagicMock(return_value=16000), get_audio_sample=MagicMock())
    stream = LocalStream(handler, robot)
    stream._audio_warming_up = True

    def read_microphone() -> np.ndarray:
        reads.append(stream._audio_warming_up)
        return warmup_frame if stream._audio_warming_up else live_frame

    robot.media.get_audio_sample.side_effect = read_microphone
    handler.receive = AsyncMock(side_effect=lambda _frame: stream._stop_event.set())
    recording = asyncio.create_task(stream.record_loop())
    while len(reads) < 2 and not recording.done():
        await asyncio.sleep(0)
    stream._audio_warming_up = False
    await asyncio.wait_for(recording, timeout=2)

    handler.receive.assert_awaited_once()
    np.testing.assert_array_equal(handler.receive.await_args.args[0][1], live_frame)


@pytest.mark.asyncio
async def test_startup_loop_rebuilds_handler_on_restart_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """LocalStream should shut down and rebuild the handler when a restart is requested."""
    monkeypatch.setattr(config, "HF_REALTIME_CONNECTION_MODE", "local")
    monkeypatch.setattr(config, "HF_REALTIME_SESSION_URL", None)
    monkeypatch.setattr(config, "HF_REALTIME_WS_URL", "ws://127.0.0.1:8765/v1/realtime")

    class FakeHandler:
        def __init__(self) -> None:
            self.connection = None
            self.output_queue = asyncio.Queue()
            self.started = asyncio.Event()
            self.stopped = asyncio.Event()
            self.shutdown_calls = 0

        async def start_up(self) -> None:
            self.connection = object()
            self.started.set()
            await self.stopped.wait()
            self.connection = None

        async def shutdown(self) -> None:
            self.shutdown_calls += 1
            self.stopped.set()

        async def receive(self, _frame: Any) -> None:
            return None

        async def emit(self) -> None:
            return None

    handlers: list[FakeHandler] = []

    def handler_factory(_voice: str | None) -> FakeHandler:
        handler = FakeHandler()
        handlers.append(handler)
        return handler

    robot = SimpleNamespace(media=SimpleNamespace(audio=None, backend=None))
    initial_handler = handler_factory(None)
    stream = LocalStream(initial_handler, robot, handler_factory=handler_factory)
    stream._backend_retry_delay = 0.01

    startup_task = asyncio.create_task(stream._run_handler_startup_loop())
    try:
        await _wait_until(lambda: initial_handler.started.is_set())

        await stream.request_backend_restart("backend_config_changed")

        await _wait_until(lambda: len(handlers) == 2 and handlers[1].started.is_set())

        assert initial_handler.shutdown_calls >= 1
        assert stream.handler is handlers[1]
        assert stream._backend_connected() is True
    finally:
        stream._stop_event.set()
        await stream._shutdown_active_handler()
        startup_task.cancel()
        try:
            await startup_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_personality_ops_return_hf_voices() -> None:
    """With no running loop, voices() falls back to the Hugging Face catalog."""
    ops = build_personality_ops(MagicMock(), lambda: None)
    assert await ops.voices() == HF_AVAILABLE_VOICES


def test_personality_ops_delete_builtin_is_not_deletable() -> None:
    """Deleting a built-in personality raises not_deletable (was REST 404)."""
    ops = build_personality_ops(MagicMock(), lambda: None)
    with pytest.raises(RouteError) as ei:
        ops.delete("mad_scientist_assistant")
    assert ei.value.reason == "not_deletable"


def test_personality_ops_load_builtin_default_profile() -> None:
    """The bulk personality API should retain the complete profile payload."""
    ops = build_personality_ops(MagicMock(), lambda: None)
    data = ops.load("default")
    assert "Reachy Mini" in data["instructions"]
    assert data["tools_text"]
    assert data["enabled_tools"]
    assert data["available_tools"]


@pytest.mark.asyncio
async def test_personality_ops_apply_voice() -> None:
    """apply_voice delegates to the handler and reports the status."""
    handler = MagicMock()
    handler.change_voice = AsyncMock(return_value="Voice changed to cedar.")
    ops = build_personality_ops(handler, lambda: asyncio.get_running_loop())

    result = await ops.apply_voice("cedar")

    assert result == {"ok": True, "status": "Voice changed to cedar."}
    handler.change_voice.assert_awaited_once_with("cedar")


@pytest.mark.asyncio
async def test_personality_ops_persist_startup_with_voice_override() -> None:
    """Applying with persist=True saves the active manual voice override."""
    handler = MagicMock()
    handler.apply_personality = AsyncMock(return_value="Applied personality and restarted realtime session.")
    handler.get_current_voice = MagicMock(return_value="shimmer")
    persist_personality = MagicMock()
    ops = build_personality_ops(handler, lambda: asyncio.get_running_loop(), persist_personality=persist_personality)

    result = await ops.apply("sorry_bro", persist=True)

    assert result["ok"] is True
    handler.apply_personality.assert_awaited_once_with("sorry_bro")
    persist_personality.assert_called_once_with("sorry_bro", "shimmer")


@pytest.mark.asyncio
async def test_personality_ops_apply_same_profile_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Re-applying the active personality is a no-op for the realtime handler."""
    monkeypatch.setattr(config, "REACHY_MINI_CUSTOM_PROFILE", "sorry_bro")
    handler = MagicMock()
    handler.apply_personality = AsyncMock(return_value="should not be called")
    handler.get_current_voice = MagicMock(return_value="shimmer")
    ops = build_personality_ops(handler, lambda: None)

    result = await ops.apply("sorry_bro")

    assert result["status"] == "Personality unchanged."
    handler.apply_personality.assert_not_awaited()
    handler.get_current_voice.assert_not_called()


def test_personality_ops_startup_choice_survives_runtime_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Runtime profile switching should not redefine the saved startup personality."""
    monkeypatch.setattr(config, "REACHY_MINI_CUSTOM_PROFILE", "captain_circuit")
    ops = build_personality_ops(MagicMock(), lambda: None)

    first = ops.get_choices()
    assert first["current"] == "captain_circuit"
    assert first["startup"] == "captain_circuit"

    monkeypatch.setattr(config, "REACHY_MINI_CUSTOM_PROFILE", "chess_coach")

    second = ops.get_choices()
    assert second["current"] == "chess_coach"
    assert second["startup"] == "captain_circuit"


@pytest.mark.asyncio
async def test_personality_ops_use_apply_callback() -> None:
    """Apply delegates to the injected apply_personality callback, not the handler."""
    handler = MagicMock()
    handler.apply_personality = AsyncMock(return_value="handler should not be called")
    apply_personality = AsyncMock(return_value="Applied personality and restarting backend.")
    get_current_voice = MagicMock(return_value="cedar")
    ops = build_personality_ops(
        handler,
        lambda: asyncio.get_running_loop(),
        apply_personality=apply_personality,
        get_current_voice=get_current_voice,
    )

    result = await ops.apply("sorry_bro")

    assert result["status"] == "Applied personality and restarting backend."
    apply_personality.assert_awaited_once_with("sorry_bro")
    handler.apply_personality.assert_not_awaited()


@pytest.mark.asyncio
async def test_apply_personality_propagates_restart_cancellation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cancellation during backend restart should not be converted into a status string."""
    monkeypatch.setattr(console_mod, "set_custom_profile", lambda _profile: None)
    monkeypatch.setattr(console_mod, "get_session_instructions", lambda _instance_path=None: "instructions")
    monkeypatch.setattr(console_mod, "get_session_voice", lambda default: default)

    stream = LocalStream(MagicMock(), MagicMock())

    async def cancel_restart(_reason: str) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(stream, "request_backend_restart", cancel_restart)

    with pytest.raises(asyncio.CancelledError):
        await stream.apply_personality("sorry_bro")


@pytest.mark.asyncio
async def test_apply_personality_restores_profile_when_tool_initialization_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed tool rebuild must not leave the rejected profile selected."""
    monkeypatch.setattr(config, "REACHY_MINI_CUSTOM_PROFILE", "default")
    monkeypatch.setattr(
        console_mod,
        "set_custom_profile",
        lambda profile: setattr(config, "REACHY_MINI_CUSTOM_PROFILE", profile),
    )
    monkeypatch.setattr(console_mod, "get_session_instructions", lambda: "instructions")
    monkeypatch.setattr(console_mod, "get_session_voice", lambda default: default)
    monkeypatch.setattr(console_mod, "initialize_tools", MagicMock(side_effect=RuntimeError("invalid tools")))
    stream = LocalStream(MagicMock(), MagicMock())

    with pytest.raises(RuntimeError, match="invalid tools"):
        await stream.apply_personality("broken")

    assert config.REACHY_MINI_CUSTOM_PROFILE == "default"


@pytest.mark.asyncio
async def test_local_stream_change_voice_delegates_without_backend_restart() -> None:
    """LocalStream voice changes should update the active handler without rebuilding it."""
    handler = MagicMock()
    handler.change_voice = AsyncMock(return_value="Voice changed to Serena.")
    handler.get_current_voice = MagicMock(return_value="Serena")
    stream = LocalStream(handler, MagicMock())

    status = await stream.change_voice("Serena")

    assert status == "Voice changed to Serena."
    handler.change_voice.assert_awaited_once_with("Serena")
    assert stream._voice_override == "Serena"
    assert not stream._restart_requested.is_set()


def test_local_stream_persist_personality_stores_voice_override(tmp_path) -> None:
    """Persisting startup settings should write both profile and voice override."""
    stream = LocalStream(MagicMock(), MagicMock(), instance_path=str(tmp_path))

    stream._persist_personality("sorry_bro", "shimmer")

    settings_path = tmp_path / "startup_settings.json"
    assert settings_path.exists()
    assert settings_path.read_text(encoding="utf-8") == '{\n  "profile": "sorry_bro",\n  "voice": "shimmer"\n}\n'
    assert stream._read_persisted_personality() == "sorry_bro"


def test_local_stream_persist_personality_clears_legacy_startup_env_overrides(tmp_path, monkeypatch) -> None:
    """Saving startup settings should remove legacy `.env` profile and voice overrides."""
    env_path = tmp_path / ".env"
    env_path.write_text(
        "HF_TOKEN=test-token\n"
        "REACHY_MINI_CUSTOM_PROFILE=mad_scientist_assistant\n"
        "REACHY_MINI_VOICE_OVERRIDE=shimmer\n",
        encoding="utf-8",
    )
    stream = LocalStream(MagicMock(), MagicMock(), instance_path=str(tmp_path))

    stream._persist_personality(None, "Aiden")

    env_text = env_path.read_text(encoding="utf-8")
    assert "HF_TOKEN=test-token" in env_text
    assert "REACHY_MINI_CUSTOM_PROFILE=" not in env_text
    assert "REACHY_MINI_VOICE_OVERRIDE=" not in env_text

    applied_profiles: list[str | None] = []
    monkeypatch.delenv("REACHY_MINI_CUSTOM_PROFILE", raising=False)
    monkeypatch.setattr(
        "reachy_companion.config.set_custom_profile",
        lambda profile: applied_profiles.append(profile),
    )

    settings = load_startup_settings_into_runtime(tmp_path)

    assert settings == StartupSettings(voice="Aiden")
    assert applied_profiles == [None]


def test_local_stream_launch_waits_for_missing_hf_target_without_starting_media(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Startup should wait for settings input when the Hugging Face target is missing."""
    monkeypatch.setattr(config, "HF_REALTIME_CONNECTION_MODE", "local")
    monkeypatch.setattr(config, "HF_REALTIME_SESSION_URL", None)
    monkeypatch.setattr(config, "HF_REALTIME_WS_URL", None)

    media = SimpleNamespace(
        start_recording=MagicMock(),
        start_playing=MagicMock(),
    )
    robot = SimpleNamespace(media=media)
    stream = LocalStream(MagicMock(), robot, settings_app=FastAPI(), instance_path=str(tmp_path))

    init_settings_ui = MagicMock()
    monkeypatch.setattr(stream, "_init_settings_ui_if_needed", init_settings_ui)
    monkeypatch.setattr("reachy_companion.console.time.sleep", MagicMock(side_effect=KeyboardInterrupt))

    stream.launch()

    init_settings_ui.assert_called_once()
    media.start_recording.assert_not_called()
    media.start_playing.assert_not_called()


def _rpc_robot() -> SimpleNamespace:
    """Return a robot mock whose audio pipeline supports clear_audio_queue()."""
    audio = SimpleNamespace(clear_player=MagicMock(), clear_output_buffer=MagicMock())
    return SimpleNamespace(media=SimpleNamespace(audio=audio))


def test_rpc_status_and_mic_over_websocket() -> None:
    """conversation.status/mic are reachable over the /rpc JSON-RPC WebSocket."""
    app = FastAPI()
    stream = LocalStream(MagicMock(), _rpc_robot(), settings_app=app)
    stream._init_settings_ui_if_needed()
    client = TestClient(app)
    with client.websocket_connect("/rpc") as ws:
        ws.send_json({"jsonrpc": "2.0", "id": "1", "method": "conversation.status"})
        resp = ws.receive_json()
        assert resp["id"] == "1"
        assert "result" in resp

        ws.send_json({"jsonrpc": "2.0", "id": "2", "method": "conversation.mic", "params": {"muted": True}})
        resp = ws.receive_json()
        assert resp["result"] == {"muted": True}
    assert stream._mic_muted is True


def test_rpc_interrupt_broadcasts_turn_listening() -> None:
    """conversation.interrupt clears playback and pushes a turn:listening event."""
    handler = MagicMock()
    handler.output_queue = asyncio.Queue()
    handler._is_connected.return_value = True
    app = FastAPI()
    stream = LocalStream(handler, _rpc_robot(), settings_app=app)
    stream._init_settings_ui_if_needed()
    with TestClient(app).websocket_connect("/rpc") as ws:
        ws.send_json({"jsonrpc": "2.0", "id": "1", "method": "conversation.interrupt"})
        msgs = [ws.receive_json(), ws.receive_json()]
    results = [m for m in msgs if "result" in m]
    notes = [m for m in msgs if m.get("method") == "conversation.turn"]
    assert results and results[0]["result"] == {"ok": True}
    assert notes and notes[0]["params"] == {"state": "listening", "reason": "interrupted"}


def test_rpc_say_requires_active_session() -> None:
    """conversation.say fails with not_running when no session is connected."""
    handler = MagicMock()
    handler._is_connected.return_value = False
    app = FastAPI()
    stream = LocalStream(handler, _rpc_robot(), settings_app=app)
    stream._init_settings_ui_if_needed()
    with TestClient(app).websocket_connect("/rpc") as ws:
        ws.send_json({"jsonrpc": "2.0", "id": "1", "method": "conversation.say", "params": {"text": "hi"}})
        resp = ws.receive_json()
    assert resp["error"]["data"]["reason"] == "not_running"


def test_rpc_transcript_notification_broadcast() -> None:
    """The handler's transcript observer pushes conversation.transcript events."""
    app = FastAPI()
    stream = LocalStream(MagicMock(), _rpc_robot(), settings_app=app)
    stream._init_settings_ui_if_needed()
    with TestClient(app).websocket_connect("/rpc") as ws:
        stream._dispatch_transcript("assistant", "hello there", True)
        msg = ws.receive_json()
    assert msg["method"] == "conversation.transcript"
    assert msg["params"] == {"role": "assistant", "text": "hello there", "final": True}


def test_rpc_settings_methods() -> None:
    """Personality, voice, and tool settings are reachable over /rpc."""
    app = FastAPI()
    stream = LocalStream(MagicMock(), _rpc_robot(), settings_app=app)
    stream._init_settings_ui_if_needed()
    with TestClient(app).websocket_connect("/rpc") as ws:
        ws.send_json({"jsonrpc": "2.0", "id": "1", "method": "personalities.list"})
        r1 = ws.receive_json()
        ws.send_json({"jsonrpc": "2.0", "id": "2", "method": "voices.list"})
        r2 = ws.receive_json()
        ws.send_json({"jsonrpc": "2.0", "id": "3", "method": "tool_spaces.list"})
        r3 = ws.receive_json()
        ws.send_json({"jsonrpc": "2.0", "id": "4", "method": "profile_tools.get"})
        r4 = ws.receive_json()
    assert "choices" in r1["result"] and "current" in r1["result"]
    assert isinstance(r2["result"], list)
    assert "spaces" in r3["result"]
    assert "enabled_tools" in r4["result"]


@pytest.mark.asyncio
async def test_a_none_emission_marks_the_playback_queue_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """A None emission must itself record the queue-empty fact.

    `emit()` answers an idle queue with None every ~0.1 s, so the 0.5 s
    timeout branch can never fire during a live session -- without this, a
    ducked track never resumes (2026-08-24).
    """
    from reachy_companion.hanova import audio_drain

    audio_drain.reset()
    generation = audio_drain.begin_response()
    audio_drain.note_enqueued(generation, sample_count=2400, sample_rate=24000)
    audio_drain.note_chunk(sample_count=2400, sample_rate=24000)  # flag now False
    audio_drain.close_response(generation)

    handler = MagicMock()
    emissions: list[Any] = [None]

    async def emit() -> Any:
        if emissions:
            return emissions.pop()
        raise asyncio.CancelledError  # stop the loop after the None

    handler.emit = emit
    robot = SimpleNamespace(media=SimpleNamespace(push_audio_sample=MagicMock()))
    stream = LocalStream(handler, robot)
    with pytest.raises(asyncio.CancelledError):
        await stream.play_loop()

    assert await audio_drain.wait_drained(generation, timeout_s=0.5) is True


# --------------------------------------------------------------------------
# Microphone reader thread (console part of upstream 8b37f31)
# --------------------------------------------------------------------------


def _stop_after(stream: LocalStream, value: Any) -> Callable[[], Any]:
    """Return a side effect that stops the stream after one read, yielding `value`."""

    def _side_effect() -> Any:
        stream._stop_event.set()
        return value

    return _side_effect


def _reader_threads() -> list[threading.Thread]:
    """Return the live microphone reader threads."""
    return [t for t in threading.enumerate() if t.name == "microphone-reader" and t.is_alive()]


@pytest.mark.asyncio
async def test_record_loop_forwards_unmuted_frames() -> None:
    """A recorded frame is forwarded to the handler with the input sample rate."""
    frame = np.zeros(4, dtype=np.int16)
    recorded = [frame]
    robot = _audio_robot(
        get_input_audio_samplerate=MagicMock(return_value=16000),
        get_audio_sample=lambda: recorded.pop() if recorded else None,
    )
    handler = MagicMock()
    stream = LocalStream(handler, robot)
    handler.receive = AsyncMock(side_effect=lambda _frame: stream._stop_event.set())

    await asyncio.wait_for(stream.record_loop(), timeout=2)

    handler.receive.assert_awaited_once_with((16000, frame))


@pytest.mark.asyncio
async def test_record_loop_skips_frames_while_muted() -> None:
    """No frames are forwarded while the mic is muted."""
    robot = _audio_robot(get_input_audio_samplerate=MagicMock(return_value=16000), get_audio_sample=MagicMock())
    handler = MagicMock()
    handler.receive = AsyncMock()
    stream = LocalStream(handler, robot)
    stream._mic_muted = True
    robot.media.get_audio_sample.side_effect = _stop_after(stream, np.zeros(4, dtype=np.int16))

    await asyncio.wait_for(stream.record_loop(), timeout=2)

    handler.receive.assert_not_awaited()


@pytest.mark.asyncio
async def test_record_loop_skips_missing_frames() -> None:
    """A None frame from the recorder is not forwarded."""
    robot = _audio_robot(get_input_audio_samplerate=MagicMock(return_value=16000), get_audio_sample=MagicMock())
    handler = MagicMock()
    handler.receive = AsyncMock()
    stream = LocalStream(handler, robot)
    robot.media.get_audio_sample.side_effect = _stop_after(stream, None)

    await asyncio.wait_for(stream.record_loop(), timeout=2)

    handler.receive.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_blocking_microphone_read_does_not_block_the_event_loop() -> None:
    """The SDK read blocks (up to 20 ms on the robot); it must block a thread, not the loop.

    The loop also runs realtime receive, playback and the barge timers, so a
    read held open here must leave other coroutines free to run meanwhile.
    """
    frame = np.zeros(4, dtype=np.int16)
    read_started = threading.Event()
    release_read = threading.Event()
    calls: list[int] = []

    def read_microphone() -> np.ndarray | None:
        calls.append(1)
        if len(calls) > 1:
            return None
        read_started.set()
        release_read.wait(timeout=2)
        return frame

    robot = _audio_robot(get_input_audio_samplerate=lambda: 16000, get_audio_sample=read_microphone)
    handler = MagicMock()
    stream = LocalStream(handler, robot)
    handler.receive = AsyncMock(side_effect=lambda _frame: stream._stop_event.set())
    loop = asyncio.get_running_loop()

    recording = asyncio.create_task(stream.record_loop())
    try:
        await _wait_until(read_started.is_set)
        ticks_started = loop.time()
        for _ in range(5):
            await asyncio.sleep(0.01)
        assert loop.time() - ticks_started < 0.5
        assert not recording.done()
        handler.receive.assert_not_awaited()
    finally:
        release_read.set()
    await asyncio.wait_for(recording, timeout=2)

    handler.receive.assert_awaited_once_with((16000, frame))


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["stop_event", "cancel"])
async def test_record_loop_stops_its_reader_thread(ending: str) -> None:
    """Both shutdown paths, the stop event and close()'s task cancel, join the reader thread."""

    def read_microphone() -> None:
        time.sleep(0.02)  # the SDK's bounded wait for a buffer

    robot = _audio_robot(get_input_audio_samplerate=lambda: 16000, get_audio_sample=read_microphone)
    stream = LocalStream(MagicMock(), robot)
    recording = asyncio.create_task(stream.record_loop())
    await _wait_until(lambda: bool(_reader_threads()))

    if ending == "stop_event":
        stream._stop_event.set()
        await asyncio.wait_for(recording, timeout=2)
    else:
        recording.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(recording, timeout=2)

    assert _reader_threads() == []


@pytest.mark.asyncio
async def test_a_failing_microphone_read_fails_the_record_loop() -> None:
    """A read that raises surfaces from record_loop instead of leaving a silently dead mic."""

    def read_microphone() -> None:
        raise RuntimeError("audio pipeline gone")

    robot = _audio_robot(get_input_audio_samplerate=lambda: 16000, get_audio_sample=read_microphone)
    stream = LocalStream(MagicMock(), robot)

    with pytest.raises(RuntimeError, match="audio pipeline gone"):
        await asyncio.wait_for(stream.record_loop(), timeout=2)

    assert _reader_threads() == []


# --- Local voice detection on the reader thread (2026-10-28 local-VAD plan) -------


class _ThreadRecordingDetector:
    """Stand-in for `SileroVAD`: always answers `speaking` and records the thread it ran on."""

    def __init__(self, speaking: bool = True, fail_after: int | None = None) -> None:
        self.speaking = speaking
        self.fail_after = fail_after
        self.threads: list[str] = []

    def process(self, audio: np.ndarray, sample_rate: int, *, detect_barge_in: bool = False) -> bool:
        self.threads.append(threading.current_thread().name)
        if self.fail_after is not None and len(self.threads) > self.fail_after:
            raise RuntimeError("onnx session died")
        return self.speaking

    def reset(self) -> None:
        pass


def _vad_stream(
    monkeypatch: pytest.MonkeyPatch, detector: Any = None, *, sample_rate: int = 16000
) -> tuple[LocalStream, MagicMock, list[tuple[bool, str]]]:
    """Build a stream with local VAD on, a fake detector, an SDK-like mic and a recording handler."""
    from reachy_companion.audio.local_vad import LocalSpeechFeed

    monkeypatch.setenv("LOCAL_VAD_LISTENING", "1")
    if detector is not None:
        monkeypatch.setattr(console_mod, "_load_local_speech_feed", lambda _rate: LocalSpeechFeed(detector))

    def read_microphone() -> np.ndarray:
        time.sleep(0.002)  # the SDK's bounded wait for a buffer
        return np.full((320, 2), 0.25, dtype=np.float32)

    robot = _audio_robot(get_input_audio_samplerate=lambda: sample_rate, get_audio_sample=read_microphone)
    handler = MagicMock()
    handler.receive = AsyncMock()
    edges: list[tuple[bool, str]] = []
    handler.on_local_speech = MagicMock(
        side_effect=lambda speaking: edges.append((speaking, threading.current_thread().name))
    )
    return LocalStream(handler, robot), handler, edges


async def _stop_recording(stream: LocalStream, recording: "asyncio.Task[None]") -> None:
    stream._stop_event.set()
    await asyncio.wait_for(recording, timeout=2)


@pytest.mark.asyncio
async def test_local_vad_runs_on_the_reader_thread_and_hands_edges_to_the_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inference happens on the microphone thread; the handler hears the edge on the loop's thread."""
    detector = _ThreadRecordingDetector(speaking=True)
    stream, handler, edges = _vad_stream(monkeypatch, detector)
    loop_thread = threading.current_thread().name

    recording = asyncio.create_task(stream.record_loop())
    await _wait_until(lambda: bool(edges))
    await _stop_recording(stream, recording)

    assert set(detector.threads) == {"microphone-reader"}
    assert edges == [(True, loop_thread)]  # one edge for a steady state, delivered on the loop
    assert handler.receive.await_count >= 1  # the conversation still gets every frame


@pytest.mark.asyncio
async def test_local_vad_hears_nothing_while_warming_up(monkeypatch: pytest.MonkeyPatch) -> None:
    """Warmup audio is discarded for the detector exactly as it is for the handler."""
    detector = _ThreadRecordingDetector(speaking=True)
    stream, handler, edges = _vad_stream(monkeypatch, detector)
    stream._audio_warming_up = True

    recording = asyncio.create_task(stream.record_loop())
    await _wait_until(lambda: stream._local_speech is not None)
    await asyncio.sleep(0.05)  # many reads after the detector is ready
    await _stop_recording(stream, recording)

    assert detector.threads == []
    assert edges == []
    handler.receive.assert_not_awaited()


@pytest.mark.asyncio
async def test_muting_the_mic_starves_the_detector_and_drops_its_vote(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mute (the UI's, or sleep quiesce's) feeds the detector nothing and ends a local speech vote at once."""
    detector = _ThreadRecordingDetector(speaking=True)
    stream, _handler, edges = _vad_stream(monkeypatch, detector)

    recording = asyncio.create_task(stream.record_loop())
    await _wait_until(lambda: bool(edges))
    stream._mic_muted = True
    await _wait_until(lambda: len(edges) == 2)
    fed_while_muted = len(detector.threads)
    await asyncio.sleep(0.05)
    await _stop_recording(stream, recording)

    assert [speaking for speaking, _thread in edges] == [True, False]
    assert len(detector.threads) <= fed_while_muted + 1  # at most the read already in flight


@pytest.mark.asyncio
async def test_local_vad_stays_off_when_disabled(monkeypatch: pytest.MonkeyPatch, caplog: Any) -> None:
    """`LOCAL_VAD_LISTENING=0` never loads the detector."""
    stream, handler, edges = _vad_stream(monkeypatch)
    monkeypatch.setenv("LOCAL_VAD_LISTENING", "0")
    loader = MagicMock()
    monkeypatch.setattr(console_mod, "_load_local_speech_feed", loader)
    caplog.set_level(logging.INFO, logger=console_mod.__name__)

    recording = asyncio.create_task(stream.record_loop())
    await _wait_until(lambda: handler.receive.await_count >= 3)
    await _stop_recording(stream, recording)

    loader.assert_not_called()
    assert edges == []
    assert "Local VAD off (LOCAL_VAD_LISTENING)" in caplog.text


@pytest.mark.asyncio
async def test_a_missing_onnxruntime_turns_local_vad_off_and_the_mic_keeps_flowing(
    monkeypatch: pytest.MonkeyPatch, caplog: Any
) -> None:
    """The guarded import: no onnxruntime is one warning, and the conversation is unaffected."""
    import sys

    stream, handler, edges = _vad_stream(monkeypatch)  # the real loader
    monkeypatch.setitem(sys.modules, "onnxruntime", None)
    monkeypatch.delitem(sys.modules, "reachy_companion.audio.local_vad", raising=False)
    caplog.set_level(logging.WARNING, logger=console_mod.__name__)

    recording = asyncio.create_task(stream.record_loop())
    await _wait_until(lambda: "Local VAD unavailable" in caplog.text)
    received = handler.receive.await_count
    await _wait_until(lambda: handler.receive.await_count > received)
    await _stop_recording(stream, recording)

    assert stream._local_speech is None
    assert edges == []
    assert "onnxruntime" in caplog.text
    assert caplog.text.count("Local VAD unavailable") == 1


@pytest.mark.asyncio
async def test_a_microphone_that_is_not_16_khz_turns_local_vad_off(monkeypatch: pytest.MonkeyPatch, caplog: Any) -> None:
    """Silero needs 16 kHz; anything else is refused before any model is fetched."""
    stream, _handler, edges = _vad_stream(monkeypatch, sample_rate=48000)  # the real loader
    caplog.set_level(logging.WARNING, logger=console_mod.__name__)

    recording = asyncio.create_task(stream.record_loop())
    await _wait_until(lambda: "Local VAD unavailable" in caplog.text)
    await _stop_recording(stream, recording)

    assert stream._local_speech is None
    assert edges == []
    assert "48000 Hz" in caplog.text


@pytest.mark.asyncio
async def test_a_failing_detector_is_dropped_once_without_a_stale_vote(
    monkeypatch: pytest.MonkeyPatch, caplog: Any
) -> None:
    """An inference error warns once, releases the vote it held, and the mic keeps flowing to the handler."""
    detector = _ThreadRecordingDetector(speaking=True, fail_after=3)
    stream, handler, edges = _vad_stream(monkeypatch, detector)
    caplog.set_level(logging.WARNING, logger=console_mod.__name__)

    recording = asyncio.create_task(stream.record_loop())
    await _wait_until(lambda: len(edges) == 2)
    received = handler.receive.await_count
    await _wait_until(lambda: handler.receive.await_count > received + 3)
    await _stop_recording(stream, recording)

    assert [speaking for speaking, _thread in edges] == [True, False]
    assert stream._local_speech is None
    assert len(detector.threads) == 4  # never fed again after the failure
    assert caplog.text.count("Local VAD failed") == 1


def test_a_local_speech_edge_reaches_the_current_handler() -> None:
    """After a handler rebuild the edge goes to the handler now installed."""
    stream = LocalStream(MagicMock(), _audio_robot())
    rebuilt = MagicMock()
    stream._install_handler(rebuilt)

    stream._on_local_speech(True)

    rebuilt.on_local_speech.assert_called_once_with(True)


# --- Backend start retry: backoff and log rate (wifi-recovery plan W1) ---------


def _offline_robot() -> SimpleNamespace:
    """Return a robot stub with no media backend, enough for the startup loop."""
    return SimpleNamespace(media=SimpleNamespace(audio=None, backend=None))


def _failing_handler(*effects: Any) -> MagicMock:
    """Return a handler whose successive start_up() calls raise or return per ``effects``."""
    handler = MagicMock()
    handler.connection = None
    handler.shutdown = AsyncMock()
    handler.start_up = AsyncMock(side_effect=list(effects))
    return handler


async def _record_startup_waits(stream: LocalStream, waits: int) -> list[float]:
    """Run the startup loop and return the first ``waits`` retry delays it asked for."""
    delays: list[float] = []

    async def record_wait(delay: float) -> None:
        delays.append(delay)
        if len(delays) >= waits:
            stream._stop_event.set()

    stream._sleep_or_restart_requested = record_wait  # type: ignore[method-assign]
    await asyncio.wait_for(stream._run_handler_startup_loop(), timeout=2)
    return delays


def test_backend_retry_delay_doubles_from_the_base_to_a_60_second_cap() -> None:
    """Consecutive start failures wait 5, 10, 20, 40 s, then 60 s for as long as they last."""
    delays = [console_mod._backoff_delay(5.0, failures, 60.0) for failures in range(1, 8)]

    assert delays == [5.0, 10.0, 20.0, 40.0, 60.0, 60.0, 60.0]
    assert console_mod._backoff_delay(5.0, 100_000, 60.0) == 60.0  # a week offline must not overflow
    assert console_mod._backoff_delay(0.0, 7, 60.0) == 0.0  # tests that zero the delay keep it zero


def test_backend_failure_log_rate_limits_repeats_of_the_same_error() -> None:
    """First failure and any new error text warn; repeats warn once per 10 min with a count."""
    now = [0.0]
    log = console_mod._BackendFailureLog(clock=lambda: now[0])

    assert log.record("RuntimeError: offline") == (logging.WARNING, 0)
    for _ in range(3):
        now[0] += 60.0
        assert log.record("RuntimeError: offline") == (logging.DEBUG, 0)
    now[0] = 600.0
    assert log.record("RuntimeError: offline") == (logging.WARNING, 4)
    now[0] += 5.0
    assert log.record("RuntimeError: offline") == (logging.DEBUG, 0)
    assert log.record("OSError: name resolution") == (logging.WARNING, 0)

    log.reset()
    assert log.record("OSError: name resolution") == (logging.WARNING, 0)


@pytest.mark.asyncio
async def test_startup_loop_backs_off_exponentially_and_resets_after_a_successful_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failed starts wait 5 -> 60 s; a start that ran resets the next failure to 5 s."""
    monkeypatch.setattr(console_mod, "has_hf_realtime_target", lambda: True)
    offline = RuntimeError("Temporary failure in name resolution")
    handler = _failing_handler(*([offline] * 6), None, offline, offline)
    stream = LocalStream(handler, _offline_robot())

    delays = await _record_startup_waits(stream, waits=9)

    assert delays == [5.0, 10.0, 20.0, 40.0, 60.0, 60.0, 5.0, 5.0, 10.0]
    assert handler.start_up.await_count == 9


@pytest.mark.asyncio
async def test_startup_loop_logs_repeated_failures_at_debug_and_warns_every_10_minutes(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The journal gets one warning per error text per 10 min, not one every retry."""
    monkeypatch.setattr(console_mod, "has_hf_realtime_target", lambda: True)
    offline = RuntimeError("Temporary failure in name resolution")
    handler = _failing_handler(offline, offline, offline, RuntimeError("server rejected the key"))
    stream = LocalStream(handler, _offline_robot())
    ticks = iter([0.0, 300.0, 600.0, 900.0])
    stream._backend_failure_log = console_mod._BackendFailureLog(clock=lambda: next(ticks))
    caplog.set_level(logging.DEBUG, logger="reachy_companion.console")

    await _record_startup_waits(stream, waits=4)

    failures = [record for record in caplog.records if "Backend failed to start" in record.getMessage()]
    assert [record.levelno for record in failures] == [logging.WARNING, logging.DEBUG, logging.WARNING, logging.WARNING]
    assert "2 times" in failures[2].getMessage()
    assert "times" not in failures[0].getMessage()
    assert "server rejected the key" in failures[3].getMessage()


@pytest.mark.asyncio
async def test_restart_request_cuts_the_backoff_wait_short_and_restarts_the_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A settings change still wakes the loop at once, and its first failure waits the base delay again."""
    monkeypatch.setattr(console_mod, "has_hf_realtime_target", lambda: True)
    handlers: list[MagicMock] = []

    def handler_factory(_voice: str | None) -> MagicMock:
        handler = _failing_handler(RuntimeError("offline"), RuntimeError("offline"))
        handlers.append(handler)
        return handler

    stream = LocalStream(handler_factory(None), _offline_robot(), handler_factory=handler_factory)
    stream._backend_retry_delay = 30.0
    delays: list[float] = []
    real_wait = stream._sleep_or_restart_requested

    async def recording_wait(delay: float) -> None:
        delays.append(delay)
        await real_wait(delay)

    stream._sleep_or_restart_requested = recording_wait  # type: ignore[method-assign]
    startup_task = asyncio.create_task(stream._run_handler_startup_loop())
    try:
        await _wait_until(lambda: len(delays) == 1)
        await stream.request_backend_restart("backend_config_changed")
        # Without the wake-up this would take the full 30 s wait; _wait_until gives up after 1 s.
        await _wait_until(lambda: len(delays) == 2)

        assert delays == [30.0, 30.0]  # not 60 s: the restart began a fresh backoff
        assert len(handlers) == 2
        handlers[1].start_up.assert_awaited_once()
    finally:
        stream._stop_event.set()
        startup_task.cancel()
        try:
            await startup_task
        except asyncio.CancelledError:
            pass
