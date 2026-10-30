#!/bin/bash
# One live test turn on the robot, read back from the journal. Run from anywhere on the Mac.
#
#   live_case.sh 1 "<user text>" [wait_s]   fresh session (app stop + start), then the turn
#   live_case.sh 0 "<user text>" [wait_s]   next turn in the same session
#   live_case.sh cleanup                     unmute the mic, print the TV state, remove the robot copies
#   live_case.sh tv-off                      turn the TV off (only if it was off when testing began)
#
# Exit 3 = the daemon wedged on the app stop (state stuck at "stopping"); see Hard limits.
set -u
REPO=$(git -C "$(dirname "$0")" rev-parse --show-toplevel) || exit 1
HERE=$(cd "$(dirname "$0")" && pwd)
H=$(grep '^REACHY_HOST=' "$REPO/.env" | cut -d= -f2-)
U=$(grep '^REACHY_SSH_USER=' "$REPO/.env" | cut -d= -f2-)
R=(ssh -o BatchMode=yes "$U@$H")
PY='/venvs/apps_venv/bin/python -I'

push() { scp -q -o BatchMode=yes "$HERE/rpc.py" "$HERE/tv_state.py" "$U@$H:/tmp/"; }

case "${1:-}" in
  cleanup)
    push
    "${R[@]}" "$PY /tmp/rpc.py unmute; $PY /tmp/tv_state.py; rm -f /tmp/rpc.py /tmp/tv_state.py"
    exit ;;
  tv-off)
    push
    "${R[@]}" "$PY /tmp/tv_state.py off"
    exit ;;
  0|1) ;;
  *) sed -n '2,9p' "$0"; exit 2 ;;
esac

if [ "$1" = 1 ]; then
  # The stop's body is wedge evidence (DAEMON-STOP-WEDGE): keep it.
  echo "stop: $(curl -s -m 30 -w ' [HTTP %{http_code}]' -X POST "http://$H:8000/api/apps/stop-current-app")"
  for _ in $(seq 20); do
    [ "$(curl -s -m 5 "http://$H:8000/api/apps/current-app-status")" = "null" ] && break
    sleep 1
  done
  ST=$(curl -s -m 5 "http://$H:8000/api/apps/current-app-status")
  if [ "$ST" != "null" ]; then echo "WEDGED: $ST"; exit 3; fi
  T=$("${R[@]}" 'date -u +%H:%M:%S')
  curl -s -m 30 -o /dev/null -X POST "http://$H:8000/api/apps/start-app/reachy_companion"
  "${R[@]}" "for i in \$(seq 45); do journalctl -u reachy-mini-daemon --since '$T' --no-pager -o cat | grep -q 'Tools in session' && break; sleep 2; done
    journalctl -u reachy-mini-daemon --since '$T' --no-pager -o cat | grep -o 'Tools in session ([^)]*)' | tail -1"
  sleep 3
fi

push
T0=$("${R[@]}" 'date -u +%H:%M:%S')
echo "$T0 said: $2"
printf '%s' "$2" | "${R[@]}" "$PY /tmp/rpc.py say" 2>&1 | grep -v '^Warning'
sleep "${3:-30}"
# Tool calls, transcripts and the token count of each response, with UTC times.
# The first "usage in=" of a fresh session is the fixed prefix (instructions +
# tools). out_audio on the response that carries a tool call is its spoken
# preamble (0 = silent until the result); "Preamble transcript" is its text.
"${R[@]}" "journalctl -u reachy-mini-daemon --since '$T0' --no-pager -o short-iso | grep reachy_companion \
  | grep -E 'Tool call received|Tools in session|role=user|role=assistant content=|transcript|casting|NAS stream: serving|Response usage' \
  | sed -E 's/^[0-9-]+T([0-9:]+)[^|]*\| /\1 /; s/^([0-9:]+) Response usage: id=[^ ]* status=[a-z]* (in=[0-9]+ in_cached=[0-9]+).* (out_audio=[0-9-]+).*/\1   [usage \2 \3]/' | cut -c1-420"
"${R[@]}" "$PY /tmp/tv_state.py"
