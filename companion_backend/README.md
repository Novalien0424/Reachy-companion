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
./run.sh                 # http://127.0.0.1:8710 — open that and you are in the UI
./run.sh --reload        # extra args are passed straight to uvicorn

# Access over Tailscale (operator-authorized 2026-08-28): bind the tailnet IP
# so other devices on YOUR tailnet reach the UI at http://<tailscale-ip>:8710,
# without exposing it on the home LAN or loopback:
COMPANION_BACKEND_HOST="$(tailscale ip -4)" ./run.sh
```

Port **8710**, bound to `127.0.0.1` unless `COMPANION_BACKEND_HOST` says
otherwise (see the security section — the Tailscale IP is the one sanctioned
alternative; never `0.0.0.0`). `/` serves the operator UI, `/docs` the generated API reference. The
first photo upload of a run pays a one-off ~1.4 s model load; the server kicks
that warmup at startup so it is usually already paid.

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
data/last_push/                   the two files the last push verified on the robot
data/recognizer/                  scratch instance dir for the shared recognizer
data/people.json.corrupt.<ms>     an unparseable store, kept for inspection
```

`last_push/` is not a backup — it is how a robot-side *deletion* is told apart
from a Mac-side addition (see **Sync** below), so deleting it makes the next
import treat every forgotten fact as still present. `recognizer/` exists only
because `FaceRecognizer` insists on an instance path; nothing reads the
`faces.v1.json` it would put there.

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
  404, `sync_busy` 409, `photo_too_large` 413, `invalid_request` 422,
  `robot_unreachable` 502 (carrying the ssh/scp stderr tail) and
  `robot_not_verified` 502. `GET /api/sync/status` is the exception the UI
  polls: an unreachable robot is `robot_reachable: false` there, never a 502.

  Two of those are worth expanding. `robot_not_verified` is *not*
  `robot_unreachable`: the promote reported success and the robot then did not
  hold what was sent, which needs looking at rather than clicking Push again.
  `sync_busy` is what a second concurrent push or import apply gets — those two
  routes stage files on the robot under fixed names, so a module-level lock
  serializes them and the loser is refused immediately rather than queued. The
  import *preview* (`GET`) is not serialized; it writes nothing.

  The refused-push 409 is the one non-envelope failure: it carries the push
  *result* (`pushed: false` plus `blocked_by`), because the diff that blocked it
  is what the operator acts on. A client tells the two apart by whether the body
  has a `pushed` key.

## Sync

The Mac is authoritative and the robot's two files are a projection, rebuilt
from this store on every push. Three rules govern the round trip:

- **Push is a guarded promote, not two `scp`s.** Both files go up under temp
  names; one ssh command re-checks the pre-push hashes and moves them into place
  together; the result is fetched back and counted with the robot's own readers
  before the push is recorded. It **never restarts the app** — both files are
  re-read on every use, so a pushed face and a pushed fact apply live. (Only the
  global memory prompt is session-scoped, and nothing here touches it.)
- **Push is refused whenever the robot holds content this store does not know**
  — a face enrolled by voice, an extra embedding on a face we already have, a
  fact remembered by voice. The 409 carries the diff: import, then push again.
- **Deletions travel too, one way, with two guards.** A fact recorded in
  `last_push/` that the robot no longer holds reads as a robot-side forget: it
  blocks the push, and the import applies that deletion here. It is honoured
  only while the fact is still on the Mac, and never while that person sits at
  the robot's 20-fact cap, where a deletion cannot be told from cap eviction.
  **Consequence worth knowing before you use it: a person carrying 20 or more
  facts on this Mac always projects at exactly 20, so nothing they forget by
  voice is ever read back — voice forgets only round-trip for people under the
  cap.** Face deletions are not modelled at all; no robot-side tool removes a
  person. The same one-way rule has a mirror image worth expecting: delete a
  person here while the robot has drifted and they reappear in the import
  preview — the gate errs toward keeping data it cannot prove you meant to drop
  — so delete them again after the import, then push; the preview always shows
  the record before anything is applied.

## Tests

```sh
cd companion_backend
../reachy_companion/.venv/bin/python -m pytest tests/ -v
../reachy_companion/.venv/bin/ruff check backend/ tests/ scripts/
../reachy_companion/.venv/bin/mypy --strict backend/ scripts/
```

`scripts/selftest.py` is the one check that runs the whole chain in a single
process — store → embedding → projection → the robot's own `FaceRecognizer`
matching against the projected `faces.v1.json` — with no robot involved:

```sh
../reachy_companion/.venv/bin/python scripts/selftest.py \
    --enroll-photo ~/Pictures/lena-1.jpg --probe-photo ~/Pictures/lena-2.jpg
```

The two photos must be **different real photos of the same person**. Matching a
photo against itself proves serialization, not recognition, so a run without
`--probe-photo` is reported as a REDUCED SMOKE test and is not end-to-end
evidence; a synthetic or generated face is not evidence either. Exit codes: `0`
recognized, `1` not recognized (a real signal about the pipeline or the 0.363
threshold), `3` could not run — the photo carried no usable face. Everything is
written to a temp directory (`--keep` to inspect it); `data/` is never touched.

