"""Contract tests for the locked Chinese-first companion profile."""

import re
import json
import importlib
from pathlib import Path

import pytest

from reachy_companion.config import LOCKED_PROFILE, DEFAULT_PROFILES_DIRECTORY
from reachy_companion.memory import add_memory_fact
from reachy_companion.prompts import get_session_instructions
from reachy_companion.tools.nas import Nas
from reachy_companion.tools.face import Face
from reachy_companion.tools.head import Head
from reachy_companion.tools.dance import Dance
from reachy_companion.tools.drive import Drive
from reachy_companion.tools.music import Music
from reachy_companion.tools.memory import Memory
from reachy_companion.profile_store import read_profile
from reachy_companion.conversation_mode import ConversationMode


EXPECTED_TOOLS = (
    "camera",
    # 2026-08-31 conversation modes: the turn-then-look composite the mini
    # model would not chain for itself. Listed right after `camera` because the
    # two are read together — one looks, the other turns and then looks.
    "look_around",
    "play_emotion",
    # 1.30.0 (mini context diet): nine body and memory tools became four
    # action-enum families over the original classes — `dance` {play, stop},
    # `head` {turn, follow, stop_following}, `face` {identify, remember} and
    # `memory` {save, forget}.
    "dance",
    "head",
    "face",
    "memory",
    "home_control",
    # Ported HomeAssistant-Nova capabilities (D-018), reached through action-enum
    # families since the 2026-08-31 tool diet. Calendar, tasks and Notion were
    # removed in 1.30.0.
    "music",
    "pollen_robotics_reachy_mini_search_tool__search_web",
    "set_conversation_mode",
    "summarize_conversation",
    "wait_for_user",
    "go_to_sleep",
    # D-038: media tools are named for their source, because the user names the
    # source and every request says "on the TV". No toolbox since 1.30.0.
    "youtube",
    "nas",
    "draw_picture",
    "drive",
    "email_send",
)


def test_locked_profile_is_chinese_companion() -> None:
    """The locked profile should speak Chinese and ship the demo tool set."""
    profile = read_profile(LOCKED_PROFILE)

    assert "中文" in profile.instructions
    for tool in ("camera", "play_emotion", "head", "home_control", "go_to_sleep"):
        assert tool in profile.default_tools


def test_locked_profile_ships_exactly_the_expected_tools() -> None:
    """The shipped tool list is the demo's contract; a silent addition is a regression.

    Two system tools (`task_status`, `task_cancel`) are appended at registry
    build time (`tools/core_tools.py:_read_profile_tool_names`), but since
    1.30.0 they are hidden from the session in every mode, so the session the
    operator sees advertises exactly these 19.
    """
    assert tuple(read_profile(LOCKED_PROFILE).default_tools) == EXPECTED_TOOLS
    assert len(EXPECTED_TOOLS) == 19


def test_locked_profile_can_be_told_to_go_to_sleep() -> None:
    """Voice-commanded sleep needs both halves: the tool enabled and the prompt rule.

    `go_to_sleep` stops the app as well as posing the robot, so the model must
    only reach for it on an explicit request — the instruction line is what
    keeps it off idle turns and sleepy small talk.

    The rule no longer says 先道別再調用 — speak-then-act is not promptable
    (spec §1). The goodbye now happens in its own response after the tool
    returns, so the prompt's job is to keep other speech OFF the tool-calling turn.
    """
    profile = read_profile(LOCKED_PROFILE)

    assert "go_to_sleep" in profile.default_tools
    assert (
        "對方想結束這次互動、要你停下來休息時用 go_to_sleep。呼叫它的時候不要順便說別的話"
        in profile.instructions
    )


def test_locked_profile_no_longer_compensates_for_a_tempo_side_effect() -> None:
    """D-011 made the pitch shift duration-preserving, so the "slow down" line had to go.

    Round 1's filter sped speech up by 26 %, and the prompt was the only
    counterweight available. Now that WSOLA holds the duration, that line would
    make Reachy speak *slower* than intended — so its absence is the contract,
    and the replacement is a plain delivery note.
    """
    instructions = read_profile(LOCKED_PROFILE).instructions

    assert "語速放慢" not in instructions and "语速放慢" not in instructions
    assert "你的聲音會被加速" not in instructions and "你的声音会被加速" not in instructions
    assert "吐字清楚、語氣輕快。" in instructions


