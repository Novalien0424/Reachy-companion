# Decisions

Durable implementation decisions. Each entry: context → decision → evidence.

## D-001 — Repo strategy: own app via the official scaffolder (2026-08-16)

Create our app with the SDK's official scaffolder
(`reachy-mini-app-assistant create --template conversation`), which clones the
official Conversation App, renames the package, and rewires
`pyproject.toml`/entry points (`SDK apps/fork_conversation.py:16-90`). Then
adapt in place. **Not** a git fork tracking upstream (upstream deleted the
multi-backend seam in `5b8d974`; their AGENTS.md says the app is not meant to
be forked/vendored), and **not** a library dependency (module-level
singletons + hardcoded tool discovery path make it non-importable —
`research-conversation-app.md §(c)`). The reference clones stay read-only for
diffing against upstream fixes.

## D-002 — Realtime backend: new `openai_realtime.py` handler (2026-08-16)

The app at HEAD has only the HuggingFace backend; the OpenAI handler was
deleted in `5b8d974` (recoverable: `git show 5b8d974^:src/…/openai_realtime.py`).
We keep the maintained `huggingface_realtime.py` event loop and replace only:
client build (`AsyncOpenAI(api_key=OPENAI_API_KEY)`), `model="gpt-realtime-2.1"`
in `realtime.connect`, 24 kHz `AudioPCM` format, OpenAI voice list, and tuned
turn detection. Audio resampling 16 kHz (robot) ↔ 24 kHz (model) is handled in
our handler (SDK is fixed at 16 kHz — `research-reachy-sdk.md §(b)2`).

## D-003 — Turn handling for Chinese: configurable server-side VAD (2026-08-16)

Upstream ships untuned `ServerVad(interrupt_response=True)` only. We expose
`threshold` / `prefix_padding_ms` / `silence_duration_ms` and optional
`SemanticVad(eagerness=…)` via env, default `silence_duration_ms=800` for
mid-sentence pauses (US-01), `REALTIME_TRANSCRIPTION_LANGUAGE=zh`, and a
Chinese-first profile (the default profile forces English).

## D-004 — MCP: reuse the client, replace the installer (2026-08-16)

`mcp_client.py` (streamable HTTP, auth headers, namespacing) and
`RemoteMcpTool` are complete and reused unchanged. The only blocker is the
HF-Space-locked URL validator in `tool_spaces.py:405-425`; we add a generic
env-fed config + a persistent extra-tools registration seam in
`core_tools.py` (`initialize_tools()` rebuilds the registry, so ad-hoc
registrations need the seam). First integration: Notion. Auth: hosted
`mcp.notion.com` expects OAuth/PKCE, which we will NOT build (PRD Mistake 4);
we try a static internal-integration bearer first and fall back to the
official self-hosted `notion-mcp-server` (static token, streamable HTTP).
Discovery failures degrade (retry → log → skip), never block app startup.
The chosen route gets recorded here at execution time.

## D-005 — Home Control: local Tool → Home Assistant REST (2026-08-16)

`tools/home_control.py` as a standard `Tool` subclass calling Home Assistant's
REST API (`POST /api/services/{domain}/{service}`, Bearer token from env).
Chosen over an MCP route for demo reliability and to exercise the local-tool
extension pattern (US-09).

## D-006 — Web search: keep the preinstalled Pollen search tool (2026-08-16)

The Space-backed `search_web` tool is preinstalled, enabled by default, and
auto-invoked purely via its description — Demo 4 needs zero new code. A direct
provider tool is the recorded fallback only if the Space route proves slow or
drags unwanted HF coupling.

## D-007 — Motion: daemon tracking + wobbler + copied arbitration (2026-08-16)

Face tracking = SDK daemon-side `start_head_tracking(weight)` (US-02 solved;
never recreate). Speech-reactive motion = SDK `enable_wobbling()`. Emotion vs
breathing vs tracking arbitration = the conversation app's `moves.py`,
retained as-is from the scaffold. Emotion clips = HF dataset
`pollen-robotics/reachy-mini-emotions-library`, preloaded before demos.

