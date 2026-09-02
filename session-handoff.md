# Session handoff — 2026-09-02 (v1.22.0 deployed; fix wave + research follow-ups COMPLETE)

State: **v1.22.0 is installed and RUNNING on the robot — left AWAKE at 15:56
robot time.** `main` is pushed. No work in flight. The robot will lie down by
itself on the inactivity timeout if nobody talks to it.

## Shipped today (13 commits, `5715829..HEAD`)

- v1.21.0 (D-031): plan rev 3 — hold-off (A), audible lead-ins (B), clean
  sleep stop (C). Twenty-first install 14:02.
- v1.22.0 (D-031 addendum): hold-off calibration telemetry, symmetric
  use-when/do-NOT-use pairs on 11 core tools, `### 變化` rule, stop guard at
  WARNING. Twenty-second install 15:56. Research note
  `docs/research-holdoff-calibration-2026-09.md`.
- CLAUDE.md review cap 3 → 2; `uv.lock` gitignored; deploy skill step 6b.

## Live evidence so far (v1.21.0, operator probe 14:36–14:37)

- `SLEEP-CLEAN-STOP` → **verified** (goodbye → pose → guard caught the daemon's
  stop-request timeout → clean shutdown, no unmute).
- `VOICE-TURN-FRAGMENTS`: first skip line seen; response.created ≈ 990 ms
  after transcript on every turn (the window's cost). Still needs the
  fragmented-sentence probe and the new `gap=`/`late continuation` numbers.
- `VOICE-SLOW-PREAMBLE`: not yet probed (no search turn).

## Next session

1. **Operator probes on v1.22.0**: a normal conversation, one search turn,
   one fast head move, a deliberately fragmented sentence, then 「睡覺吧」.
   Then `journalctl | grep 'turn hold-off'` → set `REALTIME_COMMIT_HOLDOFF_MS`
   from the gap distribution (rule of thumb in row `HOLDOFF-CALIBRATION`);
   change it in the instance `.env`, no rebuild.
2. Operator knobs to consider from the live watchpoints: raise
   `SLEEP_GOODBYE_DRAIN_CAP_S` if the goodbye is audibly cut; GROUP-mode
   ambient denials are RCA-2 (open, by design).
3. Next-wave candidates, priority order (D-031 addendum): verbatim JSON
   envelope on every recited result (RCA-4); end-of-context re-injection of
   the unclear-audio rule as a `conversation.item` (RCA-5); B4 buffered
   selective commentary (only if fast-tool narration returns);
   `reasoning.effort` three-metric A/B + one full-2.1 diagnostic session;
   `# Reference Pronunciations` (needs the operator's list); out-of-band
   responses; a Chinese voice regression set.
4. Still open, untouched: RCA-2, RCA-4 fabrication half, RCA-5,
   `who_is_this` too_far defect, `RPC-SAY-CROSS-LOOP`.

## Notes

- `reference/` clone is MISSING on this Mac; nothing today needed it.
- Codex `--profile nova-auto` lives in a separate config file and works; its
  sandbox always shows 3 environmental test failures — re-run the suite
  outside the sandbox before trusting a count.
