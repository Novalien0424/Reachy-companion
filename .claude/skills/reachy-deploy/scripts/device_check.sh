#!/bin/bash
# The playback tools against the real TV, NAS and speaker, with zero model tokens. Run from the Mac.
# Wakes the app once with the QA flag (so /rpc carries qa.tool), mutes the mic, drives each tool as
# the model would, reads the TV back after each step, then puts Reachy to sleep.
#
#   device_check.sh [nas|youtube|speaker|both|all|sleep|paused-sleep] [nas place] [youtube query] [song]
#
# sleep:        play, sleep with the clip on the TV, read the TV for 90 s (a NAS-served clip outlives
#               the app), then turn it off.
# paused-sleep: play, pause, sleep 60 s, wake, resume (the woken app records the paused clip again).
set -u
REPO=$(git -C "$(dirname "$0")" rev-parse --show-toplevel) || exit 1
HERE=$(cd "$(dirname "$0")" && pwd)
H=$(grep '^REACHY_HOST=' "$REPO/.env" | cut -d= -f2-)
U=$(grep '^REACHY_SSH_USER=' "$REPO/.env" | cut -d= -f2-)
R=(ssh -o BatchMode=yes "$U@$H")
PY='/venvs/apps_venv/bin/python -I'
WHAT=${1:-all}; PLACE=${2:-UK}; YTQ=${3:-relaxing piano music}; SONG=${4:-周杰倫 晴天}
redact() { LC_ALL=C sed -E 's/([0-9]{1,3}\.){3}[0-9]{1,3}/<ip>/g; s#https?://[^" ]+#<url>#g' | cut -b1-260; }
tool() { "${R[@]}" "$PY /tmp/rpc.py tool $1 '$2'" 2>&1 | grep -v '^Warning' | redact; }
music() { tool music "{\"action\":\"$1\"}"; }
nas_play() { tool nas "{\"action\":\"play\",\"place\":\"$PLACE\"}"; }
tv() { "${R[@]}" "$PY /tmp/tv_state.py" 2>&1 | redact; }
step() { echo "-- $1"; }
want() { [[ " $* " == *" $WHAT "* ]]; }

wake() {
  "${R[@]}" 'touch /tmp/reachy_qa; curl -s -m 10 -X POST http://127.0.0.1:8000/api/apps/start-app/reachy_companion >/dev/null'
  for _ in $(seq 30); do sleep 2; "${R[@]}" "$PY /tmp/rpc.py mute" >/dev/null 2>&1 && break; done
  echo "app up; mic muted"; tv
}
sleep_app() {
  "${R[@]}" "$PY /tmp/rpc.py unmute >/dev/null 2>&1; curl -s -m 30 -X POST http://127.0.0.1:8000/api/apps/stop-current-app >/dev/null"
  for _ in $(seq 15); do
    sleep 2; S=$("${R[@]}" 'curl -s -m 5 http://127.0.0.1:8000/api/apps/current-app-status')
    [ "$S" = null ] && { echo "app stopped (asleep)"; return 0; }
  done
  "${R[@]}" 'rm -f /tmp/reachy_qa /tmp/rpc.py /tmp/tv_state.py'; echo "app still not stopped: $S (wedge? see Hard limits)"; exit 3
}
# Pause, resume, stop with a bookmark, resume from it, stop: the same for every TV source.
cycle() {
  step "pause"; music pause; tv; sleep 5; tv
  step "resume"; music resume; sleep 4; tv
  sleep 6; step "stop (bookmark)"; music stop; tv
  step "resume from the bookmark"; music resume; sleep 3; tv
  step "stop"; music stop; tv
}

scp -q -o BatchMode=yes "$HERE/rpc.py" "$HERE/tv_state.py" "$U@$H:/tmp/" || exit 1
wake

if want nas all sleep; then step "nas play $PLACE"; nas_play; sleep 10; tv; cycle; fi
if want youtube all; then step "youtube play"; tool youtube "{\"action\":\"play\",\"query\":\"$YTQ\"}"; sleep 12; tv; cycle; fi
if want speaker all; then
  step "speaker play"; tool music "{\"action\":\"play\",\"query\":\"$SONG\"}"; sleep 8
  step "pause"; music pause; sleep 3; step "resume"; music resume; sleep 3; step "stop"; music stop
fi
if want both all; then
  step "speaker + TV, then one stop"; tool music "{\"action\":\"play\",\"query\":\"$SONG\"}"
  nas_play; sleep 10; tv; music stop; tv
fi
if want sleep; then step "nas play, then sleep with it on the TV"; nas_play; sleep 8; tv; fi
if want paused-sleep; then
  step "nas play, pause, then sleep"; nas_play; sleep 10; music pause; tv
  sleep_app
  for T in 30 60; do sleep 30; step "asleep ${T}s"; tv; done
  step "wake, then resume"; wake; music resume; sleep 4; tv
  "${R[@]}" 'journalctl --since "-2 min" --no-pager | grep -o "had no record of; recorded as [a-z]*\|video session: started ([a-z]*)" | tail -2'
  step "stop"; music stop; tv
fi

"${R[@]}" 'rm -f /tmp/reachy_qa'
sleep_app
if want sleep; then
  for T in 30 60 90; do sleep 30; step "asleep ${T}s"; tv; done
  step "TV off"; "${R[@]}" "$PY /tmp/tv_state.py off" 2>&1 | redact
fi
"${R[@]}" 'rm -f /tmp/rpc.py /tmp/tv_state.py'
