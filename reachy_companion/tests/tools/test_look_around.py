"""look_around: turn the head, let it settle, then look.

The composite exists because `gpt-realtime-2.1-mini` does not chain
move_head → camera (docs/research-mini-tool-calling-2026-08.md §B2): asked
「轉到右邊去看看有誰」 it called `camera` alone and then narrated a turn that
never happened. A composite removes the chaining decision entirely and returns
`direction_requested`, which is exactly as much as the motion API can attest
(Codex round 1, P2-2).
"""

import base64
import importlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from reachy_companion.tools.camera import Camera
from reachy_companion.tools.move_head import MoveHead
from reachy_companion.tools.look_around import LookAround


def _deps(camera_enabled: bool = True) -> SimpleNamespace:
    """Build the minimum ToolDependencies shape look_around touches."""
    media = SimpleNamespace(get_frame_jpeg=lambda: b"\xff\xd8jpeg")
    reachy_mini = SimpleNamespace(
        media=media,
        get_current_head_pose=MagicMock(return_value=object()),
        get_current_joint_positions=MagicMock(return_value=([0.0] * 6, [0.0, 0.0])),
    )
    return SimpleNamespace(
        reachy_mini=reachy_mini,
        movement_manager=MagicMock(),
        camera_enabled=camera_enabled,
        motion_duration_s=0.01,
    )


