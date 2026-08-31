"""Load an on-demand tool family into the session. Filename == Tool.name.

The router half of the tool diet. Reachy keeps 22 tools always ready and loads
the productivity and media families only when a turn needs them — the realtime
cookbook's Dynamic Conversation Flow pattern, which exists because "keeping tool
lists focused per conversation phase prevents the model from misselecting
tools" (docs/research-mini-tool-calling-2026-08.md §A1).
"""

from __future__ import annotations
import logging
from typing import Any, Dict

from reachy_companion.toolboxes import TOOLBOX_CATEGORIES
from reachy_companion.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


class OpenToolbox(Tool):
    """Bring one family of tools into the session, then keep going."""

    name = "open_toolbox"
    description = (
        "Load the tools for a whole area before you use them. Reachy keeps a small set of tools always "
        "ready and loads the rest on demand, so the FIRST time a turn needs one of these areas you call "
        "this, and then call the real tool in the same turn. "
        "Categories: `productivity` — the calendar, the to-do list, the cloud drive, email and Notion. "
        "`media` — the television and the household video archive on the NAS. "
        "Use when: the request needs a tool you cannot see yet. 行程／約／會議／待辦／任務／提醒／郵件／"
        "寄信／雲端／檔案／Notion → productivity；電視／影片／MV／NAS／影片檔 → media。"
        "「幫我加個行程」「加到待辦」「寄封信」「雲端有什麼」→ productivity；「電視上放那個」"
        "「找一下那年拍的影片」→ media；「add a task」「put that on the TV」→ the matching category. "
        "Do NOT use when: the tool you need is already in your list — call it directly, never open a box "
        "first. 音樂 is ALWAYS loaded: 「放首歌」「音樂關掉」 go straight to the music tool. "
        "Do NOT use when: the request is about looking, moving, emotions, remembering a person, the lights, "
        "music, searching the web, conversation modes, or going to sleep — those tools are always loaded. "
        "After this returns, the category's tools are available immediately: continue and call the one you "
        "actually need, in the same turn, without asking the user again."
    )
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "enum": list(TOOLBOX_CATEGORIES),
                "description": "media 電視／NAS 影片；productivity 行程／待辦／雲端／郵件／Notion。",
            },
        },
        "required": ["category"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Load the requested family through the injected seam."""
        if deps.open_toolbox is None:
            return {"ok": False, "error": "dynamic toolboxes are not wired on this build"}
        category = kwargs.get("category")
        if not isinstance(category, str):
            return {"ok": False, "error": "category must be a string", "categories": list(TOOLBOX_CATEGORIES)}
        logger.info("Tool call: open_toolbox category=%s", category)
        return await deps.open_toolbox(category)
