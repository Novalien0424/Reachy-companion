"""Contract tests for the Mac-side people store.

Every rule the later tasks depend on is pinned here: robot-identical
normalization at the boundary, photo bytes that can never be steered by a
client filename, `updated_at` bumping on *every* person-affecting mutation
(projection's top-12 ranking reads it), a sync block only `set_sync_meta`
writes, a tolerant reader, and a lock that survives concurrent FastAPI
requests.
"""

from __future__ import annotations
import json
import threading
from pathlib import Path
from itertools import count
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

import pytest

from reachy_companion import faces, memory
from backend import store
from backend.config import Settings


# --------------------------------------------------------------------------
# people: create / rename / delete
# --------------------------------------------------------------------------


def test_create_person_round_trips(settings: Settings) -> None:
    """A created person reads back from disk with the same identity and no content."""
    created = store.create_person(settings, "Lena")

    assert created.id.startswith("bp_")
    assert created.name == "Lena"
    assert created.face_id is None
    assert created.facts == ()
    assert created.photos == ()
    assert created.created_at == created.updated_at

    reloaded = store.get_person(settings, created.id)
    assert reloaded == created
    assert store.list_people(settings) == [created]


def test_create_person_normalizes_the_name_like_the_robot(settings: Settings) -> None:
    """Names go through `faces.normalize_face_name`, cap included."""
    person = store.create_person(settings, "   Lena   Ha   ")
    assert person.name == "Lena Ha"

    long_name = "x" * 60
    capped = store.create_person(settings, long_name)
    assert capped.name == faces.normalize_face_name(long_name)
    assert len(capped.name) == faces.MAX_NAME_CHARS


def test_create_person_rejects_a_name_that_normalizes_to_empty(settings: Settings) -> None:
    """Whitespace-only names are a client error, not an empty record."""
    with pytest.raises(ValueError):
        store.create_person(settings, "   \n\t ")
    assert store.list_people(settings) == []


def test_create_person_rejects_a_duplicate_after_normalization(settings: Settings) -> None:
    """`"Lena "` and `"lena"` are the same person to the robot, so they are here too."""
    store.create_person(settings, "Lena ")

    with pytest.raises(store.DuplicateNameError):
        store.create_person(settings, "lena")

    assert isinstance(store.DuplicateNameError("x"), ValueError)
    assert len(store.list_people(settings)) == 1


def test_rename_person_normalizes_and_guards_duplicates(settings: Settings) -> None:
    """A rename normalizes, refuses another person's name, and allows re-spelling its own."""
    lena = store.create_person(settings, "Lena")
    store.create_person(settings, "Mo")

    renamed = store.rename_person(settings, lena.id, "  Lena  Ha ")
    assert renamed.name == "Lena Ha"
    assert store.get_person(settings, lena.id) == renamed

    with pytest.raises(store.DuplicateNameError):
        store.rename_person(settings, lena.id, "mo")

    # Re-spelling a person's own name is not a duplicate of itself.
    assert store.rename_person(settings, lena.id, "LENA HA").name == "LENA HA"

    with pytest.raises(ValueError):
        store.rename_person(settings, lena.id, " ")


def test_delete_person_removes_the_record_and_its_photo_directory(settings: Settings) -> None:
    """Deleting a person takes their bytes with them."""
    person = store.create_person(settings, "Lena")
    photo = store.add_photo(settings, person.id, "face.jpg", b"jpeg-bytes")
    photo_dir = store.photo_dir(settings, person.id)
    assert (photo_dir / str(photo.stored_as)).exists()

    removed = store.delete_person(settings, person.id)

    assert removed.id == person.id
    assert store.get_person(settings, person.id) is None
    assert store.list_people(settings) == []
    assert not photo_dir.exists()


