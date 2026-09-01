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
from reachy_companion.tools.move_head import MoveHead
from reachy_companion.tools.core_tools import ToolDependencies
from reachy_companion.tools.stop_dance import StopDance
from reachy_companion.conversation_mode import MODE_VALUES
from reachy_companion.tools.open_toolbox import OpenToolbox
from reachy_companion.tools.stop_emotion import StopEmotion
from reachy_companion.tools.head_tracking import HeadTracking
from reachy_companion.tools.set_conversation_mode import SetConversationMode


def _deps(**kwargs: object) -> ToolDependencies:
    return ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock(), **kwargs)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_head_tracking_refuses_a_string_instead_of_coercing_it() -> None:
    """`bool("false")` is True - the coercion that turned "stop" into "start"."""
    deps = _deps()

    result = await HeadTracking()(deps, enabled="false")

    assert result == {"error": "enabled must be true or false (a boolean, not a string)"}
    deps.movement_manager.set_head_tracking.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [True, False])
async def test_head_tracking_passes_a_real_boolean_through(enabled: bool) -> None:
    """Real booleans still reach the movement manager unchanged."""
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
    """Chinese display labels are rejected in favor of the enum values."""
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
