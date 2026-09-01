"""Helpers for app startup and shutdown lifecycle behavior."""

import time
import logging
import urllib.error
import urllib.request
from typing import Any, Final

import numpy as np
import numpy.typing as npt

from reachy_mini import ReachyMini
from reachy_mini.reachy_mini import SLEEP_HEAD_POSE
from reachy_mini.utils.interpolation import distance_between_poses
from reachy_companion.hanova import audio_drain
from reachy_companion.audio.envparse import env_float
from reachy_companion.tools.core_tools import ToolDependencies


_STOP_CURRENT_APP_PATH = "/api/apps/stop-current-app"
_STOP_CURRENT_APP_TIMEOUT_S = 2.0
_SLEEP_HEAD_TRANSLATION_TOLERANCE_M = 0.05
_SLEEP_HEAD_ROTATION_TOLERANCE_RAD = 0.35


def request_stop_current_app(robot: ReachyMini, logger: logging.Logger) -> bool:
    """Request the Reachy Mini daemon to stop the current app."""
    stop_current_app_url = f"http://{robot.client.host}:{robot.client.port}{_STOP_CURRENT_APP_PATH}"
    request = urllib.request.Request(stop_current_app_url, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=_STOP_CURRENT_APP_TIMEOUT_S) as response:
            response.read()
    except urllib.error.URLError as e:
        logger.error("Failed to request current app stop via %s: %s", stop_current_app_url, e)
        return False

    logger.info("Requested current app stop via %s", stop_current_app_url)
    return True


def _is_sleep_head_pose(head_pose: npt.ArrayLike) -> bool:
    try:
        current_head_pose: npt.NDArray[np.float64] = np.asarray(head_pose, dtype=np.float64)
    except (TypeError, ValueError):
        return False

    if current_head_pose.shape != (4, 4):
        return False

    pose_distances = distance_between_poses(current_head_pose, SLEEP_HEAD_POSE)
    translation_distance = float(pose_distances[0])
    rotation_angle = float(pose_distances[1])
    return (
        translation_distance <= _SLEEP_HEAD_TRANSLATION_TOLERANCE_M
        and rotation_angle <= _SLEEP_HEAD_ROTATION_TOLERANCE_RAD
    )


def wake_up_if_sleeping(robot: ReachyMini, logger: logging.Logger) -> bool:
    """Run the SDK wake-up movement when Reachy starts from the sleep pose."""
    try:
        head_pose = robot.get_current_head_pose()
    except Exception as e:
        logger.warning("Could not read robot pose before startup wake-up check: %s", e)
        return False

    if not _is_sleep_head_pose(head_pose):
        return False

    logger.info("Robot is in sleep pose; running wake-up movement.")
    try:
        robot.enable_motors()
        robot.wake_up()
    except Exception as e:
        logger.error("Failed to run wake-up movement: %s", e)
        return False
    return True


# --- sleep quiesce (2026-08-31 plan) ----------------------------------------
# Observed on-robot 2026-08-31: `go_to_sleep` ran the sleep pose immediately
# while the mic, the player and the barge machine stayed live for another five
# to ten seconds. The journal shows a goodbye spoken after the body was already
# asleep, a second `go_to_sleep` from a repeated command, and a
# `barge-in rolled back; resuming reply` resurrecting parked audio over a
# sleeping robot. The cure is ordering, not new machinery — and the order is
# silence, then wait, then drain, then pose (Codex round 2, 2a-6). Silencing
# last would leave the microphone live for the whole of the wait.
SLEEP_DRAIN_POLL_S: Final[float] = 0.1
SLEEP_DRAIN_CAP_DEFAULT_S: Final[float] = 6.0


def sleep_drain_cap_s() -> float:
    """Longest the sleep pose waits for the goodbye to finish playing."""
    return env_float("SLEEP_GOODBYE_DRAIN_CAP_S", SLEEP_DRAIN_CAP_DEFAULT_S, lo=0.0, hi=15.0)


