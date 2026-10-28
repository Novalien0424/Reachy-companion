# Instance data out of site-packages — plan (2026-10-28)

Port of upstream `5ab905d` (#552) `resolve_instance_path` /
`_migrate_instance_data` (`reachy_mini_conversation_app` `config.py`), the
storage half only (no settings store, JSON-RPC settings or routes).

Why: the instance directory today is the installed package directory
(`main.py:602` `self._get_instance_path().parent` →
`/venvs/apps_venv/…/site-packages/reachy_companion/`), so every reinstall can
wipe the operator's `.env`, `persona.md`, faces, people, OAuth grants and the
NAS index; each deploy carries a manual backup/restore ritual (deploy skill
steps 4 and 6) to survive that. A Bluetooth `SOFTWARE_RESET` (`rm -rf /venvs`)
would destroy all of it.

## Design
- `config.resolve_instance_path(legacy)`: target `~/.config/reachy_companion`
  (`platformdirs.user_config_dir("reachy_companion")`; platformdirs already
  ships with reachy-mini), override `REACHY_COMPANION_INSTANCE_PATH`. Unusable
  target → warn and fall back to `legacy` (a bad path must not silence the
  robot). Our own directory, not upstream's sibling inside the daemon's
  `~/.config/reachy_mini`.
- `_migrate_instance_data(legacy, target)`: upstream's algorithm verbatim
  (per-item staging copy + rename, `.migrated` marker, never overwrite an
  existing destination), over OUR item list: `.env`, `persona.md`,
  `memory.v1.json`, `faces.v1.json`, `people.v1.json`, `face_snapshots/`,
  `google-workspace-mcp/`, `google-oauth.json`, `nas-video-index.json`,
  `installed_tool_spaces.json` (and any other state file the code writes into
  the instance path — the implementer inventories every writer). Not
  migrated: `hanova_media/` (regenerable cache; it simply starts empty in the
  new place). Credential files keep mode 600, directories 700.
- `main.py:602` resolves through it. Every module already receives
  `instance_path` as a parameter; any module that derives the package dir on
  its own for state is a bug to fix in the same change.
- Consumers outside the app move too: `companion_backend/backend/config.py:24`
  `INSTANCE_DIR` and `scripts/voice_audition.py:36` `INST` → the new path.
- Startup log line names the instance directory and whether a migration ran
  (`instance: ~/.config/reachy_companion (migrated N items)`).

## Rollout (deploy of the release carrying this)
Back up and restore exactly as today (legacy path), start the app once →
migration copies into `~/.config/reachy_companion` → verify the new
directory's files, modes and record counts, and the `persona: instance
persona.md` line. From then on the deploy skill backs up the new directory
(it survives reinstalls; the backup is for safety, not for restore), and the
restore step applies only while the instance lives in site-packages.

## Verify
Unit tests: upstream's `test_instance_path.py` ported (migration copies,
resumes after an interrupted copy, never overwrites, marker blocks
resurrection of deleted data, unusable target falls back), plus: the full
item list, modes preserved, override env, backend/script paths. Gate: suite,
ruff, mypy. On robot: as in Rollout.

## Self-review log
1. *Data-loss paths?* Migration only copies; the legacy copy stays until the
   next reinstall; an existing destination is never overwritten; the deploy
   backup still runs first. Accepted.
2. *Two copies drifting* (backend pushing to the old path after the switch)?
   The backend path changes in the same release; the deploy note says to
   update the Mac backend checkout before its next push. Accepted.
3. *`installed_tool_spaces.json` and D-031* — the bundled-spec override reads
   at runtime from wherever the manifest is; moving it is neutral. To verify
   in step 6b after deploy.
4. *SOFTWARE_RESET* now leaves the data in `~/.config` (outside `/venvs`) —
   an improvement, not a risk.
