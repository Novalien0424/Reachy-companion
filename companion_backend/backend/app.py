"""The management API: people, photos, facts, the guarded sync, and the UI.

This module is glue and nothing else. Every rule about what a name is, what a
push may overwrite and what an embedding means already lives in `store`,
`projection`, `robot` and `embedding`; the job here is to turn those calls into
status codes and JSON, and to be uninteresting while doing it.

Four decisions are worth stating, because they are the ones a reader would
otherwise have to reverse-engineer:

* **One error envelope.** Every failure — a duplicate name, an unknown id, an
  unreachable robot, a malformed body — comes back as
  `{"error": <message>, "kind": <slug>}` with a status code. Nothing is
  swallowed and nothing is flattened into a 200 with a false success. The
  handlers are registered on the *concrete* store exceptions rather than on
  `ValueError` / `LookupError`, deliberately: a stray `KeyError` from a defect
  in this file is a 500, not a 404 the operator would read as "no such person".

* **A failed embedding is data.** `POST …/photos` stores the bytes, embeds them
  synchronously and returns the photo record *including* its `error`. An
  operator who uploads a photo with two faces in it gets a 200 and a photo row
  saying `multiple_faces`, which is a thing they can act on; a 4xx would tell
  them the request was wrong when it was the photo.

* **The robot is allowed to be absent.** `GET /api/sync/status` is the one
  route the UI polls, so an unreachable robot is reported there as
  `robot_reachable: false` plus the ssh message, never as a 502 that would make
  the whole page look broken. Every *action* route (push, import, the app
  lifecycle) still fails loudly with a 502 carrying the command's stderr tail.

* **Embeddings never travel.** A photo's 128 floats are elided to
  `has_embedding: bool`, and a robot face's samples to `sample_count`. The UI
  has no use for the numbers, and a dozen people's worth of them would be most
  of the payload.

The JSON is snake_case throughout, which is *not* the camelCase the store
persists. That is on purpose: `to_json` is the on-disk format and is shared
with the robot's own readers, so it must not drift to suit a web client.

Handlers are plain `def`, so FastAPI runs them on its threadpool: every call
below is blocking (file IO, ssh, a synchronous embed), and the store's own
`RLock` makes that safe. The one hazard that lock does not cover is two syncs
in flight at once — they race on the robot's staged files, not on the store —
so the two mutating sync routes take `_SYNC_LOCK` and a second caller is
refused immediately with a 409 rather than queued.
"""

from __future__ import annotations
import logging
import mimetypes
import threading
from typing import Any, Final, Annotated
from pathlib import Path
from contextlib import contextmanager, asynccontextmanager
from collections.abc import Callable, Iterator, AsyncIterator

from fastapi import Depends, FastAPI, Request, APIRouter, UploadFile
from pydantic import BaseModel
from fastapi.responses import FileResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from reachy_companion.face_id import FaceRecognizer
from backend import robot, store, embedding
from backend.config import PACKAGE_ROOT, Settings, load_settings


logger = logging.getLogger(__name__)

# The operator UI (Task 12). Mounted as a directory rather than a set of routes
# so adding a file there needs no change here.
STATIC_DIR: Final[Path] = PACKAGE_ROOT / "static"
INDEX_FILENAME: Final[str] = "index.html"

# A sanity bound on one upload, not a security control: this backend binds
# localhost and trusts its operator. It exists so that a mis-picked video file
# fails with a clear 413 instead of being stored and then failing to decode.
MAX_UPLOAD_BYTES: Final[int] = 25 * 1024 * 1024

# How much of a remote command's message survives into a response body. The
# useful part of an ssh/scp failure is the *end* — our own prefix is at the
# front and the command's stderr at the back — so the tail is what is kept.
MAX_ERROR_CHARS: Final[int] = 2000


# --------------------------------------------------------------------------
# the shared recognizer
# --------------------------------------------------------------------------

