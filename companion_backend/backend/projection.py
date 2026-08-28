"""The Mac store, projected onto the robot's two files.

`faces.v1.json` and `people.v1.json` on the robot are a *projection* of
`backend.store` — never a second source of truth. This module is the only thing
on this side that writes them, and it writes them **through the robot's own
writers** (`faces._write_faces_file`, `people.upsert_person` /
`people.add_person_fact`) rather than by serializing a shape of our own. That is
the whole design: schema version, the `arcface5` alignment marker, the 12-person
and 3-embedding and 20-fact caps, the fact ordering and the eviction policy all
come from the robot's code, so the projection cannot drift from what the robot
will read back. The two underscore-prefixed reuses are same-repo and deliberate,
flagged at their call sites.

Three rules earn their own explanation:

* **Ranking is computed here.** `store.list_people` returns people
  most-recently-updated first, but that is an invariant of the store's *own
  writers* — a hand-edited `people.json` breaks it. Projection sorts by
  `updated_at` itself, so a stale row can never displace a fresh one out of the
  twelve the robot can hold.
* **The projected set is chosen once.** Faces are the top ≤12 people carrying at
  least one embedding; `people.v1.json` projects exactly those, plus facts-only
  people to fill any room left under the same cap. The replay then runs
  lowest-rank first, so the robot store's own LRU keeps every one of them and
  the *last* people to be evicted by a later voice enrollment are the facts-only
  ones. This is what makes the invariant hold: every projected face record has a
  person record beside it.
* **Face ids are minted here, once, and persisted.** A record id is the person's
  identity on the robot; if projection invented a fresh one per push, every push
  would re-identify everybody and the robot's re-enrollments could never be
  matched back. Ids are minted only for people that actually get a face record —
  a facts-only person stays unlinked, so that a later voice enrollment reads as
  "attach this face to a known name" rather than as a face-id conflict.

Minting writes through `store.set_person_face_id`, which bumps `updated_at`;
that only ever moves a projected person *up* the ranking, and the minting loop
runs lowest-rank first so the relative order of the projected set survives.
"""

from __future__ import annotations
import logging
from typing import Final
from pathlib import Path
from dataclasses import dataclass

from reachy_companion import faces, people
from backend import store
from backend.store import BackendPerson
from backend.config import Settings


logger = logging.getLogger(__name__)

# Every cap is the robot's own, re-exported rather than restated: the projection
# is not allowed to have an opinion the robot does not share.
MAX_PROJECTED_PEOPLE: Final[int] = faces.MAX_PEOPLE
MAX_PROJECTED_EMBEDDINGS: Final[int] = faces.MAX_EMBEDDINGS_PER_PERSON
MAX_PROJECTED_FACTS: Final[int] = people.MAX_FACTS_PER_PERSON


@dataclass(frozen=True)
class ProjectionResult:
    """What one projection wrote, and who did not fit.

    `skipped` names the people that reached the robot as *nothing at all* — over
    the twelve-person cap, or carrying neither an embedding nor a fact. A person
    with facts but no usable photo is not skipped: they have no face record, but
    their facts travel, which is exactly the intended behaviour.
    """

    faces_count: int
    people_count: int
    skipped: list[str]


def embeddings_for(person: BackendPerson) -> tuple[tuple[float, ...], ...]:
    """Return the newest ≤3 embeddings for one person, oldest-first.

    `person.photos` is newest-first, and the robot's own records run oldest-first
    within a person (`faces.upsert_face` appends and keeps the tail), so the
    slice is reversed to match. Order is cosmetic for matching — every sample is
    scored independently — but keeping the robot's convention is what lets an
    imported enrollment project back byte-identically.

    Synthetic photos count: an embedding imported from a robot-side voice
    enrollment is a real sample, and dropping it here would delete that
    enrollment on the next push.

    Display-only photos never do (Codex A1-3). The enrollment snapshot the sync
    layer fetches off the robot is a picture for the operator, and the window is
    three slots wide: letting one in would evict one of the robot's own samples
    and push back a face it never enrolled. The flag is read rather than relying
    on the snapshot having no embedding — that is true today, and this is the one
    line that has to stay true if it ever stops being.
    """
    newest = [
        photo.embedding
        for photo in person.photos
        if photo.embedding is not None and not photo.display_only
    ]
    return tuple(reversed(newest[:MAX_PROJECTED_EMBEDDINGS]))


