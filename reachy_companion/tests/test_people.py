"""Store tests for `people.v1.json` — the third sibling of `memory.v1.json`.

No camera and no model here: this file pins the persistence contract only
(per-person fact lists, dedupe, caps, LRU eviction, corruption tolerance,
atomic writes).
"""

import json
from pathlib import Path

from reachy_companion import faces, memory, people


def test_store_constants_match_the_sibling_stores() -> None:
    """The caps are re-exported from the stores that own the rules, so they cannot drift."""
    assert people.PEOPLE_FILENAME == "people.v1.json"
    assert people.SCHEMA_VERSION == 1
    assert people.MAX_PEOPLE == faces.MAX_PEOPLE == 12
    assert people.MAX_FACT_CHARS == memory.MAX_FACT_CHARS == 280
    assert people.MAX_FACTS_PER_PERSON == 20


def test_round_trip_add_and_list(tmp_path: Path) -> None:
    """A first fact creates the person record and links the face id."""
    fact = people.add_person_fact(tmp_path, "Lena", "Likes oolong tea", face_id="f_1_abc")
    assert fact is not None and fact.text == "Likes oolong tea"
    records = people.list_people(tmp_path)
    assert len(records) == 1
    record = records[0]
    assert record.name == "Lena" and record.face_id == "f_1_abc"
    assert [f.text for f in record.facts] == ["Likes oolong tea"]


def test_facts_for_person_is_newest_first_and_limited(tmp_path: Path) -> None:
    """Facts read back newest first, and `limit` trims from that end."""
    for i in range(4):
        people.add_person_fact(tmp_path, "Lena", f"fact {i}")
    facts = people.facts_for_person(tmp_path, "  lena ", limit=2)
    assert [f.text for f in facts] == ["fact 3", "fact 2"]


def test_fact_dedupe_and_cap(tmp_path: Path) -> None:
    """The same fact twice stays one fact, and the per-person cap holds."""
    people.add_person_fact(tmp_path, "Lena", "Same fact")
    dup = people.add_person_fact(tmp_path, "Lena", "same FACT")
    assert dup is not None
    assert len(people.facts_for_person(tmp_path, "Lena")) == 1
    for i in range(people.MAX_FACTS_PER_PERSON + 5):
        people.add_person_fact(tmp_path, "Lena", f"n{i}")
    assert len(people.facts_for_person(tmp_path, "Lena")) == people.MAX_FACTS_PER_PERSON


def test_forget_person_fact_by_substring(tmp_path: Path) -> None:
    """One fact is removed by case-insensitive substring, scoped to that person."""
    people.add_person_fact(tmp_path, "Lena", "Has a dog named Mochi")
    result = people.forget_person_fact(tmp_path, "Lena", query="mochi")
    assert result.removed is not None and "Mochi" in result.removed.text
    assert people.facts_for_person(tmp_path, "Lena") == []


