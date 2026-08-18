"""Cute-robot voice filter for the assistant's outgoing audio (D-010, D-011).

Engine-free by decision: both external pitch engines were rejected in review
(python-stretch resets its state per call; pedalboard primes for a full second;
neither ships aarch64 wheels). Round 1 therefore shipped the bare resample-rate
trick — pitch up *and* speed up together — and the profile carried a "语速放慢"
line to compensate. Operator verdict after hearing it on the robot: keep the
pitch, kill the speed-up.

So the pitch stage is now a **two-step, duration-preserving** shift, still with
no new dependency:

    x --> WSOLA time-stretch by 2**(st/12)  --> soxr rate trick by 2**(-st/12) --> y
          (longer, same pitch)                 (shorter, higher pitch)

Net: pitch multiplied by `2**(st/12)`, length unchanged. The stretcher is a
streaming WSOLA (Waveform Similarity Overlap-Add) written in numpy: hann-windowed
50 %-overlap frames, each frame's read position nudged inside a bounded tolerance
window to the offset whose normalized cross-correlation with the *natural
continuation* of the previous frame is highest. That similarity search is what
keeps the waveform periodic across the splice, so the stretch neither doubles
pitch pulses nor phase-cancels them.

Three stages, in order:

1. **Pitch** — WSOLA then soxr, as above. `semitones == 0` is a *hard* bypass:
   neither the stretcher nor the resample stream is constructed, so there is no
   allocation, no latency and nothing to reset.
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

**Latency.** Both pitch steps now contribute, and neither is a priming delay —
they are *pending tails*: samples read but not yet emitted, starting at zero on
a fresh or freshly `reset()` chain and rising to a steady state as audio flows.

* WSOLA needs to see `window + hop + tolerance` input samples past an analysis
  instant before it can commit that frame (the tolerance for the search, the
  window for the frame itself, one hop more for the next frame's correlation
  template). At 24 kHz that is 480 + 240 + 120 = 840 samples = **35.0 ms**,
  deterministic, reported by `latency_ms`.
* soxr's stream holds a further 116-689 output samples (4.8-28.7 ms) — its
  filter tail plus however much of the last block it has read but not yet
  converted, which oscillates with its internal block size.

`pending_delay` reports the live total of both, **in input samples**, so a caller
can reconcile lengths with a single identity that no longer needs a ratio:

    len(total_out) + pending_delay == total_in

Measured end to end at 24 kHz, +4 st, over five seconds of tone in mixed chunk
sizes: mean 1145 samples (47.7 ms), p95 1440 (60.0 ms), peak 1526 (63.6 ms).
The peak is a block-buffering spike inside soxr that the very next chunk drains,
not a standing delay; the standing part is WSOLA's 35 ms plus soxr's 4.8 ms
filter tail.

Round-1 note for readers of the old code: `duration_ratio` is retained at a
constant 1.0, for test-contract continuity only — no code under `src/` reads it.
Duration is preserved now; there is no tempo side-effect left to account for.
"""

import math
import logging

import soxr
import numpy as np
from numpy.typing import NDArray
from numpy.lib.stride_tricks import sliding_window_view

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

# WSOLA geometry, in milliseconds so it follows the sample rate.
#
# 20 ms window / 10 ms synthesis hop: long enough to hold two periods of a 100 Hz
# male fundamental (the hardest case for the similarity search), short enough
# that the whole pitch chain stays inside the 70 ms latency budget for a
# conversational turn-taking loop (D-011).
_WSOLA_WINDOW_MS = 20.0
# +/-5 ms of search, i.e. a 10 ms span: one whole period of a 100 Hz fundamental,
# so the search can always find a pitch-synchronous splice for any voice above
# that — and ~2.3 periods at the 440 Hz test tone. Widening it to the textbook
# +/-hop (10 ms) measured no better on a formant-and-jitter speech phantom (mean
# best correlation 0.9955 vs 0.9945, 5th percentile 0.970 vs 0.968) and cost
# 5 ms of latency, so the narrow end of the useful range wins.
_WSOLA_TOLERANCE_MS = 5.0


