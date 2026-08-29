"""Tests for sleep_summary: transcript recording, formatting, and the shutdown writer."""

from __future__ import annotations
import json
import asyncio
from types import SimpleNamespace
from typing import Any
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
from numpy.typing import NDArray

from reachy_companion import people, sleep_summary
from reachy_companion.face_id import Identification
from reachy_companion.tools.core_tools import ToolDependencies
from reachy_companion.tools.who_is_this import WhoIsThis


def _deps(instance_path: str | Path | None = None) -> ToolDependencies:
    """Build a minimal ToolDependencies; nothing here touches the robot deps."""
    return ToolDependencies(
        reachy_mini=MagicMock(),
        movement_manager=MagicMock(),
        instance_path=instance_path,
    )


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


class _FakeCompletions:
    """Records every create() call and answers with one scripted payload."""

    def __init__(self, payload: str | Exception) -> None:
        self.payload: str | Exception = payload
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        """Return the scripted completion, or raise the scripted failure."""
        self.calls.append(kwargs)
        if isinstance(self.payload, Exception):
            raise self.payload
        msg = SimpleNamespace(content=self.payload)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


class _FakeClient:
    """Shape-compatible with AsyncOpenAI for `async with` + chat.completions.create."""

    def __init__(self, payload: str | Exception) -> None:
        self.chat = SimpleNamespace(completions=_FakeCompletions(payload))

    async def __aenter__(self) -> "_FakeClient":  # special methods live on the CLASS
        """Enter the client context, exactly as AsyncOpenAI does."""
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        """Leave the client context without swallowing anything."""
        return False


def _client(payload: str | Exception) -> tuple[_FakeClient, _FakeCompletions]:
    """Build a fake OpenAI client and hand back its completions spy."""
    client = _FakeClient(payload)
    return client, client.chat.completions


def _visit_deps(tmp_path: Path, *names: str) -> ToolDependencies:
    """Build deps for a finished visit: these people were seen, this was said."""
    deps = _deps(instance_path=str(tmp_path))
    deps.recognized_people.update(names)
    sleep_summary.record_transcript(deps, "user", "我下週要考試")
    sleep_summary.record_transcript(deps, "assistant", "加油！考完跟我說")
    return deps


def test_write_sleep_summaries_writes_prefixed_fact(tmp_path: Path) -> None:
    """The visit summary lands as one prefixed person fact, asked for as JSON."""
    client, fake = _client(json.dumps({"小諾": "聊到下週的考試，Reachy 說好要問結果"}))
    deps = _visit_deps(tmp_path, "小諾")
    assert asyncio.run(sleep_summary.write_sleep_summaries(deps, client=client)) == 1
    facts = people.facts_for_person(tmp_path, "小諾")
    assert facts[0].text.startswith(sleep_summary.LAST_CHAT_PREFIX)
    assert "考試" in facts[0].text
    assert fake.calls[0]["response_format"] == {"type": "json_object"}


def test_write_sleep_summaries_supersedes_previous_last_chat(tmp_path: Path) -> None:
    """Yesterday's 上次聊天 fact is replaced, and real facts are left alone."""
    people.add_person_fact(tmp_path, "小諾", "上次聊天（8月1日）：聊了機器人")
    people.add_person_fact(tmp_path, "小諾", "小諾在軟體領域上班")
    client, _ = _client(json.dumps({"小諾": "聊到考試"}))
    deps = _visit_deps(tmp_path, "小諾")
    asyncio.run(sleep_summary.write_sleep_summaries(deps, client=client))
    texts = [f.text for f in people.facts_for_person(tmp_path, "小諾")]
    assert sum(t.startswith(sleep_summary.LAST_CHAT_PREFIX) for t in texts) == 1
    assert "小諾在軟體領域上班" in texts


def test_write_sleep_summaries_replaces_same_day_substring_summary(tmp_path: Path) -> None:
    """A same-day summary that extends the old one leaves exactly one fact: the new one.

    The old text is a substring of the new one, so a forget issued *after* the add
    would match the new fact first (newest-candidate-first) and delete it. The
    at-risk branch is the forget-first one; this is its regression test.
    """
    people.add_person_fact(tmp_path, "小諾", sleep_summary.format_last_chat_fact("聊到考試"))
    client, _ = _client(json.dumps({"小諾": "聊到考試和音樂"}))
    deps = _visit_deps(tmp_path, "小諾")
    assert asyncio.run(sleep_summary.write_sleep_summaries(deps, client=client)) == 1
    texts = [f.text for f in people.facts_for_person(tmp_path, "小諾")]
    assert texts == [sleep_summary.format_last_chat_fact("聊到考試和音樂")]


