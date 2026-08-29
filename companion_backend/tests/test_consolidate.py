"""Contract tests for the LLM consolidation pass over the Mac people store.

Nothing here reaches OpenAI. Every test hands `run` a fake client whose
`chat.completions.create` is a plain synchronous method — the shape the CLI's
`openai.OpenAI` presents — so the module's own validation, its `上次聊天`
handling and its write discipline are what is under test, never the model.

The last test is the one the whole `上次聊天` dance exists for: it drives a
complete sync cycle (robot writes → import → consolidate → project) through the
known removal-detection hole at the twenty-fact cap (`backend/robot.py:548`) and
pins that the projection the robot reads back holds exactly one last-chat fact,
the new one.
"""

from __future__ import annotations
import os
import json
import subprocess
from types import SimpleNamespace
from typing import Any
from pathlib import Path
from itertools import count
from dataclasses import field, dataclass
from collections.abc import Sequence

import httpx
import pytest

from scripts import consolidate as cli
from reachy_companion import people
from backend import robot, store, projection, consolidate
from backend.config import Settings


LAST_CHAT_PREFIX = consolidate.LAST_CHAT_PREFIX


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def monotonic_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give every backend store write a distinct, increasing timestamp.

    Borrowed from `test_robot_sync`, and load-bearing here: without it two writes
    inside the same millisecond share a timestamp, and the test that pins
    `updated_at` across an applied consolidation would pass even if the pass
    wrote through `_mutate`.
    """
    ticks = count(1_700_000_000_000)
    monkeypatch.setattr(store, "_now_ms", lambda: next(ticks))


def _completion(content: str | None) -> Any:
    """Return the object shape `chat.completions.create` answers with."""
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


class _FakeCompletions:
    """Records every create() call and answers with one scripted payload."""

    def __init__(self, payload: str | None | Exception) -> None:
        self.payload: str | None | Exception = payload
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        """Return the scripted completion, or raise the scripted failure."""
        self.calls.append(kwargs)
        if isinstance(self.payload, Exception):
            raise self.payload
        return _completion(self.payload)


class _FakeClient:
    """Shape-compatible with the sync `openai.OpenAI`: `.chat.completions.create(**kwargs)`."""

    def __init__(self, payload: str | None | Exception) -> None:
        self.chat = SimpleNamespace(completions=_FakeCompletions(payload))


def _client(payload: str | None | Exception) -> tuple[_FakeClient, _FakeCompletions]:
    """Build a fake OpenAI client and hand back its completions spy."""
    client = _FakeClient(payload)
    completions: _FakeCompletions = client.chat.completions
    return client, completions


class _EchoCompletions:
    """Hands back exactly the facts it was shown — the consolidation that changes nothing."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        """Parse the numbered list out of the user prompt and return it verbatim."""
        self.calls.append(kwargs)
        shown = str(kwargs["messages"][1]["content"]).splitlines()[1:]
        return _completion(json.dumps({"facts": [line.split(". ", 1)[1] for line in shown]}))


class _EchoClient:
    """A client that consolidates nothing, so a test can isolate everything else."""

    def __init__(self) -> None:
        self.chat = SimpleNamespace(completions=_EchoCompletions())


def _payload(*facts: str) -> str:
    """Return the JSON body the model is asked for."""
    return json.dumps({"facts": list(facts)}, ensure_ascii=False)


def _person(settings: Settings, name: str, facts: Sequence[str]) -> store.BackendPerson:
    """Create one person and add their facts **oldest first**, as the store's writers do.

    `store.add_fact` prepends, so the stored list comes back in the reverse of
    what is passed here — which is the newest-first order `run` reads.
    """
    person = store.create_person(settings, name)
    for text in facts:
        store.add_fact(settings, person.id, text)
    reloaded = store.get_person(settings, person.id)
    assert reloaded is not None
    return reloaded


def _texts(settings: Settings, person_id: str) -> list[str]:
    """Return one person's stored fact texts, newest first."""
    person = store.get_person(settings, person_id)
    assert person is not None
    return [fact.text for fact in person.facts]


_MERGED = _payload("以前想當舞者，現在是外科醫師", "喜歡寫歌")
_OLD_CHAT = "上次聊天（8月1日）：聊了機器人"
_NEW_CHAT = "上次聊天（8月29日）：聊到下週的考試"


def test_the_last_chat_prefix_matches_the_robot_s_own(monkeypatch: pytest.MonkeyPatch) -> None:
    """The backend restates the robot's supersession key; a drift here breaks the healer.

    The import is made *here* rather than in `backend.consolidate` because
    importing `reachy_companion.sleep_summary` reaches `reachy_companion.tools`,
    which runs `load_dotenv(override=True)` at import time
    (`reachy_companion/config.py:305`). The backend must not have its own
    environment — `OPENAI_API_KEY` included — rewritten as a side effect of
    reading a constant, and `os.environ` is swapped for a copy so this test does
    not do it to the rest of the suite either.
    """
    monkeypatch.setattr(os, "environ", dict(os.environ))
    from reachy_companion.sleep_summary import LAST_CHAT_PREFIX as robot_prefix

    assert consolidate.LAST_CHAT_PREFIX == robot_prefix