## Security posture — trusted LAN only

`run.sh` binds `127.0.0.1` deliberately: the UI is an operator tool for *this*
Mac, and there is no authentication, no CSRF protection and no rate limiting in
front of it. Anyone who can reach the port can read and delete every stored
photo and fact, and can push arbitrary content to the robot.

- Do not change the bind address to `0.0.0.0`.
- From another machine, access it through an SSH tunnel
  (`ssh -L 8710:127.0.0.1:8710 …`) or — operator-authorized 2026-08-28 — by
  binding this Mac's **Tailscale IP** (`COMPANION_BACKEND_HOST="$(tailscale ip
  -4)" ./run.sh`). A tailnet is device-authenticated and private to the
  operator's own devices, the same trust boundary as the home LAN this posture
  already accepts; the CSRF notes below apply on tailnet devices too. Never
  `tailscale funnel` (that is the public internet), and note the `#/control`
  live panel still needs the browser to reach the robot's LAN address —
  from a remote tailnet device that means a Tailscale subnet route to the
  home LAN; without one, everything except the live panel works (app
  start/stop/restart included, since those run server-side).
- Robot access itself is plain `ssh`/`scp` over the local network and assumes a
  trusted LAN.

Now that the sync and UI surfaces exist, the residual risks are these, and they
are accepted for a POC rather than unknown:

- **The API can push to the robot, and some of it is CSRF-reachable.**
  `POST /api/sync/push` overwrites both robot stores from this store, and
  `/api/robot/{start,stop,restart}` drives the robot's app. Those four take no
  request body, so they are *simple* cross-origin requests: any page the
  operator has open can fire one at `127.0.0.1:8710` with `mode: "no-cors"` and
  the side effect happens, even though the browser hides the response. The
  JSON-bodied routes are protected only by the preflight the browser chooses to
  send, not by anything here — there is no CSRF token and no origin check.
- **The UI talks to the robot directly.** `#/control` opens
  `ws://<reachy_host>:7860/rpc` from the browser, which is the unauthenticated
  console surface D-014 already accepted; this backend widens who *finds* it, not
  what it permits.
- **The store is the real asset.** Real people's names, faces and private facts
  live in `data/`, unencrypted, and `GET /api/people/{id}/photos/{id}/file`
  serves the original bytes. A stolen laptop is the threat this posture does not
  address.
- **Rendering is the one place we do not rely on the LAN.** No `innerHTML`
  anywhere in the UI, so a name or a fact containing markup — or an ssh error
  echoing attacker-chosen bytes — is text, not script. That defence is
  deliberate and load-bearing; keep it.

## Layout

| Path | What it is |
|---|---|
| `backend/config.py` | `Settings` + `load_settings()` — robot address, data dir |
| `backend/store.py` | the JSON people store |
| `backend/embedding.py` | photo decode + SFace embedding |
| `backend/projection.py` | Mac store → robot store files |
| `backend/robot.py` | ssh/scp push, drift detection, import, the last-push snapshot |
| `backend/app.py` | the HTTP API — routes, error mapping, the shared recognizer |
| `scripts/selftest.py` | the whole chain in one process — see **Tests** |
| `static/` | the operator UI — see below |

## The operator UI

Vanilla ES modules, no framework and no build step: open
`http://127.0.0.1:8710/` and the browser loads what is on disk. The DOM
helpers, hash router and JSON-RPC client are lifted from the robot's own
console (`reachy_companion/src/reachy_companion/static/js/`) so the two read
alike; `js/rpc.js` is that console's client pointed at
`ws://{reachy_host}:7860/rpc`, with the host coming from `GET /api/config`.

| Route | What it does |
|---|---|
| `#/people` | every person, with photo/fact counts and per-photo error badges; create, delete |
| `#/people/<id>` | photo grid with per-photo status, multi-upload, delete; facts with a 280-char counter |
| `#/sync` | drift, the guarded push (a 409 renders the diff and an "Import first" button), import preview + apply |
| `#/control` | the robot app's lifecycle over REST, plus a live panel over `rpc.js` — mic, interrupt, say, transcript |

Three rules the UI is built around, all of them load-bearing:

- **No `innerHTML`, anywhere.** Display names, facts, photo filenames, robot
  transcripts and ssh stderr are all stored and rendered verbatim, and nothing
  sanitizes them at any layer. Text nodes are the whole defense, so `js/ui.js`
  deliberately drops the trusted-HTML escape hatch the console's own `h()` has.
- **Photos are re-encoded before upload.** A phone photo is stored landscape
  with an EXIF `Orientation` tag; `backend/embedding.py` decodes through ffmpeg,
  which hands back the raw unrotated pixels, so an upright-looking portrait
  reaches YuNet sideways and comes back `no_face`. The upload path runs
  `createImageBitmap(file, {imageOrientation: "from-image"})` onto a canvas and
  uploads the re-encoded JPEG, falling back to the original file whenever that
  is unavailable or throws.
- **Synthetic photos are never fetched.** A photo imported from the robot is an
  embedding with no bytes behind it, and its file route 404s by design, so those
  tiles render a placeholder instead of a broken `<img>`.
