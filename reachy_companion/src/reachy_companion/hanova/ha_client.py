"""Async Home Assistant REST helper for the ported capabilities (D-018).

Upstream used a blocking `urllib` call inside a single-threaded stdin loop
(`server.py:131-147`), where one slow request froze every other tool including
`stop_music`. Our tools are asyncio tasks on the realtime loop, so this uses
`httpx.AsyncClient` and never blocks the audio path.

Every function returns a result dict and never raises: a tool failure must reach
the model as tool output, exactly like `tools/home_control.py:111-113` does.
"""

from __future__ import annotations
import time
import asyncio
import logging
from typing import Any, Dict
from urllib.parse import quote

import httpx

from reachy_companion.hanova import redact, settings


logger = logging.getLogger(__name__)

# States a media_player reports once a cast has actually landed. "on" covers
# androidtv-style entities that never report "playing" for an app launch.
_CAST_ACTIVE_STATES = frozenset({"playing", "buffering", "paused", "on", "casting"})
_CAST_POLL_S = 2.0


def _segment(value: str) -> str:
    """Percent-encode one URL path segment, escaping the separators too.

    Review finding 4: every segment below is model-supplied (an entity id, a
    domain, a service, the script name). With `safe=""` a value like
    `a/b?c` cannot climb out of its own segment and re-target the request at a
    different endpoint or smuggle in a query string. `quote` leaves the
    unreserved set (letters, digits, `_.-~`) alone, so ordinary ids such as
    `media_player.tv` are unchanged on the wire.
    """
    return quote(value, safe="")


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
    except Exception as exc:  # noqa: BLE001 - the never-raises contract is the point
        # Review finding 3: this catches `Exception`, not `httpx.HTTPError`. The
        # contract is "never raises", and two non-HTTPError failures are reachable
        # from a model-supplied argument alone: a `TypeError` when `data` holds
        # something `json.dumps` cannot encode, and an `httpx.InvalidURL` from a
        # malformed base URL. Either one would otherwise escape into the tool
        # dispatcher instead of reaching the model as tool output.
        # `asyncio.CancelledError` derives from `BaseException`, so an interrupted
        # turn still cancels cleanly rather than being swallowed here.
        #
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
    return await _request("POST", f"/api/services/{_segment(domain)}/{_segment(service)}", data, timeout_s)


async def ha_run_script(script_name: str, data: Dict[str, Any], timeout_s: float = 60.0) -> Dict[str, Any]:
    """Run the Home Assistant script `script.<script_name>` with *data* as its fields."""
    return await _request("POST", f"/api/services/script/{_segment(script_name)}", data, timeout_s)


async def ha_get_state(entity_id: str, timeout_s: float = 15.0) -> Dict[str, Any]:
    """Read one entity's current state object."""
    return await _request("GET", f"/api/states/{_segment(entity_id)}", None, timeout_s)


async def confirm_cast_started(entity_id: str, *, timeout_s: float) -> Dict[str, Any]:
    """Poll the cast target until it shows playback, or report what it showed.

    2026-08-24, operator report: every cast tool answered "casting" the moment
    Home Assistant accepted the *script call*, so a TV that was off produced a
    confident success while showing nothing. HA accepts a script run regardless
    of whether the cast target exists, is on, or ever starts the app — the only
    honest signal is the media_player entity's state afterwards.

    Returns ``{"confirmed": True | False | None, "state": <last seen or None>}``.
    ``None`` means unverifiable (no cast entity configured, or verification
    disabled with a non-positive timeout) — callers keep the legacy behavior.
    """
    if not entity_id or timeout_s <= 0:
        return {"confirmed": None, "state": None}
    deadline = time.monotonic() + timeout_s
    last_state: str | None = None
    while True:
        result = await ha_get_state(entity_id, timeout_s=10.0)
        if result.get("ok"):
            payload = result.get("result")
            state = payload.get("state") if isinstance(payload, dict) else None
            last_state = str(state) if state else "unknown"
            if last_state in _CAST_ACTIVE_STATES:
                return {"confirmed": True, "state": last_state}
        if time.monotonic() >= deadline:
            logger.info("cast unconfirmed: target state stayed %s", redact.ident(last_state or "unreadable"))
            return {"confirmed": False, "state": last_state}
        await asyncio.sleep(_CAST_POLL_S)


def tv_not_responding(state: str | None) -> Dict[str, Any]:
    """Return the honest tool result for a dispatched cast the TV never picked up."""
    return {
        "ok": False,
        "status": "tv_not_responding",
        "tv_state": state or "unknown",
        "error": "the cast was sent, but the TV shows no playback; it may be off or offline",
    }
