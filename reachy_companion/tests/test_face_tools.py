"""Tool-surface tests: the degradation ladder, the enrollment round trip, and the wake hook.

Two invariants are asserted over and over here, because they are the feature's
promises: **no tool result ever carries image bytes**, and **the startup
greeting is never delayed or altered** unless a face was actually recognized.
"""

import time
import asyncio
from typing import Any
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest
from numpy.typing import NDArray

import reachy_companion.huggingface_realtime as hf_mod
from reachy_companion.faces import EMBEDDING_DIM, FaceRecord, list_faces
from reachy_companion.face_id import (
    ALIGNED_SIZE,
    IDENTIFICATION_REASONS,
    Face5,
    FaceRecognizer,
    Identification,
)
from reachy_companion.tools.core_tools import ToolDependencies
from reachy_companion.tools.who_is_this import WhoIsThis
from reachy_companion.tools.face_support import capture_frame
from reachy_companion.tools.remember_face import RememberFace
from reachy_companion.huggingface_realtime import HuggingFaceRealtimeHandler


GREETING = "用一句简短自然的中文主动问候用户。"


class _FakeRecognizer:
    """A FaceRecognizer stand-in with scripted answers and call recording."""

    def __init__(
        self,
        identification: Identification | None = None,
        *,
        enabled: bool = True,
        ready: bool = True,
        record: Any = None,
        wait_delay_s: float = 0.0,
        identify_delay_s: float = 0.0,
        results: list[Identification] | None = None,
        enroll_results: list[tuple[Any, Identification] | Exception] | None = None,
        raises: Exception | None = None,
    ) -> None:
        self.identification = identification or Identification(status="no_face")
        self.enabled = enabled
        self._ready = ready
        self._record = record
        self._wait_delay_s = wait_delay_s
        self._identify_delay_s = identify_delay_s
        # A scripted answer per round; the last one repeats once exhausted.
        self._results = list(results) if results else None
        # The same idiom for enrollment: one (record, identification) per sample.
        # An Exception in that list is raised on the sample it stands at, which
        # is how a failure on the *second* sample only can be scripted at all —
        # `raises` above is global and would take the first sample down with it.
        self._enroll_results = list(enroll_results) if enroll_results else None
        self._raises = raises
        self.frames_seen = 0

    def start_warmup(self) -> None:
        return None

    def wait_ready(self, timeout_s: float) -> bool:
        # Honour the kill switch exactly as the real class does, so a test that
        # forgets the `enabled` guard cannot pass against a more permissive fake.
        if not self.enabled:
            return False
        if self._wait_delay_s:
            time.sleep(self._wait_delay_s)
        return self._ready

    def identify(self, frame_bgr: NDArray[np.uint8] | None) -> Identification:
        self.frames_seen += 1
        if self._raises is not None:
            raise self._raises
        if self._identify_delay_s:
            time.sleep(self._identify_delay_s)
        if self._results:
            return self._results.pop(0) if len(self._results) > 1 else self._results[0]
        return self.identification

    def enroll(self, frame_bgr: NDArray[np.uint8] | None, name: str) -> tuple[Any, Identification]:
        self.frames_seen += 1
        if self._raises is not None:
            raise self._raises
        if self._enroll_results:
            scripted = self._enroll_results.pop(0) if len(self._enroll_results) > 1 else self._enroll_results[0]
            if isinstance(scripted, Exception):
                raise scripted
            return scripted
        return self._record, self.identification


def _frame(value: int = 100) -> NDArray[np.uint8]:
    """Return a small uniform BGR frame; the fakes never look at its content."""
    return np.full((72, 128, 3), value, dtype=np.uint8)


def _deps(
    recognizer: Any,
    *,
    camera_enabled: bool = True,
    frame: Any = ...,
    instance_path: Path | None = None,
) -> ToolDependencies:
    """Build ToolDependencies with a fake camera and the given recognizer.

    `frame` defaults to a valid frame; pass `None` explicitly for a blind camera.
    """
    reachy_mini = MagicMock()
    reachy_mini.media.get_frame.return_value = _frame() if frame is ... else frame
    return ToolDependencies(
        reachy_mini=reachy_mini,
        movement_manager=MagicMock(),
        instance_path=instance_path,
        camera_enabled=camera_enabled,
        face_recognizer=recognizer,
    )


