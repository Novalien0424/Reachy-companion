"""Dynamic toolboxes: a small static core plus two families loaded on demand.

The cookbook's Dynamic Conversation Flow pattern — "you only provide what's
relevant to the active phase… you use `session.update` to transition, replacing
the prompt and tools" — applied to the two families the operator judged
latency-tolerant (docs/research-mini-tool-calling-2026-08.md §A1).
"""

import asyncio
import importlib
from types import ModuleType, SimpleNamespace
from typing import Any
from collections import deque
from unittest.mock import AsyncMock, MagicMock

import pytest
from test_solo_barge import _install_barge_state

from reachy_companion import toolboxes as tb_mod
from reachy_companion.toolboxes import (
    TOOLBOXES,
    CORE_TOOL_NAMES,
    TOOLBOX_CATEGORIES,
    session_tool_exclusions,
)
from reachy_companion.record_mode import RECORD_TOOL_ALLOWLIST
from reachy_companion.openai_realtime import OpenAIRealtimeHandler
from reachy_companion.tools.core_tools import EXTRA_TOOLS
from reachy_companion.conversation_mode import ConversationMode
from reachy_companion.tools.open_toolbox import OpenToolbox


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


def _box_handler(mode: ConversationMode = ConversationMode.ONE_ON_ONE) -> OpenAIRealtimeHandler:
    """Return a `__new__`-built handler carrying only mode, box and barge state."""
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


def test_every_registered_tool_belongs_to_the_core_or_a_box(registry: ModuleType) -> None:
    """A tool in neither would be permanently unreachable — the worst failure."""
    EXTRA_TOOLS = registry.EXTRA_TOOLS

    registered = set(registry.get_tools())
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


def test_the_static_core_is_the_start_of_turn_surface(registry: ModuleType) -> None:
    """41 → 22, with the two SystemTool entries counted honestly."""
    EXTRA_TOOLS = registry.EXTRA_TOOLS

    excluded = session_tool_exclusions(ConversationMode.ONE_ON_ONE, ())
    kept = {spec["name"] for spec in registry.get_tool_specs(exclusion_list=excluded)}
    # `| EXTRA_TOOLS` because out-of-band MCP tools are never hidden; in a clean
    # test environment that set is empty and `kept` is exactly the core.
    assert kept == (CORE_TOOL_NAMES | set(EXTRA_TOOLS)) & set(registry.get_tools())
    assert "music" in kept  # the stop lane, always reachable
    for boxed in ("calendar", "tasks", "drive", "email_send", "notion_add", "tv", "nas"):
        assert boxed not in kept


def test_opening_a_box_adds_exactly_that_family(registry: ModuleType) -> None:
    """One `open_toolbox` brings in its family and nothing else."""
    core = {
        spec["name"]
        for spec in registry.get_tool_specs(exclusion_list=session_tool_exclusions(ConversationMode.GROUP, ()))
    }
    opened = {
        spec["name"]
        for spec in registry.get_tool_specs(
            exclusion_list=session_tool_exclusions(ConversationMode.GROUP, ("productivity",))
        )
    }
    assert opened - core == set(TOOLBOXES["productivity"])
    both = {
        spec["name"]
        for spec in registry.get_tool_specs(
            exclusion_list=session_tool_exclusions(ConversationMode.ONE_ON_ONE, ("productivity", "media"))
        )
    }
    assert both - core == set(TOOLBOXES["productivity"]) | set(TOOLBOXES["media"])


