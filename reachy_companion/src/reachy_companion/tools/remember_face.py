import asyncio
import logging
from typing import Any

from reachy_companion.tools.core_tools import Tool, ToolDependencies
from reachy_companion.tools.face_support import capture_frame, recognizer_or_unavailable


logger = logging.getLogger(__name__)


class RememberFace(Tool):
    """Enroll the person in front of the camera under a name."""

    name = "remember_face"
    description = (
        "Remember what the person in front of the camera looks like, under the name they gave you. "
        'Use this when the user asks you to remember them or their face ("remember me", "I am X, remember my face"). '
        "Only the name and a numeric face signature are stored — never a picture. Requires exactly one person in "
        "frame: with nobody or several people visible it refuses, and you should ask them to face you alone."
    )
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "The name to remember this person by, as they gave it. One person per call.",
            },
        },
        "required": ["name"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> dict[str, Any]:
        """Store one face embedding for `name` from the current camera frame."""
        name = kwargs.get("name")
        if not isinstance(name, str) or not name.strip():
            logger.warning("remember_face: empty name")
            return {"error": "name must be a non-empty string"}

        recognizer, unavailable = recognizer_or_unavailable(deps)
        if unavailable is not None:
            return unavailable

        frame, unavailable = await capture_frame(deps)
        if unavailable is not None:
            return unavailable

        try:
            record, identification = await asyncio.to_thread(recognizer.enroll, frame, name)
        except Exception as e:
            logger.error("remember_face failed: %s", e)
            return {"status": "unavailable", "face_count": 0, "reason": f"{type(e).__name__}: {e}"}

        if record is None:
            logger.info("Tool call: remember_face refused name=%s status=%s", name[:40], identification.status)
            refusal: dict[str, Any] = identification.as_dict()
            return refusal

        logger.info("Tool call: remember_face saved name=%s samples=%d", record.name, len(record.embeddings))
        return {"status": "saved", "name": record.name, "samples": len(record.embeddings)}
