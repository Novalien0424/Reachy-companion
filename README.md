# Reachy Companion

Reachy Companion turns a [Reachy Mini Wireless](https://www.pollen-robotics.com/)
into a household companion that feels present in the room. It connects the
robot's microphone, speaker and camera to OpenAI's `gpt-realtime-2.1-mini` as
one continuous speech-to-speech session.

What it does:
- It holds a natural, Chinese-first conversation.
- It knows who it is talking to, and remembers what they told it last time.
- It plays music and the family's videos.
- It controls the home.
- It behaves like someone in the room: it answers when you say its name, and
  stays out of conversations that aren't for it.

> **Version:** 1.38.0, the forty-sixth on-robot install. The minor number is
> the install that shipped it; see [CHANGELOG.md](CHANGELOG.md).
> **Status:** a proof of concept, live on the robot in daily family use. The
> formal in-room run of the five demos is pending. Each feature's evidence is
> in `feature_list.json`.

What Reachy can do, the five demos it must pass, and the rules it is built
under are in **[docs/PRD.md](docs/PRD.md)**.

## Highlights

- **Realtime voice with human turn-taking.** Speech to speech, tuned so that
  a Mandarin pause mid-sentence doesn't cut you off.
  - **Interrupting:** a cough or someone else's sentence doesn't interrupt
    Reachy, but its name or a stop phrase (停, stop) does.
  - **Cut-off replies:** an interrupted reply is truncated server-side too, so
    Reachy never believes it finished a sentence you never heard.
- **It knows who it is talking to.** Face recognition runs on the robot.
  - **Enrolling:** say 「記住我」 in conversation, and Reachy greets you by
    name at wake.
  - **Your facts:** what you told it comes back into the greeting
    (「上次你說要考試，後來呢？」).
  - **Privacy:** recognition frames and face signatures never leave the robot.
    Only an explicitly requested picture does.
- **Memory with a life cycle.**
  - Facts are filed per person while that person is recognized.
  - Going to sleep leaves a one-line 「上次聊天」 note for each person
    recognized.
  - Everything survives redeploys.
  - A Mac-side backend (`companion_backend/`) manages people, photos and
    facts, and pushes them to the robot.
- **A short tool belt.** Twenty tools, all loaded at the start of every turn.
  The highlights:
  - Home Assistant control, limited to an allowlist;
  - music on the robot's own speaker;
  - the family's NAS videos, YouTube, and AI drawings on the TV;
  - Google Drive, and email, which reads the whole message back before
    sending;
  - web search and the time, from Pollen's MCP tool Spaces.
- **A character, not an assistant.**
  - **Persona:** a single locked Chinese-first persona. It is editable on the
    robot as `persona.md` without a redeploy.
  - **Voice:** a pitched-up, comb-resonated robot voice.
  - **Movement:** emotion moves and speech-reactive motion, layered over
    always-on face tracking.
  - **Reply length:** follows the question.
- **Runs as a managed app.** It is installed under the robot's own daemon as
  the startup app, and an antenna touch wakes it. No laptop and no dashboard
  are needed.

## Conversation modes

Switch modes by saying so: one sentence, no restart.

| Mode | Say | What Reachy does |
|---|---|---|
| 多人聊天模式 `group` | 「切到多人聊天模式」 | Several people: answers only when named, or inside a 20-second follow-up window after an answered turn. Room talk neither gets a reply nor interrupts it. |
| 一對一聊天模式 `one_on_one` | 「切到一對一聊天模式」 | Just the two of you: answers anything substantive, no name needed. |
| 紀錄模式 `record` | 「進入紀錄模式」 | A meeting: listens silently and notes every line; 「瑞奇幫我總結」 reads back a summary. |

- **Boot mode:** Reachy boots into 多人聊天模式 (`REALTIME_DEFAULT_MODE`).
- **Video on the TV:** while a video Reachy started is playing, it answers
  only when named or given a control phrase.
- **紀錄模式 notes:** kept in memory only, never written to disk. They are
  cleared when the mode is left, and again when Reachy goes to sleep.

## Tools

| Area | Tools |
|---|---|
| Body | `play_emotion`, `dance` (play, stop), `head` (turn, follow, stop following) |
| Seeing | `camera`, `look_around` (turn first, then look), `face` (identify, remember) |
| Memory | `memory` (save, forget) |
| Home and media | `home_control`, `music` (play, pause, resume, stop), `nas`, `youtube`, `draw_picture` |
| Cloud | `drive` (list, trash, upload a photo), `email_send` |
| Knowledge | `search_web`, `get_time` (Pollen's MCP tool Spaces) |
| Conversation | `set_conversation_mode`, `summarize_conversation`, `wait_for_user`, `go_to_sleep` |

**Choosing a video tool.** The video tools are split by where a video comes
from, because that is what people name:
- `nas` is the family's 影片庫. A trip or place video plays from it first, in
  one call.
- `youtube` is everything public. After a 影片庫 miss, Reachy asks before
  searching YouTube.

**Confirmations.** Irreversible actions (email, moving a file to the trash)
need a read-back and a spoken yes, enforced in code. `docs/adding-a-skill.md`
shows how to add a tool.

## Behavior notes

Two properties are deliberate for a home-network proof of concept, not
defects:

- **The local console and control channel are unauthenticated.** The app
  serves a web console and a JSON-RPC channel on the robot, with no password.
  Anyone on the same network can make Reachy speak, interrupt it, mute it, or
  change settings.
  - This is fine on a trusted home LAN, but don't expose it beyond one.
  - The Mac backend has no auth either. It binds to localhost, or to the
    tailnet by operator choice.
- **Reachy moves on its own, and eventually sleeps.** After three minutes
  without conversation, it plays a spontaneous dance, emotion or head turn.
  After 24 hours of inactivity it returns to the sleep pose and stops the
  app.

## Repository layout

| Path | What it is |
|---|---|
| `reachy_companion/` | The application: a fork of Pollen Robotics' official Conversation App, adapted in place |
| `companion_backend/` | Mac-side FastAPI app for people, photos and facts, with guarded push and import to the robot |
| `docs/` | The [PRD](docs/PRD.md), research notes (SDK, Conversation App, realtime API), plans, and the [adding-a-skill guide](docs/adding-a-skill.md) |
| `scripts/` | Dev daemon and app runners (PowerShell), the asset preloader, an SDK smoke test, a voice audition tool |
| `.claude/skills/` | Project skills: deploying to the robot, instructing the realtime model, reuse-first |
| `reference/` | Read-only clones of the official Pollen repos (gitignored) |
| `persona.md` | The version-controlled copy of the on-robot persona |
| `CHANGELOG.md` | Release notes; versions map to on-robot installs |
| `progress.md` | Current verified state, known defects, open operator items |
| `DECISIONS.md` | Durable decisions, D-001 … D-048 |
| `feature_list.json` | The demo gates and per-feature verification evidence |

## How it's built

**Reuse first.** The Reachy Mini SDK and the official Conversation App already
solve the hard robot-side problems, and none of them is reimplemented here:
- face tracking and gaze smoothing;
- camera and audio access;
- motion primitives and motion arbitration;
- the emotion library;
- the app lifecycle.

The app is a scaffolded copy of the Conversation App, adapted in place, so
upstream fixes stay easy to port.

**What is custom:**
- the realtime backend and its turn handling, including the name-gated
  barge-in;
- the voice filter;
- the tools and the Skills pattern;
- the MCP seam;
- the memories of facts, people and faces.

The model decides what to say and which tool to call. Code holds only the
safety rails, the timing, and the truth about the physical state.

## Quickstart (development, no robot)

**You need:**
- Python 3.12 and [uv](https://docs.astral.sh/uv/);
- an OpenAI API key with Realtime API access;
- a microphone and webcam on the development machine.

```sh
cd reachy_companion
uv sync                       # creates .venv with the dev tools
cp .env.example .env          # then set OPENAI_API_KEY=

# terminal 1: the simulated daemon (real kinematics, no physics, local webcam and mic)
.venv/bin/reachy-mini-daemon --mockup-sim --fastapi-port 8001

# terminal 2: the app, against that daemon
REACHY_DAEMON_PORT=8001 .venv/bin/reachy-companion --ui
```

On Windows, `scripts/dev_daemon.ps1` and `scripts/run_app_dev.ps1` do the
same.

**Tests**, from `reachy_companion/`: `.venv/bin/python -m pytest -q`, which
gives 2287 passed and 31 skipped. The skips are 30 upstream tests that assume a
user-switchable profile (listed in `tests/conftest.py`), plus one SFace test
when that model isn't cached. Lint with `.venv/bin/ruff check .`, and check
types with `.venv/bin/mypy`.

**What needs the real robot:** the simulator can't rehearse live face
tracking of a real person, or a properly lit camera scene.

## Deployment

```sh
bash .claude/skills/reachy-deploy/scripts/deploy.sh
```

The script builds the wheel, checks the robot's daemon version, backs up the
instance state, installs in two steps, and verifies the install. The app is
installed as a managed app under the robot's own daemon, and is started and
stopped only through the daemon's API. The daemon itself changes only through
its official updater, under a recorded authorization.

Instance state lives outside the package, so a reinstall doesn't touch it:
- the configuration;
- the persona;
- memory, people and faces.

The full procedure, including live testing, is in
[`.claude/skills/reachy-deploy/SKILL.md`](.claude/skills/reachy-deploy/SKILL.md).
Robot connection details are read from a gitignored `.env`.

## Configuration

Settings live in `reachy_companion/.env`. Start from
`reachy_companion/.env.example`, which documents every key. The main keys:

| Key | Meaning |
|---|---|
| `OPENAI_API_KEY` | Required. |
| `REALTIME_MODEL` | Default `gpt-realtime-2.1-mini`. |
| `PERSONA_FILE` | Persona override, if not `persona.md` beside `.env`. |
| `REALTIME_DEFAULT_MODE` | The boot mode: `group` (default), `one_on_one` or `record`. |
| `REALTIME_VAD_TYPE` | Turn detection: `server_vad` (default) or `semantic_vad`. |
| `REALTIME_VAD_SILENCE_DURATION_MS` | Silence before Reachy takes its turn. Default `1000`, so a Mandarin pause mid-sentence doesn't end the turn. |
| `REALTIME_PARTY_FOLLOWUP_S` | The follow-up window in group mode after an answered turn. Default `20`. |
| `REALTIME_REASONING_EFFORT` | Session `reasoning.effort`. Default `low`; `off` omits it. |
| `REALTIME_MAX_OUTPUT_TOKENS` | A ceiling against runaway replies, not a brevity knob. Default and maximum `4096`. |
| `VOICEFX_ENABLED`, `VOICEFX_PITCH_SEMITONES` | The character voice. Off by default; the pitch default is `5.0`. |
| `FACE_MEMORY_ENABLED`, `FACE_WAKE_BUDGET_MS` | Face memory, and the wake-time check's budget (default `4000`). |
| `MEMORY_LAST_CHAT_MODEL` | The model that writes the sleep-time visit note. Default `gpt-5-mini`. |
| `HA_URL`, `HA_TOKEN`, `HA_ENTITIES` | Home Assistant. `HA_ENTITIES` maps spoken names to entity IDs, and is the only set of devices the model may touch. |
| `HANOVA_*`, `HERMES_DRIVE_SECRETS` | Music, TV casting, the NAS, email, Drive, and the home-network check. Each tool reports itself unavailable when its keys are missing. |

Commit placeholders only, never real values.

## License and credit

Derived from Pollen Robotics'
[Reachy Mini Conversation App](https://github.com/pollen-robotics/reachy_mini_conversation_app),
licensed under Apache-2.0, and built on the
[Reachy Mini SDK](https://github.com/pollen-robotics/reachy_mini). See
`reachy_companion/LICENSE`. The original upstream README is preserved at
`reachy_companion/README_OLD.md`.
