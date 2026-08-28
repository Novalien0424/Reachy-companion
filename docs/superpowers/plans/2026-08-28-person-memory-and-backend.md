# Person Memory & Management Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recognition-aware three-way boot greeting with per-person memory on the robot, still-pose enrollment, and a Mac-side management backend (people, photos, facts, sync, robot control).

**Architecture:** Robot side gains one sibling store (`people.v1.json`) plus surgical edits to the existing greeting/tool code — no new subsystem, no new dependencies, no new RPC methods. Mac side gains `companion_backend/`, a FastAPI app + vanilla-JS UI that owns people/photos/facts, computes SFace embeddings locally, and pushes two derived JSON files to the robot over the existing key-authenticated scp channel. Spec section numbers referenced as §N.

**Tech Stack:** Python 3.12 (`reachy_companion/.venv` — the required dev venv), FastAPI/uvicorn (already deps), numpy + onnxruntime (already deps), `imageio_ffmpeg` for photo decode (already a dep), vanilla ES-module JS (no build step). **Zero new dependencies anywhere.**

**Spec:** `docs/superpowers/specs/2026-08-28-person-memory-and-backend-design.md`

## Global Constraints

- Reuse-first (PRD §10): adapt existing code; never recreate camera/motion/audio paths.
- `memory.v1.json` / `MemoryFact.to_json` is a locked external contract (D-013) — do not touch its schema.
- `IdentificationStatus` / `IdentificationReason` Literals in `face_id.py` are closed (D-014) — do not add members.
- The two profile metadata field sets (`profile_store._PROFILE_METADATA_FIELDS`, `persona.PERSONA_METADATA_FIELDS`) stay closed; `prompts.get_session_greeting_prompt()` stays zero-arg.
- Recognition stays one bounded wake hook + explicit tools (D-013/D-024 privacy property). No continuous scanning.
- No photos on or to the robot, ever. Robot receives only `faces.v1.json` + `people.v1.json`.
- No new conversational tools (D-019: the 41-tool array must not grow).
- scp/ssh must be plain key-auth `ssh -o BatchMode=yes` / `scp` — **never wrap in `expect`** (recorded deploy lesson).
- Robot deploys only via the `reachy-deploy` skill (D-009); this plan ends before deploy.
- All work from `reachy_companion/` runs in `.venv` (Python 3.12). Gate: `python -m pytest`, `ruff check`, `mypy` strict all green.
- Every new `FACE_*`/env knob must be documented in `reachy_companion/.env.example` (an env-docs test enumerates knobs: `tests/test_openai_realtime_config.py:1034-1058`).
- Commit after every task (repo root is the git root; branch `person-memory-backend` off `main` first).

---

### Task 0: Branch

- [ ] **Step 1:** `git checkout -b person-memory-backend` at `/Users/novalien0424/Reachy-companion`.

---

### Task 1: `people.py` — the per-person fact store

**Files:**
- Create: `reachy_companion/src/reachy_companion/people.py`
- Test: `reachy_companion/tests/test_people.py`

