# Session handoff — 2026-08-27

**No interrupted work.** The face-recognition fix wave is complete and
gate-green on branch `face-recognition-fix`; nothing is half-applied. What it
has *not* had is a robot.

## Where things stand

See `progress.md` → **Current state**. The robot still carries the
voice-robustness build (`b4e154f`, thirteenth install), live-verified boot gate
and `gpt-transcribe`, app left stopped with `startup_app=reachy_companion`.
Branch `face-recognition-fix` (commits `143b551`…`ece7a58`) adds identity
routing to `who_is_this`, the threshold-only margin rule, largest-face identify,
tool-layer retries + 3-sample enrollment, the bounded extended wake window
(`FACE_WAKE_EXTENDED_MS`, default 8000), the `arcface5` alignment marker plus an
enrolled-count ready log, and the SDK-pinned YuNet preload. Plan:
`docs/plans/2026-08-27-face-recognition-fix.md`; record: `DECISIONS.md`
**D-024**.

## Next natural actions

1. ~~Deploy the face fix wave~~ **DONE 2026-08-27 14:48–14:50 BST** —
   fourteenth install, commit `ae62756`, all startup evidence verified
   (`2 people enrolled`, extended wake window 7 rounds → closed at bound,
   `persona: instance persona.md`, zero tracebacks); `faces.v1.json` survived
   with both records; robot left app-stopped. Note: the stop API answered
   `Motor communication error!` though the app stopped cleanly and
   `get_throttled` read `0x0` — see `progress.md` → Current state.
2. Operator live pass on the four new `FACE-*` rows (`FACE-ROUTING`,
   `FACE-WAKE-EXTENDED`, `FACE-CROSS-SESSION`, `FACE-MULTI-SAMPLE`) — they need
   a human face; exact journal greps are in `progress.md` → **Pending
   verification** and in `feature_list.json`.
3. Operator live pass on the six voice rows from the previous round
   (mini-model tool quality, solo barge feel, `wait_for_user`, party face gate,
   noise-reduction A/B, semantic_vad A/B), plus the older human rows (music
   duck/resume, gated email send, the five PRD §8 demo gates).
4. Power-supply triage on the **next** undervoltage occurrence — procedure in
   `progress.md` → **Wake-up / power diagnosis**. Possible upstream report to
   `pollen-robotics/reachy_mini` once a second occurrence pins the pattern.

## Robot access (D-020, operator-authorized in a tracked file)

```
REACHY_HOST=10.0.0.96
REACHY_SSH_USER=pollen
```

`REACHY_SSH_PASSWORD` and `REACHY_HOSTKEY` are never tracked — repo-root `.env`
(gitignored) only. Deploy procedure: `.claude/skills/reachy-deploy/SKILL.md`.

## Repo sync

`face-recognition-fix` is local only — not pushed, not merged. `main` of both
`Reachy-companion` and `magic-mirror` is pushed to `origin/main`.
