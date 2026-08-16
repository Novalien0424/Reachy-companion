# Research Map — Official Reachy Mini Conversation App (v1.0.0, commit `4dfc6e0`)

Surveyed 2026-08-16 by Opus subagent; spot-checked and accepted by orchestrator.

Roots (all paths below are relative to these):
- `APP/` = `reference/reachy_mini_conversation_app/src/reachy_mini_conversation_app/`
- `REPO/` = `reference/reachy_mini_conversation_app/`
- `SDK/` = `reference/reachy_mini/src/reachy_mini/`

**Three findings that change the plan:**
1. **There is no OpenAI backend at HEAD.** Deliberately deleted in commit `5b8d974` "Consolidate the app around the default backend (#444)", which removed `openai_realtime.py` (134 L), `gemini_live.py` (723 L) and the multi-backend base class `base_realtime.py` (1034 L). `APP/config.py:91-101` now actively *warns and ignores* `BACKEND_PROVIDER` / `MODEL_NAME`. The old OpenAI handler is recoverable: `git show 5b8d974^:src/reachy_mini_conversation_app/openai_realtime.py`.
2. **Web search and MCP already exist and work** — the search tool from Space `pollen-robotics/reachy-mini-search-tool` is preinstalled and enabled by default (`APP/tool_spaces.py:50-71`, `REPO/profiles/default/profile.md:16`).
3. **The app still speaks OpenAI Realtime protocol** — it drives the HF endpoint through `openai.AsyncOpenAI` + `openai.types.realtime.*` (`APP/huggingface_realtime.py:12-27`). The wire format we need is already implemented; only endpoint/model/rate differ.

## 1. Realtime backend integration

- Only backend: `APP/huggingface_realtime.py:117` `class HuggingFaceRealtimeHandler(ConversationHandler)`, `SAMPLE_RATE = 16000` (`:120`). This 1069-line file *is* the conversation loop — copy as the base for our `openai_realtime.py`.
- Client build: `:1015` `async def _build_realtime_client(self) -> AsyncOpenAI`; direct-ws path `:1020-1029`, HF session-allocator path `:1031-1068`. Replace whole method with `AsyncOpenAI(api_key=OPENAI_API_KEY)`.
- Session open: `:708` `async with self.client.realtime.connect(**connect_kwargs) as conn` — **`model=` is never passed** (HF server picks). Add `model="gpt-realtime-2.1"`. openai 2.x `connect(*, call_id, model, extra_query, extra_headers, …)`.
- Session config: `:222` `_get_session_config(self, tool_specs) -> RealtimeSessionCreateRequestParam` (`:224-245`). Only `instructions`, `audio`, `tools`, `tool_choice="auto"` set.
- Audio format: `:98-100` `_native_rate_audio_pcm() -> {"type":"audio/pcm","rate":None}` (HF-only extension). Swap for `AudioPCM(type="audio/pcm", rate=24000)` — what the deleted `openai_realtime.py` did.
- **Turn detection:** `:236` `turn_detection=ServerVad(type="server_vad", interrupt_response=True)` — **no other parameter tuned**. `ServerVad` also accepts `threshold`, `prefix_padding_ms`, `silence_duration_ms`, `idle_timeout_ms`, `create_response`; `SemanticVad(eagerness=low/medium/high/auto)` is the alternative. This is our tuning surface for US-01.
- **Reasoning:** never set. `RealtimeSessionCreateRequestParam` supports `reasoning`, `max_output_tokens`, `output_modalities`, `truncation`, `prompt`, `model` — free knobs.
- Transcription: `:232-235` `AudioTranscriptionParam(model="gpt-4o-transcribe", language=config.REALTIME_TRANSCRIPTION_LANGUAGE)`; env default `"en"` (`config.py:157-160,:319`) → set `zh`.
- Voices: `config.py:51-61` = Qwen3-TTS speakers, default `Aiden` (`:84`). Replace with OpenAI voices (old default `cedar`).
- Live session update: `change_voice` `:263`, `apply_personality` `:294`, `_restart_session` `:391` — reusable as-is.
- Response serialization: `_response_sender_loop` `:484-563` — one active response at a time, retries on `conversation_already_has_active_response` (`:912-918`). Hard-won; keep verbatim.

