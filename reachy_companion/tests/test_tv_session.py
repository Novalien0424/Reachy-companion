"""The video session the answer gate reads while a video plays (2026-10-31, W3)."""

from __future__ import annotations
import time
from typing import Any, Dict

import pytest

from reachy_companion.hanova import settings, ha_client, tv_session


@pytest.fixture(autouse=True)
def _clean() -> Any:
    tv_session.mark_ended("test setup")
    yield
    tv_session.mark_ended("test teardown")


def _install_tv(monkeypatch: pytest.MonkeyPatch, reply: Dict[str, Any]) -> list[str]:
    reads: list[str] = []

    async def get_state(entity_id: str, timeout_s: float = 15.0) -> Dict[str, Any]:
        reads.append(entity_id)
        return reply

    monkeypatch.setattr(settings, "cast_entity", lambda: "SENTINEL_TV_ENTITY")
    monkeypatch.setattr(ha_client, "ha_get_state", get_state)
    return reads


def _state(state: str) -> Dict[str, Any]:
    return {"ok": True, "result": {"state": state, "attributes": {}}}


def test_start_and_end() -> None:
    """A cast starts the session and a stop ends it."""
    assert not tv_session.active()
    tv_session.mark_started("nas")
    assert tv_session.active() and tv_session.kind() == "nas"
    tv_session.mark_ended("stop")
    assert not tv_session.active() and tv_session.kind() is None


def test_stale_only_after_the_window() -> None:
    """The session turns stale only after `STALE_AFTER_S` without a look."""
    tv_session.mark_started("youtube")
    assert not tv_session.is_stale()
    assert tv_session.is_stale(now=time.monotonic() + tv_session.STALE_AFTER_S + 1)


def test_inactive_is_never_stale() -> None:
    """No session, nothing to re-check."""
    assert not tv_session.is_stale(now=time.monotonic() + 3600)


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["playing", "buffering", "paused"])
async def test_playback_keeps_the_session_and_resets_the_look(monkeypatch: pytest.MonkeyPatch, state: str) -> None:
    """A TV still in playback keeps the session and restarts the stale clock."""
    reads = _install_tv(monkeypatch, _state(state))
    tv_session.mark_started("nas")
    monkeypatch.setattr(tv_session, "_looked_at", time.monotonic() - 100)
    assert tv_session.is_stale()
    assert await tv_session.confirm_still_playing() is True
    assert reads == ["SENTINEL_TV_ENTITY"]
    assert tv_session.active() and not tv_session.is_stale()


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["idle", "off", "on", "unavailable"])
async def test_a_tv_out_of_playback_ends_the_session(monkeypatch: pytest.MonkeyPatch, state: str) -> None:
    """A clip that ended on its own ends the session, so the normal rules return."""
    _install_tv(monkeypatch, _state(state))
    tv_session.mark_started("nas")
    assert await tv_session.confirm_still_playing() is False
    assert not tv_session.active()


@pytest.mark.asyncio
async def test_an_unreadable_tv_keeps_the_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """HA down keeps Reachy name-only rather than answering the room."""
    _install_tv(monkeypatch, {"ok": False, "error": "connect failed"})
    tv_session.mark_started("youtube")
    assert await tv_session.confirm_still_playing() is True
    assert tv_session.active()


@pytest.mark.asyncio
async def test_no_read_without_a_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a session the TV is never read."""
    reads = _install_tv(monkeypatch, _state("playing"))
    assert await tv_session.confirm_still_playing() is False
    assert reads == []
