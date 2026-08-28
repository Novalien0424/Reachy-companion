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
    download_error_for: str | None = None
    upload_error_for: str | None = None
    promote_error: bool = False
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
        assert kwargs["stdin"] is subprocess.DEVNULL
        argv = list(argv)
        assert "expect" not in argv[0]
        self.calls.append(tuple(argv))

        if argv[0] == "scp":
            return self._scp(argv)
        if argv[0] == "ssh":
            if argv[-1].startswith("rm -f"):
                return self._discard(argv)
            return self._promote(argv)
        raise AssertionError(f"unexpected program {argv[0]!r}")

    def _discard(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        self.log.append(("discard",))
        self.remote.pop(robot.FACES_TMP_NAME, None)
        self.remote.pop(robot.PEOPLE_TMP_NAME, None)
        return subprocess.CompletedProcess(argv, 0, "", "")

    def _scp(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        source, destination = argv[-2], argv[-1]
        if ":" in source:
            filename = source.rsplit("/", 1)[-1]
            self.log.append(("get", filename))
            if self.download_error is not None:
                return subprocess.CompletedProcess(argv, 1, "", self.download_error)
            if self.download_error_for == filename:
                # A reachable robot that still cannot hand this one file over.
                return subprocess.CompletedProcess(argv, 1, "", "scp: read error: Input/output error")
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
        if self.upload_error_for == filename:
            return subprocess.CompletedProcess(argv, 1, "", "scp: write: No space left on device")
        self.remote[filename] = Path(source).read_text(encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, "", "")

    def _promote(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        self.log.append(("promote",))
        assert "-o" in argv and "BatchMode=yes" in argv
        script = argv[-1]
        if self.promote_error:
            return subprocess.CompletedProcess(argv, 1, "", "sh: sha256sum: not found")
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


def test_push_clears_the_staged_files_when_the_promote_fails(
    settings: Settings, fake: FakeRobot, tmp_path: Path
) -> None:
    """A promote that fails for any reason but a race must not strand its staged files."""
    _person(settings, "Lena", embeddings=[1])
    fake.promote_error = True

    with pytest.raises(robot.RobotError):
        robot.push(settings)

    assert fake.log[-1] == ("discard",)
    assert robot.FACES_TMP_NAME not in fake.remote
    assert robot.PEOPLE_TMP_NAME not in fake.remote
    assert store.get_sync_meta(settings) == store.SyncMeta()


def test_push_clears_the_staged_file_when_the_second_upload_fails(
    settings: Settings, fake: FakeRobot, tmp_path: Path
) -> None:
    """The first file is already staged when the second transfer dies."""
    _person(settings, "Lena", embeddings=[1])
    fake.upload_error_for = robot.PEOPLE_TMP_NAME

    with pytest.raises(robot.RobotError):
        robot.push(settings)

    assert fake.log[-1] == ("discard",)
    assert robot.FACES_TMP_NAME not in fake.remote
    assert ("promote",) not in fake.log


def test_push_reports_the_people_it_could_not_carry(
    settings: Settings, fake: FakeRobot, tmp_path: Path
) -> None:
    """`ProjectionResult.skipped` reaches the operator through the push result."""
    _person(settings, "Lena", embeddings=[1])
    _person(settings, "Nobody")

    result = robot.push(settings)

    assert result.pushed is True
    assert result.skipped == ["Nobody"]


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


def test_apply_import_does_not_link_a_person_it_could_not_give_samples(
    settings: Settings, fake: FakeRobot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A half-finished face import must leave the record importable, not a linked empty person.

    A person linked to a face id with no samples projects as a *person* with no
    *face record* — the robot would stop recognizing someone it already knew.
    """
    fake.remote[faces.FACES_FILENAME] = _faces_content(tmp_path, [_record("f_new", "Sam", [5, 6])])
    written = count()
    real = store.add_synthetic_photo

    def flaky(settings_: Settings, person_id: str, embedding: Sequence[float]) -> store.BackendPhoto:
        if next(written) == 1:
            raise ValueError("the second sample could not be stored")
        return real(settings_, person_id, embedding)

    monkeypatch.setattr(robot.store, "add_synthetic_photo", flaky)

    result = robot.apply_import(settings, robot.import_from_robot(settings))

    assert result.applied == 0
    assert len(result.conflicts) == 1
    person = next(item for item in store.list_people(settings) if item.name == "Sam")
    assert person.face_id is None
    # Still unlinked, so the record reads as new and importing again finishes it.
    assert [entry.record_id for entry in robot.import_from_robot(settings).new_faces] == ["f_new"]


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
# removals: what the robot forgot
# --------------------------------------------------------------------------


def _face_id_of(settings: Settings, name: str) -> str:
    person = next(item for item in store.list_people(settings) if item.name == name)
    assert person.face_id is not None
    return person.face_id


def test_a_voice_forget_survives_the_next_push(settings: Settings, fake: FakeRobot, tmp_path: Path) -> None:
    """The whole point: a fact forgotten on the robot must not be written back.

    Push, forget one fact by voice on the robot, and the next push is refused
    until the removal is imported — after which the Mac no longer holds the fact,
    the projection omits it, and the push goes through.
    """
    person = _person(settings, "Lena", embeddings=[1], facts=["likes tea", "has a cat"])
    assert robot.push(settings).pushed is True
    assert (robot.last_push_dir(settings) / people.PEOPLE_FILENAME).is_file()

    face_id = _face_id_of(settings, "Lena")
    fake.remote[people.PEOPLE_FILENAME] = _people_content(tmp_path, [("Lena", face_id, ["likes tea"])])

    diff = robot.robot_diff(settings)
    assert diff.new_person_facts == []
    assert diff.removed_person_facts == [
        robot.RobotPersonFacts(name="Lena", face_id=face_id, facts=["has a cat"])
    ]
    assert not diff.empty

    blocked = robot.push(settings)
    assert blocked.pushed is False
    assert blocked.blocked_by is not None

    result = robot.apply_import(settings, robot.import_from_robot(settings))
    assert result.applied == 1
    assert result.conflicts == []

    reloaded = store.get_person(settings, person.id)
    assert reloaded is not None
    assert [fact.text for fact in reloaded.facts] == ["likes tea"]

    projection.project(settings, tmp_path / "out")
    projected = people.list_people(tmp_path / "out")
    assert [fact.text for fact in projected[0].facts] == ["likes tea"]

    assert robot.robot_diff(settings).empty
    after = robot.push(settings)
    assert after.pushed is True
    assert "has a cat" not in fake.remote[people.PEOPLE_FILENAME]


def test_a_fact_added_on_the_mac_is_not_a_removal(settings: Settings, fake: FakeRobot, tmp_path: Path) -> None:
    """A fact the backend has but never pushed is an addition, and pushes normally."""
    person = _person(settings, "Lena", embeddings=[1], facts=["likes tea"])
    assert robot.push(settings).pushed is True

    store.add_fact(settings, person.id, "has a cat")

    diff = robot.robot_diff(settings)
    assert diff.removed_person_facts == []
    assert diff.empty

    assert robot.push(settings).pushed is True
    assert "has a cat" in fake.remote[people.PEOPLE_FILENAME]


def test_no_snapshot_means_no_removal_detection(settings: Settings, fake: FakeRobot, tmp_path: Path) -> None:
    """Never pushed: there is nothing to compare against, so nothing reads as forgotten."""
    _person(settings, "Lena", embeddings=[1], facts=["likes tea", "has a cat"], face_id="f_lena")
    fake.remote[faces.FACES_FILENAME] = _faces_content(tmp_path, [_record("f_lena", "Lena", [1])])
    fake.remote[people.PEOPLE_FILENAME] = _people_content(tmp_path, [("Lena", "f_lena", [])])

    assert robot.robot_diff(settings).removed_person_facts == []


def test_a_fact_evicted_by_the_twenty_fact_cap_is_not_a_removal(
    settings: Settings, fake: FakeRobot, tmp_path: Path
) -> None:
    """At the robot's fact cap, an absence is the store making room — not somebody forgetting.

    The two are indistinguishable in the file, and deleting a fact from the Mac
    that nobody asked to forget is the worse mistake, so removals are only read
    below the cap. The voice-added fact is still seen as new.
    """
    _person(
        settings,
        "Lena",
        embeddings=[1],
        facts=[f"fact {index:02d}" for index in range(people.MAX_FACTS_PER_PERSON)],
    )
    assert robot.push(settings).pushed is True

    face_id = _face_id_of(settings, "Lena")
    # 21 facts replayed into a store that keeps 20: the oldest falls out.
    fake.remote[people.PEOPLE_FILENAME] = _people_content(
        tmp_path,
        [("Lena", face_id, [f"fact {index:02d}" for index in range(people.MAX_FACTS_PER_PERSON + 1)])],
    )

    diff = robot.robot_diff(settings)

    assert diff.removed_person_facts == []
    assert [entry.facts for entry in diff.new_person_facts] == [["fact 20"]]


def test_a_person_evicted_from_the_robot_is_not_a_removal(
    settings: Settings, fake: FakeRobot, tmp_path: Path
) -> None:
    """The robot's twelve-person LRU dropping a record is not somebody forgetting a fact."""
    _person(settings, "Lena", embeddings=[1], facts=["likes tea"])
    assert robot.push(settings).pushed is True

    fake.remote[people.PEOPLE_FILENAME] = _people_content(tmp_path, [("Sam", None, ["plays cello"])])

    diff = robot.robot_diff(settings)

    assert diff.removed_person_facts == []
    assert [entry.name for entry in diff.new_person_facts] == ["Sam"]


# --------------------------------------------------------------------------
# merged people: aliases and former face ids (addendum Feature 1)
# --------------------------------------------------------------------------


def _merged(settings: Settings, target: str, source: str) -> store.BackendPerson:
    """Merge two people by name and return the survivor."""
    by_name = {person.name: person for person in store.list_people(settings)}
    return store.merge_people(settings, by_name[target].id, by_name[source].id)


def test_a_robot_name_that_is_now_an_alias_resolves_to_the_survivor(
    settings: Settings, fake: FakeRobot, tmp_path: Path
) -> None:
    """The robot still says "Lena"; the Mac now calls her "Linna" and knows both."""
    _person(settings, "Linna", embeddings=[1], face_id="f_linna")
    _person(settings, "Lena", facts=["likes tea"])
    survivor = _merged(settings, "Linna", "Lena")
    assert survivor.aliases == ("Lena",)

    fake.remote[faces.FACES_FILENAME] = _faces_content(tmp_path, [_record("f_linna", "Linna", [1])])
    fake.remote[people.PEOPLE_FILENAME] = _people_content(
        tmp_path, [("Lena", None, ["likes tea", "has a cat"])]
    )

    diff = robot.robot_diff(settings)

    # "likes tea" is already known *through the alias*, so only the new fact shows.
    assert diff.new_person_facts == [robot.RobotPersonFacts(name="Lena", face_id=None, facts=["has a cat"])]

    result = robot.apply_import(settings, diff)

    assert result.conflicts == []
    assert [person.name for person in store.list_people(settings)] == ["Linna"]
    reloaded = store.get_person(settings, survivor.id)
    assert reloaded is not None
    assert [fact.text for fact in reloaded.facts] == ["has a cat", "likes tea"]


def test_a_former_face_id_is_a_known_face_not_a_new_one(
    settings: Settings, fake: FakeRobot, tmp_path: Path
) -> None:
    """A record the survivor used to carry is not a stranger enrolling for the first time."""
    _person(settings, "Linna", embeddings=[1], face_id="f_linna")
    _person(settings, "Lena", embeddings=[2], face_id="f_lena")
    survivor = _merged(settings, "Linna", "Lena")
    assert survivor.former_face_ids == ("f_lena",)

    fake.remote[faces.FACES_FILENAME] = _faces_content(
        tmp_path, [_record("f_linna", "Linna", [1]), _record("f_lena", "Lena", [2])]
    )
    fake.remote[people.PEOPLE_FILENAME] = _people_content(
        tmp_path, [("Linna", "f_linna", []), ("Lena", "f_lena", [])]
    )

    diff = robot.robot_diff(settings)

    assert diff.new_faces == []
    assert diff.changed_faces == []
    assert diff.empty


def test_stored_embeddings_never_counts_a_display_only_photo(settings: Settings) -> None:
    """Mirrors `projection.embeddings_for`: a display-only photo is never "known" content.

    Nothing today gives a display-only photo an embedding, but if a future bug
    or a hand-edited store ever did, it must not let a robot record carrying
    that same sample read as already known — the picture was never enrolled.
    """
    person = _person(settings, "Lena", embeddings=[1])
    snapshot = store.add_display_photo(settings, person.id, store.ROBOT_SNAPSHOT_DISPLAY_NAME, b"jpeg-bytes")
    store.set_photo_embedding(settings, person.id, snapshot.id, _vector(9), None)

    reloaded = store.get_person(settings, person.id)
    assert reloaded is not None
    assert robot._stored_embeddings(reloaded) == {_vector(1)}


def test_a_sample_outside_the_projected_window_is_still_known_content(
    settings: Settings, fake: FakeRobot, tmp_path: Path
) -> None:
    """Codex A1-2: "changed" means unknown to ANY stored photo, not absent from the newest three.

    This is what retires the old three-slot re-block: samples the backend holds
    but no longer projects are still content it knows, so a push is not refused
    for them.
    """
    person = _person(settings, "Lena", embeddings=[1], face_id="f_lena")
    for seed in (7, 8, 9):
        store.add_synthetic_photo(settings, person.id, _vector(seed))

    reloaded = store.get_person(settings, person.id)
    assert reloaded is not None
    assert _vector(1) not in projection.embeddings_for(reloaded)

    fake.remote[faces.FACES_FILENAME] = _faces_content(tmp_path, [_record("f_lena", "Lena", [1])])
    fake.remote[people.PEOPLE_FILENAME] = _people_content(tmp_path, [("Lena", "f_lena", [])])

    assert robot.robot_diff(settings).empty
    assert robot.push(settings).pushed is True


def test_two_robot_ids_for_one_survivor_import_once_then_push_clean(
    settings: Settings, fake: FakeRobot, tmp_path: Path
) -> None:
    """Codex A1-2: more than three samples across two records must not block forever.

    The push collapses both robot records into the survivor's single projected
    one; under the old newest-three rule the un-projected samples would read as
    unknown again on the very next diff and the gate would never reopen.
    """
    _person(settings, "Linna", embeddings=[3], face_id="f_linna")
    _person(settings, "Lena", embeddings=[1], face_id="f_lena")
    survivor = _merged(settings, "Linna", "Lena")

    fake.remote[faces.FACES_FILENAME] = _faces_content(
        tmp_path, [_record("f_linna", "Linna", [3, 4]), _record("f_lena", "Lena", [1, 2])]
    )
    fake.remote[people.PEOPLE_FILENAME] = _people_content(
        tmp_path, [("Linna", "f_linna", []), ("Lena", "f_lena", [])]
    )

    blocked = robot.push(settings)
    assert blocked.pushed is False
    assert isinstance(blocked.blocked_by, robot.RobotDiff)
    assert {entry.record_id for entry in blocked.blocked_by.changed_faces} == {"f_linna", "f_lena"}
    assert blocked.blocked_by.new_faces == []

    result = robot.apply_import(settings, robot.import_from_robot(settings))
    assert result.conflicts == []

    reloaded = store.get_person(settings, survivor.id)
    assert reloaded is not None
    stored = {photo.embedding for photo in reloaded.photos}
    assert {_vector(seed) for seed in (1, 2, 3, 4)} <= stored

    # One import is enough: nothing unknown is left, even though six samples do
    # not fit the robot's three-slot window.
    assert robot.robot_diff(settings).empty
    assert robot.push(settings).pushed is True

    promoted = _faces_from_remote(fake, tmp_path)
    assert [record.id for record in promoted] == ["f_linna"]
    assert [record.name for record in promoted] == ["Linna"]
    assert len(promoted[0].embeddings) == projection.MAX_PROJECTED_EMBEDDINGS
    assert robot.robot_diff(settings).empty


def test_a_re_enrollment_under_an_alias_is_attached_not_duplicated(
    settings: Settings, fake: FakeRobot, tmp_path: Path
) -> None:
    """Codex A1-1: the new robot id is persisted, or the gate blocks forever.

    A voice re-enrollment under the merged-away name mints a *fresh* record id.
    Attaching it to the survivor without remembering the id would leave the next
    diff reporting it as a new face again, on every diff, for good.
    """
    _person(settings, "Linna", embeddings=[1], face_id="f_linna")
    _person(settings, "Lena", facts=["likes tea"])
    survivor = _merged(settings, "Linna", "Lena")

    fake.remote[faces.FACES_FILENAME] = _faces_content(
        tmp_path, [_record("f_linna", "Linna", [1]), _record("f_fresh", "Lena", [5])]
    )
    fake.remote[people.PEOPLE_FILENAME] = _people_content(
        tmp_path, [("Linna", "f_linna", []), ("Lena", "f_fresh", [])]
    )

    diff = robot.robot_diff(settings)
    assert [entry.record_id for entry in diff.new_faces] == ["f_fresh"]

    result = robot.apply_import(settings, diff)
    assert result.conflicts == []
    assert [person.name for person in store.list_people(settings)] == ["Linna"]

    reloaded = store.get_person(settings, survivor.id)
    assert reloaded is not None
    assert reloaded.face_id == "f_linna"
    assert "f_fresh" in reloaded.former_face_ids
    assert _vector(5) in {photo.embedding for photo in reloaded.photos}

    # Known now, and known after the push that collapses both records into one.
    assert robot.robot_diff(settings).empty
    assert robot.push(settings).pushed is True
    assert [record.id for record in _faces_from_remote(fake, tmp_path)] == ["f_linna"]
    assert robot.robot_diff(settings).empty


def test_the_post_merge_push_cycle_leaves_the_robot_holding_only_the_survivor(
    settings: Settings, fake: FakeRobot, tmp_path: Path
) -> None:
    """The operator's whole story: two records on the robot, one person afterwards."""
    _person(settings, "Linna", embeddings=[1], facts=["likes tea"], face_id="f_linna")
    _person(settings, "Lena", embeddings=[2], facts=["has a cat"], face_id="f_lena")
    fake.remote[faces.FACES_FILENAME] = _faces_content(
        tmp_path, [_record("f_linna", "Linna", [1]), _record("f_lena", "Lena", [2])]
    )
    fake.remote[people.PEOPLE_FILENAME] = _people_content(
        tmp_path, [("Linna", "f_linna", ["likes tea"]), ("Lena", "f_lena", ["has a cat"])]
    )
    assert robot.push(settings).pushed is True

    survivor = _merged(settings, "Linna", "Lena")
    assert survivor.aliases == ("Lena",)
    assert survivor.former_face_ids == ("f_lena",)

    assert robot.robot_diff(settings).empty
    assert robot.push(settings).pushed is True

    promoted = _faces_from_remote(fake, tmp_path)
    assert [record.id for record in promoted] == ["f_linna"]
    assert set(promoted[0].embeddings) == {_vector(1), _vector(2)}
    assert "Lena" not in fake.remote[people.PEOPLE_FILENAME]
    assert robot.robot_diff(settings).empty


def test_a_fact_forgotten_under_a_former_face_id_is_still_a_removal(
    settings: Settings, fake: FakeRobot, tmp_path: Path
) -> None:
    """Codex A2-1's payoff: the robot forgets under the old id, the Mac still hears it."""
    _person(settings, "Lena", embeddings=[2], facts=["likes tea", "has a cat"], face_id="f_lena")
    assert robot.push(settings).pushed is True

    _person(settings, "Linna", embeddings=[1], face_id="f_linna")
    survivor = _merged(settings, "Linna", "Lena")
    assert survivor.former_face_ids == ("f_lena",)

    # The robot forgot one fact by voice, under the record it still holds.
    fake.remote[people.PEOPLE_FILENAME] = _people_content(tmp_path, [("Lena", "f_lena", ["likes tea"])])

    diff = robot.robot_diff(settings)
    assert diff.removed_person_facts == [
        robot.RobotPersonFacts(name="Lena", face_id="f_lena", facts=["has a cat"])
    ]

    result = robot.apply_import(settings, diff)

    assert result.conflicts == []
    reloaded = store.get_person(settings, survivor.id)
    assert reloaded is not None
    assert [fact.text for fact in reloaded.facts] == ["likes tea"]


def test_a_face_id_collision_under_the_canonical_name_is_still_a_conflict(
    settings: Settings, fake: FakeRobot, tmp_path: Path
) -> None:
    """A1-1 loosens the *alias* case only; two faces under one real name stay the operator's call."""
    person = _person(settings, "Sam", embeddings=[1], face_id="f_mine")
    fake.remote[faces.FACES_FILENAME] = _faces_content(tmp_path, [_record("f_theirs", "Sam", [5])])
    fake.remote[people.PEOPLE_FILENAME] = _people_content(tmp_path, [("Sam", "f_theirs", [])])

    result = robot.apply_import(settings, robot.import_from_robot(settings))

    assert result.applied == 0
    assert len(result.conflicts) == 1
    reloaded = store.get_person(settings, person.id)
    assert reloaded is not None
    assert reloaded.former_face_ids == ()


# --------------------------------------------------------------------------
# enrollment snapshots (addendum Feature 2)
# --------------------------------------------------------------------------

# The shape the robot actually generates, which is the only shape the snapshot
# fetch will interpolate into a remote path.
_SNAPSHOT_RECORD = "f_1700000000000_ab12cd"


def _snapshot_calls(fake: FakeRobot) -> list[tuple[str, ...]]:
    """Every remote call that reached for a snapshot file."""
    return [call for call in fake.calls if any(robot.SNAPSHOT_DIRNAME in part for part in call)]


def _display_photos(settings: Settings, name: str) -> list[store.BackendPhoto]:
    person = next(item for item in store.list_people(settings) if item.name == name)
    return [photo for photo in person.photos if photo.display_only]


def _enrolled(fake: FakeRobot, tmp_path: Path, *, snapshot: str | None) -> None:
    """Put one three-sample voice enrollment on the fake robot, with or without its snapshot."""
    fake.remote[faces.FACES_FILENAME] = _faces_content(tmp_path, [_record(_SNAPSHOT_RECORD, "Sam", [5, 6, 7])])
    fake.remote[people.PEOPLE_FILENAME] = _people_content(tmp_path, [("Sam", _SNAPSHOT_RECORD, [])])
    if snapshot is not None:
        fake.remote[f"{_SNAPSHOT_RECORD}.jpg"] = snapshot


def test_an_imported_face_brings_its_enrollment_snapshot_as_a_display_only_photo(
    settings: Settings, fake: FakeRobot, tmp_path: Path
) -> None:
    """The operator's picture: fetched beside the face, never a recognition sample.

    Three samples is the robot's full window, so this is also the "import then
    push immediately" cycle: the snapshot must not make the push think the robot
    holds something unknown, and the projection must not carry it back.
    """
    _enrolled(fake, tmp_path, snapshot="jpeg-bytes-sam")

    result = robot.apply_import(settings, robot.import_from_robot(settings))

    assert result.conflicts == []
    fetched = _snapshot_calls(fake)
    assert len(fetched) == 1
    assert fetched[0][1].endswith(f"{robot.SNAPSHOT_DIRNAME}/{_SNAPSHOT_RECORD}.jpg")

    snapshots = _display_photos(settings, "Sam")
    assert len(snapshots) == 1
    assert snapshots[0].display_name == store.ROBOT_SNAPSHOT_DISPLAY_NAME
    assert snapshots[0].embedding is None
    assert snapshots[0].error is None
    assert snapshots[0].synthetic is False

    person = next(item for item in store.list_people(settings) if item.name == "Sam")
    stored = store.photo_path(settings, person.id, snapshots[0])
    assert stored is not None
    assert stored.read_bytes() == b"jpeg-bytes-sam"

    # The recognition samples are still exactly the robot's own three.
    assert projection.embeddings_for(person) == (_vector(5), _vector(6), _vector(7))
    assert robot.robot_diff(settings).empty
    assert robot.push(settings).pushed is True
    assert robot.robot_diff(settings).empty


def test_re_importing_the_same_snapshot_adds_no_second_photo(
    settings: Settings, fake: FakeRobot, tmp_path: Path
) -> None:
    """Content dedupe, against an existing *real* photo as well as an imported one."""
    person = _person(settings, "Sam", embeddings=[5], face_id=_SNAPSHOT_RECORD)
    uploaded = store.add_photo(settings, person.id, "sam.jpg", b"jpeg-bytes-sam")
    _enrolled(fake, tmp_path, snapshot="jpeg-bytes-sam")

    robot.apply_import(settings, robot.import_from_robot(settings))
    robot.apply_import(settings, robot.import_from_robot(settings))

    reloaded = store.get_person(settings, person.id)
    assert reloaded is not None
    with_bytes = [photo for photo in reloaded.photos if photo.stored_as is not None]
    assert [photo.id for photo in with_bytes] == [uploaded.id]
    assert _display_photos(settings, "Sam") == []
    assert len(list(store.photo_dir(settings, person.id).iterdir())) == 1


def test_a_robot_with_no_snapshot_yet_imports_its_face_unchanged(
    settings: Settings, fake: FakeRobot, tmp_path: Path
) -> None:
    """Enrolled before the feature, or not yet redeployed: a missing file is the normal case."""
    _enrolled(fake, tmp_path, snapshot=None)

    result = robot.apply_import(settings, robot.import_from_robot(settings))

    assert result.conflicts == []
    assert result.applied == 1
    assert _display_photos(settings, "Sam") == []
    assert robot.robot_diff(settings).empty


def test_a_failed_snapshot_transfer_never_fails_the_face_import(
    settings: Settings, fake: FakeRobot, tmp_path: Path
) -> None:
    """The face is the content; the picture is a nicety, and it may not take the face down."""
    _enrolled(fake, tmp_path, snapshot="jpeg-bytes-sam")
    fake.download_error_for = f"{_SNAPSHOT_RECORD}.jpg"

    result = robot.apply_import(settings, robot.import_from_robot(settings))

    assert result.conflicts == []
    assert result.applied == 1
    assert _display_photos(settings, "Sam") == []
    person = next(item for item in store.list_people(settings) if item.name == "Sam")
    assert person.face_id == _SNAPSHOT_RECORD
    assert projection.embeddings_for(person) == (_vector(5), _vector(6), _vector(7))


def test_the_snapshot_follows_a_changed_face_and_an_alias_attach(
    settings: Settings, fake: FakeRobot, tmp_path: Path
) -> None:
    """Every applied face gets its picture: a re-enrollment's new samples, and an alias attach."""
    _person(settings, "Linna", embeddings=[1], face_id="f_linna")
    _person(settings, "Lena", facts=["likes tea"])
    survivor = _merged(settings, "Linna", "Lena")

    fake.remote[faces.FACES_FILENAME] = _faces_content(
        tmp_path, [_record("f_linna", "Linna", [1, 2]), _record(_SNAPSHOT_RECORD, "Lena", [5])]
    )
    fake.remote[people.PEOPLE_FILENAME] = _people_content(
        tmp_path, [("Linna", "f_linna", []), ("Lena", _SNAPSHOT_RECORD, [])]
    )
    fake.remote[f"{_SNAPSHOT_RECORD}.jpg"] = "jpeg-bytes-lena"

    diff = robot.import_from_robot(settings)
    assert [entry.record_id for entry in diff.new_faces] == [_SNAPSHOT_RECORD]
    assert [entry.record_id for entry in diff.changed_faces] == ["f_linna"]

    result = robot.apply_import(settings, diff)

    assert result.conflicts == []
    # `f_linna` is not the generated shape, so only the alias attach fetched.
    assert len(_snapshot_calls(fake)) == 1
    reloaded = store.get_person(settings, survivor.id)
    assert reloaded is not None
    assert [photo.display_only for photo in reloaded.photos].count(True) == 1


@pytest.mark.parametrize(
    "record_id",
    [
        "f_1700000000000_ab/cd",
        "f_1700000000000_ab\\cd",
        "../../../etc/passwd",
        "f_1700000000000_ab cd",
        "f_1700000000000_'ab'",
        'f_1700000000000_"ab"',
        "f_1700000000000_$(id)",
        "f_1700000000000_ab;id",
        "f_1700000000000_ab*d",
        "f_1700000000000_ab?d",
        "f_1700000000000_AB12CD",
        "f_notanepoch_ab12cd",
        "f_1700000000000_ab12cde",
        "f_1700000000000_abc123\n",
    ],
)
def test_a_record_id_that_is_not_the_generated_shape_never_reaches_a_remote_path(
    settings: Settings, fake: FakeRobot, tmp_path: Path, record_id: str
) -> None:
    """Codex A2-5 / A3-2: the robot's JSON is hand-editable, so the id is validated, not trusted.

    An id the robot could not have generated skips the snapshot **only** — the
    face itself is real content and still imports.
    """
    fake.remote[faces.FACES_FILENAME] = _faces_content(tmp_path, [_record(record_id, "Sam", [5])])
    fake.remote[people.PEOPLE_FILENAME] = _people_content(tmp_path, [("Sam", record_id, [])])

    result = robot.apply_import(settings, robot.import_from_robot(settings))

    assert result.conflicts == []
    assert _snapshot_calls(fake) == []
    assert _display_photos(settings, "Sam") == []
    person = next(item for item in store.list_people(settings) if item.name == "Sam")
    # The store trims whitespace on any string field it persists (`_clean_str`),
    # which only bites the trailing-newline id here — every other hostile shape
    # round-trips unchanged.
    assert person.face_id == record_id.strip()


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
