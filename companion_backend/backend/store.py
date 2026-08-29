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
callers. Four things differ deliberately:

* **No caps.** The Mac holds everything; the 12-people / 3-embeddings / 20-facts
  caps are projection's concern, not the store's.
* **A corrupt file is preserved, not clobbered.** Upstream may discard a store
  it cannot parse, because the robot's copy is rebuildable. This one is the
  source of truth, so an unparseable file is renamed to
  `people.json.corrupt.<epoch_ms>` on the read that discovers it, before the
  write that follows can overwrite it. The read still returns empty.
* **Photo bytes.** They live at `data_dir/photos/<person_id>/<photo_id><ext>`.
  The extension is whitelisted from the upload's filename and the filename is
  never otherwise used for a path — a client that uploads `../../evil.jpg`
  still writes exactly one file inside that person's own directory, and
  `display_name` survives only as a label the operator recognises.
* **A `sync` block.** `SyncMeta` shares the file but is written only by
  `set_sync_meta`, after a push has been verified. Person mutations carry the
  existing block through untouched, and vice versa.

**Names resolve through one index.** The robot mishears; an operator merging the
two records it produced leaves the survivor answering to both spellings. So
uniqueness is checked over `name` **and** `aliases` together, and one normalized
string reaches at most one person — `create_person`, `rename_person` and
`merge_people` all ask the same question of the same index, and the sync layer
resolves robot records against it. `former_face_ids` is the same idea for robot
record ids. Neither field is ever projected: both exist so that content the robot
still holds under an old name or an old id reads as *known* here.

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
import hashlib
import logging
import threading
from typing import Final
from pathlib import Path
from dataclasses import field, replace, dataclass
from collections.abc import Mapping, Callable, Sequence, Collection

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
# The label on an enrollment snapshot the sync layer fetched off the robot. It is
# a `.jpg` because that is what the robot writes, and the extension is what gives
# the stored file its type.
ROBOT_SNAPSHOT_DISPLAY_NAME: Final[str] = "robot-snapshot.jpg"

_PERSON_ID_PREFIX: Final[str] = "bp"
_FACT_ID_PREFIX: Final[str] = "bf"
_PHOTO_ID_PREFIX: Final[str] = "bph"

_STORE_LOCK = threading.RLock()


class DuplicateNameError(ValueError):
    """That name is already answered to by someone else — as their name, or as an alias."""


