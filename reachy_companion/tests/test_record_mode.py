"""紀錄模式: the room transcript log.

Deliberately unlike `deps.session_transcript` (maxlen=40, accepted turns only,
feeds the D-027 sleep summary): the record log keeps EVERY final transcript,
user and assistant, answered and unanswered, for the length of one visit.
"""

import time
import asyncio
from types import SimpleNamespace
from collections import deque
from unittest.mock import MagicMock

import pytest

# `tests/` has no __init__.py, so pytest's prepend import mode puts the
# directory itself on sys.path — import the sibling harness by bare name.
from test_solo_barge import _install_barge_state

from reachy_companion.record_mode import (
    RECORD_LOG_MAX_ITEMS,
    clear_record_log,
    record_room_transcript,
)
from reachy_companion.openai_realtime import OpenAIRealtimeHandler
from reachy_companion.tools.core_tools import ToolDependencies
from reachy_companion.conversation_mode import ConversationMode


def _deps() -> ToolDependencies:
    """Return a real `ToolDependencies` — the record log's own home."""
    return ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())


def _record_handler(mode: ConversationMode = ConversationMode.RECORD) -> OpenAIRealtimeHandler:
    """Return a `__new__`-built handler carrying only mode + barge state."""
    h = OpenAIRealtimeHandler.__new__(OpenAIRealtimeHandler)
    h._conversation_mode = mode
    h._turn_mode = mode
    h._turn_modes = {}
    h._mode_update_seq = 0
    h._session_update_lock = asyncio.Lock()
    h._session_update_event_id = None
    h._session_update_waiter = None
    h._session_update_ack_debt = 0
    # Default to "the loop is running", so an update waits for its ack; the
    # pre-receive-loop tests set this back to False explicitly.
    h._receive_loop_active = True
    h._handler_loop = None
    h._party_last_accept_at = None
    h._party_speech_open = False
    h._party_utterance_seq = 0
    h._party_barge_task = None
    h._active_response_id = None
    h._cancelled_response_ids = deque(maxlen=8)
    h._response_done_event = asyncio.Event()
    h._response_done_event.set()
    h.connection = None
    h._transcript_observer = None
    h.deps = _deps()
    _install_barge_state(h)
    h._clear_queue = MagicMock()
    return h


def test_tool_dependencies_ship_a_bounded_record_log() -> None:
    """The room log is two thousand lines; the sleep-summary tail stays forty."""
    deps = _deps()
    assert deps.record_log.maxlen == RECORD_LOG_MAX_ITEMS == 2000
    # The sleep-summary buffer keeps its own, much smaller, bound.
    assert deps.session_transcript.maxlen == 40


def test_record_room_transcript_stamps_and_skips_noise() -> None:
    """Blank lines and a tool's `[error] …` plumbing are not conversation."""
    deps = _deps()
    record_room_transcript(deps, "user", "  下週三再開一次  ")
    record_room_transcript(deps, "assistant", "")
    record_room_transcript(deps, "assistant", "[error] tool blew up")
    assert [(role, text) for role, text, _ in deps.record_log] == [("user", "下週三再開一次")]
    assert isinstance(deps.record_log[0][2], float)


def test_record_log_drops_the_oldest_at_the_cap() -> None:
    """At the bound the meeting's opening lines go, not its latest."""
    deps = _deps()
    for index in range(RECORD_LOG_MAX_ITEMS + 5):
        record_room_transcript(deps, "user", f"line-{index}")
    assert len(deps.record_log) == RECORD_LOG_MAX_ITEMS
    assert deps.record_log[0][1] == "line-5"


def test_clear_record_log_empties_it() -> None:
    """Nothing recorded survives a clear."""
    deps = _deps()
    record_room_transcript(deps, "user", "abc")
    clear_record_log(deps)
    assert not deps.record_log


def test_a_recorded_user_line_is_presence_but_an_assistant_line_is_not() -> None:
    """紀錄模式 must not read as an empty room to the D-027 sleep summary.

    Ordinary meeting speech is denied by the answer gate, so it never reaches
    `sleep_summary.record_transcript` — the heartbeat that says "talking is
    presence". Recording carries that heartbeat instead, for the lines a person
    actually spoke. `session_transcript` is deliberately NOT fed: a meeting is
    not a chat with Reachy.
    """
    deps = _deps()
    deps.record_recognition("阿明")
    deps.recognized_at["阿明"] = 0.0

    record_room_transcript(deps, "user", "下週三再開一次")
    refreshed = deps.recognized_at["阿明"]
    assert refreshed > 0.0
    assert refreshed <= time.monotonic()
    assert not deps.session_transcript

    record_room_transcript(deps, "assistant", "好的")
    assert deps.recognized_at["阿明"] == refreshed
    assert not deps.session_transcript


def test_emit_transcript_records_every_role_in_record_mode() -> None:
    """The log keeps both sides of the room, in the order they were said."""
    h = _record_handler()
    h._emit_transcript("user", "他說下週三", True)
    h._emit_transcript("assistant", "好的", True)
    assert [(role, text) for role, text, _ in h.deps.record_log] == [
        ("user", "他說下週三"),
        ("assistant", "好的"),
    ]


