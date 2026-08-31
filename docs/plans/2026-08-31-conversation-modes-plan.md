# Conversation Modes + Live-Bug Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Reachy three voice-switchable conversation modes — 多人聊天模式 (`GROUP`, today's party mode, and **the boot default**), 一對一聊天模式 (`ONE_ON_ONE`), 紀錄模式 (`RECORD`, quiet scribe + spoken summary) — put the start-of-turn tool surface on a diet (41 tools → 22 static core, with two on-demand toolboxes), and, in the same wave, fix the three defects the 2026-08-31 on-robot session exposed: "look right" never moves the head, `go_to_sleep` puts the body to sleep while the mouth is still running, and every gated turn still gets a spoken answer queued behind the resumed reply.

**Architecture:** The whole mode system today is one boolean, `HuggingFaceRealtimeHandler._party_mode` (`huggingface_realtime.py:528`), read at ~14 branch sites. This plan makes `self._conversation_mode: ConversationMode` the single source of truth and keeps `_party_mode` as a **read-only property** (`mode is not ONE_ON_ONE`) so the room-vs-solo branch sites and the `__new__`-built test harnesses keep asking their genuinely-binary question unchanged. Mode flips reuse the two proven live-update seams: the narrow `audio.input` `session.update` (`openai_realtime.py:489-516`) and the `instructions=` update shape (`apply_personality`, `huggingface_realtime.py:1553-1563`), merged into one `_push_mode_update()` that also carries `tools`. `create_response=False` becomes unconditional, so the client answers only gate-accepted turns through the existing `_safe_response_create()` → `_response_sender_loop` path — the single change that kills both the answer-to-unaddressed defect and the double-audio pile-up. The tool diet rides that same `_push_mode_update()`: six CRUD/action families collapse into single action-enum **façade** tools that delegate to the untouched originals, three tools are deleted outright, and the productivity/media families become on-demand toolboxes an `open_toolbox(category)` router loads in — the cookbook's Dynamic Conversation Flow pattern, one acknowledged `session.update` per open. `look_around` is a composite tool (move → settle → capture) per the research doc's `[OFFICIAL]` "combine functions that are always called in sequence" pattern. The sleep fix is a reordering — silence the inputs, wait for the reply to finish generating, drain the speaker, then pose — split across `app_lifecycle.begin_sleep_quiesce()` and `app_lifecycle.wait_for_speaker_quiet()` so each half is testable, reusing the boot gate's `is_audible()` poll shape.

**Tech Stack:** Python 3.12, `openai 2.28.0` GA realtime types over websocket, `gpt-realtime-2.1-mini`, pytest + AsyncMock harnesses (`tests/test_solo_barge.py` conventions), ruff + mypy --strict.

