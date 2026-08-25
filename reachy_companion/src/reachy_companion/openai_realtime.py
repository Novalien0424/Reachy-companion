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
import copy
import base64
import logging
from typing import Any, Tuple, cast
from collections.abc import Callable

import soxr
import numpy as np
from openai import AsyncOpenAI
from numpy.typing import NDArray
from typing_extensions import Literal
from openai.types.realtime import RealtimeSessionCreateRequestParam
from openai.types.realtime.noise_reduction_type import NoiseReductionType
from openai.types.realtime.realtime_audio_formats_param import AudioPCM
from openai.types.realtime.realtime_audio_config_input_param import NoiseReduction
from openai.types.realtime.realtime_audio_input_turn_detection_param import (
    ServerVad,
    SemanticVad,
    RealtimeAudioInputTurnDetectionParam,
)

from reachy_companion.config import config
from reachy_companion.streaming import audio_to_int16
from reachy_companion.audio.voicefx import VoiceFX
from reachy_companion.audio.envparse import env_int, env_float
from reachy_companion.tools.core_tools import ToolSpec
from reachy_companion.conversation_handler import HandlerOutput
from reachy_companion.huggingface_realtime import HuggingFaceRealtimeHandler, _party_names


logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "gpt-realtime-2.1-mini"
ROBOT_RATE = 16000


def realtime_model() -> str:
    """Realtime model id; REALTIME_MODEL overrides for on-robot A/B (D-023)."""
    return (os.getenv("REALTIME_MODEL") or "").strip() or _DEFAULT_MODEL

_RESAMPLER_QUALITY = "HQ"
_EAGERNESS_VALUES = ("low", "medium", "high", "auto")


def _eagerness() -> Literal["low", "medium", "high", "auto"]:
    """Read the semantic-VAD eagerness from the environment, falling back to auto."""
    raw = os.getenv("REALTIME_VAD_EAGERNESS", "auto").strip().lower()
    if raw not in _EAGERNESS_VALUES:
        logger.warning("Ignoring invalid REALTIME_VAD_EAGERNESS=%r; using auto.", raw)
        return "auto"
    return cast(Literal["low", "medium", "high", "auto"], raw)


def _noise_reduction() -> NoiseReduction | None:
    """Read `REALTIME_NOISE_REDUCTION`; far_field is the default for this robot.

    2026-08-24 (multi-person investigation): the session previously configured
    no input noise reduction at all, and the robot is the textbook far-field
    device the API's `far_field` mode exists for — it filters the audio before
    VAD, which is documented to reduce false speech triggers. `off` restores
    the old behavior; `near_field` exists for bench tests with a headset mic.
    """
    raw = os.getenv("REALTIME_NOISE_REDUCTION", "far_field").strip().lower() or "far_field"
    if raw == "off":
        return None
    if raw not in ("far_field", "near_field"):
        logger.warning("Ignoring invalid REALTIME_NOISE_REDUCTION=%r; using far_field.", raw)
        raw = "far_field"
    reduction_type: NoiseReductionType = "near_field" if raw == "near_field" else "far_field"
    return NoiseReduction(type=reduction_type)


_LEGACY_TRANSCRIBE_MODELS = ("gpt-4o-transcribe", "whisper-1")
_DEFAULT_TRANSCRIBE_MODEL = "gpt-transcribe"
_DEFAULT_TRANSCRIBE_PROMPT = "與家用陪伴機器人的台灣中文對話"


def _transcription() -> dict[str, Any]:
    """Input-transcription config; new-model extras only on new models.

    `gpt-transcribe` (Task 4) supports keyword biasing and a free-text prompt
    that the legacy `gpt-4o-transcribe`/`whisper-1` shape does not, so those
    two extras are only attached when the configured model is not one of the
    legacy ones — sending them to a legacy model would be a malformed request.
    Keywords default to the party mode address names (`_party_names()`,
    `huggingface_realtime.py:109`) so a name the robot listens for is also a
    name the transcriber is biased toward hearing correctly.
    """
    model = (os.getenv("REALTIME_TRANSCRIPTION_MODEL") or "").strip() or _DEFAULT_TRANSCRIBE_MODEL
    params: dict[str, Any] = {"model": model, "language": config.REALTIME_TRANSCRIPTION_LANGUAGE}
    if model in _LEGACY_TRANSCRIBE_MODELS:
        return params
    raw_keywords = os.getenv("REALTIME_TRANSCRIPTION_KEYWORDS")
    if raw_keywords is None:
        keywords = list(_party_names())
    else:
        keywords = [k.strip() for k in raw_keywords.split(",") if k.strip()]
    if keywords:
        params["keywords"] = keywords
    prompt = os.getenv("REALTIME_TRANSCRIPTION_PROMPT")
    prompt = _DEFAULT_TRANSCRIBE_PROMPT if prompt is None else prompt.strip()
    if prompt:
        params["prompt"] = prompt
    return params


