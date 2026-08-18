"""Recognizer-core tests: alignment maths, matching rules, and the SFace contract.

Everything here except `test_sface_contract` runs with **no model at all** — the
alignment is pure numpy and the matching rules read the JSON store, so the
maths that decides who Reachy thinks you are is checkable offline.
"""

from pathlib import Path

import numpy as np
import pytest
from numpy.typing import NDArray

from reachy_mini.vision.face_detector import Face
from reachy_companion.faces import upsert_face
from reachy_companion.face_id import (
    ALIGNED_SIZE,
    DEFAULT_MARGIN,
    REFERENCE_POINTS,
    DEFAULT_MATCH_THRESHOLD,
    FaceRecognizer,
    cosine,
    align_face,
    sface_is_cached,
)


def _face_at(points: NDArray[np.float64]) -> Face:
    """Build a `Face` whose three landmarks sit at `points` (right eye, left eye, nose)."""
    xs, ys = points[:, 0], points[:, 1]
    return Face(
        bbox=(float(xs.min()), float(ys.min()), float(np.ptp(xs)), float(np.ptp(ys))),
        right_eye=(float(points[0, 0]), float(points[0, 1])),
        left_eye=(float(points[1, 0]), float(points[1, 1])),
        nose=(float(points[2, 0]), float(points[2, 1])),
    )


def _smooth_disc_frame(size: int = ALIGNED_SIZE) -> NDArray[np.uint8]:
    """Return a `size`x`size` BGR test frame: a smooth pattern inside a centered disc.

    Rotation-invariant support matters here. A full-bleed random frame loses its
    corners to the frame edge under any rotation, so a rotate/unrotate round trip
    would be compared against content that no longer exists. Keeping every
    non-black pixel inside a disc of radius 40 means the roll test measures
    resampling error, not cropping.

    The pattern is one continuous function of *canonical* (112-frame)
    coordinates, sampled on the requested grid. A 224 frame is therefore the
    exact same image at twice the scale, which is what lets the scale test
    compare a 224 source against the 112 reference pixel for pixel.
    """
    zoom = size / ALIGNED_SIZE
    ys, xs = (np.mgrid[0:size, 0:size].astype(np.float64) / zoom)
    dx, dy = xs - 55.5, ys - 55.5
    radius = np.hypot(dx, dy)
    pattern = 128.0 + 90.0 * np.sin(dx / 13.0) * np.cos(dy / 11.0)
    falloff = np.clip((40.0 - radius) / 8.0, 0.0, 1.0)
    base = pattern * falloff
    frame = np.stack([base, base * 0.8 + 20.0 * falloff, base * 0.6 + 40.0 * falloff], axis=-1)
    return np.clip(frame, 0, 255).astype(np.uint8)


def _rotation_matrix(degrees: float) -> NDArray[np.float64]:
    """Return the 2x2 rotation matrix for `degrees` counter-clockwise."""
    theta = np.deg2rad(degrees)
    return np.array([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]], dtype=np.float64)


def rotate_bilinear(frame: NDArray[np.uint8], degrees: float) -> NDArray[np.uint8]:
    """Rotate `frame` about its center with bilinear sampling — numpy only, no cv2.

    This is the test's own reference implementation, deliberately independent of
    `align_face`: if both shared a warp helper, the roll test would only prove
    that the helper is self-consistent.
    """
    height, width = frame.shape[:2]
    center = np.array([(width - 1) / 2.0, (height - 1) / 2.0])
    rot = _rotation_matrix(degrees)
    ys, xs = np.mgrid[0:height, 0:width].astype(np.float64)
    dst = np.stack([xs, ys], axis=-1) - center
    src = dst @ rot + center  # inverse map: rot.T applied to a row vector is `vec @ rot`
    return _sample_bilinear(frame, src[..., 0], src[..., 1])


def _sample_bilinear(
    frame: NDArray[np.uint8],
    sx: NDArray[np.float64],
    sy: NDArray[np.float64],
) -> NDArray[np.uint8]:
    """Bilinearly sample `frame` at float coordinates, zero-filling outside."""
    height, width = frame.shape[:2]
    x0 = np.floor(sx).astype(np.int64)
    y0 = np.floor(sy).astype(np.int64)
    fx = (sx - x0)[..., None]
    fy = (sy - y0)[..., None]
    inside = (x0 >= 0) & (y0 >= 0) & (x0 + 1 < width) & (y0 + 1 < height)
    x0c = np.clip(x0, 0, width - 2)
    y0c = np.clip(y0, 0, height - 2)
    source = frame.astype(np.float64)
    top = source[y0c, x0c] * (1 - fx) + source[y0c, x0c + 1] * fx
    bottom = source[y0c + 1, x0c] * (1 - fx) + source[y0c + 1, x0c + 1] * fx
    blended = top * (1 - fy) + bottom * fy
    return np.clip(np.rint(blended * inside[..., None]), 0, 255).astype(np.uint8)


