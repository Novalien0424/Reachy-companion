"""`music stop`, `pause` and `resume` act on the speaker and the TV together (2026-10-31).

Live failure it answers: 「停下你的播放」 while a NAS clip played reached
`music stop`, which only knew the speaker, returned `nothing_playing`, and Reachy
said it had stopped while the video went on. Then the operator's rule, "stop
stops everything" (plan W1), and real pause and resume (plan W2). Every TV half
is read back, so a receiver that ignored the command is reported.
"""

from __future__ import annotations
from typing import Any, Dict, List
from datetime import datetime, timezone, timedelta

import pytest

from reachy_companion.tools import stop_music
from reachy_companion.hanova import nas, ha_client, tv_session
from reachy_companion.tools.music import Music
from reachy_companion.tools.stop_music import StopMusic, PausePlayback, ResumePlayback


def _state(state: str, title: str | None = None) -> Dict[str, Any]:
    attributes = {"media_title": title} if title else {}
    return {"ok": True, "result": {"state": state, "attributes": attributes}}


class _FakeTV:
    """A media_player that leaves playback only for the services it obeys."""

    def __init__(
        self,
        state: str,
        title: str | None = None,
        obeys: tuple[str, ...] = ("media_stop",),
        end_state: str = "idle",
        service: Dict[str, Any] | None = None,
        reachable: bool = True,
    ) -> None:
        self.state, self.title, self.obeys, self.end_state = state, title, set(obeys), end_state
        self.service = service or {"ok": True, "result": []}
        self.reachable = reachable
        self.calls: List[tuple[str, str]] = []

    async def get_state(self, entity_id: str, timeout_s: float = 15.0) -> Dict[str, Any]:
        if not self.reachable:
            return {"ok": False, "error": "connect failed"}
        return _state(self.state, self.title)

    async def call_service(self, domain: str, service: str, data: Dict[str, Any], timeout_s: float = 30.0):
        self.calls.append((domain, service))
        if service in self.obeys:
            self.state = self.end_state
        return self.service


@pytest.fixture
def tv(monkeypatch: pytest.MonkeyPatch):
    """Install a fake TV behind ha_client, with a fast poll."""

    def install(state: str, **kwargs: Any) -> _FakeTV:
        fake = _FakeTV(state, **kwargs)
        monkeypatch.setattr(ha_client, "ha_get_state", fake.get_state)
        monkeypatch.setattr(ha_client, "ha_call_service", fake.call_service)
        monkeypatch.setattr(ha_client, "_STOP_POLL_S", 0.0)
        monkeypatch.setattr(ha_client, "_STOP_SETTLE_S", 0.05)
        return fake

    return install


STOP, QUIT = ("media_player", "media_stop"), ("media_player", "turn_off")


@pytest.mark.asyncio
async def test_a_playing_tv_is_stopped_and_read_back(tv):
    """The NAS case: `media_stop` ends it; the result names the title."""
    fake = tv("playing", title="Trip opening")
    out = await ha_client.stop_tv_playback("SENTINEL_TV_ENTITY")
    assert out == {"result": "stopped", "title": "Trip opening", "state": "idle", "content_id": None, "position_s": None}
    assert fake.calls == [STOP]


@pytest.mark.asyncio
async def test_an_idle_tv_is_left_alone(tv):
    """Nothing on screen: no service call, and no claim of a stop."""
    fake = tv("idle")
    assert (await ha_client.stop_tv_playback("SENTINEL_TV_ENTITY"))["result"] == "not_playing"
    assert fake.calls == []


@pytest.mark.asyncio
async def test_a_receiver_that_ignores_stop_has_its_app_closed(tv):
    """The live YouTube case: `media_stop` changes nothing, `turn_off` ends it."""
    fake = tv("buffering", title="MV", obeys=("turn_off",), end_state="off")
    out = await ha_client.stop_tv_playback("SENTINEL_TV_ENTITY")
    assert out["result"] == "stopped" and out["state"] == "off"
    assert fake.calls == [STOP, QUIT]


@pytest.mark.asyncio
async def test_a_tv_that_keeps_playing_is_reported(tv):
    """HA answers 200 whether or not the receiver obeyed; the read-back decides."""
    fake = tv("paused", title="Clip", obeys=())
    out = await ha_client.stop_tv_playback("SENTINEL_TV_ENTITY")
    assert out["result"] == "still_playing" and out["state"] == "paused"
    assert fake.calls == [STOP, QUIT]


@pytest.mark.asyncio
async def test_a_failed_call_is_still_read_back(tv):
    """A refused or timed-out call can still have landed (D-040's lesson)."""
    fake = tv("playing", service={"ok": False, "error": "timeout"})
    assert (await ha_client.stop_tv_playback("SENTINEL_TV_ENTITY"))["result"] == "stopped"
    assert fake.calls == [STOP]


@pytest.mark.asyncio
async def test_an_unreachable_tv_is_unreachable(tv):
    """No state read, no service call."""
    fake = tv("playing", reachable=False)
    assert (await ha_client.stop_tv_playback("SENTINEL_TV_ENTITY"))["result"] == "unreachable"
    assert fake.calls == []


