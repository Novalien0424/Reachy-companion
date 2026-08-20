"""Operator-editable persona override, read from the app instance directory (D-016).

The app ships one locked persona (`profiles/_reachy_companion_locked_profile/
profile.md`, D-001). That file lives inside the wheel, so changing the character
used to mean building and redeploying. `persona.md` in the *instance* directory —
the same directory that holds `.env`, `memory.v1.json` and `faces.v1.json` — is
the operator's editable copy: edit it over SSH, restart the app (an antenna wake
starts it fresh), and the new persona is live.

Rules, in one place because every one of them is a fallback:

- No `persona.md` → the built-in locked profile, byte for byte as before.
- A valid `persona.md` → its body becomes the system prompt, and each of
  `voice` / `greeting` / `default_tools` it declares replaces the built-in
  value. Fields it omits keep the built-in value.
- Anything wrong with it — unreadable, bad TOML, unknown key, empty body — logs
  a WARNING naming the problem and falls back to the built-in profile *whole*.
  A persona is never half-applied.

`PERSONA_FILE` (absolute path) moves the lookup somewhere else; the rules above
are unchanged, except that a `PERSONA_FILE` pointing at nothing is itself worth
a warning since the operator explicitly asked for that path.
"""

from __future__ import annotations
import os
import logging
import threading
from pathlib import Path
from dataclasses import dataclass

from reachy_companion.config import config
from reachy_companion.profile_store import (
    ProfileDefinition,
    ProfileFormatError,
    read_document_text,
    split_front_matter,
    normalize_tool_names,
    optional_string_field,
)


logger = logging.getLogger(__name__)

PERSONA_FILENAME = "persona.md"
PERSONA_FILE_ENV = "PERSONA_FILE"
PERSONA_SCHEMA_VERSION = 1
# A superset-free copy of the profile fields an operator may set. `hidden` is
# deliberately absent: a single-persona app has nothing to hide the persona from.
PERSONA_METADATA_FIELDS = frozenset({"schema_version", "default_tools", "voice", "greeting"})

INSTANCE_PERSONA_SOURCE = "instance persona.md"
BUILTIN_PERSONA_SOURCE = "built-in locked profile"

_CACHE_LOCK = threading.Lock()
_CACHE_KEY: tuple[object, ...] | None = None
_CACHED_OVERRIDE: PersonaOverride | None = None


@dataclass(frozen=True)
class PersonaOverride:
    """One parsed `persona.md`; `None` in a field means "keep the built-in value"."""

    path: Path
    instructions: str
    default_tools: tuple[str, ...] | None
    voice: str | None
    greeting: str | None


def persona_file_path(instance_path: str | Path | None = None) -> Path | None:
    """Return where `persona.md` is looked up, or None when there is nowhere to look."""
    configured = (os.getenv(PERSONA_FILE_ENV) or "").strip()
    if configured:
        return Path(configured).expanduser()

    root = instance_path if instance_path is not None else config.INSTANCE_PATH
    if root is None:
        return None
    return Path(root).expanduser() / PERSONA_FILENAME


def reset_persona_cache() -> None:
    """Forget the cached persona resolution (tests, and any explicit reload)."""
    global _CACHE_KEY, _CACHED_OVERRIDE
    with _CACHE_LOCK:
        _CACHE_KEY = None
        _CACHED_OVERRIDE = None


def _cache_key(path: Path | None) -> tuple[object, ...]:
    """Identify a persona file by path and content stamp, so an edit invalidates it."""
    if path is None:
        return (None,)
    try:
        stamp = path.stat()
    except OSError:
        return (str(path), None)
    return (str(path), stamp.st_mtime_ns, stamp.st_size)


def _parse_persona_document(path: Path) -> PersonaOverride:
    """Parse one persona document, raising ProfileFormatError on anything unusable."""
    if not path.is_file():
        raise ProfileFormatError(f"Persona path {path} is not a file.")

    metadata, body = split_front_matter(
        path, read_document_text(path, label="persona"), required=False, label="persona"
    )

    unknown_fields = sorted(set(metadata) - PERSONA_METADATA_FIELDS)
    if unknown_fields:
        raise ProfileFormatError(f"Unknown persona metadata in {path}: {', '.join(unknown_fields)}.")

    schema_version = metadata.get("schema_version")
    if schema_version is not None and (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version != PERSONA_SCHEMA_VERSION
    ):
        raise ProfileFormatError(f"Unsupported persona schema in {path}: expected version {PERSONA_SCHEMA_VERSION}.")

    raw_tools = metadata.get("default_tools")
    if raw_tools is not None and (
        not isinstance(raw_tools, list) or not all(isinstance(tool_name, str) for tool_name in raw_tools)
    ):
        raise ProfileFormatError(f"Invalid default_tools in {path}: expected a list of strings.")

    if not body:
        raise ProfileFormatError(f"Persona file {path} has an empty persona body.")

    return PersonaOverride(
        path=path,
        instructions=body,
        default_tools=tuple(normalize_tool_names(raw_tools)) if raw_tools is not None else None,
        voice=optional_string_field(metadata, "voice", path),
        greeting=optional_string_field(metadata, "greeting", path),
    )


def _read_persona_override(path: Path | None) -> PersonaOverride | None:
    """Read the persona file at `path`, degrading to None with a warning on any problem."""
    if path is None:
        return None

    if not path.exists():
        if (os.getenv(PERSONA_FILE_ENV) or "").strip():
            logger.warning(
                "%s points at %s, which does not exist; using the %s.",
                PERSONA_FILE_ENV,
                path,
                BUILTIN_PERSONA_SOURCE,
            )
        return None

    try:
        return _parse_persona_document(path)
    except ProfileFormatError as exc:
        logger.warning("Ignoring persona override: %s Using the %s instead.", exc, BUILTIN_PERSONA_SOURCE)
        return None


def load_persona_override(instance_path: str | Path | None = None) -> PersonaOverride | None:
    """Return the operator's persona override, or None when the built-in profile applies."""
    global _CACHE_KEY, _CACHED_OVERRIDE
    path = persona_file_path(instance_path)
    key = _cache_key(path)
    with _CACHE_LOCK:
        if key != _CACHE_KEY:
            _CACHED_OVERRIDE = _read_persona_override(path)
            _CACHE_KEY = key
        return _CACHED_OVERRIDE


def apply_persona_override(
    profile: ProfileDefinition,
    instance_path: str | Path | None = None,
) -> ProfileDefinition:
    """Overlay the operator's persona.md onto a built-in profile definition."""
    override = load_persona_override(instance_path)
    if override is None:
        return profile

    return ProfileDefinition(
        instructions=override.instructions,
        default_tools=override.default_tools if override.default_tools is not None else profile.default_tools,
        voice=override.voice or profile.voice,
        greeting=override.greeting or profile.greeting,
        hidden=profile.hidden,
    )


def log_persona_source(instance_path: str | Path | None = None) -> str:
    """Log one INFO line naming the persona source in use, and return that name."""
    override = load_persona_override(instance_path)
    if override is None:
        logger.info("persona: %s", BUILTIN_PERSONA_SOURCE)
        return BUILTIN_PERSONA_SOURCE

    logger.info("persona: %s (%s)", INSTANCE_PERSONA_SOURCE, override.path)
    return INSTANCE_PERSONA_SOURCE
