"""Home Control Skill via the Home Assistant REST API (D-005). Filename == Tool.name.

The model never invents entity ids. ``HA_ENTITIES`` (a JSON object mapping a
spoken friendly name to an HA entity id) is the allowlist, and both the schema
``target`` enum and the description are built from it at construction time.
That is sound because ``initialize_tools()`` instantiates tools *after*
``main.py`` has loaded the instance ``.env``; a later change to ``HA_ENTITIES``
takes effect on the next ``initialize_tools(force=True)`` rebuild.

Nothing here may raise: construction happens inside ``_build_tool_registry()``,
which is not exception-guarded, so a malformed env var would otherwise abort
startup. Bad config degrades to an empty allowlist instead.
"""

import os
import json
import logging
from typing import Any, Dict

import httpx

from reachy_companion.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)

# The only HA service names this Skill will ever put in a URL path.
SUPPORTED_ACTIONS = ("turn_on", "turn_off", "toggle")

_REQUEST_TIMEOUT_S = 8.0


def _entities() -> Dict[str, str]:
    """Read the spoken-name -> entity-id allowlist from ``HA_ENTITIES``."""
    raw = (os.getenv("HA_ENTITIES") or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        logger.warning("HA_ENTITIES is not valid JSON; home_control has no devices.")
        return {}
    if not isinstance(parsed, dict):
        logger.warning("HA_ENTITIES must be a JSON object; home_control has no devices.")
        return {}
    return {str(name): str(entity_id) for name, entity_id in parsed.items()}


class HomeControl(Tool):
    """Turn a configured smart-home device on or off through Home Assistant."""

    name = "home_control"
    description = "Control a smart-home device via Home Assistant."  # replaced in __init__
    parameters_schema: Dict[str, Any] = {}  # replaced in __init__

    def __init__(self) -> None:
        """Build the description and schema from the configured device allowlist."""
        names = sorted(_entities())
        self.description = (
            "Control a smart-home device via Home Assistant (on/off/toggle). "
            "Use when the user asks to control something in the house. "
            f"Known devices: {', '.join(names) if names else '(none configured)'}."
        )
        self.parameters_schema = {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": list(SUPPORTED_ACTIONS)},
                "target": {
                    "type": "string",
                    "enum": names,
                    "description": "The device to control, by its known name",
                },
            },
            "required": ["action", "target"],
        }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Resolve the friendly name to an entity id and call the HA service."""
        action = str(kwargs.get("action", ""))
        if action not in SUPPORTED_ACTIONS:
            return {
                "ok": False,
                "error": f"unsupported action: {action}",
                "known_actions": list(SUPPORTED_ACTIONS),
            }

        target = str(kwargs.get("target", ""))
        devices = _entities()
        entity_id = devices.get(target)
        if entity_id is None:
            return {"ok": False, "error": f"unknown device: {target}", "known_devices": sorted(devices)}

        domain, _, object_id = entity_id.partition(".")
        if not domain or not object_id:
            return {"ok": False, "error": f"malformed entity id for {target}: {entity_id}"}

        base_url = (os.getenv("HA_URL") or "").strip().rstrip("/")
        token = (os.getenv("HA_TOKEN") or "").strip()
        if not base_url or not token:
            return {"ok": False, "error": "Home Assistant is not configured; set HA_URL and HA_TOKEN."}

        logger.info("Tool call: home_control action=%s target=%s entity=%s", action, target, entity_id)
        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_S) as client:
                response = await client.post(
                    f"{base_url}/api/services/{domain}/{action}",
                    json={"entity_id": entity_id},
                    headers={"Authorization": f"Bearer {token}"},
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("home_control: Home Assistant call failed for %s: %s", entity_id, exc)
            return {"ok": False, "error": str(exc) or type(exc).__name__}
        return {"ok": True, "action": action, "target": target, "entity_id": entity_id}
