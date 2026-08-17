"""Contract tests for the shared environment parsers.

Every knob in this app degrades to its documented default with a warning rather
than raising, so one bad line in a robot's `.env` can never abort a session.
These tests pin that contract once, for every caller.
"""

import logging

import pytest

from reachy_companion.audio.envparse import env_int, env_bool, env_float


ENVPARSE_LOGGER = "reachy_companion.audio.envparse"


# --------------------------------------------------------------------------
# env_bool
# --------------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["true", "TRUE", "True", "1", "yes", "YES", "on", " true "])
def test_env_bool_accepts_truthy_spellings(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    """The spellings a human plausibly types in a .env must all mean True."""
    monkeypatch.setenv("VOICEFX_TEST_BOOL", raw)
    assert env_bool("VOICEFX_TEST_BOOL", False) is True


@pytest.mark.parametrize("raw", ["false", "FALSE", "0", "no", "off", " off "])
def test_env_bool_accepts_falsy_spellings(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    """The negative spellings must all mean False, even over a True default."""
    monkeypatch.setenv("VOICEFX_TEST_BOOL", raw)
    assert env_bool("VOICEFX_TEST_BOOL", True) is False


@pytest.mark.parametrize("raw", ["", "   "])
def test_env_bool_treats_unset_and_blank_as_default(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    """An empty value is "not configured", not "false" — and must not warn."""
    monkeypatch.setenv("VOICEFX_TEST_BOOL", raw)
    assert env_bool("VOICEFX_TEST_BOOL", True) is True
    monkeypatch.delenv("VOICEFX_TEST_BOOL")
    assert env_bool("VOICEFX_TEST_BOOL", True) is True


def test_env_bool_warns_and_defaults_on_garbage(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """An unrecognized value must warn and degrade, naming the offending knob."""
    monkeypatch.setenv("VOICEFX_TEST_BOOL", "maybe")

    with caplog.at_level(logging.WARNING, logger=ENVPARSE_LOGGER):
        assert env_bool("VOICEFX_TEST_BOOL", False) is False

    assert "VOICEFX_TEST_BOOL" in caplog.text
    assert "maybe" in caplog.text


# --------------------------------------------------------------------------
# env_float
# --------------------------------------------------------------------------


def test_env_float_reads_a_plain_number(monkeypatch: pytest.MonkeyPatch) -> None:
    """A well-formed value is returned as-is."""
    monkeypatch.setenv("VOICEFX_TEST_FLOAT", "0.72")
    assert env_float("VOICEFX_TEST_FLOAT", 0.5) == pytest.approx(0.72)


def test_env_float_defaults_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset and blank both mean "use the default"."""
    monkeypatch.delenv("VOICEFX_TEST_FLOAT", raising=False)
    assert env_float("VOICEFX_TEST_FLOAT", 0.5) == pytest.approx(0.5)
    monkeypatch.setenv("VOICEFX_TEST_FLOAT", "  ")
    assert env_float("VOICEFX_TEST_FLOAT", 0.5) == pytest.approx(0.5)


def test_env_float_warns_and_defaults_on_garbage(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A malformed number must warn and fall back."""
    monkeypatch.setenv("VOICEFX_TEST_FLOAT", "not-a-number")

    with caplog.at_level(logging.WARNING, logger=ENVPARSE_LOGGER):
        assert env_float("VOICEFX_TEST_FLOAT", 0.5) == pytest.approx(0.5)

    assert "VOICEFX_TEST_FLOAT" in caplog.text


@pytest.mark.parametrize("raw", ["nan", "NaN", "inf", "-inf", "Infinity"])
def test_env_float_rejects_non_finite_values(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, raw: str
) -> None:
    """`float()` happily parses nan/inf; a DSP knob must not accept them.

    A NaN semitone count would poison every sample downstream, so these are
    treated exactly like garbage: warn, use the default.
    """
    monkeypatch.setenv("VOICEFX_TEST_FLOAT", raw)

    with caplog.at_level(logging.WARNING, logger=ENVPARSE_LOGGER):
        got = env_float("VOICEFX_TEST_FLOAT", 4.0)

    assert got == pytest.approx(4.0)
    assert "VOICEFX_TEST_FLOAT" in caplog.text


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("1.5", 1.0), ("-0.4", 0.0), ("1.0", 1.0), ("0.0", 0.0), ("0.25", 0.25)],
)
def test_env_float_clamps_into_range(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, raw: str, expected: float
) -> None:
    """Out-of-range values clamp to the nearest bound; in-range values pass through."""
    monkeypatch.setenv("VOICEFX_TEST_FLOAT", raw)

    with caplog.at_level(logging.WARNING, logger=ENVPARSE_LOGGER):
        got = env_float("VOICEFX_TEST_FLOAT", 0.25, lo=0.0, hi=1.0)

    assert got == pytest.approx(expected)
    clamped = float(raw) != expected
    assert ("VOICEFX_TEST_FLOAT" in caplog.text) is clamped


def test_env_float_clamps_against_a_single_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    """`lo` and `hi` are independently optional."""
    monkeypatch.setenv("VOICEFX_TEST_FLOAT", "-3")
    assert env_float("VOICEFX_TEST_FLOAT", 0.0, lo=0.0) == pytest.approx(0.0)
    monkeypatch.setenv("VOICEFX_TEST_FLOAT", "9000")
    assert env_float("VOICEFX_TEST_FLOAT", 0.0, hi=12.0) == pytest.approx(12.0)


# --------------------------------------------------------------------------
# env_int
# --------------------------------------------------------------------------


def test_env_int_reads_and_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """A well-formed int is returned; unset falls back to the default."""
    monkeypatch.setenv("VOICEFX_TEST_INT", "1200")
    assert env_int("VOICEFX_TEST_INT", 800) == 1200
    monkeypatch.delenv("VOICEFX_TEST_INT")
    assert env_int("VOICEFX_TEST_INT", 800) == 800


def test_env_int_warns_and_defaults_on_garbage(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A malformed int must warn and fall back, never raise."""
    monkeypatch.setenv("VOICEFX_TEST_INT", "12.5")

    with caplog.at_level(logging.WARNING, logger=ENVPARSE_LOGGER):
        assert env_int("VOICEFX_TEST_INT", 300) == 300

    assert "VOICEFX_TEST_INT" in caplog.text


def test_env_int_clamps_into_range(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """Range clamping works the same way it does for floats."""
    monkeypatch.setenv("VOICEFX_TEST_INT", "99999")

    with caplog.at_level(logging.WARNING, logger=ENVPARSE_LOGGER):
        assert env_int("VOICEFX_TEST_INT", 300, lo=0, hi=5000) == 5000

    assert "VOICEFX_TEST_INT" in caplog.text


# --------------------------------------------------------------------------
# The move out of openai_realtime must be behaviour-preserving
# --------------------------------------------------------------------------


def test_openai_realtime_uses_the_shared_parsers() -> None:
    """The VAD knobs must be parsed by this module, not by a private copy."""
    from reachy_companion import openai_realtime

    assert openai_realtime.env_float is env_float
    assert openai_realtime.env_int is env_int
    assert not hasattr(openai_realtime, "_env_float")
    assert not hasattr(openai_realtime, "_env_int")
