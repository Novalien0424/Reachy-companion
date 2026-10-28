---
name: reachy-deploy
description: Use when deploying, installing, updating, starting, or stopping the reachy_companion app on the physical Reachy Mini robot — "deploy to the robot", "install the app on reachy", "push to the robot", "run it on the real robot", syncing persona.md to the robot — or when preparing on-robot demo verification.
---

# Reachy Deploy

## Overview

Deploy `reachy_companion` to the physical Reachy Mini Wireless **as a managed
app only** (operator authorization 2026-08-17, D-009): no daemon package
changes outside the official updater, no daemon config edits, no service
restarts beyond the app start/stop API, no system packages. The daemon on
port 8000 is the robot's infrastructure; it is upgraded only as authorized in
Hard limits below (D-033).

## Access

The robot's address and login are in the gitignored repo-root `.env` as
`REACHY_HOST` / `REACHY_SSH_USER` / `REACHY_SSH_PASSWORD`. Never write their
values into a tracked file — a committed LAN address or username is a
permanent disclosure. Read them at run time
(`REACHY_HOST=$(grep '^REACHY_HOST=' .env | cut -d= -f2-)`) and refer to them
by key name in notes, transcripts and reports. `.env.example` is the tracked,
placeholder-only template.

**The Mac's ed25519 key is authorized on the robot** (2026-08-24): use plain
`ssh -o BatchMode=yes` / `scp`. Never wrap a bulk transfer (wheel, media) in
`expect`: data through an scp running under expect's pty stalls indefinitely,
which masqueraded as a flaky robot radio for two days (plain scp of the same
wheel: 0.66 s). The password-driven expect lane is only for a robot whose
`authorized_keys` was wiped (e.g. a system reflash) — re-add the key first,
then deploy. Robot-pull over HTTP (`python3 -m http.server` on the dev box +
`curl` on the robot, sha-verified) is the proven alternative when ssh is
unavailable.

Deploying from the Windows PC (PuTTY, host-key pinning, Git Bash pitfall):
read `windows.md` in this skill's directory first.

## Deployment procedure (D-009)

1. **Build on dev machine:** `uv build ./reachy_companion` → wheel in
   `reachy_companion/dist/`. Verify the entry point locally first:
   `entry_points(group='reachy_mini_apps')` must list `reachy_companion`
   (daemon discovers apps by this group — research-reachy-sdk §1).
2. **Version gate (DECISIVE, not advisory):** the app requires the SDK line
   pinned in `reachy_companion/pyproject.toml` (the `reachy-mini>=` floor).
   The robot's daemon
   version comes from `GET http://$REACHY_HOST:8000/update/install-source`
   (NO `/api` prefix — the update/cache/logs/wifi routers mount bare, unlike
   `apps`; there is no `/api/daemon/version` route). The gate is decisive
   because the daemon force-syncs the apps venv's `reachy_mini` to its own
   version (rc5: on every boot; v1.13: whenever reachy_mini's install record
   changes, `startup_check.py` `_dist_record_sig`) — a daemon below the floor
   makes the app undeployable by any app-level means. If below floor, STOP
   and report; a daemon upgrade needs a recorded authorization (Hard limits).
3. **Transfer:** `scp reachy_companion/dist/reachy_companion-*.whl
   ${REACHY_SSH_USER}@${REACHY_HOST}:/tmp/`.