PAUSE, PLAY = ("media_player", "media_pause"), ("media_player", "media_play")


# --- the TV's place: position and content (follow-up, the bookmark) --------
def _with_place(state: str, position: float | None, *, ago_s: float = 0.0, content: str | None = None) -> Dict[str, Any]:
    attributes: Dict[str, Any] = {"media_title": "Clip"}
    if position is not None:
        attributes["media_position"] = position
        attributes["media_position_updated_at"] = (datetime.now(timezone.utc) - timedelta(seconds=ago_s)).isoformat()
    if content:
        attributes["media_content_id"] = content
    return {"ok": True, "result": {"state": state, "attributes": attributes}}


def test_a_paused_position_is_the_reported_one():
    """Paused, the TV is where it said it was, however long ago it said it."""
    assert ha_client.media_position_s(_with_place("paused", 125.0, ago_s=600)) == 125.0


def test_a_playing_position_adds_the_time_since_it_was_reported():
    """Playing, the time since `media_position_updated_at` is added."""
    position = ha_client.media_position_s(_with_place("playing", 100.0, ago_s=30))
    assert position == pytest.approx(130.0, abs=1.0)
    assert ha_client.media_position_s(_with_place("buffering", 10.0, ago_s=5)) == pytest.approx(15.0, abs=1.0)


def test_no_position_is_none():
    """A receiver that reports no position gives no place to keep."""
    assert ha_client.media_position_s(_with_place("playing", None)) is None
    assert ha_client.media_position_s({"ok": False}) is None


@pytest.mark.asyncio
async def test_stop_reports_the_place_it_read_before_stopping(tv, monkeypatch):
    """The read before the stop carries the position and the content id."""
    fake = tv("paused", title="Clip")
    reply = _with_place("paused", 42.0, content="http://nas.example.invalid/clip.mp4")

    async def get_state(entity_id: str, timeout_s: float = 15.0) -> Dict[str, Any]:
        return reply if fake.state == "paused" else _state(fake.state)

    monkeypatch.setattr(ha_client, "ha_get_state", get_state)
    out = await ha_client.stop_tv_playback("SENTINEL_TV_ENTITY")
    assert out["result"] == "stopped"
    assert out["position_s"] == 42.0 and out["content_id"] == "http://nas.example.invalid/clip.mp4"


class _ReplayTV:
    """A TV that a cast script starts at 0 s and a seek moves, if it obeys seeks."""

    def __init__(self, *, starts: bool = True, obeys_seek: bool = True) -> None:
        self.starts, self.obeys_seek = starts, obeys_seek
        self.state, self.position = "idle", None
        self.scripts: List[tuple[str, Dict[str, Any]]] = []
        self.seeks: List[float] = []

    async def run_script(self, script_name: str, data: Dict[str, Any], timeout_s: float = 60.0) -> Dict[str, Any]:
        self.scripts.append((script_name, data))
        if self.starts:
            self.state, self.position = "playing", 0.0
        return {"ok": True, "result": []}

    async def confirm(self, entity_id: str, *, timeout_s: float) -> Dict[str, Any]:
        return {"confirmed": self.state == "playing", "state": self.state}

    async def call_service(self, domain: str, service: str, data: Dict[str, Any], timeout_s: float = 30.0):
        assert (domain, service) == ("media_player", "media_seek")
        self.seeks.append(data["seek_position"])
        if self.obeys_seek:
            self.position = data["seek_position"]
        return {"ok": True, "result": []}

    async def get_state(self, entity_id: str, timeout_s: float = 15.0) -> Dict[str, Any]:
        return _with_place(self.state, self.position)


@pytest.fixture
def replay(monkeypatch: pytest.MonkeyPatch):
    """Install a `_ReplayTV` behind ha_client, with both cast scripts named and a fast poll."""
    monkeypatch.setenv("HANOVA_HA_SCRIPT_VIDEO_URL", "tv_show_video_url")
    monkeypatch.setenv("HANOVA_HA_SCRIPT_YOUTUBE", "tv_show_youtube")
    monkeypatch.setattr(ha_client, "_STOP_POLL_S", 0.0)
    monkeypatch.setattr(ha_client, "_STOP_SETTLE_S", 0.05)

    def install(**kwargs: Any) -> _ReplayTV:
        fake = _ReplayTV(**kwargs)
        monkeypatch.setattr(ha_client, "ha_run_script", fake.run_script)
        monkeypatch.setattr(ha_client, "confirm_cast_started", fake.confirm)
        monkeypatch.setattr(ha_client, "ha_call_service", fake.call_service)
        monkeypatch.setattr(ha_client, "ha_get_state", fake.get_state)
        return fake

    return install


_NAS_MARK = {"kind": "nas", "recast": "http://nas.example.invalid/clip.mp4", "title": "Clip", "place": "Coast",
             "position_s": 125.0}
