"""Per-person memory facts, keyed by the face store's name, in `people.v1.json`.

A third deliberate sibling of `memory.py` rather than an extension of it
(D-013): `MemoryFact.to_json` is the shape the mobile app reads, a locked
external contract that must not grow a person dimension. It is a sibling of
`faces.py` too — that store holds ~1.2 KB of embedding per person and is read
on every recognition attempt, while these facts are read on the greeting path
and by the person tools, so the two stay separate files with one shared key:
the normalized name.

Every idiom here is copied from those two: schema version, module-level lock,
atomic tmp+replace writes, a `*_for_instance` path helper, and tolerant readers
that degrade to "nobody is known" instead of raising through their callers.
Name rules come from `faces.normalize_face_name` and fact rules from
`memory.normalize_memory_text`, so a name that reaches one store reaches all
three the same way.

**No image and no embedding is ever persisted here.** A record is a name, an
optional `faces.v1.json` record id, up to `MAX_FACTS_PER_PERSON` short text
facts, and two timestamps. The file sits beside its two siblings in the app's
instance directory, so it shares their lifecycle exactly: wiped by every app
reinstall, carried across only by the deploy skill's backup/restore ritual.
"""

from __future__ import annotations
import os
import json
import time
import random
import string
import logging
import threading
from typing import Final
from pathlib import Path
from dataclasses import dataclass
from collections.abc import Mapping, Sequence

from reachy_companion import faces, memory


logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
PEOPLE_FILENAME = "people.v1.json"
# Both caps are re-exported from the stores that own the rules, so the three
# files cannot drift apart: this store is keyed by face-store names, and its
# facts are normalized by `memory.normalize_memory_text`, which enforces the
# memory store's own character cap.
MAX_PEOPLE: Final[int] = faces.MAX_PEOPLE
MAX_FACT_CHARS: Final[int] = memory.MAX_FACT_CHARS
MAX_FACTS_PER_PERSON = 20
# The one default for how many person facts recall surfaces, greeting and
# `who_is_this` alike — both read `FACE_GREETING_FACTS` and must not drift.
PERSON_FACTS_DEFAULT: Final[int] = 6
_PERSON_ID_PREFIX: Final[str] = "p"
_FACT_ID_PREFIX: Final[str] = "m"

_STORE_LOCK = threading.Lock()


@dataclass(frozen=True)
class PersonFact:
    """One short fact about one person."""

    id: str
    text: str
    created_at: int

    def to_json(self) -> dict[str, object]:
        """Return the persisted JSON shape for this fact."""
        return {
            "id": self.id,
            "text": self.text,
            "createdAt": self.created_at,
        }


@dataclass(frozen=True)
class PersonRecord:
    """One known person: a name, an optional face link, and their facts, newest first."""

    id: str
    face_id: str | None
    name: str
    facts: tuple[PersonFact, ...]
    created_at: int
    updated_at: int

    def to_json(self) -> dict[str, object]:
        """Return the persisted JSON shape for this person."""
        return {
            "id": self.id,
            "faceId": self.face_id,
            "name": self.name,
            # The cap is applied at the file boundary as well as at construction:
            # whatever a caller hands the writer, the store never grows past it.
            "facts": [fact.to_json() for fact in self.facts[:MAX_FACTS_PER_PERSON]],
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }


@dataclass(frozen=True)
class ForgetPersonFactResult:
    """Result of removing one fact from one person."""

    removed: PersonFact | None
    candidates: tuple[PersonFact, ...]


def people_path_for_instance(instance_path: str | Path | None = None) -> Path:
    """Return the per-person memory JSON path for this app instance."""
    if instance_path is not None:
        return Path(instance_path).expanduser() / PEOPLE_FILENAME

    data_home = os.getenv("XDG_DATA_HOME")
    data_root = Path(data_home).expanduser() if data_home else Path.home() / ".local" / "share"
    return data_root / "reachy_companion" / PEOPLE_FILENAME


def _make_id(prefix: str) -> str:
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f"{prefix}_{int(time.time() * 1000)}_{suffix}"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _clean_face_id(value: object) -> str | None:
    """Return a usable `faces.v1.json` record id, or None for anything else."""
    if not isinstance(value, str):
        return None
    return value.strip() or None


def _find(records: Sequence[PersonRecord], normalized_name: str) -> PersonRecord | None:
    """Return the record whose name matches case- and whitespace-insensitively."""
    key = normalized_name.casefold()
    return next((record for record in records if record.name.casefold() == key), None)


def _fact_from_json(value: object) -> PersonFact | None:
    if not isinstance(value, Mapping):
        return None

    fact_id = value.get("id")
    text = value.get("text")
    created_at = value.get("createdAt")

    if not isinstance(fact_id, str):
        return None
    if not isinstance(text, str):
        return None
    if not isinstance(created_at, (int, float)):
        return None

    normalized = memory.normalize_memory_text(text)
    if not normalized:
        return None

    return PersonFact(id=fact_id, text=normalized, created_at=int(created_at))


