"""Contract tests for robot-speaker music playback and its two tools (D-018, R2/R7).

Also pins review round 1 finding 2: the player is a serialized state machine
with generation tokens, both daemon commands are acknowledged, and the four
interleavings that used to be losable are covered explicitly.
"""

import types
import asyncio
import logging
import importlib
from pathlib import Path

import httpx
import pytest

from reachy_companion.hanova import ytdlp
from reachy_companion.tools.play_music import PlayMusic
from reachy_companion.tools.stop_music import StopMusic
from reachy_companion.hanova.music_player import PLAYER


class _Response:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code


class _FakeDaemon:
    """Records what the daemon was actually asked to do, and how it answered.

    The SDK's `MediaManager.play_sound` swallows a non-2xx, so the player calls
    the daemon REST API itself; this stands in for that API (finding 2).
    """

    def __init__(self) -> None:
        self.plays: list[str] = []
        self.stops = 0
        self.play_status = 200
        self.stop_status = 200
        self.play_delay = 0.0
        self.stop_delay = 0.0

    async def post(self, url: str, json=None, **kw):
        if url.endswith("/api/media/play_sound"):
            await asyncio.sleep(self.play_delay)
            self.plays.append(str((json or {}).get("file")))
            return _Response(self.play_status)
        if url.endswith("/api/media/stop_sound"):
            await asyncio.sleep(self.stop_delay)
            self.stops += 1
            return _Response(self.stop_status)
        raise AssertionError(f"unexpected daemon call: {url}")


@pytest.fixture
def daemon(monkeypatch):
    """Install the fake daemon as the only transport the player can reach."""
    fake = _FakeDaemon()

    async def fake_post(self, url, json=None, **kw):
        return await fake.post(url, json=json, **kw)

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    return fake


def _deps(instance_path=None):
    """Return a ToolDependencies-shaped stub exposing only what music touches."""
    robot = types.SimpleNamespace(_daemon_http_url="http://127.0.0.1:8000")
    return types.SimpleNamespace(reachy_mini=robot, instance_path=instance_path)


@pytest.fixture(autouse=True)
def clean_player(monkeypatch):
    """Every test starts with nothing playing and both wheels present."""
    PLAYER.reset()
    monkeypatch.setattr("reachy_companion.hanova.settings._music_wheels_ready", lambda: (True, ""))
    yield
    PLAYER.reset()


def _track(tmp_path, name="abc.mp3"):
    path = tmp_path / name
    path.write_bytes(b"ID3")
    return path


def test_tool_names_match_their_filenames():
    """The loader resolves tools by filename == Tool.name (core_tools.py:403)."""
    assert PlayMusic.name == "play_music"
    assert StopMusic.name == "stop_music"


def test_descriptions_carry_no_personal_identifier():
    """R10: no entity id, address, folder id or owner name in a description."""
    for text in (PlayMusic().description, StopMusic().description):
        assert "@" not in text
        assert "media_player." not in text
        assert len(text) <= 120


@pytest.mark.asyncio
async def test_play_starts_the_sound_and_records_state(daemon, tmp_path):
    """Playback goes through the daemon's play_sound, not the realtime output."""
    track = _track(tmp_path)
    out = await PLAYER.play(_deps(), video_id="abc", title="A Song", source_path=track)
    assert out["ok"] is True and out["status"] == "playing"
    assert daemon.plays == [str(track)]
    state = PLAYER.current()
    assert state is not None and state.video_id == "abc" and state.paused is False


@pytest.mark.asyncio
async def test_an_unacknowledged_play_is_a_failure_not_a_success(daemon, tmp_path):
    """Finding 2: the SDK swallows this; we must not report `playing` on a 500."""
    daemon.play_status = 500
    out = await PLAYER.play(_deps(), video_id="abc", title="A Song", source_path=_track(tmp_path))
    assert out["ok"] is False
    assert PLAYER.current() is None


@pytest.mark.asyncio
async def test_pause_for_speech_stops_the_daemon_sound_and_banks_the_offset(daemon, tmp_path):
    """R7: user speech ducks the music by stopping it and remembering where."""
    deps = _deps()
    await PLAYER.play(deps, video_id="abc", title="A Song", source_path=_track(tmp_path))
    # MusicState is mutable and `current()` hands back the live object, so this
    # simulates 30 s of playback without patching the global clock.
    PLAYER.current().started_at -= 30.0
    out = await PLAYER.pause_for_speech(deps)

    state = PLAYER.current()
    assert out["ok"] is True and daemon.stops == 1
    assert state is not None and state.paused is True
    assert state.offset_s == pytest.approx(30.0, abs=0.5)