_YOUTUBE_MARK = {"kind": "youtube", "recast": "vid123", "title": "MV", "place": None, "position_s": 60.0}


@pytest.mark.asyncio
async def test_a_nas_place_is_recast_through_the_video_script_and_seeked(replay):
    """The same script and fields `_cast_prepared_url` uses, then a seek that lands."""
    fake = replay()
    out = await ha_client.recast_from_bookmark("SENTINEL_TV_ENTITY", _NAS_MARK)
    assert fake.scripts == [("tv_show_video_url", {"url": _NAS_MARK["recast"], "title": "Clip",
                                                   "entity_id": "SENTINEL_TV_ENTITY"})]
    assert fake.seeks == [125.0]
    assert out == {"result": "resumed", "title": "Clip", "state": "playing", "from": "where it stopped"}


@pytest.mark.asyncio
async def test_a_youtube_place_is_recast_through_the_youtube_script_and_seeked(replay):
    """The fields `play_video` sends: the id and the entity."""
    fake = replay()
    out = await ha_client.recast_from_bookmark("SENTINEL_TV_ENTITY", _YOUTUBE_MARK)
    assert fake.scripts == [("tv_show_youtube", {"youtube_id": "vid123", "entity_id": "SENTINEL_TV_ENTITY"})]
    assert out["result"] == "resumed" and out["from"] == "where it stopped" and fake.seeks == [60.0]


@pytest.mark.asyncio
async def test_a_seek_that_did_not_land_is_tried_once_more_then_called_the_beginning(replay):
    """The read-back keeps Reachy honest: a restart is said as one."""
    fake = replay(obeys_seek=False)
    out = await ha_client.recast_from_bookmark("SENTINEL_TV_ENTITY", _NAS_MARK)
    assert out["result"] == "resumed" and out["from"] == "the beginning"
    assert fake.seeks == [125.0, 125.0]


@pytest.mark.asyncio
async def test_a_place_near_the_start_is_not_seeked(replay):
    """A second in, playing from the start is playing from where it stopped."""
    fake = replay()
    out = await ha_client.recast_from_bookmark("SENTINEL_TV_ENTITY", {**_NAS_MARK, "position_s": 1.0})
    assert out["from"] == "where it stopped" and fake.seeks == []


@pytest.mark.asyncio
async def test_a_recast_the_tv_never_showed_is_not_started(replay):
    """Dispatch is not display: no seek on a dark TV."""
    fake = replay(starts=False)
    out = await ha_client.recast_from_bookmark("SENTINEL_TV_ENTITY", _NAS_MARK)
    assert out["result"] == "not_started" and fake.seeks == []


@pytest.mark.asyncio
async def test_a_refused_recast_script_is_recast_failed(replay, monkeypatch):
    """HA refused the script: nothing to confirm or seek."""
    fake = replay()

    async def refused(script_name: str, data: Dict[str, Any], timeout_s: float = 60.0) -> Dict[str, Any]:
        return {"ok": False, "error": "Home Assistant returned HTTP 500", "status_code": 500}

    monkeypatch.setattr(ha_client, "ha_run_script", refused)
    out = await ha_client.recast_from_bookmark("SENTINEL_TV_ENTITY", _NAS_MARK)
    assert out["result"] == "recast_failed" and fake.seeks == []


# --- pause and resume on the TV (plan W2) ----------------------------------
@pytest.mark.asyncio
async def test_a_playing_tv_is_paused_and_read_back(tv):
    """Probe 2026-10-31: `media_pause` → `paused` on both receivers; the title comes back."""
    fake = tv("playing", title="Trip opening", obeys=("media_pause",), end_state="paused")
    out = await ha_client.pause_tv_playback("SENTINEL_TV_ENTITY")
    assert out == {"result": "paused", "title": "Trip opening", "state": "paused"}
    assert fake.calls == [PAUSE]


@pytest.mark.asyncio
async def test_a_paused_tv_is_already_paused_and_left_alone(tv):
    """No second command to a TV that is already where the person wants it."""
    fake = tv("paused", title="Clip")
    assert (await ha_client.pause_tv_playback("SENTINEL_TV_ENTITY"))["result"] == "already_paused"
    assert fake.calls == []


@pytest.mark.asyncio
async def test_an_idle_tv_has_nothing_to_pause(tv):
    """Nothing on screen: no service call, and no claim of a pause."""
    fake = tv("idle")
    assert (await ha_client.pause_tv_playback("SENTINEL_TV_ENTITY"))["result"] == "not_playing"
    assert fake.calls == []


@pytest.mark.asyncio
async def test_a_receiver_that_ignores_pause_is_reported(tv):
    """HA answers 200 either way; a TV still playing after the settle is still playing."""
    fake = tv("buffering", title="MV", obeys=())
    out = await ha_client.pause_tv_playback("SENTINEL_TV_ENTITY")
    assert out["result"] == "still_playing" and out["state"] == "buffering"
    assert fake.calls == [PAUSE]


