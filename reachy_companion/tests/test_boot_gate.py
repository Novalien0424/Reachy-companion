"""Boot gate: no committable turn until the greeting has left the speaker (Task 6).

The robot wakes, the greeting starts playing, and the microphone hears it — the
first "user turn" of a session was routinely the robot's own voice or its echo.
The gate closes that window: the *first* session of a handler comes up with
turn detection OFF, and VAD is only pushed back once the greeting has finished
coming out of the speaker (`audio_drain.is_audible()` says so), or once the
backstop timer fires.

The state machine these tests pin down:

* first session      -> `turn_detection: None` in the very first `session.update`
* `response.done`    -> wait for the audio to drain, THEN release (never at the
                        instant `response.done` arrives — the greeting is still
                        audible then, which is the exact failure being fixed)
* release            -> `input_audio_buffer.clear` BEFORE the VAD update, so the
                        greeting's own audio never becomes a committed turn
* stale timer        -> a timer bound to a dead connection cannot release the
                        gate of the session that replaced it
* reconnect / env-off -> not gated at all; normal VAD from the first update
"""

import os
import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-real")

from test_huggingface_realtime import _FakeEvent  # noqa: E402

import reachy_companion.huggingface_realtime as hf_mod  # noqa: E402
from reachy_companion.hanova import audio_drain  # noqa: E402
from reachy_companion.openai_realtime import OpenAIRealtimeHandler  # noqa: E402
from reachy_companion.tools.core_tools import ToolDependencies  # noqa: E402
from reachy_companion.conversation_mode import ConversationMode  # noqa: E402


_STOP = object()


class _FakeConnection:
    """A realtime connection that logs what the boot gate does to it.

    Adapted from `_make_fake_realtime_client` (test_huggingface_realtime.py:29-95)
    with two additions the gate needs:

    * an **ordered** call log, so a test can assert the input buffer was cleared
      *before* turn detection came back rather than merely that both happened;
    * a pushed event stream instead of a fixed tuple, so the session stays alive
      while the test drives the drain state and inspects the release. A scripted
      iterator ends the session immediately, and the session's `finally` cancels
      the boot-gate task — the release would never be observable.
    """

    def __init__(self) -> None:
        """Create an idle connection with an empty log and no queued events."""
        self.log: list[tuple[str, Any]] = []
        self.events: asyncio.Queue[Any] = asyncio.Queue()
        self.session = SimpleNamespace(update=self._session_update)
        self.input_audio_buffer = SimpleNamespace(append=self._noop, clear=self._clear)
        self.conversation = SimpleNamespace(item=SimpleNamespace(create=self._noop))
        self.response = SimpleNamespace(create=self._noop, cancel=self._noop)

    # --- recorded calls ---------------------------------------------------
    async def _session_update(self, **kwargs: Any) -> None:
        self.log.append(("session.update", kwargs.get("session")))
        # The server answers every accepted `session.update` with a
        # `session.updated` on the event stream, and since the 2026-08-31 modes
        # work the ordered update mechanism WAITS for it. A fake that stayed
        # silent made every live update sit out its whole acknowledgement
        # timeout.
        self.events.put_nowait(_FakeEvent("session.updated"))

    async def _clear(self, **_kwargs: Any) -> None:
        self.log.append(("input_audio_buffer.clear", None))

    async def _noop(self, **_kwargs: Any) -> None:
        pass

    # --- test-side driving -------------------------------------------------
    @property
    def updates(self) -> list[Any]:
        """Every `session.update` payload, in order."""
        return [payload for kind, payload in self.log if kind == "session.update"]

    @property
    def kinds(self) -> list[str]:
        """Every recorded call name, in order."""
        return [kind for kind, _ in self.log]

    def push(self, event: Any) -> None:
        """Hand one realtime event to the running session."""
        self.events.put_nowait(event)

    def finish(self) -> None:
        """End the event stream so the session can return."""
        self.events.put_nowait(_STOP)

    # --- connection protocol ----------------------------------------------
    async def __aenter__(self) -> "_FakeConnection":
        return self

    async def __aexit__(self, *_args: Any) -> bool:
        return False

    def __aiter__(self) -> "_FakeConnection":
        return self

    async def __anext__(self) -> Any:
        event = await self.events.get()
        if event is _STOP:
            raise StopAsyncIteration
        return event


def _make_client(conn: _FakeConnection) -> Any:
    """Return an AsyncOpenAI-shaped client whose realtime session is `conn`."""

    class FakeRealtime:
        def connect(self, **_kwargs: Any) -> _FakeConnection:
            return conn

    class FakeClient:
        realtime = FakeRealtime()

    return FakeClient()


