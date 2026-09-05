"""Music must duck for user speech and come back only when the turn is over.

D-018, R7, review round 1 findings 1 and 3: the resume fires only once the
turn's audio has really drained, and the confirmation gate is session-scoped.
"""

import types
import base64
import asyncio
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

# `tests/` has no __init__.py, so pytest's default prepend import mode puts the
# directory itself on sys.path -- import the sibling module by bare name.
from test_huggingface_realtime import _FakeEvent, _make_fake_realtime_client

import reachy_companion.huggingface_realtime as hf_mod
from reachy_companion.hanova import audio_drain, music_hooks
from reachy_companion.hanova.confirm import GATE
from reachy_companion.tools.core_tools import ToolDependencies
from reachy_companion.conversation_mode import ConversationMode
from reachy_companion.hanova.music_player import PLAYER
from reachy_companion.huggingface_realtime import HuggingFaceRealtimeHandler


HF_TEST_VOICE = "cedar"


class _Ok:
    status_code = 200


def _deps(tmp_path=None):
    robot = types.SimpleNamespace(_daemon_http_url="http://127.0.0.1:8000")
    return types.SimpleNamespace(reachy_mini=robot, instance_path=tmp_path)


async def _until(predicate, poll_s: float = 0.005):
    """Await a condition a detached hook task will eventually make true.

    The hooks are deliberately fire-and-forget, so a fixed sleep is either flaky
    or slow. This polls instead, and the caller wraps it in `asyncio.wait_for`.
    """
    while not predicate():
        await asyncio.sleep(poll_s)


@pytest.fixture(autouse=True)
def quiet_session(monkeypatch):
    """Neutralise everything a realtime session touches except our hooks."""
    monkeypatch.setattr(hf_mod, "get_session_instructions", lambda _instance_path=None: "test")
    monkeypatch.setattr(hf_mod, "get_session_voice", lambda default=HF_TEST_VOICE: default)
    monkeypatch.setattr(hf_mod, "get_tool_specs", lambda exclusion_list=None: [])
    monkeypatch.setattr("reachy_companion.hanova.settings._music_wheels_ready", lambda: (True, ""))
    PLAYER.reset()
    GATE.reset()
    audio_drain.reset()
    music_hooks.reset_for_tests()
    yield
    PLAYER.reset()
    GATE.reset()
    audio_drain.reset()
    music_hooks.reset_for_tests()


@pytest.fixture
def ok_daemon(monkeypatch):
    """Install a daemon that acknowledges everything, recording what it was told."""
    calls: list[str] = []

    async def ok_post(self, url, json=None, **kwargs):
        calls.append(url)
        return _Ok()

    monkeypatch.setattr(httpx.AsyncClient, "post", ok_post)
    return calls


def _handler_with(events: tuple[_FakeEvent, ...]) -> HuggingFaceRealtimeHandler:
    handler = HuggingFaceRealtimeHandler(ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock()))
    # Since the 2026-08-31 mode wave a real handler boots into 多人聊天模式,
    # whose speech events take the room branch. The solo ducking hooks these
    # tests exercise live on the other branch, so pin the mode.
    handler._conversation_mode = ConversationMode.ONE_ON_ONE
    handler.client = _make_fake_realtime_client(events=events)
    return handler


# --- the drain tracker (finding 1, rebuilt in round 2) --------------------
@pytest.mark.asyncio
async def test_an_open_response_is_never_drained_even_with_no_audio_yet():
    """Round 2, finding 1: pending is the *default*, not something audio sets.

    The old tracker began "empty" and only learned about audio it had already
    played, so `wait_drained()` said yes before a single byte left the queue.
    """
    generation = audio_drain.begin_response()
    assert await audio_drain.wait_drained(generation, timeout_s=0.05) is False


@pytest.mark.asyncio
async def test_drain_waits_for_the_local_queue_and_the_device_buffer():
    """`response.done` is not "the audio finished"; this is what finishing means."""
    generation = audio_drain.begin_response()
    audio_drain.note_enqueued(generation, sample_count=24000, sample_rate=24000)  # 1 s queued
    audio_drain.close_response(generation)
    assert await audio_drain.wait_drained(generation, timeout_s=0.05) is False, (
        "closed, but a second of audio is still sitting in the queue"
    )

    audio_drain.note_chunk(sample_count=24000, sample_rate=24000)  # handed to the sink
    assert await audio_drain.wait_drained(generation, timeout_s=0.05) is False, (
        "handed to the sink is not the same as heard; the device buffer holds it"
    )

    audio_drain.note_queue_empty()
    assert await audio_drain.wait_drained(generation, timeout_s=0.05) is False, (
        "an empty queue with audio still in the device buffer is not drained"
    )

    audio_drain.note_cleared()
    assert await audio_drain.wait_drained(generation, timeout_s=0.5) is True


@pytest.mark.asyncio
async def test_drain_is_immediately_true_for_a_closed_response_with_no_audio():
    """A text-only or tool-only turn has no audio to wait for."""
    generation = audio_drain.begin_response()
    audio_drain.close_response(generation)
    assert await audio_drain.wait_drained(generation, timeout_s=0.5) is True