@pytest.mark.asyncio
async def test_a_tv_that_leaves_playback_instead_of_pausing_has_ended(tv):
    """It went idle, not paused: the video is gone, and the result says so."""
    tv("playing", obeys=("media_pause",), end_state="idle")
    out = await ha_client.pause_tv_playback("SENTINEL_TV_ENTITY")
    assert out["result"] == "ended" and out["state"] == "idle"


@pytest.mark.asyncio
async def test_a_failed_pause_call_is_still_read_back(tv):
    """D-040's lesson again: a refused call can still have landed."""
    tv("playing", obeys=("media_pause",), end_state="paused", service={"ok": False, "error": "timeout"})
    assert (await ha_client.pause_tv_playback("SENTINEL_TV_ENTITY"))["result"] == "paused"


@pytest.mark.asyncio
async def test_a_paused_tv_is_resumed_and_buffering_counts_as_playing(tv):
    """Probe 2026-10-31: the YouTube app answers `media_play` with `buffering`, its normal playing."""
    fake = tv("paused", title="MV", obeys=("media_play",), end_state="buffering")
    out = await ha_client.resume_tv_playback("SENTINEL_TV_ENTITY")
    assert out == {"result": "resumed", "title": "MV", "state": "buffering"}
    assert fake.calls == [PLAY]


@pytest.mark.asyncio
async def test_resume_leaves_a_playing_or_idle_tv_alone(tv):
    """Already playing is a no-op; idle has nothing paused to resume."""
    fake = tv("playing", title="Clip")
    assert (await ha_client.resume_tv_playback("SENTINEL_TV_ENTITY"))["result"] == "already_playing"
    fake.state = "idle"
    assert (await ha_client.resume_tv_playback("SENTINEL_TV_ENTITY"))["result"] == "not_paused"
    assert fake.calls == []


@pytest.mark.asyncio
async def test_a_tv_that_stays_paused_or_ends_is_reported(tv):
    """A receiver that ignored play stays paused; one whose stream died went idle."""
    tv("paused", obeys=())
    assert (await ha_client.resume_tv_playback("SENTINEL_TV_ENTITY"))["result"] == "still_paused"
    tv("paused", obeys=("media_play",), end_state="idle")
    out = await ha_client.resume_tv_playback("SENTINEL_TV_ENTITY")
    assert out["result"] == "ended" and out["state"] == "idle"


@pytest.mark.asyncio
async def test_an_unreachable_tv_cannot_be_paused_or_resumed(tv):
    """No state read, no service call."""
    fake = tv("playing", reachable=False)
    assert (await ha_client.pause_tv_playback("SENTINEL_TV_ENTITY"))["result"] == "unreachable"
    assert (await ha_client.resume_tv_playback("SENTINEL_TV_ENTITY"))["result"] == "unreachable"
    assert fake.calls == []


# --- the tools ---------------------------------------------------------------
_SPEAKER_STOPPED = {"ok": True, "status": "stopped", "title": "A Song"}
_SPEAKER_IDLE = {"ok": True, "status": "nothing_playing"}


def _tv(result: str, title: str | None = None, state: str | None = "idle") -> Dict[str, Any]:
    return {"result": result, "title": title, "state": state}


@pytest.fixture
def halves(monkeypatch: pytest.MonkeyPatch):
    """Configure HA, fake each half, and record what the tools did to the session state."""
    monkeypatch.setenv("HA_URL", "http://ha.example.invalid:8123")
    monkeypatch.setenv("HA_TOKEN", "tok")
    monkeypatch.setenv("HANOVA_CAST_ENTITY", "SENTINEL_TV_ENTITY")
    record: Dict[str, Any] = {"cleared": 0, "tv_calls": 0}
    monkeypatch.setattr(nas, "clear_session", lambda: record.__setitem__("cleared", record["cleared"] + 1))
    tv_session.reset_for_tests()
    tv_session.mark_started("nas")

    def install(player_method: str, speaker: Dict[str, Any], tv_helper: str, tv_result: Dict[str, Any] | None):
        async def player(deps: Any) -> Dict[str, Any]:
            return speaker

        async def tv_call(entity_id: str) -> Dict[str, Any]:
            assert entity_id == "SENTINEL_TV_ENTITY"
            assert tv_result is not None, "the TV half was not expected to be called"
            record["tv_calls"] += 1
            return tv_result

        monkeypatch.setattr(stop_music.PLAYER, player_method, player)
        monkeypatch.setattr(stop_music, tv_helper, tv_call)
        return record

    yield install
    tv_session.reset_for_tests()


def _stop(halves, speaker, tv_result):
    return halves("stop", speaker, "stop_tv_playback", tv_result)


@pytest.mark.asyncio
async def test_stop_stops_the_speaker_and_the_tv_in_one_call(halves):
    """The operator's rule: both playing, one stop, and both are named."""
    record = _stop(halves, _SPEAKER_STOPPED, _tv("stopped", "Trip opening"))
    out = await StopMusic()(deps=None)
    assert out == {
        "ok": True,
        "status": "stopped",
        "stopped": ["speaker", "tv"],
        "speaker_title": "A Song",
        "tv_title": "Trip opening",
    }
    assert record["cleared"] == 1 and not tv_session.active()


