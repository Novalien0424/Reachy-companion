"""Contract tests for the management API: every route, through `TestClient`.

Nothing here reaches a robot and nothing here loads a 37 MB ONNX session. Two
seams are stood in for, and only two:

* `app._recognizer` — the module-level recognizer cache is pre-filled with a
  fake, so `recognizer_for` never builds the real one. The one test that *is*
  about the cold path (`test_startup_kicks_the_recognizer_warmup`) stands in for
  `embedding.build_recognizer` instead and enters the lifespan deliberately.
* `robot.*` — `drift`, `push`, `import_from_robot`, `apply_import` and the four
  daemon calls. Their own contracts are covered by `test_robot_sync.py`; what is
  covered here is the *mapping* from what they return or raise onto a status
  code and a JSON body.

Everything else — the store, the projection of a photo record into JSON, the
error handlers — runs for real against a `tmp_path` data directory.

The other TestClient detail worth stating: these tests build the client
*without* `with`, so the lifespan never runs. That is what keeps the warm-up
out of every test but the one that asks for it.
"""

from __future__ import annotations
import math
import random
import threading
from typing import Any
from pathlib import Path
from itertools import count
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from reachy_companion import faces
from backend import app as app_module
from backend import robot, store, embedding
from backend.config import Settings


FIXTURES = Path(__file__).resolve().parent / "fixtures"
GRAY_JPEG = FIXTURES / "gray.jpg"


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def monotonic_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give every store write a distinct, increasing timestamp."""
    ticks = count(1_700_000_000_000)
    monkeypatch.setattr(store, "_now_ms", lambda: next(ticks))


class FakeRecognizer:
    """Stands in for `FaceRecognizer`: it is only ever warmed, never called."""

    def __init__(self) -> None:
        self.warmups = 0

    def start_warmup(self) -> None:
        self.warmups += 1

    def embedding_for_frame(self, frame_bgr: Any) -> tuple[Any, Any]:  # pragma: no cover - never reached
        raise AssertionError("embed_photo is stood in for; the recognizer must not be used.")


@pytest.fixture(autouse=True)
def fake_recognizer(monkeypatch: pytest.MonkeyPatch) -> FakeRecognizer:
    """Pre-fill the module-level recognizer cache so no test loads the real models."""
    recognizer = FakeRecognizer()
    monkeypatch.setattr(app_module, "_recognizer", recognizer)
    return recognizer


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    """A client over an app rooted at this test's own data directory (no lifespan)."""
    yield TestClient(app_module.create_app(settings))


def _vector(seed: int) -> tuple[float, ...]:
    """Return a 128-float embedding shaped exactly like a stored one."""
    rng = random.Random(seed)
    raw = [rng.uniform(-1.0, 1.0) for _ in range(faces.EMBEDDING_DIM)]
    scale = math.sqrt(sum(value * value for value in raw))
    return tuple(round(value / scale, 6) for value in raw)


def _create(client: TestClient, name: str) -> dict[str, Any]:
    response = client.post("/api/people", json={"name": name})
    assert response.status_code == 200, response.text
    person: dict[str, Any] = response.json()
    return person


def _upload(client: TestClient, person_id: str, filename: str = "me.jpg") -> Any:
    return client.post(
        f"/api/people/{person_id}/photos",
        files={"file": (filename, GRAY_JPEG.read_bytes(), "image/jpeg")},
    )


# --------------------------------------------------------------------------
# config and people
# --------------------------------------------------------------------------


def test_config_reports_the_robot_host(client: TestClient, settings: Settings) -> None:
    response = client.get("/api/config")
    assert response.status_code == 200
    assert response.json()["reachy_host"] == settings.reachy_host


def test_people_crud_round_trip(client: TestClient) -> None:
    person = _create(client, "  Nova  ")
    assert person["name"] == "Nova"
    assert person["facts"] == []
    assert person["photos"] == []

    listed = client.get("/api/people").json()
    assert [item["id"] for item in listed] == [person["id"]]

    renamed = client.patch(f"/api/people/{person['id']}", json={"name": "Nova Lien"})
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Nova Lien"

    deleted = client.delete(f"/api/people/{person['id']}")
    assert deleted.status_code == 200
    assert deleted.json()["id"] == person["id"]
    assert client.get("/api/people").json() == []


def test_duplicate_name_is_409(client: TestClient) -> None:
    _create(client, "Nova")
    response = client.post("/api/people", json={"name": "nova"})
    assert response.status_code == 409
    body = response.json()
    assert body["kind"] == "duplicate_name"
    assert "Nova" in body["error"]


