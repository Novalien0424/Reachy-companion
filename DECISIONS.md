# Decisions

Durable implementation decisions, compacted 2026-08-27 — full narrative,
evidence and review logs remain in this file's git history. Every decision ID is
retained. Values quoted here (env keys, thresholds, paths, routes,
authorizations) are operative.

## D-001 — Repo strategy: own app via the official scaffolder (2026-08-16)

Create our app with `reachy-mini-app-assistant create --template conversation`
(clones the official Conversation App, renames the package, rewires
`pyproject.toml`/entry points), then adapt in place. Not a git fork (upstream
deleted the multi-backend seam in `5b8d974`; their AGENTS.md says the app is not
meant to be forked/vendored) and not a library dependency (module-level
singletons + hardcoded tool discovery make it non-importable). `reference/`
clones stay read-only for diffing against upstream fixes.

## D-002 — Realtime backend: new `openai_realtime.py` handler (2026-08-16)

Keep the maintained `huggingface_realtime.py` event loop; replace only the
client build (`AsyncOpenAI(api_key=OPENAI_API_KEY)`), `model="gpt-realtime-2.1"`
in `realtime.connect`, 24 kHz `AudioPCM`, the OpenAI voice list and turn
detection. The deleted upstream handler is recoverable at
`git show 5b8d974^:src/…/openai_realtime.py`. Resampling 16 kHz (robot, fixed by
the SDK) ↔ 24 kHz (model) lives in our handler.

## D-003 — Turn handling for Chinese: configurable server-side VAD (2026-08-16)

Expose `threshold`/`prefix_padding_ms`/`silence_duration_ms` and optional
`SemanticVad(eagerness=…)` via env; default `silence_duration_ms=800` for
mid-sentence pauses (US-01), `REALTIME_TRANSCRIPTION_LANGUAGE=zh`, and a
Chinese-first profile (the upstream default forces English).

## D-004 — MCP: reuse the client, replace the installer (2026-08-16)

`mcp_client.py` (streamable HTTP, auth headers, namespacing) and `RemoteMcpTool`
are reused unchanged; the HF-Space-locked URL validator in `tool_spaces.py` is
replaced by a generic env-fed config plus a persistent extra-tools registration
seam in `core_tools.py` (`initialize_tools()` rebuilds the registry). Hosted
`mcp.notion.com` OAuth/PKCE will NOT be built (static bearer first, self-hosted
`notion-mcp-server` as fallback). Discovery failures degrade (retry → log →
skip), never block startup. Notion later deferred — D-014.

## D-005 — Home Control: local Tool → Home Assistant REST (2026-08-16)

`tools/home_control.py` as a standard `Tool` subclass calling
`POST /api/services/{domain}/{service}` with a Bearer token from env — chosen
over MCP for demo reliability and to exercise the local-tool pattern (US-09).

## D-006 — Web search: keep the preinstalled Pollen search tool (2026-08-16)

The Space-backed `search_web` tool is preinstalled, enabled by default and
auto-invoked from its description — Demo 4 needs zero new code. A direct
provider tool is the recorded fallback only if the Space proves slow.

## D-007 — Motion: daemon tracking + wobbler + copied arbitration (2026-08-16)

Face tracking = SDK `start_head_tracking(weight)`; speech-reactive motion = SDK
`enable_wobbling()`; emotion/breathing/tracking arbitration = the conversation
app's `moves.py`, retained from the scaffold; emotion clips = HF dataset
`pollen-robotics/reachy-mini-emotions-library`, preloaded before demos. Never
recreate any of these.

## D-008 — Dev environment: Windows host + mockup-sim daemon (2026-08-16)

Develop against `reachy-mini-daemon --mockup-sim`, verify finally on the robot,
SDK and daemon versions pinned to match. Amendment (2026-08-17): the app
requires `reachy-mini>=1.10.0rc2`; dev venv at **1.10.0rc5**. `mcp` is bounded
`<2` (mcp 2.0 renamed attributes and silently broke the 1.x-style reads in
`mcp_client.py`). Superseded in practice by the Mac mini as deploy host — see
the Python 3.12 requirement in `progress.md`.

## D-009 — Robot deployment: app-only, daemon untouchable (2026-08-17)

**Operator authorization (2026-08-17): deploy `reachy_companion` to the physical
Reachy Mini as a managed app ONLY.** Hard limits: never modify/upgrade/restart
the robot's daemon or its config, no system packages; install only into
`/venvs/apps_venv`; start/stop only via the official apps API or dashboard.
Version gate: if the daemon is below the SDK floor (`>=1.10.0rc2`), deployment
STOPS. Procedure of record: `.claude/skills/reachy-deploy/SKILL.md`.

Route/install facts read from SDK source that the skill depends on: the daemon
version comes from `GET /update/install-source` (there is no
`/api/daemon/version`), because `update`/`cache`/`logs`/`wifi_config` are
mounted **without** the `/api` prefix while `apps` is not (so
`/api/apps/list-available/installed`). Never `pip install --force-reinstall
<wheel>` bare — it reinstalls `reachy-mini`, whose
`PyGObject>=3.42.2,<=3.46.0` range has no wheels and would trigger a source
build; use `--force-reinstall --no-deps <wheel>` then a plain `pip install
<wheel>` (**two-step install**). `check_and_sync_apps_venv_sdk()` force-syncs
the apps venv's `reachy_mini` to the daemon's version/git-ref on every daemon
start, which is what makes the version gate decisive. The instance path is the
installed package directory,
`/venvs/apps_venv/lib/python3.12/site-packages/reachy_companion/` — it exists
immediately after install and is **wiped by every reinstall**, so `.env`,
`persona.md`, `memory.v1.json`, `faces.v1.json` and the three HA-Nova JSON files
must be re-placed each time.

**Scoped daemon-update exception (2026-08-17, attempt 3):** the operator
authorized a one-time daemon update, scoped to the robot's own official updater.
Daemon **1.9.0 (pypi) → 1.10.0rc5 (git, ref `v1.10.0rc5`, commit `221b3c3c`)**
via `POST /update/start-from-ref?git_ref=v1.10.0rc5` (a GitHub tag, not a pip
ref; the PyPI route cannot reach a prerelease). **Verified rollback path,
unused:** the same endpoint with `git_ref=v1.9.0` — it works as a downgrade
because the ref route has no `is_update_available()` guard. The daemon is now a
**git**-source install, so official update/sync behavior differs from a stock
PyPI robot. Any further daemon change needs a new explicit authorization.

**Autostart (operator request):** `PUT /api/apps/startup-app` with
`{"startup_app": "reachy_companion"}`, persisted to
`~/.config/reachy_mini/daemon_config.json` — the one config write D-009 permits.
The Wireless boots **asleep**; an antenna touch wakes it into the startup app.
Carry-over: the shared apps_venv `mcp` was downgraded 2.0.0 → 1.29.0 by our `<2`
pin (harmless, but the venv is shared).

## D-010 — Voice: local VoiceFX chain, not cascaded TTS (2026-08-17)

Operator requirement: a "very cute robotic voice." `gpt-realtime-2.1` has a
fixed 10-voice catalog and no custom voices, and "robotic" is a DSP texture no
TTS produces natively — so keep speech-to-speech and add an env-gated local DSP
chain at the handler's emit chokepoint, before the 24k→16k resample. **Zero new
dependencies** (python-stretch resets state per call, pedalboard primes 1 s of
silence, neither ships aarch64 wheels). Revisit trigger: if live tuning cannot
reach "cute enough", escalate to cascaded TTS (`output_modalities=["text"]` + zh
TTS) keeping the same FX chain on top.

## D-011 — Pitch: duration-preserving WSOLA, in numpy (2026-08-18)

