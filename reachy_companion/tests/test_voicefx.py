"""Behavioural contract for the cute-robot voice filter (D-010, D-011, D-017).

Engine-free by design: the pitch shift is a streaming numpy WSOLA time-stretch
composed with the resample-rate trick through the already-shipped stateful
`soxr`, so these tests pin *what the chain does to a signal* — dominant
frequency, duration, latency, harmonic purity, chunk-independence, reset —
rather than any engine's internals.

Round 2 (D-011) changed three of these contracts on purpose:

* duration is now **preserved**, so `duration_ratio` is a constant 1.0 and the
  old `1 / 2**(st/12)` formula test is gone;
* `pending_delay` is quoted in input samples for the whole pitch chain, and
  reconciles with a plain subtraction instead of a ratio;
* chunked-vs-whole equivalence is stated at envelope and seam level, because a
  similarity search is not obliged to be sample-exact (this implementation
  happens to be — see `test_chunking_does_not_change_the_pitch_output_at_all`).

Round 3 (D-017) changed three more, and added the acceptance test the whole
rebuild exists for:

* the "static noise" the operator heard was a 55 Hz tremolo at the
  psychoacoustic roughness peak plus a +5 dB makeup gain into a hard clip. The
  AM stage is now off by default and its carrier is gated out of the roughness
  band; a feedback comb carries the robot character instead; a stateless
  soft-knee saturator replaces the hard clip;
* the byte-exact bypass now needs the **whole** chain idle, because a non-zero
  gain is a real change to the audio and is no longer free;
* the whole chain — not just the pitch stage — is byte-exactly chunk-invariant,
  which matters because the realtime deltas arrive in sizes we do not control.
"""

import math
import logging

import numpy as np
import pytest
from scipy.signal import lfilter

from reachy_companion.audio.voicefx import (
    MAX_KNEE,
    MIN_KNEE,
    MAX_COMB_MS,
    MAX_GAIN_DB,
    MIN_COMB_MS,
    MIN_GAIN_DB,
    DEFAULT_KNEE,
    MAX_SEMITONES,
    MAX_RINGMOD_HZ,
    MIN_RINGMOD_HZ,
    DEFAULT_COMB_MS,
    DEFAULT_GAIN_DB,
    DEFAULT_COMB_MIX,
    MAX_CEILING_DBFS,
    MIN_CEILING_DBFS,
    DEFAULT_SEMITONES,
    MAX_COMB_FEEDBACK,
    DEFAULT_CEILING_DBFS,
    DEFAULT_COMB_FEEDBACK,
    VoiceFX,
)


RATE = 24000
SEMITONES = 4.0
TONE_HZ = 440.0
FFT_WINDOW = 8192

# The chunk sizes every streaming test feeds: coprime with the analysis hop and
# with each other, so no frame boundary can line up with a chunk boundary.
ODD_CHUNKS = (479, 501, 1024, 137)
# Round chunk sizes for the D-017 whole-chain invariance test — the shapes a
# realtime delta actually arrives in (20/40/67/200 ms at 24 kHz).
REALTIME_CHUNKS = (480, 960, 1600, 4800)
# The WSOLA analysis window at RATE — the unit the duration contract is stated in.
ANALYSIS_WINDOW = 480
# WSOLA's own lookahead (window + hop + tolerance) at RATE, in ms.
LATENCY_MS = 35.0
# Ceiling for the whole pitch chain's in-flight audio, in ms. WSOLA contributes a
# deterministic 35.0 ms; soxr adds its filter tail plus a block-buffering spike
# that the next chunk drains. Measured over 5 s of tone: mean 47.7 ms, p95
# 60.0 ms, peak 63.6 ms.
LATENCY_BUDGET_MS = 70.0
# Energy outside +/-3 bins of the shifted fundamental and its 2nd/3rd harmonics,
# as a fraction of total energy. Measured on this implementation at +4 st:
# 3.1e-5 at 220 Hz, 7.7e-5 at 440 Hz, 2.1e-4 at 880 Hz. Pinned ~2x above the
# worst of those so a real quality regression trips it but jitter does not.
THD_PROXY_MAX = 5e-4

# The D-017 acceptance floor for output loudness on the speech phantom below.
# Measured: old chain -8.80 dBFS (with 3.29 % of samples destroyed), new chain
# -6.77 dBFS with none. -7.5 leaves ~0.7 dB of headroom for tuning without
# letting the chain get quieter than the thing it replaced.
ACCEPT_RMS_DBFS = -7.5
# The saturator's bound is `|y| < ceiling` in the float domain, which is where it
# is asserted exactly (`test_soft_knee_is_bounded_...`). Once the chain rounds to
# int16 the strongest *provable* statement is half a least-significant bit weaker,
# because `round()` may carry a sample sitting on the asymptote up to the next
# code: `|out| <= round(ceiling * 32767)`, i.e. `< ceiling + 0.5/32767`. Anything
# tighter would be asserting a rounding accident, not a property.
CEILING_TOLERANCE = 0.5 / 32767.0

VOICEFX_LOGGER = "reachy_companion.audio.voicefx"
ENVPARSE_LOGGER = "reachy_companion.audio.envparse"

_ENV_KNOBS = (
    "VOICEFX_ENABLED",
    "VOICEFX_PITCH_SEMITONES",
    "VOICEFX_COMB_MS",
    "VOICEFX_COMB_FEEDBACK",
    "VOICEFX_COMB_MIX",
    "VOICEFX_RINGMOD_HZ",
    "VOICEFX_RINGMOD_MIX",
    "VOICEFX_GAIN_DB",
    "VOICEFX_CEILING_DBFS",
    "VOICEFX_KNEE",
)

# Every stage off: the arguments that produce the byte-exact passthrough, and
# the base every "one stage at a time" test starts from.
IDLE = {"semitones": 0.0, "comb_ms": 0.0, "comb_mix": 0.0, "ringmod_hz": 0.0, "gain_db": 0.0}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never let a developer's real .env decide what these tests measure."""
    for name in _ENV_KNOBS:
        monkeypatch.delenv(name, raising=False)


def _sine(n: int, freq: float = TONE_HZ, rate: int = RATE, amp: float = 0.5) -> np.ndarray:
    """Return `n` samples of an int16 sine at `freq`."""
    t = np.arange(n) / rate
    return (amp * np.sin(2 * np.pi * freq * t) * 32767).astype(np.int16)


def _lsb_diff(a: np.ndarray, b: np.ndarray) -> int:
    """Return the largest absolute difference between two int16 buffers, in LSB."""
    n = min(len(a), len(b))
    return int(np.abs(a[:n].astype(np.int32) - b[:n].astype(np.int32)).max())


def _feed(fx: VoiceFX, signal: np.ndarray, sizes: tuple[int, ...]) -> np.ndarray:
    """Push `signal` through `fx` in the given repeating slice sizes."""
    out, i, k = [], 0, 0
    while i < len(signal):
        chunk = sizes[k % len(sizes)]
        k += 1
        out.append(fx.process(signal[i : i + chunk]).reshape(-1))
        i += chunk
    return np.concatenate(out) if out else np.zeros(0, dtype=np.int16)


def _rms(pcm: np.ndarray) -> float:
    """Return the root-mean-square level of an int16 buffer."""
    return float(np.sqrt(np.mean(pcm.astype(np.float64) ** 2)))


def _db(ratio: float) -> float:
    """Return an amplitude ratio in decibels."""
    return 20.0 * float(np.log10(ratio))


