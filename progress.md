# Progress

## Music loudness + resume unparked; ninth/tenth install (2026-08-24)

Operator report: YouTube music much quieter than the voice, "volume small
afterward". Root-caused with the new persistent journal, three real defects,
each fixed test-first (suite 1178/31, ruff+mypy green, deployed and verified
live on the robot):

- **Music ~12 dB down**: the test track peaked at −12.1 dBFS vs the voice's
  −1 dBFS ceiling (YouTube loudness normalization). `ytdlp.normalize_loudness`
  now rewrites fresh native downloads *and* pre-existing cache hits once as a
  gain-matched mono WAV peaking at −1 dBFS (decode-only, ~150× realtime on the
  CM4, ~2 s/song — the removed mp3 encode cost ~10 s). Gag mp3 mode untouched;
  resume cuts inherit `.wav`; prune already marker-based. On-robot: the
  operator's track measured max −1.0 dB after upgrade and played audibly loud.
- **Resume parked forever** (the "still waiting … 0.08s outstanding" residual,
  now user-visible): TWO stacked causes. (a) `wait_for_item` answers an idle
  output queue with None after 0.1 s, so play_loop's 0.5 s timeout branch —
  the only site calling `note_queue_empty` — was unreachable during a live
  session; `_QUEUE_EMPTY` stayed False from the first audio frame on. The None
  emission now records the queue-empty fact itself (console.py). (b) The play
  loop holds the final partial sink chunk back, leaving a 0.03–0.08 s
  enqueue-vs-sink residue; `_is_drained` now tolerates ≤ 0.25 s when the
  queue is empty. On-robot after the fix: zero waiter lines over a full
  play/chat/stop session (was 5+/min, forever).
- **"Volume small afterward"**: no code path changes volume; system volume
  stayed 90 through the operator's whole test (journal-verified). Most likely
  the loudness contrast with the then-quiet music and/or the model speaking
  softly at 1:40 AM. Watch after the music fix; VOICEFX_GAIN_DB is the lever
  if the voice itself ever reads quiet.

