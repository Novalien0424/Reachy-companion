"""Contract tests for the no-op wait_for_user tool."""

import asyncio
from unittest.mock import MagicMock

from reachy_companion.config import LOCKED_PROFILE
from reachy_companion.profile_store import read_profile
from reachy_companion.tools.wait_for_user import WaitForUser


def test_wait_for_user_is_silent_noop():
    """The tool takes no parameters and returns a fixed waiting acknowledgement."""
    tool = WaitForUser()
    assert tool.name == "wait_for_user"
    assert tool.needs_response is False
    assert tool.parameters_schema["properties"] == {}
    result = asyncio.run(tool(MagicMock()))
    assert result == {"ok": True, "status": "waiting"}


def test_wait_for_user_is_in_the_locked_profile():
    """wait_for_user must ship as one of the locked profile's default tools."""
    assert "wait_for_user" in read_profile(LOCKED_PROFILE).default_tools
