"""Async Home Assistant REST helper for the ported capabilities (D-018).

Upstream used a blocking `urllib` call inside a single-threaded stdin loop
(`server.py:131-147`), where one slow request froze every other tool including
`stop_music`. Our tools are asyncio tasks on the realtime loop, so this uses
`httpx.AsyncClient` and never blocks the audio path.

Every function returns a result dict and never raises: a tool failure must reach
the model as tool output, exactly like `tools/home_control.py` does.
"""

from __future__ import annotations
import time
import asyncio
import logging
from typing import Any, Dict, Callable
from datetime import datetime, timezone
from urllib.parse import quote

import httpx

from reachy_companion.hanova import redact, settings


logger = logging.getLogger(__name__)

# States a media_player reports once a cast has actually landed. "on" covers
# androidtv-style entities that never report "playing" for an app launch.
_CAST_ACTIVE_STATES = frozenset({"playing", "buffering", "paused", "on", "casting"})
# 2026-10-31 timing: a NAS clip on an idle receiver played 1.1 s after the call,
# but a 2 s poll held the tool (and Reachy's "it's on") until 2.8 s. Each poll
# is one local HA read, so 0.5 s costs at most 24 reads over the 12 s budget.
_CAST_POLL_S = 0.5


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


# States with something on screen that a stop can end. "on" and "casting" are not
# here on purpose: an idle receiver has nothing to stop, and stopping it would
# report a stop that ended nothing.
_PLAYBACK_STATES = frozenset({"playing", "buffering", "paused"})
_STOP_SETTLE_S = 3.0
_STOP_POLL_S = 1.0


def is_playback_state(state: str) -> bool:
    """Return whether a media_player state still shows a video (paused counts)."""
    return state in _PLAYBACK_STATES


def media_state_and_title(reply: Dict[str, Any]) -> tuple[str, str | None]:
    """Return a state read's ``(state, media title)``; "unknown" when unreadable."""
    payload = reply.get("result") if reply.get("ok") else None
    if not isinstance(payload, dict):
        return "unknown", None
    attributes = payload.get("attributes")
    title = attributes.get("media_title") if isinstance(attributes, dict) else None
    return str(payload.get("state") or "unknown"), (str(title) if title else None)


def media_position_s(reply: Dict[str, Any], now: datetime | None = None) -> float | None:
    """Return where the TV is in its video, in seconds, from one state read; None when it does not say.

    HA reports `media_position` as of `media_position_updated_at`. Paused, that
    is where it is; playing, the time since then is added.
    """
    payload = reply.get("result") if reply.get("ok") else None
    if not isinstance(payload, dict) or not isinstance(payload.get("attributes"), dict):
        return None
    attributes = payload["attributes"]
    position = attributes.get("media_position")
    if not isinstance(position, (int, float)) or isinstance(position, bool):
        return None
    if payload.get("state") not in _MOVING_STATES:
        return max(0.0, float(position))
    updated = attributes.get("media_position_updated_at")
    try:
        stamp = datetime.fromisoformat(str(updated))
    except ValueError:
        return max(0.0, float(position))
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    elapsed = ((now or datetime.now(timezone.utc)) - stamp).total_seconds()
    return max(0.0, float(position) + max(0.0, elapsed))


def media_content_id(reply: Dict[str, Any]) -> str | None:
    """Return the `media_content_id` of one state read (a cast URL, a YouTube id), or None."""
    payload = reply.get("result") if reply.get("ok") else None
    attributes = payload.get("attributes") if isinstance(payload, dict) else None
    value = attributes.get("media_content_id") if isinstance(attributes, dict) else None
    return str(value) if value else None


def media_app_name(reply: Dict[str, Any]) -> str | None:
    """Return the cast app of one state read ("Default Media Receiver", "YouTube"), or None."""
    payload = reply.get("result") if reply.get("ok") else None
    attributes = payload.get("attributes") if isinstance(payload, dict) else None
    value = attributes.get("app_name") if isinstance(attributes, dict) else None
    return str(value) if value else None


async def _settle(entity_id: str, done: Callable[[str], bool], last: str) -> tuple[bool, str]:
    """Poll the TV for up to `_STOP_SETTLE_S` until `done(state)`; return (done, last state seen).

    An unreadable poll keeps the last state seen: only a state the TV reported
    can end the wait.
    """
    deadline = time.monotonic() + _STOP_SETTLE_S
    while time.monotonic() < deadline:
        await asyncio.sleep(_STOP_POLL_S)
        now = await ha_get_state(entity_id, timeout_s=8.0)
        if now.get("ok"):
            last, _ = media_state_and_title(now)
            if done(last):
                return True, last
    return False, last