# --------------------------------------------------------------------------
# consolidate_person
# --------------------------------------------------------------------------


def test_consolidate_person_returns_validated_list() -> None:
    """The model's `facts` list comes back as-is, asked for under the pinned prompt as JSON."""
    client, spy = _client(_MERGED)

    out = consolidate.consolidate_person(
        client, "gpt-5-mini", "雲霓", ["想當舞者", "是外科醫師", "喜歡寫歌", "喜歡寫歌"]
    )

    assert out == ["以前想當舞者，現在是外科醫師", "喜歡寫歌"]
    call = spy.calls[0]
    assert call["model"] == "gpt-5-mini"
    assert call["response_format"] == {"type": "json_object"}
    assert call["messages"][0] == {"role": "system", "content": consolidate._SYSTEM_PROMPT}
    assert call["messages"][1] == {
        "role": "user",
        "content": "雲霓 的記憶：\n1. 想當舞者\n2. 是外科醫師\n3. 喜歡寫歌\n4. 喜歡寫歌",
    }


def test_consolidate_person_rejects_bad_payloads() -> None:
    """Anything but a non-empty list of at most 20 short strings is refused whole.

    Refused *whole*, never salvaged item by item: a payload the model got wrong
    is not evidence about the items inside it, and half of a rewrite applied over
    a person's memory would delete the other half.
    """

    def out(payload: str | None | Exception) -> list[str] | None:
        client, _ = _client(payload)
        return consolidate.consolidate_person(client, "gpt-5-mini", "雲霓", ["想當舞者", "是外科醫師"])

    assert out("不是 JSON") is None  # not JSON at all
    assert out("") is None  # empty content
    assert out(None) is None  # no content at all
    assert out(json.dumps(["喜歡寫歌"])) is None  # JSON, but not an object
    assert out(_payload()) is None  # nothing left to store
    assert out(json.dumps({"facts": "喜歡寫歌"})) is None  # `facts` is not a list
    assert out(json.dumps({"facts": ["喜歡寫歌", 7]})) is None  # not a list of *str*
    assert out(_payload(*(f"第{index}條" for index in range(21)))) is None  # more than 20 items
    assert out(_payload("好" * 281)) is None  # one item over 280 characters
    assert out(RuntimeError("connection reset by peer")) is None  # the client raised


def test_consolidate_person_keeps_the_boundary_cases_inside_the_caps() -> None:
    """Exactly 20 items and exactly 280 characters are the caps, not over them."""
    twenty = [f"第{index}條" for index in range(20)]
    client, _ = _client(_payload(*twenty))
    assert consolidate.consolidate_person(client, "gpt-5-mini", "雲霓", ["想當舞者"]) == twenty

    edge = "好" * 280
    client, _ = _client(_payload(edge))
    assert consolidate.consolidate_person(client, "gpt-5-mini", "雲霓", ["想當舞者"]) == [edge]


# --------------------------------------------------------------------------
# run: writing, and not writing
# --------------------------------------------------------------------------


def test_run_dry_run_never_writes(settings: Settings) -> None:
    """A dry run reports the rewrite it would make and leaves the store file byte-identical."""
    person = _person(settings, "雲霓", ["喜歡寫歌", "是外科醫師", "想當舞者"])
    before = store.people_path(settings).read_bytes()
    client, _ = _client(_MERGED)

    results = consolidate.run(settings, apply=False, client=client)

    assert [result.person_id for result in results] == [person.id]
    assert results[0].name == "雲霓"
    assert results[0].before == ("想當舞者", "是外科醫師", "喜歡寫歌")
    assert results[0].after == ("以前想當舞者，現在是外科醫師", "喜歡寫歌")
    assert results[0].changed is True
    assert results[0].error is None
    assert store.people_path(settings).read_bytes() == before


def test_run_apply_writes_through_replace_facts(settings: Settings) -> None:
    """With `apply`, the store holds exactly the list the pass computed, newest first."""
    person = _person(settings, "雲霓", ["喜歡寫歌", "是外科醫師", "想當舞者"])
    client, _ = _client(_MERGED)

    results = consolidate.run(settings, apply=True, client=client)

    assert _texts(settings, person.id) == ["以前想當舞者，現在是外科醫師", "喜歡寫歌"]
    assert results[0].after == tuple(_texts(settings, person.id))


