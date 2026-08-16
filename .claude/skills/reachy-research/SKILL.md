---
name: reachy-research
description: Use when official Reachy Mini repo knowledge is missing — before the first implementation work in any subsystem, when docs/research-reachy-sdk.md or docs/research-conversation-app.md do not exist, or when about to call an SDK/Conversation-App API whose signature has not been verified against source.
---

# Reachy Research

## Overview

PRD §2 makes studying the official repos **mandatory pre-development
research**. The output is two research notes files; robot-facing
implementation is blocked until they exist. Never write SDK-dependent code
from memory — the baseline failure is unverified `[VERIFY]`-marked code that
"should be assumed wrong."

## Workflow

1. **Clone the references** (into `reference/`, which stays out of git):

   ```powershell
   git clone https://github.com/pollen-robotics/reachy_mini reference/reachy_mini
   git clone https://github.com/pollen-robotics/reachy_mini_conversation_app reference/reachy_mini_conversation_app
   ```

2. **Survey via Opus subagents** (per CLAUDE.md orchestration — the main
   session dispatches and reviews; one subagent per repo):
   - SDK: robot connection, camera, mic/speaker, face/head tracking, motion,
     robot state, app lifecycle.
   - Conversation App: realtime-API integration, audio handling, turn
     handling, face tracking, camera usage, emotion moves, speech-reactive
     movement, movement arbitration, tool calling.

3. **Record findings** in `docs/research-reachy-sdk.md` and
   `docs/research-conversation-app.md`. For each subsystem: verified API
   entry points with `file:line` references, what is reusable per POC
   feature, actual gaps vs. POC requirements. Timebox: notes ≤ ~200 lines per
   file — a map, not a mirror.

4. **Only then implement.** Every SDK/app call in our code must trace to a
   verified signature in the notes (or a direct read of the cloned source).

## Quick Reference — where to look first

| POC need | Look in |
|---|---|
| Tracking, motion, camera, audio I/O, robot state | SDK (`reference/reachy_mini`) |
| Realtime voice, turn handling, emotions, arbitration, tool calls | Conversation App (`reference/reachy_mini_conversation_app`) |
| Web search, MCP, Skills, home control | Neither — this is ours to build |

## Common Mistakes

- Writing code with `[VERIFY]` markers instead of reading the source →
  blocked research means the task is blocked; clone and read, or report the
  blocker.
- Researching via web search summaries instead of the cloned source → API
  signatures come from code, not blog posts.
- Unbounded archaeology → the notes exist to unblock implementation; stop at
  the map, deep-dive per-task later.
- Doing the survey in the main session → dispatch Opus subagents; the main
  session reviews and merges their reports into the notes.