@pytest.mark.asyncio
async def test_enqueued_audio_is_outstanding_until_it_is_played():
    """The accounting the round-1 tracker did not have at all."""
    generation = audio_drain.begin_response()
    audio_drain.note_enqueued(generation, sample_count=48000, sample_rate=24000)  # 2 s
    assert audio_drain.outstanding_s() == pytest.approx(2.0, abs=0.01)
    audio_drain.note_chunk(sample_count=24000, sample_rate=24000)
    assert audio_drain.outstanding_s() == pytest.approx(1.0, abs=0.01)
    audio_drain.note_chunk(sample_count=24000, sample_rate=24000)
    assert audio_drain.outstanding_s() == pytest.approx(0.0, abs=0.01)


@pytest.mark.asyncio
async def test_device_buffered_s_reports_the_sink_side_estimate():
    """Handed to the sink is not heard yet — the truncate accounting needs the gap.

    `outstanding_s()` retires audio the moment `note_chunk` hands it over, so
    without this second term "enqueued − outstanding" would count up to a
    second of device-buffered speech as already heard.
    """
    assert audio_drain.device_buffered_s() == 0.0, "nothing handed over yet"
    generation = audio_drain.begin_response()
    audio_drain.note_enqueued(generation, sample_count=24000, sample_rate=24000)
    audio_drain.note_chunk(sample_count=24000, sample_rate=24000)  # 1 s to the sink
    assert audio_drain.device_buffered_s() == pytest.approx(1.0, abs=0.05)
    audio_drain.note_cleared()
    assert audio_drain.device_buffered_s() == 0.0, "a flush drops the estimate, never waits it out"


@pytest.mark.asyncio
async def test_barge_in_clear_discards_the_pending_device_buffer():
    """When the queue is flushed the estimate must be dropped, not waited out."""
    generation = audio_drain.begin_response()
    audio_drain.note_enqueued(generation, sample_count=24000 * 30, sample_rate=24000)  # 30 s
    audio_drain.note_chunk(sample_count=24000 * 30, sample_rate=24000)
    audio_drain.note_cleared()
    assert await audio_drain.wait_drained(generation, timeout_s=0.5) is True
    assert audio_drain.outstanding_s() == 0.0


@pytest.mark.asyncio
async def test_a_stale_generation_never_blocks_a_newer_one():
    """A superseded turn must not park the next turn's resume forever."""
    stale = audio_drain.begin_response()
    audio_drain.note_enqueued(stale, sample_count=24000, sample_rate=24000)
    fresh = audio_drain.begin_response()
    audio_drain.close_response(fresh)
    # The barge-in that produced the new response also flushed the queue.
    audio_drain.note_cleared()
    assert await audio_drain.wait_drained(fresh, timeout_s=0.5) is True


# --- resume timing (finding 1) --------------------------------------------
@pytest.mark.asyncio
async def test_resume_waits_for_the_drain_signal(ok_daemon, tmp_path, monkeypatch):
    """The whole point: the track comes back after Reachy stops, not during."""
    deps = _deps(tmp_path)
    track = tmp_path / "abc.mp3"
    track.write_bytes(b"ID3")
    await PLAYER.play(deps, video_id="abc", title="A Song", source_path=track)
    await PLAYER.pause_for_speech(deps)

    resumed = asyncio.Event()

    async def record_resume(_deps):
        resumed.set()
        return {"ok": True, "status": "resumed"}

    monkeypatch.setattr(PLAYER, "resume_after_speech", record_resume)

    music_hooks.on_response_created()
    music_hooks.on_response_audio(sample_count=24000, sample_rate=24000)
    music_hooks.on_assistant_turn_ended(deps)
    await asyncio.sleep(0.02)
    assert not resumed.is_set(), "resume must not fire while audio is still queued"

    audio_drain.note_chunk(sample_count=24000, sample_rate=24000)
    audio_drain.note_queue_empty()
    audio_drain.note_cleared()
    await asyncio.wait_for(resumed.wait(), timeout=1.0)


@pytest.mark.asyncio
async def test_audio_queued_before_response_done_still_blocks_the_resume(ok_daemon, tmp_path, monkeypatch):
    """Round 2, finding 1, mandatory case: **delayed playback after response.done**.

    This is the exact failure the round-1 "fix" still had. Every delta has been
    received and `response.done` has fired, but `play_loop` has not dequeued a
    single sample yet -- the tracker must be pending, not empty. The resume may
    only fire once the audio has actually been played out.
    """
    deps = _deps(tmp_path)
    track = tmp_path / "abc.mp3"
    track.write_bytes(b"ID3")
    await PLAYER.play(deps, video_id="abc", title="A Song", source_path=track)
    await PLAYER.pause_for_speech(deps)

    resumed = asyncio.Event()

    async def record_resume(_deps):
        resumed.set()
        return {"ok": True, "status": "resumed"}

    monkeypatch.setattr(PLAYER, "resume_after_speech", record_resume)

    music_hooks.on_response_created()
    for _ in range(10):  # ten 100 ms deltas, all enqueued, none played
        music_hooks.on_response_audio(sample_count=2400, sample_rate=24000)
    music_hooks.on_assistant_turn_ended(deps)  # response.done arrives here

    await asyncio.sleep(0.15)
    assert not resumed.is_set(), "response.done with a full queue is not a drained turn"
    assert audio_drain.outstanding_s() == pytest.approx(1.0, abs=0.05)

    # play_loop now catches up, one chunk at a time.
    for _ in range(10):
        audio_drain.note_chunk(sample_count=2400, sample_rate=24000)
        await asyncio.sleep(0)
    assert not resumed.is_set(), "the device buffer still holds the tail"

    audio_drain.note_queue_empty()
    audio_drain.note_cleared()
    await asyncio.wait_for(resumed.wait(), timeout=1.0)


