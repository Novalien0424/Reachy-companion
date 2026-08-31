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