class MergeError(ValueError):
    """A merge that cannot mean anything: a person merged into themselves."""


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

    `display_only=True` is the mirror image of `synthetic`: bytes with no
    standing as a sample. It marks the one enrollment snapshot the robot keeps
    per person (D-013 amendment), fetched by the sync layer so the operator has a
    face to look at. An explicit flag rather than "has bytes and no embedding"
    (Codex A2-4), because that description also fits an upload whose embedding is
    still being computed, and mislabelling one as the other would either hide a
    real sample from the robot or offer a snapshot to it as recognition data.
    """

    id: str
    display_name: str
    stored_as: str | None
    added_at: int
    embedding: tuple[float, ...] | None
    error: str | None
    synthetic: bool = False
    display_only: bool = False

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
            "displayOnly": self.display_only,
        }


@dataclass(frozen=True)
class BackendPerson:
    """One person on the Mac: a name, an optional robot face id, facts and photos.

    `facts` and `photos` are both newest-first, which is the order projection
    reads them in (newest ≤3 embeddings, newest ≤20 facts).

    `aliases` and `former_face_ids` are what a merge leaves behind, and both are
    **Mac-side only** — neither is ever projected. An alias is a name the robot
    still hears this person by (it misheard "Linna" as "Lena"), normalized by
    exactly the same rule as `name`, so the sync layer can resolve a robot record
    under the old name onto the survivor. A former face id is a robot record id
    that used to be this person's, so a face the robot still holds under it reads
    as *known* rather than as a stranger enrolling for the first time.
    """

    id: str
    name: str
    face_id: str | None
    facts: tuple[BackendFact, ...]
    photos: tuple[BackendPhoto, ...]
    created_at: int
    updated_at: int
    aliases: tuple[str, ...] = ()
    former_face_ids: tuple[str, ...] = ()

    def name_keys(self) -> set[str]:
        """Return every normalized string that resolves to this person."""
        return {self.name.casefold(), *(alias.casefold() for alias in self.aliases)}

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
            "aliases": list(self.aliases),
            "formerFaceIds": list(self.former_face_ids),
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


def _normalized_facts(texts: Sequence[str]) -> tuple[str, ...]:
    """Normalize a whole fact list through the same rule, dropping what it cannot keep.

    The normalizer and the case-insensitive dedupe are `add_fact`'s, so a
    rewritten list is stored in exactly the form projection would have emitted
    for the same facts added one at a time. A text that normalizes to nothing is
    *dropped* rather than raised on, which is the one deliberate difference:
    `add_fact` is an operator typing one fact, where an empty one is a mistake
    worth a 400, while this takes a whole list from a rewriter and a blank entry
    in it is simply nothing to store.
    """
    kept: list[str] = []
    for text in texts:
        normalized: str = memory.normalize_memory_text(text)
        if normalized:
            kept.append(normalized)
    return _deduped(kept)


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


def _require_full_embedding(vector: tuple[float, ...]) -> None:
    """Reject a vector the robot would silently drop, at the boundary that can still say so.

    `faces._embedding_from_json` returns None for any embedding whose length is
    not `EMBEDDING_DIM`, and `_record_from_json` skips those without a log. A
    wrong-length vector accepted here would therefore look embedded on the Mac
    and simply not exist after a push — the silent drift this store's contract
    rules out. The write side is the only place left to catch it.
    """
    if len(vector) != faces.EMBEDDING_DIM:
        raise ValueError(f"An embedding must have {faces.EMBEDDING_DIM} values, got {len(vector)}.")


def _clean_str(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip() or None


def _clean_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _deduped(values: Sequence[str], *, excluding: Collection[str] = ()) -> tuple[str, ...]:
    """Return the values in order, case-insensitively deduped, minus the `excluding` keys."""
    seen = set(excluding)
    kept: list[str] = []
    for value in values:
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        kept.append(value)
    return tuple(kept)


def _aliases_from(value: object, name: str) -> tuple[str, ...]:
    """Coerce a persisted alias list, dropping anything that is not a usable name.

    Aliases go through `faces.normalize_face_name` exactly as `name` does (Codex
    A1-4): the sync layer compares a robot record's name against them, and a
    stored alias the robot could never spell would simply never match. An alias
    equal to the person's own name is dropped too — it would be a second way for
    one string to resolve, which is the thing the index rules out.
    """
    if not isinstance(value, list):
        return ()
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        alias: str = faces.normalize_face_name(item)
        if alias:
            normalized.append(alias)
    return _deduped(normalized, excluding={name.casefold()})


def _former_face_ids_from(value: object, face_id: str | None) -> tuple[str, ...]:
    """Coerce a persisted former-id list; the primary id is never also a former one."""
    if not isinstance(value, list):
        return ()
    cleaned = [item for item in (_clean_str(entry) for entry in value) if item is not None]
    return _deduped(cleaned, excluding=set() if face_id is None else {face_id.casefold()})


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
        # Absent in every store written before the snapshot import existed, and
        # "absent" has to mean an ordinary photo — anything else would retire a
        # real sample the moment the flag shipped.
        display_only=value.get("displayOnly") is True,
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

    face_id = _clean_str(value.get("faceId"))
    return BackendPerson(
        id=person_id,
        name=normalized_name,
        face_id=face_id,
        facts=tuple(fact for fact in facts if fact is not None),
        photos=tuple(photo for photo in photos if photo is not None),
        created_at=created_at,
        updated_at=updated_at,
        aliases=_aliases_from(value.get("aliases"), normalized_name),
        former_face_ids=_former_face_ids_from(value.get("formerFaceIds"), face_id),
    )


def _sync_from_json(value: object) -> SyncMeta:
    if not isinstance(value, Mapping):
        return SyncMeta()
    return SyncMeta(
        last_push_at=_clean_int(value.get("lastPushAt")),
        last_faces_sha256=_clean_str(value.get("lastFacesSha256")),
        last_people_sha256=_clean_str(value.get("lastPeopleSha256")),
    )


def _corrupt(path: Path) -> _Document:
    """Preserve an unusable store file, then read as empty.

    The robot's stores may be discarded when they will not parse — they are a
    rebuildable projection. This one is the source of truth, so the file is
    renamed to `people.json.corrupt.<epoch_ms>` *before* the caller's write
    creates a fresh one: the store still degrades to "nobody is known", but the
    original bytes survive for the operator to inspect or salvage.

    Callers hold `_STORE_LOCK`, so two readers in this process cannot race the
    rename; a second attempt from anywhere else simply finds the file gone,
    which is not an error — the evidence is already safe.
    """
    aside = path.with_name(f"{path.name}.corrupt.{_now_ms()}")
    try:
        path.rename(aside)
    except FileNotFoundError:
        logger.warning("The backend people store at %s was already set aside by another reader.", path)
    except OSError as exc:
        # Nothing more we can do; the write that follows may overwrite it.
        logger.warning("Failed to set the corrupt backend people store at %s aside: %s", path, exc)
    else:
        logger.warning("Set the corrupt backend people store at %s aside as %s.", path, aside)
    return _Document()


def _read_document(path: Path) -> _Document:
    """Read the store, degrading to an empty document rather than raising.

    A file that exists but yields no usable people list is corrupt, and is set
    aside by `_corrupt` rather than left for the next write to overwrite. Rows
    *within* a readable file stay tolerant: one malformed person is skipped and
    the rest of the store still loads.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _Document()
    # ValueError covers UnicodeDecodeError: corrupt *bytes* never reach the JSON
    # decoder, and a bad store must still read as "nobody is known".
    except (OSError, ValueError) as exc:
        logger.warning("Failed to read the backend people store at %s: %s", path, exc)
        return _corrupt(path)

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("Failed to parse the backend people store at %s: %s", path, exc)
        return _corrupt(path)

    if not isinstance(parsed, Mapping):
        logger.warning("The backend people store at %s is not a JSON object.", path)
        return _corrupt(path)

    people_value = parsed.get("people")
    if not isinstance(people_value, list):
        logger.warning("The backend people store at %s holds no usable people list.", path)
        return _corrupt(path)

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


