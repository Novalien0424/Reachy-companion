# Progress

History compressed 2026-08-27; full narrative in git history of this file.

## Current state

The robot runs the **voice-robustness build** — thirteenth install, 2026-08-27
00:09–00:12 BST, commit `b4e154f`, wheel sha `95754c6a…` verified end to end,
two-step `--no-deps` install, manifest-driven backup/restore at
`/tmp/reachy_companion_backup/20260826T230937Z-1448` (persona sha `8d3d01e8`
preserved, `faces.v1.json` 1 record survived, `memory.v1.json` recorded absent,
google-workspace-mcp 1 file), assets preloaded, app discovered.

Live on-robot evidence from the persistent journal, zero tracebacks:
`persona: instance persona.md`; **41 tools** in-session including
`wait_for_user`; VoiceFX chain + coral voice intact; **no** `session.update
rejected; retrying with legacy transcription shape` warning, so the live API
accepted `gpt-transcribe` + keywords (`VOICE-TRANSCRIBE-MODEL` → verified);
greeting queued 00:11:19 → spoken 00:11:22 → `session turn_detection updated:
party=False` → `boot gate released (greeting played)` 00:11:24
(`VOICE-BOOT-GATE` → verified, quiet-room variant), with no confirm-vs-VAD race
warning. Clean stop; robot left **app-stopped** with
`startup_app=reachy_companion`, volume 90, daemon 1.10.0rc5.

Gate at that install: suite **1319 passed / 31 skipped**, ruff clean, mypy
strict clean. `DECISIONS.md` **D-023** summarizes the round;
`docs/research-realtime-voice-best-practices.md` is its spec, and
`docs/multi-person-investigation.md`'s 2026-08-25 addendum maps the eight tasks
onto §8's ranked recommendations (item 7, a richer interaction-state gate, is
deliberately still open).

