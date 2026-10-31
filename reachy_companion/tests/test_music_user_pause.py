"""A pause the person asked for holds; the speech duck never undoes it (2026-10-31 plan, W2).

`music pause` reuses the duck path (`pause_for_speech`): stop the daemon sound,
bank the offset. What it adds is the hold, so the drain-then-resume at the end
of the turn (`resume_after_speech`) leaves the track silent, and only
`resume_by_user` plays it again. Music the person did not pause keeps the old
duck behaviour, which `test_hanova_music.py` pins.
"""

from __future__ import annotations
import types
import asyncio
from pathlib import Path

import httpx
import pytest

from reachy_companion.hanova import ytdlp, audio_drain, music_hooks
from reachy_companion.hanova.music_player import PLAYER


class _Response:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code


class _FakeDaemon:
    """The daemon's media API, recording plays and stops."""

    def __init__(self) -> None:
        self.plays: list[str] = []
        self.stops = 0
        self.stop_status = 200
        self.stop_delay = 0.0

    async def post(self, url: str, json=None, **kw):
        if url.endswith("/api/media/play_sound"):
            self.plays.append(str((json or {}).get("file")))
            return _Response(200)
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


@pytest.fixture(autouse=True)
def clean_player():
    """Every test starts with nothing playing and no hooks pending."""
    PLAYER.reset()
    audio_drain.reset()
    music_hooks.reset_for_tests()
    yield
    PLAYER.reset()
    audio_drain.reset()
    music_hooks.reset_for_tests()


@pytest.fixture
def cuts(monkeypatch):
    """Make the ffmpeg re-cut write a tail file and record its offset."""
    seen: list[float] = []

    def fake_cut(source, offset_s, dest):
        seen.append(offset_s)
        Path(dest).write_bytes(b"ID3tail")
        return True

    monkeypatch.setattr(ytdlp, "cut_from", fake_cut)
    return seen


def _deps():
    robot = types.SimpleNamespace(_daemon_http_url="http://127.0.0.1:8000")
    return types.SimpleNamespace(reachy_mini=robot, instance_path=None)


async def _playing(tmp_path, seconds: float = 30.0):
    track = tmp_path / "abc.mp3"
    track.write_bytes(b"ID3")
    deps = _deps()
    await PLAYER.play(deps, video_id="abc", title="A Song", source_path=track)
    PLAYER.current().started_at -= seconds
    return deps, track


@pytest.mark.asyncio
async def test_a_user_pause_silences_the_speaker_and_banks_the_offset(daemon, tmp_path):
    """The duck's own silencing, plus the hold."""
    deps, _ = await _playing(tmp_path)
    out = await PLAYER.pause_by_user(deps)
    state = PLAYER.current()
    assert out == {"ok": True, "status": "paused", "title": "A Song"}
    assert daemon.stops == 1
    assert state is not None and state.paused is True and state.offset_s == pytest.approx(30.0, abs=0.5)
    assert PLAYER.user_paused() is True


@pytest.mark.asyncio
async def test_a_voiced_pause_finds_the_track_ducked_and_only_adds_the_hold(daemon, tmp_path):
    """「暫停」 ducked the music when the person started talking; the tool only holds it."""
    deps, _ = await _playing(tmp_path)
    await PLAYER.pause_for_speech(deps)
    out = await PLAYER.pause_by_user(deps)
    assert out["status"] == "paused" and daemon.stops == 1
    assert PLAYER.user_paused() is True


@pytest.mark.asyncio
async def test_the_drain_resume_never_resumes_music_the_person_paused(daemon, tmp_path):
    """The end of the turn's reply must not put the paused track back on."""
    deps, track = await _playing(tmp_path)
    await PLAYER.pause_by_user(deps)
    out = await PLAYER.resume_after_speech(deps)
    assert out == {"ok": True, "status": "user_paused"}
    assert daemon.plays == [str(track)], "nothing played after the pause"
    assert PLAYER.current().paused is True


@pytest.mark.asyncio
async def test_the_whole_turn_leaves_a_paused_track_paused(daemon, tmp_path, monkeypatch):
    """Speech ducks, the tool holds, the turn ends and drains: still silent."""
    deps, track = await _playing(tmp_path)
    verdicts: list[str] = []
    real_resume = PLAYER.resume_after_speech

    async def spy_resume(deps_):
        verdict = await real_resume(deps_)
        verdicts.append(verdict["status"])
        return verdict

    monkeypatch.setattr(PLAYER, "resume_after_speech", spy_resume)
    music_hooks.on_user_speech_started(deps)
    await music_hooks.drain_pending_for_tests()
    music_hooks.on_tool_call_started("call-1")
    assert (await PLAYER.pause_by_user(deps))["status"] == "paused"
    music_hooks.on_tool_call_finished("call-1", needs_response=True)
    music_hooks.on_response_created()
    music_hooks.on_assistant_turn_ended(deps, live_tool_calls=set())
    await asyncio.wait_for(music_hooks.drain_pending_for_tests(), timeout=1.0)
    assert verdicts == ["user_paused"], "the drain-then-resume ran and left the track alone"
    assert daemon.plays == [str(track)]
    assert PLAYER.user_paused() is True


@pytest.mark.asyncio
async def test_resume_plays_the_paused_track_from_where_it_stopped(daemon, tmp_path, cuts):
    """`music resume` re-cuts at the banked offset, plays the tail and drops the hold."""
    deps, _ = await _playing(tmp_path)
    await PLAYER.pause_by_user(deps)
    out = await PLAYER.resume_by_user(deps)
    assert out == {"ok": True, "status": "resumed", "title": "A Song"}
    assert cuts == [pytest.approx(30.0, abs=0.5)]
    assert daemon.plays[-1].endswith(".resume.mp3")
    assert PLAYER.current().paused is False and PLAYER.user_paused() is False