def test_corrupt_file_reads_as_empty(tmp_path: Path) -> None:
    """A truncated store degrades to "nobody is known", never an exception."""
    people.people_path_for_instance(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    people.people_path_for_instance(tmp_path).write_text("{not json", encoding="utf-8")
    assert people.list_people(tmp_path) == []


def test_bad_record_is_dropped_not_fatal(tmp_path: Path) -> None:
    """One malformed record must not take the good ones down with it."""
    path = people.people_path_for_instance(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '{"version": 1, "people": [ {"bogus": true}, '
        '{"id": "p_1_a", "faceId": null, "name": "Ok", '
        '"facts": [{"id": "m_1_a", "text": "kept", "createdAt": 5}], '
        '"createdAt": 5, "updatedAt": 5} ]}',
        encoding="utf-8",
    )
    records = people.list_people(tmp_path)
    assert [r.name for r in records] == ["Ok"]


def test_person_eviction_past_max_people(tmp_path: Path) -> None:
    """The store is a small cache: the person touched longest ago is dropped."""
    for i in range(people.MAX_PEOPLE + 1):
        people.add_person_fact(tmp_path, f"Person {i}", "x")
    records = people.list_people(tmp_path)
    assert len(records) == people.MAX_PEOPLE
    assert all(r.name != "Person 0" for r in records)  # LRU evicted


def test_corrupt_bytes_read_as_empty(tmp_path: Path) -> None:
    """A store that is not valid UTF-8 degrades the same way bad JSON does."""
    path = people.people_path_for_instance(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'{"version": 1, "people": [\xff\xfe]}')

    assert people.list_people(tmp_path) == []


def test_upsert_person_creates_an_empty_record_and_links_the_face_once(tmp_path: Path) -> None:
    """`upsert_person` is the fact-free entry point; the first face id wins."""
    created = people.upsert_person(tmp_path, "Lena", face_id="f_1")

    assert created is not None
    assert created.facts == ()
    assert created.face_id == "f_1"

    again = people.upsert_person(tmp_path, "  lena  ", face_id="f_2")

    assert again is not None
    assert again.id == created.id  # same person, matched case-/whitespace-insensitively
    assert again.face_id == "f_1"  # an existing link is never overwritten
    assert len(people.list_people(tmp_path)) == 1


def test_add_person_fact_backfills_a_missing_face_id(tmp_path: Path) -> None:
    """A person first known by name gains the face id the day the face is enrolled."""
    people.add_person_fact(tmp_path, "Nora", "Drinks black coffee")
    people.add_person_fact(tmp_path, "Nora", "Runs in the morning", face_id="f_9")

    (record,) = people.list_people(tmp_path)

    assert record.face_id == "f_9"


def test_empty_name_or_text_is_refused(tmp_path: Path) -> None:
    """Blank names and blank facts are refused, and no store file is created."""
    assert people.add_person_fact(tmp_path, "   ", "a fact") is None
    assert people.add_person_fact(tmp_path, "Lena", "   ") is None
    assert people.upsert_person(tmp_path, "  ") is None
    assert people.list_people(tmp_path) == []


def test_long_fact_is_truncated_like_the_memory_store(tmp_path: Path) -> None:
    """Facts are normalized by `memory.normalize_memory_text`, cap included."""
    assert people.MAX_FACT_CHARS == 280

    fact = people.add_person_fact(tmp_path, "Lena", "x" * (people.MAX_FACT_CHARS + 20))

    assert fact is not None
    assert len(fact.text) == people.MAX_FACT_CHARS
    assert fact.text.endswith("...")


def test_forget_person_fact_reports_every_candidate_and_removes_the_first(tmp_path: Path) -> None:
    """The removal is `candidates[0]`; the rest are returned for a follow-up question."""
    people.add_person_fact(tmp_path, "Lena", "Likes jazz piano")
    people.add_person_fact(tmp_path, "Lena", "Likes jazz")
    people.add_person_fact(tmp_path, "Mo", "Likes jazz too")

    result = people.forget_person_fact(tmp_path, "lena", query="JAZZ")

    assert result.removed is not None
    assert result.removed.text == "Likes jazz"  # newest first
    assert [candidate.text for candidate in result.candidates] == ["Likes jazz", "Likes jazz piano"]
    assert [fact.text for fact in people.facts_for_person(tmp_path, "Lena")] == ["Likes jazz piano"]
    assert [fact.text for fact in people.facts_for_person(tmp_path, "Mo")] == ["Likes jazz too"]


def test_forget_person_fact_on_an_unknown_person_is_an_empty_result(tmp_path: Path) -> None:
    """An unknown person, or an empty query, yields no removal and no candidates."""
    people.add_person_fact(tmp_path, "Lena", "Likes jazz")

    unknown = people.forget_person_fact(tmp_path, "Nobody", query="jazz")
    blank = people.forget_person_fact(tmp_path, "Lena", query="   ")
    missed = people.forget_person_fact(tmp_path, "Lena", query="tango")

    assert (unknown.removed, unknown.candidates) == (None, ())
    assert (blank.removed, blank.candidates) == (None, ())
    assert (missed.removed, missed.candidates) == (None, ())
    assert len(people.facts_for_person(tmp_path, "Lena")) == 1


def test_forget_person_removes_one_record(tmp_path: Path) -> None:
    """`forget_person` returns what it removed, or None when the name is unknown."""
    people.add_person_fact(tmp_path, "Lena", "Likes jazz")
    people.add_person_fact(tmp_path, "Mo", "Likes tea")

    removed = people.forget_person(tmp_path, " LENA ")

    assert removed is not None
    assert removed.name == "Lena"
    assert [record.name for record in people.list_people(tmp_path)] == ["Mo"]
    assert people.forget_person(tmp_path, "nobody") is None


def test_clear_people_empties_the_store(tmp_path: Path) -> None:
    """The privacy escape hatch: one call and nothing is remembered about anyone."""
    people.add_person_fact(tmp_path, "Lena", "Likes jazz")

    people.clear_people(tmp_path)

    assert people.list_people(tmp_path) == []
    assert people.people_path_for_instance(tmp_path).is_file()


def test_list_people_is_newest_updated_first(tmp_path: Path) -> None:
    """Adding a fact refreshes the person's `updated_at`, which is the eviction order."""
    people.add_person_fact(tmp_path, "first", "a")
    people.add_person_fact(tmp_path, "second", "b")
    people.add_person_fact(tmp_path, "first", "c")

    assert [record.name for record in people.list_people(tmp_path)] == ["first", "second"]


def test_persisted_shape_and_atomic_write(tmp_path: Path) -> None:
    """The envelope and record shapes are the contract later readers rely on."""
    people.add_person_fact(tmp_path, "Lena", "Likes jazz", face_id="f_1")

    path = people.people_path_for_instance(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert set(payload) == {"version", "people"}
    assert payload["version"] == people.SCHEMA_VERSION == 1
    record = payload["people"][0]
    assert set(record) == {"id", "faceId", "name", "facts", "createdAt", "updatedAt"}
    assert record["faceId"] == "f_1"
    assert set(record["facts"][0]) == {"id", "text", "createdAt"}
    assert record["id"].startswith("p_")
    assert record["facts"][0]["id"].startswith("m_")
    # Atomic tmp+replace, exactly like memory.py — no stray temp file survives.
    assert [item.name for item in tmp_path.iterdir()] == [people.PEOPLE_FILENAME]


def test_non_string_face_id_reads_as_none(tmp_path: Path) -> None:
    """A hand-edited `faceId` of the wrong type is tolerated as "not linked"."""
    path = people.people_path_for_instance(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "people": [
                    {"id": "p_1", "faceId": 17, "name": "Lena", "facts": [], "createdAt": 1, "updatedAt": 1},
                    {"id": "p_2", "name": "Mo", "facts": [], "createdAt": 1, "updatedAt": 1},
                ],
            }
        ),
        encoding="utf-8",
    )

    records = people.list_people(tmp_path)

    assert [record.name for record in records] == ["Lena", "Mo"]
    assert all(record.face_id is None for record in records)


def test_malformed_facts_are_dropped_individually(tmp_path: Path) -> None:
    """A bad fact inside a good record drops that fact, not the person."""
    path = people.people_path_for_instance(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "people": [
                    {
                        "id": "p_1",
                        "faceId": None,
                        "name": "Lena",
                        "facts": [
                            {"id": "m_1", "text": "kept", "createdAt": 5},
                            {"id": "m_2"},
                            "garbage",
                            {"id": "m_3", "text": "   ", "createdAt": 5},
                        ],
                        "createdAt": 5,
                        "updatedAt": 5,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    assert [fact.text for fact in people.facts_for_person(tmp_path, "Lena")] == ["kept"]


def test_to_json_round_trips_through_the_store(tmp_path: Path) -> None:
    """`PersonRecord.to_json` is what the writer persists, facts included."""
    people.add_person_fact(tmp_path, "Lena", "Likes jazz", face_id="f_1")

    (record,) = people.list_people(tmp_path)
    payload = record.to_json()

    assert payload == json.loads(people.people_path_for_instance(tmp_path).read_text(encoding="utf-8"))["people"][0]
    assert record.facts[0].to_json() == payload["facts"][0]
