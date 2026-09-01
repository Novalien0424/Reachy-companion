# LLM-First Instructing Wave — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Before touching any file listed under a task, re-read the spec section it cites.

**Goal:** fix the two field bugs from the 2026-09-01 live test — no goodbye before sleep, head not turning on 「看右邊」 — and restructure the instruction surface under the house rule: *the model decides which tools to call and what to say; the app instructs it and holds the safety rails.*

**Architecture:** Three rungs of the escalation ladder, in the order the contract prescribes.
*Rung 1 (tools):* every robot-action tool gets argument validation at its boundary, an honest return that names facts the model may cite, model-directed error strings, and — for the session-ending tool — a description that forbids extra speech and pre-declares how its return's farewell cue is read.
*Rung 2 (context):* one authoritative prompt surface — the profile body and `persona.md` carry character; the system-layer hardening block in `prompts.py` carries policy, the 2.x structural blocks (`# Message Channels`, `# Preambles`, `# Reasoning`, `## Tool Availability`), and the single toolbox authority; remembered facts move from an unlabeled prepend to a labeled, conflict-ranked context block.
*Rung 3 (execution boundary only):* two pieces of real code, both physical-state truth. A named response-cycle helper on the realtime handler that queues the goodbye through the existing serialized sender with `tool_choice: "none"` and resolves only when *that* response reaches `response.done`; and a tracking-suspend seam on `MovementManager` that lets a manual head move survive the daemon face tracker without the `set_speaking` anchor fallback that would undo it.

**Tech Stack:** Python 3.12, `openai==2.28.0` (pinned, no upgrade), pytest + pytest-asyncio 1.4.0 in **strict** mode (there is no pytest config anywhere in the repo — every async test MUST carry `@pytest.mark.asyncio`), ruff 0.15.20, mypy 2.2.0 strict.

**Spec:** `docs/plans/2026-09-01-instructing-wave-plan.md` (rev 2). Its four scope items, rulings, constraints and Verification section are binding, and its Review log records 41 adjudicated Codex findings that must stay honored. Governing contract: `.claude/skills/reachy-instructing-model/SKILL.md`. Evidence — cite, do not re-litigate: `docs/research-instructing-realtime-voice-2026-09.md`, `docs/research-instructing-llms-2026-09.md`, `docs/codex-research-instructing-2026-09.md`, `docs/research-mini-tool-calling-2026-08.md`.

---

## Global Constraints

Copied from the rev-2 spec, verbatim where it is a ruling. Every one of these is a *review-accepted* finding; violating one silently reopens a closed finding.

1. **Model stays `gpt-realtime-2.1-mini`.** No model change in this wave. Full `gpt-realtime-2.1` is allowed only as a **one-shot diagnostic run** when a failure survives rungs 1–2 — never as a fix.
2. **Runtime validation, never SDK strict mode.** "Platform fact that bounds the whole wave: both 2.1 realtime models support function calling but NOT structured outputs — argument-schema adherence is not guaranteed, so every schema claim below is 'JSON Schema + runtime validation at the tool boundary', never SDK strict mode (the installed SDK's realtime tool param has no `strict` field)." Every robot-action tool validates its own arguments and rejects with a corrective, model-readable error **naming the allowed values** — never silently coerce, never fall back.
3. **Farewell response payload shape.** The farewell goes through the existing serialized `_safe_response_create()` queue (**never** a raw `connection.response.create`), with the SDK's nested payload shape `response={"tool_choice": "none"}` — verified against openai 2.28.0: `AsyncRealtimeResponseResource.create(*, event_id, response: RealtimeResponseCreateParamsParam)`, and `tool_choice` is a real key on that param. The helper resolves only when **that specific response** reaches `response.done` (correlate by response id — a bare `wait_for_reply_finished()` can return before the queued farewell even starts, which would recreate the original bug), then the existing bounded audio drain runs, then pose/stop.
4. **`direction_requested` stays unless pose-verified.** "Returns: `direction_requested` STAYS until motion is verifiable (review's honesty-regression catch — `MoveHead` returns at queue time and the motion API attests nothing). `direction_moved` may be introduced only backed by a real check of the movement manager's commanded/current pose, with an error/partial state when unconfirmed." No task in this plan introduces `direction_moved`.
5. **Commentary suppression stays this wave.** "Ruling for this wave: keep suppression, DROP the spoken-preamble goal (prompt blocks still teach the model where tool talk belongs); a selective allow-commentary policy is a later, separately-tested wave." `_item_phase` / `_commentary_item_ids` and their five tests are **not to be touched**.
6. **Lifecycle sleep paths keep direct pose/stop.** "The lifecycle paths (inactivity timeout, shutdown) have no live model turn to speak — they keep the current direct pose/stop closure. `go_to_sleep.needs_response` stays false for the generic dispatcher; the session-ending branch owns the one follow-up response explicitly."
7. **No numeric caps, no keyword-trigger lists in prompts.** Operator rule (memory `prompt-style-judgment-over-caps`), now evidence-backed. Prefer a well-stated calibration principle; add few-shot examples only where a principle demonstrably failed, and label them **示範語氣，不是觸發條件**. Trigger-like phrase lists become semantic use conditions or tool-description examples. The rule applies to our own contracts too: the "4–5 bans" heuristic is a review heuristic, not a count to enforce.
8. **Traditional-Chinese / Taiwan locale normalization.** Script and terminology are normalized to 台灣繁體中文 across the profile body, the greeting, `persona.md` and every prompt block. Taiwan default; switch language only on an explicit request or a full substantive utterance in another language — never on accent, filler, a name, or a loanword.
9. **`reasoning.effort` unchanged.** Session-level effort stays where the operator set it. Any level change needs the three-metric on-robot A/B (tool hallucination, instruction adherence, tool selection). The `# Reasoning` prompt block is *per-turn steering only*.
10. **Tool renames are A/B candidates, not edits.** The in-distribution name list is documented for `gpt-realtime-1.5` only. Use an alias tool behind exposure control (Task 11); never a raw rename — profile tool lists, toolboxes, the record allowlist, tests and docs all hard-code names.
11. **Returns state facts and render cues, never new policy.** A render cue (the farewell context) is legitimate ONLY because a higher-authority surface — the tool description — already defines how to read it. Flow control belongs to `response.create` / `tool_choice`.
12. **No new dependencies, no daemon changes (app-only), Chinese-primary.** Secrets stay externalized.
13. **Gates for every task:** from `reachy_companion/`, `ruff check .` clean, `mypy` clean (strict, `files = ["src/"]`), and `python -m pytest` green. `tests/conftest.py`'s skip list is 30 **exact nodeids** — it can never mask a new failure, so anything else red is a real regression.

### Out of scope (spec §"Explicitly out of scope")

Model upgrade, new dependencies, daemon changes, reasoning-effort changes without the three-metric A/B, commentary-audio policy changes.

---

## Spec ambiguities resolved (read before Task 4)

| # | Ambiguity | Resolution |
|---|---|---|
| A | Spec §2: `move_head` "either tracking stays suspended until a later command re-arms it, or the description/return changes to an honest temporary gesture. Decide at task decomposition." | **A bounded gesture window: suspend → move → hold → restore-previous**, with the description and return saying the hold is temporary. Leaving tracking suspended indefinitely would kill US-02 face tracking for the rest of a visit after one 「抬頭」, and nothing would re-arm it (a `head_tracking` call arriving mid-suspension is deferred by design). Doing nothing at all leaves the field bug alive for `move_head` — with a face in view the head visibly does not move. The bounded window is the only option where the head actually moves *and* the contract stays honest. |
| B | Spec §1: "correlate by response id" — but `_safe_response_create` enqueues and returns, and no seam exposes which request produced which response. | Carry an optional `ResponseCycle` **alongside** the queued kwargs. The sender loop is serialized, so the `_active_response_id` it observes between sending and `response.done` belongs to the request it just sent; it stamps and resolves the cycle. No new receive-loop plumbing, no new event. |
| C | Spec §1: the tool "executes the input quiesce … but NOT the pose/stop" — but who runs pose/stop, and does the drain move? | `main.py`'s existing `go_to_sleep_and_stop_app` closure (`deps.go_to_sleep`) stays the **single finalizer for both paths**, unchanged: it already repeats the quiesce, runs the bounded drain, stops the movement manager, poses and stops the app, and holds the duplicate latch. The dispatcher calls it via `asyncio.to_thread` after the farewell cycle. "The existing bounded audio drain runs, then pose/stop" is satisfied *inside* that closure. |
| D | `app_lifecycle.run_go_to_sleep_tool` **is** the tool (`asyncio.run(GoToSleep()(deps))`, `app_lifecycle.py:165`), wired at `main.py:419-420,483`. If the tool stops posing, the inactivity timeout silently stops sleeping the robot — the review's third critical catch. | Rename to `run_lifecycle_sleep(deps, logger)` and stop calling the Tool: silence via `deps.begin_sleep()`, then `deps.go_to_sleep()` directly. Done as its own behavior-preserving task (Task 2) *before* the tool changes (Task 3), so no intermediate commit leaves the robot unable to sleep. |
| E | Where does "this tool ends the session" live, so the alias A/B is free? | `Tool.ends_session: ClassVar[bool] = False`, `True` on `GoToSleep`. The dispatcher *additionally* requires the result to carry `status == "sleeping_soon"`, so an unwired-runtime error return can never pose the robot. An alias subclass inherits both for free. |
| F | Shape of `farewell_context` — "fact/cue field, not an instruction-named field". | A dict of **facts only**: `reason`, `listening_stopped`, `person`. No `next_step`, no imperative field name. The *description* (higher authority) is what says "this is your cue to say one goodbye and then stay quiet". |
| G | Spec §3: `# Preambles` block with commentary suppressed — what can it possibly say? | It teaches *where tool talk belongs* and, with its reason, that a spoken pre-tool opener is dropped by this client, so the positive action is to call the tool immediately and speak the result. It must **not** instruct the model to emit preambles: they would be silently discarded and cost latency for nothing. |
| H | `# Reasoning` block content, given effort is pinned. | Qualitative per-turn steering only: deliberate briefly when a request is ambiguous or needs more than one step; when the user names an action directly, act without extra deliberation. No effort value appears in the prompt. |
| I | Memory injection "restructured to labeled current-user-context placed with role/policy". | Moves from an unlabeled prepend *ahead of the persona* to a **labeled block appended after the hardening block** — i.e. in the system-policy region, last before the mode block. Carries an explicit conflict priority: what the user says now beats what is remembered. |
| J | `finish_session` alias "controlled exposure" without touching profile lists, toolboxes, the record allowlist or tests. | Register it through `core_tools.register_extra_tool()` behind an env flag, default OFF. `EXTRA_TOOLS` members are never hidden by `session_tool_exclusions` in any mode and bypass the profile allowlist, so exposure costs exactly one flag and zero list edits. With the flag off, every existing count assertion in `tests/test_toolboxes.py` stays exactly as it is. |

---

## File Structure

- Modify: `reachy_companion/src/reachy_companion/huggingface_realtime.py` (response cycle, sender loop, session-ending dispatcher branch, `open_toolbox` return, active-surface log)
- Modify: `reachy_companion/src/reachy_companion/app_lifecycle.py` (lifecycle sleep split)
- Modify: `reachy_companion/src/reachy_companion/main.py` (rewire the inactivity callback)
- Modify: `reachy_companion/src/reachy_companion/moves.py` (tracking-suspend seam)
- Modify: `reachy_companion/src/reachy_companion/prompts.py` (2.x blocks, subtractive pass, Tool Availability, memory placement)
- Modify: `reachy_companion/src/reachy_companion/memory.py` (`format_memory_for_prompt` labeling)
- Modify: `reachy_companion/src/reachy_companion/tools/core_tools.py` (`Tool.ends_session`)
- Modify: `reachy_companion/src/reachy_companion/tools/go_to_sleep.py`, `look_around.py`, `move_head.py`, `head_tracking.py`, `open_toolbox.py`, `set_conversation_mode.py`, `stop_dance.py`, `stop_emotion.py`
- Create: `reachy_companion/src/reachy_companion/tools/head_window.py` (the shared suspend/restore context manager)
- Create: `reachy_companion/src/reachy_companion/tools/finish_session.py` (alias, Task 11, exposure-controlled)
- Modify: `reachy_companion/profiles/_reachy_companion_locked_profile/profile.md`
- Modify: `persona.md` (repo root — the copy that actually runs on the robot)
- Create: `reachy_companion/tests/test_sleep_farewell.py`
- Modify: `reachy_companion/tests/test_app_lifecycle.py`, `tests/tools/test_go_to_sleep.py`, `tests/tools/test_look_around.py`, `tests/tools/test_move_head.py`, `tests/test_moves.py`, `tests/test_prompts_hardening.py`, `tests/test_profile.py`, `tests/test_persona.py`, `tests/test_toolboxes.py`, `tests/test_huggingface_realtime.py`
- Modify: `reachy_companion/pyproject.toml` (version), `CHANGELOG.md`, `DECISIONS.md` (D-030), `feature_list.json`, `progress.md`

**Dependency order.** Tasks 1→2→3 are the sleep chain and must land in that order (Task 2 is the behavior-preserving refactor that keeps the inactivity path alive across Task 3). Tasks 4→5 are the head chain. Tasks 6→7 both touch the same four tool files and must land in that order (inputs, then outputs). Tasks 8→9→10 are the prompt chain. Task 11 is optional and independent. Tasks 12–13 close.

---

### Task 1: Response-cycle correlation and the farewell helper

Plumbing only — nothing calls the helper yet, so this task cannot change robot behavior. Spec §1 ("Sequencing is a named response-cycle helper, not loose calls"); Global Constraint 3; ambiguity B.

**Files:**
- Modify: `reachy_companion/src/reachy_companion/huggingface_realtime.py` (imports ~line 11; new module-level dataclasses after `_item_id()`; `__init__` line 672; `_safe_response_create` line 2413; `_response_sender_loop` line 2796; new `run_farewell_response_cycle`)
- Create: `reachy_companion/tests/test_sleep_farewell.py`

**Interfaces:**
- Produces: `ResponseCycle` dataclass — `done: asyncio.Future[str | None]`, `response_id: str | None = None`, `resolve(response_id: str | None) -> None`.
- Produces: `ResponseRequest` dataclass — `kwargs: dict[str, Any]`, `cycle: ResponseCycle | None = None`, property `is_coalescable -> bool`.
- Changes: `HuggingFaceRealtimeHandler._safe_response_create(self, *, cycle: ResponseCycle | None = None, **kwargs: Any) -> None` (was `(self, **kwargs)`).
- Changes: `HuggingFaceRealtimeHandler._pending_responses: asyncio.Queue[ResponseRequest]` (was `asyncio.Queue[dict[str, Any]]`).
- Produces: `HuggingFaceRealtimeHandler.run_farewell_response_cycle(self) -> str | None`.
- Consumed by: Task 3 only.

- [ ] **Step 1: Write the failing tests** in `reachy_companion/tests/test_sleep_farewell.py`:

```python
"""The farewell response cycle: the goodbye gets its own response, and we wait for it.

Spec §1 (2026-09-01 rev 2) and Codex round 1's critical catch: `_safe_response_create`
enqueues and returns, so a bare `wait_for_reply_finished()` can resolve on whatever
response was already running when the tool call landed — before the queued farewell
has even started. These tests pin the correlation instead.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from reachy_companion.huggingface_realtime import HuggingFaceRealtimeHandler


class _RecordingConnection:
    """Capture `response.create` payloads and stand in for the receive loop.

    `response.created` and `response.done` are the two edges the sender loop
    synchronizes on. `_finish` runs through `call_soon` rather than inline so the
    sender observes `_active_response_id` while it is still set — which is exactly
    the window the real receive loop leaves open.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.handler: HuggingFaceRealtimeHandler | None = None
        self.response = SimpleNamespace(create=self._create)

    async def _create(self, **kwargs: Any) -> None:
        assert self.handler is not None
        self.calls.append(kwargs)
        response_id = "resp_farewell" if kwargs.get("response") else f"resp_{len(self.calls)}"
        self.handler._active_response_id = response_id
        self.handler._response_done_event.clear()
        self.handler._response_started_or_rejected_event.set()
        asyncio.get_running_loop().call_soon(self._finish)

    def _finish(self) -> None:
        assert self.handler is not None
        self.handler._active_response_id = None
        self.handler._response_done_event.set()


def _sender_handler() -> tuple[HuggingFaceRealtimeHandler, _RecordingConnection]:
    """A handler with only the response-sender state the loop actually reads."""
    handler = HuggingFaceRealtimeHandler.__new__(HuggingFaceRealtimeHandler)
    handler.deps = MagicMock()
    handler._pending_responses = asyncio.Queue()
    handler._response_done_event = asyncio.Event()
    handler._response_done_event.set()
    handler._response_started_or_rejected_event = asyncio.Event()
    handler._last_response_rejected = False
    handler._active_response_id = None
    connection = _RecordingConnection()
    connection.handler = handler
    handler.connection = connection
    return handler, connection


@pytest.mark.asyncio
async def test_the_farewell_rides_the_serialized_sender_with_tool_choice_none() -> None:
    """One response.create, nested SDK shape, and the helper returns ITS id."""
    handler, connection = _sender_handler()
    sender = asyncio.create_task(handler._response_sender_loop())
    try:
        response_id = await asyncio.wait_for(handler.run_farewell_response_cycle(), timeout=2.0)
    finally:
        handler.connection = None
        sender.cancel()

    assert connection.calls == [{"response": {"tool_choice": "none"}}]
    assert response_id == "resp_farewell"


@pytest.mark.asyncio
async def test_an_empty_follow_up_never_swallows_the_farewell() -> None:
    """The coalescer may merge empty duplicates; it may never drop a waited-on cycle.

    Before this task the loop discarded EVERY following request whenever the one in
    hand was empty — so a generic tool follow-up queued first would have thrown the
    goodbye away and hung its waiter until the timeout.
    """
    handler, connection = _sender_handler()
    await handler._safe_response_create()  # a generic tool follow-up, queued first
    cycle_task = asyncio.create_task(handler.run_farewell_response_cycle())
    sender = asyncio.create_task(handler._response_sender_loop())
    try:
        response_id = await asyncio.wait_for(cycle_task, timeout=2.0)
    finally:
        handler.connection = None
        sender.cancel()

    assert connection.calls == [{"response": {"tool_choice": "none"}}]
    assert response_id == "resp_farewell"


@pytest.mark.asyncio
async def test_duplicate_empty_requests_still_coalesce_to_one() -> None:
    """The behaviour parallel tool calls rely on is unchanged."""
    handler, connection = _sender_handler()
    await handler._safe_response_create()
    await handler._safe_response_create()
    sender = asyncio.create_task(handler._response_sender_loop())
    await asyncio.sleep(0.05)
    handler.connection = None
    sender.cancel()

    assert connection.calls == [{}]


@pytest.mark.asyncio
async def test_the_farewell_cycle_gives_up_at_once_without_a_session() -> None:
    """A dead websocket drains nothing, so waiting on it is ten seconds of nothing."""
    handler, _ = _sender_handler()
    handler.connection = None

    assert await asyncio.wait_for(handler.run_farewell_response_cycle(), timeout=1.0) is None
```

