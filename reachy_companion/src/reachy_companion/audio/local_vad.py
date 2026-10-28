"""Local Silero voice detection for the listening reaction (2026-10-28 local-VAD plan).

`SileroVAD` is upstream's `audio/local_vad.py` (8b37f31, #569, as of b731ea1),
kept verbatim apart from the asset-cache namespace, so later upstream fixes
port as plain diffs. Its barge-in candidate is upstream's local ducking, which
we do not port (D-023/D-028/D-032 own interruption); `process` is only ever
called with `detect_barge_in=False` here.

`LocalSpeechFeed` is ours: upstream runs the detector inline in the handler's
`receive` on the event loop, while this app runs it on the microphone reader
thread (`console.LocalStream.record_loop`) and hands only the speech edges to
the loop.

`onnxruntime` is imported at module level, as upstream does. It is not a
dependency of this package: reachy-mini 1.13.0 pins `onnxruntime==1.27.0`. The
console imports this module lazily and treats any import failure as "local VAD
off", so a robot without it still converses normally.
"""

import time
import hashlib
import logging
from typing import Any
from pathlib import Path
from tempfile import NamedTemporaryFile
from collections.abc import Callable

import httpx
import numpy as np
import onnxruntime as ort
from numpy.typing import NDArray
from huggingface_hub import cached_assets_path

from reachy_companion.streaming import audio_to_int16


logger = logging.getLogger(__name__)
_MODEL_REVISION = "7e30209a3e901f9842f81b225f3e93d8199902b1"
_MODEL_FILENAME = "silero_vad_16k_op15.onnx"
_MODEL_FILES = {
    _MODEL_FILENAME: (
        "src/silero_vad/data/silero_vad_16k_op15.onnx",
        "7ed98ddbad84ccac4cd0aeb3099049280713df825c610a8ed34543318f1b2c49",
    ),
    "LICENSE": ("LICENSE", "2e63e9a38b6e8fc0c7bc37ce174caca1862870856c6daf5697cfb785e925520b"),
}


class SileroVAD:
    """Detect speech in streaming 16 kHz mono PCM for listening reactions and barge-in ducking."""

    SAMPLE_RATE = 16000
    CHUNK_SAMPLES = 512
    MIN_SPEECH_SAMPLES = 2560  # 160 ms, ignores knocks and clicks
    SILENCE_SAMPLES = 6400  # 400 ms
    IDLE_RESET_SAMPLES = 8000  # 500 ms
    BARGE_IN_SILENCE_SAMPLES = 1024  # 64 ms

    def __init__(self) -> None:
        """Download or reuse the cached Silero model and load it with a single CPU thread."""
        model_directory = cached_assets_path("reachy_companion", "silero-vad", _MODEL_REVISION)
        with httpx.Client(follow_redirects=True, timeout=30.0) as client:
            for filename, (upstream_path, checksum) in _MODEL_FILES.items():
                cached_file = model_directory / filename
                if cached_file.is_file():
                    if hashlib.sha256(cached_file.read_bytes()).hexdigest() == checksum:
                        continue
                    logger.warning("Cached Silero file failed checksum verification: %s", cached_file)
                logger.info("Downloading Silero VAD file: %s", filename)
                response = client.get(
                    f"https://raw.githubusercontent.com/snakers4/silero-vad/{_MODEL_REVISION}/{upstream_path}"
                )
                response.raise_for_status()
                if hashlib.sha256(response.content).hexdigest() != checksum:
                    raise ValueError(f"Downloaded Silero file failed checksum verification: {filename}")
                download = NamedTemporaryFile(dir=model_directory, delete=False)
                download_path = Path(download.name)
                try:
                    with download:
                        download.write(response.content)
                    download_path.replace(cached_file)
                finally:
                    download_path.unlink(missing_ok=True)
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        model_path = model_directory / _MODEL_FILENAME
        self._session = ort.InferenceSession(str(model_path), sess_options=options, providers=["CPUExecutionProvider"])
        self.reset()

    def reset(self) -> None:
        """Clear speech state and buffered audio between microphone streams."""
        self._reset_model()
        self._pending = np.empty(0, dtype=np.float32)
        self._speaking = False
        self.speech_probability = 0.0
        self._silence_samples = 0
        self.barge_in_candidate = False
        self._barge_in_silence_samples = 0

    def _reset_model(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, 64), dtype=np.float32)
        self._idle_samples = 0
        self._speech_samples = 0

    def process(self, audio: NDArray[np.int16], sample_rate: int, *, detect_barge_in: bool = False) -> bool:
        """Return whether speech is active, holding through 400 ms of silence."""
        if sample_rate != self.SAMPLE_RATE:
            raise ValueError("Local VAD requires 16 kHz microphone audio")
        if not detect_barge_in:
            self.barge_in_candidate = False
            self._barge_in_silence_samples = 0
        self._pending = np.concatenate((self._pending, audio.reshape(-1).astype(np.float32) / 32768.0))
        offset = 0
        while self._pending.size - offset >= self.CHUNK_SAMPLES:
            chunk = self._pending[offset : offset + self.CHUNK_SAMPLES].reshape(1, -1)
            model_input = np.concatenate((self._context, chunk), axis=1)
            probability, state = self._session.run(
                None,
                {"input": model_input, "state": self._state, "sr": np.array(self.SAMPLE_RATE, dtype=np.int64)},
            )
            self._state = np.asarray(state, dtype=np.float32)
            self._context = chunk[:, -64:].copy()
            speech_probability = float(np.asarray(probability).item())
            self.speech_probability = speech_probability
            if detect_barge_in:
                if speech_probability >= 0.6 or (self.barge_in_candidate and speech_probability >= 0.45):
                    self.barge_in_candidate = True
                    self._barge_in_silence_samples = 0
                elif self.barge_in_candidate:
                    self._barge_in_silence_samples += self.CHUNK_SAMPLES
                    if self._barge_in_silence_samples >= self.BARGE_IN_SILENCE_SAMPLES:
                        self.barge_in_candidate = False
                        self._barge_in_silence_samples = 0
            if speech_probability >= 0.5:
                self._idle_samples = 0
                self._silence_samples = 0
                self._speech_samples += self.CHUNK_SAMPLES
                if self._speech_samples >= self.MIN_SPEECH_SAMPLES:
                    self._speaking = True
            elif self._speaking:
                self._silence_samples += self.CHUNK_SAMPLES
                if self._silence_samples >= self.SILENCE_SAMPLES:
                    self._speaking = False
                    self._silence_samples = 0
                    self._reset_model()
            else:
                self._speech_samples = 0
                self._idle_samples += self.CHUNK_SAMPLES
                if self._idle_samples >= self.IDLE_RESET_SAMPLES:
                    self._reset_model()
            offset += self.CHUNK_SAMPLES
        self._pending = self._pending[offset:].copy()
        return self._speaking


