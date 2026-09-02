# CLAUDE.md — Reachy Mini Realtime AI Agent POC

Standalone operating contract for this repository. Product source of truth:
`docs/PRD.md`.

## Project Goal

Build a proof-of-concept for the **Reachy Mini Wireless** robot that makes it
feel like a physically present AI character: fast interruptible voice
conversation on `gpt-realtime-2.1` (Chinese is a primary scenario), face
tracking, expressive emotion movement, on-demand camera vision, automatic web
search, one working MCP integration (e.g. Notion), one real Home Control
Skill, and a simple pattern for adding more Skills. This is a POC, **not** a
generic agent platform — see Non-Goals.

## Reuse First (core principle)

The golden standard is the official Pollen Robotics code, cloned in
`reference/` (gitignored):

- `reference/reachy_mini` — the SDK
- `reference/reachy_mini_conversation_app` — the official Conversation App

For every robot-facing feature, in order: (1) check the SDK, (2) check the
Conversation App, (3) adapt existing code only where POC requirements differ,
(4) write new code only when nothing exists. **Never recreate:** face
tracking, gaze/jitter smoothing, camera access, motion primitives, motion
layering/arbitration, emotion animations, speech-reactive movements, audio
I/O. **Do build** (the genuinely different parts): `gpt-realtime-2.1`
integration, conversation/turn behavior, web search, MCP wiring, Skills
pattern, home control, thin glue around official APIs.

## Orchestration Model

- The **main Claude session is the orchestrator and reviewer only**: it
  plans, dispatches subagents, reviews their output and diffs, integrates,
  and verifies.
- Dispatch **Opus subagents** (Agent tool with `model: opus`) for the real
  work: implementation, codebase/SDK survey, research, and test
  writing/execution. This conserves token cost.
- Review subagent output before accepting it; do not hand-write large
  implementations in the main session. Give each subagent a bounded task, the
  reuse-first rules, and the exact files it owns.

## Plan Review (Codex)

Every implementation plan gets an external review by the Codex CLI before
execution — **up to 2 iterations** of: submit plan → collect Codex findings →
revise. Stop early when a round yields no accepted findings. Claude (the main
session) **holds final judgement**: each Codex finding is accepted or rejected
on evidence (PRD, research notes, source in `reference/`), and rejections are
recorded with a one-line reason in the plan's review log. Codex advises; it
does not gate.

Invocation: always run Codex as `codex --profile nova-auto exec …` — the
`nova-auto` profile is required so Codex does not block its own file/command
access mid-review. Add `--skip-git-repo-check` while the repo is not yet a git
repository. Do not pass `--sandbox` flags that would override the profile.

## Project Shape

- `docs/PRD.md`: requirements, user stories, five success demos, non-goals.
- `docs/research-reachy-sdk.md` / `docs/research-conversation-app.md`:
  research-phase findings. **Must exist before robot-facing code.**
- `reference/`: official repo clones (read-only reference, never committed).
- `reachy_companion/`: our app (scaffolded from the official conversation app,
  D-001). Package source in `reachy_companion/src/reachy_companion/`, tests in
  `reachy_companion/tests/`, profiles in `reachy_companion/profiles/`.
- `feature_list.json`: work queue — each item carries behavior description,
  verification method, state, evidence, next action.
- `progress.md`: current verified state, risks, next action.
- `DECISIONS.md`: durable implementation decisions.
- `session-handoff.md`: compact handoff for interrupted work.

## Startup Workflow

1. Read state files if present: `progress.md`, `DECISIONS.md`,
   `feature_list.json`, `session-handoff.md`, active plans.
2. Read `docs/PRD.md` — at minimum §10 Implementation Philosophy and §11
   Adversarial Review.
3. Before robot-facing work: confirm both `docs/research-*.md` files exist;
   if not, run the research phase first (`reachy-research` skill).
4. `git status --short --branch` once this becomes a git repository.

## Working Contract

- WIP=1: one bounded task at a time; state the approach before large edits.
- For bugs, reproduce the failing signal first; fix root causes; keep changes
  surgical. Add or update tests/checks first when practical.
- No new/upgraded major dependencies without approval (the two official repos
  are pre-approved).
- Externalize secrets (OpenAI keys, MCP tokens, home-control credentials);
  never commit credentials.
- Do not swallow errors; validate inputs at system boundaries.
- No broad process kills or destructive cleanup; preserve user changes you
  did not make; do not modify harness files or user-level skills unasked.

## Verification Gate

Acceptance bar is PRD §8 — five demos working reliably: multi-turn Chinese
conversation with interruption, physical expression, camera-based object
description, automatic web search, one real home-device action. Task
completion requires runnable evidence (device runs, SDK-simulated runs, logs,
tool-call traces) — never "should work". If a check cannot run (e.g. no robot
on hand), record the exact blocker and residual risk in `feature_list.json`
instead of marking complete.

## Non-Goals (do not build in the POC)

Generic agent platform, plugin marketplace, Skill SDK, multi-model routing,
cost optimization, long-term memory, identity/profile systems, continuous
video understanding, advanced authorization, new motion engine, custom motor
control, custom face recognition, mobile/desktop apps, production
observability, enterprise security. Revisit only after the five demos work.

## Project Skills

- `.claude/skills/reachy-reuse-first` — invoke **before implementing any
  robot-facing feature** (motion, tracking, camera, audio, emotions,
  lifecycle).
- `.claude/skills/reachy-research` — invoke when official-repo knowledge is
  missing, i.e. before first implementation in any subsystem or when
  `docs/research-*.md` do not exist.
- `.claude/skills/reachy-deploy` — invoke for any deployment to the physical
  robot. Operator authorization on file (2026-08-17): deploy as APP only;
  never modify the robot's daemon. Robot access is in the repo-root `.env`
  (gitignored).

User-level skills continue to apply unchanged; project skills complement them
and must never shadow a user-level skill's name.

## End Of Session

1. Run relevant verification or record exactly why it could not run.
2. Update `progress.md`, `feature_list.json`, `DECISIONS.md` when behavior or
   rules changed; write `session-handoff.md` for interrupted work.
3. Remove temporary artifacts and abandoned code you introduced.
4. Report remaining dirty files (`git status --short --branch` once a repo).
