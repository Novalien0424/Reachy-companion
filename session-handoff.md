# Session handoff — 2026-08-27

**No interrupted work — session ended clean 2026-08-27.**

## Where things stand

See `progress.md` → **Current state**: the robot carries the voice-robustness
build (`b4e154f`, thirteenth install), live-verified boot gate and
`gpt-transcribe`, app left stopped with `startup_app=reachy_companion`. Suite
1319/31, ruff + mypy green. `DECISIONS.md` D-023 is the round's record.

## Next natural actions

1. Operator live pass on the six `implemented-unverified` rows listed in
   `progress.md` → **Pending verification** (mini-model tool quality, solo
   barge feel, `wait_for_user`, party face gate, noise-reduction A/B,
   semantic_vad A/B), plus the older human rows (music duck/resume, gated email
   send, the five PRD §8 demo gates).
2. Power-supply triage on the **next** undervoltage occurrence — the procedure
   is in `progress.md` → **Wake-up / power diagnosis**. Possible upstream
   report to `pollen-robotics/reachy_mini` once a second occurrence pins the
   pattern.

## Robot access (D-020, operator-authorized in a tracked file)

```
REACHY_HOST=10.0.0.96
REACHY_SSH_USER=pollen
```

`REACHY_SSH_PASSWORD` and `REACHY_HOSTKEY` are never tracked — repo-root `.env`
(gitignored) only. Deploy procedure: `.claude/skills/reachy-deploy/SKILL.md`.

## Repo sync

Both `Reachy-companion` and `magic-mirror` are pushed to `origin/main`.
