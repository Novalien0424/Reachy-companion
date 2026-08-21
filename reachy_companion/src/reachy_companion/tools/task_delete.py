"""Delete a task, behind a confirmation gate (D-018, R3). Filename == Tool.name.

Unlike completion this is irreversible, so it searches completed tasks too and
still refuses to act on anything the user has not had read back to them.

The shape is `calendar_delete`'s, copied deliberately (Task 7): `match` optional
in the schema and mandatory in the non-confirm branch, and **every path settles
the claim, in a `finally`** -- an unexpected exception would otherwise leave the
slot claimed, and a claimed slot refuses both `claim()` and `arm()` for the rest
of the session. An unexpected failure is not a *known* transient fault, so the
fallback spends the authorisation rather than re-arming it.
"""

from __future__ import annotations
import asyncio
import logging
from typing import Any, Dict

from reachy_companion.hanova import gtasks, redact, settings
from reachy_companion.hanova.gauth import GoogleApiError, is_transient, friendly_message
from reachy_companion.hanova.confirm import GATE, confirmation_expired
from reachy_companion.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)

_MIN_MATCH_LEN = 2


class TaskDelete(Tool):
    """Delete one to-do item after the user confirms it."""

    name = "task_delete"
    description = "Delete a to-do task. 需要先確認：先讀回項目再刪除。"
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "match": {
                "type": "string",
                "description": "Text from the task title to find it by. Omit when confirming.",
                "minLength": _MIN_MATCH_LEN,
            },
            "confirm": {
                "type": "boolean",
                "description": "Set true only after the user has confirmed the exact task read back to them.",
            },
        },
        # Finding 4: optional in the schema, mandatory in the non-confirm branch.
        "required": [],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Resolve and read back, or execute a previously confirmed delete."""
        available, reason = settings.tool_status(self.name)
        if not available:
            return settings.unavailable(reason)

        if bool(kwargs.get("confirm")):
            return await self._execute_confirmed()

        match = str(kwargs.get("match", "")).strip()
        if len(match) < _MIN_MATCH_LEN:
            return {"ok": False, "error": f"match must be at least {_MIN_MATCH_LEN} characters"}

        logger.info("Tool call: task_delete resolving match=%s", redact.text(match))
        try:
            # Completed tasks are searched too: "delete the one I already
            # finished" is a real request, and completion does not remove it.
            task, candidates, error = await asyncio.to_thread(gtasks.find_task, match, True)
        except (GoogleApiError, OSError, ValueError, KeyError) as exc:
            logger.warning("task_delete lookup failed: %s", redact.error(exc))
            return {"ok": False, "error": friendly_message(exc)}

        if error == "not_found":
            return {"ok": False, "error": "not_found"}
        if error == "ambiguous":
            return {
                "ok": False,
                "error": "ambiguous",
                "candidates": [{"title": item.get("title"), "list": item.get("list_title")} for item in candidates],
            }

        assert task is not None
        title = str(task.get("title") or "")
        return GATE.arm(
            self.name,
            f"delete the task {title!r}",
            {"list_id": task.get("list_id"), "task_id": task.get("id"), "title": title},
        )

    async def _execute_confirmed(self) -> Dict[str, Any]:
        """Run the delete the user already authorised, settling the claim on every path."""
        pending = GATE.claim(self.name)
        if pending is None:
            return confirmation_expired()
        logger.info("Tool call: task_delete confirmed for %s", redact.ident(pending.payload.get("task_id")))
        settled = False
        try:
            try:
                await asyncio.to_thread(
                    gtasks.delete_task,
                    list_id=str(pending.payload["list_id"]),
                    task_id=str(pending.payload["task_id"]),
                )
            except (GoogleApiError, OSError, ValueError, KeyError) as exc:
                logger.warning("task_delete failed: %s", redact.error(exc))
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
            return {"ok": True, "status": "deleted", "summary": pending.summary}
        finally:
            if not settled:
                logger.warning("task_delete ended unexpectedly; spending the confirmation")
                GATE.complete(self.name, pending.claim_id)
