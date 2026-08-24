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
    assert "--default-search" in cmd and "ytsearch2:" in cmd
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


# --- native-container music downloads (latency work, 2026-08-22) -----------
def test_download_audio_without_transcode_fetches_bestaudio(monkeypatch, tmp_path):
    """Music skips the mp3 re-encode: bestaudio lands in its native container."""
    calls = []

    def fake_run(cmd, timeout_s):
        calls.append(cmd)
        if "volumedetect" in cmd:
            # Loud already: the normalize step measures and leaves it alone.
            return _completed(stderr="max_volume: -0.5 dB")
        (tmp_path / "abc123.m4a").write_bytes(b"M4Adata")
        return _completed("")

    monkeypatch.setattr(ytdlp, "run_command", fake_run)
    out = ytdlp.download_audio("abc123", tmp_path, transcode_mp3=False)
    assert out == {"ok": True, "path": str(tmp_path / "abc123.m4a"), "cached": False, "error": None}
    cmd = calls[0]
    # `/best` is the SABR fallback (audio-only formats can vanish per session),
    # and `-x` without a target format copies its audio track out untouched.
    assert cmd[cmd.index("-f") + 1] == "bestaudio[ext=m4a]/bestaudio/best"
    assert "-x" in cmd and "--audio-format" not in cmd and "--audio-quality" not in cmd


def test_download_audio_without_ffmpeg_fails_in_both_modes(monkeypatch, tmp_path):
    """Both modes need ffmpeg: to encode mp3, or to demux a muxed fallback."""
    monkeypatch.setattr(ytdlp, "ffmpeg_exe", lambda: None)
    for transcode in (True, False):
        out = ytdlp.download_audio("abc123", tmp_path, transcode_mp3=transcode)
        assert out["ok"] is False and "ffmpeg" in out["error"]


def test_a_cached_mp3_still_serves_the_no_transcode_mode(monkeypatch, tmp_path):
    """A track cached under the old mp3 mode keeps serving after the switch."""
    cached = tmp_path / "abc123.mp3"
    cached.write_bytes(b"ID3data")

    def fail_run(cmd, timeout_s):
        raise AssertionError("a cached track must not touch the network")

    monkeypatch.setattr(ytdlp, "run_command", fail_run)
    out = ytdlp.download_audio("abc123", tmp_path, transcode_mp3=False)
    assert out == {"ok": True, "path": str(cached), "cached": True, "error": None}


# --- loudness normalization (2026-08-24: "music much quieter than Reachy") ---


def test_normalize_gain_matches_a_quiet_track(monkeypatch, tmp_path):
    """A -12.1 dBFS peak gets exactly the gain that lands it at -1 dBFS, as mono wav."""
    source = tmp_path / "abc123.m4a"
    source.write_bytes(b"m4a")
    calls = []

    def fake_run(cmd, timeout_s):
        calls.append(cmd)
        if "volumedetect" in cmd:
            return _completed(stderr="[Parsed_volumedetect_0] max_volume: -12.1 dB")
        (tmp_path / "abc123.wav").write_bytes(b"wav")
        return _completed()

    monkeypatch.setattr(ytdlp, "run_command", fake_run)
    out = ytdlp.normalize_loudness(source)
    assert out == tmp_path / "abc123.wav"
    assert not source.exists(), "the quiet original must be replaced"
    gain_cmd = calls[1]
    assert gain_cmd[gain_cmd.index("-af") + 1] == "volume=11.10dB"
    assert gain_cmd[gain_cmd.index("-ac") + 1] == "1"
    assert gain_cmd[gain_cmd.index("-acodec") + 1] == "pcm_s16le"


def test_normalize_skips_an_already_loud_track(monkeypatch, tmp_path):
    """Under half a dB of headroom, the rewrite is inaudible and skipped."""
    source = tmp_path / "abc123.m4a"
    source.write_bytes(b"m4a")
    calls = []

    def fake_run(cmd, timeout_s):
        calls.append(cmd)
        return _completed(stderr="max_volume: -1.2 dB")

    monkeypatch.setattr(ytdlp, "run_command", fake_run)
    assert ytdlp.normalize_loudness(source) == source
    assert source.exists()
    assert len(calls) == 1, "only the measurement pass may run"