def test_run_reports_after_exactly_as_the_store_will_hold_it(settings: Settings) -> None:
    """`after` is the preview the operator approves, so it has to be what lands.

    `store.replace_facts` collapses whitespace and dedupes case-insensitively on
    the way in. Reporting the model's raw strings would show three facts where
    two get stored — and an answer differing from the current list only in
    spacing would read as a change and provoke a write that changes nothing.
    """
    person = _person(settings, "Lena", ["likes tea", "plays cello"])
    client, _ = _client(_payload("likes   tea", "plays cello", "Plays Cello"))

    results = consolidate.run(settings, apply=True, client=client)

    assert results[0].after == ("likes tea", "plays cello")  # collapsed, and deduped by case
    assert list(results[0].after) == _texts(settings, person.id)

    # The same list again, sloppier: normalizing before the comparison is what
    # makes this the no-op it is.
    unchanged = store.people_path(settings).read_bytes()
    again, _ = _client(_payload("likes  tea", " plays cello "))

    second = consolidate.run(settings, apply=True, client=again)

    assert second[0].changed is False
    assert store.people_path(settings).read_bytes() == unchanged


def test_run_apply_preserves_updated_at(settings: Settings) -> None:
    """A background rewrite must not reshuffle the projection's recency ranking."""
    person = _person(settings, "雲霓", ["喜歡寫歌", "是外科醫師", "想當舞者"])
    client, _ = _client(_MERGED)

    consolidate.run(settings, apply=True, client=client)

    reloaded = store.get_person(settings, person.id)
    assert reloaded is not None
    assert reloaded.updated_at == person.updated_at


def test_run_writes_nothing_when_the_rewrite_is_a_no_op(settings: Settings) -> None:
    """An unchanged list is not a write: `changed` is False and the file is untouched."""
    _person(settings, "雲霓", ["喜歡寫歌", "想當舞者"])
    before = store.people_path(settings).read_bytes()

    results = consolidate.run(settings, apply=True, client=_EchoClient())

    assert results[0].after == results[0].before == ("想當舞者", "喜歡寫歌")
    assert results[0].changed is False
    assert store.people_path(settings).read_bytes() == before


def test_run_reports_a_bad_payload_and_writes_nothing(settings: Settings) -> None:
    """A refused payload leaves the person exactly as they were, with a reason attached."""
    person = _person(settings, "雲霓", ["喜歡寫歌", "想當舞者"])
    before = store.people_path(settings).read_bytes()
    client, _ = _client("不是 JSON")

    results = consolidate.run(settings, apply=True, client=client)

    assert results[0].error == consolidate.INVALID_RESPONSE
    assert results[0].after == results[0].before == ("想當舞者", "喜歡寫歌")
    assert results[0].changed is False
    assert _texts(settings, person.id) == ["想當舞者", "喜歡寫歌"]
    assert store.people_path(settings).read_bytes() == before


def test_build_llm_client_answers_none_without_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """No key is a reason to skip the pass, never an exception out of it."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert consolidate.build_llm_client() is None
    monkeypatch.setenv("OPENAI_API_KEY", "   ")
    assert consolidate.build_llm_client() is None


def test_run_no_client_reports_error(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """With no client and no API key, every person is reported as skipped, not as rewritten."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    _person(settings, "雲霓", ["喜歡寫歌"])
    _person(settings, "小諾", ["喜歡機器人"])
    before = store.people_path(settings).read_bytes()

    results = consolidate.run(settings, apply=True, client=None)

    assert [result.error for result in results] == [consolidate.NO_CLIENT, consolidate.NO_CLIENT]
    assert all(not result.changed for result in results)
    assert all(result.after == result.before for result in results)
    assert store.people_path(settings).read_bytes() == before


def test_run_only_selects_one_person_through_the_store_s_own_name_rule(settings: Settings) -> None:
    """`only` resolves a name the way the store's index does — normalized, case-insensitive."""
    _person(settings, "雲霓", ["喜歡寫歌", "想當舞者"])
    other = _person(settings, "Lena", ["likes tea"])
    client, spy = _client(_MERGED)

    results = consolidate.run(settings, apply=True, client=client, only="  雲霓  ")

    assert [result.name for result in results] == ["雲霓"]
    assert len(spy.calls) == 1
    assert _texts(settings, other.id) == ["likes tea"]
    assert consolidate.run(settings, apply=False, client=client, only="LENA")[0].name == "Lena"
    assert consolidate.run(settings, apply=False, client=client, only="nobody") == []


