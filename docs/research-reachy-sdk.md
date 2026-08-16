# Research Map — Reachy Mini SDK (v1.10.0.dev0, commit `a258a00`)

Surveyed 2026-08-16 by Opus subagent; spot-checked and accepted by orchestrator.
`SDK/` = `reference/reachy_mini/` (paths relative to `src/reachy_mini/` unless noted).

## 0. The one thing that shapes everything

The SDK is **not** a library that drives motors — it is a **thin client to a
daemon**. One FastAPI/uvicorn daemon on port 8000 owns motors, kinematics,
camera, mic, speaker, face detection and move playback; the app is a separate
process on `ws://host:8000/ws/sdk` (pydantic JSON) + `http://host:8000/api/*`.

- Daemon entry `reachy-mini-daemon` → `daemon/app/main.py:124`; binds `0.0.0.0` only with `--wireless-version` (`main.py:111-121`).
- Backend control loop: **50 Hz daemon thread** (`daemon/daemon.py:404-425`).
- Commands are **last-write-wins, no queue** (`daemon/backend/abstract.py:1629-1687`); while a daemon-side move runs, raw `set_target` is **silently dropped** (`:1649-1657`).
- Client: sync websocket + one recv thread (`io/ws_client.py:85-96`); state 50 Hz, `DaemonStatus` only **1 Hz**.
- Rust in the loop: IK/FK `reachy_mini_rust_kinematics` (`kinematics/analytical_kinematics.py:14,43,85`).

## 1. Connection & app lifecycle

- `ReachyMini(robot_name, host="reachy-mini.local", port=8000, connection_mode="auto"|"localhost_only"|"network", media_backend="default", automatic_body_yaw=True, spawn_daemon, use_sim)` — `reachy_mini.py:104-117`; context manager `:182-191`. Connect: localhost → mDNS `_reachy-mini._tcp.local.` → literal host (`:465-528`).
- `ReachyMiniApp` ABC: `run(self, reachy_mini, stop_event)`, class attrs `custom_app_url`, `dont_start_webserver`, `request_media_backend` — `apps/app.py:27-50,153-162`; `wrapped_run()` `:80-152`. **Our app subclasses this.**
- `JsonRpcServer` (`apps/jsonrpc_server.py:1-22,50-57`) — ready-made control/telemetry channel.
- **Wireless = code runs ON the Pi.** Managed apps spawned by daemon as `python -u -m <module>` in shared `/venvs/apps_venv` (`apps/manager.py:203-211`; `apps/sources/local_common_venv.py:30-66`). Stop = SIGINT, 20 s grace, tree-kill. Host-PC dev supported via `connection_mode="network"` (WebRTC media).
- Packaging: entry-point group `reachy_mini_apps` = `module.main:ClassName` (`apps/templates/pyproject.toml.j2:17-18`); store = HF Spaces tagged `reachy_mini_python_app`; install = snapshot + `uv pip install` into `apps_venv`; launch via `POST /api/apps/start-app/{name}` (`daemon/app/routers/apps.py:49-334`).
- Auto-start: `daemon_config.json` key `startup_app` (`daemon/startup_app_config.py`); Wireless boots asleep, **antenna touch** wakes + launches startup app (`daemon/app/startup_app.py:80-100,220`).
- **Scaffolder:** `reachy-mini-app-assistant create --template conversation <name> <path>` clones the official conversation app, renames the package, rewrites `pyproject.toml`/entry point, generates `profiles/<name>/instructions.txt` + `tools.txt` — `apps/fork_conversation.py:16-90,242`. **The officially blessed "own repo reusing official code" path PRD §2/§10 asks for.**

## 2. Camera

- `ReachyMini.media` → `MediaManager` (`reachy_mini.py:171,193-196`).
- `get_frame() -> NDArray[uint8] | None` — **BGR (H,W,3)**, latest-frame-only, `None` after 20 ms (`media/media_manager.py:243-255`).
- `get_frame_jpeg() -> bytes | None` — `media_manager.py:257-262` → `camera_base.py:180-199`; one-shot jpegenc, "occasional stills only". **Use for the vision tool.**
- Wireless default **1280×720@30** (`media/camera_constants.py:260`); intrinsics `camera_base.py:65-103`; numpy-only undistortion `camera_utils.py:46-248`. Example `examples/take_picture.py`.

## 3. Microphone & speaker

