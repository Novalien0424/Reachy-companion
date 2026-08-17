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
4. **Install into the shared apps venv** (an app-level action, allowed) —
   NEVER bare `--force-reinstall` (it reinstalls `reachy-mini` too, whose
   linux `PyGObject>=3.42.2,<=3.46.0` pin has NO wheels → forbidden source
   build). Two-step instead:
   `/venvs/apps_venv/bin/python -m pip install --force-reinstall --no-deps /tmp/reachy_companion-*.whl`
   then `/venvs/apps_venv/bin/python -m pip install /tmp/reachy_companion-*.whl`
   (pulls only genuinely missing deps; all 43 resolve as aarch64 wheels —
   verified 2026-08-17 via `uv pip compile --python-platform
   aarch64-manylinux_2_28 --only-binary :all:`).
5. **Verify discovery:** `GET http://$REACHY_HOST:8000/api/apps/list-available/installed`
   (route per SDK `daemon/app/routers/apps.py:49-58`) lists `reachy_companion`.
6. **App config:** put runtime secrets in the app instance's `.env`. The
   instance path IS the installed package directory
   (`/venvs/apps_venv/lib/python3.X/site-packages/reachy_companion/` —
   `app.py:169` + `main.py:448`), so it exists immediately after install —
   but it lives inside site-packages, so **every reinstall wipes `.env`;
   re-place it after each install.** Never bake secrets into the wheel.
7. **Preload assets before demos:** scp `scripts/preload_assets.py` to the
   robot and run with `/venvs/apps_venv/bin/python` as the same user the app
   runs as (emotion clips + YuNet model; cold HF cache = visible stall).
8. **Start / stop:** `POST /api/apps/start-app/reachy_companion` /
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

Attempt 1 (2026-08-17): BLOCKED at reachability — robot offline (no ARP, no
mDNS, all ports timeout). Local prep complete: wheel built (pure
py3-none-any, entry point verified), aarch64 dep tree fully wheel-resolvable.
Procedure corrected from SDK source (version route, --no-deps install,
instance-path/.env lifecycle) — see DECISIONS.md D-009 Attempt 1. Next
attempt: power the robot, then resume at Step 1.
