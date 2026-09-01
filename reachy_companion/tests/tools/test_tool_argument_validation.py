"""Every robot-action tool validates its own arguments at the boundary.

Platform fact (docs/codex-research-instructing-2026-09.md): both `gpt-realtime-2.1`
models support function-calling JSON Schema and do NOT support structured outputs,
so argument-schema adherence is not guaranteed and the enum in the schema proves
nothing at runtime. The mini tier's characteristic failure is confident guessing.
Every rejection therefore names the allowed values, because the string is read back
to the model and is its only chance to self-correct.
"""

from __future__ import annotations
from unittest.mock import AsyncMock, MagicMock

import pytest

from reachy_mini.utils import create_head_pose
from reachy_companion.toolboxes import TOOLBOX_CATEGORIES
from reachy_companion.tools.move_head import MoveHead
from reachy_companion.tools.core_tools import ToolDependencies
from reachy_companion.tools.stop_dance import StopDance
from reachy_companion.conversation_mode import MODE_VALUES
from reachy_companion.tools.open_toolbox import OpenToolbox
from reachy_companion.tools.stop_emotion import StopEmotion
from reachy_companion.tools.head_tracking import HeadTracking
from reachy_companion.tools.set_conversation_mode import SetConversationMode


def _deps(**kwargs: object) -> ToolDependencies:
    reachy_mini = MagicMock()
    reachy_mini.get_current_head_pose.return_value = create_head_pose(0, 0, 0, 0, 0, 0, degrees=True)
    reachy_mini.get_current_joint_positions.return_value = ([0.0] * 6, [0.0, 0.0])
    return ToolDependencies(reachy_mini=reachy_mini, movement_manager=MagicMock(), **kwargs)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_head_tracking_refuses_a_string_instead_of_coercing_it() -> None:
    """`bool("false")` is True - the coercion that turned "stop" into "start"."""
    deps = _deps()

    result = await HeadTracking()(deps, enabled="false")

    assert result == {"error": "enabled must be true or false (a boolean, not a string)"}
    deps.movement_manager.set_head_tracking.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [True, False])
async def test_head_tracking_passes_a_real_boolean_through(enabled: bool) -> None:
    """Real booleans still reach the movement manager unchanged."""
    deps = _deps()

    await HeadTracking()(deps, enabled=enabled)

    deps.movement_manager.set_head_tracking.assert_called_once_with(enabled)


@pytest.mark.asyncio
async def test_open_toolbox_validates_the_category_before_the_seam() -> None:
    """The seam validates too, but the model must never reach it with a guess."""
    seam = AsyncMock()
    deps = _deps(open_toolbox=seam)

    result = await OpenToolbox()(deps, category="calendar")

    assert result["ok"] is False
    assert result["error"] == f"category must be one of {', '.join(TOOLBOX_CATEGORIES)}"
    assert result["categories"] == list(TOOLBOX_CATEGORIES)
    seam.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_conversation_mode_validates_the_mode_before_the_seam() -> None:
    """Chinese display labels are rejected in favor of the enum values."""
    seam = AsyncMock()
    deps = _deps(set_conversation_mode=seam)

    result = await SetConversationMode()(deps, mode="紀錄模式")

    assert result["ok"] is False
    assert result["error"] == f"mode must be one of {', '.join(MODE_VALUES)}"
    assert result["modes"] == list(MODE_VALUES)
    seam.assert_not_awaited()


@pytest.mark.asyncio
async def test_move_head_names_the_directions_it_accepts() -> None:
    """Landed with Task 5's rewrite; pinned here with the rest of the sweep."""
    result = await MoveHead()(_deps(), direction="backwards")

    assert result == {"error": "direction must be one of left, right, up, down, front"}


@pytest.mark.parametrize("tool", [StopDance(), StopEmotion()])
def test_the_stop_tools_take_no_arguments(tool: object) -> None:
    """A required `dummy: boolean` is one more thing for the mini tier to get wrong."""
    assert tool.parameters_schema == {"type": "object", "properties": {}, "required": []}


