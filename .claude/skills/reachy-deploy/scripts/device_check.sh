#!/bin/bash
# Tier 1 of the 2026-10-31 QA plan: the playback tools against the real TV, NAS and speaker, with
# zero model tokens. Run from the Mac. Wakes the app once with the QA flag (so /rpc carries
# qa.tool), mutes the mic, drives each tool as the model would, reads the TV back after each step,
# then removes the flag, unmutes and puts Reachy to sleep.
#
#   device_check.sh [nas|youtube|speaker|both|all|sleep|paused-sleep] [nas place] [youtube query] [song]
#
# `sleep` runs the nas steps, plays again and puts Reachy to sleep with the clip on the TV, then reads
# the TV for 90 s (W6: a clip served by the NAS outlives the app) and turns it off.
# `paused-sleep` plays, pauses, sleeps 60 s, wakes again and resumes: the TV keeps the paused clip,
# and the woken app records it again (1.36.1).
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
tv() { "${R[@]}" "$PY /tmp/tv_state.py" 2>&1 | redact; }
step() { echo "-- $1"; }

wake() {
  "${R[@]}" 'touch /tmp/reachy_qa; curl -s -m 10 -X POST http://127.0.0.1:8000/api/apps/start-app/reachy_companion >/dev/null'
  for _ in $(seq 1 30); do
    sleep 2
    "${R[@]}" "$PY /tmp/rpc.py mute" >/dev/null 2>&1 && break
  done
  echo "app up; mic muted"; tv
}
sleep_app() {
  "${R[@]}" "$PY /tmp/rpc.py unmute >/dev/null 2>&1; curl -s -m 30 -X POST http://127.0.0.1:8000/api/apps/stop-current-app >/dev/null"
  for _ in $(seq 1 15); do
    sleep 2
    S=$("${R[@]}" 'curl -s -m 5 http://127.0.0.1:8000/api/apps/current-app-status')
    [ "$S" = null ] && { echo "app stopped (asleep)"; return 0; }
  done
  "${R[@]}" 'rm -f /tmp/reachy_qa /tmp/rpc.py /tmp/tv_state.py'; echo "app still not stopped: $S (wedge? see Hard limits)"; exit 3
}

scp -q -o BatchMode=yes "$HERE/rpc.py" "$HERE/tv_state.py" "$U@$H:/tmp/" || exit 1
wake

if [ "$WHAT" = nas ] || [ "$WHAT" = all ] || [ "$WHAT" = sleep ]; then
  step "nas play $PLACE"; tool nas "{\"action\":\"play\",\"place\":\"$PLACE\"}"; sleep 10; tv
  step "pause"; tool music '{"action":"pause"}'; tv; sleep 5; tv
  step "resume"; tool music '{"action":"resume"}'; sleep 4; tv
  sleep 6; step "stop (bookmark)"; tv; tool music '{"action":"stop"}'; tv
  step "resume from the bookmark"; tool music '{"action":"resume"}'; sleep 3; tv
  step "stop"; tool music '{"action":"stop"}'; tv
fi
if [ "$WHAT" = youtube ] || [ "$WHAT" = all ]; then
  step "youtube play"; tool youtube "{\"action\":\"play\",\"query\":\"$YTQ\"}"; sleep 12; tv
  step "pause"; tool music '{"action":"pause"}'; tv
  step "resume"; tool music '{"action":"resume"}'; sleep 4; tv
  sleep 6; step "stop (bookmark)"; tool music '{"action":"stop"}'; tv
  step "resume from the bookmark"; tool music '{"action":"resume"}'; sleep 3; tv
  step "stop"; tool music '{"action":"stop"}'; tv
fi
if [ "$WHAT" = speaker ] || [ "$WHAT" = all ]; then
  step "speaker play"; tool music "{\"action\":\"play\",\"query\":\"$SONG\"}"; sleep 8
  step "pause"; tool music '{"action":"pause"}'; sleep 3
  step "resume"; tool music '{"action":"resume"}'; sleep 3
  step "stop"; tool music '{"action":"stop"}'
fi
if [ "$WHAT" = both ] || [ "$WHAT" = all ]; then
  step "speaker + TV, then one stop"; tool music "{\"action\":\"play\",\"query\":\"$SONG\"}"
  tool nas "{\"action\":\"play\",\"place\":\"$PLACE\"}"; sleep 10; tv
  tool music '{"action":"stop"}'; tv
fi
if [ "$WHAT" = sleep ]; then
  step "nas play, then sleep with it on the TV"; tool nas "{\"action\":\"play\",\"place\":\"$PLACE\"}"; sleep 8; tv
fi
if [ "$WHAT" = paused-sleep ]; then
  step "nas play, pause, then sleep"; tool nas "{\"action\":\"play\",\"place\":\"$PLACE\"}"; sleep 10
  tool music '{"action":"pause"}'; tv
  sleep_app
  for T in 30 60; do sleep 30; step "asleep ${T}s"; tv; done
  step "wake, then resume"; wake
  tool music '{"action":"resume"}'; sleep 4; tv
  "${R[@]}" 'journalctl --since "-2 min" --no-pager | grep -o "had no record of; recorded as [a-z]*\|video session: started ([a-z]*)" | tail -2'
  step "stop"; tool music '{"action":"stop"}'; tv
fi

"${R[@]}" 'rm -f /tmp/reachy_qa'
sleep_app
if [ "$WHAT" = sleep ]; then
  for T in 30 60 90; do sleep 30; step "asleep ${T}s"; tv; done
  step "TV off"; "${R[@]}" "$PY /tmp/tv_state.py off" 2>&1 | redact
fi
"${R[@]}" 'rm -f /tmp/rpc.py /tmp/tv_state.py'
exit 0
