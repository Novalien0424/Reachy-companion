"""Local Silero VAD: upstream's tests (8b37f31 / b731ea1) plus the reader-thread feed (ours).

The upstream tests are ported verbatim apart from the module path; none of
them reaches the network (`httpx.MockTransport`) or loads a real model
(`ort.InferenceSession` is faked).
"""

import hashlib
from typing import Any
from pathlib import Path
from functools import partial
from unittest.mock import MagicMock

import httpx
import numpy as np
import pytest

import reachy_companion.audio.local_vad as vad_mod
from reachy_companion.streaming import audio_to_int16
from reachy_companion.audio.local_vad import SileroVAD, LocalSpeechFeed


@pytest.fixture
def model_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, MagicMock]:
    """Isolate the model cache and upstream downloads from the network."""
    responses = {
        "src/silero_vad/data/silero_vad_16k_op15.onnx": b"test-model",
        "LICENSE": b"test-license",
    }

    def respond(request: httpx.Request) -> httpx.Response:
        prefix = "https://raw.githubusercontent.com/snakers4/silero-vad/7e30209a3e901f9842f81b225f3e93d8199902b1/"
        assert str(request.url).startswith(prefix)
        return httpx.Response(200, content=responses[str(request.url).removeprefix(prefix)])

    download = MagicMock(side_effect=respond)
    monkeypatch.setattr(vad_mod, "cached_assets_path", lambda *_args: tmp_path)
    monkeypatch.setattr(
        vad_mod,
        "_MODEL_FILES",
        {
            name: (upstream_path, hashlib.sha256(responses[upstream_path]).hexdigest())
            for name, (upstream_path, _checksum) in vad_mod._MODEL_FILES.items()
        },
    )
    monkeypatch.setattr(vad_mod.httpx, "Client", partial(httpx.Client, transport=httpx.MockTransport(download)))
    monkeypatch.setattr(vad_mod.ort, "InferenceSession", MagicMock())
    return tmp_path, download


def test_partial_frames_keep_context_and_reset_clears_history(
    monkeypatch: pytest.MonkeyPatch, model_cache: tuple[Path, MagicMock]
) -> None:
    """Partial and (N, 1) frames wait for a full chunk, reset() clears model history, and only 16 kHz is accepted."""
    session = MagicMock()
    session.run.side_effect = lambda _outputs, inputs: [
        np.array([[0.1]], dtype=np.float32),
        np.ones_like(inputs["state"]),
    ]
    monkeypatch.setattr(vad_mod.ort, "InferenceSession", lambda *_args, **_kwargs: session)
    detector = SileroVAD()
    half_chunk = np.full(256, 16384, dtype=np.int16)

    detector.process(half_chunk, 16000)
    session.run.assert_not_called()
    detector.process(half_chunk.reshape(-1, 1), 16000)
    assert detector.speech_probability == pytest.approx(0.1)
    first_inputs = session.run.call_args.args[1]
    np.testing.assert_array_equal(first_inputs["input"][:, :64], 0.0)
    np.testing.assert_array_equal(first_inputs["input"][:, 64:], 0.5)

    detector.process(half_chunk, 16000)
    detector.reset()
    assert detector.speech_probability == 0.0
    detector.process(half_chunk, 16000)
    detector.process(half_chunk, 16000)
    reset_inputs = session.run.call_args.args[1]
    np.testing.assert_array_equal(reset_inputs["state"], 0.0)
    np.testing.assert_array_equal(reset_inputs["input"][:, :64], 0.0)

    with pytest.raises(ValueError, match="16 kHz"):
        detector.process(half_chunk, 48000)


