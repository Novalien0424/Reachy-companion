"""Guarded sync between the Mac store and the robot's two store files.

The robot is reached three ways and no others: `scp` to move the two JSON files,
one `ssh` command to promote them, and the daemon's HTTP app API on port 8000 to
start or stop the app. Every remote call is `subprocess.run` with plain
`ssh -o BatchMode=yes` / plain `scp` and a 20 s timeout — **never `expect`**: a
bulk transfer through expect's pty stalls indefinitely, a failure this project
already paid two days for and recorded twice.

**The gate.** The robot writes to its own stores: a face enrolled by voice, a
fact added mid-conversation. A push overwrites both files wholesale, so it is
refused exactly when the robot holds content the backend does not know:

    blocked = (drift or never-pushed) and not robot_diff(...).empty

Both halves matter. Without the second, a push after any robot-side change would
be refused forever. Without the first, deleting a person on the Mac could never
be pushed — the robot would still hold them, they would read as "unknown
content", and the deletion would be un-pushable. The way out of a block is
always the same: import the robot's content, then push. After an import the
hashes are stale by design ("imported drift") but the content is known, so the
push proceeds.

**Removals are content too.** The robot's voice `forget` deletes a fact from
`people.v1.json`. A deletion leaves no trace in the file it was deleted from, so
it can only be seen by comparing against what the last push *left* there — which
is why every verified push snapshots its own two files under
`data_dir/last_push/`. A fact in that snapshot, absent from the robot now, whose
person record still exists on the robot, and which the backend still holds, is a
deliberate removal: it blocks the push and is applied to the Mac store by an
import, exactly like an addition. Without this, the next push would silently
write the forgotten fact back.

*Deliberately not modelled:* face removals. The robot has no person-deletion
tool, so a face record can only ever appear or gain samples, never be dropped by
a person talking to the robot.

**Known content is what the store holds anywhere.** A face record counts as
*changed* exactly when it carries an embedding that is in none of the mapped
person's stored photos — not merely one that fell outside the newest-three window
the projection emits. The two differ whenever the Mac holds more than three
samples for someone, which a merge makes ordinary, and reading the window would
mean a record that re-blocks the push on every diff with nothing left to import.
The projection still emits three; what this test asks is only whether the robot
is holding something the Mac has never seen.

That is also why the push may legitimately *collapse* records. Two robot ids —
the survivor's and one it inherited through a merge — both map to one person, and
after the import neither holds anything unknown, so the push proceeds and writes
the single projected record back over both.

**A merged-away name is still a name.** Aliases and `former_face_ids` live only
on the Mac and are never projected, but every resolution here reads them: a robot
fact or face under "Lena" belongs to the person now called "Linna", and a record
id the survivor used to carry is a known face rather than a stranger. When the
robot re-enrolls under an alias it mints a *fresh* id; the survivor keeps its
primary link and the new id is recorded as a former one, or the next diff would
report the same face as new again, forever.

**The promote is guarded.** The window between reading the robot's files and
overwriting them is a real race — an enrollment can land inside it. So both
files are staged as `.faces.push.tmp` / `.people.push.tmp` and promoted by ONE
ssh command that first re-checks both current files against the sha256s captured
at fetch time (an absent file is expected to still be absent) and aborts with a
distinct exit code otherwise, which `push` reports as a race rather than a
failure. `mv` within one directory is atomic per file.

*Accepted residual risk:* the two `mv`s are not atomic **together**. For the
microseconds between them the robot could read a new `faces.v1.json` beside the
old `people.v1.json`. The app only ever reads these files, the worst outcome is
one greeting built from a name whose facts arrive a moment later, and the stores
are independently valid at every instant. A single-file or two-phase-commit
scheme was judged not worth its complexity for a POC.

Nothing but these two JSON files ever leaves the Mac: photo bytes stay in
`data_dir/photos`, and only the embeddings derived from them are projected.
"""

from __future__ import annotations
import os
import re
import time
import hashlib
import logging
import tempfile
import subprocess
from typing import Final
from pathlib import Path
from dataclasses import field, dataclass
from collections.abc import Sequence

import httpx

from reachy_companion import faces, people
from backend import store, projection
from backend.config import Settings


logger = logging.getLogger(__name__)

# Staged names, promoted by the guarded ssh command below. The leading dot keeps
# them out of the way of anything globbing the instance directory.
FACES_TMP_NAME: Final[str] = ".faces.push.tmp"
PEOPLE_TMP_NAME: Final[str] = ".people.push.tmp"

