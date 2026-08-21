"""Contract tests for email_send (D-018, R2/R3/R5). Nothing is ever sent."""

import types
import importlib

import pytest

from reachy_companion.hanova import gmail_smtp
from reachy_companion.hanova.confirm import GATE
from reachy_companion.tools.email_send import EmailSend


def _deps():
    return types.SimpleNamespace(reachy_mini=None, instance_path=None)


class _FakeSmtp:
    """Records what would have been sent, and sends nothing."""

    def __init__(self) -> None:
        self.logged_in: tuple[str, str] | None = None
        self.messages: list = []

    def __enter__(self) -> "_FakeSmtp":
        return self

    def __exit__(self, *_args) -> bool:
        return False

    def login(self, user: str, password: str) -> None:
        self.logged_in = (user, password)

    def send_message(self, message) -> None:
        self.messages.append(message)


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    """Configure the email family, and open an empty confirmation gate."""
    monkeypatch.setenv("HANOVA_SMTP_USER", "sender@example.com")
    monkeypatch.setenv("HANOVA_SMTP_APP_PASSWORD", "app-password")
    monkeypatch.delenv("HANOVA_SMTP_FROM_NAME", raising=False)
    monkeypatch.delenv("HANOVA_CONFIRM_TTL_S", raising=False)
    GATE.reset()
    GATE.begin_session()
    yield
    GATE.reset()


def test_tool_name_matches_the_filename():
    """The loader resolves tools by filename == Tool.name."""
    assert EmailSend.name == "email_send"


def test_description_carries_no_personal_identifier():
    """R10: upstream put the real sender address in this description."""
    text = EmailSend().description
    assert "@" not in text
    assert len(text) <= 120


def test_send_mail_builds_the_message_and_logs_in(monkeypatch):
    """The account and app password come from config, never from a literal."""
    fake = _FakeSmtp()
    monkeypatch.setattr(gmail_smtp, "smtp_factory", lambda: fake)

    out = gmail_smtp.send_mail(["a@example.com"], "Dinner", "See you at seven.", cc=["b@example.com"])
    assert out["ok"] is True and out["cc"] == ["b@example.com"]
    assert fake.logged_in == ("sender@example.com", "app-password")
    message = fake.messages[0]
    assert message["To"] == "a@example.com"
    assert message["Cc"] == "b@example.com"
    assert message["Subject"] == "Dinner"
    assert "sender@example.com" in message["From"]
    assert message.get_content().strip() == "See you at seven."


def test_send_mail_has_no_bcc_parameter():
    """Finding 17: BCC is an approved non-goal, enforced by the signature."""
    import inspect

    assert "bcc" not in inspect.signature(gmail_smtp.send_mail).parameters


def test_send_mail_never_sets_a_bcc_header(monkeypatch):
    """Belt and braces: no code path may add a blind recipient."""
    fake = _FakeSmtp()
    monkeypatch.setattr(gmail_smtp, "smtp_factory", lambda: fake)
    gmail_smtp.send_mail(["a@example.com"], "Dinner", "hi", cc=["b@example.com"])
    assert fake.messages[0].get("Bcc") is None


def test_send_mail_without_credentials_raises(monkeypatch):
    """A missing app password is a configuration fact, surfaced as SmtpError."""
    monkeypatch.delenv("HANOVA_SMTP_APP_PASSWORD")
    with pytest.raises(gmail_smtp.SmtpError):
        gmail_smtp.send_mail(["a@example.com"], "Dinner", "hi")


def test_send_mail_surfaces_a_transport_failure_without_the_body(monkeypatch, caplog):
    """An SMTP failure becomes SmtpError -- carrying no message content."""
    import logging

    sentinel = "SENTINEL_PRIVATE_x7"

    class _Boom(_FakeSmtp):
        def send_message(self, message) -> None:
            raise OSError(f"connection reset while sending to {sentinel}")

    monkeypatch.setattr(gmail_smtp, "smtp_factory", lambda: _Boom())
    caplog.set_level(logging.DEBUG)
    with pytest.raises(gmail_smtp.SmtpError) as excinfo:
        gmail_smtp.send_mail([f"{sentinel}@example.com"], sentinel, sentinel)
    assert sentinel not in str(excinfo.value)
    assert sentinel not in caplog.text


