"""Send an email, behind a confirmation gate (D-018, R2/R3). Filename == Tool.name.

This is the most irreversible tool in the app: real mail, to a third party, from
the operator's own account, on a fuzzy voice intent. So the first call sends
nothing and reads the **whole envelope** back; only a second call with
`confirm: true` sends, and it sends exactly the message that was read back.

Review round 1, finding 5: the read-back covers **every** recipient (To and CC),
the subject, and the body. No recipient can reach `send_mail` that the summary
did not name, because the summary is derived from the same normalised envelope
that is parked.

Review round 2, finding 4: the read-back carries the **entire** body, verbatim.
The previous version quoted only the first line, capped at 120 characters, plus
a length and a hex digest -- and a person cannot verify text they were never
told. Two bodies sharing an opening line produced confirmations no listener
could tell apart. A body longer than `gmail_smtp.MAX_BODY_CHARS` (500) is now
**refused** rather than summarised, because a body that cannot be read back in
full cannot be confirmed at all.

Review round 1, finding 17: **there is no BCC.** A blind recipient is one the
read-back cannot surface.

The gate discipline is `calendar_delete`'s, copied deliberately (Task 2 review
ruling): the armed payload is the only executable source, **every path settles
the claim, in a `finally`**, and only a known transient transport fault releases
the authorisation for a bare retry.
"""

from __future__ import annotations
import asyncio
import logging
from typing import Any, Dict

from reachy_companion.hanova import redact, settings, gmail_smtp
from reachy_companion.hanova.confirm import GATE, confirmation_expired
from reachy_companion.tools.core_tools import Tool, ToolDependencies
from reachy_companion.hanova.gmail_smtp import SmtpError, is_transient, friendly_message


logger = logging.getLogger(__name__)

_MAX_RECIPIENTS = 10


