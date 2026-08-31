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
import uuid
import base64
import asyncio
import logging
from typing import Any, Final, Tuple, cast
from collections.abc import Callable

import soxr
import numpy as np
from openai import AsyncOpenAI
from numpy.typing import NDArray
from typing_extensions import Literal
from openai.types.realtime import RealtimeAudioConfigParam, RealtimeSessionCreateRequestParam
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
from reachy_companion.tools.core_tools import ToolSpec, get_tool_specs
from reachy_companion.conversation_handler import HandlerOutput
from reachy_companion.huggingface_realtime import (
    HuggingFaceRealtimeHandler,
    InputTranscriptChunksByItem,
    _party_names,
    _solo_client_barge,
    _vad_silence_duration_ms,
    to_realtime_tools_config,
    warn_if_barge_confirm_races_vad,
)


logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "gpt-realtime-2.1-mini"
ROBOT_RATE = 16000

# How long an ordered session update waits for `session.updated` (or a matching
# `error`) before giving up. The tool call that triggered it is holding a turn
# open, so this has to be short enough not to feel like a hang and long enough
# to cover a normal round trip.
_SESSION_UPDATE_ACK_TIMEOUT_S: Final[float] = 5.0


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


_REASONING_EFFORTS = ("minimal", "low", "medium", "high", "xhigh")


def _reasoning_effort() -> str | None:
    """Session `reasoning.effort`; `low` is the documented voice-agent default.

    gpt-realtime-2.x reasons before speaking; unpinned, a future server-side
    default change silently adds pre-speech latency. `off` omits the field
    entirely (server default). Research doc §1.
    """
    raw = (os.getenv("REALTIME_REASONING_EFFORT") or "low").strip().lower()
    if raw == "off":
        return None
    if raw not in _REASONING_EFFORTS:
        logger.warning("Ignoring invalid REALTIME_REASONING_EFFORT=%r; using low.", raw)
        return "low"
    return raw


_MAX_OUTPUT_TOKENS_DEFAULT = 900


def _max_output_tokens() -> int | None:
    """Per-reply token ceiling — a runaway-monologue rail, not a brevity knob.

    ~20-25 output tokens per spoken second, so 900 ≈ 40 s of speech. Hitting
    it cuts the reply MID-WORD with no wrap-up (`response.done` status
    `incomplete`/`max_output_tokens` — research doc §3), which is why the
    default is loose and the trip is logged as a warning. Brevity itself is
    the prompt's job (persona + hardening block).

    Only the three disable sentinels are handled here; every other value goes
    through `env_int`, which is what makes a malformed knob warn and fall back
    and an out-of-range one warn and clamp. Clamping silently was the bug (fix
    round, finding 1): `-5` is a one-token, effectively mute robot, and that is
    precisely the misconfiguration the "every knob degrades with a warning"
    rule exists to put in the journal.
    """
    raw = (os.getenv("REALTIME_MAX_OUTPUT_TOKENS") or "").strip().lower()
    if raw in ("inf", "off", "0"):
        return None
    return env_int("REALTIME_MAX_OUTPUT_TOKENS", _MAX_OUTPUT_TOKENS_DEFAULT, lo=1, hi=4096)


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
# How long the streaming transcriber may buffer before emitting a partial. Left
# unset by default (the server's own default stands); the knob exists for the
# `gpt-live-transcribe` A/B, where a shorter delay is what makes the name
# reach `_maybe_commit_on_partial` before `transcription.completed` does.
_TRANSCRIBE_DELAY_VALUES = ("minimal", "low", "medium", "high", "xhigh")


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
    delay = (os.getenv("REALTIME_TRANSCRIPTION_DELAY") or "").strip().lower()
    if delay:
        if delay in _TRANSCRIBE_DELAY_VALUES:
            params["delay"] = delay
        else:
            logger.warning("Ignoring invalid REALTIME_TRANSCRIPTION_DELAY=%r", delay)
    return params


