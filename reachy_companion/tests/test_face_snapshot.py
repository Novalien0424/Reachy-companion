"""Enrollment-snapshot writer tests (D-013 amendment, 2026-08-28).

The promises asserted here are the ones the amendment rests on: the file the
robot keeps is a real JPEG in the instance directory, the write is atomic so a
half-encoded frame can never be read as a photo, and **every** failure path is
silent-but-false — a snapshot may never raise into, nor delay, an enrollment.
"""

import subprocess
from typing import Any
from pathlib import Path

import numpy as np
import pytest
from numpy.typing import NDArray

from reachy_companion import face_snapshot
from reachy_companion.face_snapshot import (
    SNAPSHOT_DIRNAME,
    save_snapshot,
    snapshot_path_for,
)


JPEG_MAGIC = b"\xff\xd8\xff"


def _frame(value: int = 90, *, height: int = 48, width: int = 64) -> NDArray[np.uint8]:
    """Return a small BGR frame with a gradient, so the encoder has real content."""
    frame = np.full((height, width, 3), value, dtype=np.uint8)
    frame[:, : width // 2, 2] = 200
    return frame


# --- snapshot_path_for -------------------------------------------------------


def test_snapshot_path_for_lands_beside_the_face_store(tmp_path: Path) -> None:
    """The snapshot lives in `<instance>/face_snapshots/<record_id>.jpg`."""
    path = snapshot_path_for(tmp_path, "f_1756000000_ab12cd")

    assert path == tmp_path / SNAPSHOT_DIRNAME / "f_1756000000_ab12cd.jpg"


@pytest.mark.parametrize(
    "record_id",
    ["", "   ", "a/b", "a\\b", "..", ".", "../escape", "sub/dir/id"],
)
def test_snapshot_path_for_rejects_anything_that_could_escape_the_directory(tmp_path: Path, record_id: str) -> None:
    """A record id carrying a separator (or a dot entry) is a path, not a name — refuse it."""
    with pytest.raises(ValueError):
        snapshot_path_for(tmp_path, record_id)


def test_snapshot_path_for_falls_back_to_the_default_instance_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With no instance path the snapshot dir sits beside the default face store."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    path = snapshot_path_for(None, "f_1_abcdef")

    assert path == tmp_path / "reachy_companion" / SNAPSHOT_DIRNAME / "f_1_abcdef.jpg"


# --- save_snapshot: the happy path -------------------------------------------


def test_save_snapshot_writes_a_jpeg(tmp_path: Path) -> None:
    """A real frame becomes a real JPEG on disk, and the writer reports success."""
    assert save_snapshot(tmp_path, "f_1756000000_ab12cd", _frame()) is True

    written = tmp_path / SNAPSHOT_DIRNAME / "f_1756000000_ab12cd.jpg"
    assert written.is_file()
    assert written.read_bytes().startswith(JPEG_MAGIC)
    assert written.stat().st_size > 0


def test_save_snapshot_leaves_no_temporary_file_behind(tmp_path: Path) -> None:
    """The write is tmp+rename: after it, the directory holds exactly one file."""
    assert save_snapshot(tmp_path, "f_1756000000_ab12cd", _frame()) is True

    contents = sorted(p.name for p in (tmp_path / SNAPSHOT_DIRNAME).iterdir())
    assert contents == ["f_1756000000_ab12cd.jpg"]


def test_save_snapshot_overwrites_on_re_enrollment(tmp_path: Path) -> None:
    """One snapshot per person: a second enrollment replaces the first."""
    assert save_snapshot(tmp_path, "f_1_abcdef", _frame(height=48, width=64)) is True
    first = (tmp_path / SNAPSHOT_DIRNAME / "f_1_abcdef.jpg").read_bytes()

    assert save_snapshot(tmp_path, "f_1_abcdef", _frame(200, height=96, width=128)) is True
    second = (tmp_path / SNAPSHOT_DIRNAME / "f_1_abcdef.jpg").read_bytes()

    assert second.startswith(JPEG_MAGIC)
    assert second != first
    assert sorted(p.name for p in (tmp_path / SNAPSHOT_DIRNAME).iterdir()) == ["f_1_abcdef.jpg"]


# --- save_snapshot: every failure is a warning and a False --------------------


def test_save_snapshot_reports_false_when_the_encoder_cannot_be_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A missing ffmpeg binary logs a warning and returns False — it never raises."""
    monkeypatch.setattr(face_snapshot, "_ffmpeg_exe", lambda: str(tmp_path / "no-such-ffmpeg"))

    with caplog.at_level("WARNING", logger="reachy_companion.face_snapshot"):
        assert save_snapshot(tmp_path, "f_1_abcdef", _frame()) is False

    assert "snapshot" in caplog.text.lower()
    assert not (tmp_path / SNAPSHOT_DIRNAME).exists() or list((tmp_path / SNAPSHOT_DIRNAME).iterdir()) == []


def test_save_snapshot_reports_false_when_the_encoder_exits_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """An encoder that runs and fails leaves nothing behind — no tmp, no truncated jpg."""
    monkeypatch.setattr(face_snapshot, "_ffmpeg_exe", lambda: "/usr/bin/false")

    with caplog.at_level("WARNING", logger="reachy_companion.face_snapshot"):
        assert save_snapshot(tmp_path, "f_1_abcdef", _frame()) is False

    assert "snapshot" in caplog.text.lower()
    assert list((tmp_path / SNAPSHOT_DIRNAME).iterdir()) == []


def test_save_snapshot_reports_false_when_ffmpeg_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """No wheel-bundled binary at all is a warning and a False, not an exception."""
    monkeypatch.setattr(face_snapshot, "_ffmpeg_exe", lambda: None)

    with caplog.at_level("WARNING", logger="reachy_companion.face_snapshot"):
        assert save_snapshot(tmp_path, "f_1_abcdef", _frame()) is False

    assert "snapshot" in caplog.text.lower()


def test_save_snapshot_bounds_the_encoder_and_survives_the_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The subprocess is bounded; an expired encode is killed, warned about, and False."""
    seen: list[float] = []

    def _timeout(cmd: list[str], payload: bytes, timeout_s: float) -> subprocess.CompletedProcess[bytes]:
        seen.append(timeout_s)
        raise subprocess.TimeoutExpired(cmd, timeout_s)

    monkeypatch.setattr(face_snapshot, "_run_encoder", _timeout)

    with caplog.at_level("WARNING", logger="reachy_companion.face_snapshot"):
        assert save_snapshot(tmp_path, "f_1_abcdef", _frame()) is False

    assert seen == [face_snapshot.SNAPSHOT_TIMEOUT_S]
    assert face_snapshot.SNAPSHOT_TIMEOUT_S == 10.0
    assert list((tmp_path / SNAPSHOT_DIRNAME).iterdir()) == []


def test_save_snapshot_encodes_a_raw_bgr_frame_at_its_own_size(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The argv describes the frame exactly: rawvideo, bgr24, WxH, one frame, JPEG quality."""
    seen: dict[str, Any] = {}

    def _record(cmd: list[str], payload: bytes, timeout_s: float) -> subprocess.CompletedProcess[bytes]:
        seen["cmd"] = cmd
        seen["payload"] = payload
        return subprocess.CompletedProcess(cmd, 1, b"", b"stopped")

    monkeypatch.setattr(face_snapshot, "_run_encoder", _record)
    frame = _frame(height=48, width=64)

    assert save_snapshot(tmp_path, "f_1_abcdef", frame) is False

    cmd = seen["cmd"]
    assert "rawvideo" in cmd and "bgr24" in cmd and "64x48" in cmd
    assert cmd[cmd.index("-frames:v") + 1] == "1"
    assert cmd[cmd.index("-q:v") + 1] == "4"
    assert cmd[-1].endswith(".tmp")
    assert seen["payload"] == frame.tobytes()


@pytest.mark.parametrize(
    "frame",
    [
        None,
        np.zeros((0, 0, 3), dtype=np.uint8),
        np.zeros((4, 4), dtype=np.uint8),
        np.zeros((4, 4, 4), dtype=np.uint8),
        np.zeros((4, 4, 3), dtype=np.float32),
    ],
)
def test_save_snapshot_refuses_a_frame_that_is_not_bgr_uint8(tmp_path: Path, frame: Any) -> None:
    """A frame the encoder cannot describe is refused before the subprocess starts."""
    assert save_snapshot(tmp_path, "f_1_abcdef", frame) is False


def test_save_snapshot_refuses_an_unusable_record_id(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """A record id that is a path is refused by the writer too, not just the path helper."""
    with caplog.at_level("WARNING", logger="reachy_companion.face_snapshot"):
        assert save_snapshot(tmp_path, "../escape", _frame()) is False

    assert not (tmp_path / SNAPSHOT_DIRNAME).exists()


def test_save_snapshot_survives_an_unwritable_instance_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """An OSError anywhere in the write is a warning and a False."""

    def _boom(*args: Any, **kwargs: Any) -> None:
        raise OSError("read-only file system")

    monkeypatch.setattr(Path, "mkdir", _boom)

    with caplog.at_level("WARNING", logger="reachy_companion.face_snapshot"):
        assert save_snapshot(tmp_path, "f_1_abcdef", _frame()) is False

    assert "snapshot" in caplog.text.lower()