**Effort to reach `gpt-realtime-2.1`: code change, not config.** One new handler module (~150–250 LOC) replacing `_build_realtime_client` + `_get_session_config` + `SAMPLE_RATE`, plus `OPENAI_API_KEY`. Everything from `_run_realtime_session` (`:698`) down is model-agnostic.

## 2. Audio pipeline

- Owner: `APP/console.py:101` `class LocalStream`. Mic→backend: `record_loop` `:874-884` polls `robot.media.get_audio_sample()` → `handler.receive((rate, frame))`.
- `handler.receive` `APP/huggingface_realtime.py:947-982`: stereo→mono, `audio_to_int16` (`APP/streaming.py:39`), base64 → `input_audio_buffer.append`.
- Backend→speaker: `play_loop` `console.py:886-930` pops `handler.output_queue` → `audio_to_float32` → `robot.media.push_audio_sample(frame)`. Assistant PCM enqueued at `huggingface_realtime.py:841-854`.
- **Barge-in:** server VAD `interrupt_response=True`; on `input_audio_buffer.speech_started` (`:744-752`) handler calls `self._clear_queue()` → `LocalStream.clear_audio_queue` (`console.py:146`, impl `:843-861`) → SDK `media.audio.clear_player()` + drain output queue (`:863-872`). Manual path: JSON-RPC `conversation.interrupt` `console.py:588-595`.
- **Echo/self-hearing:** no software AEC; relies on the robot's XVF3800 DSP tuned at startup by `APP/audio/startup_config.py:12-20`, applied off-thread at `console.py:795`. Mic **never muted while robot speaks** (`console.py:881`) — deliberate full-duplex.

## 3. Turn handling

- Entirely server-side VAD; no local endpointing. `speech_started` `:744`, `speech_stopped` `:754`; end-of-turn = `conversation.item.input_audio_transcription.completed` `:809-829`.
- **No mid-sentence-pause tuning exists** — highest-value gap for PRD US-01.
- Partial transcripts debounced 0.5 s (`:143`, `:337-348`). Latency telemetry instrumented at `:774-777` and `:845-848`.
- Chinese: only lever is `REALTIME_TRANSCRIPTION_LANGUAGE=zh` (`REPO/.env.example:1-3`); default profile forces English (`REPO/profiles/default/profile.md:25`) — our profile must override.

## 4. Face tracking — local, no LLM round-trip

- `APP/moves.py:370-384` handles `set_head_tracking` → SDK `robot.start_head_tracking(weight=1.0)` / `stop_head_tracking()`.
- SDK: `SDK/reachy_mini.py:275` `start_head_tracking(weight=1.0)` (weight 0 pauses detection without teardown), `:288` `stop_head_tracking()`, `:293` `get_tracked_face(wait=True, timeout=5.0)`.
- **Smoothing is daemon-side**: `SDK/daemon/backend/abstract.py:353` `_tracking_alpha = 0.15`; `step_head_tracking()` `:716-751` eases aim per tick via `linear_pose_interpolation`, holds last aim on detection gap, recenters after `_tracking_lost_timeout`.
- Speaking handoff: `moves.py:385-401` — on `set_speaking(True)` (face locked) capture `_track_anchor`, set weight 0.0; restore 1.0 on stop.
- Model-facing toggle: `APP/tools/head_tracking.py:10-35`, `needs_response = False`. **Reuse verbatim.**

## 5. Camera / vision

- `APP/tools/camera.py:11-52` → `deps.reachy_mini.media.get_frame_jpeg()` (`SDK/media/media_manager.py:257`), returns `{"b64_im": <base64 jpeg>}`; requires `question`; disabled by `--no-camera` (`main.py:164`).
- **Image does not travel in the tool result:** `huggingface_realtime.py:167-175` strips `b64_im`, substitutes `image_attached: True`; then `:645-678` posts a separate user message with `{"type":"input_image","image_url":"data:image/jpeg;base64,…"}` after the `function_call_output`. On-demand snapshots only — matches PRD Mistake 5. **Reuse verbatim**; gpt-realtime-2.1 accepts the same `input_image` shape.

## 6. Emotion & motion