FACES_KEY: Final[str] = "faces"
PEOPLE_KEY: Final[str] = "people"

# Where a verified push keeps a copy of what it wrote, so the next diff can see
# what the robot has since deleted.
LAST_PUSH_DIRNAME: Final[str] = "last_push"

# Every remote call is bounded. 20 s is generous for two ~40 KB files over the
# robot's wifi and short enough that a wedged link fails a request instead of
# hanging a backend worker.
SSH_TIMEOUT_SECONDS: Final[int] = 20
CONNECT_TIMEOUT_SECONDS: Final[int] = 10
HTTP_TIMEOUT_SECONDS: Final[float] = 20.0

# The promote's own exit code: distinct from ssh's 255 and from the shell's 1/2,
# so "the robot changed under us" is never confused with "the command failed".
PROMOTE_RACE_EXIT: Final[int] = 9

# What the guard expects to find in place of a file that was absent at fetch.
ABSENT: Final[str] = "absent"

_SHA256_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{64}$")
# Read by the tests to prove the promote really re-checks both files.
_EXPECTED_SHA_PATTERN: Final[re.Pattern[str]] = re.compile(
    rf"expected_(faces|people)='([0-9a-f]{{64}}|{ABSENT})'"
)

DAEMON_PORT: Final[int] = 8000
APP_NAME: Final[str] = "reachy_companion"


class RobotError(RuntimeError):
    """The robot could not be reached, or a remote command failed."""


class RobotVerifyError(RobotError):
    """The promote reported success but the robot does not hold what we sent."""


# --------------------------------------------------------------------------
# what a sync can report
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class DriftState:
    """Whether the robot's files still hash to what our last verified push left."""

    faces_changed: bool
    people_changed: bool
    never_pushed: bool

    @property
    def any_change(self) -> bool:
        """True when the robot may hold writes of its own."""
        return self.faces_changed or self.people_changed or self.never_pushed


@dataclass(frozen=True)
class RobotFace:
    """One face record living on the robot, with its identity intact."""

    record_id: str
    name: str
    embeddings: tuple[tuple[float, ...], ...]


@dataclass(frozen=True)
class RobotPersonFacts:
    """Facts the robot holds for one person that the backend does not."""

    name: str
    face_id: str | None
    facts: list[str]


@dataclass(frozen=True)
class RobotDiff:
    """The content the robot holds that the backend does not know about.

    `removed_person_facts` is the mirror image of the other three: facts the last
    push left on the robot that are gone from it now. Its `facts` are the removed
    texts, so an import knows exactly what to delete from the Mac store.
    """

    new_faces: list[RobotFace]
    changed_faces: list[RobotFace]
    new_person_facts: list[RobotPersonFacts]
    removed_person_facts: list[RobotPersonFacts] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        """True when the robot holds no change the backend has not accounted for."""
        return not (
            self.new_faces or self.changed_faces or self.new_person_facts or self.removed_person_facts
        )


@dataclass(frozen=True)
class PushRace:
    """The robot's stores changed between the fetch and the promote."""

    message: str


@dataclass(frozen=True)
class PushResult:
    """What one push did, or what stopped it.

    `skipped` carries `ProjectionResult.skipped` through to the caller: the
    people that reached the robot as nothing at all, which is the one thing a
    successful push still needs to tell the operator.
    """

    pushed: bool
    faces_count: int
    people_count: int
    blocked_by: object | None
    skipped: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ImportResult:
    """What one import applied, and what it refused to guess at."""

    applied: int
    conflicts: list[str]


# --------------------------------------------------------------------------
# running remote commands
# --------------------------------------------------------------------------


def _target(settings: Settings) -> str:
    """Return `user@host`, or raise when the robot is not configured."""
    host = settings.reachy_host.strip()
    user = settings.reachy_ssh_user.strip()
    if not host or not user:
        raise RobotError("The robot is not configured; set REACHY_HOST and REACHY_SSH_USER in the repo `.env`.")
    return f"{user}@{host}"


def _remote_dir(settings: Settings) -> str:
    """Return the robot-side instance directory, safe to embed in a shell script."""
    directory = settings.instance_dir.rstrip("/")
    if not directory or "'" in directory or any(character.isspace() for character in directory):
        raise RobotError(f"Refusing to build a remote command for the instance path {settings.instance_dir!r}.")
    return directory


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    """Run one remote command, bounded, never through a pty wrapper.

    `stdin` is closed: `BatchMode=yes` already refuses to prompt, and a remote
    command that reads stdin anyway must not be handed the backend process's own.
    """
    try:
        return subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=SSH_TIMEOUT_SECONDS,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as exc:
        raise RobotError(f"`{argv[0]}` to the robot timed out after {SSH_TIMEOUT_SECONDS}s.") from exc
    except OSError as exc:
        raise RobotError(f"Could not run `{argv[0]}`: {exc}") from exc


