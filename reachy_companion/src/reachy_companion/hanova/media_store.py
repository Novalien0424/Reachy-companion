"""LAN-served media cache for the ported casting capabilities (D-018, R6).

A Chromecast fetches `media_content_id` itself, from its own network position,
so a path on the robot's disk is useless to it and `localhost` is meaningless.
Upstream solved this by writing into Home Assistant's own `www/` directory on
the Mac (`server.py:64-66`); we solve it by serving the cache off the web server
the app already runs -- the FastAPI settings app on `0.0.0.0:7860`
(`main.py:358`, `console.py:529`). No second server, no extra port.

The cache lives under the app instance directory, next to `.env`, `memory.v1.json`
and `faces.v1.json`, so the deploy ritual can reason about it in one place.
Keep-N cleanup uses upstream's caps: music 12, NAS 8.
"""

from __future__ import annotations
import logging
import tempfile
from typing import Any
from pathlib import Path

from reachy_companion.hanova import redact, settings


logger = logging.getLogger(__name__)

MEDIA_URL_PREFIX = "/hanova-media"
MEDIA_DIRNAME = "hanova_media"
KINDS: tuple[str, ...] = ("music", "nas", "images", "sfx")

# Round 2, finding 10: a file being staged by an in-progress copy. It lives here
# rather than in `nas.py` because `prune` has to know about it and `nas` already
# imports `media_store` -- the other direction would be a cycle. `nas.PART_SUFFIX`
# re-exports it so the staging code has one name for it.
PART_SUFFIX = ".part"


def media_root(instance_path: str | Path | None) -> Path:
    """Return the media cache root: the override, the instance dir, or a temp dir."""
    override = settings.media_dir_override()
    if override is not None:
        return override
    if instance_path is not None:
        return Path(instance_path) / MEDIA_DIRNAME
    return Path(tempfile.gettempdir()) / "reachy_companion_hanova_media"


def media_dir(kind: str, instance_path: str | Path | None) -> Path:
    """Return (creating if needed) the cache directory for one media *kind*."""
    if kind not in KINDS:
        raise ValueError(f"unknown media kind: {kind!r}; expected one of {KINDS}")
    directory = media_root(instance_path) / kind
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def prune(kind: str, instance_path: str | Path | None, keep: int) -> int:
    """Delete all but the *keep* most recently modified files. Returns the count removed.

    Round 2, finding 10: `*.part` files are **skipped**, not counted and not
    deleted. They are staging files belonging to a copy that is still running,
    and an LRU that deletes one destroys the download in progress.
    """
    if kind not in KINDS:
        raise ValueError(f"unknown media kind: {kind!r}; expected one of {KINDS}")
    directory = media_root(instance_path) / kind
    if not directory.is_dir():
        return 0
    try:
        files = sorted(
            (path for path in directory.iterdir() if path.is_file() and not path.name.endswith(PART_SUFFIX)),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    except OSError as exc:
        # Round 2, finding 6: an OSError renders the full path it failed on.
        logger.warning("Could not list the %s media cache: %s", kind, redact.error(exc))
        return 0
    removed = 0
    for stale in files[max(0, keep) :]:
        try:
            stale.unlink()
            removed += 1
        except OSError as exc:
            # Finding 6: the served filename is a digest, but the *directory* is
            # the instance path. Kind and shape only.
            logger.warning("Could not prune one %s cache entry: %s", kind, redact.error(exc))
    return removed


def media_url(kind: str, filename: str) -> str | None:
    """Return the LAN URL a TV can fetch, or None when no base URL is configured."""
    base = settings.media_http_base()
    if not base:
        return None
    return f"{base}{MEDIA_URL_PREFIX}/{kind}/{filename}"


# NOTE (review finding 15): there is deliberately **no** `safe_filename` helper
# here. The first draft had one that flattened a source path and then truncated
# it to 150 characters, which mapped two different NAS clips onto one served
# filename whenever their paths agreed that far -- which, for
# `<trip>/<date>/<camera>/clipNN.mp4`, they routinely do. Callers derive served
# names from a hash of a validated path instead (`nas.cast_filename`), or from an
# id they already own (`ytdlp.download_audio`, `images.generate_image`).


def mount_media_routes(app: Any, instance_path: str | Path | None) -> bool:
    """Mount the media cache as a static route on the app's settings server (R6).

    Returns True when the route is live, **and records that verdict** in
    `settings.set_media_mount_ready()` so `show_on_tv` and every `nas_*` cast
    become `unavailable` rather than handing a Chromecast a URL that this process
    is not actually serving (review round 1, finding 11). Never raises: a
    settings app that cannot mount must degrade, not abort startup.
    """
    if not hasattr(app, "mount"):
        logger.warning("Settings app cannot mount routes; hanova media will not be served.")
        settings.set_media_mount_ready(False)
        return False
    try:
        from starlette.staticfiles import StaticFiles

        root = media_root(instance_path)
        for kind in KINDS:
            (root / kind).mkdir(parents=True, exist_ok=True)
        app.mount(MEDIA_URL_PREFIX, StaticFiles(directory=str(root)), name="hanova-media")
    except Exception as exc:  # noqa: BLE001 - a failed mount must degrade, not abort
        # Round 2, finding 6: `logger.exception` prints a traceback whose frames
        # carry the instance path, and the message interpolated the root too.
        # The shape is enough to act on; the path is not ours to publish.
        logger.warning("Failed to mount the hanova media cache at %s: %s", MEDIA_URL_PREFIX, redact.error(exc))
        settings.set_media_mount_ready(False)
        return False
    settings.set_media_mount_ready(True)
    logger.info(
        "hanova media served at %s (base URL configured: %s, kinds: %d)",
        MEDIA_URL_PREFIX,
        bool(settings.media_http_base()),
        len(KINDS),
    )
    return True


__all__ = [
    "KINDS",
    "MEDIA_DIRNAME",
    "MEDIA_URL_PREFIX",
    "PART_SUFFIX",
    "media_dir",
    "media_root",
    "media_url",
    "mount_media_routes",
    "prune",
]