def test_run_uses_the_default_model_when_none_is_given(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The model comes from `COMPANION_CONSOLIDATE_MODEL`, falling back to `gpt-5-mini`."""
    _person(settings, "雲霓", ["喜歡寫歌", "想當舞者"])
    monkeypatch.delenv("COMPANION_CONSOLIDATE_MODEL", raising=False)
    client, spy = _client(_MERGED)
    consolidate.run(settings, apply=False, client=client)
    assert spy.calls[0]["model"] == "gpt-5-mini"

    monkeypatch.setenv("COMPANION_CONSOLIDATE_MODEL", "  gpt-5  ")
    client, spy = _client(_MERGED)
    consolidate.run(settings, apply=False, client=client)
    assert spy.calls[0]["model"] == "gpt-5"

    client, spy = _client(_MERGED)
    consolidate.run(settings, apply=False, client=client, model="gpt-5-nano")
    assert spy.calls[0]["model"] == "gpt-5-nano"


# --------------------------------------------------------------------------
# the 上次聊天 fact: never shown, never authored, never duplicated
# --------------------------------------------------------------------------


def test_run_never_shows_the_model_a_last_chat_fact(settings: Settings) -> None:
    """Rule 1: every last-chat fact is popped before the prompt is built.

    The model is asked to merge, rank and drop; a callback fact is none of its
    business, and showing it invites the model to rewrite or "merge" the one
    piece of memory whose exact text is a supersession key.
    """
    _person(settings, "雲霓", ["喜歡寫歌", _OLD_CHAT, "想當舞者", _NEW_CHAT])
    client, spy = _client(_payload("想當舞者", "喜歡寫歌"))

    consolidate.run(settings, apply=False, client=client)

    prompt = str(spy.calls[0]["messages"][1]["content"])
    assert LAST_CHAT_PREFIX not in prompt
    assert prompt == "雲霓 的記憶：\n1. 想當舞者\n2. 喜歡寫歌"


def test_run_drops_a_last_chat_fact_the_model_invented(settings: Settings) -> None:
    """Rule 2: a `上次聊天` string coming *back* from the model is thrown away.

    The model never gets to author one — it would be a callback to a conversation
    that never happened, and the robot would ask about it by name.
    """
    _person(settings, "雲霓", ["喜歡寫歌", _NEW_CHAT])
    client, _ = _client(_payload("上次聊天（1月1日）：偽造", "喜歡寫歌"))

    results = consolidate.run(settings, apply=True, client=client)

    assert "上次聊天（1月1日）：偽造" not in results[0].after
    assert results[0].after == (_NEW_CHAT, "喜歡寫歌")
    # The invention is the *only* thing the model added, so once it is dropped
    # this rewrite is a no-op — and a no-op must not read as a change.
    assert results[0].changed is False
    assert _texts(settings, results[0].person_id) == [_NEW_CHAT, "喜歡寫歌"]


def test_run_refuses_an_answer_that_is_nothing_but_invented_last_chat_facts(settings: Settings) -> None:
    """Dropping every line of an answer leaves nothing to store — which is a refusal.

    Rule 2 removes what the model may not author; if that empties the answer, the
    person had real memory to organize and the model returned none of it. Writing
    the empty result would erase them.
    """
    person = _person(settings, "雲霓", ["喜歡寫歌", "想當舞者"])
    client, _ = _client(_payload("上次聊天（1月1日）：偽造"))

    results = consolidate.run(settings, apply=True, client=client)

    assert results[0].error == consolidate.INVALID_RESPONSE
    assert results[0].after == results[0].before == ("想當舞者", "喜歡寫歌")
    assert results[0].changed is False
    assert _texts(settings, person.id) == ["想當舞者", "喜歡寫歌"]


def test_run_keeps_newest_last_chat_fact_first_and_dedupes(settings: Settings) -> None:
    """Rule 3: only the newest popped last-chat fact is re-prepended, at position 0.

    Two of them is the state the sync hole at `backend/robot.py:548` leaves
    behind — the robot superseded its own copy, the import could not see the
    removal, so the Mac ended up holding both. This pass is the healer.
    """
    person = _person(settings, "雲霓", ["喜歡寫歌", _OLD_CHAT, "是外科醫師", _NEW_CHAT])
    assert _texts(settings, person.id) == [_NEW_CHAT, "是外科醫師", _OLD_CHAT, "喜歡寫歌"]
    client, _ = _client(_payload("是外科醫師", "喜歡寫歌"))

    results = consolidate.run(settings, apply=True, client=client)

    after = results[0].after
    assert after[0].startswith("上次聊天（8月29日）")
    assert sum(text.startswith(LAST_CHAT_PREFIX) for text in after) == 1
    assert after == (_NEW_CHAT, "是外科醫師", "喜歡寫歌")
    assert _texts(settings, person.id) == list(after)


def test_run_caps_the_stored_list_at_the_robot_s_twenty_facts(settings: Settings) -> None:
    """The callback fact spends one of the robot's twenty slots; the body gets the rest.

    A twenty-first fact would be stored here and never reach the robot — the
    projection emits the newest twenty — so every run would re-send it to the
    model and every projection would drop it again, a difference that could
    never settle.
    """
    person = _person(settings, "雲霓", ["喜歡寫歌", _NEW_CHAT])
    body = [f"第{index}條" for index in range(consolidate.MAX_FACTS)]
    client, _ = _client(_payload(*body))

    results = consolidate.run(settings, apply=True, client=client)

    after = results[0].after
    assert consolidate.MAX_FACTS == people.MAX_FACTS_PER_PERSON == 20
    assert len(after) == consolidate.MAX_FACTS
    assert after[0] == _NEW_CHAT
    assert list(after[1:]) == body[: consolidate.MAX_FACTS - 1]
    assert body[-1] not in after  # the model's lowest-ranked fact is the one that goes
    assert _texts(settings, person.id) == list(after)


def test_run_dedupes_last_chat_facts_without_calling_the_model(settings: Settings) -> None:
    """A person whose only facts are last-chat facts is healed with no LLM call at all."""
    person = _person(settings, "雲霓", [_OLD_CHAT, _NEW_CHAT])
    client, spy = _client(_payload("never asked"))

    results = consolidate.run(settings, apply=True, client=client)

    assert spy.calls == []
    assert results[0].after == (_NEW_CHAT,)
    assert results[0].changed is True
    assert _texts(settings, person.id) == [_NEW_CHAT]


# --------------------------------------------------------------------------
# the full cycle: robot writes -> import -> consolidate -> project
# --------------------------------------------------------------------------


@dataclass
class _RobotFiles:
    """The robot's store files, served over the one `scp` an import fetches with.

    The download half of `test_robot_sync.FakeRobot`, cut to what this cycle
    touches: nothing here uploads, promotes, or fetches an enrollment snapshot.
    """

    remote: dict[str, str] = field(default_factory=dict)

    def run(self, argv: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        """Stand in for `subprocess.run`, serving a download and refusing anything else."""
        call = list(argv)
        assert call[0] == "scp" and ":" in call[-2], f"unexpected remote call {call!r}"
        source, destination = call[-2], call[-1]
        content = self.remote.get(source.rsplit("/", 1)[-1])
        if content is None:
            # Some scp builds create and truncate the local file before the
            # remote open fails; `_download` has to survive that.
            Path(destination).write_text("", encoding="utf-8")
            return subprocess.CompletedProcess(call, 1, "", f"scp: {source}: No such file or directory")
        Path(destination).write_text(content, encoding="utf-8")
        return subprocess.CompletedProcess(call, 0, "", "")


def _people_content(root: Path, name: str, facts: Sequence[str]) -> str:
    """Return the bytes the robot's own writers put in `people.v1.json`, facts oldest-first."""
    directory = root / "robot"
    directory.mkdir(parents=True, exist_ok=True)
    people.clear_people(directory)
    people.upsert_person(directory, name)
    for text in facts:
        people.add_person_fact(directory, name, text)
    path: Path = people.people_path_for_instance(directory)
    return path.read_text(encoding="utf-8")


def test_full_sync_cycle_heals_stale_last_chat(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The riskiest cycle, end to end: the robot supersedes, the import cannot see it, this heals it.

    At the robot's twenty-fact cap `_removed_facts` refuses to read an absence as
    a deletion (`backend/robot.py:548`) — an eviction looks identical, and
    deleting a real fact nobody asked to forget is the worse mistake. So an
    import brings the robot's *new* last-chat fact back and leaves the stale one
    standing here: the Mac now holds two, and the next push would write the stale
    one straight back onto the robot that just superseded it.

    This pass closes the loop, and the assertion is made on the projection the
    robot would actually read back, not on the Mac store alone.
    """
    real_facts = [f"雲霓的事實{index}" for index in range(19)]
    person = _person(settings, "雲霓", [*real_facts, _OLD_CHAT])
    assert len(person.facts) == people.MAX_FACTS_PER_PERSON

    # The robot superseded its own copy: 19 real facts and the NEW last-chat one,
    # at the cap, with the stale one gone.
    fake = _RobotFiles({people.PEOPLE_FILENAME: _people_content(tmp_path, "雲霓", [*real_facts, _NEW_CHAT])})
    # `backend.robot` does a plain `import subprocess`, so this is the same module
    # object `test_robot_sync` reaches for as `robot.subprocess`.
    monkeypatch.setattr(subprocess, "run", fake.run)

    diff = robot.import_from_robot(settings)
    assert diff.new_person_facts == [robot.RobotPersonFacts(name="雲霓", face_id=None, facts=[_NEW_CHAT])]
    assert diff.removed_person_facts == []  # the hole: at the cap, removals are not read
    assert robot.apply_import(settings, diff).conflicts == []
    assert _texts(settings, person.id)[:2] == [_NEW_CHAT, _OLD_CHAT]  # both, which is the bug

    results = consolidate.run(settings, apply=True, client=_EchoClient())

    assert results[0].changed is True and results[0].error is None
    assert _texts(settings, person.id) == [_NEW_CHAT, *reversed(real_facts)]

    out_dir = tmp_path / "projected"
    projection.project(settings, out_dir)
    projected = people.list_people(out_dir)
    texts = [fact.text for fact in projected[0].facts]
    assert sum(text.startswith(LAST_CHAT_PREFIX) for text in texts) == 1
    assert texts[0] == _NEW_CHAT
    assert _OLD_CHAT not in texts


# --------------------------------------------------------------------------
# the operator CLI (scripts/consolidate.py)
# --------------------------------------------------------------------------


@pytest.fixture
def refused(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Make every probe report a refused connection, and hand back the URLs probed.

    This is the *only* verdict that lets a run touch the store, so almost every
    CLI test needs it; the guard's own tests replace it with something else.
    """
    probed: list[str] = []

    def refuse(url: str) -> None:
        probed.append(url)
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(cli, "_probe", refuse)
    monkeypatch.setattr(cli, "_tailscale_hosts", list)
    monkeypatch.delenv(cli.BACKEND_HOST_ENV, raising=False)
    return probed


class _OrderedCompletions(_FakeCompletions):
    """A completions stub that also records *when* the model was called."""

    def __init__(self, payload: str, order: list[str]) -> None:
        super().__init__(payload)
        self.order = order

    def create(self, **kwargs: Any) -> Any:
        """Note the call in the shared order log, then answer as `_FakeCompletions` does."""
        self.order.append("consolidate")
        return super().create(**kwargs)


class _OrderedClient:
    """A fake OpenAI client whose calls interleave into a shared order log."""

    def __init__(self, payload: str, order: list[str]) -> None:
        self.chat = SimpleNamespace(completions=_OrderedCompletions(payload, order))


@pytest.fixture
def cli_settings(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Point `load_settings()` — which the CLI calls itself — at the test's data dir."""
    monkeypatch.setenv("COMPANION_BACKEND_DATA", str(settings.data_dir))
    return settings


def test_cli_dry_run_prints_diff_and_exits_zero(
    cli_settings: Settings, capsys: pytest.CaptureFixture[str], refused: list[str]
) -> None:
    """The default run prints a unified diff per changed person and writes nothing."""
    person = _person(cli_settings, "雲霓", ["想當舞者", "是外科醫師", "喜歡寫歌"])
    before = store.people_path(cli_settings).read_bytes()
    client, _ = _client(_MERGED)

    code = cli.main([], client=client)

    out = capsys.readouterr().out
    assert code == 0
    assert "雲霓" in out and "-" in out and "+" in out
    assert "-想當舞者" in out and "+以前想當舞者，現在是外科醫師" in out
    assert "1 person(s), 1 changed, applied: no" in out
    assert store.people_path(cli_settings).read_bytes() == before
    assert _texts(cli_settings, person.id) == ["喜歡寫歌", "是外科醫師", "想當舞者"]


def test_cli_apply_writes_the_consolidated_facts(
    cli_settings: Settings, capsys: pytest.CaptureFixture[str], refused: list[str]
) -> None:
    """`--apply` writes exactly the `after` list the dry run showed."""
    person = _person(cli_settings, "雲霓", ["想當舞者", "是外科醫師", "喜歡寫歌"])
    client, _ = _client(_MERGED)

    code = cli.main(["--apply"], client=client)

    assert code == 0
    assert "1 person(s), 1 changed, applied: yes" in capsys.readouterr().out
    assert _texts(cli_settings, person.id) == ["以前想當舞者，現在是外科醫師", "喜歡寫歌"]


def test_cli_person_filter_reaches_one_person(
    cli_settings: Settings, capsys: pytest.CaptureFixture[str], refused: list[str]
) -> None:
    """`--person` narrows the pass through the store's own name rule."""
    person = _person(cli_settings, "雲霓", ["想當舞者", "是外科醫師", "喜歡寫歌"])
    other = _person(cli_settings, "Lena", ["likes tea"])
    client, _ = _client(_MERGED)

    code = cli.main(["--apply", "--person", "  雲霓  "], client=client)

    assert code == 0
    assert "1 person(s), 1 changed, applied: yes" in capsys.readouterr().out
    assert _texts(cli_settings, person.id) == ["以前想當舞者，現在是外科醫師", "喜歡寫歌"]
    assert _texts(cli_settings, other.id) == ["likes tea"]


def test_cli_reports_a_person_nobody_answers_to(
    cli_settings: Settings, capsys: pytest.CaptureFixture[str], refused: list[str]
) -> None:
    """A `--person` that matches nobody is a mistyped name, not a silent success."""
    _person(cli_settings, "雲霓", ["喜歡寫歌"])
    client, spy = _client(_MERGED)

    code = cli.main(["--person", "nobody"], client=client)

    assert code == 1
    assert "nobody" in capsys.readouterr().out
    assert spy.calls == []


def test_cli_returns_two_when_there_is_no_client(
    cli_settings: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], refused: list[str]
) -> None:
    """Every person skipped for want of a client is its own exit code, not a success."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    person = _person(cli_settings, "雲霓", ["喜歡寫歌"])

    code = cli.main(["--apply"])

    assert code == 2
    assert consolidate.NO_CLIENT in capsys.readouterr().out
    assert _texts(cli_settings, person.id) == ["喜歡寫歌"]


def test_cli_reports_a_refused_answer_and_still_exits_zero(
    cli_settings: Settings, capsys: pytest.CaptureFixture[str], refused: list[str]
) -> None:
    """One unusable model answer is a skipped person, not a failed run."""
    person = _person(cli_settings, "雲霓", ["喜歡寫歌", "想當舞者"])
    client, _ = _client("不是 JSON")

    code = cli.main(["--apply"], client=client)

    assert code == 0
    assert consolidate.INVALID_RESPONSE in capsys.readouterr().out
    assert _texts(cli_settings, person.id) == ["想當舞者", "喜歡寫歌"]


# --------------------------------------------------------------------------
# the CLI's probe guard: the store lock is process-local, so fail CLOSED
# --------------------------------------------------------------------------


def test_cli_refuses_while_the_backend_answers(
    cli_settings: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A backend that answers anything at all stops the run before it reads the store."""
    _person(cli_settings, "雲霓", ["喜歡寫歌", "想當舞者"])
    before = store.people_path(cli_settings).read_bytes()

    monkeypatch.setattr(cli, "_probe", lambda url: None)
    monkeypatch.setattr(cli, "_tailscale_hosts", list)
    monkeypatch.delenv(cli.BACKEND_HOST_ENV, raising=False)

    def boom(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("the guard must return before anything reads the store")

    monkeypatch.setattr(store, "list_people", boom)
    client, spy = _client(_MERGED)

    code = cli.main(["--apply"], client=client)

    assert code == 3
    assert "stop it first" in capsys.readouterr().out
    assert spy.calls == []
    assert store.people_path(cli_settings).read_bytes() == before


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ConnectTimeout("timed out"),
        httpx.ReadTimeout("timed out"),
        httpx.ReadError("reset"),
        RuntimeError("something else entirely"),
    ],
)
def test_cli_refuses_when_it_cannot_prove_the_backend_is_stopped(
    cli_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: Exception,
) -> None:
    """Anything but a refused connection is unproven, and unproven fails closed.

    A timeout is the case worth naming: a backend under load answers late, and a
    guard that read "no answer yet" as "not running" would hand two processes the
    same store — the lost update the guard exists to prevent.
    """
    person = _person(cli_settings, "雲霓", ["喜歡寫歌", "想當舞者"])

    def fail(url: str) -> None:
        raise failure

    monkeypatch.setattr(cli, "_probe", fail)
    monkeypatch.setattr(cli, "_tailscale_hosts", list)
    monkeypatch.delenv(cli.BACKEND_HOST_ENV, raising=False)
    client, spy = _client(_MERGED)

    code = cli.main(["--apply"], client=client)

    assert code == 3
    assert "cannot prove" in capsys.readouterr().out
    assert spy.calls == []
    assert _texts(cli_settings, person.id) == ["想當舞者", "喜歡寫歌"]


def test_cli_probes_loopback_the_configured_bind_and_the_tailnet(
    cli_settings: Settings, monkeypatch: pytest.MonkeyPatch, refused: list[str]
) -> None:
    """The documented production bind is the tailnet IP, so loopback alone proves nothing."""
    monkeypatch.setenv(cli.BACKEND_HOST_ENV, "192.168.1.9")
    monkeypatch.setattr(cli, "_tailscale_hosts", lambda: ["100.64.0.7", "127.0.0.1"])

    assert cli.candidate_hosts() == ["127.0.0.1", "192.168.1.9", "100.64.0.7"]

    _person(cli_settings, "雲霓", ["喜歡寫歌"])
    client, _ = _client(_MERGED)
    assert cli.main([], client=client) == 0
    assert refused == [
        "http://127.0.0.1:8710/api/config",
        "http://192.168.1.9:8710/api/config",
        "http://100.64.0.7:8710/api/config",
    ]


def test_cli_stops_at_the_first_host_that_answers(
    cli_settings: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Loopback refusing is not a verdict while the tailnet bind still answers."""
    _person(cli_settings, "雲霓", ["喜歡寫歌"])
    monkeypatch.delenv(cli.BACKEND_HOST_ENV, raising=False)
    monkeypatch.setattr(cli, "_tailscale_hosts", lambda: ["100.64.0.7"])

    def probe(url: str) -> None:
        if "127.0.0.1" in url:
            raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(cli, "_probe", probe)
    client, spy = _client(_MERGED)

    assert cli.main([], client=client) == 3
    assert "100.64.0.7" in capsys.readouterr().out
    assert spy.calls == []


def test_tailscale_hosts_is_best_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing or unhappy `tailscale` contributes no hosts and never raises."""
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    assert cli._tailscale_hosts() == []

    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/tailscale")

    def completed(returncode: int, stdout: str) -> Any:
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")

    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **k: completed(0, "100.64.0.7\n 100.64.0.8 \n\n"))
    assert cli._tailscale_hosts() == ["100.64.0.7", "100.64.0.8"]

    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **k: completed(1, "100.64.0.7"))
    assert cli._tailscale_hosts() == []

    def raise_oserror(*args: Any, **kwargs: Any) -> None:
        raise OSError("no such binary")

    monkeypatch.setattr(cli.subprocess, "run", raise_oserror)
    assert cli._tailscale_hosts() == []


