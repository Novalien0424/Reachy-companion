# Changelog

Reachy Companion versions map to on-robot installs: the minor number is the
install that shipped the release (`1.17.0` = the seventeenth install), patch
numbers are fix-only redeploys. Versions before 1.17.0 were assigned
retroactively — the wheel said `1.0.0` for the first sixteen installs — and
their entries below are compact summaries reconstructed from `progress.md`
and `DECISIONS.md` (D-numbers cite the design records). There is no `1.18.0`:
the eighteenth install was a metadata-only redeploy that re-shipped the
`1.17.0` wheel, so it carried no release of its own.

## [Unreleased]

- **Reachy now boots into 一對一聊天模式.** Operator instruction 2026-09-04
  (D-029 decision 5, amended): the code default, the `set_conversation_mode`
  description and the dead-knob warning all follow; `REALTIME_DEFAULT_MODE=group`
  restores the room posture. The robot's instance `.env` already carries the
  new value, so the installed 1.22.0 wheel boots solo before this ships.
- **RCA, no behaviour change:** `docs/rca-solo-interrupt-2026-09-04.md` —
  why solo-mode interruption is hard (name gate + a pause cap that fires
  before the transcript exists) and why the old reply plays out before the
  new answer (rollback resumes fully-buffered audio; the new answer queues
  behind it), plus first-audio latency growing 2 s → 10 s over a session.

## [1.22.0] — 2026-09-02 · calibration and tool-surface symmetry

Deployed as the twenty-second install (commit `5d0e9e2`, wheel sha `4ba4e698…`).
Design record D-031 (addendum). A same-day follow-up to 1.21.0 after the
operator asked whether the 700 ms hold-off has a research basis (it did not).

- **It now measures its own pauses.** Every time the hold-off merges a
  fragment with its continuation the journal records how long the pause really
  was and how long the robot had been holding; and when someone resumes
  speaking within two seconds after a window already expired, it says so. A
  handful of real turns will show whether 700 ms should move.
- **Its tools say what they are not for.** Eleven always-on tools that had a
  one-line description now carry matching use-when / do-not-use pairs, so
  emotion versus dance, stop-emotion versus stop-dance, remember versus forget,
  and status versus cancel are told apart by the description rather than by
  luck.
- **It varies how it says things.** A new rule asks for different lead-ins and
  acknowledgements from turn to turn, because the lead-ins are audible now and
  the same 「我查一下」 every time sounds like a recording.

### For contributors

- `docs/research-holdoff-calibration-2026-09.md` records the external evidence
  for the window and the audit of the repo's research docs; D-031's addendum
  lists the seven next-wave candidates in priority order.
- New INFO journal lines: `turn hold-off: awaiting continuation (…) gap=<ms>
  held=<ms>` and `turn hold-off: late continuation <ms> ms after the window
  (window=<ms> ms)`.

## [1.21.0] — 2026-09-02 · the field-test fix wave

Deployed as the twenty-first install (commit `c3e46cf`, wheel sha `a96e06ed…`).
Design record D-031. Fixes three of the six findings from the 2026-09-01
field test, in the operator's order.

- **It waits for you to finish the sentence.** Mid-sentence pauses were being
  committed as turns — 「你。」「就是。」 got answers in under half a second, and
  the rest of the sentence got a second answer. After it accepts a turn the
  robot now holds its reply for a short window; if you keep talking, the
  fragment and the continuation are answered together. If what followed was
  only a cough, the original question is still answered. The window is
  `REALTIME_COMMIT_HOLDOFF_MS` (default 700 ms; 0 restores the old behaviour).
- **It tells you it is looking before a slow lookup.** Web searches used to
  mean eight to ten seconds of silence because every pre-tool remark was
  swallowed on purpose. Those remarks are audible again, the instructions say
  where they belong (before slow work — search, finding music, remote services
  — and not before quick moves), and they never end up in the room log or the
  sleep-time memory as if they were answers.
- **It sends "play that" to the speaker, not to the search engine.** The
  search and music tools now name the boundary between them, and YouTube
  playback requests belong to `music`.
- **It reads the "if you did not hear it, ask" rule last.** Moved to the end
  of the instructions, restated positively, so it is the freshest rule at the
  moment a fragment arrives.
- **「睡覺吧」 ends cleanly.** The robot's own daemon hangs up on the stop
  request a moment early, and that used to be mistaken for a failure that
  switched the microphone back on seconds before the app died. The early
  hang-up is now understood as the stop succeeding, the microphone stays off
  once sleep has begun, and a stuck motion writer can no longer abort the
  pose or the shutdown cleanup.
- **It gets a second chance to remember the visit.** The end-of-visit summary
  was a single eight-second call; one timeout lost the memory. It now retries
  once, briefly.

### For contributors

- New env knob `REALTIME_COMMIT_HOLDOFF_MS`; documented in `.env.example` and
  the README table.
- Commentary-phase items: audio passes, transcript withheld. The journal lines
  changed accordingly (`commentary-phase item … is audible; transcript
  withheld`, `withholding commentary-phase transcript for item …`); the old
  `suppressing …` / `dropping commentary-phase audio …` lines are gone.