## D-009 — Robot deployment: app-only, daemon untouchable (2026-08-17)

Operator authorization: deploy `reachy_companion` to the physical Reachy Mini
(host in repo-root `.env`, SSH as the pollen user) **as a managed app only**.
Hard limits: never modify/upgrade/restart the robot's daemon or its config,
no system packages; install only into `/venvs/apps_venv`; start/stop only via
the official apps API or dashboard. Procedure lives in the `reachy-deploy`
project skill; the concrete route actually used gets appended here at the
first live deployment (plan Task 15). Version gate: if the robot daemon is
older than the app's SDK floor (`>=1.10.0rc2`), deployment STOPS and reports
— upgrading the daemon is not authorized.

Attempt 1 (2026-08-17, Task 15 deploy prep): **BLOCKED at Step 1, robot
offline.** `10.0.0.96` gave ICMP "destination host unreachable" from our own
10.0.0.34 interface, no ARP entry, no Raspberry-Pi-OUI MAC on the LAN, TCP
22/80/8000 all timed out, and `plink` returned "Network error: Connection
timed out". No mDNS name (`reachy-mini.local`) resolved. Nothing was
transferred, installed, started, or written to the robot. Local prep that
does not touch the robot was completed instead: wheel built
(`reachy_companion-1.0.0-py3-none-any.whl`, pure `py3-none-any`, carrying the
`reachy_mini_apps` → `reachy_companion.main:ReachyCompanion` entry point) and
the aarch64 dependency tree pre-checked with `uv pip compile
--python-platform aarch64-manylinux_2_28 --only-binary :all:` — all 43
packages resolve as wheels for cp311/cp312/cp313 (soxr 1.1.0, scipy 1.17.1,
mcp 1.29.0), so no source build is needed on the Pi.

Three corrections to the procedure, found by reading the SDK source, that
apply whenever the robot is next reachable:

1. **The version-gate route in the skill is wrong.** There is no
   `/api/daemon/version`; `daemon.router` only exposes
   `start|stop|restart|status|robot-name|hardware-id|robot-app-lock-status`.
   The daemon SDK version comes from `GET /update/install-source` (and
   `GET /update/available`). Note the `update`/`cache`/`logs`/`wifi_config`
   routers are mounted on `app` directly, **without** the `/api` prefix
   (`daemon/app/main.py:317-338`), unlike `apps` — so it is
   `/update/install-source` but `/api/apps/list-available/installed`.
2. **`pip install --force-reinstall <wheel>` must not be used as written.**
   `--force-reinstall` reinstalls dependencies too, including `reachy-mini`,
   which requires `PyGObject>=3.42.2,<=3.46.0` on linux — a version range with
   no wheels, so pip would attempt a source build that needs system GObject
   headers we are not authorized to install. Use
   `pip install --force-reinstall --no-deps <wheel>` and then a plain
   `pip install <wheel>` to pull only genuinely missing dependencies.
3. **`/venvs/apps_venv`'s `reachy_mini` is daemon-managed.**
   `check_and_sync_apps_venv_sdk()` (`utils/wireless_version/startup_check.py:388`)
   runs on every daemon start and force-syncs the apps venv's `reachy_mini` to
   exactly the daemon's version/git-ref. So the version gate is decisive: if
   the daemon is below `1.10.0rc2`, the apps venv gets pinned below our floor
   on every boot and no app-level install can fix it. PyPI currently shows
   `reachy-mini` stable at 1.9.0 with the 1.10.0rc line as prereleases.

Also recorded: the app instance path is the **installed package directory**
(`app.py:169` `_get_instance_path()` returns the module file;
`main.py:448` takes `.parent`), i.e.
`/venvs/apps_venv/lib/python3.X/site-packages/reachy_companion/`. It therefore
exists immediately after install — the `.env` step does not have to wait for a
first app start — but it lives inside site-packages, so any reinstall wipes it
and `.env` must be re-placed after every install.