class EmailSend(Tool):
    """Send a plain-text email after the user confirms the whole envelope."""

    name = "email_send"
    description = "Send an email. 需要先確認：先讀回收件人、副本、主旨再寄出。"
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "to": {
                "type": "string",
                "description": "Recipient address(es), comma separated. A name alone is not enough.",
                "minLength": 5,
            },
            "subject": {"type": "string", "description": "Subject line."},
            "body": {"type": "string", "description": "Plain-text message body."},
            "cc": {"type": "string", "description": "Optional comma-separated cc addresses."},
            "confirm": {
                "type": "boolean",
                "description": "Set true only after the user has confirmed the full envelope read back to them.",
            },
        },
        # Finding 4: optional in the schema, mandatory in the non-confirm branch.
        # The confirming call carries only `confirm`, so the frozen envelope
        # cannot be mis-heard a second time.
        "required": [],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Read the whole envelope back, or send a previously confirmed one."""
        available, reason = settings.tool_status(self.name)
        if not available:
            return settings.unavailable(reason)

        if bool(kwargs.get("confirm")):
            return await self._execute_confirmed()

        subject = str(kwargs.get("subject", "")).strip()
        body = gmail_smtp.normalize_body(str(kwargs.get("body", "")))
        to_valid, to_rejected = gmail_smtp.normalize_recipients(str(kwargs.get("to") or ""))
        cc_valid, cc_rejected = gmail_smtp.normalize_recipients(str(kwargs.get("cc") or ""))

        if to_rejected or cc_rejected:
            # Finding 5: never silently drop what we could not parse -- an
            # address we cannot describe is an address we must not send to.
            return {
                "ok": False,
                "error": (
                    "one of the addresses is not a full email address containing @; "
                    "ask the user to spell it out, then try again"
                ),
                "rejected_count": len(to_rejected) + len(cc_rejected),
            }
        if not to_valid:
            return {"ok": False, "error": "to must be a full email address containing @; ask the user for it"}
        # De-duplicate across the two fields so the read-back count is honest.
        cc_valid = [address for address in cc_valid if address.lower() not in {a.lower() for a in to_valid}]
        if len(to_valid) + len(cc_valid) > _MAX_RECIPIENTS:
            return {"ok": False, "error": f"that is more than {_MAX_RECIPIENTS} recipients; narrow it down"}
        if not subject:
            return {"ok": False, "error": "subject is required"}
        if not body:
            return {"ok": False, "error": "body is required"}
        if len(body) > gmail_smtp.MAX_BODY_CHARS:
            # Round 2, finding 4: refusing is the honest answer. A body too long
            # to read back is a body the user cannot confirm, and summarising it
            # into a digest is what made two different messages sound identical.
            return {
                "ok": False,
                "status": "body_too_long",
                "max_chars": gmail_smtp.MAX_BODY_CHARS,
                "error": (
                    f"that message is {len(body)} characters, longer than the "
                    f"{gmail_smtp.MAX_BODY_CHARS} the robot can read back before sending. "
                    "Ask the user for a shorter message."
                ),
            }

        # Finding 5 + round 2 finding 4: the summary names EVERY recipient, the
        # subject, and the **entire** body, followed by a length-and-digest token
        # as an integrity check beside the text -- never instead of it. It is
        # built from the same normalised envelope that is parked, so nothing can
        # be present in one and absent from the other.
        cc_clause = f", copying {', '.join(cc_valid)}" if cc_valid else ", with nobody copied"
        summary = (
            f"send an email to {', '.join(to_valid)}{cc_clause}, "
            f"subject {subject!r}. "
            f"The message says, in full:\n{body}\n"
            f"(end of message; {gmail_smtp.body_digest(body)}). "
            "There are no blind recipients."
        )
        return GATE.arm(
            self.name,
            summary,
            {"to": to_valid, "cc": cc_valid, "subject": subject, "body": body},
        )

    async def _execute_confirmed(self) -> Dict[str, Any]:
        """Send the mail the user already authorised, settling the claim on every path."""
        pending = GATE.claim(self.name)
        if pending is None:
            return confirmation_expired()
        settled = False
        try:
            # Every payload lookup lives inside the `try` (Task 10 review
            # ruling): a KeyError out here would strand the claim for the rest
            # of the session.
            recipients = list(pending.payload["to"])
            copies = list(pending.payload["cc"])
            logger.info("Tool call: email_send confirmed to=%d cc=%d", len(recipients), len(copies))
            try:
                await asyncio.to_thread(
                    gmail_smtp.send_mail,
                    to=recipients,
                    subject=str(pending.payload["subject"]),
                    body=str(pending.payload["body"]),
                    cc=copies,
                )
            except (SmtpError, OSError, ValueError, KeyError) as exc:
                logger.warning("email_send failed: %s", redact.error(exc))
                if is_transient(exc):
                    # A transport failure is retryable on the same
                    # authorisation; the user already approved this exact
                    # envelope (finding 4). The claim id says *which* one
                    # (round 2, finding 2).
                    GATE.release(self.name, pending.claim_id)
                    settled = True
                    return {"ok": False, "error": friendly_message(exc), "retryable": True}
                # Round 2, finding 9: authentication, a refused recipient, a
                # refused sender or a refused message are all terminal -- the
                # approved envelope cannot succeed as approved, so the
                # authorisation is spent and a corrected one must be read back.
                GATE.complete(self.name, pending.claim_id)
                settled = True
                return {"ok": False, "error": friendly_message(exc)}
            GATE.complete(self.name, pending.claim_id)
            settled = True
            return {"ok": True, "status": "sent", "summary": pending.summary}
        finally:
            if not settled:
                # Task 2 review ruling: a holder that dies with an unreleased
                # claim strands the slot until the session resets. Spend it --
                # an unexpected fault is not a known-transient one (finding 9).
                logger.warning("email_send ended unexpectedly; spending the confirmation")
                GATE.complete(self.name, pending.claim_id)
