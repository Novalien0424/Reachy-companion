"""Contract tests for projecting the Mac store onto the robot's two store files.

The projection is the only thing that ever writes the robot's `faces.v1.json`
and `people.v1.json` from this side, so every rule the sync layer leans on is
pinned here: the robot's own readers must load what we write, the caps are the
robot's caps, the newest samples win, face ids are minted once and never move,
and the ranking is computed here rather than inherited from the store's file
order.
"""

from __future__ import annotations
import json
import math
import random
from pathlib import Path
from itertools import count
from collections.abc import Sequence

import pytest

from reachy_companion import faces, people
from backend import store, projection
from backend.config import Settings


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def monotonic_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give every store write a distinct, increasing timestamp.

    Ranking is by `updated_at`, and several writes really do land in the same
    millisecond on a fast machine; a strictly increasing clock makes the
    ordering assertions below deterministic rather than lucky.
    """
    ticks = count(1_700_000_000_000)
    monkeypatch.setattr(store, "_now_ms", lambda: next(ticks))


def _vector(seed: int) -> tuple[float, ...]:
    """Return a 128-float embedding shaped exactly like a stored one."""
    rng = random.Random(seed)
    raw = [rng.uniform(-1.0, 1.0) for _ in range(faces.EMBEDDING_DIM)]
    scale = math.sqrt(sum(value * value for value in raw))
    return tuple(round(value / scale, 6) for value in raw)


def _person(
    settings: Settings,
    name: str,
    *,
    embeddings: Sequence[int] = (),
    facts: Sequence[str] = (),
) -> store.BackendPerson:
    """Create one backend person with synthetic embeddings (oldest first) and facts."""
    person = store.create_person(settings, name)
    for seed in embeddings:
        store.add_synthetic_photo(settings, person.id, _vector(seed))
    for text in facts:
        store.add_fact(settings, person.id, text)
    reloaded = store.get_person(settings, person.id)
    assert reloaded is not None
    return reloaded


def _projected_faces(out_dir: Path) -> list[faces.FaceRecord]:
    """Load the projected face store with the robot's own reader."""
    return faces._read_faces_file(faces.faces_path_for_instance(out_dir))


def _projected_people(out_dir: Path) -> list[people.PersonRecord]:
    """Load the projected person store with the robot's own reader."""
    return people.list_people(out_dir)


def _raw_faces(out_dir: Path) -> dict[str, object]:
    """Return the projected face store as raw JSON, for the fields the reader hides."""
    parsed: dict[str, object] = json.loads(faces.faces_path_for_instance(out_dir).read_text(encoding="utf-8"))
    return parsed


# --------------------------------------------------------------------------
# what the robot's readers get back
# --------------------------------------------------------------------------


def test_project_writes_files_the_robot_readers_load(settings: Settings, tmp_path: Path) -> None:
    """Both files round-trip through the robot's own readers with the right identities."""
    lena = _person(settings, "Lena", embeddings=[1], facts=["likes tea"])
    sam = _person(settings, "Sam", embeddings=[2])

    result = projection.project(settings, tmp_path / "out")

    assert result.faces_count == 2
    assert result.people_count == 2
    assert result.skipped == []

    records = _projected_faces(tmp_path / "out")
    assert {record.name for record in records} == {"Lena", "Sam"}

    projected = _projected_people(tmp_path / "out")
    assert {record.name for record in projected} == {"Lena", "Sam"}
    assert [fact.text for record in projected if record.name == "Lena" for fact in record.facts] == ["likes tea"]

    # The face link survives into the person store, which is what lets the robot
    # answer "who is this" and "what do I know about them" as one person.
    face_ids = {record.name: record.id for record in records}
    assert {record.name: record.face_id for record in projected} == face_ids
    assert lena.id != sam.id


def test_project_stamps_the_current_alignment_marker(settings: Settings, tmp_path: Path) -> None:
    """The robot's writer stamps `arcface5`; a record without it would be dropped on the robot."""
    _person(settings, "Lena", embeddings=[1])

    projection.project(settings, tmp_path / "out")

    payload = _raw_faces(tmp_path / "out")
    entries = payload["faces"]
    assert isinstance(entries, list)
    assert [entry["alignment"] for entry in entries] == [faces.ALIGNMENT_VERSION]
    assert payload["version"] == faces.SCHEMA_VERSION