Attempt 2 (2026-08-17, Task 15 deploy): **BLOCKED at Step 2, version gate —
robot daemon is 1.9.0, below the `>=1.10.0rc2` floor.** The robot was fully
reachable this time: SSH as `pollen@10.0.0.96` returned `reachy-mini` /
`aarch64` / Linux 6.18.33+rpt-rpi-v8, and the daemon answered on port 8000.
The corrected route from attempt 1 worked exactly as documented —
`GET /update/install-source` → `{"version":"1.9.0","source":"pypi"}` — and the
SSH cross-check agreed: `/venvs/apps_venv/bin/python -m pip show reachy-mini`
→ `reachy_mini 1.9.0` (apps venv on Python 3.12.13; `/venvs/` holds exactly
`apps_venv` and `mini_daemon`). Per the decisive-gate rule, everything from
Step 3 on was skipped: nothing was built, transferred, installed, configured,
preloaded, started, or stopped. `GET /api/apps/list-available/installed`
confirms the robot still carries only `hand_tracker_v2` and
`reachy_mini_conversation_app` — no `reachy_companion` residue, and the
daemon was never touched. The OpenAI key was **not** placed on the robot.

New finding that makes this blocker structural rather than a pending chore:
`GET /update/available` returns
`{"reachy_mini":{"is_available":false,"current_version":"1.9.0","available_version":"1.9.0"}}`.
The robot's install source is `pypi` (the stable channel), and on that channel
1.9.0 *is* the latest — the 1.10.0rc line is a prerelease the stable updater
will never offer. So no sanctioned in-place update can lift the daemon to our
floor; the only routes to 1.10.0rc2+ are the prerelease refs
(`/update/start-from-ref`, `/update/validate-ref`), which are daemon
modifications and explicitly not authorized. Combined with
`check_and_sync_apps_venv_sdk()` force-syncing the apps venv's `reachy_mini`
to the daemon version on every boot, `reachy_companion` is undeployable to
this robot by any app-level means while the pin stands.

This is now an operator decision, not an engineering retry. Two exits, and
attempting the deployment again unchanged is not one of them:
(a) authorize a daemon move to a 1.10.0rc ref — out of current scope and
    against D-009's hard limits, so it needs an explicit new authorization; or
(b) lower the app's SDK pin in `reachy_companion/pyproject.toml` from
    `reachy-mini>=1.10.0rc2` to a 1.9.0-compatible constraint, then re-verify
    every SDK API the app calls against 1.9.0 source before redeploying —
    the floor was chosen for a reason, so this is a real compatibility review,
    not a one-line edit.

Attempt 3 (2026-08-17, Task 15): **UPDATED + DEPLOYED + VERIFIED.** The
operator took exit (a) and authorized a one-time daemon update, scoped to the
robot's own official updater. Daemon **1.9.0 (pypi) → 1.10.0rc5 (git, ref
`v1.10.0rc5`, commit `221b3c3c`)**; version gate now passes and the app is
installed, discovered, started, verified live, and stopped.

Ref choice, from source rather than guesswork: `/update/start-from-ref` takes
a **GitHub tag/branch** on `pollen-robotics/reachy_mini` as a query param, not
a pip ref or version string. Tags carry a `v` prefix, and `pyproject.toml` at
tag `v1.10.0rc5` hardcodes `version = "1.10.0rc5"` (main carries
`1.10.0.dev0`), so the ref installs the exact version the dev venv runs —
D-008 version-match holds. The PyPI route (`/update/start?pre_release=true`)
could NOT reach it: `get_pypi_version()` picks pre-releases via
`releases[-1]`, and the robot reported `is_available: false, available: 1.9.0`.

**Rollback path (verified before updating, unused):** `POST
/update/start-from-ref?git_ref=v1.9.0` — the same endpoint. It works as a
downgrade because the ref route has **no `is_update_available()` guard**
(unlike `/update/start`) and step 1 is `--force-reinstall --no-deps` at
whatever the tag declares. The robot's own `/update/validate-ref` returned
`valid: true` for both `v1.10.0rc5` and `v1.9.0` before anything was changed.
apps_venv follows automatically: `check_and_sync_apps_venv_sdk()` syncs it to
the daemon's **git ref** when the daemon source is git.