- **Arbitration owner:** `APP/moves.py:167` `class MovementManager` — one worker thread (`start()` `:625`, `working_loop()` `:722-765`), `CONTROL_LOOP_FREQUENCY_HZ = 60.0` (`:48`), exactly one `set_target()` per tick (`:550-569`, called `:752`); cross-thread input via `_command_queue` (`:231`, `:311-403`).
- **Layering** (`_get_primary_pose` `:466-507`): primary moves = sequential deque (`:205`, `:411-425`); with a speaking anchor, `EmotionQueueMove` is composed *onto* the anchor via `compose_world_offset` (`:503-504`) so emotions play while still looking at the user; dances play from neutral.
- **Idle breathing:** `BreathingMove` `:54-122` (z ±5 mm @ 0.1 Hz, antenna sway 15° @ 0.5 Hz), auto after `idle_inactivity_delay = 0.3` s (`:208`, `:427-464`).
- **Listening freeze:** antennas frozen while listening, blended back over 0.4 s (`:514-548`), 0.15 s debounce (`:224`).
- **Speech-reactive "talking" motion is NOT in this repo** — it's the daemon/SDK wobbler: `APP/main.py:291` `robot.enable_wobbling()`, driven by audio pushed via `media.push_audio_sample` (`SDK/reachy_mini.py:242-268`). Free for us.
- Move wrappers: `APP/dance_emotion_moves.py` — `DanceQueueMove:22`, `EmotionQueueMove:56`, `GotoQueueMove:90`.
- **Emotion catalogue** — `APP/tools/play_emotion.py`; clips from HF dataset `pollen-robotics/reachy-mini-emotions-library` (lazy load `:269`). Model sees a **42-intent enum** (`EMOTION_INTENTS` `:26-69`): random, happy, excited, loving, grateful, success, thinking, attentive, confused, uncertain, sad, downcast, lonely, angry, irritated, displeased, disgusted, scared, anxious, surprised, amazed, calming, relief, impatient, embarrassed, bored, tired, sleepy, yes, yes_understanding, no, no_sad, no_excited, no_firm, welcoming, greeting, goodbye, go_away, helpful, dance, electric, dying.
  Move names: `_EXCELLENT_MOVES` `:71-92` (anxiety1, boredom2, dance2, dance3, downcast1, dying1, exhausted1, grateful1, helpful1, loving1, rage1, reprimand1, resigned1, sad1, sad2, scared1, sleep1, surprised1, thoughtful1, welcoming2); `_OK_CLEAR_MOVES` `:94-122` (27 more); extras via `_INTENT_TO_MOVES` `:126-168`. Resolver `resolve_emotion_name` `:195-223`.
- Dances: enum from `reachy_mini_dances_library.collection.dance.AVAILABLE_MOVES` (`APP/tools/dance.py:12,:43-60`).
- **Local idle behaviour (no LLM):** `APP/idle_policy.py:60-65` weights DoNothing 0.60 / Dance 0.16 / PlayEmotion 0.16 / MoveHead 0.08, fired from `conversation_handler.py:76-95` after 180 s idle.
- Other motion tools: `move_head.py:14`, `sweep_look.py:14`, `stop_dance.py:10`, `stop_emotion.py:10`.

## 7. Tool calling

- **Contract:** `APP/tools/core_tools.py:57` `class Tool(abc.ABC)` — class attrs `name`, `description`, `parameters_schema` (JSON Schema), ClassVar `needs_response = True` (`:70`), `async def __call__(self, deps: ToolDependencies, **kwargs) -> Dict[str, Any]` (`:86`). `ToolDependencies` dataclass `:35-45` (`reachy_mini`, `movement_manager`, `instance_path`, `camera_enabled`, `motion_duration_s`, `go_to_sleep`).
- **Registry:** `initialize_tools(instance_path=None, *, force=False)` `:399-435`; `get_tool_specs(exclusion_list=None)` `:438`; `get_tools()` `:446`.
- **Dispatch (async, non-blocking):** `dispatch_tool_call` `:478`; `_dispatch_tool_call` `:463-475` converts exceptions to `{"error": …}`. Each call runs as own task via `BackgroundToolManager.start_tool` (`APP/tools/background_tool_manager.py:127-165`), started at `huggingface_realtime.py:856-904`. Completion `_handle_tool_result` `:565-696` waits for `response.done` before injecting `function_call_output`, batches parallel calls (`:164`, `:680-691`), skips spoken follow-up when `needs_response=False` (`:685`).
- **Adding a NEW tool:** (1) create `APP/tools/<name>.py` where **filename equals `Tool.name`**, subclass `Tool` (template: `tools/idle_do_nothing.py` or `REPO/external_content/external_tools/starter_custom_tool.py`); (2) add name to `default_tools` in profile front matter (`REPO/profiles/default/profile.md:3-19`) or via UI (`profile_toolsets.py:169-180`); (3) restart or `initialize_tools(force=True)`. Discovery by module name (`core_tools.py:367-396`, path `reachy_mini_conversation_app.tools.<name>` `:376`, fallback `REACHY_MINI_EXTERNAL_TOOLS_DIRECTORY`). Duplicate names fail fast (`:268-278`).
- **Web search: already present.** `APP/tool_spaces.py:50-71` preinstalled Space `pollen-robotics/reachy-mini-search-tool` (+ weather `:103-124`, time `:72-102`), enabled in default profile. Automatic invocation driven purely by tool description — no explicit "use web search" needed.
- **MCP: full client present.** `APP/mcp_client.py:271` `class RemoteMcpToolClient` — streamable-HTTP `_session()` `:353-368`, `list_tool_specs()` `:279`, `call_tool()` `:299-330` with timeout, namespacing `alias__tool` `:89-93`, auth via `RemoteMcpServerConfig.headers` (`:168`). Adapter: `RemoteMcpTool` `core_tools.py:100-139` (one retry `:126-134`). Startup resolves remote tools from cached manifest, zero network calls (`:322-364`).
- **MCP limitation is the installer, not the client:** `tool_spaces.py:405-425` hard-requires `https://*.hf.space/gradio_api/mcp/`; lower layer `mcp_client.py:75-86` accepts **any https URL**. Notion MCP needs a ~50-line alternative config path, not a new client.

