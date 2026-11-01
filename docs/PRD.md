# PRD — Reachy Companion

Reachy Companion is the app that makes a Reachy Mini Wireless a household
companion. You talk to it in Chinese, and it looks at you, reacts with its
body, looks when asked, finds things out, plays the family's videos and music,
and acts on the house.

This file says what Reachy does and the rules it is built under.
- **Build state:** `progress.md` and `feature_list.json` (each capability's
  verification state).
- **The reasons behind each rule:** `DECISIONS.md`.
- **The original long PRD** (user stories, journeys, architecture) is in git
  at `bb0291f`. The section numbers that `DECISIONS.md` and older plans cite
  refer to it.

## Goal

The project answers one question: does a fast, interruptible, tool-using
realtime voice model, placed behind an expressive body, feel categorically
different from a smart speaker? The gap it targets is presence, not
intelligence. Latency, turn-taking, gaze and physical reaction have to be good
enough that people stop adapting to the machine.

**Users:**
- **A family at home.** Several people may be in the room. They speak Chinese,
  and Reachy follows whatever language a person opens with.
- **A developer** adding a capability without touching the conversation
  engine (`docs/adding-a-skill.md`).

## What Reachy can do

### Converse

- **Voice.** Speech to speech on `gpt-realtime-2.1-mini`, in a voice of its
  own: an on-device character filter (pitch-shifted, duration kept, with an
  off switch).
- **Persona.** The persona is the operator's. It lives in `persona.md` on the
  robot and changes without a rebuild.
- **Three ways of taking part,** switched by voice:
  - **多人 (group, the start mode):** Reachy answers only a turn that names it
    (「瑞奇，…」), or one inside a short follow-up window after an answered
    turn. Room talk gets no reply and does not cut Reachy off.
  - **一對一 (one-on-one):** Reachy answers one person without needing its
    name. Talking over Reachy interrupts it, and the conversation carries on
    from what was actually heard. A 「嗯」 or a cough does not interrupt it.
  - **紀錄模式 (record):** Reachy stays silent and keeps notes of everything
    said. 「瑞奇幫我總結」 reads back a summary, and leaving the mode wipes the
    notes.
- **Silence.** Reachy stays quiet when nothing is meant for it: silence,
  noise, TV sound, people talking to each other.
- **Slow work.** It says a short lead-in before work that takes a while (a
  search, music, a remote tool), and does fast actions without one.
- **Clock.** It knows the time and the date.

### Move

- **Face tracking.** It follows the face in front of it from the moment it
  wakes. The model is not involved.
- **Reacting.** It moves while it speaks, and its antennas take a listening
  pose on the first syllable it hears.
- **Gestures on request.** It plays emotion reactions from the official
  library alongside what it says, and it dances on request. It also turns its
  head on request, or keeps it on you (「抬頭」, 「一直看著我」), and stops
  moving when asked.

### See and recognize

- **Pictures on request.** It takes one picture when asked and describes it:
  what someone is holding, what colour it is, how they look. It can turn
  toward a direction first (「轉到右邊去看看有誰」). No continuous video
  leaves the robot.
- **Faces.**
  - **What it does:** it learns a face when asked (「記住我」) and answers
    「你認得我嗎」. At wake it greets the people it knows by name, and it
    greets a stranger as a stranger.
  - **Privacy:** recognition runs on the robot, and frames and face
    signatures never leave it.

### Remember

- **Facts about people.** It saves facts people tell it and forgets them on
  request. While someone is recognized, the facts go into that person's own
  record, and Reachy brings them up on later visits.
- **Visit notes.** Going to sleep leaves a short note of the visit for each
  person it recognized.
- **People backend.** A Mac-side backend (`companion_backend/`, home network
  only) manages people:
  - enrollment from photos;
  - curating the facts;
  - a push to the robot;
  - a previewed import of what the robot learned.

### Find out and act

- **Web search.** It searches the web unprompted when an answer needs today's
  facts. The time and date come from a separate tool. Both are Pollen's MCP
  tool Spaces.
- **Home control.**
  - **Devices:** it controls Home Assistant devices on an allowlist, for
    example the TV.
  - **Read-back:** it reads the device's state back before saying the action
    worked.
  - **Away from home:** house actions are refused on positive evidence that
    the robot is on another network.
