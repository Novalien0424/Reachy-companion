# Voice Robustness Implementation Plan (mishearing / boot / multi-person)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adopt every feasible recommendation from
`docs/research-realtime-voice-best-practices.md` — model swap to
`gpt-realtime-2.1-mini`, official `wait_for_user`/unclear-audio/language
prompt hardening, transcription migration with keyword biasing, TTS onset
ramp, boot-time turn gating, backchannel-hardened gates with a face-presence
signal, and solo-mode pause-then-decide barge-in with false-interruption
rollback.

**Architecture:** All changes live in our fork's conversation layer
(`huggingface_realtime.py`, `openai_realtime.py`, `prompts.py`, `tools/`) —
the PRD "do build" column. The one robot-facing signal (face presence)
reuses the SDK's existing daemon-side tracker via
`ReachyMini.get_tracked_face(wait=False)` (official module:
`reachy_mini/reachy_mini.py:293-295`, cached 1 Hz `DaemonStatus`, no new
vision code). Every behavior change ships behind an env knob with the old
behavior recoverable, because the on-robot A/Bs (XVF3800 × noise-reduction,
solo barge rework, mini-model quality) can only run live.

**Tech Stack:** Python 3.12, openai SDK (pinned), numpy, pytest, ruff, mypy
strict. No new dependencies.

**Spec:** `docs/research-realtime-voice-best-practices.md` (recommendations
§8 items 1–8) + operator directives: adopt all feasible solutions; switch to
`gpt-realtime-2.1-mini` for cost.

## Global Constraints

- Suite baseline is green: 1211 passed / 31 skipped; ruff + mypy strict must
  stay green after every task. Run from `reachy_companion/`:
  `python -m pytest`, `ruff check .`, `mypy src`.
- Python 3.12 venv (`uv venv --python 3.12`) — 3.11 wedges one realtime test.
- No new/upgraded dependencies.
- Every new env key MUST be documented in `reachy_companion/.env.example`
  (enforced by `test_openai_realtime_config.py::test_env_example_documents_the_new_knobs`
  — extend that test with the new keys).
- New env parsing uses `reachy_companion.audio.envparse` helpers
  (`env_bool`/`env_float`/`env_int`): never raise, warn + fall back, clamp.
- Prompt text additions go in `prompts.py` — NOT in
  `profiles/_reachy_companion_locked_profile/profile.md` — because the
  instance `persona.md` replaces profile instructions wholesale
  (`persona.py:175-190`).
- Old solo barge-in behavior must remain reachable via
  `REALTIME_SOLO_CLIENT_BARGE=0` (full legacy path, not an approximation).
- Commit after every task; message style `feat(realtime): …` /
  `test(realtime): …` matching repo history.
- Never touch `reference/` (absent on this machine anyway) or the robot's
  daemon. No secrets in commits.

## File Structure

| File | Change |
|---|---|
| `src/reachy_companion/openai_realtime.py` | model env fn, transcription upgrade, boot-gate config, onset ramp in `emit()` |
| `src/reachy_companion/huggingface_realtime.py` | backchannel module use, solo barge state machine, boot-gate triggers, gate hardening, face signal, session-start resets, response-start hook |
| `src/reachy_companion/prompts.py` | hardening prompt blocks appended in `get_session_instructions` |
| `src/reachy_companion/tools/wait_for_user.py` | new no-op tool (`needs_response = False`) |
| `src/reachy_companion/audio/backchannel.py` | new: backchannel/min-content classifier (pure function, no I/O) |
| `profiles/_reachy_companion_locked_profile/profile.md` | add `wait_for_user` to `default_tools` (39th tool) |
| `reachy_companion/.env.example` | document all new keys |
| `tests/test_openai_realtime_config.py`, `tests/test_party_mode.py`, `tests/test_prompts_hardening.py` (new), `tests/test_backchannel.py` (new), `tests/test_solo_barge.py` (new), `tests/test_boot_gate.py` (new), `tests/tools/test_wait_for_user.py` (new) | tests |
| `progress.md`, `DECISIONS.md`, `feature_list.json`, `docs/multi-person-investigation.md` | state updates (final task) |

---

### Task 1: Model swap to gpt-realtime-2.1-mini with env override

**Files:**
- Modify: `reachy_companion/src/reachy_companion/openai_realtime.py:46` (MODEL const) and its three use sites (`:277`, `:286`)
- Modify: `reachy_companion/tests/test_openai_realtime_config.py:81` (`test_session_config_targets_gpt_realtime_21`)
- Modify: `reachy_companion/.env.example` (document `REALTIME_MODEL`)

**Interfaces:**
- Produces: `realtime_model() -> str` in `openai_realtime.py` — reads
  `REALTIME_MODEL`, default `"gpt-realtime-2.1-mini"`. Later tasks and tests
  refer to `realtime_model()`; the module-level `MODEL` constant is removed.

- [ ] **Step 1: Write the failing tests** (in `test_openai_realtime_config.py`, replacing `test_session_config_targets_gpt_realtime_21`)

```python
def test_default_model_is_mini(monkeypatch):
    monkeypatch.delenv("REALTIME_MODEL", raising=False)
    assert openai_realtime.realtime_model() == "gpt-realtime-2.1-mini"


def test_model_env_override(monkeypatch):
    monkeypatch.setenv("REALTIME_MODEL", "gpt-realtime-2.1")
    assert openai_realtime.realtime_model() == "gpt-realtime-2.1"


def test_session_config_targets_configured_model(handler, monkeypatch):
    monkeypatch.delenv("REALTIME_MODEL", raising=False)
    cfg = handler._get_session_config(tool_specs=[])
    assert cfg["model"] == "gpt-realtime-2.1-mini"
    assert cfg["audio"]["input"]["transcription"]["language"] == "zh"
```

- [ ] **Step 2: Run to verify failure** — `python -m pytest tests/test_openai_realtime_config.py -k model -v` → FAIL (`realtime_model` not defined).

- [ ] **Step 3: Implement** in `openai_realtime.py` — replace line 46:

```python
_DEFAULT_MODEL = "gpt-realtime-2.1-mini"


def realtime_model() -> str:
    """Realtime model id; REALTIME_MODEL overrides for on-robot A/B (D-023)."""
    return (os.getenv("REALTIME_MODEL") or "").strip() or _DEFAULT_MODEL
```

Replace `MODEL` at `:277` (`self._realtime_connect_query = {"model": realtime_model()}`)
and `:286` (`cfg["model"] = realtime_model()`). Grep the repo for other
`MODEL` imports (tests) and update them.

- [ ] **Step 4: Run** the two test files touching the constant + ruff + mypy → PASS.
- [ ] **Step 5: Document** `REALTIME_MODEL` in `reachy_companion/.env.example` (with the mini/full trade-off, one line) and add the key to `test_env_example_documents_the_new_knobs`.
- [ ] **Step 6: Commit** — `feat(realtime): default to gpt-realtime-2.1-mini, REALTIME_MODEL override`

---

### Task 2: Backchannel classifier module

**Files:**
- Create: `reachy_companion/src/reachy_companion/audio/backchannel.py`
- Test: `reachy_companion/tests/test_backchannel.py`

**Interfaces:**
- Produces: `is_backchannel(text: str) -> bool` and
  `is_substantive(text: str) -> bool` (module `reachy_companion.audio.backchannel`).
  `is_substantive(t)` ≡ `t` has ≥ `REALTIME_MIN_TURN_CHARS` (default 2)
  content characters after stripping AND `not is_backchannel(t)`.
  Consumed by Tasks 6 and 7.

- [ ] **Step 1: Failing tests** (`tests/test_backchannel.py`):

