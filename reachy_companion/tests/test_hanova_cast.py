"""Contract tests for TV casting: play_video and show_on_tv (D-018, R2/R4/R5)."""

import types
import base64
import importlib
from typing import Any

import pytest

from reachy_companion import home_net
from reachy_companion.hanova import images, settings
from reachy_companion.tools.play_video import PlayVideo
from reachy_companion.tools.show_on_tv import ShowOnTv


PNG_BYTES = b"\x89PNG\r\n\x1a\nfake"

_PLAY_VIDEO = "reachy_companion.tools.play_video"
_SHOW_ON_TV = "reachy_companion.tools.show_on_tv"


def _deps(tmp_path):
    return types.SimpleNamespace(reachy_mini=None, instance_path=tmp_path)


class _FakeImagesApi:
    def __init__(self, payload: Any = None, error: Exception | None = None) -> None:
        self._payload = payload
        self._error = error
        self.seen: dict[str, Any] = {}

    async def generate(self, **kwargs: Any) -> Any:
        self.seen.update(kwargs)
        if self._error is not None:
            raise self._error
        return self._payload


class _FakeOpenAI:
    """Stands in for AsyncOpenAI, and records that it was actually closed."""

    def __init__(self, images_api: "_FakeImagesApi") -> None:
        self.images = images_api
        self.closed = False

    async def __aenter__(self) -> "_FakeOpenAI":
        return self

    async def __aexit__(self, *_exc: Any) -> bool:
        self.closed = True
        return False


def _fake_openai(payload: Any = None, error: Exception | None = None):
    api = _FakeImagesApi(payload, error)
    return _FakeOpenAI(api), api


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    """Media-cast configured, robot at home, no real network anywhere."""
    # Bind the tool classes to whichever module object is live in THIS test.
    # `test_external_loading.py` and `test_tool_space_runtime.py` pop every
    # `reachy_companion.tools.*` entry out of `sys.modules`, so the next import of
    # a tool module executes a second copy of it. The class imported at collection
    # would then keep the first copy's globals while every patch below -- they all
    # name their target by string -- landed on the second, leaving `home_state`
    # the *real* one: the gating tests here would run a live network probe instead
    # of the verdict they set, and still look green on the weaker assertions.
    # Re-importing first makes class and patch target the same object again.
    # `test_home_control.py` documents the same hazard from the other side.
    monkeypatch.setitem(globals(), "PlayVideo", importlib.import_module(_PLAY_VIDEO).PlayVideo)
    monkeypatch.setitem(globals(), "ShowOnTv", importlib.import_module(_SHOW_ON_TV).ShowOnTv)
    monkeypatch.setattr("reachy_companion.hanova.settings._music_wheels_ready", lambda: (True, ""))
    monkeypatch.setenv("HA_URL", "http://ha.example.invalid:8123")
    monkeypatch.setenv("HA_TOKEN", "tok")
    monkeypatch.setenv("HANOVA_HA_SCRIPT_YOUTUBE", "tv_show_youtube")
    monkeypatch.setenv("HANOVA_HA_SCRIPT_IMAGE_URL", "tv_show_image_url")
    monkeypatch.setenv("HANOVA_CAST_ENTITY", "media_player.example_tv")
    monkeypatch.setenv("HANOVA_MEDIA_HTTP_BASE", "http://robot.example.invalid:7860")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    settings.set_media_mount_ready(True)
    home_net.reset_cache()

    async def always_home() -> str:
        return home_net.HOME

    monkeypatch.setattr("reachy_companion.tools.play_video.home_state", always_home)
    monkeypatch.setattr("reachy_companion.tools.show_on_tv.home_state", always_home)
    yield
    settings.set_media_mount_ready(False)
    home_net.reset_cache()


def _home_state(monkeypatch, module: str, verdict: str) -> None:
    async def fixed() -> str:
        return verdict

    monkeypatch.setattr(f"reachy_companion.tools.{module}.home_state", fixed)


def test_tool_names_match_their_filenames():
    """The loader resolves tools by filename == Tool.name."""
    assert PlayVideo.name == "play_video"
    assert ShowOnTv.name == "show_on_tv"


