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
  - Concrete exceptions only (Codex A2-2, the API maps concrete classes):
    the store's existing person-not-found `LookupError` subclass for missing
    ids (→404), a new `MergeError(ValueError)` for `target_id == source_id`
    (→400), `DuplicateNameError` for alias collisions (→409).
  - Facts: source facts appended into target oldest→newest through the
    existing dedupe (case-insensitive within the person).
  - Photos: photo FILES move from the source dir to the target dir
    (`stored_as` is photo-id-based, collision-free); records (synthetic
    included, embeddings intact) append to target, order preserved
    (target's first, then source's — recency semantics stay honest because
    the merge bumps target `updated_at` via `_mutate`).
  - `face_id`: target keeps its own; if target has none, it adopts the
    source's. `former_face_ids` of the survivor = `target.former_face_ids ∪
    source.former_face_ids ∪ {unadopted source.face_id}`, deduped, excluding
    the survivor's primary `face_id` (Codex A2-1 — merge chains must not
    forget older robot ids).
  - Aliases: the source's name, and the source's aliases, join the target's
    `aliases` (deduped, case-insensitive, never duplicating the target's
    own name). Aliases pass through `faces.normalize_face_name` exactly like
    names (Codex A1-4).
  - Source person deleted (dir removed after files moved).
  - Single lock hold; one `_write_document`.
- **One normalized-name index over `name` + `aliases`** (Codex A1-4): every
  uniqueness check (`create_person`, `rename_person`, merge, import attach)
  resolves against that index with a same-person exception. Rename onto your
  OWN alias is allowed and swaps: the alias is removed, the old canonical
  name becomes an alias. Rename/create onto another person's name or alias →
  `DuplicateNameError`. One normalized string resolves to at most one person.

### Sync awareness (`companion_backend/backend/robot.py`)
- Name resolution (import attach + facts matching + removals) resolves robot
  names through `aliases` as well as `name` — a robot fact or face under
  "Lena" matches the merged person "Linna" carrying alias "Lena".
- "New face" test: a robot `record_id` counts as known when it equals any
  person's `face_id` OR appears in any `former_face_ids`.
- **The changed-subset test is redefined store-wide** (Codex A1-2, and it
  retires the main plan's accepted 3-slot re-block quirk): a known robot
  record is "changed" iff it holds an embedding that is not present in ANY
  of the mapped person's stored photo embeddings (synthetic included) — not
  merely absent from the projected newest-3 window. Content the backend
  holds anywhere is known content; the push may collapse multiple robot
  records (survivor + former ids) into the single projected record once
  nothing unknown remains. Regression test: two robot ids mapping to one
  survivor with >3 total samples import once and then push cleanly.
- **Alias re-enrollment persists the new robot id** (Codex A1-1): when
  `apply_import` attaches an alias-matched robot face to a person that
  already has a different primary `face_id`, the primary is kept and the new
  `record_id` is appended to `former_face_ids` — otherwise the next diff
  re-reports it as a new face and the push gate can block forever.
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
- `tools/remember_face.py`: after a successful enroll, schedule the snapshot
  **fire-and-forget** (Codex A1-5): copy the FIRST accepted sample frame
  first (`np.ascontiguousarray(frame, dtype=np.uint8)` — the appsink buffer
  must not be aliased), then `asyncio.create_task` into a module-level task
  set with a done-callback that discards the handle and logs any exception;
  the tool result NEVER awaits it. The scheduled task wraps the synchronous
  writer in `asyncio.to_thread` (Codex A2-3 — a blocking ffmpeg call inside
  a bare task would still stall the event loop); the ffmpeg subprocess runs
  with a bounded timeout (10 s, killed on expiry). The encode happens after
  the hold releases; only the copied frame crosses. Tool `description` updated: it
  now stores the name, the numeric signature, AND one enrollment snapshot
  photo (the "never a picture" sentence is amended — D-013 amendment).
- Lifecycle notes (docs): snapshots live in the instance dir → wiped on
  reinstall like every store; the deploy-manifest note in
  `session-handoff.md` extends to `face_snapshots/`. No cascade from
  `forget_face` (matches the existing no-cascade posture; orphan files are
  harmless and overwritten on re-enroll).

### Sync + backend
- `robot.py` import: for every robot face it applies (new, changed, attach),
  best-effort scp of `face_snapshots/<record_id>.jpg` (missing file is
  normal — enrolled before this feature, or robot not yet redeployed).
  Fetched bytes become a **display-only** backend photo on that person:
  `BackendPhoto` gains a persisted `display_only: bool = False` flag (Codex
  A2-4 — an explicit flag, so a pending upload is never mislabeled);
  `display_name "robot-snapshot.jpg"`, `embedding=None`, `error=None`,
  `display_only=True`. Projection skips display-only photos structurally
  (they also carry no embedding), so the snapshot can never enter the
  projected sample window (Codex A1-3); the person's recognition samples
  remain the robot's exact synthetic embeddings. The API exposes the flag;
  the UI labels from it. The robot `record_id` is validated with the same
  no-separator/`Path(...).name` rule before it is ever interpolated into
  the scp path (Codex A2-5); an invalid id skips only the snapshot, never
  the face import. Re-import of an unchanged snapshot must not duplicate
  photos: skip when the person already has a photo whose bytes sha256-match.
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

Additional tests (Codex A1): alias re-enrollment after a post-merge push;
two robot ids → one survivor with >3 total samples (import once, push
clean); removed facts under a former_face_id record; rename onto own alias
(swap) and onto another's alias (409); alias-vs-alias merge collision;
3-sample robot face + snapshot import then immediate push (clean); snapshot
scp failure never fails the face import; sha256 dedupe against an existing
real photo's bytes.

## Review log

**Round 2 (2026-08-28, 5 findings, all accepted):** A2-1 merge chains carry
`former_face_ids` forward; A2-2 concrete exception classes so the API's
400/404/409 mapping holds; A2-3 snapshot task wraps `asyncio.to_thread`;
A2-4 explicit persisted `display_only` photo flag; A2-5 record-id validated
before scp interpolation, invalid id skips only the snapshot.

**Round 1 (2026-08-28, 5 findings, all accepted):** A1-1 attach-under-alias
must persist the new robot id into `former_face_ids` (else permanent push
block); A1-2 changed-subset test redefined to "unknown to ANY stored photo
embedding" with multi-record collapse (also retires the 3-slot re-block
quirk); A1-3 imported snapshots are display-only/non-projecting; A1-4 one
normalized name+alias index, rename-onto-own-alias swaps; A1-5 snapshot is
fire-and-forget with owned task set, bounded ffmpeg timeout, copied frame,
amended tool description.