def _turn_detection(party: bool = False) -> RealtimeAudioInputTurnDetectionParam:
    """Server-side VAD, tunable via env for Chinese mid-sentence pauses (D-003).

    Every knob degrades to its default with a warning rather than raising, so one
    bad line in a robot's `.env` cannot abort the whole realtime session.

    With ``party`` (multi-person hardening, 2026-08-24) the same VAD keeps
    committing and transcribing turns, but the server neither interrupts the
    in-flight reply nor auto-answers: the client's debounced barge-in decides
    what counts as an interruption, and the address gate decides which turns
    deserve a response.

    Since 2026-08-31 `create_response` is **false in every mode**. The server
    still commits and transcribes turns; it never answers one. The client
    answers exactly the turns its per-mode answer gate accepts, through
    `_safe_response_create()`. That is what makes a rolled-back turn produce no
    answer at all — with the server auto-answering, every gated turn still got a
    full spoken reply queued behind the resumed audio, which is the pile-up the
    operator saw as "five tries to get a reply".
    `REALTIME_SOLO_CLIENT_BARGE=0` restores server-side INTERRUPTION only.
    """
    server_interrupts = not party and not _solo_client_barge()
    warn_if_barge_confirm_races_vad()
    vad_type = os.getenv("REALTIME_VAD_TYPE", "server_vad").strip().lower() or "server_vad"
    if vad_type == "semantic_vad":
        semantic = SemanticVad(
            type="semantic_vad",
            eagerness=_eagerness(),
            interrupt_response=server_interrupts,
        )
        semantic["create_response"] = False
        return semantic
    if vad_type != "server_vad":
        logger.warning("Ignoring invalid REALTIME_VAD_TYPE=%r; using server_vad.", vad_type)
    server = ServerVad(
        type="server_vad",
        interrupt_response=server_interrupts,
        threshold=env_float("REALTIME_VAD_THRESHOLD", 0.5, lo=0.0, hi=1.0),
        prefix_padding_ms=env_int("REALTIME_VAD_PREFIX_PADDING_MS", 300, lo=0),
        # Shared with the barge-in confirm window, which must outlast it.
        silence_duration_ms=_vad_silence_duration_ms(),
    )
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
    _onset_ramp_remaining: int = 0

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
        if getattr(self, "_boot_gate_active", False) and not getattr(self, "_startup_greeting_sent", True):
            # Boot gate (Task 6): this handler's first session comes up deaf.
            # The greeting is about to play out of a speaker sitting next to the
            # microphone, so anything committed before it drains is the robot
            # hearing itself. `null` is the SDK's documented "no turn detection"
            # (RealtimeAudioInputTurnDetectionParam is Optional).
            # The condition lives HERE rather than at the call site so it holds
            # wherever the config is built — including the legacy-transcription
            # retry and `_push_turn_detection_update` (Codex round 1, finding 1).
            cfg["audio"]["input"]["turn_detection"] = None
        else:
            cfg["audio"]["input"]["turn_detection"] = _turn_detection(getattr(self, "_party_mode", False))
        noise_reduction = _noise_reduction()
        if noise_reduction is not None:
            cfg["audio"]["input"]["noise_reduction"] = noise_reduction
        # The SDK's AudioTranscriptionParam TypedDict predates `keywords`
        # (openai.types.realtime.audio_transcription_param), same precedent as
        # `_native_rate_audio_pcm()` above (huggingface_realtime.py:397).
        cfg["audio"]["input"]["transcription"] = cast(Any, _transcription())
        effort = _reasoning_effort()
        if effort is not None:
            # The installed 2.28.0 TypedDict predates `reasoning` (verified:
            # RealtimeSessionCreateRequestParam has no such key), but the field
            # is documented GA for gpt-realtime-2.x and TypedDicts are plain
            # dicts at runtime — same precedent as `keywords` and the 16 kHz
            # format. Codex round 1, finding 1.
            cast(dict[str, Any], cfg)["reasoning"] = {"effort": effort}
        tokens = _max_output_tokens()
        if tokens is not None:
            cfg["max_output_tokens"] = tokens
        return cfg

    def _session_config_fallback(
        self, cfg: RealtimeSessionCreateRequestParam
    ) -> RealtimeSessionCreateRequestParam | None:
        """Retry once with legacy `gpt-4o-transcribe` if the upgraded shape is rejected.

        Returns None (no retry) when the config already used a legacy model —
        a legacy config being rejected is a real failure, not something this
        fallback can fix.

        The retry also drops `reasoning`, because a rejection tells us nothing
        about *which* field the server refused and that one is the other field
        the installed SDK stub does not know about: it rides a runtime-dict
        cast, so if it is what was rejected, retrying with it still attached
        would fail again and leave the robot mute at boot (Codex round 1,
        finding 1). Losing the effort pin on a degraded session costs latency,
        not speech. `max_output_tokens` needs no such treatment — it IS in the
        installed stub.
        """
        current_model = cfg["audio"]["input"]["transcription"].get("model")
        if current_model in _LEGACY_TRANSCRIBE_MODELS:
            return None
        fallback = copy.deepcopy(cfg)
        fallback["audio"]["input"]["transcription"] = cast(
            Any,
            {"model": "gpt-4o-transcribe", "language": config.REALTIME_TRANSCRIPTION_LANGUAGE},
        )
        # The TypedDict has no `reasoning` key for mypy (Codex round 2, finding 8).
        cast(dict[str, Any], fallback).pop("reasoning", None)
        return fallback

    def _record_partial_transcript_delta(
        self,
        input_transcript: InputTranscriptChunksByItem,
        item_id: str,
        delta: str,
    ) -> None:
        """GA transcription deltas are incremental chunks, not snapshots — append.

        The base implementation (`huggingface_realtime.py:1322-1331`) stores
        `deltas = [delta]`, which is right for the HF-compatible server: each
        delta there is the whole partial so far. OpenAI's GA realtime API sends
        the *new* text only, so replacing would leave the accumulated partial at
        the latest fragment — a name split across deltas (`瑞` + `奇`) would
        never match the barge gate, and the debounced UI partial would show one
        stray syllable (Codex round 1, finding 3).
        """
        if input_transcript.item_id == item_id:
            input_transcript.deltas.append(delta)
        else:
            input_transcript.item_id = item_id
            input_transcript.deltas = [delta]

    async def _apply_session_update(
        self,
        build_session: Callable[[], RealtimeSessionCreateRequestParam | None],
        *,
        what: str,
    ) -> bool:
        """Build, send and confirm one session update, all under one lock.

        The single ordered, single-flight update mechanism (design decision 9;
        Codex round 1 P1-1/P1-3/P1-4/P2-9, tightened in round 2 2a-1/2a-2).

        Two properties, and both need the lock to be held across the WHOLE
        operation — which is why this takes a BUILDER rather than a payload:

        * **Ordering.** `build_session()` runs here, inside the lock, so the
          snapshot it takes cannot go stale between being built and being sent.
          An earlier design released the lock between the two and a newer flip
          could overtake the older one on the wire (round 2, 2a-1).
        * **Single flight.** `session.updated` does not echo the client
          `event_id`, so "resolve the update in flight" is only sound while
          exactly one can be in flight. Every live-session caller —
          `_push_mode_update`, `_push_turn_detection_update`, `change_voice`,
          `apply_personality` — comes through here for that reason. The one
          exemption is the initial `session.update` in `_run_realtime_session`,
          which runs before the receive loop exists and therefore before any
          waiter can be installed.

        The `event_id` is still stamped, because an `error` names the event it
        rejected and that is how a rejection is told apart from an unrelated
        server error.

        `build_session()` returning None means the caller was superseded while
        it queued: nothing is sent and the call reports success, because the
        newer update is the one that should land.

        **No ack to wait for before the receive loop runs** (Codex round 3,
        finding 1). The no-greeting startup path releases the boot gate — and so
        pushes turn detection — from `_send_startup_greeting_prompt`, which runs
        before `async for event in self.connection`. Waiting there would burn the
        full five seconds and log a failure for an update that was fine. So when
        the loop is not yet active the update is sent and reported applied, and
        the acknowledgement it will eventually produce is recorded as debt.
        """
        if not self.connection:
            return False
        async with self._session_update_lock:
            session = build_session()
            if session is None:
                return True
            event_id = f"appupd_{uuid.uuid4().hex}"
            waiting = self._receive_loop_active
            waiter: asyncio.Future[bool] | None = None
            if waiting:
                loop = asyncio.get_running_loop()
                waiter = loop.create_future()
                self._session_update_event_id = event_id
                self._session_update_waiter = waiter
            try:
                await self.connection.session.update(session=session, event_id=event_id)
            except Exception as exc:  # noqa: BLE001 - a failed update must not kill the caller
                logger.warning("Failed to send the %s session update: %s", what, exc)
                self._session_update_event_id = None
                self._session_update_waiter = None
                return False
            if waiter is None:
                # Sent before the receive loop could observe an acknowledgement.
                # It will arrive once the loop starts, with nobody waiting on
                # it, so it is booked as debt rather than allowed to resolve
                # whichever waiter happens to exist by then.
                self._session_update_ack_debt += 1
                logger.info("session updated (%s, sent before the receive loop)", what)
                return True
            try:
                applied = await asyncio.wait_for(waiter, timeout=_SESSION_UPDATE_ACK_TIMEOUT_S)
            except asyncio.TimeoutError:
                # The acknowledgement is late, not absent: it will still arrive,
                # and if it were allowed to resolve the NEXT update's waiter that
                # update would be told it had been applied on the strength of
                # this one's ack (Codex round 3, finding 6). One unit of debt
                # makes the next `session.updated` pay for this update instead.
                self._session_update_ack_debt += 1
                logger.warning(
                    "The %s session update was never acknowledged within %.1fs; "
                    "the server may still be running the previous session shape",
                    what,
                    _SESSION_UPDATE_ACK_TIMEOUT_S,
                )
                return False
            finally:
                # Cleared on every exit, cancellation included — and safely,
                # because the lock is still held, so nothing newer can have been
                # installed. The boot-gate release waits here and IS cancelled
                # at session teardown; a waiter left behind would be resolved by
                # whatever `session.updated` the next session produced first.
                # (The acknowledged path has already cleared both; this is
                # idempotent.)
                self._session_update_event_id = None
                self._session_update_waiter = None
            if applied:
                logger.info("session updated (%s)", what)
            return applied

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
        if getattr(self, "_boot_gate_active", False):
            # Boot gate (Task 6): the gate owns turn detection until it opens.
            # By the time it is holding the greeting, `_startup_greeting_sent` is
            # already True, so the config builder alone would emit normal VAD —
            # a party-mode flip mid-greeting would defeat the gate. Nothing is
            # lost by waiting: `_finish_boot_gate` clears the flag before it
            # calls this, so the release rebuilds and sends the current mode.
            logger.debug("boot gate is closed; deferring the turn-detection push to its release")
            return

        def _build() -> RealtimeSessionCreateRequestParam | None:
            audio_input = self._get_session_config(tool_specs=[])["audio"]["input"]
            return {"type": "realtime", "audio": RealtimeAudioConfigParam(input=audio_input)}

        await self._apply_session_update(_build, what="turn detection")

    async def _push_mode_update(self) -> bool:
        """Apply the current conversation mode to the live session.

        One narrow update carrying the three things a mode owns: its rules block
        (`instructions`), its tool surface (`tools`) and its turn detection
        (`audio.input`). Narrow for the reason `_push_turn_detection_update`
        is — never `model` (immutable) or `voice` (rejected once audio has been
        produced) — and the whole `audio.input` block is sent rather than
        `turn_detection` alone so a server treating the nested object as a
        replacement cannot strip the format, transcription or noise-reduction
        settings.

        While the boot gate is closed the turn-detection half is left out
        entirely: the gate owns turn detection until it opens, and
        `_finish_boot_gate` rebuilds and sends the current mode's VAD on
        release. The instructions and tools still go now — they are what the
        model needs before it speaks.

        Coalescing (Codex round 1, P1-4): each call takes a ticket from
        `_mode_update_seq` before queueing on the update lock, and the builder —
        which `_apply_session_update` runs INSIDE that lock (round 2, 2a-1) —
        drops itself if a newer call took a ticket while this one waited. The
        payload is built from live state in the same locked region that sends
        it, so an older snapshot can never land on top of a newer one.
        """
        if not self.connection:
            return False
        self._mode_update_seq += 1
        ticket = self._mode_update_seq
        mode = self._current_mode()

        def _build() -> RealtimeSessionCreateRequestParam | None:
            if ticket != self._mode_update_seq:
                logger.debug("mode update %d superseded by %d; dropping", ticket, self._mode_update_seq)
                return None
            # `mode` captured above is still correct here: a flip since then
            # would have taken a newer ticket and the guard above would have
            # returned None.
            tool_specs = get_tool_specs(exclusion_list=self._mode_tool_exclusions())
            session: RealtimeSessionCreateRequestParam = {
                "type": "realtime",
                "instructions": self._mode_instructions(),
                "tools": to_realtime_tools_config(tool_specs),
            }
            if getattr(self, "_boot_gate_active", False):
                logger.debug("boot gate is closed; deferring the mode update's turn detection to its release")
            else:
                audio_input = self._get_session_config(tool_specs=[])["audio"]["input"]
                session["audio"] = RealtimeAudioConfigParam(input=audio_input)
            logger.info(
                "Tools in session (%s): %s",
                mode.value,
                [spec["name"] for spec in tool_specs],
            )
            return session

        return await self._apply_session_update(_build, what=f"conversation mode {mode.value}")

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

    def _onset_ramp_samples(self) -> int:
        """Return how many samples the onset ramp covers, at `REALTIME_ONSET_RAMP_MS`.

        Measured in `self.SAMPLE_RATE` (the model's 24 kHz), independent of
        which emit() branch ends up applying it, so both branches ramp the
        same sample count. `0` disables the ramp entirely.
        """
        return int(self.SAMPLE_RATE * env_int("REALTIME_ONSET_RAMP_MS", 120, lo=0) / 1000)

    def _notify_response_started(self) -> None:
        """Arm the onset ramp for the reply that is about to start (Task 5).

        Ramping the first `REALTIME_ONSET_RAMP_MS` from silence gives the
        robot's hardware echo canceller time to converge before full amplitude,
        instead of slamming it with a step onset. Idempotent/re-armable: each
        call (including Task 8's rollback-resume) resets the ramp to full
        length rather than accumulating.
        """
        self._onset_ramp_remaining = self._onset_ramp_samples()

    def _apply_onset_ramp(self, pcm: NDArray[np.int16]) -> NDArray[np.int16]:
        """Scale the leading samples of `pcm` by a linear 0->1 ramp.

        The ramp continues across chunk boundaries: `_onset_ramp_remaining`
        tracks how much of the ramp is still owed, so a chunk shorter than the
        remaining ramp only consumes part of it, and the next chunk picks up
        where this one left off. Once the ramp is spent (or was never armed —
        the class-level default is 0), this is a no-op that returns `pcm`
        itself, unchanged and uncopied.
        """
        remaining = getattr(self, "_onset_ramp_remaining", 0)
        if remaining <= 0 or pcm.size == 0:
            return pcm
        total = self._onset_ramp_samples()
        n = min(remaining, pcm.size)
        start = total - remaining
        ramp = (np.arange(start, start + n, dtype=np.float32) + 1.0) / float(total)
        flat = pcm.reshape(-1).astype(np.float32)
        flat[:n] *= ramp
        self._onset_ramp_remaining = remaining - n
        return np.round(flat).astype(np.int16).reshape(pcm.shape)

    async def emit(self) -> HandlerOutput:
        """Emit the next output, filtered and downsampled to the robot's rate.

        `console.py:905-924` discards the rate label and pushes straight into the
        SDK's fixed 16 kHz sink, so 24 kHz assistant PCM has to be converted here
        — the last point we own before `play_loop` sees it — rather than in the
        console. Text outputs pass through untouched.

        The onset ramp (Task 5) runs first, on the raw int16 PCM, in both
        branches below — before the voice filter and the resample — so the
        faded-in amplitude is what actually reaches the speaker rather than
        being partly undone by makeup gain or interpolation.

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
        chunk_i16 = audio_to_int16(pcm)
        if rate == ROBOT_RATE:
            return rate, self._apply_onset_ramp(chunk_i16)

        filtered = self._voice_filter(rate).process(self._apply_onset_ramp(chunk_i16))
        return ROBOT_RATE, self._speaker_resampler(rate).process(filtered)
