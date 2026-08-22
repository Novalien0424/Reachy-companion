"""Contract tests for the yt-dlp / ffmpeg layer (D-018, R8). No network, ever."""

import os
import time
import subprocess

import pytest

from reachy_companion.hanova import ytdlp


def _completed(stdout: str = "", stderr: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["yt-dlp"], returncode=returncode, stdout=stdout, stderr=stderr)


@pytest.fixture(autouse=True)
def available(monkeypatch):
    """Pretend both wheels are installed unless a test says otherwise."""
    monkeypatch.setattr(ytdlp, "ytdlp_available", lambda: True)
    monkeypatch.setattr(ytdlp, "ffmpeg_exe", lambda: "/opt/ffmpeg")
    monkeypatch.delenv("HANOVA_YTDLP_SEARCH_N", raising=False)
    # The argv tests assert the *default* timeout, which an operator's own
    # override in the ambient environment would otherwise turn into a failure
    # on their machine and nowhere else (Task 4 review).
    monkeypatch.delenv("HANOVA_YTDLP_TIMEOUT_S", raising=False)
    monkeypatch.delenv("HANOVA_YTDLP_DOWNLOAD_TIMEOUT_S", raising=False)


def test_search_builds_the_upstream_argv(monkeypatch):
    """Same search contract upstream used: ytsearchN, live/short filtered out."""
    seen = {}

    def fake_run(cmd, timeout_s):
        seen["cmd"] = cmd
        seen["timeout_s"] = timeout_s
        return _completed("dQw4w9WgXcQ\nA Song Title\n")

    monkeypatch.setattr(ytdlp, "run_command", fake_run)
    out = ytdlp.search("some song")
    assert out == {"ok": True, "id": "dQw4w9WgXcQ", "title": "A Song Title", "error": None}
    cmd = seen["cmd"]
    assert "--default-search" in cmd and "ytsearch5:" in cmd
    assert cmd[cmd.index("--match-filter") + 1] == "duration > 30 & !is_live"
    assert "--no-playlist" in cmd and "--skip-download" in cmd
    assert cmd[-1] == "some song"
    assert seen["timeout_s"] == 20


def test_search_honours_a_max_duration(monkeypatch):
    """Music we will download whole needs an upper duration bound."""
    seen = {}

    def fake_run(cmd, timeout_s):
        seen["cmd"] = cmd
        return _completed("abc\nTitle\n")

    monkeypatch.setattr(ytdlp, "run_command", fake_run)
    ytdlp.search("some song", max_duration_s=900)
    assert seen["cmd"][seen["cmd"].index("--match-filter") + 1] == "duration > 30 & duration < 900 & !is_live"


def test_search_without_the_wheel_is_reported(monkeypatch):
    """A robot missing yt-dlp answers, it does not raise."""
    monkeypatch.setattr(ytdlp, "ytdlp_available", lambda: False)
    out = ytdlp.search("anything")
    assert out["ok"] is False
    assert "yt-dlp" in out["error"]


def test_search_timeout_is_reported(monkeypatch):
    """A hung search must become a tool result inside its own budget."""

    def fake_run(cmd, timeout_s):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout_s)

    monkeypatch.setattr(ytdlp, "run_command", fake_run)
    out = ytdlp.search("anything")
    assert out["ok"] is False
    assert "timed out" in out["error"]


def test_search_empty_result_is_reported(monkeypatch):
    """YouTube rate-limits bursts with a clean exit and no output."""
    monkeypatch.setattr(ytdlp, "run_command", lambda cmd, timeout_s: _completed(""))
    out = ytdlp.search("anything")
    assert out["ok"] is False
    assert out["id"] is None


