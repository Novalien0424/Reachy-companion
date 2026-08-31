import os
import re
import json
import time
import uuid
import base64
import random
import asyncio
import logging
from typing import TYPE_CHECKING, Any, Final, Tuple, Optional
from collections import deque
from collections.abc import Callable

import httpx
import numpy as np
from openai import AsyncOpenAI
from pydantic import Field, BaseModel
from numpy.typing import NDArray
from huggingface_hub import get_token
from typing_extensions import Literal, TypedDict
from openai.types.realtime import (
    AudioTranscriptionParam,
    RealtimeAudioConfigParam,
    RealtimeToolsConfigParam,
    RealtimeFunctionToolParam,
    RealtimeAudioConfigInputParam,
    RealtimeAudioConfigOutputParam,
    RealtimeSessionCreateRequestParam,
)
from websockets.exceptions import ConnectionClosedError
from openai.types.realtime.realtime_audio_input_turn_detection_param import ServerVad

from reachy_companion.tools import core_tools
from reachy_companion.config import (
    HF_LOCAL_CONNECTION_MODE,
    config,
    get_default_voice,
    set_custom_profile,
    get_available_voices,
    get_hf_direct_ws_url,
    parse_hf_realtime_url,
    get_hf_connection_selection,
)
from reachy_companion.hanova import audio_drain
from reachy_companion.people import PERSON_FACTS_DEFAULT, facts_for_person
from reachy_companion.prompts import (
    mode_rules_block,
    get_session_voice,
    get_session_instructions,
    get_session_greeting_prompt,
)
from reachy_companion.streaming import AdditionalOutputs, audio_to_int16
from reachy_companion.toolboxes import TOOLBOXES, TOOLBOX_CATEGORIES, session_tool_exclusions
from reachy_companion.record_mode import clear_record_log, record_room_transcript
from reachy_companion.sleep_summary import record_transcript, write_sleep_summaries
from reachy_companion.audio.envparse import env_int, env_bool, env_float
from reachy_companion.tools.core_tools import (
    ToolSpec,
    ToolDependencies,
    get_tool_specs,
)
from reachy_companion.audio.backchannel import is_backchannel, is_substantive
from reachy_companion.conversation_mode import (
    MODE_LABELS,
    MODE_VALUES,
    DEFAULT_MODE,
    ConversationMode,
    parse_mode,
)
from reachy_companion.hanova.music_hooks import (
    on_response_audio,
    on_session_started,
    on_response_created,
    on_session_shutdown,
    on_tool_call_started,
    on_tool_call_finished,
    on_user_speech_started,
    on_assistant_turn_ended,
    on_turn_without_response,
    on_user_speech_candidate,
)
from reachy_companion.conversation_handler import QueueItem, ConversationHandler
from reachy_companion.tools.background_tool_manager import (
    ToolCallRoutine,
    ToolNotification,
    BackgroundToolManager,
)


if TYPE_CHECKING:
    from openai.resources.realtime.realtime import AsyncRealtimeConnection


logger = logging.getLogger(__name__)

_RESPONSE_DONE_TIMEOUT: Final[float] = 30.0
_RESPONSE_REJECTION_RETRY_DELAY: Final[float] = 0.5

# How long the sleep path waits for the goodbye's response to finish being
# generated. Longer than a normal reply needs, short enough that a wedged
# response cannot leave the robot standing there indefinitely.
_GOODBYE_RESPONSE_WAIT_S: Final[float] = 10.0

# --- party mode (multi-person hardening, 2026-08-24) -------------------------
# docs/plans/party-mode-plan.md + docs/multi-person-investigation.md. In a
# group, most speech is not for the robot: party mode debounces barge-in and
# answers only turns that address it. Solo mode is byte-identical to before.
_PARTY_NAMES_DEFAULT = "reachy,richie,ritchie,瑞奇,里奇,小瑞,瑞曲"
# Stop-style commands always pass the gate: a robot you cannot silence because
# it decided you were not talking to it is worse than any false positive.
_PARTY_CONTROL_RE = re.compile(r"停|閉嘴|闭嘴|安靜|安静|睡覺|睡觉|別唱|别唱|stop|quiet|shut\s*up", re.IGNORECASE)


def _boot_conversation_mode() -> ConversationMode:
    """Return the mode a fresh handler starts in (operator amendment, 2026-08-31).

    `GROUP` by default, deliberately. The robot sits in a room with several
    people in it, and a robot that boots ready to answer every overheard
    sentence is the exact failure the party-mode wave was built to fix — so a
    fresh session answers only when addressed by name, and 一對一聊天模式 is
    one spoken sentence away.

    `REALTIME_PARTY_DEFAULT` (the 2026-08-24 knob) is deliberately no longer
    read, and an instance `.env` still carrying it keeps working: the only mode
    it could ever select was `GROUP`, which is now the default, so
    `REALTIME_PARTY_DEFAULT=1` lands in exactly the mode it asked for. The one
    case that changes is `REALTIME_PARTY_DEFAULT=0`, which now boots into
    `GROUP` rather than solo — that IS the operator amendment, and
    `REALTIME_DEFAULT_MODE=one_on_one` says it in the new vocabulary.

    Degrades with a warning rather than raising, like every other mode knob.
    """
    raw = (os.getenv("REALTIME_DEFAULT_MODE") or "").strip()
    if not raw:
        if os.getenv("REALTIME_PARTY_DEFAULT") is not None:
            # Deploy visibility: an operator reading their own `.env` would
            # otherwise believe the boot mode is still theirs to set with the old
            # knob. `REALTIME_PARTY_DEFAULT=0` is the case that stings — it used
            # to mean "boot solo" and now selects nothing at all.
            logger.warning(
                "REALTIME_PARTY_DEFAULT is no longer read; the boot mode is REALTIME_DEFAULT_MODE, "
                "now unset, so Reachy boots into %s. Set REALTIME_DEFAULT_MODE=%s for the old "
                "one-on-one behaviour.",
                DEFAULT_MODE.value,
                ConversationMode.ONE_ON_ONE.value,
            )
        return DEFAULT_MODE
    mode = parse_mode(raw)
    if mode is None:
        logger.warning("Ignoring invalid REALTIME_DEFAULT_MODE=%r; using %s.", raw, DEFAULT_MODE.value)
        return DEFAULT_MODE
    if mode is ConversationMode.RECORD:
        # Allowed, because an operator running a standing meeting recorder is a
        # real use, but worth saying out loud: a robot that boots into 紀錄模式
        # is silent until it hears its name, which looks exactly like a robot
        # that failed to start.
        logger.warning("REALTIME_DEFAULT_MODE=record: Reachy will boot silent until it is addressed by name.")
    return mode


def _party_confirm_s() -> float:
    """How long speech must persist while Reachy is audible to count as a barge."""
    return env_int("REALTIME_PARTY_BARGE_CONFIRM_MS", 400, lo=0) / 1000.0


def _party_followup_s() -> float:
    """How long after an accepted turn unaddressed speech still gets answered."""
    return float(env_int("REALTIME_PARTY_FOLLOWUP_S", 20, lo=0))


def _party_names() -> list[str]:
    raw = os.getenv("REALTIME_PARTY_ADDRESS_NAMES") or _PARTY_NAMES_DEFAULT
    return [name.strip().casefold() for name in raw.split(",") if name.strip()]


# --- solo pause-then-decide barge-in (Task 8) -------------------------------
# Solo mode used to hand barge-in to the server (`interrupt_response=true`), so
# any speech start killed the reply mid-word: a cough, a 「嗯」, a sentence meant
# for someone else in the room. The client now owns the decision. The reply is
# *paused* -- held in the handler, never flushed -- and then either confirmed
# (speech that outlasts the confirm window, or a substantive transcript) or
# rolled back and resumed as if nothing had happened.
def _solo_client_barge() -> bool:
    """Whether solo mode decides barge-in locally instead of at the server."""
    return env_bool("REALTIME_SOLO_CLIENT_BARGE", True)


def _solo_name_gate() -> bool:
    """Whether solo barge-in requires being addressed by name (2026-08-30 plan).

    The operator's ask: like a person telling a story, Reachy should stop for
    「瑞奇…」 or 「停」, and keep talking through speech aimed at someone else.
    `0` restores the substantive-transcript rule. Only meaningful when
    `REALTIME_SOLO_CLIENT_BARGE` is on — the legacy path never sees it.
    """
    return env_bool("REALTIME_SOLO_NAME_GATE", True)


def _gate_text_accepts(text: str) -> tuple[bool, str]:
    """Whether *text* addresses the robot: (accepted, reason).

    Control phrases beat everything (the party gate's first rule); then any
    address name (`REALTIME_PARTY_ADDRESS_NAMES` — the same list party mode and
    the transcription keyword bias use). Everything else is unaddressed.
    """
    folded = text.casefold()
    if _PARTY_CONTROL_RE.search(folded):
        return True, "control phrase"
    if any(name in folded for name in _party_names()):
        return True, "name"
    return False, "unaddressed"


_ONE_ON_ONE_ANSWER_GATES: Final[tuple[str, ...]] = ("name_only", "open")


def _one_on_one_answer_gate() -> str:
    """Which turns 一對一聊天模式 answers: `open` (default) or `name_only`.

    Its OWN variable, deliberately not `REALTIME_SOLO_NAME_GATE` (2026-08-31
    plan, Open question 1). That one keeps its 2026-08-30 meaning — the
    *interruption* gate, default on, "Reachy talks through speech aimed at
    someone else" — and the robot's instance `.env` ships it explicitly set,
    with the deploy ritual restoring `.env` from backup on every install. An
    overloaded variable would therefore have flipped one-on-one to name-only
    answering on every single deploy, silently, forever.

    `open` is the default because the whole point of one-on-one is that a single
    person does not have to say the robot's name to be answered. `name_only` is
    the field fallback if open answering turns out to pick up too much of the
    room; it makes this mode answer on the same rule 紀錄模式 uses.

    Degrades with a warning rather than raising, like every other mode knob.
    """
    raw = (os.getenv("REALTIME_ONE_ON_ONE_ANSWER_GATE") or "").strip().lower()
    if not raw:
        return "open"
    if raw not in _ONE_ON_ONE_ANSWER_GATES:
        logger.warning("Ignoring invalid REALTIME_ONE_ON_ONE_ANSWER_GATE=%r; using open.", raw)
        return "open"
    return raw


# The journal line each mode prints when it hears a turn it will not answer.
# GROUP's is unchanged from party mode: `feature_list.json` rows cite it.
_ANSWER_DENY_LOG: Final[dict[ConversationMode, str]] = {
    ConversationMode.ONE_ON_ONE: "one-on-one gate: no answer for a non-substantive turn",
    ConversationMode.GROUP: "party gate: denied ambient turn",
    ConversationMode.RECORD: "record gate: transcribed without answering",
}


def _vad_silence_duration_ms() -> int:
    """Silence the server VAD needs before it will report `speech_stopped`.

    Lives here rather than in `openai_realtime._turn_detection` (its other
    reader) because the barge-in confirm window is only meaningful relative to
    it, and this module may not import that one — the dependency runs the other
    way.
    """
    return env_int("REALTIME_VAD_SILENCE_DURATION_MS", _VAD_SILENCE_DURATION_DEFAULT_MS, lo=0)


def _barge_confirm_s() -> float:
    """How long speech must persist during a pause before it is a real barge.

    **Gate-off knob only.** With `REALTIME_SOLO_NAME_GATE` on — the default —
    this timer no longer commits anything; the confirm window is
    `REALTIME_BARGE_MAX_PAUSE_MS`, after which an unaddressed pause rolls back
    and the reply resumes. This value still governs the legacy
    `REALTIME_SOLO_NAME_GATE=0` path, which stays shipped.

    **The default must outlast `REALTIME_VAD_SILENCE_DURATION_MS`** (review
    round, finding 1). `_confirm_solo_barge` confirms iff `_barge_speech_open`
    is still True when it fires, and that flag can only go False on
    `speech_stopped` — which the server does not send until its whole silence
    window has elapsed. At the old 250 ms default the flag was therefore still
    True for *every* onset, including a 100 ms cough: every pause confirmed,
    and the rollback, backchannel and timer branches were unreachable. 1600 ms
    clears the 1000 ms window plus the cough itself with margin (bumped in step
    with the 2026-08-30 patience default, from 1400 over an 800 ms window).

    This costs no perceived stop latency: the pause already silences the robot
    at the onset, so the user hears an immediate stop either way. What the
    window buys is the chance for the transcript to arrive and decide the pause
    properly — which is now the common path, with this timer as the backstop
    for speech so long it needs no transcript.
    """
    return env_int("REALTIME_BARGE_CONFIRM_MS", 1600, lo=0) / 1000.0


def _barge_max_pause_s() -> float:
    """Longest a reply stays paused for speech that never addresses the robot.

    Gate mode only. A name can only arrive by transcript, so sustained speech
    proves nothing; but an unaddressed 30-second side conversation must not
    hold the reply hostage either. When this cap fires, the reply resumes —
    Reachy keeps talking while the room talks past it — and a name that lands
    later is still honored by the late-interrupt path (plan Task 4).
    """
    return env_int("REALTIME_BARGE_MAX_PAUSE_MS", 4000, lo=0) / 1000.0


def _barge_rollback_timeout_s() -> float:
    """How long a pause waits for a transcript before it rolls itself back."""
    return env_float("REALTIME_BARGE_ROLLBACK_TIMEOUT_S", 2.0, lo=0.0)


def _barge_cooldown_s() -> float:
    """How long after a confirmed barge a new one is suppressed (echo guard)."""
    return env_int("REALTIME_BARGE_COOLDOWN_MS", 800, lo=0) / 1000.0


# With `interrupt_response=false` the server rejects the auto `response.create`
# of a turn that commits while a response is still active (one active response
# per conversation), so a confirmed barge can leave the user's real turn with no
# reply at all. This is how long we wait before repairing that (Codex round 1,
# finding 11).
_BARGE_RESPONSE_WATCHDOG_S: Final[float] = 1.5
# Safety margin subtracted from every `conversation.item.truncate` position
# (Task 5). `audio_end_ms` above the item's real duration is a server error, so
# the accounting always rounds DOWN: this covers `audio_drain`'s own residue
# slack (0.25 s) plus the resampler priming (~32 ms). Undershooting deletes a
# fragment the user actually heard from the model's context; overshooting past
# the item's real duration is a server error that loses the whole truncate.
_TRUNCATE_SLACK_MS: Final[int] = 300
# The server-VAD default this project ships; shared with `_turn_detection`.
# The API's own default is 500 ms; 800 shipped from D-023 onward. 1000 is the
# operator's "don't rush me" request (2026-08-30) — a Mandarin mid-sentence
# pause of about a second must not commit the turn — and still sits under the
# ~1100 ms knee where the robot starts to feel sluggish instead of patient
# (research doc §1).
_VAD_SILENCE_DURATION_DEFAULT_MS: Final[int] = 1000
# Bound on the per-item turn-mode stamps. One entry lives from `speech_started`
# to that item's `transcription.completed`/`.failed`, so the map is normally
# one or two deep; the cap only matters if transcripts stop arriving at all.
_TURN_MODE_MAX_ITEMS: Final[int] = 16
_BARGE_CONFIRM_WARNED = False


def warn_if_barge_confirm_races_vad() -> None:
    """Warn once when the confirm window cannot outlast the VAD silence window.

    Review round, finding 1. With `REALTIME_BARGE_CONFIRM_MS` at or below
    `REALTIME_VAD_SILENCE_DURATION_MS`, `speech_stopped` cannot possibly have
    arrived by the time the confirm timer fires, so every pause confirms and
    the rollback path is dead. That is a silent misconfiguration — the robot
    simply goes back to being interruptible by a cough — so it gets a warning
    at session-config build rather than nothing at all.

    Two configurations have no such branch to warn about, and warning anyway
    was noise: with the name gate on the confirm timer no longer commits at all
    (it is a max pause that rolls back), and under `REALTIME_VAD_TYPE=
    semantic_vad` the server ignores `REALTIME_VAD_SILENCE_DURATION_MS`
    entirely, so the comparison was against a value with no effect (recorded
    known edge in `progress.md`).
    """
    global _BARGE_CONFIRM_WARNED
    if _BARGE_CONFIRM_WARNED or not _solo_client_barge():
        return
    if _solo_name_gate():
        return
    if os.getenv("REALTIME_VAD_TYPE", "server_vad").strip().lower() == "semantic_vad":
        return
    confirm_ms = _barge_confirm_s() * 1000.0
    silence_ms = _vad_silence_duration_ms()
    if confirm_ms > silence_ms:
        return
    _BARGE_CONFIRM_WARNED = True
    logger.warning(
        "REALTIME_BARGE_CONFIRM_MS=%.0f is not longer than REALTIME_VAD_SILENCE_DURATION_MS=%d: "
        "speech_stopped cannot arrive before the confirm timer fires, so every barge-in will be "
        "confirmed and the false-interruption rollback can never run.",
        confirm_ms,
        silence_ms,
    )


def _current_task() -> "asyncio.Task[Any] | None":
    """Return the running task, or None when called from outside the event loop.

    The JSON-RPC control surface reaches `on_external_interrupt()` from its own
    thread, where `asyncio.current_task()` raises rather than returning None.
    """
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


def _cancel_barge_task(task: "asyncio.Task[None] | None", current: "asyncio.Task[Any] | None") -> None:
    """Cancel *task*, marshalling onto its own loop when called from another thread.

    Review round, finding 3. `on_external_interrupt()` is reached from the
    JSON-RPC thread (`console.clear_audio_queue`), and `Task.cancel()` is not
    thread-safe: off-loop it is delayed until the loop next touches the task,
    and raises under asyncio debug mode. `console.close()` already marshals its
    task cancellations the same way. A task carries its own loop, so no extra
    bookkeeping is needed to find the right one.

    Never cancels the caller's own task: a task that cancels itself never
    reaches its own release.
    """
    if task is None or task is current or task.done():
        return
    loop = task.get_loop()
    try:
        on_loop = asyncio.get_running_loop() is loop
    except RuntimeError:
        on_loop = False
    if on_loop:
        task.cancel()
        return
    try:
        loop.call_soon_threadsafe(task.cancel)
    except RuntimeError:
        # The loop is gone; the task can never run again anyway.
        logger.debug("barge timer's loop is closed; skipping the cancel")


# --- party gate: face-engagement signal (Task 7) ----------------------------
def _party_face_gate_enabled() -> bool:
    """Whether a centered, engaged face alone may pass the address gate."""
    return env_bool("REALTIME_PARTY_FACE_GATE", True)


def _party_face_fresh_s() -> float:
    """How old a cached face reading may be and still count as "right now"."""
    return env_float("REALTIME_PARTY_FACE_FRESH_S", 3.0, lo=0.0)


def _party_face_center() -> float:
    """Max |x| (normalized to [-1, 1]) for a face to count as facing the robot."""
    return env_float("REALTIME_PARTY_FACE_CENTER", 0.4, lo=0.0, hi=1.0)


