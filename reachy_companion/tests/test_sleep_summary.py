"""Tests for sleep_summary: transcript recording, formatting, and the shutdown writer."""

from __future__ import annotations
import json
import time
import asyncio
from types import SimpleNamespace
from typing import Any
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest
from numpy.typing import NDArray

from reachy_companion import people, sleep_summary
from reachy_companion import huggingface_realtime as hf_mod
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


def test_record_transcript_appends_role_text_and_stamp() -> None:
    """Each finalized utterance lands in the tail as (role, text, monotonic stamp), in order."""
    deps = _deps()
    before = time.monotonic()
    sleep_summary.record_transcript(deps, "user", "你好")
    sleep_summary.record_transcript(deps, "assistant", "嘿！")
    after = time.monotonic()
    assert [(role, text) for role, text, _ in deps.session_transcript] == [("user", "你好"), ("assistant", "嘿！")]
    stamps = [stamp for _, _, stamp in deps.session_transcript]
    assert stamps == sorted(stamps)
    assert all(before <= stamp <= after for stamp in stamps)


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
    assert deps.session_transcript[0][:2] == ("user", "line 10")


def test_recognized_people_defaults_empty_per_deps() -> None:
    """Every ToolDependencies gets its own empty containers — no shared mutable default."""
    assert _deps().recognized_people == set()
    assert _deps().recognized_at == {}
    a, b = _deps(), _deps()
    a.record_recognition("小諾")
    assert b.recognized_people == set()  # no shared default object
    assert b.recognized_at == {}


def test_record_recognition_stamps_the_guest_list() -> None:
    """A recognition joins the set and gets a monotonic stamp; a re-sighting moves it forward."""
    deps = _deps()
    before = time.monotonic()
    deps.record_recognition("小諾")
    first = deps.recognized_at["小諾"]
    assert deps.recognized_people == {"小諾"}
    assert before <= first <= time.monotonic()

    deps.record_recognition("小諾")
    assert deps.recognized_at["小諾"] >= first  # last sighting wins, never the first


def test_record_transcript_refreshes_the_current_person_stamp() -> None:
    """Talking is presence: each recorded line moves the current person's stamp forward.

    Without this a long visit would summarize nobody — the recognition happens at
    the boot greeting and scrolls out of the 40-line tail long before sleep.
    """
    deps = _deps()
    deps.record_recognition("小諾")
    deps.current_person = "小諾"
    seen_at = deps.recognized_at["小諾"]

    sleep_summary.record_transcript(deps, "user", "我下週要考試")

    assert deps.recognized_at["小諾"] >= seen_at
    assert deps.recognized_at["小諾"] == deps.session_transcript[-1][2]


def test_record_transcript_does_not_invent_a_guest() -> None:
    """A current person who was never recognized is not added to the guest list."""
    deps = _deps()
    deps.current_person = "小諾"  # e.g. a label without a recognition behind it
    sleep_summary.record_transcript(deps, "user", "你好")
    assert deps.recognized_at == {} and deps.recognized_people == set()


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
    deps.record_recognition("Louis")

    result = asyncio.run(WhoIsThis()(deps))

    assert result["status"] == "recognized"
    assert deps.current_person == "小諾"
    assert "小諾" in deps.recognized_people
    assert deps.recognized_people == {"Louis", "小諾"}
    # And stamped, or the sleep summary's visit window cannot place them in time.
    assert deps.recognized_at["小諾"] >= deps.recognized_at["Louis"]


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
    """Build deps for a finished visit: these people were seen, this was said.

    Every recognition site sets `current_person` as well as the guest list, so
    the fixture does too — the sleep summary's window leans on that pairing.
    """
    deps = _deps(instance_path=str(tmp_path))
    for name in names:
        deps.record_recognition(name)
        deps.current_person = name
    sleep_summary.record_transcript(deps, "user", "我下週要考試")
    sleep_summary.record_transcript(deps, "assistant", "加油！考完跟我說")
    return deps


def _long_visit_deps(tmp_path: Path, *names: str) -> ToolDependencies:
    """Build the same visit, talked past the tail's bound so the opening lines scrolled out."""
    deps = _visit_deps(tmp_path, *names)
    for index in range(sleep_summary.TRANSCRIPT_MAX_ITEMS):
        sleep_summary.record_transcript(deps, "user" if index % 2 else "assistant", f"第{index}句")
    assert len(deps.session_transcript) == sleep_summary.TRANSCRIPT_MAX_ITEMS
    return deps


