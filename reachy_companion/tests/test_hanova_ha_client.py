"""Contract tests for the async Home Assistant REST helper (D-018)."""

import httpx
import pytest

from reachy_companion.hanova import ha_client


class _FakeResponse:
    """Minimal stand-in for the httpx response Home Assistant returns."""

    def __init__(self, status_code: int = 200, payload: object = None) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> object:
        if self._payload is None:
            raise ValueError("no json body")
        return self._payload


@pytest.fixture(autouse=True)
def ha_env(monkeypatch):
    """Provide the HA config the client reads at call time."""
    monkeypatch.setenv("HA_URL", "http://ha.example.invalid:8123")
    monkeypatch.setenv("HA_TOKEN", "tok")


@pytest.mark.asyncio
async def test_call_service_posts_to_the_service_url(monkeypatch):
    """A service call is POST /api/services/<domain>/<service> with a bearer."""
    seen = {}

    async def fake_request(self, method, url, json=None, headers=None, **kw):
        seen.update(method=method, url=url, json=json, headers=headers)
        return _FakeResponse(200, [])

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)
    out = await ha_client.ha_call_service("media_player", "media_stop", {"entity_id": "media_player.tv"})
    assert out["ok"] is True
    assert seen["method"] == "POST"
    assert seen["url"] == "http://ha.example.invalid:8123/api/services/media_player/media_stop"
    assert seen["json"] == {"entity_id": "media_player.tv"}
    assert seen["headers"]["Authorization"] == "Bearer tok"


@pytest.mark.asyncio
async def test_run_script_targets_the_script_domain(monkeypatch):
    """Casting goes through HA scripts, so the path must be the script domain."""
    seen = {}

    async def fake_request(self, method, url, json=None, headers=None, **kw):
        seen["url"] = url
        seen["json"] = json
        return _FakeResponse(200, [])

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)
    out = await ha_client.ha_run_script("tv_show_youtube", {"youtube_id": "abc"})
    assert out["ok"] is True
    assert seen["url"] == "http://ha.example.invalid:8123/api/services/script/tv_show_youtube"
    assert seen["json"] == {"youtube_id": "abc"}


@pytest.mark.asyncio
async def test_get_state_reads_the_states_endpoint(monkeypatch):
    """State reads are GET /api/states/<entity_id>."""
    seen = {}

    async def fake_request(self, method, url, json=None, headers=None, **kw):
        seen.update(method=method, url=url, json=json)
        return _FakeResponse(200, {"state": "playing"})

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)
    out = await ha_client.ha_get_state("media_player.tv")
    assert out == {"ok": True, "result": {"state": "playing"}}
    assert seen["method"] == "GET"
    assert seen["url"] == "http://ha.example.invalid:8123/api/states/media_player.tv"
    assert seen["json"] is None


@pytest.mark.asyncio
async def test_non_2xx_is_a_result_not_an_exception(monkeypatch):
    """HA errors must reach the model as tool output, never as a raise."""

    async def fake_request(self, method, url, json=None, headers=None, **kw):
        return _FakeResponse(500, None)

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)
    out = await ha_client.ha_call_service("script", "turn_on", {})
    assert out["ok"] is False
    assert out["status_code"] == 500


@pytest.mark.asyncio
async def test_transport_error_is_a_result_not_an_exception(monkeypatch):
    """Off the home LAN this is the normal failure; it must be reported.

    Finding 7: reported as a *shape*. An httpx error string embeds the full URL,
    so the house's LAN address would otherwise travel into the tool result and
    into the model's transcript.
    """

    async def fake_request(self, method, url, json=None, headers=None, **kw):
        raise httpx.ConnectError("no route to host 10.11.12.13")

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)
    out = await ha_client.ha_call_service("script", "turn_on", {})
    assert out["ok"] is False
    assert "ConnectError" in out["error"]
    assert "10.11.12.13" not in out["error"]


@pytest.mark.asyncio
async def test_missing_config_is_reported_without_a_request(monkeypatch):
    """No HA_URL means no socket work at all."""

    async def fail_request(self, *args, **kwargs):
        raise AssertionError("ha_client must not call HA when it is unconfigured")

    monkeypatch.delenv("HA_URL")
    monkeypatch.setattr(httpx.AsyncClient, "request", fail_request)
    out = await ha_client.ha_call_service("script", "turn_on", {})
    assert out["ok"] is False
    assert "HA_URL" in out["error"]


@pytest.mark.asyncio
async def test_empty_body_is_still_ok(monkeypatch):
    """HA answers some service calls with no JSON body; that is success."""

    async def fake_request(self, method, url, json=None, headers=None, **kw):
        return _FakeResponse(200, None)

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)
    assert await ha_client.ha_call_service("script", "turn_on", {}) == {"ok": True, "result": None}


@pytest.mark.asyncio
async def test_the_ha_client_logs_no_script_name_url_or_error_body(monkeypatch, caplog):
    """Round 3, finding 3: the HA seam needs a caplog sentinel like every other.

    `test_each_service_seam_has_a_caplog_sentinel_test` in Task 14 requires one
    behavioural test per service seam, and `ha_client` was the seam without one --
    which matters more here than anywhere else, because the script name IS the
    operator's `scripts.yaml` entry and the URL IS the house's LAN address. Both
    failure branches are exercised: the transport error and the non-2xx.
    """
    import logging

    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("HA_URL", "http://SENTINEL_PRIVATE_x7.invalid:8123")

    async def transport_error(self, method, url, json=None, headers=None, **kw):
        raise httpx.ConnectError(f"no route to host SENTINEL_PRIVATE_x7 via {url}")

    monkeypatch.setattr(httpx.AsyncClient, "request", transport_error)
    failed = await ha_client.ha_run_script("SENTINEL_PRIVATE_x7", {"note": "SENTINEL_PRIVATE_x7"})
    assert failed["ok"] is False
    assert "SENTINEL_PRIVATE_x7" not in failed["error"]

    async def server_error(self, method, url, json=None, headers=None, **kw):
        return _FakeResponse(500, None)

    monkeypatch.setattr(httpx.AsyncClient, "request", server_error)
    refused = await ha_client.ha_run_script("SENTINEL_PRIVATE_x7", {})
    assert refused["ok"] is False
    assert "SENTINEL_PRIVATE_x7" not in refused["error"]

    assert "SENTINEL_PRIVATE_x7" not in caplog.text
