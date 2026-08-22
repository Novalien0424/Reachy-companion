"""Contract tests for the two audio gags (D-018, R2/R3/R5/R7)."""

import types
import logging
import importlib

import pytest

from reachy_companion.hanova import sfx
from reachy_companion.hanova.confirm import GATE
from reachy_companion.tools.mad_laugh import MadLaugh
from reachy_companion.hanova.music_player import PLAYER
from reachy_companion.tools.self_destruct import SelfDestruct


# What actually reached the robot's speaker. The `MusicPlayer` calls the daemon's
# REST API itself rather than the SDK's `MediaManager` -- whose `play_sound`
# swallows a non-2xx (`music_player.py:10-13`) -- so the only honest record of a
# play is the daemon POST, exactly as `test_hanova_music._FakeDaemon` records it.
# It is module-level because the fixture that installs the transport and the
# helper that builds the deps have to share one list.
_PLAYED: list[str] = []


def _deps(tmp_path):
    robot = types.SimpleNamespace(_daemon_http_url="http://127.0.0.1:8000")
    return types.SimpleNamespace(reachy_mini=robot, instance_path=tmp_path), _PLAYED


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    """Both gag ids present, both wheels present, nothing playing."""
    monkeypatch.setenv("HANOVA_SELF_DESTRUCT_YT_ID", "sd-clip-id")
    monkeypatch.setenv("HANOVA_MAD_LAUGH_YT_ID", "ml-clip-id")
    monkeypatch.delenv("HANOVA_CONFIRM_TTL_S", raising=False)
    monkeypatch.setattr("reachy_companion.hanova.settings._music_wheels_ready", lambda: (True, ""))

    import httpx

    class _Ok:
        status_code = 200

    async def ok_post(self, url, json=None, **kwargs):
        if str(url).endswith("/api/media/play_sound"):
            _PLAYED.append(str((json or {}).get("file")))
        return _Ok()

    monkeypatch.setattr(httpx.AsyncClient, "post", ok_post)
    _PLAYED.clear()
    GATE.reset()
    GATE.begin_session()
    PLAYER.reset()
    yield
    _PLAYED.clear()
    GATE.reset()
    PLAYER.reset()


def test_tool_names_match_their_filenames():
    """The loader resolves tools by filename == Tool.name."""
    assert SelfDestruct.name == "self_destruct"
    assert MadLaugh.name == "mad_laugh"


def test_descriptions_carry_no_personal_identifier():
    """R10: descriptions stay short, generic and identifier-free."""
    for text in (SelfDestruct().description, MadLaugh().description):
        assert "@" not in text
        assert "media_player." not in text
        assert len(text) <= 120


@pytest.mark.asyncio
async def test_ensure_clip_caches_by_video_id(monkeypatch, tmp_path):
    """A gag downloads once; every later play is instant and offline."""
    calls = {"n": 0}

    def fake_download(video_id, dest_dir):
        calls["n"] += 1
        path = dest_dir / f"{video_id}.mp3"
        path.write_bytes(b"ID3")
        return {"ok": True, "path": str(path), "cached": False, "error": None}

    monkeypatch.setattr(sfx.ytdlp, "download_audio", fake_download)
    first = await sfx.ensure_clip("sd-clip-id", tmp_path)
    assert first["ok"] is True
    assert calls["n"] == 1


# Review finding 1: yt-dlp's failure text routinely quotes the video URL, the
# video id and the local output path. This sentinel stands in for all three.
_YTDLP_SENTINEL = "ERROR: [youtube] https://sentinel.example/v?id=SECRET: unable to write /home/pollen/hanova_media"


def _leaks(haystack: str) -> bool:
    """Return whether any part of the sentinel survived into *haystack*."""
    return any(token in haystack for token in ("SECRET", "sentinel.example", "/home/pollen"))


@pytest.mark.asyncio
async def test_ensure_clip_reports_a_download_failure(monkeypatch, tmp_path, caplog):
    """No network at gag time is a spoken answer, not a crash -- and never yt-dlp's own words.

    Finding 1: this error is spoken aloud by the robot and sent to OpenAI, so the
    caller gets a fixed, identifier-free reason and the raw text reaches the log
    only as a length. It is the convention `play_music.py:55-68` already follows
    for this very function, and a gag is not a reason to break it.
    """
    monkeypatch.setattr(
        sfx.ytdlp,
        "download_audio",
        lambda video_id, dest_dir: {"ok": False, "path": None, "cached": False, "error": _YTDLP_SENTINEL},
    )
    with caplog.at_level(logging.INFO, logger="reachy_companion.hanova.sfx"):
        out = await sfx.ensure_clip("sd-clip-id", tmp_path)

    assert out["ok"] is False
    assert out["error"] == "the clip could not be fetched right now"
    # The sentinel reaches neither the payload the model reads aloud...
    assert not _leaks(repr(out))
    # ...nor the log, which carries its length and nothing else.
    logged = " ".join(record.getMessage() for record in caplog.records)
    assert not _leaks(logged)
    assert f"<text:{len(_YTDLP_SENTINEL)} chars>" in logged


