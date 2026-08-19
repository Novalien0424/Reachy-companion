---
name: reachy-deploy
description: Use when deploying, installing, updating, starting, or stopping the reachy_companion app on the physical Reachy Mini robot — "deploy to the robot", "install the app on reachy", "push to the robot", "run it on the real robot" — or when preparing on-robot demo verification (plan Task 15).
---

# Reachy Deploy

## Overview

Deploy `reachy_companion` to the physical Reachy Mini Wireless **as a managed
app only**. Operator authorization (2026-08-17): deploy as APP on the robot;
**never modify the robot's daemon** — no daemon package changes, no daemon
config edits, no service restarts beyond the app start/stop API, no system
packages. The robot's own daemon on its port 8000 is untouchable
infrastructure.

## Access

Read robot credentials from the repo-root `.env` (gitignored, never commit):
`REACHY_HOST` (10.0.0.96), `REACHY_SSH_USER` (pollen), `REACHY_SSH_PASSWORD`.
Windows OpenSSH prompts for the password interactively; for non-interactive
automation prefer PuTTY's `plink`/`pscp -pw` if installed, or offer the
operator one-time SSH key setup (append a public key to
`~/.ssh/authorized_keys` for the pollen user — optional convenience, ask
first).

## Deployment procedure (matches plan Task 15 / D-009)

1. **Build on dev machine:** `uv build ./reachy_companion` → wheel in
   `reachy_companion/dist/`. Verify the entry point locally first:
   `entry_points(group='reachy_mini_apps')` must list `reachy_companion`
   (daemon discovers apps by this group — research-reachy-sdk §1).
2. **Version gate (DECISIVE, not advisory):** the app requires the SDK line
   pinned in `reachy_companion/pyproject.toml` (currently
   `reachy-mini>=1.10.0rc2`; dev venv runs 1.10.0rc5). The robot's daemon
   version comes from `GET http://$REACHY_HOST:8000/update/install-source`
   (NO `/api` prefix — the update/cache/logs/wifi routers mount bare, unlike
   `apps`; there is no `/api/daemon/version` route). The gate is decisive
   because `check_and_sync_apps_venv_sdk()` force-syncs the apps venv's
   `reachy_mini` to the daemon's version on EVERY daemon boot
   (`utils/wireless_version/startup_check.py:388`) — a daemon below the floor
   makes the app undeployable by any app-level means. If below floor, STOP
   and report — upgrading the daemon is NOT authorized.
3. **Transfer:** `scp reachy_companion/dist/reachy_companion-*.whl
   ${REACHY_SSH_USER}@${REACHY_HOST}:/tmp/` (PuTTY `pscp -pw` works on the
   dev box; PuTTY is installed).
4. **Back up instance state BEFORE installing (mandatory).** The instance path
   IS the installed package directory
   (`/venvs/apps_venv/lib/python3.X/site-packages/reachy_companion/` —
   `app.py:169` + `main.py:448`), so it sits *inside* site-packages and every
   reinstall wipes it. Two files there are user state, not build output, and
   losing either is a visible regression:
   - `.env` — runtime secrets (API keys, home-control config).
   - `memory.v1.json` — the long-term facts the `remember`/`forget` tools
     wrote (`memory.py:19`, `MEMORY_FILENAME`). **Memory must survive a
     redeploy**; a user who told Reachy their name last week must not have to
     say it again because we shipped a wheel.
   - `faces.v1.json` — the enrolled faces the `remember_face` tool wrote
     (`faces.py:33`, `FACES_FILENAME`, D-013). Same rule, higher cost to lose:
     re-enrolling means asking every person to stand in front of the camera
     again, and the wake-time greeting silently stops using anyone's name.

   ```sh
   INST=$(/venvs/apps_venv/bin/python -c \
     "import reachy_companion, pathlib; print(pathlib.Path(reachy_companion.__file__).parent)")
   mkdir -p /tmp/reachy_companion_backup
   cp -a "$INST/.env" /tmp/reachy_companion_backup/ 2>/dev/null || echo "no .env yet"
   cp -a "$INST/memory.v1.json" /tmp/reachy_companion_backup/ 2>/dev/null || echo "no memory yet"
   cp -a "$INST/faces.v1.json" /tmp/reachy_companion_backup/ 2>/dev/null || echo "no faces yet"
   ls -l /tmp/reachy_companion_backup
   ```

   On a first deploy all three are absent — record that explicitly in the deploy
   notes rather than treating a missing file as a failed backup.