@pytest.mark.asyncio
async def test_look_around_moves_then_captures(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole point of the composite: the move happens BEFORE the picture."""
    order: list[str] = []

    async def _queue(self: MoveHead, deps: Any, direction: str) -> dict[str, Any]:
        order.append(f"move:{direction}")
        return {"status": "move_queued", "direction_requested": direction}

    async def _camera(self: Camera, deps: Any, **kwargs: Any) -> dict[str, Any]:
        order.append("camera")
        return {"b64_im": base64.b64encode(b"jpeg").decode("utf-8")}

    monkeypatch.setattr(MoveHead, "queue_direction", _queue)
    monkeypatch.setattr(Camera, "__call__", _camera)
    monkeypatch.setattr("reachy_companion.tools.look_around.LOOK_AROUND_SETTLE_S", 0.0)

    deps = _deps()
    result = await LookAround()(deps, direction="right", question="誰在那邊")
    assert order == ["move:right", "camera"]
    assert result["direction_requested"] == "right"
    assert result["question"] == "誰在那邊"
    assert result["b64_im"]
    # The queue is cleared first, so this move is not stuck behind an older one.
    deps.movement_manager.clear_move_queue.assert_called_once_with()


@pytest.mark.asyncio
async def test_look_around_rejects_an_unknown_direction() -> None:
    """A direction the schema cannot express never reaches the body.

    The message is read by the model, so it is written for a reader: a plain
    comma-joined list, not a Python list repr full of brackets and quotes
    (review round 1, minor 3).
    """
    result = await LookAround()(_deps(), direction="behind")
    assert result["error"] == "direction must be one of left, right, up, down, front"
    assert "[" not in result["error"] and "'" not in result["error"]
    assert "direction_requested" not in result


@pytest.mark.asyncio
async def test_look_around_reports_a_failed_move_without_claiming_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No direction field on a failed move: the model must not narrate a turn."""

    async def _queue(self: MoveHead, deps: Any, direction: str) -> dict[str, Any]:
        return {"error": "move_head failed: RuntimeError: motors off"}

    monkeypatch.setattr(MoveHead, "queue_direction", _queue)
    monkeypatch.setattr("reachy_companion.tools.look_around.LOOK_AROUND_SETTLE_S", 0.0)
    result = await LookAround()(_deps(), direction="left")
    assert "error" in result
    assert "direction_requested" not in result


@pytest.mark.asyncio
async def test_look_around_reports_a_failed_capture_but_keeps_the_move(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The head really did turn, so say so — and say the picture failed."""

    async def _queue(self: MoveHead, deps: Any, direction: str) -> dict[str, Any]:
        return {"status": "move_queued", "direction_requested": direction}

    async def _camera(self: Camera, deps: Any, **kwargs: Any) -> dict[str, Any]:
        return {"error": "No frame available"}

    monkeypatch.setattr(MoveHead, "queue_direction", _queue)
    monkeypatch.setattr(Camera, "__call__", _camera)
    monkeypatch.setattr("reachy_companion.tools.look_around.LOOK_AROUND_SETTLE_S", 0.0)
    result = await LookAround()(_deps(), direction="up")
    assert result["direction_requested"] == "up"
    assert result["error"] == "No frame available"
    assert "b64_im" not in result


@pytest.mark.asyncio
async def test_look_around_reports_a_raised_capture_as_a_capture_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A camera that RAISES is still a capture failure, not a failed move.

    `Camera` returns `{"error": …}` for the failures it anticipates, but a
    driver fault raises instead. Without the guard that exception escapes
    `look_around` and the dispatcher turns it into a bare `{"error": …}` — the
    move-failure envelope, which tells the model the head never went anywhere
    when in fact it did (review round 1, minor 2).
    """

    async def _queue(self: MoveHead, deps: Any, direction: str) -> dict[str, Any]:
        return {"status": "move_queued", "direction_requested": direction}

    async def _camera(self: Camera, deps: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("v4l2 device disappeared")

    monkeypatch.setattr(MoveHead, "queue_direction", _queue)
    monkeypatch.setattr(Camera, "__call__", _camera)
    monkeypatch.setattr("reachy_companion.tools.look_around.LOOK_AROUND_SETTLE_S", 0.0)

    result = await LookAround()(_deps(), direction="down")
    assert result["direction_requested"] == "down"
    assert "b64_im" not in result
    assert "RuntimeError" in result["error"]


@pytest.mark.asyncio
async def test_look_around_defaults_the_question(monkeypatch: pytest.MonkeyPatch) -> None:
    """A caller that names only a direction still gets a describable picture."""
    seen: list[str] = []

    async def _queue(self: MoveHead, deps: Any, direction: str) -> dict[str, Any]:
        return {"status": "move_queued", "direction_requested": direction}

    async def _camera(self: Camera, deps: Any, **kwargs: Any) -> dict[str, Any]:
        seen.append(kwargs["question"])
        return {"b64_im": "x"}

    monkeypatch.setattr(MoveHead, "queue_direction", _queue)
    monkeypatch.setattr(Camera, "__call__", _camera)
    monkeypatch.setattr("reachy_companion.tools.look_around.LOOK_AROUND_SETTLE_S", 0.0)
    await LookAround()(_deps(), direction="front")
    assert seen == ["描述你現在看到什麼"]


@pytest.mark.asyncio
async def test_look_around_owns_one_window_across_move_settle_and_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Single ownership (spec §2): the delegate must not close the window early."""
    monkeypatch.setattr("reachy_companion.tools.look_around.LOOK_AROUND_SETTLE_S", 0.0)
    deps = _deps()
    deps.motion_duration_s = 0.0
    order: list[str] = []
    deps.movement_manager.suspend_head_tracking.side_effect = lambda owner: order.append(f"suspend:{owner}")
    deps.movement_manager.restore_head_tracking.side_effect = lambda owner: order.append(f"restore:{owner}")

    async def _queue(self, deps_, direction):  # noqa: ANN001
        order.append(f"move:{direction}")
        return {"status": "move_queued", "direction_requested": direction}

    async def _shoot(self, deps_, **kwargs):  # noqa: ANN001
        order.append("camera")
        return {"b64_im": "AAA="}

    monkeypatch.setattr(MoveHead, "queue_direction", _queue)
    monkeypatch.setattr(Camera, "__call__", _shoot)

    result = await LookAround()(deps, direction="right", question="誰在那邊")

    assert order == ["suspend:look_around", "move:right", "camera", "restore:look_around"]
    assert result == {"direction_requested": "right", "question": "誰在那邊", "b64_im": "AAA="}


@pytest.mark.asyncio
async def test_look_around_restores_tracking_when_the_capture_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every exit closes the window — the `face_support.hold_still` contract."""
    monkeypatch.setattr("reachy_companion.tools.look_around.LOOK_AROUND_SETTLE_S", 0.0)
    deps = _deps()
    deps.motion_duration_s = 0.0

    async def _queue(self, deps_, direction):  # noqa: ANN001
        return {"status": "move_queued", "direction_requested": direction}

    async def _boom(self, deps_, **kwargs):  # noqa: ANN001
        raise RuntimeError("camera driver fault")

    monkeypatch.setattr(MoveHead, "queue_direction", _queue)
    monkeypatch.setattr(Camera, "__call__", _boom)

    result = await LookAround()(deps, direction="up")

    assert result["direction_requested"] == "up"
    assert "RuntimeError" in result["error"]
    deps.movement_manager.restore_head_tracking.assert_called_once_with("look_around")


@pytest.mark.asyncio
async def test_an_unknown_direction_opens_no_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """Validation runs before the body is touched, so nothing needs restoring."""
    deps = _deps()

    result = await LookAround()(deps, direction="behind")

    assert result["error"] == "direction must be one of left, right, up, down, front"
    deps.movement_manager.suspend_head_tracking.assert_not_called()
    deps.movement_manager.restore_head_tracking.assert_not_called()


def test_descriptions_are_symmetric_and_route_directional_looks() -> None:
    """Research §A2: the asymmetry between camera and move_head WAS the bug."""
    for description in (Camera.description, MoveHead.description, LookAround.description):
        assert "Use when:" in description
        assert "Do NOT use when:" in description
    assert "look_around" in Camera.description
    assert "look_around" in MoveHead.description
    assert "camera" in LookAround.description and "move_head" in LookAround.description
    # Chinese trigger phrasings must be enumerated, not implied (research §C7).
    for phrase in ("右邊", "左邊", "轉過去"):
        assert phrase in LookAround.description
    # No `behind`: the schema cannot express it and body rotation is out of
    # scope this wave (Codex round 1, P2-4).
    assert "後面" not in LookAround.description
    assert "behind" not in LookAround.description
    assert "direction_requested" in LookAround.description
    assert "does not move" in Camera.description or "it does not move" in Camera.description


def test_schema_enumerates_the_five_directions() -> None:
    """The enum is the contract the model is handed; `behind` is not in it."""
    schema = LookAround().parameters_schema
    assert schema["properties"]["direction"]["enum"] == ["left", "right", "up", "down", "front"]
    assert schema["required"] == ["direction"]
    assert "behind" not in schema["properties"]["direction"]["enum"]


def test_the_retired_tools_are_gone() -> None:
    """sweep_look is subsumed by look_around; the two gags leave the diet's budget."""
    from reachy_companion.tools.core_tools import get_tools

    registered = set(get_tools())
    for name in ("sweep_look", "self_destruct", "mad_laugh"):
        assert name not in registered, name
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(f"reachy_companion.tools.{name}")
