"""go_to_sleep silences the inputs and hands the turn back; it never poses."""

from __future__ import annotations
from unittest.mock import MagicMock

import pytest

from reachy_companion.tools.core_tools import ToolDependencies
from reachy_companion.tools.go_to_sleep import GoToSleep


def test_go_to_sleep_has_no_required_arguments() -> None:
    """An empty-object schema: nothing to guess, nothing to get wrong."""
    assert GoToSleep().parameters_schema == {
        "type": "object",
        "properties": {},
        "required": [],
    }


def test_go_to_sleep_declares_itself_session_ending() -> None:
    """The dispatcher's branch keys off the class, so an alias inherits it free."""
    assert GoToSleep.ends_session is True
    assert GoToSleep.needs_response is False


@pytest.mark.asyncio
async def test_go_to_sleep_reports_an_unwired_runtime() -> None:
    """No finalizer means no sleep will ever happen — promising one would be a lie."""
    deps = ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())

    assert await GoToSleep()(deps) == {"error": "go_to_sleep is unavailable in this runtime"}


@pytest.mark.asyncio
async def test_go_to_sleep_silences_and_returns_the_farewell_cue() -> None:
    """The whole voice path here: mute, then hand the turn back with the facts."""
    calls: list[str] = []
    deps = ToolDependencies(
        reachy_mini=MagicMock(),
        movement_manager=MagicMock(),
        begin_sleep=lambda: calls.append("silence"),
        go_to_sleep=lambda: calls.append("sleep") or {"status": "sleeping"},
        current_person="雲霓",
    )

    result = await GoToSleep()(deps)

    assert calls == ["silence"], "the tool must not pose; the dispatcher does that after the goodbye"
    assert result["status"] == "sleeping_soon"
    assert result["farewell_context"] == {
        "reason": "user_asked_to_end_the_interaction",
        "listening_stopped": True,
        "person": "雲霓",
    }


@pytest.mark.asyncio
async def test_go_to_sleep_still_hands_the_turn_back_if_silencing_fails() -> None:
    """A failed quiesce is noisy, not fatal: the goodbye still gets its turn."""

    def _boom() -> None:
        raise RuntimeError("stream gone")

    deps = ToolDependencies(
        reachy_mini=MagicMock(),
        movement_manager=MagicMock(),
        begin_sleep=_boom,
        go_to_sleep=lambda: {"status": "sleeping"},
    )

    assert (await GoToSleep()(deps))["status"] == "sleeping_soon"


def test_the_description_forbids_extra_speech_and_declares_the_cue() -> None:
    """Session-ending tools say 'do not generate any other text' (skill: Tool design rules).

    And the cue must be defined HERE, on a higher-authority surface, because a
    return carries no policy of its own (2026 Model Spec: tool messages hold no
    authority).
    """
    description = GoToSleep().description
    assert "do not generate any other text" in description.lower()
    assert "sleeping_soon" in description
    assert "farewell_context" in description
    assert "Do NOT use when:" in description
