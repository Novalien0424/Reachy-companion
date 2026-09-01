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

### Editing the persona on the robot

`profile.md` ships inside the wheel, so changing it means a redeploy. To change
the character without one, drop a `persona.md` into the **instance directory** —
the same directory that holds `.env`, `memory.v1.json` and `faces.v1.json`, which
on the robot is the installed package directory (D-016).

It uses the same format and the same parser as `profile.md`, with everything
optional: front matter may set any of `voice`, `greeting` and `default_tools`,
and the markdown body is the persona text. Whatever the file omits keeps the
built-in value, so a file that is nothing but persona text is valid. `PERSONA_FILE`
(absolute path) points the loader somewhere else.

The file is read when the app starts — an antenna wake starts it fresh — so the
edit-restart-listen loop is the whole workflow. Nothing is ever half-applied: a
missing file, unreadable file, bad TOML, unknown key or empty body all fall back
to the built-in profile whole, the problem is logged as a WARNING, and one INFO
line at startup names the source in use (`persona: instance persona.md` or
`persona: built-in locked profile`). Like `.env`, this is user state that a
reinstall would wipe — the deploy skill backs it up and restores it around every
install.

## Tools

Since v1.19.0 the model sees a static core surface (22 tools: vision,
motion incl. `look_around`, faces, memory, home control, music, search,
conversation modes, sleep, waiting, system) plus two on-demand toolboxes
loaded by the model itself via `open_toolbox` — `productivity`
(calendar/tasks/drive/notion/email families) and `media` (tv/nas). The
authoritative lists live in `src/reachy_companion/toolboxes.py`; the
startup journal logs the live surface as `Tools in session (<mode>,
boxes=…, <count>)`.

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
replaces — the deploy skill backs up and restores `.env`, `persona.md`,
`memory.v1.json` and `faces.v1.json` around every install. `PERSONA_FILE` is
documented above, under the persona override.

| Variable | Default | Purpose |
| -------- | ------- | ------- |
| `INSTRUCTING_FINISH_SESSION_ALIAS` | `0` | Exposes `finish_session` as a second name for `go_to_sleep` for a measured rename A/B only. |

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