One risk was retired before the single attempt: the updater's step 3
(`install 'reachy-mini[wireless-version]' --upgrade`, no `--pre`) resolves
against stable-only PyPI (latest 1.9.0) and could have undone the update. It
fires only if step 2's `pip check` fails. Diffing dependency tables between
tags showed exactly one change — `huggingface-hub` floor `1.17.0 → 1.20.1`,
already satisfied at 1.27.0 — so `pip check` was predicted to pass, and the
live log confirmed step 3 never ran. (Also found: `uv` is absent from the
`pollen` login PATH but `launcher.sh` exports `/opt/uv`, so the daemon used
`uv`, not `pip`.)

Deployment then followed the skill unchanged: two-step install (never bare
`--force-reinstall`), **zero sdist builds**, discovery lists
`reachy_companion`, `.env` placed at the site-packages instance path (mode
600, no `REACHY_DAEMON_PORT` line) and assets preloaded into
`/home/pollen/.cache/huggingface` — the same user the daemon spawns apps as
(`User=pollen`). Start cycle reached a real conversation: session initialized
on **gpt-realtime-2.1** (voice `cedar`), all **12 tools** registered, **VoiceFX
active** (pitch +4.0 st, ring-mod 55 Hz @ 0.25 mix), first-audio-delta 55 ms,
**zero tracebacks**, then a clean stop.

**Autostart (operator request, mid-task):** `PUT /api/apps/startup-app` with
`{"startup_app": "reachy_companion"}` — body shape read from the `StartupApp`
model in `routers/apps.py`, not guessed — read back and confirmed persisted to
`~/.config/reachy_mini/daemon_config.json`. This is the one config write D-009
permits, via the official API, and only because the operator asked. Boot
semantics: the Wireless boots **asleep**; `watch_antennas_for_startup_app()`
wakes it into the startup app on an **antenna touch**, so the demo needs no
laptop or dashboard.

Carry-overs for the next session: the shared apps_venv `mcp` was downgraded
2.0.0 → 1.29.0 by our `<2` pin (harmless now — the conversation app pins
`mcp>=1.27.1` unbounded — but the venv is shared); the app `.env` lives inside
site-packages and is **wiped by every reinstall**; remote MCP (Notion) tools
did not register and `HA_ENTITIES` resolved to no devices, despite both being
present in the robot `.env`; and the daemon is now a **git**-source install, so
future official update/sync behavior differs from a stock PyPI robot. Full
evidence:
`.superpowers/sdd/2026-08-16-reachy-mini-poc/task-15-deploy-attempt3-report.md`.

Operator round 1 redeploy (2026-08-17): app-only redeploy of the voice-commanded
sleep + VoiceFX makeup-gain build onto daemon 1.10.0rc5 — 13 tools loaded with
`go_to_sleep` present, `makeup gain +5.0 dB` logged, startup-app still
`reachy_companion`, robot left in the SDK sleep pose; corrects one carry-over
above — `NOTION_MCP_*` and `HA_ENTITIES` are **blank** in the source `.env`, so
their "not registered / none configured" lines are correct, not a defect.
Evidence: `.superpowers/sdd/2026-08-16-reachy-mini-poc/operator-round-1-report.md`.

Operator round 2 redeploy (2026-08-18): app-only redeploy of the D-011 WSOLA
pitch + D-012 memory build onto daemon 1.10.0rc5, wheel sha256 verified end to
end, two-step install with zero network fetches. **15 tools** loaded with
`remember` and `forget` registered and handed to the model; VoiceFX logged
`pitch +4.0 st via WSOLA time-stretch + resample (duration preserved, 35.0 ms
lookahead)`; a real turn completed at 39 ms first-audio-delta with **zero**
tracebacks; startup-app still `reachy_companion`; robot left ASLEEP (motors
disabled, sleep pose matches round 1).

