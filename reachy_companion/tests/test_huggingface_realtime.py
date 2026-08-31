import time
import asyncio
import logging
from types import SimpleNamespace
from typing import Any
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

import reachy_companion.conversation_handler as conv_mod
import reachy_companion.huggingface_realtime as hf_mod
from reachy_companion.tools import core_tools
from reachy_companion.config import config, get_default_voice
from reachy_companion.people import add_person_fact
from reachy_companion.face_id import Identification
from reachy_companion.tools.core_tools import ToolDependencies
from reachy_companion.huggingface_realtime import HuggingFaceRealtimeHandler
from reachy_companion.tools.background_tool_manager import ToolState, ToolCallRoutine, ToolNotification


HF_DEFAULT_VOICE = get_default_voice()


@pytest.fixture(autouse=True)
def _isolate_the_default_stores(tmp_path_factory: pytest.TempPathFactory, monkeypatch: Any) -> None:
    """Keep the `instance_path=None` cases off the developer's real stores.

    The late-recognition path reads `people.v1.json`, and with no instance path
    the stores resolve under `XDG_DATA_HOME`. Pointing that at a fresh tmp dir
    makes every default-path test read an empty store instead of whatever the
    machine happens to have enrolled. Same fixture, same reason, as
    `tests/test_face_tools.py`.
    """
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path_factory.mktemp("xdg")))


class _FakeEvent:
    """A minimal realtime event: a `type` plus arbitrary attributes."""

    def __init__(self, event_type: str, **fields: Any) -> None:
        """Store the event type and any extra attributes."""
        self.type = event_type
        self.__dict__.update(fields)


def _make_fake_realtime_client(
    *,
    events: tuple[_FakeEvent, ...] = (),
    captured_update: dict[str, Any] | None = None,
    captured_connect: dict[str, Any] | None = None,
    update_calls: list[dict[str, Any]] | None = None,
    reject_updates: int = 0,
) -> Any:
    """Build a fake AsyncOpenAI-shaped client whose realtime session yields `events`.

    When given, `captured_update`/`captured_connect` record the kwargs passed to
    `session.update(...)` / `realtime.connect(...)`. `update_calls`, unlike
    `captured_update`, appends every call rather than overwriting, so a test can
    inspect a retried update as well as the original. `reject_updates` makes
    that many leading `update()` calls raise before any of them succeed — used
    to exercise the legacy-transcription fallback retry.
    """

    class FakeSession:
        def __init__(self) -> None:
            self._remaining_rejections = reject_updates

        async def update(self, **kwargs: Any) -> None:
            if update_calls is not None:
                update_calls.append(kwargs)
            if self._remaining_rejections > 0:
                self._remaining_rejections -= 1
                raise RuntimeError("session.update rejected (fake)")
            if captured_update is not None:
                captured_update.update(kwargs)

    class FakeNoop:
        async def append(self, **_kw: Any) -> None:
            pass

        async def create(self, **_kw: Any) -> None:
            pass

        async def cancel(self, **_kw: Any) -> None:
            pass

    class FakeConversation:
        item = FakeNoop()

    class FakeConn:
        session = FakeSession()
        input_audio_buffer = FakeNoop()
        conversation = FakeConversation()
        response = FakeNoop()

        def __init__(self) -> None:
            self._events = iter(events)

        async def __aenter__(self) -> "FakeConn":
            return self

        async def __aexit__(self, *_args: Any) -> bool:
            return False

        async def close(self) -> None:
            pass

        def __aiter__(self) -> "FakeConn":
            return self

        async def __anext__(self) -> _FakeEvent:
            try:
                return next(self._events)
            except StopIteration:
                raise StopAsyncIteration

    class FakeRealtime:
        def connect(self, **kwargs: Any) -> FakeConn:
            if captured_connect is not None:
                captured_connect.update(kwargs)
            return FakeConn()

    class FakeClient:
        realtime = FakeRealtime()

    return FakeClient()


def _fake_openai_client(captured_kwargs: dict[str, Any]) -> type:
    """Return a fake AsyncOpenAI class that records its constructor kwargs."""

    class FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            captured_kwargs.update(kwargs)

    return FakeClient


def _fake_allocator(
    connect_url: str,
    posts: list[tuple[str, dict[str, str] | None, dict[str, str] | None]],
) -> type:
    """Return a fake httpx.AsyncClient that records allocator requests."""

    class FakeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, str]:
            return {"session_id": "session-123", "connect_url": connect_url}

    class FakeAsyncClient:
        def __init__(self, **_kw: Any) -> None:
            pass

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, *_a: Any) -> bool:
            return False

        async def post(
            self,
            url: str,
            headers: dict[str, str] | None = None,
            json: dict[str, str] | None = None,
        ) -> FakeResponse:
            posts.append((url, headers, json))
            return FakeResponse()

    return FakeAsyncClient


@pytest.mark.asyncio
async def test_partial_transcription_uses_latest_snapshot(monkeypatch: Any) -> None:
    """Partial transcription snapshots should replace older snapshots for the same item."""
    monkeypatch.setattr(hf_mod, "get_session_instructions", lambda _instance_path=None: "test")
    monkeypatch.setattr(hf_mod, "get_session_voice", lambda default=HF_DEFAULT_VOICE: "Aiden")
    monkeypatch.setattr(hf_mod, "get_tool_specs", lambda: [])

    handler = HuggingFaceRealtimeHandler(ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock()))
    handler.client = _make_fake_realtime_client(
        events=(
            _FakeEvent("conversation.item.input_audio_transcription.delta", item_id="item-1", delta="Hey"),
            _FakeEvent(
                "conversation.item.input_audio_transcription.delta", item_id="item-1", delta="Hey, how are you?"
            ),
        )
    )
    monkeypatch.setattr(type(handler.tool_manager), "start_up", MagicMock())
    monkeypatch.setattr(type(handler.tool_manager), "shutdown", AsyncMock())

    await handler._run_realtime_session()

    assert handler.input_transcript_chunks_by_item.item_id == "item-1"
    assert handler.input_transcript_chunks_by_item.deltas == ["Hey, how are you?"]


