# Session handoff — 2026-09-01 (post-v1.20.0 field test RCA, fixes NOT started)

State: **v1.20.0 (twentieth install) is on the robot and verified**; repo clean
and pushed through `1a8d912` (cleanup) — this handoff is the only new file.
Robot: app stopped at 12:57 by the sleep flow; physical pose state UNCERTAIN
(see RCA-6). Operator was mid-conversation with Claude about the RCA below and
restarted Claude Code; **no fixes are planned or started yet** — the operator
said "no fix yet".

## Field test result (operator session 12:49–12:57 robot time, journal-verified)

Wave fixes CONFIRMED live: goodbye-then-sleep worked end to end (farewell
「好啦，小諾，先這樣啦…」 composed by the model with the tool's farewell_context,
4.9s drain, then pose path); look_around(left) physically turned (suspend →
capture of a genuinely-left scene → restore); memory-personalized greeting;
music eventually played the right song from a garbled title.

## Consolidated RCA (operator's 6 observations + journal analysis) — NO FIXES YET

1. **Mishearing = model-side, not audio hardware.** Homophone ASR errors
   (棋靈王→麒麟王/清望) with grammatical transcripts = acoustics fine; the
   realtime model's own audio understanding also diverged from its transcriber
   (see 5). Contributor: turn-detection commits mid-sentence fragments
   (「你。」「就是。」 answered in <400ms).
2. **GROUP mode friction is by-design config.** Boot default 多人聊天模式
   (operator's 2026-08-31 ruling): name/engaged-face gate + 1.6s barge
   confirm + 20s follow-up window. Logged denial: name-less 「用你的喇叭播出來」
   47s after last accepted turn. 「切到一對一聊天模式」 by voice flips it.
3. **No "let me search, please standby" = deliberate wave ruling.** Commentary
   channel suppressed + prompt says act-silently; cost = 8–10s dead air on
   search turns. Operator EXPECTS spoken preambles → the deferred
   "selective commentary for slow tools" wave is now operator-requested.
4. **Music denials**: (a) 「找 YouTube…播放」 routed to search (description
   says "call directly whenever user asks to search"), mini doesn't chain to
   music; (b) model FABRICATED 「我沒法直接播放 YouTube」 while `music` was in
   its 22-tool list, disproving itself minutes later.
5. **NSFW refusal was a model mishearing.** No transcript contains adult
   content (turn was garbled fragment 「你比我最喜歡的動畫。」); the model's
   audio interpretation hallucinated an 18+ request and refused twice —
   violating the shipped unclear-audio→ask-again rule.
6. **End-of-sleep failure — PRE-EXISTING, both v1.19 and v1.20 show the
   identical signature**: `Requested current app stop via …` (closure success
   line, main.py:37 area) appears in NEITHER session's journal; C6
   (`go_to_sleep failed before the stop; microphone unmuted`, main.py:405)
   fired ~7s after `Stopping movement manager`. Best-supported theory: pose ran
   (pose failures log loudly and none appears), then the closure's HTTP
   stop-request triggered the daemon teardown which broke the request's own
   response path — a non-URLError exception escaping the
   `request_stop_current_app` guard (app_lifecycle.py:26-38) into C6, unmuting
   the mic 7s before shutdown. ALSO: `Sleep summary failed: TimeoutError`
   (sleep_summary.py:189) — the D-027 visit summary to the Mac backend was
   LOST (real data loss; backend likely unreachable). OPEN QUESTION for the
   operator: what did the failure look like physically (no pose? posed then
   moved? other)? That answer places the exception precisely.

**Cross-cutting pattern**: stale 「還在處理中」 narrations (3× across sessions),
the fabricated capability denial, and the confident NSFW refusal are one
family — mini asserting state it doesn't know instead of consulting what it
has (arrived tool results, its tool list, its own uncertain hearing). The wave
fixed this for completed actions (honest returns); uncovered surface =
in-flight state + self-knowledge. Plus: turn fragmentation, and GROUP-mode
defaults hurting solo use.

Also still open from the earlier session (00:14–00:17 review): **who_is_this
returned too_far/no_face while the camera plainly described a person looking
at the lens** — face-recognition capture path defect, untouched by any wave.

## Next session start

1. Read this file + progress.md; robot journal extracts live in the session
   scratchpad (gone after restart) — re-pull from the robot if needed:
   the test window is 2026-09-01 12:49–12:57 robot time.
2. Ask the operator the RCA-6 open question, then plan the fix wave against
   this RCA under .claude/skills/reachy-instructing-model (escalation ladder).
3. Robot access: repo-root .env keys; deploys via reachy-deploy skill.