def _make_handler(
    monkeypatch: pytest.MonkeyPatch,
    conn: _FakeConnection,
    *,
    greeting: str = "打個招呼",
) -> OpenAIRealtimeHandler:
    """Build an OpenAI handler wired to `conn`, with prompts and tools stubbed."""
    monkeypatch.setattr(hf_mod, "get_session_instructions", lambda _instance_path=None: "test")
    monkeypatch.setattr(hf_mod, "get_session_voice", lambda default="cedar": default)
    monkeypatch.setattr(hf_mod, "get_tool_specs", lambda: [])
    monkeypatch.setattr(hf_mod, "get_session_greeting_prompt", lambda: greeting)
    handler = OpenAIRealtimeHandler(ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock()))
    handler.get_current_voice = MagicMock(return_value="cedar")  # type: ignore[method-assign]
    handler.client = _make_client(conn)
    monkeypatch.setattr(type(handler.tool_manager), "start_up", MagicMock())
    monkeypatch.setattr(type(handler.tool_manager), "shutdown", AsyncMock())
    return handler


async def _wait_until(predicate: Any, message: str, timeout_s: float = 3.0) -> None:
    """Poll `predicate` until it is true, or fail the test with `message`."""
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(message)


async def _start_session(handler: OpenAIRealtimeHandler, conn: _FakeConnection) -> "asyncio.Task[None]":
    """Run one realtime session in the background, up and connected."""
    task = asyncio.create_task(handler._run_realtime_session())
    await _wait_until(lambda: handler.connection is conn, "the session never connected")
    return task


async def _end_session(task: "asyncio.Task[None]", conn: _FakeConnection) -> None:
    """Close the event stream and let the session unwind."""
    conn.finish()
    await asyncio.wait_for(task, timeout=3.0)


def _make_audible() -> int:
    """Put a second of assistant audio in the queue (test_party_mode.py:173-176)."""
    generation = audio_drain.begin_response()
    audio_drain.note_enqueued(generation, sample_count=24000, sample_rate=24000)
    return generation