@pytest.mark.asyncio
async def test_outstanding_audio_past_the_report_interval_still_resumes(ok_daemon, tmp_path, monkeypatch):
    """Round 3, finding 1, mandatory case: **more than 12 s of outstanding audio**.

    The old `_resume_when_drained()` gave `wait_drained` a 12-second timeout and
    returned for good when it expired, so a long reply -- or a sink that fell
    behind -- left the music paused for the rest of the conversation. The
    interval is now diagnostic: the waiter logs and keeps waiting.

    The interval is shrunk here so the test spends milliseconds rather than
    minutes; what is NOT shrunk is the audio, which is a real 30 seconds of
    outstanding PCM -- comfortably past the 12-second mark that used to be fatal.
    """
    monkeypatch.setattr(music_hooks, "_DRAIN_REPORT_EVERY_S", 0.05)
    deps = _deps(tmp_path)
    track = tmp_path / "abc.mp3"
    track.write_bytes(b"ID3")
    await PLAYER.play(deps, video_id="abc", title="A Song", source_path=track)
    await PLAYER.pause_for_speech(deps)

    resumed = asyncio.Event()

    async def record_resume(_deps):
        resumed.set()
        return {"ok": True, "status": "resumed"}

    monkeypatch.setattr(PLAYER, "resume_after_speech", record_resume)

    music_hooks.on_response_created()
    music_hooks.on_response_audio(sample_count=24000 * 30, sample_rate=24000)  # 30 s
    music_hooks.on_assistant_turn_ended(deps)

    # Six report intervals go by with the audio still outstanding. The old code
    # had given up permanently by this point.
    await asyncio.sleep(0.3)
    assert not resumed.is_set()
    assert audio_drain.outstanding_s() == pytest.approx(30.0, abs=0.05)

    audio_drain.note_chunk(sample_count=24000 * 30, sample_rate=24000)
    audio_drain.note_queue_empty()
    audio_drain.note_cleared()
    await asyncio.wait_for(resumed.wait(), timeout=1.0)


@pytest.mark.asyncio
async def test_a_tool_call_defers_the_resume_to_the_follow_up_turn(ok_daemon, tmp_path, monkeypatch):
    """A tool turn is followed by a second, speaking response; do not resume between."""
    deps = _deps(tmp_path)
    calls: list[str] = []

    async def record_resume(_deps):
        calls.append("resume")
        return {"ok": True, "status": "resumed"}

    monkeypatch.setattr(PLAYER, "resume_after_speech", record_resume)

    music_hooks.on_response_created()
    music_hooks.on_tool_call_started("call-a")
    music_hooks.on_assistant_turn_ended(deps, {"call-a"})  # response.done for the tool turn
    await asyncio.sleep(0.05)
    assert calls == [], "a tool call still in flight means the turn is not over"

    music_hooks.on_tool_call_finished("call-a", needs_response=True)
    await asyncio.sleep(0.05)
    assert calls == [], "a follow-up response is coming; do not resume in the gap"

    music_hooks.on_response_created()  # the follow-up response
    music_hooks.on_assistant_turn_ended(deps)
    await asyncio.sleep(0.05)
    assert calls == ["resume"]


@pytest.mark.asyncio
async def test_a_final_tool_batch_with_no_follow_up_resumes_the_music(ok_daemon, tmp_path, monkeypatch):
    """Round 2, finding 1, mandatory case: **needs_response=False**.

    A tool batch that wants no reply produces no further `response.created`, so
    nothing was left to re-schedule the resume and the music stayed paused for
    the rest of the conversation. This path must close the turn itself.
    """
    deps = _deps(tmp_path)
    await music_hooks.on_session_started(deps)  # this is what supplies _DEPS
    calls: list[str] = []

    async def record_resume(_deps):
        calls.append("resume")
        return {"ok": True, "status": "resumed"}

    monkeypatch.setattr(PLAYER, "resume_after_speech", record_resume)

    music_hooks.on_response_created()
    music_hooks.on_tool_call_started("call-a")
    music_hooks.on_assistant_turn_ended(deps, {"call-a"})  # response.done for the tool turn
    await asyncio.sleep(0.02)
    assert calls == []

    music_hooks.on_tool_call_finished("call-a", needs_response=False)
    await asyncio.wait_for(_until(lambda: calls == ["resume"]), timeout=1.0)


