"""Mark a task complete, behind a confirmation gate (D-018, R3). Filename == Tool.name.

Completion is a PATCH to `status=completed`, which the Tasks UI can undo -- but
over a noisy Chinese voice channel the risk is not the write, it is writing to
the *wrong* task, and that is what the gate exists for. The first call resolves
the match across every list and reads the task back; only a second call with
`confirm: true` writes, and it writes the task that was read back rather than
whatever the second call's arguments said.

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

# Review finding 2. Wording, not data: it names no task, no list and no id, so
# it is safe for the model to say out loud verbatim.
TRUNCATED_MESSAGE = (
    "The to-do lists were too long to search all the way through, so I cannot be "
    "sure which task is meant. Ask the user which list it is in, then try again."
)


class TaskComplete(Tool):
    """Mark one to-do item complete after the user confirms it."""

    name = "task_complete"
    description = "Mark a to-do task complete. 需要先確認：先讀回項目再標記完成。"
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
        """Resolve and read back, or execute a previously confirmed completion."""
        available, reason = settings.tool_status(self.name)
        if not available:
            return settings.unavailable(reason)

        if bool(kwargs.get("confirm")):
            return await self._execute_confirmed()

        match = str(kwargs.get("match", "")).strip()
        if len(match) < _MIN_MATCH_LEN:
            return {"ok": False, "error": f"match must be at least {_MIN_MATCH_LEN} characters"}

        logger.info("Tool call: task_complete resolving match=%s", redact.text(match))
        try:
            task, candidates, error = await asyncio.to_thread(gtasks.find_task, match, False)
        except (GoogleApiError, OSError, ValueError, KeyError) as exc:
            logger.warning("task_complete lookup failed: %s", redact.error(exc))
            return {"ok": False, "error": friendly_message(exc)}

        candidate_view = [{"title": item.get("title"), "list": item.get("list_title")} for item in candidates]
        if error == "not_found":
            return {"ok": False, "error": "not_found"}
        if error == "ambiguous":
            return {"ok": False, "error": "ambiguous", "candidates": candidate_view}
        if error == "truncated":
            # Review finding 2: the search hit its page cap, so even a single hit
            # cannot be shown to be the only one. Refuse, and say why.
            return {
                "ok": False,
                "error": "search_truncated",
                "message": TRUNCATED_MESSAGE,
                "candidates": candidate_view,
            }

        assert task is not None
        title = str(task.get("title") or "")
        return GATE.arm(
            self.name,
            f"mark the task {title!r} complete",
            {"list_id": task.get("list_id"), "task_id": task.get("id"), "title": title},
        )

    async def _execute_confirmed(self) -> Dict[str, Any]:
        """Run the completion the user already authorised, settling the claim on every path."""
        pending = GATE.claim(self.name)
        if pending is None:
            return confirmation_expired()
        logger.info("Tool call: task_complete confirmed for %s", redact.ident(pending.payload.get("task_id")))
        settled = False
        try:
            try:
                await asyncio.to_thread(
                    gtasks.complete_task,
                    list_id=str(pending.payload["list_id"]),
                    task_id=str(pending.payload["task_id"]),
                )
            except (GoogleApiError, OSError, ValueError, KeyError) as exc:
                logger.warning("task_complete failed: %s", redact.error(exc))
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
            return {"ok": True, "status": "completed", "summary": pending.summary}
        finally:
            if not settled:
                logger.warning("task_complete ended unexpectedly; spending the confirmation")
                GATE.complete(self.name, pending.claim_id)
