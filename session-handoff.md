# Session handoff — 2026-08-21 (operator reboot mid-port)

## Where things stand

**HA-Nova port (22 tools) mid-execution** on branch `feat/ha-nova-port`
(pushed to origin at `eadad01`). Plan: `docs/superpowers/plans/2026-08-21-ha-nova-port.md`
(Codex-reviewed 3 rounds, 48/48 findings accepted). SDD ledger — THE resume map:
`.superpowers/sdd/2026-08-21-ha-nova-port/progress.md`.

- Tasks 0–4 COMPLETE (see ledger for commits/rulings). Suite 794 passed / 30
  skipped at `eadad01`; ruff + mypy strict green.
- **Task 5 implementer was IN FLIGHT and died with the reboot → Task 5 is NOT
  done.** Working tree was clean at clock-out; if it is dirty now, discard the
  partial Task-5 work (scoped to reachy_companion/) and delete any partial
  `task-5-report.md`. Re-dispatch Task 5 fresh from
  `.superpowers/sdd/2026-08-21-ha-nova-port/task-5-brief.md` with this context:
  audio_drain module is Task-5-owned (not in music_player); wire GATE
  begin/end_session at the same boundaries; fold in Task 4's five Low fixes
  (a: `_store(None)` before superseded-after-pre-stop return; b: cache-hit
  `os.utime` + exclude `.resume.mp3` from keep budget; c: assert loser status
  "superseded"; d: delenv HANOVA_YTDLP_TIMEOUT_S in the timeout test;
  e: ruff-format test_hanova_music.py + redact.text in play_music.py:659,665).
  Mandatory tests: >12s-outstanding-audio-still-resumes; needs_response=False
  schedules resume.
- Then: Task 5 review → Tasks 6–15 per the ledger's established loop
  (opus implementers, task reviews, EXPECTED_TOOLS update authorized per
  tool-adding task, gated tools need finally-release per Task 2 ruling).

## Robot & credentials

- Robot: ASLEEP on `main` build `2aa0403` (pre-port; persona.md live,
  D-017 voice, startup_app set). Untouched by the port so far.
- ALL HANOVA credentials installed 2026-08-21 (never in git): robot instance
  `.env` has HA_TOKEN + 8 HANOVA/Google/NAS keys; instance dir has
  `google-workspace-mcp/novahome2733@gmail.com.json`, `google-oauth.json`,
  `nas-video-index.json` (mode 600). Dev mirror: `reachy_companion/.env`
  (gitignored) + files at `C:\Users\b8901\.reachy-companion\`.
- **Until Task 14 lands, the deploy skill's backup ritual does NOT cover the
  three new instance files — back them up manually on any interim deploy.**
- Mac access: Tailscale SSH `novalien0424@100.112.33.79`, passwordless, read-only use.

## Git safety

- Operator's persona.md rewrite is in `stash@{0}` ("hanova-port: user persona.md
  baseline", patch-id `d17ac2970a8c`, patch files in the SDD workspace).
  Task 15 restores + verifies it. Do not drop the stash.
- `main` = `f1df9e5` (robot-deployed state + plan). Nothing merges to main
  until Task 15's merge-before-deploy step.

## Operator-pending (unchanged)

Live pass on the robot (voice/face/move_head checks per repo progress.md),
five demo gates, final whole-branch review + finishing-a-development-branch
after Task 15.