# Module-level and built once: `FaceRecognizer` owns two ONNX sessions and a
# cold build reads ~37 MB off disk. The lifespan below kicks `start_warmup()` so
# that cost is paid while the operator is still looking at an empty page, not
# inside their first upload.
_RECOGNIZER_LOCK: Final[threading.Lock] = threading.Lock()
_recognizer: FaceRecognizer | None = None


def recognizer_for(settings: Settings) -> FaceRecognizer:
    """Return the process-wide recognizer, building it on first use."""
    global _recognizer
    with _RECOGNIZER_LOCK:
        if _recognizer is None:
            _recognizer = embedding.build_recognizer(settings)
        return _recognizer


# --------------------------------------------------------------------------
# errors
# --------------------------------------------------------------------------


class ApiError(Exception):
    """A failure this module raises itself, carrying its own status and slug."""

    def __init__(self, status: int, kind: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.kind = kind
        self.message = message


def _tail(message: str) -> str:
    """Return a message bounded in length, keeping its end (see MAX_ERROR_CHARS)."""
    cleaned = message.strip()
    if len(cleaned) <= MAX_ERROR_CHARS:
        return cleaned
    return "…" + cleaned[-MAX_ERROR_CHARS:]


def _error(status: int, kind: str, message: str) -> JSONResponse:
    """Render the one error envelope every route and handler returns."""
    return JSONResponse({"error": _tail(message), "kind": kind}, status_code=status)


def _handler(status: int, kind: str) -> Callable[[Request, Exception], Response]:
    """Build the handler that maps one exception class onto one status code.

    The signature takes a bare `Exception` because that is what Starlette's
    dispatch promises; the class is already pinned by the registration below.
    """

    def handle(request: Request, exc: Exception) -> Response:
        logger.info("%s %s -> %d %s: %s", request.method, request.url.path, status, kind, exc)
        return _error(status, kind, str(exc))

    return handle


def _api_error_handler(request: Request, exc: Exception) -> Response:
    if not isinstance(exc, ApiError):  # pragma: no cover - registered for ApiError only
        raise exc
    logger.info("%s %s -> %d %s: %s", request.method, request.url.path, exc.status, exc.kind, exc.message)
    return _error(exc.status, exc.kind, exc.message)


def _validation_handler(request: Request, exc: Exception) -> Response:
    """Give FastAPI's own body validation the same envelope as everything else."""
    logger.info("%s %s -> 422 invalid_request: %s", request.method, request.url.path, exc)
    return _error(422, "invalid_request", str(exc))


def _http_exception_handler(request: Request, exc: Exception) -> Response:
    """Give Starlette's own 404s, 405s and static-file misses that same envelope.

    Without this the one shape a client has to handle would be two: everything
    below, plus a bare `{"detail": …}` for any URL that never reached a route.
    The exception's headers are carried through — a 405 without its `Allow` is
    a worse answer than a 405 with one.
    """
    if not isinstance(exc, StarletteHTTPException):  # pragma: no cover - registered for it only
        raise exc
    kind = "not_found" if exc.status_code == 404 else "http_error"
    response = _error(exc.status_code, kind, str(exc.detail))
    if exc.headers:
        response.headers.update(exc.headers)
    return response


# --------------------------------------------------------------------------
# views (the wire shapes)
# --------------------------------------------------------------------------


def _fact_view(fact: store.BackendFact) -> dict[str, Any]:
    return {"id": fact.id, "text": fact.text, "created_at": fact.created_at}


def _photo_view(photo: store.BackendPhoto) -> dict[str, Any]:
    """One photo, with its embedding reduced to whether there is one.

    `display_only` travels because the UI has to say why a photo with a perfectly
    good picture in it is not embedded and never will be: it is the robot's
    enrollment snapshot, not a sample.
    """
    return {
        "id": photo.id,
        "display_name": photo.display_name,
        "stored_as": photo.stored_as,
        "added_at": photo.added_at,
        "has_embedding": photo.embedding is not None,
        "error": photo.error,
        "synthetic": photo.synthetic,
        "display_only": photo.display_only,
    }


def _person_view(person: store.BackendPerson) -> dict[str, Any]:
    """One person. `aliases` travels; `former_face_ids` deliberately does not.

    An alias is something the operator needs to see — it is the other name this
    person answers to, and the reason a merge did what it did. A former robot
    record id is sync-layer bookkeeping with no meaning on the page, so it stays
    on the Mac side of this boundary.
    """
    return {
        "id": person.id,
        "name": person.name,
        "face_id": person.face_id,
        "aliases": list(person.aliases),
        "facts": [_fact_view(fact) for fact in person.facts],
        "photos": [_photo_view(photo) for photo in person.photos],
        "created_at": person.created_at,
        "updated_at": person.updated_at,
    }


def _drift_view(state: robot.DriftState) -> dict[str, Any]:
    return {
        "faces_changed": state.faces_changed,
        "people_changed": state.people_changed,
        "never_pushed": state.never_pushed,
    }


def _face_view(face: robot.RobotFace) -> dict[str, Any]:
    """One robot face record, without its samples — see the module docstring."""
    return {"record_id": face.record_id, "name": face.name, "sample_count": len(face.embeddings)}


def _person_facts_view(entry: robot.RobotPersonFacts) -> dict[str, Any]:
    return {"name": entry.name, "face_id": entry.face_id, "facts": list(entry.facts)}


def _diff_view(diff: robot.RobotDiff) -> dict[str, Any]:
    return {
        "new_faces": [_face_view(face) for face in diff.new_faces],
        "changed_faces": [_face_view(face) for face in diff.changed_faces],
        "new_person_facts": [_person_facts_view(entry) for entry in diff.new_person_facts],
        "removed_person_facts": [_person_facts_view(entry) for entry in diff.removed_person_facts],
        "empty": diff.empty,
    }


def _blocked_view(blocked: object) -> dict[str, Any] | None:
    """Render whatever stopped a push: unknown robot content, or a lost race."""
    if blocked is None:
        return None
    if isinstance(blocked, robot.RobotDiff):
        return {"kind": "robot_content", "diff": _diff_view(blocked)}
    if isinstance(blocked, robot.PushRace):
        return {"kind": "race", "message": blocked.message}
    # `PushResult.blocked_by` is typed `object`; anything else is a defect in
    # `robot.py`, and saying so beats returning a silent null.
    logger.warning("A push was blocked by an unrecognised value: %r", blocked)
    return {"kind": "unknown", "message": str(blocked)}


def _push_view(result: robot.PushResult) -> dict[str, Any]:
    return {
        "pushed": result.pushed,
        "faces_count": result.faces_count,
        "people_count": result.people_count,
        "skipped": list(result.skipped),
        "blocked_by": _blocked_view(result.blocked_by),
    }


# --------------------------------------------------------------------------
# request bodies
# --------------------------------------------------------------------------


class NameBody(BaseModel):
    """The body of a create or a rename."""

    name: str


class FactBody(BaseModel):
    """The body of an add-fact."""

    text: str


class MergeBody(BaseModel):
    """The body of a merge: who is being folded into the person in the path."""

    source_id: str


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------


def current_settings(request: Request) -> Settings:
    """Return the settings this app was built with (see `create_app`)."""
    # `Starlette.state` is untyped; the annotation is what pins it back.
    settings: Settings = request.app.state.settings
    return settings


SettingsDep = Annotated[Settings, Depends(current_settings)]

router = APIRouter()


@router.get("/api/config")
def get_config(settings: SettingsDep) -> dict[str, Any]:
    """Report what the UI needs to name the robot it is managing."""
    return {"reachy_host": settings.reachy_host}


@router.get("/api/people")
def list_people(settings: SettingsDep) -> list[dict[str, Any]]:
    """Every person, most recently updated first — the store's own order."""
    return [_person_view(person) for person in store.list_people(settings)]


@router.post("/api/people")
def create_person(body: NameBody, settings: SettingsDep) -> dict[str, Any]:
    """Create one person. 409 when the name is taken, 400 when it normalizes away."""
    return _person_view(store.create_person(settings, body.name))


@router.patch("/api/people/{person_id}")
def rename_person(person_id: str, body: NameBody, settings: SettingsDep) -> dict[str, Any]:
    """Rename one person."""
    return _person_view(store.rename_person(settings, person_id, body.name))


@router.delete("/api/people/{person_id}")
def delete_person(person_id: str, settings: SettingsDep) -> dict[str, Any]:
    """Remove one person, their facts and their photo bytes; return what was removed."""
    return _person_view(store.delete_person(settings, person_id))


@router.post("/api/people/{person_id}/merge")
def merge_people(person_id: str, body: MergeBody, settings: SettingsDep) -> dict[str, Any]:
    """Fold `source_id` into the person in the path and return the survivor.

    404 for an id neither side knows, 400 for merging someone into themselves,
    409 when a name the survivor would answer to already reaches somebody else.
    """
    return _person_view(store.merge_people(settings, person_id, body.source_id))


@router.post("/api/people/{person_id}/facts")
def add_fact(person_id: str, body: FactBody, settings: SettingsDep) -> dict[str, Any]:
    """Add one fact. A fact this person already has comes back unchanged, not twice."""
    return _fact_view(store.add_fact(settings, person_id, body.text))


@router.delete("/api/people/{person_id}/facts/{fact_id}")
def delete_fact(person_id: str, fact_id: str, settings: SettingsDep) -> dict[str, Any]:
    """Remove one fact; return what was removed."""
    return _fact_view(store.delete_fact(settings, person_id, fact_id))


@router.post("/api/people/{person_id}/photos")
def upload_photo(person_id: str, settings: SettingsDep, file: UploadFile) -> dict[str, Any]:
    """Store one uploaded photo and embed it synchronously.

    The recognizer is resolved *before* the bytes are stored: if the models
    cannot be built at all, nothing is written and the operator gets a 500 about
    the models rather than a photo row stuck in a state that means neither
    "embedded" nor "failed".

    `file.file` is read directly rather than awaited — this handler is a plain
    `def`, so it already runs on the threadpool and a blocking read is exactly
    what belongs there.
    """
    recognizer = recognizer_for(settings)

    raw = file.file.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ApiError(
            413,
            "photo_too_large",
            f"That file is {len(raw)} bytes; the limit for one photo is {MAX_UPLOAD_BYTES}.",
        )

    photo = store.add_photo(settings, person_id, file.filename or "photo", raw)
    path = store.photo_path(settings, person_id, photo)
    if path is None:  # pragma: no cover - `add_photo` always writes bytes
        raise ApiError(500, "internal_error", "The stored photo has no file on disk.")

    # `embed_photo` never raises: every outcome is a vector or one of
    # `store.PHOTO_ERRORS`, and both are recorded on the photo.
    vector, error = embedding.embed_photo(recognizer, path)
    return _photo_view(store.set_photo_embedding(settings, person_id, photo.id, vector, error))


@router.delete("/api/people/{person_id}/photos/{photo_id}")
def delete_photo(person_id: str, photo_id: str, settings: SettingsDep) -> dict[str, Any]:
    """Remove one photo record and its bytes; return what was removed."""
    return _photo_view(store.delete_photo(settings, person_id, photo_id))


@router.get("/api/people/{person_id}/photos/{photo_id}/file")
def get_photo_file(person_id: str, photo_id: str, settings: SettingsDep) -> FileResponse:
    """Serve one photo's bytes, for the UI's thumbnails.

    The path comes from the stored record, never from the request: `store` keeps
    `stored_as` a bare filename precisely so this route cannot be talked into
    reading anything else.
    """
    person = store.get_person(settings, person_id)
    if person is None:
        raise store.PersonNotFoundError(f"No person with id {person_id!r}.")
    photo = next((item for item in person.photos if item.id == photo_id), None)
    if photo is None:
        raise store.PhotoNotFoundError(f"No photo with id {photo_id!r} on person {person_id!r}.")

    path = store.photo_path(settings, person_id, photo)
    if path is None or not path.is_file():
        # A synthetic photo is an embedding imported from the robot: it is a real
        # sample with no bytes behind it, so there is nothing to show.
        raise ApiError(404, "no_photo_bytes", f"The photo {photo_id!r} has no image file.")

    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type)


