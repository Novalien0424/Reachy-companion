"""Contract tests for the locked Chinese-first companion profile."""

import re
import json
import importlib
from pathlib import Path

import pytest

from reachy_companion.config import LOCKED_PROFILE, DEFAULT_PROFILES_DIRECTORY
from reachy_companion.memory import add_memory_fact
from reachy_companion.prompts import get_session_instructions
from reachy_companion.tools.tv import Tv
from reachy_companion.tools.nas import Nas
from reachy_companion.tools.drive import Drive
from reachy_companion.tools.music import Music
from reachy_companion.tools.tasks import Tasks
from reachy_companion.profile_store import read_profile
from reachy_companion.tools.calendar import Calendar


EXPECTED_TOOLS = (
    "camera",
    # 2026-08-31 conversation modes: the move_head → camera composite the mini
    # model would not chain for itself. Listed right after `camera` because the
    # two are read together — one looks, the other turns and then looks.
    "look_around",
    "play_emotion",
    "dance",
    "stop_dance",
    "stop_emotion",
    "move_head",
    "head_tracking",
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
    # 2026-08-31 conversation modes: `sweep_look` is subsumed by `look_around`
    # (one directional look the model actually reaches for, instead of a fixed
    # left-right-centre sweep it never called); the two gags gave their slots
    # back to the diet.
    "sweep_look", "self_destruct", "mad_laugh",
)

FAMILY_NAMES = ("music", "tv", "nas", "calendar", "tasks", "drive")

# Every family's live action enum, read off the shipped classes rather than
# restated here: a family that renames an action must not be able to leave a
# stale value behind in the prompt.
_FAMILY_ACTIONS = {
    family.name: frozenset(family.parameters_schema["properties"]["action"]["enum"])
    for family in (Calendar, Tasks, Drive, Nas, Music, Tv)
}

_ACTION_VALUE = re.compile(r"action=([a-z_]+(?:/[a-z_]+)*)")
_FAMILY_MENTION = re.compile(r"(?<![0-9A-Za-z_])(" + "|".join(sorted(_FAMILY_ACTIONS)) + r")(?![0-9A-Za-z_])")


def _family_before(text: str, index: int) -> str | None:
    """Return the family named nearest before *index*, or None.

    This is how a reader — and the model — resolves 「用 nas：先 action=query 找」:
    the family is stated once and the actions that follow belong to it.
    """
    owner = None
    for match in _FAMILY_MENTION.finditer(text):
        if match.end() > index:
            break
        owner = match.group(1)
    return owner


def _resolved_action_values(text: str) -> list[tuple[str | None, str, str]]:
    """Return (family, action, whole match) for every `action=…` value in *text*."""
    resolved = []
    for match in _ACTION_VALUE.finditer(text):
        owner = _family_before(text, match.start())
        for action in match.group(1).split("/"):
            resolved.append((owner, action, match.group(0)))
    return resolved


def _tool_name_surface(text: str) -> str:
    """Blank out every `action=…` value that is REAL, and only those.

    `tv（action=play_video）` names the live `tv` tool and passes it an argument;
    that argument happens to spell a name this list retired, and an argument is
    not an instruction to call a function that no longer exists. But blanking
    every `action=…` unconditionally would turn the escape hatch into a hiding
    place — `music（action=play_music）` is a stale call dressed as an argument.
    So a value is blanked only when its family really has that action; anything
    else stays visible and faces the scan (review round 3, minor 3).
    """

    def _blank(match: re.Match[str]) -> str:
        owner = _family_before(text, match.start())
        values = match.group(1).split("/")
        if owner is not None and all(value in _FAMILY_ACTIONS[owner] for value in values):
            return "action="
        return match.group(0)

    return _ACTION_VALUE.sub(_blank, text)


