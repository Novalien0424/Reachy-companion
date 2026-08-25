"""Backchannel / minimum-content classification for turn gating.

Mandarin backchannels are monosyllabic and tonally ambiguous; no shipped
model classifies them reliably (research doc §3), so this is the field's
standard lexicon+length heuristic. Pure functions, no I/O.
"""

from __future__ import annotations
import re
from functools import lru_cache

from reachy_companion.audio.envparse import env_int


# Tokens observed committing as turns in the 2026-08-24 party journal, plus
# the standard EN/ZH backchannel sets from the research doc (§3.2, §6.3).
_BACKCHANNEL_TOKENS = frozenset(
    {
        "嗯", "對", "好", "是", "喔", "欸", "哦", "唔", "呵", "哈", "哼",
        "好的", "是喔", "這樣", "真的",
        "yeah", "yep", "ok", "okay", "mm", "hmm", "uh", "huh", "uh-huh",
        "mm-hmm", "right", "sure",
    }
)
# Runs of a single repeated char (嗯嗯嗯, 哈哈哈) collapse to the char.
_REPEAT_RE = re.compile(r"(.)\1+")
_STRIP_RE = re.compile(r"[\s。，、！？!?~～.,;:；：…‥·\-—「」『』()（）\"']+")
_CJK_RE = re.compile(r"[一-鿿]+")

# ASR rarely inserts spaces between adjacent Mandarin syllables, so a run like
# "嗯哼" or "這樣喔" never tokenizes apart on its own — whitespace/punctuation
# splitting alone would let it slip through as "unknown, therefore real
# content". The CJK-only entries of the lexicon above (single chars and short
# compounds like 這樣/好的) double as segmentation atoms so such runs can be
# recognized by decomposing them into known atoms instead.
_CJK_ATOMS = frozenset(token for token in _BACKCHANNEL_TOKENS if _CJK_RE.fullmatch(token))


def _tokens(text: str) -> list[str]:
    cleaned = _STRIP_RE.sub(" ", text.casefold()).strip()
    return [t for t in cleaned.split(" ") if t]


@lru_cache(maxsize=512)
def _segments_into_atoms(token: str) -> bool:
    """Return True when a CJK-only token fully decomposes into known backchannel atoms."""
    if not token:
        return True
    return any(
        token.startswith(atom) and _segments_into_atoms(token[len(atom) :]) for atom in _CJK_ATOMS
    )


def is_backchannel(text: str) -> bool:
    """Return True when the utterance carries no addressable content."""
    tokens = _tokens(text)
    if not tokens:
        return True
    for token in tokens:
        collapsed = _REPEAT_RE.sub(r"\1", token)
        if token in _BACKCHANNEL_TOKENS or collapsed in _BACKCHANNEL_TOKENS:
            continue
        if _CJK_RE.fullmatch(token) and _segments_into_atoms(token):
            continue
        return False
    return True


def is_substantive(text: str) -> bool:
    """Return True when the utterance is long enough and not pure backchannel."""
    min_chars = env_int("REALTIME_MIN_TURN_CHARS", 2, lo=1)
    content = _STRIP_RE.sub("", text)
    return len(content) >= min_chars and not is_backchannel(text)
