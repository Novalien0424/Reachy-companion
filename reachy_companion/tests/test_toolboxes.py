"""The session's tool surface: one static list per conversation mode, no boxes (1.30.0).

The mini context diet (docs/plans/2026-10-30-mini-context-diet-plan.md, Step 1)
retired the on-demand toolboxes: every box was a forced extra call, and the mini
model fails between calls, not at picking them. 19 tools are sent in 一對一 and
多人, 4 in 紀錄模式, plus any MCP extras, which are never hidden.
"""

import json
import asyncio
import importlib
from types import ModuleType, SimpleNamespace
from typing import Any
from collections import deque
from unittest.mock import MagicMock

import pytest
from test_solo_barge import _install_barge_state

from reachy_companion import toolboxes as tb_mod
from reachy_companion.toolboxes import CORE_TOOL_NAMES, session_tool_exclusions
from reachy_companion.record_mode import RECORD_TOOL_ALLOWLIST
from reachy_companion.openai_realtime import OpenAIRealtimeHandler
from reachy_companion.tools.core_tools import EXTRA_TOOLS
from reachy_companion.conversation_mode import ConversationMode


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Rebuild the real tool registry and point `toolboxes` at the live copy.

    `tests/test_external_loading.py` pops `reachy_companion.tools.core_tools`
    out of `sys.modules` and re-imports it, which leaves every module-level
    `from … import get_tools` binding in the process — this file's and
    `toolboxes.py`'s — holding a stale module whose `ALL_TOOLS` is empty and
    can never be rebuilt (its `Tool` base class is no longer the one the tool
    modules subclass). Same hazard, same cure as `test_home_control.py`'s
    `rebuilt_registry`, plus the rebind so the code under test and the
    assertions read the same registry. Teardown only clears the signature, so
    the next reader rebuilds lazily.
    """
    core_tools = importlib.import_module("reachy_companion.tools.core_tools")
    monkeypatch.setattr(tb_mod, "get_tools", core_tools.get_tools)
    monkeypatch.setattr(tb_mod, "EXTRA_TOOLS", core_tools.EXTRA_TOOLS)
    # The realtime handlers carry their own module-level `get_tool_specs`
    # bindings, and the active-surface log counts through THOSE — left stale,
    # the logged count and this fixture's expected count read two different
    # registries (fix loop for the Task 10 audit test).
    from reachy_companion import openai_realtime as _oai
    from reachy_companion import huggingface_realtime as _hf

    monkeypatch.setattr(_oai, "get_tool_specs", core_tools.get_tool_specs)
    monkeypatch.setattr(_hf, "get_tool_specs", core_tools.get_tool_specs)
    core_tools.initialize_tools(force=True)
    yield core_tools
    core_tools._TOOLS_SIGNATURE = None


def _handler(mode: ConversationMode = ConversationMode.ONE_ON_ONE) -> OpenAIRealtimeHandler:
    """Return a `__new__`-built handler carrying only mode and barge state."""
    h = OpenAIRealtimeHandler.__new__(OpenAIRealtimeHandler)
    h._conversation_mode = mode
    h._turn_mode = mode
    h._turn_modes = {}
    h._mode_update_seq = 0
    h._session_update_lock = asyncio.Lock()
    h._session_update_event_id = None
    h._session_update_waiter = None
    h._session_update_ack_debt = 0
    # Default to "the loop is running", so an update waits for its ack.
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


def _session_names(registry: ModuleType, mode: ConversationMode) -> set[str]:
    excluded = session_tool_exclusions(mode)
    return {spec["name"] for spec in registry.get_tool_specs(exclusion_list=excluded)}


# The 19 the locked profile ships, spelled out once so a silent change fails here.
EXPECTED_SESSION_TOOLS = frozenset(
    {
        "camera",
        "look_around",
        "play_emotion",
        "dance",
        "head",
        "face",
        "memory",
        "home_control",
        "music",
        "pollen_robotics_reachy_mini_search_tool__search_web",
        "set_conversation_mode",
        "summarize_conversation",
        "wait_for_user",
        "go_to_sleep",
        "youtube",
        "nas",
        "draw_picture",
        "drive",
        "email_send",
    }
)


def test_the_core_is_the_nineteen_tools_and_nothing_else() -> None:
    """"Aim for fewer than 20 functions available at the start of a turn" — all of them, at once."""
    assert CORE_TOOL_NAMES == EXPECTED_SESSION_TOOLS
    assert len(CORE_TOOL_NAMES) == 19
    # The stop lane is always reachable (Codex round 1, P2-7).
    assert "music" in CORE_TOOL_NAMES


def test_every_registered_tool_is_sent_or_deliberately_hidden(registry: ModuleType) -> None:
    """A registered tool outside the core is unreachable, so the only ones allowed are the two SystemTools."""
    registered = set(registry.get_tools())
    assert registered - CORE_TOOL_NAMES - set(registry.EXTRA_TOOLS) == {"task_status", "task_cancel"}
    assert CORE_TOOL_NAMES <= registered


@pytest.mark.parametrize("mode", [ConversationMode.ONE_ON_ONE, ConversationMode.GROUP])
def test_both_talking_modes_send_the_nineteen(registry: ModuleType, mode: ConversationMode) -> None:
    """一對一 and 多人 share one surface; MCP extras sit on top of it."""
    kept = _session_names(registry, mode)
    assert kept - set(registry.EXTRA_TOOLS) == EXPECTED_SESSION_TOOLS
    assert len(kept - set(registry.EXTRA_TOOLS)) == 19


@pytest.mark.parametrize("mode", list(ConversationMode))
def test_task_status_and_task_cancel_stay_registered_but_are_never_sent(
    registry: ModuleType, mode: ConversationMode
) -> None:
    """The background tool manager keeps them; the model does not get the two-call cancel chain."""
    assert {"task_status", "task_cancel"} <= set(registry.get_tools())
    assert {"task_status", "task_cancel"} <= set(session_tool_exclusions(mode))
    assert not {"task_status", "task_cancel"} & _session_names(registry, mode)


def test_record_mode_is_four_local_tools(registry: ModuleType) -> None:
    """紀錄模式 keeps its allowlist, minus the task tools, plus any MCP extras (P2-8, P2-12)."""
    kept = _session_names(registry, ConversationMode.RECORD)
    assert RECORD_TOOL_ALLOWLIST == {"set_conversation_mode", "summarize_conversation", "go_to_sleep", "wait_for_user"}
    assert kept - set(registry.EXTRA_TOOLS) == RECORD_TOOL_ALLOWLIST
    assert "camera" not in kept and "music" not in kept and "dance" not in kept


@pytest.mark.parametrize("mode", list(ConversationMode))
def test_the_mcp_extras_are_never_hidden(monkeypatch: pytest.MonkeyPatch, mode: ConversationMode) -> None:
    """An MCP tool is named by no list here, so hiding it would strand it for the visit (P2-8)."""
    monkeypatch.setattr(tb_mod, "EXTRA_TOOLS", {"notion_mcp__search": object()})
    monkeypatch.setattr(tb_mod, "get_tools", lambda: {"camera": object(), "notion_mcp__search": object()})
    excluded = session_tool_exclusions(mode)
    assert "notion_mcp__search" not in excluded
    assert excluded == (["camera"] if mode is ConversationMode.RECORD else [])


@pytest.mark.asyncio
async def test_active_surface_log_carries_mode_and_count(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    registry: ModuleType,
) -> None:
    """The journal exposes the exact active tool surface after every update (`Tools in session (`)."""
    import logging

    from reachy_companion import openai_realtime as oai_mod

    async def _update_for(handler: OpenAIRealtimeHandler, **kwargs: Any) -> None:
        handler._last_session_update = kwargs
        handler._note_session_updated()

    for mode in ConversationMode:
        h = _handler(mode)
        h._boot_gate_active = True
        h.instance_path = None
        h.connection = SimpleNamespace(
            session=SimpleNamespace(update=lambda **kwargs: _update_for(h, **kwargs)),
        )
        monkeypatch.setattr(h, "_mode_instructions", lambda: "INSTRUCTIONS")
        caplog.clear()

        with caplog.at_level(logging.INFO, logger=oai_mod.logger.name):
            assert await h._push_mode_update() is True

        expected_count = len(_session_names(registry, mode))
        assert any(
            record.message.startswith(f"Tools in session ({mode.value}, {expected_count}): ")
            for record in caplog.records
        )


def test_handler_exclusions_follow_the_mode_alone() -> None:
    """`_mode_tool_exclusions` is the handler's live view of the mode."""
    for mode in ConversationMode:
        assert _handler(mode)._mode_tool_exclusions() == session_tool_exclusions(mode)


