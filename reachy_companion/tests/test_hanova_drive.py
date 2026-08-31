"""Contract tests for the three Drive tools (D-018, R1/R2/R3/R5).

Also pins review round 1 findings 4 (claim/complete/release), 7 (no file name or
id in a log line) and 17 (Drive restore is an approved non-goal).
"""

import json
import types
import importlib
from pathlib import Path

import pytest

from reachy_companion.hanova import gdrive, sync_http
from reachy_companion.hanova.confirm import GATE
from reachy_companion.tools.drive_list import DriveList
from reachy_companion.tools.drive_trash import DriveTrash
from reachy_companion.tools.drive_upload import DriveUpload


PARENT_ID = "folder-under-test"
JPEG_BYTES = b"\xff\xd8\xff\xe0fakejpeg"


def _deps(camera_enabled: bool = True, frame: bytes | None = JPEG_BYTES):
    media = types.SimpleNamespace(get_frame_jpeg=lambda: frame)
    return types.SimpleNamespace(
        reachy_mini=types.SimpleNamespace(media=media),
        instance_path=None,
        camera_enabled=camera_enabled,
    )


@pytest.fixture(autouse=True)
def configured(monkeypatch, tmp_path):
    """Configure the drive family, and open an empty confirmation gate."""
    secrets = tmp_path / "google-oauth.json"
    secrets.write_text(
        json.dumps({"clientId": "cid", "clientSecret": "csecret", "refreshToken": "rtok"}), encoding="utf-8"
    )
    monkeypatch.setenv("HERMES_DRIVE_SECRETS", str(secrets))
    monkeypatch.setenv("HANOVA_DRIVE_PARENT_ID", PARENT_ID)
    monkeypatch.delenv("HANOVA_CONFIRM_TTL_S", raising=False)
    GATE.reset()
    GATE.begin_session()
    yield
    GATE.reset()


def test_tool_names_match_their_filenames():
    """The loader resolves tools by filename == Tool.name."""
    assert DriveList.name == "drive_list"
    assert DriveTrash.name == "drive_trash"
    assert DriveUpload.name == "drive_upload"


def test_descriptions_carry_no_personal_identifier():
    """R10: upstream embedded the real Drive folder id in two descriptions."""
    for text in (DriveList().description, DriveTrash().description, DriveUpload().description):
        assert "@" not in text
        assert PARENT_ID not in text
        assert len(text) <= 120


def test_drive_token_is_minted_from_the_refresh_token(monkeypatch):
    """A fresh token per run means no cached token file can go stale."""
    seen = {}

    def fake_request(method, url, headers, data=None, timeout_s=15):
        seen["url"] = url
        seen["data"] = data
        return 200, b'{"access_token": "drive-token"}'

    monkeypatch.setattr(sync_http, "request_bytes", fake_request)
    assert gdrive.drive_token() == "drive-token"
    assert seen["url"] == "https://oauth2.googleapis.com/token"
    assert b"refresh_token=rtok" in seen["data"]


