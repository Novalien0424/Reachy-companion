"""The Mac-side people store: names, facts, photos, embeddings, and sync state.

This is the durable side of the person memory. The robot's `faces.v1.json` and
`people.v1.json` are a *projection* of what lives here (Task 10), so the two
must agree byte-for-byte on what a name and a fact look like. That is why every
name goes through `faces.normalize_face_name` and every fact through
`memory.normalize_memory_text` **at this boundary**: whatever the store holds is
exactly what projection will emit, and a pushed file can never read back as
drift.

Every idiom is lifted from `reachy_companion.people`, its closest sibling: a
schema version, a module-level lock, atomic tmp+replace writes, and a tolerant
reader that degrades to "nobody is known" rather than raising through its
callers. Three things differ deliberately:

* **No caps.** The Mac holds everything; the 12-people / 3-embeddings / 20-facts
  caps are projection's concern, not the store's.
* **Photo bytes.** They live at `data_dir/photos/<person_id>/<photo_id><ext>`.
  The extension is whitelisted from the upload's filename and the filename is
  never otherwise used for a path — a client that uploads `../../evil.jpg`
  still writes exactly one file inside that person's own directory, and
  `display_name` survives only as a label the operator recognises.
* **A `sync` block.** `SyncMeta` shares the file but is written only by
  `set_sync_meta`, after a push has been verified. Person mutations carry the
  existing block through untouched, and vice versa.

The lock is an `RLock` so that a compound operation (write photo bytes, then
record the photo) can hold it across a nested mutation; FastAPI serves requests
on a thread pool, so every read-modify-write here is inside it.
"""

from __future__ import annotations
import os
import json
import time
import random
import shutil
import string
import logging
import threading
from typing import Final
from pathlib import Path
from dataclasses import field, replace, dataclass
from collections.abc import Mapping, Callable, Sequence

from reachy_companion import faces, memory
from backend.config import Settings


logger = logging.getLogger(__name__)

SCHEMA_VERSION: Final[int] = 1
PEOPLE_FILENAME: Final[str] = "people.json"
PHOTOS_DIRNAME: Final[str] = "photos"

# Whitelisted upload extensions. Anything else is stored under `.bin`: the byte
# stream is kept for the operator, but nothing downstream may infer a type from
# a name the client chose.
PHOTO_EXTENSIONS: Final[frozenset[str]] = frozenset({".jpg", ".jpeg", ".png", ".webp"})
FALLBACK_PHOTO_EXTENSION: Final[str] = ".bin"

# The failure vocabulary shared with `backend.embedding` (Task 9) and rendered
# per photo by the UI (Task 12).
PHOTO_ERRORS: Final[frozenset[str]] = frozenset(
    {"no_face", "multiple_faces", "too_far", "decode_failed", "internal_error"}
)

SYNTHETIC_DISPLAY_NAME: Final[str] = "robot enrollment"

_PERSON_ID_PREFIX: Final[str] = "bp"
_FACT_ID_PREFIX: Final[str] = "bf"
_PHOTO_ID_PREFIX: Final[str] = "bph"

_STORE_LOCK = threading.RLock()


class DuplicateNameError(ValueError):
    """A person with that name (case-insensitively, after normalization) already exists."""


class EmptyValueError(ValueError):
    """A name or fact normalized to nothing — there is no record to store."""


class PersonNotFoundError(LookupError):
    """No person with that id."""


class FactNotFoundError(LookupError):
    """No fact with that id on that person."""


class PhotoNotFoundError(LookupError):
    """No photo with that id on that person."""


@dataclass(frozen=True)
class BackendFact:
    """One short fact about one person, already normalized to the robot's rules."""

    id: str
    text: str
    created_at: int

    def to_json(self) -> dict[str, object]:
        """Return the persisted JSON shape for this fact."""
        return {"id": self.id, "text": self.text, "createdAt": self.created_at}


@dataclass(frozen=True)
class BackendPhoto:
    """One enrollment sample: either uploaded bytes, or an embedding imported from the robot.

    `stored_as` is the *bare filename* under the person's photo directory, or
    None when there are no bytes on disk. `synthetic=True` marks an embedding
    imported from a robot-side voice enrollment: it has no file, but it still
    projects back to the robot so a re-enrollment survives the next push.
    `embedding` is the 128-float SFace vector, None while it is unset or the
    photo failed; `error` then says why.
    """

    id: str
    display_name: str
    stored_as: str | None
    added_at: int
    embedding: tuple[float, ...] | None
    error: str | None
    synthetic: bool = False

    def to_json(self) -> dict[str, object]:
        """Return the persisted JSON shape for this photo."""
        return {
            "id": self.id,
            "displayName": self.display_name,
            "storedAs": self.stored_as,
            "addedAt": self.added_at,
            "embedding": None if self.embedding is None else list(self.embedding),
            "error": self.error,
            "synthetic": self.synthetic,
        }


