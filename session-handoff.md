# Session handoff — 2026-10-30 (v1.31.3 installed; home_control reads back)

State: **v1.31.3 installed 12:52 UTC; Reachy asleep (app stopped by its own
`go_to_sleep` at 11:01 UTC), TV off, mic unmuted.** Daemon v1.13.0. `main` is
pushed.

## Shipped today

- Contract tightened for Opus 5.5 (Codex review → self-review, skills 4 → 3).
- Upstream refresh + wave-1 ports (v1.24.0); daemon v1.10.0rc5 → v1.13.0.
- v1.25.0: instance-dir migration, local-VAD listening, usage telemetry +
  plain-UTF-8 tool outputs + opt-in truncation/image-detail knobs, backend
  retry backoff, Wi-Fi watchdog module.
- v1.26.0 (2026-10-29): MCP v2 client (upstream port, certifi-verified),
  trimmed MCP results (live search 1,700 → 729 chars).
- v1.27.0–.2 (2026-10-29): NAS search for loose Chinese requests (verified
  live), `/rpc` say on the conversation loop, tv/nas descriptions mirrored;
  robot `.env` HA address + TV entity repointed after HA's 10-25 move.
- v1.27.3 (2026-10-29): `tv play_video` refuses a NAS clip and points at
  `nas play`; `open_toolbox` says loose descriptions find home videos. Home
  video cast verified live (NAS stream 206, HA `playing`).
- v1.28.0 (2026-10-29): media tools named by source — `youtube`, `nas`
  (影片庫) and `draw_picture`; `tv` retired; ask when no source is named.
  Live 5/6 passed (D-038).
- v1.28.1–.5 (2026-10-29/30): ask-first and follow-through fixes. Asked
  3/3 before any video tool, follow-through 4/5 (D-038).
- v1.29.0 (2026-10-30): `nas` fitted to the mini model: `play` finds and
  plays in one call, `list` browses, Chinese misses say to search in English,
  and the descriptions are about half as long (D-038). Live: V1 3/3, V2, V3.
- v1.29.1 (2026-10-30): error results reach the model whole (`candidates`
  and similar fields were dropped in transport, for every tool), and
  `remaining` is now `clips_left`. Live: V4 2/2, V3 in one call (D-038).
- v1.30.0 and 1.31.0 (2026-10-30): the mini context diet (D-039).
  - 19 tools are always loaded; there are no toolboxes; calendar, tasks and
    Notion are gone.
  - Families: `dance`, `head`, `memory`, `face`.
  - The TV is a home device.
  - Each rule is written once.
  - The fixed prefix went from 9,866 to about 5,820 tokens.
- Project skills updated from this session's failures (`4531ee4`). The live
  harness is `.claude/skills/reachy-deploy/scripts/live_case.sh`.
- v1.31.1/1.31.2 (D-040): `home_control` reads the device back.
  - It reported success on HA's 200 alone.
  - Its 8 s limit timed out the TV's turn-on, which takes 4–9 s.
  - A timeout now still reads the device back, and a refusal is named as one.
  - The LAN URL no longer reaches the model.
  - Live: TV on, off and already-off each 3/3; an unknown device 3/3.
- v1.31.3: turning a device on counts as slow work, so Reachy speaks
  first. Live preamble 1/4 → 5/5; the journal now logs preamble text.

## Next session

0a. **home_control gaps** (D-040 § Open):
   - A scene, script, cover or lock entry would be judged wrongly: their
     states aren't on/off. Decide before adding one.
   - The case where a device ignores the command is unit-tested only. Stage
     it with a real light switched off at the wall.
0. **Follow-ups from the diet** (D-039, none blocking):
   - **EMOTION-WITHOUT-SPEECH:** `play_emotion` moves silently. The candidate
     fix is `needs_response=True`, behind a live A/B.
   - **V1 ask-first is about 3/4 on mini.** The misses go straight to
     YouTube.
   - **Leftovers:**
     - the locked profile's fallback prose still has the old conventions;
     - the `tzdata` dev dependency is unused;
     - `REALTIME_PROMPT_HARDENING=0` leaves the verbatim flag undefined.
   - **Live tests:** `reachy-deploy` § Live verification;
     - one fresh session per case, mic muted over `/rpc` `conversation.mic`;
     - with a family member in view, skip `memory` tests, because they
       write to that person's record;
     - unmute when done.
   - **A stop can wedge the daemon.** Clear it with `sudo -n systemctl restart
     reachy-mini-daemon` (standing authorization) and record the time.
1. **Operator:** install the Wi-Fi watchdog cron line (blocked for agents):
   `ssh <user>@<host> '( crontab -l 2>/dev/null | grep -v "# reachy-wifi-watchdog$"; echo "*/2 * * * * /usr/bin/python3 -I $HOME/.local/lib/reachy-wifi-watchdog/wifi_watchdog.py >/dev/null 2>&1 # reachy-wifi-watchdog" ) | crontab -'`
   then `journalctl -t reachy-wifi-watchdog` → `monitoring started`.
2. In-room: `docs/five-demo-checklist.md` (all five demos on v1.13 /
   v1.26.0), the local-VAD lead line, antennas quiet while Reachy talks;
   demo 4 is also the in-conversation check of the trimmed search result
   (`MCP-RESULT-TRIM`).
3. Latency: one ~20-minute session for the `Response usage` curve, then the
   reasoning-effort / truncation A/B (`docs/plans/2026-10-28-latency-observability-plan.md`).
4. Pull the Mac backend checkout before its next push (new robot path).
5. Consider: change the robot's `pollen` password if it is still the
   documented default; report runtime Wi-Fi recovery to Pollen upstream.

## Notes

- Robot credentials must not be copied off the robot (classifier); backups
  stay in `~/reachy_companion_backups/` on the robot.
- The sandbox intermittently hides the home directory from shell commands;
  relative paths from the repo root work.