```python
import pytest
from reachy_companion.audio.backchannel import is_backchannel, is_substantive


@pytest.mark.parametrize("text", [
    "嗯", "嗯嗯", "嗯嗯嗯", "對", "對對", "好", "好的", "是", "是喔", "喔",
    "欸", "哦", "唔", "呵", "哈哈", "哈哈哈", "yeah", "ok", "okay",
    "uh-huh", "mm", "hmm", "嗯 嗯", "哈哈！", "好~",
])
def test_backchannels_detected(text):
    assert is_backchannel(text)


@pytest.mark.parametrize("text", [
    "幫我開燈", "瑞奇你可以播歌嗎", "好，那幫我關冷氣",  # content beats a leading 好
    "stop", "四十二是答案嗎",
])
def test_substantive_not_backchannel(text):
    assert not is_backchannel(text)
    assert is_substantive(text)


def test_empty_and_whitespace_are_not_substantive():
    assert not is_substantive("")
    assert not is_substantive("  ")
    assert is_backchannel("")  # nothing said = nothing addressed


def test_min_chars_env(monkeypatch):
    monkeypatch.setenv("REALTIME_MIN_TURN_CHARS", "4")
    assert not is_substantive("開燈")   # 2 chars < 4
    monkeypatch.delenv("REALTIME_MIN_TURN_CHARS")
```

- [ ] **Step 2: Run** → FAIL (module missing).
- [ ] **Step 3: Implement** `audio/backchannel.py`:

```python
"""Backchannel / minimum-content classification for turn gating.

Mandarin backchannels are monosyllabic and tonally ambiguous; no shipped
model classifies them reliably (research doc §3), so this is the field's
standard lexicon+length heuristic. Pure functions, no I/O.
"""

from __future__ import annotations

import os
import re

from reachy_companion.audio.envparse import env_int

# Tokens observed committing as turns in the 2026-08-24 party journal, plus
# the standard EN/ZH backchannel sets from the research doc (§3.2, §6.3).
_BACKCHANNEL_TOKENS = frozenset(
    {
        "嗯", "對", "好", "是", "喔", "欸", "哦", "唔", "呵", "哈",
        "好的", "是喔", "這樣", "真的",
        "yeah", "yep", "ok", "okay", "mm", "hmm", "uh", "huh", "uh-huh",
        "mm-hmm", "right", "sure",
    }
)
# Runs of a single repeated char (嗯嗯嗯, 哈哈哈) collapse to the char.
_REPEAT_RE = re.compile(r"(.)\1+")
_STRIP_RE = re.compile(r"[\s。，、！？!?~～.,;:；：…‥·\-—「」『』()（）\"']+")


def _tokens(text: str) -> list[str]:
    cleaned = _STRIP_RE.sub(" ", text.casefold()).strip()
    return [t for t in cleaned.split(" ") if t]


def is_backchannel(text: str) -> bool:
    """True when the utterance carries no addressable content."""
    tokens = _tokens(text)
    if not tokens:
        return True
    for token in tokens:
        collapsed = _REPEAT_RE.sub(r"\1", token)
        if token in _BACKCHANNEL_TOKENS or collapsed in _BACKCHANNEL_TOKENS:
            continue
        return False
    return True


def is_substantive(text: str) -> bool:
    """True when the utterance is long enough and not pure backchannel."""
    min_chars = env_int("REALTIME_MIN_TURN_CHARS", 2, lo=1)
    content = _STRIP_RE.sub("", text)
    return len(content) >= min_chars and not is_backchannel(text)
```

- [ ] **Step 4: Run** → PASS; ruff + mypy.
- [ ] **Step 5: Document** `REALTIME_MIN_TURN_CHARS` in `.env.example` + extend the env-docs test.
- [ ] **Step 6: Commit** — `feat(audio): backchannel/minimum-content classifier for turn gating`

---

### Task 3: Prompt hardening + wait_for_user tool

**Files:**
- Modify: `reachy_companion/src/reachy_companion/prompts.py:29-52` (`get_session_instructions`)
- Create: `reachy_companion/src/reachy_companion/tools/wait_for_user.py`
- Modify: `reachy_companion/profiles/_reachy_companion_locked_profile/profile.md` (`default_tools` +`"wait_for_user"`)
- Test: `reachy_companion/tests/test_prompts_hardening.py` (new), `reachy_companion/tests/tools/test_wait_for_user.py` (new)

**Interfaces:**
- Produces: `prompts.hardening_block() -> str` (returns `""` when
  `REALTIME_PROMPT_HARDENING=0`); tool name `"wait_for_user"` with
  `needs_response = False`.
- Consumes: `Tool` ABC from `tools/core_tools.py:66-97`; auto-discovery
  requires filename == tool name (`core_tools.py:407`).

- [ ] **Step 1: Failing tests** (`tests/test_prompts_hardening.py`):

```python
import os

from reachy_companion import prompts


def test_hardening_block_appended_to_instructions(monkeypatch, tmp_path):
    monkeypatch.delenv("REALTIME_PROMPT_HARDENING", raising=False)
    text = prompts.get_session_instructions(tmp_path)
    assert "wait_for_user" in text
    assert "聽不清楚" in text          # unclear-audio clarifier
    assert "台灣中文" in text or "台灣國語" in text  # language pin


def test_hardening_survives_persona_override(monkeypatch, tmp_path):
    # persona.md replaces profile instructions wholesale; the block must
    # still be present because it is composed in get_session_instructions.
    (tmp_path / "persona.md").write_text("你是一隻測試機器人。", encoding="utf-8")
    monkeypatch.setenv("PERSONA_FILE", str(tmp_path / "persona.md"))
    from reachy_companion.persona import reset_persona_cache
    reset_persona_cache()
    text = prompts.get_session_instructions(tmp_path)
    assert "你是一隻測試機器人" in text
    assert "wait_for_user" in text


def test_hardening_kill_switch(monkeypatch, tmp_path):
    monkeypatch.setenv("REALTIME_PROMPT_HARDENING", "0")
    assert "wait_for_user" not in prompts.get_session_instructions(tmp_path)
```

(`tests/tools/test_wait_for_user.py`, following `tests/tools/test_head_tracking.py` style):

```python
import asyncio
from unittest.mock import MagicMock

from reachy_companion.tools.wait_for_user import WaitForUser


def test_wait_for_user_is_silent_noop():
    tool = WaitForUser()
    assert tool.name == "wait_for_user"
    assert tool.needs_response is False
    assert tool.parameters_schema["properties"] == {}
    result = asyncio.run(tool(MagicMock()))
    assert result == {"ok": True, "status": "waiting"}


def test_wait_for_user_is_in_the_locked_profile():
    from reachy_companion.profile_store import read_builtin_profile_definition
    # follow whatever accessor test_external_loading.py uses to read the
    # locked profile's default_tools; assert "wait_for_user" is present.
```

(Implementer: locate the accessor used by existing profile tests —
`profile_store.py` exposes the locked profile read used in
`test_external_loading.py` — and finish the second test with it.)

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement the tool** `tools/wait_for_user.py`:

```python
"""No-op tool: the model calls this to end a turn without speaking.

OpenAI's realtime prompting guide ships this exact pattern for silence,
background noise, TV audio, and side conversation — an affirmative action
that ends the turn is far more reliable than asking the model to do
nothing. Every call is a countable journal line.
"""

import logging
from typing import Any, Dict

from reachy_companion.tools.core_tools import Tool, ToolDependencies

logger = logging.getLogger(__name__)


class WaitForUser(Tool):
    """Silently end the turn for non-addressed or unclear audio."""

    needs_response = False

    name = "wait_for_user"
    description = (
        "Call this when the latest audio does not need a spoken response, "
        "such as silence, background noise, music, TV audio, side "
        "conversation, or speech not addressed to the assistant. This tool "
        "helps end the turn without a spoken reply."
    )
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        logger.info("wait_for_user: model chose not to respond")
        return {"ok": True, "status": "waiting"}
```