def test_empty_name_is_400(client: TestClient) -> None:
    response = client.post("/api/people", json={"name": "   "})
    assert response.status_code == 400
    assert response.json()["kind"] == "empty_value"


def test_missing_name_field_is_422(client: TestClient) -> None:
    assert client.post("/api/people", json={}).status_code == 422


def test_rename_to_another_persons_name_is_409(client: TestClient) -> None:
    _create(client, "Nova")
    other = _create(client, "Sam")
    response = client.patch(f"/api/people/{other['id']}", json={"name": "Nova"})
    assert response.status_code == 409


def test_unknown_person_is_404(client: TestClient) -> None:
    assert client.patch("/api/people/bp_nope", json={"name": "Nova"}).status_code == 404
    assert client.delete("/api/people/bp_nope").status_code == 404
    assert client.post("/api/people/bp_nope/facts", json={"text": "hi"}).status_code == 404


# --------------------------------------------------------------------------
# facts
# --------------------------------------------------------------------------


def test_facts_add_and_delete(client: TestClient) -> None:
    person = _create(client, "Nova")
    added = client.post(f"/api/people/{person['id']}/facts", json={"text": "likes tea"})
    assert added.status_code == 200
    fact = added.json()
    assert fact["text"] == "likes tea"

    listed = client.get("/api/people").json()
    assert [item["text"] for item in listed[0]["facts"]] == ["likes tea"]

    removed = client.delete(f"/api/people/{person['id']}/facts/{fact['id']}")
    assert removed.status_code == 200
    assert removed.json()["id"] == fact["id"]
    assert client.get("/api/people").json()[0]["facts"] == []


def test_empty_fact_is_400(client: TestClient) -> None:
    person = _create(client, "Nova")
    response = client.post(f"/api/people/{person['id']}/facts", json={"text": "  "})
    assert response.status_code == 400
    assert response.json()["kind"] == "empty_value"


def test_delete_unknown_fact_is_404(client: TestClient) -> None:
    person = _create(client, "Nova")
    assert client.delete(f"/api/people/{person['id']}/facts/bf_nope").status_code == 404


# --------------------------------------------------------------------------
# photos
# --------------------------------------------------------------------------


def test_photo_upload_embeds_synchronously(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Path] = []

    def fake_embed(recognizer: Any, path: Path) -> tuple[tuple[float, ...] | None, str | None]:
        seen.append(path)
        return _vector(1), None

    monkeypatch.setattr(embedding, "embed_photo", fake_embed)

    person = _create(client, "Nova")
    response = _upload(client, person["id"])
    assert response.status_code == 200, response.text
    photo = response.json()

    assert photo["display_name"] == "me.jpg"
    assert photo["has_embedding"] is True
    assert photo["error"] is None
    assert photo["synthetic"] is False
    assert "embedding" not in photo
    # The bytes really were written where the embedder was pointed.
    assert seen and seen[0].read_bytes() == GRAY_JPEG.read_bytes()