def _looks_absent(stderr: str) -> bool:
    """Tell "the robot has no such file" apart from "the robot is unreachable".

    scp exits 1 for both, so the message is all there is to go on. Guessing wrong
    in the safe direction matters: an unreachable robot read as "holds nothing"
    would let a push sail through the gate, so anything that is not clearly a
    missing file is raised.
    """
    lowered = stderr.casefold()
    return "no such file" in lowered or "not a regular file" in lowered


def _download(settings: Settings, filename: str, destination: Path) -> Path | None:
    """Fetch one robot store file, returning None when the robot does not have it.

    A failed transfer removes whatever it left behind. Some scp builds create and
    truncate the local file before the remote open fails, and the diff reads the
    fetch *directory* — a stray half-written file there would read as robot
    content, which is the one thing the gate must never get wrong.
    """
    completed = _run(["scp", f"{_target(settings)}:{_remote_dir(settings)}/{filename}", str(destination)])
    if completed.returncode == 0:
        return destination

    destination.unlink(missing_ok=True)
    if _looks_absent(completed.stderr):
        logger.info("The robot has no %s yet.", filename)
        return None
    raise RobotError(f"Could not fetch {filename} from the robot: {completed.stderr.strip() or 'scp failed'}")


def _upload(settings: Settings, source: Path, filename: str) -> None:
    """Stage one file in the robot's instance directory under a temporary name."""
    completed = _run(["scp", str(source), f"{_target(settings)}:{_remote_dir(settings)}/{filename}"])
    if completed.returncode != 0:
        raise RobotError(f"Could not stage {filename} on the robot: {completed.stderr.strip() or 'scp failed'}")


def _ssh(settings: Settings, script: str) -> subprocess.CompletedProcess[str]:
    """Run one command on the robot over a non-interactive ssh."""
    return _run(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            f"ConnectTimeout={CONNECT_TIMEOUT_SECONDS}",
            _target(settings),
            script,
        ]
    )


def _discard_staged(settings: Settings) -> None:
    """Remove both staged files from the robot, best effort.

    Called on every failure path that leaves them there. A staged file is never
    promoted by a later push — every push re-uploads both before promoting — so
    this is hygiene rather than correctness, and it must never mask the error
    that brought us here.
    """
    directory = _remote_dir(settings)
    try:
        completed = _ssh(settings, f'rm -f "{directory}/{FACES_TMP_NAME}" "{directory}/{PEOPLE_TMP_NAME}"')
    except RobotError as exc:
        logger.warning("Could not clear the staged push files on the robot: %s", exc)
        return
    if completed.returncode != 0:
        logger.warning(
            "Could not clear the staged push files on the robot: %s",
            completed.stderr.strip() or completed.returncode,
        )


def fetch_stores(settings: Settings, into: Path) -> dict[str, Path | None]:
    """Fetch both robot store files into `into`; a missing remote file is None, not an error."""
    into.mkdir(parents=True, exist_ok=True)
    return {
        FACES_KEY: _download(settings, faces.FACES_FILENAME, into / faces.FACES_FILENAME),
        PEOPLE_KEY: _download(settings, people.PEOPLE_FILENAME, into / people.PEOPLE_FILENAME),
    }


# --------------------------------------------------------------------------
# the last-push snapshot
# --------------------------------------------------------------------------


def last_push_dir(settings: Settings) -> Path:
    """Return the directory holding a copy of what the last verified push wrote."""
    return settings.data_dir / LAST_PUSH_DIRNAME


def _snapshot_push(settings: Settings, faces_file: Path, people_file: Path) -> None:
    """Keep a copy of the two files this push put on the robot.

    A deletion on the robot is invisible in the file it happened to — the fact is
    simply not there — so the only way to see one is to compare against what we
    left behind. Written with the store's own tmp+replace idiom so a crash
    mid-write cannot leave a half-file that would read as a fabricated removal.
    """
    directory = last_push_dir(settings)
    directory.mkdir(parents=True, exist_ok=True)
    for source, name in ((faces_file, faces.FACES_FILENAME), (people_file, people.PEOPLE_FILENAME)):
        destination = directory / name
        tmp_path = destination.with_name(f".{name}.{os.getpid()}.tmp")
        try:
            tmp_path.write_bytes(source.read_bytes())
            tmp_path.replace(destination)
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass


