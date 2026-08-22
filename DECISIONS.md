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

## D-015 — Face pipeline tuned from measured evidence (2026-08-19)

D-013 shipped with three open questions and one carry-over: the CM4's 239 ms
idle / 362 ms busy SFace embed, an uncalibrated 0.40 threshold, and a wake check
that stakes everything on a single frame. A measurement round answered all of
them, and the answers were not the ones the plan expected.

**1. int8 quantization is rejected.** The obvious lever — the int8bq model at a
quarter the size — is *slower* on this robot. The Cortex-A72 has no dot-product
instructions (`sdot`/`udot`, ARMv8.2), so ONNX Runtime falls back to a NEON int8
path whose de/requantization costs more than the fp32 GEMM it replaces. OpenCV
zoo's own Raspberry Pi 4 benchmark — the same SoC — measures the int8 SFace at
**27 % slower** than fp32. No further work; the fp32 model stays.

**2. Alignment now uses all five landmarks, and the threshold is OpenCV's.**
YuNet computes five keypoints — the `kps_*` tensors are `[1, anchors, 10]` — but
the SDK's parser reads columns 0-5 and discards 6-9, the two mouth corners. That
is why D-013 fitted three points, and why OpenCV's published cosine threshold did
not apply to us. `face_id._decode_five_points` re-parses the same raw outputs
with the mouth corners kept, in a `FaceDetector` subclass that overrides
`_decode` and nothing else (the SDK builds its `Face` inline, so there is no
finer seam); `REFERENCE_POINTS` is completed to the canonical five-row ArcFace
template. The pipeline now reproduces `alignCrop` semantics — same template, same
similarity warp, same raw 0-255 RGB blob — so `FACE_MATCH_THRESHOLD` defaults to
**0.363**, OpenCV's own number for this exact model, instead of the conservative
0.40 guess. The 0.05 margin rule is unchanged. **Consequence: embeddings enrolled
under the three-point warp are not comparable to five-point ones.** No migration
code exists and none is warranted — no live enrollment has happened — but any
pre-existing `faces.v1.json` must be re-enrolled.

**3. The SFace session runs on three threads with busy-spinning off.** ORT spins
its intra-op pool by default, so the recognizer's thread pool burns a core
*between* embeds; that is the likeliest cause of the 239 → 362 ms regression
under load. `session.intra_op.allow_spinning=0` plus
`FACE_ORT_INTRA_OP_THREADS=3` (default 3, clamped 1-4) makes the recognizer both
faster and a politer neighbour. This supersedes D-013's one-thread rule **for the
recognizer only** — recognition is one short burst per wake, and a ~100 ms burst
is invisible to the 50 Hz loop, where a continuous load would not be. The SDK's
YuNet detector keeps its own one-thread session, untouched. Dev-box median for
one embed: 17.4 ms at one thread with spinning, 8.3 ms at three without.

**4. The wake check may look at up to three frames.** At the twelve people this
POC enrolls, the failures that matter are per-frame accidents — a blink, a turned
head, a shadow — not per-person confusions, so extra frames buy more recognitions
than any model change does. `FACE_WAKE_ATTEMPTS` (default 3, clamped 1-5) rounds
of grab-frame-then-identify run inside the **unchanged** 1200 ms
`FACE_WAKE_BUDGET_MS` deadline, with a 150 ms pause between them so the next
frame is genuinely different. The first confident recognition wins and stops the
sequence; the deadline stops it otherwise. Every path that returned the greeting
unchanged before still does.

## D-016 — Persona externalized to an instance `persona.md` (2026-08-20)

Operator-requested. D-001 locked the app to one persona and put its text in
`profiles/_reachy_companion_locked_profile/profile.md` — a file inside the
wheel. Rewriting the character therefore meant building and redeploying, which
is the wrong cost for the thing most likely to be iterated on. The persona is
now editable *on the robot*.

**The file.** `persona.md` in the app's instance directory — the same directory
that holds `.env`, `memory.v1.json` and `faces.v1.json`, resolved through the
same `config.INSTANCE_PATH` those use. `PERSONA_FILE` (absolute path) moves the
lookup elsewhere, with identical rules.

