"""Suspend daemon face tracking for the length of one manual head window.

The daemon's face tracker is enabled at boot at weight 1.0 (`main.py:475`) and
overrides a queued goto while a face is in view -- which is why three correct
`look_around` calls on 2026-09-01 (00:15:52, 00:16:16, 00:17:24) each queued
`move_head right` with the right argument and each photographed the person
straight ahead.

Rung 3 of the escalation ladder: physical-state truth at the execution boundary,
not a prompt fix. Shaped as a context manager for the reason
`face_support.hold_still` is: `CancelledError` is a BaseException and a tool task
is cancellable at any await, so a restore left outside a `finally` would leave the
robot permanently face-blind after one cancelled look.
"""

from __future__ import annotations
import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from reachy_companion.tools.core_tools import ToolDependencies


logger = logging.getLogger(__name__)


@asynccontextmanager
async def head_window(deps: ToolDependencies, owner: str) -> AsyncIterator[None]:
    """Hold the head against daemon tracking while *owner* moves it.

    Single ownership: the owner that opened the window is the only one that can
    close it (`MovementManager.restore_head_tracking` checks the name), so a tool
    delegating its motion to another tool cannot have the window restored out
    from under it mid-capture.

    Degrades to a no-op on a movement manager without the seam -- the same
    defensiveness `look_around` already applies to `clear_move_queue`, and what
    lets the tool tests keep a bare `MagicMock` manager.
    """
    manager = deps.movement_manager
    suspend = getattr(manager, "suspend_head_tracking", None)
    restore = getattr(manager, "restore_head_tracking", None)
    if not callable(suspend) or not callable(restore):
        logger.debug("head_window(%s): this movement manager has no tracking-suspend seam", owner)
        yield
        return
    try:
        suspend(owner)
    except Exception as e:  # noqa: BLE001 - a failed suspend still deserves its move
        logger.warning("head_window(%s): could not suspend head tracking: %s", owner, e)
    try:
        yield
    finally:
        try:
            restore(owner)
        except Exception as e:  # noqa: BLE001 - never mask the caller's own failure
            logger.warning("head_window(%s): could not restore head tracking: %s", owner, e)