def _seen_hours_ago(deps: ToolDependencies, name: str, hours: float) -> None:
    """Backdate one guest's last sighting, relative to the tail's oldest retained line.

    Stamps are `time.monotonic()`, so a test moves the clock by editing the stamp
    rather than by sleeping: nothing here waits on a real hour.
    """
    deps.recognized_people.add(name)
    deps.recognized_at[name] = deps.session_transcript[0][2] - hours * 3600.0


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


def test_write_sleep_summaries_replaces_case_variant_summary(tmp_path: Path) -> None:
    """A case-only difference must not slip past the guards and delete the NEW fact.

    `forget_person_fact` matches case-insensitively (people.py:394, 405), so a raw
    `in` check reports "no collision", the post-add forget then matches the new
    fact first (newest-candidate-first) and removes it — leaving only the stale one.
    """
    people.add_person_fact(tmp_path, "小諾", sleep_summary.format_last_chat_fact("聊到 AI"))
    client, _ = _client(json.dumps({"小諾": "聊到 ai 和音樂"}))
    deps = _visit_deps(tmp_path, "小諾")
    assert asyncio.run(sleep_summary.write_sleep_summaries(deps, client=client)) == 1
    texts = [f.text for f in people.facts_for_person(tmp_path, "小諾")]
    assert texts == [sleep_summary.format_last_chat_fact("聊到 ai 和音樂")]


def test_write_sleep_summaries_replaces_whitespace_variant_summary(tmp_path: Path) -> None:
    """Whitespace the store collapses must not leave two 上次聊天 facts behind.

    `forget_person_fact` keys on `normalize_memory_text(query)`, which collapses
    runs of whitespace; a raw comparison sees a mismatch, fires neither forget,
    and the exactly-one invariant breaks.
    """
    people.add_person_fact(tmp_path, "小諾", sleep_summary.format_last_chat_fact("聊到 A B"))
    client, _ = _client(json.dumps({"小諾": "聊到 A  B 和音樂"}))
    deps = _visit_deps(tmp_path, "小諾")
    assert asyncio.run(sleep_summary.write_sleep_summaries(deps, client=client)) == 1
    texts = [f.text for f in people.facts_for_person(tmp_path, "小諾")]
    assert texts == [sleep_summary.format_last_chat_fact("聊到 A B 和音樂")]


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


def test_write_sleep_summaries_skips_a_guest_from_hours_before_the_tail(tmp_path: Path) -> None:
    """A visitor from this morning is not summarized with tonight's topics.

    `recognized_people` spans the whole app run, `session_transcript` only its
    last 40 lines. Somebody recognized at 09:00 whose lines scrolled out hours
    ago must not be handed the 20:00 conversation — that writes another person's
    evening into their 上次聊天 fact, and the next greeting reads it back to them.
    """
    client, fake = _client(json.dumps({"小諾": "聊到考試", "雲霓": "聊到考試"}))
    deps = _long_visit_deps(tmp_path, "小諾")  # here now, and talking: the tail is hers
    _seen_hours_ago(deps, "雲霓", 11.0)  # this morning; nothing of hers survives

    assert asyncio.run(sleep_summary.write_sleep_summaries(deps, client=client)) == 1

    assert people.facts_for_person(tmp_path, "雲霓") == []
    assert people.facts_for_person(tmp_path, "小諾") != []
    # And she is not even named to the model: the roster it summarizes is the filtered one.
    assert "雲霓" not in fake.calls[0]["messages"][1]["content"]


def test_write_sleep_summaries_keeps_the_speaker_of_a_long_visit(tmp_path: Path) -> None:
    """The one person here all evening is summarized even though their greeting scrolled out.

    They were recognized once, at the boot greeting, hundreds of lines ago. What
    keeps them inside the window is the talking itself — `record_transcript`
    refreshes the current person — and without that a long visit, the very visit
    most worth a callback, would end with no summary at all.
    """
    client, _ = _client(json.dumps({"小諾": "聊到考試"}))
    deps = _long_visit_deps(tmp_path, "小諾")
    assert deps.recognized_at["小諾"] >= deps.session_transcript[0][2]

    assert asyncio.run(sleep_summary.write_sleep_summaries(deps, client=client)) == 1
    assert people.facts_for_person(tmp_path, "小諾") != []


def test_write_sleep_summaries_keeps_a_guest_recognized_inside_the_tail(tmp_path: Path) -> None:
    """Someone recognized after the oldest retained line is inside the window, and is written."""
    client, _ = _client(json.dumps({"雲霓": "聊到考試"}))
    deps = _long_visit_deps(tmp_path, "小諾")
    deps.recognized_people.add("雲霓")
    deps.recognized_at["雲霓"] = deps.session_transcript[-1][2]  # walked in mid-conversation

    assert asyncio.run(sleep_summary.write_sleep_summaries(deps, client=client)) == 1
    assert people.facts_for_person(tmp_path, "雲霓") != []


