# Session handoff — 2026-08-22 (HA-Nova port, ready to merge)

## Where things stand

**HA-Nova port (22 tools) is COMPLETE on branch `feat/ha-nova-port`.** Plan:
`docs/superpowers/plans/2026-08-21-ha-nova-port.md`. SDD ledger — THE resume
map, with every task's commits, rulings and residual minors:
`.superpowers/sdd/2026-08-21-ha-nova-port/progress.md`.

- Tasks 0–14 (all 15) complete and individually reviewed.
- Final whole-branch review done: **"Ready to merge: with fixes"** — 0 Critical,
  2 Important. Both, plus five folded-in minors, were fixed in the final fix
  wave; report at
  `.superpowers/sdd/2026-08-21-ha-nova-port/final-fix-wave-report.md`.
- All other deferred minors are accepted residuals, recorded in the ledger.
- Gates green on the branch: pytest, `ruff check src tests`, `mypy` strict.
  (`ruff format --check` has 5 pre-existing offenders and is not a gate.)

## Next: Task 15 — merge and deploy

Brief: `.superpowers/sdd/2026-08-21-ha-nova-port/task-15-brief.md`. In order:
restore the operator's `persona.md` from `stash@{0}` and reconcile it with the
port's appended Tools section, merge the branch to `main`, deploy to the robot
with the `reachy-deploy` skill (app only — the daemon is never touched), then
run the five PRD §8 demo gates on the device.

## Robot and credentials

- Robot: asleep, running the pre-port `main` build. Untouched by the port.
- Machine access (host, SSH user, password, host key): repo-root `.env`, which
  is gitignored. Never quote its values in a tracked file.
- Credentials are installed on the robot and mirrored in the gitignored dev
  `.env` — **key names only** here: `HA_TOKEN` plus the eight HANOVA / Google /
  NAS keys documented in `reachy_companion/.env.example`. Three private files
  live in the robot's instance directory (Google Workspace MCP account JSON,
  Google OAuth JSON, NAS video index JSON), mode 600.
- Task 14 extended the deploy skill's backup/restore ritual to cover those three
  files, so no manual pre-deploy backup step is needed any more.

## Git safety

- The operator's `persona.md` rewrite is in `stash@{0}` ("hanova-port: user
  persona.md baseline", patch-id `d17ac2970a8c`, patch files in the SDD
  workspace). Task 15 restores and verifies it. **Do not drop the stash.**
- Nothing merges to `main` until Task 15's merge-before-deploy step.

## Operator-pending

Live pass on the robot (voice / face / move_head per `progress.md`), the five
demo gates, and `finishing-a-development-branch` after Task 15.