async def _call_and_log(service: str, entity_id: str, action: str) -> None:
    """Call one media_player service; a failed call is logged, and the caller reads the TV back anyway."""
    reply = await ha_call_service("media_player", service, {"entity_id": entity_id}, timeout_s=15.0)
    if not reply.get("ok"):
        logger.info("TV %s: %s answered %s; reading the TV back anyway", action, service,
                    reply.get("status_code") or "no reply")


async def stop_tv_playback(entity_id: str) -> Dict[str, Any]:
    """Stop what the TV is playing and read it back (2026-10-31). Never raises.

    Returns ``{"result": ..., "title": <media title or None>, "state": <last seen>,
    "content_id": ..., "position_s": ...}`` where result is ``stopped``,
    ``not_playing``, ``still_playing`` or ``unreachable``. HA answers 200 whether
    or not the receiver obeyed, so only the state afterwards counts, and it is
    read back even when the call itself failed or timed out (D-040's lesson).
    `content_id` and `position_s` come from the read before the stop, so the
    caller can keep the place (`tv_session.save_bookmark`); None when unknown.

    Two steps, because receivers differ: `media_stop` ends a Default Media
    Receiver clip, but the TV's YouTube app ignores `media_stop` (it kept
    reading `buffering`). If playback has not ended within `_STOP_SETTLE_S`,
    `turn_off` closes the casting app, which ended YouTube within a second. On
    this TV that is the same command as home_control's 「把電視關掉」. (A probe
    on 2026-10-31 measured that the YouTube app does obey `media_pause`; see
    `pause_tv_playback`.)
    """
    unknown: Dict[str, Any] = {"content_id": None, "position_s": None}
    if not entity_id:
        return {"result": "unreachable", "title": None, "state": None, **unknown}
    before = await ha_get_state(entity_id, timeout_s=8.0)
    if not before.get("ok"):
        return {"result": "unreachable", "title": None, "state": None, **unknown}
    state, title = media_state_and_title(before)
    if state not in _PLAYBACK_STATES:
        return {"result": "not_playing", "title": None, "state": state, **unknown}
    place = {"content_id": media_content_id(before), "position_s": media_position_s(before)}
    last = state
    for service in ("media_stop", "turn_off"):
        await _call_and_log(service, entity_id, "stop")
        ended, last = await _settle(entity_id, lambda now: now not in _PLAYBACK_STATES, last)
        if ended:
            logger.info("TV stop: playback ended after %s", service)
            return {"result": "stopped", "title": title, "state": last, **place}
    return {"result": "still_playing", "title": title, "state": last, **place}


# Playback with the picture moving. `buffering` is in it because this TV's
# YouTube app reports `buffering` as its normal playing state (probe, 2026-10-31).
_MOVING_STATES = frozenset({"playing", "buffering"})


async def pause_tv_playback(entity_id: str) -> Dict[str, Any]:
    """Pause what the TV is playing and read it back (2026-10-31 plan, W2). Never raises.

    Returns ``{"result": ..., "title": ..., "state": <last seen>}`` where result
    is ``paused``, ``already_paused``, ``not_playing`` (nothing on screen),
    ``ended`` (it left playback instead of pausing), ``still_playing`` or
    ``unreachable``. A probe on 2026-10-31 measured `media_pause` → `paused` on
    both receivers this TV runs (the Default Media Receiver held its position),
    but HA answers 200 either way, so a receiver that ignores the pause is
    reported, not assumed.
    """
    if not entity_id:
        return {"result": "unreachable", "title": None, "state": None}
    before = await ha_get_state(entity_id, timeout_s=8.0)
    if not before.get("ok"):
        return {"result": "unreachable", "title": None, "state": None}
    state, title = media_state_and_title(before)
    if state == "paused":
        return {"result": "already_paused", "title": title, "state": state}
    if state not in _PLAYBACK_STATES:
        return {"result": "not_playing", "title": None, "state": state}
    await _call_and_log("media_pause", entity_id, "pause")
    settled, last = await _settle(entity_id, lambda now: now not in _MOVING_STATES, state)
    if not settled:
        return {"result": "still_playing", "title": title, "state": last}
    if last == "paused":
        logger.info("TV pause: the TV reads paused")
        return {"result": "paused", "title": title, "state": last}
    return {"result": "ended", "title": title, "state": last}


