"""Cute-robot voice filter for the assistant's outgoing audio (D-010).

Engine-free by decision: both external pitch engines were rejected in review
(python-stretch resets its state per call; pedalboard primes for a full second;
neither ships aarch64 wheels). So the pitch shift is the classic resample-rate
trick through the already-shipped stateful `soxr` — the model's 24 kHz PCM is
handed to a stream that has been *told* its input is `24000 * 2**(st/12)` Hz, so
soxr plays N input samples in N/ratio output samples. That shortens the speech
as it raises it, which is exactly the chipmunk effect the "very cute robot"
brief asked for; the locked profile compensates with a "语速放慢" style line.

Three stages, in order:

1. **Pitch** — the resample-rate trick above. `semitones == 0` is a *hard*
   bypass: the stream is never constructed, so there is no allocation, no
   latency and nothing to reset.
2. **Ring modulator** — `y = x*(1-mix) + x*sin(phase)*mix`, pure numpy, zero
   latency, carrier phase carried across chunks so consecutive frames do not
   click at the seams.
3. **Makeup gain** — one float multiply, last, because the two stages above
   both cost loudness: the 0.25 ring-mod mix alone drops RMS by ~2.3 dB, and
   the +4 st shift thins the perceived weight of the voice further. Operators
   heard the result as too quiet on the robot's speaker, so the chain ends with
   a `VOICEFX_GAIN_DB` trim. It sits in the float domain *before* the existing
   `np.clip(-1, 1)`, which is therefore the overload protection: the gain can
   saturate the signal but can never wrap it past int16's rails.

Latency accounting: soxr's stream keeps a *pending tail*, not a leading delay —
it has read samples it has not emitted yet. `pending_delay` exposes that on the
input side, so a caller can reconcile output length against input length with
`len(out) + pending_delay * duration_ratio == total_in * duration_ratio`.
"""

import math
import logging

import soxr
import numpy as np
from numpy.typing import NDArray

from reachy_companion.audio.envparse import env_bool, env_float


logger = logging.getLogger(__name__)

DEFAULT_ENABLED = False
DEFAULT_SEMITONES = 4.0
DEFAULT_RINGMOD_HZ = 55.0
DEFAULT_RINGMOD_MIX = 0.25
DEFAULT_GAIN_DB = 5.0

MAX_SEMITONES = 12.0
MAX_RINGMOD_HZ = 2000.0
# Enough cut to undo the default boost and then some, and enough boost to reach
# 4x amplitude — past that a speech signal is mostly clipper, not voice.
MIN_GAIN_DB = -6.0
MAX_GAIN_DB = 12.0

_QUALITY = "HQ"
# Scale in by the full range and out by the last representable value, so that
# int16's asymmetric -32768 floor cannot wrap on the way back. The 0.003 % gain
# it costs is ~90 dB below anything audible.
_INT16_SCALE = 32768.0
_INT16_MAX = 32767.0
_TWO_PI = 2.0 * math.pi


