# Session handoff — 2026-09-02 (fix-wave plan rev 3, review CLOSED, NO code touched)

State: repo has NO code changes — docs/config only. Robot untouched since
the v1.20.0 field test (app stopped 09-01 12:57; pose state still uncertain;
the Item C fix was designed not to need it). Operator directives still in
force: (1) turn-detection over-commit is the main issue; (2) do RCA-3
(slow-tool preambles) and fix RCA-6; (3) **Claude plans/reviews/orchestrates
ONLY — Codex implements, tests, runs suites** (budget).

## Done this session (2026-09-02)

- `CLAUDE.md` Plan Review cap lowered: **up to 2 Codex iterations** (was 3).
- `reachy_companion/uv.lock` gitignored (dev = `uv pip install -e`, robot =
  wheel into apps_venv; nothing consumed the lock and it was already stale).
- Codex plan-review **round 2** on rev 2 → `docs/plans/2026-09-01-field-test-fixes-review-r2.md`:
  3 Important findings, 3 accepted, 0 rejected, all folded into **rev 3** of
  `docs/plans/2026-09-01-field-test-fixes-plan.md` (A1 gained "Non-blocking"
  and "Lifecycle" rules; B1 wording de-capped). **Review closed at the cap.**
  Totals: 2 rounds, 14 findings, 13 accepted + 1 in part, 0 rejected.

## Observations (not acted on)

- `reference/` (official SDK + Conversation App clones, gitignored) is
  MISSING on this machine. Research docs exist so the hard gate holds, but
  re-clone before Codex touches lifecycle code (Item C) if SDK behaviour
  needs checking.
- `~/.codex/config.toml` defines no `nova-auto` profile; `codex --profile
  nova-auto exec` still runs (approval never, workspace-write, gpt-5.5 xhigh)
  — identical to the 09-01 runs. Left alone.
- Codex's plugin tries a Notion MCP connection at startup and logs an
  `invalid_token` rmcp error; harmless for read-only reviews.

## Operational warning (memory `codex-exec-dispatch-hygiene`)

`codex exec` on this machine dies at remote-compact on big-context runs.
Keep dispatches single-topic, cap web fetches, never pipe through `tail`,
check `~/.codex/sessions/.../rollout-*.jsonl` mtime to detect hangs. The
round-2 review (focused, read-only) finished clean in ~4 min.

## Next session

1. Have Codex implement rev 3 **task-by-task** (Item A → B → C), one
   dispatch per task, Claude reviews each diff. Gates per task from
   `reachy_companion/`: `ruff check .`, `mypy --strict src`,
   `python -m pytest` (baseline 1819 passed / 30 skipped).
2. Item B edits `persona.md`/profile → persona re-sync is a hard deploy gate;
   B2's manifest refresh must reach a manifested robot (deploy checklist
   line). Deploy = twenty-first install via `reachy-deploy`.
3. New live rows `VOICE-TURN-FRAGMENTS`, `VOICE-SLOW-PREAMBLE`,
   `SLEEP-CLEAN-STOP` (specs in the plan §Verification) need a person in
   the room.
4. Still open, untouched: RCA-2 (GROUP default friction), RCA-4 fabrication
   half, RCA-5 (downstream of A), who_is_this too_far defect,
   RPC-SAY-CROSS-LOOP.
