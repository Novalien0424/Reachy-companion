# Session handoff — 2026-08-29 (clock-out during robot reboot)

Everything is merged to `main` and pushed (`ad5fe3e` + the deploy-session
commits after it). The **fifteenth install is on the robot but has never
booted**: the daemon's app tracker was wedged (`stopping` phantom, no
process; start/stop/restart all refused; daemon itself healthy) and the
operator is rebooting the robot **over ssh** right now. Pollen's own
prescription for this class of wedge is OFF → wait 5 s → ON.

## First actions next session

1. **Verify the fifteenth install's first boot.** Robot back up → app should
   autostart on antenna wake (`startup_app=reachy_companion`), or start via
   `POST /api/apps/start-app/reachy_companion`. Then read the journal
   (`ssh … journalctl --user -f` or the persistent journal) for, in order:
   - `persona: instance persona.md` (sha `4c87d2ec` was restored),
   - 41 tools registered,
   - `Face memory ready: … 4 people enrolled`,
   - the wake check under the new budget (`FACE_WAKE_BUDGET_MS=4000`,
     5 attempts) ending in one of the THREE greeting branches:
     `Startup greeting personalized for <name> with K remembered fact(s).`
     (recognized) / the stranger-intro prefix (face seen, unknown) / the
     profile greeting verbatim (empty room),
   - `boot gate released (greeting played)`, zero tracebacks.
   If the app-state wedge SURVIVES the reboot, that is new information —
   record it and check `current-app-status` before anything else.
2. **Walk the live rows** (operator + a human face): `PERSON-GREET-*`,
   `PERSON-MEMORY-AUTO`, `ENROLL-STILL`, `ENROLL-SNAPSHOT` (needs a FRESH
   「記住我」 — nothing backfills pictures for people enrolled earlier),
   `BACKEND-PUSH-LIVE` / `BACKEND-IMPORT`, then the older `FACE-*` and voice
   rows — full definitions in `progress.md` → Pending verification.
3. **Mac backend is currently STOPPED** (operator stopped the background
   task). Restart when wanted, from `companion_backend/`:
   `COMPANION_BACKEND_HOST="$(tailscale ip -4)" ./run.sh`
   → serves the tailnet at `http://<tailscale-ip>:8710`.
4. Owed measurements at first live contact: the Mac-embed vs robot
   voice-enrollment cosine comparability check (one person, both sources,
   `who_is_this` logs every score), and a judgement on the ~4 s pre-greeting
   pause.

## Deploy state (fifteenth install, 2026-08-28)

Wheel `068e6b81…` from `ad5fe3e`; two-step install; **extended manifest**
backup/restore at `/tmp/reachy_companion_backup/20260828T143611Z-22788` —
`faces.v1.json` 4 records + `people.v1.json` 4 records read back, persona
preserved, `memory.v1.json` + `face_snapshots/` recorded absent. The deploy
skill's manifest list now includes `people.v1.json` and `face_snapshots/`
permanently. Assets preloaded (YuNet pinned revision, SFace, emotion clips).
Rollback if ever needed: `pip uninstall -y reachy_companion` in the apps venv
(daemon untouched, D-009).

## Watch items

- The daemon app-state wedge followed the fourteenth install's stop-API
  `Motor communication error`. If it recurs after a clean stop, it is a
  pattern worth an upstream report alongside the undervoltage journal lines.
- Power: robot "hard to wake" → look at the power LED first (undervoltage
  history, triage procedure in `progress.md`).

## Robot access (D-020, operator-authorized in a tracked file)

```
REACHY_HOST=10.0.0.96
REACHY_SSH_USER=pollen
```

`REACHY_SSH_PASSWORD` and `REACHY_HOSTKEY` are never tracked — repo-root `.env`
(gitignored) only. Deploy procedure: `.claude/skills/reachy-deploy/SKILL.md`.

## Repo sync

`main` is pushed to `origin/main`; branches `person-memory-backend` and
`merge-and-snapshots` are merged and pushed. Working-tree residue, both
deliberate: `.gitignore` carries the operator's uncommitted `.gstack/` line;
`reachy_companion/uv.lock` stays untracked (does not re-resolve).
