# Face Recognition Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Reachy actually recognize enrolled people in real use — by routing identity questions to the face tools, giving recognition more than one 1.2-second chance per boot, and making single-frame flakiness survivable.

**Architecture:** The SFace/YuNet recognizer core is proven healthy on-robot (2026-08-27: same-session recognition 0.594 vs threshold 0.363; cross-person 0.145 correctly rejected). Every change here is glue around it: prompt/description routing, retry loops in our own tool layer, an extended post-greeting wake window in `huggingface_realtime.py`, and store hygiene. No new models, no new dependencies, no changes to detection or embedding math.

**Tech Stack:** Python 3.12, numpy, onnxruntime (existing), pytest, ruff, mypy --strict. Robot deploy via `.claude/skills/reachy-deploy`.

**Spec:** The RCA in this session (2026-08-27), summarized in **Background** below. PRD §8 demo 2/5 context: `docs/PRD.md`. Decisions: D-013, D-015 in `DECISIONS.md`.

## Global Constraints

- Reuse-first (CLAUDE.md): never recreate face tracking/camera access. The one behavior adapted from official code is largest-face selection — the SDK's own rule, `face = max(faces, key=_area)` in `reachy_mini/vision/face_tracking.py:97-119` (installed SDK, pinned).
- No new/upgraded dependencies. No cv2 anywhere in the app (`test_face_id.py` asserts this).
- `Identification.reason` is a **closed 7-member Literal** (D-014): `face_memory_disabled`, `camera_disabled`, `no_frame`, `unsupported_frame`, `model_unavailable`, `invalid_name`, `internal_error`. Do not add members.
- Privacy invariants (D-013): no image persisted or transmitted; recognition is never a continuous scan — the extended wake window is **bounded** and ends.
- Gate: full suite green (baseline 1319 passed / 31 skipped), `ruff check`, `mypy --strict` clean, on Python 3.12 (`reachy_companion/.venv`).
- Run tests from `reachy_companion/`: `.venv/bin/python -m pytest tests/<file> -q`.
- Commit style: existing repo convention, e.g. `fix(face): …` / `feat(face): …`, one commit per task.

## Background (RCA summary — why each task exists)

Live evidence, robot journal 2026-08-24 → 2026-08-27:

1. **Mis-routing (Task 1):** asked 「是誰。」, the model called `camera`, never `who_is_this` (party session transcript 2026-08-24). `camera`'s description claims "check their appearance / how they look"; persona says 「詢問眼前的人…先用 camera 看」. The face tools lost the routing contest every time until 2026-08-27.
2. **Wake check 0/14 (Tasks 3, 5):** every boot since Aug 24 logged `Wake face check … greeting unchanged`. Causes: person not in frame at the single boot moment (8× `no_face`), `multiple_faces` hard refusal (2 boots), 1200 ms budget fits only 1–2 of 3 rounds on the CM4 (234–730 ms/round), one `None` frame aborts the whole check.
3. **Single-frame flakiness (Task 4):** `who_is_this` recognized Lena at 12:18:49 (0.594) then returned `no_face` at 12:19:07 with the person still present. One frame, no retry; enrollment stores one sample (`samples=1`).
4. **Margin bug (Task 2):** with ≥2 people enrolled (true since today), a runner-up **below** threshold within 0.05 of the best forces `ambiguous`.
5. **Silent store states (Task 6):** store was empty Aug 19–26 (`score=None` wake lines) and nothing ever logs how many people are loaded; no alignment-version marker guards against a future D-015-style invalidation; a malformed-embedding `ValueError` is mislabeled `invalid_name`.
6. **Preload drift (Task 7):** `scripts/preload_assets.py` downloads YuNet without the SDK's pinned `revision`, warming the wrong cache entry.
7. **No verification row (Task 8):** face memory has zero rows in `feature_list.json` — it escaped the project's own gate.

---

### Task 1: Route identity questions to the face tools

**Files:**
- Modify: `reachy_companion/src/reachy_companion/tools/camera.py:15-22`
- Modify: `reachy_companion/src/reachy_companion/tools/who_is_this.py:16-22`
- Modify: `reachy_companion/src/reachy_companion/tools/remember_face.py:16-21`
- Modify: `reachy_companion/profiles/_reachy_companion_locked_profile/profile.md` (the `remember_face` / `who_is_this` guidance bullets, ~lines 61-63)
- Modify: `persona.md` (repo root; `### camera` and `### who_is_this` sections, lines 47-49 and 64-70)
- Test: `reachy_companion/tests/test_face_tools.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: no code interfaces; description strings only.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_face_tools.py`:

```python
def test_identity_routing_clauses_pin_camera_vs_face_tools() -> None:
    """D-013 routing fix: camera must disclaim identity; who_is_this must claim it.

    The 2026-08-24 party session proved the model answers 「是誰」 with `camera`.
    These clauses are the machine-visible contract that prevents that; if a
    rewrite drops them, this test is the tripwire.
    """
    from reachy_companion.tools.camera import Camera
    from reachy_companion.tools.remember_face import RememberFace
    from reachy_companion.tools.who_is_this import WhoIsThis

    camera = Camera.description
    who = WhoIsThis.description
    remember = RememberFace.description

    assert "who_is_this" in camera          # camera redirects identity asks
    assert "NEVER" in camera                # ...and does so emphatically
    assert "instead of the camera tool" in who
    assert "not the camera tool" in remember
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd reachy_companion && .venv/bin/python -m pytest tests/test_face_tools.py::test_identity_routing_clauses_pin_camera_vs_face_tools -q`
Expected: FAIL (assertions on current descriptions).

- [ ] **Step 3: Update the three descriptions**

`camera.py` — replace the `description` value with:

