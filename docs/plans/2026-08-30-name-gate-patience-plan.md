# Name-Gated Barge-In, Patience & Brevity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make solo Reachy interrupt only when addressed by name ("Reachy"/瑞奇…) or told to stop, wait for the speaker to actually finish before replying, speak more briefly, and keep the model's context honest about what was actually heard (`conversation.item.truncate`).

**Architecture:** All four changes ride the existing solo pause-then-decide barge machine (`huggingface_realtime.py`, Task 8 of the voice-robustness wave) and the session-config builder (`openai_realtime.py`). No new subsystems, no new dependencies: the name gate reuses the party-mode address-name list and control-phrase regex; patience is env-default tuning plus one new session field (`reasoning.effort`); brevity is prompt text plus a `max_output_tokens` safety rail; truncate adds per-item heard-audio accounting on top of the existing `audio_drain` enqueue/played counters.

**Tech Stack:** Python 3.12, `openai 2.28.0` GA realtime types over websocket, pytest + AsyncMock harness (`tests/test_solo_barge.py` conventions), ruff + mypy --strict.

**Spec:** Operator ask (2026-08-30, this session): (1) "like a human, Reachy should listen for barge-in if 'REACHY' is mentioned"; (2) "no need to haste to reply or action before speaker is silent"; (3) "talkative which is good but obviously speaks too much". Research basis: `docs/research-realtime-api-2026-08.md` (must be read by every implementer); prior art `docs/research-realtime-voice-best-practices.md`, `docs/plans/2026-08-25-voice-robustness-plan.md`.

## Global Constraints

- Reuse first: no new dependencies; reuse `_party_names()`, `_PARTY_CONTROL_RE`, `is_substantive`, `audio_drain` counters. Never touch `reference/` (absent in this checkout anyway).
- `REALTIME_SOLO_CLIENT_BARGE=0` legacy path stays byte-identical (existing contract, `tests/test_solo_barge.py:10-11`).
- The robot instance `.env` ships `REALTIME_VAD_THRESHOLD=0.7` (D-023); nothing here may break when that override is present.
- Control phrases (停/閉嘴/stop/…) beat every gate — "a robot you cannot silence is worse than any false positive" (`huggingface_realtime.py:93-94`). Binding rule for every decision point added here.
- `conversation.item.truncate` `audio_end_ms` must always round DOWN (server errors if it exceeds real duration — research doc §2). Never send it on a rollback path (truncation is irreversible; rollbacks resume the audio).
- Field name is `max_output_tokens` (GA), never `max_response_output_tokens` (dead beta name).
- Gates: full robot suite green (`python -m pytest` from `reachy_companion/`, baseline 1468 passed / 30 skipped), `ruff check`, `mypy --strict` clean.
- Code and tests live under `reachy_companion/` (tests mirror `src/`); Tasks 8–9 additionally touch exactly these repo-root files: `persona.md`, `README.md`, `DECISIONS.md`, `feature_list.json`, `progress.md`, `docs/research-realtime-voice-best-practices.md`. Nothing else outside the package. Commit per task.

## Design decisions (argued once, binding below)

1. **Gate scope = interruption only.** The name gate decides what may *cut off* a playing reply. It does NOT decide which turns get answered — that would be party mode (`create_response=false` + address gate), which already exists and which the operator can enable by voice. Unaddressed turns committed while the robot is quiet keep today's behavior (server auto-answers; the `wait_for_user` prompt tool absorbs ambient chatter). Rejected alternative: extending `create_response=false` to solo — collapses solo into party, doubles reply latency for every normal turn.
2. **Gate default ON** (`REALTIME_SOLO_NAME_GATE=1`). The operator asked for this behavior as the norm, not an experiment. `0` restores the substantive-transcript rule exactly.
3. **With the gate on, sustained speech no longer auto-commits.** A name is textual; only a transcript can carry it. The confirm timer becomes a bounded *max pause*: unaddressed speech that outlasts it resumes the reply (Reachy keeps telling its story while others chat — the human behavior the operator described). New env `REALTIME_BARGE_MAX_PAUSE_MS`, default 4000.
4. **Late-address catch.** With `gpt-transcribe`, partials effectively arrive post-commit (research §5), so an addressed utterance longer than the max pause would see the reply resume mid-utterance, then the transcript lands with the name. The completed-transcript handler therefore gets a late-interrupt path: name/control in a committed turn while the robot is audible silences it even when no pause is pending.
5. **Truncate only on commit paths** (solo commit, party confirm, late interrupt) — never at pause, never on rollback.
6. **Patience default stays `server_vad`, silence 800→1000 ms.** `semantic_vad eagerness=low` is the better mechanism on paper (max-timeout semantics) but is not documented Mandarin-tuned; it stays one env flip away (`REALTIME_VAD_TYPE=semantic_vad`, `REALTIME_VAD_EAGERNESS=low`) and gets a live A/B row (`VOICE-SEMANTIC-VAD-AB`, already pending). Going above ~1100 ms flat silence makes every turn sluggish (research §1).
7. **Brevity = prompt first, token cap as rail only.** `max_output_tokens` default 900 (≈40 s of speech) — hit only by runaway monologues; the cap cuts mid-word, so the shipped prompt does the real work and the cap's trip is logged loudly.

---

### Task 1: Solo name-gate verdict + env flag

**Files:**
- Modify: `reachy_companion/src/reachy_companion/huggingface_realtime.py` (near `_solo_client_barge`, `:117-127`, and `_resolve_solo_barge`, `:1014-1049`)
- Test: `reachy_companion/tests/test_solo_barge.py`

**Interfaces:**
- Produces: `_solo_name_gate() -> bool` (module-level), `_gate_text_accepts(text: str) -> tuple[bool, str]` (module-level; `(accepted, reason)` where reason ∈ `"control phrase" | "name" | "unaddressed"`). Tasks 2–4 call both.
- Consumes: `_party_names()`, `_PARTY_CONTROL_RE`, `is_substantive`, `env_bool` (all existing).

- [ ] **Step 1: Write the failing tests** (append to `tests/test_solo_barge.py`; reuse `_solo_handler`, `_make_audible`, `_install_barge_state`)

```python
# --------------------------------------------------------------------------
# Name gate (2026-08-30 plan, Task 1)
# --------------------------------------------------------------------------


def test_gate_text_accepts_name_and_control() -> None:
    """Names and control phrases pass; substantive unaddressed speech does not."""
    assert hf_mod._gate_text_accepts("瑞奇你說錯了") == (True, "name")
    assert hf_mod._gate_text_accepts("Hey Reachy, stop there") == (True, "control phrase")
    assert hf_mod._gate_text_accepts("停") == (True, "control phrase")
    accepted, reason = hf_mod._gate_text_accepts("我們晚餐要吃什麼呢")
    assert not accepted and reason == "unaddressed"


@pytest.mark.asyncio
async def test_resolve_rolls_back_unaddressed_substantive_transcript(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gate ON: substantive speech without a name resumes the reply."""
    h = _solo_handler()
    _make_audible()
    h._response_done_event.clear()
    h._solo_speech_started()
    assert h._barge_pending
    resumed = await h._resolve_solo_barge("我們晚餐要吃什麼呢這麼晚了")
    assert resumed is True
    assert not h._barge_paused
    h.connection.response.cancel.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolve_commits_on_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gate ON: the robot's name in the transcript commits the barge."""
    h = _solo_handler()
    _make_audible()
    h._response_done_event.clear()
    h._solo_speech_started()
    resumed = await h._resolve_solo_barge("瑞奇我想先問一件事")
    assert resumed is False
    h.connection.response.cancel.assert_awaited_once()


@pytest.mark.asyncio
async def test_gate_off_restores_substantive_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    """REALTIME_SOLO_NAME_GATE=0: substantive speech commits, as before."""
    monkeypatch.setenv("REALTIME_SOLO_NAME_GATE", "0")
    h = _solo_handler()
    _make_audible()
    h._response_done_event.clear()
    h._solo_speech_started()
    resumed = await h._resolve_solo_barge("我們晚餐要吃什麼呢這麼晚了")
    assert resumed is False
    h.connection.response.cancel.assert_awaited_once()
```

