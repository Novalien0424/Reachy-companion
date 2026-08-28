# Session handoff — 2026-08-28

**No interrupted work.** The person-memory + management-backend wave is
complete through its final whole-branch review and gate-green on branch
`person-memory-backend`; nothing is half-applied. The branch is **pending
merge** and has **not** been deployed.

## Where things stand

See `progress.md` → **Current state**. The robot still carries the
face-recognition build (fourteenth install, 2026-08-27, commit `ae62756`), app
left stopped with `startup_app=reachy_companion`. Branch
`person-memory-backend` (off `main` @ `59fd811`, fourteen tasks) adds the
three-way boot greeting (recognized by name with facts / stranger intro /
verbatim empty-room greeting) on a widened wake budget (`FACE_WAKE_BUDGET_MS`
4000, `FACE_WAKE_ATTEMPTS` 5), the `people.v1.json` sibling store with
person-scoped `remember`/`forget` and `known_facts` on `who_is_this`,
still-pose enrollment (`hold_still`), and the Mac-side `companion_backend/` —
FastAPI plus a vanilla-ES-module UI on `127.0.0.1:8710`, run out of the same
`reachy_companion/.venv` with no new dependency, projecting its own store onto
the robot's two files through a guarded remote promote. Spec:
`docs/superpowers/specs/2026-08-28-person-memory-and-backend-design.md`; plan:
`docs/superpowers/plans/2026-08-28-person-memory-and-backend.md`; record:
`DECISIONS.md` **D-025**.

Gate on the branch: robot suite **1414 passed / 30 skipped**, ruff clean, mypy
strict clean; backend **159 passed**, ruff clean, `mypy --strict` clean.

## Deploy note — do this first, at the next deploy session

**`people.v1.json` must be added to the `.claude/skills/reachy-deploy`
backup/restore manifest at the next deploy session.** It lives beside
`faces.v1.json` inside site-packages and is therefore wiped by every reinstall,
so without the manifest entry a redeploy silently destroys every person fact
and the cross-session half of `PERSON-MEMORY-AUTO` can never hold. The skill
file is deliberately **not** edited on this branch — do it in the deploy
session, where the change is exercised the moment it is made.

## Pending live verification (seven rows, operator, on the robot)

Full text in `feature_list.json`; all seven are `implemented-unverified`.

1. **PERSON-GREET-KNOWN** — enrolled person with ≥1 fact seated in frame at
   boot: `Wake face check: recognized <name> … on round N of 5`, then
   `Startup greeting personalized for <name> with K remembered fact(s).`
   (K ≥ 1), no `Extended wake face check` line, and a spoken greeting that
   names them, uses a fact, and does not self-introduce.
2. **PERSON-GREET-STRANGER** — never-enrolled person in frame at boot: the
   wake line reports `nobody recognized` with `face_count > 0`, the extended
   window still spawns, and the greeting is a self-introduction (not the
   empty-room line, not a name).
3. **PERSON-GREET-EMPTY** — empty-room boot: `last status=no_face` at or under
   ~4000 ms, the profile greeting verbatim, the extended window closing at its
   bound; judge by ear whether the added ~4 s pause is acceptable.
4. **PERSON-MEMORY-AUTO** — recognized session → state a fact → the tool result
   carries `scope: person:<name>`; negative control in an unrecognized session
   logs the global `remember`; restart and boot recognized again to hear the
   fact woven in; label must survive a `too_far`/`no_face` glance mid-session.
5. **ENROLL-STILL** — 「記住我，我叫X」: the head visibly stops for the burst,
   `remember_face saved name=X samples=N` with N ≥ 2 and no `hold_still: could
   not …` warning, then tracking and speech wobble come back. The 0.35 s settle
   is a guess that live use is expected to correct.
6. **BACKEND-PUSH-LIVE** — Mac selftest first (below), then create the person
   in the UI, upload photos, add a fact, `POST /api/sync/push`, and on a robot
   **already running and never restarted** have them ask 「你認得我嗎」 →
   `who_is_this status=recognized` with score ≥ 0.363 and the typed fact in the
   answer.
7. **BACKEND-IMPORT** — voice-enroll a new person on the robot and have them
   remember a fact; on the Mac the push returns 409, the import previews and
   applies (`Imported N items…`), the person appears with a synthetic photo
   tile, the push then succeeds and the next one is byte-identical. Run the
   removal half on a person **under** the 20-fact cap.

## Two things owed before those rows can close

- **Operator photos.** `companion_backend/scripts/selftest.py` needs **two
  different real photos of one person** (`--enroll-photo` / `--probe-photo`).
  No real-face photo exists on this machine: the 2026-08-28 run against the
  gray fixture blocks honestly at `no_face` (exit 3) after building the
  YuNet+SFace sessions in 1372 ms. The model load, the store and the projection
  → `FaceRecognizer.match` half are proven (the latter with a synthetic 128-d
  vector) — that is plumbing evidence, not recognition evidence.
- **Comparability check at the first live push.** One manual check that a
  Mac-embedded vector and a robot **voice** enrollment of the same person score
  like a same-person pair. Both paths share the 0.363 threshold and the
  `arcface5` alignment, but they have never been compared on one person —
  record the cosine.

## Next natural actions

1. Merge `person-memory-backend` into `main`.
2. Deploy — starting with the manifest entry above — then walk the seven rows.
3. Still open from earlier rounds: the four `FACE-*` rows, the six voice rows,
   and the older human rows (music duck/resume, gated email send, the five
   PRD §8 demo gates).
4. Power-supply triage on the **next** undervoltage occurrence — procedure in
   `progress.md` → **Wake-up / power diagnosis**.

## Robot access (D-020, operator-authorized in a tracked file)

```
REACHY_HOST=10.0.0.96
REACHY_SSH_USER=pollen
```

`REACHY_SSH_PASSWORD` and `REACHY_HOSTKEY` are never tracked — repo-root `.env`
(gitignored) only. Deploy procedure: `.claude/skills/reachy-deploy/SKILL.md`.

## Repo sync

`person-memory-backend` is local only — not pushed, not merged.
`face-recognition-fix` was merged to `main` on 2026-08-27. `main` of both
`Reachy-companion` and `magic-mirror` is pushed to `origin/main`.