def _record_from_json(value: object) -> PersonRecord | None:
    if not isinstance(value, Mapping):
        return None

    record_id = value.get("id")
    name = value.get("name")
    created_at = value.get("createdAt")
    updated_at = value.get("updatedAt")
    facts_value = value.get("facts")

    if not isinstance(record_id, str) or not isinstance(name, str):
        return None
    if not isinstance(created_at, (int, float)) or not isinstance(updated_at, (int, float)):
        return None
    if not isinstance(facts_value, list):
        return None

    normalized_name = faces.normalize_face_name(name)
    if not normalized_name:
        return None

    facts: list[PersonFact] = []
    for item in facts_value:
        fact = _fact_from_json(item)
        if fact is not None:
            facts.append(fact)

    # A missing, null or non-string `faceId` reads as "not linked to a face" —
    # a person can be known by name alone, so it is never a reason to drop the
    # record.
    return PersonRecord(
        id=record_id,
        face_id=_clean_face_id(value.get("faceId")),
        name=normalized_name,
        facts=tuple(facts[:MAX_FACTS_PER_PERSON]),
        created_at=int(created_at),
        updated_at=int(updated_at),
    )


def _read_people_file(path: Path) -> list[PersonRecord]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    # ValueError covers UnicodeDecodeError, exactly as in `faces.py`: corrupt
    # *bytes* never reach the JSON decoder, and a bad store must still read as
    # "nobody is known" rather than raise through the greeting path.
    except (OSError, ValueError) as exc:
        logger.warning("Failed to read person store at %s: %s", path, exc)
        return []

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("Failed to parse person store at %s: %s", path, exc)
        return []

    if not isinstance(parsed, Mapping):
        return []

    people_value = parsed.get("people")
    if not isinstance(people_value, list):
        return []

    records: list[PersonRecord] = []
    for item in people_value:
        record = _record_from_json(item)
        if record is not None:
            records.append(record)
    # Stable sort keeps the persisted order for equal timestamps, which is what
    # makes eviction deterministic when several writes land in the same
    # millisecond (writes always put the touched record first).
    records.sort(key=lambda record: record.updated_at, reverse=True)
    return records[:MAX_PEOPLE]