def _dominant_hz(pcm: np.ndarray, start: int = 4096) -> float:
    """Return the peak frequency of an FFT_WINDOW-long slice past the transient."""
    window = pcm[start : start + FFT_WINDOW].astype(np.float64)
    assert len(window) == FFT_WINDOW
    spectrum = np.abs(np.fft.rfft(window * np.hanning(FFT_WINDOW)))
    return float(np.fft.rfftfreq(FFT_WINDOW, 1.0 / RATE)[int(np.argmax(spectrum))])


def _thd_proxy(pcm: np.ndarray, target: float, start: int = 8192, size: int = 16384) -> float:
    """Return the fraction of energy that is neither `target` nor its 2nd/3rd harmonic.

    A sine in must give a sine out: every stitch artefact a time-stretcher can
    make — a doubled pitch pulse, a phase-cancelled splice, a period-rate warble
    — puts energy somewhere other than the shifted fundamental and its first two
    harmonics. +/-3 bins around each keeps the analysis window's own leakage
    (and the shift's fractional bin offset) out of the numerator; the first few
    bins are excluded as DC leakage.
    """
    window = pcm[start : start + size].astype(np.float64)
    assert len(window) == size
    spectrum = np.abs(np.fft.rfft(window * np.hanning(size))) ** 2
    bin_hz = RATE / size

    signal = np.zeros(len(spectrum), dtype=bool)
    signal[:4] = True
    for harmonic in (1, 2, 3):
        centre = target * harmonic
        if centre >= RATE / 2:
            continue
        index = int(round(centre / bin_hz))
        signal[max(0, index - 3) : index + 4] = True

    total = float(spectrum.sum())
    return (total - float(spectrum[signal].sum())) / total


def _rms_profile(pcm: np.ndarray, milliseconds: int = 100) -> np.ndarray:
    """Return the RMS of each `milliseconds`-long block, i.e. the loudness envelope."""
    step = RATE * milliseconds // 1000
    blocks = len(pcm) // step
    return np.array([_rms(pcm[i * step : (i + 1) * step]) for i in range(blocks)])


def _max_jump(pcm: np.ndarray) -> int:
    """Return the largest sample-to-sample step — a splice click's fingerprint."""
    return int(np.abs(np.diff(pcm.astype(np.int32))).max())


def _fizz(signal: np.ndarray, size: int = 16384) -> float:
    """Return the fraction of spectral energy above 4 kHz.

    On a low-frequency tone everything up there is distortion product, and it is
    the band a small speaker makes harsh — the audible signature of the
    high-order harmonics a discontinuous transfer curve radiates.
    """
    window = signal[:size].astype(np.float64) * np.hanning(size)
    spectrum = np.abs(np.fft.rfft(window)) ** 2
    above = np.fft.rfftfreq(size, 1.0 / RATE) > 4000.0
    return float(spectrum[above].sum() / spectrum.sum())


def _pitch_only(**overrides: float) -> VoiceFX:
    """Build a chain with only the pitch stage live, for the D-010/D-011 contracts."""
    kwargs: dict[str, float] = {"semitones": SEMITONES, "comb_mix": 0.0, "ringmod_hz": 0.0, "gain_db": 0.0}
    kwargs.update(overrides)
    return VoiceFX(RATE, **kwargs)  # type: ignore[arg-type]


def _dbfs(pcm: np.ndarray) -> float:
    """Return the RMS of an int16 buffer in dBFS."""
    return 20.0 * float(np.log10(_rms(pcm) / 32767.0 + 1e-20))


def _speech_phantom(seconds: float = 4.0, f0: float = 200.0, seed: int = 1) -> np.ndarray:
    """Return the D-017 diagnosis phantom: jittered harmonic source, 3 formants, syllables.

    Copied from the diagnosis run so the numbers this file asserts are the same
    numbers the report measured (old chain: -8.80 dBFS out, 3.29 % of samples
    destroyed). A sine cannot stand in for it: the clipping the rebuild removes
    is level- and crest-dependent, and a sine has neither a syllabic envelope
    nor a formant structure to make the loud vowels loud.
    """
    rng = np.random.default_rng(seed)
    n = int(seconds * RATE)
    t = np.arange(n) / RATE

    frequency = f0 * (1.0 + 0.06 * np.sin(2 * np.pi * 4.5 * t) + 0.02 * rng.standard_normal(n).cumsum() / np.sqrt(n))
    phase = 2 * np.pi * np.cumsum(frequency) / RATE
    source = np.zeros(n)
    harmonic = 1
    while harmonic * f0 < RATE / 2 * 0.95:
        source += (1.0 / harmonic**1.6) * np.sin(harmonic * phase + rng.uniform(0, 2 * np.pi))
        harmonic += 1

    voiced = source
    for centre, bandwidth, level in ((700.0, 90.0, 1.0), (1220.0, 110.0, 0.7), (2600.0, 160.0, 0.45)):
        pole = math.exp(-math.pi * bandwidth / RATE)
        theta = 2 * math.pi * centre / RATE
        resonator = lfilter([1 - pole], [1.0, -2 * pole * math.cos(theta), pole * pole], source)
        voiced = voiced + level * resonator

    envelope = np.clip(0.5 + 0.5 * np.sin(2 * np.pi * 3.7 * t - np.pi / 2), 0.02, 1.0) ** 1.5
    voiced = voiced * envelope + rng.standard_normal(n) * 0.02 * (1.0 - envelope)
    voiced = voiced - voiced.mean()
    return voiced.astype(np.float32)


def _peak_normalized_pcm(dbfs: float = -1.0) -> np.ndarray:
    """Return the speech phantom as int16 PCM peak-normalized to `dbfs`.

    Real TTS output is peak-normalized near 0 dBFS, not RMS-normalized, which is
    what made the +5 dB makeup gain destructive in practice.
    """
    signal = _speech_phantom()
    scaled = signal * (10.0 ** (dbfs / 20.0) / float(np.abs(signal).max()))
    return np.round(np.clip(scaled, -1.0, 1.0) * 32767.0).astype(np.int16)


# --------------------------------------------------------------------------
# Disabled: the exact pre-task path
# --------------------------------------------------------------------------


def test_disabled_process_returns_the_very_same_object() -> None:
    """Disabled must cost nothing at all — not even a copy or a float round-trip."""
    fx = VoiceFX(RATE, enabled=False)
    pcm = _sine(480).reshape(1, -1)

    assert fx.enabled is False
    assert fx.process(pcm) is pcm


def test_disabled_exposes_a_neutral_duration_and_no_pending_tail() -> None:
    """A disabled filter must not make the handler account for any latency."""
    fx = VoiceFX(RATE, enabled=False)

    assert fx.duration_ratio == pytest.approx(1.0)
    assert fx.pending_delay == pytest.approx(0.0)
    assert fx.latency_ms == pytest.approx(0.0)


# --------------------------------------------------------------------------
# Pitch stage
# --------------------------------------------------------------------------


def test_duration_ratio_is_pinned_at_unity() -> None:
    """D-011 removed the tempo side-effect, so the ratio is a constant 1.0.

    Round 1's `1 / 2**(semitones/12)` — 0.79 at +4 st, a 26 % speed-up — is the
    contract this replaces. The property is kept rather than deleted because it
    is the name the audio path reasons about, and unity is the claim being made.
    """
    fx = _pitch_only()

    assert fx.duration_ratio == pytest.approx(1.0)
    assert VoiceFX(RATE, semitones=12.0).duration_ratio == pytest.approx(1.0)