@dataclass(frozen=True)
class BackendPerson:
    """One person on the Mac: a name, an optional robot face id, facts and photos.

    `facts` and `photos` are both newest-first, which is the order projection
    reads them in (newest ≤3 embeddings, newest ≤20 facts).
    """

    id: str
    name: str
    face_id: str | None
    facts: tuple[BackendFact, ...]
    photos: tuple[BackendPhoto, ...]
    created_at: int
    updated_at: int

    def to_json(self) -> dict[str, object]:
        """Return the persisted JSON shape for this person."""
        return {
            "id": self.id,
            "name": self.name,
            "faceId": self.face_id,
            "facts": [fact.to_json() for fact in self.facts],
            "photos": [photo.to_json() for photo in self.photos],
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }


@dataclass(frozen=True)
class SyncMeta:
    """What the last verified push left on the robot.

    The two hashes are of the files as they were read back *from the robot*
    after the push, so drift detection (Task 10) is a pure content comparison:
    anything the robot wrote since — a voice enrollment, a fact added by
    speech — changes the hash.
    """

    last_push_at: int | None = None
    last_faces_sha256: str | None = None
    last_people_sha256: str | None = None

    def to_json(self) -> dict[str, object]:
        """Return the persisted JSON shape for the sync block."""
        return {
            "lastPushAt": self.last_push_at,
            "lastFacesSha256": self.last_faces_sha256,
            "lastPeopleSha256": self.last_people_sha256,
        }


@dataclass(frozen=True)
class _Document:
    """The whole store file: the people list and the sync block, read or written together."""

    people: tuple[BackendPerson, ...] = ()
    sync: SyncMeta = field(default_factory=SyncMeta)


# --------------------------------------------------------------------------
# paths
# --------------------------------------------------------------------------


def people_path(settings: Settings) -> Path:
    """Return the JSON store path."""
    return settings.data_dir / PEOPLE_FILENAME


def photo_dir(settings: Settings, person_id: str) -> Path:
    """Return the directory holding one person's photo bytes."""
    return settings.data_dir / PHOTOS_DIRNAME / person_id


def photo_path(settings: Settings, person_id: str, photo: BackendPhoto) -> Path | None:
    """Return the file backing one photo, or None when it has no bytes (synthetic)."""
    if photo.stored_as is None:
        return None
    return photo_dir(settings, person_id) / photo.stored_as


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------


def _make_id(prefix: str) -> str:
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f"{prefix}_{int(time.time() * 1000)}_{suffix}"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _normalized_name(name: str) -> str:
    """Normalize exactly as the robot's face store does, or raise if nothing is left."""
    # The annotation pins the untyped `reachy_companion` return to `str`.
    normalized: str = faces.normalize_face_name(name)
    if not normalized:
        raise EmptyValueError("A person's name cannot be empty.")
    return normalized


def _normalized_fact(text: str) -> str:
    """Normalize exactly as the robot's memory store does, or raise if nothing is left."""
    normalized: str = memory.normalize_memory_text(text)
    if not normalized:
        raise EmptyValueError("A fact cannot be empty.")
    return normalized


def _photo_extension(display_name: str) -> str:
    """Return a whitelisted extension for the upload, or the neutral fallback."""
    suffix = Path(display_name).suffix.lower()
    return suffix if suffix in PHOTO_EXTENSIONS else FALLBACK_PHOTO_EXTENSION


def _safe_stored_as(value: object) -> str | None:
    """Return a bare filename, or None — `stored_as` is never allowed to be a path.

    Writers only ever produce `<photo_id><ext>`; this guards a hand-edited store
    from turning a thumbnail request into an arbitrary file read.
    """
    if not isinstance(value, str) or not value:
        return None
    # Both separators are rejected regardless of platform: a store written on
    # one OS must not become path-traversal on another.
    separators = {"/", "\\", os.sep}
    if value in {".", ".."} or any(sep in value for sep in separators) or value != Path(value).name:
        logger.warning("Ignoring a photo filename that is not a bare name: %r", value)
        return None
    return value


def _embedding_from(value: object) -> tuple[float, ...] | None:
    """Coerce a sequence of real numbers to a float tuple, or None."""
    if value is None:
        return None
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        return None
    numbers: list[float] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            return None
        numbers.append(float(item))
    return tuple(numbers)


