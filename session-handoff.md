# Session handoff — 2026-09-02 (v1.21.0 deployed; field-test fix wave COMPLETE)

State: **v1.21.0 (the field-test fix wave, D-031) is installed and RUNNING on
the robot — left AWAKE at 14:02 robot time for the operator's voice probes.**
`main` is pushed through the final docs commit. No work in flight.

## What shipped (plan rev 3, all three items)

- **A — answer hold-off** at the accepted-turn seam: `REALTIME_COMMIT_HOLDOFF_MS`
  (700 default, 0 = old behaviour); event-ordered skip on renewed speech;
  connection-bound per-turn task; owed-answer rule when the continuation
  yields no turn. Commits `7f55584`, `39aa60f`.
- **B — spoken lead-ins**: commentary audio audible, transcripts withheld from
  persistence (`6754f4d`); prompt 訊息頻道/開場白 flip, 聽不清楚 rule moved
  last (A3), search/music/MCP description edits incl. the RCA-4 routing
  contrast, persona + locked profile one-line harmonisation (`40d575c`);
  bundled-spec override for manifested robots (`e4626b9`).
- **C — clean sleep stop**: broadened stop-request guard, C6 unmute only for
  pre-pose failures, guarded `movement_manager.stop()` ×2, sleep-summary
  retry once / 4 s (`48153a7`).
- Docs: D-031, CHANGELOG 1.21.0, plan marked EXECUTED, deploy skill step 6b,
  three new `feature_list.json` rows (`VOICE-TURN-FRAGMENTS`,
  `VOICE-SLOW-PREAMBLE`, `SLEEP-CLEAN-STOP`), `VOICE-COMMENTARY-SUPPRESS`
  retired. CLAUDE.md review cap is now 2 Codex rounds. `uv.lock` gitignored.

## Deploy evidence (twenty-first install)

Wheel sha `a96e06ed…`; backup `20260902T130104Z-39909`; restore 4 faces +
4 people (memory.v1.json + face_snapshots absent as before); persona pushed
post-restore, sha `4fe06ac4` = repo `persona.md`; step 6b: no manifest on
this robot, search description carries 示範語氣; boot: instance persona,
22 tools, session updated OK, boot gate released +28 s, 0 tracebacks /
errors / app warnings. Instance `.env` untouched (semantic_vad, low, high).

## Next session

1. **Operator voice probes** (robot is awake): the three new rows' scripts in
   `feature_list.json`; then the still-pending D-030 probes. Read the journal
   at INFO for `turn hold-off: …`, and at DEBUG for `commentary-phase item …
   is audible; transcript withheld` if needed. 「睡覺吧」 both ends the
   session and exercises `SLEEP-CLEAN-STOP`.
2. Watchpoints: 700 ms window feels sluggish → tune the knob in the instance
   `.env` (no rebuild); fast-tool narration returns → B4 buffering follow-up
   (own task), not a revert; a hold-off line with no answer following → check
   the owed-answer journal line.
3. Still open, untouched: RCA-2 (GROUP default friction), RCA-4 fabrication
   half, RCA-5 (now downstream of A — re-observe), `who_is_this` too_far
   defect, `RPC-SAY-CROSS-LOOP`.

## Notes

- `reference/` (official SDK/app clones) is MISSING on this Mac; nothing in
  this wave needed it (no new SDK call), but re-clone before SDK-facing work.
- Codex: `--profile nova-auto` lives in a separate config file and works;
  its sandbox always shows 3 environmental test failures — re-run the suite
  outside the sandbox before trusting a count (memory
  `codex-exec-dispatch-hygiene`).