def test_pitch_up_moves_the_dominant_frequency_by_four_semitones() -> None:
    """A 440 Hz tone must come out at 440*2**(4/12), fed in awkward slices.

    The bound is one FFT bin (24000/8192 = 2.93 Hz), which is ~10x tighter than
    the 31 Hz gap to the neighbouring semitones — so this cannot pass for a +3
    or +5 shift.
    """
    fx = _pitch_only()
    got = _feed(fx, _sine(int(RATE * 1.6)), ODD_CHUNKS)

    target = TONE_HZ * 2.0 ** (SEMITONES / 12.0)
    bin_hz = RATE / FFT_WINDOW
    peak = _dominant_hz(got)

    assert abs(peak - target) <= bin_hz
    for neighbour in (TONE_HZ * 2.0 ** (3 / 12), TONE_HZ * 2.0 ** (5 / 12)):
        assert abs(peak - neighbour) > bin_hz


@pytest.mark.parametrize("chunks", [ODD_CHUNKS, (240,), (97, 1531, 13), (4096, 61)])
def test_output_duration_equals_input_duration_whatever_the_chunking(chunks: tuple[int, ...]) -> None:
    """The whole point of D-011: pitch moves, length does not.

    The chain holds a pending tail — audio it has read but not yet emitted — so
    the statement is about total input against total output *plus* that tail.
    The bound is one analysis window, and it is not vacuous: `pending_delay` is
    computed from the two stages' own state, not from the counters this compares.
    """
    total_in = int(RATE * 3.0)
    fx = _pitch_only()
    got = _feed(fx, _sine(total_in), chunks)

    assert abs(len(got) + fx.pending_delay - total_in) <= ANALYSIS_WINDOW
    # And the raw length is close to the input's on its own, i.e. the tail is a
    # tail and not a 21 % shortfall: at +4 st round 1 returned 0.79 of the input.
    assert len(got) / total_in > 0.97
    # The tail is real and material; this is not a vacuous identity.
    assert fx.pending_delay > 0.0


def test_the_pitch_chain_stays_inside_its_latency_budget() -> None:
    """Both stages hold audio now, so the budget is stated on the live total.

    `latency_ms` is WSOLA's deterministic share; `pending_delay` adds soxr's
    filter tail and its block-buffering spike, and is what a caller feels.
    """
    fx = _pitch_only()
    assert fx.latency_ms == pytest.approx(LATENCY_MS)
    assert fx.pending_delay == pytest.approx(0.0)  # a pending tail, not a priming delay

    signal = _sine(int(RATE * 3.0))
    peak, i, k = 0.0, 0, 0
    while i < len(signal):
        size = ODD_CHUNKS[k % len(ODD_CHUNKS)]
        k += 1
        fx.process(signal[i : i + size])
        peak = max(peak, fx.pending_delay)
        i += size

    assert peak <= LATENCY_BUDGET_MS * RATE / 1000.0
    assert peak >= fx.latency_ms * RATE / 1000.0 * 0.5  # the measurement is live, not a constant


@pytest.mark.parametrize("tone_hz", [220.0, TONE_HZ, 880.0])
def test_a_sine_in_is_still_a_sine_out(tone_hz: float) -> None:
    """Quality floor: the stretch must not smear energy across the spectrum.

    A time-stretcher that splices out of phase produces a period-rate warble, and
    one that mis-searches produces doubled pitch pulses; both show up as energy
    away from the shifted fundamental and its first harmonics. The bound is
    pinned at roughly twice this implementation's worst measured value so the
    next change to the search or the window geometry has to justify itself.
    """
    fx = _pitch_only()
    got = _feed(fx, _sine(int(RATE * 2.0), freq=tone_hz), ODD_CHUNKS)

    assert _thd_proxy(got, tone_hz * 2.0 ** (SEMITONES / 12.0)) < THD_PROXY_MAX


def test_chunked_and_whole_pitch_output_carry_the_same_envelope_and_no_seams() -> None:
    """Chunking must not be audible: same loudness contour, no splice clicks.

    A similarity search is free to make different (equally valid) choices under
    different buffering, so sample-exactness is not the contract — these two
    measurements are. The RMS profile catches level drift or dropouts; the
    largest sample-to-sample step catches a click stitched in at a chunk seam,
    which would stand out against the single-shot signal's own steepest slope.
    """
    signal = _sine(int(RATE * 1.6))
    chunked = _feed(_pitch_only(), signal, ODD_CHUNKS)
    whole = _pitch_only().process(signal).reshape(-1)

    assert abs(len(chunked) - len(whole)) <= ANALYSIS_WINDOW

    chunked_rms, whole_rms = _rms_profile(chunked), _rms_profile(whole)
    blocks = min(len(chunked_rms), len(whole_rms))
    assert blocks >= 10
    assert np.max(np.abs(chunked_rms[:blocks] - whole_rms[:blocks]) / whole_rms[:blocks]) < 0.10

    assert _max_jump(chunked) <= 2 * _max_jump(whole)


def test_chunking_does_not_change_the_pitch_output_at_all() -> None:
    """Stronger than the contract above, and worth protecting while it holds.

    Every WSOLA decision is keyed to an absolute position on the input timeline,
    never to where a chunk happened to end, so this implementation is exactly
    chunk-invariant. If a future change trades that away, weaken this test
    deliberately — do not delete the envelope/seam test above with it.
    """
    signal = _sine(int(RATE * 1.6))
    whole = _pitch_only().process(signal).reshape(-1)

    for chunks in (ODD_CHUNKS, (97, 1531, 13), (240,)):
        chunked = _feed(_pitch_only(), signal, chunks)
        assert len(chunked) == len(whole)
        assert _lsb_diff(chunked, whole) == 0


def test_a_chunk_shorter_than_the_lookahead_comes_back_empty() -> None:
    """The chain now buffers, so an early chunk can legitimately emit nothing.

    Downstream (`openai_realtime.emit`) hands whatever comes back to the output
    resampler, which is fine with an empty buffer — but the shape contract still
    has to hold, or the (1, N) reshape downstream would fail.
    """
    fx = _pitch_only()
    first = fx.process(_sine(240).reshape(1, -1))

    assert first.shape == (1, 0)
    assert first.dtype == np.int16
    # It is buffering, not discarding: enough further input and the audio appears.
    assert fx.process(_sine(4800).reshape(1, -1)).size > 0


def test_process_preserves_the_channel_first_shape() -> None:
    """The model's (1, N) PCM must come back as (1, N'), int16."""
    fx = _pitch_only()
    out = fx.process(_sine(4800).reshape(1, -1))

    assert out.ndim == 2 and out.shape[0] == 1
    assert out.dtype == np.int16


def test_process_accepts_an_empty_chunk() -> None:
    """A zero-length chunk must not raise or corrupt the stream state."""
    fx = _pitch_only()
    assert fx.process(np.zeros((1, 0), dtype=np.int16)).size == 0


# --------------------------------------------------------------------------
# Feedback comb — the D-017 replacement for the tremolo's "robot" character
# --------------------------------------------------------------------------


def _comb_only(**overrides: float) -> VoiceFX:
    """Build a chain with only the comb live, at the shipped comb defaults."""
    kwargs: dict[str, float] = dict(IDLE)
    kwargs.update({"comb_ms": DEFAULT_COMB_MS, "comb_mix": DEFAULT_COMB_MIX})
    kwargs.update(overrides)
    return VoiceFX(RATE, **kwargs)  # type: ignore[arg-type]