def test_project_keeps_the_newest_three_embeddings_oldest_first(settings: Settings, tmp_path: Path) -> None:
    """Five samples project as the newest three, in the robot's own oldest-first order."""
    _person(settings, "Lena", embeddings=[1, 2, 3, 4, 5])

    projection.project(settings, tmp_path / "out")

    record = _projected_faces(tmp_path / "out")[0]
    assert len(record.embeddings) == faces.MAX_EMBEDDINGS_PER_PERSON
    assert record.embeddings == (_vector(3), _vector(4), _vector(5))


def test_project_carries_embeddings_through_unchanged(settings: Settings, tmp_path: Path) -> None:
    """Stored vectors are the wire format already — projection must not re-round them."""
    _person(settings, "Lena", embeddings=[7])

    projection.project(settings, tmp_path / "out")

    assert _projected_faces(tmp_path / "out")[0].embeddings == (_vector(7),)


# --------------------------------------------------------------------------
# selection: who makes it onto the robot
# --------------------------------------------------------------------------


def test_project_skips_a_person_with_no_embedded_photo_but_keeps_their_facts(
    settings: Settings, tmp_path: Path
) -> None:
    """No embedding means no face record — the facts still travel."""
    person = _person(settings, "Ada", facts=["writes poetry"])
    store.add_photo(settings, person.id, "ada.jpg", b"bytes")
    failed = store.get_person(settings, person.id)
    assert failed is not None
    store.set_photo_embedding(settings, person.id, failed.photos[0].id, None, "no_face")

    result = projection.project(settings, tmp_path / "out")

    assert result.faces_count == 0
    assert result.people_count == 1
    assert _projected_faces(tmp_path / "out") == []
    projected = _projected_people(tmp_path / "out")
    assert [record.name for record in projected] == ["Ada"]
    assert [fact.text for fact in projected[0].facts] == ["writes poetry"]


def test_project_reports_people_it_could_not_carry(settings: Settings, tmp_path: Path) -> None:
    """A person with neither an embedding nor a fact reaches the robot as nothing at all."""
    _person(settings, "Lena", embeddings=[1])
    _person(settings, "Nobody")

    result = projection.project(settings, tmp_path / "out")

    assert result.faces_count == 1
    assert result.people_count == 1
    assert result.skipped == ["Nobody"]


def test_project_caps_the_projection_at_the_robots_twelve(settings: Settings, tmp_path: Path) -> None:
    """Fifteen embedded people project as the twelve most recently updated."""
    for index in range(15):
        _person(settings, f"Person {index:02d}", embeddings=[index])

    result = projection.project(settings, tmp_path / "out")

    assert result.faces_count == faces.MAX_PEOPLE
    assert result.people_count == faces.MAX_PEOPLE
    assert len(result.skipped) == 3
    # The three oldest are the ones left behind.
    assert sorted(result.skipped) == ["Person 00", "Person 01", "Person 02"]
    assert len(_projected_faces(tmp_path / "out")) == faces.MAX_PEOPLE
    assert len(_projected_people(tmp_path / "out")) == faces.MAX_PEOPLE


def test_every_projected_face_has_a_person_record(settings: Settings, tmp_path: Path) -> None:
    """The invariant: >12 people mixing photos and facts still project as one coherent pair.

    Facts-only people are projected *after* the embedded ones, so the store's own
    LRU can never evict a face's person record — the case that would leave the
    robot recognizing a face it has no name record for.
    """
    for index in range(9):
        _person(settings, f"Talker {index}", facts=[f"fact {index}"])
    for index in range(8):
        _person(settings, f"Face {index}", embeddings=[100 + index])

    result = projection.project(settings, tmp_path / "out")

    records = _projected_faces(tmp_path / "out")
    projected = _projected_people(tmp_path / "out")
    assert result.faces_count == 8
    assert result.people_count == faces.MAX_PEOPLE
    assert len(projected) == faces.MAX_PEOPLE

    names = {record.name for record in projected}
    assert {record.name for record in records} <= names
    face_ids = {record.face_id for record in projected if record.face_id}
    assert {record.id for record in records} <= face_ids


