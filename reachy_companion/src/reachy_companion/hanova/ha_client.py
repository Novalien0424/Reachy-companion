"""Async Home Assistant REST helper for the ported capabilities (D-018).

Upstream used a blocking `urllib` call inside a single-threaded stdin loop
(`server.py:131-147`), where one slow request froze every other tool including
`stop_music`. Our tools are asyncio tasks on the realtime loop, so this uses
`httpx.AsyncClient` and never blocks the audio path.

Every function returns a result dict and never raises: a tool failure must reach
the model as tool output, exactly like `tools/home_control.py:111-113` does.
"""

from __future__ import annotations
import logging
from typing import Any, Dict

import httpx

from reachy_companion.hanova import redact, settings


logger = logging.getLogger(__name__)


async def _request(method: str, path: str, payload: Dict[str, Any] | None, timeout_s: float) -> Dict[str, Any]:
    """Perform one authenticated Home Assistant REST call."""
    base_url = settings.ha_url()
    token = settings.ha_token()
    if not base_url or not token:
        return {"ok": False, "error": "Home Assistant is not configured; set HA_URL and HA_TOKEN."}

    url = f"{base_url}{path}"
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            response = await client.request(
                method,
                url,
                json=payload,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            )
    except httpx.HTTPError as exc:
        # Finding 7: an httpx error string embeds the full URL, which carries the
        # house's LAN address. Callers get the shape, not the address. Round 2,
        # finding 6: the *path* is not safe either -- it ends in the operator's
        # own scripts.yaml entry name -- so it is a digest here too. Round 3,
        # finding 3: no word list either -- the httpx class name (ConnectTimeout /
        # ConnectError / ReadTimeout) already IS the shape, and `redact.error`
        # reads any errno straight off the exception.
        logger.warning("Home Assistant %s %s failed: %s", method, redact.ident(path), redact.error(exc))
        return {"ok": False, "error": redact.error(exc)}

    if not (200 <= response.status_code < 300):
        logger.warning("Home Assistant %s %s -> HTTP %d", method, redact.ident(path), response.status_code)
        return {
            "ok": False,
            "error": f"Home Assistant returned HTTP {response.status_code}",
            "status_code": response.status_code,
        }

    try:
        result: Any = response.json()
    except ValueError:
        result = None
    return {"ok": True, "result": result}


async def ha_call_service(
    domain: str,
    service: str,
    data: Dict[str, Any],
    timeout_s: float = 30.0,
) -> Dict[str, Any]:
    """Call `<domain>.<service>` with *data* as the service payload."""
    return await _request("POST", f"/api/services/{domain}/{service}", data, timeout_s)


async def ha_run_script(script_name: str, data: Dict[str, Any], timeout_s: float = 60.0) -> Dict[str, Any]:
    """Run the Home Assistant script `script.<script_name>` with *data* as its fields."""
    return await _request("POST", f"/api/services/script/{script_name}", data, timeout_s)


async def ha_get_state(entity_id: str, timeout_s: float = 15.0) -> Dict[str, Any]:
    """Read one entity's current state object."""
    return await _request("GET", f"/api/states/{entity_id}", None, timeout_s)
