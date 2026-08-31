# Changelog

Reachy Companion versions map to on-robot installs: the minor number is the
install that shipped the release (`1.17.0` = the seventeenth install), patch
numbers are fix-only redeploys. Versions before 1.17.0 were assigned
retroactively — the wheel said `1.0.0` for the first sixteen installs — and
their entries below are compact summaries reconstructed from `progress.md`
and `DECISIONS.md` (D-numbers cite the design records).

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