- **Music** on its own speaker: play, pause, resume, stop.
- **The living-room TV:**
  - the family's own videos from the NAS. A trip or place video is found and
    played in one step. When nothing fits, Reachy asks before trying YouTube;
  - YouTube;
  - a picture Reachy draws with AI.
- **Google Drive:** list recent files, move one to the trash, upload a camera
  photo.
- **Email.** It reads the recipients, the subject and the whole body back word
  for word, and sends only after a yes.

### Look after itself

- **Wake and sleep.** A touch on the antennas wakes it. 「睡覺吧」 gets a
  goodbye; then Reachy lies down and the app stops.
- **Install.** It installs as a managed app under the robot's own daemon. Each
  wake picks up the newest NAS video index.
- **Wi-Fi.** It rejoins Wi-Fi on its own after a drop (a watchdog, proven on a
  real drop).
- **Missing services.** It starts normally with any optional service missing.
  A tool whose configuration is absent says it is unavailable.

## Acceptance

The POC succeeds when five demonstrations work reliably in front of a person:

1. **Conversation:** a multi-turn Chinese conversation in which the person
   interrupts Reachy mid-speech.
2. **Expression:** a fitting physical reaction during conversation.
3. **Vision:** Reachy correctly describes an object it is shown.
4. **Web:** a question about today triggers a search the person didn't ask
   for.
5. **Skill:** one real home device changes state on request.

**Status:**
- Demos 1–4 passed on the simulator on 2026-08-17.
- Demo 5 passed live on the robot on 2026-10-30 (TV on and off, read back).
- The formal in-room run of all five, `docs/five-demo-checklist.md`, is still
  pending (`DEMO-1`…`DEMO-5`).

Everything else in "What Reachy can do" is additive to these five. Several
capabilities are built and in daily use but not yet formally verified;
`feature_list.json` says which.

## Known limits

- **TV sound** can be taken for a person speaking to Reachy.
- **On good news,** Reachy sometimes reacts or dances without saying anything.
  The operator chose to leave the dance case as it is.
- **The daemon's own app stop** sometimes sticks, and a daemon restart clears
  it.
- **After a video-library miss,** Reachy occasionally says nothing found
  without offering YouTube.

## Rules

- **Mini model only.** Reachy runs on `gpt-realtime-2.1-mini` (operator
  decision, for cost). Tools are shaped for what mini does on its first call.
- **The model decides.** It chooses what to say and which tool to call. Code
  holds only the safety rails, the timing, and the truth about the physical
  state.
- **Movement runs without the model.** Tracking, idle motion and arbitration
  run on the official Reachy code. The model triggers expressions; it doesn't
  steer motors.
- **Reuse first.** The official SDK and Conversation App come before new code
  (`CLAUDE.md` § Reuse First). This keeps the diff against upstream small.
- **Irreversible actions need a yes in code.** Email and Drive trash get a
  read-back and the person's yes, within a short window. The confirmed call
  runs the action that was read back, not new arguments.
- **Privacy:**
  - only an explicitly requested picture leaves the robot;
  - face data stays on it;
  - secrets live in `.env` files, never in source;
  - no tool text carries a personal identifier.
- **The robot stays the robot's.** Deploys are app-only, and the daemon is
  the robot's infrastructure.

## Non-goals

**Out of scope:**
- a generic agent platform or plugin marketplace, or a Skill SDK;
- multi-model routing;
- a long-term memory architecture or an identity system beyond the bounded
  per-person store below;
- continuous video understanding;
- a new motion engine or custom motor control;
- mobile or desktop apps;
- production observability;
- enterprise security or authorization systems;
- OAuth flows or an MCP management UI.

**Promoted, under bounded constraints:**
- **Face recognition (2026-08-18):**
  - it reuses the SDK's face detector and adds one model and no new
    dependencies;
  - it runs on the robot only;
  - it is one check at wake plus explicit requests, never a running loop.
- **Per-person memory and the Mac backend (2026-08-28):**
  - the robot gets no new subsystem, only a sibling JSON store;
  - photos never reach the robot;
  - all management lives on the Mac, which treats the robot's stores as a
    rebuildable copy;
  - design: `docs/superpowers/specs/2026-08-28-person-memory-and-backend-design.md`.

**Removed by operator decision (D-039):** Google Calendar, Google Tasks and
Notion.