def test_the_toolbox_machinery_is_gone() -> None:
    """No router, no seam, no box state: a box that cannot be opened must not be half there."""
    from dataclasses import fields

    from reachy_companion.tools.core_tools import ToolDependencies

    assert not hasattr(tb_mod, "TOOLBOXES") and not hasattr(tb_mod, "TOOLBOX_CATEGORIES")
    assert not hasattr(OpenAIRealtimeHandler, "open_toolbox")
    assert not hasattr(OpenAIRealtimeHandler, "close_toolboxes")
    assert "open_toolbox" not in {field.name for field in fields(ToolDependencies)}
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("reachy_companion.tools.open_toolbox")


@pytest.mark.asyncio
async def test_the_idle_picker_obeys_the_mode_tool_diet(monkeypatch: pytest.MonkeyPatch) -> None:
    """紀錄模式 hides the body tools, so the idle policy must not reach for them.

    Final review, C4. `send_idle_signal` selected from the UNFILTERED registry,
    so a quiet recording could still break into a dance, an emotion or a head
    turn three minutes in — movement the mode exists to suppress, chosen by a
    picker that never learned about modes.
    """
    from reachy_companion import conversation_handler as ch_mod

    seen: list[set[str]] = []

    async def _capture(**kwargs: Any) -> None:
        seen.append(set(kwargs["available_tool_names"]))
        return None

    monkeypatch.setattr(ch_mod, "start_idle_tool_call", _capture)
    h = _handler(ConversationMode.RECORD)
    h.connection = SimpleNamespace()  # `_is_connected()` only checks for one
    h.output_queue = asyncio.Queue()
    h.tool_manager = MagicMock()

    await h.send_idle_signal(200.0)

    assert seen, "the idle picker never ran"
    assert not seen[0] & set(h._mode_tool_exclusions()), "idle candidates included hidden tools"
    for name in ("dance", "play_emotion", "head"):
        assert name not in seen[0]
    assert seen[0] <= RECORD_TOOL_ALLOWLIST | set(EXTRA_TOOLS)