@pytest.mark.asyncio
async def test_emit_skips_idle_signal_while_response_active(monkeypatch: Any) -> None:
    """Idle tools should not trigger while a response is still active."""
    movement_manager = MagicMock()
    movement_manager.is_idle.return_value = True
    deps = ToolDependencies(reachy_mini=MagicMock(), movement_manager=movement_manager)
    handler = HuggingFaceRealtimeHandler(deps)
    handler.last_activity_time = time.monotonic() - (handler.IDLE_BEHAVIOR_THRESHOLD_S + 10.0)
    handler._response_done_event.clear()

    send_idle_signal = AsyncMock()
    monkeypatch.setattr(handler, "send_idle_signal", send_idle_signal)
    monkeypatch.setattr(conv_mod, "wait_for_item", AsyncMock(return_value=None))

    result = await handler.emit()

    assert result is None
    send_idle_signal.assert_not_awaited()


@pytest.mark.asyncio
async def test_parallel_tool_calls_trigger_single_response(monkeypatch: Any) -> None:
    """Parallel tool calls in one turn should yield one response, not one per completed tool."""
    monkeypatch.setattr(hf_mod, "get_session_instructions", lambda _instance_path=None: "test")
    monkeypatch.setattr(hf_mod, "get_session_voice", lambda default=HF_DEFAULT_VOICE: "Aiden")
    monkeypatch.setattr(hf_mod, "get_tool_specs", lambda: [])

    handler = HuggingFaceRealtimeHandler(ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock()))
    handler.connection = AsyncMock()
    handler.output_queue = asyncio.Queue()
    monkeypatch.setattr(handler, "_wait_for_response_done_before_tool_result", AsyncMock(return_value=True))
    create = AsyncMock()
    monkeypatch.setattr(handler, "_safe_response_create", create)

    handler._in_flight_tool_calls = {"call_a", "call_b"}

    def _completed(call_id: str) -> ToolNotification:
        return ToolNotification(
            id=call_id,
            tool_name="test__parallel_probe",
            is_idle_tool_call=False,
            status=ToolState.COMPLETED,
            result={"ok": True},
        )

    await handler._handle_tool_result(_completed("call_a"))
    assert create.await_count == 0

    await handler._handle_tool_result(_completed("call_b"))
    assert create.await_count == 1


@pytest.mark.asyncio
async def test_tool_registry_crash_does_not_wedge_the_conversation(monkeypatch: Any) -> None:
    """A crash before the dispatcher's guard must still answer the model for that call_id.

    `get_tools()` used to run outside `_dispatch_tool_call`'s try, and
    `_run_tool` awaited the routine unguarded: a registry failure mid-session
    killed the background task silently, stranded the call_id in
    `_in_flight_tool_calls`, and the response trigger never fired again.
    """
    handler = HuggingFaceRealtimeHandler(ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock()))
    handler.connection = AsyncMock()
    handler.output_queue = asyncio.Queue()
    monkeypatch.setattr(handler, "_wait_for_response_done_before_tool_result", AsyncMock(return_value=True))
    create = AsyncMock()
    monkeypatch.setattr(handler, "_safe_response_create", create)

    def _explode() -> dict[str, Any]:
        raise RuntimeError("registry exploded")

    monkeypatch.setattr(core_tools, "get_tools", _explode)

    handler.tool_manager.start_up(tool_callbacks=[handler._handle_tool_result])
    try:
        handler._in_flight_tool_calls.add("call-x")
        await handler.tool_manager.start_tool(
            call_id="call-x",
            tool_call_routine=ToolCallRoutine(tool_name="camera", args_json_str="{}", deps=handler.deps),
            is_idle_tool_call=False,
        )
        await asyncio.sleep(0.1)
    finally:
        await handler.tool_manager.shutdown()

    (submitted,) = handler.connection.conversation.item.create.await_args_list
    item = submitted.kwargs["item"]
    assert item["type"] == "function_call_output"
    assert item["call_id"] == "call-x"
    assert "registry exploded" in item["output"]

    assert handler._in_flight_tool_calls == set()
    assert create.await_count == 1


def test_handler_uses_hf_startup_voice_at_startup(monkeypatch: Any) -> None:
    """Hugging Face startup should restore persisted HF voices."""
    handler = HuggingFaceRealtimeHandler(
        ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock()),
        startup_voice="marin",
    )

    assert handler.get_current_voice() == "marin"


def test_handler_ignores_unsupported_hf_profile_voice(monkeypatch: Any) -> None:
    """Unsupported profile voices should not be sent to the Hugging Face backend."""
    monkeypatch.setattr(hf_mod, "get_session_voice", lambda default=HF_DEFAULT_VOICE: "Aiden")

    handler = HuggingFaceRealtimeHandler(ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock()))

    assert handler.get_current_voice() == HF_DEFAULT_VOICE
    session = handler._get_session_config([])
    assert session["audio"]["output"]["voice"] == HF_DEFAULT_VOICE


def test_handler_normalizes_hf_voice_case(monkeypatch: Any) -> None:
    """Differently-cased speaker names should resolve to the curated UI value."""
    monkeypatch.setattr(hf_mod, "get_session_voice", lambda default=HF_DEFAULT_VOICE: "MARIN")

    handler = HuggingFaceRealtimeHandler(ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock()))

    assert handler.get_current_voice() == "marin"