# --- envelope normalisation and validation (review finding 5) -------------
def test_recipients_are_split_trimmed_and_deduplicated():
    """Voice input arrives as one comma-run with stray whitespace."""
    valid, rejected = gmail_smtp.normalize_recipients(" a@example.com , b@example.com;  A@Example.com ")
    assert valid == ["a@example.com", "b@example.com"]
    assert rejected == []


def test_unparseable_recipients_are_reported_not_dropped():
    """Finding 5: an address we cannot describe is one we must not send to."""
    valid, rejected = gmail_smtp.normalize_recipients("a@example.com, mum, at gmail")
    assert valid == ["a@example.com"]
    assert rejected == ["mum", "at gmail"]


def test_body_digest_is_stable_and_content_free():
    """An integrity token appended after the body -- never instead of it."""
    first = gmail_smtp.body_digest("See you at seven.")
    assert first == gmail_smtp.body_digest("See you at seven.")
    assert first != gmail_smtp.body_digest("See you at eight.")
    assert "See you" not in first


# --- the body is read back in full (round 2, finding 4) -------------------
def test_normalize_body_is_the_single_source_of_what_is_sent():
    """Summary and payload must be byte-identical, so there is one normaliser."""
    normalised = gmail_smtp.normalize_body("  line one  \r\n line two \r\n\r\n")
    assert normalised == "line one\n line two"
    assert gmail_smtp.normalize_body(normalised) == normalised


def test_the_body_length_cap_is_five_hundred_characters():
    """Round 2, finding 4: a body that cannot be read back cannot be confirmed."""
    assert gmail_smtp.MAX_BODY_CHARS == 500


@pytest.mark.asyncio
async def test_a_body_over_the_cap_is_refused_and_arms_nothing(monkeypatch):
    """Refusing is the honest answer; summarising it into a digest was not."""
    import reachy_companion.tools.email_send as email_send_module

    def fail_send(**kwargs):
        raise AssertionError("nothing may be sent")

    monkeypatch.setattr(email_send_module.gmail_smtp, "send_mail", fail_send)
    out = await EmailSend()(
        deps=_deps(),
        to="a@example.com",
        subject="Dinner",
        body="x" * (gmail_smtp.MAX_BODY_CHARS + 1),
    )
    assert out["status"] == "body_too_long"
    assert out["ok"] is False
    assert out["max_chars"] == gmail_smtp.MAX_BODY_CHARS
    assert GATE.claim("email_send") is None


@pytest.mark.asyncio
async def test_a_body_exactly_at_the_cap_is_accepted(monkeypatch):
    """The boundary is inclusive; an off-by-one here is a silent refusal."""
    body = "x" * gmail_smtp.MAX_BODY_CHARS
    out = await EmailSend()(deps=_deps(), to="a@example.com", subject="Dinner", body=body)
    assert out["status"] == "needs_confirmation"
    assert body in out["summary"]


@pytest.mark.asyncio
async def test_the_summary_contains_the_entire_body_not_a_preview(monkeypatch):
    """Round 2, finding 4: every line, not the first one capped at 120 chars."""
    body = "\n".join(f"line {n}: something specific" for n in range(1, 9))
    out = await EmailSend()(deps=_deps(), to="a@example.com", subject="Dinner", body=body)
    assert out["status"] == "needs_confirmation"
    for line in body.splitlines():
        assert line in out["summary"], line


@pytest.mark.asyncio
async def test_changing_text_after_the_first_line_changes_the_summary(monkeypatch):
    """Round 2, finding 4, mandatory case: **the changed tail**.

    This is exactly what the round-1 read-back could not express. Both bodies
    open with the same line, so a first-line preview plus a length plus an
    opaque digest gave the user two confirmations they could not tell apart by
    ear -- while the *sent* mail differed in the part that mattered.
    """
    first = await EmailSend()(
        deps=_deps(),
        to="a@example.com",
        subject="Dinner",
        body="See you at seven.\nBring the map.",
    )
    GATE.reset()
    GATE.begin_session()
    second = await EmailSend()(
        deps=_deps(),
        to="a@example.com",
        subject="Dinner",
        body="See you at seven.\nBring the cash instead.",
    )
    assert first["summary"] != second["summary"]
    assert "Bring the map." in first["summary"]
    assert "Bring the cash instead." in second["summary"]
    assert "Bring the cash instead." not in first["summary"]


