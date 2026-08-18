"""Contract tests for the locked Chinese-first companion profile."""

from pathlib import Path

import pytest

from reachy_companion.config import LOCKED_PROFILE
from reachy_companion.memory import add_memory_fact
from reachy_companion.prompts import get_session_instructions
from reachy_companion.profile_store import read_profile


EXPECTED_TOOLS = (
    "camera",
    "play_emotion",
    "dance",
    "stop_dance",
    "stop_emotion",
    "move_head",
    "head_tracking",
    "sweep_look",
    "home_control",
    "go_to_sleep",
    "remember",
    "forget",
    "pollen_robotics_reachy_mini_search_tool__search_web",
)


def test_locked_profile_is_chinese_companion() -> None:
    """The locked profile should speak Chinese and ship the demo tool set."""
    profile = read_profile(LOCKED_PROFILE)

    assert "中文" in profile.instructions
    for tool in ("camera", "play_emotion", "head_tracking", "home_control", "go_to_sleep"):
        assert tool in profile.default_tools


def test_locked_profile_ships_exactly_the_expected_tools() -> None:
    """The shipped tool list is the demo's contract; a silent addition is a regression.

    Two system tools (`task_status`, `task_cancel`) are appended at registry
    build time (`tools/core_tools.py:_read_profile_tool_names`), so the session
    the operator sees advertises these plus those two.
    """
    assert tuple(read_profile(LOCKED_PROFILE).default_tools) == EXPECTED_TOOLS


def test_locked_profile_can_be_told_to_go_to_sleep() -> None:
    """Voice-commanded sleep needs both halves: the tool enabled and the prompt rule.

    `go_to_sleep` stops the app as well as posing the robot, so the model must
    only reach for it on an explicit request — the instruction line is what
    keeps it off idle turns and sleepy small talk.
    """
    profile = read_profile(LOCKED_PROFILE)

    assert "go_to_sleep" in profile.default_tools
    assert "当用户明确说想让你睡觉、休息或结束对话时，用 go_to_sleep 工具；先简短道别再调用。" in profile.instructions


def test_locked_profile_no_longer_compensates_for_a_tempo_side_effect() -> None:
    """D-011 made the pitch shift duration-preserving, so the "slow down" line had to go.

    Round 1's filter sped speech up by 26 %, and the prompt was the only
    counterweight available. Now that WSOLA holds the duration, that line would
    make Reachy speak *slower* than intended — so its absence is the contract,
    and the replacement is a plain delivery note.
    """
    instructions = read_profile(LOCKED_PROFILE).instructions

    assert "语速放慢" not in instructions
    assert "你的声音会被加速" not in instructions
    assert "吐字清楚、语气轻快。" in instructions


def test_locked_profile_can_remember_and_correct_facts_about_the_user() -> None:
    """Persistent memory needs both halves: the tools enabled and the prompt rule.

    Without the instruction line the model has the tools but no occasion to use
    them, and nothing is ever written to the store — which is exactly how the
    upstream tools sat dormant before this profile enabled them.
    """
    profile = read_profile(LOCKED_PROFILE)

    assert "remember" in profile.default_tools
    assert "forget" in profile.default_tools
    assert (
        "用户告诉你关于他们自己的重要信息（名字、喜好、习惯）时，用 remember 记下来；说错了就用 forget 修正。"
        in profile.instructions
    )


def test_a_remembered_fact_reaches_the_locked_profile_session_instructions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The round trip that makes memory worth shipping, under LOCKED_PROFILE.

    `remember` writes to `<instance_path>/memory.v1.json`; the next session's
    instructions are built by `prompts.get_session_instructions`, which prepends
    the store's contents *before* the profile body. This proves the injection
    path is live for the locked profile specifically — the profile is resolved
    by `config.REACHY_MINI_CUSTOM_PROFILE`, which the lock pins, so a profile
    lookup regression would show up here as missing Chinese instructions.
    """
    monkeypatch.setattr("reachy_companion.config.config.REACHY_MINI_CUSTOM_PROFILE", LOCKED_PROFILE)

    baseline = get_session_instructions(tmp_path)
    assert "Things you remember about the user" not in baseline

    add_memory_fact(tmp_path, "Prefers to be called 小明")

    injected = get_session_instructions(tmp_path)
    assert (tmp_path / "memory.v1.json").is_file()
    assert "Things you remember about the user" in injected
    assert "Prefers to be called 小明" in injected
    # Prepended, not substituted: the persona survives intact underneath.
    assert injected.endswith(baseline)