def _clean_str(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip() or None


def _clean_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


# --------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------


def _fact_from_json(value: object) -> BackendFact | None:
    if not isinstance(value, Mapping):
        return None
    fact_id = value.get("id")
    text = value.get("text")
    created_at = _clean_int(value.get("createdAt"))
    if not isinstance(fact_id, str) or not isinstance(text, str) or created_at is None:
        return None
    normalized: str = memory.normalize_memory_text(text)
    if not normalized:
        return None
    return BackendFact(id=fact_id, text=normalized, created_at=created_at)


def _photo_from_json(value: object) -> BackendPhoto | None:
    if not isinstance(value, Mapping):
        return None
    photo_id = value.get("id")
    added_at = _clean_int(value.get("addedAt"))
    if not isinstance(photo_id, str) or added_at is None:
        return None

    error = _clean_str(value.get("error"))
    if error is not None and error not in PHOTO_ERRORS:
        logger.warning("Ignoring an unknown photo error %r on %s.", error, photo_id)
        error = "internal_error"

    display_name = value.get("displayName")
    return BackendPhoto(
        id=photo_id,
        display_name=display_name if isinstance(display_name, str) else photo_id,
        stored_as=_safe_stored_as(value.get("storedAs")),
        added_at=added_at,
        embedding=_embedding_from(value.get("embedding")),
        error=error,
        synthetic=value.get("synthetic") is True,
    )


def _person_from_json(value: object) -> BackendPerson | None:
    if not isinstance(value, Mapping):
        return None

    person_id = value.get("id")
    name = value.get("name")
    created_at = _clean_int(value.get("createdAt"))
    updated_at = _clean_int(value.get("updatedAt"))
    if not isinstance(person_id, str) or not isinstance(name, str):
        return None
    if created_at is None or updated_at is None:
        return None

    normalized_name: str = faces.normalize_face_name(name)
    if not normalized_name:
        return None

    facts_value = value.get("facts")
    facts = [] if not isinstance(facts_value, list) else [_fact_from_json(item) for item in facts_value]
    photos_value = value.get("photos")
    photos = [] if not isinstance(photos_value, list) else [_photo_from_json(item) for item in photos_value]

    return BackendPerson(
        id=person_id,
        name=normalized_name,
        face_id=_clean_str(value.get("faceId")),
        facts=tuple(fact for fact in facts if fact is not None),
        photos=tuple(photo for photo in photos if photo is not None),
        created_at=created_at,
        updated_at=updated_at,
    )


def _sync_from_json(value: object) -> SyncMeta:
    if not isinstance(value, Mapping):
        return SyncMeta()
    return SyncMeta(
        last_push_at=_clean_int(value.get("lastPushAt")),
        last_faces_sha256=_clean_str(value.get("lastFacesSha256")),
        last_people_sha256=_clean_str(value.get("lastPeopleSha256")),
    )


def _read_document(path: Path) -> _Document:
    """Read the store, degrading to an empty document rather than raising."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _Document()
    # ValueError covers UnicodeDecodeError: corrupt *bytes* never reach the JSON
    # decoder, and a bad store must still read as "nobody is known".
    except (OSError, ValueError) as exc:
        logger.warning("Failed to read the backend people store at %s: %s", path, exc)
        return _Document()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("Failed to parse the backend people store at %s: %s", path, exc)
        return _Document()

    if not isinstance(parsed, Mapping):
        return _Document()

    people_value = parsed.get("people")
    if not isinstance(people_value, list):
        return _Document(people=(), sync=_sync_from_json(parsed.get("sync")))

    people = [_person_from_json(item) for item in people_value]
    return _Document(
        people=tuple(person for person in people if person is not None),
        sync=_sync_from_json(parsed.get("sync")),
    )


def _write_document(path: Path, document: _Document) -> None:
    """Write the whole store atomically (tmp + replace), as `reachy_companion.people` does."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "version": SCHEMA_VERSION,
        "people": [person.to_json() for person in document.people],
        "sync": document.sync.to_json(),
    }
    # The temp name carries only the pid, as upstream: concurrent writers within
    # this process are serialized by `_STORE_LOCK`.
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp_path.replace(path)
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


# --------------------------------------------------------------------------
# mutation plumbing
# --------------------------------------------------------------------------


def _require(document: _Document, person_id: str) -> BackendPerson:
    person = next((item for item in document.people if item.id == person_id), None)
    if person is None:
        raise PersonNotFoundError(f"No person with id {person_id!r}.")
    return person