def test_unknown_person_reads_as_none_and_mutates_as_an_error(settings: Settings) -> None:
    """A missing person is `None` on lookup and a `PersonNotFoundError` on mutation."""
    assert store.get_person(settings, "bp_nope") is None
    assert isinstance(store.PersonNotFoundError("x"), LookupError)

    with pytest.raises(store.PersonNotFoundError):
        store.rename_person(settings, "bp_nope", "Lena")
    with pytest.raises(store.PersonNotFoundError):
        store.add_fact(settings, "bp_nope", "likes tea")
    with pytest.raises(store.PersonNotFoundError):
        store.add_photo(settings, "bp_nope", "face.jpg", b"bytes")
    with pytest.raises(store.PersonNotFoundError):
        store.delete_person(settings, "bp_nope")


def test_list_people_is_most_recently_updated_first(settings: Settings) -> None:
    """Projection ranks by recency, so the store hands it that order already."""
    lena = store.create_person(settings, "Lena")
    mo = store.create_person(settings, "Mo")
    assert [person.id for person in store.list_people(settings)] == [mo.id, lena.id]

    store.add_fact(settings, lena.id, "likes tea")
    assert [person.id for person in store.list_people(settings)] == [lena.id, mo.id]


# --------------------------------------------------------------------------
# facts
# --------------------------------------------------------------------------


def test_add_fact_normalizes_like_the_robot_memory_store(settings: Settings) -> None:
    """A 300-char fact is stored in the 280-char form projection would emit."""
    person = store.create_person(settings, "Lena")
    raw = "a" * 300

    fact = store.add_fact(settings, person.id, raw)

    assert fact.id.startswith("bf_")
    assert fact.text == memory.normalize_memory_text(raw)
    assert len(fact.text) == memory.MAX_FACT_CHARS
    assert fact.text.endswith("...")
    assert store.get_person(settings, person.id).facts == (fact,)  # type: ignore[union-attr]


def test_add_fact_rejects_a_fact_that_normalizes_to_empty(settings: Settings) -> None:
    """An empty fact is a 400 upstream, never a blank row."""
    person = store.create_person(settings, "Lena")
    with pytest.raises(ValueError):
        store.add_fact(settings, person.id, "  \t ")


def test_facts_are_newest_first_and_delete_by_id(settings: Settings) -> None:
    """Facts stack newest-first; deleting an unknown id is an error, not a silent no-op."""
    person = store.create_person(settings, "Lena")
    first = store.add_fact(settings, person.id, "likes tea")
    second = store.add_fact(settings, person.id, "has a dog")

    assert store.get_person(settings, person.id).facts == (second, first)  # type: ignore[union-attr]

    removed = store.delete_fact(settings, person.id, first.id)
    assert removed == first
    assert store.get_person(settings, person.id).facts == (second,)  # type: ignore[union-attr]

    with pytest.raises(store.FactNotFoundError):
        store.delete_fact(settings, person.id, first.id)


def test_no_fact_or_person_cap(settings: Settings) -> None:
    """The Mac holds everything — the 12/3/20 caps are projection's concern."""
    for index in range(15):
        store.create_person(settings, f"Person {index}")
    assert len(store.list_people(settings)) == 15

    person = store.list_people(settings)[0]
    for index in range(25):
        store.add_fact(settings, person.id, f"fact number {index}")
    assert len(store.get_person(settings, person.id).facts) == 25  # type: ignore[union-attr]


# --------------------------------------------------------------------------
# photos
# --------------------------------------------------------------------------


def test_add_photo_names_the_file_after_the_photo_id_not_the_client(settings: Settings) -> None:
    """A hostile `display_name` is display metadata only — it never steers the path."""
    person = store.create_person(settings, "Lena")

    photo = store.add_photo(settings, person.id, "../../evil.jpg", b"jpeg-bytes")

    assert photo.id.startswith("bph_")
    assert photo.stored_as == f"{photo.id}.jpg"
    assert photo.display_name == "../../evil.jpg"
    assert photo.synthetic is False
    assert photo.embedding is None
    assert photo.error is None

    written = store.photo_dir(settings, person.id) / photo.stored_as
    assert written.read_bytes() == b"jpeg-bytes"
    assert store.photo_path(settings, person.id, photo) == written
    # Nothing escaped the person's own photo directory.
    assert sorted(p.name for p in settings.data_dir.rglob("*") if p.is_file()) == sorted(
        [store.PEOPLE_FILENAME, photo.stored_as]
    )