@pytest.mark.asyncio
async def test_run_realtime_session_uses_default_voice_for_lb_allocated_sessions(monkeypatch: Any) -> None:
    """Use the backend default speaker when no profile voice is selected for the hf LB."""
    monkeypatch.setattr(hf_mod, "get_session_instructions", lambda _instance_path=None: "test")
    monkeypatch.setattr(hf_mod, "get_session_voice", lambda default=HF_DEFAULT_VOICE: default)
    monkeypatch.setattr(hf_mod, "get_tool_specs", lambda: [])
    monkeypatch.setattr(config, "HF_REALTIME_SESSION_URL", "https://lb.example.test/session")

    captured_update: dict[str, Any] = {}
    handler = HuggingFaceRealtimeHandler(ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock()))
    handler.client = _make_fake_realtime_client(captured_update=captured_update)

    await handler._run_realtime_session()

    session = captured_update["session"]
    # HF at 16 kHz passes None so the backend uses its optimal default (16 kHz).
    assert session["audio"]["input"]["format"]["rate"] is None
    assert session["audio"]["output"]["format"]["rate"] is None
    assert session["audio"]["input"]["transcription"]["language"] == "zh"
    assert session["audio"]["output"]["voice"] == HF_DEFAULT_VOICE


def test_huggingface_session_uses_configured_transcription_language(monkeypatch: Any) -> None:
    """Hugging Face realtime sessions should forward the configured transcription language."""
    monkeypatch.setattr(config, "REALTIME_TRANSCRIPTION_LANGUAGE", "zh")
    handler = HuggingFaceRealtimeHandler(ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock()))

    session = handler._get_session_config([])

    assert session["audio"]["input"]["transcription"]["language"] == "zh"


@pytest.mark.asyncio
async def test_run_realtime_session_passes_allocated_session_query(monkeypatch: Any) -> None:
    """Hugging Face sessions must forward the allocated session token to the websocket connect call."""
    monkeypatch.setattr(hf_mod, "get_session_instructions", lambda _instance_path=None: "test")
    monkeypatch.setattr(hf_mod, "get_session_voice", lambda default=HF_DEFAULT_VOICE: default)
    monkeypatch.setattr(hf_mod, "get_tool_specs", lambda: [])

    captured_connect: dict[str, Any] = {}
    handler = HuggingFaceRealtimeHandler(ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock()))
    handler.client = _make_fake_realtime_client(captured_connect=captured_connect)
    handler._realtime_connect_query = {"session_token": "abc123"}

    await handler._run_realtime_session()

    assert "model" not in captured_connect
    assert captured_connect["extra_query"] == {"session_token": "abc123"}


@pytest.mark.parametrize(("hf_token", "expected_api_key"), [(None, "DUMMY"), ("hf-secret", "hf-secret")])
@pytest.mark.asyncio
async def test_build_realtime_client_local_uses_explicit_hf_token_only(
    monkeypatch: Any,
    hf_token: str | None,
    expected_api_key: str,
) -> None:
    """Local websocket mode must never forward cached Hugging Face credentials."""
    client_kwargs: dict[str, Any] = {}

    def _no_allocator(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("session allocator should not be called in direct websocket mode")

    monkeypatch.setattr(hf_mod, "AsyncOpenAI", _fake_openai_client(client_kwargs))
    monkeypatch.setattr(hf_mod.httpx, "AsyncClient", _no_allocator)
    monkeypatch.setattr(config, "HF_REALTIME_CONNECTION_MODE", "local")
    monkeypatch.setattr(config, "HF_REALTIME_SESSION_URL", "https://lb.example.test/session")
    monkeypatch.setattr(config, "HF_TOKEN", hf_token)
    monkeypatch.setattr(hf_mod, "get_token", lambda: "hf-cached")
    monkeypatch.setattr(
        config,
        "HF_REALTIME_WS_URL",
        "ws://127.0.0.1:8765/v1/realtime?session_token=abc123&model=ignored-by-sdk",
    )

    handler = HuggingFaceRealtimeHandler(ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock()))

    client = await handler._build_realtime_client()

    assert client is not None
    assert client_kwargs["api_key"] == expected_api_key
    assert client_kwargs["base_url"] == "http://127.0.0.1:8765/v1"
    assert client_kwargs["websocket_base_url"] == "ws://127.0.0.1:8765/v1"
    assert handler._realtime_connect_query == {"session_token": "abc123"}


