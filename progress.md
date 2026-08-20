# Progress

## Current verified state (2026-08-19 — PRD-vs-code audit closed, fixes landed)

Suite: **622 passed / 30 skipped / 0 failed**; ruff + mypy strict green.
SDD ledger: `.superpowers/sdd/2026-08-16-reachy-mini-poc/progress.md`.

VoiceFX rebuilt (2026-08-20, **D-017**): the operator's "full of static noise"
was diagnosed against the shipped code, not guessed. Two causes, and the first
made the second. The "ring modulator" was never ring modulation — an
interpolated mix makes it a 6 dB **tremolo**, and its 55 Hz carrier sat at 0.956
of the psychoacoustic roughness peak, adding **+23 dB** of 30-120 Hz envelope
energy at zero gain and zero clipping. That tremolo cost exactly -2.26 dB of
RMS, which is why the makeup gain was +5 dB — and +5 dB into a hard clip pinned
**3.3 %** of samples on a -1 dBFS speech signal and overshot the downstream
24 k -> 16 k resample into a second clip. Four other hypotheses (int16 wrap,
chunk seams, WSOLA artefacts, ring-mod overshoot) were measured and ruled out.
The AM stage is now **off by default** and its carrier gated to `{0}` and
`[150, 4000]` Hz; a **feedback comb** (4 ms / g 0.45 / mix 0.35, 250 Hz spacing)
carries the robot character and measures *cleaner* than the untreated signal
because it is LTI; a stateless **soft-knee saturator** at a -1 dBFS ceiling
replaces the hard clip, which stays as a proven no-op backstop. Pitch default
+4 -> +5 st, pitch code unchanged. Result on the same phantom: RMS -8.80 ->
-6.77 dBFS (louder), clipped samples 3.29 % -> 0.00 % at every input level from
-20 dBFS to full scale, roughness -16.3 -> -40.5 dB, latency delta zero, CPU
~15 % -> ~16 % of one robot core. The whole chain is byte-exactly chunk
invariant. Also fixed a latent int16 wrap in `streaming.audio_to_int16`.
**Not yet heard on the robot** — the ear-tuning pass below is now the check that
matters, and `.env.example` carries three paste-able settings blocks for it.

Persona externalized (2026-08-20, **D-016**, operator-requested): a `persona.md`
in the instance directory — beside `.env`, same parser as `profile.md`, every
field optional — replaces the built-in locked persona at app start, so the
character can be rewritten over SSH and taken live with an antenna touch instead
of a redeploy. Anything wrong with the file falls back to the built-in persona
whole with a WARNING, and one INFO line (`persona: …`) names the source in use.
It is user state: the deploy skill's backup/restore ritual now covers it, and a
redeploy that skips that step silently reverts the character. Not yet exercised
on the robot — it ships with the next deploy.