# --------------------------------------------------------------------------
# sync
# --------------------------------------------------------------------------

# One sync at a time. Both mutating sync routes stage files on the robot under
# fixed names and both write the Mac store, so two of them in flight would race
# on the robot's `.faces.push.tmp` and on the last-push snapshot that the whole
# gate is built on. Handlers are plain `def` and FastAPI runs them on its
# threadpool, so two operator clicks really are two threads — a process-wide
# `threading.Lock` is exactly the scope of the hazard.
#
# The acquire is non-blocking on purpose: an operator who double-clicks Push
# gets an immediate "already running" instead of a request parked behind a 20 s
# ssh, and a worker thread is not held hostage by one.
_SYNC_LOCK: Final[threading.Lock] = threading.Lock()


@contextmanager
def _one_sync_at_a_time() -> Iterator[None]:
    """Hold the sync lock for the body, or refuse the request outright."""
    if not _SYNC_LOCK.acquire(blocking=False):
        raise ApiError(409, "sync_busy", "Another push or import is still running; wait for it to finish.")
    try:
        yield
    finally:
        _SYNC_LOCK.release()


@router.get("/api/sync/status")
def sync_status(settings: SettingsDep) -> dict[str, Any]:
    """Report the last verified push and whether the robot has written since.

    This is the polled route, so an unreachable robot is a *field*, not a 502.
    The ssh message still travels, because "unreachable" without a reason is not
    something an operator can act on.
    """
    meta = store.get_sync_meta(settings)
    try:
        state = robot.drift(settings)
    except robot.RobotError as exc:
        logger.warning("Could not reach the robot for a drift check: %s", exc)
        return {
            "last_push_at": meta.last_push_at,
            "robot_reachable": False,
            "drift": None,
            "error": _tail(str(exc)),
        }
    return {
        "last_push_at": meta.last_push_at,
        "robot_reachable": True,
        "drift": _drift_view(state),
        "error": None,
    }


