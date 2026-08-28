import asyncio
import logging
from typing import Any
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from reachy_companion.face_snapshot import save_snapshot
from reachy_companion.tools.core_tools import Tool, ToolDependencies
from reachy_companion.tools.face_support import (
    hold_still,
    unavailable,
    capture_frame,
    recognizer_or_unavailable,
)


logger = logging.getLogger(__name__)

# The snapshot encodes off to the side of the tool result (Codex A1-5), so the
# tasks need an owner: asyncio holds only a weak reference to a running task, and
# a fire-and-forget one can otherwise be garbage-collected mid-encode. The done
# callback discards the handle again, so this set is a hold, not a queue.
_SNAPSHOT_TASKS: set[asyncio.Task[bool]] = set()


def _snapshot_finished(task: "asyncio.Task[bool]") -> None:
    """Release the finished snapshot task and log anything it raised.

    Retrieving the exception here is also what keeps a failed snapshot from
    surfacing later as an unretrieved-exception warning on shutdown.
    """
    _SNAPSHOT_TASKS.discard(task)
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        logger.warning("remember_face snapshot failed: %s: %s", type(error).__name__, error)


def _schedule_snapshot(instance_path: str | Path | None, record_id: str, frame_bgr: NDArray[np.uint8]) -> None:
    """Start the enrollment snapshot encode; the caller never waits for it.

    `asyncio.to_thread` is what makes it fire-and-forget in fact and not only in
    shape (Codex A2-3): the writer shells out to ffmpeg, and a blocking call in
    a bare task would stall the realtime loop for the length of the encode.
    """
    try:
        task = asyncio.create_task(
            asyncio.to_thread(save_snapshot, instance_path, record_id, frame_bgr),
            name="face-snapshot",
        )
    except Exception as e:
        logger.warning("remember_face could not schedule the snapshot: %s: %s", type(e).__name__, e)
        return
    _SNAPSHOT_TASKS.add(task)
    task.add_done_callback(_snapshot_finished)


class RememberFace(Tool):
    """Enroll the person in front of the camera under a name."""

    name = "remember_face"
    description = (
        "Remember what the person in front of the camera looks like, under the name they gave you. "
        "Use this tool — not the camera tool — when the user asks you to remember them, their face, or "
        'what they look like ("remember me", "I am X, remember my face"). '
        "Only the name, a numeric face signature and one snapshot photo from this enrollment are stored — no "
        "picture is ever kept outside this deliberate, posed moment. Requires exactly one person in "
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

            # The snapshot (D-013 amendment) is the FIRST accepted sample's
            # frame, taken here because the extra samples below overwrite the
            # local name. The copy is defense in depth, not a fix for a known
            # bug: today's SDK hands back a private buffer per pull
            # (`gstreamer_utils.get_sample` -> `buf.extract_dup`), but that is
            # an implementation detail, not a documented contract, and this
            # array outlives the call on a background thread.
            # `np.ascontiguousarray` alone would NOT guarantee a detached array
            # — it returns its argument unchanged when the frame is already
            # contiguous uint8 — so the copy is explicit (Codex A1-5).
            snapshot_record_id: str = record.id
            snapshot_frame: NDArray[np.uint8] = np.ascontiguousarray(frame, dtype=np.uint8).copy()

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

        # Scheduled only once the hold has released, so the encode can never
        # extend the still pose, and never awaited, so it cannot delay the
        # answer: the person is remembered whether or not the photo lands.
        _schedule_snapshot(deps.instance_path, snapshot_record_id, snapshot_frame)

        logger.info("Tool call: remember_face saved name=%s samples=%d", record.name, len(record.embeddings))
        return {"status": "saved", "name": record.name, "samples": len(record.embeddings)}
