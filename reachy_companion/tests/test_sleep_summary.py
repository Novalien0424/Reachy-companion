"""Tests for sleep_summary: transcript recording, formatting, and the shutdown writer."""

from __future__ import annotations
import asyncio
from typing import Any
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
from numpy.typing import NDArray

from reachy_companion import sleep_summary
from reachy_companion.face_id import Identification
from reachy_companion.tools.core_tools import ToolDependencies
from reachy_companion.tools.who_is_this import WhoIsThis


def _deps() -> ToolDependencies:
    """Build a minimal ToolDependencies; nothing here touches the robot deps."""
    return ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())


class _FakeRecognizer:
    """A FaceRecognizer stand-in that always answers with one scripted look.

    Mirrors the recognized-path wiring of `tests/test_face_tools.py`, trimmed to
    the two members `who_is_this` actually touches: the `enabled` kill switch and
    `identify`.
    """

    enabled = True

    def __init__(self, identification: Identification) -> None:
        self.identification = identification

    def identify(self, frame_bgr: NDArray[np.uint8] | None) -> Identification:
        """Return the scripted answer, whatever the camera handed over."""
        return self.identification


def _face_deps(recognizer: Any, instance_path: Path) -> ToolDependencies:
    """Build ToolDependencies with a fake camera and the given recognizer."""
    reachy_mini = MagicMock()
    reachy_mini.media.get_frame.return_value = np.full((72, 128, 3), 100, dtype=np.uint8)
    return ToolDependencies(
        reachy_mini=reachy_mini,
        movement_manager=MagicMock(),
        instance_path=instance_path,
        camera_enabled=True,
        face_recognizer=recognizer,
    )


def test_record_transcript_appends_role_and_text() -> None:
    """Each finalized utterance lands in the tail as a (role, text) pair, in order."""
    deps = _deps()
    sleep_summary.record_transcript(deps, "user", "你好")
    sleep_summary.record_transcript(deps, "assistant", "嘿！")
    assert list(deps.session_transcript) == [("user", "你好"), ("assistant", "嘿！")]


def test_record_transcript_skips_blank_and_error_text() -> None:
    """Whitespace-only text and handler error placeholders never enter the tail."""
    deps = _deps()
    sleep_summary.record_transcript(deps, "user", "   ")
    sleep_summary.record_transcript(deps, "assistant", "[error] Cancellation failed")
    assert not deps.session_transcript


def test_record_transcript_is_bounded() -> None:
    """The tail is a bounded deque: the oldest lines drop, the newest survive."""
    deps = _deps()
    for i in range(sleep_summary.TRANSCRIPT_MAX_ITEMS + 10):
        sleep_summary.record_transcript(deps, "user", f"line {i}")
    assert len(deps.session_transcript) == sleep_summary.TRANSCRIPT_MAX_ITEMS
    assert deps.session_transcript[0] == ("user", "line 10")


def test_recognized_people_defaults_empty_per_deps() -> None:
    """Every ToolDependencies gets its own empty set — no shared mutable default."""
    assert _deps().recognized_people == set()
    a, b = _deps(), _deps()
    a.recognized_people.add("小諾")
    assert b.recognized_people == set()  # no shared default object


def test_who_is_this_records_recognized_person(tmp_path: Path) -> None:
    """A recognition joins the visit's people set, on top of labelling the session.

    `current_person` is a single slot the next recognition overwrites — it scopes
    `remember`/`forget` to whoever is in front of the robot now. The sleep summary
    needs the other thing: everyone met during this whole run. So the tool has to
    add to the set as well, and adding must never drop an earlier visitor.
    """
    identification = Identification(status="recognized", name="小諾", score=0.7, face_count=1)
    deps = _face_deps(_FakeRecognizer(identification), tmp_path)
    deps.current_person = "Louis"
    deps.recognized_people.add("Louis")

    result = asyncio.run(WhoIsThis()(deps))

    assert result["status"] == "recognized"
    assert deps.current_person == "小諾"
    assert "小諾" in deps.recognized_people
    assert deps.recognized_people == {"Louis", "小諾"}
