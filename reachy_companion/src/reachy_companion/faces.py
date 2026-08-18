"""Persistent face memory: names plus SFace embeddings, in `faces.v1.json`.

A deliberate sibling of `memory.py` rather than an extension of it (D-013):
`MemoryFact.to_json` is the shape the mobile app reads, and an embedding is
~1.2 KB that would then be re-read and re-serialized on every `remember` call
and every prompt build. Same idioms throughout — schema version, module-level
lock, atomic tmp+replace, `*_for_instance` path helper, tolerant readers that
return `[]` on corruption — so the two stores age the same way.

**No image is ever persisted here.** A record is a name, up to three
L2-normalized 128-float vectors, and two timestamps. Vectors are rounded to
6 decimal places (cosine error < 1e-6) so the file stays readable on the robot.
"""

from __future__ import annotations
import os
import json
import time
import random
import string
import logging
import threading
from pathlib import Path
from dataclasses import dataclass
from collections.abc import Mapping, Sequence

import numpy as np
from numpy.typing import NDArray


logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
FACES_FILENAME = "faces.v1.json"
MAX_PEOPLE = 12
MAX_EMBEDDINGS_PER_PERSON = 3
EMBEDDING_DIM = 128
MAX_NAME_CHARS = 40
_STORED_DECIMALS = 6

_STORE_LOCK = threading.Lock()


@dataclass(frozen=True)
class FaceRecord:
    """One remembered person: a name and up to three face embeddings."""

    id: str
    name: str
    embeddings: tuple[tuple[float, ...], ...]
    created_at: int
    updated_at: int

    def to_json(self) -> dict[str, object]:
        """Return the persisted JSON shape."""
        return {
            "id": self.id,
            "name": self.name,
            "embeddings": [list(embedding) for embedding in self.embeddings],
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }


def faces_path_for_instance(instance_path: str | Path | None = None) -> Path:
    """Return the face-memory JSON path for this app instance."""
    if instance_path is not None:
        return Path(instance_path).expanduser() / FACES_FILENAME

    data_home = os.getenv("XDG_DATA_HOME")
    data_root = Path(data_home).expanduser() if data_home else Path.home() / ".local" / "share"
    return data_root / "reachy_companion" / FACES_FILENAME


def normalize_face_name(name: str) -> str:
    """Collapse whitespace and enforce the name length cap."""
    normalized = " ".join(name.split()).strip()
    if len(normalized) <= MAX_NAME_CHARS:
        return normalized
    return normalized[:MAX_NAME_CHARS]


def _make_id() -> str:
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f"f_{int(time.time() * 1000)}_{suffix}"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _embedding_from_json(value: object) -> tuple[float, ...] | None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return None
    if len(value) != EMBEDDING_DIM:
        return None
    numbers: list[float] = []
    for item in value:
        if not isinstance(item, (int, float)) or isinstance(item, bool):
            return None
        numbers.append(float(item))
    return tuple(numbers)


def _record_from_json(value: object) -> FaceRecord | None:
    if not isinstance(value, Mapping):
        return None

    record_id = value.get("id")
    name = value.get("name")
    created_at = value.get("createdAt")
    updated_at = value.get("updatedAt")
    embeddings_value = value.get("embeddings")

    if not isinstance(record_id, str) or not isinstance(name, str):
        return None
    if not isinstance(created_at, (int, float)) or not isinstance(updated_at, (int, float)):
        return None
    if not isinstance(embeddings_value, list):
        return None

    normalized_name = normalize_face_name(name)
    if not normalized_name:
        return None

    embeddings: list[tuple[float, ...]] = []
    for item in embeddings_value:
        embedding = _embedding_from_json(item)
        if embedding is not None:
            embeddings.append(embedding)
    if not embeddings:
        return None

    return FaceRecord(
        id=record_id,
        name=normalized_name,
        embeddings=tuple(embeddings[-MAX_EMBEDDINGS_PER_PERSON:]),
        created_at=int(created_at),
        updated_at=int(updated_at),
    )


