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
# aliases and the one name index (addendum A1-4)
# --------------------------------------------------------------------------


def _write_people(settings: Settings, people: list[dict[str, object]]) -> None:
    """Hand-write a store file, the way an operator or an older version might have."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    (settings.data_dir / store.PEOPLE_FILENAME).write_text(
        json.dumps({"version": 1, "people": people}), encoding="utf-8"
    )


def _row(person_id: str, name: str, **extra: object) -> dict[str, object]:
    row: dict[str, object] = {
        "id": person_id,
        "name": name,
        "createdAt": 1,
        "updatedAt": 1,
        "facts": [],
        "photos": [],
    }
    row.update(extra)
    return row


def test_a_store_written_before_aliases_reads_as_empty_tuples(settings: Settings) -> None:
    """Tolerant read: the two new fields are optional, and junk in them is dropped."""
    _write_people(
        settings,
        [
            _row("bp_old", "Lena"),
            _row("bp_junk", "Mo", aliases="not a list", formerFaceIds={"nope": 1}),
            _row("bp_mixed", "Ada", aliases=["  Ada  Lovelace ", 7, None, "   ", "ada lovelace"],
                 formerFaceIds=["f_1", 7, "", "f_1"]),
        ],
    )

    by_id = {person.id: person for person in store.list_people(settings)}

    assert by_id["bp_old"].aliases == ()
    assert by_id["bp_old"].former_face_ids == ()
    assert by_id["bp_junk"].aliases == ()
    assert by_id["bp_junk"].former_face_ids == ()
    # Aliases normalize exactly like names, and dedupe case-insensitively.
    assert by_id["bp_mixed"].aliases == ("Ada Lovelace",)
    assert by_id["bp_mixed"].former_face_ids == ("f_1",)


def test_an_alias_equal_to_the_persons_own_name_is_dropped_on_read(settings: Settings) -> None:
    """One normalized string resolves one way; a self-alias would be a second way."""
    _write_people(settings, [_row("bp_1", "Lena", aliases=["LENA", "Lenna"])])

    person = store.list_people(settings)[0]
    assert person.aliases == ("Lenna",)


def test_the_name_index_covers_aliases(settings: Settings) -> None:
    """Codex A1-4: create and rename resolve against `name` + `aliases`, not `name` alone."""
    target = store.create_person(settings, "Linna")
    source = store.create_person(settings, "Lena")
    store.merge_people(settings, target.id, source.id)
    mo = store.create_person(settings, "Mo")

    with pytest.raises(store.DuplicateNameError):
        store.create_person(settings, "  lena  ")
    with pytest.raises(store.DuplicateNameError):
        store.rename_person(settings, mo.id, "LENA")

    assert [person.name for person in store.list_people(settings)] == ["Mo", "Linna"]


def test_rename_onto_your_own_alias_swaps_the_two(settings: Settings) -> None:
    """Codex A1-4: the robot misheard the *survivor's* name — renaming back is a swap."""
    target = store.create_person(settings, "Linna")
    source = store.create_person(settings, "Lena")
    store.merge_people(settings, target.id, source.id)

    swapped = store.rename_person(settings, target.id, "lena")

    assert swapped.name == "lena"
    assert swapped.aliases == ("Linna",)
    # And the swap is itself reversible.
    assert store.rename_person(settings, target.id, "Linna").aliases == ("lena",)


def test_renaming_a_person_to_their_own_name_leaves_their_aliases_alone(settings: Settings) -> None:
    """Re-spelling your own name is not a swap, so it must not mint an alias for it."""
    target = store.create_person(settings, "Linna")
    source = store.create_person(settings, "Lena")
    store.merge_people(settings, target.id, source.id)

    renamed = store.rename_person(settings, target.id, "LINNA")

    assert renamed.name == "LINNA"
    assert renamed.aliases == ("Lena",)


# --------------------------------------------------------------------------
# merge (addendum Feature 1)
# --------------------------------------------------------------------------