def test_the_documented_surface_sizes_hold(registry: ModuleType) -> None:
    """22 at rest, 27 / 24 with one box, 29 with both (design decision 8).

    Boxes accumulate within a mode; a turn that asks for the calendar and then
    for the TV keeps both. The numbers are documented, so they are asserted
    (Codex round 2, 2b-3).
    """
    # Every documented size is "plus any MCP extras", which are never hidden in
    # any mode (Codex round 3, finding 11). In a clean test environment that set
    # is empty; subtracting it keeps the assertion honest either way.
    extras = len(set(registry.EXTRA_TOOLS) & set(registry.get_tools()))

    def _surface(*boxes: str) -> int:
        excluded = session_tool_exclusions(ConversationMode.ONE_ON_ONE, boxes)
        return len({spec["name"] for spec in registry.get_tool_specs(exclusion_list=excluded)}) - extras

    assert _surface() == 22
    assert _surface("productivity") == 27
    assert _surface("media") == 24
    assert _surface("productivity", "media") == 29


@pytest.mark.asyncio
async def test_active_surface_log_carries_mode_boxes_and_count(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    registry: ModuleType,
) -> None:
    """The journal exposes the exact active tool surface after every update."""
    import logging

    from reachy_companion import openai_realtime as oai_mod

    async def _update_for(handler: OpenAIRealtimeHandler, **kwargs: Any) -> None:
        handler._last_session_update = kwargs
        handler._note_session_updated()

    scenarios = (
        (ConversationMode.ONE_ON_ONE, set(), "none"),
        (ConversationMode.GROUP, {"productivity"}, "productivity"),
        (ConversationMode.RECORD, {"media", "productivity"}, "media,productivity"),
    )

    for mode, boxes, boxes_label in scenarios:
        h = _box_handler(mode)
        h._boot_gate_active = True
        h.instance_path = None
        h._open_toolboxes = set(boxes)
        h.connection = SimpleNamespace(
            session=SimpleNamespace(update=lambda **kwargs: _update_for(h, **kwargs)),
        )
        monkeypatch.setattr(h, "_mode_instructions", lambda: "INSTRUCTIONS")
        caplog.clear()

        with caplog.at_level(logging.INFO, logger=oai_mod.logger.name):
            assert await h._push_mode_update() is True

        expected_count = len(
            {
                spec["name"]
                for spec in registry.get_tool_specs(exclusion_list=session_tool_exclusions(mode, boxes))
            }
        )
        assert any(
            record.message.startswith(f"Tools in session ({mode.value}, boxes={boxes_label}, {expected_count}): ")
            for record in caplog.records
        )


@pytest.mark.asyncio
async def test_a_second_box_adds_to_the_first(monkeypatch: pytest.MonkeyPatch) -> None:
    """Opening media must not close productivity mid-turn (design decision 8)."""
    h = _box_handler()
    monkeypatch.setattr(h, "_push_mode_update", AsyncMock(return_value=True))
    await h.open_toolbox("productivity")
    await h.open_toolbox("media")
    assert h._open_toolboxes == {"productivity", "media"}


def test_an_unknown_box_name_changes_nothing() -> None:
    """A category the registry never heard of is simply not a box."""
    assert session_tool_exclusions(ConversationMode.GROUP, ("nonsense",)) == session_tool_exclusions(
        ConversationMode.GROUP, ()
    )


def test_record_mode_ignores_open_boxes(registry: ModuleType) -> None:
    """紀錄模式 scopes to its allowlist no matter what was open when it started.

    Six local tools — the four the model uses plus the two SystemTool entries —
    and whatever MCP extras the operator installed, which are never hidden in
    any mode (Codex round 1, P2-8, P2-12).
    """
    EXTRA_TOOLS = registry.EXTRA_TOOLS

    excluded = session_tool_exclusions(ConversationMode.RECORD, ("productivity", "media"))
    kept = {spec["name"] for spec in registry.get_tool_specs(exclusion_list=excluded)}
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
    """A refused update must not advertise tools that never reached the session."""
    handler = _box_handler()
    handler._push_mode_update = AsyncMock(return_value=False)

    result = asyncio.run(handler.open_toolbox("media"))

    assert result["ok"] is False
    assert result.get("session_updated") is not True


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
    """A box already open costs no second `session.update`."""
    h = _box_handler()
    push = AsyncMock(return_value=True)
    monkeypatch.setattr(h, "_push_mode_update", push)
    await h.open_toolbox("media")
    again = await h.open_toolbox("media")
    assert again["status"] == "already_open"
    push.assert_awaited_once()  # no second session.update for a box already open


