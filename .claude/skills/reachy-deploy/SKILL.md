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

The robot's address and login are in the gitignored repo-root `.env` as
`REACHY_HOST` / `REACHY_SSH_USER` / `REACHY_SSH_PASSWORD`; **never write their
values into this file** — it is tracked, and a committed LAN address or username
is a permanent disclosure. Read them at run time
(`Get-Content .env | …`, or `$env:REACHY_HOST` once loaded) and refer to them by
key name in notes, transcripts and reports.

A fourth key, `REACHY_HOSTKEY`, holds the robot's SSH host-key fingerprint in
the exact form `plink` prints it. Every `plink`/`pscp` call here runs with
`-batch` and therefore cannot answer the host-key prompt, so they need
`-hostkey $env:REACHY_HOSTKEY`. Obtain it once from an interactive `plink`
session, put it in the repo-root `.env`, and never disable host-key checking or
paste the fingerprint back into a tracked file.

`.env.example` at the repo root is the tracked, placeholder-only template for
all four keys — copy it to `.env` and fill it in.

Windows OpenSSH prompts for the password interactively; for non-interactive
automation prefer PuTTY's `plink`/`pscp -pw` if installed, or offer the
operator one-time SSH key setup (append a public key to
`~/.ssh/authorized_keys` for the `REACHY_SSH_USER` account — optional
convenience, ask first).

**Mac mini (2026-08-24): its ed25519 key is authorized on the robot** — use
plain `ssh -o BatchMode=yes` / `scp` and nothing else. **Never wrap a bulk
transfer (wheel, media) in `expect`**: data through an scp running under
expect's pty stalls indefinitely, which masqueraded as a flaky robot radio for
two days (plain scp of the same wheel: 0.66 s). The password-driven expect
lane remains only for a robot whose `authorized_keys` was wiped (e.g. a system
reflash); re-add the key first, then deploy. Robot-pull over HTTP
(`python3 -m http.server` on the dev box + `curl` on the robot, sha-verified)
is the proven alternative when ssh is unavailable.

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
   reinstall wipes it. Six files and one directory there are user state, not
   build output, and losing any of them is a visible regression:
   - `.env` — runtime secrets (API keys, home-control config).
   - `persona.md` — the operator's edited personality / system prompt
     (`persona.py`, `PERSONA_FILENAME`, D-016). Externalizing the persona is
     what lets the character change **without** a redeploy, so a redeploy that
     eats this file silently reverts Reachy to the built-in Chinese persona.
     The revert is visible in the startup log — `persona: built-in locked
     profile` where it should read `persona: instance persona.md`.
     A git-tracked **local working copy lives at repo-root `persona.md`**;
     after editing it, sync it to the robot with
     `pscp … persona.md ${REACHY_SSH_USER}@${REACHY_HOST}:$INST/persona.md` and antenna-wake
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

   Backup — a **unique, verified, empty** directory per deployment, plus a
   redacted manifest that the restore reads. Review round 1, finding 19: the old
   ritual reused one fixed `/tmp/reachy_companion_backup` directory, so a file
   absent *today* could be restored from a **stale copy left by a previous
   deployment**; and its recursive listing printed credential filenames, which
   are account addresses, into the deploy transcript.

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
commit `2aa0403` (seventh install, 2026-08-20: D-016 externalized persona +
D-017 VoiceFX comb/soft-knee chain). Deploy-time evidence: startup log shows
`persona: instance persona.md` (seeded from the built-in persona body during
this deploy — the operator can now edit it over SSH), the full new VoiceFX
chain line (pitch +5 st, comb 4 ms g 0.45 mix 0.35, AM off, +5 dB into soft
knee at -1 dBFS), 17 tools, and a full 3-round wake check inside budget
(275/255/256 ms). The restored `.env` had its four stale `VOICEFX_*` value
lines stripped so the D-017 defaults apply (only `VOICEFX_ENABLED=true`
remains); `memory.v1.json` / `faces.v1.json` still absent (no live
enrollment yet).

Two pitfalls this deploy confirmed: Git Bash mangles a remote command whose
first token is a POSIX path (`/venvs/...` became `C:/Program Files/...` and the
force-reinstall silently didn't run — the plain install then reports "already
installed" because the wheel version never changes); run plink/pscp from
PowerShell. And plink in batch mode needs `-hostkey $env:REACHY_HOSTKEY` (see
**Access** above — the fingerprint lives in the repo-root `.env`, never here).