- [ ] **Step 4: Implement the prompt block** in `prompts.py` — add after `DEFAULT_GREETING_PROMPT`:

```python
_HARDENING_BLOCK = """
## 聲音與回應規則（系統層，優先於角色設定）

### 不需要回應的聲音
如果最新的聲音是：安靜、背景噪音、音樂、電視聲、旁人之間的對話、
或不是對你說的話 — 呼叫 `wait_for_user` 工具，然後保持安靜。
呼叫後不要再說話。不要說「我在這裡」「我沒聽清楚」「慢慢來」。
只有當使用者清楚地對你說話或請你幫忙時才恢復回應。

### 聽不清楚時
- 只回應清楚的語音或文字。
- 聽不清楚時，用一句簡短的台灣中文請對方再說一次（例如「不好意思，
  可以再說一次嗎？」）。同樣的澄清句不要連續說兩次。
- 模糊、吵雜、只有雜音、被切斷、或你不確定對方確切說了什麼 — 都算
  聽不清楚。聽不清楚時：不要猜測、不要推理、不要呼叫其他工具。

### 語言
預設使用台灣中文（台灣國語）。只有在使用者「明確要求換語言」或
「用另一種語言說出完整的請求或問題」時才換語言。
不要因為口音、語助詞、簡短的附和、人名、地址、或夾雜的外語單字
而切換語言。工具回傳的資料、歌名、影像內容，一律用台灣中文回答。
""".strip()


def hardening_block() -> str:
    """Anti-mishearing prompt rules; REALTIME_PROMPT_HARDENING=0 disables."""
    if not env_bool("REALTIME_PROMPT_HARDENING", True):
        return ""
    return _HARDENING_BLOCK
```

and in `get_session_instructions` (`prompts.py:29-52`), compose it exactly
like the memory block — after the persona instructions so persona flavor
stays primary but the rules are last-word:

```python
    block = hardening_block()
    if block:
        instructions = f"{instructions}\n\n{block}"
    memory_prompt = format_memory_for_prompt(instance_path)
    if memory_prompt:
        return f"{memory_prompt}\n\n{instructions}"
    return instructions
```

Import `env_bool` from `reachy_companion.audio.envparse`.

- [ ] **Step 5: Add `"wait_for_user"` to `default_tools`** in
  `profiles/_reachy_companion_locked_profile/profile.md` (39th entry, before
  the trailing search tool to keep that last).
- [ ] **Step 6: Run** new tests + `test_external_loading.py` + full suite → PASS; ruff + mypy.
- [ ] **Step 7: Document** `REALTIME_PROMPT_HARDENING` in `.env.example` + env-docs test.
- [ ] **Step 8: Commit** — `feat(realtime): wait_for_user tool + unclear-audio/language prompt hardening`

---

### Task 4: Transcription upgrade (gpt-transcribe, keywords, fallback)

**Files:**
- Modify: `reachy_companion/src/reachy_companion/openai_realtime.py` (`_get_session_config` at `:283-295`)
- Modify: `reachy_companion/src/reachy_companion/huggingface_realtime.py:1011-1024` (session.update fallback)
- Test: `reachy_companion/tests/test_openai_realtime_config.py`

**Interfaces:**
- Produces: `_transcription() -> dict[str, Any]` in `openai_realtime.py`.
  Keys: `model` (env `REALTIME_TRANSCRIPTION_MODEL`, default
  `"gpt-transcribe"`), `language` (existing
  `config.REALTIME_TRANSCRIPTION_LANGUAGE`), optional `keywords`
  (env `REALTIME_TRANSCRIPTION_KEYWORDS`, comma list; default = the party
  address names via `_party_names()` from `huggingface_realtime`), optional
  `prompt` (env `REALTIME_TRANSCRIPTION_PROMPT`, default
  `"與家用陪伴機器人的台灣中文對話"`).
- Produces: `_legacy_transcription_fallback` behavior — if the initial
  `session.update` fails, one retry with
  `{"model": "gpt-4o-transcribe", "language": ...}` before aborting startup.
- The BASE `huggingface_realtime._get_session_config` keeps
  `gpt-4o-transcribe` untouched (the HF-compat server may not know the new
  model); only the OpenAI subclass upgrades.

- [ ] **Step 1: Failing tests**:

```python
def test_transcription_upgraded_with_keywords(handler, monkeypatch):
    monkeypatch.delenv("REALTIME_TRANSCRIPTION_MODEL", raising=False)
    monkeypatch.delenv("REALTIME_TRANSCRIPTION_KEYWORDS", raising=False)
    cfg = handler._get_session_config(tool_specs=[])
    tr = cfg["audio"]["input"]["transcription"]
    assert tr["model"] == "gpt-transcribe"
    assert tr["language"] == "zh"
    assert "瑞奇" in tr["keywords"] and "reachy" in tr["keywords"]
    assert tr["prompt"]


def test_transcription_keywords_env_override(handler, monkeypatch):
    monkeypatch.setenv("REALTIME_TRANSCRIPTION_KEYWORDS", "客廳, 冷氣")
    tr = handler._get_session_config(tool_specs=[])["audio"]["input"]["transcription"]
    assert tr["keywords"] == ["客廳", "冷氣"]


def test_transcription_model_env_override_drops_new_fields_for_legacy(handler, monkeypatch):
    monkeypatch.setenv("REALTIME_TRANSCRIPTION_MODEL", "gpt-4o-transcribe")
    tr = handler._get_session_config(tool_specs=[])["audio"]["input"]["transcription"]
    assert tr["model"] == "gpt-4o-transcribe"
    assert "keywords" not in tr and "prompt" not in tr  # legacy model, legacy shape
```

Plus an async test in `test_huggingface_realtime.py` style: build the fake
client so the **first** `session.update` raises, assert the second update's
`transcription` is the legacy shape and startup continues (reuse
`_make_fake_realtime_client` with a `captured_update` list and a
side-effecting `update`).

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** in `openai_realtime.py`:

```python
_LEGACY_TRANSCRIBE_MODELS = ("gpt-4o-transcribe", "whisper-1")
_DEFAULT_TRANSCRIBE_MODEL = "gpt-transcribe"
_DEFAULT_TRANSCRIBE_PROMPT = "與家用陪伴機器人的台灣中文對話"


def _transcription() -> dict[str, Any]:
    """Input-transcription config; new-model extras only on new models."""
    model = (os.getenv("REALTIME_TRANSCRIPTION_MODEL") or "").strip() or _DEFAULT_TRANSCRIBE_MODEL
    params: dict[str, Any] = {"model": model, "language": config.REALTIME_TRANSCRIPTION_LANGUAGE}
    if model in _LEGACY_TRANSCRIBE_MODELS:
        return params
    raw_keywords = os.getenv("REALTIME_TRANSCRIPTION_KEYWORDS")
    if raw_keywords is None:
        keywords = [n for n in _party_names()]
    else:
        keywords = [k.strip() for k in raw_keywords.split(",") if k.strip()]
    if keywords:
        params["keywords"] = keywords
    prompt = os.getenv("REALTIME_TRANSCRIPTION_PROMPT")
    prompt = _DEFAULT_TRANSCRIBE_PROMPT if prompt is None else prompt.strip()
    if prompt:
        params["prompt"] = prompt
    return params
```

In `_get_session_config` add
`cfg["audio"]["input"]["transcription"] = cast(Any, _transcription())`
(the SDK TypedDict predates `keywords`; follow the existing
`# type: ignore[typeddict-item]` precedent at `huggingface_realtime.py:397`).
Import `_party_names` from `huggingface_realtime` (already imported by the
subclass module — check the import block; add if missing).

- [ ] **Step 4: Implement the fallback** in `huggingface_realtime.py:1011-1024` — wrap the update:

