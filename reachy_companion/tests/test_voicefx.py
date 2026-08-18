"""Behavioural contract for the cute-robot voice filter (D-010, D-011).

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
"""

import logging

import numpy as np
import pytest

from reachy_companion.audio.voicefx import MAX_GAIN_DB, MIN_GAIN_DB, DEFAULT_GAIN_DB, VoiceFX


RATE = 24000
SEMITONES = 4.0
TONE_HZ = 440.0
FFT_WINDOW = 8192

# The chunk sizes every streaming test feeds: coprime with the analysis hop and
# with each other, so no frame boundary can line up with a chunk boundary.
ODD_CHUNKS = (479, 501, 1024, 137)
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

VOICEFX_LOGGER = "reachy_companion.audio.voicefx"
ENVPARSE_LOGGER = "reachy_companion.audio.envparse"

_ENV_KNOBS = (
    "VOICEFX_ENABLED",
    "VOICEFX_PITCH_SEMITONES",
    "VOICEFX_RINGMOD_HZ",
    "VOICEFX_RINGMOD_MIX",
    "VOICEFX_GAIN_DB",
)


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
    fx = VoiceFX(RATE, semitones=SEMITONES, ringmod_hz=0.0)

    assert fx.duration_ratio == pytest.approx(1.0)
    assert VoiceFX(RATE, semitones=12.0).duration_ratio == pytest.approx(1.0)


def test_pitch_up_moves_the_dominant_frequency_by_four_semitones() -> None:
    """A 440 Hz tone must come out at 440*2**(4/12), fed in awkward slices.

    The bound is one FFT bin (24000/8192 = 2.93 Hz), which is ~10x tighter than
    the 31 Hz gap to the neighbouring semitones — so this cannot pass for a +3
    or +5 shift.
    """
    fx = VoiceFX(RATE, semitones=SEMITONES, ringmod_hz=0.0)
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
    fx = VoiceFX(RATE, semitones=SEMITONES, ringmod_hz=0.0)
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
    fx = VoiceFX(RATE, semitones=SEMITONES, ringmod_hz=0.0)
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
    fx = VoiceFX(RATE, semitones=SEMITONES, ringmod_hz=0.0, gain_db=0.0)
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
    chunked = _feed(VoiceFX(RATE, semitones=SEMITONES, ringmod_hz=0.0), signal, ODD_CHUNKS)
    whole = VoiceFX(RATE, semitones=SEMITONES, ringmod_hz=0.0).process(signal).reshape(-1)

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
    whole = VoiceFX(RATE, semitones=SEMITONES, ringmod_hz=0.0).process(signal).reshape(-1)

    for chunks in (ODD_CHUNKS, (97, 1531, 13), (240,)):
        chunked = _feed(VoiceFX(RATE, semitones=SEMITONES, ringmod_hz=0.0), signal, chunks)
        assert len(chunked) == len(whole)
        assert _lsb_diff(chunked, whole) == 0


def test_a_chunk_shorter_than_the_lookahead_comes_back_empty() -> None:
    """The chain now buffers, so an early chunk can legitimately emit nothing.

    Downstream (`openai_realtime.emit`) hands whatever comes back to the output
    resampler, which is fine with an empty buffer — but the shape contract still
    has to hold, or the (1, N) reshape downstream would fail.
    """
    fx = VoiceFX(RATE, semitones=SEMITONES, ringmod_hz=0.0)
    first = fx.process(_sine(240).reshape(1, -1))

    assert first.shape == (1, 0)
    assert first.dtype == np.int16
    # It is buffering, not discarding: enough further input and the audio appears.
    assert fx.process(_sine(4800).reshape(1, -1)).size > 0


def test_process_preserves_the_channel_first_shape() -> None:
    """The model's (1, N) PCM must come back as (1, N'), int16."""
    fx = VoiceFX(RATE, semitones=SEMITONES, ringmod_hz=0.0)
    out = fx.process(_sine(4800).reshape(1, -1))

    assert out.ndim == 2 and out.shape[0] == 1
    assert out.dtype == np.int16


def test_process_accepts_an_empty_chunk() -> None:
    """A zero-length chunk must not raise or corrupt the stream state."""
    fx = VoiceFX(RATE, semitones=SEMITONES, ringmod_hz=0.0)
    assert fx.process(np.zeros((1, 0), dtype=np.int16)).size == 0


# --------------------------------------------------------------------------
# Ring modulator — zero latency, phase continuous, hard pitch bypass
# --------------------------------------------------------------------------


def test_zero_semitones_does_not_construct_a_pitch_stage() -> None:
    """st=0 is a HARD bypass: neither stage is built, so there is no latency at all."""
    fx = VoiceFX(RATE, semitones=0.0, ringmod_hz=55.0, ringmod_mix=0.25)

    assert fx._pitch is None
    assert fx._stretch is None
    assert fx.duration_ratio == pytest.approx(1.0)
    assert fx.pending_delay == pytest.approx(0.0)
    assert fx.latency_ms == pytest.approx(0.0)

    n = 4800
    assert len(fx.process(_sine(n)).reshape(-1)) == n


