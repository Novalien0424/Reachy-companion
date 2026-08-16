"""OpenAI gpt-realtime-2.1 backend (D-002).

Subclasses the maintained Hugging Face handler, replacing client build, session
config, sample rate, connect(model=), and adding 16k<->24k resampling at the two
audio boundaries. Everything else — the realtime event loop, tool plumbing,
reconnect policy — is inherited verbatim.
"""

import os
import base64
import logging
from typing import Tuple, cast

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

from reachy_companion.config import config
from reachy_companion.streaming import audio_to_int16, audio_to_float32
from reachy_companion.audio.resample import resample_pcm
from reachy_companion.tools.core_tools import ToolSpec
from reachy_companion.conversation_handler import HandlerOutput
from reachy_companion.huggingface_realtime import HuggingFaceRealtimeHandler


logger = logging.getLogger(__name__)

MODEL = "gpt-realtime-2.1"
ROBOT_RATE = 16000

_EAGERNESS_VALUES = ("low", "medium", "high", "auto")


def _eagerness() -> Literal["low", "medium", "high", "auto"]:
    """Read the semantic-VAD eagerness from the environment, falling back to auto."""
    raw = os.getenv("REALTIME_VAD_EAGERNESS", "auto").strip().lower()
    if raw not in _EAGERNESS_VALUES:
        logger.warning("Ignoring invalid REALTIME_VAD_EAGERNESS=%r; using auto.", raw)
        return "auto"
    return cast(Literal["low", "medium", "high", "auto"], raw)


def _turn_detection() -> RealtimeAudioInputTurnDetectionParam:
    """Server-side VAD, tunable via env for Chinese mid-sentence pauses (D-003)."""
    if os.getenv("REALTIME_VAD_TYPE", "server_vad").strip().lower() == "semantic_vad":
        return SemanticVad(
            type="semantic_vad",
            eagerness=_eagerness(),
            interrupt_response=True,
        )
    return ServerVad(
        type="server_vad",
        interrupt_response=True,
        threshold=float(os.getenv("REALTIME_VAD_THRESHOLD", "0.5")),
        prefix_padding_ms=int(os.getenv("REALTIME_VAD_PREFIX_PADDING_MS", "300")),
        silence_duration_ms=int(os.getenv("REALTIME_VAD_SILENCE_DURATION_MS", "800")),
    )


class OpenAIRealtimeHandler(HuggingFaceRealtimeHandler):
    """Realtime handler for the direct OpenAI Realtime API at 24 kHz."""

    SAMPLE_RATE = 24000

    async def _build_realtime_client(self) -> AsyncOpenAI:
        """Build the OpenAI realtime client and pin the model for the connect call.

        The base class builds `connect_kwargs` inline inside its 247-line
        `_run_realtime_session` (`huggingface_realtime.py:705-708`), whose only
        seam is `self._realtime_connect_query` -> `extra_query`. That is the same
        destination as `connect(model=...)`: the SDK merges `model` and
        `extra_query` into one websocket query-param dict
        (`openai/resources/realtime/realtime.py:699-704`), so the URL is
        identical to the recovered handler's
        `connect_kwargs["model"] = ...` (`base_realtime_5b8d974.py:714-716`).
        """
        self._realtime_connect_query = {"model": MODEL}
        # Fail fast rather than connecting anonymously and failing mid-session.
        return AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])

    def _get_session_config(self, tool_specs: list[ToolSpec]) -> RealtimeSessionCreateRequestParam:
        """Return the Hugging Face session config retargeted at gpt-realtime-2.1."""
        cfg = super()._get_session_config(tool_specs)
        cfg["model"] = MODEL
        cfg["audio"]["output"]["format"] = AudioPCM(type="audio/pcm", rate=24000)
        cfg["audio"]["input"]["format"] = AudioPCM(type="audio/pcm", rate=24000)
        cfg["audio"]["input"]["turn_detection"] = _turn_detection()
        cfg["audio"]["input"]["transcription"]["language"] = config.REALTIME_TRANSCRIPTION_LANGUAGE
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

        # Resample if needed. resample_pcm needs float32; casting first also keeps
        # the identity path (src == dst) from handing us a float64 array that
        # audio_to_int16 would reject.
        if input_sample_rate != self.SAMPLE_RATE:
            resampled = resample_pcm(audio_to_float32(audio_frame), input_sample_rate, self.SAMPLE_RATE)
            outgoing = audio_to_int16(resampled)
        else:
            outgoing = audio_to_int16(audio_frame)

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

        # resample_pcm returns its argument unchanged on the identity path, so
        # never mutate the result in place.
        return ROBOT_RATE, audio_to_int16(resample_pcm(audio_to_float32(pcm), rate, ROBOT_RATE))