- **Input:** `start_recording()` → poll `get_audio_sample()` → `stop_recording()` (`media_manager.py:280-297,327-332`). **float32 (N,2) stereo @ 16 kHz** (`media/audio_base.py:116-188`). Poll-only, no callback API. XVF3800 4-mic already beamformed to 2 ch.
- **Output:** file `play_sound(path)` (fire-and-forget, `:264-278`); stream `start_playing()` → `push_audio_sample(np.float32)` → `stop_playing()` (`:334-385`), F32LE 16 kHz, mono auto-duplicated (`:362-376`), sink 50 ms buffer / 5 ms latency, kept warm with silent mixer source (`audio_gstreamer.py:364-431`).
- **Barge-in:** `mini.media.audio.clear_player()` (`audio_gstreamer.py:506-520`) — **not proxied on MediaManager**, reach through `.audio`.
- **AEC:** hardware (XMOS) on-robot; software `webrtcdsp` only on dev machines without the Reachy card (`audio_base.py:46-48`).
- **DOA:** `get_DoA() -> (angle_rad, speech_detected) | None` (`media_manager.py:418-428`) or `GET /api/state/doa` — bonus for "who is speaking".
- No TTS, no volume API in the media layer (volume via `/api/volume`).

## 4. Face tracking — solved daemon-side

- `start_head_tracking(weight=1.0)` / `stop_head_tracking()` / `get_tracked_face(wait, timeout) -> FaceTarget` — `reachy_mini.py:275-295`; example `examples/head_tracking.py`.
- Detector: YuNet ONNX (HF `pollen-robotics/face_detection_yunet_2026may`), 1 CPU thread, 320 px (`vision/face_detector.py:11-14,60-94`). Tracker thread nice-19, aims at nose, `_AdaptiveCenterFilter` (α 0.3/0.6, dead zone 0.02) (`vision/face_tracking.py:44-72,84-130`).
- Blending: `step_head_tracking()` eases α=0.15/tick, holds on loss, recenters after 2.0 s (`daemon/backend/abstract.py:353-354,716-753`); aim blended with app pose by `weight`; at `weight>=1.0` app head pose ignored (`:561-564,602-603`).
- `look_at_image(u,v,duration)` / `look_at_world(x,y,z)` (`reachy_mini.py:772-870`; `vision/look_at.py:26-82`).
- **US-02 is solved by `start_head_tracking(weight≈0.6-1.0)`.** Caveat: `get_tracked_face()` rides the 1 Hz status stream; use `GET /api/media/tracking/face` for fast polling.

## 5. Motion

- **Two idioms** (`SDK/AGENTS.md:111-116`, `SDK/skills/control-loops.md:17-40`): `goto_target(...)` for gestures ≥0.5 s; `set_target(...)` from **one** ~100 Hz loop for reactive motion. Never from two threads.
- `set_target(head: 4x4|None, antennas: [r,l] rad|None, body_yaw|None)` — `reachy_mini.py:610-670`. `goto_target(..., duration=0.5, method=minjerk|linear|ease_in_out|cartoon)` — blocking, daemon-side (`:672-722`).
- `create_head_pose(x,y,z,roll,pitch,yaw,mm=False,degrees=True)` (`utils/__init__.py:13-22`); `compose_world_offset` (`utils/interpolation.py:207-221`).
- Emotions: `RecordedMoves("pollen-robotics/reachy-mini-emotions-library").get(name)` → `mini.play_move(move, initial_goto_duration=1.0)` (`motion/recorded_move.py:25-35,168-235`; `reachy_mini.py:1115-1171`); `cancel_move()` `:1105`. NOTE: `play_move` streams ~100 Hz set_target from **our** process; a daemon-side fire-and-forget path exists only via REST `POST /api/move/play/recorded-move-dataset/{ds}/{name}` (`routers/move.py:177`) — consider for emotions on the Pi.
- **Speech-reactive motion built in:** `enable_wobbling()/disable_wobbling()` (`reachy_mini.py:242-273`) — `SwayRollRT` ported from the conversation app (`motion/speech_tapper.py:75-233`), scheduled at playback PTS, composed pre-IK.
- Limits: pitch/roll ±40°, head yaw ±180°, body yaw ±160°, head−body delta ≤65° (`SDK/AGENTS.md:221-233`), enforced in `inverse_kinematics_safe` — **unreachable pose raises ValueError** in the daemon loop (`abstract.py:582-583`).
- **Arbitration (emotion vs breathing vs tracking) is NOT in the SDK** — it lives in the conversation app's `moves.py` (`SDK/skills/control-loops.md:46-66`).

## 6. Robot state

