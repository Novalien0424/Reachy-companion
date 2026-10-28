# Wi-Fi recovery — plan (2026-10-28)

Operator request: "fix the Wifi recovery". Evidence: the robot stayed booted
~2026-09 → 2026-10-28 but fell off the LAN for at least the last week (no
ARP reply, app retrying `Temporary failure in name resolution`), and never
recovered until a power-button cycle. NetworkManager logged **nothing** in the
retained week; the journal (cap `SystemMaxUse=100M`, at 103.5 MB) held 193,314
lines from that boot, 193,311 of them `launcher.sh` (daemon + app), including
our `Backend failed to start … retrying in 5.0 seconds` every 5 s. The v1.13
daemon only runs `ensure_wifi_on_startup()` — nothing watches Wi-Fi at run
time. Robot facts: NM manages `wlan0`, autoconnect on, Wi-Fi powersave off,
`sudo -n /usr/bin/nmcli` is NOPASSWD for `pollen`, cron active, no user
crontab, system `/usr/bin/python3` 3.13.

Scope note: a watchdog that runs while the robot sleeps cannot live in the
app (the app is stopped when asleep). It runs from `pollen`'s user crontab —
no root, no daemon change, no system file — using only the NOPASSWD `nmcli`.
Recorded as D-034.

## W1 — stop our share of the journal flood (app, `console.py`)
Backend start retry: exponential backoff from `BACKEND_RETRY_DELAY_SECONDS`
(5 s) doubling to a 60 s cap, reset on success. Log the first failure and any
change of error text at WARNING; repeats of the same error at most once per
10 min at WARNING with a repeat count, otherwise DEBUG. Verify: unit tests for
the delay sequence, reset on success, and the log-rate rule.

## W2 — Wi-Fi watchdog module (`reachy_companion/wifi_watchdog.py`)
Stdlib only, runnable as a plain file by `/usr/bin/python3` (no package
imports), so it keeps working if the apps venv is broken or mid-reinstall.
- Probe every run (cron `*/2`): default route present AND gateway answers
  `ping -c 2 -W 2`. Gateway OK but DNS failing → upstream problem: log once,
  never touch Wi-Fi.
- State in `~/.local/state/reachy-wifi-watchdog/state.json` under an `fcntl`
  lock (overlapping runs skip).
- Escalation on consecutive failures: 2 (≈4 min) → `sudo -n nmcli device
  connect wlan0`; 4 → `nmcli radio wifi off`, 5 s, `on`; then a radio cycle
  every further 6 failures; at most 4 actions per rolling hour.
- Never acts while the active connection is the hotspot or NM is
  `connecting`; never deletes profiles, starts the hotspot, restarts the
  daemon or reboots.
- Logs to syslog/journal as `reachy-wifi-watchdog` on transitions only (down
  detected, each action, recovered with downtime) plus one "still down" line
  per hour.
- `--dry-run` (decide and log, act on nothing) and `--status`; probe target
  override for tests.
Verify: unit tests with fake probes/clock/runner for every escalation rung,
the hourly cap, the hotspot/connecting guards, the DNS-only case, lock skip,
recovery logging; ruff + mypy.

## W3 — install on the robot (deploy skill)
Copy the module file to `~/.local/lib/reachy-wifi-watchdog/wifi_watchdog.py`,
install one tagged crontab line idempotently, run `--dry-run` and `--status`
once. Rollback: drop the tagged line (`crontab -l | grep -v
reachy-wifi-watchdog | crontab -`). Verify on robot: dry run reports healthy;
a dry run against an unreachable probe target walks the rungs without acting;
the cron run appears in the journal on its first transition only.

## Self-review log
1. *Could the watchdog cut a working link?* Only after ≥2 failed gateway
   probes 2 min apart, and the first rung (`device connect`) is a no-op on a
   healthy device; radio cycles need ≥4 failures and are capped 4/hour.
   Gateway ICMP verified answering on this LAN.
2. *Does it need root or touch the daemon?* No — NOPASSWD `nmcli` and a user
   crontab only. Outside D-009's app-only scope, so recorded as D-034.
3. *Why not in the app?* The app is stopped whenever the robot sleeps, which
   is most of the time.
4. *Why not a systemd user unit?* `Linger=no`; enabling linger is a system
   change. cron runs without a session.
5. *Evidence next time:* W1 cuts our flood; W2 logs transitions with
   timestamps under its own identifier. The daemon's own relay warnings (every
   10 s while offline) remain — Pollen's code, reportable upstream.
