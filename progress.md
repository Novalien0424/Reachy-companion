# Progress

## Current verified state (2026-08-18 — operator round 2 shipped to the robot)

Suite: **443 passed / 30 skipped / 0 failed**; ruff + mypy strict green.
SDD ledger: `.superpowers/sdd/2026-08-16-reachy-mini-poc/progress.md`.

Operator round 2 (commits `72c242c`, `7f68edc`): **D-011** replaced the
chipmunk resample trick with a streaming numpy WSOLA time-stretch composed with
soxr — pitch +4 st, **duration preserved** (35.0 ms lookahead, THD proxy
≤ 2.1e-4, bit-exact across chunkings), and the profile's "语速放慢" line is
gone. **D-012** enabled the upstream `remember`/`forget` tools (13 → **15
tools**) and made the `reachy-deploy` skill back up and restore
`memory.v1.json` alongside `.env`, since the instance path lives inside
site-packages. Redeployed and verified on the robot; robot left ASLEEP.
Evidence: `.superpowers/sdd/2026-08-16-reachy-mini-poc/operator-round-2-report.md`.

Tasks complete through implementer → review → fix rounds → re-review:
1 (scaffold, mcp<2, green baseline), 2 (dev daemon on :8001), 3 (recovered-
handler study), 4 (resample helper), 5 (OpenAIRealtimeHandler on
gpt-realtime-2.1 + soxr streaming + tunable VAD), 6 (wiring +
tracking-on-startup + zh), 7 (Chinese locked profile), 8-automated (LIVE
session verified: 234 ms response, ~1 s first audio, RPC say/interrupt,
VoiceFX active), 9+10+11-automated (emotion→dance3 chain, camera→input_image
accepted by the model, auto web-search with real zh results; US-02
dev-verified), 12-unit (MCP seam, bounded discovery), 13-unit (home_control
with HA_ENTITIES allowlist), 14 (adding-a-skill guide, skeleton executed),
16 (VoiceFX engine-free chipmunk+ringmod, Codex-reviewed amendment).

Robot deployment (Task 15 prep): robot OFFLINE at attempt 1 — nothing
touched; wheel built and verified pure; all 43 deps wheel-resolve for
aarch64; deploy procedure corrected from SDK source (D-009: version route =
`/update/install-source`, `--no-deps` two-step install, `.env` wiped on every
reinstall, version gate is decisive because the daemon force-syncs apps_venv).

## Known defects / open decisions

- BUG (tracked in DEMO-1 next): RPC/UI stop button clears the queue but
  never sends `response.cancel` — playback resumes. Voice barge-in is the
  separate server-side path (expected working; operator confirms).
- Demo behavior call needed: "恭喜我吧" → SILENT 18.4 s dance
  (play_emotion needs_response=False). Cute or confusing? Operator decides.
- Dev-box only: mockup-sim picks the IR camera (black frames) and its face
  endpoint is structurally dead — vision/face rehearsals need the robot.
- T11 latency: 16.8 s to spoken answer; 4.5 s is per-call MCP session setup
  (session-reuse opportunity, deferred).
- D-011 carry-overs: pitch chain peak latency 63.6 ms vs the ~60 ms budget
  (soxr block-buffering spike; standing delay is ~40 ms), and it costs **14.8 %
  of one robot core** while the assistant speaks (1.3 % on the dev box).
- D-012 carry-over: `memory.v1.json` lives inside site-packages and survives
  redeploys only because the skill backs it up. Moving it to `XDG_DATA_HOME`
  (already supported by `memory_path_for_instance`) would orphan any existing
  file — operator call.

## Waiting on operator

1. Mic pass (2 min): Chinese multi-turn, voice barge-in, ~1 s pause,
   VoiceFX ear-tuning (`scripts\dev_daemon.ps1` + `scripts\run_app_dev.ps1`).
2. Live mic pass on the **new build already installed on the robot** (round 2:
   WSOLA duration-preserving pitch, 15 tools). Wake it with an antenna touch —
   `startup_app` is `reachy_companion` — and listen for: pitch still cute at
   natural pace; `remember`/`forget` used at the right moments; no audible
   tail bleeding across turns (a ~48-63 ms carry is known and expected to be
   inaudible — if it is not, a response-end flush is the fix).
3. Notion MCP + Home Assistant credentials → Tasks 12.5 / 13.5.

## Next after operator items

Final whole-branch review (most capable model, per SDD) → Task 15 on-robot
five-demo gate → close feature_list with evidence.
