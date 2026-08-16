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

- Task 1 COMPLETE (commits 84a5afc..f823a81): repo on `main`;
  `reachy_companion/` scaffolded from the official conversation app
  (141 files, copy integrity verified, byte-faithful fork with exactly the
  intended deltas); locked profile `_reachy_companion_locked_profile`
  converted to `profile.md` at app root and shipping in the wheel;
  `feature_list.json` work queue in place; `mcp` bounded `<2` (2.0 silently
  broke upstream client reads); bundled suite green with a documented
  30-test skip list (locked-profile-by-design); subtree agent contract
  replaced with a correct local one. SDK now 1.10.0rc5 (see D-008
  amendment).

## Risks / blockers

- Physical robot availability unconfirmed (needed for Task 15 only).
- `OPENAI_API_KEY`, Notion MCP credentials, and Home Assistant URL/token
  needed from the operator at Tasks 8/12/13.
- Dependencies other than `mcp` still float (no lockfile yet — demo-prep
  decision).

## Next action

Continue SDD execution at Task 2 (mockup-sim dev loop smoke test); then
Tasks 3–15 per the plan.
