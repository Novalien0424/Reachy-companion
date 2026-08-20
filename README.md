# Reachy Companion

Turn a [Reachy Mini Wireless](https://www.pollen-robotics.com/) into a
physically present AI companion. Reachy Companion connects the robot's
microphone, speaker and camera to OpenAI's `gpt-realtime-2.1` as a continuous
speech-to-speech session: it holds a natural Chinese-first conversation you can
interrupt mid-sentence, tracks your face while you talk, reacts with real
expressive movement, looks through its camera when you ask what it sees,
searches the live web on its own when a question needs today's information,
controls a device in your home, remembers what you tell it and who you are —
and does all of it in a pitched-up, cute robotic voice.

> **Status:** proof of concept, implemented and dev-verified. The five demo
> gates are pending live operator validation on the physical robot. See
> [docs/PRD.md](docs/PRD.md).

## Highlights

- **Realtime voice** — speech-to-speech on `gpt-realtime-2.1`, with turn
  detection tuned so a natural mid-sentence pause in Chinese does not cut you
  off, and barge-in so you can interrupt mid-answer.
- **Chinese-first persona** — a single locked personality: cheerful, concise,
  colloquial Chinese by default, following you if you switch languages. The
  persona text is editable on the robot: a `persona.md` next to `.env` in the
  instance directory replaces the built-in one at the next app start, so
  rewriting the character costs an antenna touch instead of a redeploy.
- **Embodiment** — face tracking runs from startup with no model involvement;
  emotion moves and speech-reactive motion layer over it and hand back to idle
  breathing automatically.
- **On-demand vision** — one camera frame, attached to the live conversation.
  No continuous video to the cloud.
- **Automatic web search** — invoked by the model when it decides fresh
  information is needed; you never have to say "search the web".
- **Home control** — natural-language commands against a Home Assistant
  allowlist you configure.
- **Cute robotic voice** — an on-device pitch shift that preserves speaking
  pace, plus a light ring modulation. Fully reversible.
- **Memory that survives redeploys** — remembered facts and enrolled faces live
  on the robot, inside the installed package. A reinstall would wipe them, so
  the deployment procedure backs both stores up before installing and restores
  them afterwards; that mandatory step is what makes them survive.
- **On-device face recognition** — enrol by name in conversation, get greeted by
  name at wake. Recognition frames and face signatures never leave the robot;
  what the cloud model gets is a status, a face count, the matched name, a
  rounded similarity score, a runner-up name only on an ambiguous answer, and a
  reason code from a closed set. The camera tool is a separate, explicitly
  requested path that does send one frame to the model.
- **Runs as a managed app on the robot** — installed under the robot's own
  daemon and set as the startup app, so an antenna touch wakes the whole
  experience. No laptop, no dashboard.

## Behavior notes

Two properties of this build are deliberate and accepted for a home-network
proof of concept rather than defects to file:

- **The local console and control channel are unauthenticated.** The app serves
  a web console and a JSON-RPC control channel on the robot, reachable from any
  device on the same network, with no password or token. Anyone who can reach it
  can make Reachy speak, interrupt it, mute the microphone and change settings.
  Fine on a trusted home LAN; not something to expose beyond one.
- **Reachy moves on its own, and eventually sleeps.** After about three minutes
  with no conversation it plays a spontaneous dance, emotion or head turn — a
  personality choice, not a bug. After 24 hours of inactivity it returns to the
  sleep pose and shuts the app down.

## Repository layout

| Path                 | What it is                                                          |
| -------------------- | ------------------------------------------------------------------- |
| `reachy_companion/`  | The application — a fork of Pollen Robotics' official conversation app, adapted in place |
| `docs/`              | [PRD](docs/PRD.md), SDK and conversation-app research notes, and the [adding-a-skill guide](docs/adding-a-skill.md) |
| `scripts/`           | Development daemon launcher, development app runner, asset preloader, SDK smoke test |
| `.claude/skills/`    | Project automation skills — deployment, research, and the reuse-first checklist |
| `reference/`         | Read-only clones of the official Pollen Robotics repos (gitignored, never committed) |
| `progress.md`        | Current verified state, known defects, open operator items          |
| `DECISIONS.md`       | Durable implementation decisions (D-001 … D-014)                    |
| `feature_list.json`  | The five demo gates and per-feature verification evidence           |

## How it's built

Reuse first. The Reachy Mini SDK and the official Conversation App already
solve the hard robot-side problems — face tracking, gaze smoothing, camera and
audio access, motion primitives, motion arbitration, the emotion library, app
lifecycle — and none of that is reimplemented here. The app is a scaffolded copy
of the official conversation app, adapted in place, so upstream fixes stay easy
to port.

What *is* custom is the part that is genuinely different: the `gpt-realtime-2.1`
backend and its turn handling, the VoiceFX chain that gives Reachy its voice,
the local tools and Skills pattern, the MCP configuration seam, and the fact and
face memories.

## Quickstart (development, no robot required)

**Prerequisites**

- Python 3.11+ and [uv](https://docs.astral.sh/uv/)
- An OpenAI API key with Realtime API access
- A working microphone and webcam on the development machine

**Setup**

```powershell
uv venv
uv pip install -e reachy_companion
copy reachy_companion\.env.example reachy_companion\.env
# then put your key in OPENAI_API_KEY=
```

**Run**

```powershell
# terminal 1 — simulated daemon (real kinematics, no physics, local webcam/mic)
scripts\dev_daemon.ps1

# terminal 2 — the app, against that daemon
scripts\run_app_dev.ps1
```

**Test**

```powershell
cd reachy_companion
..\.venv\Scripts\python.exe -m pytest -q
```

The suite is green; a fixed set of upstream tests is skipped on purpose (they
assume a user-switchable profile, which this app deliberately locks).

Two things the simulator cannot rehearse and that need the physical robot: live
face tracking of a real person, and any camera scene that requires a properly
selected, lit camera.

## Deployment

The app is built as a single wheel, copied to the robot over SSH and installed
into the shared managed apps environment; the daemon's own official APIs cover
discovery, start/stop and registering the app as the startup app, so an antenna
touch on the sleeping robot brings the whole experience up. The daemon's code
and configuration are out of scope for deployment — the one exception on record
is a one-time authorised update of the daemon to the required version line
during bring-up, through the robot's own updater. Configuration plus both
persistent stores are backed up and restored around every install.

The full procedure — version gate, two-step install, `.env` and store
backup/restore, asset preload, verification, and how to leave the robot asleep —
lives in [`.claude/skills/reachy-deploy/SKILL.md`](.claude/skills/reachy-deploy/SKILL.md).
Robot connection details are read from a gitignored environment file and are not
in this repository.

## Configuration

All settings live in `reachy_companion/.env` (start from
`reachy_companion/.env.example`). Placeholders only below — never commit real
values.

| Key                                | Meaning                                                        |
| ---------------------------------- | -------------------------------------------------------------- |
| `OPENAI_API_KEY`                   | API key for the `gpt-realtime-2.1` backend. Required.          |
| `PERSONA_FILE`                     | Absolute path to the persona override, if not the default `persona.md` beside `.env`. Same fallback rules. |
| `REALTIME_TRANSCRIPTION_LANGUAGE`  | Input transcription language. Defaults to `zh`.                |
| `REALTIME_VAD_TYPE`                | Turn detection: `server_vad` (default) or `semantic_vad`.      |
| `REALTIME_VAD_SILENCE_DURATION_MS` | Silence before Reachy takes its turn. Default `800`, raised from the API default so Chinese mid-sentence pauses do not cut you off. |
| `REALTIME_VAD_THRESHOLD`           | Speech activation threshold, `0.0`–`1.0`. Raise it in a noisy room. |
| `REALTIME_VAD_PREFIX_PADDING_MS`   | Audio retained from before speech was detected.                |
| `VOICEFX_ENABLED`                  | Master switch for the character voice. Off leaves the audio path untouched. |
| `VOICEFX_PITCH_SEMITONES`          | Upward pitch shift, duration preserved. Typical `4.0`.         |
| `VOICEFX_RINGMOD_HZ`               | Ring-modulator carrier frequency; `0` disables it.             |
| `VOICEFX_RINGMOD_MIX`              | Ring-modulator wet/dry blend, `0.0`–`1.0`.                     |
| `VOICEFX_GAIN_DB`                  | Makeup gain applied last, to recover the loudness the chain costs. |
| `FACE_MEMORY_ENABLED`              | Master switch for face memory. Off loads no model and skips every check. |
| `FACE_AUTO_GREET`                  | Keep the tools but drop the automatic wake-time look when `0`.  |
| `FACE_WAKE_BUDGET_MS`              | Time budget for the wake-time recognition check; an overrun greets normally, on time. |
| `FACE_MATCH_THRESHOLD`             | Similarity a match must clear. Conservative by default.         |
| `FACE_MATCH_MARGIN`                | Lead the best match needs over the runner-up; inside it, the answer is `ambiguous`, never a guess. |
| `NOTION_MCP_URL` / `NOTION_MCP_TOKEN` | Remote MCP endpoint and bearer token for the Notion integration. |
| `HA_URL` / `HA_TOKEN`              | Home Assistant base URL and long-lived access token.            |
| `HA_ENTITIES`                      | JSON map of spoken names to entity ids — the **only** devices the model may target. |

## Status

Proof of concept, implemented. All five demo gates are pending live operator
validation on the robot; the MCP and home-control integrations are additionally
pending credentials. Requirements, journeys and the as-built architecture are in
[docs/PRD.md](docs/PRD.md); current verified state and open items are in
[progress.md](progress.md).

## License and credit

Derived from Pollen Robotics'
[Reachy Mini Conversation App](https://github.com/pollen-robotics/reachy_mini_conversation_app),
licensed under Apache-2.0, and built on the
[Reachy Mini SDK](https://github.com/pollen-robotics/reachy_mini). See
`reachy_companion/LICENSE`. The original upstream README is preserved at
`reachy_companion/README_OLD.md`.
