"""Backchannel / minimum-content classification for turn gating.

Mandarin backchannels are monosyllabic and tonally ambiguous; no shipped
model classifies them reliably (research doc §3), so this is the field's
standard lexicon+length heuristic. Pure functions, no I/O.
"""

from __future__ import annotations
import re

from reachy_companion.audio.envparse import env_int


# Tokens observed committing as turns in the 2026-08-24 party journal, plus
# the standard EN/ZH backchannel sets from the research doc (§3.2, §6.3).
_BACKCHANNEL_TOKENS = frozenset(
    {
        "嗯", "對", "好", "是", "喔", "欸", "哦", "唔", "呵", "哈",
        "好的", "是喔", "這樣", "真的",
        "yeah", "yep", "ok", "okay", "mm", "hmm", "uh", "huh", "uh-huh",
        "mm-hmm", "right", "sure",
    }
)
# Runs of a single repeated char (嗯嗯嗯, 哈哈哈) collapse to the char.
_REPEAT_RE = re.compile(r"(.)\1+")
_STRIP_RE = re.compile(r"[\s。，、！？!?~～.,;:；：…‥·\-—「」『』()（）\"']+")


def _tokens(text: str) -> list[str]:
    cleaned = _STRIP_RE.sub(" ", text.casefold()).strip()
    return [t for t in cleaned.split(" ") if t]


def is_backchannel(text: str) -> bool:
    """Return True when the utterance carries no addressable content."""
    tokens = _tokens(text)
    if not tokens:
        return True
    for token in tokens:
        collapsed = _REPEAT_RE.sub(r"\1", token)
        if token in _BACKCHANNEL_TOKENS or collapsed in _BACKCHANNEL_TOKENS:
            continue
        return False
    return True


def is_substantive(text: str) -> bool:
    """Return True when the utterance is long enough and not pure backchannel."""
    min_chars = env_int("REALTIME_MIN_TURN_CHARS", 2, lo=1)
    content = _STRIP_RE.sub("", text)
    return len(content) >= min_chars and not is_backchannel(text)
