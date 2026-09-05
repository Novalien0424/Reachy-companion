# Session handoff — 2026-09-05 (v1.23.0 solo interruption deployed)

State: **v1.23.0 is installed and RUNNING on the robot — left AWAKE at 05:30
robot time** for the operator's probe. `main` is pushed. No work in flight.
The robot lies down by itself on the inactivity timeout.

## Shipped this session (`b53e09f..HEAD`)

- 2026-09-04: boot posture → 一對一聊天模式 (D-029 decision 5 amended);
  `docs/rca-solo-interrupt-2026-09-04.md`.
- 2026-09-05: D-032 solo interruption wave — plan
  `docs/plans/2026-09-05-solo-interrupt-plan.md` rev 3 (Codex r1 8/8, r2 7/7
  accepted), 10 implementation commits, one review fix (`0f5264b`), suite
  1918/30. Twenty-third install, clean boot (evidence in `progress.md`).

## Next session

1. **Read the operator's probe** (`journalctl -u reachy-mini-daemon.service
   --since '2026-09-05 05:30'`, app lines are the `runner - WARNING -`
   payloads). Rows: `VOICE-SOLO-BARGE`, `VOICE-LATE-INTERRUPT`,
   `VOICE-INTERRUPT-CONTEXT`, `MODE-BOOT-DEFAULT` (voice half). Signals:
   `solo barge-in confirmed by transcript (substantive, N chars)` /
   `confirmed by sustained speech`, `User intervention: flushing player
   queue`, `solo barge rolled back (backchannel)`, `late solo interrupt
   (substantive) on committed turn`, `late solo interrupt declined (…)`,
   `conversation.item.truncate refused` (must NOT appear on the context
   probe), `accepted turn already answered by the barge watchdog`,
   `ignoring tool call from cancelled response`.
2. **False-interruption count** is the headline risk: `confirmed by
   sustained speech` with no user transcript after it → raise
   `REALTIME_BARGE_CONFIRM_MS` in the instance `.env` (no rebuild).
3. Recorded, not fixed (implementer deviation 4): a continuation that
   starts before a watchdog-answered turn's transcript lands and then
   produces no turn can make `_answer_owed_holdoff` request a second
   answer — would show as an answer repeating ~700 ms after a
   `turn hold-off: awaiting continuation` line on such a turn.
4. RCA Finding 4 (first-audio latency 2 s → 10.6 s over a session) is
   untouched: `REALTIME_REASONING_EFFORT=low` for one session against the
   RCA's table is the cheap probe; the three-metric A/B is the real one.
5. Still open: RCA-2, RCA-4 fabrication half, RCA-5, `who_is_this` too_far,
   `RPC-SAY-CROSS-LOOP`, hold-off calibration (`gap=` values from 09-04:
   6–638 ms merged, late continuations 239–1922 ms).

## Notes

- Journal pull from the Mac: `ssh … 'journalctl -u reachy-mini-daemon.service
  --since "…" --no-pager -o short-iso'`.
- `reference/` clone still missing on this Mac; not needed this session.
- Codex `--profile nova-auto exec` from this repo: two focused plan-review
  dispatches ran ~9 and ~7 min (193k / 129k tokens) and finished clean;
  a dispatch started before the plan edit landed had to be killed — write
  the plan first, verify the heading, then dispatch.