def _write_people_file(path: Path, records: list[PersonRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": SCHEMA_VERSION,
        "people": [record.to_json() for record in records[:MAX_PEOPLE]],
    }
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp_path.replace(path)
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


def _upserted(
    records: list[PersonRecord],
    normalized_name: str,
    *,
    face_id: str | None = None,
    fact: PersonFact | None = None,
) -> tuple[PersonRecord, list[PersonRecord]]:
    """Return the touched record and the whole store with that record moved to the front."""
    existing = _find(records, normalized_name)
    now = _now_ms()

    if existing is None:
        record = PersonRecord(
            id=_make_id(_PERSON_ID_PREFIX),
            face_id=face_id,
            name=normalized_name,
            facts=() if fact is None else (fact,),
            created_at=now,
            updated_at=now,
        )
        # Past the cap the least recently updated person is evicted, the same
        # small-cache policy `faces.upsert_face` applies to embeddings.
        return record, [record, *records][:MAX_PEOPLE]

    record = PersonRecord(
        id=existing.id,
        # An existing link is never overwritten: the face store is the authority
        # on which face a record belongs to, and a silent re-link would move one
        # person's facts onto another person's face.
        face_id=existing.face_id or face_id,
        name=existing.name,  # the first spelling is kept, as in `faces.upsert_face`
        facts=existing.facts if fact is None else (fact, *existing.facts)[:MAX_FACTS_PER_PERSON],
        created_at=existing.created_at,
        updated_at=now,
    )
    remaining = [item for item in records if item.id != existing.id]
    return record, [record, *remaining]


def list_people(instance_path: str | Path | None = None) -> list[PersonRecord]:
    """Return known people, most recently updated first."""
    with _STORE_LOCK:
        return list(_read_people_file(people_path_for_instance(instance_path)))


def facts_for_person(
    instance_path: str | Path | None,
    name: str,
    *,
    limit: int | None = None,
) -> list[PersonFact]:
    """Return the facts stored for one person, newest first.

    The name is matched case- and whitespace-insensitively, exactly as
    `faces.upsert_face` matches it. An unknown person is not an error: it
    simply has no facts.
    """
    normalized_name = faces.normalize_face_name(name)
    if not normalized_name:
        return []

    with _STORE_LOCK:
        records = _read_people_file(people_path_for_instance(instance_path))
        record = _find(records, normalized_name)
        if record is None:
            return []
        if limit is None:
            return list(record.facts)
        return list(record.facts[: max(limit, 0)])


def add_person_fact(
    instance_path: str | Path | None,
    name: str,
    text: str,
    *,
    face_id: str | None = None,
) -> PersonFact | None:
    """Store one short fact about one person, creating the person if needed.

    A fact whose text already exists for that person (case-insensitively) is
    returned unchanged instead of being stored twice. The record is still
    touched in that case: its `updated_at` moves to now, which is the eviction
    order, and a missing `face_id` is backfilled — the person was just talked
    about either way. Returns None when the name or the fact normalizes to
    nothing.
    """
    normalized_name = faces.normalize_face_name(name)
    if not normalized_name:
        logger.warning("Refusing to store a person fact under an empty name.")
        return None

    normalized_text = memory.normalize_memory_text(text)
    if not normalized_text:
        logger.warning("Refusing to store an empty fact for %r.", normalized_name)
        return None

    path = people_path_for_instance(instance_path)
    with _STORE_LOCK:
        records = _read_people_file(path)

        existing = _find(records, normalized_name)
        duplicate = None
        if existing is not None:
            key = normalized_text.lower()
            duplicate = next((fact for fact in existing.facts if fact.text.lower() == key), None)

        if duplicate is None:
            fact = PersonFact(id=_make_id(_FACT_ID_PREFIX), text=normalized_text, created_at=_now_ms())
        else:
            fact = duplicate

        _, updated = _upserted(
            records,
            normalized_name,
            face_id=_clean_face_id(face_id),
            fact=None if duplicate is not None else fact,
        )
        _write_people_file(path, updated)
        return fact


def forget_person_fact(
    instance_path: str | Path | None,
    name: str,
    *,
    query: str,
) -> ForgetPersonFactResult:
    """Remove one of a person's facts by case-insensitive substring query.

    The search is scoped to that person, so forgetting "the dog" for one person
    never touches another's. `candidates` lists every match, newest first, and
    the first one is the fact removed.
    """
    normalized_name = faces.normalize_face_name(name)
    normalized_query = memory.normalize_memory_text(query).lower()
    if not normalized_name or not normalized_query:
        return ForgetPersonFactResult(removed=None, candidates=())

    path = people_path_for_instance(instance_path)
    with _STORE_LOCK:
        records = _read_people_file(path)
        existing = _find(records, normalized_name)
        if existing is None:
            return ForgetPersonFactResult(removed=None, candidates=())

        candidates = tuple(fact for fact in existing.facts if normalized_query in fact.text.lower())
        if not candidates:
            return ForgetPersonFactResult(removed=None, candidates=())

        removed = candidates[0]
        # A removal is a change to the record, so `updated_at` moves with it and
        # the record goes back to the front — `updated_at` means "last written",
        # the same as it does in `faces.py`.
        record = PersonRecord(
            id=existing.id,
            face_id=existing.face_id,
            name=existing.name,
            facts=tuple(fact for fact in existing.facts if fact.id != removed.id),
            created_at=existing.created_at,
            updated_at=_now_ms(),
        )
        remaining = [item for item in records if item.id != existing.id]
        _write_people_file(path, [record, *remaining])
        return ForgetPersonFactResult(removed=removed, candidates=candidates)


def upsert_person(
    instance_path: str | Path | None,
    name: str,
    *,
    face_id: str | None = None,
) -> PersonRecord | None:
    """Create or touch one person record without adding a fact.

    This is the entry point for linking a name to a face store record, or for
    marking a known person as just seen. Returns None when the name normalizes
    to nothing.
    """
    normalized_name = faces.normalize_face_name(name)
    if not normalized_name:
        logger.warning("Refusing to store a person under an empty name.")
        return None

    path = people_path_for_instance(instance_path)
    with _STORE_LOCK:
        records = _read_people_file(path)
        record, updated = _upserted(records, normalized_name, face_id=_clean_face_id(face_id))
        _write_people_file(path, updated)
        return record


def forget_person(instance_path: str | Path | None, name: str) -> PersonRecord | None:
    """Remove one person and every fact stored about them, returning the removed record."""
    normalized_name = faces.normalize_face_name(name)
    if not normalized_name:
        return None

    path = people_path_for_instance(instance_path)
    with _STORE_LOCK:
        records = _read_people_file(path)
        removed = _find(records, normalized_name)
        if removed is None:
            return None

        _write_people_file(path, [record for record in records if record.id != removed.id])
        return removed


def clear_people(instance_path: str | Path | None = None) -> None:
    """Forget every stored person and every fact about them."""
    path = people_path_for_instance(instance_path)
    with _STORE_LOCK:
        _write_people_file(path, [])