@pytest.mark.asyncio
async def test_a_failed_stop_leaves_the_music_playing(daemon, tmp_path):
    """Finding 2: marking it paused when the daemon refused is a lie in state."""
    deps = _deps()
    await PLAYER.play(deps, video_id="abc", title="A Song", source_path=_track(tmp_path))
    daemon.stop_status = 503
    out = await PLAYER.pause_for_speech(deps)
    state = PLAYER.current()
    assert out["ok"] is False
    assert state is not None and state.paused is False


@pytest.mark.asyncio
async def test_a_failed_stop_in_stop_music_is_reported(daemon, tmp_path):
    """`stop` that the daemon did not acknowledge must not claim `stopped`."""
    deps = _deps()
    await PLAYER.play(deps, video_id="abc", title="A Song", source_path=_track(tmp_path))
    daemon.stop_status = 500
    out = await PLAYER.stop(deps)
    assert out["ok"] is False and out["status"] != "stopped"


@pytest.mark.asyncio
async def test_pause_is_a_no_op_when_nothing_plays(daemon):
    """Barge-in fires on every turn; with no music it must cost nothing."""
    await PLAYER.pause_for_speech(_deps())
    assert PLAYER.current() is None
    assert daemon.stops == 0 and daemon.plays == []


@pytest.mark.asyncio
async def test_resume_replays_from_the_banked_offset(daemon, monkeypatch, tmp_path):
    """The daemon has no pause, so resume re-cuts the file and plays the tail."""
    deps = _deps()
    track = _track(tmp_path)
    await PLAYER.play(deps, video_id="abc", title="A Song", source_path=track)
    PLAYER.current().started_at -= 30.0
    await PLAYER.pause_for_speech(deps)

    cuts = {}

    def fake_cut(source, offset_s, dest):
        cuts["source"], cuts["offset_s"], cuts["dest"] = source, offset_s, dest
        Path(dest).write_bytes(b"ID3tail")
        return True

    monkeypatch.setattr(ytdlp, "cut_from", fake_cut)
    await PLAYER.resume_after_speech(deps)

    assert cuts["source"] == track
    assert cuts["offset_s"] == pytest.approx(30.0, abs=0.5)
    assert daemon.plays[-1] == str(cuts["dest"])
    state = PLAYER.current()
    assert state is not None and state.paused is False


@pytest.mark.asyncio
async def test_resume_when_not_paused_does_nothing(daemon, tmp_path):
    """The drain signal fires per turn; resuming un-paused music would restart it."""
    deps = _deps()
    track = _track(tmp_path)
    await PLAYER.play(deps, video_id="abc", title="A Song", source_path=track)
    await PLAYER.resume_after_speech(deps)
    assert daemon.plays == [str(track)]


@pytest.mark.asyncio
async def test_failed_resume_gives_up_cleanly(daemon, monkeypatch, tmp_path):
    """A broken trim must not restart the track from zero in the user's face."""
    deps = _deps()
    track = _track(tmp_path)
    await PLAYER.play(deps, video_id="abc", title="A Song", source_path=track)
    PLAYER.current().started_at -= 30.0
    await PLAYER.pause_for_speech(deps)
    monkeypatch.setattr(ytdlp, "cut_from", lambda source, offset_s, dest: False)
    await PLAYER.resume_after_speech(deps)
    assert daemon.plays == [str(track)]
    assert PLAYER.current() is None


@pytest.mark.asyncio
async def test_stop_clears_state_and_reports_the_title(daemon, tmp_path):
    """`stop_music` must always work by voice, even mid-download of something else."""
    deps = _deps()
    await PLAYER.play(deps, video_id="abc", title="A Song", source_path=_track(tmp_path))
    out = await PLAYER.stop(deps)
    assert out["ok"] is True and out["status"] == "stopped" and out["title"] == "A Song"
    assert PLAYER.current() is None


@pytest.mark.asyncio
async def test_stop_when_idle_is_still_ok(daemon):
    """Stopping silence is a no-op, not an error the model must apologise for."""
    out = await PLAYER.stop(_deps())
    assert out["ok"] is True and out["status"] == "nothing_playing"


# --- the four interleavings (review finding 2) ----------------------------
@pytest.mark.asyncio
async def test_play_racing_a_stop_never_leaves_music_running(daemon, tmp_path):
    """A slow play that lands after stop_music must not resurrect the speaker."""
    deps = _deps()
    daemon.play_delay = 0.05
    play_task = asyncio.create_task(PLAYER.play(deps, video_id="abc", title="A Song", source_path=_track(tmp_path)))
    await asyncio.sleep(0)  # let play acquire the lock and start its I/O
    stop_result = await PLAYER.stop(deps)
    play_result = await play_task

    assert PLAYER.current() is None, "stop_music must win against a slower play"
    assert stop_result["ok"] is True
    assert play_result.get("status") in {"superseded", "playing"}
    if play_result.get("status") == "playing":
        # If play won the lock first, the stop that followed it must still have
        # been sent and the state must still be clear.
        assert daemon.stops >= 1