@pytest.mark.parametrize(
    ("display_name", "expected_extension"),
    [
        ("face.jpg", ".jpg"),
        ("face.JPEG", ".jpeg"),
        ("face.png", ".png"),
        ("shot.WebP", ".webp"),
        ("scan.tiff", ".bin"),
        ("noextension", ".bin"),
        (".jpg", ".bin"),
        ("archive.jpg.zip", ".bin"),
    ],
)
def test_photo_extension_is_whitelisted(settings: Settings, display_name: str, expected_extension: str) -> None:
    """Only the four image extensions survive; anything else lands as `.bin`."""
    person = store.create_person(settings, "Lena")
    photo = store.add_photo(settings, person.id, display_name, b"bytes")
    assert photo.stored_as == f"{photo.id}{expected_extension}"


def test_set_photo_embedding_stores_the_vector_or_the_error(settings: Settings) -> None:
    """The embedding and the failure reason are mutually exclusive and both round-trip."""
    person = store.create_person(settings, "Lena")
    good = store.add_photo(settings, person.id, "good.jpg", b"bytes")
    bad = store.add_photo(settings, person.id, "bad.jpg", b"bytes")

    embedding = tuple(float(index) / 8 for index in range(128))
    embedded = store.set_photo_embedding(settings, person.id, good.id, embedding, None)
    failed = store.set_photo_embedding(settings, person.id, bad.id, None, "no_face")

    assert embedded.embedding == embedding
    assert embedded.error is None
    assert failed.embedding is None
    assert failed.error == "no_face"

    reloaded = store.get_person(settings, person.id)
    assert reloaded is not None
    by_id = {photo.id: photo for photo in reloaded.photos}
    assert by_id[good.id].embedding == embedding
    assert by_id[bad.id].error == "no_face"

    with pytest.raises(ValueError):
        store.set_photo_embedding(settings, person.id, good.id, None, "not_a_known_reason")
    with pytest.raises(ValueError):
        store.set_photo_embedding(settings, person.id, good.id, embedding, "no_face")
    with pytest.raises(store.PhotoNotFoundError):
        store.set_photo_embedding(settings, person.id, "bph_nope", embedding, None)


def test_add_synthetic_photo_holds_an_embedding_with_no_bytes(settings: Settings) -> None:
    """A voice enrollment imported from the robot is a photo record with no file."""
    person = store.create_person(settings, "Lena")
    embedding = tuple(float(index) for index in range(128))

    photo = store.add_synthetic_photo(settings, person.id, embedding)

    assert photo.synthetic is True
    assert photo.stored_as is None
    assert photo.embedding == embedding
    assert photo.error is None
    assert store.photo_path(settings, person.id, photo) is None
    assert not store.photo_dir(settings, person.id).exists()

    reloaded = store.get_person(settings, person.id)
    assert reloaded is not None
    assert reloaded.photos == (photo,)
    assert reloaded.photos[0].embedding == embedding


def test_delete_photo_removes_the_record_and_the_bytes(settings: Settings) -> None:
    """Deleting a photo unlinks its file; a synthetic photo has none to unlink."""
    person = store.create_person(settings, "Lena")
    photo = store.add_photo(settings, person.id, "face.jpg", b"bytes")
    synthetic = store.add_synthetic_photo(settings, person.id, (0.5,) * 128)
    written = store.photo_dir(settings, person.id) / str(photo.stored_as)

    store.delete_photo(settings, person.id, photo.id)
    assert not written.exists()
    assert store.get_person(settings, person.id).photos == (synthetic,)  # type: ignore[union-attr]

    store.delete_photo(settings, person.id, synthetic.id)
    assert store.get_person(settings, person.id).photos == ()  # type: ignore[union-attr]

    with pytest.raises(store.PhotoNotFoundError):
        store.delete_photo(settings, person.id, photo.id)


