"""Contract tests for the locked Chinese-first companion profile."""

import re
from pathlib import Path

import pytest

from reachy_companion.config import LOCKED_PROFILE, DEFAULT_PROFILES_DIRECTORY
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
    #
    # 2026-08-31 tool diet: the eighteen CRUD/action tools became six action-enum
    # families. Their modules, names and prerequisite rows are untouched -- they
    # are simply reached through a façade now, so only this list got shorter.
    "music",
    "tv",
    "nas",
    "calendar",
    "tasks",
    "drive",
    "notion_add",
    "email_send",
    "self_destruct",
    "mad_laugh",
    "go_to_sleep",
    "set_conversation_mode",
    "summarize_conversation",
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


# --- retired tool names (2026-08-31 tool diet) -------------------------------

_RETIRED_TOOL_NAMES = (
    "calendar_add", "calendar_list", "calendar_delete",
    "task_add", "task_list", "task_complete", "task_delete",
    "drive_list", "drive_trash", "drive_upload",
    "nas_video_query", "play_nas_video", "nas_play_folder", "nas_skip",
    "play_music", "stop_music", "play_video", "show_on_tv",
    "party_mode",
)

# `tv（action=play_video）` names the live `tv` tool and one of its action
# values; that value happens to spell a name this list retired, and an argument
# is not an instruction to call a function that no longer exists. Blank the
# values out before scanning, so the tripwire keeps catching the thing it is
# for -- a prompt still telling the model to call `play_video` itself.
_ACTION_VALUE = re.compile(r"action=[a-z_]+(?:/[a-z_]+)*")


def _tool_name_surface(text: str) -> str:
    """Return *text* with every `action=…` value blanked out."""
    return _ACTION_VALUE.sub("action=", text)


def _bundled_profile_files() -> list[Path]:
    """Every profile this package ships, not just the locked one."""
    return sorted(Path(DEFAULT_PROFILES_DIRECTORY).glob("*/profile.md"))


def test_no_retired_tool_name_survives_in_any_bundled_profile() -> None:
    """The instruction body is a prompt too: a name it uses must exist.

    Conflicting instructions between the prompt and the registered tool schemas
    measurably degrade selection (research doc §A2), the front matter is only
    half the file, and the locked profile is only one of fifteen — `default`
    ships `sweep_look` in its own tool list (Codex round 2, 2b-7).
    """
    files = _bundled_profile_files()
    assert files, "no bundled profiles found"
    for path in files:
        text = _tool_name_surface(path.read_text(encoding="utf-8"))
        for name in _RETIRED_TOOL_NAMES:
            assert name not in text, f"{path.name}: {name}"


def test_the_hardening_block_names_no_retired_tool() -> None:
    """The shared prompt block is sent with every profile, so it counts too."""
    from reachy_companion.prompts import hardening_block

    block = _tool_name_surface(hardening_block())
    for name in _RETIRED_TOOL_NAMES:
        assert name not in block, name


def test_the_locked_profile_body_names_every_family_it_ships() -> None:
    """The other half of P2-6: the body must teach the names that DO exist."""
    body = read_profile(LOCKED_PROFILE).instructions
    for family in ("music", "tv", "nas", "calendar", "tasks", "drive"):
        assert family in body, family
