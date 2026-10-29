# Session handoff — 2026-10-29 (v1.28.0 installed; daemon app manager wedged)

State: **v1.28.0 installed** on daemon v1.13.0, **no app running**. The
daemon's app manager is stuck in `stopping` after an official stop at 11:42
UTC (`DAEMON-STOP-WEDGE`), so every start is refused. Clearing it takes a
daemon restart, which only the operator can authorize; this morning's
authorization covered one restart and has been used. `main` is pushed.
Details: `progress.md` top entry; D-038.

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

## Next session

0. **Blocked on the operator's daemon restart:**
   - (a) Case 6 of `MEDIA-SOURCE-ROUTING`: 「電視上放沖繩的影片」 with no source
     named should get one short question and no nas/youtube call. Cases 1–5
     passed.
   - (b) The operator's request: switch the voice model to
     gpt-realtime-2.1-mini or its latest alias, then re-run the tool-call
     cases.

   Use one fresh session per case: app stop/start through the API, mute the
   mic with `/rpc` `conversation.mic` `{"muted": true}`, then send
   `conversation.say`. Keep each stop's response body, since a 400
   names the exception that wedges the daemon.

   Still open: TV sound taken as a user turn (`TV-AUDIO-PHANTOM-TURNS`), no
   tool that stops or pauses the TV, and the `show_on_tv` image 400.
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
