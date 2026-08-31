"""Google Drive as one action-enum tool. Filename == Tool.name.

Façade over `drive_list` / `drive_trash` / `drive_upload`, which keep their
modules, their names, their `settings.tool_status` prerequisite rows and their
confirmation gates. See `tool_family.py` for why.
"""

from __future__ import annotations
from typing import Any, Dict, Mapping, ClassVar

from reachy_companion.tools.core_tools import Tool, ToolDependencies
from reachy_companion.tools.drive_list import DriveList
from reachy_companion.tools.drive_trash import DriveTrash
from reachy_companion.tools.tool_family import dispatch_family
from reachy_companion.tools.drive_upload import DriveUpload


class Drive(Tool):
    """List, trash and upload Drive files through one tool."""

    name = "drive"
    ACTIONS: ClassVar[Mapping[str, Tool]] = {
        "list": DriveList(),
        "trash": DriveTrash(),
        "upload": DriveUpload(),
    }
    description = (
        "The user's cloud drive: list recent files, move one to the trash, or upload one. "
        "Use when: the user talks about files in the cloud — 「雲端有什麼檔案」「幫我上傳」「把那個檔案丟掉」"
        "「what's in my drive」「upload that」「delete that file」. "
        "Do NOT use when: the user means a photo you should LOOK at right now — that is camera or look_around. "
        "Do NOT use when: the user means a video to play on the TV — that is tv or nas. "
        "Pick `action`: `list` optionally takes limit; `trash` takes file_id; `upload` takes name. Both `trash` "
        "and `upload` ask the user out loud to confirm first."
    )
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "trash", "upload"],
                "description": (
                    "list 列出雲端檔案；trash 把檔案丟到垃圾桶（會先口頭確認）；"
                    "upload 拍照上傳（會先口頭確認）。"
                ),
            },
            # `trash` goes last on purpose. `confirm` is the one name two of
            # these three share, and the last spread wins: DriveTrash describes
            # it as "the exact item read back to them", which is true of both
            # actions, where DriveUpload says "the upload", which is not true of
            # a trash. Nothing is invented here -- only which existing sentence
            # survives a collision.
            **DriveList.parameters_schema["properties"],
            **DriveUpload.parameters_schema["properties"],
            **DriveTrash.parameters_schema["properties"],
        },
        "required": ["action"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Forward one drive action to the tool that has always handled it."""
        return await dispatch_family(
            family=self.name,
            action=kwargs.get("action"),
            actions=self.ACTIONS,
            deps=deps,
            kwargs=kwargs,
        )