def test_normalize_caps_the_gain(monkeypatch, tmp_path):
    """A near-silent file is amplified by the cap, not by sixty dB."""
    source = tmp_path / "abc123.m4a"
    source.write_bytes(b"m4a")
    calls = []

    def fake_run(cmd, timeout_s):
        calls.append(cmd)
        if "volumedetect" in cmd:
            return _completed(stderr="max_volume: -61.0 dB")
        (tmp_path / "abc123.wav").write_bytes(b"wav")
        return _completed()

    monkeypatch.setattr(ytdlp, "run_command", fake_run)
    ytdlp.normalize_loudness(source)
    gain_cmd = calls[1]
    assert gain_cmd[gain_cmd.index("-af") + 1] == "volume=24.00dB"


def test_normalize_failure_keeps_the_original(monkeypatch, tmp_path):
    """A failed rewrite must leave the quiet-but-playable original in place."""
    source = tmp_path / "abc123.m4a"
    source.write_bytes(b"m4a")

    def fake_run(cmd, timeout_s):
        if "volumedetect" in cmd:
            return _completed(stderr="max_volume: -12.0 dB")
        (tmp_path / "abc123.wav").write_bytes(b"")  # empty artifact
        return _completed(returncode=1)

    monkeypatch.setattr(ytdlp, "run_command", fake_run)
    assert ytdlp.normalize_loudness(source) == source
    assert source.exists()
    assert not (tmp_path / "abc123.wav").exists(), "the failed artifact must be removed"


def test_normalize_returns_a_wav_untouched(monkeypatch, tmp_path):
    """Our own rewrites are never re-measured: same-path in/out would corrupt."""
    source = tmp_path / "abc123.wav"
    source.write_bytes(b"wav")

    def fake_run(cmd, timeout_s):
        raise AssertionError("a wav input must not spawn ffmpeg at all")

    monkeypatch.setattr(ytdlp, "run_command", fake_run)
    assert ytdlp.normalize_loudness(source) == source


def test_download_audio_normalizes_fresh_native_downloads(monkeypatch, tmp_path):
    """The native (music) mode hands its fresh download to the normalizer."""
    seen = {}

    def fake_run(cmd, timeout_s):
        (tmp_path / "abc123.m4a").write_bytes(b"m4a")
        return _completed()

    def fake_normalize(path):
        seen["source"] = path
        wav = tmp_path / "abc123.wav"
        wav.write_bytes(b"wav")
        return wav

    monkeypatch.setattr(ytdlp, "run_command", fake_run)
    monkeypatch.setattr(ytdlp, "normalize_loudness", fake_normalize)
    out = ytdlp.download_audio("abc123", tmp_path, transcode_mp3=False)
    assert out["ok"] is True
    assert out["path"].endswith("abc123.wav")
    assert seen["source"] == tmp_path / "abc123.m4a"


def test_download_audio_upgrades_a_quiet_cache_hit(monkeypatch, tmp_path):
    """A pre-normalization cache entry is upgraded once instead of playing quiet forever."""
    cached = tmp_path / "abc123.m4a"
    cached.write_bytes(b"m4a")
    monkeypatch.setattr(ytdlp, "run_command", lambda cmd, timeout_s: pytest.fail("no yt-dlp for a cache hit"))

    def fake_normalize(path):
        wav = tmp_path / "abc123.wav"
        wav.write_bytes(b"wav")
        return wav

    monkeypatch.setattr(ytdlp, "normalize_loudness", fake_normalize)
    out = ytdlp.download_audio("abc123", tmp_path, transcode_mp3=False)
    assert out == {"ok": True, "path": str(tmp_path / "abc123.wav"), "cached": True, "error": None}


def test_gag_mp3_mode_keeps_the_original_level(monkeypatch, tmp_path):
    """The mp3 (gag) mode never rewrites: its cut pipeline and level are tuned."""
    cached = tmp_path / "abc123.mp3"
    cached.write_bytes(b"mp3")
    monkeypatch.setattr(ytdlp, "run_command", lambda cmd, timeout_s: pytest.fail("no yt-dlp for a cache hit"))
    monkeypatch.setattr(ytdlp, "normalize_loudness", lambda path: pytest.fail("gags must not be normalized"))
    out = ytdlp.download_audio("abc123", tmp_path)
    assert out["path"] == str(cached)


def test_the_normalized_wav_wins_the_cache_lookup(monkeypatch, tmp_path):
    """When a leftover original coexists with the wav, the wav is served."""
    (tmp_path / "abc123.m4a").write_bytes(b"m4a")
    (tmp_path / "abc123.wav").write_bytes(b"wav")
    monkeypatch.setattr(ytdlp, "run_command", lambda cmd, timeout_s: pytest.fail("no yt-dlp for a cache hit"))
    out = ytdlp.download_audio("abc123", tmp_path, transcode_mp3=False)
    assert out["path"] == str(tmp_path / "abc123.wav")