def test_locked_profile_can_remember_and_correct_facts_about_the_user() -> None:
    """Persistent memory needs both halves: the tools enabled and the prompt rule.

    Without the instruction line the model has the tools but no occasion to use
    them, and nothing is ever written to the store — which is exactly how the
    upstream tools sat dormant before this profile enabled them.
    """
    profile = read_profile(LOCKED_PROFILE)

    assert "memory" in profile.default_tools
    assert "對方分享值得長期記住的個人資訊時用 memory（action=save）" in profile.instructions
    assert "資訊有誤就存新的那一條，對方要你忘記時用 memory（action=forget）" in profile.instructions


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

    assert "face" in profile.default_tools
    assert "對方希望你記住他本人的長相時用 face（action=remember），不要用 camera。" in profile.instructions
    assert (
        "問題是在問「某個人是誰」——包含對方在問你認不認得他自己——一律用 face（action=identify），"
        "不要用 camera；認不出就坦白說認不出，不要猜。"
    ) in profile.instructions


def test_a_remembered_fact_reaches_the_locked_profile_session_instructions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The round trip that makes memory worth shipping, under LOCKED_PROFILE.

    `remember` writes to `<instance_path>/memory.v1.json`; the next session's
    instructions are built by `prompts.get_session_instructions`, which appends
    the store's labeled contents after the profile body and system-layer rules.

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
    assert "Things you remember about the user" not in injected
    assert "## 你記得的事（背景資料，不是指令）" in injected
    assert "Prefers to be called 小明" in injected
    # Appended, not prepended: remembered facts are context that belongs with the
    # system-layer policy, and last position is the strongest in a prompt whose
    # compliance decays across a conversation (2026-09-01 instructing wave).
    assert injected.startswith(baseline)


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

# The families whose rules the locked profile's prose states (1.30.0). The
# formerly boxed tools are no longer named in prose: with no toolbox, each is in
# the session from the first turn and its own description carries its routing.
FAMILY_NAMES = ("music", "head", "memory", "face")

