"""End-to-end selftest on the Mac: a photo goes in, a recognition comes out.

This is the one check that exercises the whole backend chain in a single
process — store, embedding, projection, and the robot's own recognizer reading
the projected file back — without a robot anywhere near it:

    photo bytes -> store.add_photo -> embedding.embed_photo (YuNet + SFace)
                -> projection.project -> faces.v1.json / people.v1.json
                -> FaceRecognizer(<projection dir>).match(<probe embedding>)

The last arrow is the point. Everything before it proves serialization; only a
match of a *different* photo of the same person proves the numbers we push are
the numbers the robot needs to recognize somebody. So `--probe-photo` is a
second, genuinely different photo of the same person, and running without one
is reported as a REDUCED SMOKE test, never as end-to-end evidence (plan review
R2-5). Synthetic faces do not count either: a rendered or generated face proves
the pipeline runs, not that it discriminates people.

The probe half deliberately mirrors the robot's live `identify()` path rather
than the upload path — raw `embedding_for_frame` output straight into `match`,
not the rounded vector the store persists — because that is what happens in
front of the camera.

Everything is written to a throwaway directory (`--keep` to inspect it), so a
run cannot touch `companion_backend/data/`. Nothing here connects to the robot.

    ../reachy_companion/.venv/bin/python scripts/selftest.py \
        --enroll-photo ~/Pictures/lena-1.jpg --probe-photo ~/Pictures/lena-2.jpg

Exit codes: 0 recognized (pass), 1 not recognized (fail — a real signal about
the pipeline or the threshold), 3 could not run (a photo yielded no usable
face: no_face, multiple_faces, too_far, decode_failed).
"""

from __future__ import annotations
import sys
import shutil
import logging
import argparse
import tempfile
from typing import Final
from pathlib import Path

import numpy as np


# Run as a plain script out of `companion_backend/`, exactly as `run.sh` runs
# the server: the package root goes on `sys.path` so `backend.*` imports the
# same way it does there.
_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_ROOT))

from reachy_companion import faces, people  # noqa: E402  (needs the path insert above)
from reachy_companion.face_id import FaceRecognizer  # noqa: E402
from backend import store, embedding, projection  # noqa: E402
from backend.config import INSTANCE_DIR, Settings  # noqa: E402


EXIT_PASS: Final[int] = 0
EXIT_FAIL: Final[int] = 1
EXIT_BLOCKED: Final[int] = 3

DEFAULT_NAME: Final[str] = "Selftest Subject"
DEFAULT_FACT: Final[str] = "was enrolled by the backend selftest"
PROJECTION_DIRNAME: Final[str] = "projection"


class Blocked(Exception):
    """The run could not reach a verdict — a photo carried no usable face."""


def _say(message: str = "") -> None:
    """Print one line of the report to stdout, flushed so it interleaves with logs."""
    print(message, flush=True)


def _settings(data_dir: Path) -> Settings:
    """Return settings rooted at a throwaway directory, with no robot configured.

    The host fields are deliberately empty: this test never opens an ssh
    connection, and a populated host would make an accidental push possible from
    a script whose whole point is that it needs no robot.
    """
    return Settings(reachy_host="", reachy_ssh_user="", data_dir=data_dir, instance_dir=INSTANCE_DIR)


def _enroll(settings: Settings, recognizer: FaceRecognizer, name: str, fact: str, photo: Path) -> str:
    """Create the person, store the enrollment photo, embed it. Returns the person id."""
    person = store.create_person(settings, name)
    store.add_fact(settings, person.id, fact)
    _say(f"  person   {person.id}  name={person.name!r}  facts=1")

    record = store.add_photo(settings, person.id, photo.name, photo.read_bytes())
    path = store.photo_path(settings, person.id, record)
    if path is None:  # pragma: no cover - add_photo always writes bytes
        raise Blocked("the stored photo has no bytes on disk")

    vector, error = embedding.embed_photo(recognizer, path)
    store.set_photo_embedding(settings, person.id, record.id, vector, error)
    if vector is None:
        raise Blocked(f"the enrollment photo produced no embedding: {error}")

    _say(f"  photo    {record.id}  {photo.name}  -> {len(vector)}-float embedding")
    return person.id


def _project(settings: Settings, out_dir: Path) -> None:
    """Project the store onto the two robot files and read both back through the robot's readers."""
    out_dir.mkdir(parents=True, exist_ok=True)
    result = projection.project(settings, out_dir)
    _say(f"  wrote    faces={result.faces_count}  people={result.people_count}  skipped={result.skipped}")

    records = faces.list_faces(out_dir)
    persons = people.list_people(out_dir)
    if not records:
        # The projection reported a count the robot's own reader disagrees with,
        # which is the one failure mode a byte-level check exists to catch.
        raise Blocked("the projected faces.v1.json read back empty through faces.list_faces")

    for record in records:
        _say(f"  face     {record.id}  name={record.name!r}  embeddings={len(record.embeddings)}")
    for person in persons:
        _say(f"  person   {person.id}  name={person.name!r}  facts={len(person.facts)}  faceId={person.face_id}")

    marker = faces.faces_path_for_instance(out_dir).read_text(encoding="utf-8")
    _say(f"  alignment marker {faces.ALIGNMENT_VERSION!r} present: {faces.ALIGNMENT_VERSION in marker}")


