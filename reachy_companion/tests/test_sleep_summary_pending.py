"""A visit the sleep could not summarize is kept and summarized at the next start (W5.4).

On 2026-10-31 the summarizer timed out twice at bedtime and the visit's
上次聊天 was lost. Now the bounded visit is written to the instance dir and the
next start summarizes it through the same code path, then deletes it.
"""

from __future__ import annotations
import os
import json
import stat
import time
import asyncio
import logging
import threading
from typing import Any
from pathlib import Path

import httpx
import pytest

# `tests/` has no __init__.py, so pytest's prepend import mode puts the
# directory itself on sys.path — import the sibling harness by bare name.
from test_sleep_summary import _client, _visit_deps

from reachy_companion import people, sleep_summary


def _pending(tmp_path: Path) -> Path:
    return tmp_path / sleep_summary.PENDING_SUMMARY_FILE


def _timeouts(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Make every summarizer wait time out; return the budgets it was given."""
    budgets: list[float] = []

    async def fake_wait_for(awaitable: Any, timeout: float) -> Any:
        budgets.append(timeout)
        close = getattr(awaitable, "close", None)
        if callable(close):
            close()
        raise TimeoutError()

    monkeypatch.setattr(sleep_summary.asyncio, "wait_for", fake_wait_for)
    return budgets


def test_a_visit_that_timed_out_twice_is_kept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Both attempts time out: the names and lines are kept, 0600, nothing written yet."""
    client, fake = _client(json.dumps({"小諾": "unused"}))
    deps = _visit_deps(tmp_path, "小諾")
    budgets = _timeouts(monkeypatch)
    assert asyncio.run(sleep_summary.write_sleep_summaries(deps, client=client)) == 0
    assert len(budgets) == 2, "the one timeout retry still runs first"
    path = _pending(tmp_path)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    (visit,) = json.loads(path.read_text(encoding="utf-8"))["visits"]
    assert visit["names"] == ["小諾"]
    assert visit["lines"] == [["user", "我下週要考試"], ["assistant", "加油！考完跟我說"]]
    assert not path.with_name(path.name + ".part").exists()
    assert people.facts_for_person(tmp_path, "小諾") == []


@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectError("no route to host"),
        ConnectionResetError("reset by peer"),
    ],
)
def test_a_network_failure_keeps_the_visit(tmp_path: Path, error: Exception) -> None:
    """A transient error is the same bedtime problem as a timeout."""
    client, _ = _client(error)
    deps = _visit_deps(tmp_path, "小諾")
    assert asyncio.run(sleep_summary.write_sleep_summaries(deps, client=client)) == 0
    assert _pending(tmp_path).exists()


def test_a_non_transient_failure_is_not_kept(tmp_path: Path) -> None:
    """A request the summarizer refused would fail the same way at the next start."""
    client, _ = _client(RuntimeError("boom"))
    deps = _visit_deps(tmp_path, "小諾")
    assert asyncio.run(sleep_summary.write_sleep_summaries(deps, client=client)) == 0
    assert not _pending(tmp_path).exists()


def test_a_summarized_visit_leaves_no_file(tmp_path: Path) -> None:
    """The ordinary path is unchanged: summarized now, nothing kept."""
    client, _ = _client(json.dumps({"小諾": "聊到下週的考試"}))
    deps = _visit_deps(tmp_path, "小諾")
    assert asyncio.run(sleep_summary.write_sleep_summaries(deps, client=client)) == 1
    assert not _pending(tmp_path).exists()


def test_the_kept_visit_is_bounded() -> None:
    """At most `PENDING_MAX_LINES` lines and `PENDING_MAX_TEXT_BYTES` of text, newest kept."""
    many = [("user", f"第{i}句") for i in range(sleep_summary.PENDING_MAX_LINES + 50)]
    kept = sleep_summary._bounded_lines(many)
    assert len(kept) == sleep_summary.PENDING_MAX_LINES
    assert kept[-1] == many[-1]
    big = [("user", "字" * 4000) for _ in range(20)]  # 12 KB of UTF-8 per line
    kept = sleep_summary._bounded_lines(big)
    assert sum(len(text.encode("utf-8")) for _, text in kept) <= sleep_summary.PENDING_MAX_TEXT_BYTES
    assert kept and kept[-1] == big[-1]


def test_the_file_keeps_at_most_three_visits(tmp_path: Path) -> None:
    """Repeated failed sleeps cannot grow the file without bound; the oldest goes."""
    for index in range(sleep_summary.PENDING_MAX_VISITS + 2):
        assert sleep_summary._save_pending_visit(tmp_path, ["小諾"], [("user", f"第{index}次")], 1000.0 + index)
    visits = json.loads(_pending(tmp_path).read_text(encoding="utf-8"))["visits"]
    assert [v["lines"][0][1] for v in visits] == ["第2次", "第3次", "第4次"]


def test_no_instance_dir_keeps_nothing(caplog: pytest.LogCaptureFixture) -> None:
    """Without an instance dir there is nowhere private to keep it; say so."""
    with caplog.at_level(logging.INFO):
        assert sleep_summary._save_pending_visit(None, ["小諾"], [("user", "hi")], time.time()) is False
    assert "cannot be kept" in caplog.text


# --------------------------------------------------------------------------
# The next start
# --------------------------------------------------------------------------


