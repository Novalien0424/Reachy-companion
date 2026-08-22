"""The one redaction helper every ported tool logs and errors through (finding 7).

Every sentinel here is **synthetic**. A test that hunts for private identifiers
must never contain a real one -- that would put the identifier into the tracked
repository, which is the thing the test exists to prevent (finding 6).
"""

import errno
import logging

import pytest

from reachy_companion.hanova import redact


SENTINEL = "SENTINEL_PRIVATE_x7"


class _HttpError(Exception):
    """Shaped like the exceptions httpx and the Google/Notion layers raise."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


def test_count_reports_a_number_never_the_items():
    """A list of results is a count, not a list of titles."""
    assert redact.count(["a", "b", SENTINEL]) == "<3 items>"
    assert redact.count([]) == "<empty>"
    assert SENTINEL not in redact.count([SENTINEL])


def test_text_reports_a_length_never_the_text():
    """Subjects, queries and note bodies are lengths in a log line."""
    rendered = redact.text(f"dinner with {SENTINEL}")
    assert rendered.startswith("<text:") and rendered.endswith("chars>")
    assert SENTINEL not in rendered
    assert redact.text("") == "<empty>"
    assert redact.text(None) == "<none>"


def test_ident_is_a_stable_digest_never_the_identifier():
    """Two log lines about the same file must correlate without naming it."""
    first = redact.ident(SENTINEL)
    second = redact.ident(SENTINEL)
    assert first == second
    assert first.startswith("<id:") and len(first) == len("<id:") + 8 + 1
    assert SENTINEL not in first
    assert redact.ident(SENTINEL) != redact.ident(SENTINEL + "2")


def test_error_takes_the_status_from_the_exception_never_from_the_text():
    """Round 3, finding 3: structure comes from attributes, not from tokenizing.

    An HTTP status is useful; the response body is someone's data. The status
    below is on the object, and the body says 404 too -- only the attribute may
    be the reason it appears.
    """
    rendered = redact.error(_HttpError(f"404 Not Found: {SENTINEL}", status_code=404))
    assert rendered == "_HttpError(404)"
    assert SENTINEL not in rendered


def test_error_never_keeps_a_token_lifted_out_of_the_raw_message():
    """Round 3, finding 3: the `E[A-Z]+` rule passed shouty identifiers through.

    Every message here contains something the old regex would have kept -- an
    all-caps `E...` token, a bare status, an errno *spelled in the text* rather
    than set on the object. None of them may survive, because none of them came
    from an attribute this helper can trust.
    """
    for message in (
        f"EVENTID {SENTINEL} could not be deleted",
        f"404 Not Found: {SENTINEL}",
        f"{SENTINEL} ECONNREFUSED",
    ):
        rendered = redact.error(RuntimeError(message))
        assert rendered == "RuntimeError", rendered
        for leaked in (SENTINEL, "EVENTID", "404", "ECONNREFUSED"):
            assert leaked not in rendered


def test_error_reports_an_allow_listed_errno_read_off_the_exception():
    """A transport shape is worth keeping, and it cannot carry content."""
    rendered = redact.error(OSError(errno.ECONNREFUSED, f"refused by {SENTINEL}"))
    assert "ECONNREFUSED" in rendered
    assert SENTINEL not in rendered


def test_error_on_a_bare_string_reveals_nothing_at_all():
    """Tools sometimes surface a message they built themselves.

    Round 3, finding 3: a string carries no attributes, so there is nothing to
    trust in it and the rendering is the bare word.
    """
    assert redact.error(f"could not reach {SENTINEL}") == "error"
    assert SENTINEL not in redact.error(f"could not reach {SENTINEL}")


def test_safe_log_fields_is_a_closed_whitelist():
    """A field not on this list may not be logged verbatim by any ported tool."""
    assert "status" in redact.SAFE_LOG_FIELDS
    assert "count" in redact.SAFE_LOG_FIELDS
    for forbidden in ("title", "query", "subject", "to", "path", "url", "file_id"):
        assert forbidden not in redact.SAFE_LOG_FIELDS


def test_redaction_survives_a_non_string_input():
    """Never raise inside a log call; that would break the thing being logged."""
    assert redact.text(object()) == "<text:unprintable>"  # type: ignore[arg-type]
    assert redact.ident(None) == "<none>"


@pytest.mark.parametrize("level", [logging.DEBUG, logging.INFO, logging.WARNING])
def test_helper_output_is_safe_at_every_level(caplog, level):
    """Caplog sentinel guard: nothing the helper emits carries the sentinel."""
    caplog.set_level(logging.DEBUG)
    logger = logging.getLogger("reachy_companion.hanova.redact.selftest")
    logger.log(level, "probe %s %s %s", redact.text(SENTINEL), redact.ident(SENTINEL), redact.count([SENTINEL]))
    assert SENTINEL not in caplog.text