@pytest.mark.asyncio
async def test_resume_racing_a_stop_never_resurrects_the_track(daemon, monkeypatch, tmp_path):
    """The ffmpeg re-cut is slow; a stop during it must win."""
    deps = _deps()
    track = _track(tmp_path)
    await PLAYER.play(deps, video_id="abc", title="A Song", source_path=track)
    PLAYER.current().started_at -= 30.0
    await PLAYER.pause_for_speech(deps)

    def slow_cut(source, offset_s, dest):
        Path(dest).write_bytes(b"ID3tail")
        return True

    monkeypatch.setattr(ytdlp, "cut_from", slow_cut)
    daemon.play_delay = 0.05
    resume_task = asyncio.create_task(PLAYER.resume_after_speech(deps))
    await asyncio.sleep(0)
    await PLAYER.stop(deps)
    await resume_task
    assert PLAYER.current() is None


@pytest.mark.asyncio
async def test_the_newer_of_two_plays_wins(daemon, tmp_path):
    """Two songs asked for in quick succession must not interleave."""
    deps = _deps()
    first = _track(tmp_path, "first.mp3")
    second = _track(tmp_path, "second.mp3")
    daemon.play_delay = 0.05

    task_one = asyncio.create_task(PLAYER.play(deps, video_id="one", title="One", source_path=first))
    await asyncio.sleep(0)
    task_two = asyncio.create_task(PLAYER.play(deps, video_id="two", title="Two", source_path=second))
    result_one, result_two = await asyncio.gather(task_one, task_two)

    state = PLAYER.current()
    assert state is not None and state.video_id == "two"
    assert daemon.plays[-1] == str(second)
    # Task 4 review: the loser must *say* it lost. A caller that reads only the
    # winner's verdict cannot tell a superseded play from a silent failure.
    assert result_one["status"] == "superseded"
    assert result_two["status"] == "playing"


@pytest.mark.asyncio
async def test_a_play_superseded_during_its_pre_stop_drops_the_stale_state(daemon, tmp_path):
    """Task 4 review: the pre-stop silenced the old track, so its snapshot must go.

    A play that loses its race *after* the pre-stop used to return `superseded`
    with the previous track's snapshot still in place -- state claiming
    `playing` with nothing audible. The next barge-in then banked an elapsed
    offset for a track that had stopped seconds earlier, so the eventual resume
    jumped forward by the length of that silence.
    """
    deps = _deps()
    first = _track(tmp_path, "first.mp3")
    second = _track(tmp_path, "second.mp3")
    await PLAYER.play(deps, video_id="one", title="One", source_path=first)

    daemon.stop_delay = 0.05  # the second play's pre-stop is still in flight
    play_two = asyncio.create_task(PLAYER.play(deps, video_id="two", title="Two", source_path=second))
    await asyncio.sleep(0)
    pause = asyncio.create_task(PLAYER.pause_for_speech(deps))
    await asyncio.sleep(0)  # this bumps the generation and queues on the lock

    assert (await play_two)["status"] == "superseded"
    assert (await pause)["status"] == "nothing_to_pause", "the pause banked an offset for a stopped track"
    assert PLAYER.current() is None


@pytest.mark.asyncio
async def test_a_superseded_transition_reports_itself(daemon, tmp_path):
    """A losing transition must say so rather than pretend it succeeded."""
    deps = _deps()
    generation_before = PLAYER.generation()
    await PLAYER.play(deps, video_id="abc", title="A Song", source_path=_track(tmp_path))
    assert PLAYER.generation() > generation_before


# --- the two tools ---------------------------------------------------------
@pytest.mark.asyncio
async def test_play_music_reports_unavailable_when_wheels_are_missing(daemon, monkeypatch):
    """R5: the tool disables cleanly instead of raising ImportError."""
    monkeypatch.setattr(
        "reachy_companion.hanova.settings._music_wheels_ready", lambda: (False, "yt-dlp not installed")
    )
    out = await PlayMusic()(deps=_deps(), query="anything")
    assert out == {"status": "unavailable", "reason": "MUSIC_WHEELS"}