def _basis(dim: int = 128) -> NDArray[np.float32]:
    """Return the canonical orthonormal basis used to build synthetic embeddings."""
    return np.eye(dim, dtype=np.float32)


def _at_cosine(target: float, axis: NDArray[np.float32], probe: NDArray[np.float32]) -> NDArray[np.float32]:
    """Return a unit vector whose cosine with `probe` is exactly `target`."""
    vector = target * probe + np.sqrt(1.0 - target * target) * axis
    return (vector / np.linalg.norm(vector)).astype(np.float32)


# --- alignment (cv2-free) ---------------------------------------------------


def test_align_identity_is_a_plain_crop() -> None:
    """Landmarks already on the SFace reference points must map the frame through unchanged."""
    frame = _smooth_disc_frame()

    aligned = align_face(frame, _face_at(REFERENCE_POINTS.astype(np.float64)))

    assert aligned.shape == (ALIGNED_SIZE, ALIGNED_SIZE, 3)
    assert aligned.dtype == np.uint8
    assert np.abs(aligned.astype(int) - frame.astype(int)).max() <= 1


def test_align_undoes_roll() -> None:
    """A rolled head must align back onto the canonical frame, so the embedding is roll-invariant."""
    frame = _smooth_disc_frame()
    degrees = 20.0
    rotated = rotate_bilinear(frame, degrees)

    center = np.array([(ALIGNED_SIZE - 1) / 2.0, (ALIGNED_SIZE - 1) / 2.0])
    forward = _rotation_matrix(-degrees)  # image rotated by +d moves content by -d in sampling terms
    rotated_points = (REFERENCE_POINTS.astype(np.float64) - center) @ forward + center

    aligned = align_face(rotated, _face_at(rotated_points))

    assert np.abs(aligned.astype(int) - frame.astype(int)).mean() < 8


def test_align_undoes_scale() -> None:
    """A face twice the canonical size must be scaled down, not merely cropped.

    The regression this pins is a sign error in the inverse transform: sampling
    at `source * scale` instead of `source / scale` reads the top-left quarter of
    the frame and still returns a plausible-looking 112x112 crop. Landmarks at
    exactly `REFERENCE_POINTS * 2` in a 224 frame make the right answer the
    reference frame itself, pixel for pixel.
    """
    reference = _smooth_disc_frame()
    doubled = _smooth_disc_frame(ALIGNED_SIZE * 2)

    aligned = align_face(doubled, _face_at(REFERENCE_POINTS.astype(np.float64) * 2.0))

    assert np.abs(aligned.astype(int) - reference.astype(int)).max() <= 1


def test_align_returns_a_black_crop_for_an_unusable_frame() -> None:
    """The helper is public; a 1-px or 2-D frame must not raise out of it."""
    tiny = align_face(np.zeros((1, 1, 3), dtype=np.uint8), _face_at(REFERENCE_POINTS.astype(np.float64)))
    flat = align_face(np.zeros((64, 64), dtype=np.uint8), _face_at(REFERENCE_POINTS.astype(np.float64)))

    assert tiny.shape == (ALIGNED_SIZE, ALIGNED_SIZE, 3)
    assert not tiny.any()
    assert flat.shape == (ALIGNED_SIZE, ALIGNED_SIZE, 3)


def test_align_survives_degenerate_landmarks() -> None:
    """Three collinear (or identical) landmarks must not raise — identify() must stay total."""
    frame = _smooth_disc_frame()
    degenerate = np.array([[50.0, 50.0], [50.0, 50.0], [50.0, 50.0]])

    aligned = align_face(frame, _face_at(degenerate))

    assert aligned.shape == (ALIGNED_SIZE, ALIGNED_SIZE, 3)


