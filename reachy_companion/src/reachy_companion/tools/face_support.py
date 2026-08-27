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

# Statuses that carry evidence about a face the camera actually saw. `no_face`
# and `unavailable` do not, so a scored miss always beats them as an answer.
_INFORMATIVE_STATUSES = ("recognized", "ambiguous", "unknown", "too_far", "multiple_faces")


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


async def capture_frame(
    deps: ToolDependencies, *, attempts: int = 3, pause_s: float = 0.05
) -> tuple[NDArray[Any] | None, dict[str, Any] | None]:
    """Grab one BGR frame off the event loop, retrying transient `None` frames.

    The appsink is drop=True/max-buffers=1 with a 20 ms pull: on a loaded CM4 a
    `None` frame is routine timing, not an error, so one miss must not fail the
    tool. A raising camera is a different thing and returns at once.
    """
    for attempt in range(attempts):
        try:
            frame = await asyncio.to_thread(deps.reachy_mini.media.get_frame)
        except Exception as e:
            logger.error("Face memory could not read a camera frame: %s: %s", type(e).__name__, e)
            return None, unavailable("internal_error")
        if frame is not None:
            return frame, None
        if attempt + 1 < attempts:
            await asyncio.sleep(pause_s)
    return None, unavailable("no_frame")


async def identify_with_retries(
    deps: ToolDependencies, recognizer: Any, *, attempts: int = 3, pause_s: float = 0.15
) -> dict[str, Any]:
    """Look up to `attempts` times; the first recognition wins.

    Mirrors the wake check's round loop (`huggingface_realtime.py`) at tool
    level: a blink, a turned head or one dropped frame must not decide the
    answer. Failing a recognition, the best evidence seen is what the model and
    the log get — the last scored look, or else the first miss.
    """
    best: dict[str, Any] | None = None
    fallback: dict[str, Any] | None = None
    for attempt in range(attempts):
        frame, refusal = await capture_frame(deps)
        if refusal is not None:
            fallback = fallback or refusal
        else:
            try:
                identification = await asyncio.to_thread(recognizer.identify, frame)
            except Exception as e:
                logger.error("identify_with_retries failed: %s: %s", type(e).__name__, e)
                fallback = fallback or unavailable("internal_error")
            else:
                result: dict[str, Any] = identification.as_dict()
                if result.get("status") == "recognized":
                    return result
                if result.get("status") in _INFORMATIVE_STATUSES:
                    best = result
                elif fallback is None:
                    fallback = result
        if attempt + 1 < attempts:
            await asyncio.sleep(pause_s)
    return best or fallback or unavailable("internal_error")
