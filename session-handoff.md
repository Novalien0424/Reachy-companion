# Session handoff — 2026-10-29 (v1.27.3 installed; home video cast verified)

State: **v1.27.3 installed and running** on daemon v1.13.0. The wedged app
manager was cleared by one operator-instructed daemon restart; a home video
then played from the NAS on the TV through `nas` (D-037,
`NAS-PLAY-VIA-NAS`). `main` is pushed. Details: `progress.md` top entry;
D-036, D-037.

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

## Next session

0. Open from today, none blocking: the model still calls `tv` before `nas`
   (one refused call); TV sound taken as a user turn
   (`TV-AUDIO-PHANTOM-TURNS`: diagnose first); no tool stops or pauses the
   TV; one `tv show` image generation returned 400 (unexamined). If the
   app manager wedges in `stopping` again, report it: the 2026-10-29 restart
   authorization covered that one restart only. Check `motor_control_mode`
   before an app stop.
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
