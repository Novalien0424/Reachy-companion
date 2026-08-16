"""OpenAI gpt-realtime-2.1 backend (D-002).

Subclasses the maintained Hugging Face handler, replacing client build, session
config, sample rate, connect(model=), and adding 16k<->24k resampling at the two
audio boundaries. Everything else — the realtime event loop, tool plumbing,
reconnect policy — is inherited verbatim.

Both audio boundaries are *streams*, not independent buffers, so they use soxr's
stateful `ResampleStream` rather than a per-chunk one-shot: a stateless resampler
zero-pads every chunk and throws away the filter tail, which measures as ~16 %
peak error at the chunk seams (about -40 dB SNR) plus length drift. See
`_StreamingResampler`.
"""

import os
import base64
import logging
from typing import Tuple, cast
from collections.abc import Callable

import soxr
import numpy as np
from openai import AsyncOpenAI
from numpy.typing import NDArray
from typing_extensions import Literal
from openai.types.realtime import RealtimeSessionCreateRequestParam
from openai.types.realtime.realtime_audio_formats_param import AudioPCM
from openai.types.realtime.realtime_audio_input_turn_detection_param import (
    ServerVad,
    SemanticVad,
    RealtimeAudioInputTurnDetectionParam,
)

from reachy_companion.streaming import audio_to_int16
from reachy_companion.tools.core_tools import ToolSpec
from reachy_companion.conversation_handler import HandlerOutput
from reachy_companion.huggingface_realtime import HuggingFaceRealtimeHandler


logger = logging.getLogger(__name__)

MODEL = "gpt-realtime-2.1"
ROBOT_RATE = 16000

_RESAMPLER_QUALITY = "HQ"
_EAGERNESS_VALUES = ("low", "medium", "high", "auto")


def _env_float(name: str, default: float) -> float:
    """Read a float from the environment, warning and falling back when malformed."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("Ignoring invalid %s=%r; using %s.", name, raw, default)
        return default


def _env_int(name: str, default: int) -> int:
    """Read an int from the environment, warning and falling back when malformed."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("Ignoring invalid %s=%r; using %s.", name, raw, default)
        return default


def _eagerness() -> Literal["low", "medium", "high", "auto"]:
    """Read the semantic-VAD eagerness from the environment, falling back to auto."""
    raw = os.getenv("REALTIME_VAD_EAGERNESS", "auto").strip().lower()
    if raw not in _EAGERNESS_VALUES:
        logger.warning("Ignoring invalid REALTIME_VAD_EAGERNESS=%r; using auto.", raw)
        return "auto"
    return cast(Literal["low", "medium", "high", "auto"], raw)


def _turn_detection() -> RealtimeAudioInputTurnDetectionParam:
    """Server-side VAD, tunable via env for Chinese mid-sentence pauses (D-003).

    Every knob degrades to its default with a warning rather than raising, so one
    bad line in a robot's `.env` cannot abort the whole realtime session.
    """
    vad_type = os.getenv("REALTIME_VAD_TYPE", "server_vad").strip().lower() or "server_vad"
    if vad_type == "semantic_vad":
        return SemanticVad(
            type="semantic_vad",
            eagerness=_eagerness(),
            interrupt_response=True,
        )
    if vad_type != "server_vad":
        logger.warning("Ignoring invalid REALTIME_VAD_TYPE=%r; using server_vad.", vad_type)
    return ServerVad(
        type="server_vad",
        interrupt_response=True,
        threshold=_env_float("REALTIME_VAD_THRESHOLD", 0.5),
        prefix_padding_ms=_env_int("REALTIME_VAD_PREFIX_PADDING_MS", 300),
        silence_duration_ms=_env_int("REALTIME_VAD_SILENCE_DURATION_MS", 800),
    )