**The parser is shared, not cloned.** `profile_store.split_front_matter` /
`read_document_text` / `optional_string_field` were lifted out of
`_parse_profile_document` and are now called by both documents, so `persona.md`
is `profile.md` — same `+++` delimiter, same TOML dialect, same fields — with
everything optional: front matter may carry any of `voice`, `greeting`,
`default_tools` (`hidden` is deliberately not accepted; a single-persona app has
nothing to hide it from), and the body is the persona text. A file that is
nothing but persona text is valid. Copying the shipped `profile.md` verbatim and
editing it is also valid, `schema_version` included. Fields the file omits keep
the built-in value — the overlay is per field, not per file.

**Every failure is total, never partial.** Missing, not-a-file, unreadable, bad
TOML, unknown key, or an empty body: each logs a WARNING naming the problem and
uses the built-in profile *whole*. Half a persona — a new voice on the old
character — would be worse than either endpoint, so no path produces one. An
empty body discards the file's front matter too, for the same reason.

**One line of proof.** `persona.log_persona_source`, called from `main.run` after
the instance `.env` is loaded (it may carry `PERSONA_FILE`), logs `persona:
instance persona.md (<path>)` or `persona: built-in locked profile`. The operator
verifies from the log which text the robot is actually running, which matters
precisely because the fallbacks are silent about themselves otherwise.

**Where it hooks in.** `profile_store.read_profile` applies the overlay, and only
when the requested profile is the active one, so instructions, voice, greeting
and `default_tools` all pick it up through the paths they already use
(`prompts._active_profile`, `profile_toolsets.read_profile_default_tool_names`)
and no other profile is affected. `persona` imports `profile_store` for the
parser, so `read_profile` imports `persona` inside the function to break the
cycle. The resolution is cached on the file's path, mtime and size — the "loaded
at app start" semantics the operator asked for, without going stale if the file
does change under a running process.

**It is user state.** Like `.env`, the instance directory is inside
site-packages on the robot, so a reinstall wipes it. The `reachy-deploy` skill's
mandatory backup and restore blocks now cover `persona.md`; losing it silently
reverts Reachy to the built-in Chinese persona, and the `persona:` startup line
is how that is caught.

Verified: 566 passed / 30 skipped (18 new tests in `tests/test_persona.py` and
`tests/test_main.py`), ruff and mypy strict green.

## D-017 — VoiceFX static: comb + soft knee replace the tremolo and the hard clip (2026-08-20)