def _holder_of(document: _Document, key: str, *, excluding: Collection[str] = ()) -> BackendPerson | None:
    """Return the one person that normalized string resolves to (Codex A1-4).

    The index is over `name` **and** `aliases` together: after a merge, "Lena" is
    still a way to reach the person now called "Linna", and letting a second
    person claim it would make one string resolve two ways — the sync layer picks
    exactly one, so the other would silently stop being reachable.
    """
    return next(
        (item for item in document.people if item.id not in excluding and key in item.name_keys()),
        None,
    )


def _assert_name_is_free(document: _Document, name: str, *, except_id: str | None = None) -> None:
    excluding = () if except_id is None else (except_id,)
    clash = _holder_of(document, name.casefold(), excluding=excluding)
    if clash is not None:
        raise DuplicateNameError(f"{name!r} is already how {clash.name!r} is reached.")


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


def _rewrite_in_place(
    settings: Settings,
    person_id: str,
    change: Callable[[BackendPerson], BackendPerson],
) -> BackendPerson:
    """Apply one change to one person, leaving `updated_at` and their position alone.

    The deliberate opposite of `_mutate`, and the only writer allowed to be: it
    exists for the one edit that is not *news* about the person. The
    consolidation pass rewrites everybody's facts in the background, and neither
    half of `_mutate`'s touch may happen there — a bumped `updated_at` would let
    a background rewrite outrank someone the operator actually talked about, and
    the move to the front would do the same thing more quietly, because
    projection's ranking sort is stable and the stored position is its tie-break.
    Both have to stay put, so this writes the person where they already are.

    `updated_at` is pinned from the existing record rather than trusted from
    `change`, exactly as `_mutate` pins it from the clock: what the caller may
    change here is the person's content, never their standing.
    """
    path = people_path(settings)
    with _STORE_LOCK:
        document = _read_document(path)
        existing = _require(document, person_id)
        updated = replace(change(existing), id=existing.id, updated_at=existing.updated_at)
        # Everyone keeps their index, including the person written. A hand-edited
        # store can hold one id twice; `_require` resolved the first of them and
        # only that one survives, which is the collapse `_mutate` performs too by
        # rebuilding the list around the single person it touched.
        rewritten: list[BackendPerson] = []
        for item in document.people:
            if item.id != person_id:
                rewritten.append(item)
            elif item is existing:
                rewritten.append(updated)
        _write_document(path, _Document(people=tuple(rewritten), sync=document.sync))
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
    """Rename one person, swapping when the new name is one of their own aliases.

    Renaming onto your OWN alias is the merge's undo (Codex A1-4): the operator
    merged "Lena" into "Linna" and then decided the robot had it right after all.
    The alias becomes the canonical name and the old canonical name becomes the
    alias, so the robot's records under either spelling still resolve here. Every
    other rename is the plain thing: the name changes and the aliases do not.
    """
    normalized = _normalized_name(name)
    key = normalized.casefold()

    def change(person: BackendPerson, document: _Document) -> BackendPerson:
        _assert_name_is_free(document, normalized, except_id=person.id)
        if key not in {alias.casefold() for alias in person.aliases}:
            return replace(person, name=normalized)
        swapped = [alias for alias in person.aliases if alias.casefold() != key]
        return replace(person, name=normalized, aliases=_deduped([person.name, *swapped], excluding={key}))

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
    """Link a person to a robot `faces.v1.json` record id (projection mints it).

    An id promoted to primary is dropped from `former_face_ids`: the two lists
    answer the same question ("does this robot record belong to this person?"),
    and one id in both would make "is it the primary" depend on which was read.
    """
    cleaned = _clean_str(face_id)
    return _mutate(
        settings,
        person_id,
        lambda person, _: replace(
            person,
            face_id=cleaned,
            former_face_ids=_former_face_ids_from(list(person.former_face_ids), cleaned),
        ),
    )


