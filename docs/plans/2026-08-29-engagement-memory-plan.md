# Engagement Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Reachy's per-person memory *engaging* — open-loop facts, cross-person links, a "last conversation" callback written at sleep, and an operator-run consolidation pass on the Mac backend — without new infrastructure.

**Architecture:** Robot-side, a bounded in-session transcript tail plus a set of recognized names feed one timeout-bounded LLM call at handler shutdown, which writes a superseding `上次聊天` fact per recognized person into the existing `people.v1.json` store. Backend-side, a CLI batch job runs an LLM merge/supersede/rank pass over the Mac people store, feeding the existing import→push sync. Everything is prompt- and JSON-store-based; no DB, no scheduler, no new services.

**Tech Stack:** Python 3.12, existing `openai` SDK (`AsyncOpenAI` robot-side per `hanova/images.py` precedent; sync `OpenAI` in the backend CLI), pytest, FastAPI TestClient (backend), ruff + mypy strict.

**Spec:** §Design below (this document is self-contained; product context in `progress.md` → "Known defects / open edges" 2026-08-29 entries and the operator direction recorded there).

## Global Constraints

- Reuse-first (CLAUDE.md): reuse `hanova.images.build_client`, `people.py` API, `backend/store.py`/`projection.py`/`robot.py` seams; never re-implement them.
- No new/upgraded major dependencies. The `openai` package is already a dependency; the backend runs from `reachy_companion/.venv`.
- Secrets from env only (`OPENAI_API_KEY`); never committed, never logged. Log exception *types*, not messages that could carry payload (`redact` posture of `hanova/images.py`).
- Memory writes must never break the session: every failure path in the sleep-summary flow logs and returns — no raise reaches `shutdown()`.
- Fact text cap is `MAX_FACT_CHARS = 280` (`memory.py:18`); person cap `MAX_FACTS_PER_PERSON = 20`, people cap `MAX_PEOPLE = 12` (`people.py:49-51`). Do not change any cap.
- Env-flag conventions: robot-side helpers from `audio/envparse.py` (`env_bool`/`env_int`/`env_float`, clamp+warn), read at use site, default-ON feature flags log one INFO line when disabled and degrade to no-op (pattern: `main.py:394-402`).
- Gates for every task: `ruff check` + `mypy --strict` clean; robot suite `cd reachy_companion && python -m pytest` green; backend suite `cd companion_backend && ../reachy_companion/.venv/bin/python -m pytest tests/` green.
- Chinese copy: Taiwan Traditional Chinese (persona rules, `persona.md` lines 22-26).

## Design

### D1. Open loops + cross-person links (prompt only)

Facts today are static traits. The highest-engagement memory is the *unresolved thread* (an exam coming, a song being written). We nudge the `remember` tool and persona to prefer those, and explicitly allow facts that mention other enrolled people by name (「牙牙是雲霓的女兒」). No code paths change: facts are free text already.

### D2. Last-conversation callback ("上次聊天" fact)

- **Transcript tail:** the app retains no transcript (`console.py:930-938` logs and drops). Add `session_transcript: deque[tuple[str, str]]` (maxlen 40), `recognized_people: set[str]`, and `sleep_requested: bool` to `ToolDependencies`. The realtime handler records final user/assistant text at exactly TWO sites: the accepted user final (~`huggingface_realtime.py:2214`) and the assistant final (~:2242). Explicitly NOT recorded: the party-mode DENIED ambient push (~:2196-2204, "ambient chatter … touch nothing else" — rejected speech must never enter memory) and the solo-barge rolled-back backchannel (~:1037-1040 — a 「嗯」 the barge logic decided to ignore is not a committed turn). Every site that sets `deps.current_person` also adds to `recognized_people`. Neither container is cleared on session reconnect — the buffer spans the whole app run ("this visit").
- **Summary at shutdown, gated on sleep:** `HuggingFaceRealtimeHandler.shutdown()` (`huggingface_realtime.py:2449`) is awaited from `console.py:826` on the still-live event loop — async work completes there. But `shutdown()` also runs on settings/backend restarts (`console.py:307`, `:697`), which are mid-visit; so the `go_to_sleep_and_stop_app` closure (`main.py:314`) sets `deps.sleep_requested = True` and the writer runs only when that flag is set. Accepted limitation (record in D-027): a stop issued from the dashboard instead of the voice tool writes no summary — voice sleep is the normal end of a visit.
- **Multi-person attribution:** the transcript has no speaker identity (only user/reachy). The summarizer prompt therefore writes *topic-level* summaries (聊到…) and is forbidden from attributing an utterance to a specific person unless the transcript itself names them. Deeper speaker attribution (diarization) is a non-goal.
- **Supersession without schema change:** `PersonFact` has no kind field; adding one would ripple through backend sync. Instead a text convention: the fact starts with `上次聊天（M月D日）：`. Write order is ADD-then-FORGET so a failure can never lose the only copy — except in two cases that force FORGET-first (at the 20-fact cap, where add-first would evict a real fact; and when an old text is a substring of the new one, where the substring-match-newest-first `forget_person_fact` — `people.py:381-405` — would otherwise delete the NEW fact). The exact branch logic and its tests live in Task 4. Exactly one last-chat fact remains; being newest-first it lands inside the 6 facts `who_is_this`/greeting inject — the callback needs zero greeting-code changes.
- **Sync interaction (backend):** the robot-side replacement meets a known import hole — removal detection is disabled at the 20-fact cap (`backend/robot.py:548`) — so the Mac store can retain a stale `上次聊天` fact after import and push it back. The consolidation pass (Task 7) therefore always dedupes `上次聊天` facts keep-newest, healing the pair on every run, and Task 7 pins the full cycle with a test.
- **Model call:** reuse `hanova.images.build_client()` (`AsyncOpenAI` or `None`, never raises). Model `MEMORY_LAST_CHAT_MODEL` (default `gpt-5-mini`), timeout `MEMORY_LAST_CHAT_TIMEOUT_S` (default 8.0 s, clamp 1–30), kill switch `MEMORY_LAST_CHAT_ENABLED` (default true).

### D3. Backend consolidation (operator-run batch)

A CLI (`companion_backend/scripts/consolidate.py`) that runs an LLM pass over each Mac-store person's facts: merge duplicates, turn contradictions into one 「以前…，現在…」 fact, drop stale trivia, rank the most conversationally useful first, keep the newest `上次聊天` fact verbatim (deduping older ones). Dry-run by default; `--apply` rewrites facts through a new `store.replace_facts` with `preserve_updated_at=True` (a bulk pass must not reshuffle the projection's top-12 recency ranking — `projection.py:104`). The Mac store stays uncapped ("No caps", `store.py:16`); only the *robot projection* is capped at 20. The store lock is process-local (`store.py:93`), so the CLI refuses to run while the backend answers on its port (probe guard). Operator flow: stop backend (or it was never started) → UI/API **import** (robot→Mac) → `consolidate.py` (review) → `--apply` → **push** (Mac→robot). No scheduler (YAGNI; launchd later if wanted).

