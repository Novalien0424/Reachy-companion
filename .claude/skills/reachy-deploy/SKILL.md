---
name: reachy-deploy
description: Use for any work on the physical Reachy Mini robot — deploying, installing, updating, starting or stopping the reachy_companion app, syncing persona.md to it, running live tests or demo checks on it, clearing an app stop that hangs, or touching its Wi-Fi watchdog.
---

# Reachy Deploy

## Overview

`reachy_companion` goes on the physical Reachy Mini Wireless **as a managed app
only** (D-009): no daemon package or config changes, no system packages, no
service restarts beyond the app start/stop API, except clearing a wedge (Hard
limits). The daemon on port 8000 is the robot's infrastructure; it changes only
through its official updater under a recorded authorization (D-033).

Every script below is in `scripts/` and runs from the Mac with bash.

## Access

- The address and login are in the gitignored repo-root `.env` as
  `REACHY_HOST` / `REACHY_SSH_USER` / `REACHY_SSH_PASSWORD`. Read them at run
  time and name them by key in notes. Never write their values into a tracked
  file: a committed LAN address or username can't be taken back.
- The Mac's ed25519 key is authorized on the robot: plain `ssh -o BatchMode=yes`
  and `scp`. Never run a bulk transfer under `expect`: scp under its pty stalls
  indefinitely, which looks like a flaky radio. The password lane is
  only for re-adding the key after a reflash. Robot-pull over HTTP
  (`python3 -m http.server` on the Mac, `curl` + sha check on the robot) works
  when ssh doesn't.
- From the Windows PC (PuTTY, host-key pinning, the Git Bash pitfall): read
  `windows.md` first.

## Deploy

```sh
bash .claude/skills/reachy-deploy/scripts/deploy.sh
```

It builds the wheel, then:
1. checks its `reachy_mini_apps` entry point;
2. runs the version gate;
3. stops the app;
4. backs up the instance state;
5. installs in two steps;
6. verifies the install;
7. checks the daemon's discovery list.

It leaves the app stopped and prints the wheel's sha for the CHANGELOG.
- **Exit 3:** the app stop wedged (Hard limits).
- **Any other non-zero exit:** a step failed, and nothing after it ran.

What it relies on, and what to do by hand:

- **Version gate.** The daemon force-syncs the apps venv's `reachy_mini` to its
  own version (`/update/install-source`, no `/api` prefix). A daemon below
  pyproject's `reachy-mini>=` floor therefore makes the app undeployable by any
  app-level means. The script stops; report it, never upgrade.
- **Install.** Never a bare `--force-reinstall`: it reinstalls `reachy-mini`,
  whose linux `PyGObject` pin has no wheels (a forbidden source build). Use
  `--force-reinstall --no-deps`, then a plain install for genuinely missing
  deps. When a dependency is added or bumped, first prove it resolves to
  aarch64 wheels:
  `uv pip compile --python-platform aarch64-manylinux_2_28 --only-binary :all:`.
- **Instance state** lives in `~/.config/reachy_companion/` (mode 700),
  outside site-packages, so a reinstall leaves it alone. The backup
  is insurance, not a restore step. `backup_instance.sh` copies the state into
  a fresh, empty `/tmp/reachy_companion_backup/<stamp>/` with a manifest that
  holds names, modes and counts, never a credential filename. Put that
  directory in the deploy notes. What the state is:
  - `.env`: secrets.
  - `persona.md`: the operator's persona (D-016).
  - `memory.v1.json`, `people.v1.json`, `faces.v1.json`, `face_snapshots/`:
    what Reachy knows about people. Losing these is silent, and re-enrolling
    means asking everyone again.
  - `google-oauth.json`: the Drive grant.
  - `google-workspace-mcp/`: an old grant that no tool reads; deleting it is
    the operator's call.
  - `nas-video-index.json` (+ `.prev`): swapped in from the NAS at each wake
    (D-041).
  - `pending_sleep_summary.json`: present only after a timed-out sleep summary
    (D-044).
  - `hanova_media/` is deliberately skipped: it is a regenerable cache with
    keep-N caps.
- **If state is ever lost:** restore only what the manifest lists. Copy a
  directory's *contents* (`cp -a "$B/face_snapshots/." "$INST/face_snapshots/"`):
  `cp -a SRC DST` nests when DST exists. Then reassert the modes: 600 for
  `.env`, `google-oauth.json` and `nas-video-index.json`; 700 for the
  credentials directory. `/tmp` is tmpfs, so before anything that can restart
  the robot (a daemon upgrade), also copy the backup to
  `~/reachy_companion_backups/` and compare `sha256sum`s. Credentials stay on
  the robot.
- **Verify.** `verify_install.py` checks three things:
  - the installed version;
  - the record count of each store (an empty store raises nothing);
  - that the bundled search tool serves the wheel's description (`示範語氣=True`,
    D-031).

  After the next start, the startup log must say `persona: instance
  persona.md`. `built-in locked profile` means the persona was lost.
- **Persona.** The tracked working copy is repo-root `persona.md`.
  - `persona_sync.sh push` copies it to the robot. It loads at the next wake,
    with no rebuild.
  - `persona_sync.sh status` compares the two copies and names the repo
    revision a stale robot copy matches.

  It reads `~/.config/reachy_companion/persona.md`. The old copy in the
  package directory is read by nothing, so never compare against it.
- **Before demos**, preload assets: scp `scripts/preload_assets.py` (repo
  root) and run it with `/venvs/apps_venv/bin/python` as the app's user. It
  fetches the emotion clips, YuNet, SFace (about 37 MB) and the pinned Silero
  VAD. A cold cache is a visible stall, and the first greeting after a deploy
  misses its budget.