def test_descriptions_carry_no_personal_identifier():
    """R10: no entity id, address, folder id or owner name in a description."""
    for text in (PlayVideo().description, ShowOnTv().description):
        assert "@" not in text
        assert "media_player." not in text
        assert len(text) <= 120


@pytest.mark.asyncio
async def test_play_video_is_unavailable_without_its_ha_script(monkeypatch, tmp_path):
    """Finding 6: the script name has no default, so an unset one disables the tool."""
    monkeypatch.delenv("HANOVA_HA_SCRIPT_YOUTUBE")
    out = await PlayVideo()(deps=_deps(tmp_path), query="a documentary")
    assert out == {"status": "unavailable", "reason": "HANOVA_HA_SCRIPT_YOUTUBE"}


@pytest.mark.asyncio
async def test_play_video_does_not_need_the_lan_base_or_the_mount(monkeypatch, tmp_path):
    """Finding 10: this path hands HA an id; it serves no bytes of its own."""
    import reachy_companion.tools.play_video as play_video_module

    monkeypatch.delenv("HANOVA_MEDIA_HTTP_BASE")
    settings.set_media_mount_ready(False)
    monkeypatch.setattr(
        play_video_module.ytdlp,
        "search",
        lambda query, max_duration_s=None: {"ok": True, "id": "vid123", "title": "A Film", "error": None},
    )

    async def fake_run_script(script_name, data, timeout_s=60.0):
        return {"ok": True, "result": []}

    monkeypatch.setattr(play_video_module, "ha_run_script", fake_run_script)
    out = await PlayVideo()(deps=_deps(tmp_path), query="a documentary")
    assert out["ok"] is True


@pytest.mark.asyncio
async def test_play_video_is_away_from_home_off_the_lan(monkeypatch, tmp_path):
    """R4: house-bound tools say where they are, they do not blame the house."""
    _home_state(monkeypatch, "play_video", home_net.AWAY)
    out = await PlayVideo()(deps=_deps(tmp_path), query="a documentary")
    assert out == {"status": "away_from_home"}


@pytest.mark.asyncio
async def test_play_video_does_no_work_when_the_home_verdict_is_unknown(monkeypatch, tmp_path):
    """Round 2, finding 3: UNKNOWN is not permission, and it is not absence.

    Round 1 only branched on AWAY, so this path fell through and cast anyway.
    The answer must be its own status, and nothing may happen.
    """
    import reachy_companion.tools.play_video as play_video_module

    _home_state(monkeypatch, "play_video", home_net.UNKNOWN)

    def fail_search(query, max_duration_s=None):
        raise AssertionError("play_video must not resolve anything on UNKNOWN")

    async def fail_run_script(script_name, data, timeout_s=60.0):
        raise AssertionError("play_video must not touch Home Assistant on UNKNOWN")

    monkeypatch.setattr(play_video_module.ytdlp, "search", fail_search)
    monkeypatch.setattr(play_video_module, "ha_run_script", fail_run_script)
    out = await PlayVideo()(deps=_deps(tmp_path), query="a documentary")
    assert out["status"] == "home_status_unknown"
    assert out["status"] != "away_from_home"
    assert out["error"]


# --- the six no-side-effect cases (round 2, finding 3) --------------------
#
# These drive the REAL `home_net.home_state()` through its own seams rather than
# stubbing the tool's verdict, so they prove the whole chain: probe outcome ->
# verdict -> tool behaviour. In every one of the six, the house action must not
# happen. `HANOVA_HOME_NETWORKS` is left unset, which is the deployment default
# and the case in which `AWAY` is unprovable.


@pytest.fixture
def house_probe(monkeypatch):
    """Wire play_video to the real probe and record whether HA was touched."""
    import reachy_companion.tools.play_video as play_video_module

    touched: list[str] = []

    def fail_search(query, max_duration_s=None):
        touched.append("search")
        raise AssertionError("no search may run when the robot cannot confirm it is home")

    async def fail_run_script(script_name, data, timeout_s=60.0):
        touched.append("cast")
        raise AssertionError("no HA script may run when the robot cannot confirm it is home")

    monkeypatch.setattr(play_video_module.ytdlp, "search", fail_search)
    monkeypatch.setattr(play_video_module, "ha_run_script", fail_run_script)
    monkeypatch.setattr(play_video_module, "home_state", home_net.home_state)
    monkeypatch.delenv("HANOVA_HOME_NETWORKS", raising=False)
    home_net.reset_cache()
    yield touched
    home_net.reset_cache()


