"""Conversation modes: the enum, the handler seam, and the voice switch.

Replaces the `_party_mode` boolean that used to be the whole mode system
(2026-08-24 party mode). Party-specific *behavior* still lives in
`tests/test_party_mode.py`; this file owns the three-mode vocabulary.
"""

import asyncio
from types import SimpleNamespace
from typing import Any
from collections import deque
from unittest.mock import MagicMock

import pytest

# `tests/` has no __init__.py, so pytest's prepend import mode puts the
# directory itself on sys.path — import the sibling harness by bare name.
from test_solo_barge import _install_barge_state

from reachy_companion.record_mode import RECORD_LOG_MAX_ITEMS
from reachy_companion.openai_realtime import OpenAIRealtimeHandler
from reachy_companion.conversation_mode import (
    MODE_LABELS,
    MODE_VALUES,
    DEFAULT_MODE,
    ConversationMode,
    parse_mode,
)
from reachy_companion.huggingface_realtime import HuggingFaceRealtimeHandler
from reachy_companion.tools.set_conversation_mode import SetConversationMode


def _mode_handler(mode: ConversationMode = ConversationMode.ONE_ON_ONE) -> OpenAIRealtimeHandler:
    """Return a `__new__`-built handler carrying only mode + barge state."""
    h = OpenAIRealtimeHandler.__new__(OpenAIRealtimeHandler)
    h._conversation_mode = mode
    # The mode the utterance in flight began in (Task 2 reads it; stamped at
    # `speech_started`). A `__new__`-built handler starts them equal.
    h._turn_mode = mode
    h._turn_modes = {}
    # Task 8: `set_conversation_mode` closes every open toolbox on a flip.
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
    # `_push_turn_detection_update` rebuilds the whole session config, and the
    # base builder resolves the output voice through `_voice_override` — an
    # `__init__`-only field a `__new__`-built handler would otherwise lack.
    h._voice_override = None
    h._party_last_accept_at = None
    h._party_speech_open = False
    h._party_utterance_seq = 0
    h._party_barge_task = None
    h._active_response_id = None
    h._cancelled_response_ids = deque(maxlen=8)
    h._response_done_event = asyncio.Event()
    h._response_done_event.set()
    h.connection = None
    # No face in frame by default, exactly as `tests/test_party_mode.py` builds
    # its handler: the GROUP answer gate consults `_face_engaged()`, and a bare
    # MagicMock face reports `detected` truthy with a MagicMock timestamp, which
    # is neither "engaged" nor a shape the gate can compare.
    h.deps = SimpleNamespace(
        reachy_mini=SimpleNamespace(
            get_tracked_face=lambda wait: SimpleNamespace(detected=False, x=None, y=None, roll=None, ts=None)
        ),
        movement_manager=MagicMock(),
        # Task 4: leaving 紀錄模式 clears the room log, so a flip out of RECORD
        # reads this field. The real `ToolDependencies` always carries it.
        record_log=deque(maxlen=RECORD_LOG_MAX_ITEMS),
    )
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
    """`REALTIME_DEFAULT_MODE` names the boot mode, through the same parser."""
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