@pytest.mark.asyncio
async def test_only_the_last_tool_of_a_batch_closes_the_turn(ok_daemon, tmp_path, monkeypatch):
    """Two tools in one batch: the first finishing is not the batch finishing."""
    deps = _deps(tmp_path)
    await music_hooks.on_session_started(deps)
    calls: list[str] = []

    async def record_resume(_deps):
        calls.append("resume")
        return {"ok": True, "status": "resumed"}

    monkeypatch.setattr(PLAYER, "resume_after_speech", record_resume)

    music_hooks.on_response_created()
    music_hooks.on_tool_call_started("call-a")
    music_hooks.on_tool_call_started("call-b")
    music_hooks.on_assistant_turn_ended(deps, {"call-a", "call-b"})
    music_hooks.on_tool_call_finished("call-a", needs_response=False)
    await asyncio.sleep(0.05)
    assert calls == [], "one tool of two finishing does not end the batch"

    music_hooks.on_tool_call_finished("call-b", needs_response=False)
    await asyncio.wait_for(_until(lambda: calls == ["resume"]), timeout=1.0)


@pytest.mark.asyncio
async def test_a_tool_that_never_reports_back_does_not_strand_the_music(ok_daemon, tmp_path, monkeypatch):
    """Fix round, finding 2: a tool the manager cancels must not park the resume.

    `BackgroundToolManager._cleanup` calls `timeout_tools()`, which cancels the
    tool's task; `_run_tool` guards `Exception`, which does not catch
    `CancelledError`, so no notification is ever queued and
    `on_tool_call_finished` never fires for that call. A bare counter therefore
    stayed at 1 for the rest of the session: every later turn end returned early
    and the track stayed ducked until the connection dropped. The phase now keys
    on the call ids the handler already maintains, and the turn-end path
    reconciles against them.
    """
    deps = _deps(tmp_path)
    calls: list[str] = []

    async def record_resume(_deps):
        calls.append("resume")
        return {"ok": True, "status": "resumed"}

    monkeypatch.setattr(PLAYER, "resume_after_speech", record_resume)

    music_hooks.on_response_created()
    music_hooks.on_tool_call_started("call-timeout")
    music_hooks.on_assistant_turn_ended(deps, {"call-timeout"})
    await asyncio.sleep(0.05)
    assert calls == [], "a call the handler still lists as live defers the resume"

    # The tool is cancelled without notifying anyone. The handler drops the dead
    # call id from its own in-flight set on the next completed user transcript,
    # so the turn that follows presents a set that no longer contains it.
    music_hooks.on_response_created()
    music_hooks.on_assistant_turn_ended(deps, set())
    await asyncio.wait_for(_until(lambda: calls == ["resume"]), timeout=1.0)


@pytest.mark.asyncio
async def test_a_new_response_cancels_a_pending_resume(ok_daemon, tmp_path, monkeypatch):
    """If Reachy starts talking again, the music must stay down."""
    deps = _deps(tmp_path)
    calls: list[str] = []

    async def record_resume(_deps):
        calls.append("resume")
        return {"ok": True, "status": "resumed"}

    monkeypatch.setattr(PLAYER, "resume_after_speech", record_resume)

    music_hooks.on_response_created()
    music_hooks.on_response_audio(sample_count=24000, sample_rate=24000)
    music_hooks.on_assistant_turn_ended(deps)
    music_hooks.on_response_created()
    await asyncio.sleep(0.05)
    audio_drain.note_cleared()
    await asyncio.sleep(0.05)
    assert calls == []


# --- the barge-in's own turn end (fix round, finding 1) -------------------
@pytest.mark.asyncio
async def test_a_barge_in_does_not_resume_on_its_own_response_done(ok_daemon, tmp_path, monkeypatch):
    """Fix round, finding 1: an interrupted turn must not schedule a resume.

    R7's headline scenario. The track is audible, Reachy is mid-sentence -- the
    "now playing" confirmation that follows `play_music` -- and the user cuts in.
    `speech_started` ducks the music and flushes the playback queue, and that
    flush satisfies all four drain conditions at once. So the `response.done` the
    backend sends for the response it has just cancelled used to schedule a
    resume that fired immediately: the track came back under the user's own
    voice, and stayed up under the whole of the next reply.
    """
    deps = _deps(tmp_path)
    track = tmp_path / "abc.mp3"
    track.write_bytes(b"ID3")
    await PLAYER.play(deps, video_id="abc", title="A Song", source_path=track)

    calls: list[str] = []

    async def record_resume(_deps):
        calls.append("resume")
        return {"ok": True, "status": "resumed"}

    monkeypatch.setattr(PLAYER, "resume_after_speech", record_resume)

    music_hooks.on_response_created()
    music_hooks.on_response_audio(sample_count=24000, sample_rate=24000)

    music_hooks.on_user_speech_started(deps)  # the barge-in
    music_hooks.on_assistant_turn_ended(deps)  # response.done for the cancelled response
    await music_hooks.drain_pending_for_tests()
    await asyncio.sleep(0.05)

    assert calls == [], "the interrupted turn's response.done put the music back under the user"
    state = PLAYER.current()
    assert state is not None and state.paused is True, "the music must stay ducked"