def test_write_sleep_summaries_with_every_guest_stale_makes_no_call(tmp_path: Path) -> None:
    """Nobody left inside the window: no model call at all, and nothing written.

    The filter runs before the client is built, so an all-stale run costs no
    token and no network — it is the same no-op as an empty guest list.
    """
    client, fake = _client(json.dumps({"小諾": "聊到考試"}))
    deps = _long_visit_deps(tmp_path, "小諾")
    _seen_hours_ago(deps, "小諾", 11.0)

    assert asyncio.run(sleep_summary.write_sleep_summaries(deps, client=client)) == 0
    assert not fake.calls
    assert people.facts_for_person(tmp_path, "小諾") == []


def test_write_sleep_summaries_keeps_an_unstamped_guest(tmp_path: Path) -> None:
    """A name added to the set with no stamp behind it is not silently dropped.

    Every production site stamps (`ToolDependencies.record_recognition`), so this
    is the fail-open edge for anything else that only knows about the set: no
    stamp is no evidence of staleness, and the old behavior stands.
    """
    client, _ = _client(json.dumps({"雲霓": "聊到考試"}))
    deps = _long_visit_deps(tmp_path, "小諾")
    deps.recognized_people.add("雲霓")  # set only, no stamp

    assert asyncio.run(sleep_summary.write_sleep_summaries(deps, client=client)) == 1
    assert people.facts_for_person(tmp_path, "雲霓") != []


def test_write_sleep_summaries_filters_nobody_while_the_tail_covers_the_whole_visit(tmp_path: Path) -> None:
    """Nothing has scrolled out: every guest stays, because their own lines are still here.

    The window is the tail's reach, not a clock. A short run keeps the whole
    conversation, this morning's included, so the person it belongs to is still
    summarizable from it — the filter only starts dropping people once the lines
    that would speak for them are gone.
    """
    client, _ = _client(json.dumps({"雲霓": "聊到考試"}))
    deps = _visit_deps(tmp_path, "小諾")  # two lines, nothing evicted
    _seen_hours_ago(deps, "雲霓", 11.0)

    assert asyncio.run(sleep_summary.write_sleep_summaries(deps, client=client)) == 1
    assert people.facts_for_person(tmp_path, "雲霓") != []


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


class _WriterSpy:
    """Stands in for `write_sleep_summaries`, recording how it was called."""

    def __init__(self, order: list[str] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.order = order if order is not None else []

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> int:
        """Record the call and report one fact written."""
        self.calls.append(kwargs)
        self.order.append("summary")
        return 1


@pytest.mark.asyncio
async def test_handler_shutdown_without_sleep_request_writes_nothing(tmp_path: Path, monkeypatch: Any) -> None:
    """A settings/backend restart reaches shutdown() mid-visit: no summary then."""
    spy = _WriterSpy()
    monkeypatch.setattr(hf_mod, "write_sleep_summaries", spy)
    handler = hf_mod.HuggingFaceRealtimeHandler(_visit_deps(tmp_path, "小諾"))

    await handler.shutdown()

    assert spy.calls == []


@pytest.mark.asyncio
async def test_handler_shutdown_after_sleep_request_summarizes_once(tmp_path: Path, monkeypatch: Any) -> None:
    """Going to sleep summarizes, with its own client, and only on the first shutdown."""
    order: list[str] = []
    spy = _WriterSpy(order)

    async def record_music_stop(_deps: Any, _token: int) -> None:
        order.append("music_stop")

    monkeypatch.setattr(hf_mod, "write_sleep_summaries", spy)
    monkeypatch.setattr(hf_mod, "on_session_shutdown", record_music_stop)
    deps = _visit_deps(tmp_path, "小諾")
    deps.sleep_requested = True
    handler = hf_mod.HuggingFaceRealtimeHandler(deps)

    await handler.shutdown()
    await handler.shutdown()  # the session `finally` can run this a second time

    # No client argument: the writer builds one and closes it with `async with`,
    # so a shared client handed in here would be closed out from under its owner.
    assert spy.calls == [{}]
    # The summarizer call can take seconds: the daemon's speaker must already be
    # stopped when it starts, or music plays on with Reachy asleep.
    assert order == ["music_stop", "summary", "music_stop"]