@pytest.mark.asyncio
async def test_the_summarised_body_is_the_body_that_is_sent(monkeypatch):
    """One normalisation, so nothing can differ between read-back and send."""
    import reachy_companion.tools.email_send as email_send_module

    sent = {}

    def fake_send(**kwargs):
        sent.update(kwargs)
        return {"ok": True, "to": kwargs["to"], "cc": kwargs["cc"], "subject": kwargs["subject"]}

    monkeypatch.setattr(email_send_module.gmail_smtp, "send_mail", fake_send)
    armed = await EmailSend()(
        deps=_deps(),
        to="a@example.com",
        subject="Dinner",
        body="  See you at seven.  \r\n  Bring the map.  ",
    )
    await EmailSend()(deps=_deps(), confirm=True)
    assert sent["body"] in armed["summary"]
    assert sent["body"] == "See you at seven.\n  Bring the map."


@pytest.mark.asyncio
async def test_email_send_is_unavailable_without_config(monkeypatch):
    """R5: an unconfigured tool answers, it does not raise, and it names the key."""
    monkeypatch.delenv("HANOVA_SMTP_APP_PASSWORD")
    out = await EmailSend()(deps=_deps(), to="a@example.com", subject="Dinner", body="hi")
    assert out == {"status": "unavailable", "reason": "HANOVA_SMTP_APP_PASSWORD"}


@pytest.mark.asyncio
async def test_email_send_reads_back_the_whole_envelope(monkeypatch):
    """Finding 5 + round 2 finding 4: To, CC, subject and the **whole** body."""
    import reachy_companion.tools.email_send as email_send_module

    def fail_send(**kwargs):
        raise AssertionError("email_send must not send before confirmation")

    monkeypatch.setattr(email_send_module.gmail_smtp, "send_mail", fail_send)
    out = await EmailSend()(
        deps=_deps(),
        to="a@example.com",
        cc="b@example.com, c@example.com",
        subject="Dinner",
        body="See you at seven.\nBring the map.",
    )
    assert out["status"] == "needs_confirmation"
    summary = out["summary"]
    for token in (
        "a@example.com",
        "b@example.com",
        "c@example.com",
        "Dinner",
        "See you at seven.",
        "Bring the map.",  # round 2, finding 4: the tail, not just line one
    ):
        assert token in summary, token
    assert "digest" in summary  # still present, now as an appended token
    assert "no blind recipients" in summary.lower()


@pytest.mark.asyncio
async def test_no_recipient_can_hide_outside_the_summary(monkeypatch):
    """Finding 5, stated as the invariant: sent set == summarised set."""
    import reachy_companion.tools.email_send as email_send_module

    sent = {}

    def fake_send(**kwargs):
        sent.update(kwargs)
        return {"ok": True, "to": kwargs["to"], "cc": kwargs["cc"], "subject": kwargs["subject"]}

    monkeypatch.setattr(email_send_module.gmail_smtp, "send_mail", fake_send)
    armed = await EmailSend()(
        deps=_deps(),
        to="a@example.com, d@example.com",
        cc="b@example.com",
        subject="Dinner",
        body="See you at seven.",
    )
    await EmailSend()(deps=_deps(), confirm=True)

    every_recipient = set(sent["to"]) | set(sent["cc"])
    assert every_recipient == {"a@example.com", "d@example.com", "b@example.com"}
    for address in every_recipient:
        assert address in armed["summary"], f"{address} was sent to but never read back"


@pytest.mark.asyncio
async def test_a_cc_duplicating_the_to_is_collapsed(monkeypatch):
    """The read-back count must match what SMTP will actually do."""
    import reachy_companion.tools.email_send as email_send_module

    sent = {}
    monkeypatch.setattr(
        email_send_module.gmail_smtp,
        "send_mail",
        lambda **kwargs: sent.update(kwargs) or {"ok": True, "to": kwargs["to"], "cc": kwargs["cc"], "subject": ""},
    )
    await EmailSend()(deps=_deps(), to="a@example.com", cc="A@example.com", subject="Dinner", body="hi")
    await EmailSend()(deps=_deps(), confirm=True)
    assert sent["to"] == ["a@example.com"]
    assert sent["cc"] == []


