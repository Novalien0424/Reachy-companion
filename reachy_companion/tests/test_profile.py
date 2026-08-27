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
    # Ported HomeAssistant-Nova capabilities (D-018). Each porting task adds its
    # own names here in the same order the profile lists them, so this stays a
    # tripwire for an *unplanned* addition rather than a blocker for a planned one.
    "play_music",
    "stop_music",
    "play_video",
    "show_on_tv",
    "calendar_add",
    "calendar_list",
    "calendar_delete",
    "task_add",
    "task_list",
    "task_complete",
    "task_delete",
    "notion_add",
    "drive_list",
    "drive_trash",
    "drive_upload",
    "email_send",
    "self_destruct",
    "mad_laugh",
    "nas_video_query",
    "play_nas_video",
    "nas_play_folder",
    "nas_skip",
    "go_to_sleep",
    "party_mode",
    "remember",
    "forget",
    "remember_face",
    "who_is_this",
    "wait_for_user",
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


def test_locked_profile_can_remember_and_recall_a_face() -> None:
    """Face memory ships as tool + instruction together, like every round before it (D-013).

    Both halves are load-bearing: without the enrollment line nobody is ever
    stored, and without the recall line the model answers "我是谁?" from the
    conversation instead of looking. The "认不出就坦率说认不出" clause is the
    honesty rule that keeps an `unknown` status from becoming a guessed name,
    and the "不要用 camera" clause is what keeps an identity question off the
    camera tool — the routing the party session got wrong.
    """
    profile = read_profile(LOCKED_PROFILE)

    assert "remember_face" in profile.default_tools
    assert "who_is_this" in profile.default_tools
    assert (
        '当用户说"记住我"、"我叫X，记住我的样子"时，用 remember_face 工具记录他的名字和长相，不要用 camera。'
        in profile.instructions
    )
    assert (
        '只要问题是关于"这个人是谁"——"我是谁"、"你认得我吗"、"你还记得我吗"、"我叫什么名字"、'
        "有人新走进来想知道是谁——一律用 who_is_this 工具，不要用 camera；认不出就坦率说认不出，不要猜。"
    ) in profile.instructions


def test_a_remembered_fact_reaches_the_locked_profile_session_instructions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The round trip that makes memory worth shipping, under LOCKED_PROFILE.

    `remember` writes to `<instance_path>/memory.v1.json`; the next session's
    instructions are built by `prompts.get_session_instructions`, which prepends
    the store's contents *before* the profile body.

    The Chinese assertion on `baseline` is load-bearing, not decoration: when
    `read_profile` fails, `get_session_instructions` silently falls back to the
    packaged **English** default profile (`prompts.py:40-45`), and every other
    assertion here — memory header, fact text, `endswith` — passes just as
    happily against that fallback. Without this line the test would prove the
    injection path works for *some* profile, which is not the claim.
    """
    monkeypatch.setattr("reachy_companion.config.config.REACHY_MINI_CUSTOM_PROFILE", LOCKED_PROFILE)

    baseline = get_session_instructions(tmp_path)
    assert "中文" in baseline  # it really is the locked profile, not the English fallback
    assert "Things you remember about the user" not in baseline

    add_memory_fact(tmp_path, "Prefers to be called 小明")

    injected = get_session_instructions(tmp_path)
    assert (tmp_path / "memory.v1.json").is_file()
    assert "Things you remember about the user" in injected
    assert "Prefers to be called 小明" in injected
    # Prepended, not substituted: the persona survives intact underneath.
    assert injected.endswith(baseline)
