"""Contract tests for the adapted Google OAuth layer (D-018, R1). No network.

Also pins review round 1 finding 14: one lock per credentials file, an atomic
0600 write, and no duplicate refresh when Calendar and Tasks arrive together.
"""

import os
import json
import stat
import time
import threading
from datetime import datetime, timezone, timedelta

import pytest

from reachy_companion.hanova import gauth, sync_http


ACCOUNT = "someone@example.com"


def _creds(expiry: datetime) -> dict:
    return {
        "client_id": "cid",
        "client_secret": "csecret",
        "refresh_token": "rtok",
        "token": "old-access-token",
        "token_uri": "https://oauth2.googleapis.com/token",
        "expiry": expiry.strftime("%Y-%m-%dT%H:%M:%S"),
    }


@pytest.fixture
def creds_file(monkeypatch, tmp_path):
    """Provide a writable credentials directory, as the robot instance dir will be."""
    monkeypatch.setenv("GOOGLE_CREDS_DIR", str(tmp_path))
    monkeypatch.setenv("HANOVA_GOOGLE_ACCOUNT", ACCOUNT)
    path = tmp_path / f"{ACCOUNT}.json"
    path.write_text(json.dumps(_creds(datetime.now(timezone.utc) + timedelta(hours=1))), encoding="utf-8")
    return path


def test_valid_token_is_reused_without_a_refresh(monkeypatch, creds_file):
    """An unexpired token must not cost a round trip on every tool call."""

    def fail(*args, **kwargs):
        raise AssertionError("gauth must not refresh a valid token")

    monkeypatch.setattr(sync_http, "request_bytes", fail)
    assert gauth.get_access_token() == "old-access-token"


def test_expired_token_is_refreshed_and_rewritten(monkeypatch, creds_file):
    """The refresh rewrites the credentials file, so the robot needs its own copy."""
    creds_file.write_text(json.dumps(_creds(datetime.now(timezone.utc) - timedelta(hours=1))), encoding="utf-8")
    seen = {}

    def fake_request(method, url, headers, data=None, timeout_s=15):
        seen["url"] = url
        return 200, json.dumps({"access_token": "new-access-token", "expires_in": 3600}).encode()

    monkeypatch.setattr(sync_http, "request_bytes", fake_request)
    assert gauth.get_access_token() == "new-access-token"
    assert seen["url"] == "https://oauth2.googleapis.com/token"
    assert json.loads(creds_file.read_text(encoding="utf-8"))["token"] == "new-access-token"


def test_the_rewritten_file_is_owner_only(monkeypatch, creds_file):
    """Finding 14: the refresh token must never be world-readable, not even briefly."""
    creds_file.write_text(json.dumps(_creds(datetime.now(timezone.utc) - timedelta(hours=1))), encoding="utf-8")
    monkeypatch.setattr(
        sync_http,
        "request_bytes",
        lambda *a, **k: (200, json.dumps({"access_token": "new", "expires_in": 3600}).encode()),
    )
    gauth.get_access_token()
    if os.name != "nt":  # POSIX permission bits are meaningless on Windows
        assert stat.S_IMODE(creds_file.stat().st_mode) == 0o600


def test_no_stray_temp_file_is_left_behind(monkeypatch, creds_file, tmp_path):
    """The atomic write must not litter the credentials directory."""
    creds_file.write_text(json.dumps(_creds(datetime.now(timezone.utc) - timedelta(hours=1))), encoding="utf-8")
    monkeypatch.setattr(
        sync_http,
        "request_bytes",
        lambda *a, **k: (200, json.dumps({"access_token": "new", "expires_in": 3600}).encode()),
    )
    gauth.get_access_token()
    assert [p.name for p in tmp_path.iterdir()] == [creds_file.name]


