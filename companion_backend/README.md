# companion_backend — Mac-side management backend

A small FastAPI app that runs on the operator's Mac and owns the **durable**
side of the companion's person memory: names, uploaded photos, the SFace
embeddings computed from them, and per-person facts.

The robot's `faces.v1.json` and `people.v1.json` are a *projection* of this
store. Those files live inside the app's install directory on the robot and are
wiped by every reinstall; this store is not. That inversion is the whole point
of the backend — enroll once on the Mac, push as often as you like.

## Running

```sh
./run.sh                 # http://127.0.0.1:8710
./run.sh --reload        # extra args are passed straight to uvicorn
```

There is nothing to install. `run.sh` execs the existing
`reachy_companion/.venv` (Python 3.12), which already carries fastapi, uvicorn
and — importantly — `reachy_companion` itself: the store imports
`faces.normalize_face_name` and `memory.normalize_memory_text` from it so a
name or fact stored here is byte-identical to what the robot would store.

The robot's address comes from the repo-root `.env` (`REACHY_HOST`,
`REACHY_SSH_USER`), the same file the deploy skill reads. Data lives in
`companion_backend/data/` (gitignored; override with `COMPANION_BACKEND_DATA`):

```
data/people.json                  people, facts, photo records, sync state
data/photos/<person_id>/          the uploaded bytes, named after the photo id
data/people.json.corrupt.<ms>     an unparseable store, kept for inspection
```

This store is the source of truth — the robot's copy is a projection that can
be rebuilt from it, not the other way round. So a `people.json` that will not
parse is never overwritten: the read that finds it renames it aside with an
epoch-millisecond suffix and logs a WARNING naming that path, and the store
then starts fresh. If you ever see one of those files, the people in it were
not lost; salvage what you need and delete it.

## API

JSON in, JSON out, snake_case throughout — which is deliberately *not* the
camelCase the store persists, because that shape is shared with the robot's own
readers and must not drift to suit a web client. `/docs` serves the generated
reference.

| Route | Returns |
|---|---|
| `GET /api/config` | `{reachy_host}` |
| `GET /api/people` | a list of people, each with `facts` and `photos` |
| `POST /api/people` `{name}` | the person — 409 duplicate, 400 empty |
| `PATCH /api/people/{id}` `{name}` | the renamed person |
| `DELETE /api/people/{id}` | the removed person |
| `POST /api/people/{id}/facts` `{text}` | the fact (a duplicate returns the existing one) |
| `DELETE /api/people/{id}/facts/{fact_id}` | the removed fact |
| `POST /api/people/{id}/photos` (multipart `file`) | the photo, embedded synchronously |
| `DELETE /api/people/{id}/photos/{photo_id}` | the removed photo |
| `GET /api/people/{id}/photos/{photo_id}/file` | the image bytes (404 for a synthetic photo) |
| `GET /api/sync/status` | `{last_push_at, robot_reachable, drift, error}` |
| `POST /api/sync/push` | the push result — **409** when the robot holds unknown content |
| `GET` / `POST /api/sync/import` | `{diff, applied, conflicts}` — preview / apply |
| `GET /api/robot/status`, `POST /api/robot/{start,stop,restart}` | the daemon's own answer |
| `GET /`, `/static/*` | the operator UI |

Three shapes are worth knowing before writing a client:

- **Embeddings never travel.** A photo carries `has_embedding: bool`, never its
  128 floats; a robot face in a diff carries `sample_count`.
- **A failed embedding is data, not an error.** An upload always returns 200
  with the photo record; `error` is one of `no_face`, `multiple_faces`,
  `too_far`, `decode_failed`, `internal_error`, or `null`.
- **One error envelope**, always `{"error": <message>, "kind": <slug>}`:
  `duplicate_name` 409, `empty_value` 400, `not_found` 404, `no_photo_bytes`
  404, `photo_too_large` 413, `invalid_request` 422, `robot_unreachable` 502
  (carrying the ssh/scp stderr tail). `GET /api/sync/status` is the exception
  the UI polls: an unreachable robot is `robot_reachable: false` there, never a
  502.

## Tests

```sh
cd companion_backend
../reachy_companion/.venv/bin/python -m pytest tests/ -v
../reachy_companion/.venv/bin/ruff check backend/ tests/
../reachy_companion/.venv/bin/mypy --strict backend/
```

## Security posture — trusted LAN only

`run.sh` binds `127.0.0.1` deliberately: the UI is an operator tool for *this*
Mac, and there is no authentication, no CSRF protection and no rate limiting in
front of it. Anyone who can reach the port can read and delete every stored
photo and fact, and can push arbitrary content to the robot.

- Do not change the bind address to `0.0.0.0`.
- Access it from another machine only through an SSH tunnel
  (`ssh -L 8710:127.0.0.1:8710 …`), never by re-binding.
- Robot access itself is plain `ssh`/`scp` over the local network and assumes a
  trusted LAN.

*(Placeholder — Task 13 revisits this section once the sync and UI surfaces
exist and the residual risks can be stated concretely.)*

## Layout

| Path | What it is |
|---|---|
| `backend/config.py` | `Settings` + `load_settings()` — robot address, data dir |
| `backend/store.py` | the JSON people store |
| `backend/embedding.py` | photo decode + SFace embedding *(Task 9)* |
| `backend/projection.py` | Mac store → robot store files *(Task 10)* |
| `backend/robot.py` | ssh/scp push, drift detection, import *(Task 10)* |
| `backend/app.py` | the HTTP API — routes, error mapping, the shared recognizer |
| `static/` | the operator UI — a placeholder page until *(Task 12)* |
