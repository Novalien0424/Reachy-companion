"""Contract tests for the blocking-HTTP seam every sync service module calls (D-018).

The seam's whole contract is that one request has exactly two outcomes: a
`(status, body)` tuple, or an exception from the `OSError` family. Six modules
(Google Calendar, Tasks, Drive, Notion, Gmail and whatever follows) inherit that
contract through their `except (..., OSError, ...)` clauses and, for the gated
tools, through `gauth.is_transient`. Anything the seam lets escape un-normalized
breaks all of them at once, which is why this is tested here rather than in each
caller.
"""

import http.client
import urllib.error
import urllib.request

import pytest

from reachy_companion.hanova import gauth, sync_http


HEADERS = {"Accept": "application/json"}
URL = "https://api.example.invalid/v3/events"


def _raise(exc: BaseException):
    def boom(*args, **kwargs):
        raise exc

    return boom


def test_an_http_error_status_is_data_not_an_exception(monkeypatch):
    """A 4xx is a status the caller decides about, not a failure of the seam."""
    error = urllib.error.HTTPError(URL, 404, "Not Found", None, None)
    monkeypatch.setattr(urllib.request, "urlopen", _raise(error))
    assert sync_http.request_bytes("GET", URL, HEADERS) == (404, b"{}")


def test_a_flaky_link_is_normalised_into_the_oserror_family(monkeypatch):
    """Round 3 review: `IncompleteRead` is an HTTPException, and NOT an OSError.

    Every caller's `except (GoogleApiError, OSError, ValueError, KeyError)`
    assumes the seam already turned a transport fault into one of those. Left
    un-normalized, a truncated response would escape `calendar_add` as a raw
    exception instead of `{"ok": False}` -- and would make `calendar_delete`
    spend the user's confirmation through its unexpected-failure fallback, on
    precisely the kind of fault the release/complete split exists to retry.
    """
    monkeypatch.setattr(urllib.request, "urlopen", _raise(http.client.IncompleteRead(b"half a body")))
    with pytest.raises(OSError) as excinfo:
        sync_http.request_bytes("GET", URL, HEADERS)
    assert isinstance(excinfo.value.__cause__, http.client.IncompleteRead)


def test_a_normalised_transport_fault_classifies_as_transient(monkeypatch):
    """The point of the normalisation: the gated tool keeps the authorisation.

    `is_transient` answers True for the whole `OSError` family, so converting at
    the seam is what puts a flaky link on the retry path instead of the
    spend-the-confirmation path.
    """
    monkeypatch.setattr(urllib.request, "urlopen", _raise(http.client.IncompleteRead(b"")))
    with pytest.raises(OSError) as excinfo:
        sync_http.request_bytes("GET", URL, HEADERS)
    assert gauth.is_transient(excinfo.value) is True


def test_every_httpexception_shape_is_normalised(monkeypatch):
    """One subclass passing proves nothing; the seam catches the base class."""
    for raised in (
        http.client.IncompleteRead(b""),
        http.client.BadStatusLine("not a status line"),
        http.client.LineTooLong("header line"),
    ):
        monkeypatch.setattr(urllib.request, "urlopen", _raise(raised))
        with pytest.raises(OSError):
            sync_http.request_bytes("GET", URL, HEADERS)


def test_the_normalised_message_carries_no_response_content(monkeypatch):
    """Finding 7: a status line is written by the far end and is never echoed."""
    monkeypatch.setattr(urllib.request, "urlopen", _raise(http.client.BadStatusLine("SENTINEL_PRIVATE_x7")))
    with pytest.raises(OSError) as excinfo:
        sync_http.request_bytes("GET", URL, HEADERS)
    assert "SENTINEL_PRIVATE_x7" not in str(excinfo.value)
