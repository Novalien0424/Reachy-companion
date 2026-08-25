"""No-op tool: the model calls this to end a turn without speaking.

OpenAI's realtime prompting guide ships this exact pattern for silence,
background noise, TV audio, and side conversation — an affirmative action
that ends the turn is far more reliable than asking the model to do
nothing. Every call is a countable journal line.
"""

import logging
from typing import Any, Dict

from reachy_companion.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


class WaitForUser(Tool):
    """Silently end the turn for non-addressed or unclear audio."""

    needs_response = False

    name = "wait_for_user"
    description = (
        "Call this when the latest audio does not need a spoken response, "
        "such as silence, background noise, music, TV audio, side "
        "conversation, or speech not addressed to the assistant. This tool "
        "helps end the turn without a spoken reply."
    )
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Log the no-response decision and return a fixed acknowledgement."""
        logger.info("wait_for_user: model chose not to respond")
        return {"ok": True, "status": "waiting"}