This is the first redeploy to run the amended skill's **backup/restore of
`memory.v1.json` and `.env`** (new steps 4 and 6). `.env` was backed up and
re-placed; `memory.v1.json` was **absent** — expected, since this deploy is what
enables the tools that write it — and that absence is recorded rather than
treated as a backup failure. Instance path confirmed live:
`/venvs/apps_venv/lib/python3.12/site-packages/reachy_companion/`.

On-robot verification of the new pitch chain, run against the *installed*
aarch64 module after the stop: `duration_ratio 1.000`, `out + pending_delay ==
total_in` to **0.00 samples**, dominant output 553.71 Hz against a 554.37 Hz
target, `latency_ms 35.00`, peak live latency 63.2 ms. New carry-over: the chain
costs **14.8 % of one core on the robot** (1.3 % on the dev box) while the
assistant speaks — an order of magnitude above the round-1 resample-only stage,
with no observed degradation but worth watching under concurrent camera/dance
load. Evidence:
`.superpowers/sdd/2026-08-16-reachy-mini-poc/operator-round-2-report.md`.

Task 17 redeploy (2026-08-18): app-only redeploy of the D-013 face-memory build
onto daemon 1.10.0rc5, two-step `--no-deps` install, dependency set unchanged
(no new wheel). **17 tools** loaded with `remember_face` and `who_is_this`
registered and handed to the model; `Face memory ready: YuNet + SFace sessions
built in 845 ms`; the wake check ran and reported `status=no_face … in 305 ms;
greeting unchanged`; zero tracebacks; robot left ASLEEP (`motor_control_mode
disabled`, head pitch 0.51 rad).

Third run of the backup/restore ritual, now covering `faces.v1.json` as well:
`.env` (1027 B) backed up and re-placed; **both** `memory.v1.json` and
`faces.v1.json` were absent — expected, since no one has used `remember` or
`remember_face` on this robot yet — and recorded as absent rather than treated
as a backup failure. The preloader now warms the 37 MB SFace model as `pollen`
(13 s), so the first wake check does not build its session off a cold cache.
New hardware finding: the robot is a Raspberry Pi **CM4**, not a Pi 5 — see
D-013 for what that costs. Evidence:
`.superpowers/sdd/2026-08-16-reachy-mini-poc/task-17-report.md`.

Task 17 fix-round redeploy (2026-08-18, commit `0fac21a`): same ritual, second
pass, carrying the review fixes (blob-contract test, cv2 guard on indented
imports, `runner_up` dropped from `recognized`, explicit `enabled` check in the
wake hook, `align_face` dimension guard). `.env` (1027 B) backed up and
re-placed; `memory.v1.json` and `faces.v1.json` still absent — nobody has
enrolled yet — and recorded as such. **17 tools**; `Face memory ready … 976 ms`;
`Wake face check: status=no_face … in 258 ms; greeting unchanged`; zero
tracebacks and zero app `ERROR` lines; robot ASLEEP (`motor_control_mode
disabled`, head pitch 0.511 rad). No preload needed — the SFace cache from the
first pass was already warm, which is itself the evidence that the preload step
does its job across redeploys.

## D-010 — Voice: local VoiceFX chain, not cascaded TTS (2026-08-17)

Operator requirement: a "very cute robotic voice." Research verdict
(2026-08-17): gpt-realtime-2.1 has a fixed 10-voice catalog, no custom
voices; "robotic" is a DSP texture no TTS produces natively either. Decision:
keep the speech-to-speech backend and add an env-gated local DSP chain at the
handler's emit chokepoint before the 24k→16k resample — plan Task 16 (Rev 2
after Codex review): pitch-up via the soxr resample-rate trick (chipmunk
effect — duration shrinks by the pitch ratio, offset by a "speak slower"
profile line) + phase-continuous numpy ring-mod. ZERO new dependencies (both
candidate pitch engines failed source review: python-stretch resets state per
call, pedalboard primes 1 s of silence, neither ships aarch64 wheels).
Preserves the model's emotional performance, no cloud dependency, latency ≈
existing soxr delay only, reversible.
Revisit trigger: if live tuning at Task 8 can't reach "cute enough," escalate
to cascaded TTS (output_modalities=["text"] + zh TTS with cute base voice)
KEEPING the same FX chain on top. Tuned parameter values get recorded here.