def test_simultaneous_expired_calls_refresh_exactly_once(monkeypatch, creds_file):
    """Finding 14: Calendar and Tasks arriving together must not both refresh.

    The loser of the lock re-reads the file inside the critical section, finds a
    valid token, and issues no request of its own.
    """
    creds_file.write_text(json.dumps(_creds(datetime.now(timezone.utc) - timedelta(hours=1))), encoding="utf-8")
    refreshes = {"n": 0}
    barrier = threading.Barrier(2)

    def slow_refresh(method, url, headers, data=None, timeout_s=15):
        refreshes["n"] += 1
        time.sleep(0.05)  # widen the window the old code raced inside
        return 200, json.dumps({"access_token": "new-access-token", "expires_in": 3600}).encode()

    monkeypatch.setattr(sync_http, "request_bytes", slow_refresh)

    tokens: list[str] = []

    def worker() -> None:
        barrier.wait()
        tokens.append(gauth.get_access_token())

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert tokens == ["new-access-token", "new-access-token"]
    assert refreshes["n"] == 1
    assert json.loads(creds_file.read_text(encoding="utf-8"))["token"] == "new-access-token"


def test_missing_credentials_file_names_the_path(monkeypatch, tmp_path):
    """The operator must be told exactly which file to copy to the robot."""
    monkeypatch.setenv("GOOGLE_CREDS_DIR", str(tmp_path))
    monkeypatch.setenv("HANOVA_GOOGLE_ACCOUNT", ACCOUNT)
    with pytest.raises(FileNotFoundError) as excinfo:
        gauth.get_access_token()
    assert ACCOUNT in str(excinfo.value)


def test_api_call_sends_the_bearer_and_parses_json(monkeypatch, creds_file):
    """The whole point of the layer: one authenticated JSON call."""
    seen = {}

    def fake_request(method, url, headers, data=None, timeout_s=15):
        seen.update(method=method, url=url, headers=headers, data=data)
        return 200, b'{"id": "evt1"}'

    monkeypatch.setattr(sync_http, "request_bytes", fake_request)
    out = gauth.api_call("POST", "https://api.example.invalid/v3/events", body={"summary": "x"})
    assert out == {"id": "evt1"}
    assert seen["headers"]["Authorization"] == "Bearer old-access-token"
    assert seen["headers"]["Content-Type"] == "application/json"
    assert json.loads(seen["data"]) == {"summary": "x"}


def test_api_call_appends_the_query_string(monkeypatch, creds_file):
    """Query params must be encoded, and None values dropped."""
    seen = {}

    def fake_request(method, url, headers, data=None, timeout_s=15):
        seen["url"] = url
        return 200, b"{}"

    monkeypatch.setattr(sync_http, "request_bytes", fake_request)
    gauth.api_call("GET", "https://api.example.invalid/v3/events", query={"maxResults": 5, "q": None})
    assert seen["url"] == "https://api.example.invalid/v3/events?maxResults=5"


def test_api_call_refreshes_once_on_401(monkeypatch, creds_file):
    """A token that went stale mid-session must not surface as a tool failure."""
    calls: list[str] = []

    def fake_request(method, url, headers, data=None, timeout_s=15):
        calls.append(url)
        if url.endswith("/token"):
            return 200, json.dumps({"access_token": "fresh", "expires_in": 3600}).encode()
        if len([c for c in calls if not c.endswith("/token")]) == 1:
            return 401, b'{"error": {"message": "Invalid Credentials"}}'
        return 200, b'{"ok": true}'

    monkeypatch.setattr(sync_http, "request_bytes", fake_request)
    assert gauth.api_call("GET", "https://api.example.invalid/v3/events") == {"ok": True}


def test_api_call_raises_google_api_error_on_failure(monkeypatch, creds_file):
    """Callers need the status code to decide what to say -- and nothing else.

    Deviation from the brief's own draft of this test, argued in the task report:
    the draft asserted Google's message reached `str(exc)`, which is exactly what
    review finding 7 forbids, and what `GoogleApiError.__init__` (given verbatim
    two steps later in the same brief) deliberately does not do. The parsed body
    stays reachable on `.body` for a caller that genuinely needs it; the string
    form -- the thing a bare `%s` would put in a log -- carries only shape.
    """

    def fake_request(method, url, headers, data=None, timeout_s=15):
        return 404, b'{"error": {"message": "Not Found"}}'

    monkeypatch.setattr(sync_http, "request_bytes", fake_request)
    with pytest.raises(gauth.GoogleApiError) as excinfo:
        gauth.api_call("GET", "https://api.example.invalid/v3/events/x")
    assert excinfo.value.status == 404
    assert excinfo.value.body["error"]["message"] == "Not Found"
    assert "Not Found" not in str(excinfo.value)
    assert "404" in str(excinfo.value)