def test_ringmod_is_chunk_independent_and_phase_continuous() -> None:
    """The carrier phase must carry across chunk boundaries, not restart at each one.

    Restarting the phase per chunk would still produce ring-modulated audio, so
    the only proof is that arbitrary chunking reproduces the single-shot result
    sample for sample.
    """
    signal = _sine(int(RATE * 1.6))
    chunked = _feed(VoiceFX(RATE, semitones=0.0, ringmod_hz=55.0), signal, (479, 501, 1024, 137))
    whole = VoiceFX(RATE, semitones=0.0, ringmod_hz=55.0).process(signal).reshape(-1)

    assert len(chunked) == len(signal) == len(whole)
    assert _lsb_diff(chunked, whole) <= 2

    # Negative control: a per-chunk phase reset is a materially different signal.
    naive = []
    i, k, sizes = 0, 0, (479, 501, 1024, 137)
    while i < len(signal):
        size = sizes[k % len(sizes)]
        k += 1
        naive.append(VoiceFX(RATE, semitones=0.0, ringmod_hz=55.0).process(signal[i : i + size]).reshape(-1))
        i += size
    assert _lsb_diff(np.concatenate(naive), whole) > 1000


def test_ringmod_changes_the_signal_and_respects_its_mix() -> None:
    """mix=0 leaves the carrier out entirely; mix>0 must audibly alter the tone."""
    signal = _sine(4800)
    dry = VoiceFX(RATE, semitones=0.0, ringmod_hz=55.0, ringmod_mix=0.0).process(signal).reshape(-1)
    wet = VoiceFX(RATE, semitones=0.0, ringmod_hz=55.0, ringmod_mix=1.0).process(signal).reshape(-1)

    assert _lsb_diff(dry, signal) <= 2
    assert _lsb_diff(wet, signal) > 1000


def test_ringmod_off_at_zero_hz() -> None:
    """VOICEFX_RINGMOD_HZ=0 means "no ring modulation", not "DC carrier"."""
    signal = _sine(4800)
    got = VoiceFX(RATE, semitones=0.0, ringmod_hz=0.0, ringmod_mix=1.0).process(signal)

    assert got is signal  # nothing left to do -> the pre-task path


# --------------------------------------------------------------------------
# Makeup gain — recovers the loudness the rest of the chain costs
# --------------------------------------------------------------------------


