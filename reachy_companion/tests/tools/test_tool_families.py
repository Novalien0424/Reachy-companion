"""Action-enum tool families: 18 registered tools became 6, then the body ones became 4 more.

41 tools at the start of a turn is past OpenAI's own "aim for fewer than 20"
and inside the measured degradation zone
(docs/research-mini-tool-calling-2026-08.md §A1). The consolidation is a SCHEMA
refactor: every family façade delegates to the original tool instance, so the
confirmation gates, prerequisite checks and error strings are unchanged and
still covered by those modules' own tests.

1.30.0 (mini context diet): calendar and tasks are gone, and nine body and
memory tools became the `dance`, `head`, `memory` and `face` families over the
original upstream-derived classes.
"""

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from reachy_companion.hanova import settings
from reachy_companion.tools.nas import Nas
from reachy_companion.tools.face import Face
from reachy_companion.tools.head import Head
from reachy_companion.tools.dance import Dance
from reachy_companion.tools.drive import Drive
from reachy_companion.tools.music import Music
from reachy_companion.tools.forget import Forget
from reachy_companion.tools.memory import Memory
from reachy_companion.tools.youtube import Youtube
from reachy_companion.tools.remember import Remember
from reachy_companion.tools.move_head import MoveHead
from reachy_companion.tools.drive_list import DriveList
from reachy_companion.tools.play_music import PlayMusic
from reachy_companion.tools.play_video import PlayVideo
from reachy_companion.tools.show_on_tv import ShowOnTv
from reachy_companion.tools.stop_dance import StopDance
from reachy_companion.tools.dance_moves import Dance as DanceMoves
from reachy_companion.tools.drive_trash import DriveTrash
from reachy_companion.tools.who_is_this import WhoIsThis
from reachy_companion.tools.draw_picture import DrawPicture
from reachy_companion.tools.drive_upload import DriveUpload
from reachy_companion.tools.stop_emotion import StopEmotion
from reachy_companion.tools.head_tracking import HeadTracking
from reachy_companion.tools.remember_face import RememberFace


# The service families, each over hanova-ported delegates.
_SERVICE_FAMILIES = (Drive, Nas, Music)
# 1.30.0: the body and memory families, each over upstream-derived delegates.
_BODY_FAMILIES = (Dance, Head, Memory, Face)
_FAMILIES = _SERVICE_FAMILIES + _BODY_FAMILIES
# D-038: one-purpose tools named for their source, each over one original module.
_SINGLES = (Youtube, DrawPicture)


def _deps() -> SimpleNamespace:
    return SimpleNamespace(reachy_mini=MagicMock(), movement_manager=MagicMock(), instance_path=None)


@pytest.mark.parametrize("family", _FAMILIES)
def test_every_family_has_a_required_action_enum(family) -> None:
    """`action` is the one field the façade owns, so it must be required and complete."""
    schema = family().parameters_schema
    assert schema["required"] == ["action"]
    actions = schema["properties"]["action"]["enum"]
    # Unique, in the order the description teaches them (`head` lists `turn`
    # first because it is the everyday action), not necessarily sorted.
    assert actions and len(actions) == len(set(actions))
    # Every advertised action must have a delegate behind it.
    assert set(actions) == set(family.ACTIONS)


@pytest.mark.parametrize("family", _SERVICE_FAMILIES + (Dance,))
def test_every_family_description_is_symmetric(family) -> None:
    """A family that only says what it is for gets called for its neighbours too.

    `head`, `memory` and `face` carry the operator's 1.30.0 text, which states
    its routing as positive rules (「Use it, not camera」) instead of the
    labelled pair, so they are checked by their routing tests rather than here.
    """
    assert "Use when:" in family.description
    assert "Do NOT use when:" in family.description


@pytest.mark.parametrize("family", _FAMILIES)
def test_family_schema_covers_every_delegate_property(family) -> None:
    """A union that dropped a property would silently break that action."""
    properties = set(family().parameters_schema["properties"])
    for tool in family.ACTIONS.values():
        assert set(tool.parameters_schema["properties"]) <= properties


@pytest.mark.asyncio
async def test_family_rejects_an_unknown_action() -> None:
    """An action with no delegate is the one thing the façade must answer itself."""
    result = await Drive()(_deps(), action="explode")
    assert "error" in result and "action must be one of" in result["error"]