4. **Back up instance state BEFORE installing (mandatory).** Where the state
   lives depends on the installed release. From v1.25.0 it is
   `~/.config/reachy_companion/` (mode 700; `config.resolve_instance_path`,
   startup log `instance: ~/.config/reachy_companion (…)`), outside
   site-packages, so a reinstall no longer touches it: back it up with the
   script below with `INST=~/.config/reachy_companion` (for safety, not for
   restore) and skip step 6. Up to v1.24.0 the instance path IS the
   installed package directory
   (`/venvs/apps_venv/lib/python3.X/site-packages/reachy_companion/` —
   `app.py:169` + `main.py:448`), every reinstall can wipe it, and step 6 is
   mandatory; the first v1.25.0 start migrates it once (`(migrated N items)`,
   then `(already migrated)`; plan `docs/plans/2026-10-28-instance-path-plan.md`).
   These files and directories are user state, not build output, and losing
   any of them is a visible regression:
   - `.env` — runtime secrets (API keys, home-control config).
   - `persona.md` — the operator's edited personality / system prompt
     (`persona.py`, `PERSONA_FILENAME`, D-016). Externalizing the persona is
     what lets the character change **without** a redeploy, so a redeploy that
     eats this file silently reverts Reachy to the built-in Chinese persona.
     The revert is visible in the startup log — `persona: built-in locked
     profile` where it should read `persona: instance persona.md`.
     A git-tracked **local working copy lives at repo-root `persona.md`**;
     after editing it, sync it to the robot with
     `scp persona.md ${REACHY_SSH_USER}@${REACHY_HOST}:$INST/persona.md` and antenna-wake
     (or restart the app) to load it — no wheel rebuild. Keep the two copies in
     step: whichever side was edited last wins, and this repo copy is the one
     under version control.
   - `memory.v1.json` — the long-term facts the `remember`/`forget` tools
     wrote (`memory.py:19`, `MEMORY_FILENAME`). **Memory must survive a
     redeploy**; a user who told Reachy their name last week must not have to
     say it again because we shipped a wheel.
   - `faces.v1.json` — the enrolled faces the `remember_face` tool wrote
     (`faces.py:33`, `FACES_FILENAME`, D-013). Same rule, higher cost to lose:
     re-enrolling means asking every person to stand in front of the camera
     again, and the wake-time greeting silently stops using anyone's name.
   - `people.v1.json` — the per-person facts store (`people.py`, D-025).
     Person-scoped memory written by the `remember` tool while someone is
     recognized, and pushed by the Mac backend. Losing it silently strips the
     personalized greeting of its content.
   - `face_snapshots/` (directory) — one enrollment snapshot JPEG per person
     (`face_snapshot.py`, D-026). Lost snapshots are not recoverable until
     that person re-enrolls; the Mac backend may already hold imported
     copies, but the robot-side originals are the source.
   - `google-workspace-mcp/<account>.json` — the Google Calendar/Tasks OAuth
     credentials (D-018). **This file is rewritten by the app** every time the
     access token is refreshed, so the robot's copy is authoritative for its own
     expiry and must survive a redeploy. Losing it means re-running the OAuth
     bootstrap by hand; both calendar and task tools answer
     `unavailable`/`not_configured` until it is back.
   - `google-oauth.json` — the Drive OAuth secret (D-018), a **separate** grant
     carrying full `https://www.googleapis.com/auth/drive` scope. Losing it
     disables `drive_list`, `drive_trash` and `drive_upload`.
   - `nas-video-index.json` — the operator-supplied home-video index (D-018).
     Not a credential, but personal data and not reproducible on the robot: it is
     built on the operator's own machine. Losing it disables all four `nas_*`
     tools.

   Deliberately **not** backed up: `hanova_media/` (the downloaded music, staged
   NAS clips and generated images). It is a regenerable cache with keep-N caps,
   and copying it would carry hundreds of megabytes through every deploy. Record
   that it was intentionally skipped rather than treating it as a missed file.

   Backup — a unique, verified, empty directory per deployment, plus a
   redacted manifest that the restore reads, so a stale copy from an earlier
   deploy can never come back and no credential filename (they are account
   addresses) reaches the transcript.

   ```sh
   INST=$(/venvs/apps_venv/bin/python -c \
     "import reachy_companion, pathlib; print(pathlib.Path(reachy_companion.__file__).parent)")

   STAMP="$(date -u +%Y%m%dT%H%M%SZ)-$$"
   BACKUP="/tmp/reachy_companion_backup/$STAMP"
   mkdir -p "$BACKUP" && chmod 700 "$BACKUP"
   # Finding 19: a fresh directory every time. A stale file from a previous deploy
   # must never be able to overwrite a file that is legitimately absent now.
   [ -z "$(ls -A "$BACKUP")" ] || { echo "FATAL: backup dir not empty"; exit 1; }

   : > "$BACKUP/manifest.txt"
   for NAME in .env persona.md memory.v1.json faces.v1.json people.v1.json google-oauth.json nas-video-index.json; do
     if [ -e "$INST/$NAME" ]; then
       cp -a "$INST/$NAME" "$BACKUP/$NAME"
       echo "file $NAME $(stat -c '%a %s' "$INST/$NAME")" >> "$BACKUP/manifest.txt"
     else
       echo "absent $NAME" >> "$BACKUP/manifest.txt"
     fi
   done
   if [ -d "$INST/google-workspace-mcp" ]; then
     # Safe in THIS direction only: $BACKUP was just created and verified empty
     # above, so the destination cannot exist and `cp -a SRC DST` copies rather
     # than nests. The restore below is the direction where that matters.
     cp -a "$INST/google-workspace-mcp" "$BACKUP/google-workspace-mcp"
     # Finding 19: a COUNT, not a listing. The filenames are account addresses.
     echo "dir google-workspace-mcp $(find "$INST/google-workspace-mcp" -type f | wc -l) files" >> "$BACKUP/manifest.txt"
   else
     echo "absent google-workspace-mcp" >> "$BACKUP/manifest.txt"
   fi
   if [ -d "$INST/face_snapshots" ]; then
     cp -a "$INST/face_snapshots" "$BACKUP/face_snapshots"
     echo "dir face_snapshots $(find "$INST/face_snapshots" -type f | wc -l) files" >> "$BACKUP/manifest.txt"
   else
     echo "absent face_snapshots" >> "$BACKUP/manifest.txt"
   fi
   echo "$BACKUP"
   cat "$BACKUP/manifest.txt"
   ```

   The robot's `/tmp` is tmpfs, so a reboot or daemon restart can erase this
   backup. Before any step that can restart the robot (a daemon upgrade), also
   `cp -a "$BACKUP" ~/reachy_companion_backups/` (mode 700, ext4) and compare
   `sha256sum` of both copies. Keep it on the robot: copying credentials off
   the device is not part of this procedure.

   The manifest is the record the Verification Gate wants: every `absent` line is
   an explicitly recorded absence rather than a missed file, and it names no
   credential. Copy `$BACKUP` into the deploy notes — the restore needs it. On a
   first deploy every entry is `absent`; record that explicitly rather than
   treating a missing file as a failed backup.
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

   Restore — **conditional, driven by the manifest, with permissions
   reasserted** (finding 19: the old restore block was unconditional even though
   the backup block tolerated missing files):

   ```sh
   # $BACKUP is the exact directory printed by the backup step. Never a glob.
   [ -f "$BACKUP/manifest.txt" ] || { echo "FATAL: no manifest; refusing to restore"; exit 1; }

   while read -r KIND NAME REST; do
     case "$KIND" in
       file)
         # Finding 19: only what the manifest recorded, and only if it is still there.
         [ -e "$BACKUP/$NAME" ] && cp -a "$BACKUP/$NAME" "$INST/$NAME" || echo "MISSING in backup: $NAME"
         ;;
       dir)
         # `cp -a SRC DST` NESTS SRC inside DST when DST already exists, and it
         # does exist here: google-workspace-mcp is created by the app at run
         # time, so it is not in pip's RECORD and --force-reinstall leaves it in
         # place. The naive form silently produces
         # $INST/google-workspace-mcp/google-workspace-mcp/ and restores nothing.
         # Copy the CONTENTS instead, into a destination we ensure exists.
         if [ -d "$BACKUP/$NAME" ]; then
           mkdir -p "$INST/$NAME"
           cp -a "$BACKUP/$NAME/." "$INST/$NAME/"
         else
           echo "MISSING in backup: $NAME"
         fi
         ;;
       absent)
         echo "was absent before this deploy, not restored: $NAME"
         ;;
     esac
   done < "$BACKUP/manifest.txt"

   # Finding 19: restrictive modes reasserted every time, on the directory too.
   [ -d "$INST/google-workspace-mcp" ] && chmod 700 "$INST/google-workspace-mcp"
   [ -d "$INST/google-workspace-mcp" ] && find "$INST/google-workspace-mcp" -type f -exec chmod 600 {} +
   for NAME in .env google-oauth.json nas-video-index.json; do
     [ -f "$INST/$NAME" ] && chmod 600 "$INST/$NAME"
   done

   # Names and modes of the instance directory only -- never a recursive listing of
   # the credentials directory (finding 19).
   ls -l "$INST" | grep -v '^total'
   ```

   The manifest decides what comes back, so a file the backup recorded as
   `absent` stays absent. Verify by reading
   **both** JSON stores back — record counts, not just file presence:

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
   anything. `persona.md` is the one that does announce itself — read the
   `persona:` line in the startup log to confirm the restore took. Never bake
   secrets, an edited persona, memory or faces into the wheel.
