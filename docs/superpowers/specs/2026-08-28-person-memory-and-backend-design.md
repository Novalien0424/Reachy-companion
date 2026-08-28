# Person Memory & Management Backend — Design

Date: 2026-08-28. Status: approved by operator (boot wait ~4 s, auto+UI memory
writes, vanilla-JS backend UI, architecture option C). PRD §9 amendment of
2026-08-28 records the non-goal promotion.

## 1. Goal

Make Reachy greet people it knows the way a person would — pause briefly, look,
then greet the recognized person by name with genuinely personal content — and
give the operator a Mac-side management backend to curate people, photos, and
per-person memory, pushed to the robot over the existing deploy channel.

Five operator-requested capabilities:

1. **Per-person memory**: facts scoped to each face-recognized person.
2. **Recognition-aware boot greeting**: wait briefly at boot; recognized →
   personalized greeting loaded from that person's memory (no self-intro);
   human present but unrecognized → stranger intro; nobody → generic intro.
3. **Still-pose enrollment**: the robot holds its head still while capturing
   enrollment photos to avoid motion blur.
4. **Management backend + UI**: control Reachy and manage person profiles.
5. **Photo-upload enrollment**: upload photos in the backend; that person
   becomes recognizable on the robot.

## 2. Constraints (from the PRD amendment)

- Robot gains **no new subsystem**: one sibling JSON store + edits to existing
  greeting/tool code. No new robot-side dependencies. No new RPC methods.
- Recognition remains one bounded wake check + explicit tool calls (D-013,
  D-024 privacy property). No continuous recognition.
- Photos never reach or persist on the robot (`faces.py` invariant holds).
- `memory.v1.json` / `MemoryFact.to_json` is a locked external contract
  (D-013); it is not extended. Person facts live in a **sibling store**.
- Mac backend is a trusted-LAN POC surface (same standing as the §12.7
  console ruling); no auth work in this round.

## 3. Robot-side design

### 3.1 Three-way boot greeting

`_send_startup_greeting_prompt` (huggingface_realtime.py) branches on the full
`Identification` instead of a collapsed prefix string:

- `_recognized_face_prefix` → renamed/refactored to return the
  `Identification` (it already computes and logs status, name, `face_count`);
  the greeting builder maps it to one of three synthetic-user-turn prompts,
  each a **prefix on the existing profile `greeting`** (the
  `_FACE_GREETING_PREFIX` pattern — no profile/persona schema change, the two
  closed metadata field sets stay closed):
  - **recognized**: system-note prefix carrying the name and up to
    `FACE_GREETING_FACTS` (default 6) of that person's facts from
    `people.v1.json`; instructs: greet them warmly by name like an old friend,
    weave in what you remember naturally, do **not** introduce yourself.
  - **stranger present** (`unknown`/`ambiguous`/`multiple_faces`/`too_far`
    with `face_count > 0`, or `unknown` match): prefix says someone
    unfamiliar is here — introduce yourself and you may ask their name.
  - **nobody** (`no_face`, `unavailable`, disabled camera/recognizer): the
    profile greeting untouched (current behavior).
- **Wait budget**: `FACE_WAKE_BUDGET_MS` default 1200 → **4000**;
  `FACE_WAKE_ATTEMPTS` default 3 → **5** (existing clamps 0–10000 / 1–5
  already admit these). The single-shared-deadline mechanism pinned by
  `test_greeting_is_not_delayed_past_the_wake_budget` is unchanged — only the
  default moves. The wake check exits early on the first confident hit, so
  the full 4 s is only spent when nobody is recognized.
- **Extended window** (D-024) stays; its spawn condition becomes "spawn unless
  the boot greeting already went to a recognized person". The late-recognition
  prompt (`_FACE_LATE_RECOGNITION_PROMPT`) also carries that person's facts.
- Tests: `test_greeting_is_untouched_unless_someone_is_recognized` is
  **deliberately rewritten** into the three-way contract (nobody → verbatim
  greeting; stranger → stranger prefix; recognized → facts present).
  `test_startup_greeting_spawns_extended_check_only_on_a_miss` updates to the
  new spawn condition. `get_session_greeting_prompt()` stays zero-arg.

### 3.2 `people.v1.json` — the per-person store