def _lan(monkeypatch, *, reachable=True, same_subnet=True, local="203.0.113.20"):
    async def probe(host, port, timeout_s):
        return home_net.LanProbe(
            reachable=reachable,
            local_address=local if reachable else "",
            same_subnet=same_subnet,
        )

    async def resolve(host, timeout_s):
        return local if reachable else ""

    monkeypatch.setattr(home_net, "lan_signal", probe)
    monkeypatch.setattr(home_net, "local_address", resolve)


def _ha_answers(monkeypatch, status_code=None, error=None):
    import httpx

    async def fake_get(self, url, headers=None, **kw):
        if error is not None:
            raise error
        return type("_R", (), {"status_code": status_code})()

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)


@pytest.mark.asyncio
async def test_a_vpn_reach_does_no_house_work(monkeypatch, tmp_path, house_probe):
    """Reachable from another network: presence is not proven, so do nothing."""
    _lan(monkeypatch, same_subnet=False)
    _ha_answers(monkeypatch, status_code=200)
    out = await PlayVideo()(deps=_deps(tmp_path), query="a documentary")
    assert out["status"] == "home_status_unknown"
    assert house_probe == []


@pytest.mark.asyncio
async def test_an_unauthorized_ha_does_no_house_work(monkeypatch, tmp_path, house_probe):
    """A 401 is an expired token, not a user who left the house."""
    _lan(monkeypatch)
    _ha_answers(monkeypatch, status_code=401)
    out = await PlayVideo()(deps=_deps(tmp_path), query="a documentary")
    assert out["status"] == "home_status_unknown"
    assert house_probe == []


@pytest.mark.asyncio
async def test_a_server_error_from_ha_does_no_house_work(monkeypatch, tmp_path, house_probe):
    """A 5xx is Home Assistant being broken while we sit next to it."""
    _lan(monkeypatch)
    _ha_answers(monkeypatch, status_code=503)
    out = await PlayVideo()(deps=_deps(tmp_path), query="a documentary")
    assert out["status"] == "home_status_unknown"
    assert house_probe == []


@pytest.mark.asyncio
async def test_an_http_timeout_does_no_house_work(monkeypatch, tmp_path, house_probe):
    """The socket connected and then HA went quiet. Still not a location fact."""
    import httpx

    _lan(monkeypatch)
    _ha_answers(monkeypatch, error=httpx.ReadTimeout("timed out"))
    out = await PlayVideo()(deps=_deps(tmp_path), query="a documentary")
    assert out["status"] == "home_status_unknown"
    assert house_probe == []


@pytest.mark.asyncio
async def test_a_dns_failure_does_no_house_work(monkeypatch, tmp_path, house_probe):
    """Round 2, finding 3: name resolution failing is not the user being out."""
    _lan(monkeypatch, reachable=False)
    out = await PlayVideo()(deps=_deps(tmp_path), query="a documentary")
    assert out["status"] == "home_status_unknown"
    assert out["status"] != "away_from_home"
    assert house_probe == []


@pytest.mark.asyncio
async def test_a_refused_connection_does_no_house_work(monkeypatch, tmp_path, house_probe):
    """Round 2, finding 3: a closed port is an HA outage, not absence."""
    _lan(monkeypatch, reachable=False)
    out = await PlayVideo()(deps=_deps(tmp_path), query="a documentary")
    assert out["status"] == "home_status_unknown"
    assert out["status"] != "away_from_home"
    assert house_probe == []


@pytest.mark.asyncio
async def test_a_declared_off_home_address_is_away_and_also_does_no_work(monkeypatch, tmp_path, house_probe):
    """The one case that IS absence: still no side effect, different wording."""
    monkeypatch.setenv("HANOVA_HOME_NETWORKS", "203.0.113.0/24")
    _lan(monkeypatch, reachable=False, local="198.51.100.20")

    async def resolve(host, timeout_s):
        return "198.51.100.20"

    monkeypatch.setattr(home_net, "local_address", resolve)
    out = await PlayVideo()(deps=_deps(tmp_path), query="a documentary")
    assert out == {"status": "away_from_home"}
    assert house_probe == []