Operator verdict after D-010 round 1: keep the pitch, kill the speed-up. A
**streaming WSOLA time-stretch in numpy** is composed with the soxr stream —
stretch `2**(st/12)`, resample `2**(-st/12)` — so pitch rises and duration is
unchanged. Geometry at 24 kHz: 20 ms hann window, 10 ms synthesis hop (periodic
hann → COLA sums to exactly 1.0), ±5 ms similarity search by normalized
cross-correlation. Lookahead **35.0 ms** deterministic; live `pending_delay`
peak 63.6 ms. **Controller ruling (2026-08-18): the latency budget is 70 ms**
(`LATENCY_BUDGET_MS = 70.0`), that peak accepted as a soxr block-buffering
transient over a ~40 ms standing delay. Cost **14.8 % of one robot core** while
the assistant speaks. Contract: `duration_ratio` pinned at 1.0 and
`len(out) + pending_delay == total_in` in input samples. The profile's "语速放慢"
compensation line is gone.

## D-012 — Memory: enable upstream `remember`/`forget` (2026-08-18)

Upstream ships the tools, the JSON store and the prompt-injection path; nothing
was wired because the locked profile did not list them. Both enabled in
`default_tools` (13 → 15 tools) plus a Chinese behaviour line. Operational
consequence: `<instance_path>/memory.v1.json` is inside site-packages and wiped
by every reinstall, so the `reachy-deploy` skill backs it up and restores it
alongside `.env` as mandatory steps.

## D-013 — Face memory: SFace on top of the SDK's YuNet, cv2-free (2026-08-18)

Operator requirement, promoted from a PRD non-goal. Reuse
`reachy_mini.vision.face_detector.FaceDetector` untouched for detection; add
**one** model — SFace fp32 from `opencv/face_recognition_sface` (36.9 MiB,
Apache-2.0, 128-d) — with **zero new Python dependencies**, preloaded like
YuNet. Decisive constraint: no cv2 in the dev app venv, so `alignCrop` and
`blobFromImage` are replicated in numpy (`face_id.py`), following the SDK's own
precedent in `media/camera_utils.py`; our modules import cv2 nowhere and a test
asserts it. Storage is a **sibling** file `faces.v1.json`, never an extension of
`memory.v1.json` (`MemoryFact.to_json` is an external contract read by the
mobile app); same instance path, same reinstall-wipe, same backup/restore
ritual. Privacy is a design property: no image is ever persisted or transmitted
(names, 128-float vectors, timestamps only), recognition is not continuous (one
wake check plus explicit `who_is_this`), enrollment is explicit and verbal,
`FACE_MEMORY_ENABLED=0` removes the feature and `FACE_AUTO_GREET=0` keeps the
tools without the automatic look. Hardware finding: the robot is a Raspberry Pi
**CM4**, not a Pi 5 — one embed costs 239 ms idle / 362 ms with the app running.
Threshold and thread rules superseded by D-015. Storage/marker details extended
by D-024 (alignment field).

## D-014 — Audit outcome: what we accept, defer and close (2026-08-19)

A five-auditor adversarial review of `docs/PRD.md` against the code produced six
real defects, fixed in `a5f682d` (background-tool wedge guard at both call
sites, per-server MCP discovery isolation, `move_head` body-yaw arguments, the
face-tool reason contract, dead package data, a dead env key), and four standing
rulings. **(1) The local console and `/rpc` stay unauthenticated** — served on
`0.0.0.0:7860` with methods that make Reachy speak, interrupt it, mute the mic
and rewrite persona/tool settings, with no password, token or origin check;
accepted as-is for a POC on a trusted home network, documented in PRD §12.7 and
the root README, and to be revisited before anything leaves a home LAN. **(2)
The idle-motion policy stays** — after 180 s of inactivity the app picks a
movement locally and plays it without telling the model, and since
`idle_do_nothing` is not in the locked profile's `default_tools` the idle timer
in this build *always* moves; accepted as personality. **(3) Notion is deferred,
not blocked** — the bundled web-search tool is already a remote MCP Space
discovered through the same seam and has run live, so F-K3 is satisfied; a
second server is a new `mcp_servers._SERVER_ENV` tuple plus two env vars. **(4)
The face tool's `reason` is a closed machine-code contract** — an
`Identification` is echoed verbatim to the cloud model, so `reason` is typed
`IdentificationReason`, a seven-member `Literal`: `face_memory_disabled`,
`camera_disabled`, `no_frame`, `unsupported_frame`, `model_unavailable`,
`invalid_name`, `internal_error`; exception detail is logged locally, never
travels, and tests pin the closure.

## D-015 — Face pipeline tuned from measured evidence (2026-08-19)

