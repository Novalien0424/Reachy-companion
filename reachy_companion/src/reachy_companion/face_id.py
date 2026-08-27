"""Face recognition: YuNet detection (reused from the SDK) plus SFace embeddings.

Two hard constraints shaped this module (D-013):

* **No cv2 anywhere.** The app venv — dev and robot — has no OpenCV, and the SDK
  only pulls `opencv-python` in its extras. The canonical SFace pipeline
  (`cv2.FaceRecognizerSF.alignCrop` + `blobFromImage`) is therefore replicated in
  numpy here, following the precedent the SDK itself sets in
  `media/camera_utils.py` ("Pure numpy equivalent of `cv2.undistortPoints()`").
* **Never steal the control loop.** The detector keeps the SDK's own one-thread
  session, untouched. The recognizer's does not (D-015): recognition is a single
  short burst per wake, so a ~100 ms three-thread burst is invisible to the 50 Hz
  loop, while ORT's default busy-spinning pool would keep a core hot *between*
  bursts. Spinning is therefore disabled — of the two, the politer setting, not
  the riskier one.

Alignment fits a least-squares similarity transform (Umeyama, with scale) from
five landmarks — right eye, left eye, nose, right mouth corner, left mouth
corner — onto the five canonical SFace reference points, then inverse-maps with
bilinear resampling. YuNet computes all five, but the SDK's parser keeps only the
first three, so `_decode_five_points` re-parses the very same raw outputs with
the mouth corners kept (D-015). Reproducing `alignCrop` semantics — full
template, same similarity warp, same raw 0-255 RGB blob — is what makes OpenCV's
published **0.363** cosine threshold ours as well; it is the default, with a 0.05
margin rule on top. Embeddings enrolled under the earlier three-point warp are
not comparable to these and must be re-enrolled.

`identify()` never raises: every failure — disabled, model missing, bad frame,
detector error — comes back as a status, and its `reason` is one of the fixed
codes in `IDENTIFICATION_REASONS`. That closure is deliberate: an
`Identification` becomes a tool result, which is echoed verbatim to the cloud
model, so exception text must stay in the local log and never in `reason`.
"""

from __future__ import annotations
import time
import logging
import threading
from typing import Any, Literal, get_args
from pathlib import Path
from functools import lru_cache
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from reachy_companion.faces import EMBEDDING_DIM, FaceRecord, list_faces, upsert_face
from reachy_companion.audio.envparse import env_int, env_float


logger = logging.getLogger(__name__)

SFACE_REPO = "opencv/face_recognition_sface"
SFACE_FILE = "face_recognition_sface_2021dec.onnx"

# OpenCV's own published cosine threshold for this exact model, valid here
# because the pipeline reproduces `alignCrop` semantics point for point (D-015).
DEFAULT_MATCH_THRESHOLD = 0.363
DEFAULT_MARGIN = 0.05
DEFAULT_ORT_INTRA_OP_THREADS = 3
MIN_FACE_PX = 60
DETECT_DOWNSCALE = 2
ALIGNED_SIZE = 112

# OpenCV's five canonical alignment targets, from `getSimilarityTransformMatrix()`
# in modules/objdetect/src/face_recognize.cpp: right eye, left eye, nose tip,
# right mouth corner, left mouth corner, in the 112x112 aligned frame. The order
# is YuNet's own keypoint order, which is what `Face5` and `align_face` produce.
REFERENCE_POINTS: NDArray[np.float64] = np.array(
    [
        [38.2946, 51.6963],
        [73.5318, 51.5014],
        [56.0252, 71.7366],
        [41.5493, 92.3655],
        [70.7299, 92.2041],
    ],
    dtype=np.float64,
)

IdentificationStatus = Literal[
    "recognized",
    "unknown",
    "ambiguous",
    "no_face",
    "multiple_faces",
    "too_far",
    "unavailable",
]