@pytest.mark.asyncio
async def test_the_turn_after_a_barge_in_is_the_one_that_resumes(ok_daemon, tmp_path, monkeypatch):
    """Fix round, finding 1, the other half: declining is a deferral, not a drop.

    The resume the interrupted turn refuses to schedule has to arrive from the
    end of the turn that answers the user -- and not one moment before that
    turn's own audio has drained.
    """
    deps = _deps(tmp_path)
    track = tmp_path / "abc.mp3"
    track.write_bytes(b"ID3")
    await PLAYER.play(deps, video_id="abc", title="A Song", source_path=track)

    calls: list[str] = []

    async def record_resume(_deps):
        calls.append("resume")
        return {"ok": True, "status": "resumed"}

    monkeypatch.setattr(PLAYER, "resume_after_speech", record_resume)

    music_hooks.on_response_created()
    music_hooks.on_response_audio(sample_count=24000, sample_rate=24000)
    music_hooks.on_user_speech_started(deps)
    music_hooks.on_assistant_turn_ended(deps)  # the interrupted response
    await music_hooks.drain_pending_for_tests()
    await asyncio.sleep(0.05)
    assert calls == [], "the interrupted turn resumed instead of deferring"

    music_hooks.on_response_created()  # the reply to what the user just said
    music_hooks.on_response_audio(sample_count=24000, sample_rate=24000)
    music_hooks.on_assistant_turn_ended(deps)
    await asyncio.sleep(0.05)
    assert calls == [], "the reply's own audio is still queued"

    audio_drain.note_chunk(sample_count=24000, sample_rate=24000)
    audio_drain.note_queue_empty()
    audio_drain.note_cleared()
    await asyncio.wait_for(_until(lambda: calls == ["resume"]), timeout=1.0)


# --- the receiver must never block (finding 1) ----------------------------
@pytest.mark.asyncio
async def test_speech_started_hook_returns_without_awaiting_the_daemon(monkeypatch):
    """A five-second daemon timeout inside the event receiver stalls every event."""
    entered = asyncio.Event()
    release = asyncio.Event()

    async def slow_pause(_deps):
        entered.set()
        await release.wait()
        return {"ok": True, "status": "paused"}

    monkeypatch.setattr(PLAYER, "pause_for_speech", slow_pause)
    deps = _deps()

    music_hooks.on_user_speech_started(deps)  # note: NOT awaited -- it is sync
    await asyncio.sleep(0)
    assert entered.is_set(), "the pause must have been scheduled"
    release.set()
    await music_hooks.drain_pending_for_tests()


@pytest.mark.asyncio
async def test_user_speech_pauses_the_music(monkeypatch: Any) -> None:
    """Barge-in must duck the speaker, not talk over it.

    Since Task 8 solo speech-start is a *candidate*, not a confirmed
    interruption, so the duck comes from `on_user_speech_candidate` — which
    ducks without `audio_drain.note_cleared()`, the accounting a rollback
    depends on. `REALTIME_SOLO_CLIENT_BARGE=0` restores the old hook.
    """
    calls: list[str] = []

    monkeypatch.setattr(hf_mod, "on_user_speech_started", lambda _deps: calls.append("pause"))
    monkeypatch.setattr(hf_mod, "on_user_speech_candidate", lambda _deps: calls.append("candidate"))
    monkeypatch.setattr(hf_mod, "on_assistant_turn_ended", lambda _deps, _live=None: None)
    monkeypatch.setattr(hf_mod, "on_response_created", lambda: None)

    handler = _handler_with((_FakeEvent("input_audio_buffer.speech_started"),))
    await handler._run_realtime_session()
    assert calls == ["candidate"]


@pytest.mark.asyncio
async def test_legacy_solo_barge_still_ducks_through_the_old_hook(monkeypatch: Any) -> None:
    """REALTIME_SOLO_CLIENT_BARGE=0 must reproduce the pre-Task-8 wiring exactly."""
    monkeypatch.setenv("REALTIME_SOLO_CLIENT_BARGE", "0")
    calls: list[str] = []

    monkeypatch.setattr(hf_mod, "on_user_speech_started", lambda _deps: calls.append("pause"))
    monkeypatch.setattr(hf_mod, "on_user_speech_candidate", lambda _deps: calls.append("candidate"))
    monkeypatch.setattr(hf_mod, "on_assistant_turn_ended", lambda _deps, _live=None: None)
    monkeypatch.setattr(hf_mod, "on_response_created", lambda: None)

    handler = _handler_with((_FakeEvent("input_audio_buffer.speech_started"),))
    await handler._run_realtime_session()
    assert calls == ["pause"]


@pytest.mark.asyncio
async def test_the_loop_routes_solo_speech_through_the_barge_hooks(monkeypatch: Any) -> None:
    """Task 8's decision points have to be reached from the real event loop.

    The pause/rollback machinery is unit-tested in `tests/test_solo_barge.py`;
    this is the wiring: solo speech start and stop must go through the hooks
    that own the decision, not the old inline flush.
    """
    calls: list[str] = []

    monkeypatch.setattr(hf_mod.HuggingFaceRealtimeHandler, "_solo_speech_started", lambda self, item_id=None: calls.append("started"))
    monkeypatch.setattr(hf_mod.HuggingFaceRealtimeHandler, "_solo_speech_stopped", lambda self: calls.append("stopped"))
    monkeypatch.setattr(hf_mod, "on_assistant_turn_ended", lambda _deps, _live=None: None)
    monkeypatch.setattr(hf_mod, "on_response_created", lambda: None)

    handler = _handler_with(
        (
            _FakeEvent("input_audio_buffer.speech_started"),
            _FakeEvent("input_audio_buffer.speech_stopped"),
        )
    )
    await handler._run_realtime_session()
    assert calls == ["started", "stopped"]