6b. **Verify the model-visible tool descriptions (plan rev 3 B2, review r1
   finding 7).** A robot that already carries an `installed_tool_spaces.json`
   manifest used to serve the CACHED description for the bundled search tool,
   so a description edit shipped in the wheel never reached the model there.
   Since D-031 the bundled spec overrides the cache at read time; prove it on
   the robot, not on the dev box:

   ```sh
   /venvs/apps_venv/bin/python - <<'PY'
   import pathlib, reachy_companion
   from reachy_companion import tool_spaces
   inst = pathlib.Path(reachy_companion.__file__).parent
   manifest = tool_spaces.read_installed_tool_spaces(inst)
   for space in manifest.spaces:
       if space.slug in tool_spaces.PREINSTALLED_TOOL_SPACE_SPECS:
           for tool in space.tools:
               print(space.slug, tool.client_tool_name, "示範語氣" in tool.description, tool.description[:80])
   PY
   ```

   The bundled **search** tool must print `True` (it is the one that carries a
   示範語氣 preamble phrase; the bundled time and weather tools are fast and
   print `False` by design). A `False` on search means the robot would talk to
   a stale description and the install is not done. Also confirm
   `manifest file present:` — when it is `False` the override had nothing to
   do and the baked-in spec was served directly.
7. **Verify discovery:** `GET http://$REACHY_HOST:8000/api/apps/list-available/installed`
   (route per SDK `daemon/app/routers/apps.py:49-58`) lists `reachy_companion`.