async def resume_tv_playback(entity_id: str) -> Dict[str, Any]:
    """Resume a paused TV video and read it back (2026-10-31 plan, W2). Never raises.

    Returns ``{"result": ..., "title": ..., "state": <last seen>}`` where result
    is ``resumed``, ``already_playing``, ``not_paused`` (nothing paused on
    screen), ``ended`` (it left playback instead), ``still_paused`` or
    ``unreachable``. `media_play` read back as `playing` from the Default Media
    Receiver and as `buffering` from the YouTube app, which is how that app
    plays, so both count as resumed. A resumed result also carries what the TV
    shows (`content_id`, `app`): a video paused before Reachy slept is still on
    the TV after the app forgot it.
    """
    if not entity_id:
        return {"result": "unreachable", "title": None, "state": None}
    before = await ha_get_state(entity_id, timeout_s=8.0)
    if not before.get("ok"):
        return {"result": "unreachable", "title": None, "state": None}
    state, title = media_state_and_title(before)
    if state in _MOVING_STATES:
        return {"result": "already_playing", "title": title, "state": state}
    if state != "paused":
        return {"result": "not_paused", "title": None, "state": state}
    await _call_and_log("media_play", entity_id, "resume")
    settled, last = await _settle(entity_id, lambda now: now != "paused", state)
    if not settled:
        return {"result": "still_paused", "title": title, "state": last}
    if last in _MOVING_STATES:
        logger.info("TV resume: the TV reads %s", last)
        return {"result": "resumed", "title": title, "state": last,
                "content_id": media_content_id(before), "app": media_app_name(before)}
    return {"result": "ended", "title": title, "state": last}


# A seek read back within this window of its target counts as landed: the
# position moves on while the TV plays, and a restart reads near zero.
_SEEK_EARLY_S = 10.0
_SEEK_LATE_S = 30.0
# Below this, playing from the start is playing from where it stopped.
_SEEK_MIN_S = 2.0


async def _seek_landed(entity_id: str, target_s: float) -> bool | None:
    """Poll the TV's position until it is near *target_s*; None when it never reports one."""
    deadline = time.monotonic() + _STOP_SETTLE_S
    seen: float | None = None
    while time.monotonic() < deadline:
        await asyncio.sleep(_STOP_POLL_S)
        position = media_position_s(await ha_get_state(entity_id, timeout_s=8.0))
        if position is None:
            continue
        seen = position
        if target_s - _SEEK_EARLY_S <= position <= target_s + _SEEK_LATE_S:
            return True
    return None if seen is None else False


async def recast_from_bookmark(entity_id: str, mark: Dict[str, Any]) -> Dict[str, Any]:
    """Play a stopped video again where it stopped (2026-10-31, follow-up). Never raises.

    *mark* is a `tv_session` bookmark. A nas clip is re-cast through the
    video-URL script with the fields `nas._cast_prepared_url` sends, a YouTube
    video through the YouTube script with the fields `play_video` sends. The
    cast is confirmed (dispatch is not display), then `media_seek` goes to the
    kept position and the position is read back, with one more seek if the
    first did not land (a receiver can drop a seek sent while it buffers).

    Returns ``{"result": ..., "title": ..., "state": ..., "from": ...}``:
    ``resumed`` (``from`` is ``where it stopped`` or ``the beginning``, or None
    when the TV reports no position), ``recast_failed`` (HA refused the
    script) or ``not_started`` (the TV shows no playback).
    """
    title = mark.get("title")
    if mark.get("kind") == "youtube":
        script, fields = settings.ha_script_youtube(), {"youtube_id": mark.get("recast")}
    else:
        script, fields = settings.ha_script_video_url(), {"url": mark.get("recast"), "title": title or ""}
    if entity_id:
        fields["entity_id"] = entity_id
    cast = await ha_run_script(script, fields)
    if not cast.get("ok"):
        logger.info("TV resume from the kept place: the cast script answered %s", cast.get("status_code") or "no reply")
        return {"result": "recast_failed", "title": title, "state": None, "from": None}
    check = await confirm_cast_started(entity_id, timeout_s=settings.cast_confirm_timeout_s())
    if check["confirmed"] is False:
        return {"result": "not_started", "title": title, "state": check["state"], "from": None}
    target = float(mark.get("position_s") or 0.0)
    landed: bool | None = True
    if target >= _SEEK_MIN_S:
        for _attempt in range(2):
            reply = await ha_call_service(
                "media_player", "media_seek", {"entity_id": entity_id, "seek_position": target}, timeout_s=15.0
            )
            if not reply.get("ok"):
                logger.info("TV seek answered %s; reading the position back anyway", reply.get("status_code") or "no reply")
            landed = await _seek_landed(entity_id, target)
            if landed is not False:
                break
    where = None if landed is None else ("where it stopped" if landed else "the beginning")
    logger.info("TV resume from the kept place: %s", where or "position unknown")
    return {"result": "resumed", "title": title, "state": check["state"], "from": where}


def tv_not_responding(state: str | None) -> Dict[str, Any]:
    """Return the honest tool result for a dispatched cast the TV never picked up."""
    return {
        "ok": False,
        "status": "tv_not_responding",
        "tv_state": state or "unknown",
        "error": (
            "the cast was sent, but the TV shows no playback; it may be off or offline. Say the picture "
            "probably did not come up and ask them to check that the TV is on, then try again; do not say "
            "it is playing."
        ),
    }