class _WSOLATimeStretcher:
    """Streaming WSOLA: lengthen audio by `factor` without moving its pitch.

    Stateful and single-threaded like the resamplers around it: chunks must be
    fed in order, and `reset()` returns it to the state of a fresh instance.

    The frame at synthesis index `m` is overlap-added at output position
    `m * hop`, but is *read* from near input position `round(m * hop / factor)`
    — "near" because the exact read offset is chosen inside `+/- tolerance` to
    maximize the normalized cross-correlation against the natural continuation
    of the previously emitted frame. Periodic hann windows at 50 % overlap sum
    to exactly 1.0 whatever offset the search picks, so the overlap-add is
    gain-neutral by construction and only the phase alignment is at stake.
    """

    def __init__(self, rate: int, factor: float) -> None:
        """Build a stretcher for `rate` Hz audio that lengthens by `factor` (> 1).

        Args:
            rate: Sample rate of the float32 mono signal handed to `process`.
            factor: Output length as a multiple of input length. `2**(st/12)`
                for a pitch shift of `st` semitones up.

        """
        self.rate = rate
        self.factor = factor

        window = int(round(rate * _WSOLA_WINDOW_MS / 1000.0))
        # Even, so the 50 % overlap hop is an exact integer and the periodic
        # hann pair sums to 1.0 sample for sample.
        self.window = window + (window % 2)
        self.hop_out = self.window // 2
        # Kept as a float and multiplied by the frame index rather than
        # accumulated, so a long session cannot drift off the ideal timeline.
        self.hop_in = self.hop_out / factor
        self.tolerance = int(round(rate * _WSOLA_TOLERANCE_MS / 1000.0))

        index = np.arange(self.window, dtype=np.float64)
        self._win = (0.5 - 0.5 * np.cos(_TWO_PI * index / self.window)).astype(np.float32)

        self._buffer: NDArray[np.float32] = np.zeros(0, dtype=np.float32)
        self._origin = 0
        self._frame = 0
        self._natural: NDArray[np.float32] | None = None
        self._tail: NDArray[np.float32] = np.zeros(self.hop_out, dtype=np.float32)
        self._total_in = 0
        self._total_out = 0

    @property
    def lookahead(self) -> int:
        """Input samples needed past an analysis instant before its frame can be emitted.

        `tolerance` to search, `window` to cut the frame, and one `hop_out` more
        because the frame's tail becomes the correlation template for the next
        one. This is the stretcher's whole algorithmic delay.
        """
        return self.window + self.hop_out + self.tolerance

    @property
    def pending_input(self) -> float:
        """Input samples consumed but not yet represented in the output.

        Quoted on the input side: the output produced so far corresponds to
        `total_out / factor` input samples, and everything fed beyond that is
        still inside the stretcher.
        """
        return float(self._total_in) - float(self._total_out) / self.factor

    def reset(self) -> None:
        """Return to the state of a fresh instance: no buffer, no tail, no timeline."""
        self._buffer = np.zeros(0, dtype=np.float32)
        self._origin = 0
        self._frame = 0
        self._natural = None
        self._tail = np.zeros(self.hop_out, dtype=np.float32)
        self._total_in = 0
        self._total_out = 0

    def process(self, signal: NDArray[np.float32]) -> NDArray[np.float32]:
        """Stretch one chunk, continuing the previous chunk's synthesis timeline.

        Returns every frame the newly available input completes — which can be
        zero frames for a chunk shorter than the remaining lookahead, and more
        than one for a long chunk. Output length is `factor` times input length
        in the long run, exactly as `pending_input` accounts for.
        """
        if signal.size:
            self._buffer = np.concatenate((self._buffer, signal))
            self._total_in += int(signal.size)

        blocks: list[NDArray[np.float32]] = []
        while True:
            anchor = int(round(self._frame * self.hop_in))
            if self._origin + self._buffer.size < anchor + self.lookahead:
                break
            blocks.append(self._emit_frame(anchor))
            self._frame += 1
            self._discard_before(int(round(self._frame * self.hop_in)) - self.tolerance)

        if not blocks:
            return np.zeros(0, dtype=np.float32)
        out = np.concatenate(blocks)
        self._total_out += int(out.size)
        return out

    def _emit_frame(self, anchor: int) -> NDArray[np.float32]:
        """Cut, window and overlap-add the frame for `anchor`, returning one hop of output."""
        start = anchor - self._origin
        best = self._best_offset(start)

        frame = self._buffer[best : best + self.window] * self._win
        block = self._tail + frame[: self.hop_out]
        self._tail = np.array(frame[self.hop_out :], dtype=np.float32)

        # What *would* have followed this frame if the signal simply continued:
        # the next frame is searched for whichever segment looks most like it.
        natural_at = best + self.hop_out
        self._natural = np.array(self._buffer[natural_at : natural_at + self.window], dtype=np.float32)
        return block

    def _best_offset(self, start: int) -> int:
        """Return the buffer index in `start +/- tolerance` that splices most smoothly.

        Scored by normalized cross-correlation against the previous frame's
        natural continuation: the dot product alone would simply prefer the
        loudest candidate, which on a rising syllable pulls every frame to the
        edge of the tolerance window and turns the stretch into a stutter.
        """
        natural = self._natural
        if natural is None:
            return start  # first frame after a reset: nothing to align to yet

        lo = max(0, start - self.tolerance)
        hi = start + self.tolerance
        region = self._buffer[lo : hi + self.window]
        candidates = sliding_window_view(region, self.window)

        squares = np.empty(region.size + 1, dtype=np.float64)
        squares[0] = 0.0
        np.cumsum(np.square(region, dtype=np.float64), out=squares[1:])
        energy = squares[self.window :] - squares[: -self.window]

        reference = float(np.dot(natural, natural))
        peak = float(energy.max()) if energy.size else 0.0
        if reference <= 0.0 or peak <= 0.0:
            return start  # digital silence on one side: no alignment to find

        correlation = np.asarray(candidates @ natural, dtype=np.float64)
        score = correlation / np.sqrt(energy * reference + 1e-20)
        return lo + int(np.argmax(score))

    def _discard_before(self, index: int) -> None:
        """Drop buffered input that no future frame can reach back to."""
        keep_from = max(0, index)
        drop = keep_from - self._origin
        if drop > 0:
            self._buffer = np.array(self._buffer[drop:], dtype=np.float32)
            self._origin = keep_from


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
        self._shift = 2.0 ** (semitones / 12.0)
        self._stretch: _WSOLATimeStretcher | None = None
        self._pitch: soxr.ResampleStream | None = None
        if self._pitching:
            self._stretch = _WSOLATimeStretcher(rate, self._shift)
            self._pitch = self._build_pitch_stream()

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
                "VoiceFX enabled at %d Hz: pitch +%.1f st via WSOLA time-stretch + resample "
                "(duration preserved, %.1f ms lookahead), ring-mod %.0f Hz at %.2f mix, "
                "makeup gain %+.1f dB",
                voicefx.rate,
                voicefx.semitones,
                voicefx.latency_ms,
                voicefx.ringmod_hz,
                voicefx.ringmod_mix,
                voicefx.gain_db,
            )
        else:
            logger.info("VoiceFX disabled; assistant audio passes through unfiltered.")
        return voicefx

    @property
    def duration_ratio(self) -> float:
        """Output duration as a fraction of input duration — now always 1.0.

        There are **no production readers**: nothing under `src/` reads this, not
        even the `from_env` log line any more. It is retained rather than deleted as a D-011
        choice about test-contract continuity: round 1 stated the tempo
        side-effect through this name (`1 / 2**(semitones/12)`, 0.79 at +4 st,
        offset by a "speak slowly" profile line), so pinning the same name at
        unity is a legible statement that the side-effect is gone, where a
        deletion would only have removed the evidence. Delete it freely if a
        future round finds the name more confusing than useful; nothing in the
        audio path will break.
        """
        return 1.0

    @property
    def latency_ms(self) -> float:
        """Algorithmic delay of the pitch chain in milliseconds, WSOLA side.

        `window + hop + tolerance` at the chain's sample rate: 840 samples,
        35.0 ms, at 24 kHz. soxr's tail (4.8 ms of filter plus up to ~24 ms of
        block buffering) rides on top of this; `pending_delay` is the live
        measurement of the two together.
        """
        if self._stretch is None:
            return 0.0
        return 1000.0 * self._stretch.lookahead / self.rate

    @property
    def pending_delay(self) -> float:
        """Input samples the pitch chain has consumed but not yet emitted.

        Both stages contribute and both are pending *tails*, not priming delays:
        the value starts at zero on a fresh or freshly `reset()` chain and rises
        to a steady state as audio flows. The stretcher's share is quoted on the
        input side already; soxr's `delay()` is quoted in output samples, which
        the duration-preserving chain makes the same unit. So the reconciliation
        is a plain subtraction, with no ratio anywhere:

            total_in - total_out == pending_delay
        """
        if self._stretch is None or self._pitch is None:
            return 0.0
        return self._stretch.pending_input + float(self._pitch.delay())

    def process(self, chunk: NDArray[np.int16]) -> NDArray[np.int16]:
        """Apply the chain to one int16 chunk, continuing from the previous one.

        Args:
            chunk: int16 PCM, either 1-D or (1, N) channel-first as the realtime
                base enqueues it.

        Returns:
            int16 PCM in the caller's shape. Length tracks the input's over a
            stream, but any single chunk can come back shorter or longer (or
            empty) while the pitch chain fills and drains its lookahead. When
            nothing is active the caller's own array is returned unchanged — the
            exact pre-filter code path, with no float round-trip and no copy.

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
        """Drop both pitch tails and rewind the carrier, as at barge-in or session start."""
        if self._stretch is not None:
            self._stretch.reset()
        if self._pitch is not None:
            self._pitch.clear()
        self._phase = 0.0

    def _build_pitch_stream(self) -> soxr.ResampleStream:
        """Open the rate-lie stream that turns the stretch back into a pitch shift.

        `soxr.ResampleStream` accepts a float input rate, which is what makes the
        whole trick possible without a dedicated engine: claiming the stretched
        PCM arrives at `rate * 2**(st/12)` Hz makes soxr resample it down to
        `rate`, shortening it by exactly the factor WSOLA lengthened it by and
        raising every frequency in it by `st` semitones.
        """
        source_rate = self.rate * self._shift
        return soxr.ResampleStream(source_rate, self.rate, 1, dtype="float32", quality=_QUALITY)

    def _pitch_shift(self, signal: NDArray[np.float32]) -> NDArray[np.float32]:
        """Stretch then resample the chunk, or pass it through when bypassed."""
        if self._stretch is None or self._pitch is None:
            return signal
        stretched = self._stretch.process(signal)
        if stretched.size == 0:
            # Still filling the lookahead; nothing to hand soxr this chunk.
            return stretched
        return np.asarray(self._pitch.resample_chunk(stretched), dtype=np.float32)

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