def test_default_gain_lifts_a_half_scale_sine_by_five_db() -> None:
    """The default +5 dB makeup gain must actually be +5 dB of amplitude.

    Measured against the identical chain at unity gain, because a chain with
    *no* active stage is a hard identity bypass and never sees the gain at all.
    The +5 dB reference is the operator fix for a quiet robot: the 0.25 ring-mod
    mix alone costs ~2.3 dB of RMS.
    """
    signal = _sine(RATE // 2, amp=0.5)
    unity = VoiceFX(RATE, semitones=0.0, gain_db=0.0).process(signal).reshape(-1)
    louder = VoiceFX(RATE, semitones=0.0).process(signal).reshape(-1)

    assert len(louder) == len(unity)
    assert _db(_rms(louder) / _rms(unity)) == pytest.approx(DEFAULT_GAIN_DB, abs=0.5)
    # Headroom sanity: half scale at +5 dB is 0.89 FS, so nothing should be railed.
    assert int(np.abs(louder).max()) < 32767


def test_gain_is_applied_after_the_ring_modulator() -> None:
    """Gain must scale the finished chain, not a stage input.

    Ring modulation is linear in its input, so an equivalent pre-stage gain would
    also be +5 dB overall — the discriminator is that the gained output must be
    an exact scalar multiple of the unity-gain output, sample for sample.
    """
    signal = _sine(4800, amp=0.4)
    unity = VoiceFX(RATE, semitones=0.0, gain_db=0.0).process(signal).reshape(-1).astype(np.float64)
    louder = VoiceFX(RATE, semitones=0.0, gain_db=6.0).process(signal).reshape(-1).astype(np.float64)

    assert np.abs(louder - unity * (10.0 ** (6.0 / 20.0))).max() <= 2


def test_full_scale_at_maximum_gain_clips_to_the_rails_without_wrapping() -> None:
    """+12 dB on a full-scale input must saturate, never fold over to the far rail.

    The float-domain `np.clip(-1, 1)` that precedes the int16 scaling is the
    overload protection; this is the test that says so. A wrap would show up as
    samples near -32768 where the unity-gain signal was strongly positive.
    """
    signal = _sine(4800, amp=1.0)
    unity = VoiceFX(RATE, semitones=SEMITONES, ringmod_hz=0.0, gain_db=0.0).process(signal).reshape(-1)
    loud = VoiceFX(RATE, semitones=SEMITONES, ringmod_hz=0.0, gain_db=MAX_GAIN_DB).process(signal).reshape(-1)

    assert loud.dtype == np.int16
    assert int(loud.max()) == 32767
    assert int(loud.min()) == -32767  # the -32768 floor stays unreachable
    # Saturation really happened; this is not a vacuous bound.
    assert float(np.mean(np.abs(loud) == 32767)) > 0.5

    expected = np.clip(unity.astype(np.float64) * (10.0 ** (MAX_GAIN_DB / 20.0)), -32767.0, 32767.0)
    assert np.abs(loud.astype(np.float64) - expected).max() <= 8


def test_disabled_chain_ignores_the_gain_entirely() -> None:
    """A disabled filter stays byte-identical even with a gain configured."""
    fx = VoiceFX(RATE, enabled=False, gain_db=MAX_GAIN_DB)
    pcm = _sine(480).reshape(1, -1)

    assert fx.process(pcm) is pcm


def test_gain_alone_does_not_wake_a_bypassed_chain() -> None:
    """With every stage bypassed the pre-filter path is kept, gain or not."""
    signal = _sine(4800)
    fx = VoiceFX(RATE, semitones=0.0, ringmod_hz=0.0, gain_db=MAX_GAIN_DB)

    assert fx.process(signal) is signal


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


def test_from_env_logs_when_the_filter_is_off(caplog: pytest.LogCaptureFixture) -> None:
    """A disabled chain says so, so a plain-voice run is not mistaken for a filtered one."""
    with caplog.at_level(logging.INFO, logger=VOICEFX_LOGGER):
        VoiceFX.from_env(RATE)

    assert "VoiceFX disabled" in caplog.text


def test_from_env_reads_every_knob(monkeypatch: pytest.MonkeyPatch) -> None:
    """All five knobs come from the environment, each at a non-default value."""
    monkeypatch.setenv("VOICEFX_ENABLED", "true")
    monkeypatch.setenv("VOICEFX_PITCH_SEMITONES", "7")
    monkeypatch.setenv("VOICEFX_RINGMOD_HZ", "80")
    monkeypatch.setenv("VOICEFX_RINGMOD_MIX", "0.4")
    monkeypatch.setenv("VOICEFX_GAIN_DB", "8")

    fx = VoiceFX.from_env(RATE)

    assert fx.enabled is True
    assert fx.semitones == pytest.approx(7.0)
    assert fx.ringmod_hz == pytest.approx(80.0)
    assert fx.ringmod_mix == pytest.approx(0.4)
    assert fx.gain_db == pytest.approx(8.0)


def test_from_env_defaults_are_the_tuned_starting_point(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enabling the filter with nothing else set gives the D-010 starting values."""
    monkeypatch.setenv("VOICEFX_ENABLED", "1")

    fx = VoiceFX.from_env(RATE)

    assert fx.enabled is True
    assert fx.semitones == pytest.approx(4.0)
    assert fx.ringmod_hz == pytest.approx(55.0)
    assert fx.ringmod_mix == pytest.approx(0.25)
    assert fx.gain_db == pytest.approx(5.0)


def test_from_env_malformed_value_warns_and_uses_the_default(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A bad .env line must degrade the knob, never abort the session."""
    monkeypatch.setenv("VOICEFX_ENABLED", "1")
    monkeypatch.setenv("VOICEFX_PITCH_SEMITONES", "very-high")

    with caplog.at_level(logging.WARNING, logger=ENVPARSE_LOGGER):
        fx = VoiceFX.from_env(RATE)

    assert fx.semitones == pytest.approx(4.0)
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


@pytest.mark.parametrize(
    ("name", "raw", "attribute", "expected"),
    [
        ("VOICEFX_PITCH_SEMITONES", "99", "semitones", 12.0),
        ("VOICEFX_PITCH_SEMITONES", "-3", "semitones", 0.0),
        ("VOICEFX_RINGMOD_HZ", "99999", "ringmod_hz", 2000.0),
        ("VOICEFX_RINGMOD_MIX", "-1", "ringmod_mix", 0.0),
        ("VOICEFX_GAIN_DB", "99", "gain_db", MAX_GAIN_DB),
        ("VOICEFX_GAIN_DB", "-99", "gain_db", MIN_GAIN_DB),
    ],
)
def test_from_env_clamps_every_numeric_knob(
    monkeypatch: pytest.MonkeyPatch, name: str, raw: str, attribute: str, expected: float
) -> None:
    """Each numeric knob has a documented range and clamps into it."""
    monkeypatch.setenv("VOICEFX_ENABLED", "1")
    monkeypatch.setenv(name, raw)

    assert getattr(VoiceFX.from_env(RATE), attribute) == pytest.approx(expected)