```python
try:
    await conn.session.update(session=session_config)
except Exception:
    fallback = self._session_config_fallback(session_config)
    if fallback is None:
        logger.exception("Realtime session.update failed; aborting startup")
        raise
    logger.warning("session.update rejected; retrying with legacy transcription shape")
    await conn.session.update(session=fallback)
```

with a base-class method (so the HF handler inherits a no-op):

```python
def _session_config_fallback(
    self, cfg: RealtimeSessionCreateRequestParam
) -> RealtimeSessionCreateRequestParam | None:
    """Subclasses may return a downgraded config to retry a rejected update."""
    return None
```

and the OpenAI override returning a deep-copied config whose transcription
is `{"model": "gpt-4o-transcribe", "language": config.REALTIME_TRANSCRIPTION_LANGUAGE}`
(and returning `None` if the config already used a legacy model — no retry loop).

- [ ] **Step 5: Run** all touched test files + suite → PASS; ruff + mypy.
- [ ] **Step 6: Document** `REALTIME_TRANSCRIPTION_MODEL`, `REALTIME_TRANSCRIPTION_KEYWORDS`, `REALTIME_TRANSCRIPTION_PROMPT` in `.env.example` + env-docs test.
- [ ] **Step 7: Commit** — `feat(realtime): gpt-transcribe with keyword biasing and legacy fallback`

---

### Task 5: TTS onset amplitude ramp

**Files:**
- Modify: `reachy_companion/src/reachy_companion/huggingface_realtime.py` (`response.created` handling at `:1106-1121`; add `_notify_response_started` hook)
- Modify: `reachy_companion/src/reachy_companion/openai_realtime.py` (`emit()` at `:360-385`)
- Test: `reachy_companion/tests/test_openai_realtime_config.py` (emit section, follow `_emit_ready_handler()` at `:448`)

**Interfaces:**
- Produces: base-class hook `def _notify_response_started(self) -> None`
  (no-op in `HuggingFaceRealtimeHandler`), called once per `response.created`
  next to the existing `set_speaking(True)` at `huggingface_realtime.py:1114`.
- Produces: `OpenAIRealtimeHandler._arm_onset_ramp()` — sets
  `self._onset_ramp_remaining` to `int(SAMPLE_RATE * ramp_ms / 1000)`;
  `emit()` scales the first `remaining` samples of outgoing PCM by a linear
  0→1 ramp that continues across chunk boundaries. Env
  `REALTIME_ONSET_RAMP_MS` default `120`, `0` disables. Also re-armed by
  Task 7's rollback-resume.

- [ ] **Step 1: Failing tests**:

```python
@pytest.mark.asyncio
async def test_onset_ramp_scales_first_chunk(monkeypatch):
    monkeypatch.setenv("REALTIME_ONSET_RAMP_MS", "10")  # 240 samples at 24k
    h = _emit_ready_handler()
    h._notify_response_started()
    pcm = np.full(480, 16000, dtype=np.int16)  # 20ms constant tone
    h.output_queue.put_nowait((24000, pcm.reshape(1, -1)))
    rate, out = await h.emit()
    flat = out.reshape(-1)
    assert abs(int(flat[0])) < 500          # starts near silence
    assert int(flat[-1]) != 0               # tail untouched (resampled)


@pytest.mark.asyncio
async def test_onset_ramp_disabled_when_zero(monkeypatch):
    monkeypatch.setenv("REALTIME_ONSET_RAMP_MS", "0")
    h = _emit_ready_handler()
    h._notify_response_started()
    pcm = np.full(480, 16000, dtype=np.int16)
    h.output_queue.put_nowait((24000, pcm.reshape(1, -1)))
    _, out = await h.emit()
    assert abs(int(out.reshape(-1)[0])) > 5000
```

(Adapt the enqueue shape to whatever `_emit_ready_handler()` already uses —
read the existing emit tests at `:459-620` first and copy their conventions,
including VoiceFX-off autouse fixture.)

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** In `huggingface_realtime.py`:
  - base no-op `def _notify_response_started(self) -> None: return` near `_push_turn_detection_update` (`:269-276`);
  - call `self._notify_response_started()` inside the `response.created` branch right after `set_speaking(True)` (`:1114`).

  In `openai_realtime.py`:

```python
def _onset_ramp_samples(self) -> int:
    return int(self.SAMPLE_RATE * env_int("REALTIME_ONSET_RAMP_MS", 120, lo=0) / 1000)

def _notify_response_started(self) -> None:
    self._onset_ramp_remaining = self._onset_ramp_samples()

def _apply_onset_ramp(self, pcm: NDArray[np.int16]) -> NDArray[np.int16]:
    remaining = getattr(self, "_onset_ramp_remaining", 0)
    if remaining <= 0 or pcm.size == 0:
        return pcm
    total = self._onset_ramp_samples()
    n = min(remaining, pcm.size)
    start = total - remaining
    ramp = (np.arange(start, start + n, dtype=np.float32) + 1.0) / float(total)
    flat = pcm.reshape(-1).astype(np.float32)
    flat[:n] *= ramp
    self._onset_ramp_remaining = remaining - n
    return np.round(flat).astype(np.int16).reshape(pcm.shape)
```

  In `emit()` apply to the int16 stream in both branches (ramp first, then
  VoiceFX/resample so the ramp survives at the speaker):
  convert once with the existing `audio_to_int16(pcm)` then
  `filtered = self._voice_filter(rate).process(self._apply_onset_ramp(chunk_i16))`;
  in the `rate == ROBOT_RATE` early-return branch, also apply the ramp.
  Initialize `_onset_ramp_remaining = 0` where the handler initializes its
  other lazily-created audio state (`__init__`/`_build_realtime_client`).

- [ ] **Step 4: Run** emit tests + suite → PASS; ruff + mypy (mind `NDArray` import).
- [ ] **Step 5: Document** `REALTIME_ONSET_RAMP_MS` in `.env.example` + env-docs test.
- [ ] **Step 6: Commit** — `feat(audio): per-response onset amplitude ramp (AEC convergence aid)`

---

### Task 6: Boot gate (no turns until the greeting is done)

**Files:**
- Modify: `reachy_companion/src/reachy_companion/huggingface_realtime.py` (init `:222` area, session start `:1027`, greeting `:727-757`, `response.done` `:1123-1136`)
- Modify: `reachy_companion/src/reachy_companion/openai_realtime.py` (`_get_session_config` `:283-295`)
- Test: `reachy_companion/tests/test_boot_gate.py` (new; fake-connection style from `test_huggingface_realtime.py:29-95`)

**Interfaces:**
- Produces: handler fields `_boot_gate_active: bool` (True at `__init__`
  when `env_bool("REALTIME_BOOT_GATE", True)`), `_boot_gate_task:
  asyncio.Task[None] | None`.
- Produces: `async def _finish_boot_gate(self, reason: str, conn: Any | None = None) -> None` —
  idempotent; **no-ops if `conn is not None and self.connection is not conn`**
  (stale-session guard, Codex R1-4); clears the flag; cancels
  `_boot_gate_task` **only when it is not `asyncio.current_task()`**
  (Codex R1-3), clearing the ref either way; sends
  `input_audio_buffer.clear`; then `await self._push_turn_detection_update()`;
  logs `"boot gate released (%s)"`.
- OpenAI `_get_session_config`: when
  `getattr(self, "_boot_gate_active", False) and not getattr(self, "_startup_greeting_sent", True)`,
  set `cfg["audio"]["input"]["turn_detection"] = None` (SDK accepts `None`:
  `RealtimeAudioInputTurnDetection = ServerVad | SemanticVad | None`).
  Putting the reconnect condition **inside the config builder** means it is
  correct wherever the config is built — no ordering dependency on the
  `:1027` reset block (Codex R1-1).