## D-011 — Pitch: duration-preserving WSOLA, in numpy (2026-08-18)

Operator verdict after hearing D-010 round 1 on the robot: keep the pitch, kill
the speed-up. The resample-rate trick raises pitch and shortens speech together
(0.79x at +4 st), and the "语速放慢" profile line was never a real fix — it asks
the model to compensate for a DSP artefact and only half works.

Decision: compose a **streaming WSOLA time-stretch** (written in numpy, still
zero new dependencies, still no engine that primes or resets per call) with the
existing soxr stream — stretch by `2**(st/12)`, resample by `2**(-st/12)`. Net
effect: pitch up, duration unchanged. Geometry at 24 kHz: 20 ms hann window,
10 ms synthesis hop (50 % overlap, periodic hann → COLA sums to exactly 1.0
whatever offset the search picks), ±5 ms similarity search by normalized
cross-correlation against the previous frame's natural continuation.

Measured on this implementation: WSOLA lookahead 840 samples = **35.0 ms**
deterministic; live `pending_delay` (both stages) mean 47.7 ms, p95 60.0 ms,
peak 63.6 ms — the peak is a soxr block-buffering spike the next chunk drains,
not a standing delay.

**Controller ruling (2026-08-18 review):** the draft ~60 ms budget is formally
revised to **70 ms**, matching the `LATENCY_BUDGET_MS = 70.0` test constant; the
63.6 ms peak is accepted because it is a soxr transient over a ~40 ms standing
delay, not added lag, and the live turn measured 39 ms first-audio-delta. The
levers stay documented and untaken unless the operator asks for them: a 16 ms
analysis window (−4 ms, below the stated 20–40 ms band) or a lower soxr quality
for the pitch stage (changes audio that is already shipped and accepted).

Quality: energy outside ±3 bins of the shifted
fundamental and its 2nd/3rd harmonics is 3.1e-5 / 7.7e-5 / 2.1e-4 for 220 /
440 / 880 Hz in; mean best correlation 0.9955 on a formant-and-jitter speech
phantom. Cost ~1.3 % of one dev-box core at 24 kHz. Widening the search to the
textbook ±hop measured no better and cost 5 ms, so the narrow end won.

Contract changes this forced (all in tests): `duration_ratio` is pinned at 1.0
(kept as a name, not deleted); `pending_delay` is quoted in input samples for
the whole chain and reconciles as `len(out) + pending_delay == total_in`, with
no ratio; chunked-vs-whole equivalence is stated at envelope and seam level
because a similarity search need not be sample-exact (this one happens to be —
every decision is keyed to an absolute input position, never to a chunk
boundary — and a separate test protects that while it holds). The profile's
speed-compensation line is replaced by "吐字清楚、语气轻快。".

## D-012 — Memory: enable upstream `remember`/`forget` (2026-08-18)

Upstream already ships the tools, a JSON store (`memory.py`) and an injection
path (`prompts.get_session_instructions` prepends
`memory.format_memory_for_prompt` to the profile body). Nothing was wired to
them because the locked profile did not list them. Decision: enable both in
`default_tools` (13 → 15 tools with the two system tools) and add the Chinese
behaviour line that gives the model an occasion to use them.

Operational consequence, and the reason this is a decision and not a config
tweak: the store lives at `<instance_path>/memory.v1.json`, and the instance
path is the installed package directory inside `site-packages`
(`reachy_mini/apps/app.py:169` + `main.py:448`) — the same place `.env` lives,
and it is **wiped by every reinstall**. The `reachy-deploy` skill therefore now
backs up `.env` *and* `memory.v1.json` before install and restores both after,
as mandatory steps rather than a footnote.

