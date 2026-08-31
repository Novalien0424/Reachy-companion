# Session handoff — 2026-08-31 (seventeenth install deployed and booted)

The human-like-conversation wave (`name-gate-patience`, 9 tasks, D-028,
3 Codex plan-review rounds + per-task subagent reviews + final whole-branch
review) is **merged to `main`** (fast-forward to `26573f0`, pushed;
merged-tree suite 1571 / 30) and the **seventeenth install is on the robot
with a clean first boot** (2026-08-31 07:13 robot time; wheel `c5cccfa0…`;
backup/restore manifest `20260831T061202Z-10185`; 4 faces + 4 people
survived; **persona re-synced to `3a2be561`** — the no-preamble line is
live; 41 tools; boot gate released; zero tracebacks; **no `session.update
rejected`** — the live API accepted `reasoning.effort=low` and
`max_output_tokens=900`). **App left RUNNING.**

## Next actions (operator + a human in the room)

1. **VOICE-NAME-GATE**: talk over Reachy without its name → journal
   `solo barge rolled back (unaddressed)` and the reply resumes; say 「瑞奇」
   mid-reply → stops <1 s; bare 「停」 → stops.
2. **VOICE-LATE-INTERRUPT**: address it with a long sentence (>4 s) → reply
   resumes at the max-pause cap, then stops when the transcript lands
   (`late solo interrupt (...) on committed turn`). Watch for the T4-m4
   edge: answer plays, cuts ~300 ms, same answer returns ~1.5 s later.
3. **VOICE-TRUNCATE**: needs module log level DEBUG for the run — the
   `conversation.item.truncate refused` line is DEBUG; a clean INFO journal
   is not proof. After an interruption ask 「你剛剛說到哪」.
4. **VOICE-PATIENCE** (1 s silence feel; `VOICE-SEMANTIC-VAD-AB` is the
   deeper A/B: `REALTIME_VAD_TYPE=semantic_vad REALTIME_VAD_EAGERNESS=low`)
   and **VOICE-BREVITY** (length tracks content; no preambles).
5. Older pending rows unchanged — `progress.md` → Pending verification.
   Mitigation if realtime sessions misbehave: `REALTIME_REASONING_EFFORT=off`.

## Robot access (D-020)

Secrets in gitignored repo-root `.env` only. Deploy procedure:
`.claude/skills/reachy-deploy/SKILL.md`.

## Repo sync

`main` @ `26573f0` + this session's closing docs commit, pushed. Deliberate
residue: `.gitignore` operator line, untracked `reachy_companion/uv.lock`.
