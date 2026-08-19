---
title: Reachy Companion
emoji: 🤖
colorFrom: purple
colorTo: gray
sdk: static
pinned: false
tags:
  - reachy_mini
  - reachy_mini_python_app
---

# Reachy Companion

A Reachy Mini application that turns the robot into a physically present AI
companion: realtime Chinese-first voice conversation on `gpt-realtime-2.1` with
barge-in, face tracking, expressive motion, on-demand camera vision, automatic
web search, home control, persistent fact and face memory, and a cute robotic
voice produced on-device.

Product requirements and architecture: [`../docs/PRD.md`](../docs/PRD.md).

## Locked single-persona profile

The app ships one personality. `config.LOCKED_PROFILE` points at
`profiles/_reachy_companion_locked_profile/profile.md` and overrides
`REACHY_MINI_CUSTOM_PROFILE`, so that file is authoritative. Its TOML front
matter carries the `default_tools` list, the `voice` (`cedar`) and the
`greeting` — which is injected as a synthetic user turn, so it is phrased as an
instruction *to* Reachy, not words spoken *as* Reachy. Its body carries the
Chinese persona text and the behaviour rules that tell the model when to reach
for each tool.

## Tools

Seventeen tools reach the model: the fifteen listed in `default_tools` plus two
system tools for asynchronous work.

| Category            | Tools                                                             |
| ------------------- | ----------------------------------------------------------------- |
| Expression & motion | `play_emotion`, `dance`, `stop_emotion`, `stop_dance`, `move_head`, `head_tracking`, `sweep_look` |
| Vision              | `camera`                                                          |
| Knowledge           | `pollen_robotics_reachy_mini_search_tool__search_web`             |
| Home                | `home_control`                                                    |
| Memory              | `remember`, `forget`                                              |
| Face memory         | `remember_face`, `who_is_this`                                    |
| Lifecycle           | `go_to_sleep`                                                     |
| System              | `task_status`, `task_cancel`                                      |

## Adding a tool or Skill

One new file plus one line in the profile — the conversational core is never
touched. Create `src/reachy_companion/tools/<name>.py` with a `Tool` subclass
whose `name` matches the filename, then add that name to `default_tools`. Tools
must never raise: return an error payload the model can recover from. Restart
the app; the registry is not hot-loaded.

Full pattern, loader rules and verification steps:
[`../docs/adding-a-skill.md`](../docs/adding-a-skill.md). A Skill that already
exists as a remote MCP server needs no Python file at all — for the one
preconfigured server slot (`notion`), filling in its URL and token env vars is
the whole integration, and its tools are discovered and namespaced at startup.
A second server is one more tuple in `mcp_servers._SERVER_ENV` plus its two env
vars; still nothing in the conversational core.

## Configuration

Copy `.env.example` to `.env` in the package instance directory and fill it in.
`OPENAI_API_KEY` is the only required key; the rest tune turn detection, the
VoiceFX chain, face memory, and the Notion and Home Assistant integrations, and
each is documented inline. Never commit a filled-in `.env`. On the robot the
instance directory is the installed package directory, which a reinstall
replaces — the deploy skill backs up and restores `.env`, `memory.v1.json` and
`faces.v1.json` around every install.

## Tests

Run from `reachy_companion/`, using the project-root virtualenv (a bare `python`
picks up Anaconda and fails collection):

```powershell
..\.venv\Scripts\python.exe -m pytest -q
```

The baseline is green. `tests/conftest.py` skips an explicit list of upstream
tests that assume an unlocked, user-switchable profile — those skips are
intentional and incompatible with `LOCKED_PROFILE` by design. Anything else red
is a real regression.

## Origin

This package is a fork of Pollen Robotics' official Reachy Mini Conversation App
(Apache-2.0, see `LICENSE`), produced with the SDK scaffolder and adapted in
place. The original upstream README is preserved as `README_OLD.md`; the
read-only upstream clone under `../reference/` is the diff baseline.
