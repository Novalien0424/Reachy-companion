"""Contract tests for the locked Chinese-first companion profile."""

from reachy_companion.config import LOCKED_PROFILE
from reachy_companion.profile_store import read_profile


def test_locked_profile_is_chinese_companion() -> None:
    """The locked profile should speak Chinese and ship the demo tool set."""
    profile = read_profile(LOCKED_PROFILE)

    assert "中文" in profile.instructions
    for tool in ("camera", "play_emotion", "head_tracking"):
        assert tool in profile.default_tools
