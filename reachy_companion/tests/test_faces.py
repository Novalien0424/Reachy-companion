"""Store tests for `faces.v1.json` — the sibling of `memory.v1.json`.

No model and no camera here: this file pins the persistence contract only
(ring buffer, eviction, corruption tolerance, atomic writes).
"""

import json
import logging
from pathlib import Path

import numpy as np
import pytest
from numpy.typing import NDArray

from reachy_companion.faces import (
    MAX_PEOPLE,
    EMBEDDING_DIM,
    FACES_FILENAME,
    MAX_NAME_CHARS,
    ALIGNMENT_VERSION,
    MAX_EMBEDDINGS_PER_PERSON,
    list_faces,
    clear_faces,
    forget_face,
    upsert_face,
    faces_path_for_instance,
)


def _vector(index: int) -> NDArray[np.float32]:
    """Return the `index`-th canonical unit vector, a valid stored embedding."""
    return np.eye(EMBEDDING_DIM, dtype=np.float32)[index % EMBEDDING_DIM]


def _write_record(tmp_path: Path, name: str, extra: dict[str, object] | None = None) -> None:
    """Write one hand-built record straight to the store file.

    The alignment cases need bytes on disk that `upsert_face` cannot produce:
    a record stamped by an older pipeline, or one written before the marker
    existed at all.
    """
    record: dict[str, object] = {
        "id": f"f_{name}",
        "name": name,
        "embeddings": [[0.0] * EMBEDDING_DIM],
        "createdAt": 1000,
        "updatedAt": 1000,
    }
    record.update(extra or {})
    path = faces_path_for_instance(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "faces": [record]}), encoding="utf-8")


def test_upsert_appends_and_ring_buffers(tmp_path: Path) -> None:
    """A returning person accumulates at most three samples, keeping the newest."""
    vectors = [_vector(index) for index in range(4)]
    for vector in vectors:
        upsert_face(tmp_path, "小明", vector)

    (record,) = list_faces(tmp_path)

    assert record.name == "小明"
    assert len(record.embeddings) == MAX_EMBEDDINGS_PER_PERSON
    assert record.embeddings[-1] == pytest.approx(vectors[3].tolist(), abs=1e-6)
    assert record.embeddings[0] == pytest.approx(vectors[1].tolist(), abs=1e-6)
    assert record.updated_at >= record.created_at


def test_upsert_matches_names_case_and_whitespace_insensitively(tmp_path: Path) -> None:
    """"  Ada " and "ADA" are the same person, not three records."""
    upsert_face(tmp_path, "Ada", _vector(0))
    upsert_face(tmp_path, "  ada  ", _vector(1))
    upsert_face(tmp_path, "ADA", _vector(2))

    records = list_faces(tmp_path)

    assert len(records) == 1
    assert records[0].name == "Ada"  # the first spelling is kept
    assert len(records[0].embeddings) == 3


def test_upsert_caps_the_name_length(tmp_path: Path) -> None:
    """A model-supplied name is untrusted input; cap it before it reaches the store."""
    record = upsert_face(tmp_path, "x" * (MAX_NAME_CHARS + 40), _vector(0))

    assert record is not None
    assert len(record.name) == MAX_NAME_CHARS


def test_upsert_rejects_an_empty_name(tmp_path: Path) -> None:
    """Nothing is stored for a blank name, and no file is created."""
    assert upsert_face(tmp_path, "   ", _vector(0)) is None
    assert list_faces(tmp_path) == []


def test_upsert_rejects_a_wrong_sized_embedding(tmp_path: Path) -> None:
    """A wrong embedding dimension is a programming error, not user data — fail loudly."""
    with pytest.raises(ValueError):
        upsert_face(tmp_path, "Ada", np.zeros(64, dtype=np.float32))


def test_max_people_evicts_the_least_recently_updated(tmp_path: Path) -> None:
    """The store is a small cache: the person you saw longest ago is the one dropped."""
    for index in range(MAX_PEOPLE):
        upsert_face(tmp_path, f"person{index}", _vector(index))

    upsert_face(tmp_path, "person0", _vector(0))  # person0 is now the most recently seen
    upsert_face(tmp_path, "newcomer", _vector(MAX_PEOPLE))

    names = [record.name for record in list_faces(tmp_path)]

    assert len(names) == MAX_PEOPLE
    assert names[0] == "newcomer"
    assert "person0" in names
    assert "person1" not in names  # least-recently-updated, evicted


def test_list_faces_is_newest_updated_first(tmp_path: Path) -> None:
    """Ordering is the eviction policy made visible; matching does not depend on it."""
    upsert_face(tmp_path, "first", _vector(0))
    upsert_face(tmp_path, "second", _vector(1))
    upsert_face(tmp_path, "first", _vector(2))

    assert [record.name for record in list_faces(tmp_path)] == ["first", "second"]


def test_forget_face_removes_one_person(tmp_path: Path) -> None:
    """`forget_face` returns what it removed, or None when the name is unknown."""
    upsert_face(tmp_path, "Ada", _vector(0))
    upsert_face(tmp_path, "Grace", _vector(1))

    removed = forget_face(tmp_path, "ada")

    assert removed is not None
    assert removed.name == "Ada"
    assert [record.name for record in list_faces(tmp_path)] == ["Grace"]
    assert forget_face(tmp_path, "nobody") is None


def test_clear_faces_empties_the_store(tmp_path: Path) -> None:
    """The privacy escape hatch: one call and nobody is remembered."""
    upsert_face(tmp_path, "Ada", _vector(0))

    clear_faces(tmp_path)

    assert list_faces(tmp_path) == []
    assert faces_path_for_instance(tmp_path).is_file()


