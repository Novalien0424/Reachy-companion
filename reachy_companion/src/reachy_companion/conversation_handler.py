from __future__ import annotations
import time
import asyncio
import logging
from abc import ABC, abstractmethod
from typing import ClassVar, TypeAlias
from collections import deque
from collections.abc import Callable

import numpy as np
from numpy.typing import NDArray

from reachy_companion.streaming import AdditionalOutputs, AsyncStreamHandler, wait_for_item
from reachy_companion.idle_policy import start_idle_tool_call
from reachy_companion.tools.core_tools import ToolDependencies, get_tool_specs
from reachy_companion.tools.background_tool_manager import BackgroundToolManager


logger = logging.getLogger(__name__)


AudioFrame: TypeAlias = tuple[int, NDArray[np.int16]]
HandlerOutput: TypeAlias = AudioFrame | AdditionalOutputs | None
QueueItem: TypeAlias = AudioFrame | AdditionalOutputs


class ConversationHandler(AsyncStreamHandler, ABC):
    """Shared app handler contract and idle behavior for realtime conversation backends."""

    IDLE_BEHAVIOR_THRESHOLD_S: ClassVar[float] = 180.0

    deps: ToolDependencies
    tool_manager: BackgroundToolManager
    output_queue: asyncio.Queue[QueueItem]
    last_activity_time: float
    last_idle_behavior_time: float
    _activity_observer: Callable[[str], None] | None = None
    _transcript_observer: Callable[[str, str, bool], None] | None = None
    # --- solo pause-then-decide barge-in (Task 8) ---------------------------
    # Class-level defaults, because backends build partial handlers (`__new__`)
    # and `emit()` must behave exactly as it did before Task 8 on those.
    _barge_paused: bool = False
    _held_audio: "deque[QueueItem] | None" = None

    def __init__(self) -> None:
        """Initialize the stream handler and shared idle/activity tracking."""
        super().__init__()
        self.last_activity_time = time.monotonic()
        self.last_idle_behavior_time = self.last_activity_time

    def set_activity_observer(self, observer: Callable[[str], None] | None) -> None:
        """Attach or detach an activity observer. Pass None to clear."""
        self._activity_observer = observer

    def set_transcript_observer(self, observer: Callable[[str, str, bool], None] | None) -> None:
        """Attach/detach a transcript observer, called (role, text, final)."""
        self._transcript_observer = observer

    def _emit_transcript(self, role: str, text: str, final: bool = True) -> None:
        """Forward one transcript chunk to the observer, if attached."""
        observer = self._transcript_observer
        if observer is not None and text:
            try:
                observer(role, text, final)
            except Exception:
                logger.debug("transcript observer raised (ignored)", exc_info=True)

    def _mark_activity(self, reason: str) -> None:
        """Record non-idle conversation activity for the idle timer."""
        self.last_activity_time = time.monotonic()
        logger.debug("last activity time updated to %s (%s)", self.last_activity_time, reason)
        if self._activity_observer is not None:
            try:
                self._activity_observer(reason)
            except Exception:
                logger.debug("activity observer raised (ignored)", exc_info=True)

    def _idle_behavior_ready(self) -> bool:
        """Return whether idle behavior may run now. Backends can add guards."""
        return True

    async def emit(self) -> HandlerOutput:
        """Emit the next queued output, triggering local idle behavior when due.

        Task 8: while a solo barge-in decision is pending, the reply's audio is
        withheld rather than played — but the output queue is *mixed*, so the
        pause dequeues as normal and only diverts audio frames into
        `_held_audio`. Starving the queue instead would stall every transcript
        and tool notification behind the pause. A rollback replays what was
        held, in order, ahead of anything queued since.
        """
        held = self._held_audio
        if held and not self._barge_paused:
            return held.popleft()
        now = time.monotonic()
        idle_duration = now - self.last_activity_time
        idle_behavior_duration = now - self.last_idle_behavior_time
        if (
            idle_duration > self.IDLE_BEHAVIOR_THRESHOLD_S
            and idle_behavior_duration > self.IDLE_BEHAVIOR_THRESHOLD_S
            and self._idle_behavior_ready()
            and self.deps.movement_manager.is_idle()
        ):
            try:
                await self.send_idle_signal(idle_duration)
            except Exception as e:
                logger.warning("Idle tool skipped (connection closed?): %s", e)
                return None
            self.last_idle_behavior_time = now
        handler_output = await wait_for_item(self.output_queue)
        if self._barge_paused and isinstance(handler_output, tuple):
            if held is None:
                held = self._held_audio = deque()
            held.append(handler_output)
            return None
        return handler_output

    def _idle_tool_exclusions(self) -> list[str]:
        """Tool names the idle picker may not choose from. Base: nothing is hidden.

        Overridden by backends that hide tools from the live session, so the
        idle policy selects from the same surface the model has.
        """
        return []

    async def send_idle_signal(self, idle_duration: float) -> None:
        """Run a locally selected idle tool without sending an idle turn to the model."""
        if not self._is_connected():
            logger.debug("No active session; cannot run idle tool")
            return

        # Final review, C4: the same exclusion list the session was built with.
        # Selecting from the unfiltered registry let 紀錄模式 break a quiet
        # recording with a dance, an emotion or a head turn — movement the mode
        # exists to suppress, chosen by a picker that never learned about modes.
        available_tool_names = {spec["name"] for spec in get_tool_specs(self._idle_tool_exclusions())}
        await start_idle_tool_call(
            deps=self.deps,
            tool_manager=self.tool_manager,
            output_queue=self.output_queue,
            available_tool_names=available_tool_names,
            idle_duration=idle_duration,
        )

    @abstractmethod
    def _is_connected(self) -> bool:
        """Return whether the backend session/connection is currently open."""
        ...

    @abstractmethod
    async def start_up(self) -> None:
        """Start the realtime handler."""
        ...

    @abstractmethod
    async def shutdown(self) -> None:
        """Shut down the realtime handler."""
        ...

    @abstractmethod
    async def receive(self, frame: AudioFrame) -> None:
        """Receive an input audio frame."""
        ...

    @abstractmethod
    async def apply_personality(self, profile: str | None) -> str:
        """Apply a personality profile."""
        ...

    @abstractmethod
    async def get_available_voices(self) -> list[str]:
        """Return voices available for the active backend."""
        ...

    @abstractmethod
    def get_current_voice(self) -> str:
        """Return the current voice."""
        ...

    @abstractmethod
    async def change_voice(self, voice: str) -> str:
        """Change the current voice."""
        ...

    @abstractmethod
    async def say(self, text: str) -> None:
        """Make the robot speak ``text`` now (injected turn; not verbatim TTS).

        The backend is speech-to-speech, so ``text`` is an instruction the
        model voices, not a guaranteed-literal string. Raises if no session is
        open.
        """
        ...