@pytest.mark.asyncio
async def test_finished_audio_ends_the_turn(monkeypatch: Any) -> None:
    """When Reachy stops talking, the end-of-turn hook fires."""
    calls: list[str] = []

    monkeypatch.setattr(hf_mod, "on_user_speech_started", lambda _deps: None)
    monkeypatch.setattr(hf_mod, "on_assistant_turn_ended", lambda _deps, _live=None: calls.append("turn_end"))
    monkeypatch.setattr(hf_mod, "on_response_created", lambda: None)

    handler = _handler_with((_FakeEvent("response.output_audio.done"),))
    await handler._run_realtime_session()
    assert calls == ["turn_end"]


@pytest.mark.asyncio
async def test_text_only_turn_also_ends_the_turn(monkeypatch: Any) -> None:
    """Tool-only and text-only responses emit no output_audio.done."""
    calls: list[str] = []

    monkeypatch.setattr(hf_mod, "on_user_speech_started", lambda _deps: None)
    monkeypatch.setattr(hf_mod, "on_assistant_turn_ended", lambda _deps, _live=None: calls.append("turn_end"))
    monkeypatch.setattr(hf_mod, "on_response_created", lambda: None)

    handler = _handler_with((_FakeEvent("response.done"),))
    await handler._run_realtime_session()
    assert calls == ["turn_end"]


@pytest.mark.asyncio
async def test_the_receiver_reports_audio_before_it_is_queued(monkeypatch: Any) -> None:
    """Round 2, finding 1: the pre-enqueue notification is a wiring requirement.

    If the receiver does not call this, the drain tracker never learns that
    audio exists until `play_loop` dequeues it -- which is precisely the race
    that made the round-1 fix cosmetic.
    """
    seen: list[int] = []

    monkeypatch.setattr(hf_mod, "on_user_speech_started", lambda _deps: None)
    monkeypatch.setattr(hf_mod, "on_assistant_turn_ended", lambda _deps, _live=None: None)
    monkeypatch.setattr(hf_mod, "on_response_created", lambda: None)
    monkeypatch.setattr(hf_mod, "on_response_audio", lambda sample_count, sample_rate: seen.append(sample_count))

    # The realtime API sends `delta` as base64 text, which the branch decodes
    # before it counts frames -- so the fake event has to carry base64 too, or
    # the decode yields nothing and the assertion below measures the encoding
    # rather than the audio.
    delta = base64.b64encode(b"\x00\x00" * 240).decode("ascii")
    handler = _handler_with((_FakeEvent("response.output_audio.delta", delta=delta),))
    await handler._run_realtime_session()
    assert seen == [240], "each audio delta must be reported before it is enqueued"


# --- session lifecycle (finding 3, round 2 finding 8) ---------------------
@pytest.mark.asyncio
async def test_session_start_opens_a_new_confirmation_epoch(ok_daemon):
    """Finding 3: a reconnect must not inherit the previous conversation's gate."""
    GATE.begin_session()
    GATE.arm("email_send", "send mail", {"to": "a@example.com"})
    stale_epoch = GATE.epoch()

    await music_hooks.on_session_started(_deps())

    assert GATE.epoch() != stale_epoch
    assert GATE.claim("email_send") is None


@pytest.mark.asyncio
async def test_session_start_invalidates_and_silences_the_player(ok_daemon, tmp_path):
    """Round 2, finding 8: `reset()` forgot the state and left the speaker on.

    A reconnect must actually stop the daemon and advance the generation, or
    audio from the previous conversation keeps playing into the new one.
    """
    deps = _deps(tmp_path)
    track = tmp_path / "abc.mp3"
    track.write_bytes(b"ID3")
    await PLAYER.play(deps, video_id="abc", title="A Song", source_path=track)
    generation_before = PLAYER.generation()
    ok_daemon.clear()

    await music_hooks.on_session_started(deps)

    assert PLAYER.current() is None
    assert PLAYER.generation() > generation_before, "the generation must advance, not just the state"
    assert any(url.endswith("/api/media/stop_sound") for url in ok_daemon)