**Not on the robot yet — the face-recognition fix wave.** Branch
`face-recognition-fix` (commits `143b551`…`ece7a58`) is implemented and
gate-green here: suite **1348 passed / 30 skipped**, ruff clean, mypy strict
clean. It ships identity routing to `who_is_this` (tool descriptions + locked
profile + `persona.md`), the margin rule restricted to candidates that clear the
threshold, largest-face identification (the SDK tracker's own rule) while
enrollment still demands exactly one face, capture/identify retries plus
up-to-3-sample enrollment, a bounded post-greeting extended wake window
(`FACE_WAKE_EXTENDED_MS`, default 8000, 0 disables, closes silently once the
user speaks), the `arcface5` alignment marker with unmarked records
grandfathered, an enrolled-count in the ready log, `internal_error` for
malformed embeddings, and an SDK-pinned YuNet preload. RCA behind it: 14/14 boot
wake-check failures since Aug 24, 「是誰。」 routed to `camera` in the 2026-08-24
party transcript, while the recognizer itself measured healthy (same-session
0.594 vs threshold 0.363; cross-person Lena↔Louis 0.1446). Plan:
`docs/plans/2026-08-27-face-recognition-fix.md`; record: `DECISIONS.md`
**D-024**. **No on-robot evidence — deploy is the next action**, and the four
`FACE-*` rows below are the live gate.

## Wake-up / power diagnosis (2026-08-27)

**"Hard to wake up" = the robot is sometimes OFF — undervoltage hard
power-loss, two days running.** Journal: the boot of Aug 25 ends at 13:53:02
with `hwmon: Undervoltage detected!` as its final line; the boot of Aug 26 ends
identically at 16:30:51 — then nothing until a human powers it back on (Aug
26's death left it dark ~7.5 h; tonight's session found uptime 18 min). No
`Shutdown button released` lines anywhere, so this is **not** the GPIO23 EMI
clean-shutdown path from the 08-24 investigation — it is the 5 V rail sagging
below threshold. On Aug 26 the app was not even running (stopped cleanly at
13:32; daemon-only load killed it). Software cannot fix this; `startup_app` is
set correctly and the wake path works whenever the robot has power.

**Cause is ambiguous between three power stories — a draining battery produces
the exact same kernel line** (the hwmon watches the 5 V rail, not the reason it
sagged; this design has no battery-status API, only a low-battery LED).
Candidates: (a) weak PSU/cable while plugged; (b) unplugged and the battery ran
out (Aug 26's boot lasted ~5 h — plausibly one charge; Aug 25's ~23.5 h run
rules battery-only out for that day); (c) charging path under-delivering.
**Operator observation (2026-08-27): it died while/when plugged into the
ORIGINAL manufacturer adapter** — which eliminates "wrong PSU" and narrows to a
degraded/marginal official adapter or cable, a charging-path/power-board fault,
or (if the death coincided with the *moment* of plugging in) a battery↔DC
switchover glitch that dips the rail on hot-plug. No matching public issue in
`pollen-robotics/reachy_mini` (searched 2026-08-27) — worth an upstream report
with our two journal lines once one more occurrence pins the pattern.

Triage next occurrence: note whether death was AT plug-in (switchover glitch →
plug in only while the robot is off/before boot) vs minutes-to-hours later
while plugged (adapter/charging path → swap in a known-good 5 V/5 A USB-C PSU +
short cable and see if it survives the same duty). Live check while plugged:
`vcgencmd get_throttled` (bit 0 = sagging now, bit 16 = sagged since boot; 0x0
at idle tonight) — re-read after a loud motors+speaker session. Habit: robot
seems "hard to wake" → **look at the power LED first**; dark = power event, not
software. The next occurrence again prints `Undervoltage detected!` as the
boot's last line.

## Pending verification (operator)

Ten `implemented-unverified` rows in `feature_list.json` still need live use.
**Face (four, this round, all gated on the deploy):** `FACE-ROUTING` (ask 「你記得
我嗎」 and 「我是誰」 — journal must show `tool_name='who_is_this'` and no `camera`
call on those turns, while a genuinely visual question still picks `camera`);
`FACE-WAKE-EXTENDED` (boot with nobody in frame, lean in within 8 s → `Extended
wake face check: recognized … queued a late named greeting`; an empty room must
instead end `window closed`, proving the bound); `FACE-CROSS-SESSION` (Louis,
enrolled 2026-08-26, asks 「你認得我嗎」 in a fresh session → `who_is_this
status=recognized name=Louis score=…` ≥ 0.363 — the D-015 threshold's first live
cross-day test); `FACE-MULTI-SAMPLE` (`remember_face saved name=… samples=N`
with N ≥ 2, then repeated `who_is_this` calls with the person stationary and no
spurious `no_face`). **Voice (six):** `VOICE-MINI-MODEL` (exercise a spread of
tools on `gpt-realtime-2.1-mini`,
watch for misrouted/malformed calls; `REALTIME_MODEL=gpt-realtime-2.1` is the
one-line revert); `VOICE-SOLO-BARGE` (multi-turn, deliberately cough / say 「嗯」
mid-reply — the sentence should finish with `barge-in rolled back; resuming
reply`, 「停」 should confirm, a real interruption still land in <~1 s);
`VOICE-WAIT-FOR-USER` (TV on or a side conversation — count `wait_for_user:
model chose not to respond` against turns that should have been suppressed);
`VOICE-PARTY-FACE-GATE` (≥2 people: one engaged-face accept, one
turned-away/stale-face deny); `VOICE-NOISE-REDUCTION-AB`
(`REALTIME_NOISE_REDUCTION=off/near_field/far_field`, same noisy room, same
script); `VOICE-SEMANTIC-VAD-AB` (`REALTIME_VAD_TYPE=server_vad` vs
`semantic_vad` + `REALTIME_VAD_EAGERNESS=low` on Mandarin backchannels).

Older human rows still owed: music duck→resume with a real voice (the machinery
now demonstrably drains); the full gated email send with a dictated address; the
five **PRD §8** demo gates. Also unconfirmed on-device: the `move_head`
body-yaw fix (`a5f682d`, unit-covered only). The D-015 threshold **0.363** now
has same-session live evidence (0.594 hit, 0.1446 cross-person rejection); its
remaining open question is the cross-day case, which `FACE-CROSS-SESSION`
owns — every score is logged by `who_is_this` and the wake check.

## Known defects / open edges

- Solo barge-in, two residual edges (recorded, not fixed): a barge beginning
  during the *tail drain* of an already-done response captures no
  paused-response id, so a response starting during that pause (e.g. a
  tool-call follow-up of the old turn) is treated as "the answer" and is not
  cancelled — the robot can talk through that barge; and on the keep-the-answer
  path the queue flush can clip the new answer's first already-queued chunk.
- The confirm-vs-silence startup warning compares against the `server_vad`
  silence value even under `REALTIME_VAD_TYPE=semantic_vad`, where that knob is
  not sent.
- Boot-gate release ceiling is `response.done` + the 3 s drain cap, which can
  exceed `REALTIME_BOOT_GATE_TIMEOUT_S` — by design, but that env is not a hard
  upper bound.
- BUG (old, DEMO-1): the RPC/UI stop button clears the queue but never sends
  `response.cancel`, so playback resumes. Voice barge-in is a separate path.
- T11 latency: 16.8 s to spoken answer, 4.5 s of it per-call MCP session setup
  (session-reuse opportunity, deferred).
- `.env`, `persona.md`, `memory.v1.json` and `faces.v1.json` live inside
  site-packages on the robot and are wiped by every reinstall — they survive
  only via the `reachy-deploy` backup/restore ritual.
- Accepted, not defects (D-014): the local console + `/rpc` on `0.0.0.0:7860`
  is unauthenticated (can make Reachy speak, mute the mic, rewrite settings);
  the idle policy plays a spontaneous dance/emotion/head turn after 180 s.
- GPIO23 EMI spurious-shutdown risk (upstream `reachy_mini#1109`): heavy
  back-to-back app restart cycles are the risk window, normal conversation much
  lighter; `systemctl mask gpio-shutdown-daemon` is recommended against.
- Deploy lesson: **never wrap a bulk `scp` in `expect`** — it stalls
  indefinitely (5 reproductions); plain key-authenticated `scp` moves the same
  wheel in 0.66 s (the Mac's ed25519 key is authorized on the robot).
- The macOS dev env must be Python 3.12 (`uv venv --python 3.12`, the robot's
  version): on 3.11 one realtime test wedges and numpy stubs raise two mypy
  false positives in `streaming.py`.
- `reachy_companion/uv.lock` is untracked and does not currently re-resolve
  (yt-dlp against the pyproject `exclude-newer` window); left as-is.

## History digest

- **2026-08-16** — scaffold from the official conversation app → OpenAI
  realtime handler on `gpt-realtime-2.1` → Chinese locked profile (D-001…D-008).
- **2026-08-17/18** — first robot deploys (D-009), VoiceFX (D-010), WSOLA
  pitch (D-011), memory tools (D-012), face memory (D-013).
- **2026-08-19** — PRD-vs-code adversarial audit, six defects fixed
  (`a5f682d`), operator rulings D-014; D-015 face-pipeline tuning deployed
  (`9188a15`).
- **2026-08-20** — D-016 persona externalized to instance `persona.md`; D-017
  VoiceFX rebuild (comb + soft knee replace tremolo + hard clip).
- **2026-08-21/22** — D-018 HomeAssistant-Nova port (22 tools, 39 total),
  eighth install and enabled pass, two live-found fixes (`dd591f2`, `c4e1951`),
  latency work (music 15.8 s, NAS skip 0.3 s), D-019 static tool array, D-020
  handoff disclosure.
- **2026-08-23** — persona v2 audit + deploy, 13-version voice audition →
  coral V13 baked in (D-021), persistent journald.
- **2026-08-24** — music loudness + resume-parked fixes (ninth/tenth install);
  flaky-connection verdict (the Mac-side `expect` wrapper, not the robot);
  shutdown traced to the GPIO23 EMI defect; D-022 TV cast entity churn;
  multi-person hardening T1–T3 (twelfth install).
- **2026-08-25** — voice-robustness round, eight tasks, **D-023**.
- **2026-08-27** — thirteenth install (`b4e154f`) live-verified; undervoltage
  power diagnosis above; face-recognition RCA + fix wave, **D-024**, on branch
  `face-recognition-fix` (not yet deployed).