def test_the_dead_party_knob_is_announced_when_it_is_the_only_one_set(
    monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    """An operator whose `.env` still carries the old knob must hear that it is dead.

    `REALTIME_PARTY_DEFAULT=0` used to mean "boot solo" and now selects nothing at
    all. Silently booting into 多人聊天模式 against an explicit `0` is the kind of
    thing that gets diagnosed as a broken robot, so say it in the deploy log.
    """
    import logging

    from reachy_companion.huggingface_realtime import _boot_conversation_mode

    monkeypatch.delenv("REALTIME_DEFAULT_MODE", raising=False)
    monkeypatch.setenv("REALTIME_PARTY_DEFAULT", "0")
    with caplog.at_level(logging.WARNING, logger="reachy_companion.huggingface_realtime"):
        assert _boot_conversation_mode() is ConversationMode.GROUP
    assert "REALTIME_PARTY_DEFAULT" in caplog.text
    assert "REALTIME_DEFAULT_MODE" in caplog.text
    assert "group" in caplog.text, "the resolved mode has to be in the line"


def test_the_dead_party_knob_is_silent_once_the_new_one_is_set(
    monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    """Nothing to warn about: the operator has already migrated."""
    import logging

    from reachy_companion.huggingface_realtime import _boot_conversation_mode

    monkeypatch.setenv("REALTIME_PARTY_DEFAULT", "1")
    monkeypatch.setenv("REALTIME_DEFAULT_MODE", "one_on_one")
    with caplog.at_level(logging.WARNING, logger="reachy_companion.huggingface_realtime"):
        assert _boot_conversation_mode() is ConversationMode.ONE_ON_ONE
    assert "REALTIME_PARTY_DEFAULT" not in caplog.text


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
    """A flip lands, reports its new mode and label, and repeats as `unchanged`."""
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
    """An unrecognized mode name is refused, and the handler stays where it was."""
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


def test_a_new_session_clears_the_turn_stamps() -> None:
    """A stamp from a dead session must never judge a turn in the one replacing it.

    The SAS carry-over hazard `_party_reset_for_new_session` exists to prevent:
    `item_id`s are per-session, so a surviving entry can be hit by an unrelated
    item of the same name — and `_turn_mode` left over from the previous session
    would be the fallback for every turn until the first `speech_started`. The
    MODE itself deliberately survives (survey §1.2); only the turn state resets.
    """
    h = _mode_handler(ConversationMode.RECORD)
    h._turn_modes["item_1"] = ConversationMode.ONE_ON_ONE
    h._turn_mode = ConversationMode.ONE_ON_ONE

    h._party_reset_for_new_session()

    assert h._turn_modes == {}
    assert h._turn_mode is ConversationMode.RECORD
    assert h._conversation_mode is ConversationMode.RECORD, "a reconnect does not end 紀錄模式"


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
    """A build without the seam reports failure instead of raising."""
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
    """A non-string argument is refused at the tool boundary, not passed through."""

    async def _seam(mode: str) -> dict[str, object]:
        return {"ok": True}

    deps = SimpleNamespace(set_conversation_mode=_seam)
    result = await SetConversationMode()(deps, mode=3)
    assert result["ok"] is False


def test_tool_schema_enumerates_every_mode() -> None:
    """The schema offers all three modes and requires one."""
    schema = SetConversationMode().parameters_schema
    assert schema["properties"]["mode"]["enum"] == ["one_on_one", "group", "record"]
    assert schema["required"] == ["mode"]


def test_tool_description_carries_the_chinese_switch_phrases() -> None:
    """Literal-interpretation trap (research §C7): enumerate real phrasings."""
    description = SetConversationMode.description
    for phrase in ("一對一聊天模式", "多人聊天模式", "紀錄模式", "go_to_sleep", "Do NOT use when"):
        assert phrase in description


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
    """An event carrying no `item_id` still gets a verdict, from `_turn_mode`."""
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


def test_restamping_one_item_does_not_evict_another_turn() -> None:
    """A repeat `speech_started` for a live item replaces, it does not crowd out.

    Review item 4. Eviction used to fire on size alone, so once the map was
    full a re-stamp of an id already in it threw away the OLDEST turn's stamp
    to make room it did not need — and that turn's late transcript then fell
    back to `_turn_mode`, which is the very reclassification the per-item map
    exists to prevent.
    """
    from reachy_companion.huggingface_realtime import _TURN_MODE_MAX_ITEMS

    h = _mode_handler(ConversationMode.GROUP)
    for index in range(_TURN_MODE_MAX_ITEMS):
        h._stamp_turn_mode(f"item_{index}")
    assert len(h._turn_modes) == _TURN_MODE_MAX_ITEMS

    # Deliberately NOT the oldest key: evicting for a re-stamp of the oldest is
    # self-healing (it pops the very entry it is about to rewrite), so only a
    # mid-map id exposes the bug.
    h._stamp_turn_mode("item_5")

    assert h._turn_modes["item_0"] is ConversationMode.GROUP, "an unrelated turn's stamp was evicted"
    assert len(h._turn_modes) == _TURN_MODE_MAX_ITEMS


# --------------------------------------------------------------------------
# The live mode update (2026-08-31 plan, Task 3)
# --------------------------------------------------------------------------


def _acking_connection(handler: OpenAIRealtimeHandler) -> SimpleNamespace:
    """Return a connection whose `session.update` immediately acks, as the server does.

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
    """A `session.updated` nobody asked for must not raise or book anything."""
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
    from reachy_companion import openai_realtime as oai_mod
    from reachy_companion import huggingface_realtime as hf_mod

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
async def test_rapid_flips_coalesce_to_the_latest_mode(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A snapshot queued behind a newer flip is dropped, not sent (P1-4).

    The drop path only exists while an update is genuinely in flight, so the
    first send holds its acknowledgement until two more flips have taken
    tickets behind it (review Minor 4). Against an immediately-acking fake
    every flip finds the lock free, and this test passed whether or not the
    coalescing code existed at all.
    """
    import logging

    h = _mode_handler()
    h._boot_gate_active = True
    h.instance_path = None
    calls: list[dict[str, Any]] = []
    first_sent = asyncio.Event()
    release_first = asyncio.Event()

    async def _update(**kwargs: Any) -> None:
        calls.append(kwargs)
        if len(calls) == 1:
            first_sent.set()
            await release_first.wait()  # the lock is held for this whole wait
        h._note_session_updated()

    h.connection = SimpleNamespace(session=SimpleNamespace(update=_update))
    monkeypatch.setattr(h, "_mode_instructions", lambda: f"INSTRUCTIONS-{h._conversation_mode.value}")

    with caplog.at_level(logging.DEBUG, logger="reachy_companion.openai_realtime"):
        first = asyncio.create_task(h.set_conversation_mode("group"))
        await first_sent.wait()
        # Both queue behind the in-flight update; the second takes the newer
        # ticket, which is what makes the first of the two obsolete.
        second = asyncio.create_task(h.set_conversation_mode("record"))
        third = asyncio.create_task(h.set_conversation_mode("one_on_one"))
        while h._mode_update_seq < 3:
            await asyncio.sleep(0)
        release_first.set()
        await asyncio.gather(first, second, third)

    assert h._conversation_mode is ConversationMode.ONE_ON_ONE
    # Two payloads, not three: 紀錄模式 never reached the wire, because by the
    # time its builder ran the handler was already somewhere else.
    assert [call["session"]["instructions"] for call in calls] == [
        "INSTRUCTIONS-group",
        "INSTRUCTIONS-one_on_one",
    ]
    assert "superseded by 3" in caplog.text, "the superseded builder never returned None"


@pytest.mark.asyncio
async def test_a_dead_sessions_teardown_cannot_zero_the_new_sessions_debt() -> None:
    """`_restart_session` overlaps two sessions; the old finally must not reset.

    Review item 1. `_restart_session` clears `self.connection` and spawns the
    replacement, so the dead session's `finally` can run AFTER the new session
    has connected and booked the +1 its connect-time config owes. Zeroing the
    debt there would let the connect acknowledgement resolve the first live
    flip's waiter — the exact false positive the debt exists to prevent.
    """
    h = _mode_handler()
    old_conn = SimpleNamespace(name="old")
    new_conn = SimpleNamespace(name="new")
    h.connection = new_conn  # the replacement is already live
    h._receive_loop_active = True  # and its receive loop is running
    h._session_update_ack_debt = 1  # its connect config, still unacknowledged

    h._end_session_updates(old_conn)  # the dead session's finally, late

    assert h._session_update_ack_debt == 1
    assert h._receive_loop_active is True

    # The session that still owns the connection does close its own books.
    h._end_session_updates(new_conn)
    assert h._session_update_ack_debt == 0
    assert h._receive_loop_active is False


@pytest.mark.asyncio
async def test_apply_personality_does_not_block_on_the_acknowledgement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The restart right after it is the real apply; waiting only delays it.

    Review Minor 5: `apply_personality` used to pay up to the full ack timeout
    before an unconditional `_restart_session()`, so a server slow to answer
    made a personality switch feel like a hang for nothing. The send still goes
    through the one mechanism — same lock, same single flight — it just books
    the acknowledgement as debt instead of waiting for it.
    """
    from unittest.mock import AsyncMock

    from reachy_companion import openai_realtime as oai_mod
    from reachy_companion import huggingface_realtime as hf_mod

    monkeypatch.setattr(oai_mod, "_SESSION_UPDATE_ACK_TIMEOUT_S", 30.0)
    monkeypatch.setattr(hf_mod, "set_custom_profile", lambda profile: None)
    monkeypatch.setattr(hf_mod, "get_session_instructions", lambda instance_path=None: "BASE")
    monkeypatch.setattr(hf_mod.core_tools, "initialize_tools", lambda force=False: None)
    h = _mode_handler()
    h.instance_path = None
    h._receive_loop_active = True
    monkeypatch.setattr(h, "get_current_voice", lambda: "cedar")
    monkeypatch.setattr(h, "_restart_session", AsyncMock(return_value=None))
    sent: list[dict[str, Any]] = []

    async def _update(**kwargs: Any) -> None:
        sent.append(kwargs)  # sent, and deliberately never acknowledged

    h.connection = SimpleNamespace(session=SimpleNamespace(update=_update))
    started = asyncio.get_running_loop().time()
    result = await h.apply_personality("mars_rover")

    assert asyncio.get_running_loop().time() - started < 1.0
    assert "restarted realtime session" in result.lower()
    assert len(sent) == 1, "the update must still be sent, just not waited on"
    # Booked, so the acknowledgement it eventually produces cannot resolve a
    # later mode flip's waiter.
    assert h._session_update_ack_debt == 1
    assert h._session_update_waiter is None


def test_mode_instructions_append_the_mode_block(monkeypatch: pytest.MonkeyPatch) -> None:
    """One resolver: a flip and a reconnect tell the model the same thing."""
    from reachy_companion import huggingface_realtime as hf_mod

    h = _mode_handler(ConversationMode.RECORD)
    h.instance_path = None
    monkeypatch.setattr(hf_mod, "get_session_instructions", lambda instance_path: "BASE")
    text = h._mode_instructions()
    assert text.startswith("BASE\n\n")
    assert "紀錄模式" in text
