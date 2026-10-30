from __future__ import annotations
import re

import pytest

from reachy_companion.tools.face import Face
from reachy_companion.tools.head import Head
from reachy_companion.tools.dance import Dance
from reachy_companion.tools.memory import Memory
from reachy_companion.tools.home_control import HomeControl
from reachy_companion.tools.play_emotion import PlayEmotion
from reachy_companion.tools.wait_for_user import WaitForUser


NUMERIC_LENGTH_CAP = re.compile(
    r"(?i)(?:"
    r"\b(?:under|within|no more than|at most|maximum|up to)\s+"
    r"(?:one|two|three|four|five|\d+)\s+(?:sentence|sentences|word|words|line|lines)\b"
    r"|"
    r"\b\d+\s*[-–~～]\s*\d+\s*(?:sentence|sentences|word|words|line|lines)\b"
    r"|"
    r"\b(?:one|two|three|four|five|\d+)\s+sentence(?:s)?\b"
    r"|"
    r"(?:最多|不超過|少於|限制在)[^。；\n]*(?:句|字)"
    r")"
)


def _target_descriptions(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Return the always-on descriptions the model reads (1.30.0: the families, not their delegates)."""
    monkeypatch.setenv("HA_ENTITIES", '{"Lamp": "light.lamp"}')
    return {
        "play_emotion": PlayEmotion.description,
        "dance": Dance.description,
        "head": Head.description,
        "memory": Memory.description,
        "face": Face.description,
        "home_control": HomeControl().description,
        "wait_for_user": WaitForUser.description,
    }


# `head`, `memory` and `face` carry the operator's 1.30.0 text, which routes with
# positive rules and "Do NOT use when" alone (`face` with 「Use it, not camera」),
# so the two-sided form is pinned on the rest.
_TWO_SIDED = ("play_emotion", "dance", "home_control", "wait_for_user")


def test_always_on_tool_descriptions_have_use_and_do_not_use_sides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every audited always-on tool exposes both sides of its routing rule."""
    descriptions = _target_descriptions(monkeypatch)
    for name in _TWO_SIDED:
        assert "Use when:" in descriptions[name], name
        assert "Do NOT use when:" in descriptions[name], name
    for name in ("head", "memory"):
        assert "Do NOT use when:" in descriptions[name], name


def test_sibling_tool_descriptions_name_each_other(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sibling tools should route away from each other (by plain name: `that is play_emotion`)."""
    descriptions = _target_descriptions(monkeypatch)

    assert "dance" in descriptions["play_emotion"]
    assert "that is play_emotion" in descriptions["dance"]
    # One-way on purpose: a face is never a memory fact, and `face` says what it stores.
    assert "it is about a face — that is face" in descriptions["memory"]


def test_dance_stop_routes_speaker_music_to_the_music_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stopping a dance or an emotion is not the same as stopping speaker playback."""
    descriptions = _target_descriptions(monkeypatch)

    assert "that is music with action=stop" in descriptions["dance"]
    assert "`stop` ends any dance or emotion motion at once" in descriptions["dance"]


def test_non_pair_descriptions_name_their_alternatives(monkeypatch: pytest.MonkeyPatch) -> None:
    """Asymmetric tools still need an explicit alternative for the do-not-use side."""
    descriptions = _target_descriptions(monkeypatch)

    assert "look_around" in descriptions["head"]
    assert "camera" in descriptions["head"]
    assert "`youtube`" in descriptions["home_control"]
    assert "`nas`" in descriptions["home_control"]
    assert "`music`" in descriptions["home_control"]
    assert "answer instead" in descriptions["wait_for_user"]


def test_audited_tool_descriptions_have_no_numeric_length_caps(monkeypatch: pytest.MonkeyPatch) -> None:
    """Instruction-surface text should use calibration, not sentence or word budgets."""
    for name, description in _target_descriptions(monkeypatch).items():
        assert NUMERIC_LENGTH_CAP.search(description) is None, name
