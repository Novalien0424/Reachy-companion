"""Contract tests for the Range-capable NAS stream endpoint (latency work, 2026-08-22).

No SMB and no NAS: `smbclient` is a fake module installed into `sys.modules`,
exactly as `test_hanova_nas.py` does it, and every identifier here is a
synthetic `SENTINEL_*_q4` token so the repository never carries a share, a
folder or a host that resembles the operator's own.

What these pin: the three things a Chromecast's default receiver needs
(`Accept-Ranges`, `Content-Length`, `206` + `Content-Range`), the shapes it can
send that must not crash the handler, the bounded registry, and the switch --
streaming casts a `/hanova-media/nas-stream/` URL and fetches nothing, while
`HANOVA_NAS_STREAM=0` restores the staged copy untouched.
"""

import sys
import json
import types
import logging
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from reachy_companion.hanova import nas, settings, nas_stream, media_store


CAST_PATH = "SENTINEL_CAST_DIR_q4/SENTINEL_TRIP_q4/clip01.mp4"
SOURCE_PATH = "SENTINEL_SRC_DIR_q4/SENTINEL_TRIP_q4/clip01.mp4"
SENTINEL_HOST = "SENTINEL_HOST_q4.invalid"

PAYLOAD = bytes(range(256)) * 8  # 2048 bytes, every value distinct per block

VIDEO = {
    "path": SOURCE_PATH,
    "cast_path": CAST_PATH,
    "cast_ready": True,
    "year": 2019,
    "place": "SENTINEL_PLACE_q4",
    "label": "morning",
    "top_folder": "SENTINEL_TRIP_q4",
    "name": "clip01",
    "seq": 1,
}


@pytest.fixture(autouse=True)
def configured(monkeypatch, tmp_path):
    """Configure the NAS family, a LAN base URL, and an empty stream registry."""
    index_path = tmp_path / "nas-video-index.json"
    index_path.write_text(json.dumps({"folders": {}, "videos": [VIDEO]}), encoding="utf-8")
    monkeypatch.setenv("HANOVA_NAS_HOST", SENTINEL_HOST)
    monkeypatch.setenv("HANOVA_NAS_USER", "u")
    monkeypatch.setenv("HANOVA_NAS_PASSWORD", "p")
    monkeypatch.setenv("HANOVA_NAS_SHARE", "SENTINEL_SHARE_q4")
    monkeypatch.setenv("HANOVA_NAS_SUBPATH", "SENTINEL_SRC_DIR_q4")
    monkeypatch.setenv("HANOVA_NAS_CAST_SUBPATH", "SENTINEL_CAST_DIR_q4")
    monkeypatch.setenv("HANOVA_NAS_INDEX_PATH", str(index_path))
    monkeypatch.setenv("HANOVA_MEDIA_HTTP_BASE", "http://robot.example.invalid:7860")
    monkeypatch.setenv("HANOVA_HA_SCRIPT_VIDEO_URL", "tv_show_video_url")
    monkeypatch.setenv("HANOVA_CAST_ENTITY", "media_player.example_tv")
    # Cast confirmation off by default: these are dispatch-contract tests.
    # The confirmation behavior has its own tests (2026-08-24).
    monkeypatch.setenv("HANOVA_CAST_CONFIRM_S", "0")
    monkeypatch.delenv("HANOVA_MEDIA_DIR", raising=False)
    monkeypatch.delenv("HANOVA_NAS_STREAM", raising=False)
    settings.set_media_mount_ready(True)
    nas_stream._REGISTRY.clear()
    yield
    nas_stream._REGISTRY.clear()
    settings.set_media_mount_ready(False)


