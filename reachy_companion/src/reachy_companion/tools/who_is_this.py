import asyncio
import logging
from typing import Any

from reachy_companion.tools.core_tools import Tool, ToolDependencies
from reachy_companion.tools.face_support import capture_frame, recognizer_or_unavailable


logger = logging.getLogger(__name__)


class WhoIsThis(Tool):
    """Look once and report who is in front of the robot, by name if known."""

    name = "who_is_this"
    description = (
        "Look at the person in front of the camera and check whether you recognize them from face memory. "
        'Use this when the user asks who they are, whether you still recognize them, or "do you remember me". '
        "Returns a status only: recognized (with the remembered name), unknown, ambiguous, no_face, too_far, "
        "multiple_faces or unavailable. It never returns a picture. If the status is not recognized, say plainly "
        "that you do not recognize them — never guess a name."
    )
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> dict[str, Any]:
        """Identify the face in the current camera frame."""
        recognizer, unavailable = recognizer_or_unavailable(deps)
        if unavailable is not None:
            return unavailable

        frame, unavailable = await capture_frame(deps)
        if unavailable is not None:
            return unavailable

        try:
            identification = await asyncio.to_thread(recognizer.identify, frame)
        except Exception as e:
            logger.error("who_is_this failed: %s", e)
            return {"status": "unavailable", "face_count": 0, "reason": f"{type(e).__name__}: {e}"}

        logger.info(
            "Tool call: who_is_this status=%s name=%s score=%s",
            identification.status,
            identification.name,
            identification.score,
        )
        result: dict[str, Any] = identification.as_dict()
        return result
