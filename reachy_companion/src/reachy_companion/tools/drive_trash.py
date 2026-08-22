"""Move a Drive item to Trash, behind a confirmation gate (D-018, R3).

Trashing a folder trashes everything inside it, so the first call reads the real
name and type back from Drive rather than trusting the id the model produced.
Nothing here can permanently delete: `files.delete` is not exposed.

The shape is `calendar_delete`'s, copied deliberately (Task 2 review ruling):
`file_id` optional in the schema and mandatory in the non-confirm branch, and
**every path settles the claim, in a `finally`** -- an unexpected exception would
otherwise leave the slot claimed, and a claimed slot refuses both `claim()` and
`arm()` for the rest of the session. An unexpected failure is not a *known*
transient fault, so the fallback spends the authorisation rather than re-arming
it: the model has to read a corrected action back.

Restoring is an approved non-goal (review round 1, finding 17): an item stays in
Drive's Trash for about 30 days and the user can bring it back from the Drive UI
in two clicks, on any device, without the robot.
"""

from __future__ import annotations
import asyncio
import logging
from typing import Any, Dict

from reachy_companion.hanova import gdrive, redact, settings
from reachy_companion.hanova.gdrive import DriveError, is_transient, friendly_message
from reachy_companion.hanova.confirm import GATE, confirmation_expired
from reachy_companion.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


class DriveTrash(Tool):
    """Move one Drive file or folder to Trash after the user confirms it."""

    name = "drive_trash"
    description = "Move a Drive item to Trash. 需要先確認：先讀回檔名再丟。"
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "file_id": {
                "type": "string",
                "description": "Drive file or folder id, from drive_list. Omit when confirming.",
            },
            "confirm": {
                "type": "boolean",
                "description": "Set true only after the user has confirmed the exact item read back to them.",
            },
        },
        # Finding 4: optional in the schema, mandatory in the non-confirm branch.
        "required": [],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Read back the real item, or execute a previously confirmed trash."""
        available, reason = settings.tool_status(self.name)
        if not available:
            return settings.unavailable(reason)

        if bool(kwargs.get("confirm")):
            return await self._execute_confirmed()

        file_id = str(kwargs.get("file_id", "")).strip()
        if not file_id:
            return {"ok": False, "error": "file_id is required"}

        logger.info("Tool call: drive_trash resolving %s", redact.ident(file_id))
        try:
            item = await asyncio.to_thread(gdrive.get_file, file_id)
        except (DriveError, OSError, ValueError, KeyError) as exc:
            logger.warning("drive_trash lookup failed: %s", redact.error(exc))
            return {"ok": False, "error": friendly_message(exc)}

        name = str(item.get("name") or file_id)
        kind = "folder and everything in it" if item.get("mimeType") == gdrive.FOLDER_MIME else "file"
        return GATE.arm(
            self.name,
            f"move the Drive {kind} {name!r} to Trash",
            {"file_id": item.get("id") or file_id, "name": name},
        )

    async def _execute_confirmed(self) -> Dict[str, Any]:
        """Run the trash the user already authorised, settling the claim on every path."""
        pending = GATE.claim(self.name)
        if pending is None:
            return confirmation_expired()
        logger.info("Tool call: drive_trash confirmed for %s", redact.ident(pending.payload.get("file_id")))
        settled = False
        try:
            try:
                await asyncio.to_thread(gdrive.set_trashed, str(pending.payload["file_id"]), True)
            except (DriveError, OSError, ValueError, KeyError) as exc:
                logger.warning("drive_trash failed: %s", redact.error(exc))
                if is_transient(exc):
                    # Round 2, findings 2 and 9: only a transient fault may put
                    # the authorisation back for a bare retry.
                    GATE.release(self.name, pending.claim_id)
                    settled = True
                    return {"ok": False, "error": friendly_message(exc), "retryable": True}
                GATE.complete(self.name, pending.claim_id)
                settled = True
                return {"ok": False, "error": friendly_message(exc)}
            GATE.complete(self.name, pending.claim_id)
            settled = True
            return {"ok": True, "status": "trashed", "summary": pending.summary}
        finally:
            if not settled:
                logger.warning("drive_trash ended unexpectedly; spending the confirmation")
                GATE.complete(self.name, pending.claim_id)