**Interfaces:**
- Consumes: `faces.normalize_face_name` (name rules identical to the face store), `memory.normalize_memory_text` (fact rules identical to the memory store).
- Produces (later tasks rely on these exact signatures):
  - `PEOPLE_FILENAME = "people.v1.json"`, `SCHEMA_VERSION = 1`, `MAX_PEOPLE = 12`, `MAX_FACTS_PER_PERSON = 20`, `MAX_FACT_CHARS = 280`
  - `@dataclass(frozen=True) PersonFact(id: str, text: str, created_at: int)` with `to_json() -> dict[str, object]` → `{"id","text","createdAt"}`
  - `@dataclass(frozen=True) PersonRecord(id: str, face_id: str | None, name: str, facts: tuple[PersonFact, ...], created_at: int, updated_at: int)` with `to_json()` → `{"id","faceId","name","facts":[…],"createdAt","updatedAt"}`
  - `@dataclass(frozen=True) ForgetPersonFactResult(removed: PersonFact | None, candidates: tuple[PersonFact, ...])`
  - `people_path_for_instance(instance_path: str | Path | None = None) -> Path`
  - `list_people(instance_path=None) -> list[PersonRecord]` (most recently updated first)
  - `facts_for_person(instance_path, name: str, *, limit: int | None = None) -> list[PersonFact]` (newest first; name match case-/whitespace-insensitive like `faces.upsert_face`)
  - `add_person_fact(instance_path, name: str, text: str, *, face_id: str | None = None) -> PersonFact | None` (creates the person record if missing; dedupes exact case-insensitive fact text within the person; sets `face_id` only if the record has none)
  - `forget_person_fact(instance_path, name: str, *, query: str) -> ForgetPersonFactResult` (case-insensitive substring within that person's facts; removes `candidates[0]`)
  - `upsert_person(instance_path, name: str, *, face_id: str | None = None) -> PersonRecord | None`
  - `forget_person(instance_path, name: str) -> PersonRecord | None`
  - `clear_people(instance_path=None) -> None`

**Implementation notes (module body):** copy the idioms of `faces.py`/`memory.py` verbatim — module `_STORE_LOCK = threading.Lock()`, `_make_id()` with prefixes `p_` (person) / `m_` (fact), `_now_ms()`, tolerant `_record_from_json` (drop bad records, never reject the file; `faceId` missing/None tolerated; a non-str `faceId` reads as None), `_read_people_file` returning `[]` on `FileNotFoundError`/`OSError`/`ValueError`/`json.JSONDecodeError`, `_write_people_file` with the `tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")` + `tmp.replace(path)` + `finally: unlink` pattern, envelope `{"version": SCHEMA_VERSION, "people": [...]}`. Facts within a record newest-first, capped at `MAX_FACTS_PER_PERSON` on read and write; people sorted `updated_at` desc, capped `MAX_PEOPLE` with LRU eviction on insert past the cap (the `faces.upsert_face` pattern). Adding a fact updates the person's `updated_at`. Module docstring must state: deliberate sibling of `memory.py` (D-013 — `MemoryFact.to_json` is a locked mobile-app contract) and of `faces.py`; no images; same reinstall-wipe/backup-restore lifecycle.

- [ ] **Step 1: Write the failing tests** in `reachy_companion/tests/test_people.py` (model them on `tests/test_faces.py` / `tests/test_memory.py` style — `tmp_path` as instance path):

```python
from pathlib import Path

from reachy_companion import people


def test_round_trip_add_and_list(tmp_path: Path) -> None:
    fact = people.add_person_fact(tmp_path, "Lena", "Likes oolong tea", face_id="f_1_abc")
    assert fact is not None and fact.text == "Likes oolong tea"
    records = people.list_people(tmp_path)
    assert len(records) == 1
    record = records[0]
    assert record.name == "Lena" and record.face_id == "f_1_abc"
    assert [f.text for f in record.facts] == ["Likes oolong tea"]


def test_facts_for_person_is_newest_first_and_limited(tmp_path: Path) -> None:
    for i in range(4):
        people.add_person_fact(tmp_path, "Lena", f"fact {i}")
    facts = people.facts_for_person(tmp_path, "  lena ", limit=2)
    assert [f.text for f in facts] == ["fact 3", "fact 2"]


def test_fact_dedupe_and_cap(tmp_path: Path) -> None:
    people.add_person_fact(tmp_path, "Lena", "Same fact")
    dup = people.add_person_fact(tmp_path, "Lena", "same FACT")
    assert dup is not None
    assert len(people.facts_for_person(tmp_path, "Lena")) == 1
    for i in range(people.MAX_FACTS_PER_PERSON + 5):
        people.add_person_fact(tmp_path, "Lena", f"n{i}")
    assert len(people.facts_for_person(tmp_path, "Lena")) == people.MAX_FACTS_PER_PERSON


def test_forget_person_fact_by_substring(tmp_path: Path) -> None:
    people.add_person_fact(tmp_path, "Lena", "Has a dog named Mochi")
    result = people.forget_person_fact(tmp_path, "Lena", query="mochi")
    assert result.removed is not None and "Mochi" in result.removed.text
    assert people.facts_for_person(tmp_path, "Lena") == []


def test_corrupt_file_reads_as_empty(tmp_path: Path) -> None:
    people.people_path_for_instance(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    people.people_path_for_instance(tmp_path).write_text("{not json", encoding="utf-8")
    assert people.list_people(tmp_path) == []


def test_bad_record_is_dropped_not_fatal(tmp_path: Path) -> None:
    path = people.people_path_for_instance(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '{"version": 1, "people": [ {"bogus": true}, '
        '{"id": "p_1_a", "faceId": null, "name": "Ok", '
        '"facts": [{"id": "m_1_a", "text": "kept", "createdAt": 5}], '
        '"createdAt": 5, "updatedAt": 5} ]}',
        encoding="utf-8",
    )
    records = people.list_people(tmp_path)
    assert [r.name for r in records] == ["Ok"]


def test_person_eviction_past_max_people(tmp_path: Path) -> None:
    for i in range(people.MAX_PEOPLE + 1):
        people.add_person_fact(tmp_path, f"Person {i}", "x")
    records = people.list_people(tmp_path)
    assert len(records) == people.MAX_PEOPLE
    assert all(r.name != "Person 0" for r in records)  # LRU evicted
```

- [ ] **Step 2:** Run `cd reachy_companion && .venv/bin/python -m pytest tests/test_people.py -v` — expect FAIL (module missing).
- [ ] **Step 3:** Implement `people.py` per the interface + notes above.
- [ ] **Step 4:** Re-run — expect PASS. Also `ruff check src/reachy_companion/people.py` and `mypy` on the file, clean.
- [ ] **Step 5:** Commit: `feat(people): people.v1.json sibling store for per-person facts`.

---

### Task 2: Three-way boot greeting

**Files:**
- Modify: `reachy_companion/src/reachy_companion/huggingface_realtime.py` (constants ~255-296; `_recognized_face_prefix` 1306-1411; `_send_startup_greeting_prompt` 1541-1585)
- Modify: `reachy_companion/.env.example` (FACE block ~239-259)
- Test: `reachy_companion/tests/test_face_tools.py` (rewrite 705-796 region tests), `reachy_companion/tests/test_huggingface_realtime.py:861-903`

**Interfaces:**
- Consumes: `people.facts_for_person` (Task 1); `Identification` (`face_id.py`: `.status`, `.name`, `.score`, `.face_count`).
- Produces: `_wake_face_identification(self) -> Any` (returns an `Identification` or `None`; `None` means "check disabled/failed — behave exactly like today's `""`"); module helper `_startup_greeting_prefix(identification: Any, facts: list[str]) -> str`; env knob `FACE_GREETING_FACTS` (default 6, clamp 0–20); constant `_FACE_GREETING_FACTS_DEFAULT: Final[int] = 6`.

- [ ] **Step 1: Constants.** Change `_FACE_WAKE_BUDGET_MS_DEFAULT` 1200 → `4000` and `_FACE_WAKE_ATTEMPTS_DEFAULT` 3 → `5` (operator decision: ~4 s wait; clamps `hi=10_000` / `hi=5` already admit these — do not change the clamps). Add below `_FACE_GREETING_PREFIX`:

```python
_FACE_GREETING_FACTS_DEFAULT: Final[int] = 6
_FACE_KNOWN_WITH_FACTS_PREFIX: Final[str] = (
    "（系统提示：摄像头认出面前的人是「{name}」。你记得关于他的这些事：{facts}。"
    "像老朋友一样自然地叫他的名字打招呼，可以自然带到一两件你记得的事，"
    "不要自我介绍，也不要提到摄像头或识别。）"
)
_FACE_STRANGER_GREETING_PREFIX: Final[str] = (
    "（系统提示：摄像头看到面前有人，但认不出是谁。向这位新朋友自然地问候并简单介绍你自己，"
    "可以礼貌地问对方怎么称呼。不要提到摄像头或识别。）"
)
```

- [ ] **Step 2: Refactor `_recognized_face_prefix` → `_wake_face_identification`.** Same body and logging through line 1401's miss-log, with these changes: every `return ""` becomes `return None`; the final success block (1403-1411) returns `identification` instead of the formatted prefix, and its log message drops the words "greeting personalized" (the caller now decides) — log `"Wake face check: recognized %s (score %.3f) on round %d of %d in %.0f ms."`. On a *miss* (1393-1401) return `identification` as well (the caller needs `status`/`face_count` for the stranger branch) — only disabled/no-camera/exception/timeout paths return `None`. Keep the single shared monotonic deadline exactly as-is (`test_greeting_is_not_delayed_past_the_wake_budget` pins the mechanism).

- [ ] **Step 3: Module helper** (place right after the constants; module-level so it is unit-testable without a handler):

```python
def _startup_greeting_prefix(identification: Any, facts: list[str]) -> str:
    """Map the wake-check outcome onto one of the three greeting prefixes."""
    if identification is None:
        return ""
    if identification.status == "recognized" and identification.name:
        if facts:
            return (
                _FACE_KNOWN_WITH_FACTS_PREFIX.format(
                    name=identification.name, facts="；".join(facts)
                )
                + "\n"
            )
        return _FACE_GREETING_PREFIX.format(name=identification.name) + "\n"
    if identification.face_count > 0 or identification.status in (
        "unknown",
        "ambiguous",
        "too_far",
        "multiple_faces",
    ):
        return _FACE_STRANGER_GREETING_PREFIX + "\n"
    return ""
```

- [ ] **Step 4: Rewrite `_send_startup_greeting_prompt` lines 1555-1556 and 1580-1583:**

```python
        identification = await self._wake_face_identification()
        recognized = (
            identification is not None
            and identification.status == "recognized"
            and identification.name
        )
        facts: list[str] = []
        if recognized:
            limit = env_int("FACE_GREETING_FACTS", _FACE_GREETING_FACTS_DEFAULT, lo=0, hi=20)
            if limit > 0:
                try:
                    facts = [
                        fact.text
                        for fact in await asyncio.to_thread(
                            facts_for_person,
                            self.deps.instance_path,
                            identification.name,
                            limit=limit,
                        )
                    ]
                except Exception as e:
                    logger.warning("Could not read person facts for greeting: %s: %s", type(e).__name__, e)
            self.deps.current_person = identification.name
            logger.info(
                "Startup greeting personalized for %s with %d remembered fact(s).",
                identification.name,
                len(facts),
            )
        greeting_prompt = _startup_greeting_prefix(identification, facts) + greeting_prompt
```

and the spawn condition becomes `if not recognized and env_bool("FACE_AUTO_GREET", True):` (a stranger/no-face boot still gets the extended window; a recognized boot does not). Import `facts_for_person` from `reachy_companion.people` at the top of the module.

- [ ] **Step 5: Rewrite the pinned tests.** In `tests/test_face_tools.py`: replace `test_greeting_is_untouched_unless_someone_is_recognized` (705-723) with a three-way parametrization using its existing fake-recognizer scaffolding (`_sent_text(handler)` helper stays):
  - `no_face` (face_count=0) and `unavailable` → sent text `== GREETING` verbatim;
  - `unknown`/`ambiguous`/`too_far` (face_count=1) and `multiple_faces` (face_count=2) → sent text startswith `_FACE_STRANGER_GREETING_PREFIX` and endswith GREETING;
  - `recognized` name="Lena" with two facts pre-written via `people.add_person_fact(tmp_instance, "Lena", ...)` → sent text contains "Lena" and both fact texts, endswith GREETING, and `handler.deps.current_person == "Lena"`;
  - `recognized` with **no** stored facts → sent text `== _FACE_GREETING_PREFIX.format(name="Lena") + "\n" + GREETING`.
  Keep `test_greeting_is_not_delayed_past_the_wake_budget` (775-796) as-is — it must still pass (mechanism unchanged; it sets `FACE_WAKE_BUDGET_MS=300` explicitly so the new default does not slow it). In `tests/test_huggingface_realtime.py` update `test_startup_greeting_spawns_extended_check_only_on_a_miss` (861-903): extended check spawns on no-face **and** on stranger outcomes, not on recognized.
- [ ] **Step 6:** Run the two test files — expect the rewritten tests PASS, budget test PASS.
- [ ] **Step 7: `.env.example`.** Update the FACE block: `# FACE_WAKE_BUDGET_MS=4000`, `# FACE_WAKE_ATTEMPTS=5`, add `# FACE_GREETING_FACTS=6` and the currently-undocumented `# FACE_WAKE_EXTENDED_MS=8000` (Codex R1-12), one comment line each ("boot waits up to this long to see a familiar face before greeting"; "how many remembered facts a personalized greeting may mention, 0 disables"; "bounded post-greeting look window, 0 disables"). Extend the env-docs test (`tests/test_openai_realtime_config.py:1034-1058`) to also assert the FACE knobs read by `huggingface_realtime.py` (`FACE_AUTO_GREET`, `FACE_WAKE_BUDGET_MS`, `FACE_WAKE_ATTEMPTS`, `FACE_WAKE_EXTENDED_MS`, `FACE_GREETING_FACTS`) appear in `.env.example`; run it — must pass.
- [ ] **Step 8:** Commit: `feat(greeting): three-way recognition-aware boot greeting with person facts`.

---

### Task 3: Extended wake window carries facts + sets current person

**Files:**
- Modify: `reachy_companion/src/reachy_companion/huggingface_realtime.py` (`_extended_wake_face_check` recognized branch 1461-1526; handler `__init__` around line 423)
- Test: `reachy_companion/tests/test_huggingface_realtime.py` (extended-check tests near 861+)

**Interfaces:**
- Consumes: `facts_for_person` (Task 1), `_FACE_LATE_RECOGNITION_PROMPT` (existing).
- Produces: constant `_FACE_LATE_KNOWN_WITH_FACTS_PROMPT`; `self.deps.current_person` set on late recognition.

- [ ] **Step 1:** Add constant beside `_FACE_LATE_RECOGNITION_PROMPT`:

```python
_FACE_LATE_KNOWN_WITH_FACTS_PROMPT: Final[str] = (
    "（系统提示：摄像头刚认出面前的人是「{name}」。你记得关于他的这些事：{facts}。"
    "自然地用名字招呼他，可以自然带到你记得的事。不要提到摄像头或识别这件事。）"
)
```

- [ ] **Step 2:** In the recognized branch, immediately after the second staleness re-check (line 1489-1491) and before `item.create`: read facts exactly as Task 2 Step 4 does (same `FACE_GREETING_FACTS` limit, same `asyncio.to_thread(facts_for_person, ...)`, same warning on failure), set `self.deps.current_person = name`, and pick the text: `_FACE_LATE_KNOWN_WITH_FACTS_PROMPT.format(name=name, facts="；".join(facts))` when facts else `_FACE_LATE_RECOGNITION_PROMPT.format(name=name)`.
- [ ] **Step 3:** Clear the label per spec §3.3 ("cleared on session close"): set `self.deps.current_person = None` in the handler `__init__` beside `self._user_has_spoken = False` (line 423) **and** in the (re)connect path at the point a new session's `self.connection` is assigned (before the session config is built — so a reconnect re-establishes identity via the wake checks or `who_is_this`). Comment at both sites: `# Person-scoped memory label (spec §3.3): set on recognition, cleared per session.` A non-recognized `who_is_this` glance does NOT clear it (Codex R1-4, partially rejected: a transient too_far/blink must not drop a valid label mid-conversation; `identify_with_retries` already returns best evidence).
- [ ] **Step 4: Test** (follow the file's existing fake-connection pattern for extended-check tests): a late recognition with stored facts sends an `item.create` whose text contains the name and a fact text, and sets `deps.current_person`; with no facts, text `== _FACE_LATE_RECOGNITION_PROMPT.format(name=...)`.
- [ ] **Step 5:** Run, PASS, commit: `feat(greeting): late wake recognition carries person facts and sets current person`.

---

### Task 4: Person-scoped `remember` / `forget`

**Files:**
- Modify: `reachy_companion/src/reachy_companion/tools/core_tools.py` (`ToolDependencies`, ~line 54)
- Modify: `reachy_companion/src/reachy_companion/tools/remember.py`, `tools/forget.py`
- Test: `reachy_companion/tests/test_memory_tools.py` (or the existing file that covers remember/forget — find with `grep -rl "class Remember\|remember" tests/ | head`; extend it)

**Interfaces:**
- Consumes: `people.add_person_fact`, `people.forget_person_fact` (Task 1); `deps.current_person` (Task 3).
- Produces: `ToolDependencies.current_person: str | None = None`; `remember` result gains `"scope": "person:<name>" | "global"`; `forget` result gains the same key on success.

- [ ] **Step 1:** Add to `ToolDependencies` after `set_party_mode`:

```python
    # Person-scoped memory (spec §3.3): the name of the last face-recognized
    # person this app run, set by the wake checks and who_is_this. A label for
    # memory scoping only — never used to gate behavior. Optional for the same
    # reason as face_recognizer: every other construction site keeps working.
    current_person: str | None = None
```

- [ ] **Step 2: Failing tests** (same fake-deps style the file already uses; `deps.instance_path = tmp_path`):

```python
async def test_remember_scopes_to_current_person(tmp_path):
    deps = make_deps(instance_path=tmp_path)
    deps.current_person = "Lena"
    result = await Remember()(deps, fact="Likes oolong tea")
    assert result["scope"] == "person:Lena"
    assert [f.text for f in people.facts_for_person(tmp_path, "Lena")] == ["Likes oolong tea"]
    assert memory.list_memory_facts(tmp_path) == []


async def test_remember_is_global_without_person(tmp_path):
    deps = make_deps(instance_path=tmp_path)
    result = await Remember()(deps, fact="House wifi is flaky")
    assert result["scope"] == "global"
    assert [f.text for f in memory.list_memory_facts(tmp_path)] == ["House wifi is flaky"]


async def test_forget_searches_person_scope_first_then_global(tmp_path):
    deps = make_deps(instance_path=tmp_path)
    memory.add_memory_fact(tmp_path, "tea in the cupboard")
    people.add_person_fact(tmp_path, "Lena", "tea every morning")
    deps.current_person = "Lena"
    result = await Forget()(deps, query="tea")
    assert result["scope"] == "person:Lena"
    assert people.facts_for_person(tmp_path, "Lena") == []
    assert len(memory.list_memory_facts(tmp_path)) == 1
    result2 = await Forget()(deps, query="tea")
    assert result2["scope"] == "global"
```

- [ ] **Step 3: Implement.** `remember.py` `__call__` body: keep validation; then

```python
        person = deps.current_person
        if person:
            stored_person_fact = add_person_fact(deps.instance_path, person, fact)
            if stored_person_fact is not None:
                logger.info("Tool call: remember person=%s fact=%s", person[:40], stored_person_fact.text[:120])
                return {"saved": stored_person_fact.text, "memory_id": stored_person_fact.id, "scope": f"person:{person}"}
        stored = add_memory_fact(deps.instance_path, fact)
        if stored is None:
            return {"error": "fact was empty or invalid; nothing was saved"}
        logger.info("Tool call: remember fact=%s", stored.text[:120])
        return {"saved": stored.text, "memory_id": stored.id, "scope": "global"}
```

`forget.py`: when `deps.current_person` is set, try `forget_person_fact(deps.instance_path, person, query=query)` first; on `removed is not None` return `{"removed": …, "memory_id": …, "scope": f"person:{person}"}` (+ `other_matches` when >1 candidates); otherwise fall through to the existing global path and add `"scope": "global"` to its success dict. Update both tools' `description` with one added sentence: remember — "When you have recognized who you are talking to, the fact is saved about that specific person."; forget — "Searches the recognized person's facts first, then general memory."
- [ ] **Step 4:** Run tests, PASS. Full-file `ruff`/`mypy` clean.
- [ ] **Step 5:** Commit: `feat(memory): remember/forget scope to the recognized person`.

---

### Task 5: `who_is_this` returns known facts and sets current person

**Files:**
- Modify: `reachy_companion/src/reachy_companion/tools/who_is_this.py`
- Test: `reachy_companion/tests/test_face_tools.py` (who_is_this section)

**Interfaces:**
- Consumes: `facts_for_person`, `deps.current_person`, existing `identify_with_retries`.
- Produces: tool result gains `known_facts: list[str]` **only** on `status == "recognized"`. The `Identification` dataclass and its closed Literals are untouched — the field is added to the result dict at the tool layer.

- [ ] **Step 1: Failing test** (existing fake-recognizer scaffolding):

```python
async def test_who_is_this_returns_known_facts_and_sets_person(tmp_path):
    people.add_person_fact(tmp_path, "Louis", "Plays go on weekends")
    deps = make_deps(instance_path=tmp_path, recognizer=recognizing("Louis", score=0.6))
    result = await WhoIsThis()(deps)
    assert result["status"] == "recognized"
    assert result["known_facts"] == ["Plays go on weekends"]
    assert deps.current_person == "Louis"


async def test_who_is_this_unknown_has_no_facts_and_keeps_person(tmp_path):
    deps = make_deps(instance_path=tmp_path, recognizer=unknown_result())
    deps.current_person = "Louis"
    result = await WhoIsThis()(deps)
    assert "known_facts" not in result
    assert deps.current_person == "Louis"   # an unknown glance does not unset the label
```

- [ ] **Step 2: Implement** in `__call__` after `identify_with_retries`:

```python
        if result.get("status") == "recognized" and isinstance(result.get("name"), str):
            name = result["name"]
            deps.current_person = name
            limit = env_int("FACE_GREETING_FACTS", 6, lo=0, hi=20)
            if limit > 0:
                try:
                    result["known_facts"] = [
                        fact.text
                        for fact in await asyncio.to_thread(
                            facts_for_person, deps.instance_path, name, limit=limit
                        )
                    ]
                except Exception as e:
                    logger.warning("who_is_this: could not read person facts: %s: %s", type(e).__name__, e)
```

(imports: `asyncio`, `from reachy_companion.audio.envparse import env_int` — the same helper `huggingface_realtime.py:50` uses — and `from reachy_companion.people import facts_for_person`). Add one sentence to the tool description: "When recognized, the result includes short remembered facts about that person — use them naturally."
- [ ] **Step 3:** Run, PASS, commit: `feat(face): who_is_this returns person facts and labels the session`.

---

### Task 6: Still-pose enrollment

**Files:**
- Modify: `reachy_companion/src/reachy_companion/moves.py` (command API ~289-301, `_handle_command` ~311-403, breathing manager ~427-455)
- Modify: `reachy_companion/src/reachy_companion/tools/face_support.py` (new context manager)
- Modify: `reachy_companion/src/reachy_companion/tools/remember_face.py` (bracket the burst; description)
- Test: `reachy_companion/tests/test_moves.py` (or the file covering MovementManager — locate with `grep -rl "MovementManager" tests/`), `reachy_companion/tests/test_face_tools.py`

**Interfaces:**
- Produces: `MovementManager.set_hold_still(hold: bool)` (thread-safe via the command queue); `face_support.hold_still(deps)` async context manager; `_HOLD_STILL_SETTLE_S: float = 0.35` in `face_support.py`.

- [ ] **Step 1: MovementManager.** Public method beside `set_speaking`:

```python
    def set_hold_still(self, hold: bool) -> None:
        """Freeze the head for a photo capture; thread-safe via the command queue.

        While held: face tracking is paused at the current pose (weight-0.0, the
        set_speaking anchor pattern) and idle breathing is suppressed, so an
        enrollment frame is not motion-blurred. Release restores tracking unless
        the assistant is mid-speech (set_speaking owns the anchor then).
        """
        self._command_queue.put(("set_hold_still", hold))
```

In `_handle_command` add a branch (mirror the `set_speaking` branch's shape):

```python
        elif command == "set_hold_still":
            hold = bool(payload)
            if self._hold_still == hold:
                return
            self._hold_still = hold
            try:
                if hold:
                    # Any active move — breathing, a dance, an emotion — blurs
                    # the capture; the person asked to be memorized, so the
                    # photo wins (Codex R1-9). Same semantics as clear_queue.
                    self.move_queue.clear()
                    self.state.current_move = None
                    self.state.move_start_time = None
                    self._breathing_active = False
                    if self._head_tracking:
                        self._track_anchor = self.current_robot.get_current_head_pose()
                        self.current_robot.start_head_tracking(weight=0.0)
                elif self._head_tracking and not self._is_speaking:
                    self._track_anchor = None
                    self.current_robot.start_head_tracking(weight=1.0)
            except Exception as e:
                logger.warning("Hold-still toggle failed: %s", e)
```

Initialize `self._hold_still = False` in `__init__` beside `self._is_speaking`, and add `if self._hold_still: return` at the top of `_manage_breathing` (breathing must not restart mid-capture; it resumes on release via the normal idle timer).
- [ ] **Step 2: `face_support.hold_still`.**

```python
_HOLD_STILL_SETTLE_S = 0.35


@contextlib.asynccontextmanager
async def hold_still(deps: ToolDependencies) -> AsyncIterator[None]:
    """Hold the head and audio-reactive motion still around a capture burst.

    Best-effort on both edges: a motion API failure must never fail the tool —
    a slightly blurred enrollment beats a refused one.
    """
    try:
        deps.movement_manager.set_hold_still(True)
    except Exception as e:
        logger.warning("hold_still: could not freeze head tracking: %s", e)
    try:
        await asyncio.to_thread(deps.reachy_mini.disable_wobbling)
    except Exception as e:
        logger.warning("hold_still: could not disable wobbling: %s", e)
    await asyncio.sleep(_HOLD_STILL_SETTLE_S)
    try:
        yield
    finally:
        try:
            await asyncio.to_thread(deps.reachy_mini.enable_wobbling)
        except Exception as e:
            logger.warning("hold_still: could not re-enable wobbling: %s", e)
        try:
            deps.movement_manager.set_hold_still(False)
        except Exception as e:
            logger.warning("hold_still: could not release head tracking: %s", e)
```

(Wobbling restore is unconditional: main.py enables it at startup and only sleep disables it, and enrollment cannot run while asleep — noted in the docstring if the reviewer asks.)
- [ ] **Step 3: `remember_face`.** Wrap the whole capture region — from the first `capture_frame` through the extras loop — in `async with hold_still(deps):` (the final log + return stay outside). Extend `description` with: "Before calling, tell the person you are taking a quick look and ask them to look at you and hold still for two seconds; you will hold your head still while you memorize their face."
- [ ] **Step 4: Tests.** MovementManager: drive `_handle_command` directly with a fake robot recording calls — hold with tracking on → `start_head_tracking(weight=0.0)` after `get_current_head_pose`; release → `weight=1.0`; release while `_is_speaking` → no tracking call; hold with an active move (breathing or a queued primary move) clears the current move **and** the queue; `_manage_breathing` no-ops while held. Wobbling restore is deliberately unconditional (Codex R1-9 partially rejected: the SDK exposes no wobble-state getter, and the only disabled state — sleep — cannot coincide with an enrollment call); document that in the `hold_still` docstring. `remember_face`: fake movement manager + fake reachy recording `disable_wobbling`/`enable_wobbling` — assert hold precedes the first frame read and release happens **even when `recognizer.enroll` raises** (the existing exception-path test gains the assertion).
- [ ] **Step 5:** Run both test files, PASS, commit: `feat(enroll): hold the head still during the enrollment capture burst`.

---

### Task 7: Robot-side gate

- [ ] **Step 1:** `cd reachy_companion && .venv/bin/python -m pytest` — full suite green (baseline 1351 passed / 30 skipped grows by the new tests; nothing else may go red).
- [ ] **Step 2:** `ruff check .` and the repo's mypy invocation (check `grep -rn "mypy" reachy_companion/pyproject.toml Makefile 2>/dev/null` for the exact command; strict mode) — both clean.
- [ ] **Step 3:** Commit any straggler fixes: `chore: robot-side gate green for person-memory wave`.

---

### Task 8: Backend scaffold + store

**Files:**
- Create: `companion_backend/README.md`, `companion_backend/run.sh`, `companion_backend/backend/__init__.py`, `companion_backend/backend/store.py`, `companion_backend/backend/config.py`, `companion_backend/tests/__init__.py`, `companion_backend/tests/conftest.py`, `companion_backend/tests/test_store.py`
- Modify: `.gitignore` (add `companion_backend/data/`)

**Interfaces (produced for Tasks 9-12):**
- `backend/config.py`: `load_settings() -> Settings` — dataclass `Settings(reachy_host: str, reachy_ssh_user: str, data_dir: Path, instance_dir: str)`; reads the repo-root `.env` (keys `REACHY_HOST`, `REACHY_SSH_USER`; parse with a 10-line loader — `KEY=value`, `#` comments, no new deps); `instance_dir` constant `"/venvs/apps_venv/lib/python3.12/site-packages/reachy_companion"`; `data_dir` defaults to `companion_backend/data` (override env `COMPANION_BACKEND_DATA`).
- `backend/store.py`: JSON store at `data_dir/people.json`, photos under `data_dir/photos/<person_id>/`. Dataclasses:
  - `BackendFact(id: str, text: str, created_at: int)`
  - `BackendPhoto(id: str, display_name: str, stored_as: str | None, added_at: int, embedding: tuple[float, ...] | None, error: str | None, synthetic: bool = False)` — `embedding` is the 128-float SFace vector or None when embedding failed; `error` one of `"no_face" | "multiple_faces" | "too_far" | "decode_failed" | "internal_error"` or None. **Bytes are stored under `<photo_id><ext>`** where ext is whitelisted from the upload's extension (`.jpg/.jpeg/.png/.webp`, else `.bin`) — the client filename is display metadata only, never a path (Codex R1-11). `synthetic=True` marks a voice-enrolled embedding imported from the robot: `stored_as=None`, no bytes on disk (Codex R1-7).
  - `BackendPerson(id: str, name: str, face_id: str | None, facts: tuple[BackendFact, ...], photos: tuple[BackendPhoto, ...], created_at: int, updated_at: int)`
  - `SyncMeta(last_push_at: int | None, last_faces_sha256: str | None, last_people_sha256: str | None)` persisted in the same file under a `"sync"` key; `get_sync_meta(settings)` / `set_sync_meta(settings, meta)` (Codex R1-10) — written only after a verified push.
  - Functions: `list_people(settings)`, `get_person(settings, person_id)`, `create_person(settings, name) -> BackendPerson`, `rename_person`, `delete_person` (also removes its photo dir), `add_fact(settings, person_id, text)`, `delete_fact(settings, person_id, fact_id)`, `add_photo(settings, person_id, display_name, raw: bytes) -> BackendPhoto`, `set_photo_embedding(settings, person_id, photo_id, embedding, error)`, `add_synthetic_photo(settings, person_id, embedding) -> BackendPhoto`, `delete_photo`, `set_person_face_id`.
  - **Robot-contract normalization at the boundary** (Codex R1-6): `create_person`/`rename_person` pass names through `faces.normalize_face_name` (40-char cap) and `add_fact` through `memory.normalize_memory_text` (280-char cap) — what the backend stores is byte-identical to what projection emits, so a pushed file can never read back as drift. Name uniqueness enforced case-insensitively **after** normalization (`ValueError` on duplicate).
  - **Concurrency** (Codex R1-5): module-level `threading.RLock()` held across every read-modify-write (FastAPI serves concurrent requests).
  - Same atomic tmp+replace write idiom as `people.py`; ids `bp_`/`bf_`/`bph_` prefixed. No people/photo-count caps here — the Mac holds everything; the 12/3 caps are a projection concern (Task 10).
- `run.sh`: `#!/bin/sh\ncd "$(dirname "$0")"\nexec ../reachy_companion/.venv/bin/python -m uvicorn backend.app:app --host 127.0.0.1 --port 8710 "$@"` (localhost-only bind — the UI is for this Mac; document in README).
- `tests/conftest.py`: adds `companion_backend/` to `sys.path` and provides a `settings` fixture pointing `data_dir` at `tmp_path`.

- [ ] **Step 1: Failing tests** — round-trip create/rename/delete person; duplicate name raises (including duplicates that only collide after normalization, e.g. `"Lena "` vs `"lena"`); a 300-char fact is stored truncated to the 280-char normalized form; add photo stores bytes at `photos/<person_id>/<photo_id>.jpg` regardless of a hostile `display_name` like `"../../evil.jpg"`; `add_synthetic_photo` lists back with `stored_as=None`; delete person removes the photo dir; corrupt `people.json` reads as empty list (log + `[]`, the robot-store idiom); two threads adding facts concurrently (barrier + `ThreadPoolExecutor`) lose neither; sync meta round-trips.
- [ ] **Step 2:** Run (`cd companion_backend && ../reachy_companion/.venv/bin/python -m pytest tests/ -v`), FAIL.
- [ ] **Step 3:** Implement; `chmod +x run.sh`; add `companion_backend/data/` to root `.gitignore`.
- [ ] **Step 4:** Run, PASS. Commit: `feat(backend): companion_backend scaffold and people store`.

---

### Task 9: Photo → embedding on the Mac

**Files:**
- Modify: `reachy_companion/src/reachy_companion/face_id.py` (extract a public embed-from-frame seam from `_capture`, lines ~580-643)
- Create: `companion_backend/backend/embedding.py`
- Test: `reachy_companion/tests/test_face_id.py` (seam), `companion_backend/tests/test_embedding.py`

**Interfaces:**
- `face_id.FaceRecognizer.embedding_for_frame(frame: NDArray[Any]) -> tuple[NDArray[np.float32] | None, Identification]` — **pure extraction of the existing `_capture` logic minus the store write**: detect (2× decimated, existing `DETECT_DOWNSCALE`), require exactly one face (else `Identification(status="multiple_faces"/"no_face", face_count=…)`), `MIN_FACE_PX` → `too_far`, align on the full-res frame, embed; returns the raw embedding and an `Identification` describing the outcome (`status="unknown"` with `face_count=1` on success — it identifies nothing, it only extracts). `enroll` is then refactored to call it and keep only the `upsert_face` step, so there is **one** implementation. All existing `test_face_id.py` tests must stay green — this step is a refactor, verified by the suite, plus one new direct test of the seam using the file's existing synthetic-frame fixtures.
- `backend/embedding.py`:
  - `decode_image(path: Path) -> NDArray[np.uint8] | None` — BGR HxWx3 via `imageio_ffmpeg.read_frames(str(path), pix_fmt="bgr24")`: `gen = read_frames(...)`; `meta = next(gen)`; `w, h = meta["size"]`; `frame_bytes = next(gen)`; `np.frombuffer(frame_bytes, dtype=np.uint8).reshape(h, w, 3)`; close the generator in `finally`; any exception → log + `None`.
  - `embed_photo(recognizer, path: Path) -> tuple[tuple[float, ...] | None, str | None]` — decode (`None` → `(None, "decode_failed")`), then `embedding_for_frame`; map the `Identification` to the `BackendPhoto.error` vocabulary (`no_face`, `multiple_faces`, `too_far`; anything else non-success → `internal_error`); on success return `faces._to_stored_embedding(embedding)` (reuse — it validates, normalizes, rounds) and `None`.
  - `build_recognizer(settings) -> FaceRecognizer` — construct with a scratch instance path under `data_dir` (its store is never used for matching; read the constructor signature in `face_id.py` before writing this — do not guess kwargs).

- [ ] **Step 1:** Failing test for the `face_id` seam (in `tests/test_face_id.py`, reusing its fixtures): a frame the existing enroll tests accept yields `(embedding of shape (128,), Identification(face_count=1))`; a blank frame yields `(None, status="no_face")`.
- [ ] **Step 2:** Refactor `face_id.py`; run the **whole** `test_face_id.py` + `test_face_tools.py` — green.
- [ ] **Step 3:** Failing backend tests: `decode_image` on a tiny PNG written by the test (generate with ffmpeg itself: use `imageio_ffmpeg.write_frames` to write a 32×32 PNG-in-mp4? — no: simpler, commit a 64×64 JPEG fixture `companion_backend/tests/fixtures/gray.jpg` generated once via `ffmpeg -f lavfi -i color=gray:s=64x64 -frames:v 1 gray.jpg` using the `imageio_ffmpeg.get_ffmpeg_exe()` binary in a fixture-generation step; check it in) returns a (64, 64, 3) uint8 array; `embed_photo` with a **stubbed** recognizer (monkeypatched `embedding_for_frame`) maps each Identification outcome to the right error string.
- [ ] **Step 4:** Implement `embedding.py`; PASS.
- [ ] **Step 5:** Commit: `feat(backend): photo decode and SFace embedding on the Mac`.

---

### Task 10: Projection + robot sync

**Files:**
- Create: `companion_backend/backend/projection.py`, `companion_backend/backend/robot.py`
- Test: `companion_backend/tests/test_projection.py`, `companion_backend/tests/test_robot_sync.py`

**Interfaces:**
- `projection.py`:
  - `project(settings, out_dir: Path) -> ProjectionResult` — writes `out_dir/faces.v1.json` **through `reachy_companion.faces` writers** (build `FaceRecord`s: per person take the newest ≤3 photos with non-None `embedding`, synthetic included; skip people with zero embedded photos for faces but still project their facts) and `out_dir/people.v1.json` **through `reachy_companion.people` writers** (`upsert_person` + `add_person_fact` against `out_dir` as the instance path — schema-exact by construction, `arcface5` stamped by the faces writer). **Facts are replayed oldest→newest** so the store's prepend-and-cap semantics leave the newest ≤20 in newest-first order (Codex R1-8). People ranked by `updated_at` desc; only the top 12 with embeddings project into faces (the robot cap); `ProjectionResult(faces_count: int, people_count: int, skipped: list[str])`.
  - **Face-id stability** (Codex R1-3): before building records, every projected person without a `face_id` gets one assigned (`faces._make_id()` idiom) and **persisted back** via `store.set_person_face_id` — projection is what mints the id, so two consecutive pushes emit identical ids. Build `FaceRecord(id=person.face_id, …)` directly and call `faces._write_faces_file` (module-private by underscore, but same-repo use is deliberate; one-line comment at the call site).
- `robot.py` (all subprocess, `ssh -o BatchMode=yes`, plain `scp`, 20 s timeouts, never `expect`):
  - `fetch_stores(settings, into: Path) -> dict[str, Path | None]` — scp `faces.v1.json` + `people.v1.json` from `$INSTANCE_DIR`; a missing remote file is `None`, not an error.
  - **Drift rule is hash-based** (Codex R1-2, subsumes id-diffing): `drift(settings) -> DriftState(faces_changed: bool, people_changed: bool, never_pushed: bool)` — sha256 of the fetched files vs `SyncMeta.last_*_sha256`; any robot-side write since our last push (new person, re-enrollment into an existing id, voice-added fact) changes the hash and counts as drift. A missing remote file hashes as `None`.
  - `robot_diff(settings) -> RobotDiff` — the *content* view for the import UI: parse fetched files via `reachy_companion.faces._read_faces_file` / the people reader; `RobotDiff(new_faces: list[RobotFace], new_person_facts: list[RobotPersonFacts])` with `RobotFace(record_id: str, name: str, embeddings: tuple[tuple[float, ...], ...])` and `RobotPersonFacts(name: str, face_id: str | None, facts: list[str])` — structured, identity-preserving (Codex R1-7). "New" = not present in the backend store (face: unknown `record_id`; fact: unknown `(name, normalized text)` pair).
  - `push(settings) -> PushResult` — sequence: fetch → compute drift → **refuse on drift unless `never_pushed` and the robot stores are absent/empty** (`PushResult(pushed=False, blocked_by=drift_or_diff)`); else project to a temp dir, then **guarded remote promote** (Codex R1-1): `scp` both files to `$INSTANCE_DIR/.faces.push.tmp` / `.people.push.tmp`, then ONE ssh command that (a) re-checks both current remote files still match the pre-push sha256s captured at fetch (absent file ⇒ expected-absent), and (b) `mv`s both tmp files into place — abort with a distinct exit code on mismatch, which `push` reports as a race (`blocked_by`). `mv` on the same filesystem is atomic per file; the between-files window is microseconds against an app that only ever reads — recorded as accepted residual risk in the module docstring. Then verify by re-fetching, comparing sha256 locally, parsing counts locally (no robot-python assumptions), and **only then** `set_sync_meta` with the new hashes + timestamp. `PushResult(pushed: bool, faces_count: int, people_count: int, blocked_by: object | None)`.
  - `import_from_robot(settings) -> RobotDiff` (preview) and `apply_import(settings, diff) -> None` — new faces become backend people (name + `face_id=record_id`) with their robot embeddings copied into **synthetic** `BackendPhoto` entries via `add_synthetic_photo` (so projection round-trips them and voice enrollments survive); new facts append via `add_fact` matched by normalized name. After apply, the next `push` re-fetches and re-checks drift (import does not clear it; the push after a successful import will still see robot hashes ≠ last-pushed — `push` therefore also accepts the case where drift exists but `robot_diff` is empty after import, i.e. everything on the robot is already known to the backend: that is "imported drift", allowed to proceed).
  - `robot_app_status(settings)`, `robot_app_start/stop/restart(settings)` — `httpx` against `http://{reachy_host}:8000/api/apps/...` (routes: `GET current-app-status`, `POST start-app/reachy_companion`, `POST stop-current-app`, `POST restart-current-app`).
  - Tests mock `subprocess.run` and monkeypatch a thin `_http_get/_http_post` seam (respx is not installed).

- [ ] **Step 1:** Failing tests: projection produces files that `reachy_companion.faces._read_faces_file` / the people reader load back with the right counts, `arcface5` markers, ≤3 embeddings, ≤12 people; **two consecutive `project` calls emit byte-identical face ids** and persist `face_id` into the backend store; a person with >20 facts projects the newest 20 in newest-first order; facts projected for a person with no photos; push blocked on drift (fake fetch returns content whose hash ≠ last-pushed); push blocked as a race when the remote promote guard trips (fake ssh returns the mismatch exit code); push proceeds on hash match (subprocess calls recorded in order: fetch×2, scp tmp×2, ssh guarded-mv, fetch×2 verify) and records sync meta only after verify; "imported drift" (drift true, diff empty) proceeds; import preview lists a voice-enrolled robot person with embeddings; apply_import creates the synthetic-photo person and re-projection round-trips its embedding byte-identically.
- [ ] **Step 2:** Implement, PASS.
- [ ] **Step 3:** Commit: `feat(backend): projection to robot stores, guarded scp push, robot import`.

---

### Task 11: Backend API routes

**Files:**
- Create: `companion_backend/backend/app.py`
- Test: `companion_backend/tests/test_api.py` (FastAPI `TestClient`)

**Interfaces (consumed by the UI, Task 12):**

| Route | Body/Result |
|---|---|
| `GET /api/config` | `{reachy_host}` |
| `GET /api/people` | list of people (facts + photos incl. per-photo `error`, embedding elided to `has_embedding: bool`) |
| `POST /api/people` | `{name}` → person; 409 on duplicate |
| `PATCH /api/people/{id}` | `{name}` rename |
| `DELETE /api/people/{id}` | — |
| `POST /api/people/{id}/photos` | multipart file → saves, embeds synchronously (Task 9), returns the `BackendPhoto` view incl. `error` |
| `DELETE /api/people/{id}/photos/{photo_id}` | — |
| `GET /api/people/{id}/photos/{photo_id}/file` | the image bytes (for thumbnails); 404 for synthetic photos (`stored_as=None`) |
| `POST /api/people/{id}/facts` | `{text}` → fact |
| `DELETE /api/people/{id}/facts/{fact_id}` | — |
| `GET /api/sync/status` | `{last_push_at, robot_reachable, drift: {faces_changed, people_changed, never_pushed}}` — `last_push_at` from `SyncMeta`, drift from `robot.drift()`; ssh failure reported as `robot_reachable: false`, never a 500 |
| `POST /api/sync/push` | `PushResult` as JSON; 409 with the diff when blocked |
| `GET /api/sync/import` | `RobotDiff` preview |
| `POST /api/sync/import` | applies the previewed diff |
| `GET /api/robot/status`, `POST /api/robot/start`, `POST /api/robot/stop`, `POST /api/robot/restart` | proxied daemon apps API |
| `GET /` + `/static/*` | the UI |

The app builds `settings = load_settings()` at startup and the shared `FaceRecognizer` lazily on first photo upload (module-level cached — model load is ~1.5 s). Errors: no swallowing — sync/ssh failures return 502 with the command's stderr tail; embedding failures return 200 with the photo's `error` field (a failed photo is data, not an exception).

- [ ] **Step 1:** Failing tests with `TestClient` and monkeypatched `robot.py` seams: people CRUD happy paths + 409 duplicate; photo upload path with a monkeypatched `embed_photo` returning a fake embedding; push blocked → 409 carrying the diff; robot proxy calls the seam.
- [ ] **Step 2:** Implement, PASS.
- [ ] **Step 3:** Commit: `feat(backend): management API`.

---

### Task 12: Backend UI

**Files:**
- Create: `companion_backend/static/index.html`, `companion_backend/static/css/style.css`, `companion_backend/static/js/main.js`, `js/router.js`, `js/ui.js`, `js/api.js` (backend REST helper), `js/rpc.js` (adapted copy of `reachy_companion/src/reachy_companion/static/js/api.js` — the JSON-RPC-over-WS client, pointed at `ws://{reachy_host}:7860/rpc` using `GET /api/config`), `js/views/people.js`, `js/views/person.js`, `js/views/sync.js`, `js/views/control.js`

**Interfaces:** consumes Task 11 routes verbatim; `rpc.js` consumes the robot's existing methods `conversation.status`, `conversation.say`, `conversation.interrupt`, `conversation.mic`, and notifications `conversation.transcript` / `conversation.turn`.

Views (hash-routed, mirroring the existing console's `mount*View({outlet, signal, navigate})` pattern — read `reachy_companion/src/reachy_companion/static/js/router.js` and `views/home.js` first and copy their conventions):
- **People** (`#/people`): card list (name, photo count, fact count, per-photo error badges); create/delete.
- **Person** (`#/people/<id>`): photo grid with thumbnails (`GET …/file`), upload input (multiple), per-photo status (`embedded` / error string), delete photo; facts list with add/delete (280-char counter).
- **Sync** (`#/sync`): drift status, Push button (blocked state renders the diff and an "Import first" link), Import preview table + apply button, last-push time.
- **Control** (`#/control`): robot app status + start/stop/restart buttons (REST); live panel over `rpc.js` — connection state, mic toggle, interrupt, say-box, rolling transcript from `conversation.transcript`.

No framework, no build step, `h()`/`$()` helpers as in the existing `ui.js`. Keep it plain and legible; this is an operator tool, not a product.

- [ ] **Step 1:** Implement (no unit tests for the JS — the QA pass in Task 13 verifies; the API surface is already tested).
- [ ] **Step 2:** Manual smoke: `companion_backend/run.sh`, open `http://127.0.0.1:8710/`, walk: create person → upload a real photo of a face (operator-provided or any clear headshot) → see `embedded` → add fact → Sync page shows robot state (robot may be offline: the page must render the failure, not blank).
- [ ] **Step 3:** Commit: `feat(backend): management UI`.

---

### Task 13: End-to-end verification (Mac-only) + docs/state

**Files:**
- Modify: `feature_list.json`, `progress.md`, `DECISIONS.md`, `companion_backend/README.md`
- Create: `companion_backend/scripts/selftest.py`

**Steps:**
- [ ] **Step 1: Scripted E2E on the Mac** (`selftest.py`, runnable via `run.sh` venv): with `data_dir` in a temp location — create person, embed a fixture photo **of a real face** (add `companion_backend/tests/fixtures/face.jpg`: the operator provides one, or generate a synthetic face is NOT acceptable — if no photo is available, the selftest takes `--photo PATH` and the step records the exact photo used), project, then load the projected `faces.v1.json` with `reachy_companion.face_id.FaceRecognizer.match` against a second embedding of the same photo → `recognized`. Print counts and score. This proves upload→embed→project→match without the robot.
- [ ] **Step 2: feature_list.json** — add rows (state `implemented-unverified`, each with behavior + verification method + evidence-so-far): `PERSON-GREET-KNOWN`, `PERSON-GREET-STRANGER`, `PERSON-GREET-EMPTY`, `PERSON-MEMORY-AUTO`, `ENROLL-STILL`, `BACKEND-PUSH-LIVE`, `BACKEND-IMPORT` (live-verification definitions in spec §6).
- [ ] **Step 3: DECISIONS.md** — add **D-025** recording: the PRD §9 amendment, option C architecture (Mac authoritative, robot as projection, push-guarded-by-import), the three-way greeting + 4 s wake budget, person scoping via `deps.current_person` (reset per app run, not per session — with the reasoning), still-pose hold, the `embedding_for_frame` seam, and the explicitly-rejected alternatives (robot-side `people.*` RPC; extending `memory.v1.json`; greeting fields in the profile schema).
- [ ] **Step 4: progress.md** — current-state paragraph + the new pending live rows; **README.md** in `companion_backend/` — run instructions, port, data layout, the trusted-LAN caveat.
- [ ] **Step 5:** Full gates once more — robot suite + `ruff check` + mypy (existing config), **plus explicit backend gates** (Codex R1-13, the repo mypy config covers only `reachy_companion/src/`): from `companion_backend/`, `../reachy_companion/.venv/bin/python -m pytest tests/`, `../reachy_companion/.venv/bin/ruff check backend/ tests/`, `../reachy_companion/.venv/bin/mypy --strict backend/`. Commit: `docs: person-memory wave state, D-025, verification rows`.

---

### Task 14: Merge readiness (deploy is NOT in this plan)

- [ ] **Step 1:** `git status --short --branch` clean; push branch; merge to `main` after review.
- [ ] **Step 2:** Deployment to the physical robot happens via the `reachy-deploy` skill (operator-gated, D-009) — the wheel now additionally carries `people.py` and the greeting changes; `people.v1.json` must be **added to the deploy skill's backup/restore manifest list** — do this edit inside `.claude/skills/reachy-deploy/SKILL.md` as part of the deploy session, not this plan, and note it in `session-handoff.md` now so it is not forgotten.
- [ ] **Step 3:** Write `session-handoff.md` with: deploy note above, the live `FACE-*`/`PERSON-*`/`BACKEND-*` rows, and the operator-photo requirement for Task 13's selftest if it was deferred.

## Review Log

**Round 1 (2026-08-28, `codex --profile nova-auto exec`, 13 findings):**

| # | Severity | Finding | Ruling |
|---|---|---|---|
| R1-1 | high | push race + partial-push between the two scp'd files | **Accepted** — guarded remote promote: scp to tmp names, one ssh command re-checks pre-push hashes and `mv`s both; microsecond between-files window recorded as residual risk |
| R1-2 | high | id-only diff misses re-enrollment into a known id | **Accepted** — drift rule is now hash-based vs last-pushed sha256; "imported drift" (drift but empty content diff) may proceed |
| R1-3 | high | fresh projection face-ids not persisted → next push self-blocks | **Accepted** — projection mints and persists `face_id` via `set_person_face_id`; test pins two consecutive pushes byte-identical |
| R1-4 | high | `current_person` lifetime deviated from spec; stale label risk | **Partially accepted** — reverted to spec ("cleared per session": handler init + reconnect). Clearing on a non-recognized `who_is_this` **rejected**: a transient `too_far`/blink glance must not drop a valid label mid-conversation |
| R1-5 | high | backend store has no cross-request lock | **Accepted** — module `RLock` over every read-modify-write + concurrency test |
| R1-6 | high | backend without caps/normalization makes projection ≠ store → phantom drift | **Accepted** — `normalize_face_name`/`normalize_memory_text` at the store boundary |
| R1-7 | medium | RobotDiff loses identity; no synthetic-photo store API | **Accepted** — structured `RobotFace`/`RobotPersonFacts`; `add_synthetic_photo`; file route 404s synthetic |
| R1-8 | medium | fact replay order through prepend-and-cap store | **Accepted** — replay oldest→newest, test with >20 facts |
| R1-9 | medium | hold-still ignores active moves; wobble restore unconditional | **Partially accepted** — hold now clears active move + queue (the photo wins). Unconditional wobble re-enable **kept**: SDK has no wobble-state getter and the only disabled state (sleep) cannot coincide with enrollment; documented in the docstring |
| R1-10 | medium | no sync metadata for the drift indicator | **Accepted** — `SyncMeta` persisted, written only after verified push |
| R1-11 | medium | upload filename traversal/collision | **Accepted** — bytes stored as `<photo_id><whitelisted ext>`; client filename is display-only |
| R1-12 | low | env-docs test does not cover FACE knobs; `FACE_WAKE_EXTENDED_MS` undocumented | **Accepted** — document it, extend the test to the FACE block |
| R1-13 | low | mypy/ruff gates do not cover `companion_backend/` | **Accepted** — explicit backend gate commands in Task 13 |
