"""Tests for configuration helpers."""

import inspect

import pytest

from reachy_companion import config


@pytest.mark.parametrize(
    "raw_value, expected",
    [
        ("45", 45.0),
        ("", config.DEFAULT_APP_TIMEOUT_MINUTES),  # unset/blank falls back to the default
        ("soon", config.DEFAULT_APP_TIMEOUT_MINUTES),  # unparseable falls back to the default
        ("0", None),  # non-positive disables the watchdog
        ("-1", None),
    ],
)
def test_resolve_app_timeout_minutes(monkeypatch, raw_value, expected) -> None:
    """The env timeout parses to minutes, falls back to the default, or disables on non-positive."""
    monkeypatch.setenv(config.APP_TIMEOUT_MINUTES_ENV, raw_value)

    assert config.resolve_app_timeout_minutes() == expected


def test_transcription_language_defaults_to_zh(monkeypatch) -> None:
    """With no env override the realtime transcription language is Chinese (D-003).

    Regression guard for the default flipped in Task 5. Uses the runtime refresh
    hook rather than `importlib.reload`: reloading the module would rebind
    `config.config` to a new object while every other module keeps a reference to
    the old one, which silently breaks unrelated tests.
    """
    previous_language = config.config.REALTIME_TRANSCRIPTION_LANGUAGE
    monkeypatch.delenv(config.REALTIME_TRANSCRIPTION_LANGUAGE_ENV, raising=False)
    try:
        config.refresh_runtime_config_from_env()
        assert config.config.REALTIME_TRANSCRIPTION_LANGUAGE == "zh"
    finally:
        config.config.REALTIME_TRANSCRIPTION_LANGUAGE = previous_language


def test_main_builds_openai_handler_and_enables_tracking() -> None:
    """`main` wires the OpenAI backend and turns head tracking on without a tool call (US-02)."""
    import reachy_companion.main as main

    src = inspect.getsource(main)
    assert "OpenAIRealtimeHandler" in src
    assert "HuggingFaceRealtimeHandler(" not in src
    assert "set_head_tracking" in src or "start_head_tracking" in src