def ranked_people(settings: Settings) -> list[BackendPerson]:
    """Return every backend person, most recently updated first.

    Sorted here rather than taken on trust: see the module docstring. The sort is
    stable, so people whose `updated_at` ties keep the store's own order, which
    is the same tie-break the robot's readers apply.
    """
    return sorted(store.list_people(settings), key=lambda person: person.updated_at, reverse=True)


def _select(ranked: list[BackendPerson]) -> tuple[list[BackendPerson], list[BackendPerson], list[str]]:
    """Split the ranked people into face-carrying, facts-only, and left behind."""
    with_faces = [person for person in ranked if embeddings_for(person)][:MAX_PROJECTED_PEOPLE]
    chosen = {person.id for person in with_faces}

    room = MAX_PROJECTED_PEOPLE - len(with_faces)
    facts_only = [person for person in ranked if person.id not in chosen and person.facts][:room]
    chosen.update(person.id for person in facts_only)

    skipped = [person.name for person in ranked if person.id not in chosen]
    return with_faces, facts_only, skipped


def _mint_face_ids(settings: Settings, with_faces: list[BackendPerson]) -> dict[str, BackendPerson]:
    """Give every face-carrying person a stable robot record id, persisting new ones.

    Runs lowest-rank first because `set_person_face_id` bumps `updated_at`: doing
    it in this order leaves the projected people in the same relative order they
    were selected in.

    Returns the people it rewrote, so the projection emits their *post*-mint
    timestamps. Otherwise the projection right after a mint would carry the stale
    ones and the very next projection would differ in bytes for no reason a
    reader could see.
    """
    refreshed: dict[str, BackendPerson] = {}
    for person in reversed(with_faces):
        if person.face_id is not None:
            continue
        # The robot's own id shape, so an id minted here is indistinguishable
        # from one the robot would have minted for a voice enrollment.
        record_id = faces._make_id()
        refreshed[person.id] = store.set_person_face_id(settings, person.id, record_id)
        logger.info("Minted face id %s for %r.", record_id, person.name)
    return refreshed


def project(settings: Settings, out_dir: Path) -> ProjectionResult:
    """Write `faces.v1.json` and `people.v1.json` for the robot into `out_dir`.

    Both files are always written, even when the store is empty — the push scps
    them unconditionally, and an absent file would fail the transfer rather than
    clear the robot.
    """
    ranked = ranked_people(settings)
    selected, facts_only, skipped = _select(ranked)
    refreshed = _mint_face_ids(settings, selected)
    # Re-rank *after* minting, never re-select: minting rewrote `updated_at` for
    # some of these people, and writing them in the pre-mint order would put the
    # records in an order the very next projection no longer agrees with — the
    # same two files, byte-different, for a reason invisible in either of them.
    with_faces = sorted(
        (refreshed.get(person.id, person) for person in selected),
        key=lambda person: person.updated_at,
        reverse=True,
    )

    records = []
    for person in with_faces:
        record_id = person.face_id
        if record_id is None:
            # `_mint_face_ids` covers every face-carrying person, so this cannot
            # happen — and dropping the record quietly would be far worse than
            # saying so, since the robot would stop recognizing a known face.
            raise RuntimeError(f"No face id was minted for {person.name!r}.")
        records.append(
            faces.FaceRecord(
                id=record_id,
                name=person.name,
                embeddings=embeddings_for(person),
                created_at=person.created_at,
                updated_at=person.updated_at,
            )
        )
    # Private by underscore, reused on purpose: this is the robot's own writer,
    # and a second serializer here would be a second schema the day either moves.
    faces._write_faces_file(faces.faces_path_for_instance(out_dir), records)

    # Start from an empty person store: the writers below append, and `out_dir`
    # may already hold an earlier projection.
    people.clear_people(out_dir)
    projected = [*with_faces, *facts_only]
    for person in reversed(projected):
        people.upsert_person(out_dir, person.name, face_id=person.face_id)
        # Oldest first: the robot store prepends and caps, so replaying in this
        # order leaves the newest ≤20 facts in newest-first order.
        for fact in reversed(person.facts[:MAX_PROJECTED_FACTS]):
            people.add_person_fact(out_dir, person.name, fact.text, face_id=person.face_id)

    logger.info(
        "Projected %d face records and %d people (%d skipped).", len(records), len(projected), len(skipped)
    )
    return ProjectionResult(faces_count=len(records), people_count=len(projected), skipped=skipped)
