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
    # head_joints[0] (sweep_look.py:34-35), NOT an antenna angle.
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
    """Each fixed direction reaches the motion layer as that direction's head pose."""
    deps = _deps()

    result = await MoveHead()(deps, direction=direction)

    assert result == {"status": f"looking {direction}"}
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

    await MoveHead()(deps, direction="left")

    move = _queued_move(deps)
    assert move.start_body_yaw == CURRENT_BODY_YAW
    assert move.start_body_yaw not in CURRENT_ANTENNAS
    assert tuple(move.start_antennas) == CURRENT_ANTENNAS
    # Both resets are deliberate: the head goes to a fixed direction, so the body
    # and the antennas return to neutral underneath it.
    assert move.target_body_yaw == 0
    assert tuple(move.target_antennas) == (0, 0)


@pytest.mark.asyncio
async def test_move_head_rejects_a_non_string_direction() -> None:
    """A malformed argument is an error result, not a queued move."""
    deps = _deps()

    result = await MoveHead()(deps, direction=42)

    assert result == {"error": "direction must be a string"}
    deps.movement_manager.queue_move.assert_not_called()
