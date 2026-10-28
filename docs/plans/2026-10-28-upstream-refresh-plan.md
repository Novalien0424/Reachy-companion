# Upstream refresh — port plan (2026-10-28)

Baselines: SDK `a258a00` → `a9a2a42a` (v1.13.0 + dev), Conversation App
`4dfc6e0` → `b731ea1` (v1.1.1). Survey reports merged into the two research
notes (§ "Upstream changes since baseline"). The robot runs daemon
`v1.10.0rc5`, which re-syncs `reachy_mini` in `/venvs/apps_venv` on every boot,
so **no `reachy-mini` bump** — everything here is app-side.

Robot status at plan time: **unreachable** (no ARP/ping, no daemon on the
/24, no mDNS service, no setup hotspot) — most likely powered off. Wave 1 is
verified by the local suite; on-robot verification waits for power-on.

## Wave 1 — app-side ports (TDD: upstream test first where one exists)

Two implementers in parallel, disjoint files, no commits (the orchestrator
reviews, commits, bumps the version).

### Implementer A — realtime / audio path
Owns `openai_realtime.py`, `huggingface_realtime.py`, `console.py`, `main.py`
and their tests.

- **A1 · stuck audio send (upstream `9863362`, #593).** Bound the
  `input_audio_buffer.append` at `openai_realtime.py:740` with a timeout; on
  timeout abort the websocket transport so the existing reconnect path runs.
  Port upstream's test. Verify: a test where the send never completes ends
  with the connection aborted and the reconnect path entered (not a hang).
- **A2 · discard warmup mic audio (`46027a5`, #577).** Drop samples captured
  before startup audio config completes (`console.py:801-818`). Port upstream
  test. Verify: no buffered startup audio reaches the realtime input.
- **A3 · mic reader off the event loop (console part of `8b37f31` only).**
  `get_audio_sample()` (`console.py:911`) blocks up to 20 ms on the asyncio
  loop that also runs realtime receive, playback and barge timers. Move the
  read to a dedicated thread feeding an asyncio queue, as upstream does; do
  NOT port local Silero VAD, ducking or paced playback (our D-023/D-028/D-032
  logic owns that). `clear_audio_queue` stays sync. Verify: suite green, a
  test that the loop is not blocked by a slow `get_audio_sample`.
- **A4 · import warmup (`215880b`, #513).** Warm `openai` and scipy imports
  during robot init in `main.py`. Verify: unit test or timing log line; no
  behavior change.
- **A5 · pin the OpenAI websocket to certifi.** openai 2.28.0's
  `realtime.connect` accepts `websocket_connection_options` (verified,
  `openai/resources/realtime/realtime.py:99`). Pass an SSL context built from
  `certifi.where()` in the `connect_kwargs` at `huggingface_realtime.py:3862`,
  so the realtime link stops depending on the robot's system CA store. Verify:
  a test asserts the connect call carries an SSL context whose CA file is
  certifi's.

### Implementer B — deps, motion, tools, UI
Owns `pyproject.toml`, `uv.lock`, `moves.py`, `tools/background_tool_manager.py`,
`mcp_client.py`, `tools/core_tools.py`, `static/js/api.js` and their tests.

- **B1 · certifi floor (`b731ea1` #596 / SDK `9fb2bfcb`, emSign CA
  compromise).** Add `certifi>=2026.10.19` to `dependencies`, `certifi = false`
  under `[tool.uv.exclude-newer-package]` (the 7-day cooldown would otherwise
  reject it), `uv lock --upgrade-package certifi`. The robot installs with pip
  from wheel metadata, so the `dependencies` entry is what moves it there.
  Not ported: the `pip>=26.2.2` floor (would upgrade pip in the shared apps
  venv; value unverified). Verify: lock resolves; dev venv certifi ≥
  2026.10.19 after sync.
- **B2 · listening debounce (moves part of `8b37f31`).** Remove the 150 ms
  toggle debounce (`moves.py:236-237, 410-414`) and the early return on
  shared state (`:296-298`) that can freeze antennas in listening. Port
  `test_listening_applies_immediately_and_preserves_queued_changes`.
- **B3 · MCP failures recorded as failures (`dd08179`, #565).** A remote MCP
  call that returns `isError` must surface as an `"error"` key so
  `background_tool_manager.py:187` marks it FAILED. Port upstream test.
- **B4 · corrupt tool-Spaces manifest (`b099b42`, #536).** Guard
  `read_installed_tool_spaces` in `_resolve_remote_tools` (`core_tools.py`)
  so a corrupt manifest logs and drops remote tools instead of taking down
  the registry (home control included). Port upstream test.
- **B5 · `/rpc` reconnect (`5e16366`, #584).** Port the `api.js` retry only
  if our `api.js` still matches upstream's pre-change shape; skip the
  conversation-panel / talk.js parts (UI we do not ship).

Gate for wave 1: full suite green (1917 passed / 31 skipped baseline, plus new
tests), `ruff check .` clean, `mypy` strict clean. Then version 1.23.0 →
1.24.0, CHANGELOG, feature_list rows.

## Deferred (with gate)

| Item | Why deferred | Gate |
|---|---|---|
| `resolve_instance_path` (`5ab905d`, #552): move instance state out of site-packages, retire the per-deploy backup/restore | Migrates personal data (memory, faces, OAuth) on the robot; must be proven on-device with a fresh backup in hand | Robot reachable; own plan |
| Camera JPEG colour (SDK `233d7ba9`): `get_frame_jpeg` emits RGB-component JPEGs on the dev box | Robot's GStreamer output unverified; affects the camera demo's colour answers and Drive uploads | Run the stub check on the Pi first |
| Daemon security fixes (path traversal in `/ws/sdk` audio upload, OAuth XSS, BLE PIN bypass, DNS-rebinding guard), starlette 1.x, tracking pitch trim | Daemon-side; app-only deploys | New operator authorization for a daemon upgrade (note: newer daemons default PostHog telemetry on) |
| MCP v2 client, `reachy-mini>=1.11` | Needs new deps / daemon ≥ 1.11 | Daemon upgrade |

## Self-review log

1. *Is any port daemon-side?* Checked each against the SDK survey: A1–A4,
   B1–B5 are app or client-library code in our package. Accepted.
2. *Does A3 collide with our barge-in logic?* Upstream's `8b37f31` bundles
   VAD + ducking + paced playback with the reader thread; only the reader is
   taken, `clear_audio_queue` stays sync so no caller changes. Scoped in A3.
3. *Would B1 actually reach the robot?* Deploy step 5 installs the wheel with
   pip, which honours wheel `Requires-Dist`, and daemon rc5 does not pin
   certifi (SDK survey §4). Accepted; post-deploy check added to the deferred
   on-robot list.
4. *The websocket-certifi item crossed the A/B file boundary* (connect kwargs
   live in A's `huggingface_realtime.py`). Moved to A as A5 after verifying
   the openai option exists, instead of making it conditional.
5. *Unverified claim carried in:* reconnect after abort (A1) — made the
   test's explicit assertion.
6. *Verification without a robot:* every item has a local test; on-robot
   checks (certifi version in apps_venv, boot with no false `speech_started`,
   antenna listening toggles) are listed for the next deploy.