- **Start / stop:** `POST /api/apps/start-app/reachy_companion` and
  `POST /api/apps/stop-current-app`, or the dashboard. Nothing else.
- **Wi-Fi watchdog (D-034), when `wifi_watchdog.py` changed:** scp it to
  `~/.local/lib/reachy-wifi-watchdog/` and run it once with `--dry-run`. Its
  crontab line is the operator's to install or remove
  (`watchdog_cron.sh install|remove`). `watchdog_cron.sh status` is read-only
  and fine for an agent.

## Live verification

Every behavior change is judged on the robot, by mini, in fresh sessions. Use
the cheapest tier that can show the change (D-044):

1. **`device_check.sh nas|youtube|speaker|both|all|sleep|paused-sleep`.**
   Drives the playback tools on the real TV and speaker through the test-only
   `qa.tool` RPC: zero model tokens.
2. **`qa_replay.py` + `qa_cases.json`, on the robot, text only.** Model-choice
   cases replayed from the installed wheel (usage in its docstring).
   `--override cand.json` measures a candidate wording before it is built: run
   the same cases with and without it. Replay can overstate a live rate, so
   confirm a gain live.
3. **`live_case.sh 1 "<話>" [wait]`** (fresh session) / **`live_case.sh 0 "<話>"`**
   (next turn), for what needs the live model or real audio. It mutes the mic
   and sends the text over `/rpc conversation.say`, which also flushes audio, so
   it doubles as a barge-in. It prints, with LAN addresses and URLs redacted:
   - tool calls and transcripts;
   - `[usage in= in_cached= out_audio=]` per response, plus `status=incomplete`
     when the output-token rail cut a reply;
   - the app's WARNING/ERROR lines;
   - the TV state.

Reading the results:
- **k/n, 3–4 fresh runs per case**, next to the previous build's rate. One
  pass on mini is an anecdote.
- **A spoken preamble** is `out_audio` > 0 on the response that carries the
  tool call, and its words are the `Preamble transcript` line. That line is
  the only place they appear: they never reach the transcript or memory.
- **The prefix size** is the first `usage in=` of a fresh session
  (`in_cached=0`). Compare builds with it, not with JSON characters (D-039).
- **Timing:** in a fresh session the greeting is still playing when the text
  arrives, so measure delays on a second turn.
- **Exit 3 = the stop wedged.** Restart the daemon (Hard limits), record the
  time and the stop's body, and rerun.

Ending a run:
- `live_case.sh cleanup` unmutes the mic (skipped when the app sleeps; the
  mute doesn't outlive the app), prints the TV state and removes the robot
  copies.
- `live_case.sh tv-off` only if the TV was off when testing began.
- Remove any test memory fact you saved. While someone is recognized, skip
  tests that save memory: the fact would land in their record.
- **Records:** copy tool calls and transcripts into notes, never raw journal
  lines. The journal holds LAN addresses and the TV's entity ID. Trip, clip
  and folder names from the NAS index stay out, and so do people's names.

Shell habits that cost time here:
- Python on the robot: write the file locally, scp it, and run it with
  `/venvs/apps_venv/bin/python -I`. Heredocs inside `ssh '…'` break on quoting.
- Build paths from `$HOME`, never a typed home path.
- Quote globs in zsh (`--include='*.py'`): one that matches nothing aborts the
  command.
- Redact with `LC_ALL=C sed`: macOS sed rejects the journal's bytes otherwise.
- Never pass `-p no:logging` to pytest: it disables `caplog`.

## Hard limits

- **No pip into the daemon's environment.** Only `/venvs/apps_venv` is ours.
  Daemon updates go through its own updater (`/update/…`), and only under a
  recorded authorization (D-033 holds the last one). For any other daemon
  change, propose it; never do it.
- **No edits under the daemon's installation or its config.** The startup app
  is set only through `PUT /api/apps/startup-app`, and only when the operator
  asks.
- **No reboots and no killing daemon processes.** A wedge (the app state stuck
  at `stopping`) is cleared with `sudo -n systemctl restart reachy-mini-daemon`
  under the standing authorization (2026-10-30, D-038). Record the UTC time and
  the stop's body, and tell the operator. `POST /api/daemon/restart` does not
  clear it. Afterwards the app is `null`; start it with the API.
- **Nothing persistent on the robot from us:** no cron lines, services or
  startup hooks. Before proposing one, say why the app can't do the job.
  Installing one stays the operator's step.
- Port 8000 on the robot is the real daemon; port 8001 on the dev machine is
  the mockup-sim daemon.

## Rollback

- **App:** `/venvs/apps_venv/bin/python -m pip uninstall -y reachy_companion`
  on the robot, then check the installed-apps list.
- **Daemon:** the updater cannot return to `v1.10.0rc5`.
  `POST /update/start-from-ref?git_ref=v1.12.0` is the fallback that sticks; a
  true rc5 restore needs its own authorization. The pre-upgrade freezes are in
  `~/reachy_companion_backups/<stamp>/`.

## Status

The procedure is proven and app-only. The current build and its deploy
evidence are in `progress.md` and `CHANGELOG.md`; the history is in D-009 and
D-033.
- **Daemon:** v1.13.0 (git ref `v1.13.0`, telemetry off), and the startup app
  is `reachy_companion`.
- **After a daemon upgrade:** if it changes `/bluetooth/` or the gpio-shutdown
  unit, one privileged step follows, because the image's sudoers can't run its
  `sudo cp` (D-033).