@pytest.mark.asyncio
async def test_play_video_casts_the_resolved_youtube_id(monkeypatch, tmp_path):
    """Path A: only an id is handed to HA -- no bytes and no URL of ours."""
    import reachy_companion.tools.play_video as play_video_module

    monkeypatch.setattr(
        play_video_module.ytdlp,
        "search",
        lambda query, max_duration_s=None: {"ok": True, "id": "vid123", "title": "A Film", "error": None},
    )
    seen = {}

    async def fake_run_script(script_name, data, timeout_s=60.0):
        seen["script"] = script_name
        seen["data"] = data
        return {"ok": True, "result": []}

    monkeypatch.setattr(play_video_module, "ha_run_script", fake_run_script)
    out = await PlayVideo()(deps=_deps(tmp_path), query="a documentary")
    assert out["ok"] is True and out["status"] == "casting" and out["title"] == "A Film"
    assert seen["script"] == "tv_show_youtube"
    # Finding 10: the configured cast target is forwarded, not ignored.
    assert seen["data"] == {"youtube_id": "vid123", "entity_id": "media_player.example_tv"}


@pytest.mark.asyncio
async def test_the_cast_entity_is_omitted_when_it_is_not_configured(monkeypatch, tmp_path):
    """It is optional: an HA script with its own target must not get an empty id."""
    import reachy_companion.tools.play_video as play_video_module

    monkeypatch.delenv("HANOVA_CAST_ENTITY")
    monkeypatch.setattr(
        play_video_module.ytdlp,
        "search",
        lambda query, max_duration_s=None: {"ok": True, "id": "vid123", "title": "A Film", "error": None},
    )
    seen = {}

    async def fake_run_script(script_name, data, timeout_s=60.0):
        seen["data"] = data
        return {"ok": True, "result": []}

    monkeypatch.setattr(play_video_module, "ha_run_script", fake_run_script)
    await PlayVideo()(deps=_deps(tmp_path), query="a documentary")
    assert seen["data"] == {"youtube_id": "vid123"}


@pytest.mark.asyncio
async def test_play_video_reports_a_search_failure(monkeypatch, tmp_path):
    """A rate-limited search is a spoken answer, not a stack trace."""
    import reachy_companion.tools.play_video as play_video_module

    monkeypatch.setattr(
        play_video_module.ytdlp,
        "search",
        lambda query, max_duration_s=None: {"ok": False, "id": None, "title": None, "error": "no result"},
    )
    out = await PlayVideo()(deps=_deps(tmp_path), query="nonsense")
    assert out["ok"] is False and out["error"]


@pytest.mark.asyncio
async def test_play_video_surfaces_an_ha_failure(monkeypatch, tmp_path):
    """A missing HA script must be reported, not silently reported as success."""
    import reachy_companion.tools.play_video as play_video_module

    monkeypatch.setattr(
        play_video_module.ytdlp,
        "search",
        lambda query, max_duration_s=None: {"ok": True, "id": "vid123", "title": "A Film", "error": None},
    )

    async def failing_run_script(script_name, data, timeout_s=60.0):
        return {"ok": False, "error": "Home Assistant returned HTTP 400"}

    monkeypatch.setattr(play_video_module, "ha_run_script", failing_run_script)
    out = await PlayVideo()(deps=_deps(tmp_path), query="a documentary")
    assert out["ok"] is False and out["error"]


@pytest.mark.asyncio
async def test_cast_logs_never_carry_the_query(monkeypatch, caplog, tmp_path):
    """Finding 7: what the user asked to watch is not log material."""
    import logging

    import reachy_companion.tools.play_video as play_video_module

    sentinel = "SENTINEL_PRIVATE_x7"
    monkeypatch.setattr(
        play_video_module.ytdlp,
        "search",
        lambda query, max_duration_s=None: {"ok": True, "id": sentinel, "title": sentinel, "error": None},
    )

    async def fake_run_script(script_name, data, timeout_s=60.0):
        return {"ok": True, "result": []}

    monkeypatch.setattr(play_video_module, "ha_run_script", fake_run_script)
    caplog.set_level(logging.DEBUG)
    await PlayVideo()(deps=_deps(tmp_path), query=f"a film about {sentinel}")
    assert sentinel not in caplog.text


