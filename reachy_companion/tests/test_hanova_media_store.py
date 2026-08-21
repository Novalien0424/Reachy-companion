"""Contract tests for the LAN media cache and its static route (D-018, R6)."""

import os
import time
import types

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from reachy_companion.hanova import settings, media_store


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Start from no media configuration and a mount that has not come up."""
    monkeypatch.delenv("HANOVA_MEDIA_DIR", raising=False)
    monkeypatch.delenv("HANOVA_MEDIA_HTTP_BASE", raising=False)
    settings.set_media_mount_ready(False)
    yield
    settings.set_media_mount_ready(False)


def test_media_root_defaults_under_the_instance_directory(tmp_path):
    """Cached media lives with .env / memory / faces, so the deploy ritual sees it."""
    assert media_store.media_root(tmp_path) == tmp_path / "hanova_media"


def test_media_dir_override_wins(monkeypatch, tmp_path):
    """A full disk on the robot can be worked around without code changes."""
    monkeypatch.setenv("HANOVA_MEDIA_DIR", str(tmp_path / "elsewhere"))
    assert media_store.media_root(tmp_path) == tmp_path / "elsewhere"


def test_media_root_without_an_instance_path_uses_a_temp_dir():
    """Tests and `--ui`-less runs have no instance path; that must not crash."""
    root = media_store.media_root(None)
    assert root.name == "reachy_companion_hanova_media"


def test_media_dir_creates_the_kind_subdirectory(tmp_path):
    """Callers get a directory that exists, every time."""
    music = media_store.media_dir("music", tmp_path)
    assert music == tmp_path / "hanova_media" / "music"
    assert music.is_dir()


def test_media_dir_rejects_an_unknown_kind(tmp_path):
    """Only the four known kinds are servable; a typo is a programming error."""
    with pytest.raises(ValueError):
        media_store.media_dir("secrets", tmp_path)


def test_prune_keeps_the_newest_files(tmp_path):
    """Keep-N cleanup is what stops the CM4 filling up with home videos."""
    nas = media_store.media_dir("nas", tmp_path)
    for index in range(5):
        path = nas / f"clip{index}.mp4"
        path.write_bytes(b"x")
        # Distinct mtimes so "newest" is unambiguous on a coarse-resolution FS.
        os.utime(path, (time.time() + index, time.time() + index))
    removed = media_store.prune("nas", tmp_path, keep=2)
    assert removed == 3
    assert sorted(p.name for p in nas.iterdir()) == ["clip3.mp4", "clip4.mp4"]


def test_prune_on_an_empty_cache_is_zero(tmp_path):
    """First run has nothing to prune and must not raise."""
    assert media_store.prune("music", tmp_path, keep=12) == 0


def test_media_url_is_none_without_a_lan_base():
    """Without HANOVA_MEDIA_HTTP_BASE there is no URL a TV could fetch."""
    assert media_store.media_url("nas", "clip.mp4") is None


def test_media_url_composes_base_prefix_kind_and_name(monkeypatch):
    """The URL shape is what the HA cast script receives."""
    monkeypatch.setenv("HANOVA_MEDIA_HTTP_BASE", "http://robot.example.invalid:7860/")
    assert media_store.media_url("nas", "clip.mp4") == "http://robot.example.invalid:7860/hanova-media/nas/clip.mp4"


def test_there_is_no_truncating_filename_helper():
    """Review finding 15: `safe_filename` flattened *and truncated* a path.

    Two clips whose paths agree for the first 150 characters mapped to the same
    served name, so one home video silently played in place of another. The
    helper is removed rather than fixed: `nas.cast_filename` derives its name
    from a hash of the validated path, and nothing else needs to flatten one.
    Keeping a collision-prone helper around is an invitation to reintroduce it.
    """
    assert not hasattr(media_store, "safe_filename")


def test_mount_media_routes_serves_a_cached_file(tmp_path):
    """R6 end to end: a file in the cache is fetchable over the app's own server."""
    images = media_store.media_dir("images", tmp_path)
    (images / "poster.png").write_bytes(b"PNGDATA")

    app = FastAPI()
    assert media_store.mount_media_routes(app, tmp_path) is True

    with TestClient(app) as client:
        response = client.get("/hanova-media/images/poster.png")
    assert response.status_code == 200
    assert response.content == b"PNGDATA"


def test_mount_media_routes_reports_failure_instead_of_raising(tmp_path):
    """A settings app that cannot mount must not brick startup."""

    class _NoMount:
        pass

    assert media_store.mount_media_routes(_NoMount(), tmp_path) is False


# --- mount readiness feeds tool availability (review finding 11) ----------
def test_mount_readiness_is_recorded_for_the_availability_table(tmp_path):
    """A successful mount is what makes URL casting a legal thing to offer."""
    settings.set_media_mount_ready(False)
    try:
        assert media_store.mount_media_routes(FastAPI(), tmp_path) is True
        assert settings.media_mount_ready() is True
    finally:
        settings.set_media_mount_ready(False)


def test_a_failed_mount_leaves_casting_unavailable(tmp_path):
    """Finding 11: the boolean must not be discarded; casting depends on it."""

    class _NoMount:
        pass

    settings.set_media_mount_ready(True)
    try:
        assert media_store.mount_media_routes(_NoMount(), tmp_path) is False
        assert settings.media_mount_ready() is False
    finally:
        settings.set_media_mount_ready(False)