- Triggers:
  (a) **first `response.done` while gated → wait for the audio to drain →
  release.** On `response.done` while gated, spawn
  `_boot_gate_release_after_drain(conn)`: poll `audio_drain.is_audible()`
  every 100 ms (cap 3 s), then `await self._finish_boot_gate("greeting
  played", conn)`. Rationale (Codex R1-2, R2-1): `_startup_greeting_sent`
  flips before the response exists, so it cannot mean "greeting done"; and
  `response.done` fires while the greeting is still coming out of the
  speaker — enabling VAD at that instant would let the greeting's own tail
  audio (or its echo) commit the first turn, which is the exact failure the
  gate exists to prevent. While gated, VAD is off, so the only responses
  that can exist are the greeting or an operator RPC `say` — either is a
  correct release point. Stale `_pending_responses` entries are not a
  release risk: the gate runs only on a handler's **first** session
  (`_startup_greeting_sent` False), where that queue starts empty. The
  timeout remains the hard backstop and also bounds the drain wait.
  (b) fallback timer armed right after `_send_startup_greeting_prompt()` at
  `:1052`, capturing the current `conn` and passing it to
  `_finish_boot_gate("timeout", conn)` — `REALTIME_BOOT_GATE_TIMEOUT_S`
  default `8`. The timer task is **cancelled in the session's `finally`
  block** (`huggingface_realtime.py:1332-1351` area) so it cannot outlive
  its session (Codex R1-4).
  (c) if `get_session_greeting_prompt()` is empty (no greeting will ever
  produce a response), `_send_startup_greeting_prompt()`'s early-return
  path releases the gate immediately.
  (d) reconnects: `_startup_greeting_sent` True → the config-builder
  condition in (interfaces) yields normal VAD; also set
  `_boot_gate_active = False` early in `_run_realtime_session`, **before**
  the config is built at `:1013` (belt and braces).

- [ ] **Step 1: Failing tests** (`tests/test_boot_gate.py`) — using
  `_make_fake_realtime_client(events=[...], captured_update=[])`:
  1. `test_first_session_config_has_no_turn_detection` — first captured
     `session.update` has `audio.input.turn_detection is None`.
  2. `test_first_response_done_releases_the_gate_after_drain` — feed a
     `_FakeEvent("response.done", response=SimpleNamespace(id="r1"))` with
     `audio_drain` made audible (`test_party_mode.py:173-176` helper);
     assert NO second `session.update` while audible; then
     `audio_drain.note_cleared()` → the release update arrives whose
     `audio.input.turn_detection.type == "server_vad"`, with the fake
     connection's `input_audio_buffer.clear` awaited before it (Codex
     R3-1).
  2b. `test_stale_timer_cannot_release_a_new_session` — call
     `_finish_boot_gate("timeout", conn=object())` with
     `self.connection` set to a different object → gate stays active, no
     update sent.
  3. `test_reconnect_is_not_gated` — set `_startup_greeting_sent = True`
     before start; first `session.update` already carries `server_vad`.
  4. `test_boot_gate_env_off` — `REALTIME_BOOT_GATE=0` → first update
     carries `server_vad`.
  5. `test_boot_gate_timeout_releases` — no `response.done` event;
     `REALTIME_BOOT_GATE_TIMEOUT_S=0` (fires immediately); gate released.

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** per the interface block. Key code:

```python
async def _finish_boot_gate(self, reason: str, conn: Any | None = None) -> None:
    if not self._boot_gate_active:
        return
    if conn is not None and self.connection is not conn:
        return  # a stale timer from a dead session must not touch this one
    self._boot_gate_active = False
    task, self._boot_gate_task = self._boot_gate_task, None
    if task is not None and task is not asyncio.current_task():
        task.cancel()
    if self.connection is not None:
        try:
            await self.connection.input_audio_buffer.clear()
        except Exception as exc:  # noqa: BLE001 - clear is best-effort
            logger.debug("boot gate: input buffer clear failed: %s", exc)
    await self._push_turn_detection_update()
    logger.info("boot gate released (%s)", reason)
```

The fallback timer (armed after the greeting send at `:1052`, capturing the
live `conn` local from `_run_realtime_session`):

```python
if self._boot_gate_active:
    async def _boot_gate_timeout(bound_conn: Any) -> None:
        try:
            await asyncio.sleep(env_float("REALTIME_BOOT_GATE_TIMEOUT_S", 8.0, lo=0.0))
        except asyncio.CancelledError:
            return
        await self._finish_boot_gate("timeout", bound_conn)
    self._boot_gate_task = asyncio.create_task(_boot_gate_timeout(conn), name="boot-gate-timeout")
```

In the session's `finally` block (`:1332-1351` area): cancel and clear
`_boot_gate_task` if set. Early in `_run_realtime_session` — **before** the
config build at `:1013` — `if self._startup_greeting_sent:
self._boot_gate_active = False`. In `_send_startup_greeting_prompt()`'s
empty-greeting early return, also schedule
`await self._finish_boot_gate("no greeting configured")`. `response.done`
branch: `if self._boot_gate_active and self._boot_gate_task is not None:`
replace the timeout task with
`create_task(self._boot_gate_release_after_drain(conn))` — the drain
waiter from the interface block (poll `audio_drain.is_audible()` every
100 ms, cap 3 s, then `await self._finish_boot_gate("greeting played",
conn)`); never call `_finish_boot_gate` directly from `response.done`
(Codex R3-1). Note `_push_turn_detection_update` is a base no-op — the
boot gate is effective only on the OpenAI backend, which is the locked
backend (D-002); state the same in a comment.

- [ ] **Step 4: Run** new tests + `test_party_mode.py` + `test_huggingface_realtime.py` + suite → PASS; ruff + mypy.
- [ ] **Step 5: Document** `REALTIME_BOOT_GATE`, `REALTIME_BOOT_GATE_TIMEOUT_S` in `.env.example` + env-docs test.
- [ ] **Step 6: Commit** — `feat(realtime): boot gate — no committable turns until the greeting finishes`

---

### Task 7: Party-gate hardening (backchannel deny, face signal, session reset)

**Files:**
- Modify: `reachy_companion/src/reachy_companion/huggingface_realtime.py` (`_party_gate_accepts` `:286-294`, decision site `:1159-1198`, session start `:1027`)
- Test: `reachy_companion/tests/test_party_mode.py` (extend, following `_party_handler()` at `:21-37`)

**Interfaces:**
- Modifies: `_party_gate_accepts(self, transcript: str) -> bool` — new
  order: (1) control phrases accept (unchanged, checked FIRST so 「停」 can
  never be suppressed); (2) `is_backchannel(transcript)` → deny even inside
  the follow-up window; (3) names accept; (4) follow-up window accept;
  (5) face-presence accept: `env_bool("REALTIME_PARTY_FACE_GATE", True)` and
  `self._face_engaged()` and `is_substantive(transcript)`.
- Produces: `def _face_engaged(self) -> bool` — reads
  `self.deps.reachy_mini.get_tracked_face(wait=False)` inside `try/except
  Exception → False`; True iff `face.detected` and `face.ts` is within
  `REALTIME_PARTY_FACE_FRESH_S` (default `3.0`) of `time.time()` and
  `abs(face.x) <= REALTIME_PARTY_FACE_CENTER` (default `0.4`, i.e. roughly
  centered ≈ facing the robot; the daemon's YuNet only detects near-frontal
  faces, so presence is already an orientation proxy — say so in the
  docstring). `FaceTarget.ts` semantics: confirm against
  `reachy_mini/vision/face_tracking.py` whether `ts` is `time.time()`-based
  or monotonic before comparing — adjust the freshness check to the actual
  clock.
