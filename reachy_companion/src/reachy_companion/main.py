"""Entrypoint for the Reachy Mini conversation app."""

from __future__ import annotations
import sys
import time
import asyncio
import logging
import argparse
import threading
from typing import TYPE_CHECKING, Any, Optional
from pathlib import Path
from collections.abc import Callable, Awaitable

from fastapi import FastAPI, Request, Response

from reachy_mini import ReachyMini, ReachyMiniApp
from reachy_companion import app_lifecycle
from reachy_companion.utils import (
    parse_args,
    setup_logger,
    log_connection_troubleshooting,
)
from reachy_companion.audio.envparse import env_int, env_bool


if TYPE_CHECKING:
    from reachy_companion.console import LocalStream


# Port of the daemon's FastAPI server, matching the SDK's own default
# (`reachy_mini.ReachyMini.__init__`). Overridable per run via
# `REACHY_DAEMON_PORT` — see `_daemon_port()`.
DEFAULT_DAEMON_PORT = 8000

# Hard cap on how long startup waits for remote MCP discovery (D-004). Sized to
# sit just above mcp_servers' own worst case (2 x 8 s attempts + one 2 s backoff
# = 18 s), so an ordinary dead server still reports itself properly and this
# budget only fires for a server that hangs past its socket timeouts.
MCP_DISCOVERY_BUDGET_S = 20.0


def _start_inactivity_timeout_thread(
    timeout_minutes: float,
    stream_manager: LocalStream,
    logger: logging.Logger,
    app_stop_event: threading.Event | None,
    go_to_sleep: Callable[[], dict[str, Any]] | None = None,
) -> threading.Thread:
    """Start a daemon that puts the app to sleep after inactivity."""
    timeout_seconds = timeout_minutes * 60.0

    def poll_inactivity_timeout() -> None:
        logger.info("App inactivity timeout enabled: %.1f minutes.", timeout_minutes)
        while app_stop_event is None or not app_stop_event.is_set():
            elapsed = stream_manager.seconds_since_activity()
            if elapsed >= timeout_seconds:
                logger.info("No activity for %.1f minutes; going to sleep.", elapsed / 60.0)
                try:
                    if go_to_sleep is not None:
                        go_to_sleep()
                    else:
                        stream_manager.close()
                except Exception as e:
                    logger.error("Error while going to sleep after inactivity timeout: %s", e)
                    try:
                        stream_manager.close()
                    except Exception as close_error:
                        logger.error("Error while closing stream manager after inactivity timeout: %s", close_error)
                return
            time.sleep(1.0)

    thread = threading.Thread(target=poll_inactivity_timeout, daemon=True)
    thread.start()
    return thread


def _daemon_port() -> int:
    """Return the daemon's FastAPI port, overridable with ``REACHY_DAEMON_PORT``.

    The SDK's default is 8000, which is the right answer on the robot and the
    only answer the shipped app ever needs. On this Windows dev machine 8000
    belongs to the Reachy Mini Control desktop app, so the mockup-sim dev daemon
    runs on 8001 (`scripts/dev_daemon.ps1`, D-008) and the app has to be pointed
    at it. Unset — every non-dev run — this returns the SDK default, so the
    kwarg we pass is the value the SDK would have chosen anyway.

    Malformed or out-of-range values degrade to the default with a warning
    (`env_int`), the same contract the audio and VAD knobs use: one bad `.env`
    line must not stop the app from starting.
    """
    return env_int("REACHY_DAEMON_PORT", DEFAULT_DAEMON_PORT, lo=1, hi=65535)


def _discover_remote_mcp_tools(logger: logging.Logger, budget_s: float = MCP_DISCOVERY_BUDGET_S) -> list[str]:
    """Run the async MCP discovery (D-004) from this synchronous startup path.

    `run()` is only ever called synchronously, so no event loop is running here,
    but `ReachyCompanion.run` installs a fresh loop on this thread that the
    console stack later uses. `asyncio.run()` would close its own loop and leave
    the thread with no current loop, so the discovery gets its own worker thread.

    `budget_s` is the hard cap on how long startup waits. `mcp_servers` bounds its
    own retries, but httpx applies its timeouts per operation, so a server that
    trickles bytes can outlast them; the worker is therefore a daemon and the join
    has a timeout, so neither a hung server nor a stuck socket can block startup
    or keep the interpreter alive at exit. A worker that finishes late still
    registers its tools: `register_extra_tool` clears the registry signature, so
    the next `get_tool_specs()` (one per realtime session) rebuilds and picks
    them up.
    """
    from reachy_companion.mcp_servers import register_mcp_tools

    tool_names: list[str] = []

    def discover() -> None:
        nonlocal tool_names
        tool_names = asyncio.run(register_mcp_tools())

    thread = threading.Thread(target=discover, name="mcp-discovery", daemon=True)
    thread.start()
    thread.join(timeout=budget_s)

    if thread.is_alive():
        logger.warning(
            "Remote MCP discovery exceeded its %.1fs startup budget; starting without MCP tools. "
            "If it finishes later its tools join the next conversation session.",
            budget_s,
        )
        return []

    if tool_names:
        logger.info("Registered %d remote MCP tool(s): %s", len(tool_names), tool_names)
    else:
        logger.info("No remote MCP tools registered.")
    return tool_names