def _assert_name_is_free(document: _Document, name: str, *, except_id: str | None = None) -> None:
    key = name.casefold()
    clash = next(
        (item for item in document.people if item.id != except_id and item.name.casefold() == key),
        None,
    )
    if clash is not None:
        raise DuplicateNameError(f"A person named {clash.name!r} already exists.")


def _mutate(
    settings: Settings,
    person_id: str,
    change: Callable[[BackendPerson, _Document], BackendPerson],
) -> BackendPerson:
    """Apply one change to one person, bumping `updated_at` and moving them to the front.

    Routing every mutation through here is what makes the `updated_at` contract
    structural rather than remembered: projection ranks people by recency, so a
    write that forgot to bump it could silently drop a re-enrolled person out of
    the projected top 12.
    """
    path = people_path(settings)
    with _STORE_LOCK:
        document = _read_document(path)
        existing = _require(document, person_id)
        updated = replace(change(existing, document), id=existing.id, updated_at=_now_ms())
        others = tuple(item for item in document.people if item.id != person_id)
        _write_document(path, _Document(people=(updated, *others), sync=document.sync))
        return updated


# --------------------------------------------------------------------------
# people
# --------------------------------------------------------------------------


def list_people(settings: Settings) -> list[BackendPerson]:
    """Return every known person, most recently updated first."""
    with _STORE_LOCK:
        return list(_read_document(people_path(settings)).people)


def get_person(settings: Settings, person_id: str) -> BackendPerson | None:
    """Return one person, or None when the id is unknown."""
    with _STORE_LOCK:
        document = _read_document(people_path(settings))
        return next((item for item in document.people if item.id == person_id), None)


def create_person(settings: Settings, name: str) -> BackendPerson:
    """Create one person. Raises on an empty or already-taken name."""
    normalized = _normalized_name(name)
    path = people_path(settings)
    with _STORE_LOCK:
        document = _read_document(path)
        _assert_name_is_free(document, normalized)
        now = _now_ms()
        person = BackendPerson(
            id=_make_id(_PERSON_ID_PREFIX),
            name=normalized,
            face_id=None,
            facts=(),
            photos=(),
            created_at=now,
            updated_at=now,
        )
        _write_document(path, _Document(people=(person, *document.people), sync=document.sync))
        return person


def rename_person(settings: Settings, person_id: str, name: str) -> BackendPerson:
    """Rename one person. Raises on an empty name or another person's name."""
    normalized = _normalized_name(name)

    def change(person: BackendPerson, document: _Document) -> BackendPerson:
        _assert_name_is_free(document, normalized, except_id=person.id)
        return replace(person, name=normalized)

    return _mutate(settings, person_id, change)


def delete_person(settings: Settings, person_id: str) -> BackendPerson:
    """Remove one person, their facts, their photo records and their photo bytes."""
    path = people_path(settings)
    with _STORE_LOCK:
        document = _read_document(path)
        removed = _require(document, person_id)
        remaining = tuple(item for item in document.people if item.id != person_id)
        _write_document(path, _Document(people=remaining, sync=document.sync))
        shutil.rmtree(photo_dir(settings, person_id), ignore_errors=True)
        return removed


def set_person_face_id(settings: Settings, person_id: str, face_id: str | None) -> BackendPerson:
    """Link a person to a robot `faces.v1.json` record id (projection mints it)."""
    cleaned = _clean_str(face_id)
    return _mutate(settings, person_id, lambda person, _: replace(person, face_id=cleaned))


# --------------------------------------------------------------------------
# facts
# --------------------------------------------------------------------------


def add_fact(settings: Settings, person_id: str, text: str) -> BackendFact:
    """Store one fact, normalized to exactly what projection will write to the robot."""
    normalized = _normalized_fact(text)
    fact = BackendFact(id=_make_id(_FACT_ID_PREFIX), text=normalized, created_at=_now_ms())
    _mutate(settings, person_id, lambda person, _: replace(person, facts=(fact, *person.facts)))
    return fact


def delete_fact(settings: Settings, person_id: str, fact_id: str) -> BackendFact:
    """Remove one fact, returning it. Raises when the id is unknown."""
    with _STORE_LOCK:
        person = get_person(settings, person_id)
        if person is None:
            raise PersonNotFoundError(f"No person with id {person_id!r}.")
        removed = next((fact for fact in person.facts if fact.id == fact_id), None)
        if removed is None:
            raise FactNotFoundError(f"No fact with id {fact_id!r} on person {person_id!r}.")
        _mutate(
            settings,
            person_id,
            lambda current, _: replace(
                current, facts=tuple(fact for fact in current.facts if fact.id != fact_id)
            ),
        )
        return removed


# --------------------------------------------------------------------------
# photos
# --------------------------------------------------------------------------