def begin_sleep_quiesce(stream_manager: Any, logger: logging.Logger) -> None:
    """Silence the robot's inputs. First thing the sleep path does.

    Thread-agnostic by construction, because both callers exist: the tool's own
    event loop reaches it through `deps.begin_sleep`, and the worker thread
    reaches it again (idempotently) from `go_to_sleep_and_stop_app`.
    `_mic_muted` is a plain flag the record loop reads (`console.py:912`) and
    `HuggingFaceRealtimeHandler.on_external_interrupt` is documented safe from a
    non-loop thread — it never cancels the task it is running on, and every
    barge timer re-checks the flags it clears. (Cited by name: the line range
    this once carried went stale within one wave of edits.)

    Two deliberate omissions:

    * **No flush.** `clear_audio_queue()` would run `on_external_interrupt`
      *and* drop the player queue — which holds the goodbye the model spoke in
      the same response as the tool call. That audio is the whole point.
    * **No `turn_detection = None` push.** Muting the mic is the cheaper hard
      stop and needs no round trip to a server we are about to disconnect from.
    """
    if stream_manager is not None:
        # Cheapest hard stop: frames never reach `handler.receive`, so no new
        # turn can commit between here and the disconnect — including one the
        # goodbye's own echo would otherwise open while we wait for it.
        stream_manager._mic_muted = True
        logger.info("sleep quiesce: microphone muted")
    handler = getattr(stream_manager, "handler", None)
    disarm = getattr(handler, "on_external_interrupt", None)
    if callable(disarm):
        # Every barge timer stands down and the pause state is dropped, so
        # nothing can resume parked audio over a sleeping robot.
        disarm()
        logger.info("sleep quiesce: barge machine disarmed")


def wait_for_speaker_quiet(logger: logging.Logger) -> float:
    """Wait, bounded, for the goodbye to finish playing. Returns seconds waited.

    Runs on the `go_to_sleep` worker thread (`tools/go_to_sleep.py` hands the
    closure to `asyncio.to_thread`), so it blocks with `time.sleep`;
    `audio_drain.is_audible()` takes the module lock and is safe from there.

    Bounded for the boot gate's reason: a stuck drain estimate must never hold
    the robot awake. By the time this runs, the inputs are already silenced and
    the response has already finished emitting, so everything it is waiting on
    is audio that genuinely exists.
    """
    started = time.monotonic()
    deadline = started + sleep_drain_cap_s()
    while audio_drain.is_audible() and time.monotonic() < deadline:
        time.sleep(SLEEP_DRAIN_POLL_S)
    waited = time.monotonic() - started
    if audio_drain.is_audible():
        # The cap expired with audio still playing. Saying "speaker quiet" here
        # would make the journal claim the goodbye finished when the pose that
        # follows is about to cut it off (Codex round 1, P2-11).
        logger.info("sleep quiesce: drain cap reached after %.1fs with audio still playing", waited)
    else:
        logger.info("sleep quiesce: speaker quiet after %.1fs", waited)
    return waited


def run_lifecycle_sleep(deps: ToolDependencies, logger: logging.Logger) -> dict[str, object]:
    """Put Reachy to sleep from a path with no live model turn.

    Deliberately NOT the `go_to_sleep` tool. Since the instructing wave that tool
    only silences the inputs and hands the turn back to the model for a spoken
    goodbye, with the session-ending branch in `huggingface_realtime` owning the
    pose afterwards. The inactivity timeout and the shutdown path have no model
    turn to speak into and nothing downstream to run the pose, so they do both
    halves here — which is exactly what this path did before the split (Codex
    round 1, critical catch 3).

    Order is the same as everywhere else: silence, then pose. `begin_sleep` mutes
    the microphone and disarms the barge machine; `go_to_sleep` repeats that
    idempotently, drains the speaker, stops the movement manager, poses, and asks
    the daemon to stop the app.
    """
    if deps.go_to_sleep is None:
        return {"error": "go_to_sleep is unavailable in this runtime"}
    if deps.begin_sleep is not None:
        try:
            deps.begin_sleep()
        except Exception as e:  # noqa: BLE001 - quiesce is best effort; the pose is required
            logger.warning("Failed to silence Reachy before lifecycle sleep; continuing to pose: %s", e)
    try:
        return deps.go_to_sleep()
    except Exception as e:
        logger.error("Failed to put Reachy to sleep from the lifecycle path: %s", e)
        return {"error": f"go_to_sleep failed: {type(e).__name__}: {e}"}