# The complete vocabulary of `Identification.reason`. Short machine codes, not
# prose and never exception text: the model sees these, the log gets the detail.
IdentificationReason = Literal[
    "face_memory_disabled",
    "camera_disabled",
    "no_frame",
    "unsupported_frame",
    "model_unavailable",
    "invalid_name",
    "internal_error",
]

IDENTIFICATION_REASONS: frozenset[str] = frozenset(get_args(IdentificationReason))


@dataclass(frozen=True)
class Face5:
    """A detected face with all five YuNet landmarks, in pixel coordinates.

    The SDK's own `Face` carries three of them. This is the same record with the
    two mouth corners YuNet already computed and the SDK's parser throws away —
    the points SFace's canonical alignment needs (D-015). Field order is YuNet's
    keypoint order, and it must stay in step with `REFERENCE_POINTS`.
    """

    bbox: tuple[float, float, float, float]
    right_eye: tuple[float, float]
    left_eye: tuple[float, float]
    nose: tuple[float, float]
    right_mouth: tuple[float, float]
    left_mouth: tuple[float, float]

    def landmarks(self) -> NDArray[np.float64]:
        """Return the five landmarks as a (5, 2) array in `REFERENCE_POINTS` order."""
        return np.array(
            [self.right_eye, self.left_eye, self.nose, self.right_mouth, self.left_mouth],
            dtype=np.float64,
        )


@dataclass(frozen=True)
class MatchResult:
    """The outcome of comparing one embedding against the store."""

    status: Literal["recognized", "unknown", "ambiguous"]
    name: str | None = None
    score: float | None = None
    runner_up: str | None = None