class _FakeHandle:
    """The subset of an `smbclient` file object the streamer actually uses."""

    def __init__(self, data: bytes, opened: list[str], remote: str) -> None:
        self._data = data
        self._offset = 0
        opened.append(remote)

    def __enter__(self) -> "_FakeHandle":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def seek(self, offset: int) -> None:
        self._offset = offset

    def read(self, size: int) -> bytes:
        chunk = self._data[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk


def _fake_smbclient(monkeypatch, *, data: bytes = PAYLOAD, stat_raises: bool = False):
    """Install a fake `smbclient` and return what it was asked to do."""
    recorded: dict[str, Any] = {"sessions": [], "stats": [], "opened": []}

    module = types.ModuleType("smbclient")

    def register_session(host, username=None, password=None, connection_timeout=None):
        recorded["sessions"].append((host, connection_timeout))

    def stat(remote):
        recorded["stats"].append(remote)
        if stat_raises:
            raise OSError(f"cannot reach \\\\{SENTINEL_HOST}\\SENTINEL_SHARE_q4")
        return types.SimpleNamespace(st_size=len(data))

    def open_file(remote, mode="rb"):
        return _FakeHandle(data, recorded["opened"], remote)

    module.register_session = register_session  # type: ignore[attr-defined]
    module.stat = stat  # type: ignore[attr-defined]
    module.open_file = open_file  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "smbclient", module)
    return recorded


def _client(tmp_path) -> TestClient:
    """Mount the media routes on a real app, the way `console.py` does."""
    app = FastAPI()
    assert media_store.mount_media_routes(app, tmp_path) is True
    return TestClient(app)


def _url(filename: str) -> str:
    return f"{media_store.MEDIA_URL_PREFIX}/{nas_stream.URL_SEGMENT}/{filename}"


# --- serving ----------------------------------------------------------------
def test_a_full_get_returns_the_whole_clip_with_the_headers_a_receiver_needs(monkeypatch, tmp_path):
    """The default receiver refuses to play without Accept-Ranges and a length."""
    _fake_smbclient(monkeypatch)
    filename = nas_stream.register(CAST_PATH)

    with _client(tmp_path) as client:
        response = client.get(_url(filename))

    assert response.status_code == 200
    assert response.content == PAYLOAD
    assert response.headers["accept-ranges"] == "bytes"
    assert int(response.headers["content-length"]) == len(PAYLOAD)
    assert response.headers["content-type"].startswith("video/mp4")


def test_a_range_request_returns_exactly_that_span(monkeypatch, tmp_path):
    """Seek is a Range GET, so a 200 here is what breaks scrubbing on the TV."""
    _fake_smbclient(monkeypatch)
    filename = nas_stream.register(CAST_PATH)

    with _client(tmp_path) as client:
        response = client.get(_url(filename), headers={"Range": "bytes=0-15"})

    assert response.status_code == 206
    assert response.content == PAYLOAD[:16]
    assert len(response.content) == 16
    assert response.headers["content-range"] == f"bytes 0-15/{len(PAYLOAD)}"
    assert int(response.headers["content-length"]) == 16


def test_an_open_ended_range_returns_the_tail(monkeypatch, tmp_path):
    """`bytes=N-` is how a receiver resumes after a stall."""
    _fake_smbclient(monkeypatch)
    filename = nas_stream.register(CAST_PATH)
    start = len(PAYLOAD) - 4

    with _client(tmp_path) as client:
        response = client.get(_url(filename), headers={"Range": f"bytes={start}-"})

    assert response.status_code == 206
    assert response.content == PAYLOAD[-4:]
    assert response.headers["content-range"] == f"bytes {start}-{len(PAYLOAD) - 1}/{len(PAYLOAD)}"


def test_a_start_past_the_end_of_the_file_is_refused_with_the_real_size(monkeypatch, tmp_path):
    """416 must carry `bytes */size` or the receiver cannot correct itself."""
    _fake_smbclient(monkeypatch)
    filename = nas_stream.register(CAST_PATH)

    with _client(tmp_path) as client:
        response = client.get(_url(filename), headers={"Range": f"bytes={len(PAYLOAD) + 10}-"})

    assert response.status_code == 416
    assert response.headers["content-range"] == f"bytes */{len(PAYLOAD)}"


