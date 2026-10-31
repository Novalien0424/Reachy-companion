"""A NAS clip served by the NAS's own web server (2026-10-31 plan, W6, option a).

The robot-served routes die with the app: when Reachy sleeps, a clip on the TV
played ~30 s from its buffer and went idle. With `HANOVA_NAS_HTTP_BASE` set, the
TV is handed the NAS's URL for the cast copy after a quick probe; when the probe
fails, the robot route still plays it; unset, nothing changes.

Synthetic identifiers only (see `test_hanova_nas.py`).
"""

from __future__ import annotations
import logging
from typing import Any, Dict, List

import httpx
import pytest

from reachy_companion.hanova import nas, settings


ROBOT = "http://robot.example.invalid:7860"
NAS_BASE = "http://nas.example.invalid:8080"
VIDEO: Dict[str, Any] = {
    "path": "SENTINEL_SRC_DIR_q4/Trip A & B/甲城/clip 01.mpg",
    "cast_path": "SENTINEL_CAST_DIR_q4/Trip A & B/甲城/clip 01.mp4",
    "cast_ready": True,
    "year": 2019,
    "place": "SENTINEL_PLACE_q4",
    "label": "morning",
    "top_folder": "Trip A & B",
}
ENCODED = f"{NAS_BASE}/Trip%20A%20%26%20B/%E7%94%B2%E5%9F%8E/clip%2001.mp4"


@pytest.fixture(autouse=True)
def configured(monkeypatch: pytest.MonkeyPatch) -> List[Dict[str, Any]]:
    """Configure the NAS and HA, record every cast, and touch no real network."""
    monkeypatch.setenv("HANOVA_NAS_HOST", "nas.example.invalid")
    monkeypatch.setenv("HANOVA_NAS_USER", "u")
    monkeypatch.setenv("HANOVA_NAS_PASSWORD", "p")
    monkeypatch.setenv("HANOVA_NAS_SHARE", "SENTINEL_SHARE_q4")
    monkeypatch.setenv("HANOVA_NAS_SUBPATH", "SENTINEL_SRC_DIR_q4")
    monkeypatch.setenv("HANOVA_NAS_CAST_SUBPATH", "SENTINEL_CAST_DIR_q4/")
    monkeypatch.setenv("HANOVA_NAS_STREAM", "1")
    monkeypatch.setenv("HA_URL", "http://ha.example.invalid:8123")
    monkeypatch.setenv("HA_TOKEN", "tok")
    monkeypatch.setenv("HANOVA_HA_SCRIPT_VIDEO_URL", "tv_show_video_url")
    monkeypatch.setenv("HANOVA_CAST_ENTITY", "media_player.example_tv")
    monkeypatch.setenv("HANOVA_CAST_CONFIRM_S", "0")
    monkeypatch.setenv("HANOVA_MEDIA_HTTP_BASE", ROBOT)
    monkeypatch.delenv("HANOVA_NAS_HTTP_BASE", raising=False)
    casts: List[Dict[str, Any]] = []

    async def fake_run_script(script_name: str, data: Dict[str, Any], timeout_s: float = 60.0) -> Dict[str, Any]:
        casts.append(data)
        return {"ok": True, "result": []}

    monkeypatch.setattr(nas, "ha_run_script", fake_run_script)
    return casts


def _probe(monkeypatch: pytest.MonkeyPatch, answer: bool) -> List[str]:
    asked: List[str] = []

    async def fake_serves(url: str) -> bool:
        asked.append(url)
        return answer

    monkeypatch.setattr(nas, "nas_serves", fake_serves)
    return asked


@pytest.mark.asyncio
async def test_without_the_base_the_robot_serves_the_clip_exactly_as_before(monkeypatch, configured):
    """Unset: no probe, and the TV gets the robot's stream URL."""
    asked = _probe(monkeypatch, True)
    out = await nas.stage_and_cast(dict(VIDEO), None)
    assert out["ok"] is True
    assert configured[0]["url"].startswith(f"{ROBOT}/hanova-media/nas-stream/")
    assert asked == []
    assert nas.nas_http_url(VIDEO["cast_path"]) is None


@pytest.mark.asyncio
async def test_with_the_base_and_the_nas_answering_the_tv_gets_the_nas_url(monkeypatch, configured):
    """Each segment below the cast folder is percent-encoded: spaces, `&` and CJK."""
    monkeypatch.setenv("HANOVA_NAS_HTTP_BASE", NAS_BASE + "/")
    asked = _probe(monkeypatch, True)
    out = await nas.stage_and_cast(dict(VIDEO), None)
    assert out["ok"] is True and out["url"] == ENCODED
    assert asked == [ENCODED]
    assert configured[0]["url"] == ENCODED and configured[0]["title"] == out["title"]


@pytest.mark.asyncio
async def test_a_skip_uses_the_nas_url_too(monkeypatch, configured):
    """`nas_skip`'s no-confirm path takes the same route."""
    monkeypatch.setenv("HANOVA_NAS_HTTP_BASE", NAS_BASE)
    _probe(monkeypatch, True)
    out = await nas.stage_and_cast(dict(VIDEO), None, confirm_cast=False)
    assert out["ok"] is True and configured[0]["url"] == ENCODED