@pytest.mark.asyncio
async def test_stop_with_only_the_speaker_playing_still_checks_the_tv(halves):
    """The TV is asked even while the speaker played; finding it idle ends the video session."""
    record = _stop(halves, _SPEAKER_STOPPED, _tv("not_playing"))
    out = await StopMusic()(deps=None)
    assert out == {"ok": True, "status": "stopped", "stopped": ["speaker"], "speaker_title": "A Song"}
    assert record["tv_calls"] == 1 and record["cleared"] == 1 and not tv_session.active()


@pytest.mark.asyncio
async def test_stop_with_only_the_tv_playing_stops_the_tv_and_ends_the_trip(halves):
    """The morning's case: the TV stops, `skip` cannot carry the trip on, and no "not paused" note."""
    record = _stop(halves, _SPEAKER_IDLE, _tv("stopped", "Trip opening"))
    out = await StopMusic()(deps=None)
    assert out == {"ok": True, "status": "stopped", "stopped": ["tv"], "tv_title": "Trip opening"}
    assert record["cleared"] == 1 and not tv_session.active()


@pytest.mark.asyncio
async def test_nothing_playing_anywhere_is_said_plainly(halves):
    """No success the model could narrate: nothing was stopped."""
    _stop(halves, _SPEAKER_IDLE, _tv("not_playing"))
    out = await StopMusic()(deps=None)
    assert out["ok"] is True and out["status"] == "nothing_playing" and "stopped" not in out
    assert "nothing was stopped" in out["detail"] and "do not say you stopped" in out["detail"]


@pytest.mark.asyncio
async def test_a_tv_that_did_not_stop_is_an_error_that_keeps_the_speaker_stop(halves):
    """The speaker half is still reported; the person hears the remote can stop the video."""
    record = _stop(halves, _SPEAKER_STOPPED, _tv("still_playing", "Clip", "playing"))
    out = await StopMusic()(deps=None)
    assert out["ok"] is False and out["stopped"] == ["speaker"] and out["speaker_title"] == "A Song"
    assert out["error"].startswith("The music on your speaker stopped.")
    assert "did not stop (it still reads playing)" in out["error"] and "remote" in out["error"]
    assert record["cleared"] == 0 and tv_session.active(), "the video is still on screen"


@pytest.mark.asyncio
async def test_an_unreachable_tv_is_an_error_for_the_person(halves):
    """Home Assistant silent: say it could not be stopped; the remote is the fix."""
    _stop(halves, _SPEAKER_IDLE, _tv("unreachable", state=None))
    out = await StopMusic()(deps=None)
    assert out["ok"] is False and "stopped" not in out
    assert "Nothing was playing on your speaker." in out["error"] and "remote" in out["error"]
    assert tv_session.active(), "an unread TV may still be showing the video"


@pytest.mark.asyncio
async def test_home_assistant_down_after_the_speaker_stopped_says_both(halves):
    """One half done, one unknown: the error carries the stop that did happen."""
    _stop(halves, _SPEAKER_STOPPED, _tv("unreachable", state=None))
    out = await StopMusic()(deps=None)
    assert out["ok"] is False and out["stopped"] == ["speaker"]
    assert out["error"].startswith("The music on your speaker stopped.")
    assert "Home Assistant did not answer about the TV" in out["error"]


@pytest.mark.asyncio
async def test_a_failed_speaker_stop_is_reported_beside_a_tv_that_stopped(halves):
    """The speaker did not acknowledge: the model may retry once, and the TV stop still counts."""
    _stop(halves, {"ok": False, "status": "stop_failed", "error": "x"}, _tv("stopped", "Clip"))
    out = await StopMusic()(deps=None)
    assert out["ok"] is False and out["stopped"] == ["tv"] and out["tv_title"] == "Clip"
    assert out["error"].startswith("The video on the TV stopped.")
    assert "did not acknowledge the stop" in out["error"] and "Call stop again yourself once" in out["error"]


@pytest.mark.asyncio
async def test_without_home_assistant_only_the_speaker_is_stopped(halves, monkeypatch):
    """No HA configured: the TV is never asked, and the answer names the speaker only."""
    record = _stop(halves, _SPEAKER_IDLE, None)
    monkeypatch.delenv("HA_URL")
    out = await StopMusic()(deps=None)
    assert out["status"] == "nothing_playing" and "on your speaker, so nothing was stopped" in out["detail"]
    assert record["tv_calls"] == 0


def _pause(halves, speaker, tv_result):
    return halves("pause_by_user", speaker, "pause_tv_playback", tv_result)


_SPEAKER_PAUSED = {"ok": True, "status": "paused", "title": "A Song"}