def test_url_casting_tools_are_unavailable_when_the_mount_failed(monkeypatch, tmp_path):
    """The end of the chain: no live route means show_on_tv reports not_configured."""
    monkeypatch.setenv("HA_URL", "http://ha.example.invalid:8123")
    monkeypatch.setenv("HA_TOKEN", "tok")
    monkeypatch.setenv("HANOVA_HA_SCRIPT_IMAGE_URL", "tv_show_image_url")
    monkeypatch.setenv("HANOVA_MEDIA_HTTP_BASE", "http://robot.example.invalid:7860")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    settings.set_media_mount_ready(False)
    try:
        assert settings.tool_status("show_on_tv") == (False, "HANOVA_MEDIA_MOUNT")
        settings.set_media_mount_ready(True)
        assert settings.tool_available("show_on_tv") is True
    finally:
        settings.set_media_mount_ready(False)


# --- the real console app, not a fresh FastAPI (review finding 11) --------
def _real_console_stream(tmp_path):
    """Build the real `console.LocalStream` with the arguments it actually takes.

    Round 2, finding 13: the previous version called
    `LocalStream(instance_path=...)`, but the constructor is
    `LocalStream(handler, robot, *, settings_app=None, instance_path=None, ...)`
    (`console.py:104-113`) -- so both tests died with `TypeError` before they
    ever reached the mount they were written to prove. The two positional
    arguments are supplied here as the smallest fakes that satisfy what the
    constructor really touches:

    * `handler` -- `_install_handler` assigns `handler._clear_queue` and then
      looks for `set_activity_observer` / `set_transcript_observer` with
      `getattr(..., None)`, so a `SimpleNamespace` is sufficient and a
      `MagicMock` would silently satisfy the observer checks with mocks.
    * `robot` -- only stored on `self._robot` during construction.

    `settings_app` is passed **explicitly** as a real `FastAPI`, because
    `_init_settings_ui_if_needed` returns immediately when it is None
    (`console.py:516-517`) -- which is how a green-but-vacuous version of this
    test could otherwise exist.
    """
    from reachy_companion import console as console_module

    handler = types.SimpleNamespace(_clear_queue=None)
    robot = types.SimpleNamespace()
    app = FastAPI()
    stream = console_module.LocalStream(
        handler,
        robot,
        settings_app=app,
        instance_path=str(tmp_path),
    )
    stream._init_settings_ui_if_needed()
    # The accessor added in Step 5. Asserting on it rather than on the private
    # attribute is what keeps the production mount hook observable.
    assert stream.settings_app is app, "the console must expose the app it mounts onto"
    return stream


def test_the_real_settings_app_serves_the_cache_with_range_and_type(tmp_path):
    """A Chromecast issues HEAD and byte-range GETs before it plays anything.

    Finding 11: mounting a *fresh* FastAPI proves nothing about the app the robot
    actually runs. This drives `console.py`'s own settings-app wiring through
    `_init_settings_ui_if_needed`, then exercises the three request shapes a
    Chromecast really uses: HEAD for the length and content type, a full GET,
    and a `Range` GET for the seek.
    """
    nas_dir = media_store.media_dir("nas", tmp_path)
    payload = b"MP4" + bytes(1021)
    (nas_dir / "clip.mp4").write_bytes(payload)

    stream = _real_console_stream(tmp_path)

    with TestClient(stream.settings_app) as client:
        head = client.head("/hanova-media/nas/clip.mp4")
        full = client.get("/hanova-media/nas/clip.mp4")
        ranged = client.get("/hanova-media/nas/clip.mp4", headers={"Range": "bytes=0-15"})

    assert head.status_code == 200
    assert head.headers["content-type"].startswith("video/mp4")
    assert int(head.headers["content-length"]) == len(payload)

    assert full.status_code == 200
    assert full.content == payload

    assert ranged.status_code == 206
    assert ranged.headers["content-range"] == f"bytes 0-15/{len(payload)}"
    assert ranged.content == payload[:16]


def test_the_real_settings_app_refuses_a_traversal(tmp_path):
    """The static mount must not be walkable out of the media root."""
    media_store.media_dir("nas", tmp_path)
    (tmp_path / "secret.txt").write_text("SENTINEL_PRIVATE_x7", encoding="utf-8")

    stream = _real_console_stream(tmp_path)

    with TestClient(stream.settings_app) as client:
        response = client.get("/hanova-media/nas/../../secret.txt")
    assert response.status_code in (403, 404)
    assert b"SENTINEL_PRIVATE_x7" not in response.content


def test_the_media_store_logs_no_path(monkeypatch, caplog, tmp_path):
    """Round 2, finding 6: media_store is a service seam and logs like one.

    The prune and mount paths used to log the cache directory and a raw
    `logger.exception` traceback, both of which name the instance directory.
    """
    import logging

    caplog.set_level(logging.DEBUG)
    sentinel_root = tmp_path / "SENTINEL_PRIVATE_x7"
    media_dir = media_store.media_dir("music", sentinel_root)
    (media_dir / "a.mp3").write_bytes(b"ID3")

    class _NoMount:
        pass

    media_store.mount_media_routes(_NoMount(), sentinel_root)
    media_store.prune("music", sentinel_root, 0)
    assert "SENTINEL_PRIVATE_x7" not in caplog.text