@pytest.mark.parametrize(
    (
        "hf_token",
        "cached_token",
        "hardware_id",
        "status_error",
        "expected_header",
        "expected_api_key",
        "expected_payload",
    ),
    [
        (
            "hf-secret",
            "hf-cached",
            "0123456789abcdef",
            None,
            {
                "User-Agent": "reachy-mini-conversation-app",
                "X-Reachy-Mini-Authorization": "Bearer hf-secret",
            },
            "hf-secret",
            {"hardware_id": "0123456789abcdef"},
        ),
        (
            None,
            "hf-cached",
            None,
            None,
            {
                "User-Agent": "reachy-mini-conversation-app",
                "X-Reachy-Mini-Authorization": "Bearer hf-cached",
            },
            "hf-cached",
            {},
        ),
        (None, None, None, None, {"User-Agent": "reachy-mini-conversation-app"}, "DUMMY", {}),
        (
            None,
            None,
            None,
            TimeoutError("status unavailable"),
            {"User-Agent": "reachy-mini-conversation-app"},
            "DUMMY",
            {},
        ),
    ],
)
@pytest.mark.asyncio
async def test_build_realtime_client_deployed_resolves_hf_token(
    monkeypatch: Any,
    caplog: pytest.LogCaptureFixture,
    hf_token: str | None,
    cached_token: str | None,
    hardware_id: str | None,
    status_error: Exception | None,
    expected_header: dict[str, str],
    expected_api_key: str,
    expected_payload: dict[str, str],
) -> None:
    """Deployed allocation reports available credentials and robot identity."""
    client_kwargs: dict[str, Any] = {}
    posts: list[tuple[str, dict[str, str] | None, dict[str, str] | None]] = []
    connect_url = "wss://hf.example.test/v1/realtime?session_token=allocated"
    monkeypatch.setattr(hf_mod, "AsyncOpenAI", _fake_openai_client(client_kwargs))
    monkeypatch.setattr(hf_mod.httpx, "AsyncClient", _fake_allocator(connect_url, posts))
    monkeypatch.setattr(config, "HF_REALTIME_CONNECTION_MODE", "deployed")
    monkeypatch.setattr(config, "HF_REALTIME_SESSION_URL", "https://lb.example.test/session")
    # A stale local URL must be ignored in deployed mode.
    monkeypatch.setattr(config, "HF_REALTIME_WS_URL", "ws://127.0.0.1:8765/v1/realtime")
    monkeypatch.setattr(config, "HF_TOKEN", hf_token)
    monkeypatch.setattr(hf_mod, "get_token", lambda: cached_token)

    reachy_mini = MagicMock()
    reachy_mini.client.get_status.return_value.hardware_id = hardware_id
    if status_error:
        reachy_mini.client.get_status.side_effect = status_error
    handler = HuggingFaceRealtimeHandler(ToolDependencies(reachy_mini=reachy_mini, movement_manager=MagicMock()))

    client = await handler._build_realtime_client()

    assert client is not None
    assert posts == [("https://lb.example.test/session", expected_header, expected_payload)]
    reachy_mini.client.get_status.assert_called_once_with(wait=False)
    if status_error:
        assert "Daemon status unavailable for realtime session allocation" in caplog.text
    assert client_kwargs["api_key"] == expected_api_key
    assert client_kwargs["base_url"] == "https://hf.example.test/v1"
    assert client_kwargs["websocket_base_url"] == "wss://hf.example.test/v1"
    assert handler._realtime_connect_query == {"session_token": "allocated"}


@pytest.mark.asyncio
async def test_apply_personality_uses_selected_voice_for_lb_allocated_sessions(monkeypatch: Any) -> None:
    """Live personality updates should honor the selected backend voice."""
    monkeypatch.setattr(hf_mod, "get_session_instructions", lambda _instance_path=None: "new instructions")
    monkeypatch.setattr(hf_mod, "get_session_voice", lambda default=HF_DEFAULT_VOICE: "marin")
    monkeypatch.setattr(config, "HF_REALTIME_SESSION_URL", "https://lb.example.test/session")

    captured_update: dict[str, Any] = {}

    class FakeSession:
        async def update(self, **kwargs: Any) -> None:
            captured_update.update(kwargs)

    class FakeConnection:
        session = FakeSession()

    handler = HuggingFaceRealtimeHandler(ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock()))
    handler.connection = FakeConnection()
    monkeypatch.setattr(handler, "_restart_session", AsyncMock(return_value=None))

    result = await handler.apply_personality("mars_rover")

    assert "restarted realtime session" in result.lower()
    session = captured_update["session"]
    assert session["instructions"] == "new instructions"
    assert session["audio"]["output"]["voice"] == "marin"


@pytest.mark.asyncio
async def test_apply_personality_restores_profile_when_tools_fail(monkeypatch: Any) -> None:
    """A failed tool reload should leave the previous profile selected."""
    selected_profiles: list[str | None] = []

    def select_profile(profile: str | None) -> None:
        selected_profiles.append(profile)
        config.REACHY_MINI_CUSTOM_PROFILE = profile

    def fail_tool_reload(*, force: bool = False) -> None:
        raise RuntimeError("tool reload failed")

    monkeypatch.setattr(config, "REACHY_MINI_CUSTOM_PROFILE", "default")
    monkeypatch.setattr(hf_mod, "set_custom_profile", select_profile)
    monkeypatch.setattr(hf_mod, "get_session_instructions", lambda _instance_path=None: "new instructions")
    monkeypatch.setattr(hf_mod.core_tools, "initialize_tools", fail_tool_reload)
    handler = HuggingFaceRealtimeHandler(ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock()))

    result = await handler.apply_personality("broken")

    assert result == "Failed to apply personality: tool reload failed"
    assert config.REACHY_MINI_CUSTOM_PROFILE == "default"
    assert selected_profiles == ["broken", "default"]


@pytest.mark.asyncio
async def test_change_voice_updates_live_hf_session_without_restart(monkeypatch: Any) -> None:
    """Changing Hugging Face voice should update the active session in place."""
    captured_update: dict[str, Any] = {}

    class FakeSession:
        async def update(self, **kwargs: Any) -> None:
            captured_update.update(kwargs)

    class FakeConnection:
        session = FakeSession()

    handler = HuggingFaceRealtimeHandler(ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock()))
    handler.connection = FakeConnection()
    restart = AsyncMock(return_value=None)
    monkeypatch.setattr(handler, "_restart_session", restart)

    result = await handler.change_voice("marin")

    assert result == "Voice changed to marin."
    assert handler.get_current_voice() == "marin"
    restart.assert_not_awaited()
    session = captured_update["session"]
    assert session["audio"]["output"]["voice"] == "marin"


