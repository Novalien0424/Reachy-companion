# Progress

History compressed 2026-08-29 (previously 2026-08-27); full narrative in git
history of this file.

## Current state

**EIGHTEENTH INSTALL (metadata-only redeploy, 2026-08-31 07:39 robot time):**
the same code re-shipped as wheel **v1.17.0** (sha `3ddae98d…`) so the robot's
installed metadata matches the new install-mapped versioning scheme
(CHANGELOG.md; tag `v1.17.0`; GitHub release published). Full ritual again —
backup/restore manifest `20260831T063823Z-11114`, 4 faces + 4 people read
back, persona sha `3a2be561` intact — clean boot, zero tracebacks, app left
RUNNING. `importlib.metadata.version("reachy_companion")` on the robot now
answers `1.17.0`.

**SEVENTEENTH INSTALL DEPLOYED AND BOOTED (2026-08-31 07:13 robot time).**
`name-gate-patience` was merged to `main` (fast-forward to `26573f0`, pushed;
merged-tree suite re-run green **1571 / 30**) and deployed via the full
ritual: wheel sha `c5cccfa0…` verified end to end, version gate passed
(daemon 1.10.0rc5), app already stopped at deploy time, two-step `--no-deps`
install, manifest backup/restore at
`/tmp/reachy_companion_backup/20260831T061202Z-10185` (faces **4** + people
**4** read back, `memory.v1.json` + `face_snapshots/` recorded absent,
google-oauth + nas index + google-workspace-mcp restored, modes reasserted).
**The persona re-sync is DONE**: the Task 8 persona (no-preamble line) was
scp'd over the restored copy and sha-verified at **`3a2be561`** (replacing
`1ce532f3`), and the installed `prompts.py` carries the new 回答長度 block.
Assets preloaded, app discovered. First boot clean: `persona: instance
persona.md`, **41 tools**, `Face memory ready … 4 people enrolled` (1324 ms),
wake check 5 rounds / 2173 ms (empty room), `Queued startup greeting prompt`,
`session turn_detection updated: party=False`, `boot gate released (greeting
played)`, **zero tracebacks**, and — decisive for D-028's two config risks —
**no `session.update rejected` line**: the live API accepted
`reasoning.effort=low` and `max_output_tokens=900`. **App left RUNNING.** The
wave now awaits its five live rows: `VOICE-NAME-GATE`, `VOICE-LATE-INTERRUPT`,
`VOICE-TRUNCATE` (raise the module log level to DEBUG for that run — the
refused line is DEBUG), `VOICE-PATIENCE`, `VOICE-BREVITY`.

