import logging
from typing import Any, Dict

from reachy_companion.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


class HeadTracking(Tool):
    """Enable or disable following the user's face with the head."""

    name = "head_tracking"
    description = (
        "Enable or disable following the user's face with the head. "
        "Use when: the user asks for ongoing face following or to stop following. "
        "Do NOT use when: the user asks for a one-off look or turn; use `look_around` to inspect, or `move_head` "
        "for movement only."
    )
    needs_response = False
    parameters_schema = {
        "type": "object",
        "properties": {
            "enabled": {
                "type": "boolean",
                "description": "True to start following the user's face, false to stop.",
            },
        },
        "required": ["enabled"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Toggle head tracking, refusing anything that is not a real boolean."""
        enabled = kwargs.get("enabled")
        if not isinstance(enabled, bool):
            # `bool("false")` is True. On a platform with no structured-output
            # guarantee that coercion is how "stop following me" silently became
            # "follow me", so the value is refused with both options named.
            return {"error": "enabled must be true or false (a boolean, not a string)"}
        logger.info("Tool call: head_tracking enabled=%s", enabled)
        deps.movement_manager.set_head_tracking(enabled)
        # `tracking_requested`, not "following": `set_head_tracking` posts a
        # command to the movement manager's queue and the worker swallows tracking
        # errors, so at this instant nobody is being followed yet. `head_tracking`
        # carries the state as a named field the model may cite.
        return {"status": "tracking_requested", "head_tracking": enabled}