def add_former_face_id(settings: Settings, person_id: str, face_id: str) -> BackendPerson:
    """Remember one more robot record id as belonging to this person.

    Written by the sync layer when the robot re-enrolls someone under a name that
    is now an alias (Codex A1-1): the person keeps the primary id they already
    have, and the new record id is recorded here so the next diff reads that face
    as *known*. Without it the record would be re-reported as new on every diff
    and the push gate would never open again.
    """
    cleaned = _clean_str(face_id)

    def change(person: BackendPerson, _: _Document) -> BackendPerson:
        if cleaned is None:
            return person
        return replace(
            person,
            former_face_ids=_former_face_ids_from([*person.former_face_ids, cleaned], person.face_id),
        )

    return _mutate(settings, person_id, change)


# --------------------------------------------------------------------------
# merge
# --------------------------------------------------------------------------


def _merged_facts(target: BackendPerson, source: BackendPerson) -> tuple[BackendFact, ...]:
    """Interleave both fact lists newest-first, through the store's own dedupe.

    Same rule as the photos below, for the same two reasons: the projection emits
    the newest 20 and the UI prints each row's own `created_at`. Folding the whole
    source in ahead of the target — the shape a plain oldest-first replay leaves —
    would push the target's newer facts past the robot's cap to make room for the
    source's older ones, and render a list whose dates run backwards halfway down.

    A fact both people held is kept once, as the *target's* record: `add_fact`
    treats a duplicate as already stored rather than re-storing it, and the two
    rows differ only in an id and a timestamp the operator never asked to move.
    The sort is stable, so facts sharing a millisecond keep target-before-source
    order.
    """
    known = {fact.text.casefold() for fact in target.facts}
    merged = [*target.facts, *(fact for fact in source.facts if fact.text.casefold() not in known)]
    return tuple(sorted(merged, key=lambda fact: fact.created_at, reverse=True))