def test_project_ranks_by_updated_at_rather_than_store_order(settings: Settings, tmp_path: Path) -> None:
    """The store's file order is an invariant of its own writers, not an input to trust.

    A hand-edited `people.json` can hold any order at all; the top-12 selection
    has to come from `updated_at`, so a stale row can never displace a fresh one.
    """
    rows: list[dict[str, object]] = []
    for index in range(14):
        rows.append(
            {
                "id": f"bp_{index:02d}",
                "name": f"Person {index:02d}",
                "faceId": None,
                "facts": [],
                "photos": [
                    {
                        "id": f"bph_{index:02d}",
                        "displayName": "robot enrollment",
                        "storedAs": None,
                        "addedAt": 1_000 + index,
                        "embedding": list(_vector(index)),
                        "error": None,
                        "synthetic": True,
                    }
                ],
                "createdAt": 1_000 + index,
                # Ascending: the *last* rows in the file are the freshest, which is
                # the opposite of what the store's own writers would produce.
                "updatedAt": 1_000 + index,
            }
        )
    path = store.people_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "people": rows, "sync": {}}), encoding="utf-8")

    result = projection.project(settings, tmp_path / "out")

    assert sorted(result.skipped) == ["Person 00", "Person 01"]
    names = {record.name for record in _projected_faces(tmp_path / "out")}
    assert names == {f"Person {index:02d}" for index in range(2, 14)}


# --------------------------------------------------------------------------
# facts
# --------------------------------------------------------------------------


def test_project_replays_the_newest_twenty_facts_newest_first(settings: Settings, tmp_path: Path) -> None:
    """Facts replay oldest→newest so the robot store's prepend-and-cap keeps the newest 20."""
    _person(settings, "Lena", embeddings=[1], facts=[f"fact {index:02d}" for index in range(25)])

    projection.project(settings, tmp_path / "out")

    projected = _projected_people(tmp_path / "out")[0]
    assert len(projected.facts) == people.MAX_FACTS_PER_PERSON
    assert [fact.text for fact in projected.facts] == [f"fact {index:02d}" for index in range(24, 4, -1)]


def test_project_writes_an_empty_person_store_when_nothing_is_known(settings: Settings, tmp_path: Path) -> None:
    """Both files must exist even with an empty store — the push scps them unconditionally."""
    result = projection.project(settings, tmp_path / "out")

    assert result == projection.ProjectionResult(faces_count=0, people_count=0, skipped=[])
    assert faces.faces_path_for_instance(tmp_path / "out").is_file()
    assert people.people_path_for_instance(tmp_path / "out").is_file()
    assert _projected_faces(tmp_path / "out") == []
    assert _projected_people(tmp_path / "out") == []


# --------------------------------------------------------------------------
# face-id stability
# --------------------------------------------------------------------------


def test_project_mints_a_face_id_and_persists_it(settings: Settings, tmp_path: Path) -> None:
    """Projection is what mints the id, and the store keeps it."""
    person = _person(settings, "Lena", embeddings=[1])
    assert person.face_id is None

    projection.project(settings, tmp_path / "out")

    reloaded = store.get_person(settings, person.id)
    assert reloaded is not None
    assert reloaded.face_id is not None
    assert reloaded.face_id.startswith("f_")
    assert _projected_faces(tmp_path / "out")[0].id == reloaded.face_id


def test_two_projections_emit_identical_face_ids(settings: Settings, tmp_path: Path) -> None:
    """Two consecutive pushes must not re-identify the same person to the robot.

    The face store is compared byte for byte, not just id by id: minting rewrites
    the person's `updated_at`, and projecting the pre-mint value would make the
    *next* projection differ for no reason visible to anyone reading either file.
    """
    _person(settings, "Lena", embeddings=[1])
    _person(settings, "Sam", embeddings=[2])

    projection.project(settings, tmp_path / "first")
    projection.project(settings, tmp_path / "second")

    first = {record.name: record.id for record in _projected_faces(tmp_path / "first")}
    second = {record.name: record.id for record in _projected_faces(tmp_path / "second")}
    assert first == second
    assert len(first) == 2
    assert faces.faces_path_for_instance(tmp_path / "first").read_bytes() == (
        faces.faces_path_for_instance(tmp_path / "second").read_bytes()
    )


