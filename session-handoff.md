# Session handoff — 2026-10-30 (v1.29.1 running; 1.29.x checks done)

State: **v1.29.1 installed and running** (daemon v1.13.0, restarted 07:23
UTC under the operator's standing authorization), mic unmuted, TV off.
`main` is pushed.

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

## Next session

0. **Media follow-ups** (none blocking):
   - The model mostly sends Chinese search words first. The corrective error
     fixes that in one extra call. Leave it unless latency matters.
   - There is no tool to stop or pause the TV, though Reachy offers
     「想讓我停一下嗎？」 anyway. Also open: `TV-AUDIO-PHANTOM-TURNS` and the
     `show_on_tv` image 400.
   - The operator runs on mini only. Fit tools to what the model does, and
     keep text short: no full-tier diagnostic, no added instruction text.
   - Live tests: one fresh session per case, mic muted over `/rpc`
     `conversation.mic`. Unmute or stop the app when done.
   - A stop can wedge the daemon (DAEMON-STOP-WEDGE). Under the standing
     authorization, clear it with `sudo systemctl restart reachy-mini-daemon`
     and record the restart.
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