@router.post("/api/sync/push")
def sync_push(settings: SettingsDep) -> JSONResponse:
    """Push the projection to the robot. A refused push is a 409 carrying its reason."""
    with _one_sync_at_a_time():
        result = robot.push(settings)
    return JSONResponse(_push_view(result), status_code=200 if result.pushed else 409)


@router.get("/api/sync/import")
def preview_import(settings: SettingsDep) -> dict[str, Any]:
    """Preview what an import would bring back, including facts the robot has forgotten.

    Shares the POST's envelope so the UI renders one shape either way; `applied`
    is null here because nothing was. Deliberately *not* under the sync lock: it
    reads the robot and writes nothing, so refusing it during a push would only
    stop the operator from seeing why their push is taking so long.
    """
    return {"diff": _diff_view(robot.import_from_robot(settings)), "applied": None, "conflicts": []}


@router.post("/api/sync/import")
def run_import(settings: SettingsDep) -> dict[str, Any]:
    """Apply the robot's current content to the Mac store.

    The diff is fetched again here rather than taken from the preview: the robot
    may have enrolled a face in between, and importing a stale diff would leave
    that enrollment to be overwritten by the very next push.
    """
    with _one_sync_at_a_time():
        diff = robot.import_from_robot(settings)
        result = robot.apply_import(settings, diff)
    return {"diff": _diff_view(diff), "applied": result.applied, "conflicts": result.conflicts}