def test_no_module_imports_cv2() -> None:
    """The app venv (dev and robot) has no cv2; an accidental import is a deploy-time crash."""
    package_root = Path(__file__).resolve().parents[1] / "src" / "reachy_companion"
    offenders = [
        path.name
        for path in package_root.rglob("*.py")
        # `.strip()` is load-bearing: our own modules import lazily *inside*
        # functions (face_id._load, _scale_face), so an indented `import cv2`
        # is the shape this guard most needs to catch.
        if any(
            line.strip().startswith(("import cv2", "from cv2"))
            for line in path.read_text(encoding="utf-8").splitlines()
        )
    ]

    assert offenders == []


# --- matching rules ---------------------------------------------------------


def test_cosine_is_scale_invariant_and_bounded() -> None:
    """Cosine must ignore magnitude and degrade to 0.0 on a zero vector rather than divide by zero."""
    a = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    b = np.array([3.0, 0.0, 0.0], dtype=np.float32)

    assert cosine(a, b) == pytest.approx(1.0)
    assert cosine(a, np.array([0.0, 2.0, 0.0], dtype=np.float32)) == pytest.approx(0.0, abs=1e-6)
    assert cosine(a, np.zeros(3, dtype=np.float32)) == 0.0


def test_match_reports_recognized_for_a_clear_winner(tmp_path: Path) -> None:
    """One person over the threshold, well clear of the runner-up, is a recognition.

    The runner-up is deliberately **not** reported here. `who_is_this` hands its
    whole result to the model, and a confident answer that also names a second,
    absent person leaks who else is enrolled while adding nothing. Only
    `ambiguous` — where the near-tie is the answer — carries it.
    """
    basis = _basis()
    probe = basis[0]
    upsert_face(tmp_path, "A", _at_cosine(0.60, basis[1], probe))
    upsert_face(tmp_path, "B", _at_cosine(0.30, basis[2], probe))

    result = FaceRecognizer(tmp_path).match(probe)

    assert result.status == "recognized"
    assert result.name == "A"
    assert result.score == pytest.approx(0.60, abs=1e-3)
    assert result.runner_up is None


def test_match_reports_ambiguous_on_a_near_tie(tmp_path: Path) -> None:
    """Two people within the margin must never be named confidently."""
    basis = _basis()
    probe = basis[0]
    upsert_face(tmp_path, "A", _at_cosine(0.52, basis[1], probe))
    upsert_face(tmp_path, "B", _at_cosine(0.50, basis[2], probe))

    result = FaceRecognizer(tmp_path).match(probe)

    assert result.status == "ambiguous"
    assert {result.name, result.runner_up} == {"A", "B"}


def test_match_reports_unknown_below_threshold_with_the_score(tmp_path: Path) -> None:
    """A stranger is `unknown`, and the best score is reported so thresholds can be calibrated."""
    basis = _basis()
    probe = basis[0]
    upsert_face(tmp_path, "A", _at_cosine(0.20, basis[1], probe))

    result = FaceRecognizer(tmp_path).match(probe)

    assert result.status == "unknown"
    assert result.name is None
    assert result.score == pytest.approx(0.20, abs=1e-3)


def test_match_uses_the_best_of_a_persons_embeddings(tmp_path: Path) -> None:
    """Per-person score is the max over their ring buffer, so one good sample is enough."""
    basis = _basis()
    probe = basis[0]
    upsert_face(tmp_path, "A", _at_cosine(0.10, basis[1], probe))
    upsert_face(tmp_path, "A", _at_cosine(0.70, basis[2], probe))

    result = FaceRecognizer(tmp_path).match(probe)

    assert result.status == "recognized"
    assert result.name == "A"
    assert result.score == pytest.approx(0.70, abs=1e-3)


def test_match_on_an_empty_store_is_unknown(tmp_path: Path) -> None:
    """Nobody enrolled means nobody recognized — and no crash on the empty file."""
    result = FaceRecognizer(tmp_path).match(_basis()[0])

    assert result.status == "unknown"
    assert result.name is None
    assert result.score is None


def test_thresholds_are_env_tunable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """On-robot calibration happens through the env, not a code change."""
    monkeypatch.setenv("FACE_MATCH_THRESHOLD", "0.15")
    monkeypatch.setenv("FACE_MATCH_MARGIN", "0.01")
    basis = _basis()
    probe = basis[0]
    upsert_face(tmp_path, "A", _at_cosine(0.20, basis[1], probe))

    recognizer = FaceRecognizer(tmp_path)

    assert (recognizer.threshold, recognizer.margin) == (0.15, 0.01)
    assert recognizer.match(probe).status == "recognized"
    assert (DEFAULT_MATCH_THRESHOLD, DEFAULT_MARGIN) == (0.40, 0.05)


