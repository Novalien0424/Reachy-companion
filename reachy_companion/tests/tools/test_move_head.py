"""move_head must command the pose it computed, with each joint in its own slot."""

from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pytest

from reachy_mini.utils import create_head_pose
from reachy_companion.tools.move_head import MoveHead
from reachy_companion.tools.core_tools import ToolDependencies


CURRENT_BODY_YAW = 0.25
CURRENT_ANTENNAS = (1.5, -1.5)


def _deps() -> ToolDependencies:
    """Build deps whose joint positions have a body yaw distinct from both antennas."""
    reachy_mini = MagicMock()
    reachy_mini.get_current_head_pose.return_value = create_head_pose(0, 0, 0, 0, 0, 0, degrees=True)
    # get_current_joint_positions() -> (head_joints, antenna_joints); body yaw is
    # head_joints[0], NOT an antenna angle.
    reachy_mini.get_current_joint_positions.return_value = (
        np.array([CURRENT_BODY_YAW, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
        np.array(CURRENT_ANTENNAS),
    )
    return ToolDependencies(reachy_mini=reachy_mini, movement_manager=MagicMock(), motion_duration_s=0.5)


def _queued_move(deps: ToolDependencies) -> Any:
    """Return the single move handed to the movement manager."""
    (call,) = deps.movement_manager.queue_move.call_args_list
    (move,) = call.args
    return move


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
async def test_move_head_never_passes_an_antenna_angle_as_body_yaw() -> None:
    """The regression this test exists for: start_body_yaw was `antennas[0]`."""
    deps = _deps()

    await MoveHead().queue_direction(deps, "left")

    move = _queued_move(deps)
    assert move.start_body_yaw == CURRENT_BODY_YAW
    assert move.start_body_yaw not in CURRENT_ANTENNAS
    assert tuple(move.start_antennas) == CURRENT_ANTENNAS
    # Both resets are deliberate: the head goes to a fixed direction, so the body
    # and the antennas return to neutral underneath it.
    assert move.target_body_yaw == 0
    assert tuple(move.target_antennas) == (0, 0)


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
    """A malformed argument is rejected before any move is queued."""
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
