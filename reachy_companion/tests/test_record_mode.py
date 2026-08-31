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


# --------------------------------------------------------------------------
# summarize_conversation (2026-08-31 plan, Task 5)
# --------------------------------------------------------------------------


class _FakeChatClient:
    """Minimal async stand-in for `hanova.images.build_client()`'s AsyncOpenAI."""

    def __init__(self, content: str | None = "會議重點：下週三再開一次。", raises: bool = False) -> None:
        self._content = content
        self._raises = raises
        self.seen_prompt: str | None = None
        self.seen_model: str | None = None
        self.closed = False

        async def _create(**kwargs: object) -> object:
            if self._raises:
                raise RuntimeError("summarizer down")
            messages = kwargs["messages"]
            self.seen_prompt = messages[1]["content"]  # type: ignore[index]
            self.seen_model = kwargs["model"]  # type: ignore[assignment]
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self._content))])

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=_create))

    async def __aenter__(self) -> "_FakeChatClient":
        """Enter the client context, exactly as AsyncOpenAI does."""
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Record that the caller closed the connection pool."""
        self.closed = True


@pytest.mark.asyncio
async def test_summarize_returns_the_friendly_line_for_an_empty_log() -> None:
    """Nothing recorded is a sayable sentence, not an error and not silence."""
    from reachy_companion.record_mode import RECORD_EMPTY_SUMMARY, summarize_record_log

    deps = _deps()
    assert await summarize_record_log(deps, client=_FakeChatClient()) == RECORD_EMPTY_SUMMARY


@pytest.mark.asyncio
async def test_summarize_feeds_every_logged_line_to_the_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both roles reach the prompt, on the small model, with the pool closed after."""
    from reachy_companion.record_mode import summarize_record_log

    monkeypatch.delenv("MEMORY_LAST_CHAT_MODEL", raising=False)
    deps = _deps()
    record_room_transcript(deps, "user", "下週三再開一次")
    record_room_transcript(deps, "assistant", "好的")
    client = _FakeChatClient()
    summary = await summarize_record_log(deps, client=client)
    assert summary == "會議重點：下週三再開一次。"
    assert "下週三再開一次" in (client.seen_prompt or "")
    assert "reachy: 好的" in (client.seen_prompt or "")
    assert client.seen_model == "gpt-5-mini"
    assert client.closed is True


@pytest.mark.asyncio
async def test_summarize_never_raises_when_the_call_fails() -> None:
    """A dead summarizer costs one spoken sentence, never the conversation."""
    from reachy_companion.record_mode import RECORD_SUMMARY_FAILED, summarize_record_log

    deps = _deps()
    record_room_transcript(deps, "user", "下週三再開一次")
    assert await summarize_record_log(deps, client=_FakeChatClient(raises=True)) == RECORD_SUMMARY_FAILED


@pytest.mark.asyncio
async def test_summarize_treats_an_empty_answer_as_a_failure() -> None:
    """A model that returns nothing must not make Reachy say nothing."""
    from reachy_companion.record_mode import RECORD_SUMMARY_FAILED, summarize_record_log

    deps = _deps()
    record_room_transcript(deps, "user", "下週三再開一次")
    assert await summarize_record_log(deps, client=_FakeChatClient(content="   ")) == RECORD_SUMMARY_FAILED
    assert await summarize_record_log(deps, client=_FakeChatClient(content=None)) == RECORD_SUMMARY_FAILED


@pytest.mark.asyncio
async def test_summarize_handles_a_missing_client() -> None:
    """No OPENAI_API_KEY means `build_client()` returns None; that is still sayable."""
    from reachy_companion.record_mode import RECORD_SUMMARY_FAILED, summarize_record_log

    deps = _deps()
    record_room_transcript(deps, "user", "下週三再開一次")
    assert await summarize_record_log(deps, client=None) == RECORD_SUMMARY_FAILED


@pytest.mark.asyncio
async def test_summarize_respects_the_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """A summarizer that hangs must not hang the turn behind it."""
    from reachy_companion.record_mode import RECORD_SUMMARY_FAILED, summarize_record_log

    monkeypatch.setenv("RECORD_SUMMARY_TIMEOUT_S", "1.0")
    deps = _deps()
    record_room_transcript(deps, "user", "下週三再開一次")

    class _SlowClient(_FakeChatClient):
        """A client whose completion never lands inside the budget."""

        def __init__(self) -> None:
            super().__init__()

            async def _create(**kwargs: object) -> object:
                await asyncio.sleep(5.0)
                return None

            self.chat = SimpleNamespace(completions=SimpleNamespace(create=_create))

    assert await summarize_record_log(deps, client=_SlowClient()) == RECORD_SUMMARY_FAILED


@pytest.mark.asyncio
async def test_tool_returns_the_verbatim_envelope() -> None:
    """The tool hands back the text plus the instruction to read it as written."""
    from reachy_companion.tools.summarize_conversation import SummarizeConversation

    deps = _deps()
    record_room_transcript(deps, "user", "下週三再開一次")
    result = await SummarizeConversation()(deps, client=_FakeChatClient())
    assert result == {
        "summary_text": "會議重點：下週三再開一次。",
        "speak_verbatim": True,
        "lines": 1,
    }


def test_tool_description_names_the_envelope_and_the_triggers() -> None:
    """The mini tier follows the description or nothing; both halves must be in it."""
    from reachy_companion.tools.summarize_conversation import SummarizeConversation

    description = SummarizeConversation.description
    for phrase in ("speak_verbatim", "summary_text", "幫我總結", "Do NOT use when", "紀錄模式"):
        assert phrase in description
