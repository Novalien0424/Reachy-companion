"""Upload a photo Reachy takes to Drive, behind a confirmation gate (D-018, R2/R3).

Upstream's `drive_upload` took an absolute path *on the operator's Mac*
(`server.py:929`), which is meaningless on a robot. Reinterpreted: the only file
the robot can meaningfully offer is one it just produced, so this captures a
single camera frame and uploads that.

The frame is captured on the **confirm** call, not when the action is armed, so
what lands in Drive is what the room looked like when the user said yes.

The gate discipline is `calendar_delete`'s, copied deliberately (Task 2 review
ruling): **every path settles the claim, in a `finally`**, and only a known
transient fault -- a 5xx, a rate limit, a socket error, or a camera that had no
frame ready this instant -- releases the authorisation for a bare retry.
"""

from __future__ import annotations
import time
import asyncio
import logging
from typing import Any, Dict

from reachy_companion.hanova import gdrive, redact, settings
from reachy_companion.hanova.gdrive import DriveError, is_transient, friendly_message
from reachy_companion.hanova.confirm import GATE, confirmation_expired
from reachy_companion.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


class DriveUpload(Tool):
    """Take a photo and upload it to the configured Drive folder."""

    name = "drive_upload"
    description = "Take a photo and upload it to Drive. 需要先確認：拍照並上傳雲端。"
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Optional file name for the photo."},
            "confirm": {
                "type": "boolean",
                "description": "Set true only after the user has confirmed the upload read back to them.",
            },
        },
        "required": [],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Read back the upload, or capture a frame and upload it once confirmed."""
        available, reason = settings.tool_status(self.name)
        if not available:
            return settings.unavailable(reason)
        if not getattr(deps, "camera_enabled", False):
            return {"ok": False, "error": "the camera is disabled, so there is no photo to upload"}

        if bool(kwargs.get("confirm")):
            return await self._execute_confirmed(deps)

        requested = str(kwargs.get("name") or "").strip()
        filename = requested or f"reachy-{time.strftime('%Y%m%d-%H%M%S')}.jpg"
        if not filename.lower().endswith((".jpg", ".jpeg")):
            filename = f"{filename}.jpg"
        return GATE.arm(
            self.name,
            f"take a photo with Reachy's camera and upload it to your Drive folder as {filename!r}",
            {"name": filename},
        )

    async def _execute_confirmed(self, deps: ToolDependencies) -> Dict[str, Any]:
        """Capture and upload what the user already authorised, settling the claim every time."""
        pending = GATE.claim(self.name)
        if pending is None:
            return confirmation_expired()
        filename = str(pending.payload["name"])
        logger.info("Tool call: drive_upload confirmed as %s", redact.text(filename))
        settled = False
        try:
            frame = deps.reachy_mini.media.get_frame_jpeg()
            if not frame:
                # The camera failing is transient; keep the authorisation so the
                # user can just say "try again" (finding 4).
                GATE.release(self.name, pending.claim_id)
                settled = True
                return {"ok": False, "error": "no camera frame was available to upload", "retryable": True}
            try:
                uploaded = await asyncio.to_thread(
                    gdrive.upload_bytes, bytes(frame), filename, "image/jpeg", settings.drive_parent_id()
                )
            except (DriveError, OSError, ValueError, KeyError) as exc:
                logger.warning("drive_upload failed: %s", redact.error(exc))
                if is_transient(exc):
                    GATE.release(self.name, pending.claim_id)
                    settled = True
                    return {"ok": False, "error": friendly_message(exc), "retryable": True}
                GATE.complete(self.name, pending.claim_id)
                settled = True
                return {"ok": False, "error": friendly_message(exc)}
            GATE.complete(self.name, pending.claim_id)
            settled = True
            return {
                "ok": True,
                "status": "uploaded",
                "file_id": uploaded.get("id"),
                "name": uploaded.get("name"),
                "link": uploaded.get("webViewLink"),
            }
        finally:
            if not settled:
                logger.warning("drive_upload ended unexpectedly; spending the confirmation")
                GATE.complete(self.name, pending.claim_id)