- Bundled tool-space specs override a manifest's cached description at read
  time, so description edits reach a robot that already has a manifest.
- `persona.md` and the locked profile changed one line each; the instance
  persona must be re-synced at deploy (it was, sha in `progress.md`).
- `request_stop_current_app` catches `URLError`, `HTTPException` and `OSError`
  and logs the exception type; the C6 unmute recovery covers only pre-pose
  failures; the sleep summary retries a `TimeoutError` once with a fixed 4 s
  budget.
- Plan and review log: `docs/plans/2026-09-01-field-test-fixes-plan.md`
  (rev 3, two Codex rounds under the new 2-round cap, 14 findings, 0 rejected).

## [1.20.0] — 2026-09-01 · the LLM-first instructing wave

Deployed as the twentieth install (commit `…`, wheel sha `…`).
Design record D-030.

The rule this release is built on: the model decides what to say and which tools
to call; the app instructs it and holds the safety rails.

- **It says goodbye before it lies down.** 「睡覺吧」 used to produce a tool call
  with no words at all, and the robot posed in silence. Now the sleep tool only
  mutes the microphone and hands the turn back; the goodbye gets a response of
  its own that nothing else can ride along with, and the body waits for that
  sentence to finish playing before it moves. The inactivity timeout, which has
  nobody to say goodbye to, still just lies down.
- **Its head actually turns when you tell it to look somewhere.** 「看右邊」 sent
  the right command every time and the daemon's face tracker overrode it within
  a frame, so the photo came back showing whoever was straight ahead. Manual head
  moves now hold the head against face-following for as long as they need it, and
  hand it back exactly as they found it — off stays off.
- **It stops guessing at bad arguments.** A direction it does not recognise, the
  string "false" where a yes/no belonged, a toolbox that does not exist: all of
  them used to be quietly coerced into *something*. They now come back as a
  correction naming what is allowed, so the robot can fix its own mistake in the
  same breath.
- **It stops claiming things it has only started.** "Looking right" was said the
  moment the movement was queued. The robot now reports what is actually true —
  the direction it asked for — and the prompt tells it that is what to say.
- **It reads one set of instructions instead of two.** The character file said
  "use the calendar", the system rules said "open the toolbox first"; the
  character file said "follow whatever language they use", the system rules said
  "stay in Taiwanese Mandarin". One authority each now, all in Taiwan Traditional
  Chinese, with the sentence-count rules replaced by "length follows content".
- **What it remembers about you is labelled as background, not as orders** — and
  says out loud that what you tell it now wins.

### For contributors

- The `finish_session` rename is an A/B alias, not the shipped name. It is
  exposed only when `INSTRUCTING_FINISH_SESSION_ALIAS` is set; the default
  surface still carries `go_to_sleep`.
- Two controller fix loops are part of the reviewed state: the Task 3 farewell
  tests now patch the dispatcher's imported `core_tools` module rather than a
  reloadable string path, and Task 12 reconciled the commentary-only test with
  the request-scoped response waiter while removing the last stale
  `run_go_to_sleep_tool` comment.

## [1.19.0] — 2026-08-31 · the conversation-modes wave

Deployed as the nineteenth install (commit `…`, wheel sha `…`).
Design record D-029.

Reachy now has three ways of being in a room, and you switch between them by
saying so.

- **It wakes up in 多人聊天模式.** In a room with several people it listens
  quietly and answers when you say its name — the failure it used to have was
  waking up ready to answer every overheard sentence. 「切到一對一聊天模式」
  gives you the old always-answering behaviour back when it is just the two of
  you, and 「進入紀錄模式」 turns it into a silent scribe that writes the whole
  meeting down and reads a summary back when you ask (「瑞奇幫我總結」). That
  record lives in memory only and is wiped when you leave the mode or go to
  sleep.
- **It stops answering things you did not say to it.** Every reply is now the
  robot's own decision rather than the server's reflex, which also kills the
  double answer: talk over Reachy and the sentence it was saying resumes — no
  second full answer queued behind it.
- **It turns its head when you tell it to look somewhere.** 「轉到右邊去看看
  有誰」 moves first and describes second, instead of describing whatever was
  already in front of it.
- **It finishes saying goodbye before it lies down.** 「睡覺吧」 mutes the
  microphone, lets the goodbye play out, and only then takes the sleep pose —
  no more cut-off farewells, and no turn opened by the goodbye's own echo.
- **It picks the right tool more often, because it sees 22 instead of 41.**
  The calendar/to-do/drive/email/Notion family and the TV/NAS-video family load
  the moment a request needs them, six overlapping tool families became one
  tool each, and three tools nobody used were retired. Music stays always
  loaded, so 「音樂關掉」 never has to wait for anything.
- **And it stops muttering to itself.** The 2.x model's 「讓我想想…」-style
  preambles are dropped before they reach the speaker.

### For contributors

- `ConversationMode` enum + `set_conversation_mode` replace the `party_mode`
  boolean; `REALTIME_DEFAULT_MODE` replaces `REALTIME_PARTY_DEFAULT`, which is
  now a dead knob that warns. New: `REALTIME_ONE_ON_ONE_ANSWER_GATE`,
  `RECORD_SUMMARY_TIMEOUT_S`, `SLEEP_GOODBYE_DRAIN_CAP_S`.