**int8 quantization is rejected**: the Cortex-A72 has no dot-product
instructions, so ORT's int8 path is slower than fp32 (OpenCV zoo's own Pi 4
benchmark: **27 % slower**); the fp32 model stays. **Alignment uses all five
landmarks and the threshold is OpenCV's**: YuNet computes five keypoints but the
SDK parser discards the mouth corners, so `face_id._decode_five_points`
re-parses the same raw outputs in a `FaceDetector` subclass overriding `_decode`
only, completing `REFERENCE_POINTS` to the canonical five-row ArcFace template;
the pipeline now reproduces `alignCrop` semantics, so **`FACE_MATCH_THRESHOLD`
defaults to 0.363** (OpenCV's own number for this model) with the 0.05 margin
rule unchanged. **Consequence: embeddings enrolled under the three-point warp
are not comparable — any pre-existing `faces.v1.json` must be re-enrolled.**
**The SFace session runs on three threads with busy-spinning off** —
`session.intra_op.allow_spinning=0` plus `FACE_ORT_INTRA_OP_THREADS=3` (default
3, clamped 1–4), superseding D-013's one-thread rule *for the recognizer only*
(the SDK's YuNet detector keeps its own one-thread session); dev-box median per
embed 17.4 → 8.3 ms. **The wake check may look at up to three frames** —
`FACE_WAKE_ATTEMPTS` (default 3, clamped 1–5) rounds inside the **unchanged**
1200 ms `FACE_WAKE_BUDGET_MS` deadline, 150 ms apart, first confident hit wins.

## D-016 — Persona externalized to an instance `persona.md` (2026-08-20)

Operator-requested: rewriting the character must not require a redeploy. A
`persona.md` in the app's instance directory (beside `.env`) overrides the
built-in locked profile; `PERSONA_FILE` (absolute path) moves the lookup. The
parser is **shared** with `profile.md` — same `+++` delimiter, same TOML dialect
— with every field optional (`voice`, `greeting`, `default_tools`; `hidden`
deliberately not accepted) and the body as persona text; fields the file omits
keep the built-in value, so the overlay is per field, not per file. **Every
failure is total, never partial**: missing, not-a-file, unreadable, bad TOML,
unknown key or empty body → WARNING naming the problem and the built-in profile
used *whole*. One proof line — `persona: instance persona.md (<path>)` or
`persona: built-in locked profile` — is logged from `main.run` after the
instance `.env` loads; resolution is cached on path+mtime+size. **It is user
state**: inside site-packages, wiped by reinstall, covered by the deploy skill's
backup/restore; losing it silently reverts the character and that startup line
is how it is caught.

## D-017 — VoiceFX static: comb + soft knee replace the tremolo and the hard clip (2026-08-20)

Operator report: "not very robotic and cute, rather full of static noise."
Diagnosed on the **shipped code**, chunked as the runtime chunks it: the "ring
modulator" `x*(1-mix) + x*sin*mix` is an interpolation — a 6.02 dB **tremolo**
whose 55 Hz carrier sat at the psychoacoustic roughness peak (+23.1 dB of
30–120 Hz envelope energy) — and its −2.26 dB RMS cost is *why* makeup gain was
+5 dB, which then hard-clipped 3.29 % of samples and overshot the 24k→16k
resample into a second clip.

New chain: `int16 → float32 → WSOLA+soxr pitch (unchanged) → feedback comb →
[AM, off by default] → makeup gain → soft-knee saturator → np.clip(±1) (retained
no-op) → int16`. The comb is the only colour stage measuring *cleaner* than the
untreated reference, because it is LTI. A lookahead-free limiter was implemented
and rejected: quieter, 6.7× the CPU, ~3 ms more latency and **chunk-size
dependent** — fatal, since delta sizes are not ours to control. The saturator is
memoryless and tanh-asymptotic, so the trailing clip is a genuine no-op. The
chain is byte-exactly chunk-invariant; state resets via `VoiceFX.reset()`,
reached by barge-in through `_reset_output_pipeline`.

Defaults: `VOICEFX_PITCH_SEMITONES` 4.0 → **5.0**; `VOICEFX_RINGMOD_HZ` 55.0 →
**0.0** with legal set `{0}` and `[150, 4000]`; `VOICEFX_RINGMOD_MIX` 0.25 →
**0.0**; new `VOICEFX_COMB_MS` **4.0** (0 = off, else clamp 0.5–20),
`VOICEFX_COMB_FEEDBACK` **0.45** (clamp 0–0.9), `VOICEFX_COMB_MIX` **0.35**
(clamp 0–1), `VOICEFX_CEILING_DBFS` **−1.0** (clamp −12–0), `VOICEFX_KNEE`
**0.75** (clamp 0.1–0.99, read as a *fraction of the ceiling*);
`VOICEFX_GAIN_DB` keeps 5.0 and its −6..12 range but now means "gain into the
saturator". A carrier in `(0, 150)` is **refused with a warning and defaults to
off**, not clamped up — 55 Hz, the value that shipped, is unreachable. **No
`VOICEFX_PRESET`** (it would silently shadow explicit `.env` values);
`.env.example` carries three paste-able blocks instead — "cute robot (default)",
"more metallic", "plain pitched voice". The startup INFO line names the whole
chain and is how the operator verifies what is deployed. Also fixed:
`streaming.audio_to_int16` now clips before scaling (latent int16 wrap).
Measured: 0.00 % clipped samples at every level from −20 dBFS to full scale,
true peak −0.98 dBTP, latency delta zero, CPU ~16 % of one CM4 core. Live
values superseded by D-021.

## D-018 — HomeAssistant-Nova ported natively, not consumed over MCP (2026-08-21)

The operator's `ha-actions` stdio MCP server (23 tools, pinned to one Mac) was
**ported natively** because consuming it would push real
calendar/Drive/Gmail/task/place identifiers into every model prompt (porting
externalises all of it to configuration, and `tests/test_hanova_integration.py`
fails if any reappears in the package, profile or `.env.example`), because its
synchronous single-threaded stdin loop makes `stop_music` unanswerable during a
long `play_music_here` — a robot that cannot be stopped by voice — because
32.5 KB of descriptions rewritten at ≤120 chars each is ~2 KB, and because our
MCP client speaks Streamable HTTP only. The model now sees **39 tools**.

Deliberate behavioural changes: `play_music` always plays on the robot's own
speaker (Voice-PE/TV music paths dropped, `play_music_here` merged away); pause
is **synthesised** (the daemon media API is only `play_sound`/`stop_sound`, and
`/api/volume/set` is system-wide) by banking the offset and re-cutting from it;
`show_on_tv` generates its own image via the OpenAI Images API and serves it;
`drive_upload` uploads a freshly captured camera frame at confirm time, never
anyone-with-link readable; NAS auto-advance is not ported (the session keeps the
playlist, `nas_skip` advances it — no background task); media is served from the
app's existing FastAPI server at `/hanova-media` (no new port); Notion's `Owner`
property is dropped (real people's names).

Four cross-cutting behaviours live in code, not in the prompt: **per-tool**
enablement aggregated into one tri-state verdict line per family (the app boots
green with zero new configuration); a **tri-state** home probe —
`away_from_home` only on positive off-home routing evidence against
`HANOVA_HOME_NETWORKS`, and `home_status_unknown`, doing **no** house work at
all, when HA is merely broken, unauthorised or reached over a tunnel; a
90-second confirmation gate scoped to the conversation **and to an immutable
claim id**, executing the *parked* action and spending the authorisation on
success or terminal failure while transient connection/timeout faults
`release()` instead; and metadata-only logging through one shared
`hanova/redact.py`, with exempt modules named and justified in `_REDACT_EXEMPT`.
`email_send` is included but gated: the read-back names **every** recipient (To
and CC), the subject and the **entire message body verbatim**, capped at 500
chars with longer bodies refused as `body_too_long` rather than condensed, and
the send executes the parked envelope, never the second call's args.

**Approved non-goals** (not oversights): Drive restore (a voice-driven untrash
would fuzzy-match over the trash namespace for a capability the Drive UI already
gives for ~30 days; `gdrive.set_trashed` keeps its boolean but no tool may
expose `trashed=False`); email **BCC** (a blind recipient is by definition one
the read-back cannot surface — `send_mail` has no `bcc` parameter at all); and
the self-destruct gag keeps its in-character ritual, using the shared
`ConfirmationGate` TTL/claim/abort but **not** the generic read-back summary (a
test fails if the persona starts pre-explaining it).

**New dependencies (operator-approved):** `yt-dlp`, `imageio-ffmpeg`,
`smbprotocol` — pure wheels, no system packages; `imageio-ffmpeg` over
`static-ffmpeg` because it ships the binary inside a `manylinux2014_aarch64`
wheel. **New instance state (deploy ritual):**
`google-workspace-mcp/<account>.json`, `google-oauth.json`,
`nas-video-index.json`, all added to backup/restore; the `hanova_media/` cache
deliberately is **not** (regenerable, hundreds of MB). In the same move the
robot's connection details left the tracked deploy skill for the gitignored
repo-root `.env`: `REACHY_HOST`, `REACHY_SSH_USER`, `REACHY_SSH_PASSWORD`,
`REACHY_HOSTKEY`, documented by a tracked placeholder-only `.env.example`
(which needed a `!/.env.example` negation placed **after** every
`.env`-matching rule in `.gitignore`).

**One assumption the code cannot enforce:** NAS path containment
(`nas.validate_cast_path`, `HANOVA_NAS_SUBPATH` / `HANOVA_NAS_CAST_SUBPATH`) is
a client-side check over the normalized path string, while `smbprotocol`
follows symlinks and reparse points wherever the **server** resolves them — so
the two configured subpaths must contain no symlink leading out of the subtree.

Later live-found fixes: `dd591f2` — the NAS source-path bound wrongly gated the
*original* file's extension, refusing DVD-era trips whose transcoded `.mp4` twin
is what actually plays; the gate now applies only to the cast copy. `c4e1951` —
YouTube began demanding a JS runtime, so `HANOVA_YTDLP_EXTRACTOR_ARGS` forwards
`--extractor-args` (`youtube:player_client=android` verified). Latency pass:
`play_music` downloads the native audio stream instead of re-encoding mp3 and
`HANOVA_YTDLP_SEARCH_N` defaults to 2 (command→audible 15.8 s worst case);
`/hanova-media/nas-stream/` proxies the requested byte range off SMB
(`HANOVA_NAS_STREAM=0` restores staging), taking whole-trip start to 0.32 s and
skip to 0.27–0.30 s. Next-clip prefetch and serving the TV straight from the NAS
(DLNA/Plex) were both **rejected** — the copy they hid no longer exists, and
both would couple our curated index to a second server.

## D-019 — Keep the static 39-tool array on gpt-realtime-2.1 (2026-08-22)

No lazy schema loading on the Realtime endpoint. Measured: tools and
instructions are sent exactly once per connection; the 39-tool array is ~4,350
tokens, instructions ~1,060, session-open ~5,400 — about 1/3 of the documented
~16k instructions+tools ceiling. OpenAI's deferred tool loading
(`tool_search`/`defer_loading`) is Responses-only. Mid-session tool injection
would bust the KV-cached prefix (turning a one-time prefill into a recurring
per-turn cost) and add an audible round trip — the inverse of the vendors'
designs, which append at the END of context. The real risk axis at 39 tools is
selection **accuracy**, not latency (OpenAI: "fewer than 20 functions" as a soft
bar; Anthropic: degradation at 30–50). Follow-ups: instrument `cached_tokens`
per turn, watch for silent truncation, trim the `dance` schema (2.4 KB, 13 % of
the array). Escalation only on real misrouting: the realtime handoff pattern or
a per-turn `response.tools` experiment. Findings:
`docs/research-realtime-tool-payload.md`.

## D-020 — Robot LAN address and SSH user in the tracked handoff (2026-08-22)

Operator-authorized: `REACHY_HOST` and `REACHY_SSH_USER` values may appear in
the tracked `session-handoff.md` so a fresh checkout on the Mac mini reaches the
robot — deliberately reversing the review-round scrub for exactly these two
values, in a private repository. The identifier scan carries an absolute cap
(`_OPERATOR_AUTHORIZED_DISCLOSURES`: one private-IPv4 occurrence in
`session-handoff.md`); a second address there, or any address anywhere else,
still fails. **`REACHY_SSH_PASSWORD` and the host-key fingerprint remain
forbidden in every tracked file, permanently.** Reverting means removing the
handoff block and the scan cap; the value stays in git history either way.

## D-021 — Reachy's voice: coral + V13 "robot-3"; volume 90; persistent journald (2026-08-23)

A 13-version live audition (`scripts/voice_audition.py`) ended with the operator
picking **V13**: base voice **coral**, pitch **+5.0 st**, comb **2.0 ms /
feedback 0.62 / mix 0.55**, ring-mod **250 Hz at 0.16 mix**, into the D-017
soft-knee ceiling. Baked in permanently: `voice = "coral"` in the tracked
`persona.md` front matter (deployed sha-exact) and the six `VOICEFX_*` lines
written into the instance `.env` proper (audition markers removed). Speaker
volume set to **90** via `/api/volume/set` and force-saved with `alsactl store`
— it survived a hard power loss, so it is genuinely persistent. Same day the
robot dropped off the LAN hard (matches upstream `reachy_mini#1115`, closed
without a documented fix) and the RAM journal destroyed the evidence, so
journald on the robot is now **persistent and capped**:
`/etc/systemd/journald.conf.d/90-persistent-capped.conf` (`Storage=persistent`,
`SystemMaxUse=100M`) plus `/var/log/journal`. This touches systemd only, not the
Reachy daemon, and reverts by deleting that file and directory.

## D-022 — TV cast fixed: HA cast-entity churn, scripts retargeted, runbook (2026-08-24)

"Cast succeeds but TV shows nothing" was **entity churn in Home Assistant**, not
a Chromecast/YouTube protocol change: the living-room device had re-registered
over time, leaving four `media_player` entities, and `HANOVA_CAST_ENTITY` *and*
the hardcoded `target:` in all three `tv_show_*` HA scripts pointed at the dead
original (`…an_he_ke_ting`, state `unavailable`). The live video-cast entity is
`…an_he_ke_ting_3`; `…_4` is a Music Assistant **speaker** proxy for the same
device and must never receive video. Fix: all seven references retargeted to
`_3` via the HA script config API (the operator's HA was modified — recorded
here), and the robot instance `.env` now carries the live entity plus
`HANOVA_CAST_CONFIRM_S=45` (a cold YouTube-app launch measured >25 s, and the
first 25 s window produced a false `tv_not_responding`). The same-day
`ha_client.confirm_cast_started` honesty path is what made the corpse visible:
HA accepts a script run regardless of the target's state, so dispatch was being
reported as display. **Why this survives:** robot-side values are in the
instance `.env` (covered by backup/restore); the script changes live in HA's own
storage. **If it recurs** (the TV re-registers as `_5`): Reachy now *says* the
TV is not responding instead of claiming success — that sentence is the alarm.
Runbook: list HA's `media_player` entities, find the live 安和客廳 entry that is
neither `unavailable` nor the `device_class: speaker` proxy, then point
`HANOVA_CAST_ENTITY` and the three scripts' `target:` at it.

## D-023 — Voice robustness round (2026-08-25)

Eight tasks against `docs/research-realtime-voice-best-practices.md`, on top of
the 2026-08-24 multi-person hardening (T1–T3 in
`docs/multi-person-investigation.md`: `input_audio_noise_reduction: far_field`
on every session, robot `REALTIME_VAD_THRESHOLD=0.7`, and party mode's
`party_mode` tool, debounced client barge-in and address gate). Codex reviewed
in three rounds (18 findings, 18 accepted). Suite 1211/31 → 1309/31 in-round
(1319/31 after the final fix wave `3899d5c`), ruff + mypy clean. Written as
verified against unit tests and a fake connection only; on-robot rows live in
`feature_list.json`, and the 2026-08-27 install verified the boot gate and the
transcription upgrade live.

**Model default → `gpt-realtime-2.1-mini`.** `openai_realtime.realtime_model()`
returns `REALTIME_MODEL` if set, else the mini model — $10/$20 vs $32/$64 per 1M
audio tokens, a 3.2× cut on the dominant cost of an always-listening companion.
Risk: mini's tool-calling quality across the tool array, exactly the axis D-019
flagged. **Revert: `REALTIME_MODEL=gpt-realtime-2.1`** in the instance `.env`.

**Backchannel/substantive classifier (`audio/backchannel.py`).**
`is_backchannel`/`is_substantive`, a lexicon+length heuristic with CJK atom
segmentation (so 嗯哼/好喔 decompose instead of passing as "unknown, therefore
real"); `REALTIME_MIN_TURN_CHARS` (default 2) is the content floor. Consumed by
the party gate and the solo rollback; no standalone revert lever.

**`wait_for_user` no-op tool + prompt hardening.** A tool
(`tools/wait_for_user.py`, `needs_response=False`) the model calls to end a turn
silently instead of speaking into silence, noise, TV audio or side conversation
— an affirmative action is more reliable than asking a model to do nothing.
`prompts._HARDENING_BLOCK` (non-addressed audio → `wait_for_user`; ask once for
a repeat on unclear audio; pin to Taiwan Mandarin unless the user switches) is
appended **after** the resolved profile body, including an operator `persona.md`
override (D-016), so a persona rewrite cannot silently drop it. **Revert:
`REALTIME_PROMPT_HARDENING=0`** (the tool stays registered).

**Transcription: `gpt-transcribe` + `keywords` + prompt.** Default model is now
`gpt-transcribe` (OpenAI's retirement notice for `gpt-4o-transcribe` was the
trigger), with `keywords` defaulting to `REALTIME_PARTY_ADDRESS_NAMES` and a
free-text `REALTIME_TRANSCRIPTION_PROMPT`. If the API rejects the session
config, the handler retries **once** with the legacy shape and logs `retrying
with legacy transcription shape` — the absence of that line is the positive
signal. **Revert: `REALTIME_TRANSCRIPTION_MODEL=gpt-4o-transcribe`** (or
`whisper-1`).

**Per-response onset amplitude ramp** (`REALTIME_ONSET_RAMP_MS`, default
120 ms). Each reply fades in linearly from silence so the hardware AEC converges
before the mic self-triggers on a hard onset; applied at the emit chokepoint
ahead of VoiceFX and re-armed on every barge-in rollback resume. **Revert:
`REALTIME_ONSET_RAMP_MS=0`** is byte-identical to the old path.

**Boot gate.** The first session opens with `turn_detection=None` so the
greeting cannot commit as a "user" turn via the robot's own speaker. Release
fires on the first `response.done` while gated, then waits for
`audio_drain.is_audible()` to clear (100 ms poll, **3 s cap**) before
re-enabling VAD and clearing the input buffer; `REALTIME_BOOT_GATE_TIMEOUT_S`
(default 8 s) is the hard backstop. Journal: `boot gate released (<reason>)`.
**Honest limitation:** the effective ceiling is `response.done` + the 3 s drain
cap, which can exceed the configured timeout — draining audio is the correctness
condition, so that env is not a hard upper bound. **Revert:
`REALTIME_BOOT_GATE=0`.**

**Party gate hardening + face signal.** Order: control phrases (always pass) →
backchannel/substantive filter (always deny non-substantive) → name match → open
follow-up window → face signal. The face branch accepts an unaddressed but
substantive turn when `get_tracked_face(wait=False)` (the SDK's non-blocking
cached read, never a new capture) reports a face that is detected, fresh
(`REALTIME_PARTY_FACE_FRESH_S`, default 3.0 s, checked on `time.monotonic()`
matching `FaceTarget.ts`) and centered (`REALTIME_PARTY_FACE_CENTER`, default
0.4 normalized x). Session start resets the follow-up window. Journal: `party
gate: accepted via engaged face` / `party gate: denied ambient turn`. **Stated
limitation:** "detected and centered" is a presence proxy for orientation, not
gaze. **Revert: `REALTIME_PARTY_FACE_GATE=0`** leaves name + follow-up window.

**Solo pause-then-decide barge-in.** In solo mode only, `interrupt_response` is
now `false`. On `input_audio_buffer.speech_started` while a reply is audible the
handler **pauses** playback into a FIFO instead of flushing, and the transcript
normally decides. `REALTIME_BARGE_CONFIRM_MS` (default **1400 ms**) is the
sustained-speech backstop and **must outlast
`REALTIME_VAD_SILENCE_DURATION_MS` (800 ms)** — the server cannot report
`speech_stopped` until its silence window elapses, so a shorter confirm makes
every rollback branch dead code; `warn_if_barge_confirm_races_vad()` warns at
session-config build. A confirmed barge cancels the response — unless
`_active_response_id` has moved on from the id captured at pause, in which case
that response is the *answer* to the barging turn and is kept — flushes the held
audio, opens `REALTIME_BARGE_COOLDOWN_MS` (default 800 ms) against the cancelled
tail's echo, and arms a 1.5 s watchdog (`_BARGE_RESPONSE_WATCHDOG_S`, not
env-tunable) to repair the auto-response the server's one-active-response rule
would reject — **that rejection mechanism is unverified against the live API**.
Rollback (resume with the onset ramp re-armed) on backchannel, empty or failed
transcript, or `REALTIME_BARGE_ROLLBACK_TIMEOUT_S` (default 2.0 s) with no
transcript. A control phrase (`_PARTY_CONTROL_RE`: 停/閉嘴/stop/…) is checked
**before** the substantive test and always confirms — 「停」 is one character and
would otherwise fail `REALTIME_MIN_TURN_CHARS=2`, leaving the robot talking over
the person telling it to stop. Journal: `barge-in rolled back; resuming reply`,
`solo barge rolled back (<reason>)`. Parked, non-blocking: a held-audio drop
without the matching `note_cleared()` when `_clear_queue` is `None`
(unreachable in production). **Revert: `REALTIME_SOLO_CLIENT_BARGE=0`** restores
immediate flush + server-side `interrupt_response=true`.

## D-024 — Face recognition made reachable: routing, retries, a real wake window (2026-08-27)

The recognizer core was never the problem. Live robot evidence (journal
2026-08-24 → 27): the boot wake check failed **14/14 times** since Aug 24 (8×
`no_face` — nobody posed in frame at that single instant — 2× a `multiple_faces`
hard refusal, the rest inside a 1200 ms budget that fits only 1–2 of its 3
rounds on the CM4); asked 「是誰。」 in the 2026-08-24 party session the model
called **`camera`**, never `who_is_this`; meanwhile same-session recognition
scored **0.594** against the D-015 threshold 0.363 and cross-person similarity
(Lena↔Louis) measured **0.1446**, matching the 0.11/0.15 wake scores that were
correct rejections. The store had also been **empty Aug 19–26** (`score=None`
wake lines) with nothing logging how many people were loaded. Five decisions
follow. **(1) Identity questions belong to the face tools — in the descriptions
*and* in the persona:** `camera` now emphatically disclaims identity and names
`who_is_this`, the two face tools claim identity and enrollment, and the rule is
written into both `profile.md` and the instance `persona.md`, since D-016 makes
the latter the body the model actually reads. **(2) Identification scores the
largest face; enrollment still requires exactly one:** the selection rule is the
SDK's own (`face = max(faces, key=_area)`, `reachy_mini/vision/face_tracking.py`)
so recognition scores the face the head is already aiming at, while storing a
bystander under the user's name is worse than refusing — `enroll` keeps the
exactly-one contract and `multiple_faces` stays a member of the closed
`Identification` contract (D-014). **(3) Retries live in our tool layer, not in
the recognizer:** `capture_frame` retries `None` frames (the appsink is
drop=True/max-buffers=1 — a miss is routine on a loaded CM4),
`identify_with_retries` looks up to 3 times with the first recognition winning
and the most informative miss reported otherwise, and one `remember_face` call
stores up to 3 samples, the extras best-effort so a refused one is never an
error. **(4) The wake check gets a bounded second window, not continuous
scanning:** `FACE_WAKE_EXTENDED_MS` (default 8000, clamp 0–20000, 0 disables)
keeps looking *after* the greeting is queued, so the greeting is never delayed;
every await is bounded against the shared deadline, the window closes silently
once the user speaks (a context item landing mid-turn could steer the answer),
it aborts if the session reconnects, and it is cancelled-and-awaited at
shutdown. It remains the **single** automatic recognition hook — D-013's privacy
property holds. **(5) The store declares its alignment:**
`faces.ALIGNMENT_VERSION = "arcface5"` is written into every record and a
*different* marker drops that record with a warning, while records with **no**
marker are grandfathered (the live Louis/Lena records *are* arcface5 and get
stamped on their next rewrite); the ready log now reports `N people enrolled`,
and a malformed embedding reports `internal_error` instead of the mislabeled
`invalid_name`. Fixed in passing: the 0.05 margin rule now only compares
candidates that **themselves** clear the threshold (a stranger at 0.35 no longer
drags a 0.38 match to `ambiguous`), and `scripts/preload_assets.py` downloads
the SDK-pinned YuNet revision so the warmed cache entry is the one
`FaceDetector` actually loads. **Deliberately not done:** continuous recognition
(rejected on privacy, D-013); a forget/list-faces tool (no operator need yet —
the store is editable on disk); retry after a failed model load (a load failure
is a deploy defect and retrying would hide it); relocating `faces.v1.json` out
of site-packages (the `reachy-deploy` backup/restore ritual covers it, and
moving it is a deploy-wide change, not a face change). Written as verified
against the unit suite and the RCA only — the four `FACE-*` rows in
`feature_list.json` are the live gate.

## D-025 — Person memory: a Mac-authoritative backend, a projected robot (2026-08-28)

The PRD §9 amendment of 2026-08-28 promotes five non-goals into the POC:
per-person memory, a recognition-aware boot greeting, still-pose enrollment, a
Mac-side management backend with a UI, and photo-upload enrollment. Spec:
`docs/superpowers/specs/2026-08-28-person-memory-and-backend-design.md`; plan
and its three-round Codex log (22 findings, 20 accepted, 2 partially, 0 rejected
outright): `docs/superpowers/plans/2026-08-28-person-memory-and-backend.md`.
Seven decisions. **(1) Architecture option C — the Mac is the source of truth,
the robot holds a projection.** `companion_backend/` (FastAPI plus a vanilla
ES-module UI on `127.0.0.1:8710`, run out of `reachy_companion/.venv`, **no new
dependency**) owns `data/people.json` — names, uploaded photos, the SFace
vectors computed from them, per-person facts — and projects it onto the robot's
`faces.v1.json` + `people.v1.json` **through the robot's own writers**
(`faces._write_faces_file`, `people.upsert_person`/`add_person_fact`), so the
`arcface5` marker, the 12/3/20 caps, the fact ordering and the eviction policy
are right by construction rather than by a second serializer. The robot's copies
are rebuildable and are wiped by every reinstall; this store is not — that
inversion is the whole point. Sync is a **guarded remote promote** (scp both
files to temp names, one ssh command re-checks the pre-push sha256s and `mv`s
both into place, then a read-back verifies the robot's own bytes), drift is
hash-based against the last verified push, and a push is **refused exactly when
the robot holds content the backend does not know** — one import clears it. Push
deliberately does **not** restart the app: both stores are re-read per use, so a
pushed face and a pushed fact apply live; only the global memory prompt is
session-scoped and this feature does not touch it. **(2) The boot greeting is
three-way, on a 4 s wake budget.** `_send_startup_greeting_prompt` branches on
the full `Identification`: *recognized* → a prefix carrying the name and up to
`FACE_GREETING_FACTS` (default 6) of that person's facts, instructing a warm
greeting by name with no self-introduction; *stranger present*
(`unknown`/`ambiguous`/`multiple_faces`/`too_far` with `face_count > 0`) → a
prefix saying someone unfamiliar is here; *nobody* → the profile greeting
verbatim. All three are prefixes on the existing profile `greeting` (the
`_FACE_GREETING_PREFIX` pattern), so no profile or persona schema field is
added. `FACE_WAKE_BUDGET_MS` 1200 → **4000** and `FACE_WAKE_ATTEMPTS` 3 → **5**,
on the unchanged single-shared-deadline mechanism, exiting early on the first
confident hit; the facts read is off the loop under a 1 s bound with
warn-and-continue, on both the greeting path and D-024's extended window (whose
spawn condition becomes "unless the greeting already went to a recognized
person"). Accepted edge: a `multiple_faces`/`too_far` boot speaks the **stranger**
line even with an enrolled person among the faces — only `recognized` takes the
named branch — and the extended window is the correction that arrives seconds
later. **(3) Person scoping is one runtime label, cleared per session.**
`ToolDependencies.current_person` is set on boot-wake recognition, on
extended-window recognition and on every `who_is_this` → `recognized`; `remember`
then writes into that person's `people.v1.json` record and returns
`scope: "person:<name>"` (global store as the fallback when the person store
refuses the name), `forget` searches that person before the global store, and
`who_is_this` returns `known_facts`. It is cleared at handler init **and on
reconnect** — per *session*, not per app run — and a non-recognized `who_is_this`
deliberately does **not** clear it (R1-4: a blink or a `too_far` glance must not
drop a valid label mid-conversation). It is a memory label only; nothing gates
behaviour on it. **(4) Enrollment holds the head still.** `remember_face`
brackets its capture burst in `tools/face_support.hold_still`: the head parks at
its current pose on a weight-0 anchor, wobbling is disabled, 0.35 s of settle
runs before the first frame, and the release lives in a `finally` inside the
guarded region so a cancellation cannot leave the robot parked. Mid-hold,
`set_speaking`/`set_head_tracking` update their flags but issue **no** robot
calls — the state they demand is applied on release — and a queued move is
dropped with a log line: the photo wins. The wobble re-enable is unconditional
(no SDK getter, and the only wobble-off state is sleep, which enrollment cannot
reach). **(5) `embedding_for_frame` is the shared seam.** The extraction half of
the recognizer — detect, require exactly one face large enough to embed
honestly, align, embed — with neither `identify()`'s store comparison nor
`enroll()`'s store write, so the Mac embeds uploaded photos through the
identical YuNet+SFace pipeline and a vector computed there is comparable to one
enrolled by voice; `enroll()` keeps its already-matches return contract
(extract → match → upsert). Photos still never reach the robot: the projection
carries vectors only. **Unchecked on a real person:** that a Mac-embedded photo
and a robot voice enrollment of the same face actually score like a same-person
pair — `BACKEND-PUSH-LIVE` owns that measurement. **(6) Robot-side deletions are
imported, not reverted.** Push snapshots the two projected files under
`data_dir/last_push/`; a fact in that snapshot and absent on the robot now is a
robot deletion — it blocks the push, and the import applies it to the Mac store.
Two guards keep it conservative: the deletion is only honoured while the fact is
still on the Mac, and never while the robot person sits at the 20-fact cap
(indistinguishable from eviction). Consequence, accepted: a Mac person with ≥20
facts always projects at exactly 20, so removals are never readable for them.
`changed_faces` uses **subset** semantics (the robot holds an embedding the
backend does not know), not set equality — equality would block pushes for the
legitimate Mac-side edits the push exists to deliver. Face *removals* are not
modelled at all: no robot-side person-deletion tool exists. **(7) The backend
store is durable, so a corrupt one is never clobbered:** an unparseable
`people.json` is renamed `people.json.corrupt.<epoch_ms>` with a WARNING naming
the path and the store starts fresh, where the robot projection instead tolerates
per-record damage (`faces.py` idiom). The backend is a **trusted-LAN POC
surface** with the standing of the PRD §12.7 console ruling: `127.0.0.1` bind, no
auth, no CSRF, no rate limiting; reach it from another machine by ssh tunnel,
never by re-binding. **Deliberately not done:** a robot-side `people.*` RPC
(option B — a wheel rebuild per change, and it widens the unauthenticated `:7860`
surface); extending `memory.v1.json` / `MemoryFact.to_json` (a locked external
contract, D-013 — person facts live in a sibling store); greeting or person
fields in the profile/persona schema (both closed metadata field sets stay
closed); any new conversational tool (the 41-tool array does not grow);
mid-conversation automatic re-identification (D-013's privacy property holds —
the wake check and its bounded extended window remain the only automatic hooks);
face photos on or in transit to the robot; and deploy from the backend UI (D-009
keeps a human in the loop). **Verified against the unit suites and one reduced,
face-less Mac selftest run only** — the seven `PERSON-*` / `ENROLL-STILL` /
`BACKEND-*` rows in `feature_list.json` are the live gate, and `people.v1.json`
must join the `reachy-deploy` backup/restore manifest before any of it survives
a reinstall.

## D-026 — Profile merge, and one enrollment snapshot per person (2026-08-28)

Operator-requested after the *first live use* of the backend, which found two
things D-025 had no answer for: the robot misheard "Linna" as "Lena" and
enrolled one person twice, and a person imported from a voice enrollment had no
picture anywhere. Plan and its three-round Codex log (12 findings, **all 12
accepted**, review closed at the round cap):
`docs/superpowers/plans/2026-08-28-merge-and-snapshots-addendum.md` §Review log.
Five decisions. **(1) A merge is a fold that leaves two kinds of memory
behind.** `store.merge_people` folds the source into the target under one lock
hold and one `_write_document`: facts through the store's own case-insensitive
dedupe, photo records *and* photo bytes (`stored_as` is photo-id-based, so the
move cannot collide), and — for both lists — an **interleave by timestamp,
newest-first, stable on ties** rather than a concatenation, because the
projection takes the newest ≤3 embeddings and ≤20 facts off the front and the UI
prints each row's own time. The target keeps its own `face_id` and adopts the
source's only when it had none; every id that does not end up primary lands in
`former_face_ids`, ids the source had itself inherited included, so a *chain* of
merges cannot forget the robot ids at its start. The source's name and aliases
become the survivor's `aliases`, normalized by `faces.normalize_face_name`
exactly as names are. Source person and directory deleted. **(2) One
normalized-name index over `name` + `aliases`.** `create_person`,
`rename_person`, merge and import-attach all ask the same index the same
question, so one normalized string reaches at most one person; renaming onto your
*own* alias is allowed and swaps (the merge's undo), onto anyone else's name or
alias is `DuplicateNameError` → 409. Concrete exception classes throughout, since
the API maps them: `PersonNotFoundError` → 404, `MergeError` → 400,
`DuplicateNameError` → 409. **(3) The changed-face test is redefined
store-wide**, and this **retires D-025's accepted three-slot re-block quirk**: a
known robot record is *changed* iff it holds an embedding present in **none** of
the mapped person's stored photos (synthetic included) — not merely absent from
the projected newest-3 window. Content the backend holds anywhere is known
content, so one import always clears the gate, and a push may legitimately
*collapse* several robot records (survivor plus inherited ids) into the single
projected one. The sync layer resolves robot names through aliases and robot ids
through `former_face_ids`; an attach under an alias keeps the primary link and
records the fresh robot id as a former one, or the next diff would report that
face as new again forever. Neither field is ever projected. **(4) D-013 is
amended (operator-directed):** **one** posed snapshot per enrolled person, taken
at the moment of explicit verbal enrollment — the person is knowingly posing into
the still-pose hold — written to `<instance>/face_snapshots/<record_id>.jpg` by
`face_snapshot.save_snapshot` through the ffmpeg binary the `imageio-ffmpeg`
wheel already ships (D-018), atomic tmp+rename, overwritten per re-enroll.
Recognition stays snapshot-free, no other path writes an image, and continuous
capture remains rejected; "no image is ever persisted" now describes
`faces.v1.json` itself, and the `remember_face` tool description says so. It is
**fire-and-forget**: the first accepted sample's frame is copied, the write is
scheduled into an owned task set and wrapped in `asyncio.to_thread` with a 10 s
subprocess bound, and the tool result never awaits it — a snapshot may never
fail, delay or raise into an enrollment. Realistic sizes: ~2.8–4 MB for the raw
HD frame copy, low hundreds of KB for the JPEG, one scp per enrollment; nothing
downscales, because this is the only photo of a person the system will ever have.
**(5) An imported snapshot is a *display-only* photo.** The import best-effort
scps the file for every face it applies (new, changed, attach) and stores it with
`display_only=True` — an explicit persisted flag, never inferred from "has bytes
and no embedding", which also describes an upload mid-embed. The projection skips
the flag **structurally**, so a picture can never take one of the three
recognition slots, and new bytes are sha256-deduped against every photo the
person already has, so a re-import adds nothing. The robot record id is matched
against the exact generated shape `^f_\d+_[a-z0-9]{6}$` **before** it is
interpolated into the remote path — `faces.v1.json` is a plain file on a robot
anyone can ssh into — and an id that fails, a missing file, or a failed transfer
all skip the snapshot **only**, never the face. **Deliberately not done:** no
cascade from `forget_face` (orphan files are harmless and overwritten on
re-enroll, matching the existing no-cascade posture); no backfill — nobody
enrolled before the deploy ever gets a picture; no downscale; no snapshot on any
recognition path. **Consequences to hold:** `face_snapshots/` must join the
`reachy-deploy` backup/restore manifest beside `people.v1.json` or the first
reinstall wipes every picture, and merging is Mac-side so it works the moment the
backend restarts, while snapshots need both the next deploy *and* a fresh
enrollment. **Verified against the unit suites only** — backend 213 passed with
ruff and `mypy --strict` clean, robot 1449 passed / 30 skipped, ruff and mypy
strict clean — with `BACKEND-IMPORT` (extended) and the new `ENROLL-SNAPSHOT` row
in `feature_list.json` as the live gate.

## D-027 — Engagement memory: a last-chat callback, and a consolidation pass (2026-08-29)

Operator direction after the first family session on the fifteenth install: the
robot remembers *facts* but not *conversations*, so a recognized person is
greeted with trivia instead of a follow-up. Plan and its three-round Codex log
(23 findings, 22 accepted, 1 accepted as a recorded limitation):
`docs/plans/2026-08-29-engagement-memory-plan.md` §Review Log. Six decisions.
**(1) Engagement is a prompt problem first.** The persona's `### remember`
section and the `remember` tool description now rank the *open loop* — a plan, a
coming exam, a song being written — above the stable trait, and explicitly allow
a fact to name another enrolled person (「牙牙是雲霓的女兒」); `### who_is_this`
gains one line telling the model to follow up on 「上次聊天」 or an ongoing thing
rather than reciting the memory list. No code path changes — facts are free text
already. The persona half is synced to the robot instance ahead of the wheel
(D-016); the `remember` description rides the next install. The identity-routing
tripwire (`test_identity_routing_clauses_pin_camera_vs_face_tools`) was re-pinned
onto the fact-fidelity wording (`facts as returned` / `never guess a name`) that
the same day's prompt fix introduced. **(2) The unit of memory is the visit, not
the session.** `ToolDependencies` gains `recognized_people: set[str]`,
`session_transcript: deque[tuple[str, str]]` (maxlen 40 =
`sleep_summary.TRANSCRIPT_MAX_ITEMS`, restated because importing it into
`core_tools` would be a cycle) and `sleep_requested: bool`. Neither container is
cleared on reconnect — deliberately unlike `current_person`, which is
per-session (D-025 §3): a dropped websocket is not a new visitor. Every site
that sets `current_person` (boot wake, the extended window, every recognized
`who_is_this`) also adds the name to `recognized_people`. Transcript recording
happens at exactly **two** sites — the accepted user final and the assistant
final — and two neighbouring pushes are excluded on purpose: the party-mode
**denied** ambient turn (speech the robot decided was not addressed to it) and
the solo-barge **rolled-back** backchannel (a 「嗯」 the barge logic
un-committed). Both exclusions came out of the Codex rounds; neither is
conversation. **(3) `上次聊天` is a text convention, not a schema field.**
`PersonFact` gets no `kind` — one would ripple through `backend/store.py`,
`projection.py` and the sync diff, and D-013's `memory.v1.json` contract is
locked. The fact reads `上次聊天（M月D日）：<summary>` and the **prefix alone is
the supersession key**. Being newest-first it lands inside the
`FACE_GREETING_FACTS` (6) facts the boot greeting and `who_is_this` already
inject, so the callback needed **zero** greeting-code change. Supersession is
ADD-then-FORGET, so a failure between the two leaves a duplicate and never zero
— with a FORGET-first branch in exactly two cases: at the 20-fact cap
(add-first would evict a *real* fact) and when an old fact's key is contained in
the new one. The second is not theoretical: `forget_person_fact` matches on a
**case-insensitive, whitespace-collapsed substring, newest-first**
(`people.py:394, 405`), so a same-day re-sleep — and every case- or
whitespace-only variant of it — was a proven silent delete of the fact just
written. Every guard is therefore keyed through
`normalize_memory_text(text).lower()`, the exact key `forget` matches on, and
all four variants are regression-tested. **(4) The summary runs at shutdown,
gated on sleep, and never raises.** `HuggingFaceRealtimeHandler.shutdown()` is
awaited on a live loop, so async work completes there — but it also runs for
settings and backend restarts (`console.py:307`, `:697`), which are mid-visit.
So the `go_to_sleep` closure in `main.py` is the **only** writer of
`deps.sleep_requested`, and the summary runs only under it, once
(`_sleep_summary_done`: `shutdown()` can legitimately run twice, its own call
site plus the session `finally`). It is placed **after** `on_session_shutdown`
and before `connection.close()` — the call can take seconds and the daemon would
play music through all of them with Reachy already in the sleep pose — and that
ordering is pinned by a test, not by a comment. One `gpt-5-mini` call
(`MEMORY_LAST_CHAT_MODEL`), `MEMORY_LAST_CHAT_TIMEOUT_S` 8.0 s clamped 1–30,
`MEMORY_LAST_CHAT_ENABLED` as the kill switch, and the client is built **and
closed** by the writer itself through `hanova.images.build_client` rather than
borrowed, so it cannot close a client out from under its owner. Every failure —
no client, timeout, non-object JSON, a name off the list — logs an exception
*type* and returns 0: memory must never break going to sleep. **(5)
Consolidation is an operator command, not a service.**
`companion_backend/scripts/consolidate.py` over `backend/consolidate.py` hands
the model one person's whole fact list and takes back a merged, de-contradicted
(「以前…，現在…」), usefulness-ranked one. Dry run by default, and the printed
unified diff **is** the write: `after` is normalized and deduped through the
store's own rules, so a preview can never show three items where two land. A
model answer is validated whole or refused whole — half a rewrite applied over
somebody's memory deletes the other half — and a refusal costs that person
nothing. Writes go through the new `store.replace_facts(...,
preserve_updated_at=True)`, the store's only **in-place** writer: a background
pass over everybody must not reshuffle projection's top-12 recency ranking
(`projection.py:104`), and the stored *position* matters as much as the
timestamp because that sort is stable. `texts[0]` is the newest and that
ordering is pinned by a round-trip test through projection into the robot's
prepending writer. The Mac store stays uncapped; only projection trims to 20.
**No scheduler** (YAGNI; launchd is a later choice). And because `store`'s lock
is process-local (`store.py:93`), the CLI **fails closed**: it probes
`:8710/api/config` on every plausible bind — loopback, `COMPANION_BACKEND_HOST`,
and the live `tailscale ip -4`, because the documented production bind is the
*tailnet IP* and loopback alone proves nothing — and only a refused connection
on all of them lets the run continue; an answer, a timeout, a TLS error all exit
3. Exit codes are 0 done / 1 refused or failed / 2 no OpenAI client / 3 backend
up-or-unproven. `--import-first` and `--push-after` reuse `backend/robot.py`
directly so the whole round trip runs with the backend stopped, in the only safe
order (import, consolidate, push); an empty store is never pushed, because a
push projects and would clear the robot's faces. **(6) The callback fact is not
the model's business — and that rule is also a repair.** Every `上次聊天` fact is
popped before the prompt is built, any the model hands back carrying that prefix
is dropped (an invented callback is a callback to a conversation that never
happened), and only the newest popped one is put back at position 0, the body
taking the rest of the robot's 20 slots. That keep-newest dedupe heals a real
sync hole: robot-side removal detection is disabled at the 20-fact cap
(`backend/robot.py:548`, where eviction and deletion are indistinguishable), so
the Mac can retain a stale callback and push it straight back — and
`test_full_sync_cycle_heals_stale_last_chat` pins the whole cycle. One boundary
restated here because it bit twice: `reachy_companion.config` runs
`load_dotenv(override=True)` at **import** time (`config.py:305`), so backend
and CLI code must never import a robot module that reaches it — `LAST_CHAT_PREFIX`
is restated in `backend/consolidate.py` rather than imported, and a test pins the
restatement to the robot's own constant. **Accepted limitations:** a stop issued
from the dashboard or the mobile app writes **no** summary — only the voice
`go_to_sleep` sets `sleep_requested`, and voice sleep is the normal end of a
visit; a multi-person visit gets **topic-level** summaries only, because the
transcript carries no speaker identity and the prompt forbids attributing an
utterance to anyone the transcript does not name (diarization is a non-goal);
cross-person links written into facts surface **one-sided**, since retrieval is
per-person by design (PRD non-goals); and `consolidate.run` assumes **exclusive**
store access — the CLI's probe guard is what provides it, and nothing else may
call `run` while the server serves. **Deliberately not done:** SQLite or any
embedding retrieval / `recall_about_person` tool; a scheduler; Mem0/Zep/Letta; a
`kind` field on `PersonFact`; raising the 12-people or 20-fact caps; a new
conversational tool (the 41-tool array does not grow); speaker diarization. All
of it is post-POC per the plan's §Non-goals and PRD §9. **Verified against the
unit suites only** — robot **1468 passed / 30 skipped**, backend **267 passed**,
ruff and `mypy --strict` clean on both sides; `mypy tests/` on the backend runs
against a known baseline (12 errors on `main`, 17 here, the five new ones the
same `attr-defined` monkeypatch-target class `test_robot_sync.py` already
carries). The three `MEMORY-LAST-CHAT` / `MEMORY-OPEN-LOOPS` /
`BACKEND-CONSOLIDATE` rows in `feature_list.json` are the live gate, and the
robot half of it needs the **sixteenth install** — only the persona edit is live
today.

**Amendment (2026-08-30, final whole-branch review).** Both halves of the record
above had a guard that was open, and both are now closed. **(a) The visit is a
time window, not the whole app run.** §(2)'s two containers span different
things — `recognized_people` is unbounded and spans the run, `session_transcript`
is its last 40 lines — so a robot left awake all day gave *every* name it had
ever recognized a summary of the *evening's* tail: the person greeted at 09:00
got tonight's visitor's topics written into their 上次聊天 fact, and read back to
them at the next greeting. So entries are now stamped with `time.monotonic()`
(§(2)'s `deque[tuple[str, str]]` is `deque[tuple[str, str, float]]`),
`ToolDependencies` gains `recognized_at: dict[str, float]`, and
`record_recognition(name)` is the single sanctioned writer of both containers —
all three recognition sites call it, and the LAST sighting wins. Before building
the prompt, `write_sleep_summaries` keeps only the guests the tail can honestly
speak for: **once lines have been evicted**, a guest must have been last seen
at-or-after the oldest surviving line; while nothing has scrolled out the tail
*is* the whole run and nobody is filtered. An empty roster returns 0 before the
client is built, so an all-stale run costs no token. Two deliberate softenings,
both fail-open: `record_transcript` refreshes the speaker's stamp (talking is
presence — otherwise a long visit, the visit most worth a callback, would end
with no summary at all, its boot recognition having scrolled out), and a name
with no stamp behind it is kept, since no stamp is no evidence of staleness. The
speaker is `current_person` when there is one, and otherwise the **sole** guest
on the list: `_run_realtime_session` clears that label on *every* session
including reconnects (`huggingface_realtime.py:1982`) and the wake checks that
set it are one-shot per handler, so a dropped websocket leaves a live visit
unlabelled — with exactly one person known this run the talking can only be
theirs. With **two or more** known and no label, nobody's stamp refreshes:
guessing whose line it is would recreate the very leak this closes. **Residuals,
deliberate:** a guest is dropped once 40 transcript lines have elapsed since
their last recognition *or* last heartbeat — **whether or not they are still in
the room speaking**, because the heartbeat follows the label and there is no
diarization (a second guest in an unlabelled multi-person visit is therefore the
case that loses a callback); and in the other direction, while the tail is not
yet full an hours-old guest is still summarized, because their own lines are
still in the transcript being summarized. **(b) The consolidation
guard now asks the kernel first.** §(5)'s probe list is a *guess* at addresses,
and it missed the LAN bind: `COMPANION_BACKEND_HOST=192.168.x.x ./run.sh` in
another shell puts that variable in *that* shell's environment, `tailscale ip
-4` never reports a LAN address, and so every candidate refused and the guard
failed **open** — the one direction it must never fail. `backend_objection` now
runs `lsof -nP -iTCP:8710 -sTCP:LISTEN` **before** the HTTP probes (cheaper, and
it sees every bind); any listener is an objection naming the command that holds
the port. A missing or unhappy `lsof` is explicitly **not** a refusal — failing
closed on an absent binary would strand operators and buy nothing, because the
probes below are the floor and already fail closed. The README's §Consolidation
carries both, plus the reminder to export `COMPANION_BACKEND_HOST` in the CLI's
own shell when the server runs with a custom bind. Suites after the amendment:
robot **1480 passed / 30 skipped** (`test_sleep_summary.py` 19 → **31**),
backend **271 passed** (`test_consolidate.py` 37 → **45**), ruff and
`mypy --strict` clean on both sides. `mypy --strict tests/` on the backend moves
17 → **25** against its known baseline, every new one the same `attr-defined`
monkeypatch-target complaint about `cli.shutil` / `cli.subprocess` the file
already carried eight times. Still **no on-robot run**: both fixes ride the
sixteenth install, and the visit window adds a second negative control to
`MEMORY-LAST-CHAT` (two people hours apart, only the recent one summarized).
