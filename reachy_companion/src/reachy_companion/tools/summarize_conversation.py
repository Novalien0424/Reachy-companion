"""Read back a summary of what 紀錄模式 recorded. Filename == Tool.name.

The summarizer itself lives in `record_mode`; this is the voice surface. The
result is a verbatim envelope (research doc §C3): a raw string plus a separate
"say it exactly" instruction is the shape the mini tier paraphrases, so the
authoritative text is a named field and the flag travels with it.

`lines` counts what the room log holds right now, which is what was captured
rather than a whole meeting: recording only runs while 紀錄模式 is on, and a
settings or backend restart mid-visit keeps the log while dropping the mode, so
the log can hold two stretches of the same visit with an unmarked gap between
them. The summarizer's prompt is told that; nothing here tries to guess where
the seams are.
"""

from __future__ import annotations
import logging
from typing import Any, Dict

from reachy_companion.record_mode import summarize_record_log
from reachy_companion.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


class SummarizeConversation(Tool):
    """Summarize the running record of this visit and hand it back verbatim."""

    name = "summarize_conversation"
    description = (
        "Summarize everything Reachy has heard and said in this visit, using the running record kept while "
        "紀錄模式 (record mode) is on. "
        "Use when: the user asks for a summary or a recap of what was said — 「幫我總結」「剛剛講了什麼」"
        "「做個會議記錄」「唸一下重點」「summarize what we said」「recap the meeting」. "
        "Do NOT use when: the user asks what YOU remember about a person or a fact — that is the memory tools. "
        "Do NOT use when: the user asks what you can see — that is camera or look_around. "
        "The result is an envelope: when `speak_verbatim` is true, read `summary_text` out loud EXACTLY as "
        "returned, in 台灣中文, and add nothing of your own."
    )
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Summarize `deps.record_log` and return the verbatim envelope."""
        lines = len(deps.record_log)
        logger.info("Tool call: summarize_conversation over %d recorded line(s)", lines)
        summary = await summarize_record_log(deps, client=kwargs.get("client"))
        return {"summary_text": summary, "speak_verbatim": True, "lines": lines}
