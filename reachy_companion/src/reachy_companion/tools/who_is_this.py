import asyncio
import logging
from typing import Any

from reachy_companion.tools.core_tools import Tool, ToolDependencies
from reachy_companion.tools.face_support import unavailable, capture_frame, recognizer_or_unavailable


logger = logging.getLogger(__name__)


class WhoIsThis(Tool):
    """Look once and report who is in front of the robot, by name if known."""

    name = "who_is_this"
    description = (
        "Look at the person in front of the camera and check whether you recognize them from face memory. "
        "Always use this tool — instead of the camera tool — whenever the question is about a person's "
        'IDENTITY: who someone is, "do you know me", "do you remember me", "what is my name", or who just '
        "arrived. Returns a status only: recognized (with the remembered name), unknown, ambiguous, no_face, "
        "too_far, multiple_faces or unavailable. It never returns a picture. If the status is not recognized, "
        "say plainly that you do not recognize them — never guess a name."
    )
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> dict[str, Any]:
        """Identify the face in the current camera frame."""
        recognizer, refusal = recognizer_or_unavailable(deps)
        if refusal is not None:
            return refusal

        frame, refusal = await capture_frame(deps)
        if refusal is not None:
            return refusal

        try:
            identification = await asyncio.to_thread(recognizer.identify, frame)
        except Exception as e:
            logger.error("who_is_this failed: %s: %s", type(e).__name__, e)
            return unavailable("internal_error")

        logger.info(
            "Tool call: who_is_this status=%s name=%s score=%s",
            identification.status,
            identification.name,
            identification.score,
        )
        result: dict[str, Any] = identification.as_dict()
        return result