# --------------------------------------------------------------------------
# drift and the content view
# --------------------------------------------------------------------------


def _sha256(path: Path | None) -> str | None:
    """Return the sha256 of a file, or None when there is no file."""
    if path is None:
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _drift_from(meta: store.SyncMeta, faces_sha: str | None, people_sha: str | None) -> DriftState:
    """Compare the robot's current hashes against the last verified push."""
    return DriftState(
        faces_changed=faces_sha != meta.last_faces_sha256,
        people_changed=people_sha != meta.last_people_sha256,
        never_pushed=meta.last_push_at is None,
    )


def _by_face_id(people: Sequence[store.BackendPerson]) -> dict[str, store.BackendPerson]:
    """Index every robot record id the backend accounts for, primary ids first.

    A `former_face_id` is a record the survivor of a merge used to carry, so a
    face the robot still holds under it is *known* content. Primary ids are
    indexed in a first pass so that in a hand-edited store where one id appears
    both ways, the person whose current link it is wins.
    """
    index: dict[str, store.BackendPerson] = {}
    for person in people:
        if person.face_id:
            index.setdefault(person.face_id, person)
    for person in people:
        for former in person.former_face_ids:
            index.setdefault(former, person)
    return index


def _by_name(people: Sequence[store.BackendPerson]) -> dict[str, store.BackendPerson]:
    """Index every name the backend answers to, canonical names first, then aliases."""
    index: dict[str, store.BackendPerson] = {}
    for person in people:
        index.setdefault(person.name.casefold(), person)
    for person in people:
        for alias in person.aliases:
            index.setdefault(alias.casefold(), person)
    return index


def _stored_embeddings(person: store.BackendPerson) -> set[tuple[float, ...]]:
    """Every embedding the backend holds for one person, projected or not (Codex A1-2)."""
    return {photo.embedding for photo in person.photos if photo.embedding is not None}


def _resolve(
    face_id: str | None,
    name: str,
    by_face_id: dict[str, store.BackendPerson],
    by_name: dict[str, store.BackendPerson],
) -> store.BackendPerson | None:
    """Find the backend person one robot record belongs to: face link first, then name.

    The face link wins because it is the durable identity — `people._upserted`
    makes the same call on the robot ("the face store is the authority on which
    face a record belongs to"). Matching on name alone would import a person
    renamed on the Mac as a second, duplicate person. Both indexes carry what a
    merge left behind, so an old id and an old name still resolve.
    """
    if face_id is not None and face_id in by_face_id:
        return by_face_id[face_id]
    return by_name.get(name.casefold())


@dataclass(frozen=True)
class _PushedFacts:
    """The facts our last verified push left on the robot, keyed both ways."""

    by_face_id: dict[str, list[str]]
    by_name: dict[str, list[str]]

    def for_record(self, face_id: str | None, name: str) -> list[str]:
        """Return what we pushed for one robot record — face link first, then name."""
        if face_id is not None and face_id in self.by_face_id:
            return self.by_face_id[face_id]
        return self.by_name.get(name.casefold(), [])


def _last_pushed_facts(settings: Settings) -> _PushedFacts:
    """Read the last push's own `people.v1.json` back. No snapshot reads as nothing pushed."""
    directory = last_push_dir(settings)
    by_face_id: dict[str, list[str]] = {}
    by_name: dict[str, list[str]] = {}
    for record in people.list_people(directory):
        texts = [fact.text for fact in record.facts]
        if record.face_id:
            by_face_id[record.face_id] = texts
        by_name[record.name.casefold()] = texts
    return _PushedFacts(by_face_id=by_face_id, by_name=by_name)


