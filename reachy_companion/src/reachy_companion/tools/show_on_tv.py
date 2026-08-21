"""Generate a picture and put it on the TV (D-018, R2/R4). Filename == Tool.name.

The image is generated with the OpenAI Images API, written into the LAN-served
media cache, and cast by URL through a Home Assistant script. The TV fetches the
URL itself, so the base URL must be the robot's own LAN address -- which is what
`HANOVA_MEDIA_HTTP_BASE` is for.
"""

from __future__ import annotations
import logging
from typing import Any, Dict

from reachy_companion.hanova import nas, images, redact, settings, media_store
from reachy_companion.home_net import AWAY, HOME, home_state, home_unknown, away_from_home
from reachy_companion.hanova.ha_client import ha_run_script
from reachy_companion.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)

_IMAGE_CAST_TIMEOUT_S = 60.0


class ShowOnTv(Tool):
    """Draw something and show it on the living-room TV."""

    name = "show_on_tv"
    description = "Draw a picture and show it on the TV. 用於把畫面或圖片放到電視上。"
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "request": {
                "type": "string",
                "description": "What the picture should show.",
            },
        },
        "required": ["request"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Generate an image, publish it on the LAN, and cast it to the TV."""
        # `OPENAI_API_KEY` and the live media mount are both prerequisites in
        # settings.TOOL_PREREQS, so no client is constructed to answer this
        # (findings 10, 11 and 18).
        available, reason = settings.tool_status(self.name)
        if not available:
            return settings.unavailable(reason)
        # Round 2, finding 3: UNKNOWN does no work. That matters most here --
        # the round-1 shape generated a real (billed) image and wrote it to disk
        # before discovering it could not cast it.
        verdict = await home_state()
        if verdict == AWAY:
            return away_from_home()
        if verdict != HOME:
            # Round 2, finding 3: UNKNOWN is not permission. Nothing is
            # resolved, downloaded, generated, staged or cast on this path.
            return home_unknown()

        request = str(kwargs.get("request", "")).strip()
        if not request:
            return {"ok": False, "error": "request is required"}

        logger.info("Tool call: show_on_tv request=%s", redact.text(request))
        images_dir = media_store.media_dir("images", deps.instance_path)
        generated = await images.generate_image(request, images_dir)
        if not generated["ok"]:
            return {"ok": False, "error": generated["error"]}

        url = media_store.media_url("images", str(generated["filename"]))
        if url is None:
            return {"ok": False, "error": "HANOVA_MEDIA_HTTP_BASE is not set; the TV has no URL to fetch."}

        fields: Dict[str, Any] = {"url": url, "media_type": "image/png"}
        entity = settings.cast_entity()
        if entity:
            fields["entity_id"] = entity
        cast = await ha_run_script(
            settings.ha_script_image_url(),
            fields,
            timeout_s=_IMAGE_CAST_TIMEOUT_S,
        )
        media_store.prune("images", deps.instance_path, settings.image_keep())
        if not cast["ok"]:
            logger.info("show_on_tv cast failed: %s", redact.text(cast.get("error") or ""))
            return {"ok": False, "error": "Home Assistant could not put the picture on the TV"}
        # D-018 / finding 16: this supersedes whatever trip was on the TV, so the
        # nas_skip playlist must not silently continue afterwards.
        nas.clear_session()
        return {"ok": True, "status": "casting", "url": url}
