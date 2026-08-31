# Reachy Companion

Turn a [Reachy Mini Wireless](https://www.pollen-robotics.com/) into a
physically present AI companion. Reachy Companion connects the robot's
microphone, speaker and camera to OpenAI's `gpt-realtime-2.1` as a continuous
speech-to-speech session: it holds a natural Chinese-first conversation, knows
who it's talking to, remembers what you told it last time, plays your music,
puts videos on your TV, manages your calendar and mail by voice, controls your
home — and behaves like someone actually in the room: it yields the floor when
you say its name, and talks on through conversation that isn't for it.

> **Version:** 1.17.0 (the seventeenth on-robot install — versions track
> installs; see [CHANGELOG.md](CHANGELOG.md)).
> **Status:** proof of concept, live on the robot in daily family use. The
> five formal demo gates of [docs/PRD.md](docs/PRD.md) are pending scripted
> operator validation; per-feature evidence lives in `feature_list.json`.

## Highlights

- **Realtime voice, human turn-taking** — speech-to-speech on
  `gpt-realtime-2.1`, tuned so a Mandarin mid-sentence pause doesn't cut you
  off. Barge-in is **name-gated**: while Reachy is talking, 「瑞奇」 or a stop
  phrase (停/stop — those always win) takes the floor; a cough or somebody
  else's sentence just pauses the reply for a beat before it carries on.
  Interrupted replies are truncated server-side too, so Reachy never believes
  it finished a sentence you never heard.
- **Knows who it's talking to** — on-device face recognition: enrol by name in
  conversation (「記住我」), get greeted by name at wake, with per-person
  remembered facts woven in (「上次你說要考試，後來呢？」). Recognition frames
  and face signatures never leave the robot; the cloud model only ever gets a
  name, a score and a reason code. The camera tool is a separate, explicitly
  requested path that does send one frame.
- **Memory with a life cycle** — facts land per person while you're
  recognized, a one-line 「上次聊天」 summary is written when you send Reachy
  to sleep by voice, and everything survives redeploys through the
  deployment ritual's mandatory backup/restore. A Mac-side backend
  (`companion_backend/`) manages people, photos and facts and pushes them to
  the robot.
- **A real tool belt** — 41 tools live on-device: Home Assistant control
  against an allowlist you configure, music playback from the robot's own
  speaker, YouTube and home-video (NAS) casting to the TV, Google Calendar /
  Tasks / Drive / Gmail (with read-back confirmation gates before anything
  sends), Notion notes, and automatic web search the model invokes on its own.
- **A character, not an assistant** — a single locked Chinese-first persona,
  editable on the robot as `persona.md` (rewriting the character costs an
  antenna touch, not a redeploy); a pitched-up comb-resonator robot voice;
  emotion moves and speech-reactive motion layered over always-on face
  tracking; spontaneous idle behavior. Replies are length-calibrated —
  one-liners for one-line questions, real explanations when warranted, no
  「讓我想想」 filler.
- **Plays well with a full room** — party mode answers only speech addressed
  to it (by name, follow-up window, or an engaged face) and stays quiet
  through the rest; solo mode's `wait_for_user` discipline keeps it from
  reacting to TV audio and side talk.
- **Runs as a managed app on the robot** — installed under the robot's own
  daemon, registered as the startup app: an antenna touch wakes the whole
  experience. No laptop, no dashboard.

## Behavior notes

Two properties of this build are deliberate and accepted for a home-network
proof of concept rather than defects to file:

- **The local console and control channel are unauthenticated.** The app serves
  a web console and a JSON-RPC control channel on the robot, reachable from any
  device on the same network, with no password or token. Anyone who can reach it
  can make Reachy speak, interrupt it, mute the microphone and change settings.
  Fine on a trusted home LAN; not something to expose beyond one. The Mac
  backend shares the same trusted-network posture (tailnet-bound by operator
  authorization).
- **Reachy moves on its own, and eventually sleeps.** After about three minutes
  with no conversation it plays a spontaneous dance, emotion or head turn — a
  personality choice, not a bug. After 24 hours of inactivity it returns to the
  sleep pose and shuts the app down.

## Repository layout

| Path                 | What it is                                                          |
| -------------------- | ------------------------------------------------------------------- |
| `reachy_companion/`  | The application — a fork of Pollen Robotics' official conversation app, adapted in place |
| `companion_backend/` | Mac-side FastAPI app for managing people, photos and facts, with guarded push/import/merge to the robot |
| `docs/`              | [PRD](docs/PRD.md), research notes (SDK, conversation app, realtime API), the [human-like-conversation summary](docs/human-like-conversation.md), executed plans, and the [adding-a-skill guide](docs/adding-a-skill.md) |
| `scripts/`           | Development daemon launcher, development app runner, asset preloader, SDK smoke test |
| `.claude/skills/`    | Project automation skills — deployment, research, and the reuse-first checklist |
| `reference/`         | Read-only clones of the official Pollen Robotics repos (gitignored, never committed) |
| `persona.md`         | The version-controlled working copy of the on-robot persona (synced by the deploy ritual) |
| `CHANGELOG.md`       | Release notes; versions map to on-robot installs                    |
| `progress.md`        | Current verified state, known defects, open operator items          |
| `DECISIONS.md`       | Durable implementation decisions (D-001 … D-028)                    |
| `feature_list.json`  | The demo gates and per-feature verification evidence                |

## How it's built

Reuse first. The Reachy Mini SDK and the official Conversation App already
solve the hard robot-side problems — face tracking, gaze smoothing, camera and
audio access, motion primitives, motion arbitration, the emotion library, app
lifecycle — and none of that is reimplemented here. The app is a scaffolded copy
of the official conversation app, adapted in place, so upstream fixes stay easy
to port.

What *is* custom is the part that is genuinely different: the `gpt-realtime-2.1`
backend and its turn handling (including the client-side name-gated barge-in
machine), the VoiceFX chain that gives Reachy its voice, the local tools and
Skills pattern, the MCP configuration seam, and the fact, person and face
memories.

## Quickstart (development, no robot required)

**Prerequisites**

- Python 3.12 and [uv](https://docs.astral.sh/uv/)
- An OpenAI API key with Realtime API access
- A working microphone and webcam on the development machine

**Setup (macOS/Linux)**

```sh
uv venv
uv pip install -e reachy_companion
cp reachy_companion/.env.example reachy_companion/.env
# then put your key in OPENAI_API_KEY=
```

(On Windows, the same three steps with `copy` and backslashes; the dev helper
scripts in `scripts/` ship as PowerShell.)

**Run**

```sh
# terminal 1 — simulated daemon (real kinematics, no physics, local webcam/mic)
scripts/dev_daemon.ps1        # Windows; on macOS run its python command directly

# terminal 2 — the app, against that daemon
scripts/run_app_dev.ps1
```

**Test**

```sh
cd reachy_companion
.venv/bin/python -m pytest -q
```

The suite is green (1571 passed / 30 skipped as of 1.17.0); the fixed set of
upstream skips is documented in `tests/conftest.py` (they assume a
user-switchable profile, which this app deliberately locks).

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
during bring-up, through the robot's own updater. Configuration, the persona,
and every persistent store are backed up and restored around every install via
a manifest-driven ritual.

The full procedure — version gate, two-step install, backup/restore, asset
preload, verification, and how to leave the robot asleep —
lives in [`.claude/skills/reachy-deploy/SKILL.md`](.claude/skills/reachy-deploy/SKILL.md).
Robot connection details are read from a gitignored environment file and are not
in this repository.

## Configuration

All settings live in `reachy_companion/.env` (start from
`reachy_companion/.env.example`, which documents every knob). Placeholders only
below — never commit real values.

| Key                                | Meaning                                                        |
| ---------------------------------- | -------------------------------------------------------------- |
| `OPENAI_API_KEY`                   | API key for the `gpt-realtime-2.1` backend. Required.          |
| `PERSONA_FILE`                     | Absolute path to the persona override, if not the default `persona.md` beside `.env`. Same fallback rules. |
| `REALTIME_TRANSCRIPTION_LANGUAGE`  | Input transcription language. Defaults to `zh`.                |
| `REALTIME_VAD_TYPE`                | Turn detection: `server_vad` (default) or `semantic_vad`.      |
| `REALTIME_VAD_SILENCE_DURATION_MS` | Silence before Reachy takes its turn. Default `1000` (was `800`; the API's own is `500`) so a Mandarin mid-sentence pause does not commit the turn. Past ~`1100` it feels sluggish rather than patient. Ignored under `semantic_vad`. |
| `REALTIME_VAD_THRESHOLD`           | Speech activation threshold, `0.0`–`1.0`. Raise it in a noisy room. |
| `REALTIME_VAD_PREFIX_PADDING_MS`   | Audio retained from before speech was detected.                |
| `REALTIME_VAD_EAGERNESS`           | `semantic_vad` only: the *maximum* wait before taking the turn (`low`≈8 s, `medium`≈4 s, `high`≈2 s, `auto`). `semantic_vad` + `low` is the staged patience A/B. |
| `REALTIME_SOLO_NAME_GATE`          | Solo barge-in requires being addressed — the robot's name or a control phrase (停/stop, which always win). Default `1`. `0` restores interrupt-on-any-substantive-speech. Interruption only: unaddressed turns are still answered when Reachy is not talking. |
| `REALTIME_BARGE_MAX_PAUSE_MS`      | How long a reply stays paused for speech that never addresses the robot, before resuming *through* it. Default `4000`. `0` disables the gate-on pause entirely, leaving only the late interrupt. |
| `REALTIME_BARGE_CONFIRM_MS`        | Sustained-speech confirm window. **Gate-off only** — with the name gate on it commits nothing. Default `1600`; under `REALTIME_SOLO_NAME_GATE=0` it must exceed `REALTIME_VAD_SILENCE_DURATION_MS` or the rollback path is dead (the app warns). |
| `REALTIME_TRANSCRIPTION_DELAY`     | How long the streaming transcriber buffers before emitting a partial (`minimal`…`xhigh`). Unset by default; staged for the `gpt-live-transcribe` A/B. |
| `REALTIME_REASONING_EFFORT`        | Session `reasoning.effort`, pinned to `low` (OpenAI's voice-agent recommendation) so a server default change cannot add pre-speech latency. `off` omits the field. |
| `REALTIME_MAX_OUTPUT_TOKENS`       | Runaway-monologue rail, not a brevity knob — brevity is prompt work. Default `900` (≈40 s of speech); hitting it cuts mid-word and logs a warning. `inf`/`off`/`0` removes the ceiling. |
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
| `MEMORY_LAST_CHAT_ENABLED`         | Master switch for the sleep-time last-chat summary. Off writes no `上次聊天` fact. |
| `MEMORY_LAST_CHAT_MODEL`           | Model that writes the one-line summary per person. Defaults to `gpt-5-mini`. |
| `MEMORY_LAST_CHAT_TIMEOUT_S`       | Time budget for that summarizer call, `1.0`–`30.0`. Default `8.0`; an overrun leaves no fact. |
| `NOTION_MCP_URL` / `NOTION_MCP_TOKEN` | Remote MCP endpoint and bearer token for the Notion integration. |
| `HA_URL` / `HA_TOKEN`              | Home Assistant base URL and long-lived access token.            |
| `HA_ENTITIES`                      | JSON map of spoken names to entity ids — the **only** devices the model may target. |

## Status

Live on the robot (seventeenth install, v1.17.0) and in daily family use:
conversation, face recognition, person memory, music, TV and home-video
casting, calendar/tasks/email, home control and the name-gated barge-in are
all deployed with runnable evidence recorded per feature. What remains open is
formal, scripted validation of the five PRD §8 demo gates plus a set of live
acceptance rows for the newest wave — all tracked with exact pass criteria in
[`feature_list.json`](feature_list.json). Requirements, journeys and the
as-built architecture are in [docs/PRD.md](docs/PRD.md); current verified
state and open items are in [progress.md](progress.md); release notes in
[CHANGELOG.md](CHANGELOG.md).

## License and credit

Derived from Pollen Robotics'
[Reachy Mini Conversation App](https://github.com/pollen-robotics/reachy_mini_conversation_app),
licensed under Apache-2.0, and built on the
[Reachy Mini SDK](https://github.com/pollen-robotics/reachy_mini). See
`reachy_companion/LICENSE`. The original upstream README is preserved at
`reachy_companion/README_OLD.md`.