Deploy notes: bulk scp to the robot stalled repeatedly (same flaky radio as
the #1115 incident) — wheels now transfer robot-pull over HTTP from the Mac
(python3 -m http.server + curl on the robot, sha-verified; ~1 s). Full ritual
both installs: manifest backup/restore, two-step --no-deps install, persona
sha + 7 VOICEFX lines verified after restore. A start-app without stop is
silently a no-op ("already running") — the first verify round ran old code
because of it; stop→poll null→start is the sequence. Robot left ASLEEP,
startup_app set, volume 90. Human rows still owed: duck→resume with a real
voice (the machinery now demonstrably drains), plus the PRD §8 gates.

## Voice picked and baked in: coral V13 (2026-08-23, evening — D-021)

The 13-version live audition ran end to end on the robot; the operator picked
**V13 "coral robot-3"** (coral, pitch +5 st, comb 2.0 ms/0.62/0.55, ring-mod
250 Hz/0.16). Baked in: `voice = "coral"` front matter in the tracked
`persona.md` (deployed sha-exact, effective profile verified coral/37 tools),
VOICEFX lines written permanently into the instance `.env` (audition markers
removed), startup log confirms the exact chain. Volume 90 via the daemon API +
`alsactl store` — survived a real hard power loss, so persistent. Robot left
ASLEEP on the baked config; next wake speaks as picked.

Two incidents during the audition, both diagnosed: (1) a daemon stop wedge —
app process exited cleanly but `stop_current_app` hung in its return-to-zero
step, state stuck at "stopping", every apps route 400; power-cycle cleared it
(daemon bug, not ours; single occurrence in ~16 restarts). (2) A hard LAN
drop at ~14:26 BST — clock-file evidence points to full power/system death,
not just Wi-Fi; matches upstream reachy_mini#1115 (closed, no fix). The RAM
journal destroyed the evidence, so journald on the robot is now persistent
and capped (100 M) — next occurrence will carry its final moments across the
power cycle. Suspects, unranked: battery BMS cutoff (no battery-status API
exists — watch the low-battery LED, keep it plugged), PSU brownout under
speaker-at-90 + motor load, BCM4345 Wi-Fi + watchdog. `vcgencmd
get_throttled` read 0x0 after reboot (flags do not survive one).

## Persona v2 deployed + voice audition harness (2026-08-23)

The operator's rewritten `persona.md` (Taiwan-Mandarin character, c2e8900) was
audited against the code — every referenced tool maps to a registered tool in
the locked profile's 37-tool set; fixes in `6cced3b`: duplicate `## Tools`
sections merged, the fake `search` tool heading renamed to the real
`search_web`, Core Rule moved to the end. Deployed to the robot byte-exact
(sha `fa2536fe…` match, 15/15 routing tokens) after backing up the robot copy
(`/tmp/reachy_companion_backup/20260823T122700Z-persona`, which matched the
prior commit exactly — no robot-side edits lost). Verified through the app's
own code path on-device (`set_instance_path` + `read_profile`): persona body
active, 3225 chars, voice cedar, 37 tools. App is currently stopped with
`startup_app=reachy_companion`, so the **next wake starts fresh on this
persona**. Deploys from this Mac now use OpenSSH driven by `expect`
(no sshpass; creds from repo-root `.env`).

`scripts/voice_audition.py` (commit after) is the cute-voice audition
harness: 10 candidate configs (base voice via persona front-matter `voice`,
D-016 × VOICEFX chain via marker-scoped instance `.env` block, D-017), one
command per audition — apply, sanctioned daemon restart, fixed Taiwan-Mandarin
test line via `/rpc conversation.say`. `restore` returns the robot to the
shipped config byte-identically (dry-run proven locally). Awaiting the
operator's "go" per version; the winner gets baked into the repo `persona.md`
front matter and the instance `.env` proper.

## HomeAssistant-Nova port landed (2026-08-21 — D-018)

Twenty-two capabilities ported natively from the operator's `ha-actions` MCP
server: no MCP hop, no vendored `server.py`, no stdio lane. Every personal
identifier upstream hardcoded — calendar id, task-list id, Drive folder id,
account address, HA entity ids, NAS share and credentials — is now configuration,
and `tests/test_hanova_integration.py` fails if any of it reappears in the
package, the profile or `.env.example`. The model now sees 39 tools, up from 17.

Four cross-cutting behaviours are in code, not in the prompt: **per-tool**
enablement aggregated into one tri-state verdict line per family (the app boots
green with zero new configuration), a **tri-state** home-network probe that says
`away_from_home` only on positive off-home routing evidence and
`home_status_unknown` — doing no house work whatsoever — when Home Assistant is
merely broken, unauthorised or reached over a tunnel, a 90-second two-step
confirmation gate **scoped to the conversation and to an individual claim id**
that executes the *parked* action and spends the authorisation on success or on a
terminal failure, and metadata-only logging through one shared redaction helper
across both the tools and the new service layer.

Three new pure-wheel dependencies: `yt-dlp` 2026.8.19, `imageio-ffmpeg` 0.6.0,
`smbprotocol` 1.17.0 — all confirmed to resolve as aarch64 wheels. Media the TV
must fetch is served from the app's existing FastAPI server at `/hanova-media`,
so there is no new port and no second web server.

Three upstream behaviours are declared non-goals rather than quietly dropped:
Drive restore, email BCC, and the generic confirmation summary for the
self-destruct gag (whose in-character ritual, TTL and abort word are kept).

The deploy ritual was hardened in the same pass: the robot's LAN address, SSH
user and host-key fingerprint are out of the tracked `reachy-deploy` skill and
into the gitignored repo-root `.env` (`REACHY_HOST`, `REACHY_SSH_USER`,
`REACHY_SSH_PASSWORD`, `REACHY_HOSTKEY`), documented by a placeholder-only
tracked `.env.example`; and the backup/restore step now covers the three new
instance files (`google-workspace-mcp/<account>.json`, `google-oauth.json`,
`nas-video-index.json`) with a per-deployment backup directory and a redacted
manifest, so a stale copy from an earlier deploy can no longer overwrite a file
that is legitimately absent.

Suite: **1118 passed / 30 skipped / 0 failed**; ruff + mypy strict green.

**DEPLOYED AND VERIFIED ON THE ROBOT — 2026-08-22, from the Mac mini** (eighth
install; Task 15 resumed at Step 4 per `session-handoff.md`, OpenSSH instead of
plink). Version gate passed (daemon 1.10.0rc5, git ref); wheel sha256 verified
end to end; two-step `--no-deps` install pulled exactly the three new aarch64
wheels and the bundled ffmpeg binary runs on-device; backup/restore ritual run
with the new manifest (both JSON stores recorded absent — no live enrollment
yet); the committed `persona.md` deployed byte-exact (sha match, 10/10 routing
tokens); six missing keys appended as empty placeholders only (operator's 20
real keys untouched, names checked, never values).

On-robot verification: **22/22 ported tools registered within one startup
boundary, secondary count 39**; seven family verdicts — google-workspace,
drive, notion, email **enabled**; nas partial 1/4 (`HANOVA_MEDIA_HTTP_BASE`),
media-cast disabled (`HANOVA_HA_SCRIPT_YOUTUBE`), music partial 2/4
(`HANOVA_SELF_DESTRUCT_YT_ID`); `persona: instance persona.md`; D-017 VoiceFX
chain live; zero tracebacks. Media route probed from a LAN peer: HEAD 200
`video/mp4`, GET 200, RANGE 206.

A scripted RPC wake test (31 injected turns via `conversation.say`, mic muted
for determinism) exercised **every ported tool on the robot**: music
play/stop audible on the speaker; the full calendar and task cycles including
all gated deletes/completes in both directions; notion_add; drive
list/upload/trash with both gates (camera frame captured at confirm time,
then trashed — self-cleaned); the restore and BCC refusals per the declared
non-goals; email validation (no address → asks, nothing armed); and honest
`unavailable` answers from every blocked tool with the missing key as reason.
Raw transcripts and tool traces: `artifacts/deploy-2026-08-22/` (gitignored).
Robot left with the app RUNNING and mic live for the operator's voice pass.

**Enabled pass + two on-robot fixes (2026-08-22, later the same day).** The
operator authorized sourcing the six missing values from their HomeAssistant
project: the three `tv_show_*` HA scripts (verified to exist live), the two
gag clip ids, the media base and the home CIDR. After restart **all seven
families report enabled**, and the previously blocked rows all ran on the
real TV and speaker: both casts, both gags (full self-destruct ritual — cold
confirm refused, arm, abort, re-arm, authorise, clip audible), NAS query,
single-clip and whole-trip playback, and skip. Two real defects were found by
the live pass and fixed the same day, each test-first with the full gate
green (py3.12: 1127 passed / 31 skipped, ruff + mypy strict):

- `dd591f2` — the NAS source-path bound also gated the *original* file's
  extension, refusing every DVD-era trip (992/2743 index entries are
  .mpg/.mts originals whose transcoded .mp4 cast twin is the file actually
  played). Containment stays; the playability gate now applies only to the
  cast copy. Verified on-device: a 30-clip trip plays in order on the TV.
- `c4e1951` — play_music went dark mid-day: YouTube began demanding a JS
  runtime the robot does not carry, so every yt-dlp search errored. New
  `HANOVA_YTDLP_EXTRACTOR_ARGS` forwards `--extractor-args`;
  `youtube:player_client=android` verified for search and download.

**Measured latencies (scripted RPC injection, robot-side timestamps):**
talk — command→robot starts speaking ~0.9 s median (0.74–1.47 s, n=45), and
every tool call gets that same ~1 s spoken acknowledgment before the work
runs; music — command→audible ~25 s (search+download+mp3), stop→silence
~2 s; YouTube→TV ~20 s to cast dispatch; image→TV ~42 s (generation);
NAS — single clip ~17 s cold (SMB stage + cast), skip ~44 s, whole-trip
start 0.7 s warm, query instant; gags — 8–12 s to audible clip.

**Environment note:** the macOS dev env must be Python 3.12 (`uv venv
--python 3.12`) — on 3.11 one realtime test wedges (a shutdown-path event
wait exposed by darwin loop ordering) and numpy stubs raise two mypy false
positives in `streaming.py`. Both vanish on 3.12, which is also the robot's
version.

**Latency work landed (2026-08-22, evening — commits `1d1624d`, `8ac0012`,
plus the SABR-fallback fix).** First-principles pass over the two slow media
paths, every number measured on the robot:

- **Music**: the mp3 re-encode was pure waste (the daemon plays via GStreamer
  playbin; faad/opusdec verified installed) and each search candidate costs
  its own metadata fetch. `play_music` now downloads the native audio stream
  (`bestaudio…/best` with `-x` as a stream-copy demux for SABR-suppressed
  sessions) and `HANOVA_YTDLP_SEARCH_N` defaults to 2. Measured: command →
  audible **15.8 s** on a SABR-fallback video (the worst case; ~25–29 s
  before), cached replay **~9 s** (search still runs; the fetch is instant),
  stop → silence 0.16 s. Resume cuts inherit the source container; the cache
  prune recognises them by marker.
- **NAS video**: staging copied whole clips at the ~7 MB/s Wi-Fi ceiling
  (bigger SMB blocks measured no gain). New `/hanova-media/nas-stream/`
  endpoint proxies exactly the requested byte range off SMB
  (`HANOVA_NAS_STREAM=0` restores staging). Measured through the endpoint
  from a LAN peer: **61 ms to first byte**, 80 ms for a seek 200 MB into a
  clip, 4.9 MB/s sustained (≈8–15× home-video bitrate). Tool times:
  whole-trip start **0.32 s**, skip **0.27–0.30 s** (was 17–44 s). One
  caveat: the TV was OFF (cast entity `unavailable`) during these rounds, so
  the casts dispatched but no Chromecast fetch was observed — the endpoint
  was verified LAN-side with exactly the request shapes a Chromecast makes
  (HEAD, 206 ranges, mid-file seek). One TV-on replay of a trip row closes
  that.

**Prefetch and direct-NAS-serving verdicts (operator question):** next-clip
prefetch is **rejected** — it existed to hide the copy, streaming removed the
copy (skip is now 0.3 s), so re-introducing a background task against the
D-018 non-goal buys nothing. Serving the TV straight from the NAS is
**rejected** — the QNAP does run DLNA (:8200) and Plex (:32400), but DLNA
addresses content by rescan-unstable object ids behind a UPnP browse protocol
and Plex embeds an auth token in every URL; both couple our curated index to
a second server to save relay headroom we measurably do not need. Revisit
only if high-bitrate content saturates the robot's radio.

**Residual observation (harmless, worth a look next session):** after a
stop_music during live playback, the resume drain-waiter keeps logging
"music resume still waiting … 0.04s outstanding" indefinitely — stop does
not cancel the waiter and `audio_drain.outstanding_s()` never reaches zero
(the device-buffer estimate appears to expire only during playback). No
audible effect: `resume_after_speech` refuses a non-paused player
(`nothing_to_resume`), so it is log noise plus one idle task per stopped
turn, not a resurrection path.

**Still owed to close Task 15**: the human voice rows — music duck/resume,
the full gated email send with a dictated address, Chinese barge-in/VAD feel,
the home-verdict rows 31/31b/33 (robot off-LAN + broken HA token), and the
five PRD §8 demo gates. Step 15b (persona stash restore) still runs on the
Windows box only. The robot is left with the app RUNNING, all 7 families
enabled, mic live.

## Current verified state (2026-08-19 — PRD-vs-code audit closed, fixes landed)

**Superseded by the D-018 section above** for the suite numbers and the tool
count; kept for the VoiceFX, persona and face-pipeline history it records.

Suite at the time of writing: **622 passed / 30 skipped / 0 failed**; ruff +
mypy strict green.
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
redeploy that skips that step silently reverts the character. **Deployed and
verified live 2026-08-20** (build `2aa0403`, seventh install): the robot's
startup log reads `persona: instance persona.md`, seeded from the built-in
persona body — edit it over SSH, antenna-wake to reload. The same deploy took
D-017 live: the log shows the new chain (pitch +5 st, comb, AM off, soft knee)
and a full 3-round wake check in 1171 ms. The D-017 acceptance check —
does it *sound* cute and static-free — is the operator's ear, next wake.

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
