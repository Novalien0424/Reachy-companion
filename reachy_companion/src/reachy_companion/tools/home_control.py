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

Success is what the device reads afterwards, not HA's answer. HA answers 200
to a service call whether or not the device moved, so the tool reads the state
before the call and polls it after, the way ``ha_client.confirm_cast_started``
does for casts. Requests go through ``hanova.ha_client``, which redacts the
house's LAN address out of every error.
"""

import os
import json
import time
import asyncio
import logging
from typing import Any, Dict

from reachy_companion.hanova import redact, settings, ha_client
from reachy_companion.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)

# The only HA service names this Skill will ever put in a URL path.
SUPPORTED_ACTIONS = ("turn_on", "turn_off", "toggle")

# The cast TV took 9.0 s to answer turn_on in the 2026-10-30 probe; the old 8 s
# limit reported a timeout for a TV that did come on.
_SERVICE_TIMEOUT_S = 20.0
_STATE_TIMEOUT_S = 8.0
_CONFIRM_TIMEOUT_S = 8.0
_CONFIRM_POLL_S = 1.0

# A device reads like this when it is off. A TV that is asleep reads standby.
_OFF_STATES = frozenset({"off", "standby"})
# HA cannot reach the device, so a command would change nothing.
_UNREACHABLE_STATES = frozenset({"unavailable", "unknown"})


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
            "Switch a home device on or off through Home Assistant. "
            "Use when: the user asks to turn a device in the house on or off, like 「把電視關掉」. "
            "Do NOT use when: the user wants something played — on the TV that is youtube or nas, on your "
            "speaker that is music. "
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

        if not settings.ha_url() or not settings.ha_token():
            return {
                "ok": False,
                "error": "Home Assistant is not set up on this robot (HA_URL, HA_TOKEN); "
                "say you cannot control home devices right now.",
            }

        logger.info("Tool call: home_control action=%s target=%s entity=%s", action, target, redact.ident(entity_id))
        before = await ha_client.ha_get_state(entity_id, timeout_s=_STATE_TIMEOUT_S)
        if not before.get("ok"):
            if before.get("status_code") == 404:
                return {
                    "ok": False,
                    "error": f"Home Assistant has no device behind {target}, so nothing was switched; its "
                    "setup needs fixing. Tell the person you cannot reach that device.",
                }
            return _not_responding(target, before)
        state_before = _state_of(before)
        if state_before in _UNREACHABLE_STATES:
            return {
                "ok": False,
                "action": action,
                "target": target,
                "state": state_before,
                "error": f"Home Assistant cannot reach {target} (it reads {state_before}), so nothing was "
                "switched. Tell the person it seems to be offline or unplugged; do not say it worked.",
            }

        is_on_before = state_before not in _OFF_STATES
        if action == "toggle":
            want_on = not is_on_before
        else:
            want_on = action == "turn_on"
        service = "turn_on" if want_on else "turn_off"

        sent = await ha_client.ha_call_service(domain, service, {"entity_id": entity_id}, timeout_s=_SERVICE_TIMEOUT_S)
        if not sent.get("ok"):
            return _not_responding(target, sent)

        state = await _settled_state(entity_id, want_on)
        if _reads_as(state, want_on):
            result: Dict[str, Any] = {"ok": True, "action": action, "target": target, "state": state}
            if action != "toggle" and is_on_before == want_on:
                result["was_already"] = "on" if want_on else "off"
            return result
        word = "on" if want_on else "off"
        return {
            "ok": False,
            "action": action,
            "target": target,
            "state": state or "unreadable",
            "error": f"Home Assistant took the command, but {target} still reads {state or 'unreadable'}. "
            f"Tell the person it did not turn {word}, and suggest checking its power or switch; "
            "do not say it worked.",
        }


def _reads_as(state: str | None, want_on: bool) -> bool:
    """Whether *state* is a readable state on the wanted side (on, or off/standby)."""
    if state is None or state in _UNREACHABLE_STATES:
        return False
    return (state not in _OFF_STATES) == want_on


def _state_of(reply: Dict[str, Any]) -> str:
    """Read the state string out of a successful ``ha_get_state`` reply."""
    payload = reply.get("result")
    state = payload.get("state") if isinstance(payload, dict) else None
    return str(state) if state else "unknown"


async def _settled_state(entity_id: str, want_on: bool) -> str | None:
    """Poll until the device reads the wanted side, or return the last state seen."""
    deadline = time.monotonic() + _CONFIRM_TIMEOUT_S
    last: str | None = None
    while True:
        reply = await ha_client.ha_get_state(entity_id, timeout_s=_STATE_TIMEOUT_S)
        if reply.get("ok"):
            last = _state_of(reply)
            if _reads_as(last, want_on):
                return last
        if time.monotonic() >= deadline:
            logger.info("home_control: %s unconfirmed, still %s", redact.ident(entity_id), last or "unreadable")
            return last
        await asyncio.sleep(_CONFIRM_POLL_S)


def _not_responding(target: str, reply: Dict[str, Any]) -> Dict[str, Any]:
    """Build the result for a failed HA request; ``ha_client`` already redacted the cause."""
    return {
        "ok": False,
        "target": target,
        "error": f"Home Assistant is not responding ({reply.get('error') or 'no answer'}), so {target} was "
        "not switched. Tell the person the home system is not answering right now.",
    }
