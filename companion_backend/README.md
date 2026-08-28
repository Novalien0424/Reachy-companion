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
data/people.json            people, facts, photo records, sync state
data/photos/<person_id>/    the uploaded bytes, named after the photo id
```

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
| `backend/store.py` | the JSON people store (this task) |
| `backend/embedding.py` | photo decode + SFace embedding *(Task 9)* |
| `backend/projection.py` | Mac store → robot store files *(Task 10)* |
| `backend/robot.py` | ssh/scp push, drift detection, import *(Task 10)* |
| `backend/app.py` | the HTTP API *(Task 11)* |
| `static/` | the operator UI *(Task 12)* |
