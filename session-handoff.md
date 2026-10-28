# Session handoff — 2026-10-28 (v1.24.0 on daemon v1.13.0)

State: **v1.24.0 is installed and RUNNING on the robot, daemon v1.13.0,
telemetry off** (clean boot 13:07 robot time). `main` is pushed. No work in
flight. Details: `progress.md` top entry, D-033 (+ outcome).

## Shipped this session

- Contract: CLAUDE.md/skills tightened for Opus 5.5; Codex plan review
  replaced by self-review; `reachy-research` merged into `reachy-reuse-first`;
  deploy skill Mac-first (`windows.md`), D-033 limits, tmpfs backup rule,
  v1.12.0 rollback.
- Upstream refresh: full clones in `reference/`; research notes
  delta-refreshed (SDK a9a2a42a, app b731ea1); wave 1 ports (plan
  `docs/plans/2026-10-28-upstream-refresh-plan.md`), suite 1917 → 1941 / 31.
- Daemon v1.10.0rc5 → v1.13.0 via the official updater; floor
  `reachy-mini>=1.13.0`; dev venv on 1.13.0.

## Next session

1. **Operator decision pending:** refresh `/bluetooth/` (BLE service +
   commands) and the gpio-shutdown unit from the v1.13 sources — the startup
   check's `sudo cp` is not in NOPASSWD sudoers, so the BLE PIN-bypass fix
   (GHSA-993g-hgjh-whmf) is not live.
2. **Discuss improvements** (operator asked for this after the deploy).
   Candidates on file: robot lost Wi-Fi/DNS for days without recovering
   (2026-10-21 → 10-28) — no NetworkManager lines retained; deferred ports
   (`resolve_instance_path`, camera JPEG colour on the Pi); RCA Finding 4
   first-audio latency growth; 2026-09-05 probe rows still unread.
3. In-room checks for UPSTREAM-REFRESH-2026-10 and the five-demo smoke (head
   tracking aims 15° lower since v1.11).

## Notes

- Copying robot credentials to the Mac is blocked by the auto-mode
  classifier; backups stay on the robot (`~/reachy_companion_backups/`).
- The sandbox intermittently hides the home directory from shell commands
  (`cd` "no such file"); relative paths from the repo root work, and commands
  run with the sandbox disabled are unaffected.
