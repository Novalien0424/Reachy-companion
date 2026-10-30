"""Switch conversation mode by voice (2026-08-31). Filename == Tool.name.

Replaces the boolean `party_mode` tool. One tool, three postures: 多人聊天模式
(the old party mode, unchanged, and the mode Reachy boots into), 一對一聊天模式,
and 紀錄模式 (quiet scribe + spoken summary). The mechanism lives in
`huggingface_realtime`; this tool is the voice switch.
"""

from __future__ import annotations
import logging
from typing import Any, Dict

from reachy_companion.tools.core_tools import Tool, ToolDependencies
from reachy_companion.conversation_mode import MODE_VALUES


logger = logging.getLogger(__name__)


class SetConversationMode(Tool):
    """Switch between one-on-one, group and record conversation modes."""

    name = "set_conversation_mode"
    description = (
        "Change how you take part in the conversation while staying awake. one_on_one: one person; "
        "answer normally (the start mode). group: several people; answer only when someone says your "
        "name. record: a meeting; listen silently, take notes, and speak only when named, mainly to give "
        "a summary with summarize_conversation. Use when: the user asks to change how you listen or take "
        "part. Do NOT use when: they want you to stop or sleep — that is go_to_sleep; or quiet for a "
        "moment only — that is wait_for_user. After switching, confirm the new mode in one short "
        "sentence."
    )
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "enum": list(MODE_VALUES),
                "description": "one_on_one 一對一聊天模式（開機預設）；group 多人聊天模式；record 紀錄模式。",
            },
        },
        "required": ["mode"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Switch the handler's conversation mode through the injected seam.

        Awaited: the seam does not return until the server has applied the new
        instructions and tool list, so the confirmation sentence the model
        speaks next is spoken under the mode it is confirming.
        """
        if deps.set_conversation_mode is None:
            return {"ok": False, "error": "conversation modes are not wired on this build"}
        mode = kwargs.get("mode")
        if not isinstance(mode, str) or mode not in MODE_VALUES:
            # The Chinese labels are what the user says and what the description
            # teaches; the ARGUMENT is one of the three enum values, and a guess
            # is corrected here rather than round-tripped through the handler.
            return {
                "ok": False,
                "error": f"mode must be one of {', '.join(MODE_VALUES)}",
                "modes": list(MODE_VALUES),
            }
        logger.info("Tool call: set_conversation_mode mode=%s", mode)
        return await deps.set_conversation_mode(mode)
