"""Image generation for show_on_tv (D-018, R2).

Upstream's `show_on_tv` fired an HA `rest_command` at the operator's own Hermes
gateway, which called an image model and copied the result into Home Assistant's
web root (`ha-media-output/SKILL.md:526-545`). None of that is ours to port. We
generate the image here with the OpenAI Images API -- reusing the `OPENAI_API_KEY`
the app already needs for the realtime backend -- write it into the LAN-served
media cache, and cast its URL.
"""

from __future__ import annotations
import os
import uuid
import base64
import logging
from typing import Any, Dict
from pathlib import Path

from reachy_companion.hanova import redact, settings


logger = logging.getLogger(__name__)

# Landscape, because it lands on a television.
_IMAGE_SIZE = "1536x1024"


def build_client() -> Any | None:
    """Return an AsyncOpenAI client, or None when no API key is configured.

    The caller **must** use it as `async with build_client() as client:` so the
    connection pool is closed on every path, success or failure (finding 18).

    This module deliberately exposes **no availability predicate**: whether
    `show_on_tv` can run is decided once, by `OPENAI_API_KEY` in
    `settings.TOOL_PREREQS`, so nothing ever builds a client -- with its own HTTP
    connection pool -- merely to answer a boolean (finding 18), and no caller can
    route around the ordered first-unmet-key contract.
    """
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        return None
    try:
        from openai import AsyncOpenAI

        return AsyncOpenAI(api_key=api_key)
    except Exception:  # noqa: BLE001 - a missing SDK must not raise here
        logger.warning("Could not build an OpenAI client for image generation.")
        return None


async def generate_image(prompt: str, dest_dir: Path) -> Dict[str, Any]:
    """Generate one image for *prompt* into *dest_dir*. Never raises."""
    client = build_client()
    if client is None:
        return {"ok": False, "path": None, "filename": None, "error": "OPENAI_API_KEY is not set"}

    try:
        async with client:
            response = await client.images.generate(
                model=settings.image_model(),
                prompt=prompt,
                size=_IMAGE_SIZE,
                n=1,
            )
    except Exception as exc:  # noqa: BLE001 - an API failure is tool output
        # Finding 7: the API error body can echo the prompt back. Log the shape,
        # return a fixed reason.
        logger.warning("Image generation failed: %s", redact.error(exc))
        return {
            "ok": False,
            "path": None,
            "filename": None,
            "error": "the picture could not be generated right now",
        }

    try:
        encoded = response.data[0].b64_json
        raw = base64.b64decode(encoded)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Image response could not be decoded: %s", redact.error(exc))
        return {"ok": False, "path": None, "filename": None, "error": "the image response was unreadable"}

    filename = f"img_{uuid.uuid4().hex[:12]}.png"
    destination = Path(dest_dir) / filename
    try:
        destination.write_bytes(raw)
    except OSError as exc:
        # Round 2, finding 6: an OSError renders the path it failed to write,
        # which is the instance directory. Neither the log nor the tool result
        # may carry it.
        logger.warning("Could not write the generated image: %s", redact.error(exc))
        return {"ok": False, "path": None, "filename": None, "error": "the image could not be saved"}

    return {"ok": True, "path": str(destination), "filename": filename, "error": None}