@pytest.mark.parametrize("header", ["bytes=-100", "bytes=0-15,32-47", "bytes=abc-", "kilobytes=0-1"])
def test_the_range_shapes_we_do_not_implement_degrade_to_the_whole_file(monkeypatch, tmp_path, header):
    """A suffix or multi-range must answer legally, never 500.

    Serving the whole file with a 200 is a valid response to any Range request,
    and it is the one answer no receiver can choke on.
    """
    _fake_smbclient(monkeypatch)
    filename = nas_stream.register(CAST_PATH)

    with _client(tmp_path) as client:
        response = client.get(_url(filename), headers={"Range": header})

    assert response.status_code == 200
    assert response.content == PAYLOAD


def test_head_answers_with_the_same_headers_and_no_body(monkeypatch, tmp_path):
    """A Chromecast HEADs the URL for the length and the container first."""
    _fake_smbclient(monkeypatch)
    filename = nas_stream.register(CAST_PATH)

    with _client(tmp_path) as client:
        head = client.head(_url(filename))

    assert head.status_code == 200
    assert head.content == b""
    assert head.headers["accept-ranges"] == "bytes"
    assert int(head.headers["content-length"]) == len(PAYLOAD)
    assert head.headers["content-type"].startswith("video/mp4")


def test_a_span_longer_than_one_block_is_assembled_read_by_read(monkeypatch, tmp_path):
    """The real clips are hundreds of blocks; the loop arithmetic has to hold.

    With a 1 MiB block every test payload above fits in a single read, so the
    "keep reading until the span is done, and stop exactly there" part of the
    generator would otherwise never run.
    """
    _fake_smbclient(monkeypatch)
    monkeypatch.setattr(nas_stream, "_BLOCK_BYTES", 100)
    filename = nas_stream.register(CAST_PATH)

    with _client(tmp_path) as client:
        full = client.get(_url(filename))
        ranged = client.get(_url(filename), headers={"Range": "bytes=50-349"})

    assert full.content == PAYLOAD
    assert ranged.status_code == 206
    assert ranged.content == PAYLOAD[50:350]
    assert len(ranged.content) == 300


def test_an_unregistered_filename_is_a_404(monkeypatch, tmp_path):
    """The endpoint serves the registry, not the share: a guessed name gets nothing."""
    recorded = _fake_smbclient(monkeypatch)

    with _client(tmp_path) as client:
        response = client.get(_url("0123456789abcdef.mp4"))

    assert response.status_code == 404
    assert recorded["sessions"] == [] and recorded["opened"] == []


def test_a_missing_nas_credential_reports_unavailable_without_touching_smb(monkeypatch, tmp_path):
    """A half-configured NAS must answer 503, not attempt an anonymous connect."""
    recorded = _fake_smbclient(monkeypatch)
    filename = nas_stream.register(CAST_PATH)
    monkeypatch.delenv("HANOVA_NAS_PASSWORD", raising=False)

    with _client(tmp_path) as client:
        response = client.get(_url(filename))

    assert response.status_code == 503
    assert recorded["sessions"] == []


def test_a_nas_that_does_not_answer_is_a_502(monkeypatch, tmp_path):
    """The size lookup is the first real round trip, and it must fail cleanly."""
    _fake_smbclient(monkeypatch, stat_raises=True)
    filename = nas_stream.register(CAST_PATH)

    with _client(tmp_path) as client:
        response = client.get(_url(filename))

    assert response.status_code == 502
    assert SENTINEL_HOST not in response.text


def test_the_connect_is_bounded_by_the_same_timeout_the_staged_path_uses(monkeypatch, tmp_path):
    """A spun-down NAS must fail the request, not hold a worker thread forever."""
    recorded = _fake_smbclient(monkeypatch)
    filename = nas_stream.register(CAST_PATH)

    with _client(tmp_path) as client:
        client.get(_url(filename))

    assert recorded["sessions"] == [(SENTINEL_HOST, nas._SMB_CONNECT_TIMEOUT_S)]


def test_the_stream_route_and_the_staged_mount_both_stay_reachable(monkeypatch, tmp_path):
    """The dynamic route sits at the same prefix as the StaticFiles mounts."""
    _fake_smbclient(monkeypatch)
    filename = nas_stream.register(CAST_PATH)
    staged = media_store.media_dir("nas", tmp_path) / "clip.mp4"
    staged.write_bytes(b"STAGED")

    with _client(tmp_path) as client:
        streamed = client.get(_url(filename))
        served = client.get(f"{media_store.MEDIA_URL_PREFIX}/nas/clip.mp4")

    assert streamed.status_code == 200 and streamed.content == PAYLOAD
    assert served.status_code == 200 and served.content == b"STAGED"


