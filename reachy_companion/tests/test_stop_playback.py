"""`music stop` ends what Reachy plays: the speaker first, else the TV (2026-10-31).

Live failure it answers: 「停下你的播放」 while a NAS clip played reached
`music stop`, which only knew the speaker, returned `nothing_playing`, and Reachy
said it had stopped while the video went on.
"""

from __future__ import annotations
from typing import Any, Dict, List

import pytest

from reachy_companion.tools import stop_music
from reachy_companion.hanova import nas, ha_client
from reachy_companion.tools.stop_music import StopMusic


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
    fake = tv("playing", title="Boston opening")
    out = await ha_client.stop_tv_playback("SENTINEL_TV_ENTITY")
    assert out == {"result": "stopped", "title": "Boston opening", "state": "idle"}
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


# --- the tool --------------------------------------------------------------
@pytest.fixture
def stop(monkeypatch: pytest.MonkeyPatch):
    """Configure HA, fake the speaker, and capture the TV stop."""
    monkeypatch.setenv("HA_URL", "http://ha.example.invalid:8123")
    monkeypatch.setenv("HA_TOKEN", "tok")
    monkeypatch.setenv("HANOVA_CAST_ENTITY", "SENTINEL_TV_ENTITY")

    def install(speaker: Dict[str, Any], tv_stop: Dict[str, Any] | None = None, tv_state: str = "idle"):
        async def player_stop(deps: Any) -> Dict[str, Any]:
            return speaker

        async def stop_tv(entity_id: str) -> Dict[str, Any]:
            assert entity_id == "SENTINEL_TV_ENTITY"
            assert tv_stop is not None, "the TV must not be stopped while the speaker was playing"
            return tv_stop

        async def get_state(entity_id: str, timeout_s: float = 15.0) -> Dict[str, Any]:
            return _state(tv_state)

        monkeypatch.setattr(stop_music.PLAYER, "stop", player_stop)
        monkeypatch.setattr(stop_music, "stop_tv_playback", stop_tv)
        monkeypatch.setattr(stop_music, "ha_get_state", get_state)

    return install


@pytest.mark.asyncio
async def test_speaker_music_is_stopped_and_the_tv_untouched(stop):
    """Music on the speaker is what `stop` ends first."""
    stop({"ok": True, "status": "stopped", "title": "A Song"})
    out = await StopMusic()(deps=None)
    assert out == {"ok": True, "status": "stopped", "title": "A Song", "stopped": "speaker"}


@pytest.mark.asyncio
async def test_speaker_stopped_while_the_tv_also_plays_says_so(stop):
    """The TV video is named as still playing, with the fix the model can make."""
    stop({"ok": True, "status": "stopped", "title": "A Song"}, tv_state="playing")
    out = await StopMusic()(deps=None)
    assert out["stopped"] == "speaker" and out["tv_still_playing"] is True
    assert "call stop again yourself" in out["note"]


@pytest.mark.asyncio
async def test_a_silent_speaker_sends_the_stop_to_the_tv(stop, monkeypatch):
    """The morning's case: the TV stops, and `skip` cannot carry the trip on."""
    cleared: List[bool] = []
    monkeypatch.setattr(nas, "clear_session", lambda: cleared.append(True))
    stop({"ok": True, "status": "nothing_playing"}, {"result": "stopped", "title": "Boston opening", "state": "idle"})
    out = await StopMusic()(deps=None)
    assert out["ok"] is True and out["status"] == "stopped" and out["stopped"] == "tv"
    assert out["title"] == "Boston opening"
    assert "not paused" in out["note"] and "from the beginning" in out["note"]
    assert cleared == [True]


@pytest.mark.asyncio
async def test_nothing_playing_anywhere_is_said_plainly(stop):
    """No success the model could narrate: nothing was stopped."""
    stop({"ok": True, "status": "nothing_playing"}, {"result": "not_playing", "title": None, "state": "idle"})
    out = await StopMusic()(deps=None)
    assert out["ok"] is True and out["status"] == "nothing_playing"
    assert "nothing was stopped" in out["detail"] and "do not say you stopped" in out["detail"]


@pytest.mark.asyncio
async def test_a_tv_that_did_not_stop_is_an_error_with_the_next_step(stop):
    """The person hears it is still playing, and that the remote can stop it."""
    stop({"ok": True, "status": "nothing_playing"}, {"result": "still_playing", "title": "Clip", "state": "playing"})
    out = await StopMusic()(deps=None)
    assert out["ok"] is False
    assert "did not stop" in out["error"] and "remote" in out["error"]


@pytest.mark.asyncio
async def test_an_unreachable_tv_is_an_error_for_the_person(stop):
    """Home Assistant silent: say it could not be stopped; the remote is the fix."""
    stop({"ok": True, "status": "nothing_playing"}, {"result": "unreachable", "title": None, "state": None})
    out = await StopMusic()(deps=None)
    assert out["ok"] is False and "remote" in out["error"]


@pytest.mark.asyncio
async def test_without_home_assistant_only_the_speaker_is_stopped(stop, monkeypatch):
    """No HA configured: the old speaker-only answer, worded as nothing stopped."""
    stop({"ok": True, "status": "nothing_playing"})
    monkeypatch.delenv("HA_URL")
    out = await StopMusic()(deps=None)
    assert out["status"] == "nothing_playing" and "nothing was stopped" in out["detail"]


@pytest.mark.asyncio
async def test_a_failed_speaker_stop_is_returned_as_is(stop):
    """A speaker that could not be stopped is the answer; the TV is not tried."""
    stop({"ok": False, "status": "stop_failed", "error": "x"})
    out = await StopMusic()(deps=None)
    assert out == {"ok": False, "status": "stop_failed", "error": "x"}