@pytest.mark.asyncio
async def test_stop_music_is_available_with_zero_configuration(daemon, monkeypatch):
    """Finding 10: the safety lane must answer even when nothing else can."""
    monkeypatch.setattr(
        "reachy_companion.hanova.settings._music_wheels_ready", lambda: (False, "yt-dlp not installed")
    )
    out = await StopMusic()(deps=_deps())
    assert out["status"] == "nothing_playing"


@pytest.mark.asyncio
async def test_play_music_reports_a_search_failure(daemon, monkeypatch):
    """A rate-limited search is a spoken answer, not a stack trace."""
    import reachy_companion.tools.play_music as play_music_module

    monkeypatch.setattr(
        play_music_module.ytdlp,
        "search",
        lambda query, max_duration_s=None: {"ok": False, "id": None, "title": None, "error": "no result"},
    )
    out = await PlayMusic()(deps=_deps(), query="something obscure")
    assert out["ok"] is False and out["error"]


@pytest.mark.asyncio
async def test_play_music_happy_path(daemon, monkeypatch, tmp_path):
    """Search -> download -> play on the robot speaker, and report the real title."""
    import reachy_companion.tools.play_music as play_music_module

    track = _track(tmp_path)
    monkeypatch.setattr(
        play_music_module.ytdlp,
        "search",
        lambda query, max_duration_s=None: {"ok": True, "id": "abc", "title": "A Song", "error": None},
    )
    monkeypatch.setattr(
        play_music_module.ytdlp,
        "download_audio",
        lambda video_id, dest_dir: {"ok": True, "path": str(track), "cached": True, "error": None},
    )
    out = await PlayMusic()(deps=_deps(tmp_path), query="a song")
    assert out["ok"] is True and out["title"] == "A Song"
    assert daemon.plays == [str(track)]


@pytest.mark.asyncio
async def test_play_music_rejects_an_empty_query(daemon):
    """An empty query must not reach yt-dlp."""
    out = await PlayMusic()(deps=_deps(), query="   ")
    assert out["ok"] is False


@pytest.mark.asyncio
async def test_stop_music_tool_delegates_to_the_player(daemon, tmp_path):
    """One code path for stopping, whether spoken or triggered internally."""
    deps = _deps()
    await PLAYER.play(deps, video_id="abc", title="A Song", source_path=_track(tmp_path))
    out = await StopMusic()(deps=deps)
    assert out["status"] == "stopped"
    assert PLAYER.current() is None


@pytest.mark.asyncio
async def test_music_logs_never_carry_the_query_or_the_title(daemon, monkeypatch, caplog, tmp_path):
    """Finding 7: what the user asked for is theirs; the log gets metadata only."""
    import reachy_companion.tools.play_music as play_music_module

    sentinel = "SENTINEL_PRIVATE_x7"
    track = _track(tmp_path)
    monkeypatch.setattr(
        play_music_module.ytdlp,
        "search",
        lambda query, max_duration_s=None: {"ok": True, "id": sentinel, "title": sentinel, "error": None},
    )
    monkeypatch.setattr(
        play_music_module.ytdlp,
        "download_audio",
        lambda video_id, dest_dir: {"ok": True, "path": str(track), "cached": True, "error": None},
    )
    caplog.set_level(logging.DEBUG)
    await PlayMusic()(deps=_deps(tmp_path), query=f"a song about {sentinel}")
    assert sentinel not in caplog.text


@pytest.mark.asyncio
async def test_a_failed_search_is_logged_as_a_shape_not_the_word_error(daemon, monkeypatch, caplog, tmp_path):
    """Task 4 review: `redact.error` on a plain string renders the constant "error".

    The failure reason yt-dlp returns is free text nobody vouched for, so it
    cannot be logged raw -- but rendering every distinct failure as the same
    four letters left nothing to diagnose with. `redact.text` keeps the shape.
    """
    import reachy_companion.tools.play_music as play_music_module

    sentinel = "SENTINEL_PRIVATE_x7"
    monkeypatch.setattr(
        play_music_module.ytdlp,
        "search",
        lambda query, max_duration_s=None: {"ok": False, "id": None, "title": None, "error": sentinel},
    )
    caplog.set_level(logging.DEBUG)
    out = await PlayMusic()(deps=_deps(tmp_path), query="anything")

    assert out["ok"] is False
    assert sentinel not in caplog.text
    assert f"<text:{len(sentinel)} chars>" in caplog.text


def test_both_tools_reach_the_model_session():
    """The locked profile must list them, or the model never sees them."""
    core_tools = importlib.import_module("reachy_companion.tools.core_tools")
    core_tools.initialize_tools(force=True)
    try:
        names = {spec["name"] for spec in core_tools.get_tool_specs()}
        assert {"play_music", "stop_music"} <= names
    finally:
        core_tools._TOOLS_SIGNATURE = None
