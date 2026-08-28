"""One posed JPEG per enrolled person, beside the face store (D-013 amendment).

D-013 shipped face memory as "a name and a numeric signature, never a picture".
The operator amended that on 2026-08-28: **one** snapshot per person, taken at
the moment of explicit verbal enrollment — the person is knowingly posing into a
held-still camera — kept so the Mac-side management backend can show a face next
to a name. Recognition still never captures an image, no other code path writes
one, and continuous capture stays rejected.

`faces.py`'s "No image is ever persisted here" is about `faces.v1.json` itself
and remains literally true: snapshots are a sibling *directory*, they carry no
embedding, and nothing on the robot reads them back. They live inside the app
instance directory, so a reinstall wipes them exactly like every other store.

The encoder is the ffmpeg binary the `imageio-ffmpeg` wheel already ships for
music (D-018) — the robot has no system packages, and this feature must not add
an image dependency. `hanova.ytdlp.ffmpeg_exe` is the one place in this repo
that resolves that binary, so it is reused rather than copied.

Everything here is best effort. A snapshot may never fail, delay, or raise into
an enrollment: every failure path logs a warning and returns False.
"""

from __future__ import annotations
import os
import logging
import subprocess
from typing import Final
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from reachy_companion.faces import faces_path_for_instance
from reachy_companion.hanova.ytdlp import ffmpeg_exe


logger = logging.getLogger(__name__)

SNAPSHOT_DIRNAME: Final[str] = "face_snapshots"
# Encoding one 640x480 frame costs milliseconds; ten seconds is the "the child
# is wedged" bound, not a budget. `_run_encoder` kills the process on expiry.
SNAPSHOT_TIMEOUT_S: Final[float] = 10.0
# mjpeg's -q:v runs 2 (best) to 31; 4 sits at roughly quality 85 — small enough
# to scp over the robot's wifi, good enough to recognize a face by eye.
_JPEG_QSCALE: Final[str] = "4"

# A record id is a filename here, so anything that could make it a *path* is
# refused. Ids are generated (`f_<epoch>_<6 chars>`), but the store is a plain
# JSON file on a robot anyone can ssh into, so the check is on the value, not on
# where it came from.
_FORBIDDEN_IN_RECORD_ID: Final[tuple[str, ...]] = ("/", "\\", "\x00", os.sep, os.altsep or "/")


def _ffmpeg_exe() -> str | None:
    """Return the wheel-bundled ffmpeg binary, or None. The one resolution seam.

    Delegates to `hanova.ytdlp.ffmpeg_exe` (same wheel, same warning on a
    missing one); the local name is what tests substitute.
    """
    return ffmpeg_exe()


def _run_encoder(cmd: list[str], payload: bytes, timeout_s: float) -> subprocess.CompletedProcess[bytes]:
    """Run the encoder with the raw frame on stdin. The one subprocess seam.

    `subprocess.run` kills the child and reaps it before re-raising
    `TimeoutExpired`, which is what bounds the encode: a wedged ffmpeg cannot
    outlive the call and hold a thread from the default executor forever.
    """
    return subprocess.run(cmd, input=payload, capture_output=True, timeout=timeout_s, check=False)


def snapshot_dir_for_instance(instance_path: str | Path | None = None) -> Path:
    """Return the snapshot directory for this app instance.

    Derived from the face store's own path so the two always resolve the same
    way — including the `instance_path=None` fallback under `XDG_DATA_HOME`.
    """
    return faces_path_for_instance(instance_path).parent / SNAPSHOT_DIRNAME


def snapshot_path_for(instance_path: str | Path | None, record_id: str) -> Path:
    """Return `<instance>/face_snapshots/<record_id>.jpg`.

    Raises `ValueError` for a record id that is not usable as a bare filename —
    empty, padded, a dot entry, or carrying a path separator.
    """
    if not isinstance(record_id, str) or not record_id or record_id != record_id.strip():
        raise ValueError("record_id must be a non-empty, unpadded string")
    if record_id in (".", "..") or any(bad in record_id for bad in _FORBIDDEN_IN_RECORD_ID):
        raise ValueError("record_id must be a bare filename, with no path separator")
    return snapshot_dir_for_instance(instance_path) / f"{record_id}.jpg"


