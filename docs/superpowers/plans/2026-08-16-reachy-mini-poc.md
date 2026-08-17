# Reachy Mini Realtime AI Agent POC — Implementation Plan (Rev 4, post Codex rounds 1–3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Per project CLAUDE.md, implementation subagents run on **Opus**; the main session reviews between tasks.

**Goal:** A Reachy Mini Wireless app where a person can hold a natural, interruptible Chinese voice conversation with `gpt-realtime-2.1` while the robot tracks their face, reacts physically, sees on demand, searches the web automatically, reads Notion via MCP, and controls one real home device.

**Architecture:** Bootstrap our own app from the SDK's official conversation-app scaffolder (D-001), then surgically replace the HuggingFace realtime backend with a new `openai_realtime.py` handler (D-002) while keeping the scaffold's movement arbitration, tools, profiles, and audio plumbing intact. Add a generic MCP registration path (D-004), a Home Assistant tool (D-005), and a Chinese-first locked profile (D-003). Robot-level functions (tracking, wobbling, emotions) stay in the SDK daemon — never recreated.

**Tech Stack:** Python ≥3.11, `uv`, `reachy-mini` SDK (daemon client), `openai` (AsyncOpenAI Realtime), `mcp`, `scipy` (resampling), `httpx`, pytest + pytest-asyncio.

**Spec:** `docs/PRD.md` (product), `docs/research-reachy-sdk.md` + `docs/research-conversation-app.md` (verified API map), `DECISIONS.md` (D-001…D-008).

## Global Constraints

- Python ≥3.11; package/env management with `uv`; venv at `.venv` in repo root.
- Never recreate: face tracking, gaze smoothing, camera access, motion primitives, motion arbitration, emotion animations, speech-reactive movements, audio I/O (CLAUDE.md; PRD §10).
- Model is exactly `gpt-realtime-2.1`. Transcription language `zh`.
- Secrets only via env / `.env` (gitignored): `OPENAI_API_KEY`, `NOTION_MCP_URL`, `NOTION_MCP_TOKEN`, `HA_URL`, `HA_TOKEN`, `HA_ENTITIES`, and (fallback only) `TAVILY_API_KEY`. Never committed.
- The scaffolded app package is `reachy_companion` at repo root `reachy_companion/`.
- Reference clones in `reference/` are READ-ONLY; for diffing and `git show` recovery only.
- Windows dev runs against `reachy-mini-daemon --mockup-sim` (D-008); on-robot verification is the final gate.
- Every commit step: explicit `git add` of each created/modified path (never rely on `commit -a` for new files), then `git status --short` must show a clean tree.
- If any signature differs from the research notes (upstream moved), STOP, re-read the actual source in the scaffolded app, update the research doc in the same commit.

---

### Task 1: Repository bootstrap — git, scaffold, work queue

**Files:**
- Create: `reachy_companion/` (scaffolder output), `feature_list.json`
- Modify: `CLAUDE.md` (Project Shape)

**Interfaces:**
- Produces: installable package `reachy_companion` (entry point group `reachy_mini_apps`) containing the scaffolded modules later tasks modify: `src/reachy_companion/huggingface_realtime.py`, `config.py`, `main.py`, `console.py`, `moves.py`, `tools/`, and the **locked profile** at the app root: `reachy_companion/profiles/<LOCKED>/profile.md` (exact name = the `LOCKED_PROFILE` constant the scaffolder writes into the generated `config.py` — read it there; pattern `_<app>_locked_profile`). Also: pytest + pytest-asyncio installed in `.venv`.

- [ ] **Step 1: Initialize git and commit the existing docs**

```powershell
cd C:\Project\Reachy-mini
git init -b main
git add CLAUDE.md .gitignore DECISIONS.md progress.md docs .claude
git commit -m "chore: project docs, contract, skills, research notes"
```

- [ ] **Step 2: Create venv and install the SDK with uv**

```powershell
uv venv .venv
uv pip install --python .venv reachy-mini
.venv\Scripts\reachy-mini-app-assistant --help
```
Expected: help text listing a `create` command. If `uv` is missing: `winget install astral-sh.uv`.

- [ ] **Step 3: Run the conversation-app scaffolder**

```powershell
.venv\Scripts\reachy-mini-app-assistant create --template conversation reachy_companion C:\Project\Reachy-mini
```
Interactive (questionary) if args are omitted — pass both. Expected: `reachy_companion/` with `pyproject.toml` (`name = "reachy_companion"`), `src/reachy_companion/`, and a locked-profile folder announced in the console output (`SDK apps/fork_conversation.py:63-90`). If the scaffolder refuses a target inside a git repo, scaffold into a temp dir and move the result in.

- [ ] **Step 4: Remove the nested git repo the scaffolder creates** (it runs `git init` inside the app — `fork_conversation.py:423-430`; leaving it produces a gitlink, not files, in our repo):

```powershell
Remove-Item -Recurse -Force C:\Project\Reachy-mini\reachy_companion\.git
```