def _stored_record(samples: int, name: str = "Lena") -> FaceRecord:
    """Return a stored record carrying `samples` embeddings, for stub enrollment.

    The tools report `samples` straight off the record, so only its length
    matters here — the vectors themselves are never compared.
    """
    vector = (0.0,) * EMBEDDING_DIM
    return FaceRecord(
        id="face-1",
        name=name,
        embeddings=tuple(vector for _ in range(samples)),
        created_at=0,
        updated_at=0,
    )


@pytest.fixture
def instant_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Collapse the retry pauses, so a retry test costs no wall-clock time.

    The pauses exist to let the scene change between looks; what the retry tests
    assert is the sequence of looks, never its tempo.
    """
    real_sleep = asyncio.sleep

    async def _instant(delay: float, result: Any = None) -> Any:
        return await real_sleep(0, result)

    monkeypatch.setattr(asyncio, "sleep", _instant)


def _assert_carries_no_image(result: dict[str, Any]) -> None:
    """Assert a tool result contains no image payload of any kind."""
    assert "b64_im" not in result
    assert not any(isinstance(value, (bytes, bytearray)) for value in result.values())
    assert not any("image" in key or "frame" in key for key in result)


def _assert_reason_is_a_stable_code(result: dict[str, Any]) -> None:
    """Assert `reason` is one of the published codes and leaks no exception text.

    Tool results are echoed verbatim to the cloud model, so a raw
    `"RuntimeError: <message>"` there would ship internal detail off-device.
    """
    reason = result.get("reason")
    if reason is None:
        return
    assert reason in IDENTIFICATION_REASONS, f"unstable reason: {reason!r}"


# --- who_is_this degradation ladder -----------------------------------------


@pytest.mark.asyncio
async def test_who_is_this_degrades_without_a_face(instant_sleep: None) -> None:
    """Nobody in frame is a plain status, not an error and not a guess."""
    result = await WhoIsThis()(_deps(_FakeRecognizer(Identification(status="no_face"))))

    assert result == {"status": "no_face", "face_count": 0}
    _assert_carries_no_image(result)


@pytest.mark.asyncio
async def test_who_is_this_reports_too_far(instant_sleep: None) -> None:
    """A face too small to embed honestly is reported as such, so Reachy can ask them closer."""
    result = await WhoIsThis()(_deps(_FakeRecognizer(Identification(status="too_far", face_count=1))))

    assert result == {"status": "too_far", "face_count": 1}


@pytest.mark.asyncio
async def test_who_is_this_scores_a_face_when_two_are_in_frame(instant_sleep: None, tmp_path: Path) -> None:
    """Two people in frame no longer refuses the question.

    `identify` scores the largest face — the SDK head tracker's rule — so the
    tool answers about that face and still reports the true count. Enrollment
    is the path that keeps refusing (see `remember_face` below).
    """
    recognizer = FaceRecognizer(tmp_path)
    recognizer._detector = _StubDetector([_face(), _face(width=30.0)])
    recognizer._loaded = True
    recognizer._load_done.set()
    recognizer.embed = _brightness_embedder  # type: ignore[method-assign]

    result = await WhoIsThis()(_deps(recognizer, instance_path=tmp_path, frame=_frame()))

    assert result["status"] == "unknown"
    assert result["face_count"] == 2
    assert "name" not in result
    _assert_carries_no_image(result)


@pytest.mark.asyncio
async def test_who_is_this_reports_unknown_with_the_score(instant_sleep: None) -> None:
    """A stranger is `unknown` with the best score, which is what makes thresholds tunable."""
    result = await WhoIsThis()(_deps(_FakeRecognizer(Identification(status="unknown", score=0.2134, face_count=1))))

    assert result == {"status": "unknown", "score": 0.213, "face_count": 1}
    assert "name" not in result


@pytest.mark.asyncio
async def test_who_is_this_reports_ambiguous_with_the_runner_up(instant_sleep: None) -> None:
    """A near-tie names both candidates as candidates, never one as a fact."""
    identification = Identification(status="ambiguous", name="A", runner_up="B", score=0.52, face_count=1)

    result = await WhoIsThis()(_deps(_FakeRecognizer(identification)))

    assert result == {"status": "ambiguous", "name": "A", "runner_up": "B", "score": 0.52, "face_count": 1}


@pytest.mark.asyncio
async def test_who_is_this_reports_a_recognized_person() -> None:
    """The happy path returns a name and a score — and still no pixels."""
    identification = Identification(status="recognized", name="小明", score=0.71, face_count=1)

    result = await WhoIsThis()(_deps(_FakeRecognizer(identification)))

    assert result["status"] == "recognized"
    assert result["name"] == "小明"
    assert result["score"] == 0.71
    _assert_carries_no_image(result)


@pytest.mark.asyncio
async def test_who_is_this_is_unavailable_when_the_camera_is_off() -> None:
    """`--no-camera` must not produce a traceback in the middle of a conversation."""
    recognizer = _FakeRecognizer(Identification(status="recognized", name="小明"))

    result = await WhoIsThis()(_deps(recognizer, camera_enabled=False))

    assert result == {"status": "unavailable", "face_count": 0, "reason": "camera_disabled"}
    _assert_reason_is_a_stable_code(result)
    assert recognizer.frames_seen == 0


@pytest.mark.asyncio
async def test_who_is_this_is_unavailable_when_face_memory_is_disabled() -> None:
    """FACE_MEMORY_ENABLED=0 leaves the tool callable but permanently unavailable."""
    disabled = await WhoIsThis()(_deps(_FakeRecognizer(enabled=False)))
    absent = await WhoIsThis()(_deps(None))

    assert disabled == {"status": "unavailable", "face_count": 0, "reason": "face_memory_disabled"}
    assert absent == disabled
    _assert_reason_is_a_stable_code(disabled)


@pytest.mark.asyncio
async def test_who_is_this_is_unavailable_when_the_model_is_not_ready(instant_sleep: None) -> None:
    """A failed or still-loading model surfaces as `unavailable`, with the reason kept."""
    identification = Identification(status="unavailable", reason="model_unavailable")

    result = await WhoIsThis()(_deps(_FakeRecognizer(identification)))

    assert result == {"status": "unavailable", "face_count": 0, "reason": "model_unavailable"}
    _assert_reason_is_a_stable_code(result)


@pytest.mark.asyncio
async def test_who_is_this_is_unavailable_without_a_frame(instant_sleep: None) -> None:
    """A camera that never yields a frame is a status, not an exception.

    `no_frame` is only reported once the retries are exhausted: three frame
    pulls inside each of the three looks, all of them empty.
    """
    deps = _deps(_FakeRecognizer(), frame=None)

    result = await WhoIsThis()(deps)

    assert result == {"status": "unavailable", "face_count": 0, "reason": "no_frame"}
    _assert_reason_is_a_stable_code(result)
    assert deps.reachy_mini.media.get_frame.call_count == 9


@pytest.mark.asyncio
async def test_who_is_this_survives_a_recognizer_exception(
    instant_sleep: None, caplog: pytest.LogCaptureFixture
) -> None:
    """Even a broken recognizer must not take the turn down — nor leak its message.

    Tool results go straight to the cloud model, so the exception text belongs in
    the local log and `reason` stays a stable code.
    """
    with caplog.at_level("ERROR", logger="reachy_companion.tools.face_support"):
        result = await WhoIsThis()(_deps(_FakeRecognizer(raises=RuntimeError("boom"))))

    assert result == {"status": "unavailable", "face_count": 0, "reason": "internal_error"}
    _assert_reason_is_a_stable_code(result)
    assert "boom" not in str(result)
    assert "boom" in caplog.text


@pytest.mark.asyncio
async def test_remember_face_survives_an_enrollment_exception(caplog: pytest.LogCaptureFixture) -> None:
    """The enrollment path sanitizes its failures exactly like who_is_this does."""
    with caplog.at_level("ERROR", logger="reachy_companion.tools.remember_face"):
        result = await RememberFace()(_deps(_FakeRecognizer(raises=RuntimeError("boom"))), name="小明")

    assert result == {"status": "unavailable", "face_count": 0, "reason": "internal_error"}
    assert "boom" not in str(result)
    assert "boom" in caplog.text


@pytest.mark.asyncio
async def test_a_camera_failure_is_reported_without_its_exception_text(
    instant_sleep: None, caplog: pytest.LogCaptureFixture
) -> None:
    """A raising `get_frame()` is a stable code too, not a transport traceback."""
    deps = _deps(_FakeRecognizer())
    deps.reachy_mini.media.get_frame.side_effect = OSError("v4l2 device fell over")

    with caplog.at_level("ERROR", logger="reachy_companion.tools.face_support"):
        result = await WhoIsThis()(deps)

    assert result == {"status": "unavailable", "face_count": 0, "reason": "internal_error"}
    assert "v4l2" not in str(result)
    assert "v4l2" in caplog.text


# --- remember_face ----------------------------------------------------------


@pytest.mark.asyncio
async def test_remember_face_requires_a_name() -> None:
    """An empty name enrolls nobody and touches no store."""
    recognizer = _FakeRecognizer()

    result = await RememberFace()(_deps(recognizer), name="   ")

    assert result == {"error": "name must be a non-empty string"}
    assert recognizer.frames_seen == 0


@pytest.mark.asyncio
async def test_remember_face_refuses_two_faces_and_stores_nothing(tmp_path: Path) -> None:
    """Enrollment must be unambiguous: with two people in frame, store nobody."""
    recognizer = FaceRecognizer(tmp_path)
    recognizer._detector = _StubDetector([_face(), _face()])
    recognizer._loaded = True
    recognizer._load_done.set()

    result = await RememberFace()(_deps(recognizer, instance_path=tmp_path, frame=_frame()), name="小明")

    assert result == {"status": "multiple_faces", "face_count": 2}
    assert list_faces(tmp_path) == []


@pytest.mark.asyncio
async def test_remember_face_is_unavailable_when_disabled_or_blind() -> None:
    """The kill switch and a disabled camera both refuse enrollment the same way."""
    disabled = await RememberFace()(_deps(_FakeRecognizer(enabled=False)), name="小明")
    blind = await RememberFace()(_deps(_FakeRecognizer(), camera_enabled=False), name="小明")

    assert disabled["status"] == "unavailable"
    assert disabled["reason"] == "face_memory_disabled"
    assert blind["reason"] == "camera_disabled"


# --- retries: dropped frames, extra looks, extra samples ---------------------


@pytest.mark.asyncio
async def test_capture_frame_retries_none_frames(instant_sleep: None) -> None:
    """Two 20 ms appsink misses then a real frame must yield the frame, not `no_frame`.

    The camera is drop=True/max-buffers=1 with a 20 ms pull, so on a loaded CM4 a
    `None` is routine timing, not a broken camera.
    """
    frame = _frame()
    deps = _deps(_FakeRecognizer())
    deps.reachy_mini.media.get_frame.side_effect = [None, None, frame]

    captured, refusal = await capture_frame(deps)

    assert refusal is None
    assert captured is frame


@pytest.mark.asyncio
async def test_who_is_this_retries_to_a_recognition(instant_sleep: None) -> None:
    """Round one sees nobody, round two recognizes: the tool must answer recognized."""
    recognizer = _FakeRecognizer(
        results=[
            Identification(status="no_face"),
            Identification(status="recognized", name="Lena", score=0.59, face_count=1),
        ]
    )

    result = await WhoIsThis()(_deps(recognizer))

    assert result["status"] == "recognized"
    assert result["name"] == "Lena"
    # The first hit ends the loop; a third look would only risk losing it.
    assert recognizer.frames_seen == 2


@pytest.mark.asyncio
async def test_who_is_this_reports_the_best_informative_miss(instant_sleep: None) -> None:
    """Rounds [no_face, unknown(0.21), no_face]: the answer is the scored unknown.

    A scored miss is evidence — the model can say "I see you but I do not know
    you", and the log carries a number to tune the threshold with. `no_face`
    carries neither, so the last look must not overwrite the useful one.
    """
    recognizer = _FakeRecognizer(
        results=[
            Identification(status="no_face"),
            Identification(status="unknown", score=0.21, face_count=1),
            Identification(status="no_face"),
        ]
    )

    result = await WhoIsThis()(_deps(recognizer))

    assert result["status"] == "unknown"
    assert result["score"] == 0.21
    assert recognizer.frames_seen == 3
    _assert_carries_no_image(result)


@pytest.mark.asyncio
async def test_who_is_this_prefers_the_last_informative_look(instant_sleep: None) -> None:
    """Rounds [unknown(0.2), too_far]: the freshest scored look is the answer, not the first.

    Two *differing* informative statuses is what pins the ordering: the person
    stepped back between looks, so `too_far` is the true state of the scene and
    Reachy can ask them closer. Keeping the first look would answer about a
    moment that has passed.
    """
    recognizer = _FakeRecognizer(
        results=[
            Identification(status="unknown", score=0.2, face_count=1),
            Identification(status="too_far", face_count=1),
        ]
    )

    result = await WhoIsThis()(_deps(recognizer))

    assert result == {"status": "too_far", "face_count": 1}
    assert recognizer.frames_seen == 3


@pytest.mark.asyncio
async def test_remember_face_stores_multiple_samples(instant_sleep: None) -> None:
    """One call takes up to three samples; a sample that misses ends the burst, not the call.

    Three embeddings of one face — a blink, a turn, a different shadow — is what
    makes the later recognition survive the same variation.
    """
    recognizer = _FakeRecognizer(
        enroll_results=[
            (_stored_record(1), Identification(status="unknown", face_count=1)),
            (_stored_record(2), Identification(status="unknown", score=0.4, face_count=1)),
            (None, Identification(status="no_face")),
        ]
    )

    result = await RememberFace()(_deps(recognizer), name="Lena")

    assert result == {"status": "saved", "name": "Lena", "samples": 2}
    assert recognizer.frames_seen == 3
    _assert_carries_no_image(result)


@pytest.mark.asyncio
async def test_remember_face_keeps_the_first_sample_when_an_extra_frame_is_missed(
    instant_sleep: None,
) -> None:
    """A dropped frame on an extra sample ends the burst, not the call.

    The person is already remembered by then; failing the whole enrollment over
    a 20 ms camera miss would be the worst possible answer.
    """
    recognizer = _FakeRecognizer(
        record=_stored_record(1),
        identification=Identification(status="unknown", face_count=1),
    )
    deps = _deps(recognizer)
    deps.reachy_mini.media.get_frame.side_effect = [_frame(), None]

    result = await RememberFace()(deps, name="Lena")

    assert result == {"status": "saved", "name": "Lena", "samples": 1}
    # One enrollment, and the burst stopped at the missed frame rather than
    # retrying it: the extra samples are a single pull each.
    assert recognizer.frames_seen == 1
    assert deps.reachy_mini.media.get_frame.call_count == 2


@pytest.mark.asyncio
async def test_remember_face_keeps_the_first_sample_when_an_extra_enroll_raises(
    instant_sleep: None, caplog: pytest.LogCaptureFixture
) -> None:
    """An exception on an extra sample is logged and swallowed — the save still stands."""
    recognizer = _FakeRecognizer(
        enroll_results=[
            (_stored_record(1), Identification(status="unknown", face_count=1)),
            RuntimeError("boom"),
        ]
    )

    with caplog.at_level("WARNING", logger="reachy_companion.tools.remember_face"):
        result = await RememberFace()(_deps(recognizer), name="Lena")

    assert result == {"status": "saved", "name": "Lena", "samples": 1}
    assert recognizer.frames_seen == 2
    assert "boom" not in str(result)
    assert "boom" in caplog.text


# --- enrollment -> recognition round trip -----------------------------------


class _StubDetector:
    """Detector stand-in returning fixed faces, so the round trip needs no YuNet."""

    def __init__(self, faces: list[Face5]) -> None:
        self.faces = faces

    def detect(self, frame_bgr: NDArray[np.uint8]) -> list[Face5]:
        return self.faces


def _face(width: float = 60.0) -> Face5:
    """One face `width` px wide as detected; the default passes MIN_FACE_PX at full resolution.

    Landmarks keep their proportions, so a narrower face is the same face,
    smaller — which is what makes a largest-of-two frame meaningful.
    """
    scale = width / 60.0

    def at(dx: float, dy: float) -> tuple[float, float]:
        return (10.0 + dx * scale, 10.0 + dy * scale)

    return Face5(
        bbox=(10.0, 10.0, width, width),
        right_eye=at(15.0, 20.0),
        left_eye=at(35.0, 20.0),
        nose=at(25.0, 30.0),
        right_mouth=at(18.0, 40.0),
        left_mouth=at(32.0, 40.0),
    )


def _brightness_embedder(aligned: NDArray[np.uint8]) -> NDArray[np.float32]:
    """Return a deterministic stand-in for SFace: mean brightness mapped onto a unit circle.

    Similar frames land at similar angles (high cosine); a very different frame
    lands far away — enough to exercise both the recognize and the unknown path
    without a 37 MB model in the test suite.
    """
    assert aligned.shape == (ALIGNED_SIZE, ALIGNED_SIZE, 3)
    angle = float(aligned.mean()) / 255.0 * np.pi
    vector = np.zeros(128, dtype=np.float32)
    vector[0] = np.cos(angle)
    vector[1] = np.sin(angle)
    return vector


def _round_trip_recognizer(tmp_path: Path) -> FaceRecognizer:
    """Return a real recognizer with a stub detector and the deterministic embedder."""
    recognizer = FaceRecognizer(tmp_path)
    recognizer._detector = _StubDetector([_face()])
    recognizer._loaded = True
    recognizer._load_done.set()
    recognizer.embed = _brightness_embedder  # type: ignore[method-assign]
    return recognizer


@pytest.mark.asyncio
async def test_enrollment_then_recognition_round_trip(instant_sleep: None, tmp_path: Path) -> None:
    """The feature in one test: remember a face, then recognize it on a later frame.

    One call is three samples now, so the store holds three embeddings of the
    one person — still one record, still one name.
    """
    recognizer = _round_trip_recognizer(tmp_path)

    saved = await RememberFace()(
        _deps(recognizer, instance_path=tmp_path, frame=_frame(100)),
        name="小明",
    )
    recalled = await WhoIsThis()(_deps(recognizer, instance_path=tmp_path, frame=_frame(102)))

    assert saved == {"status": "saved", "name": "小明", "samples": 3}
    assert recalled["status"] == "recognized"
    assert recalled["name"] == "小明"
    records = list_faces(tmp_path)
    assert len(records) == 1
    assert len(records[0].embeddings) == 3
    _assert_carries_no_image(saved)
    _assert_carries_no_image(recalled)


@pytest.mark.asyncio
async def test_repeated_enrollment_ring_buffers_the_samples(instant_sleep: None, tmp_path: Path) -> None:
    """Saying "remember me" three more times keeps three samples, not four records."""
    recognizer = _round_trip_recognizer(tmp_path)
    deps = _deps(recognizer, instance_path=tmp_path, frame=_frame(100))

    for _ in range(4):
        await RememberFace()(deps, name="小明")

    records = list_faces(tmp_path)
    assert len(records) == 1
    assert len(records[0].embeddings) == 3


@pytest.mark.asyncio
async def test_a_stranger_is_not_recognized(instant_sleep: None, tmp_path: Path) -> None:
    """A face unlike anything enrolled is `unknown` — Reachy says so instead of guessing."""
    recognizer = _round_trip_recognizer(tmp_path)
    await RememberFace()(_deps(recognizer, instance_path=tmp_path, frame=_frame(20)), name="小明")

    result = await WhoIsThis()(_deps(recognizer, instance_path=tmp_path, frame=_frame(230)))

    assert result["status"] == "unknown"
    assert "name" not in result


# --- wake-time greeting hook ------------------------------------------------


class _CapturingItem:
    """Records every conversation item the handler creates."""

    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    async def create(self, item: dict[str, Any]) -> None:
        self.items.append(item)


class _CapturingConnection:
    """A minimal realtime connection exposing only `conversation.item.create`."""

    def __init__(self) -> None:
        self.item = _CapturingItem()

    @property
    def conversation(self) -> "_CapturingConnection":
        return self


def _handler(recognizer: Any, monkeypatch: pytest.MonkeyPatch, *, camera_enabled: bool = True) -> Any:
    """Build a handler wired to a capturing connection and a fixed greeting prompt."""
    monkeypatch.setattr(hf_mod, "get_session_greeting_prompt", lambda: GREETING)
    handler = HuggingFaceRealtimeHandler(_deps(recognizer, camera_enabled=camera_enabled))
    handler.connection = _CapturingConnection()
    handler.instance_path = None
    monkeypatch.setattr(handler, "_safe_response_create", AsyncMock())
    return handler


def _sent_text(handler: Any) -> str:
    """Return the text of the single conversation item the handler sent."""
    (item,) = handler.connection.item.items
    return str(item["content"][0]["text"])


@pytest.mark.asyncio
async def test_greeting_prefixes_a_recognized_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """The feature's payoff: the very first sentence of a session uses your name."""
    recognizer = _FakeRecognizer(Identification(status="recognized", name="小明", score=0.7, face_count=1))
    handler = _handler(recognizer, monkeypatch)

    await handler._send_startup_greeting_prompt()

    text = _sent_text(handler)
    assert "小明" in text
    assert text.endswith(GREETING)
    assert text != GREETING
    assert recognizer.frames_seen == 1


