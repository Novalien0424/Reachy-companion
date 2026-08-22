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
- **Step 4 STOP: the robot is unreachable.** The `REACHY_HOST` address in the
  repo-root `.env` answers ping but `:8000` (daemon) and `:22` (SSH) are both
  silent, no ARP entry on this segment, and the standard mDNS names answer
  nothing — consistent with the robot powered off (or on another network) with
  something else holding its old DHCP lease. Nothing on the robot was touched.

## To resume the deploy

1. Power the robot on; confirm its LAN address matches `REACHY_HOST` in the
   repo-root `.env` (update the `.env` if the lease moved — never a tracked
   file).
2. `REACHY_HOSTKEY` is still unset in `.env`; the resume sequence captures the
   fingerprint automatically on first contact and appends it.
3. Rerun Task 15 from Step 4 (brief:
   `.superpowers/sdd/2026-08-21-ha-nova-port/task-15-brief.md`) — wheel, env
   seeding rules (append only MISSING keys; the operator's real keys are
   already installed on the robot), persona deploy (Step 8b), and the
   verification steps are all ready. The wake-test voice rows need a human
   Chinese speaker; everything else is scripted.

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