def test_drive_token_accepts_the_nested_secret_shape(monkeypatch, tmp_path):
    """The operator's secret file may be flat or nested; both must work."""
    secrets = tmp_path / "nested.json"
    secrets.write_text(
        json.dumps({"gmail": {"oauth": {"clientId": "c", "clientSecret": "s", "refreshToken": "r"}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_DRIVE_SECRETS", str(secrets))
    monkeypatch.setattr(sync_http, "request_bytes", lambda *a, **k: (200, b'{"access_token": "drive-token"}'))
    assert gdrive.drive_token() == "drive-token"


def test_drive_token_without_a_secret_file_raises(monkeypatch):
    """A missing credential is a configuration fact, surfaced as DriveError."""
    monkeypatch.delenv("HERMES_DRIVE_SECRETS")
    with pytest.raises(gdrive.DriveError):
        gdrive.drive_token()


@pytest.mark.parametrize("payload", ["[]", '"nope"', '{"gmail": "not-an-object"}', '{"gmail": {}}'])
def test_a_malformed_secret_file_is_a_drive_error_not_an_attribute_error(monkeypatch, tmp_path, payload):
    """No tool catches AttributeError, so a bad secret shape must not raise one.

    Every `except` clause in the drive tools names
    `(DriveError, OSError, ValueError, KeyError)`; anything else escapes past the
    tool unhandled. A secret file whose top level is a list, or whose `gmail` key
    is a string, is a configuration fact like any other.
    """
    secrets = tmp_path / "malformed.json"
    secrets.write_text(payload, encoding="utf-8")
    monkeypatch.setenv("HERMES_DRIVE_SECRETS", str(secrets))
    with pytest.raises(gdrive.DriveError):
        gdrive.drive_token()


def test_list_files_queries_one_folder_level(monkeypatch):
    """drive_list reads a folder, it does not walk the whole Drive."""
    seen = {}

    def fake_request(method, url, headers, data=None, timeout_s=15):
        if url.endswith("/token"):
            return 200, b'{"access_token": "drive-token"}'
        seen["url"] = url
        return 200, b'{"files": [{"id": "f1", "name": "notes.txt"}]}'

    monkeypatch.setattr(sync_http, "request_bytes", fake_request)
    files = gdrive.list_files(PARENT_ID, limit=10)
    assert files == [{"id": "f1", "name": "notes.txt"}]
    assert "in%20parents" in seen["url"] or "in+parents" in seen["url"]
    assert "pageSize=10" in seen["url"]


def test_upload_bytes_sends_a_multipart_body_and_never_shares(monkeypatch):
    """Upstream defaulted to anyone-with-link; we never create a permission."""
    calls: list[str] = []

    def fake_request(method, url, headers, data=None, timeout_s=15):
        calls.append(url)
        if url.endswith("/token"):
            return 200, b'{"access_token": "drive-token"}'
        assert headers["Content-Type"].startswith("multipart/related; boundary=")
        assert b"fakejpeg" in (data or b"")
        return 200, b'{"id": "f9", "name": "reachy.jpg", "webViewLink": "https://drive.example.invalid/f9"}'

    monkeypatch.setattr(sync_http, "request_bytes", fake_request)
    out = gdrive.upload_bytes(JPEG_BYTES, "reachy.jpg", "image/jpeg", PARENT_ID)
    assert out["id"] == "f9"
    assert not any("/permissions" in url for url in calls)


@pytest.mark.asyncio
async def test_drive_list_is_unavailable_without_config(monkeypatch):
    """R5: an unconfigured tool answers, it does not raise, and it names the key."""
    monkeypatch.delenv("HERMES_DRIVE_SECRETS")
    out = await DriveList()(deps=_deps())
    assert out == {"status": "unavailable", "reason": "HERMES_DRIVE_SECRETS"}


def test_no_tool_can_untrash_anything():
    """Finding 17: Drive restore is an approved non-goal, enforced structurally."""
    tools_dir = Path(importlib.import_module("reachy_companion.tools").__file__).parent
    for name in ("drive_list", "drive_trash", "drive_upload"):
        source = (tools_dir / f"{name}.py").read_text(encoding="utf-8")
        assert "trashed=False" not in source, name
        assert "untrash" not in source.lower(), name
    trash_source = (tools_dir / "drive_trash.py").read_text(encoding="utf-8")
    assert trash_source.count("set_trashed") == 1, "exactly one trash call, and it trashes"


@pytest.mark.asyncio
async def test_a_transient_drive_failure_keeps_the_authorisation(monkeypatch):
    """Finding 4: a 503 must not cost the user their confirmation."""
    import reachy_companion.tools.drive_trash as drive_trash_module

    monkeypatch.setattr(drive_trash_module.gdrive, "get_file", lambda file_id: {"id": file_id, "name": "notes.txt"})
    attempts = {"n": 0}

    def flaky(file_id, trashed=True):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise drive_trash_module.DriveError("Drive PATCH -> HTTP 503", status=503)
        return {"id": file_id, "trashed": True}

    monkeypatch.setattr(drive_trash_module.gdrive, "set_trashed", flaky)
    await DriveTrash()(deps=_deps(), file_id="f1")
    first = await DriveTrash()(deps=_deps(), confirm=True)
    assert first["ok"] is False and first.get("retryable") is True
    second = await DriveTrash()(deps=_deps(), confirm=True)
    assert second["ok"] is True and second["status"] == "trashed"


@pytest.mark.asyncio
async def test_drive_logs_never_carry_a_file_name_or_id(monkeypatch, caplog):
    """Finding 7: Drive file names are personal data and ids are identifiers."""
    import logging

    import reachy_companion.tools.drive_trash as drive_trash_module

    sentinel = "SENTINEL_PRIVATE_x7"
    monkeypatch.setattr(drive_trash_module.gdrive, "get_file", lambda file_id: {"id": file_id, "name": sentinel})
    monkeypatch.setattr(drive_trash_module.gdrive, "set_trashed", lambda file_id, trashed=True: {"id": file_id})
    caplog.set_level(logging.DEBUG)
    await DriveTrash()(deps=_deps(), file_id=sentinel)
    await DriveTrash()(deps=_deps(), confirm=True)
    assert sentinel not in caplog.text


@pytest.mark.asyncio
async def test_drive_list_returns_compact_rows(monkeypatch):
    """The model gets names and ids, not raw Drive payloads."""
    import reachy_companion.tools.drive_list as drive_list_module

    monkeypatch.setattr(
        drive_list_module.gdrive,
        "list_files",
        lambda parent_id, limit=50, include_trashed=False: [
            {"id": "f1", "name": "notes.txt", "mimeType": "text/plain", "modifiedTime": "2026-08-01T00:00:00Z"}
        ],
    )
    out = await DriveList()(deps=_deps())
    assert out["ok"] is True and out["count"] == 1
    assert out["files"][0]["name"] == "notes.txt"


@pytest.mark.asyncio
async def test_drive_trash_arms_with_the_real_file_name(monkeypatch):
    """R3: the read-back must name the file, not echo the id the model guessed."""
    import reachy_companion.tools.drive_trash as drive_trash_module

    monkeypatch.setattr(
        drive_trash_module.gdrive,
        "get_file",
        lambda file_id: {"id": file_id, "name": "holiday-photos", "mimeType": "application/vnd.google-apps.folder"},
    )

    def fail_trash(file_id, trashed=True):
        raise AssertionError("drive_trash must not trash before confirmation")

    monkeypatch.setattr(drive_trash_module.gdrive, "set_trashed", fail_trash)
    out = await DriveTrash()(deps=_deps(), file_id="f1")
    assert out["status"] == "needs_confirmation" and "holiday-photos" in out["summary"]


@pytest.mark.asyncio
async def test_drive_trash_executes_the_armed_payload(monkeypatch):
    """The confirmed trash uses the id that was read back."""
    import reachy_companion.tools.drive_trash as drive_trash_module

    monkeypatch.setattr(drive_trash_module.gdrive, "get_file", lambda file_id: {"id": file_id, "name": "notes.txt"})
    seen = {}

    # A named function, not the plan's lambda: a `trashed=True` parameter would
    # shadow the recorder dict this test asserts on.
    def record(file_id, trashed=True):
        seen.update(file_id=file_id)
        return {"id": file_id, "trashed": True}

    monkeypatch.setattr(drive_trash_module.gdrive, "set_trashed", record)
    await DriveTrash()(deps=_deps(), file_id="f1")
    out = await DriveTrash()(deps=_deps(), file_id="a-completely-different-id", confirm=True)
    assert out["ok"] is True and out["status"] == "trashed"
    assert seen == {"file_id": "f1"}


@pytest.mark.asyncio
async def test_drive_trash_confirm_without_arm_is_refused(monkeypatch):
    """A confirm:true first call must trash nothing."""
    import reachy_companion.tools.drive_trash as drive_trash_module

    def fail_trash(file_id, trashed=True):
        raise AssertionError("drive_trash must not trash without a pending action")

    monkeypatch.setattr(drive_trash_module.gdrive, "set_trashed", fail_trash)
    out = await DriveTrash()(deps=_deps(), file_id="f1", confirm=True)
    assert out["status"] == "confirmation_expired"


@pytest.mark.asyncio
async def test_drive_upload_arms_then_captures_and_uploads(monkeypatch):
    """R2: this uploads a photo Reachy takes, captured at confirm time."""
    import reachy_companion.tools.drive_upload as drive_upload_module

    uploaded = {}

    def fake_upload(data, name, mime, parent_id):
        uploaded.update(data=data, name=name, mime=mime, parent_id=parent_id)
        return {"id": "f9", "name": name, "webViewLink": "https://drive.example.invalid/f9"}

    monkeypatch.setattr(drive_upload_module.gdrive, "upload_bytes", fake_upload)

    armed = await DriveUpload()(deps=_deps())
    assert armed["status"] == "needs_confirmation" and "camera" in armed["summary"].lower()
    assert uploaded == {}

    out = await DriveUpload()(deps=_deps(), confirm=True)
    assert out["ok"] is True and out["file_id"] == "f9"
    assert uploaded["data"] == JPEG_BYTES
    assert uploaded["mime"] == "image/jpeg"
    assert uploaded["parent_id"] == PARENT_ID


@pytest.mark.asyncio
async def test_drive_upload_reports_a_disabled_camera():
    """No camera means no photo; say so instead of uploading nothing."""
    out = await DriveUpload()(deps=_deps(camera_enabled=False))
    assert out["ok"] is False and "camera" in out["error"].lower()


@pytest.mark.asyncio
async def test_drive_upload_reports_a_missing_frame(monkeypatch):
    """A camera that yields no frame is a reportable failure at confirm time."""
    import reachy_companion.tools.drive_upload as drive_upload_module

    def fail_upload(data, name, mime, parent_id):
        raise AssertionError("drive_upload must not upload an empty frame")

    monkeypatch.setattr(drive_upload_module.gdrive, "upload_bytes", fail_upload)
    await DriveUpload()(deps=_deps())
    out = await DriveUpload()(deps=_deps(frame=None), confirm=True)
    assert out["ok"] is False and "frame" in out["error"].lower()


@pytest.mark.asyncio
async def test_a_malformed_upload_payload_never_strands_the_claim(monkeypatch):
    """Final review, F5: the payload lookup sat between `claim()` and the `try`.

    Every other settlement copy reads the parked payload **inside** the try, so a
    missing key is settled by the `finally` (Task 10 review ruling). This one read
    `pending.payload["name"]` one line too early: the `KeyError` escaped with the
    slot claimed, and a claimed slot refuses both `claim()` and `arm()` for the
    rest of the session -- the tool would be dead until a reconnect.
    """
    import reachy_companion.tools.drive_upload as drive_upload_module

    monkeypatch.setattr(
        drive_upload_module.gdrive,
        "upload_bytes",
        lambda data, name, mime, parent_id: {"id": "f9", "name": name, "webViewLink": "https://x.invalid/f9"},
    )

    await DriveUpload()(deps=_deps())
    GATE._pending["drive_upload"].payload.pop("name")
    with pytest.raises(KeyError):
        await DriveUpload()(deps=_deps(), confirm=True)

    # The slot is settled, so a fresh read-back arms and executes normally again.
    assert (await DriveUpload()(deps=_deps()))["status"] == "needs_confirmation"
    assert (await DriveUpload()(deps=_deps(), confirm=True))["ok"] is True


def test_all_three_tools_reach_the_model_session():
    """The locked profile must list the family, or the model never sees them.

    2026-08-31 tool diet: these are no longer registered under their own
    names -- they are the actions of the `drive` façade, which is what the
    profile lists now. Their modules, names and prerequisite rows are
    unchanged; only the surface the model reaches them through is.
    """
    core_tools = importlib.import_module("reachy_companion.tools.core_tools")
    core_tools.initialize_tools(force=True)
    try:
        registry = core_tools.get_tools()
        assert "drive" in registry, "the locked profile no longer lists the family"
        reachable = {tool.name for tool in type(registry["drive"]).ACTIONS.values()}
        assert {"drive_list", "drive_trash", "drive_upload"} <= reachable
    finally:
        core_tools._TOOLS_SIGNATURE = None