@pytest.mark.asyncio
async def test_generate_image_writes_the_decoded_png(monkeypatch, tmp_path):
    """The Images API returns base64; we own decoding and naming."""
    payload = types.SimpleNamespace(data=[types.SimpleNamespace(b64_json=base64.b64encode(PNG_BYTES).decode())])
    client, api = _fake_openai(payload)
    monkeypatch.setattr(images, "build_client", lambda: client)

    out = await images.generate_image("a red bicycle", tmp_path)
    assert out["ok"] is True
    assert out["filename"] is not None and out["filename"].endswith(".png")
    assert (tmp_path / out["filename"]).read_bytes() == PNG_BYTES
    assert api.seen["prompt"] == "a red bicycle"
    assert api.seen["model"] == "gpt-image-1"
    assert client.closed is True, "finding 18: the client must be closed on the success path"


@pytest.mark.asyncio
async def test_the_client_is_closed_even_when_generation_fails(monkeypatch, tmp_path):
    """Finding 18: a leaked connection pool per failed request is a slow leak."""
    client, _ = _fake_openai(error=RuntimeError("rate limited"))
    monkeypatch.setattr(images, "build_client", lambda: client)
    out = await images.generate_image("anything", tmp_path)
    assert out["ok"] is False
    assert client.closed is True


@pytest.mark.asyncio
async def test_generate_image_without_a_key_is_reported(monkeypatch, tmp_path):
    """No OPENAI_API_KEY is a configuration fact, not a crash."""
    monkeypatch.setattr(images, "build_client", lambda: None)
    out = await images.generate_image("anything", tmp_path)
    assert out["ok"] is False and "OPENAI_API_KEY" in out["error"]


@pytest.mark.asyncio
async def test_generate_image_api_error_does_not_echo_the_prompt(monkeypatch, tmp_path):
    """Finding 7: an Images API error body can quote the prompt straight back."""
    sentinel = "SENTINEL_PRIVATE_x7"
    client, _ = _fake_openai(error=RuntimeError(f"rejected prompt: {sentinel}"))
    monkeypatch.setattr(images, "build_client", lambda: client)
    out = await images.generate_image(sentinel, tmp_path)
    assert out["ok"] is False
    assert sentinel not in out["error"]


@pytest.mark.asyncio
async def test_show_on_tv_is_unavailable_without_an_openai_key(monkeypatch, tmp_path):
    """The image half of the capability has its own credential."""
    monkeypatch.delenv("OPENAI_API_KEY")
    out = await ShowOnTv()(deps=_deps(tmp_path), request="draw a cat")
    assert out == {"status": "unavailable", "reason": "OPENAI_API_KEY"}


@pytest.mark.asyncio
async def test_show_on_tv_is_unavailable_when_the_media_mount_failed(tmp_path):
    """Finding 11: without a live route the TV would fetch nothing."""
    settings.set_media_mount_ready(False)
    out = await ShowOnTv()(deps=_deps(tmp_path), request="draw a cat")
    assert out == {"status": "unavailable", "reason": "HANOVA_MEDIA_MOUNT"}


@pytest.mark.asyncio
async def test_show_on_tv_is_away_from_home_off_the_lan(monkeypatch, tmp_path):
    """R4 again: the TV is at home and so is this capability."""
    _home_state(monkeypatch, "show_on_tv", home_net.AWAY)
    out = await ShowOnTv()(deps=_deps(tmp_path), request="draw a cat")
    assert out == {"status": "away_from_home"}