def test_comb_impulse_response_matches_the_difference_equation() -> None:
    """`y[n] = x[n] + g*y[n-D]`, blended `mix` against dry, at the shipped defaults.

    D-017 chose 4 ms / g 0.45 / mix 0.35 for a reason that is entirely in these
    numbers: D = 96 at 24 kHz puts resonances every 250 Hz, and g = 0.45 gives
    8.4 dB of peak-to-null ripple — audible metallic colour, no envelope
    modulation. An impulse is the direct way to read both back off the filter:
    the k-th echo lands at `k*D` with amplitude `mix * g**k`.
    """
    delay = int(round(RATE * DEFAULT_COMB_MS / 1000.0))
    assert delay == 96
    assert RATE / delay == pytest.approx(250.0)

    amplitude = 20000
    impulse = np.zeros(delay * 4 + 16, dtype=np.int16)
    impulse[0] = amplitude

    got = _comb_only().process(impulse).reshape(-1).astype(np.float64)

    # Dry + wet coincide at n=0, so the impulse itself comes back at unity.
    assert got[0] == pytest.approx(amplitude, abs=2)
    for echo in (1, 2, 3):
        expected = amplitude * DEFAULT_COMB_MIX * DEFAULT_COMB_FEEDBACK**echo
        assert got[echo * delay] == pytest.approx(expected, abs=2)
        # And the echo is a *delay line*, not a smear: the samples around it are silent.
        assert abs(got[echo * delay - 1]) <= 1
        assert abs(got[echo * delay + 1]) <= 1


def test_comb_is_a_streaming_filter_not_a_per_chunk_one() -> None:
    """The delay line must cross chunk boundaries, or every chunk restarts the resonance.

    The negative control is the whole point: a fresh comb per chunk still
    produces comb-filtered audio, so only sample-exact agreement with the
    single-shot run proves the state is carried.
    """
    signal = _sine(int(RATE * 1.6))
    whole = _comb_only().process(signal).reshape(-1)
    chunked = _feed(_comb_only(), signal, ODD_CHUNKS)

    assert len(chunked) == len(signal) == len(whole)
    assert _lsb_diff(chunked, whole) == 0

    naive, i, k = [], 0, 0
    while i < len(signal):
        size = ODD_CHUNKS[k % len(ODD_CHUNKS)]
        k += 1
        naive.append(_comb_only().process(signal[i : i + size]).reshape(-1))
        i += size
    assert _lsb_diff(np.concatenate(naive), whole) > 100


def test_comb_colours_the_spectrum_without_moving_the_envelope() -> None:
    """The claim that justified choosing a comb over more AM, stated as a test.

    A comb is linear and time-invariant, so it must change *which* frequencies
    come out (that is the metallic character) while leaving the loudness contour
    alone (that is why it does not buzz). A tremolo does the exact opposite.
    """
    signal = _sine(int(RATE * 1.6), freq=250.0)
    dry = VoiceFX(RATE, **IDLE).process(signal).reshape(-1)  # type: ignore[arg-type]
    wet = _comb_only().process(signal).reshape(-1)

    # 250 Hz sits on a comb peak, so the tone is boosted, not merely altered.
    assert _lsb_diff(wet, dry) > 1000
    assert _rms(wet) > _rms(dry)

    # ... and the syllable-scale envelope is untouched, which is the anti-buzz claim.
    dry_profile, wet_profile = _rms_profile(dry), _rms_profile(wet)
    blocks = min(len(dry_profile), len(wet_profile))
    assert blocks >= 10
    ratio = wet_profile[:blocks] / dry_profile[:blocks]
    assert float(np.max(ratio) - np.min(ratio)) < 0.05


def test_comb_mix_and_delay_are_both_off_switches() -> None:
    """Either knob at zero must leave the stage out of the chain entirely."""
    assert _comb_only(comb_mix=0.0)._comb is None
    assert _comb_only(comb_ms=0.0)._comb is None
    assert _comb_only(comb_feedback=0.0)._comb is None
    assert _comb_only()._comb is not None


def test_comb_resonance_spacing_follows_the_delay() -> None:
    """The one number the operator tunes by: spacing = rate / delay."""
    comb = _comb_only(comb_ms=8.0)._comb
    assert comb is not None
    assert comb.delay == 192
    assert comb.spacing_hz == pytest.approx(125.0)


# --------------------------------------------------------------------------
# AM / tremolo — off by default, phase continuous, honest about what it is
# --------------------------------------------------------------------------

# A legal carrier: high enough that its sidebands resolve as timbre instead of
# beating inside one critical band. 55 Hz — what shipped — is now unreachable.
CARRIER_HZ = 300.0


def test_zero_semitones_does_not_construct_a_pitch_stage() -> None:
    """st=0 still bypasses the pitch stage: neither half is built, so no latency."""
    fx = VoiceFX(RATE, semitones=0.0, ringmod_hz=CARRIER_HZ, ringmod_mix=0.25)

    assert fx._pitch is None
    assert fx._stretch is None
    assert fx.duration_ratio == pytest.approx(1.0)
    assert fx.pending_delay == pytest.approx(0.0)
    assert fx.latency_ms == pytest.approx(0.0)

    n = 4800
    assert len(fx.process(_sine(n)).reshape(-1)) == n


def test_am_is_chunk_independent_and_phase_continuous() -> None:
    """The carrier phase must carry across chunk boundaries, not restart at each one.

    Restarting the phase per chunk would still produce modulated audio, so the
    only proof is that arbitrary chunking reproduces the single-shot result
    sample for sample.
    """
    settings: dict[str, float] = dict(IDLE, ringmod_hz=CARRIER_HZ, ringmod_mix=0.3)
    signal = _sine(int(RATE * 1.6))
    chunked = _feed(VoiceFX(RATE, **settings), signal, ODD_CHUNKS)  # type: ignore[arg-type]
    whole = VoiceFX(RATE, **settings).process(signal).reshape(-1)  # type: ignore[arg-type]

    assert len(chunked) == len(signal) == len(whole)
    assert _lsb_diff(chunked, whole) <= 2

    # Negative control: a per-chunk phase reset is a materially different signal.
    naive, i, k = [], 0, 0
    while i < len(signal):
        size = ODD_CHUNKS[k % len(ODD_CHUNKS)]
        k += 1
        naive.append(VoiceFX(RATE, **settings).process(signal[i : i + size]).reshape(-1))  # type: ignore[arg-type]
        i += size
    assert _lsb_diff(np.concatenate(naive), whole) > 1000


def test_am_changes_the_signal_and_respects_its_mix() -> None:
    """mix=0 leaves the carrier out entirely; mix>0 must audibly alter the tone."""
    signal = _sine(4800)
    dry = VoiceFX(RATE, **dict(IDLE, ringmod_hz=CARRIER_HZ, ringmod_mix=0.0)).process(signal)  # type: ignore[arg-type]
    wet = VoiceFX(RATE, **dict(IDLE, ringmod_hz=CARRIER_HZ, ringmod_mix=1.0)).process(signal)  # type: ignore[arg-type]

    assert dry is signal  # mix 0 leaves the whole chain idle -> the passthrough path
    assert _lsb_diff(wet.reshape(-1), signal) > 1000


def test_am_below_half_mix_is_tremolo_not_ring_modulation() -> None:
    """The mislabel that caused the bug, pinned so nobody re-reads the code as ring mod.

    `x*(1-m) + x*sin*m` is `x * [(1-m) + m*sin]`: an interpolation whose
    multiplier spans `[1-2m, 1]`. Below m = 0.5 it never reaches zero, so the
    carrier is never suppressed and the sign is never inverted — the definition
    of tremolo. Real ring modulation only begins at m = 1.
    """
    signal = _sine(4800, amp=0.5)
    tremolo = VoiceFX(RATE, **dict(IDLE, ringmod_hz=CARRIER_HZ, ringmod_mix=0.25)).process(signal)  # type: ignore[arg-type]
    ring = VoiceFX(RATE, **dict(IDLE, ringmod_hz=CARRIER_HZ, ringmod_mix=1.0)).process(signal)  # type: ignore[arg-type]

    modulated, original = tremolo.reshape(-1).astype(np.float64), signal.astype(np.float64)
    loud = np.abs(original) > 0.2 * 32767
    # Never inverts, and never attenuates by more than 1-2m = 0.5 -> it is an envelope.
    assert float(np.min(modulated[loud] / original[loud])) == pytest.approx(0.5, abs=0.02)
    assert float(np.max(modulated[loud] / original[loud])) == pytest.approx(1.0, abs=0.02)

    # At full mix the sign really does invert — that is what ring modulation is.
    assert float(np.min(ring.reshape(-1).astype(np.float64)[loud] / original[loud])) < -0.9