## D-013 — Face memory: SFace on top of the SDK's YuNet, cv2-free (2026-08-18)

Operator requirement (2026-08-18), promoted from a PRD non-goal: Reachy should
recognize returning people and greet them by name. Decision: reuse
`reachy_mini.vision.face_detector.FaceDetector` untouched for detection and add
**one** model — SFace fp32 from `opencv/face_recognition_sface` (36.9 MiB,
Apache-2.0, 128-d) — with **zero new Python dependencies**, preloaded exactly
like the YuNet model.

The decisive constraint was **no cv2 in the app venv on the dev machine**, where
the canonical path (`cv2.FaceRecognizerSF.alignCrop` + `blobFromImage`) does not
exist. Adding `opencv-python` for one function would be a ~35 MB native
dependency and a new entry in the aarch64 resolution set, so both OpenCV steps
are replicated in numpy (`face_id.py`): a least-squares similarity fit (Umeyama)
from the three landmarks the SDK exposes onto the first three canonical SFace
reference points, then an inverse-mapped bilinear resample; the blob is BGR→RGB,
float32 0-255, no mean, no scaling. This follows the precedent the SDK sets in
`media/camera_utils.py` ("Pure numpy equivalent of `cv2.undistortPoints()`").
Deploy-time finding: the robot's *shared* apps venv does currently carry
cv2 5.0.0 (pulled in by another installed app) — that changes nothing, because
the venv is shared and can lose it whenever another app is removed, and the dev
venv never had it. Our modules import cv2 nowhere, and a test asserts it.

Three points instead of five is what lets us import the detector rather than
fork its `_decode`. The cost is that OpenCV's published 0.363 cosine threshold
is not exactly ours, so the default is a conservative **0.40 plus a 0.05 margin
rule**: a near-tie reports `ambiguous` instead of confidently naming the wrong
person. Both are env-tunable and every score is logged, because the honest
calibration data can only come from real people on the robot.

Storage is a **sibling** file, `faces.v1.json`, never an extension of
`memory.v1.json`: `MemoryFact.to_json` is an external contract read by the
mobile app, and a 1.2 KB embedding would be re-read and re-serialized on every
`remember` call and every prompt build. Same idioms, same instance path, same
consequence — it is inside site-packages and wiped by every reinstall, so the
`reachy-deploy` ritual now backs up and restores it alongside `memory.v1.json`,
with a record-count read-back for both.

Privacy is a design property, not a promise: no image is ever persisted (names,
128-float vectors, timestamps), no image or embedding ever leaves the robot (the
model receives a name and a status string), recognition is **not continuous** —
one check at wake plus explicit `who_is_this` calls — enrollment is explicit and
verbal, and `FACE_MEMORY_ENABLED=0` removes the feature entirely while
`FACE_AUTO_GREET=0` keeps the tools but drops the automatic look.

Measured on the robot (Raspberry Pi **CM4**, Cortex-A72 — not the Pi 5 the plan
assumed): sessions build in 845 ms during app startup; one SFace embed is
**239 ms idle / 362 ms with the app running**, ~3-5x the plan's estimate, and
YuNet at 640x360 is 143 ms. The wake check therefore costs ~305 ms observed with
nobody in frame and an estimated ~600-700 ms with a face, inside the 1200 ms
budget but with less margin than planned. Tuning levers if that margin proves
too thin, both requiring their own measurement: `intra_op_num_threads=2` halved
the embed to 140 ms in an isolated test (against this file's own one-thread
rule, so it is a deliberate trade, not a free win), or the int8bq model at a
quarter the size.

## D-008 — Dev environment: Windows host + mockup-sim daemon (2026-08-16)

Development on this Windows machine against `reachy-mini-daemon --mockup-sim`
(no physics, real FK/IK, local webcam/mic). Final verification on the robot
(on-Pi LOCAL media backend — lower latency; WebRTC host-PC mode only for
convenience testing). SDK and daemon versions pinned to match.