# --------------------------------------------------------------------------
# the robot's app lifecycle, proxied
# --------------------------------------------------------------------------


@router.get("/api/robot/status")
def robot_status(settings: SettingsDep) -> dict[str, Any]:
    """What the robot's daemon says is running."""
    return dict(robot.robot_app_status(settings))


@router.post("/api/robot/start")
def robot_start(settings: SettingsDep) -> dict[str, Any]:
    """Ask the daemon to start the companion app."""
    return dict(robot.robot_app_start(settings))


@router.post("/api/robot/stop")
def robot_stop(settings: SettingsDep) -> dict[str, Any]:
    """Ask the daemon to stop the running app."""
    return dict(robot.robot_app_stop(settings))


@router.post("/api/robot/restart")
def robot_restart(settings: SettingsDep) -> dict[str, Any]:
    """Ask the daemon to restart the running app — how a push is picked up."""
    return dict(robot.robot_app_restart(settings))


# --------------------------------------------------------------------------
# the UI
# --------------------------------------------------------------------------


@router.get("/", include_in_schema=False)
def index() -> FileResponse:
    """Serve the operator UI's entry page."""
    path = STATIC_DIR / INDEX_FILENAME
    if not path.is_file():
        raise ApiError(404, "no_ui", f"There is no {INDEX_FILENAME} in {STATIC_DIR}.")
    return FileResponse(path, media_type="text/html")


