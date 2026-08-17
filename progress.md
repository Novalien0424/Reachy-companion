# Progress

## Current verified state (2026-08-16)

Research phase complete; full implementation plan written and under Codex
review. No application code yet.

- `docs/PRD.md` — product source of truth (v0.2).
- `CLAUDE.md` — standalone operating contract; includes orchestration model
  (Opus subagents implement/survey/test; main session orchestrates + reviews)
  and the Plan Review rule (up to 3 Codex rounds via
  `codex --profile nova-auto exec …`; Claude holds final judgement).
- `docs/research-reachy-sdk.md` + `docs/research-conversation-app.md` —
  file:line-verified research maps from two Opus surveys, spot-checked
  against source. Key facts: SDK = thin client to a 50 Hz daemon that already
  owns tracking/wobbling/emotions; conversation app at HEAD has NO OpenAI
  backend (deleted in `5b8d974`, recoverable); web search + MCP client exist;
  official scaffolder generates our own-repo starting point.
- `DECISIONS.md` — D-001…D-008 (scaffolder bootstrap, new openai_realtime
  handler, VAD tuning for Chinese, MCP via discovery+registration, Home
  Assistant tool, HF-Space search kept, daemon tracking + copied arbitration,
  Windows mockup-sim dev).
- `docs/superpowers/plans/2026-08-16-reachy-mini-poc.md` — **Rev 4, cleared
  for execution.** 15 tasks with TDD steps. Codex review complete (3 rounds,
  cap reached): 27 findings total (R1: 15, R2: 8, R3: 4), 27 accepted (2
  with modification), 0 rejected — all folded in; full dispositions in the
  plan's Review Log. Notable review catches: scaffolder↔app profile-format
  and profile-location skew, missing OpenAI `_param` imports, no upstream
  resampling (and a wrong-axis hazard), MCP registry rebuild wiping ad-hoc
  tools, hosted Notion MCP OAuth requirement, HA entity allowlist.

## Execution status (SDD, ledger at .superpowers/sdd/2026-08-16-reachy-mini-poc/progress.md)

Tasks COMPLETE with clean reviews (each through implementer → task review →
fix rounds as needed → scoped re-review): 1 (scaffold, mcp<2 bound, green
baseline w/ 30-skip list), 2 (dev daemon on port 8001 — coexists with the
Reachy Mini Control desktop app), 3 (recovered-handler study), 4 (soxr-era
resample helper + scipy declared), 5 (OpenAIRealtimeHandler on
gpt-realtime-2.1: extra_query model delivery verified, soxr streaming
resampling both legs, tunable VAD), 6 (main.py wiring + tracking-on-startup,
zh default), 7 (Chinese locked profile incl. verified search-tool name),
12-unit (MCP seam: EXTRA_TOOLS survives rebuilds, bounded 20s discovery,
collision degrade), 13-unit (home_control with HA_ENTITIES allowlist +
Chinese demo contract), 16 (VoiceFX cute-robot filter, engine-free soxr
chipmunk + ring-mod, D-010 — Codex-reviewed amendment). Suite:
412 passed / 30 skipped / 0 failed; ruff + mypy strict green.

## Open / blocked

- Tasks 8, 9, 10, 11 (live dev-runs) + Task 12 Step 5 (Notion) + Task 13
  Step 5 (real HA device): BLOCKED on operator credentials
  (`OPENAI_API_KEY`, `NOTION_MCP_URL/TOKEN`, `HA_URL/TOKEN/HA_ENTITIES` in
  `reachy_companion/.env`).
- Task 14 (adding-a-skill doc): in progress (keyless).
- Task 15 (on-robot deployment + five-demo gate): needs robot; use the
  `reachy-deploy` skill (D-009: app-only, daemon untouchable, version gate);
  robot at 10.0.0.96 per repo-root `.env`.
- VoiceFX live tuning (Task 16 Step 9) happens during Task 8's dev-run.
- No lockfile yet (deps beyond `mcp` float) — decide at demo prep.

## Next action

Finish Task 14; then hold for credentials → run Tasks 8-11, integration
steps 12.5/13.5, final whole-branch review, Task 15 on-robot gate.
