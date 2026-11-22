"""One journal line times every utterance (2026-11-22 plan, N1).

The server's speech events are logged at DEBUG, so the INFO journal of
2026-11-21 could not say whether a call over Reachy's voice had been heard
late or not at all. Each transcript now writes one `utterance:` line, and the
robot's own detector reports a speech start over a reply that the server never
saw.
"""

import asyncio
import logging
from typing import Any

import pytest

# `tests/` has no __init__.py, so pytest's prepend import mode puts the
# directory itself on sys.path — import the sibling harness by bare name.
from test_turn_holdoff import _failed, _handler, _accepted, _speech_started, _speech_stopped
from test_huggingface_realtime import _FakeEvent

import reachy_companion.huggingface_realtime as hf_mod
from reachy_companion.conversation_mode import ConversationMode


_LOGGER = "reachy_companion.huggingface_realtime"


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shipped gates, a short hold-off."""
    for name in ("REALTIME_DEFAULT_MODE", "REALTIME_PARTY_BARGE", "REALTIME_SOLO_NAME_GATE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("REALTIME_COMMIT_HOLDOFF_MS", "20")


def _delta(item_id: str, text: str) -> _FakeEvent:
    return _FakeEvent("conversation.item.input_audio_transcription.delta", item_id=item_id, delta=text)


def _trace_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("utterance: ")]


@pytest.mark.asyncio
async def test_a_transcript_writes_one_timing_line(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Onset, first text, stop and transcript are all on the line, and the record is dropped."""
    handler = _handler(
        monkeypatch,
        (
            (0.0, _speech_started("item_1")),
            (0.03, _delta("item_1", "瑞")),
            (0.0, _delta("item_1", "奇")),
            (0.03, _speech_stopped("item_1")),
            (0.02, _accepted("item_1", "瑞奇，你好")),
        ),
        tail_delay_s=0.05,
    )
    handler._conversation_mode = ConversationMode.GROUP
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        await handler._run_realtime_session()

    (line,) = _trace_lines(caplog)
    assert "Reachy audible at onset=False now=False" in line
    assert line.endswith("5 chars")
    fields = dict(part.strip().split(" ", 1) for part in line.removeprefix("utterance: ").split(",")[:3])
    speech_ms = int(fields["speech"].split()[0])
    first_text_ms = int(fields["first"].split()[1])
    # The first text arrived before the speaker stopped: the name was readable mid-utterance.
    assert 0 < first_text_ms < speech_ms
    assert handler._utterance_traces == {}


@pytest.mark.asyncio
async def test_an_onset_over_a_reply_is_marked(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The line says Reachy was audible when the caller started."""
    handler = _handler(
        monkeypatch,
        ((0.0, _speech_started("item_1")), (0.01, _speech_stopped("item_1")), (0.0, _accepted("item_1", "隨便聊聊天氣"))),
        tail_delay_s=0.05,
    )
    handler._conversation_mode = ConversationMode.GROUP
    monkeypatch.setattr(handler, "_robot_audible", lambda: True)
    monkeypatch.setattr(hf_mod, "on_turn_without_response", lambda _deps: None)
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        await handler._run_realtime_session()

    (line,) = _trace_lines(caplog)
    assert "first text n/a ms" in line
    assert "Reachy audible at onset=True now=True" in line


@pytest.mark.asyncio
async def test_a_failed_transcription_still_writes_the_line(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A turn that was heard and lost is evidence too."""
    handler = _handler(
        monkeypatch,
        ((0.0, _speech_started("item_1")), (0.01, _speech_stopped("item_1")), (0.0, _failed("item_1"))),
        tail_delay_s=0.02,
    )
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        await handler._run_realtime_session()

    (line,) = _trace_lines(caplog)
    assert line.endswith("transcription failed")
    assert handler._utterance_traces == {}


def _unheard(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("Local VAD heard speech while")]


@pytest.mark.asyncio
async def test_local_speech_over_a_reply_that_the_server_missed_is_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The robot's own detector is the second witness."""
    monkeypatch.setattr(hf_mod, "_LOCAL_VAD_UNHEARD_WAIT_S", 0.01)
    handler = _handler(monkeypatch, ())
    monkeypatch.setattr(handler, "_robot_audible", lambda: True)
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        handler.on_local_speech(True)
        await asyncio.sleep(0.05)
    assert len(_unheard(caplog)) == 1
    # The pose is still left alone while Reachy talks.
    handler.deps.movement_manager.set_listening.assert_not_called()


@pytest.mark.asyncio
async def test_local_speech_the_server_also_heard_is_not_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A server `speech_started` around the local one means the path works."""
    monkeypatch.setattr(hf_mod, "_LOCAL_VAD_UNHEARD_WAIT_S", 0.01)
    handler = _handler(monkeypatch, ())
    monkeypatch.setattr(handler, "_robot_audible", lambda: True)
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        handler.on_local_speech(True)
        handler._stamp_speech_started_at("item_1")
        await asyncio.sleep(0.05)
    assert _unheard(caplog) == []


@pytest.mark.asyncio
async def test_the_miss_line_is_capped_per_session(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A detector that fires on Reachy's own voice cannot flood the journal."""
    monkeypatch.setattr(hf_mod, "_LOCAL_VAD_UNHEARD_WAIT_S", 0.0)
    monkeypatch.setattr(hf_mod, "_LOCAL_VAD_UNHEARD_LOG_MAX", 2)
    handler = _handler(monkeypatch, ())
    monkeypatch.setattr(handler, "_robot_audible", lambda: True)
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        for _ in range(5):
            handler.on_local_speech(True)
        await asyncio.sleep(0.03)
    lines = _unheard(caplog)
    assert len(lines) == 2 and lines[-1].endswith("(last one logged this session)")


def test_trace_helpers_ignore_events_without_an_item(monkeypatch: pytest.MonkeyPatch) -> None:
    """An event with no item id leaves no record behind and logs nothing."""
    handler: Any = _handler(monkeypatch, ())
    handler._trace_utterance_started(None, 1.0)
    handler._trace_utterance_stopped(None, 2.0)
    handler._trace_utterance_partial(None)
    handler._log_utterance_trace(None, "你好")
    assert handler._utterance_traces == {}