# --------------------------------------------------------------------------
# the application
# --------------------------------------------------------------------------


@asynccontextmanager
async def _lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Warm the face models on a background thread while the page is still loading."""
    settings: Settings = application.state.settings
    try:
        recognizer_for(settings).start_warmup()
    except Exception:
        # A warm-up is an optimisation. Failing it must not take the whole
        # management UI down — facts, names and sync do not need the models —
        # and the same failure will be raised, not hidden, by the first upload.
        logger.warning("Could not start the face-model warm-up; the first upload will pay for it.", exc_info=True)
    yield


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the app around one `Settings`, loading the process's own when none is given."""
    application = FastAPI(title="Reachy companion — management backend", lifespan=_lifespan)
    application.state.settings = settings if settings is not None else load_settings()

    application.add_exception_handler(ApiError, _api_error_handler)
    application.add_exception_handler(RequestValidationError, _validation_handler)
    application.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    # Registered on the concrete classes, not on ValueError / LookupError: a
    # KeyError or an internal ValueError is a defect here and must stay a 500.
    application.add_exception_handler(store.DuplicateNameError, _handler(409, "duplicate_name"))
    application.add_exception_handler(store.EmptyValueError, _handler(400, "empty_value"))
    application.add_exception_handler(store.MergeError, _handler(400, "invalid_merge"))
    application.add_exception_handler(store.PersonNotFoundError, _handler(404, "not_found"))
    application.add_exception_handler(store.FactNotFoundError, _handler(404, "not_found"))
    application.add_exception_handler(store.PhotoNotFoundError, _handler(404, "not_found"))
    # The subclass first, so the file reads in the order Starlette resolves it:
    # its lookup walks the exception's MRO, so a `RobotVerifyError` finds this
    # handler and never the `RobotError` one below. Both are 502, but only one
    # of them is worth retrying — "the robot did not keep what we sent" needs
    # looking at, not clicking Push again.
    application.add_exception_handler(robot.RobotVerifyError, _handler(502, "robot_not_verified"))
    application.add_exception_handler(robot.RobotError, _handler(502, "robot_unreachable"))

    application.include_router(router)

    if STATIC_DIR.is_dir():
        application.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    else:  # pragma: no cover - the directory is committed
        logger.warning("No UI directory at %s; only the API is served.", STATIC_DIR)

    return application


app = create_app()