**Spec:** `/private/tmp/claude-501/-Users-novalien0424-Reachy-companion/1d26519c-a28a-421e-9ecf-f73141bb330a/scratchpad/plan-design-brief.md` (the orchestrator's binding rulings, folded into this plan). Supporting inputs every implementer should read for their own task: `docs/survey-conversation-modes-plumbing.md` (file:line map cited throughout), `docs/research-mini-tool-calling-2026-08.md` (mini-tier tool-calling evidence), `docs/plans/2026-08-30-name-gate-patience-plan.md` + `DECISIONS.md` D-028 (the barge-machine vocabulary this plan builds on).

---

## Open questions (recorded deviations / ambiguities)

1. **`REALTIME_SOLO_NAME_GATE` is NOT repurposed; the answer gate gets its own variable.** The brief asked to "repurpose [it] as an operator kill switch — when set, ONE_ON_ONE uses the strict name gate (old behavior)". Two facts rule that out. First, the variable's current default is `1` (on) and it governs the **interruption** gate shipped in D-028 ("keep talking through speech aimed at someone else"); flipping that default would silently undo the max-pause semantics and make Reachy interruptible by any 1.6 s cough again. Second — and decisive (orchestrator ruling, 2026-08-31) — the robot's instance `.env` already carries an explicit `REALTIME_SOLO_NAME_GATE=1`, and the deploy ritual **restores `.env` from backup on every install**. Overloading the variable would therefore silently flip 一對一聊天模式 to name-only answering on every single deploy, and "comment the line out at deploy time" would be a permanent recurring foot-gun rather than a one-off chore. **Ruling taken:** `_solo_name_gate()` stays byte-identical (interruption gate, default on). The ONE_ON_ONE answer gate reads a **new, separate** variable `REALTIME_ONE_ON_ONE_ANSWER_GATE` with two values — `open` (default: control phrase OR name OR substantive is answered) and `name_only` (the strict fallback if open answering misbehaves in the field). Reader: `_one_on_one_answer_gate() -> str` (Task 2). No `.env` surgery is owed at deploy time.
2. **Mode lifecycle at session start.** The brief says "Mode resets to ONE_ON_ONE at session start (same lifecycle as party today)", but party today is set at handler `__init__` and deliberately **survives reconnects** (survey §1.2, "A mode manager should preserve that property"). **Ruling taken:** the parenthetical governs — mode is set once in `__init__` and is **not** reset by `_party_reset_for_new_session()`; only turn state is. A dropped websocket mid-meeting must not silently end 紀錄模式.

   **Operator amendment (2026-08-31, post-review): the boot default is `GROUP`, not `ONE_ON_ONE`.** The robot lives in a room with several people in it, and a robot that boots ready to answer every sentence it overhears is the failure the party-mode wave was built to fix. Booting into 多人聊天模式 means a fresh session answers only when addressed by name; 一對一聊天模式 is one sentence away by voice. The boot mode is read from a new mode-valued `REALTIME_DEFAULT_MODE` (`one_on_one`|`group`|`record`, default `group`), with `REALTIME_PARTY_DEFAULT` kept only as a documented legacy alias.
3. **Two verbatim-envelope field names.** The brief specifies `{"summary_text": …, "speak_verbatim": true}` for `summarize_conversation` and `{"response_text": …, "require_repeat_verbatim": true}` for `who_is_this`. The research doc's `[OFFICIAL]` cookbook shape is the second one. **Ruling taken:** both are implemented exactly as the brief specifies, and the hardening block's rendering rule names **both** flags and **both** payload fields in one sentence (Task 11), so the mini model never sees an unnamed envelope.
4. **`task_status` / `task_cancel` are not folded into the `tasks` family.** The brief allows either ("if they share the family cleanly, else leave them"). They are `SystemTool` enum values (`tools/tool_constants.py`) that `_read_profile_tool_names` injects into **every** profile (`tools/core_tools.py:376`) and that the `BackgroundToolManager` needs to report and cancel any long-running call — including a call made through a family façade. **Ruling taken:** left separate, and added to the static core so no toolbox swap can remove them.
5. **Static-core count is 22, not the brief's "~17-19".** The brief's own core list has 19 entries; `task_status` and `task_cancel` are structural (Open question 4), and `music` joins them as the always-on stop lane (Codex round 1, P2-7 — `stop_music` must be reachable with no prerequisites and no toolbox to open first). That is 22 model-facing entries at the start of a turn — still down from 41, and still under the point where the `[RESEARCH]` selection curve collapses. **Ruling taken:** ship 22 and record the number honestly rather than deleting a working tool, or boxing a safety lane, to hit a round figure.

---

## Global Constraints

- Model stays `gpt-realtime-2.1-mini` (operator ruling, cost). Every fix here is prompt-, schema- or client-side. No model swap, no `reasoning.effort` change.
- No new or upgraded dependencies. Reuse-first: adapt the existing seams named in `docs/survey-conversation-modes-plumbing.md` — `_gate_text_accepts` / `_party_names()` / `_PARTY_CONTROL_RE` stay the single shared address vocabulary (they also seed the transcriber keyword bias, `openai_realtime.py:166` — do **not** fork them), `_safe_response_create()` stays the only way a response is created, `get_tool_specs(exclusion_list=…)` is the tool-scoping seam, `audio_drain.is_audible()` is the drain predicate.
- **No numeric length caps or keyword lists in prompts** (operator ruling, and the recorded user memory). Concrete few-shot examples are allowed and are the mechanism this wave uses. If brevity still fails after this wave, numeric caps need a fresh operator decision — note it, do not implement it.
- Never touch the robot daemon; app-only changes.
- Tests first where practical. The suite must stay green: `python -m pytest` from `reachy_companion/` (baseline **1571 passed / 30 skipped**; on this Mac the interpreter is `.venv/bin/python`), plus `ruff check .` and `mypy --strict src` clean. Every task ends green and ends in a commit.
- Secrets stay in the gitignored `.env`; never in tracked files.
- Chinese is the primary conversation language: every new spoken string, tool description trigger phrase and prompt rule covers zh + en.
- Control phrases (停/閉嘴/stop/…) beat every gate in every mode — "a robot you cannot silence is worse than any false positive" (`huggingface_realtime.py:93-94`). Binding at every decision point added here.
- `REALTIME_SOLO_CLIENT_BARGE=0` keeps restoring the pre-Task-8 *interruption* path. It does **not** restore server auto-answering: `create_response=false` is unconditional from Task 2 on, and the client answers in every mode.
- **The tool consolidation is a schema refactor, not a behavior change.** Every family façade delegates to the original `Tool` instance unchanged, so every spoken-confirmation gate (`hanova.confirm`), every `settings.tool_status(self.name)` prerequisite check, every error string and every existing test of those modules keeps exercising the shipped code path. If a family tool has to reimplement any part of a sub-tool's body, the refactor has gone wrong — stop and re-delegate.
- Code and tests live under `reachy_companion/` (tests mirror `src/`); `reachy_companion/.env.example` and `reachy_companion/profiles/` count as inside. **Task 12 alone** touches repo-root files, and exactly these five: `README.md`, `DECISIONS.md`, `feature_list.json`, `progress.md`, `CHANGELOG.md`. Nothing else outside the package, in any task.
- Branch: `conversation-modes` off `main`.

---

## Design decisions (argued once, binding below)

1. **`_party_mode` survives as a read-only property, not as state.** `_conversation_mode` is the single writable source of truth. The ~14 sites that branch on `_party_mode` (`:1073, :1099, :1118, :1264, :2402, :2422, :2597, :2619, :2629, :2653, :2662, :825`, `openai_realtime.py:416`) ask a binary question — "room policy or one-on-one policy?" — and RECORD wants the room policy at every single one of them (debounced barge-in instead of pause-then-decide, no solo speech tracking, gate at `transcription.completed`). Rewriting them into three-way mode checks would be a large diff that changes nothing. Sites whose behavior genuinely differs per mode (answer gate, prompts, tools, record log, follow-up window) read `self._conversation_mode` explicitly.
2. **Interruption gate and answer gate are separate concerns.** The name gate (D-028) decides what may *cut off* a playing reply and is unchanged. The new answer gate decides which committed turns get a response. Conflating them is what produced RCA #3.
3. **`create_response=false` everywhere.** Party mode has proven for a week that client-driven `response.create` latency is acceptable, and it is the only way a rolled-back turn can produce no answer at all.
4. **RECORD's log is deliberately not `session_transcript`.** `deps.session_transcript` is `maxlen=40`, accepted-turns-only, and feeds the D-027 sleep summary; it stays untouched. `deps.record_log` is `maxlen=2000` and captures every final transcript, user and assistant, denied and accepted. In memory only, and scoped to the visit: **cleared on RECORD exit and on the sleep that ends the visit; preserved across reconnects and across settings/backend restarts**, which reach `shutdown()` mid-visit and must not throw away a meeting still in progress (Codex round 1, P1-5). No files, no export (PRD non-goal: long-term memory).
5. **`look_around` is a composite, not a prompted chain.** `[COMMUNITY]` evidence in the research doc says sequential chaining is exactly where the 2.1-mini tier breaks; `[OFFICIAL]` guidance says combine functions always called in sequence. The composite also carries `direction_requested` back — what the tool can actually attest, given a motion API with no completed-move signal (Codex round 1, P2-2) — which is still far better ground truth for "I turned right" than the fabrication observed on-robot, and it is paired with a description rule to describe the returned picture rather than assert a completed motion.
6. **The sleep quiesce never flushes.** `on_external_interrupt()` disarms the barge machine and drops *held* audio; `clear_audio_queue()` would additionally kill the goodbye already in the player queue — which is the audio the whole quiesce exists to protect.
7. **Family tools are façades over the existing tools, not rewrites.** `calendar`, `tasks`, `drive`, `nas`, `music` and `tv` each own a schema and a dispatch table; the 18 original modules stay on disk, keep their names, keep their `settings.tool_status(self.name)` prerequisite rows, and keep their tests. They simply leave the profile's `default_tools`, so the registry loader never imports them for registration (`_tool_classes_from_module` only picks up classes *defined* in the module it was asked for, `tools/core_tools.py:256-274` — a family module importing them registers nothing extra). The alternative, merging 18 bodies into 6, would put the confirmation gates and the Google/NAS/HA error handling through a rewrite for zero model-facing gain.
8. **Toolboxes accumulate within a mode and close together at its edges** (revised in Codex round 2, 2b-3 — the earlier "swap, never accumulate" wording contradicted the code and the tests, which have always allowed both boxes open). `open_toolbox` is a router, and the whole state it owns is a `set[str]` of open box names on the handler. A second `open_toolbox` **adds** to that set rather than replacing it, deliberately: a turn that starts 「幫我加個行程」 and continues 「順便在電視上放那個」 must not lose the calendar tools halfway through, and closing a box the model has already been told about is how you get a call to a tool that is no longer there. So the surface at the start of a turn is 22, 27 with productivity open, 24 with media open, and **29 with both** — still well under the 41 this wave is fixing, and only reachable by a conversation that actually asked for both. Everything closes at the mode's edges: a mode switch, `go_to_sleep`, and session start. No idle timers this wave, because a timer that closes a box mid-sentence is a new failure mode for the exact model tier we are trying to stop confusing.

9. **One ordered, acknowledged, single-flight session-update mechanism, shared by every live `session.update` caller** (Codex round 1, P1-1/P1-3/P1-4/P2-9 — one defect family, one fix; tightened in Codex round 2, 2a-1/2a-2). **Every** caller that updates the live session — `_push_mode_update`, `_push_turn_detection_update`, `change_voice` (`huggingface_realtime.py:1516-1525`) and `apply_personality` (`:1553-1563`) — goes through `_apply_session_update()`. That is what makes the uncorrelated acknowledgement safe: `session.updated` does not echo the client `event_id`, so resolving "the update in flight" is only sound when **at most one can be in flight**, which the shared lock guarantees. A caller that sent its own `session.update` around the mechanism would have its `session.updated` resolve somebody else's waiter. `_apply_session_update()`:
   - **serializes** on an `asyncio.Lock` held across the WHOLE operation — ticket check, payload build, waiter install, send, and acknowledgement wait — in one uninterrupted region. Releasing between the snapshot and the send would defeat the ticket entirely (Codex round 2, 2a-1), so the caller hands in a **payload builder**, not a payload, and the builder runs inside the lock;
   - **coalesces** on a monotonic `_mode_update_seq`, so a stale snapshot queued behind a newer flip is dropped rather than sent — the builder returns `None` and the send is skipped;
   - **builds the payload inside the lock** from live `_conversation_mode` / `_open_toolboxes`, so whatever is sent is by construction the current state;
   - **correlates the acknowledgement**: it stamps a client `event_id`, then waits (bounded) for `session.updated` or for an `error` carrying that same `event_id`. `await connection.session.update(...)` returns when the event has been *sent*, not when the server has applied it — without the wait, the follow-up `response.create` can run against the old instructions and the old tool list, which is exactly the bug this whole wave is trying to stop;
   - **keeps mode-update errors out of the response-create synchronization path**: an `error` matching the in-flight update resolves the update's waiter and returns, instead of setting `_response_started_or_rejected_event` and falsely waking `_response_sender_loop`;
   - **returns a bool**, so callers can roll back. `open_toolbox` removes the box it optimistically added and reports failure; `set_conversation_mode` keeps the local mode (the gate, the barge policy and the turn detection are enforced client-side regardless) and logs loudly.

   Both `set_conversation_mode` and `open_toolbox` are therefore **async and await the update before returning their tool result**, so the model never continues against a session the server has not applied yet.

   **Unmatched acknowledgements are counted, never allowed to resolve a waiter** (Codex round 3, findings 5 and 6). `session.updated` does not echo the client `event_id`, so positional matching is only sound once every acknowledgement the server already owes us has been accounted for. Three updates are sent with nobody waiting: the *initial* `conn.session.update(session=session_config)` in `_run_realtime_session` (`:2327`, plus its legacy-transcription retry at `:2334`), which runs before `self.connection` is published and before the receive loop exists; any push made before the loop starts (the no-greeting boot-gate release, finding 1); and any update whose own ack wait timed out — late, not absent. Each books one unit of `_session_update_ack_debt`, and `_note_session_updated` pays that debt **before** it will resolve a live waiter. Without it, a mode flip would be told its instructions and tool list had been applied on the strength of the connect config's acknowledgement, which is precisely the false positive this whole design exists to prevent.

---

### Task 1: `ConversationMode` enum, handler state, and the voice switch

**Files:**
- Create: `reachy_companion/src/reachy_companion/conversation_mode.py`
- Create: `reachy_companion/src/reachy_companion/tools/set_conversation_mode.py`
- Delete: `reachy_companion/src/reachy_companion/tools/party_mode.py`
- Modify: `reachy_companion/src/reachy_companion/huggingface_realtime.py` (`__init__` `:527-528`, `set_party_mode` `:606-652`)
- Modify: `reachy_companion/src/reachy_companion/tools/core_tools.py` (`ToolDependencies` `:53-56`)
- Modify: `reachy_companion/src/reachy_companion/tools/go_to_sleep.py` (description `:15-20`)
- Modify: `reachy_companion/src/reachy_companion/main.py` (`:266-269`)
- Modify: `reachy_companion/profiles/_reachy_companion_locked_profile/profile.md` (`default_tools`)
- Create test: `reachy_companion/tests/test_conversation_modes.py`
- Modify test: `reachy_companion/tests/test_party_mode.py`

**Interfaces:**
- Produces (module `reachy_companion.conversation_mode`):
  - `class ConversationMode(str, Enum)` with members `ONE_ON_ONE = "one_on_one"`, `GROUP = "group"`, `RECORD = "record"`.
  - `DEFAULT_MODE: Final[ConversationMode] = ConversationMode.GROUP` — **the boot default** (operator amendment, 2026-08-31).
  - `MODE_VALUES: Final[tuple[str, ...]] = ("one_on_one", "group", "record")` (declaration order; the default is `DEFAULT_MODE`, not the first member)
  - `MODE_LABELS: Final[dict[ConversationMode, str]]` → `{ONE_ON_ONE: "一對一聊天模式", GROUP: "多人聊天模式", RECORD: "紀錄模式"}`
  - `parse_mode(value: str) -> ConversationMode | None`
- Produces (handler): `self._conversation_mode: ConversationMode`; `self._turn_mode: ConversationMode` (fallback stamp for a turn whose event carried no item id) and `self._turn_modes: dict[str, ConversationMode]` (bounded, item-id keyed — Task 2 reads both); read-only property `_party_mode -> bool`; **`async def set_conversation_mode(self, mode: str | ConversationMode) -> dict[str, Any]`** returning `{"ok": True, "status": "unchanged"|"mode_set", "mode": <value>, "label": <label>}`, or — when a newer flip landed while this one awaited its acknowledgement — `{"ok": True, "status": "superseded", "mode": <ACTUAL current value>, "label": <label>, "requested": <value>}`, or `{"ok": False, "error": str, "modes": list[str]}`. **`mode` is always the mode the handler is actually in when the call returns** (Codex round 3, finding 4): the model reads this result and says it out loud, so it must never name a mode that lost a race.
- Produces (deps seam): `ToolDependencies.set_conversation_mode: Callable[[str], Awaitable[dict[str, Any]]] | None = None` (import `Awaitable` from `collections.abc`). **`ToolDependencies.set_party_mode` is removed.**
- Produces (tool): `reachy_companion.tools.set_conversation_mode.SetConversationMode`, `name = "set_conversation_mode"`, one required string arg `mode`; its `__call__` **awaits** the seam.
- Consumes: `time.monotonic`, `self._push_turn_detection_update()` (existing, `huggingface_realtime.py:669`), `self._resume_playback(rolled_back=True)` (existing, `:865`).
- **Why async** (Codex round 1, P1-1): the model reads this tool's result and immediately speaks its confirmation sentence. If the session update is only *scheduled*, that confirmation runs against the previous mode's instructions and tool list. Task 3 replaces the awaited `_push_turn_detection_update()` with the full `_push_mode_update()`; the await itself is established here so the signature never changes again.
- **Removed for later tasks:** `HuggingFaceRealtimeHandler.set_party_mode`, `tools.party_mode.PartyMode`, `deps.set_party_mode`. Nothing in Tasks 2–12 may reference them.

- [ ] **Step 1: Write the failing tests** — new file `reachy_companion/tests/test_conversation_modes.py`:

```python
"""Conversation modes: the enum, the handler seam, and the voice switch.

Replaces the `_party_mode` boolean that used to be the whole mode system
(2026-08-24 party mode). Party-specific *behavior* still lives in
`tests/test_party_mode.py`; this file owns the three-mode vocabulary.
"""

import asyncio
from types import SimpleNamespace
from collections import deque
from unittest.mock import MagicMock

import pytest

# `tests/` has no __init__.py, so pytest's prepend import mode puts the
# directory itself on sys.path — import the sibling harness by bare name.
from test_solo_barge import _install_barge_state

from reachy_companion.conversation_mode import (
    DEFAULT_MODE,
    MODE_LABELS,
    MODE_VALUES,
    ConversationMode,
    parse_mode,
)
from reachy_companion.openai_realtime import OpenAIRealtimeHandler
from reachy_companion.huggingface_realtime import HuggingFaceRealtimeHandler
from reachy_companion.tools.set_conversation_mode import SetConversationMode


def _mode_handler(mode: ConversationMode = ConversationMode.ONE_ON_ONE) -> OpenAIRealtimeHandler:
    """A `__new__`-built handler carrying only mode + barge state."""
    h = OpenAIRealtimeHandler.__new__(OpenAIRealtimeHandler)
    h._conversation_mode = mode
    # The mode the utterance in flight began in (Task 2 reads it; stamped at
    # `speech_started`). A `__new__`-built handler starts them equal.
    h._turn_mode = mode
    h._turn_modes = {}
    h._mode_update_seq = 0
    h._session_update_lock = asyncio.Lock()
    h._session_update_event_id = None
    h._session_update_waiter = None
    h._session_update_ack_debt = 0
    # Default to "the loop is running", so an update waits for its ack; the
    # pre-receive-loop tests set this back to False explicitly.
    h._receive_loop_active = True
    h._handler_loop = None
    h._party_last_accept_at = None
    h._party_speech_open = False
    h._party_utterance_seq = 0
    h._party_barge_task = None
    h._active_response_id = None
    h._cancelled_response_ids = deque(maxlen=8)
    h._response_done_event = asyncio.Event()
    h._response_done_event.set()
    h.connection = None
    h.deps = SimpleNamespace(reachy_mini=MagicMock(), movement_manager=MagicMock())
    _install_barge_state(h)
    h._clear_queue = MagicMock()
    return h


def test_parse_mode_accepts_values_and_legacy_aliases() -> None:
    """`party`/`solo` are the words this codebase used until today."""
    assert parse_mode("one_on_one") is ConversationMode.ONE_ON_ONE
    assert parse_mode("one-on-one") is ConversationMode.ONE_ON_ONE
    assert parse_mode("GROUP") is ConversationMode.GROUP
    assert parse_mode("party") is ConversationMode.GROUP
    assert parse_mode("solo") is ConversationMode.ONE_ON_ONE
    assert parse_mode("record") is ConversationMode.RECORD
    assert parse_mode("紀錄") is None
    assert MODE_VALUES == ("one_on_one", "group", "record")
    assert MODE_LABELS[ConversationMode.RECORD] == "紀錄模式"


def test_the_boot_default_is_the_room_posture() -> None:
    """Operator amendment 2026-08-31: a fresh handler starts in 多人聊天模式.

    The robot sits in a room with several people in it. Booting ready to answer
    every overheard sentence is the failure party mode was built to fix, so a
    fresh session answers only when addressed by name.
    """
    assert DEFAULT_MODE is ConversationMode.GROUP


def test_the_boot_mode_env_selects_a_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    from reachy_companion.huggingface_realtime import _boot_conversation_mode

    monkeypatch.delenv("REALTIME_DEFAULT_MODE", raising=False)
    assert _boot_conversation_mode() is ConversationMode.GROUP
    monkeypatch.setenv("REALTIME_DEFAULT_MODE", "one_on_one")
    assert _boot_conversation_mode() is ConversationMode.ONE_ON_ONE
    monkeypatch.setenv("REALTIME_DEFAULT_MODE", "RECORD")
    assert _boot_conversation_mode() is ConversationMode.RECORD
    # Legacy alias, same parser.
    monkeypatch.setenv("REALTIME_DEFAULT_MODE", "party")
    assert _boot_conversation_mode() is ConversationMode.GROUP


def test_a_malformed_boot_mode_degrades_to_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every mode knob degrades with a warning, never raises."""
    from reachy_companion.huggingface_realtime import _boot_conversation_mode

    monkeypatch.setenv("REALTIME_DEFAULT_MODE", "karaoke")
    assert _boot_conversation_mode() is ConversationMode.GROUP
    monkeypatch.setenv("REALTIME_DEFAULT_MODE", "   ")
    assert _boot_conversation_mode() is ConversationMode.GROUP


def test_booting_into_record_warns(monkeypatch: pytest.MonkeyPatch, caplog) -> None:
    """A robot that boots silent looks exactly like a robot that failed to start."""
    import logging

    from reachy_companion.huggingface_realtime import _boot_conversation_mode

    monkeypatch.setenv("REALTIME_DEFAULT_MODE", "record")
    with caplog.at_level(logging.WARNING, logger="reachy_companion.huggingface_realtime"):
        assert _boot_conversation_mode() is ConversationMode.RECORD
    assert "boot silent" in caplog.text


def test_party_mode_property_tracks_the_room_modes() -> None:
    """The dozen room-vs-solo branch sites keep reading one boolean."""
    assert _mode_handler(ConversationMode.ONE_ON_ONE)._party_mode is False
    assert _mode_handler(ConversationMode.GROUP)._party_mode is True
    assert _mode_handler(ConversationMode.RECORD)._party_mode is True


def test_party_mode_property_is_read_only() -> None:
    """`_conversation_mode` is the only writable source of truth."""
    h = _mode_handler()
    with pytest.raises(AttributeError):
        h._party_mode = True


@pytest.mark.asyncio
async def test_set_conversation_mode_flips_and_reports() -> None:
    h = _mode_handler()
    result = await h.set_conversation_mode("group")
    assert result == {
        "ok": True,
        "status": "mode_set",
        "mode": "group",
        "label": "多人聊天模式",
    }
    assert h._conversation_mode is ConversationMode.GROUP
    # Whoever toggled the mode is engaged: entering GROUP opens the window.
    assert h._party_last_accept_at is not None
    assert (await h.set_conversation_mode(ConversationMode.GROUP))["status"] == "unchanged"


@pytest.mark.asyncio
async def test_a_superseded_flip_reports_the_mode_it_actually_ended_in() -> None:
    """The model says this result out loud; it must not name a dead mode.

    A second flip can land while the first is awaiting its acknowledgement
    (Codex round 3, finding 4).
    """
    h = _mode_handler()
    flipped: list[str] = []

    async def _push() -> bool:
        # A concurrent switch wins while this one is in flight.
        h._conversation_mode = ConversationMode.GROUP
        flipped.append("raced")
        return True

    h.connection = SimpleNamespace()
    # Task 1 awaits `_push_turn_detection_update`; Task 3 swaps the name to
    # `_push_mode_update`. Patch whichever this task has already introduced.
    h._push_mode_update = _push  # type: ignore[method-assign]
    h._push_turn_detection_update = _push  # type: ignore[method-assign]
    result = await h.set_conversation_mode("record")
    assert flipped == ["raced"]
    assert result["status"] == "superseded"
    assert result["mode"] == "group"
    assert result["label"] == "多人聊天模式"
    assert result["requested"] == "record"


@pytest.mark.asyncio
async def test_set_conversation_mode_rejects_an_unknown_mode() -> None:
    h = _mode_handler()
    result = await h.set_conversation_mode("karaoke")
    assert result["ok"] is False
    assert "karaoke" in result["error"]
    assert result["modes"] == ["one_on_one", "group", "record"]
    assert h._conversation_mode is ConversationMode.ONE_ON_ONE


@pytest.mark.asyncio
async def test_record_mode_opens_no_followup_window() -> None:
    """Quiet-scribe posture: every command needs the name, no free follow-ups."""
    h = _mode_handler()
    await h.set_conversation_mode("record")
    assert h._conversation_mode is ConversationMode.RECORD
    assert h._party_last_accept_at is None


@pytest.mark.asyncio
async def test_mode_flip_resolves_a_live_solo_pause() -> None:
    """The flip removes every timer that could resolve the pause; roll it back."""
    h = _mode_handler()
    h._barge_paused = True
    h._barge_pending = True
    h._barge_paused_response_id = "resp_1"
    seq = h._party_utterance_seq
    await h.set_conversation_mode("group")
    assert not h._barge_paused and not h._barge_pending
    assert h._barge_resumed_response_id is None
    assert h._party_utterance_seq == seq + 1


def test_mode_state_default_exists_on_the_base_handler() -> None:
    """The real __init__ must define the field the loop and tests touch."""
    import inspect

    source = inspect.getsource(HuggingFaceRealtimeHandler.__init__)
    for field in ("_conversation_mode", "_turn_mode", "_turn_modes", "_mode_update_seq"):
        assert field in source, field
    # The boot mode comes from the reader, not from a literal (operator
    # amendment 2026-08-31), so there is exactly one place to change it.
    assert "_boot_conversation_mode()" in source


def test_a_real_handler_boots_into_group(monkeypatch: pytest.MonkeyPatch) -> None:
    """End to end: __init__ with no env set lands in 多人聊天模式."""
    from unittest.mock import MagicMock

    from reachy_companion.tools.core_tools import ToolDependencies

    monkeypatch.delenv("REALTIME_DEFAULT_MODE", raising=False)
    monkeypatch.delenv("REALTIME_PARTY_DEFAULT", raising=False)
    handler = HuggingFaceRealtimeHandler(
        ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())
    )
    assert handler._conversation_mode is ConversationMode.GROUP
    assert handler._party_mode is True
    assert handler._turn_mode is ConversationMode.GROUP


@pytest.mark.asyncio
async def test_tool_refuses_when_the_seam_is_unwired() -> None:
    deps = SimpleNamespace(set_conversation_mode=None)
    result = await SetConversationMode()(deps, mode="group")
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_tool_awaits_the_seam_before_returning() -> None:
    """The model speaks its confirmation next; the update must already be applied."""
    seen: list[str] = []

    async def _seam(mode: str) -> dict[str, object]:
        seen.append(mode)
        return {"ok": True, "status": "mode_set", "mode": mode, "label": "紀錄模式"}

    deps = SimpleNamespace(set_conversation_mode=_seam)
    result = await SetConversationMode()(deps, mode="record")
    assert seen == ["record"]
    assert result["mode"] == "record"


@pytest.mark.asyncio
async def test_tool_rejects_a_non_string_mode() -> None:
    async def _seam(mode: str) -> dict[str, object]:
        return {"ok": True}

    deps = SimpleNamespace(set_conversation_mode=_seam)
    result = await SetConversationMode()(deps, mode=3)
    assert result["ok"] is False


def test_tool_schema_enumerates_every_mode() -> None:
    schema = SetConversationMode().parameters_schema
    assert schema["properties"]["mode"]["enum"] == ["one_on_one", "group", "record"]
    assert schema["required"] == ["mode"]


def test_tool_description_carries_the_chinese_switch_phrases() -> None:
    """Literal-interpretation trap (research §C7): enumerate real phrasings."""
    description = SetConversationMode.description
    for phrase in ("一對一聊天模式", "多人聊天模式", "紀錄模式", "go_to_sleep", "Do NOT use when"):
        assert phrase in description
```

Also update `reachy_companion/tests/test_party_mode.py` in this step:
- `_party_handler()` (`:24-28`): replace `h._party_mode = True` with `h._conversation_mode = ConversationMode.GROUP`, and add `from reachy_companion.conversation_mode import ConversationMode` to the imports.
- Add `"REALTIME_DEFAULT_MODE"` to the `_clean_party_env` fixture's delenv list (`:54-61`), beside `REALTIME_PARTY_DEFAULT` — the boot mode is now env-selectable, so a developer's shell must not decide it.
- Delete the import `from reachy_companion.tools.party_mode import PartyMode` and the five tests that used it or `set_party_mode`: `test_set_party_mode_opens_the_followup_window_on_enable` (`:236`), `test_set_party_mode_off_clears_the_window_and_invalidates_timers` (`:247`), `test_set_party_mode_is_idempotent` (`:259`), `test_party_mode_tool_flips_through_the_deps_seam` (`:471`), `test_party_mode_tool_reports_a_missing_seam` (`:480`). Their replacements live in `test_conversation_modes.py` above.
- `test_party_state_defaults_exist_on_the_base_handler` (`:488`): replace the `"_party_mode"` entry of its field tuple with `"_conversation_mode"`.
- Any remaining `h._party_mode = False` in this file becomes `h._conversation_mode = ConversationMode.ONE_ON_ONE`.

And in `reachy_companion/tests/test_solo_barge.py`, `_solo_handler()` (`:74`): replace `h._party_mode = False` with `h._conversation_mode = ConversationMode.ONE_ON_ONE`, importing `ConversationMode` from `reachy_companion.conversation_mode`. Add `"REALTIME_DEFAULT_MODE"` to the `_clean_barge_env` fixture's delenv list (`:104-116`) for the same reason.

- [ ] **Step 2: Run tests to verify they fail**

Run (from `reachy_companion/`): `python -m pytest tests/test_conversation_modes.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'reachy_companion.conversation_mode'`

- [ ] **Step 3: Implement**

New file `reachy_companion/src/reachy_companion/conversation_mode.py`:

```python
"""The three conversation modes (2026-08-31 plan).

One boolean — `HuggingFaceRealtimeHandler._party_mode` — used to be the whole
mode system. It answered a single question ("a room, or one person?") and had
no room for a third posture. This module is the shared vocabulary the handler,
the prompts, the tools and the record log all read.

A leaf module on purpose: `tools/set_conversation_mode.py` imports it, and a
tool module must not import `huggingface_realtime` (that module imports
`tools.core_tools`, so the edge would close a cycle).
"""

from __future__ import annotations
from enum import Enum
from typing import Final


class ConversationMode(str, Enum):
    """How Reachy participates while it STAYS awake (never about sleeping)."""

    ONE_ON_ONE = "one_on_one"
    GROUP = "group"
    RECORD = "record"


# The mode a fresh handler boots into (operator amendment, 2026-08-31). The
# robot lives in a room with several people in it, so the safe posture at boot
# is the one that answers only when addressed: a robot that wakes up ready to
# reply to every overheard sentence is the failure party mode was built to fix.
# 一對一聊天模式 is one spoken sentence away.
DEFAULT_MODE: Final[ConversationMode] = ConversationMode.GROUP

# Declaration order, used for the tool schema's enum. The boot default is
# `DEFAULT_MODE` above, not the first entry here.
MODE_VALUES: Final[tuple[str, ...]] = tuple(mode.value for mode in ConversationMode)

# Spoken labels, so a log line, a tool result and the model's confirmation
# sentence all name the mode the way the operator does.
MODE_LABELS: Final[dict[ConversationMode, str]] = {
    ConversationMode.ONE_ON_ONE: "一對一聊天模式",
    ConversationMode.GROUP: "多人聊天模式",
    ConversationMode.RECORD: "紀錄模式",
}

# The tool schema is not the only caller: an operator `.env`, a JSON-RPC call
# and the model's own argument all reach `parse_mode`, and `party`/`solo` are
# the words this codebase used until today.
_ALIASES: Final[dict[str, ConversationMode]] = {
    "one_on_one": ConversationMode.ONE_ON_ONE,
    "one-on-one": ConversationMode.ONE_ON_ONE,
    "solo": ConversationMode.ONE_ON_ONE,
    "group": ConversationMode.GROUP,
    "party": ConversationMode.GROUP,
    "record": ConversationMode.RECORD,
}


def parse_mode(value: str) -> ConversationMode | None:
    """Return the mode named by *value*, or None when it names nothing."""
    return _ALIASES.get(value.strip().lower().replace(" ", "_").replace("-", "_"))
```

(`"one-on-one"` still resolves: the `-`→`_` normalization happens before the lookup, and the explicit alias entry is harmless redundancy.)

In `huggingface_realtime.py`, add the import next to the other package imports:

```python
from reachy_companion.conversation_mode import (
    DEFAULT_MODE,
    MODE_LABELS,
    MODE_VALUES,
    ConversationMode,
    parse_mode,
)
```

Replace the `__init__` party-state line (`:527-528`) with:

```python
        # --- conversation modes (2026-08-31 plan) ----------------------------
        # The single source of truth. Set once, here: the mode deliberately
        # SURVIVES a reconnect (survey §1.2), because a dropped websocket
        # mid-meeting must not silently end 紀錄模式. Only turn state resets per
        # session.
        self._conversation_mode: ConversationMode = _boot_conversation_mode()
        # The mode the utterance currently in flight BEGAN in, stamped at
        # `speech_started` (Task 2). A flip must not retroactively reclassify a
        # turn that is already half-spoken: ambient speech started in 多人聊天
        # 模式 must not become answerable because someone flipped to 一對一
        # mid-sentence, and vice versa (Codex round 1, P1-2).
        self._turn_mode: ConversationMode = self._conversation_mode
        # Per-input-item stamps, because a single field is overwritten by the
        # next `speech_started` before a slow `transcription.completed` for the
        # PREVIOUS turn arrives (Codex round 2, 2a-4). Keyed by the
        # `input_audio_buffer.speech_started` event's `item_id`; popped when
        # that item's transcript completes or fails; cleared per session. The
        # bound is small because entries only survive until their own transcript
        # lands, and a dropped stamp falls back to `_turn_mode`.
        self._turn_modes: dict[str, ConversationMode] = {}
        # Monotonic coalescing token for session updates (Task 3, decision 9):
        # a snapshot queued behind a newer flip is dropped rather than sent.
        self._mode_update_seq: int = 0
```

Add the boot-mode reader at module level, replacing `_party_default_on()` (`:98-99`) — that function has no other caller once this lands, so delete it:

```python
def _boot_conversation_mode() -> ConversationMode:
    """The mode a fresh handler starts in (operator amendment, 2026-08-31).

    `GROUP` by default, deliberately. The robot sits in a room with several
    people in it, and a robot that boots ready to answer every overheard
    sentence is the exact failure the party-mode wave was built to fix — so a
    fresh session answers only when addressed by name, and 一對一聊天模式 is
    one spoken sentence away.

    `REALTIME_PARTY_DEFAULT` is honoured as a legacy alias so an instance `.env`
    carrying it keeps working; it can only ever select `GROUP`, which is now the
    default anyway, so in practice it is a no-op kept for compatibility.

    Degrades with a warning rather than raising, like every other mode knob.
    """
    raw = (os.getenv("REALTIME_DEFAULT_MODE") or "").strip()
    if not raw:
        return DEFAULT_MODE
    mode = parse_mode(raw)
    if mode is None:
        logger.warning("Ignoring invalid REALTIME_DEFAULT_MODE=%r; using %s.", raw, DEFAULT_MODE.value)
        return DEFAULT_MODE
    if mode is ConversationMode.RECORD:
        # Allowed, because an operator running a standing meeting recorder is a
        # real use, but worth saying out loud: a robot that boots into 紀錄模式
        # is silent until it hears its name, which looks exactly like a robot
        # that failed to start.
        logger.warning("REALTIME_DEFAULT_MODE=record: Reachy will boot silent until it is addressed by name.")
    return mode
```

Add the property immediately above `set_conversation_mode`:

```python
    @property
    def _party_mode(self) -> bool:
        """Whether the ROOM turn policy applies — GROUP and RECORD both do.

        Compat shim, and a deliberate one. A dozen sites branch on this
        (`:1073, :1099, :1118, :1264, :2402, :2422, :2597, :2619, :2629, :2653,
        :2662, :825` and `openai_realtime.py:416`) and every one of them asks
        the same binary question: debounced room barge-in and a gate at
        `transcription.completed`, or the solo pause-then-decide machine?
        RECORD wants the room answer at all of them. Sites whose behavior really
        differs per mode read `_conversation_mode` instead.

        The `getattr` default mirrors the existing defensive read at
        `openai_realtime.py:416`: config emission must also work on
        partially-built handlers (tests construct via `__new__`). It is
        deliberately `ONE_ON_ONE` and **not** `DEFAULT_MODE` — the contract it
        preserves is `getattr(self, "_party_mode", False)`, i.e. a handler with
        no mode state at all emits the solo config, exactly as it did before
        this wave. A real handler always has `_conversation_mode` set.
        """
        mode = getattr(self, "_conversation_mode", ConversationMode.ONE_ON_ONE)
        return mode is not ConversationMode.ONE_ON_ONE
```

Replace `set_party_mode` (`:607-652`) wholesale with:

```python
    # --- conversation modes -------------------------------------------------
    async def set_conversation_mode(self, mode: str | ConversationMode) -> dict[str, Any]:
        """Switch conversation mode and push the new policy to the live session.

        Successor to `set_party_mode` (2026-08-24 → 2026-08-31). Injected into
        `ToolDependencies` (same seam as `go_to_sleep`) so the
        `set_conversation_mode` tool can switch mid-conversation.

        **Async, unlike its predecessor** (Codex round 1, P1-1). `set_party_mode`
        scheduled its session update with `ensure_future` and returned; the model
        then spoke its confirmation against whatever the server still had. That
        was survivable when the update carried only turn detection. It is not
        survivable now that it carries the mode's instructions and its whole tool
        list — the confirmation sentence, and any tool call the model makes right
        after it, would run against the previous mode. So the update is awaited
        before the tool result goes back.
        """
        target = mode if isinstance(mode, ConversationMode) else parse_mode(mode)
        if target is None:
            logger.warning("set_conversation_mode: unknown mode %r", mode)
            return {"ok": False, "error": f"unknown conversation mode: {mode}", "modes": list(MODE_VALUES)}
        previous = self._conversation_mode
        if target is previous:
            return {"ok": True, "status": "unchanged", "mode": target.value, "label": MODE_LABELS[target]}
        # Read BEFORE the flags below are cleared (Codex round 2, 2a-3): the
        # guard at the bottom asks "was somebody mid-utterance when the mode
        # changed?", and this method is about to clear both flags itself, so
        # asking afterwards always answered no.
        turn_in_flight = self._party_speech_open or self._barge_speech_open
        self._conversation_mode = target
        self._party_speech_open = False
        # The solo speech flag is maintained by the solo branch of
        # `speech_stopped`, which stops running the moment the mode changes.
        # Left stale True it would keep the response watchdog standing down for
        # the rest of the session (Task 8 fix round, finding 3).
        self._barge_speech_open = False
        # Same hazard class, same cure: late eligibility is written only by
        # `_solo_speech_started`, which the room branch never runs.
        self._barge_late_eligible = False
        self._party_utterance_seq += 1  # any sleeping barge timer is now stale
        if self._barge_paused or self._barge_pending:
            # The solo pause has just lost every timer that could resolve it, so
            # it must be resolved here or the reply stays held forever. Rolling
            # back is the honest reading: nothing confirmed this as a barge.
            self._resume_playback(rolled_back=True)
        # `_resume_playback(rolled_back=True)` records a resumed response id and
        # nothing on the flip path ever clears it (the completed-transcript
        # branch that normally does belongs to the loop this flip just left).
        self._barge_resumed_response_id = None
        # Whoever just switched to the room mode is clearly engaged: entering
        # GROUP opens the follow-up window so the conversation that asked for it
        # can continue without re-addressing by name. RECORD deliberately does
        # NOT — quiet-scribe posture: every command needs the name.
        self._party_last_accept_at = time.monotonic() if target is ConversationMode.GROUP else None
        # A flip with no utterance in flight re-stamps the fallback turn mode
        # too, so the next `speech_started` is not the only thing that can
        # correct it. With one in flight the stamp is left alone: that turn is
        # decided under the mode it began in.
        if not turn_in_flight:
            self._turn_mode = target
        logger.info("conversation mode: %s -> %s", previous.value, target.value)
        if self.connection is not None:
            # Task 3 replaces this with `await self._push_mode_update()`, which
            # additionally carries the mode's instructions and tool list. The
            # await is established here so the signature never changes again.
            await self._push_turn_detection_update()
        # Re-read AFTER the await (Codex round 3, finding 4). A second
        # `set_conversation_mode` can land while this one is waiting for its
        # acknowledgement, and the model speaks this result out loud: reporting
        # a mode the handler is no longer in would have Reachy announce 紀錄模式
        # while it is actually in 多人聊天模式.
        current = self._conversation_mode
        if current is not target:
            logger.info(
                "conversation mode %s was superseded by %s before this call returned",
                target.value,
                current.value,
            )
            return {
                "ok": True,
                "status": "superseded",
                "mode": current.value,
                "label": MODE_LABELS[current],
                "requested": target.value,
            }
        return {"ok": True, "status": "mode_set", "mode": target.value, "label": MODE_LABELS[target]}
```

New file `reachy_companion/src/reachy_companion/tools/set_conversation_mode.py`:

```python
"""Switch conversation mode by voice (2026-08-31). Filename == Tool.name.

Replaces the boolean `party_mode` tool. One tool, three postures: 多人聊天模式
(the old party mode, unchanged, and the mode Reachy boots into), 一對一聊天模式,
and 紀錄模式 (quiet scribe + spoken summary). The mechanism lives in
`huggingface_realtime`; this tool is the voice switch.
"""

from __future__ import annotations
import logging
from typing import Any, Dict

from reachy_companion.conversation_mode import MODE_VALUES
from reachy_companion.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


class SetConversationMode(Tool):
    """Switch between one-on-one, group and record conversation modes."""

    name = "set_conversation_mode"
    description = (
        "Switch how Reachy participates in the conversation while it STAYS awake. "
        "Modes: `group` 多人聊天模式 — the mode Reachy starts in; a room with several people, so Reachy "
        "stays quiet and answers only when someone says its name. "
        "`one_on_one` 一對一聊天模式 — one person talking with Reachy; it answers normally, without "
        "needing to be named. "
        "`record` 紀錄模式 — a meeting or a long discussion; Reachy listens silently, writes everything "
        "down, and speaks only when its name is used, mainly to give a summary via summarize_conversation. "
        "Use when: the user asks to change how you listen or participate — 「開一對一模式」「切到多人聊天模式」"
        "「進入紀錄模式」「開始記錄」「幫我記會議」「回到一般模式」「switch to group mode」「record mode」"
        "「stop recording」. "
        "Do NOT use when: the user wants you to stop, sleep, or end the interaction — that is go_to_sleep. "
        "Do NOT use when: the user only wants you quiet for a moment — that is wait_for_user. "
        "After switching, say one short sentence confirming the new mode, then stop."
    )
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "enum": list(MODE_VALUES),
                "description": "group 多人聊天模式（開機預設）；one_on_one 一對一聊天模式；record 紀錄模式。",
            },
        },
        "required": ["mode"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Switch the handler's conversation mode through the injected seam.

        Awaited: the seam does not return until the server has applied the new
        instructions and tool list, so the confirmation sentence the model
        speaks next is spoken under the mode it is confirming.
        """
        if deps.set_conversation_mode is None:
            return {"ok": False, "error": "conversation modes are not wired on this build"}
        mode = kwargs.get("mode")
        if not isinstance(mode, str):
            return {"ok": False, "error": "mode must be a string", "modes": list(MODE_VALUES)}
        logger.info("Tool call: set_conversation_mode mode=%s", mode)
        return await deps.set_conversation_mode(mode)
```

Delete `reachy_companion/src/reachy_companion/tools/party_mode.py`.

In `tools/core_tools.py`, replace the `set_party_mode` field (`:53-56`) with:

```python
    # Conversation modes (2026-08-31): switches the handler's turn policy
    # between 一對一 / 多人 / 紀錄模式. Injected per handler build, same seam and
    # same optionality rationale as go_to_sleep. Takes the mode's string value
    # (`conversation_mode.MODE_VALUES`) rather than the enum, so a tool module
    # never has to import the handler. Async, unlike the older seams here,
    # because the session update must be applied before the tool result reaches
    # the model (Codex round 1, P1-1).
    set_conversation_mode: Callable[[str], Awaitable[dict[str, Any]]] | None = None
```

(add `from collections.abc import Awaitable` to `core_tools.py`'s imports — Task 8 needs it too)

In `main.py` (`:266-269`):

```python
        # The mode switch reaches the live handler through deps; the rewire here
        # (not at deps construction) is what keeps the seam correct across
        # handler rebuilds by the settings UI (voice changes).
        deps.set_conversation_mode = handler.set_conversation_mode
```

In `tools/go_to_sleep.py`, replace the description (`:15-20`) — the cross-reference must name the new tool or the model will confuse 紀錄模式 with sleeping (survey §1.1):

```python
    description = (
        "End the interaction entirely: Reachy says goodbye, stops, and rests. Use when you are sure the user "
        "wants Reachy gone, off, asleep, or the conversation over — in any wording or language. The judgment: "
        "they want you to STOP being active, not to keep participating in a different way (that is "
        "set_conversation_mode: 一對一聊天模式 / 多人聊天模式 / 紀錄模式). "
        "Do NOT use when: the user only wants you quiet for a moment — that is wait_for_user. "
        "Do NOT use when: the user wants you to listen differently, record, or stop recording — that is "
        "set_conversation_mode. "
        "Do not use for idle turns, sleepy emotions, silence, or ambiguous requests."
    )
```

In `reachy_companion/profiles/_reachy_companion_locked_profile/profile.md`, replace the `"party_mode",` entry of `default_tools` with `"set_conversation_mode",`. **Every profile edit in this plan has a matching edit in `tests/test_profile.py::EXPECTED_TOOLS`** — an ordered tripwire against unplanned tool additions — so make the same substitution there, in place.

- [ ] **Step 4: Run the new and adjacent tests**

Run: `python -m pytest tests/test_conversation_modes.py tests/test_party_mode.py tests/test_solo_barge.py tests/test_profile.py tests/test_profile_toolsets.py -v`
Expected: PASS. Then `python -m pytest` — expect 1571-baseline ± the tests moved between files (5 removed from `test_party_mode.py`, 13 added in `test_conversation_modes.py`). Any other failure is a real regression: grep for stragglers with `grep -rn "party_mode" src/ tests/ profiles/` and fix each (`set_party_mode` must have zero hits; `_party_mode` should only hit the property, its docstring and the branch sites).

- [ ] **Step 5: Lint, typecheck, commit**

```bash
ruff check . && mypy --strict src
git add -A reachy_companion/src reachy_companion/tests reachy_companion/profiles
git commit -m "feat(modes): ConversationMode enum, set_conversation_mode tool and seam"
```

---

### Task 2: Client-driven responses in every mode + the per-mode answer gate

**Files:**
- Modify: `reachy_companion/src/reachy_companion/openai_realtime.py` (`_turn_detection` `:184-227`)
- Modify: `reachy_companion/src/reachy_companion/huggingface_realtime.py` (module helpers near `_solo_name_gate` `:129-152`; completed-transcript branch `:2593-2658`)
- Modify test: `reachy_companion/tests/test_party_mode.py` (turn-detection assertions `:73-102`)
- Modify test: `reachy_companion/tests/test_conversation_modes.py`

**Interfaces:**
- Consumes (from Task 1): `ConversationMode`, `self._conversation_mode`, `self._party_mode` property.
- Consumes (existing): `_gate_text_accepts(text) -> tuple[bool, str]` (`:140`), `self._party_gate_accepts(transcript) -> bool` (`:752`), `is_substantive(text) -> bool` (`audio/backchannel.py:69`), `self._safe_response_create(**kwargs) -> None` (`:1669`), `on_turn_without_response(deps)` (`hanova/music_hooks.py:261`).
- Produces (module level, `huggingface_realtime`): `_one_on_one_answer_gate() -> str` — reads env `REALTIME_ONE_ON_ONE_ANSWER_GATE`, returns `"open"` (default) or `"name_only"`; anything else warns and falls back to `"open"`. Module constant `_ONE_ON_ONE_ANSWER_GATES: Final[tuple[str, ...]] = ("name_only", "open")`.
- Produces (module level, `huggingface_realtime`): `_ANSWER_DENY_LOG: Final[dict[ConversationMode, str]]` with exactly these values — `ONE_ON_ONE: "one-on-one gate: no answer for a non-substantive turn"`, `GROUP: "party gate: denied ambient turn"`, `RECORD: "record gate: transcribed without answering"`.
- Produces (handler): `_answer_gate_accepts(self, transcript: str, mode: ConversationMode) -> bool` — the mode is an **explicit argument**, never read from `self` inside the gate (Codex round 1, P1-2).
- Produces (handler): `_stamp_turn_mode(self, item_id: str | None) -> None` and `_take_turn_mode(self, item_id: str | None) -> ConversationMode`. The stamp is taken at `input_audio_buffer.speech_started` (whose event carries `item_id: str`, verified against the installed `openai 2.28.0` `InputAudioBufferSpeechStartedEvent`) and **keyed by that item**, because a single field is overwritten by the next `speech_started` before a slow `transcription.completed` for the previous turn arrives (Codex round 2, 2a-4). `_take_turn_mode` pops the entry; a turn whose event carried no id falls back to `self._turn_mode`. Both maps are cleared per session in `_party_reset_for_new_session`. The popped mode is what the completed-transcript branch uses for the gate verdict, the deny log line and the follow-up-window update.
- Produces (behavioral contract Tasks 4, 6 and 11 depend on): `_turn_detection()` now sets `create_response=False` for **every** mode; the completed-transcript branch calls `await self._safe_response_create()` for every accepted turn in every mode; a denied turn still reaches `self._emit_transcript("user", transcript, True)` before it `continue`s.

- [ ] **Step 1: Write the failing tests**

Replace `test_solo_turn_detection_keeps_the_server_answering` in `tests/test_party_mode.py` (`:73-87`) with:

```python
def test_every_mode_turns_off_server_auto_answer(monkeypatch: pytest.MonkeyPatch):
    """2026-08-31: the client answers gate-accepted turns in EVERY mode.

    This is the core of the double-answer fix: with `create_response` left
    absent in solo, a turn the client rolled back still got a full spoken answer
    queued behind the resumed reply.
    """
    solo = _turn_detection(party=False)
    assert solo["interrupt_response"] is False
    assert solo["create_response"] is False

    room = _turn_detection(party=True)
    assert room["interrupt_response"] is False
    assert room["create_response"] is False

    monkeypatch.setenv("REALTIME_SOLO_CLIENT_BARGE", "0")
    legacy = _turn_detection(party=False)
    # The legacy flag restores server-side INTERRUPTION only; answering stays
    # the client's job.
    assert legacy["interrupt_response"] is True
    assert legacy["create_response"] is False
```

Append to `tests/test_conversation_modes.py`:

```python
# --------------------------------------------------------------------------
# The answer gate (2026-08-31 plan, Task 2)
# --------------------------------------------------------------------------


def test_one_on_one_answers_any_substantive_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    """No name needed: this is what makes single-person conversation natural."""
    monkeypatch.delenv("REALTIME_ONE_ON_ONE_ANSWER_GATE", raising=False)
    # The interruption gate is a different knob and must not reach this one.
    monkeypatch.setenv("REALTIME_SOLO_NAME_GATE", "1")
    h = _mode_handler(ConversationMode.ONE_ON_ONE)
    one = ConversationMode.ONE_ON_ONE
    assert h._answer_gate_accepts("我們晚餐要吃什麼呢", one)
    assert h._answer_gate_accepts("停", one)
    assert h._answer_gate_accepts("瑞奇你好", one)
    assert not h._answer_gate_accepts("嗯嗯", one)


def test_one_on_one_strict_under_name_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """`REALTIME_ONE_ON_ONE_ANSWER_GATE=name_only` is the field fallback (Open question 1)."""
    h = _mode_handler(ConversationMode.ONE_ON_ONE)
    one = ConversationMode.ONE_ON_ONE
    monkeypatch.setenv("REALTIME_ONE_ON_ONE_ANSWER_GATE", "name_only")
    assert not h._answer_gate_accepts("我們晚餐要吃什麼呢", one)
    assert h._answer_gate_accepts("瑞奇我們晚餐要吃什麼呢", one)
    assert h._answer_gate_accepts("停", one)
    monkeypatch.setenv("REALTIME_ONE_ON_ONE_ANSWER_GATE", "open")
    assert h._answer_gate_accepts("我們晚餐要吃什麼呢", one)


def test_a_malformed_answer_gate_value_degrades_to_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every mode knob degrades with a warning, never raises (survey, cross-cutting)."""
    from reachy_companion.huggingface_realtime import _one_on_one_answer_gate

    monkeypatch.delenv("REALTIME_ONE_ON_ONE_ANSWER_GATE", raising=False)
    assert _one_on_one_answer_gate() == "open"
    monkeypatch.setenv("REALTIME_ONE_ON_ONE_ANSWER_GATE", "NAME_ONLY")
    assert _one_on_one_answer_gate() == "name_only"
    monkeypatch.setenv("REALTIME_ONE_ON_ONE_ANSWER_GATE", "nonsense")
    assert _one_on_one_answer_gate() == "open"


def test_the_interruption_gate_is_a_separate_knob(monkeypatch: pytest.MonkeyPatch) -> None:
    """D-028's REALTIME_SOLO_NAME_GATE must not touch answering.

    The instance `.env` ships `REALTIME_SOLO_NAME_GATE=1` and the deploy ritual
    restores `.env` from backup on every install, so an overloaded variable would
    silently re-enable name-only answering on every deploy (Open question 1).
    """
    h = _mode_handler(ConversationMode.ONE_ON_ONE)
    monkeypatch.delenv("REALTIME_ONE_ON_ONE_ANSWER_GATE", raising=False)
    for value in ("0", "1"):
        monkeypatch.setenv("REALTIME_SOLO_NAME_GATE", value)
        assert h._answer_gate_accepts("我們晚餐要吃什麼呢", ConversationMode.ONE_ON_ONE)


def test_record_answers_only_name_or_control(monkeypatch: pytest.MonkeyPatch) -> None:
    """Quiet scribe: everything else is transcribed silently."""
    monkeypatch.delenv("REALTIME_ONE_ON_ONE_ANSWER_GATE", raising=False)
    h = _mode_handler(ConversationMode.RECORD)
    record = ConversationMode.RECORD
    assert not h._answer_gate_accepts("那我們下週三再開一次", record)
    assert h._answer_gate_accepts("瑞奇幫我總結一下", record)
    assert h._answer_gate_accepts("停", record)
    # No follow-up window: a recent accept must not open one.
    h._party_last_accept_at = 10.0**9
    assert not h._answer_gate_accepts("然後呢", record)


def test_group_keeps_the_party_gate_unchanged() -> None:
    """GROUP semantics are byte-identical to party mode (brief ruling)."""
    import time as _time

    h = _mode_handler(ConversationMode.GROUP)
    group = ConversationMode.GROUP
    assert h._answer_gate_accepts("瑞奇你在嗎", group)
    assert not h._answer_gate_accepts("哈哈哈", group)
    h._party_last_accept_at = _time.monotonic()
    assert h._answer_gate_accepts("然後呢？", group)


def test_the_gate_uses_the_turn_mode_not_the_live_mode() -> None:
    """A flip mid-utterance must not retroactively reclassify it (P1-2).

    Ambient speech that began in 多人聊天模式 must not become answerable because
    someone flipped to 一對一 while it was still being spoken, and a solo
    question must not be denied because 紀錄模式 started after it.
    """
    h = _mode_handler(ConversationMode.ONE_ON_ONE)
    # The utterance began in GROUP; the live mode has since flipped to solo.
    assert not h._answer_gate_accepts("我剛剛問他為什麼耳朵這麼長", ConversationMode.GROUP)
    h_record = _mode_handler(ConversationMode.RECORD)
    assert h_record._answer_gate_accepts("我們晚餐要吃什麼呢", ConversationMode.ONE_ON_ONE)


@pytest.mark.asyncio
async def test_an_overlapping_turn_keeps_its_own_mode_stamp() -> None:
    """Turn A starts in GROUP, mode flips, turn B starts, A's transcript lands late.

    A single `_turn_mode` field would have been overwritten by turn B's
    `speech_started` and A would be judged under the new mode (Codex round 2,
    2a-4). The stamps are keyed by input item, so A is still GROUP.
    """
    h = _mode_handler(ConversationMode.GROUP)
    h.connection = None
    h._stamp_turn_mode("item_a")
    await h.set_conversation_mode("one_on_one")
    h._stamp_turn_mode("item_b")
    assert h._take_turn_mode("item_a") is ConversationMode.GROUP
    assert h._take_turn_mode("item_b") is ConversationMode.ONE_ON_ONE
    # Popped, so a repeat lands on the fallback rather than a stale entry.
    assert h._turn_modes == {}
    assert h._take_turn_mode("item_a") is ConversationMode.ONE_ON_ONE


def test_a_turn_with_no_item_id_uses_the_fallback_stamp() -> None:
    h = _mode_handler(ConversationMode.GROUP)
    h._stamp_turn_mode(None)
    assert h._take_turn_mode(None) is ConversationMode.GROUP


def test_the_stamp_map_is_bounded() -> None:
    """Only reachable if transcripts stop arriving; it must not grow forever."""
    from reachy_companion.huggingface_realtime import _TURN_MODE_MAX_ITEMS

    h = _mode_handler()
    for index in range(_TURN_MODE_MAX_ITEMS + 5):
        h._stamp_turn_mode(f"item_{index}")
    assert len(h._turn_modes) <= _TURN_MODE_MAX_ITEMS
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_conversation_modes.py tests/test_party_mode.py -k "answer or record_answers or group_keeps or turn_mode or overlapping_turn or fallback_stamp or stamp_map or superseded or auto_answer" -v`
(One `-k`, not two — pytest keeps only the last `-k` on a command line and applies it globally, which would silently deselect exactly the tests this step is checking.)
Expected: FAIL — `AttributeError: 'OpenAIRealtimeHandler' object has no attribute '_answer_gate_accepts'`, and `KeyError: 'create_response'` on the solo turn-detection assertion.

- [ ] **Step 3: Implement**

In `openai_realtime.py`, `_turn_detection` (`:184-227`): move `create_response` out of both `if party:` branches so it is unconditional, and update the docstring's last paragraph.

```python
    server_interrupts = not party and not _solo_client_barge()
    warn_if_barge_confirm_races_vad()
    vad_type = os.getenv("REALTIME_VAD_TYPE", "server_vad").strip().lower() or "server_vad"
    if vad_type == "semantic_vad":
        semantic = SemanticVad(
            type="semantic_vad",
            eagerness=_eagerness(),
            interrupt_response=server_interrupts,
        )
        semantic["create_response"] = False
        return semantic
    if vad_type != "server_vad":
        logger.warning("Ignoring invalid REALTIME_VAD_TYPE=%r; using server_vad.", vad_type)
    server = ServerVad(
        type="server_vad",
        interrupt_response=server_interrupts,
        threshold=env_float("REALTIME_VAD_THRESHOLD", 0.5, lo=0.0, hi=1.0),
        prefix_padding_ms=env_int("REALTIME_VAD_PREFIX_PADDING_MS", 300, lo=0),
        # Shared with the barge-in confirm window, which must outlast it.
        silence_duration_ms=_vad_silence_duration_ms(),
    )
    server["create_response"] = False
    return server
```

Docstring paragraph to replace the "Solo mode now does the same…" block:

```
    Since 2026-08-31 `create_response` is **false in every mode**. The server
    still commits and transcribes turns; it never answers one. The client
    answers exactly the turns its per-mode answer gate accepts, through
    `_safe_response_create()`. That is what makes a rolled-back turn produce no
    answer at all — with the server auto-answering, every gated turn still got a
    full spoken reply queued behind the resumed audio, which is the pile-up the
    operator saw as "five tries to get a reply".
    `REALTIME_SOLO_CLIENT_BARGE=0` restores server-side INTERRUPTION only.
```

In `huggingface_realtime.py`, directly after `_gate_text_accepts` (`:152`):

```python
_ONE_ON_ONE_ANSWER_GATES: Final[tuple[str, ...]] = ("name_only", "open")


def _one_on_one_answer_gate() -> str:
    """Which turns 一對一聊天模式 answers: `open` (default) or `name_only`.

    Its OWN variable, deliberately not `REALTIME_SOLO_NAME_GATE` (2026-08-31
    plan, Open question 1). That one keeps its 2026-08-30 meaning — the
    *interruption* gate, default on, "Reachy talks through speech aimed at
    someone else" — and the robot's instance `.env` ships it explicitly set,
    with the deploy ritual restoring `.env` from backup on every install. An
    overloaded variable would therefore have flipped one-on-one to name-only
    answering on every single deploy, silently, forever.

    `open` is the default because the whole point of one-on-one is that a single
    person does not have to say the robot's name to be answered. `name_only` is
    the field fallback if open answering turns out to pick up too much of the
    room; it makes this mode answer on the same rule 紀錄模式 uses.

    Degrades with a warning rather than raising, like every other mode knob.
    """
    raw = (os.getenv("REALTIME_ONE_ON_ONE_ANSWER_GATE") or "").strip().lower()
    if not raw:
        return "open"
    if raw not in _ONE_ON_ONE_ANSWER_GATES:
        logger.warning("Ignoring invalid REALTIME_ONE_ON_ONE_ANSWER_GATE=%r; using open.", raw)
        return "open"
    return raw


# The journal line each mode prints when it hears a turn it will not answer.
# GROUP's is unchanged from party mode: `feature_list.json` rows cite it.
_ANSWER_DENY_LOG: Final[dict[ConversationMode, str]] = {
    ConversationMode.ONE_ON_ONE: "one-on-one gate: no answer for a non-substantive turn",
    ConversationMode.GROUP: "party gate: denied ambient turn",
    ConversationMode.RECORD: "record gate: transcribed without answering",
}
```

Add `_answer_gate_accepts` on the handler, directly after `_party_gate_accepts` (`:775`):

```python
    def _answer_gate_accepts(self, transcript: str, mode: ConversationMode) -> bool:
        """Whether this committed turn earns a spoken reply, under *mode*.

        The mode is passed in, never read from `self` (Codex round 1, P1-2):
        the verdict belongs to the mode the utterance BEGAN in. Reading the live
        field here would let a flip that happened while someone was still
        talking retroactively reclassify their half-spoken sentence — ambient
        room chatter answered because the mode became 一對一 mid-utterance, or a
        direct question silently dropped because 紀錄模式 started after it.

        Distinct from the barge gate on purpose (2026-08-31 plan, decision 2):
        `_gate_text_accepts` / the name gate decide what may CUT OFF a playing
        reply; this decides what gets ANSWERED. Conflating them is what produced
        the observed pile-up — a turn rolled back as an interruption still got a
        full spoken answer from the server.

        * GROUP keeps `_party_gate_accepts` exactly as it is, ordering included.
        * RECORD accepts only an address name or a control phrase: no engaged
          face, no follow-up window. Quiet-scribe posture — every command needs
          the name, and everything else is transcribed silently.
        * ONE_ON_ONE accepts anything substantive, so a single person never has
          to say the robot's name; only backchannels and empties fall through.
          `REALTIME_ONE_ON_ONE_ANSWER_GATE=name_only` tightens it to RECORD's
          rule — a separate variable from the interruption gate, on purpose
          (Open question 1).
        """
        if mode is ConversationMode.GROUP:
            return self._party_gate_accepts(transcript)
        accepted, _reason = _gate_text_accepts(transcript)
        if mode is ConversationMode.RECORD or _one_on_one_answer_gate() == "name_only":
            return accepted
        return accepted or is_substantive(transcript)
```

Add the two helpers next to `_answer_gate_accepts`:

```python
    def _stamp_turn_mode(self, item_id: str | None) -> None:
        """Record the mode this utterance began in, keyed by its input item.

        Every verdict about a turn is taken under the mode it started in, so a
        flip mid-sentence cannot retroactively reclassify speech that is already
        half-spoken (Codex round 1, P1-2).

        Keyed per item rather than held in one field (Codex round 2, 2a-4):
        `transcription.completed` can arrive a second or more after the NEXT
        utterance has already started, and a single field would by then be
        describing the wrong turn. `_turn_mode` stays as the fallback for an
        event that carries no id, and as the value a mode flip re-stamps when
        nobody is speaking.
        """
        mode = self._conversation_mode
        self._turn_mode = mode
        if item_id:
            if len(self._turn_modes) >= _TURN_MODE_MAX_ITEMS:
                # Only reachable if transcripts stop arriving entirely; drop the
                # oldest so a stuck session cannot grow this without bound.
                self._turn_modes.pop(next(iter(self._turn_modes)), None)
            self._turn_modes[item_id] = mode

    def _take_turn_mode(self, item_id: str | None) -> ConversationMode:
        """Pop the mode stamped for this input item, or the fallback stamp."""
        if item_id:
            stamped = self._turn_modes.pop(item_id, None)
            if stamped is not None:
                return stamped
        return self._turn_mode
```

with the module constant next to the other bounds:

```python
# Bound on the per-item turn-mode stamps. One entry lives from `speech_started`
# to that item's `transcription.completed`/`.failed`, so the map is normally
# one or two deep; the cap only matters if transcripts stop arriving at all.
_TURN_MODE_MAX_ITEMS: Final[int] = 16
```

Stamp at `input_audio_buffer.speech_started` (`:2396-2401`), as the first statement of that branch — before the party/solo fork, so both room and solo turns carry it:

```python
                        self._stamp_turn_mode(getattr(event, "item_id", None))
```

and clear the map alongside the other per-session turn state in `_party_reset_for_new_session()` (`:654-667`):

```python
        self._turn_mode = self._conversation_mode
        self._turn_modes.clear()
```

Rewrite the completed-transcript branch's gate + answer sections. Replace `:2597-2607` (the party-only deny block) with a mode-general one that reads the stamped mode:

**Pop the stamp at the TOP of the completed branch**, not at the gate — right after `transcript = raw_transcript.strip()` (`:2566`). The branch has three `continue`s before the gate (empty transcript, rolled-back pause, and the partial-commit marker), and a stamp left behind by any of them would leak:

```python
                        turn_mode = self._take_turn_mode(getattr(event, "item_id", None))
```

and pop it in the `transcription.failed` branch too (`:2660-2673`), where no transcript will ever come:

```python
                        self._take_turn_mode(getattr(event, "item_id", None))
```

Then the gate block reads the local:

```python
                        if not self._answer_gate_accepts(transcript, turn_mode):
                            # Heard, kept as context (it is already in the
                            # conversation), and left unanswered. Close the turn
                            # for the music hooks (party plan, finding 4) and
                            # touch nothing else — the tool-batch state belongs
                            # to an accepted turn that may still be running.
                            logger.info("%s (%d chars)", _ANSWER_DENY_LOG[turn_mode], len(transcript))
                            on_turn_without_response(self.deps)
                            await self.output_queue.put(AdditionalOutputs({"role": "user", "content": transcript}))
                            self._emit_transcript("user", transcript, True)
                            continue
```

Replace the trailing party-only answer block (`:2653-2658`) with:

```python
                        if turn_mode is ConversationMode.GROUP:
                            # The follow-up window is a GROUP concept: it lets a
                            # conversation continue without re-addressing by
                            # name. RECORD deliberately has none. Keyed on the
                            # turn's own mode for the same reason the verdict is.
                            self._party_last_accept_at = time.monotonic()
                        # `create_response` is off in every mode since
                        # 2026-08-31: this turn passed its mode's answer gate, so
                        # answer it — through the sender queue, never the raw
                        # connection (party plan, finding 1).
                        await self._safe_response_create()
```

- [ ] **Step 4: Run the affected modules**

Run: `python -m pytest tests/test_conversation_modes.py tests/test_party_mode.py tests/test_solo_barge.py tests/test_huggingface_realtime.py tests/test_openai_realtime_config.py tests/test_boot_gate.py -v`
Expected: PASS. Then `python -m pytest` — full suite green. Any test that asserted `"create_response" not in …` for solo encodes the pre-2026-08-31 semantics: update it to assert `is False`, do not weaken it.

- [ ] **Step 5: Lint, typecheck, commit**

```bash
ruff check . && mypy --strict src
git add reachy_companion/src reachy_companion/tests
git commit -m "feat(modes): client-driven responses in every mode; per-mode answer gate"
```

---

### Task 3: Per-mode prompt block and one narrow live mode update

**Files:**
- Modify: `reachy_companion/src/reachy_companion/prompts.py` (after `hardening_block` `:61-65`)
- Modify: `reachy_companion/src/reachy_companion/huggingface_realtime.py` (`_get_session_config` `:1456-1479`, `apply_personality` `:1543`, `_push_turn_detection_update` no-op neighborhood `:669-676`, `set_conversation_mode` from Task 1)
- Modify: `reachy_companion/src/reachy_companion/openai_realtime.py` (after `_push_turn_detection_update` `:489-516`)
- Modify test: `reachy_companion/tests/test_prompts_hardening.py`
- Modify test: `reachy_companion/tests/test_conversation_modes.py`

**Interfaces:**
- Produces (`prompts`): `mode_rules_block(mode: ConversationMode) -> str` — the per-mode rules appended to the session instructions. Never empty.
- Produces (handler, base): `_mode_instructions(self) -> str` = `get_session_instructions(self.instance_path)` + `"\n\n"` + `mode_rules_block(self._conversation_mode)`.
- Produces (handler, base): `async _apply_session_update(self, build_session: Callable[[], RealtimeSessionCreateRequestParam | None], *, what: str) -> bool` — the **single ordered, acknowledged, single-flight update mechanism** (design decision 9). It takes a **builder**, not a payload: the builder runs inside the lock, so the ticket check, the snapshot, the waiter install, the send and the acknowledgement wait are one uninterrupted region (Codex round 2, 2a-1). A builder returning `None` means "superseded, send nothing" and the call reports `True`. Base returns `True` without sending (the HF backend has no `session.update` semantics we control), exactly like `_push_turn_detection_update`.
- **Every live-session update goes through it** (Codex round 2, 2a-2). This task also converts the three existing callers, because an uncorrelated `session.updated` is only safe under single-flight:
  - `_push_turn_detection_update` (`openai_realtime.py:489-516`) — builder returns `{"type": "realtime", "audio": {"input": …}}`, `what="turn detection"`.
  - `change_voice` (`huggingface_realtime.py:1516-1525`) — builder returns the `audio.output.voice` payload, `what=f"voice {voice}"`.
  - `apply_personality` (`:1553-1563`) — builder returns the `instructions` + `voice` payload, `what=f"personality {profile or 'default'}"`; it keeps its existing `_restart_session()` follow-up and its "will take effect on next connection" fallbacks, only the send changes.
  The **one exemption** is the initial `conn.session.update` in `_run_realtime_session` (`:2327`) and its retry (`:2334`) — see design decision 9.
- Produces (handler, base): `async _push_mode_update(self) -> bool` — builds the mode payload and hands it to `_apply_session_update`. **Returns whether the server applied it**, so callers can roll back. Base returns `True`.
- Produces (`OpenAIRealtimeHandler`): the real `_apply_session_update`, plus these fields, all initialized in `__init__` (and in the `_mode_handler`/`_box_handler` test harnesses):
  - `self._session_update_lock: asyncio.Lock`
  - `self._session_update_event_id: str | None` — the client `event_id` of the update currently awaiting acknowledgement; `None` when none is in flight.
  - `self._session_update_waiter: asyncio.Future[bool] | None`
  - `self._session_update_ack_debt: int` — acknowledgements the server still owes for updates nobody waited on (connect-time config, its fallback retry, pre-receive-loop pushes, timed-out waits). Consumed **before** any live waiter (Codex round 3, findings 5 and 6).
  - `self._receive_loop_active: bool` — whether an acknowledgement can be observed at all (Codex round 3, finding 1).
  - `self._mode_update_seq: int` (declared in Task 1) — monotonic coalescing token.
- Produces (handler): `_note_session_updated(self) -> None` — the `session.updated` branch: pays `_session_update_ack_debt` first, resolves the waiter only when the debt is clear. And `_resolve_session_update(self, applied: bool, detail: str | None) -> None` — resolves the in-flight waiter exactly once; called by `_note_session_updated`, and directly by the `error` branch when the error's `event_id` matches (that path is correlated, so it bypasses the debt).
- Produces (module constant): `_SESSION_UPDATE_ACK_TIMEOUT_S: Final[float] = 5.0`.
- Consumes: `to_realtime_tools_config(tool_specs) -> RealtimeToolsConfigParam` (`huggingface_realtime.py:426`), `get_tool_specs(exclusion_list=None) -> list[ToolSpec]` (`tools/core_tools.py:525`), `self._get_session_config(tool_specs=[])["audio"]["input"]`, `RealtimeAudioConfigParam` / `RealtimeSessionCreateRequestParam` (`from openai.types.realtime import …`), `uuid.uuid4` (already imported at `huggingface_realtime.py`).
- Contract for Task 8: `_push_mode_update` computes its `tools` list through a single call `get_tool_specs(exclusion_list=self._mode_tool_exclusions())` **made inside the update lock**, and the base handler defines `_mode_tool_exclusions(self) -> list[str]` returning `[]`. Task 8 replaces that body with the static-core / toolbox / RECORD computation and calls `_push_mode_update()` from `open_toolbox`, rolling back on `False`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_prompts_hardening.py`:

```python
def test_mode_rules_block_covers_every_mode() -> None:
    """Party mode never told the model it was in party mode; modes must."""
    from reachy_companion.conversation_mode import ConversationMode
    from reachy_companion.prompts import mode_rules_block

    one = mode_rules_block(ConversationMode.ONE_ON_ONE)
    group = mode_rules_block(ConversationMode.GROUP)
    record = mode_rules_block(ConversationMode.RECORD)
    assert "一對一聊天模式" in one
    assert "多人聊天模式" in group
    assert "紀錄模式" in record
    assert "summarize_conversation" in record
    assert "set_conversation_mode" in record
    # Each block names its own mode and no other, so a live update cannot leave
    # two postures in the instructions at once.
    assert "紀錄模式" not in one and "紀錄模式" not in group
```

Append to `tests/test_conversation_modes.py`:

```python
# --------------------------------------------------------------------------
# The live mode update (2026-08-31 plan, Task 3)
# --------------------------------------------------------------------------


def _acking_connection(handler: OpenAIRealtimeHandler) -> SimpleNamespace:
    """A connection whose `session.update` immediately acks, as the server does.

    The real acknowledgement arrives asynchronously as a `session.updated` event
    on the receive loop; here the send itself resolves the waiter, which is the
    same contract from `_apply_session_update`'s point of view.

    It also records whether the update lock was held at send time, so the
    "one uninterrupted locked region" property is asserted where it actually
    matters — at the send, not only at the build (Codex round 3, finding 9).
    """
    calls: list[dict[str, Any]] = []
    locked_at_send: list[bool] = []

    async def _update(**kwargs: Any) -> None:
        calls.append(kwargs)
        locked_at_send.append(handler._session_update_lock.locked())
        handler._note_session_updated()

    connection = SimpleNamespace(
        session=SimpleNamespace(update=_update), calls=calls, locked_at_send=locked_at_send
    )
    return connection


@pytest.mark.asyncio
async def test_push_mode_update_sends_instructions_tools_and_turn_detection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One narrow session.update carries the whole mode, never `model`/`voice`."""
    h = _mode_handler(ConversationMode.RECORD)
    h._boot_gate_active = False
    h.instance_path = None
    h.connection = _acking_connection(h)
    monkeypatch.setattr(h, "_mode_instructions", lambda: "INSTRUCTIONS-RECORD")
    monkeypatch.setattr(
        h,
        "_get_session_config",
        lambda tool_specs: {"audio": {"input": {"turn_detection": {"type": "server_vad"}}}},
    )
    assert await h._push_mode_update() is True
    assert len(h.connection.calls) == 1
    session = h.connection.calls[0]["session"]
    assert session["type"] == "realtime"
    assert session["instructions"] == "INSTRUCTIONS-RECORD"
    assert isinstance(session["tools"], list)
    assert session["audio"]["input"]["turn_detection"]["type"] == "server_vad"
    assert "model" not in session and "voice" not in session
    # Every update carries a client event_id, which is what an error is
    # correlated against.
    assert isinstance(h.connection.calls[0]["event_id"], str)
    assert h._session_update_event_id is None  # cleared once acknowledged


@pytest.mark.asyncio
async def test_push_mode_update_passes_the_mode_exclusion_list_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The tool list must come from `_mode_tool_exclusions()` (Task 8's contract).

    Without this, an implementation that calls `get_tool_specs()` bare still
    passes every other assertion here and silently breaks Task 8 before it
    starts (Codex round 1, P1-8).
    """
    from reachy_companion import openai_realtime as oai_mod

    seen: list[list[str] | None] = []

    def _fake_specs(exclusion_list: list[str] | None = None) -> list[dict[str, Any]]:
        seen.append(exclusion_list)
        return [{"type": "function", "name": "sentinel", "description": "d", "parameters": {}}]

    h = _mode_handler()
    h._boot_gate_active = True  # skip the audio half; this test is about tools
    h.instance_path = None
    h.connection = _acking_connection(h)
    monkeypatch.setattr(h, "_mode_instructions", lambda: "INSTRUCTIONS")
    monkeypatch.setattr(h, "_mode_tool_exclusions", lambda: ["camera", "dance"])
    monkeypatch.setattr(oai_mod, "get_tool_specs", _fake_specs)
    assert await h._push_mode_update() is True
    assert seen == [["camera", "dance"]]
    assert [tool["name"] for tool in h.connection.calls[0]["session"]["tools"]] == ["sentinel"]


@pytest.mark.asyncio
async def test_push_mode_update_defers_turn_detection_while_the_boot_gate_is_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The gate owns turn detection until it opens; instructions may still go."""
    h = _mode_handler(ConversationMode.GROUP)
    h._boot_gate_active = True
    h.instance_path = None
    h.connection = _acking_connection(h)
    monkeypatch.setattr(h, "_mode_instructions", lambda: "INSTRUCTIONS-GROUP")
    assert await h._push_mode_update() is True
    session = h.connection.calls[0]["session"]
    assert "audio" not in session
    assert session["instructions"] == "INSTRUCTIONS-GROUP"


@pytest.mark.asyncio
async def test_push_mode_update_survives_a_send_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed send must not kill the handler; it warns and reports False."""
    from unittest.mock import AsyncMock

    h = _mode_handler()
    h._boot_gate_active = True
    h.instance_path = None
    h.connection = SimpleNamespace(session=SimpleNamespace(update=AsyncMock(side_effect=RuntimeError("nope"))))
    monkeypatch.setattr(h, "_mode_instructions", lambda: "INSTRUCTIONS")
    assert await h._push_mode_update() is False
    assert h._session_update_event_id is None
    assert h._session_update_waiter is None


@pytest.mark.asyncio
async def test_a_server_error_for_the_in_flight_update_reports_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`session.update` rejection arrives later as an `error` event (P1-3)."""
    h = _mode_handler()
    h._boot_gate_active = True
    h.instance_path = None

    async def _update(**kwargs: Any) -> None:
        h._resolve_session_update(False, "invalid_session_parameter")

    h.connection = SimpleNamespace(session=SimpleNamespace(update=_update))
    monkeypatch.setattr(h, "_mode_instructions", lambda: "INSTRUCTIONS")
    assert await h._push_mode_update() is False


@pytest.mark.asyncio
async def test_an_unacknowledged_update_times_out_and_reports_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A server that never answers must not hang the tool call."""
    from reachy_companion import openai_realtime as oai_mod

    monkeypatch.setattr(oai_mod, "_SESSION_UPDATE_ACK_TIMEOUT_S", 0.05)
    h = _mode_handler()
    h._boot_gate_active = True
    h.instance_path = None

    async def _update(**kwargs: Any) -> None:
        return None  # sent, never acknowledged

    h.connection = SimpleNamespace(session=SimpleNamespace(update=_update))
    monkeypatch.setattr(h, "_mode_instructions", lambda: "INSTRUCTIONS")
    assert await h._push_mode_update() is False
    assert h._session_update_event_id is None


@pytest.mark.asyncio
async def test_the_connect_ack_never_resolves_a_live_waiter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The connect-time config's `session.updated` is not a mode flip's ack.

    `session.updated` carries no client event_id, so it can only be matched
    positionally — and the connect config is acknowledged AFTER the receive loop
    starts, by which time a mode flip may already be waiting. Resolving that
    waiter would tell the flip its instructions and tool list were applied when
    what the server acknowledged was the connect config (Codex round 3,
    finding 5).
    """
    h = _mode_handler()
    h._boot_gate_active = True
    h._receive_loop_active = True
    h.instance_path = None
    h._session_update_ack_debt = 1  # the connect config, still unacknowledged
    monkeypatch.setattr(h, "_mode_instructions", lambda: "INSTRUCTIONS")

    acks: list[str] = []

    async def _update(**kwargs: Any) -> None:
        # The connect config's late acknowledgement arrives while this update is
        # already waiting for its own.
        acks.append("connect")
        h._note_session_updated()
        assert h._session_update_waiter is not None, "the flip's waiter was resolved by the wrong ack"
        assert h._session_update_ack_debt == 0
        acks.append("mine")
        h._note_session_updated()

    h.connection = SimpleNamespace(session=SimpleNamespace(update=_update))
    assert await h._push_mode_update() is True
    assert acks == ["connect", "mine"]
    assert h._session_update_waiter is None


@pytest.mark.asyncio
async def test_a_late_ack_pays_its_own_debt_not_the_next_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Update A times out, B is sent, A's ack finally arrives (round 3, finding 6).

    A's acknowledgement is late, not absent. Letting it resolve B's waiter would
    report B applied on the strength of A's ack.
    """
    from reachy_companion import openai_realtime as oai_mod

    monkeypatch.setattr(oai_mod, "_SESSION_UPDATE_ACK_TIMEOUT_S", 0.05)
    h = _mode_handler()
    h._boot_gate_active = True
    h._receive_loop_active = True
    h.instance_path = None
    monkeypatch.setattr(h, "_mode_instructions", lambda: "INSTRUCTIONS")

    async def _silent(**kwargs: Any) -> None:
        return None  # A: sent, never acknowledged in time

    h.connection = SimpleNamespace(session=SimpleNamespace(update=_silent))
    assert await h._push_mode_update() is False
    assert h._session_update_ack_debt == 1

    async def _b(**kwargs: Any) -> None:
        # A's late ack lands first; it must pay A's debt, not resolve B.
        h._note_session_updated()
        assert h._session_update_waiter is not None
        assert h._session_update_ack_debt == 0
        h._note_session_updated()  # B's own ack

    h.connection = SimpleNamespace(session=SimpleNamespace(update=_b))
    assert await h._push_mode_update() is True
    assert h._session_update_ack_debt == 0


@pytest.mark.asyncio
async def test_an_ack_with_nothing_outstanding_is_a_silent_no_op() -> None:
    h = _mode_handler()
    h._note_session_updated()
    assert h._session_update_waiter is None
    assert h._session_update_ack_debt == 0


@pytest.mark.asyncio
async def test_an_update_sent_before_the_receive_loop_does_not_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The no-greeting startup path releases the boot gate before the loop runs.

    Waiting there burns the whole ack timeout and logs a failure for an update
    that was fine (Codex round 3, finding 1).
    """
    from reachy_companion import openai_realtime as oai_mod

    monkeypatch.setattr(oai_mod, "_SESSION_UPDATE_ACK_TIMEOUT_S", 30.0)
    h = _mode_handler()
    h._boot_gate_active = True
    h._receive_loop_active = False
    h.instance_path = None
    monkeypatch.setattr(h, "_mode_instructions", lambda: "INSTRUCTIONS")

    async def _update(**kwargs: Any) -> None:
        return None  # sent; the loop is not running, so no ack can be seen

    h.connection = SimpleNamespace(session=SimpleNamespace(update=_update))
    started = asyncio.get_running_loop().time()
    assert await h._push_mode_update() is True
    assert asyncio.get_running_loop().time() - started < 1.0
    # Booked, so the ack it eventually produces cannot resolve a later waiter.
    assert h._session_update_ack_debt == 1
    assert h._session_update_waiter is None


@pytest.mark.asyncio
async def test_a_blank_startup_greeting_releases_the_boot_gate_without_stalling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression for the no-greeting path itself (Codex round 3, finding 1)."""
    from reachy_companion import huggingface_realtime as hf_mod
    from reachy_companion import openai_realtime as oai_mod

    monkeypatch.setattr(oai_mod, "_SESSION_UPDATE_ACK_TIMEOUT_S", 30.0)
    monkeypatch.setattr(hf_mod, "get_session_greeting_prompt", lambda: "   ")
    h = _mode_handler()
    h._boot_gate_active = True
    h._boot_gate_task = None
    h._startup_greeting_sent = False
    h._receive_loop_active = False
    h.instance_path = None
    monkeypatch.setattr(h, "_mode_instructions", lambda: "INSTRUCTIONS")

    async def _update(**kwargs: Any) -> None:
        return None

    async def _clear() -> None:
        return None

    h.connection = SimpleNamespace(
        session=SimpleNamespace(update=_update),
        input_audio_buffer=SimpleNamespace(clear=_clear),
    )
    started = asyncio.get_running_loop().time()
    await h._send_startup_greeting_prompt()
    assert asyncio.get_running_loop().time() - started < 1.0
    assert h._boot_gate_active is False


@pytest.mark.asyncio
async def test_every_live_update_path_goes_through_the_mechanism() -> None:
    """Single flight is the invariant the uncorrelated ack depends on (2a-2).

    A caller that sent its own `session.update` around `_apply_session_update`
    would have its acknowledgement resolve somebody else's waiter.
    """
    import inspect

    from reachy_companion import openai_realtime as oai_mod
    from reachy_companion import huggingface_realtime as hf_mod

    for method in (
        hf_mod.HuggingFaceRealtimeHandler.change_voice,
        hf_mod.HuggingFaceRealtimeHandler.apply_personality,
        oai_mod.OpenAIRealtimeHandler._push_turn_detection_update,
        oai_mod.OpenAIRealtimeHandler._push_mode_update,
    ):
        source = inspect.getsource(method)
        assert "_apply_session_update" in source, method.__qualname__
        assert "session.update(" not in source, method.__qualname__


@pytest.mark.asyncio
async def test_the_send_happens_inside_the_lock_that_built_the_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No gap between snapshot and send (Codex round 2, 2a-1).

    The builder must run while the lock is held, or a newer flip can overtake an
    older payload on the wire.
    """
    h = _mode_handler()
    h._boot_gate_active = True
    h.instance_path = None
    locked_during_build: list[bool] = []

    def _instructions() -> str:
        locked_during_build.append(h._session_update_lock.locked())
        return "INSTRUCTIONS"

    h.connection = _acking_connection(h)
    monkeypatch.setattr(h, "_mode_instructions", _instructions)
    assert await h._push_mode_update() is True
    assert locked_during_build == [True]
    # And still held at send time — the region is one, not two (round 3, #9).
    assert h.connection.locked_at_send == [True]


@pytest.mark.asyncio
async def test_rapid_flips_coalesce_to_the_latest_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """A snapshot queued behind a newer flip is dropped, not sent (P1-4)."""
    h = _mode_handler()
    h._boot_gate_active = True
    h.instance_path = None
    h.connection = _acking_connection(h)
    monkeypatch.setattr(h, "_mode_instructions", lambda: f"INSTRUCTIONS-{h._conversation_mode.value}")
    first = asyncio.create_task(h.set_conversation_mode("group"))
    second = asyncio.create_task(h.set_conversation_mode("record"))
    await asyncio.gather(first, second)
    assert h._conversation_mode is ConversationMode.RECORD
    # Whatever reached the wire last describes the mode the handler is in.
    assert h.connection.calls[-1]["session"]["instructions"] == "INSTRUCTIONS-record"
    assert len(h.connection.calls) <= 2


def test_mode_instructions_append_the_mode_block(monkeypatch: pytest.MonkeyPatch) -> None:
    from reachy_companion import huggingface_realtime as hf_mod

    h = _mode_handler(ConversationMode.RECORD)
    h.instance_path = None
    monkeypatch.setattr(hf_mod, "get_session_instructions", lambda instance_path: "BASE")
    text = h._mode_instructions()
    assert text.startswith("BASE\n\n")
    assert "紀錄模式" in text
```

(Add `from typing import Any` to `tests/test_conversation_modes.py`'s imports — `_acking_connection` needs it.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_prompts_hardening.py tests/test_conversation_modes.py -k "mode_rules or push_mode or mode_instructions or server_error or unacknowledged or rapid_flips or connect_ack or late_ack or nothing_outstanding or before_the_receive_loop or blank_startup_greeting or live_update_path or inside_the_lock" -v`
(One `-k` across both files: a second `-k` would replace the first and silently deselect half of these.)
Expected: FAIL — `ImportError: cannot import name 'mode_rules_block'` and `AttributeError: … has no attribute '_push_mode_update'`.

- [ ] **Step 3: Implement**

In `prompts.py`, add the import and the blocks after `_HARDENING_BLOCK` / `hardening_block()`:

```python
from reachy_companion.conversation_mode import ConversationMode
```

```python
# --- per-mode rules (2026-08-31 plan) ---------------------------------------
# Party mode never told the model it was in party mode: the flip only changed
# turn detection, and the gate lived entirely client-side. RECORD cannot live
# like that — "stay quiet, only summarize when called" is behavior the model
# itself must know about — so every mode now ships its own rules block,
# appended to the session instructions and re-sent on every flip.
_MODE_BLOCKS: Final[dict[ConversationMode, str]] = {
    ConversationMode.ONE_ON_ONE: """
### 目前模式：一對一聊天模式
- 現在只有一個人在跟你說話。對方不需要叫你的名字，你就正常回應。
- 你講話講到一半聽到別的聲音時，只有聽到自己的名字或「停」才停下來；其他的繼續講完。
""".strip(),
    ConversationMode.GROUP: """
### 目前模式：多人聊天模式
- 現在房間裡有好幾個人，大部分的話不是對你說的。
- 只有聽到自己的名字、或明顯是在問你的時候才開口；其他時候安靜聽著。
- 有人叫過你之後的一小段時間內可以直接接著聊，不用每一句都被點名。
""".strip(),
    ConversationMode.RECORD: """
### 目前模式：紀錄模式
- 你正在幫忙做記錄：安靜聽，把在場的人說的話都記下來。不要插話、不要附和、不要主動開口。
- 只有聽到自己的名字或「停」才回應，回應也要短。
- 有人請你總結、回顧、唸重點的時候，呼叫 summarize_conversation，然後照它回傳的
  summary_text 一字不差地唸出來，不要改寫也不要補話。
- 這個模式下你能做的事只有四件：set_conversation_mode 換模式、summarize_conversation 唸摘要、
  go_to_sleep 去睡覺、wait_for_user 安靜聽著。（task_status 和 task_cancel 也還在，
  用來追蹤還沒跑完的工作。）要做別的事，請先用 set_conversation_mode 切回其他模式。
""".strip(),
}


def mode_rules_block(mode: ConversationMode) -> str:
    """Return the rules block for *mode*, appended to the session instructions."""
    return _MODE_BLOCKS[mode]
```

(`Final` is already imported in `prompts.py`? If not, add `from typing import Final`.)

In `huggingface_realtime.py`, import `mode_rules_block` alongside `get_session_instructions`, then add next to `_get_session_config`:

```python
    def _mode_instructions(self) -> str:
        """Session instructions plus the current mode's rules block.

        One resolver for both the session-config build and the live mode update,
        so a flip and a reconnect can never disagree about what the model was
        told.
        """
        return f"{get_session_instructions(self.instance_path)}\n\n{mode_rules_block(self._conversation_mode)}"

    def _mode_tool_exclusions(self) -> list[str]:
        """Tool names hidden from the session in the current mode. Base: none."""
        return []

    async def _apply_session_update(
        self,
        build_session: Callable[[], RealtimeSessionCreateRequestParam | None],
        *,
        what: str,
    ) -> bool:
        """Send one session update and wait for the server to apply it. Base: no-op.

        The Hugging Face backend has no session.update semantics we control, so
        the base reports success without sending — exactly the shape
        `_push_turn_detection_update` already uses.
        """
        return True

    async def _push_mode_update(self) -> bool:
        """Apply the current mode to the live session. Base: no-op returning True."""
        return True

    def _note_session_updated(self) -> None:
        """Handle one `session.updated`, paying older debts before the waiter.

        `session.updated` does not echo the client `event_id`, so it can only be
        matched positionally — and positional matching is wrong unless every
        acknowledgement the server still owes us is accounted for first.
        Precedence, and both arms are load-bearing (Codex round 3, findings 5
        and 6):

        1. **Unmatched acks first.** `_session_update_ack_debt` counts updates
           that were sent with nobody waiting on them: the session-config update
           at connect (sent before the receive loop exists, so its
           acknowledgement necessarily arrives later), its legacy-transcription
           retry, any pre-receive-loop push, and any update whose ack wait timed
           out. Every one of those still produces exactly one `session.updated`
           at some point. Letting one of them resolve a LIVE waiter would tell a
           mode flip its payload had been applied when what the server actually
           acknowledged was the connect config — the exact false-positive the
           whole acknowledged-update design exists to prevent.
        2. **Then the waiter**, which is by definition the only update in flight
           (the lock guarantees single flight).
        """
        if self._session_update_ack_debt > 0:
            self._session_update_ack_debt -= 1
            logger.debug(
                "session.updated matched an unwaited update; %d still outstanding",
                self._session_update_ack_debt,
            )
            return
        self._resolve_session_update(True, None)

    def _resolve_session_update(self, applied: bool, detail: str | None) -> None:
        """Resolve the in-flight session update's waiter, exactly once.

        Called from `_note_session_updated` once older debts are paid, and from
        the `error` branch when the error names the update's own `event_id` —
        that path is correlated, so it bypasses the debt entirely. Safe to call
        when nothing is in flight.
        """
        waiter, self._session_update_waiter = self._session_update_waiter, None
        self._session_update_event_id = None
        if waiter is None or waiter.done():
            return
        if not applied:
            logger.warning("session update rejected by the server: %s", detail)
        waiter.set_result(applied)
```

Add the base fields to `__init__`, next to `_mode_update_seq` from Task 1:

```python
        self._session_update_lock: asyncio.Lock = asyncio.Lock()
        self._session_update_event_id: str | None = None
        self._session_update_waiter: asyncio.Future[bool] | None = None
        # Acknowledgements the server still owes us that nobody is waiting on:
        # the connect-time session config (sent before the receive loop exists),
        # its fallback retry, any pre-receive-loop push, and any update whose
        # ack wait timed out. Each one still produces a `session.updated`
        # eventually, and each must be consumed before a live waiter can be
        # (Codex round 3, findings 5 and 6).
        self._session_update_ack_debt: int = 0
        # Whether the receive loop is running and can therefore observe an
        # acknowledgement at all (Codex round 3, finding 1).
        self._receive_loop_active: bool = False
```

Wire the two receive-loop branches. New branch, next to the other session-level events:

```python
                    if event.type == "session.updated":
                        self._note_session_updated()
```

Set and clear the receive-loop flag in `_run_realtime_session`: `self._receive_loop_active = True` immediately before `async for event in self.connection:` (`:2394`), and `self._receive_loop_active = False` in that session's `finally` (`:2835`, beside `_barge_shutdown`).

Record the debt for the two updates sent before the loop exists — in `_run_realtime_session`, right after each successful pre-loop `conn.session.update(...)` (`:2327` and the fallback at `:2334`):

```python
                    # Sent before the receive loop exists, so its
                    # `session.updated` arrives with nobody waiting on it and
                    # must not be allowed to resolve a later waiter
                    # (Codex round 3, finding 5).
                    self._session_update_ack_debt += 1
```

And in the existing `error` branch (`:2804-2818`), **before** the two existing arms — this is Codex round 1, P1-3: a `session.update` rejection arrives asynchronously as an `error`, and today every non-response error sets `_response_started_or_rejected_event`, which can falsely wake `_response_sender_loop` mid-`response.create`:

```python
                        if (
                            self._session_update_event_id is not None
                            and getattr(err, "event_id", None) == self._session_update_event_id
                        ):
                            # This error belongs to our in-flight session update,
                            # not to any response. Resolve the update's waiter and
                            # keep it out of the response-create synchronization
                            # path entirely.
                            self._resolve_session_update(False, f"{code}: {msg}")
                            continue
```

Change `_get_session_config`'s `instructions=` argument (`:1460`) from `get_session_instructions(self.instance_path)` to `self._mode_instructions()`, and `apply_personality`'s `instructions = get_session_instructions(self.instance_path)` (`:1543`) to `instructions = self._mode_instructions()`.

Change `set_conversation_mode` (Task 1) to await the richer update (the `await` was established in Task 1; only the method name changes):

```python
        if self.connection is not None:
            if not await self._push_mode_update():
                # The local mode still stands: the answer gate, the barge policy
                # and the record log are all enforced client-side. What is lost
                # is the model's own knowledge of the mode and its tool surface,
                # which the next reconnect restores.
                logger.warning("conversation mode %s applied locally only", target.value)
```

In `openai_realtime.py`, add the imports and the override directly after `_push_turn_detection_update` (`:516`):

```python
import uuid
from collections.abc import Callable
from openai.types.realtime import RealtimeAudioConfigParam, RealtimeSessionCreateRequestParam
```

(`Callable` is already imported in `openai_realtime.py` at `:20`; add `uuid` and the two `openai.types.realtime` names. `huggingface_realtime.py` already imports both types and `uuid`.)

```python
# How long an ordered session update waits for `session.updated` (or a matching
# `error`) before giving up. The tool call that triggered it is holding a turn
# open, so this has to be short enough not to feel like a hang and long enough
# to cover a normal round trip.
_SESSION_UPDATE_ACK_TIMEOUT_S: Final[float] = 5.0
```

```python
    async def _apply_session_update(
        self,
        build_session: Callable[[], RealtimeSessionCreateRequestParam | None],
        *,
        what: str,
    ) -> bool:
        """Build, send and confirm one session update, all under one lock.

        The single ordered, single-flight update mechanism (design decision 9;
        Codex round 1 P1-1/P1-3/P1-4/P2-9, tightened in round 2 2a-1/2a-2).

        Two properties, and both need the lock to be held across the WHOLE
        operation — which is why this takes a BUILDER rather than a payload:

        * **Ordering.** `build_session()` runs here, inside the lock, so the
          snapshot it takes cannot go stale between being built and being sent.
          An earlier design released the lock between the two and a newer flip
          could overtake the older one on the wire (round 2, 2a-1).
        * **Single flight.** `session.updated` does not echo the client
          `event_id`, so "resolve the update in flight" is only sound while
          exactly one can be in flight. Every live-session caller —
          `_push_mode_update`, `_push_turn_detection_update`, `change_voice`,
          `apply_personality` — comes through here for that reason. The one
          exemption is the initial `session.update` in `_run_realtime_session`,
          which runs before the receive loop exists and therefore before any
          waiter can be installed.

        The `event_id` is still stamped, because an `error` names the event it
        rejected and that is how a rejection is told apart from an unrelated
        server error.

        `build_session()` returning None means the caller was superseded while
        it queued: nothing is sent and the call reports success, because the
        newer update is the one that should land.

        **No ack to wait for before the receive loop runs** (Codex round 3,
        finding 1). The no-greeting startup path releases the boot gate — and so
        pushes turn detection — from `_send_startup_greeting_prompt`, which runs
        before `async for event in self.connection`. Waiting there would burn the
        full five seconds and log a failure for an update that was fine. So when
        the loop is not yet active the update is sent and reported applied, and
        the acknowledgement it will eventually produce is recorded as debt.
        """
        if not self.connection:
            return False
        async with self._session_update_lock:
            session = build_session()
            if session is None:
                return True
            event_id = f"appupd_{uuid.uuid4().hex}"
            waiting = self._receive_loop_active
            waiter: asyncio.Future[bool] | None = None
            if waiting:
                loop = asyncio.get_running_loop()
                waiter = loop.create_future()
                self._session_update_event_id = event_id
                self._session_update_waiter = waiter
            try:
                await self.connection.session.update(session=session, event_id=event_id)
            except Exception as exc:  # noqa: BLE001 - a failed update must not kill the caller
                logger.warning("Failed to send the %s session update: %s", what, exc)
                self._session_update_event_id = None
                self._session_update_waiter = None
                return False
            if waiter is None:
                # Sent before the receive loop could observe an acknowledgement.
                # It will arrive once the loop starts, with nobody waiting on
                # it, so it is booked as debt rather than allowed to resolve
                # whichever waiter happens to exist by then.
                self._session_update_ack_debt += 1
                logger.info("session updated (%s, sent before the receive loop)", what)
                return True
            try:
                applied = await asyncio.wait_for(waiter, timeout=_SESSION_UPDATE_ACK_TIMEOUT_S)
            except asyncio.TimeoutError:
                # The acknowledgement is late, not absent: it will still arrive,
                # and if it were allowed to resolve the NEXT update's waiter that
                # update would be told it had been applied on the strength of
                # this one's ack (Codex round 3, finding 6). One unit of debt
                # makes the next `session.updated` pay for this update instead.
                self._session_update_ack_debt += 1
                logger.warning(
                    "The %s session update was never acknowledged within %.1fs; "
                    "the server may still be running the previous session shape",
                    what,
                    _SESSION_UPDATE_ACK_TIMEOUT_S,
                )
                self._session_update_event_id = None
                self._session_update_waiter = None
                return False
            if applied:
                logger.info("session updated (%s)", what)
            return applied
```

Convert the three existing live-session callers to the same mechanism (Codex round 2, 2a-2) — the payloads are unchanged, only the send is:

```python
    async def _push_turn_detection_update(self) -> None:
        """Apply the current mode's turn detection to the live session."""
        if not self.connection:
            return
        if getattr(self, "_boot_gate_active", False):
            logger.debug("boot gate is closed; deferring the turn-detection push to its release")
            return

        def _build() -> RealtimeSessionCreateRequestParam | None:
            audio_input = self._get_session_config(tool_specs=[])["audio"]["input"]
            return {"type": "realtime", "audio": RealtimeAudioConfigParam(input=audio_input)}

        await self._apply_session_update(_build, what="turn detection")
```

`change_voice` and `apply_personality` keep every line they have except the raw `await self.connection.session.update(session=…)`, which becomes a builder returning that same payload plus `await self._apply_session_update(_build, what=…)`. `apply_personality` keeps its `_restart_session()` follow-up and both of its "will take effect on next connection" fallbacks unchanged.


```python
    async def _push_mode_update(self) -> bool:
        """Apply the current conversation mode to the live session.

        One narrow update carrying the three things a mode owns: its rules block
        (`instructions`), its tool surface (`tools`) and its turn detection
        (`audio.input`). Narrow for the reason `_push_turn_detection_update`
        is — never `model` (immutable) or `voice` (rejected once audio has been
        produced) — and the whole `audio.input` block is sent rather than
        `turn_detection` alone so a server treating the nested object as a
        replacement cannot strip the format, transcription or noise-reduction
        settings.

        While the boot gate is closed the turn-detection half is left out
        entirely: the gate owns turn detection until it opens, and
        `_finish_boot_gate` rebuilds and sends the current mode's VAD on
        release. The instructions and tools still go now — they are what the
        model needs before it speaks.

        Coalescing (Codex round 1, P1-4): each call takes a ticket from
        `_mode_update_seq` before queueing on the update lock, and the builder —
        which `_apply_session_update` runs INSIDE that lock (round 2, 2a-1) —
        drops itself if a newer call took a ticket while this one waited. The
        payload is built from live state in the same locked region that sends
        it, so an older snapshot can never land on top of a newer one.
        """
        if not self.connection:
            return False
        self._mode_update_seq += 1
        ticket = self._mode_update_seq
        mode = self._conversation_mode

        def _build() -> RealtimeSessionCreateRequestParam | None:
            if ticket != self._mode_update_seq:
                logger.debug("mode update %d superseded by %d; dropping", ticket, self._mode_update_seq)
                return None
            # `mode` captured above is still correct here: a flip since then
            # would have taken a newer ticket and the guard above would have
            # returned None.
            session: RealtimeSessionCreateRequestParam = {
                "type": "realtime",
                "instructions": self._mode_instructions(),
                "tools": to_realtime_tools_config(get_tool_specs(exclusion_list=self._mode_tool_exclusions())),
            }
            if getattr(self, "_boot_gate_active", False):
                logger.debug("boot gate is closed; deferring the mode update's turn detection to its release")
            else:
                audio_input = self._get_session_config(tool_specs=[])["audio"]["input"]
                session["audio"] = RealtimeAudioConfigParam(input=audio_input)
            logger.info(
                "Tools in session (%s): %s",
                mode.value,
                [tool["name"] for tool in session["tools"]],
            )
            return session

        return await self._apply_session_update(_build, what=f"conversation mode {mode.value}")
```

(`what=` is evaluated at the call site, from the `mode` captured with the ticket — which is the mode that actually gets sent, because any later flip takes a newer ticket and the builder then returns `None`.)

**Log-line note for `feature_list.json` (Task 12):** the acknowledged path logs `session updated (conversation mode <value>)` from `_apply_session_update`, not the older `session turn_detection updated: party=…` line. Cite the new wording in the verification rows.

Add `get_tool_specs` and `to_realtime_tools_config` to `openai_realtime.py`'s imports (`from reachy_companion.tools.core_tools import ToolSpec, get_tool_specs` and add `to_realtime_tools_config` to the `huggingface_realtime` import list).

- [ ] **Step 4: Run**

Run: `python -m pytest tests/test_prompts_hardening.py tests/test_conversation_modes.py tests/test_openai_realtime_config.py tests/test_boot_gate.py tests/test_personality_routes.py -v`
Expected: PASS. Then `python -m pytest` — full suite green.

- [ ] **Step 5: Lint, typecheck, commit**

```bash
ruff check . && mypy --strict src
git add reachy_companion/src reachy_companion/tests
git commit -m "feat(modes): per-mode prompt block and a narrow live mode update"
```

---

### Task 4: RECORD mode's room transcript log

**Files:**
- Create: `reachy_companion/src/reachy_companion/record_mode.py`
- Modify: `reachy_companion/src/reachy_companion/tools/core_tools.py` (`ToolDependencies`, next to `session_transcript` `:78`)
- Modify: `reachy_companion/src/reachy_companion/huggingface_realtime.py` (`_emit_transcript` override; `set_conversation_mode`; `shutdown()` `:2920-2946`)
- Create test: `reachy_companion/tests/test_record_mode.py`

**Interfaces:**
- Produces (`reachy_companion.record_mode`):
  - `RECORD_LOG_MAX_ITEMS: Final[int] = 2000`
  - `record_room_transcript(deps: ToolDependencies, role: str, text: str) -> None` — appends `(role, cleaned_text, time.monotonic())`; skips empty text and text starting with `"[error]"`.
  - `clear_record_log(deps: ToolDependencies) -> None`
- Produces (`ToolDependencies`): `record_log: deque[tuple[str, str, float]] = field(default_factory=lambda: deque(maxlen=2000))`
- Consumes: `ConversationMode` (Task 1), `self._conversation_mode`, base `ConversationHandler._emit_transcript(role, text, final=True)` (`conversation_handler.py:59`).
- Contract for Task 5: `deps.record_log` entries are `(role, text, monotonic_stamp)` with `role` in `{"user", "assistant"}`, oldest first, capped at 2000 with drop-oldest.
- **Untouched by this task:** `deps.session_transcript` (maxlen=40) and `sleep_summary.record_transcript` — the D-027 sleep summary keeps its accepted-turns-only rule.

- [ ] **Step 1: Write the failing tests** — new file `reachy_companion/tests/test_record_mode.py`:

```python
"""紀錄模式: the room transcript log.

Deliberately unlike `deps.session_transcript` (maxlen=40, accepted turns only,
feeds the D-027 sleep summary): the record log keeps EVERY final transcript,
user and assistant, answered and unanswered, for the length of one visit.
"""

import asyncio
from types import SimpleNamespace
from collections import deque
from unittest.mock import MagicMock

import pytest
from test_solo_barge import _install_barge_state

from reachy_companion.record_mode import (
    RECORD_LOG_MAX_ITEMS,
    clear_record_log,
    record_room_transcript,
)
from reachy_companion.conversation_mode import ConversationMode
from reachy_companion.openai_realtime import OpenAIRealtimeHandler
from reachy_companion.tools.core_tools import ToolDependencies


def _deps() -> SimpleNamespace:
    return SimpleNamespace(record_log=deque(maxlen=RECORD_LOG_MAX_ITEMS))


def _record_handler(mode: ConversationMode = ConversationMode.RECORD) -> OpenAIRealtimeHandler:
    h = OpenAIRealtimeHandler.__new__(OpenAIRealtimeHandler)
    h._conversation_mode = mode
    h._turn_mode = mode
    h._turn_modes = {}
    h._mode_update_seq = 0
    h._session_update_lock = asyncio.Lock()
    h._session_update_event_id = None
    h._session_update_waiter = None
    h._session_update_ack_debt = 0
    # Default to "the loop is running", so an update waits for its ack; the
    # pre-receive-loop tests set this back to False explicitly.
    h._receive_loop_active = True
    h._handler_loop = None
    h._party_last_accept_at = None
    h._party_speech_open = False
    h._party_utterance_seq = 0
    h._party_barge_task = None
    h._active_response_id = None
    h._cancelled_response_ids = deque(maxlen=8)
    h._response_done_event = asyncio.Event()
    h._response_done_event.set()
    h.connection = None
    h._transcript_observer = None
    h.deps = SimpleNamespace(
        reachy_mini=MagicMock(),
        movement_manager=MagicMock(),
        record_log=deque(maxlen=RECORD_LOG_MAX_ITEMS),
        sleep_requested=False,
    )
    _install_barge_state(h)
    h._clear_queue = MagicMock()
    return h


def test_tool_dependencies_ship_a_bounded_record_log() -> None:
    deps = ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())
    assert deps.record_log.maxlen == RECORD_LOG_MAX_ITEMS == 2000
    # The sleep-summary buffer keeps its own, much smaller, bound.
    assert deps.session_transcript.maxlen == 40


def test_record_room_transcript_stamps_and_skips_noise() -> None:
    deps = _deps()
    record_room_transcript(deps, "user", "  下週三再開一次  ")
    record_room_transcript(deps, "assistant", "")
    record_room_transcript(deps, "assistant", "[error] tool blew up")
    assert [(role, text) for role, text, _ in deps.record_log] == [("user", "下週三再開一次")]
    assert isinstance(deps.record_log[0][2], float)


def test_record_log_drops_the_oldest_at_the_cap() -> None:
    deps = _deps()
    for index in range(RECORD_LOG_MAX_ITEMS + 5):
        record_room_transcript(deps, "user", f"line-{index}")
    assert len(deps.record_log) == RECORD_LOG_MAX_ITEMS
    assert deps.record_log[0][1] == "line-5"


def test_clear_record_log_empties_it() -> None:
    deps = _deps()
    record_room_transcript(deps, "user", "abc")
    clear_record_log(deps)
    assert not deps.record_log


def test_emit_transcript_records_every_role_in_record_mode() -> None:
    h = _record_handler()
    h._emit_transcript("user", "他說下週三", True)
    h._emit_transcript("assistant", "好的", True)
    assert [(role, text) for role, text, _ in h.deps.record_log] == [
        ("user", "他說下週三"),
        ("assistant", "好的"),
    ]


def test_emit_transcript_records_nothing_outside_record_mode() -> None:
    for mode in (ConversationMode.ONE_ON_ONE, ConversationMode.GROUP):
        h = _record_handler(mode)
        h._emit_transcript("user", "他說下週三", True)
        assert not h.deps.record_log


def test_emit_transcript_ignores_partials() -> None:
    h = _record_handler()
    h._emit_transcript("user_partial", "他說下", False)
    assert not h.deps.record_log


def test_emit_transcript_still_reaches_the_observer() -> None:
    """Recording must not swallow the console/JSON-RPC broadcast."""
    seen: list[tuple[str, str, bool]] = []
    h = _record_handler()
    h._transcript_observer = lambda role, text, final: seen.append((role, text, final))
    h._emit_transcript("user", "他說下週三", True)
    assert seen == [("user", "他說下週三", True)]


@pytest.mark.asyncio
async def test_leaving_record_mode_clears_the_log() -> None:
    """In-memory per visit AND per stay in the mode: no files, no export."""
    h = _record_handler()
    record_room_transcript(h.deps, "user", "會議內容")
    await h.set_conversation_mode("one_on_one")
    assert not h.deps.record_log


@pytest.mark.asyncio
async def test_entering_record_mode_keeps_an_empty_log() -> None:
    h = _record_handler(ConversationMode.ONE_ON_ONE)
    await h.set_conversation_mode("record")
    assert not h.deps.record_log


async def _drive_shutdown(handler, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run `shutdown()` on a `__new__`-built handler with the I/O stubbed out."""
    from reachy_companion import huggingface_realtime as hf_mod

    handler._sleep_summary_done = True
    handler._hanova_session = 0
    handler.tool_manager = SimpleNamespace(shutdown=_noop_async())
    handler.partial_transcript_task = None
    handler.output_queue = asyncio.Queue()
    handler.connection = None
    monkeypatch.setattr(hf_mod, "on_session_shutdown", _noop_async())
    monkeypatch.setattr(handler, "_barge_shutdown", _noop_async())
    await handler.shutdown()


@pytest.mark.asyncio
async def test_going_to_sleep_clears_the_record_log(monkeypatch: pytest.MonkeyPatch) -> None:
    """The visit ends at sleep — and nothing recorded outlives the visit."""
    h = _record_handler()
    record_room_transcript(h.deps, "user", "會議內容")
    h.deps.sleep_requested = True
    await _drive_shutdown(h, monkeypatch)
    assert not h.deps.record_log


@pytest.mark.asyncio
async def test_a_settings_restart_keeps_the_record_log(monkeypatch: pytest.MonkeyPatch) -> None:
    """`shutdown()` also runs for settings/backend restarts, mid-meeting.

    D-027 already refuses to write a sleep summary on those; throwing away a
    recording that is still in progress is the same mistake (Codex round 1,
    P1-5).
    """
    h = _record_handler()
    record_room_transcript(h.deps, "user", "會議內容")
    h.deps.sleep_requested = False
    await _drive_shutdown(h, monkeypatch)
    assert [text for _role, text, _ts in h.deps.record_log] == ["會議內容"]


def _noop_async():
    async def _inner(*args: object, **kwargs: object) -> None:
        return None

    return _inner
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_record_mode.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'reachy_companion.record_mode'`.

- [ ] **Step 3: Implement**

New file `reachy_companion/src/reachy_companion/record_mode.py`:

```python
"""紀錄模式: the room transcript log and its summarizer (2026-08-31 plan).

Deliberately NOT `deps.session_transcript`. That deque is `maxlen=40`,
accepted-turns-only, and exists to feed the D-027 sleep summary — a per-person
「上次聊天」 callback. A meeting record is the opposite on both axes: it wants
every line anyone said, including the ones the answer gate declined, and forty
lines is a few minutes.

In memory, for the length of one visit. Cleared when the mode is left and again
at handler shutdown; never written to disk, never exported (PRD non-goal:
long-term memory).
"""

from __future__ import annotations
import time
import logging
from typing import Final

from reachy_companion.tools.core_tools import ToolDependencies


logger = logging.getLogger(__name__)

# Bound on the room log held in `ToolDependencies.record_log`. The deque is
# built there with a literal maxlen — core_tools cannot import this module
# without a cycle (Tool classes import core_tools) — so keep the two in step.
RECORD_LOG_MAX_ITEMS: Final[int] = 2000


def record_room_transcript(deps: ToolDependencies, role: str, text: str) -> None:
    """Append one finalized utterance, stamped, to the room log.

    Same skip rules as `sleep_summary.record_transcript`: an empty line carries
    nothing, and a tool's own `[error] …` text is plumbing, not conversation.
    """
    cleaned = text.strip()
    if not cleaned or cleaned.startswith("[error]"):
        return
    deps.record_log.append((role, cleaned, time.monotonic()))


def clear_record_log(deps: ToolDependencies) -> None:
    """Drop the room log. Called on mode exit and at handler shutdown."""
    if deps.record_log:
        logger.info("record log cleared (%d lines)", len(deps.record_log))
    deps.record_log.clear()
```

In `tools/core_tools.py`, next to `session_transcript` (`:78`):

```python
    # 紀錄模式's room log (record_mode.py): EVERY final transcript, user and
    # assistant, answered and unanswered, for one visit. Unlike
    # `session_transcript` above it is not the sleep summary's input and it does
    # not filter by the answer gate — a meeting record wants the lines the robot
    # decided not to answer most of all. The maxlen literal must stay equal to
    # record_mode.RECORD_LOG_MAX_ITEMS; importing it here would be a cycle.
    record_log: deque[tuple[str, str, float]] = field(default_factory=lambda: deque(maxlen=2000))
```

In `huggingface_realtime.py`, import `clear_record_log, record_room_transcript` from `reachy_companion.record_mode` and add the override next to `_mode_instructions`:

```python
    def _emit_transcript(self, role: str, text: str, final: bool = True) -> None:
        """Forward the transcript, and in 紀錄模式 keep a copy of every final line.

        This one override covers all four final-transcript sites — the rolled-back
        solo barge (`:1321`), the answer-gate denial (`:2606`), the answered user
        turn (`:2645`) and the assistant's own transcript (`:2682`) — plus any
        added later. Partials never reach here as `final`, so the log holds
        finished lines only.
        """
        if final and text and self._conversation_mode is ConversationMode.RECORD:
            record_room_transcript(self.deps, role, text)
        super()._emit_transcript(role, text, final)
```

In `set_conversation_mode` (Task 1), immediately after `self._conversation_mode = target`:

```python
        if previous is ConversationMode.RECORD:
            # The room log is scoped to one stay in the mode as well as to one
            # visit: leaving 紀錄模式 ends the recording, and a later 紀錄模式
            # must not open on the last meeting's lines.
            clear_record_log(self.deps)
```

In `shutdown()`, immediately after the sleep-summary block (`:2945`), **gated on `deps.sleep_requested` exactly as the sleep summary above it is** (Codex round 1, P1-5):

```python
        # 紀錄模式's room log is per visit and lives only in memory. `shutdown()`
        # also runs for settings and backend restarts (console.py:307, :697),
        # which are mid-visit — D-027 already refuses to summarize on those, and
        # for the same reason they must not throw away a meeting that is still
        # happening. Only the sleep that ends the visit clears it.
        if self.deps.sleep_requested:
            clear_record_log(self.deps)
```

- [ ] **Step 4: Run**

Run: `python -m pytest tests/test_record_mode.py tests/test_conversation_modes.py tests/test_sleep_summary.py tests/test_huggingface_realtime.py -v`
Expected: PASS. Then `python -m pytest` — full suite green.

- [ ] **Step 5: Lint, typecheck, commit**

```bash
ruff check . && mypy --strict src
git add reachy_companion/src reachy_companion/tests
git commit -m "feat(modes): record-mode room transcript log"
```

---

### Task 5: `summarize_conversation` tool

**Files:**
- Modify: `reachy_companion/src/reachy_companion/record_mode.py` (add the summarizer)
- Create: `reachy_companion/src/reachy_companion/tools/summarize_conversation.py`
- Modify: `reachy_companion/profiles/_reachy_companion_locked_profile/profile.md` (`default_tools`)
- Modify test: `reachy_companion/tests/test_record_mode.py`

**Interfaces:**
- Produces (`record_mode`):
  - `RECORD_EMPTY_SUMMARY: Final[str] = "還沒有記錄到內容。"`
  - `async summarize_record_log(deps: ToolDependencies, *, client: Any | None = None) -> str` — never raises; returns the Chinese summary text, or `RECORD_EMPTY_SUMMARY` when the log is empty, or a one-line Chinese failure message when the call fails.
  - `RECORD_SUMMARY_FAILED: Final[str] = "剛剛的記錄整理失敗了，要不要再說一次？"`
- Produces (tool): `reachy_companion.tools.summarize_conversation.SummarizeConversation`, `name = "summarize_conversation"`, no parameters, returning `{"summary_text": <str>, "speak_verbatim": True, "lines": <int>}`.
- Consumes: `hanova.images.build_client() -> AsyncOpenAI | None` (`hanova/images.py:26`), `env_float` (`audio/envparse.py`), `deps.record_log` (Task 4).
- Env produced: `RECORD_SUMMARY_TIMEOUT_S` (default `20.0`, clamped 1.0–60.0). Model reuses `MEMORY_LAST_CHAT_MODEL` or `"gpt-5-mini"` — the same summarizer family as the sleep summary, and no new knob.
- Contract for Task 8: the tool name is exactly `summarize_conversation`; it is on RECORD's allowlist and in the always-on static core.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_record_mode.py`:

```python
# --------------------------------------------------------------------------
# summarize_conversation (2026-08-31 plan, Task 5)
# --------------------------------------------------------------------------


class _FakeChatClient:
    """Minimal async stand-in for `hanova.images.build_client()`'s AsyncOpenAI."""

    def __init__(self, content: str | None = "會議重點：下週三再開一次。", raises: bool = False) -> None:
        self._content = content
        self._raises = raises
        self.seen_prompt: str | None = None
        self.seen_model: str | None = None
        self.closed = False

        async def _create(**kwargs: object) -> object:
            if self._raises:
                raise RuntimeError("summarizer down")
            messages = kwargs["messages"]
            self.seen_prompt = messages[1]["content"]  # type: ignore[index]
            self.seen_model = kwargs["model"]  # type: ignore[assignment]
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=self._content))]
            )

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=_create))

    async def __aenter__(self) -> "_FakeChatClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_summarize_returns_the_friendly_line_for_an_empty_log() -> None:
    from reachy_companion.record_mode import RECORD_EMPTY_SUMMARY, summarize_record_log

    deps = _deps()
    assert await summarize_record_log(deps, client=_FakeChatClient()) == RECORD_EMPTY_SUMMARY


