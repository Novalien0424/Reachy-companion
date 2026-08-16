---
name: reachy-reuse-first
description: Use when about to implement, design, prototype, or "spike" any robot-facing behavior for Reachy Mini — face/head tracking, gaze smoothing, motion, animations, emotions, camera, audio, app lifecycle — including temporary, fallback, or demo-deadline versions.
---

# Reachy Reuse First

## Overview

The official Pollen Robotics code already solves the robot-specific problems.
Recreating it is the project's #1 named failure mode (PRD §11). **Verbal
agreement with this rule while still writing the code is a violation** — the
baseline failure this skill exists to stop is an agent that recommends reuse,
then hand-rolls the forbidden layer anyway "just in case."

## The Ladder

Before writing any robot-facing code, in order:

1. Check the Reachy Mini SDK (`reference/reachy_mini`).
2. Check the official Conversation App (`reference/reachy_mini_conversation_app`).
3. Adapt existing code only where POC requirements actually differ.
4. Write new code only when steps 1–3 verifiably found nothing.

**Compliance gate:** before implementing, name the official module you
inspected (file path in the cloned repo) that you are reusing or adapting — or
record in the task file why nothing exists. No named module → not past step 1.

If the repos are not cloned or you cannot verify what they provide, **stop:
your deliverable is the research/spike plan, not code**. Use the
`reachy-research` skill. Writing SDK calls from memory with `[VERIFY]` markers
is not a workaround; it is the violation.

## Never Recreate

Face tracking · gaze/jitter smoothing and filtering · camera access · motion
primitives · motion layering/arbitration · emotion animations ·
speech-reactive movements · audio I/O. The official app already ships layered
motion, head tracking, and emotion moves.

## Do Build (the actually-different parts)

`gpt-realtime-2.1` integration · conversation/turn behavior · web search tool
· MCP wiring · Skills pattern · home control · thin glue adapting official
APIs.

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