def test_set_person_face_id(settings: Settings) -> None:
    """Projection mints the face id and persists it here so pushes stay stable."""
    person = store.create_person(settings, "Lena")

    linked = store.set_person_face_id(settings, person.id, "f_1700000000000_abcdef")
    assert linked.face_id == "f_1700000000000_abcdef"
    assert store.get_person(settings, person.id) == linked

    assert store.set_person_face_id(settings, person.id, None).face_id is None


# --------------------------------------------------------------------------
# updated_at (Codex R3-3)
# --------------------------------------------------------------------------


_MUTATIONS: dict[str, Callable[[Settings, str], object]] = {
    "rename": lambda s, pid: store.rename_person(s, pid, "Renamed"),
    "add_fact": lambda s, pid: store.add_fact(s, pid, "likes tea"),
    "delete_fact": lambda s, pid: store.delete_fact(s, pid, store.add_fact(s, pid, "temporary").id),
    "add_photo": lambda s, pid: store.add_photo(s, pid, "face.jpg", b"bytes"),
    "set_photo_embedding": lambda s, pid: store.set_photo_embedding(
        s, pid, store.add_photo(s, pid, "face.jpg", b"bytes").id, (0.25,) * 128, None
    ),
    "add_synthetic_photo": lambda s, pid: store.add_synthetic_photo(s, pid, (0.25,) * 128),
    "delete_photo": lambda s, pid: store.delete_photo(
        s, pid, store.add_photo(s, pid, "face.jpg", b"bytes").id
    ),
    "set_person_face_id": lambda s, pid: store.set_person_face_id(s, pid, "f_1_a"),
}


@pytest.mark.parametrize("mutation_name", sorted(_MUTATIONS))
def test_every_person_affecting_mutation_bumps_updated_at(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    mutation_name: str,
) -> None:
    """Projection's recency ranking is only honest if every write moves `updated_at`."""
    clock = count(1_000)
    monkeypatch.setattr(store, "_now_ms", lambda: next(clock))

    person = store.create_person(settings, "Lena")
    before = store.get_person(settings, person.id)
    assert before is not None

    _MUTATIONS[mutation_name](settings, person.id)

    after = store.get_person(settings, person.id)
    assert after is not None
    assert after.updated_at > before.updated_at
    assert after.created_at == before.created_at


# --------------------------------------------------------------------------
# sync meta
# --------------------------------------------------------------------------


def test_sync_meta_round_trips_and_defaults_to_never_pushed(settings: Settings) -> None:
    """`SyncMeta` lives under the `sync` key and reads as "never pushed" when absent."""
    assert store.get_sync_meta(settings) == store.SyncMeta(None, None, None)

    meta = store.SyncMeta(last_push_at=1_700_000_000_000, last_faces_sha256="a" * 64, last_people_sha256="b" * 64)
    store.set_sync_meta(settings, meta)

    assert store.get_sync_meta(settings) == meta
    raw = json.loads((settings.data_dir / store.PEOPLE_FILENAME).read_text(encoding="utf-8"))
    assert set(raw["sync"]) == {"lastPushAt", "lastFacesSha256", "lastPeopleSha256"}


def test_people_writes_preserve_sync_meta_and_the_reverse(settings: Settings) -> None:
    """The two halves of the document never clobber each other."""
    meta = store.SyncMeta(last_push_at=42, last_faces_sha256="a" * 64, last_people_sha256="b" * 64)
    store.set_sync_meta(settings, meta)

    person = store.create_person(settings, "Lena")
    store.add_fact(settings, person.id, "likes tea")
    assert store.get_sync_meta(settings) == meta

    store.set_sync_meta(settings, store.SyncMeta(99, None, None))
    reloaded = store.get_person(settings, person.id)
    assert reloaded is not None
    assert reloaded.name == "Lena"
    assert len(reloaded.facts) == 1