def _merged_photos(target: BackendPerson, source: BackendPerson) -> tuple[BackendPhoto, ...]:
    """Interleave both photo lists newest-first (Codex A3-1).

    Concatenating would be wrong, not merely untidy: `projection.embeddings_for`
    takes the *first* three embeddings, so a source's newer enrollment samples
    appended behind the target's older ones would never reach the robot. The sort
    is stable, so photos sharing a millisecond keep target-before-source order.
    """
    return tuple(sorted([*target.photos, *source.photos], key=lambda photo: photo.added_at, reverse=True))


def _move_photo_files(settings: Settings, source: BackendPerson, target_id: str) -> None:
    """Move one person's photo bytes into another's directory, all or nothing.

    `stored_as` is `<photo_id><ext>` and photo ids are unique across the store, so
    the destination can never already be taken. A partial move would leave the
    surviving records pointing at files that are neither here nor there, so a
    failure puts back what it moved and raises rather than writing the document.
    """
    destination_dir = photo_dir(settings, target_id)
    moved: list[tuple[Path, Path]] = []
    try:
        for photo in source.photos:
            origin = photo_path(settings, source.id, photo)
            if origin is None or not origin.is_file():
                continue
            destination_dir.mkdir(parents=True, exist_ok=True)
            destination = destination_dir / str(photo.stored_as)
            origin.replace(destination)
            moved.append((origin, destination))
    except OSError:
        for origin, destination in reversed(moved):
            try:
                destination.replace(origin)
            except OSError as exc:
                logger.warning("Could not put the photo bytes at %s back: %s", destination, exc)
        raise


def merge_people(settings: Settings, target_id: str, source_id: str) -> BackendPerson:
    """Fold one person into another and delete the source, returning the survivor.

    The robot misheard a name and enrolled the same person twice; this is the
    operator saying so. The target survives with its own id and name, and gains:
    the source's facts (deduped), the source's photos (records *and* bytes,
    interleaved newest-first), the source's name and aliases as aliases of its
    own, and every robot record id either of them ever carried.

    `face_id` is the one thing that is not simply unioned: the robot can hold only
    one primary link per person, so the target keeps its own and adopts the
    source's only when it had none. Every id that does not end up primary lands in
    `former_face_ids` (Codex A2-1), including ids the *source* had already
    inherited from an earlier merge — a chain of merges must not forget the
    robot ids at its start, or the faces still living under them would read as
    strangers on the next diff.
    """
    if target_id == source_id:
        raise MergeError("A person cannot be merged into themselves.")

    path = people_path(settings)
    with _STORE_LOCK:
        document = _read_document(path)
        target = _require(document, target_id)
        source = _require(document, source_id)

        aliases = _deduped(
            [*target.aliases, source.name, *source.aliases],
            excluding={target.name.casefold()},
        )
        # The index invariant has to survive the merge: every string the survivor
        # would answer to must not already reach somebody else. Unreachable
        # through the writers — they all check the same index — but a store is a
        # file an operator can edit, and one name resolving two ways afterwards
        # would silently strand whichever person lost the lookup.
        for key in {target.name.casefold(), *(alias.casefold() for alias in aliases)}:
            clash = _holder_of(document, key, excluding=(target_id, source_id))
            if clash is not None:
                raise DuplicateNameError(f"{key!r} is already how {clash.name!r} is reached.")

        face_id = target.face_id if target.face_id is not None else source.face_id
        unadopted = [] if source.face_id in (None, face_id) else [str(source.face_id)]
        former = _former_face_ids_from(
            [*target.former_face_ids, *source.former_face_ids, *unadopted], face_id
        )

        # Bytes first: a failure here leaves the document untouched, so the merge
        # simply has not happened yet rather than half-happened.
        _move_photo_files(settings, source, target_id)

        merged = replace(
            target,
            face_id=face_id,
            facts=_merged_facts(target, source),
            photos=_merged_photos(target, source),
            aliases=aliases,
            former_face_ids=former,
            updated_at=_now_ms(),
        )
        remaining = tuple(item for item in document.people if item.id not in {target_id, source_id})
        _write_document(path, _Document(people=(merged, *remaining), sync=document.sync))
        shutil.rmtree(photo_dir(settings, source_id), ignore_errors=True)
        logger.info("Merged %r into %r; aliases are now %s.", source.name, merged.name, list(merged.aliases))
        return merged


