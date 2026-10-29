# Session handoff — 2026-10-29 (v1.28.3 running; source routing verified live)

State: **v1.28.3 running** on daemon v1.13.0, with the mic unmuted and the TV
off. Media requests follow the source the user names: `youtube`, or `nas`
(影片庫). A request that names no source gets a question first; the toolbox
"don't ask again" rule now forbids only a second go-ahead.
- `nas play` refuses to pick a clip without a path or a filter.
- The daemon stop wedge is root-caused and looks unreported upstream (D-038,
  `DAEMON-STOP-WEDGE`). The operator chose not to draft an issue yet.
- `main` is pushed. Details: `progress.md` top entry; D-038.

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

0. Open, none blocking:
   - After the user answers the source question, Reachy once listed the trip
     without playing it (1 of 2 runs). Watch it in-room.
   - `NAS-PLAY-REQUIRES-SELECTOR`: the refusal has not fired live yet.
   - Every app stop carries the daemon-wedge risk. Keep the response body,
     and report a wedge: only a service restart clears it, and each restart
     needs the operator's authorization.
   - For live tests: one fresh session per case, mic muted over `/rpc`
     `conversation.mic`, and unmute when done.
   - Still open: `TV-AUDIO-PHANTOM-TURNS`, no tool to stop or pause the TV,
     and the `show_on_tv` image 400.
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
