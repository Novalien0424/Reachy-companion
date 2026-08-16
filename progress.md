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

## Risks / blockers

- Repository still not `git init`-ed (plan Task 1 does it at execution).
- Known upstream skew: SDK scaffolder emits legacy profile format
  (`instructions.txt`) while the app requires `profile.md` — handled by plan
  Task 1 Step 5.
- Physical robot availability unconfirmed (needed for Task 15 only).
- `OPENAI_API_KEY`, Notion MCP credentials, and Home Assistant URL/token
  needed from the operator at Tasks 8/12/13.

## Next action

Execute the plan task-by-task with Opus subagents
(superpowers:subagent-driven-development), main session reviewing between
tasks. Task 1 starts with `git init` and the scaffolder. Operator inputs
needed along the way: `OPENAI_API_KEY` (Task 8), Notion MCP credentials
(Task 12), `HA_URL`/`HA_TOKEN`/`HA_ENTITIES` (Task 13), robot access
(Task 15).