def test_am_off_at_zero_hz() -> None:
    """VOICEFX_RINGMOD_HZ=0 means "no modulation", not "DC carrier"."""
    signal = _sine(4800)
    got = VoiceFX(RATE, **dict(IDLE, ringmod_mix=1.0)).process(signal)  # type: ignore[arg-type]

    assert got is signal  # nothing left to do -> the pre-task path


# --------------------------------------------------------------------------
# Soft-knee saturator — the D-017 replacement for the hard clip
# --------------------------------------------------------------------------


def _ceiling(dbfs: float = DEFAULT_CEILING_DBFS) -> float:
    """Return a dBFS ceiling as a linear amplitude."""
    return 10.0 ** (dbfs / 20.0)


@pytest.mark.parametrize("ceiling_dbfs", [0.0, DEFAULT_CEILING_DBFS, -3.0, -12.0])
@pytest.mark.parametrize("knee", [MIN_KNEE, DEFAULT_KNEE, MAX_KNEE])
def test_soft_knee_is_bounded_monotonic_and_linear_below_the_knee(ceiling_dbfs: float, knee: float) -> None:
    """The three properties the whole anti-clipping argument rests on.

    Bounded, because `|tanh| < 1` strictly, so the ceiling is approached and
    never reached — which is what makes the trailing `np.clip(-1, 1)` a genuine
    no-op rather than a second clipper. Monotonic and continuous, because a
    transfer curve with a kink is exactly what hard clipping is and exactly what
    radiates the high-order harmonics that read as static. Identity below the
    knee, because quiet speech must not be touched at all.
    """
    fx = VoiceFX(RATE, ceiling_dbfs=ceiling_dbfs, knee=knee, **IDLE)  # type: ignore[arg-type]
    ceiling = _ceiling(ceiling_dbfs)
    knee_level = knee * ceiling

    ramp = np.linspace(-4.0, 4.0, 200001, dtype=np.float32)
    out = fx._saturate(ramp)

    # `knee + span*tanh(x)` is strictly below `knee + span == ceiling` in exact
    # arithmetic; in float32 the last ulp can land on it, which is harmless and
    # is the bound that actually matters — see the clip assertion below.
    assert float(np.abs(out).max()) <= float(np.float32(ceiling))
    assert bool(np.all(np.diff(out) >= 0.0))

    # The operative consequence: the chain's trailing `np.clip(-1, 1)` is a no-op.
    np.testing.assert_array_equal(np.clip(out, -1.0, 1.0), out)

    linear = np.abs(ramp) <= knee_level
    np.testing.assert_array_equal(out[linear], ramp[linear])

    # Continuity at the knee: no step where the two regimes meet.
    around = np.abs(np.abs(ramp) - knee_level) < 1e-3
    assert float(np.abs(out[around] - ramp[around]).max()) < 1e-4


def test_soft_knee_bounds_even_absurd_overdrive() -> None:
    """1000x over the ceiling must still land under it — a bound, not a soft suggestion."""
    fx = VoiceFX(RATE, **IDLE)  # type: ignore[arg-type]
    extreme = np.array([-1000.0, -12.0, -1.0, 0.0, 1.0, 12.0, 1000.0], dtype=np.float32)

    out = fx._saturate(extreme)

    assert float(np.abs(out).max()) <= float(np.float32(_ceiling()))
    assert out.dtype == np.float32
    np.testing.assert_array_equal(np.clip(out, -1.0, 1.0), out)


def test_the_trailing_clip_is_a_no_op_backstop() -> None:
    """The chain's own ceiling must land below full scale, so the clip never engages.

    If this fails the saturator is not doing its job and `np.clip` has silently
    become the overload control again — which is the D-010 design D-017 removed.
    """
    signal = _sine(4800, amp=1.0)
    loud = VoiceFX(RATE, semitones=0.0, comb_ms=0.0, ringmod_hz=0.0, gain_db=MAX_GAIN_DB).process(signal)

    peak = int(np.abs(loud.reshape(-1)).max())
    assert peak < 32767  # never railed
    assert peak / 32767.0 < _ceiling() + CEILING_TOLERANCE
    # And the ceiling was actually reached for, not merely left unapproached.
    assert peak / 32767.0 > _ceiling() * 0.99


# --------------------------------------------------------------------------
# Makeup gain — now a safe loudness control rather than the crunch knob
# --------------------------------------------------------------------------