def test_search_extraction_error_is_reported_without_the_stderr(monkeypatch):
    """A hard yt-dlp failure must be reported, but not by forwarding stderr.

    Round 2, finding 6: yt-dlp's stderr echoes the query and the resolved URL
    straight back, and the previous version returned its last 300 characters as
    the tool's `error` -- which the model then reads out loud.
    """
    monkeypatch.setattr(
        ytdlp,
        "run_command",
        lambda cmd, timeout_s: _completed("", "ERROR: SENTINEL_PRIVATE_x7 is unavailable", returncode=1),
    )
    out = ytdlp.search("anything")
    assert out["ok"] is False
    assert out["id"] is None
    assert "SENTINEL_PRIVATE_x7" not in out["error"]


def test_the_ytdlp_layer_logs_no_query_url_or_stderr(monkeypatch, caplog, tmp_path):
    """Round 2, finding 6: ytdlp.py is a service seam and logs like one."""
    import logging

    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(
        ytdlp,
        "run_command",
        lambda cmd, timeout_s: _completed("", "ERROR: https://example.invalid/SENTINEL_PRIVATE_x7", returncode=1),
    )
    ytdlp.search("SENTINEL_PRIVATE_x7")
    ytdlp.download_audio("SENTINEL_PRIVATE_x7", tmp_path)

    def boom(cmd, timeout_s):
        raise OSError("cannot run SENTINEL_PRIVATE_x7")

    monkeypatch.setattr(ytdlp, "run_command", boom)
    ytdlp.search("anything")
    ytdlp.download_audio("abc123", tmp_path)
    assert "SENTINEL_PRIVATE_x7" not in caplog.text


def test_download_audio_reuses_a_cached_file(monkeypatch, tmp_path):
    """Repeat plays must be instant and must not touch the network."""
    cached = tmp_path / "abc123.mp3"
    cached.write_bytes(b"ID3data")

    def fail_run(cmd, timeout_s):
        raise AssertionError("download_audio must not run yt-dlp for a cached track")

    monkeypatch.setattr(ytdlp, "run_command", fail_run)
    out = ytdlp.download_audio("abc123", tmp_path)
    assert out == {"ok": True, "path": str(cached), "cached": True, "error": None}


def test_a_cache_hit_refreshes_the_mtime(monkeypatch, tmp_path):
    """Task 4 review: the mtime LRU must order the cache by *use*, not by age.

    A replayed track is never rewritten, so without this touch it is the oldest
    entry in the music cache -- and the prune `play_music` runs immediately
    after starting it would delete the file currently on the speaker.
    """
    cached = tmp_path / "abc123.mp3"
    cached.write_bytes(b"ID3data")
    stale = time.time() - 86_400
    os.utime(cached, (stale, stale))

    def fail_run(cmd, timeout_s):
        raise AssertionError("download_audio must not run yt-dlp for a cached track")

    monkeypatch.setattr(ytdlp, "run_command", fail_run)
    ytdlp.download_audio("abc123", tmp_path)

    assert cached.stat().st_mtime > stale + 1, "the replayed track is still the LRU's first victim"


def test_download_audio_passes_the_bundled_ffmpeg(monkeypatch, tmp_path):
    """The wheel's ffmpeg is not on PATH, so yt-dlp must be pointed at it."""
    seen = {}

    def fake_run(cmd, timeout_s):
        seen["cmd"] = cmd
        (tmp_path / "abc123.mp3").write_bytes(b"ID3data")
        return _completed("")

    monkeypatch.setattr(ytdlp, "run_command", fake_run)
    out = ytdlp.download_audio("abc123", tmp_path)
    assert out["ok"] is True and out["cached"] is False
    cmd = seen["cmd"]
    assert cmd[cmd.index("--ffmpeg-location") + 1] == "/opt/ffmpeg"
    assert "--audio-format" in cmd and cmd[cmd.index("--audio-format") + 1] == "mp3"
    assert "https://www.youtube.com/watch?v=abc123" in cmd