@pytest.mark.asyncio
async def test_an_unparseable_recipient_arms_nothing(monkeypatch):
    """Finding 5: refusing is the only safe answer; dropping it silently is not."""
    import reachy_companion.tools.email_send as email_send_module

    def fail_send(**kwargs):
        raise AssertionError("nothing may be sent")

    monkeypatch.setattr(email_send_module.gmail_smtp, "send_mail", fail_send)
    out = await EmailSend()(deps=_deps(), to="a@example.com, mum", subject="Dinner", body="hi")
    assert out["ok"] is False and out["rejected_count"] == 1
    assert GATE.claim("email_send") is None


@pytest.mark.asyncio
async def test_email_send_sends_the_armed_payload(monkeypatch):
    """The confirmed send uses exactly what was read back."""
    import reachy_companion.tools.email_send as email_send_module

    sent = {}

    def fake_send(**kwargs):
        sent.update(kwargs)
        return {"ok": True, "to": kwargs["to"], "cc": kwargs["cc"], "subject": kwargs["subject"]}

    monkeypatch.setattr(email_send_module.gmail_smtp, "send_mail", fake_send)
    await EmailSend()(deps=_deps(), to="a@example.com", subject="Dinner", body="See you at seven.")
    out = await EmailSend()(deps=_deps(), to="wrong@example.com", subject="Wrong", body="wrong", confirm=True)
    assert out["ok"] is True and out["status"] == "sent"
    assert sent["to"] == ["a@example.com"]
    assert sent["subject"] == "Dinner"
    assert sent["body"] == "See you at seven."


@pytest.mark.asyncio
async def test_email_logs_never_carry_an_address_or_a_subject(monkeypatch, caplog):
    """Finding 7: the whole envelope is personal data."""
    import logging

    import reachy_companion.tools.email_send as email_send_module

    sentinel = "SENTINEL_PRIVATE_x7"
    monkeypatch.setattr(
        email_send_module.gmail_smtp,
        "send_mail",
        lambda **kwargs: {"ok": True, "to": kwargs["to"], "cc": kwargs["cc"], "subject": ""},
    )
    caplog.set_level(logging.DEBUG)
    await EmailSend()(deps=_deps(), to=f"{sentinel}@example.com", subject=sentinel, body=sentinel)
    await EmailSend()(deps=_deps(), confirm=True)
    assert sentinel not in caplog.text


@pytest.mark.asyncio
async def test_email_send_confirm_without_arm_is_refused(monkeypatch):
    """A confirm:true first call must send nothing."""
    import reachy_companion.tools.email_send as email_send_module

    def fail_send(**kwargs):
        raise AssertionError("email_send must not send without a pending action")

    monkeypatch.setattr(email_send_module.gmail_smtp, "send_mail", fail_send)
    out = await EmailSend()(deps=_deps(), to="a@example.com", subject="Dinner", body="hi", confirm=True)
    assert out["status"] == "confirmation_expired"


@pytest.mark.asyncio
async def test_email_send_rejects_a_name_only_recipient():
    """Refuse "send it to mum": a name is not an address, and guessing one is worse."""
    out = await EmailSend()(deps=_deps(), to="mum", subject="Dinner", body="hi")
    assert out["ok"] is False and "@" in out["error"]


@pytest.mark.asyncio
async def test_email_send_requires_subject_and_body():
    """An empty subject or body is almost always a mis-parse."""
    assert (await EmailSend()(deps=_deps(), to="a@example.com", subject="", body="hi"))["ok"] is False
    assert (await EmailSend()(deps=_deps(), to="a@example.com", subject="Dinner", body=" "))["ok"] is False


@pytest.mark.asyncio
async def test_email_send_reports_a_transport_failure_and_keeps_the_authorisation(monkeypatch):
    """Finding 4: a *transient* failed send must not force a second read-back."""
    import smtplib

    import reachy_companion.tools.email_send as email_send_module

    attempts = {"n": 0}

    def flaky(**kwargs):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise email_send_module.SmtpError("the mail could not be sent") from smtplib.SMTPServerDisconnected(
                "connection lost"
            )
        return {"ok": True, "to": kwargs["to"], "cc": kwargs["cc"], "subject": kwargs["subject"]}

    monkeypatch.setattr(email_send_module.gmail_smtp, "send_mail", flaky)
    await EmailSend()(deps=_deps(), to="a@example.com", subject="Dinner", body="hi")
    first = await EmailSend()(deps=_deps(), confirm=True)
    assert first["ok"] is False and first.get("retryable") is True
    second = await EmailSend()(deps=_deps(), confirm=True)
    assert second["ok"] is True and second["status"] == "sent"