def test_default_gain_lifts_a_quiet_sine_by_five_db() -> None:
    """The default +5 dB makeup gain must actually be +5 dB of amplitude.

    Measured below the knee, where the chain is exactly linear: above it the
    saturator legitimately compresses, and the gain would no longer be readable
    as a plain ratio. The reference is the fully idle chain, which is the
    byte-exact passthrough.
    """
    signal = _sine(RATE // 2, amp=0.2)
    unity = VoiceFX(RATE, **IDLE).process(signal).reshape(-1)  # type: ignore[arg-type]
    louder = VoiceFX(RATE, **dict(IDLE, gain_db=DEFAULT_GAIN_DB)).process(signal).reshape(-1)  # type: ignore[arg-type]

    assert len(louder) == len(unity)
    assert _db(_rms(louder) / _rms(unity)) == pytest.approx(DEFAULT_GAIN_DB, abs=0.1)
    # 0.2 FS at +5 dB is 0.356 — well under the 0.668 knee, so nothing was bent.
    assert int(np.abs(louder).max()) < int(DEFAULT_KNEE * _ceiling() * 32767)


def test_gain_is_applied_after_the_colour_stages() -> None:
    """Gain must scale the coloured signal, not a stage input.

    Every stage before it is linear in its input, so an equivalent pre-stage
    gain would also be +6 dB overall — the discriminator is that the gained
    output is an exact scalar multiple of the unity-gain output, sample for
    sample, which only holds if the gain is the last linear thing that happens.
    """
    signal = _sine(4800, amp=0.15)
    settings: dict[str, float] = dict(IDLE, comb_ms=DEFAULT_COMB_MS, comb_mix=DEFAULT_COMB_MIX)
    unity = VoiceFX(RATE, **settings).process(signal).reshape(-1).astype(np.float64)  # type: ignore[arg-type]
    louder = VoiceFX(RATE, **dict(settings, gain_db=6.0)).process(signal).reshape(-1).astype(np.float64)  # type: ignore[arg-type]

    assert np.abs(louder - unity * (10.0 ** (6.0 / 20.0))).max() <= 2


def test_maximum_gain_compresses_instead_of_clipping() -> None:
    """+12 dB on a full-scale input is the case that used to destroy 37 % of samples.

    D-017's central claim: the gain knob is safe across its whole documented
    range now. The old chain pinned 36.7 % of a full-scale sine at the int16
    rail at this drive; this one bends every sample toward a -1 dBFS ceiling it
    never reaches, so nothing rails and nothing can wrap.
    """
    signal = _sine(4800, amp=1.0)
    loud = _pitch_only(gain_db=MAX_GAIN_DB).process(signal).reshape(-1)

    assert loud.dtype == np.int16
    assert int(np.abs(loud).max()) < 32767
    assert int(loud.min()) > -32767
    assert int(np.sum(np.abs(loud) / 32767.0 >= _ceiling() + CEILING_TOLERANCE)) == 0


def test_the_soft_knee_makes_far_less_high_order_distortion_than_a_hard_clip() -> None:
    """Why a knee and not just a lower clip point — the mechanism behind "static".

    A hard clip's transfer curve has a discontinuous first derivative, so its
    harmonic series decays only as 1/k and it radiates high-order odd harmonics
    plus dense intermodulation between every pair of partials. The tanh knee is
    smooth, so the series collapses after the first few. Measured on a 250 Hz
    tone driven the way the shipped chain drives it: the knee puts ~20x less
    energy above 4 kHz, where a small speaker's harshness lives.
    """
    fx = VoiceFX(RATE, **IDLE)  # type: ignore[arg-type]
    ceiling = _ceiling()
    t = np.arange(24000) / RATE
    driven = (ceiling * 10.0 ** (5.0 / 20.0) * np.sin(2 * np.pi * 250.0 * t)).astype(np.float32)

    knee = fx._saturate(driven)
    clipped = np.clip(driven, -ceiling, ceiling).astype(np.float32)

    assert _fizz(knee) < _fizz(clipped) / 5.0
    assert _fizz(knee) < 1e-4
    # Not bought with loudness: the knee stays within a fraction of a dB.
    assert _db(_rms(knee) / _rms(clipped)) > -0.5


def test_disabled_chain_ignores_the_gain_entirely() -> None:
    """A disabled filter stays byte-identical even with a gain configured."""
    fx = VoiceFX(RATE, enabled=False, gain_db=MAX_GAIN_DB)
    pcm = _sine(480).reshape(1, -1)

    assert fx.process(pcm) is pcm


def test_gain_alone_now_wakes_the_chain() -> None:
    """D-017 reversal: a non-zero gain is a real change, so it can no longer be free.

    Under D-011 the gain rode along with the other stages and was silently
    dropped when they were all bypassed. That was defensible while the gain's
    only companion was a hard clip; with a saturator behind it the gain is a
    usable standalone loudness control, and silently ignoring it would be a
    configuration that lies.
    """
    signal = _sine(4800, amp=0.2)
    fx = VoiceFX(RATE, **dict(IDLE, gain_db=6.0))  # type: ignore[arg-type]

    out = fx.process(signal)

    assert out is not signal
    assert _db(_rms(out.reshape(-1)) / _rms(signal)) == pytest.approx(6.0, abs=0.1)


def test_a_wholly_idle_chain_is_a_byte_exact_passthrough() -> None:
    """The bypass contract D-017 narrowed: every stage idle, and only then.

    This is the path that matters for `VOICEFX_ENABLED=false` parity — no float
    round-trip, no copy, the caller's own array back.
    """
    pcm = _sine(4800).reshape(1, -1)
    idle = VoiceFX(RATE, **IDLE)  # type: ignore[arg-type]

    assert idle.process(pcm) is pcm

    # ... and each stage on its own is enough to leave that path.
    for waking in (
        {"semitones": 4.0},
        {"comb_ms": 4.0, "comb_mix": 0.35},
        {"ringmod_hz": 300.0, "ringmod_mix": 0.3},
        {"gain_db": 3.0},
    ):
        fx = VoiceFX(RATE, **dict(IDLE, **waking))  # type: ignore[arg-type]
        assert fx.process(pcm) is not pcm, waking


# --------------------------------------------------------------------------
# Whole-chain invariants — the D-017 acceptance evidence
# --------------------------------------------------------------------------


def test_the_whole_chain_is_byte_exactly_chunk_invariant() -> None:
    """Every stage is either stateless or exact-state, so chunking cannot be audible.

    This matters more than it looks: `response.output_audio.delta` sizes are
    chosen by OpenAI and vary within a single reply, so a chunk-size-dependent
    stage would itself be an artefact source that no amount of tuning could fix.
    It is also the measured reason a lookahead limiter was rejected in favour of
    the memoryless soft knee — the limiter's output moved by -12 dBFS between
    chunk sizes.
    """
    pcm = _peak_normalized_pcm()
    reference = _feed(VoiceFX(RATE), pcm, (2400,))

    for size in REALTIME_CHUNKS:
        got = _feed(VoiceFX(RATE), pcm, (size,))
        assert len(got) == len(reference), size
        assert _lsb_diff(got, reference) == 0, size

    # Mixed, coprime sizes too — a real stream is not a constant chunk size.
    mixed = _feed(VoiceFX(RATE), pcm, ODD_CHUNKS)
    assert _lsb_diff(mixed, reference) == 0


def test_the_default_chain_never_clips_a_loud_speech_signal() -> None:
    """The acceptance check the rebuild exists for (D-017).

    A -1 dBFS speech-shaped signal is what the model actually sends: real TTS is
    peak-normalized near full scale. Through the shipped chain it produced
    **3.29 %** destroyed samples and a +0.10 dBTP output that clipped a second
    time inside the downstream 24 k -> 16 k resampler. Through this one it must
    produce none, and must not be quieter for it — the loudness complaint is
    what put the +5 dB gain there in the first place.
    """
    pcm = _peak_normalized_pcm(dbfs=-1.0)
    ceiling = _ceiling()

    out = _feed(VoiceFX(RATE), pcm, (2400,))

    assert int(np.sum(np.abs(out) / 32767.0 >= ceiling + CEILING_TOLERANCE)) == 0
    assert int(np.sum(np.abs(out) >= 32767)) == 0
    assert _dbfs(out) >= ACCEPT_RMS_DBFS

    # Louder than the chain it replaces, not merely cleaner: same signal, old settings.
    old = _feed(
        VoiceFX(
            RATE,
            semitones=4.0,
            comb_mix=0.0,
            ringmod_hz=55.0,
            ringmod_mix=0.25,
            gain_db=5.0,
            knee=MAX_KNEE,
            ceiling_dbfs=MAX_CEILING_DBFS,
        ),
        pcm,
        (2400,),
    )
    assert int(np.sum(np.abs(old) >= 32767)) > 0  # the old chain really did destroy samples
    assert _dbfs(out) > _dbfs(old)


@pytest.mark.parametrize("input_dbfs", [-20.0, -12.0, -6.0, -3.0, -1.0, 0.0])
def test_the_default_chain_is_bounded_at_every_input_level(input_dbfs: float) -> None:
    """Level-independence is what makes the fix robust to the real TTS crest factor.

    The measurements behind D-017 all used a synthetic phantom, so the argument
    cannot rest on getting one signal's crest factor right. It rests on the
    saturator being a bound: from -20 dBFS to full scale, nothing over the
    ceiling.
    """
    out = _feed(VoiceFX(RATE), _peak_normalized_pcm(dbfs=input_dbfs), (2400,))

    assert int(np.sum(np.abs(out) / 32767.0 >= _ceiling() + CEILING_TOLERANCE)) == 0
    assert int(np.sum(np.abs(out) >= 32767)) == 0


# --------------------------------------------------------------------------
# Reset
# --------------------------------------------------------------------------


def test_reset_restores_a_fresh_instance() -> None:
    """After reset the filter must behave exactly like one that never ran.

    Both pitch stages have to be cleared for this: the stretcher's input buffer,
    overlap-add tail, correlation template and synthesis timeline, plus soxr's
    filter tail. Leaving any one of them behind changes this output.
    """
    block = _sine(4800)  # ~4x the pitch chain's pending tail
    fresh = VoiceFX(RATE, semitones=SEMITONES).process(block).reshape(-1)

    fx = VoiceFX(RATE, semitones=SEMITONES)
    fx.process(block)
    fx.process(block)
    fx.reset()

    assert fx.pending_delay == pytest.approx(0.0)
    after_reset = fx.process(block).reshape(-1)
    assert len(after_reset) == len(fresh)
    assert _lsb_diff(after_reset, fresh) <= 2


def test_without_reset_the_previous_utterance_bleeds_through() -> None:
    """Negative control for the test above: the state really is carried otherwise."""
    block = _sine(4800)
    fresh = VoiceFX(RATE, semitones=SEMITONES).process(block).reshape(-1)

    bleeding = VoiceFX(RATE, semitones=SEMITONES)
    bleeding.process(block)
    assert _lsb_diff(bleeding.process(block).reshape(-1), fresh) > 1000


def test_reset_is_harmless_on_a_disabled_filter() -> None:
    """The handler resets unconditionally; a disabled filter must tolerate it."""
    fx = VoiceFX(RATE, enabled=False)
    fx.reset()
    assert fx.pending_delay == pytest.approx(0.0)


def test_barge_in_reset_empties_the_comb_delay_line() -> None:
    """The comb holds up to 20 ms of the interrupted utterance; reset must drop it.

    Stated as a mutation rather than a round trip, because a round trip would
    pass if `reset` merely happened to clear the *pitch* stages: poison the
    delay line directly with a loud, obviously-wrong state, reset, and require
    the next chunk to come back identical to a comb that never ran. This is the
    state `openai_realtime._reset_output_pipeline` drops on `speech_started`.
    """
    fx = _comb_only()
    comb = fx._comb
    assert comb is not None

    block = _sine(2400, amp=0.3)
    clean = _comb_only().process(block).reshape(-1)

    comb._zi[:] = 0.9  # a delay line full of near-full-scale garbage
    fx.reset()

    assert float(np.abs(comb._zi).max()) == 0.0
    assert _lsb_diff(fx.process(block).reshape(-1), clean) == 0

    # Negative control: without the reset the poison is audible.
    poisoned = _comb_only()
    assert poisoned._comb is not None
    poisoned._comb._zi[:] = 0.9
    assert _lsb_diff(poisoned.process(block).reshape(-1), clean) > 1000


def test_reset_clears_every_stage_of_the_full_default_chain() -> None:
    """The whole shipped chain, reset, must be indistinguishable from a fresh one."""
    block = _sine(4800)
    fresh = VoiceFX(RATE).process(block).reshape(-1)

    fx = VoiceFX(RATE)
    fx.process(block)
    fx.process(block)
    fx.reset()

    after = fx.process(block).reshape(-1)
    assert len(after) == len(fresh)
    assert _lsb_diff(after, fresh) == 0


# --------------------------------------------------------------------------
# Environment
# --------------------------------------------------------------------------


def test_from_env_is_disabled_by_default() -> None:
    """Unset means off: the shipped default path is byte-for-byte the pre-task one."""
    fx = VoiceFX.from_env(RATE)

    assert fx.enabled is False
    pcm = _sine(480).reshape(1, -1)
    assert fx.process(pcm) is pcm


def test_from_env_logs_the_settled_configuration(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The chain is otherwise silent, so this line is the only run-time proof it is on."""
    monkeypatch.setenv("VOICEFX_ENABLED", "true")
    monkeypatch.setenv("VOICEFX_PITCH_SEMITONES", "4")

    with caplog.at_level(logging.INFO, logger=VOICEFX_LOGGER):
        VoiceFX.from_env(RATE)

    assert "VoiceFX enabled" in caplog.text
    assert "+4.0 st" in caplog.text
    # D-011: which pitch mode is running, and what it costs, are the two things
    # an on-robot log has to answer — the round-1 chain sounded the same but
    # played 26 % fast, so "pitched" alone does not identify the build.
    assert "WSOLA" in caplog.text
    assert "duration preserved" in caplog.text
    assert "35.0 ms" in caplog.text
    # The makeup gain is the knob the operator tunes for loudness; it must be visible.
    assert "gain" in caplog.text
    assert "+5.0 dB" in caplog.text
    # D-017: the operator verifies the *deployed chain* from this one line, so
    # every stage that shapes the sound has to be in it — including the one that
    # is off, because "AM off" is the fix and a silent absence would not say so.
    assert "comb 4.0 ms/g 0.45/mix 0.35 (250 Hz spacing)" in caplog.text
    assert "AM off" in caplog.text
    assert "soft knee at 0.75 of a -1.0 dBFS ceiling" in caplog.text


def test_from_env_logs_the_am_stage_when_it_is_switched_on(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """An operator who enabled the AM stage must be able to see that from the log."""
    monkeypatch.setenv("VOICEFX_ENABLED", "true")
    monkeypatch.setenv("VOICEFX_RINGMOD_HZ", "300")
    monkeypatch.setenv("VOICEFX_RINGMOD_MIX", "0.12")
    monkeypatch.setenv("VOICEFX_COMB_MIX", "0")

    with caplog.at_level(logging.INFO, logger=VOICEFX_LOGGER):
        VoiceFX.from_env(RATE)

    assert "AM 300 Hz at 0.12 mix" in caplog.text
    assert "comb off" in caplog.text


def test_from_env_logs_when_the_filter_is_off(caplog: pytest.LogCaptureFixture) -> None:
    """A disabled chain says so, so a plain-voice run is not mistaken for a filtered one."""
    with caplog.at_level(logging.INFO, logger=VOICEFX_LOGGER):
        VoiceFX.from_env(RATE)

    assert "VoiceFX disabled" in caplog.text


def test_from_env_reads_every_knob(monkeypatch: pytest.MonkeyPatch) -> None:
    """All ten knobs come from the environment, each at a legal non-default value."""
    monkeypatch.setenv("VOICEFX_ENABLED", "true")
    monkeypatch.setenv("VOICEFX_PITCH_SEMITONES", "7")
    monkeypatch.setenv("VOICEFX_COMB_MS", "2.0")
    monkeypatch.setenv("VOICEFX_COMB_FEEDBACK", "0.55")
    monkeypatch.setenv("VOICEFX_COMB_MIX", "0.5")
    monkeypatch.setenv("VOICEFX_RINGMOD_HZ", "300")
    monkeypatch.setenv("VOICEFX_RINGMOD_MIX", "0.4")
    monkeypatch.setenv("VOICEFX_GAIN_DB", "8")
    monkeypatch.setenv("VOICEFX_CEILING_DBFS", "-3")
    monkeypatch.setenv("VOICEFX_KNEE", "0.6")

    fx = VoiceFX.from_env(RATE)

    assert fx.enabled is True
    assert fx.semitones == pytest.approx(7.0)
    assert fx.comb_ms == pytest.approx(2.0)
    assert fx.comb_feedback == pytest.approx(0.55)
    assert fx.comb_mix == pytest.approx(0.5)
    assert fx.ringmod_hz == pytest.approx(300.0)
    assert fx.ringmod_mix == pytest.approx(0.4)
    assert fx.gain_db == pytest.approx(8.0)
    assert fx.ceiling_dbfs == pytest.approx(-3.0)
    assert fx.knee == pytest.approx(0.6)


def test_from_env_defaults_are_the_tuned_starting_point(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enabling the filter with nothing else set gives the D-017 "cute robot" chain.

    The two zeros are the fix: the AM stage that produced the buzz is off, and
    it stays off unless an operator deliberately turns it on at a legal carrier.
    """
    monkeypatch.setenv("VOICEFX_ENABLED", "1")

    fx = VoiceFX.from_env(RATE)

    assert fx.enabled is True
    assert fx.semitones == pytest.approx(DEFAULT_SEMITONES) == pytest.approx(5.0)
    assert fx.comb_ms == pytest.approx(4.0)
    assert fx.comb_feedback == pytest.approx(0.45)
    assert fx.comb_mix == pytest.approx(0.35)
    assert fx.ringmod_hz == pytest.approx(0.0)
    assert fx.ringmod_mix == pytest.approx(0.0)
    assert fx.gain_db == pytest.approx(5.0)
    assert fx.ceiling_dbfs == pytest.approx(-1.0)
    assert fx.knee == pytest.approx(0.75)


def test_from_env_malformed_value_warns_and_uses_the_default(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A bad .env line must degrade the knob, never abort the session."""
    monkeypatch.setenv("VOICEFX_ENABLED", "1")
    monkeypatch.setenv("VOICEFX_PITCH_SEMITONES", "very-high")

    with caplog.at_level(logging.WARNING, logger=ENVPARSE_LOGGER):
        fx = VoiceFX.from_env(RATE)

    assert fx.semitones == pytest.approx(DEFAULT_SEMITONES)
    assert "VOICEFX_PITCH_SEMITONES" in caplog.text


def test_from_env_clamps_an_out_of_range_mix(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A mix of 1.5 is not a legal blend; it must clamp to 1.0 with a warning."""
    monkeypatch.setenv("VOICEFX_ENABLED", "1")
    monkeypatch.setenv("VOICEFX_RINGMOD_MIX", "1.5")

    with caplog.at_level(logging.WARNING, logger=ENVPARSE_LOGGER):
        fx = VoiceFX.from_env(RATE)

    assert fx.ringmod_mix == pytest.approx(1.0)
    assert "VOICEFX_RINGMOD_MIX" in caplog.text


def test_from_env_malformed_gain_warns_and_uses_the_default(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A bad gain line must degrade to +5 dB, not silence or deafen the robot."""
    monkeypatch.setenv("VOICEFX_ENABLED", "1")
    monkeypatch.setenv("VOICEFX_GAIN_DB", "louder-please")

    with caplog.at_level(logging.WARNING, logger=ENVPARSE_LOGGER):
        fx = VoiceFX.from_env(RATE)

    assert fx.gain_db == pytest.approx(DEFAULT_GAIN_DB)
    assert "VOICEFX_GAIN_DB" in caplog.text


def test_env_example_documents_every_knob() -> None:
    """An undocumented knob is an unusable one; the duration note is part of the contract."""
    from pathlib import Path

    text = (Path(__file__).resolve().parent.parent / ".env.example").read_text(encoding="utf-8")

    for name in _ENV_KNOBS:
        assert name in text
    assert "semitones" in text
    # D-011 removed the tempo side-effect; the file must not still promise it.
    assert "duration" in text
    assert "faster" not in text
    # D-017: three paste-able settings blocks stand in for a preset mechanism,
    # so they are the documented way to A/B the character. And the stale 55 Hz
    # example value must be gone — it is the value that caused the bug.
    for block in ("cute robot (default)", "more metallic", "plain pitched voice"):
        assert block in text
    assert "VOICEFX_RINGMOD_HZ=55" not in text
    assert "VOICEFX_RINGMOD_MIX=0.25" not in text


@pytest.mark.parametrize(
    ("name", "raw", "attribute", "expected"),
    [
        ("VOICEFX_PITCH_SEMITONES", "99", "semitones", MAX_SEMITONES),
        ("VOICEFX_PITCH_SEMITONES", "-3", "semitones", 0.0),
        ("VOICEFX_COMB_MS", "99", "comb_ms", MAX_COMB_MS),
        ("VOICEFX_COMB_MS", "0.1", "comb_ms", MIN_COMB_MS),
        ("VOICEFX_COMB_MS", "-1", "comb_ms", 0.0),
        ("VOICEFX_COMB_FEEDBACK", "0.99", "comb_feedback", MAX_COMB_FEEDBACK),
        ("VOICEFX_COMB_FEEDBACK", "-1", "comb_feedback", 0.0),
        ("VOICEFX_COMB_MIX", "1.5", "comb_mix", 1.0),
        ("VOICEFX_COMB_MIX", "-1", "comb_mix", 0.0),
        ("VOICEFX_RINGMOD_HZ", "99999", "ringmod_hz", MAX_RINGMOD_HZ),
        ("VOICEFX_RINGMOD_HZ", "-1", "ringmod_hz", 0.0),
        ("VOICEFX_RINGMOD_MIX", "-1", "ringmod_mix", 0.0),
        ("VOICEFX_RINGMOD_MIX", "1.5", "ringmod_mix", 1.0),
        ("VOICEFX_GAIN_DB", "99", "gain_db", MAX_GAIN_DB),
        ("VOICEFX_GAIN_DB", "-99", "gain_db", MIN_GAIN_DB),
        ("VOICEFX_CEILING_DBFS", "6", "ceiling_dbfs", MAX_CEILING_DBFS),
        ("VOICEFX_CEILING_DBFS", "-99", "ceiling_dbfs", MIN_CEILING_DBFS),
        ("VOICEFX_KNEE", "1.5", "knee", MAX_KNEE),
        ("VOICEFX_KNEE", "0", "knee", MIN_KNEE),
    ],
)
def test_from_env_clamps_every_numeric_knob(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    name: str,
    raw: str,
    attribute: str,
    expected: float,
) -> None:
    """Each numeric knob has a documented range, clamps into it, and says so."""
    monkeypatch.setenv("VOICEFX_ENABLED", "1")
    monkeypatch.setenv(name, raw)

    with caplog.at_level(logging.WARNING):
        fx = VoiceFX.from_env(RATE)

    assert getattr(fx, attribute) == pytest.approx(expected)
    assert name in caplog.text


@pytest.mark.parametrize("raw", ["55", "1", "70", "149.9"])
def test_from_env_refuses_a_carrier_inside_the_roughness_band(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, raw: str
) -> None:
    """The knob that caused the bug can no longer be set to the value that caused it.

    55 Hz sat at 0.956 of peak psychoacoustic roughness. The whole 20-150 Hz
    band is refused rather than clamped up, because clamping would hand the
    operator a carrier they never asked for; warn-and-default is the convention
    every other malformed knob in this file follows.
    """
    monkeypatch.setenv("VOICEFX_ENABLED", "1")
    monkeypatch.setenv("VOICEFX_RINGMOD_HZ", raw)

    with caplog.at_level(logging.WARNING, logger=VOICEFX_LOGGER):
        fx = VoiceFX.from_env(RATE)

    assert fx.ringmod_hz == pytest.approx(0.0)
    assert "VOICEFX_RINGMOD_HZ" in caplog.text
    assert "roughness" in caplog.text


@pytest.mark.parametrize("raw", ["0", "150", "300", "4000"])
def test_from_env_accepts_every_legal_carrier(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    """The legal set is {0} and [150, 4000]; both endpoints and the off switch survive."""
    monkeypatch.setenv("VOICEFX_ENABLED", "1")
    monkeypatch.setenv("VOICEFX_RINGMOD_HZ", raw)

    assert VoiceFX.from_env(RATE).ringmod_hz == pytest.approx(float(raw))
    assert MIN_RINGMOD_HZ == 150.0


def test_from_env_comb_zero_is_off_not_clamped_up(monkeypatch: pytest.MonkeyPatch) -> None:
    """0 ms is the comb's documented off switch, and must not become a 0.5 ms comb."""
    monkeypatch.setenv("VOICEFX_ENABLED", "1")
    monkeypatch.setenv("VOICEFX_COMB_MS", "0")

    fx = VoiceFX.from_env(RATE)

    assert fx.comb_ms == pytest.approx(0.0)
    assert fx._comb is None
