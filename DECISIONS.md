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

**Amendment (2026-09-03) — speaker volume 90 → 95.** Operator ask. Set via
`POST /api/volume/set {"volume": 95}` (daemon reports `{"volume":95,
"device":"Reachy Mini Audio"}`; ALSA PCM playback 57/60 = 95 %, −3 dB) and
persisted with `sudo alsactl store` (`/var/lib/alsa/asound.state` rewritten,
PCM value 57). Why 95 and not 100: the last 5 % is the soft-knee headroom the
VoiceFX chain already drives into (+5 dB into a knee at −1 dBFS, D-017); 100 %
would trade loudness for clipping on the pitch-shifted voice. External
context: low volume is a documented Reachy Mini complaint — the official
troubleshooting page lists "low audio volume" as a known issue (fixed by
updating past 1.2.3, then `alsamixer` PCM as the global control), and
upstream issue pollen-robotics/reachy_mini#569 "[Improvement] louder sound"
asks for more output in noisy rooms or with people who are not close. Our
robot is past that firmware; what is left is the mixer, which is what this
amendment moves. If 95 is still not enough in a noisy room, the next lever is
the VoiceFX output gain (D-017), not the mixer.

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

## D-028 — Name-gated barge-in, turn patience, and an honest interrupted context (2026-08-30)