def test_corrupt_store_reads_as_empty_and_never_raises(tmp_path: Path) -> None:
    """A truncated JSON file must degrade to "nobody is enrolled", not crash a session."""
    path = faces_path_for_instance(tmp_path)
    path.write_text('{"version": 1, "faces": [{"id": "f_1", "nam', encoding="utf-8")

    assert list_faces(tmp_path) == []

    path.write_text(json.dumps({"version": 1, "faces": "not-a-list"}), encoding="utf-8")

    assert list_faces(tmp_path) == []


def test_corrupt_bytes_read_as_empty_and_never_raise(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """A store that is not even valid UTF-8 must degrade the same way bad JSON does.

    `UnicodeDecodeError` never reaches the JSON decoder, so before the fix it
    escaped the tolerant reader entirely and surfaced from `list_faces` — which
    the recognizer calls inside its own load `try`, misreporting a healthy
    recognizer as "model load failed".
    """
    path = faces_path_for_instance(tmp_path)
    path.write_bytes(b'{"version": 1, "faces": [\xff\xfe]}')

    with caplog.at_level(logging.WARNING, logger="reachy_companion.faces"):
        assert list_faces(tmp_path) == []

    assert "Failed to read face store" in caplog.text


def test_malformed_records_are_dropped_individually(tmp_path: Path) -> None:
    """One bad record must not take the good ones down with it."""
    path = faces_path_for_instance(tmp_path)
    good = {
        "id": "f_1",
        "name": "Ada",
        "embeddings": [[0.0] * EMBEDDING_DIM],
        "createdAt": 1000,
        "updatedAt": 1000,
    }
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "faces": [
                    good,
                    {"id": "f_2", "name": "Short", "embeddings": [[0.0] * 10], "createdAt": 1, "updatedAt": 1},
                    {"id": "f_3"},
                    "garbage",
                ],
            }
        ),
        encoding="utf-8",
    )

    assert [record.name for record in list_faces(tmp_path)] == ["Ada"]


def test_alignment_marker_round_trips(tmp_path: Path) -> None:
    """Every written record names the pipeline that produced its embeddings."""
    upsert_face(tmp_path, "Alice", _vector(0))

    raw = json.loads(faces_path_for_instance(tmp_path).read_text(encoding="utf-8"))

    assert ALIGNMENT_VERSION == "arcface5"
    assert raw["faces"][0]["alignment"] == ALIGNMENT_VERSION


def test_mismatched_alignment_marker_is_dropped_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A record from a different warp is not comparable — drop it, and say so.

    D-015 silently invalidated every stored embedding with no signal at all;
    the marker is what turns that into a loud "re-enroll this person".
    """
    _write_record(tmp_path, "Old", {"alignment": "threepoint-legacy"})

    with caplog.at_level(logging.WARNING, logger="reachy_companion.faces"):
        assert list_faces(tmp_path) == []

    assert "alignment" in caplog.text
    assert "Old" in caplog.text


def test_explicit_null_alignment_marker_is_dropped(tmp_path: Path) -> None:
    """A present-but-null marker is a corrupted stamp, not an unmarked record."""
    _write_record(tmp_path, "Broken", {"alignment": None})

    assert list_faces(tmp_path) == []


def test_unmarked_record_is_grandfathered(tmp_path: Path) -> None:
    """Records written before the marker existed (the live Louis/Lena records) keep loading.

    Those two were enrolled *after* D-015, so their embeddings are current;
    dropping them on a missing key would forget the only two people the robot
    actually knows.
    """
    _write_record(tmp_path, "Louis")

    assert [record.name for record in list_faces(tmp_path)] == ["Louis"]


def test_writes_are_atomic_and_leave_no_temp_files(tmp_path: Path) -> None:
    """Atomic tmp+replace, exactly like memory.py — a killed process must not truncate the store."""
    upsert_face(tmp_path, "Ada", _vector(0))

    assert (tmp_path / FACES_FILENAME).is_file()
    assert [path.name for path in tmp_path.iterdir() if path.name != FACES_FILENAME] == []


def test_stored_floats_are_normalized_and_stay_inspectable(tmp_path: Path) -> None:
    """Embeddings persist L2-normalized and rounded to 6 dp — readable by a human on the robot."""
    vector = np.full(EMBEDDING_DIM, 1.0 / 3.0, dtype=np.float32)
    upsert_face(tmp_path, "Ada", vector)

    payload = json.loads(faces_path_for_instance(tmp_path).read_text(encoding="utf-8"))

    assert payload["version"] == 1
    stored = payload["faces"][0]["embeddings"][0]
    assert len(stored) == EMBEDDING_DIM
    assert all(round(value, 6) == value for value in stored)
    # A constant vector normalizes to 1/sqrt(128) per component; cosine error from
    # the 6-dp rounding stays below 1e-6.
    assert stored[0] == pytest.approx(1.0 / np.sqrt(EMBEDDING_DIM), abs=1e-6)
    assert float(np.linalg.norm(stored)) == pytest.approx(1.0, abs=1e-5)


def test_no_image_bytes_are_ever_persisted(tmp_path: Path) -> None:
    """The privacy claim, asserted: names, vectors, timestamps and a pipeline tag — nothing else."""
    upsert_face(tmp_path, "Ada", _vector(0))

    payload = json.loads(faces_path_for_instance(tmp_path).read_text(encoding="utf-8"))

    assert set(payload) == {"version", "faces"}
    assert set(payload["faces"][0]) == {"id", "name", "embeddings", "createdAt", "updatedAt", "alignment"}
