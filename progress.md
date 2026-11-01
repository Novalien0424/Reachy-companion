# Progress

History compressed 2026-11-01 (previously 2026-08-29). The full earlier text is
in git: the last full version of this file is at commit `0f67ce8`.

## Current state

**v1.37.0 INSTALLED (2026-11-01 00:52 UTC, wheel `11fca7fc`); Reachy asleep
(go_to_sleep by voice, app stopped), TV off.** Operator rulings on D-045 (D-046).
- **A trip or place video plays from the 影片庫 first.** Live: our-trip 3/3
  (1.36.7: 0/3), 「放波士頓的影片」 3/4 (1 went to YouTube), MV → youtube 1/1.
  After a miss, the YouTube offer is uneven: asked 1/3, played unasked 1/3,
  no offer 1/3 (`VIDEO-MISS-FOLLOWUP`).
- **Kept by the operator:** reply rail 4096; the silent dance for good news.
- **Harness:** `deploy.sh` (one-command install, used for this deploy);
  `live_case.sh` redacts its output; the deploy skill went from 431 to 216
  lines.
- **Records:** progress.md and feature_list.json dropped verified, retired and
  superseded rows (41 removed; full text at `0f67ce8`). `VOICE-SLEEP-QUIESCE`
  verified live from this session's sleep.
- **Robot:** two app-stop wedges, each cleared by one daemon restart (00:56:17Z,
  01:03:11Z; `DAEMON-STOP-WEDGE`). Power clean (throttled=0x0). Suite 2283 / 31.

**v1.36.7 INSTALLED (2026-10-31 15:06 UTC, wheel `3dc93b1b`); Reachy asleep
(go_to_sleep by voice, app stopped), TV off.** A journey check, then fixes (D-045).
- **Journey on 1.36.3, one session:** wake, Chinese chat, dance, camera (dark
  room, honest), web search with preamble, TV on (read back), NAS Boston on the
  TV in about 2 s, pause, stop with bookmark, a story cut off by a song request
  (text barge), music stop. All demo paths worked.
- **Fixed:** the reply rail is 4096 (900 cut the news and the story mid-word;
  on 1.36.7 both completed live); smbprotocol no longer logs the NAS address
  and username (0 lines per wake, 33 before).
- **Measured, at mini's ceiling:** 「放我們去波士頓旅行的影片」 still asks for the
  source live 0/3 (replay 12/24 from 0/4); the dance for good news stays
  silent 3/4. Both shape changes are the operator's call (feature_list
  `VIDEO-OUR-TRIP-SOURCE`, `DANCE-SPEAKS-ON-GOOD-NEWS`).
- **Harness:** `live_case.sh` shows `status=incomplete` and WARNING/ERROR
  lines; `qa_replay.py --override` measures a wording before it ships.
- **Records:** suite 2283 / 31; daemon v1.13.0, no wedge, no restart; NAS
  transcode log clean (09:22 run, done 2737, failed 0); watchdog up, 0 actions.


## Wake-up / power diagnosis (2026-08-27)

- "Hard to wake" was the robot being off: the 08-25 and 08-26 boots ended on
  `hwmon: Undervoltage detected!` (5 V rail sag, not the GPIO23 path).
- Habit: look at the power LED first. Dark means a power event, not software.
- Live check: `vcgencmd get_throttled` (0x0 at idle; re-read after a loud
  motors + speaker session).
- Next time, tell a death at plug-in (battery/DC switchover) from a later death
  while plugged in (then swap in a known-good 5 V / 5 A USB-C PSU and a short
  cable). Report upstream once the pattern repeats.

## Next action

1. **Operator:** `TIME-OF-DAY-ANSWER`: add `get_time` as a 20th tool, or put the
   time in the session.
1b. **Operator:** after a miss in the 影片庫, keep asking about YouTube or play
   YouTube directly (`VIDEO-MISS-FOLLOWUP`, live 1/3 asked).
2. **Operator, in normal use:** the W3/W4 checks with real speech
   (`VIDEO-QUIET-GATE`, `GROUP-BARGE-BY-NAME`); both are unit-tested only.