@pytest.mark.asyncio
async def test_show_on_tv_generates_nothing_when_the_home_verdict_is_unknown(monkeypatch, tmp_path):
    """Round 2, finding 3: UNKNOWN must not spend an Images API call either.

    This is the most expensive UNKNOWN fall-through in the port: the round-1
    shape generated a real image, wrote it to disk and only then failed to cast.
    """
    import reachy_companion.tools.show_on_tv as show_on_tv_module

    _home_state(monkeypatch, "show_on_tv", home_net.UNKNOWN)

    async def fail_generate(prompt, dest_dir):
        raise AssertionError("show_on_tv must not call the Images API on UNKNOWN")

    async def fail_run_script(script_name, data, timeout_s=60.0):
        raise AssertionError("show_on_tv must not touch Home Assistant on UNKNOWN")

    monkeypatch.setattr(show_on_tv_module.images, "generate_image", fail_generate)
    monkeypatch.setattr(show_on_tv_module, "ha_run_script", fail_run_script)
    out = await ShowOnTv()(deps=_deps(tmp_path), request="draw a cat")
    assert out["status"] == "home_status_unknown"
    assert out["status"] != "away_from_home"


@pytest.mark.asyncio
async def test_the_images_layer_logs_no_prompt_or_path(monkeypatch, caplog, tmp_path):
    """Round 2, finding 6: images.py is a service seam and logs like one.

    The write-failure branch used to interpolate the OSError, which renders the
    instance-directory path it failed to write into.
    """
    import logging

    caplog.set_level(logging.DEBUG)
    sentinel = "SENTINEL_PRIVATE_x7"

    client, _ = _fake_openai(error=RuntimeError(f"rejected prompt: {sentinel}"))
    monkeypatch.setattr(images, "build_client", lambda: client)
    out = await images.generate_image(sentinel, tmp_path)
    assert out["ok"] is False
    assert sentinel not in caplog.text
    assert sentinel not in str(out["error"])

    # And the write-failure branch, which used to interpolate the OSError -- an
    # OSError renders the full path it failed on.
    from pathlib import Path as _Path

    def unwritable(self, _data):
        raise OSError(f"read-only filesystem at {sentinel}")

    ok_client, _ = _fake_openai(
        payload=types.SimpleNamespace(data=[types.SimpleNamespace(b64_json=base64.b64encode(PNG_BYTES).decode())])
    )
    monkeypatch.setattr(images, "build_client", lambda: ok_client)
    monkeypatch.setattr(_Path, "write_bytes", unwritable)
    out = await images.generate_image("a cat", tmp_path)
    assert out["ok"] is False
    assert sentinel not in caplog.text
    assert sentinel not in str(out["error"])


@pytest.mark.asyncio
async def test_show_on_tv_generates_serves_and_casts(monkeypatch, tmp_path):
    """End to end: generated PNG lands in the cache and its LAN URL is cast."""
    import reachy_companion.tools.show_on_tv as show_on_tv_module

    async def fake_generate(prompt, dest_dir):
        (dest_dir / "img_abc.png").write_bytes(PNG_BYTES)
        return {"ok": True, "path": str(dest_dir / "img_abc.png"), "filename": "img_abc.png", "error": None}

    seen = {}

    async def fake_run_script(script_name, data, timeout_s=60.0):
        seen["script"] = script_name
        seen["data"] = data
        return {"ok": True, "result": []}

    monkeypatch.setattr(show_on_tv_module.images, "generate_image", fake_generate)
    monkeypatch.setattr(show_on_tv_module, "ha_run_script", fake_run_script)

    out = await ShowOnTv()(deps=_deps(tmp_path), request="draw a cat")
    assert out["ok"] is True and out["status"] == "casting"
    assert seen["script"] == "tv_show_image_url"
    assert seen["data"]["url"] == "http://robot.example.invalid:7860/hanova-media/images/img_abc.png"
    assert seen["data"]["media_type"] == "image/png"
    assert seen["data"]["entity_id"] == "media_player.example_tv"
    # Finding 7: the request must not travel into Home Assistant's logbook.
    assert "title" not in seen["data"]


@pytest.mark.asyncio
async def test_show_on_tv_rejects_an_empty_request(tmp_path):
    """An empty prompt must not reach the Images API."""
    out = await ShowOnTv()(deps=_deps(tmp_path), request="   ")
    assert out["ok"] is False


def test_both_tools_reach_the_model_session():
    """The locked profile must list them, or the model never sees them."""
    core_tools = importlib.import_module("reachy_companion.tools.core_tools")
    core_tools.initialize_tools(force=True)
    try:
        names = {spec["name"] for spec in core_tools.get_tool_specs()}
        assert {"play_video", "show_on_tv"} <= names
    finally:
        core_tools._TOOLS_SIGNATURE = None