def test_photo_upload_reports_an_embedding_failure_as_data(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(embedding, "embed_photo", lambda recognizer, path: (None, "no_face"))
    person = _create(client, "Nova")
    response = _upload(client, person["id"])
    assert response.status_code == 200
    photo = response.json()
    assert photo["error"] == "no_face"
    assert photo["has_embedding"] is False


def test_photo_upload_to_an_unknown_person_is_404(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(embedding, "embed_photo", lambda recognizer, path: (_vector(2), None))
    assert _upload(client, "bp_nope").status_code == 404


def test_photo_file_route_serves_the_bytes(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(embedding, "embed_photo", lambda recognizer, path: (_vector(3), None))
    person = _create(client, "Nova")
    photo = _upload(client, person["id"]).json()

    response = client.get(f"/api/people/{person['id']}/photos/{photo['id']}/file")
    assert response.status_code == 200
    assert response.content == GRAY_JPEG.read_bytes()
    assert response.headers["content-type"].startswith("image/jpeg")


def test_photo_file_route_404s_for_a_synthetic_photo(client: TestClient, settings: Settings) -> None:
    person = store.create_person(settings, "Nova")
    photo = store.add_synthetic_photo(settings, person.id, _vector(4))
    response = client.get(f"/api/people/{person.id}/photos/{photo.id}/file")
    assert response.status_code == 404
    assert response.json()["kind"] == "no_photo_bytes"


def test_photo_delete_removes_the_record(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(embedding, "embed_photo", lambda recognizer, path: (_vector(5), None))
    person = _create(client, "Nova")
    photo = _upload(client, person["id"]).json()

    removed = client.delete(f"/api/people/{person['id']}/photos/{photo['id']}")
    assert removed.status_code == 200
    assert removed.json()["id"] == photo["id"]
    assert client.get("/api/people").json()[0]["photos"] == []
    assert client.delete(f"/api/people/{person['id']}/photos/{photo['id']}").status_code == 404


def test_listed_photos_never_carry_the_embedding(client: TestClient, settings: Settings) -> None:
    person = store.create_person(settings, "Nova")
    store.add_synthetic_photo(settings, person.id, _vector(6))
    listed = client.get("/api/people").json()
    photo = listed[0]["photos"][0]
    assert photo["has_embedding"] is True
    assert photo["synthetic"] is True
    assert photo["stored_as"] is None
    assert "embedding" not in photo


# --------------------------------------------------------------------------
# sync
# --------------------------------------------------------------------------


def test_sync_status_reports_drift_and_the_last_push(
    client: TestClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.set_sync_meta(settings, store.SyncMeta(last_push_at=1_700_000_000_123, last_faces_sha256="a" * 64))
    monkeypatch.setattr(
        robot,
        "drift",
        lambda _settings: robot.DriftState(faces_changed=True, people_changed=False, never_pushed=False),
    )
    response = client.get("/api/sync/status")
    assert response.status_code == 200
    body = response.json()
    assert body["robot_reachable"] is True
    assert body["last_push_at"] == 1_700_000_000_123
    assert body["drift"] == {"faces_changed": True, "people_changed": False, "never_pushed": False}
    assert body["error"] is None


def test_sync_status_reports_an_unreachable_robot_instead_of_500(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unreachable(_settings: Settings) -> robot.DriftState:
        raise robot.RobotError("Could not fetch faces.v1.json from the robot: ssh: connect to host failed")

    monkeypatch.setattr(robot, "drift", unreachable)
    response = client.get("/api/sync/status")
    assert response.status_code == 200
    body = response.json()
    assert body["robot_reachable"] is False
    assert body["drift"] is None
    assert "ssh: connect to host failed" in body["error"]


def test_push_returns_the_result(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        robot,
        "push",
        lambda _settings: robot.PushResult(
            pushed=True, faces_count=2, people_count=3, blocked_by=None, skipped=["Sam"]
        ),
    )
    response = client.post("/api/sync/push")
    assert response.status_code == 200
    assert response.json() == {
        "pushed": True,
        "faces_count": 2,
        "people_count": 3,
        "skipped": ["Sam"],
        "blocked_by": None,
    }


def test_push_blocked_is_409_carrying_the_diff(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    diff = robot.RobotDiff(
        new_faces=[robot.RobotFace(record_id="f1", name="Sam", embeddings=(_vector(7),))],
        changed_faces=[],
        new_person_facts=[robot.RobotPersonFacts(name="Sam", face_id="f1", facts=["likes tea"])],
        removed_person_facts=[robot.RobotPersonFacts(name="Nova", face_id=None, facts=["drinks coffee"])],
    )
    monkeypatch.setattr(
        robot,
        "push",
        lambda _settings: robot.PushResult(pushed=False, faces_count=0, people_count=0, blocked_by=diff),
    )
    response = client.post("/api/sync/push")
    assert response.status_code == 409
    body = response.json()
    assert body["pushed"] is False
    blocked = body["blocked_by"]
    assert blocked["kind"] == "robot_content"
    assert blocked["diff"]["new_faces"] == [{"record_id": "f1", "name": "Sam", "sample_count": 1}]
    assert blocked["diff"]["new_person_facts"] == [
        {"name": "Sam", "face_id": "f1", "facts": ["likes tea"]}
    ]
    assert blocked["diff"]["removed_person_facts"] == [
        {"name": "Nova", "face_id": None, "facts": ["drinks coffee"]}
    ]
    # 128 floats per sample never travel to the UI.
    assert "embeddings" not in blocked["diff"]["new_faces"][0]


def test_push_race_is_409_with_the_message(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        robot,
        "push",
        lambda _settings: robot.PushResult(
            pushed=False, faces_count=0, people_count=0, blocked_by=robot.PushRace("changed in flight")
        ),
    )
    response = client.post("/api/sync/push")
    assert response.status_code == 409
    assert response.json()["blocked_by"] == {"kind": "race", "message": "changed in flight"}


def test_push_ssh_failure_is_502_with_the_stderr(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def failing(_settings: Settings) -> robot.PushResult:
        raise robot.RobotError("Could not stage .faces.push.tmp on the robot: Permission denied (publickey)")

    monkeypatch.setattr(robot, "push", failing)
    response = client.post("/api/sync/push")
    assert response.status_code == 502
    body = response.json()
    assert body["kind"] == "robot_unreachable"
    assert "Permission denied (publickey)" in body["error"]


def test_import_preview_lists_the_diff(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    diff = robot.RobotDiff(
        new_faces=[],
        changed_faces=[robot.RobotFace(record_id="f1", name="Sam", embeddings=(_vector(8), _vector(9)))],
        new_person_facts=[],
        removed_person_facts=[robot.RobotPersonFacts(name="Nova", face_id=None, facts=["drinks coffee"])],
    )
    monkeypatch.setattr(robot, "import_from_robot", lambda _settings: diff)
    response = client.get("/api/sync/import")
    assert response.status_code == 200
    body = response.json()
    assert body["applied"] is None
    assert body["conflicts"] == []
    assert body["diff"]["changed_faces"] == [{"record_id": "f1", "name": "Sam", "sample_count": 2}]
    assert body["diff"]["removed_person_facts"][0]["facts"] == ["drinks coffee"]
    assert body["diff"]["empty"] is False


def test_import_applies_the_current_diff(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """The POST must fetch the diff again server-side, not trust a previewed one."""
    fresh = robot.RobotDiff(
        new_faces=[robot.RobotFace(record_id="f2", name="Sam", embeddings=(_vector(10),))],
        changed_faces=[],
        new_person_facts=[],
        removed_person_facts=[],
    )
    applied_with: list[robot.RobotDiff] = []
    monkeypatch.setattr(robot, "import_from_robot", lambda _settings: fresh)

    def fake_apply(_settings: Settings, diff: robot.RobotDiff) -> robot.ImportResult:
        applied_with.append(diff)
        return robot.ImportResult(applied=1, conflicts=["Sam: two faces under one name"])

    monkeypatch.setattr(robot, "apply_import", fake_apply)

    response = client.post("/api/sync/import")
    assert response.status_code == 200
    body = response.json()
    assert applied_with == [fresh]
    assert body["applied"] == 1
    assert body["conflicts"] == ["Sam: two faces under one name"]
    assert body["diff"]["new_faces"][0]["record_id"] == "f2"


def test_import_failure_is_502(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def failing(_settings: Settings) -> robot.RobotDiff:
        raise robot.RobotError("Could not fetch people.v1.json from the robot: Connection timed out")

    monkeypatch.setattr(robot, "import_from_robot", failing)
    assert client.get("/api/sync/import").status_code == 502
    assert client.post("/api/sync/import").status_code == 502


def test_a_push_the_robot_did_not_keep_is_502_robot_not_verified(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed verify is not "the robot is unreachable" — it has its own slug.

    Both are 502, but the operator's next move differs: an unreachable robot is
    retried, a robot that reported success and then does not hold what was sent
    is investigated. `RobotVerifyError` subclasses `RobotError`, so this asserts
    the *more specific* handler wins.
    """

    def failing(_settings: Settings) -> robot.PushResult:
        raise robot.RobotVerifyError("The promote reported success but the robot does not hold what this push sent")

    monkeypatch.setattr(robot, "push", failing)
    response = client.post("/api/sync/push")
    assert response.status_code == 502
    body = response.json()
    assert body["kind"] == "robot_not_verified"
    assert "does not hold what this push sent" in body["error"]


def _blocking_seam(started: threading.Event, release: threading.Event) -> Any:
    """Return a robot seam that parks inside the route until `release` is set."""

    def seam(_settings: Settings) -> Any:
        started.set()
        assert release.wait(timeout=10)
        return robot.PushResult(pushed=True, faces_count=1, people_count=1, blocked_by=None)

    return seam


@pytest.mark.parametrize("second_call", ["push", "import"])
def test_a_concurrent_mutating_sync_is_409_sync_busy(
    client: TestClient, settings: Settings, monkeypatch: pytest.MonkeyPatch, second_call: str
) -> None:
    """Two pushes at once would race on the robot's staged files; the second is refused.

    The refusal is immediate (a non-blocking acquire), not a queue: the operator
    gets an answer rather than a request that hangs behind a 20 s ssh.
    """
    started, release = threading.Event(), threading.Event()
    monkeypatch.setattr(robot, "push", _blocking_seam(started, release))
    monkeypatch.setattr(robot, "import_from_robot", lambda _settings: pytest.fail("the lock let a second sync in"))

    first: dict[str, int] = {}
    thread = threading.Thread(target=lambda: first.update(status=client.post("/api/sync/push").status_code))
    thread.start()
    try:
        assert started.wait(timeout=10)
        # A second client, to prove the lock is the module's and not one client's.
        busy = TestClient(app_module.create_app(settings)).post(f"/api/sync/{second_call}")
        assert busy.status_code == 409
        assert busy.json()["kind"] == "sync_busy"
    finally:
        release.set()
        thread.join(timeout=10)
    assert first == {"status": 200}


def test_the_sync_lock_is_released_after_a_failure(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """A push that raised must not leave the next one refused as busy."""

    def failing(_settings: Settings) -> robot.PushResult:
        raise robot.RobotError("Connection timed out")

    monkeypatch.setattr(robot, "push", failing)
    assert client.post("/api/sync/push").status_code == 502
    assert client.post("/api/sync/push").status_code == 502  # not 409
    assert not app_module._SYNC_LOCK.locked()


def test_the_import_preview_is_not_serialized(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """The GET reads the robot and writes nothing, so it must not be blocked by a push."""
    started, release = threading.Event(), threading.Event()
    monkeypatch.setattr(robot, "push", _blocking_seam(started, release))
    monkeypatch.setattr(
        robot,
        "import_from_robot",
        lambda _settings: robot.RobotDiff(new_faces=[], changed_faces=[], new_person_facts=[]),
    )

    thread = threading.Thread(target=lambda: client.post("/api/sync/push"))
    thread.start()
    try:
        assert started.wait(timeout=10)
        assert client.get("/api/sync/import").status_code == 200
    finally:
        release.set()
        thread.join(timeout=10)


# --------------------------------------------------------------------------
# the robot's app lifecycle, proxied
# --------------------------------------------------------------------------


def test_robot_routes_proxy_the_daemon(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def seam(name: str) -> Any:
        def call(_settings: Settings) -> dict[str, object]:
            calls.append(name)
            return {"result": name}

        return call

    for route, function in (
        ("status", "robot_app_status"),
        ("start", "robot_app_start"),
        ("stop", "robot_app_stop"),
        ("restart", "robot_app_restart"),
    ):
        monkeypatch.setattr(robot, function, seam(route))

    assert client.get("/api/robot/status").json() == {"result": "status"}
    assert client.post("/api/robot/start").json() == {"result": "start"}
    assert client.post("/api/robot/stop").json() == {"result": "stop"}
    assert client.post("/api/robot/restart").json() == {"result": "restart"}
    assert calls == ["status", "start", "stop", "restart"]


def test_robot_route_failure_is_502(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def failing(_settings: Settings) -> dict[str, object]:
        raise robot.RobotError("The robot daemon did not answer GET http://10.0.0.5:8000/…: timed out")

    monkeypatch.setattr(robot, "robot_app_status", failing)
    response = client.get("/api/robot/status")
    assert response.status_code == 502
    assert "timed out" in response.json()["error"]


# --------------------------------------------------------------------------
# the UI and the lifespan
# --------------------------------------------------------------------------


def test_an_unrouted_url_uses_the_same_error_envelope(client: TestClient) -> None:
    """One shape for every failure, including the ones Starlette raises itself."""
    response = client.get("/api/nope")
    assert response.status_code == 404
    assert response.json()["kind"] == "not_found"
    assert client.get("/static/missing.css").json()["kind"] == "not_found"
    method_not_allowed = client.post("/api/config")
    assert method_not_allowed.status_code == 405
    assert method_not_allowed.json()["kind"] == "http_error"
    assert "allow" in method_not_allowed.headers


def test_index_and_static_are_served(client: TestClient) -> None:
    index = client.get("/")
    assert index.status_code == 200
    assert index.headers["content-type"].startswith("text/html")
    assert client.get("/static/index.html").status_code == 200


def test_startup_kicks_the_recognizer_warmup(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    recognizer = FakeRecognizer()
    monkeypatch.setattr(app_module, "_recognizer", None)
    monkeypatch.setattr(embedding, "build_recognizer", lambda _settings: recognizer)

    with TestClient(app_module.create_app(settings)) as started:
        assert started.get("/api/config").status_code == 200

    assert recognizer.warmups == 1


def test_a_failed_warmup_does_not_stop_the_server(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    def exploding(_settings: Settings) -> Any:
        raise OSError("no such model")

    monkeypatch.setattr(app_module, "_recognizer", None)
    monkeypatch.setattr(embedding, "build_recognizer", exploding)

    with TestClient(app_module.create_app(settings)) as started:
        assert started.get("/api/config").status_code == 200