def test_the_next_start_summarizes_the_kept_visit_and_deletes_it(tmp_path: Path) -> None:
    """Same code path, same prompt, and the fact is dated by the visit, not today."""
    visited_at = time.time() - 2 * 24 * 3600
    sleep_summary._save_pending_visit(tmp_path, ["小諾"], [("user", "我下週要考試")], visited_at)
    client, fake = _client(json.dumps({"小諾": "聊到下週的考試"}))
    assert asyncio.run(sleep_summary.summarize_pending_visits(tmp_path, client=client)) == 1
    assert not _pending(tmp_path).exists()
    (fact,) = people.facts_for_person(tmp_path, "小諾")
    assert fact.text == sleep_summary.format_last_chat_fact("聊到下週的考試", visited_at)
    assert "user: 我下週要考試" in fake.calls[0]["messages"][1]["content"]


def test_a_kept_visit_the_model_answered_empty_is_still_done(tmp_path: Path) -> None:
    """The summarizer answered; there was just nothing to write. Do not ask again."""
    sleep_summary._save_pending_visit(tmp_path, ["小諾"], [("user", "嗯")], time.time())
    client, _ = _client(json.dumps({"小諾": ""}))
    assert asyncio.run(sleep_summary.summarize_pending_visits(tmp_path, client=client)) == 0
    assert not _pending(tmp_path).exists()


def test_a_kept_visit_stays_while_the_network_is_still_down(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Another timeout at startup keeps it for the start after."""
    sleep_summary._save_pending_visit(tmp_path, ["小諾"], [("user", "我下週要考試")], time.time())
    client, _ = _client(json.dumps({"小諾": "unused"}))
    _timeouts(monkeypatch)
    assert asyncio.run(sleep_summary.summarize_pending_visits(tmp_path, client=client)) == 0
    assert len(json.loads(_pending(tmp_path).read_text(encoding="utf-8"))["visits"]) == 1


def test_a_kept_visit_older_than_a_week_is_dropped_unsent(tmp_path: Path) -> None:
    """Stale personal data is deleted, not summarized."""
    now = time.time()
    sleep_summary._save_pending_visit(tmp_path, ["小諾"], [("user", "舊的")], now - 8 * 24 * 3600)
    client, fake = _client(json.dumps({"小諾": "不該寫入"}))
    assert asyncio.run(sleep_summary.summarize_pending_visits(tmp_path, client=client, now=now)) == 0
    assert not _pending(tmp_path).exists()
    assert fake.calls == []


@pytest.mark.parametrize("content", ["not json", '{"visits": "nope"}', "[]"])
def test_an_unreadable_file_is_deleted(tmp_path: Path, content: str) -> None:
    """A corrupt file is never retried forever."""
    _pending(tmp_path).write_text(content, encoding="utf-8")
    client, fake = _client("{}")
    assert asyncio.run(sleep_summary.summarize_pending_visits(tmp_path, client=client)) == 0
    assert not _pending(tmp_path).exists()
    assert fake.calls == []


def test_a_visit_kept_during_the_startup_summary_survives_it(tmp_path: Path) -> None:
    """The file is re-read before the done visit is removed: a new one is not lost."""
    sleep_summary._save_pending_visit(tmp_path, ["小諾"], [("user", "第一次")], 1000.0)

    class _SavingCompletions:
        calls: list[dict[str, Any]] = []

        def create(self, **kwargs: Any) -> Any:
            self.calls.append(kwargs)
            sleep_summary._save_pending_visit(tmp_path, ["雲霓"], [("user", "第二次")], 2000.0)

            async def answer() -> Any:
                msg = type("M", (), {"content": json.dumps({"小諾": "聊了第一次"})})()
                return type("R", (), {"choices": [type("C", (), {"message": msg})()]})()

            return answer()

    client, _ = _client("{}")
    client.chat.completions = _SavingCompletions()  # type: ignore[assignment]
    assert asyncio.run(sleep_summary.summarize_pending_visits(tmp_path, client=client, now=1000.0)) == 1
    (left,) = json.loads(_pending(tmp_path).read_text(encoding="utf-8"))["visits"]
    assert left["names"] == ["雲霓"]


def test_the_startup_summary_honours_the_kill_switch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """MEMORY_LAST_CHAT_ENABLED=false sends nothing; the visit waits."""
    monkeypatch.setenv("MEMORY_LAST_CHAT_ENABLED", "false")
    sleep_summary._save_pending_visit(tmp_path, ["小諾"], [("user", "我下週要考試")], time.time())
    client, fake = _client("{}")
    assert asyncio.run(sleep_summary.summarize_pending_visits(tmp_path, client=client)) == 0
    assert fake.calls == []
    assert _pending(tmp_path).exists()


def test_nothing_starts_without_a_kept_visit(tmp_path: Path) -> None:
    """No file, no thread: the common start costs nothing."""
    assert sleep_summary.start_pending_summary(tmp_path) is None
    assert sleep_summary.start_pending_summary(None) is None


def test_a_kept_visit_starts_one_background_thread(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The startup hook runs the summary on a daemon thread, off the wake path."""
    sleep_summary._save_pending_visit(tmp_path, ["小諾"], [("user", "我下週要考試")], time.time())
    seen: list[Any] = []
    ran = threading.Event()

    async def fake_summarize(instance_path: Any, **_kw: Any) -> int:
        seen.append((instance_path, threading.current_thread().name))
        ran.set()
        return 0

    monkeypatch.setattr(sleep_summary, "summarize_pending_visits", fake_summarize)
    thread = sleep_summary.start_pending_summary(tmp_path, delay_s=0.0)
    assert thread is not None and thread.daemon
    assert ran.wait(5.0)
    thread.join(5.0)
    assert seen == [(tmp_path, "pending-sleep-summary")]