# --- the registry -----------------------------------------------------------
def test_the_registry_is_bounded_and_evicts_the_oldest_registration(monkeypatch, tmp_path):
    """An index has thousands of clips; the mapping table may not grow with it."""
    _fake_smbclient(monkeypatch)
    first = nas_stream.register(f"SENTINEL_CAST_DIR_q4/SENTINEL_TRIP_q4/clip{0:03d}.mp4")
    for index in range(1, 65):
        nas_stream.register(f"SENTINEL_CAST_DIR_q4/SENTINEL_TRIP_q4/clip{index:03d}.mp4")

    assert nas_stream.lookup(first) is None
    with _client(tmp_path) as client:
        assert client.get(_url(first)).status_code == 404


def test_re_registering_a_clip_does_not_age_it_out(monkeypatch, tmp_path):
    """A replayed clip is the one most likely to be fetched, not the least."""
    first = nas_stream.register(CAST_PATH)
    for index in range(1, 64):
        nas_stream.register(f"SENTINEL_CAST_DIR_q4/SENTINEL_TRIP_q4/clip{index:03d}.mp4")
    nas_stream.register(CAST_PATH)
    nas_stream.register("SENTINEL_CAST_DIR_q4/SENTINEL_TRIP_q4/clip999.mp4")

    assert nas_stream.lookup(first) == CAST_PATH


@pytest.mark.parametrize(
    "bad",
    [
        "../etc/passwd",
        "SENTINEL_CAST_DIR_q4/../../etc/passwd",
        "/absolute/SENTINEL_CAST_DIR_q4/clip.mp4",
        "SomewhereElse/clip.mp4",
        "SENTINEL_CAST_DIR_q4/clip.exe",
        "",
    ],
)
def test_registering_a_path_outside_the_cast_subtree_is_refused(bad):
    """Finding 15 applies to the streamed path exactly as it does to the staged one."""
    with pytest.raises(nas.NasError):
        nas_stream.register(bad)
    assert nas_stream._REGISTRY == {}


def test_the_served_name_is_the_same_digest_the_staged_path_uses():
    """One clip has one served name, whichever route prepared it."""
    assert nas_stream.register(CAST_PATH) == nas.cast_filename(CAST_PATH)


# --- route selection --------------------------------------------------------
def _stub_cast(monkeypatch) -> dict:
    """Record the HA cast and fail loudly if anything tries to copy off the NAS."""
    recorded: dict[str, Any] = {"cast": []}

    def never_fetch(cast_path, destination):
        raise AssertionError("the streaming route must not copy the clip off the NAS")

    async def fake_run_script(script_name, data, timeout_s=60.0):
        recorded["cast"].append((script_name, data))
        return {"ok": True, "result": []}

    monkeypatch.setattr(nas, "fetch_cast_file", never_fetch)
    monkeypatch.setattr(nas, "ha_run_script", fake_run_script)
    return recorded


@pytest.mark.asyncio
async def test_streaming_casts_a_stream_url_and_stages_nothing(monkeypatch, tmp_path):
    """The whole point: the TV gets a URL immediately, with no copy in front of it."""
    recorded = _stub_cast(monkeypatch)

    out = await nas.stage_and_cast(VIDEO, tmp_path)

    assert out["ok"] is True
    script, data = recorded["cast"][0]
    assert script == "tv_show_video_url"
    assert data["url"] == (
        f"http://robot.example.invalid:7860{media_store.MEDIA_URL_PREFIX}"
        f"/{nas_stream.URL_SEGMENT}/{nas.cast_filename(CAST_PATH)}"
    )
    assert data["entity_id"] == "media_player.example_tv"
    assert data["title"]
    assert nas_stream.lookup(nas.cast_filename(CAST_PATH)) == CAST_PATH
    assert not (tmp_path / "hanova_media" / "nas").exists() or list((tmp_path / "hanova_media" / "nas").iterdir()) == []


