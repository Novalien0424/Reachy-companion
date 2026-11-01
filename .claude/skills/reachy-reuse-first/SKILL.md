---
name: reachy-reuse-first
description: Use when about to implement, design, prototype or spike any robot-facing behavior for Reachy Mini (tracking, gaze, motion, animations, emotions, camera, audio, app lifecycle), including temporary, fallback or demo-deadline versions; and when official-repo knowledge is missing or stale (no `reference/` clones, a subsystem not yet mapped, upstream moved since the research notes, porting an upstream fix).
---

# Reachy Reuse First

The official Pollen Robotics code already solves the robot-specific problems,
and recreating it is this project's top failure mode (PRD § Rules). The ladder
and the never-recreate / ours-to-build lists are in CLAUDE.md § Reuse First.

## Before writing code

- **Name the module.** Name the official module you are reusing or adapting
  (its path in `reference/`), or record in the task file why nothing exists.
- **Trace every call.** Every SDK or app call in our code traces to a
  signature in the research notes or to a direct read of the cloned source.
- **Missing clones block the task.** If the clones are missing, or you can't
  verify what they provide, the deliverable is the research or spike plan
  plus the blocker, not code. SDK calls written from memory (with `[VERIFY]`
  markers) are the failure this skill exists to prevent.

## What the failure looks like

It usually arrives as reasonable prose:
- a "fallback in case the official app is insufficient";
- a smoothing or arbitration layer that "is genuinely ours" (the official app
  already ships layered motion and speech-reactive movement);
- a "small" new emotion animation;
- skipping inspection under a deadline. Running the official app is the
  fastest spike there is.

**Signs in code:**
- a filter or smoother class for head or gaze motion;
- a keyframe player or a motion arbiter;
- an emotion authored from scratch.

When you see one of these: remove that code, run the ladder, and spike the
official app.

## Research: clone, survey, refresh

`reference/` is gitignored and holds full clones. They are not shallow or
blobless, because history is how upstream changes are traced:

```sh
git clone https://github.com/pollen-robotics/reachy_mini reference/reachy_mini
git clone https://github.com/pollen-robotics/reachy_mini_conversation_app reference/reachy_mini_conversation_app
```

**The notes:**
- **Files:** `docs/research-reachy-sdk.md` and
  `docs/research-conversation-app.md`. Each title records the commit it maps.
- **Content, per subsystem:**
  - verified entry points with `file:line`;
  - what is reusable for each POC feature;
  - the gaps against the POC's requirements.
- **Rules:** the notes are a map, not a mirror. Research from the cloned
  source, not from web summaries, because API signatures come from code.

**Survey** a subsystem not yet mapped:
- **Who:** one Opus subagent per repo. The main session reviews the reports
  and merges them into the notes.
- **SDK scope:**
  - robot connection, robot state, app lifecycle;
  - camera, mic and speaker;
  - face and head tracking;
  - motion.
- **Conversation App scope:**
  - realtime integration, turn handling, tool calling;
  - audio and camera;
  - face tracking;
  - emotion moves, speech-reactive movement, movement arbitration.

**Refresh** when upstream has moved:
1. Run `git -C reference/<repo> pull`.
2. Run `git log --oneline <note's commit>..HEAD` and
   `git diff <note's commit> HEAD -- <paths we touch>`.
3. Classify each relevant change:
   - **client library:** we can take it by bumping our dependency;
   - **daemon:** we cannot, because deploys are app-only.
4. Record the change in the note's dated `## Upstream changes since baseline`
   section.
5. Update the `Delta-refreshed … to <sha>` line under the note's title.

Stop at the map; deep-dive per task later.