```python
    description = (
        "Take a picture with the camera to see what is in front of the robot. "
        "Use this when the user asks you to look at something, see what they are holding, "
        "describe the scene, their outfit, or comment on how they look. "
        "Also use it when the user asks what you can see or wants your visual opinion. "
        "NEVER use this tool to identify WHO a person is, whether you know or remember them, "
        "or to recall someone's name — that is what the who_is_this tool is for. "
        "The camera is live, each call captures the current moment. "
        "If the user asks you to look without saying at what, do not ask for clarification, call this tool and describe what you see. "
    )
```

`who_is_this.py` — replace the `description` value with:

```python
    description = (
        "Look at the person in front of the camera and check whether you recognize them from face memory. "
        "Always use this tool — instead of the camera tool — whenever the question is about a person's "
        'IDENTITY: who someone is, "do you know me", "do you remember me", "what is my name", or who just '
        "arrived. Returns a status only: recognized (with the remembered name), unknown, ambiguous, no_face, "
        "too_far, multiple_faces or unavailable. It never returns a picture. If the status is not recognized, "
        "say plainly that you do not recognize them — never guess a name."
    )
```

`remember_face.py` — replace the `description` value with:

```python
    description = (
        "Remember what the person in front of the camera looks like, under the name they gave you. "
        "Use this tool — not the camera tool — when the user asks you to remember them, their face, or "
        'what they look like ("remember me", "I am X, remember my face"). '
        "Only the name and a numeric face signature are stored — never a picture. Requires exactly one person in "
        "frame: with nobody or several people visible it refuses, and you should ask them to face you alone."
    )
```

- [ ] **Step 4: Update the prompt guidance (profile + persona)**

`profile.md` — replace the two face bullets with:

```
- 当用户说"记住我"、"我叫X，记住我的样子"时，用 remember_face 工具记录他的名字和长相，不要用 camera。
- 只要问题是关于"这个人是谁"——"我是谁"、"你认得我吗"、"你还记得我吗"、"我叫什么名字"、有人新走进来想知道是谁——一律用 who_is_this 工具，不要用 camera；认不出就坦率说认不出，不要猜。
```

`persona.md` — replace the `### camera` section body with:

```
使用者詢問眼前的物品或環境、想知道你看到什麼時，**先用 `camera` 看，再回答**。
但如果問題是「這個人是誰」——包括「我是誰」「你認得我嗎」「你記得我嗎」——**絕對不要用 `camera`，改用 `who_is_this`**。
```

and the `### who_is_this` section body with:

```
只要問題涉及人的身分——「我是誰」「你還認得我嗎」「你記得我叫什麼嗎」、有人走進來想被認出——**一律用 `who_is_this`，不要用 `camera`**。
辨識不到就坦白說不知道，絕對不要猜。
```

(persona.md is the live instruction body on the robot per D-016 — the profile body is overridden by it, so both must carry the rule.)

- [ ] **Step 5: Run the test and the face-tool suite**

Run: `cd reachy_companion && .venv/bin/python -m pytest tests/test_face_tools.py -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add reachy_companion/src/reachy_companion/tools/camera.py reachy_companion/src/reachy_companion/tools/who_is_this.py reachy_companion/src/reachy_companion/tools/remember_face.py reachy_companion/profiles/_reachy_companion_locked_profile/profile.md persona.md reachy_companion/tests/test_face_tools.py
git commit -m "fix(face): route identity questions to who_is_this, never camera"
```

---

### Task 2: Margin rule only among candidates at or above threshold

**Files:**
- Modify: `reachy_companion/src/reachy_companion/face_id.py:519-532` (`FaceRecognizer.match`)
- Test: `reachy_companion/tests/test_face_id.py`

**Interfaces:**
- Consumes: existing `MatchResult`, `cosine`, `list_faces`.
- Produces: `match()` semantics — `ambiguous` only when the runner-up **also** clears `self.threshold`. Signature unchanged.

- [ ] **Step 1: Write the failing test**

Follow the existing direct-embedding match tests around `tests/test_face_id.py:422-431` (same fixtures/store setup style):

```python
def test_match_ignores_sub_threshold_runner_up(tmp_path: Path) -> None:
    """A stranger scoring below threshold must not drag a real match to ambiguous.

    best=0.38 (>= 0.363), runner-up=0.35 (< 0.363), gap 0.03 < margin 0.05:
    before the fix this returned `ambiguous`; the correct answer is `recognized`.
    """
    recognizer = _make_recognizer(tmp_path)  # reuse the module's existing helper/fixture
    probe = _unit_vector(0)                  # reuse the module's embedding helpers
    save_face(tmp_path, "Alice", _vector_with_cosine(probe, 0.38))
    save_face(tmp_path, "Bob", _vector_with_cosine(probe, 0.35))

    result = recognizer.match(probe)

    assert result.status == "recognized"
    assert result.name == "Alice"
```

