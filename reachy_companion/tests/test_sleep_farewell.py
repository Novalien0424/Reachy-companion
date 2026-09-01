"""The farewell response cycle: the goodbye gets its own response, and we wait for it.

Spec §1 (2026-09-01 rev 2) and Codex round 1's critical catch: `_safe_response_create`
enqueues and returns, so a bare `wait_for_reply_finished()` can resolve on whatever
response was already running when the tool call landed. The sender also cannot
trust the old `_response_started_or_rejected_event`: unrelated realtime errors set
it too. These tests pin request-scoped start correlation plus response-id completion.
"""

from __future__ import annotations
import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from reachy_companion.huggingface_realtime import HuggingFaceRealtimeHandler


class _RecordingConnection:
    """Capture `response.create` payloads and stand in for the receive loop.

    `response.created` and `response.done` are the two edges the sender loop
    synchronizes on. `_finish` runs through `call_soon` rather than inline so the
    sender observes `_active_response_id` while it is still set - which is exactly
    the window the real receive loop leaves open.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.handler: HuggingFaceRealtimeHandler | None = None
        self.response = SimpleNamespace(create=self._create)

    async def _create(self, **kwargs: Any) -> None:
        assert self.handler is not None
        self.calls.append(kwargs)
        response_id = "resp_farewell" if kwargs.get("response") else f"resp_{len(self.calls)}"
        self._start_response(response_id)

    def _start_response(self, response_id: str) -> None:
        assert self.handler is not None
        self.handler._active_response_id = response_id
        self.handler._response_done_event.clear()
        self.handler._resolve_response_start(response_id)
        asyncio.get_running_loop().call_soon(self._finish, response_id)

    def _finish(self, response_id: str) -> None:
        assert self.handler is not None
        self.handler._active_response_id = None
        self.handler._response_done_event.set()
        self.handler._resolve_response_done(response_id)


class _UnrelatedErrorBeforeCreatedConnection(_RecordingConnection):
    """Simulate the live receive-loop hazard: an unrelated error before created."""

    async def _create(self, **kwargs: Any) -> None:
        assert self.handler is not None
        self.calls.append(kwargs)
        # This is what the current generic error branch does. The new sender must
        # ignore it for the waited-on response cycle and keep waiting for created.
        self.handler._response_started_or_rejected_event.set()
        self.handler._resolve_response_rejection("evt_unrelated")
        asyncio.get_running_loop().call_later(0.05, self._start_response, "resp_farewell")


class _ConnectionClosesAfterCreatedConnection(_RecordingConnection):
    """Simulate a websocket dying after created but before done."""

    async def _create(self, **kwargs: Any) -> None:
        assert self.handler is not None
        self.calls.append(kwargs)
        response_id = "resp_farewell" if kwargs.get("response") else f"resp_{len(self.calls)}"
        self.handler._active_response_id = response_id
        self.handler._response_done_event.clear()
        self.handler._resolve_response_start(response_id)
        asyncio.get_running_loop().call_soon(self._close)

    def _close(self) -> None:
        assert self.handler is not None
        self.handler.connection = None
        self.handler._active_response_id = None
        self.handler._response_done_event.set()
        self.handler._resolve_response_disconnect()


def _sender_handler() -> tuple[HuggingFaceRealtimeHandler, _RecordingConnection]:
    """Return a handler with only the response-sender state the loop actually reads."""
    handler = HuggingFaceRealtimeHandler.__new__(HuggingFaceRealtimeHandler)
    handler.deps = MagicMock()
    handler._pending_responses = asyncio.Queue()
    handler._response_done_event = asyncio.Event()
    handler._response_done_event.set()
    handler._response_started_or_rejected_event = asyncio.Event()
    handler._last_response_rejected = False
    handler._response_start_waiter = None
    handler._response_cycles_by_id = {}
    handler._active_response_id = None
    connection = _RecordingConnection()
    connection.handler = handler
    handler.connection = connection
    return handler, connection


@pytest.mark.asyncio
async def test_the_farewell_rides_the_serialized_sender_with_tool_choice_none() -> None:
    """One response.create, nested SDK shape, and the helper returns ITS id."""
    handler, connection = _sender_handler()
    sender = asyncio.create_task(handler._response_sender_loop())
    try:
        response_id = await asyncio.wait_for(handler.run_farewell_response_cycle(), timeout=2.0)
    finally:
        handler.connection = None
        sender.cancel()

    assert len(connection.calls) == 1
    assert connection.calls[0]["response"] == {"tool_choice": "none"}
    assert connection.calls[0]["event_id"].startswith("response_create_")
    assert response_id == "resp_farewell"


@pytest.mark.asyncio
async def test_an_empty_follow_up_never_swallows_the_farewell() -> None:
    """The coalescer may merge empty duplicates; it may never drop a waited-on cycle.

    Before this task the loop discarded EVERY following request whenever the one in
    hand was empty - so a generic tool follow-up queued first would have thrown the
    goodbye away and hung its waiter until the timeout.
    """
    handler, connection = _sender_handler()
    await handler._safe_response_create()  # a generic tool follow-up, queued first
    cycle_task = asyncio.create_task(handler.run_farewell_response_cycle())
    sender = asyncio.create_task(handler._response_sender_loop())
    try:
        response_id = await asyncio.wait_for(cycle_task, timeout=2.0)
    finally:
        handler.connection = None
        sender.cancel()

    assert len(connection.calls) == 1
    assert connection.calls[0]["response"] == {"tool_choice": "none"}
    assert response_id == "resp_farewell"


@pytest.mark.asyncio
async def test_unrelated_error_events_do_not_release_the_farewell_wait() -> None:
    """Only this request's created/rejection edge may resolve the start wait."""
    handler, _ = _sender_handler()
    connection = _UnrelatedErrorBeforeCreatedConnection()
    connection.handler = handler
    handler.connection = connection
    sender = asyncio.create_task(handler._response_sender_loop())
    try:
        response_id = await asyncio.wait_for(handler.run_farewell_response_cycle(), timeout=2.0)
    finally:
        handler.connection = None
        sender.cancel()

    assert response_id == "resp_farewell"