3. **Next session:** read `~/HomeAssistant/state/nas-transcode.log` for the
   2026-11-01 03:30 run, the first with `place_zh`, then run
   `watchdog_cron.sh status`.
4. **Known, not fixed:** `JOURNEY-2026-10-31-NOTES` and the 1.36.x list (see
   Known defects).

## Pending verification (operator)

Everything below is on the robot (v1.36.7). Each row's probe is in its
`verification` field in `feature_list.json`; what is missing is a person in the
room or real speech.

- **PRD §8 demos:** `DEMO-1`–`DEMO-5` in one in-room run of
  `docs/five-demo-checklist.md`. `US-02` (head follows a face from boot),
  `US-09`, `DAEMON-UPGRADE-LATEST`, `UPSTREAM-REFRESH-2026-10` and
  `MCP-RESULT-TRIM` ride the same run.
- **Group mode and video:** `VOICE-NAME-ONLY-DEFAULT`, `VIDEO-QUIET-GATE`,
  `GROUP-BARGE-BY-NAME`: room talk gets no reply and does not cut Reachy off,
  「瑞奇，…」 does; count denied vs answered turns after a real multi-person
  conversation.
- **Modes:** `MODE-ONE-ON-ONE`, `MODE-GROUP-SWITCH`, `MODE-RECORD`,
  `VOICE-NO-DOUBLE-ANSWER`: voice switches both ways, unaddressed answering in
  一對一, the 紀錄模式 summary, no second answer after a talk-over.
- **Interruption in 一對一:** `VOICE-SOLO-BARGE`, `VOICE-LATE-INTERRUPT`,
  `VOICE-INTERRUPT-CONTEXT`, `VOICE-TRUNCATE`: a plain-sentence cut, a 「嗯」
  rollback, then 「你剛剛講到哪」 answered from the heard part only.
- **Turn timing and speech:** `VOICE-PATIENCE`, `VOICE-TURN-FRAGMENTS`,
  `VOICE-BREVITY`, `VOICE-SLOW-PREAMBLE`, `VOICE-WAIT-FOR-USER`,
  `LOCAL-VAD-LISTENING`, `LATENCY-TELEMETRY`: mid-sentence pauses, fragments,
  reply length, lead-ins only before slow work, silence for TV or side talk,
  antennas on the first syllable, one long session for the latency curve.
- **A/B trials:** `VOICE-SEMANTIC-VAD-AB`, `VOICE-NOISE-REDUCTION-AB`.
  `VOICE-MINI-MODEL` is open for tool choice on mini; its full-tier A/B is off
  the table (operator, 2026-10-30: mini only).
- **Faces and greetings:** `FACE-ROUTING`, `FACE-WAKE-EXTENDED`,
  `FACE-CROSS-SESSION`, `FACE-MULTI-SAMPLE`, `PERSON-GREET-KNOWN`,
  `PERSON-GREET-STRANGER`, `PERSON-GREET-EMPTY`, `ENROLL-STILL`,
  `ENROLL-SNAPSHOT`.
- **Person memory and the Mac backend:** `PERSON-MEMORY-AUTO`,
  `MEMORY-LAST-CHAT`, `MEMORY-OPEN-LOOPS`; `BACKEND-PUSH-LIVE` (needs two real
  photos), `BACKEND-IMPORT`, `BACKEND-CONSOLIDATE`.
- **Sleep:** `SLEEP-CLEAN-STOP`. The voice path already ran clean once
  (2026-09-02 14:37 robot time); the row's state was not updated.
- **Blocked:** `HANOVA-EMAIL` (needs a dictated real address and a
  two-paragraph body read back verbatim).
- **Probes left:** `VOICE-LOOK-AROUND` (「看右邊」, 「抬頭」 with the journal
  open); `WIFI-WATCHDOG` (running since 2026-10-31; waits for a real drop).
  `VOICE-SLEEP-QUIESCE` was verified live on 2026-11-01.
- **Other:** `NAS-PLAY-REQUIRES-SELECTOR` (confirm a live refusal when one
  happens; the actions its error names were renamed in 1.29.0); `US-07`
  (Notion over MCP, planned).
