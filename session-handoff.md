# Session handoff — 2026-08-29 (post-boot-verification)

The **fifteenth install's first boot is VERIFIED** (2026-08-29 00:41). The
operator's reboot cleared the daemon app-state wedge; the app was started via
`POST /api/apps/start-app/reachy_companion`, reached `running`, and the boot
journal showed every expected line: `persona: instance persona.md`, 41 tools,
`Face memory ready: … 4 people enrolled`, wake check 5 rounds / 2107 ms
(budget 4000 ms) → empty-room greeting branch, `boot gate released (greeting
played)`, extended window closed after 6 rounds, zero tracebacks. **The app
was left RUNNING on the robot.** No redeploy was needed — the wheel from
`ad5fe3e` was already installed.

## Next actions

1. **Walk the live rows** (operator + a human face): `PERSON-GREET-*`
   (EMPTY's journal half is now recorded as passed; the by-ear verbatim
   greeting + pause judgement remain), `PERSON-MEMORY-AUTO`, `ENROLL-STILL`,
   `ENROLL-SNAPSHOT` (needs a FRESH 「記住我」), `BACKEND-PUSH-LIVE` /
   `BACKEND-IMPORT`, then the older `FACE-*` and voice rows — full
   definitions in `progress.md` → Pending verification.
2. **Mac backend is STOPPED.** Restart when wanted, from
   `companion_backend/`:
   `COMPANION_BACKEND_HOST="$(tailscale ip -4)" ./run.sh`
   → serves the tailnet at `http://<tailscale-ip>:8710`.
3. Owed measurements at first live contact: Mac-embed vs robot
   voice-enrollment cosine comparability (one person, both sources,
   `who_is_this` logs every score), and the by-ear judgement of the ~2 s
   pre-greeting wake pause.

## Watch items

- Daemon app-state wedge: cleared by this reboot; if it recurs after a clean
  stop it is a pattern worth an upstream report.
- One benign boot warning: TURN credential fetch failed
  (`turn.fastrtc.org` DNS) — WebRTC console path only; greeting and audio
  unaffected. Re-check if the web console misbehaves.
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

`main` pushed to `origin/main`. Working-tree residue, both deliberate:
`.gitignore` carries the operator's uncommitted `.gstack/` line;
`reachy_companion/uv.lock` stays untracked (does not re-resolve).
