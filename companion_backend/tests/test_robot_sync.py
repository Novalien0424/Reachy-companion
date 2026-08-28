"""Contract tests for the guarded robot sync: fetch, drift, diff, push, import.

Nothing here talks to a robot. `subprocess.run` is replaced by a `FakeRobot`
that keeps the robot's two store files in a dict and — this is the point —
**evaluates the promote guard for real**: it reads the expected sha256s out of
the ssh script and refuses the promote when its own content no longer matches.
A test that changes the robot's files between the fetch and the promote
therefore trips the same guard the real robot would, rather than a stubbed exit
code.
"""

from __future__ import annotations
import math
import random
import hashlib
import tempfile
import subprocess
from typing import Any
from pathlib import Path
from itertools import count
from dataclasses import field, dataclass
from collections.abc import Callable, Sequence

import pytest

from reachy_companion import faces, people
from backend import robot, store, projection
from backend.config import Settings


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def monotonic_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give every backend store write a distinct, increasing timestamp."""
    ticks = count(1_700_000_000_000)
    monkeypatch.setattr(store, "_now_ms", lambda: next(ticks))


def _vector(seed: int) -> tuple[float, ...]:
    """Return a 128-float embedding shaped exactly like a stored one."""
    rng = random.Random(seed)
    raw = [rng.uniform(-1.0, 1.0) for _ in range(faces.EMBEDDING_DIM)]
    scale = math.sqrt(sum(value * value for value in raw))
    return tuple(round(value / scale, 6) for value in raw)


def _record(record_id: str, name: str, seeds: Sequence[int]) -> faces.FaceRecord:
    """Build one robot-side face record."""
    return faces.FaceRecord(
        id=record_id,
        name=name,
        embeddings=tuple(_vector(seed) for seed in seeds),
        created_at=1_600_000_000_000,
        updated_at=1_600_000_000_000,
    )


def _faces_content(root: Path, records: Sequence[faces.FaceRecord]) -> str:
    """Return the bytes the robot's own writer would put in `faces.v1.json`."""
    directory = Path(tempfile.mkdtemp(dir=root))
    path = faces.faces_path_for_instance(directory)
    faces._write_faces_file(path, list(records))
    return path.read_text(encoding="utf-8")


def _people_content(root: Path, entries: Sequence[tuple[str, str | None, Sequence[str]]]) -> str:
    """Return the bytes the robot's own writers would put in `people.v1.json`.

    Facts are given oldest-first and replayed in that order, exactly as the
    robot's own voice path would add them.
    """
    directory = Path(tempfile.mkdtemp(dir=root))
    people.clear_people(directory)
    for name, face_id, facts in entries:
        people.upsert_person(directory, name, face_id=face_id)
        for text in facts:
            people.add_person_fact(directory, name, text, face_id=face_id)
    return people.people_path_for_instance(directory).read_text(encoding="utf-8")