def test_write_sleep_summaries_at_fact_cap_keeps_real_facts(tmp_path: Path) -> None:
    """At the per-person cap the stale 上次聊天 fact goes first, so no real fact is evicted.

    The old 上次聊天 fact is seeded *last* on purpose: adding before forgetting would
    push the person one over the cap and silently drop the OLDEST fact — a real one.
    """
    real = [f"小諾養的貓叫做貓{i}號" for i in range(people.MAX_FACTS_PER_PERSON - 1)]
    for text in real:
        people.add_person_fact(tmp_path, "小諾", text)
    people.add_person_fact(tmp_path, "小諾", "上次聊天（8月1日）：聊了機器人")
    assert len(people.facts_for_person(tmp_path, "小諾")) == people.MAX_FACTS_PER_PERSON

    client, _ = _client(json.dumps({"小諾": "聊到考試"}))
    deps = _visit_deps(tmp_path, "小諾")
    assert asyncio.run(sleep_summary.write_sleep_summaries(deps, client=client)) == 1

    texts = [f.text for f in people.facts_for_person(tmp_path, "小諾")]
    assert all(text in texts for text in real)
    assert [t for t in texts if t.startswith(sleep_summary.LAST_CHAT_PREFIX)] == [
        sleep_summary.format_last_chat_fact("聊到考試")
    ]


def test_write_sleep_summaries_identical_same_day_summary_keeps_one(tmp_path: Path) -> None:
    """Re-sleeping with the very same summary leaves the fact standing, not zero of it."""
    people.add_person_fact(tmp_path, "小諾", sleep_summary.format_last_chat_fact("聊到考試"))
    client, _ = _client(json.dumps({"小諾": "聊到考試"}))
    deps = _visit_deps(tmp_path, "小諾")
    assert asyncio.run(sleep_summary.write_sleep_summaries(deps, client=client)) == 1
    texts = [f.text for f in people.facts_for_person(tmp_path, "小諾")]
    assert texts == [sleep_summary.format_last_chat_fact("聊到考試")]


def test_write_sleep_summaries_no_people_or_transcript_is_noop(tmp_path: Path) -> None:
    """Nobody recognized and nothing said: no model call, nothing written."""
    client, fake = _client("{}")
    deps = _deps(instance_path=str(tmp_path))  # nobody recognized
    assert asyncio.run(sleep_summary.write_sleep_summaries(deps, client=client)) == 0
    assert not fake.calls


def test_write_sleep_summaries_survives_client_failure(tmp_path: Path) -> None:
    """A failing model call is swallowed — shutdown must never break on memory."""
    client, _ = _client(RuntimeError("boom"))
    deps = _visit_deps(tmp_path, "小諾")
    assert asyncio.run(sleep_summary.write_sleep_summaries(deps, client=client)) == 0
    assert people.facts_for_person(tmp_path, "小諾") == []


def test_write_sleep_summaries_ignores_unlisted_names_and_bad_json(tmp_path: Path) -> None:
    """Only the people who were actually seen can be written, and never an empty summary."""
    client, _ = _client(json.dumps({"路人": "不該寫入", "小諾": ""}))
    deps = _visit_deps(tmp_path, "小諾")
    assert asyncio.run(sleep_summary.write_sleep_summaries(deps, client=client)) == 0
    assert people.facts_for_person(tmp_path, "路人") == []


def test_write_sleep_summaries_disabled_by_env(tmp_path: Path, monkeypatch: Any) -> None:
    """MEMORY_LAST_CHAT_ENABLED=false is a hard kill switch, checked before the call."""
    monkeypatch.setenv("MEMORY_LAST_CHAT_ENABLED", "false")
    client, fake = _client("{}")
    deps = _visit_deps(tmp_path, "小諾")
    assert asyncio.run(sleep_summary.write_sleep_summaries(deps, client=client)) == 0
    assert not fake.calls


def test_format_last_chat_fact_has_prefix_and_date() -> None:
    """The fact reads 上次聊天（M月D日）：<summary> — the prefix is the supersession key."""
    text = sleep_summary.format_last_chat_fact("聊到考試")
    assert text.startswith("上次聊天（")
    assert "月" in text and text.endswith("聊到考試")