# --- extended wake face window (Task 5) -------------------------------------
# The quick pre-greeting check owns ~1200 ms at the instant of boot, and on all
# 14 recorded robot boots nobody was posed in frame yet. These cover the bounded
# extension that keeps looking *after* the greeting was queued, and the two
# things it must never do: speak into a turn the user has started, or inject
# into a session that has already been replaced.

WAKE_GREETING = "用一句简短自然的中文主动问候用户。"


class _WakeRecognizer:
    """A FaceRecognizer stand-in with scripted answers, for the wake window."""

    def __init__(self, results: list[Identification]) -> None:
        """Store the scripted identifications; the last one repeats once exhausted."""
        self._results = list(results)
        # The real class carries both, and the hook reads `enabled` before it
        # spends anything; a test can flip either one directly.
        self.enabled = True
        self.ready = True
        # Called inside `identify`, so a test can move the world (a reconnect)
        # at the exact moment a hit is produced.
        self.on_identify: Any = None
        self.ready_calls = 0
        self.frames_seen = 0

    def wait_ready(self, timeout_s: float) -> bool:
        """Report readiness, counting the call so a test can assert it never happened."""
        self.ready_calls += 1
        return self.ready

    def identify(self, frame: Any) -> Identification:
        """Return the next scripted identification, running `on_identify` first."""
        self.frames_seen += 1
        if self.on_identify is not None:
            self.on_identify()
        return self._results.pop(0) if len(self._results) > 1 else self._results[0]


class _CapturingConversationItem:
    """`conversation.item`, recording every created item into a shared list."""

    def __init__(self, created_items: list[dict[str, Any]]) -> None:
        """Record into the connection's list rather than one of its own."""
        self._created_items = created_items

    async def create(self, item: dict[str, Any]) -> None:
        """Record the item the handler wanted to put into the conversation."""
        self._created_items.append(item)


class _CapturingConnection:
    """A minimal realtime connection exposing only `conversation.item.create`."""

    def __init__(self) -> None:
        """Start with an empty transcript of created items."""
        self.created_items: list[dict[str, Any]] = []
        self.item = _CapturingConversationItem(self.created_items)

    @property
    def conversation(self) -> "_CapturingConnection":
        """Expose `.conversation.item` without a second object."""
        return self


def _wake_handler(
    recognizer: Any,
    monkeypatch: Any,
    *,
    camera_enabled: bool = True,
    instance_path: Any = None,
) -> Any:
    """Build a handler wired to a capturing connection and a counting response sender."""
    # The real 0.7 s pause between looks is a robot-time value, not a test one.
    monkeypatch.setattr(hf_mod, "_FACE_WAKE_EXTENDED_PAUSE_S", 0.01)
    monkeypatch.setattr(hf_mod, "get_session_greeting_prompt", lambda: WAKE_GREETING)
    reachy_mini = MagicMock()
    reachy_mini.media.get_frame.return_value = object()
    handler = HuggingFaceRealtimeHandler(
        ToolDependencies(
            reachy_mini=reachy_mini,
            movement_manager=MagicMock(),
            instance_path=instance_path,
            camera_enabled=camera_enabled,
            face_recognizer=recognizer,
        )
    )
    handler.connection = _CapturingConnection()
    handler.instance_path = None
    # The ordinary case a late hit lands in: the greeting has finished playing
    # and the boot gate is already open. The two tests that care hold it shut.
    handler._boot_gate_active = False
    monkeypatch.setattr(handler, "_safe_response_create", AsyncMock())
    return handler


async def _drop_task(task: Any) -> None:
    """Cancel a spawned wake task and await it, so nothing leaks into the next test."""
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_extended_wake_check_injects_late_recognition(monkeypatch: Any) -> None:
    """Miss, then a hit: a context item is created and a response is requested.

    Ordering against the boot greeting is the sender loop's job, so there is no
    active-response precondition to assert here.
    """
    recognizer = _WakeRecognizer(
        [
            Identification(status="unknown", score=0.1, face_count=1),
            Identification(status="recognized", name="小明", score=0.59, face_count=1),
        ]
    )
    handler = _wake_handler(recognizer, monkeypatch)

    await handler._extended_wake_face_check()

    (item,) = handler.connection.created_items
    assert item["role"] == "user"
    assert "小明" in item["content"][0]["text"]
    assert handler._safe_response_create.await_count == 1
    assert recognizer.frames_seen == 2


@pytest.mark.asyncio
async def test_extended_wake_check_carries_what_it_remembers(monkeypatch: Any, tmp_path: Path) -> None:
    """A late hit on someone with a history greets them with it, not just by name.

    Same payoff as the boot greeting, one window later: the person walked into
    frame after the greeting went out, and the follow-up still knows them.
    """
    add_person_fact(tmp_path, "Lena", "在准备一场马拉松")
    recognizer = _WakeRecognizer([Identification(status="recognized", name="Lena", score=0.8, face_count=1)])
    handler = _wake_handler(recognizer, monkeypatch, instance_path=tmp_path)

    await handler._extended_wake_face_check()

    (item,) = handler.connection.created_items
    text = item["content"][0]["text"]
    assert text == hf_mod._FACE_LATE_KNOWN_WITH_FACTS_PROMPT.format(name="Lena", facts="在准备一场马拉松")
    assert "Lena" in text
    assert "在准备一场马拉松" in text
    assert handler.deps.current_person == "Lena"


@pytest.mark.asyncio
async def test_extended_wake_check_without_facts_uses_the_plain_late_prompt(
    monkeypatch: Any, tmp_path: Path
) -> None:
    """Nothing on file is not a degraded case: the plain named prompt, unchanged."""
    recognizer = _WakeRecognizer([Identification(status="recognized", name="Lena", score=0.8, face_count=1)])
    handler = _wake_handler(recognizer, monkeypatch, instance_path=tmp_path)

    await handler._extended_wake_face_check()

    (item,) = handler.connection.created_items
    assert item["content"][0]["text"] == hf_mod._FACE_LATE_RECOGNITION_PROMPT.format(name="Lena")
    assert handler.deps.current_person == "Lena"


