"""The app-start refresh of the NAS video index from the share's copy (2026-10-31).

The operator's nightly Mac pass mirrors the index to `<subpath>/.index.json`;
`refresh_index_from_nas` swaps it in for the instance copy only when it is newer
and sane, and leaves the instance copy untouched on every other outcome.
"""

from __future__ import annotations
import io
import sys
import json
import logging
import threading
from types import SimpleNamespace
from pathlib import Path

import pytest

from reachy_companion.hanova import nas


def _index(n: int, generated_at: float) -> dict:
    videos = [{"path": f"SRC/Trip/{i:02d}.mov", "top_folder": "Trip", "year": 2026} for i in range(n)]
    return {"generated_at": generated_at, "root": "SRC", "video_count": n, "folders": {"Trip": {}}, "videos": videos}


@pytest.fixture
def instance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Write an instance index of 10 clips, generated at t=1000, and configure the NAS."""
    path = tmp_path / "nas-video-index.json"
    path.write_text(json.dumps(_index(10, 1000.0)), encoding="utf-8")
    for key, value in {
        "HANOVA_NAS_HOST": "nas.example.invalid",
        "HANOVA_NAS_USER": "u",
        "HANOVA_NAS_PASSWORD": "p",
        "HANOVA_NAS_SHARE": "SENTINEL_SHARE_r1",
        "HANOVA_NAS_SUBPATH": "SENTINEL_SRC_r1",
        "HANOVA_NAS_INDEX_PATH": str(path),
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("HANOVA_NAS_INDEX_REFRESH", raising=False)
    return path


def _fake_share(monkeypatch: pytest.MonkeyPatch, payload: bytes | None, seen: dict | None = None) -> None:
    """Serve *payload* as the share's `.index.json`; None means the NAS is unreachable."""
    seen = seen if seen is not None else {}

    class _Client:
        @staticmethod
        def register_session(host, username=None, password=None, connection_timeout=None):
            if payload is None:
                raise OSError(r"connect to \\nas.example.invalid\SENTINEL_SHARE_r1 failed")
            seen["timeout"] = connection_timeout

        @staticmethod
        def stat(path):
            seen["remote"] = path
            return SimpleNamespace(st_size=len(payload or b""))

        @staticmethod
        def open_file(path, mode="rb"):
            return io.BytesIO(payload or b"")

    monkeypatch.setitem(sys.modules, "smbclient", _Client)


def test_a_newer_copy_replaces_the_instance_copy_and_keeps_the_old_one(instance, monkeypatch, caplog):
    """Newer and sane: renamed into place at mode 600, the old copy kept as `.prev`."""
    newer = json.dumps(_index(12, 2000.0)).encode("utf-8")
    seen: dict = {}
    _fake_share(monkeypatch, newer, seen)
    before = instance.read_bytes()
    with caplog.at_level(logging.INFO, logger=nas.logger.name):
        assert nas.refresh_index_from_nas() == "updated"
    assert instance.read_bytes() == newer
    assert (instance.parent / (instance.name + ".prev")).read_bytes() == before
    assert instance.stat().st_mode & 0o777 == 0o600
    assert not list(instance.parent.glob("*.part")), "the partial file is renamed, never left behind"
    assert seen["remote"] == r"\\nas.example.invalid\SENTINEL_SHARE_r1\SENTINEL_SRC_r1\.index.json"
    assert seen["timeout"] == nas._SMB_CONNECT_TIMEOUT_S
    assert "10 -> 12 videos" in caplog.text
    assert nas.load_index()["video_count"] == 12


def test_an_older_or_equal_copy_changes_nothing(instance, monkeypatch):
    """`generated_at` decides; a copy no newer than the robot's is left alone."""
    before = instance.read_bytes()
    for generated_at in (1000.0, 999.0):
        _fake_share(monkeypatch, json.dumps(_index(12, generated_at)).encode("utf-8"))
        assert nas.refresh_index_from_nas() == "current"
    assert instance.read_bytes() == before
    assert not (instance.parent / (instance.name + ".prev")).exists()


@pytest.mark.parametrize(
    "payload",
    [
        b"{not json",
        json.dumps([1, 2]).encode(),
        json.dumps({**_index(0, 2000.0)}).encode(),
        json.dumps({**_index(12, 2000.0), "folders": None}).encode(),
        json.dumps(_index(4, 2000.0)).encode(),  # under half of the 10 the robot knows
    ],
    ids=["not-json", "not-an-object", "no-videos", "no-folders", "shrunk"],
)
def test_a_broken_copy_is_refused(instance, monkeypatch, payload):
    """A malformed, empty or shrunken listing never replaces a working index."""
    before = instance.read_bytes()
    _fake_share(monkeypatch, payload)
    assert nas.refresh_index_from_nas() == "refused"
    assert instance.read_bytes() == before


def test_an_unreachable_nas_keeps_the_copy_and_logs_no_share_path(instance, monkeypatch, caplog):
    """The SMB error text carries the share path; the log line must not."""
    before = instance.read_bytes()
    _fake_share(monkeypatch, None)
    with caplog.at_level(logging.INFO, logger=nas.logger.name):
        assert nas.refresh_index_from_nas() == "unavailable"
    assert instance.read_bytes() == before
    assert "SENTINEL_SHARE_r1" not in caplog.text
    assert "nas.example.invalid" not in caplog.text


def test_the_first_copy_lands_when_the_robot_has_none(instance, monkeypatch):
    """With no instance copy at all, the NAS copy becomes it, and there is no `.prev`."""
    instance.unlink()
    _fake_share(monkeypatch, json.dumps(_index(3, 2000.0)).encode("utf-8"))
    assert nas.refresh_index_from_nas() == "updated"
    assert nas.load_index()["video_count"] == 3
    assert not (instance.parent / (instance.name + ".prev")).exists()


def test_unconfigured_nas_is_a_quiet_no(instance, monkeypatch):
    """Missing NAS settings are an outcome, not an exception."""
    monkeypatch.delenv("HANOVA_NAS_HOST")
    assert nas.refresh_index_from_nas() == "not_configured"


def test_start_runs_on_a_daemon_thread_and_has_a_switch(instance, monkeypatch):
    """Startup never waits on the NAS; `HANOVA_NAS_INDEX_REFRESH=0` turns it off."""
    calls: list[str] = []
    monkeypatch.setattr(nas, "refresh_index_from_nas", lambda: calls.append(threading.current_thread().name))
    thread = nas.start_index_refresh()
    assert thread is not None and thread.daemon
    thread.join(timeout=5)
    assert calls == ["nas-index-refresh"]
    monkeypatch.setenv("HANOVA_NAS_INDEX_REFRESH", "0")
    assert nas.start_index_refresh() is None