# Face memory at wake time (D-013). One monotonic deadline bounds the whole
# check — model readiness, frame capture and identification together — because
# this hook runs before the session starts processing events: every millisecond
# it spends is a millisecond the greeting is late.
_FACE_WAKE_BUDGET_MS_DEFAULT: Final[int] = 4000
# Several looks fit inside that budget, and at a dozen enrolled people extra
# frames buy more recognitions than any model change does (D-015): a blink, a
# turned head or a shadow is a per-frame accident, not a per-person one.
_FACE_WAKE_ATTEMPTS_DEFAULT: Final[int] = 5
_FACE_WAKE_RETRY_PAUSE_S: Final[float] = 0.15
_FACE_GREETING_PREFIX: Final[str] = (
    "（系统提示：摄像头认出面前的人是「{name}」。自然地叫出他的名字打招呼，不要提到摄像头或识别。）"
)
# How many remembered facts a personalized greeting may lean on is
# `people.PERSON_FACTS_DEFAULT`, kept with the store that owns the facts: the
# greeting and `who_is_this` read one knob and must not drift on its default.
_FACE_KNOWN_WITH_FACTS_PREFIX: Final[str] = (
    "（系统提示：摄像头认出面前的人是「{name}」。你记得关于他的这些事：{facts}。"
    "像老朋友一样自然地叫他的名字打招呼，可以自然带到一两件你记得的事，"
    "不要自我介绍，也不要提到摄像头或识别。）"
)
_FACE_STRANGER_GREETING_PREFIX: Final[str] = (
    "（系统提示：摄像头看到面前有人，但认不出是谁。向这位新朋友自然地问候并简单介绍你自己，"
    "可以礼貌地问对方怎么称呼。不要提到摄像头或识别。）"
)
# The same hook, given a realistic window: the pre-greeting check is over
# before anyone is posed in frame (14/14 on-robot boots), so the look continues
# for a bounded few seconds *after* the greeting has been queued.
_FACE_WAKE_EXTENDED_MS_DEFAULT: Final[int] = 8000
_FACE_WAKE_EXTENDED_PAUSE_S: Final[float] = 0.7
_FACE_LATE_RECOGNITION_PROMPT: Final[str] = (
    "（系统提示：摄像头刚认出面前的人是「{name}」。自然地用名字招呼他，"
    "或在你接下来说的话里称呼他的名字。不要提到摄像头或识别这件事。）"
)
_FACE_LATE_KNOWN_WITH_FACTS_PROMPT: Final[str] = (
    "（系统提示：摄像头刚认出面前的人是「{name}」。你记得关于他的这些事：{facts}。"
    "自然地用名字招呼他，可以自然带到你记得的事。不要提到摄像头或识别这件事。）"
)
# The person store is a small local JSON file, so this read is normally
# sub-millisecond — but it takes a lock a tool write may hold, and it sits on
# the path of a greeting the user is already waiting for. Bounded so a stalled
# store costs the recall and nothing else; both callers fall back to the plain
# named prompt when it expires.
_FACE_FACTS_READ_TIMEOUT_S: Final[float] = 1.0


def _startup_greeting_prefix(identification: Any, facts: list[str]) -> str:
    """Map the wake-check outcome onto one of the three greeting prefixes.

    Three ways a boot can start: a friend the camera placed (by name, and with
    what is remembered about them when there is anything), somebody who is there
    but unplaceable (met as a new friend — never guessed at), and an empty frame
    (the greeting exactly as the profile wrote it). `identification is None` is
    the "the check never ran or failed" case and takes the empty-frame branch,
    so face memory can still never cost anyone their greeting.
    """
    if identification is None:
        return ""
    if identification.status == "recognized" and identification.name:
        if facts:
            return _FACE_KNOWN_WITH_FACTS_PREFIX.format(name=identification.name, facts="；".join(facts)) + "\n"
        return _FACE_GREETING_PREFIX.format(name=identification.name) + "\n"
    if identification.face_count > 0 or identification.status in (
        "unknown",
        "ambiguous",
        "too_far",
        "multiple_faces",
    ):
        return _FACE_STRANGER_GREETING_PREFIX + "\n"
    return ""


# Boot gate (Task 6). The first session of a handler comes up with turn
# detection OFF: the greeting is about to play out of a speaker sitting next to
# the microphone, and until it has finished, anything the server commits is the
# robot hearing itself. `response.done` is NOT that moment -- queued PCM outlives
# it (see `audio_drain`) -- so the release waits for the audio to stop being
# audible, bounded by this cap, with the timeout below as the hard backstop.
_BOOT_GATE_DRAIN_POLL_S: Final[float] = 0.1
_BOOT_GATE_DRAIN_CAP_S: Final[float] = 3.0
_BOOT_GATE_TIMEOUT_S_DEFAULT: Final[float] = 8.0
_BOOT_GATE_DRAIN_TASK: Final[str] = "boot-gate-drain"


class InputTranscriptChunksByItem(BaseModel):
    """Current item_id and its accumulated deltas. Only one item at a time."""

    item_id: str | None = None
    deltas: list[str] = Field(default_factory=list)


def to_realtime_tools_config(tool_specs: list[ToolSpec]) -> RealtimeToolsConfigParam:
    """Convert app tool specs to the OpenAI-compatible realtime session shape."""
    realtime_tools: RealtimeToolsConfigParam = []
    for spec in tool_specs:
        realtime_tools.append(
            RealtimeFunctionToolParam(
                type="function",
                name=spec["name"],
                description=spec["description"],
                parameters=spec["parameters"],
            )
        )
    return realtime_tools


class HFNativeRateAudioPCM(TypedDict):
    """Hugging Face extension for native-rate PCM audio."""

    type: Literal["audio/pcm"]
    rate: None


def _native_rate_audio_pcm() -> HFNativeRateAudioPCM:
    """Return the Hugging Face native-rate PCM config."""
    return {"type": "audio/pcm", "rate": None}


def _build_openai_compatible_client_from_realtime_url(
    realtime_url: str,
    bearer_token: str | None,
) -> tuple[AsyncOpenAI, dict[str, str]]:
    """Build an OpenAI-compatible realtime client from a direct websocket/base URL."""
    parsed = parse_hf_realtime_url(realtime_url)
    client = AsyncOpenAI(
        api_key=bearer_token or "DUMMY",
        base_url=parsed.base_url,
        websocket_base_url=parsed.websocket_base_url,
    )
    return client, parsed.connect_query


