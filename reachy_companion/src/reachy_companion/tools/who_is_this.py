import logging
from typing import Any

from reachy_companion.tools.core_tools import Tool, ToolDependencies
from reachy_companion.tools.face_support import identify_with_retries, recognizer_or_unavailable


logger = logging.getLogger(__name__)


class WhoIsThis(Tool):
    """Look once and report who is in front of the robot, by name if known."""

    name = "who_is_this"
    description = (
        "Look at the person in front of the camera and check whether you recognize them from face memory. "
        "Always use this tool — instead of the camera tool — whenever the question is about a person's "
        'IDENTITY: who someone is, "do you know me", "do you remember me", "what is my name", or who just '
        "arrived. Returns a status only: recognized (with the remembered name), unknown, ambiguous, no_face, "
        "too_far or unavailable. It never returns a picture. If the status is not recognized, "
        "say plainly that you do not recognize them — never guess a name."
    )
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> dict[str, Any]:
        """Identify the face in front of the camera, over a few short looks."""
        recognizer, refusal = recognizer_or_unavailable(deps)
        if refusal is not None:
            return refusal

        result = await identify_with_retries(deps, recognizer)
        logger.info(
            "Tool call: who_is_this status=%s name=%s score=%s",
            result.get("status"),
            result.get("name"),
            result.get("score"),
        )
        return result