# --- kill switch ------------------------------------------------------------


def test_disabled_recognizer_never_loads_a_model(tmp_path: Path) -> None:
    """FACE_MEMORY_ENABLED=0 must skip the model entirely and report `unavailable`."""
    recognizer = FaceRecognizer(tmp_path, enabled=False)
    recognizer.start_warmup()

    assert recognizer.wait_ready(0.5) is False
    identification = recognizer.identify(np.zeros((64, 64, 3), dtype=np.uint8))
    assert identification.status == "unavailable"
    assert identification.reason == "face memory is disabled"
    record, enroll_identification = recognizer.enroll(np.zeros((64, 64, 3), dtype=np.uint8), "小明")
    assert record is None
    assert enroll_identification.status == "unavailable"


def test_identify_reports_unavailable_instead_of_raising(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every failure becomes a status: identify() is total by contract."""
    recognizer = FaceRecognizer(tmp_path)
    monkeypatch.setattr(recognizer, "_ensure_loaded", lambda: (_ for _ in ()).throw(RuntimeError("boom")))

    identification = recognizer.identify(np.zeros((64, 64, 3), dtype=np.uint8))

    assert identification.status == "unavailable"
    assert identification.reason is not None
    assert "boom" in identification.reason


def test_identify_reports_unavailable_without_a_frame(tmp_path: Path) -> None:
    """A missing or malformed frame is a status, not a traceback."""
    assert FaceRecognizer(tmp_path).identify(None).status == "unavailable"
    assert FaceRecognizer(tmp_path).identify(np.zeros((4, 4), dtype=np.uint8)).status == "unavailable"


# --- detection ladder (fake detector, no model) -----------------------------


class _FakeDetector:
    """A YuNet stand-in returning a fixed face list, recording the frame it saw."""

    def __init__(self, faces: list[Face]) -> None:
        self.faces = faces
        self.seen_shape: tuple[int, ...] | None = None

    def detect(self, frame_bgr: NDArray[np.uint8]) -> list[Face]:
        self.seen_shape = frame_bgr.shape
        return self.faces


def _loaded_recognizer(tmp_path: Path, faces: list[Face]) -> tuple[FaceRecognizer, _FakeDetector]:
    """Return a recognizer wired to a fake detector and a deterministic embedder."""
    recognizer = FaceRecognizer(tmp_path)
    detector = _FakeDetector(faces)
    recognizer._detector = detector
    recognizer._sface = object()
    recognizer._loaded = True
    recognizer._load_done.set()
    return recognizer, detector


def _face_of_width(width: float) -> Face:
    """Return one face whose bbox is `width` px wide at full resolution, landmarks inside it."""
    return Face(
        bbox=(100.0, 100.0, width, width),
        right_eye=(100.0 + width * 0.3, 100.0 + width * 0.4),
        left_eye=(100.0 + width * 0.7, 100.0 + width * 0.4),
        nose=(100.0 + width * 0.5, 100.0 + width * 0.6),
    )


def test_identify_reports_no_face(tmp_path: Path) -> None:
    """An empty frame is `no_face`, with a face count of zero."""
    recognizer, _ = _loaded_recognizer(tmp_path, [])

    identification = recognizer.identify(np.zeros((720, 1280, 3), dtype=np.uint8))

    assert (identification.status, identification.face_count) == ("no_face", 0)


def test_identify_reports_multiple_faces(tmp_path: Path) -> None:
    """Two people in frame is ambiguous by construction; report the count, name nobody."""
    recognizer, _ = _loaded_recognizer(tmp_path, [_face_of_width(50.0), _face_of_width(50.0)])

    identification = recognizer.identify(np.zeros((720, 1280, 3), dtype=np.uint8))

    assert (identification.status, identification.face_count) == ("multiple_faces", 2)
    assert identification.name is None


def test_identify_reports_too_far_for_a_small_face(tmp_path: Path) -> None:
    """A face under MIN_FACE_PX at full resolution has too little detail to embed honestly."""
    # Detection runs on the half-resolution frame, so a 59 px full-res bbox is 29.5 px there.
    recognizer, _ = _loaded_recognizer(tmp_path, [_face_of_width(59.0 / 2)])

    identification = recognizer.identify(np.zeros((720, 1280, 3), dtype=np.uint8))

    assert (identification.status, identification.face_count) == ("too_far", 1)


def test_identify_detects_on_the_downscaled_frame_and_aligns_on_the_full_one(tmp_path: Path) -> None:
    """Detection is halved for speed; the alignment crop still comes from full resolution."""
    recognizer, detector = _loaded_recognizer(tmp_path, [_face_of_width(40.0)])
    aligned_from: list[tuple[int, ...]] = []
    recognizer.embed = lambda aligned: (  # type: ignore[method-assign]
        aligned_from.append(aligned.shape),
        np.zeros(128, dtype=np.float32),
    )[1]

    recognizer.identify(np.zeros((720, 1280, 3), dtype=np.uint8))

    assert detector.seen_shape == (360, 640, 3)
    assert aligned_from == [(ALIGNED_SIZE, ALIGNED_SIZE, 3)]


# --- blob contract (no model, no network) -----------------------------------


class _RecordingSession:
    """An ORT session stand-in that captures the feed dict `embed` builds."""

    def __init__(self) -> None:
        self.feed: dict[str, NDArray[np.float32]] = {}

    def run(self, _outputs: object, feed: dict[str, NDArray[np.float32]]) -> list[NDArray[np.float32]]:
        self.feed = feed
        return [np.ones((1, 128), dtype=np.float32)]


def test_embed_builds_the_blob_opencv_builds(tmp_path: Path) -> None:
    """Pin the blob: BGR->RGB, values 0-255, no mean and no scaling.

    This is the failure mode the design singled out as *silent*: feeding BGR, or
    dividing by 255, degrades accuracy without ever raising. `test_sface_contract`
    cannot catch it — it feeds zeros, which are invariant to both channel order
    and scaling — so the real assertion lives here, against a recorder rather
    than the 37 MB model.
    """
    recognizer = FaceRecognizer(tmp_path)
    session = _RecordingSession()
    recognizer._sface = session
    recognizer._sface_input_name = "data"
    recognizer._loaded = True
    recognizer._load_done.set()

    aligned = np.zeros((ALIGNED_SIZE, ALIGNED_SIZE, 3), dtype=np.uint8)
    aligned[..., 0] = 10  # B
    aligned[..., 1] = 120  # G
    aligned[..., 2] = 240  # R

    recognizer.embed(aligned)

    blob = session.feed["data"]
    assert blob.shape == (1, 3, ALIGNED_SIZE, ALIGNED_SIZE)
    assert blob.dtype == np.float32
    # swapRB=true: the first plane must be the red channel, the last the blue one.
    assert np.array_equal(blob[0, 0], aligned[..., 2].astype(np.float32))
    assert np.array_equal(blob[0, 1], aligned[..., 1].astype(np.float32))
    assert np.array_equal(blob[0, 2], aligned[..., 0].astype(np.float32))
    # scale 1.0, no mean subtraction: values stay in 0-255, not 0-1.
    assert blob.max() == pytest.approx(240.0)
    assert blob.max() > 1.0


def test_embed_rejects_a_crop_of_the_wrong_size(tmp_path: Path) -> None:
    """A mis-sized crop is a programming error; fail loudly rather than feed the model garbage."""
    recognizer = FaceRecognizer(tmp_path)
    recognizer._sface = _RecordingSession()
    recognizer._loaded = True
    recognizer._load_done.set()

    with pytest.raises(ValueError):
        recognizer.embed(np.zeros((64, 64, 3), dtype=np.uint8))


# --- model contract (gated on the local HF cache, no network) ---------------


@pytest.mark.skipif(not sface_is_cached(), reason="SFace model not in the local HF cache")
def test_sface_contract(tmp_path: Path) -> None:
    """Pin the ONNX contract this whole feature is built on: `data` in, `fc1` [1,128] out."""
    recognizer = FaceRecognizer(tmp_path)
    recognizer.start_warmup()

    assert recognizer.wait_ready(60.0) is True
    assert recognizer._sface is not None
    assert recognizer._sface.get_inputs()[0].name == "data"
    assert recognizer._sface.get_inputs()[0].shape == [1, 3, ALIGNED_SIZE, ALIGNED_SIZE]
    assert recognizer._sface.get_outputs()[0].name == "fc1"
    assert recognizer._sface.get_outputs()[0].shape == [1, 128]

    embedding = recognizer.embed(np.zeros((ALIGNED_SIZE, ALIGNED_SIZE, 3), dtype=np.uint8))

    assert embedding.shape == (128,)
    assert embedding.dtype == np.float32
    assert abs(float(np.linalg.norm(embedding)) - 1.0) < 1e-5
    assert recognizer.load_ms is not None and recognizer.load_ms > 0.0