@pytest.mark.asyncio
async def test_open_toolbox_rejects_an_unknown_category(monkeypatch: pytest.MonkeyPatch) -> None:
    """An invented category is refused, with the real ones named back."""
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
    """A new mode is a new posture: whatever was loaded for the old one goes."""
    h = _box_handler()
    h._open_toolboxes = {"productivity", "media"}
    await h.set_conversation_mode("group")
    assert not h._open_toolboxes


@pytest.mark.asyncio
async def test_shutdown_closes_every_box(monkeypatch: pytest.MonkeyPatch) -> None:
    """`go_to_sleep` reaches `shutdown()`; boxes never outlive the visit."""
    from test_record_mode import _drive_shutdown

    h = _box_handler()
    h._open_toolboxes = {"productivity", "media"}
    h.deps.sleep_requested = True
    await _drive_shutdown(h, monkeypatch)
    assert not h._open_toolboxes


def test_handler_exclusions_follow_mode_and_boxes() -> None:
    """`_mode_tool_exclusions` is the handler's live view of mode plus boxes."""
    h = _box_handler(ConversationMode.RECORD)
    assert h._mode_tool_exclusions() == session_tool_exclusions(ConversationMode.RECORD, set())
    h2 = _box_handler()
    h2._open_toolboxes = {"media"}
    assert h2._mode_tool_exclusions() == session_tool_exclusions(ConversationMode.ONE_ON_ONE, {"media"})


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
    h = _box_handler(ConversationMode.RECORD)
    h.connection = SimpleNamespace()  # `_is_connected()` only checks for one
    h.output_queue = asyncio.Queue()
    h.tool_manager = MagicMock()

    await h.send_idle_signal(200.0)

    assert seen, "the idle picker never ran"
    assert not seen[0] & set(h._mode_tool_exclusions()), "idle candidates included hidden tools"
    for name in ("dance", "play_emotion", "move_head"):
        assert name not in seen[0]
    assert seen[0] <= RECORD_TOOL_ALLOWLIST | set(EXTRA_TOOLS)


@pytest.mark.asyncio
async def test_tool_refuses_when_the_seam_is_unwired() -> None:
    """No handler behind the seam means no box, said plainly."""
    result = await OpenToolbox()(SimpleNamespace(open_toolbox=None), category="media")
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_tool_forwards_the_category() -> None:
    """The tool is a router: it passes the category through and returns the result."""
    seen: list[str] = []

    async def _seam(category: str) -> dict[str, object]:
        seen.append(category)
        return {"ok": True, "status": "loaded", "category": category, "tools": []}

    result = await OpenToolbox()(SimpleNamespace(open_toolbox=_seam), category="productivity")
    assert seen == ["productivity"] and result["ok"] is True


def test_tool_description_enumerates_the_chinese_routing_triggers() -> None:
    """The description carries the routing rule the model reads at call time."""
    description = OpenToolbox.description
    for phrase in ("行程", "待辦", "郵件", "雲端", "音樂", "電視", "NAS", "productivity", "media"):
        assert phrase in description
    assert "Use when:" in description and "Do NOT use when:" in description


def test_the_open_toolbox_description_owns_the_continuation_rule() -> None:
    """The return states facts; the description is where the policy lives."""
    description = OpenToolbox().description
    assert "in the same turn" in description
    assert "without asking the user again" in description


def test_the_prompt_carries_the_same_routing_rules() -> None:
    """Research §A3: state the routing rule in both places, semantically."""
    from reachy_companion.prompts import hardening_block

    block = hardening_block()
    assert "工具箱" in block
    assert "open_toolbox" in block
    for phrase in ("productivity", "media", "安排時間", "音樂"):
        assert phrase in block
