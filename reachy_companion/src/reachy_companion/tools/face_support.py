"""Shared guards for the face-memory tools (D-013).

Both tools need the same three checks before any CPU work happens — kill switch,
camera, frame — and both must answer with an `Identification`-shaped dict rather
than an exception. This module holds that preamble so the two tool files stay
one screen each and cannot drift apart. It defines no `Tool`, so the registry
never loads it as one.
"""

import asyncio
import logging
from typing import Any

from numpy.typing import NDArray

from reachy_companion.face_id import IdentificationReason
from reachy_companion.tools.core_tools import ToolDependencies


logger = logging.getLogger(__name__)


def unavailable(reason: IdentificationReason) -> dict[str, Any]:
    """Return the tool-result shape used for every unavailable path.

    `reason` is restricted to the published codes because this dict is sent to
    the cloud model as-is; exception detail belongs in the log, not here.
    """
    return {"status": "unavailable", "face_count": 0, "reason": reason}


def recognizer_or_unavailable(deps: ToolDependencies) -> tuple[Any, dict[str, Any] | None]:
    """Return the recognizer, or the unavailable result explaining why there is none."""
    recognizer = deps.face_recognizer
    if recognizer is None or not getattr(recognizer, "enabled", True):
        return None, unavailable("face_memory_disabled")
    if not deps.camera_enabled:
        return None, unavailable("camera_disabled")
    return recognizer, None


async def capture_frame(deps: ToolDependencies) -> tuple[NDArray[Any] | None, dict[str, Any] | None]:
    """Grab one BGR frame off the event loop, or return the unavailable result."""
    try:
        frame = await asyncio.to_thread(deps.reachy_mini.media.get_frame)
    except Exception as e:
        logger.error("Face memory could not read a camera frame: %s: %s", type(e).__name__, e)
        return None, unavailable("internal_error")
    if frame is None:
        return None, unavailable("no_frame")
    return frame, None