Operator ask of 2026-08-30: Reachy should barge in only when addressed ("like a
human, listen for barge-in if 'REACHY' is mentioned"), should not rush to reply
"before speaker is silent", and should stay talkative in character without
"obviously speaking too much". Research: `docs/research-realtime-api-2026-08.md`;
compact summary of what changed: `docs/human-like-conversation.md`. Plan and its
three-round Codex log (26 findings — 23 accepted outright, 3 accepted in part or
rejected with a recorded reason):
`docs/plans/2026-08-30-name-gate-patience-plan.md` §Review log. Eight decisions.

**(1) The name gate is on by default, and it gates interruption only.**
`REALTIME_SOLO_NAME_GATE` (default 1) makes a paused solo reply commit only when
`_gate_text_accepts(text)` says the words address the robot: a control phrase
(停/閉嘴/stop — checked first and always winning, gate on or off) or one of
`REALTIME_PARTY_ADDRESS_NAMES`, the same list party mode and the transcription
keyword bias already use. Anything else rolls back and the sentence resumes
where it left off (journal `solo barge rolled back (unaddressed)` /
`(backchannel)`). `0` restores the pre-gate substantive-transcript rule, which
stays shipped. **The scope is deliberate:** an unaddressed turn is still
transcribed, still committed and still answered whenever Reachy is not already
speaking — the gate decides who may take the floor *away*, not who gets an
answer. There is no server-side primitive to lean on (research §4): no wake
word, nothing like "respond only when addressed", so name gating is necessarily
client-side on transcript text, and it slots into the pause-then-decide machine
D-023 already built. Latency lever: `_maybe_commit_on_partial(partial, item_id)`
commits the moment a *partial* delta carries the name, without waiting for
`transcription.completed`. Building it exposed a latent bug — the base partial
accumulator kept only the newest fragment (`deltas = [delta]`, snapshot
semantics), so a name split across chunks (瑞 + 奇) could never match; the OpenAI
subclass now appends, because GA deltas are incremental. With our shipped
`gpt-transcribe` the partials effectively arrive post-commit anyway, so that
fast path is really staged for `gpt-live-transcribe` +
`REALTIME_TRANSCRIPTION_DELAY` (unset by default, the server's own default
standing).

**(2) An unaddressed pause is bounded, and resuming *through* the chatter is the
requested behavior, not a defect.** With the gate on, sustained speech proves
nothing — a name can only arrive by transcript — but an unaddressed 30-second
side conversation must not hold the reply hostage either. `REALTIME_BARGE_MAX_PAUSE_MS`
(default **4000**) is the cap: when it fires the reply resumes and Reachy keeps
talking while the room talks past it (`solo barge pause hit its cap with no
address; resuming reply`). Codex read the resume-while-speech-is-still-open as a
defect; **rejected as a defect, accepted as a doc gap** — it is exactly what a
person telling a story does, and decision 3 is what makes it safe. `0` disables
the gate-on pause entirely, leaving the late interrupt as the only way to stop a
reply. `REALTIME_BARGE_CONFIRM_MS` therefore becomes a **gate-off-only** knob: it
commits nothing while the gate is on, and still governs the
`REALTIME_SOLO_NAME_GATE=0` path, where it must outlast the VAD silence window
(now **1600** over 1000).

**(3) The late interrupt is what makes the cap safe, and eligibility is decided
at speech onset.** `_late_solo_interrupt` fires when a *committed* turn addresses
the robot while it is still audible and the pause is already over — rolled back
at the cap, swallowed by the post-barge cooldown, or never armed — because
otherwise the gate's worst case is Reachy talking over the person who just called
its name. The permissive branch (no resumed id ⇒ cancel) is kept honest by
`_barge_late_eligible`, set in `_solo_speech_started`'s client-barge branch as
`self._robot_audible()` **at onset, before the cooldown return**, so both the
cooldown-suppressed and the pause paths capture it while a turn that started in
silence records False. That design is a **review ruling against the plan text**:
Codex round 1's finding-6 rule protected the cooldown case but let an idle
addressed turn cancel its *own* answer, since with `gpt-transcribe` the answer's
`response.created` routinely precedes `transcription.completed`. Eligibility is
deliberately **not** cleared at `response.done` — a reply keeps draining out of
the speaker long after that event, and that gap is precisely when 「停」 over a
resumed reply arrives; staleness is bounded because the next onset rewrites the
flag. It *is* cleared everywhere the resumed id is cleared, in
`on_external_interrupt`, and on `set_party_mode` flips (a stale value must not
survive a party→solo flip). The late path arms the barge watchdog for the same
reason a committed barge does — with `interrupt_response=false` the auto-response
of a turn that commits mid-reply "may fail to create", so an addressed turn could
otherwise end in silence — and it honours `REALTIME_SOLO_CLIENT_BARGE=0`. Journal:
`late solo interrupt (name|control phrase) on committed turn`.

**(4) Every committed interruption truncates the model's copy of the reply; no
rollback path ever may.** On **WebSocket** the server never trims an interrupted
reply (WebRTC and SIP get it for free), and neither we nor upstream Pollen ever
sent `conversation.item.truncate` — so after every barge the model believed it
had said the whole reply, and then talked as if it had. That is a real driver of
"speaks too much / repeats itself". All three commit paths now send it: the solo
commit, the party barge, the late interrupt. Truncation deletes the item's
transcript server-side and cannot be undone, so **no rollback path may reach it**
— rollbacks resume the audio. `_heard_audio_ms()` is `enqueued − outstanding −
device buffer − 300 ms slack`, floored at 0, always rounding **DOWN**, because an
`audio_end_ms` above the item's real duration is a server error: undershoot
deletes a fragment the user actually heard from context, overshoot loses the
whole truncate. The solo path stashes `(item_id, heard_ms)` at pause time and truncates the stash in
both commit branches — the answer-already-live branch included, since that
paused reply's tail was still dropped — because by commit time the flush has
zeroed the drain counters and `_audio_item_id` may have moved on; the party and
late paths have no pause, so they measure live, ahead of the flush. Three
accepted biases, recorded rather than fixed: `audio_drain.outstanding_s()` is
**global** (there is one sink) while `_audio_item_enqueued_ms` is per item, so
residue from an earlier item can only make the figure *smaller* — under-truncate,
the safe direction; true per-item accounting would need item identity plumbed
through `console.play_loop`'s sink handoff, disproportionate for a POC. On the
pause path the device-buffer term is subtracted from a figure the pause already
froze, so it double-counts and can **over-cut by up to ~1.3 s per barge** — again
the safe direction. And a multi-item reply truncates only its **last** item; the
earlier items keep their full text in context, which is likewise safe.

**(5) The audio-item tracker resets on item change, not on `response.created` —
a second ruling against the plan text.** The plan mandated clearing
`_audio_item_id` on `response.created`; the implementation review showed that
opens a reachable no-truncate hole in the tool-heavy path (a second response
created after a tool call while the first response's audio still plays → the
barge stashes `(None, 0)` → nothing is truncated, the exact symptom this wave
exists to remove). The reset now happens when a delta arrives carrying a
*different* item id, which covers every case the other one covered. Residual,
accepted: a fully drained item keeps its id until the next item's first delta, so
a barge inside that window sends a harmless truncate at about `duration − 300 ms`
of an item nobody is still hearing. A delta with no item id no longer accumulates
into the previous item's tally.

**(6) Patience is three numbers and one pin.**
`REALTIME_VAD_SILENCE_DURATION_MS` 800 → **1000** (the API's own default is 500)
so a Mandarin mid-sentence pause of about a second does not commit the turn; past
roughly 1100 ms the robot reads as sluggish rather than patient, and that is the
practical ceiling. `REALTIME_BARGE_CONFIRM_MS` 1400 → **1600** in step with it,
keeping the gate-off rollback branch reachable. `reasoning.effort` is pinned to
**low** (`REALTIME_REASONING_EFFORT`; `off` omits the field entirely) — gpt-realtime-2.x
reasons before it speaks and `low` is OpenAI's documented voice-agent
recommendation, so pinning stops a future server-side default change from
silently adding pre-speech latency. The field is **not** in the installed SDK
TypedDict, so it rides a runtime-dict cast, and the one-shot session-config retry
**drops it** along with the transcription upgrade: a rejection does not say which
field the server refused, and losing the effort pin on a degraded session costs
latency while a mute robot costs everything. Live mitigation if it is ever
rejected on device: `REALTIME_REASONING_EFFORT=off`; the tell in the journal is
`session.update rejected; retrying with legacy transcription shape`. The other
patience mechanism, `semantic_vad` + `eagerness=low`, is better on paper —
eagerness sets a *maximum* wait (low ≈ 8 s, medium ≈ 4 s, high ≈ 2 s), not a
fixed delay, so a finished-sounding sentence still turns over fast — but it is
not documented as Mandarin-tuned, so it stays one env flip away for the
`VOICE-SEMANTIC-VAD-AB` live trial rather than shipping blind.

**(7) `max_output_tokens` is a rail, not the brevity mechanism — and it never
clamps silently.** `REALTIME_MAX_OUTPUT_TOKENS` defaults to **900**; at roughly
20–25 output tokens per spoken second that is about 40 s of speech, and
`inf`/`off`/`0` removes the ceiling. Hitting it cuts the reply **mid-word** with
no wrap-up sentence and no error anywhere, so a `response.done` whose status is
not `completed` is now logged — `Reply cut off by REALTIME_MAX_OUTPUT_TOKENS
(status=incomplete)` at WARNING, every other non-completed status at INFO. Seeing
that warning in normal conversation means the rail is too tight, not that the
robot is verbose. A malformed value warns and falls back, an out-of-range one
warns and clamps: **clamping silently was the review's one Important finding** —
`-5` is a one-token, effectively mute robot, and "every knob degrades with a
warning" is this file's rule. Brevity itself is prompt work. The hardening block
gains a 回答長度 section teaching **calibration**, not a cap — 長度跟著內容走: a
one-line answer where one line suffices, a real explanation or story where the
topic deserves it — after the operator ruled (2026-08-31) that a flat
one-to-two-sentence rule is "over strict". What it cuts is filler: repeating the
speaker, restating itself, unasked background, reading tool data verbatim, more
than one clarifying question at a time, and 「讓我想想」-style preambles, which
gpt-realtime-2.x generates by default. `persona.md` gains one matching
no-preamble line and keeps its own calibration lines untouched.

**(8) The confirm-vs-silence startup warning now only fires where it can bite.**
`warn_if_barge_confirm_races_vad()` used to compare the two values
unconditionally. It is suppressed with the name gate on (the confirm timer
commits nothing there, so there is no dead branch to warn about) and under
`REALTIME_VAD_TYPE=semantic_vad`, where the server ignores
`REALTIME_VAD_SILENCE_DURATION_MS` entirely — the comparison was against a value
with no effect, and that known edge in `progress.md` is retired by this wave.

**Rejected alternatives.** Solo `create_response=false` plus an explicit client
`response.create` (party mode's shape): it would put every solo *answer* behind
our gate, so a missed name would cost the user a reply rather than an
interruption — the inverse of decision 1's scope. An acoustic wake word: the real
primitive, but it means an on-device spotter (LiveKit ships `livekit-wakeword`),
and short names like "Reachy" degrade spotter accuracy — out of POC scope, while
transcript gating fits the machine we already run (research §4).

**Verified against the unit suites only** — robot **1571 passed / 30 skipped**,
`ruff check .` and `mypy --strict src` clean. The five live rows
`VOICE-NAME-GATE`, `VOICE-LATE-INTERRUPT`, `VOICE-TRUNCATE`, `VOICE-PATIENCE`
and `VOICE-BREVITY` in `feature_list.json` are the gate, and all of them ride the
**seventeenth install**; `persona.md` changed, so it additionally needs the
operator's scp + sha256 re-sync (D-016) to reach the robot at all. Residual risks
if they cannot be exercised on device: live acceptance of `reasoning.effort` and
of the `status_details` shape; on-robot acceptance of our `audio_end_ms`
(community reports of partial trims — research §2); semantic-VAD behavior on
Mandarin; and `gpt-live-transcribe` partial latency.

## D-029 — Conversation modes, a client-driven answer gate, and the tool diet (2026-08-31)

Operator ask of 2026-08-31, in three parts: Reachy should have named
conversation *modes* it can be switched between by voice (one-on-one, group,
and a quiet meeting scribe); it should stop answering things nobody said to it;
and it should pick the right tool, after 「轉到右邊去看看有誰」 kept selecting
`camera` instead of moving the head. Two on-robot defects rode along: a goodbye
cut off mid-sentence by the sleep pose, and 2.x preambles playing out loud.
Research: `docs/research-mini-tool-calling-2026-08.md`; the plumbing survey the
plan was built on: `docs/survey-conversation-modes-plumbing.md`. Plan and its
three-round Codex log (45 findings — 3 Critical / 27 Important / 15 Minor, **45
accepted, 0 rejected**):
`docs/plans/2026-08-31-conversation-modes-plan.md` §Review log. Nine design
decisions, five open questions, one post-review operator amendment.

**(1) A three-valued enum replaces the boolean, and `_party_mode` survives on
purpose.** `ConversationMode` (`conversation_mode.py`) is `ONE_ON_ONE` /
`GROUP` / `RECORD`, and `set_party_mode` becomes `set_conversation_mode`. But
`_party_mode` is *kept*, as a read-only property returning
`self._current_mode() is not ConversationMode.ONE_ON_ONE`. A dozen call sites
branch on it and every one asks the same binary question — debounced room
barge-in and a gate at `transcription.completed`, or the solo pause-then-decide
machine? RECORD wants the room answer at all of them, so rewriting each site to
a three-way branch would have been twelve chances to get one wrong for no
behavior change. Sites whose behavior genuinely differs per mode read
`_conversation_mode` instead. The property's `getattr` default is deliberately
`ONE_ON_ONE`, not `DEFAULT_MODE`: the contract it preserves is
`getattr(self, "_party_mode", False)`, i.e. a handler with no mode state at all
(tests construct via `__new__`) emits the solo config exactly as it did before
this wave.

**(2) Interruption and answering are two gates, not one.** D-028's
`REALTIME_SOLO_NAME_GATE` decides what may take the floor *away* from a reply
in progress. This wave adds `REALTIME_ONE_ON_ONE_ANSWER_GATE` (`open` default,
`name_only` fallback), which decides what gets a reply *at all* in
一對一聊天模式. They are separate concerns and are now separate knobs; the deny
line is `one-on-one gate: no answer for a non-substantive turn`, alongside
GROUP's `party gate: denied ambient turn` and RECORD's `record gate:
transcribed without answering`.

**(3) `create_response=false` in every mode, so the client owns every answer.**
Party mode already did this; solo did not, and the difference was the pile-up:
the server auto-answered a turn the client had *just* rolled back as an
unaddressed barge, so talking over Reachy queued a second full answer behind
the resumed reply. With the flag off everywhere, a turn is answered only by
`_safe_response_create()` after its mode's gate accepted it. The cost is one
queue hop of latency per turn versus server auto-answer — party mode's week of
use says that is acceptable, and it is measured on-robot by
`VOICE-NO-DOUBLE-ANSWER`.

**(4) The answer gate got its OWN environment variable, and that is the whole
point (Open question 1).** Overloading `REALTIME_SOLO_NAME_GATE` to mean "and
also only answer when named" was the obvious economy and is exactly wrong here:
the instance `.env` on the robot sets that variable explicitly, and the deploy
ritual restores `.env` from backup on *every* install. An overloaded knob would
therefore have re-flipped 一對一聊天模式 to name-only answering on every deploy
— silently, forever, with no line in any diff to explain it. A new variable
that nobody's `.env` mentions defaults cleanly and costs one row in a table.
**Consequence for the deploy: no `.env` surgery is owed by this wave.**

**(5) Mode survives a reconnect; the boot posture is `GROUP` (Open question 2,
plus the operator amendment).** A dropped websocket mid-meeting must not
silently end 紀錄模式, so `_conversation_mode` is deliberately *not* reset by
`_party_reset_for_new_session()` — only the per-turn state is (follow-up
window, open-speech flags, barge timers), for the research doc's SAS carry-over
hazard. What a *settings or backend restart* does is different, and is covered
in decision 6. **The boot default is 多人聊天模式**, by explicit operator
instruction applied after the review closed: the robot sits in a room with
several people in it, and one that wakes up ready to answer every overheard
sentence is the precise failure party mode was built to fix. It is now
`REALTIME_DEFAULT_MODE` (`_boot_conversation_mode()`, values through
`parse_mode`, default `group`); `record` is accepted but logs a warning,
because a robot that boots silent looks exactly like a robot that failed to
start. `REALTIME_PARTY_DEFAULT` is **deprecated-superseded**: it is no longer
read at all, and setting it while `REALTIME_DEFAULT_MODE` is unset logs a
warning naming the mode actually booted. Its old truthy meaning is now the
default, so `=1` lands where it asked; `=0` is the case that stings, and the
warning names `REALTIME_DEFAULT_MODE=one_on_one` as its replacement. The
amendment changed a default, not a mechanism — no gate, session-update,
toolbox, sleep or record-log behavior moved with it — so it did not reopen the
review.

**Amendment 2026-09-04 — the boot posture is 一對一聊天模式.** Operator
instruction after three days of live use: every session opened with the same
spoken switch out of 多人聊天模式, and the 09-04 session's own transcript
shows one person addressing the robot directly throughout. `DEFAULT_MODE` is
now `ONE_ON_ONE`; `REALTIME_DEFAULT_MODE=group` restores the room posture,
and the dead-knob warning now names that value as the replacement for
`REALTIME_PARTY_DEFAULT=1` (the case that stings flipped with the default).
The `set_conversation_mode` description and its `mode` enum text moved "the
mode Reachy starts in / 開機預設" to `one_on_one` (instructing rung 1 — the
description is where the model reads the boot posture). Mechanism untouched
again; the robot's instance `.env` carries the value explicitly so the
still-installed v1.22.0 wheel boots the same way before the next install.
The reasons for the 08-31 GROUP default stand for a room; they no longer
match how the robot is used. Related, not changed here: the interruption
gate (`REALTIME_SOLO_NAME_GATE`) still applies in solo mode — see
`docs/rca-solo-interrupt-2026-09-04.md`.

**(6) `record_log` at 2000 and `session_transcript` at 40 are different data,
not two sizes of the same data.** The D-027 transcript is forty accepted turns
feeding a per-person 「上次聊天」 callback. A meeting record is the opposite on
both axes: it wants every line anyone said — *especially* the ones the answer
gate declined — and forty lines is a few minutes. So 紀錄模式 gets its own
deque, in memory only, never written to disk and never exported (PRD non-goal:
long-term memory). It is cleared when the mode is left and again at the sleep
that ends the visit — and, per P1-5, **not** on a settings/backend restart,
which D-027 already treats as mid-visit; throwing away a meeting that was still
happening would be worse than keeping it. **The accepted cost, recorded:** such
a restart drops the mode back to the boot default, so recording *silently
stops* while the log survives, and a second 紀錄模式 appends to the abandoned
log with an unmarked gap between the stretches. The summarizer prompt is told
the truth about this rather than guessing at seams from timestamps. Mode
persistence across restarts is the obvious follow-up and is deliberately not in
this wave.
**RECORD presence heartbeat (Task 4 ruling).** A *user*-role room-log line beats
D-027's `touch_presence`, because in 紀錄模式 the answer gate declines the
meeting's speech before `record_transcript` is ever reached — a room that talked
for an hour would otherwise look to `write_sleep_summaries` like a room whose
people were last seen at the boot greeting. An assistant line does not: the
robot's own voice is no evidence that anybody is still in front of it. And
`session_transcript` is deliberately *not* fed from the record log — the sleep
summary's input stays "turns the robot actually took part in".

**(7) RECORD's tool allowlist is six local names, and the `[OFFICIAL]`
under-20-tools rule is why it is a list at all.** `set_conversation_mode`,
`summarize_conversation`, `go_to_sleep`, `wait_for_user` — the four the model
uses — plus `task_status` and `task_cancel`, which are `SystemTool` values the
background tool manager injects into every profile and which the model needs to
follow up a long-running call (Open question 4: hiding them would break the
tools that *are* allowed). Everything else is excluded through the registry's
existing `exclusion_list` seam, so nothing in the tool pipeline had to learn
about modes. **`EXTRA_TOOLS` are never hidden in ANY mode, RECORD included**
(P2-8): an MCP tool belongs to no toolbox, so no `open_toolbox` category could
bring it back, and hiding it would strand it for the whole meeting. Every count
in this record is therefore "plus any `EXTRA_TOOLS`".

**(8) `look_around` is one composite tool, and it reports
`direction_requested`, not `direction_moved` (P2-2).** The selection failure was
the right tool present but not ranked first — `move_head` losing to `camera` on
「轉到右邊去看看有誰」 — so the fix is a single tool that does both, in order,
rather than a prompt teaching the model to chain two. It calls
`clear_move_queue()` first so its move is not stuck behind an older one. The
field is named for what the motion API can attest and nothing more: `MoveHead`
returns on *queueing*, `MovementManager` publishes no accepted/completed
signal, and `set_hold_still` can drop a queued move silently — so the tool can
honestly say where the head was *sent*, never that it arrived, and the
description tells the model to describe the returned picture rather than assert
completed motion. Three outcomes are spelled out separately: move failure (no
direction field), capture failure (direction + error, no image), success.
「看一下你後面」 was removed from both `look_around`'s and `camera`'s
descriptions (P2-4) — there is no `behind` in the schema and body rotation is
out of scope — and a test asserts neither 「後面」 nor "behind" survives. The
image-attachment path was generalized in the same pass (P2-1, Critical): the
handler keyed the attach site on `tool_name == "camera"`, so `look_around`'s
picture would never have reached the model and its base64 would have been dumped
into the tool JSON. Both the sanitizer and the attach condition now key on
`"b64_im" in tool_result`, for any tool.

**(9) Going to sleep is silence → wait → drain → pose, and silencing comes
first (P2-10 Critical, then round 2's 2a-6).** `go_to_sleep` runs from the tool
worker *before* `response.done`, so `is_audible()` could be false while the
goodbye was still being generated and the robot lay down mid-sentence. The fix
is a bounded `wait_for_reply_finished()` seam on `_response_done_event`. The
*order* then mattered more than the wait: waiting first left the microphone live
for up to ten seconds, which is how a repeated 「睡覺吧」 or the goodbye's own
echo opened a turn nobody would answer. So `quiesce_for_sleep` is split —
`begin_sleep_quiesce` (mute the mic, disarm the barge machine) runs first, then
the bounded wait, then `wait_for_speaker_quiet`, then the pose. Journal order,
which is the verification contract:

```
Tool call: go_to_sleep
sleep quiesce: microphone muted
sleep quiesce: barge machine disarmed
    (the bounded wait for the reply to finish — silent unless it overruns, and
     then `go_to_sleep: the goodbye response did not finish in time`)
Going to sleep before stopping conversation app.
sleep quiesce: speaker quiet after N.Ns
    | sleep quiesce: drain cap reached after N.Ns with audio still playing
    (the pose, only after that)
```

**It never flushes.** The quiesce silences *inputs* and waits for the speaker;
nothing on this path drops queued audio, because the whole purpose is to let the
goodbye finish. `SLEEP_GOODBYE_DRAIN_CAP_S` (default 6.0) exists only so a stuck
drain estimate cannot hold the robot awake, and the cap branch says so out loud
rather than logging "speaker quiet" over audio that is still playing (P2-11).
**The seam is loop-aware** (2a-5): the inactivity path runs `GoToSleep` via
`asyncio.run` on a daemon thread, so `wait_for_reply_finished` captures
`_handler_loop` at session start, awaits directly when already on it, marshals
via `asyncio.run_coroutine_threadsafe` with the same bounded timeout otherwise,
and reports success when the loop is gone.
**Two defects found during implementation, recorded because neither was in the
plan.** First, the plan had `begin_sleep()` set `go_to_sleep_requested` — that
event is the pose closure's own duplicate latch, so claiming it in the quiesce
would have made the very sleep it was preparing return `already_requested`, for
every voice sleep. `begin_sleep` therefore sets only `deps.sleep_requested`
(D-027's flag, idempotent) and runs the quiesce. Second, nulling `_handler_loop`
in the receive loop's `finally` strands the *live* session: a replacement session
captures the loop near the top of `_run_realtime_session`, before it publishes
its connection, so a dying session's `finally` landing in that window nulls the
loop the live session just captured — after which `wait_for_reply_finished`
short-circuits `True` for the rest of that session and the goodbye is cut off
again. The field is now nulled only in `__init__` and `shutdown()`; the session
`finally` still *sets* `_response_done_event`, conn-guarded, so a session that
dies mid-response does not leave the sleep path burning its whole timeout
(round 3, finding 2).

**Design decision 9 — one ordered, acknowledged, single-flight session update.**
Four review findings (P1-1, P1-3, P1-4, P2-9) were ruled one defect family and
fixed once. `_apply_session_update(build_session, what=…)` takes a **builder**,
not a payload, and holds `_session_update_lock` across ticket check, payload
build, waiter install, send and acknowledgement wait as one uninterrupted region
— taking the lock twice (2a-1, Critical) let a newer flip overtake an older
payload between them and made the monotonic ticket worthless. Every live-session
caller goes through it — `_push_mode_update`, `_push_turn_detection_update`,
`change_voice`, `apply_personality` — because `session.updated` carries no
client `event_id` and an uncorrelated ack would otherwise resolve somebody
else's waiter. The one exemption is the initial `session.update` in
`_run_realtime_session`, which runs before the receive loop exists. A client
`event_id` *is* stamped, because an `error` names the event it rejected, and
that is how a rejection is told apart from an unrelated server error — resolving
the update's waiter and `continue`ing, never touching the response-create path.
`set_conversation_mode` and `open_toolbox` **await** it: the model's confirmation
sentence, and any tool call right after it, must run under the mode being
confirmed. Every update logs `Tools in session (<mode>): [...]`, because the
startup-only `Tools to be used in conversation:` line cannot show a mid-visit
flip (P2-12).

**The unmatched-acknowledgement debt counter is the genuinely non-obvious
part.** `session.updated` carries no correlation id, so *every* acknowledgement
the server already owes — the connect config, its one-shot retry, pushes sent
before the receive loop, and waits that timed out — must be paid off before a
live waiter may be resolved. Without it (round 3, findings 5 and 6, both
Critical) a mode flip would be told its payload was applied on the strength of
the connect config's ack, and a late ack after a timeout would resolve the
*next* update's waiter. `_note_session_updated` pays `_session_update_ack_debt`
first and only then touches a waiter; a timeout books one unit rather than
restarting the session, which would be disproportionate on every slow ack. The
no-wait path books debt too and logs which kind it is: `session updated (<what>,
sent before the receive loop)` or `(…, not waiting for the acknowledgement)`;
the acknowledged line is `session updated (conversation mode <value>)`. Debt and
`_receive_loop_active` reset per session, conn-guarded, so a dead websocket's
teardown cannot zero a live session's debt.
**Three accepted residuals on this mechanism, none of which can wedge the
handler.** (a) *Debt ratchet:* the design assumes exactly one `session.updated`
per accepted `session.update`. A server that emits more than one, or none for a
no-op, drifts the counter and makes a later flip resolve early or time out.
(b) *One-tick timeout double-count:* an acknowledgement arriving in the same
tick the wait expires can book a phantom unit of debt, paid down by a later ack.
(c) *Dead-session late finally:* an update in flight when the session dies is
reported failed even though the server may have applied it. All three degrade to
"applied locally only" — the client-side gates, the barge policy and the record
log are all still enforced — never to a stuck session. The live tell is the
journal's `never acknowledged within 5.0s` warning on a healthy connection; if
it appears, the assumption is wrong for this endpoint, and the fallback is to
send without the wait rather than leave every flip failing. `change_voice` is
pessimistic for the same reason: an ack timeout reports failure even if the
update landed.

**Commentary-phase suppression (Task 10).** gpt-realtime-2.x tags output items
`commentary` or `final_answer` and there is no documented switch to turn
preambles off, so the client refuses to play them: an item whose `phase` is
`commentary` has its id remembered (`suppressing commentary-phase item …`) and
its transcript and audio are dropped (`dropping commentary-phase … for item …`),
while the item still closes its turn normally. Ids are cleared per session so an
abandoned turn's id cannot suppress a later one. **The on-robot check is whether
the field arrives at all**: the whole seam rests on `phase` being present on
`response.output_item.added`. If the server carries it only on
`response.output_item.done` — after the audio has already streamed — the seam
fires too late and the preamble still plays. Absence of those DEBUG lines across
a whole visit *with* preambles still audible means "the field is elsewhere": a
follow-up, not a fix. This is client-side only — the model still spends the
tokens and the latency; the prompt-side half of the problem is D-028's brevity
work.

**Two verbatim envelope shapes, and only two (Open question 3).** Tools that
produce text the model must read out *exactly* return an envelope rather than
trusting the prose: `who_is_this` sets `response_text` +
`require_repeat_verbatim`, and `summarize_conversation` sets `summary_text` +
`speak_verbatim`. The hardening block teaches one rule covering both. The reason
is the D-025 failure already recorded in `progress.md` — the tool data was right
and the spoken sentence was not — and `[COMMUNITY]` reports (research §D2)
describe verbatim-name fidelity as a model-level regression on non-English
2.1-mini. The envelope improves the odds; it cannot guarantee them.

### The tool diet: 41 tools → 22 at the start of a turn

OpenAI's own function-calling guide asks for "fewer than 20 functions available
at the start of a turn", the realtime prompting docs say a focused list
"prevents the model from misselecting tools", and the measured effect is largest
in exactly our case — the right tool present but not ranked first (research
§A1). Three mechanisms, cheapest first.

**Decision 7 — façade consolidation: 18 CRUD tools become 6 action-enum
families.** `calendar`, `tasks`, `drive`, `nas`, `music`, `tv`, all through
`tools/tool_family.py`'s `dispatch_family`. **The 18 modules stay on disk**,
with their prerequisite rows and their tests, and each façade delegates to the
untouched original: merging the bodies would mean rewriting every confirmation
gate and all the Google/NAS/HA error handling for zero model-facing gain. The
façade **adds no argument validation of its own** (P2-5) — it validates the
action *name* and nothing else — because a `REQUIRED` table at the façade would
reorder and reword each delegate's own checks, which deliberately run *after*
`settings.tool_status`; two tests pin the delegate's own error string and the
prereq-before-args order. Every spoken confirmation gate before a delete, a
trash or an upload is unchanged.
**A recorded cost:** the six family descriptions are 453–637 characters each,
3408 in total, where the eighteen one-line originals came to 832. The tool
*count* is what the selection research measures, and each entry also carries a
JSON schema, so the trade is still worth taking — but "consolidation shrinks the
prompt" would be false, and is not claimed anywhere.

**Three deletions.** `sweep_look`, `self_destruct` and `mad_laugh` are gone,
along with `HANOVA_SELF_DESTRUCT_YT_ID` / `HANOVA_MAD_LAUGH_YT_ID`. A
`_RETIRED_TOOL_NAMES` tripwire asserts no retired name survives in *any* bundled
`profiles/*/profile.md` or in `hardening_block()` (P2-6, and round 2 finding 14
— `profiles/default` shipped `sweep_look` too).

**Decision 8 — `open_toolbox`, and boxes ACCUMULATE.** Two families load on
demand: `productivity` (calendar, tasks, drive, email_send, notion_add) and
`media` (tv, nas). Sizes, plus any `EXTRA_TOOLS`: **22** at rest, **27** with
productivity open, **24** with media, **29** with both — all four asserted by
`test_the_documented_surface_sizes_hold`. Boxes accumulate within a mode and
close together at its edges — a mode switch, sleep, or a new session — because
closing a box the model has *already been told about*, mid-turn, is how you get
a call to a tool that is no longer there (round 2, 2b-3). For the same reason
there are **no idle timers**: carrying one extra family for the rest of a visit
is cheaper than that failure. The open is **awaited, not scheduled**: the model
reads the result and continues to the real call in the same turn, so the update
must be acknowledged before the result comes back. It is optimistic then rolled
back (P2-9) — the box goes into `_open_toolboxes` first, because
`_push_mode_update` builds its payload from that live set, and comes straight
out with `status: "update_failed"` if the server refused. The membership is
re-checked *after* the await (round 3, finding 3) for the second failure case: a
`set_conversation_mode` landing mid-flight calls `close_toolboxes`, so the box is
gone even though the push itself succeeded, and reporting "loaded" there would
advertise tools the session no longer has. `set_conversation_mode` re-reads the
mode after its own await for the mirror-image reason (round 3, finding 4) and a
losing flip returns `status: "superseded"` with the mode the handler is
*actually* in, plus `requested` for the journal.

**The honest count is 22, and `music` is in it (Open question 5, P2-7).** The
plan said 21 until the review pointed out that boxing `music` would hide
`stop_music` — documented as the prerequisite-free safety lane, the one tool
that must answer when nothing else can. Behind a toolbox, 「音樂關掉」 would
first have to load the tools for turning the music off. `music` moved into the
static core, the media box became `tv` + `nas`, and a negative control
(「放首歌」 then 「音樂關掉」 with no `open_toolbox` between them) joined
`TOOLBOX-DYNAMIC`. The membership rule for the core: anything the robot might
need in the *first second* of a turn with no chance to load something first —
its senses, its body, who it is talking to, the lights, the web, and the
conversation's own controls.

**Rejected alternatives.** Merging the 18 sub-tool bodies into 6 (rewrites the
confirmation gates and the Google/NAS/HA error handling for zero model-facing
gain). `allowed_tools` instead of swapping `tools` (an equivalent surface, but
the app already owns a proven `session.update` seam and this would be a second
mechanism). Idle-timer toolbox expiry, and swap-instead-of-accumulate (both
close a box the model has already been told about, mid-turn — round 2, 2b-3).
Forcing the look chain with `tool_choice: {"type":"function","name":"camera"}`
(research §B3 — `[COMMUNITY]` bug reports on `tool_choice` in Realtime; the
composite needs no such gamble). Numeric length caps (operator ruling, carried
over from D-028). Raising `reasoning.effort` to medium (unmeasured latency cost;
revisit only if the composite does not fix selection). Restarting the session on
a slow ack instead of booking one unit of debt (disproportionate on every slow
ack). A per-item mode map beyond the one stamped field — the orchestrator scoped
round 1's finding 2 to a single `_turn_mode`, and round 2's 2a-4 then forced
exactly as much more as the defect required: an item-keyed `_turn_modes` dict
stamped at `speech_started` and popped at the completed/failed branches, no more.

**Accepted residuals beyond those already named.** A turn whose transcript
arrives empty or fails gets no response at all, in every mode — the GROUP
precedent, unchanged by this wave, with `_barge_response_watchdog` as the repair
for the case where a barge was confirmed first. Under the non-default
`REALTIME_SOLO_NAME_GATE=0` + `REALTIME_ONE_ON_ONE_ANSWER_GATE=name_only`
combination an already-fired watchdog can meet a late denied transcript; the
combination is documented rather than special-cased.

**Verified against the unit suites only** — **1746 passed / 30 skipped**,
`ruff check .` and `mypy --strict src` clean. Ten live rows in
`feature_list.json` are the acceptance gate — `MODE-BOOT-DEFAULT`,
`MODE-ONE-ON-ONE`, `MODE-GROUP-SWITCH`, `MODE-RECORD`, `VOICE-LOOK-AROUND`,
`VOICE-SLEEP-QUIESCE`, `VOICE-NO-DOUBLE-ANSWER`, `VOICE-COMMENTARY-SUPPRESS`,
`TOOLS-CONSOLIDATED`, `TOOLBOX-DYNAMIC` — and all of them ride the next install.
**`persona.md` MUST be re-synced in that same pass, and this is a hard
pre-deploy gate, not a nicety.** The instance copy still names all eighteen
retired CRUD sub-tools, `party_mode`, and the `### self_destruct` ritual
section; it reaches the robot only by the operator's scp + sha256 ritual
(D-016), never by the wheel, so an install without it puts a prompt describing a
tool belt that no longer exists in front of the model. The re-sync must edit
`tests/test_hanova_integration.py`'s `PERSONA_TOOL_TOKENS` in the same change —
it currently pins `("play_music", "nas_skip")`, i.e. the *stale* state, exactly
so this file can say which copy is which.

## D-030 — LLM-first instructing with boundary code only (2026-09-01)

This release closes the two 2026-09-01 field bugs — voice sleep produced no
goodbye, and 「看右邊」 queued a head move that face tracking immediately erased
— under the house rule from the instructing research: the model chooses the
words and the tools; the app writes the tool surface, context, validation and
execution-boundary rails.

**Escalation ladder used in this wave.** Rung 1, *fix the tool*, covers runtime
argument validation, corrective model-readable errors, honest physical-action
returns, the `go_to_sleep` return cue, and the report-only rename check. Schema
enums are not enough on this Realtime surface, so every robot-action boundary
rejects bad values instead of coercing them. Rung 2, *fix the context*, covers
the prompt restructure: the profile/persona carry character, `prompts.py`
carries the 2.x policy blocks and the single `## Tool Availability` authority,
memory is appended as labeled background with an explicit "current user wins"
priority, and Taiwan Traditional Chinese plus content-calibrated length replace
the old contradictory language and sentence-count rules. Rung 3, *code only at
the execution boundary*, is used only for timing and physical-state truth: the
farewell response cycle and the manual head-tracking suspension.

**Voice sleep is an instructed generation turn.** `go_to_sleep` no longer poses
the robot. It mutes inputs and returns facts plus `farewell_context`; the tool
description, not the return field names, tells the model how to read that cue.
The app then queues exactly one follow-up response through the existing
serialized sender with `response={"tool_choice": "none"}`. The model composes
the goodbye; the app owns when that generation happens, prevents another tool
call from riding along, waits for that specific response id to finish, drains
speaker audio, and only then calls the sleep finalizer. The Task 1 sender fix is
part of the decision: request-scoped start and done correlation replaced the
old generic `_response_started_or_rejected_event`, because unrelated realtime
errors could otherwise release the sleep wait early.

**Lifecycle sleeps still pose directly.** The inactivity timeout has no live
model turn and no person waiting for a goodbye. It therefore keeps the direct
quiesce/pose/stop closure through `run_lifecycle_sleep`: silence inputs
best-effort, then call `deps.go_to_sleep` even if silencing failed. Routing it
through the tool would strand the robot awake now that the tool deliberately
does not pose. (A plain external app stop — dashboard or API — was never a
sleep path and remains untouched: it shuts the app down without posing, as
before this wave; final-review clarification 2026-09-01.)

**Manual head windows are suspension, not speech anchoring.** The field bug was
the daemon face tracker overwriting a correct queued goto. `MovementManager`
therefore gained owner-gated `suspend_head_tracking` /
`restore_head_tracking`, and `look_around` / `move_head` use a bounded window
around the move, hold and capture. This deliberately avoids the existing
`set_speaking` anchor: `set_speaking(True)` captures `_track_anchor`, and
`_get_primary_pose` restores that anchor over a finished move, which would undo
the very look the window exists to make visible. `set_hold_still` is avoided
too because it clears the move queue. The window restores the prior tracking
state exactly; off stays off.

**Return facts stay honest.** `direction_requested` stays until motion is
verifiable. `MoveHead` returns when a command is queued, and the movement
manager exposes no completed-pose signal; publishing `direction_moved` without
that evidence would create a better-looking lie. Other physical-action returns
follow the same rule: requested, queued or stopped-as-requested facts only.

**Preambles and renames.** Commentary-phase suppression stays in place, and the
spoken-preamble goal is dropped for this wave; the prompt teaches where tool
talk belongs, while a selective allow-commentary policy is deferred to a
separately tested wave. Renames are alias A/Bs, never edits. The
`finish_session` alias subclasses `GoToSleep`, inherits `ends_session`, and is
registered through `EXTRA_TOOLS` only when `INSTRUCTING_FINISH_SESSION_ALIAS` is
truthy; with the default off, profile lists, toolboxes, the record allowlist and
surface-count tests continue to advertise `go_to_sleep`.

**Verification status.** Layer 1 is SDK-simulated and covered by the merged
tests: the farewell output reaches the model, the follow-up request carries
`tool_choice: none`, pose waits for the farewell's own completion plus drain,
head tracking suspension restores prior state, bad arguments produce
corrective errors, and lifecycle sleeps still pose directly. Two controller
fix loops are part of the accepted state: `1f831c2` repaired the Task 3
test-pollution failure by patching the dispatcher's imported `core_tools`
module, and `b652d05` reconciled the commentary-only test with the
request-scoped response waiter while removing stale sleep-wrapper wording. No
on-robot proof has run in this task, so the two field-bug rows in
`feature_list.json` remain blocked on the deploy-time journal probes. Residuals
to watch there: `MOVE_HEAD_HOLD_S=1.5` is a feel-based guess, the gesture window
adds latency to `move_head`, and `reasoning.effort` intentionally stays
untouched unless a later three-metric A/B or one-shot full-`gpt-realtime-2.1`
diagnostic justifies a change.

## D-031 — Field-test fix wave: answer hold-off, spoken lead-ins, clean sleep stop (2026-09-02)

The v1.20.0 field test (2026-09-01, operator session 12:49–12:57 robot time)
produced six RCA findings; the operator ordered three for this wave —
turn-detection over-commit first ("the main issue"), spoken preambles for slow
tools, and the end-of-sleep failure that unmuted the microphone. Plan:
`docs/plans/2026-09-01-field-test-fixes-plan.md` (rev 3; two Codex review
rounds, 14 findings, 13 accepted + 1 in part, 0 rejected — the first plan
reviewed under the 2-round cap that replaced the 3-round cap in `CLAUDE.md`
on 2026-09-02). Execution model, operator directive: Claude planned, reviewed
diffs and integrated; Codex (`--profile nova-auto`) implemented and ran the
suites. Reuse-first note: no new SDK call was introduced anywhere in the wave —
every change wraps our own seams (response issuance, commentary bookkeeping,
the daemon stop-endpoint glue), so the missing `reference/` clone on the dev
machine did not gate it.

**Decision 1 — the answer hold-off lives at the accepted-turn seam, in the
client (rung 3, timing).** `semantic_vad` exposes only `eagerness`, and
eagerness bounds the *maximum* wait while the classifier is unsure; a fragment
with terminal prosody commits at once at any setting. Server tuning is
exhausted (`docs/codex-research-turn-detection-2026-09.md`). Because
`create_response=false` since D-029, the client already issues every
`response.create`, so the fix is a bounded wait — `REALTIME_COMMIT_HOLDOFF_MS`,
default 700, 0 restores the old path — between a turn being ACCEPTED and its
request. Codex round 1 moved it from a commit-event seam that does not exist
in this handler to the accepted-turn path, which keeps the GROUP/RECORD/name
gates in front of it. The "already speaking" test is event order, not timing
and not transcript content: a monotonic `speech_started` sequence, stamped per
input item, compared at acceptance. Rules that round 2 and the implementation
review added, all test-pinned: the window is a per-turn task (an inline sleep
would stall the single receive loop and make the skip unobservable); the task
is bound to its connection and re-checks sequence, connection identity and
`_receive_loop_active` at fire because `_pending_responses` outlives sessions
while the sender worker does not; `on_external_interrupt()` and
`_barge_shutdown()` cancel it beside the barge timers; a newer accepted turn
supersedes an older window; and — the gap the plan itself missed — a skip
*owes* the held turn an answer when its continuation produces no turn (empty
or failed transcript, gate denial, rollback), so a cough inside the window
cannot eat a real question. Rejected: transcript-length gating (content
heuristics on a fragment are exactly the guessing the prompt forbids), a
short-turn qualifier (needs committed-audio duration the handler does not
track — YAGNI until the flat window annoys), and any change to VAD type or
eagerness (the on-robot A/B row stays the fallback).

**Decision 2 — preambles are spoken again; selectivity is instructed, not
coded (rungs 1–2 with one minimal seam change).** Dead air on search turns was
by design: every commentary-phase item was dropped (D-029 Task 10, kept in
D-030). The survey (`docs/codex-investigation-commentary-2026-09.md`) showed the
tool name is unknowable at the drop point, so code cannot enforce "slow work
only" without buffering. Under the escalation ladder the wave removes only the
*audio* drop — preamble frames now take the normal speaker path with full
drain, truncate and item bookkeeping — while commentary *transcripts* stay out
of the output queue, operator transcript, RECORD room log and the D-027
sleep-summary tail (Codex round 1: a preamble is not the answer those should
keep). When a lead-in belongs is taught where the ladder says it works: the
prompt's 訊息頻道/開場白 block (calibration principle — enough to show the work
has started, never a narration of the steps; Codex round 2 struck the "one
short sentence" cap), 示範語氣 preamble phrases in the slow tools' descriptions
(bundled search, `music` play only, and one appended sentence on every remote
MCP tool at the wrap point), and the persona/profile point-first line reworded
to apply to the answer. Two riders rode the same description edits: the RCA-4
routing contrast (media playback → `music`, not search; `music` owns YouTube
playback), and the A3 placement move — the 聽不清楚時 rule now follows the mode
block so it is the last system-layer text the model reads. Known risk, stated
up front: fast-tool narration may return; the reviewed fallback is buffering
commentary until `function_call_arguments.done` names the tool, as its own
task — not a reason to hard-code speech now. Deploy trap (Codex round 1): a
robot with an `installed_tool_spaces.json` manifest serves cached descriptions,
so bundled specs now override the cache at read time for bundled slugs.

**Decision 3 — sleep teardown treats the daemon's early hang-up as success
(rung 3, execution-boundary correctness).** The daemon accepts
`POST /api/apps/stop-current-app` and tears the app down before finishing
the HTTP response, so `response.read()` raises `http.client.RemoteDisconnected`
— a `ConnectionResetError`/`OSError`, not a `URLError` — past the old guard
and into the C6 unmute recovery, which reopened the microphone about seven
seconds before the process died (`docs/codex-investigation-sleep-2026-09.md`;
journal 2026-09-01 12:57:15 `go_to_sleep failed before the stop; microphone
unmuted`). The guard now catches `URLError`, `HTTPException` and `OSError`,
logs the exception type, and lets the local stop proceed as today. The C6
recovery boundary shrank to the genuinely pre-pose steps (quiesce, speaker
wait, wobbling, movement stop, pose); the stop request and local shutdown sit
outside it, because by then quiesce muted the microphone on purpose and
reopening it contradicts sleep. `movement_manager.stop()` is log-and-continue
in both the sleep closure and the final shutdown path (Codex round 1, finding
11 — a raise there aborted wobbling-disable, media-close and disconnect). The
D-027 sleep summary retries one `TimeoutError` with a fixed 4 s budget so the
added shutdown delay is bounded regardless of `MEMORY_LAST_CHAT_TIMEOUT_S`
(round 1, finding 10 — "2× env" could have held the still-open realtime
connection for a minute). Rejected: persisting the transcript locally before
summarising (the non-surgical follow-up, recorded if loss recurs).

**Verification status.** Layer 1 is SDK-simulated and pinned: the suite
went 1819 → 1859 passed / 30 skipped across seven commits, ruff and
`mypy --strict` clean at each. Codex's sandboxed runs reported three
environmental failures every time (two MCP tests cannot bind 127.0.0.1, one
wheel test cannot read the uv cache); none reproduces outside the sandbox and
the session re-ran the full suite on the dev machine before every commit.
On-robot proof is the twenty-first install's three new rows in
`feature_list.json` — `VOICE-TURN-FRAGMENTS`, `VOICE-SLOW-PREAMBLE`,
`SLEEP-CLEAN-STOP` — all `implemented-unverified` until a person is in the
room. Residuals to watch: the 700 ms window is a flat cost per turn (the
short-turn qualifier is the recorded optimisation if it annoys); fast-tool
narration may return under the audible-commentary contract (the B4 buffering
fallback is the reviewed answer, not a revert); RCA-2, the RCA-4 fabrication
half, RCA-5, the `who_is_this` too_far defect and `RPC-SAY-CROSS-LOOP` remain
open and untouched.

**Addendum (2026-09-02, later the same day) — the 700 ms question and the
research audit.** The operator asked whether the hold-off default has a
research basis. It does not: "700" appears in no research doc; it is the
midpoint of an INFERRED "~600–900 ms" band whose vendor citations the source
run never confirmed (`docs/research-holdoff-calibration-2026-09.md` has the
audit and the external evidence: LiveKit endpointing 0.5/3.0 s defaults,
within-speaker pause clusters ~150/500/1500 ms, Mandarin boundary pauses
0.33/0.49 s, and the repo's own <500 ms and ~1100 ms bounds). Ruling: keep
700 as the first value, measure, then set the knob from the operator's own
gaps — v1.22.0 adds calibration telemetry (`gap=`/`held=` on the skip lines and
a one-shot `late continuation` line when speech resumes within 2 s of an
expired window). An Opus audit of all seven research docs against the code
then ranked ten unapplied recommendations. Shipped in v1.22.0 without a
separate plan round, as single-surface rung-1/2 items already recommended by
reviewed research: symmetric use-when / do-NOT-use-when pairs on the eleven
always-on core tools that were still one-liners (measured evidence: +17%
selection accuracy, +60% success on description rewrites), and a `### 變化`
rule so the newly audible lead-ins do not become one recorded sentence.
Deliberately NOT shipped, recorded as next-wave candidates in priority order:
(1) extend the verbatim JSON envelope to every recited result — search, MCP,
`nas_video_query` return prose today (targets the open RCA-4 fabrication half;
M); (2) end-of-context re-injection of the unclear-audio rule as a
`conversation.item`, never `response.create.instructions` (targets RCA-5; M,
needs on-robot); (3) B4 buffered selective commentary, gated on observing
fast-tool narration live; (4) `reasoning.effort` A/B measured on selection,
hallucination and adherence together, plus one diagnostic session on full
`gpt-realtime-2.1` (both need a person in the room); (5) `# Reference
Pronunciations` for the household's proper nouns (needs the operator's list);
(6) out-of-band `conversation: "none"` responses for side tasks (no current
need); (7) a Chinese voice regression set and eval loop (L — the thing that
would settle the 700 ms and the effort questions with data instead of
argument). `parallel_tool_calls` is available on 2.1-mini and unset; no
current failure asks for it.

## D-032 — Solo interruption: any real sentence stops the reply; context preserved by truncate (2026-09-05)

The 2026-09-04 session (v1.22.0, one operator, 11:47–12:10 robot time) rolled
back 19 of 22 speech onsets over a talking robot:
`docs/rca-solo-interrupt-2026-09-04.md`. 「等一下」, 「你就播吧」 and
「嗯嗯嗯。这句话很怪怪的」 all left Reachy talking, and a rolled-back pause put
the fully buffered old reply back on the speaker in front of the new answer.
Operator ruling on RCA candidate fix 1, verbatim from the plan header: *RCA
candidate fix 1 approved — in 一對一聊天模式 any real sentence stops the reply —
with one added requirement: an interruption must preserve the transcript so the
model keeps the previous context, and the conversation must continue from the
latest human speech, never from where the old reply left off.* Plan:
`docs/plans/2026-09-05-solo-interrupt-plan.md` (rev 3). This wave is rung 3 of
`.claude/skills/reachy-instructing-model/SKILL.md` by definition — which sound
stops the speaker is an execution-boundary fact, not a behaviour the model can
be instructed into — so no prompt, persona or tool-description text moved.

**Decision 1 — `REALTIME_SOLO_NAME_GATE` defaults OFF; it stays a knob.**
Supersedes D-028 decision 1's *default* only. A paused solo reply is decided by
the substantive rule again (the D-023 path, shipped all along as the `0`
branch): control phrase, or any substantive transcript, commits; a backchannel,
an empty transcript and a failed transcription still roll back. `=1` restores
D-028's story-telling posture — stop for 「瑞奇…」 or 「停」, talk through speech
aimed at someone else — which remains the right posture for a room. The flip
touches solo only *by construction*, with no new mode plumbing: `_party_mode` is
True for GROUP and RECORD and every solo barge site early-returns under it. Two
consequences follow the default rather than the code: `REALTIME_BARGE_CONFIRM_MS`
(1600) becomes the live commit backstop — sustained speech commits, which is what
stops a long interjection at 1.6 s instead of resuming the reply at the cap — and
`REALTIME_BARGE_MAX_PAUSE_MS` (4000) becomes gate-on-only code, which retires RCA
Finding 2 outright.

**Decision 2 — one verdict decides the pause and the late path.**
`_solo_interrupt_verdict(text)` is now the single rule, read by
`_resolve_solo_barge` and by the `transcription.completed` late-interrupt guard.
They are the same decision taken at two moments — a transcript that beat the 2 s
rollback timer, or one that arrived after it — and they used to disagree: the
late path fired only on a control phrase or (gate on) a name, so with the gate
off a plain sentence whose pause had already rolled back was answered *behind*
the reply the user talked over. That was RCA Finding 3, and the late path is
what makes stopping a guarantee rather than a race.

**Decision 3 — an interruption stops whatever is speaking, and takes its
context with it (the operator's added requirement).** The mapping is
cancel → flush → truncate: `_cancel_active_response` stops the server
generating, `_clear_queue` empties the local player so nothing of the old reply
is heard after that instant, and `conversation.item.truncate` cuts the *server's*
copy at the position that provably reached the ear (`_heard_audio_ms`: enqueued −
outstanding − device buffer − 300 ms slack, always rounded down). The model
therefore keeps the words the user heard, loses the unheard tail — the thing that
made an interrupted Reachy repeat itself before D-023 — and the interrupting
utterance is already a server-side conversation item, so the next reply answers
the latest speech with the heard part still in context. Rollback paths never
truncate: truncation is irreversible, which is why the rule change had to land on
the decision and not on the truncate. Both commit sites now cancel the live
response *whatever its id* (Codex round 1, finding 2): the pre-D-032 rule kept a
newer response on the theory it was this turn's answer, but its audio has already
been flushed, so the user heard a gap and then the rest of a reply they had
interrupted. Two items can lose audio in one barge, so two items are truncated —
the paused one at its stashed position, the live one at a position measured
before the flush zeroes the drain counters. Best-effort, stated plainly (round 1,
finding 6): a truncate is skipped when nothing was heard, and a server refusal is
caught; that refusal now logs at INFO so the rare surviving tail is visible.

**Decision 4 — the bookkeeping that keeps one interruption to one answer.**
Three per-item markers, all keyed by input item because `transcription.completed`
can land after the NEXT utterance's `speech_started` (Codex round 2, findings 1
and 6). (a) A sustained-speech commit precedes the turn's own transcript, so the
repair watchdog can answer an utterance whose transcript is still to come;
`_barge_watchdog_answered_item` records which, and the accepted path skips only
the request — all hold-off bookkeeping still runs — when that marker names this
item AND `response.created` has been seen since the arm, the only proof the
enqueue-only `_safe_response_create` offers (round 2, findings 2 and 4). The same
marker is the one exception to Decision 3: the late path never cancels the reply
the watchdog asked for on behalf of the same utterance. (b) Late eligibility is
stamped per item, with the session flag kept only as the fallback for an event
with no id. (c) A tool call whose `response_id` is in the cancelled set is
dropped before it starts anything (round 2, finding 5) — no in-flight entry, no
tool-batch follow-up, no music tool phase; tools already running when the cancel
lands finish and post their outputs, as they do for every other cancel. Also
under this decision: a name in a *partial* transcript now commits under either
gate — the old "gate-mode only" restriction was a latency-lever scoping, not a
safety property. No substantive-on-partial, because a partial cannot prove
substantiveness (「嗯嗯」 grows into 「嗯嗯好」).

**Decision 5 — the declined branch says why.** RCA Finding 3's open case is that
the 11:51:23 turn carried the robot's name, looked eligible, and the journal had
no line explaining which guard refused it. `late solo interrupt declined
(audible=<bool>, verdict=<reason>)` is now emitted once per turn that began over
a talking robot and did not interrupt it — from the answer-gate denial (where a
one-on-one backchannel exits, round 1 finding 5), from the empty-transcript exit
(`verdict=empty`), and from the late block itself.

**Knobs and the false-interruption risk.** The knobs that govern this wave, all
env, all with shipped defaults: `REALTIME_SOLO_NAME_GATE` (now 0; `1` restores
D-028), `REALTIME_BARGE_CONFIRM_MS` (1600 — raise it if false cuts appear),
`REALTIME_BARGE_ROLLBACK_TIMEOUT_S` (2.0 — raise it to lengthen the silent wait
for a slow transcript), `REALTIME_BARGE_MAX_PAUSE_MS` (4000, gate-on only),
`REALTIME_BARGE_COOLDOWN_MS` (800), `REALTIME_SOLO_CLIENT_BARGE` (1),
`REALTIME_VAD_THRESHOLD` (0.7 on the robot). The risk this trades for is false
interruption from non-speech: under `semantic_vad` the server decides
`speech_stopped` on its own schedule, so a cough that keeps `_barge_speech_open`
True past 1600 ms commits by sustained speech. The cost of a false cut is a
re-ask, never lost context — the truncate keeps the heard part — and the signal
to watch in the journal is `confirmed by sustained speech` with no user
transcript following. Second residual, recorded not fixed: stop latency is
bounded by the transcript, not zero. Speech shorter than the confirm window is
decided by its transcript, and if that arrives after the 2 s rollback timer the
reply audibly resumes and is cut again by the late path — a short
resume-then-cut. The pause at onset still silences the robot immediately.

**Review outcome.** Codex (`--profile nova-auto`, gpt-5.5), two rounds under the
`CLAUDE.md` 2-round cap: round 1 eight findings, round 2 seven — **15 findings,
15 accepted, 0 rejected**. Rev 3 was the execution spec and the review log in the
plan records each finding against the task that answers it.

**Verification status.** Layer 1 is SDK-simulated and pinned: the suite went
1873 → 1915 passed / 30 skipped across eight commits, with `ruff check` and
`mypy --strict` clean at each. On-robot proof is v1.23.0's operator probe (plan
T7) and the three `feature_list.json` rows it feeds — `VOICE-SOLO-BARGE`,
`VOICE-LATE-INTERRUPT` and the new `VOICE-INTERRUPT-CONTEXT`, all
`implemented-unverified` until a person is in the room. Out of scope and
untouched, so review does not re-open them: RCA candidate 4 (re-time the cap —
moot, the cap is gate-on-only), candidates 5–6 (`reasoning.effort` and
`semantic_vad` eagerness A/Bs — instance `.env` probes, no code), and RCA
Finding 4, the first-audio latency growth from 2 s to 10 s over a session.
