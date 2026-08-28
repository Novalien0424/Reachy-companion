import time
import threading
from unittest.mock import MagicMock, call
from collections.abc import Callable

import numpy as np
import pytest

from reachy_mini.utils import create_head_pose
from reachy_mini.utils.interpolation import compose_world_offset
from reachy_companion.moves import BreathingMove, MovementManager
from reachy_companion.dance_emotion_moves import EmotionQueueMove


class _FakeMove:
    """Minimal non-emotion Move stub returning a fixed head pose."""

    def __init__(self, head: np.ndarray) -> None:
        self._head = head
        self.duration = 10.0

    def evaluate(self, t: float):
        return (self._head, np.array([0.0, 0.0]), 0.0)


def _wait_for(predicate: Callable[[], bool], timeout: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


def test_stop_can_skip_neutral_reset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sleep shutdown should stop the movement loop without undoing the sleep pose."""
    robot = MagicMock()
    manager = MovementManager(robot)
    started = threading.Event()

    def fake_working_loop() -> None:
        started.set()
        while not manager._stop_event.is_set():
            time.sleep(0.001)

    monkeypatch.setattr(manager, "working_loop", fake_working_loop)

    manager.start()
    assert started.wait(timeout=1.0)

    manager.stop(reset_to_neutral=False)

    assert manager._thread is None
    robot.goto_target.assert_not_called()


def test_head_tracking_follows_speaking() -> None:
    """Once enabled, tracking owns the head when idle and releases it while the assistant speaks."""
    robot = MagicMock()
    robot.get_current_head_pose.return_value = np.eye(4)
    robot.get_current_joint_positions.return_value = ([0.0] * 6, [0.0, 0.0])
    manager = MovementManager(robot)
    manager.start()
    try:
        # The head_tracking tool enables tracking with full weight.
        manager.set_head_tracking(True)
        assert _wait_for(lambda: call(weight=1.0) in robot.start_head_tracking.call_args_list)

        # Speaking with a locked face captures the anchor and releases the head.
        manager.set_speaking(True)
        assert _wait_for(lambda: call(weight=0.0) in robot.start_head_tracking.call_args_list)
        assert _wait_for(lambda: manager._track_anchor is not None)

        # Done speaking hands the head back to tracking.
        robot.start_head_tracking.reset_mock()
        manager.set_speaking(False)
        assert _wait_for(lambda: call(weight=1.0) in robot.start_head_tracking.call_args_list)
        assert _wait_for(lambda: manager._track_anchor is None)
    finally:
        manager.stop(reset_to_neutral=False)

    robot.stop_head_tracking.assert_called_once()


def _apply_hold(manager: MovementManager, hold: bool) -> None:
    """Post the public hold command and let the loop's own drain apply it.

    Driving the queue rather than `_handle_command` keeps the thread-safety
    plumbing (the command actually reaches the worker) inside the test.
    """
    manager.set_hold_still(hold)
    manager._poll_signals(manager._now())


def test_hold_still_parks_the_head_and_hands_it_back() -> None:
    """An enrollment burst freezes tracking at the current pose, then restores it."""
    robot = MagicMock()
    anchor = create_head_pose(0, 0, 0, 0, 0, 12, degrees=True)
    robot.get_current_head_pose.return_value = anchor
    manager = MovementManager(robot)
    manager._head_tracking = True

    _apply_hold(manager, True)

    assert manager._hold_still is True
    assert manager._track_anchor is not None and np.allclose(manager._track_anchor, anchor)
    assert robot.start_head_tracking.call_args_list == [call(weight=0.0)]

    _apply_hold(manager, False)

    assert manager._hold_still is False
    assert manager._track_anchor is None
    assert robot.start_head_tracking.call_args_list == [call(weight=0.0), call(weight=1.0)]


def test_hold_still_release_leaves_the_head_to_speech() -> None:
    """If the assistant started talking during the burst, set_speaking owns the anchor."""
    robot = MagicMock()
    robot.get_current_head_pose.return_value = np.eye(4)
    manager = MovementManager(robot)
    manager._head_tracking = True
    manager._is_speaking = True

    _apply_hold(manager, True)
    robot.start_head_tracking.reset_mock()
    _apply_hold(manager, False)

    robot.start_head_tracking.assert_not_called()
    assert manager._hold_still is False


def test_hold_still_clears_the_active_move_and_the_queue() -> None:
    """Any motion blurs the frame, so the photo wins: current move and queue both go."""
    robot = MagicMock()
    manager = MovementManager(robot)
    manager.state.current_move = _FakeMove(create_head_pose(0, 0, 0, 0, 0, 0, degrees=True))
    manager.state.move_start_time = manager._now()
    manager.move_queue.append(_FakeMove(create_head_pose(0, 0, 0, 0, 10, 0, degrees=True)))
    manager._breathing_active = True

    _apply_hold(manager, True)

    assert manager.state.current_move is None
    assert manager.state.move_start_time is None
    assert not manager.move_queue
    assert manager._breathing_active is False


def test_breathing_does_not_restart_while_held() -> None:
    """Idle breathing must not creep back mid-burst — it resumes on release."""
    robot = MagicMock()
    robot.get_current_head_pose.return_value = np.eye(4)
    robot.get_current_joint_positions.return_value = ([0.0] * 6, [0.0, 0.0])
    manager = MovementManager(robot)
    manager.state.last_activity_time = manager._now() - 10.0
    manager._hold_still = True

    manager._manage_breathing(manager._now())

    assert not manager.move_queue
    assert manager._breathing_active is False
    robot.get_current_head_pose.assert_not_called()

    # Positive control: the same idle state does breathe once the hold is gone.
    manager._hold_still = False
    manager._manage_breathing(manager._now())

    assert any(isinstance(move, BreathingMove) for move in manager.move_queue)


def test_speaking_anchor_composes_emotions_and_holds_dances_from_neutral() -> None:
    """While speaking: hold the anchor, compose emotions onto it, play dances from neutral."""
    robot = MagicMock()
    manager = MovementManager(robot)
    anchor = create_head_pose(0, 0, 0, 0, 0, 20, degrees=True)
    manager._track_anchor = anchor

    # No move: the head holds the captured look-at anchor.
    manager.state.current_move = None
    head, _, _ = manager._get_primary_pose(manager._now())
    assert np.allclose(head, anchor)

    # Emotion: composed onto the anchor exactly like the daemon wobble.
    emotion_head = create_head_pose(0, 0, 0, 0, 0, 15, degrees=True)
    recorded = MagicMock()
    recorded.get.return_value = _FakeMove(emotion_head)
    manager.state.current_move = EmotionQueueMove("happy", recorded)
    manager.state.move_start_time = manager._now()
    head, _, _ = manager._get_primary_pose(manager._now())
    assert np.allclose(head, compose_world_offset(anchor, emotion_head))

    # Any other move (e.g. a dance) plays from its own neutral base, ignoring the anchor.
    dance_head = create_head_pose(0, 0, 0, 0, 25, 0, degrees=True)
    manager.state.current_move = _FakeMove(dance_head)
    manager.state.move_start_time = manager._now()
    head, _, _ = manager._get_primary_pose(manager._now())
    assert np.allclose(head, dance_head)