**Wave record (merged): the human-like-conversation wave (2026-08-31).**
Nine tasks off
`docs/plans/2026-08-30-name-gate-patience-plan.md` (three Codex review rounds,
26 findings, 23 accepted outright, 3 in part; plus two implementation-review
rulings that went *against* plan text — the late-interrupt eligibility flag and
the dropped `response.created` reset of the audio-item tracker). Design record:
**D-028**. What it changes: solo barge-in is **name-gated** by default
(`REALTIME_SOLO_NAME_GATE=1` — only the robot's name or a control phrase takes
the floor away mid-reply, interruption only, answers are unaffected), an
unaddressed pause is capped at `REALTIME_BARGE_MAX_PAUSE_MS=4000` and the reply
resumes *through* the side conversation, a late name/control phrase on a
committed turn still stops it (`late solo interrupt (...) on committed turn`),
every **committed** interruption now sends `conversation.item.truncate` so the
model stops believing it finished replies nobody heard, patience defaults move
(VAD silence 800 → **1000**, barge confirm 1400 → **1600** and now gate-off-only,
`reasoning.effort` pinned **low**), `REALTIME_MAX_OUTPUT_TOKENS=900` is a loudly
logged runaway rail, and brevity is taught by prompt calibration rather than a
sentence cap. Gates at merge: robot suite **1571 passed / 30 skipped** (final
fix wave added two tests over the plan's 1569 pin), `ruff check .` and
`mypy --strict src` clean. All of it is **on the robot as of the seventeenth
install above**, persona included. Five new live rows own the acceptance:
`VOICE-NAME-GATE`, `VOICE-LATE-INTERRUPT`, `VOICE-TRUNCATE`, `VOICE-PATIENCE`,
`VOICE-BREVITY`.

**SIXTEENTH INSTALL DEPLOYED AND BOOTED (2026-08-30 00:03 robot time).**
`engagement-memory` was merged to `main` (fast-forward to `e4a40b1`, pushed)
and deployed via the full ritual: wheel sha `0f95e9ff…` verified end to end,
version gate passed (daemon 1.10.0rc5), two-step `--no-deps` install,
manifest backup/restore at `/tmp/reachy_companion_backup/20260829T230203Z-12174`
(faces 4 + people 4 read back, persona sha `1ce532f3` intact,
`memory.v1.json` + `face_snapshots/` recorded absent, google-oauth + nas
index + google-workspace-mcp restored, modes reasserted), `sleep_summary.py`
confirmed in site-packages, assets preloaded, app discovered. First boot
clean: `persona: instance persona.md`, 41 tools with the NEW fact-fidelity
`who_is_this` description visible in the registration log, `Face memory
ready … 4 people enrolled` (777 ms), wake check 5 rounds / 2083 ms
(empty room), `boot gate released (greeting played)`, extended window
closed after 7 rounds, zero tracebacks. **App left RUNNING.** The
engagement-memory features now await their live rows (`MEMORY-LAST-CHAT`
needs a recognized session ending in a voice 「進入睡眠模式」).

Branch history (merged): Nine tasks off
`docs/plans/2026-08-29-engagement-memory-plan.md` (three Codex review rounds,
23 findings, 22 accepted): the last-chat callback written at sleep, open-loop
prompt guidance, and a Mac-side consolidation CLI. Design record: **D-027**.
Gates on the branch: robot suite **1468 passed / 30 skipped**, backend **267
passed**, `ruff check` and `mypy --strict` clean on both sides (`mypy tests/`
on the backend runs against a known baseline — 12 errors on `main`, 17 here,
the five new ones the same `attr-defined` monkeypatch-target class
`test_robot_sync.py` already carries). What is live TODAY: only the persona
half of Task 1 — `persona.md` was scp'd to the robot instance 2026-08-29 and
sha256-verified (`1ce532f3…`), loading at the next app start. **Everything
else needs the sixteenth install**: `sleep_summary.py`, the ToolDependencies
containers, the shutdown hook, and the `remember`/`who_is_this` description
edits all ride the next wheel. The backend half (`store.replace_facts`,
`backend/consolidate.py`, `scripts/consolidate.py`) is Mac-side and works the
moment the backend is restarted from this branch.

**Fifteenth install BOOTED AND VERIFIED on the robot (2026-08-29 00:41, after
the operator's reboot).** The reboot cleared the daemon app-state wedge
(`current-app-status` back to `null`), the app started via the sanctioned
start API and reached `running`, and the boot journal shows every expected
line: `persona: instance persona.md`, 41 tools registered, `Face memory
ready: … 4 people enrolled` (1337 ms), wake check 5 rounds / 2107 ms (≤ the
4000 ms budget) ending in the empty-room branch (`Queued startup greeting
prompt`), `boot gate released (greeting played)` at +6.6 s, extended wake
window closed at its bound after 6 rounds, zero tracebacks. One benign
warning: TURN credential fetch failed (`turn.fastrtc.org` DNS) — WebRTC
console path only, greeting and audio unaffected. **The app was left
RUNNING.** All remaining verification is live/human (see Pending
verification).

Deploy record: deployed 2026-08-28 ~22:35–22:45 local (Mac), commit `ad5fe3e`
(main: the person-memory wave + the merge/snapshots addendum together), wheel
sha `068e6b81…` verified end to end, two-step `--no-deps` install,
manifest-driven backup/restore at
`/tmp/reachy_companion_backup/20260828T143611Z-22788` with the **extended
manifest** (now `people.v1.json` + `face_snapshots/`): `faces.v1.json` **4
records** and `people.v1.json` **4 records** read back after restore (the
operator's merged "Linna" push), persona sha `4c87d2ec` preserved,
`memory.v1.json` and `face_snapshots/` recorded absent, google-oauth + nas
index + google-workspace-mcp restored, `people.py` + `face_snapshot.py`
confirmed present in site-packages, assets preloaded, app discovered.

Daemon app-state wedge history: after the fifteenth install (2026-08-28) the
tracker was stuck at `state: "stopping"` with no app process — start/stop/
restart all refused, daemon otherwise healthy. The operator's ssh reboot
(2026-08-29) cleared it, consistent with Pollen's OFF → 5 s → ON
prescription. Watch whether it recurs after clean stops (it followed the
fourteenth install's `Motor communication error` stop).

What this build ships (first time on the device): the three-way boot greeting
with per-person facts, `people.v1.json` person-scoped `remember`/`forget` and
`known_facts` on `who_is_this`, still-pose enrollment with the deferred-
application hold, and enrollment snapshots (`face_snapshots/<record_id>.jpg`
— written only on a **fresh** enrollment; nothing backfills earlier people).
Design records: **D-025** (wave) and **D-026** (merge + snapshots, the D-013
amendment). Gates at deploy: robot suite **1449 passed / 30 skipped**, backend
**215 passed**, ruff + mypy strict clean on both.

**Mac backend:** `companion_backend/` (FastAPI + vanilla-JS UI) owns
people/photos/facts and pushes projections over the guarded scp promote;
merge + aliases are live Mac-side and already used (Linna/Lena merged and
pushed — that data is what the restore preserved). Operator-authorized bind:
`COMPANION_BACKEND_HOST="$(tailscale ip -4)" ./run.sh` serves the tailnet at
port 8710. **The process is currently stopped** (operator stopped the session
background task); restart with that one-liner from `companion_backend/`. New
on the `engagement-memory` branch: `scripts/consolidate.py`, an operator-run
LLM tidy-up of the store's facts — dry run by default, `--apply` to write,
`--import-first --apply --push-after` for the whole round trip. It **refuses
to run while the backend answers** (the store lock is process-local), probing
loopback, `COMPANION_BACKEND_HOST` and the live `tailscale ip -4` and failing
closed on anything but a refused connection. Usage and exit codes are in
`companion_backend/README.md` §Consolidation.

## Wake-up / power diagnosis (2026-08-27)

**"Hard to wake up" = the robot is sometimes OFF — undervoltage hard
power-loss, two days running.** Journal: the boots of Aug 25 and Aug 26 both
end with `hwmon: Undervoltage detected!` as their final line, then nothing
until a human powers the robot back on. No `Shutdown button released` lines,
so this is the 5 V rail sagging, not the GPIO23 EMI path. Software cannot fix
it; `startup_app` is set and the wake path works whenever there is power.

Cause is ambiguous (the kernel line is identical for all three): weak
PSU/cable, battery ran out, or charging path under-delivering. Operator
observation 2026-08-27: it died while plugged into the ORIGINAL adapter —
narrowing to a marginal adapter/cable, a charging-path fault, or a battery↔DC
switchover glitch at plug-in. No matching public issue upstream (searched
2026-08-27); report upstream once one more occurrence pins the pattern.
Triage next occurrence: death AT plug-in (switchover → plug in while off) vs
later while plugged (swap in a known-good 5 V/5 A USB-C PSU + short cable).
Live check: `vcgencmd get_throttled` (0x0 at idle 2026-08-27; re-read after a
loud motors+speaker session). Habit: robot "hard to wake" → look at the power
LED first; dark = power event, not software.

## Next action

1. Merge `name-gate-patience` into `main`.
2. Deploy the **seventeenth install** via the `reachy-deploy` ritual — that is
   what puts the whole D-028 wave (name gate, max pause, late interrupt,
   `conversation.item.truncate`, the patience defaults, the token rail, the
   hardening-block verbosity section) on the robot. Manifest must still cover
   `people.v1.json` + `face_snapshots/`.
3. **Re-sync `persona.md` in the same pass** — it changed in Task 8 (the
   no-preamble line) and reaches the robot only by the operator scp + sha256
   ritual (D-016), never by the wheel.
4. Then the five new live rows: `VOICE-NAME-GATE` (talk over it without the
   name → rolls back; 「瑞奇」 → stops; 「停」 alone → stops),
   `VOICE-LATE-INTERRUPT` (long addressed sentence → resume at the cap, then
   stop when the transcript lands), `VOICE-TRUNCATE` (interrupt, then ask
   「你剛剛說到哪」), `VOICE-PATIENCE` (~1 s Mandarin mid-sentence pauses),
   `VOICE-BREVITY` (subjective, across a normal session) — plus the still-owed
   `MEMORY-LAST-CHAT`, `MEMORY-OPEN-LOOPS` and `BACKEND-CONSOLIDATE` rows from
   the sixteenth install.

## Pending verification (operator)

Twenty-six `implemented-unverified` rows in `feature_list.json` still need live
use. **Everything through the engagement-memory wave is DEPLOYED and BOOTED
(sixteenth install, first boot verified 2026-08-30) and needs only a human in
front of the camera / a listener in the room; the five new D-028 voice rows
additionally need the seventeenth install and the persona re-sync.**

**Engagement memory (three, D-027 — on the robot since the sixteenth install,
awaiting live use):**
`MEMORY-LAST-CHAT` (recognized session about an ongoing thing → 「請進入睡眠
模式」 → journal shows `Sleep summary: wrote last-chat fact for 1 person(s).`
*after* the session-shutdown lines, `people.v1.json` holds exactly one
`上次聊天（M月D日）：…` fact, and the next recognized boot calls back to it;
negative control: a dashboard stop writes no summary. **Watch on that run:** a
daemon force-kill during sleep can race the ≤8 s summarizer call, and the
failure is silent by design — judge it by the missing journal line, not by an
error); `MEMORY-OPEN-LOOPS` (live listening: does `remember` favour the open
loop over the static trait, does a cross-person fact land, and does Reachy ask
a follow-up rather than reciting the list — the persona half is testable now,
the tool descriptions after the deploy); `BACKEND-CONSOLIDATE` (backend
stopped, seed a duplicate + a contradiction on a real person, read the dry-run
diff, `--apply`, confirm `updated_at` and store position unchanged, restart the
backend and confirm the guard exits 3 on the **tailnet** bind, then the
one-shot `--import-first --apply --push-after` — that whole flow is
monkeypatch-verified only and has never driven the real robot).

**Person memory + backend (eight):** `PERSON-GREET-KNOWN` (enrolled person in
frame at boot → `Wake face check: recognized …` then `Startup greeting
personalized for <name> with K remembered fact(s).`, a named fact-referencing
greeting, no self-introduction — and listen for the accepted edge: a
`multiple_faces`/`too_far` boot speaks the *stranger* line even with an
enrolled person among the faces, corrected late by the extended window);
`PERSON-GREET-STRANGER` (unenrolled person → self-introduction, not the
empty-room greeting); `PERSON-GREET-EMPTY` (empty room → profile greeting
verbatim, wake line ≤ ~4000 ms — judge whether the ~4 s pause feels right);
`PERSON-MEMORY-AUTO` (say a fact while recognized → `Tool call: remember
person=<name> …` + `scope: person:<name>`; hear it come back next recognized
boot; global control in an unrecognized session); `ENROLL-STILL` (「記住我」 →
head visibly stops, `remember_face saved name=… samples=N` with N ≥ 2, no
`hold_still: could not …` warning, motion restored — the 0.35 s settle has
never met the real head); `BACKEND-PUSH-LIVE` (two real photos →
`scripts/selftest.py` PASS ≥ 0.363 → push → recognized without an app
restart; also the owed Mac-embed vs voice-enrollment comparability check);
`BACKEND-IMPORT` (voice-enroll → blocked push → import preview/apply →
byte-identical re-push; a voice-forgotten fact on a person under the 20-fact
cap; the merge cycle end-to-end); `ENROLL-SNAPSHOT` (a fresh 「記住我」 →
exactly one `face_snapshots/<record_id>.jpg` over ssh → import shows the
picture once, labelled display-only).

**Face (four):** `FACE-ROUTING` (「你記得我嗎」/「我是誰」 → journal shows
`who_is_this`, never `camera`; a genuinely visual question still picks
`camera`); `FACE-WAKE-EXTENDED` (nobody at boot, lean in within 8 s → late
named greeting; empty room → `window closed`); `FACE-CROSS-SESSION` (Louis,
enrolled 2026-08-26, fresh session → `recognized name=Louis score ≥ 0.363` —
the threshold's first cross-day test); `FACE-MULTI-SAMPLE` (samples ≥ 2, then
stable repeated `who_is_this`).

**Voice (eleven):** `VOICE-MINI-MODEL` (tool spread on `gpt-realtime-2.1-mini`;
revert `REALTIME_MODEL=gpt-realtime-2.1`); `VOICE-SOLO-BARGE` (cough/「嗯」
mid-reply → `barge-in rolled back; resuming reply` + `solo barge rolled back
(backchannel)`; 「停」 confirms; real interruption < ~1 s — rollback reasons
renamed by D-028); `VOICE-WAIT-FOR-USER` (TV/side talk → suppressed turns);
`VOICE-PARTY-FACE-GATE` (≥2 people: engaged-face accept, turned-away deny);
`VOICE-NOISE-REDUCTION-AB`; `VOICE-SEMANTIC-VAD-AB` (now also the
eagerness=low **patience** trial). **Five new, D-028, needing the seventeenth
install:** `VOICE-NAME-GATE` (unaddressed talk-over → `solo barge rolled back
(unaddressed)` and the reply resumes; 「瑞奇」 mid-reply → stops < ~1 s; 「停」
alone → stops); `VOICE-LATE-INTERRUPT` (a >4 s addressed sentence → the reply
resumes at the cap, then `late solo interrupt (name) on committed turn` when
the transcript lands; negative control: an idle-start addressed turn must NOT
cancel its own answer); `VOICE-TRUNCATE` (interrupt, then 「你剛剛說到哪」 — the
model must not believe it finished; journal free of `conversation.item.truncate
refused` and of `session.update rejected; retrying with legacy transcription
shape`, whose mitigation is `REALTIME_REASONING_EFFORT=off`); `VOICE-PATIENCE`
(~1 s Mandarin mid-sentence pauses no longer commit, without the robot feeling
sluggish); `VOICE-BREVITY` (subjective — length tracks content, no preambles,
no padding, and the `max_output_tokens` warning absent in normal use).

Older human rows still owed: music duck→resume with a real voice; the gated
email send with a dictated address; the five **PRD §8** demo gates; the
`move_head` body-yaw fix (`a5f682d`, unit-covered only).

## Known defects / open edges

- Live session 2026-08-29 06:00 (first family session on the fifteenth
  install): the realtime model SPOKE person facts wrong while the tool data
  was right — `who_is_this` returned 雲霓 / 「女外科醫師…興趣是自己創作歌曲」
  (score 0.679, correct), but Reachy said 「雲玲…有趣的舞蹈老師」 (wrong name
  rendering + invented "dance teacher"), then falsely blamed "先前系統給的資
  料". Storage, recognition and retrieval all correct; this is generation
  infidelity on `gpt-realtime-2.1`. FIXED 2026-08-29 (prompt): one
  grounding principle in persona.md (person info has one source — the tool
  result; say the name as written, add nothing, re-verify when corrected)
  plus the who_is_this description ending rewritten ("state as returned",
  replacing "use them naturally"). Persona synced to the robot (sha
  94d87bb0, then re-synced 2026-08-29 at **1ce532f3** with the D-027
  guidance on top — that is what the robot holds; loads at next wake); the
  description edit rides the sixteenth install. Live re-test owed.
- Same session: 「請進入睡眠模式」 misrouted to `party_mode` instead of
  `go_to_sleep` (06:01:25); a later retry slow-walked; a still-later plain
  retry worked (06:07). FIXED 2026-08-29 (prompt): both tool descriptions
  and persona sections rewritten as an intent contrast — go_to_sleep = end
  the interaction entirely; party_mode = stay awake, change participation;
  each names the other as the wrong choice. Deliberately no keyword/phrase
  lists (operator direction: the model judges intent; enumeration is
  brittle). Persona half live at next wake; descriptions ride the sixteenth
  install. Live re-test owed. **Now load-bearing beyond routing:** the
  last-chat summary (D-027) is written only when `go_to_sleep` runs, so a
  misroute to `party_mode` also costs that visit its memory.
- Same night 06:06 boot: the documented too_far edge played out live —
  wake check rounds ended `too_far` (face visible, person seated across the
  room), stranger intro spoken, extended window then recognized 小諾 (0.454)
  on round 4 and delivered the late named greeting ~5 s later. Works as
  designed (D-025 accepted edge); operator flagged the
  generic-greeting-first feel as undesirable — design judgement pending.
  Note: the 5-attempt cap ends the wake check at ~2.1 s, well inside
  FACE_WAKE_BUDGET_MS=4000 — attempts, not the budget, are the binding
  limit.

- Solo barge-in, two residual edges (recorded, not fixed): a barge starting
  during the tail drain of a done response captures no paused-response id (a
  follow-up response is treated as "the answer" and not cancelled); the
  keep-the-answer path can clip the new answer's first queued chunk.
- ~~The confirm-vs-silence startup warning compares against the `server_vad`
  silence value even under `semantic_vad`.~~ **FIXED 2026-08-30 (D-028 §8,
  commit `c8a384c`)**: the warning is now suppressed under `semantic_vad` (the
  server ignores that value entirely) and with the name gate on (the confirm
  timer commits nothing there).
- Truncate accounting (D-028 §4), accepted biases, not defects: the global
  outstanding-audio figure can only *under*-truncate; the pause path's
  device-buffer term double-counts and can over-cut up to ~1.3 s per barge; a
  multi-item reply truncates only its last item — all in the safe direction. A
  fully drained audio item keeps its id until the next item's first delta, so a
  barge in that window sends a harmless truncate at ~`duration − 300 ms`.
- Late-interrupt eligibility (D-028 §3) deliberately survives `response.done`,
  so that 「停」 over a still-draining reply is honoured; the cost is a rare
  self-cancel in an exotic ordering, healed by the response watchdog.
- Boot-gate release ceiling is `response.done` + the 3 s drain cap — can
  exceed `REALTIME_BOOT_GATE_TIMEOUT_S` by design.
- BUG (old, DEMO-1): the RPC/UI stop button clears the queue but never sends
  `response.cancel`, so playback resumes. Voice barge-in is separate.
- T11 latency: 16.8 s to spoken answer, 4.5 s of it per-call MCP session
  setup (session-reuse opportunity, deferred).
- Instance state (`.env`, `persona.md`, `memory.v1.json`, `faces.v1.json`,
  `people.v1.json`, `face_snapshots/`, credentials) lives inside
  site-packages and is wiped by every reinstall — survives only via the
  `reachy-deploy` backup/restore ritual. **The manifest now covers all of it**
  (extended this session; first exercised in the fifteenth install).
- Engagement memory (D-027), accepted limitations: a stop issued from the
  dashboard or the mobile app writes **no** last-chat summary — only the voice
  `go_to_sleep` sets `sleep_requested`, because `shutdown()` also runs for
  settings/backend restarts mid-visit; a multi-person visit gets **topic-level**
  summaries only (the transcript carries no speaker identity, and the prompt
  forbids attributing an utterance to anyone it does not name); cross-person
  links written into facts surface **one-sided**, retrieval being per-person by
  design; and `consolidate.run` assumes **exclusive** store access — the CLI's
  probe guard is the only thing providing it, so nothing else may call it while
  the server serves.
- Import-time boundary that bit twice (D-027): `reachy_companion.config` runs
  `load_dotenv(override=True)` at **import** time, so backend and CLI code must
  never import a robot module that reaches it — it would rewrite the process's
  own `OPENAI_API_KEY` as a side effect of a constant lookup. `LAST_CHAT_PREFIX`
  is restated in `backend/consolidate.py` and pinned to the robot's by a test.
- Backend sync, deliberate holes: a Mac person with ≥ 20 facts always
  projects at exactly 20, so their robot-side voice-forgets are never
  imported back; face removals are not modelled at all; re-enrollment adds a
  second display-only snapshot tile (no auto-delete).
- Daemon app-state wedge (2026-08-28): `stopping` phantom with no process —
  start/stop/restart all refuse; only a daemon restart or the OFF→ON cycle
  clears it. Watch whether it recurs after clean stops (it followed the
  fourteenth install's `Motor communication error` stop).
- Accepted, not defects (D-014): unauthenticated console + `/rpc` on
  `0.0.0.0:7860`; idle policy moves after 180 s. The Mac backend (8710) is
  the same trusted-network posture, tailnet-bound by operator authorization.
- GPIO23 EMI spurious-shutdown risk (upstream `reachy_mini#1109`): heavy
  restart cycles are the risk window; masking the service is recommended
  against.
- Deploy lessons: never wrap bulk `scp` in `expect`; macOS dev env must be
  Python 3.12.
- `reachy_companion/uv.lock` untracked and does not re-resolve; left as-is.

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
- **2026-08-29** — operator reboot cleared the wedge; **fifteenth install's
  first boot verified** (persona, 41 tools, 4 people, 2107 ms wake check,
  empty-room greeting branch, boot gate released, 0 tracebacks); app left
  running; PERSON-GREET-EMPTY journal half recorded as passed. First family
  session found two prompt defects (fact infidelity, sleep/party misroute),
  both fixed in prompt and the persona re-synced. Then the **engagement-memory
  wave**, 9 tasks on branch `engagement-memory`, **D-027** (1468/30 + 267):
  last-chat callback written at sleep, open-loop prompt guidance, and the
  operator-run backend consolidation CLI — implemented and reviewed, **not
  merged and not deployed**; the sixteenth install is what puts the robot half
  on the device.
- **2026-08-30/31** — `engagement-memory` merged and the **sixteenth install**
  deployed and booted clean; then the **human-like-conversation wave**, 9 tasks
  on branch `name-gate-patience`, **D-028** (1468/30 → **1569/30**): name-gated
  solo barge-in, a bounded unaddressed pause with a late-interrupt catch,
  `conversation.item.truncate` on every committed interruption, patience
  defaults (VAD silence 1000, confirm 1600 gate-off-only, `reasoning.effort`
  pinned low), a 900-token runaway rail, and prompt-taught verbosity
  calibration — implemented and reviewed, **not deployed**; the seventeenth
  install plus a persona re-sync is what puts it on the device.