5. **Install into the shared apps venv** (an app-level action, allowed) —
   NEVER bare `--force-reinstall` (it reinstalls `reachy-mini` too, whose
   linux `PyGObject>=3.42.2,<=3.46.0` pin has NO wheels → forbidden source
   build). Two-step instead:
   `/venvs/apps_venv/bin/python -m pip install --force-reinstall --no-deps /tmp/reachy_companion-*.whl`
   then `/venvs/apps_venv/bin/python -m pip install /tmp/reachy_companion-*.whl`
   (pulls only genuinely missing deps; all 43 resolve as aarch64 wheels —
   verified 2026-08-17 via `uv pip compile --python-platform
   aarch64-manylinux_2_28 --only-binary :all:`).
6. **Restore instance state immediately after installing, before starting.**
   Same `$INST` (recompute it — the python minor version in the path can move):

   ```sh
   cp -a /tmp/reachy_companion_backup/.env "$INST/.env"
   cp -a /tmp/reachy_companion_backup/memory.v1.json "$INST/memory.v1.json"
   cp -a /tmp/reachy_companion_backup/faces.v1.json "$INST/faces.v1.json"
   ls -l "$INST/.env" "$INST/memory.v1.json" "$INST/faces.v1.json"
   ```

   Skip whichever file the backup step reported as absent. Verify by reading
   **both** stores back — record counts, not just file presence:

   ```sh
   /venvs/apps_venv/bin/python - <<'PY'
   import json, pathlib, reachy_companion
   inst = pathlib.Path(reachy_companion.__file__).parent
   for name, key in (("memory.v1.json", "facts"), ("faces.v1.json", "faces")):
       path = inst / name
       if not path.is_file():
           print(f"{name}: absent"); continue
       data = json.loads(path.read_text(encoding="utf-8"))
       print(f"{name}: {len(data.get(key, []))} records")
   PY
   ```

   An empty file is silent data loss, not a visible error: memory is injected
   into every session's instructions via `prompts.get_session_instructions` →
   `memory.format_memory_for_prompt`, and faces are read by
   `face_id.FaceRecognizer.match` → `faces.list_faces`. Neither failure raises
   anything. Never bake secrets, memory or faces into the wheel.
7. **Verify discovery:** `GET http://$REACHY_HOST:8000/api/apps/list-available/installed`
   (route per SDK `daemon/app/routers/apps.py:49-58`) lists `reachy_companion`.
8. **Preload assets before demos:** scp `scripts/preload_assets.py` to the
   robot and run with `/venvs/apps_venv/bin/python` as the same user the app
   runs as — emotion clips, the YuNet detector, and the ~37 MB SFace
   recognition model (D-013); a cold HF cache is a visible stall, and for
   SFace it also means the wake-time greeting misses its budget on the first
   session after a redeploy.
9. **Start / stop:** `POST /api/apps/start-app/reachy_companion` /
   `POST /api/apps/stop-current-app` — or the dashboard. This is the ONLY
   sanctioned start/stop mechanism.

## Hard limits

- Never `pip install`/upgrade anything into the daemon's environment; only
  `/venvs/apps_venv` (shared apps venv) is writable by us.
  **Scoped exception (operator-authorized 2026-08-17):** a one-time daemon
  update to the 1.10.0rc line is authorized, ONLY via the robot's own
  official updater (`/update/start-from-ref` family) — never via pip/ssh
  surgery. Verify the rollback path (ref back to 1.9.0) BEFORE updating.
  This exception does not extend to any other daemon change.
- Never edit files under the daemon's installation or its config
  (`daemon_config.json` startup-app entry may be set via the official
  `PUT /api/apps/startup-app` API only, and only if the operator asks).
- Never reboot the robot or kill daemon processes; if the daemon is wedged,
  report to the operator.
- Robot port 8000 = robot daemon (real). Dev machine port 8001 = local
  mockup-sim daemon. Do not confuse them.

## Rollback

`ssh … "/venvs/apps_venv/bin/python -m pip uninstall -y reachy_companion"`
then re-verify the installed-apps list. The daemon itself is never touched,
so rollback is always app-only.

## Status

Current as of 2026-08-19.

Deployment works and this procedure is proven. Five successful installs to
date, all app-only: the first deploy (Task 15 attempt 3), operator rounds 1 and
2, the Task 17 face-memory deploy, and the Task 17 fix-round redeploy. The two
earlier attempts that failed did so before touching the robot — attempt 1 on
reachability, attempt 2 on the version gate. Full history in DECISIONS.md D-009.

Robot state: daemon on the **1.10.0rc line** (git-source install, so the version
gate passes and future daemon syncs follow the git ref); startup app is
`reachy_companion`; the robot was last left **ASLEEP** running the build of
commit `f37e6d9`.

Local `main` is ahead of the robot and **not yet deployed**: the PRD/README docs
commit `fb6fd69`, the six audit fixes `a5f682d`, and the docs-alignment commit
that follows them. Nothing has been installed since `f37e6d9`, so the next
deployment resumes at **Step 1** and carries all three.