def test_download_audio_reports_a_missing_output(monkeypatch, tmp_path):
    """yt-dlp can exit 0 and still produce nothing; that is a failure."""
    monkeypatch.setattr(
        ytdlp, "run_command", lambda cmd, timeout_s: _completed("", "boom SENTINEL_PRIVATE_x7", returncode=1)
    )
    out = ytdlp.download_audio("abc123", tmp_path)
    assert out["ok"] is False
    assert out["path"] is None
    # Round 2, finding 6: the tail of yt-dlp's output is not the tool's error.
    assert "SENTINEL_PRIVATE_x7" not in out["error"]


def test_download_audio_without_ffmpeg_is_reported(monkeypatch, tmp_path):
    """No transcoder means no mp3; say so instead of producing a broken file."""
    monkeypatch.setattr(ytdlp, "ffmpeg_exe", lambda: None)
    out = ytdlp.download_audio("abc123", tmp_path)
    assert out["ok"] is False
    assert "ffmpeg" in out["error"]


def test_cut_from_builds_a_seeking_ffmpeg_command(monkeypatch, tmp_path):
    """Resume-after-speech is implemented as a stream-copy seek."""
    seen = {}
    source = tmp_path / "abc123.mp3"
    source.write_bytes(b"ID3data")
    dest = tmp_path / "abc123.resume.mp3"

    def fake_run(cmd, timeout_s):
        seen["cmd"] = cmd
        dest.write_bytes(b"ID3cut")
        return _completed("")

    monkeypatch.setattr(ytdlp, "run_command", fake_run)
    assert ytdlp.cut_from(source, 42.5, dest) is True
    cmd = seen["cmd"]
    assert cmd[0] == "/opt/ffmpeg"
    assert cmd[cmd.index("-ss") + 1] == "42.500"
    assert "-c" in cmd and cmd[cmd.index("-c") + 1] == "copy"
    assert cmd[-1] == str(dest)


def test_cut_from_returns_false_on_failure(monkeypatch, tmp_path):
    """A failed trim leaves the caller free to give up cleanly."""
    source = tmp_path / "abc123.mp3"
    source.write_bytes(b"ID3data")
    monkeypatch.setattr(ytdlp, "run_command", lambda cmd, timeout_s: _completed("", "bad", returncode=1))
    assert ytdlp.cut_from(source, 10.0, tmp_path / "out.mp3") is False


# --- extractor args passthrough (on-robot finding, 2026-08-22) -------------
def test_extractor_args_are_absent_by_default(monkeypatch):
    """With the key unset, the argv carries no --extractor-args at all."""
    seen = {}

    def fake_run(cmd, timeout_s):
        seen["cmd"] = cmd
        return _completed("dQw4w9WgXcQ\nA Song Title\n")

    monkeypatch.setattr(ytdlp, "run_command", fake_run)
    monkeypatch.delenv("HANOVA_YTDLP_EXTRACTOR_ARGS", raising=False)
    assert ytdlp.search("some song")["ok"] is True
    assert "--extractor-args" not in seen["cmd"]


def test_extractor_args_reach_search_and_download(monkeypatch, tmp_path):
    """The configured value is forwarded to every yt-dlp invocation.

    YouTube intermittently refuses extraction without a JavaScript runtime the
    robot does not carry; the operator sets a player-client workaround here.
    """
    seen = {"cmds": []}

    def fake_run(cmd, timeout_s):
        seen["cmds"].append(cmd)
        return _completed("dQw4w9WgXcQ\nA Song Title\n")

    monkeypatch.setattr(ytdlp, "run_command", fake_run)
    monkeypatch.setenv("HANOVA_YTDLP_EXTRACTOR_ARGS", "youtube:player_client=android")
    assert ytdlp.search("some song")["ok"] is True
    ytdlp.download_audio("dQw4w9WgXcQ", tmp_path)
    for cmd in seen["cmds"]:
        assert cmd[cmd.index("--extractor-args") + 1] == "youtube:player_client=android"