def test_first_start_downloads_model_and_license_then_reuses_cache_offline(
    model_cache: tuple[Path, MagicMock],
) -> None:
    """First use downloads both files; later starts work without a connection."""
    cache, download = model_cache
    SileroVAD()
    assert download.call_count == 2
    assert (cache / "silero_vad_16k_op15.onnx").read_bytes() == b"test-model"
    assert (cache / "LICENSE").read_bytes() == b"test-license"
    download.reset_mock()
    download.side_effect = httpx.ConnectError("offline")
    SileroVAD()
    download.assert_not_called()
    assert {path.name for path in cache.iterdir()} == {"silero_vad_16k_op15.onnx", "LICENSE"}


def test_corrupted_cached_model_is_replaced(
    model_cache: tuple[Path, MagicMock], caplog: pytest.LogCaptureFixture
) -> None:
    """A corrupted model is downloaded again before inference starts."""
    cache, download = model_cache
    SileroVAD()
    download.reset_mock()
    (cache / "silero_vad_16k_op15.onnx").write_bytes(b"corrupted")
    SileroVAD()
    assert download.call_count == 1
    assert (cache / "silero_vad_16k_op15.onnx").read_bytes() == b"test-model"
    assert "Cached Silero file failed checksum verification" in caplog.text


@pytest.mark.parametrize("failure", ["offline", "checksum", "license"])
def test_failed_download_does_not_load_unverified_model(model_cache: tuple[Path, MagicMock], failure: str) -> None:
    """Download failures leave no incomplete files and never start inference."""
    cache, download = model_cache
    expected_error: type[Exception]
    if failure == "offline":
        download.side_effect = httpx.ConnectError("offline")
        expected_error = httpx.ConnectError
    elif failure == "checksum":
        download.side_effect = lambda request: httpx.Response(200, content=b"wrong-model")
        expected_error = ValueError
    else:
        download.side_effect = [
            httpx.Response(200, content=b"test-model"),
            httpx.Response(404),
        ]
        expected_error = httpx.HTTPStatusError
    with pytest.raises(expected_error):
        SileroVAD()
    vad_mod.ort.InferenceSession.assert_not_called()
    assert not (cache / "LICENSE").exists()
    assert {path.name for path in cache.iterdir()} <= {"silero_vad_16k_op15.onnx"}


def test_barge_in_candidate_follows_speech_during_playback_only(
    monkeypatch: pytest.MonkeyPatch, model_cache: tuple[Path, MagicMock]
) -> None:
    """A candidate starts at 0.6, holds down to 0.45, clears after 64 ms below that, and never outside playback."""
    probabilities = iter([0.5, 0.7, 0.5, 0.1, 0.1, 0.7, 0.7])
    session = MagicMock()
    session.run.side_effect = lambda *_args: [
        np.array([[next(probabilities)]], dtype=np.float32),
        np.zeros((2, 1, 128), dtype=np.float32),
    ]
    monkeypatch.setattr(vad_mod.ort, "InferenceSession", lambda *_args, **_kwargs: session)
    detector = SileroVAD()
    chunk = np.zeros(512, dtype=np.int16)
    candidates = []
    for _ in range(6):
        detector.process(chunk, 16000, detect_barge_in=True)
        candidates.append(detector.barge_in_candidate)
    assert candidates == [False, True, True, True, False, True]
    detector.process(chunk, 16000)
    assert not detector.barge_in_candidate


