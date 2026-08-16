"""Session-config unit tests — no network, no robot."""

import os
import base64
import asyncio
from time import monotonic
from unittest.mock import MagicMock

import numpy as np
import pytest


os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-real")

from reachy_companion.config import (  # noqa: E402
    get_default_voice,
    get_available_voices,
    _normalize_transcription_language,
)
from reachy_companion.streaming import AdditionalOutputs  # noqa: E402
from reachy_companion.openai_realtime import MODEL, ROBOT_RATE, OpenAIRealtimeHandler  # noqa: E402


@pytest.fixture()
def handler() -> OpenAIRealtimeHandler:
    """Return a handler with the heavy __init__ skipped."""
    h = OpenAIRealtimeHandler.__new__(OpenAIRealtimeHandler)  # skip heavy __init__
    h.get_current_voice = MagicMock(return_value="cedar")  # type: ignore[method-assign]
    h.instance_path = None  # _get_session_config reads it (huggingface_realtime.py:226)
    return h


def test_sample_rate_is_24k() -> None:
    """The model side runs at 24 kHz, unlike the 16 kHz Hugging Face backend."""
    assert OpenAIRealtimeHandler.SAMPLE_RATE == 24000


def test_session_config_targets_gpt_realtime_21(handler: OpenAIRealtimeHandler) -> None:
    """The session must name gpt-realtime-2.1 and use 24 kHz PCM plus zh transcription."""
    cfg = handler._get_session_config(tool_specs=[])
    assert cfg["model"] == "gpt-realtime-2.1"
    assert cfg["audio"]["output"]["format"]["rate"] == 24000
    assert cfg["audio"]["input"]["format"]["rate"] == 24000
    assert cfg["audio"]["input"]["transcription"]["language"] == "zh"


def test_vad_tuning_from_env(monkeypatch: pytest.MonkeyPatch, handler: OpenAIRealtimeHandler) -> None:
    """Server VAD silence duration is tunable for Chinese mid-sentence pauses (D-003)."""
    monkeypatch.setenv("REALTIME_VAD_SILENCE_DURATION_MS", "800")
    cfg = handler._get_session_config(tool_specs=[])
    td = cfg["audio"]["input"]["turn_detection"]
    assert td["type"] == "server_vad"
    assert td["silence_duration_ms"] == 800
    assert td["interrupt_response"] is True


def test_semantic_vad_from_env(monkeypatch: pytest.MonkeyPatch, handler: OpenAIRealtimeHandler) -> None:
    """REALTIME_VAD_TYPE=semantic_vad switches turn detection to the semantic detector."""
    monkeypatch.setenv("REALTIME_VAD_TYPE", "semantic_vad")
    monkeypatch.setenv("REALTIME_VAD_EAGERNESS", "low")
    cfg = handler._get_session_config(tool_specs=[])
    td = cfg["audio"]["input"]["turn_detection"]
    assert td["type"] == "semantic_vad"
    assert td["eagerness"] == "low"
    assert td["interrupt_response"] is True


def test_invalid_eagerness_falls_back_to_auto(monkeypatch: pytest.MonkeyPatch, handler: OpenAIRealtimeHandler) -> None:
    """An unsupported eagerness value must not be forwarded to the API."""
    monkeypatch.setenv("REALTIME_VAD_TYPE", "semantic_vad")
    monkeypatch.setenv("REALTIME_VAD_EAGERNESS", "nonsense")
    cfg = handler._get_session_config(tool_specs=[])
    assert cfg["audio"]["input"]["turn_detection"]["eagerness"] == "auto"


def test_session_config_keeps_selected_voice(handler: OpenAIRealtimeHandler) -> None:
    """The inherited voice selection must survive the override."""
    cfg = handler._get_session_config(tool_specs=[])
    assert cfg["audio"]["output"]["voice"] == "cedar"


@pytest.mark.asyncio
async def test_build_client_puts_model_in_the_connect_query(handler: OpenAIRealtimeHandler) -> None:
    """The model id must reach `realtime.connect` via the base's connect_kwargs seam."""
    client = await handler._build_realtime_client()
    assert client.api_key == os.environ["OPENAI_API_KEY"]
    assert handler._realtime_connect_query == {"model": MODEL}