## 8. Configuration & profiles

- `REPO/.env.example` (32 lines, no API keys): `REALTIME_TRANSCRIPTION_LANGUAGE`, `HF_REALTIME_CONNECTION_MODE`, `HF_REALTIME_WS_URL`, `HF_TOKEN`, `REACHY_MINI_CUSTOM_PROFILE`, `REACHY_MINI_EXTERNAL_PROFILES_DIRECTORY`, `REACHY_MINI_EXTERNAL_TOOLS_DIRECTORY`, `AUTOLOAD_EXTERNAL_TOOLS`, `REACHY_MINI_APP_TIMEOUT_MINUTES`.
- `APP/config.py:310` `class Config` (singleton `:417`), hot-refresh `:420-433`. Per-instance `.env` at `<instance_path>/.env` (`main.py:106-116`), written by UI (`console.py:339-380`).
- `REPO/profiles/` — 14 personalities; each one `profile.md`: `+++` TOML front matter (`schema_version`, `default_tools`, optional `voice`, `greeting`, `hidden`) + Markdown body = realtime `instructions`. Parser `profile_store.py:70-123`; memory injection `prompts.py:29-52`; greeting = synthetic user turn (`huggingface_realtime.py:454-482`).
- Per-profile tool overrides in instance-local `profile_toolsets.json` (`profile_toolsets.py:21-45`). Long-term memory in `memory.v1.json` (`memory.py:19`, tools `remember.py`/`forget.py`).
- **Reuse:** `profile.md` format adopted as-is; one new Chinese-first companion profile.

## 9. App structure

- Entry points (`REPO/pyproject.toml`): console script `reachy-mini-conversation-app = …main:main`; robot-app entry `[project.entry-points."reachy_mini_apps"]`.
- `APP/main.py:349` `class ReachyMiniConversationApp(ReachyMiniApp)`, `custom_app_url = "http://0.0.0.0:7860/"`, `run(self, reachy_mini, stop_event)` `:355` → module `run()` `:79`.
- **Threading:** (a) MovementManager thread 60 Hz; (b) one asyncio loop in `LocalStream.launch()` (`console.py:736-809`): `_run_handler_startup_loop` (5 s auto-retry), `record_loop`, `play_loop`; (c) optional uvicorn UI on 7860 (`main.py:271-278`); (d) inactivity-timeout thread (`main.py:29-61`).
- **SDK consumption:** `ReachyMini(**robot_kwargs)` `main.py:139`; wake-if-sleeping `app_lifecycle.py:57-75`; `movement_manager.start()` + `robot.enable_wobbling()` `main.py:287-291`; media `start_recording()/start_playing()` `console.py:783-784`; teardown `main.py:323-346`.
- Control surface: JSON-RPC `/rpc` via SDK `reachy_mini.apps.jsonrpc_server.JsonRpcServer` (`console.py:566-637`). Deployment: installed as a Reachy Mini app (dashboard supplies `instance_path` + `settings_app`). License Apache 2.0.