@pytest.mark.asyncio
async def test_pause_pauses_the_speaker_and_the_tv_together(halves):
    """Both halves pause; a paused video still counts as on screen."""
    _pause(halves, _SPEAKER_PAUSED, _tv("paused", "Clip", "paused"))
    out = await PausePlayback()(deps=None)
    assert out == {
        "ok": True,
        "status": "paused",
        "paused": ["speaker", "tv"],
        "speaker_title": "A Song",
        "tv_title": "Clip",
    }
    assert tv_session.active(), "pause must not end the video session"


@pytest.mark.asyncio
async def test_pause_with_one_half_playing_names_that_half(halves):
    """Speaker only, then TV only (already paused counts as paused)."""
    _pause(halves, _SPEAKER_PAUSED, _tv("not_playing"))
    out = await PausePlayback()(deps=None)
    assert out["paused"] == ["speaker"] and "tv_title" not in out
    assert not tv_session.active(), "an idle TV ends the video session"

    _pause(halves, {"ok": True, "status": "nothing_playing"}, _tv("already_paused", "Clip", "paused"))
    out = await PausePlayback()(deps=None)
    assert out == {"ok": True, "status": "paused", "paused": ["tv"], "tv_title": "Clip"}


@pytest.mark.asyncio
async def test_pause_with_nothing_playing_says_so(halves):
    """Nothing to pause anywhere, and a TV that went idle ends the video session."""
    _pause(halves, {"ok": True, "status": "nothing_playing"}, _tv("ended", "Clip", "idle"))
    out = await PausePlayback()(deps=None)
    assert out["ok"] is True and out["status"] == "nothing_playing"
    assert "nothing was paused" in out["detail"] and "do not say you paused" in out["detail"]
    assert not tv_session.active()


@pytest.mark.asyncio
async def test_a_tv_that_did_not_pause_is_an_error_with_the_remote(halves):
    """The read-back failed: the speaker half stands, the TV is still playing, the remote can pause it."""
    _pause(halves, _SPEAKER_PAUSED, _tv("still_playing", "MV", "buffering"))
    out = await PausePlayback()(deps=None)
    assert out["ok"] is False and out["paused"] == ["speaker"]
    assert out["error"].startswith("The music on your speaker is paused.")
    assert "did not pause (it still reads buffering)" in out["error"] and "remote" in out["error"]
    assert tv_session.active()


@pytest.mark.asyncio
async def test_a_speaker_that_refused_the_pause_is_an_error(halves):
    """The daemon refused: the music still plays, and the model must not say it paused."""
    _pause(halves, {"ok": False, "status": "pause_failed", "error": "x"}, _tv("not_playing"))
    out = await PausePlayback()(deps=None)
    assert out["ok"] is False and "paused" not in out
    assert "did not pause" in out["error"] and "do not say it paused" in out["error"]


@pytest.mark.asyncio
async def test_pause_with_home_assistant_down_says_the_tv_is_unknown(halves):
    """HA silent: the remote is the fix."""
    _pause(halves, {"ok": True, "status": "nothing_playing"}, _tv("unreachable", state=None))
    out = await PausePlayback()(deps=None)
    assert out["ok"] is False and "could not be paused" in out["error"] and "remote" in out["error"]


def _resume(halves, speaker, tv_result):
    return halves("resume_by_user", speaker, "resume_tv_playback", tv_result)


_SPEAKER_RESUMED = {"ok": True, "status": "resumed", "title": "A Song"}
_SPEAKER_NOTHING = {"ok": True, "status": "nothing_to_resume"}


@pytest.mark.asyncio
async def test_resume_resumes_both_halves(halves):
    """Both were paused; both play again and are named."""
    _resume(halves, _SPEAKER_RESUMED, _tv("resumed", "Clip", "playing"))
    out = await ResumePlayback()(deps=None)
    assert out == {
        "ok": True,
        "status": "resumed",
        "resumed": ["speaker", "tv"],
        "speaker_title": "A Song",
        "tv_title": "Clip",
    }
    assert tv_session.active()


@pytest.mark.asyncio
async def test_resume_with_one_half_paused_names_that_half(halves):
    """Speaker only, then TV only."""
    _resume(halves, _SPEAKER_RESUMED, _tv("not_paused", state="off"))
    assert (await ResumePlayback()(deps=None))["resumed"] == ["speaker"]
    _resume(halves, _SPEAKER_NOTHING, _tv("resumed", "MV", "buffering"))
    out = await ResumePlayback()(deps=None)
    assert out == {"ok": True, "status": "resumed", "resumed": ["tv"], "tv_title": "MV"}


@pytest.mark.asyncio
async def test_resume_with_nothing_paused_says_a_stopped_video_is_played_again(halves):
    """A stopped video is not paused: the error points at nas or youtube, not at resume."""
    _resume(halves, _SPEAKER_NOTHING, _tv("not_paused", state="idle"))
    out = await ResumePlayback()(deps=None)
    assert out["ok"] is False and out["status"] == "nothing_paused"
    assert "nothing was resumed" in out["error"]
    assert "a stopped video is played again with nas or youtube" in out["error"]
    assert "do not say you resumed" in out["error"]
    assert not tv_session.active()