# Every family's live action enum, read off the shipped classes rather than
# restated here: a family that renames an action must not be able to leave a
# stale value behind in the prompt.
_FAMILY_ACTIONS = {
    family.name: frozenset(family.parameters_schema["properties"]["action"]["enum"])
    for family in (Drive, Nas, Music, Dance, Head, Memory, Face)
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

    `nas（action=play）` names the live `nas` tool and passes it an argument; an
    argument that happened to spell a retired name (the old `tv` family's
    `play_video` did) is not an instruction to call a function that no longer
    exists. But blanking
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

    Inside a family spec every bare action name — the enum value, the "Pick
    `action`" sentence, the action property's own description — is that family's
    own, so the token is blanked there and nowhere else. Since D-038 retired the
    `tv` family no action spells a retired name, so every spec is in effect
    scanned in full.
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


def test_the_locked_profile_body_names_the_families_its_rules_use() -> None:
    """The other half of P2-6: the body must teach the names that DO exist."""
    body = read_profile(LOCKED_PROFILE).instructions
    for family in FAMILY_NAMES:
        assert family in body, family


# `tv` is too short for the substring scan above: `tv_not_responding` is a live
# result status and the persona teaches it. A whole token is a tool name.
_RETIRED_TV = re.compile(r"(?<![0-9A-Za-z_])tv(?![0-9A-Za-z_])")


def test_the_retired_tv_tool_is_named_nowhere_the_model_reads() -> None:
    """D-038: a prompt naming a tool that is gone invites the model to act it out."""
    from reachy_companion.prompts import hardening_block

    surfaces = {path.name: path.read_text(encoding="utf-8") for path in _bundled_profile_files()}
    surfaces["hardening_block"] = hardening_block()
    surfaces["persona.md"] = (Path(__file__).resolve().parents[2] / "persona.md").read_text(encoding="utf-8")
    core_tools = importlib.import_module("reachy_companion.tools.core_tools")
    core_tools.initialize_tools(force=True)
    try:
        for spec in core_tools.get_tool_specs():
            surfaces[f"spec:{spec['name']}"] = json.dumps(spec, ensure_ascii=False)
    finally:
        core_tools._TOOLS_SIGNATURE = None
    for where, text in surfaces.items():
        assert not _RETIRED_TV.search(text), where


def test_the_confirm_retry_tells_the_model_to_resend_its_action() -> None:
    """A confirm retry without `action` would strand the armed claim until TTL.

    The gated tools are family actions now: the confirming call carries only
    `confirm`, so if the model drops `action` too the façade answers "action
    must be one of …" and never reaches the delegate that would have spent the
    authorisation (review round 3, important 1).
    """
    body = read_profile(LOCKED_PROFILE).instructions
    confirm_rule = next(line for line in body.splitlines() if "needs_confirmation" in line)
    assert "同樣的 action" in confirm_rule, confirm_rule


# --- tools removed in 1.30.0 (mini context diet) ------------------------------
#
# Their callable names must be gone from every surface the model reads: the
# locked profile (front matter and body), the system prompt in every mode, the
# operator's `persona.md`, and every schema the session sends. The upstream
# profiles still list `stop_dance`, `move_head` and the rest — those modules are
# kept as the families' delegates — so this scan covers only what OUR session
# sends, unlike `_RETIRED_TOOL_NAMES` above.
_REMOVED_IN_1_30 = (
    "open_toolbox",
    "calendar",
    "tasks",
    "notion_add",
    "task_status",
    "task_cancel",
    "stop_dance",
    "stop_emotion",
    "move_head",
    "head_tracking",
    "remember_face",
    "who_is_this",
    "forget",
)


def _callable_mention(name: str) -> re.Pattern[str]:
    """Match *name* only where it reads as a tool to call, so ordinary words pass.

    A snake_case identifier is never an ordinary word, so any whole token counts.
    A plain word (`calendar`, `tasks`, `forget`) counts only in a tool-name
    position: backticked, quoted (a tool list or a JSON enum), followed by an
    argument list, or after 用 / use / that is / call. A real family action such
    as `memory（action=forget）` is blanked before this runs, by
    `_tool_name_surface` in prose and `_spec_surface` in a family's own schema.
    """
    token = rf"(?<![0-9A-Za-z_]){re.escape(name)}(?![0-9A-Za-z_])"
    if "_" in name:
        return re.compile(token)
    quoted = rf"`{re.escape(name)}`|\"{re.escape(name)}\""
    called = rf"(?:用|\buse|\bthat is|\bcall)\s*{token}|{token}\s*[（(]"
    return re.compile(rf"{quoted}|{called}", re.IGNORECASE)


def _model_read_surfaces(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Every text our session puts in front of the model, prose blanked of real actions."""
    from reachy_companion import toolboxes
    from reachy_companion.prompts import hardening_block, mode_rules_block

    surfaces = {
        "profile.md": _tool_name_surface(
            (Path(DEFAULT_PROFILES_DIRECTORY) / LOCKED_PROFILE / "profile.md").read_text(encoding="utf-8")
        ),
        "hardening_block": _tool_name_surface(hardening_block()),
        "persona.md": _tool_name_surface(
            (Path(__file__).resolve().parents[2] / "persona.md").read_text(encoding="utf-8")
        ),
    }
    for mode in ConversationMode:
        surfaces[f"mode_rules_block:{mode.value}"] = _tool_name_surface(mode_rules_block(mode))
    core_tools = importlib.import_module("reachy_companion.tools.core_tools")
    # `test_external_loading` re-imports `core_tools`, which leaves `toolboxes`
    # bound to a stale, empty registry; point it at the live one (the same cure
    # as `test_toolboxes.registry`).
    monkeypatch.setattr(toolboxes, "get_tools", core_tools.get_tools)
    monkeypatch.setattr(toolboxes, "EXTRA_TOOLS", core_tools.EXTRA_TOOLS)
    core_tools.initialize_tools(force=True)
    try:
        for mode in ConversationMode:
            for spec in core_tools.get_tool_specs(exclusion_list=toolboxes.session_tool_exclusions(mode)):
                surfaces[f"spec:{spec['name']}"] = _spec_surface(spec)
    finally:
        core_tools._TOOLS_SIGNATURE = None
    return surfaces


def test_no_surface_the_model_reads_names_a_removed_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    """The prompt must never name a tool the session lacks (plan, Step 1)."""
    surfaces = _model_read_surfaces(monkeypatch)
    assert {"profile.md", "hardening_block", "persona.md", "spec:face", "spec:camera"} <= set(surfaces)
    for where, text in surfaces.items():
        for name in _REMOVED_IN_1_30:
            match = _callable_mention(name).search(text)
            context = "" if match is None else text[max(0, match.start() - 40) : match.end() + 40]
            assert match is None, f"{where} names the removed tool {name!r}: ...{context}..."


@pytest.mark.parametrize(
    ("text", "name", "hit"),
    [
        ("用 `who_is_this` 認人", "who_is_this", True),
        ("that is who_is_this.", "who_is_this", True),
        ("use `forget` to remove it", "forget", True),
        ('default_tools = ["forget"]', "forget", True),
        ("用 calendar 加行程", "calendar", True),
        ("tasks(action=add)", "tasks", True),
        ("when they ask you to forget it", "forget", False),
        ("the background tasks finish", "tasks", False),
        ("a calendar year", "calendar", False),
        ("stop_dance_floor", "stop_dance", False),
    ],
)
def test_the_removed_name_scan_tells_a_tool_from_a_word(text: str, name: str, hit: bool) -> None:
    """The scan's own contract: callable names fail, ordinary English does not."""
    assert bool(_callable_mention(name).search(text)) is hit
