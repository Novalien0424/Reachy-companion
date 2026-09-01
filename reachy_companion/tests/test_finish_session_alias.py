"""The sleep-tool rename is an A/B behind a flag, never an edit to the real name."""

from __future__ import annotations
from unittest.mock import MagicMock
from collections.abc import Generator

import pytest

from reachy_companion.tools import core_tools
from reachy_companion.tools.go_to_sleep import GoToSleep
from reachy_companion.finish_session_alias import (
    ALIAS_ENV,
    FinishSession,
    register_finish_session_alias,
)


@pytest.fixture(autouse=True)
def _clean_registry() -> Generator[None, None, None]:
    core_tools.EXTRA_TOOLS.pop("finish_session", None)
    yield
    core_tools.EXTRA_TOOLS.pop("finish_session", None)


def test_the_alias_is_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unmeasured rename must not reach the model by accident."""
    monkeypatch.delenv(ALIAS_ENV, raising=False)

    assert register_finish_session_alias() is False
    assert "finish_session" not in core_tools.EXTRA_TOOLS


def test_the_flag_exposes_the_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    """The opt-in flag adds the alias to the persistent extra-tool registry."""
    monkeypatch.setenv(ALIAS_ENV, "1")

    assert register_finish_session_alias() is True
    assert "finish_session" in core_tools.EXTRA_TOOLS


def test_the_alias_is_the_same_tool_under_a_second_name() -> None:
    """Same implementation, same session-ending contract; only the name differs."""
    assert issubclass(FinishSession, GoToSleep)
    assert FinishSession.name == "finish_session"
    assert FinishSession.ends_session is True
    assert FinishSession.needs_response is False
    assert FinishSession().description == GoToSleep().description


def test_the_alias_never_joins_the_module_scan() -> None:
    """Exposure is the flag's decision alone."""
    assert FinishSession._auto_register is False


@pytest.mark.asyncio
async def test_the_alias_behaves_exactly_like_go_to_sleep() -> None:
    """The alias silences first and returns the inherited sleeping-soon cue."""
    calls: list[str] = []
    deps = core_tools.ToolDependencies(
        reachy_mini=MagicMock(),
        movement_manager=MagicMock(),
        begin_sleep=lambda: calls.append("silence"),
        go_to_sleep=lambda: {"status": "sleeping"},
    )

    assert (await FinishSession()(deps))["status"] == "sleeping_soon"
    assert calls == ["silence"]
