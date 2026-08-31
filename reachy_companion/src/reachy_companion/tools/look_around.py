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
            # Comma-joined, not the list's repr: this string is read back to the
            # model, and brackets and quotes are noise to it (review round 1,
            # minor 3).
            return {"error": f"direction must be one of {', '.join(valid)}"}
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

        # Guarded, because from here on the head HAS been sent and every exit
        # owes the model the capture-failure envelope. `Camera` returns
        # `{"error": …}` for the faults it anticipates, but a driver fault
        # raises — and an exception escaping here would be turned into a bare
        # `{"error": …}` by the dispatcher, which is the *move*-failure shape and
        # would tell the model the head never moved (review round 1, minor 2).
        try:
            shot = await Camera()(deps, question=question)
        except asyncio.CancelledError:
            # A cancelled tool call is the caller unwinding the turn, not a
            # capture failure; it must keep propagating.
            raise
        except Exception as exc:  # noqa: BLE001 - the move already happened; report it honestly
            logger.warning("look_around: capture raised after the move: %s: %s", type(exc).__name__, exc)
            shot = {"error": f"camera failed: {type(exc).__name__}: {exc}"}
        # `direction_requested`, not `direction_moved`: `MoveHead` returns once
        # the move is QUEUED, and `MovementManager` publishes no accepted- or
        # completed-move signal for us to wait on (`moves.py:245-266`, `:764`) —
        # `set_hold_still(True)` can even drop a queued move silently. Clearing
        # the queue above removes the common way the move gets deferred; the
        # field name carries the rest of the honesty, and the description above
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