@pytest.mark.asyncio
async def test_resume_does_not_restart_music_the_person_did_not_pause(daemon, tmp_path):
    """Ducked by speech only: it comes back by itself after the reply, so resume leaves it."""
    deps, track = await _playing(tmp_path)
    await PLAYER.pause_for_speech(deps)
    out = await PLAYER.resume_by_user(deps)
    assert out == {"ok": True, "status": "already_playing", "title": "A Song"}
    assert daemon.plays == [str(track)]


@pytest.mark.asyncio
async def test_pause_and_resume_with_nothing_on_the_speaker(daemon):
    """Nothing to pause or resume, and no hold left behind."""
    assert (await PLAYER.pause_by_user(_deps())) == {"ok": True, "status": "nothing_playing"}
    assert PLAYER.user_paused() is False
    assert (await PLAYER.resume_by_user(_deps())) == {"ok": True, "status": "nothing_to_resume"}
    assert daemon.stops == 0 and daemon.plays == []


@pytest.mark.asyncio
async def test_a_refused_pause_leaves_the_music_playing_and_unheld(daemon, tmp_path, cuts):
    """The daemon did not stop it: no paused state, no hold, and the duck behaves as before."""
    deps, _ = await _playing(tmp_path)
    daemon.stop_status = 503
    out = await PLAYER.pause_by_user(deps)
    assert out["ok"] is False and out["status"] == "pause_failed"
    assert PLAYER.current().paused is False and PLAYER.user_paused() is False


@pytest.mark.asyncio
async def test_stop_and_play_drop_the_hold(daemon, tmp_path):
    """A new song or a stop is a new state; nothing stays held from before."""
    deps, track = await _playing(tmp_path)
    await PLAYER.pause_by_user(deps)
    assert (await PLAYER.stop(deps))["status"] == "stopped"
    assert PLAYER.user_paused() is False and PLAYER.current() is None

    await PLAYER.play(deps, video_id="abc", title="A Song", source_path=track)
    await PLAYER.pause_by_user(deps)
    await PLAYER.play(deps, video_id="abc", title="A Song", source_path=track)
    assert PLAYER.user_paused() is False and PLAYER.current().paused is False


@pytest.mark.asyncio
async def test_a_speech_duck_that_overtakes_the_pause_still_ends_paused_and_held(daemon, tmp_path):
    """The person talks on while `music pause` is stopping the daemon: the pause wins anyway."""
    deps, track = await _playing(tmp_path)
    daemon.stop_delay = 0.05
    pause = asyncio.create_task(PLAYER.pause_by_user(deps))
    await asyncio.sleep(0)  # the pause holds the lock and is inside daemon_stop_sound
    duck = asyncio.create_task(PLAYER.pause_for_speech(deps))
    await asyncio.sleep(0)
    assert (await pause)["status"] == "paused"
    assert (await duck)["status"] == "nothing_to_pause"
    assert PLAYER.user_paused() is True
    assert (await PLAYER.resume_after_speech(deps))["status"] == "user_paused"
    assert daemon.plays == [str(track)]


@pytest.mark.asyncio
async def test_a_pause_that_overtakes_a_duck_keeps_the_track_resumable(daemon, tmp_path, cuts):
    """The duck was mid-stop when `music pause` arrived: the silenced track is banked, not dropped."""
    deps, _ = await _playing(tmp_path)
    daemon.stop_delay = 0.05
    duck = asyncio.create_task(PLAYER.pause_for_speech(deps))
    await asyncio.sleep(0)  # the duck holds the lock and is inside daemon_stop_sound
    pause = asyncio.create_task(PLAYER.pause_by_user(deps))
    await asyncio.sleep(0)
    assert (await duck)["status"] == "superseded"
    assert (await pause) == {"ok": True, "status": "paused", "title": "A Song"}
    assert PLAYER.user_paused() is True
    assert (await PLAYER.resume_by_user(deps))["status"] == "resumed"


@pytest.mark.asyncio
async def test_a_stop_racing_a_pause_wins(daemon, tmp_path):
    """Stop is the safety lane: a pause it overtakes reports superseded and nothing is held."""
    deps, _ = await _playing(tmp_path)
    daemon.stop_delay = 0.05
    pause = asyncio.create_task(PLAYER.pause_by_user(deps))
    await asyncio.sleep(0)
    stop = await PLAYER.stop(deps)
    assert (await pause)["status"] == "superseded"
    assert stop["ok"] is True
    assert PLAYER.current() is None and PLAYER.user_paused() is False


@pytest.mark.asyncio
async def test_a_refused_pause_overtaken_by_a_duck_leaves_no_hold(daemon, tmp_path, cuts):
    """The pause failed, so the duck that follows is an ordinary duck and resumes after the reply."""
    deps, _ = await _playing(tmp_path)
    daemon.stop_status = 503
    daemon.stop_delay = 0.05
    pause = asyncio.create_task(PLAYER.pause_by_user(deps))
    await asyncio.sleep(0)
    duck = asyncio.create_task(PLAYER.pause_for_speech(deps))
    await asyncio.sleep(0)
    assert (await pause)["status"] == "pause_failed"
    daemon.stop_status = 200
    assert (await duck)["status"] == "paused"
    assert PLAYER.user_paused() is False
    assert (await PLAYER.resume_after_speech(deps))["status"] == "resumed"
