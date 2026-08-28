# Addendum plan: profile merge + enrollment snapshots

Date: 2026-08-28. Operator-requested after first live use of the backend:
(1) merge two backend people (robot misheard "Linna" as "Lena" — same person),
(2) robot keeps the enrollment snapshot so imported people have a picture —
an explicit operator amendment of D-013's "no image is ever persisted".
Extends `docs/superpowers/plans/2026-08-28-person-memory-and-backend.md`
(same constraints, gates, and store idioms).

## Feature 1 — merge people (backend only; works immediately)

### Store (`companion_backend/backend/store.py`)
- `BackendPerson` gains `aliases: tuple[str, ...] = ()` and
  `former_face_ids: tuple[str, ...] = ()` (tolerant read: missing → empty;
  non-list/non-str entries dropped). Both persisted.
- `merge_people(settings, target_id, source_id) -> BackendPerson`:
  - `LookupError` if either id missing; `ValueError` if `target_id == source_id`.
  - Facts: source facts appended into target oldest→newest through the
    existing dedupe (case-insensitive within the person).
  - Photos: photo FILES move from the source dir to the target dir
    (`stored_as` is photo-id-based, collision-free); records (synthetic
    included, embeddings intact) append to target, order preserved
    (target's first, then source's — recency semantics stay honest because
    the merge bumps target `updated_at` via `_mutate`).
  - `face_id`: target keeps its own; if target has none, it adopts the
    source's. A source `face_id` that is NOT adopted goes into
    `former_face_ids` (so the sync diff still knows the robot record).
  - Aliases: the source's name, and the source's aliases, join the target's
    `aliases` (deduped, case-insensitive, never duplicating the target's
    own name). Alias name uniqueness: an alias may NOT collide with another
    person's name or alias (`DuplicateNameError`) — one name must resolve to
    at most one person.
  - Source person deleted (dir removed after files moved).
  - Single lock hold; one `_write_document`.
- `create_person` / `rename_person` uniqueness checks now scan aliases too.

### Sync awareness (`companion_backend/backend/robot.py`)
- Name resolution (import attach + facts matching + removals) resolves robot
  names through `aliases` as well as `name` — a robot fact or face under
  "Lena" matches the merged person "Linna" carrying alias "Lena".
- "New face" test: a robot `record_id` counts as known when it equals any
  person's `face_id` OR appears in any `former_face_ids`. A former-id record
  maps to its absorbing person for the changed-subset check (one import cycle
  may be required after a merge before push — acceptable, matches the
  existing changed-faces flow).
- Projection: aliases and former ids are Mac-side metadata only — never
  projected; after the first post-merge push the robot holds only the
  surviving person.
- A voice re-enrollment under a merged-away name (new id, alias-matching
  name) imports as an attach to the surviving person, not a duplicate.

### API + UI
- `POST /api/people/{target_id}/merge` body `{"source_id": ...}` →
  the merged person view; 404 unknown ids, 400 same-id, 409 alias collision.
  Person views expose `aliases` (list) — `former_face_ids` stays internal.
- Person detail page: "合併其他檔案到這裡 / Merge another profile into this
  one" — a select of other people + confirm (shows both names and what will
  happen); after merge navigate to the survivor; aliases render as small
  badges under the name. All text via text nodes (no innerHTML rule).

## Feature 2 — enrollment snapshots (robot + sync; effective after next deploy)

Operator amendment to D-013 (2026-08-28): ONE posed snapshot per enrolled
person, captured at the moment of explicit verbal enrollment (the person is
knowingly posing — the still-pose flow), stored on the robot and copied to
the Mac on import. Recognition stays snapshot-free; nothing else captures
images; continuous capture remains rejected.

### Robot side (`reachy_companion`)
- New module `src/reachy_companion/face_snapshot.py`:
  - `SNAPSHOT_DIRNAME = "face_snapshots"`;
    `snapshot_path_for(instance_path, record_id) -> Path`
    (`<instance>/face_snapshots/<record_id>.jpg`; record ids are `f_…`
    generated names — safe as filenames; still assert no separator).
  - `save_snapshot(instance_path, record_id, frame_bgr) -> bool`:
    encode JPEG (quality ~85) via the already-shipped `imageio_ffmpeg`
    binary (`get_ffmpeg_exe()`, `-f rawvideo -pix_fmt bgr24 -s WxH -i -
    -frames:v 1`), atomic tmp+rename, overwrite per re-enroll. Best-effort:
    any failure logs a warning and returns False — a snapshot must NEVER
    fail or delay an enrollment result.
- `tools/remember_face.py`: after a successful enroll, fire the snapshot
  from the FIRST accepted sample frame via `asyncio.to_thread`, inside the
  existing `hold_still` bracket (the frame is already captured; encoding may
  finish after release — hold only the frame reference, do not extend the
  hold). Tool result unchanged.
- Lifecycle notes (docs): snapshots live in the instance dir → wiped on
  reinstall like every store; the deploy-manifest note in
  `session-handoff.md` extends to `face_snapshots/`. No cascade from
  `forget_face` (matches the existing no-cascade posture; orphan files are
  harmless and overwritten on re-enroll).

### Sync + backend
- `robot.py` import: for every robot face it applies (new, changed, attach),
  best-effort scp of `face_snapshots/<record_id>.jpg` (missing file is
  normal — enrolled before this feature, or robot not yet redeployed).
  Fetched bytes become a REGULAR backend photo on that person
  (`display_name "robot-snapshot.jpg"`), embedded locally best-effort like
  an upload (embedding failure keeps the photo as display-only with its
  error). Re-import of an unchanged snapshot must not duplicate photos:
  skip when the person already has a photo whose bytes hash matches
  (sha256 compare, cheap at one file per person).
- UI: nothing new — the photo grid already renders real photos.

## Tests (same gates as the main plan)
Store: merge happy path (facts deduped, photos moved on disk + records,
face_id adoption, aliases, former ids, source gone, single write); alias
collision 409 path; tolerant read of alias-less older files. Robot sync:
alias-resolved fact matching + attach; former-id known-face; post-merge
push cycle (merge → diff shows changed/known, import → push proceeds,
robot ends with only the survivor); snapshot import creates the photo once
(hash dedupe on re-import); missing snapshot tolerated. Robot side:
save_snapshot writes a decodable JPEG (decode with the backend's
`decode_image` in a backend test — cross-package, or assert JPEG magic
robot-side); enrollment result unaffected when encoding fails (monkeypatched
failure); snapshot fired only on successful enroll. API: merge route codes.
UI: manual smoke.

## Review log

(Codex round(s) recorded here.)
