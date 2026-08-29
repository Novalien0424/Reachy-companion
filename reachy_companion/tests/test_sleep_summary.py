"""Tests for sleep_summary: transcript recording, formatting, and the shutdown writer."""

from __future__ import annotations
from unittest.mock import MagicMock

from reachy_companion import sleep_summary
from reachy_companion.tools.core_tools import ToolDependencies


def _deps() -> ToolDependencies:
    """Build a minimal ToolDependencies; nothing here touches the robot deps."""
    return ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())


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