- **Optional leftovers on rows removed as verified (see `0f67ce8`):** music
  duck/resume under real speech; the off-LAN and broken-token wake rows for the
  cast family.

## Known defects / open edges

- `VIDEO-MISS-FOLLOWUP`: after a miss in the 影片庫, Reachy asked about
  YouTube 1/3, played it unasked 1/3, gave no offer 1/3 (1.37.0). Operator's
  call: keep the ask or play YouTube directly.
- `DANCE-SPEAKS-ON-GOOD-NEWS`: the good-news dance is silent 3/4. Left as is
  (operator, 2026-11-01).
- `TIME-OF-DAY-ANSWER`: 「現在幾點？」 has no clock to answer from (no time tool in
  the 19, no time in the prompt); it web-searched and gave no time. Operator's call.
- `EMOTION-WITHOUT-SPEECH`: `play_emotion` plays with no words (3/4 on
  1.30.0/1.31.0). Candidate: `needs_response=True`, A/B on the robot, watching
  for double speech.
- `TV-AUDIO-PHANTOM-TURNS`: TV sound was taken as a user turn twice on
  2026-10-29. Diagnose the VAD/echo path first; `VIDEO-QUIET-GATE` (unit only)
  covers videos Reachy started.
- `DAEMON-STOP-WEDGE`: an official app stop can leave the daemon's app manager
  in `stopping` until a service restart. Standing authorization to restart and
  record it (D-038); not reported upstream.
- `JOURNEY-2026-10-31-NOTES`: small speech slips from the 10-31 journey (news
  named portals, a mid-session TV-on with no lead-in, greeting slips, a spoken
  「（微笑）」, a benign sleep stop-timeout warning). Fix only what recurs.
- 1.36.x, no row: a mood request for speaker music finds no track (the error
  asks for a title); occasional simplified characters (no reliable prompt fix,
  D-044); 「暫停」 taken as `stop` about 1 in 3 (the bookmark absorbs it).
- Left open on rows removed as verified: `home_control` has no rule for states
  that are not on/off (scene, script, cover, lock), and the ignore case with a
  light switched off at the wall is unstaged (D-040); 「我們在哪裡」 during a
  home clip picked the camera 1/4 (D-043).
- `DEMO-1`: the `/rpc` interrupt (UI stop button) flushes playback but sends no
  `response.cancel`; voice barge-in is separate. `console.py` still only
  flushes (read 2026-11-01).
- `DEMO-4`: in the 2026-08 T11 run, 4.5 s of 16.8 s to the spoken answer was
  per-call MCP session setup. Session reuse is deferred; not re-measured since
  the MCP v2 client (D-036).
- Conversation mode does not survive a settings or backend restart (D-029 §6):
  it falls back to the boot mode, so 紀錄模式 stops recording silently.
- Boot greeting (D-025 accepted edge): a `too_far` or `multiple_faces` boot
  speaks the stranger line, then the named greeting late. The operator dislikes
  the generic-first feel; design judgement pending.
- Solo interruption residuals (D-032): a cough held past 1600 ms commits as
  speech (`confirmed by sustained speech` with no transcript; knob
  `REALTIME_BARGE_CONFIRM_MS`); a transcript later than the 2 s rollback gives
  a short resume-then-cut.
- Accepted by design (D-028): truncate positions are approximate (up to ~1.3 s
  over-cut per barge); late-interrupt eligibility outlives `response.done`; the
  boot-gate release can exceed `REALTIME_BOOT_GATE_TIMEOUT_S` by the drain cap.
- Engagement memory (D-027): only a voice `go_to_sleep` writes the last-chat
  fact; multi-person visits get topic-level summaries; cross-person facts
  surface one-sided; `consolidate.run` relies on the CLI's probe guard for
  exclusive store access.
- `reachy_companion.config` runs `load_dotenv(override=True)` at import, so
  backend and CLI code must never import a robot module that reaches it.
- Backend sync holes: a Mac person with ≥ 20 facts always projects 20, so their
  robot-side forgets never import back; face removals are not modelled;
  re-enrollment adds a second snapshot tile.
- The official conversation app installed beside ours needs a dashboard update
  before its own MCP tools work under mcp 2 (D-036).