# --------------------------------------------------------------------------
# the one-shot flow: import -> consolidate -> push, with the backend stopped
# --------------------------------------------------------------------------


def test_cli_runs_import_then_consolidate_then_push(
    cli_settings: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], refused: list[str]
) -> None:
    """The whole round trip in one process, in the only order that is safe."""
    person = _person(cli_settings, "雲霓", ["想當舞者", "是外科醫師", "喜歡寫歌"])
    order: list[str] = []

    def fake_import(settings: Settings) -> robot.RobotDiff:
        order.append("import")
        return robot.RobotDiff(new_faces=[], changed_faces=[], new_person_facts=[])

    def fake_apply(settings: Settings, diff: robot.RobotDiff) -> robot.ImportResult:
        order.append("apply_import")
        return robot.ImportResult(applied=0, conflicts=[])

    def fake_push(settings: Settings) -> robot.PushResult:
        order.append("push")
        return robot.PushResult(pushed=True, faces_count=1, people_count=1, blocked_by=None)

    monkeypatch.setattr(robot, "import_from_robot", fake_import)
    monkeypatch.setattr(robot, "apply_import", fake_apply)
    monkeypatch.setattr(robot, "push", fake_push)

    code = cli.main(["--import-first", "--apply", "--push-after"], client=_OrderedClient(_MERGED, order))

    assert code == 0
    assert order == ["import", "apply_import", "consolidate", "push"]
    assert _texts(cli_settings, person.id) == ["以前想當舞者，現在是外科醫師", "喜歡寫歌"]
    assert "push" in capsys.readouterr().out


