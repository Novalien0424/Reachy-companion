"""Move to the next clip in the trip being played (D-018, R2/R4). Filename == Tool.name.

Upstream advanced automatically from a 1 Hz polling daemon; that daemon is not
ported (see `hanova/nas.py`). This tool is the whole of the advance capability:
the user says "next one" and the session moves forward.
"""

from __future__ import annotations
import logging
from typing import Any, Dict

from reachy_companion.hanova import nas, redact, settings
from reachy_companion.home_net import AWAY, HOME, home_state, home_unknown, away_from_home
from reachy_companion.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


class NasSkip(Tool):
    """Play the next clip in the trip currently on the TV."""

    name = "nas_skip"
    description = "Skip to the next home video in this trip. 用於下一段、跳過。"
    parameters_schema: Dict[str, Any] = {"type": "object", "properties": {}, "required": []}

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Advance the trip session and cast the next clip."""
        available, reason = settings.tool_status(self.name)
        if not available:
            return settings.unavailable(reason)
        # Finding 12 + round 2 finding 3: three verdicts, three branches. On
        # UNKNOWN nothing is read, staged or cast -- "I cannot tell where I am"
        # is not permission, and it is not absence either.
        verdict = await home_state()
        if verdict == AWAY:
            return away_from_home()
        if verdict != HOME:
            return home_unknown()

        # Finding 16: look at the next clip without consuming it. A failed cast
        # must not silently eat a clip out of the trip.
        # Round 2, finding 11: the reservation now carries a token, so the
        # advance can only be committed against the same playlist generation and
        # the same cursor position it was computed from.
        video, token, error = nas.peek_next()
        if error is not None:
            return {"ok": False, "error": error}

        assert video is not None and token is not None
        logger.info("Tool call: nas_skip -> %s", redact.ident(video.get("path")))
        # confirm_cast off: a skip only makes sense mid-trip with the TV already
        # playing, and the 12 s confirmation poll would destroy its 0.3 s path.
        result = await nas.stage_and_cast(video, deps.instance_path, confirm_cast=False)
        if not result["ok"]:
            return {"ok": False, "error": result["error"]}
        if not nas.commit_next(token):
            # Another skip won, or the trip was superseded while this clip was
            # staging. The clip is on the TV either way; the playlist position
            # belongs to whoever won, so this call does not claim it.
            logger.info("nas_skip: the cursor moved under this cast; not advancing again")
            return {"ok": True, "status": "casting", "title": result["title"], "remaining": nas.remaining()}
        return {"ok": True, "status": "casting", "title": result["title"], "remaining": nas.remaining()}
