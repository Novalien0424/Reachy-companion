# Session handoff — 2026-09-02 (fix-wave plan rev 2, review round 1 done, NO code touched)

State: repo has NO code changes — this session produced docs only. Robot
untouched since the v1.20.0 field test (app stopped 09-01 12:57; pose state
still uncertain — the RCA-6 physical question was never answered, and the fix
was designed to not need it). Operator directives this session: (1) the main
issue is turn-detection over-commit; (2) do RCA-3 (slow-tool preambles) and
fix RCA-6; (3) **Claude plans/reviews/orchestrates ONLY — Codex implements,
tests, runs suites** (budget).

## What exists now (all new, all uncommitted)

- `docs/plans/2026-09-01-field-test-fixes-plan.md` — **rev 2**, three items:
  A (answer hold-off at the ACCEPTED-TURN seam, `REALTIME_COMMIT_HOLDOFF_MS`
  default 700ms, 0=off; skip response when speech_started since commit or in
  window; composition rules for watchdog/denied-path/barge pinned in-plan),
  B (un-suppress commentary AUDIO only — transcripts stay out of
  RECORD/sleep/operator persistence; prompt 訊息頻道/開場白 flip; PREAMBLE
  phrases in search/music-play/MCP descriptions; RCA-4 routing rider;
  manifest-refresh deploy trap), C (broaden stop-request guard to
  URLError+HTTPException+OSError; C6 recovery must never unmute at/after the
  stop request; guard movement_manager.stop in BOTH main.py sites; sleep
  summary one retry, fixed +4s cap). Full review log in the plan.
- `docs/plans/2026-09-01-field-test-fixes-review-r1.md` — Codex round 1:
  11 findings (1 Crit/7 Imp/3 Min), 10 accepted + 1 in-part, 0 rejected;
  all folded into rev 2.
- `docs/codex-investigation-sleep-2026-09.md` — RCA-6 confirmed:
  RemoteDisconnected escapes the URLError-only guard (NOT a URLError
  subclass); sleep summary is a single 8s chat.completions call, no retry.
- `docs/codex-investigation-commentary-2026-09.md` — suppression seam map;
  tool name unknowable at the drop point; 5 existing test pins listed.
- `docs/codex-research-turn-detection-2026-09.md` — semantic_vad has NO
  server-side knob left (eagerness only bounds the unsure case); client
  hold-off = LiveKit's cancel-on-continuation pattern at our seam.
  Provenance note inside: partially SALVAGED from a dead Codex run.

## Operational warning (memory `codex-exec-dispatch-hygiene`)

`codex exec` on this machine dies at remote-compact (404 from
chatgpt.com/backend-api/codex/responses/compact) on any big-context run —
killed 3 research attempts. Keep dispatches single-topic, cap web fetches,
never pipe through `tail`, check `~/.codex/sessions/.../rollout-*.jsonl`
mtime to detect hangs. Focused code tasks (~8min) work fine.

## Next session

1. Dispatch Codex plan-review **round 2** on rev 2 (stop early if no
   accepted findings), adjudicate, then rev 3 if needed.
2. Have Codex implement task-by-task (Claude reviews diffs; suite baseline
   1819/30, ruff + mypy --strict clean).
3. Persona/profile edits in Item B make the persona re-sync a hard deploy
   gate; deploy = twenty-first install via `reachy-deploy`; new live rows
   `VOICE-TURN-FRAGMENTS`, `VOICE-SLOW-PREAMBLE`, `SLEEP-CLEAN-STOP` (specs
   in the plan §Verification).
4. Still open, untouched: RCA-2 (GROUP default friction), RCA-4 fabrication
   half, RCA-5 (downstream of A), who_is_this too_far defect,
   RPC-SAY-CROSS-LOOP.