`get_current_head_pose()`, `get_current_joint_positions() -> (head[7], antennas[2])` (`reachy_mini.py:926-959`); `imu` property 50 Hz, **Wireless only** (`:297-325`). `StateSnapshot`: `is_move_running`, `is_recording`, `motor_mode`, `face_target`, `doa` (`io/protocol.py:111-133`). REST mirror `/api/state/*`. **No battery API anywhere.**

## 7. Developing without hardware

- `reachy-mini-daemon --mockup-sim` — no physics, real FK/IK, 50 Hz; apps use local webcam/mic directly (`daemon/backend/mockup_sim/backend.py:26-137`). **Best fit for Windows dev.**
- `reachy-mini-daemon --sim` — MuJoCo 3.3.0 + viewer, virtual cameras UDP:5005.
- `media_backend="no_media"` → MediaManager no-op, zero GStreamer (`media_manager.py:93-102`) — motion-only dev.
- Rerun viewer broken on Windows (imports Placo; Placo excluded on win32, `pyproject.toml:71`).

## 8. Dependencies & environment

Python **≥3.11**; install with `uv`. Key deps: numpy≥2.2.5, scipy, onnxruntime==1.27.0, fastapi/uvicorn/websockets, huggingface-hub 1.20.1–2 (private internals, CI-guarded), rust kinematics/motor crates. Windows: prebuilt `gstreamer-bundle==1.28.3`; `placo`/`lgpio`/`gpiozero`/`nmcli` Linux-only; app stop degrades to `terminate()` (no SIGINT cleanup) (`apps/manager.py:319-326`).

## (a) PRD §7 items the SDK already solves

| Item | Covered | Anchor |
|---|---|---|
| Face tracking | Fully | `reachy_mini.py:275-295`; `vision/face_tracking.py`; `abstract.py:716-753` |
| Expression/motion reuse | Yes | `motion/recorded_move.py`; `reachy_mini.py:1115-1171` |
| Speech-reactive movement | Yes | `reachy_mini.py:242-273`; `motion/speech_tapper.py` |
| Camera VQA capture | Yes | `media_manager.py:257-262` |
| Barge-in audio flush | Yes | `audio_gstreamer.py:506-520` |
| Mic/speaker I/O | Yes (16 kHz) | `audio_base.py:116-188`; `media_manager.py:334-385` |
| Skill/app extension | Partly | `reachy_mini_apps` entry point; `JsonRpcServer` |
| Realtime LLM, VAD, tools, MCP, home | **No — zero LLM/OpenAI/MCP code** | grep confirmed |

## (b) Real gaps vs our POC

1. No realtime-model plumbing — session, VAD, tool dispatch, MCP are ours (from the conversation app).
2. **Audio 16 kHz fixed; gpt-realtime wants 24 kHz PCM16** — resample both directions ourselves.
3. Mic is poll-only — our capture loop feeds the websocket.
4. Stereo in / mono expected out — downmix on us.
5. `clear_player` not on MediaManager — reach into `mini.media.audio`.
6. No `ReachyMini` API for daemon-side recorded-move playback (REST only).
7. No motion arbitration layer (conversation app's `moves.py`).
8. No battery/health telemetry. 9. `get_tracked_face()` is 1 Hz (REST for fast).

## (c) Surprises that shape the architecture

1. **`stop_recording()`/`stop_playing()` set the SAME shared GStreamer pipeline to NULL** (shared so AEC shares a clock) — stopping either direction kills the other (`audio_gstreamer.py:124-133,480-504`). `clear_player()` transitions PAUSED→PLAYING, briefly interrupting capture — measure vs barge-in. **The #1 footgun for a full-duplex voice app.**
2. Daemon-side moves drop app `set_target`; tracking `weight>=1.0` overrides app head pose — the expression layer must respect this.
3. `RobotAppLock` does not protect against direct LAN SDK clients — stray dev scripts can fight our app.
4. The `--template conversation` scaffolder exists (`apps/fork_conversation.py`) — strong default starting point.
5. Media backend auto-selection changes behavior: LOCAL (on-Pi, low-latency) vs WEBRTC (host-PC) vs NO_MEDIA. **On-Pi LOCAL is the right runtime for the POC**; Pi CPU is shared with 50 Hz loop + YuNet + GStreamer.
6. `release_media()`/`acquire_media()` escape hatch for direct sounddevice/OpenCV use (loses hardware AEC routing + wobbling).
7. SDK↔daemon version skew warns (`reachy_mini.py:410-431`); pin versions.
8. Face model + emotion datasets download from HF on first use — **preload before demos** or cold cache = visible stall.