Also add `"REALTIME_SOLO_NAME_GATE"` and `"REALTIME_BARGE_MAX_PAUSE_MS"` to the `_clean_barge_env` fixture's delenv list (`tests/test_solo_barge.py:88-99`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_solo_barge.py -k "gate or unaddressed" -v` (from `reachy_companion/`)
Expected: FAIL — `AttributeError: module ... has no attribute '_gate_text_accepts'`

- [ ] **Step 3: Implement** — in `huggingface_realtime.py`, directly after `_solo_client_barge()` (`:127`):

```python
def _solo_name_gate() -> bool:
    """Whether solo barge-in requires being addressed by name (2026-08-30 plan).

    The operator's ask: like a person telling a story, Reachy should stop for
    「瑞奇…」 or 「停」, and keep talking through speech aimed at someone else.
    `0` restores the substantive-transcript rule. Only meaningful when
    `REALTIME_SOLO_CLIENT_BARGE` is on — the legacy path never sees it.
    """
    return env_bool("REALTIME_SOLO_NAME_GATE", True)


def _gate_text_accepts(text: str) -> tuple[bool, str]:
    """Whether *text* addresses the robot: (accepted, reason).

    Control phrases beat everything (the party gate's first rule); then any
    address name (`REALTIME_PARTY_ADDRESS_NAMES` — the same list party mode and
    the transcription keyword bias use). Everything else is unaddressed.
    """
    folded = text.casefold()
    if _PARTY_CONTROL_RE.search(folded):
        return True, "control phrase"
    if any(name in folded for name in _party_names()):
        return True, "name"
    return False, "unaddressed"
```

Then rework the decision block of `_resolve_solo_barge` (`:1033-1041`) to:

```python
        if _solo_name_gate():
            accepted, reason = _gate_text_accepts(transcript)
        else:
            control = bool(_PARTY_CONTROL_RE.search(transcript.casefold()))
            accepted = control or is_substantive(transcript)
            reason = "control phrase" if control else "substantive"
        if accepted:
            logger.info(
                "solo barge-in confirmed by transcript (%s, %d chars)", reason, len(transcript)
            )
            await self._commit_solo_barge()
            return False
```

and extend the rollback log line so unaddressed real speech is distinguishable in the journal (replace `:1044`):

```python
            kind = "unaddressed" if _solo_name_gate() and is_substantive(transcript) else "backchannel"
            logger.info("solo barge rolled back (%s)", kind)
```

- [ ] **Step 4: Run the new tests and the whole barge module**

Run: `python -m pytest tests/test_solo_barge.py -v`
Expected: PASS, including all pre-existing tests (the old substantive-commit tests may need `monkeypatch.setenv("REALTIME_SOLO_NAME_GATE", "0")` added where they assert substantive-speech commits — adjust those tests, not the semantics they check).

- [ ] **Step 5: `ruff check . && mypy --strict src` then commit**

```bash
git add reachy_companion/src/reachy_companion/huggingface_realtime.py reachy_companion/tests/test_solo_barge.py
git commit -m "feat(voice): name-gate solo barge-in transcripts (REALTIME_SOLO_NAME_GATE)"
```

---

### Task 2: Partial-transcript fast commit + transcription `delay` pass-through

**Files:**
- Modify: `reachy_companion/src/reachy_companion/huggingface_realtime.py` (delta handler, `:2168-2186`)
- Modify: `reachy_companion/src/reachy_companion/openai_realtime.py` (`_transcription()`, `:99-125`; new `_record_partial_transcript_delta` override)
- Test: `reachy_companion/tests/test_solo_barge.py`, `reachy_companion/tests/test_openai_realtime_config.py`

**Interfaces:**
- Consumes: `_gate_text_accepts`, `_solo_name_gate` (Task 1), `_commit_solo_barge` (existing).
- Produces: nothing new for later tasks; env `REALTIME_TRANSCRIPTION_DELAY`.

The moment a partial transcript shows the name or a control phrase, commit — don't wait for `transcription.completed`. With today's `gpt-transcribe` partials arrive late (post-commit), so this is mainly the enabling half of the future `gpt-live-transcribe` + `delay` A/B; it must be harmless when partials are late.

**Codex round 1, finding 3 (accepted):** the base handler's `_record_partial_transcript_delta` (`huggingface_realtime.py:1263-1271`) stores a *snapshot* — `input_transcript.deltas = [delta]` — which matches the HF-compatible server. GA OpenAI transcription deltas are **incremental chunks**, so on our backend `current_partial` is only ever the latest fragment and a name split across deltas (`瑞` + `奇`) would never match. The OpenAI subclass must override it with append semantics (which also fixes the UI partial emission for this backend):

```python
    def _record_partial_transcript_delta(
        self,
        input_transcript: InputTranscriptChunksByItem,
        item_id: str,
        delta: str,
    ) -> None:
        """GA transcription deltas are incremental chunks, not snapshots — append."""
        if input_transcript.item_id == item_id:
            input_transcript.deltas.append(delta)
        else:
            input_transcript.item_id = item_id
            input_transcript.deltas = [delta]
```