@pytest.mark.asyncio
async def test_build_client_requires_an_api_key(
    monkeypatch: pytest.MonkeyPatch, handler: OpenAIRealtimeHandler
) -> None:
    """A missing OPENAI_API_KEY must fail fast rather than connect anonymously."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(KeyError):
        await handler._build_realtime_client()


class _FakeInputAudioBuffer:
    """Capture base64 audio appended to the realtime input buffer."""

    def __init__(self) -> None:
        """Start with no captured frames."""
        self.appended: list[str] = []

    async def append(self, *, audio: str) -> None:
        """Record one appended frame."""
        self.appended.append(audio)


class _FakeConnection:
    """Minimal stand-in for AsyncRealtimeConnection."""

    def __init__(self) -> None:
        """Create the fake input audio buffer."""
        self.input_audio_buffer = _FakeInputAudioBuffer()


@pytest.mark.asyncio
async def test_receive_resamples_microphone_audio_to_24k(handler: OpenAIRealtimeHandler) -> None:
    """Mic frames arrive at the robot rate and must be upsampled to the model rate."""
    connection = _FakeConnection()
    handler.connection = connection  # type: ignore[assignment]

    frame = (np.arange(320, dtype=np.float32) / 320.0).astype(np.float32)
    await handler.receive((ROBOT_RATE, (frame * 32767).astype(np.int16)))

    assert len(connection.input_audio_buffer.appended) == 1
    decoded = np.frombuffer(base64.b64decode(connection.input_audio_buffer.appended[0]), dtype=np.int16)
    assert decoded.size == 320 * 24000 // ROBOT_RATE


@pytest.mark.asyncio
async def test_receive_downmixes_stereo_before_resampling(handler: OpenAIRealtimeHandler) -> None:
    """Stereo mic frames must be reduced to mono, then resampled."""
    connection = _FakeConnection()
    handler.connection = connection  # type: ignore[assignment]

    stereo = np.zeros((2, 320), dtype=np.int16)
    await handler.receive((ROBOT_RATE, stereo))

    decoded = np.frombuffer(base64.b64decode(connection.input_audio_buffer.appended[0]), dtype=np.int16)
    assert decoded.size == 480


@pytest.mark.asyncio
async def test_receive_skips_resampling_when_already_at_model_rate(handler: OpenAIRealtimeHandler) -> None:
    """A mic already at 24 kHz must be passed through untouched."""
    connection = _FakeConnection()
    handler.connection = connection  # type: ignore[assignment]

    await handler.receive((24000, np.zeros(480, dtype=np.int16)))

    decoded = np.frombuffer(base64.b64decode(connection.input_audio_buffer.appended[0]), dtype=np.int16)
    assert decoded.size == 480


def _emit_ready_handler() -> OpenAIRealtimeHandler:
    """Return a handler wired just enough for ConversationHandler.emit()."""
    h = OpenAIRealtimeHandler.__new__(OpenAIRealtimeHandler)
    h.output_queue = asyncio.Queue()
    h.deps = MagicMock()
    h.last_activity_time = monotonic()
    h.last_idle_behavior_time = h.last_activity_time
    return h


@pytest.mark.asyncio
async def test_emit_downsamples_model_audio_to_the_robot_rate() -> None:
    """console.py drops the rate label, so the handler must emit 16 kHz itself."""
    h = _emit_ready_handler()
    await h.output_queue.put((24000, np.zeros((1, 480), dtype=np.int16)))

    output = await h.emit()

    assert isinstance(output, tuple)
    rate, pcm = output
    assert rate == ROBOT_RATE
    assert pcm.shape == (1, 320)
    assert pcm.dtype == np.int16


@pytest.mark.asyncio
async def test_emit_passes_text_outputs_through_unchanged() -> None:
    """Non-audio queue items must not be touched."""
    h = _emit_ready_handler()
    payload = AdditionalOutputs({"role": "assistant", "content": "hi"})
    await h.output_queue.put(payload)

    assert await h.emit() is payload


@pytest.mark.asyncio
async def test_emit_leaves_robot_rate_audio_alone() -> None:
    """Audio already at the robot rate must be forwarded without a copy."""
    h = _emit_ready_handler()
    pcm = np.zeros((1, 320), dtype=np.int16)
    await h.output_queue.put((ROBOT_RATE, pcm))

    output = await h.emit()

    assert isinstance(output, tuple)
    assert output[1] is pcm


@pytest.mark.asyncio
async def test_emit_returns_none_when_the_queue_is_empty() -> None:
    """The base emit() timeout behavior must be preserved."""
    h = _emit_ready_handler()
    assert await h.emit() is None


def test_openai_voices_are_configured() -> None:
    """The voice catalog must be the OpenAI realtime one, defaulting to cedar."""
    assert get_default_voice() == "cedar"
    assert get_available_voices() == [
        "alloy",
        "ash",
        "ballad",
        "cedar",
        "coral",
        "echo",
        "marin",
        "sage",
        "shimmer",
        "verse",
    ]


def test_transcription_language_defaults_to_zh() -> None:
    """The POC ships Chinese-first transcription (D-003)."""
    assert _normalize_transcription_language(None) == "zh"
    assert _normalize_transcription_language("  ") == "zh"
    assert _normalize_transcription_language("en") == "en"