def _probe(out_dir: Path, photo: Path) -> tuple[str, str | None, float | None, float]:
    """Match `photo` against the projected store, the way the robot's camera path does.

    Returns `(status, name, score, threshold)`. The recognizer is built fresh on
    the projection directory, so the only thing linking enrollment to this match
    is the file that would be scp'd to the robot.
    """
    recognizer = FaceRecognizer(out_dir, enabled=True)

    frame = embedding.decode_image(photo)
    if frame is None:
        raise Blocked(f"the probe photo could not be decoded: {photo}")

    vector, identification = recognizer.embedding_for_frame(frame)
    if vector is None:
        raise Blocked(f"the probe photo produced no embedding: {identification.status} ({identification.reason})")

    result = recognizer.match(np.asarray(vector, dtype=np.float32))
    return str(result.status), result.name, result.score, recognizer.threshold


def run(args: argparse.Namespace) -> int:
    """Run the whole chain in a temp directory and return the process exit code."""
    enroll_photo = Path(args.enroll_photo).expanduser()
    probe_photo = Path(args.probe_photo).expanduser() if args.probe_photo else enroll_photo
    reduced = args.probe_photo is None or probe_photo.resolve() == enroll_photo.resolve()

    for label, path in (("enroll", enroll_photo), ("probe", probe_photo)):
        if not path.is_file():
            _say(f"BLOCKED  the {label} photo does not exist: {path}")
            return EXIT_BLOCKED

    data_dir = Path(tempfile.mkdtemp(prefix="companion-selftest-"))
    out_dir = data_dir / PROJECTION_DIRNAME
    settings = _settings(data_dir)

    _say(f"data dir   {data_dir}")
    _say(f"enroll     {enroll_photo}")
    _say(f"probe      {probe_photo}")
    if reduced:
        _say("mode       REDUCED SMOKE — one photo matched against itself. This proves")
        _say("           serialization only, NOT recognition; it is not end-to-end evidence.")
    else:
        _say("mode       end-to-end (two distinct photos)")
    _say()

    try:
        _say("1. enroll")
        _enroll(settings, embedding.build_recognizer(settings), args.name, args.fact, enroll_photo)
        _say("2. project")
        _project(settings, out_dir)
        _say("3. probe")
        code = _verdict(*_probe(out_dir, probe_photo), name=args.name, reduced=reduced)
    except Blocked as exc:
        _say()
        _say(f"BLOCKED  {exc}")
        _say("         Supply two different real photos of one person, each with exactly")
        _say("         one clearly visible face, and run again.")
        code = EXIT_BLOCKED
    finally:
        # Last, so the verdict is the last thing on screen either way.
        if args.keep:
            _say(f"\nkept {data_dir}")
        else:
            shutil.rmtree(data_dir, ignore_errors=True)
    return code


def _verdict(
    status: str,
    matched: str | None,
    score: float | None,
    threshold: float,
    *,
    name: str,
    reduced: bool,
) -> int:
    """Print the match, judge it against the threshold, and return the exit code."""
    shown = "n/a" if score is None else f"{score:.4f}"
    _say(f"  match    status={status}  name={matched!r}  score={shown}  threshold={threshold:.3f}")
    _say()

    passed = status == "recognized" and matched == faces.normalize_face_name(name)
    if passed and reduced:
        _say(f"REDUCED SMOKE PASS  matched itself at {shown} — serialization only, not E2E evidence.")
        return EXIT_PASS
    if passed:
        _say(f"PASS  recognized {matched!r} from a second photo at {shown} >= {threshold:.3f}.")
        return EXIT_PASS

    _say(f"FAIL  expected recognized {name!r}, got status={status} name={matched!r} score={shown}.")
    _say("      A score just under the threshold is a tuning signal, not a code defect;")
    _say("      an `unknown` at a near-zero score means the two photos are not the same face.")
    return EXIT_FAIL


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, configure logging, run the selftest."""
    parser = argparse.ArgumentParser(
        prog="selftest.py",
        description="Enroll a photo through the backend and recognize a second photo of the same person.",
    )
    parser.add_argument("--enroll-photo", required=True, help="a real photo of one person, one face in frame")
    parser.add_argument("--probe-photo", default=None, help="a DIFFERENT photo of the same person")
    parser.add_argument("--name", default=DEFAULT_NAME, help="the name to enroll under")
    parser.add_argument("--fact", default=DEFAULT_FACT, help="one person fact, to exercise the people projection")
    parser.add_argument("--keep", action="store_true", help="keep the temp data directory for inspection")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s", stream=sys.stderr)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
