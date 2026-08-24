"""yt-dlp and ffmpeg layer for music playback (D-018, R8).

Upstream shelled out to Homebrew binaries (`server.py:258`, `:338-341`). We have
no system packages, so both come from wheels: `yt-dlp` is invoked as
`sys.executable -m yt_dlp` (the console script is not reliably on PATH inside the
robot's shared apps venv), and ffmpeg comes from `imageio-ffmpeg`, which ships a
`manylinux2014_aarch64` wheel with the binary inside it and exposes
`get_ffmpeg_exe()`. `static-ffmpeg` was rejected: it downloads its binaries on
first use, which needs network at playback time.

Every subprocess goes through `run_command`, the one seam tests monkeypatch.
Everything here is synchronous and must be called from `asyncio.to_thread`.
"""

from __future__ import annotations
import os
import re
import sys
import logging
import subprocess
import importlib.util
from typing import Any, Dict
from pathlib import Path

from reachy_companion.hanova import redact, settings


logger = logging.getLogger(__name__)


def ytdlp_available() -> bool:
    """Return whether the yt-dlp wheel is importable in this interpreter."""
    return importlib.util.find_spec("yt_dlp") is not None


def ffmpeg_exe() -> str | None:
    """Return the path to the wheel-bundled ffmpeg binary, or None."""
    try:
        import imageio_ffmpeg

        path = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # noqa: BLE001 - a missing wheel must not raise here
        # Round 2, finding 6: an ImportError/OSError here renders the venv path.
        logger.warning("imageio-ffmpeg is unavailable: %s", redact.error(exc))
        return None
    return str(path) if path else None


def run_command(cmd: list[str], timeout_s: int) -> subprocess.CompletedProcess[str]:
    """Run one child process with captured text output. The single test seam."""
    env = os.environ.copy()
    env.setdefault("LANG", "en_US.UTF-8")
    env.setdefault("LC_ALL", "en_US.UTF-8")
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout_s,
        env=env,
        check=False,
    )


def _ytdlp_argv() -> list[str]:
    argv = [sys.executable, "-m", "yt_dlp"]
    extractor_args = settings.ytdlp_extractor_args()
    if extractor_args:
        argv += ["--extractor-args", extractor_args]
    return argv


def search(query: str, max_duration_s: int | None = None) -> Dict[str, Any]:
    """Resolve *query* to one YouTube id and title. Never raises."""
    if not ytdlp_available():
        return {"ok": False, "id": None, "title": None, "error": "yt-dlp is not installed on this robot"}

    match_filter = "duration > 30 & !is_live"
    if max_duration_s:
        match_filter = f"duration > 30 & duration < {int(max_duration_s)} & !is_live"

    cmd = _ytdlp_argv() + [
        "--default-search",
        f"ytsearch{settings.ytdlp_search_n()}:",
        "--match-filter",
        match_filter,
        "--no-playlist",
        "--print",
        "id",
        "--print",
        "title",
        "--skip-download",
        "--no-warnings",
        "--quiet",
        query,
    ]
    timeout_s = settings.ytdlp_timeout_s()
    try:
        proc = run_command(cmd, timeout_s)
    except subprocess.TimeoutExpired:
        return {"ok": False, "id": None, "title": None, "error": f"search timed out after {timeout_s}s"}
    except Exception as exc:  # noqa: BLE001
        # Round 2, finding 6: the exception text quotes the argv, which contains
        # the user's query.
        logger.warning("yt-dlp search failed: %s", redact.error(exc))
        return {"ok": False, "id": None, "title": None, "error": "the search could not be run"}

    lines = [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]
    if len(lines) >= 2:
        return {"ok": True, "id": lines[0], "title": lines[1], "error": None}
    if proc.returncode != 0:
        # Finding 6: yt-dlp's stderr echoes the query and the resolved URL back.
        # The shape reaches the log; the caller gets a fixed, speakable reason.
        # Round 3, finding 3: the old call passed the raw stderr with a word
        # allow-list, which is exactly the tokenizing that let an echoed value
        # through. stderr is free text nobody vouched for, so only its LENGTH is
        # loggable -- the return code above is the diagnostic that matters.
        logger.warning(
            "yt-dlp search exited %d, stderr %s",
            proc.returncode,
            redact.text(proc.stderr or ""),
        )
        return {"ok": False, "id": None, "title": None, "error": "the search was refused or returned nothing"}
    return {"ok": False, "id": None, "title": None, "error": "no playable result for that query"}


# Containers GStreamer's playbin decodes on the robot (faad, opusdec, mpg123
# all verified installed 2026-08-22). The cache lookup accepts any of them so a
# track downloaded under either mode keeps serving after the mode changes.
# `.wav` first: it is the loudness-normalized rewrite this module produces, and
# when both it and a leftover original exist the normalized one must win.
_AUDIO_EXTENSIONS: tuple[str, ...] = (".wav", ".m4a", ".mp3", ".webm", ".opus", ".ogg", ".aac")

