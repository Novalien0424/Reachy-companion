"""Google Calendar as one action-enum tool. Filename == Tool.name.

Façade over `calendar_add` / `calendar_list` / `calendar_delete`, which keep
their modules, their names, their `settings.tool_status` prerequisite rows and
their confirmation gate. See `tool_family.py` for why.

Naming: this module sits next to the standard library's `calendar`. Python 3's
absolute imports keep them apart -- `reachy_companion.tools.calendar` never
shadows `import calendar` -- and nothing in `src/` imports the stdlib one.
Inside the package, always spell this one out in full.
"""

from __future__ import annotations
from typing import Any, Dict, Mapping, ClassVar

from reachy_companion.tools.core_tools import Tool, ToolDependencies
from reachy_companion.tools.tool_family import dispatch_family
from reachy_companion.tools.calendar_add import CalendarAdd
from reachy_companion.tools.calendar_list import CalendarList
from reachy_companion.tools.calendar_delete import CalendarDelete


class Calendar(Tool):
    """Add, list and delete calendar events through one tool."""

    name = "calendar"
    ACTIONS: ClassVar[Mapping[str, Tool]] = {
        "add": CalendarAdd(),
        "list": CalendarList(),
        "delete": CalendarDelete(),
    }
    description = (
        "The user's calendar: add an event, read what is coming up, or delete an event. "
        "Use when: the user talks about their schedule, an appointment or a meeting — 「幫我加個行程」"
        "「下週三下午三點跟醫生」「我這禮拜有什麼安排」「把星期五那個會取消」「add it to my calendar」"
        "「what's on my calendar」「cancel that meeting」. "
        "Do NOT use when: the user means a to-do or a reminder with no time on the clock — that is tasks. "
        "Do NOT use when: the user asks about today's date or the current time — just answer. "
        "Pick `action`: `add` needs summary, start and end; `list` optionally takes days and search; "
        "`delete` takes match, and asks the user out loud to confirm before it removes anything."
    )
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "delete", "list"],
                "description": "add 新增行程；list 查看行程；delete 刪除行程（會先口頭確認）。",
            },
            **CalendarAdd.parameters_schema["properties"],
            **CalendarList.parameters_schema["properties"],
            **CalendarDelete.parameters_schema["properties"],
        },
        "required": ["action"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Forward one calendar action to the tool that has always handled it."""
        return await dispatch_family(
            family=self.name,
            action=kwargs.get("action"),
            actions=self.ACTIONS,
            deps=deps,
            kwargs=kwargs,
        )
