import base64
import logging
from typing import Any, Dict

from reachy_companion.hanova import tv_session
from reachy_companion.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


class Camera(Tool):
    """Take a picture with the camera to see what is in front of the robot."""

    name = "camera"
    description = (
        "Take a picture where the head already points and describe what is in front of you now; it moves "
        "nothing. Use when: the user asks what you see, what they are holding or wearing, or how they "
        "look, or asks you to look with no direction — call it rather than asking which way. Do NOT use "
        "when: the user names a direction (左邊、右邊、上面、下面、轉過去) — that is look_around, which turns first; or "
        "asks who someone is or whether you know them — that is face with action=identify; or asks about the "
        "video playing on the TV — answer from the title the nas or youtube call returned."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": (
                    "What to look at or answer from the picture."
                ),
            },
        },
        "required": ["question"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Take a picture with the camera and return the base64-encoded JPEG."""
        question = (kwargs.get("question") or "").strip()
        if not question:
            logger.warning("camera: empty question")
            return {"error": "question must be a non-empty string"}

        logger.info("Tool call: camera question=%s", question[:120])

        if not deps.camera_enabled:
            logger.error("Camera is disabled")
            return {"error": "Camera is disabled"}

        jpeg_bytes = deps.reachy_mini.media.get_frame_jpeg()
        if jpeg_bytes is None:
            logger.error("No frame available from camera")
            return {"error": "No frame available"}

        result: Dict[str, Any] = {"b64_im": base64.b64encode(jpeg_bytes).decode("utf-8")}
        # Ours (2026-10-31): asked 「我們在哪裡？」 during a clip, mini looked with the
        # camera 2 of 3 times. The picture shows the room; what the TV plays is
        # here, by name, next to it.
        on_tv = tv_session.on_tv()
        if on_tv is not None:
            result["video_on_tv"] = on_tv
        return result