@dataclass(frozen=True)
class Identification:
    """The outcome of looking at one frame — the only thing a tool ever returns."""

    status: IdentificationStatus
    name: str | None = None
    score: float | None = None
    runner_up: str | None = None
    face_count: int = 0
    reason: IdentificationReason | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return the tool-result shape: fields that are set, and never image bytes."""
        payload: dict[str, Any] = {"status": self.status, "face_count": self.face_count}
        if self.name is not None:
            payload["name"] = self.name
        if self.score is not None:
            payload["score"] = round(self.score, 3)
        if self.runner_up is not None:
            payload["runner_up"] = self.runner_up
        if self.reason is not None:
            payload["reason"] = self.reason
        return payload


def sface_is_cached() -> bool:
    """Return whether the SFace model is already in the local HF cache (no network)."""
    try:
        from huggingface_hub import try_to_load_from_cache

        cached = try_to_load_from_cache(SFACE_REPO, SFACE_FILE)
    except Exception:  # pragma: no cover - hub API drift must not break collection
        return False
    return isinstance(cached, str) and Path(cached).is_file()


def cosine(a: NDArray[np.float32], b: NDArray[np.float32]) -> float:
    """Return the cosine similarity of two vectors, 0.0 when either has no length."""
    left = np.asarray(a, dtype=np.float64).reshape(-1)
    right = np.asarray(b, dtype=np.float64).reshape(-1)
    norms = float(np.linalg.norm(left)) * float(np.linalg.norm(right))
    if norms <= 0.0:
        return 0.0
    return float(np.dot(left, right) / norms)


def _decode_five_points(
    outputs: dict[str, NDArray[np.float32]],
    width: int,
    score_threshold: float,
    nms_threshold: float,
) -> list[Face5]:
    """Parse YuNet's raw head outputs into faces, keeping all five landmarks.

    A deliberate mirror of `FaceDetector._decode` in the pinned SDK
    (`reachy_mini.vision.face_detector`, 1.10.0rc5): same geometric-mean score
    fusion, same anchor decoding, same greedy NMS — the SDK's own `_nms` is
    reused rather than reimplemented. The single difference is the keypoint
    slice. `kps_*` is [1, anchors, 10]; the SDK reads columns 0-5 and discards
    6-9, which are the right and left mouth corners. We read all ten.

    Copying the loop is the smallest available seam: the SDK builds its `Face`
    inline inside `_decode`, so there is nothing finer to override.
    """
    from reachy_mini.vision.face_detector import _STRIDES, _nms

    boxes: list[tuple[float, float, float, float]] = []
    scores: list[float] = []
    faces: list[Face5] = []
    for stride in _STRIDES:
        cls = outputs[f"cls_{stride}"][0, :, 0]
        obj = outputs[f"obj_{stride}"][0, :, 0]
        score = np.sqrt(np.clip(cls, 0.0, 1.0) * np.clip(obj, 0.0, 1.0))
        idx = np.nonzero(score >= score_threshold)[0]
        if idx.size == 0:
            continue
        bbox = outputs[f"bbox_{stride}"][0][idx]
        kps = outputs[f"kps_{stride}"][0][idx]
        cols = width // stride
        col = (idx % cols).astype(np.float32)
        row = (idx // cols).astype(np.float32)
        cx = (col + bbox[:, 0]) * stride
        cy = (row + bbox[:, 1]) * stride
        w = np.exp(bbox[:, 2]) * stride
        h = np.exp(bbox[:, 3]) * stride
        for k in range(idx.size):
            box = (
                float(cx[k] - w[k] / 2),
                float(cy[k] - h[k] / 2),
                float(w[k]),
                float(h[k]),
            )
            boxes.append(box)
            scores.append(float(score[idx[k]]))
            points = [
                (float((col[k] + kps[k, i]) * stride), float((row[k] + kps[k, i + 1]) * stride))
                for i in range(0, 10, 2)
            ]
            faces.append(
                Face5(
                    bbox=box,
                    right_eye=points[0],
                    left_eye=points[1],
                    nose=points[2],
                    right_mouth=points[3],
                    left_mouth=points[4],
                )
            )
    return [faces[i] for i in _nms(boxes, scores, nms_threshold)]


@lru_cache(maxsize=1)
def _five_point_detector_class() -> type[Any]:
    """Return the `FaceDetector` subclass that keeps all five landmarks.

    Built on first use, not at import: the SDK detector module pulls onnxruntime
    and the hub client, which this module otherwise touches only when a model is
    actually loaded.
    """
    from reachy_mini.vision.face_detector import FaceDetector

    # The SDK ships no `py.typed`, so its detector is `Any` to mypy; subclassing
    # it is exactly what we want here, and is the one thing strict mode forbids.
    class FivePointFaceDetector(FaceDetector):  # type: ignore[misc]
        """The SDK's YuNet detector, returning `Face5` instead of the SDK's `Face`.

        Everything else — model, revision, thresholds, the one-thread session —
        is the SDK's, untouched. Only the output parse is overridden.
        """

        def _decode(self, outputs: dict[str, NDArray[np.float32]], width: int) -> list[Face5]:
            """Decode the head outputs with the two mouth corners kept."""
            return _decode_five_points(outputs, width, self._score_threshold, self._nms_threshold)

    return FivePointFaceDetector


def _similarity_transform(source: NDArray[np.float64], target: NDArray[np.float64]) -> tuple[
    float,
    NDArray[np.float64],
    NDArray[np.float64],
]:
    """Return (scale, rotation, translation) mapping `source` onto `target` (Umeyama).

    Degenerate input — three identical or collinear landmarks, which a bad
    detection can produce — falls back to a pure translation instead of raising,
    because `identify()` must stay total.
    """
    count = source.shape[0]
    source_mean = source.mean(axis=0)
    target_mean = target.mean(axis=0)
    source_centered = source - source_mean
    target_centered = target - target_mean

    variance = float((source_centered**2).sum() / count)
    if variance <= 1e-9:
        return 1.0, np.eye(2), target_mean - source_mean

    covariance = (target_centered.T @ source_centered) / count
    u, singular_values, vt = np.linalg.svd(covariance)
    correction = np.ones(2)
    if np.linalg.det(u) * np.linalg.det(vt) < 0:
        correction[-1] = -1.0
    rotation = u @ np.diag(correction) @ vt
    scale = float((singular_values * correction).sum() / variance)
    if not np.isfinite(scale) or abs(scale) < 1e-9:
        return 1.0, np.eye(2), target_mean - source_mean
    translation = target_mean - scale * (rotation @ source_mean)
    return scale, rotation, translation


def _sample_bilinear(
    frame_bgr: NDArray[np.uint8],
    x: NDArray[np.float64],
    y: NDArray[np.float64],
) -> NDArray[np.uint8]:
    """Bilinearly sample `frame_bgr` at float coordinates, zero-filling outside the frame."""
    height, width = frame_bgr.shape[:2]
    x0 = np.floor(x).astype(np.int64)
    y0 = np.floor(y).astype(np.int64)
    fx = (x - x0)[..., None]
    fy = (y - y0)[..., None]
    inside = (x0 >= 0) & (y0 >= 0) & (x0 + 1 < width) & (y0 + 1 < height)
    x0c = np.clip(x0, 0, max(width - 2, 0))
    y0c = np.clip(y0, 0, max(height - 2, 0))

    source = frame_bgr.astype(np.float64)
    top = source[y0c, x0c] * (1.0 - fx) + source[y0c, x0c + 1] * fx
    bottom = source[y0c + 1, x0c] * (1.0 - fx) + source[y0c + 1, x0c + 1] * fx
    blended = top * (1.0 - fy) + bottom * fy
    sampled: NDArray[np.uint8] = np.clip(np.rint(blended * inside[..., None]), 0, 255).astype(np.uint8)
    return sampled


def align_face(frame_bgr: NDArray[np.uint8], face: Face5) -> NDArray[np.uint8]:
    """Warp the face in `frame_bgr` onto the canonical 112x112 SFace frame.

    `face` carries all five landmarks in **full-resolution** pixel coordinates.
    Pure numpy: a least-squares similarity fit followed by an inverse-mapped
    bilinear resample, the cv2-free equivalent of
    `FaceRecognizerSF.alignCrop()` — and, with the full five-point template,
    equivalent in semantics too, which is what the 0.363 threshold rests on.

    A frame too small or the wrong shape to sample yields a black crop rather
    than an exception: `identify()` guards its own input, but this helper is
    public and must be safe standalone.
    """
    frame = np.asarray(frame_bgr)
    if frame.ndim != 3 or frame.shape[2] != 3 or frame.shape[0] < 2 or frame.shape[1] < 2:
        logger.warning("align_face received an unusable frame of shape %s; returning a black crop.", frame.shape)
        return np.zeros((ALIGNED_SIZE, ALIGNED_SIZE, 3), dtype=np.uint8)

    scale, rotation, translation = _similarity_transform(face.landmarks(), REFERENCE_POINTS)

    grid_y, grid_x = np.mgrid[0:ALIGNED_SIZE, 0:ALIGNED_SIZE].astype(np.float64)
    destination = np.stack([grid_x, grid_y], axis=-1) - translation
    # Inverse of `scale * rotation @ p`: for row vectors, `rotation.T @ v` is `v @ rotation`.
    source = (destination @ rotation) / scale
    return _sample_bilinear(frame, source[..., 0], source[..., 1])


class FaceRecognizer:
    """Detect, align, embed and match faces — the whole on-robot recognition path.

    Owns no camera and does no image I/O: callers hand it BGR frames. Model
    loading is lazy and thread-safe, so a wake-time check can wait on a warm-up
    thread with a deadline while a tool call simply blocks until the sessions
    exist.
    """

    def __init__(self, instance_path: str | Path | None = None, *, enabled: bool = True) -> None:
        """Create a recognizer for one app instance, honouring the kill switch."""
        self.instance_path = instance_path
        self.enabled = enabled
        self.threshold = env_float("FACE_MATCH_THRESHOLD", DEFAULT_MATCH_THRESHOLD, lo=0.0, hi=1.0)
        self.margin = env_float("FACE_MATCH_MARGIN", DEFAULT_MARGIN, lo=0.0, hi=1.0)

        self._detector: Any = None
        self._sface: Any = None
        self._sface_input_name: str = "data"
        self._loaded = False
        self._load_error: str | None = None
        self._load_ms: float | None = None
        self._load_done = threading.Event()
        self._load_lock = threading.Lock()
        self._warmup_thread: threading.Thread | None = None

    @property
    def load_ms(self) -> float | None:
        """Milliseconds spent building both ONNX sessions, once loaded."""
        return self._load_ms

    def start_warmup(self) -> None:
        """Build both ONNX sessions on a daemon thread, so the first use is warm.

        A cold build reads ~37 MB of SFace off eMMC on the robot; doing that
        inside the wake-time budget would simply lose the greeting check.
        """
        if not self.enabled:
            logger.info("Face memory is disabled; skipping model warm-up.")
            return
        with self._load_lock:
            if self._loaded or self._warmup_thread is not None:
                return
            self._warmup_thread = threading.Thread(target=self._load, name="face-warmup", daemon=True)
            self._warmup_thread.start()

    def wait_ready(self, timeout_s: float) -> bool:
        """Wait up to `timeout_s` for the models, returning whether they are usable."""
        if not self.enabled:
            return False
        if not self._loaded and self._warmup_thread is None:
            self.start_warmup()
        if not self._load_done.wait(timeout=max(0.0, timeout_s)):
            return False
        return self._loaded

    def _load(self) -> None:
        """Build the YuNet and SFace sessions; record the failure instead of raising."""
        started = time.perf_counter()
        try:
            import onnxruntime as ort
            from huggingface_hub import hf_hub_download

            detector = _five_point_detector_class()()
            model_path = hf_hub_download(SFACE_REPO, SFACE_FILE)

            options = ort.SessionOptions()
            # D-015, from measurements on the robot. Recognition is one short
            # burst per wake, not a continuous load: three intra-op threads turn
            # a 239 ms embed into ~100 ms, which the 50 Hz loop never feels once.
            # Disabling the busy-wait pool is the other half — ORT spins its
            # worker threads by default, burning a core *between* bursts, which
            # is what made the same embed cost 362 ms with the app running. Both
            # settings make the recognizer a politer neighbour, not a greedier
            # one. The detector's session stays exactly as the SDK built it.
            options.intra_op_num_threads = env_int(
                "FACE_ORT_INTRA_OP_THREADS", DEFAULT_ORT_INTRA_OP_THREADS, lo=1, hi=4
            )
            options.inter_op_num_threads = 1
            options.add_session_config_entry("session.intra_op.allow_spinning", "0")
            # SFace ships its batch-norm constants as graph inputs, which makes ORT
            # log ~50 initializer warnings per session build. They are informational
            # and would bury the app's own startup log on the robot.
            options.log_severity_level = 3
            session = ort.InferenceSession(model_path, options, providers=["CPUExecutionProvider"])

            self._detector = detector
            self._sface = session
            self._sface_input_name = session.get_inputs()[0].name
            self._load_ms = (time.perf_counter() - started) * 1000.0
            self._loaded = True
            logger.info(
                "Face memory ready: YuNet + SFace sessions built in %.0f ms (threshold %.2f, margin %.2f)",
                self._load_ms,
                self.threshold,
                self.margin,
            )
        except Exception as exc:
            self._load_error = f"{type(exc).__name__}: {exc}"
            logger.warning("Face memory unavailable: model load failed: %s", self._load_error)
        finally:
            self._load_done.set()

    def _ensure_loaded(self) -> bool:
        """Load the models synchronously if needed; return whether they are usable."""
        if self._loaded:
            return True
        with self._load_lock:
            if self._loaded:
                return True
            if self._warmup_thread is not None:
                thread = self._warmup_thread
            else:
                thread = None
        if thread is not None:
            thread.join()
            return self._loaded
        self._load()
        return self._loaded

    def embed(self, aligned_bgr: NDArray[np.uint8]) -> NDArray[np.float32]:
        """Return the L2-normalized 128-float SFace embedding of an aligned 112x112 BGR crop.

        The blob matches OpenCV's `blobFromImage(aligned, 1, (112,112), (0,0,0),
        swapRB=true, crop=false)`: BGR to RGB, float32 values 0-255, **no mean
        subtraction and no scaling**. Getting that wrong degrades accuracy
        silently rather than crashing.
        """
        array = np.asarray(aligned_bgr)
        if array.shape != (ALIGNED_SIZE, ALIGNED_SIZE, 3):
            raise ValueError(f"aligned crop must be ({ALIGNED_SIZE}, {ALIGNED_SIZE}, 3), got {array.shape}")
        if not self._ensure_loaded() or self._sface is None:
            raise RuntimeError(self._load_error or "face recognition model is unavailable")

        blob = array[..., ::-1].astype(np.float32).transpose(2, 0, 1)[np.newaxis]
        outputs = self._sface.run(None, {self._sface_input_name: blob})
        vector = np.asarray(outputs[0], dtype=np.float32).reshape(-1)
        norm = float(np.linalg.norm(vector))
        if norm <= 0.0:
            return vector
        return (vector / norm).astype(np.float32)

    def match(self, embedding: NDArray[np.float32]) -> MatchResult:
        """Compare one embedding against the store and apply the threshold + margin rules.

        Per-person score is the best of that person's (up to three) samples.
        `recognized` requires both `best >= threshold` and a `margin` lead over
        the runner-up; a near-tie is reported as `ambiguous` rather than
        confidently naming the wrong person.
        """
        scored = [
            (max(cosine(embedding, np.asarray(sample, dtype=np.float32)) for sample in record.embeddings), record.name)
            for record in list_faces(self.instance_path)
            if record.embeddings
        ]
        if not scored:
            return MatchResult(status="unknown")

        scored.sort(key=lambda item: item[0], reverse=True)
        best_score, best_name = scored[0]

        if best_score < self.threshold:
            return MatchResult(status="unknown", score=best_score, runner_up=None)

        # The margin compares candidates, not noise: only a runner-up that
        # itself clears the threshold can make the answer ambiguous. A stranger
        # scoring 0.35 must not stop us from naming the person scoring 0.38.
        qualified = [item for item in scored if item[0] >= self.threshold]
        runner_up_score, runner_up_name = qualified[1] if len(qualified) > 1 else (None, None)

        if runner_up_score is not None and (best_score - runner_up_score) < self.margin:
            return MatchResult(
                status="ambiguous",
                name=best_name,
                score=best_score,
                runner_up=runner_up_name,
            )

        # A confident recognition reports no runner-up: naming a second, absent
        # person to the model buys nothing and leaks who else is enrolled. The
        # `ambiguous` branch above keeps it, because there the runner-up is the
        # whole point of the answer.
        return MatchResult(status="recognized", name=best_name, score=best_score)

    def identify(self, frame_bgr: NDArray[np.uint8] | None) -> Identification:
        """Return who is in front of the camera, as a status that is never an exception."""
        _, identification = self._capture(frame_bgr)
        return identification

    def enroll(
        self,
        frame_bgr: NDArray[np.uint8] | None,
        name: str,
    ) -> tuple[FaceRecord | None, Identification]:
        """Store the face in `frame_bgr` under `name`; require exactly one face.

        Returns the stored record (None when nothing was stored) alongside the
        identification, so a caller can tell *why* enrollment was refused and
        whether this face already matched someone.
        """
        embedding, identification = self._capture(frame_bgr)
        if embedding is None:
            return None, identification
        try:
            record = upsert_face(self.instance_path, name, embedding)
        except ValueError as exc:
            logger.warning("Face enrollment rejected: %s", exc)
            return None, Identification(status="unavailable", face_count=1, reason="invalid_name")
        if record is None:
            logger.warning("Face enrollment rejected: the name was empty.")
            return None, Identification(status="unavailable", face_count=1, reason="invalid_name")
        return record, identification

    def _capture(
        self,
        frame_bgr: NDArray[np.uint8] | None,
    ) -> tuple[NDArray[np.float32] | None, Identification]:
        """Run the whole pipeline once, returning the embedding when there is exactly one face."""
        if not self.enabled:
            return None, Identification(status="unavailable", reason="face_memory_disabled")
        if frame_bgr is None:
            return None, Identification(status="unavailable", reason="no_frame")

        frame = np.asarray(frame_bgr)
        if frame.ndim != 3 or frame.shape[2] != 3 or frame.shape[0] < 2 or frame.shape[1] < 2:
            return None, Identification(status="unavailable", reason="unsupported_frame")

        try:
            if not self._ensure_loaded():
                logger.warning(
                    "Face identification unavailable: %s",
                    self._load_error or "face recognition model is unavailable",
                )
                return None, Identification(status="unavailable", reason="model_unavailable")

            # Detect on an exact 2x decimation (no interpolation): ~4x cheaper than
            # full resolution, and the landmarks scale back linearly.
            small = np.ascontiguousarray(frame[::DETECT_DOWNSCALE, ::DETECT_DOWNSCALE])
            detected = self._detector.detect(small)
            if not detected:
                return None, Identification(status="no_face", face_count=0)
            if len(detected) > 1:
                return None, Identification(status="multiple_faces", face_count=len(detected))

            face = _scale_face(detected[0], DETECT_DOWNSCALE)
            if face.bbox[2] < MIN_FACE_PX:
                return None, Identification(status="too_far", face_count=1)

            # The crop comes from the full-resolution frame, not the decimated one.
            aligned = align_face(frame, face)
            embedding = self.embed(aligned)
        except Exception as exc:
            # The detail stays here; `reason` is a code because it reaches the model.
            logger.warning("Face identification failed: %s: %s", type(exc).__name__, exc)
            return None, Identification(status="unavailable", reason="internal_error")

        result = self.match(embedding)
        return embedding, Identification(
            status=result.status,
            name=result.name,
            score=result.score,
            runner_up=result.runner_up,
            face_count=1,
        )


def _scale_face(face: Face5, factor: int) -> Face5:
    """Return `face` with bbox and all five landmarks scaled from the decimated frame to full resolution."""
    x, y, width, height = face.bbox

    def scaled(point: tuple[float, float]) -> tuple[float, float]:
        return (point[0] * factor, point[1] * factor)

    return Face5(
        bbox=(x * factor, y * factor, width * factor, height * factor),
        right_eye=scaled(face.right_eye),
        left_eye=scaled(face.left_eye),
        nose=scaled(face.nose),
        right_mouth=scaled(face.right_mouth),
        left_mouth=scaled(face.left_mouth),
    )


__all__ = [
    "ALIGNED_SIZE",
    "DEFAULT_MARGIN",
    "DEFAULT_MATCH_THRESHOLD",
    "DEFAULT_ORT_INTRA_OP_THREADS",
    "DETECT_DOWNSCALE",
    "EMBEDDING_DIM",
    "MIN_FACE_PX",
    "REFERENCE_POINTS",
    "SFACE_FILE",
    "SFACE_REPO",
    "IDENTIFICATION_REASONS",
    "Face5",
    "FaceRecognizer",
    "Identification",
    "IdentificationReason",
    "MatchResult",
    "align_face",
    "cosine",
    "sface_is_cached",
]