@pytest.mark.asyncio
async def test_a_failed_gag_never_speaks_ytdlp_back_at_the_user(monkeypatch, tmp_path):
    """Finding 1, end to end: the fix belongs to sfx, so both tools inherit it."""
    import reachy_companion.tools.mad_laugh as mad_laugh_module

    monkeypatch.setattr(
        mad_laugh_module.sfx.ytdlp,
        "download_audio",
        lambda video_id, dest_dir: {"ok": False, "path": None, "cached": False, "error": _YTDLP_SENTINEL},
    )
    deps, played = _deps(tmp_path)
    out = await MadLaugh()(deps=deps)
    assert out == {"ok": False, "error": "the clip could not be fetched right now"}
    assert not _leaks(repr(out))
    assert played == []


@pytest.mark.asyncio
async def test_mad_laugh_plays_on_the_robot_speaker(monkeypatch, tmp_path):
    """No Home Assistant, no LAN URL: it is the robot's own speaker."""
    import reachy_companion.tools.mad_laugh as mad_laugh_module

    def fake_download(video_id, dest_dir):
        path = dest_dir / f"{video_id}.mp3"
        path.write_bytes(b"ID3")
        return {"ok": True, "path": str(path), "cached": True, "error": None}

    monkeypatch.setattr(mad_laugh_module.sfx.ytdlp, "download_audio", fake_download)
    deps, played = _deps(tmp_path)
    out = await MadLaugh()(deps=deps)
    assert out["ok"] is True and out["status"] == "playing"
    assert played and played[0].endswith("ml-clip-id.mp3")


@pytest.mark.asyncio
async def test_mad_laugh_is_unavailable_without_a_clip_id(monkeypatch, tmp_path):
    """Finding 10: the clip id is this tool's own prerequisite, and it is named."""
    monkeypatch.delenv("HANOVA_MAD_LAUGH_YT_ID")
    deps, _ = _deps(tmp_path)
    out = await MadLaugh()(deps=deps)
    assert out == {"status": "unavailable", "reason": "HANOVA_MAD_LAUGH_YT_ID"}


@pytest.mark.asyncio
async def test_mad_laugh_is_unavailable_without_the_wheels(monkeypatch, tmp_path):
    """R5: no yt-dlp / ffmpeg means the tool is off, not broken."""
    monkeypatch.setattr(
        "reachy_companion.hanova.settings._music_wheels_ready", lambda: (False, "yt-dlp not installed")
    )
    deps, _ = _deps(tmp_path)
    out = await MadLaugh()(deps=deps)
    assert out == {"status": "unavailable", "reason": "MUSIC_WHEELS"}


@pytest.mark.asyncio
async def test_self_destruct_arms_before_it_plays(monkeypatch, tmp_path):
    """R3: the first call plays nothing and reads the ritual back."""
    import reachy_companion.tools.self_destruct as self_destruct_module

    def fail_download(video_id, dest_dir):
        raise AssertionError("self_destruct must not fetch or play before confirmation")

    monkeypatch.setattr(self_destruct_module.sfx.ytdlp, "download_audio", fail_download)
    deps, played = _deps(tmp_path)
    out = await SelfDestruct()(deps=deps)
    assert out["status"] == "needs_confirmation" and out["summary"]
    assert played == []


def test_the_arm_summary_stays_in_character(monkeypatch):
    """Finding 17: the confirmation must not spoil the gag it is confirming."""
    import reachy_companion.tools.self_destruct as self_destruct_module

    summary = self_destruct_module._ARM_SUMMARY.lower()
    for spoiler in ("joke", "gag", "prank", "nothing else", "just a sound", "pretend"):
        assert spoiler not in summary, f"the arm summary gives away the gag: {spoiler!r}"
    # ...but it must still be a real two-step with a real way out.
    assert "abort" in summary
    assert "authorise" in summary or "authorize" in summary