@pytest.mark.asyncio
async def test_the_delegate_still_validates_its_own_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    """The façade adds no argument check of its own (Codex round 1, P2-5).

    `drive_trash` answers a missing `file_id` with its own sentence, and it only
    gets that far after its `settings.tool_status` prerequisite check. A
    façade-level check would run first, change the wording, and mask "this is
    not configured" behind "you forgot an argument".
    """
    monkeypatch.setattr(settings, "tool_status", lambda name: (True, ""))
    result = await Drive()(_deps(), action="trash")
    assert result == {"ok": False, "error": "file_id is required"}


@pytest.mark.asyncio
async def test_the_prerequisite_refusal_wins_over_a_missing_argument(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Original check order preserved: unavailable is answered before args are."""
    monkeypatch.setattr(settings, "tool_status", lambda name: (False, "MUSIC_WHEELS"))
    result = await Music()(_deps(), action="play")
    assert result == settings.unavailable("MUSIC_WHEELS")


@pytest.mark.asyncio
async def test_family_forwards_to_the_original_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    """Delegation, not reimplementation: the sub-tool's own body still runs."""
    seen: list[dict[str, object]] = []

    async def _upload(self, deps, **kwargs):
        seen.append(kwargs)
        return {"status": "needs_confirmation"}

    monkeypatch.setattr(DriveUpload, "__call__", _upload)
    result = await Drive()(_deps(), action="upload", name="午餐.jpg")
    assert result == {"status": "needs_confirmation"}
    assert seen == [{"name": "午餐.jpg"}]


@pytest.mark.asyncio
async def test_family_forwards_a_no_argument_action(monkeypatch: pytest.MonkeyPatch) -> None:
    """`action` is consumed by the dispatch and never reaches the delegate."""

    async def _list(self, deps, **kwargs):
        return {"files": [], "seen": kwargs}

    monkeypatch.setattr(DriveList, "__call__", _list)
    result = await Drive()(_deps(), action="list")
    assert result["seen"] == {}


@pytest.mark.asyncio
async def test_family_preserves_the_delegate_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A prerequisite refusal must reach the model exactly as before."""

    async def _play(self, deps, **kwargs):
        return {"error": "play_music unavailable: MUSIC_WHEELS"}

    monkeypatch.setattr(PlayMusic, "__call__", _play)
    result = await Music()(_deps(), action="play", query="周杰倫")
    assert result == {"error": "play_music unavailable: MUSIC_WHEELS"}


def test_the_sub_tools_are_no_longer_registered() -> None:
    """The whole point of the diets: sub-tool names leave the session, families arrive."""
    from reachy_companion.tools.core_tools import get_tools

    registered = set(get_tools())
    for name in (
        "drive_list", "drive_trash", "drive_upload",
        "nas_video_query", "play_nas_video", "nas_skip",
        "play_music", "stop_music", "play_video", "show_on_tv",
        # 1.30.0: the family delegates and the family-internal helpers.
        "stop_dance", "stop_emotion", "move_head", "head_tracking",
        "remember", "forget", "who_is_this", "remember_face", "dance_stop",
        # 1.30.0: removed outright.
        "calendar", "tasks", "notion_add", "open_toolbox",
    ):
        assert name not in registered, name
    for name in ("drive", "nas", "music", "youtube", "draw_picture", "dance", "head", "memory", "face"):
        assert name in registered, name
    assert "tv" not in registered  # retired by D-038
    # `dance` is the family, not upstream's move player it delegates to. By
    # name, not identity: `test_external_loading` re-imports every tool module.
    dance = type(get_tools()["dance"])
    assert (dance.__module__, dance.__qualname__) == ("reachy_companion.tools.dance", "Dance")


# --- delegation fidelity: the two answers, in their original order -----------
#
# The façade is only allowed to pick a delegate. These pin BOTH halves of the
# behavior the brief's Global Constraint protects: with the prerequisite
# missing, every family answers `settings.unavailable(reason)` even though the
# action-specific argument is absent too; with the prerequisite satisfied, the
# delegate's own sentence comes through byte for byte.

_UNAVAILABLE_CASES = (
    (Drive, "list"),
    (Drive, "trash"),
    (Drive, "upload"),
    (Nas, "list"),
    (Nas, "play"),
    (Nas, "skip"),
    (Music, "play"),
)


@pytest.mark.asyncio
@pytest.mark.parametrize("family,action", _UNAVAILABLE_CASES)
async def test_unavailable_is_answered_before_the_missing_argument(
    family: Any, action: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A robot that is not configured could not have done the thing either way.

    Every one of these calls omits the action's required argument as well, so a
    façade-level `required` table would answer "argument missing" and hide the
    real reason. `settings.tool_status` runs first in every delegate and must
    stay first through the façade (Codex round 1, P2-5).
    """
    monkeypatch.setattr(settings, "tool_status", lambda name: (False, "SOME_PREREQ"))
    result = await family()(_deps(), action=action)
    assert result == settings.unavailable("SOME_PREREQ")


_DELEGATE_ERRORS = (
    (Drive, "trash", {}, {"ok": False, "error": "file_id is required"}),
    (Music, "play", {}, {"ok": False, "error": "query is required"}),
    # 1.30.0 body families: D-029 decision 7, the argument check is the delegate's.
    (Head, "turn", {}, {"error": "direction must be one of left, right, up, down, front"}),
    (Head, "turn", {"direction": "sideways"}, {"error": "direction must be one of left, right, up, down, front"}),
    (Memory, "save", {}, {"error": "fact must be a non-empty string"}),
    (Memory, "forget", {"query": "  "}, {"error": "query must be a non-empty string"}),
    (Face, "remember", {}, {"error": "name must be a non-empty string"}),
)


@pytest.mark.asyncio
@pytest.mark.parametrize("family,action,kwargs,expected", _DELEGATE_ERRORS)
async def test_the_delegate_owns_the_argument_error_text(
    family: Any, action: str, kwargs: dict[str, Any], expected: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Once the prerequisite passes, the delegate's own wording reaches the model."""
    monkeypatch.setattr(settings, "tool_status", lambda name: (True, ""))
    result = await family()(_deps(), action=action, **kwargs)
    assert result == expected


@pytest.mark.asyncio
async def test_the_confirmation_flag_reaches_the_gated_delegate(monkeypatch: pytest.MonkeyPatch) -> None:
    """`confirm` is the spoken gate's only key; the façade must not eat it."""
    seen: list[dict[str, object]] = []

    async def _trash(self, deps, **kwargs):
        seen.append(kwargs)
        return {"ok": True, "status": "trashed"}

    monkeypatch.setattr(DriveTrash, "__call__", _trash)
    result = await Drive()(_deps(), action="trash", confirm=True)
    assert result == {"ok": True, "status": "trashed"}
    assert seen == [{"confirm": True}]


@pytest.mark.asyncio
async def test_a_non_string_action_is_refused_not_crashed() -> None:
    """The model can emit anything; `action` is the one field the façade owns."""
    result = await Drive()(_deps(), action=None)
    assert "action must be one of" in result["error"]


def test_no_service_family_suppresses_the_spoken_follow_up() -> None:
    """None of the service sub-tools sets `needs_response = False`, so no service family may either."""
    for family in _SERVICE_FAMILIES + (Memory, Face):
        assert family.needs_response is True
        for tool in family.ACTIONS.values():
            assert tool.needs_response is True


@pytest.mark.parametrize("family", _FAMILIES)
def test_every_family_carries_its_delegates_tool_flags(family) -> None:
    """The handler reads the REGISTERED tool's flags, and the family is what is registered.

    So a family's `needs_response` / `ends_session` must be the value every one
    of its delegates has: `dance` and `head` stay silent after a move, exactly
    as `dance`, `stop_dance`, `stop_emotion`, `move_head` and `head_tracking`
    did on their own. A family whose delegates disagreed could not be honest
    for all of its actions, so the delegates must agree too. A `BoundAction` is
    read through to the tool it binds, because that is the tool doing the work.
    """
    delegates = [getattr(tool, "delegate", tool) for tool in family.ACTIONS.values()]
    flags = {(tool.needs_response, tool.ends_session) for tool in delegates}
    assert flags == {(family.needs_response, family.ends_session)}, family.name
    assert family.ends_session is False


def test_the_body_families_keep_upstreams_silent_moves() -> None:
    """Upstream's motion tools all opt out of the spoken follow-up; the families keep that."""
    assert Dance.needs_response is False and Head.needs_response is False
    for delegate in (DanceMoves, StopDance, StopEmotion, MoveHead, HeadTracking):
        assert delegate.needs_response is False, delegate.name


# --- the one-purpose media tools (D-038) -------------------------------------


@pytest.mark.parametrize("single,delegate", [(Youtube, PlayVideo), (DrawPicture, ShowOnTv)])
def test_each_single_tool_delegates_to_its_original_module(single: Any, delegate: Any) -> None:
    """The work, the prerequisite row and the error strings stay the original module's."""
    assert isinstance(single.DELEGATE, delegate)
    assert single.needs_response is True and single.DELEGATE.needs_response is True


@pytest.mark.asyncio
@pytest.mark.parametrize("single", _SINGLES)
async def test_a_single_tool_answers_unavailable_before_the_missing_argument(
    single: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same order the families keep: the delegate's prerequisite check comes first."""
    seen: list[str] = []

    def status(name: str) -> tuple[bool, str]:
        seen.append(name)
        return False, "SOME_PREREQ"

    monkeypatch.setattr(settings, "tool_status", status)
    assert await single()(_deps()) == settings.unavailable("SOME_PREREQ")
    assert seen == [single.DELEGATE.name]


@pytest.mark.asyncio
async def test_a_single_tool_forwards_its_arguments_and_drops_only_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """`str(None)` would be a search phrase, so a null argument must not reach the delegate."""
    seen: list[dict[str, Any]] = []

    async def _play(self, deps, **kwargs):
        seen.append(kwargs)
        return {"ok": True, "status": "casting"}

    monkeypatch.setattr(PlayVideo, "__call__", _play)
    assert await Youtube()(_deps(), query="a cat video", extra=None) == {"ok": True, "status": "casting"}
    assert seen == [{"query": "a cat video"}]


def _ask_first_clause(description: str) -> str:
    start = description.index("Ask first when:")
    return description[start : description.index("Do NOT use when:", start)]


def test_youtube_and_nas_ask_first_with_the_same_words() -> None:
    """D-038 live case 6: 「電視上放…的影片」 went to youtube; a place alone fits both sources.

    The rule is mirrored word for word so neither tool reads as the default, and
    no youtube example is framed by the TV, which every media request shares.
    """
    assert _ask_first_clause(Youtube.description) == _ask_first_clause(Nas.description)
    assert "電視上放" not in Youtube.description


def test_nas_plays_in_one_call_and_lists_only_to_browse() -> None:
    """D-038 follow-through, 2026-10-30: the mini model searched before every play.

    It stopped between the search and the play in 2 of 7 sessions, and a long
    recipe in the description did not change which call it reached for first.
    So the actions are named by intent, `play` finds and plays in one call, and
    the description stays short.
    """
    assert Nas.parameters_schema["properties"]["action"]["enum"] == ["list", "play", "skip"]
    assert "`play` finds a clip or a trip and plays it, in one call" in Nas.description
    assert "`list` tells what is there and plays nothing" in Nas.description
    assert "limit" not in Nas.parameters_schema["properties"]
    assert len(Nas.description) < 800 and len(Youtube.description) < 600
    assert "wait for the answer, then do what they asked" in _ask_first_clause(Nas.description)


# --- 1.30.0 body and memory families (mini context diet) ----------------------

# (family, action, the delegate class whose `__call__` must run, the call's arguments)
_BODY_ROUTES = (
    (Dance, "play", DanceMoves, {"move": "simple_nod", "repeat": 2}),
    (Head, "turn", MoveHead, {"direction": "left"}),
    (Memory, "save", Remember, {"fact": "Has a dog named Mochi"}),
    (Memory, "forget", Forget, {"query": "Mochi"}),
    (Face, "identify", WhoIsThis, {}),
    (Face, "remember", RememberFace, {"name": "小明"}),
)


@pytest.mark.asyncio
@pytest.mark.parametrize("family", _BODY_FAMILIES)
async def test_a_body_family_rejects_an_unknown_action_with_the_corrective_error(family: Any) -> None:
    """The error names every real action, so the model's next call can be right."""
    result = await family()(_deps(), action="explode")
    assert result == {"error": f"{family.name}: action must be one of {sorted(family.ACTIONS)}"}


@pytest.mark.asyncio
@pytest.mark.parametrize("family", _BODY_FAMILIES)
async def test_an_unknown_action_reaches_no_delegate(family: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing moves, saves or looks when the action itself is wrong."""
    called: list[str] = []

    async def _never(self, deps, **kwargs):
        called.append(self.name)
        return {}

    for delegate in (DanceMoves, StopDance, StopEmotion, MoveHead, HeadTracking, Remember, Forget, WhoIsThis,
                     RememberFace):
        monkeypatch.setattr(delegate, "__call__", _never)
    await family()(_deps(), action="explode")
    await family()(_deps())
    assert called == []


@pytest.mark.asyncio
@pytest.mark.parametrize("family,action,delegate,kwargs", _BODY_ROUTES)
async def test_a_body_family_calls_its_delegate_unchanged(
    family: Any, action: str, delegate: Any, kwargs: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Delegation, not reimplementation: the original class gets exactly the model's arguments."""
    seen: list[dict[str, Any]] = []

    async def _record(self, deps, **forwarded):
        seen.append(forwarded)
        return {"status": "from-the-delegate"}

    monkeypatch.setattr(delegate, "__call__", _record)
    result = await family()(_deps(), action=action, **kwargs)
    assert result == {"status": "from-the-delegate"}
    assert seen == [kwargs]


@pytest.mark.asyncio
async def test_dance_stop_calls_both_stop_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    """The one composite: `stop` ends a dance AND an emotion, so the model never has to know which runs."""
    called: list[str] = []

    async def _stop(self, deps, **kwargs):
        called.append(self.name)
        assert kwargs == {}
        return {"status": "stop_queued", "stopped": self.name.removeprefix("stop_")}

    monkeypatch.setattr(StopDance, "__call__", _stop)
    monkeypatch.setattr(StopEmotion, "__call__", _stop)
    result = await Dance()(_deps(), action="stop", move="simple_nod")
    assert called == ["stop_dance", "stop_emotion"]
    assert result == {"status": "stop_queued", "stopped": ["dance", "emotion"]}


@pytest.mark.asyncio
async def test_dance_stop_clears_the_move_queue_through_the_real_delegates() -> None:
    """End to end over the unpatched upstream classes: each one queues its clear."""
    deps = _deps()
    result = await Dance()(deps, action="stop")
    assert deps.movement_manager.clear_move_queue.call_count == 2
    assert result == {"status": "stop_queued", "stopped": ["dance", "emotion"]}


@pytest.mark.asyncio
async def test_dance_stop_reports_a_delegate_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stop that did not happen must not come back as `stop_queued`."""

    async def _broken(self, deps, **kwargs):
        return {"error": "movement manager unavailable"}

    monkeypatch.setattr(StopEmotion, "__call__", _broken)
    result = await Dance()(_deps(), action="stop")
    assert result == {"error": "movement manager unavailable"}


@pytest.mark.asyncio
@pytest.mark.parametrize("action,enabled", [("follow", True), ("stop_following", False)])
async def test_head_follow_and_stop_following_pass_enabled(
    action: str, enabled: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The action names the boolean, so the model never passes `enabled` itself."""
    seen: list[dict[str, Any]] = []

    async def _track(self, deps, **kwargs):
        seen.append(kwargs)
        return {"status": "tracking_requested", "head_tracking": kwargs["enabled"]}

    monkeypatch.setattr(HeadTracking, "__call__", _track)
    result = await Head()(_deps(), action=action)
    assert seen == [{"enabled": enabled}]
    assert result == {"status": "tracking_requested", "head_tracking": enabled}


@pytest.mark.asyncio
@pytest.mark.parametrize("action,enabled", [("follow", True), ("stop_following", False)])
async def test_head_follow_reaches_the_movement_manager(action: str, enabled: bool) -> None:
    """Over the unpatched `HeadTracking`: the bound value is what the robot receives, and wins over a stray one."""
    deps = _deps()
    result = await Head()(deps, action=action, enabled="nonsense")
    deps.movement_manager.set_head_tracking.assert_called_once_with(enabled)
    assert result == {"status": "tracking_requested", "head_tracking": enabled}


def test_the_dance_family_lists_upstreams_move_names_without_their_descriptions() -> None:
    """The model picks a move by name; upstream's per-move description list was ~1.3k chars every turn."""
    move = Dance.parameters_schema["properties"]["move"]
    assert move["enum"] == DanceMoves.parameters_schema["properties"]["move"]["enum"]
    assert move["description"] == "Which dance; leave out for a random one."
    assert Dance.parameters_schema["properties"]["repeat"] == DanceMoves.parameters_schema["properties"]["repeat"]


def test_the_head_family_offers_the_same_directions_as_move_head() -> None:
    """`turn` is `move_head`; a direction the family advertises must be one the delegate accepts."""
    assert Head.parameters_schema["properties"]["direction"]["enum"] == ["left", "right", "up", "down", "front"]
    assert set(Head.parameters_schema["properties"]["direction"]["enum"]) == set(MoveHead.DELTAS)


def test_each_family_module_registers_only_its_family() -> None:
    """The loader takes the `Tool` classes DEFINED in a listed module; helpers must not register.

    `dance.py` defines the private stop composite and `tool_family.py` the bound
    action, and both are `_auto_register = False`.
    """
    import importlib

    from reachy_companion.tools.core_tools import _tool_classes_from_module

    # By name, not identity: `test_external_loading` re-imports every tool module.
    for module_name, expected in (("dance", "Dance"), ("head", "Head"), ("memory", "Memory"), ("face", "Face")):
        module = importlib.import_module(f"reachy_companion.tools.{module_name}")
        assert [cls.__qualname__ for cls in _tool_classes_from_module(module)] == [expected], module_name
    assert _tool_classes_from_module(importlib.import_module("reachy_companion.tools.tool_family")) == []
