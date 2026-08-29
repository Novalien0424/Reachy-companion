# Session handoff — 2026-08-30 (sixteenth install deployed and booted)

`engagement-memory` (9 tasks, D-027) is **merged to `main`** (fast-forward to
`e4a40b1`, pushed) and the **sixteenth install is on the robot with a clean
first boot** (2026-08-30 00:03 robot time; wheel `0f95e9ff…`; backup/restore
manifest `20260829T230203Z-12174`; 4 faces + 4 people survived; persona sha
`1ce532f3`; 41 tools; new who_is_this fact-fidelity description confirmed in
the registration log; 0 tracebacks). **App left RUNNING.**

## Next actions (operator + a human)

1. **MEMORY-LAST-CHAT live row:** get recognized, mention an ongoing thing,
   say 「請進入睡眠模式」 (must be the VOICE tool — a dashboard stop writes no
   summary, that's the negative control). Journal must show `Sleep summary:
   wrote last-chat fact for 1 person(s).` after the session-shutdown lines;
   `people.v1.json` gains exactly one 「上次聊天（M月D日）：…」 fact; next
   recognized boot weaves it in. **Measure the summarizer latency** on this
   first run — gpt-5-mini under the 8 s `MEMORY_LAST_CHAT_TIMEOUT_S` budget
   is unproven on-device; raise the knob if it times out. Second negative
   control: two people hours apart → only the recent one gets a summary.
2. **MEMORY-OPEN-LOOPS:** listen for `remember` preferring ongoing threads.
3. **BACKEND-CONSOLIDATE:** with the backend STOPPED, run
   `companion_backend/scripts/consolidate.py` (dry-run) → review diff →
   `--apply` → push. One-shot flow: `--import-first --apply --push-after`.
   The CLI refuses (exit 3) while anything listens on :8710.
4. Older pending rows unchanged — see `progress.md` → Pending verification.

## Watch items

- Daemon force-kill racing the ≤8 s sleep-summary write (benign loss; watch
  the journal line's absence).
- Daemon app-state wedge (recurred once, cleared by reboot 2026-08-29).
- Power/undervoltage triage procedure in `progress.md`.

## Robot access (D-020)

```
REACHY_HOST=10.0.0.96
REACHY_SSH_USER=pollen
```

Secrets in gitignored repo-root `.env` only. Deploy procedure:
`.claude/skills/reachy-deploy/SKILL.md`.

## Repo sync

`main` @ `e4a40b1` + this session's docs commit, pushed. Deliberate residue:
`.gitignore` operator line, untracked `reachy_companion/uv.lock`.