New module `people.py`, mirroring `faces.py` idioms exactly (atomic
tmp+`replace` write, in-process lock, tolerant per-record read, caps enforced
on read and write, instance-path sibling file):

```json
{
  "version": 1,
  "people": [
    {
      "id": "p_<epoch_ms>_<6>",
      "faceId": "f_…",            // FK to faces.v1.json record id; may be null
      "name": "Lena",              // ≤ 40 chars, the join key the tools use
      "facts": [
        {"id": "m_…", "text": "…", "createdAt": 1724}
      ],
      "createdAt": 1724, "updatedAt": 1724
    }
  ]
}
```

Caps: `MAX_PEOPLE = 12` (aligned with faces), `MAX_FACTS_PER_PERSON = 20`,
`MAX_FACT_CHARS = 280` (mirrors memory.py). Name matching is the same
whitespace-collapse + case-insensitive compare `faces.py` uses. Lookup is by
`name` (what recognition returns); `faceId` is carried for the backend's
diff/import logic, tolerated absent. The store is **re-read on every use**
(like `faces.list_faces` in `match()`) so a Mac push applies live.

### 3.3 Person-aware memory tools (auto + UI decision)

- `ToolDependencies` gains `current_person: str | None` (runtime-injected,
  `face_recognizer` precedent), set on: boot-wake recognition, extended-window
  recognition, and every `who_is_this` → `recognized`. Cleared on session
  close. It is a label for memory scoping only — no behavioral gating.
- `remember`: when `current_person` is set, the fact is written to that
  person's `people.v1.json` record (created if missing); otherwise to the
  global `memory.v1.json` exactly as today. Tool result gains
  `"scope": "person:<name>" | "global"` so the model knows where it landed.
- `forget`: searches the current person's facts first, then global.
- `who_is_this` result gains `known_facts: [str]` (up to `FACE_GREETING_FACTS`)
  on `recognized` — person memory reaches the model through the existing
  tool-result path; **no new mid-session injection mechanism**. The closed
  `IdentificationReason`/`IdentificationStatus` Literals are untouched
  (`known_facts` is added at the tool layer, not on `Identification`).
- Session instructions: `format_memory_for_prompt` stays global-only.
  Person facts enter via greeting prefix / tool results only, keeping the
  memory prompt stable and the D-019 token budget unchanged.
- No new tools; the 41-tool array does not grow.

### 3.4 Still-pose enrollment

`remember_face` brackets its capture burst (first sample + 2 extras) with a
motion hold, via the thread-safe `movement_manager` seam:

- Hold: `set_head_tracking(False)` equivalent hold using the existing
  weight-0.0 anchor pattern (`moves.py` `set_speaking` precedent:
  `get_current_head_pose()` → `start_head_tracking(weight=0.0)`) plus
  `disable_wobbling()`; a small settle pause (~0.3 s) before the first frame.
- Release in `finally`: restore tracking weight and wobbling to their prior
  state (read, not assumed — tracking may already be off).
- Implemented as a context-manager helper in `tools/face_support.py` so
  `who_is_this` can reuse it later if wanted (not enabled there now).
- The tool description is extended so the model asks the person to look at
  Reachy and hold still before calling `remember_face`.

## 4. Mac-side backend (`companion_backend/`)

Architecture **option C**: the Mac backend is the source of truth; the robot's
`faces.v1.json` + `people.v1.json` are a rebuildable projection.

### 4.1 Shape

- New top-level directory `companion_backend/` with its own small FastAPI app
  run from the existing dev venv (`reachy_companion/.venv`, Python 3.12); it
  imports `reachy_companion.faces` / `face_id` / `people` for schema-exact
  writes and embedding. No new Python dependencies: photo decode via
  `imageio_ffmpeg` (already a hard dep; verified BGR-decode path), embedding
  via the already-cached YuNet (SDK-pinned revision) + SFace models.
- Storage on the Mac: `companion_backend/data/people.json` (backend-native
  schema: person + photos + facts + faceId + sync metadata) and
  `companion_backend/data/photos/<person_id>/…` (originals). Gitignored.
- UI: vanilla ES modules, no build step, hash router — the `static/js` idiom;
  `static/js/api.js` (RPC-over-WS client) is copied/adapted for the live
  control panel.

### 4.2 Capabilities

