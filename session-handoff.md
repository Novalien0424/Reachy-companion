# Session handoff — 2026-10-30 (v1.28.5 installed; robot asleep; follow-through 4/5)

State: **v1.28.5 installed**, robot asleep (the app was stopped after the
2026-10-30 live checks), TV off. The next wake loads 1.28.5. Live results by
run are in D-038; `main` is pushed.

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

## Next session

0. **Media follow-through (MEDIA-SOURCE-ROUTING next_action):**
   - After the source question, the answer turn runs `query` first every time
     (6 of 6), and stopped there in 1 of 3 on 1.28.5. Rungs 1-2 are spent.
   - Proposed next: the V1 pair ×3 once on full `gpt-realtime-2.1`. This
     needs the operator's OK, because it changes the model setting for the
     run and costs more.
   - Also open: an untranslated 「沖繩」 query missed and Reachy said the trip
     isn't there (NAS-FUZZY-SEARCH, now a known defect).
   - Still open: `NAS-PLAY-REQUIRES-SELECTOR` hasn't fired live yet. There is
     no tool to stop or pause the TV, but Reachy still offered 「想讓我停一下嗎？」.
   - `TV-AUDIO-PHANTOM-TURNS` and the `show_on_tv` image 400 are unchanged.
   - Every app stop carries the daemon-wedge risk: keep the response body and
     report a wedge. Each restart needs the operator's authorization.
   - For live tests: one fresh session per case, mic muted over `/rpc`
     `conversation.mic`. Stop the app or unmute when done.
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
