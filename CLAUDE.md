# CLAUDE.md — Reachy Companion

Operating contract for this repository. What Reachy does, and the rules it is
built under, are in `docs/PRD.md`.

This is a proof-of-concept that makes a Reachy Mini Wireless feel physically
present: interruptible Chinese voice conversation on `gpt-realtime-2.1-mini`,
face tracking, expressive movement, vision on request, web search and MCP
tools, home control, and a simple pattern for adding Skills. It is a POC, not
an agent platform.

## Commands

Run from `reachy_companion/` with its dev venv (Python 3.12):

- **Tests:** `.venv/bin/python -m pytest -q`. The baseline is green apart from
  two known skips:
  - `tests/conftest.py` skips 30 upstream tests that assume a switchable
    profile (D-001);
  - one SFace test skips when that model is not in the local HF cache.

  Any other failure or skip is a regression.
- **Lint and types:** `.venv/bin/ruff check .` and `.venv/bin/mypy` (strict,
  `src/`).
- **Wheel:** `uv build .` builds it into `dist/`. The `reachy-deploy` skill
  deploys it.

## Reuse first

The official Pollen Robotics code is the golden standard. Full clones live in
the gitignored `reference/`:
- `reachy_mini`, the SDK;
- `reachy_mini_conversation_app`, which our package was scaffolded from
  (D-001).

**For every robot-facing feature:**
1. Check the SDK.
2. Then check the Conversation App.
3. Adapt existing code only where the POC's needs differ.
4. Write new code only when nothing exists.

**Never recreate:**
- face tracking, and gaze or jitter smoothing;
- camera access and audio I/O;
- motion primitives, and motion layering or arbitration;
- emotion animations and speech-reactive movement.

**Ours to build:**
- the realtime model integration, and conversation and turn behavior;
- web search and MCP wiring;
- Skills and home control;
- thin glue around official APIs.

The `reachy-reuse-first` skill has the compliance gate, and the clone and
refresh commands.

## How work is done

- **One task at a time.** Work on one bounded task at a time, and state the
  approach before large edits.
- **Delegation.**
  - **The main session** plans, reviews, integrates and verifies.
  - **A subagent** takes sizeable, bounded work: an implementation wave, a
    multi-file survey or a test suite. Use an Opus subagent (`model: opus`),
    and give it the reuse-first rules and the exact files it owns. Review its
    diff before accepting it.
  - **A few reads or edits** cost less done directly than a subagent that has
    to rebuild the context.
- **Plans.** Before executing a plan, check it against the PRD, the research
  notes and the cloned source. Look for:
  - reuse-first violations;
  - claims about the SDK or the app that no source read backs;
  - steps without a verification;
  - files no task owns.

  Record each finding, with its resolution, in the plan's review log.
- **Bugs.** Reproduce the failing signal before fixing the root cause, and
  keep the change surgical.
- **Dependencies.** No new or upgraded major dependency without approval. The
  two official repos are pre-approved.
- **Secrets** stay in the repo-root `.env` and the robot's instance `.env`.
  Never commit credentials, LAN addresses or usernames.
- **The robot.** Deploys are app-only (D-009), through the `reachy-deploy`
  skill. The daemon changes only through its official updater, under a
  recorded authorization (D-033).
- **Cleanup.** No broad process kills or destructive cleanup. Don't modify
  user-level skills unasked.

## Done means evidence

Acceptance is the five demos in PRD § Acceptance.

Completion needs runnable evidence: device runs, SDK-simulated runs, logs, or
tool-call traces. When a check can't run, record the blocker and the residual
risk in `feature_list.json` instead of marking the item complete.

Face recognition and per-person memory were promoted from the non-goals under
bounded constraints (PRD § Non-goals). Read those constraints before extending
either.

## State files

| File | Holds | Read it by |
|---|---|---|
| `session-handoff.md` | where the last session stopped, next steps | whole, first |
| `progress.md` | verified state (newest first), next action, known defects | the top `## Current state` entry and `## Next action` |
| `DECISIONS.md` | durable decisions D-001… | ID: `grep -n '^## D-032' DECISIONS.md` |
| `feature_list.json` | work queue: `items[]` of `id, behavior, verification, state, evidence, next_action` (older rows: `next`) | query: `jq '.items[] \| select(.id=="VOICE-SOLO-BARGE")' feature_list.json` |
| `docs/PRD.md` | what Reachy does, the five demos, the rules, non-goals | its Rules and Non-goals, before planning a feature |
| `docs/research-*.md` | maps of the official repos at the commit in each title | before robot-facing work |

`DECISIONS.md`, `feature_list.json` and `progress.md` together run past 80k
tokens, so look things up in them rather than reading them whole.

**Startup:** run `git status --short --branch`, then read
`session-handoff.md`, then the top of `progress.md`.

## Project skills

- **`reachy-reuse-first`:** robot-facing work, and upstream research and
  refresh.
- **`reachy-instructing-model`:** anything the realtime model reads.
- **`reachy-deploy`:** the physical robot.

A project skill never shadows a user-level skill's name.

## End of session

1. Run the relevant verification, or record why it couldn't run.
2. When behavior or rules changed, update `progress.md`, `feature_list.json`
   and `DECISIONS.md`. Write `session-handoff.md`.
3. Remove the temporary artifacts and abandoned code you introduced.
4. Report the remaining dirty files (`git status --short --branch`).