# The 22-tool core's schemas at HEAD 1.29.1 (cf661c3) — `json.dumps(spec,
# ensure_ascii=False)` summed over the specs sent in 一對一 with no box open and
# `HA_ENTITIES` unset. Measured 2026-10-30 from a `git archive HEAD` scratch
# copy, the same way as this test: 21,921 chars for 22 tools, and 30,586 for
# all 30 with both boxes open. A hard-coded number because the old surface no
# longer exists to be built.
_OLD_22_CORE_SCHEMA_CHARS = 21_921


def test_the_nineteen_schemas_are_smaller_than_the_old_twenty_two_core(
    registry: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """All 19 always-on tools must cost less every turn than the 22 that used to be the core."""
    monkeypatch.delenv("HA_ENTITIES", raising=False)
    registry.initialize_tools(force=True)  # `home_control` builds its description from HA_ENTITIES
    specs = registry.get_tool_specs(exclusion_list=session_tool_exclusions(ConversationMode.ONE_ON_ONE))
    local = [spec for spec in specs if spec["name"] not in registry.EXTRA_TOOLS]
    assert len(local) == 19
    total = sum(len(json.dumps(spec, ensure_ascii=False)) for spec in local)
    assert total < _OLD_22_CORE_SCHEMA_CHARS, total


# --- the system prompt's tool rules (prompts.py, operator text) ----------------


def test_the_prompt_names_no_toolbox() -> None:
    """There is nothing to open, so the prompt must not teach opening it.

    Since 2026-10-30 (context diet) the two availability rules are one sentence.
    """
    from reachy_companion.prompts import hardening_block

    block = hardening_block()
    assert "工具箱" not in block
    assert "open_toolbox" not in block
    assert "你能做的事都在工具清單裡，直接呼叫就好；清單上沒有的事，就說你做不到，不要自己演一遍。" in block


def test_the_ask_first_rule_waits_for_the_answer_before_playing() -> None:
    """1.28.4 live: 「要放就直接放」 read as "play now", and one run skipped the question.

    D-038 case 6: a place alone fits both video sources, so Reachy asks which
    one, then does what was asked once the answer comes.

    1.36.5 (D-045): 「直接放」 is allowed in one place only, the family-library
    branch that a named source or 「我們」 reaches. Unscoped, it made mini skip the
    question; scoped, the place-only case asked 8/8 and 「放我們去波士頓旅行的影片」
    played 4/4 (without the phrase: 1/4).
    """
    from reachy_companion.prompts import hardening_block

    block = hardening_block()
    assert "要放就直接放" not in block
    assert block.count("直接放") == 1
    assert "就是家裡的影片庫，直接放" in block
    assert "不要再問使用者一次" not in block
    assert "先問一句" in block and "要找家裡的影片庫還是 YouTube" in block
    assert "等回答了再照原本的要求做" in block
