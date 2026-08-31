"""Session-config and audio-boundary unit tests — no network, no robot."""

import os
import base64
import asyncio
import logging
from time import monotonic
from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pytest
from scipy.signal import resample_poly


os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-real")

from test_huggingface_realtime import _make_fake_realtime_client  # noqa: E402

from reachy_companion.config import (  # noqa: E402
    get_default_voice,
    get_available_voices,
    _normalize_transcription_language,
)
from reachy_companion.streaming import AdditionalOutputs  # noqa: E402
from reachy_companion.openai_realtime import (  # noqa: E402
    ROBOT_RATE,
    OpenAIRealtimeHandler,
    realtime_model,
    _StreamingResampler,
)
from reachy_companion.tools.core_tools import ToolDependencies  # noqa: E402


MODEL_RATE = 24000


@pytest.fixture(autouse=True)
def _voicefx_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never let a developer's exported VOICEFX_* knobs decide what emit() produces."""
    for name in ("VOICEFX_ENABLED", "VOICEFX_PITCH_SEMITONES", "VOICEFX_RINGMOD_HZ", "VOICEFX_RINGMOD_MIX"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture()
def handler() -> OpenAIRealtimeHandler:
    """Return a handler with the heavy __init__ skipped."""
    h = OpenAIRealtimeHandler.__new__(OpenAIRealtimeHandler)  # skip heavy __init__
    h.get_current_voice = MagicMock(return_value="cedar")  # type: ignore[method-assign]
    h.instance_path = None  # _get_session_config reads it (huggingface_realtime.py:226)
    return h


def _sine(rate: int, n: int, freq: float = 440.0, amp: float = 0.5) -> np.ndarray:
    """Return an `amp`-amplitude float32 sine of `n` samples at `rate`."""
    t = np.arange(n) / rate
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _to_int16(signal: np.ndarray) -> np.ndarray:
    """Convert a float32 signal in [-1, 1) to int16."""
    return (signal * 32767).astype(np.int16)


def _to_float(pcm: np.ndarray) -> np.ndarray:
    """Convert int16 PCM back to float32 in [-1, 1)."""
    return pcm.astype(np.float32) / 32767.0


def _lsb_diff(a: np.ndarray, b: np.ndarray) -> int:
    """Return the largest absolute difference between two int16 buffers, in LSB."""
    return int(np.abs(a.astype(np.int32) - b.astype(np.int32)).max())


# --------------------------------------------------------------------------
# Session config
# --------------------------------------------------------------------------


def test_sample_rate_is_24k() -> None:
    """The model side runs at 24 kHz, unlike the 16 kHz Hugging Face backend."""
    assert OpenAIRealtimeHandler.SAMPLE_RATE == 24000


def test_default_model_is_mini(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default realtime model must be gpt-realtime-2.1-mini."""
    monkeypatch.delenv("REALTIME_MODEL", raising=False)
    assert realtime_model() == "gpt-realtime-2.1-mini"


def test_model_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """REALTIME_MODEL environment variable overrides the default model."""
    monkeypatch.setenv("REALTIME_MODEL", "gpt-realtime-2.1")
    assert realtime_model() == "gpt-realtime-2.1"


def test_session_config_targets_configured_model(handler: OpenAIRealtimeHandler, monkeypatch: pytest.MonkeyPatch) -> None:
    """The session must use the configured model (default mini) and 24 kHz PCM plus zh transcription."""
    monkeypatch.delenv("REALTIME_MODEL", raising=False)
    cfg = handler._get_session_config(tool_specs=[])
    assert cfg["model"] == "gpt-realtime-2.1-mini"
    assert cfg["audio"]["output"]["format"]["rate"] == 24000
    assert cfg["audio"]["input"]["format"]["rate"] == 24000
    # Applied by the base from config.REALTIME_TRANSCRIPTION_LANGUAGE
    # (huggingface_realtime.py:234); asserted here as an integration check.
    assert cfg["audio"]["input"]["transcription"]["language"] == "zh"


def test_session_config_keeps_selected_voice(handler: OpenAIRealtimeHandler) -> None:
    """The inherited voice selection must survive the override."""
    cfg = handler._get_session_config(tool_specs=[])
    assert cfg["audio"]["output"]["voice"] == "cedar"


# --------------------------------------------------------------------------
# Transcription upgrade (Task 4): gpt-transcribe, keyword biasing, fallback
# --------------------------------------------------------------------------


def test_transcription_upgraded_with_keywords(handler: OpenAIRealtimeHandler, monkeypatch: pytest.MonkeyPatch) -> None:
    """The OpenAI subclass upgrades to gpt-transcribe with party-name keyword biasing."""
    monkeypatch.delenv("REALTIME_TRANSCRIPTION_MODEL", raising=False)
    monkeypatch.delenv("REALTIME_TRANSCRIPTION_KEYWORDS", raising=False)
    cfg = handler._get_session_config(tool_specs=[])
    tr = cfg["audio"]["input"]["transcription"]
    assert tr["model"] == "gpt-transcribe"
    assert tr["language"] == "zh"
    assert "瑞奇" in tr["keywords"] and "reachy" in tr["keywords"]
    assert tr["prompt"]


def test_transcription_keywords_env_override(handler: OpenAIRealtimeHandler, monkeypatch: pytest.MonkeyPatch) -> None:
    """REALTIME_TRANSCRIPTION_KEYWORDS overrides the default party-name keyword list."""
    monkeypatch.setenv("REALTIME_TRANSCRIPTION_KEYWORDS", "客廳, 冷氣")
    tr = handler._get_session_config(tool_specs=[])["audio"]["input"]["transcription"]
    assert tr["keywords"] == ["客廳", "冷氣"]


def test_transcription_model_env_override_drops_new_fields_for_legacy(
    handler: OpenAIRealtimeHandler, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Forcing a legacy model must not send keywords/prompt the legacy shape does not expect."""
    monkeypatch.setenv("REALTIME_TRANSCRIPTION_MODEL", "gpt-4o-transcribe")
    tr = handler._get_session_config(tool_specs=[])["audio"]["input"]["transcription"]
    assert tr["model"] == "gpt-4o-transcribe"
    assert "keywords" not in tr and "prompt" not in tr  # legacy model, legacy shape


def test_transcription_delay_is_absent_by_default(
    handler: OpenAIRealtimeHandler, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`delay` is opt-in: an unset env var must not change the shipped session shape."""
    monkeypatch.delenv("REALTIME_TRANSCRIPTION_DELAY", raising=False)
    tr = handler._get_session_config(tool_specs=[])["audio"]["input"]["transcription"]
    assert "delay" not in tr


@pytest.mark.parametrize("value", ["minimal", "low", "medium", "high", "xhigh"])
def test_transcription_delay_from_env(
    handler: OpenAIRealtimeHandler, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """REALTIME_TRANSCRIPTION_DELAY lands in the transcription params (Task 2)."""
    monkeypatch.setenv("REALTIME_TRANSCRIPTION_DELAY", f"  {value.upper()} ")
    tr = handler._get_session_config(tool_specs=[])["audio"]["input"]["transcription"]
    assert tr["delay"] == value


def test_invalid_transcription_delay_warns_and_is_dropped(
    handler: OpenAIRealtimeHandler, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A bad .env value must warn and degrade, never send a malformed session."""
    monkeypatch.setenv("REALTIME_TRANSCRIPTION_DELAY", "instant")

    with caplog.at_level(logging.WARNING, logger="reachy_companion.openai_realtime"):
        tr = handler._get_session_config(tool_specs=[])["audio"]["input"]["transcription"]

    assert "delay" not in tr
    assert "REALTIME_TRANSCRIPTION_DELAY" in caplog.text


def test_transcription_delay_is_not_sent_to_a_legacy_model(
    handler: OpenAIRealtimeHandler, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`delay` is a new-model extra, on the same guard as keywords/prompt."""
    monkeypatch.setenv("REALTIME_TRANSCRIPTION_MODEL", "gpt-4o-transcribe")
    monkeypatch.setenv("REALTIME_TRANSCRIPTION_DELAY", "low")
    tr = handler._get_session_config(tool_specs=[])["audio"]["input"]["transcription"]
    assert "delay" not in tr


@pytest.mark.asyncio
async def test_session_update_falls_back_to_legacy_transcription_on_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rejected upgraded session.update must retry once with the legacy transcription shape."""
    import reachy_companion.huggingface_realtime as hf_mod

    monkeypatch.setattr(hf_mod, "get_session_instructions", lambda _instance_path=None: "test")
    monkeypatch.setattr(hf_mod, "get_session_voice", lambda default="cedar": default)
    monkeypatch.setattr(hf_mod, "get_tool_specs", lambda: [])
    monkeypatch.delenv("REALTIME_TRANSCRIPTION_MODEL", raising=False)

    update_calls: list[dict[str, Any]] = []
    handler = OpenAIRealtimeHandler(ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock()))
    handler.get_current_voice = MagicMock(return_value="cedar")  # type: ignore[method-assign]
    handler.client = _make_fake_realtime_client(update_calls=update_calls, reject_updates=1)

    await handler._run_realtime_session()

    assert len(update_calls) == 2
    first_transcription = update_calls[0]["session"]["audio"]["input"]["transcription"]
    assert first_transcription["model"] == "gpt-transcribe"

    second_transcription = update_calls[1]["session"]["audio"]["input"]["transcription"]
    assert second_transcription["model"] == "gpt-4o-transcribe"
    assert second_transcription["language"] == "zh"
    assert "keywords" not in second_transcription and "prompt" not in second_transcription

    # Startup proceeded past the retried update rather than aborting.
    assert handler.connection is not None


# --------------------------------------------------------------------------
# VAD knobs — every knob at a NON-default value, plus malformed input
# --------------------------------------------------------------------------


def test_vad_tuning_from_env(monkeypatch: pytest.MonkeyPatch, handler: OpenAIRealtimeHandler) -> None:
    """All three server-VAD numeric knobs come from the environment (D-003)."""
    monkeypatch.setenv("REALTIME_VAD_SILENCE_DURATION_MS", "1200")
    monkeypatch.setenv("REALTIME_VAD_THRESHOLD", "0.72")
    monkeypatch.setenv("REALTIME_VAD_PREFIX_PADDING_MS", "450")

    td = handler._get_session_config(tool_specs=[])["audio"]["input"]["turn_detection"]

    assert td["type"] == "server_vad"
    # False since Task 8: the client owns the interrupt decision in solo mode
    # too (tests/test_solo_barge.py covers the flag itself).
    assert td["interrupt_response"] is False
    # All three differ from the built-in defaults (1000 / 0.5 / 300).
    assert td["silence_duration_ms"] == 1200
    assert td["threshold"] == pytest.approx(0.72)
    assert td["prefix_padding_ms"] == 450


def test_vad_defaults_when_env_is_unset(monkeypatch: pytest.MonkeyPatch, handler: OpenAIRealtimeHandler) -> None:
    """With nothing configured, the Chinese-tuned defaults apply."""
    for name in (
        "REALTIME_VAD_SILENCE_DURATION_MS",
        "REALTIME_VAD_THRESHOLD",
        "REALTIME_VAD_PREFIX_PADDING_MS",
        "REALTIME_VAD_TYPE",
    ):
        monkeypatch.delenv(name, raising=False)

    td = handler._get_session_config(tool_specs=[])["audio"]["input"]["turn_detection"]

    # Raised from the API's 500 for mid-sentence pauses; 1000 since the 2026-08-30
    # patience wave (was 800 from D-023).
    assert td["silence_duration_ms"] == 1000
    assert td["threshold"] == pytest.approx(0.5)
    assert td["prefix_padding_ms"] == 300


@pytest.mark.parametrize(
    ("env_name", "key", "expected"),
    [
        ("REALTIME_VAD_SILENCE_DURATION_MS", "silence_duration_ms", 1000),
        ("REALTIME_VAD_PREFIX_PADDING_MS", "prefix_padding_ms", 300),
        ("REALTIME_VAD_THRESHOLD", "threshold", 0.5),
    ],
)
def test_malformed_vad_number_warns_and_uses_default(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    handler: OpenAIRealtimeHandler,
    env_name: str,
    key: str,
    expected: float,
) -> None:
    """A bad .env value must warn and degrade, never abort the session."""
    monkeypatch.setenv(env_name, "not-a-number")

    with caplog.at_level(logging.WARNING, logger="reachy_companion.openai_realtime"):
        td = handler._get_session_config(tool_specs=[])["audio"]["input"]["turn_detection"]

    assert td[key] == pytest.approx(expected)
    assert env_name in caplog.text


def test_unknown_vad_type_warns_and_falls_back_to_server_vad(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, handler: OpenAIRealtimeHandler
) -> None:
    """An unrecognized REALTIME_VAD_TYPE must warn rather than silently degrade."""
    monkeypatch.setenv("REALTIME_VAD_TYPE", "magic_vad")

    with caplog.at_level(logging.WARNING, logger="reachy_companion.openai_realtime"):
        td = handler._get_session_config(tool_specs=[])["audio"]["input"]["turn_detection"]

    assert td["type"] == "server_vad"
    assert "REALTIME_VAD_TYPE" in caplog.text


def test_semantic_vad_from_env(monkeypatch: pytest.MonkeyPatch, handler: OpenAIRealtimeHandler) -> None:
    """REALTIME_VAD_TYPE=semantic_vad switches turn detection to the semantic detector."""
    monkeypatch.setenv("REALTIME_VAD_TYPE", "semantic_vad")
    monkeypatch.setenv("REALTIME_VAD_EAGERNESS", "low")

    td = handler._get_session_config(tool_specs=[])["audio"]["input"]["turn_detection"]

    assert td["type"] == "semantic_vad"
    assert td["eagerness"] == "low"
    # Task 8: semantic VAD gets the same client-owned interrupt flag as server VAD.
    assert td["interrupt_response"] is False


def test_invalid_eagerness_warns_and_falls_back_to_auto(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, handler: OpenAIRealtimeHandler
) -> None:
    """An unsupported eagerness value must not be forwarded to the API."""
    monkeypatch.setenv("REALTIME_VAD_TYPE", "semantic_vad")
    monkeypatch.setenv("REALTIME_VAD_EAGERNESS", "nonsense")

    with caplog.at_level(logging.WARNING, logger="reachy_companion.openai_realtime"):
        td = handler._get_session_config(tool_specs=[])["audio"]["input"]["turn_detection"]

    assert td["eagerness"] == "auto"
    assert "REALTIME_VAD_EAGERNESS" in caplog.text


# --------------------------------------------------------------------------
# reasoning.effort — pinned so a server-side default change cannot silently
# add pre-speech latency (2026-08-30 patience wave, Task 6).
#
# Every test here clears REALTIME_REASONING_EFFORT first: the autouse fixture in
# this file only scrubs VOICEFX_*, so an exported shell value would otherwise
# decide what the "default" assertions see.
# --------------------------------------------------------------------------


def test_session_config_pins_reasoning_effort_to_low(
    monkeypatch: pytest.MonkeyPatch, handler: OpenAIRealtimeHandler
) -> None:
    """Unconfigured, the session ships the documented voice-agent effort."""
    monkeypatch.delenv("REALTIME_REASONING_EFFORT", raising=False)

    cfg = handler._get_session_config(tool_specs=[])

    assert cfg["reasoning"] == {"effort": "low"}  # type: ignore[typeddict-item]


def test_reasoning_effort_off_omits_the_field(
    monkeypatch: pytest.MonkeyPatch, handler: OpenAIRealtimeHandler
) -> None:
    """`off` means "say nothing" — the server default stands, unpinned."""
    monkeypatch.setenv("REALTIME_REASONING_EFFORT", "off")

    assert "reasoning" not in handler._get_session_config(tool_specs=[])


def test_reasoning_effort_env_override(
    monkeypatch: pytest.MonkeyPatch, handler: OpenAIRealtimeHandler
) -> None:
    """A supported effort passes through for the on-robot A/B."""
    monkeypatch.setenv("REALTIME_REASONING_EFFORT", "minimal")

    cfg = handler._get_session_config(tool_specs=[])

    assert cfg["reasoning"] == {"effort": "minimal"}  # type: ignore[typeddict-item]


def test_invalid_reasoning_effort_warns_and_falls_back_to_low(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, handler: OpenAIRealtimeHandler
) -> None:
    """A bad .env value degrades with a warning rather than reaching the API."""
    monkeypatch.setenv("REALTIME_REASONING_EFFORT", "ludicrous")

    with caplog.at_level(logging.WARNING, logger="reachy_companion.openai_realtime"):
        cfg = handler._get_session_config(tool_specs=[])

    assert cfg["reasoning"] == {"effort": "low"}  # type: ignore[typeddict-item]
    assert "REALTIME_REASONING_EFFORT" in caplog.text


def test_the_transcription_fallback_config_strips_reasoning(
    monkeypatch: pytest.MonkeyPatch, handler: OpenAIRealtimeHandler
) -> None:
    """A rejected session.update must not be retried with the same unknown field.

    `reasoning` is not in the installed SDK stub, so it rides the runtime-dict
    cast; if the server is what rejected it, retrying with it still attached
    would fail again and leave the robot mute at boot.
    """
    monkeypatch.delenv("REALTIME_REASONING_EFFORT", raising=False)
    monkeypatch.delenv("REALTIME_TRANSCRIPTION_MODEL", raising=False)

    cfg = handler._get_session_config(tool_specs=[])
    assert "reasoning" in cfg  # precondition: there is something to strip

    fallback = handler._session_config_fallback(cfg)

    assert fallback is not None
    assert "reasoning" not in fallback
    assert "reasoning" in cfg  # the retry is a copy; the original is untouched


# --------------------------------------------------------------------------
# max_output_tokens — a runaway-monologue rail, not a brevity knob
# (2026-08-30 patience wave, Task 7). Same env-scrubbing rule as above.
# --------------------------------------------------------------------------


def test_session_config_ships_the_output_token_rail(
    monkeypatch: pytest.MonkeyPatch, handler: OpenAIRealtimeHandler
) -> None:
    """~900 tokens is roughly 40 s of speech — loose enough to never fire in normal use."""
    monkeypatch.delenv("REALTIME_MAX_OUTPUT_TOKENS", raising=False)

    cfg = handler._get_session_config(tool_specs=[])

    assert cfg["max_output_tokens"] == 900


def test_max_output_tokens_inf_removes_the_rail(
    monkeypatch: pytest.MonkeyPatch, handler: OpenAIRealtimeHandler
) -> None:
    """`inf` omits the field entirely, restoring the server's own ceiling."""
    monkeypatch.setenv("REALTIME_MAX_OUTPUT_TOKENS", "inf")

    assert "max_output_tokens" not in handler._get_session_config(tool_specs=[])


def test_max_output_tokens_env_override(
    monkeypatch: pytest.MonkeyPatch, handler: OpenAIRealtimeHandler
) -> None:
    """A plain number passes through for a deliberately tight bench test."""
    monkeypatch.setenv("REALTIME_MAX_OUTPUT_TOKENS", "200")

    assert handler._get_session_config(tool_specs=[])["max_output_tokens"] == 200


@pytest.mark.parametrize(("raw", "clamped"), [("99999", 4096), ("-5", 1)])
def test_out_of_range_max_output_tokens_warns_and_clamps(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    handler: OpenAIRealtimeHandler,
    raw: str,
    clamped: int,
) -> None:
    """Clamping is never silent — `-5` would otherwise mute the robot at one token.

    Above the ceiling the clamp saves a server rejection; below the floor it is
    a real misconfiguration the operator has to be able to see in the journal.
    Same "every knob degrades with a warning" rule as the VAD knobs.
    """
    monkeypatch.setenv("REALTIME_MAX_OUTPUT_TOKENS", raw)

    with caplog.at_level(logging.WARNING):
        cfg = handler._get_session_config(tool_specs=[])

    assert cfg["max_output_tokens"] == clamped
    assert "REALTIME_MAX_OUTPUT_TOKENS" in caplog.text
    assert "Clamping" in caplog.text


def test_invalid_max_output_tokens_warns_and_uses_the_default(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, handler: OpenAIRealtimeHandler
) -> None:
    """A bad .env value must warn and degrade, never abort the session."""
    monkeypatch.setenv("REALTIME_MAX_OUTPUT_TOKENS", "lots")

    with caplog.at_level(logging.WARNING, logger="reachy_companion.openai_realtime"):
        cfg = handler._get_session_config(tool_specs=[])

    assert cfg["max_output_tokens"] == 900
    assert "REALTIME_MAX_OUTPUT_TOKENS" in caplog.text


# --------------------------------------------------------------------------
# Client build
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_client_puts_model_in_the_connect_query(handler: OpenAIRealtimeHandler) -> None:
    """The model id must reach `realtime.connect` via the base's connect_kwargs seam."""
    client = await handler._build_realtime_client()
    assert client.api_key == os.environ["OPENAI_API_KEY"]
    assert handler._realtime_connect_query == {"model": realtime_model()}


@pytest.mark.asyncio
async def test_build_client_requires_an_api_key(
    monkeypatch: pytest.MonkeyPatch, handler: OpenAIRealtimeHandler
) -> None:
    """A missing OPENAI_API_KEY must fail fast with a clear message."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        await handler._build_realtime_client()


@pytest.mark.asyncio
async def test_build_client_is_the_session_restart_reset_point(handler: OpenAIRealtimeHandler) -> None:
    """Starting or restarting a session must not inherit the old filter tails."""
    primed = _to_int16(_sine(MODEL_RATE, 4800))
    fresh = _StreamingResampler(MODEL_RATE, ROBOT_RATE).process(primed)

    handler._input_resampler = _StreamingResampler(ROBOT_RATE, MODEL_RATE)
    handler._output_resampler = _StreamingResampler(MODEL_RATE, ROBOT_RATE)
    handler._output_resampler.process(primed)

    await handler._build_realtime_client()

    # After the reset the stream behaves like a brand-new one (bar int16 dither).
    assert _lsb_diff(handler._output_resampler.process(primed), fresh) <= 4


# --------------------------------------------------------------------------
# Streaming resampler
# --------------------------------------------------------------------------


def test_streaming_resampler_is_seamless_across_odd_chunks() -> None:
    """Chunked input must reconstruct the signal as well as a whole-signal resample.

    The stateless per-chunk alternative zero-pads each chunk and discards the
    filter tail; this pins the streaming path far below that error.
    """
    n = 24000
    source = _to_int16(_sine(MODEL_RATE, n))
    stream = _StreamingResampler(MODEL_RATE, ROBOT_RATE)

    out, i, k, sizes = [], 0, 0, (479, 501)
    while i < n:
        chunk = sizes[k % len(sizes)]
        k += 1
        out.append(stream.process(source[i : i + chunk]))
        i += chunk
    got = np.concatenate(out)

    exact = n * ROBOT_RATE / MODEL_RATE
    # The only shortfall is the constant filter delay still held in the stream,
    # not per-chunk drift.
    assert abs(len(got) + stream.delay - exact) <= 2

    ideal = _sine(ROBOT_RATE, len(got))
    error = np.abs(_to_float(got) - ideal)[400:-400].max()
    assert error < 0.01


def test_streaming_resampler_output_does_not_depend_on_chunking() -> None:
    """Different chunk sizes over the same signal must give the same total length."""
    n = 24000
    source = _to_int16(_sine(MODEL_RATE, n))

    def run(sizes: tuple[int, ...]) -> int:
        stream = _StreamingResampler(MODEL_RATE, ROBOT_RATE)
        total, i, k = 0, 0, 0
        while i < n:
            chunk = sizes[k % len(sizes)]
            k += 1
            total += len(stream.process(source[i : i + chunk]))
            i += chunk
        return total

    assert run((479, 501)) == run((480,)) == run((1024, 137))


def test_stateless_chunked_resampling_is_the_bug_being_fixed() -> None:
    """Guard the regression: the one-shot-per-chunk approach fails the same bound."""
    n = 24000
    signal = _sine(MODEL_RATE, n)
    stateless = np.concatenate([resample_poly(signal[i : i + 480], 2, 3) for i in range(0, n, 480)]).astype(np.float32)

    ideal = _sine(ROBOT_RATE, len(stateless))
    error = np.abs(stateless - ideal)[400:-400].max()
    assert error > 0.01  # measured ~0.08 on this 0.5-amplitude signal


def test_streaming_resampler_preserves_the_channel_first_shape() -> None:
    """The model's (1, N) PCM must survive; soxr itself rejects it as 2-D input."""
    stream = _StreamingResampler(MODEL_RATE, ROBOT_RATE)
    out = stream.process(_to_int16(_sine(MODEL_RATE, 4800)).reshape(1, -1))

    assert out.ndim == 2
    assert out.shape[0] == 1
    assert out.dtype == np.int16

    mono = _StreamingResampler(MODEL_RATE, ROBOT_RATE).process(_to_int16(_sine(MODEL_RATE, 4800)))
    assert np.array_equal(out.reshape(-1), mono)


def test_streaming_resampler_reset_restores_a_fresh_stream() -> None:
    """reset() must drop the filter tail completely.

    The residual is soxr's int16 output dither, whose RNG `clear()` does not
    reset (the float32 path is bit-exact); it measures 2 LSB, about -84 dBFS.
    Leaving the tail in place instead measures ~23886 LSB, which the companion
    assertion pins.
    """
    block = _to_int16(_sine(MODEL_RATE, 4800))
    fresh = _StreamingResampler(MODEL_RATE, ROBOT_RATE).process(block)

    stream = _StreamingResampler(MODEL_RATE, ROBOT_RATE)
    stream.process(block)
    stream.process(block)
    stream.reset()
    assert _lsb_diff(stream.process(block), fresh) <= 4

    bleeding = _StreamingResampler(MODEL_RATE, ROBOT_RATE)
    bleeding.process(block)
    assert _lsb_diff(bleeding.process(block), fresh) > 1000


# --------------------------------------------------------------------------
# Microphone path
# --------------------------------------------------------------------------


class _FakeInputAudioBuffer:
    """Capture base64 audio appended to the realtime input buffer."""

    def __init__(self) -> None:
        """Start with no captured frames."""
        self.appended: list[str] = []

    async def append(self, *, audio: str) -> None:
        """Record one appended frame."""
        self.appended.append(audio)

    def decoded(self) -> np.ndarray:
        """Return every appended frame concatenated as int16 PCM."""
        if not self.appended:
            return np.zeros(0, dtype=np.int16)
        return np.concatenate([np.frombuffer(base64.b64decode(a), dtype=np.int16) for a in self.appended])


class _FakeConnection:
    """Minimal stand-in for AsyncRealtimeConnection."""

    def __init__(self) -> None:
        """Create the fake input audio buffer."""
        self.input_audio_buffer = _FakeInputAudioBuffer()


def _connected(handler: OpenAIRealtimeHandler) -> _FakeInputAudioBuffer:
    """Attach a fake connection and return its capture buffer."""
    connection = _FakeConnection()
    handler.connection = connection  # type: ignore[assignment]
    return connection.input_audio_buffer


@pytest.mark.asyncio
async def test_receive_resamples_microphone_audio_to_24k(handler: OpenAIRealtimeHandler) -> None:
    """Mic frames arrive at the robot rate and must be upsampled to the model rate."""
    buffer = _connected(handler)
    n = 16000
    pcm = _to_int16(_sine(ROBOT_RATE, n))

    for i in range(0, n, 320):  # 20 ms frames, as the recorder delivers them
        await handler.receive((ROBOT_RATE, pcm[i : i + 320]))

    got = buffer.decoded()
    exact = n * MODEL_RATE / ROBOT_RATE
    assert abs(len(got) + handler._input_resampler.delay - exact) <= 2

    ideal = _sine(MODEL_RATE, len(got))
    assert np.abs(_to_float(got) - ideal)[400:-400].max() < 0.01


@pytest.mark.asyncio
async def test_receive_downmixes_stereo_before_resampling(handler: OpenAIRealtimeHandler) -> None:
    """Stereo mic frames must be reduced to mono, then resampled."""
    buffer = _connected(handler)
    mono = _to_int16(_sine(ROBOT_RATE, 8000))
    stereo = np.stack([mono, np.zeros_like(mono)])  # (2, N) channel-first

    for i in range(0, 8000, 320):
        await handler.receive((ROBOT_RATE, stereo[:, i : i + 320]))

    got = buffer.decoded()
    ideal = _sine(MODEL_RATE, len(got))
    assert np.abs(_to_float(got) - ideal)[400:-400].max() < 0.01


@pytest.mark.asyncio
async def test_receive_skips_resampling_when_already_at_model_rate(
    handler: OpenAIRealtimeHandler,
) -> None:
    """A mic already at 24 kHz must be passed through untouched, with no stream built."""
    buffer = _connected(handler)

    await handler.receive((MODEL_RATE, np.zeros(480, dtype=np.int16)))

    assert buffer.decoded().size == 480
    assert handler._input_resampler is None


@pytest.mark.asyncio
async def test_receive_ignores_empty_frames(handler: OpenAIRealtimeHandler) -> None:
    """Empty mic frames must not reach the realtime buffer."""
    buffer = _connected(handler)

    await handler.receive((ROBOT_RATE, np.zeros(0, dtype=np.int16)))

    assert buffer.appended == []


@pytest.mark.asyncio
async def test_receive_does_not_send_while_the_resampler_primes(
    handler: OpenAIRealtimeHandler,
) -> None:
    """The first frames produce no output yet; nothing empty may be sent upstream."""
    buffer = _connected(handler)

    await handler.receive((ROBOT_RATE, _to_int16(_sine(ROBOT_RATE, 320))))

    assert all(a != "" for a in buffer.appended)


# --------------------------------------------------------------------------
# Speaker path
# --------------------------------------------------------------------------


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
    n = 24000
    pcm = _to_int16(_sine(MODEL_RATE, n))

    collected = []
    for i in range(0, n, 480):  # 20 ms of model PCM, shaped (1, N) as the base enqueues it
        await h.output_queue.put((MODEL_RATE, pcm[i : i + 480].reshape(1, -1)))
        output = await h.emit()
        assert isinstance(output, tuple)
        rate, frame = output
        assert rate == ROBOT_RATE
        assert frame.ndim == 2 and frame.shape[0] == 1  # (1, N) preserved for console.py
        assert frame.dtype == np.int16
        collected.append(frame.reshape(-1))

    got = np.concatenate(collected)
    exact = n * ROBOT_RATE / MODEL_RATE
    assert abs(len(got) + h._output_resampler.delay - exact) <= 2

    ideal = _sine(ROBOT_RATE, len(got))
    assert np.abs(_to_float(got) - ideal)[400:-400].max() < 0.01


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
    assert h._output_resampler is None
    assert h._voicefx is None  # nothing on the output path is built either


@pytest.mark.asyncio
async def test_emit_returns_none_when_the_queue_is_empty() -> None:
    """The base emit() timeout behavior must be preserved."""
    h = _emit_ready_handler()
    assert await h.emit() is None


# --------------------------------------------------------------------------
# VoiceFX in the emit chain (D-010)
# --------------------------------------------------------------------------


class _OrderSpy:
    """Stand in for one stage of the output chain and record when it ran."""

    def __init__(self, name: str, calls: list[str], rate: int = MODEL_RATE) -> None:
        """Record into the shared `calls` log under `name`."""
        self.name = name
        self.calls = calls
        # Both accessors rebuild their stage when the source rate changed; these
        # make the spy look like the stage that is already correct for 24 kHz.
        self.rate = rate
        self.src_rate = rate
        self.resets = 0

    def process(self, pcm: np.ndarray) -> np.ndarray:
        """Record one call and pass the buffer through untouched."""
        self.calls.append(self.name)
        return pcm

    def reset(self) -> None:
        """Record one reset."""
        self.resets += 1


@pytest.mark.asyncio
async def test_emit_applies_the_voice_filter_before_the_output_resample() -> None:
    """The filter works on the model's 24 kHz PCM, not on the 16 kHz robot audio.

    Order matters twice over: the pitch stage is a rate trick that assumes the
    model rate, and filtering after the downsample would waste the 24 kHz
    headroom the ring modulator needs.
    """
    h = _emit_ready_handler()
    calls: list[str] = []
    h._voicefx = _OrderSpy("voicefx", calls)  # type: ignore[assignment]
    h._output_resampler = _OrderSpy("resampler", calls)  # type: ignore[assignment]

    await h.output_queue.put((MODEL_RATE, np.zeros((1, 480), dtype=np.int16)))
    await h.emit()

    assert calls == ["voicefx", "resampler"]


@pytest.mark.asyncio
async def test_emit_with_the_filter_disabled_is_byte_for_byte_the_pre_task_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VOICEFX_ENABLED defaults to off, and off must cost exactly nothing.

    Not "sounds the same" — the same bytes as a bare resample, with no float
    round-trip in between, because a disabled filter returns the caller's own
    array object.
    """
    monkeypatch.delenv("VOICEFX_ENABLED", raising=False)
    h = _emit_ready_handler()
    n = 4800
    pcm = _to_int16(_sine(MODEL_RATE, n)).reshape(1, -1)
    expected = _StreamingResampler(MODEL_RATE, ROBOT_RATE).process(pcm)

    await h.output_queue.put((MODEL_RATE, pcm))
    output = await h.emit()

    assert isinstance(output, tuple)
    assert np.array_equal(output[1], expected)
    assert h._voicefx is not None and h._voicefx.enabled is False
    assert h._voicefx.process(pcm) is pcm  # identity, not a copy


@pytest.mark.asyncio
async def test_emit_applies_the_filter_when_it_is_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the knob on, the audio that reaches the speaker is pitched but NOT shorter.

    End-to-end through the real chain — filter, then downsample — so this also
    pins that the pitch survives the 24k->16k stage rather than being an artefact
    measured before it. Round 1's chain shortened the reply by 21 % on the way;
    D-011's WSOLA stretch removed that, which is the difference this asserts.
    """
    monkeypatch.setenv("VOICEFX_ENABLED", "1")
    monkeypatch.setenv("VOICEFX_PITCH_SEMITONES", "4")
    monkeypatch.setenv("VOICEFX_RINGMOD_HZ", "0")
    h = _emit_ready_handler()
    n = 48000  # 2 s of 440 Hz at the model rate
    pcm = _to_int16(_sine(MODEL_RATE, n))

    collected = []
    for i in range(0, n, 480):
        await h.output_queue.put((MODEL_RATE, pcm[i : i + 480].reshape(1, -1)))
        output = await h.emit()
        assert isinstance(output, tuple)
        assert output[0] == ROBOT_RATE
        collected.append(output[1].reshape(-1))

    got = np.concatenate(collected)
    fx = h._voicefx
    assert fx is not None and fx.enabled is True

    # As long as the input, less only what the three stages still hold (the
    # filter's tail is quoted on its own input side, which duration preservation
    # makes the same unit as its output side).
    filtered = n - fx.pending_delay
    expected = filtered * fx.duration_ratio * ROBOT_RATE / MODEL_RATE
    assert abs(len(got) + h._output_resampler.delay - expected) <= 2
    # Unfiltered would be 32000; round 1's resample-only pitch gave ~25400.
    assert len(got) > n * ROBOT_RATE / MODEL_RATE * 0.95

    # And pitched: the dominant frequency at the speaker is 440 * 2**(4/12).
    window = 8192
    slice_ = got[2048 : 2048 + window].astype(np.float64)
    spectrum = np.abs(np.fft.rfft(slice_ * np.hanning(window)))
    peak = np.fft.rfftfreq(window, 1.0 / ROBOT_RATE)[int(np.argmax(spectrum))]
    assert abs(peak - 440.0 * 2.0 ** (4 / 12)) <= ROBOT_RATE / window


# --------------------------------------------------------------------------
# Onset amplitude ramp (Task 5) — AEC convergence aid
# --------------------------------------------------------------------------


def test_notify_response_started_arms_the_configured_sample_count(monkeypatch: pytest.MonkeyPatch) -> None:
    """The hook must convert REALTIME_ONSET_RAMP_MS into a sample count at SAMPLE_RATE."""
    monkeypatch.setenv("REALTIME_ONSET_RAMP_MS", "10")
    h = OpenAIRealtimeHandler.__new__(OpenAIRealtimeHandler)

    h._notify_response_started()

    assert h._onset_ramp_remaining == 240  # 24000 Hz * 10 ms / 1000


def test_onset_ramp_ms_zero_disables_the_ramp(monkeypatch: pytest.MonkeyPatch) -> None:
    """REALTIME_ONSET_RAMP_MS=0 arms zero samples, i.e. no ramp at all."""
    monkeypatch.setenv("REALTIME_ONSET_RAMP_MS", "0")
    h = OpenAIRealtimeHandler.__new__(OpenAIRealtimeHandler)

    h._notify_response_started()

    assert h._onset_ramp_remaining == 0


def test_apply_onset_ramp_is_a_noop_that_returns_the_same_array_when_unarmed() -> None:
    """A handler that never had a response start (or already finished ramping) must not copy."""
    h = OpenAIRealtimeHandler.__new__(OpenAIRealtimeHandler)
    pcm = np.array([100, 200, 300], dtype=np.int16)

    assert h._apply_onset_ramp(pcm) is pcm


def test_apply_onset_ramp_scales_linearly_and_continues_across_chunk_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 0->1 ramp must track fractional progress across two separate chunks.

    240 samples total (10 ms at 24 kHz). The first 100-sample chunk consumes
    100/240 of the ramp; the second 200-sample chunk finishes the remaining
    140 samples exactly on its 140th sample and leaves the rest untouched.
    """
    monkeypatch.setenv("REALTIME_ONSET_RAMP_MS", "10")
    h = OpenAIRealtimeHandler.__new__(OpenAIRealtimeHandler)
    h._notify_response_started()
    assert h._onset_ramp_remaining == 240

    chunk1 = np.full(100, 16000, dtype=np.int16)
    out1 = h._apply_onset_ramp(chunk1)
    assert int(out1[0]) == 67  # round(16000 * 1/240)
    assert int(out1[50]) == 3400  # round(16000 * 51/240)
    assert int(out1[99]) == 6667  # round(16000 * 100/240)
    assert h._onset_ramp_remaining == 140

    chunk2 = np.full(200, 16000, dtype=np.int16)
    out2 = h._apply_onset_ramp(chunk2)
    assert int(out2[0]) == 6733  # round(16000 * 101/240), continuing chunk1's fraction
    assert int(out2[139]) == 16000  # round(16000 * 240/240): ramp completes exactly here
    assert int(out2[140]) == 16000  # past the ramp: untouched full amplitude
    assert int(out2[199]) == 16000
    assert h._onset_ramp_remaining == 0


@pytest.mark.asyncio
async def test_emit_ramps_robot_rate_audio_from_silence(monkeypatch: pytest.MonkeyPatch) -> None:
    """The `rate == ROBOT_RATE` early-return branch must also apply the ramp."""
    monkeypatch.setenv("REALTIME_ONSET_RAMP_MS", "10")
    h = _emit_ready_handler()
    h._notify_response_started()
    pcm = np.full((1, 300), 16000, dtype=np.int16)  # 300 > the 240-sample ramp
    await h.output_queue.put((ROBOT_RATE, pcm))

    output = await h.emit()

    assert isinstance(output, tuple)
    rate, out = output
    assert rate == ROBOT_RATE
    flat = out.reshape(-1)
    assert int(flat[0]) == 67  # starts near silence
    assert int(flat[239]) == 16000  # ramp complete
    assert int(flat[299]) == 16000  # tail beyond the ramp is untouched
    assert out is not pcm  # ramping mutates -> no longer the caller's own array


@pytest.mark.asyncio
async def test_emit_robot_rate_audio_is_unchanged_when_the_ramp_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REALTIME_ONSET_RAMP_MS=0 must preserve the pre-Task-5 byte-identical path."""
    monkeypatch.setenv("REALTIME_ONSET_RAMP_MS", "0")
    h = _emit_ready_handler()
    h._notify_response_started()  # arms zero samples -> effectively a no-op
    pcm = np.full((1, 300), 16000, dtype=np.int16)
    await h.output_queue.put((ROBOT_RATE, pcm))

    output = await h.emit()

    assert isinstance(output, tuple)
    assert output[1] is pcm


@pytest.mark.asyncio
async def test_emit_applies_the_onset_ramp_before_the_voice_filter() -> None:
    """The ramp must run on raw int16 PCM, before VoiceFX and the resample.

    Otherwise the filter's makeup gain or the resampler's interpolation could
    partly undo the faded-in onset before it reaches the speaker.
    """
    h = _emit_ready_handler()
    calls: list[str] = []
    h._voicefx = _OrderSpy("voicefx", calls)  # type: ignore[assignment]
    h._output_resampler = _OrderSpy("resampler", calls)  # type: ignore[assignment]

    def _spy_ramp(pcm: np.ndarray) -> np.ndarray:
        calls.append("ramp")
        return pcm

    h._apply_onset_ramp = _spy_ramp  # type: ignore[method-assign]

    await h.output_queue.put((MODEL_RATE, np.zeros((1, 480), dtype=np.int16)))
    await h.emit()

    assert calls == ["ramp", "voicefx", "resampler"]


@pytest.mark.asyncio
async def test_emit_fades_in_the_first_reply_through_the_full_output_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End to end: the onset ramp must survive VoiceFX (disabled) and the resample.

    A constant-amplitude 440 Hz tone at the model rate, ramped for its first
    10 ms, must reach the robot rate with its opening samples far quieter than
    its steady-state ones — the audible effect this task exists to produce.
    """
    monkeypatch.setenv("REALTIME_ONSET_RAMP_MS", "10")
    h = _emit_ready_handler()
    h._notify_response_started()
    n = 24000
    pcm = _to_int16(_sine(MODEL_RATE, n))

    collected = []
    for i in range(0, n, 480):
        await h.output_queue.put((MODEL_RATE, pcm[i : i + 480].reshape(1, -1)))
        output = await h.emit()
        assert isinstance(output, tuple)
        collected.append(output[1].reshape(-1))

    got = np.concatenate(collected)
    assert np.abs(got[:20]).max() < 2000  # ramped onset: near silence
    assert np.abs(got[400:500]).max() > 15000  # steady state: full amplitude


@pytest.mark.asyncio
async def test_emit_without_the_onset_ramp_reaches_full_amplitude_immediately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REALTIME_ONSET_RAMP_MS=0 must leave the model-rate path's onset unramped too."""
    monkeypatch.setenv("REALTIME_ONSET_RAMP_MS", "0")
    h = _emit_ready_handler()
    h._notify_response_started()
    n = 24000
    pcm = _to_int16(_sine(MODEL_RATE, n))

    collected = []
    for i in range(0, n, 480):
        await h.output_queue.put((MODEL_RATE, pcm[i : i + 480].reshape(1, -1)))
        output = await h.emit()
        assert isinstance(output, tuple)
        collected.append(output[1].reshape(-1))

    got = np.concatenate(collected)
    assert np.abs(got[:20]).max() > 15000  # no ramp: full amplitude from sample 0


# --------------------------------------------------------------------------
# Barge-in
# --------------------------------------------------------------------------


class _SpyResampler:
    """Record reset() calls."""

    def __init__(self) -> None:
        """Start with no resets recorded."""
        self.resets = 0

    def reset(self) -> None:
        """Record one reset."""
        self.resets += 1


def test_clear_queue_is_none_until_the_console_installs_one() -> None:
    """The base guard `if self._clear_queue:` must still see None when unwired."""
    h = OpenAIRealtimeHandler.__new__(OpenAIRealtimeHandler)
    assert h._clear_queue is None


def test_barge_in_resets_the_output_resampler_and_calls_the_console() -> None:
    """The interrupted utterance's filter tail must not bleed into the next reply."""
    h = OpenAIRealtimeHandler.__new__(OpenAIRealtimeHandler)
    spy = _SpyResampler()
    h._output_resampler = spy  # type: ignore[assignment]
    calls: list[str] = []
    h._clear_queue = lambda: calls.append("flushed")  # as console.py:146 assigns

    clear = h._clear_queue
    assert clear is not None
    clear()

    assert spy.resets == 1
    assert calls == ["flushed"]


def test_barge_in_without_an_output_stream_is_harmless() -> None:
    """A barge-in before any assistant audio must not blow up."""
    h = OpenAIRealtimeHandler.__new__(OpenAIRealtimeHandler)
    calls: list[str] = []
    h._clear_queue = lambda: calls.append("flushed")

    clear = h._clear_queue
    assert clear is not None
    clear()

    assert calls == ["flushed"]


def test_barge_in_resets_the_whole_output_pipeline_but_never_the_microphone() -> None:
    """Barge-in owns the *output* path only.

    Resetting the mic resampler here would throw away the very audio the user is
    interrupting with — the frames that triggered `speech_started` in the first
    place. So the filter and the output resampler are dropped; the mic stream is
    left running.
    """
    h = OpenAIRealtimeHandler.__new__(OpenAIRealtimeHandler)
    mic, speaker, fx = _SpyResampler(), _SpyResampler(), _SpyResampler()
    h._input_resampler = mic  # type: ignore[assignment]
    h._output_resampler = speaker  # type: ignore[assignment]
    h._voicefx = fx  # type: ignore[assignment]
    h._clear_queue = lambda: None

    clear = h._clear_queue
    assert clear is not None
    clear()

    assert (speaker.resets, fx.resets) == (1, 1)
    assert mic.resets == 0


def test_barge_in_without_a_voice_filter_is_harmless() -> None:
    """A barge-in before the filter has ever been built must not blow up."""
    h = OpenAIRealtimeHandler.__new__(OpenAIRealtimeHandler)
    calls: list[str] = []
    h._output_resampler = _SpyResampler()  # type: ignore[assignment]
    h._clear_queue = lambda: calls.append("flushed")

    clear = h._clear_queue
    assert clear is not None
    clear()

    assert calls == ["flushed"]


@pytest.mark.asyncio
async def test_session_build_resets_the_microphone_and_the_output_pipeline() -> None:
    """A reconnect is a clean slate for all three stages, in both directions."""
    h = OpenAIRealtimeHandler.__new__(OpenAIRealtimeHandler)
    mic, speaker, fx = _SpyResampler(), _SpyResampler(), _SpyResampler()
    h._input_resampler = mic  # type: ignore[assignment]
    h._output_resampler = speaker  # type: ignore[assignment]
    h._voicefx = fx  # type: ignore[assignment]

    await h._build_realtime_client()

    assert (mic.resets, speaker.resets, fx.resets) == (1, 1, 1)


def test_stream_handler_init_still_clears_the_callback() -> None:
    """AsyncStreamHandler.__init__ assigns None through the property setter."""
    h = OpenAIRealtimeHandler.__new__(OpenAIRealtimeHandler)
    h._clear_queue = lambda: None
    h._clear_queue = None
    assert h._clear_queue is None


# --------------------------------------------------------------------------
# config.py flips owned by this task
# --------------------------------------------------------------------------


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


def test_env_example_documents_the_new_knobs() -> None:
    """.env.example must not still advertise the old English default."""
    from pathlib import Path

    text = (Path(__file__).resolve().parent.parent / ".env.example").read_text(encoding="utf-8")
    assert 'REALTIME_TRANSCRIPTION_LANGUAGE="zh"' in text
    assert "OPENAI_API_KEY" in text
    assert "REALTIME_VAD_SILENCE_DURATION_MS" in text
    assert "REALTIME_MODEL" in text
    assert "REALTIME_MIN_TURN_CHARS" in text
    assert "REALTIME_PROMPT_HARDENING" in text
    assert "REALTIME_TRANSCRIPTION_MODEL" in text
    assert "REALTIME_TRANSCRIPTION_KEYWORDS" in text
    assert "REALTIME_TRANSCRIPTION_PROMPT" in text
    assert "REALTIME_ONSET_RAMP_MS" in text
    assert "REALTIME_BOOT_GATE" in text
    assert "REALTIME_BOOT_GATE_TIMEOUT_S" in text
    assert "REALTIME_PARTY_FACE_GATE" in text
    assert "REALTIME_PARTY_FACE_FRESH_S" in text
    assert "REALTIME_PARTY_FACE_CENTER" in text
    assert "REALTIME_SOLO_CLIENT_BARGE" in text
    assert "REALTIME_BARGE_CONFIRM_MS" in text
    assert "REALTIME_BARGE_ROLLBACK_TIMEOUT_S" in text
    assert "REALTIME_BARGE_COOLDOWN_MS" in text
    # The face knobs `huggingface_realtime.py` reads at wake time. An operator
    # tuning the boot greeting has no other place to discover them.
    assert "FACE_AUTO_GREET" in text
    assert "FACE_WAKE_BUDGET_MS" in text
    assert "FACE_WAKE_ATTEMPTS" in text
    assert "FACE_WAKE_EXTENDED_MS" in text
    assert "FACE_GREETING_FACTS" in text


def test_session_config_defaults_to_far_field_noise_reduction(handler: OpenAIRealtimeHandler) -> None:
    """T1: far-field noise reduction is on by default for this robot.

    It runs server-side before VAD and cuts false speech triggers.
    """
    cfg = handler._get_session_config(tool_specs=[])
    assert cfg["audio"]["input"]["noise_reduction"] == {"type": "far_field"}


def test_noise_reduction_off_restores_the_bare_input(
    handler: OpenAIRealtimeHandler, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REALTIME_NOISE_REDUCTION=off restores the pre-hardening input config."""
    monkeypatch.setenv("REALTIME_NOISE_REDUCTION", "off")
    cfg = handler._get_session_config(tool_specs=[])
    assert "noise_reduction" not in cfg["audio"]["input"]


def test_noise_reduction_rejects_garbage(
    handler: OpenAIRealtimeHandler, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An invalid mode degrades to far_field with a warning, never a crash."""
    monkeypatch.setenv("REALTIME_NOISE_REDUCTION", "sideways_field")
    cfg = handler._get_session_config(tool_specs=[])
    assert cfg["audio"]["input"]["noise_reduction"] == {"type": "far_field"}
