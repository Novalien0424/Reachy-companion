"""Contract tests for photo decoding and Mac-side SFace embedding.

No model is ever loaded here. Decoding is exercised against a committed 64x64
JPEG (`tests/fixtures/gray.jpg`, made once with the bundled ffmpeg), and
`embed_photo` runs against a stub of the robot recognizer's extract seam — the
real `Identification` dataclass, so the status vocabulary the mapping depends on
is the robot's own and not a copy that can drift.
"""

from __future__ import annotations
import logging
from typing import Any
from pathlib import Path

import numpy as np
import pytest
from numpy.typing import NDArray

from reachy_companion.faces import EMBEDDING_DIM
from reachy_companion.face_id import Identification
from backend import store, embedding
from backend.config import Settings


FIXTURES = Path(__file__).resolve().parent / "fixtures"
GRAY_JPEG = FIXTURES / "gray.jpg"


# --------------------------------------------------------------------------
# decode
# --------------------------------------------------------------------------


def test_decode_image_reads_the_committed_fixture() -> None:
    """A real JPEG decodes to a BGR HxWx3 uint8 array of the right shape.

    The fixture is a flat 64x64 grey field, so every channel of every pixel is
    the same value: a decode that dropped a channel or mis-strided the rows
    would not survive the shape and dtype assertions.
    """
    frame = embedding.decode_image(GRAY_JPEG)

    assert frame is not None
    assert frame.shape == (64, 64, 3)
    assert frame.dtype == np.uint8
    assert int(frame.min()) == int(frame.max()) == 128


def test_decode_image_returns_none_for_bytes_that_are_not_an_image(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """An upload that is not an image is `None` plus a local log line, never a traceback.

    Clients upload whatever they like; a corrupt file must become one photo's
    `decode_failed`, not a 500 that loses the whole batch.
    """
    junk = tmp_path / "not-an-image.jpg"
    junk.write_bytes(b"this is not a JPEG" * 8)

    with caplog.at_level(logging.WARNING, logger="backend.embedding"):
        assert embedding.decode_image(junk) is None

    assert "not-an-image.jpg" in caplog.text


def test_decode_image_returns_none_for_a_missing_file(tmp_path: Path) -> None:
    """A record whose bytes are gone is a decode failure, not an exception."""
    assert embedding.decode_image(tmp_path / "gone.jpg") is None


# --------------------------------------------------------------------------
# embed
# --------------------------------------------------------------------------


class _StubRecognizer:
    """The robot recognizer's extract seam, stubbed, recording the frames it saw."""

    def __init__(
        self,
        result: NDArray[np.float32] | None,
        identification: Identification,
    ) -> None:
        self.result = result
        self.identification = identification
        self.seen: list[tuple[int, ...]] = []

    def embedding_for_frame(
        self, frame_bgr: NDArray[Any] | None
    ) -> tuple[NDArray[np.float32] | None, Identification]:
        self.seen.append(np.asarray(frame_bgr).shape)
        return self.result, self.identification


def _unit_embedding(index: int = 0) -> NDArray[np.float32]:
    """Return a 128-d unit vector — the shape SFace really produces."""
    vector = np.zeros(EMBEDDING_DIM, dtype=np.float32)
    vector[index] = 1.0
    return vector


def test_embed_photo_returns_a_stored_embedding_on_success() -> None:
    """A decoded photo is handed to the seam and its vector comes back store-ready.

    Store-ready means `faces._to_stored_embedding`'s own output: validated,
    L2-normalized and rounded exactly as the robot rounds it, so a vector
    embedded here and a vector embedded on the robot are the same number.
    """
    recognizer = _StubRecognizer(_unit_embedding() * 3.0, Identification(status="unknown", face_count=1))

    vector, error = embedding.embed_photo(recognizer, GRAY_JPEG)

    assert error is None
    assert vector is not None
    assert len(vector) == EMBEDDING_DIM
    assert all(isinstance(value, float) for value in vector)
    # Normalized, not the raw magnitude-3 vector the seam returned.
    assert vector[0] == pytest.approx(1.0)
    assert sum(value * value for value in vector) == pytest.approx(1.0)
    # The seam saw the decoded frame, at full resolution.
    assert recognizer.seen == [(64, 64, 3)]


@pytest.mark.parametrize(
    ("status", "reason", "expected"),
    [
        ("no_face", None, "no_face"),
        ("multiple_faces", None, "multiple_faces"),
        ("too_far", None, "too_far"),
        ("unavailable", "model_unavailable", "internal_error"),
        ("unavailable", "unsupported_frame", "internal_error"),
        ("unavailable", "face_memory_disabled", "internal_error"),
    ],
)
def test_embed_photo_maps_the_seam_status_to_the_photo_error_vocabulary(
    status: str, reason: str | None, expected: str
) -> None:
    """Three statuses are the operator's problem; everything else is ours.

    `no_face` / `multiple_faces` / `too_far` tell the operator to pick a better
    photo. An `unavailable` of any reason means the recognizer failed, which the
    operator cannot fix by cropping — it is `internal_error` and stays one.
    """
    identification = Identification(status=status, reason=reason)  # type: ignore[arg-type]
    recognizer = _StubRecognizer(None, identification)

    vector, error = embedding.embed_photo(recognizer, GRAY_JPEG)

    assert vector is None
    assert error == expected
    # The store is the only consumer of these strings, and it rejects strangers.
    assert error in store.PHOTO_ERRORS


def test_embed_photo_reports_decode_failed_without_touching_the_recognizer(tmp_path: Path) -> None:
    """A file that will not decode never reaches the model — decoding gates the burn."""
    junk = tmp_path / "broken.png"
    junk.write_bytes(b"\x89PNG\r\n\x1a\n" + b"garbage")
    recognizer = _StubRecognizer(_unit_embedding(), Identification(status="unknown", face_count=1))

    vector, error = embedding.embed_photo(recognizer, junk)

    assert (vector, error) == (None, "decode_failed")
    assert error in store.PHOTO_ERRORS
    assert recognizer.seen == []


def test_embed_photo_reports_internal_error_for_a_malformed_embedding(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A vector of the wrong length is our defect, reported per photo, not raised.

    `_to_stored_embedding` is the validator; a batch embed must survive one bad
    photo, so its `ValueError` becomes this photo's `internal_error`.
    """
    recognizer = _StubRecognizer(np.zeros(64, dtype=np.float32), Identification(status="unknown", face_count=1))

    with caplog.at_level(logging.WARNING, logger="backend.embedding"):
        vector, error = embedding.embed_photo(recognizer, GRAY_JPEG)

    assert (vector, error) == (None, "internal_error")
    assert "64" in caplog.text


# --------------------------------------------------------------------------
# recognizer construction
# --------------------------------------------------------------------------


def test_build_recognizer_uses_scratch_space_under_the_data_dir(settings: Settings) -> None:
    """The recognizer's own face store is scratch: never read, and never beside the package.

    `FaceRecognizer` resolves `faces.v1.json` from `instance_path`, and an
    unset path would land it in the user's XDG data dir. Photo embedding never
    reads that store — but it must not write over the robot-shaped one either.
    """
    recognizer = embedding.build_recognizer(settings)

    scratch = Path(str(recognizer.instance_path)).resolve()
    assert scratch.is_relative_to(settings.data_dir.resolve())
    assert scratch.is_dir()
    assert recognizer.enabled is True
    # Constructing must not build a 37 MB ONNX session.
    assert recognizer.load_ms is None