Amendment (2026-08-17, Task 1): the scaffolded app requires
`reachy-mini>=1.10.0rc2`; dev venv upgraded to **1.10.0rc5**. The dev daemon
launches from the same `.venv`, so the version-match holds automatically on
this machine; the robot's daemon must be brought to the matching 1.10.0rc
line at Task 15. Related: `mcp` is bounded `<2` (mcp 2.0 renamed attributes
and silently broke the 1.x-style reads in `mcp_client.py`); other deps still
float — lockfile decision deferred to demo prep.

## D-014 — Audit outcome: what we accept, defer and close (2026-08-19)

A five-auditor adversarial review compared `docs/PRD.md` against the code. Six
defects were real and are fixed in commit `a5f682d` (background-tool wedge guard
at both call sites, per-server MCP discovery isolation, `move_head` body-yaw
arguments, the face-tool reason contract below, dead package data, a dead env
key). The rest of the findings resolved into four rulings, recorded here because
each one is a standing position, not a pending chore.

**1. The local console and JSON-RPC channel stay unauthenticated.** The app is
served on `0.0.0.0:7860` — `main.py:457` sets `custom_app_url`, and the SDK's
own `ReachyMiniApp` webserver binds that host with only a no-cache middleware
(`reference/reachy_mini/src/reachy_mini/apps/app.py:52-66,100-115`) — and
`console.py:570-637` mounts `/rpc` on it with methods that make Reachy speak,
interrupt it, mute the microphone and rewrite persona and tool settings. No
password, no token, no origin check anywhere in
`console.py`. Operator ruling: **accepted as-is** for a POC on a trusted home
network. This is a documentation item (PRD §12.7, root README "Behavior notes"),
not a work item, and it must be revisited before anything leaves a home LAN.

**2. The idle-motion policy stays.** After 180 s of conversation inactivity, and
only when the movement manager is otherwise idle
(`conversation_handler.py:29,72-94`), the app selects a movement locally and
plays it without telling the model. `idle_do_nothing` — the 0.60-weighted
stillness option — is not in the locked profile's `default_tools`, so
`choose_idle_tool_call` filters it out and renormalizes across dance, emotion
and head turn (`idle_policy.py:60-90`): in this build the idle timer *always*
moves. Operator ruling: **accepted as personality.** A companion that never
stirs reads as switched off.

**3. Notion is deferred, and the MCP requirement is already met.** The bundled
web-search tool is a remote MCP Space (`tool_spaces.py:49-71`), discovered and
namespaced through the same seam, and it has run live with real results — so
F-K3 is satisfied today. `mcp_servers._SERVER_ENV` (`mcp_servers.py:39`) carries
exactly one hardcoded alias, `notion`, and its credentials are blank. Operator
decision: **deferred**, not blocked. The PRD's "a remote MCP server needs no code
at all" is therefore true only for that preconfigured slot; a second server is a
new tuple plus two env vars.

**4. The face tool's `reason` is a closed machine-code contract.** An
`Identification` is echoed verbatim to the cloud model, so raw exception text in
`reason` would be an exfiltration path out of an otherwise on-device feature.
`reason` is now typed `IdentificationReason` — a seven-member `Literal`
(`face_id.py:78-88`): `face_memory_disabled`, `camera_disabled`, `no_frame`,
`unsupported_frame`, `model_unavailable`, `invalid_name`, `internal_error`. The
exception detail is logged locally and never travels
(`face_id.py:470-473`). Tests in `tests/test_face_id.py` and
`tests/test_face_tools.py` pin the closure.

Consequence for the docs, applied in the same pass: PRD §12.7 documents the
accepted behaviours and the remaining local surfaces (auto-sleep at 24 h, the
60-fact and 12-people×3-signature caps, the external-tools autoload flag), and
the overclaims the audit found — the unscoped face-privacy sentence, "the check
never delays the greeting", "layered over the tracking pose", tracking running
continuously, "nothing else on the robot is modified", the "Make the room
cooler" example, and the tense on US-07 — are corrected in place.
