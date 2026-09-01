"""Turn the head and hold it there for a moment. Filename == Tool.name.

Movement only -- no picture. The head goes where it is sent and stays for a
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
