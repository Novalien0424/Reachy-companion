"""While a video plays, a soundtrack onset does not defer an accepted turn's answer (W3).

The accepted-turn hold-off (plan rev 3 A1) treats any `speech_started` inside
its window as the speaker going on, and waits for that continuation. A TV
soundtrack starts speech all the time, so 「瑞奇，暫停」 could wait as long as
the clip kept talking. With a video on the TV the hold-off runs its window and
answers; with none, it defers exactly as before.
"""

import logging
from typing import Any

import pytest

# `tests/` has no __init__.py, so pytest's prepend import mode puts the
# directory itself on sys.path — import the sibling harness by bare name.
from test_turn_holdoff import _handler, _accepted, _speech_started

from reachy_companion.hanova import tv_session
from reachy_companion.conversation_mode import ConversationMode


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Run on the shipped gates with a 60 ms hold-off and no video left behind."""
    for name in (
        "REALTIME_DEFAULT_MODE",
        "REALTIME_ONE_ON_ONE_ANSWER_GATE",
        "REALTIME_PARTY_BARGE",
        "REALTIME_SOLO_NAME_GATE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("REALTIME_COMMIT_HOLDOFF_MS", "60")
    tv_session.mark_ended("test setup")
    yield
    tv_session.mark_ended("test teardown")


def _soundtrack_after_a_named_turn() -> tuple[tuple[float, Any], ...]:
    """「瑞奇，暫停」 is accepted, then the clip keeps starting speech inside the window."""
    return (
        (0.0, _speech_started("item_1")),
        (0.0, _accepted("item_1", "瑞奇，暫停")),
        (0.01, _speech_started("item_2")),
        (0.01, _speech_started("item_3")),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [ConversationMode.ONE_ON_ONE, ConversationMode.GROUP])
async def test_a_video_soundtrack_does_not_defer_the_answer(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, mode: ConversationMode
) -> None:
    """The hold-off fires on time while speech keeps starting."""
    tv_session.mark_started("nas")
    handler = _handler(monkeypatch, _soundtrack_after_a_named_turn(), tail_delay_s=0.12)
    handler._conversation_mode = mode
    with caplog.at_level(logging.INFO, logger="reachy_companion.huggingface_realtime"):
        await handler._run_realtime_session()
    assert handler._pending_responses.qsize() == 1
    assert handler._holdoff_owed is False
    assert "turn hold-off: awaiting continuation (" not in caplog.text


@pytest.mark.asyncio
async def test_a_soundtrack_already_started_at_acceptance_does_not_defer_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The clip's next line can start before the named transcript lands; still answered."""
    tv_session.mark_started("youtube")
    handler = _handler(
        monkeypatch,
        (
            (0.0, _speech_started("item_1")),
            (0.0, _speech_started("item_2")),
            (0.0, _accepted("item_1", "瑞奇，停")),
        ),
        tail_delay_s=0.12,
    )
    await handler._run_realtime_session()
    assert handler._pending_responses.qsize() == 1


@pytest.mark.asyncio
async def test_without_a_video_the_continuation_still_defers_it(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """No video: today's behaviour, the onset is a continuation and the answer is owed."""
    handler = _handler(monkeypatch, _soundtrack_after_a_named_turn(), tail_delay_s=0.12)
    with caplog.at_level(logging.INFO, logger="reachy_companion.huggingface_realtime"):
        await handler._run_realtime_session()
    assert handler._pending_responses.qsize() == 0
    assert "turn hold-off: awaiting continuation (" in caplog.text


@pytest.mark.asyncio
async def test_record_mode_keeps_the_continuation_rule_while_a_video_plays(monkeypatch: pytest.MonkeyPatch) -> None:
    """紀錄模式 is untouched by the video, so its hold-off still waits for the continuation."""
    tv_session.mark_started("nas")
    handler = _handler(monkeypatch, _soundtrack_after_a_named_turn(), tail_delay_s=0.12)
    handler._conversation_mode = ConversationMode.RECORD
    await handler._run_realtime_session()
    assert handler._pending_responses.qsize() == 0
