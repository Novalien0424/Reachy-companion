"""In a room, a called name is answered even when people keep talking (2026-11-22 plan, N3).

The accepted-turn hold-off waits for the speaker to go on, and every new
`speech_started` restarts the wait. On 2026-11-21, in 多人聊天模式, five calls
by name were each accepted and then answered 18 s and 10 s late, because
someone in the room was always starting to speak. The two sequences below are
that session's, with the times scaled down. A room now holds an answer for one
continuation at most, and never past `REALTIME_HOLDOFF_MAX_DEFER_MS`.
"""

import logging
from typing import Any

import pytest

# `tests/` has no __init__.py, so pytest's prepend import mode puts the
# directory itself on sys.path — import the sibling harness by bare name.
from test_turn_holdoff import _handler, _accepted, _speech_started, _speech_stopped

from reachy_companion.hanova import tv_session
from reachy_companion.conversation_mode import ConversationMode


_LOGGER = "reachy_companion.huggingface_realtime"


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Run on the shipped gates with a 60 ms hold-off and no video on the TV."""
    for name in (
        "REALTIME_DEFAULT_MODE",
        "REALTIME_HOLDOFF_MAX_DEFER_MS",
        "REALTIME_ONE_ON_ONE_ANSWER_GATE",
        "REALTIME_PARTY_BARGE",
        "REALTIME_PARTY_FOLLOWUP_S",
        "REALTIME_SOLO_NAME_GATE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("REALTIME_COMMIT_HOLDOFF_MS", "60")
    tv_session.mark_ended("test setup")
    yield
    tv_session.mark_ended("test teardown")


def _first_call() -> tuple[tuple[float, Any], ...]:
    """23:15:18: 「Ritchie.」, then the request, then the room talks on."""
    return (
        (0.0, _speech_started("item_1")),
        (0.0, _speech_stopped("item_1")),
        # The caller goes on before the name's transcript arrives.
        (0.0, _speech_started("item_2")),
        (0.0, _accepted("item_1", "Ritchie.")),
        (0.01, _speech_stopped("item_2")),
        (0.0, _accepted("item_2", "你再看一次前面。")),
        # Someone starts again 10 ms into the 60 ms window, and never finishes
        # inside this test: on the day that utterance took 7 s to close.
        (0.01, _speech_started("item_3")),
    )


def _second_call() -> tuple[tuple[float, Any], ...]:
    """23:15:59: the name three ways, the request, a follow-up question."""
    return (
        (0.0, _speech_started("item_1")),
        (0.0, _speech_stopped("item_1")),
        (0.0, _speech_started("item_2")),
        (0.0, _accepted("item_1", "瑞奇，瑞奇。")),
        (0.01, _speech_stopped("item_2")),
        (0.0, _accepted("item_2", "Richie！")),
        (0.01, _speech_started("item_3")),
        (0.1, _speech_stopped("item_3")),
        (0.0, _accepted("item_3", "瑞，我要你看前面。")),
        (0.01, _speech_started("item_4")),
        (0.01, _speech_stopped("item_4")),
        (0.0, _accepted("item_4", "你有看到什么?")),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [ConversationMode.GROUP, ConversationMode.RECORD])
async def test_a_called_name_is_answered_after_one_continuation(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, mode: ConversationMode
) -> None:
    """The second onset does not hold the answer back again."""
    handler = _handler(monkeypatch, _first_call(), tail_delay_s=0.15)
    handler._conversation_mode = mode
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        await handler._run_realtime_session()
    assert handler._pending_responses.qsize() == 1
    assert "turn hold-off: answering after one continuation; the room is still talking" in caplog.text
    assert handler._holdoff_firm is False and handler._holdoff_cap_task is None


@pytest.mark.asyncio
async def test_the_bound_can_be_switched_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """`REALTIME_HOLDOFF_MAX_DEFER_MS=0` restores the wait that 1.39.0 had."""
    monkeypatch.setenv("REALTIME_HOLDOFF_MAX_DEFER_MS", "0")
    handler = _handler(monkeypatch, _first_call(), tail_delay_s=0.15)
    handler._conversation_mode = ConversationMode.GROUP
    await handler._run_realtime_session()
    assert handler._pending_responses.qsize() == 0


@pytest.mark.asyncio
async def test_one_on_one_still_waits_for_the_speaker(monkeypatch: pytest.MonkeyPatch) -> None:
    """One person finishing a sentence is what the hold-off is for; no bound there."""
    handler = _handler(monkeypatch, _first_call(), tail_delay_s=0.15)
    handler._conversation_mode = ConversationMode.ONE_ON_ONE
    await handler._run_realtime_session()
    assert handler._pending_responses.qsize() == 0
    assert handler._holdoff_firm is False and handler._holdoff_cap_task is None


@pytest.mark.asyncio
async def test_the_cap_answers_when_the_continuation_never_closes(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A continuation with no transcript inside the cap does not keep the name waiting."""
    monkeypatch.setenv("REALTIME_HOLDOFF_MAX_DEFER_MS", "80")
    handler = _handler(
        monkeypatch,
        (
            (0.0, _speech_started("item_1")),
            (0.0, _speech_stopped("item_1")),
            (0.0, _speech_started("item_2")),
            (0.0, _accepted("item_1", "瑞奇")),
        ),
        tail_delay_s=0.2,
    )
    handler._conversation_mode = ConversationMode.GROUP
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        await handler._run_realtime_session()
    assert handler._pending_responses.qsize() == 1
    assert "turn hold-off: the room talked on for 80 ms; answering the held turn" in caplog.text


@pytest.mark.asyncio
async def test_the_cap_counts_from_the_first_deferral(monkeypatch: pytest.MonkeyPatch) -> None:
    """A second accepted turn inside the cap does not earn a second cap, or a second answer."""
    monkeypatch.setenv("REALTIME_HOLDOFF_MAX_DEFER_MS", "120")
    monkeypatch.setenv("REALTIME_COMMIT_HOLDOFF_MS", "200")
    handler = _handler(
        monkeypatch,
        (
            (0.0, _speech_started("item_1")),
            (0.0, _speech_stopped("item_1")),
            (0.0, _speech_started("item_2")),
            (0.0, _accepted("item_1", "瑞奇")),
            (0.05, _speech_stopped("item_2")),
            # Its 200 ms hold-off would end 250 ms in; the cap ends at 120 ms.
            (0.0, _accepted("item_2", "你再看一次前面。")),
        ),
        tail_delay_s=0.35,
    )
    handler._conversation_mode = ConversationMode.GROUP
    await handler._run_realtime_session()
    assert handler._pending_responses.qsize() == 1


@pytest.mark.asyncio
async def test_the_second_call_of_the_day_gets_both_its_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    """The name is answered, and so is the request that followed it."""
    handler = _handler(monkeypatch, _second_call(), tail_delay_s=0.15)
    handler._conversation_mode = ConversationMode.GROUP
    await handler._run_realtime_session()
    assert handler._pending_responses.qsize() == 2