def _removed_facts(
    pushed: _PushedFacts,
    person: store.BackendPerson | None,
    face_id: str | None,
    name: str,
    here: set[str],
    fact_count: int,
) -> list[str]:
    """Return the facts this push left on the robot that the robot no longer has.

    Four conditions, and each one is load-bearing:

    * the person still exists on the robot — a record evicted by the robot's own
      twelve-person LRU is not a deliberate deletion;
    * the fact was in the last push's snapshot — a fact the backend has but never
      pushed is a *Mac-side addition*, which pushes normally;
    * the fact is not in the robot's file now;
    * the backend still holds it — otherwise an import that already applied the
      removal would keep reporting it and the push could never proceed.

    Plus one guard the criteria alone do not give: at the robot's twenty-fact cap
    an absence is indistinguishable from the store evicting its own oldest fact
    to make room, and treating an eviction as a deletion would delete a fact from
    the Mac that nobody asked to forget. Below the cap no eviction can have
    happened, so removals are only read there. Erring here costs a reverted
    deletion; erring the other way costs data.
    """
    if person is None or fact_count >= people.MAX_FACTS_PER_PERSON:
        return []
    still_on_the_mac = {fact.text.casefold() for fact in person.facts}
    return [
        text
        for text in pushed.for_record(face_id, name)
        if text.casefold() not in here and text.casefold() in still_on_the_mac
    ]


def _diff_from(settings: Settings, fetched_dir: Path) -> RobotDiff:
    """Build the content view of a fetched pair of robot stores.

    Read through the robot's own public readers (which call the same tolerant
    parsers the robot uses), so anything the robot would ignore is ignored here
    too — a record we cannot see is a record the robot cannot use either.
    """
    backend_people = store.list_people(settings)
    by_face_id = _by_face_id(backend_people)
    by_name = _by_name(backend_people)

    new_faces: list[RobotFace] = []
    changed_faces: list[RobotFace] = []
    for record in faces.list_faces(fetched_dir):
        entry = RobotFace(record_id=record.id, name=record.name, embeddings=tuple(record.embeddings))
        person = by_face_id.get(record.id)
        if person is None:
            new_faces.append(entry)
            continue
        # "Changed" is asymmetric on purpose: what blocks a push is a sample the
        # backend does not hold, not a sample it holds *extra*. Equality would
        # also flag the case where an operator added a Mac photo after the
        # enrollment, and that state could survive an import — a push that could
        # never be unblocked. Compared against every stored photo rather than the
        # projected newest three (Codex A1-2): content the backend holds anywhere
        # is content it knows, so a sample pushed out of the window by a later
        # photo — or by a merge — does not re-block the push with nothing left to
        # import.
        if not set(entry.embeddings) <= _stored_embeddings(person):
            changed_faces.append(entry)

    new_person_facts: list[RobotPersonFacts] = []
    removed_person_facts: list[RobotPersonFacts] = []
    pushed_facts = _last_pushed_facts(settings)
    for record in people.list_people(fetched_dir):
        person = _resolve(record.face_id, record.name, by_face_id, by_name)
        known = set() if person is None else {fact.text.casefold() for fact in person.facts}
        here = {fact.text.casefold() for fact in record.facts}

        # `record.facts` is newest-first; the order is carried through so the
        # import can replay it oldest-first into the backend's own store.
        unseen = [fact.text for fact in record.facts if fact.text.casefold() not in known]
        if unseen:
            new_person_facts.append(RobotPersonFacts(name=record.name, face_id=record.face_id, facts=unseen))

        gone = _removed_facts(
            pushed_facts, person, record.face_id, record.name, here, len(record.facts)
        )
        if gone:
            removed_person_facts.append(
                RobotPersonFacts(name=record.name, face_id=record.face_id, facts=gone)
            )

    return RobotDiff(
        new_faces=new_faces,
        changed_faces=changed_faces,
        new_person_facts=new_person_facts,
        removed_person_facts=removed_person_facts,
    )


def drift(settings: Settings) -> DriftState:
    """Fetch the robot's stores and report whether they still match our last push."""
    with tempfile.TemporaryDirectory(prefix="companion-drift-") as raw:
        fetched = fetch_stores(settings, Path(raw))
        return _drift_from(
            store.get_sync_meta(settings),
            _sha256(fetched[FACES_KEY]),
            _sha256(fetched[PEOPLE_KEY]),
        )


def robot_diff(settings: Settings) -> RobotDiff:
    """Fetch the robot's stores and report what they hold that the backend does not."""
    with tempfile.TemporaryDirectory(prefix="companion-diff-") as raw:
        directory = Path(raw)
        fetch_stores(settings, directory)
        return _diff_from(settings, directory)


def import_from_robot(settings: Settings) -> RobotDiff:
    """Preview what an import would bring back from the robot."""
    return robot_diff(settings)


# --------------------------------------------------------------------------
# push
# --------------------------------------------------------------------------


def _expectation(digest: str | None) -> str:
    """Render one pre-push hash for the guard, refusing anything unexpected."""
    if digest is None:
        return ABSENT
    if not _SHA256_PATTERN.match(digest):
        raise RobotError(f"Refusing to build a remote guard around {digest!r}.")
    return digest