# --------------------------------------------------------------------------
# facts
# --------------------------------------------------------------------------


def add_fact(settings: Settings, person_id: str, text: str) -> BackendFact:
    """Store one fact, normalized to exactly what projection will write to the robot.

    A fact this person already has (case-insensitively) is returned unchanged
    instead of being stored twice — the same rule `people.add_person_fact`
    applies on the robot, and for the same reason: the robot's voice path and the
    sync layer both re-offer facts they have already offered, and a store that
    counted every re-offer would project a person's memory back to them twice.
    The record is still touched, so `updated_at` moves and the person keeps their
    place in projection's ranking; they were just talked about either way.
    """
    normalized = _normalized_fact(text)
    key = normalized.casefold()
    created = BackendFact(id=_make_id(_FACT_ID_PREFIX), text=normalized, created_at=_now_ms())
    stored: list[BackendFact] = []

    def change(person: BackendPerson, _: _Document) -> BackendPerson:
        duplicate = next((fact for fact in person.facts if fact.text.casefold() == key), None)
        if duplicate is not None:
            stored.append(duplicate)
            return person
        stored.append(created)
        return replace(person, facts=(created, *person.facts))

    _mutate(settings, person_id, change)
    return stored[0]


def replace_facts(
    settings: Settings,
    person_id: str,
    texts: Sequence[str],
    *,
    preserve_updated_at: bool = False,
) -> BackendPerson:
    """Replace one person's whole fact list in a single write; `texts[0]` is the newest.

    The bulk counterpart of `add_fact`, written for the consolidation pass: an
    LLM rewrites a person's memory and hands back the list it wants stored, most
    useful first. That order is stored as-is, because the store keeps facts
    newest-first and projection replays them oldest-first into the robot's
    prepending writer — so what the caller puts first is what the robot answers
    with first. There is **no cap**: the Mac holds everything (see the module
    docstring), and only projection trims to the robot's newest 20.

    A text this person already has keeps its existing record — same id, same
    `created_at` — for the reason `add_fact` returns a fact already stored rather
    than storing it twice: it is the same fact, and re-minting it would move a
    date the operator never changed and break a fact id a client is holding to
    delete by. Only genuinely new texts get a new record, and the surviving
    spelling is the stored one, as `add_fact`'s duplicate check also leaves it.
    The consequence is that the list's order is the caller's order and no longer
    necessarily `created_at`-descending; `_merged_facts` sorts by `created_at`,
    so a later merge restores chronological order for the people it touches.

    `preserve_updated_at=True` leaves the person exactly where they were — the
    timestamp *and* their place in the stored list, via `_rewrite_in_place`. A
    background pass over everybody must not reshuffle projection's ranking, and
    the position matters as much as the timestamp because that sort is stable.
    The default is `False`: an ordinary edit, touched and moved to the front like
    every other one.
    """
    normalized = _normalized_facts(texts)
    now = _now_ms()

    def change(person: BackendPerson) -> BackendPerson:
        held = {fact.text.casefold(): fact for fact in person.facts}
        facts: list[BackendFact] = []
        for text in normalized:
            kept = held.get(text.casefold())
            if kept is None:
                kept = BackendFact(id=_make_id(_FACT_ID_PREFIX), text=text, created_at=now)
            facts.append(kept)
        return replace(person, facts=tuple(facts))

    if preserve_updated_at:
        return _rewrite_in_place(settings, person_id, change)
    return _mutate(settings, person_id, lambda person, _: change(person))


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