def _sha(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _person(
    settings: Settings,
    name: str,
    *,
    embeddings: Sequence[int] = (),
    facts: Sequence[str] = (),
    face_id: str | None = None,
) -> store.BackendPerson:
    """Create one backend person with synthetic embeddings (oldest first) and facts."""
    person = store.create_person(settings, name)
    if face_id is not None:
        store.set_person_face_id(settings, person.id, face_id)
    for seed in embeddings:
        store.add_synthetic_photo(settings, person.id, _vector(seed))
    for text in facts:
        store.add_fact(settings, person.id, text)
    reloaded = store.get_person(settings, person.id)
    assert reloaded is not None
    return reloaded


@dataclass
class FakeRobot:
    """The robot's two store files, reachable only through `subprocess.run`."""

    remote: dict[str, str] = field(default_factory=dict)
    calls: list[tuple[str, ...]] = field(default_factory=list)
    log: list[tuple[str, ...]] = field(default_factory=list)
    download_error: str | None = None
    before_promote: Callable[[FakeRobot], None] | None = None
    after_promote: Callable[[FakeRobot], None] | None = None

    def sha(self, filename: str) -> str:
        content = self.remote.get(filename)
        return robot.ABSENT if content is None else _sha(content)

    def run(self, argv: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        """Stand in for `subprocess.run`, asserting the call shape as it goes."""
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        assert kwargs["timeout"] == robot.SSH_TIMEOUT_SECONDS
        argv = list(argv)
        assert "expect" not in argv[0]
        self.calls.append(tuple(argv))

        if argv[0] == "scp":
            return self._scp(argv)
        if argv[0] == "ssh":
            return self._promote(argv)
        raise AssertionError(f"unexpected program {argv[0]!r}")

    def _scp(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        source, destination = argv[-2], argv[-1]
        if ":" in source:
            filename = source.rsplit("/", 1)[-1]
            self.log.append(("get", filename))
            if self.download_error is not None:
                return subprocess.CompletedProcess(argv, 1, "", self.download_error)
            content = self.remote.get(filename)
            if content is None:
                # Some scp builds create and truncate the local file before the
                # remote open fails; the caller has to survive that.
                Path(destination).write_text("", encoding="utf-8")
                return subprocess.CompletedProcess(argv, 1, "", f"scp: {source}: No such file or directory")
            Path(destination).write_text(content, encoding="utf-8")
            return subprocess.CompletedProcess(argv, 0, "", "")

        filename = destination.rsplit("/", 1)[-1]
        self.log.append(("put", filename))
        self.remote[filename] = Path(source).read_text(encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, "", "")

    def _promote(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        self.log.append(("promote",))
        assert "-o" in argv and "BatchMode=yes" in argv
        script = argv[-1]
        if self.before_promote is not None:
            self.before_promote(self)

        expected = dict(robot._EXPECTED_SHA_PATTERN.findall(script))
        # The guard has to re-check *both* files, or the promote is not guarded.
        assert set(expected) == {"faces", "people"}, script
        current = {"faces": self.sha(faces.FACES_FILENAME), "people": self.sha(people.PEOPLE_FILENAME)}
        if current != expected:
            self.remote.pop(robot.FACES_TMP_NAME, None)
            self.remote.pop(robot.PEOPLE_TMP_NAME, None)
            return subprocess.CompletedProcess(argv, robot.PROMOTE_RACE_EXIT, "", "")

        self.remote[faces.FACES_FILENAME] = self.remote.pop(robot.FACES_TMP_NAME)
        self.remote[people.PEOPLE_FILENAME] = self.remote.pop(robot.PEOPLE_TMP_NAME)
        if self.after_promote is not None:
            self.after_promote(self)
        return subprocess.CompletedProcess(argv, 0, "", "")


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeRobot:
    """Install a fake robot in place of every `subprocess.run` the module makes."""
    stand_in = FakeRobot()
    monkeypatch.setattr(robot.subprocess, "run", stand_in.run)
    return stand_in


def _mark_pushed(settings: Settings, fake: FakeRobot) -> None:
    """Record the fake's current content as the last verified push."""
    store.set_sync_meta(
        settings,
        store.SyncMeta(
            last_push_at=1_600_000_000_000,
            last_faces_sha256=fake.sha(faces.FACES_FILENAME),
            last_people_sha256=fake.sha(people.PEOPLE_FILENAME),
        ),
    )


# --------------------------------------------------------------------------
# fetch
# --------------------------------------------------------------------------


def test_fetch_stores_downloads_both_files(settings: Settings, fake: FakeRobot, tmp_path: Path) -> None:
    """Both stores land locally, fetched over plain scp with no interactive wrapper."""
    fake.remote[faces.FACES_FILENAME] = _faces_content(tmp_path, [_record("f_1", "Lena", [1])])
    fake.remote[people.PEOPLE_FILENAME] = _people_content(tmp_path, [("Lena", "f_1", ["likes tea"])])

    fetched = robot.fetch_stores(settings, tmp_path / "into")

    assert fetched["faces"] is not None and fetched["people"] is not None
    assert fetched["faces"].read_text(encoding="utf-8") == fake.remote[faces.FACES_FILENAME]
    assert fetched["people"].read_text(encoding="utf-8") == fake.remote[people.PEOPLE_FILENAME]
    assert fake.log == [("get", faces.FACES_FILENAME), ("get", people.PEOPLE_FILENAME)]
    assert fake.calls[0][0] == "scp"
    assert fake.calls[0][-2] == f"pollen@10.0.0.5:{settings.instance_dir}/{faces.FACES_FILENAME}"


def test_fetch_stores_reports_a_missing_remote_file_as_none(
    settings: Settings, fake: FakeRobot, tmp_path: Path
) -> None:
    """A robot that has never run the app has no stores — that is not an error.

    The half-written file scp may leave behind must not survive: the diff reads
    the fetch directory, and an empty `faces.v1.json` there would read as a robot
    that holds nothing rather than one we failed to read.
    """
    fetched = robot.fetch_stores(settings, tmp_path / "into")

    assert fetched == {"faces": None, "people": None}
    assert list((tmp_path / "into").iterdir()) == []


def test_fetch_stores_raises_when_the_robot_cannot_be_reached(
    settings: Settings, fake: FakeRobot, tmp_path: Path
) -> None:
    """A connection failure must not read as an empty robot — that would push over live data."""
    fake.download_error = "ssh: connect to host 10.0.0.5 port 22: Operation timed out"

    with pytest.raises(robot.RobotError):
        robot.fetch_stores(settings, tmp_path / "into")


def test_fetch_stores_raises_on_a_hung_transfer(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Every remote call is bounded; a hung scp surfaces as an error, not a stuck request."""

    def hang(argv: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(list(argv), robot.SSH_TIMEOUT_SECONDS)

    monkeypatch.setattr(robot.subprocess, "run", hang)

    with pytest.raises(robot.RobotError):
        robot.fetch_stores(settings, tmp_path / "into")


# --------------------------------------------------------------------------
# drift
# --------------------------------------------------------------------------


def test_drift_is_hash_based(settings: Settings, fake: FakeRobot, tmp_path: Path) -> None:
    """Any robot-side write changes the hash, and a changed hash is drift."""
    fake.remote[faces.FACES_FILENAME] = _faces_content(tmp_path, [_record("f_1", "Lena", [1])])
    fake.remote[people.PEOPLE_FILENAME] = _people_content(tmp_path, [("Lena", "f_1", [])])
    _mark_pushed(settings, fake)

    assert robot.drift(settings) == robot.DriftState(faces_changed=False, people_changed=False, never_pushed=False)

    fake.remote[people.PEOPLE_FILENAME] = _people_content(tmp_path, [("Lena", "f_1", ["likes tea"])])

    assert robot.drift(settings) == robot.DriftState(faces_changed=False, people_changed=True, never_pushed=False)


def test_drift_treats_an_absent_remote_file_as_no_hash(
    settings: Settings, fake: FakeRobot, tmp_path: Path
) -> None:
    """A robot that has never been pushed to and holds nothing has drifted from nothing."""
    assert robot.drift(settings) == robot.DriftState(faces_changed=False, people_changed=False, never_pushed=True)


def test_drift_on_a_never_pushed_robot_that_holds_content(
    settings: Settings, fake: FakeRobot, tmp_path: Path
) -> None:
    """Content we never pushed is drift by definition."""
    fake.remote[faces.FACES_FILENAME] = _faces_content(tmp_path, [_record("f_1", "Lena", [1])])

    assert robot.drift(settings) == robot.DriftState(faces_changed=True, people_changed=False, never_pushed=True)


# --------------------------------------------------------------------------
# the content view
# --------------------------------------------------------------------------


def test_robot_diff_lists_a_voice_enrolled_person(settings: Settings, fake: FakeRobot, tmp_path: Path) -> None:
    """A face enrolled by voice is new content, carried with its identity intact."""
    fake.remote[faces.FACES_FILENAME] = _faces_content(tmp_path, [_record("f_new", "Sam", [5, 6])])
    fake.remote[people.PEOPLE_FILENAME] = _people_content(tmp_path, [("Sam", "f_new", ["plays cello"])])

    diff = robot.robot_diff(settings)

    assert diff.new_faces == [robot.RobotFace(record_id="f_new", name="Sam", embeddings=(_vector(5), _vector(6)))]
    assert diff.changed_faces == []
    assert diff.new_person_facts == [robot.RobotPersonFacts(name="Sam", face_id="f_new", facts=["plays cello"])]
    assert not diff.empty
    assert robot.import_from_robot(settings) == diff


def test_robot_diff_is_empty_when_the_robot_holds_what_we_know(
    settings: Settings, fake: FakeRobot, tmp_path: Path
) -> None:
    """Content the backend already knows is not a reason to block anything."""
    _person(settings, "Lena", embeddings=[1], facts=["likes tea"], face_id="f_lena")
    fake.remote[faces.FACES_FILENAME] = _faces_content(tmp_path, [_record("f_lena", "Lena", [1])])
    fake.remote[people.PEOPLE_FILENAME] = _people_content(tmp_path, [("Lena", "f_lena", ["likes tea"])])

    diff = robot.robot_diff(settings)

    assert diff.empty
    assert diff == robot.RobotDiff(new_faces=[], changed_faces=[], new_person_facts=[])


def test_robot_diff_reports_a_re_enrollment_as_changed(
    settings: Settings, fake: FakeRobot, tmp_path: Path
) -> None:
    """A second enrollment into a known record id is a sample the backend does not hold."""
    _person(settings, "Lena", embeddings=[1], face_id="f_lena")
    fake.remote[faces.FACES_FILENAME] = _faces_content(tmp_path, [_record("f_lena", "Lena", [1, 9])])
    fake.remote[people.PEOPLE_FILENAME] = _people_content(tmp_path, [("Lena", "f_lena", [])])

    diff = robot.robot_diff(settings)

    assert diff.new_faces == []
    assert diff.changed_faces == [
        robot.RobotFace(record_id="f_lena", name="Lena", embeddings=(_vector(1), _vector(9)))
    ]
    assert not diff.empty


def test_robot_diff_ignores_facts_the_backend_already_holds(
    settings: Settings, fake: FakeRobot, tmp_path: Path
) -> None:
    """Only the facts we do not have count as new."""
    _person(settings, "Lena", embeddings=[1], facts=["likes tea"], face_id="f_lena")
    fake.remote[faces.FACES_FILENAME] = _faces_content(tmp_path, [_record("f_lena", "Lena", [1])])
    fake.remote[people.PEOPLE_FILENAME] = _people_content(tmp_path, [("Lena", "f_lena", ["likes tea", "has a cat"])])

    diff = robot.robot_diff(settings)

    assert diff.new_faces == []
    assert diff.changed_faces == []
    assert diff.new_person_facts == [robot.RobotPersonFacts(name="Lena", face_id="f_lena", facts=["has a cat"])]


# --------------------------------------------------------------------------
# push: the gate
# --------------------------------------------------------------------------


def test_push_is_blocked_by_robot_content_the_backend_does_not_know(
    settings: Settings, fake: FakeRobot, tmp_path: Path
) -> None:
    """The one thing a push must never do is overwrite an enrollment nobody imported."""
    _person(settings, "Lena", embeddings=[1])
    fake.remote[faces.FACES_FILENAME] = _faces_content(tmp_path, [_record("f_new", "Sam", [5])])
    fake.remote[people.PEOPLE_FILENAME] = _people_content(tmp_path, [("Sam", "f_new", [])])
    before = dict(fake.remote)

    result = robot.push(settings)

    assert result.pushed is False
    assert isinstance(result.blocked_by, robot.RobotDiff)
    assert [entry.name for entry in result.blocked_by.new_faces] == ["Sam"]
    # Nothing was uploaded and nothing was promoted.
    assert fake.log == [("get", faces.FACES_FILENAME), ("get", people.PEOPLE_FILENAME)]
    assert fake.remote == before
    assert store.get_sync_meta(settings) == store.SyncMeta()


def test_push_is_blocked_until_a_re_enrollment_is_imported(
    settings: Settings, fake: FakeRobot, tmp_path: Path
) -> None:
    """Codex R2-1: a changed record blocks, and importing it is what unblocks the push."""
    person = _person(settings, "Lena", embeddings=[1], face_id="f_lena")
    fake.remote[faces.FACES_FILENAME] = _faces_content(tmp_path, [_record("f_lena", "Lena", [1, 9])])
    fake.remote[people.PEOPLE_FILENAME] = _people_content(tmp_path, [("Lena", "f_lena", [])])
    # The robot re-enrolled after our last push, so the recorded hashes are stale.
    store.set_sync_meta(settings, store.SyncMeta(last_push_at=1, last_faces_sha256="0" * 64, last_people_sha256="1" * 64))

    blocked = robot.push(settings)
    assert blocked.pushed is False
    assert isinstance(blocked.blocked_by, robot.RobotDiff)
    assert [entry.record_id for entry in blocked.blocked_by.changed_faces] == ["f_lena"]

    applied = robot.apply_import(settings, robot.import_from_robot(settings))
    assert applied.applied == 1
    assert applied.conflicts == []

    # The re-enrolled sample is now the newest, so the projection emits it back.
    reloaded = store.get_person(settings, person.id)
    assert reloaded is not None
    assert set(projection.embeddings_for(reloaded)) == {_vector(1), _vector(9)}

    after = robot.push(settings)
    assert after.pushed is True
    promoted = _faces_from_remote(fake, tmp_path)
    assert set(promoted[0].embeddings) == {_vector(1), _vector(9)}


def test_push_proceeds_on_imported_drift(settings: Settings, fake: FakeRobot, tmp_path: Path) -> None:
    """Hashes stale by design after an import are not a reason to refuse."""
    _person(settings, "Lena", embeddings=[1], facts=["likes tea"], face_id="f_lena")
    fake.remote[faces.FACES_FILENAME] = _faces_content(tmp_path, [_record("f_lena", "Lena", [1])])
    fake.remote[people.PEOPLE_FILENAME] = _people_content(tmp_path, [("Lena", "f_lena", ["likes tea"])])
    store.set_sync_meta(settings, store.SyncMeta(last_push_at=1, last_faces_sha256="0" * 64, last_people_sha256="1" * 64))

    state = robot.drift(settings)
    assert state.faces_changed and state.people_changed
    assert robot.robot_diff(settings).empty

    result = robot.push(settings)

    assert result.pushed is True
    assert result.blocked_by is None


def test_push_proceeds_on_a_never_pushed_empty_robot(
    settings: Settings, fake: FakeRobot, tmp_path: Path
) -> None:
    """The first push of all: fetch twice, upload twice, promote once, verify twice."""
    _person(settings, "Lena", embeddings=[1], facts=["likes tea"])

    result = robot.push(settings)

    assert result == robot.PushResult(pushed=True, faces_count=1, people_count=1, blocked_by=None)
    assert fake.log == [
        ("get", faces.FACES_FILENAME),
        ("get", people.PEOPLE_FILENAME),
        ("put", robot.FACES_TMP_NAME),
        ("put", robot.PEOPLE_TMP_NAME),
        ("promote",),
        ("get", faces.FACES_FILENAME),
        ("get", people.PEOPLE_FILENAME),
    ]
    assert robot.FACES_TMP_NAME not in fake.remote
    assert robot.PEOPLE_TMP_NAME not in fake.remote

    promoted = _faces_from_remote(fake, tmp_path)
    assert [record.name for record in promoted] == ["Lena"]
    assert promoted[0].embeddings == (_vector(1),)

    meta = store.get_sync_meta(settings)
    assert meta.last_faces_sha256 == fake.sha(faces.FACES_FILENAME)
    assert meta.last_people_sha256 == fake.sha(people.PEOPLE_FILENAME)
    assert meta.last_push_at is not None


def test_push_reports_a_race_when_the_promote_guard_trips(
    settings: Settings, fake: FakeRobot, tmp_path: Path
) -> None:
    """An enrollment that lands between the fetch and the promote must abort the promote."""
    _person(settings, "Lena", embeddings=[1])
    landed = _faces_content(tmp_path, [_record("f_new", "Sam", [5])])

    def enroll(target: FakeRobot) -> None:
        target.remote[faces.FACES_FILENAME] = landed

    fake.before_promote = enroll

    result = robot.push(settings)

    assert result.pushed is False
    assert isinstance(result.blocked_by, robot.PushRace)
    # The robot kept the enrollment, the staged files were cleaned up, and we
    # did not record a push that never happened.
    assert fake.remote[faces.FACES_FILENAME] == landed
    assert robot.FACES_TMP_NAME not in fake.remote
    assert robot.PEOPLE_TMP_NAME not in fake.remote
    assert store.get_sync_meta(settings) == store.SyncMeta()


def test_push_records_sync_meta_only_after_the_verify_fetch(
    settings: Settings, fake: FakeRobot, tmp_path: Path
) -> None:
    """A promote we cannot verify is not a push, and must not be recorded as one."""
    _person(settings, "Lena", embeddings=[1])
    corrupted = _faces_content(tmp_path, [_record("f_other", "Someone Else", [7])])

    def corrupt(target: FakeRobot) -> None:
        target.remote[faces.FACES_FILENAME] = corrupted

    fake.after_promote = corrupt

    with pytest.raises(robot.RobotError):
        robot.push(settings)

    assert store.get_sync_meta(settings) == store.SyncMeta()


def test_push_writes_the_whole_projection(settings: Settings, fake: FakeRobot, tmp_path: Path) -> None:
    """What lands on the robot is exactly what a local projection would produce."""
    _person(settings, "Lena", embeddings=[1, 2], facts=["likes tea"])
    _person(settings, "Sam", facts=["plays cello"])

    result = robot.push(settings)

    assert result.pushed is True
    assert result.faces_count == 1
    assert result.people_count == 2

    projection.project(settings, tmp_path / "local")
    assert fake.remote[faces.FACES_FILENAME] == faces.faces_path_for_instance(tmp_path / "local").read_text(
        encoding="utf-8"
    )


def test_push_guard_expects_an_absent_file_as_absent(
    settings: Settings, fake: FakeRobot, tmp_path: Path
) -> None:
    """The first push expects *no* remote file; a file appearing is still a race."""
    _person(settings, "Lena", embeddings=[1])

    def enroll(target: FakeRobot) -> None:
        target.remote[people.PEOPLE_FILENAME] = _people_content(tmp_path, [("Sam", None, ["plays cello"])])

    fake.before_promote = enroll

    result = robot.push(settings)

    assert isinstance(result.blocked_by, robot.PushRace)


# --------------------------------------------------------------------------
# import
# --------------------------------------------------------------------------


def _faces_from_remote(fake: FakeRobot, root: Path) -> list[faces.FaceRecord]:
    """Load the fake robot's face store with the robot's own reader."""
    directory = Path(tempfile.mkdtemp(dir=root))
    path = faces.faces_path_for_instance(directory)
    path.write_text(fake.remote[faces.FACES_FILENAME], encoding="utf-8")
    return faces._read_faces_file(path)


def test_apply_import_creates_a_person_whose_embedding_round_trips(
    settings: Settings, fake: FakeRobot, tmp_path: Path
) -> None:
    """A voice enrollment becomes a backend person, and projects back byte-identically."""
    fake.remote[faces.FACES_FILENAME] = _faces_content(tmp_path, [_record("f_new", "Sam", [5, 6])])
    fake.remote[people.PEOPLE_FILENAME] = _people_content(tmp_path, [("Sam", "f_new", ["plays cello"])])

    result = robot.apply_import(settings, robot.import_from_robot(settings))

    assert result.conflicts == []
    assert result.applied == 2  # the face, and one fact

    person = next(item for item in store.list_people(settings) if item.name == "Sam")
    assert person.face_id == "f_new"
    assert all(photo.synthetic for photo in person.photos)
    assert [fact.text for fact in person.facts] == ["plays cello"]

    projection.project(settings, tmp_path / "out")
    record = faces._read_faces_file(faces.faces_path_for_instance(tmp_path / "out"))[0]
    assert record.id == "f_new"
    assert record.embeddings == (_vector(5), _vector(6))


def test_apply_import_attaches_to_an_existing_unlinked_person(
    settings: Settings, fake: FakeRobot, tmp_path: Path
) -> None:
    """Codex R3-2: a name we already know and never linked gains the robot's face id."""
    person = _person(settings, "Sam", facts=["plays cello"])
    fake.remote[faces.FACES_FILENAME] = _faces_content(tmp_path, [_record("f_new", "sam", [5])])
    fake.remote[people.PEOPLE_FILENAME] = _people_content(tmp_path, [("sam", "f_new", [])])

    result = robot.apply_import(settings, robot.import_from_robot(settings))

    assert result.conflicts == []
    assert [item.name for item in store.list_people(settings)] == ["Sam"]
    reloaded = store.get_person(settings, person.id)
    assert reloaded is not None
    assert reloaded.face_id == "f_new"
    assert projection.embeddings_for(reloaded) == (_vector(5),)


def test_apply_import_reports_a_face_id_collision_as_a_conflict(
    settings: Settings, fake: FakeRobot, tmp_path: Path
) -> None:
    """Two different faces under one name is the operator's call, not ours."""
    person = _person(settings, "Sam", embeddings=[1], face_id="f_mine")
    fake.remote[faces.FACES_FILENAME] = _faces_content(tmp_path, [_record("f_theirs", "Sam", [5])])
    fake.remote[people.PEOPLE_FILENAME] = _people_content(tmp_path, [("Sam", "f_theirs", [])])

    result = robot.apply_import(settings, robot.import_from_robot(settings))

    assert result.applied == 0
    assert len(result.conflicts) == 1
    assert "Sam" in result.conflicts[0]
    assert "f_theirs" in result.conflicts[0]

    reloaded = store.get_person(settings, person.id)
    assert reloaded is not None
    assert reloaded.face_id == "f_mine"
    assert projection.embeddings_for(reloaded) == (_vector(1),)


def test_apply_import_appends_facts_oldest_first(settings: Settings, fake: FakeRobot, tmp_path: Path) -> None:
    """Imported facts keep the robot's order once the backend's newest-first store is read back."""
    _person(settings, "Lena", embeddings=[1], face_id="f_lena")
    fake.remote[faces.FACES_FILENAME] = _faces_content(tmp_path, [_record("f_lena", "Lena", [1])])
    fake.remote[people.PEOPLE_FILENAME] = _people_content(
        tmp_path, [("Lena", "f_lena", ["has a cat", "likes tea"])]
    )

    result = robot.apply_import(settings, robot.import_from_robot(settings))

    assert result.applied == 2
    person = next(item for item in store.list_people(settings) if item.name == "Lena")
    assert [fact.text for fact in person.facts] == ["likes tea", "has a cat"]


def test_apply_import_follows_the_face_link_across_a_rename(
    settings: Settings, fake: FakeRobot, tmp_path: Path
) -> None:
    """A person renamed on the Mac must not come back from the robot as a second person."""
    person = _person(settings, "Lena Ha", embeddings=[1], face_id="f_lena")
    fake.remote[faces.FACES_FILENAME] = _faces_content(tmp_path, [_record("f_lena", "Lena", [1])])
    fake.remote[people.PEOPLE_FILENAME] = _people_content(tmp_path, [("Lena", "f_lena", ["has a cat"])])

    diff = robot.import_from_robot(settings)
    assert diff.new_person_facts == [robot.RobotPersonFacts(name="Lena", face_id="f_lena", facts=["has a cat"])]

    result = robot.apply_import(settings, diff)

    assert result.conflicts == []
    assert [item.name for item in store.list_people(settings)] == ["Lena Ha"]
    reloaded = store.get_person(settings, person.id)
    assert reloaded is not None
    assert [fact.text for fact in reloaded.facts] == ["has a cat"]


def test_apply_import_creates_a_person_known_only_by_a_voice_fact(
    settings: Settings, fake: FakeRobot, tmp_path: Path
) -> None:
    """A fact about someone with no face record still has to reach the Mac."""
    fake.remote[people.PEOPLE_FILENAME] = _people_content(tmp_path, [("Ada", None, ["writes poetry"])])

    result = robot.apply_import(settings, robot.import_from_robot(settings))

    assert result.conflicts == []
    person = next(item for item in store.list_people(settings) if item.name == "Ada")
    assert person.face_id is None
    assert [fact.text for fact in person.facts] == ["writes poetry"]


def test_import_then_push_leaves_the_robot_holding_its_own_enrollment(
    settings: Settings, fake: FakeRobot, tmp_path: Path
) -> None:
    """End to end: enrollment blocks the push, the import unblocks it, the face survives."""
    _person(settings, "Lena", embeddings=[1], face_id="f_lena")
    fake.remote[faces.FACES_FILENAME] = _faces_content(
        tmp_path, [_record("f_lena", "Lena", [1]), _record("f_sam", "Sam", [5])]
    )
    fake.remote[people.PEOPLE_FILENAME] = _people_content(
        tmp_path, [("Lena", "f_lena", []), ("Sam", "f_sam", ["plays cello"])]
    )

    assert robot.push(settings).pushed is False
    robot.apply_import(settings, robot.import_from_robot(settings))
    assert robot.push(settings).pushed is True

    promoted = {record.id: record for record in _faces_from_remote(fake, tmp_path)}
    assert set(promoted) == {"f_lena", "f_sam"}
    assert promoted["f_sam"].embeddings == (_vector(5),)


# --------------------------------------------------------------------------
# the daemon's app API
# --------------------------------------------------------------------------


def test_robot_app_calls_hit_the_daemon_routes(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """The four app controls are the daemon's documented routes and nothing else."""
    got: list[str] = []
    posted: list[str] = []
    monkeypatch.setattr(robot, "_http_get", lambda url: (got.append(url), {"name": "reachy_companion"})[1])
    monkeypatch.setattr(robot, "_http_post", lambda url: (posted.append(url), {"ok": True})[1])

    assert robot.robot_app_status(settings) == {"name": "reachy_companion"}
    assert robot.robot_app_start(settings) == {"ok": True}
    assert robot.robot_app_stop(settings) == {"ok": True}
    assert robot.robot_app_restart(settings) == {"ok": True}

    base = "http://10.0.0.5:8000/api/apps"
    assert got == [f"{base}/current-app-status"]
    assert posted == [
        f"{base}/start-app/reachy_companion",
        f"{base}/stop-current-app",
        f"{base}/restart-current-app",
    ]