@pytest.mark.asyncio
async def test_summarize_feeds_every_logged_line_to_the_model() -> None:
    from reachy_companion.record_mode import summarize_record_log

    deps = _deps()
    record_room_transcript(deps, "user", "下週三再開一次")
    record_room_transcript(deps, "assistant", "好的")
    client = _FakeChatClient()
    summary = await summarize_record_log(deps, client=client)
    assert summary == "會議重點：下週三再開一次。"
    assert "下週三再開一次" in (client.seen_prompt or "")
    assert "reachy: 好的" in (client.seen_prompt or "")
    assert client.seen_model == "gpt-5-mini"
    assert client.closed is True


@pytest.mark.asyncio
async def test_summarize_never_raises_when_the_call_fails() -> None:
    from reachy_companion.record_mode import RECORD_SUMMARY_FAILED, summarize_record_log

    deps = _deps()
    record_room_transcript(deps, "user", "下週三再開一次")
    assert await summarize_record_log(deps, client=_FakeChatClient(raises=True)) == RECORD_SUMMARY_FAILED


@pytest.mark.asyncio
async def test_summarize_handles_a_missing_client() -> None:
    from reachy_companion.record_mode import RECORD_SUMMARY_FAILED, summarize_record_log

    deps = _deps()
    record_room_transcript(deps, "user", "下週三再開一次")
    assert await summarize_record_log(deps, client=None) == RECORD_SUMMARY_FAILED