def test_cli_refuses_push_after_without_apply(
    cli_settings: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], refused: list[str]
) -> None:
    """A dry run has nothing to push, so the combination is refused before anything runs."""
    _person(cli_settings, "雲霓", ["喜歡寫歌"])

    def boom(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("nothing may run when the flags are refused")

    monkeypatch.setattr(robot, "push", boom)
    monkeypatch.setattr(store, "list_people", boom)
    client, spy = _client(_MERGED)

    code = cli.main(["--push-after"], client=client)

    assert code == 1
    assert "--push-after" in capsys.readouterr().out
    assert spy.calls == []
    assert refused == []  # refused before the probe, which costs two seconds a host


def test_cli_does_not_push_when_nothing_was_consolidated(
    cli_settings: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], refused: list[str]
) -> None:
    """No client means no rewrite, and a push of an unchanged store is not what was asked for."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    _person(cli_settings, "雲霓", ["喜歡寫歌"])

    def boom(settings: Settings) -> robot.PushResult:
        raise AssertionError("the push must not run when the pass consolidated nothing")

    monkeypatch.setattr(robot, "push", boom)

    code = cli.main(["--apply", "--push-after"])

    assert code == 2
    assert "Skipping the push" in capsys.readouterr().out


def test_cli_reports_a_refused_push(
    cli_settings: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], refused: list[str]
) -> None:
    """A push the sync gate blocks is a nonzero exit, not a quiet line in the log."""
    _person(cli_settings, "雲霓", ["想當舞者", "是外科醫師", "喜歡寫歌"])
    blocked = robot.RobotDiff(new_faces=[], changed_faces=[], new_person_facts=[])
    monkeypatch.setattr(
        robot,
        "push",
        lambda settings: robot.PushResult(pushed=False, faces_count=0, people_count=0, blocked_by=blocked),
    )
    client, _ = _client(_MERGED)

    code = cli.main(["--apply", "--push-after"], client=client)

    assert code == 1
    assert "refused" in capsys.readouterr().out.casefold()


def test_cli_reports_a_failed_import_without_consolidating(
    cli_settings: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], refused: list[str]
) -> None:
    """An unreachable robot stops the run at the import; the facts are left alone."""
    person = _person(cli_settings, "雲霓", ["喜歡寫歌", "想當舞者"])

    def fail(settings: Settings) -> robot.RobotDiff:
        raise robot.RobotError("the robot is not configured")

    monkeypatch.setattr(robot, "import_from_robot", fail)
    client, spy = _client(_MERGED)

    code = cli.main(["--import-first", "--apply"], client=client)

    assert code == 1
    assert "the robot is not configured" in capsys.readouterr().out
    assert spy.calls == []
    assert _texts(cli_settings, person.id) == ["想當舞者", "喜歡寫歌"]


def test_cli_never_pushes_an_empty_store(
    cli_settings: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], refused: list[str]
) -> None:
    """A push projects this store onto the robot, so an empty one must never travel."""

    def boom(settings: Settings) -> robot.PushResult:
        raise AssertionError("pushing an empty store would clear the robot's faces")

    monkeypatch.setattr(robot, "push", boom)

    code = cli.main(["--apply", "--push-after"], client=_FakeClient(_MERGED))

    assert code == 0
    assert "nobody to consolidate" in capsys.readouterr().out
