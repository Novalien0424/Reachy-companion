# CLAUDE.md — Reachy Mini Realtime AI Agent POC

Operating contract for this repository. Product source of truth: `docs/PRD.md`.

## Project Goal

A proof-of-concept that makes the Reachy Mini Wireless feel like a physically
present AI character: fast interruptible voice conversation on
`gpt-realtime-2.1` (Chinese is a primary scenario), face tracking, expressive
emotion movement, on-demand camera vision, automatic web search, one working
MCP integration, one real Home Control Skill, and a simple pattern for adding
Skills. A POC, not a generic agent platform (see Non-Goals).

## Commands

Run from `reachy_companion/` with its dev venv (Python 3.12):

- Tests: `.venv/bin/python -m pytest -q`. The baseline is green.
  `tests/conftest.py` skips 30 upstream tests that assume a switchable profile
  (D-001), and one SFace test skips when that model is not in the local HF
  cache. Any other failure or skip is a regression.
- Lint and types: `.venv/bin/ruff check .` and `.venv/bin/mypy` (strict, `src/`).
- Wheel: `uv build .` → `dist/`. Deploying it: `reachy-deploy` skill.

## Reuse First

The official Pollen Robotics code is the golden standard. Full clones live in
`reference/` (gitignored; the `reachy-reuse-first` skill has the clone and
refresh commands):

- `reference/reachy_mini` — the SDK
- `reference/reachy_mini_conversation_app` — the Conversation App our package
  was scaffolded from (D-001)

For every robot-facing feature: (1) check the SDK, (2) check the Conversation
App, (3) adapt existing code only where POC requirements differ, (4) write new
code only when nothing exists. Never recreate face tracking, gaze/jitter
smoothing, camera access, motion primitives, motion layering/arbitration,
emotion animations, speech-reactive movements, or audio I/O. Ours to build:
`gpt-realtime-2.1` integration, conversation/turn behavior, web search, MCP
wiring, Skills, home control, and thin glue around official APIs.

## Orchestration

- The main session plans, reviews, integrates and verifies, and keeps its
  context for that judgement. Implementation waves, multi-file surveys and
  test writing go to Opus subagents (Agent tool, `model: opus`), each with a
  bounded task, the reuse-first rules and the exact files it owns.
- Delegate only sizeable, bounded work: a few file reads, a handful of edits,
  or checking a subagent's result cost less done directly in the main session,
  because each subagent re-establishes context and reports back.
- Review each subagent's output and diff before accepting it.

## Plan Review

Before executing an implementation plan, review it yourself: check every step
against the PRD, the research notes and the cloned source in `reference/`;
look for reuse-first violations, steps without a verification, files no task
owns, and claims about SDK/app behavior that no source read backs. Revise, and
record each finding with its resolution in the plan's review log.

## State Files

| File | Holds | Read it by |
|---|---|---|
| `session-handoff.md` | where the last session stopped, next steps | whole, first |
| `progress.md` | verified state (newest first), next action, known defects | the top `## Current state` entry and `## Next action` |
| `DECISIONS.md` | durable decisions D-001… | ID: `grep -n '^## D-032' DECISIONS.md` |
| `feature_list.json` | work queue: `items[]` of `id, behavior, verification, state, evidence, next_action` (older rows: `next`) | query: `jq '.items[] \| select(.id=="VOICE-SOLO-BARGE")' feature_list.json` |
| `docs/PRD.md` | requirements, the five demos, non-goals | §10 Implementation Philosophy and §11 Adversarial Review before planning a feature |
| `docs/research-*.md` | maps of the official repos at the commit in each title | before robot-facing work |

`DECISIONS.md`, `feature_list.json` and `progress.md` together run past 80k
tokens, so look things up in them rather than reading them whole.

Startup: `git status --short --branch`, then `session-handoff.md`, then the top
of `progress.md`.

## Working Contract

- WIP=1: one bounded task at a time; state the approach before large edits.
- Bugs: reproduce the failing signal first, fix the root cause, keep the change
  surgical; add or update tests first when practical.
- No new or upgraded major dependencies without approval (the two official
  repos are pre-approved).
- Secrets stay external (repo-root `.env`, the robot's instance `.env`); never
  commit credentials, LAN addresses or usernames.
- Don't swallow errors; validate inputs at system boundaries.
- The robot: deploy as an app only (D-009). The daemon changes only through
  its official updater under a recorded authorization — currently the
  2026-10-28 upgrade to the latest release (D-033). `reachy-deploy` skill;
  access is in the repo-root `.env`.
- No broad process kills or destructive cleanup; don't modify user-level skills
  unasked.

## Verification Gate

Acceptance is PRD §8: five demos working reliably — multi-turn Chinese
conversation with interruption, physical expression, camera-based object
description, automatic web search, one real home-device action. Completion
needs runnable evidence (device runs, SDK-simulated runs, logs, tool-call
traces), never "should work". When a check cannot run (e.g. no robot on hand),
record the blocker and residual risk in `feature_list.json` instead of marking
the item complete.

## Non-Goals

PRD §9 is the list. Two amendments promoted bounded items under strict
constraints: face recognition (2026-08-18, US-12) and per-person memory with a
Mac-side backend (2026-08-28, D-025). Read those constraints before extending
either; everything else on §9 stays out until the five demos work.

## Project Skills

`reachy-reuse-first` (robot-facing work, upstream research and refresh),
`reachy-instructing-model` (anything the realtime model reads),
`reachy-deploy` (the physical robot). A project skill never shadows a
user-level skill's name.

## End Of Session

1. Run relevant verification or record exactly why it could not run.
2. Update `progress.md`, `feature_list.json`, `DECISIONS.md` when behavior or
   rules changed; write `session-handoff.md` for interrupted work.
3. Remove temporary artifacts and abandoned code you introduced.
4. Report remaining dirty files (`git status --short --branch`).
