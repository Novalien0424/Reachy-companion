"""Photo bytes to an SFace embedding, on the Mac, with the robot's own recognizer.

Two reuses carry this module, and both are deliberate:

* **The recognizer is the robot's.** `FaceRecognizer.embedding_for_frame` is the
  same detect-align-embed path the robot runs, so a vector computed here is
  comparable to one enrolled by voice in front of the camera — same YuNet
  revision, same five-point warp, same SFace model, same rounding. A second
  implementation on this side would be a second answer to "is this the same
  person", which is the one question the whole feature turns on.
* **The decoder is ffmpeg.** `imageio_ffmpeg` is already in the venv (the app
  records audio with it), it ships its own binary, and it reads JPEG, PNG and
  WebP — the three formats the upload path whitelists. Adding Pillow or
  opencv-python to decode four operator photos would be a new dependency on the
  robot-side venv this backend shares, for nothing.

`embed_photo` never raises: every outcome is a `(vector, None)` or a
`(None, error)` from `store.PHOTO_ERRORS`, because the caller is a loop over an
operator's uploads and one unreadable photo must not lose the rest.
"""

from __future__ import annotations
import logging
from typing import Any, Final, Protocol
from pathlib import Path

import numpy as np
import imageio_ffmpeg
from numpy.typing import NDArray

from reachy_companion import faces
from reachy_companion.face_id import FaceRecognizer
from backend.config import Settings


logger = logging.getLogger(__name__)

# The recognizer's scratch instance directory, under the backend's own data dir.
# `FaceRecognizer` resolves a `faces.v1.json` from its `instance_path`; nothing
# here ever reads it, but an unset path would put one in the user's XDG data
# directory, next to a real robot-shaped store.
RECOGNIZER_DIRNAME: Final[str] = "recognizer"

# The three extraction failures an operator can act on — pick another photo,
# crop the bystander out, stand closer. They map straight through to
# `BackendPhoto.error`. Every other non-success status is our defect, and
# `internal_error` says so rather than blaming the photo.
_ERROR_FOR_STATUS: Final[dict[str, str]] = {
    "no_face": "no_face",
    "multiple_faces": "multiple_faces",
    "too_far": "too_far",
}
INTERNAL_ERROR: Final[str] = "internal_error"
DECODE_FAILED: Final[str] = "decode_failed"


class FrameEmbedder(Protocol):
    """The one method `embed_photo` needs — the robot recognizer's extraction seam.

    Typed as a protocol so the dependency is exactly that method: the concrete
    `FaceRecognizer` satisfies it structurally, and a test can stand in for it
    without a 37 MB ONNX session. The second element is the robot's
    `Identification`, which crosses an untyped package boundary (`reachy_companion`
    ships no `py.typed`), so it is `Any` here and read only through `.status`.
    """

    def embedding_for_frame(self, frame_bgr: NDArray[Any] | None) -> tuple[NDArray[np.float32] | None, Any]: ...


def decode_image(path: Path) -> NDArray[np.uint8] | None:
    """Decode one image file to a BGR HxWx3 uint8 array, or None if it cannot be read.

    BGR because that is what the robot's camera hands the recognizer, and the
    channel order is not cosmetic: SFace's blob swaps BGR to RGB itself, so
    feeding RGB here would degrade every score silently instead of raising.

    The generator is closed in a `finally` — it owns an ffmpeg subprocess, and
    leaking one per malformed upload would outlive the request.
    """
    frames = None
    try:
        frames = imageio_ffmpeg.read_frames(str(path), pix_fmt="bgr24")
        meta = next(frames)
        width, height = meta["size"]
        frame_bytes = next(frames)
        # A truncated file yields fewer bytes than the header promised; the
        # reshape is what catches that, and it raises rather than corrupting.
        frame: NDArray[np.uint8] = np.frombuffer(frame_bytes, dtype=np.uint8).reshape(height, width, 3)
        return frame
    except Exception as exc:
        # Whatever ffmpeg objected to stays in the local log: the caller gets
        # one flat `decode_failed`, which is all the UI can act on anyway.
        logger.warning("Could not decode %s: %s: %s", path.name, type(exc).__name__, exc)
        return None
    finally:
        if frames is not None:
            frames.close()


def embed_photo(recognizer: FrameEmbedder, path: Path) -> tuple[tuple[float, ...] | None, str | None]:
    """Embed the single face in the photo at `path`: a stored vector, or why not.

    Returns `(vector, None)` on success and `(None, error)` otherwise, where
    `error` is always one of `store.PHOTO_ERRORS`. The vector is what
    `faces._to_stored_embedding` produces — validated, L2-normalized and rounded
    to the robot's own six decimals — so a photo embedded on this Mac and a face
    enrolled on the robot are literally the same numbers.
    """
    frame = decode_image(path)
    if frame is None:
        return None, DECODE_FAILED

    embedding, identification = recognizer.embedding_for_frame(frame)
    if embedding is None:
        status = str(identification.status)
        error = _ERROR_FOR_STATUS.get(status, INTERNAL_ERROR)
        logger.info(
            "No embedding for %s: status=%s reason=%s -> %s",
            path.name,
            status,
            identification.reason,
            error,
        )
        return None, error

    try:
        # The robot's own writer, private but reused on purpose: rounding and
        # normalization are the wire format, and a second copy of them here
        # would be a second wire format the day either one changes.
        return faces._to_stored_embedding(embedding), None
    except ValueError as exc:
        # A malformed vector is our defect, not the operator's photo. One bad
        # photo must not end a batch, so it becomes this photo's error.
        logger.warning("Rejected the embedding computed for %s: %s", path.name, exc)
        return None, INTERNAL_ERROR


def build_recognizer(settings: Settings) -> FaceRecognizer:
    """Return the robot recognizer, pointed at scratch space under `data_dir`.

    The models load lazily on first use, so constructing this is cheap; the
    caller decides when to pay for the ~37 MB SFace session (`start_warmup()`).
    `enabled` is explicit: the robot's `FACE_MEMORY_ENABLED` kill switch governs
    what the robot does with its camera and has no say over an operator
    embedding a photo on their own Mac.
    """
    scratch = settings.data_dir / RECOGNIZER_DIRNAME
    scratch.mkdir(parents=True, exist_ok=True)
    return FaceRecognizer(scratch, enabled=True)