@pytest.fixture(autouse=True)
def _clean_boot_gate_env(monkeypatch: pytest.MonkeyPatch):
    """No developer's exported knobs may decide what the gate does here."""
    for name in (
        "REALTIME_BOOT_GATE",
        "REALTIME_BOOT_GATE_TIMEOUT_S",
        "REALTIME_VAD_TYPE",
        "REALTIME_DEFAULT_MODE",
        "REALTIME_PARTY_DEFAULT",
        "FACE_AUTO_GREET",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("FACE_AUTO_GREET", "0")
    audio_drain.reset()
    yield
    audio_drain.reset()


def _turn_detection_of(update: Any) -> Any:
    """Pull `audio.input.turn_detection` out of a captured session.update payload."""
    return update["audio"]["input"]["turn_detection"]


# --------------------------------------------------------------------------
# 1. The gated session config
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_session_config_has_no_turn_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    """The first session of a handler comes up with server VAD switched off."""
    conn = _FakeConnection()
    handler = _make_handler(monkeypatch, conn)
    assert handler._boot_gate_active is True

    task = await _start_session(handler, conn)
    try:
        assert len(conn.updates) == 1
        assert _turn_detection_of(conn.updates[0]) is None
    finally:
        await _end_session(task, conn)


# --------------------------------------------------------------------------
# 2. Release: only once the greeting has actually stopped being audible
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_response_done_releases_the_gate_after_drain(monkeypatch: pytest.MonkeyPatch) -> None:
    """`response.done` starts the drain wait; only silence releases the gate."""
    conn = _FakeConnection()
    handler = _make_handler(monkeypatch, conn)
    task = await _start_session(handler, conn)
    try:
        _make_audible()
        conn.push(_FakeEvent("response.done", response=SimpleNamespace(id="r1")))
        await _wait_until(lambda: conn.events.empty(), "response.done was never consumed")
        await asyncio.sleep(0.3)

        assert handler._boot_gate_active is True, "the greeting is still coming out of the speaker"
        assert len(conn.updates) == 1, "VAD must not come back while the greeting is audible"

        audio_drain.note_cleared()
        await _wait_until(lambda: len(conn.updates) == 2, "the gate never released after the audio drained")

        assert handler._boot_gate_active is False
        assert _turn_detection_of(conn.updates[1])["type"] == "server_vad"
        # The buffer that collected the greeting's own echo is dropped BEFORE
        # VAD is allowed to commit anything from it (Codex R3-1).
        assert conn.kinds == ["session.update", "input_audio_buffer.clear", "session.update"]
    finally:
        await _end_session(task, conn)


# --------------------------------------------------------------------------
# 2b. A timer from a dead session cannot touch its successor
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stale_timer_cannot_release_a_new_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """A backstop bound to a replaced connection is a no-op (Codex R1-4)."""
    conn = _FakeConnection()
    handler = _make_handler(monkeypatch, conn)
    handler.connection = conn  # the live session
    dead_conn = _FakeConnection()  # the one the timer was born with

    await handler._finish_boot_gate("timeout", dead_conn)

    assert handler._boot_gate_active is True
    assert conn.log == [], "a stale timer must not touch the live connection"


# --------------------------------------------------------------------------
# 3-5. The un-gated paths
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconnect_is_not_gated(monkeypatch: pytest.MonkeyPatch) -> None:
    """A reconnect mid-conversation keeps normal VAD: the greeting already played."""
    conn = _FakeConnection()
    handler = _make_handler(monkeypatch, conn)
    handler._startup_greeting_sent = True

    task = await _start_session(handler, conn)
    try:
        assert handler._boot_gate_active is False
        assert len(conn.updates) == 1
        assert _turn_detection_of(conn.updates[0])["type"] == "server_vad"
    finally:
        await _end_session(task, conn)


@pytest.mark.asyncio
async def test_boot_gate_env_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """REALTIME_BOOT_GATE=0 restores the pre-gate startup exactly."""
    monkeypatch.setenv("REALTIME_BOOT_GATE", "0")
    conn = _FakeConnection()
    handler = _make_handler(monkeypatch, conn)
    assert handler._boot_gate_active is False

    task = await _start_session(handler, conn)
    try:
        assert _turn_detection_of(conn.updates[0])["type"] == "server_vad"
        assert handler._boot_gate_task is None
    finally:
        await _end_session(task, conn)


@pytest.mark.asyncio
async def test_boot_gate_timeout_releases(monkeypatch: pytest.MonkeyPatch) -> None:
    """The backstop releases the gate even when no response ever completes."""
    monkeypatch.setenv("REALTIME_BOOT_GATE_TIMEOUT_S", "0")
    conn = _FakeConnection()
    handler = _make_handler(monkeypatch, conn)

    task = await _start_session(handler, conn)
    try:
        await _wait_until(lambda: len(conn.updates) == 2, "the backstop timer never released the gate")
        assert handler._boot_gate_active is False
        assert _turn_detection_of(conn.updates[1])["type"] == "server_vad"
        assert conn.kinds == ["session.update", "input_audio_buffer.clear", "session.update"]
    finally:
        await _end_session(task, conn)


@pytest.mark.asyncio
async def test_turn_detection_push_is_deferred_while_gated(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing but the gate itself may hand turn detection back while it is closed.

    `set_conversation_mode` pushes turn detection to the live session, and by the
    time the gate is holding the greeting `_startup_greeting_sent` is already True —
    so the config builder alone would emit normal VAD. The push waits for the
    release instead, and the mode it wanted lands there.
    """
    conn = _FakeConnection()
    handler = _make_handler(monkeypatch, conn)
    task = await _start_session(handler, conn)
    try:
        await _wait_until(lambda: handler._startup_greeting_sent, "the greeting was never queued")
        handler._conversation_mode = ConversationMode.GROUP

        await handler._push_turn_detection_update()
        assert len(conn.updates) == 1, "the boot gate owns turn detection until it releases"

        await handler._finish_boot_gate("test")
        assert len(conn.updates) == 2
        assert _turn_detection_of(conn.updates[1])["create_response"] is False, "party config still applied"
    finally:
        await _end_session(task, conn)


@pytest.mark.asyncio
async def test_empty_greeting_releases_the_gate_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no greeting configured no response will ever come; do not gate forever."""
    conn = _FakeConnection()
    handler = _make_handler(monkeypatch, conn, greeting="   ")

    task = await _start_session(handler, conn)
    try:
        await _wait_until(lambda: len(conn.updates) == 2, "an empty greeting left the gate closed")
        assert handler._boot_gate_active is False
        assert handler._boot_gate_task is None, "no backstop is armed once the gate is already open"
        assert _turn_detection_of(conn.updates[1])["type"] == "server_vad"
    finally:
        await _end_session(task, conn)