@pytest.mark.asyncio
async def test_the_kill_switch_restores_the_staged_copy(monkeypatch, tmp_path):
    """`HANOVA_NAS_STREAM=0` must reach the old path, copy and all."""
    monkeypatch.setenv("HANOVA_NAS_STREAM", "0")
    recorded: dict[str, Any] = {"fetched": [], "cast": []}

    def fake_fetch(cast_path, destination):
        recorded["fetched"].append(cast_path)
        destination.write_bytes(b"MP4")

    async def fake_run_script(script_name, data, timeout_s=60.0):
        recorded["cast"].append((script_name, data))
        return {"ok": True, "result": []}

    monkeypatch.setattr(nas, "fetch_cast_file", fake_fetch)
    monkeypatch.setattr(nas, "ha_run_script", fake_run_script)

    out = await nas.stage_and_cast(VIDEO, tmp_path)

    assert out["ok"] is True
    assert recorded["fetched"] == [CAST_PATH]
    _script, data = recorded["cast"][0]
    assert f"{media_store.MEDIA_URL_PREFIX}/nas/" in data["url"]
    assert nas_stream.URL_SEGMENT not in data["url"]
    assert nas_stream.lookup(nas.cast_filename(CAST_PATH)) is None


@pytest.mark.asyncio
async def test_a_rejected_index_entry_never_reaches_the_registry(monkeypatch, tmp_path):
    """A clip that fails validation must not become streamable as a side effect."""
    _stub_cast(monkeypatch)
    stray = dict(VIDEO)
    stray["path"] = "SomewhereElse/SENTINEL_TRIP_q4/clip01.mp4"

    out = await nas.stage_and_cast(stray, tmp_path)

    assert out["ok"] is False
    assert nas_stream._REGISTRY == {}


@pytest.mark.asyncio
async def test_streaming_without_a_lan_base_url_reports_the_same_failure_as_staging(monkeypatch, tmp_path):
    """No base URL means no URL for the TV, whichever route prepared the clip."""
    monkeypatch.delenv("HANOVA_MEDIA_HTTP_BASE", raising=False)
    recorded = _stub_cast(monkeypatch)

    out = await nas.stage_and_cast(VIDEO, tmp_path)

    assert out["ok"] is False
    assert out["error"].startswith("HANOVA_MEDIA_HTTP_BASE is not set")
    assert recorded["cast"] == []


@pytest.mark.parametrize("raw,expected", [("", True), ("1", True), ("yes", True), ("0", False), ("false", False), ("NO", False)])
def test_the_switch_reads_the_documented_values(monkeypatch, raw, expected):
    """On by default; only the three off-words turn it off."""
    if raw:
        monkeypatch.setenv("HANOVA_NAS_STREAM", raw)
    else:
        monkeypatch.delenv("HANOVA_NAS_STREAM", raising=False)
    assert settings.nas_stream_enabled() is expected


# --- redaction --------------------------------------------------------------
def test_the_nas_stream_logs_no_path_share_or_host(monkeypatch, caplog, tmp_path):
    """Round 2, finding 6: the stream endpoint is a service seam and logs like one.

    The SMB error text carries the whole UNC path, and the served URL is on the
    LAN in the clear -- so the failure that is easiest to log carelessly is the
    one this drives: a NAS that will not answer the size lookup.

    Only **our** records are scanned: the test client's own httpx logger prints
    the request line, which is not this port's log surface and is not something
    this module can redact.
    """
    caplog.set_level(logging.DEBUG)
    _fake_smbclient(monkeypatch, stat_raises=True)
    filename = nas_stream.register(CAST_PATH)

    with _client(tmp_path) as client:
        assert client.get(_url(filename)).status_code == 502

    ours = "\n".join(
        record.getMessage() for record in caplog.records if record.name.startswith("reachy_companion")
    )
    assert ours, "nothing was logged at all; this sentinel would pass vacuously"
    for token in (SENTINEL_HOST, "SENTINEL_SHARE_q4", "SENTINEL_CAST_DIR_q4", "SENTINEL_TRIP_q4", filename):
        assert token not in ours