def test_merge_people_carries_everything_onto_the_survivor(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole contract in one pass: facts, photo bytes, identity, and one write."""
    # Merged facts order by `created_at`, so the assertions below need a clock
    # that cannot put two writes in the same millisecond.
    ticks = count(1_700_000_000_000)
    monkeypatch.setattr(store, "_now_ms", lambda: next(ticks))
    target = store.create_person(settings, "Linna")
    source = store.create_person(settings, "Lena")
    store.set_person_face_id(settings, source.id, "f_lena")
    store.add_fact(settings, target.id, "likes tea")
    store.add_fact(settings, source.id, "LIKES TEA")
    store.add_fact(settings, source.id, "has a cat")
    kept = store.add_photo(settings, target.id, "target.jpg", b"target-bytes")
    moved = store.add_photo(settings, source.id, "source.jpg", b"source-bytes")

    writes: list[int] = []
    real_write = store._write_document
    monkeypatch.setattr(
        store,
        "_write_document",
        lambda path, document: (writes.append(1), real_write(path, document))[1],
    )

    merged = store.merge_people(settings, target.id, source.id)

    assert writes == [1]
    assert merged.id == target.id
    assert merged.name == "Linna"
    assert merged.aliases == ("Lena",)
    # The target had no face id of its own, so it adopts the source's — and an
    # adopted id is the primary, never also a former one.
    assert merged.face_id == "f_lena"
    assert merged.former_face_ids == ()
    # Case-insensitive dedupe, and the source's facts land newest-first.
    assert [fact.text for fact in merged.facts] == ["has a cat", "likes tea"]
    assert {photo.id for photo in merged.photos} == {kept.id, moved.id}

    # The bytes moved; the source person and their directory are gone.
    target_dir = store.photo_dir(settings, target.id)
    assert (target_dir / str(kept.stored_as)).read_bytes() == b"target-bytes"
    assert (target_dir / str(moved.stored_as)).read_bytes() == b"source-bytes"
    assert not store.photo_dir(settings, source.id).exists()
    assert store.get_person(settings, source.id) is None
    assert store.get_person(settings, target.id) == merged


def test_merge_people_interleaves_photos_newest_first(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex A3-1: the projection window takes the first three, so order is correctness.

    The target holds three older enrollment samples and the source two newer
    ones. Concatenating instead of interleaving would leave the newest samples
    hidden behind the older ones and the robot would be pushed a stale face.
    """
    ticks = count(1_700_000_000_000)
    monkeypatch.setattr(store, "_now_ms", lambda: next(ticks))
    target = store.create_person(settings, "Linna")
    source = store.create_person(settings, "Lena")
    older = [store.add_photo(settings, target.id, f"old-{index}.jpg", b"x") for index in range(3)]
    newer = [store.add_photo(settings, source.id, f"new-{index}.jpg", b"y") for index in range(2)]

    merged = store.merge_people(settings, target.id, source.id)

    assert [photo.id for photo in merged.photos] == [
        newer[1].id,
        newer[0].id,
        older[2].id,
        older[1].id,
        older[0].id,
    ]
    assert [photo.added_at for photo in merged.photos] == sorted(
        (photo.added_at for photo in merged.photos), reverse=True
    )


def test_merge_people_interleaves_facts_newest_first(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Facts interleave by `created_at` for the same reason photos do: a window takes the front.

    Projection emits the newest 20, and the UI prints each row's own timestamp.
    Appending the source's facts behind the target's would both hide the newest
    ones past the cap and render a list whose dates run backwards halfway down.
    """
    ticks = count(1_700_000_000_000)
    monkeypatch.setattr(store, "_now_ms", lambda: next(ticks))
    target = store.create_person(settings, "Linna")
    source = store.create_person(settings, "Lena")
    # Interleaved in time: the source's are older, newer, and newest.
    store.add_fact(settings, source.id, "old source fact")
    for index in range(20):
        store.add_fact(settings, target.id, f"target fact {index}")
    store.add_fact(settings, source.id, "new source fact")
    store.add_fact(settings, source.id, "newest source fact")

    merged = store.merge_people(settings, target.id, source.id)

    assert [fact.text for fact in merged.facts] == [
        "newest source fact",
        "new source fact",
        *(f"target fact {index}" for index in reversed(range(20))),
        "old source fact",
    ]
    assert [fact.created_at for fact in merged.facts] == sorted(
        (fact.created_at for fact in merged.facts), reverse=True
    )


def test_merge_people_keeps_the_targets_face_id_and_remembers_the_sources(settings: Settings) -> None:
    """Codex A2-1: a merge chain must not forget an older robot id along the way."""
    first = store.create_person(settings, "Linna")
    store.set_person_face_id(settings, first.id, "f_linna")
    second = store.create_person(settings, "Lena")
    store.set_person_face_id(settings, second.id, "f_lena")

    once = store.merge_people(settings, first.id, second.id)
    assert once.face_id == "f_linna"
    assert once.former_face_ids == ("f_lena",)

    third = store.create_person(settings, "Leena")
    store.set_person_face_id(settings, third.id, "f_leena")
    store.merge_people(settings, third.id, first.id)

    survivor = store.get_person(settings, third.id)
    assert survivor is not None
    assert survivor.face_id == "f_leena"
    assert set(survivor.former_face_ids) == {"f_linna", "f_lena"}
    assert survivor.face_id not in survivor.former_face_ids
    assert set(survivor.aliases) == {"Linna", "Lena"}


def test_merge_people_refuses_to_merge_a_person_into_themselves(settings: Settings) -> None:
    """Codex A2-2: a concrete class, because the API maps it to a 400 and not a 404."""
    person = store.create_person(settings, "Lena")

    with pytest.raises(store.MergeError):
        store.merge_people(settings, person.id, person.id)

    assert isinstance(store.MergeError("x"), ValueError)
    assert store.get_person(settings, person.id) == person


def test_merge_people_raises_for_an_unknown_id(settings: Settings) -> None:
    """An unknown id on either side is a 404, so it stays a `PersonNotFoundError`."""
    person = store.create_person(settings, "Lena")

    with pytest.raises(store.PersonNotFoundError):
        store.merge_people(settings, person.id, "bp_nope")
    with pytest.raises(store.PersonNotFoundError):
        store.merge_people(settings, "bp_nope", person.id)


def test_merge_people_refuses_an_alias_a_third_person_already_answers_to(settings: Settings) -> None:
    """The index invariant survives a merge: one normalized string, at most one person.

    Both collisions are only reachable through a hand-edited store — the writers
    themselves never let one name resolve two ways — which is exactly why the
    check is here rather than assumed.
    """
    _write_people(
        settings,
        [
            _row("bp_target", "Linna"),
            _row("bp_source", "Lena"),
            _row("bp_third", "Lena Wu", aliases=["Lena"]),
        ],
    )

    with pytest.raises(store.DuplicateNameError):
        store.merge_people(settings, "bp_target", "bp_source")

    # …and alias against alias, the same way.
    _write_people(
        settings,
        [
            _row("bp_target", "Linna"),
            _row("bp_source", "Lena", aliases=["Lenna"]),
            _row("bp_third", "Mo", aliases=["Lenna"]),
        ],
    )

    with pytest.raises(store.DuplicateNameError):
        store.merge_people(settings, "bp_target", "bp_source")

    assert {person.id for person in store.list_people(settings)} == {"bp_target", "bp_source", "bp_third"}


def test_merge_people_never_makes_the_survivor_an_alias_of_themselves(settings: Settings) -> None:
    """The source's aliases may already include the target's name; that is a no-op."""
    _write_people(
        settings,
        [
            _row("bp_target", "Linna Hmm"),
            _row("bp_source", "Lena", aliases=["Linna Hmm", "Lenna"]),
        ],
    )

    merged = store.merge_people(settings, "bp_target", "bp_source")

    assert merged.name == "Linna Hmm"
    assert set(merged.aliases) == {"Lena", "Lenna"}
    assert "Linna Hmm" not in merged.aliases


def test_add_former_face_id_dedupes_and_never_shadows_the_primary(settings: Settings) -> None:
    """Codex A1-1's store side: the sync layer records a second robot id for one person."""
    person = store.create_person(settings, "Linna")
    store.set_person_face_id(settings, person.id, "f_linna")

    once = store.add_former_face_id(settings, person.id, "f_lena")
    assert once.former_face_ids == ("f_lena",)

    twice = store.add_former_face_id(settings, person.id, "f_lena")
    assert twice.former_face_ids == ("f_lena",)

    # The primary is never also a former id.
    assert store.add_former_face_id(settings, person.id, "f_linna").former_face_ids == ("f_lena",)
    assert store.add_former_face_id(settings, person.id, "   ").former_face_ids == ("f_lena",)

    with pytest.raises(store.PersonNotFoundError):
        store.add_former_face_id(settings, "bp_nope", "f_x")


def test_aliases_and_former_ids_survive_a_round_trip_through_disk(settings: Settings) -> None:
    """Both new fields are persisted, or a merge would be forgotten on the next read."""
    target = store.create_person(settings, "Linna")
    store.set_person_face_id(settings, target.id, "f_linna")
    source = store.create_person(settings, "Lena")
    store.set_person_face_id(settings, source.id, "f_lena")
    merged = store.merge_people(settings, target.id, source.id)

    payload = json.loads((settings.data_dir / store.PEOPLE_FILENAME).read_text(encoding="utf-8"))
    assert payload["people"][0]["aliases"] == ["Lena"]
    assert payload["people"][0]["formerFaceIds"] == ["f_lena"]
    assert store.get_person(settings, target.id) == merged


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


def test_add_fact_returns_the_existing_fact_instead_of_duplicating_it(settings: Settings) -> None:
    """The robot's own rule: a fact a person already has is not stored twice.

    The sync layer re-offers facts it has already imported, so without this an
    import applied twice would project a person's memory back to them doubled.
    """
    person = store.create_person(settings, "Lena")
    first = store.add_fact(settings, person.id, "likes tea")
    mo = store.create_person(settings, "Mo")
    assert [item.id for item in store.list_people(settings)] == [mo.id, person.id]

    again = store.add_fact(settings, person.id, "  Likes   TEA  ")

    assert again == first
    reloaded = store.get_person(settings, person.id)
    assert reloaded is not None
    assert reloaded.facts == (first,)
    # The record is still touched: they were talked about either way, and
    # projection ranks by that.
    assert [item.id for item in store.list_people(settings)] == [person.id, mo.id]


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
# facts: the bulk rewrite (consolidation)
# --------------------------------------------------------------------------


def test_replace_facts_replaces_the_whole_list_newest_first(settings: Settings) -> None:
    """`texts[0]` is the newest fact, which is the order the whole stack is stored in."""
    person = store.create_person(settings, "小諾")
    store.add_fact(settings, person.id, "舊事實")

    updated = store.replace_facts(settings, person.id, ["最新的事", "第二新"])

    assert [fact.text for fact in updated.facts] == ["最新的事", "第二新"]
    reloaded = store.get_person(settings, person.id)
    assert reloaded is not None
    assert reloaded.facts == updated.facts


def test_replace_facts_with_no_texts_clears_the_list(settings: Settings) -> None:
    """An empty rewrite is a legitimate outcome — this person has nothing worth keeping."""
    person = store.create_person(settings, "Lena")
    store.add_fact(settings, person.id, "likes tea")

    assert store.replace_facts(settings, person.id, []).facts == ()
    assert store.get_person(settings, person.id).facts == ()  # type: ignore[union-attr]


def test_replace_facts_normalizes_dedupes_and_stays_uncapped(settings: Settings) -> None:
    """`add_fact`'s normalization over a whole list; blanks drop, and nothing is capped."""
    person = store.create_person(settings, "小諾")
    texts = ["  a  ", "", "A", *[f"f{index}" for index in range(25)]]

    updated = store.replace_facts(settings, person.id, texts)

    # "  a  " normalizes to "a", "" is dropped rather than raised on, and "A" is
    # the same fact as "a" — the store's own case-insensitive rule.
    assert [fact.text for fact in updated.facts][0] == "a"
    assert len(updated.facts) == 26  # a + f0..f24 — the Mac store is uncapped.


def test_replace_facts_keeps_the_record_of_a_fact_that_survives(settings: Settings) -> None:
    """A fact the person already has keeps its id and `created_at`, as `add_fact` would.

    The rewrite reorders and rewords; it does not re-learn. Minting a fresh record
    for text that did not change would move a date the operator never changed and
    break a fact id a client is holding to delete by.
    """
    person = store.create_person(settings, "Lena")
    kept = store.add_fact(settings, person.id, "likes tea")
    store.add_fact(settings, person.id, "has a dog")

    updated = store.replace_facts(settings, person.id, ["walks at dawn", "  Likes   TEA  "])

    assert [fact.text for fact in updated.facts] == ["walks at dawn", "likes tea"]
    assert updated.facts[1] == kept
    assert updated.facts[0].id.startswith("bf_")
    assert updated.facts[0].id != kept.id


def test_replace_facts_touches_the_record_like_any_other_edit_by_default(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without `preserve_updated_at` this is an ordinary edit: bumped and moved to the front."""
    clock = count(1_000)
    monkeypatch.setattr(store, "_now_ms", lambda: next(clock))

    lena = store.create_person(settings, "Lena")
    mo = store.create_person(settings, "Mo")
    before = store.get_person(settings, lena.id)
    assert before is not None
    assert [item.id for item in store.list_people(settings)] == [mo.id, lena.id]

    updated = store.replace_facts(settings, lena.id, ["likes tea"])

    assert updated.updated_at > before.updated_at
    assert [item.id for item in store.list_people(settings)] == [lena.id, mo.id]


def test_replace_facts_can_leave_the_record_exactly_where_it_was(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`preserve_updated_at=True` keeps the timestamp *and* the position in the list.

    Projection ranks by `updated_at` and its sort is stable, so the stored
    position is the tie-break: a bulk pass that moved people to the front would
    reshuffle the robot's top twelve without anything having been said.
    """
    clock = count(1_000)
    monkeypatch.setattr(store, "_now_ms", lambda: next(clock))

    lena = store.create_person(settings, "Lena")
    store.create_person(settings, "Mo")
    before = store.get_person(settings, lena.id)
    assert before is not None
    order_before = [item.id for item in store.list_people(settings)]

    updated = store.replace_facts(settings, lena.id, ["likes tea"], preserve_updated_at=True)

    assert updated.updated_at == before.updated_at
    assert updated.created_at == before.created_at
    assert [fact.text for fact in updated.facts] == ["likes tea"]
    assert [item.id for item in store.list_people(settings)] == order_before
    reloaded = store.get_person(settings, lena.id)
    assert reloaded == updated


def test_replace_facts_in_place_leaves_everyone_elses_position_alone(settings: Settings) -> None:
    """The person written keeps their index, not merely their distance from the front."""
    _write_people(settings, [_row("bp_1", "Lena"), _row("bp_2", "Mo"), _row("bp_3", "Ada")])

    store.replace_facts(settings, "bp_2", ["likes tea"], preserve_updated_at=True)

    reloaded = store.list_people(settings)
    assert [item.id for item in reloaded] == ["bp_1", "bp_2", "bp_3"]
    assert [item.updated_at for item in reloaded] == [1, 1, 1]


def test_replace_facts_in_place_collapses_a_hand_written_duplicate_id(settings: Settings) -> None:
    """One id held twice is a broken store; writing it leaves one record, as `_mutate` would."""
    _write_people(settings, [_row("bp_1", "Lena"), _row("bp_1", "Lena Two"), _row("bp_2", "Mo")])

    store.replace_facts(settings, "bp_1", ["likes tea"], preserve_updated_at=True)

    assert [item.id for item in store.list_people(settings)] == ["bp_1", "bp_2"]
    # The record `_require` resolved is the one that survives.
    assert store.get_person(settings, "bp_1").name == "Lena"  # type: ignore[union-attr]


def test_replace_facts_raises_for_an_unknown_person(settings: Settings) -> None:
    """An unknown id is the same error every other id-addressed mutator raises."""
    with pytest.raises(store.PersonNotFoundError):
        store.replace_facts(settings, "bp_nope", ["likes tea"])
    with pytest.raises(store.PersonNotFoundError):
        store.replace_facts(settings, "bp_nope", ["likes tea"], preserve_updated_at=True)


def test_replace_facts_projects_to_the_robot_newest_first(settings: Settings, tmp_path: Path) -> None:
    """The ordering oracle: `texts` comes back off the robot in exactly that order.

    Two conventions have to agree for this to hold — the store's newest-first
    stack and projection's oldest-first replay into the robot's prepending
    writer — so it is pinned end to end rather than in either half alone.
    """
    # Imported here, not at module scope: these store contracts do not otherwise
    # depend on the projection layer, and this one test is deliberately the
    # exception that pins both sides together.
    from reachy_companion import people as robot_people
    from backend import projection

    person = store.create_person(settings, "小諾")
    store.replace_facts(settings, person.id, ["最新", "其次", "最舊"])

    projection.project(settings, tmp_path / "out")

    facts = robot_people.facts_for_person(tmp_path / "out", "小諾")
    assert [fact.text for fact in facts] == ["最新", "其次", "最舊"]


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


def test_add_display_photo_keeps_bytes_that_never_reach_the_projection(settings: Settings) -> None:
    """An imported enrollment snapshot is a picture for the operator and nothing else."""
    person = store.create_person(settings, "Lena")

    photo = store.add_display_photo(settings, person.id, store.ROBOT_SNAPSHOT_DISPLAY_NAME, b"jpeg-bytes")

    assert photo.display_only is True
    assert photo.synthetic is False
    assert photo.embedding is None
    assert photo.error is None
    assert photo.display_name == "robot-snapshot.jpg"
    assert photo.stored_as == f"{photo.id}.jpg"

    written = store.photo_dir(settings, person.id) / str(photo.stored_as)
    assert written.read_bytes() == b"jpeg-bytes"

    # Persisted, or the next read would offer the snapshot as an un-embedded upload.
    payload = json.loads((settings.data_dir / store.PEOPLE_FILENAME).read_text(encoding="utf-8"))
    assert payload["people"][0]["photos"][0]["displayOnly"] is True
    reloaded = store.get_person(settings, person.id)
    assert reloaded is not None
    assert reloaded.photos == (photo,)


def test_a_photo_without_the_display_only_flag_reads_as_a_normal_photo(settings: Settings) -> None:
    """Tolerant read: a store written before the flag existed is not display-only."""
    person = store.create_person(settings, "Lena")
    photo = store.add_photo(settings, person.id, "face.jpg", b"jpeg-bytes")

    path = settings.data_dir / store.PEOPLE_FILENAME
    payload = json.loads(path.read_text(encoding="utf-8"))
    del payload["people"][0]["photos"][0]["displayOnly"]
    path.write_text(json.dumps(payload), encoding="utf-8")

    reloaded = store.get_person(settings, person.id)
    assert reloaded is not None
    assert reloaded.photos[0].id == photo.id
    assert reloaded.photos[0].display_only is False


def test_add_display_photo_is_deduped_against_any_photo_bytes_this_person_has(settings: Settings) -> None:
    """Re-importing the same snapshot must add nothing — including over a real upload.

    The sync layer re-fetches the snapshot on every import that touches the face,
    so without content dedupe one enrollment would grow a photo per import.
    """
    person = store.create_person(settings, "Lena")
    uploaded = store.add_photo(settings, person.id, "face.jpg", b"jpeg-bytes")

    same = store.add_display_photo(settings, person.id, store.ROBOT_SNAPSHOT_DISPLAY_NAME, b"jpeg-bytes")

    assert same == uploaded
    reloaded = store.get_person(settings, person.id)
    assert reloaded is not None
    assert reloaded.photos == (uploaded,)
    assert len(list(store.photo_dir(settings, person.id).iterdir())) == 1

    # Different bytes are a different photo.
    other = store.add_display_photo(settings, person.id, store.ROBOT_SNAPSHOT_DISPLAY_NAME, b"other-bytes")
    assert other.id != uploaded.id
    assert other.display_only is True

    # And re-offering *that* one is a no-op too.
    assert store.add_display_photo(settings, person.id, store.ROBOT_SNAPSHOT_DISPLAY_NAME, b"other-bytes") == other

    with pytest.raises(store.PersonNotFoundError):
        store.add_display_photo(settings, "bp_nope", store.ROBOT_SNAPSHOT_DISPLAY_NAME, b"bytes")


def test_set_photo_embedding_rejects_a_vector_of_the_wrong_length(settings: Settings) -> None:
    """A short or long vector is refused here, where the caller can still see it.

    The robot's reader (`faces._embedding_from_json`) drops any embedding that is
    not exactly `EMBEDDING_DIM` long, silently — no log, no error. Accepting one
    here would mean a photo that looks embedded on the Mac and simply is not
    there after a push, which is exactly the silent drift this store forbids.
    """
    person = store.create_person(settings, "Lena")
    photo = store.add_photo(settings, person.id, "face.jpg", b"bytes")

    with pytest.raises(ValueError):
        store.set_photo_embedding(settings, person.id, photo.id, (0.1,) * 127, None)
    with pytest.raises(ValueError):
        store.set_photo_embedding(settings, person.id, photo.id, (0.1,) * 129, None)
    with pytest.raises(ValueError):
        store.set_photo_embedding(settings, person.id, photo.id, (), None)

    reloaded = store.get_person(settings, person.id)
    assert reloaded is not None
    assert reloaded.photos[0].embedding is None  # nothing half-written


def test_add_synthetic_photo_rejects_a_vector_of_the_wrong_length(settings: Settings) -> None:
    """The import path gets the same gate: a robot embedding is 128 floats or it is nothing."""
    person = store.create_person(settings, "Lena")

    with pytest.raises(ValueError):
        store.add_synthetic_photo(settings, person.id, (0.1,) * 127)
    with pytest.raises(ValueError):
        store.add_synthetic_photo(settings, person.id, ())

    assert store.get_person(settings, person.id).photos == ()  # type: ignore[union-attr]


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
    "replace_facts": lambda s, pid: store.replace_facts(s, pid, ["likes tea"]),
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


def test_a_corrupt_store_is_set_aside_never_clobbered(settings: Settings) -> None:
    """This store is the source of truth, so corrupt bytes are preserved, not overwritten.

    The robot's copy is a rebuildable projection and may be discarded; this one
    may not. A read that cannot parse the file renames it aside first, so the
    write that follows creates a fresh store without destroying the evidence.
    """
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    path = settings.data_dir / store.PEOPLE_FILENAME
    original = b'{"version": 1, "people": [{"id": "bp_1", "name": "Lena", truncated'
    path.write_bytes(original)

    assert store.list_people(settings) == []

    asides = list(settings.data_dir.glob(f"{store.PEOPLE_FILENAME}.corrupt.*"))
    assert len(asides) == 1
    assert asides[0].read_bytes() == original
    assert asides[0].name.rsplit(".", 1)[-1].isdigit()
    assert not path.exists()

    person = store.create_person(settings, "Mo")

    assert store.list_people(settings) == [person]
    assert json.loads(path.read_text(encoding="utf-8"))["people"][0]["name"] == "Mo"
    # The evidence survives the write that followed it.
    assert asides[0].read_bytes() == original


def test_setting_a_corrupt_store_aside_happens_once(settings: Settings) -> None:
    """A second reader finds the file already gone and tolerates it."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    (settings.data_dir / store.PEOPLE_FILENAME).write_bytes(b"{not json")

    assert store.list_people(settings) == []
    assert store.list_people(settings) == []
    assert store.get_sync_meta(settings) == store.SyncMeta(None, None, None)

    assert len(list(settings.data_dir.glob(f"{store.PEOPLE_FILENAME}.corrupt.*"))) == 1


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