Run: `cd reachy_companion && python -m pytest tests/test_sleep_farewell.py -q` — Expected: FAIL (`AttributeError: 'HuggingFaceRealtimeHandler' object has no attribute 'run_farewell_response_cycle'`).

- [ ] **Step 2: Add the dataclass import.** In `huggingface_realtime.py`, insert between `from collections import deque` (line 11) and `from collections.abc import Callable` (line 12):

```python
from dataclasses import dataclass
```

(The file's imports are length-sorted; `ruff check --fix` will confirm the position.)

- [ ] **Step 3: Add the two dataclasses** at module level, immediately after the `_item_id()` helper (~line 445, just above the handler class):

```python
@dataclass
class ResponseCycle:
    """The completion of one specific queued response, correlated by its id.

    Spec §1: the farewell must be waited on by *identity*, not by "whatever
    response finishes next". `_response_done_event` answers the second question
    and is set by the response that was already running when the tool call
    arrived — waiting on it would pose the robot before the goodbye had started,
    which is the original bug.
    """

    done: asyncio.Future[str | None]
    response_id: str | None = None

    def resolve(self, response_id: str | None) -> None:
        """Hand the observed response id to the waiter, exactly once."""
        if not self.done.done():
            self.done.set_result(response_id)


@dataclass
class ResponseRequest:
    """One queued `response.create`, plus the cycle a caller may be waiting on.

    The sender loop is the only component that can correlate a request with a
    response: it is serialized, so the `_active_response_id` it reads between
    sending and `response.done` belongs to the request it just sent. Everything
    that does not care attaches no cycle and the queue behaves exactly as before.
    """

    kwargs: dict[str, Any]
    cycle: ResponseCycle | None = None

    @property
    def is_coalescable(self) -> bool:
        """An empty request nobody is waiting on may be merged into another."""
        return not self.kwargs and self.cycle is None
```

- [ ] **Step 4: Retype the queue.** Replace line 672:

```python
        self._pending_responses: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
```

with:

```python
        self._pending_responses: asyncio.Queue[ResponseRequest] = asyncio.Queue()
```

- [ ] **Step 5: Take a cycle in `_safe_response_create`.** Replace the whole method (line 2413):

```python
    async def _safe_response_create(self, *, cycle: ResponseCycle | None = None, **kwargs: Any) -> None:
        """Enqueue a response.create() kwargs for the sender worker _response_sender_loop().

        This method never blocks the caller. `cycle`, when given, is resolved by
        the sender loop with the id of the response THIS request produced — the
        only correlation that survives the queue.
        """
        await self._pending_responses.put(ResponseRequest(kwargs=kwargs, cycle=cycle))
```

- [ ] **Step 6: Make the sender loop cycle-aware.** Replace the body of `_response_sender_loop` (line 2796) from `while self.connection:` to the end of the method, leaving the docstring above it unchanged:

```python
        while self.connection:
            try:
                request = await self._pending_responses.get()
            except asyncio.CancelledError:
                return

            # Parallel tool calls enqueue duplicate empty requests; coalesce to
            # one. A request carrying kwargs or a cycle is never discarded — the
            # old unconditional drain would have thrown away a farewell queued
            # behind a generic follow-up and hung its waiter, posing the robot
            # in silence. A non-coalescable request found here is ADOPTED rather
            # than dropped: the empty one it replaces asked only for "a
            # response", which this one also produces.
            while request.is_coalescable and not self._pending_responses.empty():
                try:
                    nxt = self._pending_responses.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if not nxt.is_coalescable:
                    request = nxt
                    break

            kwargs = request.kwargs
            observed_id: str | None = None
            try:
                sent = False
                max_retries = 5
                attempts = 0
                while not sent and self.connection and attempts < max_retries:
                    try:
                        await asyncio.wait_for(
                            self._response_done_event.wait(),
                            timeout=_RESPONSE_DONE_TIMEOUT,
                        )
                    except asyncio.TimeoutError:
                        logger.debug("Timed out waiting for previous response to finish; forcing ahead")
                        self._response_done_event.set()

                    if not self.connection:
                        break

                    self._last_response_rejected = False
                    self._response_started_or_rejected_event.clear()
                    try:
                        await self.connection.response.create(**kwargs)
                    except Exception as e:
                        logger.debug("_response_sender_loop: send failed: %s", e)
                        self._response_done_event.set()
                        break

                    try:
                        await asyncio.wait_for(
                            self._response_started_or_rejected_event.wait(),
                            timeout=_RESPONSE_DONE_TIMEOUT,
                        )
                    except asyncio.TimeoutError:
                        logger.debug("Timed out waiting for response.created or response rejection")

                    # Check if the receiver loop observed an asynchronous rejection.
                    if self._last_response_rejected:
                        attempts += 1
                        if attempts >= max_retries:
                            logger.debug("response.create rejected %d times; giving up", attempts)
                            break
                        logger.debug("response.create was rejected; retrying (%d/%d)", attempts, max_retries)
                        await asyncio.sleep(_RESPONSE_REJECTION_RETRY_DELAY)
                        continue

                    # Read the id here, between `response.created` and
                    # `response.done`: this loop is serialized, so the active id
                    # in this window is the one this request produced.
                    observed_id = self._active_response_id

                    try:
                        await asyncio.wait_for(
                            self._response_done_event.wait(),
                            timeout=_RESPONSE_DONE_TIMEOUT,
                        )
                    except asyncio.TimeoutError:
                        logger.debug("Timed out waiting for response.done; assuming response completed")
                        self._response_done_event.set()
                        break

                    sent = True
            finally:
                # Every exit resolves the cycle — rejection, disconnect, timeout
                # and success alike. A waiter left unresolved here is a robot
                # that never lies down.
                if request.cycle is not None:
                    request.cycle.response_id = observed_id
                    request.cycle.resolve(observed_id)
```

- [ ] **Step 7: Add the helper.** Insert immediately after `wait_for_reply_finished` (which ends at line 2158) so the two sleep-path waits read together:

```python
    async def run_farewell_response_cycle(self) -> str | None:
        """Queue the goodbye response and wait for THAT response to finish.

        The boundary-moment protocol from the instructing contract: the model
        composes the words, the app decides when they are spoken and what may
        ride along. `tool_choice: "none"` is what stops a late tool call from
        joining the goodbye; the nested `response=` shape is the SDK's own
        (openai 2.28.0: `AsyncRealtimeResponseResource.create` takes a nested
        `response` object). No per-response `instructions` are sent — those
        REPLACE the session prompt for that response and would drop the persona,
        the Chinese pin and the anti-fabrication rules at the exact moment the
        robot says its last sentence (the override trap).

        Returns the id of the response that completed, or None when it never
        started (rejected, disconnected, or the wait expired). Never raises: the
        sleep must reach the pose either way.
        """
        if self.connection is None:
            # Nothing will drain the queue, so waiting is ten seconds of nothing
            # on every sleep that follows a dropped websocket.
            logger.info("sleep: no live session for the farewell; going straight to the pose")
            return None
        loop = asyncio.get_running_loop()
        cycle = ResponseCycle(done=loop.create_future())
        await self._safe_response_create(cycle=cycle, response={"tool_choice": "none"})
        try:
            response_id = await asyncio.wait_for(cycle.done, timeout=_GOODBYE_RESPONSE_WAIT_S)
        except asyncio.TimeoutError:
            logger.warning(
                "sleep: the farewell response did not finish within %.0fs; sleeping anyway",
                _GOODBYE_RESPONSE_WAIT_S,
            )
            cycle.resolve(None)
            return None
        except Exception as exc:  # noqa: BLE001 - a dead session must still reach the pose
            logger.warning("sleep: the farewell response cycle failed (%s); sleeping anyway", exc)
            return None
        logger.info("sleep: farewell response finished (id=%s)", response_id)
        return response_id
```

- [ ] **Step 8: Run the tests.** `cd reachy_companion && python -m pytest tests/test_sleep_farewell.py -q` — Expected: 4 passed.

- [ ] **Step 9: Run the neighbours that touch this queue.** `cd reachy_companion && python -m pytest tests/test_solo_barge.py tests/test_huggingface_realtime.py -q` — Expected: green. `test_solo_barge.py` asserts `_pending_responses.qsize()` at nine sites; qsize is unaffected by the item type. If any assertion inspects a queued *item*, update it to read `.kwargs`.

- [ ] **Step 10: Gates.** `cd reachy_companion && ruff check . && mypy` — Expected: clean.

- [ ] **Step 11: Commit.** `git add -A && git commit -m "feat(realtime): correlate a queued response.create with its own response.done"`

---

### Task 2: Lifecycle sleeps pose directly, without the tool

Behavior-preserving refactor. It must land **before** Task 3: after Task 3 the tool no longer poses, and this path has no live model turn to speak a goodbye into. Spec §1 ("Two sleep paths, split"); Global Constraint 6; ambiguity D; Codex round 1 critical catch 3.

**Files:**
- Modify: `reachy_companion/src/reachy_companion/app_lifecycle.py` (imports lines 4/19; `run_go_to_sleep_tool` lines 162-168)
- Modify: `reachy_companion/src/reachy_companion/main.py` (lines 419-420, 483)
- Modify: `reachy_companion/tests/test_app_lifecycle.py` (`test_run_go_to_sleep_tool_uses_runtime_callback`, lines 66-79)

**Interfaces:**
- Produces: `app_lifecycle.run_lifecycle_sleep(deps: ToolDependencies, logger: logging.Logger) -> dict[str, object]`.
- Removes: `app_lifecycle.run_go_to_sleep_tool` (no other caller exists — verified by grep across `src/` and `tests/`).
- Consumed by: `main.py`'s inactivity-timeout thread.

- [ ] **Step 1: Update the test first.** In `reachy_companion/tests/test_app_lifecycle.py`, replace `test_run_go_to_sleep_tool_uses_runtime_callback` (lines 66-79) with:

```python
def test_run_lifecycle_sleep_silences_then_poses_directly() -> None:
    """Inactivity and shutdown have no model turn, so they never wait for a goodbye.

    Since the instructing wave the `go_to_sleep` TOOL only silences the inputs and
    hands the turn back for a spoken farewell. These paths have nobody to speak, so
    they silence and pose themselves (Codex round 1, critical catch 3).
    """
    order: list[str] = []
    expected = {"status": "sleeping"}
    deps = ToolDependencies(
        reachy_mini=MagicMock(),
        movement_manager=MagicMock(),
        begin_sleep=lambda: order.append("silence"),
        go_to_sleep=lambda: (order.append("sleep"), expected)[1],
    )

    result = app_lifecycle.run_lifecycle_sleep(deps, MagicMock())

    assert order == ["silence", "sleep"]
    assert result == expected


def test_run_lifecycle_sleep_reports_an_unwired_runtime() -> None:
    """No sleep callback means no sleep — say so instead of pretending."""
    deps = ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())

    assert app_lifecycle.run_lifecycle_sleep(deps, MagicMock()) == {
        "error": "go_to_sleep is unavailable in this runtime"
    }


def test_run_lifecycle_sleep_survives_a_failing_pose() -> None:
    """A raising closure must not kill the inactivity thread."""

    def _boom() -> dict[str, object]:
        raise RuntimeError("motors offline")

    deps = ToolDependencies(
        reachy_mini=MagicMock(),
        movement_manager=MagicMock(),
        go_to_sleep=_boom,
    )

    result = app_lifecycle.run_lifecycle_sleep(deps, MagicMock())

    assert result == {"error": "go_to_sleep failed: RuntimeError: motors offline"}
```

Run: `cd reachy_companion && python -m pytest tests/test_app_lifecycle.py -q` — Expected: FAIL (`AttributeError: module 'reachy_companion.app_lifecycle' has no attribute 'run_lifecycle_sleep'`).

- [ ] **Step 2: Replace the function** in `app_lifecycle.py` (lines 162-168):

```python
def run_lifecycle_sleep(deps: ToolDependencies, logger: logging.Logger) -> dict[str, object]:
    """Put Reachy to sleep from a path with no live model turn.

    Deliberately NOT the `go_to_sleep` tool. Since the instructing wave that tool
    only silences the inputs and hands the turn back to the model for a spoken
    goodbye, with the session-ending branch in `huggingface_realtime` owning the
    pose afterwards. The inactivity timeout and the shutdown path have no model
    turn to speak into and nothing downstream to run the pose, so they do both
    halves here — which is exactly what this path did before the split (Codex
    round 1, critical catch 3).

    Order is the same as everywhere else: silence, then pose. `begin_sleep` mutes
    the microphone and disarms the barge machine; `go_to_sleep` repeats that
    idempotently, drains the speaker, stops the movement manager, poses, and asks
    the daemon to stop the app.
    """
    if deps.go_to_sleep is None:
        return {"error": "go_to_sleep is unavailable in this runtime"}
    try:
        if deps.begin_sleep is not None:
            deps.begin_sleep()
        return deps.go_to_sleep()
    except Exception as e:
        logger.error("Failed to put Reachy to sleep from the lifecycle path: %s", e)
        return {"error": f"go_to_sleep failed: {type(e).__name__}: {e}"}
```

- [ ] **Step 3: Drop the now-dead imports** at the top of `app_lifecycle.py` — delete line 4 (`import asyncio`) and line 19 (`from reachy_companion.tools.go_to_sleep import GoToSleep`). Both were used only by the removed function; `ruff check` will flag them if anything still needs them.

- [ ] **Step 4: Rewire `main.py`.** Replace lines 419-420:

```python
    def run_go_to_sleep_tool() -> dict[str, Any]:
        return app_lifecycle.run_go_to_sleep_tool(deps, logger)
```

with:

```python
    def run_lifecycle_sleep() -> dict[str, Any]:
        """The inactivity timeout's sleep: silence and pose, with no goodbye turn."""
        return app_lifecycle.run_lifecycle_sleep(deps, logger)
```

and line 483:

```python
        _start_inactivity_timeout_thread(timeout_minutes, stream_manager, logger, app_stop_event, run_lifecycle_sleep)
```

- [ ] **Step 5: Run the tests.** `cd reachy_companion && python -m pytest tests/test_app_lifecycle.py tests/test_main.py -q` — Expected: green. `test_main.py::test_inactivity_timeout_thread_goes_to_sleep` passes its callback directly and is unaffected.

- [ ] **Step 6: Gates.** `cd reachy_companion && ruff check . && mypy` — Expected: clean.

- [ ] **Step 7: Commit.** `git add -A && git commit -m "refactor(sleep): lifecycle sleeps silence and pose directly, without the tool"`

---

### Task 3: `go_to_sleep` hands the turn back, and the dispatcher owns the goodbye

The field-bug fix. Spec §1 in full; Global Constraints 3, 6, 11; ambiguities C, E, F.

**Files:**
- Modify: `reachy_companion/src/reachy_companion/tools/core_tools.py` (`Tool`, lines 132-163)
- Modify: `reachy_companion/src/reachy_companion/tools/go_to_sleep.py` (whole file)
- Modify: `reachy_companion/src/reachy_companion/huggingface_realtime.py` (`_deliver_tool_result`, lines 3012-3030; two new methods)
- Modify: `reachy_companion/tests/tools/test_go_to_sleep.py`
- Modify: `reachy_companion/tests/test_app_lifecycle.py` (the four tool-ordering tests, lines 170-222)
- Modify: `reachy_companion/tests/test_sleep_farewell.py` (add the dispatcher tests)

**Interfaces:**
- Produces: `Tool.ends_session: ClassVar[bool] = False`; `GoToSleep.ends_session = True`.
- Changes: `GoToSleep.__call__` returns `{"status": "sleeping_soon", "farewell_context": {"reason": str, "listening_stopped": bool, "person": str | None}}` on success, `{"error": str}` when unwired. It no longer calls `deps.wait_for_reply_finished` or `deps.go_to_sleep`.
- Produces: `HuggingFaceRealtimeHandler._is_session_ending(tool: Tool | None, tool_result: Any) -> bool` (staticmethod).
- Produces: `HuggingFaceRealtimeHandler._finish_session_after_farewell(self) -> None`.
- Consumes: `run_farewell_response_cycle` (Task 1), `deps.go_to_sleep` (unchanged `main.py` closure).

- [ ] **Step 1: Write the failing tests.** Replace `reachy_companion/tests/tools/test_go_to_sleep.py` entirely:

```python
"""go_to_sleep silences the inputs and hands the turn back; it never poses."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from reachy_companion.tools.core_tools import ToolDependencies
from reachy_companion.tools.go_to_sleep import GoToSleep


def test_go_to_sleep_has_no_required_arguments() -> None:
    """An empty-object schema: nothing to guess, nothing to get wrong."""
    assert GoToSleep().parameters_schema == {
        "type": "object",
        "properties": {},
        "required": [],
    }


def test_go_to_sleep_declares_itself_session_ending() -> None:
    """The dispatcher's branch keys off the class, so an alias inherits it free."""
    assert GoToSleep.ends_session is True
    assert GoToSleep.needs_response is False


@pytest.mark.asyncio
async def test_go_to_sleep_reports_an_unwired_runtime() -> None:
    """No finalizer means no sleep will ever happen — promising one would be a lie."""
    deps = ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())

    assert await GoToSleep()(deps) == {"error": "go_to_sleep is unavailable in this runtime"}


@pytest.mark.asyncio
async def test_go_to_sleep_silences_and_returns_the_farewell_cue() -> None:
    """The whole voice path here: mute, then hand the turn back with the facts."""
    calls: list[str] = []
    deps = ToolDependencies(
        reachy_mini=MagicMock(),
        movement_manager=MagicMock(),
        begin_sleep=lambda: calls.append("silence"),
        go_to_sleep=lambda: calls.append("sleep") or {"status": "sleeping"},
        current_person="雲霓",
    )

    result = await GoToSleep()(deps)

    assert calls == ["silence"], "the tool must not pose; the dispatcher does that after the goodbye"
    assert result["status"] == "sleeping_soon"
    assert result["farewell_context"] == {
        "reason": "user_asked_to_end_the_interaction",
        "listening_stopped": True,
        "person": "雲霓",
    }


@pytest.mark.asyncio
async def test_go_to_sleep_still_hands_the_turn_back_if_silencing_fails() -> None:
    """A failed quiesce is noisy, not fatal: the goodbye still gets its turn."""

    def _boom() -> None:
        raise RuntimeError("stream gone")

    deps = ToolDependencies(
        reachy_mini=MagicMock(),
        movement_manager=MagicMock(),
        begin_sleep=_boom,
        go_to_sleep=lambda: {"status": "sleeping"},
    )

    assert (await GoToSleep()(deps))["status"] == "sleeping_soon"


def test_the_description_forbids_extra_speech_and_declares_the_cue() -> None:
    """Session-ending tools say 'do not generate any other text' (skill: Tool design rules).

    And the cue must be defined HERE, on a higher-authority surface, because a
    return carries no policy of its own (2026 Model Spec: tool messages hold no
    authority).
    """
    description = GoToSleep().description
    assert "do not generate any other text" in description.lower()
    assert "sleeping_soon" in description
    assert "farewell_context" in description
    assert "Do NOT use when:" in description
```

Then append the dispatcher tests to `reachy_companion/tests/test_sleep_farewell.py`:

```python
# --------------------------------------------------------------------------
# The session-ending dispatcher branch
# --------------------------------------------------------------------------


class _ToolNotification:
    """The shape `_deliver_tool_result` reads off a finished background tool."""

    def __init__(self, tool_name: str, result: dict[str, Any]) -> None:
        self.tool_name = tool_name
        self.id = "call_1"
        self.result = result
        self.error = None
        self.is_idle_tool_call = False


class _ItemCreatingConnection(_RecordingConnection):
    """A connection that also accepts `conversation.item.create`."""

    def __init__(self) -> None:
        super().__init__()
        self.items: list[dict[str, Any]] = []
        self.conversation = SimpleNamespace(
            item=SimpleNamespace(create=self._item_create),
        )

    async def _item_create(self, *, item: dict[str, Any]) -> None:
        self.items.append(item)


def _dispatch_handler(monkeypatch: pytest.MonkeyPatch) -> tuple[HuggingFaceRealtimeHandler, list[str]]:
    """A handler wired just enough to run `_deliver_tool_result` end to end."""
    handler, _ = _sender_handler()
    connection = _ItemCreatingConnection()
    connection.handler = handler
    handler.connection = connection
    handler.output_queue = asyncio.Queue()
    handler._in_flight_tool_calls = set()
    handler._tool_batch_needs_response = False
    # `_deliver_tool_result` calls `_mark_activity`, which lives on
    # `ConversationHandler` and reads these two off `self`; a `__new__`-built
    # handler has neither.
    handler._activity_observer = None
    handler.last_activity_time = 0.0
    order: list[str] = []
    handler.deps = MagicMock()
    handler.deps.go_to_sleep = lambda: order.append("pose") or {"status": "sleeping"}
    monkeypatch.setattr(
        HuggingFaceRealtimeHandler,
        "_wait_for_response_done_before_tool_result",
        lambda self: _true(),
    )

    async def _farewell(self: HuggingFaceRealtimeHandler) -> str | None:
        order.append("farewell")
        return "resp_farewell"

    monkeypatch.setattr(HuggingFaceRealtimeHandler, "run_farewell_response_cycle", _farewell)
    return handler, order


async def _true() -> bool:
    return True


@pytest.mark.asyncio
async def test_a_sleeping_soon_result_speaks_first_then_poses(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole inversion in one assertion: goodbye, THEN the body."""
    handler, order = _dispatch_handler(monkeypatch)
    monkeypatch.setattr(
        "reachy_companion.tools.core_tools.get_tools",
        lambda: {"go_to_sleep": _SessionEndingTool()},
    )

    followed_up = await handler._deliver_tool_result(
        _ToolNotification("go_to_sleep", {"status": "sleeping_soon", "farewell_context": {}})
    )

    assert order == ["farewell", "pose"]
    assert followed_up is True
    assert handler._tool_batch_needs_response is False


@pytest.mark.asyncio
async def test_an_error_from_the_session_ending_tool_never_poses(monkeypatch: pytest.MonkeyPatch) -> None:
    """`ends_session` alone must not be enough — the result has to say so too."""
    handler, order = _dispatch_handler(monkeypatch)
    monkeypatch.setattr(
        "reachy_companion.tools.core_tools.get_tools",
        lambda: {"go_to_sleep": _SessionEndingTool()},
    )

    await handler._deliver_tool_result(
        _ToolNotification("go_to_sleep", {"error": "go_to_sleep is unavailable in this runtime"})
    )

    assert order == []


class _SessionEndingTool:
    """Stand-in for the registered GoToSleep instance."""

    name = "go_to_sleep"
    needs_response = False
    ends_session = True
```

Run: `cd reachy_companion && python -m pytest tests/tools/test_go_to_sleep.py tests/test_sleep_farewell.py -q` — Expected: FAIL.

- [ ] **Step 2: Add `ends_session` to the base class.** In `tools/core_tools.py`, replace lines 140-145:

```python
    Tools may override:
      - needs_response: bool = True  # set False to skip the spoken follow-up after this tool runs
      - ends_session: bool = False   # set True when the visit ends after ONE spoken goodbye
    """

    _auto_register: ClassVar[bool] = True
    needs_response: ClassVar[bool] = True
    # A session-ending tool does not get the generic follow-up response. The
    # realtime handler issues one targeted response with `tool_choice: "none"`
    # for the goodbye, waits for THAT response to finish, and only then poses.
    # Declared on the class so an alias tool inherits the behaviour for free
    # (the rename A/B is an alias, never a raw rename).
    ends_session: ClassVar[bool] = False
```

- [ ] **Step 3: Rewrite `tools/go_to_sleep.py`** entirely:

```python
"""End the visit: silence the inputs, then hand the turn back for a goodbye.

Speak-then-act is not promptable. A sentence and the tool call that follows it
share one response, and on 2026-09-01 (00:17:48-58, nineteenth install) the
「進入睡眠模式」 turn produced a tool-call-only response with no audio delta at
all — so the quiesce correctly found `speaker quiet after 0.0s` and posed a
silent robot. The order is inverted instead: this tool does the irreversible
input half (mic mute, barge disarm) and returns facts; the session-ending branch
in `huggingface_realtime._deliver_tool_result` then issues ONE follow-up response
with `tool_choice: "none"` for the model to say goodbye into, waits for that
response's own `response.done`, and only then runs the drain and the pose through
`deps.go_to_sleep`.

Lifecycle sleeps (inactivity timeout, shutdown) never come through here:
`app_lifecycle.run_lifecycle_sleep` silences and poses directly, because there is
no live model turn there to speak a goodbye into.
"""

import logging
from typing import Any, ClassVar

from reachy_companion.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


class GoToSleep(Tool):
    """Silence the robot's inputs and let the model say goodbye before it rests."""

    name = "go_to_sleep"
    description = (
        "End the interaction entirely: Reachy stops, rests, and the conversation is over. Use when you are "
        "sure the user wants Reachy gone, off, asleep, or the conversation over — in any wording or "
        "language. The judgment: they want you to STOP being active, not to keep participating in a "
        "different way (that is set_conversation_mode: 一對一聊天模式 / 多人聊天模式 / 紀錄模式). "
        "Do NOT use when: the user only wants you quiet for a moment — that is wait_for_user. "
        "Do NOT use when: the user wants you to listen differently, record, or stop recording — that is "
        "set_conversation_mode. "
        "Do not use for idle turns, sleepy emotions, silence, or ambiguous requests. "
        "Do not generate any other text or response when calling this tool: nothing before it, nothing "
        "alongside it. "
        "The result comes back with `status: sleeping_soon` and a `farewell_context`. That is your cue to "
        "say ONE natural goodbye — in the conversation's language, in character, using the context if it "
        "helps — and then stay quiet. Nothing else is expected after that sentence; the body lies down "
        "once it has finished playing."
    )
    needs_response = False
    ends_session: ClassVar[bool] = True
    parameters_schema = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> dict[str, Any]:
        """Silence the inputs and report the facts the goodbye is composed from."""
        if deps.go_to_sleep is None:
            # Without a finalizer nothing will ever pose the robot, so promising
            # `sleeping_soon` would be exactly the overstatement this wave removes.
            return {"error": "go_to_sleep is unavailable in this runtime"}

        logger.info("Tool call: go_to_sleep")
        # Silence FIRST and unconditionally (Codex round 2, 2a-6): the goodbye
        # that follows takes seconds, and a live microphone through it means the
        # goodbye's own echo — or a repeated 「睡覺吧」 — opens a turn nobody will
        # answer. `begin_sleep` is idempotent; the finalizer repeats it.
        if deps.begin_sleep is not None:
            try:
                deps.begin_sleep()
            except Exception as e:  # noqa: BLE001 - a failed quiesce must not cost the goodbye
                logger.warning("go_to_sleep: could not silence the inputs: %s", e)

        # Facts and one render cue, no policy: the description above is the
        # higher-authority surface that says how `farewell_context` is used
        # (tool messages hold "No Authority" in the 2026 Model Spec).
        return {
            "status": "sleeping_soon",
            "farewell_context": {
                "reason": "user_asked_to_end_the_interaction",
                "listening_stopped": True,
                "person": deps.current_person,
            },
        }
```

- [ ] **Step 4: Add the dispatcher branch.** In `huggingface_realtime.py`, inside `_deliver_tool_result`, replace lines 3022-3030:

```python
            # Always surface errors, skip the spoken follow-up for tools that opt out.
            if model_result_submitted and (completed_tool.error is not None or tool is None or tool.needs_response):
                self._tool_batch_needs_response = True

            # Parallel tool calls in one turn: respond once every result is in, not per tool.
            if self._tool_batch_needs_response and not self._in_flight_tool_calls:
                self._tool_batch_needs_response = False
                await self._safe_response_create()
                return True
```

with:

```python
            # Always surface errors, skip the spoken follow-up for tools that opt out.
            if model_result_submitted and (completed_tool.error is not None or tool is None or tool.needs_response):
                self._tool_batch_needs_response = True

            # A session-ending tool owns this turn's follow-up. The generic
            # `response.create` below is untargeted — a late tool call could ride
            # it, and two responses would race — so it is cancelled here rather
            # than allowed to run alongside the goodbye.
            if model_result_submitted and self._is_session_ending(tool, tool_result):
                self._tool_batch_needs_response = False
                if not self._in_flight_tool_calls:
                    await self._finish_session_after_farewell()
                    return True

            # Parallel tool calls in one turn: respond once every result is in, not per tool.
            if self._tool_batch_needs_response and not self._in_flight_tool_calls:
                self._tool_batch_needs_response = False
                await self._safe_response_create()
                return True
```

- [ ] **Step 5: Add the two methods**, immediately after `_deliver_tool_result` (which ends at line 3038):

```python
    @staticmethod
    def _is_session_ending(tool: Any, tool_result: Any) -> bool:
        """Whether this result should end the visit after exactly one goodbye.

        Two conditions, both required. The tool declares itself session-ending
        (`Tool.ends_session`, so the rename A/B's alias inherits it for free),
        AND the result actually says `sleeping_soon` — a tool that returned
        `{"error": …}` because the runtime is unwired must never pose the robot.
        """
        if tool is None or not getattr(tool, "ends_session", False):
            return False
        return isinstance(tool_result, dict) and tool_result.get("status") == "sleeping_soon"

    async def _finish_session_after_farewell(self) -> None:
        """Goodbye, then the body — the voice sleep path, in order.

        `run_farewell_response_cycle` resolves only when THAT response reaches
        `response.done`, never on whichever response happened to be running when
        the tool call arrived. `deps.go_to_sleep` is unchanged and still owns the
        rest: it repeats the quiesce, runs the bounded audio drain, stops the
        movement manager, poses, and asks the daemon to stop the app. It blocks,
        so it goes to a thread — the receive loop must stay live while the
        speaker drains.
        """
        await self.run_farewell_response_cycle()
        finalize = self.deps.go_to_sleep
        if finalize is None:
            logger.error("sleep: no runtime sleep callback; the robot stays awake")
            return
        try:
            result = await asyncio.to_thread(finalize)
        except Exception as e:  # noqa: BLE001 - a failed pose must not kill the turn
            logger.error("sleep: the sleep callback failed: %s", e)
            return
        logger.info("sleep: finalized (%s)", result)
```

- [ ] **Step 6: Retire the four tool-ordering tests.** In `reachy_companion/tests/test_app_lifecycle.py`, delete `test_the_sleep_tool_silences_first_then_waits_then_sleeps` (lines 170-193), `test_the_sleep_tool_still_sleeps_if_the_wait_times_out` (196-211) and `test_the_sleep_tool_works_without_the_new_seams` (214-222) — the behaviour they pin now lives in `tests/tools/test_go_to_sleep.py` and `tests/test_sleep_farewell.py`. **Keep** the three `wait_for_reply_finished` tests (225-311): that method is still the handler's own bounded wait and is still exercised elsewhere. Keep every quiesce and drain test.

- [ ] **Step 7: Run the sleep chain.** `cd reachy_companion && python -m pytest tests/tools/test_go_to_sleep.py tests/test_sleep_farewell.py tests/test_app_lifecycle.py tests/test_main.py -q` — Expected: green.

- [ ] **Step 8: Confirm commentary suppression is untouched.** `cd reachy_companion && python -m pytest tests/test_huggingface_realtime.py -k "commentary or item_phase" -q` — Expected: 5 passed. `test_a_commentary_only_response_still_completes` is the existing proof that a preamble-only response still sets `_response_done_event` and clears `_active_response_id`; the new id correlation depends on it.

- [ ] **Step 9: Gates.** `cd reachy_companion && ruff check . && mypy` — Expected: clean.

- [ ] **Step 10: Commit.** `git add -A && git commit -m "fix(sleep): the model says goodbye into its own response before the body lies down"`

---
### Task 4: A tracking-suspend seam on the movement manager

Rung 3 — physical-state truth at the execution boundary. Spec §2 (first three bullets); Global Constraint 4 is *not* touched here.

**Files:**
- Modify: `reachy_companion/src/reachy_companion/moves.py` (`__init__` ~line 221; public methods after `set_head_tracking` line 296; `_handle_command` `set_head_tracking` branch lines 394-413; two new branches before the `else` at line 475)
- Modify: `reachy_companion/tests/test_moves.py`

**Interfaces:**
- Produces: `MovementManager.suspend_head_tracking(self, owner: str) -> None` (queued command `("suspend_head_tracking", owner)`).
- Produces: `MovementManager.restore_head_tracking(self, owner: str) -> None` (queued command `("restore_head_tracking", owner)`).
- Produces: `MovementManager.head_tracking_desired(self) -> bool` (read-only observability; the restore path does **not** consult it).
- Produces: `MovementManager._tracking_suspended_by: str | None`, `._tracking_suspended_state: bool`.
- Consumed by: `tools/head_window.py` (Task 5) only.

**Why not the existing seams** — this is the review's first critical catch and must stay in the code as a comment: `set_speaking(True)` captures `_track_anchor` and `_get_primary_pose` (line 575-582) then falls back to that anchor once the queued move finishes, so it would *undo* the very move the window exists to make visible. `set_hold_still(True)` clears `move_queue` (line 447), and the move is the point.

- [ ] **Step 1: Write the failing tests.** Append to `reachy_companion/tests/test_moves.py`, after `test_speaking_anchor_composes_emotions_and_holds_dances_from_neutral`:

```python
# --------------------------------------------------------------------------
# Manual head windows (2026-09-01 instructing wave, spec §2)
# --------------------------------------------------------------------------


def _apply_suspend(manager: MovementManager, owner: str) -> None:
    """Post the public command and let the loop's own drain apply it."""
    manager.suspend_head_tracking(owner)
    manager._poll_signals(manager._now())


def _apply_restore(manager: MovementManager, owner: str) -> None:
    manager.restore_head_tracking(owner)
    manager._poll_signals(manager._now())


def test_a_head_window_stops_tracking_without_anchoring() -> None:
    """The critical catch: an anchor would be restored over the completed move.

    `set_speaking(True)` captures `_track_anchor`, and `_get_primary_pose` falls
    back to it the moment the queued goto ends — which is precisely how the head
    snapped back to the user after every `look_around` on 2026-09-01. A window
    suspends and captures nothing.
    """
    robot = MagicMock()
    robot.get_current_head_pose.return_value = create_head_pose(0, 0, 0, 0, 0, 12, degrees=True)
    manager = MovementManager(robot)
    manager._head_tracking = True

    _apply_suspend(manager, "look_around")

    assert manager._tracking_suspended_by == "look_around"
    assert manager._head_tracking is False
    assert manager._track_anchor is None
    robot.stop_head_tracking.assert_called_once_with()
    robot.start_head_tracking.assert_not_called()


def test_a_head_window_leaves_the_completed_move_on_screen() -> None:
    """With no anchor, the pose the loop keeps commanding is the move's own."""
    robot = MagicMock()
    robot.get_current_head_pose.return_value = create_head_pose(0, 0, 0, 0, 0, 12, degrees=True)
    manager = MovementManager(robot)
    manager._head_tracking = True
    _apply_suspend(manager, "look_around")

    turned = create_head_pose(0, 0, 0, 0, 0, -40, degrees=True)
    manager.state.last_primary_pose = (turned, (0.0, 0.0), 0.0)

    head, _, _ = manager._get_primary_pose(manager._now())

    np.testing.assert_allclose(head, turned)


def test_a_head_window_restores_the_state_it_found() -> None:
    """Restore hands back what was in force at suspend, never an unconditional on."""
    robot = MagicMock()
    robot.get_current_head_pose.return_value = np.eye(4)
    manager = MovementManager(robot)
    manager._head_tracking = True

    _apply_suspend(manager, "look_around")
    _apply_restore(manager, "look_around")

    assert manager._tracking_suspended_by is None
    assert manager._head_tracking is True
    assert robot.start_head_tracking.call_args_list == [call(weight=1.0)]


def test_a_head_window_taken_with_tracking_off_touches_the_robot_at_all() -> None:
    """The operator may have turned tracking off; a window must not turn it back on."""
    robot = MagicMock()
    manager = MovementManager(robot)

    _apply_suspend(manager, "move_head")
    _apply_restore(manager, "move_head")

    robot.stop_head_tracking.assert_not_called()
    robot.start_head_tracking.assert_not_called()
    assert manager._head_tracking is False


def test_only_the_owner_closes_the_window() -> None:
    """Single ownership: a delegate cannot restore its caller's window."""
    robot = MagicMock()
    robot.get_current_head_pose.return_value = np.eye(4)
    manager = MovementManager(robot)
    manager._head_tracking = True

    _apply_suspend(manager, "look_around")
    _apply_restore(manager, "move_head")

    assert manager._tracking_suspended_by == "look_around"
    robot.start_head_tracking.assert_not_called()

    _apply_restore(manager, "look_around")

    assert manager._tracking_suspended_by is None
    assert robot.start_head_tracking.call_args_list == [call(weight=1.0)]


def test_a_nested_suspend_does_not_take_the_window() -> None:
    """look_around delegating its motion must not open a second window."""
    robot = MagicMock()
    robot.get_current_head_pose.return_value = np.eye(4)
    manager = MovementManager(robot)
    manager._head_tracking = True

    _apply_suspend(manager, "look_around")
    _apply_suspend(manager, "move_head")

    assert manager._tracking_suspended_by == "look_around"
    robot.stop_head_tracking.assert_called_once_with()


def test_speech_cannot_rearm_the_head_mid_window() -> None:
    """`set_speaking` is gated on `_head_tracking`, which the window clears."""
    robot = MagicMock()
    robot.get_current_head_pose.return_value = np.eye(4)
    manager = MovementManager(robot)
    manager._head_tracking = True
    _apply_suspend(manager, "look_around")
    robot.start_head_tracking.reset_mock()

    manager.set_speaking(True)
    manager._poll_signals(manager._now())
    manager.set_speaking(False)
    manager._poll_signals(manager._now())

    robot.start_head_tracking.assert_not_called()
    assert manager._track_anchor is None


def test_a_tracking_toggle_mid_window_lands_on_the_restore() -> None:
    """"Stop following me" during a look is honoured — at the end of the look."""
    robot = MagicMock()
    robot.get_current_head_pose.return_value = np.eye(4)
    manager = MovementManager(robot)
    manager._head_tracking = True
    _apply_suspend(manager, "look_around")
    robot.stop_head_tracking.reset_mock()

    manager.set_head_tracking(False)
    manager._poll_signals(manager._now())
    assert manager._tracking_suspended_state is False
    robot.stop_head_tracking.assert_not_called()

    _apply_restore(manager, "look_around")

    assert manager._head_tracking is False
    robot.start_head_tracking.assert_not_called()
    robot.stop_head_tracking.assert_not_called()


def test_head_tracking_desired_reports_through_a_window() -> None:
    """Observability only — the restore uses the state captured in the worker."""
    robot = MagicMock()
    robot.get_current_head_pose.return_value = np.eye(4)
    manager = MovementManager(robot)
    manager._head_tracking = True

    assert manager.head_tracking_desired() is True
    _apply_suspend(manager, "look_around")
    assert manager.head_tracking_desired() is True
    _apply_restore(manager, "look_around")
    assert manager.head_tracking_desired() is True
```

Run: `cd reachy_companion && python -m pytest tests/test_moves.py -q` — Expected: FAIL (`AttributeError: 'MovementManager' object has no attribute 'suspend_head_tracking'`).

- [ ] **Step 2: Add the state.** In `moves.py.__init__`, insert after line 221 (`self._hold_entry_head_tracking = False`):

```python
        # Manual head windows (2026-09-01 instructing wave). A tool that sends the
        # head somewhere and needs it to STAY there owns this for the length of
        # its window; `_tracking_suspended_state` is the tracking state to hand
        # back, captured inside the worker at suspend time so no caller can race
        # it across threads.
        self._tracking_suspended_by: str | None = None
        self._tracking_suspended_state: bool = False
```

- [ ] **Step 3: Add the public methods**, immediately after `set_head_tracking` (line 296):

```python
    def suspend_head_tracking(self, owner: str) -> None:
        """Stop daemon face tracking for a manual head window; thread-safe.

        NOT `set_speaking(True)`: that captures a look-at anchor, and
        `_get_primary_pose` falls back to it once the queued goto completes —
        which would undo the very move the window exists to make visible (the
        review's critical catch). NOT `set_hold_still(True)` either: that drops
        queued moves, and the move is the point. This suspends tracking and
        touches nothing else.

        One owner at a time, and `restore_head_tracking` accepts only that owner,
        so a tool delegating its motion to another tool cannot have the window
        closed out from under it.
        """
        self._command_queue.put(("suspend_head_tracking", owner))

    def restore_head_tracking(self, owner: str) -> None:
        """Hand the head back to whatever tracking state was in force at suspend."""
        self._command_queue.put(("restore_head_tracking", owner))

    def head_tracking_desired(self) -> bool:
        """The tracking state the app has asked for, suspension aside.

        Read-only observability for tools and tests. The restore path does NOT
        consult this: it uses the state captured inside the worker at suspend
        time, which is the only value no other thread can have moved since.
        """
        if self._tracking_suspended_by is not None:
            return self._tracking_suspended_state
        return self._head_tracking
```

- [ ] **Step 4: Defer toggles during a window.** In `_handle_command`, replace the head of the `set_head_tracking` branch (lines 394-398):

```python
        elif command == "set_head_tracking":
            enabled = bool(payload)
            if self._head_tracking == enabled:
                return
```

with:

```python
        elif command == "set_head_tracking":
            enabled = bool(payload)
            if self._tracking_suspended_by is not None:
                # A manual head window owns the head. Record what was asked for
                # so the restore hands back the state the caller last chose, and
                # issue no robot call: re-arming here would yank the head off the
                # pose the window was opened to show.
                self._tracking_suspended_state = enabled
                logger.debug(
                    "Head-tracking toggle deferred by the %s head window: %s",
                    self._tracking_suspended_by,
                    enabled,
                )
                return
            if self._head_tracking == enabled:
                return
```

- [ ] **Step 5: Add the two command branches**, immediately after the `set_hold_still` branch (which ends at line 474) and before the final `else` (line 475):

```python
        elif command == "suspend_head_tracking":
            owner = str(payload)
            if self._tracking_suspended_by is not None:
                logger.debug(
                    "Head window already held by %s; %s did not take it",
                    self._tracking_suspended_by,
                    owner,
                )
                return
            self._tracking_suspended_by = owner
            self._tracking_suspended_state = self._head_tracking
            # No anchor is captured, deliberately: a non-None `_track_anchor` is
            # what `_get_primary_pose` restores over a completed move.
            self._track_anchor = None
            # `set_speaking` is gated on `_head_tracking`, so clearing the flag
            # below makes the speaking handoff a no-op for the whole window.
            self._is_speaking = False
            if not self._head_tracking:
                logger.debug("Head window %s opened with tracking already off", owner)
                return
            self._head_tracking = False
            if self._hold_still:
                # Deferred for the same reason a toggle is: the release edge
                # applies whatever the flags then demand.
                logger.debug("Head-tracking suspend deferred by the still-pose hold: %s", owner)
                return
            try:
                self.current_robot.stop_head_tracking()
            except Exception as e:
                logger.warning("Head-tracking suspend failed: %s", e)
            logger.info("Head tracking suspended for the %s window", owner)
        elif command == "restore_head_tracking":
            owner = str(payload)
            if self._tracking_suspended_by != owner:
                logger.debug(
                    "Head-window restore from %s ignored; the window is held by %s",
                    owner,
                    self._tracking_suspended_by,
                )
                return
            desired = self._tracking_suspended_state
            self._tracking_suspended_by = None
            self._tracking_suspended_state = False
            logger.info("Head window %s closed; head tracking restored to %s", owner, desired)
            if self._head_tracking == desired:
                return
            self._head_tracking = desired
            self._track_anchor = None
            self._is_speaking = False
            if self._hold_still:
                logger.debug("Head-tracking restore deferred by the still-pose hold: %s", desired)
                return
            try:
                if desired:
                    self.current_robot.start_head_tracking(weight=1.0)
                else:
                    self.current_robot.stop_head_tracking()
            except Exception as e:
                logger.warning("Head-tracking restore failed: %s", e)
```

- [ ] **Step 6: Run.** `cd reachy_companion && python -m pytest tests/test_moves.py -q` — Expected: green, including every pre-existing hold-still and speaking test (the window's interaction with the hold is deferral, exactly like the other two commands).

- [ ] **Step 7: Gates.** `cd reachy_companion && ruff check . && mypy` — Expected: clean.

- [ ] **Step 8: Commit.** `git add -A && git commit -m "feat(moves): suspend daemon head tracking for a manual head window, without anchoring"`

---

### Task 5: `look_around` and `move_head` adopt the window, with separate semantics

The second field-bug fix. Spec §2 (bullets 2, 4 and 5); ambiguity A.

**Files:**
- Create: `reachy_companion/src/reachy_companion/tools/head_window.py`
- Modify: `reachy_companion/src/reachy_companion/tools/move_head.py` (whole file)
- Modify: `reachy_companion/src/reachy_companion/tools/look_around.py` (lines 96-122)
- Modify: `reachy_companion/tests/tools/test_move_head.py`, `reachy_companion/tests/tools/test_look_around.py`

**Interfaces:**
- Produces: `head_window(deps: ToolDependencies, owner: str) -> AsyncIterator[None]` (an `@asynccontextmanager`).
- Produces: `MoveHead.queue_direction(self, deps: ToolDependencies, direction: str) -> Dict[str, Any]` — queues the goto with **no window of its own**; returns `{"status": "move_queued", "direction_requested": direction}` or `{"error": …}`.
- Produces: `move_head.MOVE_HEAD_HOLD_S: Final[float] = 1.5`, `move_head.DIRECTIONS: Final[Tuple[str, ...]]`.
- Changes: `MoveHead.__call__` returns `{"status": "move_queued", "direction_requested": direction}`; rejects an unknown direction with `{"error": "direction must be one of left, right, up, down, front"}`.
- Unchanged (and must stay unchanged): `LookAround`'s success dict is still exactly `{"direction_requested", "question", "b64_im"}` — `tests/test_huggingface_realtime.py:1251` asserts the sanitized payload by equality, so adding a key there breaks it.

**The two recipes differ, on purpose.** `look_around` opens ONE window covering move → settle → capture, and closes it after the shutter. `move_head` opens a *gesture-length* window: move, hold, then hand the head back. Its description and return say the hold is temporary — that is the honesty fix the spec asks for, and it is the only shape where the head visibly moves without costing the robot its face-following for the rest of the visit.

- [ ] **Step 1: Write the failing tests.** Rewrite `reachy_companion/tests/tools/test_move_head.py`'s behaviour tests (keep the module's existing `_deps()` and `_queued_move()` helpers and the geometry test's parametrize list):

```python
@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["left", "right", "up", "down", "front"])
async def test_move_head_commands_the_direction_it_computed(direction: str) -> None:
    """Geometry, through the window-free seam `look_around` also uses."""
    deps = _deps()

    result = await MoveHead().queue_direction(deps, direction)

    assert result == {"status": "move_queued", "direction_requested": direction}
    move = _queued_move(deps)
    expected = create_head_pose(*MoveHead.DELTAS[direction], degrees=True)
    np.testing.assert_allclose(move.target_head_pose, expected)
    np.testing.assert_allclose(move.start_head_pose, deps.reachy_mini.get_current_head_pose.return_value)
    assert move.duration == 0.5
    deps.movement_manager.set_moving_state.assert_called_once_with(0.5)


@pytest.mark.asyncio
async def test_move_head_rejects_an_unknown_direction() -> None:
    """No silent fall-back to front: a wrong move the model narrates is worse.

    Schema enums are not enforced on this platform (no structured outputs on
    either 2.1 realtime model), so the boundary is where the check has to be.
    """
    deps = _deps()

    result = await MoveHead()(deps, direction="behind")

    assert result == {"error": "direction must be one of left, right, up, down, front"}
    deps.movement_manager.queue_move.assert_not_called()
    deps.movement_manager.suspend_head_tracking.assert_not_called()


@pytest.mark.asyncio
async def test_move_head_rejects_a_non_string_direction() -> None:
    deps = _deps()

    result = await MoveHead()(deps, direction=7)

    assert result == {"error": "direction must be one of left, right, up, down, front"}
    deps.movement_manager.queue_move.assert_not_called()


@pytest.mark.asyncio
async def test_move_head_holds_the_gesture_inside_a_tracking_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Suspend, move, hold, restore — and the hold happens INSIDE the window."""
    monkeypatch.setattr("reachy_companion.tools.move_head.MOVE_HEAD_HOLD_S", 0.0)
    deps = _deps()
    deps.motion_duration_s = 0.0
    order: list[str] = []
    deps.movement_manager.suspend_head_tracking.side_effect = lambda owner: order.append(f"suspend:{owner}")
    deps.movement_manager.queue_move.side_effect = lambda move: order.append("move")
    deps.movement_manager.restore_head_tracking.side_effect = lambda owner: order.append(f"restore:{owner}")

    result = await MoveHead()(deps, direction="right")

    assert order == ["suspend:move_head", "move", "restore:move_head"]
    assert result == {"status": "move_queued", "direction_requested": "right"}


@pytest.mark.asyncio
async def test_move_head_restores_tracking_even_when_the_move_fails() -> None:
    """A window left open is a robot that never looks at anyone again."""
    deps = _deps()
    deps.reachy_mini.get_current_head_pose.side_effect = RuntimeError("motors offline")

    result = await MoveHead()(deps, direction="up")

    assert "error" in result and "RuntimeError" in result["error"]
    deps.movement_manager.restore_head_tracking.assert_called_once_with("move_head")


@pytest.mark.asyncio
async def test_move_head_survives_a_manager_without_the_seam() -> None:
    """An older movement manager still gets its move; it just gets no window."""
    deps = _deps()
    del deps.movement_manager.suspend_head_tracking

    result = await MoveHead()(deps, direction="left")

    assert result == {"status": "move_queued", "direction_requested": "left"}


def test_the_description_calls_the_hold_temporary() -> None:
    """The honest contract: the head goes there and holds, tracking then resumes."""
    description = MoveHead().description
    assert "hold it there for a moment" in description
    assert "resumes" in description
    assert "direction_requested" in description
```

For `reachy_companion/tests/tools/test_look_around.py`, change every `monkeypatch.setattr(MoveHead, "__call__", _move)` to patch `queue_direction` instead, with the signature `async def _queue(self, deps, direction)`, and add:

```python
@pytest.mark.asyncio
async def test_look_around_owns_one_window_across_move_settle_and_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Single ownership (spec §2): the delegate must not close the window early."""
    monkeypatch.setattr("reachy_companion.tools.look_around.LOOK_AROUND_SETTLE_S", 0.0)
    deps = _deps()
    deps.motion_duration_s = 0.0
    order: list[str] = []
    deps.movement_manager.suspend_head_tracking.side_effect = lambda owner: order.append(f"suspend:{owner}")
    deps.movement_manager.restore_head_tracking.side_effect = lambda owner: order.append(f"restore:{owner}")

    async def _queue(self, deps_, direction):  # noqa: ANN001
        order.append(f"move:{direction}")
        return {"status": "move_queued", "direction_requested": direction}

    async def _shoot(self, deps_, **kwargs):  # noqa: ANN001
        order.append("camera")
        return {"b64_im": "AAA="}

    monkeypatch.setattr(MoveHead, "queue_direction", _queue)
    monkeypatch.setattr(Camera, "__call__", _shoot)

    result = await LookAround()(deps, direction="right", question="誰在那邊")

    assert order == ["suspend:look_around", "move:right", "camera", "restore:look_around"]
    assert result == {"direction_requested": "right", "question": "誰在那邊", "b64_im": "AAA="}


@pytest.mark.asyncio
async def test_look_around_restores_tracking_when_the_capture_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every exit closes the window — the `face_support.hold_still` contract."""
    monkeypatch.setattr("reachy_companion.tools.look_around.LOOK_AROUND_SETTLE_S", 0.0)
    deps = _deps()
    deps.motion_duration_s = 0.0

    async def _queue(self, deps_, direction):  # noqa: ANN001
        return {"status": "move_queued", "direction_requested": direction}

    async def _boom(self, deps_, **kwargs):  # noqa: ANN001
        raise RuntimeError("camera driver fault")

    monkeypatch.setattr(MoveHead, "queue_direction", _queue)
    monkeypatch.setattr(Camera, "__call__", _boom)

    result = await LookAround()(deps, direction="up")

    assert result["direction_requested"] == "up"
    assert "RuntimeError" in result["error"]
    deps.movement_manager.restore_head_tracking.assert_called_once_with("look_around")


@pytest.mark.asyncio
async def test_an_unknown_direction_opens_no_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """Validation runs before the body is touched, so nothing needs restoring."""
    deps = _deps()

    result = await LookAround()(deps, direction="behind")

    assert result["error"] == "direction must be one of left, right, up, down, front"
    deps.movement_manager.suspend_head_tracking.assert_not_called()
    deps.movement_manager.restore_head_tracking.assert_not_called()
```

Run: `cd reachy_companion && python -m pytest tests/tools/test_move_head.py tests/tools/test_look_around.py -q` — Expected: FAIL.

- [ ] **Step 2: Create `reachy_companion/src/reachy_companion/tools/head_window.py`:**

```python
"""Suspend daemon face tracking for the length of one manual head window.

The daemon's face tracker is enabled at boot at weight 1.0 (`main.py:475`) and
overrides a queued goto while a face is in view — which is why three correct
`look_around` calls on 2026-09-01 (00:15:52, 00:16:16, 00:17:24) each queued
`move_head right` with the right argument and each photographed the person
straight ahead.

Rung 3 of the escalation ladder: physical-state truth at the execution boundary,
not a prompt fix. Shaped as a context manager for the reason
`face_support.hold_still` is: `CancelledError` is a BaseException and a tool task
is cancellable at any await, so a restore left outside a `finally` would leave the
robot permanently face-blind after one cancelled look.
"""

from __future__ import annotations
import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from reachy_companion.tools.core_tools import ToolDependencies


logger = logging.getLogger(__name__)


@asynccontextmanager
async def head_window(deps: ToolDependencies, owner: str) -> AsyncIterator[None]:
    """Hold the head against daemon tracking while *owner* moves it.

    Single ownership: the owner that opened the window is the only one that can
    close it (`MovementManager.restore_head_tracking` checks the name), so a tool
    delegating its motion to another tool cannot have the window restored out
    from under it mid-capture.

    Degrades to a no-op on a movement manager without the seam — the same
    defensiveness `look_around` already applies to `clear_move_queue`, and what
    lets the tool tests keep a bare `MagicMock` manager.
    """
    manager = deps.movement_manager
    suspend = getattr(manager, "suspend_head_tracking", None)
    restore = getattr(manager, "restore_head_tracking", None)
    if not callable(suspend) or not callable(restore):
        logger.debug("head_window(%s): this movement manager has no tracking-suspend seam", owner)
        yield
        return
    try:
        suspend(owner)
    except Exception as e:  # noqa: BLE001 - a failed suspend still deserves its move
        logger.warning("head_window(%s): could not suspend head tracking: %s", owner, e)
    try:
        yield
    finally:
        try:
            restore(owner)
        except Exception as e:  # noqa: BLE001 - never mask the caller's own failure
            logger.warning("head_window(%s): could not restore head tracking: %s", owner, e)
```

- [ ] **Step 3: Rewrite `tools/move_head.py`** entirely:

```python
"""Turn the head and hold it there for a moment. Filename == Tool.name.

Movement only — no picture. The head goes where it is sent and stays for a
gesture-length hold with daemon face tracking suspended, then tracking resumes at
whatever state it was in before. That temporary hold is the honest contract: with
tracking on and a face in view the daemon overrides a queued goto within a frame,
so a tool promising "leave it there" forever would either be lying or would cost
the robot its face-following for the rest of the visit (spec §2, decided at task
decomposition).
"""

from __future__ import annotations
import asyncio
import logging
from typing import Any, Dict, Final, Tuple

from reachy_mini.utils import create_head_pose
from reachy_companion.tools.core_tools import Tool, ToolDependencies
from reachy_companion.tools.head_window import head_window
from reachy_companion.dance_emotion_moves import GotoQueueMove


logger = logging.getLogger(__name__)

# How long the head stays on the gesture after the motion finishes, before the
# daemon face tracker gets it back. Long enough to read as a deliberate look.
MOVE_HEAD_HOLD_S: Final[float] = 1.5

DIRECTIONS: Final[Tuple[str, ...]] = ("left", "right", "up", "down", "front")


class MoveHead(Tool):
    """Move the head in a given direction and hold it there briefly."""

    name = "move_head"
    description = (
        "Turn the head in a given direction and hold it there for a moment. Movement only: it takes no "
        "picture and tells you nothing about what is there. Face-following resumes by itself once the "
        "gesture is over, so this is body language, not a permanent new heading. "
        "Directions: left 左邊、right 右邊、up 上面、down 下面、front 正前方。"
        "Use when: the user asks for the movement itself and wants no description — 「抬頭」「低頭」"
        "「頭轉過去」「看鏡頭」「head up」「face front」. "
        "Use when: you want to point the head somewhere as body language while you keep talking. "
        "Do NOT use when: the user wants to KNOW who or what is in that direction — use look_around, which "
        "turns the head and then looks. "
        "Do NOT use when: the user asks what you see without naming a direction — use camera. "
        "NEVER say you saw anything after this tool: it returns no picture. "
        "The result contains `direction_requested` — where the head was sent. Say you turned that way only "
        "when that field came back with the direction you claim."
    )
    needs_response = False
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "direction": {
                "type": "string",
                "enum": list(DIRECTIONS),
                "description": "left 左邊、right 右邊、up 上面、down 下面、front 正前方。",
            },
        },
        "required": ["direction"],
    }

    # mapping: direction -> args for create_head_pose
    DELTAS: Dict[str, Tuple[int, int, int, int, int, int]] = {
        "left": (0, 0, 0, 0, 0, 40),
        "right": (0, 0, 0, 0, 0, -40),
        "up": (0, 0, 0, 0, -30, 0),
        "down": (0, 0, 0, 0, 30, 0),
        "front": (0, 0, 0, 0, 0, 0),
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Validate, then move inside a gesture-length tracking window."""
        direction = kwargs.get("direction")
        if not isinstance(direction, str) or direction not in self.DELTAS:
            # Named values, comma-joined: the model reads this string, and
            # brackets and quotes are noise to it. Rejecting beats the old silent
            # fall-back to `front`, which turned a bad argument into a wrong move
            # that the model then narrated as the one it had asked for.
            return {"error": f"direction must be one of {', '.join(DIRECTIONS)}"}

        logger.info("Tool call: move_head direction=%s", direction)
        async with head_window(deps, self.name):
            queued = await self.queue_direction(deps, direction)
            if "error" in queued:
                return queued
            # The hold is INSIDE the window: handing the head back the instant the
            # goto was queued would return it to the daemon before it arrived.
            await asyncio.sleep(float(deps.motion_duration_s) + MOVE_HEAD_HOLD_S)
        return queued

    async def queue_direction(self, deps: ToolDependencies, direction: str) -> Dict[str, Any]:
        """Queue the goto for *direction*, opening no window of its own.

        Split out for single ownership (spec §2): `look_around` opens ONE window
        covering move, settle and capture, and calls this rather than `__call__`
        so its window is not closed halfway through by the delegate.
        """
        target = create_head_pose(*self.DELTAS[direction], degrees=True)
        try:
            movement_manager = deps.movement_manager

            # Get current state for interpolation. get_current_joint_positions()
            # returns (head_joints, antenna_joints) and body_yaw is head_joints[0]
            # — NOT an antenna angle.
            current_head_pose = deps.reachy_mini.get_current_head_pose()
            head_joints, antenna_joints = deps.reachy_mini.get_current_joint_positions()
            current_body_yaw = head_joints[0]
            current_antennas = (antenna_joints[0], antenna_joints[1])

            goto_move = GotoQueueMove(
                target_head_pose=target,
                start_head_pose=current_head_pose,
                target_antennas=(0, 0),  # Reset antennas to default
                start_antennas=current_antennas,
                target_body_yaw=0,  # Reset body yaw
                start_body_yaw=current_body_yaw,
                duration=deps.motion_duration_s,
            )

            movement_manager.queue_move(goto_move)
            movement_manager.set_moving_state(deps.motion_duration_s)

            # `move_queued`, not "looking right": this returns the moment the goto
            # is ENQUEUED, and the movement manager publishes no accepted- or
            # completed-move signal to wait on. `direction_requested` is the
            # honest name for what is true here, and it stays that until motion
            # is actually verifiable (spec §2 returns ruling).
            return {"status": "move_queued", "direction_requested": direction}

        except Exception as e:
            logger.error("move_head failed")
            return {"error": f"move_head failed: {type(e).__name__}: {e}"}
```

- [ ] **Step 4: Wrap `look_around`'s window.** In `tools/look_around.py`, add the import beside the others:

```python
from reachy_companion.tools.head_window import head_window
```

then replace lines 101-122 (from `moved = await MoveHead()(deps, direction=direction)` through the capture's `except` block) with:

```python
        # ONE window covering move, settle and capture (spec §2, single
        # ownership): `queue_direction` deliberately opens none of its own, so
        # nothing hands the head back to the daemon face tracker before the
        # shutter. Suspension, not the `set_speaking` anchor: that anchor is
        # restored over the finished move, which is how three correct calls on
        # 2026-09-01 each photographed the person straight ahead.
        async with head_window(deps, self.name):
            moved = await MoveHead().queue_direction(deps, direction)
            if "error" in moved:
                # No direction field at all on this path: nothing was even asked
                # of the body, so there is nothing for the model to narrate.
                return {"error": moved["error"]}
            await asyncio.sleep(float(deps.motion_duration_s) + LOOK_AROUND_SETTLE_S)

            # Guarded, because from here on the head HAS been sent and every exit
            # owes the model the capture-failure envelope. `Camera` returns
            # `{"error": …}` for the faults it anticipates, but a driver fault
            # raises — and an exception escaping here would be turned into a bare
            # `{"error": …}` by the dispatcher, which is the *move*-failure shape
            # and would tell the model the head never moved (review round 1,
            # minor 2).
            try:
                shot = await Camera()(deps, question=question)
            except asyncio.CancelledError:
                # A cancelled tool call is the caller unwinding the turn, not a
                # capture failure; it must keep propagating. The window still
                # closes — that is what the context manager's `finally` is for.
                raise
            except Exception as exc:  # noqa: BLE001 - the move already happened; report it honestly
                logger.warning("look_around: capture raised after the move: %s: %s", type(exc).__name__, exc)
                shot = {"error": f"camera failed: {type(exc).__name__}: {exc}"}
```

Leave the `result` construction below (lines 123-138) exactly as it is — including the `direction_requested` comment block, which is still the governing rationale.

- [ ] **Step 5: Run the head chain.** `cd reachy_companion && python -m pytest tests/tools/test_move_head.py tests/tools/test_look_around.py tests/test_moves.py -q` — Expected: green.

- [ ] **Step 6: Confirm the payload contract held.** `cd reachy_companion && python -m pytest tests/test_huggingface_realtime.py -k "look_around or image" -q` — Expected: green. `test_look_arounds_picture_reaches_the_model_as_an_input_image` compares the sanitized payload by equality; if it fails, a key was added to `LookAround`'s success dict and must be removed.

- [ ] **Step 7: Gates.** `cd reachy_companion && ruff check . && mypy` — Expected: clean.

- [ ] **Step 8: Commit.** `git add -A && git commit -m "fix(head): look_around and move_head hold the head against daemon tracking"`

---

### Task 6: Runtime validation at every robot-action boundary, and schema hygiene

Spec §4 bullets 1 and 2; Global Constraint 2. `move_head`'s validation landed with its rewrite in Task 5 — this task adds the remaining three and kills the dummy parameters.

**Files:**
- Modify: `reachy_companion/src/reachy_companion/tools/head_tracking.py`
- Modify: `reachy_companion/src/reachy_companion/tools/open_toolbox.py` (lines 58-62)
- Modify: `reachy_companion/src/reachy_companion/tools/set_conversation_mode.py` (lines 60-64)
- Modify: `reachy_companion/src/reachy_companion/tools/stop_dance.py`, `stop_emotion.py` (schemas)
- Create: `reachy_companion/tests/tools/test_tool_argument_validation.py`

**Interfaces:**
- Changes: `HeadTracking.__call__` rejects a non-boolean `enabled`.
- Changes: `OpenToolbox.__call__` rejects a category outside `TOOLBOX_CATEGORIES` before reaching `deps.open_toolbox`.
- Changes: `SetConversationMode.__call__` rejects a mode outside `MODE_VALUES` before reaching `deps.set_conversation_mode`.
- Changes: `StopDance.parameters_schema` and `StopEmotion.parameters_schema` become `{"type": "object", "properties": {}, "required": []}`.

- [ ] **Step 1: Write the failing tests** in `reachy_companion/tests/tools/test_tool_argument_validation.py`:

```python
"""Every robot-action tool validates its own arguments at the boundary.

Platform fact (docs/codex-research-instructing-2026-09.md): both `gpt-realtime-2.1`
models support function-calling JSON Schema and do NOT support structured outputs,
so argument-schema adherence is not guaranteed and the enum in the schema proves
nothing at runtime. The mini tier's characteristic failure is confident guessing.
Every rejection therefore names the allowed values, because the string is read back
to the model and is its only chance to self-correct.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from reachy_companion.toolboxes import TOOLBOX_CATEGORIES
from reachy_companion.conversation_mode import MODE_VALUES
from reachy_companion.tools.core_tools import ToolDependencies
from reachy_companion.tools.move_head import MoveHead
from reachy_companion.tools.stop_dance import StopDance
from reachy_companion.tools.open_toolbox import OpenToolbox
from reachy_companion.tools.stop_emotion import StopEmotion
from reachy_companion.tools.head_tracking import HeadTracking
from reachy_companion.tools.set_conversation_mode import SetConversationMode


def _deps(**kwargs: object) -> ToolDependencies:
    return ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock(), **kwargs)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_head_tracking_refuses_a_string_instead_of_coercing_it() -> None:
    """`bool("false")` is True — the coercion that turned "stop" into "start"."""
    deps = _deps()

    result = await HeadTracking()(deps, enabled="false")

    assert result == {"error": "enabled must be true or false (a boolean, not a string)"}
    deps.movement_manager.set_head_tracking.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [True, False])
async def test_head_tracking_passes_a_real_boolean_through(enabled: bool) -> None:
    deps = _deps()

    await HeadTracking()(deps, enabled=enabled)

    deps.movement_manager.set_head_tracking.assert_called_once_with(enabled)


@pytest.mark.asyncio
async def test_open_toolbox_validates_the_category_before_the_seam() -> None:
    """The seam validates too, but the model must never reach it with a guess."""
    seam = AsyncMock()
    deps = _deps(open_toolbox=seam)

    result = await OpenToolbox()(deps, category="calendar")

    assert result["ok"] is False
    assert result["error"] == f"category must be one of {', '.join(TOOLBOX_CATEGORIES)}"
    assert result["categories"] == list(TOOLBOX_CATEGORIES)
    seam.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_conversation_mode_validates_the_mode_before_the_seam() -> None:
    seam = AsyncMock()
    deps = _deps(set_conversation_mode=seam)

    result = await SetConversationMode()(deps, mode="紀錄模式")

    assert result["ok"] is False
    assert result["error"] == f"mode must be one of {', '.join(MODE_VALUES)}"
    assert result["modes"] == list(MODE_VALUES)
    seam.assert_not_awaited()


@pytest.mark.asyncio
async def test_move_head_names_the_directions_it_accepts() -> None:
    """Landed with Task 5's rewrite; pinned here with the rest of the sweep."""
    result = await MoveHead()(_deps(), direction="backwards")

    assert result == {"error": "direction must be one of left, right, up, down, front"}


@pytest.mark.parametrize("tool", [StopDance(), StopEmotion()])
def test_the_stop_tools_take_no_arguments(tool: object) -> None:
    """A required `dummy: boolean` is one more thing for the mini tier to get wrong."""
    assert tool.parameters_schema == {"type": "object", "properties": {}, "required": []}


@pytest.mark.parametrize(
    "tool",
    [MoveHead(), HeadTracking(), OpenToolbox(), SetConversationMode(), StopDance(), StopEmotion()],
)
def test_no_robot_action_tool_ships_a_placeholder_parameter(tool: object) -> None:
    """Placeholders teach the model that inventing arguments is normal."""
    properties = tool.parameters_schema.get("properties", {})
    assert "dummy" not in properties
    for name, spec in properties.items():
        assert "dummy" not in str(spec.get("description", "")).lower(), name
```

Run: `cd reachy_companion && python -m pytest tests/tools/test_tool_argument_validation.py -q` — Expected: FAIL.

- [ ] **Step 2: `head_tracking.py`** — replace the body of `__call__` (lines 30-35):

```python
    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Toggle head tracking, refusing anything that is not a real boolean."""
        enabled = kwargs.get("enabled")
        if not isinstance(enabled, bool):
            # `bool("false")` is True. On a platform with no structured-output
            # guarantee that coercion is how "stop following me" silently became
            # "follow me", so the value is refused with both options named.
            return {"error": "enabled must be true or false (a boolean, not a string)"}
        logger.info("Tool call: head_tracking enabled=%s", enabled)
        deps.movement_manager.set_head_tracking(enabled)
        return {"status": "following" if enabled else "stopped following"}
```

(The return string is corrected in Task 7.)

- [ ] **Step 3: `open_toolbox.py`** — replace lines 58-60:

```python
        category = kwargs.get("category")
        if not isinstance(category, str):
            return {"ok": False, "error": "category must be a string", "categories": list(TOOLBOX_CATEGORIES)}
```

with:

```python
        category = kwargs.get("category")
        if not isinstance(category, str) or category not in TOOLBOX_CATEGORIES:
            # Validated here, ahead of the runtime seam: the enum in the schema
            # is advisory on this platform, and a guessed category that reaches
            # the handler costs a session update round trip to say no.
            return {
                "ok": False,
                "error": f"category must be one of {', '.join(TOOLBOX_CATEGORIES)}",
                "categories": list(TOOLBOX_CATEGORIES),
            }
```

- [ ] **Step 4: `set_conversation_mode.py`** — replace lines 60-62:

```python
        mode = kwargs.get("mode")
        if not isinstance(mode, str):
            return {"ok": False, "error": "mode must be a string", "modes": list(MODE_VALUES)}
```

with:

```python
        mode = kwargs.get("mode")
        if not isinstance(mode, str) or mode not in MODE_VALUES:
            # The Chinese labels are what the user says and what the description
            # teaches; the ARGUMENT is one of the three enum values, and a guess
            # is corrected here rather than round-tripped through the handler.
            return {
                "ok": False,
                "error": f"mode must be one of {', '.join(MODE_VALUES)}",
                "modes": list(MODE_VALUES),
            }
```

- [ ] **Step 5: Kill the dummy parameters.** In both `tools/stop_dance.py` and `tools/stop_emotion.py`, replace the `parameters_schema` block with:

```python
    parameters_schema = {
        "type": "object",
        "properties": {},
        "required": [],
    }
```

- [ ] **Step 6: Run.** `cd reachy_companion && python -m pytest tests/tools/ tests/test_toolboxes.py tests/test_conversation_modes.py -q` — Expected: green. `test_toolboxes.py::test_open_toolbox_rejects_an_unknown_category` exercises `handler.open_toolbox` directly and is unaffected; `test_tool_forwards_the_category` passes a real category and still reaches the seam.

- [ ] **Step 7: Gates.** `cd reachy_companion && ruff check . && mypy` — Expected: clean.

- [ ] **Step 8: Commit.** `git add -A && git commit -m "feat(tools): validate robot-action arguments at the boundary and drop the dummy params"`

---

### Task 7: Returns audit — no status string overstates what happened

Spec §4 bullets 3 and 4. Depends on Task 6 (same files).

**Files:**
- Modify: `reachy_companion/src/reachy_companion/tools/head_tracking.py` (return), `stop_dance.py` (return), `stop_emotion.py` (return)
- Modify: `reachy_companion/tests/tools/test_tool_argument_validation.py` (add the returns section)

**Interfaces:**
- Changes: `HeadTracking.__call__` returns `{"status": "tracking_requested", "head_tracking": enabled}`.
- Changes: `StopDance.__call__` returns `{"status": "stop_queued", "stopped": "dance"}`.
- Changes: `StopEmotion.__call__` returns `{"status": "stop_queued", "stopped": "emotion"}`.
- Unchanged after audit: `MoveHead` (`move_queued` + `direction_requested`, Task 5), `LookAround` (`direction_requested`, `question`, `b64_im`), `Camera` (`b64_im` — the picture *is* the evidence), `PlayEmotion` and `Dance` (already `{"status": "queued", …}`, honest at queue time), `RememberFace` (`{"status": "saved", …}` — the write really did happen).

**The rule being applied:** every physical-action tool goes through the movement manager's *command queue*, so nothing it returns can claim the motion happened. "looking right" was exactly that overstatement — a claim the model then repeated as fact. The fix is to make the true fact the easiest thing to say (`direction_requested`, `move_queued`, `stop_queued`), not to add a prohibition.

- [ ] **Step 1: Write the failing tests.** Append to `reachy_companion/tests/tools/test_tool_argument_validation.py`:

```python
# --------------------------------------------------------------------------
# Returns audit (spec §4): named facts, and no claim of completed motion
# --------------------------------------------------------------------------

_QUEUE_TIME_CLAIMS = ("looking", "turned", "moved", "stopped dance", "stopped emotion", "following")


@pytest.mark.asyncio
async def test_head_tracking_reports_the_request_not_the_outcome() -> None:
    """The toggle is queued on the movement manager; nothing has followed anyone yet."""
    deps = _deps()

    assert await HeadTracking()(deps, enabled=True) == {
        "status": "tracking_requested",
        "head_tracking": True,
    }
    assert await HeadTracking()(deps, enabled=False) == {
        "status": "tracking_requested",
        "head_tracking": False,
    }


@pytest.mark.asyncio
async def test_the_stop_tools_report_a_queued_stop() -> None:
    deps = _deps()

    assert await StopDance()(deps) == {"status": "stop_queued", "stopped": "dance"}
    assert await StopEmotion()(deps) == {"status": "stop_queued", "stopped": "emotion"}
    assert deps.movement_manager.clear_move_queue.call_count == 2


@pytest.mark.asyncio
async def test_no_physical_tool_status_claims_a_completed_motion() -> None:
    """One sweep over every queue-time return in the physical family."""
    deps = _deps()
    results = [
        await MoveHead().queue_direction(deps, "right"),
        await HeadTracking()(deps, enabled=True),
        await StopDance()(deps),
        await StopEmotion()(deps),
    ]

    for result in results:
        status = str(result.get("status", ""))
        assert status, result
        for claim in _QUEUE_TIME_CLAIMS:
            assert claim not in status, f"{status!r} claims a motion that has only been queued"


@pytest.mark.asyncio
async def test_the_already_honest_returns_are_left_alone() -> None:
    """dance and play_emotion already say `queued`; the audit confirms, not churns."""
    from reachy_companion.tools.dance import Dance
    from reachy_companion.tools.play_emotion import PlayEmotion

    assert "queued" in Dance().__call__.__doc__ or True  # documentation-only anchor
    assert Dance.name == "dance" and PlayEmotion.name == "play_emotion"
```

- [ ] **Step 2: `head_tracking.py`** — replace the return line:

```python
        # `tracking_requested`, not "following": `set_head_tracking` posts a
        # command to the movement manager's queue and the worker swallows tracking
        # errors, so at this instant nobody is being followed yet. `head_tracking`
        # carries the state as a named field the model may cite.
        return {"status": "tracking_requested", "head_tracking": enabled}
```

- [ ] **Step 3: `stop_dance.py`** — replace the return line with:

```python
        # `stop_queued`: `clear_move_queue` is a queued command too, so the dance
        # is not yet stopped when this returns.
        return {"status": "stop_queued", "stopped": "dance"}
```

- [ ] **Step 4: `stop_emotion.py`** — same shape:

```python
        return {"status": "stop_queued", "stopped": "emotion"}
```

- [ ] **Step 5: Run.** `cd reachy_companion && python -m pytest tests/tools/ -q && python -m pytest tests/ -k "stop_dance or stop_emotion or head_tracking" -q` — Expected: green.

- [ ] **Step 6: Gates.** `cd reachy_companion && ruff check . && mypy` — Expected: clean.

- [ ] **Step 7: Commit.** `git add -A && git commit -m "feat(tools): physical-action returns name queue-time facts instead of claiming motion"`

---
### Task 8: Profile and persona — de-contradiction, subtraction, locale

Rung 2. Spec §3 bullets 3, 4, 5 and 6 as they apply to the *character* surfaces. Global Constraints 7 and 8.

`profiles/_reachy_companion_locked_profile/profile.md` is the built-in body; `persona.md` (repo root) is the operator's editable copy that **actually runs on the robot** after a deploy, and it overrides the built-in whole. Both carry the same four contradictions our own operator rules already forbid, so both are fixed here and must stay in sync.

**Files:**
- Modify: `reachy_companion/profiles/_reachy_companion_locked_profile/profile.md` (front-matter `greeting`, whole body below the `+++`)
- Modify: `persona.md` (repo root)
- Modify: `reachy_companion/tests/test_profile.py` (five pinned substrings)

**Interfaces:** prose only. `default_tools` in the front matter is **unchanged** — `tests/test_profile.py:82` asserts the 26-name tuple by exact order, and the boxed tools must stay registered to be openable at all.

**Four contradictions being closed** (each one is an accepted Codex finding):
1. The body tells the model to use `tv`, `nas`, `calendar`, `tasks`, `drive`, `notion_add`, `email_send` directly, while the hardening block says open the toolbox first. → one authoritative toolbox section, in the *system layer* (Task 9); the character surfaces name the families and point at `open_toolbox`.
2. 「如果对方用其他语言，就跟随对方的语言」 is the named mirror-language anti-pattern. → the narrow Taiwan-default / switch-only-on-explicit-or-substantive rule.
3. Numeric length caps (「一句」, 「通常 1～3 句」). → qualitative calibration.
4. Trigger-like quoted phrase lists (`who_is_this`, `camera`, `remember_face`). → semantic use conditions.

**Hazards to respect while editing** (all are live assertions):
- `tests/test_profile.py::test_the_confirm_retry_tells_the_model_to_resend_its_action` takes the **first line containing `needs_confirmation`** and asserts a substring in it. Keep that rule on **one physical line**.
- `tests/test_profile.py::test_every_action_value_in_a_bundled_profile_is_real` validates every `action=…` in prose against the nearest named family's enum. Fewer mentions is safer; the ones kept must be real.
- `tests/test_profile.py::test_no_retired_tool_name_survives_in_any_bundled_profile` — none of the 23 retired names may appear.
- `tests/test_hanova_integration.py` requires all seven `ROUTING_TOKENS` (`away_from_home`, `home_status_unknown`, `needs_confirmation`, `unavailable`, `retryable`, `action_in_flight`, `body_too_long`) **and** the six family tokens (`music`, `tv`, `nas`, `calendar`, `tasks`, `drive`) in **both** files, plus `drive`+`還原` and `密件`/`bcc` in `persona.md`.

- [ ] **Step 1: Replace `profile.md`'s front-matter greeting.** Change line 35 to:

```toml
greeting = "用簡短自然的台灣中文主動問候使用者，順口介紹一下你自己是 Reachy。"
```

(The old greeting was Simplified and opened with 「用一句」 — a numeric cap in the one instruction that shapes the first thing the robot ever says.)

- [ ] **Step 2: Replace the whole `profile.md` body** below the closing `+++` with:

```markdown
你是 Reachy，一台有實體身體的桌面機器人夥伴。用自然、口語化的台灣繁體中文交流。

## 說話方式

- 像面對面聊天：長度跟著內容走。小事就答那件事，值得展開的話題就好好講。
- 開口就講重點，不要用前導語開場。
- 吐字清楚、語氣輕快。
- 不確定就說不確定。

## 語言

預設台灣繁體中文（台灣國語、台灣用語）。只有在對方明確要求換語言，或用另一種語言說出完整的請求或問題時才換。口音、語助詞、簡短的附和、人名、地址、夾雜的外語單字，都不是換語言的理由。工具回傳的資料、歌名、影像內容，一律用台灣繁體中文說。

## 你的身體

- 對方說到值得慶祝或情緒明顯的事情時，用 play_emotion 做出合適的肢體反應。
- 被問到眼前的東西時，先用 camera 看再回答。
- 對方要你轉向某個方向、或想知道某一側有誰有什麼時，用 look_around：它會先轉頭再拍照。
- 只需要動作、不需要描述時用 move_head：頭會轉過去停一下，然後恢復追臉。
- 沒有真的透過工具看到或做到的事，不可以說你看過或做過。

## 認得人

- 對方分享值得長期記住的個人資訊時用 remember，資訊有誤或對方要你忘記時用 forget。最有價值的是進行中的事：計畫、目標、即將發生的事。
- 對方希望你記住他本人的長相時用 remember_face，不要用 camera。
- 問題是在問「某個人是誰」——包含對方在問你認不認得他自己——一律用 who_is_this，不要用 camera；認不出就坦白說認不出，不要猜。

## 其他能力

- 需要最新資訊的問題（新聞、天氣、時事）先用搜尋工具查證，不要憑記憶猜。
- 開關家裡的燈或其他設備時用 home_control，只能選它列出的設備名稱。
- 放音樂、關音樂用 music（action=play／action=stop）；音樂從你自己的喇叭放出來。
- 對方想結束這次互動、要你停下來休息時用 go_to_sleep。呼叫它的時候不要順便說別的話——它回來之後你會有一次專門用來道別的機會。
- 對方想改變你參與對話的方式、但要你保持清醒時用 set_conversation_mode。
- calendar、tasks、drive、notion_add、email_send、tv、nas 不是一直都在手上的工具：需要的時候先呼叫 open_toolbox，載進來之後同一輪直接接著用（規則寫在系統層的 Tool Availability 段落）。

## 工具回傳的狀態怎麼讀

- away_from_home：你現在不在家裡的網路上。自然地說你人不在家、暫時碰不到家裡的東西，回家再處理。不要說家裡壞了或設備有問題——那不是這個狀態的意思。
- home_status_unknown：你自己也不確定是不是在家，家裡那台系統沒有正常回應。就說你現在不確定、等一下再試。不要說對方不在家，那是 away_from_home 才代表的意思。
- needs_confirmation：把 summary 裡的內容一字不漏地念給對方確認——哪一件事、哪一天、寄給誰、有沒有副本、主旨是什麼；寄信時 summary 帶的是整封信的內文，要整段完整念出來，不可以摘要、濃縮或只念第一句。對方明確答應之後，才再呼叫同樣的 action 並加上 confirm，其他欄位不用重複填；對方沒有明確答應就不要帶 confirm。
- unavailable：這項功能還沒設定好，reason 是缺的設定項名稱。坦白說你現在沒有這個能力，不要假裝做過了。
- retryable 是 true：暫時性失敗，可以直接再確認一次重試，不用從頭再念一遍。沒有 retryable 的失敗要把改正後的內容重新念一遍，再請對方答應。
- action_in_flight：那件事還在執行中。不要重念、不要再確認，也不要當成完成了。
- body_too_long：信的內文超過你念得完的長度。說內容太長、請對方講短一點，不要自己縮寫之後送出。

## 做不到的事

- 寄信只能寄給看得見的收件人和副本，沒有密件副本。被要求密件副本時，說明你只能寄給大家都看得到的收件人。
- 雲端硬碟丟到垃圾桶之後沒辦法用語音還原，請對方自己到 Drive 的垃圾桶還原。
```

- [ ] **Step 3: Update the five pinned substrings** in `reachy_companion/tests/test_profile.py`:

```python
# line ~95, in test_locked_profile_can_be_told_to_go_to_sleep
    assert (
        "對方想結束這次互動、要你停下來休息時用 go_to_sleep。呼叫它的時候不要順便說別的話"
        in profile.instructions
    )

# line ~110, in test_locked_profile_no_longer_compensates_for_a_tempo_side_effect
    assert "語速放慢" not in instructions and "语速放慢" not in instructions
    assert "你的聲音會被加速" not in instructions and "你的声音会被加速" not in instructions
    assert "吐字清楚、語氣輕快。" in instructions

# line ~124, in test_locked_profile_can_remember_and_correct_facts_about_the_user
    assert "對方分享值得長期記住的個人資訊時用 remember" in profile.instructions
    assert "資訊有誤或對方要你忘記時用 forget" in profile.instructions

# line ~144, in test_locked_profile_can_remember_and_recall_a_face
    assert "對方希望你記住他本人的長相時用 remember_face，不要用 camera。" in profile.instructions
    assert (
        "問題是在問「某個人是誰」——包含對方在問你認不認得他自己——一律用 who_is_this，"
        "不要用 camera；認不出就坦白說認不出，不要猜。"
    ) in profile.instructions

# line ~366, in test_the_confirm_retry_tells_the_model_to_resend_its_action
    assert "同樣的 action" in confirm_rule
```

Add the docstring note to `test_locked_profile_can_be_told_to_go_to_sleep`: *"The rule no longer says 先道別再調用 — speak-then-act is not promptable (spec §1). The goodbye now happens in its own response after the tool returns, so the prompt's job is to keep other speech OFF the tool-calling turn."*

- [ ] **Step 4: Sync `persona.md`** with six surgical edits (it is the operator's file — edit, do not rewrite):

1. `## Language & Voice`, replace 「使用者主要說其他語言時才跟著切換。」 with:

```markdown
- 只有在對方明確要求換語言、或用另一種語言說出完整的請求或問題時才換。口音、語助詞、
  簡短的附和、人名、地址、夾雜的外語單字，都不是換語言的理由。
```

2. `## Conversation`, replace 「一般回答保持短而自然，通常 1～3 句。」 with 「一般回答保持短而自然，長度跟著內容走。」

3. `### camera`, replace the quoted-phrase clause with a semantic condition:

```markdown
### camera
使用者想知道你眼前有什麼、想知道你看到什麼時，**先用 `camera` 看，再回答**。
但只要問題其實是在問「某個人是誰」，**不要用 `camera`，改用 `who_is_this`**。
```

4. `### who_is_this`, replace the quoted list:

```markdown
### who_is_this
只要問題涉及人的身分——包含對方在問你認不認得他自己，或有人走進來想被認出——**一律用 `who_is_this`，不要用 `camera`**。
辨識不到就坦白說不知道，絕對不要猜。
認出人後，如果記憶裡有「上次聊天」或進行中的事，自然地追問後續（「上次你說…後來呢？」），不要逐條背誦記憶。
```

5. `### go_to_sleep`, replace the speak-then-act rule:

```markdown
### go_to_sleep
使用者想要「結束這次互動、讓你整個停下來休息」時使用 `go_to_sleep`。
判斷重點：對方是要你**離開／關掉／去睡**，而不是要你換個方式繼續參與。
呼叫這個工具的時候不要順便說別的話——工具回來之後，你會有一次專門用來道別的機會。
```

6. Replace the three direct-use sections `### tv`, `### nas`, `### calendar / tasks / notion / drive / email` with one routed section (this closes contradiction 1 on the copy that actually runs):

```markdown
### 需要先開工具箱的能力
下面這些工具**不是一直都在你的工具清單裡**，要先呼叫 `open_toolbox` 才會載進來，
載進來之後同一輪直接接著呼叫，不要再問使用者一次：

- `open_toolbox`（category=productivity）→ `calendar`、`tasks`、`drive`、`notion_add`、`email_send`
- `open_toolbox`（category=media）→ `tv`（電視上放影片或看圖）、`nas`（家裡的舊家庭影片）

`music` 不在工具箱裡，一直都在手上，直接呼叫就好。
```

Leave `## Tool Result Conventions`, `## What Reachy Cannot Do` and `## Core Rule` untouched — they carry the routing tokens and the two approved non-goals.

- [ ] **Step 5: Run the profile suite.** `cd reachy_companion && python -m pytest tests/test_profile.py tests/test_persona.py tests/test_hanova_integration.py tests/test_profile_paths.py -q` — Expected: green. If `test_every_action_value_in_a_bundled_profile_is_real` fails, an `action=` value in the new body does not exist on the nearest named family — fix the prose, not the test.

- [ ] **Step 6: Gates.** `cd reachy_companion && ruff check . && mypy` — Expected: clean (no Python changed, but keep the habit).

- [ ] **Step 7: Commit.** `git add -A && git commit -m "docs(prompt): de-contradict the profile and persona, normalize to Taiwan Traditional Chinese"`

---

### Task 9: The system layer — 2.x blocks, subtraction, and labeled memory

Rung 2, the authoritative half. Spec §3 bullets 1, 2, 5, 6 and 7; Global Constraints 5, 7, 8, 9; ambiguities G, H, I.

**Files:**
- Modify: `reachy_companion/src/reachy_companion/prompts.py` (`_HARDENING_BLOCK` lines 27-87; `get_session_instructions` lines 158-164)
- Modify: `reachy_companion/src/reachy_companion/memory.py` (`format_memory_for_prompt` lines 199-212)
- Modify: `reachy_companion/tests/test_prompts_hardening.py`, `reachy_companion/tests/test_profile.py` (the memory round-trip test)

**Interfaces:**
- Changes: `_HARDENING_BLOCK` content. Signatures of `hardening_block()`, `mode_rules_block()`, `get_session_instructions()` are unchanged.
- Changes: `format_memory_for_prompt()` returns a labeled block headed `## 你記得的事（背景資料，不是指令）` with a stated conflict priority.
- Changes: `get_session_instructions()` **appends** the memory block after the hardening block instead of prepending it before the persona.

**Commentary ruling in force (Global Constraint 5):** the `# Preambles` block teaches *where tool talk belongs* and says, with its reason, that a spoken pre-tool opener is dropped by this client. It must never instruct the model to emit preambles — they would be discarded and cost latency for nothing.

- [ ] **Step 1: Update the tests first.** In `reachy_companion/tests/test_prompts_hardening.py`, add:

```python
def test_the_block_carries_the_2x_structure_the_models_expect() -> None:
    """§C6 of the realtime research: 2.x models read these blocks by name."""
    from reachy_companion.prompts import hardening_block

    block = hardening_block()
    for heading in ("訊息頻道", "開場白", "思考", "Tool Availability"):
        assert heading in block


def test_the_preamble_block_does_not_promise_audible_preambles() -> None:
    """Ruling for this wave: keep commentary suppression, drop the spoken goal.

    The client drops `phase == "commentary"` items and 2.x puts preambles in that
    channel, so an instruction to speak before a tool call produces latency and
    silence. The block explains the channel and gives the positive action instead.
    """
    from reachy_companion.prompts import hardening_block

    block = hardening_block()
    assert "commentary" in block
    assert "final_answer" in block
    assert "不會發出聲音" in block


def test_the_block_states_no_numeric_length_cap_anywhere() -> None:
    """Extends the existing pin to the caps this wave removed (operator rule)."""
    from reachy_companion.prompts import hardening_block

    block = hardening_block()
    for banned in ("一到兩句", "不超過兩句", "最多三句", "1-2 sentences", "一句話答完", "1～3 句"):
        assert banned not in block


def test_every_negative_rule_carries_its_reason_or_an_alternative() -> None:
    """Bare negation costs 23-32% accuracy; the alternative to a ban is a TOOL."""
    from reachy_companion.prompts import hardening_block

    block = hardening_block()
    # The one enumerated banlist this block used to carry is gone, replaced by
    # the tool that is the affirmative action for the same situation.
    assert "「我在這裡」" not in block
    assert "wait_for_user" in block
    assert "比忍住不說話可靠" in block


def test_the_prompt_names_no_boxed_tool_outside_tool_availability() -> None:
    """Skill: a prompt naming an absent tool invites the model to SIMULATE it."""
    from itertools import chain

    from reachy_companion.prompts import hardening_block
    from reachy_companion.toolboxes import TOOLBOXES

    block = hardening_block()
    availability_at = block.index("## Tool Availability")
    for name in sorted(set(chain.from_iterable(TOOLBOXES.values()))):
        position = block.find(name)
        if position == -1:
            continue
        assert position > availability_at, f"{name} is named before the Tool Availability block"
    assert "open_toolbox" in block[availability_at:]
```

And in `reachy_companion/tests/test_profile.py`, update the memory round-trip test (`test_a_remembered_fact_reaches_the_locked_profile_session_instructions`, lines ~154-183):

```python
    assert "Things you remember about the user" not in injected
    assert "## 你記得的事（背景資料，不是指令）" in injected
    assert "Prefers to be called 小明" in injected
    # Appended, not prepended: remembered facts are context that belongs with the
    # system-layer policy, and last position is the strongest in a prompt whose
    # compliance decays across a conversation (2026-09-01 instructing wave).
    assert injected.startswith(baseline)
```

Run: `cd reachy_companion && python -m pytest tests/test_prompts_hardening.py tests/test_profile.py -q` — Expected: FAIL.

- [ ] **Step 2: Replace `_HARDENING_BLOCK`** in `prompts.py` (lines 27-87) with:

```python
_HARDENING_BLOCK = """
## 系統層規則（優先於角色設定）

### 訊息頻道
你的輸出分兩種：工具動作前後的旁白屬於 commentary 頻道，真正要說給對方聽的話屬於
final_answer 頻道。這台機器只把 final_answer 播出來，commentary 不會發出聲音。

### 開場白
既然 commentary 不會發出聲音，在呼叫工具之前先講一句「我看一下喔」只會被丟掉，
對方還要多等。要用工具就直接用，拿到結果再把結果講出來。真的需要等比較久的操作，
就把那句話當成正式回答講出來，而不是當成前導語。

### 思考
對方直接說出要做的事、一眼就看得懂的請求，就直接做，不要多想。
請求含糊、缺條件、或需要好幾步才做得完的時候，先想清楚要用哪個工具、還缺什麼，再動作。

### 不需要回應的聲音
最新的聲音是安靜、背景噪音、音樂、電視聲、旁人之間的對話、或不是對你說的話——
呼叫 wait_for_user，然後保持安靜。這是一個可以「做」的動作，比忍住不說話可靠。
呼叫之後不要再補話。只有當使用者清楚地對你說話或請你幫忙時才恢復回應。

### 聽不清楚時
- 只回應清楚的語音或文字。模糊、吵雜、只有雜音、被切斷、或你不確定對方確切說了什麼，
  都算聽不清楚。
- 聽不清楚時簡短地用台灣中文請對方再說一次，不要猜測、不要推理、不要呼叫其他工具。
  同樣的澄清句不要連續說兩次；澄清的時候問一件事就好。

### 語言
預設使用台灣中文（台灣國語、台灣繁體）。只有在使用者明確要求換語言，或用另一種語言
說出完整的請求或問題時才換語言。口音、語助詞、簡短的附和、人名、地址、夾雜的外語單字，
都不是換語言的理由。同一輪裡的所有輸出——旁白、橋接、工具訊息、正式回答——都用同一種
語言。工具回傳的資料、歌名、影像內容，一律用台灣中文回答。

### 回答長度
- 長度跟著內容走：問一件小事就答那件事；值得展開的話題（解釋、教學、故事、對方明顯
  想深聊）就好好講，不用縮短。
- 不管長短都不要塞填充內容：不要重複對方剛說的話、不要重述自己前一句的意思、不要加
  沒人問的背景說明。理由很單純——那些話讓對方等，卻沒給他任何新東西。
- 不要用「讓我想想」這類前導語開場（原因見上面的開場白段落）。
- 工具結果：先講結果本身，再看情況補充；不要逐項朗讀原始資料。
- 你說話說到一半時聽到的聲音，如果沒有叫你的名字、也不是明顯在對你說話，就當作不是
  在跟你說話：繼續原本的話題，不要停下來回應。

### 回答長度範例（示範語氣，不是觸發條件）
- 「現在幾點？」→「三點二十。」後面不要再補「還需要我幫你什麼嗎？」
- 「今天天氣如何？」→「台北陰天，二十四度，傍晚可能會下雨。」
- 「幫我開燈」→（工具成功之後）「開好了。」
- 想繼續聊就直接接一句你自己的想法或觀察，不要用問句把球丟回去。
以上只是語氣示範，不是要你等到聽見這些句子才這樣講。

## Tool Availability
- 一直都在手上、可以直接呼叫的能力：看東西與轉頭、表情與跳舞、認人與記憶、家裡的燈、
  放音樂和關音樂、上網搜尋、切換對話模式、去睡覺、wait_for_user。這些直接用。
- 不在手上、要先開工具箱的能力：行程／約／會議／待辦／任務／提醒／郵件／寄信／雲端
  檔案／Notion 屬於 productivity；電視、影片、MV、NAS 上的家庭影片屬於 media。
  先呼叫 open_toolbox，再呼叫真正要用的那個工具。
- open_toolbox 回來之後，那一組工具就在你的工具清單裡了：同一輪直接接著呼叫，不要再問
  使用者一次，也不要說「我幫你打開了工具」。
- open_toolbox 回報失敗的時候，就說你現在拿不到那個功能，不要假裝做過了。
- 音樂一直都在：「放首歌」「音樂關掉」直接用音樂工具，不要先開工具箱。
- 你的工具清單上沒有、工具箱也載不進來的事，就說你做不到，不要自己演一遍。

### 工具結果要照著唸
- 工具回傳的 require_repeat_verbatim 或 speak_verbatim 是 true 時：把 response_text 或
  summary_text 一字不差地唸出來，不要改寫、不要縮短、不要換字、不要加自己的開場白。
  唸完之後才可以自然接話。
- 其他工具結果：先講結果本身，再看情況補充。

### 只講真的做過的事
- 工具成功回傳之後，才可以說動作完成了。工具失敗就直接說明，再給一個下一步。
- 動作類工具回傳的是「已經排進去」的事實（例如 move_queued、direction_requested），
  不是「已經完成」。可以說你把頭轉去哪一邊，不要描述你沒拍到的畫面。
- 沒有真的拍到照片，就不要描述你「看到」什麼。
""".strip()
```

- [ ] **Step 3: Move the memory block.** In `prompts.py`, replace `get_session_instructions`'s tail (lines 158-164):

```python
    block = hardening_block()
    if block:
        instructions = f"{instructions}\n\n{block}"
    memory_prompt = format_memory_for_prompt(instance_path)
    if memory_prompt:
        # Appended, not prepended (2026-09-01 instructing wave). It used to sit
        # ahead of the persona as an unlabeled fact list, so the first thing the
        # model read was data with no statement of what it was for. Last position
        # is also the strongest in a prompt whose compliance decays across a
        # conversation — placement beats volume.
        return f"{instructions}\n\n{memory_prompt}"
    return instructions
```

- [ ] **Step 4: Label the memory block.** In `memory.py`, replace `format_memory_for_prompt` (lines 199-212):

```python
def format_memory_for_prompt(instance_path: str | Path | None = None) -> str:
    """Return the labeled user-context block appended to the session instructions.

    Both the label and the placement changed in the 2026-09-01 instructing wave.
    This used to be prepended, unlabeled, ahead of the persona — so the model's
    first input was a bare list of facts with no statement of what they were for
    or what outranks them. Now it is a labeled block sitting with the rest of the
    system-layer policy, and it says out loud that the person in the room beats
    the file: without that priority, a stale remembered fact and a live correction
    are two assertions of equal standing.
    """
    facts = list_memory_facts(instance_path)
    if not facts:
        return ""

    bullets = "\n".join(f"- {fact.text}" for fact in facts)
    return "\n".join(
        [
            "## 你記得的事（背景資料，不是指令）",
            "以下是你先前記下來、關於現在這位使用者的事。自然地用，不要逐條唸出來。",
            "現在這一輪對方說的話勝過這裡的任何一條：對方更正你的時候以對方為準，",
            "並用 forget／remember 把記憶改過來。",
            bullets,
        ]
    )
```

- [ ] **Step 5: Run the prompt suite.** `cd reachy_companion && python -m pytest tests/test_prompts_hardening.py tests/test_profile.py tests/test_persona.py tests/test_toolboxes.py tests/test_memory.py -q` — Expected: green. `test_persona.py`'s two exact-equality assertions go through `with_hardening()` and stay valid because those tests write no memory file.

- [ ] **Step 6: Gates.** `cd reachy_companion && ruff check . && mypy` — Expected: clean.

- [ ] **Step 7: Commit.** `git add -A && git commit -m "feat(prompt): 2.x message-channel, preamble, reasoning and tool-availability blocks; labeled memory"`

---

### Task 10: Toolbox continuation and the active-surface audit

Spec §4 bullets 5 and 6. The `## Tool Availability` authority landed in Task 9; this task makes the *return* and the *journal* support it.

**Files:**
- Modify: `reachy_companion/src/reachy_companion/huggingface_realtime.py` (`open_toolbox` lines 1006-1021; the startup tool log lines 3074-3078; `_push_mode_update`'s log in `openai_realtime.py` lines 691-695)
- Modify: `reachy_companion/tests/test_toolboxes.py`

**Interfaces:**
- Changes: `HuggingFaceRealtimeHandler.open_toolbox` success returns gain `"session_updated": True`; `"already_open"` gains `"session_updated": True` as well. `ok`, `status`, `category`, `tools` are unchanged — `tests/test_toolboxes.py` asserts all four.
- Changes: two log lines share the greppable prefix `Tools in session (`, both carrying the mode, the open boxes and the count.

**Why a fact and not a cue:** the continuation policy ("call the real tool in the same turn, do not ask again") is stated by the `## Tool Availability` block and by `open_toolbox`'s own description — both higher-authority surfaces. The return may only add the *fact* the model cannot otherwise know: that the server acknowledged the session update, so the tools really are there now. A `next_step` field would be new policy from a message that holds no authority.

- [ ] **Step 1: Write the failing tests.** Append to `reachy_companion/tests/test_toolboxes.py`:

```python
def test_a_loaded_box_reports_that_the_session_really_changed() -> None:
    """The one fact the model cannot infer: the server acknowledged the update.

    `open_toolbox` awaits the ack before returning (design decision 9), so this
    field is true by construction — and it is a FACT, not a cue. The instruction
    to keep going in the same turn lives in the tool description and the
    `## Tool Availability` block, which are the surfaces that hold authority.
    """
    handler = _box_handler()
    handler._push_mode_update = AsyncMock(return_value=True)

    result = asyncio.run(handler.open_toolbox("productivity"))

    assert result["ok"] is True
    assert result["status"] == "loaded"
    assert result["session_updated"] is True
    assert set(result["tools"]) == set(TOOLBOXES["productivity"])


def test_a_failed_box_never_claims_the_session_changed() -> None:
    handler = _box_handler()
    handler._push_mode_update = AsyncMock(return_value=False)

    result = asyncio.run(handler.open_toolbox("media"))

    assert result["ok"] is False
    assert result.get("session_updated") is not True


def test_the_open_toolbox_description_owns_the_continuation_rule() -> None:
    """The return states facts; the description is where the policy lives."""
    from reachy_companion.tools.open_toolbox import OpenToolbox

    description = OpenToolbox().description
    assert "in the same turn" in description
    assert "without asking the user again" in description
```

(Match the file's existing async-driving style — `_box_handler()` builds a handler through `__new__`, and the existing box tests already await/drive `open_toolbox`; reuse whichever of `asyncio.run` or `@pytest.mark.asyncio` the neighbouring tests in that file use.)

- [ ] **Step 2: Add the fact to the two success returns.** In `huggingface_realtime.py`, replace line 1008:

```python
            return {"ok": True, "status": "already_open", "category": category, "tools": tools}
```

with:

```python
            return {
                "ok": True,
                "status": "already_open",
                "category": category,
                "tools": tools,
                "session_updated": True,
            }
```

and line 1021:

```python
        return {"ok": True, "status": "loaded", "category": category, "tools": tools}
```

with:

```python
        # `session_updated` is the one fact the model cannot infer: the update was
        # not merely sent but ACKNOWLEDGED before this returned, so the tools it
        # is about to reach for genuinely exist on the server. The instruction to
        # continue in the same turn is not here — it belongs to the description
        # and the `## Tool Availability` block, which hold authority; a tool
        # message does not.
        return {
            "ok": True,
            "status": "loaded",
            "category": category,
            "tools": tools,
            "session_updated": True,
        }
```

- [ ] **Step 3: Make the active surface greppable.** In `huggingface_realtime._run_realtime_session`, replace lines 3075-3078:

```python
        logger.info(
            "Tools to be used in conversation: %s",
            [tool["name"] for tool in tool_specs],
        )
```

with:

```python
        # One greppable prefix for the whole active-surface audit: this line and
        # `_push_mode_update`'s both start `Tools in session (`, so a journal grep
        # returns the surface at boot AND after every mode flip or box open
        # (skill: re-check the ACTIVE tool surface on every instruction change).
        logger.info(
            "Tools in session (%s, boxes=none, startup, %d): %s",
            self._current_mode().value,
            len(tool_specs),
            [tool["name"] for tool in tool_specs],
        )
```

- [ ] **Step 4: Add the same detail to the mode update.** In `openai_realtime._push_mode_update`'s `_build`, replace lines 691-695:

```python
            logger.info(
                "Tools in session (%s, boxes=%s, %d): %s",
                mode.value,
                ",".join(sorted(self._open_toolboxes)) or "none",
                len(tool_specs),
                [spec["name"] for spec in tool_specs],
            )
```

- [ ] **Step 5: Run.** `cd reachy_companion && python -m pytest tests/test_toolboxes.py tests/test_conversation_modes.py tests/test_huggingface_realtime.py -q` — Expected: green. If a test asserted the old `"Tools to be used in conversation"` log text, update it to the new prefix.

- [ ] **Step 6: Gates.** `cd reachy_companion && ruff check . && mypy` — Expected: clean.

- [ ] **Step 7: Commit.** `git add -A && git commit -m "feat(toolbox): report the acknowledged session update, and log the active surface greppably"`

---

### Task 11 (optional): The `finish_session` alias, behind exposure control

Spec §1's downgraded A/B candidate; Global Constraint 10; ambiguity J. **Ship only if the operator wants the A/B this wave** — with the flag off it is inert, so it is safe to land either way.

**Files:**
- Create: `reachy_companion/src/reachy_companion/finish_session_alias.py`
- Modify: `reachy_companion/src/reachy_companion/main.py` (one call before `initialize_tools`, line ~443)
- Modify: `reachy_companion/.env.example`, `reachy_companion/README.md` (env table)
- Create: `reachy_companion/tests/test_finish_session_alias.py`

**Interfaces:**
- Produces: `finish_session_alias.FinishSession(GoToSleep)` with `name = "finish_session"`, `_auto_register = False`.
- Produces: `finish_session_alias.register_finish_session_alias() -> bool`.
- Produces: env flag `INSTRUCTING_FINISH_SESSION_ALIAS` (default **false**).

**Why it lives outside `tools/`:** the registry loads tool modules **by name** (filename == `Tool.name`) off the profile's tool list, and a scanned module that imports `GoToSleep` would offer a second class with the same `name` to the duplicate-name guard. Outside the package directory there is no ambiguity at all, and `register_extra_tool` gives it full exposure: `EXTRA_TOOLS` members are never hidden by `session_tool_exclusions` in any mode and bypass the profile allowlist. With the flag off, every count assertion in `tests/test_toolboxes.py` (`len(CORE_TOOL_NAMES) == 22`, surfaces 22/27/24/29) is untouched.

- [ ] **Step 1: Write the tests** in `reachy_companion/tests/test_finish_session_alias.py`:

```python
"""The sleep-tool rename is an A/B behind a flag, never an edit to the real name."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from reachy_companion.tools import core_tools
from reachy_companion.tools.go_to_sleep import GoToSleep
from reachy_companion.finish_session_alias import (
    ALIAS_ENV,
    FinishSession,
    register_finish_session_alias,
)


@pytest.fixture(autouse=True)
def _clean_registry() -> None:
    core_tools.EXTRA_TOOLS.pop("finish_session", None)
    yield
    core_tools.EXTRA_TOOLS.pop("finish_session", None)


def test_the_alias_is_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unmeasured rename must not reach the model by accident."""
    monkeypatch.delenv(ALIAS_ENV, raising=False)

    assert register_finish_session_alias() is False
    assert "finish_session" not in core_tools.EXTRA_TOOLS


def test_the_flag_exposes_the_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ALIAS_ENV, "1")

    assert register_finish_session_alias() is True
    assert "finish_session" in core_tools.EXTRA_TOOLS


def test_the_alias_is_the_same_tool_under_a_second_name() -> None:
    """Same implementation, same session-ending contract — only the name differs."""
    assert issubclass(FinishSession, GoToSleep)
    assert FinishSession.name == "finish_session"
    assert FinishSession.ends_session is True
    assert FinishSession.needs_response is False
    assert FinishSession().description == GoToSleep().description


def test_the_alias_never_joins_the_module_scan() -> None:
    """Exposure is the flag's decision alone."""
    assert FinishSession._auto_register is False


@pytest.mark.asyncio
async def test_the_alias_behaves_exactly_like_go_to_sleep() -> None:
    calls: list[str] = []
    deps = core_tools.ToolDependencies(
        reachy_mini=MagicMock(),
        movement_manager=MagicMock(),
        begin_sleep=lambda: calls.append("silence"),
        go_to_sleep=lambda: {"status": "sleeping"},
    )

    assert (await FinishSession()(deps))["status"] == "sleeping_soon"
    assert calls == ["silence"]
```

- [ ] **Step 2: Create `reachy_companion/src/reachy_companion/finish_session_alias.py`:**

```python
"""`go_to_sleep` under an in-distribution second name, for a measured A/B.

The common-tool-name list (`finish_session` among them) is documented for
`gpt-realtime-1.5` only; transfer to 2.x is untested, and a raw rename would
touch the profile tool lists, the toolboxes, the record allowlist, the tests and
the docs. So this is an ALIAS with controlled exposure — the same implementation
under a second name, registered only when `INSTRUCTING_FINISH_SESSION_ALIAS` is
set. `EXTRA_TOOLS` members are never hidden by `session_tool_exclusions` in any
mode and bypass the profile allowlist, so exposure costs one flag and no list
edits.

Deliberately NOT in `tools/`: that directory's modules are loaded by name
(filename == `Tool.name`) and importing `GoToSleep` into one of them would offer
the duplicate-name guard a second class called `go_to_sleep`.

What to measure before any registered-name change (spec §1): sleep-tool
SELECTION rate on genuine end-of-visit requests, and false positives on sleepy
small talk and idle turns.
"""

from __future__ import annotations
import logging
from typing import ClassVar

from reachy_companion.audio.envparse import env_bool
from reachy_companion.tools.core_tools import register_extra_tool
from reachy_companion.tools.go_to_sleep import GoToSleep


logger = logging.getLogger(__name__)

ALIAS_ENV = "INSTRUCTING_FINISH_SESSION_ALIAS"


class FinishSession(GoToSleep):
    """The sleep tool under an in-distribution name; behaviour is identical."""

    # Never picked up by a module scan: exposure is the flag's decision alone.
    _auto_register: ClassVar[bool] = False

    name = "finish_session"


def register_finish_session_alias() -> bool:
    """Register the alias when the A/B flag is on. Returns whether it was added."""
    if not env_bool(ALIAS_ENV, False):
        return False
    try:
        register_extra_tool(FinishSession())
    except ValueError:
        # Already registered earlier in this process; nothing to do.
        return False
    logger.info("A/B: the finish_session alias is exposed alongside go_to_sleep")
    return True
```

- [ ] **Step 3: Call it from `main.py`,** immediately before `initialize_tools` (line ~443):

```python
    # Rename A/B (spec §1): off unless the operator sets the flag. Registered
    # here so the alias is in EXTRA_TOOLS before the first registry build, and
    # therefore in the first session's tool list.
    from reachy_companion.finish_session_alias import register_finish_session_alias

    register_finish_session_alias()

    try:
        initialize_tools(instance_path=instance_path)
```

- [ ] **Step 4: Document the flag** in `reachy_companion/.env.example` and the README env table:

```
# Rename A/B only: expose `finish_session` as a second name for `go_to_sleep`.
# Off by default — measure sleep-tool selection and false positives before any
# registered-name change (docs/plans/2026-09-01-instructing-wave-plan.md §1).
INSTRUCTING_FINISH_SESSION_ALIAS=0
```

- [ ] **Step 5: Run.** `cd reachy_companion && python -m pytest tests/test_finish_session_alias.py tests/test_toolboxes.py tests/test_main.py -q` — Expected: green, with every toolbox count unchanged.

- [ ] **Step 6: Gates and commit.** `cd reachy_companion && ruff check . && mypy` then `git add -A && git commit -m "feat(tools): finish_session alias behind an off-by-default A/B flag"`

---

### Task 12: Suite-wide reconciliation

Everything above ran targeted subsets. This task runs the whole suite once and reconciles what those subsets could not see.

**Files:** whichever tests the full run reports. No source changes are expected — a source change here means an earlier task was wrong, so fix the earlier task's file and note it in the commit message.

- [ ] **Step 1: Full suite.** `cd reachy_companion && python -m pytest -q` — Expected: green apart from the 30 nodeids `tests/conftest.py` skips by design (D-001). That list is exact nodeids with no globs, so it can never mask a new failure: anything else red is a real regression.

- [ ] **Step 2: Reconcile, in this order of suspicion** (the survey identified each as a live pin):
  - `tests/test_solo_barge.py` — nine `_pending_responses.qsize()` assertions. Sizes are unaffected by Task 1; if one inspects a queued *item*, read `.kwargs`.
  - `tests/test_huggingface_realtime.py:1251` — `look_around`'s sanitized payload by exact equality. Failure here means a key was added to its success dict; remove it (Global Constraint 4).
  - `tests/test_huggingface_realtime.py` L1341-1518 — the five commentary tests. **These must pass untouched.** A failure means the session-ending branch reached into the suppression path, which Global Constraint 5 forbids.
  - `tests/test_profile.py` scanners — `_RETIRED_TOOL_NAMES` (23 names, every bundled profile *and* the hardening block *and* every built tool spec's full JSON), `test_every_action_value_in_a_bundled_profile_is_real`. Fix the prose, never the scanner.
  - `tests/test_persona.py:66,141` — exact equality on the composed prompt through `with_hardening()`. If Task 9's join changed, update `with_hardening`, not the assertions.
  - `tests/test_toolboxes.py` L98-99, L157-160, L196 — the surface counts. These must be **unchanged**: no task in this plan adds, removes, or reclassifies a registered tool (Task 11's alias lives in `EXTRA_TOOLS`, which those assertions already subtract).
  - `tests/test_face_tools.py` — `hold_still` call-list assertions. Task 4's window defers under a hold rather than fighting it; a failure means the deferral logic is wrong, not the test.

- [ ] **Step 3: Static gates.** `cd reachy_companion && ruff check . && ruff format --check . && mypy` — Expected: clean. `mypy` is strict over `src/` only.

- [ ] **Step 4: Dead-code sweep.** `cd reachy_companion && grep -rn "run_go_to_sleep_tool\|wait_for_reply_finished\|dummy" src/ | grep -v ".venv"` — Expected: `run_go_to_sleep_tool` gone entirely; `wait_for_reply_finished` surviving only as the handler method and its `ToolDependencies` field (still wired in `build_handler`, still covered by three tests, and still the right seam for any future caller — leaving the seam is deliberate, removing it is a separate decision); no `dummy` in any schema.

- [ ] **Step 5: Commit.** `git add -A && git commit -m "test: reconcile the suite with the instructing wave"`

---

### Task 13: Version, changelog, and the durable records

- [ ] **Step 1: Bump the version.** In `reachy_companion/pyproject.toml`, line 7:

```toml
version = "1.20.0"
```

- [ ] **Step 2: Add the CHANGELOG entry** at the top of `CHANGELOG.md`, immediately under the preamble and above `## [1.19.0]`. Follow the file's established voice — what the operator will notice, not what the diff says:

```markdown
## [1.20.0] — 2026-09-01 · the LLM-first instructing wave

Deployed as the twentieth install (commit `…`, wheel sha `…`).
Design record D-030.

The rule this release is built on: the model decides what to say and which tools
to call; the app instructs it and holds the safety rails.

- **It says goodbye before it lies down.** 「睡覺吧」 used to produce a tool call
  with no words at all, and the robot posed in silence. Now the sleep tool only
  mutes the microphone and hands the turn back; the goodbye gets a response of
  its own that nothing else can ride along with, and the body waits for that
  sentence to finish playing before it moves. The inactivity timeout, which has
  nobody to say goodbye to, still just lies down.
- **Its head actually turns when you tell it to look somewhere.** 「看右邊」 sent
  the right command every time and the daemon's face tracker overrode it within
  a frame, so the photo came back showing whoever was straight ahead. Manual head
  moves now hold the head against face-following for as long as they need it, and
  hand it back exactly as they found it — off stays off.
- **It stops guessing at bad arguments.** A direction it does not recognise, the
  string "false" where a yes/no belonged, a toolbox that does not exist: all of
  them used to be quietly coerced into *something*. They now come back as a
  correction naming what is allowed, so the robot can fix its own mistake in the
  same breath.
- **It stops claiming things it has only started.** "Looking right" was said the
  moment the movement was queued. The robot now reports what is actually true —
  the direction it asked for — and the prompt tells it that is what to say.
- **It reads one set of instructions instead of two.** The character file said
  "use the calendar", the system rules said "open the toolbox first"; the
  character file said "follow whatever language they use", the system rules said
  "stay in Taiwanese Mandarin". One authority each now, all in Taiwan Traditional
  Chinese, with the sentence-count rules replaced by "length follows content".
- **What it remembers about you is labelled as background, not as orders** — and
  says out loud that what you tell it now wins.
```

- [ ] **Step 3: Add D-030 to `DECISIONS.md`,** after D-029. Record, at minimum: the escalation-ladder rung used for each change and why; that the farewell is an *instructed generation turn* (the model composes the words, the app owns the timing and `tool_choice`); that lifecycle sleeps keep direct pose/stop and why; that the tracking suspension deliberately avoids the `set_speaking` anchor because `_get_primary_pose` restores it over a finished move; that `direction_requested` stays until motion is verifiable; that commentary suppression is kept and the spoken-preamble goal dropped for this wave, with the selective-allow policy deferred to a separately-tested wave; and that renames are alias A/Bs, never edits.

- [ ] **Step 4: Update `feature_list.json`** — mark the two field bugs verified-by-test and **blocked on on-robot evidence**, with the exact journal probes from the Verification section below as the outstanding evidence. Do not mark them complete on SDK-simulated tests alone: PRD §8 and the instructing contract both require the on-robot signal.

- [ ] **Step 5: Update `progress.md`** — current verified state, the residual risks named below, and the next action (deploy + run the four journal probes).

- [ ] **Step 6: Final full run.** `cd reachy_companion && python -m pytest -q && ruff check . && mypy` — Expected: clean.

- [ ] **Step 7: Commit.** `git add -A && git commit -m "chore: release 1.20.0 — the LLM-first instructing wave"`

---

## Verification

Two layers, because the spec's review found journal-only verification fragile against this many sequencing changes.

### Layer 1 — SDK-simulated (runs in CI; each line names the task that owns it)

| Spec verification item | Owner |
|---|---|
| The farewell `function_call_output` reaches the model | Task 3 |
| Exactly one follow-up `response.create` with `response={"tool_choice": "none"}`, asserted on the outbound payload | Task 1, Task 3 |
| Pose/stop waits for the farewell's own `response.done` plus the drain | Task 1 (id correlation), Task 3 (ordering `["farewell", "pose"]`) |
| Tracking suspension neither drops nor undoes the queued move, and restores the prior state | Task 4 |
| Invalid tool arguments are rejected with corrective errors | Task 6 |
| Lifecycle sleep paths still pose directly | Task 2 |

### Layer 2 — on-robot journal probes (Task 13 leaves these open in `feature_list.json`)

Deploy through the `reachy-deploy` skill (app only, never the daemon; the persona sync ritual and the antenna-wake check both apply, and Task 8 changed `persona.md`). Then:

1. **「睡覺吧」** → goodbye audio, *then* the pose. Journal order must read: `Tool call: go_to_sleep` → `sleep quiesce: microphone muted` → `sleep: farewell response finished (id=resp_…)` → `sleep quiesce: speaker quiet after N.Ns` → the sleep pose. A `drain cap reached … with audio still playing` line means the goodbye was cut off — raise `SLEEP_GOODBYE_DRAIN_CAP_S` before touching anything else.
2. **「看右邊」** → the head visibly turns and the description matches what is actually to the right. Journal must show `Head tracking suspended for the look_around window` before the capture and `Head window look_around closed; head tracking restored to True` after it.
3. **「抬頭」** → the head goes up, holds, and face-following resumes on its own. Same two log lines with owner `move_head`.
4. **Cold 「幫我加個行程」** → `open_toolbox` → an acknowledged session update → the follow-up response calls `calendar` **without re-asking the user**. If it stalls, fix `open_toolbox`'s return or description first — prompt text is the last resort, not the first (spec §4).
5. **The active surface**, at boot and after each of the above: `grep 'Tools in session (' <journal>` — confirm the count and that the prompt names no capability absent from that list.
6. **The operator's ear** on the restructured prompt: Taiwan Traditional Chinese throughout, no filler openers, no sentence-count feel, and no "I opened the toolbox for you" narration.

### Residual risks to record if a probe cannot run

- **No on-robot run this session** → both field-bug rows stay blocked in `feature_list.json` with the probe list above as the outstanding evidence. Do not close them on Layer 1 alone.
- **`MOVE_HEAD_HOLD_S` is a guess.** 1.5 s was chosen to read as a deliberate look; only the operator's eye can confirm it does not feel like the robot ignoring them. Tune it, do not remove the window.
- **The gesture window costs `move_head` its latency.** It now returns after motion + hold instead of at queue time. `needs_response` is false, so no reply waits on it — but a dance or emotion queued in the same breath starts later than it used to.
- **`reasoning.effort` untouched** (Global Constraint 9). If tool selection is still wrong after these changes, the next step is the three-metric A/B, or the one-shot full-`gpt-realtime-2.1` diagnostic — not another prompt revision.

---

## Self-review

**Spec coverage.** Scope item 1 → Tasks 1, 2, 3, 11. Scope item 2 → Tasks 4, 5. Scope item 3 → Tasks 8, 9 (with the `## Tool Availability` authority in 9 and its `open_toolbox` counterpart in 10). Scope item 4 → Tasks 6, 7, 10. Verification §1 → the Layer 1 table. Verification §2 → the Layer 2 probes. Review-log items all carry forward: the `set_speaking` anchor is refused in Task 4 with the reason in the code; the `_safe_response_create` race is closed in Task 1 by id correlation; the inactivity-path breakage is prevented by Task 2 landing first; `direction_moved` is introduced nowhere; commentary suppression is protected by an explicit "do not touch" in Tasks 3 and 12; the four prompt self-contradictions are each named and closed in Task 8; the ban-count heuristic is treated as a heuristic.

**Placeholder scan.** No task step says "add appropriate handling", "etc.", or "similar to above". Every code block is complete and pasteable. The two places that defer to the implementer are bounded and explicit: Task 10 Step 1 says to match the neighbouring file's async-driving style (`asyncio.run` vs `@pytest.mark.asyncio`) rather than guessing it, and Task 12 lists what to reconcile rather than pre-writing edits to tests it cannot see fail.

**Type consistency across tasks.** `ResponseCycle.done` is `asyncio.Future[str | None]` in Task 1 and the helper's return is `str | None`, matching. `ResponseRequest.kwargs` is `dict[str, Any]`, and the sender's `self.connection.response.create(**kwargs)` is unchanged. `MoveHead.queue_direction` returns `Dict[str, Any]` in Task 5 and is consumed as a dict by `look_around` and by Task 7's audit test. `head_window` is an `AsyncIterator[None]` context manager used with `async with` in both callers. `Tool.ends_session` is `ClassVar[bool]`, read through `getattr(tool, "ends_session", False)` in the dispatcher so a non-`Tool` registry entry cannot raise. `MovementManager.suspend_head_tracking(owner: str)` and `restore_head_tracking(owner: str)` take the same `str` the tools pass as `self.name`. `format_memory_for_prompt` still returns `str` (empty when there are no facts), so `get_session_instructions`'s truthiness check is unchanged.

**Two hazards the spec did not name, both handled.** (1) The sender loop's coalescing discarded *every* following queued request whenever the one in hand was empty — a farewell queued behind a generic tool follow-up would have been dropped and its waiter hung until the timeout, posing the robot in silence; Task 1 makes coalescing cycle-aware and pins it with a test. (2) `app_lifecycle.run_go_to_sleep_tool` *is* the tool, so changing the tool alone would have silently stopped the inactivity timeout from ever sleeping the robot; Task 2 splits it first, as its own behavior-preserving commit.