def _promote_script(settings: Settings, faces_sha: str | None, people_sha: str | None) -> str:
    """Return the one remote command that re-checks both files and promotes both.

    Re-checking here rather than before the upload is the whole point: the robot
    is the only place that can compare against its own *current* state at the
    instant of the move.
    """
    directory = _remote_dir(settings)
    return "\n".join(
        [
            "set -eu",
            f"dir='{directory}'",
            f"expected_faces='{_expectation(faces_sha)}'",
            f"expected_people='{_expectation(people_sha)}'",
            f'current() {{ if [ -f "$1" ]; then sha256sum "$1" | cut -d" " -f1; else echo {ABSENT}; fi; }}',
            f'if [ "$(current "$dir/{faces.FACES_FILENAME}")" != "$expected_faces" ] ||'
            f' [ "$(current "$dir/{people.PEOPLE_FILENAME}")" != "$expected_people" ]; then',
            f'  rm -f "$dir/{FACES_TMP_NAME}" "$dir/{PEOPLE_TMP_NAME}"',
            f"  exit {PROMOTE_RACE_EXIT}",
            "fi",
            f'mv "$dir/{FACES_TMP_NAME}" "$dir/{faces.FACES_FILENAME}"',
            f'mv "$dir/{PEOPLE_TMP_NAME}" "$dir/{people.PEOPLE_FILENAME}"',
        ]
    )


def _promote(settings: Settings, faces_sha: str | None, people_sha: str | None) -> bool:
    """Run the guarded promote; return False when the guard found the robot changed.

    The guard cleans up after itself on a race. Every other non-zero exit is a
    failure we caused, so the staged files are cleared here before raising.
    """
    completed = _ssh(settings, _promote_script(settings, faces_sha, people_sha))
    if completed.returncode == PROMOTE_RACE_EXIT:
        return False
    if completed.returncode != 0:
        _discard_staged(settings)
        raise RobotError(f"The robot refused the promote: {completed.stderr.strip() or completed.returncode}")
    return True


def push(settings: Settings) -> PushResult:
    """Project the backend onto the robot, refusing to overwrite anything unknown."""
    with tempfile.TemporaryDirectory(prefix="companion-push-") as raw:
        workspace = Path(raw)
        fetched_dir = workspace / "fetched"
        fetched = fetch_stores(settings, fetched_dir)
        faces_before = _sha256(fetched[FACES_KEY])
        people_before = _sha256(fetched[PEOPLE_KEY])

        state = _drift_from(store.get_sync_meta(settings), faces_before, people_before)
        diff = _diff_from(settings, fetched_dir)

        # The gate, as one expression: refuse exactly when the robot may hold
        # writes of its own *and* some of that content is unknown here.
        blocked = state.any_change and not diff.empty
        if blocked:
            logger.info(
                "Refusing to push: the robot holds %d new faces, %d changed faces and facts for %d people.",
                len(diff.new_faces),
                len(diff.changed_faces),
                len(diff.new_person_facts),
            )
            return PushResult(pushed=False, faces_count=0, people_count=0, blocked_by=diff)

        out_dir = workspace / "projected"
        projected = projection.project(settings, out_dir)
        # Annotated because `reachy_companion` ships no `py.typed`: these two
        # cross an untyped boundary, exactly as `backend.store` does.
        local_faces: Path = faces.faces_path_for_instance(out_dir)
        local_people: Path = people.people_path_for_instance(out_dir)

        try:
            _upload(settings, local_faces, FACES_TMP_NAME)
            _upload(settings, local_people, PEOPLE_TMP_NAME)
        except RobotError:
            # The first file may already be staged; leaving it there would strand
            # a half-push on the robot until the next successful one.
            _discard_staged(settings)
            raise

        if not _promote(settings, faces_before, people_before):
            logger.info("The robot's stores changed between the fetch and the promote; nothing was written.")
            return PushResult(
                pushed=False,
                faces_count=0,
                people_count=0,
                blocked_by=PushRace("The robot wrote to its stores while this push was in flight."),
            )

        # Verify from the robot's own bytes, and count them with the robot's own
        # readers: nothing here assumes the robot can run our Python.
        verify_dir = workspace / "verify"
        verified = fetch_stores(settings, verify_dir)
        faces_after = _sha256(verified[FACES_KEY])
        people_after = _sha256(verified[PEOPLE_KEY])
        if faces_after != _sha256(local_faces) or people_after != _sha256(local_people):
            raise RobotVerifyError(
                "The promote reported success but the robot does not hold what this push sent; "
                "the push was not recorded, so the next one will re-check the robot from scratch."
            )

        faces_count = len(faces.list_faces(verify_dir))
        people_count = len(people.list_people(verify_dir))
        # The snapshot and the hashes describe the same verified state and are
        # written together: a hash without its snapshot would report every fact
        # the robot forgets as still present, and a snapshot without its hash
        # would never be consulted.
        _snapshot_push(settings, local_faces, local_people)
        store.set_sync_meta(
            settings,
            store.SyncMeta(
                last_push_at=int(time.time() * 1000),
                last_faces_sha256=faces_after,
                last_people_sha256=people_after,
            ),
        )
        logger.info(
            "Pushed %d face records and %d people to the robot (%d skipped).",
            faces_count,
            people_count,
            len(projected.skipped),
        )
        return PushResult(
            pushed=True,
            faces_count=faces_count,
            people_count=people_count,
            blocked_by=None,
            skipped=projected.skipped,
        )