8. **Preload assets before demos:** scp `scripts/preload_assets.py` to the
   robot and run with `/venvs/apps_venv/bin/python` as the same user the app
   runs as — emotion clips, the YuNet detector, the ~37 MB SFace
   recognition model (D-013) and, from v1.25.0, the sha256-pinned Silero VAD
   model (`cached: Silero VAD model`); a cold cache is a visible stall, and for
   SFace it also means the wake-time greeting misses its budget on the first
   session after a redeploy.
9. **Start / stop:** `POST /api/apps/start-app/reachy_companion` /
   `POST /api/apps/stop-current-app` — or the dashboard. This is the ONLY
   sanctioned start/stop mechanism.
10. **Refresh the Wi-Fi watchdog** (D-034) when `wifi_watchdog.py` changed:
   `scp reachy_companion/src/reachy_companion/wifi_watchdog.py
   ${REACHY_SSH_USER}@${REACHY_HOST}:.local/lib/reachy-wifi-watchdog/` and run
   it once with `--dry-run`. It runs from the operator-installed crontab line
   tagged `# reachy-wifi-watchdog` (installing or changing that line is the
   operator's step); `journalctl -t reachy-wifi-watchdog` shows its
   transitions, `--status` its state.

## Hard limits

- Never `pip install`/upgrade anything into the daemon's environment; only
  `/venvs/apps_venv` (shared apps venv) is writable by us.
  Daemon updates happen only through the robot's own official updater
  (`/update/…`), never pip/ssh surgery, and only under a recorded
  authorization: the 2026-08-17 move to the 1.10.0rc line (used), and the
  2026-10-28 upgrade to the latest Pollen release (D-033 — back up instance
  state and record the rollback ref first). Any other daemon change needs its
  own operator authorization; propose it, never do it.
- Never edit files under the daemon's installation or its config
  (`daemon_config.json` startup-app entry may be set via the official
  `PUT /api/apps/startup-app` API only, and only if the operator asks).
- Never reboot the robot or kill daemon processes; if the daemon is wedged,
  report to the operator.
- Robot port 8000 = robot daemon (real). Dev machine port 8001 = local
  mockup-sim daemon. Do not confuse them.

## Rollback

App: `ssh … "/venvs/apps_venv/bin/python -m pip uninstall -y reachy_companion"`
then re-verify the installed-apps list.

Daemon (only after an authorized upgrade): the official updater cannot return
to `v1.10.0rc5` — its `starlette<1.0.0` fails the updater's `uv pip check`,
and the fallback `--upgrade` reinstalls PyPI's latest. The official fallback
that sticks is `POST /update/start-from-ref?git_ref=v1.12.0`. A true rc5
restore is a manual pinned install and needs its own authorization. Pre-upgrade
freezes of both venvs and `daemon_config.json` sit with the instance backup in
`~/reachy_companion_backups/<stamp>/` on the robot.

## Status

This procedure is proven, app-only. The current install count, the build on
the robot and its last-deploy evidence live in `progress.md` (newest first);
the deployment history is DECISIONS.md D-009 and D-033. The daemon is on
v1.13.0 (git-source install, ref `v1.13.0`; telemetry off via
`PUT /api/telemetry`); startup app is `reachy_companion`. A daemon upgrade
that changes `/bluetooth/` or the gpio-shutdown unit needs one privileged
step afterwards, because the image's NOPASSWD sudoers cannot run its
`sudo cp` (D-033 outcome).