- Produces: session-boundary reset — at session start (`:1027` area):
  `self._party_last_accept_at = None; self._party_speech_open = False;
  self._party_utterance_seq += 1` (stale context must not carry into a new
  session — the research doc's SAS carry-over hazard).

- [ ] **Step 1: Failing tests** (extend `test_party_mode.py`; `_party_handler()` needs a `deps` attr — add `h.deps = SimpleNamespace(reachy_mini=SimpleNamespace(get_tracked_face=lambda wait: SimpleNamespace(detected=False, x=None, ts=None)), movement_manager=MagicMock())`):

```python
def test_gate_denies_backchannel_even_in_followup_window():
    h = _party_handler()
    h._party_last_accept_at = time.monotonic()
    assert h._party_gate_accepts("嗯嗯") is False
    assert h._party_gate_accepts("哈哈哈") is False

def test_gate_control_phrase_beats_backchannel_filter():
    h = _party_handler()
    assert h._party_gate_accepts("停") is True

def test_gate_accepts_substantive_speech_from_engaged_face(monkeypatch):
    h = _party_handler()
    face = SimpleNamespace(detected=True, x=0.1, y=0.0, roll=0.0, ts=time.time())
    h.deps.reachy_mini.get_tracked_face = lambda wait: face
    assert h._party_gate_accepts("可以幫我開燈嗎") is True
    assert h._party_gate_accepts("嗯嗯") is False        # backchannel still denied

def test_gate_face_signal_ignores_stale_or_offcenter(monkeypatch):
    h = _party_handler()
    stale = SimpleNamespace(detected=True, x=0.1, ts=time.time() - 60)
    h.deps.reachy_mini.get_tracked_face = lambda wait: stale
    assert h._party_gate_accepts("可以幫我開燈嗎") is False
    off = SimpleNamespace(detected=True, x=0.9, ts=time.time())
    h.deps.reachy_mini.get_tracked_face = lambda wait: off
    assert h._party_gate_accepts("可以幫我開燈嗎") is False

def test_gate_face_signal_env_off(monkeypatch):
    monkeypatch.setenv("REALTIME_PARTY_FACE_GATE", "0")
    h = _party_handler()
    face = SimpleNamespace(detected=True, x=0.0, ts=time.time())
    h.deps.reachy_mini.get_tracked_face = lambda wait: face
    assert h._party_gate_accepts("可以幫我開燈嗎") is False

def test_face_query_failure_is_a_quiet_no():
    h = _party_handler()
    def boom(wait): raise RuntimeError("daemon gone")
    h.deps.reachy_mini.get_tracked_face = boom
    assert h._face_engaged() is False
```

Plus a session-reset test: construct via the fake-client path (or set the
fields and call the reset seam directly) asserting `_party_last_accept_at`
is None after a session (re)start.

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** per the interface block (gate order matters;
  keep the method under ~25 lines; log the face-accept distinctly:
  `logger.info("party gate: accepted via engaged face (%d chars)", len(transcript))`).
- [ ] **Step 4: Run** `test_party_mode.py` + suite → PASS; ruff + mypy.
- [ ] **Step 5: Document** `REALTIME_PARTY_FACE_GATE`, `REALTIME_PARTY_FACE_FRESH_S`, `REALTIME_PARTY_FACE_CENTER` in `.env.example` + env-docs test.
- [ ] **Step 6: Commit** — `feat(realtime): party gate — backchannel deny, face-engagement signal, session reset`

---

### Task 8: Solo pause-then-decide barge-in with rollback

The riskiest task; everything is behind `REALTIME_SOLO_CLIENT_BARGE`
(default ON; `0` = byte-identical legacy behavior).

**Files:**
- Modify: `reachy_companion/src/reachy_companion/openai_realtime.py` (`_turn_detection` `:81-114`)
- Modify: `reachy_companion/src/reachy_companion/huggingface_realtime.py` (init fields, `speech_started` `:1056-1080`, `speech_stopped` `:1082-1086`, transcription completed `:1159-1198` / failed `:1200-1207`)
- Modify: `reachy_companion/src/reachy_companion/conversation_handler.py:76-94` (`emit()` pause/held-audio logic)
- Modify: `reachy_companion/src/reachy_companion/hanova/audio_drain.py` (`note_paused` flag; `is_audible` and `note_queue_empty` honor it)
- Test: `reachy_companion/tests/test_solo_barge.py` (new; `_party_handler()`-style construction + `audio_drain` helpers from `test_party_mode.py:173-176`)

**Interfaces:**
- `_solo_client_barge() -> bool` module fn in `huggingface_realtime.py`:
  `env_bool("REALTIME_SOLO_CLIENT_BARGE", True)`.
- `_turn_detection(party)` change: solo sets
  `interrupt_response = not _solo_client_barge()` (i.e. `False` when the new
  mode is on — the client now owns cancellation); `create_response` stays
  absent (server auto-responds) in solo. Party unchanged.
  **Server-response semantics we rely on (Codex R1-10):** with
  `interrupt_response=false`, a turn that commits while a response is still
  active gets its auto `response.create` **rejected server-side** (one
  active response per conversation). So after a rollback the backchannel
  turn usually gets no reply for free, and after a confirmed barge the
  user's real turn may ALSO have lost its auto-response — which is exactly
  what the watchdog below repairs. If a `response.created` does arrive
  after a rollback (the old response finished at commit time — a race), let
  it play; the `wait_for_user` prompt rule is the model-side suppressor,
  and the case is logged for the journal.
- New handler fields (init together with the party fields `:229-243`):
  `self._barge_paused: bool = False`, `self._barge_pending: bool = False`,
  `self._barge_speech_open: bool = False` (solo has no speech-open state of
  its own today — `_party_speech_open` is set only in the party branch;
  Codex R1-7), `self._barge_confirm_task: asyncio.Task[None] | None`,
  `self._barge_rollback_task: asyncio.Task[None] | None`,
  `self._barge_watchdog_task: asyncio.Task[None] | None` (three distinct
  refs — one field cannot represent three lifecycles, Codex R1-8),
  `self._barge_cooldown_until: float = 0.0`,
  `self._barge_response_seen: bool = False`,
  `self._held_audio: collections.deque[tuple[int, Any]]` (see pause
  mechanics).
- **Pause mechanics (Codex R1-5, R1-6).** `_barge_paused` must not starve
  `AdditionalOutputs` (the output queue is mixed) and must not let
  `audio_drain` believe the robot went silent:
  - `conversation_handler.py` `emit()` (`:76-94`): while
    `getattr(self, "_barge_paused", False)`: dequeue as normal; if the item
    is `AdditionalOutputs` (or any non-audio item) → return it unchanged;
    if it is an audio tuple → append to `self._held_audio` and return
    `None`. On normal (unpaused) calls, `emit()` first drains
    `self._held_audio` (FIFO) before touching the queue.
  - `audio_drain` gains `note_paused(paused: bool)` + module flag; while
    paused, `is_audible()` returns `True` unconditionally,
    `note_queue_empty()` is a no-op, **and `_is_drained()` /
    `wait_drained()` report not-drained** (Codex R2-3: music_hooks'
    `_resume_when_drained` waits on `wait_drained`, which does not consult
    `is_audible` — without this, music could resume mid-pause). The
    queue-empty marks generated by the idling play loop during a pause are
    lies and must not reach the music hooks or `_robot_audible()`.
  - `_pause_playback()` — sets `_barge_paused = True` and calls
    `audio_drain.note_paused(True)`.
  - `_resume_playback(*, rolled_back: bool)` — clears `_barge_paused`,
    `_barge_pending`; `audio_drain.note_paused(False)`; cancels whichever
    of confirm/rollback tasks is not the current task; when `rolled_back`,
    calls `self._notify_response_started()` (re-arms the onset ramp so the
    resume does not pop) and logs `"barge-in rolled back; resuming reply"`;
    when not rolled back (real barge), clears `self._held_audio` (that
    audio belongs to the cancelled reply).
- Timer methods (every one takes `seq: int` and re-checks
  `self._party_utterance_seq`, `self._party_mode is False`, and its
  precondition flags before acting — the staleness pattern of
  `_party_barge_confirm` `:305-321`):
  - `async def _confirm_solo_barge(self, seq: int) -> None` — after
    `REALTIME_BARGE_CONFIRM_MS` (default `250`): if `_barge_speech_open`
    still True → real barge: `await self._cancel_active_response()`;
    `self._clear_queue()` if set; `_resume_playback(rolled_back=False)`;
    `_barge_cooldown_until = time.monotonic() + REALTIME_BARGE_COOLDOWN_MS/1000`
    (default `800`); `_barge_response_seen = False`; arm
    `_barge_watchdog_task = create_task(self._barge_response_watchdog(seq))`.
  - `async def _rollback_timer(self, seq: int) -> None` — waits
    `REALTIME_BARGE_ROLLBACK_TIMEOUT_S` (default `2.0`); if `_barge_pending`
    still True → `_resume_playback(rolled_back=True)`.
  - `async def _barge_response_watchdog(self, seq: int) -> None` — sleeps
    1.5 s; if seq current and `not self._barge_response_seen` and
    `self._response_done_event.is_set()` → `await self._safe_response_create()`
    (Codex R1-11). The `response.created` branch sets
    `_barge_response_seen = True` and cancels the watchdog task if set.
- Event wiring (solo path only; party path untouched):
  - `speech_started`: if `not _solo_client_barge()` → legacy branch
    verbatim (`_clear_queue()` immediately, `on_user_speech_started`).
    Else: `self._barge_speech_open = True`;
    **`on_user_speech_candidate(self.deps)` unconditionally** (Codex R2-2:
    ducks robot-speaker music exactly as the party path does at `:1063`;
    do NOT call `on_user_speech_started` here — it runs
    `audio_drain.note_cleared()`, which would wreck rollback accounting);
    if `time.monotonic() < self._barge_cooldown_until` → log debug, skip;
    elif `self._robot_audible()` → `_pause_playback()`;
    `_barge_pending = True`; `self._party_utterance_seq += 1`; arm
    `_barge_confirm_task = create_task(self._confirm_solo_barge(seq))`;
    else → nothing to protect, normal listening.
  - `speech_stopped`: `self._barge_speech_open = False`; if
    `_barge_pending` and confirm task still pending → cancel confirm task,
    arm `_barge_rollback_task = create_task(self._rollback_timer(seq))`.
  - `conversation.item.input_audio_transcription.completed`: the
    `_barge_pending` resolution runs **before** the existing empty-transcript
    `continue` at `:1168-1170` (an empty transcript must resolve the barge
    as a rollback, not leak the pause — Codex R1-9): if `_barge_pending`:
    cancel rollback timer; if `is_substantive(transcript)` → real
    interruption: `await self._cancel_active_response()`;
    `self._clear_queue()`; `_resume_playback(rolled_back=False)`; cooldown;
    watchdog. Else (backchannel/empty) →
    `_resume_playback(rolled_back=True)` and, for non-empty backchannels,
    surface the transcript to the console exactly like the party deny does
    (`AdditionalOutputs` + `_emit_transcript`, `:1181-1183`). Log
    `"solo barge rolled back (backchannel)"` / `"(empty)"`.
  - `...transcription.failed` (solo): if `_barge_pending` →
    `_resume_playback(rolled_back=True)`.
- **External-interrupt hook (Codex R2-4):** produce
  `def on_external_interrupt(self) -> None` on the handler — cancels all
  three barge tasks, clears `_barge_paused`/`_barge_pending`/
  `_barge_speech_open`, empties `_held_audio`, calls
  `audio_drain.note_paused(False)`. `LocalStream.clear_audio_queue()`
  (`console.py:862-884`) calls
  `getattr(self.handler, "on_external_interrupt", lambda: None)()` first —
  otherwise an operator RPC `conversation.interrupt`/`say` during a solo
  pause would flush the live queue but leave held audio to be resurrected
  by a later rollback.
- Session-start reset (same seam as Task 7) and handler shutdown/finally:
  **cancel** all three barge tasks (not merely clear the refs — Codex
  R1-8), reset the scalar fields, empty `_held_audio`, and call
  `audio_drain.note_paused(False)` (i.e. call `on_external_interrupt()`
  plus reset `_barge_cooldown_until`/`_barge_response_seen`).

- [ ] **Step 1: Failing tests** (`tests/test_solo_barge.py`) — all
  `@pytest.mark.asyncio`, confirm/rollback envs set to tiny values
  (`REALTIME_BARGE_CONFIRM_MS=30`, `REALTIME_BARGE_ROLLBACK_TIMEOUT_S=0.05`)
  like `test_party_mode.py:180-251` does:
  1. `test_solo_speech_start_pauses_instead_of_flushing` — audible handler,
     `speech_started` → `_barge_paused` True, `_clear_queue` NOT called.
  2. `test_sustained_speech_confirms_and_cancels` — speech stays open past
     confirm → `response.cancel` awaited, `_clear_queue` called, pause
     cleared, cooldown set.
  3. `test_short_blip_rolls_back_on_timeout` — `speech_stopped` before
     confirm, no transcript → after rollback timeout `_barge_paused` False,
     no cancel, onset ramp re-armed (`_notify_response_started` spied).
  4. `test_backchannel_transcript_rolls_back` — `speech_stopped` then
     transcription.completed with 「嗯嗯」 → rollback, no cancel.
  5. `test_substantive_transcript_confirms` — transcription.completed with
     「幫我開燈」 → cancel + flush + no pause.
  6. `test_cooldown_swallows_immediate_retrigger`.
  7. `test_legacy_env_restores_old_path` — `REALTIME_SOLO_CLIENT_BARGE=0` →
     `speech_started` flushes immediately, never pauses; and
     `_turn_detection(False)["interrupt_response"] is True`. Also update the
     existing `test_solo_turn_detection_is_unchanged`
     (`test_party_mode.py:60-64`) for the new default (Codex R1-12).
  8. `test_paused_emit_holds_audio_but_passes_additional_outputs` —
     `ConversationHandler` is abstract (`conversation_handler.py:111-159`),
     so build the handler via `test_openai_realtime_config.py`'s
     `_emit_ready_handler()` (a concrete, emit-capable instance): with
     `_barge_paused=True`, enqueue an audio tuple then an
     `AdditionalOutputs` — `emit()` buffers the audio into `_held_audio`
     and returns the `AdditionalOutputs`; after `_resume_playback`, the
     held audio comes out first, in order.
  9. `test_audio_drain_paused_keeps_audible_and_blocks_drain` —
     `audio_drain.note_paused(True)`; `note_queue_empty()` → `is_audible()`
     still True; AND (Codex R3-2) a begun+closed generation with all audio
     sunk must NOT drain while paused:
     `await audio_drain.wait_drained(gen, timeout_s=0.05) is False`; after
     `note_paused(False)` it drains normally; `audio_drain.reset()` must
     clear the paused flag. (Assert `_clear_queue_callback` — the property
     setter destination, `openai_realtime.py:180-208` — where the sketches
     spy on flushes.)
  10. `test_external_interrupt_clears_held_audio_and_barge_state` — pause
     with held audio + live timers; call `on_external_interrupt()`; all
     three task refs cancelled, `_held_audio` empty, flags cleared,
     `audio_drain` unpaused.

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** exactly per the interface block. Keep the
  solo/party branches visibly separate in `speech_started`; do not touch the
  party debounce. All timer tasks must check a captured seq against
  `self._party_utterance_seq` before acting (staleness guard, same pattern
  as `_party_barge_confirm` `:305-321`).
- [ ] **Step 4: Run** new tests + `test_party_mode.py` + `test_huggingface_realtime.py` + full suite → PASS; ruff + mypy.
- [ ] **Step 5: Document** `REALTIME_SOLO_CLIENT_BARGE`, `REALTIME_BARGE_CONFIRM_MS`, `REALTIME_BARGE_ROLLBACK_TIMEOUT_S`, `REALTIME_BARGE_COOLDOWN_MS` in `.env.example` + env-docs test.
- [ ] **Step 6: Commit** — `feat(realtime): solo pause-then-decide barge-in with false-interruption rollback`

---

### Task 9: State files, docs, and the verification ledger

**Files:**
- Modify: `progress.md` (new top section), `DECISIONS.md` (D-023),
  `feature_list.json` (new rows), `docs/multi-person-investigation.md`
  (pointer to the research doc + shipped tiers),
  `reachy_companion/.env.example` (final consistency pass).

- [ ] **Step 1:** `DECISIONS.md` — record **D-023**: model default
  `gpt-realtime-2.1-mini` (cost; `REALTIME_MODEL` reverts), prompt
  hardening + `wait_for_user` (39 tools), transcription `gpt-transcribe` +
  keywords with legacy fallback, onset ramp, boot gate, gate hardening +
  face signal (presence-as-orientation proxy, its limits), solo client
  barge-in with rollback (`REALTIME_SOLO_CLIENT_BARGE=0` reverts). One
  paragraph each: what + why + revert lever.
- [ ] **Step 2:** `feature_list.json` — add rows with
  `verification` = the on-robot checks only a live pass can run:
  (a) mini-model tool-calling quality across the 38+1 tools (fallback:
  `REALTIME_MODEL=gpt-realtime-2.1`); (b) boot: wake the robot into a noisy
  room, journal shows `boot gate released (greeting done)` and no committed
  turn before it; (c) solo barge feel: real interruption still lands
  &lt; ~1 s, a cough/嗯 mid-reply produces `barge-in rolled back` and the
  sentence finishes; (d) `wait_for_user` fires on TV/side-talk (count
  journal lines); (e) party mode with faces: engaged-face accept works,
  stale/off-center does not; (f) A/B `REALTIME_NOISE_REDUCTION`
  off/near/far downstream of the XVF3800; (g) `gpt-transcribe` actually
  accepted by the live API (else the fallback fires — check for the
  `retrying with legacy transcription shape` warning); (h) semantic_vad
  eagerness=low A/B (env already exists). Each row: state
  `implemented-unverified`, exact journal line to look for.
- [ ] **Step 3:** `progress.md` new section + `docs/multi-person-investigation.md` addendum linking `docs/research-realtime-voice-best-practices.md`.
- [ ] **Step 4:** Full gate one last time: `python -m pytest`, `ruff check .`, `mypy src`. Record counts in `progress.md`.
- [ ] **Step 5: Commit** — `docs: voice-robustness round — D-023, verification ledger`

---

## Plan Review Log (Codex)

Per CLAUDE.md: up to 3 iterations of `codex --profile nova-auto exec`;
each finding accepted or rejected on evidence; rejections get a one-line
reason. Rounds appended below by the orchestrator.

**Round 1 (2026-08-25): 12 findings — 12 accepted (2 with adaptation), 0 rejected.**

| # | Sev | Verdict | Resolution |
|---|-----|---------|------------|
| 1 | high | accepted | Reconnect condition moved inside OpenAI `_get_session_config` (`… and not _startup_greeting_sent`); belt-and-braces reset before `:1013`. |
| 2 | high | accepted (adapted) | Release = **first `response.done` while gated** — while gated VAD is off, so only the greeting or an RPC `say` can produce one; response-id tracking rejected as overkill given the timeout backstop. Empty-greeting path releases immediately. |
| 3 | high | accepted | `_finish_boot_gate` never cancels `asyncio.current_task()`; ref cleared separately. |
| 4 | high | accepted | Timer binds the session's `conn`; `_finish_boot_gate` no-ops on mismatch; task cancelled in the session `finally`. |
| 5 | high | accepted | `audio_drain.note_paused()`: while paused `is_audible()` is True and `note_queue_empty()` no-ops; pause can no longer corrupt drain accounting. |
| 6 | med | accepted | Paused `emit()` passes `AdditionalOutputs` through and buffers audio tuples in `_held_audio` (FIFO, drained first on resume). |
| 7 | high | accepted | New `_barge_speech_open` field owned by the solo path. |
| 8 | high | accepted | Three distinct task refs (confirm/rollback/watchdog); all cancelled (not cleared) on reset/shutdown; seq-guarded. |
| 9 | high | accepted | Barge resolution runs before the empty-transcript `continue` (`:1168-1170`); empty ⇒ rollback. |
| 10 | high | accepted (adapted) | Documented the server one-active-response rejection mechanism; watchdog repairs the lost auto-response after a real barge; rollback-race response is allowed to play and logged. Full client-owned `create_response` in solo rejected: it adds transcription latency to every normal turn (party mode's known cost) for a case the rejection mechanism already covers. |
| 11 | med | accepted | Watchdog takes `seq`, `_barge_response_seen` set at `response.created` (which also cancels the watchdog). |
| 12 | med | accepted | Tests use `_emit_ready_handler()` for emit-path coverage; `_clear_queue_callback` asserted; `test_solo_turn_detection_is_unchanged` updated for the new default. |

**Round 2 (2026-08-25): 4 findings — 4 accepted (1 with adaptation), 0 rejected.**

| # | Sev | Verdict | Resolution |
|---|-----|---------|------------|
| 1 | high | accepted (adapted) | Gate release now waits for `audio_drain.is_audible()` to clear (100 ms poll, 3 s cap) after the first `response.done`. Greeting-response-id tracking and `say()` deferral rejected: the gate runs only on a handler's first session, where `_pending_responses` starts empty, and an RPC-`say` response is an equally valid release point; the timeout stays the hard backstop. |
| 2 | high | accepted | Solo barge path calls `on_user_speech_candidate` unconditionally (music duck preserved); `on_user_speech_started` deliberately NOT called (its `note_cleared()` would corrupt rollback accounting). |
| 3 | med | accepted | `note_paused` also forces `_is_drained()`/`wait_drained()` false while paused; test added (paused, closed, no-audio generation must not drain). |
| 4 | med | accepted | New `handler.on_external_interrupt()` hook; `LocalStream.clear_audio_queue()` invokes it before draining, so RPC interrupts clear held audio and barge state. |

**Round 3 (2026-08-25): 2 findings — 2 accepted, 0 rejected. Review closed.**

| # | Sev | Verdict | Resolution |
|---|-----|---------|------------|
| 1 | high | accepted | Task 6 Step 3 aligned with the interface: `response.done` swaps the timeout task for `_boot_gate_release_after_drain(conn)`; never calls `_finish_boot_gate` directly; test asserts no VAD update while audible. |
| 2 | high | accepted | Task 8 test 9 now requires `wait_drained` to report not-drained while paused (a sunk, closed generation), with `reset()` clearing the flag — pinning the `_is_drained()` check, not just `is_audible()`. |

## Self-Review Notes

- Spec coverage: research §8 items 1–8 map to Tasks 1–8; §8 item 9
  (watchlist) intentionally unimplemented; on-robot A/Bs live as
  feature_list rows (Task 9), because they cannot run without the robot.
- Solo latency: Task 8 keeps `create_response` server-side in solo, so
  normal turn latency is unchanged; only barge-in decisions add delay
  (bounded by confirm 250 ms).
- Type consistency: `realtime_model()` (T1) used in T4's config tests;
  `is_backchannel`/`is_substantive` (T2) consumed by T7/T8;
  `_notify_response_started` (T5) re-used by T8's rollback resume; seq
  staleness reuses `_party_utterance_seq` (existing field).