@pytest.mark.parametrize(
    "tool",
    [MoveHead(), HeadTracking(), OpenToolbox(), SetConversationMode(), StopDance(), StopEmotion()],
)
def test_no_robot_action_tool_ships_a_placeholder_parameter(tool: object) -> None:
    """Placeholders teach the model that inventing arguments is normal."""
    properties = tool.parameters_schema.get("properties", {})
    assert "dummy" not in properties
    for name, spec in properties.items():
        assert "dummy" not in str(spec.get("description", "")).lower(), name


# --------------------------------------------------------------------------
# Returns audit (spec §4): named facts, and no claim of completed motion
# --------------------------------------------------------------------------

_QUEUE_TIME_CLAIMS = ("looking", "turned", "moved", "stopped dance", "stopped emotion", "following")


@pytest.mark.asyncio
async def test_head_tracking_reports_the_request_not_the_outcome() -> None:
    """The toggle is queued on the movement manager; nothing has followed anyone yet."""
    deps = _deps()

    assert await HeadTracking()(deps, enabled=True) == {
        "status": "tracking_requested",
        "head_tracking": True,
    }
    assert await HeadTracking()(deps, enabled=False) == {
        "status": "tracking_requested",
        "head_tracking": False,
    }


@pytest.mark.asyncio
async def test_the_stop_tools_report_a_queued_stop() -> None:
    """The stop tools queue a stop request rather than claiming completion."""
    deps = _deps()

    assert await StopDance()(deps) == {"status": "stop_queued", "stopped": "dance"}
    assert await StopEmotion()(deps) == {"status": "stop_queued", "stopped": "emotion"}
    assert deps.movement_manager.clear_move_queue.call_count == 2


@pytest.mark.asyncio
async def test_no_physical_tool_status_claims_a_completed_motion() -> None:
    """One sweep over every queue-time return in the physical family."""
    deps = _deps()
    results = [
        await MoveHead().queue_direction(deps, "right"),
        await HeadTracking()(deps, enabled=True),
        await StopDance()(deps),
        await StopEmotion()(deps),
    ]

    for result in results:
        status = str(result.get("status", ""))
        assert status, result
        for claim in _QUEUE_TIME_CLAIMS:
            assert claim not in status, f"{status!r} claims a motion that has only been queued"


@pytest.mark.asyncio
async def test_the_already_honest_returns_are_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dance and play_emotion already say `queued`; the audit confirms, not churns."""
    from reachy_companion.tools import dance as dance_module
    from reachy_companion.tools import play_emotion as emotion_module
    from reachy_companion.tools.dance import Dance
    from reachy_companion.tools.play_emotion import PlayEmotion

    class _DanceQueueMove:
        def __init__(self, move_name: str) -> None:
            self.move_name = move_name

    class _RecordedMoves:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def list_moves(self) -> list[str]:
            return ["laughing2"]

    class _EmotionQueueMove:
        def __init__(self, emotion_name: str, library: object) -> None:
            self.emotion_name = emotion_name
            self.library = library

    monkeypatch.setattr(dance_module, "DANCE_AVAILABLE", True)
    monkeypatch.setattr(
        dance_module,
        "AVAILABLE_MOVES",
        {"mini_wave": (lambda: None, {}, {"description": "wave"})},
    )
    monkeypatch.setattr(dance_module, "DanceQueueMove", _DanceQueueMove)
    monkeypatch.setattr(emotion_module, "EMOTION_AVAILABLE", True)
    monkeypatch.setattr(emotion_module, "RecordedMoves", _RecordedMoves)
    monkeypatch.setattr(emotion_module, "EmotionQueueMove", _EmotionQueueMove)
    monkeypatch.setattr(PlayEmotion, "_library", None)
    deps = _deps()

    dance_result = await Dance()(deps, move="mini_wave", repeat=2)
    emotion_result = await PlayEmotion()(deps, emotion="happy")

    assert dance_result == {"status": "queued", "move": "mini_wave", "repeat": 2}
    assert emotion_result == {"status": "queued", "emotion": "laughing2"}
    assert deps.movement_manager.queue_move.call_count == 3
