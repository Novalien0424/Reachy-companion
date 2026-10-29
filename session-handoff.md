# Session handoff — 2026-10-29 (v1.28.0 running; 1.28.1 built, not deployed)

State: **v1.28.0 running** on daemon v1.13.0. The daemon was restarted once
at 12:02 UTC on the operator's instruction, which cleared the app-manager
wedge. Live case 6 (no source named) went to YouTube instead of asking.
**1.28.1** fixes that in the tool descriptions; it is built and committed
but not deployed. Deploying needs an app stop, and the daemon's stop path
can wedge (D-038, `DAEMON-STOP-WEDGE`: 3 of 12 stops failed today, 2
wedged). `main` is pushed. Details: `progress.md` top entry; D-038.

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

0. **Deploy 1.28.1 when the operator agrees to the wedge risk.**
   - Rerun case 6 (「電視上放沖繩的影片」 → one short question, no
     nas/youtube call) in a fresh session, plus cases 1 and 3 as regression
     checks; then mark `MEDIA-SOURCE-ROUTING` verified.
   - Keep each stop's response body: a 400 now names the motor error. If it
     wedges, report it; only a service restart clears it, and each restart
     needs fresh authorization.
   - Fresh session per case: app stop/start through the API, mute the mic
     with `/rpc` `conversation.mic` `{"muted": true}`, send `conversation.say`,
     and unmute when done (the mute survives until the app restarts).
   - Voice model: no change needed (D-038 progress note; the robot already
     runs `gpt-realtime-2.1-mini`, the newest id on the account).
   - Still open: TV sound taken as a user turn (`TV-AUDIO-PHANTOM-TURNS`),
     no tool to stop or pause the TV, and the `show_on_tv` image 400. Offer
     an upstream issue for the stop wedge (analysis in D-038).
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