def add_photo(settings: Settings, person_id: str, display_name: str, raw: bytes) -> BackendPhoto:
    """Store uploaded bytes for one person and record the photo (embedding still unset).

    The file is named after the generated photo id; `display_name` is kept only
    as a label. Nothing a client sends can influence where the bytes land.
    """
    photo_id = _make_id(_PHOTO_ID_PREFIX)
    stored_as = f"{photo_id}{_photo_extension(display_name)}"
    label = " ".join(display_name.split()) or stored_as
    photo = BackendPhoto(
        id=photo_id,
        display_name=label,
        stored_as=stored_as,
        added_at=_now_ms(),
        embedding=None,
        error=None,
        synthetic=False,
    )

    with _STORE_LOCK:
        # Fail before writing bytes we would then have to clean up.
        if get_person(settings, person_id) is None:
            raise PersonNotFoundError(f"No person with id {person_id!r}.")
        directory = photo_dir(settings, person_id)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / stored_as).write_bytes(raw)
        _mutate(settings, person_id, lambda person, _: replace(person, photos=(photo, *person.photos)))
        return photo


def add_synthetic_photo(settings: Settings, person_id: str, embedding: Sequence[float]) -> BackendPhoto:
    """Record an embedding imported from the robot: no bytes, but it still projects back."""
    vector = _embedding_from(embedding)
    if vector is None or not vector:
        raise ValueError("A synthetic photo needs a non-empty embedding.")

    photo = BackendPhoto(
        id=_make_id(_PHOTO_ID_PREFIX),
        display_name=SYNTHETIC_DISPLAY_NAME,
        stored_as=None,
        added_at=_now_ms(),
        embedding=vector,
        error=None,
        synthetic=True,
    )
    _mutate(settings, person_id, lambda person, _: replace(person, photos=(photo, *person.photos)))
    return photo


def set_photo_embedding(
    settings: Settings,
    person_id: str,
    photo_id: str,
    embedding: Sequence[float] | None,
    error: str | None,
) -> BackendPhoto:
    """Record the outcome of embedding one photo: a vector, or a reason it failed."""
    if error is not None and error not in PHOTO_ERRORS:
        raise ValueError(f"Unknown photo error {error!r}; expected one of {sorted(PHOTO_ERRORS)}.")
    vector = None if embedding is None else _embedding_from(embedding)
    if embedding is not None and vector is None:
        raise ValueError("An embedding must be a sequence of real numbers.")
    if vector is not None and error is not None:
        raise ValueError("A photo has either an embedding or an error, never both.")

    updated: list[BackendPhoto] = []

    def change(person: BackendPerson, _: _Document) -> BackendPerson:
        existing = next((photo for photo in person.photos if photo.id == photo_id), None)
        if existing is None:
            raise PhotoNotFoundError(f"No photo with id {photo_id!r} on person {person_id!r}.")
        replacement = replace(existing, embedding=vector, error=error)
        updated.append(replacement)
        return replace(
            person,
            photos=tuple(replacement if photo.id == photo_id else photo for photo in person.photos),
        )

    _mutate(settings, person_id, change)
    return updated[0]


def delete_photo(settings: Settings, person_id: str, photo_id: str) -> BackendPhoto:
    """Remove one photo record and its bytes, returning the removed record."""
    with _STORE_LOCK:
        person = get_person(settings, person_id)
        if person is None:
            raise PersonNotFoundError(f"No person with id {person_id!r}.")
        removed = next((photo for photo in person.photos if photo.id == photo_id), None)
        if removed is None:
            raise PhotoNotFoundError(f"No photo with id {photo_id!r} on person {person_id!r}.")

        _mutate(
            settings,
            person_id,
            lambda current, _: replace(
                current, photos=tuple(photo for photo in current.photos if photo.id != photo_id)
            ),
        )
        path = photo_path(settings, person_id, removed)
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                # The record is gone either way; a stranded file is not worth a 500.
                logger.warning("Failed to remove photo bytes at %s: %s", path, exc)
        return removed


# --------------------------------------------------------------------------
# sync meta
# --------------------------------------------------------------------------


def get_sync_meta(settings: Settings) -> SyncMeta:
    """Return what the last verified push left on the robot (all None when never pushed)."""
    with _STORE_LOCK:
        return _read_document(people_path(settings)).sync


def set_sync_meta(settings: Settings, meta: SyncMeta) -> None:
    """Record a verified push. This is the only writer of the `sync` block."""
    path = people_path(settings)
    with _STORE_LOCK:
        document = _read_document(path)
        _write_document(path, _Document(people=document.people, sync=meta))