- Accepted (D-014): unauthenticated console and `/rpc` on port 7860; the Mac
  backend (8710) is tailnet-bound by operator authorization.
- GPIO23 EMI spurious-shutdown risk (upstream `reachy_mini#1109`) in heavy
  restart cycles; do not mask the service.
- Upstream report candidates, none filed: the daemon stop wedge, `/rpc` say off
  the conversation loop (D-037), error results cut to the error string (1.29.1).

## History digest

- **2026-08-16** — scaffold → OpenAI realtime handler on `gpt-realtime-2.1`,
  Chinese locked profile (D-001…D-008).
- **2026-08-17/18** — first deploys (D-009), VoiceFX (D-010), WSOLA pitch
  (D-011), memory tools (D-012), face memory (D-013).
- **2026-08-19** — adversarial audit, six fixes (`a5f682d`), D-014 rulings;
  D-015 face-pipeline tuning deployed.
- **2026-08-20** — D-016 instance persona; D-017 VoiceFX comb/soft-knee.
- **2026-08-21/22** — D-018 HomeAssistant-Nova port (39 tools), latency work,
  D-019 static tool array, D-020 handoff disclosure.
- **2026-08-23** — persona v2, coral V13 voice baked in (D-021), persistent
  journald.
- **2026-08-24** — music fixes; `expect`-wrapper verdict; GPIO23 EMI trace;
  D-022 TV cast churn; multi-person hardening T1–T3.
- **2026-08-25** — voice-robustness round, **D-023**.
- **2026-08-27** — **thirteenth install** (`b4e154f`) live-verified (boot
  gate, `gpt-transcribe`, 41 tools, 1319/31); undervoltage power diagnosis;
  face-recognition RCA + fix wave **D-024**; **fourteenth install**
  (`ae62756`) live-verified same day (identity routing, extended wake window
  ran 7 rounds, 2 faces survived restore, 1351/30) — robot left app-stopped
  with the stop API's spurious `Motor communication error`.
- **2026-08-28** — person-memory + Mac backend wave, 14 tasks, **D-025**
  (1414/30 + 159); merge + enrollment-snapshots addendum, **D-026** (1449/30
  + 215); backend live on the tailnet, first real use (Linna enrolled by
  photo, merged with the misheard voice record, pushed); **fifteenth
  install** deployed (`ad5fe3e`, extended manifest, 4 faces + 4 people
  survived) — first boot blocked by the daemon app-state wedge, operator
  rebooting; boot verification owed next session.
- **2026-08-29/31** — fifteenth install verified; installs 16–19 (v1.17.0,
  v1.19.0) ship **D-027** engagement memory, **D-028** name-gated barge-in,
  truncate and patience, **D-029** three modes, an answer gate, 41 → 22 tools.
- **2026-09-01/05** — **D-030** LLM-first instructing (v1.20.0); **D-031**
  hold-off, lead-ins, clean sleep stop (v1.21.0, v1.22.0); boot to 一對一;
  **D-032** any real sentence stops a solo reply (v1.23.0).
- **2026-09-06 → 10-27** — no sessions; the robot lost Wi-Fi around 10-21.
- **2026-10-28** — **D-033** upstream refresh (v1.24.0) and daemon v1.13.0 via
  the official updater; **D-034** Wi-Fi watchdog, **D-035** instance dir (1.25.0).
- **2026-10-29** — **D-036** MCP v2 (v1.26.0); **D-037** NAS search for loose
  Chinese, `/rpc` fix (1.27.x); **D-038** media tools by source (1.28.x).
- **2026-10-30** — `nas` one-call play (1.29.x); **D-039** mini diet: 19 tools,
  no toolboxes, prefix 9.9k → 5.8k (1.30.0, 1.31.0); **D-040** read-back (1.31.x).
- **2026-10-31** — boot to 多人 (1.32.0); **D-041** NAS index self-refresh
  (1.33.0); **D-042** 「停」 stops the TV (1.34.0); **D-043** slips replayed
  (1.35.0); **D-044** pause, video gate, barge by name (1.36.0–1.36.3);
  **D-045** reply rail 4096, quiet SMB log (1.36.4–1.36.7).