def test_emit_transcript_records_nothing_outside_record_mode() -> None:
    """Only 紀錄模式 records; the other two modes leave the log empty."""
    for mode in (ConversationMode.ONE_ON_ONE, ConversationMode.GROUP):
        h = _record_handler(mode)
        h._emit_transcript("user", "他說下週三", True)
        assert not h.deps.record_log


def test_emit_transcript_ignores_partials() -> None:
    """A partial is a guess in progress, not a line of the record."""
    h = _record_handler()
    h._emit_transcript("user_partial", "他說下", False)
    assert not h.deps.record_log


def test_emit_transcript_still_reaches_the_observer() -> None:
    """Recording must not swallow the console/JSON-RPC broadcast."""
    seen: list[tuple[str, str, bool]] = []
    h = _record_handler()
    h._transcript_observer = lambda role, text, final: seen.append((role, text, final))
    h._emit_transcript("user", "他說下週三", True)
    assert seen == [("user", "他說下週三", True)]


def test_a_failed_recording_still_lets_the_broadcast_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """The console line is the pre-existing duty; recording is the added one.

    Ordering, not error handling: the broadcast happens first, so a raise while
    recording costs the log a line but never costs the operator the line they
    were watching for. The failure is left to surface — it is not swallowed.
    """
    from reachy_companion import huggingface_realtime as hf_mod

    def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("record log exploded")

    monkeypatch.setattr(hf_mod, "record_room_transcript", _boom)
    seen: list[tuple[str, str, bool]] = []
    h = _record_handler()
    h._transcript_observer = lambda role, text, final: seen.append((role, text, final))

    with pytest.raises(RuntimeError, match="record log exploded"):
        h._emit_transcript("user", "他說下週三", True)

    assert seen == [("user", "他說下週三", True)]


@pytest.mark.asyncio
async def test_a_flip_that_raises_partway_keeps_the_log(monkeypatch: pytest.MonkeyPatch) -> None:
    """The clear is the LAST act of the flip, so a half-flip cannot lose lines.

    `_resume_playback` is the one call in `set_conversation_mode` that runs real
    logic rather than flag assignment. If it raises, the mode change never
    completed — and a recording must not have been thrown away on behalf of a
    flip that did not happen.
    """
    h = _record_handler()
    record_room_transcript(h.deps, "user", "會議內容")
    h._barge_paused = True

    def _boom(**kwargs: object) -> None:
        raise RuntimeError("resume exploded")

    monkeypatch.setattr(h, "_resume_playback", _boom)

    with pytest.raises(RuntimeError, match="resume exploded"):
        await h.set_conversation_mode("one_on_one")

    assert [text for _role, text, _ts in h.deps.record_log] == ["會議內容"]


@pytest.mark.asyncio
async def test_leaving_record_mode_clears_the_log() -> None:
    """In-memory per visit AND per stay in the mode: no files, no export."""
    h = _record_handler()
    record_room_transcript(h.deps, "user", "會議內容")
    await h.set_conversation_mode("one_on_one")
    assert not h.deps.record_log


@pytest.mark.asyncio
async def test_entering_record_mode_keeps_an_empty_log() -> None:
    """Arriving in 紀錄模式 starts a blank recording, not a continued one."""
    h = _record_handler(ConversationMode.ONE_ON_ONE)
    await h.set_conversation_mode("record")
    assert not h.deps.record_log


async def _drive_shutdown(handler: OpenAIRealtimeHandler, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run `shutdown()` on a `__new__`-built handler with the I/O stubbed out."""
    from reachy_companion import huggingface_realtime as hf_mod

    handler._sleep_summary_done = True
    handler._hanova_session = 0
    handler.tool_manager = SimpleNamespace(shutdown=_noop_async())
    handler.partial_transcript_task = None
    handler.output_queue = asyncio.Queue()
    handler.connection = None
    monkeypatch.setattr(hf_mod, "on_session_shutdown", _noop_async())
    monkeypatch.setattr(handler, "_barge_shutdown", _noop_async())
    await handler.shutdown()


@pytest.mark.asyncio
async def test_going_to_sleep_clears_the_record_log(monkeypatch: pytest.MonkeyPatch) -> None:
    """The visit ends at sleep — and nothing recorded outlives the visit."""
    h = _record_handler()
    record_room_transcript(h.deps, "user", "會議內容")
    h.deps.sleep_requested = True
    await _drive_shutdown(h, monkeypatch)
    assert not h.deps.record_log


@pytest.mark.asyncio
async def test_a_settings_restart_keeps_the_record_log(monkeypatch: pytest.MonkeyPatch) -> None:
    """`shutdown()` also runs for settings/backend restarts, mid-meeting.

    D-027 already refuses to write a sleep summary on those; throwing away a
    recording that is still in progress is the same mistake (Codex round 1,
    P1-5).
    """
    h = _record_handler()
    record_room_transcript(h.deps, "user", "會議內容")
    h.deps.sleep_requested = False
    await _drive_shutdown(h, monkeypatch)
    assert [text for _role, text, _ts in h.deps.record_log] == ["會議內容"]


def _noop_async():
    """Return a fresh async no-op, for stubbing an awaited collaborator."""

    async def _inner(*args: object, **kwargs: object) -> None:
        return None

    return _inner
