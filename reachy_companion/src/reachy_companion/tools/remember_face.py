import asyncio
import logging
from typing import Any

from reachy_companion.tools.core_tools import Tool, ToolDependencies
from reachy_companion.tools.face_support import (
    hold_still,
    unavailable,
    capture_frame,
    recognizer_or_unavailable,
)


logger = logging.getLogger(__name__)


class RememberFace(Tool):
    """Enroll the person in front of the camera under a name."""

    name = "remember_face"
    description = (
        "Remember what the person in front of the camera looks like, under the name they gave you. "
        "Use this tool — not the camera tool — when the user asks you to remember them, their face, or "
        'what they look like ("remember me", "I am X, remember my face"). '
        "Only the name and a numeric face signature are stored — never a picture. Requires exactly one person in "
        "frame: with nobody or several people visible it refuses, and you should ask them to face you alone. "
        "Before calling, tell the person you are taking a quick look and ask them to look at you and hold still "
        "for two seconds; you will hold your head still while you memorize their face."
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

        recognizer, blocked = recognizer_or_unavailable(deps)
        if blocked is not None:
            return blocked

        # The whole burst runs with the head parked: a tracking correction or an
        # idle breath mid-capture is exactly the motion blur that costs a sample.
        async with hold_still(deps):
            frame, blocked = await capture_frame(deps)
            if blocked is not None:
                return blocked

            try:
                record, identification = await asyncio.to_thread(recognizer.enroll, frame, name)
            except Exception as e:
                logger.error("remember_face failed: %s: %s", type(e).__name__, e)
                return unavailable("internal_error")

            if record is None:
                logger.info("Tool call: remember_face refused name=%s status=%s", name[:40], identification.status)
                refusal: dict[str, Any] = identification.as_dict()
                return refusal

            # Two more looks, a fifth of a second apart: three embeddings of the
            # same face — a blink, a turn, another shadow — are what make the
            # later recognition survive that variation. Extras are best effort:
            # the first sample is already saved, so a miss ends the burst, never
            # the call.
            for _ in range(2):
                await asyncio.sleep(0.2)
                extra_frame, blocked = await capture_frame(deps, attempts=1)
                if blocked is not None:
                    break
                try:
                    extra_record, extra_identification = await asyncio.to_thread(recognizer.enroll, extra_frame, name)
                except Exception as e:
                    logger.warning("remember_face extra sample failed: %s: %s", type(e).__name__, e)
                    break
                if extra_record is None:
                    logger.info("remember_face extra sample refused: status=%s", extra_identification.status)
                    break
                record = extra_record

        logger.info("Tool call: remember_face saved name=%s samples=%d", record.name, len(record.embeddings))
        return {"status": "saved", "name": record.name, "samples": len(record.embeddings)}