@pytest.mark.asyncio
async def test_resume_of_something_already_playing_names_it(halves):
    """Nothing paused, but the TV plays: say it is already playing, not that nothing is on."""
    _resume(halves, _SPEAKER_NOTHING, _tv("already_playing", "Clip", "playing"))
    out = await ResumePlayback()(deps=None)
    assert out["ok"] is True and out["status"] == "already_playing" and out["playing"] == ["tv"]
    assert out["tv_title"] == "Clip"


@pytest.mark.asyncio
async def test_a_tv_that_stayed_paused_is_an_error_with_the_remote(halves):
    """The read-back failed: do not say it plays."""
    _resume(halves, _SPEAKER_RESUMED, _tv("still_paused", "Clip", "paused"))
    out = await ResumePlayback()(deps=None)
    assert out["ok"] is False and out["resumed"] == ["speaker"]
    assert "did not resume" in out["error"] and "remote" in out["error"] and "do not say it is playing" in out["error"]


@pytest.mark.asyncio
async def test_a_tv_that_ended_on_resume_ends_the_session_and_offers_to_play_again(halves):
    """The W6 case: the stream died, so the video is gone and is played again, not resumed."""
    _resume(halves, _SPEAKER_NOTHING, _tv("ended", "Clip", "idle"))
    out = await ResumePlayback()(deps=None)
    assert out["ok"] is False and "has ended" in out["error"] and "nas or youtube" in out["error"]
    assert not tv_session.active()


@pytest.mark.asyncio
async def test_a_speaker_resume_that_failed_is_an_error(halves):
    """The re-cut failed and the music is stopped: say so, offer to play it again."""
    _resume(halves, {"ok": False, "status": "resume_failed"}, _tv("not_paused", state="idle"))
    out = await ResumePlayback()(deps=None)
    assert out["ok"] is False and "could not be resumed" in out["error"] and "music play" in out["error"]


def test_the_music_family_routes_all_four_actions():
    """`play | pause | resume | stop`, one verb family, each to its own delegate."""
    assert Music.parameters_schema["properties"]["action"]["enum"] == ["play", "pause", "resume", "stop"]
    assert isinstance(Music.ACTIONS["pause"], PausePlayback)
    assert isinstance(Music.ACTIONS["resume"], ResumePlayback)
    assert isinstance(Music.ACTIONS["stop"], StopMusic)
    gloss = Music.parameters_schema["properties"]["action"]["description"]
    assert "pause 暫停" in gloss and "resume 繼續播放" in gloss and "stop 停止播放" in gloss


def test_music_says_how_pause_differs_from_stop() -> None:
    """Mini picked `stop` for 「暫停」 2 of 6 times while the enum said nothing of the difference."""
    from reachy_companion.tools.music import Music

    gloss = Music.parameters_schema["properties"]["action"]["description"]
    assert "停在原處" in gloss and "從頭播" in gloss
    assert "`pause` keeps the place" in Music.description


# --- stop keeps the place; resume plays it on (follow-up) -------------------
_URL = "http://nas.example.invalid/clip.mp4"


def _stopped_at(position: float | None, content: str | None = None) -> Dict[str, Any]:
    return {"result": "stopped", "title": "Clip", "state": "idle", "content_id": content, "position_s": position}


@pytest.mark.asyncio
async def test_stopping_reachys_own_clip_keeps_its_place(halves):
    """The bookmark holds what plays it again, the title, the place and the position; the result says so."""
    _stop(halves, _SPEAKER_IDLE, _stopped_at(125.0, content=_URL))
    tv_session.mark_started("nas", title="2019 Coast morning", place="Coast", recast=_URL)
    out = await StopMusic()(deps=None)
    assert out["stopped"] == ["tv"] and "繼續" in out["note"] and "music resume" in out["note"]
    mark = tv_session.bookmark()
    assert mark is not None
    assert (mark["kind"], mark["recast"], mark["title"], mark["place"], mark["position_s"]) == (
        "nas", _URL, "Clip", "Coast", 125.0)
    assert not tv_session.active(), "stop still really stops"


@pytest.mark.asyncio
async def test_a_youtube_stop_keeps_the_id_the_tv_reports(halves):
    """YouTube may have moved on to the next video, so the TV's id wins over the one Reachy cast."""
    _stop(halves, _SPEAKER_IDLE, _stopped_at(60.0, content="vid999"))
    tv_session.mark_started("youtube", title="MV", recast="vid123")
    await StopMusic()(deps=None)
    assert tv_session.bookmark()["recast"] == "vid999"


@pytest.mark.asyncio
async def test_a_video_reachy_did_not_start_keeps_no_place(halves):
    """Without Reachy's own cast there is no telling how to play it again."""
    _stop(halves, _SPEAKER_IDLE, _stopped_at(125.0, content="something"))
    tv_session.reset_for_tests()
    out = await StopMusic()(deps=None)
    assert out == {"ok": True, "status": "stopped", "stopped": ["tv"], "tv_title": "Clip"}
    assert tv_session.bookmark() is None