(`InputTranscriptChunksByItem` is importable from `reachy_companion.huggingface_realtime`; match the base method's exact signature.)

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_partial_transcript_with_name_commits_early() -> None:
    """A delta containing the name resolves the pause without waiting for completed."""
    h = _solo_handler()
    _make_audible()
    h._response_done_event.clear()
    h._solo_speech_started()
    assert h._barge_pending
    await h._maybe_commit_on_partial("欸瑞奇", "item_1")
    assert not h._barge_pending
    assert h._barge_partial_committed_item == "item_1"
    h.connection.response.cancel.assert_awaited_once()
    # A later delta must not double-commit.
    await h._maybe_commit_on_partial("欸瑞奇你聽我說", "item_1")
    h.connection.response.cancel.assert_awaited_once()


@pytest.mark.asyncio
async def test_partial_transcript_without_name_keeps_pause() -> None:
    h = _solo_handler()
    _make_audible()
    h._response_done_event.clear()
    h._solo_speech_started()
    await h._maybe_commit_on_partial("我們晚餐", "item_1")
    assert h._barge_pending
    h.connection.response.cancel.assert_not_awaited()


@pytest.mark.asyncio
async def test_incremental_deltas_accumulate_and_commit_split_name() -> None:
    """GA deltas are incremental: 瑞 + 奇 across two deltas must still match (round 2, finding 3)."""
    h = _solo_handler()
    _make_audible()
    h._response_done_event.clear()
    h._solo_speech_started()
    chunks = hf_mod.InputTranscriptChunksByItem(item_id=None, deltas=[])
    h._record_partial_transcript_delta(chunks, "item_1", "欸瑞")
    h._record_partial_transcript_delta(chunks, "item_1", "奇你聽我說")
    joined = "".join(chunks.deltas)
    assert joined == "欸瑞奇你聽我說"
    await h._maybe_commit_on_partial(joined, "item_1")
    assert not h._barge_pending
    assert h._barge_partial_committed_item == "item_1"
    # A new item resets the accumulator.
    h._record_partial_transcript_delta(chunks, "item_2", "另一句")
    assert chunks.deltas == ["另一句"]
```

(Adjust the `InputTranscriptChunksByItem` construction to its real definition in `huggingface_realtime.py` — if it is not constructible this way, build it as the handler's `input_transcript_chunks_by_item` does. `test_partial_transcript_with_name_commits_early` likewise gains the `item_id` argument and asserts `h._barge_partial_committed_item` afterward.)

- [ ] **Step 2: Run to verify failure** (`AttributeError: _maybe_commit_on_partial`)

- [ ] **Step 3: Implement.** New method next to `_resolve_solo_barge`:

```python
    async def _maybe_commit_on_partial(self, partial: str) -> None:
        """Commit a pending pause the moment a partial transcript addresses us.

        Latency lever for the name gate (research doc §5): with a streaming
        transcriber the name arrives in a delta long before `completed`. Only
        ever commits — a partial can prove address, never prove its absence.
        Control phrases commit regardless of the name-gate flag (Codex round 1,
        finding 12: a robot you cannot silence is worse than any false
        positive); the name path is gate-mode only.
        """
        if self._party_mode or not self._barge_pending:
            return
        accepted, reason = _gate_text_accepts(partial)
        if not accepted:
            return
        if reason == "name" and not _solo_name_gate():
            return
        logger.info("solo barge-in confirmed by partial transcript (%s)", reason)
        await self._commit_solo_barge()
        # The completed transcript for this item must not re-interrupt the
        # answer this commit is about to produce (Codex round 2, finding 2).
        self._barge_partial_committed_item = item_id
```

Signature: `async def _maybe_commit_on_partial(self, partial: str, item_id: str) -> None`; the field `self._barge_partial_committed_item: str | None = None` joins the barge-state initialization, `on_external_interrupt`, and `_install_barge_state`.

Call it in the delta handler, after `current_partial` is computed (`:2178`):

```python
                        await self._maybe_commit_on_partial(current_partial, item_id)
```

In `openai_realtime._transcription()`, after the `prompt` block (`:121-124`), add the `delay` pass-through (new-model shape only, same guard as keywords):

```python
    delay = (os.getenv("REALTIME_TRANSCRIPTION_DELAY") or "").strip().lower()
    if delay:
        if delay in ("minimal", "low", "medium", "high", "xhigh"):
            params["delay"] = delay
        else:
            logger.warning("Ignoring invalid REALTIME_TRANSCRIPTION_DELAY=%r", delay)
    return params
```

(and delete the now-duplicated `return params` above it). Add a config test in `test_openai_realtime_config.py` asserting `delay` lands in the transcription params when the env var is set and is absent by default.

**Codex round 1, finding 2 (partially accepted):** the installed `AudioTranscriptionParam` stub only knows `language`/`model`/`prompt` — but `_transcription()` already returns `dict[str, Any]` and is `cast(Any, ...)` at the call site precisely because the stub predates `keywords` (which ships and works live today), so `delay` rides the same pattern with no mypy issue. It is opt-in (unset by default), only attached alongside the new-model extras, and a server-side rejection of the whole session shape already has the `_session_config_fallback` legacy retry. No feature flag beyond the env var itself.

- [ ] **Step 4: Run** `python -m pytest tests/test_solo_barge.py tests/test_openai_realtime_config.py -v` — PASS.

- [ ] **Step 5: Lint, typecheck, commit** — `feat(voice): commit name-gated barge on partial transcripts; REALTIME_TRANSCRIPTION_DELAY`

---

### Task 3: Confirm timer → bounded max pause under the gate

**Files:**
- Modify: `reachy_companion/src/reachy_companion/huggingface_realtime.py` (`_barge_confirm_s` area `:140-206`, `_confirm_solo_barge` `:901-915`)
- Test: `reachy_companion/tests/test_solo_barge.py`

**Interfaces:**
- Produces: `_barge_max_pause_s() -> float` (module-level, default 4.0 from `REALTIME_BARGE_MAX_PAUSE_MS`).
- Consumes: `_solo_name_gate` (Task 1).

- [ ] **Step 1: Failing tests**

```python
@pytest.mark.asyncio
async def test_sustained_unaddressed_speech_resumes_at_max_pause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gate ON: long speech with no name rolls the pause back instead of committing."""
    monkeypatch.setenv("REALTIME_BARGE_MAX_PAUSE_MS", "10")
    h = _solo_handler()
    _make_audible()
    h._response_done_event.clear()
    h._solo_speech_started()
    assert h._barge_confirm_task is not None
    await asyncio.wait_for(h._barge_confirm_task, timeout=1.0)
    assert not h._barge_paused and not h._barge_pending
    h.connection.response.cancel.assert_not_awaited()


@pytest.mark.asyncio
async def test_sustained_speech_still_commits_with_gate_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REALTIME_SOLO_NAME_GATE", "0")
    monkeypatch.setenv("REALTIME_BARGE_CONFIRM_MS", "10")
    h = _solo_handler()
    _make_audible()
    h._response_done_event.clear()
    h._solo_speech_started()
    await asyncio.wait_for(h._barge_confirm_task, timeout=1.0)
    h.connection.response.cancel.assert_awaited_once()
```

- [ ] **Step 2: Run to verify failure** (first test: cancel IS awaited today / timer delay wrong).

- [ ] **Step 3: Implement.** Module-level, next to `_barge_confirm_s`:

```python
def _barge_max_pause_s() -> float:
    """Longest a reply stays paused for speech that never addresses the robot.

    Gate mode only. A name can only arrive by transcript, so sustained speech
    proves nothing; but an unaddressed 30-second side conversation must not
    hold the reply hostage either. When this cap fires, the reply resumes —
    Reachy keeps talking while the room talks past it — and a name that lands
    later is still honored by the late-interrupt path (Task 4).
    """
    return env_int("REALTIME_BARGE_MAX_PAUSE_MS", 4000, lo=0) / 1000.0
```

In `_arm_barge_confirm` keep one timer but pick the delay and outcome by mode — change `_confirm_solo_barge`:

```python
    async def _confirm_solo_barge(self, seq: int) -> None:
        """Resolve a pause whose speech outlasted the confirm/max-pause window.

        Gate off: sustained speech IS the proof — commit (pre-plan behavior).
        Gate on: sustained speech proves nothing about address — roll back and
        resume; the transcript paths (partial, completed, late) keep the final
        say.
        """
        gate = _solo_name_gate()
        try:
            await asyncio.sleep(_barge_max_pause_s() if gate else _barge_confirm_s())
        except asyncio.CancelledError:
            return
        if self._party_mode or seq != self._party_utterance_seq:
            return
        if not self._barge_pending or not self._barge_speech_open:
            return
        if gate:
            logger.info("solo barge pause hit its cap with no address; resuming reply")
            self._barge_pending = False
            self._resume_playback(rolled_back=True)
            return
        logger.info("solo barge-in confirmed by sustained speech; cancelling the active reply")
        await self._commit_solo_barge()
```

Update `warn_if_barge_confirm_races_vad` (`:182-206`): return early when `_solo_name_gate()` is on (the confirm-commit branch it warns about no longer exists) **and** when `REALTIME_VAD_TYPE` is `semantic_vad` (fixes the recorded defect: the warning compared against a `server_vad` value the server ignores — `progress.md` known-edges list). Read the vad type with `os.getenv("REALTIME_VAD_TYPE", "server_vad").strip().lower()`.

Note for the implementer: after this rollback, `speech_stopped` later fires with `_barge_pending` False → `_solo_speech_stopped` returns without arming anything; the completed transcript then flows the normal path where Task 4's late catch applies. `_resume_playback` already handles being called from the timer task itself (it never cancels the current task).

**Deliberate and tested (Codex round 1, finding 4):** the max-pause rollback resumes the reply while `_barge_speech_open` is still true — Reachy talking *over* still-running unaddressed speech is the requested human behavior ("keeps telling its story while the room chats"), not a violation of "don't reply before the speaker is silent" (that principle governs *answering the user*, which still waits for their turn to commit). `test_sustained_unaddressed_speech_resumes_at_max_pause` pins it.

**Existing-test migration (Codex round 1, finding 14):** the pre-plan tests that assert sustained-speech commits, the confirm/rollback race tests, and the `warn_if_barge_confirm_races_vad` tests all describe gate-OFF semantics — add `monkeypatch.setenv("REALTIME_SOLO_NAME_GATE", "0")` to each of them (they keep guarding the legacy path) rather than weakening their assertions; the new gate-ON tests above are their counterparts.

- [ ] **Step 4: Run the full barge module** — PASS.

- [ ] **Step 5: Lint, typecheck, commit** — `feat(voice): cap unaddressed barge pauses (REALTIME_BARGE_MAX_PAUSE_MS); gate-aware confirm warning`

---

### Task 4: Late interrupt on an addressed committed turn

**Files:**
- Modify: `reachy_companion/src/reachy_companion/huggingface_realtime.py` (`_resume_playback` `:740`, `on_external_interrupt` `:773`, `_barge_reset_for_new_session` `:801`, completed handler `:2189-2241`)
- Test: `reachy_companion/tests/test_solo_barge.py`

**Interfaces:**
- Produces: `self._barge_resumed_response_id: str | None` (set on rollback, cleared on resets), `async _late_solo_interrupt() -> None`. Task 5 adds truncate inside it.
- Consumes: `_gate_text_accepts`, `_solo_name_gate`, `_robot_audible`, `_cancel_active_response`, `_barge_cooldown_s` (existing).

Covers: an addressed utterance longer than the max pause (Task 3 resumed the reply before the transcript landed), a name spoken during the post-barge cooldown, and 「停」 while a resumed reply is still draining. Without this, the name-gate's worst case is "Reachy talks over the person who called its name."

- [ ] **Step 1: Failing tests**

```python
@pytest.mark.asyncio
async def test_late_addressed_transcript_silences_resumed_reply() -> None:
    """Name in a committed turn while the reply is audible → cancel + flush + watchdog."""
    h = _solo_handler()
    _make_audible()
    h._response_done_event.clear()
    h._solo_speech_started()
    # Max-pause rollback happened; the reply is audible again.
    h._barge_pending = False
    h._resume_playback(rolled_back=True)
    assert h._barge_resumed_response_id == "resp_123"
    await h._late_solo_interrupt()
    h.connection.response.cancel.assert_awaited_once()
    h._clear_queue_callback.assert_called()
    assert h._barge_watchdog_task is not None  # the addressed turn must get an answer


@pytest.mark.asyncio
async def test_late_interrupt_with_no_resumed_id_still_silences() -> None:
    """Cooldown swallowed the pause: no resumed id, but the name must still stop the reply."""
    h = _solo_handler()
    _make_audible()
    h._response_done_event.clear()
    assert h._barge_resumed_response_id is None
    await h._late_solo_interrupt()
    h.connection.response.cancel.assert_awaited_once()
    h._clear_queue_callback.assert_called()


@pytest.mark.asyncio
async def test_late_interrupt_keeps_a_newer_response() -> None:
    """A live response newer than the resumed one IS the answer — do not kill it."""
    h = _solo_handler()
    _make_audible()
    h._response_done_event.clear()
    h._barge_resumed_response_id = "resp_old"
    h._active_response_id = "resp_new"
    await h._late_solo_interrupt()
    h.connection.response.cancel.assert_not_awaited()
    h._clear_queue_callback.assert_not_called()
```

(`_clear_queue_callback`, not `_clear_queue` — on `OpenAIRealtimeHandler` the latter is a wrapping property and the harness mock lands in the callback; Codex round 1, finding 13, and the comment at `tests/test_solo_barge.py:69-71`.)

- [ ] **Step 2: Run to verify failure** (`AttributeError: _late_solo_interrupt` / missing attribute).

- [ ] **Step 3: Implement.**

In `_resume_playback`, inside the `rolled_back` branch before `self._barge_paused_response_id = None` executes — capture first (move the assignment or capture above it):

```python
        resumed_id = self._barge_paused_response_id
        ...
        if not rolled_back:
            self._held_audio.clear()
            return
        self._barge_resumed_response_id = resumed_id
```

Initialize `self._barge_resumed_response_id = None` wherever the other barge fields are initialized (`__init__` — find the block that sets `_barge_paused_response_id`), and clear it in `on_external_interrupt` (and hence `_barge_reset_for_new_session`). **Lifecycle (Codex round 2, finding 1, superseding the round-1 ruling on finding 8):** the id is scoped to *the utterance that caused the rollback* — do **not** clear it on `response.created` (the answer's `response.created` routinely precedes that utterance's `transcription.completed`, and clearing early would let the late path mis-cancel the very answer the guard exists to protect). Instead clear it at the **end of the solo completed-transcript handling** for the turn (whether or not the late path fired) and in the `transcription.failed` handler — after that turn is decided, a lingering id would only suppress future real interrupts (the round-1 finding-8 hazard, still closed). Add the field to `_install_barge_state` in `tests/test_solo_barge.py:36-52`.

New method:

```python
    async def _late_solo_interrupt(self) -> None:
        """Silence a reply the transcript proved the user was talking over.

        The pause machinery already resolved (rolled back, cooled down, or was
        never armed), but the committed turn addresses the robot while it is
        audible. The newer-answer guard applies only when we actually have a
        resumed id to compare against (Codex round 1, finding 6): with no
        resumed id, an active response is simply the reply being talked over —
        refusing to cancel it would make the robot unsilenceable during the
        post-barge cooldown. The rare mis-cancel of a racing answer self-heals
        through the watchdog below.
        """
        resumed = self._barge_resumed_response_id
        answer_already_live = (
            resumed is not None
            and self._active_response_id is not None
            and self._active_response_id != resumed
        )
        if answer_already_live:
            logger.info("late solo interrupt: a newer response is live; leaving it be")
            return
        await self._cancel_active_response()
        if self._clear_queue:
            self._clear_queue()
        self._barge_resumed_response_id = None
        self._barge_cooldown_until = time.monotonic() + _barge_cooldown_s()
        # The addressed turn must not end in silence (Codex round 1, finding 5):
        # its auto-response may have been rejected against the reply we just
        # cancelled, so give the watchdog the repair duty, exactly as a
        # committed barge does.
        self._barge_response_seen = False
        self._arm_barge_watchdog()
```

In the completed-transcript handler, after the party-gate block (`:2209-2219`) and before the turn bookkeeping (`:2221`). Two same-transcript double-fire hazards must be closed (Codex round 1 finding 9; round 2 finding 2): a pause committed by `_resolve_solo_barge` in this very iteration, and a pause committed *earlier* by a partial delta of this same item (`_maybe_commit_on_partial` sets `self._barge_partial_committed_item = item_id`; Task 2 gains that assignment and the field, initialized `None`, reset in `on_external_interrupt`). Change `:2202-2203` to

```python
                        pause_committed = False
                        if self._barge_pending:
                            if await self._resolve_solo_barge(transcript):
                                continue
                            pause_committed = True
                        if event.item_id == self._barge_partial_committed_item:
                            # This turn already interrupted via its partial
                            # transcript; the reply now playing is its answer.
                            self._barge_partial_committed_item = None
                            pause_committed = True
```

then add:

```python
                        if (
                            not self._party_mode
                            and _solo_client_barge()
                            and not pause_committed
                            and self._robot_audible()
                        ):
                            accepted, reason = _gate_text_accepts(transcript)
                            if accepted and (reason == "control phrase" or _solo_name_gate()):
                                logger.info("late solo interrupt (%s) on committed turn", reason)
                                await self._late_solo_interrupt()
                        if not self._party_mode:
                            # The rollback's utterance is now decided either way;
                            # a lingering resumed-id would suppress a future real
                            # interrupt (round 2, finding 1 lifecycle).
                            self._barge_resumed_response_id = None
```

(`_solo_client_barge()` keeps the `REALTIME_SOLO_CLIENT_BARGE=0` legacy path untouched — Codex round 1, finding 7; the control-phrase arm works even with the name gate off — finding 12. The turn then continues normally: transcript emitted, `record_transcript`, and the server's auto-response or the watchdog supplies the answer.)

**Resumed-id cleanup, complete set (Codex round 3, finding 1):** the trailing clear block above only covers turns that reach it — three other exits must also clear `self._barge_resumed_response_id = None`: (a) inside `_resolve_solo_barge`'s rollback branch (a transcript-decided rollback fully decides the utterance, and its handler `continue`s before the clear block); (b) in `_resolve_solo_barge_failure` and the `transcription.failed` handler (`:2243-2254`) — which also clears `self._barge_partial_committed_item = None` (round 3, finding 2); (c) in the `response.done` handler (`:2134-2166`): when `getattr(getattr(event, "response", None), "id", None) == self._barge_resumed_response_id`, clear it — the bounded cleanup for a timer rollback whose speech never produced a transcript at all (the resumed reply finishing naturally is the end of that id's meaning). Task 2's `_maybe_commit_on_partial` signature becomes `(self, partial: str, item_id: str) -> None`, the delta-handler call passes `item_id`, and the method sets `self._barge_partial_committed_item = item_id` immediately after its `_commit_solo_barge()` — update Task 2's tests to pass an item id and assert the field.

- [ ] **Step 4: Run the barge module + `tests/test_huggingface_realtime.py`** — PASS.

- [ ] **Step 5: Lint, typecheck, commit** — `feat(voice): late name/control interrupt on committed turns`

---

### Task 5: `conversation.item.truncate` on committed interruptions

**Files:**
- Modify: `reachy_companion/src/reachy_companion/huggingface_realtime.py` (delta handler `:2267-2284`, `response.created` handler `:2113-2132`, `_commit_solo_barge` `:964-1012`, `_party_barge_confirm` `:705-721`, `_late_solo_interrupt` from Task 4)
- Test: `reachy_companion/tests/test_solo_barge.py`

**Interfaces:**
- Produces: `self._audio_item_id: str | None`, `self._audio_item_enqueued_ms: float`, `_heard_audio_ms() -> int`, `async _truncate_heard_audio(audio_end_ms: int) -> None`.
- Consumes: `audio_drain.outstanding_s()` (existing, `hanova/audio_drain.py:205`), `self.SAMPLE_RATE`, `self.connection.conversation.item.truncate(...)` (openai GA realtime connection resource; verify the exact resource path against the installed SDK — `connection.conversation.item` carries `create`/`retrieve`/`delete`/`truncate` senders; if the installed 2.28.0 surface lacks `truncate`, fall back to `await self.connection.send({"type": "conversation.item.truncate", "item_id": ..., "content_index": 0, "audio_end_ms": ...})`).

We are on WebSocket: the server never truncates for us (research §2). Without this, every cancelled reply leaves the model believing it said things nobody heard — which also feeds the "talks too much / repeats itself" feel.

`audio_end_ms` accounting: `note_enqueued` is called at delta reception for every frame (`:2281`), including frames later diverted to `_held_audio` (the divert happens downstream in `emit()`), and `note_chunk` retires only what reached the sink. So `enqueued_ms(item) − outstanding_s()·1000` is an upper bound on what was heard, and subtracting the residue slack (250 ms device-buffer estimate, `audio_drain._RESIDUE_SLACK_S`) plus the resampler priming (~32 ms) keeps us safely under the real duration. Constant: `_TRUNCATE_SLACK_MS: Final[int] = 300`. Always `max(0, int(...))`. Compute **before** `_clear_queue()` (which zeroes `outstanding` via `note_cleared`). VoiceFX time-domain effects preserve duration (comb/pitch, D-011/D-017), so source-ms ≈ played-ms; the slack absorbs the rest.

- [ ] **Step 1: Failing tests**

```python
@pytest.mark.asyncio
async def test_commit_sends_truncate_with_heard_ms() -> None:
    """A committed barge truncates the paused item at the heard position."""
    h = _solo_handler()
    truncate = AsyncMock()
    h.connection = SimpleNamespace(
        response=SimpleNamespace(cancel=AsyncMock()),
        conversation=SimpleNamespace(item=SimpleNamespace(truncate=truncate)),
    )
    generation = audio_drain.begin_response()
    h._audio_item_id = "item_abc"
    # 2000 ms enqueued for the item, 500 ms still outstanding → heard ≈ 1200 ms.
    h._audio_item_enqueued_ms = 2000.0
    audio_drain.note_enqueued(generation, sample_count=12000, sample_rate=24000)
    h._response_done_event.clear()
    h._solo_speech_started()
    await h._commit_solo_barge()
    truncate.assert_awaited_once()
    kwargs = truncate.await_args.kwargs
    assert kwargs["item_id"] == "item_abc"
    assert kwargs["content_index"] == 0
    assert 0 < kwargs["audio_end_ms"] <= 1200


@pytest.mark.asyncio
async def test_rollback_never_truncates() -> None:
    h = _solo_handler()
    truncate = AsyncMock()
    h.connection = SimpleNamespace(
        response=SimpleNamespace(cancel=AsyncMock()),
        conversation=SimpleNamespace(item=SimpleNamespace(truncate=truncate)),
    )
    _make_audible()
    h._audio_item_id = "item_abc"
    h._audio_item_enqueued_ms = 2000.0
    h._response_done_event.clear()
    h._solo_speech_started()
    await h._resolve_solo_barge("嗯")
    truncate.assert_not_awaited()


@pytest.mark.asyncio
async def test_zero_heard_ms_skips_truncate() -> None:
    """If nothing measurably played, do not send a truncate the server may reject."""
    h = _solo_handler()
    truncate = AsyncMock()
    h.connection = SimpleNamespace(
        response=SimpleNamespace(cancel=AsyncMock()),
        conversation=SimpleNamespace(item=SimpleNamespace(truncate=truncate)),
    )
    generation = audio_drain.begin_response()
    h._audio_item_id = "item_abc"
    h._audio_item_enqueued_ms = 400.0
    audio_drain.note_enqueued(generation, sample_count=9600, sample_rate=24000)  # all outstanding
    h._response_done_event.clear()
    h._solo_speech_started()
    await h._commit_solo_barge()
    truncate.assert_not_awaited()
```

Add `handler._audio_item_id = None` and `handler._audio_item_enqueued_ms = 0.0` to `_install_barge_state`.

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement.**

Module constant near the other barge constants: `_TRUNCATE_SLACK_MS: Final[int] = 300`.

Handler methods:

```python
    def _heard_audio_ms(self) -> int:
        """Milliseconds of the current audio item that provably reached the ear.

        enqueued − outstanding − slack, floored at 0: `audio_end_ms` above the
        item's real duration is a server error, so this always rounds DOWN
        (research doc §2). Undershoot costs a sentence fragment left in
        context; overshoot costs the whole truncate.
        """
        if self._audio_item_id is None:
            return 0
        outstanding_ms = audio_drain.outstanding_s() * 1000.0
        return max(0, int(self._audio_item_enqueued_ms - outstanding_ms - _TRUNCATE_SLACK_MS))

    async def _truncate_heard_audio(self, item_id: str, audio_end_ms: int) -> None:
        """Cut the server's copy of a cancelled reply at the heard position.

        WebSocket transport: the server never truncates on its own, so without
        this every barge leaves unheard text in the model's context. Commit
        paths only — truncation deletes the item's transcript server-side and
        cannot be rolled back.
        """
        if self.connection is None or audio_end_ms <= 0:
            return
        try:
            await self.connection.conversation.item.truncate(
                item_id=item_id, content_index=0, audio_end_ms=audio_end_ms
            )
        except Exception as exc:  # noqa: BLE001 - a stale/finished item is a benign race
            logger.debug("conversation.item.truncate refused: %s", exc)
```

**Accepted limitation (Codex round 1, finding 10):** `outstanding_s()` is global (one sink) while `_audio_item_enqueued_ms` is per-item, so residue from an earlier item or generation can only make `heard` *smaller* — i.e. under-truncate, the safe direction (context keeps a fragment; the server never errors). True per-item played accounting would need item identity plumbed through `console.play_loop`'s sink handoff — disproportionate for the POC; recorded in D-028.

**Device buffer (Codex round 2, finding 5, accepted):** audio handed to the sink can sit up to ~1 s in the device buffer (`audio_drain.py:5-7`), which `outstanding_s()` no longer counts — `enqueued − outstanding` therefore overstates what reached the ear. Add an accessor to `hanova/audio_drain.py`:

```python
def device_buffered_s() -> float:
    """Estimated seconds of sink-handed audio still inside the device buffer."""
    with _LOCK:
        return max(0.0, _DRAINED_AT - time.monotonic())
```

and subtract it in `_heard_audio_ms` alongside the slack: `... - outstanding_ms - audio_drain.device_buffered_s() * 1000.0 - _TRUNCATE_SLACK_MS`. (No server-error risk either way — the enqueued total bounds us under the item's real duration — this is heard-fidelity only.)

**Stash at pause (Codex round 2, finding 4, accepted):** the item to truncate is the one that was *paused*, and by commit time `_audio_item_id` may already belong to a newer response. `_pause_playback()` therefore stashes `self._barge_paused_item_id = self._audio_item_id` and `self._barge_paused_heard_ms = self._heard_audio_ms()` (a conservative floor — nothing new reaches the ear during a pause); `_resume_playback` clears both. `_commit_solo_barge` truncates the stashed pair **in both branches** — even when `answer_already_live` keeps the new response, the paused reply's tail was still dropped and its unheard text must still leave the context. The late-interrupt and party paths have no pause, so they compute `(self._audio_item_id, self._heard_audio_ms())` live before their flush. Both stash fields join the barge-state init, `on_external_interrupt`, and `_install_barge_state`.

Bookkeeping in the event loop:
- `response.created` handler: `self._audio_item_id = None`, `self._audio_item_enqueued_ms = 0.0`.
- `on_external_interrupt` (`:773-799`) — the operator-RPC/session-boundary reset — also clears both fields (Codex round 1, finding 11): a stale item id surviving a `conversation.interrupt` or a reconnect must not get truncated in a later session.
- `response.output_audio.delta` handler, after the cancelled-id drop and next to the existing `on_response_audio` call:

```python
                        item_id = getattr(event, "item_id", None)
                        if item_id is not None and item_id != self._audio_item_id:
                            self._audio_item_id = item_id
                            self._audio_item_enqueued_ms = 0.0
                        self._audio_item_enqueued_ms += (
                            (len(decoded_pcm_bytes) // 2) / float(self.SAMPLE_RATE) * 1000.0
                        )
```

Call sites (each captures its pair BEFORE any flush — `note_cleared` zeroes `outstanding`):
- `_commit_solo_barge`: right after `self._barge_pending = False`, capture `truncate_item, truncate_ms = self._barge_paused_item_id, self._barge_paused_heard_ms` (the stash from pause time — see the stash note above; `_resume_playback` inside the `finally` clears the stash fields, which is why they are captured first). In the block after the `finally` (next to the cooldown assignment), add `if truncate_item is not None: await self._truncate_heard_audio(truncate_item, truncate_ms)` — unconditionally, both the cancel and the `answer_already_live` branches (round 2, finding 4).
- `_party_barge_confirm`: before `await self._cancel_active_response()`, capture `item, ms = self._audio_item_id, self._heard_audio_ms()` (no pause exists in party mode); after the `_clear_queue()` call, `if item is not None: await self._truncate_heard_audio(item, ms)`.
- `_late_solo_interrupt` (Task 4): same live capture as party, before its `_clear_queue()`.

- [ ] **Step 4: Run** `python -m pytest tests/test_solo_barge.py tests/test_party_mode.py -v` — PASS.

- [ ] **Step 5: Lint, typecheck, commit** — `feat(voice): send conversation.item.truncate on committed interruptions`

---

### Task 6: Patience defaults + `reasoning.effort`

**Files:**
- Modify: `reachy_companion/src/reachy_companion/huggingface_realtime.py` (`_VAD_SILENCE_DURATION_DEFAULT_MS` `:178`, `_barge_confirm_s` default `:158`)
- Modify: `reachy_companion/src/reachy_companion/openai_realtime.py` (`_get_session_config` `:341-368`, new `_reasoning_effort()` near `_eagerness()`)
- Test: `reachy_companion/tests/test_openai_realtime_config.py`, `reachy_companion/tests/test_solo_barge.py`

**Interfaces:**
- Produces: env `REALTIME_REASONING_EFFORT` (default `low`; `off` omits the field); new defaults silence=1000 ms, confirm=1600 ms.

- [ ] **Step 1: Failing tests** — in `test_openai_realtime_config.py`, follow whichever existing test in that file already exercises `_get_session_config` / `_turn_detection` end-to-end and reuse *its* fixture (NOT `_emit_ready_handler`, which is wired for `emit()` only and lacks `instance_path`/`get_current_voice` — Codex round 2, finding 7; if only `_turn_detection`-level tests exist, test `_reasoning_effort()`/`_turn_detection()` directly and add one config-level test using the file's fullest handler fixture): assert (a) default `_turn_detection()["silence_duration_ms"] == 1000`; (b) `cfg["reasoning"] == {"effort": "low"}` by default; (c) `REALTIME_REASONING_EFFORT=off` omits `"reasoning"`; (d) `REALTIME_REASONING_EFFORT=minimal` passes through. In `test_solo_barge.py` assert `_barge_confirm_s() == pytest.approx(1.6)` and `_vad_silence_duration_ms() == 1000` at defaults. Every new reasoning/tokens test starts with `monkeypatch.delenv("REALTIME_REASONING_EFFORT", raising=False)` (and `REALTIME_MAX_OUTPUT_TOKENS`) — the file's autouse cleanup only covers `VOICEFX_*`, so a developer's shell env would otherwise flake the default assertions (Codex round 3, finding 3). **Also update the existing default assertions** — `test_vad_defaults_when_env_is_unset` and the malformed-env fallback tests in `test_openai_realtime_config.py` (and any `test_solo_barge.py` guard tests) currently assert 800/1400; they must be updated to 1000/1600 in this task, not weakened (Codex round 2, finding 6).

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement.**

`huggingface_realtime.py`: `_VAD_SILENCE_DURATION_DEFAULT_MS = 1000` (update its comment: server default is 500; 800 shipped since D-023; 1000 is the operator's "don't rush me" request, still under the ~1100 ms sluggishness knee — research doc §1). `_barge_confirm_s` default `1400 → 1600` (keeps the ≥400 ms margin over the silence window the docstring argues for; update the docstring numbers).

`openai_realtime.py`, near `_eagerness()`:

```python
_REASONING_EFFORTS = ("minimal", "low", "medium", "high", "xhigh")


def _reasoning_effort() -> str | None:
    """Session `reasoning.effort`; `low` is the documented voice-agent default.

    gpt-realtime-2.x reasons before speaking; unpinned, a future server-side
    default change silently adds pre-speech latency. `off` omits the field
    entirely (server default). Research doc §1.
    """
    raw = (os.getenv("REALTIME_REASONING_EFFORT") or "low").strip().lower()
    if raw == "off":
        return None
    if raw not in _REASONING_EFFORTS:
        logger.warning("Ignoring invalid REALTIME_REASONING_EFFORT=%r; using low.", raw)
        return "low"
    return raw
```

In `_get_session_config`, after the transcription line (`:367`):

```python
        effort = _reasoning_effort()
        if effort is not None:
            # The installed 2.28.0 TypedDict predates `reasoning` (verified:
            # RealtimeSessionCreateRequestParam has no such key), but the field
            # is documented GA for gpt-realtime-2.x and TypedDicts are plain
            # dicts at runtime — same precedent as `keywords` and the 16 kHz
            # format. Codex round 1, finding 1.
            cast(dict[str, Any], cfg)["reasoning"] = {"effort": effort}
```

**Rejection safety (Codex round 1, finding 1, accepted in part):** a `session.update` the server refuses must not mute the robot at boot. Extend the OpenAI subclass's `_session_config_fallback` (`openai_realtime.py:370-380`) so its retry config also strips the `reasoning` key when present — via the same runtime-dict cast, `cast(dict[str, Any], fallback_cfg).pop("reasoning", None)`, since the TypedDict has no such key for mypy (Codex round 2, finding 8) — alongside the existing legacy-transcription downgrade, and add a config test asserting the fallback config carries no `reasoning`. `max_output_tokens` needs no such treatment — it IS in the installed stub.

- [ ] **Step 4: Run** `python -m pytest tests/test_openai_realtime_config.py tests/test_solo_barge.py -v` — PASS. Also run the full suite once here; the silence-default bump can ripple into timing-adjacent tests.

- [ ] **Step 5: Lint, typecheck, commit** — `feat(voice): patience defaults (silence 1000ms, confirm 1600ms); pin reasoning.effort=low`

---

### Task 7: `max_output_tokens` rail + incomplete-status logging

**Files:**
- Modify: `reachy_companion/src/reachy_companion/openai_realtime.py` (new `_max_output_tokens()`, wire into `_get_session_config`)
- Modify: `reachy_companion/src/reachy_companion/huggingface_realtime.py` (`response.done` handler `:2134-2166`)
- Test: `reachy_companion/tests/test_openai_realtime_config.py`, `reachy_companion/tests/test_huggingface_realtime.py`

**Interfaces:**
- Produces: env `REALTIME_MAX_OUTPUT_TOKENS` (default 900; `inf`/`off`/`0` disables; clamped 1–4096).

- [ ] **Step 1: Failing tests** — config: default `cfg["max_output_tokens"] == 900`; `REALTIME_MAX_OUTPUT_TOKENS=inf` omits the field; `=200` passes 200; `=99999` clamps to 4096. Event handling (in `test_huggingface_realtime.py`, using that file's existing event-loop test scaffolding): a `response.done` whose `event.response.status == "incomplete"` with `status_details.reason == "max_output_tokens"` logs a warning (assert via `caplog`).

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement.**

```python
_MAX_OUTPUT_TOKENS_DEFAULT = 900


def _max_output_tokens() -> int | None:
    """Per-reply token ceiling — a runaway-monologue rail, not a brevity knob.

    ~20-25 output tokens per spoken second, so 900 ≈ 40 s of speech. Hitting
    it cuts the reply MID-WORD with no wrap-up (`response.done` status
    `incomplete`/`max_output_tokens` — research doc §3), which is why the
    default is loose and the trip is logged as a warning. Brevity itself is
    the prompt's job (persona + hardening block).
    """
    raw = (os.getenv("REALTIME_MAX_OUTPUT_TOKENS") or "").strip().lower()
    if raw in ("inf", "off", "0"):
        return None
    if not raw:
        return _MAX_OUTPUT_TOKENS_DEFAULT
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "Ignoring invalid REALTIME_MAX_OUTPUT_TOKENS=%r; using %d.",
            raw,
            _MAX_OUTPUT_TOKENS_DEFAULT,
        )
        return _MAX_OUTPUT_TOKENS_DEFAULT
    return max(1, min(value, 4096))
```

Wire into `_get_session_config` beside the reasoning field: `tokens = _max_output_tokens()` / `if tokens is not None: cfg["max_output_tokens"] = tokens`.

In the `response.done` handler (`huggingface_realtime.py`, after `:2137`):

```python
                        response_obj = getattr(event, "response", None)
                        status = getattr(response_obj, "status", None)
                        if status not in (None, "completed"):
                            details = getattr(response_obj, "status_details", None)
                            reason = getattr(details, "reason", None)
                            if reason == "max_output_tokens":
                                logger.warning(
                                    "Reply cut off by REALTIME_MAX_OUTPUT_TOKENS "
                                    "(status=incomplete); raise the rail if this recurs"
                                )
                            else:
                                logger.info("response ended status=%s reason=%s", status, reason)
```

- [ ] **Step 4: Run both test files** — PASS.

- [ ] **Step 5: Lint, typecheck, commit** — `feat(voice): max_output_tokens safety rail + incomplete-status logging`

---

### Task 8: Brevity prompts — hardening block + persona

**Files:**
- Modify: `reachy_companion/src/reachy_companion/prompts.py` (`_HARDENING_BLOCK` `:25-46`)
- Modify: `persona.md` (repo root — operator instance persona, D-016; Conversation section `:30-37`)
- Test: `reachy_companion/tests/test_prompts_hardening.py`

**Interfaces:** none new — text only.

**Operator direction (2026-08-31, binding):** no flat "1–2 sentences" cap — "it is over strict. The model need to know when to explain more, when to be concise." The prompt therefore teaches *calibration* (length follows content), not a number. This matches the official Verbosity guidance's actual framing ("Define what concise means in context: direct answers, tool results, troubleshooting, comparisons … may each need different response lengths" — research doc §3). The persona's existing `簡單問題直接回答；複雜問題才多解釋` line already carries this instinct and stays untouched; what gets cut is padding, repetition, and preambles — the things that make it *feel* talkative regardless of length.

- [ ] **Step 1: Failing test** — in `test_prompts_hardening.py`, following its existing assertions on `hardening_block()` content: assert the block contains `"回答長度"`, `"長度跟著內容走"`, and `"前導語"`.

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement.** Append to `_HARDENING_BLOCK` (before the closing `"""`):

```
### 回答長度
- 長度跟著內容走：能一句話答完的就一句話答完；值得展開的話題
  （解釋、教學、故事、對方明顯想深聊）就好好講，不用縮短。
- 但不管長短，都不要塞填充內容：不要重複對方剛說的話、不要重述
  自己前一句的意思、不要加沒人問的背景說明。
- 一次只問一個澄清問題。
- 工具結果：先講結果本身，再看情況補充；不要逐項朗讀原始資料。
- 不要使用「讓我想想」「我看一下喔」這類前導語；只有真的需要等待的
  工具操作才可以先講一句在做什麼。
- 你說話說到一半時聽到的聲音，如果沒有叫你的名字、也不是明顯在對你
  說話，就當作不是在跟你說話：繼續原本的話題，不要停下來回應。
```

`persona.md` Conversation section: leave `一般回答保持短而自然，通常 1～3 句` and `簡單問題直接回答；複雜問題才多解釋` as they are (the calibration already lives there); only add `- 開口就講重點，不要用「讓我想想」「跟你說喔」之類的前導語開場。` after the 長篇演講 line. (Persona reaches the robot via the operator's scp+sha ritual — flag in the final report that a re-sync is owed, as with the D-027 persona change.)

- [ ] **Step 4: Run** `python -m pytest tests/test_prompts_hardening.py tests/test_persona.py -v` — PASS.

- [ ] **Step 5: Lint, commit** — `feat(prompts): verbosity + preamble rules in hardening block and persona`

---

### Task 9: Docs, env reference, work-queue rows

**Files:**
- Modify: `reachy_companion/.env.example` (`:36-152` knob section), `README.md` (`:163-166` env table)
- Modify: `DECISIONS.md` (append D-028), `feature_list.json`, `progress.md`, `docs/research-realtime-voice-best-practices.md` (corrections header)

**Interfaces:** none — documentation of everything above.

- [ ] **Step 1: `.env.example` + README.** Document (commented out, code-default style like the existing entries): `REALTIME_SOLO_NAME_GATE` (default 1), `REALTIME_BARGE_MAX_PAUSE_MS` (4000), `REALTIME_VAD_SILENCE_DURATION_MS` (new default 1000), `REALTIME_BARGE_CONFIRM_MS` (1600), `REALTIME_REASONING_EFFORT` (low), `REALTIME_MAX_OUTPUT_TOKENS` (900), `REALTIME_TRANSCRIPTION_DELAY` (unset). Note beside the VAD block: `semantic_vad + eagerness=low` is the patience A/B (`VOICE-SEMANTIC-VAD-AB`).

- [ ] **Step 2: `DECISIONS.md` D-028** — record: name gate default-on and why gate scope is interruption-only (design decision 1); max-pause semantics; truncate-on-commit-only; patience numbers; token rail is a rail; the confirm-warning fix; the rejected alternatives (solo `create_response=false`; acoustic wake word — out of POC scope, research §4).

- [ ] **Step 3: `feature_list.json`** — add rows (state `implemented-unverified`, verification = live on-device): `VOICE-NAME-GATE` (talk over Reachy without the name → journal `solo barge rolled back (unaddressed)` and the reply resumes; say 「瑞奇」 mid-reply → stops < ~1 s; 「停」 alone → stops), `VOICE-LATE-INTERRUPT` (address it with a long sentence → reply resumes at the cap, then stops when the transcript lands), `VOICE-TRUNCATE` (after an interruption, ask 「你剛剛說到哪」 — the model should not believe it finished; journal shows no truncate errors), `VOICE-PATIENCE` (mid-sentence Mandarin pauses ~1 s no longer commit turns), `VOICE-BREVITY` (subjective: reply length tracks the content — short for simple questions, still willing to expand when asked to explain; no preambles, no repetition/padding; `max_output_tokens` warning absent in normal use). Update `VOICE-SOLO-BARGE`'s expected journal lines (rollback reasons renamed) and note `VOICE-SEMANTIC-VAD-AB` now also covers the eagerness=low patience trial.

- [ ] **Step 4: `progress.md`** — next-action: seventeenth install carries this wave; persona re-sync owed.

- [ ] **Step 5: Research-doc corrections** — add a dated pointer at the top of `docs/research-realtime-voice-best-practices.md`: "2026-08-30: §transcription and §truncate guidance superseded in parts by `research-realtime-api-2026-08.md` (gpt-live-transcribe + delay; truncate gap closed)."

- [ ] **Step 6: Full gates then commit** — `python -m pytest` (expect ≥ baseline 1468/30), `ruff check .`, `mypy --strict src`. Commit: `docs: D-028 name-gated barge-in wave; env + feature rows`

---

## Review log (Codex)

**Round 1** (2026-08-30, 14 findings — 11 accepted, 3 accepted in part / rejected with reason):

1. `reasoning` not in installed SDK stub — **accepted in part**: verified true (stub has no key); kept with runtime-dict cast + fallback strip, since the field is documented GA and killing the session on rejection is the real risk. Not dropped: pinning effort is the point.
2. `delay`/`gpt-live-transcribe` not in stubs — **rejected as blocker, accepted as note**: `_transcription()` already ships stub-unknown `keywords` through `cast(Any, ...)` and works live; `delay` is opt-in and rides the same pattern (noted in Task 2).
3. Partial accumulator replaces instead of appends — **accepted**: verified (`deltas = [delta]` snapshot semantics); OpenAI subclass override with append semantics added to Task 2.
4. Max-pause resumes while speech open — **rejected as defect, accepted as doc gap**: it is the requested human behavior (talk through unaddressed chatter); made explicit + already tested (Task 3 note).
5. Late interrupt could leave the addressed turn unanswered — **accepted**: `_late_solo_interrupt` now arms the barge watchdog.
6. Newer-answer guard wrong when no resumed id — **accepted**: guard requires `resumed is not None`; mis-cancel edge self-heals via finding-5 watchdog; test added.
7. Late path ignores `REALTIME_SOLO_CLIENT_BARGE=0` — **accepted**: `_solo_client_barge()` added to the condition.
8. Stale `_barge_resumed_response_id` — **accepted**: cleared on `response.created` (ownership change) and in resets.
9. Late block double-fires after a committed pause — **accepted**: `pause_committed` local skips it.
10. Global outstanding vs per-item enqueued — **accepted as limitation, rejected as change**: contamination only under-truncates (safe direction); per-item sink plumbing disproportionate; recorded in D-028.
11. Truncate fields not reset on external interrupt — **accepted**: cleared in `on_external_interrupt`.
12. Control phrases gated behind the name-gate flag in new paths — **accepted**: control arm now unconditional in partial + late paths.
13. `_clear_queue` vs `_clear_queue_callback` in tests — **accepted**: asserts corrected.
14. Existing confirm/warning tests break under gate-on default — **accepted**: migration note in Task 3 (pin `REALTIME_SOLO_NAME_GATE=0` in legacy-semantics tests).

**Round 2** (2026-08-30, 9 findings — all accepted, two amending round-1 rulings):

1. Clearing the resumed-id on `response.created` destroys the newer-answer guard (answers routinely precede their turn's completed transcript) — **accepted, supersedes round-1 ruling on finding 8**: id now cleared at end of the turn's completed/failed transcript handling instead.
2. A partial-triggered commit could let the same item's completed transcript late-interrupt its own answer — **accepted**: `_barge_partial_committed_item` tracks the resolved item; the completed handler treats it as `pause_committed`.
3. Split-name accumulation untested — **accepted**: incremental-delta test added (Task 2).
4. `answer_already_live` branch dropped the paused reply's audio without truncating it — **accepted**: `(item_id, heard_ms)` stashed at pause time; commit truncates the stash in both branches.
5. Device-buffer time missing from `_heard_audio_ms` — **accepted**: `audio_drain.device_buffered_s()` accessor added and subtracted.
6. Existing 800/1400 default assertions — **accepted**: updated in Task 6.
7. `_emit_ready_handler` unfit for session-config tests — **accepted**: Task 6 step reworded to the file's real config fixtures.
8. `pop("reasoning")` vs mypy strict — **accepted**: runtime-dict cast.
9. Global constraint contradicted Tasks 8–9's root-file edits — **accepted**: constraint enumerates the allowed root files.

**Round 3** (2026-08-30, 1 major + 2 minor — all accepted; the stash design and `device_buffered_s()` explicitly verified sound):

1. Resumed-id leaks past the rollback `continue`s and no-transcript timer rollbacks — **accepted**: cleared in the rollback branch of `_resolve_solo_barge`, in the failure paths, and on the resumed response's own `response.done` (Task 4 cleanup set).
2. `_barge_partial_committed_item` not cleared on `transcription.failed` — **accepted**: cleared there.
3. Reasoning/tokens default tests flaky against a developer's shell env — **accepted**: explicit `delenv` in each new test.

Review closed after the contract's 3 rounds (round 3 still yielded accepted findings, all incorporated above; nothing open).

## Verification after implementation

Runnable evidence (SDK-simulated): full pytest suite + the new tests above. The behavior rows themselves are live/human (`feature_list.json` rows in Task 9) and ride the **seventeenth install** via the `reachy-deploy` ritual; persona.md additionally needs the operator scp+sha re-sync. Residual risks to record if they cannot be tested on-device: truncate precision (community reports partial trims — research §2), semantic-VAD Mandarin behavior (A/B owed), `gpt-live-transcribe` partial latency (A/B owed).