# --- ours: the microphone reader thread's side --------------------------------


class LocalSpeechFeed:
    """Feed SDK microphone frames to `SileroVAD` and report speech edges.

    Runs on the microphone reader thread, never the event loop. A frame is
    reduced exactly as the realtime handler's `receive` reduces it for the
    server (first channel, int16), so both detectors judge the same signal.
    Every method returns the new speech state when it changed and None when it
    did not, so the caller hands the loop edges only.
    """

    # Upstream's 500 ms: a microphone gap this long drops the detector's history
    # and any speech vote it held (upstream resets on the next frame and runs a
    # 500 ms loop timer; here the reader thread checks on its idle reads).
    GAP_RESET_S = 0.5

    def __init__(self, detector: SileroVAD, clock: Callable[[], float] = time.monotonic) -> None:
        """Wrap a loaded detector; `clock` is injectable for tests."""
        self._detector = detector
        self._clock = clock
        self._speaking = False
        self._fed_at: float | None = None  # None: nothing fed since the last reset

    @property
    def speaking(self) -> bool:
        """Whether the last reported state was speech."""
        return self._speaking

    def feed(self, frame: NDArray[Any], sample_rate: int) -> bool | None:
        """Run one microphone frame through the detector."""
        before = self._speaking
        now = self._clock()
        if self._fed_at is not None and now - self._fed_at > self.GAP_RESET_S:
            self.silence()
        self._fed_at = now
        if frame.size:
            if frame.ndim == 2:
                if frame.shape[1] > frame.shape[0]:  # channels-first
                    frame = frame.T
                if frame.shape[1] > 1:
                    frame = frame[:, 0]
            self._speaking = self._detector.process(audio_to_int16(frame), sample_rate)
        return self._speaking if self._speaking != before else None

    def silence(self) -> bool | None:
        """Hear nothing (muted, warming up, mic gone): drop history and end any speech."""
        if self._fed_at is None and not self._speaking:
            return None
        self._fed_at = None
        self._detector.reset()
        was_speaking, self._speaking = self._speaking, False
        return False if was_speaking else None

    def idle(self) -> bool | None:
        """Note a read that returned no frame; past `GAP_RESET_S` without audio, the mic is silent."""
        if self._fed_at is not None and self._clock() - self._fed_at > self.GAP_RESET_S:
            return self.silence()
        return None