@pytest.mark.parametrize("input_samples", [173, 512, 4096])
def test_model_resets_preserve_audio_and_context_during_speech(
    monkeypatch: pytest.MonkeyPatch, model_cache: tuple[Path, MagicMock], input_samples: int
) -> None:
    """Idle and segment resets preserve every PCM sample and context throughout active speech and pauses."""
    probabilities = iter([*([0.1] * 16), *([0.8] * 5), *([0.1] * 12), 0.8, *([0.1] * 13), *([0.8] * 5), 0.1, 0.1])
    chunks: list[np.ndarray] = []
    states: list[np.ndarray] = []
    contexts: list[np.ndarray] = []

    def infer(_outputs: object, inputs: dict[str, np.ndarray]) -> list[np.ndarray]:
        chunks.append(inputs["input"][:, 64:].copy())
        states.append(inputs["state"].copy())
        contexts.append(inputs["input"][:, :64].copy())
        return [np.array([[next(probabilities)]], dtype=np.float32), np.ones_like(inputs["state"])]

    session = MagicMock()
    session.run.side_effect = infer
    monkeypatch.setattr(vad_mod.ort, "InferenceSession", lambda *_args, **_kwargs: session)
    detector = SileroVAD()
    audio = np.arange(53 * 512 + 137, dtype=np.int16)
    listening = [
        detector.process(audio[offset : offset + input_samples], 16000)
        for offset in range(0, audio.size, input_samples)
    ]
    assert listening[-1]
    assert session.run.call_count == 53
    assert detector.process(np.zeros(375, dtype=np.int16), 16000)

    expected = np.concatenate((audio, np.zeros(375, dtype=np.int16))).astype(np.float32) / 32768.0
    np.testing.assert_array_equal(np.concatenate(chunks, axis=1).ravel(), expected)
    for index, (state, context) in enumerate(zip(states, contexts)):
        if index in (0, 16, 47):
            np.testing.assert_array_equal(state, 0.0)
            np.testing.assert_array_equal(context, 0.0)
        else:
            np.testing.assert_array_equal(state, 1.0)
            np.testing.assert_array_equal(context, chunks[index - 1][:, -64:])

    if input_samples == 512:
        assert not any(listening[:20])
        assert all(listening[20:46])
        assert not any(listening[46:51])
        assert all(listening[51:])


def test_short_bursts_are_ignored_and_uncertain_audio_counts_as_silence(
    monkeypatch: pytest.MonkeyPatch, model_cache: tuple[Path, MagicMock]
) -> None:
    """Knocks under 160 ms never vote, and hum below the speech threshold still ends listening."""
    probabilities = iter([*([0.8] * 4), 0.1, *([0.8] * 5), *([0.4] * 30)])
    states: list[np.ndarray] = []

    def infer(_outputs: object, inputs: dict[str, np.ndarray]) -> list[np.ndarray]:
        states.append(inputs["state"].copy())
        return [np.array([[next(probabilities)]], dtype=np.float32), np.ones_like(inputs["state"])]

    session = MagicMock()
    session.run.side_effect = infer
    monkeypatch.setattr(vad_mod.ort, "InferenceSession", lambda *_args, **_kwargs: session)
    detector = SileroVAD()
    frame = np.full(512, 16384, dtype=np.int16)
    listening = [detector.process(frame, 16000) for _ in range(40)]

    assert not any(listening[:9])
    assert all(listening[9:22])
    assert not any(listening[22:])
    assert [index for index, state in enumerate(states) if not np.any(state)] == [0, 23, 39]


# --- ours ----------------------------------------------------------------------


def test_the_model_is_cached_under_this_apps_namespace(
    monkeypatch: pytest.MonkeyPatch, model_cache: tuple[Path, MagicMock]
) -> None:
    """The cache entry `scripts/preload_assets.py` warms is the one the app reads: ours, at the pinned revision."""
    cache, _download = model_cache
    asked: list[tuple[Any, ...]] = []

    def cached_assets_path(*args: Any) -> Path:
        asked.append(args)
        return cache

    monkeypatch.setattr(vad_mod, "cached_assets_path", cached_assets_path)
    SileroVAD()
    assert asked == [("reachy_companion", "silero-vad", "7e30209a3e901f9842f81b225f3e93d8199902b1")]


