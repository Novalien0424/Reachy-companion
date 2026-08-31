"""Google Tasks as one action-enum tool. Filename == Tool.name.

Façade over `task_add` / `task_list` / `task_complete` / `task_delete`, which
keep their modules, their names, their `settings.tool_status` prerequisite rows
and their confirmation gates. See `tool_family.py` for why.
"""

from __future__ import annotations
from typing import Any, Dict, Mapping, ClassVar

from reachy_companion.tools.task_add import TaskAdd
from reachy_companion.tools.task_list import TaskList
from reachy_companion.tools.core_tools import Tool, ToolDependencies
from reachy_companion.tools.task_delete import TaskDelete
from reachy_companion.tools.tool_family import dispatch_family
from reachy_companion.tools.task_complete import TaskComplete


class Tasks(Tool):
    """Add, list, complete and delete to-do items through one tool."""

    name = "tasks"
    ACTIONS: ClassVar[Mapping[str, Tool]] = {
        "add": TaskAdd(),
        "list": TaskList(),
        "complete": TaskComplete(),
        "delete": TaskDelete(),
    }
    description = (
        "The user's to-do list: add a task, read the list, mark one done, or delete one. "
        "Use when: the user names something to remember or to do, with no fixed clock time — 「記得幫我買牛奶」"
        "「加到待辦」「我還有什麼要做的」「那個做完了」「把那項刪掉」「add a task」「what's on my list」"
        "「mark it done」. "
        "Do NOT use when: the event has a date and time and belongs on the schedule — that is calendar. "
        "Do NOT use when: the user is telling you a fact about themselves to keep — that is remember. "
        "Pick `action`: `add` needs title; `list` optionally takes include_completed; `complete` and `delete` "
        "take match, and ask the user out loud to confirm before changing anything."
    )
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "complete", "delete", "list"],
                "description": (
                    "add 新增待辦；list 查看待辦；complete 標記完成（會先口頭確認）；"
                    "delete 刪除待辦（會先口頭確認）。"
                ),
            },
            **TaskAdd.parameters_schema["properties"],
            **TaskList.parameters_schema["properties"],
            **TaskComplete.parameters_schema["properties"],
            **TaskDelete.parameters_schema["properties"],
        },
        "required": ["action"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Forward one task action to the tool that has always handled it."""
        return await dispatch_family(
            family=self.name,
            action=kwargs.get("action"),
            actions=self.ACTIONS,
            deps=deps,
            kwargs=kwargs,
        )