class _StreamingResampler:
    """One stateful soxr resampler for one direction of one session.

    `soxr.ResampleStream` carries its polyphase filter state across chunks, so
    consecutive 20 ms frames rejoin seamlessly and the sample count converges on
    the exact rate ratio. Everything here is int16 end to end — the mic, the
    model's PCM and the SDK's speaker sink all speak int16, so there is no
    float round-trip to lose amplitude in.
    """

    def __init__(self, src_rate: int, dst_rate: int) -> None:
        """Open a resampling stream from src_rate to dst_rate."""
        self.src_rate = src_rate
        self.dst_rate = dst_rate
        self._stream = soxr.ResampleStream(
            src_rate,
            dst_rate,
            1,
            dtype="int16",
            quality=_RESAMPLER_QUALITY,
        )

    @property
    def delay(self) -> float:
        """Output samples still inside the filter — a constant priming cost, not drift.

        Measured with HQ quality: 768 samples (32 ms) for 16k->24k and 341
        samples (21 ms) for 24k->16k. It is paid once when a stream starts, and
        it is why a chunk sequence's total output is short by exactly this much
        until the stream is flushed.
        """
        return float(self._stream.delay())

    def process(self, pcm: NDArray[np.int16]) -> NDArray[np.int16]:
        """Resample one chunk, continuing from the previous chunk's filter state.

        soxr reads 2-D input as [frame, channel]; the model's PCM is (1, N)
        channel-first, which soxr would reject as a channel-count mismatch. So
        flatten to mono, then give the caller its own shape back.
        """
        flat = np.ascontiguousarray(pcm.reshape(-1))
        resampled: NDArray[np.int16] = np.asarray(self._stream.resample_chunk(flat), dtype=np.int16)
        if pcm.ndim == 2:
            return resampled.reshape(1, -1)
        return resampled

    def reset(self) -> None:
        """Drop the filter tail so the next utterance does not inherit this one's."""
        self._stream.clear()