Operator report: the assistant voice was "not very robotic and cute, rather full
of static noise". An offline diagnosis ran the **shipped code**, chunked exactly
as the runtime chunks it, on a speech-shaped phantom at realistic TTS levels.
Four plausible causes were measured and eliminated — int16 wraparound at the
daemon boundary (0 sign flips in 7911 overshooting samples; libsoxr saturates),
chunk-boundary discontinuities (seam/within-chunk jump ratio 0.99-1.21x), WSOLA
overlap-add artefacts (roughness proxy -24.68 dB vs the input's -24.46 dB), and
ring-mod overshoot (`|y| <= |x|` by construction below mix 0.5). Two real causes
were found, and the first created the second.

**Cause 1 — the "ring modulator" was a maximum-roughness buzz generator.**
`x*(1-mix) + x*sin*mix` is `x * [(1-mix) + mix*sin]`: an *interpolation*, so at
the shipped `mix = 0.25` it never inverts sign and never suppresses the carrier.
It was not ring modulation at all — it was a **6.02 dB tremolo**. Its 55 Hz
carrier sat at **0.956 of the psychoacoustic roughness peak** (~70 Hz, where the
*asper* is defined; the roughness band is ~20-150 Hz), and `2*fc = 110 Hz` put
every sideband pair inside one ERB critical band (~132 Hz at 1 kHz) — the
textbook condition for beating rather than timbre. Measured: envelope energy in
the 30-120 Hz roughness band rose from **-38.1 dB** (pitch only) to **-15.0 dB**,
**+23.1 dB**, with the dominant envelope modulation moving to **56 Hz**. That was
present at zero gain with zero clipped samples, which isolates it beyond doubt.

**Cause 2 — the +5 dB makeup gain hard-clipped real material.** The tremolo cost
exactly `sqrt((1-m)^2 + m^2/2)` = **-2.26 dB** of RMS (theory and measurement
agreed to two decimals), which is *why* the gain was raised to +5 dB. x1.7783
destroys everything above -5.0 dBFS: on a peak-normalized -1 dBFS speech signal
**3.29 %** of samples pinned (4.90 % at 0 dBFS), a **-19.2 dB** nonlinear
residual (~11 % distortion) on the loudest vowels. Level-dependent, so it came
and went with the syllables — which is what "intermittent static" is. Hard
clipping is also the worst available way to bound a signal: a discontinuous
first derivative, a series decaying as 1/k, 10 dB more H7 and a 3.5 % (vs 2.1 %)
high-order share against a soft knee at the same drive.

**Cause 3, consequential — a second, uncontrolled clip downstream.** The clipped
int16 then went 24 k -> 16 k, where band-limited reconstruction overshot the
flat tops back over full scale and soxr saturated them again: true peak
**+0.10 to +0.14 dBTP** against the EBU R128 / ITU-R BS.1770 production limit of
-1 dBTP, 1079-1560 extra samples per 4 s.

### The new chain

```
int16 -> float32 -> WSOLA+soxr pitch (+5 st, UNCHANGED) -> feedback comb
      -> [AM, off by default] -> makeup gain -> soft-knee saturator
      -> np.clip(+/-1) [retained, no-op] -> int16
```

**Comb, not more AM.** Every candidate colour stage was measured on the same
signal. The AM stage adds roughness at *every* carrier (best case -38.2 dB at
250 Hz, and only because that carrier happened to land on the shifted F0 — not
robust). A feedback comb `y[n] = x[n] + g*y[n-D]` is the only candidate that
adds audible robot character while measuring **cleaner than the untreated
reference** (-43.6 dB vs -38.7 dB roughness), because it is linear and
time-invariant: it reshapes the spectrum and modulates the envelope not at all.
4 ms at 24 kHz resonates every 250 Hz with 8.4 dB of peak-to-null ripple — the
"small speaker inside a tin robot" colour — at no intelligibility cost, which
matters because Chinese is a primary scenario and it is tonal. Implemented as
one `scipy.signal.lfilter` call with `zi` carried across chunks.

**Soft knee, not a limiter.** A lookahead-free limiter was implemented and
measured against the stateless saturator and lost on every axis: quieter
(-10.16 vs -9.12 dBFS), 6.7x the CPU, ~3 ms more latency — and decisively, its
output **depended on the chunk size** (0.245 difference between 2400- and
137-sample chunks). `response.output_audio.delta` sizes are variable and not
ours to control, so a chunk-dependent stage is itself an artefact source. The
saturator is exactly linear below `knee * ceiling` and tanh-asymptotic above it;
because `|tanh| < 1` strictly, the ceiling is never reached, which is what turns
the trailing `np.clip(-1, 1)` into a genuine no-op backstop rather than a second
clipper.

**Streaming and chunk-invariant, asserted byte-exactly.** Comb state is an
exact-state IIR delay line; the saturator is memoryless; WSOLA is keyed to
absolute input positions. The whole chain is bit-identical at 480/960/1600/4800
and at mixed coprime chunk sizes. The comb's delay line resets with the same
lifecycle as the WSOLA and soxr state — `VoiceFX.reset()`, which barge-in
reaches through `OpenAIRealtimeHandler._reset_output_pipeline`.

### Defaults, and why the carrier is *gated* rather than clamped

| knob | was | now |
|---|---|---|
| `VOICEFX_PITCH_SEMITONES` | 4.0 | **5.0** — more character at zero latency/CPU cost; WSOLA lookahead is geometry-bound at 35.0 ms and does not move with the shift |
| `VOICEFX_RINGMOD_HZ` | 55.0, clamp 0-2000 | **0.0**, legal set `{0}` and `[150, 4000]` |
| `VOICEFX_RINGMOD_MIX` | 0.25 | **0.0** |
| `VOICEFX_COMB_MS` | — | **4.0**, 0 = off, else clamp 0.5-20 |
| `VOICEFX_COMB_FEEDBACK` | — | **0.45**, clamp 0-0.9 |
| `VOICEFX_COMB_MIX` | — | **0.35**, clamp 0-1 |
| `VOICEFX_GAIN_DB` | 5.0, clamp -6..12 | unchanged in value and range; its **meaning** is now "gain into the saturator", safe by construction across the whole range |
| `VOICEFX_CEILING_DBFS` | — | **-1.0**, clamp -12-0 |
| `VOICEFX_KNEE` | — | **0.75**, clamp 0.1-0.99, read as a *fraction of the ceiling* |

A carrier in `(0, 150)` is **refused with a warning and defaults to off**, not
clamped up to 150. The whole 20-150 Hz band is harmful, not merely
out-of-preference, and clamping would silently hand the operator a carrier they
never chose — warn-and-default is what every other malformed knob does. 55 Hz,
the value that shipped, is now unreachable.

**No `VOICEFX_PRESET`.** The candidate characters differ by two or three scalars
and the knobs are already independent and env-driven; a preset would add a
second configuration mechanism that silently shadows explicit `.env` values.
`.env.example` instead carries three commented, paste-able blocks — "cute robot
(default)", "more metallic", "plain pitched voice".

The startup INFO line now names the whole chain — pitch, comb delay/feedback/mix
with its resonance spacing, AM state, gain, knee and ceiling — because that line
is how the operator verifies which chain is actually deployed.

### Also fixed in passing

`streaming.audio_to_int16` did an unguarded `(audio * 32767).astype(np.int16)`,
which wraps on `|x| > 1.0` — 1.001 lands near -32768, a full-scale polarity flip.
On today's emit path it only ever receives int16, so it was a latent hazard
rather than a live bug; it now clips before scaling.

### Measured result

Same phantom, same chunking, -1 dBFS peak-normalized input: output RMS
**-8.80 -> -6.77 dBFS** (*louder*, which answers the complaint that put the
+5 dB gain there), clipped samples **3.29 % -> 0.00 %**, downstream over-rail
samples **1079 -> 0**, true peak **+0.10 -> -0.98 dBTP**, roughness
**-16.3 -> -40.5 dB**, intelligibility proxy essentially unchanged. Zero clipped
samples at every input level from -20 dBFS to full scale, so the conclusion does
not depend on guessing the real TTS crest factor. Latency delta **zero** — the
comb has no lookahead and the saturator has no state. CPU delta measured on the
dev box: 0.99 % -> 1.15 % of one core, i.e. ~15 % -> ~16 % on the CM4.

**Residual risk:** the phantom is synthetic, and whether 250 Hz comb spacing
reads as "cute robot" or as "phone on speaker" is a listening judgement no test
can make. `VOICEFX_COMB_MIX=0.0` bypasses the stage, and the "plain pitched
voice" block isolates the pitch stage if the operator wants to hear it alone.
The on-robot listening pass is still owed.

Verified: 622 passed / 30 skipped (56 new tests in `tests/test_voicefx.py` and
`tests/test_streaming.py`), ruff and mypy strict green.

## D-018 — HomeAssistant-Nova ported natively, not consumed over MCP (2026-08-21)

The operator's `ha-actions` server exposes 23 tools we wanted. It is a
single-file, stdio-only, hand-rolled JSON-RPC server pinned to one Mac
(`reference/HomeAssistant-Nova/bin/ha-actions-mcp/server.py`). Three
alternatives were weighed and the port won on four independent counts.

**Identifiers.** Thirteen of its 23 tool descriptions embed the operator's real
calendar address, Drive folder id, Gmail address, task-list names and family
trip place names, and ~60 code sites carry the same. Consuming it verbatim would
push all of that into the realtime model prompt on every session, and therefore
into transcripts and logs. Porting externalises every identifier to configuration
once. `tests/test_hanova_integration.py` now fails if any of it comes back.

**Concurrency.** Upstream's handlers are synchronous inside a single-threaded
stdin loop (`server.py:2364`, `:2390`), so one 200-second `play_music_here` or
one 600-second `drive_upload` makes `stop_music` unanswerable. On a voice robot
that is a safety defect: a robot that cannot be stopped by voice. Our realtime
loop already dispatches every tool as its own asyncio task
(`huggingface_realtime.py:1011`), so a native port gets a fast stop lane for
free — and upstream itself hand-rolls daemon threads to dodge its own loop,
which is the argument that the concurrency model belongs to the host app.

**Prompt budget.** 32.5 KB of tool descriptions, ~8–9 K tokens, written as
routing rules for a *different* agent with sibling tools we do not have. Rewritten
fresh at ≤120 characters each, the whole catalogue is roughly 2 KB.

**Transport.** Our MCP client speaks Streamable HTTP only
(`mcp_client.py:133-141`); stdio would need a new transport lane *plus* a way to
reach another machine's process, and the tools would still need `yt-dlp`,
`ffmpeg`, an SMB client and the credential files on the robot regardless.

### What the port changed on purpose

- **`play_music` always plays on the robot's own speaker.** The Voice-PE and
  TV-cast music paths are not ported and `play_music_here` is merged away: a desk
  robot asked for music is asked for *its* music, and that path needs no Home
  Assistant, no LAN URL and no home network.
- **Pause is synthesised.** The daemon media API is exactly `play_sound(file)`
  and `stop_sound()` (`daemon/app/routers/media.py:77-115`) — no pause, no seek,
  no per-stream volume; `/api/volume/set` is system-wide and plays a test beep.
  So barge-in stops the sound and banks the offset, and the turn's end re-cuts
  the cached mp3 from that offset with the bundled ffmpeg.
- **`show_on_tv` generates its own image.** Upstream's version depended on the
  operator's Hermes gateway and a read-only mount of its image cache. We call the
  OpenAI Images API with the key the app already has, serve the PNG from the
  app's own web server, and cast that URL.
- **`drive_upload` uploads a camera frame.** Upstream took an absolute path on
  the operator's Mac. The only file a robot can meaningfully offer is one it just
  produced, so it captures one frame — at confirm time, not at arm time — and
  uploads that. Never anyone-with-link readable, reversing upstream's default.
- **NAS auto-advance is not ported.** Upstream ran an unbounded 1 Hz daemon
  polling Home Assistant and prefetching (`server.py:1976-2058`). The session
  keeps the trip playlist and its position; `nas_skip` advances it on request.
  Same user-visible capability, no background task to own, cancel or leak.
- **Media is served from the app's own web server.** `console.py` already mounts
  `StaticFiles` on a FastAPI app bound to `0.0.0.0:7860`; a second mount at
  `/hanova-media` was enough. No new port, no stdlib server.
- **Notion's `Owner` property is dropped.** Its select options are real people's
  names, which have no place in a schema that enters the model prompt.
- **`email_send` is included but gated.** It was recommended for exclusion. The
  operator kept it; the mitigation is that the read-back names **every**
  recipient (To and CC), the subject and the **entire message body, verbatim** —
  bounded at 500 characters, with a longer body refused as `body_too_long`
  rather than condensed — and the send executes the parked envelope, never the
  second call's args. The digest appended after the body is an integrity token
  only, never a stand-in for reading the body out.

### Approved non-goals (external review round 1, 2026-08-21)

Three upstream behaviours are deliberately **not** ported. Each was raised by the
external reviewer as an undeclared scope change; the controller accepted them as
scope decisions and they are recorded here so they cannot be mistaken later for
oversights.

- **Drive restore.** Upstream could untrash. `drive_trash` already leaves the
  item recoverable from the Drive UI for about thirty days, on any device,
  without the robot. A voice-driven restore would add a second fuzzy match over
  the *trash* namespace — precisely where duplicate names accumulate — for a
  capability the user already has. `gdrive.set_trashed` keeps its boolean because
  it is one API call either way, but no tool may expose `trashed=False`.
- **Email BCC.** Upstream accepted a blind-carbon list. This port supports To and
  CC only, and `send_mail` has no `bcc` parameter at all. A blind recipient is by
  definition one the confirmation read-back cannot surface, which contradicts the
  reason the gate exists. The persona explains this rather than failing silently.
- **The self-destruct gag keeps its in-character ritual.** It uses the shared
  `ConfirmationGate` for its TTL, its claim/complete lifecycle and its explicit
  abort path, but **not** the generic read-back summary: spelling out what the
  tool is about to do destroys the only thing the tool does. Nothing destructive
  is at stake — it is audio. The arm text is the countdown ritual, the
  confirmation phrase is thematic, and `abort` is enforced in code. The persona
  is instructed not to pre-explain the ritual, and
  `test_the_self_destruct_ritual_is_not_explained_away` fails if it starts to.

### What review round 1 changed in the design

- **Availability is per tool, not per family** (finding 10). `settings.TOOL_PREREQS`
  maps all 22 names to their own prerequisites; families aggregate into a
  tri-state startup verdict. `nas_video_query` needs only the index file;
  `play_video` needs neither a LAN base nor a live media mount; `stop_music`
  needs nothing at all, by design.
- **The home verdict is tri-state** (finding 12). `away_from_home` now requires
  routing-level absence proven by a socket-level LAN signal. An expired HA token,
  an HA outage and a VPN connection are all `home_status_unknown`.
- **The confirmation gate is session-scoped, and spends authorisation on success**
  (findings 3 and 4), so a confirmation cannot survive a backend reconnect and a
  transient 503 does not cost the user their approval.
- **Music is a serialized state machine with acknowledged daemon commands**
  (finding 2), and the resume waits for a real audio-drain signal from
  `console.play_loop` rather than for `response.done` (finding 1).
- **Ported tools log metadata only** (finding 7), through `hanova/redact.py`.
- **No default in `settings.py` is derived from the operator's own setup**
  (finding 6): the NAS share and subpaths and the three HA script names all
  default to empty and are real prerequisites.

### What review round 2 changed in the design

- **`away_from_home` now requires positive off-home evidence** (finding 3). The
  robot's own address must sit outside every network declared in
  `HANOVA_HOME_NETWORKS`; a failed connection to Home Assistant is an outage, not
  an absence, and produces `home_status_unknown`. Every house-bound tool branches
  all three verdicts and does **no work at all** on `unknown` — round 1 tested
  only `AWAY`, so a VPN, a 401 or a timeout still fired real house actions. The
  boolean `is_home()` is deleted, because it could not express the third state.
- **Every armed action carries an immutable claim id** (finding 2).
  `complete()`, `release()` and the claim-bound `abort()` require it and compare
  epoch *and* id inside the mutating lock, so an operation in flight from an
  older session can no longer spend or re-arm an authorisation that belongs to a
  newer one, and a slot whose action is executing cannot be re-armed at all.
- **Transient and terminal failures are different outcomes** (finding 9).
  `release()` — a bare "try again" — is reserved for connection, disconnection
  and timeout faults. Authentication, refused-recipient, refused-sender and
  validation failures **spend** the authorisation, because the approved action
  cannot succeed as approved and the user must hear a corrected one.
- **The email read-back carries the entire message body** (finding 4), capped at
  500 characters. A first-line preview plus a hex digest is not something a
  person can verify by ear: two bodies with the same opening line produced
  indistinguishable confirmations while the sent mail differed. Longer bodies are
  refused with `body_too_long` rather than summarised.
- **The music resume waits on real, per-response audio accounting** (finding 1).
  `audio_drain` marks a response pending the moment it is created — before any
  audio exists — counts samples at **enqueue** time, and only reports drained
  once the response is closed, nothing is outstanding, the queue is empty and the
  device-buffer estimate has expired. A final tool batch with
  `needs_response=False` now closes the turn itself, which is the path that used
  to leave music paused for the rest of the conversation.
- **Session boundaries invalidate rather than forget** (finding 8).
  `PLAYER.invalidate()` advances the generation under the state lock and the
  boundary also stops the daemon; cleanup runs from the realtime connection's own
  `finally`, so a dropped connection cleans up even when the handler never shuts
  down.
- **Staging is single-flight, and the cursor advances by token** (findings 10
  and 11). Per-destination locks plus uniquely named `.part` files that pruning
  skips; `peek_next()` returns a `CursorToken` and `commit_next(token)` is a
  compare-and-swap, so two concurrent skips consume one clip and a late cast
  cannot advance a new playlist.
- **`HANOVA_NAS_SUBPATH` is consumed** (finding 12): it bounds the subtree an
  index entry's original path may resolve inside, so a mandatory prerequisite
  finally changes behaviour instead of merely blocking deployments.
- **Identifier hygiene reaches tests, docs and the deploy skill** (finding 5).
  Every NAS fixture is an obvious synthetic sentinel, the shape scan covers
  tests/docs/skills with a private-address pattern that actually matches private
  addresses, and the staged-content scan compares literally against an untracked
  value list and reports counts and paths only — never a prefix of a value.
- **Service-layer logging goes through `redact` too** (finding 6). `settings.py`,
  `home_net.py`, `media_store.py`, `ytdlp.py`, `images.py`, `nas.py` and
  `ha_client.py` no longer log raw paths, URLs, stderr tails or tracebacks, and
  each has a `caplog` sentinel test. A module whose only log line is a fixed
  string or a count — `stop_music`, `mad_laugh`, `self_destruct`,
  `nas_video_query`, the gate, the music state machine — is exempt only as a
  named entry with a written reason in `_REDACT_EXEMPT`, and a second test
  re-reads each exempt module's log lines to keep the claim honest.

### New dependencies (operator-approved)

`yt-dlp`, `imageio-ffmpeg`, `smbprotocol` — three pure-wheel additions, no system
packages. `imageio-ffmpeg` over `static-ffmpeg` because it ships the ffmpeg
binary *inside* a `manylinux2014_aarch64` wheel and exposes `get_ffmpeg_exe()`,
where `static-ffmpeg` downloads its binaries at first use.

### New instance-directory state (deploy ritual)

`google-workspace-mcp/<account>.json` (rewritten on every token refresh),
`google-oauth.json`, `nas-video-index.json`. All three are added to the
backup/restore steps in `.claude/skills/reachy-deploy/SKILL.md`. The
`hanova_media/` cache is deliberately **not** backed up: it is regenerable, and
keeping it would carry hundreds of megabytes of home video through every deploy.

The robot's connection details leave the tracked deploy skill in the same move:
`REACHY_HOST`, `REACHY_SSH_USER`, `REACHY_SSH_PASSWORD` and the new
`REACHY_HOSTKEY` (the SSH host-key fingerprint `plink -batch` needs, previously
a literal in the skill file) all live in the gitignored repo-root `.env`. A
placeholder-only `.env.example` documents the four keys and is tracked on
purpose — which needed a `!/.env.example` negation placed **after** every
`.env`-matching rule in `.gitignore`, because git honours the last matching
pattern and the file's later `.env.*` rule was silently re-ignoring it.

### One deployment assumption the code cannot enforce

NAS path containment (`nas.validate_cast_path`, `HANOVA_NAS_SUBPATH` /
`HANOVA_NAS_CAST_SUBPATH`) is a **client-side check over the normalized path
string** — it refuses `..`, absolute paths and anything resolving outside the
configured subtree — while `smbprotocol` follows symlinks and reparse points
wherever the SMB **server** resolves them. So the two configured subpaths must
contain no symlink or reparse point leading out of the subtree; otherwise a path
the check accepts can still open a file elsewhere on the share. That is a
property of the operator's NAS layout, verified when the share is configured,
and it is recorded here because nothing in the app can detect it.

**Not yet run on the robot.** Everything above rests on the suite until the
Task 15 deployment and wake test say otherwise.
