import asyncio
import logging
from typing import Any

from reachy_companion.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


class GoToSleep(Tool):
    """Put Reachy to sleep and stop the current app."""

    name = "go_to_sleep"
    description = (
        "End the interaction entirely: Reachy says goodbye, stops, and rests. Use when you are sure the user "
        "wants Reachy gone, off, asleep, or the conversation over — in any wording or language. The judgment: "
        "they want you to STOP being active, not to keep participating in a different way (that is "
        "set_conversation_mode: 一對一聊天模式 / 多人聊天模式 / 紀錄模式). "
        "Do NOT use when: the user only wants you quiet for a moment — that is wait_for_user. "
        "Do NOT use when: the user wants you to listen differently, record, or stop recording — that is "
        "set_conversation_mode. "
        "Do not use for idle turns, sleepy emotions, silence, or ambiguous requests."
    )
    needs_response = False
    parameters_schema = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> dict[str, Any]:
        """Silence, wait for the goodbye to finish, then put Reachy to sleep."""
        if deps.go_to_sleep is None:
            return {"error": "go_to_sleep is unavailable in this runtime"}

        logger.info("Tool call: go_to_sleep")
        # Order is the fix (Codex round 2, 2a-6). Silence first: the wait below
        # can take seconds, and a live microphone through it means a repeated
        # 「睡覺吧」 or the goodbye's own echo opens a turn nobody will answer.
        if deps.begin_sleep is not None:
            deps.begin_sleep()
        # Then wait, because the closure below hands off to a worker thread that
        # measures whether the speaker has gone quiet — and measuring that
        # before the response has finished emitting is measuring nothing
        # (Codex round 1, P2-10).
        if deps.wait_for_reply_finished is not None:
            if not await deps.wait_for_reply_finished():
                logger.warning("go_to_sleep: the goodbye response did not finish in time; sleeping anyway")
        try:
            return await asyncio.to_thread(deps.go_to_sleep)
        except Exception as e:
            logger.error("go_to_sleep failed: %s", e)
            return {"error": f"go_to_sleep failed: {type(e).__name__}: {e}"}