# The voice chain peaks at -1 dBFS (D-017's soft-knee ceiling); YouTube tracks
# are loudness-normalized well below that (-12 dBFS peak measured on-robot,
# 2026-08-24), which the operator hears as "music much quieter than Reachy".
# Fresh music downloads are therefore gain-matched to the same peak. The rewrite
# is decode-only (measured ~150x realtime on the CM4), so it costs ~2 s per song
# where the removed mp3 re-encode cost ~10 s.
_NORMALIZE_TARGET_PEAK_DBFS = -1.0
# Below this the rewrite would be inaudible and is not worth a decode pass.
_NORMALIZE_MIN_GAIN_DB = 0.5
# A file this far down is noise or silence; amplifying it further helps nobody.
_NORMALIZE_MAX_GAIN_DB = 24.0
_VOLUMEDETECT_TIMEOUT_S = 60
_NORMALIZE_TIMEOUT_S = 120
_MAX_VOLUME_RE = re.compile(r"max_volume:\s*(-?\d+(?:\.\d+)?)\s*dB")


def measure_peak_dbfs(path: Path) -> float | None:
    """Return the file's peak level in dBFS via one decode pass, or None."""
    ffmpeg = ffmpeg_exe()
    if not ffmpeg or not Path(path).is_file():
        return None
    cmd = [ffmpeg, "-hide_banner", "-nostats", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"]
    try:
        proc = run_command(cmd, _VOLUMEDETECT_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001
        logger.warning("volumedetect failed: %s", redact.error(exc))
        return None
    match = _MAX_VOLUME_RE.search(proc.stderr or "")
    if proc.returncode != 0 or match is None:
        logger.warning("volumedetect reported no peak (rc=%s)", proc.returncode)
        return None
    return float(match.group(1))


def normalize_loudness(source: Path) -> Path:
    """Rewrite *source* as a gain-matched mono WAV peaking at -1 dBFS.

    Returns the WAV's path on success (the original is deleted) and *source*
    unchanged on any failure or when the track is already loud enough — a
    quiet track must still play. WAV keeps the rewrite decode-only; mono
    halves the file for a robot with one speaker; pure gain (no dynamics
    processing) preserves the mix. A `.wav` input is one of our own rewrites
    and is returned as-is: ffmpeg reading and writing the same path corrupts it.
    """
    source = Path(source)
    if source.suffix == ".wav":
        return source
    peak_dbfs = measure_peak_dbfs(source)
    if peak_dbfs is None:
        return source
    gain_db = _NORMALIZE_TARGET_PEAK_DBFS - peak_dbfs
    if gain_db < _NORMALIZE_MIN_GAIN_DB:
        return source
    gain_db = min(gain_db, _NORMALIZE_MAX_GAIN_DB)
    ffmpeg = ffmpeg_exe()
    if not ffmpeg:
        return source
    dest = source.with_suffix(".wav")
    cmd = [
        ffmpeg,
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-af",
        f"volume={gain_db:.2f}dB",
        "-ac",
        "1",
        "-acodec",
        "pcm_s16le",
        str(dest),
    ]
    try:
        proc = run_command(cmd, _NORMALIZE_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001
        logger.warning("loudness normalize failed: %s", redact.error(exc))
        return source
    if proc.returncode != 0 or not dest.is_file() or dest.stat().st_size == 0:
        logger.warning("loudness normalize produced nothing (rc=%s)", proc.returncode)
        dest.unlink(missing_ok=True)
        return source
    try:
        source.unlink()
    except OSError as exc:
        # The wav still wins the cache lookup; the leftover ages out via LRU.
        logger.debug("could not remove the pre-normalize original: %s", redact.error(exc))
    logger.info("music normalized: peak %.1f dBFS -> %.1f (%+.1f dB gain)", peak_dbfs, _NORMALIZE_TARGET_PEAK_DBFS, gain_db)
    return dest


def _cached_audio(video_id: str, dest_dir: Path) -> Path | None:
    """Return the cached audio file for *video_id* in any playable container."""
    for extension in _AUDIO_EXTENSIONS:
        candidate = dest_dir / f"{video_id}{extension}"
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    return None


def download_audio(video_id: str, dest_dir: Path, *, transcode_mp3: bool = True) -> Dict[str, Any]:
    """Download one video's audio into *dest_dir*. Never raises.

    With ``transcode_mp3`` (the default, kept for the gag clips whose cut
    pipeline expects mp3) the result is `<video_id>.mp3` via an ffmpeg
    re-encode. Without it the best native audio stream lands untouched as
    `<video_id>.<ext>` -- the daemon plays through GStreamer playbin, which
    decodes m4a/opus/mp3 alike, and skipping the re-encode was measured at
    15.9 s -> 4.1 s for one song on the robot (2026-08-22).
    """
    if not ytdlp_available():
        return {"ok": False, "path": None, "cached": False, "error": "yt-dlp is not installed on this robot"}
    ffmpeg = ffmpeg_exe()
    if not ffmpeg:
        # Both modes need it: the mp3 mode to encode, the native mode to pull
        # the audio track out of a muxed fallback download (a stream copy).
        return {"ok": False, "path": None, "cached": False, "error": "ffmpeg is unavailable; cannot produce audio"}

    cached = _cached_audio(video_id, dest_dir)
    if cached is not None:
        if not transcode_mp3:
            # A cache entry from before loudness normalization existed plays
            # quiet forever unless upgraded; a no-op for `.wav` entries. Gags
            # (the mp3 mode) keep their original level.
            cached = normalize_loudness(cached)
        # Task 4 review: the music cache is pruned by mtime, and a cache hit
        # rewrites nothing -- so a track played straight from the cache is the
        # OLDEST entry in the directory and the prune that runs right after this
        # play would delete the file currently on the speaker. Touching it makes
        # the LRU order reflect use rather than download date.
        try:
            os.utime(cached, None)
        except OSError as exc:
            # A read-only cache must still be playable; the mtime is an
            # optimisation, not a precondition.
            logger.debug("Could not refresh the cached track's mtime: %s", redact.error(exc))
        return {"ok": True, "path": str(cached), "cached": True, "error": None}

    out_file = dest_dir / f"{video_id}.mp3"
    if transcode_mp3:
        format_args = [
            "-x",
            "--audio-format",
            "mp3",
            "--audio-quality",
            "5",
            "--ffmpeg-location",
            str(ffmpeg),
        ]
    else:
        # `/best` matters: YouTube's SABR experiment strips the audio-only
        # formats from some sessions entirely (observed on-robot 2026-08-22),
        # and the muxed stream is then the only thing left. `-x` without a
        # target format extracts its audio track as a stream copy -- never a
        # re-encode -- so both branches land a native-container audio file.
        format_args = [
            "-f",
            "bestaudio[ext=m4a]/bestaudio/best",
            "-x",
            "--ffmpeg-location",
            str(ffmpeg),
        ]
    cmd = _ytdlp_argv() + [
        f"https://www.youtube.com/watch?v={video_id}",
        *format_args,
        "--no-playlist",
        "--force-overwrites",
        "--socket-timeout",
        "20",
        "--no-warnings",
        "--quiet",
        "-o",
        str(dest_dir / f"{video_id}.%(ext)s"),
    ]
    timeout_s = settings.ytdlp_download_timeout_s()
    try:
        proc = run_command(cmd, timeout_s)
    except subprocess.TimeoutExpired:
        return {"ok": False, "path": None, "cached": False, "error": f"download timed out after {timeout_s}s"}
    except Exception as exc:  # noqa: BLE001
        # Round 2, finding 6: the argv in the message carries the video URL and
        # the instance-directory output template.
        logger.warning("yt-dlp download failed: %s", redact.error(exc))
        return {"ok": False, "path": None, "cached": False, "error": "the download could not be run"}

    produced = out_file if transcode_mp3 else _cached_audio(video_id, dest_dir)
    if produced is None or not produced.is_file() or produced.stat().st_size == 0:
        # Finding 6: the tail of yt-dlp's output names the video and the path it
        # tried to write. Log the shape, return a fixed reason. Round 3,
        # finding 3: a length, never a token lifted out of the text.
        logger.warning(
            "yt-dlp produced no audio (rc=%d), output %s",
            proc.returncode,
            redact.text(proc.stderr or proc.stdout or ""),
        )
        return {"ok": False, "path": None, "cached": False, "error": "no audio could be produced for that track"}
    if not transcode_mp3:
        produced = normalize_loudness(produced)
    return {"ok": True, "path": str(produced), "cached": False, "error": None}


def cut_from(source: Path, offset_s: float, dest: Path) -> bool:
    """Stream-copy *source* from *offset_s* into *dest*. Returns success."""
    ffmpeg = ffmpeg_exe()
    if not ffmpeg or not Path(source).is_file():
        return False
    cmd = [
        ffmpeg,
        "-y",
        "-loglevel",
        "error",
        "-ss",
        f"{offset_s:.3f}",
        "-i",
        str(source),
        "-c",
        "copy",
        str(dest),
    ]
    try:
        proc = run_command(cmd, 30)
    except Exception as exc:  # noqa: BLE001
        # Round 2, finding 6: the argv names the cached track's path.
        logger.warning("ffmpeg seek failed: %s", redact.error(exc))
        return False
    if proc.returncode != 0 or not Path(dest).is_file() or Path(dest).stat().st_size == 0:
        logger.warning("ffmpeg seek produced nothing (rc=%s)", proc.returncode)
        return False
    return True