@pytest.mark.asyncio
async def test_extended_wake_check_survives_an_unreadable_person_store(
    monkeypatch: Any, tmp_path: Path
) -> None:
    """A fact lookup that blows up costs the recall, never the late greeting or the label."""

    def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise OSError("people.v1.json is on fire")

    monkeypatch.setattr(hf_mod, "facts_for_person", _boom)
    recognizer = _WakeRecognizer([Identification(status="recognized", name="Lena", score=0.8, face_count=1)])
    handler = _wake_handler(recognizer, monkeypatch, instance_path=tmp_path)

    await handler._extended_wake_face_check()

    (item,) = handler.connection.created_items
    assert item["content"][0]["text"] == hf_mod._FACE_LATE_RECOGNITION_PROMPT.format(name="Lena")
    assert handler._safe_response_create.await_count == 1
    assert handler.deps.current_person == "Lena"


@pytest.mark.asyncio
async def test_extended_wake_check_drops_a_hit_when_the_session_changes_mid_recall(
    monkeypatch: Any, tmp_path: Path
) -> None:
    """The recall is an await too, so the staleness re-check has to sit after it.

    A label written once the session has been replaced would land in the session
    that just cleared it — the one leak the per-session clear exists to prevent.
    """
    recognizer = _WakeRecognizer([Identification(status="recognized", name="Lena", score=0.8, face_count=1)])
    handler = _wake_handler(recognizer, monkeypatch, instance_path=tmp_path)
    original = handler.connection
    replacement = _CapturingConnection()

    def _reconnect_while_reading(*_args: Any, **_kwargs: Any) -> list[Any]:
        handler.connection = replacement
        return []

    monkeypatch.setattr(hf_mod, "facts_for_person", _reconnect_while_reading)

    await handler._extended_wake_face_check()

    assert original.created_items == []
    assert replacement.created_items == []
    assert handler._safe_response_create.await_count == 0
    assert handler.deps.current_person is None


@pytest.mark.asyncio
async def test_extended_wake_check_goes_silent_after_user_spoke(monkeypatch: Any) -> None:
    """Once the user has spoken, the window closes without injecting anything.

    A context item landing mid-turn could steer the model's answer; from that
    point identity belongs to the routed tools, not to the wake hook.
    """
    recognizer = _WakeRecognizer([Identification(status="recognized", name="小明", score=0.8, face_count=1)])
    handler = _wake_handler(recognizer, monkeypatch)
    handler._user_has_spoken = True

    await handler._extended_wake_face_check()

    assert handler.connection.created_items == []
    assert handler._safe_response_create.await_count == 0
    assert recognizer.frames_seen == 0


@pytest.mark.asyncio
async def test_extended_wake_check_drops_a_hit_when_the_user_speaks_mid_round(monkeypatch: Any) -> None:
    """The user starts talking while the round is in flight: the hit is dropped, unspoken.

    The loop condition passed before the look began, so only the second half of
    the double-check — the one immediately before `item.create` — can stop this
    item from landing in the middle of a turn the user is already waiting on.
    """
    recognizer = _WakeRecognizer([Identification(status="recognized", name="小明", score=0.8, face_count=1)])
    handler = _wake_handler(recognizer, monkeypatch)

    def _user_speaks() -> None:
        handler._user_has_spoken = True

    recognizer.on_identify = _user_speaks

    await handler._extended_wake_face_check()

    assert recognizer.frames_seen == 1
    assert handler.connection.created_items == []
    assert handler._safe_response_create.await_count == 0


@pytest.mark.asyncio
async def test_extended_wake_check_waits_for_the_boot_gate_before_injecting(monkeypatch: Any) -> None:
    """A hit while the greeting is still playing waits for the gate, then speaks.

    The boot gate's drain cap is not restarted by a second `response.done`, so a
    late greeting queued while the gate is shut can still be speaking when the
    cap opens the microphone — the echo turn the gate exists to prevent.
    """
    recognizer = _WakeRecognizer([Identification(status="recognized", name="小明", score=0.8, face_count=1)])
    handler = _wake_handler(recognizer, monkeypatch)
    handler._boot_gate_active = True

    async def _release_gate() -> None:
        await asyncio.sleep(0.05)
        handler._boot_gate_active = False

    releaser = asyncio.create_task(_release_gate())
    await handler._extended_wake_face_check()
    await releaser

    (item,) = handler.connection.created_items
    assert "小明" in item["content"][0]["text"]
    assert handler._safe_response_create.await_count == 1


@pytest.mark.asyncio
async def test_extended_wake_check_drops_a_hit_when_the_boot_gate_never_opens(monkeypatch: Any) -> None:
    """A gate that outlasts the window closes it: no item, no response, no echo turn."""
    monkeypatch.setenv("FACE_WAKE_EXTENDED_MS", "300")
    recognizer = _WakeRecognizer([Identification(status="recognized", name="小明", score=0.8, face_count=1)])
    handler = _wake_handler(recognizer, monkeypatch)
    handler._boot_gate_active = True

    started = time.monotonic()
    await handler._extended_wake_face_check()
    elapsed_ms = (time.monotonic() - started) * 1000.0

    assert handler.connection.created_items == []
    assert handler._safe_response_create.await_count == 0
    # The gate wait is inside the window's budget, not on top of it.
    assert elapsed_ms < 1500.0


