"""Outbound mail over SMTP, adapted from upstream `gmail_send.py` (D-018).

SMTP rather than the Gmail API because the OAuth grant this project has does not
carry the `gmail.send` scope (`bin/google/gmail_send.py:6-9`). The account and
its app password come from `HANOVA_SMTP_USER` / `HANOVA_SMTP_APP_PASSWORD`;
upstream read them out of a secrets JSON on the operator's Mac.

`smtp_factory` exists so the whole module can be tested without a socket.

**No BCC (review finding 17).** The parameter is gone, not ignored: a
blind-carbon recipient is one the confirmation read-back cannot surface, and
that is exactly the hole finding 5 closes.
"""

from __future__ import annotations
import re
import hashlib
import logging
import smtplib
from typing import Any, Dict, List
from email.message import EmailMessage

from reachy_companion.hanova import redact, settings


logger = logging.getLogger(__name__)

_TIMEOUT_S = 60
# Deliberately permissive but not empty: this is a sanity check against the STT
# channel handing us "mum" or "at gmail", not an RFC 5322 parser.
_ADDRESS = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")

# Round 2, finding 4: the longest body that can be honestly read back to a person
# who is listening. Roughly 30 seconds of speech. A body longer than this is
# refused rather than summarised, because a summary is not a confirmation.
MAX_BODY_CHARS = 500

# Round 2, finding 9. A failure is transient only when retrying the *same*
# envelope could plausibly succeed. Everything else means the resolved action is
# wrong, so the user's approval no longer describes anything achievable.
_TERMINAL_SMTP = (
    smtplib.SMTPAuthenticationError,
    smtplib.SMTPRecipientsRefused,
    smtplib.SMTPSenderRefused,
    smtplib.SMTPDataError,
    smtplib.SMTPNotSupportedError,
)
_TRANSIENT_SMTP = (
    smtplib.SMTPConnectError,
    smtplib.SMTPServerDisconnected,
    smtplib.SMTPHeloError,
)


class SmtpError(RuntimeError):
    """Mail that could not be built or could not be sent.

    Always raised `from` the underlying `smtplib` exception, so `is_transient`
    can classify it without the message text (round 2, finding 9).
    """


def is_transient(exc: BaseException) -> bool:
    """Return whether a gated retry on the same confirmation makes sense."""
    candidate: BaseException | None = exc
    if isinstance(exc, SmtpError) and exc.__cause__ is not None:
        candidate = exc.__cause__
    if isinstance(candidate, _TERMINAL_SMTP):
        return False
    if isinstance(candidate, _TRANSIENT_SMTP):
        return True
    # A bare socket problem is transient; anything else we cannot classify is
    # treated as terminal, because spending an authorisation is the safe error.
    return isinstance(candidate, (TimeoutError, OSError))


_TERMINAL_MESSAGES: Dict[type[BaseException], str] = {
    smtplib.SMTPAuthenticationError: "the mail account rejected the robot's credentials",
    smtplib.SMTPRecipientsRefused: "the mail server refused one of the recipients",
    smtplib.SMTPSenderRefused: "the mail server refused the sending address",
    smtplib.SMTPDataError: "the mail server refused the message itself",
    smtplib.SMTPNotSupportedError: "the mail server does not support this kind of connection",
}


def friendly_message(exc: BaseException) -> str:
    """Return a fixed, identifier-free reason the model may say out loud (finding 7)."""
    candidate: BaseException = exc
    if isinstance(exc, SmtpError) and exc.__cause__ is not None:
        candidate = exc.__cause__
    for family, message in _TERMINAL_MESSAGES.items():
        if isinstance(candidate, family):
            return message
    return "the mail could not be sent right now"


def smtp_factory() -> Any:
    """Return a context-manager SMTP connection. The single test seam."""
    return smtplib.SMTP_SSL(settings.smtp_host(), settings.smtp_port(), timeout=_TIMEOUT_S)


def normalize_recipients(raw: str | None) -> tuple[List[str], List[str]]:
    """Split, trim, validate and de-duplicate a recipient field (finding 5).

    Returns `(valid, rejected)`, both order-preserving. The caller must refuse
    to arm anything while *rejected* is non-empty: a recipient we could not
    parse is a recipient the read-back could not describe.
    """
    valid: List[str] = []
    rejected: List[str] = []
    seen: set[str] = set()
    for item in re.split(r"[,;]", raw or ""):
        candidate = item.strip().strip("<>")
        if not candidate:
            continue
        if not _ADDRESS.match(candidate):
            rejected.append(candidate)
            continue
        key = candidate.lower()
        if key in seen:
            continue
        seen.add(key)
        valid.append(candidate)
    return valid, rejected


def normalize_body(raw: str) -> str:
    """Apply the one body normalisation (round 2, finding 4).

    Whatever the read-back quotes must be byte-identical to what is sent, so
    there is exactly one function that decides what the body *is*: line endings
    collapsed, trailing whitespace stripped per line, the whole thing stripped.
    Idempotent by construction.
    """
    text = str(raw or "").replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in text.split("\n")).strip()


def body_digest(body: str) -> str:
    """Return an integrity token appended **after** the full body in the read-back.

    Round 2, finding 4 demoted this. It was the only description the summary
    carried of everything past the first line, and a human being cannot verify
    "digest 4f2a9c31" against what they meant to say. The body itself is now in
    the summary; this is a checksum beside it, not a substitute for it.
    """
    digest = hashlib.blake2s(body.encode("utf-8"), digest_size=4).hexdigest()
    return f"{len(body)} characters, digest {digest}"


def send_mail(
    to: List[str],
    subject: str,
    body: str,
    cc: List[str] | None = None,
) -> Dict[str, Any]:
    """Send one plain-text message to an already-validated envelope.

    *to* and *cc* are lists, not comma strings: by the time this is called the
    envelope has been normalised, validated and read back to the user, and no
    further parsing may reinterpret it (finding 5).
    """
    user = settings.smtp_user()
    password = settings.smtp_app_password()
    if not user or not password:
        raise SmtpError("HANOVA_SMTP_USER and HANOVA_SMTP_APP_PASSWORD must both be set.")
    if not to:
        raise SmtpError("no recipient")

    message = EmailMessage()
    message["From"] = f"{settings.smtp_from_name()} <{user}>"
    message["To"] = ", ".join(to)
    if cc:
        message["Cc"] = ", ".join(cc)
    message["Subject"] = subject
    message.set_content(body)

    # Finding 7: counts and lengths only -- never an address, subject or body.
    logger.info(
        "Sending mail: to=%d cc=%d subject=%s body=%s",
        len(to),
        len(cc or []),
        redact.text(subject),
        redact.text(body),
    )
    try:
        with smtp_factory() as server:
            server.login(user, password)
            server.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        logger.warning("SMTP send failed: %s", redact.error(exc))
        # `from exc` is load-bearing: `is_transient` classifies on the cause,
        # not on this message (round 2, finding 9).
        raise SmtpError("the mail could not be sent") from exc
    return {"ok": True, "to": list(to), "cc": list(cc or []), "subject": subject}