- [ ] **Step 5: Convert the locked profile to the format and LOCATION the app requires.** Two upstream skews (Codex R1-2, R2-1): the scaffolder emits legacy `instructions.txt`/`tools.txt` AND writes them under `src/<app>/profiles/` (`fork_conversation.py:368`), but the app at HEAD (a) requires a `profile.md` per profile and **exits at import** when the locked profile has none (`config.py:280-290`), and (b) resolves profiles from the **app-root `profiles/` directory** for a source checkout (`config.py:32-45`) — the root dir is also what wheel packaging includes (upstream `setup.py:22`). `LOCKED_PROFILE` overrides `REACHY_MINI_CUSTOM_PROFILE` (`config.py:336,433`). So: create `reachy_companion/profiles/<LOCKED>/profile.md` (app root, NOT under `src/`) with minimal valid content (full Chinese version comes in Task 7), and remove the scaffolder's stray `src/reachy_companion/profiles/` folder if the generated `config.py`'s resolution (Step 6's import check) doesn't use it. Confirm packaging: the generated `pyproject.toml`/`setup.py` must ship the root `profiles/` dir into the wheel exactly as upstream does (`setup.py:22`) — fix it now if the scaffolder dropped that:

```markdown
+++
schema_version = 1
default_tools = ["camera", "play_emotion", "head_tracking"]
+++

You are Reachy, a friendly desktop robot companion. (Placeholder — replaced in Task 7.)
```
Keep or delete `instructions.txt`/`tools.txt` per what the generated `profile_store.py` actually reads — read it first; `profile.md` is authoritative at HEAD.

- [ ] **Step 6: Install the app editable WITH dev dependencies and verify import**

```powershell
uv pip install --python .venv -e ./reachy_companion --group dev
.venv\Scripts\python -c "import reachy_companion.config as c; print('LOCKED_PROFILE =', c.LOCKED_PROFILE)"
```
If `--group dev` fails (group name differs in the generated `pyproject.toml` — upstream defines dev deps at `pyproject.toml:28-36`), install directly: `uv pip install --python .venv pytest pytest-asyncio pytest-cov`. Expected: the import succeeds and prints the locked profile name (no `logger.critical` about a missing profile).

- [ ] **Step 7: Create the work queue with the demo gates**

Create `feature_list.json`:

```json
{
  "items": [
    {"id": "DEMO-1", "behavior": "Multi-turn natural Chinese conversation on gpt-realtime-2.1; user can interrupt Reachy mid-speech; a natural ~1s mid-sentence pause does not trigger a premature response", "verification": "Live run on robot: 5-turn Chinese conversation incl. one barge-in and one ~1s mid-sentence pause", "state": "planned", "evidence": null, "next": "Task 4-8"},
    {"id": "DEMO-2", "behavior": "Reachy reacts with an appropriate emotion move during conversation while continuing to track the speaker", "verification": "Live run: 'I got the job!' triggers excited-class move layered on tracking anchor", "state": "planned", "evidence": null, "next": "Task 9"},
    {"id": "DEMO-3", "behavior": "User shows an object; Reachy captures a frame and describes it correctly", "verification": "Live run: 'What am I holding?' with 3 different objects, 3/3 correct", "state": "planned", "evidence": null, "next": "Task 10"},
    {"id": "DEMO-4", "behavior": "Question about today's news triggers automatic web search without being asked to search", "verification": "Live run: 'What happened with NVIDIA today?' → search tool call visible in logs → current answer", "state": "planned", "evidence": null, "next": "Task 11"},
    {"id": "DEMO-5", "behavior": "Natural-language command controls one real home device via Home Assistant", "verification": "Live run: 'Turn on the living room lights' → HA service call succeeds → light state changes", "state": "planned", "evidence": null, "next": "Task 13"},
    {"id": "US-02", "behavior": "Face tracking is active continuously from app startup, with no model tool call required", "verification": "Dev + robot run: tracking active immediately after launch; log shows no head_tracking tool call", "state": "planned", "evidence": null, "next": "Task 9"},
    {"id": "US-07", "behavior": "Reachy reads project status from Notion via MCP", "verification": "Live run: 'What is the latest status of my Magic Mirror project in Notion?' → notion__* tool call → correct summary", "state": "planned", "evidence": null, "next": "Task 12"},
    {"id": "US-09", "behavior": "A new Skill can be added without touching the conversational core", "verification": "home_control added as one file + one profile line; documented in docs/adding-a-skill.md", "state": "planned", "evidence": null, "next": "Task 14"}
  ]
}
```

- [ ] **Step 8: Update CLAUDE.md Project Shape** — add "`reachy_companion/`: our app (scaffolded from the official conversation app, D-001)".

- [ ] **Step 9: Commit**

```powershell
git add reachy_companion feature_list.json CLAUDE.md
git commit -m "feat: scaffold reachy_companion app from official conversation template (D-001)"
git status --short
```
Expected: empty status. Spot-check the commit actually contains files: `git ls-files reachy_companion | Measure-Object -Line` > 50.

---

### Task 2: Windows dev loop — mockup-sim daemon smoke test

**Files:**
- Create: `scripts/dev_daemon.ps1`, `scripts/smoke_sdk.py`

**Interfaces:**
- Produces: daemon launcher + SDK connect smoke test. Later tasks assume "daemon running via dev_daemon.ps1" as test precondition.

- [ ] **Step 1: Write the daemon launcher**

`scripts/dev_daemon.ps1`:
```powershell
# Starts the Reachy Mini daemon in mockup-sim mode for local development (D-008).
& "$PSScriptRoot\..\.venv\Scripts\reachy-mini-daemon" --mockup-sim
```

- [ ] **Step 2: Write the smoke script**

`scripts/smoke_sdk.py`:
```python
"""Smoke test: SDK client connects to a local daemon and reads state.

Precondition: scripts/dev_daemon.ps1 running in another terminal.
"""
from reachy_mini import ReachyMini

with ReachyMini(connection_mode="localhost_only", media_backend="no_media") as mini:
    pose = mini.get_current_head_pose()
    assert pose.shape == (4, 4), f"unexpected head pose shape {pose.shape}"
    print("OK: connected, head pose:\n", pose)
```

- [ ] **Step 3: Run it** — Terminal A: `powershell scripts/dev_daemon.ps1`; Terminal B: `.venv\Scripts\python scripts\smoke_sdk.py`. Expected: `OK: connected`. GStreamer errors with `no_media` → record exact error in progress.md before proceeding.

- [ ] **Step 4: Commit** — `git add scripts; git commit -m "chore: mockup-sim dev daemon launcher and SDK smoke test (D-008)"; git status --short` (clean).

---

### Task 3: Recover and study the deleted OpenAI handler

**Files:**
- Create: `reference/recovered/openai_realtime_5b8d974.py`, `reference/recovered/base_realtime_5b8d974.py` (gitignored)
- Modify: `docs/research-conversation-app.md` (append findings)

**Interfaces:**
- Produces: verified answers in the research doc to: (Q1) old handler's SAMPLE_RATE; (Q2) where 16 kHz robot ↔ model-rate conversion happened, if anywhere; (Q3) old session config for model/voice/format. Tasks 4–5 consume these.

- [ ] **Step 1: Recover the deleted files**

```powershell
New-Item -ItemType Directory -Force reference\recovered | Out-Null
git -C reference\reachy_mini_conversation_app show "5b8d974^:src/reachy_mini_conversation_app/openai_realtime.py" | Out-File -Encoding utf8 reference\recovered\openai_realtime_5b8d974.py
git -C reference\reachy_mini_conversation_app show "5b8d974^:src/reachy_mini_conversation_app/base_realtime.py" | Out-File -Encoding utf8 reference\recovered\base_realtime_5b8d974.py
```
Expected: two non-empty Python files.

- [ ] **Step 2: Answer Q1–Q3** from the recovered files + the scaffolded `streaming.py`/`console.py`. Search: `SAMPLE_RATE`, `resample`, `24000`, `rate`, `AudioPCM`, `voice`. Known already (Codex-verified): the CURRENT `console.py` `play_loop` discards the handler's rate label and pushes samples unchanged to the 16 kHz robot sink (`console.py:905-924`) — so 24 kHz output WILL play slow/pitch-shifted unless converted before or at the queue.

- [ ] **Step 3: Append "Recovered OpenAI handler (5b8d974^)" subsection** to `docs/research-conversation-app.md` with Q1–Q3 answers, `file:line`-anchored.

- [ ] **Step 4: Commit** — `git add docs\research-conversation-app.md; git commit -m "docs: recovered-handler findings (sample rate, resampling, session config)"; git status --short` (clean).

---

### Task 4: Audio resampling helper (unconditional)

Upstream does NOT rate-convert (Codex finding 7, `console.py:905-924`); this helper is required.

**Files:**
- Create: `reachy_companion/src/reachy_companion/audio/resample.py`, `reachy_companion/src/reachy_companion/audio/__init__.py`
- Test: `reachy_companion/tests/test_resample.py`

**Interfaces:**
- Produces: `resample_pcm(frame: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray` — float32 in/out, length scaled by dst/src. Task 5 calls it in `receive()` (16k→24k) and at output enqueue (24k→16k).

- [ ] **Step 1: Write the failing test**

`reachy_companion/tests/test_resample.py`:
```python
import numpy as np
from reachy_companion.audio.resample import resample_pcm


def test_upsample_16k_to_24k_length_and_dtype():
    frame = np.sin(np.linspace(0, 2 * np.pi * 220, 1600)).astype(np.float32)  # 100 ms @ 16 kHz
    out = resample_pcm(frame, 16000, 24000)
    assert out.dtype == np.float32
    assert abs(len(out) - 2400) <= 2


def test_downsample_24k_to_16k_roundtrip_energy():
    frame = np.sin(np.linspace(0, 2 * np.pi * 220, 2400)).astype(np.float32)
    out = resample_pcm(frame, 24000, 16000)
    assert abs(len(out) - 1600) <= 2
    assert 0.8 < (np.abs(out).mean() / np.abs(frame).mean()) < 1.2


def test_same_rate_is_identity():
    frame = np.zeros(160, dtype=np.float32)
    assert resample_pcm(frame, 16000, 16000) is frame


def test_2d_channel_first_frames_resample_on_sample_axis():
    # Model PCM arrives shaped (1, N) (huggingface_realtime.py:843) —
    # the SAMPLE axis is the last one; a wrong-axis resample leaves N unchanged.
    frame = np.zeros((1, 2400), dtype=np.float32)
    out = resample_pcm(frame, 24000, 16000)
    assert out.shape[0] == 1
    assert abs(out.shape[-1] - 1600) <= 2
```

- [ ] **Step 2: Run to verify failure** — `.venv\Scripts\python -m pytest reachy_companion\tests\test_resample.py -v` → FAIL (module not found).

- [ ] **Step 3: Implement**

`reachy_companion/src/reachy_companion/audio/resample.py`:
```python
"""Rate conversion between robot audio (16 kHz) and gpt-realtime (24 kHz)."""
import numpy as np
from scipy.signal import resample_poly


def resample_pcm(frame: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    if src_rate == dst_rate:
        return frame
    g = np.gcd(src_rate, dst_rate)
    # axis=-1: model PCM is (1, N) channel-first (huggingface_realtime.py:843);
    # 1-D mic frames are unaffected. Default axis=0 would resample the wrong dim.
    out = resample_poly(frame.astype(np.float32), dst_rate // g, src_rate // g, axis=-1)
    return out.astype(np.float32)
```
Note: the scaffolded package already has an `audio/` subpackage (`audio/startup_config.py`) — add the module beside it; do not create a duplicate package.

- [ ] **Step 4: Run to verify pass** → 4 passed.

- [ ] **Step 5: Commit** — `git add reachy_companion\src\reachy_companion\audio\resample.py reachy_companion\src\reachy_companion\audio\__init__.py reachy_companion\tests\test_resample.py; git commit -m "feat: 16k/24k PCM resampling helper"; git status --short` (clean). (If `audio/__init__.py` already existed from the scaffold, the extra `git add` is a no-op.)

---

### Task 5: `openai_realtime.py` — the gpt-realtime-2.1 handler (D-002)

**Files:**
- Create: `reachy_companion/src/reachy_companion/openai_realtime.py`
- Test: `reachy_companion/tests/test_openai_realtime_config.py`

**Interfaces:**
- Consumes: `HuggingFaceRealtimeHandler` (scaffolded; loop kept verbatim), `resample_pcm` (Task 4).
- Produces: `class OpenAIRealtimeHandler(HuggingFaceRealtimeHandler)` — `SAMPLE_RATE = 24000`; `_build_realtime_client()` → `AsyncOpenAI(api_key=…)`; `_get_session_config(tool_specs)` sets `model="gpt-realtime-2.1"`, 24 kHz `AudioPCM`, tuned turn detection, `zh` transcription; `model=` passed at `realtime.connect`; **both-direction resampling inside the handler** so `console.py` stays untouched. Task 6 wires it into `main.py`'s `build_handler()`.

Override surface (verified): `SAMPLE_RATE` (`huggingface_realtime.py:120`), `_build_realtime_client` (`:1015`), `_get_session_config` (`:222-245`), connect site (`:708`), `receive()` (`:947-982`), output enqueue (`:841-854`).

- [ ] **Step 1: Write the failing tests**

`reachy_companion/tests/test_openai_realtime_config.py`:
```python
"""Session-config unit tests — no network, no robot."""
import os
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-real")

from reachy_companion.openai_realtime import OpenAIRealtimeHandler


@pytest.fixture()
def handler():
    h = OpenAIRealtimeHandler.__new__(OpenAIRealtimeHandler)  # skip heavy __init__
    h.get_current_voice = MagicMock(return_value="cedar")
    h.instance_path = None  # _get_session_config reads it (huggingface_realtime.py:226)
    return h


def test_sample_rate_is_24k():
    assert OpenAIRealtimeHandler.SAMPLE_RATE == 24000


def test_session_config_targets_gpt_realtime_21(handler):
    cfg = handler._get_session_config(tool_specs=[])
    assert cfg["model"] == "gpt-realtime-2.1"
    assert cfg["audio"]["output"]["format"]["rate"] == 24000
    assert cfg["audio"]["input"]["transcription"]["language"] == "zh"


def test_vad_tuning_from_env(monkeypatch, handler):
    monkeypatch.setenv("REALTIME_VAD_SILENCE_DURATION_MS", "800")
    cfg = handler._get_session_config(tool_specs=[])
    td = cfg["audio"]["input"]["turn_detection"]
    assert td["type"] == "server_vad"
    assert td["silence_duration_ms"] == 800
    assert td["interrupt_response"] is True
```
`RealtimeSessionCreateRequestParam` is a TypedDict; the `_param` variants of the nested types are also TypedDicts, so index access works. If `get_session_instructions(None)` inside the base method needs more, mirror how it degrades and stub the module function with `monkeypatch`.

- [ ] **Step 2: Run to verify failure** → FAIL (no module `openai_realtime`).

- [ ] **Step 3: Implement the handler**

**Imports MUST come from the `_param` modules** — mirror the scaffolded `huggingface_realtime.py:17-27` import block exactly (Codex finding 5; e.g. `from openai.types.realtime.realtime_audio_input_turn_detection_param import ServerVad, SemanticVad` — copy the actual module paths used there for `AudioPCM` and the session param types; the non-`_param` response models are not subscriptable and break the tests).

```python
"""OpenAI gpt-realtime-2.1 backend (D-002).

Subclasses the maintained HF handler, replacing client build, session config,
sample rate, connect(model=), and adding 16k<->24k resampling at the two audio
boundaries. Everything else is inherited verbatim.
"""
import os

from openai import AsyncOpenAI
# _param imports: mirror huggingface_realtime.py:17-27 exactly (see note above)

from .audio.resample import resample_pcm
from .config import config
from .huggingface_realtime import HuggingFaceRealtimeHandler

MODEL = "gpt-realtime-2.1"
ROBOT_RATE = 16000


def _turn_detection():
    """Server-side VAD, tunable via env for Chinese mid-sentence pauses (D-003)."""
    if os.getenv("REALTIME_VAD_TYPE", "server_vad") == "semantic_vad":
        return SemanticVad(
            type="semantic_vad",
            eagerness=os.getenv("REALTIME_VAD_EAGERNESS", "auto"),
            interrupt_response=True,
        )
    return ServerVad(
        type="server_vad",
        interrupt_response=True,
        threshold=float(os.getenv("REALTIME_VAD_THRESHOLD", "0.5")),
        prefix_padding_ms=int(os.getenv("REALTIME_VAD_PREFIX_PADDING_MS", "300")),
        silence_duration_ms=int(os.getenv("REALTIME_VAD_SILENCE_DURATION_MS", "800")),
    )


class OpenAIRealtimeHandler(HuggingFaceRealtimeHandler):
    SAMPLE_RATE = 24000

    async def _build_realtime_client(self) -> AsyncOpenAI:
        return AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])  # fail fast if unset

    def _get_session_config(self, tool_specs):
        cfg = super()._get_session_config(tool_specs)
        cfg["model"] = MODEL
        cfg["audio"]["output"]["format"] = AudioPCM(type="audio/pcm", rate=24000)
        cfg["audio"]["input"]["format"] = AudioPCM(type="audio/pcm", rate=24000)
        cfg["audio"]["input"]["turn_detection"] = _turn_detection()
        cfg["audio"]["input"]["transcription"]["language"] = config.REALTIME_TRANSCRIPTION_LANGUAGE
        return cfg
```
Then the three integration points:
1. **`model=` at connect** (`:708`): read how `connect_kwargs` is built; add `model=MODEL` — if not injectable, copy the containing method, change only that line, mark `# copied from base :698-740, changed: model=`.
2. **Input resampling**: override `receive()` — after the base's stereo→mono downmix point, `mono = resample_pcm(mono, robot_rate, self.SAMPLE_RATE)` before int16/base64 (mirror base `:947-982`; the incoming tuple carries the robot rate).
3. **Output resampling**: at the enqueue site (base `:841-854`), convert model PCM to the robot rate before putting it on `output_queue` — `resample_pcm(pcm_f32, self.SAMPLE_RATE, ROBOT_RATE)` — so the untouched `play_loop` (`console.py:905-924`, pushes unchanged) receives 16 kHz. Override the smallest method that contains the enqueue; copy-and-mark if the event loop method is monolithic.
Also, both in the scaffolded `config.py` (staged in this task's commit): (a) replace the Qwen voice list (`:51-61,84`) with OpenAI realtime voices `["alloy","ash","ballad","cedar","coral","echo","marin","sage","shimmer","verse"]`, default `cedar`; (b) flip the `REALTIME_TRANSCRIPTION_LANGUAGE` default `"en"` → `"zh"` (`config.py:157-160,319`) — this task's test asserts `zh`, so the default must change HERE, not in Task 6 (Codex R3-1).

- [ ] **Step 4: Run to verify pass.**

- [ ] **Step 5: Commit** — `git add reachy_companion\src\reachy_companion\openai_realtime.py reachy_companion\tests\test_openai_realtime_config.py reachy_companion\src\reachy_companion\config.py; git commit -m "feat: OpenAIRealtimeHandler targeting gpt-realtime-2.1 (D-002, D-003)"; git status --short` (clean).

---

### Task 6: Configuration — env surface, backend selection, tracking-on-startup

**Files:**
- Modify: `reachy_companion/src/reachy_companion/config.py`, `reachy_companion/src/reachy_companion/main.py`
- Create: `reachy_companion/.env.example`
- Test: `reachy_companion/tests/test_config.py`

**Interfaces:**
- Consumes: `OpenAIRealtimeHandler` (Task 5).
- Produces: `main.py`'s **`build_handler()` factory** (`main.py:167-184` — NOT `console.py`; Codex finding 8) constructs `OpenAIRealtimeHandler`; `config.REALTIME_TRANSCRIPTION_LANGUAGE` defaults `"zh"`; **head tracking enabled once at startup** (Codex finding 11 / US-02): after `movement_manager.start()` (`main.py:287-291`), issue the same enable call the `head_tracking` tool makes (copy the exact call from `tools/head_tracking.py:10-35` — it routes `set_head_tracking` through the movement manager, which calls `robot.start_head_tracking(weight=1.0)` at `moves.py:370-382`).

- [ ] **Step 1: Write the failing tests**

`reachy_companion/tests/test_config.py`:
```python
import importlib
import inspect


def test_transcription_language_defaults_to_zh(monkeypatch):
    monkeypatch.delenv("REALTIME_TRANSCRIPTION_LANGUAGE", raising=False)
    import reachy_companion.config as c
    importlib.reload(c)
    assert c.config.REALTIME_TRANSCRIPTION_LANGUAGE == "zh"


def test_main_builds_openai_handler_and_enables_tracking():
    import reachy_companion.main as main
    src = inspect.getsource(main)
    assert "OpenAIRealtimeHandler" in src
    assert "HuggingFaceRealtimeHandler(" not in src
    assert "set_head_tracking" in src or "start_head_tracking" in src
```

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement** — `main.py`: swap handler class inside `build_handler()`; add the tracking-enable call right after `movement_manager.start()`, before the conversation loop starts. (The `zh` config default was already flipped in Task 5 — the Step 1 test is its regression guard.) Write `reachy_companion/.env.example`:

```ini
# --- OpenAI realtime (required) ---
OPENAI_API_KEY=
# --- Turn handling (D-003) ---
REALTIME_TRANSCRIPTION_LANGUAGE=zh
REALTIME_VAD_TYPE=server_vad          # server_vad | semantic_vad
REALTIME_VAD_THRESHOLD=0.5
REALTIME_VAD_PREFIX_PADDING_MS=300
REALTIME_VAD_SILENCE_DURATION_MS=800
REALTIME_VAD_EAGERNESS=auto           # semantic_vad only
# --- MCP (Task 12) ---
NOTION_MCP_URL=
NOTION_MCP_TOKEN=
# --- Home Assistant (Task 13) ---
HA_URL=
HA_TOKEN=
# JSON map of spoken friendly names -> HA entity ids; the ONLY entities the
# model may target (Task 13), e.g. {"客厅的灯": "light.living_room"}
HA_ENTITIES=
# --- Web search fallback (Task 11 Step 2 only; leave empty otherwise) ---
TAVILY_API_KEY=
```

- [ ] **Step 4: Run full suite** — `.venv\Scripts\python -m pytest reachy_companion\tests -v` → all pass.

- [ ] **Step 5: Commit** — `git add reachy_companion\src\reachy_companion\config.py reachy_companion\src\reachy_companion\main.py reachy_companion\.env.example reachy_companion\tests\test_config.py; git commit -m "feat: OpenAI backend wiring, zh default, tracking on startup"; git status --short` (clean).

---

### Task 7: Chinese-first companion profile (the locked profile)

**Files:**
- Modify: `reachy_companion/profiles/<LOCKED>/profile.md` (app-root profiles dir; created minimal in Task 1 Step 5)
- Test: `reachy_companion/tests/test_profile.py`

**Interfaces:**
- Consumes: profile parser (`profile_store.py:70-123`); `LOCKED_PROFILE` constant (Task 1 Step 6 printed it).
- Produces: the locked profile with Chinese instructions and the full `default_tools` set every demo runs under.

- [ ] **Step 1: Write the failing test**

`reachy_companion/tests/test_profile.py`:
```python
from reachy_companion.config import LOCKED_PROFILE
# Loader: use the same function prompts.py:29-52 uses to resolve profiles —
# read the scaffolded profile_store.py/prompts.py and import that exact
# function here; do NOT write a new parser.
from reachy_companion.profile_store import load_profiles  # adjust name to match source


def test_locked_profile_is_chinese_companion():
    profiles = load_profiles()
    p = profiles[LOCKED_PROFILE]
    assert "中文" in p.instructions
    for tool in ("camera", "play_emotion", "head_tracking"):
        assert tool in p.default_tools
```

- [ ] **Step 2: Run to verify failure** (placeholder profile has no 中文).

- [ ] **Step 3: Write the full profile** — replace the placeholder `profile.md`:

```markdown
+++
schema_version = 1
default_tools = [
  "camera",
  "play_emotion",
  "dance",
  "stop_dance",
  "stop_emotion",
  "move_head",
  "head_tracking",
  "sweep_look",
  "<SEARCH_TOOL_NAME>",
]
voice = "cedar"
# greeting is injected as a synthetic USER turn (huggingface_realtime.py:454-482),
# so it must be an instruction TO Reachy, not words spoken AS Reachy:
greeting = "用一句简短自然的中文主动问候用户，并简单介绍你自己是 Reachy。"
+++

你是 Reachy，一个有实体的桌面机器人伙伴。默认使用自然、口语化的中文交流；
如果对方用其他语言，就跟随对方的语言。

行为准则：
- 回答简短自然，像面对面聊天，不要长篇大论。
- 对方说到值得庆祝或情绪明显的事情时，用 play_emotion 做出合适的肢体反应。
- 被问到眼前的东西时，用 camera 工具先看再回答。
- 涉及今天的新闻、天气、时事等需要最新信息的问题时，直接调用搜索工具查证后回答，不要凭记忆猜测。
- 不确定就说不确定。
```
`<SEARCH_TOOL_NAME>`: copy the exact namespaced search-tool name from the scaffolded default profile's front matter (`profiles/default/profile.md:16` in the reference clone; pattern `pollen_robotics_reachy_mini_search_tool__…`). The test's tool list stays as-is (checks only local tools).

- [ ] **Step 4: Run to verify pass.**

- [ ] **Step 5: Commit** — `git add reachy_companion\profiles reachy_companion\tests\test_profile.py; git commit -m "feat: Chinese-first companion locked profile"; git status --short` (clean).

---

### Task 8: End-to-end conversation smoke on mockup-sim (Demo 1 rehearsal)

**Files:**
- Create: `scripts/run_app_dev.ps1`
- Modify: `feature_list.json` (DEMO-1)

**Interfaces:**
- Consumes: Tasks 1–7; real `OPENAI_API_KEY` in `reachy_companion/.env` (operator supplies).
- Produces: dev evidence for DEMO-1 (Chinese reply, barge-in, ~1 s pause survives).

- [ ] **Step 1: Write the launcher** — config loads `.env` via `find_dotenv(usecwd=True)` (upward from CWD — `config.py:297-305`), so the launcher **must run from the app directory** (Codex finding 9):

```powershell
# Runs the companion app against the local mockup-sim daemon (start dev_daemon.ps1 first).
# CWD must be the app dir so find_dotenv picks up reachy_companion\.env.
Set-Location "$PSScriptRoot\..\reachy_companion"
& "..\\.venv\Scripts\python" -m reachy_companion.main
```
(The locked profile is active automatically — `LOCKED_PROFILE` overrides `REACHY_MINI_CUSTOM_PROFILE`, `config.py:336`. Verify the module entry matches the scaffolded `pyproject.toml` console script; adjust `-m` target to it.)

- [ ] **Step 2: Manual verification run** — daemon running, `.env` populated. Speak Chinese; verify: (a) spoken Chinese reply; (b) barge-in stops audio (log `speech_started` + queue clear — inherited `:744-752`); (c) a natural ~1 s mid-sentence pause does NOT trigger a response. If (c) fails: raise `REALTIME_VAD_SILENCE_DURATION_MS` (try 1000–1200) or switch `REALTIME_VAD_TYPE=semantic_vad` with `REALTIME_VAD_EAGERNESS=low` — record the winning config in DECISIONS.md under D-003.

- [ ] **Step 3: Record evidence** — `feature_list.json` DEMO-1 → `state: "dev-verified"`, evidence = transcript/log excerpt + final VAD values.

- [ ] **Step 4: Commit** — `git add scripts\run_app_dev.ps1 feature_list.json DECISIONS.md; git commit -m "feat: dev-run launcher; DEMO-1 dev-verified"; git status --short` (clean).

---

### Task 9: Expression + continuous tracking verification (Demo 2 + US-02 rehearsal)

**Files:**
- Create: `scripts/preload_assets.py`
- Modify: `feature_list.json` (DEMO-2, US-02)

**Interfaces:**
- Consumes: scaffolded `tools/play_emotion.py`, `moves.py`, SDK daemon tracking + wobbler (D-007); tracking-on-startup from Task 6. No new robot code (reuse-first).

- [ ] **Step 1: Write the preloader** (cold HF cache = visible stall; YuNet is a separate pinned download — Codex finding 12):

`scripts/preload_assets.py`:
```python
"""Warm the HF caches the demos rely on (emotion clips + YuNet face model)."""
from huggingface_hub import hf_hub_download
from reachy_mini.motion.recorded_move import RecordedMoves

# Face-detection model (daemon side). Mirror repo/file/revision pinned in
# reference/reachy_mini/src/reachy_mini/vision/face_detector.py:11-14,67.
hf_hub_download("pollen-robotics/face_detection_yunet_2026may",
                "face_detection_yunet_2026may.onnx")
print("cached: YuNet face model")

lib = RecordedMoves("pollen-robotics/reachy-mini-emotions-library")
for name in ("welcoming2", "grateful1", "loving1", "surprised1", "sad1"):
    lib.get(name)
    print("cached:", name)
print("done")
```
If `face_detector.py` pins a `revision=`, pass the same one. Run: `.venv\Scripts\python scripts\preload_assets.py` → 6 `cached:` lines.

- [ ] **Step 2: US-02 check (no-tool tracking)** — launch the app; verify tracking is active immediately (mockup-sim: `GET http://127.0.0.1:8000/api/media/tracking/face` returns a target when a face is in the webcam) and that the log shows NO `head_tracking` tool call. Record US-02 `dev-verified`.

- [ ] **Step 3: Emotion check** — say "我升职了！"; verify `play_emotion` tool call with an excited/happy intent while breathing/tracking resumes after. Record DEMO-2 `dev-verified`.

- [ ] **Step 4: Commit** — `git add scripts\preload_assets.py feature_list.json; git commit -m "feat: asset preloader (YuNet + emotions); DEMO-2/US-02 dev-verified"; git status --short` (clean).

---

### Task 10: Vision Q&A (Demo 3 rehearsal)

**Files:**
- Modify: `feature_list.json` (DEMO-3)

**Interfaces:**
- Consumes: scaffolded `tools/camera.py` → `get_frame_jpeg()`; inherited image-injection (`huggingface_realtime.py:645-678`); local webcam via mockup-sim.

- [ ] **Step 1: Manual dev run** — hold an object to the webcam, ask "我拿的是什么？". Verify logs: `camera` tool call → `image_attached: True` → separate `input_image` item → correct spoken answer.
- [ ] **Step 2: Contingency** — if gpt-realtime-2.1 rejects the image item, capture the exact API error, compare against the recovered handler's image shape (Task 3), fix in `openai_realtime.py` with a unit test pinning the corrected item shape.
- [ ] **Step 3: Record evidence** (DEMO-3 `dev-verified`, 3 objects), commit — `git add feature_list.json` (plus `reachy_companion\src\reachy_companion\openai_realtime.py` and the new test if the Step 2 contingency ran); `git commit -m "feat: DEMO-3 dev-verified"; git status --short` (clean).

---

### Task 11: Automatic web search (Demo 4 rehearsal) — D-006

**Files:**
- Modify: `feature_list.json` (DEMO-4)
- Contingency create: `reachy_companion/src/reachy_companion/tools/web_search.py` + `reachy_companion/tests/test_web_search.py`

**Interfaces:**
- Consumes: preinstalled Space search tool (`tool_spaces.py:50-71`), enabled in the locked profile (Task 7).

- [ ] **Step 1: Manual dev run** — ask "今天英伟达有什么新闻？" (no mention of searching). Verify: search tool call in logs → current answer.
- [ ] **Step 2 (only if the Space route fails/too slow — record why first):** implement the direct fallback tool:

`reachy_companion/tests/test_web_search.py`:
```python
import pytest

from reachy_companion.tools.web_search import WebSearch


def test_schema_declares_query():
    assert WebSearch.name == "web_search"
    assert "query" in WebSearch.parameters_schema["properties"]


@pytest.mark.asyncio
async def test_returns_results_and_sends_bearer_auth(monkeypatch):
    seen = {}

    async def fake_post(self, url, json=None, headers=None, **kw):
        seen["headers"] = headers
        class R:
            status_code = 200
            def json(self):
                return {"results": [{"title": "t", "url": "u", "content": "c"}]}
            def raise_for_status(self):
                return None
        return R()
    import httpx
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    monkeypatch.setenv("TAVILY_API_KEY", "test")
    out = await WebSearch()(deps=None, query="nvidia news")
    assert out["results"][0]["title"] == "t"
    assert seen["headers"]["Authorization"] == "Bearer test"  # Tavily requires bearer auth
```

`reachy_companion/src/reachy_companion/tools/web_search.py`:
```python
"""Direct web search fallback (D-006). Filename must equal Tool.name."""
import os
from typing import Any, Dict

import httpx

from .core_tools import Tool


class WebSearch(Tool):
    name = "web_search"
    description = (
        "Search the web for current information. Call this whenever the user "
        "asks about news, weather, prices, or anything happening now."
    )
    parameters_schema = {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "search query"}},
        "required": ["query"],
    }

    async def __call__(self, deps, **kwargs) -> Dict[str, Any]:
        query = kwargs["query"]
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(
                "https://api.tavily.com/search",
                json={"query": query, "max_results": 5, "search_depth": "basic"},
                headers={"Authorization": f"Bearer {os.environ['TAVILY_API_KEY']}"},
            )
            r.raise_for_status()
            data = r.json()
        return {"results": [
            {"title": x["title"], "url": x["url"], "content": x["content"][:400]}
            for x in data.get("results", [])
        ]}
```
Register: add `"web_search"` to the locked profile's `default_tools`, remove the Space tool line. Set `TAVILY_API_KEY` in `reachy_companion/.env` (already listed in `.env.example`); the tool fails fast via `os.environ[...]` if unset.

- [ ] **Step 3: Record evidence** (DEMO-4 `dev-verified` + route used), commit — `git add feature_list.json` (plus, if Step 2 ran: `reachy_companion\src\reachy_companion\tools\web_search.py reachy_companion\tests\test_web_search.py reachy_companion\profiles`); `git commit -m "feat: DEMO-4 dev-verified"; git status --short` (clean).

---

### Task 12: MCP registration + Notion (US-07) — D-004

**Files:**
- Create: `reachy_companion/src/reachy_companion/mcp_servers.py`
- Modify: `reachy_companion/src/reachy_companion/tools/core_tools.py` (persistent extra-tools seam), `reachy_companion/src/reachy_companion/main.py` (startup registration call)
- Test: `reachy_companion/tests/test_mcp_servers.py`

**Interfaces:**
- Consumes: `RemoteMcpServerConfig`, `RemoteMcpToolClient` (`mcp_client.py:168,271`: `list_tool_specs()` `:279`, spec objects carrying `remote_name`/`namespaced_name`/`description`/`parameters_schema`), `RemoteMcpTool` (`core_tools.py:100-139`).
- Produces: (a) in `core_tools.py`, a **persistent seam**: module dict `EXTRA_TOOLS: dict[str, Tool]` + `register_extra_tool(tool) -> None`, merged into the registry INSIDE `initialize_tools()` after its rebuild — required because `initialize_tools()` reconstructs `ALL_TOOLS` and would wipe ad-hoc registrations (Codex R2-3; `core_tools.py:399-435`), and `get_tool_specs()`/`get_tools()` read from that registry (`:438,:446`); (b) `load_mcp_servers() -> list[RemoteMcpServerConfig]` (env-driven); (c) `async register_mcp_tools() -> list[str]` that discovers via `list_tool_specs()`, wraps in `RemoteMcpTool` (constructed exactly as `_resolve_remote_tools` does — `core_tools.py:322-364`), and registers through the seam. `main.py` awaits (c) BEFORE `initialize_tools()` runs (`main.py:281`); the seam makes ordering robust either way.

- [ ] **Step 1: Write the failing tests**

`reachy_companion/tests/test_mcp_servers.py`:
```python
import pytest

from reachy_companion.mcp_servers import load_mcp_servers, register_mcp_tools


def test_empty_env_yields_no_servers(monkeypatch):
    monkeypatch.delenv("NOTION_MCP_URL", raising=False)
    assert load_mcp_servers() == []


def test_notion_from_env(monkeypatch):
    monkeypatch.setenv("NOTION_MCP_URL", "https://mcp.notion.com/mcp")
    monkeypatch.setenv("NOTION_MCP_TOKEN", "secret")
    (srv,) = load_mcp_servers()
    assert srv.alias == "notion"
    assert srv.headers["Authorization"] == "Bearer secret"


def test_invalid_url_rejected(monkeypatch):
    monkeypatch.setenv("NOTION_MCP_URL", "http://not-https.example.com/mcp")
    monkeypatch.setenv("NOTION_MCP_TOKEN", "x")
    with pytest.raises(ValueError):
        load_mcp_servers()


@pytest.mark.asyncio
async def test_register_discovers_and_registers(monkeypatch):
    monkeypatch.setenv("NOTION_MCP_URL", "https://mcp.notion.com/mcp")
    monkeypatch.setenv("NOTION_MCP_TOKEN", "secret")

    class FakeSpec:  # mirror the real spec fields used by RemoteMcpTool
        remote_name = "search_pages"
        namespaced_name = "notion__search_pages"
        description = "search notion"
        parameters_schema = {"type": "object", "properties": {}}

    class FakeClient:
        def __init__(self, cfg):
            pass
        async def list_tool_specs(self):
            return [FakeSpec()]

    import reachy_companion.mcp_servers as m
    monkeypatch.setattr(m, "RemoteMcpToolClient", FakeClient)
    names = await register_mcp_tools()
    assert names == ["notion__search_pages"]

    # The seam must SURVIVE a registry rebuild (initialize_tools reconstructs
    # ALL_TOOLS — core_tools.py:399-435):
    from reachy_companion.tools.core_tools import get_tool_specs, get_tools, initialize_tools
    initialize_tools(force=True)
    assert any(t.name == "notion__search_pages" for t in get_tools())
    assert any(s["name"] == "notion__search_pages" for s in get_tool_specs())
    # (adjust the spec-shape assertion to get_tool_specs()' real return type)


@pytest.mark.asyncio
async def test_discovery_failure_degrades_instead_of_raising(monkeypatch):
    # A dead/unauthorized MCP server must not prevent app startup (Codex R3-4).
    monkeypatch.setenv("NOTION_MCP_URL", "https://mcp.notion.com/mcp")
    monkeypatch.setenv("NOTION_MCP_TOKEN", "bad")

    class FailingClient:
        def __init__(self, cfg):
            pass
        async def list_tool_specs(self):
            raise RuntimeError("401 unauthorized")

    async def _no_sleep(_seconds):
        return None

    import reachy_companion.mcp_servers as m
    monkeypatch.setattr(m, "RemoteMcpToolClient", FailingClient)
    monkeypatch.setattr("asyncio.sleep", _no_sleep)
    names = await register_mcp_tools()
    assert names == []  # skipped, not raised
```

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement**

`reachy_companion/src/reachy_companion/mcp_servers.py` — shape (match the real constructor fields/registration mechanism from the scaffolded source; `RemoteMcpServerConfig` may require `request_timeout_s`/`tool_timeout_s` — pass its defaults explicitly):
```python
"""Generic remote-MCP registration (D-004).

Bypasses the HF-Space-locked installer; reuses RemoteMcpToolClient and
RemoteMcpTool unchanged. Called once at startup before session config.
"""
import os

from .mcp_client import RemoteMcpServerConfig, RemoteMcpToolClient, validate_http_mcp_url


def load_mcp_servers() -> list[RemoteMcpServerConfig]:
    servers: list[RemoteMcpServerConfig] = []
    url = (os.getenv("NOTION_MCP_URL") or "").strip()
    if url:
        validate_http_mcp_url(url)  # any https URL; raises ValueError otherwise
        token = os.environ["NOTION_MCP_TOKEN"]
        servers.append(RemoteMcpServerConfig(
            alias="notion", url=url,
            headers={"Authorization": f"Bearer {token}"},
        ))
    return servers


async def register_mcp_tools() -> list[str]:
    """Discover tools on each configured server and register them through the
    persistent seam. NEVER raises: a failing MCP server is retried (bounded),
    then logged and skipped — the conversation app must start without MCP
    (Codex R3-4; live discovery raises on auth/transport errors,
    mcp_client.py:279-289). Returns namespaced tool names."""
    import asyncio
    import logging

    from .tools.core_tools import RemoteMcpTool, register_extra_tool

    logger = logging.getLogger(__name__)
    names: list[str] = []
    for cfg in load_mcp_servers():
        client = RemoteMcpToolClient(cfg)
        specs = None
        for attempt in (1, 2):
            try:
                specs = await client.list_tool_specs()
                break
            except Exception as e:  # auth/transport — degrade, don't die
                logger.warning("MCP %s discovery attempt %d failed: %s", cfg.alias, attempt, e)
                if attempt == 1:
                    await asyncio.sleep(2.0)
        if specs is None:
            logger.error("MCP server %s disabled for this session", cfg.alias)
            continue
        for spec in specs:
            tool = RemoteMcpTool(...)  # construct with the same args
            # _resolve_remote_tools uses (core_tools.py:322-364) — copy them.
            register_extra_tool(tool)
            names.append(spec.namespaced_name)
    return names
```
The seam in `core_tools.py` (marked `# reachy_companion: persistent extra tools (D-004)`):
```python
EXTRA_TOOLS: dict[str, "Tool"] = {}


def register_extra_tool(tool) -> None:
    if tool.name in EXTRA_TOOLS:
        raise ValueError(f"duplicate extra tool: {tool.name}")
    EXTRA_TOOLS[tool.name] = tool
```
plus ONE marked merge line inside `initialize_tools()` immediately after it finishes rebuilding the registry (`:399-435`): merge `EXTRA_TOOLS` into the same structure `get_tools()`/`get_tool_specs()` read (`:438,:446`), reusing the existing duplicate-name guard (`:268-278`) semantics. In `main.py`: `mcp_tool_names = asyncio.run(register_mcp_tools())` (or await inside the existing loop) BEFORE the line that calls `initialize_tools()` (`main.py:281`); log the returned names as evidence. Extra tools are active regardless of profile `default_tools` (the seam adds them to the registry the session reads) — verify this against how the enabled-tool set is computed and, if the profile list gates the session tools, append `mcp_tool_names` there too, at the single site where profile tool names are collected.

- [ ] **Step 4: Run to verify pass** (unit).

- [ ] **Step 5: Integration — with an explicit auth contingency** (Codex R3-2: hosted `mcp.notion.com` expects user OAuth/PKCE; a static token may be rejected — do NOT build an OAuth flow, that is PRD Mistake 4 overbuild):
  1. **Attempt A:** `NOTION_MCP_URL=https://mcp.notion.com/mcp` + `NOTION_MCP_TOKEN=<Notion internal-integration secret>` as bearer. If discovery succeeds, done.
  2. **Attempt B (fallback):** run the official open-source Notion MCP server on the LAN with the static token and streamable HTTP (current package: `npx @notionhq/notion-mcp-server --transport http --port 3333` with `NOTION_TOKEN` env — verify exact flag names against its README at execution time), then `NOTION_MCP_URL=http://<lan-host>:3333/mcp`. Note: `validate_http_mcp_url` accepts http only on localhost (`mcp_client.py:75-86`) — run it on the same host as the app, or relax our `load_mcp_servers` validation for RFC1918 hosts in that step.
  3. Record which route worked (and why) in DECISIONS.md under D-004.
  Then dev-run: "查一下我 Notion 里 Magic Mirror 项目的最新状态" → `notion__*` tool call in logs + sensible summary. Record US-07 evidence.

- [ ] **Step 6: Commit** — `git add reachy_companion\src\reachy_companion\mcp_servers.py reachy_companion\src\reachy_companion\tools\core_tools.py reachy_companion\src\reachy_companion\main.py reachy_companion\tests\test_mcp_servers.py feature_list.json; git commit -m "feat: generic MCP discovery/registration via persistent seam; Notion (D-004)"; git status --short` (clean).

---

### Task 13: Home Control Skill (Demo 5) — D-005

**Files:**
- Create: `reachy_companion/src/reachy_companion/tools/home_control.py`
- Test: `reachy_companion/tests/test_home_control.py`
- Modify: locked profile `profile.md` (add `"home_control"`), `reachy_companion/tests/test_profile.py` (expected list)

**Interfaces:**
- Consumes: `Tool` ABC (`core_tools.py:57-90`); discovery by filename == `Tool.name` (`core_tools.py:367-396`).
- Produces: tool `home_control`; HA REST `POST {HA_URL}/api/services/{domain}/{action}` with `{"entity_id": …}`.

- [ ] **Step 1: Write the failing tests**

`reachy_companion/tests/test_home_control.py`:
```python
import pytest

from reachy_companion.tools.home_control import HomeControl

ENTITIES = '{"客厅的灯": "light.living_room", "书房的灯": "light.study"}'


@pytest.fixture(autouse=True)
def ha_env(monkeypatch):
    monkeypatch.setenv("HA_URL", "http://homeassistant.local:8123")
    monkeypatch.setenv("HA_TOKEN", "tok")
    monkeypatch.setenv("HA_ENTITIES", ENTITIES)


def test_tool_contract_enumerates_configured_devices():
    tool = HomeControl()  # schema/description computed at construction from HA_ENTITIES
    props = tool.parameters_schema["properties"]
    assert HomeControl.name == "home_control"
    assert set(tool.parameters_schema["required"]) == {"action", "target"}
    assert props["action"]["enum"] == ["turn_on", "turn_off", "toggle"]
    assert set(props["target"]["enum"]) == {"客厅的灯", "书房的灯"}
    assert "客厅的灯" in tool.description  # model sees the real device names


@pytest.mark.asyncio
async def test_friendly_name_resolves_to_entity_and_calls_ha(monkeypatch):
    calls = {}

    async def fake_post(self, url, json=None, headers=None, **kw):
        calls["url"], calls["json"], calls["headers"] = url, json, headers
        class R:
            status_code = 200
            def raise_for_status(self):
                return None
            def json(self):
                return []
        return R()

    import httpx
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    out = await HomeControl()(deps=None, action="turn_on", target="客厅的灯")
    assert calls["url"] == "http://homeassistant.local:8123/api/services/light/turn_on"
    assert calls["json"] == {"entity_id": "light.living_room"}
    assert calls["headers"]["Authorization"] == "Bearer tok"
    assert out["ok"] is True


@pytest.mark.asyncio
async def test_unknown_target_reports_known_devices():
    out = await HomeControl()(deps=None, action="turn_on", target="车库门")
    assert out["ok"] is False
    assert "客厅的灯" in out["known_devices"]


@pytest.mark.asyncio
async def test_ha_error_is_reported_not_raised(monkeypatch):
    async def fake_post(self, url, **kw):
        import httpx
        raise httpx.ConnectError("no route")
    import httpx
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    out = await HomeControl()(deps=None, action="turn_off", target="客厅的灯")
    assert out["ok"] is False and "no route" in out["error"]
```

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement**

`reachy_companion/src/reachy_companion/tools/home_control.py`:
```python
"""Home Control Skill via Home Assistant REST (D-005). Filename == Tool.name.

The model never invents entity ids: HA_ENTITIES (JSON, spoken-name -> entity_id)
is the allowlist; schema enum + description are built from it at construction
time (initialize_tools constructs tools after .env is loaded).
"""
import json
import os
from typing import Any, Dict

import httpx

from .core_tools import Tool


def _entities() -> Dict[str, str]:
    raw = (os.getenv("HA_ENTITIES") or "").strip()
    return json.loads(raw) if raw else {}


class HomeControl(Tool):
    name = "home_control"
    description = "Control a smart-home device via Home Assistant."  # replaced in __init__
    parameters_schema: Dict[str, Any] = {}  # replaced in __init__

    def __init__(self) -> None:
        devices = _entities()
        names = sorted(devices)
        self.description = (
            "Control a smart-home device via Home Assistant (on/off/toggle). "
            "Use when the user asks to control something in the house. "
            f"Known devices: {', '.join(names) if names else '(none configured)'}."
        )
        self.parameters_schema = {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["turn_on", "turn_off", "toggle"]},
                "target": {
                    "type": "string",
                    "enum": names,
                    "description": "The device to control, by its known name",
                },
            },
            "required": ["action", "target"],
        }

    async def __call__(self, deps, **kwargs) -> Dict[str, Any]:
        action, target = kwargs["action"], kwargs["target"]
        devices = _entities()
        entity_id = devices.get(target)
        if entity_id is None:
            return {"ok": False, "error": f"unknown device: {target}",
                    "known_devices": sorted(devices)}
        base = os.environ["HA_URL"].rstrip("/")
        domain = entity_id.split(".", 1)[0]
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                r = await client.post(
                    f"{base}/api/services/{domain}/{action}",
                    json={"entity_id": entity_id},
                    headers={"Authorization": f"Bearer {os.environ['HA_TOKEN']}"},
                )
                r.raise_for_status()
        except httpx.HTTPError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "action": action, "target": target, "entity_id": entity_id}
```
If the `Tool` base/spec conversion reads `description`/`parameters_schema` from the class rather than the instance (check `to_realtime_tools_config`, `huggingface_realtime.py:76-88`, and `get_tool_specs`), adapt so the instance attrs are what the spec uses — instance attributes shadow class attributes for plain reads, but verify the spec builder isn't using `type(tool).description`. Add `"home_control"` to the locked profile `default_tools`; update `test_profile.py` expected tools.

- [ ] **Step 4: Run full suite.** **Step 5: Integration** — real `HA_URL`/`HA_TOKEN`, and `HA_ENTITIES` mapping the actual demo device (e.g. `{"客厅的灯": "<real entity_id from the operator's HA>"}`). Say exactly "打开客厅的灯" → the mapped light changes state. Record DEMO-5 `dev-verified` with the mapping used.

- [ ] **Step 6: Commit** — `git add reachy_companion\src\reachy_companion\tools\home_control.py reachy_companion\tests\test_home_control.py reachy_companion\profiles reachy_companion\tests\test_profile.py feature_list.json; git commit -m "feat: home_control Skill via Home Assistant REST (D-005)"; git status --short` (clean).

---

### Task 14: Extension pattern documentation (US-09)

**Files:**
- Create: `docs/adding-a-skill.md`
- Modify: `feature_list.json` (US-09)

- [ ] **Step 1: Write `docs/adding-a-skill.md`** using home_control as the worked example: (1) `src/reachy_companion/tools/<name>.py`, filename == `Tool.name`, subclass `Tool` (contract: `name`, `description`, `parameters_schema`, `async __call__(deps, **kwargs) -> dict`, optional `needs_response=False`); (2) add `<name>` to the locked profile `default_tools`; (3) restart. Include the discovery mechanism (`core_tools.py:367-396`), error convention (return `{"ok": False, "error": …}`, never raise), and the async/no-blocking rule (BackgroundToolManager).
- [ ] **Step 2: Verify against reality** — every documented step must match what Task 13 actually required; add any extra step Task 13 needed.
- [ ] **Step 3: Record US-09 evidence, commit** — `git add docs\adding-a-skill.md feature_list.json; git commit -m "docs: adding-a-skill extension guide (US-09)"; git status --short` (clean).

---

### Task 15: On-robot deployment and the five-demo verification gate

**Files:**
- Modify: `feature_list.json` (all items → `passing` with on-robot evidence), `progress.md`, `DECISIONS.md` (D-009 deployment route + final VAD values)

**Interfaces:**
- Consumes: physical Reachy Mini Wireless on the LAN; all prior tasks.

- [ ] **Step 1: Build and verify the wheel** (Codex finding 15 — no prior task built one):

```powershell
uv build ./reachy_companion
# Expected: reachy_companion\dist\reachy_companion-*.whl
.venv\Scripts\python -c "from importlib.metadata import entry_points; eps=[e for e in entry_points(group='reachy_mini_apps')]; print(eps); assert any(e.name=='reachy_companion' for e in eps)"
```
The entry-point check runs against the editable install and must list `reachy_companion` (the daemon discovers apps through this group — `apps/sources/local_common_venv.py:257-268,714-721`).

- [ ] **Step 2: Transfer and install on the robot**

```powershell
scp reachy_companion\dist\reachy_companion-*.whl pollen@reachy-mini.local:/tmp/
ssh pollen@reachy-mini.local "/venvs/apps_venv/bin/python -m pip install /tmp/reachy_companion-*.whl"
```
(Adjust user/host per the robot's actual SSH access; if `uv` exists on the Pi prefer `uv pip install --python /venvs/apps_venv/bin/python …`. Fallback route if direct install misbehaves: push the repo to a private HF Space tagged `reachy_mini_python_app` and install via the dashboard.) Record the exact route that worked in DECISIONS.md as D-009.

- [ ] **Step 3: Verify discovery and start** — `GET http://reachy-mini.local:8000/api/apps/list-available/installed` (route per `daemon/app/routers/apps.py:49-58`; confirm exact path in source) shows `reachy_companion`; `POST /api/apps/start-app/reachy_companion` starts it (or use the dashboard).

- [ ] **Step 4: Preload on-device** — the preloader is a repo script, not part of the wheel: `scp scripts/preload_assets.py pollen@reachy-mini.local:/tmp/` then run it on the Pi with the SAME user/HF-cache the daemon and app use (`/venvs/apps_venv/bin/python /tmp/preload_assets.py`) before any demo.

- [ ] **Step 5: Populate the robot-side `.env`** (the app instance path — the dashboard shows it; `main.py:106-114` loads `<instance_path>/.env`) with real keys. Verify SDK↔daemon version match (no skew warning at connect — `reachy_mini.py:410-431`).

- [ ] **Step 6: Run the demos** — DEMO-1…DEMO-5 + US-02 + US-07 per `feature_list.json` verification fields. For each: capture log excerpt (tool calls, VAD events) + result; set `state: "passing"` with evidence. A failing demo gets a NEW feature_list item (never rewrite old evidence).

- [ ] **Step 7: Latency check** — built-in telemetry (`:774-777,845-848`) during Demo 1; record median response latency in progress.md. If slow, tune `reasoning`/`max_output_tokens` in `_get_session_config` first.

- [ ] **Step 8: Close out** — update progress.md (verified state + residual risks); `git add feature_list.json progress.md DECISIONS.md; git commit -m "feat: on-robot verification evidence for PRD §8 demos"; git status --short` (clean).

---

### Task 16: VoiceFX — cute-robot voice filter (D-010; amendment Rev 2, post Codex round 1)

Operator decision: "very cute robotic voice" via a local DSP chain (D-010), not
a TTS pivot. Rev 2 redesign after Codex round 1 killed both external pitch
engines (python-stretch: binding resets state per call; pedalboard: 1 s
priming delay; neither has aarch64 wheels): **no external engine.** Pitch-up
uses the resample-rate trick through the already-shipped stateful `soxr`
(output duration shrinks by the pitch ratio — the classic chipmunk effect,
which IS the cute-robot aesthetic; the tempo side-effect is offset by a
"语速放慢" style line in the locked profile). Ring-mod is pure numpy. Unit
scope is keyless; live tuning at Task 8.

**Files:**
- Create: `reachy_companion/src/reachy_companion/audio/envparse.py` (shared env parsers — breaks the circular import), `reachy_companion/src/reachy_companion/audio/voicefx.py`
- Modify: `reachy_companion/src/reachy_companion/openai_realtime.py` (use shared parsers; construct FX; emit-chain insert; dedicated output-pipeline reset), locked profile `profile.md` (one style line), `reachy_companion/.env.example`
- Test: `reachy_companion/tests/test_envparse.py`, `reachy_companion/tests/test_voicefx.py`, additions to `reachy_companion/tests/test_openai_realtime_config.py` (handler wiring)

**Interfaces:**
- Produces: `envparse.py` with `env_bool(name, default)`, `env_float(name, default, lo=None, hi=None)` (finite-only, range-clamped with warning), `env_int(...)` — `openai_realtime.py`'s existing `_env_float`/`_env_int` move here and are re-exported or call through (existing tests keep passing). `VoiceFX.from_env(rate)` → `.process(chunk: int16 (1,N)) -> int16 (1,N')`, `.reset()`, `.enabled`, plus documented `duration_ratio = 1 / 2**(semitones/12)`.
- Chain in `emit()`: model 24 kHz PCM → VoiceFX (pitch stage: soxr `ResampleStream(in_rate=24000*2**(st/12), out_rate=24000)` fed as-if-higher-rate, i.e. plays N input samples in N/ratio output samples; st=0 → **hard bypass, stage not constructed**; then ring-mod: `y = x*(1-mix) + x*sin(phase)*mix`, phase carried across chunks, zero latency) → existing 24k→16k resample → queue.
- Reset: new `_reset_output_pipeline()` = reset `_output_resampler` AND `_voicefx`; called from `_clear_queue` (barge-in) and from session build. The mic resampler is NOT touched by barge-in (Codex R1-5). `_voicefx` gets a class-level `None` default so `__new__`-constructed test handlers don't `AttributeError` (Codex R1-10).

**Env knobs** (all via `envparse`): `VOICEFX_ENABLED` (bool, default false → the exact pre-task code path, zero-cost identity), `VOICEFX_PITCH_SEMITONES` (float, default 4.0, clamp 0..12), `VOICEFX_RINGMOD_HZ` (float, default 55.0, clamp 0..2000; 0 = off), `VOICEFX_RINGMOD_MIX` (float, default 0.25, clamp 0..1).

- [ ] **Step 1: `envparse.py` first** (move + extend the parsers; RED via a small `tests/test_envparse.py`: bool parsing incl. "true"/"1"/"yes"/invalid→default+warning; float range-clamp + NaN/Inf rejection→default+warning; existing openai_realtime tests stay green after the move).

- [ ] **Step 2: `test_voicefx.py` (RED)** — behavioral contract, engine-free:
  - disabled → `process` returns the SAME object (identity).
  - pitch +4: ONE continuous sine (440 Hz, ≥1.5 s), fed in consecutive odd-sized slices (479/501/1024/137…); concatenated output: dominant FFT frequency within ±1 bin of 440·2^(4/12)=554.37 Hz (window ≥8192, bin-aware bound — excludes +3/+5); duration contract accounts for soxr's PENDING TAIL (Codex R2-1, empirically 535 samples at +4): `VoiceFX` exposes a `pending_delay` property (input-side samples retained by the stream) and the test asserts `len(output) + pending_delay*duration_ratio ≈ sum(inputs) * duration_ratio` within ±16.
  - chunked-vs-whole equivalence: same continuous signal chunked vs single-shot through two fresh instances → outputs directly equal within ≤2 LSB (no trimming needed — both retain the same pending tail; Codex R2-1 probe measured exactly this).
  - ring-mod alone (pitch=0 → stage bypassed, zero latency): chunked == whole bit-near-exact (≤2 LSB), phase-continuity proof (Codex R1-8).
  - reset: process ≥3× engine delay of signal, reset, process again → output equal (stable region, tolerance) to a fresh instance; NEGATIVE control: without reset the outputs must differ (Codex R1-9).
  - env: malformed value → default + warning; out-of-range mix (1.5) → clamped to 1.0 + warning.

- [ ] **Step 3: implement `voicefx.py`** (int16 ↔ float32/32768 at the edges; clip before int16; `duration_ratio` property; `reset()` recreates the pitch stream + zeroes ring phase).

- [ ] **Step 4: wire the handler** — construct in session build; apply in emit before the 24k→16k resample; add `_reset_output_pipeline()` and call from `_clear_queue` + session build (mic resampler untouched by barge-in). Handler-level tests (extend `test_openai_realtime_config.py`, following its existing `__new__` fixture pattern): FX-applied-before-resample order (spy on call sequence), disabled path is the exact pre-task path (identity, no float round-trip), barge-in resets FX + output resampler and NOT the mic resampler, session build resets all three.

- [ ] **Step 5: profile style line** — append to the locked profile instructions: `- 语速放慢一点，吐字清楚（你的声音会被加速，说慢一点正好）。` and update `test_profile.py` if it pins instruction content (it pins only 中文 + tools — verify).

- [ ] **Step 6: GREEN + full suite + gates.** No new dependency should appear in pyproject (soxr already declared).

- [ ] **Step 7: `.env.example`** — the four knobs with one-line comments incl. the duration/tempo note.

- [ ] **Step 8: Commit** — explicit `git add` (envparse.py, voicefx.py, openai_realtime.py, profile.md, .env.example, tests/test_envparse.py, tests/test_voicefx.py, tests/test_openai_realtime_config.py); message `feat: VoiceFX cute-robot voice filter, engine-free (D-010 Rev 2)`.

- [ ] **Step 9 (deferred to Task 8 dev-run): live A/B tuning** — tune semitones/ringmod by ear; record values in D-010; capture recording evidence for feature_list VOICE-1. Escalation stays per D-010: if chipmunk aesthetics fail the "cute" bar, upgrade path is a true duration-preserving engine (requires solving the aarch64 wheel problem first) or cascaded TTS — both KEEP this chain.

## Self-Review (performed at plan time)

- **Spec coverage:** US-01→Tasks 5,6,8; US-02→Task 6 (startup enable) + Task 9 (verify, no-tool); US-03→Task 9; US-04→Task 10; US-05→Task 11; US-06→Tasks 11–13; US-07→Task 12; US-08→Task 13; US-09→Task 14; PRD §8 gate→Task 15. §2 research done (docs/research-*.md). §9 non-goals: no task builds any.
- **Known uncertainty, handled explicitly:** exact `_param` module paths (mirror the scaffolded import block); locked-profile name (read from generated config); `connect_kwargs` injectability (copy-and-mark fallback); `profile_store` loader name (read source); scaffolder interactivity (temp-dir fallback); OpenAI image-item shape (Task 10 contingency); Pi SSH user/route (Task 15 fallback).
- **Type consistency:** `resample_pcm` (Task 4) matches Task 5's two call sites; `HomeControl`/`WebSearch` follow the verified `Tool` contract; `register_mcp_tools() -> list[str]` consumed in `main.py`.

## Review Log (CLAUDE.md Plan Review rule: up to 3 Codex rounds, Claude judges)

**Round 1 — Codex (`nova-auto`, gpt-5.6-sol), 15 findings: 15 accepted (1 with modification), 0 rejected.**

| # | Sev | Disposition |
|---|-----|-------------|
| 1 | blocker | Accepted — Task 1 Step 4 removes nested `.git` (scaffolder `git init`s the app). |
| 2 | blocker | Accepted, verified in source — scaffolder emits `instructions.txt`/`tools.txt`, app requires `profile.md` + exits; Task 1 Step 5 converts; Task 7 targets the locked profile. |
| 3 | blocker | Accepted — Task 1 Step 6 installs dev group / pytest explicitly. |
| 4 | minor | Accepted — Task 3 creates `reference/recovered/` first. |
| 5 | blocker | Accepted — imports switched to `_param` modules, mirroring `huggingface_realtime.py:17-27`. |
| 6 | minor | Accepted — fixture sets `handler.instance_path = None`. |
| 7 | major | Accepted — Task 4 now unconditional; Task 5 resamples both directions inside the handler (play_loop pushes 16 kHz unchanged). |
| 8 | blocker | Accepted — Task 6 targets `main.py` `build_handler()` (not console.py); test inspects main.py. |
| 9 | blocker | Accepted — launcher `Set-Location`s into `reachy_companion/` so `find_dotenv(usecwd=True)` finds the `.env`. |
| 10 | major | Accepted with modification — acceptance aligned to a natural ~1 s pause everywhere (not 2 s); escalation path = higher silence_duration or semantic_vad low-eagerness, recorded under D-003. |
| 11 | major | Accepted — tracking enabled once at startup in `main.py` (Task 6); US-02 added to feature_list; Task 9 verifies without a tool call. |
| 12 | minor | Accepted — preloader downloads YuNet + emotion clips. |
| 13 | blocker | Accepted — Task 12 rewritten: async discovery via `list_tool_specs()` + `RemoteMcpTool` registration + enabled-names wiring; bare server-config append removed. |
| 14 | major | Accepted — all commit steps use explicit `git add` + clean-status check; global constraint added. |
| 15 | major | Accepted — Task 15 now builds/verifies/transfers the wheel, checks entry-point discovery, keeps dashboard fallback (→ D-009). |

**Round 2 — Codex (`nova-auto`, gpt-5.6-sol), 8 findings: 8 accepted, 0 rejected.**

| # | Sev | Disposition |
|---|-----|-------------|
| 1 | blocker | Accepted — locked profile moved to app-root `reachy_companion/profiles/<LOCKED>/profile.md` (runtime resolves root `profiles/`, `config.py:32-45`; wheel packages it, upstream `setup.py:22`); packaging check added to Task 1 Step 5. |
| 2 | major | Accepted — `resample_pcm` uses `axis=-1`; regression test for `(1, 2400) → (1, 1600)` added. |
| 3 | blocker | Accepted — persistent `EXTRA_TOOLS` seam added in `core_tools.py`, merged inside `initialize_tools()` so rebuilds don't wipe MCP tools; test asserts survival across `initialize_tools(force=True)`; registration ordered before `main.py:281`. |
| 4 | major | Accepted — `HA_ENTITIES` JSON allowlist (spoken name → entity_id); schema enum + description built from it at construction; unknown targets report known devices; demo utterance tested against the configured real device. |
| 5 | major | Accepted — `TAVILY_API_KEY` added to secrets contract + `.env.example`; fallback commit stages tool, test, and profile. |
| 6 | minor | Accepted — greeting rewritten as an instruction to Reachy (it is injected as a synthetic user turn, `huggingface_realtime.py:454-482`). |
| 7 | minor | Accepted — `audio/__init__.py` staged in Task 4; Task 10 contingency files staged in its commit. |
| 8 | minor | Accepted — Task 15 uses `/api/apps/list-available/installed` (verify in source) and scp's the preloader to the Pi before running it. |

**Round 3 (final) — Codex (`nova-auto`, gpt-5.6-sol), 4 findings: 4 accepted (1 with modification), 0 rejected.**

| # | Sev | Disposition |
|---|-----|-------------|
| 1 | blocker | Accepted — `zh` config default flip moved into Task 5 (whose test asserts it); Task 6's test is the regression guard. |
| 2 | blocker | Accepted with modification — no OAuth/PKCE build (PRD Mistake 4); Task 12 Step 5 now has an explicit auth contingency: internal-integration bearer first, official self-hosted `notion-mcp-server` (static token, streamable HTTP) as fallback; outcome recorded under D-004. |
| 3 | major | Accepted — Tavily fallback sends `Authorization: Bearer`, test asserts the header. |
| 4 | major | Accepted — `register_mcp_tools` never raises: bounded retry, then log + skip the server so the app starts without MCP; degradation test added. |

**Final verdict (Claude, per the Plan Review rule):** three rounds complete — 27 findings total, 27 accepted (2 with modification), 0 rejected, all folded into Rev 4. Review cap reached; the plan is cleared for execution. Residual risks accepted knowingly: exact `_param` module paths, `RemoteMcpTool` constructor args, and the Notion auth route are resolved at execution time against the scaffolded source, each with an in-plan fallback.
