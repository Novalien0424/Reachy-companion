# Progress

History compressed 2026-08-29 (previously 2026-08-27); full narrative in git
history of this file.

## Current state

**Fifteenth install is ON the robot; its first boot is pending an operator
reboot.** Deployed 2026-08-28 ~22:35–22:45 local (Mac), commit `ad5fe3e`
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

**Why boot verification did not run:** the daemon's app tracker was wedged —
`current-app-status` stuck at `state: "stopping"` with **no app process
running** (verified over ssh), start refused ("an app is already running"),
stop/restart answered "no app is currently running", app lock free, daemon
itself healthy (control loop ~50 Hz, motors disabled/asleep, uptime 2 days).
Likely the residue of the fourteenth install's odd stop (`Motor communication
error` on the stop API). Per D-009 the daemon is untouchable; Pollen's own
troubleshooting prescribes OFF → 5 s → ON. **The operator is rebooting over
ssh; the next session verifies the boot journal** — expected lines: `persona:
instance persona.md`, 41 tools, `Face memory ready: … 4 people enrolled`, the
new ~4 s wake check (`FACE_WAKE_BUDGET_MS=4000`, 5 attempts), and one of the
three greeting branches (`Startup greeting personalized for …` /
stranger-intro prefix / profile greeting verbatim).

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
background task); restart with that one-liner from `companion_backend/`.

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

## Pending verification (operator)

Eighteen `implemented-unverified` rows in `feature_list.json` still need live
use — **all robot-side rows are now DEPLOYED (fifteenth install) and gated
only on the post-reboot boot + a human in front of the camera.**

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

**Voice (six):** `VOICE-MINI-MODEL` (tool spread on `gpt-realtime-2.1-mini`;
revert `REALTIME_MODEL=gpt-realtime-2.1`); `VOICE-SOLO-BARGE` (cough/「嗯」
mid-reply → `barge-in rolled back; resuming reply`; 「停」 confirms; real
interruption < ~1 s); `VOICE-WAIT-FOR-USER` (TV/side talk → suppressed turns);
`VOICE-PARTY-FACE-GATE` (≥2 people: engaged-face accept, turned-away deny);
`VOICE-NOISE-REDUCTION-AB`; `VOICE-SEMANTIC-VAD-AB`.

Older human rows still owed: music duck→resume with a real voice; the gated
email send with a dictated address; the five **PRD §8** demo gates; the
`move_head` body-yaw fix (`a5f682d`, unit-covered only).

## Known defects / open edges

- Solo barge-in, two residual edges (recorded, not fixed): a barge starting
  during the tail drain of a done response captures no paused-response id (a
  follow-up response is treated as "the answer" and not cancelled); the
  keep-the-answer path can clip the new answer's first queued chunk.
- The confirm-vs-silence startup warning compares against the `server_vad`
  silence value even under `semantic_vad`.
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