def main() -> None:
    """Entrypoint for the Reachy Mini conversation app."""
    args, _ = parse_args()
    if args.command == "tool-spaces":
        from reachy_companion.tool_spaces import handle_tool_spaces_command

        logger = setup_logger(args.debug)
        try:
            raise SystemExit(handle_tool_spaces_command(args))
        except Exception as exc:
            logger.error("tool-spaces command failed: %s", exc)
            raise SystemExit(1) from exc
    run(args)


def run(
    args: argparse.Namespace,
    robot: ReachyMini = None,
    app_stop_event: Optional[threading.Event] = None,
    settings_app: Optional[FastAPI] = None,
    instance_path: Optional[str] = None,
) -> None:
    """Run the Reachy Mini conversation app."""
    # Putting these dependencies here makes the dashboard faster to load when the conversation app is installed
    from reachy_companion.moves import MovementManager
    from reachy_companion.config import config as runtime_config
    from reachy_companion.config import (
        set_instance_path,
        resolve_app_timeout_minutes,
        refresh_runtime_config_from_env,
    )
    from reachy_companion.startup_settings import (
        StartupSettings,
        load_startup_settings_into_runtime,
    )

    logger = setup_logger(args.debug)
    logger.info("Starting Reachy Mini Conversation App")
    set_instance_path(instance_path)
    startup_settings = StartupSettings()

    if instance_path is not None:
        try:
            from dotenv import load_dotenv

            env_path = Path(instance_path) / ".env"
            if env_path.exists():
                load_dotenv(dotenv_path=str(env_path), override=True)
                refresh_runtime_config_from_env()
                logger.info("Loaded instance configuration from %s", env_path)
        except Exception as e:
            logger.warning("Failed to load instance configuration: %s", e)

        try:
            startup_settings = load_startup_settings_into_runtime(instance_path)
        except Exception as e:
            logger.warning("Failed to load startup settings: %s", e)

    # D-016: name the persona source once, after the instance .env is loaded (it
    # may carry PERSONA_FILE) and before the first session can use it. Each
    # antenna wake starts the app fresh, so this line is the operator's proof
    # that the persona.md they edited on the robot is the one in play.
    from reachy_companion.persona import log_persona_source

    log_persona_source(instance_path)

    logger.info(
        "Configured OpenAI realtime backend, transcription language: %s",
        runtime_config.REALTIME_TRANSCRIPTION_LANGUAGE,
    )

    from reachy_companion.console import LocalStream
    from reachy_companion.tools.core_tools import ToolDependencies, initialize_tools
    from reachy_companion.conversation_handler import ConversationHandler

    if robot is None:
        try:
            robot_kwargs: dict[str, Any] = {"port": _daemon_port()}
            if args.robot_name is not None:
                robot_kwargs["robot_name"] = args.robot_name

            logger.info(
                "Initializing ReachyMini on daemon port %d (SDK will auto-detect appropriate backend)",
                robot_kwargs["port"],
            )
            robot = ReachyMini(**robot_kwargs)

        except TimeoutError as e:
            logger.error(f"Connection timeout: Failed to connect to Reachy Mini daemon. Details: {e}")
            log_connection_troubleshooting(logger, args.robot_name)
            sys.exit(1)

        except ConnectionError as e:
            logger.error(f"Connection failed: Unable to establish connection to Reachy Mini. Details: {e}")
            log_connection_troubleshooting(logger, args.robot_name)
            sys.exit(1)

        except Exception as e:
            logger.error(f"Unexpected error during robot initialization: {type(e).__name__}: {e}")
            logger.error("Please check your configuration and try again.")
            sys.exit(1)

    app_lifecycle.wake_up_if_sleeping(robot, logger)

    movement_manager = MovementManager(current_robot=robot)

    deps = ToolDependencies(
        reachy_mini=robot,
        movement_manager=movement_manager,
        instance_path=instance_path,
        camera_enabled=not args.no_camera,
    )

    def build_handler(startup_voice: Optional[str] = None) -> ConversationHandler:
        """Build an OpenAI realtime handler for the current runtime config (D-002).

        The single construction site for the conversation backend: `LocalStream`
        rebuilds handlers through this same factory, so the settings UI cannot
        resurrect the Hugging Face backend behind our back.
        """
        from reachy_companion.openai_realtime import OpenAIRealtimeHandler, realtime_model

        logger.info("Using OpenAI realtime handler (model %s)", realtime_model())
        handler = OpenAIRealtimeHandler(
            deps,
            instance_path=instance_path,
            startup_voice=startup_voice,
        )
        # Party mode's voice switch reaches the live handler through deps; the
        # rewire here (not at deps construction) is what keeps the seam correct
        # across handler rebuilds by the settings UI (voice changes).
        deps.set_party_mode = handler.set_party_mode
        return handler

    handler = build_handler(startup_settings.voice)

    stream_manager: LocalStream | None = None
    own_ui_server = None

    effective_settings_app = settings_app
    if args.ui and settings_app is None:
        effective_settings_app = FastAPI()

        @effective_settings_app.middleware("http")
        async def _no_cache(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
            """Serve everything no-store so browsers don't keep stale UI modules."""
            response = await call_next(request)
            response.headers["Cache-Control"] = "no-store"
            return response

    stream_manager = LocalStream(
        handler,
        robot,
        settings_app=effective_settings_app,
        instance_path=instance_path,
        handler_factory=build_handler,
        startup_voice=startup_settings.voice,
    )

    # The page is served immediately, so the API must be live before the slow startup work below.
    if effective_settings_app is not None:
        stream_manager._init_settings_ui_if_needed()

    go_to_sleep_lock = threading.Lock()
    go_to_sleep_requested = threading.Event()

    def go_to_sleep_and_stop_app() -> dict[str, Any]:
        """Put Reachy to sleep, then stop the current app."""
        if not go_to_sleep_lock.acquire(blocking=False):
            return {"status": "already_requested"}

        try:
            if go_to_sleep_requested.is_set():
                return {"status": "already_requested"}
            go_to_sleep_requested.set()

            logger.info("Going to sleep before stopping conversation app.")
            sleep_error: str | None = None

            try:
                robot.disable_wobbling()
            except Exception as e:
                logger.debug("Error disabling wobbling before sleep: %s", e)

            movement_manager.stop(reset_to_neutral=False)

            try:
                robot.goto_sleep()
            except Exception as e:
                sleep_error = f"{type(e).__name__}: {e}"
                logger.error("Failed to move Reachy Mini to sleep pose: %s", e)

            stop_current_app_requested = False
            if app_stop_event is None or not app_stop_event.is_set():
                stop_current_app_requested = app_lifecycle.request_stop_current_app(robot, logger)
            local_stop_requested = True
            if app_stop_event is not None:
                app_stop_event.set()
            else:
                try:
                    stream_manager.close()
                except Exception as e:
                    local_stop_requested = False
                    logger.error("Error while closing stream manager after go_to_sleep: %s", e)

            result: dict[str, Any] = {
                "status": "sleeping" if sleep_error is None else "stop_requested",
                "stop_current_app_requested": stop_current_app_requested,
                "local_stop_requested": local_stop_requested,
            }
            if sleep_error is not None:
                result["error"] = f"go_to_sleep movement failed: {sleep_error}"
            return result
        finally:
            go_to_sleep_lock.release()

    deps.go_to_sleep = go_to_sleep_and_stop_app

    def run_go_to_sleep_tool() -> dict[str, Any]:
        return app_lifecycle.run_go_to_sleep_tool(deps, logger)

    if args.ui and settings_app is None and effective_settings_app is not None:
        import uvicorn

        own_ui_server = uvicorn.Server(
            uvicorn.Config(effective_settings_app, host="0.0.0.0", port=7860, log_level="warning")
        )
        threading.Thread(target=own_ui_server.run, daemon=True, name="ui-server").start()
        logger.info("Web UI available at http://localhost:7860")

    # D-018 / R5: one INFO line per ported tool family, so a deploy can be read
    # off the log instead of guessed at. Runs after the instance .env is loaded
    # and before the registry is built, so the verdicts describe this boot.
    from reachy_companion.hanova.settings import log_family_status

    log_family_status()

    # US-07 / D-004: discover remote MCP tools before the registry is built, so
    # the first initialize_tools() already includes them. The persistent seam in
    # core_tools keeps them registered even if the registry is rebuilt later.
    _discover_remote_mcp_tools(logger)

    try:
        initialize_tools(instance_path=instance_path)
    except Exception as e:
        logger.error("Failed to initialize tools: %s", e)
        sys.exit(1)

    # US-10 / D-013: face memory. Built after the tool registry so the recognizer
    # is in `deps` before the first session can dispatch `who_is_this`, and warmed
    # on a daemon thread because a cold build reads ~37 MB of SFace off eMMC —
    # far more than the wake-time budget the greeting hook allows itself.
    # FACE_MEMORY_ENABLED=0 is the kill switch: no model, no warm-up, and both
    # tools plus the wake check answer "unavailable".
    from reachy_companion.face_id import FaceRecognizer

    face_memory_enabled = env_bool("FACE_MEMORY_ENABLED", True)
    face_recognizer = FaceRecognizer(instance_path, enabled=face_memory_enabled)
    deps.face_recognizer = face_recognizer
    if not face_memory_enabled:
        logger.info("Face memory disabled by FACE_MEMORY_ENABLED; recognition tools will report unavailable.")
    elif not deps.camera_enabled:
        logger.info("Face memory has no camera (--no-camera); skipping model warm-up.")
    else:
        face_recognizer.start_warmup()

    # Each async service → its own thread/loop
    movement_manager.start()
    # US-02: follow the user's face from startup, without waiting for the model to
    # call the head_tracking tool. Exactly the enable the tool issues
    # (tools/head_tracking.py:34): queued on the movement manager, whose worker
    # runs robot.start_head_tracking(weight=1.0) (moves.py:370-384). The queue put
    # is non-blocking and the worker swallows tracking errors, so this is safe
    # even when the robot cannot track (e.g. no face, camera disabled).
    movement_manager.set_head_tracking(True)
    # Audio-reactive head motion is driven by the daemon's wobbler, which
    # taps the media pipeline at push_audio_sample. The console stream pushes
    # assistant audio through that pipeline directly.
    robot.enable_wobbling()

    timeout_minutes = resolve_app_timeout_minutes()
    if timeout_minutes is not None:
        _start_inactivity_timeout_thread(timeout_minutes, stream_manager, logger, app_stop_event, run_go_to_sleep_tool)

    def poll_stop_event() -> None:
        """Poll the stop event to allow graceful shutdown.

        Deliberately does NOT put the robot to sleep: an external stop
        (mobile app, dashboard, app switch) means "stop this app", not
        "power the robot down" — the daemon returns it to the neutral
        pose afterwards, awake and ready for the next app. Sleeping is
        reserved for the explicit paths (the voice go_to_sleep tool and
        the inactivity timeout).
        """
        if app_stop_event is not None:
            app_stop_event.wait()

        logger.info("App stop event detected, shutting down...")
        try:
            stream_manager.close()
        except Exception as e:
            logger.error(f"Error while closing stream manager: {e}")

    if app_stop_event:
        threading.Thread(target=poll_stop_event, daemon=True).start()

    try:
        stream_manager.launch()
    except KeyboardInterrupt:
        logger.info("Keyboard interruption in main thread... closing server.")
    finally:
        if own_ui_server is not None:
            own_ui_server.should_exit = True

        # Stop the motion writes without changing the robot's posture. If
        # the shutdown came from the voice go_to_sleep tool the robot is
        # already in the sleep pose; on a plain stop it stays awake and
        # the daemon returns it to neutral once the process exits.
        movement_manager.stop(reset_to_neutral=False)
        try:
            robot.disable_wobbling()
        except Exception as e:
            logger.debug(f"Error disabling wobbling during shutdown: {e}")

        # Ensure media is explicitly closed before disconnecting
        try:
            robot.media.close()
        except Exception as e:
            logger.debug(f"Error closing media during shutdown: {e}")

        # prevent connection to keep alive some threads
        robot.client.disconnect()
        time.sleep(1)
        logger.info("Shutdown complete.")


class ReachyCompanion(ReachyMiniApp):  # type: ignore[misc]
    """Reachy Mini Apps entry point for the conversation app."""

    custom_app_url = "http://0.0.0.0:7860/"
    dont_start_webserver = False

    def run(self, reachy_mini: ReachyMini, stop_event: threading.Event) -> None:
        """Run the Reachy Mini conversation app."""
        asyncio.set_event_loop(asyncio.new_event_loop())

        args, _ = parse_args()

        instance_path = self._get_instance_path().parent
        run(
            args,
            robot=reachy_mini,
            app_stop_event=stop_event,
            settings_app=self.settings_app,
            instance_path=instance_path,
        )


if __name__ == "__main__":
    app = ReachyCompanion()
    try:
        app.wrapped_run()
    except KeyboardInterrupt:
        app.stop()
