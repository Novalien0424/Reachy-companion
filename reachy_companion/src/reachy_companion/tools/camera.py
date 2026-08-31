import base64
import logging
from typing import Any, Dict

from reachy_companion.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


class Camera(Tool):
    """Take a picture with the camera to see what is in front of the robot."""

    name = "camera"
    description = (
        "Take a picture with the camera and describe what is in front of the robot RIGHT NOW. It sees only "
        "where the head is already pointing; it does not move anything. "
        "Use when: the user asks what you see, or about something in front of you, what they are holding, "
        "their outfit, or how they look — 「你看到什麼」「這是什麼」「看看我今天穿的衣服」「what do you see」. "
        "Use when: the user asks you to look with no direction at all — do not ask for clarification, call "
        "this tool and describe what you see. "
        "Do NOT use when: the user asks you to physically turn or look in a direction (右邊/左邊/上面/下面/"
        "轉過去/那邊) — use look_around, which turns the head first and then looks. "
        "Do NOT use when: the question is about WHO a person is, whether you know or remember them, or what "
        "someone's name is — that is who_is_this. "
        "The camera is live; each call captures the current moment."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": (
                    "What to observe or ask about in the picture. "
                    "Examples: what is the user holding, describe the user's outfit, "
                    "what do you see around you, how does the user look today."
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

        return {"b64_im": base64.b64encode(jpeg_bytes).decode("utf-8")}
