# HomeAssistant-Nova Native Port Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port 22 capabilities from the operator's `HomeAssistant-Nova` `ha-actions` MCP server into `reachy_companion` as native app `Tool` subclasses — music on the robot's own speaker, TV casting, a NAS home-video library, Google Calendar/Tasks/Drive, Notion, email, and two audio gags — with in-code confirmation gating, home-network awareness, and clean per-family disablement.

**Architecture:** No MCP hop and no vendored `server.py`. A new `reachy_companion.hanova` package holds thin, personally-identifier-free service layers (Google OAuth + API calls, Notion request layer, Drive, SMTP, yt-dlp, SMB, media cache) adapted from the upstream stdlib modules; each capability is one `tools/<name>.py` file whose `Tool.name` equals the filename, exactly like `tools/home_control.py`. Long or blocking work is wrapped in `asyncio.to_thread` — the realtime loop already dispatches *every* tool call through `BackgroundToolManager.start_tool()` (`huggingface_realtime.py:1011`), so there is no separate "background" lane to build. Media that a Chromecast must fetch is written into an instance-dir cache and served by the app's existing FastAPI settings server (`console.py`), which already binds `0.0.0.0:7860`.

**Tech Stack:** Python 3.11+, `httpx` (async HA + probes), stdlib `urllib` (Google/Notion/Drive, adapted from upstream), `openai==2.28.0` (Images API), FastAPI/Starlette `StaticFiles` (media serving), `yt-dlp`, `imageio-ffmpeg`, `smbprotocol`. Tests: `pytest` + `pytest-asyncio`, `ruff==0.15.20`, `mypy==2.2.0` strict.

**Spec:** Three private scratchpad reports (NOT in the repo — read them from
`C:\Users\b8901\AppData\Local\Temp\claude\C--Project-Reachy-mini\4444d3b4-d0ee-42bf-9486-dbf4a4d4c0ca\scratchpad\`):
- `ha-actions-portability.md` — per-tool mechanics, substitutes, timing budgets
- `ha-nova-config-manifest.md` — exact config values and env naming (§D naming resolutions)
- `ha-nova-survey.md` — tool inventory, transport, risk review

Upstream source of the port (read-only): `C:\Project\Reachy-mini\reference\HomeAssistant-Nova\bin\ha-actions-mcp\server.py`, `bin/google/{gauth,gcal,gtasks,gmail_send}.py`, `bin/notion/notion.py`, `scripts/google/gdrive.py`, `bin/nas-video/nasvideo/{config,smb,query}.py`.

Project contracts that also bind this plan: `C:\Project\Reachy-mini\CLAUDE.md`, `C:\Project\Reachy-mini\reachy_companion\CLAUDE.md`, `C:\Project\Reachy-mini\docs\adding-a-skill.md`, `C:\Project\Reachy-mini\docs\PRD.md`.

## Global Constraints

These are the controller's binding architecture rulings. Every task's requirements implicitly include this section. Restated verbatim:

**R1. Native port:** every capability is a native app Tool subclass in `reachy_companion/src/reachy_companion/tools/`. NO MCP hop, NO vendored `server.py`, NO stdio. Vendored-and-adapted small modules allowed for services (gauth request layer, notion request layer, nas query logic) with all personal identifiers replaced by config reads.

**R2. Final tool inventory (22 new):** `play_music` (ALWAYS plays on the robot's own speaker — operator ruling; yt-dlp resolves/downloads audio; the Voice-PE and TV-cast music paths are NOT ported; `play_music_here` is merged away), `stop_music`, `play_video` (TV cast via HA, house-bound), `show_on_tv` (image generated via OpenAI Images API using the robot's existing `OPENAI_API_KEY`, served over LAN, cast to TV, house-bound), `nas_video_query`, `play_nas_video`, `nas_play_folder`, `nas_skip` (house-bound, smbprotocol fetch → LAN serve → HA cast), `calendar_add`, `calendar_delete` (GATED), `calendar_list`, `task_add`, `task_complete` (GATED), `task_delete` (GATED), `task_list`, `notion_add`, `drive_list`, `drive_trash` (GATED), `drive_upload` (REINTERPRETED: captures one camera frame from the robot and uploads THAT to the configured Drive folder; GATED), `email_send` (GATED), `self_destruct` (arm/confirm gag on the ROBOT's speaker), `mad_laugh` (robot speaker).

**R3. Confirmation gating IN CODE:** gated tools use a two-step contract — call without `confirm:true` returns `{"status":"needs_confirmation", "summary": <exact human-readable action: matched event title+date, recipient+subject, item name>}`; only a second call with `confirm:true` executes. The pending action is held tool-side with a TTL (90 s, like upstream `self_destruct`) and cleared after execution/expiry. Enforced in the tool, never only in the prompt.

**R4. Home-network awareness:** new module `home_net.py` — `home_state()` probes the configured `HA_URL` `/api/` with the token (timeout 1.5 s, result cached 30 s, thread-safe). House-bound tools (`play_video`, `show_on_tv`, all `nas_*`, and existing `home_control` stays as-is but does NOT need changing) return `{"status":"away_from_home"}` when not home. Cloud tools and music NEVER consult it. Persona/profile gains behavior lines: when a tool reports `away_from_home`, Reachy explains naturally in Chinese that it is away from home and cannot do house things; it never claims the house is broken. *(Review round 1 finding 12 and round 2 finding 3 refined this: the verdict is tri-state, `away_from_home` requires positive off-home evidence, and the `is_home()` boolean is gone because it collapsed `unknown` into "yes".)*

**R5. Per-family enablement:** each family (google-calendar/tasks, drive, notion, email, nas, media-cast) cleanly disables when its config/credentials are absent — tool registered but returns `{"status":"unavailable","reason":"not_configured"}`; one INFO line per family at startup saying enabled/disabled. The app must boot green with ZERO of the new config present.

**R6. Media serving:** REUSE the app's existing web server (`console.py`) by mounting a static/file route for the media dir if its framework allows; only if that is genuinely infeasible fall back to a tiny stdlib `ThreadingHTTPServer` on a new port. The base URL comes from `HANOVA_MEDIA_HTTP_BASE` (operator supplies robot LAN IP). Downloaded/fetched media lives in a media cache dir under the instance path with keep-N cleanup (music keep 12, NAS keep 8 — upstream defaults).

**R7. Music/mic interplay:** when the realtime session detects user speech starting (the existing `speech_started`/barge-in path in `huggingface_realtime.py`), any robot-speaker music playback is PAUSED (or volume-ducked if pause is unsupported by the daemon API — plan must pick based on the actual daemon media API and say which); `stop_music` always works by voice. Music playback must go through the daemon's `media/play_sound` path, not through the realtime audio output.

**R8. New pip deps (operator-approved):** `yt-dlp`, one ffmpeg wheel (choose `static-ffmpeg` or `imageio-ffmpeg` after checking which provides an aarch64 ffmpeg binary usable for audio extraction; state the choice and why), `smbprotocol`. Nothing else; no system packages.

**R9. Env naming per manifest section D:** reuse `HA_URL`/`HA_TOKEN`; `HANOVA_*` prefix for the new family config; `NOTION_MCP_*` stays separate from `HANOVA_NOTION_*`; `GOOGLE_CREDS_DIR` + writable google OAuth JSONs in the instance dir; every new instance-dir credential/state file goes into the deploy skill's backup/restore ritual (a plan task updates the skill + `.env.example` + docs + D-018 in `DECISIONS.md` + PRD F-K/§12 touches + `progress.md`).

**R10. Tool descriptions:** compact, personal-identifier-free, written fresh (target ≤120 chars English or mixed zh per tool); `profile.md` `default_tools` gains all 22; persona seed/profile behavior lines updated for routing (music, house control away-behavior, confirmation etiquette: read back before confirming).

**R11. Timezone** `Asia/Taipei` from config (`HANOVA_TZ` default `Asia/Taipei`) — calendar/task tools must not hardcode it.

**R12. Every task ends green:** full suite + ruff + mypy strict; tests use the established patterns (no network in tests — httpx/urllib mocked; smb/yt-dlp/google mocked at module seams).

### Resolutions the plan makes under those rulings

| Ruling | Resolution and evidence |
|---|---|
| R6 — media serving | **Feasible on the existing server.** `console.py:529` already does `settings_app.mount("/static", StaticFiles(...))` on a FastAPI app (`console.py:56`), served by uvicorn at `http://0.0.0.0:7860` (`main.py:358`, `main.py:465` `custom_app_url = "http://0.0.0.0:7860/"`). We mount a second `StaticFiles` at `/hanova-media`. **No stdlib fallback server is built.** |
| R7 — pause vs duck | **Neither pause nor per-stream ducking exists.** The daemon media API is exactly `POST /api/media/play_sound {file}` and `POST /api/media/stop_sound` (`reference/reachy_mini/src/reachy_mini/daemon/app/routers/media.py:77-115`); the client `MediaManager` exposes only `play_sound` (`media/media_manager.py:264`) — there is no `stop_sound`, no seek, and no per-stream volume anywhere in `media/`. `POST /api/volume/set` is **system-wide** and plays a test beep as a side effect (`daemon/app/routers/volume.py:88-110`), so it would also duck Reachy's own voice. **Chosen: stop-and-resume-at-offset** — on `speech_started` we `POST /api/media/stop_sound` and record the elapsed offset; when the assistant's audio finishes we re-cut the cached MP3 from that offset with the bundled ffmpeg (`ffmpeg -ss <offset> -i cached.mp3 -c copy resume.mp3`) and `play_sound` it. That is a pause built from the only primitive the daemon offers. |
| R8 — ffmpeg wheel | **`imageio-ffmpeg`.** It publishes a `manylinux2014_aarch64` wheel with the ffmpeg binary *inside the wheel* and exposes `imageio_ffmpeg.get_ffmpeg_exe()` for `yt-dlp --ffmpeg-location`. `static-ffmpeg` instead fetches/extracts binaries at first run, which needs network at runtime and has no guaranteed linux-aarch64 payload. Task 4 Step 1 proves the wheel resolves for `aarch64-manylinux_2_28` before any code is written. |
| Daemon stop path | The app reaches the daemon at `deps.reachy_mini._daemon_http_url` (set at `reference/reachy_mini/src/reachy_mini/reachy_mini.py:160`), with fallback `http://127.0.0.1:8000`. **Both** `play_sound` and `stop_sound` go through the daemon REST API directly and their HTTP status is checked, because the SDK's `MediaManager.play_sound` swallows a non-2xx playback failure and would let us report success when nothing played (review round 1, finding 2). |
| Family count | **Seven** INFO lines: the six families named in R5 plus `music` (yt-dlp/ffmpeg presence), because music is robot-local and never config-gated but its two binaries still need a startup verdict. A family verdict is **tri-state** — `enabled` / `partial (<n>/<m> tools)` / `disabled (<reason>)` — because per-tool prerequisites inside one family genuinely differ (review round 1, finding 10). |
| Availability granularity (finding 10) | Enablement is decided **per tool**, not per family. `settings.TOOL_PREREQS` maps each of the 22 names to its own ordered prerequisite list; `settings.tool_available(name)` is what every tool body calls, and `family_status()` aggregates the tools of a family into the tri-state startup verdict. `stop_music` deliberately has **no** prerequisites — it is the safety lane that must always answer — and that is an explicit design decision, not an oversight. |
| Home verdict granularity (finding 12, revised by round 2 finding 3) | `home_net` returns **tri-state** `home` / `away` / `unknown`. **`away` requires positive off-home routing evidence**: the robot's own address sitting outside every network the operator declared in `HANOVA_HOME_NETWORKS`. A *failed* connection to Home Assistant — no route, DNS failure, TCP refused, connect timeout — is `unknown`, not absence: those are exactly what an HA outage looks like from a machine sitting in the house. So are 401, 5xx, an HTTP timeout and a VPN-shaped route. With `HANOVA_HOME_NETWORKS` unset, `away` is **unreachable by construction**. The old fixed-`/24` comparison is demoted to a hint that may only withhold `home`. Every house-bound tool branches all three verdicts explicitly and does **no work at all** on `unknown`. |
| Logging hygiene (finding 7, scoped) | New-tool logs are **metadata only** — status, counts, durations, family, tool name. Never titles, queries, subjects, recipients, ids, paths or URLs. One shared `hanova/redact.py` helper is used by all 22 tools for **both** their log lines and any free-text error surface they return. **Out of scope:** the existing framework's model-visible tool-result and assistant-content logging in `huggingface_realtime.py` is *not* redesigned by this plan (controller ruling, review round 1). |
| Isolation (finding 24) | All work happens on the feature branch `feat/ha-nova-port`, created in **Task 0**. Every task's commit step stages only the files that task names. Task 15 merges the branch back into `main` **before** the deploy wheel is built, so the robot always runs `main`. |
| R5 `reason` field — one deliberate refinement | R5 fixes the unavailable payload as `{"status":"unavailable","reason":"not_configured"}`. The shape is unchanged and `settings.unavailable()` still defaults to exactly that, but tools pass the **name of the first unmet prerequisite key** instead (`{"status":"unavailable","reason":"HANOVA_NOTION_TOKEN"}`). Reason: finding 10 made availability per-tool, so `not_configured` alone can no longer tell an operator *which* switch is missing out of the twenty-five, and finding 7 forbids putting the value there. A key name is safe to log, safe to speak, and actionable. Everything else about R5 is unchanged. |

### File structure

```
reachy_companion/src/reachy_companion/
  home_net.py                      # R4 — tri-state home_state(), LAN signal, cache, away_from_home()
  hanova/__init__.py
  hanova/settings.py               # every HANOVA_* read + per-tool prereqs + family gating + startup log
  hanova/redact.py                 # finding 7 — the one redaction helper all 22 tools log/error through
  hanova/confirm.py                # R3 — ConfirmationGate (epoch-scoped, claim/complete/abort), GATE
  hanova/ha_client.py              # async HA REST (httpx): service calls, scripts, states
  hanova/media_store.py            # R6 — instance media cache dirs, keep-N prune, URLs
  hanova/ytdlp.py                  # yt-dlp search + audio download, ffmpeg location
  hanova/music_player.py           # R7 — robot-speaker music session, pause/resume/stop
  hanova/images.py                 # OpenAI Images API -> PNG in the media cache
  hanova/gauth.py                  # Google OAuth refresh + api_call (adapted upstream)
  hanova/gcal.py                   # Calendar v3 calls
  hanova/gtasks.py                 # Tasks v1 calls + cross-list fuzzy resolver
  hanova/notion_client.py          # Notion v1 request layer + add_page
  hanova/gdrive.py                 # Drive v3 token + list/trash/upload-bytes
  hanova/gmail_smtp.py             # SMTP_SSL send
  hanova/nas.py                    # index load/query + smbprotocol fetch + session state
  tools/play_music.py  tools/stop_music.py  tools/play_video.py  tools/show_on_tv.py
  tools/nas_video_query.py  tools/play_nas_video.py  tools/nas_play_folder.py  tools/nas_skip.py
  tools/calendar_add.py  tools/calendar_delete.py  tools/calendar_list.py
  tools/task_add.py  tools/task_complete.py  tools/task_delete.py  tools/task_list.py
  tools/notion_add.py
  tools/drive_list.py  tools/drive_trash.py  tools/drive_upload.py
  tools/email_send.py
  tools/self_destruct.py  tools/mad_laugh.py
```

---

### Task 0: Isolate the work on a feature branch

Implements review round 1, finding 24 and **review round 2, finding 7**. The
repository is currently on `main` with `persona.md` **already modified by the
user**. Executing straight onto `main` would fold unrelated user changes into
the Task 14 commit, so every task below runs on a dedicated branch and stages
only its own named files.

**Round 2, finding 7 — "read the diff and decide" is not a control.** The
previous version let Task 14 run `git add persona.md` when the hunks did not
overlap, which stages *both* sets of changes; it also left the file dirty across
the Task 15 `git checkout main`, which git can refuse outright, and deployed the
working-tree copy rather than the committed mainline blob. The fix is
mechanical rather than judgemental: **the user's persona patch is stashed out of
the working tree here, in Task 0, and does not come back until Task 15 has
finished deploying.** Between those two points `persona.md` in the worktree
contains exactly one thing — our port patch — so `git add persona.md` is
unambiguous, `git checkout main` is clean, and `git show HEAD:persona.md` is by
construction what runs on the robot.

**Files:**
- No file content changes in the repository. This task creates the branch,
  records the pre-existing working-tree state, and moves the user's `persona.md`
  patch into a stash plus a saved patch file **outside** the repo.

**Interfaces:**
- Produces: the branch `feat/ha-nova-port`; a recorded baseline `git status
  --short`; **two** saved patch files outside the repo —
  `$env:TEMP\hanova-persona-baseline.patch` (full context, the only thing the
  Step 15b conflict fallback can apply) and
  `$env:TEMP\hanova-persona-baseline.u0.patch` (zero context, the *identity*
  Step 15b verifies against) — plus the `Get-PatchId` helper both steps use.

- [ ] **Step 1: Record the pre-existing dirty state before touching anything**

```powershell
cd C:\Project\Reachy-mini
git status --short --branch
git stash list
```

Copy the exact `git status --short` output into the plan's execution notes (or
the session handoff). Every path listed here is a **user change this port did
not make**. It must still be listed, unstaged and unmodified by us, when Task 15
finishes. `persona.md` is expected to appear in this list.

- [ ] **Step 2: Capture the user's `persona.md` patch, then stash it (finding 7)**

**Round 3, finding 5 rewrote this step.** The previous version ran
`git diff -- persona.md | Out-File -FilePath $patch -Encoding utf8 -NoNewline`.
That is a corruption, not a save: PowerShell hands `Out-File` git's stdout as an
**array of lines**, and `-NoNewline` then concatenates them — the saved "patch"
is one enormous line, `git apply` cannot read it, and nothing downstream
notices. It also verified only the *added* lines, so a line the user **deleted**
could vanish in the restore without tripping anything. Both are fixed here:
**git writes the files itself**, and the identity is a **stable patch id over
the complete payload — additions and deletions**.

Save the patch to a path **outside the repository** (it is the user's work, not
ours, and it must never be staged or committed), record its identity, then
remove it from the working tree with a named stash:

```powershell
cd C:\Project\Reachy-mini
$patch   = Join-Path $env:TEMP "hanova-persona-baseline.patch"
$patchU0 = Join-Path $env:TEMP "hanova-persona-baseline.u0.patch"

# `--output=` makes GIT write the file. Never pipe git into Out-File/Set-Content
# with -NoNewline: PowerShell passes an array of lines and -NoNewline joins them
# into a single line, silently destroying the patch (round 3, finding 5).
git diff --output="$patch"        -- persona.md      # full context: the fallback applies THIS
git diff -U0 --output="$patchU0"  -- persona.md      # zero context: the identity is taken from THIS
"patch bytes: $((Get-Item $patch).Length)"
"u0 patch bytes: $((Get-Item $patchU0).Length)"
(Get-FileHash -Algorithm SHA256 $patch).Hash.ToLower()
```

Now the identity. It is `git patch-id --stable`, computed by git over the whole
change payload — every `+` line **and** every `-` line — from the **zero-context**
patch, so appending our block later shifts line numbers and context without
changing the id:

```powershell
function Get-PatchId([string]$patchFile) {
  # PowerShell has no stdin redirection operator, and piping git into git
  # through a PowerShell pipeline re-encodes the bytes. cmd.exe does the
  # redirect so git reads the file verbatim.
  if (-not (Test-Path $patchFile) -or (Get-Item $patchFile).Length -eq 0) { return "" }
  $line = cmd /c "git patch-id --stable < `"$patchFile`""
  if (-not $line) { throw "git patch-id produced nothing for $patchFile" }
  return ($line -split ' ')[0]
}

$baselinePatchId = Get-PatchId $patchU0
"baseline stable patch-id: $baselinePatchId"
```

Record the byte counts, the SHA256 and the **patch id** in the execution notes;
Step 15b recomputes the id from the same file rather than trusting a variable
across sessions. If the patch is empty (`patch bytes: 0` and an empty patch id),
the user has no pending persona change: say so explicitly in the notes, skip the
stash below, and treat every later "restore the user patch" instruction as a
no-op that must still be *checked*, not assumed.

```powershell
cd C:\Project\Reachy-mini
git stash push --message "hanova-port: user persona.md baseline" -- persona.md
git stash list                      # the new entry must be stash@{0}
git status --short --branch         # persona.md must NO LONGER appear
git diff --stat -- persona.md       # must print nothing
```

Expected: `persona.md` is gone from the dirty list and identical to
`HEAD:persona.md`. Everything else from Step 1 is untouched — the stash was
path-limited on purpose.

- [ ] **Step 3: Create the feature branch from current `main`**

A plain branch in the same worktree — not a `git worktree`, because the dev
venv, `reference/` clones and `.env` all live in this working directory and a
second worktree would not have them.

```powershell
cd C:\Project\Reachy-mini
git rev-parse --abbrev-ref HEAD
git checkout -b feat/ha-nova-port
git status --short --branch
```

Expected: `## feat/ha-nova-port` on the branch line, and the Step 1 dirty paths
**minus `persona.md`** (Step 2 stashed it).

- [ ] **Step 4: Fix the staging discipline for every later task**

These four rules bind Tasks 1–15 and are restated in each commit step:

1. **Never `git add -A`, never `git add .`, never `git commit -a`.** Every
   commit step in this plan lists its files explicitly; stage exactly those.
2. **`persona.md` is staged in exactly one place: Task 14, Step 4**, and by then
   the working-tree diff of that file *is* the port patch and nothing else,
   because the user's patch is stashed. `git add persona.md` is therefore the
   correct command and `git add -p persona.md` is **not** needed — but
   `git diff persona.md` is still read before staging, and if it shows anything
   the port did not write, **STOP**: the stash was lost and must be recovered
   before continuing.
3. **The stash is not restored until Task 15, Step 15b.** No other task may run
   `git stash pop`, `git stash apply` or `git stash drop`; Step 15b uses
   `apply` + verify + `drop` (never `pop`, which drops before anything has been
   checked — round 3, finding 5). A `git stash list` that no longer shows the
   `hanova-port: user persona.md baseline` entry is a stop condition.
4. **After every commit, run `git status --short`** and confirm the Step 1
   baseline paths (minus `persona.md`) are still present and still unstaged.

- [ ] **Step 5: Confirm the branch is the only thing that changed**

```powershell
cd C:\Project\Reachy-mini
git log --oneline -1
git diff --stat
git stash list
```

Expected: `git log` shows the same commit `main` pointed at; `git diff --stat`
shows only the Step 1 baseline paths minus `persona.md`; and the stash list
still holds the baseline entry. Nothing is committed in Task 0.

---

### Task 1: Config surface, per-tool prerequisites, per-family enablement, startup logging

Implements R5, R9, R11, plus review round 1 findings 6 (no identifier/path/script
defaults), 7 (the shared redaction helper), 10 (per-tool prerequisites) and 13
(bounded numeric readers). Every `HANOVA_*` environment read in the whole port
lives in one module, every **tool** declares its own prerequisites, and every
family gets a tri-state startup verdict. The app must boot green with zero new
config present.

**Files:**
- Create: `reachy_companion/src/reachy_companion/hanova/__init__.py`
- Create: `reachy_companion/src/reachy_companion/hanova/settings.py`
- Create: `reachy_companion/src/reachy_companion/hanova/redact.py`
- Modify: `reachy_companion/src/reachy_companion/main.py` (insert one call before `_discover_remote_mcp_tools(logger)`)
- Modify: `reachy_companion/.env.example` (append the HomeAssistant-Nova block after the existing `HA_ENTITIES=` line)
- Modify: `reachy_companion/pyproject.toml` (add `yt-dlp`, `imageio-ffmpeg`, `smbprotocol` to `[project].dependencies`)
- Test: `reachy_companion/tests/test_hanova_settings.py`
- Test: `reachy_companion/tests/test_hanova_redact.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces (every later task imports from `reachy_companion.hanova.settings`):
  - `env_str(name: str, default: str = "") -> str`
  - `env_int(name: str, default: int, *, minimum: int, maximum: int) -> int` — **bounds are mandatory** (finding 13)
  - `env_float(name: str, default: float, *, minimum: float, maximum: float) -> float` — **bounds are mandatory**, and non-finite input is rejected
  - `env_path(name: str, default: Path | None = None) -> Path | None`
  - `ha_url() -> str`, `ha_token() -> str`, `timezone_name() -> str` (validated with `zoneinfo.ZoneInfo`), `cast_entity() -> str`
  - `media_http_base() -> str`, `media_dir_override() -> Path | None`, `music_keep() -> int`, `nas_cast_keep() -> int`, `image_keep() -> int`
  - `google_account() -> str`, `google_creds_dir() -> Path | None`, `gcal_calendar_id() -> str`, `gtasks_list_id() -> str`, `cal_delete_window_days() -> int`
  - `drive_secrets_path() -> Path | None`, `drive_parent_id() -> str`
  - `notion_token() -> str`, `notion_data_source_id() -> str`, `notion_title_prop() -> str`
  - `smtp_host() -> str`, `smtp_port() -> int`, `smtp_user() -> str`, `smtp_app_password() -> str`, `smtp_from_name() -> str`
  - `nas_host() -> str`, `nas_user() -> str`, `nas_password() -> str`, `nas_share() -> str`, `nas_subpath() -> str`, `nas_cast_subpath() -> str`, `nas_index_path() -> Path | None` — **the share and the two subpaths have no default** (finding 6)
  - `ytdlp_search_n() -> int`, `ytdlp_timeout_s() -> int`, `ytdlp_download_timeout_s() -> int`
  - `self_destruct_yt_id() -> str`, `mad_laugh_yt_id() -> str`
  - `confirm_ttl_s() -> float`, `home_probe_timeout_s() -> float`, `home_cache_ttl_s() -> float`, `image_model() -> str`
  - `ha_script_youtube() -> str`, `ha_script_image_url() -> str`, `ha_script_video_url() -> str` — **no defaults**; an unset script name disables the tool that needs it (finding 6)
  - `TOOL_PREREQS: Dict[str, tuple[str, ...]]` — the 22 ported names mapped to their own ordered prerequisite keys (finding 10)
  - `tool_status(tool_name: str) -> tuple[bool, str]`, `tool_available(tool_name: str) -> bool` — **what every tool body calls**
  - `FAMILIES: tuple[str, ...]`, `FAMILY_TOOLS: Dict[str, tuple[str, ...]]`
  - `family_status(family: str) -> tuple[str, str]` — tri-state `("enabled"|"partial"|"disabled", reason)`
  - `family_enabled(family: str) -> bool` — True only when the verdict is `"enabled"`
  - `unavailable(reason: str = "not_configured") -> Dict[str, Any]` returning exactly `{"status": "unavailable", "reason": <reason>}`; the reason is a **prerequisite key name**, never a value
  - `log_family_status() -> None`
- Produces (`reachy_companion.hanova.redact`, finding 7 — used by all 22 tools):
  - `redact.count(value: Any) -> str` — `"<3 items>"` / `"<empty>"`, never the items
  - `redact.text(value: str | None) -> str` — `"<text:42 chars>"`, never the text
  - `redact.ident(value: str | None) -> str` — `"<id:ab12cd34>"`, a stable salted 8-hex digest, never the id
  - `redact.error(exc_or_text: BaseException | str, *, allow_errno: tuple[str, ...] = ()) -> str` — a class name plus structure read **off the exception object** (an HTTP status from `status_code`/`status`/`response.status_code`/`code`, an errno name from `errno`); the raw message is never tokenized, scanned or returned, and a bare string renders as exactly `"error"` (round 3, finding 3)
  - `redact.SAFE_LOG_FIELDS: frozenset[str]` — the only field names a tool may log verbatim (`status`, `count`, `duration_ms`, `family`, `tool`, `http_status`, `cached`, `ok`)

- [ ] **Step 1: Write the failing test**

Create `reachy_companion/tests/test_hanova_settings.py`:

```python
"""Contract tests for the HomeAssistant-Nova config surface (D-018, R5/R9/R11).

Also pins review round 1 findings 6 (no identifier/path/script defaults),
10 (per-tool prerequisites, table-driven over all 22 tools) and 13 (bounded
numeric readers, validated timezone).
"""

import os
import math
import logging
from pathlib import Path

import pytest

from reachy_companion.hanova import settings


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Start every test from zero HomeAssistant-Nova configuration."""
    for name in list(os.environ):
        if name.startswith("HANOVA_") or name in {
            "HA_URL",
            "HA_TOKEN",
            "GOOGLE_CREDS_DIR",
            "HERMES_DRIVE_SECRETS",
            "OPENAI_API_KEY",
        }:
            monkeypatch.delenv(name, raising=False)


def test_defaults_are_generic_never_operator_derived():
    """Only vendor/behaviour defaults survive; nothing traceable to the operator."""
    assert settings.timezone_name() == "Asia/Taipei"
    assert settings.music_keep() == 12
    assert settings.nas_cast_keep() == 8
    assert settings.notion_title_prop() == "Name"  # Notion's own default title property
    assert settings.smtp_host() == "smtp.gmail.com"  # a vendor endpoint, not an identifier
    assert settings.smtp_port() == 465
    assert settings.ytdlp_search_n() == 5
    assert settings.ytdlp_timeout_s() == 20
    assert settings.ytdlp_download_timeout_s() == 120
    assert settings.cal_delete_window_days() == 14
    assert settings.confirm_ttl_s() == 90.0
    assert settings.home_probe_timeout_s() == 1.5
    assert settings.home_cache_ttl_s() == 30.0
    assert settings.image_model() == "gpt-image-1"


def test_identifier_path_and_script_keys_all_default_to_empty():
    """Finding 6: a value derived from the operator's setup is never a default."""
    for reader in (
        settings.gcal_calendar_id,
        settings.gtasks_list_id,
        settings.drive_parent_id,
        settings.google_account,
        settings.cast_entity,
        settings.nas_host,
        settings.notion_data_source_id,
        settings.smtp_user,
        # finding 6: all three used to carry the operator's own share name and
        # folder layout as defaults. Round 2, finding 5: this comment does not
        # name them either -- a comment is committed text like any other.
        settings.nas_share,
        settings.nas_subpath,
        settings.nas_cast_subpath,
        # finding 6: these three were the operator's own scripts.yaml entry names.
        settings.ha_script_youtube,
        settings.ha_script_image_url,
        settings.ha_script_video_url,
    ):
        assert reader() == "", f"{reader.__name__} must default to empty"


def test_env_values_win_and_are_stripped(monkeypatch):
    """Whitespace around a pasted value must not corrupt it."""
    monkeypatch.setenv("HANOVA_TZ", "  Europe/Paris  ")
    monkeypatch.setenv("HANOVA_MUSIC_KEEP", " 3 ")
    monkeypatch.setenv("HANOVA_CONFIRM_TTL_S", "45.5")
    monkeypatch.setenv("HA_URL", "http://ha.example.invalid:8123/")
    assert settings.timezone_name() == "Europe/Paris"
    assert settings.music_keep() == 3
    assert settings.confirm_ttl_s() == 45.5
    assert settings.ha_url() == "http://ha.example.invalid:8123"


def test_malformed_numbers_fall_back_to_defaults(monkeypatch):
    """A typo in a numeric env var must not raise at tool construction time."""
    monkeypatch.setenv("HANOVA_MUSIC_KEEP", "twelve")
    monkeypatch.setenv("HANOVA_CONFIRM_TTL_S", "soon")
    assert settings.music_keep() == 12
    assert settings.confirm_ttl_s() == 90.0


@pytest.mark.parametrize("raw", ["0", "-1", "-0.5", "nan", "inf", "-inf", "1e400", "999999999"])
def test_out_of_range_numbers_fall_back_to_defaults(monkeypatch, raw):
    """Finding 13: zero, negative, non-finite and absurd values are all rejected."""
    monkeypatch.setenv("HANOVA_YTDLP_TIMEOUT_S", raw)
    monkeypatch.setenv("HANOVA_CONFIRM_TTL_S", raw)
    monkeypatch.setenv("HANOVA_HOME_PROBE_TIMEOUT_S", raw)
    assert settings.ytdlp_timeout_s() == 20
    assert settings.confirm_ttl_s() == 90.0
    assert settings.home_probe_timeout_s() == 1.5


def test_every_numeric_accessor_stays_inside_finite_bounds(monkeypatch):
    """Finding 13: no accessor may return 0, a negative, or a non-finite number."""
    for name in (
        "HANOVA_MUSIC_KEEP",
        "HANOVA_NAS_CAST_KEEP",
        "HANOVA_IMAGE_KEEP",
        "HANOVA_CAL_DELETE_WINDOW_DAYS",
        "HANOVA_SMTP_PORT",
        "HANOVA_YTDLP_SEARCH_N",
        "HANOVA_YTDLP_TIMEOUT_S",
        "HANOVA_YTDLP_DOWNLOAD_TIMEOUT_S",
        "HANOVA_CONFIRM_TTL_S",
        "HANOVA_HOME_PROBE_TIMEOUT_S",
        "HANOVA_HOME_CACHE_TTL_S",
    ):
        monkeypatch.setenv(name, "-99999999")
    numbers = [
        settings.music_keep(),
        settings.nas_cast_keep(),
        settings.image_keep(),
        settings.cal_delete_window_days(),
        settings.smtp_port(),
        settings.ytdlp_search_n(),
        settings.ytdlp_timeout_s(),
        settings.ytdlp_download_timeout_s(),
        settings.confirm_ttl_s(),
        settings.home_probe_timeout_s(),
        settings.home_cache_ttl_s(),
    ]
    for value in numbers:
        assert math.isfinite(value) and value > 0


def test_home_cache_ttl_may_be_zero_only_because_tests_need_it(monkeypatch):
    """A zero cache TTL is the one legal zero: it means "always re-probe"."""
    monkeypatch.setenv("HANOVA_HOME_CACHE_TTL_S", "0")
    assert settings.home_cache_ttl_s() == 0.0


def test_timezone_is_validated_against_the_tz_database(monkeypatch):
    """Finding 13: an unknown zone must degrade, not blow up inside a tool."""
    monkeypatch.setenv("HANOVA_TZ", "Mars/Olympus_Mons")
    assert settings.timezone_name() == "Asia/Taipei"
    monkeypatch.setenv("HANOVA_TZ", "UTC")
    assert settings.timezone_name() == "UTC"


# --- per-tool prerequisites (finding 10) ----------------------------------
def test_every_ported_tool_declares_prerequisites():
    """All 22 names are covered; a new tool cannot be silently ungated."""
    assert len(settings.TOOL_PREREQS) == 22
    for family, names in settings.FAMILY_TOOLS.items():
        assert family in settings.FAMILIES
        for name in names:
            assert name in settings.TOOL_PREREQS, name
    covered = {name for names in settings.FAMILY_TOOLS.values() for name in names}
    assert covered == set(settings.TOOL_PREREQS)


def test_with_zero_config_only_the_always_on_tools_are_available():
    """stop_music is the safety lane and must answer even with nothing set up."""
    available = {name for name in settings.TOOL_PREREQS if settings.tool_available(name)}
    assert "stop_music" in available
    assert "calendar_add" not in available
    assert "email_send" not in available


def test_stop_music_has_no_prerequisites_by_design():
    """Finding 10: this is a deliberate exemption, recorded as an empty tuple."""
    assert settings.TOOL_PREREQS["stop_music"] == ()


def test_every_disabled_tool_names_the_key_not_a_value():
    """The reason string is a config key name; it must never leak a value."""
    for name in settings.TOOL_PREREQS:
        available, reason = settings.tool_status(name)
        if not available:
            assert reason
            assert reason.isupper() or reason.replace("_", "").isalnum() or " " in reason
            assert "@" not in reason and "/" not in reason


def test_google_calendar_tools_need_the_creds_file_and_a_calendar_id(monkeypatch, tmp_path):
    """Finding 10: upstream's family gate ignored the calendar and list ids."""
    creds_dir = tmp_path / "google-workspace-mcp"
    creds_dir.mkdir()
    monkeypatch.setenv("GOOGLE_CREDS_DIR", str(creds_dir))
    monkeypatch.setenv("HANOVA_GOOGLE_ACCOUNT", "someone@example.com")
    assert settings.tool_status("calendar_list") == (False, "GOOGLE_CREDS_FILE")
    (creds_dir / "someone@example.com.json").write_text("{}", encoding="utf-8")
    assert settings.tool_status("calendar_list") == (False, "HANOVA_GCAL_CALENDAR_ID")
    monkeypatch.setenv("HANOVA_GCAL_CALENDAR_ID", "cal-under-test")
    assert settings.tool_available("calendar_list") is True
    # task_add needs its own list id; task_list does not.
    assert settings.tool_status("task_add") == (False, "HANOVA_GTASKS_LIST_ID")
    assert settings.tool_available("task_list") is True


def test_google_creds_dir_must_be_writable(monkeypatch, tmp_path):
    """Finding 10: gauth rewrites the file on refresh; a read-only dir is broken."""
    creds_dir = tmp_path / "google-workspace-mcp"
    creds_dir.mkdir()
    (creds_dir / "someone@example.com.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("GOOGLE_CREDS_DIR", str(creds_dir))
    monkeypatch.setenv("HANOVA_GOOGLE_ACCOUNT", "someone@example.com")
    monkeypatch.setenv("HANOVA_GCAL_CALENDAR_ID", "cal-under-test")
    monkeypatch.setattr(settings.os, "access", lambda path, mode: False)
    assert settings.tool_status("calendar_list") == (False, "GOOGLE_CREDS_DIR not writable")


def test_play_video_needs_no_lan_base_and_no_cast_entity(monkeypatch):
    """Finding 10: play_video hands HA an id; it serves nothing and casts no URL."""
    monkeypatch.setattr(settings, "_music_wheels_ready", lambda: (True, ""))
    monkeypatch.setenv("HA_URL", "http://ha.example.invalid:8123")
    monkeypatch.setenv("HA_TOKEN", "tok")
    monkeypatch.setenv("HANOVA_HA_SCRIPT_YOUTUBE", "tv_show_youtube")
    assert settings.tool_available("play_video") is True
    assert settings.tool_status("show_on_tv")[0] is False  # needs base + key + mount


def test_url_casting_tools_need_a_live_media_mount(monkeypatch):
    """Finding 11: a failed mount means casting a URL nothing will serve."""
    monkeypatch.setenv("HA_URL", "http://ha.example.invalid:8123")
    monkeypatch.setenv("HA_TOKEN", "tok")
    monkeypatch.setenv("HANOVA_HA_SCRIPT_IMAGE_URL", "tv_show_image_url")
    monkeypatch.setenv("HANOVA_MEDIA_HTTP_BASE", "http://robot.example.invalid:7860")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    settings.set_media_mount_ready(False)
    try:
        assert settings.tool_status("show_on_tv") == (False, "HANOVA_MEDIA_MOUNT")
        settings.set_media_mount_ready(True)
        assert settings.tool_available("show_on_tv") is True
    finally:
        settings.set_media_mount_ready(False)


def test_index_only_nas_query_does_not_need_smb_credentials(monkeypatch, tmp_path):
    """Finding 10: nas_video_query reads a local JSON file and nothing else."""
    index = tmp_path / "nas-video-index.json"
    index.write_text('{"videos": []}', encoding="utf-8")
    monkeypatch.setenv("HANOVA_NAS_INDEX_PATH", str(index))
    assert settings.tool_available("nas_video_query") is True
    assert settings.tool_status("play_nas_video") == (False, "HANOVA_NAS_HOST")


def test_gag_tools_need_their_own_clip_ids(monkeypatch):
    """Finding 10: "music enabled" said nothing about whether a gag can play."""
    monkeypatch.setattr(settings, "_music_wheels_ready", lambda: (True, ""))
    assert settings.tool_status("self_destruct") == (False, "HANOVA_SELF_DESTRUCT_YT_ID")
    assert settings.tool_status("mad_laugh") == (False, "HANOVA_MAD_LAUGH_YT_ID")
    monkeypatch.setenv("HANOVA_SELF_DESTRUCT_YT_ID", "sd")
    monkeypatch.setenv("HANOVA_MAD_LAUGH_YT_ID", "ml")
    assert settings.tool_available("self_destruct") is True
    assert settings.tool_available("mad_laugh") is True


def test_unknown_tool_is_reported_not_raised():
    """A typo in a caller must not crash a tool call."""
    assert settings.tool_status("nope") == (False, "unknown tool")


# --- family aggregation ----------------------------------------------------
def test_all_config_families_disabled_with_zero_config():
    """R5: the app must boot with none of the new config present."""
    for family in settings.FAMILIES:
        if family == "music":
            continue  # depends on installed wheels and clip ids, not on a service
        verdict, reason = settings.family_status(family)
        assert verdict == "disabled"
        assert reason, f"{family} must explain why it is disabled"


def test_family_verdict_is_partial_when_only_some_tools_qualify(monkeypatch, tmp_path):
    """Finding 10: one family can be half-configured, and must say so."""
    index = tmp_path / "nas-video-index.json"
    index.write_text('{"videos": []}', encoding="utf-8")
    monkeypatch.setenv("HANOVA_NAS_INDEX_PATH", str(index))
    verdict, reason = settings.family_status("nas")
    assert verdict == "partial"
    assert "1/4" in reason


def test_family_status_never_raises_for_any_family():
    """Called from startup: a bad value must degrade, never abort the process."""
    for family in settings.FAMILIES:
        verdict, reason = settings.family_status(family)
        assert verdict in {"enabled", "partial", "disabled"}
        assert isinstance(reason, str)


def test_unknown_family_is_reported_not_raised():
    """A typo in a caller must not crash startup."""
    assert settings.family_status("nope") == ("disabled", "unknown family")


def test_unavailable_payload_is_exactly_the_contract():
    """R5 fixes this shape; tools return it verbatim."""
    assert settings.unavailable() == {"status": "unavailable", "reason": "not_configured"}
    assert settings.unavailable("HANOVA_NAS_HOST") == {
        "status": "unavailable",
        "reason": "HANOVA_NAS_HOST",
    }


def test_log_family_status_emits_one_line_per_family(caplog):
    """R5: one INFO line per family, with its tri-state verdict, every startup."""
    caplog.set_level(logging.INFO, logger="reachy_companion.hanova.settings")
    settings.log_family_status()
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("hanova family ")]
    assert len(lines) == len(settings.FAMILIES)
    for family in settings.FAMILIES:
        assert any(line.startswith(f"hanova family {family}: ") for line in lines)


def test_startup_log_never_prints_a_configured_value(monkeypatch, caplog, tmp_path):
    """Finding 7: the verdict names keys, never the operator's values."""
    secret = "SENTINEL_PRIVATE_x7"
    monkeypatch.setenv("HANOVA_GCAL_CALENDAR_ID", secret)
    monkeypatch.setenv("HANOVA_NOTION_TOKEN", secret)
    monkeypatch.setenv("HANOVA_NAS_HOST", secret)
    monkeypatch.setenv("HANOVA_SMTP_USER", secret)
    caplog.set_level(logging.DEBUG)
    settings.log_family_status()
    assert secret not in caplog.text


def test_env_path_expands_user(monkeypatch):
    """Paths written with ~ in .env must resolve."""
    monkeypatch.setenv("HANOVA_NAS_INDEX_PATH", "~/nas-video-index.json")
    resolved = settings.nas_index_path()
    assert resolved is not None
    assert str(resolved) == str(Path.home() / "nas-video-index.json")


def test_env_path_rejection_never_logs_the_path(monkeypatch, caplog):
    """Round 2, finding 6: settings.py is a service seam and logs like one.

    The previous version logged the rejected value with `%r`, which puts the
    operator's own directory layout into the log for a mere typo.
    """
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("HANOVA_NAS_INDEX_PATH", "~SENTINEL_PRIVATE_x7/index.json")
    settings.nas_index_path()
    assert "SENTINEL_PRIVATE_x7" not in caplog.text


# --- the declared home network (round 2, finding 3) ------------------------
def test_home_networks_is_empty_by_default(monkeypatch):
    """With nothing declared there is no evidence that could justify AWAY."""
    monkeypatch.delenv("HANOVA_HOME_NETWORKS", raising=False)
    assert settings.home_networks() == []


def test_home_networks_parses_a_comma_separated_cidr_list(monkeypatch):
    """One robot may legitimately live on a wired and a wireless subnet."""
    monkeypatch.setenv("HANOVA_HOME_NETWORKS", "203.0.113.0/24, 198.51.100.7 ")
    networks = settings.home_networks()
    assert len(networks) == 2
    assert str(networks[0]) == "203.0.113.0/24"
    assert str(networks[1]) == "198.51.100.7/32"


def test_a_malformed_home_network_is_dropped_and_never_logged(monkeypatch, caplog):
    """A typo must narrow "home", never widen it, and never echo the value."""
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("HANOVA_HOME_NETWORKS", "203.0.113.0/24,SENTINEL_PRIVATE_x7")
    networks = settings.home_networks()
    assert len(networks) == 1
    assert "SENTINEL_PRIVATE_x7" not in caplog.text
```

Create `reachy_companion/tests/test_hanova_redact.py`:

```python
"""The one redaction helper every ported tool logs and errors through (finding 7).

Every sentinel here is **synthetic**. A test that hunts for private identifiers
must never contain a real one -- that would put the identifier into the tracked
repository, which is the thing the test exists to prevent (finding 6).
"""

import errno
import logging

import pytest

from reachy_companion.hanova import redact


SENTINEL = "SENTINEL_PRIVATE_x7"


class _HttpError(Exception):
    """Shaped like the exceptions httpx and the Google/Notion layers raise."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


def test_count_reports_a_number_never_the_items():
    """A list of results is a count, not a list of titles."""
    assert redact.count(["a", "b", SENTINEL]) == "<3 items>"
    assert redact.count([]) == "<empty>"
    assert SENTINEL not in redact.count([SENTINEL])


def test_text_reports_a_length_never_the_text():
    """Subjects, queries and note bodies are lengths in a log line."""
    rendered = redact.text(f"dinner with {SENTINEL}")
    assert rendered.startswith("<text:") and rendered.endswith("chars>")
    assert SENTINEL not in rendered
    assert redact.text("") == "<empty>"
    assert redact.text(None) == "<none>"


def test_ident_is_a_stable_digest_never_the_identifier():
    """Two log lines about the same file must correlate without naming it."""
    first = redact.ident(SENTINEL)
    second = redact.ident(SENTINEL)
    assert first == second
    assert first.startswith("<id:") and len(first) == len("<id:") + 8 + 1
    assert SENTINEL not in first
    assert redact.ident(SENTINEL) != redact.ident(SENTINEL + "2")


def test_error_takes_the_status_from_the_exception_never_from_the_text():
    """Round 3, finding 3: structure comes from attributes, not from tokenizing.

    An HTTP status is useful; the response body is someone's data. The status
    below is on the object, and the body says 404 too -- only the attribute may
    be the reason it appears.
    """
    rendered = redact.error(_HttpError(f"404 Not Found: {SENTINEL}", status_code=404))
    assert rendered == "_HttpError(404)"
    assert SENTINEL not in rendered


def test_error_never_keeps_a_token_lifted_out_of_the_raw_message():
    """Round 3, finding 3: the `E[A-Z]+` rule passed shouty identifiers through.

    Every message here contains something the old regex would have kept -- an
    all-caps `E...` token, a bare status, an errno *spelled in the text* rather
    than set on the object. None of them may survive, because none of them came
    from an attribute this helper can trust.
    """
    for message in (
        f"EVENTID {SENTINEL} could not be deleted",
        f"404 Not Found: {SENTINEL}",
        f"{SENTINEL} ECONNREFUSED",
    ):
        rendered = redact.error(RuntimeError(message))
        assert rendered == "RuntimeError", rendered
        for leaked in (SENTINEL, "EVENTID", "404", "ECONNREFUSED"):
            assert leaked not in rendered


def test_error_reports_an_allow_listed_errno_read_off_the_exception():
    """A transport shape is worth keeping, and it cannot carry content."""
    rendered = redact.error(OSError(errno.ECONNREFUSED, f"refused by {SENTINEL}"))
    assert "ECONNREFUSED" in rendered
    assert SENTINEL not in rendered


def test_error_on_a_bare_string_reveals_nothing_at_all():
    """Tools sometimes surface a message they built themselves.

    Round 3, finding 3: a string carries no attributes, so there is nothing to
    trust in it and the rendering is the bare word.
    """
    assert redact.error(f"could not reach {SENTINEL}") == "error"
    assert SENTINEL not in redact.error(f"could not reach {SENTINEL}")


def test_safe_log_fields_is_a_closed_whitelist():
    """A field not on this list may not be logged verbatim by any ported tool."""
    assert "status" in redact.SAFE_LOG_FIELDS
    assert "count" in redact.SAFE_LOG_FIELDS
    for forbidden in ("title", "query", "subject", "to", "path", "url", "file_id"):
        assert forbidden not in redact.SAFE_LOG_FIELDS


def test_redaction_survives_a_non_string_input():
    """Never raise inside a log call; that would break the thing being logged."""
    assert redact.text(object()) == "<text:unprintable>"  # type: ignore[arg-type]
    assert redact.ident(None) == "<none>"


@pytest.mark.parametrize("level", [logging.DEBUG, logging.INFO, logging.WARNING])
def test_helper_output_is_safe_at_every_level(caplog, level):
    """caplog sentinel guard: nothing the helper emits carries the sentinel."""
    caplog.set_level(logging.DEBUG)
    logger = logging.getLogger("reachy_companion.hanova.redact.selftest")
    logger.log(level, "probe %s %s %s", redact.text(SENTINEL), redact.ident(SENTINEL), redact.count([SENTINEL]))
    assert SENTINEL not in caplog.text
```

- [ ] **Step 2: Run test to verify it fails**

```powershell
cd C:\Project\Reachy-mini\reachy_companion
..\.venv\Scripts\python.exe -m pytest tests/test_hanova_settings.py tests/test_hanova_redact.py -q
```

Expected: two collection errors — `ModuleNotFoundError: No module named 'reachy_companion.hanova'`.

- [ ] **Step 3: Create the package, the redaction helper and the settings module**

Create `reachy_companion/src/reachy_companion/hanova/__init__.py`:

```python
"""Ported HomeAssistant-Nova service layers (D-018).

Adapted from the operator's `HomeAssistant-Nova` repo (read-only clone at
`reference/HomeAssistant-Nova`). Every personal identifier the upstream code
hardcoded -- calendar id, task-list id, Drive folder id, account address, HA
entity ids, NAS share and credentials -- is read from the environment through
`reachy_companion.hanova.settings` instead. Nothing in this package embeds a
private value, and no tool description in `reachy_companion/tools/` may either.
"""
```

Create `reachy_companion/src/reachy_companion/hanova/settings.py`:

```python
"""Single config surface for the ported HomeAssistant-Nova capabilities (D-018).

Nothing here may raise. Tools are constructed inside `_build_tool_registry()`
(`tools/core_tools.py:291`), which is *not* exception-guarded, so a malformed
value must degrade to its documented default rather than abort startup.

Only **generic** defaults live here (a vendor endpoint, a behaviour constant, a
timezone). Every value that upstream hardcoded from the operator's own setup --
an identifier, a share or folder name, an HA script name -- defaults to the
empty string, which disables the tools that need it (R5, review finding 6).

Numeric readers take **mandatory finite bounds** (review finding 13): a zero HTTP
timeout, a negative keep count or an infinite confirmation TTL are all reachable
from a typo in `.env`, and each one is a real failure mode -- an immediate
timeout, a destructive prune, a gate that never expires.

Nothing here logs a configured **value**; a disabled verdict names the missing
**key** (review finding 7).
"""

from __future__ import annotations

import os
import math
import logging
import ipaddress
from typing import Any, Dict, List, Tuple
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Round 2, finding 6: this module logs too, so it goes through the same helper
# every tool does. `redact` imports nothing from `hanova`, so there is no cycle.
from reachy_companion.hanova import redact


logger = logging.getLogger(__name__)


# --- primitive readers -----------------------------------------------------
def env_str(name: str, default: str = "") -> str:
    """Read a stripped string env var, falling back to *default* when blank."""
    return (os.getenv(name) or "").strip() or default


def env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    """Read a bounded int env var; anything outside the range yields *default*.

    Bounds are keyword-only and mandatory so a new accessor cannot be added
    without deciding what its legal range actually is (review finding 13).
    """
    raw = env_str(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("%s is not an integer; using the default.", name)
        return default
    if not (minimum <= value <= maximum):
        logger.warning("%s is outside [%d, %d]; using the default.", name, minimum, maximum)
        return default
    return value


def env_float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    """Read a bounded, finite float env var; anything else yields *default*."""
    raw = env_str(name)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("%s is not a number; using the default.", name)
        return default
    if not math.isfinite(value):
        logger.warning("%s is not finite; using the default.", name)
        return default
    if not (minimum <= value <= maximum):
        logger.warning("%s is outside [%s, %s]; using the default.", name, minimum, maximum)
        return default
    return value


def env_path(name: str, default: Path | None = None) -> Path | None:
    """Read a filesystem path env var with `~` expansion, or *default* when unset.

    Round 2, finding 6: the rejected value used to be interpolated into the
    warning with a raw repr. A path is exactly the kind of identifier `redact`
    exists for -- it names the operator's home directory, their NAS layout, or
    their account -- so the log line carries the **key name and a length**,
    never the value.
    """
    raw = env_str(name)
    if not raw:
        return default
    try:
        return Path(raw).expanduser()
    except (OSError, RuntimeError) as exc:
        logger.warning("%s is not a usable path (%s): %s", name, redact.text(raw), redact.error(exc))
        return default


# --- Home Assistant (existing keys, reused per manifest section D) ---------
def ha_url() -> str:
    """Base LAN URL of Home Assistant, without a trailing slash."""
    return env_str("HA_URL").rstrip("/")


def ha_token() -> str:
    """Home Assistant long-lived token (a JWT that must carry an `exp` claim)."""
    return env_str("HA_TOKEN")


# --- general ---------------------------------------------------------------
_DEFAULT_TZ = "Asia/Taipei"


def timezone_name() -> str:
    """IANA timezone used for calendar/task times (R11), validated at read time.

    An unknown zone here would surface much later as a `ZoneInfoNotFoundError`
    deep inside a calendar tool, so it is checked against the tz database now
    and degraded to the default (review finding 13).
    """
    raw = env_str("HANOVA_TZ", _DEFAULT_TZ)
    try:
        ZoneInfo(raw)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        logger.warning("HANOVA_TZ is not a known IANA timezone; using %s.", _DEFAULT_TZ)
        return _DEFAULT_TZ
    return raw


def cast_entity() -> str:
    """Optional media_player entity id, forwarded to the HA cast scripts.

    Not a prerequisite for anything: the cast scripts have their own target. When
    it is set it is passed through as `entity_id` so one robot can drive a
    specific TV in a house with several (review finding 10).
    """
    return env_str("HANOVA_CAST_ENTITY")


# --- Home Assistant script names (no defaults -- review finding 6) ---------
def ha_script_youtube() -> str:
    """Name of the HA script that launches a YouTube id on the TV."""
    return env_str("HANOVA_HA_SCRIPT_YOUTUBE")


def ha_script_image_url() -> str:
    """Name of the HA script that casts an image URL to the TV."""
    return env_str("HANOVA_HA_SCRIPT_IMAGE_URL")


def ha_script_video_url() -> str:
    """Name of the HA script that casts a video URL to the TV."""
    return env_str("HANOVA_HA_SCRIPT_VIDEO_URL")


# --- media cache and serving (R6) ------------------------------------------
def media_http_base() -> str:
    """LAN base URL the TV dereferences, without a trailing slash."""
    return env_str("HANOVA_MEDIA_HTTP_BASE").rstrip("/")


def media_dir_override() -> Path | None:
    """Optional override for the media cache root; default is the instance dir."""
    return env_path("HANOVA_MEDIA_DIR")


def music_keep() -> int:
    """How many cached music files to retain (upstream default 12)."""
    return env_int("HANOVA_MUSIC_KEEP", 12, minimum=1, maximum=1000)


def nas_cast_keep() -> int:
    """How many staged NAS MP4s to retain (upstream default 8)."""
    return env_int("HANOVA_NAS_CAST_KEEP", 8, minimum=1, maximum=1000)


def image_keep() -> int:
    """How many generated TV images to retain."""
    return env_int("HANOVA_IMAGE_KEEP", 12, minimum=1, maximum=1000)


# --- Google Calendar / Tasks -----------------------------------------------
def google_account() -> str:
    """Google account whose OAuth JSON is used for Calendar and Tasks."""
    return env_str("HANOVA_GOOGLE_ACCOUNT")


def google_creds_dir() -> Path | None:
    """Directory holding `<account>.json`; must be writable (gauth rewrites it)."""
    return env_path("GOOGLE_CREDS_DIR")


def gcal_calendar_id() -> str:
    """Default calendar id for add/list/delete."""
    return env_str("HANOVA_GCAL_CALENDAR_ID")


def gtasks_list_id() -> str:
    """Default Google Tasks list id for task_add."""
    return env_str("HANOVA_GTASKS_LIST_ID")


def cal_delete_window_days() -> int:
    """Forward search window, in days, for resolving a calendar_delete match."""
    return env_int("HANOVA_CAL_DELETE_WINDOW_DAYS", 14, minimum=1, maximum=365)


# --- Google Drive (separate OAuth grant) -----------------------------------
def drive_secrets_path() -> Path | None:
    """Path to the Drive OAuth secret JSON (flat or nested shape)."""
    return env_path("HERMES_DRIVE_SECRETS")


def drive_parent_id() -> str:
    """Drive folder id that drive_list reads and drive_upload writes into."""
    return env_str("HANOVA_DRIVE_PARENT_ID")


# --- Notion (distinct from the NOTION_MCP_* remote lane) -------------------
def notion_token() -> str:
    """Notion internal-integration bearer token."""
    return env_str("HANOVA_NOTION_TOKEN")


def notion_data_source_id() -> str:
    """Notion data-source id the notes database writes into."""
    return env_str("HANOVA_NOTION_DATA_SOURCE_ID")


def notion_title_prop() -> str:
    """Name of the title property on that data source."""
    return env_str("HANOVA_NOTION_TITLE_PROP", "Name")


# --- Email -----------------------------------------------------------------
def smtp_host() -> str:
    """SMTP host for email_send."""
    return env_str("HANOVA_SMTP_HOST", "smtp.gmail.com")


def smtp_port() -> int:
    """SMTP SSL port for email_send."""
    return env_int("HANOVA_SMTP_PORT", 465, minimum=1, maximum=65535)


def smtp_user() -> str:
    """SMTP username / sender address."""
    return env_str("HANOVA_SMTP_USER")


def smtp_app_password() -> str:
    """SMTP app password. Never logged."""
    return env_str("HANOVA_SMTP_APP_PASSWORD")


def smtp_from_name() -> str:
    """Display name on outgoing mail."""
    return env_str("HANOVA_SMTP_FROM_NAME", "Reachy")


# --- NAS -------------------------------------------------------------------
def nas_host() -> str:
    """NAS host or IP for SMB access."""
    return env_str("HANOVA_NAS_HOST")


def nas_user() -> str:
    """SMB username."""
    return env_str("HANOVA_NAS_USER")


def nas_password() -> str:
    """SMB password. Never logged."""
    return env_str("HANOVA_NAS_PASSWORD")


def nas_share() -> str:
    """SMB share name. No default: this is the operator's own share (finding 6)."""
    return env_str("HANOVA_NAS_SHARE")


def nas_subpath() -> str:
    """The only folder inside the share we ever read. No default (finding 6).

    Every relative path taken from the index is validated to normalise **inside**
    this subtree before it reaches an SMB path (see `nas.py`, review finding 15).
    """
    return env_str("HANOVA_NAS_SUBPATH")


def nas_cast_subpath() -> str:
    """Folder holding the pre-transcoded, cast-ready MP4s. No default (finding 6)."""
    return env_str("HANOVA_NAS_CAST_SUBPATH")


def nas_index_path() -> Path | None:
    """Path to the operator-supplied `nas-video-index.json`."""
    return env_path("HANOVA_NAS_INDEX_PATH")


# --- yt-dlp ----------------------------------------------------------------
def ytdlp_search_n() -> int:
    """How many YouTube search results yt-dlp considers."""
    return env_int("HANOVA_YTDLP_SEARCH_N", 5, minimum=1, maximum=25)


def ytdlp_timeout_s() -> int:
    """Per-attempt timeout for a yt-dlp search. Zero would abort instantly."""
    return env_int("HANOVA_YTDLP_TIMEOUT_S", 20, minimum=1, maximum=300)


def ytdlp_download_timeout_s() -> int:
    """Timeout for a yt-dlp audio download plus transcode."""
    return env_int("HANOVA_YTDLP_DOWNLOAD_TIMEOUT_S", 120, minimum=5, maximum=1800)


def self_destruct_yt_id() -> str:
    """YouTube id of the self-destruct gag clip."""
    return env_str("HANOVA_SELF_DESTRUCT_YT_ID")


def mad_laugh_yt_id() -> str:
    """YouTube id of the mad-laugh gag clip."""
    return env_str("HANOVA_MAD_LAUGH_YT_ID")


# --- behaviour knobs -------------------------------------------------------
def confirm_ttl_s() -> float:
    """Seconds a pending confirmation stays valid (R3, upstream default 90).

    Bounded below by 1 s (a zero TTL would expire every confirmation before the
    user could speak) and above by 600 s (an authorisation that outlives the
    conversation is exactly what review finding 3 is about).
    """
    return env_float("HANOVA_CONFIRM_TTL_S", 90.0, minimum=1.0, maximum=600.0)


def home_probe_timeout_s() -> float:
    """Timeout of the home-network probe (R4). Zero would mean "always away"."""
    return env_float("HANOVA_HOME_PROBE_TIMEOUT_S", 1.5, minimum=0.1, maximum=30.0)


def home_cache_ttl_s() -> float:
    """How long a home verdict is cached (R4). Zero is legal: always re-probe."""
    return env_float("HANOVA_HOME_CACHE_TTL_S", 30.0, minimum=0.0, maximum=3600.0)


def home_networks() -> List[Any]:
    """The operator's declared home network(s) as CIDRs (round 2, finding 3).

    This is the **only** source of positive off-home evidence. `home_net.py` may
    answer `AWAY` when the robot's own address is outside every network listed
    here -- that is a fact about routing, not a guess. With this unset there is
    no evidence that could justify `AWAY`, and `home_net` degrades to
    `HOME`/`UNKNOWN` only: it will never tell the user they are out of the house
    on the strength of a failed connection.

    Comma-separated. Each entry is an IPv4 or IPv6 network in CIDR form; a bare
    address is accepted and treated as a /32 or /128. Anything unparseable is
    dropped with a key-only warning, because a malformed entry must not silently
    widen the definition of "home".
    """
    raw = env_str("HANOVA_HOME_NETWORKS")
    if not raw:
        return []
    networks: List[Any] = []
    for item in raw.split(","):
        candidate = item.strip()
        if not candidate:
            continue
        try:
            networks.append(ipaddress.ip_network(candidate, strict=False))
        except ValueError:
            # Finding 7: the value is the operator's LAN. Length only.
            logger.warning("HANOVA_HOME_NETWORKS has an unparseable entry (%s); ignoring it.", redact.text(candidate))
    return networks


def image_model() -> str:
    """OpenAI Images model used by show_on_tv."""
    return env_str("HANOVA_IMAGE_MODEL", "gpt-image-1")


# --- media mount readiness (R6, review finding 11) -------------------------
# `media_store.mount_media_routes()` writes this at startup. It lives here, not
# in media_store, so the prerequisite table has no import cycle and no lazy
# import: casting a URL that nothing will serve is a configuration failure, and
# it belongs in the same place as every other configuration failure.
_MEDIA_MOUNT_READY = False


def set_media_mount_ready(ready: bool) -> None:
    """Record whether the `/hanova-media` static route actually came up."""
    global _MEDIA_MOUNT_READY
    _MEDIA_MOUNT_READY = bool(ready)


def media_mount_ready() -> bool:
    """Return whether the LAN media route is live in this process."""
    return _MEDIA_MOUNT_READY


# --- per-tool prerequisites (R5, review finding 10) ------------------------
FAMILIES: tuple[str, ...] = (
    "google-workspace",
    "drive",
    "notion",
    "email",
    "nas",
    "media-cast",
    "music",
)

FAMILY_TOOLS: Dict[str, Tuple[str, ...]] = {
    "google-workspace": (
        "calendar_add",
        "calendar_list",
        "calendar_delete",
        "task_add",
        "task_list",
        "task_complete",
        "task_delete",
    ),
    "drive": ("drive_list", "drive_trash", "drive_upload"),
    "notion": ("notion_add",),
    "email": ("email_send",),
    "nas": ("nas_video_query", "play_nas_video", "nas_play_folder", "nas_skip"),
    "media-cast": ("play_video", "show_on_tv"),
    "music": ("play_music", "stop_music", "self_destruct", "mad_laugh"),
}


def _music_wheels_ready() -> tuple[bool, str]:
    """Return whether yt-dlp and a usable ffmpeg binary are both importable."""
    try:
        import yt_dlp  # noqa: F401
    except Exception:
        return False, "yt-dlp not installed"
    try:
        import imageio_ffmpeg

        if not imageio_ffmpeg.get_ffmpeg_exe():
            return False, "imageio-ffmpeg binary unavailable"
    except Exception:
        return False, "imageio-ffmpeg binary unavailable"
    return True, ""


def _google_creds_file_ready() -> bool:
    creds_dir = google_creds_dir()
    account = google_account()
    if creds_dir is None or not account:
        return False
    return (creds_dir / f"{account}.json").is_file()


def _google_creds_dir_writable() -> bool:
    # gauth rewrites `<account>.json` on every token refresh, so a read-only
    # directory is a working credential that will stop working within the hour.
    creds_dir = google_creds_dir()
    return creds_dir is not None and os.access(creds_dir, os.W_OK)


def _nas_index_ready() -> bool:
    index = nas_index_path()
    return index is not None and index.is_file()


# Each prerequisite is a key name plus a predicate. The key name is what a
# disabled tool reports, so it is always a *config key*, never a config value.
_PREREQS: Dict[str, Any] = {
    "HA_URL": lambda: bool(ha_url()),
    "HA_TOKEN": lambda: bool(ha_token()),
    "HANOVA_HA_SCRIPT_YOUTUBE": lambda: bool(ha_script_youtube()),
    "HANOVA_HA_SCRIPT_IMAGE_URL": lambda: bool(ha_script_image_url()),
    "HANOVA_HA_SCRIPT_VIDEO_URL": lambda: bool(ha_script_video_url()),
    "HANOVA_MEDIA_HTTP_BASE": lambda: bool(media_http_base()),
    "HANOVA_MEDIA_MOUNT": media_mount_ready,
    "OPENAI_API_KEY": lambda: bool((os.getenv("OPENAI_API_KEY") or "").strip()),
    "GOOGLE_CREDS_DIR": lambda: google_creds_dir() is not None,
    "HANOVA_GOOGLE_ACCOUNT": lambda: bool(google_account()),
    "GOOGLE_CREDS_FILE": _google_creds_file_ready,
    "GOOGLE_CREDS_DIR not writable": _google_creds_dir_writable,
    "HANOVA_GCAL_CALENDAR_ID": lambda: bool(gcal_calendar_id()),
    "HANOVA_GTASKS_LIST_ID": lambda: bool(gtasks_list_id()),
    "HERMES_DRIVE_SECRETS": lambda: drive_secrets_path() is not None,
    "DRIVE_SECRET_FILE": lambda: (drive_secrets_path() or Path("/nonexistent")).is_file(),
    "HANOVA_DRIVE_PARENT_ID": lambda: bool(drive_parent_id()),
    "HANOVA_NOTION_TOKEN": lambda: bool(notion_token()),
    "HANOVA_NOTION_DATA_SOURCE_ID": lambda: bool(notion_data_source_id()),
    "HANOVA_SMTP_USER": lambda: bool(smtp_user()),
    "HANOVA_SMTP_APP_PASSWORD": lambda: bool(smtp_app_password()),
    "HANOVA_NAS_INDEX_PATH": lambda: nas_index_path() is not None,
    "NAS_INDEX_FILE": _nas_index_ready,
    "HANOVA_NAS_HOST": lambda: bool(nas_host()),
    "HANOVA_NAS_USER": lambda: bool(nas_user()),
    "HANOVA_NAS_PASSWORD": lambda: bool(nas_password()),
    "HANOVA_NAS_SHARE": lambda: bool(nas_share()),
    "HANOVA_NAS_SUBPATH": lambda: bool(nas_subpath()),
    "HANOVA_NAS_CAST_SUBPATH": lambda: bool(nas_cast_subpath()),
    "HANOVA_SELF_DESTRUCT_YT_ID": lambda: bool(self_destruct_yt_id()),
    "HANOVA_MAD_LAUGH_YT_ID": lambda: bool(mad_laugh_yt_id()),
    "MUSIC_WHEELS": lambda: _music_wheels_ready()[0],
}

_GOOGLE_BASE: Tuple[str, ...] = (
    "GOOGLE_CREDS_DIR",
    "HANOVA_GOOGLE_ACCOUNT",
    "GOOGLE_CREDS_FILE",
    "GOOGLE_CREDS_DIR not writable",
)
_DRIVE_BASE: Tuple[str, ...] = ("HERMES_DRIVE_SECRETS", "DRIVE_SECRET_FILE", "HANOVA_DRIVE_PARENT_ID")
_HA_BASE: Tuple[str, ...] = ("HA_URL", "HA_TOKEN")
_URL_CAST_BASE: Tuple[str, ...] = _HA_BASE + ("HANOVA_MEDIA_HTTP_BASE", "HANOVA_MEDIA_MOUNT")
_NAS_FETCH: Tuple[str, ...] = (
    "HANOVA_NAS_INDEX_PATH",
    "NAS_INDEX_FILE",
    "HANOVA_NAS_HOST",
    "HANOVA_NAS_USER",
    "HANOVA_NAS_PASSWORD",
    "HANOVA_NAS_SHARE",
    "HANOVA_NAS_SUBPATH",
    "HANOVA_NAS_CAST_SUBPATH",
)

TOOL_PREREQS: Dict[str, Tuple[str, ...]] = {
    # music -- `stop_music` is deliberately unconditional: it is the safety lane
    # that must answer even when nothing else can (review finding 10).
    "play_music": ("MUSIC_WHEELS",),
    "stop_music": (),
    "self_destruct": ("MUSIC_WHEELS", "HANOVA_SELF_DESTRUCT_YT_ID"),
    "mad_laugh": ("MUSIC_WHEELS", "HANOVA_MAD_LAUGH_YT_ID"),
    # media-cast -- play_video hands HA an id and serves nothing, so it needs
    # neither the LAN base nor a live media mount.
    "play_video": ("MUSIC_WHEELS",) + _HA_BASE + ("HANOVA_HA_SCRIPT_YOUTUBE",),
    "show_on_tv": _URL_CAST_BASE + ("HANOVA_HA_SCRIPT_IMAGE_URL", "OPENAI_API_KEY"),
    # nas -- querying the index is local and needs no SMB credentials at all.
    "nas_video_query": ("HANOVA_NAS_INDEX_PATH", "NAS_INDEX_FILE"),
    "play_nas_video": _NAS_FETCH + _URL_CAST_BASE + ("HANOVA_HA_SCRIPT_VIDEO_URL",),
    "nas_play_folder": _NAS_FETCH + _URL_CAST_BASE + ("HANOVA_HA_SCRIPT_VIDEO_URL",),
    "nas_skip": _NAS_FETCH + _URL_CAST_BASE + ("HANOVA_HA_SCRIPT_VIDEO_URL",),
    # google-workspace
    "calendar_add": _GOOGLE_BASE + ("HANOVA_GCAL_CALENDAR_ID",),
    "calendar_list": _GOOGLE_BASE + ("HANOVA_GCAL_CALENDAR_ID",),
    "calendar_delete": _GOOGLE_BASE + ("HANOVA_GCAL_CALENDAR_ID",),
    "task_add": _GOOGLE_BASE + ("HANOVA_GTASKS_LIST_ID",),
    "task_list": _GOOGLE_BASE,
    "task_complete": _GOOGLE_BASE,
    "task_delete": _GOOGLE_BASE,
    # drive
    "drive_list": _DRIVE_BASE,
    "drive_trash": _DRIVE_BASE,
    "drive_upload": _DRIVE_BASE,
    # notion / email
    "notion_add": ("HANOVA_NOTION_TOKEN", "HANOVA_NOTION_DATA_SOURCE_ID"),
    "email_send": ("HANOVA_SMTP_USER", "HANOVA_SMTP_APP_PASSWORD"),
}


def tool_status(tool_name: str) -> tuple[bool, str]:
    """Return (available, reason) for one ported tool. Never raises.

    *reason* is the name of the first unmet prerequisite -- a configuration key,
    never a configuration value, so it is safe to log and safe to hand to the
    model (review finding 7).
    """
    prereqs = TOOL_PREREQS.get(tool_name)
    if prereqs is None:
        return False, "unknown tool"
    for key in prereqs:
        predicate = _PREREQS.get(key)
        if predicate is None:  # pragma: no cover - a typo in the table
            return False, key
        try:
            if not predicate():
                return False, key
        except Exception:  # noqa: BLE001 - a tool call must never abort here
            logger.warning("hanova prerequisite %s could not be evaluated.", key)
            return False, key
    return True, ""


def tool_available(tool_name: str) -> bool:
    """Return whether one ported tool has everything it needs."""
    return tool_status(tool_name)[0]


def family_status(family: str) -> tuple[str, str]:
    """Aggregate a family's tools into a tri-state startup verdict. Never raises.

    `enabled` -- every tool in the family is available.
    `partial` -- some are, some are not; the reason carries the ratio and the
    first blocking key, because a half-configured family is a real state the
    operator needs to see (review finding 10).
    `disabled` -- none are.
    """
    names = FAMILY_TOOLS.get(family)
    if names is None:
        return "disabled", "unknown family"
    ready: list[str] = []
    first_reason = ""
    for name in names:
        available, reason = tool_status(name)
        if available:
            ready.append(name)
        elif not first_reason:
            first_reason = reason
    if len(ready) == len(names):
        return "enabled", ""
    if not ready:
        return "disabled", first_reason
    return "partial", f"{len(ready)}/{len(names)} tools ready; first blocker {first_reason}"


def family_enabled(family: str) -> bool:
    """Return whether every tool in one family has everything it needs."""
    return family_status(family)[0] == "enabled"


def unavailable(reason: str = "not_configured") -> Dict[str, Any]:
    """The exact payload a tool returns when it is not configured (R5).

    *reason* is a prerequisite key name from `tool_status`, so the model can say
    which switch is missing without ever seeing what its value would be.
    """
    return {"status": "unavailable", "reason": reason}


def log_family_status() -> None:
    """Emit one INFO line per tool family at startup (R5)."""
    for family in FAMILIES:
        verdict, reason = family_status(family)
        if reason:
            logger.info("hanova family %s: %s (%s)", family, verdict, reason)
        else:
            logger.info("hanova family %s: %s", family, verdict)
```

Create `reachy_companion/src/reachy_companion/hanova/redact.py`:

```python
"""The one place a ported tool turns private data into something loggable.

Review round 1, finding 7 (scoped by the controller): every log line and every
free-text error surface produced by the 22 new tools is **metadata only** --
status, counts, durations, family, tool name. Never a title, a query, a subject,
a recipient, an id, a path or a URL, and never a raw API error body.

Scope note: this covers the *new* tool surface. The existing framework's
model-visible tool-result and assistant-content logging in
`huggingface_realtime.py` is explicitly out of scope for this plan.

Review round 3, finding 3: `error()` no longer looks at the raw message at all.
See its docstring for why tokenizing an error body can never be made safe.
"""

from __future__ import annotations

import os
import errno
import hashlib
from typing import Any


# A per-process salt so a digest cannot be reversed by rainbow table across
# deployments, while staying stable inside one run so two log lines about the
# same object correlate.
_SALT = os.urandom(16)

SAFE_LOG_FIELDS = frozenset(
    {"status", "count", "duration_ms", "family", "tool", "http_status", "cached", "ok"}
)

# Errno names that describe transport shape and can never carry content. They
# are matched against `errno.errorcode`, a closed stdlib table -- never against
# anything an API wrote (round 3, finding 3).
_ERRNO_ALLOWED = frozenset(
    {
        "EACCES", "EAFNOSUPPORT", "EAGAIN", "EBADF", "ECONNABORTED", "ECONNREFUSED",
        "ECONNRESET", "EHOSTUNREACH", "EINVAL", "EISDIR", "EMFILE", "ENETDOWN",
        "ENETUNREACH", "ENOENT", "ENOSPC", "ENOTDIR", "EPERM", "EPIPE", "ETIMEDOUT",
    }
)


def count(value: Any) -> str:
    """Render a collection as its size. Never renders an element."""
    try:
        size = len(value)
    except TypeError:
        return "<uncountable>"
    return "<empty>" if size == 0 else f"<{size} items>"


def text(value: Any) -> str:
    """Render free text as its length. Never renders the text."""
    if value is None:
        return "<none>"
    if not isinstance(value, str):
        return "<text:unprintable>"
    return "<empty>" if not value else f"<text:{len(value)} chars>"


def ident(value: Any) -> str:
    """Render an identifier as a stable salted digest. Never renders the id."""
    if value is None:
        return "<none>"
    if not isinstance(value, str):
        value = repr(value)
    digest = hashlib.blake2s(_SALT + value.encode("utf-8"), digest_size=4).hexdigest()
    return f"<id:{digest}>"


def _http_status(exc: BaseException) -> int | None:
    """Read an HTTP status off the exception OBJECT. Never parses a message."""
    response = getattr(exc, "response", None)
    for candidate in (
        getattr(exc, "status_code", None),
        getattr(exc, "status", None),
        getattr(response, "status_code", None),
        getattr(response, "status", None),
        getattr(exc, "code", None),          # urllib.error.HTTPError
    ):
        if isinstance(candidate, bool) or not isinstance(candidate, int):
            continue
        if 100 <= candidate <= 599:
            return candidate
    return None


def _errno_name(exc: BaseException) -> str | None:
    """Map the exception's own `errno` through the stdlib code table."""
    raw = getattr(exc, "errno", None)
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None
    return errno.errorcode.get(raw)


def error(exc_or_text: BaseException | str, *, allow_errno: tuple[str, ...] = ()) -> str:
    """Render a failure as its class plus structure taken from the object itself.

    The raw message is dropped: Google, Notion, Drive and SMTP all echo request
    content back inside their error bodies, and that body would otherwise land
    in the log and in the tool result the model reads aloud.

    **Round 3, finding 3.** The previous version tokenized the raw text and kept
    any token matching `^(?:[45]\\d{2}|E[A-Z]+)$` plus a caller-supplied word
    list. That is not a whitelist of *structure*, it is a whitelist of *shape* --
    every all-caps token starting with `E` passed, so an echoed identifier like
    `EVENTID_...` walked straight through the redactor, and so did a bare `404`
    that happened to sit inside somebody's note title. Raw text is now **never
    tokenized, never scanned and never returned**. Structure is read off the
    exception's own attributes:

    * an HTTP status from `status_code` / `status` / `response.status_code` /
      `code`, accepted only in the 100-599 range;
    * an errno **name** from `errno`, resolved through `errno.errorcode` -- a
      closed stdlib table -- and emitted only if it is in `_ERRNO_ALLOWED` or in
      the caller's *allow_errno*.

    A bare string has no attributes, so it renders as exactly `"error"`. That is
    the only safe rendering of text nobody has vouched for; a caller that wants
    shape from free text logs `redact.text(...)` beside it instead.
    """
    if not isinstance(exc_or_text, BaseException):
        return "error"
    label = type(exc_or_text).__name__
    parts: list[str] = []
    status = _http_status(exc_or_text)
    if status is not None:
        parts.append(str(status))
    name = _errno_name(exc_or_text)
    if name is not None and (name in _ERRNO_ALLOWED or name in allow_errno):
        parts.append(name)
    return f"{label}({' '.join(parts)})" if parts else label


__all__ = ["SAFE_LOG_FIELDS", "count", "error", "ident", "text"]
```

- [ ] **Step 4: Run test to verify it passes**

```powershell
cd C:\Project\Reachy-mini\reachy_companion
..\.venv\Scripts\python.exe -m pytest tests/test_hanova_settings.py tests/test_hanova_redact.py -q
```

Expected: green — **38 collected cases** from `test_hanova_settings.py`
(31 test functions, one of which is parametrised eight ways) and **12** from
`test_hanova_redact.py` (10 test functions, one parametrised three ways):
**50 total**. Round 2, finding 17: these are counted from the embedded test code,
not estimated. Round 3, finding 3 replaced one `error()` test with four, which is
why redact went from 8/10 to 10/12. Record the exact number `pytest -q` prints; a
later task that changes it must say why.

- [ ] **Step 5: Call the startup logger from main.py**

In `reachy_companion/src/reachy_companion/main.py`, find this exact block:

```python
    # US-07 / D-004: discover remote MCP tools before the registry is built, so
    # the first initialize_tools() already includes them. The persistent seam in
    # core_tools keeps them registered even if the registry is rebuilt later.
    _discover_remote_mcp_tools(logger)
```

and insert immediately **above** it:

```python
    # D-018 / R5: one INFO line per ported tool family, so a deploy can be read
    # off the log instead of guessed at. Runs after the instance .env is loaded
    # and before the registry is built, so the verdicts describe this boot.
    from reachy_companion.hanova.settings import log_family_status

    log_family_status()

```

- [ ] **Step 6: Add the three approved dependencies (R8)**

**Recorded evidence (external review round 1, 2026-08-21).** The reviewer ran
this resolution itself and it succeeded. All three additions resolve as aarch64
wheels, with these versions and transitive dependencies:

```
yt-dlp==2026.8.19
imageio-ffmpeg==0.6.0        # manylinux2014_aarch64, ffmpeg binary inside the wheel
smbprotocol==1.17.0
  ├─ pyspnego==0.12.1
  ├─ cryptography==50.0.0
  ├─ cffi==2.1.1
  └─ pycparser==3.0
```

That is the R8 proof; `imageio-ffmpeg` is confirmed over `static-ffmpeg`.
Re-verify it before editing `pyproject.toml` (the index moves), using this
**corrected** command. The previous version of this plan used a Bash `<<<`
here-string, which PowerShell does not have, and `--output-file -`, which the
installed `uv` writes to a literal file named `-` inside the repository (review
finding 8). Pipe a PowerShell here-string in on stdin and write the result to an
explicit temporary path **outside** the repo:

```powershell
cd C:\Project\Reachy-mini
$req = @'
yt-dlp>=2026.8.19
imageio-ffmpeg>=0.6.0
smbprotocol>=1.17.0
'@
$out = Join-Path $env:TEMP "hanova-aarch64-resolution.txt"
$req | uv pip compile - --python-platform aarch64-manylinux_2_28 --only-binary :all: --no-header -o $out
Get-Content $out
Select-String -Path $out -Pattern "yt-dlp|imageio-ffmpeg|smbprotocol"
```

Note what is **not** in that command: the real `pyproject.toml`. Resolving the
whole project with `--only-binary :all:` fails on the existing `reachy-mini`
Linux dependency chain before it ever reaches the three additions, so it proves
nothing about them (review finding 8). Only the three new requirements are
resolved here.

Expected: a resolved pin list naming all three, with no "no wheels are
available" line, and no file named `-` created anywhere (`git status --short`
after the command must be unchanged from the Task 0 baseline). If
`imageio-ffmpeg` fails to resolve for aarch64, STOP and report — do not silently
substitute `static-ffmpeg` (it downloads binaries at runtime, which breaks the
offline-robot assumption).

In `reachy_companion/pyproject.toml`, replace the `dependencies` line:

```toml
dependencies = [ "scipy>=1.11", "soxr>=1.0", "huggingface-hub>=1.17.0", "httpx>=0.28.1", "python-dotenv", "openai==2.28.0", "reachy_mini_dances_library", "reachy-mini>=1.10.0rc2", "mcp>=1.27.1,<2",]
```

with:

```toml
dependencies = [ "scipy>=1.11", "soxr>=1.0", "huggingface-hub>=1.17.0", "httpx>=0.28.1", "python-dotenv", "openai==2.28.0", "reachy_mini_dances_library", "reachy-mini>=1.10.0rc2", "mcp>=1.27.1,<2", "yt-dlp>=2026.8.19", "imageio-ffmpeg>=0.6.0", "smbprotocol>=1.17.0",]
```

Then install into the dev venv and prove the ffmpeg binary is real:

```powershell
cd C:\Project\Reachy-mini
.\.venv\Scripts\python.exe -m pip install "yt-dlp>=2026.8.19" "imageio-ffmpeg>=0.6.0" "smbprotocol>=1.17.0"
.\.venv\Scripts\python.exe -c "import yt_dlp, imageio_ffmpeg, smbclient; print(yt_dlp.version.__version__); print(imageio_ffmpeg.get_ffmpeg_exe())"
```

Expected: a yt-dlp version string and an absolute path to a bundled ffmpeg binary.

- [ ] **Step 7: Document every new key in `.env.example`**

Append this block to `reachy_companion/.env.example`, immediately after the existing `HA_ENTITIES=` line. **Placeholders only — never paste a real id, address, token or LAN address into this file; it is committed to a public repo.**

```dotenv
# --- HomeAssistant-Nova ported capabilities (D-018) ---
# Every family below is optional. With none of these set the app boots normally
# and each ported tool answers {"status":"unavailable","reason":"not_configured"}.
# Startup logs one line per family: `hanova family <name>: enabled|disabled (reason)`.

# Timezone used for calendar and task times.
HANOVA_TZ=Asia/Taipei

# --- media-cast family (play_video, show_on_tv, all nas_* casting) ---
# Reuses HA_URL / HA_TOKEN above.
# Names of the Home Assistant scripts that do the casting. There is NO default:
# these are entry names in the operator's own scripts.yaml, so an unset name
# disables the tool that needs it rather than guessing.
#   play_video       needs HANOVA_HA_SCRIPT_YOUTUBE
#   show_on_tv       needs HANOVA_HA_SCRIPT_IMAGE_URL
#   all nas_* casts  need  HANOVA_HA_SCRIPT_VIDEO_URL
HANOVA_HA_SCRIPT_YOUTUBE=
HANOVA_HA_SCRIPT_IMAGE_URL=
HANOVA_HA_SCRIPT_VIDEO_URL=
# Optional. When set it is forwarded to those scripts as `entity_id`, so one
# robot can target a specific TV in a house with several. Not required by any
# tool.
HANOVA_CAST_ENTITY=
# LAN base URL the TV/Chromecast fetches robot-served media from. Must be the
# robot's own LAN address (reserved IP, or a name the TV resolves) -- localhost
# is meaningless to the TV. Port 7860 is the app's own settings web server.
# Required by show_on_tv and by all nas_* casting; play_video does NOT need it.
HANOVA_MEDIA_HTTP_BASE=http://<robot-lan-ip>:7860
# Optional: move the media cache off the instance directory.
# HANOVA_MEDIA_DIR=
# LRU caps on the media cache (upstream defaults).
# HANOVA_MUSIC_KEEP=12
# HANOVA_NAS_CAST_KEEP=8
# HANOVA_IMAGE_KEEP=12
# OpenAI Images model used by show_on_tv (reuses OPENAI_API_KEY).
# HANOVA_IMAGE_MODEL=gpt-image-1

# --- music (robot speaker; needs no config, only the yt-dlp + ffmpeg wheels) ---
# HANOVA_YTDLP_SEARCH_N=5
# HANOVA_YTDLP_TIMEOUT_S=20
# HANOVA_YTDLP_DOWNLOAD_TIMEOUT_S=120
# YouTube ids of the two gag clips (self_destruct / mad_laugh). Blank disables them.
HANOVA_SELF_DESTRUCT_YT_ID=
HANOVA_MAD_LAUGH_YT_ID=

# --- google-workspace family (calendar_*, task_*) ---
# Directory holding <account>.json. It MUST be writable: the OAuth helper
# rewrites the file whenever it refreshes the access token.
GOOGLE_CREDS_DIR=
HANOVA_GOOGLE_ACCOUNT=<google-account>@example.com
# A shared calendar id looks like an address ending in .group.calendar.google.com;
# leave blank here and paste the real one into the robot's own .env only.
HANOVA_GCAL_CALENDAR_ID=
HANOVA_GTASKS_LIST_ID=<google-tasks-list-id>
# HANOVA_CAL_DELETE_WINDOW_DAYS=14

# --- drive family (drive_list, drive_trash, drive_upload) ---
# Path to the Drive OAuth secret JSON. NOTE: that grant carries full
# https://www.googleapis.com/auth/drive scope.
HERMES_DRIVE_SECRETS=
HANOVA_DRIVE_PARENT_ID=<drive-folder-id>

# --- notion family (notion_add) --- deliberately separate from NOTION_MCP_* above.
HANOVA_NOTION_TOKEN=
HANOVA_NOTION_DATA_SOURCE_ID=<notion-data-source-id>
# HANOVA_NOTION_TITLE_PROP=Name

# --- email family (email_send) --- sends real, irreversible outbound mail.
HANOVA_SMTP_USER=<smtp-account>@example.com
HANOVA_SMTP_APP_PASSWORD=
# HANOVA_SMTP_HOST=smtp.gmail.com
# HANOVA_SMTP_PORT=465
# HANOVA_SMTP_FROM_NAME=Reachy

# --- nas family (nas_video_query, play_nas_video, nas_play_folder, nas_skip) ---
# nas_video_query needs ONLY the index file below -- it reads local JSON and
# touches no SMB. The three casting tools need everything in this block.
HANOVA_NAS_HOST=<nas-host-or-ip>
HANOVA_NAS_USER=
HANOVA_NAS_PASSWORD=
# The share and the two folders inside it. NO defaults: these are the operator's
# own NAS layout, not something this project may guess. HANOVA_NAS_SUBPATH also
# defines the only subtree an index path is allowed to resolve inside.
HANOVA_NAS_SHARE=<smb-share-name>
HANOVA_NAS_SUBPATH=<folder-with-the-originals>
HANOVA_NAS_CAST_SUBPATH=<folder-with-the-cast-ready-mp4s>
# Operator-supplied index file; lives in the instance directory on the robot.
HANOVA_NAS_INDEX_PATH=

# --- confirmation gating and home-network probe ---
# Seconds a pending confirmation stays valid before it must be read back again.
# HANOVA_CONFIRM_TTL_S=90
# HANOVA_HOME_PROBE_TIMEOUT_S=1.5
# HANOVA_HOME_CACHE_TTL_S=30
# The operator's home network(s), comma separated, in CIDR form. This is the ONLY
# thing that can make a house-bound tool say "I am away from home": the robot's
# own address being outside every network listed here is positive evidence of
# being somewhere else. Leave it blank and the robot will never claim you are
# out of the house -- it will say it cannot tell (home_status_unknown) instead.
# A failed connection to Home Assistant is NEVER treated as absence.
HANOVA_HOME_NETWORKS=
```

- [ ] **Step 8: Run the full gate**

```powershell
cd C:\Project\Reachy-mini\reachy_companion
..\.venv\Scripts\python.exe -m pytest -q
..\.venv\Scripts\python.exe -m ruff check src tests
..\.venv\Scripts\python.exe -m ruff format --check src tests
..\.venv\Scripts\python.exe -m mypy
```

Expected: pytest green (30 skips are the documented locked-profile baseline), ruff clean, mypy `Success: no issues found`.

- [ ] **Step 9: Commit**

```bash
cd /c/Project/Reachy-mini
git rev-parse --abbrev-ref HEAD   # must print feat/ha-nova-port (Task 0)
git add reachy_companion/src/reachy_companion/hanova/__init__.py \
        reachy_companion/src/reachy_companion/hanova/settings.py \
        reachy_companion/src/reachy_companion/hanova/redact.py \
        reachy_companion/src/reachy_companion/main.py \
        reachy_companion/tests/test_hanova_settings.py \
        reachy_companion/tests/test_hanova_redact.py \
        reachy_companion/.env.example \
        reachy_companion/pyproject.toml
git commit -m "feat(hanova): config surface, per-tool prerequisites, redaction helper and startup logging"
git status --short
```

The `git status --short` must still show exactly the Task 0 baseline paths and
nothing else. Nothing was staged that this task did not name.

---

### Task 2: Home-network probe and the confirmation gate

Implements R3 and R4 — the two cross-cutting primitives every later tool depends
on — plus review round 1 findings 3 (the gate must be session-scoped, not
process-global), 4 (claim/complete/abort instead of a destructive `take()`) and
12 (the home verdict must be tri-state with a real LAN signal). Both modules are
small, pure, and fully testable without a network.

**Review round 2, finding 2 — an epoch is not enough; every claim gets an id.**
`complete()` and `release()` took only a tool name, so any holder of *any* claim
on that name could spend or re-arm whatever happened to be in the slot at the
moment it called. Two concrete losses: an operation still in flight from an
older session could `complete()` an action a *newer* session had just armed
(dropping the user's authorisation without executing it), and `arm()` could
overwrite a slot whose action was mid-execution (making a destructive action
claimable again while it was still running). Now `claim()` mints an opaque
`claim_id`; `complete()`, `release()` and a claim-bound `abort()` all **require**
it; and the gate compares epoch *and* claim id **inside the same lock
acquisition** as the mutation. Re-arming a claimed slot is refused outright with
its own status, `action_in_flight`.

**Review round 2, finding 3 — `AWAY` needs positive evidence, and every house
tool branches all three ways.** Round 1 left two holes. First, tools only tested
`AWAY`, so `UNKNOWN` fell through and did the work anyway — a VPN, a 401, a 5xx
or a timeout could still fire a house action. Second, `AWAY` was produced by a
*failed* TCP connect, which is exactly what an HA outage, a DNS failure or a
refused port look like; that contradicts the round-1 ruling that an HA outage is
`UNKNOWN`. Now: `AWAY` requires the robot's **own** address to be outside every
network the operator declared in `HANOVA_HOME_NETWORKS`, which is positive
routing evidence and is available even when Home Assistant is down. No
declaration means `AWAY` is unreachable. The `/24` guess is **demoted** to a
weak locality hint that can only *withhold* `HOME`; it can no longer produce
`AWAY`, so a `/22` or `/16` home LAN degrades to `UNKNOWN` rather than to a lie.

**Review round 2, finding 9 — transient and terminal failures are not the same
authorisation.** `release()` re-arms the action for a bare "try again". That is
right for a 503, a socket timeout or a dropped SMTP connection. It is wrong for
an authentication failure, a refused recipient or a validation error: the
*resolved action itself* is wrong, so the approval the user gave no longer
describes anything that can succeed. Terminal failures now **spend** the claim
(`complete()`), which forces a fresh read-back after the correction.

**Files:**
- Create: `reachy_companion/src/reachy_companion/home_net.py`
- Create: `reachy_companion/src/reachy_companion/hanova/confirm.py`
- Test: `reachy_companion/tests/test_home_net.py`
- Test: `reachy_companion/tests/test_hanova_confirm.py`

**Interfaces:**
- Consumes: `reachy_companion.hanova.settings.{ha_url, ha_token, home_probe_timeout_s, home_cache_ttl_s, home_networks, confirm_ttl_s}` (Task 1).
- Produces (`reachy_companion.home_net`, tri-state per finding 12 and round 2 finding 3):
  - `HOME: str`, `AWAY: str`, `UNKNOWN: str` — the three verdicts
  - `home_state() -> Awaitable[str]` — the real probe; returns one of the three
  - `away_from_home() -> Dict[str, Any]` returning exactly `{"status": "away_from_home"}`
  - `home_unknown() -> Dict[str, Any]` returning `{"status": "home_status_unknown", "error": <a fixed, speakable sentence>}` — "I cannot tell where I am", which is neither presence nor absence. It takes **no argument**: a caller-supplied detail is how an HA error body would have reached the model (finding 6)
  - `LanProbe` — frozen dataclass with `reachable: bool`, `local_address: str`, `same_subnet: bool`
  - `lan_signal(host: str, port: int, timeout_s: float) -> Awaitable[LanProbe]` — the socket-level seam tests monkeypatch. `local_address` is the source address the kernel actually used; `same_subnet` is the **demoted** /24-/64 hint (round 2, finding 3), which may only withhold `HOME` and can never produce `AWAY`
  - `local_address(host: str, timeout_s: float) -> Awaitable[str]` — the robot's own address on the route towards *host*, learned from an unconnected UDP socket that sends nothing. This is what makes an `AWAY` verdict possible while Home Assistant is down. Returns `""` when it cannot be determined
  - `reset_cache() -> None`
  - **`is_home()` is deliberately gone** (round 2, finding 3). A boolean cannot express three verdicts, and every one of its former call sites was a place where `UNKNOWN` silently became "yes, do the work". The Task 14 shape test asserts no tool contains the token.
- Produces (`reachy_companion.hanova.confirm`, epoch- **and claim-id**-scoped per findings 3, 4 and round 2 findings 2 and 9):
  - `PendingAction` — frozen dataclass with `tool_name: str`, `summary: str`, `payload: Dict[str, Any]`, `expires_at: float`, `epoch: str`, `claim_id: str`, `claimed_at: float | None`. `claim_id` is minted at **arm** time and is immutable for the life of the action; it is what `claim()` hands back and what every mutator requires
  - `ConfirmationGate` with:
    - `begin_session() -> str` — mint a new epoch and drop everything armed under the old one
    - `end_session() -> None` — drop everything; called on shutdown
    - `epoch() -> str`
    - `arm(tool_name, summary, payload) -> Dict[str, Any]` — stamps the **current** epoch and a fresh `claim_id`. **Refuses** to overwrite a slot whose action is already claimed, returning `action_in_flight()` instead (round 2, finding 2)
    - `claim(tool_name) -> PendingAction | None` — returns the pending action **without deleting it**, marking it in flight; returns None when absent, expired, in-flight already, or armed under a different epoch. The returned object carries the `claim_id` every subsequent call must present
    - `complete(tool_name, claim_id) -> bool` — the authorisation is spent. Returns False (and changes nothing) when the slot no longer holds that exact epoch+claim id
    - `release(tool_name, claim_id) -> bool` — a **transient** failure; put it back for a bare retry. Same epoch+claim-id check, same False on mismatch
    - `abort(tool_name, claim_id: str | None = None) -> Dict[str, Any]` — the user said no. With a `claim_id` it is the claim-bound abort and must match. **Without** one it may only drop an action that is *not* in flight; an in-flight action answers `action_in_flight()`, because a bare abort must not yank an operation that is already executing
    - `clear(tool_name) -> None`, `reset() -> None`
  - `GATE` — the process-wide instance, whose *contents* are always epoch-scoped
  - `confirmation_expired() -> Dict[str, Any]`
  - `action_in_flight() -> Dict[str, Any]` returning `{"status": "action_in_flight", "error": ...}` — the status re-arming a claimed slot gets (round 2, finding 2)
- **Gated-tool call shape (findings 3, 4 and round 2 findings 2 and 9).** Every gated tool in Tasks 7–12 uses exactly this body, and no other. Note the three things it now threads: the claim id, the transient/terminal split, and the `retryable` flag that only the transient branch sets:

```python
if bool(kwargs.get("confirm")):
    pending = GATE.claim(self.name)
    if pending is None:
        return confirmation_expired()
    try:
        result = await <do the thing using pending.payload only>
    except <the tool's error family> as exc:
        if is_transient(exc):
            # Transient: the approved action is still the right action.
            GATE.release(self.name, pending.claim_id)
            return {"ok": False, "error": friendly_message(exc), "retryable": True}
        # Terminal: the resolved action itself is wrong, so the approval no
        # longer describes anything that can succeed. Spend it and make the
        # model read a corrected action back (round 2, finding 9).
        GATE.complete(self.name, pending.claim_id)
        return {"ok": False, "error": friendly_message(exc)}
    GATE.complete(self.name, pending.claim_id)   # spent only on success
    return result
```

  Each family supplies its own `is_transient` / `friendly_message` pair —
  `hanova.gauth` for Calendar and Tasks, `hanova.gdrive` for Drive,
  `hanova.gmail_smtp` for email — so the classification lives next to the error
  family that defines it, and Task 14's shape test asserts every gated tool has
  **both** `GATE.release(self.name, pending.claim_id)` and
  `GATE.complete(self.name, pending.claim_id)` in its source.

- [ ] **Step 1: Write the failing tests**

Create `reachy_companion/tests/test_home_net.py`:

```python
"""Contract tests for the tri-state home-network probe (D-018, R4, finding 12,
round 2 finding 3).

Two rules the whole module exists to keep:

1. Only **positive off-home routing evidence** may be reported as
   `away_from_home` -- the robot's own address sitting outside every declared
   home network. A failed connection to Home Assistant is not that: an HA that
   is down, a refused port, a DNS failure, a 401, a 5xx and a VPN tunnel are all
   `UNKNOWN`.
2. With `HANOVA_HOME_NETWORKS` unset there is no evidence that could justify
   `AWAY` at all, so the verdict is only ever `HOME` or `UNKNOWN`.
"""

import asyncio

import httpx
import pytest

from reachy_companion import home_net


HOME_LAN = "203.0.113.0/24"
AT_HOME_ADDRESS = "203.0.113.20"
ELSEWHERE_ADDRESS = "198.51.100.20"


class _FakeResponse:
    """Minimal stand-in for the httpx response Home Assistant returns."""

    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code


def _lan(reachable: bool = True, same_subnet: bool = True, local: str = AT_HOME_ADDRESS):
    async def probe(host, port, timeout_s):
        return home_net.LanProbe(
            reachable=reachable,
            local_address=local if reachable else "",
            same_subnet=same_subnet,
        )

    return probe


def _local(address: str):
    async def resolve(host, timeout_s):
        return address

    return resolve


@pytest.fixture(autouse=True)
def clean_probe(monkeypatch):
    """Every test starts with an empty cache, a configured HA, and a good LAN."""
    home_net.reset_cache()
    monkeypatch.setenv("HA_URL", "http://ha.example.invalid:8123")
    monkeypatch.setenv("HA_TOKEN", "tok")
    monkeypatch.delenv("HANOVA_HOME_CACHE_TTL_S", raising=False)
    monkeypatch.delenv("HANOVA_HOME_PROBE_TIMEOUT_S", raising=False)
    monkeypatch.delenv("HANOVA_HOME_NETWORKS", raising=False)
    monkeypatch.setattr(home_net, "lan_signal", _lan())
    monkeypatch.setattr(home_net, "local_address", _local(AT_HOME_ADDRESS))
    yield
    home_net.reset_cache()


def test_away_payload_is_exactly_the_contract():
    """R4 fixes this shape; house-bound tools return it verbatim."""
    assert home_net.away_from_home() == {"status": "away_from_home"}


def test_unknown_payload_is_its_own_status_not_a_flavour_of_away():
    """Round 2, finding 3: "I cannot tell" gets its own name in the contract."""
    out = home_net.home_unknown()
    assert out["status"] == "home_status_unknown"
    assert out["status"] != "away_from_home"
    assert out["error"]


def test_unknown_payload_carries_no_caller_supplied_text():
    """Finding 6: a detail argument is how an HA error body would leak out."""
    import inspect

    signature = inspect.signature(home_net.home_unknown)
    assert list(signature.parameters) == [], "home_unknown() takes no arguments"


def test_the_is_home_shortcut_no_longer_exists():
    """Round 2, finding 3: a boolean cannot carry three verdicts."""
    assert not hasattr(home_net, "is_home")


@pytest.mark.asyncio
async def test_probe_hits_the_ha_api_root_with_the_token(monkeypatch):
    """The HTTP half is one authenticated GET on /api/, nothing heavier."""
    seen = {}

    async def fake_get(self, url, headers=None, **kw):
        seen["url"] = url
        seen["headers"] = headers
        return _FakeResponse(200)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    assert await home_net.home_state() == home_net.HOME
    assert seen["url"] == "http://ha.example.invalid:8123/api/"
    assert seen["headers"]["Authorization"] == "Bearer tok"


# --- AWAY needs positive evidence (round 2, finding 3) ---------------------
@pytest.mark.asyncio
async def test_an_address_outside_every_declared_home_network_is_away(monkeypatch):
    """The one and only thing that justifies telling the user they are out."""

    async def fake_get(self, url, headers=None, **kw):
        return _FakeResponse(200)

    monkeypatch.setenv("HANOVA_HOME_NETWORKS", HOME_LAN)
    monkeypatch.setattr(home_net, "lan_signal", _lan(local=ELSEWHERE_ADDRESS, same_subnet=False))
    monkeypatch.setattr(home_net, "local_address", _local(ELSEWHERE_ADDRESS))
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    assert await home_net.home_state() == home_net.AWAY


@pytest.mark.asyncio
async def test_off_home_is_away_even_when_home_assistant_is_unreachable(monkeypatch):
    """The evidence is our own routing, so it survives HA being down.

    This is what makes the verdict useful on a train: HA is unreachable *and*
    we are demonstrably on someone else's network.
    """

    async def fail_get(self, *args, **kwargs):
        raise AssertionError("no HTTP call is needed once routing already decided")

    monkeypatch.setenv("HANOVA_HOME_NETWORKS", HOME_LAN)
    monkeypatch.setattr(home_net, "lan_signal", _lan(reachable=False))
    monkeypatch.setattr(home_net, "local_address", _local(ELSEWHERE_ADDRESS))
    monkeypatch.setattr(httpx.AsyncClient, "get", fail_get)
    assert await home_net.home_state() == home_net.AWAY


@pytest.mark.asyncio
async def test_no_declared_home_network_can_never_produce_away(monkeypatch):
    """Round 2, finding 3: with nothing declared, absence is unprovable."""

    async def fail_get(self, *args, **kwargs):
        raise AssertionError("no HTTP call once the LAN signal has already failed")

    monkeypatch.setattr(home_net, "lan_signal", _lan(reachable=False))
    monkeypatch.setattr(home_net, "local_address", _local(""))
    monkeypatch.setattr(httpx.AsyncClient, "get", fail_get)
    assert await home_net.home_state() == home_net.UNKNOWN


@pytest.mark.asyncio
async def test_no_tcp_route_from_inside_the_home_network_is_unknown(monkeypatch):
    """Finding 3: a refused port at home is an HA outage, not absence."""

    async def fail_get(self, *args, **kwargs):
        raise AssertionError("no HTTP call once the LAN signal has already failed")

    monkeypatch.setenv("HANOVA_HOME_NETWORKS", HOME_LAN)
    monkeypatch.setattr(home_net, "lan_signal", _lan(reachable=False))
    monkeypatch.setattr(home_net, "local_address", _local(AT_HOME_ADDRESS))
    monkeypatch.setattr(httpx.AsyncClient, "get", fail_get)
    assert await home_net.home_state() == home_net.UNKNOWN


@pytest.mark.asyncio
async def test_a_dns_failure_is_unknown_not_away(monkeypatch):
    """Round 2, finding 3: name resolution is infrastructure, not location."""

    async def dns_failure(host, port, timeout_s):
        return home_net.LanProbe(reachable=False, local_address="", same_subnet=False)

    async def fail_get(self, *args, **kwargs):
        raise AssertionError("a DNS failure must not reach the HTTP layer")

    monkeypatch.setattr(home_net, "lan_signal", dns_failure)
    monkeypatch.setattr(home_net, "local_address", _local(""))
    monkeypatch.setattr(httpx.AsyncClient, "get", fail_get)
    assert await home_net.home_state() == home_net.UNKNOWN


@pytest.mark.asyncio
async def test_the_subnet_hint_can_withhold_home_but_never_assert_away(monkeypatch):
    """Round 2, finding 3: the /24 guess is demoted, not deleted.

    A /16 home LAN whose robot and HA sit in different /24s degrades to UNKNOWN
    -- honest and fixable with one config key -- rather than to a false AWAY.
    """

    async def fake_get(self, url, headers=None, **kw):
        return _FakeResponse(200)

    monkeypatch.setattr(home_net, "lan_signal", _lan(same_subnet=False))
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    assert await home_net.home_state() == home_net.UNKNOWN


@pytest.mark.asyncio
async def test_a_declared_home_network_overrides_the_subnet_hint(monkeypatch):
    """With a declaration, the /24 guess is not consulted at all."""

    async def fake_get(self, url, headers=None, **kw):
        return _FakeResponse(200)

    monkeypatch.setenv("HANOVA_HOME_NETWORKS", "203.0.113.0/16")
    monkeypatch.setattr(home_net, "lan_signal", _lan(same_subnet=False))
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    assert await home_net.home_state() == home_net.HOME


@pytest.mark.asyncio
async def test_unauthorized_is_unknown_not_away(monkeypatch):
    """Finding 12: an expired HA token must not be reported as absence."""

    async def fake_get(self, url, headers=None, **kw):
        return _FakeResponse(401)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    assert await home_net.home_state() == home_net.UNKNOWN


@pytest.mark.asyncio
async def test_server_error_is_unknown_not_away(monkeypatch):
    """An HA outage while the robot sits at home is not the robot being out."""

    async def fake_get(self, url, headers=None, **kw):
        return _FakeResponse(503)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    assert await home_net.home_state() == home_net.UNKNOWN


@pytest.mark.asyncio
async def test_http_timeout_on_a_reachable_host_is_unknown(monkeypatch):
    """The socket connected, so the robot is on a network that reaches HA."""

    async def slow_get(self, url, headers=None, **kw):
        raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr(httpx.AsyncClient, "get", slow_get)
    assert await home_net.home_state() == home_net.UNKNOWN


@pytest.mark.asyncio
async def test_reachable_over_a_vpn_is_unknown_not_home(monkeypatch):
    """Finding 12: remote access proves reachability, never presence."""

    async def fake_get(self, url, headers=None, **kw):
        return _FakeResponse(200)

    monkeypatch.setattr(home_net, "lan_signal", _lan(reachable=True, same_subnet=False))
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    assert await home_net.home_state() == home_net.UNKNOWN


@pytest.mark.asyncio
async def test_unconfigured_ha_is_unknown_without_any_request(monkeypatch):
    """No HA_URL means no probe at all -- not a 1.5 s wait on nothing."""

    async def fail_get(self, *args, **kwargs):
        raise AssertionError("home_state must not probe when HA_URL is unset")

    async def fail_lan(*args, **kwargs):
        raise AssertionError("home_state must not open a socket when HA_URL is unset")

    monkeypatch.delenv("HA_URL")
    monkeypatch.setattr(home_net, "lan_signal", fail_lan)
    monkeypatch.setattr(httpx.AsyncClient, "get", fail_get)
    assert await home_net.home_state() == home_net.UNKNOWN


@pytest.mark.asyncio
async def test_verdict_is_cached_for_the_ttl(monkeypatch):
    """A second call inside the TTL must not touch the network again."""
    calls = {"n": 0}

    async def counting_get(self, url, headers=None, **kw):
        calls["n"] += 1
        return _FakeResponse(200)

    monkeypatch.setattr(httpx.AsyncClient, "get", counting_get)
    assert await home_net.home_state() == home_net.HOME
    assert await home_net.home_state() == home_net.HOME
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_away_and_unknown_verdicts_are_cached_too(monkeypatch):
    """Neither of the negative verdicts may re-probe on every tool call."""
    calls = {"n": 0}

    async def counting_lan(host, port, timeout_s):
        calls["n"] += 1
        return home_net.LanProbe(reachable=False, local_address="", same_subnet=False)

    monkeypatch.setenv("HANOVA_HOME_NETWORKS", HOME_LAN)
    monkeypatch.setattr(home_net, "lan_signal", counting_lan)
    monkeypatch.setattr(home_net, "local_address", _local(ELSEWHERE_ADDRESS))
    assert await home_net.home_state() == home_net.AWAY
    assert await home_net.home_state() == home_net.AWAY
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_cache_expires_and_reprobes(monkeypatch):
    """A zero TTL means every call re-probes, proving the TTL is honoured."""
    calls = {"n": 0}

    async def counting_get(self, url, headers=None, **kw):
        calls["n"] += 1
        return _FakeResponse(200)

    monkeypatch.setenv("HANOVA_HOME_CACHE_TTL_S", "0")
    monkeypatch.setattr(httpx.AsyncClient, "get", counting_get)
    assert await home_net.home_state() == home_net.HOME
    assert await home_net.home_state() == home_net.HOME
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_concurrent_cold_probes_issue_one_request(monkeypatch):
    """Finding 12: a burst of tool calls on a cold cache is one probe, not five."""
    calls = {"n": 0}
    started = asyncio.Event()

    async def slow_get(self, url, headers=None, **kw):
        calls["n"] += 1
        started.set()
        await asyncio.sleep(0.05)
        return _FakeResponse(200)

    monkeypatch.setattr(httpx.AsyncClient, "get", slow_get)
    results = await asyncio.gather(*(home_net.home_state() for _ in range(5)))
    assert results == [home_net.HOME] * 5
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_probe_never_raises_whatever_the_socket_layer_does(monkeypatch):
    """A verdict is required on every path; an exception here breaks every tool."""

    async def exploding_lan(host, port, timeout_s):
        raise OSError("interface went away")

    monkeypatch.setattr(home_net, "lan_signal", exploding_lan)
    assert await home_net.home_state() == home_net.UNKNOWN


@pytest.mark.asyncio
async def test_the_probe_logs_no_address_and_no_url(monkeypatch, caplog):
    """Round 2, finding 6: home_net is a service seam and logs like one.

    The HA base URL is the house's LAN address and the source address is the
    robot's; neither belongs in a log line, at any level.
    """
    import logging

    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("HA_URL", "http://SENTINEL_PRIVATE_x7.invalid:8123")
    monkeypatch.setattr(home_net, "lan_signal", _lan(same_subnet=False, local="SENTINEL_PRIVATE_x7"))
    monkeypatch.setattr(home_net, "local_address", _local("SENTINEL_PRIVATE_x7"))
    assert await home_net.home_state() == home_net.UNKNOWN
    assert "SENTINEL_PRIVATE_x7" not in caplog.text
```

Create `reachy_companion/tests/test_hanova_confirm.py`:

```python
"""Contract tests for the two-step confirmation gate (D-018, R3).

Also pins review round 1 findings 3 (a confirmation must not survive a session
boundary) and 4 (authorisation is spent on success, not on attempt), and review
round 2 finding 2 (every armed action carries an immutable claim id, and every
mutator must present it together with the current epoch).
"""

import pytest

from reachy_companion.hanova.confirm import (
    GATE,
    ConfirmationGate,
    action_in_flight,
    confirmation_expired,
)


@pytest.fixture(autouse=True)
def clean_gate(monkeypatch):
    """Each test gets a fresh session epoch and the default 90 s TTL."""
    monkeypatch.delenv("HANOVA_CONFIRM_TTL_S", raising=False)
    GATE.reset()
    GATE.begin_session()
    yield
    GATE.reset()


def test_arm_returns_the_exact_needs_confirmation_contract():
    """R3 fixes this shape; the model reads `summary` back to the user."""
    out = GATE.arm("email_send", "send mail to a@example.com, subject: Dinner", {"to": "a@example.com"})
    assert out == {
        "status": "needs_confirmation",
        "summary": "send mail to a@example.com, subject: Dinner",
    }


def test_claim_returns_the_armed_payload_without_consuming_it():
    """Finding 4: the authorisation must survive until the operation succeeds."""
    GATE.arm("calendar_delete", "delete 'Dentist' on 2026-09-02", {"event_id": "abc"})
    pending = GATE.claim("calendar_delete")
    assert pending is not None
    assert pending.tool_name == "calendar_delete"
    assert pending.payload == {"event_id": "abc"}
    assert GATE.complete("calendar_delete", pending.claim_id) is True
    assert GATE.claim("calendar_delete") is None  # spent only after success


def test_release_lets_a_transient_failure_be_retried():
    """Finding 4: a 503 must not force the user through a second read-back."""
    GATE.arm("drive_trash", "move 'notes.txt' to Drive trash", {"file_id": "f1"})
    first = GATE.claim("drive_trash")
    assert first is not None
    assert GATE.release("drive_trash", first.claim_id) is True
    retry = GATE.claim("drive_trash")
    assert retry is not None and retry.payload == {"file_id": "f1"}
    # Round 2, finding 2: a retry re-uses the SAME action, so the id is stable.
    assert retry.claim_id == first.claim_id


def test_a_claim_in_flight_cannot_be_claimed_again():
    """Two concurrent confirm calls must not both execute the same delete."""
    GATE.arm("task_delete", "delete task 'buy milk'", {"task_id": "t1"})
    assert GATE.claim("task_delete") is not None
    assert GATE.claim("task_delete") is None


# --- claim identity (round 2, finding 2) -----------------------------------
def test_every_armed_action_gets_its_own_opaque_claim_id():
    """The id is what makes "this exact authorisation" expressible at all."""
    GATE.arm("drive_trash", "move 'a.txt' to Drive trash", {"file_id": "f1"})
    first = GATE.claim("drive_trash")
    assert first is not None and first.claim_id
    GATE.complete("drive_trash", first.claim_id)

    GATE.arm("drive_trash", "move 'b.txt' to Drive trash", {"file_id": "f2"})
    second = GATE.claim("drive_trash")
    assert second is not None
    assert second.claim_id != first.claim_id


def test_a_stale_claim_id_cannot_complete_a_newer_action():
    """Round 2, finding 2: the exact loss -- an old call spending a new approval.

    An operation still in flight when the conversation restarted used to be able
    to call `complete("drive_trash")` and silently destroy the authorisation the
    *new* session had just armed, without ever performing it.
    """
    GATE.arm("drive_trash", "move 'a.txt' to Drive trash", {"file_id": "f1"})
    stale = GATE.claim("drive_trash")
    assert stale is not None

    GATE.begin_session()
    GATE.arm("drive_trash", "move 'b.txt' to Drive trash", {"file_id": "f2"})

    assert GATE.complete("drive_trash", stale.claim_id) is False
    fresh = GATE.claim("drive_trash")
    assert fresh is not None and fresh.payload == {"file_id": "f2"}


def test_a_stale_claim_id_cannot_release_a_newer_action():
    """The same hole in the other direction: re-arming someone else's action."""
    GATE.arm("task_delete", "delete task 'a'", {"task_id": "t1"})
    stale = GATE.claim("task_delete")
    assert stale is not None
    GATE.begin_session()
    GATE.arm("task_delete", "delete task 'b'", {"task_id": "t2"})
    live = GATE.claim("task_delete")
    assert live is not None

    assert GATE.release("task_delete", stale.claim_id) is False
    # The live action is still in flight, exactly as it was.
    assert GATE.claim("task_delete") is None


def test_re_arming_a_claimed_slot_is_refused_with_its_own_status():
    """Round 2, finding 2: an executing destructive action is not re-armable."""
    GATE.arm("calendar_delete", "delete 'Dentist'", {"event_id": "abc"})
    in_flight = GATE.claim("calendar_delete")
    assert in_flight is not None

    refused = GATE.arm("calendar_delete", "delete 'Optician'", {"event_id": "xyz"})
    assert refused["status"] == "action_in_flight"
    assert refused["status"] != "needs_confirmation"

    # And the original claim is untouched: it still completes normally.
    assert GATE.complete("calendar_delete", in_flight.claim_id) is True


def test_a_bare_abort_cannot_yank_an_action_that_is_executing():
    """Round 2, finding 2: abort without an id may only drop an idle action."""
    GATE.arm("drive_trash", "move 'a.txt' to Drive trash", {"file_id": "f1"})
    pending = GATE.claim("drive_trash")
    assert pending is not None
    assert GATE.abort("drive_trash")["status"] == "action_in_flight"
    # With the id it is the claim-bound abort, and it works.
    assert GATE.abort("drive_trash", pending.claim_id) == {"status": "aborted"}
    assert GATE.claim("drive_trash") is None


def test_a_claim_bound_abort_rejects_the_wrong_id():
    """Same comparison as complete/release, in the same lock."""
    GATE.arm("drive_trash", "move 'a.txt' to Drive trash", {"file_id": "f1"})
    pending = GATE.claim("drive_trash")
    assert pending is not None
    assert GATE.abort("drive_trash", "not-the-id")["status"] == "action_in_flight"
    assert GATE.complete("drive_trash", pending.claim_id) is True


def test_action_in_flight_payload_is_self_describing():
    """The model has to be able to say something sensible about this."""
    out = action_in_flight()
    assert out["status"] == "action_in_flight"
    assert out["error"]


def test_abort_drops_the_action_and_says_so():
    """The user changing their mind is a first-class outcome, not a timeout."""
    GATE.arm("self_destruct", "arm the sequence", {})
    assert GATE.abort("self_destruct") == {"status": "aborted"}
    assert GATE.claim("self_destruct") is None


def test_claim_without_arm_is_none():
    """A confirm:true first call must not execute anything."""
    assert GATE.claim("drive_trash") is None


def test_pending_actions_do_not_cross_tools():
    """Arming one gated tool must never authorise a different one."""
    GATE.arm("task_delete", "delete task 'buy milk'", {"task_id": "t1"})
    assert GATE.claim("drive_trash") is None
    assert GATE.claim("task_delete") is not None


def test_expired_pending_is_dropped(monkeypatch):
    """The 90 s window is enforced in code, not in the prompt."""
    monkeypatch.setenv("HANOVA_CONFIRM_TTL_S", "1")
    GATE.arm("self_destruct", "arm the sequence", {})
    pending = GATE.claim("self_destruct")
    assert pending is not None
    GATE.release("self_destruct", pending.claim_id)
    # Move the deadline into the past rather than sleeping for the TTL.
    GATE.expire_now_for_tests("self_destruct")
    assert GATE.claim("self_destruct") is None


# --- session scoping (finding 3) ------------------------------------------
def test_a_new_session_invalidates_everything_armed_before_it():
    """A backend reconnect must not carry someone's pending delete across."""
    GATE.arm("calendar_delete", "delete 'Dentist'", {"event_id": "abc"})
    GATE.begin_session()
    assert GATE.claim("calendar_delete") is None


def test_shutdown_clears_the_gate():
    """A closing conversation leaves no authorisation behind for the next one."""
    GATE.arm("email_send", "send mail", {"to": "a@example.com"})
    GATE.end_session()
    assert GATE.claim("email_send") is None


def test_an_action_armed_under_an_older_epoch_is_never_claimable():
    """Even if the dict survived, the epoch stamp refuses it (defence in depth)."""
    GATE.arm("drive_trash", "move 'notes.txt' to Drive trash", {"file_id": "f1"})
    stale_epoch = GATE.epoch()
    GATE.begin_session()
    assert GATE.epoch() != stale_epoch
    GATE.arm("drive_trash", "move 'other.txt' to Drive trash", {"file_id": "f2"})
    pending = GATE.claim("drive_trash")
    assert pending is not None and pending.payload == {"file_id": "f2"}


def test_arming_twice_replaces_an_unclaimed_pending_action():
    """A corrected read-back must supersede the first one, not queue behind it.

    Round 2, finding 2 narrowed this: replacement is right for an action nobody
    has started, and refused for one that is mid-execution.
    """
    GATE.arm("task_complete", "complete 'buy milk'", {"task_id": "t1"})
    GATE.arm("task_complete", "complete 'buy bread'", {"task_id": "t2"})
    pending = GATE.claim("task_complete")
    assert pending is not None
    assert pending.payload == {"task_id": "t2"}


def test_clear_drops_a_pending_action():
    """An aborted ritual leaves nothing armed."""
    GATE.arm("self_destruct", "arm the sequence", {})
    GATE.clear("self_destruct")
    assert GATE.claim("self_destruct") is None


def test_payload_is_copied_not_aliased():
    """Mutating the caller's dict afterwards must not change what executes."""
    payload = {"file_id": "f1"}
    GATE.arm("drive_trash", "move 'notes.txt' to Drive trash", payload)
    payload["file_id"] = "f2"
    pending = GATE.claim("drive_trash")
    assert pending is not None
    assert pending.payload == {"file_id": "f1"}


def test_confirmation_expired_payload_is_self_describing():
    """The model needs enough to recover: re-describe and ask again."""
    out = confirmation_expired()
    assert out["status"] == "confirmation_expired"
    assert "confirm" in out["error"].lower()


def test_independent_gates_do_not_share_state():
    """The class is reusable; only GATE is wired into the app."""
    other = ConfirmationGate()
    other.begin_session()
    GATE.arm("email_send", "send mail", {})
    assert other.claim("email_send") is None


def test_gate_logs_no_summary_and_no_payload(caplog):
    """Finding 7: the summary is the user's own data; it is never logged."""
    import logging

    caplog.set_level(logging.DEBUG)
    GATE.arm("email_send", "send mail to SENTINEL_PRIVATE_x7 about SENTINEL_PRIVATE_x7", {"to": "SENTINEL_PRIVATE_x7"})
    pending = GATE.claim("email_send")
    assert pending is not None
    GATE.complete("email_send", pending.claim_id)
    assert "SENTINEL_PRIVATE_x7" not in caplog.text
```

- [ ] **Step 2: Run the tests to verify they fail**

```powershell
cd C:\Project\Reachy-mini\reachy_companion
..\.venv\Scripts\python.exe -m pytest tests/test_home_net.py tests/test_hanova_confirm.py -q
```

Expected: two collection errors — `ModuleNotFoundError: No module named 'reachy_companion.home_net'` and `... 'reachy_companion.hanova.confirm'`.

- [ ] **Step 3: Implement `home_net.py`**

Create `reachy_companion/src/reachy_companion/home_net.py`:

```python
"""Home-network awareness for house-bound tools (D-018, R4).

House-bound capabilities (TV casting, the NAS home-video library) only work when
the robot is on the same LAN as Home Assistant. Rather than let each one fail
with a socket error tens of seconds later, they ask `home_state()` first.

**The verdict is tri-state** (review round 1, finding 12), and **`AWAY` requires
positive evidence** (review round 2, finding 3):

* `AWAY` -- the robot's **own** address is outside every network the operator
  declared in `HANOVA_HOME_NETWORKS`. That is a fact about where this machine is
  attached, it does not depend on Home Assistant answering, and it is the *only*
  thing that justifies telling the user they are not at home. With no declared
  network this verdict is **unreachable**, by design.
* `UNKNOWN` -- everything else that is not a clean `HOME`: the TCP connect
  failed (no route, DNS failure, connection refused -- all of which are what an
  HA outage looks like), the answer was 401/403 or 5xx, the HTTP read timed out,
  the connection came from a plainly different subnet (a VPN or a remote proxy),
  or there is simply no declaration to judge locality with. The robot cannot
  tell where it is, and it says exactly that.
* `HOME` -- inside a declared home network (or, with none declared, on the same
  subnet as Home Assistant) **and** `/api/` answered 200.

**The `/24` subnet comparison is a demoted hint** (round 2, finding 3). It ran as
a hard rule in round 1, which misclassifies any home LAN wider than a `/24`. It
may now only *withhold* `HOME`; it can never produce `AWAY`, and a declared
`HANOVA_HOME_NETWORKS` bypasses it entirely. A `/16` home LAN therefore degrades
to `UNKNOWN` -- honest, and fixable with one config key -- instead of to a lie.

The probe is a TCP connect plus one `GET {HA_URL}/api/` with the long-lived
token, both capped at `HANOVA_HOME_PROBE_TIMEOUT_S` (1.5 s) and cached -- all
three verdicts -- for `HANOVA_HOME_CACHE_TTL_S` (30 s). A single-flight
`asyncio.Lock` collapses a burst of cold tool calls into one probe; a
`threading.Lock` guards the cache itself because the settings web server runs on
its own thread.

Cloud tools (calendar, tasks, Notion, Drive, email) and music never call this:
they work from anywhere, and a needless probe would be pure added latency.

There is deliberately **no boolean `is_home` shortcut** (round 2, finding 3): a
boolean cannot carry three verdicts, and every call site that used one turned
`UNKNOWN` into "yes, go ahead".
"""

from __future__ import annotations

import time
import socket
import asyncio
import logging
import threading
import ipaddress
import urllib.parse
from typing import Any, Dict, List
from dataclasses import dataclass

import httpx

from reachy_companion.hanova import redact, settings


logger = logging.getLogger(__name__)

HOME = "home"
AWAY = "away"
UNKNOWN = "unknown"

_LOCK = threading.Lock()
_PROBE_LOCK = asyncio.Lock()
_CACHED_VERDICT: str | None = None
_CACHED_AT: float = 0.0


@dataclass(frozen=True)
class LanProbe:
    """What one TCP connect to Home Assistant told us about where we are."""

    reachable: bool
    local_address: str
    same_subnet: bool


def reset_cache() -> None:
    """Drop the cached verdict so the next probe runs. Used by tests."""
    global _CACHED_VERDICT, _CACHED_AT
    with _LOCK:
        _CACHED_VERDICT = None
        _CACHED_AT = 0.0


def _read_cache(ttl_s: float) -> str | None:
    with _LOCK:
        if _CACHED_VERDICT is None:
            return None
        if (time.monotonic() - _CACHED_AT) > ttl_s:
            return None
        return _CACHED_VERDICT


def _write_cache(verdict: str) -> None:
    global _CACHED_VERDICT, _CACHED_AT
    with _LOCK:
        _CACHED_VERDICT = verdict
        _CACHED_AT = time.monotonic()


def _same_subnet(local: str, peer: str) -> bool:
    """Return whether two addresses *look* like they share one local network.

    Round 2, finding 3: this is a **hint**, not a rule. A /24 for IPv4 and a /64
    for IPv6 is true for a typical home LAN and false for a VPN tunnel address,
    but it is also false for a perfectly ordinary /22 or /16 home network. Its
    only permitted effect is to withhold `HOME`; it can never produce `AWAY`, and
    a configured `HANOVA_HOME_NETWORKS` bypasses it entirely.
    """
    try:
        left = ipaddress.ip_address(local)
        right = ipaddress.ip_address(peer)
    except ValueError:
        return False
    if left.version != right.version:
        return False
    prefix = 24 if left.version == 4 else 64
    network = ipaddress.ip_network(f"{right}/{prefix}", strict=False)
    return left in network


def _inside_declared_home(address: str, networks: List[Any]) -> bool | None:
    """True/False when the declaration can decide, None when it cannot."""
    if not networks or not address:
        return None
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return None
    for network in networks:
        if parsed.version == network.version and parsed in network:
            return True
    return False


async def local_address(host: str, timeout_s: float) -> str:
    """Return this machine's source address on the route towards *host*.

    Round 2, finding 3: `AWAY` has to be decidable even when Home Assistant is
    down, so locality cannot be a by-product of a successful connect. A UDP
    socket that is `connect()`ed sends **no packets** -- it only asks the kernel
    which interface and address would be used -- so this works with HA offline
    and costs nothing. Returns "" when there is no route at all, which is itself
    not evidence of anything and yields `UNKNOWN`.

    A seam tests monkeypatch. Never raises.
    """

    def _probe() -> str:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.settimeout(timeout_s)
            sock.connect((host, 9))  # discard port; nothing is transmitted
            return str(sock.getsockname()[0])
        finally:
            sock.close()

    try:
        return await asyncio.wait_for(asyncio.to_thread(_probe), timeout=timeout_s)
    except (OSError, asyncio.TimeoutError, socket.gaierror):
        return ""


async def lan_signal(host: str, port: int, timeout_s: float) -> LanProbe:
    """Open one TCP connection and report reachability plus locality.

    The single seam tests monkeypatch. Never raises.
    """
    writer = None
    try:
        _reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout_s)
        local = writer.get_extra_info("sockname")
        peer = writer.get_extra_info("peername")
        if not local or not peer:
            # Connected, but we cannot tell which network we are on.
            return LanProbe(reachable=True, local_address="", same_subnet=False)
        return LanProbe(
            reachable=True,
            local_address=str(local[0]),
            same_subnet=_same_subnet(str(local[0]), str(peer[0])),
        )
    except (OSError, asyncio.TimeoutError, socket.gaierror):
        # Round 2, finding 3: a failed connect is an HA fact, not a location
        # fact. It never produces AWAY on its own.
        return LanProbe(reachable=False, local_address="", same_subnet=False)
    finally:
        if writer is not None:
            writer.close()


def _host_and_port(base_url: str) -> tuple[str, int]:
    parsed = urllib.parse.urlsplit(base_url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return parsed.hostname or "", int(port)


async def _probe() -> str:
    base_url = settings.ha_url()
    token = settings.ha_token()
    if not base_url or not token:
        # Unconfigured is not absence. The tools that need HA are already
        # `unavailable` by their prerequisites (settings.tool_status).
        return UNKNOWN

    timeout_s = settings.home_probe_timeout_s()
    host, port = _host_and_port(base_url)
    if not host:
        return UNKNOWN

    networks = settings.home_networks()

    try:
        signal = await lan_signal(host, port, timeout_s)
    except Exception:  # noqa: BLE001 - a verdict is required on every path
        logger.info("hanova home probe: LAN signal failed; verdict unknown.")
        signal = LanProbe(reachable=False, local_address="", same_subnet=False)

    # --- step 1: positive off-home evidence, decided before anything else ---
    # This runs whether or not Home Assistant answered, which is the whole point
    # (round 2, finding 3): being on a foreign network is a fact about us.
    own_address = signal.local_address
    if not own_address:
        try:
            own_address = await local_address(host, timeout_s)
        except Exception:  # noqa: BLE001 - a verdict is required on every path
            own_address = ""

    verdict_from_declaration = _inside_declared_home(own_address, networks)
    if verdict_from_declaration is False:
        logger.info("hanova home probe: this machine is outside every declared home network; verdict away.")
        return AWAY

    # --- step 2: everything else needs Home Assistant to actually answer ----
    if not signal.reachable:
        # No route, DNS failure, refused port, connect timeout. All of these are
        # what a Home Assistant outage looks like from here, and none of them
        # says where we are (round 2, finding 3).
        logger.info("hanova home probe: Home Assistant did not accept a connection; verdict unknown.")
        return UNKNOWN

    if verdict_from_declaration is None and not signal.same_subnet:
        # No declaration to judge with, and the demoted hint says "probably not
        # local". Withhold HOME; never assert AWAY.
        logger.info("hanova home probe: no declared home network and an off-subnet route; verdict unknown.")
        return UNKNOWN

    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            response = await client.get(
                f"{base_url}/api/",
                headers={"Authorization": f"Bearer {token}"},
            )
    except httpx.HTTPError as exc:
        # Finding 6: an httpx error string embeds the full URL.
        logger.info(
            "hanova home probe: HTTP layer failed on a reachable host (%s); verdict unknown.",
            redact.error(exc),
        )
        return UNKNOWN

    if response.status_code == 200:
        return HOME
    logger.info("hanova home probe: Home Assistant answered %d; verdict unknown.", response.status_code)
    return UNKNOWN


async def home_state() -> str:
    """Return HOME, AWAY or UNKNOWN for where the robot is right now."""
    ttl_s = settings.home_cache_ttl_s()
    cached = _read_cache(ttl_s)
    if cached is not None:
        return cached
    async with _PROBE_LOCK:
        cached = _read_cache(ttl_s)
        if cached is not None:
            return cached
        verdict = await _probe()
        _write_cache(verdict)
        return verdict


def away_from_home() -> Dict[str, Any]:
    """The exact payload a house-bound tool returns off the home network (R4)."""
    return {"status": "away_from_home"}


def home_unknown() -> Dict[str, Any]:
    """"I cannot tell where I am." Neither presence nor absence.

    Round 2, finding 3: this is a distinct status, `home_status_unknown`, so the
    persona can never confuse it with `away_from_home`. It takes **no argument**
    (finding 6): a caller-supplied detail is exactly how a Home Assistant error
    body -- which quotes the house's LAN address back -- would reach the model.
    """
    return {
        "status": "home_status_unknown",
        "error": (
            "Cannot tell whether the robot is on the home network right now. "
            "Say you are not sure you are at home and that the home system is "
            "not answering; do not tell the user they are out of the house."
        ),
    }
```

- [ ] **Step 4: Implement `hanova/confirm.py`**

Create `reachy_companion/src/reachy_companion/hanova/confirm.py`:

```python
"""Two-step confirmation gate for destructive tools (D-018, R3).

A gated tool called without `confirm: true` does *no work*: it computes the
exact human-readable action, parks the resolved payload here, and returns
`{"status": "needs_confirmation", "summary": ...}`. Only a second call with
`confirm: true` executes -- and it executes the *parked* payload, not whatever
arguments the second call carried, so a mis-heard correction between the two
turns cannot silently retarget a delete.

The window is `HANOVA_CONFIRM_TTL_S` (90 s, matching upstream `self_destruct`).
This is enforced here, in code. A prompt instruction is not a gate.

**Two things review round 1 changed.**

*Finding 3 -- the gate is session-scoped.* A `ConfirmationGate` keyed only by
tool name and living for the life of the process would let a confirmation armed
in one conversation be consumed by the next one after a backend reconnect. Every
pending action is stamped with the current **epoch**; `begin_session()` mints a
new epoch (wired into the realtime session start in Task 5) and `end_session()`
drops everything (wired into shutdown). An action from an older epoch is never
claimable, even if it is somehow still in the dict.

*Finding 4 -- authorisation is spent on success, not on attempt.* The old
`take()` removed the pending action *before* the destructive call ran, so a 503
from Google turned a confirmed delete into "please confirm again" and lost the
user's authorisation to a transient fault. Now: `claim()` marks it in flight and
hands it back, `complete()` spends it, `release()` puts it back for a retry, and
`abort()` throws it away because the user said no.

**Two things review round 2 changed.**

*Finding 2 -- every armed action carries an immutable claim id.* An epoch scopes
a *session*; it does not identify an *action*. `complete("drive_trash")` took
only a tool name, so whoever called it spent whatever happened to be in that
slot — including an action a newer session had just armed, which dropped the
user's authorisation without performing anything. And `arm()` overwrote a slot
whose action was mid-execution, which made a destructive operation claimable
again while it was still running. Now `arm()` mints an opaque `claim_id`,
`claim()` hands it to the caller, and `complete()` / `release()` / the
claim-bound `abort()` all require it. Every one of them compares **epoch and
claim id inside the same `with self._lock:` block that performs the mutation**,
so there is no window between checking and acting. Re-arming a claimed slot is
refused with `action_in_flight()`.

*Finding 9 -- `release()` is for transient failures only.* Re-arming an action
after an authentication failure, a refused recipient or a validation error keeps
an approval alive for something that can never succeed as approved. Terminal
failures call `complete()` instead: the authorisation is spent, and the model
has to read a corrected action back. The classification itself lives with each
error family (`gauth.is_transient`, `gdrive.is_transient`,
`gmail_smtp.is_transient`), not here — this module only enforces that whichever
one is chosen presents the right claim id.

Nothing here logs `summary` or `payload`: both are the user's own words about
their own data (finding 7). The `claim_id` is a random token, not derived from
either, so it is safe to log.
"""

from __future__ import annotations

import time
import uuid
import logging
import threading
from typing import Any, Dict
from dataclasses import replace, dataclass

from reachy_companion.hanova import settings


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PendingAction:
    """One resolved, read-back-to-the-user action awaiting confirmation."""

    tool_name: str
    summary: str
    payload: Dict[str, Any]
    expires_at: float
    epoch: str
    # Round 2, finding 2: minted at arm time, immutable for the life of the
    # action, and required by every mutator. It identifies *this* authorisation,
    # which the epoch alone cannot do.
    claim_id: str
    claimed_at: float | None = None


class ConfirmationGate:
    """Holds at most one pending action per tool name, per session, with a TTL."""

    def __init__(self) -> None:
        """Create an empty gate with no session yet."""
        self._lock = threading.Lock()
        self._pending: Dict[str, PendingAction] = {}
        self._epoch = ""

    # --- session lifecycle (finding 3) ------------------------------------
    def begin_session(self) -> str:
        """Start a new confirmation epoch, invalidating everything armed before."""
        with self._lock:
            self._pending.clear()
            self._epoch = uuid.uuid4().hex
            epoch = self._epoch
        logger.info("Confirmation gate: new session epoch")
        return epoch

    def end_session(self) -> None:
        """Drop every pending action; the conversation is over."""
        with self._lock:
            self._pending.clear()
            self._epoch = ""
        logger.info("Confirmation gate: session ended, nothing left armed")

    def epoch(self) -> str:
        """Return the current session epoch id."""
        with self._lock:
            return self._epoch

    # --- the two-step contract --------------------------------------------
    def _live(self, tool_name: str, claim_id: str) -> PendingAction | None:
        """Return the pending action iff it matches epoch **and** claim id.

        Round 2, finding 2: callers must invoke this **inside** their own
        `with self._lock:` block, immediately before mutating, so the comparison
        and the mutation cannot be separated by another thread.
        """
        pending = self._pending.get(tool_name)
        if pending is None:
            return None
        if not self._epoch or pending.epoch != self._epoch:
            return None
        if pending.claim_id != claim_id:
            return None
        return pending

    def arm(self, tool_name: str, summary: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Park a resolved action and return the needs-confirmation contract.

        Round 2, finding 2: refuses to overwrite an action that is already in
        flight. Replacing a claimed slot would make a destructive operation that
        is *currently executing* claimable a second time.
        """
        ttl_s = settings.confirm_ttl_s()
        with self._lock:
            if not self._epoch:
                # Arming outside a session would be immediately unclaimable;
                # open one rather than silently parking a dead action.
                self._epoch = uuid.uuid4().hex
            existing = self._pending.get(tool_name)
            if (
                existing is not None
                and existing.claimed_at is not None
                and existing.epoch == self._epoch
                and existing.expires_at > time.monotonic()
            ):
                logger.info("Confirmation for %s is in flight; refused to re-arm", tool_name)
                return action_in_flight()
            self._pending[tool_name] = PendingAction(
                tool_name=tool_name,
                summary=summary,
                payload=dict(payload),
                expires_at=time.monotonic() + ttl_s,
                epoch=self._epoch,
                claim_id=uuid.uuid4().hex,
            )
        logger.info("Confirmation armed for %s (ttl %.0fs)", tool_name, ttl_s)
        return {"status": "needs_confirmation", "summary": summary}

    def claim(self, tool_name: str) -> PendingAction | None:
        """Take the pending action in flight without spending it (finding 4).

        Returns None when there is nothing armed, when it has expired, when it
        belongs to an earlier session epoch, or when another call already has it
        in flight. The returned object carries the `claim_id` that every
        subsequent `complete` / `release` / `abort` must present (round 2,
        finding 2).
        """
        now = time.monotonic()
        with self._lock:
            pending = self._pending.get(tool_name)
            if pending is None:
                return None
            if pending.epoch != self._epoch or not self._epoch:
                logger.info("Confirmation for %s belonged to an earlier session; refused", tool_name)
                self._pending.pop(tool_name, None)
                return None
            if pending.expires_at <= now:
                logger.info("Confirmation for %s expired before it was used", tool_name)
                self._pending.pop(tool_name, None)
                return None
            if pending.claimed_at is not None:
                logger.info("Confirmation for %s is already in flight; refused", tool_name)
                return None
            in_flight = replace(pending, claimed_at=now)
            self._pending[tool_name] = in_flight
            return in_flight

    def complete(self, tool_name: str, claim_id: str) -> bool:
        """Spend *this* authorisation. Returns whether it was still the live one.

        Called on success, and on a **terminal** failure (round 2, finding 9):
        an authentication error, a refused recipient or a validation error means
        the resolved action itself is wrong, so keeping the approval alive would
        keep an approval for something unachievable.
        """
        with self._lock:
            if self._live(tool_name, claim_id) is None:
                logger.info("Confirmation for %s: stale claim refused at complete", tool_name)
                return False
            self._pending.pop(tool_name, None)
        logger.info("Confirmation for %s completed", tool_name)
        return True

    def release(self, tool_name: str, claim_id: str) -> bool:
        """A **transient** failure; re-arm so the user can just say "try again".

        Returns whether this claim was still the live one. Round 2, finding 9:
        only transient faults may take this path.
        """
        with self._lock:
            pending = self._live(tool_name, claim_id)
            if pending is None:
                logger.info("Confirmation for %s: stale claim refused at release", tool_name)
                return False
            self._pending[tool_name] = replace(pending, claimed_at=None)
        logger.info("Confirmation for %s released for retry", tool_name)
        return True

    def abort(self, tool_name: str, claim_id: str | None = None) -> Dict[str, Any]:
        """The user said no. Drop it and say so.

        With a *claim_id* this is the claim-bound abort and the id must match.
        Without one it may only drop an action that is **not** in flight: a bare
        abort must never yank an operation that is already executing (round 2,
        finding 2).
        """
        with self._lock:
            pending = self._pending.get(tool_name)
            if pending is None:
                logger.info("Confirmation for %s aborted (nothing was armed)", tool_name)
                return {"status": "aborted"}
            if claim_id is None:
                if pending.claimed_at is not None and pending.epoch == self._epoch:
                    logger.info("Confirmation for %s is in flight; bare abort refused", tool_name)
                    return action_in_flight()
            elif self._live(tool_name, claim_id) is None:
                logger.info("Confirmation for %s: stale claim refused at abort", tool_name)
                return action_in_flight()
            self._pending.pop(tool_name, None)
        logger.info("Confirmation for %s aborted", tool_name)
        return {"status": "aborted"}

    def clear(self, tool_name: str) -> None:
        """Drop any pending action for *tool_name*, in flight or not.

        Unlike `abort`, this is a lifecycle operation (session start/shutdown),
        not a user decision, so it is unconditional by design.
        """
        with self._lock:
            self._pending.pop(tool_name, None)

    def reset(self) -> None:
        """Drop every pending action and the epoch. Used by tests."""
        with self._lock:
            self._pending.clear()
            self._epoch = ""

    def expire_now_for_tests(self, tool_name: str) -> None:
        """Move a pending deadline into the past so a test need not sleep."""
        with self._lock:
            pending = self._pending.get(tool_name)
            if pending is not None:
                self._pending[tool_name] = replace(pending, expires_at=time.monotonic() - 1.0)


GATE = ConfirmationGate()


def confirmation_expired() -> Dict[str, Any]:
    """Payload for a `confirm: true` call with nothing armed to confirm."""
    return {
        "status": "confirmation_expired",
        "error": (
            "Nothing is pending confirmation. Describe the action again and ask "
            "the user to confirm before calling with confirm true."
        ),
    }


def action_in_flight() -> Dict[str, Any]:
    """Payload for trying to re-arm or bare-abort an executing action.

    Round 2, finding 2. This is a distinct status on purpose: the model must not
    read it as "expired" and start a fresh read-back while the first operation is
    still running, and it must not read it as success either.
    """
    return {
        "status": "action_in_flight",
        "error": (
            "That action is already running. Wait for it to finish and report "
            "its result before arming or cancelling anything for this tool."
        ),
    }
```

- [ ] **Step 5: Run the tests to verify they pass**

```powershell
cd C:\Project\Reachy-mini\reachy_companion
..\.venv\Scripts\python.exe -m pytest tests/test_home_net.py tests/test_hanova_confirm.py -q
```

Expected: green — **23** test functions from `test_home_net.py` and **24**
from `test_hanova_confirm.py`, neither parametrised: **47 collected cases**.
Record the exact number.

- [ ] **Step 6: Run the full gate**

```powershell
cd C:\Project\Reachy-mini\reachy_companion
..\.venv\Scripts\python.exe -m pytest -q
..\.venv\Scripts\python.exe -m ruff check src tests
..\.venv\Scripts\python.exe -m ruff format --check src tests
..\.venv\Scripts\python.exe -m mypy
```

Expected: pytest green (30 documented skips), ruff clean, mypy `Success: no issues found`.

- [ ] **Step 7: Commit**

```bash
cd /c/Project/Reachy-mini
git rev-parse --abbrev-ref HEAD   # must print feat/ha-nova-port (Task 0)
git add reachy_companion/src/reachy_companion/home_net.py \
        reachy_companion/src/reachy_companion/hanova/confirm.py \
        reachy_companion/tests/test_home_net.py \
        reachy_companion/tests/test_hanova_confirm.py
git commit -m "feat(hanova): home-network probe and in-code confirmation gate"
```

---

### Task 3: Home Assistant REST client and the LAN media cache

Implements R6. The Chromecast dereferences URLs from its own network position, so anything we download has to be served from the robot's LAN address. The app already runs a FastAPI settings server bound to `0.0.0.0:7860` (`main.py:358`, `main.py:465`), and already mounts `StaticFiles` on it (`console.py:529`) — so we mount a second static route rather than starting a second server.

**Files:**
- Create: `reachy_companion/src/reachy_companion/hanova/ha_client.py`
- Create: `reachy_companion/src/reachy_companion/hanova/media_store.py`
- Modify: `reachy_companion/src/reachy_companion/console.py` (inside `_init_settings_ui_if_needed`, after the existing `/static` mount)
- Test: `reachy_companion/tests/test_hanova_ha_client.py`
- Test: `reachy_companion/tests/test_hanova_media_store.py`

**Interfaces:**
- Consumes: `reachy_companion.hanova.settings.{ha_url, ha_token, media_dir_override, media_http_base}` (Task 1).
- Produces:
  - `hanova.ha_client.ha_call_service(domain: str, service: str, data: Dict[str, Any], timeout_s: float = 30.0) -> Awaitable[Dict[str, Any]]`
  - `hanova.ha_client.ha_run_script(script_name: str, data: Dict[str, Any], timeout_s: float = 60.0) -> Awaitable[Dict[str, Any]]`
  - `hanova.ha_client.ha_get_state(entity_id: str, timeout_s: float = 15.0) -> Awaitable[Dict[str, Any]]`
  - All three return `{"ok": True, "result": <parsed json or None>}` or `{"ok": False, "error": str}` and never raise.
  - `hanova.media_store.MEDIA_URL_PREFIX: str` (`"/hanova-media"`), `hanova.media_store.KINDS: tuple[str, ...]` (`("music", "nas", "images", "sfx")`)
  - `hanova.media_store.media_root(instance_path: str | Path | None) -> Path`
  - `hanova.media_store.media_dir(kind: str, instance_path: str | Path | None) -> Path`
  - `hanova.media_store.prune(kind: str, instance_path: str | Path | None, keep: int) -> int`
  - `hanova.media_store.media_url(kind: str, filename: str) -> str | None`
  - `hanova.media_store.mount_media_routes(app: Any, instance_path: str | Path | None) -> bool` — **and it records the outcome** via `settings.set_media_mount_ready(...)`, so a mount that failed makes every URL-casting tool report `unavailable` instead of handing a Chromecast a URL nothing will serve (review finding 11)

- [ ] **Step 1: Write the failing tests**

Create `reachy_companion/tests/test_hanova_ha_client.py`:

```python
"""Contract tests for the async Home Assistant REST helper (D-018)."""

import httpx
import pytest

from reachy_companion.hanova import ha_client


class _FakeResponse:
    """Minimal stand-in for the httpx response Home Assistant returns."""

    def __init__(self, status_code: int = 200, payload: object = None) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> object:
        if self._payload is None:
            raise ValueError("no json body")
        return self._payload


@pytest.fixture(autouse=True)
def ha_env(monkeypatch):
    """Provide the HA config the client reads at call time."""
    monkeypatch.setenv("HA_URL", "http://ha.example.invalid:8123")
    monkeypatch.setenv("HA_TOKEN", "tok")


@pytest.mark.asyncio
async def test_call_service_posts_to_the_service_url(monkeypatch):
    """A service call is POST /api/services/<domain>/<service> with a bearer."""
    seen = {}

    async def fake_request(self, method, url, json=None, headers=None, **kw):
        seen.update(method=method, url=url, json=json, headers=headers)
        return _FakeResponse(200, [])

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)
    out = await ha_client.ha_call_service("media_player", "media_stop", {"entity_id": "media_player.tv"})
    assert out["ok"] is True
    assert seen["method"] == "POST"
    assert seen["url"] == "http://ha.example.invalid:8123/api/services/media_player/media_stop"
    assert seen["json"] == {"entity_id": "media_player.tv"}
    assert seen["headers"]["Authorization"] == "Bearer tok"


@pytest.mark.asyncio
async def test_run_script_targets_the_script_domain(monkeypatch):
    """Casting goes through HA scripts, so the path must be the script domain."""
    seen = {}

    async def fake_request(self, method, url, json=None, headers=None, **kw):
        seen["url"] = url
        seen["json"] = json
        return _FakeResponse(200, [])

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)
    out = await ha_client.ha_run_script("tv_show_youtube", {"youtube_id": "abc"})
    assert out["ok"] is True
    assert seen["url"] == "http://ha.example.invalid:8123/api/services/script/tv_show_youtube"
    assert seen["json"] == {"youtube_id": "abc"}


@pytest.mark.asyncio
async def test_get_state_reads_the_states_endpoint(monkeypatch):
    """State reads are GET /api/states/<entity_id>."""
    seen = {}

    async def fake_request(self, method, url, json=None, headers=None, **kw):
        seen.update(method=method, url=url, json=json)
        return _FakeResponse(200, {"state": "playing"})

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)
    out = await ha_client.ha_get_state("media_player.tv")
    assert out == {"ok": True, "result": {"state": "playing"}}
    assert seen["method"] == "GET"
    assert seen["url"] == "http://ha.example.invalid:8123/api/states/media_player.tv"
    assert seen["json"] is None


@pytest.mark.asyncio
async def test_non_2xx_is_a_result_not_an_exception(monkeypatch):
    """HA errors must reach the model as tool output, never as a raise."""

    async def fake_request(self, method, url, json=None, headers=None, **kw):
        return _FakeResponse(500, None)

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)
    out = await ha_client.ha_call_service("script", "turn_on", {})
    assert out["ok"] is False
    assert out["status_code"] == 500


@pytest.mark.asyncio
async def test_transport_error_is_a_result_not_an_exception(monkeypatch):
    """Off the home LAN this is the normal failure; it must be reported.

    Finding 7: reported as a *shape*. An httpx error string embeds the full URL,
    so the house's LAN address would otherwise travel into the tool result and
    into the model's transcript.
    """

    async def fake_request(self, method, url, json=None, headers=None, **kw):
        raise httpx.ConnectError("no route to host 10.11.12.13")

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)
    out = await ha_client.ha_call_service("script", "turn_on", {})
    assert out["ok"] is False
    assert "ConnectError" in out["error"]
    assert "10.11.12.13" not in out["error"]


@pytest.mark.asyncio
async def test_missing_config_is_reported_without_a_request(monkeypatch):
    """No HA_URL means no socket work at all."""

    async def fail_request(self, *args, **kwargs):
        raise AssertionError("ha_client must not call HA when it is unconfigured")

    monkeypatch.delenv("HA_URL")
    monkeypatch.setattr(httpx.AsyncClient, "request", fail_request)
    out = await ha_client.ha_call_service("script", "turn_on", {})
    assert out["ok"] is False
    assert "HA_URL" in out["error"]


@pytest.mark.asyncio
async def test_empty_body_is_still_ok(monkeypatch):
    """HA answers some service calls with no JSON body; that is success."""

    async def fake_request(self, method, url, json=None, headers=None, **kw):
        return _FakeResponse(200, None)

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)
    assert await ha_client.ha_call_service("script", "turn_on", {}) == {"ok": True, "result": None}


@pytest.mark.asyncio
async def test_the_ha_client_logs_no_script_name_url_or_error_body(monkeypatch, caplog):
    """Round 3, finding 3: the HA seam needs a caplog sentinel like every other.

    `test_each_service_seam_has_a_caplog_sentinel_test` in Task 14 requires one
    behavioural test per service seam, and `ha_client` was the seam without one --
    which matters more here than anywhere else, because the script name IS the
    operator's `scripts.yaml` entry and the URL IS the house's LAN address. Both
    failure branches are exercised: the transport error and the non-2xx.
    """
    import logging

    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("HA_URL", "http://SENTINEL_PRIVATE_x7.invalid:8123")

    async def transport_error(self, method, url, json=None, headers=None, **kw):
        raise httpx.ConnectError(f"no route to host SENTINEL_PRIVATE_x7 via {url}")

    monkeypatch.setattr(httpx.AsyncClient, "request", transport_error)
    failed = await ha_client.ha_run_script("SENTINEL_PRIVATE_x7", {"note": "SENTINEL_PRIVATE_x7"})
    assert failed["ok"] is False
    assert "SENTINEL_PRIVATE_x7" not in failed["error"]

    async def server_error(self, method, url, json=None, headers=None, **kw):
        return _FakeResponse(500, None)

    monkeypatch.setattr(httpx.AsyncClient, "request", server_error)
    refused = await ha_client.ha_run_script("SENTINEL_PRIVATE_x7", {})
    assert refused["ok"] is False
    assert "SENTINEL_PRIVATE_x7" not in refused["error"]

    assert "SENTINEL_PRIVATE_x7" not in caplog.text
```

Create `reachy_companion/tests/test_hanova_media_store.py`:

```python
"""Contract tests for the LAN media cache and its static route (D-018, R6)."""

import os
import time
import types

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from reachy_companion.hanova import settings, media_store


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Start from no media configuration and a mount that has not come up."""
    monkeypatch.delenv("HANOVA_MEDIA_DIR", raising=False)
    monkeypatch.delenv("HANOVA_MEDIA_HTTP_BASE", raising=False)
    settings.set_media_mount_ready(False)
    yield
    settings.set_media_mount_ready(False)


def test_media_root_defaults_under_the_instance_directory(tmp_path):
    """Cached media lives with .env / memory / faces, so the deploy ritual sees it."""
    assert media_store.media_root(tmp_path) == tmp_path / "hanova_media"


def test_media_dir_override_wins(monkeypatch, tmp_path):
    """A full disk on the robot can be worked around without code changes."""
    monkeypatch.setenv("HANOVA_MEDIA_DIR", str(tmp_path / "elsewhere"))
    assert media_store.media_root(tmp_path) == tmp_path / "elsewhere"


def test_media_root_without_an_instance_path_uses_a_temp_dir():
    """Tests and `--ui`-less runs have no instance path; that must not crash."""
    root = media_store.media_root(None)
    assert root.name == "reachy_companion_hanova_media"


def test_media_dir_creates_the_kind_subdirectory(tmp_path):
    """Callers get a directory that exists, every time."""
    music = media_store.media_dir("music", tmp_path)
    assert music == tmp_path / "hanova_media" / "music"
    assert music.is_dir()


def test_media_dir_rejects_an_unknown_kind(tmp_path):
    """Only the four known kinds are servable; a typo is a programming error."""
    with pytest.raises(ValueError):
        media_store.media_dir("secrets", tmp_path)


def test_prune_keeps_the_newest_files(tmp_path):
    """Keep-N cleanup is what stops the CM4 filling up with home videos."""
    nas = media_store.media_dir("nas", tmp_path)
    for index in range(5):
        path = nas / f"clip{index}.mp4"
        path.write_bytes(b"x")
        # Distinct mtimes so "newest" is unambiguous on a coarse-resolution FS.
        os.utime(path, (time.time() + index, time.time() + index))
    removed = media_store.prune("nas", tmp_path, keep=2)
    assert removed == 3
    assert sorted(p.name for p in nas.iterdir()) == ["clip3.mp4", "clip4.mp4"]


def test_prune_on_an_empty_cache_is_zero(tmp_path):
    """First run has nothing to prune and must not raise."""
    assert media_store.prune("music", tmp_path, keep=12) == 0


def test_media_url_is_none_without_a_lan_base():
    """Without HANOVA_MEDIA_HTTP_BASE there is no URL a TV could fetch."""
    assert media_store.media_url("nas", "clip.mp4") is None


def test_media_url_composes_base_prefix_kind_and_name(monkeypatch):
    """The URL shape is what the HA cast script receives."""
    monkeypatch.setenv("HANOVA_MEDIA_HTTP_BASE", "http://robot.example.invalid:7860/")
    assert media_store.media_url("nas", "clip.mp4") == "http://robot.example.invalid:7860/hanova-media/nas/clip.mp4"


def test_there_is_no_truncating_filename_helper():
    """Review finding 15: `safe_filename` flattened *and truncated* a path.

    Two clips whose paths agree for the first 150 characters mapped to the same
    served name, so one home video silently played in place of another. The
    helper is removed rather than fixed: `nas.cast_filename` derives its name
    from a hash of the validated path, and nothing else needs to flatten one.
    Keeping a collision-prone helper around is an invitation to reintroduce it.
    """
    assert not hasattr(media_store, "safe_filename")


def test_mount_media_routes_serves_a_cached_file(tmp_path):
    """R6 end to end: a file in the cache is fetchable over the app's own server."""
    images = media_store.media_dir("images", tmp_path)
    (images / "poster.png").write_bytes(b"PNGDATA")

    app = FastAPI()
    assert media_store.mount_media_routes(app, tmp_path) is True

    with TestClient(app) as client:
        response = client.get("/hanova-media/images/poster.png")
    assert response.status_code == 200
    assert response.content == b"PNGDATA"


def test_mount_media_routes_reports_failure_instead_of_raising(tmp_path):
    """A settings app that cannot mount must not brick startup."""

    class _NoMount:
        pass

    assert media_store.mount_media_routes(_NoMount(), tmp_path) is False


# --- mount readiness feeds tool availability (review finding 11) ----------
def test_mount_readiness_is_recorded_for_the_availability_table(tmp_path):
    """A successful mount is what makes URL casting a legal thing to offer."""
    settings.set_media_mount_ready(False)
    try:
        assert media_store.mount_media_routes(FastAPI(), tmp_path) is True
        assert settings.media_mount_ready() is True
    finally:
        settings.set_media_mount_ready(False)


def test_a_failed_mount_leaves_casting_unavailable(tmp_path):
    """Finding 11: the boolean must not be discarded; casting depends on it."""

    class _NoMount:
        pass

    settings.set_media_mount_ready(True)
    try:
        assert media_store.mount_media_routes(_NoMount(), tmp_path) is False
        assert settings.media_mount_ready() is False
    finally:
        settings.set_media_mount_ready(False)


def test_url_casting_tools_are_unavailable_when_the_mount_failed(monkeypatch, tmp_path):
    """The end of the chain: no live route means show_on_tv reports not_configured."""
    monkeypatch.setenv("HA_URL", "http://ha.example.invalid:8123")
    monkeypatch.setenv("HA_TOKEN", "tok")
    monkeypatch.setenv("HANOVA_HA_SCRIPT_IMAGE_URL", "tv_show_image_url")
    monkeypatch.setenv("HANOVA_MEDIA_HTTP_BASE", "http://robot.example.invalid:7860")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    settings.set_media_mount_ready(False)
    try:
        assert settings.tool_status("show_on_tv") == (False, "HANOVA_MEDIA_MOUNT")
        settings.set_media_mount_ready(True)
        assert settings.tool_available("show_on_tv") is True
    finally:
        settings.set_media_mount_ready(False)


# --- the real console app, not a fresh FastAPI (review finding 11) --------
def _real_console_stream(tmp_path):
    """Build the real `console.LocalStream` with the arguments it actually takes.

    Round 2, finding 13: the previous version called
    `LocalStream(instance_path=...)`, but the constructor is
    `LocalStream(handler, robot, *, settings_app=None, instance_path=None, ...)`
    (`console.py:104-113`) -- so both tests died with `TypeError` before they
    ever reached the mount they were written to prove. The two positional
    arguments are supplied here as the smallest fakes that satisfy what the
    constructor really touches:

    * `handler` -- `_install_handler` assigns `handler._clear_queue` and then
      looks for `set_activity_observer` / `set_transcript_observer` with
      `getattr(..., None)`, so a `SimpleNamespace` is sufficient and a
      `MagicMock` would silently satisfy the observer checks with mocks.
    * `robot` -- only stored on `self._robot` during construction.

    `settings_app` is passed **explicitly** as a real `FastAPI`, because
    `_init_settings_ui_if_needed` returns immediately when it is None
    (`console.py:516-517`) -- which is how a green-but-vacuous version of this
    test could otherwise exist.
    """
    from reachy_companion import console as console_module

    handler = types.SimpleNamespace(_clear_queue=None)
    robot = types.SimpleNamespace()
    app = FastAPI()
    stream = console_module.LocalStream(
        handler,
        robot,
        settings_app=app,
        instance_path=str(tmp_path),
    )
    stream._init_settings_ui_if_needed()
    # The accessor added in Step 5. Asserting on it rather than on the private
    # attribute is what keeps the production mount hook observable.
    assert stream.settings_app is app, "the console must expose the app it mounts onto"
    return stream


def test_the_real_settings_app_serves_the_cache_with_range_and_type(tmp_path):
    """A Chromecast issues HEAD and byte-range GETs before it plays anything.

    Finding 11: mounting a *fresh* FastAPI proves nothing about the app the robot
    actually runs. This drives `console.py`'s own settings-app wiring through
    `_init_settings_ui_if_needed`, then exercises the three request shapes a
    Chromecast really uses: HEAD for the length and content type, a full GET,
    and a `Range` GET for the seek.
    """
    nas_dir = media_store.media_dir("nas", tmp_path)
    payload = b"MP4" + bytes(1021)
    (nas_dir / "clip.mp4").write_bytes(payload)

    stream = _real_console_stream(tmp_path)

    with TestClient(stream.settings_app) as client:
        head = client.head("/hanova-media/nas/clip.mp4")
        full = client.get("/hanova-media/nas/clip.mp4")
        ranged = client.get("/hanova-media/nas/clip.mp4", headers={"Range": "bytes=0-15"})

    assert head.status_code == 200
    assert head.headers["content-type"].startswith("video/mp4")
    assert int(head.headers["content-length"]) == len(payload)

    assert full.status_code == 200
    assert full.content == payload

    assert ranged.status_code == 206
    assert ranged.headers["content-range"] == f"bytes 0-15/{len(payload)}"
    assert ranged.content == payload[:16]


def test_the_real_settings_app_refuses_a_traversal(tmp_path):
    """The static mount must not be walkable out of the media root."""
    media_store.media_dir("nas", tmp_path)
    (tmp_path / "secret.txt").write_text("SENTINEL_PRIVATE_x7", encoding="utf-8")

    stream = _real_console_stream(tmp_path)

    with TestClient(stream.settings_app) as client:
        response = client.get("/hanova-media/nas/../../secret.txt")
    assert response.status_code in (403, 404)
    assert b"SENTINEL_PRIVATE_x7" not in response.content


def test_the_media_store_logs_no_path(monkeypatch, caplog, tmp_path):
    """Round 2, finding 6: media_store is a service seam and logs like one.

    The prune and mount paths used to log the cache directory and a raw
    `logger.exception` traceback, both of which name the instance directory.
    """
    import logging

    caplog.set_level(logging.DEBUG)
    sentinel_root = tmp_path / "SENTINEL_PRIVATE_x7"
    media_dir = media_store.media_dir("music", sentinel_root)
    (media_dir / "a.mp3").write_bytes(b"ID3")

    class _NoMount:
        pass

    media_store.mount_media_routes(_NoMount(), sentinel_root)
    media_store.prune("music", sentinel_root, 0)
    assert "SENTINEL_PRIVATE_x7" not in caplog.text
```

**Note for the implementer (round 2, finding 13).** `LocalStream` does not
currently expose the FastAPI object it mounts onto; the helper above asserts
`stream.settings_app`. Add exactly one read-only property in Step 5:

```python
    @property
    def settings_app(self) -> Optional[FastAPI]:
        """The settings FastAPI this stream mounts onto, or None."""
        return self._settings_app
```

That is the whole production change to `console.py`'s class surface, and it is
what lets the mount be verified against the real object instead of a stand-in.
Nothing else about `console.py` changes.

- [ ] **Step 2: Run the tests to verify they fail**

```powershell
cd C:\Project\Reachy-mini\reachy_companion
..\.venv\Scripts\python.exe -m pytest tests/test_hanova_ha_client.py tests/test_hanova_media_store.py -q
```

Expected: two collection errors — `ModuleNotFoundError: No module named 'reachy_companion.hanova.ha_client'` and `... 'reachy_companion.hanova.media_store'`.

- [ ] **Step 3: Implement `hanova/ha_client.py`**

Create `reachy_companion/src/reachy_companion/hanova/ha_client.py`:

```python
"""Async Home Assistant REST helper for the ported capabilities (D-018).

Upstream used a blocking `urllib` call inside a single-threaded stdin loop
(`server.py:131-147`), where one slow request froze every other tool including
`stop_music`. Our tools are asyncio tasks on the realtime loop, so this uses
`httpx.AsyncClient` and never blocks the audio path.

Every function returns a result dict and never raises: a tool failure must reach
the model as tool output, exactly like `tools/home_control.py:111-113` does.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

import httpx

from reachy_companion.hanova import redact, settings


logger = logging.getLogger(__name__)


async def _request(method: str, path: str, payload: Dict[str, Any] | None, timeout_s: float) -> Dict[str, Any]:
    """Perform one authenticated Home Assistant REST call."""
    base_url = settings.ha_url()
    token = settings.ha_token()
    if not base_url or not token:
        return {"ok": False, "error": "Home Assistant is not configured; set HA_URL and HA_TOKEN."}

    url = f"{base_url}{path}"
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            response = await client.request(
                method,
                url,
                json=payload,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            )
    except httpx.HTTPError as exc:
        # Finding 7: an httpx error string embeds the full URL, which carries the
        # house's LAN address. Callers get the shape, not the address. Round 2,
        # finding 6: the *path* is not safe either -- it ends in the operator's
        # own scripts.yaml entry name -- so it is a digest here too. Round 3,
        # finding 3: no word list either -- the httpx class name (ConnectTimeout /
        # ConnectError / ReadTimeout) already IS the shape, and `redact.error`
        # reads any errno straight off the exception.
        logger.warning("Home Assistant %s %s failed: %s", method, redact.ident(path), redact.error(exc))
        return {"ok": False, "error": redact.error(exc)}

    if not (200 <= response.status_code < 300):
        logger.warning("Home Assistant %s %s -> HTTP %d", method, redact.ident(path), response.status_code)
        return {
            "ok": False,
            "error": f"Home Assistant returned HTTP {response.status_code}",
            "status_code": response.status_code,
        }

    try:
        result: Any = response.json()
    except ValueError:
        result = None
    return {"ok": True, "result": result}


async def ha_call_service(
    domain: str,
    service: str,
    data: Dict[str, Any],
    timeout_s: float = 30.0,
) -> Dict[str, Any]:
    """Call `<domain>.<service>` with *data* as the service payload."""
    return await _request("POST", f"/api/services/{domain}/{service}", data, timeout_s)


async def ha_run_script(script_name: str, data: Dict[str, Any], timeout_s: float = 60.0) -> Dict[str, Any]:
    """Run the Home Assistant script `script.<script_name>` with *data* as its fields."""
    return await _request("POST", f"/api/services/script/{script_name}", data, timeout_s)


async def ha_get_state(entity_id: str, timeout_s: float = 15.0) -> Dict[str, Any]:
    """Read one entity's current state object."""
    return await _request("GET", f"/api/states/{entity_id}", None, timeout_s)
```

- [ ] **Step 4: Implement `hanova/media_store.py`**

Create `reachy_companion/src/reachy_companion/hanova/media_store.py`:

```python
"""LAN-served media cache for the ported casting capabilities (D-018, R6).

A Chromecast fetches `media_content_id` itself, from its own network position,
so a path on the robot's disk is useless to it and `localhost` is meaningless.
Upstream solved this by writing into Home Assistant's own `www/` directory on
the Mac (`server.py:64-66`); we solve it by serving the cache off the web server
the app already runs -- the FastAPI settings app on `0.0.0.0:7860`
(`main.py:358`, `console.py:529`). No second server, no extra port.

The cache lives under the app instance directory, next to `.env`, `memory.v1.json`
and `faces.v1.json`, so the deploy ritual can reason about it in one place.
Keep-N cleanup uses upstream's caps: music 12, NAS 8.
"""

from __future__ import annotations

import logging
import tempfile
from typing import Any
from pathlib import Path

from reachy_companion.hanova import redact, settings


logger = logging.getLogger(__name__)

MEDIA_URL_PREFIX = "/hanova-media"
MEDIA_DIRNAME = "hanova_media"
KINDS: tuple[str, ...] = ("music", "nas", "images", "sfx")

# Round 2, finding 10: a file being staged by an in-progress copy. It lives here
# rather than in `nas.py` because `prune` has to know about it and `nas` already
# imports `media_store` -- the other direction would be a cycle. `nas.PART_SUFFIX`
# re-exports it so the staging code has one name for it.
PART_SUFFIX = ".part"


def media_root(instance_path: str | Path | None) -> Path:
    """Return the media cache root: the override, the instance dir, or a temp dir."""
    override = settings.media_dir_override()
    if override is not None:
        return override
    if instance_path is not None:
        return Path(instance_path) / MEDIA_DIRNAME
    return Path(tempfile.gettempdir()) / "reachy_companion_hanova_media"


def media_dir(kind: str, instance_path: str | Path | None) -> Path:
    """Return (creating if needed) the cache directory for one media *kind*."""
    if kind not in KINDS:
        raise ValueError(f"unknown media kind: {kind!r}; expected one of {KINDS}")
    directory = media_root(instance_path) / kind
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def prune(kind: str, instance_path: str | Path | None, keep: int) -> int:
    """Delete all but the *keep* most recently modified files. Returns the count removed.

    Round 2, finding 10: `*.part` files are **skipped**, not counted and not
    deleted. They are staging files belonging to a copy that is still running,
    and an LRU that deletes one destroys the download in progress.
    """
    if kind not in KINDS:
        raise ValueError(f"unknown media kind: {kind!r}; expected one of {KINDS}")
    directory = media_root(instance_path) / kind
    if not directory.is_dir():
        return 0
    try:
        files = sorted(
            (
                path
                for path in directory.iterdir()
                if path.is_file() and not path.name.endswith(PART_SUFFIX)
            ),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    except OSError as exc:
        # Round 2, finding 6: an OSError renders the full path it failed on.
        logger.warning("Could not list the %s media cache: %s", kind, redact.error(exc))
        return 0
    removed = 0
    for stale in files[max(0, keep) :]:
        try:
            stale.unlink()
            removed += 1
        except OSError as exc:
            # Finding 6: the served filename is a digest, but the *directory* is
            # the instance path. Kind and shape only.
            logger.warning("Could not prune one %s cache entry: %s", kind, redact.error(exc))
    return removed


def media_url(kind: str, filename: str) -> str | None:
    """Return the LAN URL a TV can fetch, or None when no base URL is configured."""
    base = settings.media_http_base()
    if not base:
        return None
    return f"{base}{MEDIA_URL_PREFIX}/{kind}/{filename}"


# NOTE (review finding 15): there is deliberately **no** `safe_filename` helper
# here. The first draft had one that flattened a source path and then truncated
# it to 150 characters, which mapped two different NAS clips onto one served
# filename whenever their paths agreed that far -- which, for
# `<trip>/<date>/<camera>/clipNN.mp4`, they routinely do. Callers derive served
# names from a hash of a validated path instead (`nas.cast_filename`), or from an
# id they already own (`ytdlp.download_audio`, `images.generate_image`).


def mount_media_routes(app: Any, instance_path: str | Path | None) -> bool:
    """Mount the media cache as a static route on the app's settings server (R6).

    Returns True when the route is live, **and records that verdict** in
    `settings.set_media_mount_ready()` so `show_on_tv` and every `nas_*` cast
    become `unavailable` rather than handing a Chromecast a URL that this process
    is not actually serving (review round 1, finding 11). Never raises: a
    settings app that cannot mount must degrade, not abort startup.
    """
    if not hasattr(app, "mount"):
        logger.warning("Settings app cannot mount routes; hanova media will not be served.")
        settings.set_media_mount_ready(False)
        return False
    try:
        from starlette.staticfiles import StaticFiles

        root = media_root(instance_path)
        for kind in KINDS:
            (root / kind).mkdir(parents=True, exist_ok=True)
        app.mount(MEDIA_URL_PREFIX, StaticFiles(directory=str(root)), name="hanova-media")
    except Exception as exc:  # noqa: BLE001 - a failed mount must degrade, not abort
        # Round 2, finding 6: `logger.exception` prints a traceback whose frames
        # carry the instance path, and the message interpolated the root too.
        # The shape is enough to act on; the path is not ours to publish.
        logger.warning("Failed to mount the hanova media cache at %s: %s", MEDIA_URL_PREFIX, redact.error(exc))
        settings.set_media_mount_ready(False)
        return False
    settings.set_media_mount_ready(True)
    logger.info(
        "hanova media served at %s (base URL configured: %s, kinds: %d)",
        MEDIA_URL_PREFIX,
        bool(settings.media_http_base()),
        len(KINDS),
    )
    return True


__all__ = [
    "KINDS",
    "MEDIA_DIRNAME",
    "MEDIA_URL_PREFIX",
    "PART_SUFFIX",
    "media_dir",
    "media_root",
    "media_url",
    "mount_media_routes",
    "prune",
]
```

- [ ] **Step 5: Mount the route from console.py**

**5a — expose the settings app (round 2, finding 13).** In
`reachy_companion/src/reachy_companion/console.py`, add this read-only property
to `LocalStream`, immediately after `_install_handler`:

```python
    @property
    def settings_app(self) -> Optional[FastAPI]:
        """The settings FastAPI this stream mounts onto, or None.

        D-018: the media-route integration tests drive the *real* console object
        and need the app it actually mounted onto, not a stand-in
        (review round 1 finding 11, round 2 finding 13).
        """
        return self._settings_app
```

`Optional` and `FastAPI` are already imported in this module
(`console.py:104-113`), so this adds no imports.

**5b — mount the media route.** In the same file, find this exact block inside `_init_settings_ui_if_needed`:

```python
        if hasattr(settings_app, "mount"):
            try:
                settings_app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
            except Exception:
                logger.exception("Failed to mount settings UI static assets")
                raise
```

and insert immediately **after** it:

```python
        # D-018 / R6: serve the ported media cache from this same uvicorn, which
        # already binds 0.0.0.0:7860 (main.py:358). A Chromecast on the LAN then
        # fetches HANOVA_MEDIA_HTTP_BASE + /hanova-media/<kind>/<file>. Failure
        # to mount degrades casting to unavailable; it must not abort startup.
        from reachy_companion.hanova.media_store import mount_media_routes

        mount_media_routes(settings_app, self._instance_path)
```

- [ ] **Step 6: Run the tests to verify they pass**

```powershell
cd C:\Project\Reachy-mini\reachy_companion
..\.venv\Scripts\python.exe -m pytest tests/test_hanova_ha_client.py tests/test_hanova_media_store.py -q
```

Expected: green — **8** test functions from `test_hanova_ha_client.py` (round 3,
finding 3 added the caplog sentinel) and
**18** from `test_hanova_media_store.py`, neither parametrised: **26 collected
cases**, including the two real-console integration tests and **both** caplog
sentinels (media store and HA client). Record the exact number.

- [ ] **Step 7: Run the full gate**

```powershell
cd C:\Project\Reachy-mini\reachy_companion
..\.venv\Scripts\python.exe -m pytest -q
..\.venv\Scripts\python.exe -m ruff check src tests
..\.venv\Scripts\python.exe -m ruff format --check src tests
..\.venv\Scripts\python.exe -m mypy
```

Expected: pytest green (30 documented skips), ruff clean, mypy `Success: no issues found`.

- [ ] **Step 8: Commit**

```bash
cd /c/Project/Reachy-mini
git rev-parse --abbrev-ref HEAD   # must print feat/ha-nova-port (Task 0)
git add reachy_companion/src/reachy_companion/hanova/ha_client.py \
        reachy_companion/src/reachy_companion/hanova/media_store.py \
        reachy_companion/src/reachy_companion/console.py \
        reachy_companion/tests/test_hanova_ha_client.py \
        reachy_companion/tests/test_hanova_media_store.py
git commit -m "feat(hanova): async HA REST client and LAN-served media cache"
```

---

### Task 4: yt-dlp layer, robot-speaker music player, `play_music` and `stop_music`

Implements R2 (`play_music` ALWAYS plays on the robot's own speaker; `play_music_here` is merged away; the Voice-PE and TV-cast music paths are not ported) and the playback half of R7 (music goes through the daemon's `media/play_sound`, never through the realtime audio output).

**Review round 1, finding 2 — the player is a serialized async state machine.**
The first draft released its lock across every await, so a slow `play` could
land *after* a `stop_music`, a resume could revive a track the user had already
stopped, and a second `play` could interleave with the first. It also called the
SDK's `MediaManager.play_sound`, which swallows a non-2xx from the daemon, so the
tool could report `playing` when nothing played, and it marked the state
`paused` even when the stop request failed. All four are fixed here:

- one `asyncio.Lock` serialises every transition, and a monotonically increasing
  **generation token** is re-checked after *every* await; a transition whose
  generation has been superseded aborts and undoes nothing it did not do;
- both `play` and `stop` go through the daemon REST API directly
  (`POST /api/media/play_sound`, `POST /api/media/stop_sound`) and **check the
  HTTP status**, so an unacknowledged command is a failure, not a success;
- `pause_for_speech` only marks the state paused when the stop was acknowledged;
  a failed stop leaves the music playing and says so;
- the tests cover play-vs-stop, resume-vs-stop, play-vs-play and failed-stop.

**Files:**
- Create: `reachy_companion/src/reachy_companion/hanova/ytdlp.py`
- Create: `reachy_companion/src/reachy_companion/hanova/music_player.py`
- Create: `reachy_companion/src/reachy_companion/tools/play_music.py`
- Create: `reachy_companion/src/reachy_companion/tools/stop_music.py`
- Modify: `reachy_companion/profiles/_reachy_companion_locked_profile/profile.md` (add `"play_music"`, `"stop_music"` to `default_tools`)
- Test: `reachy_companion/tests/test_hanova_ytdlp.py`
- Test: `reachy_companion/tests/test_hanova_music.py`

**Interfaces:**
- Consumes: `hanova.settings.{tool_status, unavailable, ytdlp_search_n, ytdlp_timeout_s, ytdlp_download_timeout_s}` (Task 1); `hanova.redact` (Task 1); `hanova.media_store.{media_dir, prune}` (Task 3); `settings.music_keep` (Task 1); `tools.core_tools.{Tool, ToolDependencies}`.
- Produces:
  - `hanova.ytdlp.ytdlp_available() -> bool`
  - `hanova.ytdlp.ffmpeg_exe() -> str | None`
  - `hanova.ytdlp.run_command(cmd: list[str], timeout_s: int) -> subprocess.CompletedProcess[str]` — the single subprocess seam every test monkeypatches
  - `hanova.ytdlp.search(query: str, max_duration_s: int | None = None) -> Dict[str, Any]` returning `{"ok": bool, "id": str | None, "title": str | None, "error": str | None}`
  - `hanova.ytdlp.download_audio(video_id: str, dest_dir: Path) -> Dict[str, Any]` returning `{"ok": bool, "path": str | None, "cached": bool, "error": str | None}`
  - `hanova.ytdlp.cut_from(source: Path, offset_s: float, dest: Path) -> bool`
  - `hanova.music_player.MusicState` — mutable dataclass with `video_id: str`, `title: str`, `source_path: Path`, `started_at: float`, `offset_s: float`, `paused: bool`, `generation: int`
  - `hanova.music_player.MusicPlayer` with `current() -> MusicState | None`, `generation() -> int`, `invalidate() -> int`, `reset() -> None`, and the coroutines `play(deps, *, video_id, title, source_path) -> Dict[str, Any]`, `stop(deps) -> Dict[str, Any]`, `pause_for_speech(deps) -> Dict[str, Any]`, `resume_after_speech(deps) -> Dict[str, Any]`
  - **`invalidate()` vs `reset()` (round 2, finding 8).** `reset()` drops the state snapshot and nothing else: it does not bump the generation and it does not touch the speaker, so a transition already in flight can still land afterwards and repopulate the state. That is fine inside an isolated test and wrong at a session boundary. `invalidate()` bumps the generation under the state lock and clears the snapshot, so every in-flight transition sees itself superseded and undoes its own side effect. **`reset()` is for tests only**; `music_hooks` uses `invalidate()` plus an actual `stop(deps)` at both session boundaries, and the Task 14 shape test asserts `PLAYER.reset(` appears nowhere under `src/`.
  - `hanova.music_player.PLAYER` — the process-wide `MusicPlayer`
  - `hanova.music_player.daemon_base_url(deps: Any) -> str`
  - `hanova.music_player.daemon_stop_sound(deps: Any) -> Awaitable[bool]` — returns the **acknowledgement**, not just "we sent it"
  - `hanova.music_player.daemon_play_sound(deps: Any, path: str) -> Awaitable[bool]` — the direct REST call that replaces `MediaManager.play_sound` (finding 2)
  - `tools.play_music.PlayMusic` (`Tool.name == "play_music"`), `tools.stop_music.StopMusic` (`Tool.name == "stop_music"`)

**Why the daemon REST call for both directions:** the client-side `MediaManager` exposes `play_sound` (`reference/reachy_mini/src/reachy_mini/media/media_manager.py:264`) but has **no** `stop_sound` — the only stop is `POST /api/media/stop_sound` on the daemon (`reference/reachy_mini/src/reachy_mini/daemon/app/routers/media.py:105-115`). And `MediaManager.play_sound` **swallows a non-2xx** from `POST /api/media/play_sound`, so a failed playback is indistinguishable from a successful one through that path (review finding 2). Both directions therefore go straight to the daemon and check the status code. The daemon base URL is `deps.reachy_mini._daemon_http_url` (`reachy_mini.py:160`), with `http://127.0.0.1:8000` as fallback.

- [ ] **Step 1: Write the failing tests**

Create `reachy_companion/tests/test_hanova_ytdlp.py`:

```python
"""Contract tests for the yt-dlp / ffmpeg layer (D-018, R8). No network, ever."""

import subprocess
from pathlib import Path

import pytest

from reachy_companion.hanova import ytdlp


def _completed(stdout: str = "", stderr: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["yt-dlp"], returncode=returncode, stdout=stdout, stderr=stderr)


@pytest.fixture(autouse=True)
def available(monkeypatch):
    """Pretend both wheels are installed unless a test says otherwise."""
    monkeypatch.setattr(ytdlp, "ytdlp_available", lambda: True)
    monkeypatch.setattr(ytdlp, "ffmpeg_exe", lambda: "/opt/ffmpeg")
    monkeypatch.delenv("HANOVA_YTDLP_SEARCH_N", raising=False)


def test_search_builds_the_upstream_argv(monkeypatch):
    """Same search contract upstream used: ytsearchN, live/short filtered out."""
    seen = {}

    def fake_run(cmd, timeout_s):
        seen["cmd"] = cmd
        seen["timeout_s"] = timeout_s
        return _completed("dQw4w9WgXcQ\nA Song Title\n")

    monkeypatch.setattr(ytdlp, "run_command", fake_run)
    out = ytdlp.search("some song")
    assert out == {"ok": True, "id": "dQw4w9WgXcQ", "title": "A Song Title", "error": None}
    cmd = seen["cmd"]
    assert "--default-search" in cmd and "ytsearch5:" in cmd
    assert cmd[cmd.index("--match-filter") + 1] == "duration > 30 & !is_live"
    assert "--no-playlist" in cmd and "--skip-download" in cmd
    assert cmd[-1] == "some song"
    assert seen["timeout_s"] == 20


def test_search_honours_a_max_duration(monkeypatch):
    """Music we will download whole needs an upper duration bound."""
    seen = {}

    def fake_run(cmd, timeout_s):
        seen["cmd"] = cmd
        return _completed("abc\nTitle\n")

    monkeypatch.setattr(ytdlp, "run_command", fake_run)
    ytdlp.search("some song", max_duration_s=900)
    assert seen["cmd"][seen["cmd"].index("--match-filter") + 1] == "duration > 30 & duration < 900 & !is_live"


def test_search_without_the_wheel_is_reported(monkeypatch):
    """A robot missing yt-dlp answers, it does not raise."""
    monkeypatch.setattr(ytdlp, "ytdlp_available", lambda: False)
    out = ytdlp.search("anything")
    assert out["ok"] is False
    assert "yt-dlp" in out["error"]


def test_search_timeout_is_reported(monkeypatch):
    """A hung search must become a tool result inside its own budget."""

    def fake_run(cmd, timeout_s):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout_s)

    monkeypatch.setattr(ytdlp, "run_command", fake_run)
    out = ytdlp.search("anything")
    assert out["ok"] is False
    assert "timed out" in out["error"]


def test_search_empty_result_is_reported(monkeypatch):
    """YouTube rate-limits bursts with a clean exit and no output."""
    monkeypatch.setattr(ytdlp, "run_command", lambda cmd, timeout_s: _completed(""))
    out = ytdlp.search("anything")
    assert out["ok"] is False
    assert out["id"] is None


def test_search_extraction_error_is_reported_without_the_stderr(monkeypatch):
    """A hard yt-dlp failure must be reported, but not by forwarding stderr.

    Round 2, finding 6: yt-dlp's stderr echoes the query and the resolved URL
    straight back, and the previous version returned its last 300 characters as
    the tool's `error` -- which the model then reads out loud.
    """
    monkeypatch.setattr(
        ytdlp,
        "run_command",
        lambda cmd, timeout_s: _completed("", "ERROR: SENTINEL_PRIVATE_x7 is unavailable", returncode=1),
    )
    out = ytdlp.search("anything")
    assert out["ok"] is False
    assert out["id"] is None
    assert "SENTINEL_PRIVATE_x7" not in out["error"]


def test_the_ytdlp_layer_logs_no_query_url_or_stderr(monkeypatch, caplog, tmp_path):
    """Round 2, finding 6: ytdlp.py is a service seam and logs like one."""
    import logging

    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(
        ytdlp,
        "run_command",
        lambda cmd, timeout_s: _completed("", "ERROR: https://example.invalid/SENTINEL_PRIVATE_x7", returncode=1),
    )
    ytdlp.search("SENTINEL_PRIVATE_x7")
    ytdlp.download_audio("SENTINEL_PRIVATE_x7", tmp_path)

    def boom(cmd, timeout_s):
        raise OSError("cannot run SENTINEL_PRIVATE_x7")

    monkeypatch.setattr(ytdlp, "run_command", boom)
    ytdlp.search("anything")
    ytdlp.download_audio("abc123", tmp_path)
    assert "SENTINEL_PRIVATE_x7" not in caplog.text


def test_download_audio_reuses_a_cached_file(monkeypatch, tmp_path):
    """Repeat plays must be instant and must not touch the network."""
    cached = tmp_path / "abc123.mp3"
    cached.write_bytes(b"ID3data")

    def fail_run(cmd, timeout_s):
        raise AssertionError("download_audio must not run yt-dlp for a cached track")

    monkeypatch.setattr(ytdlp, "run_command", fail_run)
    out = ytdlp.download_audio("abc123", tmp_path)
    assert out == {"ok": True, "path": str(cached), "cached": True, "error": None}


def test_download_audio_passes_the_bundled_ffmpeg(monkeypatch, tmp_path):
    """The wheel's ffmpeg is not on PATH, so yt-dlp must be pointed at it."""
    seen = {}

    def fake_run(cmd, timeout_s):
        seen["cmd"] = cmd
        (tmp_path / "abc123.mp3").write_bytes(b"ID3data")
        return _completed("")

    monkeypatch.setattr(ytdlp, "run_command", fake_run)
    out = ytdlp.download_audio("abc123", tmp_path)
    assert out["ok"] is True and out["cached"] is False
    cmd = seen["cmd"]
    assert cmd[cmd.index("--ffmpeg-location") + 1] == "/opt/ffmpeg"
    assert "--audio-format" in cmd and cmd[cmd.index("--audio-format") + 1] == "mp3"
    assert "https://www.youtube.com/watch?v=abc123" in cmd


def test_download_audio_reports_a_missing_output(monkeypatch, tmp_path):
    """yt-dlp can exit 0 and still produce nothing; that is a failure."""
    monkeypatch.setattr(
        ytdlp, "run_command", lambda cmd, timeout_s: _completed("", "boom SENTINEL_PRIVATE_x7", returncode=1)
    )
    out = ytdlp.download_audio("abc123", tmp_path)
    assert out["ok"] is False
    assert out["path"] is None
    # Round 2, finding 6: the tail of yt-dlp's output is not the tool's error.
    assert "SENTINEL_PRIVATE_x7" not in out["error"]


def test_download_audio_without_ffmpeg_is_reported(monkeypatch, tmp_path):
    """No transcoder means no mp3; say so instead of producing a broken file."""
    monkeypatch.setattr(ytdlp, "ffmpeg_exe", lambda: None)
    out = ytdlp.download_audio("abc123", tmp_path)
    assert out["ok"] is False
    assert "ffmpeg" in out["error"]


def test_cut_from_builds_a_seeking_ffmpeg_command(monkeypatch, tmp_path):
    """Resume-after-speech is implemented as a stream-copy seek."""
    seen = {}
    source = tmp_path / "abc123.mp3"
    source.write_bytes(b"ID3data")
    dest = tmp_path / "abc123.resume.mp3"

    def fake_run(cmd, timeout_s):
        seen["cmd"] = cmd
        dest.write_bytes(b"ID3cut")
        return _completed("")

    monkeypatch.setattr(ytdlp, "run_command", fake_run)
    assert ytdlp.cut_from(source, 42.5, dest) is True
    cmd = seen["cmd"]
    assert cmd[0] == "/opt/ffmpeg"
    assert cmd[cmd.index("-ss") + 1] == "42.500"
    assert "-c" in cmd and cmd[cmd.index("-c") + 1] == "copy"
    assert cmd[-1] == str(dest)


def test_cut_from_returns_false_on_failure(monkeypatch, tmp_path):
    """A failed trim leaves the caller free to give up cleanly."""
    source = tmp_path / "abc123.mp3"
    source.write_bytes(b"ID3data")
    monkeypatch.setattr(ytdlp, "run_command", lambda cmd, timeout_s: _completed("", "bad", returncode=1))
    assert ytdlp.cut_from(source, 10.0, tmp_path / "out.mp3") is False
```

Create `reachy_companion/tests/test_hanova_music.py`:

```python
"""Contract tests for robot-speaker music playback and its two tools (D-018, R2/R7).

Also pins review round 1 finding 2: the player is a serialized state machine
with generation tokens, both daemon commands are acknowledged, and the four
interleavings that used to be losable are covered explicitly.
"""

import types
import asyncio
import logging
import importlib
from pathlib import Path

import httpx
import pytest

from reachy_companion.hanova import ytdlp
from reachy_companion.hanova.music_player import PLAYER
from reachy_companion.tools.play_music import PlayMusic
from reachy_companion.tools.stop_music import StopMusic


class _Response:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code


class _FakeDaemon:
    """Records what the daemon was actually asked to do, and how it answered.

    The SDK's `MediaManager.play_sound` swallows a non-2xx, so the player calls
    the daemon REST API itself; this stands in for that API (finding 2).
    """

    def __init__(self) -> None:
        self.plays: list[str] = []
        self.stops = 0
        self.play_status = 200
        self.stop_status = 200
        self.play_delay = 0.0
        self.stop_delay = 0.0

    async def post(self, url: str, json=None, **kw):
        if url.endswith("/api/media/play_sound"):
            await asyncio.sleep(self.play_delay)
            self.plays.append(str((json or {}).get("file")))
            return _Response(self.play_status)
        if url.endswith("/api/media/stop_sound"):
            await asyncio.sleep(self.stop_delay)
            self.stops += 1
            return _Response(self.stop_status)
        raise AssertionError(f"unexpected daemon call: {url}")


@pytest.fixture
def daemon(monkeypatch):
    """Install the fake daemon as the only transport the player can reach."""
    fake = _FakeDaemon()

    async def fake_post(self, url, json=None, **kw):
        return await fake.post(url, json=json, **kw)

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    return fake


def _deps(instance_path=None):
    """A ToolDependencies-shaped stub exposing only what music touches."""
    robot = types.SimpleNamespace(_daemon_http_url="http://127.0.0.1:8000")
    return types.SimpleNamespace(reachy_mini=robot, instance_path=instance_path)


@pytest.fixture(autouse=True)
def clean_player(monkeypatch):
    """Every test starts with nothing playing and both wheels present."""
    PLAYER.reset()
    monkeypatch.setattr("reachy_companion.hanova.settings._music_wheels_ready", lambda: (True, ""))
    yield
    PLAYER.reset()


def _track(tmp_path, name="abc.mp3"):
    path = tmp_path / name
    path.write_bytes(b"ID3")
    return path


def test_tool_names_match_their_filenames():
    """The loader resolves tools by filename == Tool.name (core_tools.py:403)."""
    assert PlayMusic.name == "play_music"
    assert StopMusic.name == "stop_music"


def test_descriptions_carry_no_personal_identifier():
    """R10: no entity id, address, folder id or owner name in a description."""
    for text in (PlayMusic().description, StopMusic().description):
        assert "@" not in text
        assert "media_player." not in text
        assert len(text) <= 120


@pytest.mark.asyncio
async def test_play_starts_the_sound_and_records_state(daemon, tmp_path):
    """Playback goes through the daemon's play_sound, not the realtime output."""
    track = _track(tmp_path)
    out = await PLAYER.play(_deps(), video_id="abc", title="A Song", source_path=track)
    assert out["ok"] is True and out["status"] == "playing"
    assert daemon.plays == [str(track)]
    state = PLAYER.current()
    assert state is not None and state.video_id == "abc" and state.paused is False


@pytest.mark.asyncio
async def test_an_unacknowledged_play_is_a_failure_not_a_success(daemon, tmp_path):
    """Finding 2: the SDK swallows this; we must not report `playing` on a 500."""
    daemon.play_status = 500
    out = await PLAYER.play(_deps(), video_id="abc", title="A Song", source_path=_track(tmp_path))
    assert out["ok"] is False
    assert PLAYER.current() is None


@pytest.mark.asyncio
async def test_pause_for_speech_stops_the_daemon_sound_and_banks_the_offset(daemon, tmp_path):
    """R7: user speech ducks the music by stopping it and remembering where."""
    deps = _deps()
    await PLAYER.play(deps, video_id="abc", title="A Song", source_path=_track(tmp_path))
    # MusicState is mutable and `current()` hands back the live object, so this
    # simulates 30 s of playback without patching the global clock.
    PLAYER.current().started_at -= 30.0
    out = await PLAYER.pause_for_speech(deps)

    state = PLAYER.current()
    assert out["ok"] is True and daemon.stops == 1
    assert state is not None and state.paused is True
    assert state.offset_s == pytest.approx(30.0, abs=0.5)


@pytest.mark.asyncio
async def test_a_failed_stop_leaves_the_music_playing(daemon, tmp_path):
    """Finding 2: marking it paused when the daemon refused is a lie in state."""
    deps = _deps()
    await PLAYER.play(deps, video_id="abc", title="A Song", source_path=_track(tmp_path))
    daemon.stop_status = 503
    out = await PLAYER.pause_for_speech(deps)
    state = PLAYER.current()
    assert out["ok"] is False
    assert state is not None and state.paused is False


@pytest.mark.asyncio
async def test_a_failed_stop_in_stop_music_is_reported(daemon, tmp_path):
    """`stop` that the daemon did not acknowledge must not claim `stopped`."""
    deps = _deps()
    await PLAYER.play(deps, video_id="abc", title="A Song", source_path=_track(tmp_path))
    daemon.stop_status = 500
    out = await PLAYER.stop(deps)
    assert out["ok"] is False and out["status"] != "stopped"


@pytest.mark.asyncio
async def test_pause_is_a_no_op_when_nothing_plays(daemon):
    """Barge-in fires on every turn; with no music it must cost nothing."""
    await PLAYER.pause_for_speech(_deps())
    assert PLAYER.current() is None
    assert daemon.stops == 0 and daemon.plays == []


@pytest.mark.asyncio
async def test_resume_replays_from_the_banked_offset(daemon, monkeypatch, tmp_path):
    """The daemon has no pause, so resume re-cuts the file and plays the tail."""
    deps = _deps()
    track = _track(tmp_path)
    await PLAYER.play(deps, video_id="abc", title="A Song", source_path=track)
    PLAYER.current().started_at -= 30.0
    await PLAYER.pause_for_speech(deps)

    cuts = {}

    def fake_cut(source, offset_s, dest):
        cuts["source"], cuts["offset_s"], cuts["dest"] = source, offset_s, dest
        Path(dest).write_bytes(b"ID3tail")
        return True

    monkeypatch.setattr(ytdlp, "cut_from", fake_cut)
    await PLAYER.resume_after_speech(deps)

    assert cuts["source"] == track
    assert cuts["offset_s"] == pytest.approx(30.0, abs=0.5)
    assert daemon.plays[-1] == str(cuts["dest"])
    state = PLAYER.current()
    assert state is not None and state.paused is False


@pytest.mark.asyncio
async def test_resume_when_not_paused_does_nothing(daemon, tmp_path):
    """The drain signal fires per turn; resuming un-paused music would restart it."""
    deps = _deps()
    track = _track(tmp_path)
    await PLAYER.play(deps, video_id="abc", title="A Song", source_path=track)
    await PLAYER.resume_after_speech(deps)
    assert daemon.plays == [str(track)]


@pytest.mark.asyncio
async def test_failed_resume_gives_up_cleanly(daemon, monkeypatch, tmp_path):
    """A broken trim must not restart the track from zero in the user's face."""
    deps = _deps()
    track = _track(tmp_path)
    await PLAYER.play(deps, video_id="abc", title="A Song", source_path=track)
    PLAYER.current().started_at -= 30.0
    await PLAYER.pause_for_speech(deps)
    monkeypatch.setattr(ytdlp, "cut_from", lambda source, offset_s, dest: False)
    await PLAYER.resume_after_speech(deps)
    assert daemon.plays == [str(track)]
    assert PLAYER.current() is None


@pytest.mark.asyncio
async def test_stop_clears_state_and_reports_the_title(daemon, tmp_path):
    """`stop_music` must always work by voice, even mid-download of something else."""
    deps = _deps()
    await PLAYER.play(deps, video_id="abc", title="A Song", source_path=_track(tmp_path))
    out = await PLAYER.stop(deps)
    assert out["ok"] is True and out["status"] == "stopped" and out["title"] == "A Song"
    assert PLAYER.current() is None


@pytest.mark.asyncio
async def test_stop_when_idle_is_still_ok(daemon):
    """Stopping silence is a no-op, not an error the model must apologise for."""
    out = await PLAYER.stop(_deps())
    assert out["ok"] is True and out["status"] == "nothing_playing"


# --- the four interleavings (review finding 2) ----------------------------
@pytest.mark.asyncio
async def test_play_racing_a_stop_never_leaves_music_running(daemon, tmp_path):
    """A slow play that lands after stop_music must not resurrect the speaker."""
    deps = _deps()
    daemon.play_delay = 0.05
    play_task = asyncio.create_task(
        PLAYER.play(deps, video_id="abc", title="A Song", source_path=_track(tmp_path))
    )
    await asyncio.sleep(0)  # let play acquire the lock and start its I/O
    stop_result = await PLAYER.stop(deps)
    play_result = await play_task

    assert PLAYER.current() is None, "stop_music must win against a slower play"
    assert stop_result["ok"] is True
    assert play_result.get("status") in {"superseded", "playing"}
    if play_result.get("status") == "playing":
        # If play won the lock first, the stop that followed it must still have
        # been sent and the state must still be clear.
        assert daemon.stops >= 1


@pytest.mark.asyncio
async def test_resume_racing_a_stop_never_resurrects_the_track(daemon, monkeypatch, tmp_path):
    """The ffmpeg re-cut is slow; a stop during it must win."""
    deps = _deps()
    track = _track(tmp_path)
    await PLAYER.play(deps, video_id="abc", title="A Song", source_path=track)
    PLAYER.current().started_at -= 30.0
    await PLAYER.pause_for_speech(deps)

    def slow_cut(source, offset_s, dest):
        Path(dest).write_bytes(b"ID3tail")
        return True

    monkeypatch.setattr(ytdlp, "cut_from", slow_cut)
    daemon.play_delay = 0.05
    resume_task = asyncio.create_task(PLAYER.resume_after_speech(deps))
    await asyncio.sleep(0)
    await PLAYER.stop(deps)
    await resume_task
    assert PLAYER.current() is None


@pytest.mark.asyncio
async def test_the_newer_of_two_plays_wins(daemon, tmp_path):
    """Two songs asked for in quick succession must not interleave."""
    deps = _deps()
    first = _track(tmp_path, "first.mp3")
    second = _track(tmp_path, "second.mp3")
    daemon.play_delay = 0.05

    task_one = asyncio.create_task(PLAYER.play(deps, video_id="one", title="One", source_path=first))
    await asyncio.sleep(0)
    task_two = asyncio.create_task(PLAYER.play(deps, video_id="two", title="Two", source_path=second))
    await asyncio.gather(task_one, task_two)

    state = PLAYER.current()
    assert state is not None and state.video_id == "two"
    assert daemon.plays[-1] == str(second)


@pytest.mark.asyncio
async def test_a_superseded_transition_reports_itself(daemon, tmp_path):
    """A losing transition must say so rather than pretend it succeeded."""
    deps = _deps()
    generation_before = PLAYER.generation()
    await PLAYER.play(deps, video_id="abc", title="A Song", source_path=_track(tmp_path))
    assert PLAYER.generation() > generation_before


# --- the two tools ---------------------------------------------------------
@pytest.mark.asyncio
async def test_play_music_reports_unavailable_when_wheels_are_missing(daemon, monkeypatch):
    """R5: the tool disables cleanly instead of raising ImportError."""
    monkeypatch.setattr(
        "reachy_companion.hanova.settings._music_wheels_ready", lambda: (False, "yt-dlp not installed")
    )
    out = await PlayMusic()(deps=_deps(), query="anything")
    assert out == {"status": "unavailable", "reason": "MUSIC_WHEELS"}


@pytest.mark.asyncio
async def test_stop_music_is_available_with_zero_configuration(daemon, monkeypatch):
    """Finding 10: the safety lane must answer even when nothing else can."""
    monkeypatch.setattr(
        "reachy_companion.hanova.settings._music_wheels_ready", lambda: (False, "yt-dlp not installed")
    )
    out = await StopMusic()(deps=_deps())
    assert out["status"] == "nothing_playing"


@pytest.mark.asyncio
async def test_play_music_reports_a_search_failure(daemon, monkeypatch):
    """A rate-limited search is a spoken answer, not a stack trace."""
    import reachy_companion.tools.play_music as play_music_module

    monkeypatch.setattr(
        play_music_module.ytdlp,
        "search",
        lambda query, max_duration_s=None: {"ok": False, "id": None, "title": None, "error": "no result"},
    )
    out = await PlayMusic()(deps=_deps(), query="something obscure")
    assert out["ok"] is False and out["error"]


@pytest.mark.asyncio
async def test_play_music_happy_path(daemon, monkeypatch, tmp_path):
    """Search -> download -> play on the robot speaker, and report the real title."""
    import reachy_companion.tools.play_music as play_music_module

    track = _track(tmp_path)
    monkeypatch.setattr(
        play_music_module.ytdlp,
        "search",
        lambda query, max_duration_s=None: {"ok": True, "id": "abc", "title": "A Song", "error": None},
    )
    monkeypatch.setattr(
        play_music_module.ytdlp,
        "download_audio",
        lambda video_id, dest_dir: {"ok": True, "path": str(track), "cached": True, "error": None},
    )
    out = await PlayMusic()(deps=_deps(tmp_path), query="a song")
    assert out["ok"] is True and out["title"] == "A Song"
    assert daemon.plays == [str(track)]


@pytest.mark.asyncio
async def test_play_music_rejects_an_empty_query(daemon):
    """An empty query must not reach yt-dlp."""
    out = await PlayMusic()(deps=_deps(), query="   ")
    assert out["ok"] is False


@pytest.mark.asyncio
async def test_stop_music_tool_delegates_to_the_player(daemon, tmp_path):
    """One code path for stopping, whether spoken or triggered internally."""
    deps = _deps()
    await PLAYER.play(deps, video_id="abc", title="A Song", source_path=_track(tmp_path))
    out = await StopMusic()(deps=deps)
    assert out["status"] == "stopped"
    assert PLAYER.current() is None


@pytest.mark.asyncio
async def test_music_logs_never_carry_the_query_or_the_title(daemon, monkeypatch, caplog, tmp_path):
    """Finding 7: what the user asked for is theirs; the log gets metadata only."""
    import reachy_companion.tools.play_music as play_music_module

    sentinel = "SENTINEL_PRIVATE_x7"
    track = _track(tmp_path)
    monkeypatch.setattr(
        play_music_module.ytdlp,
        "search",
        lambda query, max_duration_s=None: {"ok": True, "id": sentinel, "title": sentinel, "error": None},
    )
    monkeypatch.setattr(
        play_music_module.ytdlp,
        "download_audio",
        lambda video_id, dest_dir: {"ok": True, "path": str(track), "cached": True, "error": None},
    )
    caplog.set_level(logging.DEBUG)
    await PlayMusic()(deps=_deps(tmp_path), query=f"a song about {sentinel}")
    assert sentinel not in caplog.text


def test_both_tools_reach_the_model_session():
    """The locked profile must list them, or the model never sees them."""
    core_tools = importlib.import_module("reachy_companion.tools.core_tools")
    core_tools.initialize_tools(force=True)
    try:
        names = {spec["name"] for spec in core_tools.get_tool_specs()}
        assert {"play_music", "stop_music"} <= names
    finally:
        core_tools._TOOLS_SIGNATURE = None
```

- [ ] **Step 2: Run the tests to verify they fail**

```powershell
cd C:\Project\Reachy-mini\reachy_companion
..\.venv\Scripts\python.exe -m pytest tests/test_hanova_ytdlp.py tests/test_hanova_music.py -q
```

Expected: collection errors for `reachy_companion.hanova.ytdlp`, `reachy_companion.hanova.music_player`, `reachy_companion.tools.play_music`, `reachy_companion.tools.stop_music`.

- [ ] **Step 3: Implement `hanova/ytdlp.py`**

Create `reachy_companion/src/reachy_companion/hanova/ytdlp.py`:

```python
"""yt-dlp and ffmpeg layer for music playback (D-018, R8).

Upstream shelled out to Homebrew binaries (`server.py:258`, `:338-341`). We have
no system packages, so both come from wheels: `yt-dlp` is invoked as
`sys.executable -m yt_dlp` (the console script is not reliably on PATH inside the
robot's shared apps venv), and ffmpeg comes from `imageio-ffmpeg`, which ships a
`manylinux2014_aarch64` wheel with the binary inside it and exposes
`get_ffmpeg_exe()`. `static-ffmpeg` was rejected: it downloads its binaries on
first use, which needs network at playback time.

Every subprocess goes through `run_command`, the one seam tests monkeypatch.
Everything here is synchronous and must be called from `asyncio.to_thread`.
"""

from __future__ import annotations

import os
import sys
import logging
import subprocess
import importlib.util
from typing import Any, Dict
from pathlib import Path

from reachy_companion.hanova import redact, settings


logger = logging.getLogger(__name__)


def ytdlp_available() -> bool:
    """Return whether the yt-dlp wheel is importable in this interpreter."""
    return importlib.util.find_spec("yt_dlp") is not None


def ffmpeg_exe() -> str | None:
    """Return the path to the wheel-bundled ffmpeg binary, or None."""
    try:
        import imageio_ffmpeg

        path = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # noqa: BLE001 - a missing wheel must not raise here
        # Round 2, finding 6: an ImportError/OSError here renders the venv path.
        logger.warning("imageio-ffmpeg is unavailable: %s", redact.error(exc))
        return None
    return str(path) if path else None


def run_command(cmd: list[str], timeout_s: int) -> subprocess.CompletedProcess[str]:
    """Run one child process with captured text output. The single test seam."""
    env = os.environ.copy()
    env.setdefault("LANG", "en_US.UTF-8")
    env.setdefault("LC_ALL", "en_US.UTF-8")
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout_s,
        env=env,
        check=False,
    )


def _ytdlp_argv() -> list[str]:
    return [sys.executable, "-m", "yt_dlp"]


def search(query: str, max_duration_s: int | None = None) -> Dict[str, Any]:
    """Resolve *query* to one YouTube id and title. Never raises."""
    if not ytdlp_available():
        return {"ok": False, "id": None, "title": None, "error": "yt-dlp is not installed on this robot"}

    match_filter = "duration > 30 & !is_live"
    if max_duration_s:
        match_filter = f"duration > 30 & duration < {int(max_duration_s)} & !is_live"

    cmd = _ytdlp_argv() + [
        "--default-search",
        f"ytsearch{settings.ytdlp_search_n()}:",
        "--match-filter",
        match_filter,
        "--no-playlist",
        "--print",
        "id",
        "--print",
        "title",
        "--skip-download",
        "--no-warnings",
        "--quiet",
        query,
    ]
    timeout_s = settings.ytdlp_timeout_s()
    try:
        proc = run_command(cmd, timeout_s)
    except subprocess.TimeoutExpired:
        return {"ok": False, "id": None, "title": None, "error": f"search timed out after {timeout_s}s"}
    except Exception as exc:  # noqa: BLE001
        # Round 2, finding 6: the exception text quotes the argv, which contains
        # the user's query.
        logger.warning("yt-dlp search failed: %s", redact.error(exc))
        return {"ok": False, "id": None, "title": None, "error": "the search could not be run"}

    lines = [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]
    if len(lines) >= 2:
        return {"ok": True, "id": lines[0], "title": lines[1], "error": None}
    if proc.returncode != 0:
        # Finding 6: yt-dlp's stderr echoes the query and the resolved URL back.
        # The shape reaches the log; the caller gets a fixed, speakable reason.
        # Round 3, finding 3: the old call passed the raw stderr with a word
        # allow-list, which is exactly the tokenizing that let an echoed value
        # through. stderr is free text nobody vouched for, so only its LENGTH is
        # loggable -- the return code above is the diagnostic that matters.
        logger.warning(
            "yt-dlp search exited %d, stderr %s",
            proc.returncode,
            redact.text(proc.stderr or ""),
        )
        return {"ok": False, "id": None, "title": None, "error": "the search was refused or returned nothing"}
    return {"ok": False, "id": None, "title": None, "error": "no playable result for that query"}


def download_audio(video_id: str, dest_dir: Path) -> Dict[str, Any]:
    """Download one video's audio as `<video_id>.mp3` into *dest_dir*. Never raises."""
    if not ytdlp_available():
        return {"ok": False, "path": None, "cached": False, "error": "yt-dlp is not installed on this robot"}
    ffmpeg = ffmpeg_exe()
    if not ffmpeg:
        return {"ok": False, "path": None, "cached": False, "error": "ffmpeg is unavailable; cannot make an mp3"}

    out_file = dest_dir / f"{video_id}.mp3"
    if out_file.is_file() and out_file.stat().st_size > 0:
        return {"ok": True, "path": str(out_file), "cached": True, "error": None}

    cmd = _ytdlp_argv() + [
        f"https://www.youtube.com/watch?v={video_id}",
        "-x",
        "--audio-format",
        "mp3",
        "--audio-quality",
        "5",
        "--no-playlist",
        "--force-overwrites",
        "--socket-timeout",
        "20",
        "--no-warnings",
        "--quiet",
        "--ffmpeg-location",
        ffmpeg,
        "-o",
        str(dest_dir / f"{video_id}.%(ext)s"),
    ]
    timeout_s = settings.ytdlp_download_timeout_s()
    try:
        proc = run_command(cmd, timeout_s)
    except subprocess.TimeoutExpired:
        return {"ok": False, "path": None, "cached": False, "error": f"download timed out after {timeout_s}s"}
    except Exception as exc:  # noqa: BLE001
        # Round 2, finding 6: the argv in the message carries the video URL and
        # the instance-directory output template.
        logger.warning("yt-dlp download failed: %s", redact.error(exc))
        return {"ok": False, "path": None, "cached": False, "error": "the download could not be run"}

    if not out_file.is_file() or out_file.stat().st_size == 0:
        # Finding 6: the tail of yt-dlp's output names the video and the path it
        # tried to write. Log the shape, return a fixed reason. Round 3,
        # finding 3: a length, never a token lifted out of the text.
        logger.warning(
            "yt-dlp produced no audio (rc=%d), output %s",
            proc.returncode,
            redact.text(proc.stderr or proc.stdout or ""),
        )
        return {"ok": False, "path": None, "cached": False, "error": "no audio could be produced for that track"}
    return {"ok": True, "path": str(out_file), "cached": False, "error": None}


def cut_from(source: Path, offset_s: float, dest: Path) -> bool:
    """Stream-copy *source* from *offset_s* into *dest*. Returns success."""
    ffmpeg = ffmpeg_exe()
    if not ffmpeg or not Path(source).is_file():
        return False
    cmd = [
        ffmpeg,
        "-y",
        "-loglevel",
        "error",
        "-ss",
        f"{offset_s:.3f}",
        "-i",
        str(source),
        "-c",
        "copy",
        str(dest),
    ]
    try:
        proc = run_command(cmd, 30)
    except Exception as exc:  # noqa: BLE001
        # Round 2, finding 6: the argv names the cached track's path.
        logger.warning("ffmpeg seek failed: %s", redact.error(exc))
        return False
    if proc.returncode != 0 or not Path(dest).is_file() or Path(dest).stat().st_size == 0:
        logger.warning("ffmpeg seek produced nothing (rc=%s)", proc.returncode)
        return False
    return True
```

- [ ] **Step 4: Implement `hanova/music_player.py`**

Create `reachy_companion/src/reachy_companion/hanova/music_player.py`:

```python
"""Robot-speaker music session, with barge-in ducking (D-018, R2/R7).

Music plays through the daemon's own sound path and *not* through the realtime
audio queue, so it mixes with Reachy's voice at the GStreamer sink instead of
competing for the same buffer.

**Both directions go straight to the daemon REST API.** The media API is exactly
`POST /api/media/play_sound {file}` and `POST /api/media/stop_sound`
(`daemon/app/routers/media.py:77-115`). The client `MediaManager` does not expose
`stop_sound` at all, and its `play_sound` **swallows a non-2xx**, so using it
would let this module report `playing` when nothing played (review round 1,
finding 2). We call both endpoints ourselves and check the status code.

**Pause is synthesised, because the daemon has none.** `POST /api/volume/set`
changes *system* volume and plays a test beep, so it cannot duck one stream. So
`pause_for_speech()` stops the sound and banks the elapsed offset, and
`resume_after_speech()` re-cuts the cached mp3 from that offset with the bundled
ffmpeg and plays the tail.

**Every transition is serialized and generation-checked** (finding 2). Each
public coroutine takes a generation number *before* it queues on the lock, so a
later request always supersedes an earlier one even while the earlier one is
mid-I/O; the number is re-checked after every await, and a superseded transition
undoes what it started (it stops the sound it just began) rather than leaving the
speaker running or overwriting newer state.
"""

from __future__ import annotations

import time
import asyncio
import logging
import threading
from typing import Any, Dict
from pathlib import Path
from dataclasses import dataclass

import httpx

from reachy_companion.hanova import ytdlp


logger = logging.getLogger(__name__)

_DAEMON_FALLBACK_URL = "http://127.0.0.1:8000"
_DAEMON_TIMEOUT_S = 5.0
# Below this many seconds, restarting the track is indistinguishable from
# resuming it, and skips one ffmpeg round trip.
_MIN_RESUME_OFFSET_S = 0.5


@dataclass
class MusicState:
    """What is on the robot's speaker right now."""

    video_id: str
    title: str
    source_path: Path
    started_at: float
    offset_s: float
    paused: bool
    generation: int


def daemon_base_url(deps: Any) -> str:
    """Return the daemon's HTTP base URL, falling back to localhost:8000."""
    robot = getattr(deps, "reachy_mini", None)
    url = getattr(robot, "_daemon_http_url", "") if robot is not None else ""
    return str(url).rstrip("/") or _DAEMON_FALLBACK_URL


async def _daemon_post(deps: Any, path: str, payload: Dict[str, Any] | None = None) -> bool:
    """POST one daemon media command and return whether it was acknowledged."""
    url = f"{daemon_base_url(deps)}{path}"
    try:
        async with httpx.AsyncClient(timeout=_DAEMON_TIMEOUT_S) as client:
            response = await client.post(url, json=payload)
    except httpx.HTTPError:
        logger.warning("Daemon media command %s failed at the transport layer.", path)
        return False
    acknowledged = 200 <= response.status_code < 300
    if not acknowledged:
        logger.warning("Daemon media command %s returned HTTP %d.", path, response.status_code)
    return acknowledged


async def daemon_stop_sound(deps: Any) -> bool:
    """Ask the daemon to stop the current sound file. Returns the ack."""
    return await _daemon_post(deps, "/api/media/stop_sound")


async def daemon_play_sound(deps: Any, path: str) -> bool:
    """Ask the daemon to play one file. Returns the ack, not just "we asked"."""
    return await _daemon_post(deps, "/api/media/play_sound", {"file": path})


def _superseded() -> Dict[str, Any]:
    """A transition that a newer request overtook. Not a success, not a fault."""
    return {"ok": False, "status": "superseded"}


class MusicPlayer:
    """Holds the single music session and mediates every speaker transition."""

    def __init__(self) -> None:
        """Create an idle player."""
        self._lock = asyncio.Lock()          # serialises whole transitions
        self._state_lock = threading.Lock()  # guards the snapshot itself
        self._state: MusicState | None = None
        self._generation = 0

    # --- state access ------------------------------------------------------
    def current(self) -> MusicState | None:
        """Return the active session, or None when nothing is playing."""
        with self._state_lock:
            return self._state

    def generation(self) -> int:
        """Return the current transition generation. Used by tests and logs."""
        with self._state_lock:
            return self._generation

    def reset(self) -> None:
        """Forget the session without touching the speaker. **Tests only.**

        Round 2, finding 8: this neither advances the generation nor stops the
        daemon, so a `play` or `resume` still in flight will happily finish and
        write its state back afterwards. At a session boundary that resurrects
        audio across a reconnect -- use `invalidate()` there.
        """
        with self._state_lock:
            self._state = None

    def invalidate(self) -> int:
        """Supersede every in-flight transition and drop the state (finding 8).

        Bumping the generation *inside* the state lock, in the same critical
        section that clears the snapshot, is what makes this safe: any
        transition that wakes up after this point fails its `_is_current` check,
        undoes its own side effect, and returns `superseded` instead of
        repopulating state that belongs to a session which no longer exists.

        Returns the new generation, so a caller can log or assert on it.
        """
        with self._state_lock:
            self._generation += 1
            self._state = None
            return self._generation

    def _next_generation(self) -> int:
        with self._state_lock:
            self._generation += 1
            return self._generation

    def _is_current(self, generation: int) -> bool:
        with self._state_lock:
            return generation == self._generation

    def _store(self, state: MusicState | None) -> None:
        with self._state_lock:
            self._state = state

    # --- transitions -------------------------------------------------------
    async def play(self, deps: Any, *, video_id: str, title: str, source_path: Path) -> Dict[str, Any]:
        """Start *source_path* on the robot's speaker, superseding anything playing."""
        generation = self._next_generation()
        async with self._lock:
            if not self._is_current(generation):
                return _superseded()

            await daemon_stop_sound(deps)
            if not self._is_current(generation):
                return _superseded()

            started = await daemon_play_sound(deps, str(source_path))
            if not self._is_current(generation):
                # A newer request arrived while the daemon was starting this
                # file. Undo our own side effect rather than leave it audible.
                await daemon_stop_sound(deps)
                self._store(None)
                return _superseded()

            if not started:
                self._store(None)
                return {
                    "ok": False,
                    "status": "failed",
                    "error": "the robot's speaker did not accept the file",
                }

            self._store(
                MusicState(
                    video_id=video_id,
                    title=title,
                    source_path=Path(source_path),
                    started_at=time.monotonic(),
                    offset_s=0.0,
                    paused=False,
                    generation=generation,
                )
            )
            logger.info("Music playing on the robot speaker (generation %d)", generation)
            return {"ok": True, "status": "playing", "title": title, "video_id": video_id}

    async def stop(self, deps: Any) -> Dict[str, Any]:
        """Stop the music and clear the session. Always safe to call.

        This is the safety lane: it never checks the generation after its await,
        because a stop must win against anything queued behind it. Whatever runs
        next re-establishes its own state.
        """
        self._next_generation()
        async with self._lock:
            state = self.current()
            acknowledged = await daemon_stop_sound(deps)
            if not acknowledged:
                return {
                    "ok": False,
                    "status": "stop_failed",
                    "error": "the robot's speaker did not acknowledge the stop",
                }
            self._store(None)
            if state is None:
                return {"ok": True, "status": "nothing_playing"}
            logger.info("Music stopped")
            return {"ok": True, "status": "stopped", "title": state.title}

    async def pause_for_speech(self, deps: Any) -> Dict[str, Any]:
        """Duck the music because the user started talking (R7). No-op when idle."""
        generation = self._next_generation()
        async with self._lock:
            state = self.current()
            if state is None or state.paused:
                return {"ok": True, "status": "nothing_to_pause"}

            acknowledged = await daemon_stop_sound(deps)
            if not self._is_current(generation):
                return _superseded()
            if not acknowledged:
                # Finding 2: do NOT mark it paused. The music is still playing,
                # and a resume later would then start a second stream.
                logger.warning("Music pause: the daemon refused the stop; state left playing.")
                return {
                    "ok": False,
                    "status": "pause_failed",
                    "error": "the robot's speaker did not acknowledge the stop",
                }

            with self._state_lock:
                live = self._state
                if live is not None:
                    live.offset_s += max(0.0, time.monotonic() - live.started_at)
                    live.paused = True
            logger.debug("Music paused for user speech (generation %d)", generation)
            return {"ok": True, "status": "paused"}

    async def resume_after_speech(self, deps: Any) -> Dict[str, Any]:
        """Resume ducked music once the turn's audio has fully drained. No-op when idle."""
        generation = self._next_generation()
        async with self._lock:
            state = self.current()
            if state is None or not state.paused:
                return {"ok": True, "status": "nothing_to_resume"}
            source = state.source_path
            offset = state.offset_s

            if offset <= _MIN_RESUME_OFFSET_S:
                playback_path = source
            else:
                playback_path = source.with_suffix(".resume.mp3")
                trimmed = await asyncio.to_thread(ytdlp.cut_from, source, offset, playback_path)
                if not self._is_current(generation):
                    return _superseded()
                if not trimmed:
                    logger.warning("Could not resume music; leaving it stopped.")
                    self._store(None)
                    return {"ok": False, "status": "resume_failed"}

            started = await daemon_play_sound(deps, str(playback_path))
            if not self._is_current(generation):
                await daemon_stop_sound(deps)
                self._store(None)
                return _superseded()
            if not started:
                self._store(None)
                return {"ok": False, "status": "resume_failed"}

            with self._state_lock:
                live = self._state
                if live is not None:
                    live.paused = False
                    live.started_at = time.monotonic()
            logger.debug("Music resumed (generation %d)", generation)
            return {"ok": True, "status": "resumed"}


PLAYER = MusicPlayer()
```

- [ ] **Step 5: Implement the two tools**

Create `reachy_companion/src/reachy_companion/tools/play_music.py`:

```python
"""Play music on Reachy's own speaker (D-018, R2). Filename == Tool.name.

Upstream had three music tools -- cast-to-TV, cast-to-puck, and a local one.
Only one survives here, and it always plays on the robot: a desk robot asked for
music is asked for *its* music, and that path needs no Home Assistant, no LAN
URL and no home network at all.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict
from pathlib import Path

from reachy_companion.hanova import ytdlp, redact, settings, media_store
from reachy_companion.hanova.music_player import PLAYER
from reachy_companion.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)

# Whole tracks get downloaded, so cap the length the way upstream did.
_MAX_TRACK_SECONDS = 900


class PlayMusic(Tool):
    """Search for a track and play it on the robot's speaker."""

    name = "play_music"
    description = "Play music on Reachy's own speaker. 用於任何放音樂、播首歌的請求。"
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Song, artist, or mood to search for.",
            },
        },
        "required": ["query"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Resolve the query, cache the audio, and play it on the robot."""
        available, reason = settings.tool_status(self.name)
        if not available:
            return settings.unavailable(reason)

        query = str(kwargs.get("query", "")).strip()
        if not query:
            return {"ok": False, "error": "query is required"}

        # Finding 7: metadata only. What the user asked for is not log material.
        logger.info("Tool call: play_music query=%s", redact.text(query))
        found = await asyncio.to_thread(ytdlp.search, query, _MAX_TRACK_SECONDS)
        if not found["ok"]:
            # yt-dlp's stderr echoes the query back, so it is summarised, never
            # forwarded (finding 7). The model gets a fixed, speakable reason.
            logger.info("play_music search failed: %s", redact.error(found["error"] or ""))
            return {"ok": False, "error": "no playable result for that request"}

        music_dir = media_store.media_dir("music", deps.instance_path)
        downloaded = await asyncio.to_thread(ytdlp.download_audio, found["id"], music_dir)
        if not downloaded["ok"]:
            logger.info("play_music download failed: %s", redact.error(downloaded["error"] or ""))
            return {"ok": False, "error": "the audio could not be fetched right now"}

        result = await PLAYER.play(
            deps,
            video_id=str(found["id"]),
            title=str(found["title"]),
            source_path=Path(str(downloaded["path"])),
        )
        media_store.prune("music", deps.instance_path, settings.music_keep())
        return result
```

Create `reachy_companion/src/reachy_companion/tools/stop_music.py`:

```python
"""Stop the music on Reachy's speaker (D-018, R2). Filename == Tool.name.

This tool must always answer. Upstream's single-threaded server could not stop
music while a download was in flight; here every tool is its own asyncio task
(`huggingface_realtime.py:1011`), so this one is never starved.

It is also the one ported tool with **no prerequisites at all**
(`settings.TOOL_PREREQS["stop_music"] == ()`, review finding 10): a robot that
cannot be silenced by voice is a safety defect, so this tool stays reachable even
when yt-dlp is missing and nothing could have started the music in the first
place. That is a deliberate exemption, not a missing family check.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from reachy_companion.hanova.music_player import PLAYER
from reachy_companion.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


class StopMusic(Tool):
    """Stop whatever music is playing on the robot's speaker."""

    name = "stop_music"
    description = "Stop the music Reachy is playing. 用於停止、關掉、別放了。"
    parameters_schema: Dict[str, Any] = {"type": "object", "properties": {}, "required": []}

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Stop the robot-speaker music session."""
        logger.info("Tool call: stop_music")
        return await PLAYER.stop(deps)
```

- [ ] **Step 6: Enable both tools in the locked profile**

In `reachy_companion/profiles/_reachy_companion_locked_profile/profile.md`, add two entries to `default_tools`, immediately after `"home_control",`:

```toml
  "home_control",
  "play_music",
  "stop_music",
```

- [ ] **Step 7: Run the tests to verify they pass**

```powershell
cd C:\Project\Reachy-mini\reachy_companion
..\.venv\Scripts\python.exe -m pytest tests/test_hanova_ytdlp.py tests/test_hanova_music.py -q
```

Expected: green — **13** test functions from `test_hanova_ytdlp.py` and **25**
from `test_hanova_music.py`, neither parametrised: **38 collected cases**,
including the four interleaving tests. Record the exact number.

- [ ] **Step 8: Run the full gate**

```powershell
cd C:\Project\Reachy-mini\reachy_companion
..\.venv\Scripts\python.exe -m pytest -q
..\.venv\Scripts\python.exe -m ruff check src tests
..\.venv\Scripts\python.exe -m ruff format --check src tests
..\.venv\Scripts\python.exe -m mypy
```

Expected: pytest green (30 documented skips), ruff clean, mypy `Success: no issues found`.

- [ ] **Step 9: Commit**

```bash
cd /c/Project/Reachy-mini
git rev-parse --abbrev-ref HEAD   # must print feat/ha-nova-port (Task 0)
git add reachy_companion/src/reachy_companion/hanova/ytdlp.py \
        reachy_companion/src/reachy_companion/hanova/music_player.py \
        reachy_companion/src/reachy_companion/tools/play_music.py \
        reachy_companion/src/reachy_companion/tools/stop_music.py \
        reachy_companion/profiles/_reachy_companion_locked_profile/profile.md \
        reachy_companion/tests/test_hanova_ytdlp.py \
        reachy_companion/tests/test_hanova_music.py
git commit -m "feat(hanova): robot-speaker music with play_music and stop_music"
```

---

### Task 5: Wire music ducking and session lifecycle into the realtime loop

Completes R7 and closes review round 1 findings 1 (resume only when the turn's
audio has really drained, and never block the event receiver on the daemon) and
3 (the confirmation gate's session epoch must actually be wired).

**What finding 1 changed.** The first draft resumed the music on
`response.output_audio.done` *and* on `response.done`. But the real handler says
in its own comment that `response.done` **"Doesn't mean the audio is done
playing"** — at that instant there is still queued PCM in `console.play_loop`'s
buffer and more in the device buffer, and on a tool-calling turn a *second*
response (the one that speaks the tool result) has not even been created yet. So
the music came back over the top of Reachy's own voice. The fix has three parts:

1. `console.play_loop` reports what it actually did with the audio — an
   `audio_drain` tracker that knows how many samples were handed to the sink,
   when the estimated device buffer runs dry, and when a barge-in cleared it.
2. The handler tracks the **turn phase**: a response is in flight, a tool call is
   in flight, or the turn is genuinely over. A resume is only scheduled from the
   third state.
3. The resume runs in its own task that first awaits the drain signal, then
   re-checks that no new response or tool call started while it waited.

**Review round 2, finding 1 — that version of the drain tracker was cosmetic,
and one path never resumed at all.** Two defects, both fatal to the behaviour the
whole task exists for:

*The tracker started "drained" and only ever learned about audio it had already
played.* `_QUEUE_EMPTY` began `True` and `_DRAINED_AT` began `0.0`, and the only
thing that changed them was `play_loop` **dequeuing**. So on a normal turn,
`response.output_audio.done` arrived while the PCM was still sitting in the
queue, `wait_drained()` looked at a tracker that had never been told anything,
returned `True` immediately, and the music came back on top of the reply — the
exact bug finding 1 was raised about, reproduced by the fix for it. The tracker
now works **per response generation** and marks audio **pending before it is
enqueued**: `begin_response()` opens a generation that is pending by definition,
`note_enqueued()` is called by the event receiver *before* each delta goes into
the playback queue, `note_chunk()` is called by `play_loop` when the samples
actually reach the sink, and `close_response()` (from `output_audio.done` /
`response.done`) says no more audio is coming. `wait_drained(generation)` is
`closed AND nothing outstanding AND the local queue empty AND the device-buffer
estimate expired` — four conditions, none of which is true by default.

*A tool batch that needs no follow-up response left the music paused forever.*
`on_assistant_turn_ended` refused to schedule while a tool was in flight, which
is right, and `on_tool_call_finished()` scheduled nothing, which is right when a
follow-up response is coming — and wrong when it is not. A batch whose last tool
returns with `needs_response=False` produces no further `response.created`, so
nothing ever re-scheduled the resume and the track stayed down for the rest of
the conversation. `on_tool_call_finished(needs_response)` now takes that flag and
**explicitly closes the turn and schedules the resume** when the last tool of the
batch wants no reply.

And `on_user_speech_started` no longer awaits anything inside the receive loop:
it *schedules* the pause. Awaiting a request with a five-second timeout inside
the realtime event receiver stalls every other event behind it — including the
next `speech_started`.

**Review round 2, finding 8 — the session boundary must invalidate, not forget.**
`on_session_started()` called `PLAYER.reset()`, which drops the state snapshot
and does nothing else: it does not advance the generation and it does not stop
the daemon. A `play` or `resume` that was mid-I/O when the backend reconnected
therefore finished normally *after* the reset and wrote its state back, so audio
from the previous conversation survived into the new one. Both boundaries now
call `PLAYER.invalidate()` (which bumps the generation under the state lock, so
every in-flight transition sees itself superseded) **and** `await PLAYER.stop()`
(which actually silences the daemon and checks the acknowledgement). And the
cleanup is attached to `_run_realtime_session()`'s own `finally`, not only to
handler shutdown: a connection that drops without the handler shutting down is
the common case, and it was leaving both the speaker and the confirmation gate
live.

**Review round 3, finding 1 — a bounded drain wait is a bounded music outage.**
`_resume_when_drained()` waited 12 seconds and then gave up *permanently*: it
logged "the turn's audio never reported drained" and returned without resuming.
A long queued response, a slow sink, or any device-buffer estimate that outlives
the timeout therefore left the track paused for the rest of the conversation —
the same class of bug as round 2's `needs_response=False` path, reached by a
different route. The 12 seconds is now a **diagnostic interval, not a deadline**:
the resume waits until one of three things is true — the generation drains, the
realtime **session** it belongs to ends, or a **newer response** supersedes it —
and every interval that passes without one of them emits one INFO line carrying
the elapsed seconds and the outstanding audio. Nothing else ends the wait, and a
cancellation (which is what a new response or a session teardown actually
triggers) still returns immediately.

**Review round 3, finding 2 — the session boundary needs an identity, not just
an ordering.** The hooks kept the session's `deps` in a module-global `_DEPS` and
shut down unconditionally. The handler's own restart path can open a replacement
connection before the previous connection's `finally` has run, and that late,
**stale** `on_session_shutdown` then tore down the *new* session: it cleared the
new drain state, ended the new gate epoch, cleared the new NAS trip session and
nulled `_DEPS` — so the fresh conversation started with no captured deps and a
gate that had already been closed. `on_session_started()` now **mints and returns
a session token**, `on_session_shutdown(deps, token)` **ignores any token that is
not the live one**, and the handler carries the token from the connection that
opened the session to the two places that close it. Cleanup is still idempotent;
it is now also *attributable*.

The realtime loop already has the exact hook points:
`input_audio_buffer.speech_started` (`huggingface_realtime.py:874`),
`response.created`, `response.output_audio.delta`,
`response.output_audio.done` (`:887`), `response.done` (`:910`), the
`BackgroundToolManager.start_tool()` dispatch (`:1011`), and
`HuggingFaceRealtimeHandler.shutdown` (`:1114`).

**Files:**
- Create: `reachy_companion/src/reachy_companion/hanova/audio_drain.py`
- Create: `reachy_companion/src/reachy_companion/hanova/music_hooks.py`
- Modify: `reachy_companion/src/reachy_companion/console.py` (three one-line notifications inside `play_loop`)
- Modify: `reachy_companion/src/reachy_companion/huggingface_realtime.py` (one import, one `__init__` attribute, nine insertions — round 3, finding 2 added the session-token attribute and threads it through both cleanup call sites)
- Test: `reachy_companion/tests/test_hanova_music_barge_in.py`

**Interfaces:**
- Consumes: `hanova.music_player.PLAYER` (Task 4); `hanova.confirm.GATE` (Task 2); the existing `HuggingFaceRealtimeHandler._run_realtime_session` and `.shutdown` (unchanged signatures).
- Produces (`hanova.audio_drain`, **per response generation** per round 2 finding 1):
  - `begin_response() -> int` — a new assistant response exists. Mints and returns a generation that is **pending by definition**: `wait_drained` on it cannot succeed until `close_response` is called. This is what "marked pending before enqueue" means — the pending flag predates the first byte
  - `note_enqueued(generation: int, sample_count: int, sample_rate: int) -> None` — called by the **event receiver**, immediately *before* the delta's PCM is appended to the playback queue. This is the accounting the round-1 version lacked entirely
  - `note_chunk(sample_count: int, sample_rate: int) -> None` — called by `play_loop` per chunk actually handed to the sink; retires that much outstanding audio and advances the device-buffer estimate
  - `note_queue_empty() -> None` — called by `play_loop` when its own queue is empty
  - `close_response(generation: int) -> None` — no further audio will be enqueued for this generation (`response.output_audio.done` / `response.done`)
  - `note_cleared() -> None` — barge-in flushed the queue: outstanding audio will never play, every open generation is closed, and the device estimate is discarded
  - `wait_drained(generation: int, timeout_s: float) -> Awaitable[bool]` — True only when that generation is **closed**, nothing it enqueued is still outstanding, the local queue is empty, **and** the device-buffer estimate has expired. False on timeout. Round 3, finding 1: `timeout_s` is now a *polling interval* to its only production caller, which loops instead of giving up
  - `outstanding_s() -> float`, `reset() -> None` — introspection for tests and a full reset
- Produces (`hanova.music_hooks`, the only names `huggingface_realtime.py` imports):
  - `on_session_started(deps: Any) -> Awaitable[int]` — new confirmation epoch, **invalidated and stopped** player, cleared audio and trip state (round 2, finding 8), **and it mints and returns the session token** every later cleanup must present (round 3, finding 2)
  - `on_user_speech_started(deps: Any) -> None` — **synchronous**; schedules the pause and returns
  - `on_response_created() -> None` — **synchronous**; opens a drain generation and cancels any pending resume
  - `on_response_audio(sample_count: int, sample_rate: int) -> None` — **synchronous**; the receiver's pre-enqueue notification
  - `on_tool_call_started() -> None` — **synchronous**
  - `on_tool_call_finished(needs_response: bool) -> None` — **synchronous**. When this is the last tool of the batch **and** `needs_response` is False, no follow-up response will ever be created, so this closes the turn and schedules the resume itself (round 2, finding 1)
  - `on_assistant_turn_ended(deps: Any) -> None` — **synchronous**; closes the current drain generation and schedules the drain-then-resume task
  - `on_session_shutdown(deps: Any, token: int) -> Awaitable[None]` — invalidates and stops the speaker, ends the confirmation session, clears the trip session, cancels any pending resume — **but only when *token* is the live session's** (round 3, finding 2). A cleanup arriving from an older connection is logged at DEBUG and does nothing, so an overlapping reconnect cannot be torn down by the connection it replaced
  - All of them are idempotent and cost nothing when no music is playing.
  - **`on_tool_call_finished` needs a deps-free way to schedule.** It is called from the tool-completion path, which has `self.deps` in scope in the handler; the hook signature deliberately does not take it, so `music_hooks` keeps the deps of the **current session**, captured in `on_session_started`, in a module-level `_DEPS`. That is the same object every other hook is handed, and it is cleared on shutdown.

- [ ] **Step 1: Write the failing test**

Create `reachy_companion/tests/test_hanova_music_barge_in.py`:

```python
"""Music must duck for user speech and come back only when the turn is really
over (D-018, R7, review round 1 findings 1 and 3)."""

import types
import asyncio
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

import reachy_companion.huggingface_realtime as hf_mod
from reachy_companion.hanova import audio_drain, music_hooks
from reachy_companion.hanova.confirm import GATE
from reachy_companion.hanova.music_player import PLAYER
from reachy_companion.tools.core_tools import ToolDependencies
from reachy_companion.huggingface_realtime import HuggingFaceRealtimeHandler

# `tests/` has no __init__.py, so pytest's default prepend import mode puts the
# directory itself on sys.path -- import the sibling module by bare name.
from test_huggingface_realtime import _FakeEvent, _make_fake_realtime_client


HF_TEST_VOICE = "cedar"


class _Ok:
    status_code = 200


def _deps(tmp_path=None):
    robot = types.SimpleNamespace(_daemon_http_url="http://127.0.0.1:8000")
    return types.SimpleNamespace(reachy_mini=robot, instance_path=tmp_path)


async def _until(predicate, poll_s: float = 0.005):
    """Await a condition a detached hook task will eventually make true.

    The hooks are deliberately fire-and-forget, so a fixed sleep is either flaky
    or slow. This polls instead, and the caller wraps it in `asyncio.wait_for`.
    """
    while not predicate():
        await asyncio.sleep(poll_s)


@pytest.fixture(autouse=True)
def quiet_session(monkeypatch):
    """Neutralise everything a realtime session touches except our hooks."""
    monkeypatch.setattr(hf_mod, "get_session_instructions", lambda _instance_path=None: "test")
    monkeypatch.setattr(hf_mod, "get_session_voice", lambda default=HF_TEST_VOICE: default)
    monkeypatch.setattr(hf_mod, "get_tool_specs", lambda: [])
    monkeypatch.setattr("reachy_companion.hanova.settings._music_wheels_ready", lambda: (True, ""))
    PLAYER.reset()
    GATE.reset()
    audio_drain.reset()
    music_hooks.reset_for_tests()
    yield
    PLAYER.reset()
    GATE.reset()
    audio_drain.reset()
    music_hooks.reset_for_tests()


@pytest.fixture
def ok_daemon(monkeypatch):
    """A daemon that acknowledges everything, recording what it was told."""
    calls: list[str] = []

    async def ok_post(self, url, json=None, **kwargs):
        calls.append(url)
        return _Ok()

    monkeypatch.setattr(httpx.AsyncClient, "post", ok_post)
    return calls


def _handler_with(events: tuple[_FakeEvent, ...]) -> HuggingFaceRealtimeHandler:
    handler = HuggingFaceRealtimeHandler(ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock()))
    handler.client = _make_fake_realtime_client(events=events)
    return handler


# --- the drain tracker (finding 1, rebuilt in round 2) --------------------
@pytest.mark.asyncio
async def test_an_open_response_is_never_drained_even_with_no_audio_yet():
    """Round 2, finding 1: pending is the *default*, not something audio sets.

    The old tracker began "empty" and only learned about audio it had already
    played, so `wait_drained()` said yes before a single byte left the queue.
    """
    generation = audio_drain.begin_response()
    assert await audio_drain.wait_drained(generation, timeout_s=0.05) is False


@pytest.mark.asyncio
async def test_drain_waits_for_the_local_queue_and_the_device_buffer():
    """`response.done` is not "the audio finished"; this is what finishing means."""
    generation = audio_drain.begin_response()
    audio_drain.note_enqueued(generation, sample_count=24000, sample_rate=24000)  # 1 s queued
    audio_drain.close_response(generation)
    assert await audio_drain.wait_drained(generation, timeout_s=0.05) is False, (
        "closed, but a second of audio is still sitting in the queue"
    )

    audio_drain.note_chunk(sample_count=24000, sample_rate=24000)  # handed to the sink
    assert await audio_drain.wait_drained(generation, timeout_s=0.05) is False, (
        "handed to the sink is not the same as heard; the device buffer holds it"
    )

    audio_drain.note_queue_empty()
    assert await audio_drain.wait_drained(generation, timeout_s=0.05) is False, (
        "an empty queue with audio still in the device buffer is not drained"
    )

    audio_drain.note_cleared()
    assert await audio_drain.wait_drained(generation, timeout_s=0.5) is True


@pytest.mark.asyncio
async def test_drain_is_immediately_true_for_a_closed_response_with_no_audio():
    """A text-only or tool-only turn has no audio to wait for."""
    generation = audio_drain.begin_response()
    audio_drain.close_response(generation)
    assert await audio_drain.wait_drained(generation, timeout_s=0.5) is True


@pytest.mark.asyncio
async def test_enqueued_audio_is_outstanding_until_it_is_played():
    """The accounting the round-1 tracker did not have at all."""
    generation = audio_drain.begin_response()
    audio_drain.note_enqueued(generation, sample_count=48000, sample_rate=24000)  # 2 s
    assert audio_drain.outstanding_s() == pytest.approx(2.0, abs=0.01)
    audio_drain.note_chunk(sample_count=24000, sample_rate=24000)
    assert audio_drain.outstanding_s() == pytest.approx(1.0, abs=0.01)
    audio_drain.note_chunk(sample_count=24000, sample_rate=24000)
    assert audio_drain.outstanding_s() == pytest.approx(0.0, abs=0.01)


@pytest.mark.asyncio
async def test_barge_in_clear_discards_the_pending_device_buffer():
    """When the queue is flushed the estimate must be dropped, not waited out."""
    generation = audio_drain.begin_response()
    audio_drain.note_enqueued(generation, sample_count=24000 * 30, sample_rate=24000)  # 30 s
    audio_drain.note_chunk(sample_count=24000 * 30, sample_rate=24000)
    audio_drain.note_cleared()
    assert await audio_drain.wait_drained(generation, timeout_s=0.5) is True
    assert audio_drain.outstanding_s() == 0.0


@pytest.mark.asyncio
async def test_a_stale_generation_never_blocks_a_newer_one():
    """A superseded turn must not park the next turn's resume forever."""
    stale = audio_drain.begin_response()
    audio_drain.note_enqueued(stale, sample_count=24000, sample_rate=24000)
    fresh = audio_drain.begin_response()
    audio_drain.close_response(fresh)
    # The barge-in that produced the new response also flushed the queue.
    audio_drain.note_cleared()
    assert await audio_drain.wait_drained(fresh, timeout_s=0.5) is True


# --- resume timing (finding 1) --------------------------------------------
@pytest.mark.asyncio
async def test_resume_waits_for_the_drain_signal(ok_daemon, tmp_path, monkeypatch):
    """The whole point: the track comes back after Reachy stops, not during."""
    deps = _deps(tmp_path)
    track = tmp_path / "abc.mp3"
    track.write_bytes(b"ID3")
    await PLAYER.play(deps, video_id="abc", title="A Song", source_path=track)
    await PLAYER.pause_for_speech(deps)

    resumed = asyncio.Event()

    async def record_resume(_deps):
        resumed.set()
        return {"ok": True, "status": "resumed"}

    monkeypatch.setattr(PLAYER, "resume_after_speech", record_resume)

    music_hooks.on_response_created()
    music_hooks.on_response_audio(sample_count=24000, sample_rate=24000)
    music_hooks.on_assistant_turn_ended(deps)
    await asyncio.sleep(0.02)
    assert not resumed.is_set(), "resume must not fire while audio is still queued"

    audio_drain.note_chunk(sample_count=24000, sample_rate=24000)
    audio_drain.note_queue_empty()
    audio_drain.note_cleared()
    await asyncio.wait_for(resumed.wait(), timeout=1.0)


@pytest.mark.asyncio
async def test_audio_queued_before_response_done_still_blocks_the_resume(ok_daemon, tmp_path, monkeypatch):
    """**Round 2, finding 1, mandatory case: delayed playback after response.done.**

    This is the exact failure the round-1 "fix" still had. Every delta has been
    received and `response.done` has fired, but `play_loop` has not dequeued a
    single sample yet -- the tracker must be pending, not empty. The resume may
    only fire once the audio has actually been played out.
    """
    deps = _deps(tmp_path)
    track = tmp_path / "abc.mp3"
    track.write_bytes(b"ID3")
    await PLAYER.play(deps, video_id="abc", title="A Song", source_path=track)
    await PLAYER.pause_for_speech(deps)

    resumed = asyncio.Event()

    async def record_resume(_deps):
        resumed.set()
        return {"ok": True, "status": "resumed"}

    monkeypatch.setattr(PLAYER, "resume_after_speech", record_resume)

    music_hooks.on_response_created()
    for _ in range(10):  # ten 100 ms deltas, all enqueued, none played
        music_hooks.on_response_audio(sample_count=2400, sample_rate=24000)
    music_hooks.on_assistant_turn_ended(deps)      # response.done arrives here

    await asyncio.sleep(0.15)
    assert not resumed.is_set(), "response.done with a full queue is not a drained turn"
    assert audio_drain.outstanding_s() == pytest.approx(1.0, abs=0.05)

    # play_loop now catches up, one chunk at a time.
    for _ in range(10):
        audio_drain.note_chunk(sample_count=2400, sample_rate=24000)
        await asyncio.sleep(0)
    assert not resumed.is_set(), "the device buffer still holds the tail"

    audio_drain.note_queue_empty()
    audio_drain.note_cleared()
    await asyncio.wait_for(resumed.wait(), timeout=1.0)


@pytest.mark.asyncio
async def test_outstanding_audio_past_the_report_interval_still_resumes(
    ok_daemon, tmp_path, monkeypatch
):
    """**Round 3, finding 1, mandatory case: more than 12 s of outstanding audio.**

    The old `_resume_when_drained()` gave `wait_drained` a 12-second timeout and
    returned for good when it expired, so a long reply -- or a sink that fell
    behind -- left the music paused for the rest of the conversation. The
    interval is now diagnostic: the waiter logs and keeps waiting.

    The interval is shrunk here so the test spends milliseconds rather than
    minutes; what is NOT shrunk is the audio, which is a real 30 seconds of
    outstanding PCM -- comfortably past the 12-second mark that used to be fatal.
    """
    monkeypatch.setattr(music_hooks, "_DRAIN_REPORT_EVERY_S", 0.05)
    deps = _deps(tmp_path)
    track = tmp_path / "abc.mp3"
    track.write_bytes(b"ID3")
    await PLAYER.play(deps, video_id="abc", title="A Song", source_path=track)
    await PLAYER.pause_for_speech(deps)

    resumed = asyncio.Event()

    async def record_resume(_deps):
        resumed.set()
        return {"ok": True, "status": "resumed"}

    monkeypatch.setattr(PLAYER, "resume_after_speech", record_resume)

    music_hooks.on_response_created()
    music_hooks.on_response_audio(sample_count=24000 * 30, sample_rate=24000)   # 30 s
    music_hooks.on_assistant_turn_ended(deps)

    # Six report intervals go by with the audio still outstanding. The old code
    # had given up permanently by this point.
    await asyncio.sleep(0.3)
    assert not resumed.is_set()
    assert audio_drain.outstanding_s() == pytest.approx(30.0, abs=0.05)

    audio_drain.note_chunk(sample_count=24000 * 30, sample_rate=24000)
    audio_drain.note_queue_empty()
    audio_drain.note_cleared()
    await asyncio.wait_for(resumed.wait(), timeout=1.0)


@pytest.mark.asyncio
async def test_a_tool_call_defers_the_resume_to_the_follow_up_turn(ok_daemon, tmp_path, monkeypatch):
    """A tool turn is followed by a second, speaking response; do not resume between."""
    deps = _deps(tmp_path)
    calls: list[str] = []

    async def record_resume(_deps):
        calls.append("resume")
        return {"ok": True, "status": "resumed"}

    monkeypatch.setattr(PLAYER, "resume_after_speech", record_resume)

    music_hooks.on_response_created()
    music_hooks.on_tool_call_started()
    music_hooks.on_assistant_turn_ended(deps)   # response.done for the tool turn
    await asyncio.sleep(0.05)
    assert calls == [], "a tool call still in flight means the turn is not over"

    music_hooks.on_tool_call_finished(needs_response=True)
    await asyncio.sleep(0.05)
    assert calls == [], "a follow-up response is coming; do not resume in the gap"

    music_hooks.on_response_created()           # the follow-up response
    music_hooks.on_assistant_turn_ended(deps)
    await asyncio.sleep(0.05)
    assert calls == ["resume"]


@pytest.mark.asyncio
async def test_a_final_tool_batch_with_no_follow_up_resumes_the_music(ok_daemon, tmp_path, monkeypatch):
    """**Round 2, finding 1, mandatory case: needs_response=False.**

    A tool batch that wants no reply produces no further `response.created`, so
    nothing was left to re-schedule the resume and the music stayed paused for
    the rest of the conversation. This path must close the turn itself.
    """
    deps = _deps(tmp_path)
    await music_hooks.on_session_started(deps)   # this is what supplies _DEPS
    calls: list[str] = []

    async def record_resume(_deps):
        calls.append("resume")
        return {"ok": True, "status": "resumed"}

    monkeypatch.setattr(PLAYER, "resume_after_speech", record_resume)

    music_hooks.on_response_created()
    music_hooks.on_tool_call_started()
    music_hooks.on_assistant_turn_ended(deps)    # response.done for the tool turn
    await asyncio.sleep(0.02)
    assert calls == []

    music_hooks.on_tool_call_finished(needs_response=False)
    await asyncio.wait_for(_until(lambda: calls == ["resume"]), timeout=1.0)


@pytest.mark.asyncio
async def test_only_the_last_tool_of_a_batch_closes_the_turn(ok_daemon, tmp_path, monkeypatch):
    """Two tools in one batch: the first finishing is not the batch finishing."""
    deps = _deps(tmp_path)
    await music_hooks.on_session_started(deps)
    calls: list[str] = []

    async def record_resume(_deps):
        calls.append("resume")
        return {"ok": True, "status": "resumed"}

    monkeypatch.setattr(PLAYER, "resume_after_speech", record_resume)

    music_hooks.on_response_created()
    music_hooks.on_tool_call_started()
    music_hooks.on_tool_call_started()
    music_hooks.on_assistant_turn_ended(deps)
    music_hooks.on_tool_call_finished(needs_response=False)
    await asyncio.sleep(0.05)
    assert calls == [], "one tool of two finishing does not end the batch"

    music_hooks.on_tool_call_finished(needs_response=False)
    await asyncio.wait_for(_until(lambda: calls == ["resume"]), timeout=1.0)


@pytest.mark.asyncio
async def test_a_new_response_cancels_a_pending_resume(ok_daemon, tmp_path, monkeypatch):
    """If Reachy starts talking again, the music must stay down."""
    deps = _deps(tmp_path)
    calls: list[str] = []

    async def record_resume(_deps):
        calls.append("resume")
        return {"ok": True, "status": "resumed"}

    monkeypatch.setattr(PLAYER, "resume_after_speech", record_resume)

    music_hooks.on_response_created()
    music_hooks.on_response_audio(sample_count=24000, sample_rate=24000)
    music_hooks.on_assistant_turn_ended(deps)
    music_hooks.on_response_created()
    await asyncio.sleep(0.05)
    audio_drain.note_cleared()
    await asyncio.sleep(0.05)
    assert calls == []


# --- the receiver must never block (finding 1) ----------------------------
@pytest.mark.asyncio
async def test_speech_started_hook_returns_without_awaiting_the_daemon(monkeypatch):
    """A five-second daemon timeout inside the event receiver stalls every event."""
    entered = asyncio.Event()
    release = asyncio.Event()

    async def slow_pause(_deps):
        entered.set()
        await release.wait()
        return {"ok": True, "status": "paused"}

    monkeypatch.setattr(PLAYER, "pause_for_speech", slow_pause)
    deps = _deps()

    music_hooks.on_user_speech_started(deps)  # note: NOT awaited -- it is sync
    await asyncio.sleep(0)
    assert entered.is_set(), "the pause must have been scheduled"
    release.set()
    await music_hooks.drain_pending_for_tests()


@pytest.mark.asyncio
async def test_user_speech_pauses_the_music(monkeypatch: Any) -> None:
    """Barge-in must duck the speaker, not talk over it."""
    calls: list[str] = []

    monkeypatch.setattr(hf_mod, "on_user_speech_started", lambda _deps: calls.append("pause"))
    monkeypatch.setattr(hf_mod, "on_assistant_turn_ended", lambda _deps: None)
    monkeypatch.setattr(hf_mod, "on_response_created", lambda: None)

    handler = _handler_with((_FakeEvent("input_audio_buffer.speech_started"),))
    await handler._run_realtime_session()
    assert calls == ["pause"]


@pytest.mark.asyncio
async def test_finished_audio_ends_the_turn(monkeypatch: Any) -> None:
    """When Reachy stops talking, the end-of-turn hook fires."""
    calls: list[str] = []

    monkeypatch.setattr(hf_mod, "on_user_speech_started", lambda _deps: None)
    monkeypatch.setattr(hf_mod, "on_assistant_turn_ended", lambda _deps: calls.append("turn_end"))
    monkeypatch.setattr(hf_mod, "on_response_created", lambda: None)

    handler = _handler_with((_FakeEvent("response.output_audio.done"),))
    await handler._run_realtime_session()
    assert calls == ["turn_end"]


@pytest.mark.asyncio
async def test_text_only_turn_also_ends_the_turn(monkeypatch: Any) -> None:
    """Tool-only and text-only responses emit no output_audio.done."""
    calls: list[str] = []

    monkeypatch.setattr(hf_mod, "on_user_speech_started", lambda _deps: None)
    monkeypatch.setattr(hf_mod, "on_assistant_turn_ended", lambda _deps: calls.append("turn_end"))
    monkeypatch.setattr(hf_mod, "on_response_created", lambda: None)

    handler = _handler_with((_FakeEvent("response.done"),))
    await handler._run_realtime_session()
    assert calls == ["turn_end"]


@pytest.mark.asyncio
async def test_the_receiver_reports_audio_before_it_is_queued(monkeypatch: Any) -> None:
    """Round 2, finding 1: the pre-enqueue notification is a wiring requirement.

    If the receiver does not call this, the drain tracker never learns that
    audio exists until `play_loop` dequeues it -- which is precisely the race
    that made the round-1 fix cosmetic.
    """
    seen: list[int] = []

    monkeypatch.setattr(hf_mod, "on_user_speech_started", lambda _deps: None)
    monkeypatch.setattr(hf_mod, "on_assistant_turn_ended", lambda _deps: None)
    monkeypatch.setattr(hf_mod, "on_response_created", lambda: None)
    monkeypatch.setattr(
        hf_mod, "on_response_audio", lambda sample_count, sample_rate: seen.append(sample_count)
    )

    handler = _handler_with((_FakeEvent("response.output_audio.delta", delta=b"\x00\x00" * 240),))
    await handler._run_realtime_session()
    assert seen == [240], "each audio delta must be reported before it is enqueued"


# --- session lifecycle (finding 3, round 2 finding 8) ---------------------
@pytest.mark.asyncio
async def test_session_start_opens_a_new_confirmation_epoch(ok_daemon):
    """Finding 3: a reconnect must not inherit the previous conversation's gate."""
    GATE.begin_session()
    GATE.arm("email_send", "send mail", {"to": "a@example.com"})
    stale_epoch = GATE.epoch()

    await music_hooks.on_session_started(_deps())

    assert GATE.epoch() != stale_epoch
    assert GATE.claim("email_send") is None


@pytest.mark.asyncio
async def test_session_start_invalidates_and_silences_the_player(ok_daemon, tmp_path):
    """Round 2, finding 8: `reset()` forgot the state and left the speaker on.

    A reconnect must actually stop the daemon and advance the generation, or
    audio from the previous conversation keeps playing into the new one.
    """
    deps = _deps(tmp_path)
    track = tmp_path / "abc.mp3"
    track.write_bytes(b"ID3")
    await PLAYER.play(deps, video_id="abc", title="A Song", source_path=track)
    generation_before = PLAYER.generation()
    ok_daemon.clear()

    await music_hooks.on_session_started(deps)

    assert PLAYER.current() is None
    assert PLAYER.generation() > generation_before, "the generation must advance, not just the state"
    assert any(url.endswith("/api/media/stop_sound") for url in ok_daemon)


@pytest.mark.asyncio
async def test_a_transition_in_flight_across_a_session_boundary_cannot_come_back(
    ok_daemon, tmp_path, monkeypatch
):
    """Round 2, finding 8, stated as the failure it prevents.

    A `play` that was mid-I/O when the backend reconnected used to finish
    afterwards and write its state back over a session that no longer exists.
    """
    deps = _deps(tmp_path)
    track = tmp_path / "abc.mp3"
    track.write_bytes(b"ID3")

    started = asyncio.Event()
    release = asyncio.Event()
    real_post = httpx.AsyncClient.post

    async def slow_post(self, url, json=None, **kwargs):
        if url.endswith("/api/media/play_sound"):
            started.set()
            await release.wait()
        return await real_post(self, url, json=json, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "post", slow_post)
    play_task = asyncio.create_task(
        PLAYER.play(deps, video_id="abc", title="A Song", source_path=track)
    )
    await asyncio.wait_for(started.wait(), timeout=1.0)

    await music_hooks.on_session_started(deps)   # the reconnect happens here
    release.set()
    result = await play_task

    assert result.get("status") == "superseded"
    assert PLAYER.current() is None, "a superseded play must not repopulate the state"


@pytest.mark.asyncio
async def test_shutdown_stops_the_music_and_closes_the_gate(ok_daemon, tmp_path):
    """The daemon keeps playing after our session dies; and so did the gate."""
    deps = _deps(tmp_path)
    token = await music_hooks.on_session_started(deps)   # round 3, finding 2
    track = tmp_path / "abc.mp3"
    track.write_bytes(b"ID3")
    await PLAYER.play(deps, video_id="abc", title="A Song", source_path=track)
    GATE.arm("drive_trash", "move a file to Trash", {"file_id": "f1"})
    ok_daemon.clear()

    await music_hooks.on_session_shutdown(deps, token)

    assert PLAYER.current() is None
    assert GATE.claim("drive_trash") is None
    assert any(url.endswith("/api/media/stop_sound") for url in ok_daemon)


@pytest.mark.asyncio
async def test_an_overlapping_reconnect_ignores_the_stale_shutdown(ok_daemon, tmp_path):
    """**Round 3, finding 2, mandatory case: the replaced connection's `finally`.**

    The handler can open a replacement connection before the previous one's
    `finally` has run. That late cleanup used to tear down the session that
    replaced it -- clearing the new drain state, ending the new gate epoch and
    nulling the deps the tool-completion path needs. It must now do nothing.
    """
    deps = _deps(tmp_path)
    old_token = await music_hooks.on_session_started(deps)
    new_token = await music_hooks.on_session_started(deps)   # the reconnect
    assert new_token != old_token

    GATE.arm("drive_trash", "move a file to Trash", {"file_id": "f1"})
    track = tmp_path / "abc.mp3"
    track.write_bytes(b"ID3")
    await PLAYER.play(deps, video_id="abc", title="A Song", source_path=track)
    ok_daemon.clear()

    await music_hooks.on_session_shutdown(deps, old_token)   # the stale finally

    assert GATE.claim("drive_trash") is not None, "the stale cleanup closed the live gate"
    assert PLAYER.current() is not None, "the stale cleanup silenced the live session"
    assert ok_daemon == [], "the stale cleanup talked to the daemon"

    # And the live token still works, which is what makes this a scoping fix
    # rather than a shutdown that stopped working.
    await music_hooks.on_session_shutdown(deps, new_token)
    assert PLAYER.current() is None
    assert GATE.claim("drive_trash") is None


@pytest.mark.asyncio
async def test_the_session_hooks_run_in_the_connection_finally(monkeypatch: Any) -> None:
    """Round 2, finding 8: cleanup belongs to the connection, not to shutdown.

    A realtime connection that drops without the handler shutting down is the
    common case, and it was leaving the speaker running and the gate armed.
    `_run_realtime_session()` alone must open **and** close the session.
    """
    calls: list[str] = []
    tokens: list[int] = []

    async def record_start(_deps):
        calls.append("start")
        return 7                       # round 3, finding 2: start mints a token

    async def record_stop(_deps, token):
        calls.append("stop")
        tokens.append(token)

    monkeypatch.setattr(hf_mod, "on_session_started", record_start)
    monkeypatch.setattr(hf_mod, "on_session_shutdown", record_stop)
    monkeypatch.setattr(hf_mod, "on_user_speech_started", lambda _deps: None)
    monkeypatch.setattr(hf_mod, "on_assistant_turn_ended", lambda _deps: None)
    monkeypatch.setattr(hf_mod, "on_response_created", lambda: None)

    handler = _handler_with(())
    await handler._run_realtime_session()
    assert calls == ["start", "stop"], "the finally must close the session on its own"
    assert tokens == [7], "the finally must present the token its own session was minted with"


@pytest.mark.asyncio
async def test_the_session_is_closed_even_when_the_connection_raises(monkeypatch: Any) -> None:
    """The `finally` is only worth having if it survives the error path."""
    calls: list[str] = []

    async def record_start(_deps):
        calls.append("start")
        return 11

    async def record_stop(_deps, _token):
        calls.append("stop")

    monkeypatch.setattr(hf_mod, "on_session_started", record_start)
    monkeypatch.setattr(hf_mod, "on_session_shutdown", record_stop)
    monkeypatch.setattr(hf_mod, "on_user_speech_started", lambda _deps: None)
    monkeypatch.setattr(hf_mod, "on_assistant_turn_ended", lambda _deps: None)
    monkeypatch.setattr(hf_mod, "on_response_created", lambda: None)

    # A connection that dies mid-stream, which is what actually happens on a
    # flaky link. `_make_fake_realtime_client`'s connection object yields from an
    # iterator, so making `__anext__` raise reproduces it exactly -- and reuses
    # the real fake, so the rest of the connection surface stays correct.
    handler = _handler_with(())
    connection_type = type(handler.client.realtime.connect())

    async def dropped(_self):
        raise ConnectionError("connection dropped")

    monkeypatch.setattr(connection_type, "__anext__", dropped, raising=True)

    with pytest.raises(ConnectionError):
        await handler._run_realtime_session()
    assert calls == ["start", "stop"], "the finally must close the session on the error path too"


@pytest.mark.asyncio
async def test_handler_shutdown_is_still_safe_after_the_connection_closed(monkeypatch: Any) -> None:
    """Both call sites are idempotent; running cleanup twice must be harmless."""
    calls: list[str] = []

    async def record_start(_deps):
        return 13

    async def record_stop(_deps, _token):
        calls.append("stop")

    monkeypatch.setattr(hf_mod, "on_session_started", record_start)
    monkeypatch.setattr(hf_mod, "on_session_shutdown", record_stop)
    monkeypatch.setattr(hf_mod, "on_user_speech_started", lambda _deps: None)
    monkeypatch.setattr(hf_mod, "on_assistant_turn_ended", lambda _deps: None)
    monkeypatch.setattr(hf_mod, "on_response_created", lambda: None)

    handler = _handler_with(())
    await handler._run_realtime_session()
    handler.connection = None
    await handler.shutdown()
    assert calls == ["stop", "stop"]


@pytest.mark.asyncio
async def test_hooks_are_no_ops_when_nothing_plays(monkeypatch: Any) -> None:
    """Every turn fires these; with no music they must cost nothing."""

    async def fail_post(self, *args, **kwargs):
        raise AssertionError("music hooks must not touch the daemon when idle")

    monkeypatch.setattr(httpx.AsyncClient, "post", fail_post)
    deps = _deps()
    music_hooks.on_user_speech_started(deps)
    music_hooks.on_assistant_turn_ended(deps)
    await music_hooks.drain_pending_for_tests()
    assert PLAYER.current() is None


def test_no_production_code_calls_player_reset():
    """Round 2, finding 8: `reset()` is a test affordance, not a lifecycle call."""
    from pathlib import Path

    src_root = Path(__file__).parents[1] / "src" / "reachy_companion"
    for path in src_root.rglob("*.py"):
        if "__pycache__" in str(path):
            continue
        source = path.read_text(encoding="utf-8")
        if path.name == "music_player.py":
            continue  # this is where it is defined
        assert "PLAYER.reset(" not in source, f"{path.name} resets instead of invalidating"
```

- [ ] **Step 2: Run the test to verify it fails**

```powershell
cd C:\Project\Reachy-mini\reachy_companion
..\.venv\Scripts\python.exe -m pytest tests/test_hanova_music_barge_in.py -q
```

Expected: a collection error — `ModuleNotFoundError: No module named
'reachy_companion.hanova.audio_drain'`.

- [ ] **Step 3a: Create `hanova/audio_drain.py`**

Create `reachy_companion/src/reachy_companion/hanova/audio_drain.py`:

```python
"""When has this turn's audio actually finished coming out of the speaker?

Review round 1, finding 1. `response.done` does not answer that question -- the
real handler says so in its own comment -- and neither does
`response.output_audio.done`: at that instant `console.play_loop` still has
queued PCM, and the device buffer behind it holds up to a second more. Resuming
music on either event puts the track back over the top of Reachy's own voice.

**Review round 2, finding 1 rebuilt this module.** The first version began in the
"drained" state and only ever learned about audio that had *already been played*:
`_QUEUE_EMPTY` started `True`, `_DRAINED_AT` started `0.0`, and nothing but
`play_loop` dequeuing ever changed them. So on a real turn the sequence was
`response.output_audio.done` -> `wait_drained()` -> "yes, drained" -> resume, with
the whole reply still sitting in the queue. The fix has two halves:

* **Pending is the default, and it is set before the audio exists.**
  `begin_response()` opens a generation that `wait_drained` refuses until
  `close_response()` is called. There is no window in which a live response looks
  finished.
* **Audio is counted at enqueue time, not at play time.** The event receiver
  calls `note_enqueued()` *before* each delta goes into the playback queue;
  `play_loop` calls `note_chunk()` when the samples reach the sink, which retires
  that much outstanding audio and pushes out the device-buffer estimate.

`wait_drained(generation)` is therefore four conditions, none true by default:
the generation is closed, nothing it enqueued is outstanding, the local queue is
empty, and the device-buffer estimate has expired. The estimate is deliberately
conservative and capped: a wrong estimate should cost a short extra silence,
never a hung resume.
"""

from __future__ import annotations

import time
import asyncio
import logging
import threading
from typing import Dict

logger = logging.getLogger(__name__)

# The device buffer is not observable through the SDK, so the drain time is
# estimated from the samples handed over. This cap stops a bad sample-rate from
# parking a resume for minutes.
_MAX_PENDING_S = 10.0
_POLL_S = 0.02
# Floating-point slack: 0.02 s is far below anything audible.
_EPSILON_S = 0.02

_LOCK = threading.Lock()
_GENERATION = 0
# generation -> whether the model has finished emitting audio for it. A
# generation absent from this map is treated as closed, so a `wait_drained` for
# a turn that never opened one cannot hang.
_CLOSED: Dict[int, bool] = {}
# Seconds of audio enqueued but not yet handed to the sink.
_OUTSTANDING_S = 0.0
_QUEUE_EMPTY = True
_DRAINED_AT = 0.0


def reset() -> None:
    """Forget every generation and all pending audio. Session start and tests."""
    global _GENERATION, _OUTSTANDING_S, _QUEUE_EMPTY, _DRAINED_AT
    with _LOCK:
        _GENERATION += 1
        _CLOSED.clear()
        _OUTSTANDING_S = 0.0
        _QUEUE_EMPTY = True
        _DRAINED_AT = 0.0


def begin_response() -> int:
    """Open a new response generation, **pending by definition** (finding 1).

    Returns the generation token the turn-end hook must close and wait on.
    """
    global _GENERATION
    with _LOCK:
        _GENERATION += 1
        _CLOSED[_GENERATION] = False
        # Bound the bookkeeping: only the last few generations can matter.
        for stale in sorted(_CLOSED)[:-4]:
            _CLOSED.pop(stale, None)
        return _GENERATION


def note_enqueued(generation: int, sample_count: int, sample_rate: int) -> None:
    """Record audio that is about to enter the playback queue (finding 1).

    Called by the **event receiver**, before the append. This is the accounting
    that makes "the response is done but nothing has played yet" expressible.
    """
    global _OUTSTANDING_S, _QUEUE_EMPTY
    if sample_count <= 0 or sample_rate <= 0:
        return
    duration_s = sample_count / float(sample_rate)
    with _LOCK:
        # Counted regardless of which generation it belongs to: there is one
        # sink, and a late delta from a superseded turn still occupies it. The
        # *generation* only decides who is allowed to stop waiting.
        _OUTSTANDING_S += duration_s
        _QUEUE_EMPTY = False


def note_chunk(sample_count: int, sample_rate: int) -> None:
    """Record that *sample_count* frames were handed to the audio sink."""
    global _OUTSTANDING_S, _QUEUE_EMPTY, _DRAINED_AT
    if sample_count <= 0 or sample_rate <= 0:
        return
    duration_s = min(sample_count / float(sample_rate), _MAX_PENDING_S)
    now = time.monotonic()
    with _LOCK:
        _OUTSTANDING_S = max(0.0, _OUTSTANDING_S - duration_s)
        _QUEUE_EMPTY = False
        base = max(_DRAINED_AT, now)
        _DRAINED_AT = min(base + duration_s, now + _MAX_PENDING_S)


def note_queue_empty() -> None:
    """Record that the local playback queue has nothing left in it."""
    global _QUEUE_EMPTY
    with _LOCK:
        _QUEUE_EMPTY = True


def close_response(generation: int) -> None:
    """No further audio will be enqueued for *generation*."""
    with _LOCK:
        if generation in _CLOSED:
            _CLOSED[generation] = True


def note_cleared() -> None:
    """The queue was flushed: nothing outstanding will ever play.

    A barge-in also ends every open turn, so every generation is closed here --
    otherwise a resume scheduled for the interrupted turn would wait out its
    whole timeout for audio that no longer exists.
    """
    global _OUTSTANDING_S, _QUEUE_EMPTY, _DRAINED_AT
    with _LOCK:
        for generation in list(_CLOSED):
            _CLOSED[generation] = True
        _OUTSTANDING_S = 0.0
        _QUEUE_EMPTY = True
        _DRAINED_AT = 0.0


def outstanding_s() -> float:
    """Seconds of audio enqueued but not yet handed to the sink. For tests."""
    with _LOCK:
        return _OUTSTANDING_S


def _is_drained(generation: int) -> bool:
    with _LOCK:
        if not _CLOSED.get(generation, True):
            return False
        if _OUTSTANDING_S > _EPSILON_S:
            return False
        return _QUEUE_EMPTY and time.monotonic() >= _DRAINED_AT


async def wait_drained(generation: int, timeout_s: float) -> bool:
    """Wait until *generation* is closed and all of its audio has been played."""
    deadline = time.monotonic() + max(0.0, timeout_s)
    while True:
        if _is_drained(generation):
            return True
        if time.monotonic() >= deadline:
            logger.debug("audio drain wait timed out after %.2fs", timeout_s)
            return False
        await asyncio.sleep(_POLL_S)
```

- [ ] **Step 3b: Create `hanova/music_hooks.py`**

Create `reachy_companion/src/reachy_companion/hanova/music_hooks.py`:

```python
"""The realtime loop's call sites (D-018, R7, findings 1 and 3; round 2
findings 1 and 8).

`huggingface_realtime.py` imports only these names, so the wiring is testable on
its own and the handler never touches player internals.

Three rules the earlier drafts broke:

* **Nothing here awaits I/O on behalf of the event receiver.** A pause request
  carries a five-second daemon timeout; awaiting it inside the receive loop
  stalls every event queued behind it, including the next `speech_started`. The
  speech, audio and turn-end hooks are therefore plain `def`s that *schedule*
  work.
* **A turn is over only when its audio has drained and nothing else is in
  flight.** A tool-calling turn emits `response.done` and is then followed by a
  second response that speaks the result; resuming in between talks over it.
  Round 2, finding 1: "drained" is now decided per response generation by
  `audio_drain`, which is told about audio *before* it is queued.
* **A tool batch that needs no follow-up response still ends the turn.** Round 2,
  finding 1: `on_tool_call_finished()` used to schedule nothing at all, so a
  final batch with `needs_response=False` -- which produces no further
  `response.created` -- left the music paused for the rest of the conversation.
  The flag is now a parameter and the last tool of such a batch closes the turn
  and schedules the resume itself.

Round 2, finding 8: both session boundaries **invalidate and stop** the player
rather than merely forgetting its state, and they run from
`_run_realtime_session()`'s `finally`, so a dropped connection cleans up even
when the handler never shuts down.

Round 3 added two more:

* **finding 1 — the drain wait is unbounded.** A 12-second cap on
  `_resume_when_drained()` was a 12-second cap on how long a reply may take
  before the music stays down forever. The interval is now purely diagnostic;
  the wait ends only on a real event: drained, session over, or superseded.
* **finding 2 — the session has a token.** `_DEPS` alone could not tell a
  cleanup from the *previous* connection apart from one belonging to the live
  one, so a late `finally` from a replaced connection tore down its successor.
  `on_session_started()` mints `_SESSION_TOKEN`; `on_session_shutdown()` refuses
  anything else.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Set

from reachy_companion.hanova import audio_drain
from reachy_companion.hanova.confirm import GATE
from reachy_companion.hanova.music_player import PLAYER

logger = logging.getLogger(__name__)

# Round 3, finding 1: how often the resume waiter reports that it is still
# waiting. It is a LOGGING interval, not a deadline -- the previous version
# treated the same number as a give-up point and stranded the music.
_DRAIN_REPORT_EVERY_S = 12.0

_TASKS: Set[asyncio.Task[Any]] = set()
_RESUME_TASK: asyncio.Task[Any] | None = None
_TOOLS_IN_FLIGHT = 0
_RESPONSE_IN_FLIGHT = False
# The generation `audio_drain` handed us for the response currently being
# spoken. 0 means "no response has been created in this session".
_RESPONSE_GENERATION = 0
# The deps of the current session, captured at session start. `on_tool_call_
# finished` is called from a completion path that must not have to thread deps
# through the background tool manager, and it is the one hook that can need to
# schedule work (round 2, finding 1).
_DEPS: Any = None
# Round 3, finding 2: the identity of the live realtime session. `_SESSION_SEQ`
# only ever grows; `_SESSION_TOKEN` is the live value, or 0 when no session is
# open. A cleanup presenting anything else belongs to a connection that has
# already been replaced, and tearing down on its behalf would dismantle the
# session that replaced it.
_SESSION_SEQ = 0
_SESSION_TOKEN = 0


def _spawn(coro: Any) -> asyncio.Task[Any]:
    """Run *coro* detached, keeping a strong reference so it is not collected."""
    task = asyncio.ensure_future(coro)
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return task


def _cancel_pending_resume() -> None:
    global _RESUME_TASK
    if _RESUME_TASK is not None and not _RESUME_TASK.done():
        _RESUME_TASK.cancel()
    _RESUME_TASK = None


def _clear_phase() -> None:
    global _TOOLS_IN_FLIGHT, _RESPONSE_IN_FLIGHT, _RESPONSE_GENERATION
    _cancel_pending_resume()
    _TOOLS_IN_FLIGHT = 0
    _RESPONSE_IN_FLIGHT = False
    _RESPONSE_GENERATION = 0
    audio_drain.reset()


# --- session lifecycle ------------------------------------------------------
async def on_session_started(deps: Any) -> int:
    """A new realtime session: new epoch, silenced speaker, clean audio state.

    Round 2, finding 8: `PLAYER.reset()` used to stand here. It dropped the
    state snapshot without advancing the generation or stopping the daemon, so a
    transition still in flight from the previous connection finished afterwards
    and wrote its state back -- audio surviving a reconnect. `invalidate()`
    supersedes those transitions and `stop()` actually silences the device.

    Round 3, finding 2: returns the **session token**. The caller keeps it and
    hands it back to `on_session_shutdown`, which is how a cleanup arriving late
    from a replaced connection is told apart from the live one's.
    """
    global _DEPS, _SESSION_SEQ, _SESSION_TOKEN
    _SESSION_SEQ += 1
    _SESSION_TOKEN = _SESSION_SEQ
    token = _SESSION_TOKEN
    _DEPS = deps
    _clear_phase()
    PLAYER.invalidate()
    await PLAYER.stop(deps)
    if token != _SESSION_TOKEN:
        # A newer session opened while we were silencing the daemon. It owns the
        # gate and the deps now; finishing our start would clobber both.
        logger.info("realtime session %d was superseded while starting", token)
        return token
    GATE.begin_session()
    # Task 13, Step 6b adds `nas.clear_session()` here once that module exists.
    return token


async def on_session_shutdown(deps: Any, token: int) -> None:
    """Stop the speaker and close the confirmation session (findings 1, 3, 8).

    Round 3, finding 2: *token* must be the live session's. The handler can open
    a replacement connection before the previous connection's `finally` runs, and
    that stale cleanup used to clear the **new** session's drain state, gate
    epoch, NAS trip and deps. A stale token is now a no-op, which also keeps the
    hook idempotent: the second of two cleanups for the same session presents a
    token that is no longer live.
    """
    global _DEPS, _SESSION_TOKEN
    if not token or token != _SESSION_TOKEN:
        logger.debug("ignoring a stale realtime-session cleanup (token %s, live %s)", token, _SESSION_TOKEN)
        return
    _SESSION_TOKEN = 0
    _clear_phase()
    PLAYER.invalidate()
    await PLAYER.stop(deps)
    GATE.end_session()
    # Task 13, Step 6b adds `nas.clear_session()` here once that module exists.
    _DEPS = None


# --- turn phase -------------------------------------------------------------
def on_response_created() -> None:
    """A new assistant response started; any pending resume is now wrong."""
    global _RESPONSE_IN_FLIGHT, _RESPONSE_GENERATION
    _RESPONSE_IN_FLIGHT = True
    _cancel_pending_resume()
    # Round 2, finding 1: opening the generation here is what makes the turn
    # "pending" before a single byte of its audio exists.
    _RESPONSE_GENERATION = audio_drain.begin_response()


def on_response_audio(sample_count: int, sample_rate: int) -> None:
    """Report audio the receiver is about to enqueue (round 2, finding 1)."""
    if _RESPONSE_GENERATION:
        audio_drain.note_enqueued(_RESPONSE_GENERATION, sample_count, sample_rate)


def on_tool_call_started() -> None:
    """A tool call is running, so a follow-up response is still to come."""
    global _TOOLS_IN_FLIGHT
    _TOOLS_IN_FLIGHT += 1
    _cancel_pending_resume()


def on_tool_call_finished(needs_response: bool) -> None:
    """One tool call finished. Close the turn when nothing else will.

    Round 2, finding 1: when this is the **last** tool of the batch and the batch
    wants no follow-up response, there will never be another
    `response.created` / `response.done` pair, so nothing else would ever
    schedule the resume. This path closes the turn itself.
    """
    global _TOOLS_IN_FLIGHT
    _TOOLS_IN_FLIGHT = max(0, _TOOLS_IN_FLIGHT - 1)
    if _TOOLS_IN_FLIGHT > 0 or needs_response:
        return
    if _DEPS is None:
        logger.debug("music resume not scheduled: no session deps captured yet")
        return
    logger.debug("final tool batch wants no follow-up response; closing the turn")
    on_assistant_turn_ended(_DEPS)


# --- the audio hooks --------------------------------------------------------
def on_user_speech_started(deps: Any) -> None:
    """Duck the music because the user just started talking. Never blocks."""
    _cancel_pending_resume()
    audio_drain.note_cleared()
    _spawn(PLAYER.pause_for_speech(deps))


def on_assistant_turn_ended(deps: Any) -> None:
    """Close this turn's audio generation and schedule the drain-then-resume."""
    global _RESPONSE_IN_FLIGHT, _RESUME_TASK
    _RESPONSE_IN_FLIGHT = False
    generation = _RESPONSE_GENERATION
    if generation:
        # No more audio is coming for this response. Outstanding audio still in
        # the queue keeps `wait_drained` False until it has actually played.
        audio_drain.close_response(generation)
    if _TOOLS_IN_FLIGHT > 0:
        # A tool is still running; its result will produce another response, or
        # `on_tool_call_finished(needs_response=False)` will come back here.
        return
    _cancel_pending_resume()
    _RESUME_TASK = _spawn(_resume_when_drained(deps, generation, _SESSION_TOKEN))


async def _resume_when_drained(deps: Any, generation: int, session: int) -> None:
    """Wait for this generation's drain signal, re-check the phase, then resume.

    **Round 3, finding 1: this wait is not bounded.** The previous version gave
    `wait_drained` a 12-second timeout and returned on expiry, so one long queued
    response or one slow sink left the track paused for the rest of the
    conversation. There are exactly three ways out now:

    1. the generation drains -- resume;
    2. the realtime session that scheduled this resume has ended (*session* is no
       longer the live token) -- there is nothing left to resume into;
    3. a newer response superseded this turn -- the resume it schedules is the
       right one, and this one must not race it.

    Everything else is a log line. A cancellation (what `on_response_created`
    and `_clear_phase` actually raise here) still returns immediately.
    """
    waited_s = 0.0
    try:
        while True:
            if await audio_drain.wait_drained(generation, _DRAIN_REPORT_EVERY_S):
                break
            waited_s += _DRAIN_REPORT_EVERY_S
            if session != _SESSION_TOKEN:
                logger.info("music resume abandoned after %.0fs: the session ended", waited_s)
                return
            if generation != _RESPONSE_GENERATION:
                logger.info("music resume abandoned after %.0fs: a newer response superseded it", waited_s)
                return
            # Diagnostic only -- the loop continues (round 3, finding 1).
            logger.info(
                "music resume still waiting for the turn's audio to drain: %.0fs elapsed, %.2fs outstanding",
                waited_s,
                audio_drain.outstanding_s(),
            )
    except asyncio.CancelledError:
        return
    if session != _SESSION_TOKEN:
        logger.debug("music resume skipped: the session ended while the audio drained")
        return
    if _RESPONSE_IN_FLIGHT or _TOOLS_IN_FLIGHT > 0:
        logger.debug("music resume skipped: another response or tool call started")
        return
    await PLAYER.resume_after_speech(deps)


# --- test support -----------------------------------------------------------
def reset_for_tests() -> None:
    """Cancel every scheduled hook and clear the phase counters."""
    global _DEPS, _SESSION_TOKEN
    _clear_phase()
    for task in list(_TASKS):
        task.cancel()
    _TASKS.clear()
    _DEPS = None
    # Round 3, finding 2: back to "no session open". `_SESSION_SEQ` is
    # deliberately NOT reset -- a token must never be reused across tests.
    _SESSION_TOKEN = 0


async def drain_pending_for_tests() -> None:
    """Await every scheduled hook so a test can assert on its effects."""
    pending = [task for task in list(_TASKS) if not task.done()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
```

- [ ] **Step 3c: Report the drain from `console.play_loop`**

In `reachy_companion/src/reachy_companion/console.py`, inside `play_loop`, add
three notifications. The exact lines depend on the loop's current shape; the
contract is:

1. immediately after each PCM chunk is written to the audio sink:
   `audio_drain.note_chunk(sample_count=<frames in this chunk>, sample_rate=<the sink's rate>)`
2. where the loop observes that its queue is empty (the branch that waits for
   more audio): `audio_drain.note_queue_empty()`
3. inside the barge-in queue flush — the same code path `_clear_queue` triggers:
   `audio_drain.note_cleared()`

Import it at the top of `console.py` as
`from reachy_companion.hanova import audio_drain`. These three calls are pure
bookkeeping: they take a lock, do arithmetic, and return. Nothing in the audio
path may await.

- [ ] **Step 4: Wire the eight call sites in `huggingface_realtime.py`**

**4a.** Add the import next to the other `reachy_companion` imports near the top of the file (the block that already contains `from reachy_companion.tools.background_tool_manager import (`):

```python
from reachy_companion.hanova.music_hooks import (
    on_response_audio,
    on_session_started,
    on_session_shutdown,
    on_response_created,
    on_tool_call_started,
    on_tool_call_finished,
    on_user_speech_started,
    on_assistant_turn_ended,
)
```

**4a-bis (round 3, finding 2).** In `HuggingFaceRealtimeHandler.__init__`, next
to the other instance attributes, declare the session token the two cleanup call
sites present:

```python
        # D-018 / round 3 finding 2: the token of the realtime session this
        # handler currently owns. 0 means "no session open". It is what stops a
        # late cleanup from a replaced connection tearing down its successor.
        self._hanova_session: int = 0
```

**4b.** Wrap the body of `_run_realtime_session()` so the session is opened after
the connection is established and **closed in a `finally`** (round 2, finding 8).
Attaching the close only to `handler.shutdown()` meant a connection that dropped
on its own left the speaker playing and the confirmation gate armed — which is
the common case, not the rare one. Round 3, finding 2 adds the token: the
`finally` closes **the session this connection opened**, never whatever session
happens to be live by the time it runs.

```python
        # D-018 / R7 + finding 3: a new realtime session gets a new confirmation
        # epoch, so nothing armed in the previous conversation can be confirmed
        # in this one, and starts with clean audio-drain bookkeeping.
        # Round 3, finding 2: keep the token this session was minted with.
        session_token = await on_session_started(self.deps)
        self._hanova_session = session_token
        try:
            <the existing event loop body, indented one level>
        finally:
            # Round 2, finding 8: this connection is over however it ended --
            # clean exit, exception, or cancellation. Stop the daemon audio and
            # close the gate here rather than hoping shutdown() runs.
            # Round 3, finding 2: with OUR token. If the handler already opened a
            # replacement connection, this cleanup is stale and does nothing --
            # which is exactly right, because the replacement already reset
            # everything this would otherwise tear down a second time.
            await on_session_shutdown(self.deps, session_token)
```

Use the local `session_token`, not `self._hanova_session`, inside the `finally` —
a replacement connection overwrites the attribute, and the whole point is that
this `finally` speaks for *its own* session.

`on_session_shutdown` is idempotent (a second cleanup for the same session
presents a token that is no longer live), so the `handler.shutdown()` call site
in **4h** stays as well: whichever runs first does the work and the other is a
no-op.

**4c.** Find this exact block:

```python
                    if event.type == "input_audio_buffer.speech_started":
                        self._mark_activity("user_speech_started")
                        self._turn_user_done_at = None
                        self._turn_response_created_at = None
                        self._turn_first_audio_at = None
                        if self._clear_queue:
                            self._clear_queue()
                        self.deps.movement_manager.set_listening(True)
                        logger.debug("User speech started")
```

and insert between `set_listening(True)` and the `logger.debug` line:

```python
                        # D-018 / R7: duck robot-speaker music the instant the user
                        # starts talking. NOT awaited (finding 1): the pause carries a
                        # five-second daemon timeout, and awaiting it here would stall
                        # every event queued behind it in this receiver.
                        on_user_speech_started(self.deps)
```

**4d.** Find the `response.created` branch (the one that already sets
`self._turn_response_created_at`) and add, as its first statement:

```python
                        # D-018 / finding 1: a new response means any resume that is
                        # waiting on the previous turn's drain signal is now wrong.
                        # It also opens the drain generation, which is PENDING from
                        # this moment on -- before any audio exists (round 2).
                        on_response_created()
```

**4d-bis (round 2, finding 1).** Find the `response.output_audio.delta` branch —
the one that decodes the base64 PCM and puts it on the playback queue — and call
the hook **immediately before the enqueue**, with the sample count of *this*
chunk:

```python
                        # D-018 / round 2 finding 1: the drain tracker has to know
                        # the audio exists BEFORE it enters the queue. Counting it
                        # only when play_loop dequeues is what let response.done
                        # look "drained" with the whole reply still buffered.
                        on_response_audio(
                            sample_count=len(pcm_bytes) // 2,   # 16-bit mono frames
                            sample_rate=OUTPUT_SAMPLE_RATE,
                        )
```

Use whatever the branch already calls the decoded bytes and the output rate;
`// 2` is the frame count for the 16-bit mono PCM this path carries. This call
must be *before* the queue append, and it must not be inside a `try` that could
skip it.

**4e.** Find this exact block:

```python
                    if event.type == "response.output_audio.done":
                        self.deps.movement_manager.set_speaking(False)
                        logger.debug("response completed")
```

and insert between the two lines:

```python
                        # D-018 / R7: the assistant's turn produced its last audio.
                        # This only *schedules* the resume; it fires when
                        # console.play_loop reports the audio has actually drained.
                        on_assistant_turn_ended(self.deps)
```

**4f.** Find this exact block:

```python
                    if event.type == "response.done":
                        # Doesn't mean the audio is done playing
                        # Resume tracking for responses that emit no audio (text-only / tool-only).
                        self.deps.movement_manager.set_speaking(False)
                        self._response_done_event.set()
```

and insert between `set_speaking(False)` and `self._response_done_event.set()`:

```python
                        # D-018 / R7: a text-only or tool-only response never emits
                        # response.output_audio.done, so end the turn here too. The
                        # hook is idempotent, and it refuses to schedule a resume
                        # while a tool call is still in flight -- a tool turn is
                        # always followed by a second, speaking response (finding 1).
                        on_assistant_turn_ended(self.deps)
```

**4g.** Around the `BackgroundToolManager.start_tool()` dispatch
(`huggingface_realtime.py:1011`), bracket the tool call so the turn-phase counter
is accurate:

```python
                        on_tool_call_started()
```

immediately before `start_tool(...)`, and, in the completion path that already
handles the tool result — **including the failure path**, because a tool that
raised still ends its phase or a pending resume would be blocked forever:

```python
                        # D-018 / round 2 finding 1: `needs_response` decides
                        # whether anything else will ever end this turn. When the
                        # last tool of a batch wants no reply there is no further
                        # response.created, so this hook has to close the turn and
                        # schedule the resume itself -- otherwise the music stays
                        # paused for the rest of the conversation.
                        on_tool_call_finished(needs_response=<the batch's needs_response>)
```

Pass the **same** `needs_response` value this completion path already uses to
decide whether to ask the backend for a follow-up response. If the handler
computes it per batch rather than per tool, pass the batch's value to every tool
in that batch; `music_hooks` only acts on the transition to zero tools in flight.
If the failure path has no `needs_response` in scope, pass `needs_response=True`
there — a tool that raised will be reported back to the model, which produces a
response, and over-waiting costs a short silence while under-waiting costs a
permanently ducked track.

**4h.** Find this exact block:

```python
    async def shutdown(self) -> None:
        """Shutdown the handler."""
        # Unblock the response sender worker so it can exit
        self._response_done_event.set()
```

and insert between the docstring and the comment:

```python
        # D-018 / R7 + finding 3: the daemon keeps playing a sound file after our
        # session dies, so a shutdown that leaves music running is a bug the user
        # hears -- and a confirmation left armed is one the next conversation
        # could consume. Round 2, finding 8: this is now the *second* line of
        # defence; `_run_realtime_session()`'s finally is the first. Round 3,
        # finding 2: presenting the handler's own token makes "running it twice"
        # a no-op by construction, and makes a shutdown() that arrives after a
        # reconnect unable to close the reconnected session.
        await on_session_shutdown(self.deps, self._hanova_session)
```

- [ ] **Step 5: Normalise the import ordering ruff expects**

```powershell
cd C:\Project\Reachy-mini\reachy_companion
..\.venv\Scripts\python.exe -m ruff check --fix src tests
..\.venv\Scripts\python.exe -m ruff format src tests
```

- [ ] **Step 6: Run the test to verify it passes**

```powershell
cd C:\Project\Reachy-mini\reachy_companion
..\.venv\Scripts\python.exe -m pytest tests/test_hanova_music_barge_in.py -q
```

Expected: green — **28** test functions, none parametrised: **28 collected
cases** (round 3 added `test_outstanding_audio_past_the_report_interval_still_resumes`
for finding 1 and `test_an_overlapping_reconnect_ignores_the_stale_shutdown` for
finding 2). Record the exact number.

- [ ] **Step 7: Run the full gate**

```powershell
cd C:\Project\Reachy-mini\reachy_companion
..\.venv\Scripts\python.exe -m pytest -q
..\.venv\Scripts\python.exe -m ruff check src tests
..\.venv\Scripts\python.exe -m ruff format --check src tests
..\.venv\Scripts\python.exe -m mypy
```

Expected: pytest green (30 documented skips), ruff clean, mypy `Success: no issues found`.

- [ ] **Step 8: Commit**

```bash
cd /c/Project/Reachy-mini
git rev-parse --abbrev-ref HEAD   # must print feat/ha-nova-port (Task 0)
git add reachy_companion/src/reachy_companion/hanova/audio_drain.py \
        reachy_companion/src/reachy_companion/hanova/music_hooks.py \
        reachy_companion/src/reachy_companion/console.py \
        reachy_companion/src/reachy_companion/huggingface_realtime.py \
        reachy_companion/tests/test_hanova_music_barge_in.py
git commit -m "feat(hanova): duck music on barge-in, resume only after the turn's audio drains, scope the confirmation gate to the session"
```

---

### Task 6: TV casting — `play_video` and `show_on_tv`

Implements the media-cast half of R2 plus R4's house-bound behaviour. `play_video` is upstream's Path A (cast an app launch — no bytes leave us). `show_on_tv` is reinterpreted per R2: we generate the image ourselves with the OpenAI Images API using the robot's existing `OPENAI_API_KEY`, serve it from the Task 3 media cache, and cast that LAN URL — the operator's Hermes image pipeline is not ported.

**Files:**
- Create: `reachy_companion/src/reachy_companion/hanova/images.py`
- Create: `reachy_companion/src/reachy_companion/tools/play_video.py`
- Create: `reachy_companion/src/reachy_companion/tools/show_on_tv.py`
- Modify: `reachy_companion/profiles/_reachy_companion_locked_profile/profile.md` (`default_tools` gains `"play_video"`, `"show_on_tv"`)
- Test: `reachy_companion/tests/test_hanova_cast.py`

The three HA script-name accessors and their `.env.example` block **already exist
from Task 1** — review finding 6 moved them there so they could default to empty
and become real prerequisites. Nothing about `settings.py` changes in this task.

**Interfaces:**
- Consumes: `hanova.settings.{tool_status, unavailable, image_model, image_keep, cast_entity, ha_script_youtube, ha_script_image_url}`; `hanova.ha_client.ha_run_script`; `hanova.media_store.{media_dir, media_url, prune}`; `hanova.ytdlp.search`; `home_net.{home_state, HOME, AWAY, away_from_home, home_unknown}`; `hanova.redact`.
- **The house-bound preamble (round 2, finding 3).** Both tools — and all four `nas_*` tools in Task 13 — open with the *same three-way branch*, and no other shape. Round 1 tested only `AWAY`, so `UNKNOWN` fell straight through and the tool did the work anyway: a VPN, a 401, a 5xx or a timeout could all still fire a real house action. `UNKNOWN` now does **no work at all**:

```python
        verdict = await home_state()
        if verdict == AWAY:
            return away_from_home()
        if verdict != HOME:
            # Round 2, finding 3: UNKNOWN is not permission. Nothing is
            # resolved, downloaded, generated, staged or cast on this path.
            return home_unknown()
```

  Task 14's shape test asserts the exact `if verdict != HOME:` line in all six house-bound tools, so a future tool cannot quietly go back to a two-way branch.
- Produces:
  - `hanova.images.images_available() -> bool` — a **key-presence check that constructs nothing** (review finding 18)
  - `hanova.images.build_client() -> Any | None` — the seam tests monkeypatch; the caller uses it as an `async with` context manager so the HTTP pool is always closed
  - `hanova.images.generate_image(prompt: str, dest_dir: Path) -> Awaitable[Dict[str, Any]]` returning `{"ok": bool, "path": str | None, "filename": str | None, "error": str | None}`
  - `tools.play_video.PlayVideo` (`Tool.name == "play_video"`), `tools.show_on_tv.ShowOnTv` (`Tool.name == "show_on_tv"`)
- **Cast-target forwarding (review finding 10).** `HANOVA_CAST_ENTITY` was config nothing read. Both tools now forward it to the HA script as `entity_id` when it is set, and omit the field when it is not — so a house with two TVs can be targeted, and the key stops being dead weight in the availability table.

- [ ] **Step 1: Write the failing test**

Create `reachy_companion/tests/test_hanova_cast.py`:

```python
"""Contract tests for TV casting: play_video and show_on_tv (D-018, R2/R4/R5)."""

import base64
import types
import importlib
from typing import Any

import pytest

from reachy_companion import home_net
from reachy_companion.hanova import images, settings
from reachy_companion.tools.play_video import PlayVideo
from reachy_companion.tools.show_on_tv import ShowOnTv


PNG_BYTES = b"\x89PNG\r\n\x1a\nfake"


def _deps(tmp_path):
    return types.SimpleNamespace(reachy_mini=None, instance_path=tmp_path)


class _FakeImagesApi:
    def __init__(self, payload: Any = None, error: Exception | None = None) -> None:
        self._payload = payload
        self._error = error
        self.seen: dict[str, Any] = {}

    async def generate(self, **kwargs: Any) -> Any:
        self.seen.update(kwargs)
        if self._error is not None:
            raise self._error
        return self._payload


class _FakeOpenAI:
    """Stands in for AsyncOpenAI, and records that it was actually closed."""

    def __init__(self, images_api: "_FakeImagesApi") -> None:
        self.images = images_api
        self.closed = False

    async def __aenter__(self) -> "_FakeOpenAI":
        return self

    async def __aexit__(self, *_exc: Any) -> bool:
        self.closed = True
        return False


def _fake_openai(payload: Any = None, error: Exception | None = None):
    api = _FakeImagesApi(payload, error)
    return _FakeOpenAI(api), api


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    """Media-cast configured, robot at home, no real network anywhere."""
    monkeypatch.setattr("reachy_companion.hanova.settings._music_wheels_ready", lambda: (True, ""))
    monkeypatch.setenv("HA_URL", "http://ha.example.invalid:8123")
    monkeypatch.setenv("HA_TOKEN", "tok")
    monkeypatch.setenv("HANOVA_HA_SCRIPT_YOUTUBE", "tv_show_youtube")
    monkeypatch.setenv("HANOVA_HA_SCRIPT_IMAGE_URL", "tv_show_image_url")
    monkeypatch.setenv("HANOVA_CAST_ENTITY", "media_player.example_tv")
    monkeypatch.setenv("HANOVA_MEDIA_HTTP_BASE", "http://robot.example.invalid:7860")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    settings.set_media_mount_ready(True)
    home_net.reset_cache()

    async def always_home() -> str:
        return home_net.HOME

    monkeypatch.setattr("reachy_companion.tools.play_video.home_state", always_home)
    monkeypatch.setattr("reachy_companion.tools.show_on_tv.home_state", always_home)
    yield
    settings.set_media_mount_ready(False)
    home_net.reset_cache()


def _home_state(monkeypatch, module: str, verdict: str) -> None:
    async def fixed() -> str:
        return verdict

    monkeypatch.setattr(f"reachy_companion.tools.{module}.home_state", fixed)


def test_tool_names_match_their_filenames():
    """The loader resolves tools by filename == Tool.name."""
    assert PlayVideo.name == "play_video"
    assert ShowOnTv.name == "show_on_tv"


def test_descriptions_carry_no_personal_identifier():
    """R10: no entity id, address, folder id or owner name in a description."""
    for text in (PlayVideo().description, ShowOnTv().description):
        assert "@" not in text
        assert "media_player." not in text
        assert len(text) <= 120


@pytest.mark.asyncio
async def test_play_video_is_unavailable_without_its_ha_script(monkeypatch, tmp_path):
    """Finding 6: the script name has no default, so an unset one disables the tool."""
    monkeypatch.delenv("HANOVA_HA_SCRIPT_YOUTUBE")
    out = await PlayVideo()(deps=_deps(tmp_path), query="a documentary")
    assert out == {"status": "unavailable", "reason": "HANOVA_HA_SCRIPT_YOUTUBE"}


@pytest.mark.asyncio
async def test_play_video_does_not_need_the_lan_base_or_the_mount(monkeypatch, tmp_path):
    """Finding 10: this path hands HA an id; it serves no bytes of its own."""
    import reachy_companion.tools.play_video as play_video_module

    monkeypatch.delenv("HANOVA_MEDIA_HTTP_BASE")
    settings.set_media_mount_ready(False)
    monkeypatch.setattr(
        play_video_module.ytdlp,
        "search",
        lambda query, max_duration_s=None: {"ok": True, "id": "vid123", "title": "A Film", "error": None},
    )

    async def fake_run_script(script_name, data, timeout_s=60.0):
        return {"ok": True, "result": []}

    monkeypatch.setattr(play_video_module, "ha_run_script", fake_run_script)
    out = await PlayVideo()(deps=_deps(tmp_path), query="a documentary")
    assert out["ok"] is True


@pytest.mark.asyncio
async def test_play_video_is_away_from_home_off_the_lan(monkeypatch, tmp_path):
    """R4: house-bound tools say where they are, they do not blame the house."""
    _home_state(monkeypatch, "play_video", home_net.AWAY)
    out = await PlayVideo()(deps=_deps(tmp_path), query="a documentary")
    assert out == {"status": "away_from_home"}


@pytest.mark.asyncio
async def test_play_video_does_no_work_when_the_home_verdict_is_unknown(monkeypatch, tmp_path):
    """Round 2, finding 3: UNKNOWN is not permission, and it is not absence.

    Round 1 only branched on AWAY, so this path fell through and cast anyway.
    The answer must be its own status, and nothing may happen.
    """
    import reachy_companion.tools.play_video as play_video_module

    _home_state(monkeypatch, "play_video", home_net.UNKNOWN)

    def fail_search(query, max_duration_s=None):
        raise AssertionError("play_video must not resolve anything on UNKNOWN")

    async def fail_run_script(script_name, data, timeout_s=60.0):
        raise AssertionError("play_video must not touch Home Assistant on UNKNOWN")

    monkeypatch.setattr(play_video_module.ytdlp, "search", fail_search)
    monkeypatch.setattr(play_video_module, "ha_run_script", fail_run_script)
    out = await PlayVideo()(deps=_deps(tmp_path), query="a documentary")
    assert out["status"] == "home_status_unknown"
    assert out["status"] != "away_from_home"
    assert out["error"]


# --- the six no-side-effect cases (round 2, finding 3) --------------------
#
# These drive the REAL `home_net.home_state()` through its own seams rather than
# stubbing the tool's verdict, so they prove the whole chain: probe outcome ->
# verdict -> tool behaviour. In every one of the six, the house action must not
# happen. `HANOVA_HOME_NETWORKS` is left unset, which is the deployment default
# and the case in which `AWAY` is unprovable.


@pytest.fixture
def house_probe(monkeypatch):
    """Wire play_video to the real probe and record whether HA was touched."""
    import reachy_companion.tools.play_video as play_video_module

    touched: list[str] = []

    def fail_search(query, max_duration_s=None):
        touched.append("search")
        raise AssertionError("no search may run when the robot cannot confirm it is home")

    async def fail_run_script(script_name, data, timeout_s=60.0):
        touched.append("cast")
        raise AssertionError("no HA script may run when the robot cannot confirm it is home")

    monkeypatch.setattr(play_video_module.ytdlp, "search", fail_search)
    monkeypatch.setattr(play_video_module, "ha_run_script", fail_run_script)
    monkeypatch.setattr(play_video_module, "home_state", home_net.home_state)
    monkeypatch.delenv("HANOVA_HOME_NETWORKS", raising=False)
    home_net.reset_cache()
    yield touched
    home_net.reset_cache()


def _lan(monkeypatch, *, reachable=True, same_subnet=True, local="203.0.113.20"):
    async def probe(host, port, timeout_s):
        return home_net.LanProbe(
            reachable=reachable,
            local_address=local if reachable else "",
            same_subnet=same_subnet,
        )

    async def resolve(host, timeout_s):
        return local if reachable else ""

    monkeypatch.setattr(home_net, "lan_signal", probe)
    monkeypatch.setattr(home_net, "local_address", resolve)


def _ha_answers(monkeypatch, status_code=None, error=None):
    import httpx

    async def fake_get(self, url, headers=None, **kw):
        if error is not None:
            raise error
        return type("_R", (), {"status_code": status_code})()

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)


@pytest.mark.asyncio
async def test_a_vpn_reach_does_no_house_work(monkeypatch, tmp_path, house_probe):
    """Reachable from another network: presence is not proven, so do nothing."""
    _lan(monkeypatch, same_subnet=False)
    _ha_answers(monkeypatch, status_code=200)
    out = await PlayVideo()(deps=_deps(tmp_path), query="a documentary")
    assert out["status"] == "home_status_unknown"
    assert house_probe == []


@pytest.mark.asyncio
async def test_an_unauthorized_ha_does_no_house_work(monkeypatch, tmp_path, house_probe):
    """A 401 is an expired token, not a user who left the house."""
    _lan(monkeypatch)
    _ha_answers(monkeypatch, status_code=401)
    out = await PlayVideo()(deps=_deps(tmp_path), query="a documentary")
    assert out["status"] == "home_status_unknown"
    assert house_probe == []


@pytest.mark.asyncio
async def test_a_server_error_from_ha_does_no_house_work(monkeypatch, tmp_path, house_probe):
    """A 5xx is Home Assistant being broken while we sit next to it."""
    _lan(monkeypatch)
    _ha_answers(monkeypatch, status_code=503)
    out = await PlayVideo()(deps=_deps(tmp_path), query="a documentary")
    assert out["status"] == "home_status_unknown"
    assert house_probe == []


@pytest.mark.asyncio
async def test_an_http_timeout_does_no_house_work(monkeypatch, tmp_path, house_probe):
    """The socket connected and then HA went quiet. Still not a location fact."""
    import httpx

    _lan(monkeypatch)
    _ha_answers(monkeypatch, error=httpx.ReadTimeout("timed out"))
    out = await PlayVideo()(deps=_deps(tmp_path), query="a documentary")
    assert out["status"] == "home_status_unknown"
    assert house_probe == []


@pytest.mark.asyncio
async def test_a_dns_failure_does_no_house_work(monkeypatch, tmp_path, house_probe):
    """Round 2, finding 3: name resolution failing is not the user being out."""
    _lan(monkeypatch, reachable=False)
    out = await PlayVideo()(deps=_deps(tmp_path), query="a documentary")
    assert out["status"] == "home_status_unknown"
    assert out["status"] != "away_from_home"
    assert house_probe == []


@pytest.mark.asyncio
async def test_a_refused_connection_does_no_house_work(monkeypatch, tmp_path, house_probe):
    """Round 2, finding 3: a closed port is an HA outage, not absence."""
    _lan(monkeypatch, reachable=False)
    out = await PlayVideo()(deps=_deps(tmp_path), query="a documentary")
    assert out["status"] == "home_status_unknown"
    assert out["status"] != "away_from_home"
    assert house_probe == []


@pytest.mark.asyncio
async def test_a_declared_off_home_address_is_away_and_also_does_no_work(monkeypatch, tmp_path, house_probe):
    """The one case that IS absence: still no side effect, different wording."""
    monkeypatch.setenv("HANOVA_HOME_NETWORKS", "203.0.113.0/24")
    _lan(monkeypatch, reachable=False, local="198.51.100.20")

    async def resolve(host, timeout_s):
        return "198.51.100.20"

    monkeypatch.setattr(home_net, "local_address", resolve)
    out = await PlayVideo()(deps=_deps(tmp_path), query="a documentary")
    assert out == {"status": "away_from_home"}
    assert house_probe == []


@pytest.mark.asyncio
async def test_play_video_casts_the_resolved_youtube_id(monkeypatch, tmp_path):
    """Path A: only an id is handed to HA -- no bytes and no URL of ours."""
    import reachy_companion.tools.play_video as play_video_module

    monkeypatch.setattr(
        play_video_module.ytdlp,
        "search",
        lambda query, max_duration_s=None: {"ok": True, "id": "vid123", "title": "A Film", "error": None},
    )
    seen = {}

    async def fake_run_script(script_name, data, timeout_s=60.0):
        seen["script"] = script_name
        seen["data"] = data
        return {"ok": True, "result": []}

    monkeypatch.setattr(play_video_module, "ha_run_script", fake_run_script)
    out = await PlayVideo()(deps=_deps(tmp_path), query="a documentary")
    assert out["ok"] is True and out["status"] == "casting" and out["title"] == "A Film"
    assert seen["script"] == "tv_show_youtube"
    # Finding 10: the configured cast target is forwarded, not ignored.
    assert seen["data"] == {"youtube_id": "vid123", "entity_id": "media_player.example_tv"}


@pytest.mark.asyncio
async def test_the_cast_entity_is_omitted_when_it_is_not_configured(monkeypatch, tmp_path):
    """It is optional: an HA script with its own target must not get an empty id."""
    import reachy_companion.tools.play_video as play_video_module

    monkeypatch.delenv("HANOVA_CAST_ENTITY")
    monkeypatch.setattr(
        play_video_module.ytdlp,
        "search",
        lambda query, max_duration_s=None: {"ok": True, "id": "vid123", "title": "A Film", "error": None},
    )
    seen = {}

    async def fake_run_script(script_name, data, timeout_s=60.0):
        seen["data"] = data
        return {"ok": True, "result": []}

    monkeypatch.setattr(play_video_module, "ha_run_script", fake_run_script)
    await PlayVideo()(deps=_deps(tmp_path), query="a documentary")
    assert seen["data"] == {"youtube_id": "vid123"}


@pytest.mark.asyncio
async def test_play_video_reports_a_search_failure(monkeypatch, tmp_path):
    """A rate-limited search is a spoken answer, not a stack trace."""
    import reachy_companion.tools.play_video as play_video_module

    monkeypatch.setattr(
        play_video_module.ytdlp,
        "search",
        lambda query, max_duration_s=None: {"ok": False, "id": None, "title": None, "error": "no result"},
    )
    out = await PlayVideo()(deps=_deps(tmp_path), query="nonsense")
    assert out["ok"] is False and out["error"]


@pytest.mark.asyncio
async def test_play_video_surfaces_an_ha_failure(monkeypatch, tmp_path):
    """A missing HA script must be reported, not silently reported as success."""
    import reachy_companion.tools.play_video as play_video_module

    monkeypatch.setattr(
        play_video_module.ytdlp,
        "search",
        lambda query, max_duration_s=None: {"ok": True, "id": "vid123", "title": "A Film", "error": None},
    )

    async def failing_run_script(script_name, data, timeout_s=60.0):
        return {"ok": False, "error": "Home Assistant returned HTTP 400"}

    monkeypatch.setattr(play_video_module, "ha_run_script", failing_run_script)
    out = await PlayVideo()(deps=_deps(tmp_path), query="a documentary")
    assert out["ok"] is False and out["error"]


@pytest.mark.asyncio
async def test_cast_logs_never_carry_the_query(monkeypatch, caplog, tmp_path):
    """Finding 7: what the user asked to watch is not log material."""
    import logging

    import reachy_companion.tools.play_video as play_video_module

    sentinel = "SENTINEL_PRIVATE_x7"
    monkeypatch.setattr(
        play_video_module.ytdlp,
        "search",
        lambda query, max_duration_s=None: {"ok": True, "id": sentinel, "title": sentinel, "error": None},
    )

    async def fake_run_script(script_name, data, timeout_s=60.0):
        return {"ok": True, "result": []}

    monkeypatch.setattr(play_video_module, "ha_run_script", fake_run_script)
    caplog.set_level(logging.DEBUG)
    await PlayVideo()(deps=_deps(tmp_path), query=f"a film about {sentinel}")
    assert sentinel not in caplog.text


@pytest.mark.asyncio
async def test_generate_image_writes_the_decoded_png(monkeypatch, tmp_path):
    """The Images API returns base64; we own decoding and naming."""
    payload = types.SimpleNamespace(data=[types.SimpleNamespace(b64_json=base64.b64encode(PNG_BYTES).decode())])
    client, api = _fake_openai(payload)
    monkeypatch.setattr(images, "build_client", lambda: client)

    out = await images.generate_image("a red bicycle", tmp_path)
    assert out["ok"] is True
    assert out["filename"] is not None and out["filename"].endswith(".png")
    assert (tmp_path / out["filename"]).read_bytes() == PNG_BYTES
    assert api.seen["prompt"] == "a red bicycle"
    assert api.seen["model"] == "gpt-image-1"
    assert client.closed is True, "finding 18: the client must be closed on the success path"


@pytest.mark.asyncio
async def test_the_client_is_closed_even_when_generation_fails(monkeypatch, tmp_path):
    """Finding 18: a leaked connection pool per failed request is a slow leak."""
    client, _ = _fake_openai(error=RuntimeError("rate limited"))
    monkeypatch.setattr(images, "build_client", lambda: client)
    out = await images.generate_image("anything", tmp_path)
    assert out["ok"] is False
    assert client.closed is True


def test_availability_is_a_key_check_that_builds_nothing(monkeypatch):
    """Finding 18: constructing a client to answer a boolean is the bug."""

    def explode():
        raise AssertionError("images_available must not construct a client")

    monkeypatch.setattr(images, "build_client", explode)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert images.images_available() is True
    monkeypatch.delenv("OPENAI_API_KEY")
    assert images.images_available() is False


@pytest.mark.asyncio
async def test_generate_image_without_a_key_is_reported(monkeypatch, tmp_path):
    """No OPENAI_API_KEY is a configuration fact, not a crash."""
    monkeypatch.setattr(images, "build_client", lambda: None)
    out = await images.generate_image("anything", tmp_path)
    assert out["ok"] is False and "OPENAI_API_KEY" in out["error"]


@pytest.mark.asyncio
async def test_generate_image_api_error_does_not_echo_the_prompt(monkeypatch, tmp_path):
    """Finding 7: an Images API error body can quote the prompt straight back."""
    sentinel = "SENTINEL_PRIVATE_x7"
    client, _ = _fake_openai(error=RuntimeError(f"rejected prompt: {sentinel}"))
    monkeypatch.setattr(images, "build_client", lambda: client)
    out = await images.generate_image(sentinel, tmp_path)
    assert out["ok"] is False
    assert sentinel not in out["error"]


@pytest.mark.asyncio
async def test_show_on_tv_is_unavailable_without_an_openai_key(monkeypatch, tmp_path):
    """The image half of the capability has its own credential."""
    monkeypatch.delenv("OPENAI_API_KEY")
    out = await ShowOnTv()(deps=_deps(tmp_path), request="draw a cat")
    assert out == {"status": "unavailable", "reason": "OPENAI_API_KEY"}


@pytest.mark.asyncio
async def test_show_on_tv_is_unavailable_when_the_media_mount_failed(tmp_path):
    """Finding 11: without a live route the TV would fetch nothing."""
    settings.set_media_mount_ready(False)
    out = await ShowOnTv()(deps=_deps(tmp_path), request="draw a cat")
    assert out == {"status": "unavailable", "reason": "HANOVA_MEDIA_MOUNT"}


@pytest.mark.asyncio
async def test_show_on_tv_is_away_from_home_off_the_lan(monkeypatch, tmp_path):
    """R4 again: the TV is at home and so is this capability."""
    _home_state(monkeypatch, "show_on_tv", home_net.AWAY)
    out = await ShowOnTv()(deps=_deps(tmp_path), request="draw a cat")
    assert out == {"status": "away_from_home"}


@pytest.mark.asyncio
async def test_show_on_tv_generates_nothing_when_the_home_verdict_is_unknown(monkeypatch, tmp_path):
    """Round 2, finding 3: UNKNOWN must not spend an Images API call either.

    This is the most expensive UNKNOWN fall-through in the port: the round-1
    shape generated a real image, wrote it to disk and only then failed to cast.
    """
    import reachy_companion.tools.show_on_tv as show_on_tv_module

    _home_state(monkeypatch, "show_on_tv", home_net.UNKNOWN)

    async def fail_generate(prompt, dest_dir):
        raise AssertionError("show_on_tv must not call the Images API on UNKNOWN")

    async def fail_run_script(script_name, data, timeout_s=60.0):
        raise AssertionError("show_on_tv must not touch Home Assistant on UNKNOWN")

    monkeypatch.setattr(show_on_tv_module.images, "generate_image", fail_generate)
    monkeypatch.setattr(show_on_tv_module, "ha_run_script", fail_run_script)
    out = await ShowOnTv()(deps=_deps(tmp_path), request="draw a cat")
    assert out["status"] == "home_status_unknown"
    assert out["status"] != "away_from_home"


@pytest.mark.asyncio
async def test_the_images_layer_logs_no_prompt_or_path(monkeypatch, caplog, tmp_path):
    """Round 2, finding 6: images.py is a service seam and logs like one.

    The write-failure branch used to interpolate the OSError, which renders the
    instance-directory path it failed to write into.
    """
    import logging

    caplog.set_level(logging.DEBUG)
    sentinel = "SENTINEL_PRIVATE_x7"

    client, _ = _fake_openai(error=RuntimeError(f"rejected prompt: {sentinel}"))
    monkeypatch.setattr(images, "build_client", lambda: client)
    out = await images.generate_image(sentinel, tmp_path)
    assert out["ok"] is False
    assert sentinel not in caplog.text
    assert sentinel not in str(out["error"])

    # And the write-failure branch, which used to interpolate the OSError -- an
    # OSError renders the full path it failed on.
    from pathlib import Path as _Path

    def unwritable(self, _data):
        raise OSError(f"read-only filesystem at {sentinel}")

    ok_client, _ = _fake_openai(
        payload=types.SimpleNamespace(
            data=[types.SimpleNamespace(b64_json=base64.b64encode(PNG_BYTES).decode())]
        )
    )
    monkeypatch.setattr(images, "build_client", lambda: ok_client)
    monkeypatch.setattr(_Path, "write_bytes", unwritable)
    out = await images.generate_image("a cat", tmp_path)
    assert out["ok"] is False
    assert sentinel not in caplog.text
    assert sentinel not in str(out["error"])


@pytest.mark.asyncio
async def test_show_on_tv_generates_serves_and_casts(monkeypatch, tmp_path):
    """End to end: generated PNG lands in the cache and its LAN URL is cast."""
    import reachy_companion.tools.show_on_tv as show_on_tv_module

    async def fake_generate(prompt, dest_dir):
        (dest_dir / "img_abc.png").write_bytes(PNG_BYTES)
        return {"ok": True, "path": str(dest_dir / "img_abc.png"), "filename": "img_abc.png", "error": None}

    seen = {}

    async def fake_run_script(script_name, data, timeout_s=60.0):
        seen["script"] = script_name
        seen["data"] = data
        return {"ok": True, "result": []}

    monkeypatch.setattr(show_on_tv_module.images, "generate_image", fake_generate)
    monkeypatch.setattr(show_on_tv_module, "ha_run_script", fake_run_script)

    out = await ShowOnTv()(deps=_deps(tmp_path), request="draw a cat")
    assert out["ok"] is True and out["status"] == "casting"
    assert seen["script"] == "tv_show_image_url"
    assert seen["data"]["url"] == "http://robot.example.invalid:7860/hanova-media/images/img_abc.png"
    assert seen["data"]["media_type"] == "image/png"
    assert seen["data"]["entity_id"] == "media_player.example_tv"
    # Finding 7: the request must not travel into Home Assistant's logbook.
    assert "title" not in seen["data"]


@pytest.mark.asyncio
async def test_show_on_tv_rejects_an_empty_request(tmp_path):
    """An empty prompt must not reach the Images API."""
    out = await ShowOnTv()(deps=_deps(tmp_path), request="   ")
    assert out["ok"] is False


def test_both_tools_reach_the_model_session():
    """The locked profile must list them, or the model never sees them."""
    core_tools = importlib.import_module("reachy_companion.tools.core_tools")
    core_tools.initialize_tools(force=True)
    try:
        names = {spec["name"] for spec in core_tools.get_tool_specs()}
        assert {"play_video", "show_on_tv"} <= names
    finally:
        core_tools._TOOLS_SIGNATURE = None
```

- [ ] **Step 2: Run the test to verify it fails**

```powershell
cd C:\Project\Reachy-mini\reachy_companion
..\.venv\Scripts\python.exe -m pytest tests/test_hanova_cast.py -q
```

Expected: collection error — `ModuleNotFoundError: No module named 'reachy_companion.hanova.images'`.

- [ ] **Step 3: (removed — the script-name accessors landed in Task 1)**

Review finding 6 moved `ha_script_youtube()`, `ha_script_image_url()` and
`ha_script_video_url()` into Task 1 with **empty defaults**, because
`tv_show_youtube` and friends are entry names in the operator's own
`scripts.yaml`, not something this project may assume. They are now prerequisites
in `settings.TOOL_PREREQS`, so a robot whose HA uses different script names gets
`unavailable` with the missing key named, instead of a silent 400 from Home
Assistant. Nothing to do here; confirm the accessors exist and move on.

- [ ] **Step 4: Implement `hanova/images.py`**

Create `reachy_companion/src/reachy_companion/hanova/images.py`:

```python
"""Image generation for show_on_tv (D-018, R2).

Upstream's `show_on_tv` fired an HA `rest_command` at the operator's own Hermes
gateway, which called an image model and copied the result into Home Assistant's
web root (`ha-media-output/SKILL.md:526-545`). None of that is ours to port. We
generate the image here with the OpenAI Images API -- reusing the `OPENAI_API_KEY`
the app already needs for the realtime backend -- write it into the LAN-served
media cache, and cast its URL.
"""

from __future__ import annotations

import os
import uuid
import base64
import logging
from typing import Any, Dict
from pathlib import Path

from reachy_companion.hanova import redact, settings


logger = logging.getLogger(__name__)

# Landscape, because it lands on a television.
_IMAGE_SIZE = "1536x1024"


def images_available() -> bool:
    """Return whether image generation is configured.

    Review finding 18: this is a **key-presence check**. The previous version
    built a whole `AsyncOpenAI` client -- with its own HTTP connection pool --
    purely to answer a boolean, and then dropped it unclosed on every
    availability probe, which happens on every `show_on_tv` call.
    """
    return bool((os.getenv("OPENAI_API_KEY") or "").strip())


def build_client() -> Any | None:
    """Return an AsyncOpenAI client, or None when no API key is configured.

    The caller **must** use it as `async with build_client() as client:` so the
    connection pool is closed on every path, success or failure (finding 18).
    """
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        return None
    try:
        from openai import AsyncOpenAI

        return AsyncOpenAI(api_key=api_key)
    except Exception:  # noqa: BLE001 - a missing SDK must not raise here
        logger.warning("Could not build an OpenAI client for image generation.")
        return None


async def generate_image(prompt: str, dest_dir: Path) -> Dict[str, Any]:
    """Generate one image for *prompt* into *dest_dir*. Never raises."""
    client = build_client()
    if client is None:
        return {"ok": False, "path": None, "filename": None, "error": "OPENAI_API_KEY is not set"}

    try:
        async with client:
            response = await client.images.generate(
                model=settings.image_model(),
                prompt=prompt,
                size=_IMAGE_SIZE,
                n=1,
            )
    except Exception as exc:  # noqa: BLE001 - an API failure is tool output
        # Finding 7: the API error body can echo the prompt back. Log the shape,
        # return a fixed reason.
        logger.warning("Image generation failed: %s", redact.error(exc))
        return {
            "ok": False,
            "path": None,
            "filename": None,
            "error": "the picture could not be generated right now",
        }

    try:
        encoded = response.data[0].b64_json
        raw = base64.b64decode(encoded)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Image response could not be decoded: %s", redact.error(exc))
        return {"ok": False, "path": None, "filename": None, "error": "the image response was unreadable"}

    filename = f"img_{uuid.uuid4().hex[:12]}.png"
    destination = Path(dest_dir) / filename
    try:
        destination.write_bytes(raw)
    except OSError as exc:
        # Round 2, finding 6: an OSError renders the path it failed to write,
        # which is the instance directory. Neither the log nor the tool result
        # may carry it.
        logger.warning("Could not write the generated image: %s", redact.error(exc))
        return {"ok": False, "path": None, "filename": None, "error": "the image could not be saved"}

    return {"ok": True, "path": str(destination), "filename": filename, "error": None}
```

- [ ] **Step 5: Implement `tools/play_video.py`**

Create `reachy_companion/src/reachy_companion/tools/play_video.py`:

```python
"""Cast a video to the living-room TV (D-018, R2/R4). Filename == Tool.name.

This is upstream's "Path A": yt-dlp resolves the query to a YouTube id, and the
id alone is handed to a Home Assistant script that launches the TV's own YouTube
app. No bytes and no URL of ours ever leave the robot, so this works identically
from a Raspberry Pi as it did from the operator's Mac -- as long as the robot is
on the home LAN, which is what `home_state()` decides.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict

from reachy_companion.home_net import AWAY, HOME, home_state, home_unknown, away_from_home
from reachy_companion.hanova import ytdlp, redact, settings
from reachy_companion.hanova.ha_client import ha_run_script
from reachy_companion.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


class PlayVideo(Tool):
    """Search YouTube and cast the result to the TV."""

    name = "play_video"
    description = "Cast a video to the living-room TV. 用於在電視上播放影片。"
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "What to watch: a title, topic, or channel.",
            },
        },
        "required": ["query"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Resolve the query to a video id and ask Home Assistant to cast it."""
        available, reason = settings.tool_status(self.name)
        if not available:
            return settings.unavailable(reason)
        # Finding 12 + round 2 finding 3: three verdicts, three branches.
        # AWAY is proven absence and says so. UNKNOWN (401, HA down, a VPN, a
        # refused port) means we cannot tell -- so nothing is resolved and
        # nothing is cast, and the answer is its own status rather than a lie in
        # either direction.
        verdict = await home_state()
        if verdict == AWAY:
            return away_from_home()
        if verdict != HOME:
            return home_unknown()

        query = str(kwargs.get("query", "")).strip()
        if not query:
            return {"ok": False, "error": "query is required"}

        logger.info("Tool call: play_video query=%s", redact.text(query))
        found = await asyncio.to_thread(ytdlp.search, query, None)
        if not found["ok"]:
            logger.info("play_video search failed: %s", redact.error(found["error"] or ""))
            return {"ok": False, "error": "no playable result for that request"}

        fields: Dict[str, Any] = {"youtube_id": found["id"]}
        entity = settings.cast_entity()
        if entity:
            fields["entity_id"] = entity
        cast = await ha_run_script(settings.ha_script_youtube(), fields)
        if not cast["ok"]:
            logger.info("play_video cast failed: %s", redact.error(cast.get("error") or ""))
            return {"ok": False, "error": "Home Assistant could not start the video on the TV"}
        return {"ok": True, "status": "casting", "title": found["title"], "video_id": found["id"]}
```

- [ ] **Step 6: Implement `tools/show_on_tv.py`**

Create `reachy_companion/src/reachy_companion/tools/show_on_tv.py`:

```python
"""Generate a picture and put it on the TV (D-018, R2/R4). Filename == Tool.name.

The image is generated with the OpenAI Images API, written into the LAN-served
media cache, and cast by URL through a Home Assistant script. The TV fetches the
URL itself, so the base URL must be the robot's own LAN address -- which is what
`HANOVA_MEDIA_HTTP_BASE` is for.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from reachy_companion.home_net import AWAY, HOME, home_state, home_unknown, away_from_home
from reachy_companion.hanova import images, redact, settings, media_store
from reachy_companion.hanova.ha_client import ha_run_script
from reachy_companion.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)

_IMAGE_CAST_TIMEOUT_S = 60.0


class ShowOnTv(Tool):
    """Draw something and show it on the living-room TV."""

    name = "show_on_tv"
    description = "Draw a picture and show it on the TV. 用於把畫面或圖片放到電視上。"
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "request": {
                "type": "string",
                "description": "What the picture should show.",
            },
        },
        "required": ["request"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Generate an image, publish it on the LAN, and cast it to the TV."""
        # `OPENAI_API_KEY` and the live media mount are both prerequisites in
        # settings.TOOL_PREREQS, so no client is constructed to answer this
        # (findings 10, 11 and 18).
        available, reason = settings.tool_status(self.name)
        if not available:
            return settings.unavailable(reason)
        # Round 2, finding 3: UNKNOWN does no work. That matters most here --
        # the round-1 shape generated a real (billed) image and wrote it to disk
        # before discovering it could not cast it.
        verdict = await home_state()
        if verdict == AWAY:
            return away_from_home()
        if verdict != HOME:
            return home_unknown()

        request = str(kwargs.get("request", "")).strip()
        if not request:
            return {"ok": False, "error": "request is required"}

        logger.info("Tool call: show_on_tv request=%s", redact.text(request))
        images_dir = media_store.media_dir("images", deps.instance_path)
        generated = await images.generate_image(request, images_dir)
        if not generated["ok"]:
            return {"ok": False, "error": generated["error"]}

        url = media_store.media_url("images", str(generated["filename"]))
        if url is None:
            return {"ok": False, "error": "HANOVA_MEDIA_HTTP_BASE is not set; the TV has no URL to fetch."}

        fields: Dict[str, Any] = {"url": url, "media_type": "image/png"}
        entity = settings.cast_entity()
        if entity:
            fields["entity_id"] = entity
        cast = await ha_run_script(
            settings.ha_script_image_url(),
            fields,
            timeout_s=_IMAGE_CAST_TIMEOUT_S,
        )
        media_store.prune("images", deps.instance_path, settings.image_keep())
        if not cast["ok"]:
            logger.info("show_on_tv cast failed: %s", redact.error(cast.get("error") or ""))
            return {"ok": False, "error": "Home Assistant could not put the picture on the TV"}
        return {"ok": True, "status": "casting", "url": url}
```

**Note.** The cast payload no longer carries `"title": request[:60]` — the
request is the user's own words and would have travelled into Home Assistant's
own logbook and recorder database (finding 7). The picture needs no caption.

- [ ] **Step 7: Enable both tools in the locked profile**

In `reachy_companion/profiles/_reachy_companion_locked_profile/profile.md`, add to `default_tools` immediately after `"stop_music",`:

```toml
  "stop_music",
  "play_video",
  "show_on_tv",
```

The three `HANOVA_HA_SCRIPT_*` keys are already documented in `.env.example`
from Task 1 (review finding 6). `.env.example` is **not** touched in this task.

- [ ] **Step 8: Run the test to verify it passes**

```powershell
cd C:\Project\Reachy-mini\reachy_companion
..\.venv\Scripts\python.exe -m pytest tests/test_hanova_cast.py -q
```

Expected: green — **31** test functions, none parametrised: **31 collected
cases**, including the six no-side-effect cases round 2 finding 3 requires and
the images caplog sentinel. Record the exact number.

- [ ] **Step 9: Run the full gate**

```powershell
cd C:\Project\Reachy-mini\reachy_companion
..\.venv\Scripts\python.exe -m pytest -q
..\.venv\Scripts\python.exe -m ruff check src tests
..\.venv\Scripts\python.exe -m ruff format --check src tests
..\.venv\Scripts\python.exe -m mypy
```

Expected: pytest green (30 documented skips), ruff clean, mypy `Success: no issues found`.

- [ ] **Step 10: Commit**

```bash
cd /c/Project/Reachy-mini
git rev-parse --abbrev-ref HEAD   # must print feat/ha-nova-port (Task 0)
git add reachy_companion/src/reachy_companion/hanova/images.py \
        reachy_companion/src/reachy_companion/tools/play_video.py \
        reachy_companion/src/reachy_companion/tools/show_on_tv.py \
        reachy_companion/profiles/_reachy_companion_locked_profile/profile.md \
        reachy_companion/tests/test_hanova_cast.py
git commit -m "feat(hanova): play_video and show_on_tv with home-network gating"
```

---

### Task 7: Google OAuth layer and the three calendar tools

Implements R1 (adapt the small upstream modules, drop the `localhost:18790` shim and the five subprocess CLIs entirely), R3 (`calendar_delete` is gated), R5 and R11.

**Review round 1, finding 14 — the credential file needs a lock and an atomic
write.** Calendar and Tasks share one `<account>.json`, and both are dispatched
as independent asyncio tasks onto worker threads. Two of them arriving on an
expired token would both refresh, both write through the *same fixed* temp
filename `<account>.json.tmp`, and race: duplicate refresh round trips, a
`replace()` onto a half-written file, or the second refresh silently discarding
the first. The permissions were also applied *after* the content was written, so
the refresh token sat world-readable for a moment on every refresh. Fixed with a
per-path `threading.Lock`, `tempfile.mkstemp(dir=..., mode 0600 at creation)`,
an `fsync` before the rename, and `os.replace`.

**Findings 4 and 10 also land here** and set the pattern every later gated tool
follows: `claim`/`complete`/`release` instead of a destructive `take()`, action
fields optional at the JSON-Schema level but mandatory in the non-confirm branch,
and `settings.tool_status(self.name)` instead of a family-wide check.

**Files:**
- Create: `reachy_companion/src/reachy_companion/hanova/sync_http.py`
- Create: `reachy_companion/src/reachy_companion/hanova/gauth.py`
- Create: `reachy_companion/src/reachy_companion/hanova/gcal.py`
- Create: `reachy_companion/src/reachy_companion/tools/calendar_add.py`
- Create: `reachy_companion/src/reachy_companion/tools/calendar_list.py`
- Create: `reachy_companion/src/reachy_companion/tools/calendar_delete.py`
- Modify: `reachy_companion/profiles/_reachy_companion_locked_profile/profile.md` (`default_tools` gains the three names)
- Test: `reachy_companion/tests/test_hanova_gauth.py`
- Test: `reachy_companion/tests/test_hanova_calendar.py`

**Interfaces:**
- Consumes: `hanova.settings.{google_account, google_creds_dir, gcal_calendar_id, timezone_name, cal_delete_window_days, tool_status, unavailable}`; `hanova.redact`; `hanova.confirm.{GATE, confirmation_expired}` — `GATE.claim` / `GATE.complete` / `GATE.release`, never a destructive `take()` (finding 4).
- Produces:
  - `hanova.sync_http.request_bytes(method: str, url: str, headers: Dict[str, str], data: bytes | None = None, timeout_s: int = 15) -> tuple[int, bytes]` — the single blocking-HTTP seam every sync service module calls **through the module object** (`sync_http.request_bytes(...)`, never `from ... import request_bytes`) so one monkeypatch covers all of them.
  - `hanova.gauth.GoogleApiError(RuntimeError)` with attributes `status: int`, `body: Dict[str, Any]`
  - `hanova.gauth.get_access_token(account: str | None = None) -> str`
  - `hanova.gauth.api_call(method: str, url: str, body: Dict[str, Any] | None = None, query: Dict[str, Any] | None = None, account: str | None = None) -> Dict[str, Any]`
  - `hanova.gcal.CAL_BASE: str`
  - `hanova.gcal.create_event(calendar_id: str, summary: str, start: str, end: str, timezone_name: str, location: str | None = None, description: str | None = None) -> Dict[str, Any]`
  - `hanova.gcal.list_events(calendar_id: str, time_min: str, time_max: str, limit: int = 25, search: str | None = None) -> list[Dict[str, Any]]`
  - `hanova.gcal.delete_event(calendar_id: str, event_id: str) -> None`
  - `hanova.gcal.find_event(calendar_id: str, match: str, window_days: int) -> tuple[Dict[str, Any] | None, list[Dict[str, Any]], str | None]` — error is `"not_found"` or `"ambiguous"`
  - `hanova.gcal.event_when(event: Dict[str, Any]) -> str` — human-readable start for the confirmation read-back
  - `tools.calendar_add.CalendarAdd`, `tools.calendar_list.CalendarList`, `tools.calendar_delete.CalendarDelete`

- [ ] **Step 1: Write the failing tests**

Create `reachy_companion/tests/test_hanova_gauth.py`:

```python
"""Contract tests for the adapted Google OAuth layer (D-018, R1). No network.

Also pins review round 1 finding 14: one lock per credentials file, an atomic
0600 write, and no duplicate refresh when Calendar and Tasks arrive together.
"""

import os
import json
import stat
import time
import threading
from datetime import datetime, timezone, timedelta

import pytest

from reachy_companion.hanova import gauth, sync_http


ACCOUNT = "someone@example.com"


def _creds(expiry: datetime) -> dict:
    return {
        "client_id": "cid",
        "client_secret": "csecret",
        "refresh_token": "rtok",
        "token": "old-access-token",
        "token_uri": "https://oauth2.googleapis.com/token",
        "expiry": expiry.strftime("%Y-%m-%dT%H:%M:%S"),
    }


@pytest.fixture
def creds_file(monkeypatch, tmp_path):
    """A writable credentials directory, as the robot instance dir will be."""
    monkeypatch.setenv("GOOGLE_CREDS_DIR", str(tmp_path))
    monkeypatch.setenv("HANOVA_GOOGLE_ACCOUNT", ACCOUNT)
    path = tmp_path / f"{ACCOUNT}.json"
    path.write_text(json.dumps(_creds(datetime.now(timezone.utc) + timedelta(hours=1))), encoding="utf-8")
    return path


def test_valid_token_is_reused_without_a_refresh(monkeypatch, creds_file):
    """An unexpired token must not cost a round trip on every tool call."""

    def fail(*args, **kwargs):
        raise AssertionError("gauth must not refresh a valid token")

    monkeypatch.setattr(sync_http, "request_bytes", fail)
    assert gauth.get_access_token() == "old-access-token"


def test_expired_token_is_refreshed_and_rewritten(monkeypatch, creds_file):
    """gauth rewrites the credentials file, so the robot needs its own copy."""
    creds_file.write_text(
        json.dumps(_creds(datetime.now(timezone.utc) - timedelta(hours=1))), encoding="utf-8"
    )
    seen = {}

    def fake_request(method, url, headers, data=None, timeout_s=15):
        seen["url"] = url
        return 200, json.dumps({"access_token": "new-access-token", "expires_in": 3600}).encode()

    monkeypatch.setattr(sync_http, "request_bytes", fake_request)
    assert gauth.get_access_token() == "new-access-token"
    assert seen["url"] == "https://oauth2.googleapis.com/token"
    assert json.loads(creds_file.read_text(encoding="utf-8"))["token"] == "new-access-token"


def test_the_rewritten_file_is_owner_only(monkeypatch, creds_file):
    """Finding 14: the refresh token must never be world-readable, not even briefly."""
    creds_file.write_text(
        json.dumps(_creds(datetime.now(timezone.utc) - timedelta(hours=1))), encoding="utf-8"
    )
    monkeypatch.setattr(
        sync_http,
        "request_bytes",
        lambda *a, **k: (200, json.dumps({"access_token": "new", "expires_in": 3600}).encode()),
    )
    gauth.get_access_token()
    if os.name != "nt":  # POSIX permission bits are meaningless on Windows
        assert stat.S_IMODE(creds_file.stat().st_mode) == 0o600


def test_no_stray_temp_file_is_left_behind(monkeypatch, creds_file, tmp_path):
    """The atomic write must not litter the credentials directory."""
    creds_file.write_text(
        json.dumps(_creds(datetime.now(timezone.utc) - timedelta(hours=1))), encoding="utf-8"
    )
    monkeypatch.setattr(
        sync_http,
        "request_bytes",
        lambda *a, **k: (200, json.dumps({"access_token": "new", "expires_in": 3600}).encode()),
    )
    gauth.get_access_token()
    assert [p.name for p in tmp_path.iterdir()] == [creds_file.name]


def test_simultaneous_expired_calls_refresh_exactly_once(monkeypatch, creds_file):
    """Finding 14: Calendar and Tasks arriving together must not both refresh.

    The loser of the lock re-reads the file inside the critical section, finds a
    valid token, and issues no request of its own.
    """
    creds_file.write_text(
        json.dumps(_creds(datetime.now(timezone.utc) - timedelta(hours=1))), encoding="utf-8"
    )
    refreshes = {"n": 0}
    barrier = threading.Barrier(2)

    def slow_refresh(method, url, headers, data=None, timeout_s=15):
        refreshes["n"] += 1
        time.sleep(0.05)  # widen the window the old code raced inside
        return 200, json.dumps({"access_token": "new-access-token", "expires_in": 3600}).encode()

    monkeypatch.setattr(sync_http, "request_bytes", slow_refresh)

    tokens: list[str] = []

    def worker() -> None:
        barrier.wait()
        tokens.append(gauth.get_access_token())

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert tokens == ["new-access-token", "new-access-token"]
    assert refreshes["n"] == 1
    assert json.loads(creds_file.read_text(encoding="utf-8"))["token"] == "new-access-token"


def test_missing_credentials_file_names_the_path(monkeypatch, tmp_path):
    """The operator must be told exactly which file to copy to the robot."""
    monkeypatch.setenv("GOOGLE_CREDS_DIR", str(tmp_path))
    monkeypatch.setenv("HANOVA_GOOGLE_ACCOUNT", ACCOUNT)
    with pytest.raises(FileNotFoundError) as excinfo:
        gauth.get_access_token()
    assert ACCOUNT in str(excinfo.value)


def test_api_call_sends_the_bearer_and_parses_json(monkeypatch, creds_file):
    """The whole point of the layer: one authenticated JSON call."""
    seen = {}

    def fake_request(method, url, headers, data=None, timeout_s=15):
        seen.update(method=method, url=url, headers=headers, data=data)
        return 200, b'{"id": "evt1"}'

    monkeypatch.setattr(sync_http, "request_bytes", fake_request)
    out = gauth.api_call("POST", "https://api.example.invalid/v3/events", body={"summary": "x"})
    assert out == {"id": "evt1"}
    assert seen["headers"]["Authorization"] == "Bearer old-access-token"
    assert seen["headers"]["Content-Type"] == "application/json"
    assert json.loads(seen["data"]) == {"summary": "x"}


def test_api_call_appends_the_query_string(monkeypatch, creds_file):
    """Query params must be encoded, and None values dropped."""
    seen = {}

    def fake_request(method, url, headers, data=None, timeout_s=15):
        seen["url"] = url
        return 200, b"{}"

    monkeypatch.setattr(sync_http, "request_bytes", fake_request)
    gauth.api_call("GET", "https://api.example.invalid/v3/events", query={"maxResults": 5, "q": None})
    assert seen["url"] == "https://api.example.invalid/v3/events?maxResults=5"


def test_api_call_refreshes_once_on_401(monkeypatch, creds_file):
    """A token that went stale mid-session must not surface as a tool failure."""
    calls: list[str] = []

    def fake_request(method, url, headers, data=None, timeout_s=15):
        calls.append(url)
        if url.endswith("/token"):
            return 200, json.dumps({"access_token": "fresh", "expires_in": 3600}).encode()
        if len([c for c in calls if not c.endswith("/token")]) == 1:
            return 401, b'{"error": {"message": "Invalid Credentials"}}'
        return 200, b'{"ok": true}'

    monkeypatch.setattr(sync_http, "request_bytes", fake_request)
    assert gauth.api_call("GET", "https://api.example.invalid/v3/events") == {"ok": True}


def test_api_call_raises_google_api_error_on_failure(monkeypatch, creds_file):
    """Callers need the status code to decide what to say."""

    def fake_request(method, url, headers, data=None, timeout_s=15):
        return 404, b'{"error": {"message": "Not Found"}}'

    monkeypatch.setattr(sync_http, "request_bytes", fake_request)
    with pytest.raises(gauth.GoogleApiError) as excinfo:
        gauth.api_call("GET", "https://api.example.invalid/v3/events/x")
    assert excinfo.value.status == 404
    assert "Not Found" in str(excinfo.value)
```

Create `reachy_companion/tests/test_hanova_calendar.py`:

```python
"""Contract tests for the three calendar tools (D-018, R1/R3/R5/R11)."""

import types
import importlib
from typing import Any

import pytest

from reachy_companion.hanova import gcal
from reachy_companion.hanova.confirm import GATE
from reachy_companion.tools.calendar_add import CalendarAdd
from reachy_companion.tools.calendar_list import CalendarList
from reachy_companion.tools.calendar_delete import CalendarDelete


CALENDAR_ID = "calendar-under-test@example.invalid"


def _deps():
    return types.SimpleNamespace(reachy_mini=None, instance_path=None)


@pytest.fixture(autouse=True)
def configured(monkeypatch, tmp_path):
    """A configured google-workspace family and an empty confirmation gate."""
    creds_dir = tmp_path / "google-workspace-mcp"
    creds_dir.mkdir()
    (creds_dir / "someone@example.com.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("GOOGLE_CREDS_DIR", str(creds_dir))
    monkeypatch.setenv("HANOVA_GOOGLE_ACCOUNT", "someone@example.com")
    monkeypatch.setenv("HANOVA_GCAL_CALENDAR_ID", CALENDAR_ID)
    monkeypatch.delenv("HANOVA_TZ", raising=False)
    monkeypatch.delenv("HANOVA_CONFIRM_TTL_S", raising=False)
    GATE.reset()
    GATE.begin_session()
    yield
    GATE.reset()


def test_tool_names_match_their_filenames():
    """The loader resolves tools by filename == Tool.name."""
    assert CalendarAdd.name == "calendar_add"
    assert CalendarList.name == "calendar_list"
    assert CalendarDelete.name == "calendar_delete"


def test_descriptions_carry_no_personal_identifier():
    """R10: upstream leaked the real calendar address into this description."""
    for text in (CalendarAdd().description, CalendarList().description, CalendarDelete().description):
        assert "@" not in text
        assert CALENDAR_ID not in text
        assert len(text) <= 120


def test_create_event_uses_the_configured_timezone(monkeypatch):
    """R11: Asia/Taipei comes from config, never from a literal in the code."""
    seen = {}

    def fake_api_call(method, url, body=None, query=None, account=None):
        seen.update(method=method, url=url, body=body)
        return {"id": "evt1", "summary": "Dinner", "htmlLink": "https://example.invalid/e"}

    monkeypatch.setattr(gcal.gauth, "api_call", fake_api_call)
    monkeypatch.setenv("HANOVA_TZ", "Europe/Paris")
    gcal.create_event(CALENDAR_ID, "Dinner", "2026-09-02T19:00:00+02:00", "2026-09-02T20:30:00+02:00", "Europe/Paris")
    assert seen["method"] == "POST"
    assert seen["url"] == f"{gcal.CAL_BASE}/calendars/{CALENDAR_ID}/events"
    assert seen["body"]["start"] == {"dateTime": "2026-09-02T19:00:00+02:00", "timeZone": "Europe/Paris"}


def test_list_events_builds_the_expected_query(monkeypatch):
    """singleEvents + orderBy is what makes recurring events readable."""
    seen = {}

    def fake_api_call(method, url, body=None, query=None, account=None):
        seen.update(url=url, query=query)
        return {"items": []}

    monkeypatch.setattr(gcal.gauth, "api_call", fake_api_call)
    gcal.list_events(CALENDAR_ID, "2026-09-01T00:00:00Z", "2026-09-08T00:00:00Z", limit=10, search="dentist")
    assert seen["query"]["singleEvents"] == "true"
    assert seen["query"]["orderBy"] == "startTime"
    assert seen["query"]["maxResults"] == 10
    assert seen["query"]["q"] == "dentist"


def _events(*summaries: str) -> dict:
    return {
        "items": [
            {"id": f"evt{index}", "summary": summary, "start": {"dateTime": "2026-09-02T19:00:00+08:00"}}
            for index, summary in enumerate(summaries)
        ]
    }


def test_find_event_matches_case_insensitively(monkeypatch):
    """Voice input has no case; a substring match must not care either."""
    monkeypatch.setattr(gcal.gauth, "api_call", lambda *a, **k: _events("Dentist Appointment", "Gym"))
    event, candidates, error = gcal.find_event(CALENDAR_ID, "dentist", 14)
    assert error is None and candidates == []
    assert event is not None and event["id"] == "evt0"


def test_find_event_reports_ambiguity(monkeypatch):
    """Two matches must never be resolved by guessing."""
    monkeypatch.setattr(gcal.gauth, "api_call", lambda *a, **k: _events("Dentist A", "Dentist B"))
    event, candidates, error = gcal.find_event(CALENDAR_ID, "dentist", 14)
    assert event is None and error == "ambiguous" and len(candidates) == 2


def test_find_event_reports_no_match(monkeypatch):
    """Zero matches is a clean answer, not an exception."""
    monkeypatch.setattr(gcal.gauth, "api_call", lambda *a, **k: _events("Gym"))
    event, candidates, error = gcal.find_event(CALENDAR_ID, "dentist", 14)
    assert event is None and error == "not_found"


@pytest.mark.asyncio
async def test_calendar_add_is_unavailable_without_credentials(monkeypatch):
    """R5: no credentials directory means the tool is off, and it says which key."""
    monkeypatch.delenv("GOOGLE_CREDS_DIR")
    out = await CalendarAdd()(deps=_deps(), summary="Dinner", start="2026-09-02T19:00:00+08:00", end="2026-09-02T20:30:00+08:00")
    assert out == {"status": "unavailable", "reason": "GOOGLE_CREDS_DIR"}


@pytest.mark.asyncio
async def test_calendar_tools_are_unavailable_without_a_calendar_id(monkeypatch):
    """Finding 10: the old family gate ignored the calendar id entirely."""
    monkeypatch.delenv("HANOVA_GCAL_CALENDAR_ID")
    out = await CalendarList()(deps=_deps())
    assert out == {"status": "unavailable", "reason": "HANOVA_GCAL_CALENDAR_ID"}


@pytest.mark.asyncio
async def test_calendar_add_creates_and_reports_the_event(monkeypatch):
    """A real artifact grounds what Reachy says next."""
    import reachy_companion.tools.calendar_add as calendar_add_module

    monkeypatch.setattr(
        calendar_add_module.gcal,
        "create_event",
        lambda **kwargs: {"id": "evt1", "summary": "Dinner", "htmlLink": "https://example.invalid/e"},
    )
    out = await CalendarAdd()(
        deps=_deps(), summary="Dinner", start="2026-09-02T19:00:00+08:00", end="2026-09-02T20:30:00+08:00"
    )
    assert out["ok"] is True and out["event_id"] == "evt1"


@pytest.mark.asyncio
async def test_calendar_add_reports_an_api_error_without_echoing_it(monkeypatch, caplog):
    """A Google failure is tool output -- but finding 7 says not *that* output.

    Google's error bodies quote the request back, so the summary the user
    dictated would otherwise reach the log and the model's mouth.
    """
    import logging

    import reachy_companion.tools.calendar_add as calendar_add_module

    sentinel = "SENTINEL_PRIVATE_x7"

    def boom(**kwargs):
        raise calendar_add_module.GoogleApiError(
            403,
            {"error": {"message": f"forbidden for {sentinel}"}},
            url=f"https://www.googleapis.com/calendar/v3/calendars/{sentinel}/events",
            method="POST",
        )

    monkeypatch.setattr(calendar_add_module.gcal, "create_event", boom)
    caplog.set_level(logging.DEBUG)
    out = await CalendarAdd()(
        deps=_deps(), summary=f"Dinner with {sentinel}", start="2026-09-02T19:00:00+08:00", end="2026-09-02T20:30:00+08:00"
    )
    assert out["ok"] is False and out["error"]
    assert sentinel not in out["error"]
    assert sentinel not in caplog.text


@pytest.mark.asyncio
async def test_calendar_list_returns_compact_events(monkeypatch):
    """The model gets titles and times, not raw Google payloads."""
    import reachy_companion.tools.calendar_list as calendar_list_module

    monkeypatch.setattr(
        calendar_list_module.gcal,
        "list_events",
        lambda **kwargs: [
            {"id": "evt1", "summary": "Dentist", "start": {"dateTime": "2026-09-02T19:00:00+08:00"}, "end": {}}
        ],
    )
    out = await CalendarList()(deps=_deps(), days=7)
    assert out["ok"] is True and out["count"] == 1
    assert out["events"][0]["summary"] == "Dentist"


@pytest.mark.asyncio
async def test_calendar_delete_arms_before_it_deletes(monkeypatch):
    """R3: the first call reads the exact event back and deletes nothing."""
    import reachy_companion.tools.calendar_delete as calendar_delete_module

    monkeypatch.setattr(
        calendar_delete_module.gcal,
        "find_event",
        lambda calendar_id, match, window_days: (
            {"id": "evt1", "summary": "Dentist", "start": {"dateTime": "2026-09-02T19:00:00+08:00"}},
            [],
            None,
        ),
    )

    def fail_delete(calendar_id, event_id):
        raise AssertionError("calendar_delete must not delete before confirmation")

    monkeypatch.setattr(calendar_delete_module.gcal, "delete_event", fail_delete)
    out = await CalendarDelete()(deps=_deps(), match="dentist")
    assert out["status"] == "needs_confirmation"
    assert "Dentist" in out["summary"] and "2026-09-02" in out["summary"]


@pytest.mark.asyncio
async def test_calendar_delete_executes_the_armed_payload(monkeypatch):
    """The confirmed delete uses what was read back, not the second call's args."""
    import reachy_companion.tools.calendar_delete as calendar_delete_module

    monkeypatch.setattr(
        calendar_delete_module.gcal,
        "find_event",
        lambda calendar_id, match, window_days: (
            {"id": "evt1", "summary": "Dentist", "start": {"dateTime": "2026-09-02T19:00:00+08:00"}},
            [],
            None,
        ),
    )
    deleted = {}
    monkeypatch.setattr(
        calendar_delete_module.gcal,
        "delete_event",
        lambda calendar_id, event_id: deleted.update(calendar_id=calendar_id, event_id=event_id),
    )
    await CalendarDelete()(deps=_deps(), match="dentist")
    out = await CalendarDelete()(deps=_deps(), match="something else entirely", confirm=True)
    assert out["ok"] is True and out["status"] == "deleted"
    assert deleted == {"calendar_id": CALENDAR_ID, "event_id": "evt1"}


@pytest.mark.asyncio
async def test_confirming_needs_no_match_at_all(monkeypatch):
    """Finding 4: the schema must not force the model to resupply the frozen field."""
    import reachy_companion.tools.calendar_delete as calendar_delete_module

    assert calendar_delete_module.CalendarDelete.parameters_schema["required"] == []

    monkeypatch.setattr(
        calendar_delete_module.gcal,
        "find_event",
        lambda calendar_id, match, window_days: (
            {"id": "evt1", "summary": "Dentist", "start": {"dateTime": "2026-09-02T19:00:00+08:00"}},
            [],
            None,
        ),
    )
    deleted = {}
    monkeypatch.setattr(
        calendar_delete_module.gcal,
        "delete_event",
        lambda calendar_id, event_id: deleted.update(event_id=event_id),
    )
    await CalendarDelete()(deps=_deps(), match="dentist")
    out = await CalendarDelete()(deps=_deps(), confirm=True)  # no `match` at all
    assert out["ok"] is True and deleted == {"event_id": "evt1"}


@pytest.mark.asyncio
async def test_a_transient_failure_keeps_the_authorisation(monkeypatch):
    """Finding 4: a 503 must not cost the user their confirmation."""
    import reachy_companion.tools.calendar_delete as calendar_delete_module

    monkeypatch.setattr(
        calendar_delete_module.gcal,
        "find_event",
        lambda calendar_id, match, window_days: (
            {"id": "evt1", "summary": "Dentist", "start": {"dateTime": "2026-09-02T19:00:00+08:00"}},
            [],
            None,
        ),
    )
    attempts = {"n": 0}

    def flaky(calendar_id, event_id):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise calendar_delete_module.GoogleApiError(503, {}, url="https://x.invalid/e", method="DELETE")

    monkeypatch.setattr(calendar_delete_module.gcal, "delete_event", flaky)
    await CalendarDelete()(deps=_deps(), match="dentist")
    first = await CalendarDelete()(deps=_deps(), confirm=True)
    assert first["ok"] is False and first.get("retryable") is True
    second = await CalendarDelete()(deps=_deps(), confirm=True)
    assert second["ok"] is True and second["status"] == "deleted"


@pytest.mark.asyncio
async def test_a_permanent_failure_spends_the_authorisation(monkeypatch):
    """A 404 means the resolved action is wrong; re-confirm from scratch."""
    import reachy_companion.tools.calendar_delete as calendar_delete_module

    monkeypatch.setattr(
        calendar_delete_module.gcal,
        "find_event",
        lambda calendar_id, match, window_days: (
            {"id": "evt1", "summary": "Dentist", "start": {"dateTime": "2026-09-02T19:00:00+08:00"}},
            [],
            None,
        ),
    )

    def gone(calendar_id, event_id):
        raise calendar_delete_module.GoogleApiError(404, {}, url="https://x.invalid/e", method="DELETE")

    monkeypatch.setattr(calendar_delete_module.gcal, "delete_event", gone)
    await CalendarDelete()(deps=_deps(), match="dentist")
    assert (await CalendarDelete()(deps=_deps(), confirm=True))["ok"] is False
    assert (await CalendarDelete()(deps=_deps(), confirm=True))["status"] == "confirmation_expired"


@pytest.mark.asyncio
async def test_a_confirmation_does_not_survive_a_new_session(monkeypatch):
    """Finding 3: a backend reconnect must invalidate everything armed before it."""
    import reachy_companion.tools.calendar_delete as calendar_delete_module

    monkeypatch.setattr(
        calendar_delete_module.gcal,
        "find_event",
        lambda calendar_id, match, window_days: (
            {"id": "evt1", "summary": "Dentist", "start": {"dateTime": "2026-09-02T19:00:00+08:00"}},
            [],
            None,
        ),
    )

    def fail_delete(calendar_id, event_id):
        raise AssertionError("a confirmation from a previous session must not execute")

    monkeypatch.setattr(calendar_delete_module.gcal, "delete_event", fail_delete)
    await CalendarDelete()(deps=_deps(), match="dentist")
    GATE.begin_session()
    out = await CalendarDelete()(deps=_deps(), confirm=True)
    assert out["status"] == "confirmation_expired"


@pytest.mark.asyncio
async def test_calendar_delete_confirm_without_arm_is_refused(monkeypatch):
    """A confirm:true first call must delete nothing."""
    import reachy_companion.tools.calendar_delete as calendar_delete_module

    def fail_delete(calendar_id, event_id):
        raise AssertionError("calendar_delete must not delete without a pending action")

    monkeypatch.setattr(calendar_delete_module.gcal, "delete_event", fail_delete)
    out = await CalendarDelete()(deps=_deps(), match="dentist", confirm=True)
    assert out["status"] == "confirmation_expired"


@pytest.mark.asyncio
async def test_calendar_delete_refuses_an_ambiguous_match(monkeypatch):
    """Two candidates arm nothing and are handed back for the user to pick."""
    import reachy_companion.tools.calendar_delete as calendar_delete_module

    monkeypatch.setattr(
        calendar_delete_module.gcal,
        "find_event",
        lambda calendar_id, match, window_days: (
            None,
            [
                {"id": "evt1", "summary": "Dentist A", "start": {"dateTime": "2026-09-02T19:00:00+08:00"}},
                {"id": "evt2", "summary": "Dentist B", "start": {"dateTime": "2026-09-03T19:00:00+08:00"}},
            ],
            "ambiguous",
        ),
    )
    out = await CalendarDelete()(deps=_deps(), match="dentist")
    assert out["ok"] is False and out["error"] == "ambiguous"
    assert len(out["candidates"]) == 2
    assert GATE.claim("calendar_delete") is None


@pytest.mark.asyncio
async def test_calendar_delete_rejects_a_too_short_match():
    """A two-character floor is the minimum defence over a noisy STT channel."""
    out = await CalendarDelete()(deps=_deps(), match="d")
    assert out["ok"] is False


def test_all_three_tools_reach_the_model_session():
    """The locked profile must list them, or the model never sees them."""
    core_tools = importlib.import_module("reachy_companion.tools.core_tools")
    core_tools.initialize_tools(force=True)
    try:
        names = {spec["name"] for spec in core_tools.get_tool_specs()}
        assert {"calendar_add", "calendar_list", "calendar_delete"} <= names
    finally:
        core_tools._TOOLS_SIGNATURE = None
```

- [ ] **Step 2: Run the tests to verify they fail**

```powershell
cd C:\Project\Reachy-mini\reachy_companion
..\.venv\Scripts\python.exe -m pytest tests/test_hanova_gauth.py tests/test_hanova_calendar.py -q
```

Expected: collection errors for `reachy_companion.hanova.sync_http`, `...gauth`, `...gcal`, and the three tool modules.

- [ ] **Step 3: Implement `hanova/sync_http.py`**

Create `reachy_companion/src/reachy_companion/hanova/sync_http.py`:

```python
"""The one blocking-HTTP call every synchronous service module makes (D-018).

Google, Notion and Drive are ported from stdlib `urllib` code and stay
synchronous; tools call them through `asyncio.to_thread`. Routing all of them
through this single function means one monkeypatch in a test covers every
service, and there is exactly one place where a timeout or a header policy
changes.

Callers MUST reach it through the module (`sync_http.request_bytes(...)`), not
`from ... import request_bytes`, or monkeypatching will miss their binding.
"""

from __future__ import annotations

import logging
import urllib.error
import urllib.request
from typing import Dict


logger = logging.getLogger(__name__)


def request_bytes(
    method: str,
    url: str,
    headers: Dict[str, str],
    data: bytes | None = None,
    timeout_s: int = 15,
) -> tuple[int, bytes]:
    """Perform one HTTP request and return (status_code, body_bytes).

    An HTTP error status is returned like any other status -- it is data, not an
    exception -- so callers decide what a 401 or a 404 means for them.
    """
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            return int(response.getcode()), bytes(response.read())
    except urllib.error.HTTPError as exc:
        body = exc.read() if exc.fp is not None else b""
        return int(exc.code), body or b"{}"
```

- [ ] **Step 4: Implement `hanova/gauth.py`**

Create `reachy_companion/src/reachy_companion/hanova/gauth.py`:

```python
"""Google OAuth for Calendar and Tasks, adapted from upstream `gauth.py` (D-018).

Upstream hardcoded the account address at `bin/google/gauth.py:22`; here it comes
from `HANOVA_GOOGLE_ACCOUNT`, and the credentials directory from
`GOOGLE_CREDS_DIR` (upstream's own env name, kept per manifest section D).

The credentials file is **rewritten** whenever the access token is refreshed, so
the robot needs its own writable copy -- which is why it lives in the app
instance directory and is part of the deploy backup ritual.

Everything here is synchronous. Tools must call it via `asyncio.to_thread`.
"""

from __future__ import annotations

import os
import json
import logging
import tempfile
import threading
import contextlib
import urllib.parse
from typing import Any, Dict
from pathlib import Path
from datetime import datetime, timezone, timedelta

from reachy_companion.hanova import settings, sync_http


logger = logging.getLogger(__name__)

TOKEN_URI = "https://oauth2.googleapis.com/token"
_TOKEN_SLACK_SECONDS = 60
_TIMEOUT_S = 15

# Finding 14: Calendar and Tasks share one credentials file and run as separate
# worker threads, so every read-refresh-write cycle is serialised per path.
_LOCK_REGISTRY: Dict[str, threading.Lock] = {}
_LOCK_REGISTRY_LOCK = threading.Lock()


class GoogleApiError(RuntimeError):
    """A non-2xx response from a Google API.

    The parsed body is kept on the exception for callers that need it, but the
    string form deliberately carries **only** the method, the path shape and the
    status: Google's error messages quote the request back, including event
    titles, addresses and file names (review finding 7). Use `friendly_message()`
    for anything the model will say out loud.
    """

    def __init__(self, status: int, body: Dict[str, Any], url: str, method: str) -> None:
        """Record the status and the parsed error body for the caller."""
        self.status = status
        self.body = body
        host = urllib.parse.urlsplit(url).netloc
        super().__init__(f"{method} {host} -> HTTP {status}")


_STATUS_MESSAGES = {
    401: "the Google account needs to be re-authorised on the robot",
    403: "Google refused that request for this account",
    404: "Google could not find that item",
    429: "Google is rate-limiting this account right now",
}


def friendly_message(exc: BaseException) -> str:
    """A fixed, identifier-free reason the model may say out loud (finding 7)."""
    status = getattr(exc, "status", None)
    if isinstance(status, int):
        return _STATUS_MESSAGES.get(status, f"Google returned an error (HTTP {status})")
    return "the Google request could not be completed"


# A 5xx, a rate limit or a socket error may be retried on the same confirmation;
# a 4xx means the resolved action itself is wrong, so the authorisation is spent
# (review finding 4).
_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})


def is_transient(exc: BaseException) -> bool:
    """Return whether a gated tool may retry on the same confirmation."""
    status = getattr(exc, "status", None)
    if isinstance(status, int):
        return status in _RETRYABLE_STATUSES
    return isinstance(exc, OSError)


def _creds_path(account: str) -> Path:
    creds_dir = settings.google_creds_dir()
    if creds_dir is None:
        raise FileNotFoundError("GOOGLE_CREDS_DIR is not set; Google credentials cannot be located.")
    return creds_dir / f"{account}.json"


def _read_creds(account: str) -> Dict[str, Any]:
    path = _creds_path(account)
    if not path.is_file():
        raise FileNotFoundError(f"No Google credentials for {account} at {path}.")
    parsed: Dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return parsed


def _creds_lock(path: Path) -> threading.Lock:
    """Return the process-wide lock guarding one credentials file (finding 14)."""
    key = str(path.resolve() if path.parent.exists() else path)
    with _LOCK_REGISTRY_LOCK:
        lock = _LOCK_REGISTRY.get(key)
        if lock is None:
            lock = threading.Lock()
            _LOCK_REGISTRY[key] = lock
        return lock


def _write_creds(account: str, creds: Dict[str, Any]) -> None:
    """Replace the credentials file atomically, never widening its permissions.

    Review finding 14: a fixed `<account>.json.tmp` filename made two concurrent
    refreshes clobber each other, and `chmod` *after* `write_text` left the
    refresh token world-readable in between. `mkstemp` creates the file 0600 in
    one step, in the same directory so `os.replace` stays atomic, and the fsync
    means a power cut cannot leave a truncated credential behind.
    """
    path = _creds_path(account)
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)
    handle, tmp_name = tempfile.mkstemp(dir=str(directory), prefix=".creds-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(creds, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def _expired(expiry_iso: str | None) -> bool:
    if not expiry_iso:
        return True
    try:
        expiry = datetime.fromisoformat(expiry_iso.replace("Z", "+00:00"))
    except ValueError:
        return True
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    return (expiry - datetime.now(timezone.utc)).total_seconds() < _TOKEN_SLACK_SECONDS


def _refresh(creds: Dict[str, Any]) -> Dict[str, Any]:
    payload = urllib.parse.urlencode(
        {
            "client_id": creds["client_id"],
            "client_secret": creds["client_secret"],
            "refresh_token": creds["refresh_token"],
            "grant_type": "refresh_token",
        }
    ).encode()
    status, raw = sync_http.request_bytes(
        "POST",
        str(creds.get("token_uri") or TOKEN_URI),
        {"Content-Type": "application/x-www-form-urlencoded"},
        payload,
        _TIMEOUT_S,
    )
    if not (200 <= status < 300):
        raise GoogleApiError(status, _parse(raw), url=TOKEN_URI, method="POST")
    body = _parse(raw)
    creds["token"] = body["access_token"]
    expires_in = int(body.get("expires_in", 3600))
    creds["expiry"] = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).strftime("%Y-%m-%dT%H:%M:%S")
    return creds


def _parse(raw: bytes) -> Dict[str, Any]:
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"_raw": raw.decode("utf-8", "replace")}
    return parsed if isinstance(parsed, dict) else {"_raw": parsed}


def get_access_token(account: str | None = None) -> str:
    """Return a valid access token, refreshing and rewriting the file if needed.

    The whole read-check-refresh-write cycle runs under the per-path lock, and
    the file is re-read *inside* the lock: whichever caller loses the race then
    finds a fresh token already on disk and issues no second refresh at all
    (review finding 14).
    """
    resolved = account or settings.google_account()
    with _creds_lock(_creds_path(resolved)):
        creds = _read_creds(resolved)
        if not _expired(creds.get("expiry")):
            return str(creds["token"])
        creds = _refresh(creds)
        _write_creds(resolved, creds)
        return str(creds["token"])


def force_refresh(account: str | None = None) -> str:
    """Refresh unconditionally, under the same lock. Used on a 401 retry."""
    resolved = account or settings.google_account()
    with _creds_lock(_creds_path(resolved)):
        creds = _refresh(_read_creds(resolved))
        _write_creds(resolved, creds)
        return str(creds["token"])


def api_call(
    method: str,
    url: str,
    body: Dict[str, Any] | None = None,
    query: Dict[str, Any] | None = None,
    account: str | None = None,
) -> Dict[str, Any]:
    """Make one authenticated Google API call, refreshing once on a 401."""
    resolved = account or settings.google_account()
    if query:
        pairs = {key: value for key, value in query.items() if value is not None}
        if pairs:
            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}{urllib.parse.urlencode(pairs)}"

    data = json.dumps(body).encode() if body is not None else None

    def call(token: str) -> tuple[int, bytes]:
        headers = {"Authorization": f"Bearer {token}"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        return sync_http.request_bytes(method, url, headers, data, _TIMEOUT_S)

    status, raw = call(get_access_token(resolved))
    if status == 401:
        logger.info("Google API returned 401; forcing a token refresh and retrying once.")
        status, raw = call(force_refresh(resolved))

    parsed = _parse(raw)
    if not (200 <= status < 300):
        raise GoogleApiError(status, parsed, url=url, method=method)
    return parsed
```

- [ ] **Step 5: Implement `hanova/gcal.py`**

Create `reachy_companion/src/reachy_companion/hanova/gcal.py`:

```python
"""Google Calendar v3 calls, adapted from upstream `gcal.py` (D-018).

Upstream reached these through a subprocess CLI behind a localhost HTTP shim
(`host-tools.py:79-97`); the shim and the subprocess are both deleted here. The
default calendar id is configuration, never a literal.

Synchronous by design (it wraps `gauth`); tools call it via `asyncio.to_thread`.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List
from datetime import datetime, timezone, timedelta

from reachy_companion.hanova import gauth


logger = logging.getLogger(__name__)

CAL_BASE = "https://www.googleapis.com/calendar/v3"


def create_event(
    calendar_id: str,
    summary: str,
    start: str,
    end: str,
    timezone_name: str,
    location: str | None = None,
    description: str | None = None,
) -> Dict[str, Any]:
    """Create one timed event. *start* and *end* are ISO-8601 with an offset."""
    body: Dict[str, Any] = {
        "summary": summary,
        "start": {"dateTime": start, "timeZone": timezone_name},
        "end": {"dateTime": end, "timeZone": timezone_name},
    }
    if location:
        body["location"] = location
    if description:
        body["description"] = description
    return gauth.api_call("POST", f"{CAL_BASE}/calendars/{calendar_id}/events", body=body)


def list_events(
    calendar_id: str,
    time_min: str,
    time_max: str,
    limit: int = 25,
    search: str | None = None,
) -> List[Dict[str, Any]]:
    """Return the expanded events in a window, ordered by start time."""
    query: Dict[str, Any] = {
        "timeMin": time_min,
        "timeMax": time_max,
        "singleEvents": "true",
        "orderBy": "startTime",
        "maxResults": limit,
    }
    if search:
        query["q"] = search
    response = gauth.api_call("GET", f"{CAL_BASE}/calendars/{calendar_id}/events", query=query)
    items = response.get("items", [])
    return list(items) if isinstance(items, list) else []


def delete_event(calendar_id: str, event_id: str) -> None:
    """Delete one event by id."""
    gauth.api_call("DELETE", f"{CAL_BASE}/calendars/{calendar_id}/events/{event_id}")


def event_when(event: Dict[str, Any]) -> str:
    """Return a short human-readable start for a confirmation read-back."""
    start = event.get("start") or {}
    return str(start.get("dateTime") or start.get("date") or "")


def find_event(
    calendar_id: str,
    match: str,
    window_days: int,
) -> tuple[Dict[str, Any] | None, List[Dict[str, Any]], str | None]:
    """Resolve *match* to exactly one event, or report not_found / ambiguous.

    The window is UTC (`now - 1 day` .. `now + window_days`), which Google accepts
    regardless of the calendar's own timezone, so no local tz database is needed.
    """
    now = datetime.now(timezone.utc)
    time_min = (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    time_max = (now + timedelta(days=window_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    needle = match.strip().lower()
    events = list_events(calendar_id, time_min, time_max, limit=100)
    matches = [event for event in events if needle in str(event.get("summary") or "").lower()]
    if not matches:
        return None, [], "not_found"
    if len(matches) > 1:
        return None, matches, "ambiguous"
    return matches[0], [], None
```

- [ ] **Step 6: Implement the three calendar tools**

Create `reachy_companion/src/reachy_companion/tools/calendar_add.py`:

```python
"""Create a calendar event (D-018). Filename == Tool.name."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict

from reachy_companion.hanova import gcal, redact, settings
from reachy_companion.hanova.gauth import GoogleApiError, friendly_message
from reachy_companion.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


class CalendarAdd(Tool):
    """Add an event to the configured calendar."""

    name = "calendar_add"
    description = "Add an event to the calendar. 用於新增行程、約會、提醒事項。"
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "Event title."},
            "start": {
                "type": "string",
                "description": "Start time, ISO-8601 with an offset, e.g. 2026-09-02T19:00:00+08:00.",
            },
            "end": {
                "type": "string",
                "description": "End time, ISO-8601 with an offset, e.g. 2026-09-02T20:30:00+08:00.",
            },
            "location": {"type": "string", "description": "Optional location."},
        },
        "required": ["summary", "start", "end"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Create the event and report its real id."""
        available, reason = settings.tool_status(self.name)
        if not available:
            return settings.unavailable(reason)

        summary = str(kwargs.get("summary", "")).strip()
        start = str(kwargs.get("start", "")).strip()
        end = str(kwargs.get("end", "")).strip()
        if not (summary and start and end):
            return {"ok": False, "error": "summary, start and end are all required"}

        calendar_id = settings.gcal_calendar_id()
        logger.info("Tool call: calendar_add summary=%s", redact.text(summary))
        try:
            created = await asyncio.to_thread(
                gcal.create_event,
                calendar_id=calendar_id,
                summary=summary,
                start=start,
                end=end,
                timezone_name=settings.timezone_name(),
                location=str(kwargs.get("location") or "") or None,
            )
        except (GoogleApiError, OSError, ValueError, KeyError) as exc:
            # Finding 7: a Google error body quotes the event back at us.
            logger.warning("calendar_add failed: %s", redact.error(exc))
            return {"ok": False, "error": friendly_message(exc)}
        return {
            "ok": True,
            "event_id": created.get("id"),
            "summary": created.get("summary"),
            "link": created.get("htmlLink"),
        }
```

Create `reachy_companion/src/reachy_companion/tools/calendar_list.py`:

```python
"""Read upcoming calendar events (D-018). Filename == Tool.name."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict
from datetime import datetime, timezone, timedelta

from reachy_companion.hanova import gcal, redact, settings
from reachy_companion.hanova.gauth import GoogleApiError, friendly_message
from reachy_companion.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


class CalendarList(Tool):
    """List upcoming events on the configured calendar."""

    name = "calendar_list"
    description = "List upcoming calendar events. 用於查行程、最近有什麼安排。"
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "days": {
                "type": "integer",
                "description": "How many days ahead to look. Default 7.",
                "minimum": 1,
                "maximum": 365,
            },
            "search": {"type": "string", "description": "Optional text to filter titles by."},
        },
        "required": [],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Return a compact list of upcoming events."""
        available, reason = settings.tool_status(self.name)
        if not available:
            return settings.unavailable(reason)

        try:
            days = int(kwargs.get("days") or 7)
        except (TypeError, ValueError):
            days = 7
        days = max(1, min(365, days))

        now = datetime.now(timezone.utc)
        logger.info("Tool call: calendar_list days=%d", days)
        try:
            events = await asyncio.to_thread(
                gcal.list_events,
                calendar_id=settings.gcal_calendar_id(),
                time_min=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                time_max=(now + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                limit=25,
                search=str(kwargs.get("search") or "") or None,
            )
        except (GoogleApiError, OSError, ValueError, KeyError) as exc:
            logger.warning("calendar_list failed: %s", redact.error(exc))
            return {"ok": False, "error": friendly_message(exc)}

        compact = [
            {
                "id": event.get("id"),
                "summary": event.get("summary"),
                "when": gcal.event_when(event),
                "location": event.get("location"),
            }
            for event in events
        ]
        return {"ok": True, "count": len(compact), "events": compact}
```

Create `reachy_companion/src/reachy_companion/tools/calendar_delete.py`:

```python
"""Delete a calendar event, behind a confirmation gate (D-018, R3).

Upstream resolved the event by a two-character fuzzy substring match and deleted
it in one call (`host-tools.py:303-327`). Over a noisy Chinese voice channel that
is genuinely dangerous, so here the first call resolves and reads the event back,
and only a second call with `confirm: true` deletes -- and it deletes the event
that was read back, not whatever the second call's arguments said.

**This file is the worked example of the gated-tool shape** every other gated
tool copies (review round 1, finding 4):

* `match` is **optional in the schema and mandatory in the non-confirm branch**.
  The confirming call carries only `confirm: true`, so a schema that required
  `match` forced the model to resupply -- and possibly mis-hear -- the very field
  the gate exists to freeze.
* `GATE.claim()` takes the action *in flight* without spending it;
  `GATE.complete()` spends it only after Google acknowledged the delete;
  `GATE.release()` puts it back when the failure was transient, so the user can
  say "try again" instead of walking the whole read-back a second time.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict

from reachy_companion.hanova import gcal, redact, settings
from reachy_companion.hanova.gauth import GoogleApiError, is_transient, friendly_message
from reachy_companion.hanova.confirm import GATE, confirmation_expired
from reachy_companion.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)

_MIN_MATCH_LEN = 2


class CalendarDelete(Tool):
    """Delete one calendar event after the user confirms it."""

    name = "calendar_delete"
    description = "Delete a calendar event. 需要先確認：先讀回事件再刪。"
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "match": {
                "type": "string",
                "description": "Text from the event title to find it by. Omit when confirming.",
                "minLength": _MIN_MATCH_LEN,
            },
            "confirm": {
                "type": "boolean",
                "description": "Set true only after the user has confirmed the exact event read back to them.",
            },
        },
        # Finding 4: optional at the schema level, mandatory in the code path
        # that actually needs it. The confirming call carries only `confirm`.
        "required": [],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Resolve and read back, or execute a previously confirmed delete."""
        available, reason = settings.tool_status(self.name)
        if not available:
            return settings.unavailable(reason)

        if bool(kwargs.get("confirm")):
            pending = GATE.claim(self.name)
            if pending is None:
                return confirmation_expired()
            logger.info("Tool call: calendar_delete confirmed for %s", redact.ident(pending.payload.get("event_id")))
            try:
                await asyncio.to_thread(
                    gcal.delete_event,
                    calendar_id=str(pending.payload["calendar_id"]),
                    event_id=str(pending.payload["event_id"]),
                )
            except (GoogleApiError, OSError, ValueError, KeyError) as exc:
                logger.warning("calendar_delete failed: %s", redact.error(exc))
                if is_transient(exc):
                    # Round 2, findings 2 and 9: the claim id says *which*
                    # authorisation this is, and only a transient fault may put
                    # it back for a bare retry.
                    GATE.release(self.name, pending.claim_id)
                    return {"ok": False, "error": friendly_message(exc), "retryable": True}
                GATE.complete(self.name, pending.claim_id)
                return {"ok": False, "error": friendly_message(exc)}
            GATE.complete(self.name, pending.claim_id)
            return {"ok": True, "status": "deleted", "summary": pending.summary}

        match = str(kwargs.get("match", "")).strip()
        if len(match) < _MIN_MATCH_LEN:
            return {"ok": False, "error": f"match must be at least {_MIN_MATCH_LEN} characters"}

        calendar_id = settings.gcal_calendar_id()
        logger.info("Tool call: calendar_delete resolving match=%s", redact.text(match))
        try:
            event, candidates, error = await asyncio.to_thread(
                gcal.find_event,
                calendar_id,
                match,
                settings.cal_delete_window_days(),
            )
        except (GoogleApiError, OSError, ValueError, KeyError) as exc:
            logger.warning("calendar_delete lookup failed: %s", redact.error(exc))
            return {"ok": False, "error": friendly_message(exc)}

        if error == "not_found":
            return {"ok": False, "error": "not_found"}
        if error == "ambiguous":
            return {
                "ok": False,
                "error": "ambiguous",
                "candidates": [
                    {"summary": item.get("summary"), "when": gcal.event_when(item)} for item in candidates
                ],
            }

        assert event is not None
        title = str(event.get("summary") or "")
        when = gcal.event_when(event)
        return GATE.arm(
            self.name,
            f"delete the calendar event {title!r} on {when}",
            {"calendar_id": calendar_id, "event_id": event.get("id"), "summary": title, "when": when},
        )
```

- [ ] **Step 7: Enable the three tools in the locked profile**

In `reachy_companion/profiles/_reachy_companion_locked_profile/profile.md`, add to `default_tools` immediately after `"show_on_tv",`:

```toml
  "show_on_tv",
  "calendar_add",
  "calendar_list",
  "calendar_delete",
```

- [ ] **Step 8: Run the tests to verify they pass**

```powershell
cd C:\Project\Reachy-mini\reachy_companion
..\.venv\Scripts\python.exe -m pytest tests/test_hanova_gauth.py tests/test_hanova_calendar.py -q
```

Expected: green — **10** test functions from `test_hanova_gauth.py` and **22**
from `test_hanova_calendar.py`, neither parametrised: **32 collected cases**.
Record the exact number.

- [ ] **Step 9: Run the full gate**

```powershell
cd C:\Project\Reachy-mini\reachy_companion
..\.venv\Scripts\python.exe -m pytest -q
..\.venv\Scripts\python.exe -m ruff check src tests
..\.venv\Scripts\python.exe -m ruff format --check src tests
..\.venv\Scripts\python.exe -m mypy
```

Expected: pytest green (30 documented skips), ruff clean, mypy `Success: no issues found`.

- [ ] **Step 10: Commit**

```bash
cd /c/Project/Reachy-mini
git rev-parse --abbrev-ref HEAD   # must print feat/ha-nova-port (Task 0)
git add reachy_companion/src/reachy_companion/hanova/sync_http.py \
        reachy_companion/src/reachy_companion/hanova/gauth.py \
        reachy_companion/src/reachy_companion/hanova/gcal.py \
        reachy_companion/src/reachy_companion/tools/calendar_add.py \
        reachy_companion/src/reachy_companion/tools/calendar_list.py \
        reachy_companion/src/reachy_companion/tools/calendar_delete.py \
        reachy_companion/profiles/_reachy_companion_locked_profile/profile.md \
        reachy_companion/tests/test_hanova_gauth.py \
        reachy_companion/tests/test_hanova_calendar.py
git commit -m "feat(hanova): Google OAuth layer and calendar add/list/delete"
```

---

### Task 8: Google Tasks — `task_add`, `task_list`, `task_complete`, `task_delete`

Implements R1, R3 (`task_complete` and `task_delete` are both gated), R5. Upstream resolved a task by spawning one cold Python interpreter *per task list* (`host-tools.py:231-271`); in-process this is one HTTP call per list on one asyncio thread.

**Files:**
- Create: `reachy_companion/src/reachy_companion/hanova/gtasks.py`
- Create: `reachy_companion/src/reachy_companion/tools/task_add.py`
- Create: `reachy_companion/src/reachy_companion/tools/task_list.py`
- Create: `reachy_companion/src/reachy_companion/tools/task_complete.py`
- Create: `reachy_companion/src/reachy_companion/tools/task_delete.py`
- Modify: `reachy_companion/profiles/_reachy_companion_locked_profile/profile.md` (`default_tools` gains the four names)
- Test: `reachy_companion/tests/test_hanova_tasks.py`

**Interfaces:**
- Consumes: `hanova.gauth.{api_call, GoogleApiError, friendly_message}` (Task 7); `hanova.settings.{gtasks_list_id, tool_status, unavailable}`; `hanova.redact`; `hanova.confirm.{GATE, confirmation_expired}`.

**Both gated tools here follow the Task 7 shape exactly** (review findings 4 and
10): `required: []` in the schema with `match` mandatory only in the non-confirm
branch, `GATE.claim()` → `GATE.complete()` on success → `GATE.release()` on a
transient failure, `settings.tool_status(self.name)` for availability, and
`redact` on every log line and error surface. `task_add` requires
`HANOVA_GTASKS_LIST_ID`; `task_list`, `task_complete` and `task_delete` do not,
because they walk every list.
- Produces:
  - `hanova.gtasks.T_BASE: str`
  - `hanova.gtasks.list_task_lists() -> List[Dict[str, Any]]`
  - `hanova.gtasks.list_tasks(list_id: str, limit: int = 50, show_completed: bool = False) -> List[Dict[str, Any]]`
  - `hanova.gtasks.create_task(list_id: str, title: str, notes: str | None = None, due: str | None = None) -> Dict[str, Any]`
  - `hanova.gtasks.complete_task(list_id: str, task_id: str) -> Dict[str, Any]`
  - `hanova.gtasks.delete_task(list_id: str, task_id: str) -> None`
  - `hanova.gtasks.find_task(match: str, include_completed: bool = False) -> tuple[Dict[str, Any] | None, List[Dict[str, Any]], str | None]` — the returned task and each candidate carry extra keys `list_id` and `list_title`; error is `"not_found"` or `"ambiguous"`
  - `tools.task_add.TaskAdd`, `tools.task_list.TaskList`, `tools.task_complete.TaskComplete`, `tools.task_delete.TaskDelete`

- [ ] **Step 1: Write the failing test**

Create `reachy_companion/tests/test_hanova_tasks.py`:

```python
"""Contract tests for the four Google Tasks tools (D-018, R1/R3/R5)."""

import types
import importlib
from typing import Any

import pytest

from reachy_companion.hanova import gtasks
from reachy_companion.hanova.confirm import GATE
from reachy_companion.tools.task_add import TaskAdd
from reachy_companion.tools.task_list import TaskList
from reachy_companion.tools.task_delete import TaskDelete
from reachy_companion.tools.task_complete import TaskComplete


LIST_ID = "list-under-test"


def _deps():
    return types.SimpleNamespace(reachy_mini=None, instance_path=None)


@pytest.fixture(autouse=True)
def configured(monkeypatch, tmp_path):
    """A configured google-workspace family and an empty confirmation gate."""
    creds_dir = tmp_path / "google-workspace-mcp"
    creds_dir.mkdir()
    (creds_dir / "someone@example.com.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("GOOGLE_CREDS_DIR", str(creds_dir))
    monkeypatch.setenv("HANOVA_GOOGLE_ACCOUNT", "someone@example.com")
    monkeypatch.setenv("HANOVA_GTASKS_LIST_ID", LIST_ID)
    monkeypatch.delenv("HANOVA_CONFIRM_TTL_S", raising=False)
    GATE.reset()
    GATE.begin_session()
    yield
    GATE.reset()


def test_tool_names_match_their_filenames():
    """The loader resolves tools by filename == Tool.name."""
    assert TaskAdd.name == "task_add"
    assert TaskList.name == "task_list"
    assert TaskComplete.name == "task_complete"
    assert TaskDelete.name == "task_delete"


def test_descriptions_carry_no_personal_identifier():
    """R10: upstream leaked the owner's name and list names into these."""
    for text in (TaskAdd().description, TaskList().description, TaskComplete().description, TaskDelete().description):
        assert "@" not in text
        assert LIST_ID not in text
        assert len(text) <= 120


def test_create_task_posts_the_expected_body(monkeypatch):
    """Title is required, notes and due are optional and omitted when absent."""
    seen = {}

    def fake_api_call(method, url, body=None, query=None, account=None):
        seen.update(method=method, url=url, body=body)
        return {"id": "t1", "title": "buy milk", "status": "needsAction"}

    monkeypatch.setattr(gtasks.gauth, "api_call", fake_api_call)
    gtasks.create_task(LIST_ID, "buy milk")
    assert seen["method"] == "POST"
    assert seen["url"] == f"{gtasks.T_BASE}/lists/{LIST_ID}/tasks"
    assert seen["body"] == {"title": "buy milk"}


def test_complete_task_patches_the_status(monkeypatch):
    """Completion is a PATCH to status=completed, not a delete."""
    seen = {}

    def fake_api_call(method, url, body=None, query=None, account=None):
        seen.update(method=method, url=url, body=body)
        return {"id": "t1", "status": "completed"}

    monkeypatch.setattr(gtasks.gauth, "api_call", fake_api_call)
    gtasks.complete_task(LIST_ID, "t1")
    assert seen["method"] == "PATCH"
    assert seen["url"] == f"{gtasks.T_BASE}/lists/{LIST_ID}/tasks/t1"
    assert seen["body"] == {"status": "completed"}


def _across_lists(monkeypatch, tasks_by_list: dict[str, list[dict]]) -> None:
    """Stub gauth.api_call so find_task walks two lists without a network."""

    def fake_api_call(method, url, body=None, query=None, account=None):
        if url.endswith("/users/@me/lists"):
            return {
                "items": [{"id": name, "title": f"List {name}"} for name in tasks_by_list],
            }
        for name, tasks in tasks_by_list.items():
            if f"/lists/{name}/tasks" in url:
                return {"items": tasks}
        return {"items": []}

    monkeypatch.setattr(gtasks.gauth, "api_call", fake_api_call)


def test_find_task_searches_every_list(monkeypatch):
    """A task the user names may live in any list, not just the default one."""
    _across_lists(monkeypatch, {"a": [{"id": "t1", "title": "Gym"}], "b": [{"id": "t2", "title": "Buy milk"}]})
    task, candidates, error = gtasks.find_task("milk")
    assert error is None and candidates == []
    assert task is not None and task["id"] == "t2" and task["list_id"] == "b"


def test_find_task_reports_ambiguity(monkeypatch):
    """Two matches must never be resolved by guessing."""
    _across_lists(monkeypatch, {"a": [{"id": "t1", "title": "Buy milk"}], "b": [{"id": "t2", "title": "buy MILK again"}]})
    task, candidates, error = gtasks.find_task("milk")
    assert task is None and error == "ambiguous" and len(candidates) == 2


def test_find_task_reports_no_match(monkeypatch):
    """Zero matches is a clean answer, not an exception."""
    _across_lists(monkeypatch, {"a": [{"id": "t1", "title": "Gym"}]})
    task, candidates, error = gtasks.find_task("milk")
    assert task is None and error == "not_found"


@pytest.mark.asyncio
async def test_task_add_is_unavailable_without_credentials(monkeypatch):
    """R5: no credentials directory means the tool is off, and it names the key."""
    monkeypatch.delenv("GOOGLE_CREDS_DIR")
    out = await TaskAdd()(deps=_deps(), title="buy milk")
    assert out == {"status": "unavailable", "reason": "GOOGLE_CREDS_DIR"}


@pytest.mark.asyncio
async def test_task_add_requires_a_configured_list(monkeypatch):
    """Finding 10: a per-tool prerequisite, because task_list does not need it."""
    monkeypatch.delenv("HANOVA_GTASKS_LIST_ID")
    out = await TaskAdd()(deps=_deps(), title="buy milk")
    assert out == {"status": "unavailable", "reason": "HANOVA_GTASKS_LIST_ID"}


@pytest.mark.asyncio
async def test_the_other_task_tools_do_not_need_a_list_id(monkeypatch):
    """Finding 10: list/complete/delete walk every list, so the id is irrelevant."""
    import reachy_companion.tools.task_list as task_list_module

    monkeypatch.delenv("HANOVA_GTASKS_LIST_ID")
    monkeypatch.setattr(task_list_module.gtasks, "list_task_lists", lambda: [])
    out = await TaskList()(deps=_deps())
    assert out["ok"] is True


@pytest.mark.asyncio
async def test_gated_task_tools_confirm_without_resupplying_match(monkeypatch):
    """Finding 4: the confirming call carries only `confirm`."""
    import reachy_companion.tools.task_delete as task_delete_module

    assert task_delete_module.TaskDelete.parameters_schema["required"] == []
    monkeypatch.setattr(
        task_delete_module.gtasks,
        "find_task",
        lambda match, include_completed=False: (
            {"id": "t9", "title": "Old chore", "list_id": "b", "list_title": "Home"},
            [],
            None,
        ),
    )
    deleted = {}
    monkeypatch.setattr(
        task_delete_module.gtasks,
        "delete_task",
        lambda list_id, task_id: deleted.update(task_id=task_id),
    )
    await TaskDelete()(deps=_deps(), match="chore")
    out = await TaskDelete()(deps=_deps(), confirm=True)
    assert out["ok"] is True and deleted == {"task_id": "t9"}


@pytest.mark.asyncio
async def test_a_transient_task_failure_keeps_the_authorisation(monkeypatch):
    """Finding 4: a 503 must not cost the user their confirmation."""
    import reachy_companion.tools.task_complete as task_complete_module

    monkeypatch.setattr(
        task_complete_module.gtasks,
        "find_task",
        lambda match, include_completed=False: (
            {"id": "t1", "title": "Buy milk", "list_id": "a", "list_title": "Work"},
            [],
            None,
        ),
    )
    attempts = {"n": 0}

    def flaky(list_id, task_id):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise task_complete_module.GoogleApiError(503, {}, url="https://x.invalid/t", method="PATCH")
        return {"id": task_id}

    monkeypatch.setattr(task_complete_module.gtasks, "complete_task", flaky)
    await TaskComplete()(deps=_deps(), match="milk")
    first = await TaskComplete()(deps=_deps(), confirm=True)
    assert first["ok"] is False and first.get("retryable") is True
    second = await TaskComplete()(deps=_deps(), confirm=True)
    assert second["ok"] is True and second["status"] == "completed"


@pytest.mark.asyncio
async def test_task_logs_never_carry_the_task_title(monkeypatch, caplog):
    """Finding 7: the to-do list is the user's own data."""
    import logging

    import reachy_companion.tools.task_add as task_add_module

    sentinel = "SENTINEL_PRIVATE_x7"
    monkeypatch.setattr(
        task_add_module.gtasks,
        "create_task",
        lambda **kwargs: {"id": "t1", "title": sentinel, "status": "needsAction", "due": None},
    )
    caplog.set_level(logging.DEBUG)
    await TaskAdd()(deps=_deps(), title=sentinel)
    assert sentinel not in caplog.text


@pytest.mark.asyncio
async def test_task_add_creates_and_reports_the_task(monkeypatch):
    """A real artifact grounds what Reachy says next."""
    import reachy_companion.tools.task_add as task_add_module

    monkeypatch.setattr(
        task_add_module.gtasks,
        "create_task",
        lambda **kwargs: {"id": "t1", "title": "buy milk", "status": "needsAction", "due": None},
    )
    out = await TaskAdd()(deps=_deps(), title="buy milk")
    assert out["ok"] is True and out["task_id"] == "t1"


@pytest.mark.asyncio
async def test_task_list_groups_by_list(monkeypatch):
    """The model needs to know which list an item is in to talk about it."""
    import reachy_companion.tools.task_list as task_list_module

    monkeypatch.setattr(
        task_list_module.gtasks, "list_task_lists", lambda: [{"id": "a", "title": "Work"}]
    )
    monkeypatch.setattr(
        task_list_module.gtasks,
        "list_tasks",
        lambda list_id, limit=50, show_completed=False: [{"id": "t1", "title": "Gym", "due": None}],
    )
    out = await TaskList()(deps=_deps())
    assert out["ok"] is True
    assert out["lists"][0]["title"] == "Work"
    assert out["lists"][0]["tasks"][0]["title"] == "Gym"


@pytest.mark.asyncio
async def test_task_complete_arms_before_it_completes(monkeypatch):
    """R3: the first call reads the exact task back and changes nothing."""
    import reachy_companion.tools.task_complete as task_complete_module

    monkeypatch.setattr(
        task_complete_module.gtasks,
        "find_task",
        lambda match, include_completed=False: (
            {"id": "t1", "title": "Buy milk", "list_id": "a", "list_title": "Work"},
            [],
            None,
        ),
    )

    def fail_complete(list_id, task_id):
        raise AssertionError("task_complete must not write before confirmation")

    monkeypatch.setattr(task_complete_module.gtasks, "complete_task", fail_complete)
    out = await TaskComplete()(deps=_deps(), match="milk")
    assert out["status"] == "needs_confirmation" and "Buy milk" in out["summary"]


@pytest.mark.asyncio
async def test_task_complete_executes_the_armed_payload(monkeypatch):
    """The confirmed write uses what was read back."""
    import reachy_companion.tools.task_complete as task_complete_module

    monkeypatch.setattr(
        task_complete_module.gtasks,
        "find_task",
        lambda match, include_completed=False: (
            {"id": "t1", "title": "Buy milk", "list_id": "a", "list_title": "Work"},
            [],
            None,
        ),
    )
    done = {}
    monkeypatch.setattr(
        task_complete_module.gtasks,
        "complete_task",
        lambda list_id, task_id: done.update(list_id=list_id, task_id=task_id) or {"id": task_id},
    )
    await TaskComplete()(deps=_deps(), match="milk")
    out = await TaskComplete()(deps=_deps(), match="totally different", confirm=True)
    assert out["ok"] is True and out["status"] == "completed"
    assert done == {"list_id": "a", "task_id": "t1"}


@pytest.mark.asyncio
async def test_task_delete_arms_and_then_deletes(monkeypatch):
    """R3 again, for the irreversible one."""
    import reachy_companion.tools.task_delete as task_delete_module

    monkeypatch.setattr(
        task_delete_module.gtasks,
        "find_task",
        lambda match, include_completed=False: (
            {"id": "t9", "title": "Old chore", "list_id": "b", "list_title": "Home"},
            [],
            None,
        ),
    )
    deleted = {}
    monkeypatch.setattr(
        task_delete_module.gtasks,
        "delete_task",
        lambda list_id, task_id: deleted.update(list_id=list_id, task_id=task_id),
    )
    armed = await TaskDelete()(deps=_deps(), match="chore")
    assert armed["status"] == "needs_confirmation" and "Old chore" in armed["summary"]
    out = await TaskDelete()(deps=_deps(), match="chore", confirm=True)
    assert out["ok"] is True and out["status"] == "deleted"
    assert deleted == {"list_id": "b", "task_id": "t9"}


@pytest.mark.asyncio
async def test_task_delete_confirm_without_arm_is_refused(monkeypatch):
    """A confirm:true first call must delete nothing."""
    import reachy_companion.tools.task_delete as task_delete_module

    def fail_delete(list_id, task_id):
        raise AssertionError("task_delete must not delete without a pending action")

    monkeypatch.setattr(task_delete_module.gtasks, "delete_task", fail_delete)
    out = await TaskDelete()(deps=_deps(), match="chore", confirm=True)
    assert out["status"] == "confirmation_expired"


@pytest.mark.asyncio
async def test_gated_task_tools_refuse_a_too_short_match():
    """A two-character floor is the minimum defence over a noisy STT channel."""
    assert (await TaskComplete()(deps=_deps(), match="a"))["ok"] is False
    assert (await TaskDelete()(deps=_deps(), match="a"))["ok"] is False


@pytest.mark.asyncio
async def test_task_complete_refuses_an_ambiguous_match(monkeypatch):
    """Two candidates arm nothing and are handed back for the user to pick."""
    import reachy_companion.tools.task_complete as task_complete_module

    monkeypatch.setattr(
        task_complete_module.gtasks,
        "find_task",
        lambda match, include_completed=False: (
            None,
            [
                {"id": "t1", "title": "Buy milk", "list_id": "a", "list_title": "Work"},
                {"id": "t2", "title": "Buy milk again", "list_id": "b", "list_title": "Home"},
            ],
            "ambiguous",
        ),
    )
    out = await TaskComplete()(deps=_deps(), match="milk")
    assert out["ok"] is False and out["error"] == "ambiguous" and len(out["candidates"]) == 2
    assert GATE.claim("task_complete") is None


def test_all_four_tools_reach_the_model_session():
    """The locked profile must list them, or the model never sees them."""
    core_tools = importlib.import_module("reachy_companion.tools.core_tools")
    core_tools.initialize_tools(force=True)
    try:
        names = {spec["name"] for spec in core_tools.get_tool_specs()}
        assert {"task_add", "task_list", "task_complete", "task_delete"} <= names
    finally:
        core_tools._TOOLS_SIGNATURE = None
```

- [ ] **Step 2: Run the test to verify it fails**

```powershell
cd C:\Project\Reachy-mini\reachy_companion
..\.venv\Scripts\python.exe -m pytest tests/test_hanova_tasks.py -q
```

Expected: collection error — `ModuleNotFoundError: No module named 'reachy_companion.hanova.gtasks'`.

- [ ] **Step 3: Implement `hanova/gtasks.py`**

Create `reachy_companion/src/reachy_companion/hanova/gtasks.py`:

```python
"""Google Tasks v1 calls, adapted from upstream `gtasks.py` (D-018).

Upstream's cross-list task resolver spawned one cold Python interpreter per task
list (`host-tools.py:231-271`, `:357-390`). In-process it is one HTTP call per
list, on one worker thread, with no interpreter startup at all.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from reachy_companion.hanova import gauth


logger = logging.getLogger(__name__)

T_BASE = "https://tasks.googleapis.com/tasks/v1"
_MAX_LISTS = 100


def list_task_lists() -> List[Dict[str, Any]]:
    """Return every task list on the account."""
    response = gauth.api_call("GET", f"{T_BASE}/users/@me/lists", query={"maxResults": _MAX_LISTS})
    items = response.get("items", [])
    return list(items) if isinstance(items, list) else []


def list_tasks(list_id: str, limit: int = 50, show_completed: bool = False) -> List[Dict[str, Any]]:
    """Return the tasks in one list."""
    query: Dict[str, Any] = {"maxResults": limit}
    if show_completed:
        query["showCompleted"] = "true"
        query["showHidden"] = "true"
    response = gauth.api_call("GET", f"{T_BASE}/lists/{list_id}/tasks", query=query)
    items = response.get("items", [])
    return list(items) if isinstance(items, list) else []


def create_task(list_id: str, title: str, notes: str | None = None, due: str | None = None) -> Dict[str, Any]:
    """Create one task in *list_id*."""
    body: Dict[str, Any] = {"title": title}
    if notes:
        body["notes"] = notes
    if due:
        body["due"] = due
    return gauth.api_call("POST", f"{T_BASE}/lists/{list_id}/tasks", body=body)


def complete_task(list_id: str, task_id: str) -> Dict[str, Any]:
    """Mark one task completed (reversible in the Tasks UI)."""
    return gauth.api_call("PATCH", f"{T_BASE}/lists/{list_id}/tasks/{task_id}", body={"status": "completed"})


def delete_task(list_id: str, task_id: str) -> None:
    """Delete one task permanently."""
    gauth.api_call("DELETE", f"{T_BASE}/lists/{list_id}/tasks/{task_id}")


def find_task(
    match: str,
    include_completed: bool = False,
) -> tuple[Dict[str, Any] | None, List[Dict[str, Any]], str | None]:
    """Resolve *match* to exactly one task across every list.

    The returned task and each candidate carry two extra keys, `list_id` and
    `list_title`, so a caller can act on the result and name it to the user
    without a second lookup.
    """
    needle = match.strip().lower()
    matches: List[Dict[str, Any]] = []
    for task_list in list_task_lists():
        list_id = str(task_list.get("id") or "")
        if not list_id:
            continue
        for task in list_tasks(list_id, limit=50, show_completed=include_completed):
            if needle in str(task.get("title") or "").lower():
                enriched = dict(task)
                enriched["list_id"] = list_id
                enriched["list_title"] = task_list.get("title")
                matches.append(enriched)
    if not matches:
        return None, [], "not_found"
    if len(matches) > 1:
        return None, matches, "ambiguous"
    return matches[0], [], None
```

- [ ] **Step 4: Implement `tools/task_add.py` and `tools/task_list.py`**

Create `reachy_companion/src/reachy_companion/tools/task_add.py`:

```python
"""Add a task to the configured Google Tasks list (D-018). Filename == Tool.name."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict

from reachy_companion.hanova import gtasks, redact, settings
from reachy_companion.hanova.gauth import GoogleApiError, friendly_message
from reachy_companion.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


class TaskAdd(Tool):
    """Add one to-do item."""

    name = "task_add"
    description = "Add a to-do task. 用於新增待辦事項、記得要做的事。"
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "What needs doing."},
            "due": {"type": "string", "description": "Optional RFC3339 due date, e.g. 2026-09-02T00:00:00Z."},
            "notes": {"type": "string", "description": "Optional extra detail."},
        },
        "required": ["title"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Create the task and report its real id."""
        available, reason = settings.tool_status(self.name)
        if not available:
            return settings.unavailable(reason)

        title = str(kwargs.get("title", "")).strip()
        if not title:
            return {"ok": False, "error": "title is required"}
        list_id = settings.gtasks_list_id()
        if not list_id:
            return {"ok": False, "error": "HANOVA_GTASKS_LIST_ID is not set; there is no list to write to."}

        logger.info("Tool call: task_add title=%s", redact.text(title))
        try:
            created = await asyncio.to_thread(
                gtasks.create_task,
                list_id=list_id,
                title=title,
                notes=str(kwargs.get("notes") or "") or None,
                due=str(kwargs.get("due") or "") or None,
            )
        except (GoogleApiError, OSError, ValueError, KeyError) as exc:
            logger.warning("task_add failed: %s", redact.error(exc))
            return {"ok": False, "error": friendly_message(exc)}
        return {"ok": True, "task_id": created.get("id"), "title": created.get("title"), "due": created.get("due")}
```

Create `reachy_companion/src/reachy_companion/tools/task_list.py`:

```python
"""Read the to-do lists (D-018). Filename == Tool.name."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List

from reachy_companion.hanova import gtasks, redact, settings
from reachy_companion.hanova.gauth import GoogleApiError, friendly_message
from reachy_companion.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)

_PER_LIST_LIMIT = 50


class TaskList(Tool):
    """List outstanding to-do items, grouped by list."""

    name = "task_list"
    description = "List outstanding to-do tasks. 用於查待辦事項、還有什麼沒做。"
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "include_completed": {
                "type": "boolean",
                "description": "Include tasks already finished. Default false.",
            },
        },
        "required": [],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Return every list with its tasks."""
        available, reason = settings.tool_status(self.name)
        if not available:
            return settings.unavailable(reason)

        include_completed = bool(kwargs.get("include_completed"))
        logger.info("Tool call: task_list include_completed=%s", include_completed)

        def collect() -> List[Dict[str, Any]]:
            grouped: List[Dict[str, Any]] = []
            for task_list in gtasks.list_task_lists():
                list_id = str(task_list.get("id") or "")
                if not list_id:
                    continue
                tasks = gtasks.list_tasks(list_id, limit=_PER_LIST_LIMIT, show_completed=include_completed)
                grouped.append(
                    {
                        "id": list_id,
                        "title": task_list.get("title"),
                        "tasks": [
                            {"id": task.get("id"), "title": task.get("title"), "due": task.get("due")}
                            for task in tasks
                        ],
                    }
                )
            return grouped

        try:
            lists = await asyncio.to_thread(collect)
        except (GoogleApiError, OSError, ValueError, KeyError) as exc:
            logger.warning("task_list failed: %s", redact.error(exc))
            return {"ok": False, "error": friendly_message(exc)}
        return {"ok": True, "count": sum(len(entry["tasks"]) for entry in lists), "lists": lists}
```

- [ ] **Step 5: Implement the two gated task tools**

Create `reachy_companion/src/reachy_companion/tools/task_complete.py`:

```python
"""Mark a task complete, behind a confirmation gate (D-018, R3). Filename == Tool.name."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict

from reachy_companion.hanova import gtasks, redact, settings
from reachy_companion.hanova.gauth import GoogleApiError, is_transient, friendly_message
from reachy_companion.hanova.confirm import GATE, confirmation_expired
from reachy_companion.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)

_MIN_MATCH_LEN = 2


class TaskComplete(Tool):
    """Mark one to-do item complete after the user confirms it."""

    name = "task_complete"
    description = "Mark a to-do task complete. 需要先確認：先讀回項目再標記完成。"
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "match": {
                "type": "string",
                "description": "Text from the task title to find it by. Omit when confirming.",
                "minLength": _MIN_MATCH_LEN,
            },
            "confirm": {
                "type": "boolean",
                "description": "Set true only after the user has confirmed the exact task read back to them.",
            },
        },
        # Finding 4: optional in the schema, mandatory in the non-confirm branch.
        "required": [],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Resolve and read back, or execute a previously confirmed completion."""
        available, reason = settings.tool_status(self.name)
        if not available:
            return settings.unavailable(reason)

        if bool(kwargs.get("confirm")):
            pending = GATE.claim(self.name)
            if pending is None:
                return confirmation_expired()
            logger.info("Tool call: task_complete confirmed for %s", redact.ident(pending.payload.get("task_id")))
            try:
                await asyncio.to_thread(
                    gtasks.complete_task,
                    list_id=str(pending.payload["list_id"]),
                    task_id=str(pending.payload["task_id"]),
                )
            except (GoogleApiError, OSError, ValueError, KeyError) as exc:
                logger.warning("task_complete failed: %s", redact.error(exc))
                if is_transient(exc):
                    GATE.release(self.name, pending.claim_id)
                    return {"ok": False, "error": friendly_message(exc), "retryable": True}
                GATE.complete(self.name, pending.claim_id)
                return {"ok": False, "error": friendly_message(exc)}
            GATE.complete(self.name, pending.claim_id)
            return {"ok": True, "status": "completed", "summary": pending.summary}

        match = str(kwargs.get("match", "")).strip()
        if len(match) < _MIN_MATCH_LEN:
            return {"ok": False, "error": f"match must be at least {_MIN_MATCH_LEN} characters"}

        logger.info("Tool call: task_complete resolving match=%s", redact.text(match))
        try:
            task, candidates, error = await asyncio.to_thread(gtasks.find_task, match, False)
        except (GoogleApiError, OSError, ValueError, KeyError) as exc:
            logger.warning("task_complete lookup failed: %s", redact.error(exc))
            return {"ok": False, "error": friendly_message(exc)}

        if error == "not_found":
            return {"ok": False, "error": "not_found"}
        if error == "ambiguous":
            return {
                "ok": False,
                "error": "ambiguous",
                "candidates": [
                    {"title": item.get("title"), "list": item.get("list_title")} for item in candidates
                ],
            }

        assert task is not None
        title = str(task.get("title") or "")
        return GATE.arm(
            self.name,
            f"mark the task {title!r} complete",
            {"list_id": task.get("list_id"), "task_id": task.get("id"), "title": title},
        )
```

Create `reachy_companion/src/reachy_companion/tools/task_delete.py`:

```python
"""Delete a task, behind a confirmation gate (D-018, R3). Filename == Tool.name.

Unlike completion this is irreversible, so it searches completed tasks too and
still refuses to act on anything the user has not had read back to them.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict

from reachy_companion.hanova import gtasks, redact, settings
from reachy_companion.hanova.gauth import GoogleApiError, is_transient, friendly_message
from reachy_companion.hanova.confirm import GATE, confirmation_expired
from reachy_companion.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)

_MIN_MATCH_LEN = 2


class TaskDelete(Tool):
    """Delete one to-do item after the user confirms it."""

    name = "task_delete"
    description = "Delete a to-do task. 需要先確認：先讀回項目再刪除。"
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "match": {
                "type": "string",
                "description": "Text from the task title to find it by. Omit when confirming.",
                "minLength": _MIN_MATCH_LEN,
            },
            "confirm": {
                "type": "boolean",
                "description": "Set true only after the user has confirmed the exact task read back to them.",
            },
        },
        # Finding 4: optional in the schema, mandatory in the non-confirm branch.
        "required": [],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Resolve and read back, or execute a previously confirmed delete."""
        available, reason = settings.tool_status(self.name)
        if not available:
            return settings.unavailable(reason)

        if bool(kwargs.get("confirm")):
            pending = GATE.claim(self.name)
            if pending is None:
                return confirmation_expired()
            logger.info("Tool call: task_delete confirmed for %s", redact.ident(pending.payload.get("task_id")))
            try:
                await asyncio.to_thread(
                    gtasks.delete_task,
                    list_id=str(pending.payload["list_id"]),
                    task_id=str(pending.payload["task_id"]),
                )
            except (GoogleApiError, OSError, ValueError, KeyError) as exc:
                logger.warning("task_delete failed: %s", redact.error(exc))
                if is_transient(exc):
                    GATE.release(self.name, pending.claim_id)
                    return {"ok": False, "error": friendly_message(exc), "retryable": True}
                GATE.complete(self.name, pending.claim_id)
                return {"ok": False, "error": friendly_message(exc)}
            GATE.complete(self.name, pending.claim_id)
            return {"ok": True, "status": "deleted", "summary": pending.summary}

        match = str(kwargs.get("match", "")).strip()
        if len(match) < _MIN_MATCH_LEN:
            return {"ok": False, "error": f"match must be at least {_MIN_MATCH_LEN} characters"}

        logger.info("Tool call: task_delete resolving match=%s", redact.text(match))
        try:
            task, candidates, error = await asyncio.to_thread(gtasks.find_task, match, True)
        except (GoogleApiError, OSError, ValueError, KeyError) as exc:
            logger.warning("task_delete lookup failed: %s", redact.error(exc))
            return {"ok": False, "error": friendly_message(exc)}

        if error == "not_found":
            return {"ok": False, "error": "not_found"}
        if error == "ambiguous":
            return {
                "ok": False,
                "error": "ambiguous",
                "candidates": [
                    {"title": item.get("title"), "list": item.get("list_title")} for item in candidates
                ],
            }

        assert task is not None
        title = str(task.get("title") or "")
        return GATE.arm(
            self.name,
            f"delete the task {title!r}",
            {"list_id": task.get("list_id"), "task_id": task.get("id"), "title": title},
        )
```

- [ ] **Step 6: Enable the four tools in the locked profile**

In `reachy_companion/profiles/_reachy_companion_locked_profile/profile.md`, add to `default_tools` immediately after `"calendar_delete",`:

```toml
  "calendar_delete",
  "task_add",
  "task_list",
  "task_complete",
  "task_delete",
```

- [ ] **Step 7: Run the test to verify it passes**

```powershell
cd C:\Project\Reachy-mini\reachy_companion
..\.venv\Scripts\python.exe -m pytest tests/test_hanova_tasks.py -q
```

Expected: green — **22** test functions, none parametrised: **22 collected
cases**. Record the exact number.

- [ ] **Step 8: Run the full gate**

```powershell
cd C:\Project\Reachy-mini\reachy_companion
..\.venv\Scripts\python.exe -m pytest -q
..\.venv\Scripts\python.exe -m ruff check src tests
..\.venv\Scripts\python.exe -m ruff format --check src tests
..\.venv\Scripts\python.exe -m mypy
```

Expected: pytest green (30 documented skips), ruff clean, mypy `Success: no issues found`.

- [ ] **Step 9: Commit**

```bash
cd /c/Project/Reachy-mini
git rev-parse --abbrev-ref HEAD   # must print feat/ha-nova-port (Task 0)
git add reachy_companion/src/reachy_companion/hanova/gtasks.py \
        reachy_companion/src/reachy_companion/tools/task_add.py \
        reachy_companion/src/reachy_companion/tools/task_list.py \
        reachy_companion/src/reachy_companion/tools/task_complete.py \
        reachy_companion/src/reachy_companion/tools/task_delete.py \
        reachy_companion/profiles/_reachy_companion_locked_profile/profile.md \
        reachy_companion/tests/test_hanova_tasks.py
git commit -m "feat(hanova): Google Tasks add/list/complete/delete with confirmation gating"
```

---

### Task 9: Notion note writer — `notion_add`

Implements R1, R5 and R9's naming rule that `HANOVA_NOTION_*` never collides with the existing remote `NOTION_MCP_*` lane — these are two different Notion surfaces (an internal integration writing one database vs. a bearer-auth remote MCP server) and must not share a key.

Upstream's schema carried an `Owner` select whose options are real people's names (`bin/notion/notion.py:50-58`). That property is **not ported**: a personal identifier has no place in a tool schema that goes into the model prompt (R10).

**Files:**
- Create: `reachy_companion/src/reachy_companion/hanova/notion_client.py`
- Create: `reachy_companion/src/reachy_companion/tools/notion_add.py`
- Modify: `reachy_companion/profiles/_reachy_companion_locked_profile/profile.md` (`default_tools` gains `"notion_add"`)
- Test: `reachy_companion/tests/test_hanova_notion.py`

**Interfaces:**
- Consumes: `hanova.sync_http.request_bytes` (Task 7); `hanova.settings.{notion_token, notion_data_source_id, notion_title_prop, tool_status, unavailable}`; `hanova.redact` — the note the user dictated never reaches a log line or an error string (review finding 7).
- Produces:
  - `hanova.notion_client.API_BASE: str`, `hanova.notion_client.API_VERSION: str` (`"2025-09-03"`), `hanova.notion_client.BLOCK_LIMIT: int` (`1900`)
  - `hanova.notion_client.TYPE_OPTIONS: tuple[str, ...]`, `hanova.notion_client.STATUS_OPTIONS: tuple[str, ...]`
  - `hanova.notion_client.NotionError(RuntimeError)`
  - `hanova.notion_client.notion_request(method: str, path: str, body: Dict[str, Any] | None = None) -> Dict[str, Any]`
  - `hanova.notion_client.chunk_text(text: str, limit: int = BLOCK_LIMIT) -> List[str]`
  - `hanova.notion_client.add_page(title: str, type_: str | None = None, status: str | None = None, tags: str | None = None, body: str | None = None) -> Dict[str, Any]`
  - `tools.notion_add.NotionAdd` (`Tool.name == "notion_add"`)

- [ ] **Step 1: Write the failing test**

Create `reachy_companion/tests/test_hanova_notion.py`:

```python
"""Contract tests for the Notion note writer (D-018, R1/R5/R9/R10)."""

import json
import types
import importlib

import pytest

from reachy_companion.hanova import sync_http, notion_client
from reachy_companion.tools.notion_add import NotionAdd


DATA_SOURCE_ID = "data-source-under-test"


def _deps():
    return types.SimpleNamespace(reachy_mini=None, instance_path=None)


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    """A configured notion family, and the MCP lane left untouched."""
    monkeypatch.setenv("HANOVA_NOTION_TOKEN", "ntn_test")
    monkeypatch.setenv("HANOVA_NOTION_DATA_SOURCE_ID", DATA_SOURCE_ID)
    monkeypatch.delenv("HANOVA_NOTION_TITLE_PROP", raising=False)
    monkeypatch.setenv("NOTION_MCP_TOKEN", "a-different-token")


def test_tool_name_matches_the_filename():
    """The loader resolves tools by filename == Tool.name."""
    assert NotionAdd.name == "notion_add"


def test_description_carries_no_personal_identifier():
    """R10: upstream put the family database's real name in this description."""
    text = NotionAdd().description
    assert "@" not in text
    assert DATA_SOURCE_ID not in text
    assert len(text) <= 120


def test_schema_has_no_owner_property():
    """The Owner select's options are real people's names; it is not ported."""
    assert "owner" not in NotionAdd().parameters_schema["properties"]


def test_notion_request_sends_the_pinned_api_version(monkeypatch):
    """The data-sources model needs 2025-09-03; do not let it drift silently."""
    seen = {}

    def fake_request(method, url, headers, data=None, timeout_s=15):
        seen.update(method=method, url=url, headers=headers, data=data)
        return 200, b'{"id": "page1"}'

    monkeypatch.setattr(sync_http, "request_bytes", fake_request)
    out = notion_client.notion_request("POST", "/pages", {"parent": {}})
    assert out == {"id": "page1"}
    assert seen["url"] == f"{notion_client.API_BASE}/pages"
    assert seen["headers"]["Authorization"] == "Bearer ntn_test"
    assert seen["headers"]["Notion-Version"] == "2025-09-03"


def test_notion_request_raises_with_the_body_off_the_message(monkeypatch):
    """Finding 7: a Notion validation error quotes the submitted note back."""
    sentinel = "SENTINEL_PRIVATE_x7"

    def fake_request(method, url, headers, data=None, timeout_s=15):
        return 400, f'{{"message": "validation error near {sentinel}"}}'.encode()

    monkeypatch.setattr(sync_http, "request_bytes", fake_request)
    with pytest.raises(notion_client.NotionError) as excinfo:
        notion_client.notion_request("POST", "/pages", {})
    assert excinfo.value.status == 400
    assert sentinel in str(excinfo.value.body)     # kept for the caller
    assert sentinel not in str(excinfo.value)      # never in the message


def test_notion_request_without_a_token_raises(monkeypatch):
    """No token is a configuration fact the tool turns into "unavailable"."""
    monkeypatch.delenv("HANOVA_NOTION_TOKEN")
    with pytest.raises(notion_client.NotionError):
        notion_client.notion_request("GET", "/users/me")


def test_chunk_text_respects_the_block_limit():
    """Notion rejects rich-text runs over 2000 chars; we cut at 1900."""
    chunks = notion_client.chunk_text("x" * 5000)
    assert chunks
    assert all(len(chunk) <= notion_client.BLOCK_LIMIT for chunk in chunks)
    assert "".join(chunks) == "x" * 5000


def test_chunk_text_of_empty_input_is_empty():
    """A note with no body must not produce an empty paragraph block."""
    assert notion_client.chunk_text("") == []


def test_add_page_targets_the_configured_data_source(monkeypatch):
    """The database id is configuration; upstream read it from a committed cache."""
    seen = {}

    def fake_request(method, url, headers, data=None, timeout_s=15):
        seen["body"] = json.loads(data or b"{}")
        return 200, b'{"id": "page1", "url": "https://notion.example.invalid/page1"}'

    monkeypatch.setattr(sync_http, "request_bytes", fake_request)
    notion_client.add_page("Buy a lamp", type_="購物", tags="home,urgent")
    assert seen["body"]["parent"] == {"data_source_id": DATA_SOURCE_ID}
    assert seen["body"]["properties"]["Name"]["title"][0]["text"]["content"] == "Buy a lamp"
    assert seen["body"]["properties"]["Type"]["select"] == {"name": "購物"}
    assert [tag["name"] for tag in seen["body"]["properties"]["Tags"]["multi_select"]] == ["home", "urgent"]


def test_add_page_defaults_actionable_types_to_pending(monkeypatch):
    """Upstream's rule: shopping / to-do / contact rows start as 待辦."""
    seen = {}

    def fake_request(method, url, headers, data=None, timeout_s=15):
        seen["body"] = json.loads(data or b"{}")
        return 200, b'{"id": "page1"}'

    monkeypatch.setattr(sync_http, "request_bytes", fake_request)
    notion_client.add_page("Buy a lamp", type_="購物")
    assert seen["body"]["properties"]["Status"]["select"] == {"name": "待辦"}


@pytest.mark.asyncio
async def test_notion_add_is_unavailable_without_config(monkeypatch):
    """R5: an unconfigured tool answers, it does not raise, and it names the key."""
    monkeypatch.delenv("HANOVA_NOTION_TOKEN")
    out = await NotionAdd()(deps=_deps(), title="Buy a lamp")
    assert out == {"status": "unavailable", "reason": "HANOVA_NOTION_TOKEN"}


@pytest.mark.asyncio
async def test_notion_add_reports_the_created_page(monkeypatch):
    """A real artifact grounds what Reachy says next."""
    import reachy_companion.tools.notion_add as notion_add_module

    monkeypatch.setattr(
        notion_add_module.notion_client,
        "add_page",
        lambda **kwargs: {"id": "page1", "url": "https://notion.example.invalid/page1"},
    )
    out = await NotionAdd()(deps=_deps(), title="Buy a lamp", type="購物")
    assert out["ok"] is True and out["page_id"] == "page1"


@pytest.mark.asyncio
async def test_notion_add_reports_an_api_error_without_echoing_the_note(monkeypatch, caplog):
    """A Notion failure is tool output -- and finding 7 says a redacted one."""
    import logging

    import reachy_companion.tools.notion_add as notion_add_module

    sentinel = "SENTINEL_PRIVATE_x7"

    def boom(**kwargs):
        raise notion_add_module.NotionError(
            "HTTP 400 on POST /pages", status=400, body={"message": sentinel}
        )

    monkeypatch.setattr(notion_add_module.notion_client, "add_page", boom)
    caplog.set_level(logging.DEBUG)
    out = await NotionAdd()(deps=_deps(), title=sentinel)
    assert out["ok"] is False and out["error"]
    assert sentinel not in out["error"]
    assert sentinel not in caplog.text


@pytest.mark.asyncio
async def test_notion_add_rejects_an_empty_title():
    """A row with no title is unusable in the database."""
    out = await NotionAdd()(deps=_deps(), title="   ")
    assert out["ok"] is False


def test_notion_add_reaches_the_model_session():
    """The locked profile must list it, or the model never sees it."""
    core_tools = importlib.import_module("reachy_companion.tools.core_tools")
    core_tools.initialize_tools(force=True)
    try:
        assert "notion_add" in {spec["name"] for spec in core_tools.get_tool_specs()}
    finally:
        core_tools._TOOLS_SIGNATURE = None
```

- [ ] **Step 2: Run the test to verify it fails**

```powershell
cd C:\Project\Reachy-mini\reachy_companion
..\.venv\Scripts\python.exe -m pytest tests/test_hanova_notion.py -q
```

Expected: collection error — `ModuleNotFoundError: No module named 'reachy_companion.hanova.notion_client'`.

- [ ] **Step 3: Implement `hanova/notion_client.py`**

Create `reachy_companion/src/reachy_companion/hanova/notion_client.py`:

```python
"""Notion note writer, adapted from upstream `notion.py` (D-018).

This is a *schema-specific* writer for one notes database, and is deliberately
separate from the app's general remote Notion MCP lane (`NOTION_MCP_URL` /
`NOTION_MCP_TOKEN`): different auth, different surface, different keys.

Two things upstream did are not ported. The data-source id came from a cache
file committed to a public repo (`bin/notion/.cache/data_sources.json`); here it
is `HANOVA_NOTION_DATA_SOURCE_ID`. And the `Owner` select's options were real
people's names, so that property is dropped entirely.

The API version is pinned to the data-sources release. Do not bump it blind.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from reachy_companion.hanova import settings, sync_http


logger = logging.getLogger(__name__)

API_BASE = "https://api.notion.com/v1"
API_VERSION = "2025-09-03"
# The API hard limit is 2000 characters per rich-text run; leave headroom.
BLOCK_LIMIT = 1900
_TIMEOUT_S = 30

TYPE_OPTIONS: tuple[str, ...] = ("購物", "待辦", "備忘", "聯絡", "其他")
STATUS_OPTIONS: tuple[str, ...] = ("待辦", "進行中", "完成")
# Upstream's rule: action-oriented rows start as pending so a status filter finds them.
_ACTIONABLE_TYPES = ("購物", "待辦", "聯絡")


class NotionError(RuntimeError):
    """A Notion API call that did not succeed.

    The parsed body is kept as `.body` for callers that need it, but the string
    form carries only the method, the path and the status: Notion echoes the
    submitted properties back inside a validation error, which would otherwise
    put the note the user dictated into the log (review finding 7).
    """

    def __init__(self, message: str, status: int | None = None, body: Dict[str, Any] | None = None) -> None:
        """Record the status and body without putting either into the message."""
        self.status = status
        self.body = body or {}
        super().__init__(message)


def friendly_message(exc: BaseException) -> str:
    """A fixed, identifier-free reason the model may say out loud (finding 7)."""
    status = getattr(exc, "status", None)
    if status == 401:
        return "the Notion integration token is not accepted"
    if status == 404:
        return "the Notion database could not be found for this integration"
    if isinstance(status, int):
        return f"Notion returned an error (HTTP {status})"
    return "the note could not be saved to Notion"


def notion_request(method: str, path: str, body: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Make one authenticated Notion API call. Raises NotionError on failure."""
    token = settings.notion_token()
    if not token:
        raise NotionError("HANOVA_NOTION_TOKEN is not set.")

    headers = {"Authorization": f"Bearer {token}", "Notion-Version": API_VERSION}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    status, raw = sync_http.request_bytes(method, f"{API_BASE}{path}", headers, data, _TIMEOUT_S)
    try:
        parsed = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        parsed = {"raw": raw.decode("utf-8", "replace")}
    if not (200 <= status < 300):
        # Finding 7: the body stays on the exception, out of the message.
        raise NotionError(f"HTTP {status} on {method} {path}", status=status, body=parsed)
    return parsed if isinstance(parsed, dict) else {}


def chunk_text(text: str, limit: int = BLOCK_LIMIT) -> List[str]:
    """Split *text* into runs no longer than *limit* characters."""
    if not text:
        return []
    return [text[index : index + limit] for index in range(0, len(text), limit)]


def _blocks(text: str) -> List[Dict[str, Any]]:
    return [
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": chunk}}]},
        }
        for chunk in chunk_text(text)
    ]


def add_page(
    title: str,
    type_: str | None = None,
    status: str | None = None,
    tags: str | None = None,
    body: str | None = None,
) -> Dict[str, Any]:
    """Create one row in the configured notes data source."""
    data_source_id = settings.notion_data_source_id()
    if not data_source_id:
        raise NotionError("HANOVA_NOTION_DATA_SOURCE_ID is not set.")

    effective_status = status
    if effective_status is None and type_ in _ACTIONABLE_TYPES:
        effective_status = "待辦"

    properties: Dict[str, Any] = {
        settings.notion_title_prop(): {"title": [{"type": "text", "text": {"content": title}}]},
    }
    if type_:
        properties["Type"] = {"select": {"name": type_}}
    if effective_status:
        properties["Status"] = {"select": {"name": effective_status}}
    if tags:
        names = [tag.strip() for tag in tags.split(",") if tag.strip()]
        if names:
            properties["Tags"] = {"multi_select": [{"name": name} for name in names]}

    payload: Dict[str, Any] = {"parent": {"data_source_id": data_source_id}, "properties": properties}
    if body:
        payload["children"] = _blocks(body)
    return notion_request("POST", "/pages", payload)
```

- [ ] **Step 4: Implement `tools/notion_add.py`**

Create `reachy_companion/src/reachy_companion/tools/notion_add.py`:

```python
"""Write one row into the shared notes database (D-018). Filename == Tool.name."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict

from reachy_companion.hanova import redact, settings, notion_client
from reachy_companion.hanova.notion_client import NotionError, friendly_message
from reachy_companion.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


class NotionAdd(Tool):
    """Add a note, shopping item or reminder to the shared notes database."""

    name = "notion_add"
    description = "Save a note to the shared notes database. 用於記錄備忘、購物、待辦。"
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "The note itself, in one line."},
            "type": {
                "type": "string",
                "enum": list(notion_client.TYPE_OPTIONS),
                "description": "What kind of note this is.",
            },
            "status": {
                "type": "string",
                "enum": list(notion_client.STATUS_OPTIONS),
                "description": "Optional status; actionable types default to pending.",
            },
            "tags": {"type": "string", "description": "Optional comma-separated tags."},
            "body": {"type": "string", "description": "Optional longer detail."},
        },
        "required": ["title"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Create the row and report the real page id and URL."""
        available, reason = settings.tool_status(self.name)
        if not available:
            return settings.unavailable(reason)

        title = str(kwargs.get("title", "")).strip()
        if not title:
            return {"ok": False, "error": "title is required"}

        logger.info("Tool call: notion_add title=%s", redact.text(title))
        try:
            page = await asyncio.to_thread(
                notion_client.add_page,
                title=title,
                type_=str(kwargs.get("type") or "") or None,
                status=str(kwargs.get("status") or "") or None,
                tags=str(kwargs.get("tags") or "") or None,
                body=str(kwargs.get("body") or "") or None,
            )
        except (NotionError, OSError, ValueError, KeyError) as exc:
            logger.warning("notion_add failed: %s", redact.error(exc))
            return {"ok": False, "error": friendly_message(exc)}
        return {"ok": True, "page_id": page.get("id"), "url": page.get("url"), "title": title}
```

- [ ] **Step 5: Enable the tool in the locked profile**

In `reachy_companion/profiles/_reachy_companion_locked_profile/profile.md`, add to `default_tools` immediately after `"task_delete",`:

```toml
  "task_delete",
  "notion_add",
```

- [ ] **Step 6: Run the test to verify it passes**

```powershell
cd C:\Project\Reachy-mini\reachy_companion
..\.venv\Scripts\python.exe -m pytest tests/test_hanova_notion.py -q
```

Expected: green — **15** test functions, none parametrised: **15 collected
cases**. Record the exact number.

- [ ] **Step 7: Run the full gate**

```powershell
cd C:\Project\Reachy-mini\reachy_companion
..\.venv\Scripts\python.exe -m pytest -q
..\.venv\Scripts\python.exe -m ruff check src tests
..\.venv\Scripts\python.exe -m ruff format --check src tests
..\.venv\Scripts\python.exe -m mypy
```

Expected: pytest green (30 documented skips), ruff clean, mypy `Success: no issues found`.

- [ ] **Step 8: Commit**

```bash
cd /c/Project/Reachy-mini
git rev-parse --abbrev-ref HEAD   # must print feat/ha-nova-port (Task 0)
git add reachy_companion/src/reachy_companion/hanova/notion_client.py \
        reachy_companion/src/reachy_companion/tools/notion_add.py \
        reachy_companion/profiles/_reachy_companion_locked_profile/profile.md \
        reachy_companion/tests/test_hanova_notion.py
git commit -m "feat(hanova): notion_add note writer with config-supplied data source"
```

---

### Task 10: Google Drive — `drive_list`, `drive_trash`, `drive_upload`

Implements R1, R2's reinterpretation of `drive_upload` (upstream took an absolute path *on the operator's Mac*, which the robot does not have — here it captures one camera frame and uploads that), R3 (both write tools gated), R5.

Two upstream defaults are deliberately reversed: uploads are **never** made anyone-with-link readable (upstream defaulted `shareable: true`, `host-tools.py:163`), and `files.delete` stays unexposed — trashing only, recoverable for about 30 days.

**Approved non-goal — Drive restore (review round 1, finding 17).** Upstream
exposed an untrash operation; this port does not, and that is a scope decision
the controller accepted rather than an omission. The reasoning: `drive_trash` is
already the least destructive write in the port — the item stays in Drive's Trash
for about 30 days and the user can restore it from the Drive UI in two clicks,
on any device, without the robot. Adding a voice-driven restore would add a
second fuzzy-match surface over the *trash* namespace (where duplicate names are
common, because that is where deleted duplicates go) for a capability the user
already has. `hanova/gdrive.py` keeps `set_trashed(file_id, trashed: bool)`
with the flag, because the API is the same call either way, but **no tool exposes
`trashed=False`** and none may be added without revisiting this decision. This is
recorded in D-018 (Task 14) and in the persona: when asked to undo a trash,
Reachy says it can be restored from the Drive trash and does not attempt it.

**Files:**
- Create: `reachy_companion/src/reachy_companion/hanova/gdrive.py`
- Create: `reachy_companion/src/reachy_companion/tools/drive_list.py`
- Create: `reachy_companion/src/reachy_companion/tools/drive_trash.py`
- Create: `reachy_companion/src/reachy_companion/tools/drive_upload.py`
- Modify: `reachy_companion/profiles/_reachy_companion_locked_profile/profile.md` (`default_tools` gains the three names)
- Test: `reachy_companion/tests/test_hanova_drive.py`

**Interfaces:**
- Consumes: `hanova.sync_http.request_bytes` (Task 7); `hanova.settings.{drive_secrets_path, drive_parent_id, tool_status, unavailable}`; `hanova.redact`; `hanova.confirm.{GATE, confirmation_expired}` — `claim`/`complete`/`release`, per finding 4; `deps.reachy_mini.media.get_frame_jpeg()` and `deps.camera_enabled` (the same seam `tools/camera.py:47-54` uses).
- Produces:
  - `hanova.gdrive.DRIVE_API: str`, `hanova.gdrive.UPLOAD_API: str`
  - `hanova.gdrive.DriveError(RuntimeError)`
  - `hanova.gdrive.drive_token() -> str`
  - `hanova.gdrive.list_files(parent_id: str, limit: int = 50, include_trashed: bool = False) -> List[Dict[str, Any]]`
  - `hanova.gdrive.get_file(file_id: str) -> Dict[str, Any]`
  - `hanova.gdrive.set_trashed(file_id: str, trashed: bool = True) -> Dict[str, Any]`
  - `hanova.gdrive.upload_bytes(data: bytes, name: str, mime: str, parent_id: str) -> Dict[str, Any]`
  - `tools.drive_list.DriveList`, `tools.drive_trash.DriveTrash`, `tools.drive_upload.DriveUpload`

- [ ] **Step 1: Write the failing test**

Create `reachy_companion/tests/test_hanova_drive.py`:

```python
"""Contract tests for the three Drive tools (D-018, R1/R2/R3/R5).

Also pins review round 1 findings 4 (claim/complete/release), 7 (no file name or
id in a log line) and 17 (Drive restore is an approved non-goal).
"""

import json
import types
import importlib
from pathlib import Path

import pytest

from reachy_companion.hanova import gdrive, sync_http
from reachy_companion.hanova.confirm import GATE
from reachy_companion.tools.drive_list import DriveList
from reachy_companion.tools.drive_trash import DriveTrash
from reachy_companion.tools.drive_upload import DriveUpload


PARENT_ID = "folder-under-test"
JPEG_BYTES = b"\xff\xd8\xff\xe0fakejpeg"


def _deps(camera_enabled: bool = True, frame: bytes | None = JPEG_BYTES):
    media = types.SimpleNamespace(get_frame_jpeg=lambda: frame)
    return types.SimpleNamespace(
        reachy_mini=types.SimpleNamespace(media=media),
        instance_path=None,
        camera_enabled=camera_enabled,
    )


@pytest.fixture(autouse=True)
def configured(monkeypatch, tmp_path):
    """A configured drive family and an empty confirmation gate."""
    secrets = tmp_path / "google-oauth.json"
    secrets.write_text(
        json.dumps({"clientId": "cid", "clientSecret": "csecret", "refreshToken": "rtok"}), encoding="utf-8"
    )
    monkeypatch.setenv("HERMES_DRIVE_SECRETS", str(secrets))
    monkeypatch.setenv("HANOVA_DRIVE_PARENT_ID", PARENT_ID)
    monkeypatch.delenv("HANOVA_CONFIRM_TTL_S", raising=False)
    GATE.reset()
    GATE.begin_session()
    yield
    GATE.reset()


def test_tool_names_match_their_filenames():
    """The loader resolves tools by filename == Tool.name."""
    assert DriveList.name == "drive_list"
    assert DriveTrash.name == "drive_trash"
    assert DriveUpload.name == "drive_upload"


def test_descriptions_carry_no_personal_identifier():
    """R10: upstream embedded the real Drive folder id in two descriptions."""
    for text in (DriveList().description, DriveTrash().description, DriveUpload().description):
        assert "@" not in text
        assert PARENT_ID not in text
        assert len(text) <= 120


def test_drive_token_is_minted_from_the_refresh_token(monkeypatch):
    """A fresh token per run means no cached token file can go stale."""
    seen = {}

    def fake_request(method, url, headers, data=None, timeout_s=15):
        seen["url"] = url
        seen["data"] = data
        return 200, b'{"access_token": "drive-token"}'

    monkeypatch.setattr(sync_http, "request_bytes", fake_request)
    assert gdrive.drive_token() == "drive-token"
    assert seen["url"] == "https://oauth2.googleapis.com/token"
    assert b"refresh_token=rtok" in seen["data"]


def test_drive_token_accepts_the_nested_secret_shape(monkeypatch, tmp_path):
    """The operator's secret file may be flat or nested; both must work."""
    secrets = tmp_path / "nested.json"
    secrets.write_text(
        json.dumps({"gmail": {"oauth": {"clientId": "c", "clientSecret": "s", "refreshToken": "r"}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_DRIVE_SECRETS", str(secrets))
    monkeypatch.setattr(
        sync_http, "request_bytes", lambda *a, **k: (200, b'{"access_token": "drive-token"}')
    )
    assert gdrive.drive_token() == "drive-token"


def test_drive_token_without_a_secret_file_raises(monkeypatch):
    """A missing credential is a configuration fact, surfaced as DriveError."""
    monkeypatch.delenv("HERMES_DRIVE_SECRETS")
    with pytest.raises(gdrive.DriveError):
        gdrive.drive_token()


def test_list_files_queries_one_folder_level(monkeypatch):
    """drive_list reads a folder, it does not walk the whole Drive."""
    seen = {}

    def fake_request(method, url, headers, data=None, timeout_s=15):
        if url.endswith("/token"):
            return 200, b'{"access_token": "drive-token"}'
        seen["url"] = url
        return 200, b'{"files": [{"id": "f1", "name": "notes.txt"}]}'

    monkeypatch.setattr(sync_http, "request_bytes", fake_request)
    files = gdrive.list_files(PARENT_ID, limit=10)
    assert files == [{"id": "f1", "name": "notes.txt"}]
    assert "in%20parents" in seen["url"] or "in+parents" in seen["url"]
    assert "pageSize=10" in seen["url"]


def test_upload_bytes_sends_a_multipart_body_and_never_shares(monkeypatch):
    """Upstream defaulted to anyone-with-link; we never create a permission."""
    calls: list[str] = []

    def fake_request(method, url, headers, data=None, timeout_s=15):
        calls.append(url)
        if url.endswith("/token"):
            return 200, b'{"access_token": "drive-token"}'
        assert headers["Content-Type"].startswith("multipart/related; boundary=")
        assert b"fakejpeg" in (data or b"")
        return 200, b'{"id": "f9", "name": "reachy.jpg", "webViewLink": "https://drive.example.invalid/f9"}'

    monkeypatch.setattr(sync_http, "request_bytes", fake_request)
    out = gdrive.upload_bytes(JPEG_BYTES, "reachy.jpg", "image/jpeg", PARENT_ID)
    assert out["id"] == "f9"
    assert not any("/permissions" in url for url in calls)


@pytest.mark.asyncio
async def test_drive_list_is_unavailable_without_config(monkeypatch):
    """R5: an unconfigured tool answers, it does not raise, and it names the key."""
    monkeypatch.delenv("HERMES_DRIVE_SECRETS")
    out = await DriveList()(deps=_deps())
    assert out == {"status": "unavailable", "reason": "HERMES_DRIVE_SECRETS"}


def test_no_tool_can_untrash_anything():
    """Finding 17: Drive restore is an approved non-goal, enforced structurally."""
    tools_dir = Path(importlib.import_module("reachy_companion.tools").__file__).parent
    for name in ("drive_list", "drive_trash", "drive_upload"):
        source = (tools_dir / f"{name}.py").read_text(encoding="utf-8")
        assert "trashed=False" not in source, name
        assert "untrash" not in source.lower(), name
    trash_source = (tools_dir / "drive_trash.py").read_text(encoding="utf-8")
    assert trash_source.count("set_trashed") == 1, "exactly one trash call, and it trashes"


@pytest.mark.asyncio
async def test_a_transient_drive_failure_keeps_the_authorisation(monkeypatch):
    """Finding 4: a 503 must not cost the user their confirmation."""
    import reachy_companion.tools.drive_trash as drive_trash_module

    monkeypatch.setattr(
        drive_trash_module.gdrive, "get_file", lambda file_id: {"id": file_id, "name": "notes.txt"}
    )
    attempts = {"n": 0}

    def flaky(file_id, trashed=True):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise drive_trash_module.DriveError("Drive PATCH -> HTTP 503", status=503)
        return {"id": file_id, "trashed": True}

    monkeypatch.setattr(drive_trash_module.gdrive, "set_trashed", flaky)
    await DriveTrash()(deps=_deps(), file_id="f1")
    first = await DriveTrash()(deps=_deps(), confirm=True)
    assert first["ok"] is False and first.get("retryable") is True
    second = await DriveTrash()(deps=_deps(), confirm=True)
    assert second["ok"] is True and second["status"] == "trashed"


@pytest.mark.asyncio
async def test_drive_logs_never_carry_a_file_name_or_id(monkeypatch, caplog):
    """Finding 7: Drive file names are personal data and ids are identifiers."""
    import logging

    import reachy_companion.tools.drive_trash as drive_trash_module

    sentinel = "SENTINEL_PRIVATE_x7"
    monkeypatch.setattr(
        drive_trash_module.gdrive, "get_file", lambda file_id: {"id": file_id, "name": sentinel}
    )
    monkeypatch.setattr(
        drive_trash_module.gdrive, "set_trashed", lambda file_id, trashed=True: {"id": file_id}
    )
    caplog.set_level(logging.DEBUG)
    await DriveTrash()(deps=_deps(), file_id=sentinel)
    await DriveTrash()(deps=_deps(), confirm=True)
    assert sentinel not in caplog.text


@pytest.mark.asyncio
async def test_drive_list_returns_compact_rows(monkeypatch):
    """The model gets names and ids, not raw Drive payloads."""
    import reachy_companion.tools.drive_list as drive_list_module

    monkeypatch.setattr(
        drive_list_module.gdrive,
        "list_files",
        lambda parent_id, limit=50, include_trashed=False: [
            {"id": "f1", "name": "notes.txt", "mimeType": "text/plain", "modifiedTime": "2026-08-01T00:00:00Z"}
        ],
    )
    out = await DriveList()(deps=_deps())
    assert out["ok"] is True and out["count"] == 1
    assert out["files"][0]["name"] == "notes.txt"


@pytest.mark.asyncio
async def test_drive_trash_arms_with_the_real_file_name(monkeypatch):
    """R3: the read-back must name the file, not echo the id the model guessed."""
    import reachy_companion.tools.drive_trash as drive_trash_module

    monkeypatch.setattr(
        drive_trash_module.gdrive,
        "get_file",
        lambda file_id: {"id": file_id, "name": "holiday-photos", "mimeType": "application/vnd.google-apps.folder"},
    )

    def fail_trash(file_id, trashed=True):
        raise AssertionError("drive_trash must not trash before confirmation")

    monkeypatch.setattr(drive_trash_module.gdrive, "set_trashed", fail_trash)
    out = await DriveTrash()(deps=_deps(), file_id="f1")
    assert out["status"] == "needs_confirmation" and "holiday-photos" in out["summary"]


@pytest.mark.asyncio
async def test_drive_trash_executes_the_armed_payload(monkeypatch):
    """The confirmed trash uses the id that was read back."""
    import reachy_companion.tools.drive_trash as drive_trash_module

    monkeypatch.setattr(
        drive_trash_module.gdrive, "get_file", lambda file_id: {"id": file_id, "name": "notes.txt"}
    )
    trashed = {}
    monkeypatch.setattr(
        drive_trash_module.gdrive,
        "set_trashed",
        lambda file_id, trashed=True: trashed.update(file_id=file_id) or {"id": file_id, "trashed": True},
    )
    await DriveTrash()(deps=_deps(), file_id="f1")
    out = await DriveTrash()(deps=_deps(), file_id="a-completely-different-id", confirm=True)
    assert out["ok"] is True and out["status"] == "trashed"
    assert trashed == {"file_id": "f1"}


@pytest.mark.asyncio
async def test_drive_trash_confirm_without_arm_is_refused(monkeypatch):
    """A confirm:true first call must trash nothing."""
    import reachy_companion.tools.drive_trash as drive_trash_module

    def fail_trash(file_id, trashed=True):
        raise AssertionError("drive_trash must not trash without a pending action")

    monkeypatch.setattr(drive_trash_module.gdrive, "set_trashed", fail_trash)
    out = await DriveTrash()(deps=_deps(), file_id="f1", confirm=True)
    assert out["status"] == "confirmation_expired"


@pytest.mark.asyncio
async def test_drive_upload_arms_then_captures_and_uploads(monkeypatch):
    """R2: this uploads a photo Reachy takes, captured at confirm time."""
    import reachy_companion.tools.drive_upload as drive_upload_module

    uploaded = {}

    def fake_upload(data, name, mime, parent_id):
        uploaded.update(data=data, name=name, mime=mime, parent_id=parent_id)
        return {"id": "f9", "name": name, "webViewLink": "https://drive.example.invalid/f9"}

    monkeypatch.setattr(drive_upload_module.gdrive, "upload_bytes", fake_upload)

    armed = await DriveUpload()(deps=_deps())
    assert armed["status"] == "needs_confirmation" and "camera" in armed["summary"].lower()
    assert uploaded == {}

    out = await DriveUpload()(deps=_deps(), confirm=True)
    assert out["ok"] is True and out["file_id"] == "f9"
    assert uploaded["data"] == JPEG_BYTES
    assert uploaded["mime"] == "image/jpeg"
    assert uploaded["parent_id"] == PARENT_ID


@pytest.mark.asyncio
async def test_drive_upload_reports_a_disabled_camera():
    """No camera means no photo; say so instead of uploading nothing."""
    out = await DriveUpload()(deps=_deps(camera_enabled=False))
    assert out["ok"] is False and "camera" in out["error"].lower()


@pytest.mark.asyncio
async def test_drive_upload_reports_a_missing_frame(monkeypatch):
    """A camera that yields no frame is a reportable failure at confirm time."""
    import reachy_companion.tools.drive_upload as drive_upload_module

    def fail_upload(data, name, mime, parent_id):
        raise AssertionError("drive_upload must not upload an empty frame")

    monkeypatch.setattr(drive_upload_module.gdrive, "upload_bytes", fail_upload)
    await DriveUpload()(deps=_deps())
    out = await DriveUpload()(deps=_deps(frame=None), confirm=True)
    assert out["ok"] is False and "frame" in out["error"].lower()


def test_all_three_tools_reach_the_model_session():
    """The locked profile must list them, or the model never sees them."""
    core_tools = importlib.import_module("reachy_companion.tools.core_tools")
    core_tools.initialize_tools(force=True)
    try:
        names = {spec["name"] for spec in core_tools.get_tool_specs()}
        assert {"drive_list", "drive_trash", "drive_upload"} <= names
    finally:
        core_tools._TOOLS_SIGNATURE = None
```

- [ ] **Step 2: Run the test to verify it fails**

```powershell
cd C:\Project\Reachy-mini\reachy_companion
..\.venv\Scripts\python.exe -m pytest tests/test_hanova_drive.py -q
```

Expected: collection error — `ModuleNotFoundError: No module named 'reachy_companion.hanova.gdrive'`.

- [ ] **Step 3: Implement `hanova/gdrive.py`**

Create `reachy_companion/src/reachy_companion/hanova/gdrive.py`:

```python
"""Google Drive v3 calls, adapted from upstream `gdrive.py` (D-018).

This uses a *different* OAuth grant from `gauth.py` -- Drive's own secret file,
whose scope is full `https://www.googleapis.com/auth/drive`. Copying it to the
robot grants the robot full Drive, which is why the drive family is off by
default and why both write tools are confirmation-gated.

Two upstream defaults are reversed here on purpose: nothing is ever made
anyone-with-link readable, and `files.delete` is not exposed at all.
"""

from __future__ import annotations

import json
import uuid
import logging
import urllib.parse
from typing import Any, Dict, List

from reachy_companion.hanova import settings, sync_http


logger = logging.getLogger(__name__)

DRIVE_API = "https://www.googleapis.com/drive/v3"
UPLOAD_API = "https://www.googleapis.com/upload/drive/v3"
TOKEN_URI = "https://oauth2.googleapis.com/token"
FOLDER_MIME = "application/vnd.google-apps.folder"
_TIMEOUT_S = 60
_UPLOAD_TIMEOUT_S = 600


class DriveError(RuntimeError):
    """A Drive call that could not be made or did not succeed.

    Finding 7: Drive error bodies quote file names and ids back. The status goes
    on the exception; the body does not go into the message.
    """

    def __init__(self, message: str, status: int | None = None, body: Dict[str, Any] | None = None) -> None:
        """Record the status and body without putting either into the message."""
        self.status = status
        self.body = body or {}
        super().__init__(message)


_STATUS_MESSAGES = {
    401: "the Drive credential needs to be re-authorised on the robot",
    403: "Drive refused that request for this account",
    404: "Drive could not find that item",
    429: "Drive is rate-limiting this account right now",
}
_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})


def friendly_message(exc: BaseException) -> str:
    """A fixed, identifier-free reason the model may say out loud (finding 7)."""
    status = getattr(exc, "status", None)
    if isinstance(status, int):
        return _STATUS_MESSAGES.get(status, f"Drive returned an error (HTTP {status})")
    return "the Drive request could not be completed"


def is_transient(exc: BaseException) -> bool:
    """Return whether a gated Drive tool may retry on the same confirmation."""
    status = getattr(exc, "status", None)
    if isinstance(status, int):
        return status in _RETRYABLE_STATUSES
    return isinstance(exc, OSError)


def _load_oauth() -> Dict[str, str]:
    path = settings.drive_secrets_path()
    if path is None or not path.is_file():
        raise DriveError("HERMES_DRIVE_SECRETS is not set or the Drive OAuth secret file is missing.")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DriveError(f"Could not read the Drive OAuth secret: {exc}") from exc
    secret = raw if "refreshToken" in raw else raw.get("gmail", {}).get("oauth", {})
    if not secret.get("refreshToken"):
        raise DriveError("The Drive OAuth secret has no refreshToken.")
    return {str(key): str(value) for key, value in secret.items()}


def drive_token() -> str:
    """Mint a fresh Drive access token from the stored refresh token."""
    secret = _load_oauth()
    payload = urllib.parse.urlencode(
        {
            "client_id": secret["clientId"],
            "client_secret": secret["clientSecret"],
            "refresh_token": secret["refreshToken"],
            "grant_type": "refresh_token",
        }
    ).encode()
    status, raw = sync_http.request_bytes(
        "POST", TOKEN_URI, {"Content-Type": "application/x-www-form-urlencoded"}, payload, _TIMEOUT_S
    )
    if not (200 <= status < 300):
        raise DriveError(f"Drive token refresh failed: HTTP {status}")
    try:
        return str(json.loads(raw)["access_token"])
    except (json.JSONDecodeError, KeyError) as exc:
        raise DriveError(f"Drive token response was unreadable: {exc}") from exc


def _json_call(
    method: str,
    url: str,
    payload: Dict[str, Any] | None = None,
    timeout_s: int = _TIMEOUT_S,
) -> Dict[str, Any]:
    headers = {"Authorization": f"Bearer {drive_token()}"}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json; charset=UTF-8"
    status, raw = sync_http.request_bytes(method, url, headers, data, timeout_s)
    try:
        parsed = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        parsed = {"raw": raw.decode("utf-8", "replace")}
    if not (200 <= status < 300):
        # Finding 7: the body stays on the exception, out of the message.
        raise DriveError(f"Drive {method} -> HTTP {status}", status=status, body=parsed)
    return parsed if isinstance(parsed, dict) else {}


def list_files(parent_id: str, limit: int = 50, include_trashed: bool = False) -> List[Dict[str, Any]]:
    """List one folder level: files and folders, newest first."""
    clauses = [f"'{parent_id}' in parents"]
    if not include_trashed:
        clauses.append("trashed = false")
    query = urllib.parse.quote(" and ".join(clauses))
    order = urllib.parse.quote("folder,modifiedTime desc")
    url = (
        f"{DRIVE_API}/files?q={query}"
        "&fields=files(id,name,mimeType,modifiedTime,size,trashed,webViewLink)"
        f"&pageSize={min(max(1, limit), 1000)}&orderBy={order}"
    )
    response = _json_call("GET", url)
    files = response.get("files", [])
    return list(files) if isinstance(files, list) else []


def get_file(file_id: str) -> Dict[str, Any]:
    """Read one file's metadata, so a confirmation can name it."""
    fields = "id,name,mimeType,webViewLink,trashed,modifiedTime,size"
    return _json_call("GET", f"{DRIVE_API}/files/{file_id}?fields={fields}")


def set_trashed(file_id: str, trashed: bool = True) -> Dict[str, Any]:
    """Move a file or folder to Trash (recoverable ~30 days), or restore it.

    The `trashed=False` direction is an **approved non-goal** (review finding 17):
    no tool exposes it, because restoring is two clicks in the Drive UI and a
    voice-driven restore would add a second fuzzy match over the trash namespace.
    The parameter stays only because it is the same API call either way.
    """
    url = f"{DRIVE_API}/files/{file_id}?fields=id,name,trashed,mimeType"
    return _json_call("PATCH", url, {"trashed": trashed})


def upload_bytes(data: bytes, name: str, mime: str, parent_id: str) -> Dict[str, Any]:
    """Upload in-memory bytes as a new file. The result is private by default."""
    boundary = f"==={uuid.uuid4().hex}=="
    metadata = {"name": name, "parents": [parent_id]}
    body = (
        f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode()
        + json.dumps(metadata).encode()
        + f"\r\n--{boundary}\r\nContent-Type: {mime}\r\n\r\n".encode()
        + data
        + f"\r\n--{boundary}--\r\n".encode()
    )
    headers = {
        "Authorization": f"Bearer {drive_token()}",
        "Content-Type": f"multipart/related; boundary={boundary}",
        "Content-Length": str(len(body)),
    }
    url = f"{UPLOAD_API}/files?uploadType=multipart&fields=id,name,mimeType,webViewLink,size,parents"
    status, raw = sync_http.request_bytes("POST", url, headers, body, _UPLOAD_TIMEOUT_S)
    try:
        parsed = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        parsed = {"raw": raw.decode("utf-8", "replace")}
    if not (200 <= status < 300):
        raise DriveError(f"Drive upload -> HTTP {status}", status=status, body=parsed)
    return parsed if isinstance(parsed, dict) else {}
```

- [ ] **Step 4: Implement `tools/drive_list.py`**

Create `reachy_companion/src/reachy_companion/tools/drive_list.py`:

```python
"""List one Drive folder (D-018). Filename == Tool.name."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict

from reachy_companion.hanova import redact, gdrive, settings
from reachy_companion.hanova.gdrive import DriveError, friendly_message
from reachy_companion.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)

_DEFAULT_LIMIT = 50


class DriveList(Tool):
    """List what is in the configured Drive folder."""

    name = "drive_list"
    description = "List files in the shared Drive folder. 用於查雲端硬碟裡有什麼檔案。"
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "How many entries to return. Default 50.", "minimum": 1, "maximum": 200},
        },
        "required": [],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Return a compact listing of one folder level."""
        available, reason = settings.tool_status(self.name)
        if not available:
            return settings.unavailable(reason)

        try:
            limit = int(kwargs.get("limit") or _DEFAULT_LIMIT)
        except (TypeError, ValueError):
            limit = _DEFAULT_LIMIT
        limit = max(1, min(200, limit))

        logger.info("Tool call: drive_list limit=%d", limit)
        try:
            files = await asyncio.to_thread(gdrive.list_files, settings.drive_parent_id(), limit, False)
        except (DriveError, OSError, ValueError, KeyError) as exc:
            logger.warning("drive_list failed: %s", redact.error(exc))
            return {"ok": False, "error": friendly_message(exc)}

        compact = [
            {
                "id": entry.get("id"),
                "name": entry.get("name"),
                "is_folder": entry.get("mimeType") == gdrive.FOLDER_MIME,
                "modified": entry.get("modifiedTime"),
            }
            for entry in files
        ]
        return {"ok": True, "count": len(compact), "files": compact}
```

- [ ] **Step 5: Implement `tools/drive_trash.py`**

Create `reachy_companion/src/reachy_companion/tools/drive_trash.py`:

```python
"""Move a Drive item to Trash, behind a confirmation gate (D-018, R3).

Trashing a folder trashes everything inside it, so the first call reads the real
name and type back from Drive rather than trusting the id the model produced.
Nothing here can permanently delete: `files.delete` is not exposed.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict

from reachy_companion.hanova import redact, gdrive, settings
from reachy_companion.hanova.gdrive import DriveError, is_transient, friendly_message
from reachy_companion.hanova.confirm import GATE, confirmation_expired
from reachy_companion.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


class DriveTrash(Tool):
    """Move one Drive file or folder to Trash after the user confirms it."""

    name = "drive_trash"
    description = "Move a Drive item to Trash. 需要先確認：先讀回檔名再丟。"
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "file_id": {
                "type": "string",
                "description": "Drive file or folder id, from drive_list. Omit when confirming.",
            },
            "confirm": {
                "type": "boolean",
                "description": "Set true only after the user has confirmed the exact item read back to them.",
            },
        },
        # Finding 4: optional in the schema, mandatory in the non-confirm branch.
        "required": [],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Read back the real item, or execute a previously confirmed trash."""
        available, reason = settings.tool_status(self.name)
        if not available:
            return settings.unavailable(reason)

        if bool(kwargs.get("confirm")):
            pending = GATE.claim(self.name)
            if pending is None:
                return confirmation_expired()
            logger.info("Tool call: drive_trash confirmed for %s", redact.ident(pending.payload.get("file_id")))
            try:
                await asyncio.to_thread(gdrive.set_trashed, str(pending.payload["file_id"]), True)
            except (DriveError, OSError, ValueError, KeyError) as exc:
                logger.warning("drive_trash failed: %s", redact.error(exc))
                if is_transient(exc):
                    GATE.release(self.name, pending.claim_id)
                    return {"ok": False, "error": friendly_message(exc), "retryable": True}
                GATE.complete(self.name, pending.claim_id)
                return {"ok": False, "error": friendly_message(exc)}
            GATE.complete(self.name, pending.claim_id)
            return {"ok": True, "status": "trashed", "summary": pending.summary}

        file_id = str(kwargs.get("file_id", "")).strip()
        if not file_id:
            return {"ok": False, "error": "file_id is required"}

        logger.info("Tool call: drive_trash resolving %s", redact.ident(file_id))
        try:
            item = await asyncio.to_thread(gdrive.get_file, file_id)
        except (DriveError, OSError, ValueError, KeyError) as exc:
            logger.warning("drive_trash lookup failed: %s", redact.error(exc))
            return {"ok": False, "error": friendly_message(exc)}

        name = str(item.get("name") or file_id)
        kind = "folder and everything in it" if item.get("mimeType") == gdrive.FOLDER_MIME else "file"
        return GATE.arm(
            self.name,
            f"move the Drive {kind} {name!r} to Trash",
            {"file_id": item.get("id") or file_id, "name": name},
        )
```

- [ ] **Step 6: Implement `tools/drive_upload.py`**

Create `reachy_companion/src/reachy_companion/tools/drive_upload.py`:

```python
"""Upload a photo Reachy takes to Drive, behind a confirmation gate (D-018, R2/R3).

Upstream's `drive_upload` took an absolute path *on the operator's Mac*
(`server.py:929`), which is meaningless on a robot. Reinterpreted: the only file
the robot can meaningfully offer is one it just produced, so this captures a
single camera frame and uploads that.

The frame is captured on the **confirm** call, not when the action is armed, so
what lands in Drive is what the room looked like when the user said yes.
"""

from __future__ import annotations

import time
import asyncio
import logging
from typing import Any, Dict

from reachy_companion.hanova import redact, gdrive, settings
from reachy_companion.hanova.gdrive import DriveError, is_transient, friendly_message
from reachy_companion.hanova.confirm import GATE, confirmation_expired
from reachy_companion.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


class DriveUpload(Tool):
    """Take a photo and upload it to the configured Drive folder."""

    name = "drive_upload"
    description = "Take a photo and upload it to Drive. 需要先確認：拍照並上傳雲端。"
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Optional file name for the photo."},
            "confirm": {
                "type": "boolean",
                "description": "Set true only after the user has confirmed the upload read back to them.",
            },
        },
        "required": [],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Read back the upload, or capture a frame and upload it once confirmed."""
        available, reason = settings.tool_status(self.name)
        if not available:
            return settings.unavailable(reason)
        if not getattr(deps, "camera_enabled", False):
            return {"ok": False, "error": "the camera is disabled, so there is no photo to upload"}

        if bool(kwargs.get("confirm")):
            pending = GATE.claim(self.name)
            if pending is None:
                return confirmation_expired()
            filename = str(pending.payload["name"])
            logger.info("Tool call: drive_upload confirmed as %s", redact.text(filename))

            frame = deps.reachy_mini.media.get_frame_jpeg()
            if not frame:
                # The camera failing is transient; keep the authorisation so the
                # user can just say "try again" (finding 4).
                GATE.release(self.name, pending.claim_id)
                return {"ok": False, "error": "no camera frame was available to upload", "retryable": True}
            try:
                uploaded = await asyncio.to_thread(
                    gdrive.upload_bytes, bytes(frame), filename, "image/jpeg", settings.drive_parent_id()
                )
            except (DriveError, OSError, ValueError, KeyError) as exc:
                logger.warning("drive_upload failed: %s", redact.error(exc))
                if is_transient(exc):
                    GATE.release(self.name, pending.claim_id)
                    return {"ok": False, "error": friendly_message(exc), "retryable": True}
                GATE.complete(self.name, pending.claim_id)
                return {"ok": False, "error": friendly_message(exc)}
            GATE.complete(self.name, pending.claim_id)
            return {
                "ok": True,
                "status": "uploaded",
                "file_id": uploaded.get("id"),
                "name": uploaded.get("name"),
                "link": uploaded.get("webViewLink"),
            }

        requested = str(kwargs.get("name") or "").strip()
        filename = requested or f"reachy-{time.strftime('%Y%m%d-%H%M%S')}.jpg"
        if not filename.lower().endswith((".jpg", ".jpeg")):
            filename = f"{filename}.jpg"
        return GATE.arm(
            self.name,
            f"take a photo with Reachy's camera and upload it to your Drive folder as {filename!r}",
            {"name": filename},
        )
```

- [ ] **Step 7: Enable the three tools in the locked profile**

In `reachy_companion/profiles/_reachy_companion_locked_profile/profile.md`, add to `default_tools` immediately after `"notion_add",`:

```toml
  "notion_add",
  "drive_list",
  "drive_trash",
  "drive_upload",
```

- [ ] **Step 8: Run the test to verify it passes**

```powershell
cd C:\Project\Reachy-mini\reachy_companion
..\.venv\Scripts\python.exe -m pytest tests/test_hanova_drive.py -q
```

Expected: green — **19** test functions, none parametrised: **19 collected
cases**. Record the exact number.

- [ ] **Step 9: Run the full gate**

```powershell
cd C:\Project\Reachy-mini\reachy_companion
..\.venv\Scripts\python.exe -m pytest -q
..\.venv\Scripts\python.exe -m ruff check src tests
..\.venv\Scripts\python.exe -m ruff format --check src tests
..\.venv\Scripts\python.exe -m mypy
```

Expected: pytest green (30 documented skips), ruff clean, mypy `Success: no issues found`.

- [ ] **Step 10: Commit**

```bash
cd /c/Project/Reachy-mini
git rev-parse --abbrev-ref HEAD   # must print feat/ha-nova-port (Task 0)
git add reachy_companion/src/reachy_companion/hanova/gdrive.py \
        reachy_companion/src/reachy_companion/tools/drive_list.py \
        reachy_companion/src/reachy_companion/tools/drive_trash.py \
        reachy_companion/src/reachy_companion/tools/drive_upload.py \
        reachy_companion/profiles/_reachy_companion_locked_profile/profile.md \
        reachy_companion/tests/test_hanova_drive.py
git commit -m "feat(hanova): Drive list/trash plus camera-frame upload, both writes gated"
```

---

### Task 11: `email_send` — real outbound mail, gated

Implements R2 (`email_send` is in the inventory and GATED), R3 and R5. This is the single most irreversible tool in the port: it sends real mail from the operator's own account to a third party, triggered by a fuzzy voice intent. The gate is not decoration — the read-back must name the recipient *and* the subject before anything is sent.

**Review round 1, finding 5 — the read-back must cover the whole envelope.** The
first draft's summary named only the primary recipient and the subject, while a
`cc` and the entire message body were parked and sent **without ever being read
back**. That is the exact failure mode the gate exists to prevent: the user
confirms "send to Alice about dinner" and a second recipient they never heard
about receives a body they never heard either. The fix has four parts:

1. every recipient field is **normalised and validated** before anything is
   armed — split on commas, trimmed, each one checked for a plausible address,
   duplicates removed, and the whole set de-duplicated across To and CC;
2. the summary reads back **To, CC, the subject, and the body**;
3. the parked payload is the *only* source of the send, so nothing can be added
   between the two calls;
4. a test proves that **no recipient can reach `send_mail` that did not appear
   in the confirmed summary**.

**Review round 2, finding 4 — a digest is not a read-back.** Round 1 still put
only the body's *first line*, capped at 120 characters, plus a length and an
opaque hex digest into the summary. A user cannot verify text they were never
told, and "digest 4f2a9c31" is unverifiable by a person listening to a robot: two
materially different bodies — same opening line, different second paragraph —
produce confirmations that are indistinguishable by ear. The fix is to stop
summarising the body and start *including* it:

- **the accepted body is bounded at 500 characters.** Longer is rejected before
  anything is armed, with its own status (`body_too_long`) and a spoken reason,
  because a body that cannot be read back in full cannot be confirmed at all.
  500 characters is roughly 30 seconds of speech — the practical ceiling for
  something a person will actually listen to before saying yes;
- **the summary carries the entire normalised body**, verbatim, after the
  recipients and subject. `body_digest` survives only as an *additional*
  integrity token appended after the text, never as a substitute for it;
- **the persona is instructed to read the whole thing**, not to paraphrase or
  abbreviate it, and to say so if it is long;
- a test changes text **after the first line** and asserts the two summaries
  differ.

**Approved non-goal — BCC (review round 1, finding 17).** Upstream's
`gmail_send` accepted a BCC list. This port supports **To and CC only**, and
`send_mail` has no `bcc` parameter at all. A blind-carbon recipient is by
definition one the read-back cannot make visible to anyone else in the room, and
"a recipient the confirmation does not surface" is precisely what finding 5 is
about. The controller approved dropping it. The persona says so: asked to BCC
someone, Reachy explains it can only send to visible recipients.

**Files:**
- Create: `reachy_companion/src/reachy_companion/hanova/gmail_smtp.py`
- Create: `reachy_companion/src/reachy_companion/tools/email_send.py`
- Modify: `reachy_companion/profiles/_reachy_companion_locked_profile/profile.md` (`default_tools` gains `"email_send"`)
- Test: `reachy_companion/tests/test_hanova_email.py`

**Interfaces:**
- Consumes: `hanova.settings.{smtp_host, smtp_port, smtp_user, smtp_app_password, smtp_from_name, tool_status, unavailable}`; `hanova.redact`; `hanova.confirm.{GATE, confirmation_expired}`.
- Produces:
  - `hanova.gmail_smtp.SmtpError(RuntimeError)`
  - `hanova.gmail_smtp.MAX_BODY_CHARS: int` — `500` (round 2, finding 4): the longest body that can be honestly read back to a listening user
  - `hanova.gmail_smtp.smtp_factory() -> Any` — the seam tests monkeypatch; returns a context-manager SMTP connection
  - `hanova.gmail_smtp.normalize_recipients(raw: str | None) -> tuple[list[str], list[str]]` — returns `(valid, rejected)`, trimmed, de-duplicated, order-preserving (finding 5)
  - `hanova.gmail_smtp.normalize_body(raw: str) -> str` — collapse CRLF, strip trailing whitespace per line, strip the ends. The **one** normalisation, so what is summarised and what is sent are byte-identical (round 2, finding 4)
  - `hanova.gmail_smtp.body_digest(body: str) -> str` — `"<n> characters, digest ab12cd34"`. Round 2, finding 4 demoted this: it is appended **after** the full body text as an integrity token, and it is never the only description of the body
  - `hanova.gmail_smtp.is_transient(exc: BaseException) -> bool` — round 2, finding 9. Connection, disconnection, HELO and socket-timeout failures are transient; authentication, recipient-refused, sender-refused, data-refused and "not supported" failures are **terminal** and spend the claim
  - `hanova.gmail_smtp.friendly_message(exc: BaseException) -> str` — a fixed, identifier-free sentence per failure class
  - `hanova.gmail_smtp.send_mail(to: list[str], subject: str, body: str, cc: list[str] | None = None) -> Dict[str, Any]` returning `{"ok": True, "to": [...], "cc": [...], "subject": str}` — **there is no `bcc` parameter** (finding 17). It raises the original `smtplib` exception class wrapped in `SmtpError` with `__cause__` preserved, so `is_transient` can classify it
  - `tools.email_send.EmailSend` (`Tool.name == "email_send"`)

- [ ] **Step 1: Write the failing test**

Create `reachy_companion/tests/test_hanova_email.py`:

```python
"""Contract tests for email_send (D-018, R2/R3/R5). Nothing is ever sent."""

import types
import importlib

import pytest

from reachy_companion.hanova import gmail_smtp
from reachy_companion.hanova.confirm import GATE
from reachy_companion.tools.email_send import EmailSend


def _deps():
    return types.SimpleNamespace(reachy_mini=None, instance_path=None)


class _FakeSmtp:
    """Records what would have been sent, and sends nothing."""

    def __init__(self) -> None:
        self.logged_in: tuple[str, str] | None = None
        self.messages: list = []

    def __enter__(self) -> "_FakeSmtp":
        return self

    def __exit__(self, *_args) -> bool:
        return False

    def login(self, user: str, password: str) -> None:
        self.logged_in = (user, password)

    def send_message(self, message) -> None:
        self.messages.append(message)


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    """A configured email family and an empty confirmation gate."""
    monkeypatch.setenv("HANOVA_SMTP_USER", "sender@example.com")
    monkeypatch.setenv("HANOVA_SMTP_APP_PASSWORD", "app-password")
    monkeypatch.delenv("HANOVA_SMTP_FROM_NAME", raising=False)
    monkeypatch.delenv("HANOVA_CONFIRM_TTL_S", raising=False)
    GATE.reset()
    GATE.begin_session()
    yield
    GATE.reset()


def test_tool_name_matches_the_filename():
    """The loader resolves tools by filename == Tool.name."""
    assert EmailSend.name == "email_send"


def test_description_carries_no_personal_identifier():
    """R10: upstream put the real sender address in this description."""
    text = EmailSend().description
    assert "@" not in text
    assert len(text) <= 120


def test_send_mail_builds_the_message_and_logs_in(monkeypatch):
    """The account and app password come from config, never from a literal."""
    fake = _FakeSmtp()
    monkeypatch.setattr(gmail_smtp, "smtp_factory", lambda: fake)

    out = gmail_smtp.send_mail(["a@example.com"], "Dinner", "See you at seven.", cc=["b@example.com"])
    assert out["ok"] is True and out["cc"] == ["b@example.com"]
    assert fake.logged_in == ("sender@example.com", "app-password")
    message = fake.messages[0]
    assert message["To"] == "a@example.com"
    assert message["Cc"] == "b@example.com"
    assert message["Subject"] == "Dinner"
    assert "sender@example.com" in message["From"]
    assert message.get_content().strip() == "See you at seven."


def test_send_mail_has_no_bcc_parameter():
    """Finding 17: BCC is an approved non-goal, enforced by the signature."""
    import inspect

    assert "bcc" not in inspect.signature(gmail_smtp.send_mail).parameters


def test_send_mail_never_sets_a_bcc_header(monkeypatch):
    """Belt and braces: no code path may add a blind recipient."""
    fake = _FakeSmtp()
    monkeypatch.setattr(gmail_smtp, "smtp_factory", lambda: fake)
    gmail_smtp.send_mail(["a@example.com"], "Dinner", "hi", cc=["b@example.com"])
    assert fake.messages[0].get("Bcc") is None


def test_send_mail_without_credentials_raises(monkeypatch):
    """A missing app password is a configuration fact, surfaced as SmtpError."""
    monkeypatch.delenv("HANOVA_SMTP_APP_PASSWORD")
    with pytest.raises(gmail_smtp.SmtpError):
        gmail_smtp.send_mail(["a@example.com"], "Dinner", "hi")


def test_send_mail_surfaces_a_transport_failure_without_the_body(monkeypatch, caplog):
    """An SMTP failure becomes SmtpError -- carrying no message content."""
    import logging

    sentinel = "SENTINEL_PRIVATE_x7"

    class _Boom(_FakeSmtp):
        def send_message(self, message) -> None:
            raise OSError(f"connection reset while sending to {sentinel}")

    monkeypatch.setattr(gmail_smtp, "smtp_factory", lambda: _Boom())
    caplog.set_level(logging.DEBUG)
    with pytest.raises(gmail_smtp.SmtpError) as excinfo:
        gmail_smtp.send_mail([f"{sentinel}@example.com"], sentinel, sentinel)
    assert sentinel not in str(excinfo.value)
    assert sentinel not in caplog.text


# --- envelope normalisation and validation (review finding 5) -------------
def test_recipients_are_split_trimmed_and_deduplicated():
    """Voice input arrives as one comma-run with stray whitespace."""
    valid, rejected = gmail_smtp.normalize_recipients(" a@example.com , b@example.com;  A@Example.com ")
    assert valid == ["a@example.com", "b@example.com"]
    assert rejected == []


def test_unparseable_recipients_are_reported_not_dropped():
    """Finding 5: an address we cannot describe is one we must not send to."""
    valid, rejected = gmail_smtp.normalize_recipients("a@example.com, mum, at gmail")
    assert valid == ["a@example.com"]
    assert rejected == ["mum", "at gmail"]


def test_body_digest_is_stable_and_content_free():
    """An integrity token appended after the body -- never instead of it."""
    first = gmail_smtp.body_digest("See you at seven.")
    assert first == gmail_smtp.body_digest("See you at seven.")
    assert first != gmail_smtp.body_digest("See you at eight.")
    assert "See you" not in first


# --- the body is read back in full (round 2, finding 4) -------------------
def test_normalize_body_is_the_single_source_of_what_is_sent():
    """Summary and payload must be byte-identical, so there is one normaliser."""
    normalised = gmail_smtp.normalize_body("  line one  \r\n line two \r\n\r\n")
    assert normalised == "line one\n line two"
    assert gmail_smtp.normalize_body(normalised) == normalised


def test_the_body_length_cap_is_five_hundred_characters():
    """Round 2, finding 4: a body that cannot be read back cannot be confirmed."""
    assert gmail_smtp.MAX_BODY_CHARS == 500


@pytest.mark.asyncio
async def test_a_body_over_the_cap_is_refused_and_arms_nothing(monkeypatch):
    """Refusing is the honest answer; summarising it into a digest was not."""
    import reachy_companion.tools.email_send as email_send_module

    def fail_send(**kwargs):
        raise AssertionError("nothing may be sent")

    monkeypatch.setattr(email_send_module.gmail_smtp, "send_mail", fail_send)
    out = await EmailSend()(
        deps=_deps(),
        to="a@example.com",
        subject="Dinner",
        body="x" * (gmail_smtp.MAX_BODY_CHARS + 1),
    )
    assert out["status"] == "body_too_long"
    assert out["ok"] is False
    assert out["max_chars"] == gmail_smtp.MAX_BODY_CHARS
    assert GATE.claim("email_send") is None


@pytest.mark.asyncio
async def test_a_body_exactly_at_the_cap_is_accepted(monkeypatch):
    """The boundary is inclusive; an off-by-one here is a silent refusal."""
    body = "x" * gmail_smtp.MAX_BODY_CHARS
    out = await EmailSend()(deps=_deps(), to="a@example.com", subject="Dinner", body=body)
    assert out["status"] == "needs_confirmation"
    assert body in out["summary"]


@pytest.mark.asyncio
async def test_the_summary_contains_the_entire_body_not_a_preview(monkeypatch):
    """Round 2, finding 4: every line, not the first one capped at 120 chars."""
    body = "\n".join(f"line {n}: something specific" for n in range(1, 9))
    out = await EmailSend()(deps=_deps(), to="a@example.com", subject="Dinner", body=body)
    assert out["status"] == "needs_confirmation"
    for line in body.splitlines():
        assert line in out["summary"], line


@pytest.mark.asyncio
async def test_changing_text_after_the_first_line_changes_the_summary(monkeypatch):
    """**Round 2, finding 4, mandatory case: the changed tail.**

    This is exactly what the round-1 read-back could not express. Both bodies
    open with the same line, so a first-line preview plus a length plus an
    opaque digest gave the user two confirmations they could not tell apart by
    ear -- while the *sent* mail differed in the part that mattered.
    """
    first = await EmailSend()(
        deps=_deps(),
        to="a@example.com",
        subject="Dinner",
        body="See you at seven.\nBring the map.",
    )
    GATE.reset()
    GATE.begin_session()
    second = await EmailSend()(
        deps=_deps(),
        to="a@example.com",
        subject="Dinner",
        body="See you at seven.\nBring the cash instead.",
    )
    assert first["summary"] != second["summary"]
    assert "Bring the map." in first["summary"]
    assert "Bring the cash instead." in second["summary"]
    assert "Bring the cash instead." not in first["summary"]


@pytest.mark.asyncio
async def test_the_summarised_body_is_the_body_that_is_sent(monkeypatch):
    """One normalisation, so nothing can differ between read-back and send."""
    import reachy_companion.tools.email_send as email_send_module

    sent = {}

    def fake_send(**kwargs):
        sent.update(kwargs)
        return {"ok": True, "to": kwargs["to"], "cc": kwargs["cc"], "subject": kwargs["subject"]}

    monkeypatch.setattr(email_send_module.gmail_smtp, "send_mail", fake_send)
    armed = await EmailSend()(
        deps=_deps(),
        to="a@example.com",
        subject="Dinner",
        body="  See you at seven.  \r\n  Bring the map.  ",
    )
    await EmailSend()(deps=_deps(), confirm=True)
    assert sent["body"] in armed["summary"]
    assert sent["body"] == "See you at seven.\n  Bring the map."


@pytest.mark.asyncio
async def test_email_send_is_unavailable_without_config(monkeypatch):
    """R5: an unconfigured tool answers, it does not raise, and it names the key."""
    monkeypatch.delenv("HANOVA_SMTP_APP_PASSWORD")
    out = await EmailSend()(deps=_deps(), to="a@example.com", subject="Dinner", body="hi")
    assert out == {"status": "unavailable", "reason": "HANOVA_SMTP_APP_PASSWORD"}


@pytest.mark.asyncio
async def test_email_send_reads_back_the_whole_envelope(monkeypatch):
    """Finding 5 + round 2 finding 4: To, CC, subject and the **whole** body."""
    import reachy_companion.tools.email_send as email_send_module

    def fail_send(**kwargs):
        raise AssertionError("email_send must not send before confirmation")

    monkeypatch.setattr(email_send_module.gmail_smtp, "send_mail", fail_send)
    out = await EmailSend()(
        deps=_deps(),
        to="a@example.com",
        cc="b@example.com, c@example.com",
        subject="Dinner",
        body="See you at seven.\nBring the map.",
    )
    assert out["status"] == "needs_confirmation"
    summary = out["summary"]
    for token in (
        "a@example.com",
        "b@example.com",
        "c@example.com",
        "Dinner",
        "See you at seven.",
        "Bring the map.",          # round 2, finding 4: the tail, not just line one
    ):
        assert token in summary, token
    assert "digest" in summary     # still present, now as an appended token
    assert "no blind recipients" in summary.lower()


@pytest.mark.asyncio
async def test_no_recipient_can_hide_outside_the_summary(monkeypatch):
    """Finding 5, stated as the invariant: sent set == summarised set."""
    import reachy_companion.tools.email_send as email_send_module

    sent = {}

    def fake_send(**kwargs):
        sent.update(kwargs)
        return {"ok": True, "to": kwargs["to"], "cc": kwargs["cc"], "subject": kwargs["subject"]}

    monkeypatch.setattr(email_send_module.gmail_smtp, "send_mail", fake_send)
    armed = await EmailSend()(
        deps=_deps(),
        to="a@example.com, d@example.com",
        cc="b@example.com",
        subject="Dinner",
        body="See you at seven.",
    )
    await EmailSend()(deps=_deps(), confirm=True)

    every_recipient = set(sent["to"]) | set(sent["cc"])
    assert every_recipient == {"a@example.com", "d@example.com", "b@example.com"}
    for address in every_recipient:
        assert address in armed["summary"], f"{address} was sent to but never read back"


@pytest.mark.asyncio
async def test_a_cc_duplicating_the_to_is_collapsed(monkeypatch):
    """The read-back count must match what SMTP will actually do."""
    import reachy_companion.tools.email_send as email_send_module

    sent = {}
    monkeypatch.setattr(
        email_send_module.gmail_smtp,
        "send_mail",
        lambda **kwargs: sent.update(kwargs) or {"ok": True, "to": kwargs["to"], "cc": kwargs["cc"], "subject": ""},
    )
    await EmailSend()(deps=_deps(), to="a@example.com", cc="A@example.com", subject="Dinner", body="hi")
    await EmailSend()(deps=_deps(), confirm=True)
    assert sent["to"] == ["a@example.com"]
    assert sent["cc"] == []


@pytest.mark.asyncio
async def test_an_unparseable_recipient_arms_nothing(monkeypatch):
    """Finding 5: refusing is the only safe answer; dropping it silently is not."""
    import reachy_companion.tools.email_send as email_send_module

    def fail_send(**kwargs):
        raise AssertionError("nothing may be sent")

    monkeypatch.setattr(email_send_module.gmail_smtp, "send_mail", fail_send)
    out = await EmailSend()(deps=_deps(), to="a@example.com, mum", subject="Dinner", body="hi")
    assert out["ok"] is False and out["rejected_count"] == 1
    assert GATE.claim("email_send") is None


@pytest.mark.asyncio
async def test_email_send_sends_the_armed_payload(monkeypatch):
    """The confirmed send uses exactly what was read back."""
    import reachy_companion.tools.email_send as email_send_module

    sent = {}

    def fake_send(**kwargs):
        sent.update(kwargs)
        return {"ok": True, "to": kwargs["to"], "cc": kwargs["cc"], "subject": kwargs["subject"]}

    monkeypatch.setattr(email_send_module.gmail_smtp, "send_mail", fake_send)
    await EmailSend()(deps=_deps(), to="a@example.com", subject="Dinner", body="See you at seven.")
    out = await EmailSend()(deps=_deps(), to="wrong@example.com", subject="Wrong", body="wrong", confirm=True)
    assert out["ok"] is True and out["status"] == "sent"
    assert sent["to"] == ["a@example.com"]
    assert sent["subject"] == "Dinner"
    assert sent["body"] == "See you at seven."


@pytest.mark.asyncio
async def test_email_logs_never_carry_an_address_or_a_subject(monkeypatch, caplog):
    """Finding 7: the whole envelope is personal data."""
    import logging

    import reachy_companion.tools.email_send as email_send_module

    sentinel = "SENTINEL_PRIVATE_x7"
    monkeypatch.setattr(
        email_send_module.gmail_smtp,
        "send_mail",
        lambda **kwargs: {"ok": True, "to": kwargs["to"], "cc": kwargs["cc"], "subject": ""},
    )
    caplog.set_level(logging.DEBUG)
    await EmailSend()(deps=_deps(), to=f"{sentinel}@example.com", subject=sentinel, body=sentinel)
    await EmailSend()(deps=_deps(), confirm=True)
    assert sentinel not in caplog.text


@pytest.mark.asyncio
async def test_email_send_confirm_without_arm_is_refused(monkeypatch):
    """A confirm:true first call must send nothing."""
    import reachy_companion.tools.email_send as email_send_module

    def fail_send(**kwargs):
        raise AssertionError("email_send must not send without a pending action")

    monkeypatch.setattr(email_send_module.gmail_smtp, "send_mail", fail_send)
    out = await EmailSend()(deps=_deps(), to="a@example.com", subject="Dinner", body="hi", confirm=True)
    assert out["status"] == "confirmation_expired"


@pytest.mark.asyncio
async def test_email_send_rejects_a_name_only_recipient():
    """"Send it to mum" has no address; refuse rather than guess one."""
    out = await EmailSend()(deps=_deps(), to="mum", subject="Dinner", body="hi")
    assert out["ok"] is False and "@" in out["error"]


@pytest.mark.asyncio
async def test_email_send_requires_subject_and_body():
    """An empty subject or body is almost always a mis-parse."""
    assert (await EmailSend()(deps=_deps(), to="a@example.com", subject="", body="hi"))["ok"] is False
    assert (await EmailSend()(deps=_deps(), to="a@example.com", subject="Dinner", body=" "))["ok"] is False


@pytest.mark.asyncio
async def test_email_send_reports_a_transport_failure_and_keeps_the_authorisation(monkeypatch):
    """Finding 4: a *transient* failed send must not force a second read-back."""
    import smtplib

    import reachy_companion.tools.email_send as email_send_module

    attempts = {"n": 0}

    def flaky(**kwargs):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise email_send_module.SmtpError("the mail could not be sent") from smtplib.SMTPServerDisconnected(
                "connection lost"
            )
        return {"ok": True, "to": kwargs["to"], "cc": kwargs["cc"], "subject": kwargs["subject"]}

    monkeypatch.setattr(email_send_module.gmail_smtp, "send_mail", flaky)
    await EmailSend()(deps=_deps(), to="a@example.com", subject="Dinner", body="hi")
    first = await EmailSend()(deps=_deps(), confirm=True)
    assert first["ok"] is False and first.get("retryable") is True
    second = await EmailSend()(deps=_deps(), confirm=True)
    assert second["ok"] is True and second["status"] == "sent"


# --- transient vs terminal (round 2, finding 9) ---------------------------
def test_smtp_failures_are_classified_not_all_transient():
    """Round 2, finding 9: an auth failure is not a "try again" situation."""
    import smtplib

    for terminal in (
        smtplib.SMTPAuthenticationError(535, b"bad credentials"),
        smtplib.SMTPRecipientsRefused({"a@example.com": (550, b"no such user")}),
        smtplib.SMTPSenderRefused(553, b"not allowed", "sender@example.com"),
        smtplib.SMTPDataError(554, b"message refused"),
        smtplib.SMTPNotSupportedError("no SSL"),
    ):
        assert gmail_smtp.is_transient(terminal) is False, type(terminal).__name__

    for transient in (
        smtplib.SMTPConnectError(421, b"try later"),
        smtplib.SMTPServerDisconnected("connection lost"),
        smtplib.SMTPHeloError(451, b"greeting failed"),
        TimeoutError("timed out"),
        OSError("network unreachable"),
    ):
        assert gmail_smtp.is_transient(transient) is True, type(transient).__name__


def test_the_classification_follows_the_wrapped_cause():
    """`send_mail` raises SmtpError; the class that decides is underneath it."""
    import smtplib

    wrapped = gmail_smtp.SmtpError("the mail could not be sent")
    wrapped.__cause__ = smtplib.SMTPAuthenticationError(535, b"bad credentials")
    assert gmail_smtp.is_transient(wrapped) is False

    retryable = gmail_smtp.SmtpError("the mail could not be sent")
    retryable.__cause__ = smtplib.SMTPServerDisconnected("connection lost")
    assert gmail_smtp.is_transient(retryable) is True


@pytest.mark.asyncio
async def test_a_terminal_failure_spends_the_authorisation(monkeypatch):
    """Round 2, finding 9: a refused recipient means the envelope is wrong.

    Keeping the approval alive would keep an approval for something that can
    never succeed as approved. The user has to hear a corrected envelope.
    """
    import smtplib

    import reachy_companion.tools.email_send as email_send_module

    def refused(**kwargs):
        raise email_send_module.SmtpError("the mail could not be sent") from smtplib.SMTPRecipientsRefused(
            {"a@example.com": (550, b"no such user")}
        )

    monkeypatch.setattr(email_send_module.gmail_smtp, "send_mail", refused)
    await EmailSend()(deps=_deps(), to="a@example.com", subject="Dinner", body="hi")
    out = await EmailSend()(deps=_deps(), confirm=True)
    assert out["ok"] is False
    assert out.get("retryable") is not True
    # Spent: a bare retry now finds nothing armed.
    again = await EmailSend()(deps=_deps(), confirm=True)
    assert again["status"] == "confirmation_expired"


@pytest.mark.asyncio
async def test_a_stale_confirm_cannot_spend_a_newly_armed_envelope(monkeypatch):
    """Round 2, finding 2, at the tool level: claim ids, not just tool names."""
    import reachy_companion.tools.email_send as email_send_module

    sent = []

    def record(**kwargs):
        sent.append(kwargs)
        return {"ok": True, "to": kwargs["to"], "cc": kwargs["cc"], "subject": kwargs["subject"]}

    monkeypatch.setattr(email_send_module.gmail_smtp, "send_mail", record)
    await EmailSend()(deps=_deps(), to="a@example.com", subject="First", body="one")
    stale = GATE.claim("email_send")
    assert stale is not None

    GATE.begin_session()
    await EmailSend()(deps=_deps(), to="b@example.com", subject="Second", body="two")

    # The old in-flight operation finishing must not touch the new approval.
    assert GATE.complete("email_send", stale.claim_id) is False
    out = await EmailSend()(deps=_deps(), confirm=True)
    assert out["ok"] is True
    assert sent[-1]["to"] == ["b@example.com"]


def test_email_send_reaches_the_model_session():
    """The locked profile must list it, or the model never sees it."""
    core_tools = importlib.import_module("reachy_companion.tools.core_tools")
    core_tools.initialize_tools(force=True)
    try:
        assert "email_send" in {spec["name"] for spec in core_tools.get_tool_specs()}
    finally:
        core_tools._TOOLS_SIGNATURE = None
```

- [ ] **Step 2: Run the test to verify it fails**

```powershell
cd C:\Project\Reachy-mini\reachy_companion
..\.venv\Scripts\python.exe -m pytest tests/test_hanova_email.py -q
```

Expected: collection error — `ModuleNotFoundError: No module named 'reachy_companion.hanova.gmail_smtp'`.

- [ ] **Step 3: Implement `hanova/gmail_smtp.py`**

Create `reachy_companion/src/reachy_companion/hanova/gmail_smtp.py`:

```python
"""Outbound mail over SMTP, adapted from upstream `gmail_send.py` (D-018).

SMTP rather than the Gmail API because the OAuth grant this project has does not
carry the `gmail.send` scope (`bin/google/gmail_send.py:6-9`). The account and
its app password come from `HANOVA_SMTP_USER` / `HANOVA_SMTP_APP_PASSWORD`;
upstream read them out of a secrets JSON on the operator's Mac.

`smtp_factory` exists so the whole module can be tested without a socket.

**No BCC (review finding 17).** The parameter is gone, not ignored: a
blind-carbon recipient is one the confirmation read-back cannot surface, and
that is exactly the hole finding 5 closes.
"""

from __future__ import annotations

import re
import hashlib
import logging
import smtplib
from typing import Any, Dict, List
from email.message import EmailMessage

from reachy_companion.hanova import redact, settings


logger = logging.getLogger(__name__)

_TIMEOUT_S = 60
# Deliberately permissive but not empty: this is a sanity check against the STT
# channel handing us "mum" or "at gmail", not an RFC 5322 parser.
_ADDRESS = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")

# Round 2, finding 4: the longest body that can be honestly read back to a person
# who is listening. Roughly 30 seconds of speech. A body longer than this is
# refused rather than summarised, because a summary is not a confirmation.
MAX_BODY_CHARS = 500

# Round 2, finding 9. A failure is transient only when retrying the *same*
# envelope could plausibly succeed. Everything else means the resolved action is
# wrong, so the user's approval no longer describes anything achievable.
_TERMINAL_SMTP = (
    smtplib.SMTPAuthenticationError,
    smtplib.SMTPRecipientsRefused,
    smtplib.SMTPSenderRefused,
    smtplib.SMTPDataError,
    smtplib.SMTPNotSupportedError,
)
_TRANSIENT_SMTP = (
    smtplib.SMTPConnectError,
    smtplib.SMTPServerDisconnected,
    smtplib.SMTPHeloError,
)


class SmtpError(RuntimeError):
    """Mail that could not be built or could not be sent.

    Always raised `from` the underlying `smtplib` exception, so `is_transient`
    can classify it without the message text (round 2, finding 9).
    """


def is_transient(exc: BaseException) -> bool:
    """Return whether a gated retry on the same confirmation makes sense."""
    candidate: BaseException | None = exc
    if isinstance(exc, SmtpError) and exc.__cause__ is not None:
        candidate = exc.__cause__
    if isinstance(candidate, _TERMINAL_SMTP):
        return False
    if isinstance(candidate, _TRANSIENT_SMTP):
        return True
    # A bare socket problem is transient; anything else we cannot classify is
    # treated as terminal, because spending an authorisation is the safe error.
    return isinstance(candidate, (TimeoutError, OSError))


_TERMINAL_MESSAGES = {
    smtplib.SMTPAuthenticationError: "the mail account rejected the robot's credentials",
    smtplib.SMTPRecipientsRefused: "the mail server refused one of the recipients",
    smtplib.SMTPSenderRefused: "the mail server refused the sending address",
    smtplib.SMTPDataError: "the mail server refused the message itself",
    smtplib.SMTPNotSupportedError: "the mail server does not support this kind of connection",
}


def friendly_message(exc: BaseException) -> str:
    """A fixed, identifier-free reason the model may say out loud (finding 7)."""
    candidate: BaseException = exc
    if isinstance(exc, SmtpError) and exc.__cause__ is not None:
        candidate = exc.__cause__
    for family, message in _TERMINAL_MESSAGES.items():
        if isinstance(candidate, family):
            return message
    return "the mail could not be sent right now"


def smtp_factory() -> Any:
    """Return a context-manager SMTP connection. The single test seam."""
    return smtplib.SMTP_SSL(settings.smtp_host(), settings.smtp_port(), timeout=_TIMEOUT_S)


def normalize_recipients(raw: str | None) -> tuple[List[str], List[str]]:
    """Split, trim, validate and de-duplicate a recipient field (finding 5).

    Returns `(valid, rejected)`, both order-preserving. The caller must refuse
    to arm anything while *rejected* is non-empty: a recipient we could not
    parse is a recipient the read-back could not describe.
    """
    valid: List[str] = []
    rejected: List[str] = []
    seen: set[str] = set()
    for item in re.split(r"[,;]", raw or ""):
        candidate = item.strip().strip("<>")
        if not candidate:
            continue
        if not _ADDRESS.match(candidate):
            rejected.append(candidate)
            continue
        key = candidate.lower()
        if key in seen:
            continue
        seen.add(key)
        valid.append(candidate)
    return valid, rejected


def normalize_body(raw: str) -> str:
    """The one body normalisation (round 2, finding 4).

    Whatever the read-back quotes must be byte-identical to what is sent, so
    there is exactly one function that decides what the body *is*: line endings
    collapsed, trailing whitespace stripped per line, the whole thing stripped.
    Idempotent by construction.
    """
    text = str(raw or "").replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in text.split("\n")).strip()


def body_digest(body: str) -> str:
    """An integrity token appended **after** the full body in the read-back.

    Round 2, finding 4 demoted this. It was the only description the summary
    carried of everything past the first line, and a human being cannot verify
    "digest 4f2a9c31" against what they meant to say. The body itself is now in
    the summary; this is a checksum beside it, not a substitute for it.
    """
    digest = hashlib.blake2s(body.encode("utf-8"), digest_size=4).hexdigest()
    return f"{len(body)} characters, digest {digest}"


def send_mail(
    to: List[str],
    subject: str,
    body: str,
    cc: List[str] | None = None,
) -> Dict[str, Any]:
    """Send one plain-text message to an already-validated envelope.

    *to* and *cc* are lists, not comma strings: by the time this is called the
    envelope has been normalised, validated and read back to the user, and no
    further parsing may reinterpret it (finding 5).
    """
    user = settings.smtp_user()
    password = settings.smtp_app_password()
    if not user or not password:
        raise SmtpError("HANOVA_SMTP_USER and HANOVA_SMTP_APP_PASSWORD must both be set.")
    if not to:
        raise SmtpError("no recipient")

    message = EmailMessage()
    message["From"] = f"{settings.smtp_from_name()} <{user}>"
    message["To"] = ", ".join(to)
    if cc:
        message["Cc"] = ", ".join(cc)
    message["Subject"] = subject
    message.set_content(body)

    # Finding 7: counts and lengths only -- never an address, subject or body.
    logger.info(
        "Sending mail: to=%d cc=%d subject=%s body=%s",
        len(to),
        len(cc or []),
        redact.text(subject),
        redact.text(body),
    )
    try:
        with smtp_factory() as server:
            server.login(user, password)
            server.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        logger.warning("SMTP send failed: %s", redact.error(exc))
        # `from exc` is load-bearing: `is_transient` classifies on the cause,
        # not on this message (round 2, finding 9).
        raise SmtpError("the mail could not be sent") from exc
    return {"ok": True, "to": list(to), "cc": list(cc or []), "subject": subject}
```

**Note (round 2, finding 4): `preview()` is gone.** It returned the first line of
the body capped at 120 characters, and that string was the *entire* description
of the message content in the confirmation summary. Nothing may reintroduce it:
the summary carries the whole normalised body, or the body is refused for being
over `MAX_BODY_CHARS`. Task 14's shape test asserts `preview(` appears nowhere in
`tools/email_send.py`.

- [ ] **Step 4: Implement `tools/email_send.py`**

Create `reachy_companion/src/reachy_companion/tools/email_send.py`:

```python
"""Send an email, behind a confirmation gate (D-018, R2/R3). Filename == Tool.name.

This is the most irreversible tool in the app: real mail, to a third party, from
the operator's own account, on a fuzzy voice intent. So the first call sends
nothing and reads the **whole envelope** back; only a second call with
`confirm: true` sends, and it sends exactly the message that was read back.

Review round 1, finding 5: the read-back covers **every** recipient (To and CC),
the subject, and the body. No recipient can reach `send_mail` that the summary
did not name, because the summary is derived from the same normalised envelope
that is parked.

Review round 2, finding 4: the read-back carries the **entire** body, verbatim.
The previous version quoted only the first line, capped at 120 characters, plus
a length and a hex digest -- and a person cannot verify text they were never
told. Two bodies sharing an opening line produced confirmations no listener
could tell apart. A body longer than `gmail_smtp.MAX_BODY_CHARS` (500) is now
**refused** rather than summarised, because a body that cannot be read back in
full cannot be confirmed at all.

Review round 1, finding 17: **there is no BCC.** A blind recipient is one the
read-back cannot surface.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict

from reachy_companion.hanova import redact, settings, gmail_smtp
from reachy_companion.hanova.confirm import GATE, confirmation_expired
from reachy_companion.hanova.gmail_smtp import SmtpError, is_transient, friendly_message
from reachy_companion.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)

_MAX_RECIPIENTS = 10


class EmailSend(Tool):
    """Send a plain-text email after the user confirms the whole envelope."""

    name = "email_send"
    description = "Send an email. 需要先確認：先讀回收件人、副本、主旨再寄出。"
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "to": {
                "type": "string",
                "description": "Recipient address(es), comma separated. A name alone is not enough.",
                "minLength": 5,
            },
            "subject": {"type": "string", "description": "Subject line."},
            "body": {"type": "string", "description": "Plain-text message body."},
            "cc": {"type": "string", "description": "Optional comma-separated cc addresses."},
            "confirm": {
                "type": "boolean",
                "description": "Set true only after the user has confirmed the full envelope read back to them.",
            },
        },
        # Finding 4: optional in the schema, mandatory in the non-confirm branch.
        # The confirming call carries only `confirm`, so the frozen envelope
        # cannot be mis-heard a second time.
        "required": [],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Read the whole envelope back, or send a previously confirmed one."""
        available, reason = settings.tool_status(self.name)
        if not available:
            return settings.unavailable(reason)

        if bool(kwargs.get("confirm")):
            pending = GATE.claim(self.name)
            if pending is None:
                return confirmation_expired()
            logger.info(
                "Tool call: email_send confirmed to=%d cc=%d",
                len(pending.payload["to"]),
                len(pending.payload["cc"]),
            )
            try:
                await asyncio.to_thread(
                    gmail_smtp.send_mail,
                    to=list(pending.payload["to"]),
                    subject=str(pending.payload["subject"]),
                    body=str(pending.payload["body"]),
                    cc=list(pending.payload["cc"]),
                )
            except (SmtpError, OSError, ValueError, KeyError) as exc:
                logger.warning("email_send failed: %s", redact.error(exc))
                if is_transient(exc):
                    # A transport failure is retryable on the same
                    # authorisation; the user already approved this exact
                    # envelope (finding 4). The claim id says *which* one
                    # (round 2, finding 2).
                    GATE.release(self.name, pending.claim_id)
                    return {"ok": False, "error": friendly_message(exc), "retryable": True}
                # Round 2, finding 9: authentication, a refused recipient, a
                # refused sender or a refused message are all terminal -- the
                # approved envelope cannot succeed as approved, so the
                # authorisation is spent and a corrected one must be read back.
                GATE.complete(self.name, pending.claim_id)
                return {"ok": False, "error": friendly_message(exc)}
            GATE.complete(self.name, pending.claim_id)
            return {"ok": True, "status": "sent", "summary": pending.summary}

        subject = str(kwargs.get("subject", "")).strip()
        body = gmail_smtp.normalize_body(str(kwargs.get("body", "")))
        to_valid, to_rejected = gmail_smtp.normalize_recipients(str(kwargs.get("to") or ""))
        cc_valid, cc_rejected = gmail_smtp.normalize_recipients(str(kwargs.get("cc") or ""))

        if to_rejected or cc_rejected:
            # Finding 5: never silently drop what we could not parse -- an
            # address we cannot describe is an address we must not send to.
            return {
                "ok": False,
                "error": (
                    "one of the addresses is not a full email address; ask the user "
                    "to spell it out, then try again"
                ),
                "rejected_count": len(to_rejected) + len(cc_rejected),
            }
        if not to_valid:
            return {"ok": False, "error": "to must be a full email address containing @; ask the user for it"}
        # De-duplicate across the two fields so the read-back count is honest.
        cc_valid = [address for address in cc_valid if address.lower() not in {a.lower() for a in to_valid}]
        if len(to_valid) + len(cc_valid) > _MAX_RECIPIENTS:
            return {"ok": False, "error": f"that is more than {_MAX_RECIPIENTS} recipients; narrow it down"}
        if not subject:
            return {"ok": False, "error": "subject is required"}
        if not body:
            return {"ok": False, "error": "body is required"}
        if len(body) > gmail_smtp.MAX_BODY_CHARS:
            # Round 2, finding 4: refusing is the honest answer. A body too long
            # to read back is a body the user cannot confirm, and summarising it
            # into a digest is what made two different messages sound identical.
            return {
                "ok": False,
                "status": "body_too_long",
                "max_chars": gmail_smtp.MAX_BODY_CHARS,
                "error": (
                    f"that message is {len(body)} characters, longer than the "
                    f"{gmail_smtp.MAX_BODY_CHARS} the robot can read back before sending. "
                    "Ask the user for a shorter message."
                ),
            }

        # Finding 5 + round 2 finding 4: the summary names EVERY recipient, the
        # subject, and the **entire** body, followed by a length-and-digest token
        # as an integrity check beside the text -- never instead of it. It is
        # built from the same normalised envelope that is parked, so nothing can
        # be present in one and absent from the other.
        cc_clause = f", copying {', '.join(cc_valid)}" if cc_valid else ", with nobody copied"
        summary = (
            f"send an email to {', '.join(to_valid)}{cc_clause}, "
            f"subject {subject!r}. "
            f"The message says, in full:\n{body}\n"
            f"(end of message; {gmail_smtp.body_digest(body)}). "
            "There are no blind recipients."
        )
        return GATE.arm(
            self.name,
            summary,
            {"to": to_valid, "cc": cc_valid, "subject": subject, "body": body},
        )
```

- [ ] **Step 5: Enable the tool in the locked profile**

In `reachy_companion/profiles/_reachy_companion_locked_profile/profile.md`, add to `default_tools` immediately after `"drive_upload",`:

```toml
  "drive_upload",
  "email_send",
```

- [ ] **Step 6: Run the test to verify it passes**

```powershell
cd C:\Project\Reachy-mini\reachy_companion
..\.venv\Scripts\python.exe -m pytest tests/test_hanova_email.py -q
```

Expected: green — **33** test functions, none parametrised: **33 collected
cases**, including the eight round 2 finding 4 body-read-back cases and the
three finding 9 classification cases. Record the exact number.

- [ ] **Step 7: Run the full gate**

```powershell
cd C:\Project\Reachy-mini\reachy_companion
..\.venv\Scripts\python.exe -m pytest -q
..\.venv\Scripts\python.exe -m ruff check src tests
..\.venv\Scripts\python.exe -m ruff format --check src tests
..\.venv\Scripts\python.exe -m mypy
```

Expected: pytest green (30 documented skips), ruff clean, mypy `Success: no issues found`.

- [ ] **Step 8: Commit**

```bash
cd /c/Project/Reachy-mini
git rev-parse --abbrev-ref HEAD   # must print feat/ha-nova-port (Task 0)
git add reachy_companion/src/reachy_companion/hanova/gmail_smtp.py \
        reachy_companion/src/reachy_companion/tools/email_send.py \
        reachy_companion/profiles/_reachy_companion_locked_profile/profile.md \
        reachy_companion/tests/test_hanova_email.py
git commit -m "feat(hanova): email_send with recipient/subject read-back gating"
```

---

### Task 12: The two audio gags — `self_destruct` and `mad_laugh`

Implements R2's last two tools. Upstream played both through Home Assistant onto a Voice-PE puck via a URL under HA's web root (`server.py:2200-2208`, `:2254-2262`). On a robot with its own speaker all of that disappears: the clip is cached once by id and played locally, so the gags work with no Home Assistant, no LAN URL, and no home network.

Both go through the same `MusicPlayer` as music, which is deliberate: it means `stop_music` stops a gag mid-clip, and a gag ducks when the user starts talking (R7), with no extra code.

`self_destruct` keeps upstream's two-step ritual, implemented on the same
`ConfirmationGate` every other gated tool uses — one contract, one TTL, one place
to audit.

**Review round 1, finding 17 (controller ruling) — the confirmation stays in
character.** The first draft's summary was
`"start the self-destruct sequence (a joke: it plays a loud sound and nothing
else)"`, which is the generic gate summary bolted onto a gag and which tells the
user the punchline before it lands. Upstream's contract was an in-character
arm/confirm ritual, and that is what is ported: the arm reads back **as the
robot's own countdown ritual**, the confirm word is thematic, and an explicit
**abort word** cancels it. Nothing destructive is at stake — it plays a sound —
so the read-back's job here is theatre plus a real TTL, not disclosure. The
generic gate summary is not used for this tool, and the persona is told not to
explain the joke before running it. The 90 s TTL and the abort path are still
enforced in code by the same `ConfirmationGate`.

**Files:**
- Create: `reachy_companion/src/reachy_companion/hanova/sfx.py`
- Create: `reachy_companion/src/reachy_companion/tools/self_destruct.py`
- Create: `reachy_companion/src/reachy_companion/tools/mad_laugh.py`
- Modify: `reachy_companion/profiles/_reachy_companion_locked_profile/profile.md` (`default_tools` gains both names)
- Test: `reachy_companion/tests/test_hanova_gags.py`

**Interfaces:**
- Consumes: `hanova.ytdlp.download_audio`; `hanova.media_store.media_dir`; `hanova.music_player.PLAYER`; `hanova.settings.{self_destruct_yt_id, mad_laugh_yt_id, tool_status, unavailable}`; `hanova.confirm.{GATE, confirmation_expired}` — `claim`/`complete`/`abort`.
- Produces:
  - `hanova.sfx.ensure_clip(video_id: str, instance_path: str | Path | None) -> Awaitable[Dict[str, Any]]` returning `{"ok": bool, "path": str | None, "error": str | None}`
  - `hanova.sfx.play_clip(deps: Any, video_id: str, title: str, instance_path: str | Path | None) -> Awaitable[Dict[str, Any]]`
  - `tools.self_destruct.SelfDestruct` (`Tool.name == "self_destruct"`), `tools.mad_laugh.MadLaugh` (`Tool.name == "mad_laugh"`)

- [ ] **Step 1: Write the failing test**

Create `reachy_companion/tests/test_hanova_gags.py`:

```python
"""Contract tests for the two audio gags (D-018, R2/R3/R5/R7)."""

import types
import importlib

import pytest

from reachy_companion.hanova import sfx
from reachy_companion.hanova.confirm import GATE
from reachy_companion.hanova.music_player import PLAYER
from reachy_companion.tools.mad_laugh import MadLaugh
from reachy_companion.tools.self_destruct import SelfDestruct


def _deps(tmp_path):
    played: list[str] = []
    media = types.SimpleNamespace(play_sound=played.append)
    robot = types.SimpleNamespace(media=media, _daemon_http_url="http://127.0.0.1:8000")
    return types.SimpleNamespace(reachy_mini=robot, instance_path=tmp_path), played


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    """Both gag ids present, both wheels present, nothing playing."""
    monkeypatch.setenv("HANOVA_SELF_DESTRUCT_YT_ID", "sd-clip-id")
    monkeypatch.setenv("HANOVA_MAD_LAUGH_YT_ID", "ml-clip-id")
    monkeypatch.delenv("HANOVA_CONFIRM_TTL_S", raising=False)
    monkeypatch.setattr("reachy_companion.hanova.settings._music_wheels_ready", lambda: (True, ""))

    import httpx

    class _Ok:
        status_code = 200

    async def ok_post(self, *args, **kwargs):
        return _Ok()

    monkeypatch.setattr(httpx.AsyncClient, "post", ok_post)
    GATE.reset()
    GATE.begin_session()
    PLAYER.reset()
    yield
    GATE.reset()
    PLAYER.reset()


def test_tool_names_match_their_filenames():
    """The loader resolves tools by filename == Tool.name."""
    assert SelfDestruct.name == "self_destruct"
    assert MadLaugh.name == "mad_laugh"


def test_descriptions_carry_no_personal_identifier():
    """R10: descriptions stay short, generic and identifier-free."""
    for text in (SelfDestruct().description, MadLaugh().description):
        assert "@" not in text
        assert "media_player." not in text
        assert len(text) <= 120


@pytest.mark.asyncio
async def test_ensure_clip_caches_by_video_id(monkeypatch, tmp_path):
    """A gag downloads once; every later play is instant and offline."""
    calls = {"n": 0}

    def fake_download(video_id, dest_dir):
        calls["n"] += 1
        path = dest_dir / f"{video_id}.mp3"
        path.write_bytes(b"ID3")
        return {"ok": True, "path": str(path), "cached": False, "error": None}

    monkeypatch.setattr(sfx.ytdlp, "download_audio", fake_download)
    first = await sfx.ensure_clip("sd-clip-id", tmp_path)
    assert first["ok"] is True
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_ensure_clip_reports_a_download_failure(monkeypatch, tmp_path):
    """No network at gag time is a spoken answer, not a crash."""
    monkeypatch.setattr(
        sfx.ytdlp,
        "download_audio",
        lambda video_id, dest_dir: {"ok": False, "path": None, "cached": False, "error": "no network"},
    )
    out = await sfx.ensure_clip("sd-clip-id", tmp_path)
    assert out["ok"] is False and "no network" in out["error"]


@pytest.mark.asyncio
async def test_mad_laugh_plays_on_the_robot_speaker(monkeypatch, tmp_path):
    """No Home Assistant, no LAN URL: it is the robot's own speaker."""
    import reachy_companion.tools.mad_laugh as mad_laugh_module

    def fake_download(video_id, dest_dir):
        path = dest_dir / f"{video_id}.mp3"
        path.write_bytes(b"ID3")
        return {"ok": True, "path": str(path), "cached": True, "error": None}

    monkeypatch.setattr(mad_laugh_module.sfx.ytdlp, "download_audio", fake_download)
    deps, played = _deps(tmp_path)
    out = await MadLaugh()(deps=deps)
    assert out["ok"] is True and out["status"] == "playing"
    assert played and played[0].endswith("ml-clip-id.mp3")


@pytest.mark.asyncio
async def test_mad_laugh_is_unavailable_without_a_clip_id(monkeypatch, tmp_path):
    """Finding 10: the clip id is this tool's own prerequisite, and it is named."""
    monkeypatch.delenv("HANOVA_MAD_LAUGH_YT_ID")
    deps, _ = _deps(tmp_path)
    out = await MadLaugh()(deps=deps)
    assert out == {"status": "unavailable", "reason": "HANOVA_MAD_LAUGH_YT_ID"}


@pytest.mark.asyncio
async def test_mad_laugh_is_unavailable_without_the_wheels(monkeypatch, tmp_path):
    """R5: no yt-dlp / ffmpeg means the tool is off, not broken."""
    monkeypatch.setattr(
        "reachy_companion.hanova.settings._music_wheels_ready", lambda: (False, "yt-dlp not installed")
    )
    deps, _ = _deps(tmp_path)
    out = await MadLaugh()(deps=deps)
    assert out == {"status": "unavailable", "reason": "MUSIC_WHEELS"}


@pytest.mark.asyncio
async def test_self_destruct_arms_before_it_plays(monkeypatch, tmp_path):
    """R3: the first call plays nothing and reads the ritual back."""
    import reachy_companion.tools.self_destruct as self_destruct_module

    def fail_download(video_id, dest_dir):
        raise AssertionError("self_destruct must not fetch or play before confirmation")

    monkeypatch.setattr(self_destruct_module.sfx.ytdlp, "download_audio", fail_download)
    deps, played = _deps(tmp_path)
    out = await SelfDestruct()(deps=deps)
    assert out["status"] == "needs_confirmation" and out["summary"]
    assert played == []


def test_the_arm_summary_stays_in_character(monkeypatch):
    """Finding 17: the confirmation must not spoil the gag it is confirming."""
    import reachy_companion.tools.self_destruct as self_destruct_module

    summary = self_destruct_module._ARM_SUMMARY.lower()
    for spoiler in ("joke", "gag", "prank", "nothing else", "just a sound", "pretend"):
        assert spoiler not in summary, f"the arm summary gives away the gag: {spoiler!r}"
    # ...but it must still be a real two-step with a real way out.
    assert "abort" in summary
    assert "authorise" in summary or "authorize" in summary


@pytest.mark.asyncio
async def test_self_destruct_can_be_aborted(monkeypatch, tmp_path):
    """Finding 17: an explicit abort word, enforced in code."""
    import reachy_companion.tools.self_destruct as self_destruct_module

    def fail_download(video_id, dest_dir):
        raise AssertionError("an aborted sequence must never play")

    monkeypatch.setattr(self_destruct_module.sfx.ytdlp, "download_audio", fail_download)
    deps, played = _deps(tmp_path)
    await SelfDestruct()(deps=deps)
    out = await SelfDestruct()(deps=deps, abort=True)
    assert out == {"status": "aborted"}
    assert played == []
    assert (await SelfDestruct()(deps=deps, confirm=True))["status"] == "confirmation_expired"


@pytest.mark.asyncio
async def test_aborting_when_nothing_is_armed_is_harmless(tmp_path):
    """Standing down a sequence that was never armed is still in character."""
    deps, _ = _deps(tmp_path)
    assert (await SelfDestruct()(deps=deps, abort=True)) == {"status": "aborted"}


@pytest.mark.asyncio
async def test_the_armed_sequence_expires(monkeypatch, tmp_path):
    """Finding 17: the TTL is real, and it is the shared gate's TTL."""
    import reachy_companion.tools.self_destruct as self_destruct_module

    def fail_download(video_id, dest_dir):
        raise AssertionError("an expired sequence must never play")

    monkeypatch.setattr(self_destruct_module.sfx.ytdlp, "download_audio", fail_download)
    deps, _ = _deps(tmp_path)
    await SelfDestruct()(deps=deps)
    GATE.expire_now_for_tests("self_destruct")
    out = await SelfDestruct()(deps=deps, confirm=True)
    assert out["status"] == "confirmation_expired"


@pytest.mark.asyncio
async def test_self_destruct_plays_once_confirmed(monkeypatch, tmp_path):
    """The confirmed call is the only one that makes noise."""
    import reachy_companion.tools.self_destruct as self_destruct_module

    def fake_download(video_id, dest_dir):
        path = dest_dir / f"{video_id}.mp3"
        path.write_bytes(b"ID3")
        return {"ok": True, "path": str(path), "cached": True, "error": None}

    monkeypatch.setattr(self_destruct_module.sfx.ytdlp, "download_audio", fake_download)
    deps, played = _deps(tmp_path)
    await SelfDestruct()(deps=deps)
    out = await SelfDestruct()(deps=deps, confirm=True)
    assert out["ok"] is True and out["status"] == "playing"
    assert played and played[0].endswith("sd-clip-id.mp3")


@pytest.mark.asyncio
async def test_self_destruct_confirm_without_arm_is_refused(monkeypatch, tmp_path):
    """A confirm:true first call must play nothing."""
    import reachy_companion.tools.self_destruct as self_destruct_module

    def fail_download(video_id, dest_dir):
        raise AssertionError("self_destruct must not play without a pending action")

    monkeypatch.setattr(self_destruct_module.sfx.ytdlp, "download_audio", fail_download)
    deps, _ = _deps(tmp_path)
    out = await SelfDestruct()(deps=deps, confirm=True)
    assert out["status"] == "confirmation_expired"


@pytest.mark.asyncio
async def test_a_playing_gag_is_stoppable_by_voice(monkeypatch, tmp_path):
    """R7: gags route through the music player, so stop_music stops them."""
    import reachy_companion.tools.mad_laugh as mad_laugh_module
    from reachy_companion.tools.stop_music import StopMusic

    def fake_download(video_id, dest_dir):
        path = dest_dir / f"{video_id}.mp3"
        path.write_bytes(b"ID3")
        return {"ok": True, "path": str(path), "cached": True, "error": None}

    monkeypatch.setattr(mad_laugh_module.sfx.ytdlp, "download_audio", fake_download)
    deps, _ = _deps(tmp_path)
    await MadLaugh()(deps=deps)
    assert PLAYER.current() is not None
    out = await StopMusic()(deps=deps)
    assert out["status"] == "stopped"
    assert PLAYER.current() is None


def test_both_gags_reach_the_model_session():
    """The locked profile must list them, or the model never sees them."""
    core_tools = importlib.import_module("reachy_companion.tools.core_tools")
    core_tools.initialize_tools(force=True)
    try:
        names = {spec["name"] for spec in core_tools.get_tool_specs()}
        assert {"self_destruct", "mad_laugh"} <= names
    finally:
        core_tools._TOOLS_SIGNATURE = None
```

- [ ] **Step 2: Run the test to verify it fails**

```powershell
cd C:\Project\Reachy-mini\reachy_companion
..\.venv\Scripts\python.exe -m pytest tests/test_hanova_gags.py -q
```

Expected: collection error — `ModuleNotFoundError: No module named 'reachy_companion.hanova.sfx'`.

- [ ] **Step 3: Implement `hanova/sfx.py`**

Create `reachy_companion/src/reachy_companion/hanova/sfx.py`:

```python
"""Cached sound-effect clips for the two gags (D-018, R2).

Upstream pushed these onto a Voice-PE puck through Home Assistant, using a URL
under HA's own web root. The robot has a speaker, so the clip is simply cached
once by video id in the `sfx` media directory and played locally -- no Home
Assistant, no LAN URL, and no home network required.

Playback goes through the shared `MusicPlayer`, so `stop_music` stops a gag and
user speech ducks it, with no code specific to gags (R7).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict
from pathlib import Path

from reachy_companion.hanova import ytdlp, media_store
from reachy_companion.hanova.music_player import PLAYER


logger = logging.getLogger(__name__)


async def ensure_clip(video_id: str, instance_path: str | Path | None) -> Dict[str, Any]:
    """Return the cached clip for *video_id*, downloading it once if needed."""
    sfx_dir = media_store.media_dir("sfx", instance_path)
    result = await asyncio.to_thread(ytdlp.download_audio, video_id, sfx_dir)
    if not result["ok"]:
        return {"ok": False, "path": None, "error": result["error"] or "could not fetch the clip"}
    return {"ok": True, "path": result["path"], "error": None}


async def play_clip(
    deps: Any,
    video_id: str,
    title: str,
    instance_path: str | Path | None,
) -> Dict[str, Any]:
    """Fetch (once) and play a gag clip on the robot's speaker."""
    clip = await ensure_clip(video_id, instance_path)
    if not clip["ok"]:
        return {"ok": False, "error": clip["error"]}
    return await PLAYER.play(deps, video_id=video_id, title=title, source_path=Path(str(clip["path"])))
```

- [ ] **Step 4: Implement `tools/mad_laugh.py`**

Create `reachy_companion/src/reachy_companion/tools/mad_laugh.py`:

```python
"""Play a maniacal-laughter clip on the robot's speaker (D-018). Filename == Tool.name."""

from __future__ import annotations

import logging
from typing import Any, Dict

from reachy_companion.hanova import sfx, settings
from reachy_companion.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


class MadLaugh(Tool):
    """Play the maniacal-laughter clip."""

    name = "mad_laugh"
    description = "Play a maniacal laugh out loud. 用於開玩笑、耍壞、假裝反派。"
    parameters_schema: Dict[str, Any] = {"type": "object", "properties": {}, "required": []}

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Play the cached laugh clip on the robot's speaker."""
        # Finding 10: the clip id is this tool's own prerequisite, not the
        # family's -- "music enabled" said nothing about whether a gag can play.
        available, reason = settings.tool_status(self.name)
        if not available:
            return settings.unavailable(reason)

        logger.info("Tool call: mad_laugh")
        return await sfx.play_clip(deps, settings.mad_laugh_yt_id(), "mad laugh", deps.instance_path)
```

- [ ] **Step 5: Implement `tools/self_destruct.py`**

Create `reachy_companion/src/reachy_companion/tools/self_destruct.py`:

```python
"""The self-destruct joke, on the standard confirmation gate (D-018, R2/R3).

Upstream used a bespoke `arm` / `confirm` / `abort` stage enum with its own
module-global timer (`server.py:2140-2165`). The *mechanism* here is the shared
`ConfirmationGate` -- one contract, one TTL, one place to audit -- but the
**wording stays upstream's in-character ritual** (review round 1, finding 17,
controller ruling).

Why the summary is not the generic one: the generic gate summary exists so a
user can hear exactly what irreversible thing is about to happen. Nothing
irreversible happens here -- it plays a sound -- and spelling out "it is a joke,
it plays a loud noise and nothing else" destroys the only thing the tool is for.
So the arm returns the countdown ritual, the confirmation phrase is thematic,
and `abort` is a real, code-enforced path rather than a punchline.

The TTL is still `HANOVA_CONFIRM_TTL_S` (90 s) and it is still enforced by the
gate, not by the prompt.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from reachy_companion.hanova import sfx, settings
from reachy_companion.hanova.confirm import GATE, confirmation_expired
from reachy_companion.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)

# In-character, and deliberately not an explanation (finding 17).
_ARM_SUMMARY = (
    "SELF-DESTRUCT SEQUENCE ARMED. Ninety seconds on the clock. "
    "Say 'authorise self-destruct' to commit, or 'abort self-destruct' to stand down."
)


class SelfDestruct(Tool):
    """Run the two-step self-destruct ritual on the robot's speaker."""

    name = "self_destruct"
    description = "Run the self-destruct sequence. 需要先確認或取消才會執行。"
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "confirm": {
                "type": "boolean",
                "description": "Set true when the user authorises the sequence.",
            },
            "abort": {
                "type": "boolean",
                "description": "Set true when the user stands the sequence down.",
            },
        },
        "required": [],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Arm the ritual, stand it down, or run it once authorised."""
        available, reason = settings.tool_status(self.name)
        if not available:
            return settings.unavailable(reason)

        # Finding 17: an explicit abort word, enforced here rather than left to
        # the model to interpret. Aborting something never armed is still fine.
        # Round 2, finding 2: this is the *bare* abort -- it may drop an armed
        # sequence but never yank one that is already playing, which answers
        # `action_in_flight` instead.
        if bool(kwargs.get("abort")):
            logger.info("Tool call: self_destruct aborted")
            return GATE.abort(self.name)

        if bool(kwargs.get("confirm")):
            pending = GATE.claim(self.name)
            if pending is None:
                # The 90 s window closed, or nothing was armed. In character.
                return confirmation_expired()
            logger.info("Tool call: self_destruct authorised")
            result = await sfx.play_clip(
                deps, settings.self_destruct_yt_id(), "self-destruct sequence", deps.instance_path
            )
            if result.get("ok"):
                GATE.complete(self.name, pending.claim_id)
            else:
                # A clip that would not download or play is transient: the
                # authorisation still describes exactly what the user asked for.
                GATE.release(self.name, pending.claim_id)
            return result

        return GATE.arm(self.name, _ARM_SUMMARY, {"video_id": settings.self_destruct_yt_id()})
```

- [ ] **Step 6: Enable both tools in the locked profile**

In `reachy_companion/profiles/_reachy_companion_locked_profile/profile.md`, add to `default_tools` immediately after `"email_send",`:

```toml
  "email_send",
  "self_destruct",
  "mad_laugh",
```

- [ ] **Step 7: Run the test to verify it passes**

```powershell
cd C:\Project\Reachy-mini\reachy_companion
..\.venv\Scripts\python.exe -m pytest tests/test_hanova_gags.py -q
```

Expected: green — **16** test functions, none parametrised: **16 collected
cases**. Record the exact number.

- [ ] **Step 8: Run the full gate**

```powershell
cd C:\Project\Reachy-mini\reachy_companion
..\.venv\Scripts\python.exe -m pytest -q
..\.venv\Scripts\python.exe -m ruff check src tests
..\.venv\Scripts\python.exe -m ruff format --check src tests
..\.venv\Scripts\python.exe -m mypy
```

Expected: pytest green (30 documented skips), ruff clean, mypy `Success: no issues found`.

- [ ] **Step 9: Commit**

```bash
cd /c/Project/Reachy-mini
git rev-parse --abbrev-ref HEAD   # must print feat/ha-nova-port (Task 0)
git add reachy_companion/src/reachy_companion/hanova/sfx.py \
        reachy_companion/src/reachy_companion/tools/self_destruct.py \
        reachy_companion/src/reachy_companion/tools/mad_laugh.py \
        reachy_companion/profiles/_reachy_companion_locked_profile/profile.md \
        reachy_companion/tests/test_hanova_gags.py
git commit -m "feat(hanova): self_destruct and mad_laugh gags on the robot speaker"
```

---

### Task 13: NAS home-video library — `nas_video_query`, `play_nas_video`, `nas_play_folder`, `nas_skip`

Implements the NAS quarter of R2, plus R4 (all four are house-bound), R5 and R6. Three upstream dependencies are replaced: `/opt/homebrew/bin/smbclient` + `gtimeout` become `smbprotocol` (`nasvideo/smb.py:20-21`), staging into Home Assistant's `www/` directory becomes the LAN-served media cache from Task 3, and the personal index file becomes an operator-supplied path.

**Deliberate scope decision — auto-advance is not ported.** Upstream ran an unbounded 1 Hz daemon polling Home Assistant forever, prefetching the next clip (`server.py:1976-2058`). On a CM4 with full-size home videos that is the largest risk in the whole port for the least demo value. Here the session holds the trip playlist and its position, and **`nas_skip` advances it on request** — the same user-visible capability ("play the next one"), with no background poller to own, cancel, or leak.

**Review round 1, finding 15 — staging is validated, collision-free and atomic.**
Three defects in the first draft:

* `cast_filename()` flattened and then *truncated* a NAS path to 150 characters,
  so two clips whose paths differ only past that point map to the same served
  filename — one home video silently plays in place of another. Filenames are now
  derived from a **hash of the validated relative path plus a whitelisted
  extension**, which cannot collide by truncation.
* An index entry's `cast_path` went straight into an SMB path with no validation,
  so a bad or hostile index could walk out of the configured subtree. Every path
  is now normalised and checked to resolve **inside** `HANOVA_NAS_CAST_SUBPATH`,
  and the two configured subpath accessors are finally used rather than unread.
* The SMB copy wrote directly into the served destination, so a Chromecast that
  fetched during the copy got a **partial file**. The copy now goes to a private
  `.part` file in the same directory and is `os.replace`d into place only when it
  is complete and non-empty.

**Review round 1, finding 16 — the trip session is conversation-scoped.** The
first draft's `_SESSION` was a process global that outlived the conversation,
survived `on_session_shutdown`, was not invalidated when music or a non-NAS video
superseded it on the TV, and advanced its index *before* the staging and cast
succeeded — so a failed `nas_skip` still consumed a clip. Now: the session
carries the confirmation epoch and is refused across epochs, it is cleared on
session start and shutdown and by every superseding media action, and the new
position is **committed only after the cast returns ok**.

**Review round 2, finding 10 — staging is not safe just because the rename is
atomic.** Every fetch of the same clip used the *same* deterministic `.part`
filename, so two concurrent fetches opened the same file with `"wb"`: the second
truncated the first's partial data, and whichever finished first renamed a file
the other was still writing into place. `os.replace` being atomic does not help
when both writers share the destination's staging file. Three fixes: a
**per-destination single-flight lock**, so the second caller waits and then finds
the file already staged rather than racing it; **uniquely named** temporary files
(`<destination>.<8 hex>.part`) so even a lock-free path cannot collide; and
`media_store.prune` **skipping `.part` files**, because the LRU could otherwise
delete a staging file out from under an active copy. The concurrency test now
runs two **real** `fetch_cast_file` calls released together by a barrier, instead
of mocking the very routine it claims to exercise.

**Review round 2, finding 11 — the cursor needs a token, not a global "next".**
`peek_next()` returned a clip and nothing else, and `commit_next()` advanced
whatever session happened to be current. Two concrete losses: two concurrent
skips both peeked clip *n+1*, both cast it, and both advanced — so the trip
jumped two clips for one user request; and a cast that was still in flight when
the conversation restarted committed its advance against the **new** session's
playlist. `peek_next()` now returns a `CursorToken` carrying the session
generation and the index it observed, and `commit_next(token)` is a
compare-and-swap under the session lock: it advances only if both still match,
and reports whether it did.

**Review round 2, finding 12 — `HANOVA_NAS_SUBPATH` is finally consumed.** It was
a mandatory prerequisite for all three casting tools while no NAS code read it,
so an operator could be blocked from a working deployment by a value that changed
nothing. `validate_source_path()` now bounds the subtree an index entry's
original `path` may name, exactly as `validate_cast_path()` bounds `cast_path`,
and `stage_and_cast` calls it first.

**Files:**
- Create: `reachy_companion/src/reachy_companion/hanova/nas.py`
- Create: `reachy_companion/src/reachy_companion/tools/nas_video_query.py`
- Create: `reachy_companion/src/reachy_companion/tools/play_nas_video.py`
- Create: `reachy_companion/src/reachy_companion/tools/nas_play_folder.py`
- Create: `reachy_companion/src/reachy_companion/tools/nas_skip.py`
- Modify: `reachy_companion/profiles/_reachy_companion_locked_profile/profile.md` (`default_tools` gains the four names)
- Test: `reachy_companion/tests/test_hanova_nas.py`

**Interfaces:**
- Consumes: `hanova.settings.{nas_host, nas_user, nas_password, nas_share, nas_subpath, nas_cast_subpath, nas_index_path, nas_cast_keep, tool_status, unavailable, ha_script_video_url, cast_entity}`; `hanova.redact`; `hanova.media_store.{media_dir, media_url, prune}`; `hanova.ha_client.ha_run_script`; `hanova.confirm.GATE` (for the session epoch); `home_net.{home_state, AWAY, away_from_home}`.
- Produces:
  - `hanova.nas.load_index() -> Dict[str, Any] | None`
  - `hanova.nas.filter_index(index, year=None, year_from=None, year_to=None, place=None, keyword=None, limit=None) -> List[Dict[str, Any]]`
  - `hanova.nas.summarize_folders(index: Dict[str, Any]) -> List[Dict[str, Any]]`
  - `hanova.nas.folder_playlist(index: Dict[str, Any], top_folder: str) -> List[Dict[str, Any]]`
  - `hanova.nas.video_title(video: Dict[str, Any]) -> str`
  - `hanova.nas.ALLOWED_EXTENSIONS: frozenset[str]` (`{".mp4", ".m4v", ".mov"}`)
  - `hanova.nas.validate_cast_path(cast_path: str) -> str` — normalises and proves the path resolves **inside** `HANOVA_NAS_CAST_SUBPATH`; raises `NasError` otherwise (finding 15)
  - `hanova.nas.validate_source_path(path: str) -> str` — the same check against **`HANOVA_NAS_SUBPATH`** for the *original* index path (round 2, finding 12). That key was a mandatory prerequisite of all three casting tools and nothing read it, so a fresh deployment could be blocked on a value with no behaviour attached. It now bounds the subtree an index entry's `path` may name, and `stage_and_cast` calls it before anything else happens
  - `hanova.nas.cast_filename(cast_path: str) -> str` — `"<16 hex of the validated path>.mp4"`; **collision-free**, no truncation (finding 15)
  - `hanova.nas.fetch_cast_file(cast_path: str, destination: Path) -> None` — synchronous; copies to a **uniquely named** `<destination>.<8 hex>.part` sibling and `os.replace`s it into place, under a **per-destination single-flight lock**; raises `NasError` (round 2, finding 10)
  - `hanova.nas.PART_SUFFIX: str` — `".part"`. `media_store.prune` skips anything ending in it, so a concurrent staging cannot be pruned out from under the writer (round 2, finding 10)
  - `hanova.nas.NasError(RuntimeError)`
  - `hanova.nas.start_session(playlist: List[Dict[str, Any]], index: int) -> None` — stamps the current confirmation epoch and a fresh **session generation** (finding 16, round 2 finding 11)
  - `hanova.nas.clear_session() -> None`
  - `hanova.nas.CursorToken` — frozen dataclass with `generation: int`, `expected_index: int`, `next_index: int`
  - `hanova.nas.peek_next() -> tuple[Dict[str, Any] | None, CursorToken | None, str | None]` — the next clip **without moving the cursor**, plus the token that identifies *this* advance. Error is `"nothing_playing"` or `"last_clip"` (round 2, finding 11)
  - `hanova.nas.commit_next(token: CursorToken) -> bool` — an atomic compare-and-swap: the cursor moves only if the session generation and the current index still match what the token recorded. Returns whether it moved (round 2, finding 11)
  - `hanova.nas.remaining() -> int` — clips left after the one on screen
  - `hanova.nas.stage_and_cast(video: Dict[str, Any], instance_path: str | Path | None) -> Awaitable[Dict[str, Any]]` returning `{"ok": bool, "url": str | None, "title": str, "error": str | None}`
  - `tools.nas_video_query.NasVideoQuery`, `tools.play_nas_video.PlayNasVideo`, `tools.nas_play_folder.NasPlayFolder`, `tools.nas_skip.NasSkip`
- **All four tools use the same three-way house-bound preamble as Task 6** (round 2, finding 3): `AWAY` returns `away_from_home()`, and anything that is not `HOME` returns `home_unknown()` **after doing no work at all** — no index read, no SMB connection, no staging, no cast.
- **Superseding actions clear the trip session** (finding 16). `play_music`,
  `play_video` and `show_on_tv` each call `nas.clear_session()` on success — the
  trip is no longer what is on the TV, so `nas_skip` must not silently continue
  it. `music_hooks.on_session_started` and `on_session_shutdown` also clear it
  (one added line each in Step 7 below).

- [ ] **Step 1: Write the failing test**

Create `reachy_companion/tests/test_hanova_nas.py`:

```python
"""Contract tests for the NAS home-video tools (D-018, R2/R4/R5/R6). No SMB.

Also pins review round 1 findings 15 (validated paths, collision-free names,
atomic staging) and 16 (a conversation-scoped session whose cursor only moves
after a successful cast), and review round 2 findings 3 (tri-state house
gating), 5 (synthetic fixtures only), 10 (single-flight staging) and 11 (a
cursor token, compared and swapped atomically).

**Every identifier below is a synthetic sentinel** (round 2, finding 5). The
previous version used share, folder and place names copied from the operator's
private manifest -- which committed those identifiers to the repository inside
the very test suite written to keep them out of it. The `SENTINEL_*_q4` tokens
are deliberately unmistakable: they cannot be confused with a real NAS layout by
a reader, and the untracked scan in Task 14 Step 9b treats any of them appearing
next to a real value as a failure.
"""

import json
import time
import types
import importlib
from typing import Any

import pytest

from reachy_companion import home_net
from reachy_companion.hanova import nas, settings
from reachy_companion.hanova.confirm import GATE
from reachy_companion.tools.nas_skip import NasSkip
from reachy_companion.tools.play_nas_video import PlayNasVideo
from reachy_companion.tools.nas_play_folder import NasPlayFolder
from reachy_companion.tools.nas_video_query import NasVideoQuery


INDEX = {
    "folders": {
        "SENTINEL_TRIP_q4": {"year": 2019, "place": "SENTINEL_PLACE_q4", "country": "SENTINEL_COUNTRY_q4", "count": 2, "is_travel": True},
    },
    "videos": [
        {
            "path": "SENTINEL_SRC_DIR_q4/SENTINEL_TRIP_q4/clip01.mp4",
            "cast_path": "SENTINEL_CAST_DIR_q4/SENTINEL_TRIP_q4/clip01.mp4",
            "cast_ready": True,
            "year": 2019,
            "place": "SENTINEL_PLACE_q4",
            "country": "SENTINEL_COUNTRY_q4",
            "label": "morning",
            "top_folder": "SENTINEL_TRIP_q4",
            "name": "clip01",
            "seq": 1,
        },
        {
            "path": "SENTINEL_SRC_DIR_q4/SENTINEL_TRIP_q4/clip02.mp4",
            "cast_path": "SENTINEL_CAST_DIR_q4/SENTINEL_TRIP_q4/clip02.mp4",
            "cast_ready": True,
            "year": 2019,
            "place": "SENTINEL_PLACE_q4",
            "country": "SENTINEL_COUNTRY_q4",
            "label": "evening",
            "top_folder": "SENTINEL_TRIP_q4",
            "name": "clip02",
            "seq": 2,
        },
    ],
}


def _deps(tmp_path):
    return types.SimpleNamespace(reachy_mini=None, instance_path=tmp_path)


@pytest.fixture(autouse=True)
def configured(monkeypatch, tmp_path):
    """A configured nas family, a robot at home, and no SMB or HTTP anywhere."""
    index_path = tmp_path / "nas-video-index.json"
    index_path.write_text(json.dumps(INDEX), encoding="utf-8")
    monkeypatch.setenv("HANOVA_NAS_HOST", "nas.example.invalid")
    monkeypatch.setenv("HANOVA_NAS_USER", "u")
    monkeypatch.setenv("HANOVA_NAS_PASSWORD", "p")
    monkeypatch.setenv("HANOVA_NAS_SHARE", "SENTINEL_SHARE_q4")
    monkeypatch.setenv("HANOVA_NAS_SUBPATH", "SENTINEL_SRC_DIR_q4")
    monkeypatch.setenv("HANOVA_NAS_CAST_SUBPATH", "SENTINEL_CAST_DIR_q4")
    monkeypatch.setenv("HANOVA_NAS_INDEX_PATH", str(index_path))
    monkeypatch.setenv("HA_URL", "http://ha.example.invalid:8123")
    monkeypatch.setenv("HA_TOKEN", "tok")
    monkeypatch.setenv("HANOVA_HA_SCRIPT_VIDEO_URL", "tv_show_video_url")
    monkeypatch.setenv("HANOVA_CAST_ENTITY", "media_player.example_tv")
    monkeypatch.setenv("HANOVA_MEDIA_HTTP_BASE", "http://robot.example.invalid:7860")
    settings.set_media_mount_ready(True)
    home_net.reset_cache()
    GATE.reset()
    GATE.begin_session()
    nas.clear_session()

    async def always_home() -> str:
        return home_net.HOME

    for module in (
        "reachy_companion.tools.nas_video_query",
        "reachy_companion.tools.play_nas_video",
        "reachy_companion.tools.nas_play_folder",
        "reachy_companion.tools.nas_skip",
    ):
        monkeypatch.setattr(f"{module}.home_state", always_home)
    yield
    nas.clear_session()
    GATE.reset()
    settings.set_media_mount_ready(False)
    home_net.reset_cache()


def _stub_transfer(monkeypatch) -> dict:
    """Replace the SMB fetch and the HA cast with recorders."""
    recorded: dict[str, Any] = {"fetched": [], "cast": []}

    def fake_fetch(cast_path, destination):
        recorded["fetched"].append(cast_path)
        destination.write_bytes(b"MP4")

    async def fake_run_script(script_name, data, timeout_s=60.0):
        recorded["cast"].append((script_name, data))
        return {"ok": True, "result": []}

    monkeypatch.setattr(nas, "fetch_cast_file", fake_fetch)
    monkeypatch.setattr(nas, "ha_run_script", fake_run_script)
    return recorded


def test_tool_names_match_their_filenames():
    """The loader resolves tools by filename == Tool.name."""
    assert NasVideoQuery.name == "nas_video_query"
    assert PlayNasVideo.name == "play_nas_video"
    assert NasPlayFolder.name == "nas_play_folder"
    assert NasSkip.name == "nas_skip"


def test_descriptions_carry_no_personal_identifier():
    """R10: upstream put the owner's name and real place names in these."""
    for tool in (NasVideoQuery(), PlayNasVideo(), NasPlayFolder(), NasSkip()):
        assert "@" not in tool.description
        assert "SENTINEL_PLACE_q4" not in tool.description
        assert len(tool.description) <= 120


def test_filter_index_matches_place_case_insensitively():
    """Voice input has no case, and the index is mixed-language."""
    assert len(nas.filter_index(INDEX, place="sentinel_place_q4")) == 2
    assert nas.filter_index(INDEX, place="nowhere") == []


def test_filter_index_filters_by_year_range():
    """Year filters are how a user says "the trip a few years ago"."""
    assert len(nas.filter_index(INDEX, year=2019)) == 2
    assert nas.filter_index(INDEX, year_from=2020) == []


def test_folder_playlist_is_ordered_by_sequence():
    """Advancing through a trip must follow the recorded order."""
    playlist = nas.folder_playlist(INDEX, "SENTINEL_TRIP_q4")
    assert [video["name"] for video in playlist] == ["clip01", "clip02"]


def test_cast_filename_is_flat_and_safe():
    """The served name must contain no separators the static route could walk."""
    name = nas.cast_filename("SENTINEL_CAST_DIR_q4/SENTINEL_TRIP_q4/clip01.mp4")
    assert "/" not in name and name.endswith(".mp4")


# --- path validation and naming (review finding 15) -----------------------
def test_long_paths_that_share_a_prefix_do_not_collide():
    """Finding 15: truncation mapped two different home videos onto one name."""
    prefix = "SENTINEL_CAST_DIR_q4/" + "a" * 200
    first = nas.cast_filename(f"{prefix}/clip01.mp4")
    second = nas.cast_filename(f"{prefix}/clip02.mp4")
    assert first != second


def test_the_served_name_leaks_no_folder_names():
    """A LAN URL is visible to anything on the network, including guests."""
    name = nas.cast_filename("SENTINEL_CAST_DIR_q4/SENTINEL_TRIP_q4/clip01.mp4")
    for token in ("SENTINEL", "TRIP", "CAST", "clip01", "2019"):
        assert token not in name


@pytest.mark.parametrize(
    "bad",
    [
        "../etc/passwd",
        "SENTINEL_CAST_DIR_q4/../../etc/passwd",
        "/absolute/SENTINEL_CAST_DIR_q4/clip.mp4",
        "SomeOtherFolder/clip.mp4",
        "C:/Windows/clip.mp4",
        "SENTINEL_CAST_DIR_q4/clip.exe",
        "",
    ],
)
def test_paths_outside_the_configured_subtree_are_refused(bad):
    """Finding 15: an index entry is untrusted input, not a path to interpolate."""
    with pytest.raises(nas.NasError):
        nas.cast_filename(bad)


def test_a_valid_path_normalises_rather_than_being_refused():
    """A tidy-but-redundant path from the index is fine."""
    assert nas.validate_cast_path("SENTINEL_CAST_DIR_q4/./SENTINEL_TRIP_q4/clip01.mp4") == (
        "SENTINEL_CAST_DIR_q4/SENTINEL_TRIP_q4/clip01.mp4"
    )


def test_the_configured_cast_subpath_is_actually_used(monkeypatch):
    """Finding 15: the accessor existed and nothing read it."""
    monkeypatch.setenv("HANOVA_NAS_CAST_SUBPATH", "Elsewhere")
    with pytest.raises(nas.NasError):
        nas.validate_cast_path("SENTINEL_CAST_DIR_q4/SENTINEL_TRIP_q4/clip01.mp4")
    assert nas.validate_cast_path("Elsewhere/clip01.mp4") == "Elsewhere/clip01.mp4"


# --- HANOVA_NAS_SUBPATH is consumed (round 2, finding 12) -----------------
def test_the_configured_source_subpath_is_actually_used(monkeypatch):
    """Round 2, finding 12: a mandatory prerequisite that nothing read.

    `HANOVA_NAS_SUBPATH` blocked all three casting tools while changing no
    behaviour whatsoever. It now bounds the subtree an index entry's *original*
    path may name.
    """
    assert nas.validate_source_path("SENTINEL_SRC_DIR_q4/SENTINEL_TRIP_q4/clip01.mp4") == (
        "SENTINEL_SRC_DIR_q4/SENTINEL_TRIP_q4/clip01.mp4"
    )
    monkeypatch.setenv("HANOVA_NAS_SUBPATH", "SomewhereElse")
    with pytest.raises(nas.NasError):
        nas.validate_source_path("SENTINEL_SRC_DIR_q4/SENTINEL_TRIP_q4/clip01.mp4")


@pytest.mark.asyncio
async def test_an_index_entry_outside_the_source_subpath_is_never_staged(monkeypatch, tmp_path):
    """The prerequisite has to change behaviour, or it is a dead switch."""
    recorded = _stub_transfer(monkeypatch)
    stray = dict(INDEX["videos"][0])
    stray["path"] = "SomewhereElse/SENTINEL_TRIP_q4/clip01.mp4"
    out = await nas.stage_and_cast(stray, tmp_path)
    assert out["ok"] is False
    assert recorded["fetched"] == [] and recorded["cast"] == []


def test_the_copy_is_staged_privately_and_renamed(monkeypatch, tmp_path):
    """Finding 15: a Chromecast fetching mid-copy must not get a partial file."""
    seen = {}

    class _FakeSmbFile:
        def __init__(self) -> None:
            self._data = b"MP4DATA"

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def read(self, size=-1):
            data, self._data = self._data, b""
            return data

    class _FakeSmbClient:
        @staticmethod
        def register_session(host, username=None, password=None):
            seen["host"] = host

        @staticmethod
        def open_file(path, mode="rb"):
            seen["remote"] = path
            # Prove the served file does not exist yet while the copy runs.
            seen["destination_exists_midway"] = (tmp_path / "out.mp4").exists()
            partials = list(tmp_path.glob("*.part"))
            seen["partial_seen"] = bool(partials)
            return _FakeSmbFile()

    monkeypatch.setitem(__import__("sys").modules, "smbclient", _FakeSmbClient)
    destination = tmp_path / "out.mp4"
    nas.fetch_cast_file("SENTINEL_CAST_DIR_q4/SENTINEL_TRIP_q4/clip01.mp4", destination)

    assert destination.read_bytes() == b"MP4DATA"
    assert seen["destination_exists_midway"] is False
    assert list(tmp_path.glob("*.part")) == [], "the .part file must be renamed away"
    assert seen["remote"].startswith("\\\\nas.example.invalid\\SENTINEL_SHARE_q4\\")


def test_a_failed_copy_leaves_nothing_behind(monkeypatch, tmp_path):
    """A half-written clip is worse than no clip."""

    class _Boom:
        @staticmethod
        def register_session(host, username=None, password=None):
            return None

        @staticmethod
        def open_file(path, mode="rb"):
            raise OSError("connection refused")

    monkeypatch.setitem(__import__("sys").modules, "smbclient", _Boom)
    destination = tmp_path / "out.mp4"
    with pytest.raises(nas.NasError):
        nas.fetch_cast_file("SENTINEL_CAST_DIR_q4/SENTINEL_TRIP_q4/clip01.mp4", destination)
    assert not destination.exists()
    assert list(tmp_path.glob("*.part")) == []


def test_the_smb_error_text_never_reaches_the_caller(monkeypatch, tmp_path):
    """Finding 7: an SMB error quotes the full share path back."""
    sentinel = "SENTINEL_PRIVATE_x7"

    class _Boom:
        @staticmethod
        def register_session(host, username=None, password=None):
            return None

        @staticmethod
        def open_file(path, mode="rb"):
            raise OSError(f"cannot open {sentinel}")

    monkeypatch.setitem(__import__("sys").modules, "smbclient", _Boom)
    with pytest.raises(nas.NasError) as excinfo:
        nas.fetch_cast_file("SENTINEL_CAST_DIR_q4/SENTINEL_TRIP_q4/clip01.mp4", tmp_path / "out.mp4")
    assert sentinel not in str(excinfo.value)


def test_load_index_returns_none_when_missing(monkeypatch, tmp_path):
    """A missing index is a configuration fact, not an exception."""
    monkeypatch.setenv("HANOVA_NAS_INDEX_PATH", str(tmp_path / "absent.json"))
    assert nas.load_index() is None


@pytest.mark.asyncio
async def test_nas_video_query_is_unavailable_without_the_index(monkeypatch, tmp_path):
    """R5: the tool is dead without the operator-supplied index, and it says so."""
    monkeypatch.delenv("HANOVA_NAS_INDEX_PATH")
    out = await NasVideoQuery()(deps=_deps(tmp_path), place="sentinel_place_q4")
    assert out == {"status": "unavailable", "reason": "HANOVA_NAS_INDEX_PATH"}


@pytest.mark.asyncio
async def test_nas_video_query_is_away_from_home_off_the_lan(monkeypatch, tmp_path):
    """R4: all nas_* tools are house-bound."""

    async def not_home() -> str:
        return home_net.AWAY

    monkeypatch.setattr("reachy_companion.tools.nas_video_query.home_state", not_home)
    out = await NasVideoQuery()(deps=_deps(tmp_path), place="sentinel_place_q4")
    assert out == {"status": "away_from_home"}


@pytest.mark.parametrize(
    "module,tool_factory,kwargs",
    [
        ("nas_video_query", NasVideoQuery, {"place": "sentinel_place_q4"}),
        ("play_nas_video", PlayNasVideo, {"place": "sentinel_place_q4"}),
        ("nas_play_folder", NasPlayFolder, {"top_folder": "SENTINEL_TRIP_q4"}),
        ("nas_skip", NasSkip, {}),
    ],
)
@pytest.mark.asyncio
async def test_every_nas_tool_does_no_work_when_home_is_unknown(
    monkeypatch, tmp_path, module, tool_factory, kwargs
):
    """Round 2, finding 3: UNKNOWN is not permission, for any of the four.

    Round 1 branched only on AWAY, so an HA outage or a VPN let all four fall
    through and touch the index, the NAS and Home Assistant. The answer must be
    its own status and nothing may happen.
    """

    async def unknown() -> str:
        return home_net.UNKNOWN

    def fail_load():
        raise AssertionError(f"{module} must not read the index on UNKNOWN")

    def fail_fetch(cast_path, destination):
        raise AssertionError(f"{module} must not touch the NAS on UNKNOWN")

    async def fail_cast(script_name, data, timeout_s=60.0):
        raise AssertionError(f"{module} must not touch Home Assistant on UNKNOWN")

    monkeypatch.setattr(f"reachy_companion.tools.{module}.home_state", unknown)
    monkeypatch.setattr(nas, "load_index", fail_load)
    monkeypatch.setattr(nas, "fetch_cast_file", fail_fetch)
    monkeypatch.setattr(nas, "ha_run_script", fail_cast)

    out = await tool_factory()(deps=_deps(tmp_path), **kwargs)
    assert out["status"] == "home_status_unknown"
    assert out["status"] != "away_from_home"
    assert out["error"]


@pytest.mark.asyncio
async def test_nas_video_query_needs_no_smb_credentials(monkeypatch, tmp_path):
    """Finding 10: it reads a local JSON file and touches nothing else."""
    monkeypatch.delenv("HANOVA_NAS_HOST")
    monkeypatch.delenv("HANOVA_NAS_USER")
    monkeypatch.delenv("HANOVA_NAS_PASSWORD")
    out = await NasVideoQuery()(deps=_deps(tmp_path), place="sentinel_place_q4")
    assert out["ok"] is True and out["count"] == 2


@pytest.mark.asyncio
async def test_nas_video_query_returns_matching_clips(tmp_path):
    """Ground-truth records only: the model must never invent a clip."""
    out = await NasVideoQuery()(deps=_deps(tmp_path), place="sentinel_place_q4")
    assert out["ok"] is True and out["count"] == 2
    assert out["videos"][0]["path"].endswith("clip01.mp4")


@pytest.mark.asyncio
async def test_nas_video_query_with_no_filters_summarises_folders(tmp_path):
    """"What home videos do we have?" gets an overview, not 2800 rows."""
    out = await NasVideoQuery()(deps=_deps(tmp_path))
    assert out["ok"] is True
    assert out["folders"][0]["top_folder"] == "SENTINEL_TRIP_q4"


@pytest.mark.asyncio
async def test_play_nas_video_stages_and_casts_a_lan_url(monkeypatch, tmp_path):
    """R6: the TV fetches the robot's own LAN URL, not a path on our disk."""
    recorded = _stub_transfer(monkeypatch)
    out = await PlayNasVideo()(deps=_deps(tmp_path), place="sentinel_place_q4", keyword="morning")
    assert out["ok"] is True and out["status"] == "casting"
    assert recorded["fetched"] == ["SENTINEL_CAST_DIR_q4/SENTINEL_TRIP_q4/clip01.mp4"]
    script, data = recorded["cast"][0]
    assert script == "tv_show_video_url"
    assert data["url"].startswith("http://robot.example.invalid:7860/hanova-media/nas/")


@pytest.mark.asyncio
async def test_play_nas_video_reuses_a_staged_file(monkeypatch, tmp_path):
    """A second play of the same clip must not re-copy it off the NAS."""
    recorded = _stub_transfer(monkeypatch)
    await PlayNasVideo()(deps=_deps(tmp_path), path="SENTINEL_SRC_DIR_q4/SENTINEL_TRIP_q4/clip01.mp4")
    await PlayNasVideo()(deps=_deps(tmp_path), path="SENTINEL_SRC_DIR_q4/SENTINEL_TRIP_q4/clip01.mp4")
    assert len(recorded["fetched"]) == 1


@pytest.mark.asyncio
async def test_play_nas_video_reports_no_match(monkeypatch, tmp_path):
    """An unknown request must not silently cast something else."""
    _stub_transfer(monkeypatch)
    out = await PlayNasVideo()(deps=_deps(tmp_path), place="atlantis")
    assert out["ok"] is False and out["error"] == "no_match"


@pytest.mark.asyncio
async def test_play_nas_video_reports_an_smb_failure(monkeypatch, tmp_path):
    """A NAS that is off must produce a spoken answer, not a stack trace."""

    def boom(cast_path, destination):
        raise nas.NasError("the video could not be copied from the NAS")

    monkeypatch.setattr(nas, "fetch_cast_file", boom)
    out = await PlayNasVideo()(deps=_deps(tmp_path), path="SENTINEL_SRC_DIR_q4/SENTINEL_TRIP_q4/clip01.mp4")
    assert out["ok"] is False
    assert out["error"] == "the video could not be copied from the NAS"


@pytest.mark.asyncio
async def test_an_unlisted_nas_error_text_never_reaches_the_caller(monkeypatch, tmp_path):
    """Round 2, finding 6: `str(exc)` relied on an invariant nothing enforced."""

    def boom(cast_path, destination):
        raise nas.NasError("cannot open \\\\SENTINEL_PRIVATE_x7\\share\\clip.mp4")

    monkeypatch.setattr(nas, "fetch_cast_file", boom)
    out = await PlayNasVideo()(deps=_deps(tmp_path), path="SENTINEL_SRC_DIR_q4/SENTINEL_TRIP_q4/clip01.mp4")
    assert out["ok"] is False
    assert "SENTINEL_PRIVATE_x7" not in out["error"]
    assert out["error"] == "that home video could not be prepared"


@pytest.mark.asyncio
async def test_nas_play_folder_starts_at_the_first_clip(monkeypatch, tmp_path):
    """A whole trip plays in order, starting from the first clip."""
    recorded = _stub_transfer(monkeypatch)
    out = await NasPlayFolder()(deps=_deps(tmp_path), top_folder="SENTINEL_TRIP_q4")
    assert out["ok"] is True and out["remaining"] == 1
    assert recorded["fetched"] == ["SENTINEL_CAST_DIR_q4/SENTINEL_TRIP_q4/clip01.mp4"]


@pytest.mark.asyncio
async def test_nas_skip_advances_to_the_next_clip(monkeypatch, tmp_path):
    """"Next one" is the whole of the auto-advance capability we port."""
    recorded = _stub_transfer(monkeypatch)
    await NasPlayFolder()(deps=_deps(tmp_path), top_folder="SENTINEL_TRIP_q4")
    out = await NasSkip()(deps=_deps(tmp_path))
    assert out["ok"] is True and out["status"] == "casting"
    assert recorded["fetched"][-1] == "SENTINEL_CAST_DIR_q4/SENTINEL_TRIP_q4/clip02.mp4"


@pytest.mark.asyncio
async def test_nas_skip_reports_the_end_of_a_trip(monkeypatch, tmp_path):
    """At the end there is nothing to skip to, and that must be said plainly."""
    _stub_transfer(monkeypatch)
    await NasPlayFolder()(deps=_deps(tmp_path), top_folder="SENTINEL_TRIP_q4")
    await NasSkip()(deps=_deps(tmp_path))
    out = await NasSkip()(deps=_deps(tmp_path))
    assert out["ok"] is False and out["error"] == "last_clip"


@pytest.mark.asyncio
async def test_nas_skip_without_a_session_reports_nothing_playing(tmp_path):
    """Skipping when nothing is playing is a clean answer."""
    out = await NasSkip()(deps=_deps(tmp_path))
    assert out["ok"] is False and out["error"] == "nothing_playing"


# --- session scoping and cursor discipline (review finding 16) ------------
@pytest.mark.asyncio
async def test_a_failed_skip_does_not_consume_a_clip(monkeypatch, tmp_path):
    """Finding 16: the cursor moved before the cast, so a failure ate a clip."""
    recorded = _stub_transfer(monkeypatch)
    await NasPlayFolder()(deps=_deps(tmp_path), top_folder="SENTINEL_TRIP_q4")

    async def failing_cast(script_name, data, timeout_s=60.0):
        return {"ok": False, "error": "Home Assistant returned HTTP 500"}

    monkeypatch.setattr(nas, "ha_run_script", failing_cast)
    failed = await NasSkip()(deps=_deps(tmp_path))
    assert failed["ok"] is False

    async def working_cast(script_name, data, timeout_s=60.0):
        recorded["cast"].append((script_name, data))
        return {"ok": True, "result": []}

    monkeypatch.setattr(nas, "ha_run_script", working_cast)
    out = await NasSkip()(deps=_deps(tmp_path))
    assert out["ok"] is True
    assert recorded["fetched"][-1] == "SENTINEL_CAST_DIR_q4/SENTINEL_TRIP_q4/clip02.mp4"


@pytest.mark.asyncio
async def test_the_trip_session_does_not_survive_a_new_conversation(monkeypatch, tmp_path):
    """Finding 16: the session was a process global that outlived its context."""
    _stub_transfer(monkeypatch)
    await NasPlayFolder()(deps=_deps(tmp_path), top_folder="SENTINEL_TRIP_q4")
    assert nas.remaining() == 1

    GATE.begin_session()  # a realtime reconnect

    assert nas.remaining() == 0
    out = await NasSkip()(deps=_deps(tmp_path))
    assert out["ok"] is False and out["error"] == "nothing_playing"


# --- the cursor token (round 2, finding 11) -------------------------------
def test_peek_next_returns_a_token_identifying_this_advance(monkeypatch):
    """Round 2, finding 11: "the next clip" is not enough to commit against."""
    playlist = list(INDEX["videos"])
    nas.start_session(playlist, 0)
    video, token, error = nas.peek_next()
    assert error is None
    assert video is not None and token is not None
    assert token.expected_index == 0 and token.next_index == 1


def test_only_the_first_of_two_concurrent_skips_can_commit(monkeypatch):
    """Round 2, finding 11: two skips used to consume two clips for one request."""
    nas.start_session(list(INDEX["videos"]), 0)
    _video_a, token_a, _ = nas.peek_next()
    _video_b, token_b, _ = nas.peek_next()
    assert token_a is not None and token_b is not None
    assert token_a.next_index == token_b.next_index == 1

    assert nas.commit_next(token_a) is True
    # The second token observed index 0, which is no longer where the cursor is.
    assert nas.commit_next(token_b) is False
    assert nas.remaining() == 0


def test_a_token_from_a_superseded_playlist_cannot_commit(monkeypatch):
    """Round 2, finding 11: an in-flight cast must not advance a new trip."""
    nas.start_session(list(INDEX["videos"]), 0)
    _video, stale_token, _ = nas.peek_next()
    assert stale_token is not None

    nas.start_session(list(INDEX["videos"]), 0)  # a new trip started meanwhile

    assert nas.commit_next(stale_token) is False
    assert nas.remaining() == 1, "the new trip's cursor is untouched"


def test_a_token_taken_before_a_clear_cannot_commit(monkeypatch):
    """A superseding media action ends the trip; a late cast may not revive it."""
    nas.start_session(list(INDEX["videos"]), 0)
    _video, token, _ = nas.peek_next()
    assert token is not None
    nas.clear_session()
    assert nas.commit_next(token) is False


@pytest.mark.asyncio
async def test_a_session_replacement_during_an_in_flight_cast_is_refused(monkeypatch, tmp_path):
    """The end-to-end version: a new trip starts while a skip is staging."""
    import asyncio as _asyncio

    recorded = _stub_transfer(monkeypatch)
    await NasPlayFolder()(deps=_deps(tmp_path), top_folder="SENTINEL_TRIP_q4")

    released = _asyncio.Event()

    async def slow_cast(script_name, data, timeout_s=60.0):
        recorded["cast"].append((script_name, data))
        await released.wait()
        return {"ok": True, "result": []}

    monkeypatch.setattr(nas, "ha_run_script", slow_cast)
    skip_task = _asyncio.create_task(NasSkip()(deps=_deps(tmp_path)))
    await _asyncio.sleep(0)

    # A new trip begins while the skip's cast is still in flight.
    nas.start_session(list(INDEX["videos"]), 0)
    released.set()
    out = await skip_task

    assert out["ok"] is True, "the clip did reach the TV; that part is honest"
    assert nas.remaining() == 1, "the new trip's cursor was not advanced by the old cast"


@pytest.mark.asyncio
async def test_two_concurrent_skips_advance_the_trip_by_exactly_one(monkeypatch, tmp_path):
    """Round 2, finding 11, stated as the user-visible loss: a skipped clip."""
    import asyncio as _asyncio

    _stub_transfer(monkeypatch)
    await NasPlayFolder()(deps=_deps(tmp_path), top_folder="SENTINEL_TRIP_q4")

    await _asyncio.gather(
        NasSkip()(deps=_deps(tmp_path)),
        NasSkip()(deps=_deps(tmp_path)),
    )
    assert nas.remaining() == 0, "two concurrent skips must not consume two clips"


@pytest.mark.asyncio
async def test_music_supersedes_the_trip_session(monkeypatch, tmp_path):
    """Finding 16: nas_skip must not silently continue a trip nobody is watching."""
    _stub_transfer(monkeypatch)
    await NasPlayFolder()(deps=_deps(tmp_path), top_folder="SENTINEL_TRIP_q4")
    assert nas.remaining() == 1

    nas.clear_session()  # what tools/play_music.py calls on a successful play

    out = await NasSkip()(deps=_deps(tmp_path))
    assert out["ok"] is False and out["error"] == "nothing_playing"


@pytest.mark.asyncio
async def test_the_shutdown_hook_clears_the_trip_session(monkeypatch, tmp_path):
    """Finding 16: a closing conversation leaves no playlist behind."""
    import types as _types

    from reachy_companion.hanova import music_hooks

    import httpx

    class _Ok:
        status_code = 200

    async def ok_post(self, *args, **kwargs):
        return _Ok()

    monkeypatch.setattr(httpx.AsyncClient, "post", ok_post)
    hook_deps = _types.SimpleNamespace(
        reachy_mini=_types.SimpleNamespace(_daemon_http_url="http://127.0.0.1:8000"),
        instance_path=tmp_path,
    )
    # Round 3, finding 2: the cleanup hook only acts for the LIVE session, so the
    # token has to be minted before the trip exists -- `on_session_started` also
    # clears the trip session (Step 6b).
    token = await music_hooks.on_session_started(hook_deps)

    _stub_transfer(monkeypatch)
    await NasPlayFolder()(deps=_deps(tmp_path), top_folder="SENTINEL_TRIP_q4")
    assert nas.remaining() == 1

    await music_hooks.on_session_shutdown(hook_deps, token)
    assert nas.remaining() == 0


@pytest.mark.asyncio
async def test_two_real_concurrent_fetches_release_together_do_not_corrupt_each_other(
    monkeypatch, tmp_path
):
    """**Round 2, finding 10: the real fetch, two threads, one barrier.**

    The previous version of this test stubbed out `fetch_cast_file` -- the exact
    routine whose concurrency it claimed to prove -- so it could not have failed
    however broken the staging was. This runs the real one twice against one
    destination, released simultaneously, with a slow SMB source so the copies
    genuinely overlap.
    """
    import threading as _threading
    import asyncio as _asyncio

    payload = b"MP4" + bytes(200_000)
    barrier = _threading.Barrier(2)
    opens = {"n": 0}

    class _SlowSmbFile:
        def __init__(self) -> None:
            self._chunks = [payload[i : i + 8192] for i in range(0, len(payload), 8192)]

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def read(self, size=-1):
            if not self._chunks:
                return b""
            time.sleep(0.001)  # let the other writer interleave if it can
            return self._chunks.pop(0)

    class _SlowSmbClient:
        @staticmethod
        def register_session(host, username=None, password=None):
            return None

        @staticmethod
        def open_file(path, mode="rb"):
            opens["n"] += 1
            barrier.wait(timeout=5)  # both copies start together or not at all
            return _SlowSmbFile()

    monkeypatch.setitem(__import__("sys").modules, "smbclient", _SlowSmbClient)
    destination = tmp_path / "clip.mp4"

    async def fetch():
        await _asyncio.to_thread(
            nas.fetch_cast_file, "SENTINEL_CAST_DIR_q4/SENTINEL_TRIP_q4/clip01.mp4", destination
        )

    # The single-flight lock means the second caller may never open the file at
    # all; the barrier therefore has a timeout and a second waiter that arrives
    # late is fine. What must hold is the *result*.
    results = await _asyncio.gather(fetch(), fetch(), return_exceptions=True)
    for result in results:
        assert not isinstance(result, BaseException), result

    assert destination.read_bytes() == payload, "one writer truncated the other"
    assert list(tmp_path.glob(f"*{nas.PART_SUFFIX}")) == [], "no staging file survives"


@pytest.mark.asyncio
async def test_concurrent_plays_of_the_same_clip_both_succeed(monkeypatch, tmp_path):
    """The tool-level version: two "play that one" requests must both answer."""
    import asyncio as _asyncio

    _stub_transfer(monkeypatch)
    results = await _asyncio.gather(
        PlayNasVideo()(deps=_deps(tmp_path), path="SENTINEL_SRC_DIR_q4/SENTINEL_TRIP_q4/clip01.mp4"),
        PlayNasVideo()(deps=_deps(tmp_path), path="SENTINEL_SRC_DIR_q4/SENTINEL_TRIP_q4/clip01.mp4"),
    )
    assert all(result["ok"] for result in results)
    nas_dir = tmp_path / "hanova_media" / "nas"
    assert list(nas_dir.glob(f"*{nas.PART_SUFFIX}")) == []


def test_pruning_never_deletes_a_staging_file(tmp_path):
    """Round 2, finding 10: the LRU must not race an in-progress copy."""
    from reachy_companion.hanova import media_store

    nas_dir = media_store.media_dir("nas", tmp_path)
    (nas_dir / "old.mp4").write_bytes(b"MP4")
    staging = nas_dir / f"new.mp4.abcd1234{nas.PART_SUFFIX}"
    staging.write_bytes(b"partial")

    media_store.prune("nas", tmp_path, keep=0)
    assert staging.exists(), "a .part file is an active writer, not LRU fodder"
    assert not (nas_dir / "old.mp4").exists()


@pytest.mark.asyncio
async def test_a_rejected_index_path_is_reported_not_cast(monkeypatch, tmp_path):
    """Finding 15: a bad index entry stops here, not at the SMB layer."""
    recorded = _stub_transfer(monkeypatch)
    bad = dict(INDEX["videos"][0])
    bad["cast_path"] = "../../etc/passwd"
    out = await nas.stage_and_cast(bad, tmp_path)
    assert out["ok"] is False
    assert recorded["fetched"] == [] and recorded["cast"] == []


@pytest.mark.asyncio
async def test_nas_logs_never_carry_a_clip_path(monkeypatch, caplog, tmp_path):
    """Finding 7: home-video folder names are the most personal data in the port."""
    import logging

    _stub_transfer(monkeypatch)
    caplog.set_level(logging.DEBUG)
    await PlayNasVideo()(deps=_deps(tmp_path), path="SENTINEL_SRC_DIR_q4/SENTINEL_TRIP_q4/clip01.mp4")
    for token in ("SENTINEL_TRIP_q4", "SENTINEL_PLACE_q4", "clip01"):
        assert token not in caplog.text, token


def test_all_four_tools_reach_the_model_session():
    """The locked profile must list them, or the model never sees them."""
    core_tools = importlib.import_module("reachy_companion.tools.core_tools")
    core_tools.initialize_tools(force=True)
    try:
        names = {spec["name"] for spec in core_tools.get_tool_specs()}
        assert {"nas_video_query", "play_nas_video", "nas_play_folder", "nas_skip"} <= names
    finally:
        core_tools._TOOLS_SIGNATURE = None
```

- [ ] **Step 2: Run the test to verify it fails**

```powershell
cd C:\Project\Reachy-mini\reachy_companion
..\.venv\Scripts\python.exe -m pytest tests/test_hanova_nas.py -q
```

Expected: collection error — `ModuleNotFoundError: No module named 'reachy_companion.hanova.nas'`.

- [ ] **Step 3: Implement `hanova/nas.py`**

Create `reachy_companion/src/reachy_companion/hanova/nas.py`:

```python
"""NAS home-video library: index queries, SMB staging, and cast session state.

Adapted from upstream `nasvideo/query.py` (pure filtering, ported nearly as-is)
and `nasvideo/smb.py` (rewritten). Three upstream dependencies are gone:

* `/opt/homebrew/bin/smbclient` wrapped in `gtimeout` (`nasvideo/smb.py:20-21`)
  becomes `smbprotocol`, which is a pure-Python pip wheel.
* Staging into Home Assistant's `www/` directory becomes the LAN-served media
  cache, because the Chromecast fetches the URL from its own network position.
* The index path and every NAS credential come from configuration; upstream read
  them from files under the operator's home directory.

**Auto-advance is deliberately not ported.** Upstream ran an unbounded 1 Hz
daemon polling Home Assistant forever and prefetching the next clip
(`server.py:1976-2058`). Here the session holds the trip playlist and its
position, and `nas_skip` advances it on request -- the same user-visible
capability with no background task to own or leak.
"""

from __future__ import annotations

import os
import json
import uuid
import shutil
import hashlib
import asyncio
import logging
import posixpath
import threading
from typing import Any, Dict, List
from pathlib import Path
from dataclasses import dataclass

from reachy_companion.hanova import redact, settings, media_store
from reachy_companion.hanova.confirm import GATE
from reachy_companion.hanova.ha_client import ha_run_script


logger = logging.getLogger(__name__)

_SESSION_LOCK = threading.Lock()
# `generation` (round 2, finding 11) is what a cursor token compares against. It
# is distinct from the confirmation epoch: a `nas_play_folder` inside one
# conversation starts a new *playlist* without starting a new conversation.
_SESSION: Dict[str, Any] = {"playlist": [], "index": -1, "epoch": "", "generation": 0}

# Round 2, finding 10: one lock per staged destination, so two callers racing for
# the same clip serialise instead of writing over each other.
_FETCH_LOCKS_LOCK = threading.Lock()
_FETCH_LOCKS: Dict[str, threading.Lock] = {}

# Finding 15: only these ever get served, and the served name always ends in one
# of them, whatever the index says.
ALLOWED_EXTENSIONS = frozenset({".mp4", ".m4v", ".mov"})

# Round 2, finding 10: re-exported from `media_store`, which owns it because
# `prune` has to skip these and the reverse import would be a cycle. The LRU
# therefore cannot delete a staging file out from under an active copy.
PART_SUFFIX = media_store.PART_SUFFIX


class NasError(RuntimeError):
    """The NAS could not be reached, or the file could not be copied."""


# Round 2, finding 6: the only NasError texts a caller may ever see. Forwarding
# the exception's own text relied on every message in this module staying
# path-free forever, which nothing enforced; an allow-list does enforce it, and
# a message added later without being listed degrades to the generic sentence
# rather than leaking whatever it happens to interpolate.
_NAS_MESSAGES = frozenset(
    {
        "the NAS host, credentials or share are not all configured",
        "that clip path is not a relative path inside the share",
        "that clip path escapes the configured folder",
        "that clip path is outside the configured folder",
        "that clip is not one of the playable video types",
        "the clip copied off the NAS was empty",
        "the video could not be copied from the NAS",
        "smbprotocol is not installed",
        "HANOVA_NAS_SUBPATH is not set.",
        "HANOVA_NAS_CAST_SUBPATH is not set.",
    }
)


def nas_message(exc: BaseException) -> str:
    """Return an allow-listed NasError sentence, or a generic fallback."""
    text = f"{exc}"
    return text if text in _NAS_MESSAGES else "that home video could not be prepared"


@dataclass(frozen=True)
class CursorToken:
    """A reservation on one advance of the trip playlist (round 2, finding 11).

    `generation` identifies the playlist this was taken against and
    `expected_index` the cursor position that was observed. `commit_next` moves
    the cursor only if both still hold, which is what stops two concurrent skips
    from consuming two clips for one request, and stops a cast that outlived its
    session from advancing somebody else's playlist.
    """

    generation: int
    expected_index: int
    next_index: int


# --- index -----------------------------------------------------------------
def load_index() -> Dict[str, Any] | None:
    """Read the operator-supplied video index, or None when it is absent."""
    path = settings.nas_index_path()
    if path is None or not path.is_file():
        return None
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        # Round 2, finding 6: the path is the instance directory and the
        # JSONDecodeError message quotes the offending line of the index --
        # which is a home-video filename.
        logger.warning("Could not read the NAS index: %s", redact.error(exc))
        return None
    return parsed if isinstance(parsed, dict) else None


def _hay(value: Any) -> str:
    if value is None:
        return ""
    return str(value).lower()


def filter_index(
    index: Dict[str, Any],
    year: int | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    place: str | None = None,
    keyword: str | None = None,
    limit: int | None = None,
) -> List[Dict[str, Any]]:
    """Filter the index in memory. Ground truth only -- never fabricate a record."""
    out: List[Dict[str, Any]] = []
    for video in index.get("videos", []):
        if year is not None and video.get("year") != year:
            continue
        if year_from is not None and (video.get("year") or 0) < year_from:
            continue
        if year_to is not None and (video.get("year") or 9999) > year_to:
            continue
        if place is not None:
            needle = _hay(place)
            haystacks = (video.get("place"), video.get("label"), video.get("top_folder"), video.get("country"))
            if not any(needle in _hay(field) for field in haystacks):
                continue
        if keyword is not None:
            needle = _hay(keyword)
            blob = " ".join(
                _hay(video.get(field)) for field in ("place", "label", "top_folder", "name", "country")
            )
            if needle not in blob:
                continue
        out.append(video)
    out.sort(
        key=lambda video: (
            video.get("year") or 0,
            str(video.get("top_folder") or ""),
            video.get("seq") if video.get("seq") is not None else 9999,
        )
    )
    return out[:limit] if limit else out


def summarize_folders(index: Dict[str, Any]) -> List[Dict[str, Any]]:
    """One row per top-level folder, for a "what do we have?" overview."""
    rows = [
        {
            "top_folder": name,
            "year": meta.get("year"),
            "place": meta.get("place"),
            "country": meta.get("country"),
            "count": meta.get("count"),
        }
        for name, meta in (index.get("folders") or {}).items()
    ]
    rows.sort(key=lambda row: (row["year"] or 0, str(row["place"] or "")))
    return rows


def folder_playlist(index: Dict[str, Any], top_folder: str) -> List[Dict[str, Any]]:
    """The cast-ready clips of one folder, in recorded order."""
    videos = [
        video
        for video in index.get("videos", [])
        if video.get("top_folder") == top_folder and video.get("cast_ready")
    ]
    videos.sort(
        key=lambda video: (
            video.get("seq") if video.get("seq") is not None else 9999,
            str(video.get("name") or ""),
        )
    )
    return videos


def video_title(video: Dict[str, Any]) -> str:
    """A short spoken title for one clip."""
    parts = [video.get("year"), video.get("place"), video.get("label")]
    return " ".join(str(part) for part in parts if part).strip()


def _validate_inside(raw_path: str, subpath: str, key_name: str) -> str:
    """Normalise a path and prove it resolves inside *subpath*. Raises NasError."""
    if not subpath:
        raise NasError(f"{key_name} is not set.")
    raw = str(raw_path or "").replace("\\", "/").strip()
    if not raw or raw.startswith("/") or ":" in raw:
        raise NasError("that clip path is not a relative path inside the share")
    normalised = posixpath.normpath(raw)
    root = posixpath.normpath(subpath)
    if normalised in (".", "..") or normalised.startswith("../"):
        raise NasError("that clip path escapes the configured folder")
    if normalised != root and not normalised.startswith(root + "/"):
        raise NasError("that clip path is outside the configured folder")
    if posixpath.splitext(normalised)[1].lower() not in ALLOWED_EXTENSIONS:
        raise NasError("that clip is not one of the playable video types")
    return normalised


def validate_cast_path(cast_path: str) -> str:
    """Normalise an index path and prove it stays inside the cast subtree.

    Review finding 15: an index entry went straight into an SMB path with no
    checking, so a bad -- or hostile -- index could reach anywhere on the share.
    The configured `HANOVA_NAS_CAST_SUBPATH` is now actually used, as the root
    the normalised path must resolve inside. Raises `NasError` on anything else.
    """
    return _validate_inside(cast_path, settings.nas_cast_subpath(), "HANOVA_NAS_CAST_SUBPATH")


def validate_source_path(path: str) -> str:
    """Prove an index entry's *original* path stays inside `HANOVA_NAS_SUBPATH`.

    Round 2, finding 12: that key was a mandatory prerequisite of all three
    casting tools and **nothing read it**, so a fresh deployment could be blocked
    on a value with no behaviour attached to it -- either a dead switch or a
    missing check, and it was the second. It is the same bound as
    `validate_cast_path`, applied to the field the index calls `path`.
    """
    return _validate_inside(path, settings.nas_subpath(), "HANOVA_NAS_SUBPATH")


def cast_filename(cast_path: str) -> str:
    """Derive one collision-free served filename from a validated path.

    Review finding 15: the old flatten-and-truncate mapped two different clips
    onto the same served name whenever their paths agreed for the first 150
    characters -- which, for `<trip>/<date>/<camera>/clipNN.mp4`, they routinely
    do. A digest of the *whole* validated path cannot collide that way, and it
    also stops the served name from leaking folder names onto the LAN.
    """
    validated = validate_cast_path(cast_path)
    extension = posixpath.splitext(validated)[1].lower()
    digest = hashlib.blake2s(validated.encode("utf-8"), digest_size=8).hexdigest()
    return f"{digest}{extension}"


# --- SMB -------------------------------------------------------------------
def _fetch_lock(destination: Path) -> threading.Lock:
    """Return the process-wide lock guarding one staged destination (finding 10).

    Two `play_nas_video` calls for the same clip used to open the same
    deterministic `.part` file with `"wb"` at the same time: the second
    truncated the first, and whichever finished first renamed a half-written
    file into place. An atomic `os.replace` does not help when both writers
    share the staging file. This makes the second caller wait and then find the
    clip already staged.
    """
    key = str(destination)
    with _FETCH_LOCKS_LOCK:
        lock = _FETCH_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _FETCH_LOCKS[key] = lock
        return lock


def fetch_cast_file(cast_path: str, destination: Path) -> None:
    """Copy one pre-transcoded MP4 off the NAS. Synchronous; raises NasError.

    The copy lands in a **uniquely named** private `.part` sibling and is renamed
    into place only when it is complete, so a Chromecast that fetches mid-copy
    gets a 404 rather than a truncated video (review finding 15), and two
    concurrent fetches cannot share a staging file (round 2, finding 10).

    The whole body runs under a per-destination single-flight lock, and re-checks
    the destination after acquiring it: the common concurrent case is "someone
    else already staged this", and that must cost one `stat`, not a second copy.
    """
    host = settings.nas_host()
    user = settings.nas_user()
    password = settings.nas_password()
    share = settings.nas_share()
    if not (host and user and password and share):
        raise NasError("the NAS host, credentials or share are not all configured")

    validated = validate_cast_path(cast_path)

    try:
        import smbclient
    except ImportError as exc:  # pragma: no cover - the wheel is a hard dependency
        raise NasError("smbprotocol is not installed") from exc

    remote = "\\\\" + host + "\\" + share + "\\" + validated.replace("/", "\\")
    with _fetch_lock(destination):
        if destination.is_file() and destination.stat().st_size > 0:
            # Another caller staged it while we waited for the lock.
            return
        # Round 2, finding 10: a unique name per attempt. Even without the lock
        # two writers could then not collide, and a crashed attempt leaves a
        # file that pruning skips and the next success does not depend on.
        partial = destination.with_name(f"{destination.name}.{uuid.uuid4().hex[:8]}{PART_SUFFIX}")
        try:
            smbclient.register_session(host, username=user, password=password)
            with smbclient.open_file(remote, mode="rb") as source:
                with open(partial, "wb") as target:
                    shutil.copyfileobj(source, target, length=1024 * 1024)
                    target.flush()
                    os.fsync(target.fileno())
            if partial.stat().st_size == 0:
                raise NasError("the clip copied off the NAS was empty")
            os.replace(partial, destination)
        except NasError:
            partial.unlink(missing_ok=True)
            raise
        except Exception as exc:  # noqa: BLE001 - smbprotocol raises a wide family
            partial.unlink(missing_ok=True)
            # Finding 7: the SMB error text carries the full share path.
            logger.warning("NAS copy failed: %s", redact.error(exc))
            raise NasError("the video could not be copied from the NAS") from exc


# --- staging + casting -----------------------------------------------------
async def stage_and_cast(video: Dict[str, Any], instance_path: str | Path | None) -> Dict[str, Any]:
    """Copy a clip into the LAN media cache (if needed) and cast its URL."""
    title = video_title(video)
    cast_path = str(video.get("cast_path") or "")
    if not cast_path:
        return {"ok": False, "url": None, "title": title, "error": "not_ready"}

    try:
        # Round 2, finding 12: `HANOVA_NAS_SUBPATH` is a prerequisite of this
        # tool, so it must bound something. It bounds the subtree an index
        # entry's original path may name -- the same guarantee `cast_path` has
        # had since finding 15, applied to the field that had none.
        validate_source_path(str(video.get("path") or ""))
        filename = cast_filename(cast_path)
    except NasError as exc:
        # A bad index entry is a configuration fault, not a user fault. Round 2,
        # finding 6: forwarding the exception's own text verbatim relied on every
        # NasError message *staying* path-free, which is an invariant nothing
        # enforced. `nas_message` allow-lists the fixed sentences instead.
        logger.warning("NAS index entry rejected: %s", redact.error(exc))
        return {"ok": False, "url": None, "title": title, "error": nas_message(exc)}

    nas_dir = media_store.media_dir("nas", instance_path)
    local = nas_dir / filename

    if not local.is_file() or local.stat().st_size == 0:
        try:
            await asyncio.to_thread(fetch_cast_file, cast_path, local)
        except NasError as exc:
            return {"ok": False, "url": None, "title": title, "error": nas_message(exc)}
    media_store.prune("nas", instance_path, settings.nas_cast_keep())

    url = media_store.media_url("nas", filename)
    if url is None:
        return {
            "ok": False,
            "url": None,
            "title": title,
            "error": "HANOVA_MEDIA_HTTP_BASE is not set; the TV has no URL to fetch.",
        }

    fields: Dict[str, Any] = {"url": url, "title": title}
    entity = settings.cast_entity()
    if entity:
        fields["entity_id"] = entity
    cast = await ha_run_script(settings.ha_script_video_url(), fields)
    if not cast["ok"]:
        logger.info("NAS cast failed: %s", redact.error(cast.get("error") or ""))
        return {"ok": False, "url": url, "title": title, "error": "the TV did not accept the video"}
    return {"ok": True, "url": url, "title": title, "error": None}


# --- session (conversation-scoped, review finding 16; token per round 2 #11) -
def start_session(playlist: List[Dict[str, Any]], index: int) -> None:
    """Remember the trip playlist, which clip is on screen, and whose it is.

    Round 2, finding 11: this also mints a new **session generation**. A cursor
    token taken against the old playlist is then refused by `commit_next`, so a
    cast that was still in flight when a new trip started cannot advance the new
    one.
    """
    with _SESSION_LOCK:
        _SESSION["playlist"] = list(playlist)
        _SESSION["index"] = index
        _SESSION["epoch"] = GATE.epoch()
        _SESSION["generation"] = int(_SESSION["generation"]) + 1


def clear_session() -> None:
    """Forget any trip playlist.

    Called on realtime session start and shutdown, and by every action that
    supersedes the trip on the TV or the speaker: `play_music`, `play_video`,
    `show_on_tv`, and a `play_nas_video` that resolves outside the current
    playlist (finding 16). The generation advances here too, so a token taken
    before the clear can never commit (round 2, finding 11).
    """
    with _SESSION_LOCK:
        _SESSION["playlist"] = []
        _SESSION["index"] = -1
        _SESSION["epoch"] = ""
        _SESSION["generation"] = int(_SESSION["generation"]) + 1


def _live_session() -> tuple[List[Dict[str, Any]], int]:
    """Return the playlist and cursor, or an empty session across an epoch.

    Callers must already hold `_SESSION_LOCK`.
    """
    playlist: List[Dict[str, Any]] = _SESSION["playlist"]
    position: int = _SESSION["index"]
    if not playlist or position < 0:
        return [], -1
    if _SESSION["epoch"] != GATE.epoch():
        # Finding 16: a trip from a previous conversation is not this one's.
        return [], -1
    return playlist, position


def peek_next() -> tuple[Dict[str, Any] | None, CursorToken | None, str | None]:
    """Reserve the next clip **without moving the cursor** (round 2, finding 11).

    Returns `(video, token, error)`. The token records which playlist generation
    and which cursor position this advance was computed against; `commit_next`
    refuses to move unless both still hold. Two concurrent skips therefore
    produce two tokens for the same index, and only the first can commit --
    which is what stops one user request from consuming two clips.
    """
    with _SESSION_LOCK:
        playlist, position = _live_session()
        if not playlist:
            return None, None, "nothing_playing"
        if position + 1 >= len(playlist):
            return None, None, "last_clip"
        token = CursorToken(
            generation=int(_SESSION["generation"]),
            expected_index=position,
            next_index=position + 1,
        )
        return playlist[position + 1], token, None


def commit_next(token: CursorToken) -> bool:
    """Compare-and-swap the cursor forward. Only after the cast succeeded.

    Round 2, finding 11: returns False and changes nothing when the playlist
    generation has moved on (a new trip, a clear, a superseding media action) or
    when the cursor is no longer where the token observed it (another skip won).
    """
    with _SESSION_LOCK:
        playlist, position = _live_session()
        if not playlist:
            return False
        if int(_SESSION["generation"]) != token.generation:
            logger.info("nas cursor: stale generation, refusing to advance")
            return False
        if position != token.expected_index:
            logger.info("nas cursor: another advance won, refusing to advance again")
            return False
        if token.next_index >= len(playlist):
            return False
        _SESSION["index"] = token.next_index
        return True


def remaining() -> int:
    """How many clips are left after the current one."""
    with _SESSION_LOCK:
        playlist, position = _live_session()
        if not playlist:
            return 0
        return max(0, len(playlist) - position - 1)
```

- [ ] **Step 4: Implement `tools/nas_video_query.py`**

Create `reachy_companion/src/reachy_companion/tools/nas_video_query.py`:

```python
"""Search the family home-video index (D-018, R4). Filename == Tool.name.

Read-only and entirely local -- no SMB, no network, no binary. It is still
house-bound, because everything it can lead to (casting a clip) is.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from reachy_companion.home_net import AWAY, HOME, home_state, home_unknown, away_from_home
from reachy_companion.hanova import nas, redact, settings
from reachy_companion.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)

_DEFAULT_LIMIT = 40


class NasVideoQuery(Tool):
    """Search the home-video library by year, place, or keyword."""

    name = "nas_video_query"
    description = "Search the family home-video library. 用於找以前拍的家庭影片。"
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "year": {"type": "integer", "description": "Exact year to match."},
            "year_from": {"type": "integer", "description": "Earliest year to include."},
            "year_to": {"type": "integer", "description": "Latest year to include."},
            "place": {"type": "string", "description": "Place or trip name."},
            "keyword": {"type": "string", "description": "Any text to search the records for."},
            "limit": {"type": "integer", "description": "Maximum clips to return. Default 40."},
        },
        "required": [],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Return matching clips, or a folder overview when no filter is given."""
        available, reason = settings.tool_status(self.name)
        if not available:
            return settings.unavailable(reason)
        # Finding 12 + round 2 finding 3: three verdicts, three branches. On
        # UNKNOWN nothing is read, staged or cast -- "I cannot tell where I am"
        # is not permission, and it is not absence either.
        verdict = await home_state()
        if verdict == AWAY:
            return away_from_home()
        if verdict != HOME:
            return home_unknown()

        index = nas.load_index()
        if index is None:
            # The prerequisite said the file exists; it is unreadable or malformed.
            return settings.unavailable("NAS_INDEX_FILE")

        filters = {key: kwargs.get(key) for key in ("year", "year_from", "year_to", "place", "keyword")}
        # Finding 7: the filters are the user's own words about their own family.
        logger.info(
            "Tool call: nas_video_query filters=%d",
            sum(1 for value in filters.values() if value not in (None, "")),
        )
        if not any(value is not None and str(value).strip() != "" for value in filters.values()):
            return {"ok": True, "folders": nas.summarize_folders(index)}

        try:
            limit = int(kwargs.get("limit") or _DEFAULT_LIMIT)
        except (TypeError, ValueError):
            limit = _DEFAULT_LIMIT

        videos = nas.filter_index(
            index,
            year=filters["year"],
            year_from=filters["year_from"],
            year_to=filters["year_to"],
            place=str(filters["place"]) if filters["place"] else None,
            keyword=str(filters["keyword"]) if filters["keyword"] else None,
            limit=max(1, min(200, limit)),
        )
        return {
            "ok": True,
            "count": len(videos),
            "videos": [
                {
                    "path": video.get("path"),
                    "title": nas.video_title(video),
                    "year": video.get("year"),
                    "place": video.get("place"),
                    "ready": bool(video.get("cast_ready")),
                }
                for video in videos
            ],
        }
```

- [ ] **Step 5: Implement `tools/play_nas_video.py` and `tools/nas_play_folder.py`**

Create `reachy_companion/src/reachy_companion/tools/play_nas_video.py`:

```python
"""Cast one home video to the TV (D-018, R2/R4/R6). Filename == Tool.name.

The clip is copied off the NAS into the LAN-served media cache and its URL is
cast, because a Chromecast dereferences the URL itself. The rest of the clip's
trip becomes the session playlist, so `nas_skip` can move through it.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from reachy_companion.home_net import AWAY, HOME, home_state, home_unknown, away_from_home
from reachy_companion.hanova import nas, redact, settings
from reachy_companion.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


class PlayNasVideo(Tool):
    """Play one home video on the TV."""

    name = "play_nas_video"
    description = "Play one family home video on the TV. 用於播放某一段家庭影片。"
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Exact clip path from nas_video_query. Preferred."},
            "year": {"type": "integer", "description": "Year, when no path is known."},
            "place": {"type": "string", "description": "Place or trip name, when no path is known."},
            "keyword": {"type": "string", "description": "Any text to narrow the match."},
        },
        "required": [],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Resolve one clip, stage it on the LAN, and cast it."""
        available, reason = settings.tool_status(self.name)
        if not available:
            return settings.unavailable(reason)
        # Finding 12 + round 2 finding 3: three verdicts, three branches. On
        # UNKNOWN nothing is read, staged or cast -- "I cannot tell where I am"
        # is not permission, and it is not absence either.
        verdict = await home_state()
        if verdict == AWAY:
            return away_from_home()
        if verdict != HOME:
            return home_unknown()

        index = nas.load_index()
        if index is None:
            # The prerequisite said the file exists; it is unreadable or malformed.
            return settings.unavailable("NAS_INDEX_FILE")

        path = str(kwargs.get("path") or "").strip()
        if path:
            matches = [video for video in index.get("videos", []) if video.get("path") == path]
        else:
            matches = nas.filter_index(
                index,
                year=kwargs.get("year"),
                place=str(kwargs.get("place")) if kwargs.get("place") else None,
                keyword=str(kwargs.get("keyword")) if kwargs.get("keyword") else None,
            )
        if not matches:
            return {"ok": False, "error": "no_match"}

        ready = [video for video in matches if video.get("cast_ready")]
        if not ready:
            return {"ok": False, "error": "not_ready"}
        video = ready[0]

        logger.info("Tool call: play_nas_video %s", redact.ident(video.get("path")))
        result = await nas.stage_and_cast(video, deps.instance_path)
        if not result["ok"]:
            # Finding 16: a failed play leaves whatever was on the TV alone, and
            # therefore leaves the trip session alone too.
            return {"ok": False, "error": result["error"]}

        playlist = nas.folder_playlist(index, str(video.get("top_folder") or ""))
        position = next(
            (i for i, item in enumerate(playlist) if item.get("path") == video.get("path")),
            -1,
        )
        if position >= 0:
            nas.start_session(playlist, position)
        else:
            nas.clear_session()

        return {
            "ok": True,
            "status": "casting",
            "title": result["title"],
            "ambiguous": len(ready) > 1,
            "remaining": nas.remaining(),
        }
```

Create `reachy_companion/src/reachy_companion/tools/nas_play_folder.py`:

```python
"""Play a whole home-video trip in order (D-018, R2/R4/R6). Filename == Tool.name."""

from __future__ import annotations

import logging
from typing import Any, Dict

from reachy_companion.home_net import AWAY, HOME, home_state, home_unknown, away_from_home
from reachy_companion.hanova import nas, redact, settings
from reachy_companion.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


class NasPlayFolder(Tool):
    """Start a whole trip folder from its first clip."""

    name = "nas_play_folder"
    description = "Play a whole home-video trip in order. 用於播整趟旅行的影片。"
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "top_folder": {"type": "string", "description": "Folder name from nas_video_query. Preferred."},
            "year": {"type": "integer", "description": "Year, when no folder name is known."},
            "place": {"type": "string", "description": "Place or trip name, when no folder name is known."},
        },
        "required": [],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Resolve a trip folder and cast its first clip."""
        available, reason = settings.tool_status(self.name)
        if not available:
            return settings.unavailable(reason)
        # Finding 12 + round 2 finding 3: three verdicts, three branches. On
        # UNKNOWN nothing is read, staged or cast -- "I cannot tell where I am"
        # is not permission, and it is not absence either.
        verdict = await home_state()
        if verdict == AWAY:
            return away_from_home()
        if verdict != HOME:
            return home_unknown()

        index = nas.load_index()
        if index is None:
            # The prerequisite said the file exists; it is unreadable or malformed.
            return settings.unavailable("NAS_INDEX_FILE")

        top_folder = str(kwargs.get("top_folder") or "").strip()
        if not top_folder:
            matches = nas.filter_index(
                index,
                year=kwargs.get("year"),
                place=str(kwargs.get("place")) if kwargs.get("place") else None,
            )
            folders = sorted({str(video.get("top_folder") or "") for video in matches if video.get("top_folder")})
            if not folders:
                return {"ok": False, "error": "no_match"}
            if len(folders) > 1:
                return {"ok": False, "error": "ambiguous", "candidates": folders}
            top_folder = folders[0]

        playlist = nas.folder_playlist(index, top_folder)
        if not playlist:
            return {"ok": False, "error": "no_match"}

        logger.info("Tool call: nas_play_folder %s (%d clips)", redact.ident(top_folder), len(playlist))
        result = await nas.stage_and_cast(playlist[0], deps.instance_path)
        if not result["ok"]:
            # Finding 16: nothing reached the TV, so no trip session is opened.
            return {"ok": False, "error": result["error"]}

        nas.start_session(playlist, 0)
        return {
            "ok": True,
            "status": "casting",
            "top_folder": top_folder,
            "title": result["title"],
            "remaining": nas.remaining(),
        }
```

- [ ] **Step 6: Implement `tools/nas_skip.py`**

Create `reachy_companion/src/reachy_companion/tools/nas_skip.py`:

```python
"""Move to the next clip in the trip being played (D-018, R2/R4). Filename == Tool.name.

Upstream advanced automatically from a 1 Hz polling daemon; that daemon is not
ported (see `hanova/nas.py`). This tool is the whole of the advance capability:
the user says "next one" and the session moves forward.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from reachy_companion.home_net import AWAY, HOME, home_state, home_unknown, away_from_home
from reachy_companion.hanova import nas, redact, settings
from reachy_companion.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


class NasSkip(Tool):
    """Play the next clip in the trip currently on the TV."""

    name = "nas_skip"
    description = "Skip to the next home video in this trip. 用於下一段、跳過。"
    parameters_schema: Dict[str, Any] = {"type": "object", "properties": {}, "required": []}

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Advance the trip session and cast the next clip."""
        available, reason = settings.tool_status(self.name)
        if not available:
            return settings.unavailable(reason)
        # Finding 12 + round 2 finding 3: three verdicts, three branches. On
        # UNKNOWN nothing is read, staged or cast -- "I cannot tell where I am"
        # is not permission, and it is not absence either.
        verdict = await home_state()
        if verdict == AWAY:
            return away_from_home()
        if verdict != HOME:
            return home_unknown()

        # Finding 16: look at the next clip without consuming it. A failed cast
        # must not silently eat a clip out of the trip.
        # Round 2, finding 11: the reservation now carries a token, so the
        # advance can only be committed against the same playlist generation and
        # the same cursor position it was computed from.
        video, token, error = nas.peek_next()
        if error is not None:
            return {"ok": False, "error": error}

        assert video is not None and token is not None
        logger.info("Tool call: nas_skip -> %s", redact.ident(video.get("path")))
        result = await nas.stage_and_cast(video, deps.instance_path)
        if not result["ok"]:
            return {"ok": False, "error": result["error"]}
        if not nas.commit_next(token):
            # Another skip won, or the trip was superseded while this clip was
            # staging. The clip is on the TV either way; the playlist position
            # belongs to whoever won, so this call does not claim it.
            logger.info("nas_skip: the cursor moved under this cast; not advancing again")
            return {"ok": True, "status": "casting", "title": result["title"], "remaining": nas.remaining()}
        return {"ok": True, "status": "casting", "title": result["title"], "remaining": nas.remaining()}
```

- [ ] **Step 6b: Clear the trip session on every superseding action (finding 16)**

A trip session that outlives what is actually on the TV makes `nas_skip` cast a
clip into the middle of something else. Five one-line additions:

1. `reachy_companion/src/reachy_companion/hanova/music_hooks.py` —
   `on_session_started()` and `on_session_shutdown()` each gain
   `nas.clear_session()` (imported locally inside the functions to keep
   `music_hooks` free of a hard import cycle with `nas`).
2. `tools/play_music.py` — after a successful `PLAYER.play(...)`, call
   `nas.clear_session()`. Music on the speaker does not stop the TV, but it does
   end the "we are watching the trip" context that `nas_skip` assumes.
3. `tools/play_video.py` and `tools/show_on_tv.py` — after a successful cast,
   call `nas.clear_session()`. Something else is now on the TV.

Each site gets the same one-line comment:

```python
        # D-018 / finding 16: this supersedes whatever trip was on the TV, so the
        # nas_skip playlist must not silently continue afterwards.
        nas.clear_session()
```

- [ ] **Step 7: Enable the four tools in the locked profile**

In `reachy_companion/profiles/_reachy_companion_locked_profile/profile.md`, add to `default_tools` immediately after `"mad_laugh",`:

```toml
  "mad_laugh",
  "nas_video_query",
  "play_nas_video",
  "nas_play_folder",
  "nas_skip",
```

- [ ] **Step 8: Run the test to verify it passes**

```powershell
cd C:\Project\Reachy-mini\reachy_companion
..\.venv\Scripts\python.exe -m pytest tests/test_hanova_nas.py -q
```

Expected: green — **48** test functions, two of which are parametrised (the
path-validation test seven ways, the tri-state house-gating test four ways):
**57 collected cases**. Record the exact number.

- [ ] **Step 9: Run the full gate**

```powershell
cd C:\Project\Reachy-mini\reachy_companion
..\.venv\Scripts\python.exe -m pytest -q
..\.venv\Scripts\python.exe -m ruff check src tests
..\.venv\Scripts\python.exe -m ruff format --check src tests
..\.venv\Scripts\python.exe -m mypy
```

Expected: pytest green (30 documented skips), ruff clean, mypy `Success: no issues found`.

- [ ] **Step 10: Commit**

```bash
cd /c/Project/Reachy-mini
git rev-parse --abbrev-ref HEAD   # must print feat/ha-nova-port (Task 0)
git add reachy_companion/src/reachy_companion/hanova/nas.py \
        reachy_companion/src/reachy_companion/hanova/music_hooks.py \
        reachy_companion/src/reachy_companion/tools/nas_video_query.py \
        reachy_companion/src/reachy_companion/tools/play_nas_video.py \
        reachy_companion/src/reachy_companion/tools/nas_play_folder.py \
        reachy_companion/src/reachy_companion/tools/nas_skip.py \
        reachy_companion/src/reachy_companion/tools/play_music.py \
        reachy_companion/src/reachy_companion/tools/play_video.py \
        reachy_companion/src/reachy_companion/tools/show_on_tv.py \
        reachy_companion/profiles/_reachy_companion_locked_profile/profile.md \
        reachy_companion/tests/test_hanova_nas.py
git commit -m "feat(hanova): NAS home-video query, staging over SMB, casting and skip"
```

---

### Task 14: Persona routing, documentation, decision record and the deploy ritual

Implements R9 and R10. All 22 tools now exist and are in `default_tools`; this task teaches the character how to *use* them, records the decision, and makes sure a redeploy cannot silently destroy the new credential files.

**Files:**
- Modify: `reachy_companion/profiles/_reachy_companion_locked_profile/profile.md` (instruction body: routing + result-convention lines)
- Modify: `persona.md` (repo-root, the git-tracked working copy of the instance persona — same behaviour, Traditional Chinese, matching the file's existing style)
- Modify: `docs/adding-a-skill.md` (a section on the ported families and the three result conventions)
- Modify: `DECISIONS.md` (append D-018)
- Modify: `docs/PRD.md` (§7 Functional Scope gains F-K; §12 System Architecture gains the hanova package)
- Modify: `progress.md` (current verified state)
- Modify: `feature_list.json` (one item per family)
- Modify: `.claude/skills/reachy-deploy/SKILL.md` (backup/restore ritual gains the three new instance files; **and the literal robot IP and SSH username currently parenthesised in that file are removed** — review finding 6 — replaced by "read from the repo-root `.env`", with the host-key fingerprint moving to a new gitignored `REACHY_HOSTKEY` key — finding 20)
- **Create**: `.env.example` (repo root) — round 2, finding 14. This file **does not exist** and is currently swallowed by the repo-root `.gitignore`'s `.env*` rule, so the round-1 instruction to "modify and stage" it could not have worked. It is created here with the complete four-key deployment template, placeholders only.
- **Modify**: `.gitignore` (repo root) — add `!/.env.example` so the new template can actually be tracked. **Round 3, finding 6: it goes AFTER every `.env`-matching pattern, i.e. at the end of the file** — the repo-root `.gitignore` already carries a later `.env.*` rule (line 9 today), and git honours the *last* matching pattern, so a negation placed under `.env` is silently re-ignored. Staged in this task's commit.
- Test: `reachy_companion/tests/test_hanova_integration.py`

**Interfaces:**
- Consumes: every tool from Tasks 4–13 and `hanova.settings` from Task 1.
- Produces: no new code interfaces. The test file is the durable artifact: it pins the full 22-tool inventory, the ≤120-char identifier-free description rule, and the `.env.example`-documents-every-key invariant, so a later change cannot quietly break any of them.

- [ ] **Step 1: Write the failing test**

Create `reachy_companion/tests/test_hanova_integration.py`:

```python
"""Whole-port invariants: inventory, descriptions, env docs, no leaked identifiers."""

import re
import importlib
import subprocess
from pathlib import Path

import pytest


PACKAGE_ROOT = Path(__file__).parents[1]
SRC_ROOT = PACKAGE_ROOT / "src" / "reachy_companion"
ENV_EXAMPLE = PACKAGE_ROOT / ".env.example"
PROFILE = PACKAGE_ROOT / "profiles" / "_reachy_companion_locked_profile" / "profile.md"

PORTED_TOOLS = frozenset(
    {
        "play_music",
        "stop_music",
        "play_video",
        "show_on_tv",
        "nas_video_query",
        "play_nas_video",
        "nas_play_folder",
        "nas_skip",
        "calendar_add",
        "calendar_delete",
        "calendar_list",
        "task_add",
        "task_complete",
        "task_delete",
        "task_list",
        "notion_add",
        "drive_list",
        "drive_trash",
        "drive_upload",
        "email_send",
        "self_destruct",
        "mad_laugh",
    }
)

GATED_TOOLS = frozenset(
    {
        "calendar_delete",
        "task_complete",
        "task_delete",
        "drive_trash",
        "drive_upload",
        "email_send",
        "self_destruct",
    }
)

HOUSE_BOUND_TOOLS = frozenset(
    {"play_video", "show_on_tv", "nas_video_query", "play_nas_video", "nas_play_folder", "nas_skip"}
)


@pytest.fixture
def specs():
    """Build the real registry once and hand back name -> spec."""
    core_tools = importlib.import_module("reachy_companion.tools.core_tools")
    core_tools.initialize_tools(force=True)
    try:
        yield {spec["name"]: spec for spec in core_tools.get_tool_specs()}
    finally:
        core_tools._TOOLS_SIGNATURE = None


def test_all_twenty_two_ported_tools_reach_the_model(specs):
    """R2: the inventory is 22 tools, and every one is in the session config."""
    assert len(PORTED_TOOLS) == 22
    missing = sorted(PORTED_TOOLS - set(specs))
    assert missing == [], f"not registered: {missing}"


def test_every_ported_description_is_short_and_identifier_free(specs):
    """R10: descriptions go into the model prompt and into every transcript."""
    for name in sorted(PORTED_TOOLS):
        description = specs[name]["description"]
        assert len(description) <= 120, f"{name} description is {len(description)} chars"
        assert "@" not in description, f"{name} description contains an address-like token"
        assert "media_player." not in description, f"{name} description contains an HA entity id"
        assert "/Users/" not in description


def test_every_gated_tool_exposes_a_confirm_flag(specs):
    """R3: the gate is part of the schema the model sees, not just the code."""
    for name in sorted(GATED_TOOLS):
        properties = specs[name]["parameters"]["properties"]
        assert "confirm" in properties, f"{name} has no confirm parameter"
        assert properties["confirm"]["type"] == "boolean"
        assert "confirm" not in specs[name]["parameters"].get("required", [])


def test_house_bound_tools_branch_all_three_home_verdicts():
    """R4 + finding 12 + round 2 finding 3: three verdicts, three branches.

    Round 1 only tested `AWAY`, so `UNKNOWN` fell straight through and the tool
    did the work anyway -- a VPN, a 401, a 5xx or a timeout could all still fire
    a real house action. The exact `if verdict != HOME:` line is asserted here so
    a future tool cannot quietly go back to a two-way branch.
    """
    for name in sorted(HOUSE_BOUND_TOOLS):
        source = (SRC_ROOT / "tools" / f"{name}.py").read_text(encoding="utf-8")
        assert "home_state" in source, name
        assert "verdict = await home_state()" in source, name
        assert "if verdict == AWAY:" in source, name
        assert "return away_from_home()" in source, name
        assert "if verdict != HOME:" in source, f"{name} does not branch on UNKNOWN"
        assert "return home_unknown()" in source, name
        # `away_from_home` is only ever returned for AWAY, so no tool may branch
        # on a bare boolean here -- and `is_home` no longer exists at all.
        assert "is_home" not in source, name


def test_the_boolean_home_shortcut_does_not_exist_anywhere():
    """Round 2, finding 3: a boolean cannot carry three verdicts."""
    from reachy_companion import home_net

    assert not hasattr(home_net, "is_home")
    for path in SRC_ROOT.rglob("*.py"):
        if "__pycache__" in str(path):
            continue
        assert "is_home(" not in path.read_text(encoding="utf-8"), path.name


def test_the_unknown_status_is_its_own_name():
    """Round 2, finding 3: `home_status_unknown` must never read as absence."""
    from reachy_companion import home_net

    payload = home_net.home_unknown()
    assert payload["status"] == "home_status_unknown"
    assert payload["status"] != "away_from_home"


def test_cloud_and_music_tools_never_consult_the_home_probe():
    """R4: probing from a coffee shop would add latency for no reason."""
    for name in sorted(PORTED_TOOLS - HOUSE_BOUND_TOOLS):
        source = (SRC_ROOT / "tools" / f"{name}.py").read_text(encoding="utf-8")
        assert "home_state" not in source, f"{name} must not consult the home-network probe"
        assert "is_home" not in source, f"{name} must not consult the home-network probe"


def test_every_ported_tool_gates_on_its_own_prerequisites():
    """Finding 10: availability is per tool, not per family."""
    from reachy_companion.hanova import settings as hanova_settings

    for name in sorted(PORTED_TOOLS):
        assert name in hanova_settings.TOOL_PREREQS, name
        source = (SRC_ROOT / "tools" / f"{name}.py").read_text(encoding="utf-8")
        if name == "stop_music":
            continue  # the documented exemption: no prerequisites at all
        assert "settings.tool_status(self.name)" in source, name
        assert "family_enabled(" not in source, f"{name} still uses the old family gate"


def test_every_gated_tool_threads_the_claim_id():
    """Finding 4 + round 2 finding 2: the claim id is what identifies *this*
    authorisation, and every mutator must present it.

    Without it, `complete("drive_trash")` spent whatever happened to be in the
    slot -- including an action a newer session had just armed.
    """
    for name in sorted(GATED_TOOLS):
        source = (SRC_ROOT / "tools" / f"{name}.py").read_text(encoding="utf-8")
        assert "GATE.claim(self.name)" in source, name
        assert "GATE.complete(self.name, pending.claim_id)" in source, name
        assert "GATE.take(" not in source, f"{name} still consumes the gate before executing"
        # The bare, id-less forms must not survive anywhere.
        assert "GATE.complete(self.name)" not in source, f"{name} completes without a claim id"
        assert "GATE.release(self.name)" not in source, f"{name} releases without a claim id"


def test_every_gated_tool_classifies_transient_and_terminal_failures():
    """Round 2, finding 9: `release()` is for transient faults only.

    Re-arming after an authentication failure or a refused recipient keeps an
    approval alive for something that can never succeed as approved.
    """
    for name in sorted(GATED_TOOLS):
        source = (SRC_ROOT / "tools" / f"{name}.py").read_text(encoding="utf-8")
        if name == "self_destruct":
            # The gag has exactly one failure mode -- the clip would not play --
            # and it is transient by construction (finding 17's scoped ruling).
            assert "GATE.release(self.name, pending.claim_id)" in source, name
            continue
        assert "is_transient(exc)" in source, f"{name} treats every failure the same way"
        assert "GATE.release(self.name, pending.claim_id)" in source, name
        assert '"retryable": True' in source, name


def test_the_gate_rejects_a_stale_claim_id():
    """Round 2, finding 2, at the module level rather than per tool."""
    from reachy_companion.hanova.confirm import GATE

    GATE.reset()
    GATE.begin_session()
    try:
        GATE.arm("drive_trash", "move 'a.txt' to Drive trash", {"file_id": "f1"})
        stale = GATE.claim("drive_trash")
        assert stale is not None
        GATE.begin_session()
        GATE.arm("drive_trash", "move 'b.txt' to Drive trash", {"file_id": "f2"})
        assert GATE.complete("drive_trash", stale.claim_id) is False
        assert GATE.release("drive_trash", stale.claim_id) is False
        live = GATE.claim("drive_trash")
        assert live is not None and live.payload == {"file_id": "f2"}
    finally:
        GATE.reset()


def test_re_arming_an_executing_action_is_refused():
    """Round 2, finding 2: a destructive action mid-flight is not re-armable."""
    from reachy_companion.hanova.confirm import GATE

    GATE.reset()
    GATE.begin_session()
    try:
        GATE.arm("calendar_delete", "delete 'Dentist'", {"event_id": "abc"})
        assert GATE.claim("calendar_delete") is not None
        assert GATE.arm("calendar_delete", "delete 'Optician'", {"event_id": "xyz"})["status"] == (
            "action_in_flight"
        )
    finally:
        GATE.reset()


def test_no_gated_tool_requires_its_action_field_in_the_schema():
    """Finding 4: the confirming call must not resupply the frozen field."""
    for name in sorted(GATED_TOOLS):
        module = importlib.import_module(f"reachy_companion.tools.{name}")
        tool_class = next(
            value
            for value in vars(module).values()
            if isinstance(value, type) and getattr(value, "name", None) == name
        )
        assert tool_class.parameters_schema.get("required", []) == [], name


def test_env_example_documents_every_setting_key():
    """R9: a key the code reads but nobody documents is a key nobody sets."""
    settings_source = (SRC_ROOT / "hanova" / "settings.py").read_text(encoding="utf-8")
    keys = set(re.findall(r'env_(?:str|int|float|path)\(\s*"([A-Z0-9_]+)"', settings_source))
    documented = ENV_EXAMPLE.read_text(encoding="utf-8")
    missing = sorted(key for key in keys if key not in documented)
    assert missing == [], f"undocumented in .env.example: {missing}"


def _scannable_paths():
    """Every tracked file this port writes, including tests, docs and skills.

    Round 2, finding 5: the previous version scanned `src/**/*.py` plus the
    profile plus one `.env.example`. That excluded the **tests** -- which is
    exactly where the round-1 leak was, a real private sentinel embedded as a
    needle -- and it excluded the documentation and the deploy skill, which are
    where connection identifiers actually lived.
    """
    repo_root = PACKAGE_ROOT.parent
    roots = [
        SRC_ROOT,
        PACKAGE_ROOT / "tests",
        PACKAGE_ROOT / "profiles",
        repo_root / "docs",
        repo_root / ".claude" / "skills",
    ]
    # `docs/superpowers/` holds the implementation plans and the external review
    # transcripts. Those quote findings verbatim -- including regex sources and
    # illustrative addresses -- so shape-matching them produces noise, not
    # signal. They are still covered by the Step 9b scan against the operator's
    # real values, which is the check that matters for them.
    excluded_prefixes = (repo_root / "docs" / "superpowers",)
    singles = [
        PROFILE,
        PACKAGE_ROOT / ".env.example",
        repo_root / ".env.example",
        repo_root / "persona.md",
        repo_root / "DECISIONS.md",
        repo_root / "progress.md",
        repo_root / "feature_list.json",
    ]
    seen: set[Path] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or "__pycache__" in str(path):
                continue
            if any(str(path).startswith(str(prefix)) for prefix in excluded_prefixes):
                continue
            if path.suffix in {".py", ".md", ".json", ".toml", ".example", ".txt"}:
                seen.add(path)
    for path in singles:
        if path.is_file():
            seen.add(path)
    return sorted(seen)


# Round 2, finding 5: the private-range IPv4 pattern in round 1 was
# `\b(?:10|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b` -- three
# octets after `10`, but only **two** after `192.168`, so an ordinary
# `192.168.1.50` did not match at all. Each alternative now names its own full
# four-octet shape.
_PRIVATE_IPV4 = re.compile(
    r"\b(?:"
    r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r"|100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}"   # CGNAT / Tailscale
    r"|169\.254\.\d{1,3}\.\d{1,3}"
    r")\b"
)


def test_the_private_ip_pattern_matches_a_normal_private_address():
    """Round 2, finding 5: the round-1 regex could not match a plain 192.168.x.y.

    It required three octets after `10` but only two after `192.168`, so the
    single commonest private address shape in the world slipped through the scan
    entirely. This pins the corrected pattern before anything relies on it.

    The addresses are **assembled from parts** on purpose: writing them as
    literals would plant private-address-shaped strings in this very file, which
    `test_no_personal_identifier_shape_is_committed_anywhere_this_port_writes`
    scans. A test for a leak detector must not be a leak.
    """
    private = [
        ".".join(parts)
        for parts in (
            ("10", "0", "0", "5"),
            ("192", "168", "1", "50"),
            ("172", "20", "10", "1"),
            ("100", "80", "3", "9"),      # CGNAT / Tailscale range
            ("169", "254", "1", "1"),     # link-local
        )
    ]
    for address in private:
        assert _PRIVATE_IPV4.search(address), address

    public = [".".join(parts) for parts in (("203", "0", "113", "20"), ("1", "2", "3", "4"), ("192", "169", "1", "1"))]
    for address in public:
        assert not _PRIVATE_IPV4.search(address), address


# Round 3, finding 4: the shapes, each with a label. The label is what a failure
# reports -- the matched text never is, because the matched text is the leak.
_FORBIDDEN_SHAPES = (
    ("gmail-address", re.compile(r"@gmail\.com")),
    ("google-calendar-id", re.compile(r"@group\.calendar\.google\.com")),
    ("macos-home-path", re.compile(r"/Users/[a-z]")),
    ("private-ipv4", _PRIVATE_IPV4),
    ("ha-media-player-entity", re.compile(r"\bmedia_player\.(?!example)[a-z0-9_]+\b")),
    ("aws-key-id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("ssh-host-key", re.compile(r"SHA256:[A-Za-z0-9+/=]{20,}")),
)

# The only files this port promises to leave **free** of these shapes. For them
# the tolerated count is zero. Everywhere else the bar is "no more than HEAD
# already had", because this port cannot be gated on identifiers that were
# committed before it started -- those are a separate, recorded clean-up.
_SCRUBBED_RELATIVE_PATHS = (
    ".claude/skills/reachy-deploy/SKILL.md",
    ".env.example",
    "reachy_companion/.env.example",
)

_ALLOWED_SHAPE_TOKENS = ("example.com", "example.invalid", "example_tv")


def _git_head_available() -> bool:
    """Is there a HEAD to compare against? (A tarball checkout has none.)"""
    try:
        done = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"],
            cwd=PACKAGE_ROOT.parent,
            capture_output=True,
            text=True,
        )
    except (OSError, ValueError):
        return False
    return done.returncode == 0


def _head_text(relative: str) -> str:
    """The file's content at HEAD, or "" when HEAD does not have that path."""
    done = subprocess.run(
        ["git", "show", f"HEAD:{relative}"],
        cwd=PACKAGE_ROOT.parent,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
    )
    return done.stdout if done.returncode == 0 else ""


def _shape_counts(text: str) -> dict[str, int]:
    """How many times each labelled shape occurs. Never returns the matches."""
    counts: dict[str, int] = {}
    for label, pattern in _FORBIDDEN_SHAPES:
        hits = sum(
            1
            for found in pattern.finditer(text)
            if not any(token in found.group(0) for token in _ALLOWED_SHAPE_TOKENS)
        )
        if hits:
            counts[label] = hits
    return counts


def test_no_personal_identifier_shape_is_committed_anywhere_this_port_writes():
    """The upstream repo leaked ~60 of these; this port must leak none.

    Review finding 6: the previous version of this test embedded a **real**
    private token as a needle, which put that identifier into the tracked
    repository -- inside the very test written to keep identifiers out of it.
    The needles are *shapes*, not values.

    Round 2, finding 5 widened the haystack: tests, docs, the profile directory
    and the deploy skill are all scanned now, and the private-address pattern
    actually matches private addresses.

    **Round 3, finding 4 made it executable.** As written it could not pass: it
    demanded zero matches everywhere, and several files in this repository
    already carried an identifier-shaped token before this port existed, so the
    gate failed on somebody else's history and would have been switched off. Two
    changes: the bar is now **"no more occurrences than `HEAD` already had"**
    except for the small set of files this port explicitly scrubs, where it stays
    zero; and a failure reports the **path, the shape label and the two counts**
    -- never the matched text, which was itself a disclosure into the terminal
    and into any transcript of the run.

    The check against the operator's **actual** values lives in the untracked
    scan in Step 9b, which reads them from the gitignored `.env` and the three
    private reports and never writes them anywhere.
    """
    if not _git_head_available():
        pytest.skip("no git HEAD to compare against; run this test in a checkout")

    repo_root = PACKAGE_ROOT.parent
    failures: list[str] = []
    tolerated: list[str] = []
    for path in _scannable_paths():
        relative = path.relative_to(repo_root).as_posix()
        now = _shape_counts(path.read_text(encoding="utf-8", errors="ignore"))
        if not now:
            continue
        scrubbed = relative in _SCRUBBED_RELATIVE_PATHS
        was = {} if scrubbed else _shape_counts(_head_text(relative))
        for label, count in sorted(now.items()):
            before = was.get(label, 0)
            if count > before:
                failures.append(f"{relative}: {label} x{count} (HEAD had {before})")
            else:
                tolerated.append(f"{relative}: {label} x{count} (unchanged from HEAD)")

    assert failures == [], (
        "identifier-shaped tokens introduced by this port (paths and counts only, "
        f"never the value): {failures}. Pre-existing and tolerated: {tolerated}"
    )


# Round 3, finding 4: a NAS fixture can be planted by ANY test file, not only by
# `test_hanova_nas.py`, and `monkeypatch.setenv` is written with either quote.
_NAS_FIXTURE_ASSIGNMENT = re.compile(
    r"""HANOVA_NAS_(?:SHARE|SUBPATH|CAST_SUBPATH)["']\s*,\s*["']([^"']*)["']"""
)


def test_the_nas_fixtures_are_obviously_synthetic():
    """Round 2, finding 5: no fixture may be copied from the private manifest.

    A reader has to be able to tell at a glance that a share or folder name in
    this repository is invented. `SENTINEL_*_q4` is not a plausible NAS layout.

    **Round 3, finding 4 fixed the scoping.** The positive half -- the sentinels
    exist -- belongs to `test_hanova_nas.py` and stays there. The negative half
    was scoped to that same file, which is exactly the file least likely to
    offend; and it was written as a double-quote-only lookahead, so a fixture set
    with single quotes slipped past it. It now covers **every** test file and
    both quoting styles, and it fails loudly if it matches nothing at all --
    a scan that silently scans nothing is the failure mode this round is about.
    """
    nas_tests = (PACKAGE_ROOT / "tests" / "test_hanova_nas.py").read_text(encoding="utf-8")
    for token in ("SENTINEL_SHARE_q4", "SENTINEL_SRC_DIR_q4", "SENTINEL_CAST_DIR_q4", "SENTINEL_TRIP_q4"):
        assert token in nas_tests, token

    checked = 0
    for path in sorted((PACKAGE_ROOT / "tests").rglob("test_*.py")):
        source = path.read_text(encoding="utf-8")
        for value in _NAS_FIXTURE_ASSIGNMENT.findall(source):
            checked += 1
            # The value is never echoed -- the file name is enough to find it.
            assert value == "" or value.startswith("SENTINEL"), (
                f"{path.name} sets a NAS fixture that is not a SENTINEL_* synthetic"
            )
        # And the shape a real manifest has: a four-digit-year trip folder.
        assert not re.search(r'"\d{4}_[A-Z][a-z]+/', source), (
            f"{path.name} carries a trip folder shaped like the operator's own"
        )
    assert checked > 0, "no NAS fixture assignment was found at all; the scan is scoped wrong"


def test_the_defaults_reveal_nothing_about_the_operators_setup():
    """Finding 6: a default IS a committed value. These must all be empty."""
    from reachy_companion.hanova import settings as hanova_settings

    for reader in (
        hanova_settings.nas_share,
        hanova_settings.nas_subpath,
        hanova_settings.nas_cast_subpath,
        hanova_settings.ha_script_youtube,
        hanova_settings.ha_script_image_url,
        hanova_settings.ha_script_video_url,
    ):
        assert reader() == "", f"{reader.__name__} must have no default"


def _new_service_and_tool_sources():
    """Every new module this port adds, service layer included.

    Round 2, finding 6: the round-1 version scanned `tools/*.py` only, so it
    could not see the raw paths and exception strings in `hanova/settings.py`,
    `media_store.py`, `ytdlp.py`, `images.py`, `nas.py` or `home_net.py` -- which
    is where the actual leaks were.
    """
    paths = sorted((SRC_ROOT / "hanova").rglob("*.py"))
    paths += [SRC_ROOT / "tools" / f"{name}.py" for name in sorted(PORTED_TOOLS)]
    paths += [SRC_ROOT / "home_net.py"]
    return [path for path in paths if path.is_file() and "__pycache__" not in str(path)]


def test_no_new_module_logs_or_returns_raw_content():
    """Finding 7 + round 2 finding 6: a grep-able guarantee across the port."""
    for path in _new_service_and_tool_sources():
        source = path.read_text(encoding="utf-8")
        if path.name == "redact.py":
            continue  # the helper itself handles the raw values
        for leaky in ("[:60]", "[:80]", "[:120]", "[:300]", "str(exc)", "%r"):
            assert leaky not in source, f"{path.name} logs or returns raw content ({leaky})"
        # Round 2, finding 6: a traceback quotes local variables and file paths,
        # so `logger.exception` is banned outright in new code.
        assert "logger.exception(" not in source, f"{path.name} logs a traceback"


# Modules that log but legitimately never touch user data, with the reason each
# one is exempt. Anything not on this list that logs must go through `redact`.
_REDACT_EXEMPT = {
    "redact.py": "it is the helper",
    "__init__.py": "package docstring only",
    "confirm.py": "logs tool names, TTLs and lifecycle transitions -- never summary or payload",
    "audio_drain.py": "logs durations and timeouts only",
    "music_hooks.py": "logs turn-phase transitions only",
    "music_player.py": "logs generation numbers, fixed daemon API paths and HTTP statuses only",
    "gauth.py": "one fixed line about forcing a token refresh; every caller-facing error goes through friendly_message",
    "stop_music.py": "logs the fixed string 'Tool call: stop_music' and takes no arguments",
    "mad_laugh.py": "logs the fixed string 'Tool call: mad_laugh' and takes no arguments",
    "self_destruct.py": "logs two fixed in-character lines; the arm summary is a module constant, not user text",
}


def test_every_new_module_that_logs_goes_through_the_redaction_helper():
    """Finding 6: importing `redact` is the minimum bar for a module that logs.

    The exemptions are enumerated with a reason rather than inferred, so adding a
    module that logs a title is a deliberate act someone has to write down.
    """
    for path in _new_service_and_tool_sources():
        source = path.read_text(encoding="utf-8")
        if path.name in _REDACT_EXEMPT:
            continue
        if "logger." not in source:
            continue
        assert "redact" in source, f"{path.name} logs without importing the redaction helper"


def test_the_redaction_exemptions_still_log_nothing_but_metadata():
    """An exemption is a claim, and this is the check that keeps it honest."""
    for name, _reason in _REDACT_EXEMPT.items():
        matches = [path for path in _new_service_and_tool_sources() if path.name == name]
        for path in matches:
            source = path.read_text(encoding="utf-8")
            for leaky in ("summary", "payload", "query", "title=", "subject", "recipient"):
                for line in source.splitlines():
                    if "logger." in line:
                        assert leaky not in line, f"{name} logs {leaky!r}: {line.strip()!r}"


def test_each_service_seam_has_a_caplog_sentinel_test():
    """Round 2, finding 6: the shape check needs a behavioural counterpart.

    A grep proves the helper is *mentioned*; only a caplog test proves nothing
    private reaches a record. One is required per new service seam.

    Round 3, finding 3 added the ninth: `ha_client` was the one service seam
    without a behavioural sentinel, and it is the seam whose log lines carry the
    operator's `scripts.yaml` entry names and the house's LAN address.
    """
    required = {
        "test_hanova_settings.py": "test_env_path_rejection_never_logs_the_path",
        "test_home_net.py": "test_the_probe_logs_no_address_and_no_url",
        "test_hanova_ha_client.py": "test_the_ha_client_logs_no_script_name_url_or_error_body",
        "test_hanova_media_store.py": "test_the_media_store_logs_no_path",
        "test_hanova_ytdlp.py": "test_the_ytdlp_layer_logs_no_query_url_or_stderr",
        "test_hanova_cast.py": "test_the_images_layer_logs_no_prompt_or_path",
        "test_hanova_nas.py": "test_nas_logs_never_carry_a_clip_path",
        "test_hanova_email.py": "test_email_logs_never_carry_an_address_or_a_subject",
        "test_hanova_confirm.py": "test_gate_logs_no_summary_and_no_payload",
    }
    for filename, test_name in required.items():
        source = (PACKAGE_ROOT / "tests" / filename).read_text(encoding="utf-8")
        assert f"def {test_name}(" in source, f"{filename} is missing {test_name}"


ROUTING_TOKENS = (
    "away_from_home",
    "home_status_unknown",   # round 2, finding 3: renamed, and its own status
    "needs_confirmation",
    "unavailable",
    "retryable",
    "action_in_flight",      # round 2, finding 2
    "body_too_long",         # round 2, finding 4
    "play_music",
    "nas_skip",
)


def test_profile_teaches_the_result_conventions():
    """R4/R10: the persona must know what each result status means."""
    body = PROFILE.read_text(encoding="utf-8")
    for token in ROUTING_TOKENS:
        assert token in body, f"profile.md does not mention {token}"


def test_repo_persona_teaches_the_same_conventions():
    """persona.md is the copy that actually runs on the robot after a deploy."""
    body = (PACKAGE_ROOT.parent / "persona.md").read_text(encoding="utf-8")
    for token in ROUTING_TOKENS:
        assert token in body, f"persona.md does not mention {token}"


def test_the_persona_records_the_two_approved_non_goals():
    """Finding 17: a dropped capability the character cannot explain is a bug."""
    body = (PACKAGE_ROOT.parent / "persona.md").read_text(encoding="utf-8")
    assert "drive" in body.lower() and "還原" in body, "Drive restore must be explained, not attempted"
    assert "密件" in body or "bcc" in body.lower(), "BCC must be explained, not silently dropped"


def test_the_self_destruct_ritual_is_not_explained_away():
    """Finding 17: the persona must not spoil the gag before running it."""
    body = (PACKAGE_ROOT.parent / "persona.md").read_text(encoding="utf-8")
    profile = PROFILE.read_text(encoding="utf-8")
    for text in (body, profile):
        window = text[text.find("self_destruct") : text.find("self_destruct") + 500]
        for spoiler in ("玩笑", "只是聲音", "假的"):
            assert spoiler not in window, f"the persona explains the gag: {spoiler}"


def test_deploy_skill_backs_up_the_new_instance_files():
    """R9: a redeploy that eats an OAuth file is silent, expensive data loss."""
    skill = (PACKAGE_ROOT.parent / ".claude" / "skills" / "reachy-deploy" / "SKILL.md").read_text(encoding="utf-8")
    for token in ("google-workspace-mcp", "google-oauth.json", "nas-video-index.json"):
        assert token in skill, f"the deploy skill does not back up {token}"


def test_the_deploy_skill_holds_no_connection_identifiers():
    """Finding 6: the skill file parenthesised the robot's real IP and username.

    They now come from the gitignored repo-root `.env` at run time. This test is
    shape-based for the same reason as the package scan above.

    Round 3, finding 4: it reuses `_PRIVATE_IPV4` rather than carrying its own
    copy of the round-1 regex, which required three octets after `10` but only
    two after `192.168` and therefore could not match the commonest private
    address shape in the world. The deploy skill is one of the explicitly
    scrubbed paths, so the bar here is zero, not "no worse than HEAD".
    """
    skill_path = PACKAGE_ROOT.parent / ".claude" / "skills" / "reachy-deploy" / "SKILL.md"
    skill = skill_path.read_text(encoding="utf-8")
    assert not _PRIVATE_IPV4.search(skill), "the deploy skill still contains a private LAN address"
    assert "REACHY_HOST" in skill and "REACHY_SSH_USER" in skill
    assert "REACHY_HOSTKEY" in skill, "the host-key fingerprint must be an .env key, not a literal"
    fingerprint_like = re.compile(r"SHA256:[A-Za-z0-9+/=]{20,}")
    assert not fingerprint_like.search(skill), "the host-key fingerprint is still committed"


def test_the_env_keys_the_deploy_uses_are_all_gitignored():
    """Finding 20: REACHY_HOSTKEY joins the other secrets in the root .env."""
    gitignore = (PACKAGE_ROOT.parent / ".gitignore").read_text(encoding="utf-8")
    lines = [line.strip() for line in gitignore.splitlines()]
    assert any(line in {".env", "/.env", ".env*"} for line in lines), "the root .env must be ignored"


def test_the_root_env_example_exists_and_is_tracked():
    """Round 2, finding 14: the round-1 version was conditional on a file that
    does not exist, and that the .gitignore would have swallowed anyway.

    `if example.is_file():` made the whole check vacuous on the exact repository
    it was written for. The file is now created in Step 9a, `!/.env.example` is
    added to `.gitignore` so git can actually track it, and its existence is a
    hard requirement here.

    Round 3, finding 6: the negation's **position** is the thing that decides
    whether it works. This repository's `.gitignore` carries `.env` early and
    `.env.*` later, and git honours the last matching pattern -- so a negation
    sitting under `.env` is re-ignored by the rule below it and the template is
    tracked by nothing. The ordering is asserted here, not just the presence.
    """
    gitignore = (PACKAGE_ROOT.parent / ".gitignore").read_text(encoding="utf-8")
    lines = [line.strip() for line in gitignore.splitlines()]
    assert "!/.env.example" in lines, "the template is ignored by the .env* rule without this"

    negation = lines.index("!/.env.example")
    env_rules = [
        index
        for index, line in enumerate(lines)
        if re.fullmatch(r"/?\.env(\*|\..*)?", line)
    ]
    assert env_rules, "no .env ignore rule at all -- this test is scoped wrong"
    assert negation > max(env_rules), (
        "!/.env.example must come after EVERY .env pattern; git honours the last match "
        f"(negation at line {negation + 1}, last .env rule at line {max(env_rules) + 1})"
    )

    example = PACKAGE_ROOT.parent / ".env.example"
    assert example.is_file(), "the repo-root .env.example must exist, not merely be planned"
    body = example.read_text(encoding="utf-8")
    for key in ("REACHY_HOST", "REACHY_SSH_USER", "REACHY_SSH_PASSWORD", "REACHY_HOSTKEY"):
        assert f"{key}=" in body, f"{key} is undocumented in the deployment template"


def test_the_root_env_example_carries_no_real_value():
    """A template that ships a real host is worse than no template."""
    example = (PACKAGE_ROOT.parent / ".env.example").read_text(encoding="utf-8")
    for line in example.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        _key, _, value = stripped.partition("=")
        assert value == "" or value.startswith("<"), f"{stripped!r} looks like a real value"
```

- [ ] **Step 2: Run the test to verify it fails**

```powershell
cd C:\Project\Reachy-mini\reachy_companion
..\.venv\Scripts\python.exe -m pytest tests/test_hanova_integration.py -q
```

Expected failures: `test_profile_teaches_the_result_conventions`,
`test_repo_persona_teaches_the_same_conventions`,
`test_the_persona_records_the_two_approved_non_goals`,
`test_deploy_skill_backs_up_the_new_instance_files`,
`test_the_deploy_skill_holds_no_connection_identifiers`,
`test_the_env_keys_the_deploy_uses_are_all_gitignored`, and — round 2,
finding 14 — `test_the_root_env_example_exists_and_is_tracked` and
`test_the_root_env_example_carries_no_real_value`, because the repo-root
`.env.example` **does not exist yet** and the `.gitignore` would swallow it if it
did. Step 9a creates both the negation rule and the file.

The inventory, description, gate-shape, claim-id, transient/terminal,
prerequisite, home-probe, redaction and env-doc tests should already pass from
Tasks 1–13; if any of those fail, fix that task's output before continuing rather
than patching it here.

- [ ] **Step 3: Add the routing and result-convention lines to the locked profile**

In `reachy_companion/profiles/_reachy_companion_locked_profile/profile.md`, append these lines to the end of the instruction body (after the existing `go_to_sleep` line), keeping the file's Simplified-Chinese style:

```markdown
- 被要求放音乐、播歌时用 play_music，音乐会从你自己的喇叭放出来；要停下来就用 stop_music。
- 要在电视上看影片用 play_video，要在电视上看图用 show_on_tv。
- 家里的旧家庭影片：先用 nas_video_query 找，再用 play_nas_video 播一段，或用 nas_play_folder 播一整趟旅行；对方说「下一段」时用 nas_skip。
- 行程用 calendar_add / calendar_list / calendar_delete，待办用 task_add / task_list / task_complete / task_delete，笔记用 notion_add，云端文件用 drive_list / drive_trash / drive_upload，寄信用 email_send。
- 工具回覆 away_from_home 时，表示你现在不在家里的网络上。自然地说你人不在家，暂时碰不到家里的东西，回家再处理。绝对不要说家里坏了、电视坏了或设备有问题。
- 工具回覆 home_status_unknown 时，表示你自己也不确定是不是在家：家里那台系统现在没有正常回应。就说「我现在不确定是不是在家，家里的系统没回应」，等一下再试。绝对不要说对方不在家，那是 away_from_home 才代表的意思。
- 工具回覆 needs_confirmation 时，把 summary 里的内容一字不漏地念给对方确认——是哪一件事、哪一天、寄给谁、有没有副本、主旨是什么。寄信时 summary 里带的是整封信的内文，要整段完整念出来，不可以摘要、浓缩或只念第一句；内文长的话先说「内容有点长，我念一遍」再照念。对方明确答应之后，才再呼叫同一个工具并带上 confirm，而且不用重复填其他栏位。对方没有明确答应就绝对不要带 confirm。
- 工具回覆 unavailable 时，表示这项功能还没设定好；reason 是缺的设定项名称。坦白说你现在没有这个能力，不要假装做过了。
- 工具回覆 retryable 为 true 时，是暂时性失败。可以直接再确认一次重试，不用从头再念一遍。没有 retryable 的失败不能直接重试，要把改正后的内容重新念一遍再请对方答应。
- 工具回覆 action_in_flight 时，表示那件事还在执行中：不要重念、不要再确认，也不要当成完成了。
- 工具回覆 body_too_long 时，表示信的内文超过你念得完的长度。说内容太长、请对方讲短一点，绝对不要自己缩写后送出。
- self_destruct 是角色扮演的倒数仪式：照 summary 念出来，不要事先解释这是玩笑或说明会发生什么。对方说取消时，用 abort 呼叫。
- 寄信只能寄给看得见的收件人和副本，没有密件副本。被要求密件副本时，说明你只能寄给大家都看得到的收件人。
- 云端硬盘丢到垃圾桶之后没有办法用语音还原；请对方自己到 Drive 的垃圾桶还原。
```

- [ ] **Step 4: Add the same behaviour to `persona.md` — the only patch in the file**

Round 2, finding 7: the user's pre-existing `persona.md` change was **stashed in
Task 0, Step 2**, so the working-tree copy is currently identical to
`HEAD:persona.md`. That is what makes this step mechanical instead of a judgement
call: whatever `git diff persona.md` shows after this edit **is** the port patch,
and `git add persona.md` stages exactly it.

Confirm that before editing — a non-empty diff here means the stash was lost:

```powershell
cd C:\Project\Reachy-mini
git stash list                # the "hanova-port: user persona.md baseline" entry must still be there
git diff --stat -- persona.md # must print NOTHING before this step's edit
```

If either check fails, **STOP** and recover the stash before touching the file;
editing on top of the user's un-stashed change is how their work gets committed
into this port's history.

Our block goes at the end of the existing `## Tools` section, immediately before
the `## Core Rule` heading.

In `C:\Project\Reachy-mini\persona.md`, add these `###` blocks (Traditional Chinese, matching the file's style):

```markdown
### play_music / stop_music
使用者要求放音樂、播歌時使用 `play_music`；音樂會從你自己的喇叭放出來。
要停下來時使用 `stop_music`。

### play_video / show_on_tv
要在客廳電視上看影片時使用 `play_video`；要在電視上看一張圖時使用 `show_on_tv`。

### nas_video_query / play_nas_video / nas_play_folder / nas_skip
家裡的舊家庭影片：先用 `nas_video_query` 找，再用 `play_nas_video` 播一段，
或用 `nas_play_folder` 播一整趟旅行。使用者說「下一段」時使用 `nas_skip`。

### calendar / task / notion / drive / email
行程用 `calendar_add`、`calendar_list`、`calendar_delete`。
待辦用 `task_add`、`task_list`、`task_complete`、`task_delete`。
筆記用 `notion_add`。雲端檔案用 `drive_list`、`drive_trash`、`drive_upload`。
寄信用 `email_send`。

### self_destruct
這是一段角色扮演的自毀倒數儀式。呼叫之後照 `summary` 念出來就好，
**不要**事先解釋這是什麼、會發生什麼、或說這只是開玩笑。
對方授權時帶 `confirm`；對方喊停時帶 `abort`。

## Tool Result Conventions

### away_from_home
工具回覆 `away_from_home` 表示你現在不在家裡的網路上。
自然地說你人不在家、暫時碰不到家裡的東西，回家再處理。
**絕對不要**說家裡壞了、電視壞了或設備有問題。

### home_status_unknown
工具回覆 `home_status_unknown` 表示你自己也不確定現在是不是在家：
家裡那台系統沒有正常回應，所以你無法判斷。
就直接說「我現在不確定是不是在家，家裡的系統沒有回應」，等一下再試。
**不要**說對方不在家——那是 `away_from_home` 才代表的意思。

### needs_confirmation
工具回覆 `needs_confirmation` 時，把 `summary` 的內容**一字不漏**地念出來讓對方確認
——是哪一件事、哪一天、寄給誰、有沒有副本、主旨是什麼。
寄信時 `summary` 裡會有**整封信的內文**：把它**整段完整念出來**，
不要摘要、不要濃縮、不要只念第一句，也不要說「內容大概是……」。
內文比較長的話，就先說「內容有點長，我念一遍」，然後照念。
對方明確答應之後，才再呼叫同一個工具並帶上 `confirm`；
這時候**不用**重新填其他欄位，系統會用剛剛念過的那一份。
對方沒有明確答應，就**絕對不要**自己帶上 `confirm`。

### unavailable
工具回覆 `unavailable` 表示這項功能還沒設定好，`reason` 是缺少的設定項名稱。
坦白說你現在沒有這個能力，不要假裝做過了。

### retryable
工具回覆裡 `retryable` 是 `true` 時，代表只是暫時性失敗。
可以直接再問一次要不要重試，不用從頭再念一遍。
沒有 `retryable` 的失敗**不能**直接重試：那一份授權已經用掉了，
要先把改正後的內容重新念一遍，對方再答應一次。

### action_in_flight
工具回覆 `action_in_flight` 表示剛剛那件事**還在執行中**。
不要重念一次、不要再確認一次，也不要當成已經完成。
就說還在處理，等它回報結果。

### body_too_long
工具回覆 `body_too_long` 表示信的內文太長，超過念得完的長度（`max_chars`）。
說內容太長、你沒辦法整段念出來確認，請對方講短一點——
**不要**自己縮寫之後就送出。

## What Reachy Cannot Do (D-018 approved non-goals)

### 密件副本（BCC）
寄信只能寄給看得見的收件人和副本。
被要求加密件副本時，說明你只能寄給大家都看得到的人，請對方改用副本。

### Drive 還原
`drive_trash` 丟到垃圾桶之後，你沒有辦法用語音還原。
請對方自己到 Google Drive 的垃圾桶把檔案還原回來（大約保留三十天）。
```

- [ ] **Step 5: Document the ported families in `docs/adding-a-skill.md`**

Append this section to the end of `C:\Project\Reachy-mini\docs\adding-a-skill.md`:

```markdown
## The ported HomeAssistant-Nova families (D-018)

Twenty-two of the app's Skills are ports of the operator's `ha-actions` MCP
server, and they follow three extra conventions on top of everything above.
Read `src/reachy_companion/tools/calendar_delete.py` for a worked example of all
three at once.

**1. Per-tool gating, per-family reporting.** Every ported tool declares its own
prerequisites in `settings.TOOL_PREREQS` and starts with
`settings.tool_status(self.name)`; a tool that is not configured returns
`settings.unavailable(reason)`, where *reason* is the name of the first missing
**config key** — never its value. Families aggregate their tools into one
tri-state startup line: `hanova family notion: disabled
(HANOVA_NOTION_TOKEN)` / `partial (2/4 tools ready; ...)` / `enabled`. The app
boots green with none of this configured. `stop_music` is the one documented
exemption: it has no prerequisites, because a robot that cannot be silenced by
voice is a safety defect.

**2. House-bound tools check the network, and the verdict is tri-state.**
Anything that needs Home Assistant or the NAS calls `await home_state()` from
`home_net.py` first and branches **all three** verdicts:

```python
verdict = await home_state()
if verdict == AWAY:
    return away_from_home()
if verdict != HOME:
    return home_unknown()      # status: home_status_unknown -- and do NO work
```

`AWAY` requires **positive off-home routing evidence**: the robot's own address
outside every network in `HANOVA_HOME_NETWORKS`. A failed connection to Home
Assistant is not that — it is what an outage looks like from inside the house —
and neither is a 401, a 5xx, an HTTP timeout or a VPN-shaped route. All of those
are `UNKNOWN`, which returns `home_status_unknown` and performs nothing: telling
the user they are out of their own house on that evidence is a lie, and doing the
house action anyway is worse. Cloud tools and music must **not** call it; a
needless probe is pure latency. `tests/test_hanova_integration.py` enforces both
directions and the exact three-way shape.

**3. Destructive tools are gated in code, and the authorisation is spent on
success.** A gated tool called without `confirm: true` resolves the action,
parks it in `hanova/confirm.py`'s `GATE` with a 90 s TTL **stamped with the
current session epoch**, and returns
`{"status": "needs_confirmation", "summary": "<the exact action, in full>"}`.
The confirming call carries only `confirm: true` — the action fields are
optional in the schema so the model cannot mis-hear them a second time — and it
executes the **parked** payload. `GATE.claim()` takes it in flight and returns a
`PendingAction` carrying an opaque **`claim_id`**; every mutator requires that
id, so a call can only spend, retry or cancel *the authorisation it actually
holds*. Then: `GATE.complete(name, claim_id)` on success **and on a terminal
failure** (an auth error, a refused recipient, a validation error — the approved
action cannot succeed as approved, so it must be read back afresh),
`GATE.release(name, claim_id)` only on a **transient** failure so the user can
say "try again", and `GATE.abort()` when they say no. Re-arming a slot whose
action is mid-execution is refused with `action_in_flight`. A new realtime
session mints a new epoch, so nothing armed in one conversation can be confirmed
in another. The gate is never a prompt instruction alone.

**4. Nothing personal reaches a log line or an error string.** Ported tools log
through `hanova/redact.py`: counts, lengths, salted digests, HTTP statuses,
durations. Never a query, title, subject, recipient, id, path or URL, and never
a raw API error body — Google, Notion, Drive and SMTP all echo the request back
inside theirs. A tool's user-facing `error` is a fixed, speakable sentence, not
the upstream message. (Scope note: this covers the ported tool surface. The
framework's own model-visible tool-result logging is unchanged by D-018.)

Adding a tool to one of these families is still one file plus one profile line;
it just also needs an entry in `TOOL_PREREQS`, possibly a home check, redacted
logging, and — if it destroys anything — a `confirm` parameter and the
claim/complete two-branch body.
```

- [ ] **Step 6: Record D-018 in `DECISIONS.md`**

Append this to the end of `C:\Project\Reachy-mini\DECISIONS.md`:

```markdown
## D-018 — HomeAssistant-Nova ported natively, not consumed over MCP (2026-08-21)

The operator's `ha-actions` server exposes 23 tools we wanted. It is a
single-file, stdio-only, hand-rolled JSON-RPC server pinned to one Mac
(`reference/HomeAssistant-Nova/bin/ha-actions-mcp/server.py`). Three
alternatives were weighed and the port won on four independent counts.

**Identifiers.** Thirteen of its 23 tool descriptions embed the operator's real
calendar address, Drive folder id, Gmail address, task-list names and family
trip place names, and ~60 code sites carry the same. Consuming it verbatim would
push all of that into the realtime model prompt on every session, and therefore
into transcripts and logs. Porting externalises every identifier to configuration
once. `tests/test_hanova_integration.py` now fails if any of it comes back.

**Concurrency.** Upstream's handlers are synchronous inside a single-threaded
stdin loop (`server.py:2364`, `:2390`), so one 200-second `play_music_here` or
one 600-second `drive_upload` makes `stop_music` unanswerable. On a voice robot
that is a safety defect: a robot that cannot be stopped by voice. Our realtime
loop already dispatches every tool as its own asyncio task
(`huggingface_realtime.py:1011`), so a native port gets a fast stop lane for
free — and upstream itself hand-rolls daemon threads to dodge its own loop,
which is the argument that the concurrency model belongs to the host app.

**Prompt budget.** 32.5 KB of tool descriptions, ~8–9 K tokens, written as
routing rules for a *different* agent with sibling tools we do not have. Rewritten
fresh at ≤120 characters each, the whole catalogue is roughly 2 KB.

**Transport.** Our MCP client speaks Streamable HTTP only
(`mcp_client.py:133-141`); stdio would need a new transport lane *plus* a way to
reach another machine's process, and the tools would still need `yt-dlp`,
`ffmpeg`, an SMB client and the credential files on the robot regardless.

### What the port changed on purpose

- **`play_music` always plays on the robot's own speaker.** The Voice-PE and
  TV-cast music paths are not ported and `play_music_here` is merged away: a desk
  robot asked for music is asked for *its* music, and that path needs no Home
  Assistant, no LAN URL and no home network.
- **Pause is synthesised.** The daemon media API is exactly `play_sound(file)`
  and `stop_sound()` (`daemon/app/routers/media.py:77-115`) — no pause, no seek,
  no per-stream volume; `/api/volume/set` is system-wide and plays a test beep.
  So barge-in stops the sound and banks the offset, and the turn's end re-cuts
  the cached mp3 from that offset with the bundled ffmpeg.
- **`show_on_tv` generates its own image.** Upstream's version depended on the
  operator's Hermes gateway and a read-only mount of its image cache. We call the
  OpenAI Images API with the key the app already has, serve the PNG from the
  app's own web server, and cast that URL.
- **`drive_upload` uploads a camera frame.** Upstream took an absolute path on
  the operator's Mac. The only file a robot can meaningfully offer is one it just
  produced, so it captures one frame — at confirm time, not at arm time — and
  uploads that. Never anyone-with-link readable, reversing upstream's default.
- **NAS auto-advance is not ported.** Upstream ran an unbounded 1 Hz daemon
  polling Home Assistant and prefetching (`server.py:1976-2058`). The session
  keeps the trip playlist and its position; `nas_skip` advances it on request.
  Same user-visible capability, no background task to own, cancel or leak.
- **Media is served from the app's own web server.** `console.py` already mounts
  `StaticFiles` on a FastAPI app bound to `0.0.0.0:7860`; a second mount at
  `/hanova-media` was enough. No new port, no stdlib server.
- **Notion's `Owner` property is dropped.** Its select options are real people's
  names, which have no place in a schema that enters the model prompt.
- **`email_send` is included but gated.** It was recommended for exclusion. The
  operator kept it; the mitigation is that the read-back names **every**
  recipient (To and CC), the subject and a body preview plus digest, and the send
  executes the parked envelope, never the second call's args.

### Approved non-goals (external review round 1, 2026-08-21)

Three upstream behaviours are deliberately **not** ported. Each was raised by the
external reviewer as an undeclared scope change; the controller accepted them as
scope decisions and they are recorded here so they cannot be mistaken later for
oversights.

- **Drive restore.** Upstream could untrash. `drive_trash` already leaves the
  item recoverable from the Drive UI for about thirty days, on any device,
  without the robot. A voice-driven restore would add a second fuzzy match over
  the *trash* namespace — precisely where duplicate names accumulate — for a
  capability the user already has. `gdrive.set_trashed` keeps its boolean because
  it is one API call either way, but no tool may expose `trashed=False`.
- **Email BCC.** Upstream accepted a blind-carbon list. This port supports To and
  CC only, and `send_mail` has no `bcc` parameter at all. A blind recipient is by
  definition one the confirmation read-back cannot surface, which contradicts the
  reason the gate exists. The persona explains this rather than failing silently.
- **The self-destruct gag keeps its in-character ritual.** It uses the shared
  `ConfirmationGate` for its TTL, its claim/complete lifecycle and its explicit
  abort path, but **not** the generic read-back summary: spelling out "this is a
  joke that plays a loud noise and nothing else" destroys the only thing the tool
  does. Nothing destructive is at stake — it is audio. The arm text is the
  countdown ritual, the confirmation phrase is thematic, and `abort` is enforced
  in code.

### What review round 1 changed in the design

- **Availability is per tool, not per family** (finding 10). `settings.TOOL_PREREQS`
  maps all 22 names to their own prerequisites; families aggregate into a
  tri-state startup verdict. `nas_video_query` needs only the index file;
  `play_video` needs neither a LAN base nor a live media mount; `stop_music`
  needs nothing at all, by design.
- **The home verdict is tri-state** (finding 12). `away_from_home` now requires
  routing-level absence proven by a socket-level LAN signal. An expired HA token,
  an HA outage and a VPN connection are all `home_status_unknown`.
- **The confirmation gate is session-scoped, and spends authorisation on success**
  (findings 3 and 4), so a confirmation cannot survive a backend reconnect and a
  transient 503 does not cost the user their approval.
- **Music is a serialized state machine with acknowledged daemon commands**
  (finding 2), and the resume waits for a real audio-drain signal from
  `console.play_loop` rather than for `response.done` (finding 1).
- **Ported tools log metadata only** (finding 7), through `hanova/redact.py`.
- **No default in `settings.py` is derived from the operator's own setup**
  (finding 6): the NAS share and subpaths and the three HA script names all
  default to empty and are real prerequisites.

### What review round 2 changed in the design

- **`away_from_home` now requires positive off-home evidence** (finding 3). The
  robot's own address must sit outside every network declared in
  `HANOVA_HOME_NETWORKS`; a failed connection to Home Assistant is an outage, not
  an absence, and produces `home_status_unknown`. Every house-bound tool branches
  all three verdicts and does **no work at all** on `unknown` — round 1 tested
  only `AWAY`, so a VPN, a 401 or a timeout still fired real house actions. The
  boolean `is_home()` is deleted, because it could not express the third state.
- **Every armed action carries an immutable claim id** (finding 2).
  `complete()`, `release()` and the claim-bound `abort()` require it and compare
  epoch *and* id inside the mutating lock, so an operation in flight from an
  older session can no longer spend or re-arm an authorisation that belongs to a
  newer one, and a slot whose action is executing cannot be re-armed at all.
- **Transient and terminal failures are different outcomes** (finding 9).
  `release()` — a bare "try again" — is reserved for connection, disconnection
  and timeout faults. Authentication, refused-recipient, refused-sender and
  validation failures **spend** the authorisation, because the approved action
  cannot succeed as approved and the user must hear a corrected one.
- **The email read-back carries the entire message body** (finding 4), capped at
  500 characters. A first-line preview plus a hex digest is not something a
  person can verify by ear: two bodies with the same opening line produced
  indistinguishable confirmations while the sent mail differed. Longer bodies are
  refused with `body_too_long` rather than summarised.
- **The music resume waits on real, per-response audio accounting** (finding 1).
  `audio_drain` marks a response pending the moment it is created — before any
  audio exists — counts samples at **enqueue** time, and only reports drained
  once the response is closed, nothing is outstanding, the queue is empty and the
  device-buffer estimate has expired. A final tool batch with
  `needs_response=False` now closes the turn itself, which is the path that used
  to leave music paused for the rest of the conversation.
- **Session boundaries invalidate rather than forget** (finding 8).
  `PLAYER.invalidate()` advances the generation under the state lock and the
  boundary also stops the daemon; cleanup runs from the realtime connection's own
  `finally`, so a dropped connection cleans up even when the handler never shuts
  down.
- **Staging is single-flight, and the cursor advances by token** (findings 10
  and 11). Per-destination locks plus uniquely named `.part` files that pruning
  skips; `peek_next()` returns a `CursorToken` and `commit_next(token)` is a
  compare-and-swap, so two concurrent skips consume one clip and a late cast
  cannot advance a new playlist.
- **`HANOVA_NAS_SUBPATH` is consumed** (finding 12): it bounds the subtree an
  index entry's original path may resolve inside, so a mandatory prerequisite
  finally changes behaviour instead of merely blocking deployments.
- **Identifier hygiene reaches tests, docs and the deploy skill** (finding 5).
  Every NAS fixture is an obvious synthetic sentinel, the shape scan covers
  tests/docs/skills with a private-address pattern that actually matches private
  addresses, and the staged-content scan compares literally against an untracked
  value list and reports counts and paths only — never a prefix of a value.
- **Service-layer logging goes through `redact` too** (finding 6). `settings.py`,
  `home_net.py`, `media_store.py`, `ytdlp.py`, `images.py`, `nas.py` and
  `ha_client.py` no longer log raw paths, URLs, stderr tails or tracebacks, and
  each has a `caplog` sentinel test.

### New dependencies (operator-approved)

`yt-dlp`, `imageio-ffmpeg`, `smbprotocol` — three pure-wheel additions, no system
packages. `imageio-ffmpeg` over `static-ffmpeg` because it ships the ffmpeg
binary *inside* a `manylinux2014_aarch64` wheel and exposes `get_ffmpeg_exe()`,
where `static-ffmpeg` downloads its binaries at first use.

### New instance-directory state (deploy ritual)

`google-workspace-mcp/<account>.json` (rewritten on every token refresh),
`google-oauth.json`, `nas-video-index.json`. All three are added to the
backup/restore steps in `.claude/skills/reachy-deploy/SKILL.md`. The
`hanova_media/` cache is deliberately **not** backed up: it is regenerable, and
keeping it would carry hundreds of megabytes of home video through every deploy.
```

- [ ] **Step 7: Update the PRD**

In `C:\Project\Reachy-mini\docs\PRD.md`, in **§7 Functional Scope**, append a new entry after the last existing `F-` item:

```markdown
### F-K — Ported home-and-cloud capabilities (D-018)

Twenty-two Skills ported natively from the operator's `ha-actions` MCP server:
music on the robot's own speaker (`play_music`, `stop_music`), TV casting
(`play_video`, `show_on_tv`), the NAS home-video library (`nas_video_query`,
`play_nas_video`, `nas_play_folder`, `nas_skip`), Google Calendar
(`calendar_add`, `calendar_list`, `calendar_delete`), Google Tasks (`task_add`,
`task_list`, `task_complete`, `task_delete`), Notion (`notion_add`), Google Drive
(`drive_list`, `drive_trash`, `drive_upload`), email (`email_send`), and two
audio gags (`self_destruct`, `mad_laugh`).

Three cross-cutting rules apply. Every **tool** declares its own prerequisites
and returns `{"status": "unavailable", "reason": "<the missing config key>"}`
when they are not met; families report a tri-state
`enabled` / `partial` / `disabled` verdict at startup, and the app boots green
with none of it present. House-bound capabilities answer
`{"status": "away_from_home"}` **only on positive evidence that the robot is on
some other network** — declared via `HANOVA_HOME_NETWORKS`; an unreachable,
unauthorised or tunnelled Home Assistant is `home_status_unknown`, which performs
no house action at all. Every destructive capability is gated in code by a
two-step read-back with a 90-second window, scoped to the conversation and to an
individual claim id, and the confirmed call executes the parked action, not the
arguments of the second call; `email_send` reads the **whole** message body back
and refuses one too long to read.

Three upstream behaviours are declared non-goals: Drive restore (the Drive UI
does it), email BCC (a recipient the read-back cannot surface), and the generic
confirmation summary for `self_destruct` (which would spoil the gag it gates —
its in-character ritual, TTL and abort word are kept instead).

This is additive to the five §8 demos and is not on their critical path.
```

In **§12 System Architecture**, append to the component list:

```markdown
- `hanova/` — ported service layers for the D-018 capabilities: `settings.py`
  (the whole `HANOVA_*` config surface, per-tool prerequisites and per-family
  enablement), `redact.py` (the one helper every ported tool logs and errors
  through), `confirm.py` (the session-scoped confirmation gate), `ha_client.py`
  (async Home Assistant REST), `media_store.py` (the LAN-served media cache
  mounted on the existing web server), `ytdlp.py` / `music_player.py` /
  `audio_drain.py` / `music_hooks.py` / `sfx.py` (robot-speaker audio, its
  barge-in lifecycle and the realtime-loop call sites), `images.py` (OpenAI
  Images for `show_on_tv`), `gauth.py` / `gcal.py` / `gtasks.py` / `gdrive.py` /
  `notion_client.py` / `gmail_smtp.py` (cloud APIs over one shared blocking-HTTP
  seam), and `nas.py` (index queries, validated SMB staging and the trip
  session). `home_net.py` at package root holds the tri-state home-network probe.
```

- [ ] **Step 8: Update `progress.md` and `feature_list.json`**

At the top of `C:\Project\Reachy-mini\progress.md`, insert a new section directly under the `# Progress` heading:

```markdown
## HomeAssistant-Nova port landed (2026-08-21 — D-018)

Twenty-two capabilities ported natively from the operator's `ha-actions` MCP
server: no MCP hop, no vendored `server.py`, no stdio lane. Every personal
identifier upstream hardcoded — calendar id, task-list id, Drive folder id,
account address, HA entity ids, NAS share and credentials — is now configuration,
and `tests/test_hanova_integration.py` fails if any of it reappears in the
package, the profile or `.env.example`.

Four cross-cutting behaviours are in code, not in the prompt: **per-tool**
enablement aggregated into one tri-state verdict line per family (the app boots
green with zero new configuration), a **tri-state** home-network probe that says
`away_from_home` only on positive off-home routing evidence and
`home_status_unknown` — doing no house work whatsoever — when Home Assistant is
merely broken, unauthorised or reached over a tunnel, a 90-second two-step
confirmation gate **scoped to the conversation and to an individual claim id**
that executes the *parked* action and spends the authorisation on success or on a
terminal failure, and metadata-only logging through one shared redaction helper
across both the tools and the new service layer.

Three new pure-wheel dependencies: `yt-dlp` 2026.8.19, `imageio-ffmpeg` 0.6.0,
`smbprotocol` 1.17.0 — all confirmed to resolve as aarch64 wheels. Media the TV
must fetch is served from the app's existing FastAPI server at `/hanova-media`,
so there is no new port and no second web server.

Three upstream behaviours are declared non-goals rather than quietly dropped:
Drive restore, email BCC, and the generic confirmation summary for the
self-destruct gag (whose in-character ritual, TTL and abort word are kept).

**Not yet run on the robot.** The on-robot deployment and wake-test checklist is
the check that matters; until it runs, every claim here rests on the suite.
```

In `C:\Project\Reachy-mini\feature_list.json`, add these **nine** items to
`items` — **one per actual family, plus a separate disabled-boot acceptance
item** (review round 1, finding 23). The previous seven-item list folded Drive
and email into one state, which made "Drive verified, email blocked"
unspellable, and used the disabled-boot item as a stand-in for a family, which
left the media-cast family with no item of its own.

That is seven family items (`HANOVA-MUSIC`, `HANOVA-CAST`, `HANOVA-NAS`,
`HANOVA-GOOGLE`, `HANOVA-NOTION`, `HANOVA-DRIVE`, `HANOVA-EMAIL`), plus
`HANOVA-GAGS` split out of the music family because the two gags block on their
own clip ids rather than on the wheels the other two music tools need — the same
independence argument that separated Drive from email — plus the standalone
`HANOVA-DISABLED-BOOT`.

```json
{"id": "HANOVA-MUSIC", "behavior": "play_music searches and plays a track on the robot's own speaker; stop_music always stops it; user speech ducks it and it resumes only once the turn's audio has drained", "verification": "On-robot: ask for a song, interrupt mid-track, confirm the music ducks and resumes near where it stopped (not from zero, and not over Reachy's reply), then say stop", "state": "planned", "evidence": "", "next_action": "run the Task 15 wake test"},
{"id": "HANOVA-GAGS", "behavior": "mad_laugh plays its clip on the robot speaker; self_destruct runs its in-character arm/confirm ritual with a 90 s TTL and a working abort word, and plays nothing until authorised", "verification": "On-robot: run mad_laugh; arm self_destruct and abort it; arm it again and authorise it", "state": "planned", "evidence": "", "next_action": "operator supplies the two HANOVA_*_YT_ID clip ids"},
{"id": "HANOVA-CAST", "behavior": "play_video casts a YouTube result to the TV; show_on_tv generates an image and casts its LAN URL; both answer away_from_home only on positive off-home evidence (the robot outside every HANOVA_HOME_NETWORKS entry) and home_status_unknown -- doing no work at all -- for a VPN, a 401, a 5xx, a timeout, a DNS failure or a refused connection", "verification": "On-robot at home: one cast of each; then with HANOVA_HOME_NETWORKS set and the robot off the LAN confirm away_from_home; then with it blank confirm home_status_unknown instead; then with HA returning 401 confirm home_status_unknown and that no HA script ran", "state": "planned", "evidence": "", "next_action": "operator supplies HANOVA_HA_SCRIPT_* names, the cast entity, the LAN base and HANOVA_HOME_NETWORKS"},
{"id": "HANOVA-NAS", "behavior": "nas_video_query searches the index with no SMB credentials needed; play_nas_video and nas_play_folder stage a clip over SMB atomically and cast its LAN URL; nas_skip advances the trip only after a successful cast", "verification": "On-robot at home with the index and NAS credentials present: query, play, skip; then force one cast failure and confirm the trip position did not move", "state": "planned", "evidence": "", "next_action": "operator supplies nas-video-index.json, the NAS credentials, the share and the two subpaths"},
{"id": "HANOVA-GOOGLE", "behavior": "calendar_add/list/delete and task_add/list/complete/delete work against the real Google account; the three destructive ones require a read-back confirmation that survives a transient failure", "verification": "On-robot: add and list one event; add and list one task; then run each of the three gated calls and confirm nothing happens before the second confirmed call", "state": "planned", "evidence": "", "next_action": "operator supplies the writable Google OAuth JSON, the calendar id and the tasks list id"},
{"id": "HANOVA-NOTION", "behavior": "notion_add writes one row into the configured notes data source, distinct from the NOTION_MCP_* remote lane", "verification": "On-robot: dictate a note and confirm the row appears in Notion", "state": "planned", "evidence": "", "next_action": "operator supplies HANOVA_NOTION_TOKEN and the data-source id"},
{"id": "HANOVA-DRIVE", "behavior": "drive_list reads the configured folder; drive_trash and drive_upload (a camera frame captured at confirm time) both require a read-back confirmation and execute the parked action; nothing is ever made anyone-with-link readable and nothing can be untrashed", "verification": "On-robot: list the folder, then run drive_trash and drive_upload and confirm neither acts before its second confirmed call", "state": "planned", "evidence": "", "next_action": "operator supplies the Drive OAuth secret and the folder id"},
{"id": "HANOVA-EMAIL", "behavior": "email_send reads back every recipient (To and CC), the subject and the ENTIRE message body verbatim, refuses a body over 500 characters and an address it cannot parse, supports no BCC at all, sends only the parked envelope, and spends the authorisation on a terminal SMTP failure rather than offering a bare retry", "verification": "On-robot: attempt a send with no address and confirm it asks for one; dictate a two-paragraph body and confirm Reachy reads BOTH paragraphs back before sending; then send one real mail to the operator and confirm the read-back named every recipient before it went", "state": "planned", "evidence": "", "next_action": "operator supplies the sending address and its app password"},
{"id": "HANOVA-DISABLED-BOOT", "behavior": "With zero HANOVA_* configuration the app boots normally and logs one tri-state line per family. Six families report disabled with the missing key named. The music family is the documented exception: it depends on installed wheels rather than on configuration, so play_music and stop_music are available while self_destruct and mad_laugh are not (no clip ids) -- i.e. music reports partial, not enabled", "verification": "On-robot: start with the new keys blank and read the seven hanova family lines in the startup log; then ask for a calendar event and confirm the tool answered unavailable with the missing key as the reason", "state": "planned", "evidence": "", "next_action": "run the Task 15 config checklist"}
```

- [ ] **Step 9a: Strip the connection identifiers out of the deploy skill (findings 6 and 20)**

`.claude/skills/reachy-deploy/SKILL.md` is a **tracked file** that currently
parenthesises the robot's real LAN IP and SSH username, and carries the host-key
fingerprint as a literal. Recommitting it in this task would recommit all three.
Before anything else in this step:

1. Replace every parenthesised literal IP and username with the phrase
   **"read from the repo-root `.env`"**, naming the keys `REACHY_HOST`,
   `REACHY_SSH_USER` and `REACHY_SSH_PASSWORD`. The skill should read, for
   example: "the robot's address and login are in the gitignored repo-root
   `.env` as `REACHY_HOST` / `REACHY_SSH_USER` / `REACHY_SSH_PASSWORD`; never
   write their values into this file."
2. Move the host-key fingerprint to a **new gitignored key**, `REACHY_HOSTKEY`,
   in the same repo-root `.env`. The skill documents the key name and the
   `-hostkey $env:REACHY_HOSTKEY` usage; it must not contain the fingerprint.
3. **Create** the repo-root `.env.example` — round 2, finding 14. It does not
   exist today, and the round-1 instruction to "modify and stage" it would have
   failed twice over: there was nothing to modify, and the repo-root
   `.gitignore` swallows it. Fix the ignore rule first, then write the file:

```powershell
cd C:\Project\Reachy-mini
# Every rule that can match `.env.example`, WITH its line number. Round 3,
# finding 6: this file has more than one -- `.env` early and `.env.*` later.
Select-String -Path .gitignore -Pattern '^\s*!?/?\.env' |
  ForEach-Object { "$($_.LineNumber): $($_.Line)" }
```

   Append the negation **at the end of `.gitignore`, after every one of those
   rules** (round 3, finding 6). Putting it directly under `.env` does **not**
   work and was the previous version's defect: git applies the *last* matching
   pattern, and the later `.env.*` rule re-ignores the template, so Task 14 could
   never have staged it. If a future `.env`-matching rule is ever added below,
   this negation moves down with it.

```gitignore
# D-018: the deployment template is tracked on purpose. This negation must stay
# BELOW every `.env` / `.env.*` rule above it -- the last matching pattern wins.
!/.env.example
```

   Then create `C:\Project\Reachy-mini\.env.example` with the complete
   four-key template — **placeholders only**, and all four keys present, because
   Task 15 Step 4 refuses to deploy unless every one of them is set:

```dotenv
# Robot deployment connection (D-018). Copy this file to `.env` and fill it in;
# `.env` is gitignored and NONE of these values may ever be committed.
#
# Reachy Mini Wireless, reachable on the operator's own LAN.
REACHY_HOST=<robot-lan-ip-or-hostname>
# The SSH account the reachy-deploy skill logs in as.
REACHY_SSH_USER=<ssh-username>
REACHY_SSH_PASSWORD=
# The robot's SSH host-key fingerprint, in the exact form plink prints it, e.g.
# "ssh-ed25519 255 SHA256:....". Required by every `plink`/`pscp` call in the
# deploy ritual, which runs with `-batch` and therefore cannot answer a prompt.
# Get it once from an interactive plink session; never disable host-key checking
# and never put the fingerprint back into a tracked file.
REACHY_HOSTKEY=
```

4. Confirm the repo-root `.env` is gitignored (it is; verify, do not assume),
   that the new template is **not**, and — round 3, finding 6 — that git will
   actually **track** it. `check-ignore` alone proves only that no rule matches;
   `ls-files --error-unmatch` is the check that the file is in the index:

```powershell
cd C:\Project\Reachy-mini
git check-ignore -v .env           # must print the matching rule
"env-ignored exit: $LASTEXITCODE"  # must be 0 (a match was found)

git check-ignore -v .env.example   # must print NOTHING
"template-ignored exit: $LASTEXITCODE"   # must be 1 (no rule matches it)

git add .env.example
git ls-files --error-unmatch .env.example   # must print `.env.example`
"template-tracked exit: $LASTEXITCODE"      # must be 0
```

   Both exit codes matter. The previous version checked only the first and would
   have reported success on a template git was still ignoring.

`test_the_deploy_skill_holds_no_connection_identifiers`,
`test_the_env_keys_the_deploy_uses_are_all_gitignored`,
`test_the_root_env_example_exists_and_is_tracked` and
`test_the_root_env_example_carries_no_real_value` all fail until this is done.

- [ ] **Step 9b: Scan the staged *content* for the operator's real values (findings 6 and round 2 finding 5)**

A shape-based test cannot catch the operator's *actual* values. This scan can,
and it runs **locally, writing nothing**. Round 2, finding 5 rewrote it; round 3,
finding 4 made it *executable* — the round-2 version would have thrown on this
repository's existing history and had a hole big enough to walk a short value
through.

| Defect | Why it mattered | Fix here |
|---|---|---|
| (R1) Scanned `git diff --cached`, which includes **deleted** lines | Step 9a *removes* the robot's IP and username from the deploy skill, so the scan was guaranteed to "find" them and cry wolf on every run — training the operator to ignore it | Scan the **post-image**: `git show :<path>` for every staged path, never the diff |
| (R1) Only values present in the repo-root `.env` | The three scratchpad reports hold identifiers that are in no `.env` at all — the calendar id, the Drive folder id, the NAS layout | A secure, **untracked** value list at a path outside the repo, seeded from the `.env` *and* from all three reports, every entry **labelled** |
| (R1) `-like "*$secret*"` | `-like` is wildcard matching, so a value containing `*`, `?` or `[` silently matches the wrong things — or nothing | `IndexOf(..., Ordinal)`, a literal comparison, counted rather than tested |
| (R1) Printed `$secret.Substring(0,3)` on a hit | A three-character prefix of a token is still a disclosure, and it lands in the terminal scrollback and in any transcript of the session | Report **path, label and count only** — never any part of a value |
| (R2) "Values shorter than 8 characters are skipped" | A short value is still an identifier. A four-character NAS share silently left the scan, and the skip was reported as a bare number nobody could act on | **No length skip at all.** Every labelled value is scanned; the short ones are *listed by label* in the summary so a coincidental hit is diagnosable |
| (R2) Any occurrence anywhere was a hard failure | A value already committed in `DECISIONS.md` before this port existed made the gate unpassable, so it would have been switched off rather than fixed | Compare the post-image count with the **`HEAD` count of the same path**. A failure is an *increase* — or **any** occurrence in a file this port explicitly scrubs |

Create the value list **once**, outside the repository, and never commit it.
Every line is `LABEL=VALUE`: the label is what the scan prints, the value is what
it searches for and never shows.

```powershell
# One-time. $env:TEMP is outside the repo and outside any git worktree.
$listPath = Join-Path $env:TEMP "hanova-secret-values.txt"
$lines = @()
foreach ($line in (Get-Content C:\Project\Reachy-mini\.env)) {
  if ($line -match '^\s*#' -or $line -notmatch '=') { continue }
  $parts = $line -split '=', 2
  $key = $parts[0].Trim(); $value = $parts[1].Trim()
  if ($key -and $value) { $lines += "$key=$value" }     # label = the env key
}
$lines | Set-Content -Path $listPath -Encoding utf8
icacls $listPath /inheritance:r /grant:r "$($env:USERNAME):(R,W)" | Out-Null
"seeded from .env: $((Get-Content $listPath).Count) labelled value(s)"
```

Then **add the report-derived entries by hand**, one `LABEL=VALUE` per line.
This inventory is the whole strength of the scan, so it is enumerated rather than
described (round 3, finding 4 — the round-2 wording named four categories and
missed the rest):

```text
# --- from ha-nova-config-manifest.md (§D naming resolutions) -----------------
HA_URL=            HA_TOKEN=
GCAL_CALENDAR_ID=  GTASKS_LIST_ID=   GOOGLE_ACCOUNT=   GOOGLE_CREDS_DIR=
DRIVE_PARENT_ID=   DRIVE_SECRETS_PATH=
NOTION_TOKEN=      NOTION_DATA_SOURCE_ID=
SMTP_USER=         SMTP_APP_PASSWORD=  SMTP_FROM_NAME=
NAS_HOST=  NAS_USER=  NAS_PASSWORD=  NAS_SHARE=  NAS_SUBPATH=  NAS_CAST_SUBPATH=
NAS_INDEX_PATH=    CAST_ENTITY=      MEDIA_HTTP_BASE=   HOME_NETWORKS=
HA_SCRIPT_YOUTUBE= HA_SCRIPT_IMAGE_URL=  HA_SCRIPT_VIDEO_URL=
SELF_DESTRUCT_YT_ID=  MAD_LAUGH_YT_ID=
# --- from ha-actions-portability.md (per-tool mechanics) ---------------------
# every entity_id the upstream tools drive (TV, each speaker, the Voice-PE),
# every scripts.yaml entry name, each media player's friendly name, and every
# absolute upstream path quoted in the report (/Users/..., the server.py home)
ENTITY_TV=  ENTITY_SPEAKER_1=  ENTITY_VOICE_PE=  SCRIPT_...=  UPSTREAM_PATH_...=
# --- from ha-nova-survey.md (inventory and risk review) ----------------------
# the operator's own address and any second address, the household member names
# used in the tool examples, the NAS trip and folder names, the Notion page and
# database titles, the calendar display names, the robot's LAN address, the NAS
# LAN address, each home subnet, and any Tailscale/CGNAT address
OWNER_EMAIL=  OTHER_EMAIL=  PERSON_...=  TRIP_FOLDER_...=  NOTION_TITLE_...=
CALENDAR_NAME_...=  ROBOT_LAN_IP=  NAS_LAN_IP=  HOME_SUBNET_...=  TAILSCALE_IP=
# --- already seeded from the repo-root .env ---------------------------------
# REACHY_HOST  REACHY_SSH_USER  REACHY_SSH_PASSWORD  REACHY_HOSTKEY
```

Delete the lines you have no value for; **do not** leave a label with an empty
value (the scan skips those, which is the only skip it has). There is **no
minimum length** — a four-character share name is scanned like everything else.

Run the scan immediately before the Step 12 commit, over everything this task
will stage:

```powershell
cd C:\Project\Reachy-mini
$listPath = Join-Path $env:TEMP "hanova-secret-values.txt"

$inventory = @()
foreach ($line in (Get-Content $listPath)) {
  $trimmed = $line.Trim()
  if (-not $trimmed -or $trimmed.StartsWith('#') -or $trimmed -notmatch '=') { continue }
  $parts = $trimmed -split '=', 2
  $label = $parts[0].Trim(); $value = $parts[1].Trim()
  if (-not $label -or -not $value) { continue }
  $inventory += [pscustomobject]@{ Label = $label; Value = $value }
}
if ($inventory.Count -eq 0) { throw "the value inventory is empty; this scan would prove nothing" }

# Round 3, finding 4: NO length skip. Short values are kept and named, so a
# coincidental hit can be diagnosed without ever printing the value.
$short = @($inventory | Where-Object { $_.Value.Length -lt 8 })
"inventory: $($inventory.Count) labelled value(s); short (<8 chars, kept): $((@($short.Label) | Sort-Object) -join ', ')"

# Files this port promises to leave clean. Zero occurrences, no HEAD excuse.
$scrubbed = @('.claude/skills/reachy-deploy/SKILL.md', '.env.example', 'reachy_companion/.env.example')

function Get-Occurrences([string]$haystack, [string]$needle) {
  if (-not $haystack -or -not $needle) { return 0 }
  $count = 0
  $index = $haystack.IndexOf($needle, [System.StringComparison]::Ordinal)
  while ($index -ge 0) {
    $count++
    $index = $haystack.IndexOf($needle, $index + $needle.Length, [System.StringComparison]::Ordinal)
  }
  return $count
}

# Round 2, finding 5: the POST-IMAGE of each staged path, not the diff. A diff
# contains the deleted identifiers Step 9a is removing on purpose.
$staged = @(git diff --cached --name-only --diff-filter=ACMR)
$failures = @()
$preexisting = @()
foreach ($path in $staged) {
  $post = (git show ":$path") -join "`n"
  # Empty when the path is new at HEAD -- git exits non-zero and prints nothing.
  $head = (git show "HEAD:$path" 2>$null) -join "`n"
  $isScrubbed = $scrubbed -contains $path
  foreach ($entry in $inventory) {
    $now = Get-Occurrences $post $entry.Value
    if ($now -eq 0) { continue }
    $was = if ($isScrubbed) { 0 } else { Get-Occurrences $head $entry.Value }
    if ($isScrubbed -or $now -gt $was) {
      $failures += "$path : $($entry.Label) x$now (HEAD had $was)"
    } else {
      $preexisting += "$path : $($entry.Label) x$now (unchanged from HEAD)"
    }
  }
}

"scanned $($staged.Count) staged path(s) against $($inventory.Count) labelled value(s)"
if ($preexisting.Count -gt 0) {
  "pre-existing, NOT introduced by this port -- record these, do not ignore them:"
  $preexisting | ForEach-Object { "  $_" }
}
if ($failures.Count -gt 0) {
  $failures | ForEach-Object { "  LEAK: $_" }
  throw "STAGED CONTENT LEAK: $($failures.Count) new or scrubbed-file occurrence(s); see the labelled list above"
}
"clean"
```

Expected: an `inventory: ...` line, a `scanned ...` line, possibly a
`pre-existing` block, and `clean`. **The script prints paths, labels and counts,
never any part of a value** — not even a prefix. A `LEAK:` line means this port
*introduced* an occurrence (or left one in a file it promised to scrub): unstage,
remove the value, and re-run; do not "fix it in the next commit". A
`pre-existing` line means the identifier was already in `HEAD` at that path —
copy it into the notes as a recorded, separate clean-up rather than silently
accepting it. Run the same scan again in Task 15 over that task's staged
post-image, where the risk is highest.

- [ ] **Step 9c: Extend the deploy skill's backup ritual**

In `C:\Project\Reachy-mini\.claude\skills\reachy-deploy\SKILL.md`, step 4 currently lists four instance files. Add these three bullets after the `faces.v1.json` bullet:

```markdown
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
```

and replace the two shell blocks in that step. **Review round 1, finding 19**
rewrote both: the old ritual reused one fixed `/tmp/reachy_companion_backup`
directory, so a file that is absent *today* could be restored from a **stale copy
left by a previous deployment**, and the restore block was unconditional even
though the backup block tolerated missing files. The recursive `ls -lR` also
printed credential filenames into the deploy transcript.

Backup — a **unique, verified, empty** directory per deployment, plus a redacted
manifest that the restore reads:

```sh
STAMP="$(date -u +%Y%m%dT%H%M%SZ)-$$"
BACKUP="/tmp/reachy_companion_backup/$STAMP"
mkdir -p "$BACKUP" && chmod 700 "$BACKUP"
# Finding 19: a fresh directory every time. A stale file from a previous deploy
# must never be able to overwrite a file that is legitimately absent now.
[ -z "$(ls -A "$BACKUP")" ] || { echo "FATAL: backup dir not empty"; exit 1; }

: > "$BACKUP/manifest.txt"
for NAME in .env persona.md memory.v1.json faces.v1.json google-oauth.json nas-video-index.json; do
  if [ -e "$INST/$NAME" ]; then
    cp -a "$INST/$NAME" "$BACKUP/$NAME"
    echo "file $NAME $(stat -c '%a %s' "$INST/$NAME")" >> "$BACKUP/manifest.txt"
  else
    echo "absent $NAME" >> "$BACKUP/manifest.txt"
  fi
done
if [ -d "$INST/google-workspace-mcp" ]; then
  cp -a "$INST/google-workspace-mcp" "$BACKUP/google-workspace-mcp"
  # Finding 19: a COUNT, not a listing. The filenames are account addresses.
  echo "dir google-workspace-mcp $(find "$INST/google-workspace-mcp" -type f | wc -l) files" >> "$BACKUP/manifest.txt"
else
  echo "absent google-workspace-mcp" >> "$BACKUP/manifest.txt"
fi
echo "$BACKUP"
cat "$BACKUP/manifest.txt"
```

The manifest is the record the Verification Gate wants: every `absent` line is an
explicitly recorded absence rather than a missed file, and it names no
credential. Copy `$BACKUP` into the deploy notes — the restore needs it.

Restore — **conditional, driven by the manifest, with permissions reasserted**:

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
      [ -d "$BACKUP/$NAME" ] && cp -a "$BACKUP/$NAME" "$INST/$NAME" || echo "MISSING in backup: $NAME"
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

- [ ] **Step 10: Run the test to verify it passes**

```powershell
cd C:\Project\Reachy-mini\reachy_companion
..\.venv\Scripts\python.exe -m pytest tests/test_hanova_integration.py -q
```

Expected: green — **31** test functions, none parametrised: **31 collected
cases**, and **0 skipped**. Round 3, finding 4 gave
`test_no_personal_identifier_shape_is_committed_anywhere_this_port_writes` a
`pytest.skip` for a checkout with no git `HEAD`; this repository has one, so a
skip here means git is not reachable and must be fixed, not accepted. Record the
exact number.

- [ ] **Step 11: Run the full gate**

```powershell
cd C:\Project\Reachy-mini\reachy_companion
..\.venv\Scripts\python.exe -m pytest -q
..\.venv\Scripts\python.exe -m ruff check src tests
..\.venv\Scripts\python.exe -m ruff format --check src tests
..\.venv\Scripts\python.exe -m mypy
```

Expected: pytest green (30 documented skips), ruff clean, mypy `Success: no issues found`.

- [ ] **Step 12: Commit**

```bash
cd /c/Project/Reachy-mini
git rev-parse --abbrev-ref HEAD   # must print feat/ha-nova-port (Task 0)
git add reachy_companion/profiles/_reachy_companion_locked_profile/profile.md \
        docs/adding-a-skill.md \
        docs/PRD.md \
        DECISIONS.md \
        progress.md \
        feature_list.json \
        .claude/skills/reachy-deploy/SKILL.md \
        .gitignore \
        .env.example \
        reachy_companion/tests/test_hanova_integration.py
```

`.gitignore` and the newly created repo-root `.env.example` are staged **here**
(round 2, finding 14): the negation rule and the file it un-ignores have to land
in the same commit, or the template is tracked by nothing.

`persona.md` is staged **separately and deliberately** (Task 0, rule 2). Because
the user's patch is stashed, the working-tree diff is the port patch and nothing
else, so the whole file is the correct thing to stage — but read it first, and
**STOP** if it contains anything this task did not write:

```bash
git diff persona.md          # ours is the Tools/Conventions block, and ONLY that
git add persona.md           # never `git add -p`: there is nothing to separate
git diff --cached --stat     # confirm exactly the intended paths and nothing else
```

Now run the Step 9b staged-content scan, then commit:

```bash
git commit -m "docs(hanova): persona routing, D-018 with its non-goals, PRD F-K, and the hardened deploy ritual"
git status --short
git stash list               # the baseline persona stash must STILL be present
```

The `git status --short` must show the Task 0 baseline paths **minus**
`persona.md` (stashed since Task 0, staged and committed here), and the stash
list must still hold `hanova-port: user persona.md baseline`. Task 15, Step 15b
is the only place it comes back.

---

### Task 15: Deploy to the physical robot and verify on device

The acceptance bar is runnable evidence, never "should work". This task follows
`C:\Project\Reachy-mini\.claude\skills\reachy-deploy\SKILL.md` exactly, uses the
hardened backup/restore ritual landed in Task 14, seeds the new environment keys,
reads the seven family verdicts out of the startup log, and runs a scripted wake
test that covers **every** ported tool.

**Operator authorisation on file (2026-08-17): deploy as APP only. Never modify
the robot's daemon.** No daemon package changes, no daemon config edits, no
system packages, no reboots. If the version gate fails, STOP and report.

**Four things review round 1 changed about this task:**

- **The full local gate runs before the wheel is built, and again after any fix**
  (finding 22). Global Constraint R12 requires it and this task skipped it
  entirely — it built and shipped from an unverified tree.
- **The feature branch is merged into `main` before the wheel is built**
  (finding 24), so the robot always runs `main` and the deployed artifact
  corresponds to a commit on the mainline, not to a branch tip.
- **The robot's `persona.md` is replaced with the reviewed repo copy, not
  restored from backup** (finding 9). The instance persona overrides the built-in
  profile, so restoring the old one would leave every routing and confirmation
  convention from Task 14 unreachable on the device — the deploy would look
  successful and change nothing the user can hear.
- **Deployment evidence is split** (finding 7). Raw log excerpts go into a
  gitignored artifact directory; only redacted status and count lines are
  committed.

**Files:**
- Modify: `feature_list.json` (fill in `evidence` and flip `state` for the nine `HANOVA-*` items)
- Modify: `progress.md` (replace "Not yet run on the robot" with the measured result)
- Write (gitignored, **never committed**): `artifacts/deploy-2026-08-21/` — raw `journalctl` excerpts and the on-device transcript
- No source changes. If deployment reveals a defect, fix it in the owning task's files, re-run that task's gate **and the full gate**, rebuild, and redeploy.

**Interfaces:**
- Consumes: everything from Tasks 0–14; `.claude/skills/reachy-deploy/SKILL.md` as the procedure of record; and the gitignored repo-root `.env` for `REACHY_HOST`, `REACHY_SSH_USER`, `REACHY_SSH_PASSWORD` and `REACHY_HOSTKEY`.
- Produces: on-device evidence — the seven family verdict lines, the exact set of 22 registered tool names within one startup boundary, a media-route probe against a seeded file, and a completed wake-test table covering all 22 tools and **all seven gated tools in both directions, plus the separate self-destruct abort branch** (round 2, finding 17: the plan previously alternated between "eight gated paths" and seven gated tools; there are seven gated tools — `calendar_delete`, `task_complete`, `task_delete`, `drive_trash`, `drive_upload`, `email_send`, `self_destruct` — and the eighth thing being counted was self_destruct's abort, which is a third branch of one tool, not an eighth tool).

- [ ] **Step 1: Load the deploy skill and re-read the procedure**

Invoke the `reachy-deploy` skill and read
`C:\Project\Reachy-mini\.claude\skills\reachy-deploy\SKILL.md` end to end,
including the two pitfalls at the bottom: run `plink`/`pscp` from **PowerShell**
(Git Bash mangles a remote command whose first token is a POSIX path, and the
force-reinstall then silently does not run), and `plink` needs
`-batch -hostkey <SHA256 fingerprint>` in batch mode. As of Task 14 the skill
holds **no connection values**: the host, user, password and host-key fingerprint
all come from the gitignored repo-root `.env`.

- [ ] **Step 1b: Run the full local gate (R12, review finding 22)**

Nothing is built or shipped from an unverified tree. This is the same gate every
other task ends with, run once more over the integrated result:

```powershell
cd C:\Project\Reachy-mini\reachy_companion
..\.venv\Scripts\python.exe -m pytest -q
..\.venv\Scripts\python.exe -m ruff check src tests
..\.venv\Scripts\python.exe -m ruff format --check src tests
..\.venv\Scripts\python.exe -m mypy
```

Expected: pytest green (30 documented skips), ruff clean, mypy `Success: no
issues found`. **If any of these fail, STOP.** Do not build, do not deploy. Fix
it in the owning task's files and re-run this step. This gate also runs again
after any fix made in response to something the deployment reveals.

- [ ] **Step 1c: Merge the feature branch into `main` (review finding 24)**

The robot runs `main`. Merge before building, so the wheel corresponds to a
mainline commit:

Round 2, finding 7: the branch switch happens with `persona.md` **clean**,
because Task 0 stashed the user's patch. A dirty `persona.md` here is exactly
what git refuses to carry across a checkout that changes the file, and the
round-1 plan would have hit that — or worse, carried the user's edits onto
`main`. Assert it rather than hoping:

```powershell
cd C:\Project\Reachy-mini
git status --short                       # only the Task 0 baseline paths
git stash list                           # the persona baseline stash is present

# Round 2, finding 7: persona.md must be clean before the checkout.
if ((git status --porcelain -- persona.md)) {
  throw "persona.md is dirty; the Task 0 stash was lost. Recover it before merging."
}

git checkout main
git merge --no-ff feat/ha-nova-port -m "feat(hanova): HomeAssistant-Nova native port (D-018)"
git log --oneline -1
git status --short --branch              # must print '## main'
git show --stat HEAD -- persona.md       # the port's persona patch is in the merge
```

A fast-forward is equally acceptable if `main` has not moved
(`git merge feat/ha-nova-port`); the `--no-ff` form is preferred because it keeps
the port legible as one unit in the history. If the merge conflicts, resolve it,
**re-run Step 1b**, and only then continue. Everything from here on happens on
`main`, and the user's persona patch stays stashed until Step 15b.

- [ ] **Step 2: Re-prove the three new dependencies resolve as aarch64 wheels**

Use the **corrected** command (review finding 8). Resolving the whole
`pyproject.toml` with `--only-binary :all:` fails on the existing `reachy-mini`
Linux dependency chain long before it reaches the three additions, so it proves
nothing about them; and `--output-file -` creates a literal file named `-` in the
repository with the installed `uv`. Resolve only the three new requirements, from
stdin, to an explicit path outside the repo:

```powershell
cd C:\Project\Reachy-mini
$req = @'
yt-dlp>=2026.8.19
imageio-ffmpeg>=0.6.0
smbprotocol>=1.17.0
'@
$out = Join-Path $env:TEMP "hanova-aarch64-resolution.txt"
$req | uv pip compile - --python-platform aarch64-manylinux_2_28 --only-binary :all: --no-header -o $out
Select-String -Path $out -Pattern "yt-dlp|imageio-ffmpeg|smbprotocol|no wheels"
git status --short   # must be unchanged: no file named '-' anywhere
```

Expected — matching the resolution the external reviewer already ran on
2026-08-21: `yt-dlp==2026.8.19`, `imageio-ffmpeg==0.6.0` (manylinux2014_aarch64),
`smbprotocol==1.17.0` with `pyspnego`, `cryptography`, `cffi` and `pycparser`
behind it, and no "no wheels are available" line. If `imageio-ffmpeg` does not
resolve for aarch64, STOP and report — do not substitute `static-ffmpeg`, which
downloads its binaries at first use and would make music fail on a robot with no
outbound access at that moment.

- [ ] **Step 3: Build exactly one wheel and capture its exact filename**

Round 2, finding 16: `dist/` accumulates. Every previous build's wheel is still
there, so `reachy_companion-*.whl` expands to *several* arguments — `pscp` then
copies all of them and `pip install /tmp/reachy_companion-*.whl` resolves to
whichever the remote shell happens to sort first. The robot can end up running a
stale artifact while the transcript says the build succeeded. Clear the directory,
build once, and require **exactly one** result:

```powershell
cd C:\Project\Reachy-mini
if (Test-Path .\reachy_companion\dist) { Remove-Item .\reachy_companion\dist\*.whl -Force }
uv build .\reachy_companion
.\.venv\Scripts\python.exe -c "from importlib.metadata import entry_points; print([e.name for e in entry_points(group='reachy_mini_apps')])"

$wheels = @(Get-ChildItem .\reachy_companion\dist\*.whl)
if ($wheels.Count -ne 1) {
  throw "expected exactly one freshly built wheel, found $($wheels.Count): $($wheels.Name -join ', ')"
}
$env:HANOVA_WHEEL = $wheels[0].FullName
$env:HANOVA_WHEEL_NAME = $wheels[0].Name
"built: $($env:HANOVA_WHEEL_NAME)"
```

Expected: exactly one fresh `reachy_companion-1.0.0-*.whl`, its name echoed, and
`reachy_companion` present in the `reachy_mini_apps` entry-point group. Every
later step uses `$env:HANOVA_WHEEL` / `$env:HANOVA_WHEEL_NAME` — **never a
glob**.

- [ ] **Step 4: Load every connection variable, then the version gate**

Review finding 20: the previous version loaded only `REACHY_HOST` and then used
`$env:REACHY_SSH_USER`, `$env:REACHY_SSH_PASSWORD` and no host key at all, as if
they were already in the process. Load and **validate all four** first — this is
the one place they are read, and none of them is ever echoed:

```powershell
cd C:\Project\Reachy-mini
foreach ($line in (Get-Content .env)) {
  if ($line -match '^\s*(REACHY_HOST|REACHY_SSH_USER|REACHY_SSH_PASSWORD|REACHY_HOSTKEY)\s*=\s*(.+)$') {
    Set-Item -Path "env:$($Matches[1])" -Value $Matches[2].Trim()
  }
}
$missing = @()
foreach ($name in 'REACHY_HOST','REACHY_SSH_USER','REACHY_SSH_PASSWORD','REACHY_HOSTKEY') {
  $value = [Environment]::GetEnvironmentVariable($name)
  if ([string]::IsNullOrWhiteSpace($value)) {
    $missing += $name
  } else {
    "${name}: set"   # names only, never values
  }
}
if ($missing.Count -gt 0) {
  # Round 2, finding 15: `throw` is terminating; `Write-Error` is not, so the
  # previous version printed a red line and carried straight on into the
  # network calls below with unset credentials.
  throw "missing from the repo-root .env: $($missing -join ', ') -- add them before deploying"
}
```

Round 2, finding 15 fixed two things in this block. `"$name: set"` is a parser
error, not a variable: PowerShell reads `$name:` as a scope-qualified variable
(`$env:`, `$script:`) and fails at parse time — so the previous version could not
run at all, which means the deployment could not start. `"${name}: set"` is the
correct delimiter. And `Write-Error` is a **non-terminating** error by default,
so even once it parsed, a missing variable printed a warning and execution
continued into `Invoke-RestMethod` and `pscp` with an empty password. Missing
keys are collected and a single `throw` fires **before any network request**.

Expected: four `set` lines and no throw. `REACHY_HOSTKEY` is the SHA256
fingerprint that Task 14 moved out of the tracked skill file; if it is absent,
get it once with an interactive `plink` session and paste it into the gitignored
`.env` — do not disable host-key checking, and do not put it back into a tracked
file.

Now the version gate:

```powershell
Invoke-RestMethod "http://$($env:REACHY_HOST):8000/update/install-source"
```

Expected: a daemon on the **1.10.0rc line or newer**. Note there is no `/api`
prefix on this route. If the daemon is below the floor pinned in
`reachy_companion/pyproject.toml` (`reachy-mini>=1.10.0rc2`), **STOP and report** —
`check_and_sync_apps_venv_sdk()` force-syncs the apps venv on every daemon boot,
so a daemon below the floor makes the app undeployable by any app-level means,
and upgrading the daemon is not authorised in this task.

- [ ] **Step 5: Transfer the wheel**

Every `pscp` and `plink` invocation in this task carries `-batch` and
`-hostkey` (review finding 20). Without `-hostkey` in batch mode the connection
fails on an unknown host key; without `-batch` it hangs on the prompt.

Round 2, finding 16: the wheel is transferred to **one fixed remote path**,
`/tmp/reachy_companion_deploy.whl`, so the install step names a file rather than
matching a pattern against whatever `/tmp` accumulated from previous deploys:

```powershell
cd C:\Project\Reachy-mini
if (-not $env:HANOVA_WHEEL) { throw "Step 3 did not run; \$env:HANOVA_WHEEL is unset" }
# Remove any wheel a previous deployment left behind, then send exactly ours.
plink -batch -hostkey $env:REACHY_HOSTKEY -pw $env:REACHY_SSH_PASSWORD `
  "$($env:REACHY_SSH_USER)@$($env:REACHY_HOST)" `
  "rm -f /tmp/reachy_companion_deploy.whl /tmp/reachy_companion-*.whl; ls /tmp/*.whl 2>/dev/null | wc -l"
pscp -batch -hostkey $env:REACHY_HOSTKEY -pw $env:REACHY_SSH_PASSWORD `
  "$env:HANOVA_WHEEL" `
  "$($env:REACHY_SSH_USER)@$($env:REACHY_HOST):/tmp/reachy_companion_deploy.whl"
```

Expected: the `wc -l` prints `0` (no stale wheels left in `/tmp`), and the
transfer reports one file.

- [ ] **Step 6: Back up instance state — unique directory, manifest, no listing**

Review finding 19: a fresh backup directory per deployment, a manifest that
records absences explicitly, and **no recursive listing** of the credentials
directory. Run it as one complete, copy-pasteable command:

```powershell
$remote = @'
set -e
INST=$(/venvs/apps_venv/bin/python -c "import reachy_companion, pathlib; print(pathlib.Path(reachy_companion.__file__).parent)")
STAMP="$(date -u +%Y%m%dT%H%M%SZ)-$$"
BACKUP="/tmp/reachy_companion_backup/$STAMP"
mkdir -p "$BACKUP" && chmod 700 "$BACKUP"
[ -z "$(ls -A "$BACKUP")" ] || { echo "FATAL: backup dir not empty"; exit 1; }
: > "$BACKUP/manifest.txt"
for NAME in .env persona.md memory.v1.json faces.v1.json google-oauth.json nas-video-index.json; do
  if [ -e "$INST/$NAME" ]; then
    cp -a "$INST/$NAME" "$BACKUP/$NAME"
    echo "file $NAME $(stat -c '%a %s' "$INST/$NAME")" >> "$BACKUP/manifest.txt"
  else
    echo "absent $NAME" >> "$BACKUP/manifest.txt"
  fi
done
if [ -d "$INST/google-workspace-mcp" ]; then
  cp -a "$INST/google-workspace-mcp" "$BACKUP/google-workspace-mcp"
  echo "dir google-workspace-mcp $(find "$INST/google-workspace-mcp" -type f | wc -l) files" >> "$BACKUP/manifest.txt"
else
  echo "absent google-workspace-mcp" >> "$BACKUP/manifest.txt"
fi
echo "BACKUP=$BACKUP"
cat "$BACKUP/manifest.txt"
'@
plink -batch -hostkey $env:REACHY_HOSTKEY -pw $env:REACHY_SSH_PASSWORD `
  "$($env:REACHY_SSH_USER)@$($env:REACHY_HOST)" $remote
```

Copy the printed `BACKUP=` path into the deploy notes; Step 8 needs it exactly,
never as a glob. Every `absent` line in the manifest is a **recorded** absence,
which is what the Verification Gate asks for. On this first D-018 deploy all
three new files are expected to be absent. The manifest names no credential and
lists no account addresses.

- [ ] **Step 7: Install into the shared apps venv (two-step, never bare `--force-reinstall`)**

Round 2, finding 16: both commands install **the one fixed path** Step 5 wrote,
never a glob that a stale wheel could win.

```powershell
$remote = @'
set -e
WHEEL=/tmp/reachy_companion_deploy.whl
[ -f "$WHEEL" ] || { echo "FATAL: $WHEEL is missing; re-run Step 5"; exit 1; }
/venvs/apps_venv/bin/python -m pip install --force-reinstall --no-deps "$WHEEL"
/venvs/apps_venv/bin/python -m pip install "$WHEEL"
/venvs/apps_venv/bin/python -c "import yt_dlp, imageio_ffmpeg, smbclient; print(yt_dlp.version.__version__); print(imageio_ffmpeg.get_ffmpeg_exe())"
/venvs/apps_venv/bin/python -c "from importlib.metadata import version; print('installed reachy_companion', version('reachy_companion'))"
rm -f "$WHEEL"
'@
plink -batch -hostkey $env:REACHY_HOSTKEY -pw $env:REACHY_SSH_PASSWORD `
  "$($env:REACHY_SSH_USER)@$($env:REACHY_HOST)" $remote
```

Confirm the printed `installed reachy_companion <version>` matches the version in
`$env:HANOVA_WHEEL_NAME`. If it does not, a stale artifact won — stop, clear
`/tmp`, and redo Steps 3 and 5.

The second command is what pulls `yt-dlp`, `imageio-ffmpeg` and `smbprotocol`.
The third proves the ffmpeg binary really shipped inside the aarch64 wheel and is
executable on this device — without it, `play_music` cannot produce an mp3 and
both gags are dead. Never run a bare `pip install --force-reinstall` on the
wheel: it would reinstall `reachy-mini`, whose linux `PyGObject` pin has no
wheels and would trigger a forbidden source build.

- [ ] **Step 8: Restore instance state — conditionally, from the manifest**

Set `$BACKUP` to the exact path Step 6 printed. Review finding 19: only what the
manifest recorded is restored, an absence stays an absence, permissions are
reasserted, and nothing recursive is listed.

```powershell
$backup = '<paste the BACKUP= path from Step 6>'
$remote = @"
set -e
BACKUP='$backup'
INST=`$(/venvs/apps_venv/bin/python -c "import reachy_companion, pathlib; print(pathlib.Path(reachy_companion.__file__).parent)")
[ -f "`$BACKUP/manifest.txt" ] || { echo "FATAL: no manifest; refusing to restore"; exit 1; }
while read -r KIND NAME REST; do
  case "`$KIND" in
    file) [ -e "`$BACKUP/`$NAME" ] && cp -a "`$BACKUP/`$NAME" "`$INST/`$NAME" || echo "MISSING in backup: `$NAME" ;;
    dir)  [ -d "`$BACKUP/`$NAME" ] && cp -a "`$BACKUP/`$NAME" "`$INST/`$NAME" || echo "MISSING in backup: `$NAME" ;;
    absent) echo "was absent before this deploy, not restored: `$NAME" ;;
  esac
done < "`$BACKUP/manifest.txt"
[ -d "`$INST/google-workspace-mcp" ] && chmod 700 "`$INST/google-workspace-mcp"
[ -d "`$INST/google-workspace-mcp" ] && find "`$INST/google-workspace-mcp" -type f -exec chmod 600 {} +
for NAME in .env google-oauth.json nas-video-index.json; do [ -f "`$INST/`$NAME" ] && chmod 600 "`$INST/`$NAME"; done
ls -l "`$INST" | grep -v '^total'
"@
plink -batch -hostkey $env:REACHY_HOSTKEY -pw $env:REACHY_SSH_PASSWORD `
  "$($env:REACHY_SSH_USER)@$($env:REACHY_HOST)" $remote
```

Then verify the two JSON stores read back with real counts, exactly as the skill
requires:

```powershell
$remote = @'
/venvs/apps_venv/bin/python - <<'"'"'PY'"'"'
import json, pathlib, reachy_companion
inst = pathlib.Path(reachy_companion.__file__).parent
for name, key in (("memory.v1.json", "facts"), ("faces.v1.json", "faces")):
    path = inst / name
    if not path.is_file():
        print(f"{name}: absent"); continue
    data = json.loads(path.read_text(encoding="utf-8"))
    print(f"{name}: {len(data.get(key, []))} records")
PY
'@
plink -batch -hostkey $env:REACHY_HOSTKEY -pw $env:REACHY_SSH_PASSWORD `
  "$($env:REACHY_SSH_USER)@$($env:REACHY_HOST)" $remote
```

- [ ] **Step 8b: Deploy the reviewed persona — do NOT leave the restored one (finding 9)**

**This is the step that makes the whole of Task 14 reach the robot.** The
instance `persona.md` overrides the built-in profile, so Step 8 having just
restored the *old* instance persona means the new routing, the tri-state
`home_status_unknown` behaviour, the confirmation etiquette and the two declared
non-goals are all still unreachable on the device. The old copy stays in the
backup directory as the rollback; the repo copy is what runs.

**Round 2, finding 7: deploy the committed blob, not the working-tree file.**
The previous version sent `persona.md` straight out of the working tree. That is
the copy the user's own edits live in — and the whole point of the Task 0 stash
is that *no step of this plan* is allowed to ship them. Materialise the file from
`git show HEAD:persona.md` into a temporary path **outside the repo** and send
that. It is by construction the reviewed, committed content and nothing else.

**Round 3, finding 5: git writes that file, not a PowerShell pipeline.** The
previous version ran `git show HEAD:persona.md | Out-File -FilePath $staged
-Encoding utf8 -NoNewline`. PowerShell hands `Out-File` git's stdout as an
**array of lines**, and `-NoNewline` joins them with nothing in between — so the
"materialised blob" was the entire persona collapsed onto **one line**. The
failure is silent by construction: we hash *that* file and compare it with the
robot's copy of *that same file*, so the two hashes agree perfectly while the
persona the robot loads is a single unreadable line. The redirect is done by
`cmd.exe`, which hands git's stdout to the file byte for byte.

```powershell
cd C:\Project\Reachy-mini
git rev-parse --abbrev-ref HEAD    # must print main (merged in Step 1c)
git stash list                     # the user's baseline patch is STILL stashed

# The mainline blob, materialised outside the repository, byte for byte.
$staged = Join-Path $env:TEMP "hanova-persona-mainline.md"
cmd /c "git show HEAD:persona.md > `"$staged`""

# Sanity before it goes anywhere: a one-line file is the -NoNewline corruption
# round 3 finding 5 was raised about, and a persona is never one line.
$lineCount = (Get-Content $staged).Count
"mainline persona lines: $lineCount"
if ($lineCount -lt 20) { throw "persona.md materialised as $lineCount line(s); the redirect is broken" }

$localHash = (Get-FileHash -Algorithm SHA256 $staged).Hash.ToLower()
"mainline persona sha256: $localHash"

pscp -batch -hostkey $env:REACHY_HOSTKEY -pw $env:REACHY_SSH_PASSWORD `
  "$staged" "$($env:REACHY_SSH_USER)@$($env:REACHY_HOST):/tmp/persona.md"

$remote = @'
set -e
INST=$(/venvs/apps_venv/bin/python -c "import reachy_companion, pathlib; print(pathlib.Path(reachy_companion.__file__).parent)")
install -m 600 /tmp/persona.md "$INST/persona.md"
rm -f /tmp/persona.md
sha256sum "$INST/persona.md" | cut -d" " -f1
# The routing tokens that prove the Task 14 content is the content that landed.
# Round 3, finding 7: the list stays on ONE line. The previous version wrapped it
# with a literal `\n`, which the shell reads as an eleventh token and reports as
# a false TOKEN MISSING against an expected count of ten.
for TOKEN in away_from_home home_status_unknown needs_confirmation unavailable retryable action_in_flight body_too_long play_music nas_skip self_destruct; do
  grep -q "$TOKEN" "$INST/persona.md" && echo "token ok: $TOKEN" || echo "TOKEN MISSING: $TOKEN"
done
'@
plink -batch -hostkey $env:REACHY_HOSTKEY -pw $env:REACHY_SSH_PASSWORD `
  "$($env:REACHY_SSH_USER)@$($env:REACHY_HOST)" $remote
```

Expected: the on-device SHA256 **equals** `$localHash`, exactly **ten** `token
ok` lines and **no** `TOKEN MISSING` line — the six routing tokens plus the three
round-2 statuses (`retryable`, `action_in_flight`, `body_too_long`) and
`self_destruct`. A count other than ten now means a real missing token rather
than the shell-quoting artefact round 3 finding 7 removed. If the hashes differ,
the file did not land — stop and fix it, because every persona-dependent row of
the wake test below would otherwise be measuring the previous persona.

Then remove the materialised copy, so nothing later can pick it up by accident:

```powershell
Remove-Item $staged -Force
```

If the operator had made robot-only edits to the instance persona, merge them
into the repo copy **on the workstation**, re-run the Task 14 gate, and redeploy
— do not hand-edit the file on the robot, or the next deploy loses it again.

- [ ] **Step 9: Config checklist — seed the new keys with placeholders**

Append the D-018 block to the robot's `.env` with **placeholder values only**, so
the first boot exercises the fully-disabled path. Do not paste real
identifiers in this step.

```powershell
$remote = @'
set -e
INST=$(/venvs/apps_venv/bin/python -c "import reachy_companion, pathlib; print(pathlib.Path(reachy_companion.__file__).parent)")
grep -q "HANOVA_TZ" "$INST/.env" || cat >> "$INST/.env" <<'"'"'ENVEOF'"'"'

# --- HomeAssistant-Nova ported capabilities (D-018) ---
HANOVA_TZ=Asia/Taipei
HANOVA_HA_SCRIPT_YOUTUBE=
HANOVA_HA_SCRIPT_IMAGE_URL=
HANOVA_HA_SCRIPT_VIDEO_URL=
HANOVA_CAST_ENTITY=
HANOVA_MEDIA_HTTP_BASE=
HANOVA_SELF_DESTRUCT_YT_ID=
HANOVA_MAD_LAUGH_YT_ID=
GOOGLE_CREDS_DIR=
HANOVA_GOOGLE_ACCOUNT=
HANOVA_GCAL_CALENDAR_ID=
HANOVA_GTASKS_LIST_ID=
HERMES_DRIVE_SECRETS=
HANOVA_DRIVE_PARENT_ID=
HANOVA_NOTION_TOKEN=
HANOVA_NOTION_DATA_SOURCE_ID=
HANOVA_SMTP_USER=
HANOVA_SMTP_APP_PASSWORD=
HANOVA_NAS_HOST=
HANOVA_NAS_USER=
HANOVA_NAS_PASSWORD=
HANOVA_NAS_SHARE=
HANOVA_NAS_SUBPATH=
HANOVA_NAS_CAST_SUBPATH=
HANOVA_NAS_INDEX_PATH=
HANOVA_HOME_NETWORKS=
ENVEOF
chmod 600 "$INST/.env"
grep -c "^HANOVA_\|^GOOGLE_CREDS_DIR\|^HERMES_DRIVE_SECRETS" "$INST/.env"
'@
plink -batch -hostkey $env:REACHY_HOSTKEY -pw $env:REACHY_SSH_PASSWORD `
  "$($env:REACHY_SSH_USER)@$($env:REACHY_HOST)" $remote
```

Expected: `26` — the 19 keys the first draft seeded, plus the three
`HANOVA_HA_SCRIPT_*` names and the three NAS share/subpath keys that review
finding 6 turned from silent defaults into required configuration, plus
`HANOVA_HOME_NETWORKS`, which round 2 finding 3 made the only thing that can
justify an `away_from_home` answer.

Then hand the operator this checklist — the values are theirs to paste over SSH,
and **none of them may ever be written into this repository**:

| Key | What the operator supplies | What it unlocks |
|---|---|---|
| `HANOVA_HA_SCRIPT_YOUTUBE` | the scripts.yaml entry name that launches a YouTube id | `play_video` |
| `HANOVA_HA_SCRIPT_IMAGE_URL` | the entry name that casts an image URL | `show_on_tv` |
| `HANOVA_HA_SCRIPT_VIDEO_URL` | the entry name that casts a video URL | all `nas_*` casting |
| `HANOVA_CAST_ENTITY` | *optional* — the TV's `media_player.*` entity id, forwarded to those scripts | targeting one TV of several |
| `HANOVA_MEDIA_HTTP_BASE` | `http://<robot's reserved LAN IP>:7860` | `show_on_tv`, all NAS casting |
| `HANOVA_SELF_DESTRUCT_YT_ID`, `HANOVA_MAD_LAUGH_YT_ID` | the two gag clip ids | `self_destruct`, `mad_laugh` |
| `GOOGLE_CREDS_DIR` | `<instance>/google-workspace-mcp` **and the writable `<account>.json` copied into it** | all calendar and task tools |
| `HANOVA_GOOGLE_ACCOUNT` | the Google account address | all calendar and task tools |
| `HANOVA_GCAL_CALENDAR_ID` | the calendar id | the three calendar tools |
| `HANOVA_GTASKS_LIST_ID` | the Tasks list id | `task_add` only |
| `HERMES_DRIVE_SECRETS` | `<instance>/google-oauth.json` **and the file copied in, mode 600** | the three Drive tools |
| `HANOVA_DRIVE_PARENT_ID` | the Drive folder id | the three Drive tools |
| `HANOVA_NOTION_TOKEN`, `HANOVA_NOTION_DATA_SOURCE_ID` | the integration token and data-source id | `notion_add` |
| `HANOVA_SMTP_USER`, `HANOVA_SMTP_APP_PASSWORD` | the sending address and its app password | `email_send` |
| `HANOVA_NAS_INDEX_PATH` | `<instance>/nas-video-index.json` **and the index file copied in** | `nas_video_query` (alone) |
| `HANOVA_NAS_HOST`, `HANOVA_NAS_USER`, `HANOVA_NAS_PASSWORD` | the NAS host and SMB credentials | the three NAS casting tools |
| `HANOVA_NAS_SHARE`, `HANOVA_NAS_SUBPATH`, `HANOVA_NAS_CAST_SUBPATH` | the share and the two folders inside it (round 2, finding 12: `HANOVA_NAS_SUBPATH` now bounds the original index paths, so it is a real check rather than a dead switch) | the three NAS casting tools |
| `HANOVA_HOME_NETWORKS` | *strongly recommended* — the house LAN in CIDR form, e.g. `<a.b.c.0>/24`, comma separated for more than one | round 2, finding 3: this is the **only** thing that lets Reachy say "I am away from home". Leave it blank and every off-home situation answers `home_status_unknown` instead, which is honest but less useful |

Note the granularity, which is what review finding 10 bought: the operator can
enable `nas_video_query` with an index file alone and leave SMB unconfigured, or
enable `play_video` without ever setting a LAN base.

Also confirm with the operator, before the enabled pass: the robot must hold a
**reserved or static LAN IP** (or a name the TV resolves), and the TV must not be
isolated from the robot by AP isolation or a VLAN boundary. If either is untrue,
`HANOVA_MEDIA_HTTP_BASE` cannot work and `show_on_tv` plus all NAS casting stay
unavailable — record that as the blocker rather than marking the item complete.

- [ ] **Step 10: Preload assets and start the app (disabled pass)**

```powershell
cd C:\Project\Reachy-mini
pscp -batch -hostkey $env:REACHY_HOSTKEY -pw $env:REACHY_SSH_PASSWORD `
  scripts\preload_assets.py "$($env:REACHY_SSH_USER)@$($env:REACHY_HOST):/tmp/"
plink -batch -hostkey $env:REACHY_HOSTKEY -pw $env:REACHY_SSH_PASSWORD `
  "$($env:REACHY_SSH_USER)@$($env:REACHY_HOST)" "/venvs/apps_venv/bin/python /tmp/preload_assets.py"

Invoke-RestMethod -Method Get  "http://$($env:REACHY_HOST):8000/api/apps/list-available/installed"
Invoke-RestMethod -Method Post "http://$($env:REACHY_HOST):8000/api/apps/start-app/reachy_companion"
```

- [ ] **Step 11: Verify the seven family verdict lines and the exact tool set (disabled pass)**

Review finding 21 rewrote this step twice over. First, the arithmetic: the loader
**automatically appends two system tools**, so the pre-existing registry is 17
(15 from the profile plus those 2), and 17 + 22 = **39**, not 37. Second, and
more important, a count is weak evidence anyway — `journalctl` spans multiple
startups, so `grep -c` happily counts three boots' worth of registrations and
reports a number that means nothing. The primary check is therefore the **exact
set of 22 names within one startup boundary**, with 39 as a secondary sanity
check.

```powershell
$remote = @'
set -e
# Everything since the LAST app start, so no previous boot can be counted.
BOOT=$(journalctl --user -n 20000 --no-pager | grep -n "hanova family google-workspace" | tail -1 | cut -d: -f1)
journalctl --user -n 20000 --no-pager | tail -n +"$BOOT" > /tmp/hanova-startup.log
echo "--- family verdicts ---"
grep -E "hanova family" /tmp/hanova-startup.log
echo "--- media mount ---"
grep -E "hanova media served" /tmp/hanova-startup.log
echo "--- persona source ---"
grep -E "persona:" /tmp/hanova-startup.log | tail -1
echo "--- registered tool count (secondary) ---"
grep -c "tool registered:" /tmp/hanova-startup.log
echo "--- the 22 ported names (primary) ---"
for T in play_music stop_music play_video show_on_tv nas_video_query play_nas_video \
         nas_play_folder nas_skip calendar_add calendar_delete calendar_list task_add \
         task_complete task_delete task_list notion_add drive_list drive_trash \
         drive_upload email_send self_destruct mad_laugh; do
  grep -q "tool registered: $T\b" /tmp/hanova-startup.log && echo "ok   $T" || echo "MISS $T"
done
'@
plink -batch -hostkey $env:REACHY_HOSTKEY -pw $env:REACHY_SSH_PASSWORD `
  "$($env:REACHY_SSH_USER)@$($env:REACHY_HOST)" $remote
```

Expected, with every key still blank:

```
hanova family google-workspace: disabled (GOOGLE_CREDS_DIR)
hanova family drive: disabled (HERMES_DRIVE_SECRETS)
hanova family notion: disabled (HANOVA_NOTION_TOKEN)
hanova family email: disabled (HANOVA_SMTP_USER)
hanova family nas: disabled (HANOVA_NAS_INDEX_PATH)
hanova family media-cast: disabled (HANOVA_HA_SCRIPT_YOUTUBE)
hanova family music: partial (2/4 tools ready; first blocker HANOVA_SELF_DESTRUCT_YT_ID)
```

**The music line is `partial`, not `enabled`** (review finding 23). `play_music`
and `stop_music` need only the installed wheels, but `self_destruct` and
`mad_laugh` each need their own clip id, which is blank on this pass. That is the
accurate statement of the music/dependency exception: music is the one family
whose availability comes from installed wheels rather than configuration, and
even it is not fully enabled without the two gag ids.

Also confirm in the same excerpt:
- `hanova media served at /hanova-media (base URL configured: True, kinds: 4)` — the Task 3 mount came up. The line no longer prints the cache directory (round 2, finding 6: it is the instance path). If it is absent, `show_on_tv` and all NAS casting will report `unavailable (HANOVA_MEDIA_MOUNT)`, which is correct behaviour but means casting is blocked.
- **22 `ok` lines and zero `MISS`** — this is the primary evidence.
- The secondary count is **39** (15 profile entries + 2 auto-appended system tools + 22 ported). A different number is worth investigating but is not on its own a failure, because it is a `journalctl` window artefact; the 22-name list is what decides.
- `persona: instance persona.md` — Step 8b took. If it says `persona: built-in locked profile`, the instance persona did not land; fix it before going on, because every persona-dependent row below would be measuring the wrong file.

Then prove the disabled path end to end: ask Reachy (by voice, via the wake below,
or through the console) to add a calendar event and confirm the answer is a plain
"that is not set up yet" and the log shows the tool returned
`unavailable` with `GOOGLE_CREDS_DIR` as the reason. **This is the R5 acceptance
check**, and the reason field is what makes it actionable.

- [ ] **Step 12: Enabled pass — operator fills the keys, restart, re-read the log**

After the operator pastes real values and copies the three credential files in
(Step 9's table), restart the app and re-read the verdict lines:

```powershell
Invoke-RestMethod -Method Post "http://$($env:REACHY_HOST):8000/api/apps/stop-current-app"
Invoke-RestMethod -Method Post "http://$($env:REACHY_HOST):8000/api/apps/start-app/reachy_companion"
```

```powershell
plink -batch -hostkey $env:REACHY_HOSTKEY -pw $env:REACHY_SSH_PASSWORD `
  "$($env:REACHY_SSH_USER)@$($env:REACHY_HOST)" `
  'journalctl --user -n 500 --no-pager | grep "hanova family"'
```

Record which families flipped to `enabled` or `partial`. Any family the operator
chose not to configure stays `disabled` with its reason — that is a valid
outcome, not a failure, and the corresponding `feature_list.json` item records
the blocker. A `partial` line is the most informative of the three: it names
which prerequisite is still missing for the tools that are not yet ready.

Verify the media route is reachable from another machine on the LAN — this is the
one thing a Chromecast will do that nothing else tests. **Probe a seeded file,
not the bare directory** (review finding 11): a 404 on `/hanova-media/` is what
`StaticFiles` returns for a directory listing whether the mount works or not, so
it proves nothing. Seed a known file first, then fetch it three ways — the same
three a Chromecast uses:

```powershell
# 1. Seed a tiny probe file into the served cache.
$remote = @'
set -e
INST=$(/venvs/apps_venv/bin/python -c "import reachy_companion, pathlib; print(pathlib.Path(reachy_companion.__file__).parent)")
mkdir -p "$INST/hanova_media/nas"
head -c 1024 /dev/urandom > "$INST/hanova_media/nas/_probe.mp4"
echo "seeded $(stat -c '%s' "$INST/hanova_media/nas/_probe.mp4") bytes"
'@
plink -batch -hostkey $env:REACHY_HOSTKEY -pw $env:REACHY_SSH_PASSWORD `
  "$($env:REACHY_SSH_USER)@$($env:REACHY_HOST)" $remote

# 2. Fetch it from THIS machine, over the LAN, exactly as the TV would.
$base = "http://$($env:REACHY_HOST):7860/hanova-media/nas/_probe.mp4"
$head = Invoke-WebRequest $base -Method Head
"HEAD status: $($head.StatusCode); type: $($head.Headers['Content-Type']); length: $($head.Headers['Content-Length'])"
$full = Invoke-WebRequest $base -Method Get
"GET  status: $($full.StatusCode); bytes: $($full.RawContentLength)"
$range = Invoke-WebRequest $base -Headers @{ Range = 'bytes=0-15' }
"RANGE status: $($range.StatusCode); content-range: $($range.Headers['Content-Range'])"

# 3. Remove the probe.
plink -batch -hostkey $env:REACHY_HOSTKEY -pw $env:REACHY_SSH_PASSWORD `
  "$($env:REACHY_SSH_USER)@$($env:REACHY_HOST)" `
  'INST=$(/venvs/apps_venv/bin/python -c "import reachy_companion, pathlib; print(pathlib.Path(reachy_companion.__file__).parent)"); rm -f "$INST/hanova_media/nas/_probe.mp4"'
```

Expected: `HEAD 200` with `video/mp4` and `1024`; `GET 200` with 1024 bytes;
`RANGE 206` with `bytes 0-15/1024`. A connection refused, a timeout, or a 404 on
the seeded file all mean `HANOVA_MEDIA_HTTP_BASE` will never work for the TV —
record it as the blocker for `show_on_tv` and every NAS casting tool rather than
marking them complete.

- [ ] **Step 13: Wake test — run this transcript and record every row**

Wake the robot (antenna) and speak each line in Traditional/Simplified Chinese.
Record the actual spoken reply and the tool trace from the log for each row.
A row passes only when both the behaviour and the log agree.

**Review finding 22 rewrote this table.** The previous version omitted all four
Google Tasks tools, every Drive operation, both gags, and any *successful* gated
email — so it could not support a "22 tools verified" conclusion no matter how it
came out. Every ported tool now appears at least once, and **every one of the
seven gated tools is exercised in both directions**, plus the self-destruct abort branch: the refusal before
confirmation and the execution after it.

Log lines below are **redacted by design** (finding 7): the log shows
`play_music query=<text:14 chars>`, not the query. Confirm the *shape* in the log
and the *content* by what Reachy says and what actually happened in Google,
Notion, Drive, the TV or your inbox.

| # | Say | Expected behaviour | Expected in the log | Tools covered |
|---|---|---|---|---|
| 1 | 「放一首周杰倫的歌」 | Music starts from the **robot's own speaker**, and Reachy names the real track title | `Tool call: play_music query=<text:…>`, then `Music playing on the robot speaker (generation N)` | `play_music` |
| 2 | (while the music plays) 「欸你聽得到我嗎」 | The music **ducks** the moment you start speaking, Reachy answers **without the music underneath her**, then the music **resumes near where it stopped** — not from the beginning, and not on top of her reply | `Music paused for user speech`, then `Music resumed` **after** the reply audio finishes | R7 + finding 1 |
| 3 | 「停」 | Music stops immediately, even if something else is still running | `Tool call: stop_music`, result `status: stopped` | `stop_music` |
| 4 | 「幫我在行事曆加一個明天晚上七點的晚餐」 | Reachy states the real event it created, with the date it used | `Tool call: calendar_add summary=<text:…>`, result carries a real `event_id` | `calendar_add` |
| 5 | 「最近有什麼行程」 | Reachy reads back real events from the calendar, none invented | `Tool call: calendar_list` with a non-zero `count` | `calendar_list` |
| 6 | 「把剛剛那個晚餐刪掉」 | Reachy **reads the event title and date back and asks you to confirm**, and deletes nothing yet | `calendar_delete resolving`, result `status: needs_confirmation`, and **no** `confirmed` line | `calendar_delete` (gate, refusal) |
| 7 | 「對，刪掉」 | Only now is it deleted, and Reachy says which event | `calendar_delete confirmed for <id:…>`, result `status: deleted` | `calendar_delete` (gate, execution) |
| 8 | 「幫我加一個待辦：換濾網」 | The task appears in Google Tasks | `Tool call: task_add title=<text:…>`, real `task_id` | `task_add` |
| 9 | 「我還有什麼待辦」 | Reachy reads back real tasks, grouped by list | `Tool call: task_list`, non-zero `count` | `task_list` |
| 10 | 「把換濾網標成完成」 then 「對」 | First call reads the task back and changes nothing; the second completes it | `task_complete` → `needs_confirmation`, then `task_complete confirmed for <id:…>` | `task_complete` (both directions) |
| 11 | 「把換濾網刪掉」 then 「對」 | Same two-step, then the task is gone | `task_delete` → `needs_confirmation`, then `status: deleted` | `task_delete` (both directions) |
| 12 | 「幫我記一下要買燈泡」 | A row appears in the notes database, and Reachy confirms with the real title | `Tool call: notion_add title=<text:…>`, real `page_id` | `notion_add` |
| 13 | 「雲端硬碟裡有什麼」 | Reachy lists the real folder contents, none invented | `Tool call: drive_list limit=…`, non-zero `count` | `drive_list` |
| 14 | 「幫我拍張照上傳到雲端」 then 「對」 | First call reads the upload back and uploads nothing; the second takes the photo **now** and uploads it. Check the file really appears in Drive | `drive_upload` → `needs_confirmation`, then `drive_upload confirmed as <text:…>`, real `file_id` | `drive_upload` (both directions) |
| 15 | 「把剛剛那張照片丟到垃圾桶」 then 「對」 | Reachy reads back the **real file name it looked up**, not the id it was given; then trashes it | `drive_trash resolving <id:…>` → `needs_confirmation`, then `status: trashed` | `drive_trash` (both directions) |
| 16 | 「把它還原回來」 | Reachy says it cannot restore by voice and tells you to use the Drive trash. **It must not attempt anything** | no tool call at all | non-goal (finding 17) |
| 17 | 「寄封信給我朋友」 (give no address) | Reachy **asks for the address** and sends nothing | `email_send` result `ok: false` | `email_send` (validation) |
| 18 | 「寄給 <your own address>，主旨測試，內容第一句說今天測試成功，第二句說明天再確認一次，副本給 <a second address of yours>」 | Reachy reads back **both** recipients, the subject and the **whole body — both sentences, verbatim** — before sending. A first-line-only read-back is a failure of this row | `email_send` → `needs_confirmation`; the spoken summary names **both** addresses **and contains the second sentence** | `email_send` (gate, full envelope — finding 5 + round 2 finding 4) |
| 18b | 「寄一封很長的信」 then dictate more than ~500 characters | Reachy says the message is too long for it to read back and asks for a shorter one. It must **not** summarise and send | `email_send` result `{"status": "body_too_long"}`; nothing armed | round 2 finding 4 |
| 19 | 「對，寄出去」 | The mail arrives at **both** addresses, and at no others | `email_send confirmed to=1 cc=1`, then `status: sent` | `email_send` (gate, execution) |
| 20 | 「也幫我密件副本給 X」 | Reachy explains it can only send to visible recipients | no BCC anywhere; no tool call carrying one | non-goal (finding 17) |
| 21 | 「在電視上放一段海洋的影片」 | The TV launches the video | `Tool call: play_video query=<text:…>`, then a successful script call | `play_video` |
| 22 | 「電視上畫一隻戴帽子的貓」 | An image appears on the TV | `Tool call: show_on_tv request=<text:…>`, cast URL under `HANOVA_MEDIA_HTTP_BASE` | `show_on_tv` |
| 23 | 「我們以前出去玩的影片有哪些」 | Reachy lists real trips from the index, none invented | `Tool call: nas_video_query filters=0` | `nas_video_query` |
| 24 | 「放那趟旅行的影片」 | The first clip of the trip plays on the TV | `Tool call: play_nas_video <id:…>` | `play_nas_video` |
| 25 | 「從頭放整趟」 | The trip starts from clip one and Reachy says how many are left | `Tool call: nas_play_folder <id:…> (N clips)` | `nas_play_folder` |
| 26 | 「下一段」 | The second clip plays | `Tool call: nas_skip -> <id:…>` | `nas_skip` |
| 27 | 「笑一個壞人的笑」 | The laugh plays on the robot's speaker | `Tool call: mad_laugh` | `mad_laugh` |
| 28 | 「啟動自毀程序」 | Reachy runs the **countdown ritual in character** and plays nothing yet. It must **not** explain that it is a joke | `self_destruct` → `needs_confirmation` | `self_destruct` (gate, refusal) |
| 29 | 「取消自毀」 | Reachy stands down; nothing plays | `self_destruct aborted`, `status: aborted` | `self_destruct` (abort — finding 17) |
| 30 | 「啟動自毀程序」 then 「授權自毀」 | Now the clip plays | `self_destruct authorised`, then playback | `self_destruct` (gate, execution) |
| 31 | (set `HANOVA_HOME_NETWORKS` to the house LAN CIDR and restart; then take the robot off the home LAN) 「在電視上放個影片」 | Reachy says **it is away from home** and cannot touch things in the house. It must not say the TV or the house is broken | `play_video` result `{"status": "away_from_home"}`; the probe logged `outside every declared home network; verdict away` | R4 + round 2 finding 3 |
| 31b | (leave `HANOVA_HOME_NETWORKS` **blank**, robot still off the LAN) 「在電視上放個影片」 | Reachy says it **cannot tell whether it is at home**, and does nothing. Round 2, finding 3: with nothing declared, absence is unprovable, so claiming it would be a guess | `play_video` result `{"status": "home_status_unknown"}`, **not** `away_from_home`; no HA script call at all | round 2 finding 3 |
| 32 | (still away) 「最近有什麼行程」 | The calendar still works — cloud tools never consult the home probe | `Tool call: calendar_list` succeeds | R4 |
| 33 | (back home; temporarily break `HA_TOKEN` in the instance `.env` and restart) 「在電視上放個影片」 | Reachy says it is **not sure whether it is at home and the home system is not answering**, NOT that you are away from home. Nothing is searched and no HA script runs | `play_video` result `{"status": "home_status_unknown"}`; the home probe logged `Home Assistant answered 401; verdict unknown` | finding 12 + round 2 finding 3 |

**Coverage check before you conclude anything.** Rows 1, 3, 4, 5, 8, 9, 12, 13,
21, 22, 23, 24, 25, 26, 27 cover the 15 ungated tools; rows 6–7, 10, 11, 14, 15,
18–19 and 28–30 cover all **seven** gated tools in both directions, plus the
separate self-destruct abort branch (row 29). That is 22 of 22 tools and 7 of 7
gates. Round 2, finding 17: there is no eighth gated tool — earlier wording that
said "eight gated paths" was counting that abort branch as one. If a row could not be run, it is
**blocked**, not passed.

For any family the operator left disabled, record its rows as **blocked** with the
exact missing key, per the Verification Gate: a check that cannot run is recorded
as a blocker, never marked complete. Rows 4–11 require google-workspace; 12
requires notion; 13–16 require drive; 17–20 require email; 21–22 require
media-cast; 23–26 require nas; 27–30 require the two gag clip ids.

Undo row 33's deliberate breakage (restore the real `HA_TOKEN`, restart) before
Step 14.

- [ ] **Step 14: Record the evidence — raw kept out of the repository (finding 7)**

Deployment evidence splits in two. The raw material is full of identifiers — real
calendar titles, the addresses the mail went to, the trip folder names, the
robot's LAN address — and none of it may be committed.

**Untracked (raw).** Put the full `journalctl` excerpts, the wake-test transcript
with what Reachy actually said, and the probe outputs into
`C:\Project\Reachy-mini\artifacts\deploy-2026-08-21\`. Confirm first that
`artifacts/` is gitignored; add it to `.gitignore` **in this commit** if it is
not. Nothing in this directory is ever staged.

**Tracked (redacted).** Only statuses, counts and key names go into the repo.

Update `C:\Project\Reachy-mini\feature_list.json`: for each of the nine
`HANOVA-*` items, set `state` to `"verified"` or `"blocked"`, and write an
`evidence` string built **only** from: the family verdict line (which names keys,
not values), the wake-test row numbers that passed, the count of registered
ported names (`22/22`), and the probe status codes. For example — this is the
level of detail permitted:

> `"evidence": "2026-08-21 on-robot: family verdict 'hanova family google-workspace: enabled'; wake rows 4,5,6,7,8,9,10,11 passed; calendar_delete refused before confirmation and executed after; 22/22 ported tools registered in one startup boundary; secondary count 39."`

Not permitted: an event title, an address, a file name, a trip name, a LAN
address, a page id, or a pasted log line containing any of them. Set
`next_action` to the exact missing **key name** for anything blocked.

Update `C:\Project\Reachy-mini\progress.md`: replace the "**Not yet run on the
robot**" sentence added in Task 14 with the measured outcome — the seven family
verdict lines from both passes, the 22/22 ported-name result and the 39
secondary count, which wake-test rows passed, and any blocker with its missing
key. Same redaction rule; point at the untracked artifact directory for detail.

- [ ] **Step 15: Leave the robot in a known state, scan, and commit to `main`**

Put the robot to sleep or stop the app deliberately, and say which in the notes:

```powershell
Invoke-RestMethod -Method Post "http://$($env:REACHY_HOST):8000/api/apps/stop-current-app"
```

Stage, then run the Task 14 Step 9b labelled-value scan over the staged
**post-image** (the scan reads `git show :<path>` and compares against
`git show HEAD:<path>` — never the diff) — this is the run that matters most,
because this task's evidence is where a real identifier is most likely to slip
in:

```bash
cd /c/Project/Reachy-mini
git rev-parse --abbrev-ref HEAD   # must print main (merged in Step 1c)
git add feature_list.json progress.md .gitignore
git diff --cached --stat          # exactly these paths, nothing else
```

Run the scan from Task 14 Step 9b now. Expected: `clean` (a `pre-existing` block
is acceptable and must be recorded; a `LEAK:` line is not). If it reports a leak,
unstage, redact, re-run. Then:

```bash
git commit -m "chore(hanova): on-robot deployment evidence for the D-018 port"
git status --short --branch
git log --oneline -3
```

- [ ] **Step 15b: Give the user their `persona.md` patch back, and prove it (round 2, finding 7)**

This is the last step of the plan and it is **not optional**. The user's
pre-existing `persona.md` change has been stashed since Task 0, Step 2 — out of
every commit, out of the merge, and out of the deployed file, all on purpose.
Now it comes back, and the plan verifies that what came back is what went in.

**Round 3, finding 5 rewrote this step too.** Three defects: it ran `git stash
pop`, which **drops the entry before anything has been verified** — a failed
verification then had no stash left to recover from; it verified only the
*added* lines, so a line the user had **deleted** could quietly fail to come back
and still pass; and the conflict fallback left the stash in place *and* left the
applied patch **staged**, so the user's work would have been committed by the
next `git commit` that came along. The restore is now `apply` → verify → `drop`,
the identity covers deletions, and nothing is left in the index.

```powershell
cd C:\Project\Reachy-mini
git rev-parse --abbrev-ref HEAD    # main

$patch   = Join-Path $env:TEMP "hanova-persona-baseline.patch"
$patchU0 = Join-Path $env:TEMP "hanova-persona-baseline.u0.patch"

# Task 0 recorded `patch bytes: 0` when the user had no pending change. Print the
# verdict rather than silently skipping a step whose whole job is not skipping.
if (-not (Test-Path $patch) -or (Get-Item $patch).Length -eq 0) {
  "no user persona patch was captured in Task 0; nothing to restore (recorded, not assumed)"
  "STOP: copy that line into the notes and skip the remainder of Step 15b."
}

# Resolve the entry by its MESSAGE, not by position: another stash may have been
# created since Task 0 and stash@{0} would then be the wrong one.
$stashRef = (git stash list --format="%gd %gs" |
             Where-Object { $_ -match 'hanova-port: user persona\.md baseline' } |
             Select-Object -First 1)
if (-not $stashRef) { throw "the baseline stash is gone -- restore by hand from $patch and do NOT delete it" }
$stashRef = ($stashRef -split ' ')[0]
"restoring from $stashRef"

# APPLY, never POP. A pop drops the entry before a single check has run.
git stash apply $stashRef
if ($LASTEXITCODE -ne 0) {
  # Conflict fallback -- our block was appended near where the user was editing.
  # Clean the file back to the committed state first so no conflict markers
  # survive, then apply the saved patch.
  "stash apply conflicted; falling back to the saved patch"
  git checkout -- persona.md
  git apply --3way -- $patch
  if ($LASTEXITCODE -ne 0) {
    git checkout -- persona.md
    throw "neither the stash nor $patch applied cleanly -- the stash is UNTOUCHED; resolve by hand"
  }
}

# `git apply --3way` resolves through the index, so it can leave the result
# STAGED. The user's work must come back exactly as it was: unstaged.
git restore --staged -- persona.md
git status --short -- persona.md            # expect ' M persona.md' -- a leading space
git diff --cached --stat -- persona.md      # must print NOTHING
```

Then verify against the baseline **identity**, not a hash of the added lines.
`git patch-id --stable` covers the complete payload — every `+` line and every
`-` line — and the zero-context patches make it immune to the line-number and
context shifts our appended block causes:

```powershell
function Get-PatchId([string]$patchFile) {
  if (-not (Test-Path $patchFile) -or (Get-Item $patchFile).Length -eq 0) { return "" }
  $line = cmd /c "git patch-id --stable < `"$patchFile`""
  if (-not $line) { throw "git patch-id produced nothing for $patchFile" }
  return ($line -split ' ')[0]
}

$restoredU0 = Join-Path $env:TEMP "hanova-persona-restored.u0.patch"
git diff -U0 --output="$restoredU0" -- persona.md     # git writes it; never a -NoNewline pipe

$baselineId = Get-PatchId $patchU0
$restoredId = Get-PatchId $restoredU0
"baseline stable patch-id: $baselineId"
"restored stable patch-id: $restoredId"
if (-not $baselineId -or $baselineId -ne $restoredId) {
  throw "the user's persona.md patch did not come back intact -- the stash is STILL THERE ($stashRef) and both patches are still on disk; do NOT clean up"
}
"user persona patch restored and verified (additions AND deletions)"
```

Expected: the two ids match, nothing is staged, and `git status --short` once
again shows the **exact** Task 0, Step 1 baseline paths — `persona.md` included,
unstaged, and containing only the user's own change plus the committed port
block.

Only once that passes — **this is the first and only place the stash is
dropped**:

```powershell
git stash drop $stashRef
git stash list            # must now be empty of the hanova entry
Remove-Item $patch -Force
Remove-Item $patchU0 -Force
Remove-Item $restoredU0 -Force
Remove-Item (Join-Path $env:TEMP "hanova-secret-values.txt") -Force
```

Report any remaining dirty files — the Task 0 baseline paths should be the only
ones left, `persona.md` among them. The feature branch has already been merged
(Step 1c) and may now be deleted with `git branch -d feat/ha-nova-port` once the
merge commit is confirmed present.

---

## Review log

**Round 1 (Codex, 2026-08-21): 24 findings — 21 accepted as stated, 3 accepted with scoped rulings (7: tool-surface redaction only; 17: declared non-goals + in-character self_destruct gate; 24: feature branch + merge-before-deploy). 0 rejected.**

| # | Severity | Finding, in one line | Disposition | Where the fix landed |
|---|---|---|---|---|
| 1 | CRITICAL | Music resumed on `response.done`, which does not mean the audio finished | Accepted as stated | Task 5 — new `hanova/audio_drain.py`, turn-phase tracking in `hanova/music_hooks.py`, `console.play_loop` reports drain, and the pause is scheduled rather than awaited in the event receiver |
| 2 | CRITICAL | Player released its lock across I/O; the SDK's `play_sound` swallowed failures | Accepted as stated | Task 4 — serialized `asyncio.Lock` state machine with generation tokens, `daemon_play_sound`/`daemon_stop_sound` check the HTTP status, plus play-vs-stop, resume-vs-stop, play-vs-play and failed-stop tests |
| 3 | CRITICAL | `GATE` was process-global and never reset, so a confirmation crossed sessions | Accepted as stated | Task 2 — epoch-stamped `PendingAction`, `begin_session`/`end_session`; wired at both ends in Task 5; cross-session tests in Tasks 2 and 7 |
| 4 | IMPORTANT | `take()` spent the authorisation before the work ran; schemas forced a resupply | Accepted as stated | Task 2 — `claim`/`complete`/`release`/`abort`; applied in Tasks 7–12 with `required: []`; enforced by two new integration tests in Task 14 |
| 5 | CRITICAL | Email read-back named only the primary recipient; CC and body went unconfirmed | Accepted as stated | Task 11 — `normalize_recipients`, `body_digest`, `preview`, a summary carrying To/CC/subject/preview/digest, and a test that the sent recipient set equals the summarised set |
| 6 | CRITICAL | Manifest-derived defaults, a real private sentinel in a test, a deploy skill holding live identifiers | Accepted as stated | Task 1 (NAS share/subpaths and the three HA script names now default to empty), Task 14 (shape-based scan, synthetic `SENTINEL_PRIVATE_x7` only, Step 9a strips the skill's IP/username/fingerprint, Step 9b scans the staged diff against the gitignored `.env`) |
| 7 | CRITICAL | Tool logs recorded queries, titles, ids, paths and error bodies | **Accepted, scoped by the controller** — new-tool surface only; the framework's existing model-visible logging is explicitly **out of scope** and is not redesigned | Task 1 — `hanova/redact.py` + `test_hanova_redact.py`; used by all 22 tools for logs *and* error surfaces; caplog sentinel tests in Tasks 1, 2, 4, 6, 7, 8, 9, 10, 11, 13; Task 15 splits raw evidence into a gitignored artifact directory |
| 8 | CRITICAL | Bash `<<<` in PowerShell; whole-project resolve; `--output-file -` writes a file named `-` | Accepted as stated | Task 1 Step 6 and Task 15 Step 2 — PowerShell here-string piped to `uv pip compile -` with an explicit temp path outside the repo; the reviewer's own successful resolution (yt-dlp 2026.8.19, imageio-ffmpeg 0.6.0 aarch64, smbprotocol 1.17.0 + cryptography/pyspnego/cffi) recorded as evidence |
| 9 | CRITICAL | Task 15 restored the robot's old `persona.md` and never deployed the new one | Accepted as stated | Task 15 Step 8b — old copy kept as the backup, repo persona deployed with `install -m 600`, SHA256 compared against the local file, seven routing tokens verified on-device |
| 10 | IMPORTANT | Family predicates did not match tool prerequisites | Accepted as stated | Task 1 — `TOOL_PREREQS`, `tool_status`, `tool_available`, tri-state `family_status`, `FAMILY_TOOLS`; table-driven tests over all 22 tools; every tool body switched off `family_enabled` |
| 11 | IMPORTANT | Mount result discarded; tests mounted a fresh FastAPI; Task 15 probed a bare directory | Accepted as stated | Task 3 — `settings.set_media_mount_ready()` fed by `mount_media_routes`, `HANOVA_MEDIA_MOUNT` as a prerequisite, integration tests against the real `console.LocalStream` app with GET/HEAD/Range/content-type and a traversal check; Task 15 Step 12 probes a seeded file three ways |
| 12 | IMPORTANT | HA reachability treated as proof of location | Accepted as stated | Task 2 — tri-state `home_state()` with a socket-level `lan_signal` and a same-subnet test, `home_unknown()`, single-flight probe; tests for 401, 5xx, timeout, VPN, cached and concurrent cold probes; all six house-bound tools and the persona updated |
| 13 | IMPORTANT | Numeric readers accepted zero, negative, infinity and extremes | Accepted as stated | Task 1 — mandatory keyword-only bounds on `env_int`/`env_float`, non-finite rejection, `zoneinfo` validation of `HANOVA_TZ`, parametrised boundary tests |
| 14 | IMPORTANT | Concurrent credential refresh raced through one fixed temp filename | Accepted as stated | Task 7 — per-path lock registry, `tempfile.mkstemp` at 0600, fsync, `os.replace`, `force_refresh`, and a two-thread simultaneous-expiry test asserting exactly one refresh |
| 15 | IMPORTANT | Truncated filenames collided; index paths unvalidated; copies written in place | Accepted as stated | Task 13 — `validate_cast_path` against `HANOVA_NAS_CAST_SUBPATH`, hash-based `cast_filename`, `.part` + `os.replace` staging, and tests for collisions, traversal, concurrency and partial failure |
| 16 | IMPORTANT | NAS session was process-global, survived shutdown, and advanced before success | Accepted as stated | Task 13 — epoch-stamped session, `peek_next`/`commit_next`, cleared on session start/shutdown and by every superseding media action (Step 6b), with a test for each |
| 17 | IMPORTANT | Dropped upstream behaviours were never declared as scope changes | **Accepted, with the controller's ruling**: Drive restore and email BCC become explicit approved non-goals; `self_destruct` keeps its upstream-style **in-character** arm/confirm with a TTL and an abort word rather than the generic gate summary, so the confirmation does not spoil the gag | Task 10 (Drive restore non-goal + structural test), Task 11 (`send_mail` has no `bcc` parameter + signature test), Task 12 (`_ARM_SUMMARY`, `abort` flag, TTL test, anti-spoiler test), D-018 "Approved non-goals", persona "What Reachy Cannot Do", wake rows 16, 20, 28–30 |
| 18 | MINOR | `show_on_tv` built one `AsyncOpenAI` client to test availability and closed neither | Accepted as stated | Task 6 — `images_available()` checks the key without constructing anything; `generate_image` uses `async with client`; tests assert the client is closed on both paths |
| 19 | IMPORTANT | Backup directory reused; restore unconditional; listings exposed credential filenames | Accepted as stated | Task 14 Step 9c and Task 15 Steps 6/8 — unique verified backup directory per deployment, redacted manifest, manifest-driven conditional restore, reasserted 700/600 modes, and a count instead of a recursive listing |
| 20 | IMPORTANT | Only `REACHY_HOST` loaded; no host key; Steps 6–8 were unattached snippets | Accepted as stated | Task 15 Step 4 loads and validates all four variables including the new gitignored `REACHY_HOSTKEY`; every `plink`/`pscp` carries `-batch -hostkey`; Steps 6–11 are complete copy-pasteable PowerShell |
| 21 | IMPORTANT | "37 registered tools" was wrong, and counting `journalctl` lines counts many startups | Accepted as stated | Task 15 Step 11 — the exact 22 ported names verified within one startup boundary as the primary check, **39** (15 profile + 2 auto-appended system tools + 22 ported) as a secondary sanity check only |
| 22 | IMPORTANT | Task 15 skipped the R12 gate; the wake table omitted Tasks, Drive, a successful email and both gags | Accepted as stated | Task 15 Step 1b runs the full pytest/ruff/mypy gate before the build and after any fix; Step 13's table is rebuilt to 33 rows covering all 22 tools and all eight gated paths in both directions, with an explicit coverage check |
| 23 | IMPORTANT | Drive and email shared one feature item; a disabled-boot item stood in for a family; the music exception was misstated | Accepted as stated | Task 14 Step 8 — nine items: one per real family (`HANOVA-MUSIC`, `HANOVA-GAGS`, `HANOVA-CAST`, `HANOVA-NAS`, `HANOVA-GOOGLE`, `HANOVA-NOTION`, `HANOVA-DRIVE`, `HANOVA-EMAIL`) plus a separate `HANOVA-DISABLED-BOOT`, which now states accurately that music reports **partial**, not enabled, without the gag ids |
| 24 | IMPORTANT | Execution planned on whatever branch existed, staging all of `persona.md` | **Accepted, with the controller's ruling**: a plain feature branch in the same worktree (not a `git worktree`), and the branch merges back to `main` before the deploy wheel is built | New **Task 0** creates `feat/ha-nova-port`, records the pre-existing `git status --short`, and fixes the staging discipline; Task 14 Steps 4 and 12 stage `persona.md` as their own reviewed hunk; Task 15 Step 1c merges to `main` before the build |

**Rejections: none.** Every finding was accepted; three carry scoped rulings, recorded above and in the tasks they touch.

---

**Round 2 (Codex, 2026-08-21): 17 findings — 17 accepted, 0 rejected. Round-1 findings 2, 8, 9, 13, 14, 17, 18, 19, 21, 22, 23 independently verified fixed.**

| # | Severity | Finding, in one line | Disposition | Where the fix landed |
|---|---|---|---|---|
| 1 | CRITICAL | The drain fix was cosmetic — the tracker began "empty" and only learned about audio it had already played — and a `needs_response=False` tool batch left the music paused forever | Accepted as stated | Task 5 — `audio_drain` rebuilt around **response generations**: `begin_response()` opens a generation that is pending before any audio exists, `note_enqueued()` is called by the receiver **before** the queue append, `close_response()` ends it, and `wait_drained(generation)` requires closed + nothing outstanding + empty queue + expired device estimate. `on_tool_call_finished(needs_response)` closes the turn and schedules the resume when the last tool of a batch wants no reply. New handler call site **4d-bis**. Both mandatory tests present: `test_audio_queued_before_response_done_still_blocks_the_resume` and `test_a_final_tool_batch_with_no_follow_up_resumes_the_music` |
| 2 | CRITICAL | Epochs did not protect completion: `complete()`/`release()` took only a tool name, and `arm()` could overwrite a claimed slot | Accepted as stated | Task 2 — `PendingAction.claim_id`, minted at arm time; `complete(name, id)`, `release(name, id)` and the claim-bound `abort(name, id)` all compare epoch **and** id inside the mutating lock via `_live()`; `arm()` on a claimed slot returns the new `action_in_flight()` status. Threaded through **all seven** gated tools (Tasks 7, 8, 10, 11, 12) and asserted by `test_every_gated_tool_threads_the_claim_id` |
| 3 | CRITICAL | All six house-bound tools tested only `AWAY`, so `UNKNOWN` fell through and acted; and a failed TCP connect produced `AWAY`, contradicting "an HA outage is UNKNOWN" | Accepted as stated | Task 2 — `AWAY` now requires **positive off-home evidence** (`HANOVA_HOME_NETWORKS`, new in Task 1, plus `local_address()`); connect failure, DNS failure, refusal, 401, 5xx, timeout and VPN are all `UNKNOWN`; the `/24` rule is demoted to a hint that can only withhold `HOME`. Status renamed `home_status_unknown`, persona wording「我现在不确定是不是在家…」. Tasks 6 and 13 — all six tools branch `HOME`/`AWAY`/`UNKNOWN` and do **no work** on `UNKNOWN`. All six named no-side-effect tests in Task 6 drive the real probe: `test_a_vpn_reach_does_no_house_work`, `test_an_unauthorized_ha_does_no_house_work`, `test_a_server_error_from_ha_does_no_house_work`, `test_an_http_timeout_does_no_house_work`, `test_a_dns_failure_does_no_house_work`, `test_a_refused_connection_does_no_house_work` |
| 4 | CRITICAL | The email "full envelope" read-back still exposed only the first body line, capped at 120 chars, plus a length and an opaque digest | Accepted as stated | Task 11 — `MAX_BODY_CHARS = 500`, `normalize_body()` as the single source of truth, `preview()` **deleted**, the summary carries the **entire** normalised body with the digest appended after it as an integrity token, and a longer body is refused with `body_too_long`. Persona (both profile and `persona.md`) instructs reading the whole thing verbatim. Mandatory changed-tail test: `test_changing_text_after_the_first_line_changes_the_summary` |
| 5 | CRITICAL | Identifier hygiene: manifest-derived NAS fixtures, a shape scan that excluded tests and most docs with a broken private-IP regex, and a staged-diff scan that flagged its own deletions, matched with wildcards, read only `.env`, and printed value prefixes | Accepted as stated | Task 13 — every fixture is now a `SENTINEL_*_q4` synthetic. Task 14 — `_scannable_paths()` covers `src`, `tests`, `profiles`, `docs` and `.claude/skills`; `_PRIVATE_IPV4` names each range's full four octets and is pinned by `test_the_private_ip_pattern_matches_a_normal_private_address` (addresses assembled from parts so the test is not itself a leak); Step 9b scans the **post-image** (`git show :<path>`) with `.Contains()` against an untracked value list seeded from `.env` *and* the scratchpad reports, reporting counts and paths only |
| 6 | CRITICAL | New service-layer code logged raw paths and exception strings, and the scan covered only `tools/*.py` | Accepted as stated | Tasks 1, 2, 3, 4, 6, 13 — `settings.env_path`, `home_net`, `media_store.prune`/`mount_media_routes` (no more `logger.exception`), `ytdlp` search/download/cut, `images` write failure, `nas.load_index`, `ha_client` paths and `nas_message()`'s allow-list all route through `redact`. Task 14 — `_new_service_and_tool_sources()` scans all `hanova/**/*.py` plus `home_net.py` plus the tools, bans `logger.exception` outright, and `test_each_service_seam_has_a_caplog_sentinel_test` requires one sentinel test per seam (all eight now exist) |
| 7 | CRITICAL | The dirty-branch workflow could still commit or deploy the user's pre-existing `persona.md` changes | Accepted as stated | Task 0 Step 2 — the baseline patch is saved outside the repo, hashed, and **stashed**, so between Task 0 and Task 15 the working-tree `persona.md` is the port patch and nothing else. Task 14 Step 4 asserts a clean diff before editing and stages the whole file (never `-p`); Task 15 Step 1c refuses to merge with `persona.md` dirty; Step 8b deploys a file materialised from `git show HEAD:persona.md`; new **Step 15b** pops the stash, falls back to `git apply --3way` on conflict, and hash-verifies the restored added lines against the Task 0 baseline |
| 8 | IMPORTANT | `on_session_started()` called `PLAYER.reset()`, which neither advanced the generation nor stopped the daemon; cleanup hung off handler shutdown rather than the connection | Accepted as stated | Task 4 — new `PLAYER.invalidate()` bumps the generation *inside* the state lock; `reset()` documented as tests-only. Task 5 — both boundaries `invalidate()` **and** `await PLAYER.stop(deps)`; the wiring moves into `_run_realtime_session()`'s `finally` (4b), with `shutdown()` kept as an idempotent second line of defence; `test_no_production_code_calls_player_reset` enforces it |
| 9 | IMPORTANT | Every caught SMTP failure was treated as transient, including authentication, recipient and validation failures | Accepted as stated | Task 2 — the gated-tool contract now has two `except` branches. Task 11 — `gmail_smtp.is_transient` / `friendly_message`, `send_mail` raises `SmtpError` **from** the underlying `smtplib` class so the cause classifies it; terminal failures call `complete()` and force a fresh read-back. Task 14 — `test_every_gated_tool_classifies_transient_and_terminal_failures` |
| 10 | IMPORTANT | Atomic rename was unsafe: every fetch used the same deterministic `.part` name, the concurrency test mocked the real fetch, and pruning could see temporary files | Accepted as stated | Task 13 — per-destination `_fetch_lock()` single-flight with a post-lock re-check, uniquely named `<dest>.<8 hex>.part` staging files, and `media_store.PART_SUFFIX` skipped by `prune`. `test_two_real_concurrent_fetches_release_together_do_not_corrupt_each_other` runs the **real** `fetch_cast_file` twice behind a `threading.Barrier`; `test_pruning_never_deletes_a_staging_file` covers the LRU |
| 11 | IMPORTANT | `peek_next()` returned no token and `commit_next()` advanced whichever session was current | Accepted as stated | Task 13 — `CursorToken(generation, expected_index, next_index)`, a session `generation` bumped by `start_session`/`clear_session`, and `commit_next(token)` as a compare-and-swap returning whether it moved. Tests: only-the-first-of-two-skips-commits, superseded playlist, cleared session, `test_a_session_replacement_during_an_in_flight_cast_is_refused`, `test_two_concurrent_skips_advance_the_trip_by_exactly_one` |
| 12 | IMPORTANT | `HANOVA_NAS_SUBPATH` was a mandatory prerequisite that no NAS code read | Accepted as stated | Task 13 — `validate_source_path()` bounds an index entry's original `path` against it, called first in `stage_and_cast`; `test_the_configured_source_subpath_is_actually_used` and `test_an_index_entry_outside_the_source_subpath_is_never_staged` prove it is consumed |
| 13 | IMPORTANT | Both "real settings app" tests called `LocalStream(instance_path=...)`, which the constructor does not accept | Accepted as stated | Task 3 — `_real_console_stream()` supplies the positional `handler` and `robot` fakes plus an explicit `settings_app=FastAPI()`, and Step 5a adds the one production accessor (`LocalStream.settings_app`) the tests assert on |
| 14 | IMPORTANT | The repo-root `.env.example` does not exist, is ignored by `.gitignore`, and the test made its own validation conditional | Accepted as stated | Task 14 — the file is marked **Create**, Step 9a adds `!/.env.example` to `.gitignore` and writes the complete four-key template, Step 12 stages `.gitignore` alongside it, and `test_the_root_env_example_exists_and_is_tracked` / `test_the_root_env_example_carries_no_real_value` are unconditional |
| 15 | IMPORTANT | `"$name: set"` is a PowerShell parser error, and `Write-Error` is non-terminating | Accepted as stated | Task 15 Step 4 — `"${name}: set"`, missing keys collected into `$missing`, and a single `throw` **before any network request** |
| 16 | IMPORTANT | Transfer and install both used `reachy_companion-*.whl`, so a stale wheel could win | Accepted as stated | Task 15 — Step 3 clears `dist/`, builds once, requires **exactly one** wheel and captures `$env:HANOVA_WHEEL` / `HANOVA_WHEEL_NAME`; Step 5 clears stale remote wheels and copies to the fixed `/tmp/reachy_companion_deploy.whl`; Step 7 installs that exact path and prints the installed version for comparison |
| 17 | MINOR | Test-count claims were stale, and the plan alternated between "eight gated paths" and seven gated tools | Accepted as stated | Every one of the 14 count claims regenerated by counting `def test_` in the embedded code, with parametrised node counts stated separately: settings 31 fn / 38 nodes, redact 8/10, home_net 23, confirm 24, ha_client 7, media_store 18, ytdlp 13, music 25, barge-in 26, cast 31, gauth 10, calendar 22, tasks 22, notion 15, drive 19, email 33, gags 16, nas 48 fn / 57 nodes, integration 31. Coverage is now described as **seven gated tools in both directions plus the separate self-destruct abort branch**, in the Task 15 Interfaces block, the Step 13 preamble and the coverage check. *(Round 3 moved three of these, and the tasks carry the new numbers: redact **10 fn / 12 nodes** (finding 3), ha_client **8** (finding 3), barge-in **28** (findings 1 and 2). Everything else is unchanged.)* |

**Per-finding disposition:** 1: done · 2: done · 3: done · 4: done · 5: done · 6: done · 7: done · 8: done · 9: done · 10: done · 11: done · 12: done · 13: done · 14: done · 15: done · 16: done · 17: done.

**Rejections: none.** All 17 round-2 findings were accepted as stated by the controller and implemented in the tasks named above.

**Contract changes this round introduced**, listed once so no task is left describing the old shape:

- `home_net.is_home()` is **deleted**; `home_net.home_unknown()` takes no argument and returns `status: "home_status_unknown"`; `LanProbe` gains `local_address`; `home_net.local_address()` is new; `HANOVA_HOME_NETWORKS` is a new config key (documented in both `.env.example` files and seeded on the robot in Task 15 Step 9, which now expects **26** keys).
- `ConfirmationGate.complete/release` take `(tool_name, claim_id)` and return `bool`; `abort` takes an optional `claim_id`; `PendingAction` gains `claim_id`; `action_in_flight()` is a new payload.
- `audio_drain` is generation-based: `begin_response`, `note_enqueued`, `close_response`, `outstanding_s`, and `wait_drained(generation, timeout_s)`.
- `music_hooks.on_tool_call_finished(needs_response)` and `music_hooks.on_response_audio(...)` are new; `MusicPlayer.invalidate()` is new.
- `gmail_smtp` gains `MAX_BODY_CHARS`, `normalize_body`, `is_transient`, `friendly_message`; `preview()` is deleted.
- `nas.peek_next()` returns `(video, CursorToken | None, error)`; `nas.commit_next(token) -> bool`; `nas.validate_source_path`, `nas.nas_message`, `nas.PART_SUFFIX` are new; `media_store.PART_SUFFIX` is new and `prune` skips it.
- `console.LocalStream.settings_app` is a new read-only property.

---

**Round 3 (Codex, 2026-08-21): 7 findings — 7 accepted, 0 rejected. Round-2 criticals 2/3/4 verified fixed by reviewer; 1/5/6/7 completed this round. Review closed after 3 rounds per contract; execution authorized by controller.**

| # | Severity | Finding, in one line | Disposition | Where the fix landed |
|---|---|---|---|---|
| 1 | CRITICAL | `_resume_when_drained()` abandoned the resume permanently after 12 s, so a long queued response or a slow sink left the music paused for the rest of the conversation | Accepted as stated | Task 5 — `_DRAIN_TIMEOUT_S` becomes `_DRAIN_REPORT_EVERY_S`, a **diagnostic interval**: `_resume_when_drained(deps, generation, session)` loops until the generation drains, the session token stops being live, or a newer generation supersedes it, logging elapsed seconds and outstanding audio at each interval and ending on nothing else. Mandatory test `test_outstanding_audio_past_the_report_interval_still_resumes` holds **30 s** of outstanding audio across six report intervals and still resumes |
| 2 | IMPORTANT | Module-global `_DEPS` plus an unconditional shutdown let a replaced connection's late `finally` tear down the session that replaced it | Accepted as stated | Task 5 — `on_session_started(deps) -> int` mints `_SESSION_TOKEN`; `on_session_shutdown(deps, token)` ignores any token that is not live (and logs it at DEBUG); the start path re-checks its own token after `await PLAYER.stop(...)` before claiming the gate; `reset_for_tests()` clears the live token without reusing sequence numbers. Handler gains `self._hanova_session` (**4a-bis**), the `finally` presents its own local token (**4b**), `shutdown()` presents the attribute (**4h**). Test `test_an_overlapping_reconnect_ignores_the_stale_shutdown`; the three fake hooks in the lifecycle tests now mint and accept tokens; Task 13's shutdown test mints one before the trip exists |
| 3 | CRITICAL | `redact.error()` tokenized the **raw** message and kept anything matching `^(?:[45]\d{2}\|E[A-Z]+)$`, so an echoed all-caps identifier passed straight through; and the HA client had no caplog sentinel | Accepted as stated | Task 1 — `_STRUCTURAL` deleted; the raw message is never tokenized, scanned or returned. `error(exc, *, allow_errno=())` reads an HTTP status off `status_code`/`status`/`response.status_code`/`code` (100–599 only) and an errno **name** through `errno.errorcode` filtered by `_ERRNO_ALLOWED`; a bare string renders as exactly `"error"`. Four redact tests replace one (10 fn / 12 nodes). Call sites: `ha_client` drops its word list, both `ytdlp` stderr logs switch to `redact.text(...)` (a length, not a token). Task 3 — new `test_the_ha_client_logs_no_script_name_url_or_error_body` (8 fn), registered as the ninth required seam in `test_each_service_seam_has_a_caplog_sentinel_test` |
| 4 | CRITICAL | The identifier scans were not executable: the shape test demanded zero matches on files that already carried them, the staged scan flagged a pre-existing `DECISIONS.md` value, short values were silently skipped, the report-derived list omitted whole categories, and both printed the matched value | Accepted as stated | Task 14 — the shape test now compares **post-image counts against `HEAD`** per path (`_git_head_available`, `_head_text`, `_shape_counts`), enforces zero only for `_SCRUBBED_RELATIVE_PATHS`, and reports **path + shape label + both counts, never the match**. `test_the_nas_fixtures_are_obviously_synthetic` widens to every `tests/test_*.py`, matches both quoting styles, and fails if it matched nothing. Step 9b is rewritten: a **labelled** `LABEL=VALUE` inventory enumerated from all three private reports (manifest, portability, survey) plus the `.env`, **no minimum-length skip** (short values are kept and listed by label), `IndexOf(..., Ordinal)` occurrence counts compared against `git show HEAD:<path>`, failure only on an increase or on any occurrence in a scrubbed file, and output limited to paths, labels and counts |
| 5 | CRITICAL | Both `Out-File -NoNewline` pipelines concatenated git's line-oriented stdout — corrupting the saved patch and deploying a one-line persona whose hashes still agreed; restore verified only added lines; the conflict fallback left the stash and staged the patch | Accepted as stated | Task 0 Step 2 — `git diff --output=` writes both patches itself (full-context for the fallback, `-U0` for identity), and the identity is `git patch-id --stable` over the **complete payload including deletions**, read through a `cmd.exe` redirect so nothing re-encodes. Task 15 Step 8b — `cmd /c "git show HEAD:persona.md > …"` plus a line-count guard that refuses to ship a collapsed file. Step 15b — resolve the stash **by message**, `apply` (never `pop`), `git restore --staged` so the user's patch comes back **unstaged**, compare stable patch ids, and `git stash drop` **only after** verification; the conflict fallback cleans the file, unstages, and on failure leaves the stash untouched. Task 0 rule 3 restated to match |
| 6 | IMPORTANT | `!/.env.example` placed under `.env` is re-ignored by the later `.env.*` rule, so Task 14 could never stage the template | Accepted as stated | Task 14 — the Files bullet, Step 9a and the negation comment all state that it goes **after every `.env`-matching pattern** (end of file); Step 9a lists the matching rules with line numbers first and verifies with **both** `git check-ignore` (exit 1 for the template, 0 for `.env`) and `git ls-files --error-unmatch` after `git add`. `test_the_root_env_example_exists_and_is_tracked` now asserts the negation's **index is greater than every `.env` rule's**, not merely that it is present |
| 7 | IMPORTANT | The literal `\n` in the Task 15 persona token loop became an eleventh shell token and produced a false `TOKEN MISSING` | Accepted as stated | Task 15 Step 8b — the ten tokens are on one line, with a comment saying why; the expectation is restated as exactly ten `token ok` lines and **no** `TOKEN MISSING` line |

**Per-finding disposition:** 1: done (Task 5 — unbounded resume + 30 s test) · 2: done (Task 5 — session token through start/shutdown + overlapping-reconnect test) · 3: done (Task 1 — attribute-derived structure, no text tokenizing; Task 3 — HA-client caplog sentinel) · 4: done (Task 14 — HEAD-relative counts, labelled inventory, no length skip, path+count reporting, scoped fixture check) · 5: done (Tasks 0 and 15 — git-written patch/blob, stable patch-id over the full payload, apply→verify→drop, unstaged restore, cleaning fallback) · 6: done (Task 14 — negation after every `.env*` pattern, verified two ways) · 7: done (Task 15 — single-line token list).

**Rejections: none.** All seven round-3 findings were accepted as stated by the controller and implemented in the tasks named above.

**Test counts moved this round** (every other count in the round-2 table stands):
`test_hanova_redact.py` 8 fn / 10 nodes → **10 fn / 12 nodes**;
`test_hanova_ha_client.py` 7 → **8** (Task 3 total 25 → **26**);
`test_hanova_music_barge_in.py` 26 → **28**.
`test_hanova_integration.py` stays at **31** — round 3 rewrote three of its tests and added three module-level helpers, but no test function.

**Contract changes this round introduced:**

- `redact.error` takes `allow_errno: tuple[str, ...]` instead of `allow: tuple[str, ...]`, never inspects raw text, and returns `"error"` for any non-exception input; `redact._STRUCTURAL` is deleted and `_ERRNO_ALLOWED` is new.
- `music_hooks.on_session_started(deps)` returns an `int` session token; `music_hooks.on_session_shutdown(deps, token)` takes it and ignores a stale one; `_DRAIN_TIMEOUT_S` is replaced by `_DRAIN_REPORT_EVERY_S` and the resume wait is unbounded; `HuggingFaceRealtimeHandler` gains `self._hanova_session: int`.
- Task 0 produces a **second** patch file (`hanova-persona-baseline.u0.patch`) and a `Get-PatchId` helper; the persona restore is `apply` → verify → `drop` rather than `pop`.
- The Step 9b value list is `LABEL=VALUE` rather than bare values, and the scan is HEAD-relative rather than absolute.
