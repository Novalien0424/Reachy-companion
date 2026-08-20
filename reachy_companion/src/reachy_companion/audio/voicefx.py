"""Cute-robot voice filter for the assistant's outgoing audio (D-010, D-011, D-017).

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

Round 3 (**D-017**) rebuilt everything downstream of the pitch stage. The
operator heard the shipped chain as "full of static noise", and an offline
measurement of the shipped code found two causes and one consequence:

* the "ring modulator" was never ring modulation. `x*(1-mix) + x*sin*mix`
  interpolates rather than adds, so at `mix = 0.25` it is a **6 dB tremolo**,
  and its 55 Hz carrier sat at 0.956 of the psychoacoustic roughness peak
  (~70 Hz). Measured: **+23 dB** of envelope energy in the 30-120 Hz roughness
  band over the pitch-only chain, present at zero gain with zero clipped
  samples. That is the buzz.
* the tremolo cost exactly `sqrt((1-m)**2 + m**2/2)` = **-2.26 dB** of RMS,
  which is *why* the makeup gain was raised to +5 dB — and +5 dB into a hard
  clip pinned **3.3 %** of samples on a -1 dBFS speech signal, then overshot
  the 24 k -> 16 k resampler into a second clip at +0.10 dBTP.

So the AM stage is off by default and can no longer be tuned into the roughness
band, and the hard clip is now a *backstop* behind a soft knee.

Five stages, in order:

1. **Pitch** — WSOLA then soxr, as above, unchanged. `semitones == 0` bypasses
   the stage: neither the stretcher nor the resample stream is constructed, so
   there is no allocation, no latency and nothing to reset.
2. **Feedback comb** — `y[n] = x[n] + g*y[n-D]`, blended `mix` against the dry
   signal. This is the stage that carries the robot character the tremolo was
   supposed to provide, and it is the only candidate measured that adds one
   *without* adding roughness: a comb is linear and time-invariant, so it
   reshapes the spectrum and modulates the envelope not at all (roughness
   -43.6 dB vs -38.7 dB for the untreated reference — it measures *cleaner*).
   4 ms at 24 kHz resonates every 250 Hz with 8.4 dB of peak-to-null ripple:
   the "small speaker inside a tin robot" colour. One `scipy.signal.lfilter`
   call whose `zi` is carried across chunks, so it is exactly chunk-invariant.
3. **AM / tremolo** — `y = x*(1-mix) + x*sin(phase)*mix`, **off by default**.
   Kept because it is a real (if narrow) colour at a high carrier, honestly
   named now: below `mix = 0.5` the multiplier never reaches zero, so this is
   amplitude modulation and not the carrier-suppressed ring modulation the old
   docstring claimed. The carrier is clamped to `{0} u [150, 4000] Hz` — the
   entire 20-150 Hz roughness band is unreachable by construction.
4. **Makeup gain** — one float multiply. It still exists because the pitch
   shift thins the perceived weight of the voice, but it is no longer paying
   for a 2.3 dB tremolo loss, and it is no longer a trap: the knee below it
   bounds the output, so every value in the documented -6..+12 dB range is
   safe. Its meaning is now "gain **into** the saturator".
5. **Soft-knee saturator** — exactly linear below `knee * ceiling`, then
   tanh-asymptotic to `ceiling` (default -1 dBFS = 0.891, the EBU R128
   true-peak production limit, which also leaves the downstream 24 k -> 16 k
   resampler the headroom its intersample overshoot needs). Stateless,
   zero-latency, chunk-invariant, and provably bounded: because `|tanh| < 1`
   strictly, the ceiling is never even reached. Hard clipping is a transfer
   curve with a discontinuous first derivative whose harmonics decay only as
   1/k; the knee is smooth, and measured 10 dB less H7 with a third less
   high-order energy.

The `np.clip(-1, 1)` that used to be the overload control is retained as a pure
no-op backstop — which is exactly what a safety net should be.

Measured end to end on a -1 dBFS speech phantom, old chain vs new: output RMS
-8.80 -> -6.77 dBFS (*louder*, which was the complaint the +5 dB gain answered),
clipped samples 3.29 % -> **0.00 %**, downstream over-rail samples 1079 -> 0,
roughness -16.3 -> -40.5 dB. Latency delta: **zero** — the comb has no lookahead
and the saturator has no state.

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

Measured end to end at 24 kHz, over five seconds of tone in mixed chunk
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
from scipy.signal import lfilter
from numpy.lib.stride_tricks import sliding_window_view

from reachy_companion.audio.envparse import env_bool, env_float


logger = logging.getLogger(__name__)

DEFAULT_ENABLED = False
DEFAULT_SEMITONES = 5.0
DEFAULT_RINGMOD_HZ = 0.0
DEFAULT_RINGMOD_MIX = 0.0
DEFAULT_COMB_MS = 4.0
DEFAULT_COMB_FEEDBACK = 0.45
DEFAULT_COMB_MIX = 0.35
DEFAULT_GAIN_DB = 5.0
DEFAULT_CEILING_DBFS = -1.0
DEFAULT_KNEE = 0.75

MAX_SEMITONES = 12.0
# The AM carrier is gated, not merely clamped: 20-150 Hz is the psychoacoustic
# roughness band (peak ~70 Hz, where the *asper* is defined), and inside it the
# sidebands at f +/- fc stay unresolved within one critical band, which is the
# textbook condition for buzz rather than timbre. 150 Hz is where 2*fc starts to
# exceed the ERB critical bandwidth across the formant region and the colour
# turns metallic instead. Anything in (0, MIN) is refused, not clamped — see
# `_env_carrier_hz`.
MIN_RINGMOD_HZ = 150.0
MAX_RINGMOD_HZ = 4000.0
# Comb delay in ms. 0 disables the stage; otherwise the resonance spacing is
# `rate / round(rate * ms / 1000)` Hz, so 4 ms at 24 kHz is 250 Hz. Below 0.5 ms
# the spacing leaves the speech band entirely; above 20 ms it stops being a
# timbre and starts being an echo.
MIN_COMB_MS = 0.5
MAX_COMB_MS = 20.0
# Feedback gain. `g < 1` is the stability condition; 0.9 keeps a wide margin
# from the ringing that approaching unity produces. 0.45 gives 8.4 dB of ripple.
MAX_COMB_FEEDBACK = 0.9
# Enough cut to undo the default boost and then some, and enough boost to reach
# 4x amplitude. Since D-017 the whole range is safe: the knee bounds the output,
# so more gain buys loudness and smooth compression rather than crunch.
MIN_GAIN_DB = -6.0
MAX_GAIN_DB = 12.0
# Output ceiling. -1 dBFS is the EBU R128 / ITU-R BS.1770 true-peak production
# limit and leaves the downstream 24 k -> 16 k resample the headroom its
# intersample overshoot needs; -3 dBFS covers even a pathological square wave.
MIN_CEILING_DBFS = -12.0
MAX_CEILING_DBFS = 0.0
# Knee as a *fraction of the ceiling*: the chain is exactly linear (identity)
# below `knee * ceiling`, and tanh-asymptotic above it. 0.99 leaves a usable
# span; 0.1 is a knee so low the whole signal is compressed.
MIN_KNEE = 0.1
MAX_KNEE = 0.99

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


class _Comb:
    """Streaming feedback comb: `y[n] = x[n] + g*y[n-D]`, blended `mix` against dry.

    The stage that gives the voice its metallic "small speaker in a tin robot"
    resonance (D-017). It is a linear time-invariant filter, so unlike the
    tremolo it replaces it colours the spectrum without touching the envelope —
    measured *lower* roughness than the untreated signal, and no intelligibility
    cost, which matters because Chinese is a primary scenario.

    Stateful and single-threaded like everything else in the chain: `zi` is the
    delay line, carried across `process` calls and cleared by `reset`. Because
    `lfilter` keeps exact state, the output is bit-identical however the caller
    chunks the stream — which the OpenAI realtime deltas make necessary, since
    their sizes are variable and not under our control.
    """

    def __init__(self, rate: int, delay_ms: float, feedback: float, mix: float) -> None:
        """Build the comb for `rate` Hz audio.

        Args:
            rate: Sample rate of the float32 mono signal handed to `process`.
            delay_ms: Delay line length in milliseconds; resonances land every
                `rate / delay` Hz.
            feedback: `g` in the difference equation. Must be < 1 for stability.
            mix: Wet/dry blend, 0..1. 0 makes the stage an identity.

        """
        self.rate = rate
        self.delay = max(1, int(round(rate * delay_ms / 1000.0)))
        self.feedback = feedback
        self.mix = mix

        # b = [1], a = [1, 0, ..., 0, -g]: one pole at the delay tap, which is
        # exactly `y[n] = x[n] + g*y[n-D]`.
        self._numerator = np.array([1.0], dtype=np.float64)
        self._denominator = np.zeros(self.delay + 1, dtype=np.float64)
        self._denominator[0] = 1.0
        self._denominator[self.delay] = -feedback
        self._zi: NDArray[np.float64] = np.zeros(self.delay, dtype=np.float64)

        self._dry = np.float32(1.0 - mix)
        self._wet = np.float32(mix)

    @property
    def spacing_hz(self) -> float:
        """Frequency spacing of the comb's resonant peaks, in Hz."""
        return self.rate / self.delay

    def reset(self) -> None:
        """Empty the delay line, as at barge-in or session start."""
        self._zi = np.zeros(self.delay, dtype=np.float64)

    def process(self, signal: NDArray[np.float32]) -> NDArray[np.float32]:
        """Filter one chunk, continuing the previous chunk's delay line."""
        if signal.size == 0:
            return signal
        wet, self._zi = lfilter(self._numerator, self._denominator, signal, zi=self._zi)
        resonant = np.asarray(wet, dtype=np.float32)
        return np.asarray(signal * self._dry + resonant * self._wet, dtype=np.float32)