@pytest.mark.asyncio
async def test_self_destruct_can_be_aborted(monkeypatch, tmp_path):
    """Finding 17: an explicit abort word, enforced in code."""
    import reachy_companion.tools.self_destruct as self_destruct_module

    def fail_download(video_id, dest_dir):
        raise AssertionError("an aborted sequence must never play")

    monkeypatch.setattr(self_destruct_module.sfx.ytdlp, "download_audio", fail_download)
    deps, played = _deps(tmp_path)
    await SelfDestruct()(deps=deps)
    out = await SelfDestruct()(deps=deps, abort=True)
    assert out == {"status": "aborted"}
    assert played == []
    assert (await SelfDestruct()(deps=deps, confirm=True))["status"] == "confirmation_expired"


@pytest.mark.asyncio
async def test_aborting_when_nothing_is_armed_is_harmless(tmp_path):
    """Standing down a sequence that was never armed is still in character."""
    deps, _ = _deps(tmp_path)
    assert (await SelfDestruct()(deps=deps, abort=True)) == {"status": "aborted"}


@pytest.mark.asyncio
async def test_the_armed_sequence_expires(monkeypatch, tmp_path):
    """Finding 17: the TTL is real, and it is the shared gate's TTL."""
    import reachy_companion.tools.self_destruct as self_destruct_module

    def fail_download(video_id, dest_dir):
        raise AssertionError("an expired sequence must never play")

    monkeypatch.setattr(self_destruct_module.sfx.ytdlp, "download_audio", fail_download)
    deps, _ = _deps(tmp_path)
    await SelfDestruct()(deps=deps)
    GATE.expire_now_for_tests("self_destruct")
    out = await SelfDestruct()(deps=deps, confirm=True)
    assert out["status"] == "confirmation_expired"


@pytest.mark.asyncio
async def test_self_destruct_plays_once_confirmed(monkeypatch, tmp_path):
    """The confirmed call is the only one that makes noise."""
    import reachy_companion.tools.self_destruct as self_destruct_module

    def fake_download(video_id, dest_dir):
        path = dest_dir / f"{video_id}.mp3"
        path.write_bytes(b"ID3")
        return {"ok": True, "path": str(path), "cached": True, "error": None}

    monkeypatch.setattr(self_destruct_module.sfx.ytdlp, "download_audio", fake_download)
    deps, played = _deps(tmp_path)
    await SelfDestruct()(deps=deps)
    out = await SelfDestruct()(deps=deps, confirm=True)
    assert out["ok"] is True and out["status"] == "playing"
    assert played and played[0].endswith("sd-clip-id.mp3")


@pytest.mark.asyncio
async def test_self_destruct_confirm_without_arm_is_refused(monkeypatch, tmp_path):
    """A confirm:true first call must play nothing."""
    import reachy_companion.tools.self_destruct as self_destruct_module

    def fail_download(video_id, dest_dir):
        raise AssertionError("self_destruct must not play without a pending action")

    monkeypatch.setattr(self_destruct_module.sfx.ytdlp, "download_audio", fail_download)
    deps, _ = _deps(tmp_path)
    out = await SelfDestruct()(deps=deps, confirm=True)
    assert out["status"] == "confirmation_expired"


@pytest.mark.asyncio
async def test_a_playing_gag_is_stoppable_by_voice(monkeypatch, tmp_path):
    """R7: gags route through the music player, so stop_music stops them."""
    import reachy_companion.tools.mad_laugh as mad_laugh_module
    from reachy_companion.tools.stop_music import StopMusic

    def fake_download(video_id, dest_dir):
        path = dest_dir / f"{video_id}.mp3"
        path.write_bytes(b"ID3")
        return {"ok": True, "path": str(path), "cached": True, "error": None}

    monkeypatch.setattr(mad_laugh_module.sfx.ytdlp, "download_audio", fake_download)
    deps, _ = _deps(tmp_path)
    await MadLaugh()(deps=deps)
    assert PLAYER.current() is not None
    out = await StopMusic()(deps=deps)
    assert out["status"] == "stopped"
    assert PLAYER.current() is None


def test_both_gags_reach_the_model_session():
    """The locked profile must list them, or the model never sees them."""
    core_tools = importlib.import_module("reachy_companion.tools.core_tools")
    core_tools.initialize_tools(force=True)
    try:
        names = {spec["name"] for spec in core_tools.get_tool_specs()}
        assert {"self_destruct", "mad_laugh"} <= names
    finally:
        core_tools._TOOLS_SIGNATURE = None