def _turn_detection(party: bool = False) -> RealtimeAudioInputTurnDetectionParam:
    """Server-side VAD, tunable via env for Chinese mid-sentence pauses (D-003).

    Every knob degrades to its default with a warning rather than raising, so one
    bad line in a robot's `.env` cannot abort the whole realtime session.

    With ``party`` (multi-person hardening, 2026-08-24) the same VAD keeps
    committing and transcribing turns, but the server neither interrupts the
    in-flight reply nor auto-answers: the client's debounced barge-in decides
    what counts as an interruption, and the address gate decides which turns
    deserve a response. Solo mode is byte-identical to the pre-party config.
    """
    vad_type = os.getenv("REALTIME_VAD_TYPE", "server_vad").strip().lower() or "server_vad"
    if vad_type == "semantic_vad":
        semantic = SemanticVad(
            type="semantic_vad",
            eagerness=_eagerness(),
            interrupt_response=not party,
        )
        if party:
            semantic["create_response"] = False
        return semantic
    if vad_type != "server_vad":
        logger.warning("Ignoring invalid REALTIME_VAD_TYPE=%r; using server_vad.", vad_type)
    server = ServerVad(
        type="server_vad",
        interrupt_response=not party,
        threshold=env_float("REALTIME_VAD_THRESHOLD", 0.5, lo=0.0, hi=1.0),
        prefix_padding_ms=env_int("REALTIME_VAD_PREFIX_PADDING_MS", 300, lo=0),
        silence_duration_ms=env_int("REALTIME_VAD_SILENCE_DURATION_MS", 800, lo=0),
    )
    if party:
        server["create_response"] = False
    return server


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
    _voicefx: VoiceFX | None = None
    _clear_queue_callback: Callable[[], None] | None = None

    @property
    def _clear_queue(self) -> Callable[[], None] | None:
        """Return the console's queue flush, wrapped to also reset the output pipeline.

        The base calls this on `input_audio_buffer.speech_started`
        (`huggingface_realtime.py:749-750`), i.e. on barge-in, right after
        `console.py:146` has installed `clear_audio_queue`. Everything still held
        in the voice filter and the output resampler belongs to the utterance
        being interrupted, so it has to be dropped with the queue rather than
        bleeding into the next reply.

        The *microphone* stream is deliberately untouched: it is carrying the
        very audio the user is interrupting with, the frames that triggered
        `speech_started` in the first place.
        """
        callback = self._clear_queue_callback
        if callback is None:
            return None

        def clear_queue_and_reset_output() -> None:
            self._reset_output_pipeline()
            callback()

        return clear_queue_and_reset_output

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

    def _voice_filter(self, src_rate: int) -> VoiceFX:
        """Return the cute-robot filter for `src_rate`, building it on first audio.

        Built lazily for the same reason the resamplers are: the real rate is
        only known once the model has sent something. `VOICEFX_ENABLED` defaults
        to false, and a disabled filter's `process` returns its argument
        unchanged, so the shipped path stays byte-identical to the unfiltered one.
        """
        voicefx = self._voicefx
        if voicefx is None or voicefx.rate != src_rate:
            voicefx = VoiceFX.from_env(src_rate)
            self._voicefx = voicefx
        return voicefx

    def _reset_output_pipeline(self) -> None:
        """Drop everything the assistant's outgoing audio still holds.

        Both stages in emit order: the voice filter's pitch tail and carrier
        phase, then the output resampler's filter tail.
        """
        if self._voicefx is not None:
            self._voicefx.reset()
        if self._output_resampler is not None:
            self._output_resampler.reset()

    def _reset_resamplers(self) -> None:
        """Drop every filter tail in both directions, at session start.

        Unlike barge-in, a fresh session has no in-flight user turn to protect,
        so the microphone stream is reset here too.
        """
        if self._input_resampler is not None:
            self._input_resampler.reset()
        self._reset_output_pipeline()

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
        self._realtime_connect_query = {"model": realtime_model()}
        api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY must be set to use the OpenAI realtime backend")
        return AsyncOpenAI(api_key=api_key)

    def _get_session_config(self, tool_specs: list[ToolSpec]) -> RealtimeSessionCreateRequestParam:
        """Return the Hugging Face session config retargeted at gpt-realtime-2.1."""
        cfg = super()._get_session_config(tool_specs)
        cfg["model"] = realtime_model()
        cfg["audio"]["output"]["format"] = AudioPCM(type="audio/pcm", rate=24000)
        cfg["audio"]["input"]["format"] = AudioPCM(type="audio/pcm", rate=24000)
        # getattr: config emission must also work on partially-built handlers
        # (tests construct via __new__), where party state defaults to solo.
        cfg["audio"]["input"]["turn_detection"] = _turn_detection(getattr(self, "_party_mode", False))
        noise_reduction = _noise_reduction()
        if noise_reduction is not None:
            cfg["audio"]["input"]["noise_reduction"] = noise_reduction
        # The SDK's AudioTranscriptionParam TypedDict predates `keywords`
        # (openai.types.realtime.audio_transcription_param), same precedent as
        # `_native_rate_audio_pcm()` above (huggingface_realtime.py:397).
        cfg["audio"]["input"]["transcription"] = cast(Any, _transcription())
        return cfg

    def _session_config_fallback(
        self, cfg: RealtimeSessionCreateRequestParam
    ) -> RealtimeSessionCreateRequestParam | None:
        """Retry once with legacy `gpt-4o-transcribe` if the upgraded shape is rejected.

        Returns None (no retry) when the config already used a legacy model —
        a legacy config being rejected is a real failure, not something this
        fallback can fix.
        """
        current_model = cfg["audio"]["input"]["transcription"].get("model")
        if current_model in _LEGACY_TRANSCRIBE_MODELS:
            return None
        fallback = copy.deepcopy(cfg)
        fallback["audio"]["input"]["transcription"] = cast(
            Any,
            {"model": "gpt-4o-transcribe", "language": config.REALTIME_TRANSCRIPTION_LANGUAGE},
        )
        return fallback

    async def _push_turn_detection_update(self) -> None:
        """Apply the current mode's turn detection to the live session.

        Codex round 1, finding 2: this must be a NARROW update — never `model`
        (immutable) or `voice` (rejected once audio has been produced). The
        whole `audio.input` block is sent rather than `turn_detection` alone so
        a server treating the nested object as a replacement cannot strip the
        format, transcription or noise-reduction settings.
        """
        if not self.connection:
            return
        audio_input = self._get_session_config(tool_specs=[])["audio"]["input"]
        try:
            await self.connection.session.update(
                session={"type": "realtime", "audio": {"input": audio_input}}
            )
            logger.info("session turn_detection updated: party=%s", self._party_mode)
        except Exception as exc:  # noqa: BLE001 - a failed update must not kill the receive loop
            logger.warning("Failed to update session turn_detection: %s", exc)

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
        """Emit the next output, filtered and downsampled to the robot's rate.

        `console.py:905-924` discards the rate label and pushes straight into the
        SDK's fixed 16 kHz sink, so 24 kHz assistant PCM has to be converted here
        — the last point we own before `play_loop` sees it — rather than in the
        console. Text outputs pass through untouched.

        The voice filter (D-010) runs *before* the downsample, on the model's
        24 kHz PCM: its pitch stage is a rate trick that assumes the model rate,
        and the colour stages want the full 24 kHz band. Its -1 dBFS ceiling
        (D-017) is also what keeps the downsample below its own clip point,
        since band-limited reconstruction overshoots a signal pushed to 0 dBFS.
        Disabled — the shipped default — it returns the same array object,
        leaving the chain exactly as it was before the filter existed.
        """
        handler_output = await super().emit()
        if not isinstance(handler_output, tuple):
            return handler_output

        rate, pcm = handler_output
        if rate == ROBOT_RATE:
            return handler_output

        filtered = self._voice_filter(rate).process(audio_to_int16(pcm))
        return ROBOT_RATE, self._speaker_resampler(rate).process(filtered)
