"""Contract tests for the async Home Assistant REST helper (D-018)."""

import logging

import httpx
import pytest

from reachy_companion.hanova import ha_client


class _Capture(logging.Handler):
    """Collect every formatted message that reaches the root logger.

    Used instead of `caplog` for the httpx-logger test only: that test calls the
    production `setup_logger`, whose `logging.basicConfig(force=True)` removes
    the root handlers -- caplog's included. A test that silently lost its own
    capture handler would pass no matter how badly httpx leaked, so this one owns
    a handler it attaches after `setup_logger` has run.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.NOTSET)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        """Record one message."""
        self.messages.append(record.getMessage())


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


@pytest.fixture
def restore_logging():
    """Snapshot global logging state around a test that calls `setup_logger`.

    `setup_logger` runs `logging.basicConfig(force=True)`, which detaches (and
    closes) every root handler and rewrites the root level. Without this the one
    test that exercises it would leave the rest of the suite logging into
    nothing.
    """
    root = logging.getLogger()
    saved = (list(root.handlers), root.level, logging.getLogger("httpx").level)
    try:
        yield
    finally:
        root.handlers[:] = saved[0]
        root.setLevel(saved[1])
        logging.getLogger("httpx").setLevel(saved[2])


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
async def test_a_non_serializable_payload_is_a_result_not_a_raise():
    """Review finding 3: `except httpx.HTTPError` was narrower than the contract.

    `data` reaches this module straight from a model tool call, so a value
    `json.dumps` cannot encode is reachable without any network involvement at
    all -- httpx raises `TypeError` while *building* the request, which is not an
    `HTTPError`, so it escaped the guard and propagated into the tool dispatcher
    instead of reaching the model as tool output.
    """
    out = await ha_client.ha_call_service("script", "turn_on", {"handle": object()})
    assert out["ok"] is False
    assert "TypeError" in out["error"]


@pytest.mark.asyncio
async def test_path_segments_are_percent_encoded(monkeypatch):
    """Review finding 4: an unencoded segment lets a value re-target the request.

    `entity_id` is model-supplied. Interpolated raw, `a/b?c` would become a
    different path with a query string attached; encoded, it stays exactly one
    path segment of `/api/states/`.
    """
    seen = {}

    async def fake_request(self, method, url, json=None, headers=None, **kw):
        seen["url"] = url
        return _FakeResponse(200, {"state": "on"})

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)
    out = await ha_client.ha_get_state("a/b?c")
    assert out["ok"] is True
    assert seen["url"] == "http://ha.example.invalid:8123/api/states/a%2Fb%3Fc"


@pytest.mark.asyncio
async def test_ordinary_ids_survive_encoding_unchanged(monkeypatch):
    """The encoding must not mangle the ids the cast scripts actually receive."""
    seen = {}

    async def fake_request(self, method, url, json=None, headers=None, **kw):
        seen["url"] = url
        return _FakeResponse(200, {})

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)
    await ha_client.ha_get_state("media_player.living_room-tv")
    assert seen["url"].endswith("/api/states/media_player.living_room-tv")


@pytest.mark.asyncio
async def test_httpxs_own_logger_never_prints_the_ha_url(monkeypatch, restore_logging):
    """Review finding 1: the leak was httpx's logger, not one of ours.

    `hanova/redact.py` can only govern messages this codebase formats. httpx logs
    `HTTP Request: POST <full url> "HTTP/1.1 200 OK"` at INFO from inside its own
    `_send_single_request`, so the house's Home Assistant address -- and the
    operator's `scripts.yaml` entry name, which is the last path segment -- went
    into the log through a channel no redactor ever sees. `utils.setup_logger`
    now pins that logger to WARNING.

    httpx's real client code has to run for the record to exist, so this drives a
    `MockTransport` rather than stubbing `AsyncClient.request` the way the other
    tests here do -- stubbing the method would skip the very line under test. The
    control phase proves the harness can still see the leak; without it a broken
    capture would make the second half pass vacuously.
    """
    from reachy_companion.utils import setup_logger

    monkeypatch.setenv("HA_URL", "http://SENTINEL_PRIVATE_x7.invalid:8123")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    real_client = httpx.AsyncClient

    def mock_transport_client(**kwargs):
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", mock_transport_client)
    root = logging.getLogger()

    # --- control: with httpx at INFO the URL really does reach a handler ---
    root.setLevel(logging.INFO)
    logging.getLogger("httpx").setLevel(logging.INFO)
    control = _Capture()
    root.addHandler(control)
    try:
        leaked = await ha_client.ha_run_script("SENTINEL_PRIVATE_x7", {})
    finally:
        root.removeHandler(control)
    assert leaked["ok"] is True
    assert any("SENTINEL_PRIVATE_x7" in message for message in control.messages), (
        "control phase failed: this test cannot observe the leak it exists to prevent"
    )

    # --- production: setup_logger silences it, in DEBUG as well as INFO ---
    setup_logger(debug=False)
    assert logging.getLogger("httpx").level == logging.WARNING
    setup_logger(debug=True)
    assert logging.getLogger("httpx").level == logging.WARNING

    tamed = _Capture()
    root.addHandler(tamed)
    try:
        out = await ha_client.ha_run_script("SENTINEL_PRIVATE_x7", {})
    finally:
        root.removeHandler(tamed)
    assert out["ok"] is True
    assert not any("SENTINEL_PRIVATE_x7" in message for message in tamed.messages)


@pytest.mark.asyncio
async def test_the_ha_client_logs_no_script_name_url_or_error_body(monkeypatch, caplog):
    """Round 3, finding 3: the HA seam needs a caplog sentinel like every other.

    `test_each_service_seam_has_a_caplog_sentinel_test` in Task 14 requires one
    behavioural test per service seam, and `ha_client` was the seam without one --
    which matters more here than anywhere else, because the script name IS the
    operator's `scripts.yaml` entry and the URL IS the house's LAN address. Both
    failure branches are exercised: the transport error and the non-2xx.
    """
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