# --------------------------------------------------------------------------
# import
# --------------------------------------------------------------------------


def _person_for_name(settings: Settings, name: str) -> store.BackendPerson | None:
    """Find the backend person that name reaches — their own, or one merged into them."""
    return _by_name(store.list_people(settings)).get(name.casefold())


def _person_with_face_id(settings: Settings, face_id: str) -> store.BackendPerson | None:
    """Find the backend person accounting for one robot face record id, current or former."""
    return _by_face_id(store.list_people(settings)).get(face_id)


def _person_for_record(settings: Settings, face_id: str | None, name: str) -> store.BackendPerson | None:
    """Resolve a robot record to a backend person exactly as `_diff_from` did: link, then name."""
    linked = None if face_id is None else _person_with_face_id(settings, face_id)
    return linked if linked is not None else _person_for_name(settings, name)


def _add_embeddings(settings: Settings, person_id: str, embeddings: tuple[tuple[float, ...], ...]) -> None:
    """Add the robot's samples as this person's newest synthetic photos.

    Every sample is added, including ones the backend may already hold: the
    projection emits the newest ≤3 photos, so adding the robot's whole set is
    what guarantees that window ends up holding exactly it, and the record the
    next push writes back is the one the robot already has.

    The robot's embeddings run oldest-first and the store prepends, so replaying
    them in order leaves the newest-first photo list in the mirror image — which
    `projection.embeddings_for` reverses straight back.
    """
    for embedding in embeddings:
        store.add_synthetic_photo(settings, person_id, embedding)