class VoiceFX:
    """The assistant-voice DSP chain for one session, at one sample rate.

    Stateful and single-threaded, like the resamplers it sits next to: it is
    owned by the handler, fed one chunk at a time in order, and `reset()` on
    barge-in and at session start so one utterance's tail never bleeds into the
    next one's opening.
    """

    def __init__(
        self,
        rate: int,
        *,
        enabled: bool = True,
        semitones: float = DEFAULT_SEMITONES,
        ringmod_hz: float = DEFAULT_RINGMOD_HZ,
        ringmod_mix: float = DEFAULT_RINGMOD_MIX,
        gain_db: float = DEFAULT_GAIN_DB,
    ) -> None:
        """Build the chain for `rate` Hz audio.

        Args:
            rate: Sample rate of the PCM handed to `process`, in Hz.
            enabled: When False every method is a no-op and `process` is identity.
            semitones: Upward pitch shift; 0 bypasses the stage entirely.
            ringmod_hz: Ring-modulator carrier; 0 bypasses the stage.
            ringmod_mix: Wet/dry blend of the ring modulator, 0..1.
            gain_db: Makeup gain in dB applied after the other stages. It rides
                along with them rather than switching the chain on: when every
                stage is bypassed the identity path is kept and the gain is not
                applied, so a "disabled" filter stays byte-for-byte the
                pre-filter audio path.

        """
        self.rate = rate
        self.enabled = enabled
        self.semitones = semitones
        self.ringmod_hz = ringmod_hz
        self.ringmod_mix = ringmod_mix
        self.gain_db = gain_db

        self._pitching = enabled and semitones > 0.0
        self._ringmodding = enabled and ringmod_hz > 0.0 and ringmod_mix > 0.0
        self._gain: np.float32 = np.float32(10.0 ** (gain_db / 20.0)) if enabled else np.float32(1.0)
        # Radians per output sample; the ring modulator runs *after* the pitch
        # stage, so it is clocked at the unchanged output rate.
        self._phase_step = _TWO_PI * ringmod_hz / rate
        self._phase = 0.0
        self._pitch: soxr.ResampleStream | None = self._build_pitch_stream()

    @classmethod
    def from_env(cls, rate: int) -> "VoiceFX":
        """Build the chain from `VOICEFX_*`, degrading every malformed knob.

        Logs the settled configuration once per chain. The filter is otherwise
        completely silent — a disabled chain returns its argument unchanged —
        so without this line there is no way to tell from a run's log whether
        the robot is speaking in its cute voice or its plain one.
        """
        voicefx = cls(
            rate,
            enabled=env_bool("VOICEFX_ENABLED", DEFAULT_ENABLED),
            semitones=env_float("VOICEFX_PITCH_SEMITONES", DEFAULT_SEMITONES, lo=0.0, hi=MAX_SEMITONES),
            ringmod_hz=env_float("VOICEFX_RINGMOD_HZ", DEFAULT_RINGMOD_HZ, lo=0.0, hi=MAX_RINGMOD_HZ),
            ringmod_mix=env_float("VOICEFX_RINGMOD_MIX", DEFAULT_RINGMOD_MIX, lo=0.0, hi=1.0),
            gain_db=env_float("VOICEFX_GAIN_DB", DEFAULT_GAIN_DB, lo=MIN_GAIN_DB, hi=MAX_GAIN_DB),
        )
        if voicefx.enabled:
            logger.info(
                "VoiceFX enabled at %d Hz: pitch +%.1f st (speech x%.2f duration), "
                "ring-mod %.0f Hz at %.2f mix, makeup gain %+.1f dB",
                voicefx.rate,
                voicefx.semitones,
                voicefx.duration_ratio,
                voicefx.ringmod_hz,
                voicefx.ringmod_mix,
                voicefx.gain_db,
            )
        else:
            logger.info("VoiceFX disabled; assistant audio passes through unfiltered.")
        return voicefx

    @property
    def duration_ratio(self) -> float:
        """Output duration as a fraction of input duration: `1 / 2**(semitones/12)`.

        Pitching up by four semitones plays the same speech in 79 % of the time.
        This is the tempo side-effect of the resample trick, and it is what the
        profile's "语速放慢" line exists to offset.
        """
        if not self._pitching:
            return 1.0
        return 1.0 / float(2.0 ** (self.semitones / 12.0))

    @property
    def pending_delay(self) -> float:
        """Input samples the pitch stream has consumed but not yet emitted.

        soxr reports its pending residue in *output* samples; this converts to
        the input side by `duration_ratio` so it composes with input lengths.
        Multiply it back by `duration_ratio` to recover the output-side shortfall
        — which is exact, not approximate:

            total_in * duration_ratio - total_out == pending_delay * duration_ratio

        It is a pending tail, not a priming delay: it starts at zero on a fresh
        or freshly `reset()` stream and rises as audio flows.
        """
        if self._pitch is None:
            return 0.0
        return float(self._pitch.delay()) / self.duration_ratio

    def process(self, chunk: NDArray[np.int16]) -> NDArray[np.int16]:
        """Apply the chain to one int16 chunk, continuing from the previous one.

        Args:
            chunk: int16 PCM, either 1-D or (1, N) channel-first as the realtime
                base enqueues it.

        Returns:
            int16 PCM in the caller's shape. Length changes with `duration_ratio`
            when the pitch stage is active. When nothing is active the caller's
            own array is returned unchanged — the exact pre-filter code path,
            with no float round-trip and no copy.

        """
        if not (self._pitching or self._ringmodding) or chunk.size == 0:
            return chunk

        flat = np.ascontiguousarray(chunk.reshape(-1))
        signal = flat.astype(np.float32) / _INT16_SCALE
        signal = self._pitch_shift(signal)
        signal = self._ring_modulate(signal)
        signal = self._apply_gain(signal)

        # The clip is what makes the gain safe: it bounds the float signal to
        # +/-1 BEFORE the int16 scaling, so an overdriven chain saturates
        # instead of wrapping around to the opposite rail.
        out = np.round(np.clip(signal, -1.0, 1.0) * _INT16_MAX).astype(np.int16)
        if chunk.ndim == 2:
            return out.reshape(1, -1)
        return out

    def reset(self) -> None:
        """Drop the pitch tail and rewind the carrier, as at barge-in or session start."""
        if self._pitch is not None:
            self._pitch.clear()
        self._phase = 0.0

    def _build_pitch_stream(self) -> soxr.ResampleStream | None:
        """Open the rate-lie stream, or None when the stage is bypassed.

        `soxr.ResampleStream` accepts a float input rate, which is what makes the
        whole trick possible without a dedicated engine: claiming the 24 kHz PCM
        arrives at `24000 * 2**(st/12)` Hz makes soxr resample it down to 24 kHz,
        and playing that back at 24 kHz raises the pitch by `st` semitones.
        """
        if not self._pitching:
            return None
        source_rate = self.rate * 2.0 ** (self.semitones / 12.0)
        return soxr.ResampleStream(source_rate, self.rate, 1, dtype="float32", quality=_QUALITY)

    def _pitch_shift(self, signal: NDArray[np.float32]) -> NDArray[np.float32]:
        """Resample the chunk through the rate-lie stream, or pass it through."""
        if self._pitch is None:
            return signal
        return np.asarray(self._pitch.resample_chunk(signal), dtype=np.float32)

    def _apply_gain(self, signal: NDArray[np.float32]) -> NDArray[np.float32]:
        """Scale the finished chain by the makeup gain, still in the float domain.

        Deliberately unbounded here: the caller's `np.clip(-1, 1)` is the single
        place overload is handled, so this stage never has to guess a ceiling.
        """
        if self._gain == np.float32(1.0):
            return signal
        return np.asarray(signal * self._gain, dtype=np.float32)

    def _ring_modulate(self, signal: NDArray[np.float32]) -> NDArray[np.float32]:
        """Blend in the modulated copy, carrying the carrier phase to the next chunk.

        The phase is advanced in float64 and wrapped into one period so a long
        session cannot accumulate rounding error in the carrier.
        """
        if not self._ringmodding or signal.size == 0:
            return signal
        n = signal.size
        phase = self._phase + self._phase_step * np.arange(n, dtype=np.float64)
        self._phase = float((self._phase + self._phase_step * n) % _TWO_PI)
        carrier = np.sin(phase).astype(np.float32)
        mix = np.float32(self.ringmod_mix)
        return np.asarray(signal * (np.float32(1.0) - mix) + signal * carrier * mix, dtype=np.float32)
