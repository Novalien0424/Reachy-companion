#!/bin/bash
# The robot's Wi-Fi watchdog crontab line (D-034). Run from the Mac, by the operator:
# installing persistence on the robot is the operator's step, not an agent's.
#
#   watchdog_cron.sh install   add (or replace) the line: every 2 minutes, as the robot user
#   watchdog_cron.sh remove    take the line out; the script itself stays in place
#   watchdog_cron.sh status    show whether the line is there, the saved state, recent actions
set -u
REPO=$(git -C "$(dirname "$0")" rev-parse --show-toplevel) || exit 1
H=$(grep '^REACHY_HOST=' "$REPO/.env" | cut -d= -f2-)
U=$(grep '^REACHY_SSH_USER=' "$REPO/.env" | cut -d= -f2-)
R=(ssh -o BatchMode=yes -o ConnectTimeout=10 "$U@$H")
TAG='# reachy-wifi-watchdog'

case "${1:-}" in
  install)
    # $HOME is expanded on the robot (single quotes here).
    "${R[@]}" '
      test -f "$HOME/.local/lib/reachy-wifi-watchdog/wifi_watchdog.py" || { echo "watchdog script missing on the robot"; exit 1; }
      ( crontab -l 2>/dev/null | grep -v "# reachy-wifi-watchdog$"
        echo "*/2 * * * * /usr/bin/python3 -I $HOME/.local/lib/reachy-wifi-watchdog/wifi_watchdog.py >/dev/null 2>&1 # reachy-wifi-watchdog"
      ) | crontab - && echo "installed: $(crontab -l | grep -c "# reachy-wifi-watchdog$") watchdog line in the crontab"'
    ;;
  remove)
    "${R[@]}" '( crontab -l 2>/dev/null | grep -v "# reachy-wifi-watchdog$" ) | crontab - && echo "removed; watchdog lines left: $(crontab -l 2>/dev/null | grep -c "# reachy-wifi-watchdog$")"'
    ;;
  status)
    "${R[@]}" '
      echo "crontab lines: $(crontab -l 2>/dev/null | grep -c "# reachy-wifi-watchdog$")"
      /usr/bin/python3 -I "$HOME/.local/lib/reachy-wifi-watchdog/wifi_watchdog.py" --status 2>&1 | head -5
      journalctl -t reachy-wifi-watchdog --since "-1 day" --no-pager -o short-iso 2>/dev/null | tail -5'
    ;;
  *) sed -n '2,8p' "$0"; exit 2 ;;
esac
