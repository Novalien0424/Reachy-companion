import logging
from typing import Any, Dict

from reachy_companion.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


class StopEmotion(Tool):
    """Stop the current emotion."""

    name = "stop_emotion"
    description = (
        "Stop the current emotion motion. "
        "Use when: the user wants a robot emotion stopped; stopping is instant with no preamble because they "
        "want it ended now. "
        "Do NOT use when: stopping dance motion is needed; that is `stop_dance`. "
        "Do NOT use when: speaker music should stop; that is `music` with `action=stop`."
    )
    needs_response = False
    parameters_schema = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Stop the current emotion."""
        logger.info("Tool call: stop_emotion")
        movement_manager = deps.movement_manager
        movement_manager.clear_move_queue()
        return {"status": "stop_queued", "stopped": "emotion"}