@pytest.mark.asyncio
async def test_speech_started_marks_that_the_user_has_spoken(monkeypatch: Any) -> None:
    """The receiver loop is what sets the flag the wake window watches.

    Asserted through a real session run rather than by calling the branch's
    helpers, because the flag lives on the branch itself.
    """
    monkeypatch.setattr(hf_mod, "get_session_instructions", lambda _instance_path=None: "test")
    monkeypatch.setattr(hf_mod, "get_session_voice", lambda default=HF_DEFAULT_VOICE: "Aiden")
    monkeypatch.setattr(hf_mod, "get_tool_specs", lambda: [])

    handler = HuggingFaceRealtimeHandler(ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock()))
    handler.client = _make_fake_realtime_client(events=(_FakeEvent("input_audio_buffer.speech_started"),))
    monkeypatch.setattr(type(handler.tool_manager), "start_up", MagicMock())
    monkeypatch.setattr(type(handler.tool_manager), "shutdown", AsyncMock())

    assert handler._user_has_spoken is False

    await handler._run_realtime_session()

    assert handler._user_has_spoken is True


# --------------------------------------------------------------------------
# response.done status — the `max_output_tokens` rail tripping is silent
# otherwise: the reply just stops mid-word (2026-08-30 patience wave, Task 7).
# --------------------------------------------------------------------------


async def _run_with_response_done(monkeypatch: Any, response: Any) -> None:
    """Drive one `response.done` carrying `response` through the receiver loop."""
    monkeypatch.setattr(hf_mod, "get_session_instructions", lambda _instance_path=None: "test")
    monkeypatch.setattr(hf_mod, "get_session_voice", lambda default=HF_DEFAULT_VOICE: "Aiden")
    monkeypatch.setattr(hf_mod, "get_tool_specs", lambda: [])

    handler = HuggingFaceRealtimeHandler(ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock()))
    handler.client = _make_fake_realtime_client(events=(_FakeEvent("response.done", response=response),))
    monkeypatch.setattr(type(handler.tool_manager), "start_up", MagicMock())
    monkeypatch.setattr(type(handler.tool_manager), "shutdown", AsyncMock())

    await handler._run_realtime_session()


@pytest.mark.asyncio
async def test_a_reply_cut_by_the_token_rail_warns(monkeypatch: Any, caplog: pytest.LogCaptureFixture) -> None:
    """Hitting the rail truncates mid-word with no wrap-up — that must be visible."""
    with caplog.at_level(logging.INFO, logger="reachy_companion.huggingface_realtime"):
        await _run_with_response_done(
            monkeypatch,
            SimpleNamespace(
                id="resp_1",
                status="incomplete",
                status_details=SimpleNamespace(reason="max_output_tokens"),
            ),
        )

    tripped = [r for r in caplog.records if "REALTIME_MAX_OUTPUT_TOKENS" in r.getMessage()]
    assert len(tripped) == 1
    assert tripped[0].levelno == logging.WARNING