@pytest.mark.asyncio
async def test_started_farewell_cycle_resolves_when_the_connection_closes() -> None:
    """A cycle already mapped to a response id must not hang on a dead websocket."""
    handler, _ = _sender_handler()
    connection = _ConnectionClosesAfterCreatedConnection()
    connection.handler = handler
    handler.connection = connection
    sender = asyncio.create_task(handler._response_sender_loop())
    try:
        response_id = await asyncio.wait_for(handler.run_farewell_response_cycle(), timeout=1.0)
    finally:
        handler.connection = None
        sender.cancel()

    assert len(connection.calls) == 1
    assert response_id is None


@pytest.mark.asyncio
async def test_queued_farewell_cycle_resolves_when_connection_closes_before_send() -> None:
    """A cycle still waiting in the sender queue belongs to the dead session too."""
    handler, _ = _sender_handler()
    cycle_task = asyncio.create_task(handler.run_farewell_response_cycle())
    await asyncio.sleep(0)

    handler.connection = None
    handler._resolve_response_disconnect()

    assert await asyncio.wait_for(cycle_task, timeout=1.0) is None
    assert handler._pending_responses.qsize() == 0


@pytest.mark.asyncio
async def test_duplicate_empty_requests_still_coalesce_to_one() -> None:
    """The behaviour parallel tool calls rely on is unchanged."""
    handler, connection = _sender_handler()
    await handler._safe_response_create()
    await handler._safe_response_create()
    sender = asyncio.create_task(handler._response_sender_loop())
    await asyncio.sleep(0.05)
    handler.connection = None
    sender.cancel()

    assert len(connection.calls) == 1
    assert set(connection.calls[0]) == {"event_id"}
    assert connection.calls[0]["event_id"].startswith("response_create_")


@pytest.mark.asyncio
async def test_the_farewell_cycle_gives_up_at_once_without_a_session() -> None:
    """A dead websocket drains nothing, so waiting on it is ten seconds of nothing."""
    handler, _ = _sender_handler()
    handler.connection = None

    assert await asyncio.wait_for(handler.run_farewell_response_cycle(), timeout=1.0) is None