class OpenAIRealtimeHandler(HuggingFaceRealtimeHandler):
    """Realtime handler for the direct OpenAI Realtime API at 24 kHz."""

    SAMPLE_RATE = 24000

    # Class-level defaults so instances built with __new__ (tests) work, and so
    # the streams are created lazily once the real rates are known.
    _input_resampler: _StreamingResampler | None = None
    _output_resampler: _StreamingResampler | None = None
    _clear_queue_callback: Callable[[], None] | None = None

    @property
    def _clear_queue(self) -> Callable[[], None] | None:
        """Return the console's queue flush, wrapped to also reset the output stream.

        The base calls this on `input_audio_buffer.speech_started`
        (`huggingface_realtime.py:749-750`), i.e. on barge-in, right after
        `console.py:146` has installed `clear_audio_queue`. The ~21 ms still held
        in the output resampler belongs to the utterance being interrupted, so it
        has to be dropped with the queue rather than bleeding into the next reply.
        """
        callback = self._clear_queue_callback
        if callback is None:
            return None

        def clear_queue_and_reset_resampler() -> None:
            if self._output_resampler is not None:
                self._output_resampler.reset()
            callback()

        return clear_queue_and_reset_resampler

    @_clear_queue.setter
    def _clear_queue(self, callback: Callable[[], None] | None) -> None:
        """Store the console's flush callback (assigned by `console.py:146`)."""
        self._clear_queue_callback = callback

    def _mic_resampler(self, src_rate: int) -> _StreamingResampler:
        """Return the mic->model stream, rebuilding it if the mic rate changed."""
        stream = self._input_resampler
        if stream is None or stream.src_rate != src_rate:
            stream = _StreamingResampler(src_rate, self.SAMPLE_RATE)
            self._input_resampler = stream
        return stream

    def _speaker_resampler(self, src_rate: int) -> _StreamingResampler:
        """Return the model->speaker stream, rebuilding it if the model rate changed."""
        stream = self._output_resampler
        if stream is None or stream.src_rate != src_rate:
            stream = _StreamingResampler(src_rate, ROBOT_RATE)
            self._output_resampler = stream
        return stream

    def _reset_resamplers(self) -> None:
        """Drop both filter tails, so a new session never replays the old one's."""
        if self._input_resampler is not None:
            self._input_resampler.reset()
        if self._output_resampler is not None:
            self._output_resampler.reset()

    async def _build_realtime_client(self) -> AsyncOpenAI:
        """Build the OpenAI realtime client and pin the model for the connect call.

        The base class builds `connect_kwargs` inline inside its 247-line
        `_run_realtime_session` (`huggingface_realtime.py:705-708`), whose only
        seam is `self._realtime_connect_query` -> `extra_query`. That is the same
        destination as `connect(model=...)`: the SDK merges `model` and
        `extra_query` into one websocket query-param dict (openai 2.28.0,
        `openai/resources/realtime/realtime.py:378-384` async, `:569-575` sync),
        so the URL is identical to the recovered handler's
        `connect_kwargs["model"] = ...` (`base_realtime_5b8d974.py:714-716`).

        This runs on every connect and reconnect (`huggingface_realtime.py:362,
        374, 415`), which makes it the session-start seam for the resamplers too.
        """
        self._reset_resamplers()
        self._realtime_connect_query = {"model": MODEL}
        api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY must be set to use the OpenAI realtime backend")
        return AsyncOpenAI(api_key=api_key)

    def _get_session_config(self, tool_specs: list[ToolSpec]) -> RealtimeSessionCreateRequestParam:
        """Return the Hugging Face session config retargeted at gpt-realtime-2.1."""
        cfg = super()._get_session_config(tool_specs)
        cfg["model"] = MODEL
        cfg["audio"]["output"]["format"] = AudioPCM(type="audio/pcm", rate=24000)
        cfg["audio"]["input"]["format"] = AudioPCM(type="audio/pcm", rate=24000)
        cfg["audio"]["input"]["turn_detection"] = _turn_detection()
        return cfg

    # Microphone receive — adapted from huggingface_realtime.py:947-982,
    # added: upsample the robot's 16 kHz mic to the model's 24 kHz.
    async def receive(self, frame: Tuple[int, NDArray[np.int16]]) -> None:
        """Receive a microphone frame, resample it to 24 kHz, and send it upstream.

        Args:
            frame: A tuple containing (sample_rate, audio_data).

        """
        if not self.connection:
            return

        input_sample_rate, audio_frame = frame
        if audio_frame.size == 0:
            return

        # Reshape if needed
        if audio_frame.ndim == 2:
            # channels-last convention
            if audio_frame.shape[1] > audio_frame.shape[0]:
                audio_frame = audio_frame.T
            # Multiple channels -> Mono channel
            if audio_frame.shape[1] > 1:
                audio_frame = audio_frame[:, 0]

        # Cast if needed, then resample if needed (mono first — the resampler is
        # single-channel and downmixing after it would waste half the work).
        outgoing = audio_to_int16(audio_frame)
        if input_sample_rate != self.SAMPLE_RATE:
            outgoing = self._mic_resampler(input_sample_rate).process(outgoing)

        # A streaming resampler legitimately returns nothing while it primes.
        if outgoing.size == 0:
            return

        # Send to the realtime input buffer (guard against races during reconnect).
        try:
            audio_message = base64.b64encode(outgoing.tobytes()).decode("utf-8")
            await self.connection.input_audio_buffer.append(audio=audio_message)
        except Exception as e:
            logger.debug("Dropping audio frame: connection not ready (%s)", e)
            return

    async def emit(self) -> HandlerOutput:
        """Emit the next output, downsampling model audio to the robot's rate.

        `console.py:905-924` discards the rate label and pushes straight into the
        SDK's fixed 16 kHz sink, so 24 kHz assistant PCM has to be converted here
        — the last point we own before `play_loop` sees it — rather than in the
        console. Text outputs pass through untouched.
        """
        handler_output = await super().emit()
        if not isinstance(handler_output, tuple):
            return handler_output

        rate, pcm = handler_output
        if rate == ROBOT_RATE:
            return handler_output

        return ROBOT_RATE, self._speaker_resampler(rate).process(audio_to_int16(pcm))
