from __future__ import annotations
import re

import pytest

from reachy_companion.tools.dance import Dance
from reachy_companion.tools.forget import Forget
from reachy_companion.tools.remember import Remember
from reachy_companion.tools.stop_dance import StopDance
from reachy_companion.tools.task_cancel import TaskCancel
from reachy_companion.tools.task_status import TaskStatus
from reachy_companion.tools.home_control import HomeControl
from reachy_companion.tools.play_emotion import PlayEmotion
from reachy_companion.tools.stop_emotion import StopEmotion
from reachy_companion.tools.head_tracking import HeadTracking
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
    monkeypatch.setenv("HA_ENTITIES", '{"Lamp": "light.lamp"}')
    return {
        "play_emotion": PlayEmotion.description,
        "dance": Dance.description,
        "stop_emotion": StopEmotion.description,
        "stop_dance": StopDance.description,
        "head_tracking": HeadTracking.description,
        "home_control": HomeControl().description,
        "wait_for_user": WaitForUser.description,
        "remember": Remember.description,
        "forget": Forget.description,
        "task_status": TaskStatus.description,
        "task_cancel": TaskCancel.description,
    }


def test_always_on_tool_descriptions_have_use_and_do_not_use_sides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every audited always-on tool exposes both sides of its routing rule."""
    for name, description in _target_descriptions(monkeypatch).items():
        assert "Use when:" in description, name
        assert "Do NOT use when:" in description, name


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("play_emotion", "dance"),
        ("stop_emotion", "stop_dance"),
        ("remember", "forget"),
        ("task_status", "task_cancel"),
    ],
)
def test_sibling_tool_descriptions_name_each_other(
    monkeypatch: pytest.MonkeyPatch,
    left: str,
    right: str,
) -> None:
    """Sibling tools should route away from each other symmetrically."""
    descriptions = _target_descriptions(monkeypatch)

    assert right in descriptions[left], left
    assert left in descriptions[right], right


def test_stop_motion_descriptions_route_music_to_the_music_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stopping dance or emotion is not the same as stopping speaker playback."""
    descriptions = _target_descriptions(monkeypatch)

    for name in ("stop_emotion", "stop_dance"):
        assert "`music`" in descriptions[name], name
        assert "`action=stop`" in descriptions[name], name


def test_non_pair_descriptions_name_their_alternatives(monkeypatch: pytest.MonkeyPatch) -> None:
    """Asymmetric tools still need an explicit alternative for the do-not-use side."""
    descriptions = _target_descriptions(monkeypatch)

    assert "`look_around`" in descriptions["head_tracking"]
    assert "`move_head`" in descriptions["head_tracking"]
    assert "`tv`" in descriptions["home_control"]
    assert "`nas`" in descriptions["home_control"]
    assert "`music`" in descriptions["home_control"]
    assert "answer instead" in descriptions["wait_for_user"]


def test_audited_tool_descriptions_have_no_numeric_length_caps(monkeypatch: pytest.MonkeyPatch) -> None:
    """Instruction-surface text should use calibration, not sentence or word budgets."""
    for name, description in _target_descriptions(monkeypatch).items():
        assert NUMERIC_LENGTH_CAP.search(description) is None, name