@pytest.mark.asyncio
async def test_a_transition_in_flight_across_a_session_boundary_cannot_come_back(ok_daemon, tmp_path, monkeypatch):
    """Round 2, finding 8, stated as the failure it prevents.

    A `play` that was mid-I/O when the backend reconnected used to finish
    afterwards and write its state back over a session that no longer exists.
    """
    deps = _deps(tmp_path)
    track = tmp_path / "abc.mp3"
    track.write_bytes(b"ID3")

    started = asyncio.Event()
    release = asyncio.Event()
    real_post = httpx.AsyncClient.post

    async def slow_post(self, url, json=None, **kwargs):
        if url.endswith("/api/media/play_sound"):
            started.set()
            await release.wait()
        return await real_post(self, url, json=json, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "post", slow_post)
    play_task = asyncio.create_task(PLAYER.play(deps, video_id="abc", title="A Song", source_path=track))
    await asyncio.wait_for(started.wait(), timeout=1.0)

    # The reconnect and the release have to overlap: `on_session_started` stops
    # the daemon under the player's transition lock, which this play still
    # holds, so awaiting the whole hook before releasing the play would wedge
    # both. Real life is exactly this overlap -- the connection drops while the
    # play is still talking to the daemon, and the daemon answers a moment
    # later. `invalidate()` runs before the hook's first await, so the play is
    # already superseded by the time it wakes up.
    session_start = asyncio.create_task(music_hooks.on_session_started(deps))
    await asyncio.sleep(0)
    release.set()
    await asyncio.wait_for(session_start, timeout=2.0)
    result = await play_task

    assert result.get("status") == "superseded"
    assert PLAYER.current() is None, "a superseded play must not repopulate the state"


@pytest.mark.asyncio
async def test_shutdown_stops_the_music_and_closes_the_gate(ok_daemon, tmp_path):
    """The daemon keeps playing after our session dies; and so did the gate."""
    deps = _deps(tmp_path)
    token = await music_hooks.on_session_started(deps)  # round 3, finding 2
    track = tmp_path / "abc.mp3"
    track.write_bytes(b"ID3")
    await PLAYER.play(deps, video_id="abc", title="A Song", source_path=track)
    GATE.arm("drive_trash", "move a file to Trash", {"file_id": "f1"})
    ok_daemon.clear()

    await music_hooks.on_session_shutdown(deps, token)

    assert PLAYER.current() is None
    assert GATE.claim("drive_trash") is None
    assert any(url.endswith("/api/media/stop_sound") for url in ok_daemon)


@pytest.mark.asyncio
async def test_an_overlapping_reconnect_ignores_the_stale_shutdown(ok_daemon, tmp_path):
    """Round 3, finding 2, mandatory case: **the replaced connection's `finally`**.

    The handler can open a replacement connection before the previous one's
    `finally` has run. That late cleanup used to tear down the session that
    replaced it -- clearing the new drain state, ending the new gate epoch and
    nulling the deps the tool-completion path needs. It must now do nothing.
    """
    deps = _deps(tmp_path)
    old_token = await music_hooks.on_session_started(deps)
    new_token = await music_hooks.on_session_started(deps)  # the reconnect
    assert new_token != old_token

    GATE.arm("drive_trash", "move a file to Trash", {"file_id": "f1"})
    track = tmp_path / "abc.mp3"
    track.write_bytes(b"ID3")
    await PLAYER.play(deps, video_id="abc", title="A Song", source_path=track)
    ok_daemon.clear()

    await music_hooks.on_session_shutdown(deps, old_token)  # the stale finally

    assert GATE.claim("drive_trash") is not None, "the stale cleanup closed the live gate"
    assert PLAYER.current() is not None, "the stale cleanup silenced the live session"
    assert ok_daemon == [], "the stale cleanup talked to the daemon"

    # And the live token still works, which is what makes this a scoping fix
    # rather than a shutdown that stopped working.
    await music_hooks.on_session_shutdown(deps, new_token)
    assert PLAYER.current() is None
    assert GATE.claim("drive_trash") is None


@pytest.mark.asyncio
async def test_the_session_hooks_run_in_the_connection_finally(monkeypatch: Any) -> None:
    """Round 2, finding 8: cleanup belongs to the connection, not to shutdown.

    A realtime connection that drops without the handler shutting down is the
    common case, and it was leaving the speaker running and the gate armed.
    `_run_realtime_session()` alone must open **and** close the session.
    """
    calls: list[str] = []
    tokens: list[int] = []

    async def record_start(_deps):
        calls.append("start")
        return 7  # round 3, finding 2: start mints a token

    async def record_stop(_deps, token):
        calls.append("stop")
        tokens.append(token)

    monkeypatch.setattr(hf_mod, "on_session_started", record_start)
    monkeypatch.setattr(hf_mod, "on_session_shutdown", record_stop)
    monkeypatch.setattr(hf_mod, "on_user_speech_started", lambda _deps: None)
    monkeypatch.setattr(hf_mod, "on_assistant_turn_ended", lambda _deps, _live=None: None)
    monkeypatch.setattr(hf_mod, "on_response_created", lambda: None)

    handler = _handler_with(())
    await handler._run_realtime_session()
    assert calls == ["start", "stop"], "the finally must close the session on its own"
    assert tokens == [7], "the finally must present the token its own session was minted with"


