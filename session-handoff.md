# Session handoff — 2026-09-04 (solo boot default + interruption RCA)

State: **v1.22.0 is still the installed wheel; the app is STOPPED** (operator
said 「請進入睡眠模式」 at 12:10 robot time). `main` is pushed after this
session's commit. The robot's instance `.env` now has
`REALTIME_DEFAULT_MODE=one_on_one` (line 103; backup `.env.bak-20260904-mode`
alongside it), so the next antenna wake boots into 一對一聊天模式 without a
redeploy. The repo's code default matches, unreleased (CHANGELOG
`[Unreleased]`, version still 1.22.0 — bump to 1.23.0 at the next install).

## Done today

- Boot posture → `ONE_ON_ONE` (D-029 decision 5, amendment 2026-09-04):
  `conversation_mode.py`, `_boot_conversation_mode` docstring + dead-knob
  warning (now points `REALTIME_PARTY_DEFAULT=1` at
  `REALTIME_DEFAULT_MODE=group`), `set_conversation_mode` description and
  enum text, 6 tests. Suite 1873/30, ruff + mypy --strict clean.
- `docs/rca-solo-interrupt-2026-09-04.md` — the operator's two complaints
  traced to: name-gated interruption in solo mode (Finding 1), pause cap
  measured from onset vs transcript-after-commit (Finding 2), rollback
  re-queues the fully-buffered old reply ahead of the new answer (Finding 3),
  first-audio latency 2 s → 10.6 s across the session (Finding 4).
- feature_list: MODE-BOOT-DEFAULT rewritten; VOICE-SOLO-BARGE and
  VOICE-LATE-INTERRUPT carry today's live evidence (late path VERIFIED at
  12:02:02; one unexplained non-fire at 11:51:23).

## Next session

1. **Operator decision** on RCA candidate fix 1: in `ONE_ON_ONE`, decide a
   pause on the substantive rule (any real sentence stops the reply) instead
   of the name gate. It reverses D-029 decision 1 for solo only; GROUP/RECORD
   keep the gate. Fix 2 (cancel + flush the old reply when a post-rollback
   turn is accepted for an answer) removes the "finishes first" symptom on
   its own and is compatible with either answer.
2. If approved: plan (Codex review, 2 rounds cap), implement fixes 1–3
   (3 = INFO line on the declined late-interrupt branch), suite, deploy as
   v1.23.0 via `reachy-deploy` (backup ritual restores `.env`; step 6b).
3. Cheap probes meanwhile, instance `.env` only: `REALTIME_SOLO_NAME_GATE=0`
   for one session (pre-D-028 substantive rule; confirm window 1600 ms) —
   this IS fix 1 without the mode awareness; `REALTIME_VAD_EAGERNESS=auto`
   one session to size Finding 2's VAD share; `REALTIME_REASONING_EFFORT=low`
   one session against Finding 4's table.
4. Verify MODE-BOOT-DEFAULT on the first wake: `Tools in session (one_on_one,
   boxes=none, startup, 22)` and an unaddressed sentence answered.
5. Still open, untouched: RCA-2, RCA-4 fabrication half, RCA-5,
   `who_is_this` too_far defect, `RPC-SAY-CROSS-LOOP`, hold-off calibration
   (today's `gap=` values: 6–638 ms merged, late continuations 239–1922 ms).

## Notes

- Journal pull that worked from the Mac: `ssh … 'journalctl -u
  reachy-mini-daemon.service --since "…" --no-pager -o short-iso'`; app lines
  are the `runner - WARNING -` payloads.
- Speaker volume 95 (2026-09-03). `reference/` clone still missing on this Mac.