def _env_carrier_hz() -> float:
    """Read `VOICEFX_RINGMOD_HZ`, refusing the roughness band outright.

    The legal set is `{0} u [MIN_RINGMOD_HZ, MAX_RINGMOD_HZ]`. A value inside
    `(0, MIN)` is not clamped up — clamping would silently hand the operator a
    carrier they did not ask for — it is treated as malformed and warns back to
    the default, which is off. This is the knob that shipped at 55 Hz and
    produced the buzz D-017 removes.
    """
    value = env_float("VOICEFX_RINGMOD_HZ", DEFAULT_RINGMOD_HZ, lo=0.0, hi=MAX_RINGMOD_HZ)
    if 0.0 < value < MIN_RINGMOD_HZ:
        logger.warning(
            "Ignoring VOICEFX_RINGMOD_HZ=%s: below %.0f Hz is the psychoacoustic roughness band "
            "and buzzes rather than colours (D-017); using %s.",
            value,
            MIN_RINGMOD_HZ,
            DEFAULT_RINGMOD_HZ,
        )
        return DEFAULT_RINGMOD_HZ
    return value


def _env_comb_ms() -> float:
    """Read `VOICEFX_COMB_MS`, keeping 0 as the off switch above the clamp floor.

    `env_float(lo=MIN_COMB_MS)` would turn "off" into a 0.5 ms comb, so the
    lower bound is applied by hand and only to values that actually asked for
    the stage.
    """
    value = env_float("VOICEFX_COMB_MS", DEFAULT_COMB_MS, lo=0.0, hi=MAX_COMB_MS)
    if 0.0 < value < MIN_COMB_MS:
        logger.warning("Clamping VOICEFX_COMB_MS=%s to its minimum %s.", value, MIN_COMB_MS)
        return MIN_COMB_MS
    return value


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
        comb_ms: float = DEFAULT_COMB_MS,
        comb_feedback: float = DEFAULT_COMB_FEEDBACK,
        comb_mix: float = DEFAULT_COMB_MIX,
        ringmod_hz: float = DEFAULT_RINGMOD_HZ,
        ringmod_mix: float = DEFAULT_RINGMOD_MIX,
        gain_db: float = DEFAULT_GAIN_DB,
        ceiling_dbfs: float = DEFAULT_CEILING_DBFS,
        knee: float = DEFAULT_KNEE,
    ) -> None:
        """Build the chain for `rate` Hz audio.

        Args:
            rate: Sample rate of the PCM handed to `process`, in Hz.
            enabled: When False every method is a no-op and `process` is identity.
            semitones: Upward pitch shift; 0 bypasses the stage entirely.
            comb_ms: Feedback-comb delay in ms; 0 bypasses the stage.
            comb_feedback: Comb feedback gain `g`, < 1 for stability.
            comb_mix: Wet/dry blend of the comb, 0..1; 0 bypasses the stage.
            ringmod_hz: AM/tremolo carrier; 0 bypasses the stage.
            ringmod_mix: Wet/dry blend of the AM stage, 0..1; 0 bypasses it.
            gain_db: Makeup gain in dB applied into the saturator. Unlike
                D-011, a non-zero gain *does* wake the chain: it is a real
                change to the audio, and the saturator behind it makes that
                change safe. The byte-for-byte identity path is reserved for a
                wholly idle chain — no pitch, no comb, no AM, no gain.
            ceiling_dbfs: Output ceiling in dBFS; the saturator is asymptotic
                to it and never reaches it.
            knee: Fraction of the ceiling below which the chain is exactly
                linear.

        """
        self.rate = rate
        self.enabled = enabled
        self.semitones = semitones
        self.comb_ms = comb_ms
        self.comb_feedback = comb_feedback
        self.comb_mix = comb_mix
        self.ringmod_hz = ringmod_hz
        self.ringmod_mix = ringmod_mix
        self.gain_db = gain_db
        self.ceiling_dbfs = ceiling_dbfs
        self.knee = knee

        self._pitching = enabled and semitones > 0.0
        self._combing = enabled and comb_ms > 0.0 and comb_mix > 0.0 and comb_feedback > 0.0
        self._ringmodding = enabled and ringmod_hz > 0.0 and ringmod_mix > 0.0
        self._gain: np.float32 = np.float32(10.0 ** (gain_db / 20.0)) if enabled else np.float32(1.0)
        self._gaining = enabled and self._gain != np.float32(1.0)
        self._active = self._pitching or self._combing or self._ringmodding or self._gaining

        # Saturator geometry, precomputed: the linear region ends at
        # `knee * ceiling` and the tanh spans whatever is left up to `ceiling`.
        self._ceiling = np.float32(10.0 ** (ceiling_dbfs / 20.0))
        self._knee_level = np.float32(knee) * self._ceiling
        self._span = self._ceiling - self._knee_level

        # Radians per output sample; the AM stage runs *after* the pitch stage,
        # so it is clocked at the unchanged output rate.
        self._phase_step = _TWO_PI * ringmod_hz / rate
        self._phase = 0.0
        self._shift = 2.0 ** (semitones / 12.0)
        self._stretch: _WSOLATimeStretcher | None = None
        self._pitch: soxr.ResampleStream | None = None
        if self._pitching:
            self._stretch = _WSOLATimeStretcher(rate, self._shift)
            self._pitch = self._build_pitch_stream()
        self._comb: _Comb | None = _Comb(rate, comb_ms, comb_feedback, comb_mix) if self._combing else None

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
            comb_ms=_env_comb_ms(),
            comb_feedback=env_float("VOICEFX_COMB_FEEDBACK", DEFAULT_COMB_FEEDBACK, lo=0.0, hi=MAX_COMB_FEEDBACK),
            comb_mix=env_float("VOICEFX_COMB_MIX", DEFAULT_COMB_MIX, lo=0.0, hi=1.0),
            ringmod_hz=_env_carrier_hz(),
            ringmod_mix=env_float("VOICEFX_RINGMOD_MIX", DEFAULT_RINGMOD_MIX, lo=0.0, hi=1.0),
            gain_db=env_float("VOICEFX_GAIN_DB", DEFAULT_GAIN_DB, lo=MIN_GAIN_DB, hi=MAX_GAIN_DB),
            ceiling_dbfs=env_float(
                "VOICEFX_CEILING_DBFS", DEFAULT_CEILING_DBFS, lo=MIN_CEILING_DBFS, hi=MAX_CEILING_DBFS
            ),
            knee=env_float("VOICEFX_KNEE", DEFAULT_KNEE, lo=MIN_KNEE, hi=MAX_KNEE),
        )
        if voicefx.enabled:
            logger.info(
                "VoiceFX enabled at %d Hz: pitch +%.1f st via WSOLA time-stretch + resample "
                "(duration preserved, %.1f ms lookahead), comb %s, AM %s, "
                "makeup gain %+.1f dB into a soft knee at %.2f of a %+.1f dBFS ceiling",
                voicefx.rate,
                voicefx.semitones,
                voicefx.latency_ms,
                voicefx._comb_summary(),
                voicefx._am_summary(),
                voicefx.gain_db,
                voicefx.knee,
                voicefx.ceiling_dbfs,
            )
        else:
            logger.info("VoiceFX disabled; assistant audio passes through unfiltered.")
        return voicefx

    def _comb_summary(self) -> str:
        """Describe the comb stage for the startup log, resonance spacing included."""
        if self._comb is None:
            return "off"
        return f"{self.comb_ms:.1f} ms/g {self.comb_feedback:.2f}/mix {self.comb_mix:.2f} ({self._comb.spacing_hz:.0f} Hz spacing)"

    def _am_summary(self) -> str:
        """Describe the AM/tremolo stage for the startup log."""
        if not self._ringmodding:
            return "off"
        return f"{self.ringmod_hz:.0f} Hz at {self.ringmod_mix:.2f} mix"

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
            **every** stage is idle — no pitch, no comb, no AM, no gain — the
            caller's own array is returned unchanged: the exact pre-filter code
            path, with no float round-trip and no copy.

        """
        if not self._active or chunk.size == 0:
            return chunk

        flat = np.ascontiguousarray(chunk.reshape(-1))
        signal = flat.astype(np.float32) / _INT16_SCALE
        signal = self._pitch_shift(signal)
        signal = self._comb_filter(signal)
        signal = self._modulate(signal)
        signal = self._apply_gain(signal)
        signal = self._saturate(signal)

        # Retained backstop only: the saturator is asymptotic to a ceiling of at
        # most 1.0 and `|tanh| < 1` strictly, so this clip can never engage. It
        # stays because it costs nothing and it is the last thing standing
        # between a future bug in the stages above and an int16 wraparound.
        out = np.round(np.clip(signal, -1.0, 1.0) * _INT16_MAX).astype(np.int16)
        if chunk.ndim == 2:
            return out.reshape(1, -1)
        return out

    def reset(self) -> None:
        """Drop every carried tail, as at barge-in or session start.

        The pitch stretcher's buffers, soxr's filter tail, the comb's delay line
        and the AM carrier phase. The comb matters as much as the others here:
        its delay line holds up to 20 ms of the interrupted utterance, and
        without this it would keep feeding that back into the next reply.
        """
        if self._stretch is not None:
            self._stretch.reset()
        if self._pitch is not None:
            self._pitch.clear()
        if self._comb is not None:
            self._comb.reset()
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

    def _comb_filter(self, signal: NDArray[np.float32]) -> NDArray[np.float32]:
        """Run the feedback comb, or pass the chunk through when bypassed."""
        if self._comb is None:
            return signal
        return self._comb.process(signal)

    def _apply_gain(self, signal: NDArray[np.float32]) -> NDArray[np.float32]:
        """Scale the coloured signal by the makeup gain, still in the float domain.

        Deliberately unbounded here: `_saturate` is the single place overload is
        handled, so this stage never has to guess a ceiling. Since D-017 that is
        a soft knee rather than a hard clip, which is what makes the whole
        documented gain range usable instead of only its bottom 2 dB.
        """
        if self._gain == np.float32(1.0):
            return signal
        return np.asarray(signal * self._gain, dtype=np.float32)

    def _saturate(self, signal: NDArray[np.float32]) -> NDArray[np.float32]:
        """Bound the chain to `ceiling` through a soft knee — the last stage (D-017).

        Exactly the identity below `knee * ceiling`; above it the excess is bent
        through a tanh scaled to the remaining span, so the transfer curve is
        continuous, monotonic and smooth *at* the knee (tanh(0) = 0 with unit
        slope) rather than kinked the way a hard clip is. Because `|tanh| < 1`
        strictly, `|y| < ceiling` for every finite input, at any overdrive.

        Memoryless by choice. A lookahead limiter was measured against this and
        lost on every axis: quieter, 6.7x the CPU, ~3 ms more latency, and — the
        decisive one — its output depended on the chunk size, which the OpenAI
        realtime deltas do not let us control.
        """
        if signal.size == 0:
            return signal
        magnitude = np.abs(signal)
        over = magnitude > self._knee_level
        if not bool(over.any()):
            return signal
        out = np.array(signal, dtype=np.float32)
        excess = (magnitude[over] - self._knee_level) / self._span
        out[over] = np.sign(signal[over]) * (self._knee_level + self._span * np.tanh(excess))
        return out

    def _modulate(self, signal: NDArray[np.float32]) -> NDArray[np.float32]:
        """Blend in the amplitude-modulated copy, carrying the carrier phase forward.

        Honest naming since D-017: `x*(1-mix) + x*sin*mix` is `x * [(1-mix) +
        mix*sin]`, an *interpolation*, so below `mix = 0.5` the multiplier never
        reaches zero and this is tremolo, not the carrier-suppressed ring
        modulation the name suggests. It is off by default and its carrier
        cannot be set into the roughness band.

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