(The helper names above must be replaced with this test module's actual existing fixtures — read the neighboring match tests first and reuse their store-seeding helpers verbatim. If no `_vector_with_cosine`-style helper exists, construct two 128-d unit vectors whose cosines against the probe are 0.38 and 0.35 by mixing the probe with an orthogonal unit vector: `v = cos * probe + sqrt(1 - cos**2) * ortho`.)

Also add the guard case that must keep working:

```python
def test_match_still_ambiguous_when_both_clear_threshold(tmp_path: Path) -> None:
    """best=0.40 vs runner-up=0.38: both >= threshold, gap < margin -> ambiguous."""
```

with the same construction (cosines 0.40 and 0.38) asserting `status == "ambiguous"` and `runner_up == "Bob"`.

- [ ] **Step 2: Run tests to verify the first fails**

Run: `cd reachy_companion && .venv/bin/python -m pytest tests/test_face_id.py -q -k "runner_up or ambiguous"`
Expected: new sub-threshold test FAILS (`ambiguous != recognized`); the both-clear test passes already.

- [ ] **Step 3: Fix `match()`**

Replace lines 519-532 with:

```python
        scored.sort(key=lambda item: item[0], reverse=True)
        best_score, best_name = scored[0]

        if best_score < self.threshold:
            return MatchResult(status="unknown", score=best_score, runner_up=None)

        # The margin compares candidates, not noise: only a runner-up that
        # itself clears the threshold can make the answer ambiguous. A stranger
        # scoring 0.35 must not stop us from naming the person scoring 0.38.
        qualified = [item for item in scored if item[0] >= self.threshold]
        runner_up_score, runner_up_name = qualified[1] if len(qualified) > 1 else (None, None)

        if runner_up_score is not None and (best_score - runner_up_score) < self.margin:
            return MatchResult(
                status="ambiguous",
                name=best_name,
                score=best_score,
                runner_up=runner_up_name,
            )
```

- [ ] **Step 4: Run the face_id suite**

Run: `cd reachy_companion && .venv/bin/python -m pytest tests/test_face_id.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add reachy_companion/src/reachy_companion/face_id.py reachy_companion/tests/test_face_id.py
git commit -m "fix(face): margin rule only compares candidates above threshold"
```

---

### Task 3: Identification picks the largest face; enrollment still requires exactly one

**Files:**
- Modify: `reachy_companion/src/reachy_companion/face_id.py` (`identify` :540-543, `enroll` :545-567, `_capture` :569-619)
- Test: `reachy_companion/tests/test_face_id.py`

**Interfaces:**
- Consumes: `_scale_face`, `align_face`, `MIN_FACE_PX`, `DETECT_DOWNSCALE` (existing).
- Produces: `_capture(self, frame_bgr, *, select_largest: bool = False)`; `identify()` passes `select_largest=True`; `enroll()` passes `select_largest=False` (default). With several faces and `select_largest=True`, the returned `Identification.face_count` is the real detected count.

**Reuse-first compliance:** the selection rule is the SDK's own — `reachy_mini/vision/face_tracking.py:97` ("Track one face: acquire largest"), `:118` `face = max(faces, key=_area)` where `_area` is bbox width×height. We mirror it exactly; head tracking therefore aims at the same face recognition scores.

- [ ] **Step 1: Write the failing tests**

Using this module's existing detector-stub pattern (the tests already fabricate `Face5` results — reuse the same stub/fixture the `multiple_faces` test uses):

```python
def test_identify_picks_the_largest_of_several_faces(tmp_path: Path) -> None:
    """Two faces in frame: identify must score the largest (the SDK tracker's rule),
    not refuse — and report the true face_count."""
    # detector stub returns two Face5s: small (bbox w=40) and large (bbox w=120)
    # embedder stub keyed by crop so the large face maps to enrolled "Alice"
    identification = recognizer.identify(frame)
    assert identification.status == "recognized"
    assert identification.name == "Alice"
    assert identification.face_count == 2

def test_enroll_still_refuses_multiple_faces(tmp_path: Path) -> None:
    record, identification = recognizer.enroll(frame_with_two_faces, "Alice")
    assert record is None
    assert identification.status == "multiple_faces"
    assert identification.face_count == 2
```

(Adapt setup to the file's real stubs — `test_who_is_this_reports_multiple_faces` in `test_face_tools.py:158` and the corresponding face_id tests show the current construction. The largest-face pick happens on the **downscaled** detections before `_scale_face`.)

- [ ] **Step 2: Run to verify the identify test fails**

Run: `cd reachy_companion && .venv/bin/python -m pytest tests/test_face_id.py -q -k largest`
Expected: FAIL — current code returns `multiple_faces` for identify.

- [ ] **Step 3: Implement**

In `_capture`, change the signature and the multi-face branch:

```python
    def _capture(
        self,
        frame_bgr: NDArray[np.uint8] | None,
        *,
        select_largest: bool = False,
    ) -> tuple[NDArray[np.float32] | None, Identification]:
```

and replace lines 595-600 with:

```python
            if not detected:
                return None, Identification(status="no_face", face_count=0)
            face_count = len(detected)
            if face_count > 1 and not select_largest:
                return None, Identification(status="multiple_faces", face_count=face_count)

            # Identification mirrors the SDK head tracker's rule (face_tracking.py:
            # "acquire largest"), so recognition scores the same face the head is
            # already aiming at. Enrollment keeps the exactly-one-face contract:
            # storing a bystander under the user's name is worse than refusing.
            chosen = max(detected, key=lambda f: f.bbox[2] * f.bbox[3])
            face = _scale_face(chosen, DETECT_DOWNSCALE)
```

Thread `face_count` through the remaining returns of `_capture` (the `too_far` return and the final `Identification`) so they report `face_count=face_count` instead of the hardcoded `1`. In `identify` (line 542) call `self._capture(frame_bgr, select_largest=True)`. `enroll` keeps the default.

- [ ] **Step 4: Run the suite**

Run: `cd reachy_companion && .venv/bin/python -m pytest tests/test_face_id.py tests/test_face_tools.py -q`
Expected: all PASS (the who_is_this `multiple_faces` tool test now needs its expectation updated — with `select_largest=True` a two-face frame scores the larger one; update that test to assert the recognized/unknown result with `face_count=2`, keeping a `multiple_faces` expectation only on the `remember_face` path).

- [ ] **Step 5: Commit**

```bash
git add reachy_companion/src/reachy_companion/face_id.py reachy_companion/tests/test_face_id.py reachy_companion/tests/test_face_tools.py
git commit -m "feat(face): identify scores the largest face (SDK tracker rule); enroll still requires exactly one"
```

---

### Task 4: Retries in the tool layer — frames, identification rounds, multi-sample enrollment

**Files:**
- Modify: `reachy_companion/src/reachy_companion/tools/face_support.py`
- Modify: `reachy_companion/src/reachy_companion/tools/who_is_this.py:29-52`
- Modify: `reachy_companion/src/reachy_companion/tools/remember_face.py:33-60`
- Test: `reachy_companion/tests/test_face_tools.py`

**Interfaces:**
- Consumes: `recognizer.identify(frame)`, `recognizer.enroll(frame, name)` (Task 3 semantics).
- Produces:
  - `face_support.capture_frame(deps, *, attempts: int = 3, pause_s: float = 0.05)` — same return contract as today, but retries `None` frames.
  - `face_support.identify_with_retries(deps, recognizer, *, attempts: int = 3, pause_s: float = 0.15) -> dict[str, Any]` — returns an `Identification.as_dict()`-shaped dict; first `recognized` wins, otherwise the most informative result seen (any scored status beats `no_face`/`unavailable`; last such result wins).
  - `remember_face` stores up to 3 samples per call and reports `samples=N`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_face_tools.py`, reusing its `ToolDependencies` factory at :109 and stub recognizer pattern:

```python
async def test_capture_frame_retries_none_frames() -> None:
    """Two 20ms appsink misses then a real frame must yield the frame, not no_frame."""
    deps = _deps_with_media(frames=[None, None, REAL_FRAME])
    frame, refusal = await capture_frame(deps)
    assert refusal is None
    assert frame is REAL_FRAME

async def test_who_is_this_retries_to_a_recognition() -> None:
    """Round 1 no_face, round 2 recognized: the tool must answer recognized."""
    # stub recognizer.identify returns [no_face, recognized("Lena", 0.59)] in sequence
    result = await WhoIsThis()(deps)
    assert result["status"] == "recognized"
    assert result["name"] == "Lena"

async def test_who_is_this_reports_best_informative_miss() -> None:
    """Rounds [no_face, unknown(score=0.21), no_face]: answer is the scored unknown."""
    result = await WhoIsThis()(deps)
    assert result["status"] == "unknown"
    assert result["score"] == 0.21

async def test_remember_face_stores_multiple_samples() -> None:
    """One call captures up to 3 samples; a failed extra sample is not an error."""
    # stub recognizer.enroll succeeds, succeeds, returns (None, no_face)
    result = await RememberFace()(deps, name="Lena")
    assert result["status"] == "saved"
    assert result["samples"] == 2
```

(Sequence-stubbing: the file's stubs are plain classes — give them a list they pop from. Patch `asyncio.sleep` where the tests would otherwise wait.)

- [ ] **Step 2: Run to verify they fail**

Run: `cd reachy_companion && .venv/bin/python -m pytest tests/test_face_tools.py -q -k "retries or samples or informative"`
Expected: FAIL (helpers don't exist / single-shot behavior).

- [ ] **Step 3: Implement `face_support` helpers**

```python
_INFORMATIVE_STATUSES = ("recognized", "ambiguous", "unknown", "too_far", "multiple_faces")


async def capture_frame(
    deps: ToolDependencies, *, attempts: int = 3, pause_s: float = 0.05
) -> tuple[NDArray[Any] | None, dict[str, Any] | None]:
    """Grab one BGR frame off the event loop, retrying transient None frames.

    The appsink is drop=True/max-buffers=1 with a 20 ms pull: on a loaded CM4 a
    None frame is routine, not an error, so one miss must not fail the tool.
    """
    for attempt in range(attempts):
        try:
            frame = await asyncio.to_thread(deps.reachy_mini.media.get_frame)
        except Exception as e:
            logger.error("Face memory could not read a camera frame: %s: %s", type(e).__name__, e)
            return None, unavailable("internal_error")
        if frame is not None:
            return frame, None
        if attempt + 1 < attempts:
            await asyncio.sleep(pause_s)
    return None, unavailable("no_frame")


async def identify_with_retries(
    deps: ToolDependencies, recognizer: Any, *, attempts: int = 3, pause_s: float = 0.15
) -> dict[str, Any]:
    """Look up to `attempts` times; first recognition wins.

    Mirrors the wake check's round loop (huggingface_realtime.py) at tool level:
    a blink, a turned head or one dropped frame must not decide the answer. On a
    miss, the most informative result seen is returned — a scored `unknown`
    beats `no_face` — so the model and the log always get the best evidence.
    """
    best: dict[str, Any] | None = None
    for attempt in range(attempts):
        frame, refusal = await capture_frame(deps)
        if refusal is not None:
            best = best or refusal
        else:
            try:
                identification = await asyncio.to_thread(recognizer.identify, frame)
            except Exception as e:
                logger.error("identify_with_retries failed: %s: %s", type(e).__name__, e)
                best = best or unavailable("internal_error")
            else:
                result = identification.as_dict()
                if result.get("status") == "recognized":
                    return result
                if best is None or (
                    result.get("status") in _INFORMATIVE_STATUSES
                    and best.get("status") not in _INFORMATIVE_STATUSES
                ) or (
                    result.get("status") in _INFORMATIVE_STATUSES
                    and best.get("status") in _INFORMATIVE_STATUSES
                ):
                    best = result
        if attempt + 1 < attempts:
            await asyncio.sleep(pause_s)
    return best if best is not None else unavailable("internal_error")
```

- [ ] **Step 4: Rewire the two tools**

`who_is_this.py.__call__` — replace the capture+identify block with the call below, and fix the imports: add `identify_with_retries` to the `face_support` import; remove now-unused names (`asyncio`, `capture_frame`, `unavailable`) so `ruff check` stays clean:

```python
        result = await identify_with_retries(deps, recognizer)
        logger.info(
            "Tool call: who_is_this status=%s name=%s score=%s",
            result.get("status"),
            result.get("name"),
            result.get("score"),
        )
        return result
```

`remember_face.py.__call__` — after the first successful `enroll` (existing code path unchanged through the `record is None` refusal), take up to 2 extra samples:

```python
        for _ in range(2):
            await asyncio.sleep(0.2)
            extra_frame, blocked = await capture_frame(deps, attempts=1)
            if blocked is not None:
                break
            try:
                extra_record, extra_identification = await asyncio.to_thread(recognizer.enroll, extra_frame, name)
            except Exception as e:  # extra samples are best-effort, never an error
                logger.warning("remember_face extra sample failed: %s: %s", type(e).__name__, e)
                break
            if extra_record is None:
                logger.info("remember_face extra sample refused: status=%s", extra_identification.status)
                break
            record = extra_record

        logger.info("Tool call: remember_face saved name=%s samples=%d", record.name, len(record.embeddings))
        return {"status": "saved", "name": record.name, "samples": len(record.embeddings)}
```

- [ ] **Step 5: Run the face-tools suite**

Run: `cd reachy_companion && .venv/bin/python -m pytest tests/test_face_tools.py -q`
Expected: all PASS (update any existing single-shot expectations — e.g. the `no_frame` test now needs `get_frame` to return `None` for all 3 attempts).

- [ ] **Step 6: Commit**

```bash
git add reachy_companion/src/reachy_companion/tools/face_support.py reachy_companion/src/reachy_companion/tools/who_is_this.py reachy_companion/src/reachy_companion/tools/remember_face.py reachy_companion/tests/test_face_tools.py
git commit -m "feat(face): multi-frame retries for who_is_this and multi-sample enrollment"
```

---

### Task 5: Extended wake window — keep looking after the greeting, greet by name late

**Files:**
- Modify: `reachy_companion/src/reachy_companion/huggingface_realtime.py` (constants ~:268-276, `__init__` state ~:405-410, `speech_started` handler ~:1771-1772, `_send_startup_greeting_prompt` :1398-1432, new method after `_recognized_face_prefix`, task cancellation in the shutdown path)
- Test: `reachy_companion/tests/test_huggingface_realtime.py`

**Interfaces:**
- Consumes: `self.deps.face_recognizer`, `self.deps.reachy_mini.media.get_frame`, `self._safe_response_create()`, `self._active_response_id`, `env_int`/`env_bool` (all existing).
- Produces: `self._wake_face_task: asyncio.Task[None] | None`, `self._user_has_spoken: bool`, method `_extended_wake_face_check() -> None`, env `FACE_WAKE_EXTENDED_MS` (default 8000, clamp 0–20000, 0 disables).

**Design contract (privacy, D-013):** this is still the *one* auto-recognition hook — the same wake check, given a realistic window. It runs once per app start, is hard-bounded by `FACE_WAKE_EXTENDED_MS` (every await inside the loop is wrapped in `asyncio.wait_for` against the shared deadline, like the quick check), is cancelled-and-awaited at shutdown, and never runs again after it ends. The quick pre-greeting check (1200 ms) stays exactly as is, so the greeting is never delayed; the extension runs *after* the greeting is queued. It honors `FACE_AUTO_GREET` (the same kill switch as the quick check). **Turn safety:** once the user has spoken (`self._user_has_spoken`), the window closes without injecting anything — a context item landing mid-turn could steer the model's answer; from that point Task 1's routing owns identity. Response ordering is delegated to `_safe_response_create()`'s sender loop (it serializes on `_response_done_event`), so the late greeting can never overlap the boot greeting; no `_active_response_id` gate is used (it would race the server's `response.created` event). The task binds `connection = self.connection` once at start and aborts if `self.connection` is no longer that object (reconnect) before injecting.

- [ ] **Step 1: Add constants and state**

After line 273 (`_FACE_WAKE_RETRY_PAUSE_S`):

```python
_FACE_WAKE_EXTENDED_MS_DEFAULT: Final[int] = 8000
_FACE_WAKE_EXTENDED_PAUSE_S: Final[float] = 0.7
_FACE_LATE_RECOGNITION_PROMPT: Final[str] = (
    "（系统提示：摄像头刚认出面前的人是「{name}」。自然地用名字招呼他，"
    "或在你接下来说的话里称呼他的名字。不要提到摄像头或识别这件事。）"
)
```

In `__init__` beside `self._active_response_id` (~:409):

```python
        self._user_has_spoken = False
        self._wake_face_task: asyncio.Task[None] | None = None
```

In the receiver loop at the `input_audio_buffer.speech_started` branch (:1771-1772), first line of the branch:

```python
                        self._user_has_spoken = True
```

- [ ] **Step 2: Write the failing tests**

New tests in `tests/test_huggingface_realtime.py`, following that file's existing handler-construction pattern (build the handler with stub deps/connection the way neighboring async tests do):

```python
async def test_extended_wake_check_injects_late_recognition(monkeypatch) -> None:
    """Miss, then a hit: a context item is created and a response is requested
    (ordering is the sender loop's job, so no active-response precondition)."""
    # recognizer.identify sequence: [unknown, recognized("Lena", 0.59)]
    # handler._user_has_spoken False
    await handler._extended_wake_face_check()
    assert any("Lena" in call.item_text for call in fake_connection.created_items)
    assert fake_handler_response_requests == 1

async def test_extended_wake_check_goes_silent_after_user_spoke(monkeypatch) -> None:
    """Once the user has spoken, the window closes without injecting anything —
    a context item landing mid-turn could steer the model's answer."""
    handler._user_has_spoken = True
    await handler._extended_wake_face_check()
    assert fake_connection.created_items == []
    assert fake_handler_response_requests == 0

async def test_extended_wake_check_disabled_by_env(monkeypatch) -> None:
    monkeypatch.setenv("FACE_WAKE_EXTENDED_MS", "0")
    await handler._extended_wake_face_check()
    assert fake_connection.created_items == []

async def test_extended_wake_check_respects_auto_greet_kill_switch(monkeypatch) -> None:
    monkeypatch.setenv("FACE_AUTO_GREET", "0")
    await handler._extended_wake_face_check()
    assert fake_connection.created_items == []

async def test_extended_wake_check_gives_up_at_deadline(monkeypatch) -> None:
    """identify always returns unknown: the loop must end by budget, having
    created nothing."""

async def test_extended_wake_check_aborts_on_reconnected_session(monkeypatch) -> None:
    """If self.connection is swapped while the task runs, the hit must NOT be
    injected into the new session."""
    # recognizer.identify returns recognized; between spawn and the hit,
    # replace handler.connection with a different fake object
    await handler._extended_wake_face_check()
    assert new_fake_connection.created_items == []

async def test_startup_greeting_spawns_extended_check_only_on_a_miss(monkeypatch) -> None:
    """_send_startup_greeting_prompt wiring: spawn when the quick prefix is "",
    no spawn when the prefix recognized someone, no spawn under FACE_AUTO_GREET=0."""
    # three sub-cases; assert handler._wake_face_task is (not) None after each call
```

(Patch `asyncio.sleep` to a no-op and drive `time.monotonic` via monkeypatch, or set `FACE_WAKE_EXTENDED_MS` to a small value — match how existing timing tests in this file handle deadlines.)

- [ ] **Step 3: Run to verify they fail**

Run: `cd reachy_companion && .venv/bin/python -m pytest tests/test_huggingface_realtime.py -q -k extended_wake`
Expected: FAIL (`_extended_wake_face_check` does not exist).

- [ ] **Step 4: Implement the method**

Insert after `_recognized_face_prefix` (:1396):

```python
    async def _extended_wake_face_check(self) -> None:
        """Keep the wake face check alive briefly after the greeting (D-013 hook, part 2).

        The pre-greeting check gets ~1200 ms at the exact moment of boot — the
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
                        # Bounded so a stalled network write cannot keep this
                        # task alive far past its window; 5 s is a transport
                        # guard, not budget.
                        await asyncio.wait_for(
                            connection.conversation.item.create(
                                item={
                                    "type": "message",
                                    "role": "user",
                                    "content": [
                                        {
                                            "type": "input_text",
                                            "text": _FACE_LATE_RECOGNITION_PROMPT.format(name=name),
                                        },
                                    ],
                                },
                            ),
                            5.0,
                        )
                        await self._safe_response_create()
                        logger.info(
                            "Extended wake face check: recognized %s (score %.3f) on round %d; queued a late named greeting.",
                            name, identification.score or 0.0, rounds,
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
```

- [ ] **Step 5: Spawn and cancel the task**

In `_send_startup_greeting_prompt`, capture the prefix result and spawn after the greeting is queued — replace :1412 and extend the success path:

```python
        face_prefix = await self._recognized_face_prefix()
        greeting_prompt = face_prefix + greeting_prompt
```

and after `logger.info("Queued startup greeting prompt")` (:1430):

```python
            if not face_prefix and env_bool("FACE_AUTO_GREET", True):
                self._wake_face_task = asyncio.create_task(self._extended_wake_face_check())
```

(The method re-checks `FACE_AUTO_GREET` itself, so the guard here is belt-and-braces; keep both — the spawn-site check saves creating a task that instantly returns, and the in-method check protects direct callers and tests.)

In the shutdown/close path: find where the handler cancels its long-lived tasks (the same place `_response_sender_loop`'s task and the receiver task are cancelled — grep for `.cancel()` in this file) and add, **following the exact cancel-and-await pattern the neighboring tasks use there** (cancel, then await the task with `CancelledError` suppressed, so no in-flight `item.create`/`to_thread` result is left unobserved):

```python
        if self._wake_face_task is not None:
            self._wake_face_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._wake_face_task
            self._wake_face_task = None
```

(If the neighbors use a shared helper for this, use that helper instead of the inline suppress block — mirror, don't invent. `contextlib.suppress(asyncio.CancelledError, Exception)` collapses to `suppress(Exception)`; write whichever form the file's existing shutdown code uses.)

- [ ] **Step 6: Run the realtime suite**

Run: `cd reachy_companion && .venv/bin/python -m pytest tests/test_huggingface_realtime.py -q`
Expected: all PASS, including the pre-existing greeting tests (they must be unaffected: prefix behavior unchanged, task spawn is fire-and-forget).

- [ ] **Step 7: Commit**

```bash
git add reachy_companion/src/reachy_companion/huggingface_realtime.py reachy_companion/tests/test_huggingface_realtime.py
git commit -m "feat(face): extended wake window keeps recognizing briefly after the greeting"
```

---

### Task 6: Store hygiene — alignment marker, load-count logging, invalid_name mislabel

**Files:**
- Modify: `reachy_companion/src/reachy_companion/faces.py` (record write :177-191 area, record parse :143-174 area)
- Modify: `reachy_companion/src/reachy_companion/face_id.py` (ready log :452-457, `enroll` ValueError branch :559-563)
- Test: `reachy_companion/tests/test_faces.py`, `reachy_companion/tests/test_face_id.py`

**Interfaces:**
- Consumes: existing `FaceRecord`, `_read_faces_file`, `_write_faces_file`.
- Produces: module constant `faces.ALIGNMENT_VERSION = "arcface5"`; stored records carry `"alignment": "arcface5"`; records with a *different* marker are dropped with a WARNING; records with **no** marker are accepted (the two live post-D-015 records must survive). "Face memory ready" log line gains `, N people enrolled`.

- [ ] **Step 1: Write the failing tests**

```python
def test_alignment_marker_round_trips(tmp_path: Path) -> None:
    save_face(tmp_path, "Alice", _embedding())
    raw = json.loads((tmp_path / "faces.v1.json").read_text())
    assert raw["faces"][0]["alignment"] == "arcface5"

def test_mismatched_alignment_marker_is_dropped_with_warning(tmp_path, caplog) -> None:
    _write_record(tmp_path, name="Old", alignment="threepoint-legacy")
    assert list_faces(tmp_path) == []
    assert "alignment" in caplog.text

def test_unmarked_record_is_grandfathered(tmp_path: Path) -> None:
    """Records written before the marker existed (the live Louis/Lena records)
    must keep loading."""
    _write_record(tmp_path, name="Louis", alignment=None)  # no field at all
    assert [r.name for r in list_faces(tmp_path)] == ["Louis"]

def test_ready_log_reports_people_count(...) -> None:   # in test_face_id.py
    # build recognizer over a store with 2 people; assert "2 people enrolled"
    # appears in the "Face memory ready" log record (caplog)

def test_enroll_malformed_embedding_reports_internal_error(...) -> None:  # in test_face_id.py
    # monkeypatch upsert_face to raise ValueError("bad embedding")
    record, identification = recognizer.enroll(frame, "Alice")
    assert record is None
    assert identification.reason == "internal_error"
```

(Use each test module's existing store helpers for `_embedding`/`_write_record`; write raw JSON directly for the marker cases.)

- [ ] **Step 2: Run to verify they fail**

Run: `cd reachy_companion && .venv/bin/python -m pytest tests/test_faces.py tests/test_face_id.py -q -k "alignment or grandfathered or people_count or malformed"`
Expected: FAIL.

- [ ] **Step 3: Implement**

`faces.py`: add `ALIGNMENT_VERSION: Final[str] = "arcface5"` beside `SCHEMA_VERSION` (add `from typing import Final` if the module does not already import it); include `"alignment": ALIGNMENT_VERSION` in the record dict written by `_write_faces_file`. The read-side check goes **inside `_record_from_json`** (which already validates that the item is a mapping and that each field is well-typed — putting the check in the outer loop would call `.get` on non-dict rows that today are skipped safely). After `_record_from_json`'s existing mapping/type validation, before constructing the record:

```python
    if "alignment" in data and data["alignment"] != ALIGNMENT_VERSION:
        logger.warning(
            "Dropping face record %r: alignment %r does not match the current pipeline (%s); re-enroll this person.",
            data.get("name"), data["alignment"], ALIGNMENT_VERSION,
        )
        return None
```

(Bind `data`/return-shape to `_record_from_json`'s actual parameter name and rejected-record convention — read the function first. Key-presence, not `.get()`: only a record with **no** `alignment` key at all is grandfathered; an explicit `null` or wrong marker is dropped.)

`face_id.py`: extend the ready log (:452-457) to:

```python
        logger.info(
            "Face memory ready: YuNet + SFace sessions built in %.0f ms (threshold %.2f, margin %.2f, %d people enrolled)",
            elapsed_ms, self.threshold, self.margin, len(list_faces(self.instance_path)),
        )
```

and change the `enroll` ValueError branch (:561-563) to `reason="internal_error"` (a malformed embedding is our defect, not a bad name; `invalid_name` stays for the empty-name branch).

- [ ] **Step 4: Run both suites**

Run: `cd reachy_companion && .venv/bin/python -m pytest tests/test_faces.py tests/test_face_id.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add reachy_companion/src/reachy_companion/faces.py reachy_companion/src/reachy_companion/face_id.py reachy_companion/tests/test_faces.py reachy_companion/tests/test_face_id.py
git commit -m "fix(face): alignment marker in the store, enrolled-count in the ready log, correct reason for malformed embeddings"
```

---

### Task 7: Pin the YuNet revision in the preload script

**Files:**
- Modify: `scripts/preload_assets.py:5-9`

**Interfaces:** none (standalone script).

- [ ] **Step 1: Implement**

Replace the YuNet block with:

```python
# Face-detection model (daemon side). Import the SDK's own pins so the warmed
# cache entry is exactly the revision FaceDetector loads — an unpinned download
# warms a *different* entry and the robot still hits the network at wake time.
from reachy_mini.vision.face_detector import _MODEL_FILE, _MODEL_REPO, _MODEL_REVISION

hf_hub_download(_MODEL_REPO, _MODEL_FILE, revision=_MODEL_REVISION)
print("cached: YuNet face model")
```

(SFace in `face_id.py` is downloaded unpinned by the app itself, so the existing unpinned SFace preload line already matches — leave it.)

- [ ] **Step 2: Verify by running it**

Run: `cd /Users/novalien0424/Reachy-companion && reachy_companion/.venv/bin/python scripts/preload_assets.py`
Expected: exit 0, `cached:` lines (warm HF cache, ~seconds).

- [ ] **Step 3: Commit**

```bash
git add scripts/preload_assets.py
git commit -m "fix(deploy): preload the SDK-pinned YuNet revision"
```

---

### Task 8: Feature rows, decision record, state files

**Files:**
- Modify: `feature_list.json`
- Modify: `DECISIONS.md` (append D-024)
- Modify: `progress.md`, `session-handoff.md` (end-of-session ritual)

**Interfaces:** none.

- [ ] **Step 1: Add four rows to `feature_list.json`** (matching the existing row schema — `id`, `behavior`, `verification`, `state`, `evidence`, `next_action`):

- `FACE-ROUTING` — behavior: identity questions (「我是谁」「你认得我吗」, someone new arriving) select `who_is_this`, never `camera`; verification: on-robot session asking 「你記得我嗎」 and 「我是誰」 → journal shows `Tool call received — tool_name='who_is_this'` and no `camera` call for those turns; state `implemented-unverified`.
- `FACE-WAKE-EXTENDED` — behavior: after a missed pre-greeting check, the extended window (default 8 s) recognizes a person who sits down after boot and speaks/logs a late named greeting; verification: boot with nobody in frame, lean in within 8 s → journal `Extended wake face check: recognized … late greeting`; state `implemented-unverified`.
- `FACE-CROSS-SESSION` — behavior: a person enrolled on a previous day is recognized (score ≥ 0.363 logged) in a fresh session; verification: Louis (enrolled 2026-08-26) asks 「你認得我嗎」 → `who_is_this status=recognized name=Louis score=…`; this is the D-015 threshold's first live validation; state `implemented-unverified`.
- `FACE-MULTI-SAMPLE` — behavior: one `remember_face` call stores up to 3 samples (`samples>=2` when the person holds still); verification: journal `remember_face saved name=… samples=N`; state `implemented-unverified`.

- [ ] **Step 2: Append D-024 to `DECISIONS.md`** — one paragraph in house style: RCA evidence (14/14 wake failures, camera mis-routing transcript, 0.594/0.145 live scores), the five decisions (routing clauses in descriptions + persona; largest-face identify per SDK rule while enroll keeps exactly-one; tool-layer retries + 3-sample enrollment; bounded extended wake window `FACE_WAKE_EXTENDED_MS` after the greeting, still the single auto-recognition hook; alignment marker with unmarked-records-grandfathered), and what was deliberately not done (no continuous recognition, no forget/list tool yet, no retry after a failed model load).

- [ ] **Step 3: Update `progress.md` and `session-handoff.md`** per the End Of Session contract (fold the RCA, the fix wave, and the new operator-verification rows into "Pending verification").

- [ ] **Step 4: Commit**

```bash
git add feature_list.json DECISIONS.md progress.md session-handoff.md
git commit -m "docs: face-recognition RCA outcome — D-024, verification rows, state"
```

---

### Task 9: Full gate

- [ ] **Step 1:** `cd reachy_companion && .venv/bin/python -m pytest -q` — expected ≥1319 passed / 31 skipped, 0 failed.
- [ ] **Step 2:** `cd reachy_companion && .venv/bin/ruff check .` — clean.
- [ ] **Step 3:** `cd reachy_companion && .venv/bin/python -m mypy --strict src` — clean (match the invocation the repo used for the 13th-install gate; check `pyproject.toml`/`Makefile` for the exact mypy target and use that).
- [ ] **Step 4:** Fix anything red; amend the owning task's commit or add `fix:` commits.

---

### Task 10: Deploy and on-robot smoke verification

**Prerequisite:** invoke `.claude/skills/reachy-deploy` and follow it exactly (backup/restore ritual for `.env`, `persona.md`, `memory.v1.json`, `faces.v1.json`; two-step `--no-deps` wheel install; plain scp, never expect-wrapped).

- [ ] **Step 1:** Build the wheel, deploy per the skill, restore instance state (the current `faces.v1.json` with Louis + Lena **must survive** — it is the cross-session test fixture).
- [ ] **Step 2:** Copy the updated repo-root `persona.md` to the instance path (the skill's persona step).
- [ ] **Step 3:** Start the app; verify in the journal, zero tracebacks:
  - `Face memory ready: … 2 people enrolled` (Task 6 live),
  - wake check lines followed by `Extended wake face check: …` (Task 5 live — with nobody in frame it must end with `window closed`, proving the bound),
  - `persona: instance persona.md` still present.
- [ ] **Step 4:** Leave the robot app-stopped or running per the deploy skill's end state; record install evidence in `progress.md` (Task 8 files may be amended).
- [ ] **Step 5:** Remaining rows are operator-gated (`FACE-ROUTING`, `FACE-WAKE-EXTENDED`, `FACE-CROSS-SESSION`, `FACE-MULTI-SAMPLE`) — they need a human face. Record them as pending with exact journal greps.

---

## Review Log (Codex)

**Round 1 (2026-08-27):** 10 findings, all accepted.
1. blocker — extended check ignored `FACE_AUTO_GREET` → kill-switch check added in-method + at spawn site.
2. blocker — `_active_response_id is None` gate races `response.created` → gate removed; ordering delegated to `_safe_response_create`'s sender loop, which already serializes on `_response_done_event`.
3. major — context item after user speech could steer the turn → window now closes silently once `_user_has_spoken`; no item, no response.
4. major — loop not hard-bounded → every await wrapped in `asyncio.wait_for(…, remaining())`.
5. major — mutable `self.connection` across the task → bound once at start; abort when `self.connection is not connection`.
6. major — cancel without await leaks in-flight work → cancel-then-await with suppression, mirroring the file's existing shutdown pattern.
7. major — alignment check on possibly-non-dict rows → moved inside `_record_from_json` after its mapping validation.
8. major — tests skipped startup wiring → added spawn-condition test (miss / hit / kill switch) plus reconnect-abort test.
9. minor — no `wait_ready` in extended path → added within the window budget.
10. minor — Task 4 import hygiene → explicit import add/remove instructions.

**Round 2 (2026-08-27):** 4 findings — 3 accepted, 1 rejected.
1. major, response-request coalescing race — **rejected**: if the late request coalesces with a still-queued boot request, the single `response.create` already sees the created name context item, so the outcome is a *named* greeting; and the hit arrives seconds after the boot request is dequeued, so the window is not reachable in practice.
2. major — `item.create` unbounded → wrapped in `asyncio.wait_for(…, 5.0)` (transport guard); the residual "speech starts during the awaited create" race is inherent to any network write and accepted.
3. minor — `Final` import in faces.py → instruction added.
4. minor — explicit `null` marker grandfathered → key-presence check (`"alignment" in data`) instead of `.get()`.

**Round 3 (2026-08-27):** No findings. Plan approved for execution.