@pytest.mark.asyncio
async def test_summarize_respects_the_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    from reachy_companion.record_mode import RECORD_SUMMARY_FAILED, summarize_record_log

    monkeypatch.setenv("RECORD_SUMMARY_TIMEOUT_S", "1.0")
    deps = _deps()
    record_room_transcript(deps, "user", "下週三再開一次")

    class _SlowClient(_FakeChatClient):
        def __init__(self) -> None:
            super().__init__()

            async def _create(**kwargs: object) -> object:
                await asyncio.sleep(5.0)
                return None

            self.chat = SimpleNamespace(completions=SimpleNamespace(create=_create))

    assert await summarize_record_log(deps, client=_SlowClient()) == RECORD_SUMMARY_FAILED


@pytest.mark.asyncio
async def test_tool_returns_the_verbatim_envelope() -> None:
    from reachy_companion.tools.summarize_conversation import SummarizeConversation

    deps = _deps()
    record_room_transcript(deps, "user", "下週三再開一次")
    result = await SummarizeConversation()(deps, client=_FakeChatClient())
    assert result == {
        "summary_text": "會議重點：下週三再開一次。",
        "speak_verbatim": True,
        "lines": 1,
    }


def test_tool_description_names_the_envelope_and_the_triggers() -> None:
    from reachy_companion.tools.summarize_conversation import SummarizeConversation

    description = SummarizeConversation.description
    for phrase in ("speak_verbatim", "summary_text", "幫我總結", "Do NOT use when", "紀錄模式"):
        assert phrase in description
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_record_mode.py -k "summarize or tool_returns or tool_description" -v`
Expected: FAIL — `ImportError: cannot import name 'summarize_record_log' from 'reachy_companion.record_mode'`.

- [ ] **Step 3: Implement**

**Merge these into `record_mode.py`'s existing TOP import block** — do not append them below the module's definitions, which is an `E402`/import-order failure under this repo's `ruff` config and would stop the task ending green (Codex round 1, P1-6). After the edit the block reads:

```python
from __future__ import annotations
import os
import time
import asyncio
import logging
from typing import Any, Final

from reachy_companion.audio.envparse import env_float
from reachy_companion.tools.core_tools import ToolDependencies
```

Then append to the body of `record_mode.py`:

```python
RECORD_EMPTY_SUMMARY: Final[str] = "還沒有記錄到內容。"
RECORD_SUMMARY_FAILED: Final[str] = "剛剛的記錄整理失敗了，要不要再說一次？"

_SUMMARY_SYSTEM_PROMPT: Final[str] = (
    "你是會議記錄整理員。根據下面這段對話記錄，用臺灣繁體中文整理出重點，"
    "念出來就能聽懂的口語段落，不要用條列符號、不要用 Markdown。"
    "優先寫：講了哪些主題、做了什麼決定、誰要做什麼、還沒有結論的事。"
    "只根據記錄，不要編造，也不要把某句話歸給記錄裡沒有寫出名字的人。"
    "記錄很短的時候就簡短講完，不要硬湊。"
)
# The summarizer runs after the fact on a much bigger input than the sleep
# summary's forty lines, so it gets its own, longer budget (the brief's ~20 s).
_SUMMARY_TIMEOUT_DEFAULT_S: Final[float] = 20.0


def _summary_model() -> str:
    """The summarizer model — the same small one the sleep summary uses."""
    return os.getenv("MEMORY_LAST_CHAT_MODEL", "").strip() or "gpt-5-mini"


async def summarize_record_log(deps: ToolDependencies, *, client: Any | None = None) -> str:
    """Summarize the room log in Traditional Chinese. Never raises.

    Same client/model/timeout shape as `sleep_summary.write_sleep_summaries`
    (`sleep_summary.py:141-162`): a plain Chat Completions call on a small
    model, the client used as `async with` so its pool closes, and the whole
    body inside one `try` — a summary that fails must cost a sentence, never the
    conversation.

    Returns the text for the model to read aloud verbatim, so every failure mode
    also has to return something sayable.
    """
    lines = list(deps.record_log)
    if not lines:
        return RECORD_EMPTY_SUMMARY
    if client is None:
        from reachy_companion.hanova.images import build_client

        client = build_client()
    if client is None:
        logger.info("Record summary skipped: no OpenAI client available.")
        return RECORD_SUMMARY_FAILED
    rendered = "\n".join(f"{'user' if role == 'user' else 'reachy'}: {text}" for role, text, _ in lines)
    timeout_s = env_float("RECORD_SUMMARY_TIMEOUT_S", _SUMMARY_TIMEOUT_DEFAULT_S, lo=1.0, hi=60.0)
    try:
        async with client:
            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model=_summary_model(),
                    messages=[
                        {"role": "system", "content": _SUMMARY_SYSTEM_PROMPT},
                        {"role": "user", "content": f"對話記錄（共 {len(lines)} 句）：\n{rendered}"},
                    ],
                ),
                timeout=timeout_s,
            )
        summary = (response.choices[0].message.content or "").strip()
    except Exception as exc:  # noqa: BLE001 — a summary must never break the turn
        logger.warning("Record summary failed: %s", type(exc).__name__)
        return RECORD_SUMMARY_FAILED
    if not summary:
        return RECORD_SUMMARY_FAILED
    logger.info("Record summary written from %d logged lines.", len(lines))
    return summary
```

New file `reachy_companion/src/reachy_companion/tools/summarize_conversation.py`:

```python
"""Read back a summary of what 紀錄模式 recorded. Filename == Tool.name.

The summarizer itself lives in `record_mode`; this is the voice surface. The
result is a verbatim envelope (research doc §C3): a raw string plus a separate
"say it exactly" instruction is the shape the mini tier paraphrases, so the
authoritative text is a named field and the flag travels with it.
"""

from __future__ import annotations
import logging
from typing import Any, Dict

from reachy_companion.record_mode import summarize_record_log
from reachy_companion.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


class SummarizeConversation(Tool):
    """Summarize the running record of this visit and hand it back verbatim."""

    name = "summarize_conversation"
    description = (
        "Summarize everything Reachy has heard and said in this visit, using the running record kept while "
        "紀錄模式 (record mode) is on. "
        "Use when: the user asks for a summary or a recap of what was said — 「幫我總結」「剛剛講了什麼」"
        "「做個會議記錄」「唸一下重點」「summarize what we said」「recap the meeting」. "
        "Do NOT use when: the user asks what YOU remember about a person or a fact — that is the memory tools. "
        "Do NOT use when: the user asks what you can see — that is camera or look_around. "
        "The result is an envelope: when `speak_verbatim` is true, read `summary_text` out loud EXACTLY as "
        "returned, in 台灣中文, and add nothing of your own."
    )
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Summarize `deps.record_log` and return the verbatim envelope."""
        lines = len(deps.record_log)
        logger.info("Tool call: summarize_conversation over %d recorded line(s)", lines)
        summary = await summarize_record_log(deps, client=kwargs.get("client"))
        return {"summary_text": summary, "speak_verbatim": True, "lines": lines}
```

In `profiles/_reachy_companion_locked_profile/profile.md`, add `"summarize_conversation",` to `default_tools` immediately after `"set_conversation_mode",`, and make the same insertion in `tests/test_profile.py::EXPECTED_TOOLS`.

- [ ] **Step 4: Run**

Run: `python -m pytest tests/test_record_mode.py tests/test_profile.py tests/test_profile_toolsets.py tests/test_external_loading.py -v`
Expected: PASS. Then `python -m pytest` — full suite green.

- [ ] **Step 5: Lint, typecheck, commit**

```bash
ruff check . && mypy --strict src
git add reachy_companion/src reachy_companion/tests reachy_companion/profiles
git commit -m "feat(tools): summarize_conversation over the record log"
```

---

### Task 6: Consolidate the CRUD/action tool families

**Files:**
- Create: `reachy_companion/src/reachy_companion/tools/tool_family.py`
- Create: `reachy_companion/src/reachy_companion/tools/calendar.py`, `tasks.py`, `drive.py`, `nas.py`, `music.py`, `tv.py`
- Modify: `reachy_companion/profiles/_reachy_companion_locked_profile/profile.md` (`default_tools`)
- Modify test: `reachy_companion/tests/test_profile.py` (`EXPECTED_TOOLS`)
- Create test: `reachy_companion/tests/tools/test_tool_families.py`
- **Unchanged, deliberately:** the 18 original modules under `tools/` and all their tests; `hanova/settings.py`'s `TOOL_PREREQS` and `TOOL_GROUPS` (each sub-tool still calls `settings.tool_status(self.name)` with its own name, so every prerequisite row keeps working).

**Interfaces:**
- Produces (`reachy_companion.tools.tool_family`): `async dispatch_family(*, family: str, action: Any, actions: Mapping[str, Tool], deps: ToolDependencies, kwargs: dict[str, Any]) -> dict[str, Any]`. **It validates the action name and nothing else** (Codex round 1, P2-5).
- Produces six family tools, each a `Tool` subclass whose `parameters_schema` is `{"type": "object", "properties": {"action": {...enum...}, **union_of_sub_tool_properties}, "required": ["action"]}`:

| tool | class | actions → delegate |
|---|---|---|
| `calendar` | `Calendar` | `add`→`CalendarAdd`, `list`→`CalendarList`, `delete`→`CalendarDelete` |
| `tasks` | `Tasks` | `add`→`TaskAdd`, `list`→`TaskList`, `complete`→`TaskComplete`, `delete`→`TaskDelete` |
| `drive` | `Drive` | `list`→`DriveList`, `trash`→`DriveTrash`, `upload`→`DriveUpload` |
| `nas` | `Nas` | `query`→`NasVideoQuery`, `play`→`PlayNasVideo`, `play_folder`→`NasPlayFolder`, `skip`→`NasSkip` |
| `music` | `Music` | `play`→`PlayMusic`, `stop`→`StopMusic` |
| `tv` | `Tv` | `play_video`→`PlayVideo`, `show`→`ShowOnTv` |

- **No required-argument validation in the façade** (Codex round 1, P2-5). Every one of the 18 delegates already validates its own arguments — and does so *after* its `settings.tool_status(self.name)` prerequisite check, with its own exact error string (`calendar_add.py:39-48`, `task_add.py:33-39`, `play_music.py:45-51`). A façade-level check would run first, change the observed error text, and hide the "this feature is not configured" answer behind an "argument missing" one for a robot that could not have done the thing either way. Per-action requirements are advertised to the model in the `action` enum's description and in each property's description instead; the delegate remains the only validator. `dispatch_family` therefore takes no `required` mapping and the family classes carry no `REQUIRED` table.

- Property unions (verified against each sub-tool's `parameters_schema`; overlapping names carry the same meaning in every action of their family, so the union is lossless):
  - `calendar`: `summary, start, end, location, days, search, match, confirm`
  - `tasks`: `title, due, notes, include_completed, match, confirm`
  - `drive`: `limit, file_id, name, confirm`
  - `nas`: `year, year_from, year_to, place, keyword, limit, path, top_folder`
  - `music`: `query`
  - `tv`: `query, request`
- Consumes: the 18 existing `Tool` subclasses, imported by class from their own modules. **`needs_response` stays default (`True`) on every family — none of the 18 sets it False.**
- Contract for Tasks 7, 8 and 12: registered tool names after this task are `calendar`, `tasks`, `drive`, `nas`, `music`, `tv` — the 18 sub-tool names are no longer registered and must not appear in any profile, allowlist or core list.
- **Naming caution:** `tools/calendar.py` sits next to the stdlib's `calendar` module. Python 3's absolute imports keep them apart — `reachy_companion.tools.calendar` never shadows `import calendar` — and a grep confirms nothing in `src/` imports the stdlib one today (`grep -rn "^import calendar\|^from calendar" src/` is empty). Keep it that way: inside the package, always import the family as `from reachy_companion.tools.calendar import Calendar`.

- [ ] **Step 1: Write the failing tests** — new file `reachy_companion/tests/tools/test_tool_families.py`:

```python
"""Action-enum tool families: 18 registered tools become 6.

41 tools at the start of a turn is past OpenAI's own "aim for fewer than 20"
and inside the measured degradation zone
(docs/research-mini-tool-calling-2026-08.md §A1). The consolidation is a SCHEMA
refactor: every family façade delegates to the original tool instance, so the
confirmation gates, prerequisite checks and error strings are unchanged and
still covered by those modules' own tests.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from reachy_companion.hanova import settings
from reachy_companion.tools.tv import Tv
from reachy_companion.tools.nas import Nas
from reachy_companion.tools.drive import Drive
from reachy_companion.tools.music import Music
from reachy_companion.tools.tasks import Tasks
from reachy_companion.tools.calendar import Calendar
from reachy_companion.tools.play_music import PlayMusic
from reachy_companion.tools.calendar_add import CalendarAdd
from reachy_companion.tools.calendar_list import CalendarList


_FAMILIES = (Calendar, Tasks, Drive, Nas, Music, Tv)


def _deps() -> SimpleNamespace:
    return SimpleNamespace(reachy_mini=MagicMock(), movement_manager=MagicMock(), instance_path=None)


@pytest.mark.parametrize("family", _FAMILIES)
def test_every_family_has_a_required_action_enum(family) -> None:
    schema = family().parameters_schema
    assert schema["required"] == ["action"]
    actions = schema["properties"]["action"]["enum"]
    assert actions and actions == sorted(set(actions))
    # Every advertised action must have a delegate behind it.
    assert set(actions) == set(family.ACTIONS)


@pytest.mark.parametrize("family", _FAMILIES)
def test_every_family_description_is_symmetric(family) -> None:
    assert "Use when:" in family.description
    assert "Do NOT use when:" in family.description


@pytest.mark.parametrize("family", _FAMILIES)
def test_family_schema_covers_every_delegate_property(family) -> None:
    """A union that dropped a property would silently break that action."""
    properties = set(family().parameters_schema["properties"])
    for tool in family.ACTIONS.values():
        assert set(tool.parameters_schema["properties"]) <= properties


@pytest.mark.asyncio
async def test_family_rejects_an_unknown_action() -> None:
    result = await Calendar()(_deps(), action="explode")
    assert "error" in result and "action must be one of" in result["error"]


@pytest.mark.asyncio
async def test_the_delegate_still_validates_its_own_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    """The façade adds no argument check of its own (Codex round 1, P2-5).

    `calendar_add` answers a missing `start`/`end` with its own sentence, and it
    only gets that far after its `settings.tool_status` prerequisite check. A
    façade-level check would run first, change the wording, and mask "this is
    not configured" behind "you forgot an argument".
    """
    monkeypatch.setattr(settings, "tool_status", lambda name: (True, ""))
    result = await Calendar()(_deps(), action="add", summary="午餐")
    assert result == {"ok": False, "error": "summary, start and end are all required"}