def _spec_surface(spec: dict) -> str:
    """Render one tool spec as scannable JSON, blanking only its OWN action names.

    Keyed on the spec's own name, not on nearest-mention: inside a family schema
    the surrounding prose names sibling families ("that is music"), so proximity
    would resolve the wrong owner.

    Inside the `tv` spec every bare `play_video` — the enum value, the "Pick
    `action`" sentence, the action property's own description — is that family's
    action, so the whole token is blanked there and nowhere else. That is the one
    place this scan cannot tell an action from the tool it was named after, and
    it is unavoidable: the reviewed design named the action `play_video`. Every
    other spec, and the other seventeen retired names, are scanned in full.
    """
    blob = json.dumps(spec, ensure_ascii=False)
    own_actions = _FAMILY_ACTIONS.get(spec["name"], frozenset())
    if not own_actions:
        return blob
    # Token-bounded, so `drive_list` is NOT blanked by the `list` action and
    # `play_music` is NOT blanked by the `play` action.
    pattern = "|".join(sorted(own_actions, key=len, reverse=True))
    return re.sub(rf"(?<![0-9A-Za-z_])(?:{pattern})(?![0-9A-Za-z_])", "<action>", blob)


def _bundled_profile_files() -> list[Path]:
    """Every profile this package ships, not just the locked one."""
    return sorted(Path(DEFAULT_PROFILES_DIRECTORY).glob("*/profile.md"))


def test_no_retired_tool_name_survives_in_any_bundled_profile() -> None:
    """The instruction body is a prompt too: a name it uses must exist.

    Conflicting instructions between the prompt and the registered tool schemas
    measurably degrade selection (research doc §A2), the front matter is only
    half the file, and the locked profile is only one of fifteen — `default`
    shipped `sweep_look` in its own tool list until the composite retired it
    (Codex round 2, 2b-7).
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


def test_no_retired_tool_name_reaches_the_model_in_a_tool_spec() -> None:
    """The schemas are a prompt too — and the widest of the three surfaces.

    A family's `parameters_schema` is a union of its delegates', so a property
    description still saying "from nas_video_query" ships that dead name to the
    model on every single turn while both prose tripwires above stay green.
    Scanning the full JSON of every spec covers names, descriptions and every
    nested property at once (review round 3, important 2).
    """
    core_tools = importlib.import_module("reachy_companion.tools.core_tools")
    core_tools.initialize_tools(force=True)
    try:
        specs = core_tools.get_tool_specs()
    finally:
        core_tools._TOOLS_SIGNATURE = None
    assert specs, "no tool specs were built"
    for spec in specs:
        blob = _spec_surface(spec)
        for name in _RETIRED_TOOL_NAMES:
            assert name not in blob, f"{spec['name']}: {name}"


def test_every_action_value_in_a_bundled_profile_is_real() -> None:
    """The blanking in `_tool_name_surface` must never become a hiding place.

    Checked independently of the retired-name list, so an action that is merely
    invented (`music（action=blast）`) fails here too, not only one that happens
    to spell a name we retired.
    """
    for path in _bundled_profile_files():
        for owner, action, context in _resolved_action_values(path.read_text(encoding="utf-8")):
            assert owner is not None, f"{path.name}: {context} names no family before it"
            assert action in _FAMILY_ACTIONS[owner], f"{path.name}: {owner} has no action {action!r} ({context})"


def test_the_locked_profile_body_names_every_family_it_ships() -> None:
    """The other half of P2-6: the body must teach the names that DO exist."""
    body = read_profile(LOCKED_PROFILE).instructions
    for family in FAMILY_NAMES:
        assert family in body, family


def test_the_confirm_retry_tells_the_model_to_resend_its_action() -> None:
    """A confirm retry without `action` would strand the armed claim until TTL.

    The gated tools are family actions now: the confirming call carries only
    `confirm`, so if the model drops `action` too the façade answers "action
    must be one of …" and never reaches the delegate that would have spent the
    authorisation (review round 3, important 1).
    """
    body = read_profile(LOCKED_PROFILE).instructions
    confirm_rule = next(line for line in body.splitlines() if "needs_confirmation" in line)
    assert "同样的 action" in confirm_rule, confirm_rule