class _FakeDetector:
    """Stand-in for `SileroVAD`: replays speech states and records what it was fed."""

    def __init__(self, *states: bool) -> None:
        self._states = iter(states)
        self.fed: list[tuple[np.ndarray, int]] = []
        self.resets = 0

    def process(self, audio: np.ndarray, sample_rate: int, *, detect_barge_in: bool = False) -> bool:
        assert detect_barge_in is False  # local barge-in ducking is not ported
        self.fed.append((audio.copy(), sample_rate))
        return next(self._states)

    def reset(self) -> None:
        self.resets += 1


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def _feed(detector: _FakeDetector, clock: _Clock | None = None) -> LocalSpeechFeed:
    return LocalSpeechFeed(detector, clock=clock or _Clock())  # type: ignore[arg-type]


@pytest.mark.parametrize("layout", ["channels_last", "channels_first", "mono"])
def test_feed_hears_what_the_server_hears(layout: str) -> None:
    """An SDK frame reaches the detector as the handler's `receive` sends it: first channel, int16."""
    left = np.linspace(-0.5, 0.5, 320, dtype=np.float32)
    frame = {
        "channels_last": np.stack((left, -left), axis=1),
        "channels_first": np.stack((left, -left), axis=0),
        "mono": left,
    }[layout]
    detector = _FakeDetector(False)

    _feed(detector).feed(frame, 16000)

    (audio, rate), = detector.fed
    assert rate == 16000
    assert audio.dtype == np.int16
    np.testing.assert_array_equal(audio.reshape(-1), audio_to_int16(left))


def test_feed_reports_edges_only() -> None:
    """Each change of speech state is reported once; a steady state reports nothing."""
    detector = _FakeDetector(False, True, True, True, False, False)
    feed = _feed(detector)
    frame = np.zeros((320, 2), dtype=np.float32)

    edges = [feed.feed(frame, 16000) for _ in range(6)]

    assert edges == [None, True, None, None, False, None]


def test_silence_ends_speech_once_and_is_free_while_already_silent() -> None:
    """Muted or warming-up audio drops the detector's history and its vote, once."""
    detector = _FakeDetector(True)
    feed = _feed(detector)
    assert feed.feed(np.zeros(320, dtype=np.float32), 16000) is True

    assert feed.silence() is False
    assert feed.speaking is False
    assert detector.resets == 1
    assert feed.silence() is None
    assert detector.resets == 1  # nothing fed since, so nothing to reset


def test_a_microphone_gap_drops_the_vote_and_the_history() -> None:
    """Upstream's 500 ms: no audio for longer than that ends speech and resets the detector."""
    clock = _Clock()
    detector = _FakeDetector(True, True, True)
    feed = _feed(detector, clock)
    frame = np.zeros(320, dtype=np.float32)
    assert feed.feed(frame, 16000) is True

    clock.now += 0.4
    assert feed.idle() is None
    clock.now += 0.2
    assert feed.idle() is False
    assert detector.resets == 1
    assert feed.idle() is None

    # A gap the idle reads never saw (one long blocking read) resets on the next frame.
    assert feed.feed(frame, 16000) is True
    clock.now += 0.6
    assert feed.feed(frame, 16000) is None  # reset, then speech again: no net change to report
    assert detector.resets == 2


def test_feed_drives_the_real_detector_end_to_end(
    monkeypatch: pytest.MonkeyPatch, model_cache: tuple[Path, MagicMock]
) -> None:
    """Stereo float32 SDK frames through the real `SileroVAD`: speech after 160 ms, silence after 400 ms."""
    probabilities = iter([*([0.8] * 5), *([0.1] * 13)])
    session = MagicMock()
    session.run.side_effect = lambda _outputs, inputs: [
        np.array([[next(probabilities)]], dtype=np.float32),
        np.ones_like(inputs["state"]),
    ]
    monkeypatch.setattr(vad_mod.ort, "InferenceSession", lambda *_args, **_kwargs: session)
    feed = LocalSpeechFeed(SileroVAD())
    frame = np.full((512, 2), 0.25, dtype=np.float32)

    edges = [feed.feed(frame, 16000) for _ in range(18)]

    assert edges.index(True) == 4
    assert edges.index(False) == 17
    assert [edge for edge in edges if edge is not None] == [True, False]