# --- transient vs terminal (round 2, finding 9) ---------------------------
def test_smtp_failures_are_classified_not_all_transient():
    """Round 2, finding 9: an auth failure is not a "try again" situation."""
    import smtplib

    for terminal in (
        smtplib.SMTPAuthenticationError(535, b"bad credentials"),
        smtplib.SMTPRecipientsRefused({"a@example.com": (550, b"no such user")}),
        smtplib.SMTPSenderRefused(553, b"not allowed", "sender@example.com"),
        smtplib.SMTPDataError(554, b"message refused"),
        smtplib.SMTPNotSupportedError("no SSL"),
    ):
        assert gmail_smtp.is_transient(terminal) is False, type(terminal).__name__

    for transient in (
        smtplib.SMTPConnectError(421, b"try later"),
        smtplib.SMTPServerDisconnected("connection lost"),
        smtplib.SMTPHeloError(451, b"greeting failed"),
        TimeoutError("timed out"),
        OSError("network unreachable"),
    ):
        assert gmail_smtp.is_transient(transient) is True, type(transient).__name__


def test_the_classification_follows_the_wrapped_cause():
    """`send_mail` raises SmtpError; the class that decides is underneath it."""
    import smtplib

    wrapped = gmail_smtp.SmtpError("the mail could not be sent")
    wrapped.__cause__ = smtplib.SMTPAuthenticationError(535, b"bad credentials")
    assert gmail_smtp.is_transient(wrapped) is False

    retryable = gmail_smtp.SmtpError("the mail could not be sent")
    retryable.__cause__ = smtplib.SMTPServerDisconnected("connection lost")
    assert gmail_smtp.is_transient(retryable) is True


@pytest.mark.asyncio
async def test_a_terminal_failure_spends_the_authorisation(monkeypatch):
    """Round 2, finding 9: a refused recipient means the envelope is wrong.

    Keeping the approval alive would keep an approval for something that can
    never succeed as approved. The user has to hear a corrected envelope.
    """
    import smtplib

    import reachy_companion.tools.email_send as email_send_module

    def refused(**kwargs):
        raise email_send_module.SmtpError("the mail could not be sent") from smtplib.SMTPRecipientsRefused(
            {"a@example.com": (550, b"no such user")}
        )

    monkeypatch.setattr(email_send_module.gmail_smtp, "send_mail", refused)
    await EmailSend()(deps=_deps(), to="a@example.com", subject="Dinner", body="hi")
    out = await EmailSend()(deps=_deps(), confirm=True)
    assert out["ok"] is False
    assert out.get("retryable") is not True
    # Spent: a bare retry now finds nothing armed.
    again = await EmailSend()(deps=_deps(), confirm=True)
    assert again["status"] == "confirmation_expired"


@pytest.mark.asyncio
async def test_a_stale_confirm_cannot_spend_a_newly_armed_envelope(monkeypatch):
    """Round 2, finding 2, at the tool level: claim ids, not just tool names."""
    import reachy_companion.tools.email_send as email_send_module

    sent = []

    def record(**kwargs):
        sent.append(kwargs)
        return {"ok": True, "to": kwargs["to"], "cc": kwargs["cc"], "subject": kwargs["subject"]}

    monkeypatch.setattr(email_send_module.gmail_smtp, "send_mail", record)
    await EmailSend()(deps=_deps(), to="a@example.com", subject="First", body="one")
    stale = GATE.claim("email_send")
    assert stale is not None

    GATE.begin_session()
    await EmailSend()(deps=_deps(), to="b@example.com", subject="Second", body="two")

    # The old in-flight operation finishing must not touch the new approval.
    assert GATE.complete("email_send", stale.claim_id) is False
    out = await EmailSend()(deps=_deps(), confirm=True)
    assert out["ok"] is True
    assert sent[-1]["to"] == ["b@example.com"]


def test_email_send_reaches_the_model_session():
    """The locked profile must list it, or the model never sees it."""
    core_tools = importlib.import_module("reachy_companion.tools.core_tools")
    core_tools.initialize_tools(force=True)
    try:
        assert "email_send" in {spec["name"] for spec in core_tools.get_tool_specs()}
    finally:
        core_tools._TOOLS_SIGNATURE = None
