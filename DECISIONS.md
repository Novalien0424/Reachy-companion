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