### Non-goals

SQLite migration, embedding retrieval / `recall_about_person` tool, any scheduler, Mem0/Zep/Letta adoption, raising the 12-people or 20-fact caps, changing SFace identity. Revisit post-POC per PRD non-goals.

---

## File Structure

- Modify: `persona.md` (repo root; synced to robot instance)
- Modify: `reachy_companion/src/reachy_companion/tools/remember.py` (description only)
- Modify: `reachy_companion/src/reachy_companion/tools/core_tools.py` (two new `ToolDependencies` fields)
- Create: `reachy_companion/src/reachy_companion/sleep_summary.py` (record + summarize + write; all new logic in one module)
- Modify: `reachy_companion/src/reachy_companion/huggingface_realtime.py` (4 one-line wirings + shutdown hook)
- Modify: `reachy_companion/src/reachy_companion/tools/who_is_this.py` (one line: recognized_people)
- Create: `reachy_companion/tests/test_sleep_summary.py`
- Modify: `reachy_companion/.env.example`, `README.md` (env table)
- Create: `companion_backend/backend/consolidate.py`
- Modify: `companion_backend/backend/store.py` (`replace_facts`)
- Create: `companion_backend/scripts/consolidate.py` (CLI)
- Create: `companion_backend/tests/test_consolidate.py`
- Modify: `companion_backend/tests/test_store.py` (replace_facts tests; create file only if it does not exist — store tests may live in `test_api.py`'s module: put them wherever existing `add_fact` tests live)
- Modify: `DECISIONS.md` (D-027), `feature_list.json`, `progress.md`, `companion_backend/README.md`

Part A (Tasks 1–5, robot) and Part B (Tasks 6–8, backend) are independently shippable; Task 9 closes records for both.

---

### Task 1: Open-loop + cross-person prompt guidance

**Files:**
- Modify: `persona.md` (`### remember` section, line ~59; `## Physical Behavior` person-info bullet area, line ~45)
- Modify: `reachy_companion/src/reachy_companion/tools/remember.py:15-25` (description string)

**Interfaces:** none (prose only). Later tasks do not depend on this task.

- [ ] **Step 1: Edit `persona.md`.** Replace the `### remember` section body (currently 「使用者提供值得長期保留的個人資訊，例如名字、喜好或習慣時使用 `remember`。」) with:

```markdown
### remember
使用者提供值得長期保留的個人資訊時使用 `remember`。
最有價值的是「進行中的事」：計畫、目標、即將發生的事（考試、旅行、正在寫的歌）。
其次才是穩定的喜好與習慣。
事實裡可以自然提到其他你認識的人（例如「牙牙是雲霓的女兒」）。
```

Then, in the `## Tools` → `### who_is_this` section, append one line after 「辨識不到就坦白說不知道，絕對不要猜。」:

```markdown
認出人後，如果記憶裡有「上次聊天」或進行中的事，自然地追問後續（「上次你說…後來呢？」），不要逐條背誦記憶。
```

- [ ] **Step 2: Edit `remember.py` description.** In the `description` string, after the sentence ending "recurring projects, important people, or plans.", insert: `"Prefer ongoing threads — plans, upcoming events, things in progress — over static traits, and the fact may mention other people you know by name. "` (keep every other sentence unchanged).

- [ ] **Step 3: Run the tool tests.** `cd reachy_companion && python -m pytest tests -k "remember or persona" -q` — Expected: PASS (no test pins the old description; if one does, update the pinned string to match).

- [ ] **Step 4: Sync persona to the robot** (only if the operator's robot is reachable; otherwise record as pending): from repo root, `set -a && source .env && set +a`, then `scp persona.md "$REACHY_SSH_USER@$REACHY_HOST:/venvs/apps_venv/lib/python3.12/site-packages/reachy_companion/persona.md"` and verify `sha256sum` matches the local `shasum -a 256 persona.md`. Loads at next app start.

- [ ] **Step 5: Commit.** `git add persona.md reachy_companion/src/reachy_companion/tools/remember.py && git commit -m "feat(memory): prefer open-loop facts and allow cross-person links in remember guidance"`

---

### Task 2: Session transcript tail + recognized-people set

**Files:**
- Modify: `reachy_companion/src/reachy_companion/tools/core_tools.py` (ToolDependencies, near `current_person`, line ~55)
- Create: `reachy_companion/src/reachy_companion/sleep_summary.py` (constants + `record_transcript` only; the summarizer comes in Task 4)
- Test: `reachy_companion/tests/test_sleep_summary.py`

**Interfaces:**
- Produces: `ToolDependencies.session_transcript: deque[tuple[str, str]]` (maxlen `TRANSCRIPT_MAX_ITEMS`), `ToolDependencies.recognized_people: set[str]`, `ToolDependencies.sleep_requested: bool = False`; `sleep_summary.TRANSCRIPT_MAX_ITEMS: Final[int] = 40`; `sleep_summary.record_transcript(deps: ToolDependencies, role: str, text: str) -> None`.

- [ ] **Step 1: Write the failing tests** in `reachy_companion/tests/test_sleep_summary.py`:

```python
"""Tests for sleep_summary: transcript recording, formatting, and the shutdown writer."""
from __future__ import annotations

from reachy_companion import sleep_summary
from reachy_companion.tools.core_tools import ToolDependencies


def _deps(**kwargs: object) -> ToolDependencies:
    return ToolDependencies(**kwargs)  # type: ignore[arg-type]


def test_record_transcript_appends_role_and_text() -> None:
    deps = _deps()
    sleep_summary.record_transcript(deps, "user", "你好")
    sleep_summary.record_transcript(deps, "assistant", "嘿！")
    assert list(deps.session_transcript) == [("user", "你好"), ("assistant", "嘿！")]


def test_record_transcript_skips_blank_and_error_text() -> None:
    deps = _deps()
    sleep_summary.record_transcript(deps, "user", "   ")
    sleep_summary.record_transcript(deps, "assistant", "[error] Cancellation failed")
    assert not deps.session_transcript


def test_record_transcript_is_bounded() -> None:
    deps = _deps()
    for i in range(sleep_summary.TRANSCRIPT_MAX_ITEMS + 10):
        sleep_summary.record_transcript(deps, "user", f"line {i}")
    assert len(deps.session_transcript) == sleep_summary.TRANSCRIPT_MAX_ITEMS
    assert deps.session_transcript[0] == ("user", "line 10")


def test_recognized_people_defaults_empty_per_deps() -> None:
    assert _deps().recognized_people == set()
    a, b = _deps(), _deps()
    a.recognized_people.add("小諾")
    assert b.recognized_people == set()  # no shared default object
```

If `ToolDependencies` has required constructor fields, mirror how `tests/test_face_tools.py:129-148` builds one (`_deps` helper with `MagicMock()`s) instead of the bare constructor above.

- [ ] **Step 2: Run to verify failure.** `python -m pytest tests/test_sleep_summary.py -q` — Expected: FAIL (`ModuleNotFoundError: reachy_companion.sleep_summary` / missing attribute).

- [ ] **Step 3: Implement.** In `core_tools.py`, next to `current_person`, add (imports at top: `from collections import deque`, `from dataclasses import field` if not present):

```python
    # Whole-app-run engagement memory (sleep_summary.py): every name ever
    # recognized this run, and a bounded tail of final user/assistant text.
    # Deliberately NOT cleared on session reconnect — the unit is the visit.
    recognized_people: set[str] = field(default_factory=set)
    session_transcript: deque[tuple[str, str]] = field(default_factory=lambda: deque(maxlen=40))
    # Set only by the go_to_sleep closure in main.py; gates the sleep summary so
    # settings/backend restarts (console.py:307/:697 also reach shutdown()) don't
    # write mid-visit.
    sleep_requested: bool = False
```

Create `sleep_summary.py`:

```python
"""Sleep-time engagement memory: record the visit, write one 上次聊天 fact per person.

`record_transcript` is called by the realtime handler at its final-text push
sites; `write_sleep_summaries` (Task 4) runs once at handler shutdown.
"""
from __future__ import annotations

import logging
from typing import Final

from reachy_companion.tools.core_tools import ToolDependencies

logger = logging.getLogger(__name__)

TRANSCRIPT_MAX_ITEMS: Final[int] = 40
LAST_CHAT_PREFIX: Final[str] = "上次聊天"


def record_transcript(deps: ToolDependencies, role: str, text: str) -> None:
    """Append one finalized utterance to the bounded session tail."""
    cleaned = text.strip()
    if not cleaned or cleaned.startswith("[error]"):
        return
    deps.session_transcript.append((role, cleaned))
```

Keep the deque maxlen literal in `core_tools.py` equal to `TRANSCRIPT_MAX_ITEMS` (a `sleep_summary` import from `core_tools` would be a cycle — Tool classes import core_tools; add a comment in both files naming the other).

- [ ] **Step 4: Run tests.** `python -m pytest tests/test_sleep_summary.py -q` — Expected: PASS. Then `ruff check src tests && mypy --strict src` (match the repo's existing mypy invocation if different — see `pyproject.toml`).

- [ ] **Step 5: Commit.** `git add reachy_companion/src/reachy_companion/tools/core_tools.py reachy_companion/src/reachy_companion/sleep_summary.py reachy_companion/tests/test_sleep_summary.py && git commit -m "feat(memory): bounded session transcript and recognized-people set on ToolDependencies"`

---

### Task 3: Wire recording and recognition sites

**Files:**
- Modify: `reachy_companion/src/reachy_companion/huggingface_realtime.py` — final-text sites at lines ~2214 (ACCEPTED user final only) and ~2242 (assistant final from `response.output_audio_transcript.done`); recognition sites ~1592 (extended wake) and ~1669 (quick wake)
- Modify: `reachy_companion/src/reachy_companion/tools/who_is_this.py:48` area
- Test: `reachy_companion/tests/test_sleep_summary.py` (who_is_this side); wiring in the handler is verified by grep + existing suite

**Interfaces:**
- Consumes: `sleep_summary.record_transcript`, `deps.recognized_people` (Task 2).

- [ ] **Step 1: Failing test for who_is_this.** Append to `test_sleep_summary.py` (reuse `tests/test_face_tools.py`'s `_FakeRecognizer` + `_deps` helper by import or copy — copy is fine, they are ~15 lines):

```python
import asyncio

def test_who_is_this_records_recognized_person(...) -> None:
    # Build deps + fake recognizer exactly as tests/test_face_tools.py does for
    # its recognized-path test; then:
    result = asyncio.run(WhoIsThis()(deps))
    assert result["status"] == "recognized"
    assert deps.current_person == "小諾"
    assert "小諾" in deps.recognized_people
```

(Write it concretely against the real fixture shapes in `test_face_tools.py` — the recognized-path test there shows the exact fake wiring; assert the *new* `recognized_people` line in addition to what that test already proves.)

- [ ] **Step 2: Run to verify failure.** Expected: FAIL on the `recognized_people` assertion.

- [ ] **Step 3: Implement.**
  - `who_is_this.py`, directly under `deps.current_person = name` (line ~48): `deps.recognized_people.add(name)`.
  - `huggingface_realtime.py` recognition sites: at line ~1592 and ~1669, wherever `deps.current_person` (or `self.deps.current_person`) is assigned a recognized name, add the matching `.recognized_people.add(name)` line.
  - `huggingface_realtime.py` transcript sites — exactly two: the ACCEPTED user final push (~:2214) gets `record_transcript(self.deps, "user", text_variable_used_there)`; the assistant final site (~:2242) gets `record_transcript(self.deps, "assistant", ...)`. Import `from reachy_companion.sleep_summary import record_transcript` at top. Do NOT record: the party-mode DENIED ambient push (~:2196-2204 — the block whose comment says "ambient chatter"/"touch nothing else"; rejected speech must never reach memory), the solo-barge rolled-back backchannel (~:1037-1040 — ignored, not committed), `user_partial` (~:1251), or `[error]` pushes (~:2358).
- [ ] **Step 4: Verify.** `python -m pytest tests/test_sleep_summary.py tests/test_face_tools.py -q` — Expected: PASS. Then `grep -n "record_transcript" src/reachy_companion/huggingface_realtime.py` — Expected: 1 import + exactly 2 call sites, neither inside the party-mode denied block nor the solo-barge rollback block (paste the grep output into the task notes). Full suite: `python -m pytest -q` — Expected: same pass/skip counts as before this task.
- [ ] **Step 5: Commit.** `git add reachy_companion/src/reachy_companion/huggingface_realtime.py reachy_companion/src/reachy_companion/tools/who_is_this.py reachy_companion/tests/test_sleep_summary.py && git commit -m "feat(memory): record final transcript lines and every recognized name during the visit"`

---

### Task 4: The sleep summarizer

**Files:**
- Modify: `reachy_companion/src/reachy_companion/sleep_summary.py`
- Test: `reachy_companion/tests/test_sleep_summary.py`

**Interfaces:**
- Consumes: `hanova.images.build_client() -> Any | None`; `people.add_person_fact(instance_path, name, text) -> PersonFact | None`; `people.facts_for_person(instance_path, name) -> list[PersonFact]`; `people.forget_person_fact(instance_path, name, *, query) -> ForgetPersonFactResult`; `audio.envparse.env_bool/env_float`.
- Produces: `format_last_chat_fact(summary: str) -> str`; `async write_sleep_summaries(deps: ToolDependencies, *, client: Any | None = None) -> int` (count of persons written; never raises).

- [ ] **Step 1: Write the failing tests.** Append to `test_sleep_summary.py`:

```python
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock


class _FakeCompletions:
    def __init__(self, payload: str | Exception) -> None:
        self.payload, self.calls = payload, []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.payload, Exception):
            raise self.payload
        msg = SimpleNamespace(content=self.payload)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


class _FakeClient:
    """Shape-compatible with AsyncOpenAI for `async with` + chat.completions.create."""

    def __init__(self, payload: str | Exception) -> None:
        self.chat = SimpleNamespace(completions=_FakeCompletions(payload))

    async def __aenter__(self) -> "_FakeClient":  # special methods live on the CLASS
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


def _client(payload: str | Exception):
    client = _FakeClient(payload)
    return client, client.chat.completions


def _visit_deps(tmp_path, *names: str) -> ToolDependencies:
    deps = _deps(instance_path=str(tmp_path))
    deps.recognized_people.update(names)
    sleep_summary.record_transcript(deps, "user", "我下週要考試")
    sleep_summary.record_transcript(deps, "assistant", "加油！考完跟我說")
    return deps


def test_write_sleep_summaries_writes_prefixed_fact(tmp_path) -> None:
    client, fake = _client(json.dumps({"小諾": "聊到下週的考試，Reachy 說好要問結果"}))
    deps = _visit_deps(tmp_path, "小諾")
    assert asyncio.run(sleep_summary.write_sleep_summaries(deps, client=client)) == 1
    facts = people.facts_for_person(tmp_path, "小諾")
    assert facts[0].text.startswith(sleep_summary.LAST_CHAT_PREFIX)
    assert "考試" in facts[0].text
    assert fake.calls[0]["response_format"] == {"type": "json_object"}


def test_write_sleep_summaries_supersedes_previous_last_chat(tmp_path) -> None:
    people.add_person_fact(tmp_path, "小諾", "上次聊天（8月1日）：聊了機器人")
    people.add_person_fact(tmp_path, "小諾", "小諾在軟體領域上班")
    client, _ = _client(json.dumps({"小諾": "聊到考試"}))
    deps = _visit_deps(tmp_path, "小諾")
    asyncio.run(sleep_summary.write_sleep_summaries(deps, client=client))
    texts = [f.text for f in people.facts_for_person(tmp_path, "小諾")]
    assert sum(t.startswith(sleep_summary.LAST_CHAT_PREFIX) for t in texts) == 1
    assert "小諾在軟體領域上班" in texts


def test_write_sleep_summaries_no_people_or_transcript_is_noop(tmp_path) -> None:
    client, fake = _client("{}")
    deps = _deps(instance_path=str(tmp_path))  # nobody recognized
    assert asyncio.run(sleep_summary.write_sleep_summaries(deps, client=client)) == 0
    assert not fake.calls


def test_write_sleep_summaries_survives_client_failure(tmp_path) -> None:
    client, _ = _client(RuntimeError("boom"))
    deps = _visit_deps(tmp_path, "小諾")
    assert asyncio.run(sleep_summary.write_sleep_summaries(deps, client=client)) == 0
    assert people.facts_for_person(tmp_path, "小諾") == []


def test_write_sleep_summaries_ignores_unlisted_names_and_bad_json(tmp_path) -> None:
    client, _ = _client(json.dumps({"路人": "不該寫入", "小諾": ""}))
    deps = _visit_deps(tmp_path, "小諾")
    assert asyncio.run(sleep_summary.write_sleep_summaries(deps, client=client)) == 0
    assert people.facts_for_person(tmp_path, "路人") == []


def test_write_sleep_summaries_disabled_by_env(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MEMORY_LAST_CHAT_ENABLED", "false")
    client, fake = _client("{}")
    deps = _visit_deps(tmp_path, "小諾")
    assert asyncio.run(sleep_summary.write_sleep_summaries(deps, client=client)) == 0
    assert not fake.calls


def test_format_last_chat_fact_has_prefix_and_date() -> None:
    text = sleep_summary.format_last_chat_fact("聊到考試")
    assert text.startswith("上次聊天（") and text.endswith("：聊到考試".replace("：", "：聊到考試")[-4:]) or "：聊到考試" in text
```

(Adjust the last assertion to simply `assert "月" in text and text.endswith("聊到考試")` — keep it plain.) Add `from reachy_companion import people` and `from reachy_companion.tools.who_is_this import WhoIsThis` to the imports.

- [ ] **Step 2: Run to verify failure.** Expected: FAIL (`write_sleep_summaries` missing).

- [ ] **Step 3: Implement** in `sleep_summary.py`:

```python
import asyncio
import json
import time
from typing import Any

from reachy_companion.audio.envparse import env_bool, env_float
from reachy_companion.people import add_person_fact, facts_for_person, forget_person_fact

_SYSTEM_PROMPT = (
    "你是機器人 Reachy 的記憶整理員。根據這次的對話記錄，為列出的每個人各寫一句"
    "「上次聊天」摘要（不超過 50 字，臺灣繁體中文）。優先寫：聊了什麼主題、"
    "還沒有結果的事（考試、計畫、承諾）。只根據記錄，不要編造。"
    "記錄裡看不出是誰說的：多人在場時寫「大家聊了什麼」的主題摘要，"
    "除非記錄裡明白寫出名字，否則不要把某句話歸給特定的人。"
    '只輸出 JSON 物件：{"人名": "摘要"}；名單外的人不要出現；沒有內容可寫的人給空字串。'
)


def _default_model() -> str:
    import os

    return os.getenv("MEMORY_LAST_CHAT_MODEL", "").strip() or "gpt-5-mini"


def format_last_chat_fact(summary: str) -> str:
    """`上次聊天（M月D日）：<summary>` — the prefix is the supersession key."""
    stamp = time.strftime("%m月%d日").lstrip("0").replace("月0", "月")
    return f"{LAST_CHAT_PREFIX}（{stamp}）：{summary.strip()}"


async def write_sleep_summaries(deps: ToolDependencies, *, client: Any | None = None) -> int:
    """Summarize the visit for every recognized person. Never raises."""
    try:
        if not env_bool("MEMORY_LAST_CHAT_ENABLED", True):
            logger.info("Sleep summary disabled by MEMORY_LAST_CHAT_ENABLED.")
            return 0
        names = sorted(deps.recognized_people)
        transcript = list(deps.session_transcript)
        if not names or not transcript:
            return 0
        if client is None:
            from reachy_companion.hanova.images import build_client

            client = build_client()
        if client is None:
            logger.info("Sleep summary skipped: no OpenAI client available.")
            return 0
        lines = "\n".join(f"{'user' if role == 'user' else 'reachy'}: {text}" for role, text in transcript)
        user_prompt = f"在場的人：{'、'.join(names)}\n\n對話記錄：\n{lines}"
        timeout_s = env_float("MEMORY_LAST_CHAT_TIMEOUT_S", 8.0, lo=1.0, hi=30.0)
        async with client:
            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model=_default_model(),
                    messages=[
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    response_format={"type": "json_object"},
                ),
                timeout=timeout_s,
            )
        raw = response.choices[0].message.content or ""
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            logger.warning("Sleep summary: model returned non-object JSON; skipping.")
            return 0
        written = 0
        for name in names:
            summary = parsed.get(name)
            if not isinstance(summary, str) or not summary.strip():
                continue
            written += await asyncio.to_thread(_replace_last_chat_fact, deps.instance_path, name, summary)
        return written
    except Exception as exc:  # noqa: BLE001 — memory must never break shutdown
        logger.warning("Sleep summary failed: %s", type(exc).__name__)
        return 0


def _replace_last_chat_fact(instance_path: Any, name: str, summary: str) -> int:
    """ADD first, then forget the old copies — a failure can never leave zero last-chat facts.

    forget_person_fact is substring-match, newest-candidate-first (people.py:381-405):
    passing an OLD fact's full text selects that fact and cannot match the new one
    (different date/summary). New text identical to an old one is returned unstored
    by add_person_fact's duplicate check — then there is nothing to forget.
    """
    facts = facts_for_person(instance_path, name)
    old_texts = [f.text for f in facts if f.text.startswith(LAST_CHAT_PREFIX)]
    new_text = format_last_chat_fact(summary)
    # forget_person_fact is SUBSTRING match, newest-candidate-first (people.py:405).
    # Two cases force forget-FIRST (tiny failure window; worst loss = last week's
    # callback, never a real fact):
    #  - at the 20-fact cap, add-first would evict a REAL fact;
    #  - an old text that is a substring of the new one (same-day re-sleep) would
    #    make the post-add forget delete the NEW fact instead of the old.
    forget_first = old_texts and (
        len(facts) >= MAX_FACTS_PER_PERSON or any(old in new_text for old in old_texts)
    )
    if forget_first:
        for old in old_texts:
            forget_person_fact(instance_path, name, query=old)
    stored = add_person_fact(instance_path, name, new_text)
    if stored is None:
        return 0
    if not forget_first:
        for old in old_texts:
            if old != stored.text and old not in stored.text:
                forget_person_fact(instance_path, name, query=old)
    return 1
```

Add a test for the collision case: seed 「上次聊天（X月Y日）：聊到考試」, write a same-day summary 「聊到考試和音樂」 (old text is a substring of the new) — assert exactly one last-chat fact remains and it is the NEW one.

(Import `MAX_FACTS_PER_PERSON` from `reachy_companion.people`.) Add one more test: seed 19 real facts + 1 old `上次聊天` fact (20 total, at cap), run the writer — assert all 19 real facts survive and exactly one NEW last-chat fact exists.

(`ToolDependencies.instance_path` — confirm the attribute name on the dataclass; `tools/remember.py:51` uses `deps.instance_path`, so it exists. Keep `async with client` in production — `hanova/images.py:31` requires context management — and keep the fake a real class so `__aenter__` resolves on the type, as written in Step 1.)

- [ ] **Step 4: Run tests.** `python -m pytest tests/test_sleep_summary.py -q` — Expected: PASS. `ruff check` + `mypy --strict` clean.
- [ ] **Step 5: Commit.** `git add reachy_companion/src/reachy_companion/sleep_summary.py reachy_companion/tests/test_sleep_summary.py && git commit -m "feat(memory): sleep-time last-chat summarizer writing superseding person facts"`

---

### Task 5: Shutdown hook + env docs

**Files:**
- Modify: `reachy_companion/src/reachy_companion/huggingface_realtime.py` (`__init__` ~:425 area; `async def shutdown()` at ~:2449)
- Modify: `reachy_companion/src/reachy_companion/main.py` (`go_to_sleep_and_stop_app` closure, ~:314)
- Modify: `reachy_companion/.env.example`, `README.md` (Configuration table, lines ~158-181)

**Interfaces:**
- Consumes: `write_sleep_summaries(deps) -> int` (Task 4); `deps.sleep_requested` (Task 2).

- [ ] **Step 1: Set the gate at the sleep source.** In `main.py`'s `go_to_sleep_and_stop_app` closure (~:314), first statement inside the guarded body: `deps.sleep_requested = True`. This is the ONLY writer; settings/backend restarts (`console.py:307`, `:697`) reach `shutdown()` without it and must not trigger a summary.

- [ ] **Step 2: Wire the hook.** In `__init__`, add `self._sleep_summary_done = False`. At the **top** of `shutdown()` (before `connection.close()` and queue drain), add:

```python
        if self.deps.sleep_requested and not self._sleep_summary_done:
            self._sleep_summary_done = True
            written = await write_sleep_summaries(self.deps)
            if written:
                logger.info("Sleep summary: wrote last-chat fact for %d person(s).", written)
```

Import `write_sleep_summaries` alongside the Task 3 import. `write_sleep_summaries` never raises and is timeout-bounded, so no extra guard is needed; the flag makes a double `shutdown()` harmless. Accepted limitation (goes into D-027): a dashboard-issued stop bypasses the voice tool, so no summary is written for that visit.

- [ ] **Step 3: Verify by running the full robot suite.** `python -m pytest -q` — Expected: same pass/skip counts as the pre-plan baseline plus the new tests (record counts in the task notes). If any handler test constructs the handler and calls `shutdown()`, it must still pass — the gate is `sleep_requested` (default False), so no test path summarizes accidentally.
- [ ] **Step 4: Document the three env keys.** `.env.example`: append a commented block — `MEMORY_LAST_CHAT_ENABLED` (default true; false disables the sleep-time summary), `MEMORY_LAST_CHAT_MODEL` (default gpt-5-mini), `MEMORY_LAST_CHAT_TIMEOUT_S` (default 8.0, clamp 1–30). `README.md`: add the same three rows to the Configuration table with the same wording style as `FACE_*` rows.
- [ ] **Step 5: Commit.** `git add -A reachy_companion/src reachy_companion/.env.example README.md && git commit -m "feat(memory): write last-chat summaries at sleep-gated handler shutdown; document MEMORY_LAST_CHAT_* knobs"`

---

### Task 6: Backend `replace_facts`

**Files:**
- Modify: `companion_backend/backend/store.py` (next to `add_fact`/`delete_fact`)
- Test: wherever the existing `add_fact` store tests live (check `companion_backend/tests/`; add `test_store_replace_facts.py` if there is no store-level test module)

**Interfaces:**
- Consumes: existing `store` internals (`BackendFact`, `BackendPerson`, `_now_ms`, load/save helpers — mirror `add_fact`'s implementation shape exactly).
- Produces: `replace_facts(settings: Settings, person_id: str, texts: Sequence[str], *, preserve_updated_at: bool = False) -> BackendPerson` — replaces the person's whole fact list; `texts[0]` becomes the NEWEST fact; normalizes each text the way the store's existing fact path does (reuse its helper; strip + drop empties and case-insensitive duplicates). **NO 20-item cap** — the Mac store is deliberately uncapped ("No caps", `store.py:16`; `test_store.py:542` pins 25 facts persisting); only the robot projection caps at 20. Unknown `person_id` raises the same error type the store's other id-addressed mutators raise (find it where `rename_person`/`delete_fact` handle an unknown id — `get_person` merely returns `None` and is NOT the model). `preserve_updated_at=True` keeps the person's `updated_at` AND their position in the stored people list unchanged — do NOT route this branch through the store's move-to-front mutation path (`store.py:639` `_mutate` reorders, and projection's stable sort preserves tie order, `projection.py:107`); write the person in place. Default `False` bumps and reorders like any edit. Test: with `preserve_updated_at=True`, `[p.id for p in list_people(...)]` is identical before and after.

- [ ] **Step 1: Write failing tests** (constructor/fixture shapes copied from the existing store tests — the `settings` fixture in `companion_backend/tests/conftest.py:15-30`):

```python
def test_replace_facts_replaces_and_orders_newest_first(settings) -> None:
    person = store.create_person(settings, "小諾")
    store.add_fact(settings, person.id, "舊事實")
    updated = store.replace_facts(settings, person.id, ["最新的事", "第二新"])
    assert [f.text for f in updated.facts] == ["最新的事", "第二新"]

def test_replace_facts_normalizes_dedupes_and_stays_uncapped(settings) -> None:
    person = store.create_person(settings, "小諾")
    texts = ["  a  ", "", "A", *[f"f{i}" for i in range(25)]]
    updated = store.replace_facts(settings, person.id, texts)
    assert [f.text for f in updated.facts][0] == "a"
    assert len(updated.facts) == 26  # a + f0..f24 — the Mac store is uncapped (store.py:16)

def test_replace_facts_preserve_updated_at(settings) -> None:
    person = store.create_person(settings, "小諾")
    before = store.get_person(settings, person.id).updated_at
    updated = store.replace_facts(settings, person.id, ["x"], preserve_updated_at=True)
    assert updated.updated_at == before

def test_replace_facts_unknown_person_raises(settings) -> None:
    with pytest.raises(<the error type rename_person raises for an unknown id — read store.py>):
        store.replace_facts(settings, "nope", ["x"])
```

(Fill `<same error type>` with the real class after reading `get_person` — it is defined in `store.py`; assert the ordering assumption against how `facts` are stored: if the existing store keeps facts oldest-first, ADAPT `replace_facts` and this test so that **after `projection.project()` the robot's `facts_for_person` returns `texts` in the same order** — that contract is pinned by Step 4.)

- [ ] **Step 2: Run to verify failure.** `cd companion_backend && ../reachy_companion/.venv/bin/python -m pytest tests -k replace_facts -q` — Expected: FAIL.
- [ ] **Step 3: Implement `replace_facts`** following `add_fact`'s exact load-mutate-save pattern (same locking, same `_now_ms` usage, one save).
- [ ] **Step 4: Round-trip ordering test** (this is the ordering oracle — write it in the same test module):

```python
def test_replace_facts_projects_to_robot_newest_first(settings, tmp_path) -> None:
    person = store.create_person(settings, "小諾")
    store.replace_facts(settings, person.id, ["最新", "其次", "最舊"])
    projection.project(settings, tmp_path)
    from reachy_companion import people as robot_people
    assert [f.text for f in robot_people.facts_for_person(tmp_path, "小諾")] == ["最新", "其次", "最舊"]
```

Run it; if order comes out reversed, flip the storage order inside `replace_facts` (not in projection) until this test passes.
- [ ] **Step 5: Run backend suite + commit.** `../reachy_companion/.venv/bin/python -m pytest tests -q` — Expected: PASS. `git add -A companion_backend && git commit -m "feat(backend): store.replace_facts with robot-order round-trip pinned"`

---

### Task 7: Backend consolidation module

**Files:**
- Create: `companion_backend/backend/consolidate.py`
- Test: `companion_backend/tests/test_consolidate.py`

**Interfaces:**
- Consumes: `store.list_people(settings)`, `store.replace_facts(settings, person_id, texts)` (Task 6), `BackendPerson(id, name, facts, ...)`.
- Produces:

```python
@dataclass(frozen=True)
class PersonConsolidation:
    person_id: str
    name: str
    before: tuple[str, ...]
    after: tuple[str, ...]
    changed: bool
    error: str | None  # None on success; short reason on skip

def build_llm_client() -> Any | None          # sync openai.OpenAI from OPENAI_API_KEY; None when unset/SDK missing (mirror hanova.images.build_client's tolerance)
def consolidate_person(client: Any, model: str, name: str, facts: Sequence[str]) -> list[str] | None
def run(settings: Settings, *, apply: bool, only: str | None = None, client: Any | None = None, model: str | None = None) -> list[PersonConsolidation]
```

- [ ] **Step 1: Write failing tests** (`test_consolidate.py`; fake sync client analogous to Task 4's fake, non-async):

```python
def test_consolidate_person_returns_validated_list(fake_client_returning({"facts": ["以前想當舞者，現在是外科醫師", "喜歡寫歌"]})):
    out = consolidate.consolidate_person(client, "gpt-5-mini", "雲霓", ["想當舞者", "是外科醫師", "喜歡寫歌", "喜歡寫歌"])
    assert out == ["以前想當舞者，現在是外科醫師", "喜歡寫歌"]

def test_consolidate_person_rejects_bad_payloads():
    # non-JSON, JSON non-object, "facts" not a list of str, >20 items, item >280 chars,
    # and a client that raises — each returns None
    ...  # one fake per case, six asserts, written out concretely

def test_run_dry_run_never_writes(settings):
    # seed one person via store; run(apply=False, client=fake) — store unchanged, result.changed True

def test_run_apply_writes_through_replace_facts(settings):
    # run(apply=True, client=fake) — store facts equal the fake's output

def test_run_keeps_newest_last_chat_fact_first_and_dedupes(settings):
    # seed facts including TWO last-chat facts ("上次聊天（8月1日）：…" older, "上次聊天（8月29日）：…" newer,
    # the stale-resurrection case backend/robot.py:548 can produce); fake returns a list WITHOUT them;
    # run() must re-insert ONLY the newest at position 0 — assert result.after[0].startswith("上次聊天（8月29日）")
    # and sum(t.startswith("上次聊天") for t in result.after) == 1

def test_run_apply_preserves_updated_at(settings):
    # capture person.updated_at; run(apply=True, client=fake); assert unchanged
    # (replace_facts called with preserve_updated_at=True — bulk pass must not reshuffle projection recency)

def test_run_no_client_reports_error(settings):
    # run(client=None) with no OPENAI_API_KEY in env -> every PersonConsolidation.error == "no_client", changed False

def test_full_sync_cycle_heals_stale_last_chat(settings, tmp_path):
    # The riskiest cycle (review finding 14): Mac person at 20+ facts including a stale
    # last-chat; simulate the robot's replacement by writing the robot-side store in tmp_path
    # (robot people.add_person_fact with the NEW last-chat, stale one absent); monkeypatch the
    # fetch used by import to read tmp_path's files; apply_import; then run(apply=True, client=fake
    # that echoes facts back unchanged); then projection.project(settings, out_dir) —
    # assert the projected people.v1.json contains exactly ONE 上次聊天 fact and it is the new one.
```

Write each concretely (the `...` above marks enumeration, not omission — every case gets its own fake payload and assert).

- [ ] **Step 2: Run to verify failure.** Expected: FAIL (module missing).
- [ ] **Step 3: Implement.** The system prompt, verbatim:

```python
_SYSTEM_PROMPT = (
    "你是家用機器人 Reachy 的記憶整理員。整理一個人的記憶清單：\n"
    "1. 合併重複或近似的事實。\n"
    "2. 互相矛盾的事實合併成一條「以前…，現在…」。\n"
    "3. 刪除一次性的瑣事；保留穩定特質、進行中的事、人際關係。\n"
    "4. 按「聊天時最有用」排序，最有用的放最前面。\n"
    "5. 只能重組既有內容，絕對不可以新增資訊。\n"
    "6. 最多 20 條，每條不超過 280 字，臺灣繁體中文。\n"
    '只輸出 JSON：{"facts": ["...", "..."]}'
)
```

`consolidate_person`: build messages (system + `f"{name} 的記憶：\n" + numbered facts`), call `client.chat.completions.create(model=..., messages=..., response_format={"type": "json_object"})`, validate exactly the cases the tests enumerate, return the list or `None`. `run`: iterate `list_people(settings)` (filter by `only` via the same name normalization `store` uses); pop ALL `上次聊天` facts before sending to the LLM, **drop any `上次聊天`-prefixed string the LLM returns** (the model never gets to author one; add a test: fake returns a list containing `"上次聊天（1月1日）：偽造"` — it must not appear in `after`), then re-prepend only the NEWEST popped one at position 0 (this dedupe is the designed healer for the stale-resurrection sync hole, `backend/robot.py:548`); `changed = tuple(after) != tuple(before)`; `apply and changed and error is None` → `replace_facts(settings, person_id, after, preserve_updated_at=True)`. Model default: `os.getenv("COMPANION_CONSOLIDATE_MODEL", "").strip() or "gpt-5-mini"`.
- [ ] **Step 4: Run tests.** Expected: PASS; ruff + mypy clean on `companion_backend/backend`.
- [ ] **Step 5: Commit.** `git add companion_backend/backend/consolidate.py companion_backend/tests/test_consolidate.py && git commit -m "feat(backend): LLM consolidation pass over person facts (dry-run + apply)"`

---

### Task 8: Consolidation CLI

**Files:**
- Create: `companion_backend/scripts/consolidate.py`
- Test: `companion_backend/tests/test_consolidate.py` (CLI-level test via `main(argv, client=...)`)

**Interfaces:**
- Consumes: `consolidate.run`, `config.load_settings`.
- Produces: `main(argv: list[str] | None = None, *, client: Any | None = None) -> int` (exit code; 0 = ok, 2 = nothing consolidated because no client, 3 = backend running).

- [ ] **Step 1: Failing test.**

```python
def test_cli_dry_run_prints_diff_and_exits_zero(settings, capsys, monkeypatch) -> None:
    monkeypatch.setenv("COMPANION_BACKEND_DATA", str(settings.data_dir))
    # seed one person whose facts the fake client changes
    code = cli_consolidate.main([], client=fake_client)
    out = capsys.readouterr().out
    assert code == 0 and "雲霓" in out and "-" in out and "+" in out
```

- [ ] **Step 2: Run to verify failure.** Expected: FAIL.
- [ ] **Step 3: Implement.** argparse: `--apply` (store_true; default dry-run), `--person NAME`. Body: **probe guard first, failing CLOSED, on every plausible bind** — the store lock is process-local (`store.py:93`), so a CLI write while the backend serves is a lost-update race — and the documented production bind is the *tailnet IP, not loopback* (`run.sh:8`, README). Build the candidate host set: `127.0.0.1`, `os.getenv("COMPANION_BACKEND_HOST")` when set, and the output of `subprocess.run(["tailscale", "ip", "-4"], ...)` when the binary exists (ignore its failure). Probe `GET http://{host}:8710/api/config` (2 s timeout, httpx — already a backend dep) for each. A host that answers ANYTHING → "backend is running — stop it first (the store lock is per-process)", return 3. A host that times out or errors any way other than connection-refused → also return 3 ("cannot prove the backend is stopped"). Continue only when EVERY candidate host refuses the connection (`httpx.ConnectError`). Then `settings = load_settings()`, `results = run(settings, apply=args.apply, only=args.person, client=client)`; for each changed person print a `difflib.unified_diff` of before/after fact lists headed by the name; print a one-line tally (`N person(s), M changed, applied: yes/no`); return 2 if every result has `error == "no_client"` else 0. Follow `scripts/selftest.py`'s structure for settings/bootstrapping (it is the existing CLI precedent). Add CLI tests: monkeypatch the probe to report "running" → `main([]) == 3` and the store untouched; monkeypatch it to time out → also 3 (fail closed).

  Also add two optional flags so the whole flow runs with the backend STOPPED (the UI import/push needs the server the guard forbids): `--import-first` calls `robot.import_from_robot(settings)` then `robot.apply_import(settings, diff)` before consolidating; `--push-after` calls `robot.push(settings)` after a successful `--apply` (refuse `--push-after` without `--apply`). Both reuse the existing `backend/robot.py` functions directly — no new sync logic. Test each with the `robot.*` functions monkeypatched (the existing backend tests already monkeypatch `robot.*` — same pattern), asserting call order import → consolidate → push. Document both flows in the README section (Step 5): UI flow (backend up for import/push, down for consolidate) and one-shot CLI flow (backend down throughout).
- [ ] **Step 4: Run tests + full backend suite.** Expected: PASS.
- [ ] **Step 5: Document + commit.** Add a "Consolidation" section to `companion_backend/README.md`: operator flow **import → `python scripts/consolidate.py` (review) → `--apply` → push**, env `COMPANION_CONSOLIDATE_MODEL`, needs `OPENAI_API_KEY`. `git add companion_backend/scripts/consolidate.py companion_backend/tests/test_consolidate.py companion_backend/README.md && git commit -m "feat(backend): consolidate CLI with dry-run diff"`

---

### Task 9: Records, gates, and closeout

**Files:**
- Modify: `DECISIONS.md`, `feature_list.json`, `progress.md`, `README.md` (if not already), `reachy_companion/.env.example` (verify)

- [ ] **Step 1: DECISIONS.md D-027.** Record: engagement-memory design — visit-scoped transcript tail + recognized set on ToolDependencies; `上次聊天` prefix as supersession key (no PersonFact schema change, backend-sync compatible); summarizer at handler shutdown, `gpt-5-mini` default, never-raise posture; backend consolidation operator-run (no scheduler), `replace_facts` ordering pinned by round-trip test; SQLite/retrieval deferred post-POC (link the 2026-08-29 research).
- [ ] **Step 2: feature_list.json rows.** Add `MEMORY-LAST-CHAT` (verification: two-session on-robot test — visit with a recognized person mentioning an ongoing thing, sleep, journal shows `Sleep summary: wrote last-chat fact for 1 person(s).`, `people.v1.json` holds one `上次聊天` fact, next recognized boot greeting references it), `MEMORY-OPEN-LOOPS` (live listening: remember calls prefer ongoing threads), `BACKEND-CONSOLIDATE` (seed a duplicate + contradiction on the Mac store, dry-run shows merge, `--apply` + push, robot facts reflect it). State: `implemented-unverified` with exact blockers.
- [ ] **Step 3: Full gates.** Robot: `cd reachy_companion && python -m pytest -q` (record counts), `ruff check`, `mypy --strict`. Backend: full pytest. Paste counts into `progress.md`.
- [ ] **Step 4: progress.md** current-state update + next-action (deploy the sixteenth install via the `reachy-deploy` ritual to put Tasks 2–5 on the robot; persona from Task 1 syncs without it).
- [ ] **Step 5: Commit.** `git add DECISIONS.md feature_list.json progress.md README.md reachy_companion/.env.example && git commit -m "docs: D-027 engagement memory; feature rows and state"`

---

## Review Log (Codex)

### Round 1 (2026-08-29, 15 findings)

- **1 (high, stale last-chat resurrection through the sync cap hole): ACCEPTED** — consolidation now dedupes `上次聊天` keep-newest every run (§D3, Task 7) and the full cycle is pinned by `test_full_sync_cycle_heals_stale_last_chat`.
- **2 (high, replace_facts must not cap the uncapped Mac store): ACCEPTED** — cap removed from the spec; uncapped behavior pinned by test (Task 6).
- **3 (high, CLI vs running backend lost-update race): ACCEPTED** — probe guard added (exit 3 while the backend answers); an in-server route was rejected as scope growth for an operator-run batch.
- **4 (high, shutdown() also fires on settings/backend restarts): ACCEPTED** — summary gated on `deps.sleep_requested`, set only by the go_to_sleep closure (Tasks 2/5); dashboard-stop writes no summary, recorded as an accepted limitation for D-027.
- **5 (high, multi-person attribution unsound): ACCEPTED (mitigated)** — summarizer prompt now mandates topic-level summaries and forbids per-person attribution unless the transcript names the speaker; diarization rejected as a non-goal.
- **6 (high, party-mode denied turns recorded): ACCEPTED** — the ~:2196-2204 denied push is explicitly excluded; record sites reduced to three (Tasks 2-3).
- **7 (high, delete-then-add can lose the only last-chat fact): ACCEPTED** — add-then-forget, with a forget-first branch only at the 20-fact cap so a real fact is never evicted; both paths tested (Task 4).
- **8 (medium, forget_person_fact is substring-match, not exact): ACCEPTED** — wording corrected; the full-old-text query is shown to select only the old fact.
- **9/10 (medium, async-with contract and invalid SimpleNamespace fake): ACCEPTED** — production keeps `async with`; the fake is a real class with `__aenter__/__aexit__` on the type.
- **11 (medium, get_person does not raise): ACCEPTED** — spec now points at the store's id-addressed mutators for the not-found error type.
- **12 (medium, updated_at bump reshuffles projection recency): ACCEPTED** — `preserve_updated_at=True` added; consolidation uses it; tested.
- **13 (medium, cross-person links surface one-sided): ACCEPTED AS RECORDED LIMITATION** — per-person retrieval is the designed scope (PRD non-goals); D-027 notes it; no plan change.
- **14 (medium, missing riskiest sync test): ACCEPTED** — `test_full_sync_cycle_heals_stale_last_chat` added (Task 7).
- **15 (low, 3-vs-4 call-site inconsistency): ACCEPTED** — resolved to exactly 3 by finding 6; grep expectation updated.

### Round 2 (2026-08-29, 5 findings — all accepted)

- **1 (high, same-day prefix collision — post-add forget can delete the NEW fact): ACCEPTED** — forget-first now also triggers when any old text is a substring of the new; post-add loop additionally skips substrings; collision test added (Task 4).
- **2 (medium, solo-barge ~:1040 is a rolled-back backchannel, not a committed turn): ACCEPTED** — site dropped; exactly 2 record sites remain, grep expectation updated (Tasks 2-3).
- **3 (medium, preserve_updated_at could still reshuffle via _mutate's move-to-front + stable tie sort): ACCEPTED** — spec now requires in-place write preserving list position, pinned by an id-order test (Task 6).
- **4 (high, probe guard failed open on timeout): ACCEPTED** — fail closed: only connection-refused continues; response/timeout/other all exit 3 (Task 8).
- **5 (medium, flow conflict — import/push need the server the guard forbids): ACCEPTED** — CLI gains `--import-first`/`--push-after` reusing `backend/robot.py` functions directly, giving a backend-stopped one-shot flow; both flows documented (Task 8).

### Round 3 (2026-08-29, 3 findings — all accepted)

- **1 (high, probe misses a tailnet-bound backend): ACCEPTED** — the guard now probes every plausible bind (loopback, `COMPANION_BACKEND_HOST`, and the live `tailscale ip -4`), still failing closed (Task 8).
- **2 (medium, `git commit -am` never stages created files): ACCEPTED** — every commit step now lists explicit `git add` paths.
- **3 (medium, the LLM can author a fake `上次聊天` fact): ACCEPTED** — LLM-returned `上次聊天`-prefixed strings are dropped before the newest real one is re-prepended; tested (Task 7).

Review complete: 3 rounds, 23 findings, 22 accepted (1 accepted as a recorded limitation). Per CLAUDE.md the round cap is reached; execution may proceed.
