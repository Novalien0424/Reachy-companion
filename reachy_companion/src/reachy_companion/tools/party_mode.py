"""Toggle multi-person party mode (2026-08-24). Filename == Tool.name.

In a group, most speech in the room is not addressed to the robot. Party mode
switches the realtime session to debounced barge-in plus an address gate:
Reachy stops answering ambient chatter, keeps listening for its name, and can
no longer be cut off mid-sentence by a laugh across the room. The mechanism
lives in `huggingface_realtime` (see docs/plans/party-mode-plan.md); this tool
is the voice switch.
"""

from __future__ import annotations
import logging
from typing import Any, Dict

from reachy_companion.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


class PartyMode(Tool):
    """Switch the group-conversation policy on or off by voice."""

    name = "party_mode"
    description = (
        "Change how Reachy participates while it STAYS awake: in a group conversation it answers only when "
        "addressed by name and otherwise listens quietly. 多人聊天場合開啟；結束時關閉。 "
        "Not for ending the interaction or sleeping — that is go_to_sleep."
    )
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "enabled": {
                "type": "boolean",
                "description": "true 開啟派對模式（多人場合）；false 回到一對一模式。",
            },
        },
        "required": ["enabled"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Flip the handler's party flag through the injected seam."""
        if deps.set_party_mode is None:
            return {"ok": False, "error": "party mode is not wired on this build"}
        enabled = bool(kwargs.get("enabled"))
        logger.info("Tool call: party_mode enabled=%s", enabled)
        return deps.set_party_mode(enabled)