def apply_import(settings: Settings, diff: RobotDiff) -> ImportResult:
    """Copy the robot's content into the backend store, one item at a time."""
    applied = 0
    conflicts: list[str] = []

    for entry in diff.new_faces:
        name: str = faces.normalize_face_name(entry.name)
        if not name:
            conflicts.append(f"The robot face record {entry.record_id} has no usable name; skipped.")
            continue

        existing = _person_for_name(settings, name)
        # An alias match is the robot enrolling someone under a name we merged
        # away: the survivor already has a primary link and is *expected* to have
        # a different one, so it is not the conflict below (Codex A1-1).
        by_alias = existing is not None and existing.name.casefold() != name.casefold()
        if existing is not None and not by_alias and existing.face_id not in (None, entry.record_id):
            # Codex R3-2: two faces under one name is the operator's call.
            conflicts.append(
                f"{name}: the robot's face record {entry.record_id} does not match the face id "
                f"{existing.face_id} already stored for this person. Rename one side, then import again."
            )
            continue

        try:
            person = existing if existing is not None else store.create_person(settings, name)
            # Samples first, link last. A failure part-way then leaves a person
            # who is *not* linked to this record, so the record still reads as new
            # and importing again finishes the job. Linking first would leave a
            # person claiming a face they have no sample of — the projection would
            # drop them from `faces.v1.json` and the robot would stop recognizing
            # someone it already knew.
            _add_embeddings(settings, person.id, entry.embeddings)
            if person.face_id in (None, entry.record_id):
                store.set_person_face_id(settings, person.id, entry.record_id)
            else:
                # Codex A1-1: the primary link stays; the new id is remembered so
                # the next diff reads this face as known. Without it the record
                # would be re-reported as new on every diff and the push gate
                # would never open again.
                store.add_former_face_id(settings, person.id, entry.record_id)
        except (ValueError, LookupError) as exc:
            conflicts.append(f"{name}: could not import the robot's face record ({exc}).")
            continue
        applied += 1

    for entry in diff.changed_faces:
        linked = _person_with_face_id(settings, entry.record_id)
        if linked is None:
            conflicts.append(
                f"{entry.name}: no person here carries the face id {entry.record_id} any more; import again "
                "to pick it up as a new face."
            )
            continue
        try:
            _add_embeddings(settings, linked.id, entry.embeddings)
        except (ValueError, LookupError) as exc:
            conflicts.append(f"{entry.name}: could not import the robot's new samples ({exc}).")
            continue
        applied += 1

    for facts_entry in diff.new_person_facts:
        name = faces.normalize_face_name(facts_entry.name)
        # Resolved the same way the diff resolved it — face link first — so facts
        # about someone renamed on the Mac land on that person, not a duplicate.
        target = _person_for_record(settings, facts_entry.face_id, name) if name else None
        if target is None:
            try:
                target = store.create_person(settings, name)
            except ValueError as exc:
                conflicts.append(f"{facts_entry.name!r}: could not import the robot's facts ({exc}).")
                continue
        # Oldest first: `store.add_fact` prepends, so this leaves the backend's
        # newest-first order matching the robot's.
        for text in reversed(facts_entry.facts):
            try:
                store.add_fact(settings, target.id, text)
            except (ValueError, LookupError) as exc:
                conflicts.append(f"{name}: could not import the fact {text!r} ({exc}).")
                continue
            applied += 1

    for facts_entry in diff.removed_person_facts:
        name = faces.normalize_face_name(facts_entry.name)
        target = _person_for_record(settings, facts_entry.face_id, name) if name else None
        if target is None:
            conflicts.append(f"{facts_entry.name!r}: nobody here to forget {len(facts_entry.facts)} fact(s) for.")
            continue
        for text in facts_entry.facts:
            key = text.casefold()
            fact = next((item for item in target.facts if item.text.casefold() == key), None)
            if fact is None:
                # Already gone from the Mac: the removal has nothing left to do,
                # which is a finished job rather than a conflict.
                continue
            try:
                store.delete_fact(settings, target.id, fact.id)
            except LookupError as exc:
                conflicts.append(f"{name}: could not forget the fact {text!r} ({exc}).")
                continue
            applied += 1

    logger.info("Imported %d items from the robot with %d conflicts.", applied, len(conflicts))
    return ImportResult(applied=applied, conflicts=conflicts)


# --------------------------------------------------------------------------
# the daemon's app API
# --------------------------------------------------------------------------


def _payload(response: httpx.Response) -> dict[str, object]:
    """Return the daemon's answer as a mapping, whatever shape it arrived in."""
    try:
        parsed = response.json()
    except ValueError:
        return {"result": response.text}
    if isinstance(parsed, dict):
        return {str(key): value for key, value in parsed.items()}
    return {"result": parsed}


def _request(method: str, url: str) -> dict[str, object]:
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS) as client:
            response = client.request(method, url)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise RobotError(f"The robot daemon did not answer {method} {url}: {exc}") from exc
    return _payload(response)


def _http_get(url: str) -> dict[str, object]:
    """The one GET seam, kept thin so tests can stand in for the daemon."""
    return _request("GET", url)


def _http_post(url: str) -> dict[str, object]:
    """The one POST seam, kept thin so tests can stand in for the daemon."""
    return _request("POST", url)


def _apps_url(settings: Settings, route: str) -> str:
    host = settings.reachy_host.strip()
    if not host:
        raise RobotError("The robot is not configured; set REACHY_HOST in the repo `.env`.")
    return f"http://{host}:{DAEMON_PORT}/api/apps/{route}"


def robot_app_status(settings: Settings) -> dict[str, object]:
    """Return what the robot's daemon says is running."""
    return _http_get(_apps_url(settings, "current-app-status"))


def robot_app_start(settings: Settings) -> dict[str, object]:
    """Ask the daemon to start our app. The daemon owns the app lifecycle, we only ask."""
    return _http_post(_apps_url(settings, f"start-app/{APP_NAME}"))


def robot_app_stop(settings: Settings) -> dict[str, object]:
    """Ask the daemon to stop whatever app is running."""
    return _http_post(_apps_url(settings, "stop-current-app"))


def robot_app_restart(settings: Settings) -> dict[str, object]:
    """Ask the daemon to restart the running app — the way a push is picked up."""
    return _http_post(_apps_url(settings, "restart-current-app"))