Face-pipeline research round (2026-08-19, **D-015**): the D-013 carry-overs were
measured rather than guessed, and three changes landed. int8 quantization is
**rejected** — the CM4's Cortex-A72 has no dot-product instructions, so ORT's
int8 path is slower (OpenCV zoo's own Pi 4 bench: 27 % slower than fp32).
Alignment now uses **five landmarks**, recovered by re-parsing the mouth corners
YuNet already computes and the SDK's parser discards, which lets the default
threshold move to OpenCV's published **0.363** for this model; any face enrolled
before this change must be re-enrolled. The SFace session runs on **3 intra-op
threads with busy-spinning disabled** (one short burst per wake; the SDK's YuNet
detector is untouched) — 17.4 → 8.3 ms per embed on the dev box. And the wake
check now looks at up to **3 frames** inside the unchanged 1200 ms budget, first
confident hit wins. All three are unit-covered; the robot numbers are still the
ones only a live pass can produce.

Audit round (2026-08-19): a five-auditor adversarial review compared
`docs/PRD.md` against the code. **Six defects fixed** in commit `a5f682d`, each
unit-covered: the background-tool wedge guard at both call sites (a raise before
the dispatcher used to kill the task silently, leaving the model's `call_id`
unanswered and the turn wedged), per-server MCP discovery isolation (one
malformed server entry no longer disables every other server's tools), the
`move_head` body-yaw arguments, the face-tool `reason` field closed to a
seven-member machine-code contract (raw exception text is logged locally and
never reaches the model), dead package data, and a dead env key. The PRD and
both READMEs were then amended to as-built accuracy — face-privacy scope, the
wake check's 1.2 s greeting delay, tracking released while Reachy speaks,
emotions playing from a frozen anchor, the honest barge-in mechanism, the
deployment shape, and US-07's tense. Operator rulings recorded as **D-014**:
the unauthenticated local console/RPC exposure is accepted as-is for a home
POC, the idle-motion policy is accepted as personality, and Notion is deferred.

**Deployed 2026-08-19: the robot now runs `9188a15`** (audit fixes `a5f682d` +
D-015 face-pipeline tuning). On-robot evidence: embed median 141.7 ms at 3
threads (was 239/362 ms), face memory ready 787 ms warm with threshold 0.363
live, 17 tools registered, multi-frame wake check ran live (2× ~295 ms rounds,
budget expiry clean, greeting on time), VoiceFX chain up, Chinese greeting
spoken. Robot left ASLEEP, `startup_app` set. The `move_head` body-yaw fix
still needs a human-visible on-device check (ask Reachy to look left/right).

Task 17 (commits `1d7eaa0` + review fixes `0fac21a`): **D-013** face memory — Reachy enrolls a face by
name (`remember_face`), answers "我是谁" (`who_is_this`), and greets a
recognized person by name at wake time. Reuses the SDK's YuNet detector and
adds SFace fp32 with **zero new dependencies**; the app venv has no cv2, so
alignment is a numpy Umeyama warp. Separate `faces.v1.json` store, now in the
deploy backup/restore ritual. 15 → **17 tools**. Redeployed and verified on the
robot twice (feature, then the review fix round); robot left ASLEEP. Evidence:
`.superpowers/sdd/2026-08-16-reachy-mini-poc/task-17-report.md`.

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
- D-011 carry-overs: pitch chain 63.6 ms peak accepted under the revised 70 ms
  budget (D-011) — a soxr block-buffering spike; standing delay is ~40 ms — and
  it costs **14.8 % of one robot core** while the assistant speaks (1.3 % on the
  dev box). D-017 adds the comb and the saturator on top: latency delta zero
  (neither has lookahead or state that delays), CPU ~+1 % of one robot core.
- D-017 carry-over: every number behind the rebuild comes from a **synthetic**
  speech phantom, not real `gpt-realtime-2.1` output. The fix is
  level-independent by construction (0 clipped samples from -20 dBFS to full
  scale), so the conclusion does not rest on the crest factor — but whether
  250 Hz comb spacing reads as "cute robot" by ear is a judgement only the
  operator pass can make.
- D-012 carry-over: `memory.v1.json` lives inside site-packages and survives
  redeploys only because the skill backs it up. Moving it to `XDG_DATA_HOME`
  (already supported by `memory_path_for_instance`) would orphan any existing
  file — operator call. `faces.v1.json` (D-013) has exactly the same shape.
- **D-013 carry-over, needs a human**: the robot is a Pi **CM4**, not the Pi 5
  the plan assumed, so one SFace embed costs **239 ms idle / 362 ms with the app
  running** (plan: 50-70 ms). The wake check measured 305 ms with nobody in
  frame and should land ~600-700 ms with a face — inside the 1200 ms budget,
  with less margin than planned. **Acted on in D-015**: the session now runs at
  3 intra-op threads with spinning off; int8 is rejected outright. The remaining
  lever is `FACE_WAKE_BUDGET_MS`, and the new per-round timings need a live pass.
- **D-013 carry-over, superseded by D-015: the threshold is 0.363 (OpenCV's own),
  still unconfirmed against real faces.** Synthetic crops cannot validate it —
  out-of-distribution inputs collapse into
  a narrow cone (two unrelated noise crops scored 0.87 on-robot). Only a live
  session with two people produces the numbers: every score is logged by
  `who_is_this` and the wake check, so tune `FACE_MATCH_THRESHOLD` /
  `FACE_MATCH_MARGIN` from those.
- Audit carry-over: the `move_head` body-yaw fix (`a5f682d`) is unit-covered but
  has never run on the robot — it needs on-device confirmation at the next
  deployment.
- Accepted, not defects (D-014, 2026-08-19): the local console + `/rpc` on
  `0.0.0.0:7860` is unauthenticated and can make Reachy speak, mute the mic and
  rewrite settings; the idle policy plays a spontaneous dance/emotion/head turn
  after 180 s of silence. Both stay as they are for the POC and are documented
  in PRD §12.7.

## Waiting on operator

1. Mic pass (2 min): Chinese multi-turn, voice barge-in, ~1 s pause,
   VoiceFX ear-tuning (`scripts\dev_daemon.ps1` + `scripts\run_app_dev.ps1`).
   **This is now the D-017 acceptance check**: the static should be gone and the
   voice should read as metallic rather than buzzy. If the comb reads as "phone
   on speaker" rather than "tin robot", paste the "plain pitched voice" block
   from `.env.example` to hear the pitch stage alone, or the "more metallic"
   block for a stronger colour. Confirm from the startup INFO line which chain
   is actually running.
2. Live mic pass on the build **currently installed on the robot** (`9188a15`;
   WSOLA pitch, 17 tools, face memory with 5-point alignment). Wake it with an antenna touch —
   `startup_app` is `reachy_companion` — and listen for: pitch still cute at
   natural pace; `remember`/`forget` used at the right moments; no audible
   tail bleeding across turns (a ~48-63 ms carry is known and expected to be
   inaudible — if it is not, a response-end flush is the fix).
3. **Face memory, the pass only a human can run** (D-013): say 「记住我，我叫X」
   → then 「我是谁?」 in the same session → stop the app, restart, stand in front
   → the *greeting itself* should use the name. Then a second person, who must
   come back `unknown` rather than guessed. Report the cosine scores from the
   log; they are the calibration data for `FACE_MATCH_THRESHOLD`. Since D-015
   the default is **0.363** — OpenCV's own published threshold for this model,
   now valid because the alignment reproduces `alignCrop` — so this pass
   *confirms* a number rather than discovering one. Enroll fresh: any face
   stored before D-015 used the old three-point warp and is not comparable.
4. Home Assistant credentials → Task 13.5. Notion MCP is **deferred by operator
   decision** (D-014); the web-search MCP Space already satisfies F-K3, so
   US-07's mechanism is proven without it.

## Next after operator items

Redeploy so the robot carries the audit fixes (`a5f682d`) and confirm `move_head`
on-device → final whole-branch review (most capable model, per SDD) → Task 15
on-robot five-demo gate → close feature_list with evidence.