def _read_faces_file(path: Path) -> list[FaceRecord]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError as exc:
        logger.warning("Failed to read face store at %s: %s", path, exc)
        return []

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("Failed to parse face store at %s: %s", path, exc)
        return []

    if not isinstance(parsed, Mapping):
        return []

    faces_value = parsed.get("faces")
    if not isinstance(faces_value, list):
        return []

    records: list[FaceRecord] = []
    for item in faces_value:
        record = _record_from_json(item)
        if record is not None:
            records.append(record)
    # Stable sort keeps the persisted order for equal timestamps, which is what
    # makes eviction deterministic when several upserts land in the same
    # millisecond (writes always put the touched record first).
    records.sort(key=lambda record: record.updated_at, reverse=True)
    return records[:MAX_PEOPLE]


def _write_faces_file(path: Path, records: list[FaceRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": SCHEMA_VERSION,
        "faces": [record.to_json() for record in records[:MAX_PEOPLE]],
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


def _to_stored_embedding(embedding: NDArray[np.float32]) -> tuple[float, ...]:
    """Validate, L2-normalize and round one embedding for persistence."""
    vector = np.asarray(embedding, dtype=np.float64).reshape(-1)
    if vector.size != EMBEDDING_DIM:
        raise ValueError(f"embedding must have {EMBEDDING_DIM} values, got {vector.size}")
    if not np.all(np.isfinite(vector)):
        raise ValueError("embedding contains non-finite values")
    norm = float(np.linalg.norm(vector))
    if norm > 0.0:
        vector = vector / norm
    return tuple(round(float(value), _STORED_DECIMALS) for value in vector)


def list_faces(instance_path: str | Path | None = None) -> list[FaceRecord]:
    """Return remembered people, most recently updated first."""
    with _STORE_LOCK:
        return list(_read_faces_file(faces_path_for_instance(instance_path)))


def upsert_face(
    instance_path: str | Path | None,
    name: str,
    embedding: NDArray[np.float32],
) -> FaceRecord | None:
    """Store one embedding under `name`, returning the resulting record.

    A name already known (case- and whitespace-insensitively) gains the new
    sample, ring-buffered to `MAX_EMBEDDINGS_PER_PERSON` with the oldest
    dropped. A new name creates a record and, past `MAX_PEOPLE`, evicts the
    least recently updated person. Returns None when the name normalizes to
    nothing; raises ValueError on a malformed embedding.
    """
    stored_embedding = _to_stored_embedding(embedding)
    normalized_name = normalize_face_name(name)
    if not normalized_name:
        logger.warning("Refusing to store a face under an empty name.")
        return None

    path = faces_path_for_instance(instance_path)
    with _STORE_LOCK:
        records = _read_faces_file(path)
        key = normalized_name.casefold()
        existing = next((record for record in records if record.name.casefold() == key), None)

        if existing is not None:
            updated = FaceRecord(
                id=existing.id,
                name=existing.name,
                embeddings=(*existing.embeddings, stored_embedding)[-MAX_EMBEDDINGS_PER_PERSON:],
                created_at=existing.created_at,
                updated_at=_now_ms(),
            )
            remaining = [record for record in records if record.id != existing.id]
            _write_faces_file(path, [updated, *remaining])
            return updated

        now = _now_ms()
        record = FaceRecord(
            id=_make_id(),
            name=normalized_name,
            embeddings=(stored_embedding,),
            created_at=now,
            updated_at=now,
        )
        _write_faces_file(path, [record, *records][:MAX_PEOPLE])
        return record


def forget_face(instance_path: str | Path | None, name: str) -> FaceRecord | None:
    """Remove one remembered person by name, returning the removed record."""
    normalized_name = normalize_face_name(name)
    if not normalized_name:
        return None

    path = faces_path_for_instance(instance_path)
    with _STORE_LOCK:
        records = _read_faces_file(path)
        key = normalized_name.casefold()
        removed = next((record for record in records if record.name.casefold() == key), None)
        if removed is None:
            return None

        _write_faces_file(path, [record for record in records if record.id != removed.id])
        return removed


def clear_faces(instance_path: str | Path | None = None) -> None:
    """Forget every remembered face."""
    path = faces_path_for_instance(instance_path)
    with _STORE_LOCK:
        _write_faces_file(path, [])
