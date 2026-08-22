# Session handoff — 2026-08-22 (HA-Nova port merged; deploy blocked on robot power)

## Where things stand

**The HA-Nova port (22 tools) is COMPLETE and MERGED to `main` (`5601738`),
pushed.** Plan: `docs/superpowers/plans/2026-08-21-ha-nova-port.md`. SDD
ledger — the full record of every task's commits, rulings and residual minors:
`.superpowers/sdd/2026-08-21-ha-nova-port/progress.md`.

- Tasks 0–14 complete and individually reviewed; final whole-branch review
  closed ("with fixes" — 0 Critical, 2 Important, all fixed and re-reviewed in
  the final fix wave; report:
  `.superpowers/sdd/2026-08-21-ha-nova-port/final-fix-wave-report.md`).
- Full gate over the integrated tree: **1123 passed / 30 skipped**, ruff check
  clean, mypy strict clean. (`ruff format --check` has 5 pre-existing
  offenders; not a gate.)
- Task 15 progress: Step 1b (gate) ✅, Step 1c (merge, persona stash intact) ✅,
  Step 2 (aarch64 re-proof: yt-dlp 2026.8.19, imageio-ffmpeg 0.6.0,
  smbprotocol 1.17.0) ✅, Step 3 (exactly one wheel,
  `reachy_companion-1.0.0-py3-none-any.whl`, entry point verified) ✅.
- **Step 4 STOP: the robot did not route from the dev machine — confirmed
  cause: the Windows dev box is on a DIFFERENT LAN than the robot.** The robot
  is fine; nothing on it was touched. Verification continues from the
  **Mac mini**, which shares the robot's LAN.

## Resuming on the Mac mini (same LAN as the robot)

1. Clone the repo (`main` at the merge + evidence commits; everything needed is
   tracked). The SDD scratch workspace is gitignored and stays on the Windows
   box — the **Task 15 procedure is the plan's own Task 15 section** in
   `docs/superpowers/plans/2026-08-21-ha-nova-port.md` (identical content), and
   the deploy procedure of record is `.claude/skills/reachy-deploy/SKILL.md`.
2. Create the repo-root `.env` on the Mac (`cp .env.example .env`). Two of the
   four values are recorded HERE by explicit operator authorization (D-020,
   2026-08-22) so this checkout is self-sufficient:

   ```
   REACHY_HOST=10.0.0.96
   REACHY_SSH_USER=pollen
   ```

   `REACHY_SSH_PASSWORD` is NOT in git (a real secret): macOS `ssh`/`scp`
   prompt for it interactively, or set up a one-time SSH key. `REACHY_HOSTKEY`
   is PuTTY-specific and unused on macOS — first `ssh` contact prompts to
   accept the host key into `known_hosts`.
3. Platform notes — the Task 15 runbook's LOCAL wrappers are Windows-flavored;
   the REMOTE `sh` blocks (everything inside the here-strings) run on the robot
   and are portable as-is:
   - `plink`/`pscp` → `ssh`/`scp` (macOS OpenSSH). First contact prompts to
     accept the host key into `known_hosts` — interactive accept replaces the
     `REACHY_HOSTKEY`/`-hostkey` mechanism, which is PuTTY-specific. Password
     auth is interactive, or set up a one-time SSH key (ask the operator).
   - PowerShell steps → bash equivalents; the `Invoke-RestMethod` calls are
     `curl` one-liners.
4. Rebuild the wheel on the Mac (`uv build ./reachy_companion`, or
   `python -m build`); the wheel is pure-Python and platform-independent.
   Verify exactly ONE wheel in `dist/` before transfer (Step 3's rule).
5. Resume at Task 15 Step 4 (version gate: `GET http://$REACHY_HOST:8000/update/install-source`,
   no `/api` prefix). Then Steps 5–14 in order. Two standing rulings:
   - **Step 9 amendment (controller ruling):** the operator's real keys are
     already installed in the robot's instance `.env` — append ONLY missing
     keys as empty placeholders, never the full 26-key block (dotenv last-wins
     would blank real values). Check key NAMES only, never print values.
   - Wake-test voice rows (Step 13's 33-row table) need a human Chinese
     speaker; every other verification is scripted. Blocked rows are recorded
     as blocked with the missing key, never passed.
6. **Step 15b (persona stash restore) CANNOT run on the Mac** — git stashes do
   not push. `stash@{0}` ("hanova-port: user persona.md baseline", patch-id
   `d17ac2970a8c`) lives on the Windows dev box only; restore it there
   afterwards. Deploying the committed persona (Step 8b) is unaffected — it
   materializes from `git show HEAD:persona.md`.

## Git safety

- The operator's `persona.md` rewrite is in `stash@{0}` ("hanova-port: user
  persona.md baseline", patch-id `d17ac2970a8c`). Step 15b restores and
  verifies it (apply → verify → drop, never pop). **Do not drop the stash.**
  Note: the Task 0 patch files referenced by Step 15b lived in a `$env:TEMP`
  that predates an operator reboot — if absent, verify identity against
  `git stash show -p` patch-id instead.
- `feat/ha-nova-port` is merged; delete only after the operator confirms the
  on-robot pass.

## Robot and credentials

- Robot last known: asleep on the pre-port build; the new wheel is built but
  NOT yet installed.
- Machine access (host, SSH user, password, host key): repo-root `.env`,
  gitignored — never quote values in tracked files.
- Robot-side credentials are installed (key names in
  `reachy_companion/.env.example`); three private instance files (Google
  Workspace account JSON, Google OAuth JSON, NAS video index), mode 600. The
  deploy skill's ritual backs all three up.

## Operator-pending

Power/network for the robot (the deploy blocker), then the wake-test voice rows
(Step 13's 33-row transcript), the five PRD §8 demo gates, and
`finishing-a-development-branch` cleanup.