def _write_photo(
    settings: Settings, person_id: str, display_name: str, raw: bytes, *, display_only: bool
) -> BackendPhoto:
    """Write bytes for one person and record the photo. The caller holds the lock.

    The file is named after the generated photo id; `display_name` is kept only
    as a label. Nothing a client sends can influence where the bytes land.
    """
    photo_id = _make_id(_PHOTO_ID_PREFIX)
    stored_as = f"{photo_id}{_photo_extension(display_name)}"
    photo = BackendPhoto(
        id=photo_id,
        display_name=" ".join(display_name.split()) or stored_as,
        stored_as=stored_as,
        added_at=_now_ms(),
        embedding=None,
        error=None,
        synthetic=False,
        display_only=display_only,
    )
    directory = photo_dir(settings, person_id)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / stored_as).write_bytes(raw)
    _mutate(settings, person_id, lambda person, _: replace(person, photos=(photo, *person.photos)))
    return photo


def add_photo(settings: Settings, person_id: str, display_name: str, raw: bytes) -> BackendPhoto:
    """Store uploaded bytes for one person and record the photo (embedding still unset)."""
    with _STORE_LOCK:
        # Fail before writing bytes we would then have to clean up.
        if get_person(settings, person_id) is None:
            raise PersonNotFoundError(f"No person with id {person_id!r}.")
        return _write_photo(settings, person_id, display_name, raw, display_only=False)


def _photo_holding(settings: Settings, person: BackendPerson, digest: str) -> BackendPhoto | None:
    """Return this person's photo whose bytes hash to `digest`, if they have one."""
    for photo in person.photos:
        path = photo_path(settings, person.id, photo)
        if path is None:
            continue
        try:
            existing = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            # A photo we cannot read is not a match; it is also not a reason to
            # refuse the new bytes.
            logger.warning("Could not hash the photo bytes at %s: %s", path, exc)
            continue
        if existing == digest:
            return photo
    return None


def add_display_photo(settings: Settings, person_id: str, display_name: str, raw: bytes) -> BackendPhoto:
    """Store bytes that are a picture and nothing else — the robot's enrollment snapshot.

    `display_only` bytes never become a recognition sample: no embedding is ever
    computed for them and the projection skips them structurally, so the person's
    samples stay exactly what the robot enrolled.

    Deduped by content against *every* photo this person already has, uploads
    included: the sync layer re-offers the same snapshot on every import that
    touches the face, and a matching photo is returned unchanged rather than
    stored twice — the same answer `add_fact` gives a fact the person already
    has, and for the same reason.
    """
    digest = hashlib.sha256(raw).hexdigest()
    with _STORE_LOCK:
        person = get_person(settings, person_id)
        if person is None:
            raise PersonNotFoundError(f"No person with id {person_id!r}.")
        existing = _photo_holding(settings, person, digest)
        if existing is not None:
            return existing
        return _write_photo(settings, person_id, display_name, raw, display_only=True)


def add_synthetic_photo(settings: Settings, person_id: str, embedding: Sequence[float]) -> BackendPhoto:
    """Record an embedding imported from the robot: no bytes, but it still projects back."""
    vector = _embedding_from(embedding)
    if vector is None:
        raise ValueError("A synthetic photo needs an embedding of real numbers.")
    _require_full_embedding(vector)

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
    if vector is not None:
        _require_full_embedding(vector)
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
