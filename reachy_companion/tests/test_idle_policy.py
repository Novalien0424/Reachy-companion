from __future__ import annotations
from typing import Any

import pytest

import reachy_companion.idle_policy as idle_policy_mod
from reachy_companion.tools.head import Head
from reachy_companion.tools.dance import Dance
from reachy_companion.tools.move_head import MoveHead
from reachy_companion.tools.core_tools import Tool, ToolDependencies
from reachy_companion.tools.dance_moves import Dance as DanceMoves
from reachy_companion.tools.idle_do_nothing import IdleDoNothing


class FakeIdleTool(Tool):
    """Tool with an idle tool name but not the idle tool class."""

    _auto_register = False
    name = "idle_do_nothing"
    description = "Fake idle tool with the same public name."
    parameters_schema: dict[str, Any] = {"type": "object", "properties": {}, "required": []}

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> dict[str, Any]:
        """Return a fake result."""
        return {"status": "fake"}


def test_choose_idle_tool_call_uses_registered_name_for_matching_class() -> None:
    """A matching idle class should use the registered tool instance name."""
    tool = IdleDoNothing()
    tool.name = "profile_idle_do_nothing"

    selected = idle_policy_mod.choose_idle_tool_call(
        ["profile_idle_do_nothing"],
        tool_registry={"profile_idle_do_nothing": tool},
    )

    assert selected == ("profile_idle_do_nothing", {"reason": "random idle policy selected stillness"})


def test_choose_idle_tool_call_rejects_same_name_with_unmatched_class() -> None:
    """A matching public name alone should not make a tool eligible."""
    selected = idle_policy_mod.choose_idle_tool_call(
        ["idle_do_nothing"],
        tool_registry={"idle_do_nothing": FakeIdleTool()},
    )

    assert selected is None


def test_choose_idle_tool_call_keeps_args_with_weighted_candidate(monkeypatch) -> None:
    """Argument generation should stay attached to the weighted candidate."""
    monkeypatch.setattr(idle_policy_mod.random, "choice", lambda _choices: "right")

    selected = idle_policy_mod.choose_idle_tool_call(
        ["head"],
        tool_registry={"head": Head()},
    )

    assert selected == ("head", {"action": "turn", "direction": "right"})


def test_the_idle_dance_goes_through_the_dance_family() -> None:
    """1.30.0: `dance` is the family, so the idle call names the action that runs upstream's `Dance`."""
    selected = idle_policy_mod.choose_idle_tool_call(["dance"], tool_registry={"dance": Dance()})

    assert selected == ("dance", {"action": "play"})
    assert isinstance(Dance.ACTIONS["play"], DanceMoves)


def test_a_bare_move_head_is_no_longer_an_idle_candidate() -> None:
    """The locked profile does not register `move_head`; the head family is the candidate."""
    selected = idle_policy_mod.choose_idle_tool_call(["move_head"], tool_registry={"move_head": MoveHead()})

    assert selected is None


@pytest.mark.asyncio
async def test_the_idle_head_turn_reaches_move_head_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """The idle arguments are a valid `head` call: `turn` forwards the direction to `move_head`."""
    seen: list[dict[str, Any]] = []

    async def _move(self: MoveHead, deps: Any, **kwargs: Any) -> dict[str, Any]:
        seen.append(kwargs)
        return {"status": "move_queued", "direction_requested": kwargs["direction"]}

    monkeypatch.setattr(idle_policy_mod.random, "choice", lambda _choices: "left")
    monkeypatch.setattr(MoveHead, "__call__", _move)
    _name, arguments = idle_policy_mod.choose_idle_tool_call(["head"], tool_registry={"head": Head()}) or ("", {})

    result = await Head()(None, **arguments)  # type: ignore[arg-type]

    assert seen == [{"direction": "left"}]
    assert result == {"status": "move_queued", "direction_requested": "left"}
