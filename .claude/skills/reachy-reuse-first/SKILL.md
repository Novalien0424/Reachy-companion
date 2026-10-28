---
name: reachy-reuse-first
description: Use when about to implement, design, prototype, or "spike" any robot-facing behavior for Reachy Mini — face/head tracking, gaze smoothing, motion, animations, emotions, camera, audio, app lifecycle — including temporary, fallback, or demo-deadline versions; and when official-repo knowledge is missing or stale (`reference/` absent, a subsystem not yet mapped, upstream released since the research notes, porting an upstream fix).
---

# Reachy Reuse First

The official Pollen Robotics code already solves the robot-specific problems.
Recreating it is the project's #1 named failure mode (PRD §11). Verbal
agreement with this rule while still writing the code is a violation — the
baseline failure this skill exists to stop is an agent that recommends reuse,
then hand-rolls the forbidden layer anyway "just in case." The ladder and the
never-recreate / ours-to-build lists are in CLAUDE.md § Reuse First.

## Compliance gate

Before implementing, name the official module you inspected (file path in
`reference/`) that you are reusing or adapting — or record in the task file why
nothing exists. No named module → not past step 1. Every SDK/app call in our
code traces to a verified signature in the research notes or a direct read of
the cloned source.

If the repos are not cloned or you cannot verify what they provide, stop: your
deliverable is the research/spike plan (below), not code. Writing SDK calls
from memory with `[VERIFY]` markers is not a workaround; it is the violation.

## Rationalization Table

| Excuse (observed verbatim in baseline testing) | Reality |
|---|---|
| "I'll build it as a fallback in case the official app is insufficient" | You just built the forbidden thing. Spike first; decide after evidence. |
| "The smoothing/arbitration layer is genuinely ours" | The official app ships layered motion and speech-reactive movement. Inspect it before claiming ownership. |
| "I can't access the repos right now, so I'll write from memory and mark [VERIFY]" | Blocked research = blocked task. Deliver the spike plan and the blocker. |
| "Demo is in 3 hours — no time to inspect" | The 25-minute run-the-official-app spike is the *fastest* path (PRD §11 Mistake 1). |
| "A new emotion animation is small" | Official emotions are the primary reference (US-03). New animations only after inspecting them. |

## Red Flags — STOP, you are recreating

- Writing a filter/smoother class for head or gaze motion
- Writing a keyframe player, animation clip, or motion arbiter
- Authoring a new emotion from scratch
- `[VERIFY]` comments on SDK calls; "from memory"; "should be assumed wrong"
- The words "fallback", "if the spike fails", "in case reuse is insufficient"
  attached to code you are producing now

**All of these mean: delete the code, run the ladder, spike the official app.**

## Research: clone, survey, refresh

`reference/` is gitignored and holds full clones (not shallow or blobless —
history is how upstream changes are traced):

```sh
git clone https://github.com/pollen-robotics/reachy_mini reference/reachy_mini
git clone https://github.com/pollen-robotics/reachy_mini_conversation_app reference/reachy_mini_conversation_app
```

The notes are `docs/research-reachy-sdk.md` and
`docs/research-conversation-app.md`. Each title records the commit it maps.
Per subsystem they hold verified entry points with `file:line`, what is
reusable per POC feature, and gaps vs. POC requirements — a map, not a mirror.
Research from the cloned source, not web summaries: API signatures come from
code.

- **Survey** a subsystem not yet mapped: one Opus subagent per repo; the main
  session reviews and merges the reports into the notes. SDK scope: robot
  connection, camera, mic/speaker, face/head tracking, motion, robot state, app
  lifecycle. Conversation App scope: realtime integration, audio, turn
  handling, face tracking, camera, emotion moves, speech-reactive movement,
  movement arbitration, tool calling.
- **Refresh** when upstream has moved: `git -C reference/<repo> pull`, then
  `git log --oneline <note's commit>..HEAD` and `git diff <note's commit> HEAD
  -- <paths we touch>`. Classify each relevant change as client library (we
  can take it by bumping our dependency) or daemon (we cannot — app-only
  deploys), record it in the note's dated `## Upstream changes since baseline`
  section, and update the `Delta-refreshed … to <sha>` line under its title.
- Stop at the map; deep-dive per task later.
