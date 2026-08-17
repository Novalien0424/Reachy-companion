"""Contract tests for the locked Chinese-first companion profile."""

from reachy_companion.config import LOCKED_PROFILE
from reachy_companion.profile_store import read_profile


def test_locked_profile_is_chinese_companion() -> None:
    """The locked profile should speak Chinese and ship the demo tool set."""
    profile = read_profile(LOCKED_PROFILE)

    assert "中文" in profile.instructions
    for tool in ("camera", "play_emotion", "head_tracking", "home_control", "go_to_sleep"):
        assert tool in profile.default_tools


def test_locked_profile_can_be_told_to_go_to_sleep() -> None:
    """Voice-commanded sleep needs both halves: the tool enabled and the prompt rule.

    `go_to_sleep` stops the app as well as posing the robot, so the model must
    only reach for it on an explicit request — the instruction line is what
    keeps it off idle turns and sleepy small talk.
    """
    profile = read_profile(LOCKED_PROFILE)

    assert "go_to_sleep" in profile.default_tools
    assert "当用户明确说想让你睡觉、休息或结束对话时，用 go_to_sleep 工具；先简短道别再调用。" in profile.instructions


def test_locked_profile_compensates_for_the_voice_filter() -> None:
    """The cute-robot filter pitches up by resampling, which also speeds speech up (D-010).

    The only available counterweight is the prompt, so the style line asking
    Reachy to slow down is part of the filter's contract, not decoration.
    """
    instructions = read_profile(LOCKED_PROFILE).instructions

    assert "语速放慢一点，吐字清楚（你的声音会被加速，说慢一点正好）。" in instructions
