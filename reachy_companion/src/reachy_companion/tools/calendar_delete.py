"""Delete a calendar event, behind a confirmation gate (D-018, R3).

Upstream resolved the event by a two-character fuzzy substring match and deleted
it in one call (`host-tools.py:303-327`). Over a noisy Chinese voice channel that
is genuinely dangerous, so here the first call resolves and reads the event back,
and only a second call with `confirm: true` deletes -- and it deletes the event
that was read back, not whatever the second call's arguments said.

**This file is the worked example of the gated-tool shape** every other gated
tool copies (review round 1, finding 4):

* `match` is **optional in the schema and mandatory in the non-confirm branch**.
  The confirming call carries only `confirm: true`, so a schema that required
  `match` forced the model to resupply -- and possibly mis-hear -- the very field
  the gate exists to freeze.
* `GATE.claim()` takes the action *in flight* without spending it;
  `GATE.complete()` spends it only after Google acknowledged the delete;
  `GATE.release()` puts it back when the failure was transient, so the user can
  say "try again" instead of walking the whole read-back a second time.
* **Every path settles the claim, in a `finally`** (Task 2 review ruling). The
  `except` clause names a closed set of expected error families; anything outside
  it -- a bug, a cancellation, a driver raising its own class -- would otherwise
  leave the slot claimed forever, and a claimed slot refuses both `claim()` and
  `arm()` for the rest of the session. An unexpected failure is not a *known*
  transient fault (finding 9), so the fallback spends the authorisation rather
  than re-arming it: the model has to read a corrected action back.
"""

from __future__ import annotations
import asyncio
import logging
from typing import Any, Dict

from reachy_companion.hanova import gcal, redact, settings
from reachy_companion.hanova.gauth import GoogleApiError, is_transient, friendly_message
from reachy_companion.hanova.confirm import GATE, confirmation_expired
from reachy_companion.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)

_MIN_MATCH_LEN = 2


class CalendarDelete(Tool):
    """Delete one calendar event after the user confirms it."""

    name = "calendar_delete"
    description = "Delete a calendar event. 需要先確認：先讀回事件再刪。"
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "match": {
                "type": "string",
                "description": "Text from the event title to find it by. Omit when confirming.",
                "minLength": _MIN_MATCH_LEN,
            },
            "confirm": {
                "type": "boolean",
                "description": "Set true only after the user has confirmed the exact event read back to them.",
            },
        },
        # Finding 4: optional at the schema level, mandatory in the code path
        # that actually needs it. The confirming call carries only `confirm`.
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

        calendar_id = settings.gcal_calendar_id()
        logger.info("Tool call: calendar_delete resolving match=%s", redact.text(match))
        try:
            event, candidates, error = await asyncio.to_thread(
                gcal.find_event,
                calendar_id,
                match,
                settings.cal_delete_window_days(),
            )
        except (GoogleApiError, OSError, ValueError, KeyError) as exc:
            logger.warning("calendar_delete lookup failed: %s", redact.error(exc))
            return {"ok": False, "error": friendly_message(exc)}

        if error == "not_found":
            return {"ok": False, "error": "not_found"}
        if error == "ambiguous":
            return {
                "ok": False,
                "error": "ambiguous",
                "candidates": [{"summary": item.get("summary"), "when": gcal.event_when(item)} for item in candidates],
            }

        assert event is not None
        title = str(event.get("summary") or "")
        when = gcal.event_when(event)
        return GATE.arm(
            self.name,
            f"delete the calendar event {title!r} on {when}",
            {"calendar_id": calendar_id, "event_id": event.get("id"), "summary": title, "when": when},
        )

    async def _execute_confirmed(self) -> Dict[str, Any]:
        """Run the delete the user already authorised, settling the claim on every path."""
        pending = GATE.claim(self.name)
        if pending is None:
            return confirmation_expired()
        logger.info("Tool call: calendar_delete confirmed for %s", redact.ident(pending.payload.get("event_id")))
        settled = False
        try:
            try:
                await asyncio.to_thread(
                    gcal.delete_event,
                    calendar_id=str(pending.payload["calendar_id"]),
                    event_id=str(pending.payload["event_id"]),
                )
            except (GoogleApiError, OSError, ValueError, KeyError) as exc:
                logger.warning("calendar_delete failed: %s", redact.error(exc))
                if is_transient(exc):
                    # Round 2, findings 2 and 9: the claim id says *which*
                    # authorisation this is, and only a transient fault may put
                    # it back for a bare retry.
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
                # Task 2 review ruling: a holder that dies with an unreleased
                # claim strands the slot until the session resets. Spend it --
                # an unexpected fault is not a known-transient one (finding 9).
                logger.warning("calendar_delete ended unexpectedly; spending the confirmation")
                GATE.complete(self.name, pending.claim_id)
