"""Shared `.env` parsers for the audio path and the realtime handler.

One bad line in a robot's `.env` must never abort a conversation session, so
every parser here degrades to its caller-supplied default and logs a warning
naming the offending knob. Numbers additionally reject the non-finite values
`float()` accepts (`nan`, `inf`) and clamp into an optional range — a NaN
semitone count or an out-of-range mix would otherwise poison every sample
downstream of it.

This module lives under `audio/` and imports nothing from the package, which is
what lets both `openai_realtime.py` and `audio/voicefx.py` use it without an
import cycle.
"""

import os
import math
import logging


logger = logging.getLogger(__name__)

_TRUE_VALUES = frozenset({"1", "true", "yes", "y", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "n", "off"})


def _raw(name: str) -> str | None:
    """Return the stripped value of `name`, or None when unset or blank.

    Blank means "not configured" rather than "empty value", so a commented-out
    knob left as `FOO=` behaves exactly like a missing one — and does not warn.
    """
    value = os.getenv(name)
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def env_bool(name: str, default: bool) -> bool:
    """Read a boolean from the environment, warning and falling back when malformed."""
    raw = _raw(name)
    if raw is None:
        return default
    lowered = raw.lower()
    if lowered in _TRUE_VALUES:
        return True
    if lowered in _FALSE_VALUES:
        return False
    logger.warning("Ignoring invalid %s=%r; using %s.", name, raw, default)
    return default


def env_float(name: str, default: float, lo: float | None = None, hi: float | None = None) -> float:
    """Read a finite float from the environment, clamped into [lo, hi].

    Args:
        name: Environment variable to read.
        default: Value used when unset, blank, malformed or non-finite.
        lo: Optional inclusive lower bound; values below it clamp up and warn.
        hi: Optional inclusive upper bound; values above it clamp down and warn.

    Returns:
        The parsed value, or `default` when the variable cannot be honoured.

    """
    raw = _raw(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("Ignoring invalid %s=%r; using %s.", name, raw, default)
        return default
    if not math.isfinite(value):
        logger.warning("Ignoring non-finite %s=%r; using %s.", name, raw, default)
        return default
    return _clamp(name, value, lo, hi)


def env_int(name: str, default: int, lo: int | None = None, hi: int | None = None) -> int:
    """Read an int from the environment, clamped into [lo, hi].

    Args:
        name: Environment variable to read.
        default: Value used when unset, blank or malformed.
        lo: Optional inclusive lower bound; values below it clamp up and warn.
        hi: Optional inclusive upper bound; values above it clamp down and warn.

    Returns:
        The parsed value, or `default` when the variable cannot be honoured.

    """
    raw = _raw(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Ignoring invalid %s=%r; using %s.", name, raw, default)
        return default
    return int(_clamp(name, value, lo, hi))


def _clamp(name: str, value: float, lo: float | None, hi: float | None) -> float:
    """Clamp `value` into [lo, hi], warning once with the bound that was hit."""
    if lo is not None and value < lo:
        logger.warning("Clamping %s=%s to its minimum %s.", name, value, lo)
        return lo
    if hi is not None and value > hi:
        logger.warning("Clamping %s=%s to its maximum %s.", name, value, hi)
        return hi
    return value