- All live `session.update`s go through one ordered, acknowledged,
  single-flight mechanism with an unmatched-acknowledgement debt counter — mode
  flips and toolbox opens await it, so the model never speaks under a session
  shape the server has not applied yet.
- Three Codex review rounds, 45 findings, 45 accepted. Suite 1746/30.

## [1.17.0] — 2026-08-31 · the human-like-conversation wave

Deployed as the seventeenth install (commit `26573f0`, wheel sha `c5cccfa0…`).
Design record D-028.

Reachy now handles a room full of talk the way a person telling a story does.

- **Say its name to interrupt.** While Reachy is speaking, only 「瑞奇」/"Reachy"
  (or a stop phrase — 停/閉嘴/stop, which always win) takes the floor away.
  A cough, an 「嗯」, or a sentence aimed at someone else just pauses the reply
  for a beat, then it carries on. Talk past it for more than ~4 seconds and it
  resumes right through the side conversation — and still yields if the name
  turns out to have been said (`REALTIME_SOLO_NAME_GATE`,
  `REALTIME_BARGE_MAX_PAUSE_MS`).
- **It stops rushing you.** End-of-turn silence window raised to 1 second, and
  the session pins `reasoning.effort=low` so replies start fast and stay fast.
  The `semantic_vad eagerness=low` "wait until you actually sound finished"
  mode is one env flip away for a live A/B.
- **It remembers only what you heard.** Every committed interruption now sends
  `conversation.item.truncate`, so a reply you cut off no longer lives on in
  the model's memory as if it had been finished — the thing that made Reachy
  refer back to explanations nobody heard.
- **It talks like a friend, sized to the moment.** The prompts teach length
  calibration (one-line answers for one-line questions, real explanations when
  the topic deserves them) and ban the filler: preambles (「讓我想想…」),
  repeating what you just said, unasked background. No sentence caps — a
  900-token safety rail (`REALTIME_MAX_OUTPUT_TOKENS`) only catches runaway
  monologues, loudly.

### For contributors

- Client-side barge decisions gained a late-interrupt path with a careful
  response-id lifecycle (eligibility captured at speech onset), partial-
  transcript fast commit, and heard-audio accounting for truncate
  (stash-at-pause, always rounded down). 100+ new tests; suite 1571/30.
- `REALTIME_BARGE_CONFIRM_MS` is now gate-off-only; the confirm-vs-silence
  startup warning is semantic-VAD-aware (fixes a recorded defect).
- New knobs documented in `.env.example` and the README table.

## Earlier installs (retroactive)

- **1.16.0 — 2026-08-30 · engagement memory** (D-027): the sleep-time
  last-chat summary (「上次聊天…」 written per recognized person when you send
  Reachy to sleep by voice), open-loop-first memory guidance, and the Mac-side
  `consolidate.py` store tidy-up CLI.
- **1.15.0 — 2026-08-28/29 · person memory + Mac backend** (D-025, D-026):
  per-person fact stores with personalized wake greetings, still-pose face
  enrollment with snapshots, and the `companion_backend/` FastAPI app for
  managing people/photos/facts from a Mac, with guarded push/import/merge.
- **1.14.0 — 2026-08-27 · face-recognition fix wave** (D-024): identity
  routing (「你記得我嗎」 goes to `who_is_this`, never the camera), the
  extended wake window for late recognition, threshold retuning.
- **1.13.0 — 2026-08-27 · voice robustness** (D-023): the pause-then-decide
  solo barge-in with false-interruption rollback, `wait_for_user` prompt
  hardening, far-field noise reduction, `gpt-transcribe` with keyword biasing.
- **1.12.0 — 2026-08-24/25 · multi-person & media hardening** (D-022, party
  plan): party mode (address-gated answers in a group), TV-cast churn fixes,
  music barge-in coordination.
- **1.9.0 – 1.11.0 — 2026-08-21/23 · the tool wave** (D-018…D-021): the
  HomeAssistant-Nova port — 39 tools spanning home control, music, TV/NAS
  video, calendar/tasks/email/Notion/Drive with spoken confirmation gates —
  persona v2, the coral V13 voice, static tool registration for latency.
- **1.7.0 – 1.8.0 — 2026-08-20 · character** (D-016, D-017): the persona
  externalized to an editable on-robot `persona.md`, and the VoiceFX
  comb/soft-knee chain behind Reachy's voice.
- **1.2.0 – 1.6.0 — 2026-08-17/19 · memory, faces, first hardening**
  (D-009…D-015): first on-robot deploys and the backup/restore ritual, WSOLA
  pitch shift, `remember`/`forget` fact memory, on-device face recognition
  with enrolment-by-name, the adversarial-audit fix wave.
- **1.0.0 – 1.1.0 — 2026-08-16 · foundation** (D-001…D-008): scaffold from
  Pollen Robotics' official conversation app, the `gpt-realtime-2.1` backend,
  the locked Chinese-first profile.