@pytest.mark.asyncio
async def test_another_incomplete_reason_is_logged_at_info(
    monkeypatch: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """Every other non-completed status is context, not an operator action item."""
    with caplog.at_level(logging.INFO, logger="reachy_companion.huggingface_realtime"):
        await _run_with_response_done(
            monkeypatch,
            SimpleNamespace(
                id="resp_1",
                status="cancelled",
                status_details=SimpleNamespace(reason="turn_detected"),
            ),
        )

    ended = [r for r in caplog.records if "response ended" in r.getMessage()]
    assert len(ended) == 1
    assert ended[0].levelno == logging.INFO
    assert "reason=turn_detected" in ended[0].getMessage()
    assert not any("REALTIME_MAX_OUTPUT_TOKENS" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_completed_reply_logs_no_status_line(
    monkeypatch: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """The normal path stays quiet — this fires on every single reply."""
    with caplog.at_level(logging.INFO, logger="reachy_companion.huggingface_realtime"):
        await _run_with_response_done(
            monkeypatch, SimpleNamespace(id="resp_1", status="completed", status_details=None)
        )

    assert "response ended" not in caplog.text


@pytest.mark.asyncio
async def test_extended_wake_check_disabled_by_env(monkeypatch: Any) -> None:
    """`FACE_WAKE_EXTENDED_MS=0` turns the extension off without touching the camera."""
    monkeypatch.setenv("FACE_WAKE_EXTENDED_MS", "0")
    recognizer = _WakeRecognizer([Identification(status="recognized", name="小明", score=0.8, face_count=1)])
    handler = _wake_handler(recognizer, monkeypatch)

    await handler._extended_wake_face_check()

    assert handler.connection.created_items == []
    assert handler._safe_response_create.await_count == 0
    assert recognizer.ready_calls == 0
    assert recognizer.frames_seen == 0


@pytest.mark.asyncio
async def test_extended_wake_check_respects_auto_greet_kill_switch(monkeypatch: Any) -> None:
    """The extension is the same D-013 hook, so the same kill switch silences it."""
    monkeypatch.setenv("FACE_AUTO_GREET", "0")
    recognizer = _WakeRecognizer([Identification(status="recognized", name="小明", score=0.8, face_count=1)])
    handler = _wake_handler(recognizer, monkeypatch)

    await handler._extended_wake_face_check()

    assert handler.connection.created_items == []
    assert handler._safe_response_create.await_count == 0
    assert recognizer.ready_calls == 0
    assert recognizer.frames_seen == 0


@pytest.mark.asyncio
async def test_extended_wake_check_gives_up_at_deadline(monkeypatch: Any) -> None:
    """Nobody recognized: the loop must end on its budget, having created nothing."""
    monkeypatch.setenv("FACE_WAKE_EXTENDED_MS", "200")
    recognizer = _WakeRecognizer([Identification(status="unknown", score=0.1, face_count=1)])
    handler = _wake_handler(recognizer, monkeypatch)

    started = time.monotonic()
    await handler._extended_wake_face_check()
    elapsed_ms = (time.monotonic() - started) * 1000.0

    assert handler.connection.created_items == []
    assert handler._safe_response_create.await_count == 0
    assert recognizer.frames_seen >= 1
    # 200 ms budget plus slack for a busy CI box; far below any unbounded wait.
    assert elapsed_ms < 1500.0


@pytest.mark.asyncio
async def test_extended_wake_check_aborts_on_reconnected_session(monkeypatch: Any) -> None:
    """A hit produced after a reconnect must not be injected into the new session."""
    recognizer = _WakeRecognizer([Identification(status="recognized", name="小明", score=0.8, face_count=1)])
    handler = _wake_handler(recognizer, monkeypatch)
    original = handler.connection
    replacement = _CapturingConnection()

    def _reconnect() -> None:
        handler.connection = replacement

    recognizer.on_identify = _reconnect

    await handler._extended_wake_face_check()

    assert replacement.created_items == []
    assert original.created_items == []
    assert handler._safe_response_create.await_count == 0


@pytest.mark.asyncio
async def test_current_person_is_cleared_for_every_new_session(monkeypatch: Any) -> None:
    """The identity label lives one session, no longer (spec §3.3).

    A reconnect drops into a conversation whose recognition happened in a
    session that is gone; whoever is in the room now must be re-established by
    the wake checks or `who_is_this`, never inherited. Asserted at the moment
    the session config is gathered — before the connection is published and so
    before any tool of the new session could read the label.
    """
    monkeypatch.setattr(hf_mod, "get_session_instructions", lambda _instance_path=None: "test")
    monkeypatch.setattr(hf_mod, "get_session_voice", lambda default=HF_DEFAULT_VOICE: "Aiden")

    deps = ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())
    handler = HuggingFaceRealtimeHandler(deps)
    # A handler that has already greeted: this is the reconnect path, not a boot.
    handler._startup_greeting_sent = True
    deps.current_person = "Lena"

    labels: list[str | None] = []

    def _tool_specs() -> list[Any]:
        labels.append(deps.current_person)
        return []

    monkeypatch.setattr(hf_mod, "get_tool_specs", _tool_specs)
    handler.client = _make_fake_realtime_client(events=(_FakeEvent("input_audio_buffer.speech_started"),))
    monkeypatch.setattr(type(handler.tool_manager), "start_up", MagicMock())
    monkeypatch.setattr(type(handler.tool_manager), "shutdown", AsyncMock())

    await handler._run_realtime_session()

    assert labels == [None]
    assert deps.current_person is None


@pytest.mark.asyncio
async def test_current_person_starts_unset_on_a_fresh_handler() -> None:
    """A handler build is a session boundary too: shared deps must not carry a label in."""
    deps = ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())
    deps.current_person = "Lena"

    HuggingFaceRealtimeHandler(deps)

    assert deps.current_person is None


@pytest.mark.asyncio
async def test_startup_greeting_spawns_extended_check_only_on_a_miss(monkeypatch: Any, tmp_path: Path) -> None:
    """The extension is spawned exactly when the quick check did not place anybody.

    Four sub-cases in one test because they are one rule: an empty frame spawns
    it once, a stranger spawns it too (they may yet be someone Reachy knows, seen
    badly), a quick-check hit has nothing left to look for, and the kill switch
    stops the spawn before a task is ever created.
    """
    empty = _WakeRecognizer([Identification(status="no_face")])
    handler = _wake_handler(empty, monkeypatch, instance_path=tmp_path)

    await handler._send_startup_greeting_prompt()

    (item,) = handler.connection.created_items
    assert item["content"][0]["text"] == WAKE_GREETING
    spawned = handler._wake_face_task
    assert spawned is not None
    # Once per app start: the greeting is already sent, so a second call is a
    # no-op and must not start a second window.
    await handler._send_startup_greeting_prompt()
    assert handler._wake_face_task is spawned
    await _drop_task(spawned)

    stranger = _WakeRecognizer([Identification(status="unknown", score=0.1, face_count=1)])
    met = _wake_handler(stranger, monkeypatch, instance_path=tmp_path)

    await met._send_startup_greeting_prompt()

    (stranger_item,) = met.connection.created_items
    stranger_text = stranger_item["content"][0]["text"]
    assert stranger_text.startswith(hf_mod._FACE_STRANGER_GREETING_PREFIX)
    assert stranger_text.endswith(WAKE_GREETING)
    stranger_task = met._wake_face_task
    assert stranger_task is not None
    await _drop_task(stranger_task)

    hit = _WakeRecognizer([Identification(status="recognized", name="小明", score=0.8, face_count=1)])
    greeted = _wake_handler(hit, monkeypatch, instance_path=tmp_path)

    await greeted._send_startup_greeting_prompt()

    (greeted_item,) = greeted.connection.created_items
    assert "小明" in greeted_item["content"][0]["text"]
    assert greeted._wake_face_task is None

    monkeypatch.setenv("FACE_AUTO_GREET", "0")
    silenced = _wake_handler(
        _WakeRecognizer([Identification(status="unknown", score=0.1, face_count=1)]),
        monkeypatch,
        instance_path=tmp_path,
    )

    await silenced._send_startup_greeting_prompt()

    (silenced_item,) = silenced.connection.created_items
    assert silenced_item["content"][0]["text"] == WAKE_GREETING
    assert silenced._wake_face_task is None