## (a) PRD §7 scope already satisfied outright

| §7 item | Status | Evidence |
|---|---|---|
| Barge-in / interruption | YES | `huggingface_realtime.py:236,744-752` + `console.py:843-861` |
| Face tracking | YES | `moves.py:370-401`, `SDK/reachy_mini.py:275-293`, smoothing `SDK/daemon/backend/abstract.py:353,716-751` |
| Expression/motion reuse | YES | `tools/play_emotion.py`, `tools/dance.py`, `moves.py:466-507`, wobbler `main.py:291` |
| Camera visual QA | YES | `tools/camera.py:11-52` + `huggingface_realtime.py:645-678` |
| Live web search | YES | `tool_spaces.py:50-71`, default profile |
| Function/tool calling | YES | `core_tools.py:57-90,399-490` + `background_tool_manager.py` |
| Add Skill w/o touching core | YES | one file in `tools/` + one profile line |
| Speech-to-speech | PARTIAL — wrong model | HF backend only (`config.py:63-101`) |
| Good turn handling | PARTIAL — untuned | `ServerVad` defaults only (`:236`) |
| MCP/external integration | PARTIAL — mechanism only | client complete, installer HF-locked (`tool_spaces.py:405-425`) |
| Home Control Skill | NO | nothing exists |

## (b) Actual gaps vs our POC

1. **`gpt-realtime-2.1`** — new handler module (~150–250 LOC): OpenAI client + `model=` + 24 kHz `AudioPCM` + OpenAI voices + `OPENAI_API_KEY`. Start from `git show 5b8d974^:…/openai_realtime.py` for shape; keep current `huggingface_realtime.py` event loop (the maintained one).
2. **Turn tuning for Chinese + mid-sentence pauses** — extend `_get_session_config` with `silence_duration_ms`/`prefix_padding_ms`/`threshold` or `SemanticVad(eagerness=…)`; `REALTIME_TRANSCRIPTION_LANGUAGE=zh`; Chinese-first profile.
3. **Notion MCP** — client done; write ~50-LOC generic-MCP config path (static `RemoteMcpServerConfig(alias, url, headers={"Authorization": …})` list) bypassing `validate_space_mcp_url`; reuse `RemoteMcpTool` unchanged.
4. **Home Control Skill** — ~60-LOC local `Tool` (copy `tools/head_tracking.py`) calling Home Assistant REST API, or second entry in the generic MCP list.
5. **Reasoning config** — never set; free to add.
6. **Web search "automatic"** — already automatic via description; decide keep HF Space tool (drags `HF_TOKEN`) vs direct provider.

## (c) Recommendation — own repo that depends on the SDK and copies ~8 modules

Not a fork, not a dependency on the conversation app:

- **Not importable as a library**: module-level singletons (`config` `config.py:417`, `ALL_TOOLS` `core_tools.py:91`), tool discovery hardcoded to package path (`core_tools.py:376`), no backend seam after `5b8d974`.
- **Packaging is a singleton app**: installing alongside ours creates a competing dashboard entry.
- **Fork is worst**: upstream deleted the multi-backend abstraction; re-adding OpenAI diverges in the one file upstream churns most. Their `AGENTS.md:29` says the app is not meant to be forked/vendored.
- **Copying is clean**: Apache 2.0; the valuable modules are backend-agnostic.

**Concrete shape** — new app package, `dependencies = ["reachy-mini>=1.10", "reachy_mini_dances_library", "openai", "mcp", "python-dotenv"]`. Copy near-verbatim (~2,500 LOC): `moves.py`, `dance_emotion_moves.py`, `idle_policy.py`, `streaming.py`, `conversation_handler.py`, `mcp_client.py`, `tools/background_tool_manager.py`, `tools/core_tools.py`, `tools/{camera,play_emotion,dance,stop_dance,stop_emotion,move_head,head_tracking,sweep_look,idle_do_nothing}.py`, `profile_store.py` + `prompts.py`, `audio/startup_config.py`, trimmed `console.py`. Write new (~400 LOC): `openai_realtime.py`, ~50-line `mcp_servers.py` (replaces 789-line `tool_spaces.py`), `tools/home_control.py`, one Chinese-first `profiles/companion/profile.md`. Keep the reference clone read-only to diff against upstream fixes.