class HuggingFaceRealtimeHandler(ConversationHandler):
    """Realtime stream handler for the Hugging Face OpenAI-compatible endpoint."""

    SAMPLE_RATE = 16000

    def __init__(
        self,
        deps: ToolDependencies,
        instance_path: Optional[str] = None,
        startup_voice: Optional[str] = None,
    ):
        """Initialize the handler."""
        super().__init__()

        self.deps = deps

        self.client: AsyncOpenAI
        self.connection: "AsyncRealtimeConnection | None" = None
        self.output_queue: "asyncio.Queue[Tuple[int, NDArray[np.int16]] | AdditionalOutputs]" = asyncio.Queue()

        self.instance_path = instance_path
        self._voice_override: str | None = self._normalize_startup_voice(startup_voice)
        self._realtime_connect_query: dict[str, str] = {}

        # Debouncing for partial transcripts
        self.partial_transcript_task: asyncio.Task[None] | None = None
        self.partial_debounce_delay = 0.5  # seconds
        self.input_transcript_chunks_by_item = InputTranscriptChunksByItem()

        # Internal lifecycle flags
        self._connected_event: asyncio.Event = asyncio.Event()

        # Background tool manager
        self.tool_manager = BackgroundToolManager()

        # Response-in-progress guard: the Realtime API only allows one active
        # response per conversation at a time.  A dedicated worker task
        # (_response_sender_loop) dequeues and sends one request at a time
        self._pending_responses: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._response_done_event: asyncio.Event = asyncio.Event()
        self._response_done_event.set()
        # The loop this handler's session runs on, captured in
        # `_run_realtime_session` and released in its `finally`. `asyncio.Event`
        # is loop-bound, so a caller arriving from another thread's loop — the
        # inactivity path's `asyncio.run` most of all — has to marshal onto this
        # one rather than await the event directly (Codex round 2, 2a-5).
        self._handler_loop: asyncio.AbstractEventLoop | None = None
        self._response_started_or_rejected_event: asyncio.Event = asyncio.Event()
        self._last_response_rejected: bool = False
        self._turn_user_done_at: float | None = None
        self._turn_response_created_at: float | None = None
        self._turn_first_audio_at: float | None = None
        self._startup_greeting_sent = False
        # --- boot gate (Task 6) ---------------------------------------------
        # Active for this handler's FIRST session only: no turn can be committed
        # until the greeting has finished coming out of the speaker. Released by
        # the drain waiter after the greeting's `response.done`, by the backstop
        # timer, or immediately when no greeting is configured.
        self._boot_gate_active: bool = env_bool("REALTIME_BOOT_GATE", True)
        self._boot_gate_task: asyncio.Task[None] | None = None
        self._in_flight_tool_calls: set[str] = set()
        self._tool_batch_needs_response = False
        # D-018 / round 3 finding 2: the token of the realtime session this
        # handler currently owns. 0 means "no session open". It is what stops a
        # late cleanup from a replaced connection tearing down its successor.
        self._hanova_session: int = 0
        # --- conversation modes (2026-08-31 plan) ----------------------------
        # The single source of truth. Set once, here: the mode deliberately
        # SURVIVES a reconnect (survey §1.2), because a dropped websocket
        # mid-meeting must not silently end 紀錄模式. Only turn state resets per
        # session.
        self._conversation_mode: ConversationMode = _boot_conversation_mode()
        # The mode the utterance currently in flight BEGAN in, stamped at
        # `speech_started` (Task 2). A flip must not retroactively reclassify a
        # turn that is already half-spoken: ambient speech started in 多人聊天
        # 模式 must not become answerable because someone flipped to 一對一
        # mid-sentence, and vice versa (Codex round 1, P1-2).
        self._turn_mode: ConversationMode = self._conversation_mode
        # Per-input-item stamps, because a single field is overwritten by the
        # next `speech_started` before a slow `transcription.completed` for the
        # PREVIOUS turn arrives (Codex round 2, 2a-4). Keyed by the
        # `input_audio_buffer.speech_started` event's `item_id`; popped when
        # that item's transcript completes or fails; cleared per session. The
        # bound is small because entries only survive until their own transcript
        # lands, and a dropped stamp falls back to `_turn_mode`.
        self._turn_modes: dict[str, ConversationMode] = {}
        # Monotonic coalescing token for session updates (Task 3, decision 9):
        # a snapshot queued behind a newer flip is dropped rather than sent.
        self._mode_update_seq: int = 0
        # The ordered, acknowledged, single-flight session-update mechanism
        # (Task 3, decision 9). One lock spans ticket check, payload build,
        # waiter install, send and acknowledgement wait, so no two updates can
        # be on the wire at once — which is what makes an uncorrelated
        # `session.updated` safe to match positionally.
        self._session_update_lock: asyncio.Lock = asyncio.Lock()
        self._session_update_event_id: str | None = None
        self._session_update_waiter: asyncio.Future[bool] | None = None
        # Acknowledgements the server still owes us that nobody is waiting on:
        # the connect-time session config (sent before the receive loop exists),
        # its fallback retry, any pre-receive-loop push, and any update whose
        # ack wait timed out. Each one still produces a `session.updated`
        # eventually, and each must be consumed before a live waiter can be
        # (Codex round 3, findings 5 and 6).
        self._session_update_ack_debt: int = 0
        # Whether the receive loop is running and can therefore observe an
        # acknowledgement at all (Codex round 3, finding 1).
        self._receive_loop_active: bool = False
        # --- dynamic toolboxes (2026-08-31 tool diet) ------------------------
        # Which on-demand tool families are currently in `session.tools`. Opened
        # by the `open_toolbox` router, closed on a mode switch, at session
        # start and at shutdown (the path `go_to_sleep` takes). No idle timer:
        # a box that closes mid-sentence is a new failure mode for exactly the
        # model tier this diet exists to stop confusing.
        self._open_toolboxes: set[str] = set()
        # --- party mode (multi-person hardening, 2026-08-24) -----------------
        # monotonic() of the last gate-ACCEPTED user turn; the only thing that
        # opens the follow-up window (Codex round 1, finding 3 — greeting and
        # tool-follow-up responses must not).
        self._party_last_accept_at: float | None = None
        self._party_speech_open: bool = False
        # Bumped per speech_started and per mode flip so a sleeping barge timer
        # can tell it belongs to a superseded utterance (finding 8).
        self._party_utterance_seq: int = 0
        self._party_barge_task: asyncio.Task[None] | None = None
        self._active_response_id: str | None = None
        # --- extended wake face window (Task 5) ------------------------------
        # `_user_has_spoken` closes that window for good: after the first
        # syllable, an injected identity item would be steering an answer the
        # user is already waiting on.
        self._user_has_spoken = False
        self._wake_face_task: asyncio.Task[None] | None = None
        # Person-scoped memory label (spec §3.3): set on recognition, cleared
        # per session. `deps` outlives the handler, so a rebuild starts with no
        # inherited identity — whoever is in the room is established again.
        self.deps.current_person = None
        # Late audio deltas from a cancelled response must not reach the
        # speaker (finding 8). Tiny bound: only very recent ids can race.
        self._cancelled_response_ids: deque[str] = deque(maxlen=8)
        # --- solo pause-then-decide barge-in (Task 8) ------------------------
        # `_barge_paused` and `_barge_pending` move together: paused means emit()
        # is withholding audio, pending means a decision is still owed. Solo has
        # no speech-open flag of its own (`_party_speech_open` is set only in the
        # party branch), hence `_barge_speech_open`. Three separate task refs:
        # one field cannot represent three lifecycles (Codex round 1, finding 8).
        self._barge_paused: bool = False
        self._barge_pending: bool = False
        self._barge_speech_open: bool = False
        self._barge_confirm_task: asyncio.Task[None] | None = None
        self._barge_rollback_task: asyncio.Task[None] | None = None
        self._barge_watchdog_task: asyncio.Task[None] | None = None
        self._barge_cooldown_until: float = 0.0
        self._barge_response_seen: bool = False
        # The response that was speaking when the pause began, so a commit can
        # tell it apart from the answer to the turn being barged in with.
        self._barge_paused_response_id: str | None = None
        # The item whose *partial* transcript already committed a pause, so the
        # `completed` transcript for that same item cannot interrupt a second
        # time — the answer it would kill is the one the commit asked for
        # (Codex round 2, finding 2). Consumed by the completed handler.
        self._barge_partial_committed_item: str | None = None
        # The reply a rollback put back on the speaker, so a late interrupt can
        # tell it apart from a *newer* response that is already the answer to
        # the turn now being decided (2026-08-30 plan, Task 4). Scoped to the
        # utterance that caused the rollback: cleared once that turn is decided,
        # never on `response.created` (Codex round 2, finding 1).
        self._barge_resumed_response_id: str | None = None
        # Was Reachy audible when the current utterance BEGAN? Only then can a
        # late interrupt be talking over anything (Task 4 fix round, finding 1).
        # A turn started from silence is answered by the response that is live
        # by the time its transcript lands, so cancelling that response would
        # cut the answer to the very turn being decided.
        self._barge_late_eligible: bool = False
        # The reply's audio, withheld while the decision is pending.
        self._held_audio: deque[QueueItem] = deque()
        # --- heard-audio accounting for `conversation.item.truncate` (Task 5) -
        # We are on WebSocket, where the server never trims an interrupted
        # reply: without a truncate the model believes it said every word it
        # generated, including the ones the barge cut off. `_audio_item_id` is
        # the assistant item currently coming out of the speaker and
        # `_audio_item_enqueued_ms` is how much of it has been handed to the
        # playback path; the `_barge_paused_*` pair is that same measurement
        # frozen at pause time, because by commit time `_audio_item_id` may
        # already belong to a newer response (Codex round 2, finding 4).
        self._audio_item_id: str | None = None
        self._audio_item_enqueued_ms: float = 0.0
        self._barge_paused_item_id: str | None = None
        self._barge_paused_heard_ms: int = 0
        # --- sleep-time engagement memory (D-027) ----------------------------
        # One summary per visit: `shutdown()` can legitimately run twice (its own
        # call site plus the session `finally`), and the second must be a no-op.
        self._sleep_summary_done: bool = False

    @property
    def _party_mode(self) -> bool:
        """Whether the ROOM turn policy applies — GROUP and RECORD both do.

        Compat shim, and a deliberate one. A dozen sites branch on this
        (`:1073, :1099, :1118, :1264, :2402, :2422, :2597, :2619, :2629, :2653,
        :2662, :825` and `openai_realtime.py:416`) and every one of them asks
        the same binary question: debounced room barge-in and a gate at
        `transcription.completed`, or the solo pause-then-decide machine?
        RECORD wants the room answer at all of them. Sites whose behavior really
        differs per mode read `_conversation_mode` instead.

        The `getattr` default mirrors the existing defensive read at
        `openai_realtime.py:416`: config emission must also work on
        partially-built handlers (tests construct via `__new__`). It is
        deliberately `ONE_ON_ONE` and **not** `DEFAULT_MODE` — the contract it
        preserves is `getattr(self, "_party_mode", False)`, i.e. a handler with
        no mode state at all emits the solo config, exactly as it did before
        this wave. A real handler always has `_conversation_mode` set.
        """
        return self._current_mode() is not ConversationMode.ONE_ON_ONE

    def _current_mode(self) -> ConversationMode:
        """Return the live conversation mode, readable on a partially-built handler.

        The one place the `__new__`-safe default lives, so `_party_mode` and the
        prompt/tool emission that Task 3 added cannot drift apart about what a
        handler with no mode state at all is.
        """
        return getattr(self, "_conversation_mode", ConversationMode.ONE_ON_ONE)

    # --- conversation modes -------------------------------------------------
    async def set_conversation_mode(self, mode: str | ConversationMode) -> dict[str, Any]:
        """Switch conversation mode and push the new policy to the live session.

        Successor to `set_party_mode` (2026-08-24 → 2026-08-31). Injected into
        `ToolDependencies` (same seam as `go_to_sleep`) so the
        `set_conversation_mode` tool can switch mid-conversation.

        **Async, unlike its predecessor** (Codex round 1, P1-1). `set_party_mode`
        scheduled its session update with `ensure_future` and returned; the model
        then spoke its confirmation against whatever the server still had. That
        was survivable when the update carried only turn detection. It is not
        survivable now that it carries the mode's instructions and its whole tool
        list — the confirmation sentence, and any tool call the model makes right
        after it, would run against the previous mode. So the update is awaited
        before the tool result goes back.
        """
        target = mode if isinstance(mode, ConversationMode) else parse_mode(mode)
        if target is None:
            logger.warning("set_conversation_mode: unknown mode %r", mode)
            return {"ok": False, "error": f"unknown conversation mode: {mode}", "modes": list(MODE_VALUES)}
        previous = self._conversation_mode
        if target is previous:
            return {"ok": True, "status": "unchanged", "mode": target.value, "label": MODE_LABELS[target]}
        # Read BEFORE the flags below are cleared (Codex round 2, 2a-3): the
        # guard at the bottom asks "was somebody mid-utterance when the mode
        # changed?", and this method is about to clear both flags itself, so
        # asking afterwards always answered no.
        turn_in_flight = self._party_speech_open or self._barge_speech_open
        self._conversation_mode = target
        # A new mode is a new posture: its instructions describe a different
        # tool surface, so whatever was loaded for the old one goes. Before the
        # push below, so the update this flip sends already carries the smaller
        # surface rather than needing a second one to take the boxes back out.
        self.close_toolboxes(f"mode -> {target.value}")
        self._party_speech_open = False
        # The solo speech flag is maintained by the solo branch of
        # `speech_stopped`, which stops running the moment the mode changes.
        # Left stale True it would keep the response watchdog standing down for
        # the rest of the session (Task 8 fix round, finding 3).
        self._barge_speech_open = False
        # Same hazard class, same cure: late eligibility is written only by
        # `_solo_speech_started`, which the room branch never runs.
        self._barge_late_eligible = False
        self._party_utterance_seq += 1  # any sleeping barge timer is now stale
        if self._barge_paused or self._barge_pending:
            # The solo pause has just lost every timer that could resolve it, so
            # it must be resolved here or the reply stays held forever. Rolling
            # back is the honest reading: nothing confirmed this as a barge.
            self._resume_playback(rolled_back=True)
        # `_resume_playback(rolled_back=True)` records a resumed response id and
        # nothing on the flip path ever clears it (the completed-transcript
        # branch that normally does belongs to the loop this flip just left).
        self._barge_resumed_response_id = None
        # Whoever just switched to the room mode is clearly engaged: entering
        # GROUP opens the follow-up window so the conversation that asked for it
        # can continue without re-addressing by name. RECORD deliberately does
        # NOT — quiet-scribe posture: every command needs the name.
        self._party_last_accept_at = time.monotonic() if target is ConversationMode.GROUP else None
        # A flip with no utterance in flight re-stamps the fallback turn mode
        # too, so the next `speech_started` is not the only thing that can
        # correct it. With one in flight the stamp is left alone: that turn is
        # decided under the mode it began in.
        if not turn_in_flight:
            self._turn_mode = target
        # Last of the local flip, deliberately: everything above is in-memory
        # flag work that cannot fail, so a raise here cannot leave a
        # half-flipped handler, and nothing above can leave the log cleared
        # under a mode that never finished changing.
        #
        # What this guarantees is narrower than "紀錄模式 always opens empty":
        # it fires on a flip that goes through this method — the voice command
        # and the `set_conversation_mode` tool. A settings or backend restart
        # never reaches here (the new handler simply boots at the default mode,
        # `_boot_conversation_mode`), so a recording abandoned that way is left
        # standing on purpose (P1-5) and survives until the sleep that ends the
        # visit clears it in `shutdown()`. A second 紀錄模式 after such a
        # restart therefore continues the abandoned log rather than opening a
        # blank one — the deliberate cost of not throwing away a meeting that
        # was still happening.
        if previous is ConversationMode.RECORD:
            clear_record_log(self.deps)
        logger.info("conversation mode: %s -> %s", previous.value, target.value)
        if self.connection is not None:
            if not await self._push_mode_update():
                # The local mode still stands: the answer gate, the barge policy
                # and the record log are all enforced client-side. What is lost
                # is the model's own knowledge of the mode and its tool surface,
                # which the next reconnect restores.
                logger.warning("conversation mode %s applied locally only", target.value)
        # Re-read AFTER the await (Codex round 3, finding 4). A second
        # `set_conversation_mode` can land while this one is waiting for its
        # acknowledgement, and the model speaks this result out loud: reporting
        # a mode the handler is no longer in would have Reachy announce 紀錄模式
        # while it is actually in 多人聊天模式.
        current = self._conversation_mode
        if current is not target:
            logger.info(
                "conversation mode %s was superseded by %s before this call returned",
                target.value,
                current.value,
            )
            return {
                "ok": True,
                "status": "superseded",
                "mode": current.value,
                "label": MODE_LABELS[current],
                "requested": target.value,
            }
        return {"ok": True, "status": "mode_set", "mode": target.value, "label": MODE_LABELS[target]}

    # --- dynamic toolboxes --------------------------------------------------
    async def open_toolbox(self, category: str) -> dict[str, Any]:
        """Load one on-demand tool family into the live session.

        The model reads this tool's result and continues to the real call in the
        same turn, so the update is not merely sent but ACKNOWLEDGED before the
        result comes back — otherwise the tool it reaches for still does not
        exist on the server (design decision 9).

        Optimistic then rolled back (Codex round 1, P2-9): the box goes into
        `_open_toolboxes` first, because `_push_mode_update` builds its payload
        from that live set — and comes straight back out if the server refused,
        because a box marked open that the session never got would have the
        model calling tools that are not there for the rest of the visit.

        The membership re-check after the await covers the second failure case
        (Codex round 3, finding 3): a `set_conversation_mode` landing while the
        update was in flight calls `close_toolboxes`, so the box is gone even
        though the push itself succeeded. Reporting "loaded" there would
        advertise tools the session no longer has.
        """
        if category not in TOOLBOXES:
            logger.warning("open_toolbox: unknown category %r", category)
            return {
                "ok": False,
                "error": f"unknown toolbox category: {category}",
                "categories": list(TOOLBOX_CATEGORIES),
            }
        tools = list(TOOLBOXES[category])
        if category in self._open_toolboxes:
            return {"ok": True, "status": "already_open", "category": category, "tools": tools}
        self._open_toolboxes.add(category)
        if not await self._push_mode_update() or category not in self._open_toolboxes:
            self._open_toolboxes.discard(category)
            logger.warning("toolbox %s was not applied by the server; rolled back", category)
            return {
                "ok": False,
                "status": "update_failed",
                "error": f"the {category} tools could not be loaded right now",
                "category": category,
                "categories": list(TOOLBOX_CATEGORIES),
            }
        logger.info("toolbox opened: %s (%s)", category, ", ".join(tools))
        return {"ok": True, "status": "loaded", "category": category, "tools": tools}

    def close_toolboxes(self, reason: str) -> None:
        """Drop every open toolbox. Caller owns pushing the smaller surface."""
        if not self._open_toolboxes:
            return
        logger.info("toolboxes closed (%s): %s", reason, ", ".join(sorted(self._open_toolboxes)))
        self._open_toolboxes.clear()

    def _party_reset_for_new_session(self) -> None:
        """Clear party-mode turn state at the start of every (re)connect.

        A follow-up window, an open-speech flag, or a barge timer's utterance
        id from a previous session must never leak into a new one (the
        research doc's SAS carry-over hazard): someone who was inside the
        follow-up window when a reconnect happened must not be silently
        treated as still addressing the robot in the session that replaces it.
        Called once near the top of `_run_realtime_session`, for both the
        first session and every reconnect/restart after it.

        The conversation MODE deliberately survives (survey §1.2) — a dropped
        websocket mid-meeting must not silently end 紀錄模式 — but the per-turn
        stamps of that mode do not: `item_id`s are scoped to a session, so an
        entry left behind can be hit by an unrelated item of the same name in
        the session that replaces it, which is this method's hazard exactly.
        """
        self._party_last_accept_at = None
        self._party_speech_open = False
        self._party_utterance_seq += 1  # any sleeping barge timer is now stale
        # Per-session, as promised where they are declared: the stamps describe
        # turns of the session that just ended, and the fallback stamp is
        # re-anchored to the mode the new session actually opens in rather than
        # left pointing at whatever the last turn happened to begin in.
        self._turn_modes.clear()
        self._turn_mode = self._conversation_mode

    async def _push_turn_detection_update(self) -> None:
        """Send the mode's turn-detection to the live session. Base: no-op.

        The Hugging Face backend has no session.update semantics we control;
        the OpenAI subclass overrides this with a narrow update (Codex round 1,
        finding 2).
        """
        return None

    def _mode_instructions(self) -> str:
        """Session instructions plus the current mode's rules block.

        One resolver for both the session-config build and the live mode update,
        so a flip and a reconnect can never disagree about what the model was
        told.
        """
        return f"{get_session_instructions(self.instance_path)}\n\n{mode_rules_block(self._current_mode())}"

    def _emit_transcript(self, role: str, text: str, final: bool = True) -> None:
        """Forward the transcript, and in 紀錄模式 keep a copy of every final line.

        This one override covers all four final-transcript sites — the rolled-back
        solo barge (`:1800`), the answer-gate denial (`:3152`), the answered user
        turn (`:3191`) and the assistant's own transcript (`:3236`) — plus any
        added later. Debounced partials never come through here at all (they go
        straight to the output queue, `_emit_debounced_partial` `:2073`), and the
        `final` guard keeps it that way for any future caller, so the log holds
        finished lines only.

        The broadcast goes FIRST. Recording is the added duty here and the
        console/JSON-RPC transcript is the pre-existing one: were the order
        reversed, a raise while recording would silently cost the operator the
        line they were watching for. The base already swallows a misbehaving
        observer, so nothing below can be starved by one.
        """
        super()._emit_transcript(role, text, final)
        if final and text and self._current_mode() is ConversationMode.RECORD:
            record_room_transcript(self.deps, role, text)

    def _mode_tool_exclusions(self) -> list[str]:
        """Tool names hidden from the session right now: mode plus open boxes."""
        return session_tool_exclusions(self._current_mode(), self._open_toolboxes)

    async def _apply_session_update(
        self,
        build_session: Callable[[], RealtimeSessionCreateRequestParam | None],
        *,
        what: str,
        wait_for_ack: bool = True,
    ) -> bool:
        """Send one session update, reporting whether it left the client.

        The base sends and reports the send, nothing more. It installs no
        waiter, so the `session.updated` the receive loop hands to
        `_note_session_updated` here only ever pays down debt or falls through
        as a no-op — there is never anything for it to resolve. That is enough
        for this backend: the three live-session updates that predate the modes
        work — `change_voice`, `apply_personality` and (on the subclass) turn
        detection — must keep reaching the server exactly as they did before,
        and the mode surface an acknowledgement would protect is the subclass's.

        `OpenAIRealtimeHandler` overrides it with the real ordered,
        acknowledged, single-flight mechanism (design decision 9). A builder
        returning None means "superseded, send nothing", which is success:
        the newer update is the one that should land. `wait_for_ack` is honored
        only there; the base never waits.
        """
        if not self.connection:
            return False
        session = build_session()
        if session is None:
            return True
        try:
            await self.connection.session.update(session=session)
        except Exception as exc:  # noqa: BLE001 - a failed update must not kill the caller
            logger.warning("Failed to send the %s session update: %s", what, exc)
            return False
        logger.info("session updated (%s)", what)
        return True

    async def _push_mode_update(self) -> bool:
        """Apply the current mode to the live session. Base: no-op returning True.

        The mode's instructions and tool surface are an OpenAI-backend concern
        (D-002 locks the backend); the subclass overrides this. Reporting True
        keeps `set_conversation_mode` from warning about a push that was never
        going to happen here.
        """
        return True

    def _note_session_updated(self) -> None:
        """Handle one `session.updated`, paying older debts before the waiter.

        `session.updated` does not echo the client `event_id`, so it can only be
        matched positionally — and positional matching is wrong unless every
        acknowledgement the server still owes us is accounted for first.
        Precedence, and both arms are load-bearing (Codex round 3, findings 5
        and 6):

        1. **Unmatched acks first.** `_session_update_ack_debt` counts updates
           that were sent with nobody waiting on them: the session-config update
           at connect (sent before the receive loop exists, so its
           acknowledgement necessarily arrives later), its legacy-transcription
           retry, any pre-receive-loop push, and any update whose ack wait timed
           out. Every one of those still produces exactly one `session.updated`
           at some point. Letting one of them resolve a LIVE waiter would tell a
           mode flip its payload had been applied when what the server actually
           acknowledged was the connect config — the exact false-positive the
           whole acknowledged-update design exists to prevent.
        2. **Then the waiter**, which is by definition the only update in flight
           (the lock guarantees single flight).
        """
        if self._session_update_ack_debt > 0:
            self._session_update_ack_debt -= 1
            logger.debug(
                "session.updated matched an unwaited update; %d still outstanding",
                self._session_update_ack_debt,
            )
            return
        self._resolve_session_update(True, None)

    def _resolve_session_update(self, applied: bool, detail: str | None) -> None:
        """Resolve the in-flight session update's waiter, exactly once.

        Called from `_note_session_updated` once older debts are paid, from the
        `error` branch when the error names the update's own `event_id` — that
        path is correlated, so it bypasses the debt entirely — and from
        `_end_session_updates` when the websocket goes away underneath a waiter.
        `detail` carries WHY it was not applied, because those two are different
        events and a journal that called both "rejected" would read false.
        Safe to call when nothing is in flight.
        """
        waiter, self._session_update_waiter = self._session_update_waiter, None
        self._session_update_event_id = None
        if waiter is None or waiter.done():
            return
        if not applied:
            logger.warning("session update was not applied: %s", detail)
        waiter.set_result(applied)

    def _end_session_updates(self, conn: Any) -> None:
        """Close the session-update books for the session that owned *conn*.

        The `finally` this runs from can be LATE: `_restart_session` clears
        `self.connection` and spawns the replacement immediately, so the dead
        session's teardown can land after the new session has already connected
        and booked the +1 its connect-time config owes. Zeroing the debt there
        would hand that connect acknowledgement to the first live mode flip's
        waiter, which is the precise false positive the debt exists to prevent.
        So the two pieces of per-session state are reset only while this
        session still owns the live connection — the same "am I still the live
        one?" guard `_finish_boot_gate` uses (review item 1).

        The waiter is resolved unconditionally: one left installed would sit out
        its whole timeout for an acknowledgement that can no longer arrive, and
        the resolve is idempotent when there is nothing in flight.
        """
        if self.connection is conn:
            self._receive_loop_active = False
            self._session_update_ack_debt = 0
        self._resolve_session_update(False, "the realtime session ended")

    # --- boot gate (Task 6) -------------------------------------------------
    async def _finish_boot_gate(self, reason: str, conn: Any | None = None) -> None:
        """Re-enable turn detection once the greeting can no longer be heard.

        Idempotent, and safe to call from anything that might be stale:

        * *conn* is the connection the caller was born with. If it is no longer
          the live one, a backstop from a session that already ended is trying
          to open the gate of the session that replaced it — refuse (Codex
          round 1, finding 4).
        * the pending gate task is cancelled unless it is the caller itself; a
          task that cancels itself never reaches its own release (finding 3).

        The input buffer is dropped *before* turn detection comes back, so the
        greeting's own audio (and its echo) cannot be committed as the first
        user turn the instant VAD wakes up (round 3, finding 1).
        """
        if not self._boot_gate_active:
            return
        if conn is not None and self.connection is not conn:
            return
        self._boot_gate_active = False
        task, self._boot_gate_task = self._boot_gate_task, None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
        if self.connection is not None:
            try:
                await self.connection.input_audio_buffer.clear()
            except Exception as exc:  # noqa: BLE001 - clearing is best-effort
                logger.debug("boot gate: input buffer clear failed: %s", exc)
        # Base `_push_turn_detection_update` is a no-op: the Hugging Face
        # backend has no session.update semantics we control, so the gate is
        # effective only on the OpenAI backend — which is the locked backend
        # (D-002).
        await self._push_turn_detection_update()
        logger.info("boot gate released (%s)", reason)

    async def _boot_gate_release_after_drain(self, conn: Any) -> None:
        """Wait for the greeting to stop being audible, then open the gate.

        `response.done` means the model finished emitting, not that the speaker
        finished playing: enabling VAD at that instant hands the greeting's own
        tail audio straight to the turn detector, which is the exact failure the
        gate exists to prevent (Codex round 2, finding 1). The cap keeps a stuck
        drain estimate from holding the microphone shut.
        """
        deadline = time.monotonic() + _BOOT_GATE_DRAIN_CAP_S
        try:
            while audio_drain.is_audible() and time.monotonic() < deadline:
                await asyncio.sleep(_BOOT_GATE_DRAIN_POLL_S)
        except asyncio.CancelledError:
            return
        await self._finish_boot_gate("greeting played", conn)

    def _notify_response_started(self) -> None:
        """Notify that a new spoken response has started. Base: no-op.

        The OpenAI subclass overrides this to arm the onset amplitude ramp
        (Task 5) so a fresh reply fades in from silence, giving the robot's
        hardware echo canceller time to converge before full volume. Called
        once per `response.created`; Task 8's rollback-resume calls it again
        to re-arm the ramp for the resumed reply, so this must stay idempotent
        (each call re-arms the full ramp rather than accumulating).
        """
        return None

    def _robot_audible(self) -> bool:
        """Whether Reachy is speaking or still has queued/buffered speech.

        Response lifecycle alone is not enough — queued PCM outlives
        `response.done` (Codex round 1, finding 6).
        """
        return (not self._response_done_event.is_set()) or audio_drain.is_audible()

    def _party_gate_accepts(self, transcript: str) -> bool:
        """Decide whether a committed turn was addressed to the robot.

        Gate order is deliberate and binding: control phrases beat everything
        (「停」 must never be suppressed by any content filter below it); the
        backchannel filter then beats even a live follow-up window (agreement
        noise inside a real conversation must not be treated as re-addressing
        the robot); only after both does a name, the follow-up window, or an
        engaged/substantive face get to accept.
        """
        text = transcript.casefold()
        if _PARTY_CONTROL_RE.search(text):
            return True
        if is_backchannel(transcript):
            return False
        if any(name in text for name in _party_names()):
            return True
        last = self._party_last_accept_at
        if last is not None and (time.monotonic() - last) <= _party_followup_s():
            return True
        if _party_face_gate_enabled() and self._face_engaged() and is_substantive(transcript):
            logger.info("party gate: accepted via engaged face (%d chars)", len(transcript))
            return True
        return False

    def _answer_gate_accepts(self, transcript: str, mode: ConversationMode) -> bool:
        """Whether this committed turn earns a spoken reply, under *mode*.

        The mode is passed in, never read from `self` (Codex round 1, P1-2):
        the verdict belongs to the mode the utterance BEGAN in. Reading the live
        field here would let a flip that happened while someone was still
        talking retroactively reclassify their half-spoken sentence — ambient
        room chatter answered because the mode became 一對一 mid-utterance, or a
        direct question silently dropped because 紀錄模式 started after it.

        Distinct from the barge gate on purpose (2026-08-31 plan, decision 2):
        `_gate_text_accepts` / the name gate decide what may CUT OFF a playing
        reply; this decides what gets ANSWERED. Conflating them is what produced
        the observed pile-up — a turn rolled back as an interruption still got a
        full spoken answer from the server.

        * GROUP keeps `_party_gate_accepts` exactly as it is, ordering included.
        * RECORD accepts only an address name or a control phrase: no engaged
          face, no follow-up window. Quiet-scribe posture — every command needs
          the name, and everything else is transcribed silently.
        * ONE_ON_ONE accepts anything substantive, so a single person never has
          to say the robot's name; only backchannels and empties fall through.
          `REALTIME_ONE_ON_ONE_ANSWER_GATE=name_only` tightens it to RECORD's
          rule — a separate variable from the interruption gate, on purpose
          (Open question 1).
        """
        if mode is ConversationMode.GROUP:
            return self._party_gate_accepts(transcript)
        accepted, _reason = _gate_text_accepts(transcript)
        if mode is ConversationMode.RECORD or _one_on_one_answer_gate() == "name_only":
            return accepted
        return accepted or is_substantive(transcript)

    def _stamp_turn_mode(self, item_id: str | None) -> None:
        """Record the mode this utterance began in, keyed by its input item.

        Every verdict about a turn is taken under the mode it started in, so a
        flip mid-sentence cannot retroactively reclassify speech that is already
        half-spoken (Codex round 1, P1-2).

        Keyed per item rather than held in one field (Codex round 2, 2a-4):
        `transcription.completed` can arrive a second or more after the NEXT
        utterance has already started, and a single field would by then be
        describing the wrong turn. `_turn_mode` stays as the fallback for an
        event that carries no id, and as the value a mode flip re-stamps when
        nobody is speaking.
        """
        mode = self._conversation_mode
        self._turn_mode = mode
        if item_id:
            if item_id not in self._turn_modes and len(self._turn_modes) >= _TURN_MODE_MAX_ITEMS:
                # Only reachable if transcripts stop arriving entirely; drop the
                # oldest so a stuck session cannot grow this without bound. The
                # `not in` guard matters: re-stamping an id already present
                # replaces its entry rather than adding one, so evicting for it
                # would throw away an unrelated turn's stamp to make room that
                # is not needed.
                self._turn_modes.pop(next(iter(self._turn_modes)), None)
            self._turn_modes[item_id] = mode

    def _take_turn_mode(self, item_id: str | None) -> ConversationMode:
        """Pop the mode stamped for this input item, or the fallback stamp."""
        if item_id:
            stamped = self._turn_modes.pop(item_id, None)
            if stamped is not None:
                return stamped
        return self._turn_mode

    def _face_engaged(self) -> bool:
        """Whether a person is currently facing the robot, as an address signal.

        Reads the daemon's already-computed tracking state through
        `get_tracked_face(wait=False)` -- a non-blocking cached read, never new
        vision work (reuse-first: this app owns no camera/detection code of its
        own). The daemon's YuNet detector only fires on near-frontal faces, so
        `detected` is already a coarse orientation proxy; requiring the face to
        also be roughly centered (`abs(x) <= REALTIME_PARTY_FACE_CENTER`)
        tightens that toward "actually facing the robot" rather than glimpsed
        at the edge of frame. `FaceTarget.ts` is stamped with `time.monotonic()`
        on the daemon side (confirmed against `reachy_mini/vision/
        face_tracking.py` and `daemon/backend/abstract.py` in the installed
        SDK), and the app and daemon run on the same host and hence share one
        system monotonic clock, so freshness is checked against
        `time.monotonic()` here too, never the wall clock. Any failure -- a
        torn-down daemon, a mid-init state, an unexpected shape -- is a quiet
        False, never a crash of the gate.
        """
        try:
            face = self.deps.reachy_mini.get_tracked_face(wait=False)
        except Exception:
            return False
        if not face.detected or face.ts is None or face.x is None:
            return False
        if (time.monotonic() - face.ts) > _party_face_fresh_s():
            return False
        # The SDK ships no py.typed marker, so `face.x` type-checks as Any;
        # bool() gives mypy strict a concrete return type back.
        return bool(abs(face.x) <= _party_face_center())

    def _start_party_barge_timer(self) -> None:
        """Arm the debounce: sustained speech while audible = real interruption."""
        if self._party_barge_task is not None and not self._party_barge_task.done():
            self._party_barge_task.cancel()
        self._party_barge_task = asyncio.create_task(
            self._party_barge_confirm(self._party_utterance_seq), name="party-barge-confirm"
        )

    async def _party_barge_confirm(self, seq: int) -> None:
        """After the confirm delay, cancel the reply iff the speech persisted."""
        try:
            await asyncio.sleep(_party_confirm_s())
        except asyncio.CancelledError:
            return
        # Finding 8: re-verify everything — the mode may have flipped, a newer
        # utterance may own the floor, the blip may have ended, or the robot
        # may have finished talking on its own.
        if not self._party_mode or seq != self._party_utterance_seq:
            return
        if not self._party_speech_open or not self._robot_audible():
            return
        logger.info("party barge-in confirmed; cancelling the active reply")
        # Task 5: party mode never pauses, so the heard position is measured
        # live — and before the cancel/flush, which zeroes the drain counters.
        truncate_item, truncate_ms = self._audio_item_id, self._heard_audio_ms()
        await self._cancel_active_response()
        if self._clear_queue:
            self._clear_queue()
        if truncate_item is not None:
            await self._truncate_heard_audio(truncate_item, truncate_ms)

    # --- solo pause-then-decide barge-in (Task 8) ---------------------------
    def _pause_playback(self) -> None:
        """Hold the reply back mid-sentence while the barge decision is pending.

        Nothing is thrown away: `emit()` diverts the audio into `_held_audio`,
        and the drain tracker is told the robot has *not* gone quiet, so neither
        the music hooks nor `_robot_audible()` mistake a pause for a finished
        reply.

        The id of the reply being paused is captured here so a later commit can
        tell it apart from a *different* response that started in the meantime
        (review round, finding 4).

        Task 5 stashes the *audio item* and its heard position for the same
        reason: a commit truncates the reply that was paused, not whatever is
        speaking by the time the decision lands. The heard figure taken here is
        a conservative floor — nothing new reaches the ear during a pause — and
        it has to be read now, because the commit's own flush zeroes the drain
        counters it is computed from.
        """
        self._barge_paused = True
        self._barge_paused_response_id = self._active_response_id
        self._barge_paused_item_id = self._audio_item_id
        self._barge_paused_heard_ms = self._heard_audio_ms()
        audio_drain.note_paused(True)

    def _resume_playback(self, *, rolled_back: bool) -> None:
        """End the pause — by putting the reply back, or by dropping what it held.

        *rolled_back* is the whole decision: True means the voice was not an
        interruption, so the withheld audio plays on (and the onset ramp is
        re-armed, so the resume fades in instead of popping). False means a real
        barge-in was confirmed and the withheld audio belongs to a reply that
        has just been cancelled.

        A timer that is itself resolving the pause must not cancel itself; a
        task that cancels itself never reaches its own release (the same reason
        `_finish_boot_gate` guards it).
        """
        # Captured before the field is cleared below: a rollback hands the id on
        # to `_barge_resumed_response_id`, which is what the late interrupt
        # (Task 4) compares a live response against.
        resumed_id = self._barge_paused_response_id
        self._barge_paused = False
        self._barge_pending = False
        audio_drain.note_paused(False)
        self._barge_paused_response_id = None
        # Both branches: the pause is over either way, and a stash outliving it
        # would aim a later commit's truncate at an item nobody paused. The
        # committing caller captures the pair before it gets here (Task 5).
        self._barge_paused_item_id = None
        self._barge_paused_heard_ms = 0
        current = _current_task()
        confirm, self._barge_confirm_task = self._barge_confirm_task, None
        rollback, self._barge_rollback_task = self._barge_rollback_task, None
        for task in (confirm, rollback):
            _cancel_barge_task(task, current)
        if not rolled_back:
            self._held_audio.clear()
            return
        # The reply is speaking again over a voice that was never judged: if the
        # transcript for that voice turns out to address us after all, Task 4's
        # late interrupt silences exactly this response.
        self._barge_resumed_response_id = resumed_id
        self._notify_response_started()
        # The rolled-back turn produces no response of its own, so nothing else
        # would ever lift the duck `on_user_speech_candidate` applied — the same
        # hazard the party gate's deny path closes (party plan, finding 4). The
        # resume waits for the drain, i.e. for the rest of this reply to play.
        on_turn_without_response(self.deps)
        logger.info("barge-in rolled back; resuming reply")

    def on_external_interrupt(self) -> None:
        """Drop every trace of a pause because something else took over the turn.

        `LocalStream.clear_audio_queue()` calls this *before* it flushes, so an
        operator RPC (`conversation.interrupt` / `conversation.say`) landing
        mid-pause cannot leave held audio behind for a later rollback to
        resurrect (Codex round 2, finding 4). It is also the session-boundary
        and shutdown reset.

        Safe from a non-loop thread (the RPC surface is one) and from inside a
        barge timer: it never cancels the task it is running on, and every timer
        re-checks the flags cleared here before it acts, so a cancellation that
        does not land still leaves an inert timer.
        """
        current = _current_task()
        tasks = (self._barge_confirm_task, self._barge_rollback_task, self._barge_watchdog_task)
        self._barge_confirm_task = None
        self._barge_rollback_task = None
        self._barge_watchdog_task = None
        for task in tasks:
            _cancel_barge_task(task, current)
        self._barge_paused = False
        self._barge_pending = False
        self._barge_speech_open = False
        self._barge_paused_response_id = None
        self._barge_partial_committed_item = None
        self._barge_resumed_response_id = None
        self._barge_late_eligible = False
        self._held_audio.clear()
        # Task 5: a stale item id surviving a `conversation.interrupt` or a
        # reconnect must never be truncated in a later session — the id would
        # name an item from a conversation this handler no longer owns.
        self._audio_item_id = None
        self._audio_item_enqueued_ms = 0.0
        self._barge_paused_item_id = None
        self._barge_paused_heard_ms = 0
        audio_drain.note_paused(False)

    def _barge_reset_for_new_session(self) -> None:
        """Clear solo barge state at the start of every (re)connect.

        Held audio belongs to a reply the dead session was speaking, and a
        cooldown measured against the previous conversation's echo has no
        meaning in this one.
        """
        self.on_external_interrupt()
        self._barge_cooldown_until = 0.0
        self._barge_response_seen = False

    async def _barge_shutdown(self) -> None:
        """Cancel every pending barge timer and wait for it, at session teardown.

        Cancelling is not enough on its own (Codex round 1, finding 8): a timer
        that outlives its session would resolve a pause that belongs to the
        session which replaced it.
        """
        current = _current_task()
        tasks = [
            task
            for task in (self._barge_confirm_task, self._barge_rollback_task, self._barge_watchdog_task)
            if task is not None and task is not current
        ]
        self.on_external_interrupt()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _solo_speech_started(self) -> None:
        """Solo `speech_started`: pause and decide, instead of flushing.

        The legacy branch (``REALTIME_SOLO_CLIENT_BARGE=0``) is the pre-Task-8
        path verbatim — flush now, ask questions never.

        `on_user_speech_candidate` rather than `on_user_speech_started` (Codex
        round 2, finding 2): both duck robot-speaker music, but the latter also
        runs `audio_drain.note_cleared()`, which would tell the drain tracker
        the reply is gone — the exact accounting a rollback depends on.
        """
        if not _solo_client_barge():
            if self._clear_queue:
                self._clear_queue()
            # D-018 / R7: duck robot-speaker music the instant the user starts
            # talking. NOT awaited (finding 1): the pause carries a five-second
            # daemon timeout, and awaiting it here would stall every event
            # queued behind it.
            on_user_speech_started(self.deps)
            return

        self._barge_speech_open = True
        # Task 4 fix round, finding 1: whether a LATE interrupt may fire for
        # this utterance is decided here, at its onset, and nowhere else. Set
        # before the cooldown return on purpose — the cooldown-swallowed pause
        # is one of the cases the late path exists for — while a turn that
        # started in silence records False and can never cancel the answer the
        # server is producing for it.
        self._barge_late_eligible = self._robot_audible()
        on_user_speech_candidate(self.deps)
        if time.monotonic() < self._barge_cooldown_until:
            # The tail of the reply we just cancelled (or its echo) is the most
            # likely thing to trigger VAD right now, and pausing on it would
            # fight the user who is still talking.
            logger.debug("solo barge-in suppressed: inside the post-barge cooldown")
            return
        if not self._robot_audible():
            return  # nothing to protect: ordinary listening
        self._pause_playback()
        self._barge_pending = True
        self._party_utterance_seq += 1  # any sleeping barge timer is now stale
        self._arm_barge_confirm()

    def _solo_speech_stopped(self) -> None:
        """Solo `speech_stopped`: the pause now has a deadline of its own.

        The confirm timer can no longer fire (the speech it was measuring has
        ended), so the rollback clock takes over: if no transcript arrives to
        decide the pause, it resumes the reply by itself. Armed whenever a
        decision is still owed, so a pause can never be left with no timer.
        """
        self._barge_speech_open = False
        if not self._barge_pending:
            return
        confirm, self._barge_confirm_task = self._barge_confirm_task, None
        _cancel_barge_task(confirm, _current_task())
        self._arm_barge_rollback()

    def _arm_barge_confirm(self) -> None:
        """Start the confirm timer for the pause that just began."""
        _cancel_barge_task(self._barge_confirm_task, _current_task())
        self._barge_confirm_task = asyncio.create_task(
            self._confirm_solo_barge(self._party_utterance_seq), name="solo-barge-confirm"
        )

    def _arm_barge_rollback(self) -> None:
        """Start the rollback timer that resumes a pause nothing else resolved."""
        _cancel_barge_task(self._barge_rollback_task, _current_task())
        self._barge_rollback_task = asyncio.create_task(
            self._rollback_timer(self._party_utterance_seq), name="solo-barge-rollback"
        )

    def _arm_barge_watchdog(self) -> None:
        """Start the watchdog that repairs a barged turn the server did not answer."""
        _cancel_barge_task(self._barge_watchdog_task, _current_task())
        self._barge_watchdog_task = asyncio.create_task(
            self._barge_response_watchdog(self._party_utterance_seq), name="solo-barge-watchdog"
        )

    async def _confirm_solo_barge(self, seq: int) -> None:
        """Resolve a pause whose speech outlasted the confirm/max-pause window.

        Gate off: sustained speech IS the proof — commit (pre-plan behavior).
        Gate on: sustained speech proves nothing about address — roll back and
        resume; the transcript paths (partial, completed, late) keep the final
        say. The mode is read once, before the sleep, so the delay and the
        outcome can never come from two different rules.
        """
        gate = _solo_name_gate()
        try:
            await asyncio.sleep(_barge_max_pause_s() if gate else _barge_confirm_s())
        except asyncio.CancelledError:
            return
        # Re-verify everything: the mode may have flipped, a newer utterance may
        # own the floor, the blip may have ended, or the pause may already have
        # been resolved by a transcript or an external interrupt.
        if self._party_mode or seq != self._party_utterance_seq:
            return
        if not self._barge_pending or not self._barge_speech_open:
            return
        if gate:
            # `_barge_speech_open` is still True on purpose: Reachy keeps
            # telling its story over speech that never addressed it. A name
            # arriving later still lands, on the transcript paths.
            logger.info("solo barge pause hit its cap with no address; resuming reply")
            self._barge_pending = False
            self._resume_playback(rolled_back=True)
            return
        logger.info("solo barge-in confirmed by sustained speech; cancelling the active reply")
        await self._commit_solo_barge()

    async def _rollback_timer(self, seq: int) -> None:
        """Resume the reply when a pause has run out of ways to be decided.

        The user blipped and no transcript ever came — an empty commit, a
        dropped turn, a noise the server never transcribed. Whatever it was, it
        was not an interruption.
        """
        try:
            await asyncio.sleep(_barge_rollback_timeout_s())
        except asyncio.CancelledError:
            return
        if self._party_mode or seq != self._party_utterance_seq:
            return
        if not self._barge_pending:
            return
        logger.info("solo barge rolled back (no transcript)")
        self._resume_playback(rolled_back=True)

    async def _barge_response_watchdog(self, seq: int) -> None:
        """Ask for the reply a confirmed barge cancelled and nothing replaced.

        Codex round 1, finding 11, restated for client-driven answering
        (2026-08-31). The server-rejected auto-`response.create` this was
        written for no longer exists — `create_response` is false in every mode
        and the client answers accepted turns itself. What remains is the same
        silence from a different cause: a barge is confirmed *before* the
        turn's own transcript lands (the confirm timer, or
        `_maybe_commit_on_partial` acting on a partial), so the reply is
        cancelled by a turn whose answer depends on a `transcription.completed`
        that may never come — a failed transcription, or one that arrives empty
        and takes the branch's early `continue`. Then the user barged in and got
        nothing at all.

        Only fires when nothing answered the turn and nothing is speaking; a
        turn the answer gate deliberately denied stands it down explicitly
        (`_stand_down_barge_watchdog`).
        """
        try:
            await asyncio.sleep(_BARGE_RESPONSE_WATCHDOG_S)
        except asyncio.CancelledError:
            return
        if self._party_mode or seq != self._party_utterance_seq:
            return
        if self._barge_response_seen or not self._response_done_event.is_set():
            return
        if self._barge_speech_open:
            # The user is talking again; their next commit brings its own
            # response, and answering over them would be worse than waiting.
            return
        logger.info("no reply arrived after a confirmed barge-in; requesting one")
        await self._safe_response_create()

    def _barge_note_response_created(self) -> None:
        """Record that a response did start, and stand the watchdog down."""
        self._barge_response_seen = True
        task, self._barge_watchdog_task = self._barge_watchdog_task, None
        _cancel_barge_task(task, _current_task())

    def _stand_down_barge_watchdog(self) -> None:
        """Cancel the repair watchdog for a turn deliberately left unanswered.

        The watchdog exists to rescue a barged turn whose answer never
        materialised. A turn the answer gate DENIED has no answer to rescue —
        it was heard, kept as context and left unanswered on purpose — so the
        watchdog must not speak for it 1.5 s later.

        Reachable with `REALTIME_SOLO_NAME_GATE=0` together with
        `REALTIME_ONE_ON_ONE_ANSWER_GATE=name_only`: with the name gate off,
        sustained speech alone confirms the barge and arms this watchdog, and
        name-only answering then denies the same turn an answer. Without this
        the two knobs combined would produce exactly the unprompted reply the
        gate refused.

        Deliberately does not touch `_barge_response_seen`: no response was
        seen, and the next commit re-arms the watchdog with its own flag reset.
        """
        task, self._barge_watchdog_task = self._barge_watchdog_task, None
        _cancel_barge_task(task, _current_task())

    async def _commit_solo_barge(self) -> None:
        """Turn a pending pause into a real interruption: cancel, flush, cool down.

        Resolving the pause is claimed **before** the `response.cancel` round
        trip (fix round, finding 2). That await is a window the event loop runs
        in: a `transcription.completed` landing inside it would otherwise find
        `_barge_pending` still True and commit the same pause a second time
        (double cancel, double flush, cooldown re-armed), and a `speech_stopped`
        would cancel this very task mid-await.

        The flush and the resume live in a `finally` for the other half of that
        finding: a `CancelledError` raised inside the round trip must not leave
        `_barge_paused` True with the reply already cancelled — that pause would
        have no timer left to resolve it, and `audio_drain` would stay paused
        for the rest of the session.
        """
        self._barge_pending = False
        # Task 5: the pair to truncate is the one stashed when the pause began,
        # and it has to be read HERE — the `finally` below runs
        # `_resume_playback` (which clears the stash) and `_clear_queue` (which
        # in production runs `on_external_interrupt` and zeroes the drain
        # counters the figure was computed from).
        truncate_item = self._barge_paused_item_id
        truncate_ms = self._barge_paused_heard_ms
        # In production `_clear_queue` IS `console.clear_audio_queue`, which
        # calls `on_external_interrupt()` — a full barge-state reset, including
        # `_barge_speech_open` (fix round, finding 1). Here the flush is our
        # own and the user is by definition still mid-sentence (that is what
        # confirmed the barge), so the flag has to survive it: it is what stops
        # the response watchdog from injecting a reply over a talking user.
        speech_open = self._barge_speech_open
        # Review round, finding 4, restated for client-driven answering
        # (2026-08-31): a *different* response is live, so the reply we paused
        # already ended and something else started speaking in its place. With
        # `create_response` false in every mode that id can only be a
        # CLIENT-created response, and it is never this turn's answer — this
        # turn has not reached the answer gate yet, and `_safe_response_create`
        # is the only thing that could have asked for one. It belongs to
        # something else the sender queue released: an earlier turn's reply, a
        # tool-batch follow-up, a wake-face greeting. Cancelling it would cut
        # that off mid-word, and arming the watchdog for this turn would ask
        # twice for an answer the completed-transcript branch is about to
        # request itself. The held audio still goes: it belongs to the reply
        # that is over. A live id of None is NOT this case — nothing is
        # speaking, so a turn whose transcript never lands would end in silence
        # and still needs the watchdog.
        answer_already_live = (
            self._active_response_id is not None and self._active_response_id != self._barge_paused_response_id
        )
        try:
            if answer_already_live:
                logger.info("solo barge: the paused reply already ended; keeping the new response")
            else:
                await self._cancel_active_response()
        finally:
            if self._clear_queue:
                self._clear_queue()
            self._resume_playback(rolled_back=False)
            self._barge_speech_open = speech_open
        self._barge_cooldown_until = time.monotonic() + _barge_cooldown_s()
        if not answer_already_live:
            self._barge_response_seen = False
            self._arm_barge_watchdog()
        # Dead last, below the watchdog arm (fix round 1, finding 3). This is
        # the only `await` between the flush and the arm, and when the commit
        # runs from the confirm timer the receive loop is free to process a
        # whole short reply — `response.created` *and* `response.done` — inside
        # it. `_barge_response_seen = False` would then erase the proof that the
        # reply existed and the watchdog would ask for a second one, i.e. Reachy
        # speaking unprompted. The truncate is best-effort and order-independent
        # down here.
        #
        # Unconditional across both branches (Codex round 2, finding 4): even
        # when the answer to the barged turn is already live, the paused reply's
        # tail was still dropped, and its unheard text must still leave the
        # model's context.
        if truncate_item is not None:
            await self._truncate_heard_audio(truncate_item, truncate_ms)

    async def _late_solo_interrupt(self) -> None:
        """Silence a reply the transcript proved the user was talking over.

        The pause machinery already resolved (rolled back, cooled down, or was
        never armed), but the committed turn addresses the robot while it is
        audible. The newer-answer guard applies only when we actually have a
        resumed id to compare against (Codex round 1, finding 6): with no
        resumed id, an active response is simply the reply being talked over —
        refusing to cancel it would make the robot unsilenceable during the
        post-barge cooldown. Eligibility (`_barge_late_eligible`, fix round
        finding 1) is what keeps that permissive branch honest: it fires only
        for an utterance that began while Reachy was already talking. The rare
        mis-cancel of a racing answer self-heals through the watchdog below.
        """
        resumed = self._barge_resumed_response_id
        answer_already_live = (
            resumed is not None and self._active_response_id is not None and self._active_response_id != resumed
        )
        if answer_already_live:
            logger.info("late solo interrupt: a newer response is live; leaving it be")
            return
        # Fix round, finding 2 — the same hazard `_commit_solo_barge` guards:
        # in production `_clear_queue` IS `console.clear_audio_queue`, which
        # runs `on_external_interrupt()` and would wipe `_barge_speech_open`.
        # That flag is what stops the watchdog armed below from firing a
        # response at a user who is already talking again.
        speech_open = self._barge_speech_open
        # Task 5: no pause exists on this path either, so the pair is measured
        # live, before the flush zeroes the drain counters behind it.
        truncate_item, truncate_ms = self._audio_item_id, self._heard_audio_ms()
        await self._cancel_active_response()
        if self._clear_queue:
            self._clear_queue()
        self._barge_speech_open = speech_open
        if truncate_item is not None:
            await self._truncate_heard_audio(truncate_item, truncate_ms)
        self._barge_resumed_response_id = None
        self._barge_late_eligible = False
        self._barge_cooldown_until = time.monotonic() + _barge_cooldown_s()
        # The addressed turn must not end in silence (Codex round 1, finding 5):
        # its auto-response may have been rejected against the reply we just
        # cancelled, so give the watchdog the repair duty, exactly as a
        # committed barge does.
        self._barge_response_seen = False
        self._arm_barge_watchdog()

    async def _maybe_commit_on_partial(self, partial: str, item_id: str) -> None:
        """Commit a pending pause the moment a partial transcript addresses us.

        Latency lever for the name gate (`research-realtime-api-2026-08.md`
        §5, and `REALTIME_TRANSCRIPTION_DELAY`): with a streaming
        transcriber the name arrives in a delta long before `completed`. Only
        ever commits — a partial can prove address, never prove its absence.
        Control phrases commit regardless of the name-gate flag (Codex round 1,
        finding 12: a robot you cannot silence is worse than any false
        positive); the name path is gate-mode only.
        """
        if self._party_mode or not self._barge_pending:
            return
        accepted, reason = _gate_text_accepts(partial)
        if not accepted:
            return
        if reason == "name" and not _solo_name_gate():
            return
        logger.info("solo barge-in confirmed by partial transcript (%s)", reason)
        await self._commit_solo_barge()
        # The completed transcript for this item must not re-interrupt the
        # answer this commit is about to produce (Codex round 2, finding 2).
        self._barge_partial_committed_item = item_id

    async def _resolve_solo_barge(self, transcript: str) -> bool:
        """Decide a pending pause from the transcript the turn committed.

        This runs BEFORE the loop's empty-transcript `continue` (Codex round 1,
        finding 9): an empty transcript is a decision too, and leaking past it
        would leave the reply paused with no timer left to resume it.

        Returns True when the turn was a false interruption and the reply has
        been resumed — the caller must then skip the normal turn bookkeeping,
        exactly as the party gate's deny path does, since that bookkeeping
        belongs to the reply that is still speaking.
        """
        task, self._barge_rollback_task = self._barge_rollback_task, None
        _cancel_barge_task(task, _current_task())
        # A robot you cannot silence is worse than any false positive (the party
        # gate's first rule, review round finding 2). 「停」 is one character, so
        # `is_substantive` rejects it against REALTIME_MIN_TURN_CHARS=2 and the
        # reply would roll back and keep talking over the person telling it to
        # stop. Control phrases are checked first, exactly as in `_party_gate_accepts`,
        # and that ordering holds in both branches below: with the name gate on
        # only an address name or a control phrase commits, and with it off the
        # pre-gate substantive rule comes back unchanged.
        if _solo_name_gate():
            accepted, reason = _gate_text_accepts(transcript)
        else:
            control = bool(_PARTY_CONTROL_RE.search(transcript.casefold()))
            accepted = control or is_substantive(transcript)
            reason = "control phrase" if control else "substantive"
        if accepted:
            logger.info("solo barge-in confirmed by transcript (%s, %d chars)", reason, len(transcript))
            await self._commit_solo_barge()
            return False
        self._resume_playback(rolled_back=True)
        # This transcript IS the verdict on the utterance that paused the reply,
        # so the resumed id the resume just recorded has already served its
        # purpose — and the caller `continue`s before the completed handler's
        # own clear (Codex round 3, finding 1). Left set, it would only suppress
        # the newer-answer guard on some future turn.
        self._barge_resumed_response_id = None
        self._barge_late_eligible = False
        if transcript:
            kind = "unaddressed" if _solo_name_gate() and is_substantive(transcript) else "backchannel"
            logger.info("solo barge rolled back (%s)", kind)
            await self.output_queue.put(AdditionalOutputs({"role": "user", "content": transcript}))
            self._emit_transcript("user", transcript, True)
        else:
            logger.info("solo barge rolled back (empty)")
        return True

    def _resolve_solo_barge_failure(self, item_id: str | None) -> None:
        """Roll a pause back when transcription failed: no verdict will ever come.

        The late-interrupt fields are cleared first, ahead of the pending guard:
        the common shape here is a turn whose pause was already resolved (a
        max-pause rollback, or a partial-transcript commit) and whose transcript
        then failed. Nothing about that turn can be decided any more, so neither
        its resumed id nor its committed-item marker may outlive it (Codex round
        3, findings 1 and 2).

        `item_id` is the failing event's own item (T4 m5): the resumed-id and
        eligibility clears are session-wide and stay unconditional, but the
        committed-item marker names one specific turn, so a *different* turn's
        failure must not consume it — that would let the marked turn's own
        completed transcript interrupt a second time.
        """
        self._barge_resumed_response_id = None
        self._barge_late_eligible = False
        if item_id is not None and item_id == self._barge_partial_committed_item:
            self._barge_partial_committed_item = None
        if not self._barge_pending:
            return
        logger.info("solo barge rolled back (transcription failed)")
        self._resume_playback(rolled_back=True)

    async def _cancel_active_response(self) -> None:
        """Cancel the in-flight response, remembering its id to drop late deltas."""
        response_id = self._active_response_id
        if response_id is None or self._response_done_event.is_set() or self.connection is None:
            return
        self._cancelled_response_ids.append(response_id)
        try:
            await self.connection.response.cancel()
        except Exception as exc:  # noqa: BLE001 - "no active response" is a benign race
            logger.debug("response.cancel refused (likely already done): %s", exc)

    def _heard_audio_ms(self) -> int:
        """Milliseconds of the current audio item that provably reached the ear.

        enqueued − outstanding − device buffer − slack, floored at 0:
        `audio_end_ms` above the item's real duration is a server error, so this
        always rounds DOWN (`docs/research-realtime-api-2026-08.md` §2).
        Undershoot deletes a fragment the user actually heard from context;
        overshoot past the item's real duration is a server error that loses
        the whole truncate.

        Accepted limitation (D-028): `outstanding_s()` is global — there is one
        sink — while `_audio_item_enqueued_ms` is per item, so residue from an
        earlier item can only make this figure *smaller*, i.e. under-truncate,
        which is the safe direction.
        """
        if self._audio_item_id is None:
            return 0
        outstanding_ms = audio_drain.outstanding_s() * 1000.0
        buffered_ms = audio_drain.device_buffered_s() * 1000.0
        return max(0, int(self._audio_item_enqueued_ms - outstanding_ms - buffered_ms - _TRUNCATE_SLACK_MS))

    async def _truncate_heard_audio(self, item_id: str, audio_end_ms: int) -> None:
        """Cut the server's copy of a cancelled reply at the heard position.

        WebSocket transport: the server never truncates on its own, so without
        this every barge leaves unheard text in the model's context — which is
        what makes an interrupted Reachy repeat itself. Commit paths only:
        truncation deletes the item's transcript server-side and cannot be
        rolled back, so no rollback path may ever reach here.
        """
        if self.connection is None or audio_end_ms <= 0:
            return
        try:
            await self.connection.conversation.item.truncate(
                item_id=item_id, content_index=0, audio_end_ms=audio_end_ms
            )
        except Exception as exc:  # noqa: BLE001 - a stale/finished item is a benign race
            logger.debug("conversation.item.truncate refused: %s", exc)

    @staticmethod
    def _sanitize_tool_result_for_model(tool_name: str, tool_result: dict[str, Any]) -> dict[str, Any]:
        """Remove bulky transport-only fields before echoing tool output back to the model.

        Keyed on the payload, not the tool's name: `look_around` returns a
        picture too, and a name list would have to be maintained for every tool
        that ever does (Codex round 1, P2-1). `tool_name` stays in the signature
        for the log line and for future per-tool rules.
        """
        if "b64_im" in tool_result:
            sanitized = dict(tool_result)
            sanitized.pop("b64_im", None)
            sanitized["image_attached"] = True
            return sanitized
        return tool_result

    def _normalize_startup_voice(self, voice: str | None) -> str | None:
        """Return a valid persisted startup voice, or None."""
        return self._resolve_backend_voice(voice, source="persisted startup voice")

    async def _wait_for_response_done_before_tool_result(self) -> bool:
        """Return whether the function-call response finished before sending tool output."""
        if self._response_done_event.is_set():
            return True

        try:
            await asyncio.wait_for(
                self._response_done_event.wait(),
                timeout=_RESPONSE_DONE_TIMEOUT,
            )
            return True
        except asyncio.TimeoutError:
            return False

    async def wait_for_reply_finished(self) -> bool:
        """Wait for the response now being generated to finish. Never raises.

        Step 4 of the sleep path (Codex round 1, P2-10). `go_to_sleep` is called
        from the tool worker the instant
        `response.function_call_arguments.done` arrives — which is BEFORE
        `response.done` and before the rest of the goodbye's audio deltas exist.
        Draining the speaker at that moment finds nothing audible and the robot
        lies down mid-sentence. Waiting here means every delta of the goodbye is
        enqueued before anything starts measuring whether it has played. The
        microphone and the barge machine are already silenced by the time this
        runs (round 2, 2a-6), so the wait cannot let a new turn in.

        **Loop-aware** (round 2, 2a-5): the inactivity path reaches `GoToSleep`
        through `app_lifecycle.run_go_to_sleep_tool`, which does
        `asyncio.run(...)` on a daemon thread with its own fresh loop. Awaiting
        `_response_done_event` — an `asyncio.Event` bound to the handler's
        loop — from there is undefined behavior, so a caller on any other loop
        is marshalled across. No handler loop at all means nothing to wait for,
        which is success, not failure.
        """
        event = self._response_done_event
        if event.is_set():
            return True
        # A session that died mid-response leaves the event clear forever:
        # nothing will ever arrive to set it, so waiting is ten seconds of
        # nothing on every shutdown that follows a dropped websocket (Codex
        # round 3, finding 2).
        if self.connection is None:
            return True
        loop = self._handler_loop
        if loop is None or loop.is_closed():
            return True
        try:
            running: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            try:
                await asyncio.wait_for(event.wait(), timeout=_GOODBYE_RESPONSE_WAIT_S)
                return True
            except asyncio.TimeoutError:
                return False
        future = asyncio.run_coroutine_threadsafe(
            asyncio.wait_for(event.wait(), timeout=_GOODBYE_RESPONSE_WAIT_S), loop
        )
        try:
            await asyncio.to_thread(future.result, _GOODBYE_RESPONSE_WAIT_S + 1.0)
            return True
        except Exception:  # noqa: BLE001 - timeout, cancellation, or a dead loop
            future.cancel()
            return False

    def _resolve_backend_voice(
        self,
        voice: str | None,
        *,
        source: str,
        fallback: str | None = None,
    ) -> str | None:
        """Return a backend-supported voice, optionally falling back when unsupported."""
        available_voices = get_available_voices()
        voice_value = (voice or "").strip()
        if not voice_value:
            return fallback

        voice_by_lowercase = {candidate.lower(): candidate for candidate in available_voices}
        normalized_voice = voice_by_lowercase.get(voice_value.lower())
        if normalized_voice is not None:
            return normalized_voice

        if voice:
            logger.warning(
                "Ignoring unsupported %s %r; expected one of %s",
                source,
                voice,
                available_voices,
            )
        return fallback

    def _get_session_config(self, tool_specs: list[ToolSpec]) -> RealtimeSessionCreateRequestParam:
        """Return the Hugging Face OpenAI-compatible session config."""
        return RealtimeSessionCreateRequestParam(
            type="realtime",
            # The mode's rules block rides along, so a reconnect brings the
            # session up in the mode the handler is actually in (Task 3).
            instructions=self._mode_instructions(),
            audio=RealtimeAudioConfigParam(
                input=RealtimeAudioConfigInputParam(
                    # The OpenAI SDK type only includes 24 kHz PCM, but the HF
                    # compatible server uses rate=None for native 16 kHz mode.
                    format=_native_rate_audio_pcm(),  # type: ignore[typeddict-item]
                    transcription=AudioTranscriptionParam(
                        model="gpt-4o-transcribe",
                        language=config.REALTIME_TRANSCRIPTION_LANGUAGE,
                    ),
                    turn_detection=ServerVad(type="server_vad", interrupt_response=True),
                ),
                output=RealtimeAudioConfigOutputParam(
                    format=_native_rate_audio_pcm(),  # type: ignore[typeddict-item]
                    voice=self.get_current_voice(),
                ),
            ),
            tools=to_realtime_tools_config(tool_specs),
            tool_choice="auto",
        )

    def _session_config_fallback(
        self, cfg: RealtimeSessionCreateRequestParam
    ) -> RealtimeSessionCreateRequestParam | None:
        """Return a downgraded config for a subclass to retry a rejected update.

        The base HF-compatible backend has nothing to downgrade to, so a
        rejected `session.update` here is a real failure (Task 4).
        """
        return None

    def _is_connected(self) -> bool:
        """Return whether the realtime connection is open."""
        return self.connection is not None

    def _idle_behavior_ready(self) -> bool:
        """Hold idle behavior while a model response is still active."""
        return self._response_done_event.is_set()

    async def _cancel_partial_transcript_task(self) -> None:
        if self.partial_transcript_task and not self.partial_transcript_task.done():
            self.partial_transcript_task.cancel()
            try:
                await self.partial_transcript_task
            except asyncio.CancelledError:
                pass

    async def change_voice(self, voice: str) -> str:
        """Change only the voice, updating the active session when possible."""
        default_voice = get_default_voice()
        resolved_voice = (
            self._resolve_backend_voice(voice, source="requested voice", fallback=default_voice) or default_voice
        )
        self._voice_override = resolved_voice
        if self.connection is not None:
            # Through the one ordered update mechanism, like every other live
            # update (Task 3, Codex round 2, 2a-2): an uncorrelated
            # `session.updated` is only safe to match while exactly one update
            # can be in flight, and a voice change sent around the mechanism
            # would have its acknowledgement resolve somebody else's waiter.
            def _build() -> RealtimeSessionCreateRequestParam | None:
                return RealtimeSessionCreateRequestParam(
                    type="realtime",
                    audio=RealtimeAudioConfigParam(
                        output=RealtimeAudioConfigOutputParam(
                            voice=resolved_voice,
                        ),
                    ),
                )

            if await self._apply_session_update(_build, what=f"voice {resolved_voice}"):
                return f"Voice changed to {resolved_voice}."
            return "Voice change failed. Will take effect on next connection."
        return "Voice changed. Will take effect on next connection."

    def get_current_voice(self) -> str:
        """Return the voice currently selected for this handler."""
        default_voice = get_default_voice()
        voice = self._voice_override or get_session_voice(default=default_voice)
        return self._resolve_backend_voice(voice, source="session voice", fallback=default_voice) or default_voice

    async def apply_personality(self, profile: str | None) -> str:
        """Apply a personality to the active or next realtime connection."""
        previous_profile = config.REACHY_MINI_CUSTOM_PROFILE
        set_custom_profile(profile)
        try:
            instructions = self._mode_instructions()
            voice = self.get_current_voice()
            core_tools.initialize_tools(force=True)
        except Exception as exc:
            set_custom_profile(previous_profile)
            logger.error("Failed to resolve personality %r: %s", profile, exc)
            return f"Failed to apply personality: {exc}"

        if self.connection is not None:
            # Same single-flight mechanism as every other live update (Task 3),
            # but deliberately without the acknowledgement wait (review Minor
            # 5): the `_restart_session()` two lines down is unconditional and
            # is what actually applies the personality, so waiting out the ack
            # first would only delay it. The send is still ordered and its ack
            # still booked, so it cannot disturb a mode flip.
            def _build() -> RealtimeSessionCreateRequestParam | None:
                return RealtimeSessionCreateRequestParam(
                    type="realtime",
                    instructions=instructions,
                    audio=RealtimeAudioConfigParam(
                        output=RealtimeAudioConfigOutputParam(
                            voice=voice,
                        ),
                    ),
                )

            if await self._apply_session_update(
                _build, what=f"personality {profile or 'default'}", wait_for_ack=False
            ):
                logger.info("Applied personality via live update: %s", profile or "default")
            else:
                logger.warning("Live update failed; will restart session")

            try:
                await self._restart_session()
                return "Applied personality and restarted realtime session."
            except Exception as exc:
                logger.warning("Failed to restart session after apply: %s", exc)
                return "Applied personality. Will take effect on next connection."

        logger.info(
            "Applied personality recorded: %s (no live connection; will apply on next session)",
            profile or "default",
        )
        return "Applied personality. Will take effect on next connection."

    async def _emit_debounced_partial(self, transcript: str, item_id: str, sequence_counter: int) -> None:
        """Emit partial transcript after debounce delay."""
        try:
            await asyncio.sleep(self.partial_debounce_delay)

            input_transcript = self.input_transcript_chunks_by_item
            if input_transcript.item_id == item_id and len(input_transcript.deltas) - 1 == sequence_counter:
                await self.output_queue.put(AdditionalOutputs({"role": "user_partial", "content": transcript}))
                logger.debug(f"Debounced partial emitted: {transcript}")
        except asyncio.CancelledError:
            logger.debug("Debounced partial cancelled")
            raise

    def _record_partial_transcript_delta(
        self,
        input_transcript: InputTranscriptChunksByItem,
        item_id: str,
        delta: str,
    ) -> None:
        """Record a Hugging Face partial transcript snapshot."""
        input_transcript.item_id = item_id
        input_transcript.deltas = [delta]

    async def start_up(self) -> None:
        """Start the handler with minimal retries on unexpected websocket closure."""
        self.client = await self._build_realtime_client()

        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                await self._run_realtime_session()
                # Normal exit from the session, stop retrying
                return
            except ConnectionClosedError as e:
                # Abrupt close (e.g., "no close frame received or sent") → retry
                logger.warning("Realtime websocket closed unexpectedly (attempt %d/%d): %s", attempt, max_attempts, e)
                if attempt < max_attempts:
                    self.client = await self._build_realtime_client()
                    # exponential backoff with jitter
                    base_delay = 2 ** (attempt - 1)  # 1s, 2s, 4s, 8s, etc.
                    jitter = random.uniform(0, 0.5)
                    delay = base_delay + jitter
                    logger.info("Retrying in %.1f seconds...", delay)
                    await asyncio.sleep(delay)
                    continue
                raise
            finally:
                # never keep a stale reference
                self.connection = None
                try:
                    self._connected_event.clear()
                except Exception:
                    pass

    async def _restart_session(self) -> None:
        """Force-close the current session and start a fresh one in background.

        Does not block the caller while the new session is establishing.
        """
        try:
            if self.connection is not None:
                try:
                    await self.connection.close()
                except Exception:
                    pass
                finally:
                    self.connection = None

            # Ensure we have a client (start_up must have run once)
            if getattr(self, "client", None) is None:
                logger.warning("Cannot restart: realtime client not initialized yet.")
                return

            # Fire-and-forget new session and wait briefly for connection
            try:
                self._connected_event.clear()
            except Exception:
                pass
            self.client = await self._build_realtime_client()
            asyncio.create_task(self._run_realtime_session(), name="realtime-session-restart")
            try:
                await asyncio.wait_for(self._connected_event.wait(), timeout=5.0)
                logger.info("Realtime session restarted and connected.")
            except asyncio.TimeoutError:
                logger.warning("Realtime session restart timed out; continuing in background.")
        except Exception as e:
            logger.warning("_restart_session failed: %s", e)

    async def _safe_response_create(self, **kwargs: Any) -> None:
        """Enqueue a response.create() kwargs for the sender worker _response_sender_loop().

        This method never blocks the caller.
        """
        await self._pending_responses.put(kwargs)

    async def say(self, text: str) -> None:
        """Inject ``text`` as a turn and have the model voice it now.

        Mirrors the startup-greeting path: create a user message item, then
        queue a ``response.create`` through the serial sender. Not verbatim TTS
        (speech-to-speech may rephrase). Raises if the session is closed.
        """
        text = (text or "").strip()
        if not text:
            raise ValueError("say: empty text")
        if not self.connection:
            raise RuntimeError("say: no active session")
        await self.connection.conversation.item.create(
            item={
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": text}],
            },
        )
        self._mark_activity("say")
        await self._safe_response_create()

    async def _wake_face_identification(self) -> Any:
        """Return the wake-time `Identification`, or None when no check happened (D-013, D-015).

        The single auto-recognition hook in the app: one bounded check at wake
        time, never a continuous scan. Inside it, up to `FACE_WAKE_ATTEMPTS`
        looks, and the first confident one wins. Every round shares **one**
        monotonic deadline covering readiness, frame capture and identification
        together — hitting it, or any exception at all, yields None, and the
        caller then sends the greeting unchanged and on time. Face memory must
        never be able to delay or lose the first thing Reachy says.

        A *miss* still returns its `Identification`: "somebody is there but I
        cannot place them" is a real outcome the caller greets differently from
        "the camera saw nobody". Only the paths where no look ever happened —
        disabled, no camera, no frame, timeout, exception — return None.
        """
        if not env_bool("FACE_AUTO_GREET", True):
            return None

        recognizer = self.deps.face_recognizer
        if recognizer is None:
            return None
        # Checked here rather than left to `wait_ready` returning False, so the
        # log says "disabled" instead of misattributing the skip to the budget.
        if not getattr(recognizer, "enabled", True):
            logger.info("Face memory is disabled; greeting unchanged.")
            return None
        if not self.deps.camera_enabled:
            logger.info("No camera available for the wake face check; greeting unchanged.")
            return None

        budget_s = env_int("FACE_WAKE_BUDGET_MS", _FACE_WAKE_BUDGET_MS_DEFAULT, lo=0, hi=10_000) / 1000.0
        attempts = env_int("FACE_WAKE_ATTEMPTS", _FACE_WAKE_ATTEMPTS_DEFAULT, lo=1, hi=5)
        deadline = time.monotonic() + budget_s
        started = time.monotonic()

        def remaining() -> float:
            return deadline - time.monotonic()

        # `Identification` from face_id.py, untyped here for the same reason the
        # recognizer itself is: the runtime injects it, and this module must keep
        # working with face memory absent entirely.
        identification: Any = None
        rounds = 0
        try:
            if remaining() <= 0.0:
                return None
            ready = await asyncio.wait_for(asyncio.to_thread(recognizer.wait_ready, remaining()), remaining())
            if not ready:
                logger.info("Face memory not ready within the wake budget; greeting unchanged.")
                return None

            for attempt in range(1, attempts + 1):
                if remaining() <= 0.0:
                    break
                round_started = time.monotonic()
                # Correction 1: the wake check reads the camera through the same
                # media path as the tools; a None frame skips recognition entirely.
                frame = await asyncio.wait_for(asyncio.to_thread(self.deps.reachy_mini.media.get_frame), remaining())
                if frame is None:
                    return None

                if remaining() <= 0.0:
                    break
                identification = await asyncio.wait_for(asyncio.to_thread(recognizer.identify, frame), remaining())
                rounds = attempt
                logger.info(
                    "Wake face check round %d/%d: status=%s score=%s in %.0f ms",
                    attempt,
                    attempts,
                    identification.status,
                    identification.score,
                    (time.monotonic() - round_started) * 1000.0,
                )
                if identification.status == "recognized" and identification.name:
                    break

                # A short pause between looks, so the next frame is a genuinely
                # different one — the pose, the blink or the light has moved on.
                # Never past the deadline: the pause is budget, not extra time.
                pause = min(_FACE_WAKE_RETRY_PAUSE_S, remaining())
                if attempt == attempts or pause <= 0.0:
                    break
                await asyncio.sleep(pause)
        except asyncio.TimeoutError:
            logger.info("Face memory exceeded its %.0f ms wake budget; greeting unchanged.", budget_s * 1000.0)
            return None
        except Exception as e:
            logger.warning("Face memory check failed at wake time: %s: %s", type(e).__name__, e)
            return None

        elapsed_ms = (time.monotonic() - started) * 1000.0
        if identification is None or identification.status != "recognized" or not identification.name:
            logger.info(
                "Wake face check: %d round(s), last status=%s score=%s in %.0f ms; nobody recognized.",
                rounds,
                identification.status if identification is not None else "none",
                identification.score if identification is not None else None,
                elapsed_ms,
            )
            # Still the identification, not None: a face that is *there* but
            # unplaceable is greeted as a stranger, and only the caller knows that.
            return identification

        logger.info(
            "Wake face check: recognized %s (score %.3f) on round %d of %d in %.0f ms.",
            identification.name,
            identification.score or 0.0,
            rounds,
            attempts,
            elapsed_ms,
        )
        return identification

    async def _remembered_facts(self, name: str, timeout_s: float) -> list[str]:
        """Return the newest remembered facts for `name`, newest first.

        One reader for both recognition moments (boot greeting and the extended
        wake window) so the two can never drift on the limit, the off switch or
        the failure policy. Three rules, all of them "the greeting wins": the
        store is read off the event loop, the read is bounded, and any failure
        — including that bound expiring — is a warning and an empty list, never
        an exception through a caller that is about to speak.
        """
        limit = env_int("FACE_GREETING_FACTS", PERSON_FACTS_DEFAULT, lo=0, hi=20)
        if limit <= 0:
            return []
        try:
            facts = await asyncio.wait_for(
                asyncio.to_thread(facts_for_person, self.deps.instance_path, name, limit=limit),
                timeout_s,
            )
        except Exception as e:
            logger.warning("Could not read person facts for greeting: %s: %s", type(e).__name__, e)
            return []
        return [fact.text for fact in facts]

    async def _extended_wake_face_check(self) -> None:
        """Keep the wake face check alive briefly after the greeting (D-013 hook, part 2).

        The pre-greeting check gets ~4000 ms at the exact moment of boot — the
        14/14 on-robot failure mode is simply that nobody is posed in frame at
        that instant. This extension keeps looking for a bounded few seconds
        *after* the greeting went out; a hit becomes a context item plus a
        queued spoken follow-up (the response sender loop serializes it behind
        the greeting). The window closes silently the moment the user speaks —
        a context item landing mid-turn could steer the answer. It runs once
        per app start and is cancelled at shutdown; recognition never becomes
        a continuous scan.
        """
        if not env_bool("FACE_AUTO_GREET", True):
            return
        budget_ms = env_int("FACE_WAKE_EXTENDED_MS", _FACE_WAKE_EXTENDED_MS_DEFAULT, lo=0, hi=20_000)
        if budget_ms <= 0:
            return
        recognizer = self.deps.face_recognizer
        if recognizer is None or not getattr(recognizer, "enabled", True) or not self.deps.camera_enabled:
            return
        connection = self.connection
        if connection is None:
            return

        deadline = time.monotonic() + budget_ms / 1000.0

        def remaining() -> float:
            return deadline - time.monotonic()

        rounds = 0
        try:
            ready = await asyncio.wait_for(asyncio.to_thread(recognizer.wait_ready, remaining()), remaining())
            if not ready:
                logger.info("Extended wake face check: face memory not ready within the window.")
                return
            while remaining() > 0.0 and not self._user_has_spoken:
                if self.connection is not connection:
                    logger.info("Extended wake face check: session changed; window closed.")
                    return
                frame = await asyncio.wait_for(
                    asyncio.to_thread(self.deps.reachy_mini.media.get_frame), remaining()
                )
                if frame is not None:
                    identification = await asyncio.wait_for(
                        asyncio.to_thread(recognizer.identify, frame), remaining()
                    )
                    rounds += 1
                    if identification.status == "recognized" and identification.name:
                        if self._user_has_spoken or self.connection is not connection:
                            logger.info("Extended wake face check: hit arrived too late; window closed.")
                            return
                        name = identification.name
                        # The boot gate is still holding turn detection off
                        # while the greeting plays, and its drain cap does NOT
                        # restart for a second response (see `response.done`).
                        # Injecting now would queue a reply that is still
                        # speaking when that cap fires — VAD back on with the
                        # robot audible, which is the echo turn the gate exists
                        # to prevent. Wait it out inside the same budget,
                        # polling exactly as the drain waiter does.
                        async def _gate_released() -> None:
                            while self._boot_gate_active:
                                await asyncio.sleep(_BOOT_GATE_DRAIN_POLL_S)

                        try:
                            await asyncio.wait_for(_gate_released(), remaining())
                        except asyncio.TimeoutError:
                            logger.info(
                                "Extended wake face check: boot gate still closed at the deadline; "
                                "window closed without greeting %s.",
                                name,
                            )
                            return
                        # The same recall the boot greeting gets, one window
                        # later. Its own 1 s bound stays *inside* this window's
                        # deadline, so a slow store can never push the late
                        # greeting past the budget it was given. Read *before*
                        # the re-check below on purpose: that check has to stay
                        # the last thing between here and `item.create`, which
                        # is exactly what its comment promises.
                        facts = await self._remembered_facts(
                            name, min(_FACE_FACTS_READ_TIMEOUT_S, max(remaining(), 0.0))
                        )
                        # The gate wait can be seconds long and the recall is
                        # bounded rather than instant, so both turn-safety
                        # conditions are asked again before anything is sent.
                        if self._user_has_spoken or self.connection is not connection:
                            logger.info("Extended wake face check: hit went stale while gated; window closed.")
                            return
                        # Person-scoped memory label (spec §3.3): set on
                        # recognition, cleared per session. Below the re-check
                        # so a label can never be written into the session that
                        # replaced this one and already cleared it.
                        self.deps.current_person = name
                        # Whole-run guest list for the sleep summary: the label
                        # above is overwritten by the next recognition, this one
                        # is not — it is what the visit is summarized against,
                        # stamped so an old sighting cannot claim a new visit.
                        self.deps.record_recognition(name)
                        late_prompt = (
                            _FACE_LATE_KNOWN_WITH_FACTS_PROMPT.format(name=name, facts="；".join(facts))
                            if facts
                            else _FACE_LATE_RECOGNITION_PROMPT.format(name=name)
                        )
                        # Bounded so a stalled network write cannot keep this
                        # task alive far past its window; 5 s is a transport
                        # guard, not budget — hence its own handler, so a stall
                        # is never logged as an expired window.
                        try:
                            await asyncio.wait_for(
                                connection.conversation.item.create(
                                    item={
                                        "type": "message",
                                        "role": "user",
                                        "content": [
                                            {
                                                "type": "input_text",
                                                "text": late_prompt,
                                            },
                                        ],
                                    },
                                ),
                                5.0,
                            )
                        except asyncio.TimeoutError:
                            logger.warning(
                                "Extended wake face check: item.create timed out; window closed without greeting %s.",
                                name,
                            )
                            return
                        await self._safe_response_create()
                        logger.info(
                            "Extended wake face check: recognized %s (score %.3f) on round %d; "
                            "queued a late named greeting with %d remembered fact(s).",
                            name,
                            identification.score or 0.0,
                            rounds,
                            len(facts),
                        )
                        return
                pause = min(_FACE_WAKE_EXTENDED_PAUSE_S, remaining())
                if pause <= 0.0:
                    break
                await asyncio.sleep(pause)
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            logger.info("Extended wake face check: window expired mid-round after %d round(s).", rounds)
            return
        except Exception as e:
            logger.warning("Extended wake face check failed: %s: %s", type(e).__name__, e)
            return
        logger.info("Extended wake face check: no recognition in %d round(s); window closed.", rounds)

    async def _send_startup_greeting_prompt(self) -> None:
        """Prompt the model to open the conversation once the session is ready."""
        if self._startup_greeting_sent or not self.connection:
            return

        greeting_prompt = get_session_greeting_prompt().strip()
        if not greeting_prompt:
            self._startup_greeting_sent = True
            # No greeting means no response will ever complete, so nothing would
            # ever release the boot gate but its backstop timer. Open it now
            # rather than starting the conversation deaf for eight seconds.
            await self._finish_boot_gate("no greeting configured")
            return

        identification = await self._wake_face_identification()
        recognized = identification is not None and identification.status == "recognized" and identification.name
        facts: list[str] = []
        if recognized:
            facts = await self._remembered_facts(identification.name, _FACE_FACTS_READ_TIMEOUT_S)
            # Person-scoped memory label (spec §3.3): set on recognition,
            # cleared per session.
            self.deps.current_person = identification.name
            # And onto the visit's guest list, stamped: it outlives the label and
            # the session it was set in (sleep_summary.py).
            self.deps.record_recognition(identification.name)
            logger.info(
                "Startup greeting personalized for %s with %d remembered fact(s).",
                identification.name,
                len(facts),
            )
        greeting_prompt = _startup_greeting_prefix(identification, facts) + greeting_prompt

        try:
            await self.connection.conversation.item.create(
                item={
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": greeting_prompt,
                        },
                    ],
                },
            )
            self._startup_greeting_sent = True
            self._mark_activity("startup_greeting_prompt")
            await self._safe_response_create()
            logger.info("Queued startup greeting prompt")
            # Task 5: nobody was *placed* in the seconds before the greeting —
            # either the frame was empty or the face could not be matched. Keep
            # looking for a bounded few seconds now that the greeting is out, and
            # greet by name late if the person resolves. The method re-checks the
            # kill switch itself; the guard here only avoids creating a task
            # that would instantly return.
            if not recognized and env_bool("FACE_AUTO_GREET", True):
                self._wake_face_task = asyncio.create_task(
                    self._extended_wake_face_check(), name="extended-wake-face-check"
                )
        except Exception as e:
            logger.warning("Failed to queue startup greeting prompt: %s", e)

    async def _response_sender_loop(self) -> None:
        """Dedicated worker that sends ``response.create()`` calls serially.

        This logic was designed to comply with the response.create() docstring specification for event ordering:
        https://github.com/openai/openai-python/blob/3e0c05b84a2056870abf3bd6a5e7849020209cc3/src/openai/resources/realtime/realtime.py#L649C1-L651C30

        For each queued request the worker:
        1. Waits until no response is active (_response_done_event).
        2. Sends response.create().
        3. Waits until the receiver observes response.created or a rejection.
        4. Waits for the response cycle to complete (response.done).
        5. If the server rejected with active_response, retries from step 1.
        """
        while self.connection:
            try:
                kwargs = await self._pending_responses.get()
            except asyncio.CancelledError:
                return

            # Parallel tool calls enqueue duplicate empty requests; coalesce to one.
            while not kwargs and not self._pending_responses.empty():
                try:
                    self._pending_responses.get_nowait()
                except asyncio.QueueEmpty:
                    break

            sent = False
            max_retries = 5
            attempts = 0
            while not sent and self.connection and attempts < max_retries:
                try:
                    await asyncio.wait_for(
                        self._response_done_event.wait(),
                        timeout=_RESPONSE_DONE_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    logger.debug("Timed out waiting for previous response to finish; forcing ahead")
                    self._response_done_event.set()

                if not self.connection:
                    break

                self._last_response_rejected = False
                self._response_started_or_rejected_event.clear()
                try:
                    await self.connection.response.create(**kwargs)
                except Exception as e:
                    logger.debug("_response_sender_loop: send failed: %s", e)
                    self._response_done_event.set()
                    break

                try:
                    await asyncio.wait_for(
                        self._response_started_or_rejected_event.wait(),
                        timeout=_RESPONSE_DONE_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    logger.debug("Timed out waiting for response.created or response rejection")

                # Check if the receiver loop observed an asynchronous rejection.
                if self._last_response_rejected:
                    attempts += 1
                    if attempts >= max_retries:
                        logger.debug("response.create rejected %d times; giving up", attempts)
                        break
                    logger.debug("response.create was rejected; retrying (%d/%d)", attempts, max_retries)
                    await asyncio.sleep(_RESPONSE_REJECTION_RETRY_DELAY)
                    continue

                try:
                    await asyncio.wait_for(
                        self._response_done_event.wait(),
                        timeout=_RESPONSE_DONE_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    logger.debug("Timed out waiting for response.done; assuming response completed")
                    self._response_done_event.set()
                    break

                sent = True

    async def _handle_tool_result(self, completed_tool: ToolNotification) -> None:
        """Process the result of a tool call and close its music-ducking phase.

        D-018 / round 2 finding 1: `needs_response` decides whether anything else
        will ever end this turn. When the last tool of a batch wants no reply
        there is no further `response.created`, so the hook has to close the turn
        and schedule the music resume itself — otherwise the track stays paused
        for the rest of the conversation. The notification is in a `finally` so
        the failure and connection-closed paths end the phase too; a tool whose
        phase never ends blocks every later resume.
        """
        follow_up_requested = False
        try:
            follow_up_requested = await self._deliver_tool_result(completed_tool)
        finally:
            if not completed_tool.is_idle_tool_call:
                on_tool_call_finished(completed_tool.id, needs_response=follow_up_requested)

    async def _deliver_tool_result(self, completed_tool: ToolNotification) -> bool:
        """Send one tool result back. Returns whether a follow-up response was asked for."""
        if completed_tool.error is not None:
            logger.error(
                "Tool '%s' (id=%s) failed with error: %s",
                completed_tool.tool_name,
                completed_tool.id,
                completed_tool.error,
            )
            tool_result = {"error": completed_tool.error}
            tool_result_for_model = tool_result
        elif completed_tool.result is not None:
            tool_result = completed_tool.result
            tool_result_for_model = (
                self._sanitize_tool_result_for_model(completed_tool.tool_name, tool_result)
                if isinstance(tool_result, dict)
                else tool_result
            )
            logger.info(
                "Tool '%s' (id=%s) executed successfully.",
                completed_tool.tool_name,
                completed_tool.id,
            )
            logger.debug("Tool '%s' model-visible result: %s", completed_tool.tool_name, tool_result_for_model)
        else:
            logger.warning(
                "Tool '%s' (id=%s) returned no result and no error", completed_tool.tool_name, completed_tool.id
            )
            tool_result = {"error": "No result returned from tool execution"}
            tool_result_for_model = tool_result

        # Connection may have closed while tool was running
        if not self.connection:
            logger.warning(
                "Connection closed during tool '%s' (id=%s) execution; cannot send result back",
                completed_tool.tool_name,
                completed_tool.id,
            )
            return False

        try:
            send_result_to_model = not completed_tool.is_idle_tool_call
            if send_result_to_model:
                self._mark_activity("tool_result_ready")
            model_result_submitted = False
            if send_result_to_model and isinstance(completed_tool.id, str):
                if not await self._wait_for_response_done_before_tool_result():
                    send_result_to_model = False
                if not send_result_to_model:
                    logger.warning(
                        "Dropping realtime model result for tool '%s' (id=%s) because response.done was not observed",
                        completed_tool.tool_name,
                        completed_tool.id,
                    )
                elif not self.connection:
                    logger.warning(
                        "Connection closed before sending tool '%s' (id=%s) result back",
                        completed_tool.tool_name,
                        completed_tool.id,
                    )
                    return False
                else:
                    await self.connection.conversation.item.create(
                        item={
                            "type": "function_call_output",
                            "call_id": completed_tool.id,
                            "output": json.dumps(tool_result_for_model),
                        },
                    )
                    model_result_submitted = True

            await self.output_queue.put(
                AdditionalOutputs(
                    {
                        "role": "assistant",
                        "content": json.dumps(tool_result_for_model),
                    },
                ),
            )

            # Any tool result carrying a picture is attached, not just `camera`'s
            # — `look_around` returns one too (Codex round 1, P2-1).
            if model_result_submitted and "b64_im" in tool_result:
                # use raw base64, don't json.dumps (which adds quotes)
                b64_im = tool_result["b64_im"]
                if not isinstance(b64_im, str):
                    logger.warning("Unexpected type for b64_im: %s", type(b64_im))
                    b64_im = str(b64_im)
                image_width = tool_result.get("image_width")
                image_height = tool_result.get("image_height")
                jpeg_bytes_value = tool_result.get("jpeg_bytes")
                jpeg_bytes = jpeg_bytes_value if isinstance(jpeg_bytes_value, int) else (len(b64_im) * 3) // 4
                await self.connection.conversation.item.create(
                    item={
                        "type": "message",
                        "role": "user",
                        "content": [
                            {
                                "type": "input_image",
                                "image_url": f"data:image/jpeg;base64,{b64_im}",
                            },
                        ],
                    },
                )
                if isinstance(image_width, int) and isinstance(image_height, int):
                    logger.info(
                        "Added camera image to conversation frame=%sx%s jpeg_bytes=%s",
                        image_width,
                        image_height,
                        jpeg_bytes,
                    )
                else:
                    logger.info(
                        "Added camera image to conversation jpeg_bytes=%s",
                        jpeg_bytes,
                    )

            if isinstance(completed_tool.id, str):
                self._in_flight_tool_calls.discard(completed_tool.id)

            try:
                tool = core_tools.get_tools().get(completed_tool.tool_name)
            except Exception:
                # The result is already submitted and the call is out of flight;
                # a broken registry must not stop the response that closes the turn.
                logger.exception("Tool registry lookup failed for '%s'", completed_tool.tool_name)
                tool = None
            # Always surface errors, skip the spoken follow-up for tools that opt out.
            if model_result_submitted and (completed_tool.error is not None or tool is None or tool.needs_response):
                self._tool_batch_needs_response = True

            # Parallel tool calls in one turn: respond once every result is in, not per tool.
            if self._tool_batch_needs_response and not self._in_flight_tool_calls:
                self._tool_batch_needs_response = False
                await self._safe_response_create()
                return True

        except ConnectionClosedError:
            logger.warning("Connection closed while sending tool result")
            self.connection = None
            self._response_done_event.set()
        # No follow-up response was asked for on this path, so nothing else will
        # end the turn (D-018, round 2 finding 1).
        return False

    async def _run_realtime_session(self) -> None:
        """Establish and manage a single realtime session."""
        # Boot gate (Task 6): a reconnect drops into an ongoing conversation
        # whose greeting played long ago, so there is nothing left to gate.
        # Belt and braces — the session-config builder tests the same condition
        # itself, so it is correct wherever the config is built.
        if self._startup_greeting_sent:
            self._boot_gate_active = False
        # Party session-boundary reset (Task 7): stale follow-up windows must
        # not carry into a new session, first or reconnected alike.
        self._party_reset_for_new_session()
        # Solo barge session-boundary reset (Task 8): a pause, its held audio and
        # its timers belong to the session that opened them.
        self._barge_reset_for_new_session()
        # Session-update bookkeeping is per session too (Task 3). Debt is a
        # promise THIS websocket made to send a `session.updated`; carried into
        # the next one it would eat that session's first real acknowledgement
        # and leave a live mode flip waiting out its whole timeout.
        self._session_update_ack_debt = 0
        self._receive_loop_active = False
        # The loop this handler's session runs on, so a caller from another
        # thread's loop can marshal onto it (round 2, 2a-5).
        self._handler_loop = asyncio.get_running_loop()
        # Person-scoped memory label (spec §3.3): set on recognition, cleared
        # per session. Cleared here, before the session config is built and
        # therefore before the connection is published, so nothing in the new
        # session — instructions or a routed tool — can read an identity that
        # was established in the session this one replaces. A reconnect
        # re-establishes it through the wake checks or `who_is_this`.
        self.deps.current_person = None
        # A fresh session starts from the static core, first connect and
        # reconnect alike: a box opened in the session that died says nothing
        # about the one replacing it.
        self.close_toolboxes("new session")
        tool_specs = get_tool_specs(exclusion_list=self._mode_tool_exclusions())
        logger.info(
            "Tools to be used in conversation: %s",
            [tool["name"] for tool in tool_specs],
        )
        connect_kwargs: dict[str, Any] = {}
        if self._realtime_connect_query:
            connect_kwargs["extra_query"] = self._realtime_connect_query
        async with self.client.realtime.connect(**connect_kwargs) as conn:
            try:
                session_config = self._get_session_config(tool_specs)
                try:
                    await conn.session.update(session=session_config)
                    # Sent before the receive loop exists, so its
                    # `session.updated` arrives with nobody waiting on it and
                    # must not be allowed to resolve a later waiter
                    # (Codex round 3, finding 5).
                    self._session_update_ack_debt += 1
                except Exception:
                    fallback = self._session_config_fallback(session_config)
                    if fallback is None:
                        logger.exception("Realtime session.update failed; aborting startup")
                        raise
                    logger.warning("session.update rejected; retrying with legacy transcription shape")
                    await conn.session.update(session=fallback)
                    # Same reasoning as the update it replaces: pre-loop, so its
                    # acknowledgement is owed to nobody.
                    self._session_update_ack_debt += 1
                logger.info(
                    "Realtime session initialized with profile=%r voice=%r",
                    getattr(config, "REACHY_MINI_CUSTOM_PROFILE", None),
                    self.get_current_voice(),
                )
            except Exception:
                logger.exception("Realtime session.update failed; aborting startup")
                raise

            logger.info("Realtime session updated successfully")

            # Reset the partial-transcript accumulator for each new session
            self.input_transcript_chunks_by_item = InputTranscriptChunksByItem()

            # Manage events received from the realtime server.
            self.connection = conn
            try:
                self._connected_event.set()
            except Exception:
                pass

            # D-018 / R7 + finding 3: a new realtime session gets a new
            # confirmation epoch, so nothing armed in the previous conversation
            # can be confirmed in this one, and starts with clean audio-drain
            # bookkeeping. Round 3, finding 2: keep the token this session was
            # minted with — the `finally` below closes *this* session, never
            # whichever one happens to be live by the time it runs.
            session_token = await on_session_started(self.deps)
            self._hanova_session = session_token

            response_sender_task: asyncio.Task[None] | None = None
            try:
                # Start the background tool manager
                self.tool_manager.start_up(tool_callbacks=[self._handle_tool_result])

                # Start the response sender worker
                response_sender_task = asyncio.create_task(self._response_sender_loop(), name="response-sender")
                await self._send_startup_greeting_prompt()

                # Boot-gate backstop: a greeting that never produces a
                # `response.done` (rejected, dropped, or a model that stays
                # silent) must not leave the microphone gated forever. Bound to
                # THIS connection, and cancelled in the `finally` below, so it
                # cannot open the gate of a session it does not belong to.
                if self._boot_gate_active:

                    async def _boot_gate_timeout(bound_conn: Any) -> None:
                        try:
                            await asyncio.sleep(
                                env_float("REALTIME_BOOT_GATE_TIMEOUT_S", _BOOT_GATE_TIMEOUT_S_DEFAULT, lo=0.0)
                            )
                        except asyncio.CancelledError:
                            return
                        await self._finish_boot_gate("timeout", bound_conn)

                    self._boot_gate_task = asyncio.create_task(
                        _boot_gate_timeout(conn), name="boot-gate-timeout"
                    )

                # From here on an acknowledgement can actually be observed, so a
                # live session update may wait for one (Codex round 3,
                # finding 1). Everything sent before this point — the connect
                # config, its retry, the no-greeting boot-gate release — books
                # debt instead of waiting.
                self._receive_loop_active = True

                async for event in self.connection:
                    logger.debug("Realtime event: %s", event.type)
                    if event.type == "session.updated":
                        # The server applied a `session.update`. Which one is
                        # positional, not correlated: the event carries no
                        # client event_id. `_note_session_updated` is where that
                        # is made safe.
                        self._note_session_updated()

                    if event.type == "input_audio_buffer.speech_started":
                        self._stamp_turn_mode(getattr(event, "item_id", None))
                        self._user_has_spoken = True
                        self._mark_activity("user_speech_started")
                        self._turn_user_done_at = None
                        self._turn_response_created_at = None
                        self._turn_first_audio_at = None
                        if self._party_mode:
                            # Party: a voice in the room is a candidate, not an
                            # interruption. Duck the music only; the reply keeps
                            # playing unless the speech outlasts the debounce
                            # while Reachy is audible (plan T2).
                            self._party_speech_open = True
                            self._party_utterance_seq += 1
                            on_user_speech_candidate(self.deps)
                            if self._robot_audible():
                                self._start_party_barge_timer()
                        else:
                            # Solo (Task 8): pause the reply and decide, rather
                            # than flushing it on the first syllable.
                            self._solo_speech_started()
                        self.deps.movement_manager.set_listening(True)
                        logger.debug("User speech started")

                    if event.type == "input_audio_buffer.speech_stopped":
                        self._mark_activity("user_speech_stopped")
                        self._party_speech_open = False
                        if not self._party_mode:
                            self._solo_speech_stopped()
                        self.deps.movement_manager.set_listening(False)
                        logger.debug("User speech stopped - server will auto-commit with VAD")

                    if event.type == "response.output_audio.done":
                        self.deps.movement_manager.set_speaking(False)
                        # D-018 / R7: the assistant's turn produced its last audio.
                        # This only *schedules* the resume; it fires when
                        # console.play_loop reports the audio has actually drained.
                        # The in-flight call ids go with it (fix round, finding 2):
                        # they are what the hook reconciles its own phase against,
                        # so a tool cancelled without reporting back cannot defer
                        # every later resume.
                        on_assistant_turn_ended(self.deps, self._in_flight_tool_calls)
                        logger.debug("response completed")

                    if event.type == "response.output_text.delta":
                        logger.debug("response text delta")

                    if event.type == "response.output_text.done":
                        logger.debug("response text done: %s", event.text)

                    if event.type == "response.created":
                        # D-018 / finding 1: a new response means any resume that is
                        # waiting on the previous turn's drain signal is now wrong.
                        # It also opens the drain generation, which is PENDING from
                        # this moment on -- before any audio exists (round 2).
                        on_response_created()
                        self._mark_activity("response_created")
                        self._active_response_id = getattr(getattr(event, "response", None), "id", None)
                        # Task 5, fix round 1 finding 2: `_audio_item_id` is
                        # deliberately NOT reset here. `response.created` is not
                        # the moment the previous reply stops being audible — a
                        # tool turn creates its follow-up response while the
                        # first reply's PCM is still coming out of the speaker,
                        # and a barge in that window would find no item to
                        # truncate. The delta handler's item-change reset covers
                        # the real hazard on its own: item ids are unique, so
                        # the first delta of any new item zeroes the tally
                        # before anything can read it.
                        self.deps.movement_manager.set_speaking(True)
                        self._notify_response_started()
                        # Task 8: a reply exists, so the post-barge watchdog has
                        # nothing left to repair.
                        self._barge_note_response_created()
                        self._response_done_event.clear()
                        self._response_started_or_rejected_event.set()
                        if self._turn_user_done_at is not None and self._turn_response_created_at is None:
                            self._turn_response_created_at = time.perf_counter()
                            delta_ms = (self._turn_response_created_at - self._turn_user_done_at) * 1000
                            logger.info("Turn latency: response.created %.0f ms after user transcript", delta_ms)
                        logger.debug("Response created (active)")

                    if event.type == "response.done":
                        # Doesn't mean the audio is done playing
                        # Resume tracking for responses that emit no audio (text-only / tool-only).
                        self.deps.movement_manager.set_speaking(False)
                        # D-018 / R7: a text-only or tool-only response never emits
                        # response.output_audio.done, so end the turn here too. The
                        # hook is idempotent, and it refuses to schedule a resume
                        # while a tool call is still in flight -- a tool turn is
                        # always followed by a second, speaking response (finding 1).
                        on_assistant_turn_ended(self.deps, self._in_flight_tool_calls)
                        # Task 4: the resumed reply finishing naturally is the
                        # end of that id's meaning — the bounded cleanup for a
                        # timer rollback whose speech never produced a
                        # transcript at all (Codex round 3, finding 1).
                        # `_barge_late_eligible` deliberately does NOT reset
                        # here (fix round, finding 1): it records what was true
                        # at the utterance's onset, and a reply keeps draining
                        # out of the speaker long after `response.done` — this
                        # is exactly when 「停」 over a resumed reply arrives.
                        response_obj = getattr(event, "response", None)
                        done_id = getattr(response_obj, "id", None)
                        if done_id is not None and done_id == self._barge_resumed_response_id:
                            self._barge_resumed_response_id = None
                        # Task 7: a reply the `max_output_tokens` rail cut is
                        # otherwise silent — it just stops mid-word, with no
                        # wrap-up sentence and no error anywhere.
                        status = getattr(response_obj, "status", None)
                        if status not in (None, "completed"):
                            details = getattr(response_obj, "status_details", None)
                            reason = getattr(details, "reason", None)
                            if reason == "max_output_tokens":
                                logger.warning(
                                    "Reply cut off by REALTIME_MAX_OUTPUT_TOKENS "
                                    "(status=incomplete); raise the rail if this recurs"
                                )
                            else:
                                logger.info("response ended status=%s reason=%s", status, reason)
                        self._active_response_id = None
                        self._response_done_event.set()
                        self._response_started_or_rejected_event.set()
                        # Boot gate (Task 6): while gated, VAD is off, so the
                        # only response that can exist is the greeting or an
                        # operator `say` — either is a correct release point.
                        # The release itself waits for the audio to drain
                        # (Codex round 3, finding 1): opening the gate here
                        # would hand the greeting's own tail to the turn
                        # detector. Swap the timeout backstop for the drain
                        # waiter; a drain waiter already running is left alone
                        # so repeated `response.done`s cannot keep restarting
                        # its cap.
                        if (
                            self._boot_gate_active
                            and self._boot_gate_task is not None
                            and self._boot_gate_task.get_name() != _BOOT_GATE_DRAIN_TASK
                        ):
                            self._boot_gate_task.cancel()
                            self._boot_gate_task = asyncio.create_task(
                                self._boot_gate_release_after_drain(conn), name=_BOOT_GATE_DRAIN_TASK
                            )
                        logger.debug("Response done")

                    if event.type == "conversation.item.input_audio_transcription.delta":
                        self._mark_activity("user_transcription_delta")
                        logger.debug(f"User partial transcript: {event.delta}")

                        item_id = event.item_id
                        delta = event.delta or ""

                        input_transcript = self.input_transcript_chunks_by_item
                        self._record_partial_transcript_delta(input_transcript, item_id, delta)

                        current_partial = "".join(input_transcript.deltas)
                        sequence_counter = len(input_transcript.deltas) - 1

                        # Task 2 (2026-08-30 plan): the name or a 「停」 showing up
                        # in a partial resolves a pending pause now, rather than
                        # waiting for `transcription.completed`.
                        await self._maybe_commit_on_partial(current_partial, item_id)

                        await self._cancel_partial_transcript_task()

                        # Start new debounce timer with the last delta
                        self.partial_transcript_task = asyncio.create_task(
                            self._emit_debounced_partial(current_partial, item_id, sequence_counter)
                        )

                    # Handle completed transcription (user finished speaking)
                    if event.type == "conversation.item.input_audio_transcription.completed":
                        self._mark_activity("user_transcription_completed")
                        raw_transcript = event.transcript or ""
                        transcript = raw_transcript.strip()
                        # Popped at the TOP, not at the gate: the branch has
                        # three `continue`s before the gate (empty transcript,
                        # rolled-back pause, partial-commit marker) and a stamp
                        # left behind by any of them would leak.
                        turn_mode = self._take_turn_mode(getattr(event, "item_id", None))
                        logger.debug("User transcript: %s", raw_transcript)
                        self.deps.movement_manager.set_listening(False)

                        await self._cancel_partial_transcript_task()

                        # Task 8: resolve a pending solo pause FIRST — before the
                        # empty-transcript `continue` below, which would
                        # otherwise leak the pause (Codex round 1, finding 9). A
                        # rolled-back turn is handled entirely in there.
                        # `pause_committed` then keeps the late path (below)
                        # from interrupting a second time on the same turn: the
                        # reply now playing is the answer that commit asked for.
                        pause_committed = False
                        if self._barge_pending:
                            if await self._resolve_solo_barge(transcript):
                                continue
                            pause_committed = True
                        if (
                            self._barge_partial_committed_item is not None
                            and event.item_id == self._barge_partial_committed_item
                        ):
                            # This turn already interrupted via its partial
                            # transcript; the reply now playing is its answer.
                            self._barge_partial_committed_item = None
                            pause_committed = True

                        if not transcript:
                            logger.debug("Ignoring empty user transcript")
                            continue

                        if not self._answer_gate_accepts(transcript, turn_mode):
                            # Heard, kept as context (it is already in the
                            # conversation), and left unanswered. Close the turn
                            # for the music hooks (party plan, finding 4) and
                            # touch nothing else — the tool-batch state belongs
                            # to an accepted turn that may still be running.
                            logger.info("%s (%d chars)", _ANSWER_DENY_LOG[turn_mode], len(transcript))
                            if not self._party_mode:
                                # This utterance is decided — denied — so the
                                # solo barge lifecycle it owned closes here as
                                # well as on the accept path below (round 2,
                                # finding 1 lifecycle). Left set, a stale
                                # resumed id makes `_late_solo_interrupt`'s
                                # `answer_already_live` guard suppress the NEXT
                                # real 「瑞奇停」, and stale late-eligibility
                                # credits a later turn with an onset over a
                                # talking robot that it never had.
                                self._barge_resumed_response_id = None
                                self._barge_late_eligible = False
                                # A denied turn has no answer to repair, so the
                                # watchdog a confirmed barge armed for it must
                                # not fire one. Only reachable with
                                # REALTIME_SOLO_NAME_GATE=0 and
                                # REALTIME_ONE_ON_ONE_ANSWER_GATE=name_only,
                                # where sustained speech confirms the barge and
                                # name-only answering then denies it.
                                self._stand_down_barge_watchdog()
                            on_turn_without_response(self.deps)
                            await self.output_queue.put(AdditionalOutputs({"role": "user", "content": transcript}))
                            self._emit_transcript("user", transcript, True)
                            continue

                        # Task 4 (2026-08-30 plan): the pause is over — rolled
                        # back at the max pause, swallowed by the cooldown, or
                        # never armed — but this committed turn addresses the
                        # robot while it is still audible. Silence it now, or
                        # the name gate's worst case is Reachy talking over the
                        # person who called its name. `_barge_late_eligible`
                        # (fix round, finding 1) restricts that to utterances
                        # that began over a talking robot: from silence, the
                        # audible response IS this turn's answer.
                        if (
                            not self._party_mode
                            and _solo_client_barge()
                            and not pause_committed
                            and self._barge_late_eligible
                            and self._robot_audible()
                        ):
                            accepted, reason = _gate_text_accepts(transcript)
                            if accepted and (reason == "control phrase" or _solo_name_gate()):
                                logger.info("late solo interrupt (%s) on committed turn", reason)
                                await self._late_solo_interrupt()
                        if not self._party_mode:
                            # The rollback's utterance is now decided either way;
                            # a lingering resumed-id would suppress a future real
                            # interrupt (round 2, finding 1 lifecycle), and the
                            # onset audibility belongs to the utterance that is
                            # now over.
                            self._barge_resumed_response_id = None
                            self._barge_late_eligible = False

                        self._turn_user_done_at = time.perf_counter()
                        self._turn_response_created_at = None
                        self._turn_first_audio_at = None
                        self._in_flight_tool_calls.clear()
                        self._tool_batch_needs_response = False

                        await self.output_queue.put(AdditionalOutputs({"role": "user", "content": transcript}))
                        self._emit_transcript("user", transcript, True)
                        # Engagement memory (sleep_summary.py): only turns that
                        # got this far. A party-gate denial above and a rolled-
                        # back solo barge both `continue` before here on purpose
                        # — speech the robot decided was not addressed to it is
                        # not part of the conversation it will summarize.
                        record_transcript(self.deps, "user", transcript)

                        if turn_mode is ConversationMode.GROUP:
                            # The follow-up window is a GROUP concept: it lets a
                            # conversation continue without re-addressing by
                            # name. RECORD deliberately has none. Keyed on the
                            # turn's own mode for the same reason the verdict is.
                            self._party_last_accept_at = time.monotonic()
                        # `create_response` is off in every mode since
                        # 2026-08-31: this turn passed its mode's answer gate, so
                        # answer it — through the sender queue, never the raw
                        # connection (party plan, finding 1).
                        await self._safe_response_create()

                    if event.type == "conversation.item.input_audio_transcription.failed":
                        self._mark_activity("user_transcription_failed")
                        # No transcript will ever arrive for this item, so its
                        # stamp has no reader left; pop it rather than leak it.
                        self._take_turn_mode(getattr(event, "item_id", None))
                        if self._party_mode:
                            # No transcript will ever arrive for this turn, so no
                            # gate decision and no response: close it for the
                            # music hooks (finding 4).
                            on_turn_without_response(self.deps)
                        else:
                            # Task 8: same reasoning for a pending solo pause —
                            # nothing is coming that could confirm it. The
                            # failing item is named so the partial-commit marker
                            # is only consumed when it is *this* turn's (T4 m5).
                            self._resolve_solo_barge_failure(getattr(event, "item_id", None))
                        logger.debug("User transcription failed")

                    # Handle assistant transcription
                    if event.type == "response.output_audio_transcript.done":
                        self._mark_activity("assistant_transcript_done")
                        logger.debug(f"Assistant transcript: {event.transcript}")
                        await self.output_queue.put(
                            AdditionalOutputs({"role": "assistant", "content": event.transcript})
                        )
                        self._emit_transcript("assistant", event.transcript or "", True)
                        record_transcript(self.deps, "assistant", event.transcript or "")

                    # Handle audio delta
                    if event.type == "response.output_audio.delta":
                        if getattr(event, "response_id", None) in self._cancelled_response_ids:
                            # Finding 8: response.cancel is asynchronous; audio
                            # already in flight from the cancelled reply must not
                            # reach the speaker after the local flush.
                            logger.debug("Dropping audio delta from a cancelled response")
                            continue
                        decoded_pcm_bytes = base64.b64decode(event.delta)
                        decoded_pcm = np.frombuffer(decoded_pcm_bytes, dtype=np.int16).reshape(1, -1)
                        self._mark_activity("assistant_audio_delta")
                        # D-018 / round 2 finding 1: the drain tracker has to know
                        # the audio exists BEFORE it enters the queue. Counting it
                        # only when play_loop dequeues is what let response.done
                        # look "drained" with the whole reply still buffered.
                        on_response_audio(
                            sample_count=len(decoded_pcm_bytes) // 2,  # 16-bit mono frames
                            sample_rate=self.SAMPLE_RATE,
                        )
                        # Task 5: per-item enqueued total, the numerator of the
                        # `conversation.item.truncate` position. Counted here,
                        # like the drain accounting above, because this is the
                        # one place that sees every frame — including the ones
                        # a barge-in pause later diverts into `_held_audio`.
                        #
                        # This item-change branch is the ONLY reset (fix round
                        # 1, finding 2). Accepted residual: an item that has
                        # fully drained keeps its id until the next item's first
                        # delta, so a barge in that gap truncates it at roughly
                        # its own duration minus the slack — still under the
                        # real duration, so never a server error, and the item
                        # was heard in full anyway.
                        #
                        # A delta that names NO item is not accounted for at all
                        # (D-028 §5): its frames belong to an item we cannot
                        # identify, and adding them to whichever tally happens
                        # to be live would push `enqueued` past that item's real
                        # duration — the one input that can make `audio_end_ms`
                        # exceed the item and turn the truncate into a server
                        # error. The audio itself still plays and still counts
                        # toward the drain accounting above; only the per-item
                        # numerator skips it, which under-counts, the safe
                        # direction.
                        audio_item_id = getattr(event, "item_id", None)
                        if audio_item_id is not None:
                            if audio_item_id != self._audio_item_id:
                                self._audio_item_id = audio_item_id
                                self._audio_item_enqueued_ms = 0.0
                            self._audio_item_enqueued_ms += (
                                (len(decoded_pcm_bytes) // 2) / float(self.SAMPLE_RATE) * 1000.0
                            )
                        if self._turn_user_done_at is not None and self._turn_first_audio_at is None:
                            self._turn_first_audio_at = time.perf_counter()
                            delta_ms = (self._turn_first_audio_at - self._turn_user_done_at) * 1000
                            logger.info("Turn latency: first audio delta %.0f ms after user transcript", delta_ms)
                        await self.output_queue.put(
                            (
                                self.SAMPLE_RATE,
                                decoded_pcm,
                            ),
                        )
                    # ---- tool-calling plumbing ----
                    if event.type == "response.function_call_arguments.done":
                        self._mark_activity("tool_call_received")
                        tool_name = getattr(event, "name", None)
                        args_json_str = getattr(event, "arguments", None)
                        call_id: str = str(getattr(event, "call_id", uuid.uuid4()))

                        logger.info(
                            "Tool call received — tool_name=%r, call_id=%s, args=%s",
                            tool_name,
                            call_id,
                            args_json_str,
                        )

                        if not isinstance(tool_name, str) or not isinstance(args_json_str, str):
                            logger.error(
                                "Invalid tool call: tool_name=%s (type=%s), args=%s (type=%s), call_id=%s",
                                tool_name,
                                type(tool_name).__name__,
                                args_json_str,
                                type(args_json_str).__name__,
                                call_id,
                            )
                            continue

                        self._in_flight_tool_calls.add(call_id)
                        # D-018 / finding 1: a tool call in flight means this turn
                        # is not over — a second, speaking response is still to
                        # come — so no resume may be scheduled until it finishes.
                        # It is tracked by the same call id as above, which is what
                        # the turn-end reconciliation compares against.
                        on_tool_call_started(call_id)
                        background_tool = await self.tool_manager.start_tool(
                            call_id=call_id,
                            tool_call_routine=ToolCallRoutine(
                                tool_name=tool_name,
                                args_json_str=args_json_str,
                                deps=self.deps,
                            ),
                            is_idle_tool_call=False,
                        )

                        await self.output_queue.put(
                            AdditionalOutputs(
                                {
                                    "role": "assistant",
                                    "content": f"🛠️ Used tool {tool_name} with args {args_json_str}. The tool is now running. Tool ID: {background_tool.tool_id}",
                                },
                            ),
                        )
                        logger.info(
                            "Started background tool: %s (id=%s, call_id=%s)",
                            tool_name,
                            background_tool.tool_id,
                            call_id,
                        )

                    # server error
                    if event.type == "error":
                        err = getattr(event, "error", None)
                        msg = getattr(err, "message", str(err) if err else "unknown error")
                        code = getattr(err, "code", "") or getattr(err, "type", "")

                        if (
                            self._session_update_event_id is not None
                            and getattr(err, "event_id", None) == self._session_update_event_id
                        ):
                            # This error belongs to our in-flight session update,
                            # not to any response. Resolve the update's waiter and
                            # keep it out of the response-create synchronization
                            # path entirely — every non-response error below sets
                            # `_response_started_or_rejected_event`, which would
                            # falsely wake `_response_sender_loop` mid-
                            # `response.create` (Codex round 1, P1-3).
                            self._resolve_session_update(False, f"rejected by the server ({code}: {msg})")
                            continue

                        if code == "conversation_already_has_active_response":
                            # response.create was rejected.  The sender worker
                            # is waiting on _response_done_event; when the active
                            # response finishes it will wake up and see this flag.
                            self._last_response_rejected = True
                            self._response_started_or_rejected_event.set()
                            logger.debug("response.create rejected; worker will retry after active response finishes")
                        else:
                            self._response_started_or_rejected_event.set()
                            logger.error("Realtime error [%s]: %s (raw=%s)", code, msg, err)

                        if code == "input_audio_buffer_commit_empty":
                            self.deps.movement_manager.set_listening(False)

                        # Only show user-facing errors, not internal state errors.
                        if code not in (
                            "input_audio_buffer_commit_empty",
                            "conversation_already_has_active_response",
                        ):
                            await self.output_queue.put(
                                AdditionalOutputs({"role": "assistant", "content": f"[error] {msg}"})
                            )
            finally:
                # Session updates (Task 3): no acknowledgement can be observed
                # once this loop is over. Conn-guarded inside, because this can
                # run after a restart's replacement session is already live.
                self._end_session_updates(conn)

                # A session that dies mid-response leaves `_response_done_event`
                # clear forever. Anything still waiting on it — the sleep path
                # most of all — is waiting for a response that can no longer
                # finish, so end the wait rather than let it burn its timeout
                # (Codex round 3, finding 2). Conn-guarded for the same reason
                # as `_end_session_updates(conn)` above: a reconnect's
                # replacement session owns both of these by the time this runs,
                # and must not have them cleared out from under it.
                if self.connection is None or self.connection is conn:
                    self._response_done_event.set()
                    self._handler_loop = None

                # Solo barge-in (Task 8): a pause must never outlive the session
                # that opened it — it would hold audio the next session cannot
                # play and keep the drain tracker reporting a robot that speaks.
                await self._barge_shutdown()

                # Boot gate (Task 6): whatever is still pending — the backstop
                # timer or the drain waiter — belongs to THIS session and must
                # not outlive it. A survivor would either release a gate that
                # the next session legitimately re-armed, or send a session
                # update down a connection that is already gone.
                if self._boot_gate_task is not None:
                    boot_gate_task, self._boot_gate_task = self._boot_gate_task, None
                    boot_gate_task.cancel()
                    try:
                        await boot_gate_task
                    except asyncio.CancelledError:
                        pass

                # Extended wake face window (Task 5): it holds this session's
                # connection and may be mid-`item.create`. Cancel and await it
                # here, exactly like the boot gate above, so nothing it started
                # outlives the session it was looking on behalf of.
                if self._wake_face_task is not None:
                    wake_face_task, self._wake_face_task = self._wake_face_task, None
                    wake_face_task.cancel()
                    try:
                        await wake_face_task
                    except asyncio.CancelledError:
                        pass

                # Stop the response sender worker.
                if response_sender_task is not None:
                    response_sender_task.cancel()
                    try:
                        await response_sender_task
                    except asyncio.CancelledError:
                        pass

                # Stop background tool manager tasks (listener + cleanup) in all paths.
                await self.tool_manager.shutdown()

                # Round 2, finding 8: this connection is over however it ended —
                # clean exit, exception, or cancellation. Stop the daemon audio
                # and close the confirmation gate here rather than hoping
                # shutdown() runs; a connection that drops on its own is the
                # common case. Round 3, finding 2: with the LOCAL token, so a
                # replacement connection that already opened cannot be torn
                # down by the one it replaced.
                await on_session_shutdown(self.deps, session_token)

    # Microphone receive
    async def receive(self, frame: Tuple[int, NDArray[np.int16]]) -> None:
        """Receive audio frame from the microphone and send it to the realtime server.

        Handles both mono and stereo audio formats, converting to the expected
        mono format for the realtime API.

        Args:
            frame: A tuple containing (sample_rate, audio_data).

        """
        if not self.connection:
            return

        _, audio_frame = frame
        if audio_frame.size == 0:
            return

        # Reshape if needed
        if audio_frame.ndim == 2:
            # channels-last convention
            if audio_frame.shape[1] > audio_frame.shape[0]:
                audio_frame = audio_frame.T
            # Multiple channels -> Mono channel
            if audio_frame.shape[1] > 1:
                audio_frame = audio_frame[:, 0]

        # Cast if needed
        audio_frame = audio_to_int16(audio_frame)

        # Send to the realtime input buffer (guard against races during reconnect).
        try:
            audio_message = base64.b64encode(audio_frame.tobytes()).decode("utf-8")
            await self.connection.input_audio_buffer.append(audio=audio_message)
        except Exception as e:
            logger.debug("Dropping audio frame: connection not ready (%s)", e)
            return

    async def shutdown(self) -> None:
        """Shutdown the handler."""
        # D-018 / R7 + finding 3: the daemon keeps playing a sound file after our
        # session dies, so a shutdown that leaves music running is a bug the user
        # hears -- and a confirmation left armed is one the next conversation
        # could consume. Round 2, finding 8: this is now the *second* line of
        # defence; `_run_realtime_session()`'s finally is the first. Round 3,
        # finding 2: presenting the handler's own token makes "running it twice"
        # a no-op by construction, and makes a shutdown() that arrives after a
        # reconnect unable to close the reconnected session.
        await on_session_shutdown(self.deps, self._hanova_session)

        # D-027: the visit's last-chat summary, written once, only when this
        # shutdown is the one that follows `go_to_sleep` -- settings and backend
        # restarts (console.py:307, :697) reach here mid-visit and must not
        # summarize. `write_sleep_summaries` never raises and is timeout-bounded,
        # and it builds (and closes) its own client, so it takes no argument here.
        # Deliberately AFTER the music stop above and before `connection.close()`:
        # the summarizer call can take seconds, and the daemon would keep playing
        # through all of them if this ran first -- audible, with Reachy already in
        # the sleep pose.
        if self.deps.sleep_requested and not self._sleep_summary_done:
            self._sleep_summary_done = True
            written = await write_sleep_summaries(self.deps)
            if written:
                logger.info("Sleep summary: wrote last-chat fact for %d person(s).", written)

        # 紀錄模式's room log is per visit and lives only in memory. `shutdown()`
        # also runs for settings and backend restarts (console.py:307, :697),
        # which are mid-visit — D-027 already refuses to summarize on those, and
        # for the same reason they must not throw away a meeting that is still
        # happening. Only the sleep that ends the visit clears it.
        if self.deps.sleep_requested:
            clear_record_log(self.deps)

        # `go_to_sleep` reaches shutdown, and so does every other end of a
        # visit: boxes never outlive the conversation that opened them. Not
        # gated on `sleep_requested` like the record log above — a settings or
        # backend restart rebuilds the handler, and the session that comes back
        # must not be told it still has a family it was never sent.
        self.close_toolboxes("shutdown")

        # Unblock the response sender worker so it can exit
        self._response_done_event.set()

        # Task 8: second line of defence for a pause left open, exactly as the
        # session `finally` is the first.
        await self._barge_shutdown()

        # Stop background tool manager tasks (listener + cleanup)
        await self.tool_manager.shutdown()

        await self._cancel_partial_transcript_task()

        if self.connection:
            try:
                await self.connection.close()
            except ConnectionClosedError as e:
                logger.debug(f"Connection already closed during shutdown: {e}")
            except Exception as e:
                logger.debug(f"connection.close() ignored: {e}")
            finally:
                self.connection = None

        # Clear any remaining items in the output queue
        while not self.output_queue.empty():
            try:
                self.output_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    async def get_available_voices(self) -> list[str]:
        """Return the available Hugging Face voices."""
        return get_available_voices()

    async def _build_realtime_client(self) -> AsyncOpenAI:
        """Build the Hugging Face OpenAI-compatible realtime client."""
        configured_bearer_token = (config.HF_TOKEN or "").strip()
        connection_selection = get_hf_connection_selection()
        direct_realtime_url = get_hf_direct_ws_url()
        if connection_selection.mode == HF_LOCAL_CONNECTION_MODE:
            if not direct_realtime_url:
                raise RuntimeError("HF_REALTIME_WS_URL must be set when HF_REALTIME_CONNECTION_MODE=local")
            client, connect_query = _build_openai_compatible_client_from_realtime_url(
                direct_realtime_url,
                configured_bearer_token,
            )
            self._realtime_connect_query = connect_query
            logger.info("Using direct Hugging Face realtime endpoint %s", direct_realtime_url)
            return client

        session_url = connection_selection.session_url
        if not session_url:
            raise RuntimeError("Built-in Hugging Face session proxy URL is unavailable")
        if direct_realtime_url:
            logger.info("HF_REALTIME_CONNECTION_MODE=deployed; ignoring HF_REALTIME_WS_URL.")

        bearer_token = configured_bearer_token or (get_token() or "").strip()
        allocator_headers = {"User-Agent": "reachy-mini-conversation-app"}
        if bearer_token:
            allocator_headers["X-Reachy-Mini-Authorization"] = f"Bearer {bearer_token}"
        allocator_payload: dict[str, str] = {}
        try:
            hardware_id = self.deps.reachy_mini.client.get_status(wait=False).hardware_id
        except (AssertionError, ConnectionError, TimeoutError) as e:
            logger.warning("Daemon status unavailable for realtime session allocation: %s", e)
        else:
            if hardware_id:
                allocator_payload["hardware_id"] = hardware_id

        async with httpx.AsyncClient(timeout=10.0) as http_client:
            response = await http_client.post(session_url, headers=allocator_headers, json=allocator_payload)
            response.raise_for_status()
            payload = response.json()

        connect_url = payload.get("connect_url")
        if not isinstance(connect_url, str) or not connect_url:
            raise RuntimeError(f"Session allocator response did not contain a valid connect_url: {payload!r}")

        parsed_connect_url = parse_hf_realtime_url(connect_url)
        if not parsed_connect_url.has_realtime_path:
            raise ValueError(f"Expected realtime connect URL ending with /realtime, got: {connect_url}")

        logger.info("Allocated realtime session %s", payload.get("session_id") or "<unknown>")
        client, connect_query = _build_openai_compatible_client_from_realtime_url(
            connect_url,
            bearer_token,
        )
        self._realtime_connect_query = connect_query
        return client
