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

from reachy_mini.vision.face_detector import Face
import reachy_companion.huggingface_realtime as hf_mod
from reachy_companion.faces import list_faces
from reachy_companion.face_id import ALIGNED_SIZE, FaceRecognizer, Identification
from reachy_companion.tools.core_tools import ToolDependencies
from reachy_companion.tools.who_is_this import WhoIsThis
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
        raises: Exception | None = None,
    ) -> None:
        self.identification = identification or Identification(status="no_face")
        self.enabled = enabled
        self._ready = ready
        self._record = record
        self._wait_delay_s = wait_delay_s
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
        return self.identification

    def enroll(self, frame_bgr: NDArray[np.uint8] | None, name: str) -> tuple[Any, Identification]:
        self.frames_seen += 1
        if self._raises is not None:
            raise self._raises
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


def _assert_carries_no_image(result: dict[str, Any]) -> None:
    """Assert a tool result contains no image payload of any kind."""
    assert "b64_im" not in result
    assert not any(isinstance(value, (bytes, bytearray)) for value in result.values())
    assert not any("image" in key or "frame" in key for key in result)


# --- who_is_this degradation ladder -----------------------------------------


@pytest.mark.asyncio
async def test_who_is_this_degrades_without_a_face() -> None:
    """Nobody in frame is a plain status, not an error and not a guess."""
    result = await WhoIsThis()(_deps(_FakeRecognizer(Identification(status="no_face"))))

    assert result == {"status": "no_face", "face_count": 0}
    _assert_carries_no_image(result)


@pytest.mark.asyncio
async def test_who_is_this_reports_too_far() -> None:
    """A face too small to embed honestly is reported as such, so Reachy can ask them closer."""
    result = await WhoIsThis()(_deps(_FakeRecognizer(Identification(status="too_far", face_count=1))))

    assert result == {"status": "too_far", "face_count": 1}


@pytest.mark.asyncio
async def test_who_is_this_reports_multiple_faces() -> None:
    """With two people in frame there is no single answer; say so instead of picking one."""
    result = await WhoIsThis()(_deps(_FakeRecognizer(Identification(status="multiple_faces", face_count=2))))

    assert result == {"status": "multiple_faces", "face_count": 2}


@pytest.mark.asyncio
async def test_who_is_this_reports_unknown_with_the_score() -> None:
    """A stranger is `unknown` with the best score, which is what makes thresholds tunable."""
    result = await WhoIsThis()(_deps(_FakeRecognizer(Identification(status="unknown", score=0.2134, face_count=1))))

    assert result == {"status": "unknown", "score": 0.213, "face_count": 1}
    assert "name" not in result


@pytest.mark.asyncio
async def test_who_is_this_reports_ambiguous_with_the_runner_up() -> None:
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

    assert result == {"status": "unavailable", "face_count": 0, "reason": "camera is disabled"}
    assert recognizer.frames_seen == 0


@pytest.mark.asyncio
async def test_who_is_this_is_unavailable_when_face_memory_is_disabled() -> None:
    """FACE_MEMORY_ENABLED=0 leaves the tool callable but permanently unavailable."""
    disabled = await WhoIsThis()(_deps(_FakeRecognizer(enabled=False)))
    absent = await WhoIsThis()(_deps(None))

    assert disabled == {"status": "unavailable", "face_count": 0, "reason": "face memory is disabled"}
    assert absent == disabled


@pytest.mark.asyncio
async def test_who_is_this_is_unavailable_when_the_model_is_not_ready() -> None:
    """A failed or still-loading model surfaces as `unavailable`, with the reason kept."""
    identification = Identification(status="unavailable", reason="model not ready")

    result = await WhoIsThis()(_deps(_FakeRecognizer(identification)))

    assert result == {"status": "unavailable", "face_count": 0, "reason": "model not ready"}


@pytest.mark.asyncio
async def test_who_is_this_is_unavailable_without_a_frame() -> None:
    """A camera that returns no frame is a status, not an exception."""
    result = await WhoIsThis()(_deps(_FakeRecognizer(), frame=None))

    assert result == {"status": "unavailable", "face_count": 0, "reason": "no frame available"}


@pytest.mark.asyncio
async def test_who_is_this_survives_a_recognizer_exception() -> None:
    """Even a broken recognizer must not take the turn down."""
    result = await WhoIsThis()(_deps(_FakeRecognizer(raises=RuntimeError("boom"))))

    assert result["status"] == "unavailable"
    assert "boom" in result["reason"]


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
    assert disabled["reason"] == "face memory is disabled"
    assert blind["reason"] == "camera is disabled"


# --- enrollment -> recognition round trip -----------------------------------


class _StubDetector:
    """Detector stand-in returning fixed faces, so the round trip needs no YuNet."""

    def __init__(self, faces: list[Face]) -> None:
        self.faces = faces

    def detect(self, frame_bgr: NDArray[np.uint8]) -> list[Face]:
        return self.faces


def _face() -> Face:
    """One face large enough to pass MIN_FACE_PX once scaled back to full resolution."""
    return Face(bbox=(10.0, 10.0, 60.0, 60.0), right_eye=(25.0, 30.0), left_eye=(45.0, 30.0), nose=(35.0, 40.0))


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
async def test_enrollment_then_recognition_round_trip(tmp_path: Path) -> None:
    """The feature in one test: remember a face, then recognize it on a later frame."""
    recognizer = _round_trip_recognizer(tmp_path)

    saved = await RememberFace()(
        _deps(recognizer, instance_path=tmp_path, frame=_frame(100)),
        name="小明",
    )
    recalled = await WhoIsThis()(_deps(recognizer, instance_path=tmp_path, frame=_frame(102)))

    assert saved == {"status": "saved", "name": "小明", "samples": 1}
    assert recalled["status"] == "recognized"
    assert recalled["name"] == "小明"
    records = list_faces(tmp_path)
    assert len(records) == 1
    assert len(records[0].embeddings) == 1
    _assert_carries_no_image(saved)
    _assert_carries_no_image(recalled)


@pytest.mark.asyncio
async def test_repeated_enrollment_ring_buffers_the_samples(tmp_path: Path) -> None:
    """Saying "remember me" three more times keeps three samples, not four records."""
    recognizer = _round_trip_recognizer(tmp_path)
    deps = _deps(recognizer, instance_path=tmp_path, frame=_frame(100))

    for _ in range(4):
        await RememberFace()(deps, name="小明")

    records = list_faces(tmp_path)
    assert len(records) == 1
    assert len(records[0].embeddings) == 3


@pytest.mark.asyncio
async def test_a_stranger_is_not_recognized(tmp_path: Path) -> None:
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
        Identification(status="unavailable", reason="model not ready"),
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
    assert seen == [False]