def test_two_projections_are_identical_when_only_some_people_were_minted(
    settings: Settings, tmp_path: Path
) -> None:
    """Minting reorders the ranking, so the records are re-ranked after it, not before.

    Bea is older and unlinked; Ada is newer and already carries an id. Minting
    moves Bea to the top, so a projection that wrote the *pre*-mint order would
    disagree with every projection after it.
    """
    _person(settings, "Bea", embeddings=[2])
    ada = _person(settings, "Ada", embeddings=[1])
    store.set_person_face_id(settings, ada.id, "f_ada")

    projection.project(settings, tmp_path / "first")
    projection.project(settings, tmp_path / "second")

    assert faces.faces_path_for_instance(tmp_path / "first").read_bytes() == (
        faces.faces_path_for_instance(tmp_path / "second").read_bytes()
    )
    assert [record.name for record in _projected_faces(tmp_path / "first")] == ["Bea", "Ada"]


def test_project_keeps_an_existing_face_id(settings: Settings, tmp_path: Path) -> None:
    """An id imported from the robot is the person's identity — projection never re-mints it."""
    person = _person(settings, "Lena", embeddings=[1])
    store.set_person_face_id(settings, person.id, "f_from_robot")

    projection.project(settings, tmp_path / "out")

    assert _projected_faces(tmp_path / "out")[0].id == "f_from_robot"


def test_project_does_not_mint_face_ids_for_people_without_a_face_record(
    settings: Settings, tmp_path: Path
) -> None:
    """A facts-only person must stay unlinked, or a later voice enrollment reads as a conflict."""
    person = _person(settings, "Ada", facts=["writes poetry"])

    projection.project(settings, tmp_path / "out")

    reloaded = store.get_person(settings, person.id)
    assert reloaded is not None
    assert reloaded.face_id is None
    assert _projected_people(tmp_path / "out")[0].face_id is None


# --------------------------------------------------------------------------
# re-projection
# --------------------------------------------------------------------------


def test_project_overwrites_a_dirty_output_directory(settings: Settings, tmp_path: Path) -> None:
    """A second projection into the same directory holds only the second projection."""
    gone = _person(settings, "Lena", embeddings=[1], facts=["likes tea"])
    projection.project(settings, tmp_path / "out")
    store.delete_person(settings, gone.id)
    _person(settings, "Sam", embeddings=[2], facts=["likes coffee"])

    result = projection.project(settings, tmp_path / "out")

    assert result.faces_count == 1
    assert result.people_count == 1
    assert [record.name for record in _projected_faces(tmp_path / "out")] == ["Sam"]
    assert [record.name for record in _projected_people(tmp_path / "out")] == ["Sam"]


def test_embeddings_for_reads_the_newest_samples(settings: Settings, tmp_path: Path) -> None:
    """The per-person rule is a named function — the robot diff compares against it too."""
    person = _person(settings, "Lena", embeddings=[1, 2, 3, 4])

    assert projection.embeddings_for(person) == (_vector(2), _vector(3), _vector(4))
    assert projection.embeddings_for(_person(settings, "Ada")) == ()


# --------------------------------------------------------------------------
# the boundary projection depends on (Codex R2-6)
# --------------------------------------------------------------------------


def test_store_rejects_a_whitespace_only_name_or_fact(settings: Settings) -> None:
    """Nothing empty can reach the store, so nothing empty can reach the robot.

    The matching 400 on the API side is Task 11's; this is the half projection
    relies on — every projected name and fact is non-empty by construction.
    """
    with pytest.raises(ValueError):
        store.create_person(settings, "   ")

    person = store.create_person(settings, "Lena")
    with pytest.raises(ValueError):
        store.add_fact(settings, person.id, " \t\n ")
