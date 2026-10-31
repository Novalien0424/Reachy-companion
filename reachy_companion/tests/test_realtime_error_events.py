"""Server `error` events and the operator transcript (2026-10-31 plan, W5.2).

`response_cancel_not_active` is our own `response.cancel` racing the reply's
natural end — benign, and it used to reach the transcript as an `[error]` line.
"""

import logging
from types import SimpleNamespace

import pytest

# `tests/` has no __init__.py, so pytest's prepend import mode puts the
# directory itself on sys.path — import the sibling harnesses by bare name.
from test_solo_barge import _loop_handler, _quiet_session
from test_huggingface_realtime import _FakeEvent, _drain_queue, _queued_messages


def _error(code: str, message: str) -> _FakeEvent:
    """Build a server `error` event shaped like the SDK's."""
    return _FakeEvent(
        "error",
        error=SimpleNamespace(code=code, type="invalid_request_error", message=message, event_id=None),
    )


@pytest.mark.asyncio
async def test_a_cancel_that_found_no_active_response_stays_off_the_transcript(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Logged at debug only; nothing on the output queue, nothing at ERROR."""
    _quiet_session(monkeypatch)
    handler = _loop_handler((_error("response_cancel_not_active", "Cancellation failed: no active response found"),))
    with caplog.at_level(logging.DEBUG, logger="reachy_companion.huggingface_realtime"):
        await handler._run_realtime_session()
    assert not any("[error]" in str(m.get("content")) for m in _queued_messages(_drain_queue(handler)))
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert "response.cancel found no active response (benign race)" in caplog.text


@pytest.mark.asyncio
async def test_other_server_errors_still_reach_the_transcript(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Only the benign race is quieted; a real error is still shown and logged."""
    _quiet_session(monkeypatch)
    handler = _loop_handler((_error("invalid_value", "Invalid value for audio_end_ms"),))
    with caplog.at_level(logging.ERROR):
        await handler._run_realtime_session()
    messages = _queued_messages(_drain_queue(handler))
    assert {"role": "assistant", "content": "[error] Invalid value for audio_end_ms"} in messages
    assert "Realtime error [invalid_value]" in caplog.text