@pytest.mark.asyncio
async def test_the_session_is_closed_even_when_the_connection_raises(monkeypatch: Any) -> None:
    """The `finally` is only worth having if it survives the error path."""
    calls: list[str] = []

    async def record_start(_deps):
        calls.append("start")
        return 11

    async def record_stop(_deps, _token):
        calls.append("stop")

    monkeypatch.setattr(hf_mod, "on_session_started", record_start)
    monkeypatch.setattr(hf_mod, "on_session_shutdown", record_stop)
    monkeypatch.setattr(hf_mod, "on_user_speech_started", lambda _deps: None)
    monkeypatch.setattr(hf_mod, "on_assistant_turn_ended", lambda _deps, _live=None: None)
    monkeypatch.setattr(hf_mod, "on_response_created", lambda: None)

    # A connection that dies mid-stream, which is what actually happens on a
    # flaky link. `_make_fake_realtime_client`'s connection object yields from an
    # iterator, so making `__anext__` raise reproduces it exactly -- and reuses
    # the real fake, so the rest of the connection surface stays correct.
    handler = _handler_with(())
    connection_type = type(handler.client.realtime.connect())

    async def dropped(_self):
        raise ConnectionError("connection dropped")

    monkeypatch.setattr(connection_type, "__anext__", dropped, raising=True)

    with pytest.raises(ConnectionError):
        await handler._run_realtime_session()
    assert calls == ["start", "stop"], "the finally must close the session on the error path too"


@pytest.mark.asyncio
async def test_handler_shutdown_is_still_safe_after_the_connection_closed(monkeypatch: Any) -> None:
    """Both call sites are idempotent; running cleanup twice must be harmless."""
    calls: list[str] = []

    async def record_start(_deps):
        return 13

    async def record_stop(_deps, _token):
        calls.append("stop")

    monkeypatch.setattr(hf_mod, "on_session_started", record_start)
    monkeypatch.setattr(hf_mod, "on_session_shutdown", record_stop)
    monkeypatch.setattr(hf_mod, "on_user_speech_started", lambda _deps: None)
    monkeypatch.setattr(hf_mod, "on_assistant_turn_ended", lambda _deps, _live=None: None)
    monkeypatch.setattr(hf_mod, "on_response_created", lambda: None)

    handler = _handler_with(())
    await handler._run_realtime_session()
    handler.connection = None
    await handler.shutdown()
    assert calls == ["stop", "stop"]


@pytest.mark.asyncio
async def test_hooks_are_no_ops_when_nothing_plays(monkeypatch: Any) -> None:
    """Every turn fires these; with no music they must cost nothing.

    Fix round, finding 4: this used to raise `AssertionError` from inside the
    patched `post`, which could not fail the test. The hooks are deliberately
    fire-and-forget and the only collector is `drain_pending_for_tests`, which
    gathers with `return_exceptions=True` -- so the raise was swallowed and the
    surviving assertion held whether or not an idle hook had called the daemon.
    Record the calls in the fake and assert on the record instead.
    """
    posted: list[str] = []

    async def record_post(self, url, json=None, **kwargs):
        posted.append(url)
        return _Ok()

    monkeypatch.setattr(httpx.AsyncClient, "post", record_post)
    deps = _deps()
    music_hooks.on_user_speech_started(deps)
    music_hooks.on_assistant_turn_ended(deps)
    await music_hooks.drain_pending_for_tests()
    assert posted == [], "an idle hook talked to the daemon"
    assert PLAYER.current() is None


def test_no_production_code_calls_player_reset():
    """Round 2, finding 8: `reset()` is a test affordance, not a lifecycle call."""
    from pathlib import Path

    src_root = Path(__file__).parents[1] / "src" / "reachy_companion"
    for path in src_root.rglob("*.py"):
        if "__pycache__" in str(path):
            continue
        source = path.read_text(encoding="utf-8")
        if path.name == "music_player.py":
            continue  # this is where it is defined
        assert "PLAYER.reset(" not in source, f"{path.name} resets instead of invalidating"


@pytest.mark.asyncio
async def test_a_small_sink_residue_does_not_park_the_resume():
    """A held-back partial sink chunk must not block the drain verdict.

    2026-08-24, on-robot: the play loop holds the final partial chunk back, so
    a ~0.05 s enqueue-vs-sink residue survived every real drain and the resume
    waiter looped forever ("still waiting ... 0.08s outstanding").
    """
    generation = audio_drain.begin_response()
    audio_drain.note_enqueued(generation, sample_count=12000, sample_rate=24000)  # 0.5 s
    audio_drain.note_chunk(sample_count=11000, sample_rate=24000)  # sink got 0.458 s
    audio_drain.note_queue_empty()
    audio_drain.close_response(generation)
    assert audio_drain.outstanding_s() > 0.0, "the held-back tail is the premise"
    assert await audio_drain.wait_drained(generation, timeout_s=2.0) is True


@pytest.mark.asyncio
async def test_a_real_pending_reply_still_blocks_the_resume():
    """Residue above one sink chunk is unplayed speech, not a measurement artifact."""
    generation = audio_drain.begin_response()
    audio_drain.note_enqueued(generation, sample_count=24000, sample_rate=24000)  # 1.0 s
    audio_drain.note_chunk(sample_count=9600, sample_rate=24000)  # only 0.4 s reached the sink
    audio_drain.note_queue_empty()
    audio_drain.close_response(generation)
    assert await audio_drain.wait_drained(generation, timeout_s=0.6) is False