def _as_bgr_frame(frame_bgr: NDArray[np.uint8] | None) -> NDArray[np.uint8] | None:
    """Return the frame as a contiguous HxWx3 uint8 array, or None if it is not one.

    Deliberately no coercion: a float or 4-channel buffer is a caller bug or a
    changed camera format, and silently reinterpreting it would write a garbage
    photo instead of reporting nothing.
    """
    if frame_bgr is None:
        return None
    try:
        frame: NDArray[np.uint8] = np.ascontiguousarray(frame_bgr)
    except Exception:  # noqa: BLE001 - a non-array argument must not raise out
        return None
    if frame.dtype != np.uint8 or frame.ndim != 3 or frame.shape[2] != 3:
        return None
    if frame.shape[0] < 1 or frame.shape[1] < 1:
        return None
    return frame


def _discard(tmp_path: Path) -> None:
    """Remove a partial temporary file, ignoring anything that goes wrong."""
    try:
        tmp_path.unlink(missing_ok=True)
    except OSError:
        pass


def save_snapshot(
    instance_path: str | Path | None,
    record_id: str,
    frame_bgr: NDArray[np.uint8] | None,
) -> bool:
    """Write *frame_bgr* as this record's enrollment snapshot. Never raises.

    Returns True only when a complete JPEG is in place. The write is
    tmp+rename inside the snapshot directory, so a reader (the Mac-side sync)
    can only ever see a whole file, and a re-enrollment replaces the previous
    snapshot rather than accumulating one per sample.
    """
    try:
        path = snapshot_path_for(instance_path, record_id)
    except (TypeError, ValueError) as exc:
        logger.warning("Enrollment snapshot skipped: unusable record id (%s)", exc)
        return False

    frame = _as_bgr_frame(frame_bgr)
    if frame is None:
        logger.warning("Enrollment snapshot skipped: the frame is not an HxWx3 uint8 BGR image")
        return False

    ffmpeg = _ffmpeg_exe()
    if not ffmpeg:
        logger.warning("Enrollment snapshot skipped: no bundled ffmpeg binary is available")
        return False

    height, width = int(frame.shape[0]), int(frame.shape[1])
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    cmd = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "bgr24",
        "-s",
        f"{width}x{height}",
        "-i",
        "-",
        "-frames:v",
        "1",
        "-q:v",
        _JPEG_QSCALE,
        "-f",
        "image2",
        "-c:v",
        "mjpeg",
        str(tmp_path),
    ]

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        proc = _run_encoder(cmd, frame.tobytes(), SNAPSHOT_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        logger.warning("Enrollment snapshot for %s timed out after %.0fs; the encoder was killed", record_id, SNAPSHOT_TIMEOUT_S)
        _discard(tmp_path)
        return False
    except Exception as exc:  # noqa: BLE001 - a snapshot may never fail an enrollment
        logger.warning("Enrollment snapshot for %s could not be encoded: %s: %s", record_id, type(exc).__name__, exc)
        _discard(tmp_path)
        return False

    if proc.returncode != 0 or not tmp_path.is_file() or tmp_path.stat().st_size == 0:
        logger.warning("Enrollment snapshot for %s produced nothing (rc=%s)", record_id, proc.returncode)
        _discard(tmp_path)
        return False

    try:
        tmp_path.replace(path)
    except OSError as exc:
        logger.warning("Enrollment snapshot for %s could not be stored: %s", record_id, exc)
        _discard(tmp_path)
        return False

    logger.info("Enrollment snapshot stored for %s (%dx%d)", record_id, width, height)
    return True