1. **People manager**: CRUD person; upload photos (each photo must contain
   exactly one detectable face — the `enroll` contract; clear per-photo
   error reporting: `no_face`, `multiple_faces`, `too_far`); pick up to 3
   photos as embedding samples (newest wins, mirroring the ring-buffer);
   edit per-person facts (add/edit/delete, 280-char cap surfaced in UI).
2. **Sync**:
   - **Push**: project backend people → `faces.v1.json` (through
     `faces._write_faces_file`, so the `arcface5` alignment stamp and caps
     are right by construction) + `people.v1.json`; `scp` both to
     `$INST/` (plain key-auth scp per the deploy lesson — never expect).
     Faces apply live; person facts apply live to greeting/tools (both
     re-read per use). Read-back verification prints record counts (deploy
     skill's "empty file is silent data loss" rule).
   - **Import from robot**: scp both files back, diff by record id/name;
     voice-enrolled faces and person-scoped facts created on the robot are
     imported into the backend store (Mac stays authoritative; import is
     explicit, shown as a diff, and confirmed in the UI).
   - Drift indicator: last-push hash vs robot file hash (ssh `shasum`).
3. **Robot control panel**: browser connects directly to
   `ws://$REACHY_HOST:7860/rpc` (no origin check; same LAN): status, say,
   interrupt, mic, personality/voice apply. Backend additionally proxies the
   daemon apps API (`:8000`): start/stop/restart app, current-app-status.
4. **Deploy**: a backend endpoint that shells out to the existing
   `reachy-deploy` ritual is **out of scope for this round** — deploy remains
   the skill-driven flow; the UI links/documents it. (Keeps the D-009
   authorization boundary with a human in the loop.)

### 4.3 Concurrency & conflict rule

Single-writer-per-side with explicit sync: the robot writes its stores only
via voice enrollment / person-scoped remember; the Mac only via push. Push
performs read-back-diff first: if the robot's file contains records unknown to
the backend, the push is blocked with an "import first" prompt in the UI.
This closes the lost-update window without robot-side locking.

## 5. Error handling

- Robot: `people.py` read is tolerant per record (bad record dropped with a
  warning, file never rejected whole — the `faces.py` pattern). A missing or
  corrupt `people.v1.json` degrades to "no person facts", never blocks
  greeting or tools. All greeting-path additions live inside the existing
  single-deadline/try-except structure: any face/people failure yields the
  generic greeting.
- Backend: embedding/model-load failures are surfaced in the UI per photo;
  ssh/scp failures are surfaced with the exact command result; no partial
  push (push writes both files or reports which one failed; faces first, then
  people).

## 6. Testing & verification

- Unit: `people.py` store round-trip/caps/tolerant-read; three-way greeting
  branch (rewrite the two pinned tests as §3.1); wake-budget mechanism test
  unchanged; still-pose hold/release incl. restore-on-exception; `remember`/
  `forget` scoping; `who_is_this` `known_facts`; backend projection produces
  byte-valid `faces.v1.json` (loaded back through `faces._read`); push
  blocked-on-unknown-records rule. Suite + ruff + mypy strict stay green.
- Backend integration (Mac, no robot): upload → embed → project → verify via
  `FaceRecognizer.match` against a second photo of the same person.
- Live rows (feature_list.json): `PERSON-GREET-KNOWN` (boot with enrolled
  person in frame → named, fact-referencing greeting, no self-intro),
  `PERSON-GREET-STRANGER` (unenrolled person → stranger intro),
  `PERSON-GREET-EMPTY` (empty room → generic greeting after ~4 s),
  `PERSON-MEMORY-AUTO` (tell Reachy a fact while recognized → lands person-
  scoped; recall in a later session), `ENROLL-STILL` (enrollment holds the
  head still, samples ≥ 2), `BACKEND-PUSH-LIVE` (backend-enrolled person
  recognized on the robot without app restart), `BACKEND-IMPORT` (voice-
  enrolled person appears in backend after import).

## 7. Explicitly not done

- No robot-side RPC extension (`people.*`) — option B rejected this round
  (wheel-rebuild per change; widens the unauthenticated surface).
- No auth on backend or console (POC LAN ruling stands).
- No continuous / mid-conversation automatic re-identification.
- No face-photo storage or transmission to the robot.
- No new conversational tools; no profile/persona schema fields.
- No automated deploy from the backend UI.