@pytest.mark.asyncio
async def test_a_tv_without_a_position_keeps_no_place(halves):
    """No position, no bookmark, and no promise in the result."""
    _stop(halves, _SPEAKER_IDLE, _stopped_at(None, content=_URL))
    tv_session.mark_started("nas", recast=_URL)
    out = await StopMusic()(deps=None)
    assert "note" not in out and tv_session.bookmark() is None


def _resume_from(halves, monkeypatch, again: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Leave the TV unpaused and record the re-cast the resume asks for."""
    _resume(halves, _SPEAKER_NOTHING, _tv("not_paused", state="idle"))
    marks: List[Dict[str, Any]] = []

    async def recast(entity_id: str, mark: Dict[str, Any]) -> Dict[str, Any]:
        assert entity_id == "SENTINEL_TV_ENTITY"
        marks.append(mark)
        return again

    monkeypatch.setattr(stop_music, "recast_from_bookmark", recast)
    return marks


def _keep(kind: str = "nas", recast: str = _URL, position: float = 125.0) -> None:
    tv_session.reset_for_tests()
    tv_session.save_bookmark(kind=kind, recast=recast, title="Clip", place="Coast", position_s=position)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind, recast", [("nas", _URL), ("youtube", "vid123")])
async def test_resume_plays_a_kept_place_on_from_where_it_stopped(halves, monkeypatch, kind, recast):
    """Nothing paused, a fresh bookmark: re-cast, seek, read back, and the video is on again."""
    marks = _resume_from(halves, monkeypatch,
                         {"result": "resumed", "title": "Clip", "state": "playing", "from": "where it stopped"})
    _keep(kind, recast)
    out = await ResumePlayback()(deps=None)
    assert out == {"ok": True, "status": "resumed", "resumed": ["tv"], "tv_title": "Clip",
                   "from": "where it stopped"}
    assert marks[0]["kind"] == kind and marks[0]["recast"] == recast and marks[0]["position_s"] == 125.0
    assert tv_session.now_playing() == {"kind": kind, "title": "Clip", "place": "Coast", "recast": recast}
    assert tv_session.bookmark() is None, "the bookmark is spent"


@pytest.mark.asyncio
async def test_a_kept_place_that_restarted_says_from_the_beginning(halves, monkeypatch):
    """The seek did not land; Reachy says it started over."""
    _resume_from(halves, monkeypatch, {"result": "resumed", "title": "Clip", "state": "playing", "from": "the beginning"})
    _keep()
    out = await ResumePlayback()(deps=None)
    assert out["ok"] is True and out["from"] == "the beginning"


@pytest.mark.asyncio
async def test_a_stale_bookmark_is_ignored(halves, monkeypatch):
    """Older than six hours, 「繼續」 is a new request: nothing_paused, and no re-cast."""
    marks = _resume_from(halves, monkeypatch, {"result": "resumed", "title": "Clip", "state": "playing", "from": None})
    _keep()
    monkeypatch.setattr(tv_session, "BOOKMARK_MAX_AGE_S", -1.0)
    out = await ResumePlayback()(deps=None)
    assert out["status"] == "nothing_paused" and "played again with nas or youtube" in out["error"]
    assert marks == [] and tv_session.bookmark() is None


@pytest.mark.asyncio
async def test_a_real_pause_resumes_with_media_play_not_the_bookmark(tv, halves, monkeypatch):
    """A paused video on screen is the one the person means."""
    fake = tv("paused", title="Clip", obeys=("media_play",), end_state="playing")
    monkeypatch.setattr(stop_music.PLAYER, "resume_by_user", lambda deps: _async(_SPEAKER_NOTHING))
    monkeypatch.setattr(stop_music, "resume_tv_playback", ha_client.resume_tv_playback)

    async def recast(entity_id: str, mark: Dict[str, Any]) -> Dict[str, Any]:
        raise AssertionError("a real pause must not re-cast the bookmark")

    monkeypatch.setattr(stop_music, "recast_from_bookmark", recast)
    _keep()
    out = await ResumePlayback()(deps=None)
    assert out["resumed"] == ["tv"] and "from" not in out
    assert fake.calls == [PLAY]


async def _async(value: Dict[str, Any]) -> Dict[str, Any]:
    return value


@pytest.mark.asyncio
async def test_a_kept_place_the_tv_did_not_take_is_an_error_and_stays_kept(halves, monkeypatch):
    """Refused or dark: say so, and keep the bookmark for another try."""
    _resume_from(halves, monkeypatch, {"result": "not_started", "title": "Clip", "state": "off", "from": None})
    _keep()
    out = await ResumePlayback()(deps=None)
    assert out["ok"] is False and "shows no playback" in out["error"] and "do not say it is playing" in out["error"]
    assert tv_session.bookmark() is not None

    _resume_from(halves, monkeypatch, {"result": "recast_failed", "title": "Clip", "state": None, "from": None})
    out = await ResumePlayback()(deps=None)
    assert out["ok"] is False and "could not be started again" in out["error"]
    assert tv_session.bookmark() is not None