@pytest.mark.asyncio
async def test_the_prerequisite_refusal_wins_over_a_missing_argument(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Original check order preserved: unavailable is answered before args are."""
    monkeypatch.setattr(settings, "tool_status", lambda name: (False, "MUSIC_WHEELS"))
    result = await Music()(_deps(), action="play")
    assert result == settings.unavailable("MUSIC_WHEELS")


@pytest.mark.asyncio
async def test_family_forwards_to_the_original_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    """Delegation, not reimplementation: the sub-tool's own body still runs."""
    seen: list[dict[str, object]] = []

    async def _add(self, deps, **kwargs):
        seen.append(kwargs)
        return {"status": "added"}

    monkeypatch.setattr(CalendarAdd, "__call__", _add)
    result = await Calendar()(
        _deps(), action="add", summary="午餐", start="2026-09-01T12:00", end="2026-09-01T13:00"
    )
    assert result == {"status": "added"}
    assert seen == [{"summary": "午餐", "start": "2026-09-01T12:00", "end": "2026-09-01T13:00"}]


@pytest.mark.asyncio
async def test_family_forwards_a_no_argument_action(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _list(self, deps, **kwargs):
        return {"events": [], "seen": kwargs}

    monkeypatch.setattr(CalendarList, "__call__", _list)
    result = await Calendar()(_deps(), action="list")
    assert result["seen"] == {}


@pytest.mark.asyncio
async def test_family_preserves_the_delegate_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A prerequisite refusal must reach the model exactly as before."""

    async def _play(self, deps, **kwargs):
        return {"error": "play_music unavailable: MUSIC_WHEELS"}

    monkeypatch.setattr(PlayMusic, "__call__", _play)
    result = await Music()(_deps(), action="play", query="周杰倫")
    assert result == {"error": "play_music unavailable: MUSIC_WHEELS"}


def test_the_eighteen_sub_tools_are_no_longer_registered() -> None:
    from reachy_companion.tools.core_tools import get_tools

    registered = set(get_tools())
    for name in (
        "calendar_add", "calendar_list", "calendar_delete",
        "task_add", "task_list", "task_complete", "task_delete",
        "drive_list", "drive_trash", "drive_upload",
        "nas_video_query", "play_nas_video", "nas_play_folder", "nas_skip",
        "play_music", "stop_music", "play_video", "show_on_tv",
    ):
        assert name not in registered, name
    for name in ("calendar", "tasks", "drive", "nas", "music", "tv"):
        assert name in registered, name
```

Also update `tests/test_profile.py::EXPECTED_TOOLS` in this step: delete the 18 sub-tool entries and insert `"music"`, `"tv"`, `"nas"`, `"calendar"`, `"tasks"`, `"drive"` at the positions the profile now lists them. (`EXPECTED_TOOLS` is an ordered tripwire against *unplanned* tool additions; keep it in exact profile order.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/tools/test_tool_families.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'reachy_companion.tools.calendar'`.

- [ ] **Step 3: Implement**

New file `reachy_companion/src/reachy_companion/tools/tool_family.py`:

```python
"""Shared dispatch for action-enum tool families (2026-08-31 tool diet).

Six CRUD/action families went from 18 separately registered tools to 6, because
41 tools at the start of a turn is well past OpenAI's own "aim for fewer than 20
functions available at the start of a turn" and inside the measured degradation
zone (docs/research-mini-tool-calling-2026-08.md §A1).

The consolidation is a SCHEMA refactor, not a behavior change. Each family
validates its action and that action's required arguments, then calls the
ORIGINAL `Tool` instance unchanged — so every confirmation gate
(`hanova.confirm`), every `settings.tool_status(self.name)` prerequisite check,
every retry and every error string still comes from the module that always
produced it, and every existing test of those modules still tests shipped code.

The sub-tool modules stay on disk and simply leave the profile's
`default_tools`: the registry loader imports one module per listed name and
picks up only the `Tool` subclasses *defined* there (`core_tools.py:256-274`),
so a family module importing its delegates registers nothing extra.
"""

from __future__ import annotations
import logging
from typing import Any, Mapping

from reachy_companion.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


async def dispatch_family(
    *,
    family: str,
    action: Any,
    actions: Mapping[str, Tool],
    deps: ToolDependencies,
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    """Route one family call to the tool that has always handled it.

    The ONLY validation here is the action name, because that is the only thing
    the delegates cannot check — they never see it. Argument validation stays
    where it has always lived: inside each delegate, *after* its
    `settings.tool_status(self.name)` prerequisite check, with its own error
    string. Checking arguments here would reorder those two answers and reword
    one of them, which is a behavior change, and this refactor is not allowed to
    be one (Codex round 1, P2-5).

    Everything but `action` is forwarded untouched, including unknown extra
    keys: every delegate reads its arguments with `kwargs.get`, so a stray key
    is inert and a dropped one would not be.
    """
    if not isinstance(action, str) or action not in actions:
        return {"error": f"{family}: action must be one of {sorted(actions)}"}
    forwarded = {key: value for key, value in kwargs.items() if key != "action" and value is not None}
    logger.info("Tool call: %s action=%s", family, action)
    return await actions[action](deps, **forwarded)
```

New file `reachy_companion/src/reachy_companion/tools/calendar.py` — the template every family follows:

```python
"""Google Calendar as one action-enum tool. Filename == Tool.name.

Façade over `calendar_add` / `calendar_list` / `calendar_delete`, which keep
their modules, their names, their `settings.tool_status` prerequisite rows and
their confirmation gate. See `tool_family.py` for why.
"""

from __future__ import annotations
from typing import Any, ClassVar, Dict, Mapping

from reachy_companion.tools.core_tools import Tool, ToolDependencies
from reachy_companion.tools.tool_family import dispatch_family
from reachy_companion.tools.calendar_add import CalendarAdd
from reachy_companion.tools.calendar_list import CalendarList
from reachy_companion.tools.calendar_delete import CalendarDelete


class Calendar(Tool):
    """Add, list and delete calendar events through one tool."""

    name = "calendar"
    ACTIONS: ClassVar[Mapping[str, Tool]] = {
        "add": CalendarAdd(),
        "list": CalendarList(),
        "delete": CalendarDelete(),
    }
    description = (
        "The user's calendar: add an event, read what is coming up, or delete an event. "
        "Use when: the user talks about their schedule, an appointment or a meeting — 「幫我加個行程」"
        "「下週三下午三點跟醫生」「我這禮拜有什麼安排」「把星期五那個會取消」「add it to my calendar」"
        "「what's on my calendar」「cancel that meeting」. "
        "Do NOT use when: the user means a to-do or a reminder with no time on the clock — that is tasks. "
        "Do NOT use when: the user asks about today's date or the current time — just answer. "
        "Pick `action`: `add` needs summary, start and end; `list` optionally takes days and search; "
        "`delete` takes match, and asks the user out loud to confirm before it removes anything."
    )
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "delete", "list"],
                "description": "add 新增行程；list 查看行程；delete 刪除行程（會先口頭確認）。",
            },
            **CalendarAdd.parameters_schema["properties"],
            **CalendarList.parameters_schema["properties"],
            **CalendarDelete.parameters_schema["properties"],
        },
        "required": ["action"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Forward one calendar action to the tool that has always handled it."""
        return await dispatch_family(
            family=self.name,
            action=kwargs.get("action"),
            actions=self.ACTIONS,
            deps=deps,
            kwargs=kwargs,
        )
```

Write the other five to the same shape. Only four per-family values change — `name`, `ACTIONS`, the `action` enum (**sorted**, so the schema test passes) and the description. Use these descriptions verbatim:

`tasks.py` (`Tasks`, actions `add`/`complete`/`delete`/`list`, properties union from `TaskAdd`, `TaskList`, `TaskComplete`, `TaskDelete`):

```
"The user's to-do list: add a task, read the list, mark one done, or delete one. "
"Use when: the user names something to remember or to do, with no fixed clock time — 「記得幫我買牛奶」"
"「加到待辦」「我還有什麼要做的」「那個做完了」「把那項刪掉」「add a task」「what's on my list」"
"「mark it done」. "
"Do NOT use when: the event has a date and time and belongs on the schedule — that is calendar. "
"Do NOT use when: the user is telling you a fact about themselves to keep — that is remember. "
"Pick `action`: `add` needs title; `list` optionally takes include_completed; `complete` and `delete` "
"take match, and ask the user out loud to confirm before changing anything."
```

`drive.py` (`Drive`, actions `list`/`trash`/`upload`, properties union from `DriveList`, `DriveTrash`, `DriveUpload`):

```
"The user's cloud drive: list recent files, move one to the trash, or upload one. "
"Use when: the user talks about files in the cloud — 「雲端有什麼檔案」「幫我上傳」「把那個檔案丟掉」"
"「what's in my drive」「upload that」「delete that file」. "
"Do NOT use when: the user means a photo you should LOOK at right now — that is camera or look_around. "
"Do NOT use when: the user means a video to play on the TV — that is tv or nas. "
"Pick `action`: `list` optionally takes limit; `trash` takes file_id; `upload` takes name. Both `trash` "
"and `upload` ask the user out loud to confirm first."
```

`nas.py` (`Nas`, actions `play`/`play_folder`/`query`/`skip`, properties union from `NasVideoQuery`, `PlayNasVideo`, `NasPlayFolder`, `NasSkip`):

```
"The household video archive on the NAS: search it, play one video or a whole folder on the TV, or skip "
"to the next one. "
"Use when: the user asks about their own recorded videos, by year, place or keyword — 「二零一九年在花蓮"
"拍的影片」「放家裡那部影片」「播那個資料夾」「下一部」「play our old videos」「skip」. "
"Do NOT use when: the user wants something from the internet — that is tv (YouTube) or the search tool. "
"Do NOT use when: the user wants music rather than video — that is music. "
"Pick `action`: `query` searches the index and returns matches; `play` plays one path or the best match; "
"`play_folder` plays a whole folder; `skip` moves to the next video in a running folder."
```

`music.py` (`Music`, actions `play`/`stop`, properties union from `PlayMusic`, `StopMusic`):

```
"Play or stop music through the speaker. "
"Use when: the user asks for a song, an artist or background music, or asks for it to stop — 「放首歌」"
"「放周杰倫」「音樂關掉」「停止播放」「play some music」「stop the music」. "
"Do NOT use when: the user wants a video on the TV — that is tv or nas. "
"Do NOT use when: the user just wants you to be quiet for a moment — that is wait_for_user. "
"Pick `action`: `play` needs query; `stop` needs nothing and ALWAYS works, even when playing does not."
```

`tv.py` (`Tv`, actions `play_video`/`show`, properties union from `PlayVideo`, `ShowOnTv`):

```
"The television: play a video from the internet on it, or put a generated picture on the screen. "
"Use when: the user asks to watch something or to see something on the big screen — 「電視上放那個 MV」"
"「幫我在電視上播」「畫一張圖放到電視上」「put it on the TV」「show me that on screen」. "
"Do NOT use when: the user means their own recorded videos from the NAS — that is nas. "
"Do NOT use when: the user wants sound only — that is music. "
"Pick `action`: `play_video` needs query (what to search for and play); `show` needs request (what "
"picture to generate and display)."
```

In `profiles/_reachy_companion_locked_profile/profile.md`, replace the 18 sub-tool entries of `default_tools` with the six family names, keeping the file's existing grouping order: `"music", "tv", "nas", "calendar", "tasks", "drive"` where the media/Google blocks used to be. Leave `"notion_add"`, `"email_send"` and everything else in place.

**And rewrite the profile's instruction BODY, not only its TOML front matter** (Codex round 1, P2-6). The body below the `+++` block names the old tools directly and would keep telling the model to call functions that no longer exist — the exact "conflicting instructions in your prompt to what the model is expecting" the research doc's §A2 warns degrades selection. Replace these lines (numbering from the shipped file):

- `:20` 「被要求放音乐、播歌时用 play_music，音乐会从你自己的喇叭放出来；要停下来就用 stop_music。」
  → 「被要求放音乐、播歌时用 music（action=play），音乐会从你自己的喇叭放出来；要停下来就用 music（action=stop）。」
- `:21` 「要在电视上看影片用 play_video，要在电视上看图用 show_on_tv。」
  → 「要在电视上看影片用 tv（action=play_video），要在电视上看图用 tv（action=show）。」
- `:22` 「家里的旧家庭影片：先用 nas_video_query 找，再用 play_nas_video 播一段，或用 nas_play_folder 播一整趟旅行；对方说「下一段」时用 nas_skip。」
  → 「家里的旧家庭影片都用 nas：先 action=query 找，再 action=play 播一段，或 action=play_folder 播一整趟旅行；对方说「下一段」时 action=skip。」
- `:23` 「行程用 calendar_add / calendar_list / calendar_delete，待办用 task_add / task_list / task_complete / task_delete，笔记用 notion_add，云端文件用 drive_list / drive_trash / drive_upload，寄信用 email_send。」
  → 「行程用 calendar（action=add/list/delete），待办用 tasks（action=add/list/complete/delete），笔记用 notion_add，云端文件用 drive（action=list/trash/upload），寄信用 email_send。」

Add a tripwire to `tests/test_profile.py` so this can never drift again:

```python
_RETIRED_TOOL_NAMES = (
    "calendar_add", "calendar_list", "calendar_delete",
    "task_add", "task_list", "task_complete", "task_delete",
    "drive_list", "drive_trash", "drive_upload",
    "nas_video_query", "play_nas_video", "nas_play_folder", "nas_skip",
    "play_music", "stop_music", "play_video", "show_on_tv",
    "party_mode",
)


def _bundled_profile_files() -> list[Path]:
    """Every profile this package ships, not just the locked one."""
    return sorted(Path(config.DEFAULT_PROFILES_DIRECTORY).glob("*/profile.md"))


def test_no_retired_tool_name_survives_in_any_bundled_profile() -> None:
    """The instruction body is a prompt too: a name it uses must exist.

    Conflicting instructions between the prompt and the registered tool schemas
    measurably degrade selection (research doc §A2), the front matter is only
    half the file, and the locked profile is only one of fifteen — `default`
    ships `sweep_look` in its own tool list (Codex round 2, 2b-7).
    """
    files = _bundled_profile_files()
    assert files, "no bundled profiles found"
    for path in files:
        text = path.read_text(encoding="utf-8")
        for name in _RETIRED_TOOL_NAMES:
            assert name not in text, f"{path.name}: {name}"


def test_the_hardening_block_names_no_retired_tool() -> None:
    """The shared prompt block is sent with every profile, so it counts too."""
    from reachy_companion.prompts import hardening_block

    block = hardening_block()
    for name in _RETIRED_TOOL_NAMES:
        assert name not in block, name
```

(Reuse whatever `tests/test_profile.py` already imports to locate profiles — it imports `LOCKED_PROFILE` from `reachy_companion.config` at the top; add `config`/`DEFAULT_PROFILES_DIRECTORY` alongside it rather than hard-coding a path. Task 7 extends `_RETIRED_TOOL_NAMES` with `sweep_look`, `self_destruct` and `mad_laugh`, and both tests then cover them across every bundled profile.)

- [ ] **Step 4: Run**

Run: `python -m pytest tests/tools/ tests/test_profile.py tests/test_profile_toolsets.py tests/test_external_loading.py tests/test_hanova_integration.py tests/test_hanova_settings.py -v`
Expected: PASS. Then `python -m pytest` — full suite green. The 18 sub-tool test modules must all still pass untouched; if any of them fails, the façade has changed behavior and the delegation is wrong.

- [ ] **Step 5: Lint, typecheck, commit**

```bash
ruff check . && mypy --strict src
git add reachy_companion/src reachy_companion/tests reachy_companion/profiles
git commit -m "feat(tools): consolidate CRUD families into action-enum tools"
```

---

### Task 7: `look_around` composite, symmetric descriptions, and three deletions

**Files:**
- Create: `reachy_companion/src/reachy_companion/tools/look_around.py`
- Modify: `reachy_companion/src/reachy_companion/tools/camera.py` (description `:15-24`)
- Modify: `reachy_companion/src/reachy_companion/tools/move_head.py` (description `:18`, and the `sweep_look.py:34-35` comment reference at `:57`)
- Modify: `reachy_companion/src/reachy_companion/huggingface_realtime.py` (`_sanitize_tool_result_for_model` `:1401-1409`, image-attachment condition `:2231`) — generalize the image path off the `camera` name
- Delete: `reachy_companion/src/reachy_companion/tools/sweep_look.py`, `tools/self_destruct.py`, `tools/mad_laugh.py`
- Delete test: `reachy_companion/tests/test_hanova_gags.py`
- Modify: `reachy_companion/src/reachy_companion/hanova/settings.py` (`TOOL_GROUPS["music"]` `:475`, `TOOL_PREREQS` `:577-578`, `_PREREQS` `:547-548`, `self_destruct_yt_id` `:363`, `mad_laugh_yt_id` `:368`)
- Modify: `reachy_companion/profiles/_reachy_companion_locked_profile/profile.md` and `reachy_companion/profiles/default/profile.md` (`default_tools`)
- Modify test: `reachy_companion/tests/test_profile.py`, `tests/test_external_loading.py`, `tests/test_hanova_settings.py`, `tests/test_hanova_integration.py`
- Create test: `reachy_companion/tests/tools/test_look_around.py`
- **Leave alone:** `tests/test_hanova_confirm.py` — it uses the string `"self_destruct"` as an arbitrary gate key and imports nothing from the deleted module.

**Interfaces:**
- Produces (tool): `reachy_companion.tools.look_around.LookAround`, `name = "look_around"`, `needs_response = True` (default — the model must describe what it saw).
- **Removes:** the registered tool names `sweep_look` (subsumed by `look_around` — one directional look the model actually reaches for, instead of a fixed left-right-centre sweep it did not), `self_destruct` and `mad_laugh` (gag tools; two of the 41 slots the diet is buying back). Nothing in Tasks 8–12 may reference them.
  - Schema: required `direction` ∈ `["left", "right", "up", "down", "front"]` — **no `behind`**; `MoveHead.DELTAS` has no such entry and body rotation is out of scope this wave (Codex round 1, P2-4), so the trigger phrase 「看一下你後面」 is removed from the description too rather than advertising a capability the schema cannot express. Optional `question` (string).
  - Returns **`direction_requested`**, not `direction_moved` (Codex round 1, P2-2, and the orchestrator's ruling to record which option the motion API supports). `MoveHead` returns as soon as the move is *queued* (`move_head.py:74-77`); `MovementManager` exposes `queue_move` / `clear_move_queue` / `set_moving_state` / `get_status` (`moves.py:245-266`, `:764`) and **no** accepted-or-completed signal, and `set_hold_still(True)` silently drops queued moves (`moves.py:307-320`). So the honest half is bought where it is cheap — `look_around` calls `clear_move_queue()` first, so its own move is not stuck behind an earlier one — and the field is named for what the tool can actually attest.
  - Three distinct outcomes, and the interface distinguishes them (Codex round 1, P2-3):
    - move queued and picture taken → `{"direction_requested": <direction>, "question": <question>, "b64_im": <base64 jpeg>}`
    - **move failed** → `{"error": <str>}` and **no** `direction_requested`: nothing was even asked of the body, so the model has nothing to narrate.
    - move queued, **capture failed** → `{"direction_requested": <direction>, "question": <question>, "error": <str>}` and no `b64_im`: the head really was sent, and saying so is honest; what failed was the picture.
  - Module constant `LOOK_AROUND_SETTLE_S: Final[float] = 0.8`.
- Consumes: `MoveHead` (`tools/move_head.py`, returns `{"status": "looking <direction>"}` or `{"error": …}`), `Camera` (`tools/camera.py`, returns `{"b64_im": …}` or `{"error": …}`), `deps.motion_duration_s` (`tools/core_tools.py:46`, default 1.0), `deps.movement_manager.clear_move_queue()` (`moves.py:253`).
- **Modifies the image-attachment path** (Codex round 1, P2-1 — Critical). Today the handler attaches a tool result's picture as an `input_image` only when `completed_tool.tool_name == "camera"` (`huggingface_realtime.py:2231`), and only strips the base64 for that same name (`:1402`). A `look_around` result would therefore never reach the model as an image, and its base64 would be dumped into the tool JSON instead. **Generalize both sites to key on the payload, not the name:** any tool result carrying a `b64_im` string is sanitized and attached. That is the correct shape anyway — a second tool returning a picture was always going to happen — and it needs no name list to maintain.
- Contract for Task 12: the three descriptions are the verbatim texts written in Step 3 below; `feature_list.json`'s `VOICE-LOOK-AROUND` row cites the journal line `Tool call: look_around direction=%s`.

- [ ] **Step 1: Write the failing tests** — new file `reachy_companion/tests/tools/test_look_around.py`:

```python
"""look_around: turn the head, let it settle, then look.

The composite exists because `gpt-realtime-2.1-mini` does not chain
move_head → camera (docs/research-mini-tool-calling-2026-08.md §B2): asked
「轉到右邊去看看有誰」 it called `camera` alone and then narrated a turn that
never happened. A composite removes the chaining decision entirely and returns
`direction_requested`, which is exactly as much as the motion API can attest
(Codex round 1, P2-2).
"""

import base64
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from reachy_companion.tools.camera import Camera
from reachy_companion.tools.move_head import MoveHead
from reachy_companion.tools.look_around import LookAround


def _deps(camera_enabled: bool = True) -> SimpleNamespace:
    media = SimpleNamespace(get_frame_jpeg=lambda: b"\xff\xd8jpeg")
    reachy_mini = SimpleNamespace(
        media=media,
        get_current_head_pose=MagicMock(return_value=object()),
        get_current_joint_positions=MagicMock(return_value=([0.0] * 6, [0.0, 0.0])),
    )
    return SimpleNamespace(
        reachy_mini=reachy_mini,
        movement_manager=MagicMock(),
        camera_enabled=camera_enabled,
        motion_duration_s=0.01,
    )


@pytest.mark.asyncio
async def test_look_around_moves_then_captures(monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []

    async def _move(self, deps, **kwargs):
        order.append(f"move:{kwargs['direction']}")
        return {"status": f"looking {kwargs['direction']}"}

    async def _camera(self, deps, **kwargs):
        order.append("camera")
        return {"b64_im": base64.b64encode(b"jpeg").decode("utf-8")}

    monkeypatch.setattr(MoveHead, "__call__", _move)
    monkeypatch.setattr(Camera, "__call__", _camera)
    monkeypatch.setattr("reachy_companion.tools.look_around.LOOK_AROUND_SETTLE_S", 0.0)

    deps = _deps()
    result = await LookAround()(deps, direction="right", question="誰在那邊")
    assert order == ["move:right", "camera"]
    assert result["direction_requested"] == "right"
    assert result["question"] == "誰在那邊"
    assert result["b64_im"]
    # The queue is cleared first, so this move is not stuck behind an older one.
    deps.movement_manager.clear_move_queue.assert_called_once_with()


@pytest.mark.asyncio
async def test_look_around_rejects_an_unknown_direction() -> None:
    result = await LookAround()(_deps(), direction="behind")
    assert "error" in result
    assert "direction_requested" not in result


@pytest.mark.asyncio
async def test_look_around_reports_a_failed_move_without_claiming_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No direction field on a failed move: the model must not narrate a turn."""

    async def _move(self, deps, **kwargs):
        return {"error": "move_head failed: RuntimeError: motors off"}

    monkeypatch.setattr(MoveHead, "__call__", _move)
    monkeypatch.setattr("reachy_companion.tools.look_around.LOOK_AROUND_SETTLE_S", 0.0)
    result = await LookAround()(_deps(), direction="left")
    assert "error" in result
    assert "direction_requested" not in result


@pytest.mark.asyncio
async def test_look_around_reports_a_failed_capture_but_keeps_the_move(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The head really did turn, so say so — and say the picture failed."""

    async def _move(self, deps, **kwargs):
        return {"status": "looking up"}

    async def _camera(self, deps, **kwargs):
        return {"error": "No frame available"}

    monkeypatch.setattr(MoveHead, "__call__", _move)
    monkeypatch.setattr(Camera, "__call__", _camera)
    monkeypatch.setattr("reachy_companion.tools.look_around.LOOK_AROUND_SETTLE_S", 0.0)
    result = await LookAround()(_deps(), direction="up")
    assert result["direction_requested"] == "up"
    assert result["error"] == "No frame available"
    assert "b64_im" not in result


@pytest.mark.asyncio
async def test_look_around_defaults_the_question(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    async def _move(self, deps, **kwargs):
        return {"status": "ok"}

    async def _camera(self, deps, **kwargs):
        seen.append(kwargs["question"])
        return {"b64_im": "x"}

    monkeypatch.setattr(MoveHead, "__call__", _move)
    monkeypatch.setattr(Camera, "__call__", _camera)
    monkeypatch.setattr("reachy_companion.tools.look_around.LOOK_AROUND_SETTLE_S", 0.0)
    await LookAround()(_deps(), direction="front")
    assert seen == ["描述你現在看到什麼"]


def test_descriptions_are_symmetric_and_route_directional_looks() -> None:
    """Research §A2: the asymmetry between camera and move_head WAS the bug."""
    for description in (Camera.description, MoveHead.description, LookAround.description):
        assert "Use when:" in description
        assert "Do NOT use when:" in description
    assert "look_around" in Camera.description
    assert "look_around" in MoveHead.description
    assert "camera" in LookAround.description and "move_head" in LookAround.description
    # Chinese trigger phrasings must be enumerated, not implied (research §C7).
    for phrase in ("右邊", "左邊", "轉過去"):
        assert phrase in LookAround.description
    # No `behind`: the schema cannot express it and body rotation is out of
    # scope this wave (Codex round 1, P2-4).
    assert "後面" not in LookAround.description
    assert "behind" not in LookAround.description
    assert "direction_requested" in LookAround.description
    assert "does not move" in Camera.description or "it does not move" in Camera.description


def test_schema_enumerates_the_five_directions() -> None:
    schema = LookAround().parameters_schema
    assert schema["properties"]["direction"]["enum"] == ["left", "right", "up", "down", "front"]
    assert schema["required"] == ["direction"]
    assert "behind" not in schema["properties"]["direction"]["enum"]
```

And append to `tests/test_huggingface_realtime.py`, for the generalized image path (Codex round 1, P2-1 — without this, `look_around`'s picture never reaches the model and its base64 is dumped into the tool JSON instead):

```python
def test_any_tool_result_with_an_image_is_sanitized() -> None:
    """The image path keys on the payload, not on the tool's name."""
    sanitize = HuggingFaceRealtimeHandler._sanitize_tool_result_for_model
    for name in ("camera", "look_around", "some_future_tool"):
        out = sanitize(name, {"b64_im": "AAAA", "direction_requested": "right"})
        assert "b64_im" not in out
        assert out["image_attached"] is True
        assert out["direction_requested"] == "right"
    # Results with no picture are returned untouched.
    passthrough = {"ok": True, "status": "waiting"}
    assert sanitize("wait_for_user", passthrough) is passthrough
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/tools/test_look_around.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'reachy_companion.tools.look_around'`.

- [ ] **Step 3: Implement**

New file `reachy_companion/src/reachy_companion/tools/look_around.py`:

```python
"""Turn the head, let it settle, then look. Filename == Tool.name.

`gpt-realtime-2.1-mini` does not chain move_head → camera. Asked
「轉到右邊去看看有誰」 on 2026-08-31 it called `camera` alone, saw the wall it was
already facing, and narrated a turn that never happened. OpenAI's own
function-calling guide prescribes the cure: "Combine functions that are always
called in sequence." This tool is that combination — one decision instead of
two — and it returns `direction_requested`, so the sentence "I turned to the
right" has something behind it instead of being invented
(docs/research-mini-tool-calling-2026-08.md §B2).

Reuse-first: the motion is `MoveHead` and the capture is `Camera`, called
as-is. This module owns the ordering and the settle, nothing else.
"""

from __future__ import annotations
import asyncio
import logging
from typing import Any, Dict, Final

from reachy_companion.tools.camera import Camera
from reachy_companion.tools.move_head import MoveHead
from reachy_companion.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)

# How long to wait AFTER the queued goto's own duration before capturing.
# `move_head` returns as soon as the move is queued (`move_head.py:74-77`), and
# `deps.motion_duration_s` is how long that move takes; this is the extra
# settle so the frame is not smeared by the tail of the motion.
LOOK_AROUND_SETTLE_S: Final[float] = 0.8

_DEFAULT_QUESTION: Final[str] = "描述你現在看到什麼"


class LookAround(Tool):
    """Move the head to a direction and describe what is there."""

    name = "look_around"
    description = (
        "Physically turn the head to one side and then look with the camera: this tool moves FIRST and takes "
        "the picture afterwards. Directions: left 左邊, right 右邊, up 上面, down 下面, front 正前方. "
        "Use when: the user names one of those directions, or points at something away from where you are "
        "already facing — 「轉到右邊去看看有誰」「看左邊」「往上看」「轉過去看看」「turn right and see who is "
        "there」「look to your left」. "
        "Use when: the user asks who or what is to one side rather than straight ahead. "
        "Do NOT use when: the user asks what you see with NO direction at all — use camera, which looks "
        "without moving. "
        "Do NOT use when: the user only wants the movement and no description — use move_head. "
        "Do NOT use when: the question is about WHO a person is or whether you remember them — that is "
        "who_is_this. "
        "The result contains `direction_requested`: it names where the head was sent. Say you turned that "
        "way only when that field came back with the direction you claim, and describe what the returned "
        "PICTURE shows — never a room, a person or an object you did not see in it."
    )
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "direction": {
                "type": "string",
                "enum": ["left", "right", "up", "down", "front"],
                "description": (
                    "Where to turn the head before taking the picture: left 左邊、right 右邊、up 上面、"
                    "down 下面、front 正前方。"
                ),
            },
            "question": {
                "type": "string",
                "description": (
                    "What to observe once the head has turned. Examples: 那邊有誰、那一側有什麼東西、"
                    "上面有什麼。"
                ),
            },
        },
        "required": ["direction"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Own the queue, move, settle, capture — and claim only what is true."""
        direction = kwargs.get("direction")
        valid = self.parameters_schema["properties"]["direction"]["enum"]
        if not isinstance(direction, str) or direction not in valid:
            return {"error": f"direction must be one of {valid}"}
        question = (kwargs.get("question") or "").strip() or _DEFAULT_QUESTION
        logger.info("Tool call: look_around direction=%s question=%s", direction, question[:120])

        # Own the queue before adding to it (Codex round 1, P2-2). `queue_move`
        # is sequential: without this, an emotion or dance already queued runs
        # first and the picture is taken from wherever THAT left the head.
        # Everything queued is by definition older than the instruction the user
        # just gave.
        try:
            deps.movement_manager.clear_move_queue()
        except Exception as exc:  # noqa: BLE001 - a manager without the seam still gets a look
            logger.debug("look_around: could not clear the move queue: %s", exc)

        moved = await MoveHead()(deps, direction=direction)
        if "error" in moved:
            # No direction field at all on this path: nothing was even asked of
            # the body, so there is nothing for the model to narrate.
            return {"error": moved["error"]}
        await asyncio.sleep(float(deps.motion_duration_s) + LOOK_AROUND_SETTLE_S)

        shot = await Camera()(deps, question=question)
        # `direction_requested`, not `direction_moved`: `MoveHead` returns once
        # the move is QUEUED, and `MovementManager` publishes no accepted- or
        # completed-move signal for us to wait on (`moves.py:245-266`, `:764`) —
        # `set_hold_still(True)` can even drop a queued move silently. Clearing
        # the queue above removes the common way the move gets deferred; the
        # field name carries the rest of the honesty, and the description below
        # tells the model to describe the PICTURE rather than assert a completed
        # motion (Codex round 1, P2-2).
        result: Dict[str, Any] = {"direction_requested": direction, "question": question}
        if "error" in shot:
            # The head really was sent, so the direction is reported and the
            # capture failure travels with it.
            result["error"] = shot["error"]
            return result
        result["b64_im"] = shot["b64_im"]
        return result
```

Generalize the image path in `huggingface_realtime.py` (Codex round 1, P2-1). `_sanitize_tool_result_for_model` (`:1401-1409`) becomes:

```python
    @staticmethod
    def _sanitize_tool_result_for_model(tool_name: str, tool_result: dict[str, Any]) -> dict[str, Any]:
        """Remove bulky transport-only fields before echoing tool output back to the model.

        Keyed on the payload, not the tool's name: `look_around` returns a
        picture too, and a name list would have to be maintained for every tool
        that ever does (Codex round 1, P2-1). `tool_name` stays in the signature
        for the log line and for future per-tool rules.
        """
        if "b64_im" in tool_result:
            sanitized = dict(tool_result)
            sanitized.pop("b64_im", None)
            sanitized["image_attached"] = True
            return sanitized
        return tool_result
```

and the attachment condition at `:2231`:

```python
            if model_result_submitted and "b64_im" in tool_result:
```

Replace `camera.py`'s description (`:15-24`) with:

```python
    description = (
        "Take a picture with the camera and describe what is in front of the robot RIGHT NOW. It sees only "
        "where the head is already pointing; it does not move anything. "
        "Use when: the user asks what you see, or about something in front of you, what they are holding, "
        "their outfit, or how they look — 「你看到什麼」「這是什麼」「看看我今天穿的衣服」「what do you see」. "
        "Use when: the user asks you to look with no direction at all — do not ask for clarification, call "
        "this tool and describe what you see. "
        "Do NOT use when: the user asks you to physically turn or look in a direction (右邊/左邊/上面/下面/"
        "轉過去/那邊) — use look_around, which turns the head first and then looks. "
        "Do NOT use when: the question is about WHO a person is, whether you know or remember them, or what "
        "someone's name is — that is who_is_this. "
        "The camera is live; each call captures the current moment."
    )
```

Replace `move_head.py`'s description (`:18`) with:

```python
    description = (
        "Move the head in a given direction and leave it there. Movement only: it takes no picture and tells "
        "you nothing about what is there. "
        "Use when: the user asks for the movement itself and wants no description — 「抬頭」「低頭」"
        "「頭轉過去」「看鏡頭」「head up」「face front」. "
        "Use when: you want to point the head somewhere as body language while you keep talking. "
        "Do NOT use when: the user wants to KNOW who or what is in that direction — use look_around, which "
        "turns the head and then looks. "
        "Do NOT use when: the user asks what you see without naming a direction — use camera. "
        "NEVER say you saw anything after this tool: it returns no picture."
    )
```

In `profiles/_reachy_companion_locked_profile/profile.md`, add `"look_around",` to `default_tools` immediately **after `"camera",`** — that is where Task 8's final list puts it, and `EXPECTED_TOOLS` is order-sensitive, so the two must agree (Codex round 2, 2b-2).

- [ ] **Step 4: Retire `sweep_look`, `self_destruct` and `mad_laugh`**

First the failing assertion — append to `tests/tools/test_look_around.py`:

```python
def test_the_retired_tools_are_gone() -> None:
    """sweep_look is subsumed by look_around; the two gags leave the diet's budget."""
    import importlib

    from reachy_companion.tools.core_tools import get_tools

    registered = set(get_tools())
    for name in ("sweep_look", "self_destruct", "mad_laugh"):
        assert name not in registered, name
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(f"reachy_companion.tools.{name}")
```

Run: `python -m pytest tests/tools/test_look_around.py -k retired -v` — FAIL (`sweep_look` still registered). Then:

1. `git rm` the three tool modules: `tools/sweep_look.py`, `tools/self_destruct.py`, `tools/mad_laugh.py`, and `git rm tests/test_hanova_gags.py` (that file is entirely self_destruct/mad_laugh coverage).
2. Remove the three names from `default_tools` in `profiles/_reachy_companion_locked_profile/profile.md` and in `profiles/default/profile.md`, **and delete the instruction-body line that scripts the removed gag** (Codex round 1, P2-6). Find that line by grep rather than by line number — it moves, and it is written in SIMPLIFIED characters, so a search for 「倒數儀式」 finds nothing (Codex round 2, 2b-6):

   ```bash
   grep -n "self_destruct" reachy_companion/profiles/_reachy_companion_locked_profile/profile.md
   ```

   The body hit is the bullet beginning `- self_destruct 是角色扮演的倒数仪式：照 summary 念出来就好…`; delete the whole bullet. The other hit is the `default_tools` entry, removed above. Add `"self_destruct"`, `"mad_laugh"` and `"sweep_look"` to Task 6's `_RETIRED_TOOL_NAMES` tripwire in `tests/test_profile.py`, so both whole-file assertions — every bundled `profiles/*/profile.md` and the hardening block — cover them too. `profiles/default/profile.md` currently ships `sweep_look`, so that test fails until step 2 is done there as well. Add `"look_around"` to `EXPECTED_TOOLS` **after `"camera"`** (matching the profile order and Task 8's final list) and drop `"sweep_look"`.
3. `hanova/settings.py`: drop `"self_destruct"` and `"mad_laugh"` from `TOOL_GROUPS["music"]` (`:475`) — leaving `("play_music", "stop_music")`, which keeps `family_status("music")` from reporting a permanently partial family; drop their two `TOOL_PREREQS` rows (`:577-578`); drop the `"HANOVA_SELF_DESTRUCT_YT_ID"` / `"HANOVA_MAD_LAUGH_YT_ID"` entries from `_PREREQS` (`:547-548`); delete `self_destruct_yt_id()` (`:363`) and `mad_laugh_yt_id()` (`:368`).
4. `tests/test_profile.py`: covered by step 2 above (`EXPECTED_TOOLS` plus the `_RETIRED_TOOL_NAMES` tripwire).
5. `tests/test_external_loading.py`: it uses `sweep_look` as a representative registered profile tool at `:63, :165, :178, :186, :188, :203, :248` — replace every occurrence with `move_head`, which stays in the profile and is equally representative. Do not weaken the assertions.
6. `tests/test_hanova_settings.py` and `tests/test_hanova_integration.py`: delete the assertions and table rows that name the two gags (4 and 8 occurrences respectively). Keep everything else in those files intact.
7. `.env.example`: remove the `HANOVA_SELF_DESTRUCT_YT_ID` and `HANOVA_MAD_LAUGH_YT_ID` blocks if present.
8. In `tools/move_head.py:57`, the comment cites `sweep_look.py:34-35` for how body yaw is read; change it to cite the code it now shares that reading with — `look_around.py` calls this very method — or simply drop the file reference and keep the explanation.

- [ ] **Step 5: Run**

Run: `python -m pytest tests/tools/ tests/test_profile.py tests/test_profile_toolsets.py tests/test_external_loading.py tests/test_hanova_settings.py tests/test_hanova_integration.py tests/test_hanova_confirm.py -v`
Expected: PASS. Then `python -m pytest` — full suite green, and the total drops by however many tests `test_hanova_gags.py` contributed (record the new baseline for Task 12).

- [ ] **Step 6: Lint, typecheck, commit**

```bash
ruff check . && mypy --strict src
git add -A reachy_companion/src reachy_companion/tests reachy_companion/profiles reachy_companion/.env.example
git commit -m "feat(tools): look_around composite, symmetric descriptions, retire three tools"
```

---

### Task 8: The tool surface — static core, `open_toolbox`, and RECORD scoping

**Files:**
- Create: `reachy_companion/src/reachy_companion/toolboxes.py`
- Create: `reachy_companion/src/reachy_companion/tools/open_toolbox.py`
- Modify: `reachy_companion/src/reachy_companion/record_mode.py` (allowlist only)
- Modify: `reachy_companion/src/reachy_companion/huggingface_realtime.py` (`__init__`; `_mode_tool_exclusions` stub from Task 3; `set_conversation_mode` from Task 1; `_run_realtime_session` `:2315`; `shutdown()`)
- Modify: `reachy_companion/src/reachy_companion/tools/core_tools.py` (`ToolDependencies`)
- Modify: `reachy_companion/src/reachy_companion/main.py` (seam wiring, next to `deps.set_conversation_mode`)
- Modify: `reachy_companion/src/reachy_companion/prompts.py` (`_HARDENING_BLOCK` — the routing rules, stated identically to the tool description per research §A3)
- Modify: `reachy_companion/profiles/_reachy_companion_locked_profile/profile.md` (`default_tools`, final list)
- Modify test: `reachy_companion/tests/test_profile.py` (`EXPECTED_TOOLS`), `tests/test_record_mode.py`, `tests/test_prompts_hardening.py`
- Create test: `reachy_companion/tests/test_toolboxes.py`

**Interfaces:**
- Produces (`reachy_companion.record_mode`): `RECORD_TOOL_ALLOWLIST: Final[frozenset[str]] = frozenset({"set_conversation_mode", "summarize_conversation", "go_to_sleep", "wait_for_user", "task_status", "task_cancel"})`.
- Produces (`reachy_companion.toolboxes`):
  - `CORE_TOOL_NAMES: Final[frozenset[str]]` — exactly these **22**: `camera, look_around, move_head, play_emotion, dance, stop_dance, stop_emotion, head_tracking, who_is_this, remember_face, remember, forget, home_control, music, pollen_robotics_reachy_mini_search_tool__search_web, go_to_sleep, set_conversation_mode, wait_for_user, summarize_conversation, open_toolbox, task_status, task_cancel`.
    **`music` is core, not boxed** (Codex round 1, P2-7): the family carries `stop_music`, whose own module documents it as the safety lane that must answer even when nothing else can (`settings.TOOL_PREREQS["stop_music"] == ()`, `stop_music.py:8`). Putting it behind a toolbox would mean the robot cannot be told to stop the music until it has first loaded the tools for stopping the music. 22 rather than 21 is the honest price of that.
  - `TOOLBOXES: Final[dict[str, tuple[str, ...]]] = {"productivity": ("calendar", "tasks", "drive", "email_send", "notion_add"), "media": ("tv", "nas")}`
  - `TOOLBOX_CATEGORIES: Final[tuple[str, ...]] = ("media", "productivity")` (sorted, for the tool's enum)
  - `session_tool_exclusions(mode: ConversationMode, open_boxes: Iterable[str]) -> list[str]`
- Produces (handler): `self._open_toolboxes: set[str]`; `async open_toolbox(self, category: str) -> dict[str, Any]`; `close_toolboxes(self, reason: str) -> None`.
- Produces (deps seam): `ToolDependencies.open_toolbox: Callable[[str], Awaitable[dict[str, Any]]] | None = None` (`Awaitable` is already imported for `set_conversation_mode` in Task 1).
- Produces (tool): `reachy_companion.tools.open_toolbox.OpenToolbox`, `name = "open_toolbox"`, one required string arg `category`. Success returns `{"ok": True, "status": "loaded"|"already_open", "category": <str>, "tools": [<names>]}`; failure returns `{"ok": False, "status": "update_failed", "error": <str>, "category": <str>, "categories": ["media", "productivity"]}` and the box is **not** left marked open. Failure covers two cases, and the second is why the check happens *after* the await (Codex round 3, finding 3): the server refused the update, **or** a concurrent mode switch called `close_toolboxes` while it was in flight. Reporting "loaded" in that second case would advertise tools the session no longer has, and the model's very next call would hit one that is not there.
- Consumes: `get_tools()` / `get_tool_specs(exclusion_list=…)` / `EXTRA_TOOLS` (`tools/core_tools.py:533, :525, :155`), `self._push_mode_update() -> bool` (Task 3), `RECORD_TOOL_ALLOWLIST`.
- **`EXTRA_TOOLS` are never hidden, in EVERY mode including RECORD** (Codex round 1, P2-8). The MCP tool spaces (D-004) are installed deliberately by the operator, belong to no toolbox, and have no `open_toolbox` category that could bring them back — so a RECORD branch that allowed only the six local names would make them unreachable for the whole meeting. The invariant is stated once and holds everywhere; the RECORD tests assert `kept <= RECORD_TOOL_ALLOWLIST | set(EXTRA_TOOLS)`, not `<= RECORD_TOOL_ALLOWLIST`.
- **Behavioral contract:** `open_toolbox` **awaits** `_push_mode_update()` before returning, so the `session.update` is acknowledged before the model reads the result and continues to the real call — and **rolls the box back** if it was not (Codex round 1, P2-9). Boxes close on a mode switch (`set_conversation_mode`), at session start (`_run_realtime_session`) and at `shutdown()` — which is the path `go_to_sleep` takes. No idle timers this wave.

- [ ] **Step 1: Write the failing tests** — new file `reachy_companion/tests/test_toolboxes.py`:

```python
"""Dynamic toolboxes: a small static core plus two families loaded on demand.

The cookbook's Dynamic Conversation Flow pattern — "you only provide what's
relevant to the active phase… you use `session.update` to transition, replacing
the prompt and tools" — applied to the two families the operator judged
latency-tolerant (docs/research-mini-tool-calling-2026-08.md §A1).
"""

import asyncio
from types import SimpleNamespace
from collections import deque
from unittest.mock import AsyncMock, MagicMock

import pytest
from test_solo_barge import _install_barge_state

from reachy_companion.toolboxes import (
    CORE_TOOL_NAMES,
    TOOLBOX_CATEGORIES,
    TOOLBOXES,
    session_tool_exclusions,
)
from reachy_companion.record_mode import RECORD_TOOL_ALLOWLIST
from reachy_companion.conversation_mode import ConversationMode
from reachy_companion.openai_realtime import OpenAIRealtimeHandler
from reachy_companion.tools.core_tools import get_tool_specs, get_tools
from reachy_companion.tools.open_toolbox import OpenToolbox


def _box_handler(mode: ConversationMode = ConversationMode.ONE_ON_ONE) -> OpenAIRealtimeHandler:
    h = OpenAIRealtimeHandler.__new__(OpenAIRealtimeHandler)
    h._conversation_mode = mode
    h._turn_mode = mode
    h._turn_modes = {}
    h._open_toolboxes = set()
    h._mode_update_seq = 0
    h._session_update_lock = asyncio.Lock()
    h._session_update_event_id = None
    h._session_update_waiter = None
    h._session_update_ack_debt = 0
    # Default to "the loop is running", so an update waits for its ack; the
    # pre-receive-loop tests set this back to False explicitly.
    h._receive_loop_active = True
    h._handler_loop = None
    h._party_last_accept_at = None
    h._party_speech_open = False
    h._party_utterance_seq = 0
    h._party_barge_task = None
    h._active_response_id = None
    h._cancelled_response_ids = deque(maxlen=8)
    h._response_done_event = asyncio.Event()
    h._response_done_event.set()
    h.connection = None
    h.deps = SimpleNamespace(
        reachy_mini=MagicMock(), movement_manager=MagicMock(), record_log=deque(), sleep_requested=False
    )
    _install_barge_state(h)
    h._clear_queue = MagicMock()
    return h


def test_every_registered_tool_belongs_to_the_core_or_a_box() -> None:
    """A tool in neither would be permanently unreachable — the worst failure."""
    from reachy_companion.tools.core_tools import EXTRA_TOOLS

    registered = set(get_tools())
    boxed = {name for names in TOOLBOXES.values() for name in names}
    assert registered - CORE_TOOL_NAMES - boxed - set(EXTRA_TOOLS) == set()
    # And nothing is in two places at once.
    assert CORE_TOOL_NAMES & boxed == set()
    assert set(TOOLBOXES["productivity"]) & set(TOOLBOXES["media"]) == set()
    assert TOOLBOX_CATEGORIES == ("media", "productivity")
    assert len(CORE_TOOL_NAMES) == 22
    # The stop lane must never live behind a toolbox (Codex round 1, P2-7).
    assert "music" in CORE_TOOL_NAMES
    assert "music" not in boxed


def test_the_static_core_is_the_start_of_turn_surface() -> None:
    """41 → 22, with the two SystemTool entries counted honestly."""
    from reachy_companion.tools.core_tools import EXTRA_TOOLS

    excluded = session_tool_exclusions(ConversationMode.ONE_ON_ONE, ())
    kept = {spec["name"] for spec in get_tool_specs(exclusion_list=excluded)}
    # `| EXTRA_TOOLS` because out-of-band MCP tools are never hidden; in a clean
    # test environment that set is empty and `kept` is exactly the core.
    assert kept == (CORE_TOOL_NAMES | set(EXTRA_TOOLS)) & set(get_tools())
    assert "music" in kept  # the stop lane, always reachable
    for boxed in ("calendar", "tasks", "drive", "email_send", "notion_add", "tv", "nas"):
        assert boxed not in kept


def test_opening_a_box_adds_exactly_that_family() -> None:
    core = {spec["name"] for spec in get_tool_specs(exclusion_list=session_tool_exclusions(ConversationMode.GROUP, ()))}
    opened = {
        spec["name"]
        for spec in get_tool_specs(
            exclusion_list=session_tool_exclusions(ConversationMode.GROUP, ("productivity",))
        )
    }
    assert opened - core == set(TOOLBOXES["productivity"])
    both = {
        spec["name"]
        for spec in get_tool_specs(
            exclusion_list=session_tool_exclusions(ConversationMode.ONE_ON_ONE, ("productivity", "media"))
        )
    }
    assert both - core == set(TOOLBOXES["productivity"]) | set(TOOLBOXES["media"])


def test_the_documented_surface_sizes_hold() -> None:
    """22 at rest, 27 / 24 with one box, 29 with both (design decision 8).

    Boxes accumulate within a mode; a turn that asks for the calendar and then
    for the TV keeps both. The numbers are documented, so they are asserted
    (Codex round 2, 2b-3).
    """

    from reachy_companion.tools.core_tools import EXTRA_TOOLS

    # Every documented size is "plus any MCP extras", which are never hidden in
    # any mode (Codex round 3, finding 11). In a clean test environment that set
    # is empty; subtracting it keeps the assertion honest either way.
    extras = len(set(EXTRA_TOOLS) & set(get_tools()))

    def _surface(*boxes: str) -> int:
        excluded = session_tool_exclusions(ConversationMode.ONE_ON_ONE, boxes)
        return len({spec["name"] for spec in get_tool_specs(exclusion_list=excluded)}) - extras

    assert _surface() == 22
    assert _surface("productivity") == 27
    assert _surface("media") == 24
    assert _surface("productivity", "media") == 29


@pytest.mark.asyncio
async def test_a_second_box_adds_to_the_first(monkeypatch: pytest.MonkeyPatch) -> None:
    """Opening media must not close productivity mid-turn (design decision 8)."""
    from unittest.mock import AsyncMock

    h = _box_handler()
    monkeypatch.setattr(h, "_push_mode_update", AsyncMock(return_value=True))
    await h.open_toolbox("productivity")
    await h.open_toolbox("media")
    assert h._open_toolboxes == {"productivity", "media"}


def test_an_unknown_box_name_changes_nothing() -> None:
    assert session_tool_exclusions(ConversationMode.GROUP, ("nonsense",)) == session_tool_exclusions(
        ConversationMode.GROUP, ()
    )


def test_record_mode_ignores_open_boxes() -> None:
    """紀錄模式 scopes to its allowlist no matter what was open when it started.

    Six local tools — the four the model uses plus the two SystemTool entries —
    and whatever MCP extras the operator installed, which are never hidden in
    any mode (Codex round 1, P2-8, P2-12).
    """
    from reachy_companion.tools.core_tools import EXTRA_TOOLS

    excluded = session_tool_exclusions(ConversationMode.RECORD, ("productivity", "media"))
    kept = {spec["name"] for spec in get_tool_specs(exclusion_list=excluded)}
    assert kept <= RECORD_TOOL_ALLOWLIST | set(EXTRA_TOOLS)
    assert {"set_conversation_mode", "summarize_conversation", "go_to_sleep", "wait_for_user"} <= kept
    assert {"task_status", "task_cancel"} <= kept
    assert "camera" not in kept and "calendar" not in kept and "music" not in kept
    # Six LOCAL tools, plus any MCP extras (Codex round 3, finding 11).
    assert len(kept - set(EXTRA_TOOLS)) == 6


def test_record_mode_keeps_the_mcp_extras_reachable(monkeypatch: pytest.MonkeyPatch) -> None:
    """An MCP tool belongs to no box, so hiding it would strand it (P2-8)."""
    from reachy_companion import toolboxes as tb_mod

    monkeypatch.setattr(tb_mod, "EXTRA_TOOLS", {"notion_mcp__search": object()})
    monkeypatch.setattr(tb_mod, "get_tools", lambda: {"camera": object(), "notion_mcp__search": object()})
    assert session_tool_exclusions(ConversationMode.RECORD, ()) == ["camera"]


@pytest.mark.asyncio
async def test_open_toolbox_pushes_the_update_before_returning(monkeypatch: pytest.MonkeyPatch) -> None:
    """The session.update must be ACKNOWLEDGED before the model reads the result."""
    h = _box_handler()
    order: list[str] = []
    seen_boxes: list[set[str]] = []

    async def _push() -> bool:
        order.append("push")
        # The payload is built from live state, so the box must already be in.
        seen_boxes.append(set(h._open_toolboxes))
        return True

    monkeypatch.setattr(h, "_push_mode_update", _push)
    result = await h.open_toolbox("productivity")
    order.append("return")
    assert order == ["push", "return"]
    assert seen_boxes == [{"productivity"}]
    assert result["ok"] is True and result["status"] == "loaded"
    assert result["category"] == "productivity"
    assert set(result["tools"]) == set(TOOLBOXES["productivity"])
    assert h._open_toolboxes == {"productivity"}


@pytest.mark.asyncio
async def test_open_toolbox_rolls_back_when_the_update_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A box the server never applied must not be marked open (P2-9).

    Left set, `_mode_tool_exclusions()` would keep claiming those tools are in
    the session, the model would be told they are available, and every call to
    one of them would fail as an unknown tool for the rest of the visit.
    """

    async def _push() -> bool:
        return False

    h = _box_handler()
    monkeypatch.setattr(h, "_push_mode_update", _push)
    result = await h.open_toolbox("productivity")
    assert result["ok"] is False
    assert result["status"] == "update_failed"
    assert not h._open_toolboxes


@pytest.mark.asyncio
async def test_open_toolbox_rolls_back_when_a_mode_switch_races_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A box closed mid-flight must not be reported as loaded (round 3, #3).

    `set_conversation_mode` calls `close_toolboxes`, so a flip landing while the
    update is in flight empties the set. Returning "loaded" then advertises
    tools the session no longer has, and the model's next call hits one that is
    not there.
    """

    async def _push() -> bool:
        h.close_toolboxes("mode -> group")  # the concurrent switch
        return True

    h = _box_handler()
    monkeypatch.setattr(h, "_push_mode_update", _push)
    result = await h.open_toolbox("productivity")
    assert result["ok"] is False
    assert result["status"] == "update_failed"
    assert not h._open_toolboxes


@pytest.mark.asyncio
async def test_open_toolbox_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    h = _box_handler()
    push = AsyncMock(return_value=True)
    monkeypatch.setattr(h, "_push_mode_update", push)
    await h.open_toolbox("media")
    again = await h.open_toolbox("media")
    assert again["status"] == "already_open"
    push.assert_awaited_once()  # no second session.update for a box already open


@pytest.mark.asyncio
async def test_open_toolbox_rejects_an_unknown_category(monkeypatch: pytest.MonkeyPatch) -> None:
    h = _box_handler()
    push = AsyncMock(return_value=True)
    monkeypatch.setattr(h, "_push_mode_update", push)
    result = await h.open_toolbox("gardening")
    assert result["ok"] is False
    assert result["categories"] == ["media", "productivity"]
    assert not h._open_toolboxes
    push.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_mode_switch_closes_every_box() -> None:
    h = _box_handler()
    h._open_toolboxes = {"productivity", "media"}
    await h.set_conversation_mode("group")
    assert not h._open_toolboxes


def test_handler_exclusions_follow_mode_and_boxes() -> None:
    h = _box_handler(ConversationMode.RECORD)
    assert h._mode_tool_exclusions() == session_tool_exclusions(ConversationMode.RECORD, set())
    h2 = _box_handler()
    h2._open_toolboxes = {"media"}
    assert h2._mode_tool_exclusions() == session_tool_exclusions(ConversationMode.ONE_ON_ONE, {"media"})


@pytest.mark.asyncio
async def test_tool_refuses_when_the_seam_is_unwired() -> None:
    result = await OpenToolbox()(SimpleNamespace(open_toolbox=None), category="media")
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_tool_forwards_the_category() -> None:
    seen: list[str] = []

    async def _seam(category: str) -> dict[str, object]:
        seen.append(category)
        return {"ok": True, "status": "loaded", "category": category, "tools": []}

    result = await OpenToolbox()(SimpleNamespace(open_toolbox=_seam), category="productivity")
    assert seen == ["productivity"] and result["ok"] is True


def test_tool_description_enumerates_the_chinese_routing_triggers() -> None:
    description = OpenToolbox.description
    for phrase in ("行程", "待辦", "郵件", "雲端", "音樂", "電視", "NAS", "productivity", "media"):
        assert phrase in description
    assert "Use when:" in description and "Do NOT use when:" in description


def test_the_prompt_carries_the_same_routing_rules() -> None:
    """Research §A3: state the rule in both places, worded the same way."""
    from reachy_companion.prompts import hardening_block

    block = hardening_block()
    assert "工具箱" in block
    assert "open_toolbox" in block
    for phrase in ("productivity", "media", "行程", "音樂"):
        assert phrase in block
```

Two harness updates this task must make, because `set_conversation_mode` and `shutdown()` gain a `close_toolboxes` call and both are exercised by `__new__`-built handlers:
- `tests/test_conversation_modes.py::_mode_handler` — add `h._open_toolboxes = set()`.
- `tests/test_record_mode.py::_record_handler` — add `h._open_toolboxes = set()`.

Move no RECORD-scoping expectations out of `tests/test_record_mode.py` — this task is where they first exist — and do not add `"open_toolbox"` to `RECORD_TOOL_ALLOWLIST`: a scribe has nothing to open.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_toolboxes.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'reachy_companion.toolboxes'`.

- [ ] **Step 3: Implement**

Append to `record_mode.py` (the allowlist only — the exclusion computation lives in `toolboxes.py`, which needs it):

```python
# What 紀錄模式 leaves on the table: SIX local names — the four the model uses
# plus two structural ones. `task_status` and `task_cancel` are `SystemTool`
# values the background tool manager injects into every profile, and the model
# needs them to follow up a long-running call, so hiding them would break the
# tools that ARE allowed. MCP extras (`EXTRA_TOOLS`) are additionally always
# kept — see `toolboxes.session_tool_exclusions` (Codex round 1, P2-8).
RECORD_TOOL_ALLOWLIST: Final[frozenset[str]] = frozenset(
    {
        "set_conversation_mode",
        "summarize_conversation",
        "go_to_sleep",
        "wait_for_user",
        "task_status",
        "task_cancel",
    }
)
```

New file `reachy_companion/src/reachy_companion/toolboxes.py`:

```python
"""The session's tool surface: a small static core plus two on-demand boxes.

Before this, 41 tools were sent at the start of every turn. OpenAI's own
function-calling guide asks for "fewer than 20 functions available at the start
of a turn", the realtime prompting docs say a focused list "prevents the model
from misselecting tools", and the measured effect is largest in exactly our
case — the right tool present but not ranked first (research doc §A1). The
observed symptom was `move_head` losing to `camera` on 「轉到右邊去看看有誰」.

Three mechanisms, cheapest first: consolidate (18 CRUD tools → 6 families,
`tools/tool_family.py`), delete what nobody calls, and load the rest on demand
through `open_toolbox` — the cookbook's Dynamic Conversation Flow pattern.

Result at the start of a turn: 22 tools, 27 while the productivity box is open,
24 while the media box is, 29 with both (they accumulate within a mode — design
decision 8), and 6 local tools in 紀錄模式. Every count is "plus any
`EXTRA_TOOLS`": MCP tool spaces belong to no box and are never hidden in any
mode, so they sit on top of all of these.
"""

from __future__ import annotations
import logging
from typing import Final
from collections.abc import Iterable

from reachy_companion.record_mode import RECORD_TOOL_ALLOWLIST
from reachy_companion.conversation_mode import ConversationMode
from reachy_companion.tools.core_tools import EXTRA_TOOLS, get_tools


logger = logging.getLogger(__name__)

# Always in `session.tools`. The rule for membership: anything the robot might
# need in the FIRST second of a turn, with no chance to load something first —
# its senses, its body, who it is talking to, the lights, the web, the
# conversation's own controls — plus the two `SystemTool` entries the
# background tool manager injects into every profile.
CORE_TOOL_NAMES: Final[frozenset[str]] = frozenset(
    {
        "camera",
        "look_around",
        "move_head",
        "play_emotion",
        "dance",
        "stop_dance",
        "stop_emotion",
        "head_tracking",
        "who_is_this",
        "remember_face",
        "remember",
        "forget",
        "home_control",
        # The music family is core, not boxed: it carries `stop_music`, the
        # safety lane that must answer even when nothing else can
        # (`settings.TOOL_PREREQS["stop_music"] == ()`, `stop_music.py:8`).
        # Behind a toolbox, "音樂關掉" would first have to load the tools for
        # turning the music off (Codex round 1, P2-7).
        "music",
        "pollen_robotics_reachy_mini_search_tool__search_web",
        "go_to_sleep",
        "set_conversation_mode",
        "wait_for_user",
        "summarize_conversation",
        "open_toolbox",
        "task_status",
        "task_cancel",
    }
)

# Loaded on demand, one `session.update` per open. Both families are things the
# user asks for in a sentence that can afford one extra hop — "add it to my
# calendar", "put that on the TV" — never something the robot needs mid-reflex,
# and never a way to make the robot stop doing something.
TOOLBOXES: Final[dict[str, tuple[str, ...]]] = {
    "productivity": ("calendar", "tasks", "drive", "email_send", "notion_add"),
    "media": ("tv", "nas"),
}

TOOLBOX_CATEGORIES: Final[tuple[str, ...]] = tuple(sorted(TOOLBOXES))


def session_tool_exclusions(mode: ConversationMode, open_boxes: Iterable[str]) -> list[str]:
    """Tool names to hide from the session, given the mode and the open boxes.

    Expressed through the registry's existing `exclusion_list` seam
    (`tools/core_tools.py:525`), so nothing else in the tool pipeline has to
    learn about modes or boxes. Computed against the LIVE registry rather than a
    literal, because MCP and external tools join it at runtime.

    Out-of-band tools (`EXTRA_TOOLS` — the MCP tool spaces, D-004) are never
    hidden: an operator installed them deliberately, they belong to no box, and
    a box that cannot be opened for them would make them unreachable forever.
    """
    registered = set(get_tools())
    # The invariant holds in EVERY mode, RECORD included (Codex round 1, P2-8):
    # an MCP tool belongs to no toolbox, so there is no `open_toolbox` category
    # that could bring it back, and hiding it strands it for the whole meeting.
    allowed = set(EXTRA_TOOLS)
    if mode is ConversationMode.RECORD:
        allowed |= set(RECORD_TOOL_ALLOWLIST)
    else:
        allowed |= set(CORE_TOOL_NAMES)
        for box in open_boxes:
            allowed |= set(TOOLBOXES.get(box, ()))
    return sorted(registered - allowed)
```

In `huggingface_realtime.py`, import `TOOLBOXES, TOOLBOX_CATEGORIES, session_tool_exclusions` from `reachy_companion.toolboxes`, and add to `__init__` next to `_conversation_mode`:

```python
        # --- dynamic toolboxes (2026-08-31 tool diet) ------------------------
        # Which on-demand tool families are currently in `session.tools`. Opened
        # by the `open_toolbox` router, closed on a mode switch, at session
        # start and at shutdown (the path `go_to_sleep` takes). No idle timer:
        # a box that closes mid-sentence is a new failure mode for exactly the
        # model tier this diet exists to stop confusing.
        self._open_toolboxes: set[str] = set()
```

Handler methods, next to `set_conversation_mode`:

```python
    async def open_toolbox(self, category: str) -> dict[str, Any]:
        """Load one on-demand tool family into the live session.

        The model reads this tool's result and continues to the real call in the
        same turn, so the update is not merely sent but ACKNOWLEDGED before the
        result comes back — otherwise the tool it reaches for still does not
        exist on the server (design decision 9).

        Optimistic then rolled back (Codex round 1, P2-9): the box goes into
        `_open_toolboxes` first, because `_push_mode_update` builds its payload
        from that live set — and comes straight back out if the server refused,
        because a box marked open that the session never got would have the
        model calling tools that are not there for the rest of the visit.
        """
        if category not in TOOLBOXES:
            logger.warning("open_toolbox: unknown category %r", category)
            return {
                "ok": False,
                "error": f"unknown toolbox category: {category}",
                "categories": list(TOOLBOX_CATEGORIES),
            }
        tools = list(TOOLBOXES[category])
        if category in self._open_toolboxes:
            return {"ok": True, "status": "already_open", "category": category, "tools": tools}
        self._open_toolboxes.add(category)
        if not await self._push_mode_update():
            self._open_toolboxes.discard(category)
            logger.warning("toolbox %s was not applied by the server; rolled back", category)
            return {
                "ok": False,
                "status": "update_failed",
                "error": f"the {category} tools could not be loaded right now",
                "category": category,
                "categories": list(TOOLBOX_CATEGORIES),
            }
        logger.info("toolbox opened: %s (%s)", category, ", ".join(tools))
        return {"ok": True, "status": "loaded", "category": category, "tools": tools}

    def close_toolboxes(self, reason: str) -> None:
        """Drop every open toolbox. Caller owns pushing the smaller surface."""
        if not self._open_toolboxes:
            return
        logger.info("toolboxes closed (%s): %s", reason, ", ".join(sorted(self._open_toolboxes)))
        self._open_toolboxes.clear()
```

Replace the Task 3 `_mode_tool_exclusions` stub body:

```python
    def _mode_tool_exclusions(self) -> list[str]:
        """Tool names hidden from the session right now: mode plus open boxes."""
        return session_tool_exclusions(self._conversation_mode, self._open_toolboxes)
```

In `set_conversation_mode` (Task 1), immediately after `self._conversation_mode = target` (and beside the Task 4 record-log clear):

```python
        # A new mode is a new posture: its instructions describe a different
        # tool surface, so whatever was loaded for the old one goes.
        self.close_toolboxes(f"mode -> {target.value}")
```

In `_run_realtime_session` (`:2315`), replace `tool_specs = get_tool_specs()` with:

```python
        # A fresh session starts from the static core, first connect and
        # reconnect alike: a box opened in the session that died says nothing
        # about the one replacing it.
        self.close_toolboxes("new session")
        tool_specs = get_tool_specs(exclusion_list=self._mode_tool_exclusions())
```

**Call-signature ripple, must be fixed in this task:** several existing tests stub the registry with a zero-argument lambda — `monkeypatch.setattr(hf_mod, "get_tool_specs", lambda: [])`. That now raises `TypeError: <lambda>() got an unexpected keyword argument 'exclusion_list'`. Change every one to `lambda exclusion_list=None: []`. Find them all with:

```bash
grep -rn 'get_tool_specs", lambda' tests/
```

(at time of writing: 7 occurrences in `tests/test_huggingface_realtime.py` and one each in `tests/test_boot_gate.py`, `tests/test_openai_realtime_config.py`, `tests/test_solo_barge.py`, `tests/test_mcp_servers.py` and the `tests/test_hanova_*.py` family — take the grep as authoritative, not this list).

The `Tools in session (<mode>): [...]` line that makes the live surface checkable on-robot (Codex round 1, P2-12) is already inside `_push_mode_update`'s builder from Task 3 — nothing to add here; just confirm it lists the mode-scoped names once this task's `_mode_tool_exclusions` is real.

In `shutdown()`, beside the Task 4 `clear_record_log(self.deps)` call:

```python
        # `go_to_sleep` reaches shutdown, and so does every other end of a
        # visit: boxes never outlive the conversation that opened them.
        self.close_toolboxes("shutdown")
```

In `tools/core_tools.py`, next to `set_conversation_mode` (Task 1; `Awaitable` is already imported there):

```python
    # Dynamic toolboxes (2026-08-31 tool diet): loads one on-demand tool family
    # into the live session. Async for the same reason `set_conversation_mode`
    # is: the `session.update` must be applied before the tool result reaches
    # the model, or the tool it reaches for next does not exist yet.
    open_toolbox: Callable[[str], Awaitable[dict[str, Any]]] | None = None
```

In `main.py`, beside `deps.set_conversation_mode = handler.set_conversation_mode`:

```python
        deps.open_toolbox = handler.open_toolbox
```

New file `reachy_companion/src/reachy_companion/tools/open_toolbox.py`:

```python
"""Load an on-demand tool family into the session. Filename == Tool.name.

The router half of the tool diet. Reachy keeps 22 tools always ready and loads
the productivity and media families only when a turn needs them — the realtime
cookbook's Dynamic Conversation Flow pattern, which exists because "keeping tool
lists focused per conversation phase prevents the model from misselecting
tools" (docs/research-mini-tool-calling-2026-08.md §A1).
"""

from __future__ import annotations
import logging
from typing import Any, Dict

from reachy_companion.toolboxes import TOOLBOX_CATEGORIES
from reachy_companion.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


class OpenToolbox(Tool):
    """Bring one family of tools into the session, then keep going."""

    name = "open_toolbox"
    description = (
        "Load the tools for a whole area before you use them. Reachy keeps a small set of tools always "
        "ready and loads the rest on demand, so the FIRST time a turn needs one of these areas you call "
        "this, and then call the real tool in the same turn. "
        "Categories: `productivity` — the calendar, the to-do list, the cloud drive, email and Notion. "
        "`media` — the television and the household video archive on the NAS. "
        "Use when: the request needs a tool you cannot see yet. 行程／約／會議／待辦／任務／提醒／郵件／"
        "寄信／雲端／檔案／Notion → productivity；電視／影片／MV／NAS／影片檔 → media。"
        "「幫我加個行程」「加到待辦」「寄封信」「雲端有什麼」→ productivity；「電視上放那個」"
        "「找一下那年拍的影片」→ media；「add a task」「put that on the TV」→ the matching category. "
        "Do NOT use when: the tool you need is already in your list — call it directly, never open a box "
        "first. 音樂 is ALWAYS loaded: 「放首歌」「音樂關掉」 go straight to the music tool. "
        "Do NOT use when: the request is about looking, moving, emotions, remembering a person, the lights, "
        "music, searching the web, conversation modes, or going to sleep — those tools are always loaded. "
        "After this returns, the category's tools are available immediately: continue and call the one you "
        "actually need, in the same turn, without asking the user again."
    )
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "enum": list(TOOLBOX_CATEGORIES),
                "description": "media 電視／NAS 影片；productivity 行程／待辦／雲端／郵件／Notion。",
            },
        },
        "required": ["category"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Load the requested family through the injected seam."""
        if deps.open_toolbox is None:
            return {"ok": False, "error": "dynamic toolboxes are not wired on this build"}
        category = kwargs.get("category")
        if not isinstance(category, str):
            return {"ok": False, "error": "category must be a string", "categories": list(TOOLBOX_CATEGORIES)}
        logger.info("Tool call: open_toolbox category=%s", category)
        return await deps.open_toolbox(category)
```

In `prompts.py`, append this section to `_HARDENING_BLOCK` (research §A3: the same rule in the description *and* the prompt, worded the same way, because a contradiction between the two is what measurably degrades selection):

```
### 工具箱
- 一直都在手上的工具：看東西、移動、表情、認人、記憶、開關家裡的燈、放音樂和關音樂、
  上網搜尋、切換對話模式、睡覺。這些直接用，不要先開工具箱。
- 行程、約、會議、待辦、任務、提醒、郵件、寄信、雲端檔案、Notion：
  先呼叫 open_toolbox("productivity")。
- 電視、影片、MV、NAS 上的家庭影片：先呼叫 open_toolbox("media")。
- open_toolbox 回來之後工具就在了：同一輪直接接著呼叫真正要用的那個，
  不要再問使用者一次、也不要說「我幫你打開了工具」。
- open_toolbox 回報失敗的時候，就說你現在拿不到那個功能，不要假裝做過了。
```

Finally, `profiles/_reachy_companion_locked_profile/profile.md`'s `default_tools` settles at exactly these 27 entries, in this order (and `tests/test_profile.py::EXPECTED_TOOLS` must match it exactly):

```
"camera", "look_around", "play_emotion", "dance", "stop_dance", "stop_emotion",
"move_head", "head_tracking", "home_control",
"music", "tv", "nas",
"calendar", "tasks", "drive", "notion_add", "email_send",
"go_to_sleep", "set_conversation_mode", "summarize_conversation", "open_toolbox",
"remember", "forget", "remember_face", "who_is_this", "wait_for_user",
"pollen_robotics_reachy_mini_search_tool__search_web"
```

27 registered + `task_status` + `task_cancel` = 29 total; **22** of them are the static core, which is the start-of-turn surface. The media box adds 2 (→24), the productivity box 5 (→27), and both together 7 (→29) — boxes accumulate within a mode, design decision 8.

- [ ] **Step 4: Run**

Run: `python -m pytest tests/test_toolboxes.py tests/test_record_mode.py tests/test_conversation_modes.py tests/test_profile.py tests/test_prompts_hardening.py tests/test_huggingface_realtime.py tests/test_external_loading.py -v`
Expected: PASS. Then `python -m pytest` — full suite green.

- [ ] **Step 5: Lint, typecheck, commit**

```bash
ruff check . && mypy --strict src
git add reachy_companion/src reachy_companion/tests reachy_companion/profiles
git commit -m "feat(tools): static core plus on-demand toolboxes via open_toolbox"
```

---

### Task 9: Quiesce the mic, the barge machine and the speaker before the sleep pose

**Files:**
- Modify: `reachy_companion/src/reachy_companion/app_lifecycle.py`
- Modify: `reachy_companion/src/reachy_companion/main.py` (`go_to_sleep_and_stop_app` `:304-356`, and the deps wiring near `:358`)
- Modify: `reachy_companion/src/reachy_companion/huggingface_realtime.py` (new `wait_for_reply_finished`)
- Modify: `reachy_companion/src/reachy_companion/tools/core_tools.py` (`ToolDependencies`)
- Modify: `reachy_companion/src/reachy_companion/tools/go_to_sleep.py` (`__call__`)
- Modify test: `reachy_companion/tests/test_app_lifecycle.py`, `reachy_companion/tests/tools/test_go_to_sleep.py` (create if the repo has none)
- Modify: `reachy_companion/.env.example`

**Interfaces — and the ordering, which is the whole task** (Codex round 1 P2-10, reordered in round 2 2a-6):

**Silence first, then wait, then drain, then pose.** Waiting for `response.done` *before* muting would leave the microphone live for up to ten seconds while the robot is already committed to sleeping — long enough for a repeated 「睡覺吧」, or the goodbye's own echo, to open a fresh turn the robot will never answer. And draining before the response has finished emitting measures nothing, because `audio_drain.is_audible()` is legitimately `False` at the instant `response.function_call_arguments.done` arrives. So:

1. **mark sleep pending** — `deps.sleep_requested = True` (still `main.py`'s closure, still the only writer, D-027)
2. **mute the mic** — no new turn can commit from here on
3. **disarm the barge machine** — nothing can resurrect parked audio over a sleeping robot
4. **bounded wait for `response.done`** — every delta of the goodbye now exists
5. **bounded audible drain** — the goodbye finishes coming out of the speaker
6. **sleep pose**, then **app stop**

Steps 1–3 are synchronous and instant; step 4 must happen on the handler's event loop; steps 5–6 are the worker thread's. That splits the quiesce in two:

- Produces (`app_lifecycle`): `begin_sleep_quiesce(stream_manager: Any, logger: logging.Logger) -> None` — steps 2 and 3. Sets `stream_manager._mic_muted = True` and calls the handler's `on_external_interrupt()`. Tolerates `stream_manager is None` and a handler without the method. **Never flushes the player queue** — `clear_audio_queue()` would kill the goodbye this whole sequence exists to protect. Thread-safe (a flag write plus a documented thread-safe call), so either the tool's loop or the worker thread may call it.
- Produces (`app_lifecycle`): `wait_for_speaker_quiet(logger: logging.Logger) -> float` — step 5. Polls `audio_drain.is_audible()` until quiet or the cap expires, returns the seconds waited, and logs `sleep quiesce: speaker quiet after N.Ns` **only when the speaker actually went quiet**, `sleep quiesce: drain cap reached after N.Ns with audio still playing` otherwise (Codex round 1, P2-11). Worker-thread safe (`time.sleep`, never `asyncio`).
- Produces (`app_lifecycle`): `SLEEP_DRAIN_POLL_S: Final[float] = 0.1`, `SLEEP_DRAIN_CAP_DEFAULT_S: Final[float] = 6.0`, `sleep_drain_cap_s() -> float` — reads `SLEEP_GOODBYE_DRAIN_CAP_S`, default 6.0, clamped 0.0–15.0.
- Produces (deps seam): `ToolDependencies.begin_sleep: Callable[[], None] | None = None` — a `main.py` closure that does steps 1–3 (marks `deps.sleep_requested`, then `app_lifecycle.begin_sleep_quiesce(stream_manager, logger)`). Idempotent: `go_to_sleep_and_stop_app` sets `sleep_requested` again and `begin_sleep_quiesce` is harmless to repeat.
- Produces (handler): `async wait_for_reply_finished(self) -> bool` — step 4. Bounded on `self._response_done_event`; module constant `_GOODBYE_RESPONSE_WAIT_S: Final[float] = 10.0`.
- Produces (deps seam): `ToolDependencies.wait_for_reply_finished: Callable[[], Awaitable[bool]] | None = None`, wired in `main.py` next to `deps.go_to_sleep`.
- **`wait_for_reply_finished` must be loop-aware** (Codex round 2, 2a-5). The inactivity path reaches `GoToSleep` through `app_lifecycle.run_go_to_sleep_tool` (`app_lifecycle.py:78-84`), which does `asyncio.run(GoToSleep()(deps))` on a **daemon thread with its own fresh event loop** — awaiting `_response_done_event` (an `asyncio.Event` bound to the handler's loop) from there is undefined behavior. So the handler captures its loop and, when called from anywhere else, marshals through `asyncio.run_coroutine_threadsafe` with the same bounded timeout, returning `True` (nothing to wait for) when the loop is gone or never started.
- Call sites: `GoToSleep.__call__` performs steps 1–4; `main.py`'s `go_to_sleep_and_stop_app` performs steps 1 (idempotent), 5 and 6, with the existing `logger.info("Going to sleep before stopping conversation app.")` line moved **above** the drain so the documented journal order actually holds (Codex round 2, 2a-7).
- Consumes: `LocalStream._mic_muted` (`console.py:131`, read by `record_loop` `:912`), `LocalStream.handler` (`console.py:146`, re-pointed on every handler rebuild), `HuggingFaceRealtimeHandler.on_external_interrupt()` (`huggingface_realtime.py:911-947`, documented thread-safe), `audio_drain.is_audible()` (`hanova/audio_drain.py:223`), `self._response_done_event` (`huggingface_realtime.py:506`), `env_float`.
- Env produced: `SLEEP_GOODBYE_DRAIN_CAP_S` (default `6.0`).
- **No deadlock:** `GoToSleep.needs_response = False`, so no follow-up response is queued behind the wait, and `response.done` for the goodbye does not depend on the tool result being submitted — the model emits the function call and then finishes the response on its own.

- [ ] **Step 1: Write the failing tests** — append to `reachy_companion/tests/test_app_lifecycle.py`:

```python
# --------------------------------------------------------------------------
# Sleep quiesce (2026-08-31 plan, Task 9)
# --------------------------------------------------------------------------


def _quiesce_stream(handler: object | None = None) -> SimpleNamespace:
    return SimpleNamespace(_mic_muted=False, handler=handler)


def test_begin_sleep_quiesce_mutes_the_mic_and_disarms_the_barge_machine():
    """Step 2 and 3, and they come BEFORE any waiting (Codex round 2, 2a-6).

    Waiting for `response.done` with the microphone still live would leave up to
    ten seconds in which a repeated 「睡覺吧」 or the goodbye's own echo opens a
    turn the robot will never answer.
    """
    from reachy_companion import app_lifecycle

    calls: list[str] = []
    stream = _quiesce_stream(SimpleNamespace(on_external_interrupt=lambda: calls.append("disarm")))
    app_lifecycle.begin_sleep_quiesce(stream, logging.getLogger("test"))
    assert stream._mic_muted is True
    assert calls == ["disarm"]


def test_begin_sleep_quiesce_never_flushes_the_player():
    """`clear_audio_queue` would kill the very goodbye we are protecting."""
    from reachy_companion import app_lifecycle

    flushed: list[str] = []
    stream = _quiesce_stream(SimpleNamespace(on_external_interrupt=lambda: None))
    stream.clear_audio_queue = lambda: flushed.append("flush")
    app_lifecycle.begin_sleep_quiesce(stream, logging.getLogger("test"))
    assert flushed == []


def test_begin_sleep_quiesce_tolerates_a_missing_stream_and_handler():
    from reachy_companion import app_lifecycle

    app_lifecycle.begin_sleep_quiesce(None, logging.getLogger("test"))
    app_lifecycle.begin_sleep_quiesce(_quiesce_stream(object()), logging.getLogger("test"))


def test_wait_for_speaker_quiet_stops_as_soon_as_it_is_quiet(monkeypatch):
    """The goodbye finishes playing, then we stop waiting."""
    from reachy_companion import app_lifecycle
    from reachy_companion.hanova import audio_drain

    audible = iter([True, True, False, False, False])
    monkeypatch.setattr(audio_drain, "is_audible", lambda: next(audible, False))
    monkeypatch.setattr(app_lifecycle, "SLEEP_DRAIN_POLL_S", 0.001)
    waited = app_lifecycle.wait_for_speaker_quiet(logging.getLogger("test"))
    assert waited >= 0.0
    assert next(audible, "exhausted") != "exhausted"  # the loop stopped early


def test_wait_for_speaker_quiet_is_bounded_by_the_cap(monkeypatch, caplog):
    """A stuck drain estimate must not hold the robot awake forever.

    And the outcome is logged honestly: the cap expiring with audio still
    playing is not "speaker quiet" (Codex round 1, P2-11).
    """
    from reachy_companion import app_lifecycle
    from reachy_companion.hanova import audio_drain

    monkeypatch.setenv("SLEEP_GOODBYE_DRAIN_CAP_S", "0.05")
    monkeypatch.setattr(audio_drain, "is_audible", lambda: True)
    monkeypatch.setattr(app_lifecycle, "SLEEP_DRAIN_POLL_S", 0.001)
    with caplog.at_level(logging.INFO):
        waited = app_lifecycle.wait_for_speaker_quiet(logging.getLogger("test"))
    assert 0.04 <= waited <= 1.0
    assert "drain cap reached" in caplog.text
    assert "speaker quiet" not in caplog.text


def test_wait_for_speaker_quiet_reports_a_real_drain_as_quiet(monkeypatch, caplog):
    from reachy_companion import app_lifecycle
    from reachy_companion.hanova import audio_drain

    monkeypatch.setattr(audio_drain, "is_audible", lambda: False)
    with caplog.at_level(logging.INFO):
        app_lifecycle.wait_for_speaker_quiet(logging.getLogger("test"))
    assert "speaker quiet" in caplog.text
    assert "drain cap reached" not in caplog.text


@pytest.mark.asyncio
async def test_the_sleep_tool_silences_first_then_waits_then_sleeps() -> None:
    """The whole ordering claim, as one assertion (Codex round 2, 2a-6).

    Silence the inputs, THEN wait for the goodbye to finish generating, THEN
    hand off to the thread that drains and poses. Waiting first leaves the mic
    live; draining first measures audio that does not exist yet.
    """
    from reachy_companion.tools.go_to_sleep import GoToSleep

    order: list[str] = []

    async def _wait() -> bool:
        order.append("wait")
        return True

    deps = SimpleNamespace(
        begin_sleep=lambda: order.append("silence"),
        wait_for_reply_finished=_wait,
        go_to_sleep=lambda: (order.append("sleep"), {"status": "sleeping"})[1],
    )
    result = await GoToSleep()(deps)
    assert order == ["silence", "wait", "sleep"]
    assert result == {"status": "sleeping"}


@pytest.mark.asyncio
async def test_the_sleep_tool_still_sleeps_if_the_wait_times_out() -> None:
    """A reply that never ends must not leave the robot permanently awake."""
    from reachy_companion.tools.go_to_sleep import GoToSleep

    async def _wait() -> bool:
        return False

    calls: list[str] = []
    deps = SimpleNamespace(
        begin_sleep=lambda: None,
        wait_for_reply_finished=_wait,
        go_to_sleep=lambda: (calls.append("sleep"), {"status": "sleeping"})[1],
    )
    assert (await GoToSleep()(deps))["status"] == "sleeping"
    assert calls == ["sleep"]


@pytest.mark.asyncio
async def test_the_sleep_tool_works_without_the_new_seams() -> None:
    """Older construction sites keep working with both seams simply absent."""
    from reachy_companion.tools.go_to_sleep import GoToSleep

    deps = SimpleNamespace(
        begin_sleep=None, wait_for_reply_finished=None, go_to_sleep=lambda: {"status": "sleeping"}
    )
    assert (await GoToSleep()(deps))["status"] == "sleeping"


def test_wait_for_reply_finished_is_safe_from_another_loop() -> None:
    """The inactivity path runs the tool under its own `asyncio.run` loop.

    `app_lifecycle.run_go_to_sleep_tool` does exactly that on a daemon thread,
    so awaiting the handler's `asyncio.Event` directly there is undefined
    (Codex round 2, 2a-5). The seam must marshal, or give up cleanly.
    """
    import asyncio as _asyncio
    import threading

    from reachy_companion.openai_realtime import OpenAIRealtimeHandler

    handler = OpenAIRealtimeHandler.__new__(OpenAIRealtimeHandler)
    handler.connection = object()  # a live session; see the dead-session test below
    results: list[bool] = []
    ready = threading.Event()
    stop = threading.Event()

    def _run_handler_loop() -> None:
        loop = _asyncio.new_event_loop()
        _asyncio.set_event_loop(loop)
        handler._response_done_event = _asyncio.Event()
        handler._handler_loop = loop
        ready.set()
        loop.run_until_complete(_asyncio.sleep(0.05))
        handler._response_done_event.set()
        loop.run_until_complete(_asyncio.sleep(0.15))
        stop.set()
        loop.close()

    thread = threading.Thread(target=_run_handler_loop, daemon=True)
    thread.start()
    ready.wait(timeout=2.0)
    # A DIFFERENT loop, exactly as `run_go_to_sleep_tool` creates.
    results.append(_asyncio.run(handler.wait_for_reply_finished()))
    stop.wait(timeout=2.0)
    thread.join(timeout=2.0)
    assert results == [True]


def test_wait_for_reply_finished_gives_up_when_the_loop_is_gone() -> None:
    """No handler loop at all: report success rather than hang or raise."""
    import asyncio as _asyncio

    from reachy_companion.openai_realtime import OpenAIRealtimeHandler

    handler = OpenAIRealtimeHandler.__new__(OpenAIRealtimeHandler)
    handler.connection = object()
    handler._response_done_event = _asyncio.Event()
    handler._handler_loop = None
    assert _asyncio.run(handler.wait_for_reply_finished()) is True


@pytest.mark.asyncio
async def test_wait_for_reply_finished_does_not_wait_on_a_dead_session() -> None:
    """A live loop, an unset event, and no connection: still instant.

    `_response_done_event` is cleared by `response.created` and set by
    `response.done`; a session that dies mid-response leaves it clear forever,
    and without this the shutdown path pays the full ten seconds every time
    (Codex round 3, finding 2).
    """
    import asyncio as _asyncio

    from reachy_companion.openai_realtime import OpenAIRealtimeHandler

    handler = OpenAIRealtimeHandler.__new__(OpenAIRealtimeHandler)
    handler.connection = None
    handler._response_done_event = _asyncio.Event()  # deliberately NOT set
    handler._handler_loop = _asyncio.get_running_loop()
    started = _asyncio.get_running_loop().time()
    assert await handler.wait_for_reply_finished() is True
    assert _asyncio.get_running_loop().time() - started < 1.0


def test_sleep_drain_cap_clamps(monkeypatch):
    from reachy_companion import app_lifecycle

    monkeypatch.delenv("SLEEP_GOODBYE_DRAIN_CAP_S", raising=False)
    assert app_lifecycle.sleep_drain_cap_s() == 6.0
    monkeypatch.setenv("SLEEP_GOODBYE_DRAIN_CAP_S", "999")
    assert app_lifecycle.sleep_drain_cap_s() == 15.0
    monkeypatch.setenv("SLEEP_GOODBYE_DRAIN_CAP_S", "nonsense")
    assert app_lifecycle.sleep_drain_cap_s() == 6.0
```

(Add `import logging` and `from types import SimpleNamespace` to the file's imports if they are not already there.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_app_lifecycle.py -k "sleep or quiet or reply_finished or dead_session" -v`
Expected: FAIL — `AttributeError: module 'reachy_companion.app_lifecycle' has no attribute 'begin_sleep_quiesce'`.

- [ ] **Step 3: Implement**

In `app_lifecycle.py`, add the imports (`time`, `typing.Any`, `typing.Final`, `reachy_companion.hanova.audio_drain`, `reachy_companion.audio.envparse.env_float`) and:

```python
# --- sleep quiesce (2026-08-31 plan) ----------------------------------------
# Observed on-robot 2026-08-31: `go_to_sleep` ran the sleep pose immediately
# while the mic, the player and the barge machine stayed live for another five
# to ten seconds. The journal shows a goodbye spoken after the body was already
# asleep, a second `go_to_sleep` from a repeated command, and a
# `barge-in rolled back; resuming reply` resurrecting parked audio over a
# sleeping robot. The cure is ordering, not new machinery — and the order is
# silence, then wait, then drain, then pose (Codex round 2, 2a-6). Silencing
# last would leave the microphone live for the whole of the wait.
SLEEP_DRAIN_POLL_S: Final[float] = 0.1
SLEEP_DRAIN_CAP_DEFAULT_S: Final[float] = 6.0


def sleep_drain_cap_s() -> float:
    """Longest the sleep pose waits for the goodbye to finish playing."""
    return env_float("SLEEP_GOODBYE_DRAIN_CAP_S", SLEEP_DRAIN_CAP_DEFAULT_S, lo=0.0, hi=15.0)


def begin_sleep_quiesce(stream_manager: Any, logger: logging.Logger) -> None:
    """Silence the robot's inputs. First thing the sleep path does.

    Thread-agnostic by construction, because both callers exist: the tool's own
    event loop reaches it through `deps.begin_sleep`, and the worker thread
    reaches it again (idempotently) from `go_to_sleep_and_stop_app`.
    `_mic_muted` is a plain flag the record loop reads (`console.py:912`) and
    `on_external_interrupt()` marshals its cancels onto the handler's own loop
    (`huggingface_realtime.py:911-947`).

    Two deliberate omissions:

    * **No flush.** `clear_audio_queue()` would run `on_external_interrupt`
      *and* drop the player queue — which holds the goodbye the model spoke in
      the same response as the tool call. That audio is the whole point.
    * **No `turn_detection = None` push.** Muting the mic is the cheaper hard
      stop and needs no round trip to a server we are about to disconnect from.
    """
    if stream_manager is not None:
        # Cheapest hard stop: frames never reach `handler.receive`, so no new
        # turn can commit between here and the disconnect — including one the
        # goodbye's own echo would otherwise open while we wait for it.
        stream_manager._mic_muted = True
        logger.info("sleep quiesce: microphone muted")
    handler = getattr(stream_manager, "handler", None)
    disarm = getattr(handler, "on_external_interrupt", None)
    if callable(disarm):
        # Every barge timer stands down and the pause state is dropped, so
        # nothing can resume parked audio over a sleeping robot.
        disarm()
        logger.info("sleep quiesce: barge machine disarmed")


def wait_for_speaker_quiet(logger: logging.Logger) -> float:
    """Wait, bounded, for the goodbye to finish playing. Returns seconds waited.

    Runs on the `go_to_sleep` worker thread (`tools/go_to_sleep.py` hands the
    closure to `asyncio.to_thread`), so it blocks with `time.sleep`;
    `audio_drain.is_audible()` takes the module lock and is safe from there.

    Bounded for the boot gate's reason (`huggingface_realtime.py:715-730`): a
    stuck drain estimate must never hold the robot awake. By the time this runs,
    the inputs are already silenced and the response has already finished
    emitting, so everything it is waiting on is audio that genuinely exists.
    """
    started = time.monotonic()
    deadline = started + sleep_drain_cap_s()
    while audio_drain.is_audible() and time.monotonic() < deadline:
        time.sleep(SLEEP_DRAIN_POLL_S)
    waited = time.monotonic() - started
    if audio_drain.is_audible():
        # The cap expired with audio still playing. Saying "speaker quiet" here
        # would make the journal claim the goodbye finished when the pose that
        # follows is about to cut it off (Codex round 1, P2-11).
        logger.info("sleep quiesce: drain cap reached after %.1fs with audio still playing", waited)
    else:
        logger.info("sleep quiesce: speaker quiet after %.1fs", waited)
    return waited
```

In `huggingface_realtime.py`, next to `_wait_for_response_done_before_tool_result` (`:1415`):

```python
# How long the sleep path waits for the goodbye's response to finish being
# generated. Longer than a normal reply needs, short enough that a wedged
# response cannot leave the robot standing there indefinitely.
_GOODBYE_RESPONSE_WAIT_S: Final[float] = 10.0
```

```python
    async def wait_for_reply_finished(self) -> bool:
        """Wait for the response now being generated to finish. Never raises.

        Step 4 of the sleep path (Codex round 1, P2-10). `go_to_sleep` is called
        from the tool worker the instant
        `response.function_call_arguments.done` arrives — which is BEFORE
        `response.done` and before the rest of the goodbye's audio deltas exist.
        Draining the speaker at that moment finds nothing audible and the robot
        lies down mid-sentence. Waiting here means every delta of the goodbye is
        enqueued before anything starts measuring whether it has played. The
        microphone and the barge machine are already silenced by the time this
        runs (round 2, 2a-6), so the wait cannot let a new turn in.

        **Loop-aware** (round 2, 2a-5): the inactivity path reaches `GoToSleep`
        through `app_lifecycle.run_go_to_sleep_tool`, which does
        `asyncio.run(...)` on a daemon thread with its own fresh loop. Awaiting
        `_response_done_event` — an `asyncio.Event` bound to the handler's
        loop — from there is undefined behavior, so a caller on any other loop
        is marshalled across. No handler loop at all means nothing to wait for,
        which is success, not failure.
        """
        event = self._response_done_event
        if event.is_set():
            return True
        loop = getattr(self, "_handler_loop", None)
        if loop is None or loop.is_closed():
            return True
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            try:
                await asyncio.wait_for(event.wait(), timeout=_GOODBYE_RESPONSE_WAIT_S)
                return True
            except asyncio.TimeoutError:
                return False
        future = asyncio.run_coroutine_threadsafe(
            asyncio.wait_for(event.wait(), timeout=_GOODBYE_RESPONSE_WAIT_S), loop
        )
        try:
            await asyncio.to_thread(future.result, _GOODBYE_RESPONSE_WAIT_S + 1.0)
            return True
        except Exception:  # noqa: BLE001 - timeout, cancellation, or a dead loop
            future.cancel()
            return False
```

`_handler_loop` is the handler's own running loop, captured where the session is established. Add the field to `__init__` (`self._handler_loop: asyncio.AbstractEventLoop | None = None`) and set it at the top of `_run_realtime_session`, beside the other per-session resets:

```python
        # The loop this handler's session runs on, so a caller from another
        # thread's loop can marshal onto it (round 2, 2a-5).
        self._handler_loop = asyncio.get_running_loop()
```

and **release both in that session's `finally`** (`:2835`, beside `_barge_shutdown` and the `_receive_loop_active` clear), so nothing is left waiting on a loop or an event that belongs to a session which has ended (Codex round 3, finding 2):

```python
                # A session that dies mid-response leaves `_response_done_event`
                # clear forever. Anything still waiting on it — the sleep path
                # most of all — is waiting for a response that can no longer
                # finish, so end the wait rather than let it burn its timeout.
                self._response_done_event.set()
                self._handler_loop = None
```

In `tools/core_tools.py`, next to `go_to_sleep`:

```python
    # Silences the mic and disarms the barge machine before anything waits or
    # poses (Codex round 2, 2a-6). Optional for the same reason as
    # `go_to_sleep` itself: every other construction site keeps working with it
    # simply absent.
    begin_sleep: Callable[[], None] | None = None
    # Lets `go_to_sleep` wait for the goodbye's response to finish generating
    # before the body is put to sleep (Codex round 1, P2-10). Same optionality.
    wait_for_reply_finished: Callable[[], Awaitable[bool]] | None = None
```

In `main.py`, define the step-1-to-3 closure next to `go_to_sleep_and_stop_app` and wire both seams beside `deps.go_to_sleep = go_to_sleep_and_stop_app` (`:358`):

```python
    def begin_sleep() -> None:
        """Mark the visit over and silence the inputs, before anything waits."""
        # D-027: still the only writer of this flag, and setting it twice is a
        # no-op — `go_to_sleep_and_stop_app` sets it again on its own path.
        deps.sleep_requested = True
        go_to_sleep_requested.set()
        app_lifecycle.begin_sleep_quiesce(stream_manager, logger)

    deps.begin_sleep = begin_sleep
    deps.wait_for_reply_finished = handler.wait_for_reply_finished
```

(and re-point `deps.wait_for_reply_finished` inside `build_handler` alongside `deps.set_conversation_mode`, so a handler rebuilt by the settings UI keeps the seam correct. `begin_sleep` closes over `stream_manager`, which is not rebuilt.)

In `tools/go_to_sleep.py`, `__call__`:

```python
    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> dict[str, Any]:
        """Silence, wait for the goodbye to finish, then put Reachy to sleep."""
        if deps.go_to_sleep is None:
            return {"error": "go_to_sleep is unavailable in this runtime"}

        logger.info("Tool call: go_to_sleep")
        # Order is the fix (Codex round 2, 2a-6). Silence first: the wait below
        # can take seconds, and a live microphone through it means a repeated
        # 「睡覺吧」 or the goodbye's own echo opens a turn nobody will answer.
        if deps.begin_sleep is not None:
            deps.begin_sleep()
        # Then wait, because the closure below hands off to a worker thread that
        # measures whether the speaker has gone quiet — and measuring that
        # before the response has finished emitting is measuring nothing
        # (Codex round 1, P2-10).
        if deps.wait_for_reply_finished is not None:
            if not await deps.wait_for_reply_finished():
                logger.warning("go_to_sleep: the goodbye response did not finish in time; sleeping anyway")
        try:
            return await asyncio.to_thread(deps.go_to_sleep)
        except Exception as e:
            logger.error("go_to_sleep failed: %s", e)
            return {"error": f"go_to_sleep failed: {type(e).__name__}: {e}"}
```

In `main.py`'s `go_to_sleep_and_stop_app`, keep `deps.sleep_requested = True` where it is (`:316`), leave `logger.info("Going to sleep before stopping conversation app.")` at `:318` **where it already is** — above everything that follows, so the documented journal order holds (Codex round 2, 2a-7) — and insert the drain between that log and `robot.disable_wobbling()` (`:322`):

```python
            # Silencing already happened in the tool (deps.begin_sleep); repeat
            # it here so the timeout/inactivity paths, which reach this closure
            # without the tool, get it too. Both calls are idempotent.
            app_lifecycle.begin_sleep_quiesce(stream_manager, logger)
            # The response has finished emitting by now, so what this waits on
            # is audio that genuinely exists: let the goodbye out of the speaker
            # before the body lies down.
            app_lifecycle.wait_for_speaker_quiet(logger)
```

(`app_lifecycle` is already imported in `main.py:18`; `stream_manager` is already in the closure's scope and is already used at `:342`.)

- [ ] **Step 4: Run**

Run: `python -m pytest tests/test_app_lifecycle.py tests/test_main.py tests/test_console.py tests/tools/ tests/test_huggingface_realtime.py -v`
Expected: PASS. Then `python -m pytest` — full suite green.

- [ ] **Step 5: Document the knob, lint, typecheck, commit**

Add to `reachy_companion/.env.example`, in the realtime knob section:

```
# Longest the sleep pose waits for the goodbye to finish playing before Reachy
# lies down, in seconds (0.0-15.0). By then the mic is already muted, the barge
# machine disarmed and the reply finished generating, so nothing new can start
# during the wait; the cap only exists so a stuck drain estimate cannot hold the
# robot awake.
# SLEEP_GOODBYE_DRAIN_CAP_S=6.0
```

```bash
ruff check . && mypy --strict src
git add reachy_companion/src reachy_companion/tests reachy_companion/.env.example
git commit -m "fix(sleep): quiesce mic, barge machine and speaker before the sleep pose"
```

---

### Task 10: Drop `commentary`-phase output items

**Files:**
- Modify: `reachy_companion/src/reachy_companion/huggingface_realtime.py` (`__init__` `:551`; a new `response.output_item.added` branch; the audio-delta branch `:2686-2692`; the assistant-transcript branch `:2676-2683`; `on_external_interrupt` `:911-947`)
- Modify test: `reachy_companion/tests/test_huggingface_realtime.py`

**Interfaces:**
- Produces (handler): `self._commentary_item_ids: deque[str]` (`maxlen=8`, same bound and rationale as `_cancelled_response_ids` `:551`).
- Produces (module level): `_item_phase(item: Any) -> str | None` — defensive reader that works for a pydantic model (`getattr`) and for a plain dict (`.get`), returning `None` for anything else.
- Behavior: an output item whose phase is `"commentary"` has its id remembered; audio deltas and the final transcript for that id are dropped with `logger.debug("dropping commentary-phase %s for item %s", …)`. Everything else is untouched.
- Consumes: nothing new. The installed `openai 2.28.0` `ResponseOutputItemAddedEvent` has no `phase` field, but `openai._models.BaseModel` is `ConfigDict(extra="allow")` (verified: `openai/_models.py:118`), so a server-sent `phase` arrives as an attribute. `[OFFICIAL]` `gpt-realtime-2.x` output items carry `phase ∈ {commentary, final_answer}` and preambles are on by default with no documented boolean to disable them (research doc §C6).

- [ ] **Step 1: Write the failing tests** — append to `reachy_companion/tests/test_huggingface_realtime.py`, using that file's existing `_FakeEvent` helper:

```python
# --------------------------------------------------------------------------
# Commentary-phase suppression (2026-08-31 plan, Task 10)
# --------------------------------------------------------------------------


def test_item_phase_reads_models_and_dicts() -> None:
    from types import SimpleNamespace

    from reachy_companion.huggingface_realtime import _item_phase

    assert _item_phase(SimpleNamespace(phase="commentary")) == "commentary"
    assert _item_phase({"phase": "final_answer"}) == "final_answer"
    assert _item_phase(SimpleNamespace(id="item_1")) is None
    assert _item_phase(None) is None
    assert _item_phase(object()) is None


@pytest.mark.asyncio
async def test_commentary_audio_and_transcript_are_dropped(monkeypatch: Any) -> None:
    """2.x emits preambles by default; they must not play as normal speech."""
    import base64

    monkeypatch.setattr(hf_mod, "get_session_instructions", lambda _instance_path=None: "test")
    monkeypatch.setattr(hf_mod, "get_session_voice", lambda default=HF_DEFAULT_VOICE: "Aiden")
    monkeypatch.setattr(hf_mod, "get_tool_specs", lambda exclusion_list=None: [])

    silence = base64.b64encode(np.zeros(240, dtype=np.int16).tobytes()).decode("utf-8")
    handler = HuggingFaceRealtimeHandler(ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock()))
    handler.client = _make_fake_realtime_client(
        events=(
            _FakeEvent(
                "response.output_item.added",
                item={"id": "item_pre", "phase": "commentary"},
                response_id="resp_1",
                output_index=0,
            ),
            _FakeEvent("response.output_audio.delta", item_id="item_pre", response_id="resp_1", delta=silence),
            _FakeEvent("response.output_audio_transcript.done", item_id="item_pre", transcript="讓我想想喔"),
            _FakeEvent(
                "response.output_item.added",
                item={"id": "item_ans", "phase": "final_answer"},
                response_id="resp_1",
                output_index=1,
            ),
            _FakeEvent("response.output_audio.delta", item_id="item_ans", response_id="resp_1", delta=silence),
            _FakeEvent("response.output_audio_transcript.done", item_id="item_ans", transcript="三點二十"),
        )
    )
    monkeypatch.setattr(type(handler.tool_manager), "start_up", MagicMock())
    monkeypatch.setattr(type(handler.tool_manager), "shutdown", AsyncMock())
    transcripts: list[tuple[str, str]] = []
    handler.set_transcript_observer(lambda role, text, final: transcripts.append((role, text)))

    await handler._run_realtime_session()

    assert transcripts == [("assistant", "三點二十")]
    # Exactly one audio tuple reached the output queue: the answer's, not the
    # preamble's.
    queued = []
    while not handler.output_queue.empty():
        queued.append(handler.output_queue.get_nowait())
    assert len([item for item in queued if isinstance(item, tuple)]) == 1
    assert handler._commentary_item_ids == deque(["item_pre"], maxlen=8)


def test_external_interrupt_forgets_commentary_ids() -> None:
    """A stale id must not suppress a real item in the session that replaces it."""
    handler = HuggingFaceRealtimeHandler(ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock()))
    handler._commentary_item_ids.append("item_pre")
    handler.on_external_interrupt()
    assert not handler._commentary_item_ids
```

(`_FakeEvent` `:38`, `_make_fake_realtime_client` `:47` and the `hf_mod` / `HF_DEFAULT_VOICE` / `ToolDependencies` / `MagicMock` / `AsyncMock` / `np` imports already exist in `tests/test_huggingface_realtime.py`; the three `monkeypatch.setattr` lines and the `_run_realtime_session()` drive are copied from `_run_with_response_done` at `:908-919`. Add `from collections import deque` to the file's imports if it is not already there.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_huggingface_realtime.py -k "commentary or item_phase" -v`
Expected: FAIL — `ImportError: cannot import name '_item_phase'`.

- [ ] **Step 3: Implement**

Module level in `huggingface_realtime.py`, near the other small readers:

```python
def _item_phase(item: Any) -> str | None:
    """Return an output item's `phase`, for a model or a dict, else None.

    `gpt-realtime-2.x` generates preambles by DEFAULT and tags each output item
    `commentary` or `final_answer`; there is no documented switch to turn them
    off (research doc §C6). The installed openai 2.28.0 stub predates the field,
    but `openai._models.BaseModel` is `extra="allow"`, so it arrives as a plain
    attribute. Defensive on both shapes because this is an undeclared field on a
    wire format we do not control.
    """
    if item is None:
        return None
    if isinstance(item, dict):
        phase = item.get("phase")
    else:
        phase = getattr(item, "phase", None)
    return phase if isinstance(phase, str) else None
```

In `__init__`, next to `_cancelled_response_ids` (`:551`):

```python
        # Output items the model tagged `commentary` — 2.x preambles. Their
        # audio and transcript are dropped so a "讓我想想喔" never plays as
        # speech and never counts toward the reply the brevity rules judge.
        # Tiny bound, same as above: only very recent ids can matter.
        self._commentary_item_ids: deque[str] = deque(maxlen=8)
```

New event branch, placed immediately before the `response.output_audio.done` branch (`:2427`):

```python
                    if event.type == "response.output_item.added":
                        item = getattr(event, "item", None)
                        if _item_phase(item) == "commentary":
                            item_id = item.get("id") if isinstance(item, dict) else getattr(item, "id", None)
                            if isinstance(item_id, str):
                                self._commentary_item_ids.append(item_id)
                                logger.debug("suppressing commentary-phase item %s", item_id)
```

In the audio-delta branch, right after the cancelled-response drop (`:2687-2692`):

```python
                        if getattr(event, "item_id", None) in self._commentary_item_ids:
                            logger.debug(
                                "dropping commentary-phase audio for item %s", getattr(event, "item_id", None)
                            )
                            continue
```

In the assistant-transcript branch, as the first statement of the block (`:2677`):

```python
                        if getattr(event, "item_id", None) in self._commentary_item_ids:
                            logger.debug(
                                "dropping commentary-phase transcript for item %s",
                                getattr(event, "item_id", None),
                            )
                            continue
```

In `on_external_interrupt` (`:911-947`), next to the `_audio_item_id` clears:

```python
        # A commentary id from an abandoned turn must not suppress a real item
        # in the session that replaces it (ids are unique, but the bound is
        # small and a stale entry is pure risk).
        self._commentary_item_ids.clear()
```

Add `self._commentary_item_ids = deque(maxlen=8)` to `_install_barge_state` in `tests/test_solo_barge.py` (`:43-68`) so every `__new__`-built harness carries it.

- [ ] **Step 4: Run**

Run: `python -m pytest tests/test_huggingface_realtime.py tests/test_solo_barge.py tests/test_party_mode.py tests/test_conversation_modes.py -v`
Expected: PASS. Then `python -m pytest` — full suite green.

- [ ] **Step 5: Lint, typecheck, commit**

```bash
ruff check . && mypy --strict src
git add reachy_companion/src reachy_companion/tests
git commit -m "fix(voice): drop commentary-phase output items"
```

---

### Task 11: Verbatim envelopes and brevity few-shots

**Files:**
- Modify: `reachy_companion/src/reachy_companion/tools/who_is_this.py` (description `:18-28`, `__call__` `:35-62`)
- Modify: `reachy_companion/src/reachy_companion/prompts.py` (`_HARDENING_BLOCK` `:25-58`)
- Modify test: `reachy_companion/tests/test_face_tools.py`
- Modify test: `reachy_companion/tests/test_prompts_hardening.py`

**Interfaces:**
- Produces (`who_is_this` result, recognized only): `result["response_text"]: str` and `result["require_repeat_verbatim"]: True`. `response_text` is `<name>` when there are no facts, and `f"{name}。{facts[0]}"` when there is at least one — one name plus one fact sentence, because a longer recitation is exactly what the mini tier paraphrases. Every existing key (`status`, `name`, `score`, `known_facts`) is unchanged.
- Produces (`prompts`): three new sections inside `_HARDENING_BLOCK`, with these exact headings — `### 回答長度範例（示範語氣，不是觸發條件）`, `### 工具結果要照著唸`, `### 只講真的做過的事`.
- Consumes: nothing new.
- Constraint reminder: **no numeric caps, no keyword lists.** The mechanism is concrete examples (`[OFFICIAL]` "the model strongly closely follows sample phrases"), labelled as style examples because `[COMMUNITY]` reports 2.1-mini matches prompt example phrases too literally as triggers (research doc §D3).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_prompts_hardening.py`:

```python
def test_hardening_block_carries_the_verbatim_and_example_rules() -> None:
    from reachy_companion.prompts import hardening_block

    block = hardening_block()
    for phrase in (
        "回答長度範例",
        "require_repeat_verbatim",
        "speak_verbatim",
        "response_text",
        "summary_text",
        "只講真的做過的事",
    ):
        assert phrase in block


def test_hardening_block_states_no_numeric_length_cap() -> None:
    """Operator ruling: calibration, never a number (D-028, and the user memory)."""
    from reachy_companion.prompts import hardening_block

    block = hardening_block()
    for banned in ("一到兩句", "不超過兩句", "最多三句", "1-2 sentences"):
        assert banned not in block
```

Append to `tests/test_face_tools.py` (reusing that file's existing `who_is_this` fixtures and monkeypatching of `identify_with_retries` / `facts_for_person`):

```python
@pytest.mark.asyncio
async def test_who_is_this_wraps_a_recognition_in_a_verbatim_envelope(tmp_path: Path) -> None:
    """Research §C3: a raw string plus 'say it exactly' is what mini paraphrases."""
    add_person_fact(tmp_path, "雲霓", "喜歡爬山")
    recognizer = _FakeRecognizer(Identification(status="recognized", name="雲霓", score=0.81, face_count=1))

    result = await WhoIsThis()(_deps(recognizer, instance_path=tmp_path))

    assert result["response_text"] == "雲霓。喜歡爬山"
    assert result["require_repeat_verbatim"] is True
    assert result["name"] == "雲霓"
    assert result["known_facts"] == ["喜歡爬山"]
    _assert_carries_no_image(result)


@pytest.mark.asyncio
async def test_who_is_this_envelope_without_facts() -> None:
    """Recognized with nothing on file: the name alone is the whole envelope."""
    recognizer = _FakeRecognizer(Identification(status="recognized", name="雲霓", score=0.71, face_count=1))

    result = await WhoIsThis()(_deps(recognizer))

    assert result["response_text"] == "雲霓"
    assert result["require_repeat_verbatim"] is True
    assert result["known_facts"] == []


@pytest.mark.asyncio
async def test_who_is_this_unknown_carries_no_envelope(instant_sleep: None) -> None:
    """Nothing to repeat, so nothing to repeat verbatim."""
    recognizer = _FakeRecognizer(Identification(status="unknown", score=0.21, face_count=1))

    result = await WhoIsThis()(_deps(recognizer))

    assert "response_text" not in result
    assert "require_repeat_verbatim" not in result
```

(`_deps`, `_FakeRecognizer`, `_assert_carries_no_image`, `instant_sleep`, `add_person_fact`, `Identification` and `Path` are all already defined or imported in `tests/test_face_tools.py` — `_deps` at `:129`, `_FakeRecognizer` at `:58`, `_assert_carries_no_image` at `:264`, the `instant_sleep` fixture at `:250`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_prompts_hardening.py tests/test_face_tools.py -k "verbatim or envelope or numeric_length" -v`
Expected: FAIL — `KeyError: 'response_text'` / `assert '回答長度範例' in block`.

- [ ] **Step 3: Implement**

In `who_is_this.py`, append to the description (before the closing paren):

```python
        "When recognized, the result also carries `response_text` and `require_repeat_verbatim`: while that "
        "flag is true, say `response_text` out loud EXACTLY as returned — do not change the name, do not add, "
        "omit or reorder anything — and only then continue naturally."
```

In `_attach_known_facts`, after `result["known_facts"] = [fact.text for fact in facts]`, and in `__call__` after the `_attach_known_facts` await, build the envelope:

```python
    @staticmethod
    def _attach_verbatim_envelope(result: dict[str, Any], name: str) -> None:
        """Wrap the name (and one fact) in the cookbook's verbatim envelope.

        Research doc §C3, reproducing OpenAI's own diagnosis of this exact bug:
        "If your tool returns a raw string and separately asks the model to
        'repeat exactly', the model may be more prone to paraphrasing,
        truncation, or blending in its own preamble." On 2026-08-31 that is
        precisely what happened — told to repeat 雲霓, the model said a
        different name. So the authoritative text becomes a named field with the
        flag travelling beside it.

        One fact, not all of them: a longer recitation is what gets paraphrased,
        and the remaining facts are still in `known_facts` for the model to use
        in its own words afterwards.
        """
        facts = result.get("known_facts") or []
        first = facts[0] if facts and isinstance(facts[0], str) and facts[0].strip() else ""
        result["response_text"] = f"{name}。{first}" if first else name
        result["require_repeat_verbatim"] = True
```

and call it in `__call__`, immediately after `await self._attach_known_facts(deps, result, name)`:

```python
            self._attach_verbatim_envelope(result, name)
```

In `prompts.py`, append these three sections to `_HARDENING_BLOCK`, before the closing `"""`:

```
### 回答長度範例（示範語氣，不是觸發條件）
- 「現在幾點？」→「三點二十。」後面不要再補「還需要我幫你什麼嗎？」
- 「今天天氣如何？」→「台北陰天，二十四度，傍晚可能會下雨。」
- 「幫我開燈」→（工具成功之後）「開好了。」
- 想繼續聊就直接接一句你自己的想法或觀察，不要用問句把球丟回去。
以上只是語氣示範，不是要你等到聽見這些句子才這樣講。

### 工具結果要照著唸
- 工具回傳的 `require_repeat_verbatim` 或 `speak_verbatim` 是 true 時：把
  `response_text` 或 `summary_text` 一字不差地唸出來，不要改寫、不要縮短、
  不要換字、不要加自己的開場白。唸完之後才可以自然接話。
- 其他工具結果：先講結果本身，再看情況補充。

### 只講真的做過的事
- 工具成功回傳之後，才可以說動作完成了。工具失敗就一句話說明，再給一個下一步。
- 沒有真的轉頭、沒有真的拍到照片，就不要說你看了哪一邊、也不要描述你「看到」什麼。
```

- [ ] **Step 4: Run**

Run: `python -m pytest tests/test_prompts_hardening.py tests/test_face_tools.py tests/test_persona.py tests/test_record_mode.py -v`
Expected: PASS. Then `python -m pytest` — full suite green.

- [ ] **Step 5: Lint, typecheck, commit**

```bash
ruff check . && mypy --strict src
git add reachy_companion/src reachy_companion/tests
git commit -m "feat(prompts): verbatim envelopes and brevity few-shots"
```

---

### Task 12: Docs, env reference, work-queue rows, closure

**Files:**
- Modify: `reachy_companion/.env.example`
- Modify: `README.md` (env table `:180-191`)
- Modify: `DECISIONS.md` (append D-029)
- Modify: `feature_list.json`
- Modify: `CHANGELOG.md`
- Modify: `progress.md`

**Interfaces:** none — documentation of everything above.

- [ ] **Step 1: `.env.example`** — document, commented out in the file's existing code-default style:
  - `# REALTIME_SOLO_NAME_GATE=true` — **amend the existing block** (`:160-166`) with one clarifying sentence only: it governs *interruption* and nothing else, and the question of which turns get answered is `REALTIME_ONE_ON_ONE_ANSWER_GATE` below. Its meaning, default and behavior are unchanged; the instance `.env` line stays exactly as it is.
  - `# REALTIME_ONE_ON_ONE_ANSWER_GATE=open` — **new block**: which turns 一對一聊天模式 answers. `open` (default) answers anything substantive, so one person never has to say the robot's name; `name_only` answers only a name or a control phrase, the same rule 紀錄模式 uses, as the fallback if `open` turns out to pick up too much of the room. A malformed value warns and falls back to `open`. State explicitly that this is a *separate* knob from `REALTIME_SOLO_NAME_GATE` and why: that one decides what may interrupt a reply in progress, this one decides what gets a reply at all.
  - `# SLEEP_GOODBYE_DRAIN_CAP_S=6.0` — already added in Task 9; verify it is present.
  - `# RECORD_SUMMARY_TIMEOUT_S=20.0` — new block: seconds the 紀錄模式 summarizer may take (1.0–60.0); longer than `MEMORY_LAST_CHAT_TIMEOUT_S` because the input is a whole meeting, not forty lines. An overrun is spoken, not silent: Reachy says 「剛剛的記錄整理失敗了，要不要再說一次？」.
  - `# REALTIME_DEFAULT_MODE=group` — **new block**, and the one that now decides the boot posture (operator amendment, 2026-08-31). Values `one_on_one` / `group` / `record`; default `group`. Say why: Reachy sits in a room with several people in it, so it starts in 多人聊天模式 and answers only when somebody says its name; 「切到一對一聊天模式」 switches by voice in one sentence. Note that `record` is **allowed but discouraged** as a boot value — a robot that boots into 紀錄模式 is silent until it is addressed, which looks exactly like a robot that failed to start, and the app logs a warning when it is set. A malformed value warns and falls back to `group`.
  - Mark the `REALTIME_PARTY_DEFAULT` block (`:82-84`) **deprecated-superseded**: it is kept only as a legacy alias (truthy → `group`), it can no longer select anything the default does not already give, and `REALTIME_DEFAULT_MODE` is the knob to use. Also note the switch is the `set_conversation_mode` tool now, not `party_mode`.
  - Remove the `HANOVA_SELF_DESTRUCT_YT_ID` and `HANOVA_MAD_LAUGH_YT_ID` blocks if Task 7 did not already (their tools are gone).

- [ ] **Step 2: `README.md`** — env table rows, a feature paragraph, and the tool inventory:
  - Amend the `REALTIME_SOLO_NAME_GATE` row (`:186`) to say it governs interruption only, and point at the new answer-gate row.
  - Add rows: `REALTIME_DEFAULT_MODE` (the mode Reachy boots into — `group` default, `one_on_one`, or `record` (discouraged: boots silent); `REALTIME_PARTY_DEFAULT` is a deprecated alias), `REALTIME_ONE_ON_ONE_ANSWER_GATE` (which turns 一對一聊天模式 answers — `open` default: anything substantive, no name needed; `name_only`: only a name or a control phrase. Separate from `REALTIME_SOLO_NAME_GATE`, which decides interruption), `SLEEP_GOODBYE_DRAIN_CAP_S` (goodbye drain cap before the sleep pose, default `6.0`), `RECORD_SUMMARY_TIMEOUT_S` (紀錄模式 summarizer budget, default `20.0`).
  - Add a "Conversation modes" paragraph naming the three modes, their Chinese names, the switch phrases, **that Reachy boots into 多人聊天模式 and therefore answers only when addressed by name until you switch** (「切到一對一聊天模式」), and that 紀錄模式 keeps an in-memory-only record cleared on mode exit and at sleep.
  - Add a "Tools" paragraph: 22 tools are always loaded (music among them, so «音樂關掉» always reaches a tool); the calendar/to-do/drive/email/Notion family and the TV/NAS family load on demand when `open_toolbox` is called, and unload on a mode switch, at sleep, and on reconnect. Name the six consolidated families (`calendar`, `tasks`, `drive`, `nas`, `music`, `tv`) and the three retired tools (`sweep_look`, `self_destruct`, `mad_laugh`).
  - Wherever the README names `party_mode`, `sweep_look`, `self_destruct`, `mad_laugh`, or any of the 18 consolidated sub-tools, rename or remove to match.

- [ ] **Step 3: `DECISIONS.md` — append `## D-029 — Conversation modes, client-driven answers, and three on-robot fixes (2026-08-31)`.** Record: the enum replacing the boolean and *why `_party_mode` survives as a read-only property* (decision 1); interruption gate vs answer gate as separate concerns (decision 2); `create_response=false` everywhere and the pile-up it kills; **why the answer gate got its own `REALTIME_ONE_ON_ONE_ANSWER_GATE` instead of overloading `REALTIME_SOLO_NAME_GATE` — the instance `.env` sets that variable explicitly and the deploy ritual restores `.env` from backup on every install, so an overloaded knob would have re-flipped one-on-one to name-only answering on every deploy, silently and forever** (Open question 1); mode lifecycle across reconnects, **and the operator amendment that made `GROUP` the boot default** — a robot in a shared room must not wake up answering every overheard sentence; `REALTIME_DEFAULT_MODE` replaces `REALTIME_PARTY_DEFAULT`, which survives only as a deprecated alias (Open question 2); `record_log` at 2000 vs `session_transcript` at 40 and why both exist; RECORD's tool allowlist and the `[OFFICIAL]` <20-tools rule behind it; `look_around` as a composite instead of a prompted chain, and `direction_requested` as the anti-fabrication field (named for what the motion API can attest — P2-2); the sleep ordering — silence, wait, drain, pose — *why silencing must come first* (round 2, 2a-6), *why it never flushes*, and the loop-aware `wait_for_reply_finished` the inactivity path forced (round 2, 2a-5); commentary-phase suppression; the two verbatim envelope shapes (Open question 3). **The tool diet, in its own subsection:** 41 → 22 at the start of a turn, and the three mechanisms that got there — façade consolidation (decision 7, and why the 18 modules stay on disk with their prerequisite rows and tests), three deletions, and `open_toolbox` (decision 8: boxes ACCUMULATE within a mode — 22 / 27 / 24 / 29 — and close together at a mode switch, sleep or session start; awaited, not scheduled; no idle timers, and why closing a box the model has already been told about is worse than carrying one extra); the `task_status`/`task_cancel` ruling (Open question 4) and the honest count of 22 (Open question 5, plus Codex round 1 P2-7 moving `music` into the core so the stop lane is never boxed); `EXTRA_TOOLS` never hidden **in any mode, RECORD included** (P2-8) and why; the façade adding no argument validation of its own so delegate check order and error payloads are preserved (P2-5); and the ordered, acknowledged, single-flight session-update mechanism (design decision 9) that P1-1/P1-3/P1-4/P2-9 collapsed into — including the unmatched-ack debt counter, which is the part that is genuinely non-obvious: `session.updated` carries no client event_id, so every acknowledgement the server already owes (connect config, its retry, pre-receive-loop pushes, timed-out waits) must be paid off before a live waiter may be resolved, or a mode flip gets told it was applied on the strength of the connect config's ack (round 3, findings 1/5/6). Rejected alternatives to record: merging the 18 sub-tool bodies into 6 (rewrites the confirmation gates and the Google/NAS/HA error handling for zero model-facing gain); `allowed_tools` instead of a `tools` swap (equivalent surface, but the app already owns a proven `session.update` seam and `allowed_tools` would be a second mechanism); idle-timer toolbox expiry, and swap-instead-of-accumulate (both close a box the model has already been told about, mid-turn — Codex round 2, 2b-3); forcing the look chain with `tool_choice: {"type":"function","name":"camera"}` (research §B3 — `[COMMUNITY]` bug reports on `tool_choice` in Realtime; the composite needs no such gamble); numeric length caps (operator ruling); raising `reasoning.effort` to medium (unmeasured latency cost; revisit only if the composite does not fix selection).

- [ ] **Step 4: `feature_list.json`** — add rows with `"state": "implemented-unverified"`, `"evidence": null`, and a `next_action` naming the operator probe. Use the file's newer row shape (`id`, `behavior`, `verification`, `state`, `evidence`, `next_action`):
  - `MODE-ONE-ON-ONE` — behavior: after switching into it by voice, a single person is answered without saying the robot's name; a backchannel 「嗯嗯」 gets no reply. Verification: on-robot, **first say 「切到一對一聊天模式」** and confirm the journal shows `conversation mode: group -> one_on_one` (a fresh boot is in 多人聊天模式 — operator amendment 2026-08-31), then ask three ordinary questions without saying 瑞奇 — each answered once, exactly once; journal shows no `one-on-one gate: no answer for a non-substantive turn` for them, and exactly that line for an 「嗯」.
  - `MODE-BOOT-DEFAULT` — behavior: a fresh boot comes up in 多人聊天模式 and answers only when addressed by name (operator amendment, 2026-08-31). Verification: power-cycle or restart the app with no `REALTIME_DEFAULT_MODE` in the instance `.env`; the startup journal must show the greeting and then, on the first ambient sentence spoken near the robot without its name, `party gate: denied ambient turn` — and no reply. Say 「瑞奇你好」 and it answers. Also confirm the startup line `Tools to be used in conversation:` still lists 22 names (the boot mode changes the posture, not the tool surface).
  - `MODE-GROUP-SWITCH` — behavior: 「切到多人聊天模式」 flips back by voice from any other mode, the model confirms in one sentence, party semantics unchanged. Verification: from 一對一聊天模式, journal `conversation mode: one_on_one -> group` followed by `session updated (conversation mode group)`; ambient chatter then logs `party gate: denied ambient turn`. The confirmation sentence must come AFTER that update line — the tool awaits it (P1-1).
  - `MODE-RECORD` — behavior: 「進入紀錄模式」 makes Reachy silent; it keeps every line; 「瑞奇幫我總結」 reads back a Chinese summary verbatim; leaving the mode wipes the log. Verification: journal `conversation mode: * -> record`, then `record gate: transcribed without answering` on unaddressed lines, `Record summary written from N logged lines.` on the summary, and `record log cleared (N lines)` on exit. Confirm the live surface in the `Tools in session (record): [...]` line logged on the flip — **six local names** (`set_conversation_mode`, `summarize_conversation`, `go_to_sleep`, `wait_for_user`, `task_status`, `task_cancel`) plus any MCP extras the operator installed, which are never hidden in any mode. The startup-only `Tools to be used in conversation:` line does not reflect a mid-visit flip, which is why the per-update line exists (Codex round 1, P2-12).
  - `VOICE-LOOK-AROUND` — behavior: 「轉到右邊去看看有誰」 physically turns the head, then describes. Verification: journal `Tool call: look_around direction=right`; the head visibly moves before the description; a description that follows a failed move must not claim the turn.
  - `VOICE-SLEEP-QUIESCE` — behavior: 「睡覺吧」 lets the goodbye finish, then the body lies down; no speech after the pose, no `barge-in rolled back; resuming reply` afterwards. Verification: journal order `Tool call: go_to_sleep` → `sleep quiesce: microphone muted` → `sleep quiesce: barge machine disarmed` → (the bounded wait for the reply to finish) → `Going to sleep before stopping conversation app.` → `sleep quiesce: speaker quiet after N.Ns`, and the pose only after that. The two quiesce lines appearing BEFORE the wait is the point (Codex round 2, 2a-6): a live microphone through a ten-second wait is how a repeated 「睡覺吧」 opened a turn nobody answered. A `drain cap reached … with audio still playing` line means the cap is too tight for this goodbye, not that the fix failed — record it and raise `SLEEP_GOODBYE_DRAIN_CAP_S`. If `go_to_sleep: the goodbye response did not finish in time` appears, the response wait (P2-10) hit its cap. Repeat the command twice and confirm the second returns `already_requested`.
  - `VOICE-NO-DOUBLE-ANSWER` — behavior: talking over Reachy no longer queues a second full answer behind the resumed reply. Verification: interrupt mid-reply with an unaddressed sentence three times; each time the reply resumes and NO extra answer follows; journal shows the rollback line and no matching `response.created` for that turn.
  - `VOICE-COMMENTARY-SUPPRESS` — behavior: 2.x preambles never play. Verification: journal `suppressing commentary-phase item …` / `dropping commentary-phase audio for item …` with nothing audible; if the lines never appear over a whole visit, record that as "preambles not observed on this model snapshot" rather than a pass.
  - `TOOLS-CONSOLIDATED` — behavior: the calendar/to-do/drive/NAS/music/TV families are single action-enum tools, and every old behavior — including the spoken confirmation before a delete, a trash or an upload — is unchanged. Verification: on-robot, run one action per family (「幫我加個行程」、「加到待辦」、「雲端有什麼檔案」、「放首歌」、「電視上放那個」、「找一下那年的影片」) plus one destructive one (「把星期五那個會取消」) and confirm the spoken confirmation still gates it; journal shows `Tool call: calendar action=add` style lines and the delegate's own log line right after.
  - `TOOLBOX-DYNAMIC` — behavior: 22 tools are loaded at rest; a productivity or media request opens its box and completes in the same turn. **Verification must include the cold-start case:** with no box open and nothing else said first, say 「幫我加個行程，明天下午三點看牙醫」 — Reachy must call `open_toolbox` and then `calendar` in the same turn, with no second prompt to the user and no silent skip (the `[COMMUNITY]`-reported 2.1-mini failure this row exists to catch). Journal: `Tool call: open_toolbox category=productivity` → `Tools in session (one_on_one): [...]` listing 27 names → `session updated (conversation mode one_on_one)` → `toolbox opened: productivity (...)` → `Tool call: calendar action=add`. Repeat cold for media (「電視上放那個」). **Negative control for the always-core stop lane:** 「放首歌」 then 「音樂關掉」 must both work with NO `open_toolbox` call in between (Codex round 1, P2-7). Then switch modes and confirm `toolboxes closed (mode -> group): productivity`. Also confirm the startup line `Tools to be used in conversation:` lists 22 names.
  - Update `VOICE-NAME-GATE`'s behavior text: `REALTIME_SOLO_NAME_GATE` is now interruption-only by construction — which turns get *answered* is `REALTIME_ONE_ON_ONE_ANSWER_GATE` (default `open`), covered by `MODE-ONE-ON-ONE`. Add to `MODE-ONE-ON-ONE`'s verification a negative control: set `REALTIME_ONE_ON_ONE_ANSWER_GATE=name_only` in the instance `.env`, restart, **switch into 一對一聊天模式 by voice** (the boot mode is 多人聊天模式), and confirm an unaddressed substantive question is transcribed with `one-on-one gate: no answer for a non-substantive turn` and gets no reply; then remove the line again.
  - Remove or retire any row whose only verification is `self_destruct`, `mad_laugh` or `sweep_look`.

- [ ] **Step 5: `CHANGELOG.md`** — add a `## [1.18.0] — 2026-08-31 · the conversation-modes wave` stub above `## [1.17.0]`, in the file's voice (what the user experiences, not what changed in code): three modes switchable by voice with their Chinese names, **with Reachy now waking up in 多人聊天模式 — it listens quietly in a room full of people and answers when you say its name, and 「切到一對一聊天模式」 gives you the old always-answering behaviour back**; Reachy stops answering things you did not say to it; it turns its head when you tell it to look somewhere; it finishes saying goodbye before it lies down; **and it picks the right tool more often, because it now sees 22 tools instead of 41 — the calendar/to-do/drive/email/Notion family and the TV/NAS-video family load the moment a request needs them, six overlapping tool families became one tool each, and three tools nobody used were retired.** Mark the install line as `Deployed as the eighteenth install (commit …, wheel sha …)` with placeholders to be filled at deploy time, matching the 1.17.0 entry's shape. Do **not** bump `reachy_companion/pyproject.toml`'s `version` here — versions map to installs and the bump belongs to the deploy.

- [ ] **Step 6: `progress.md`** — current state (conversation modes, the tool diet, and the three RCA fixes implemented; suite green at the new baseline — record the exact number, which is below 1571 because `tests/test_hanova_gags.py` is gone and above it again from the new files), next action (**eighteenth install** via the `reachy-deploy` ritual carries this wave), and the deploy-time chores: (a) **no `.env` surgery is owed** — the instance's `REALTIME_SOLO_NAME_GATE=1` keeps meaning exactly what it meant, and the new answer gate has its own variable precisely so that the deploy ritual's restore-from-backup cannot change conversation behavior (Open question 1); (b) drop `HANOVA_SELF_DESTRUCT_YT_ID` / `HANOVA_MAD_LAUGH_YT_ID` from the instance `.env` when convenient — they configure nothing now, and leaving them costs nothing either; (c) `persona.md` needs no re-sync this wave — every prompt change is in the packaged hardening block.

- [ ] **Step 7: Full gates then commit**

```bash
python -m pytest        # from reachy_companion/; green, and no fewer non-gag tests than the 1571/30 baseline
ruff check .
mypy --strict src
git add -A
git commit -m "docs: D-029 conversation modes and the tool diet; env, README, feature rows, CHANGELOG"
```

---

## Verification after implementation

Runnable evidence (SDK-simulated): the full pytest suite plus every test written above — the mode enum and seam, the per-mode answer gate, the live mode update, the record log and its summarizer, the six family façades delegating to untouched originals, the ordered session-update mechanism (coalescing, acknowledgement, error correlation, timeout, the unmatched-ack debt, and the no-wait path before the receive loop), the static-core/toolbox partition (every registered tool reachable, none in two boxes, `music` never boxed, MCP extras never hidden, exactly 22 at rest), `open_toolbox` awaiting the acknowledgement and rolling back both a refused update and one a concurrent mode switch invalidated, `set_conversation_mode` reporting the mode it actually ended in, the RECORD allowlist ignoring open boxes, `look_around`'s ordering and its refusal to claim a move it never queued, the generalized image-attachment path, the three deletions being gone, the sleep path's silence → response-wait → drain → pose ordering, its cap/no-flush guarantees, its cross-loop safety and its refusal to wait on a dead session, and commentary suppression.

Live/human evidence rides the **eighteenth install** (`feature_list.json` rows in Task 12, `reachy-deploy` ritual). Residual risks to record if they cannot be checked on-device:
- `phase` is an undeclared field on our installed SDK stub. If the server never sends it, Task 10 is inert — provable only from the journal.
- `[COMMUNITY]` reports (research §D2) describe verbatim-name fidelity as a model-level regression on non-English 2.1-mini. The envelope improves the odds; it cannot guarantee them.
- Client-driven answering in ONE_ON_ONE adds one queue hop of latency per turn versus server auto-answer. Party mode's week of use says it is acceptable; measure `Turn latency: response.created … ms` on-robot and record the delta.
- Brevity: if replies are still long after the few-shots, numeric caps need a fresh operator decision (Global Constraints) — record the observation, do not implement a cap.
- Dynamic toolboxes add one model round trip before the first productivity/media action of a turn. The operator accepted that cost for these two families; if the cold-start probe shows the model asking the user again instead of continuing, the fallback is to promote the box's family into the static core (surface 22 → 27 for productivity, 22 → 24 for media) rather than to prompt harder — record which, and why.
- The acknowledged session update adds up to `_SESSION_UPDATE_ACK_TIMEOUT_S` (5 s) of worst-case latency to a mode switch or a toolbox open, and the plan assumes the server sends `session.updated` for a `session.update` it applies. If the journal shows `was never acknowledged within 5.0s` on a healthy connection, that assumption is wrong for this endpoint — record it, and fall back to sending without the wait rather than leaving every flip failing.
- `[COMMUNITY]` report §D1 describes 2.1-mini silently skipping a function call after completing the conversational step. The `TOOLBOX-DYNAMIC` cold-start probe is the guard; a silent skip there means the router pattern is not safe on this tier and the boxes must go static.

## Review log

### Round 1 (Codex, 2026-08-31 — two passes, 20 findings: 2 Critical / 12 Important / 6 Minor. **20 accepted, 0 rejected.**)

Pass 1 covered Tasks 1–5, pass 2 Tasks 6–12. Four findings (P1-1, P1-3, P1-4, P2-9) were ruled one defect family and fixed once, as design decision 9.

**Pass 1 — Tasks 1–5**

1. *Important* — `set_conversation_mode` scheduled its session update with `ensure_future`, so the model's confirmation could run against the previous mode's instructions and tools. **Accepted:** the method is now `async` and awaits an ordered, acknowledged update before returning; the deps seam and the tool are async with it (Task 1, design decision 9).
2. *Important* — the answer gate read the live `_conversation_mode` at transcription completion, so a flip could retroactively reclassify an utterance already in flight. **Accepted, scoped to one field per the orchestrator's ruling:** `self._turn_mode` is stamped at `speech_started` and passed explicitly into `_answer_gate_accepts(transcript, mode)`, the deny log and the follow-up window (Task 2). No per-item map.
3. *Important* — `session.update` rejections arrive asynchronously as `error` events, and the existing handler sets `_response_started_or_rejected_event` for those, which can falsely wake the response sender. **Accepted:** the update stamps a client `event_id`; an `error` naming it resolves the update's waiter and `continue`s, never touching the response-create path (Task 3).
4. *Important* — rapid flips could queue several updates with no lock or generation, letting an older snapshot land last. **Accepted:** a monotonic `_mode_update_seq` ticket plus an `asyncio.Lock`; the payload is built inside the lock from live state, and a superseded ticket drops itself (Task 3).
5. *Important* — clearing `record_log` in every `shutdown()` would discard a meeting on a settings/backend restart, which D-027 already treats as mid-visit. **Accepted:** the clear is gated on `deps.sleep_requested`, mirroring the sleep summary; a restart keeps the log (Task 4, with a test for each branch).
6. *Important* — Task 5 appended imports below `record_mode.py`'s definitions, an import-order failure under this repo's ruff config. **Accepted:** Task 5 now merges them into the existing top import block, and the block is written out in full.
7. *Minor* — the Task 2 failing-test command carried two `-k` options; pytest keeps only the last, deselecting the very tests being checked. **Accepted:** one combined `-k` expression across both files; the same fix applied to Task 3's command.
8. *Minor* — the Task 3 test only asserted `session["tools"]` was a list, so an implementation ignoring `_mode_tool_exclusions()` would pass and break Task 8's contract before it started. **Accepted:** a sentinel test monkeypatches `get_tool_specs` and `_mode_tool_exclusions` and asserts the exact exclusion list is passed exactly once.

**Pass 2 — Tasks 6–12**

9. *Critical* (P2-1) — the handler attaches a tool result's image only for `tool_name == "camera"`, so `look_around`'s picture would never reach the model and its base64 would be dumped into the tool JSON. **Accepted, generalize-the-path option per the orchestrator's ruling:** `_sanitize_tool_result_for_model` and the attachment condition now key on `"b64_im" in tool_result`, for any tool (Task 7, with a test).
10. *Important* (P2-2) — `direction_moved` is not ground truth: `MoveHead` returns on queueing and `MovementManager` publishes no accepted/completed signal. **Accepted:** `look_around` calls `clear_move_queue()` so its move is not stuck behind an older one, and — the motion API offering no execution confirmation, and `set_hold_still` being able to drop a queued move silently — the field is renamed **`direction_requested`**, with the description telling the model to describe the returned picture rather than assert completed motion. Choice recorded in the task text.
11. *Minor* (P2-3) — the interface claimed failures never carry a direction field, but the capture-failure path returned both. **Accepted:** three outcomes are now spelled out separately — move failure (no direction field), capture failure (direction + error, no image), success.
12. *Important* (P2-4) — `look_around` advertised 「看一下你後面」 with no `behind` in the schema. **Accepted:** the trigger is removed from `look_around`'s and `camera`'s descriptions, body rotation stays out of scope, and a test asserts neither 「後面」 nor "behind" appears.
13. *Important* (P2-5) — façade-level required-arg validation would reorder and reword each delegate's own checks, which run after `settings.tool_status`. **Accepted:** `dispatch_family` validates the action name only; `REQUIRED` tables are gone, and two tests pin the delegate's own error string and the prereq-before-args order.
14. *Important* (P2-6) — the locked profile's instruction body still named `play_music`, `calendar_add`, `drive_trash`, `self_destruct`… **Accepted:** the body lines are rewritten to the family names in Task 6, the `self_destruct` ritual line is deleted in Task 7, and a `_RETIRED_TOOL_NAMES` tripwire asserts no retired name survives anywhere in the profile text.
15. *Important* (P2-7) — boxing `music` would hide `stop_music`, documented as the prerequisite-free safety lane. **Accepted per the orchestrator's ruling:** `music` moves into the static core, the media box becomes `tv` + `nas`, the core count becomes **22**, and every count, list and doc line is updated. A negative control (「放首歌」 then 「音樂關掉」 with no `open_toolbox` between them) joins `TOOLBOX-DYNAMIC`.
16. *Important* (P2-8) — the "EXTRA_TOOLS are never hidden" invariant was false in RECORD. **Accepted, keep the invariant:** `EXTRA_TOOLS` is added to the allowed set before the mode branch, so it holds in every mode; the RECORD tests assert `kept <= RECORD_TOOL_ALLOWLIST | set(EXTRA_TOOLS)` and a new test pins that an MCP tool survives RECORD.
17. *Important* (P2-9) — `open_toolbox` set local state before an unacknowledged, failure-swallowing update. **Accepted, same fix as P1-1/3/4:** `_push_mode_update` returns a bool from the acknowledged mechanism, and `open_toolbox` discards the box and reports `status: "update_failed"` when the server refused.
18. *Critical* (P2-10) — `go_to_sleep` runs from the tool worker before `response.done`, so `is_audible()` can be false while the goodbye is still being generated and the robot sleeps mid-sentence. **Accepted per the orchestrator's ruling:** a new `wait_for_reply_finished()` handler seam (bounded, on `_response_done_event`) is awaited by the `GoToSleep` tool *before* the worker thread quiesces. Order: response finishes → mic muted → barge disarmed → audible drain → pose.
19. *Minor* (P2-11) — the quiesce logged "speaker quiet" even when the cap expired with audio still playing. **Accepted:** it re-checks `is_audible()` after the loop and logs `drain cap reached … with audio still playing` instead, with a test for each branch.
20. *Important* (P2-12) — RECORD's allowlist is six tools but Task 12 said "four-tool allowlist", and the only tool-list log is startup-only. **Accepted:** every doc, test and feature row now says six local names plus any `EXTRA_TOOLS`, and `_push_mode_update` logs `Tools in session (<mode>): [...]` on every update so a mid-visit flip is verifiable.

### Round 2 (Codex, 2026-08-31 — two passes, 14 findings: 1 Critical / 10 Important / 3 Minor. **14 accepted, 0 rejected.**)

Pass 2a re-reviewed the concurrency of the round-1 fixes; pass 2b re-reviewed the tool surface.

**Pass 2a — concurrency**

1. *Critical* — `_push_mode_update` took the lock for the snapshot, released it, and `_apply_session_update` relocked to send, so a newer flip could overtake an older payload between the two and the monotonic ticket bought nothing. **Accepted:** `_apply_session_update` now takes a **builder**, not a payload, and holds the lock across ticket check, payload build, waiter install, send and acknowledgement wait as one uninterrupted region. Test `test_the_send_happens_inside_the_lock_that_built_the_payload` asserts the builder runs with the lock held.
2. *Important* — `session.updated` was resolved with no correlation while `change_voice`, `apply_personality` and the startup turn-detection push still sent their own `session.update`s, so an acknowledgement could resolve somebody else's waiter. **Accepted, single-flight ruling:** every live-session caller is routed through the one mechanism, the invariant is documented where the mechanism is defined, the single exemption (the pre-receive-loop init update) is named and explained, and two tests pin it — a stray `session.updated` with no waiter is a silent no-op, and a source-level check that no live-update method calls `session.update(` directly.
3. *Important* — the "no utterance in flight" restamp guard in `set_conversation_mode` always passed, because the method had already cleared `_party_speech_open`/`_barge_speech_open` above it. **Accepted:** `turn_in_flight` is captured before either flag is mutated.
4. *Important* — a single `_turn_mode` field is overwritten by the next `speech_started` before a slow `transcription.completed` for the previous turn arrives, so an overlapping turn is still judged under the wrong mode. **Accepted, upgraded to item-keyed stamps:** `_stamp_turn_mode(item_id)` / `_take_turn_mode(item_id)` over a bounded `_turn_modes` dict keyed by the `speech_started` event's `item_id` (verified present on the installed SDK's `InputAudioBufferSpeechStartedEvent`), popped at the top of the completed branch and in the failed branch, cleared per session, with `_turn_mode` as the no-id fallback. The reviewer's exact stream (turn A in GROUP → flip → turn B → A completes late) is `test_an_overlapping_turn_keeps_its_own_mode_stamp`.
5. *Important* — the inactivity path runs `GoToSleep` via `asyncio.run` on a daemon thread, so `wait_for_reply_finished` would await a handler-loop primitive from a foreign loop. **Accepted:** the seam is loop-aware — it captures `_handler_loop` at session start, awaits directly when already on it, marshals via `asyncio.run_coroutine_threadsafe` with the same bounded timeout otherwise, and reports success when the loop is gone. Two tests: one across a real second loop, one with no loop at all.
6. *Important* — waiting for `response.done` **before** muting left the microphone live for up to ten seconds, long enough for a repeated sleep command or the goodbye's own echo to open a turn nobody would answer. **Accepted, reordered exactly as the reviewer specified:** mark sleep pending → mute mic → disarm barge → bounded wait for `response.done` → drain → pose. `quiesce_for_sleep` is split into `begin_sleep_quiesce` (silence) and `wait_for_speaker_quiet` (drain), a `deps.begin_sleep` seam lets the tool silence before it waits, and every ordering claim, docstring, test and verification row was rewritten to this order.
7. *Minor* — the verification row's journal order contradicted where the implementation logs `Going to sleep before stopping conversation app.`. **Accepted:** the log stays at its existing position, above everything the closure does, and the drain is inserted below it; the feature row now documents the order that actually results.

**Pass 2b — tool surface**

8. *Important* — the Goal line and Open question 5 still said 21 after `music` moved into the core. **Accepted:** both now say 22 and name `music` as the added always-on stop lane.
9. *Important* — Task 7 inserted `look_around` after `move_head` while Task 8's final list has it after `camera`; `EXPECTED_TOOLS` is order-sensitive, so following both would fail. **Accepted:** Task 8's order is authoritative; Task 7 now says **after `camera`**, in both the profile edit and the `EXPECTED_TOOLS` edit.
10. *Important* — the prose said toolboxes "swap, never accumulate" while the code and tests allow both open, leaving a 29-tool surface undocumented. **Accepted, document-the-accumulation ruling:** design decision 8 is rewritten (boxes accumulate within a mode and close together at its edges, with the reason: closing a box the model has already been told about is how you get a call to a tool that is no longer there), the 22/27/24/**29** sizes are stated everywhere and asserted by `test_the_documented_surface_sizes_hold`, a new `test_a_second_box_adds_to_the_first` pins the behavior, and swap-instead-of-accumulate joins the rejected alternatives.
11. *Important* — `OpenToolbox`'s parameter description still routed 音樂 to the media box. **Accepted:** it now reads `media 電視／NAS 影片；productivity 行程／待辦／雲端／郵件／Notion。`, matching the tool description and the hardening block, both of which already list music as always-loaded.
12. *Minor* — the CHANGELOG said "TV/video families" when the box is `tv` + `nas`. **Accepted:** "the TV/NAS-video family".
13. *Minor* — the self_destruct body-line reference was wrong twice over: the shipped line uses simplified characters (`倒数仪式`, not `倒數儀式`) and sits at `:76`, not `:31`. **Accepted:** the instruction now identifies it by `grep` and quotes the actual shipped text, with no line number to go stale.
14. *Important* — the retired-name tripwire read only the locked profile, but `profiles/default/profile.md` also ships `sweep_look`, and the shared hardening block is a prompt too. **Accepted:** the test iterates every bundled `profiles/*/profile.md`, a second test asserts `hardening_block()` names no retired tool, and Task 7 notes that `profiles/default` must be cleaned for the first to pass.

### Round 3 (Codex, 2026-08-31 — final round, 11 findings: 2 Critical / 5 Important / 4 Minor. **11 accepted, 0 rejected.**)

1. *Important* — the no-greeting startup path releases the boot gate (and so pushes turn detection) from `_send_startup_greeting_prompt`, which runs **before** the receive loop, so the acknowledgement wait burned the full 5 s and logged a false failure. **Accepted:** `_receive_loop_active` gates the wait — before the loop, the update is sent, reported applied, and its future acknowledgement booked as debt. Two tests, including a blank-greeting regression.
2. *Important* — `_handler_loop` and `_response_done_event` outlived their session: an event left clear by a session that died mid-response made every later `wait_for_reply_finished` pay its whole timeout. **Accepted:** the session `finally` sets `_response_done_event` and clears `_handler_loop`, and `wait_for_reply_finished` returns `True` immediately when `self.connection is None`. Test: live loop, `connection=None`, event unset → returns in well under a second.
3. *Important* — `open_toolbox` returned `"loaded"` on a `True` from `_push_mode_update` without re-checking, so a concurrent mode switch (which calls `close_toolboxes`) left the model told about tools the session no longer had. **Accepted:** the box membership is re-checked after the await and rolled back to `status: "update_failed"` otherwise; `test_open_toolbox_rolls_back_when_a_mode_switch_races_it` pins it.
4. *Important* — `set_conversation_mode` reported its own `target` after awaiting, so a flip that lost a race had the model announce a mode the handler was not in. **Accepted:** the mode is re-read after the await and a losing call returns `status: "superseded"` with the **actual** current mode (and `requested` for the journal). New tri-state documented in the Task 1 interface.
5. *Critical* — the initial `session.update`'s acknowledgement arrives after the receive loop starts and could resolve a live waiter, telling a mode flip its payload was applied when the server had acknowledged the connect config. **Accepted:** `_note_session_updated` pays `_session_update_ack_debt` before it will touch a waiter; the connect update and its fallback retry each book one unit. The round-2 test that treated a stray ack as acceptable is **replaced** by `test_the_connect_ack_never_resolves_a_live_waiter`, which asserts the waiter survives the wrong ack and is resolved only by its own.
6. *Critical* — after an ack timeout the late acknowledgement would resolve the *next* update's waiter. **Accepted, debt counter per the ruling** (not a session restart, which would be disproportionate on every slow ack): a timeout books one unit of debt, paid by the next `session.updated`. `test_a_late_ack_pays_its_own_debt_not_the_next_update` runs the reviewer's A-times-out / B-sends / A's-ack-arrives stream.
7. *Important* — the `_push_mode_update` snippet was still annotated `-> None` while the interface and every caller expect `bool`. **Accepted:** signature corrected.
8. *Minor* — the RECORD prompt block listed four tools against a six-name allowlist. **Accepted:** it now names the four work tools by what each is for and notes that `task_status`/`task_cancel` remain available for tracking an unfinished job.
9. *Minor* — the locked-region test only checked the build. **Accepted:** `_acking_connection` records `_session_update_lock.locked()` at send time and the test asserts `True` there too.
10. *Minor* — design decision 4's record-log lifetime wording predated the P1-5 gating. **Accepted:** "cleared on RECORD exit and on the sleep that ends the visit; preserved across reconnects and settings/backend restarts", with the reason.
11. *Minor* — the toolbox docstring and the size assertions read as absolute counts. **Accepted:** the docstring says "6 local tools in 紀錄模式" and states that every count is "plus any `EXTRA_TOOLS`"; both size tests subtract the extras so they are correct with or without MCP tool spaces installed.

---

**Review closed.** Three rounds, **45 findings** total (3 Critical / 27 Important / 15 Minor) — **45 accepted, 0 rejected**. Round 3 produced no finding that contradicted an earlier ruling, and no open items remain. The plan is ready for execution.

### Operator amendment (post-review): default mode GROUP

2026-08-31, explicit operator instruction, applied after the review closed. **The boot default is 多人聊天模式 (`GROUP`), not `ONE_ON_ONE`** — the robot sits in a room with several people in it, and one that wakes up ready to answer every overheard sentence is the failure party mode was built to fix. Scope: `DEFAULT_MODE`, a new mode-valued `REALTIME_DEFAULT_MODE` reader (`_boot_conversation_mode()`, values via `parse_mode`, default `group`, `record` allowed but warned) replacing `_party_default_on()`, the Goal line, Open question 2, the `set_conversation_mode` tool description and schema, the boot-mode tests, the two env-cleaning fixtures, and Task 12's `.env.example` / README / D-029 / CHANGELOG / feature rows (new `MODE-BOOT-DEFAULT` row; `MODE-ONE-ON-ONE`'s probe now opens with a voice switch into 一對一聊天模式). `REALTIME_PARTY_DEFAULT` remains only as a documented deprecated-superseded alias.

**This does not reopen the review.** It changes a default, not a mechanism: no gate, session-update, toolbox, sleep or record-log behaviour is affected, and no round-1/2/3 finding or ruling is disturbed.