@pytest.mark.asyncio
async def test_the_cast_is_still_confirmed_on_the_nas_route(monkeypatch, configured):
    """Dispatch is not display: a dark TV is still `tv_not_responding`."""
    monkeypatch.setenv("HANOVA_NAS_HTTP_BASE", NAS_BASE)
    monkeypatch.setenv("HANOVA_CAST_CONFIRM_S", "5")
    _probe(monkeypatch, True)

    async def unconfirmed(entity_id: str, *, timeout_s: float) -> Dict[str, Any]:
        return {"confirmed": False, "state": "off"}

    monkeypatch.setattr(nas, "confirm_cast_started", unconfirmed)
    out = await nas.stage_and_cast(dict(VIDEO), None)
    assert out["ok"] is False and out["status"] == "tv_not_responding"


@pytest.mark.asyncio
async def test_when_the_nas_does_not_answer_the_robot_serves_the_clip(monkeypatch, configured):
    """A failed probe falls back to the stream route, so the clip still plays."""
    monkeypatch.setenv("HANOVA_NAS_HTTP_BASE", NAS_BASE)
    asked = _probe(monkeypatch, False)
    out = await nas.stage_and_cast(dict(VIDEO), None)
    assert out["ok"] is True and asked == [ENCODED]
    assert configured[0]["url"].startswith(f"{ROBOT}/hanova-media/nas-stream/")


@pytest.mark.asyncio
async def test_the_staged_route_is_the_fallback_when_streaming_is_off(monkeypatch, configured, tmp_path):
    """With `HANOVA_NAS_STREAM=0` the fallback copies as before; with the NAS answering nothing is copied."""
    monkeypatch.setenv("HANOVA_NAS_STREAM", "0")
    monkeypatch.setenv("HANOVA_NAS_HTTP_BASE", NAS_BASE)
    fetched: List[str] = []
    monkeypatch.setattr(nas, "fetch_cast_file", lambda cast_path, dest: (fetched.append(cast_path), dest.write_bytes(b"MP4")))
    _probe(monkeypatch, True)
    await nas.stage_and_cast(dict(VIDEO), tmp_path)
    assert fetched == [] and configured[-1]["url"] == ENCODED
    _probe(monkeypatch, False)
    await nas.stage_and_cast(dict(VIDEO), tmp_path)
    assert fetched == [VIDEO["cast_path"]]
    assert configured[-1]["url"].startswith(f"{ROBOT}/hanova-media/nas/")


@pytest.mark.asyncio
async def test_a_bad_index_path_is_refused_before_any_probe(monkeypatch, configured):
    """The NAS URL is built only from a validated path."""
    monkeypatch.setenv("HANOVA_NAS_HTTP_BASE", NAS_BASE)
    asked = _probe(monkeypatch, True)
    out = await nas.stage_and_cast({**VIDEO, "cast_path": "SENTINEL_CAST_DIR_q4/../elsewhere/x.mp4"}, None)
    assert out["ok"] is False and asked == [] and configured == []


# --- the probe itself ----------------------------------------------------------
def _transport(monkeypatch: pytest.MonkeyPatch, handler) -> List[httpx.Request]:
    seen: List[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        nas.httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(record), **kwargs)
    )
    return seen


@pytest.mark.asyncio
@pytest.mark.parametrize("status, ok", [(200, True), (206, True), (404, False), (403, False), (301, False)])
async def test_the_probe_accepts_only_a_served_clip(monkeypatch, status, ok):
    """HEAD 200 or 206 means the NAS serves it; anything else falls back."""
    seen = _transport(monkeypatch, lambda request: httpx.Response(status))
    assert await nas.nas_serves(ENCODED) is ok
    assert [request.method for request in seen] == ["HEAD"]


@pytest.mark.asyncio
async def test_a_server_that_refuses_head_is_asked_for_one_byte(monkeypatch):
    """405 to HEAD: a GET with `Range: bytes=0-0` answers the same question."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(405)
        return httpx.Response(206, content=b"x")

    seen = _transport(monkeypatch, handler)
    assert await nas.nas_serves(ENCODED) is True
    assert [request.method for request in seen] == ["HEAD", "GET"]
    assert seen[1].headers["range"] == "bytes=0-0"


@pytest.mark.asyncio
async def test_an_unreachable_nas_falls_back_and_logs_no_address(monkeypatch, caplog):
    """A connect failure is a warning in shape only: no host, no folder."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused to nas.example.invalid", request=request)

    _transport(monkeypatch, handler)
    caplog.set_level(logging.WARNING)
    assert await nas.nas_serves(ENCODED) is False
    assert "NAS web server did not answer" in caplog.text
    assert "nas.example.invalid" not in caplog.text and "Trip" not in caplog.text


@pytest.mark.asyncio
async def test_a_slow_nas_falls_back_within_the_probe_budget(monkeypatch):
    """The probe is bounded, so a stalled NAS costs about two seconds at most."""
    import asyncio

    async def stalled(url: str) -> int:
        await asyncio.sleep(10)
        return 200

    monkeypatch.setattr(nas, "_probe_nas_http", stalled)
    monkeypatch.setattr(nas, "_NAS_HTTP_PROBE_S", 0.05)
    assert await nas.nas_serves(ENCODED) is False


def test_the_setting_is_read_without_a_trailing_slash(monkeypatch):
    """`HANOVA_NAS_HTTP_BASE` is the base; the join adds exactly one slash."""
    monkeypatch.setenv("HANOVA_NAS_HTTP_BASE", NAS_BASE + "//")
    assert settings.nas_http_base() == NAS_BASE
    assert nas.nas_http_url(VIDEO["cast_path"]) == ENCODED