# --------------------------------------------------------------------------
# tolerant reader
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        b"\xff\xfe not utf-8 at all",
        b"{not json",
        b"[]",
        b'{"version": 1}',
        b'{"version": 1, "people": "nope"}',
    ],
    ids=["bad-bytes", "bad-json", "not-a-mapping", "no-people-key", "people-not-a-list"],
)
def test_a_corrupt_store_reads_as_empty(settings: Settings, raw: bytes) -> None:
    """A bad store degrades to "nobody is known", the robot-store idiom."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    (settings.data_dir / store.PEOPLE_FILENAME).write_bytes(raw)

    assert store.list_people(settings) == []
    assert store.get_sync_meta(settings) == store.SyncMeta(None, None, None)
    # And the store is still writable afterwards.
    assert store.create_person(settings, "Lena").name == "Lena"


def test_unusable_records_are_dropped_not_raised(settings: Settings) -> None:
    """Individual malformed rows are skipped; the rest of the file still loads."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "people": [
            "not a mapping",
            {"id": "bp_1", "name": "   ", "createdAt": 1, "updatedAt": 1},
            {"id": "bp_2", "name": "Lena", "createdAt": 1, "updatedAt": 2, "facts": [], "photos": []},
        ],
    }
    (settings.data_dir / store.PEOPLE_FILENAME).write_text(json.dumps(payload), encoding="utf-8")

    people = store.list_people(settings)
    assert [person.name for person in people] == ["Lena"]


def test_a_hand_edited_stored_as_can_never_escape_the_photo_directory(settings: Settings) -> None:
    """`stored_as` is re-validated on read: a path is not a filename."""
    person = store.create_person(settings, "Lena")
    photo = store.add_photo(settings, person.id, "face.jpg", b"bytes")

    path = settings.data_dir / store.PEOPLE_FILENAME
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["people"][0]["photos"][0]["storedAs"] = "../../../etc/passwd"
    path.write_text(json.dumps(payload), encoding="utf-8")

    reloaded = store.get_person(settings, person.id)
    assert reloaded is not None
    assert reloaded.photos[0].id == photo.id
    assert reloaded.photos[0].stored_as is None
    assert store.photo_path(settings, person.id, reloaded.photos[0]) is None


# --------------------------------------------------------------------------
# concurrency (Codex R1-5)
# --------------------------------------------------------------------------


def test_concurrent_fact_writers_lose_nothing(settings: Settings) -> None:
    """FastAPI serves requests concurrently; a module lock spans read-modify-write."""
    person = store.create_person(settings, "Lena")
    workers = 8
    barrier = threading.Barrier(workers, timeout=10)

    def add(index: int) -> str:
        barrier.wait()
        return store.add_fact(settings, person.id, f"fact number {index}").text

    with ThreadPoolExecutor(max_workers=workers) as pool:
        written = set(pool.map(add, range(workers)))

    reloaded = store.get_person(settings, person.id)
    assert reloaded is not None
    assert {fact.text for fact in reloaded.facts} == written
    assert len(reloaded.facts) == workers
    assert len({fact.id for fact in reloaded.facts}) == workers


def test_concurrent_photo_writers_lose_nothing(settings: Settings) -> None:
    """Photo bytes and the record that names them are written under the same lock."""
    person = store.create_person(settings, "Lena")
    workers = 8
    barrier = threading.Barrier(workers, timeout=10)

    def add(index: int) -> str:
        barrier.wait()
        return store.add_photo(settings, person.id, f"face-{index}.jpg", f"bytes-{index}".encode()).id

    with ThreadPoolExecutor(max_workers=workers) as pool:
        ids = set(pool.map(add, range(workers)))

    reloaded = store.get_person(settings, person.id)
    assert reloaded is not None
    assert {photo.id for photo in reloaded.photos} == ids
    on_disk = {path.name for path in store.photo_dir(settings, person.id).iterdir()}
    assert on_disk == {f"{photo_id}.jpg" for photo_id in ids}


# --------------------------------------------------------------------------
# paths
# --------------------------------------------------------------------------


def test_store_paths_hang_off_the_data_dir(settings: Settings) -> None:
    """One place builds every path the API and the sync layer will need."""
    assert store.people_path(settings) == settings.data_dir / "people.json"
    assert store.photo_dir(settings, "bp_1") == settings.data_dir / "photos" / "bp_1"
    assert isinstance(store.people_path(settings), Path)