@pytest.mark.parametrize(
    "identification",
    [
        Identification(status="unknown", score=0.1, face_count=1),
        Identification(status="ambiguous", name="A", runner_up="B", score=0.5, face_count=1),
        Identification(status="no_face"),
        Identification(status="unavailable", reason="model_unavailable"),
    ],
)
@pytest.mark.asyncio
async def test_greeting_is_untouched_unless_someone_is_recognized(
    identification: Identification, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Anything short of a confident recognition sends the greeting verbatim."""
    handler = _handler(_FakeRecognizer(identification), monkeypatch)

    await handler._send_startup_greeting_prompt()

    assert _sent_text(handler) == GREETING


@pytest.mark.asyncio
async def test_greeting_is_untouched_when_the_recognizer_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """A crashing recognizer must never cost the user their greeting."""
    handler = _handler(_FakeRecognizer(raises=RuntimeError("boom")), monkeypatch)

    await handler._send_startup_greeting_prompt()

    assert _sent_text(handler) == GREETING


@pytest.mark.asyncio
async def test_greeting_is_untouched_when_auto_greet_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """FACE_AUTO_GREET=0 keeps the tools but drops the wake-time check entirely."""
    monkeypatch.setenv("FACE_AUTO_GREET", "0")
    recognizer = _FakeRecognizer(Identification(status="recognized", name="小明", face_count=1))
    handler = _handler(recognizer, monkeypatch)

    await handler._send_startup_greeting_prompt()

    assert _sent_text(handler) == GREETING
    assert recognizer.frames_seen == 0


@pytest.mark.asyncio
async def test_greeting_skips_recognition_when_the_camera_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """No camera means no frame source; the hook must not even try."""
    recognizer = _FakeRecognizer(Identification(status="recognized", name="小明", face_count=1))
    handler = _handler(recognizer, monkeypatch, camera_enabled=False)

    await handler._send_startup_greeting_prompt()

    assert _sent_text(handler) == GREETING
    assert recognizer.frames_seen == 0


@pytest.mark.asyncio
async def test_greeting_skips_recognition_without_a_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    """`get_frame()` returning None skips recognition; the greeting is unchanged."""
    recognizer = _FakeRecognizer(Identification(status="recognized", name="小明", face_count=1))
    handler = _handler(recognizer, monkeypatch)
    handler.deps.reachy_mini.media.get_frame.return_value = None

    await handler._send_startup_greeting_prompt()

    assert _sent_text(handler) == GREETING
    assert recognizer.frames_seen == 0


@pytest.mark.asyncio
async def test_greeting_is_not_delayed_past_the_wake_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """One monotonic deadline covers readiness + capture + identification, together.

    The hook runs before the session's event loop starts processing
    (`huggingface_realtime.py:740`), so a slow model load must cost the budget
    and nothing more.
    """
    monkeypatch.setenv("FACE_WAKE_BUDGET_MS", "300")
    recognizer = _FakeRecognizer(
        Identification(status="recognized", name="小明", face_count=1),
        wait_delay_s=3.0,
    )
    handler = _handler(recognizer, monkeypatch)

    started = time.monotonic()
    await handler._send_startup_greeting_prompt()
    elapsed_ms = (time.monotonic() - started) * 1000.0

    assert _sent_text(handler) == GREETING
    # 300 ms budget + slack. Loose enough for a busy CI box, tight enough that
    # the 3 s sleep could never hide inside it.
    assert elapsed_ms < 500.0


@pytest.mark.asyncio
async def test_wake_check_takes_a_second_look_and_the_first_hit_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    """A miss on frame one is not an answer: a later look inside the budget still greets by name.

    This is D-015's whole point — at a dozen enrolled people, a blink, a turned
    head or a shadow costs far more recognitions than any model change buys back.
    """
    recognizer = _FakeRecognizer(
        results=[
            Identification(status="no_face"),
            Identification(status="recognized", name="小明", score=0.51, face_count=1),
            Identification(status="unknown", score=0.1, face_count=1),
        ]
    )
    handler = _handler(recognizer, monkeypatch)

    await handler._send_startup_greeting_prompt()

    text = _sent_text(handler)
    assert "小明" in text
    assert text.endswith(GREETING)
    # Stops the moment somebody is recognized: the third look never happens.
    assert recognizer.frames_seen == 2


@pytest.mark.asyncio
async def test_wake_check_stops_after_the_attempt_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nobody recognized in any round leaves the greeting exactly as it was, after N looks."""
    recognizer = _FakeRecognizer(Identification(status="unknown", score=0.2, face_count=1))
    handler = _handler(recognizer, monkeypatch)

    await handler._send_startup_greeting_prompt()

    assert _sent_text(handler) == GREETING
    assert recognizer.frames_seen == 3


@pytest.mark.parametrize(("raw", "expected"), [("1", 1), ("5", 5), ("0", 1), ("77", 5), ("nonsense", 3)])
@pytest.mark.asyncio
async def test_wake_attempt_count_is_env_tunable_and_clamped(
    raw: str, expected: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The retry count is tunable on the robot, and never outside [1, 5]."""
    monkeypatch.setenv("FACE_WAKE_ATTEMPTS", raw)
    monkeypatch.setenv("FACE_WAKE_BUDGET_MS", "10000")  # the deadline must not decide this test
    recognizer = _FakeRecognizer(Identification(status="no_face"))
    handler = _handler(recognizer, monkeypatch)

    await handler._send_startup_greeting_prompt()

    assert recognizer.frames_seen == expected
    assert _sent_text(handler) == GREETING


@pytest.mark.asyncio
async def test_wake_check_rounds_share_one_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retries live *inside* the existing budget; they can never extend it.

    Three 120 ms looks plus their pauses would need ~700 ms. The 400 ms budget
    must cut the sequence short and still send the greeting on time.
    """
    monkeypatch.setenv("FACE_WAKE_BUDGET_MS", "400")
    recognizer = _FakeRecognizer(Identification(status="no_face"), identify_delay_s=0.12)
    handler = _handler(recognizer, monkeypatch)

    started = time.monotonic()
    await handler._send_startup_greeting_prompt()
    elapsed_ms = (time.monotonic() - started) * 1000.0

    assert _sent_text(handler) == GREETING
    assert 0 < recognizer.frames_seen < 3
    assert elapsed_ms < 600.0


@pytest.mark.asyncio
async def test_greeting_hook_is_a_no_op_for_a_disabled_recognizer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The kill switch composed with the wake hook, against the REAL recognizer.

    Not a fake: `FaceRecognizer(enabled=False)` is what `main.py` constructs
    under `FACE_MEMORY_ENABLED=0`, and the promise is that it loads no model and
    burns none of the wake budget. The log line must say so, rather than
    misattributing the skip to a budget that was never started.
    """
    recognizer = FaceRecognizer(tmp_path, enabled=False)
    handler = _handler(recognizer, monkeypatch)

    with caplog.at_level("INFO", logger="reachy_companion.huggingface_realtime"):
        started = time.monotonic()
        await handler._send_startup_greeting_prompt()
        elapsed_ms = (time.monotonic() - started) * 1000.0

    assert _sent_text(handler) == GREETING
    assert elapsed_ms < 100.0
    assert recognizer._sface is None  # no model was ever built
    assert "Face memory is disabled" in caplog.text


@pytest.mark.asyncio
async def test_greeting_is_sent_when_no_recognizer_is_wired(monkeypatch: pytest.MonkeyPatch) -> None:
    """Runtimes without face memory (tests, older deployments) behave exactly as before."""
    handler = _handler(None, monkeypatch)

    await handler._send_startup_greeting_prompt()

    assert _sent_text(handler) == GREETING


@pytest.mark.asyncio
async def test_greeting_is_sent_only_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """The recognition hook must not re-arm the greeting; the second call is a no-op."""
    handler = _handler(_FakeRecognizer(Identification(status="no_face")), monkeypatch)

    await handler._send_startup_greeting_prompt()
    await handler._send_startup_greeting_prompt()

    assert len(handler.connection.item.items) == 1


@pytest.mark.asyncio
async def test_greeting_hook_runs_recognition_off_the_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """CPU work belongs on a worker thread; the realtime loop must stay responsive."""
    loop_thread = asyncio.get_running_loop()
    seen: list[bool] = []

    class _ThreadCheckingRecognizer(_FakeRecognizer):
        def identify(self, frame_bgr: NDArray[np.uint8] | None) -> Identification:
            import threading

            seen.append(threading.current_thread() is threading.main_thread())
            return Identification(status="no_face")

    handler = _handler(_ThreadCheckingRecognizer(), monkeypatch)

    await handler._send_startup_greeting_prompt()

    assert loop_thread is asyncio.get_running_loop()
    # One entry per round since D-015; every one of them off the event loop.
    assert seen and not any(seen)


# --- tool-description routing ------------------------------------------------


def test_identity_routing_clauses_pin_camera_vs_face_tools() -> None:
    """D-013 routing fix: camera must disclaim identity; who_is_this must claim it.

    The 2026-08-24 party session proved the model answers 「是誰」 with `camera`.
    These clauses are the machine-visible contract that prevents that; if a
    rewrite drops them, this test is the tripwire.
    """
    from reachy_companion.tools.camera import Camera
    from reachy_companion.tools.who_is_this import WhoIsThis
    from reachy_companion.tools.remember_face import RememberFace

    camera = Camera.description
    who = WhoIsThis.description
    remember = RememberFace.description

    assert "who_is_this" in camera  # camera redirects identity asks
    assert "NEVER" in camera  # ...and does so emphatically
    assert "instead of the camera tool" in who
    assert "not the camera tool" in remember
