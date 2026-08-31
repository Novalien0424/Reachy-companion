"""Action-enum tool families: 18 registered tools become 6.

41 tools at the start of a turn is past OpenAI's own "aim for fewer than 20"
and inside the measured degradation zone
(docs/research-mini-tool-calling-2026-08.md §A1). The consolidation is a SCHEMA
refactor: every family façade delegates to the original tool instance, so the
confirmation gates, prerequisite checks and error strings are unchanged and
still covered by those modules' own tests.
"""

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from reachy_companion.hanova import settings
from reachy_companion.tools.tv import Tv
from reachy_companion.tools.nas import Nas
from reachy_companion.tools.drive import Drive
from reachy_companion.tools.music import Music
from reachy_companion.tools.tasks import Tasks
from reachy_companion.tools.calendar import Calendar
from reachy_companion.tools.play_music import PlayMusic
from reachy_companion.tools.calendar_add import CalendarAdd
from reachy_companion.tools.calendar_list import CalendarList
from reachy_companion.tools.calendar_delete import CalendarDelete


_FAMILIES = (Calendar, Tasks, Drive, Nas, Music, Tv)


def _deps() -> SimpleNamespace:
    return SimpleNamespace(reachy_mini=MagicMock(), movement_manager=MagicMock(), instance_path=None)


@pytest.mark.parametrize("family", _FAMILIES)
def test_every_family_has_a_required_action_enum(family) -> None:
    """`action` is the one field the façade owns, so it must be required and complete."""
    schema = family().parameters_schema
    assert schema["required"] == ["action"]
    actions = schema["properties"]["action"]["enum"]
    assert actions and actions == sorted(set(actions))
    # Every advertised action must have a delegate behind it.
    assert set(actions) == set(family.ACTIONS)


@pytest.mark.parametrize("family", _FAMILIES)
def test_every_family_description_is_symmetric(family) -> None:
    """A family that only says what it is for gets called for its neighbours too."""
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
    result = await Calendar()(_deps(), action="explode")
    assert "error" in result and "action must be one of" in result["error"]


@pytest.mark.asyncio
async def test_the_delegate_still_validates_its_own_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    """The façade adds no argument check of its own (Codex round 1, P2-5).

    `calendar_add` answers a missing `start`/`end` with its own sentence, and it
    only gets that far after its `settings.tool_status` prerequisite check. A
    façade-level check would run first, change the wording, and mask "this is
    not configured" behind "you forgot an argument".
    """
    monkeypatch.setattr(settings, "tool_status", lambda name: (True, ""))
    result = await Calendar()(_deps(), action="add", summary="午餐")
    assert result == {"ok": False, "error": "summary, start and end are all required"}


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

    async def _add(self, deps, **kwargs):
        seen.append(kwargs)
        return {"status": "added"}

    monkeypatch.setattr(CalendarAdd, "__call__", _add)
    result = await Calendar()(
        _deps(), action="add", summary="午餐", start="2026-09-01T12:00", end="2026-09-01T13:00"
    )
    assert result == {"status": "added"}
    assert seen == [{"summary": "午餐", "start": "2026-09-01T12:00", "end": "2026-09-01T13:00"}]


@pytest.mark.asyncio
async def test_family_forwards_a_no_argument_action(monkeypatch: pytest.MonkeyPatch) -> None:
    """`action` is consumed by the dispatch and never reaches the delegate."""

    async def _list(self, deps, **kwargs):
        return {"events": [], "seen": kwargs}

    monkeypatch.setattr(CalendarList, "__call__", _list)
    result = await Calendar()(_deps(), action="list")
    assert result["seen"] == {}


@pytest.mark.asyncio
async def test_family_preserves_the_delegate_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A prerequisite refusal must reach the model exactly as before."""

    async def _play(self, deps, **kwargs):
        return {"error": "play_music unavailable: MUSIC_WHEELS"}

    monkeypatch.setattr(PlayMusic, "__call__", _play)
    result = await Music()(_deps(), action="play", query="周杰倫")
    assert result == {"error": "play_music unavailable: MUSIC_WHEELS"}


def test_the_eighteen_sub_tools_are_no_longer_registered() -> None:
    """The whole point of the diet: 18 names leave the session, 6 arrive."""
    from reachy_companion.tools.core_tools import get_tools

    registered = set(get_tools())
    for name in (
        "calendar_add", "calendar_list", "calendar_delete",
        "task_add", "task_list", "task_complete", "task_delete",
        "drive_list", "drive_trash", "drive_upload",
        "nas_video_query", "play_nas_video", "nas_play_folder", "nas_skip",
        "play_music", "stop_music", "play_video", "show_on_tv",
    ):
        assert name not in registered, name
    for name in ("calendar", "tasks", "drive", "nas", "music", "tv"):
        assert name in registered, name


# --- delegation fidelity: the two answers, in their original order -----------
#
# The façade is only allowed to pick a delegate. These pin BOTH halves of the
# behavior the brief's Global Constraint protects: with the prerequisite
# missing, every family answers `settings.unavailable(reason)` even though the
# action-specific argument is absent too; with the prerequisite satisfied, the
# delegate's own sentence comes through byte for byte.

_UNAVAILABLE_CASES = (
    (Calendar, "add"),
    (Calendar, "list"),
    (Calendar, "delete"),
    (Tasks, "add"),
    (Tasks, "list"),
    (Tasks, "complete"),
    (Tasks, "delete"),
    (Drive, "list"),
    (Drive, "trash"),
    (Drive, "upload"),
    (Nas, "query"),
    (Nas, "play"),
    (Nas, "play_folder"),
    (Nas, "skip"),
    (Music, "play"),
    (Tv, "play_video"),
    (Tv, "show"),
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
    (Calendar, "add", {}, {"ok": False, "error": "summary, start and end are all required"}),
    (Calendar, "delete", {}, {"ok": False, "error": "match must be at least 2 characters"}),
    (Tasks, "add", {}, {"ok": False, "error": "title is required"}),
    (Tasks, "complete", {}, {"ok": False, "error": "match must be at least 2 characters"}),
    (Tasks, "delete", {"match": "x"}, {"ok": False, "error": "match must be at least 2 characters"}),
    (Drive, "trash", {}, {"ok": False, "error": "file_id is required"}),
    (Music, "play", {}, {"ok": False, "error": "query is required"}),
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

    async def _delete(self, deps, **kwargs):
        seen.append(kwargs)
        return {"ok": True, "status": "deleted"}

    monkeypatch.setattr(CalendarDelete, "__call__", _delete)
    result = await Calendar()(_deps(), action="delete", confirm=True)
    assert result == {"ok": True, "status": "deleted"}
    assert seen == [{"confirm": True}]


@pytest.mark.asyncio
async def test_a_non_string_action_is_refused_not_crashed() -> None:
    """The model can emit anything; `action` is the one field the façade owns."""
    result = await Calendar()(_deps(), action=None)
    assert "action must be one of" in result["error"]


def test_no_family_suppresses_the_spoken_follow_up() -> None:
    """None of the 18 sets `needs_response = False`, so no family may either."""
    for family in _FAMILIES:
        assert family.needs_response is True
        for tool in family.ACTIONS.values():
            assert tool.needs_response is True
