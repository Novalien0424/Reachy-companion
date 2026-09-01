import logging
from typing import Any, Dict

from reachy_companion.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


class StopDance(Tool):
    """Stop the current dance move."""

    name = "stop_dance"
    description = "Stop the current dance move"
    needs_response = False
    parameters_schema = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Stop the current dance move."""
        logger.info("Tool call: stop_dance")
        movement_manager = deps.movement_manager
        movement_manager.clear_move_queue()
        # `stop_queued`: `clear_move_queue` is a queued command too, so the dance
        # is not yet stopped when this returns.
        return {"status": "stop_queued", "stopped": "dance"}
