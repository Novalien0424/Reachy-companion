#!/bin/bash
# Build the wheel and install it on the robot as an app (D-009), from the Mac:
#
#   deploy.sh
#
# Steps: build → entry-point check → version gate → stop the app → back up the instance state →
# two-step install → verify → discovery. Leaves the app stopped. Exit 3 = the app stop wedged
# (see Hard limits); any other non-zero exit = a failed step, nothing after it ran.
set -eu -o pipefail
REPO=$(git -C "$(dirname "$0")" rev-parse --show-toplevel)
HERE=$(cd "$(dirname "$0")" && pwd)
H=$(grep '^REACHY_HOST=' "$REPO/.env" | cut -d= -f2-)
U=$(grep '^REACHY_SSH_USER=' "$REPO/.env" | cut -d= -f2-)
R=(ssh -o BatchMode=yes "$U@$H")
API="http://$H:8000"
PKG="$REPO/reachy_companion"

V=$(sed -nE 's/^version = "([^"]+)"/\1/p' "$PKG/pyproject.toml")
WHL="$PKG/dist/reachy_companion-$V-py3-none-any.whl"
(cd "$PKG" && uv build -q --wheel .)
unzip -p "$WHL" '*.dist-info/entry_points.txt' | grep -A3 reachy_mini_apps | grep -q reachy_companion \
  || { echo "FATAL: no reachy_mini_apps entry point in the wheel"; exit 1; }
echo "built $V  sha256 $(shasum -a 256 "$WHL" | cut -c1-8)"

# The daemon force-syncs the apps venv's reachy_mini to its own version, so a daemon below the
# app's floor makes the app undeployable by any app-level means. Stop and report; never upgrade.
FLOOR=$(grep -oE 'reachy-mini>=[0-9.]+' "$PKG/pyproject.toml" | cut -d= -f2)
DAEMON=$(curl -s -m 10 "$API/update/install-source" | python3 -c 'import json,sys; print(json.load(sys.stdin)["version"])')
[ "$(printf '%s\n%s\n' "$FLOOR" "$DAEMON" | sort -V | head -1)" = "$FLOOR" ] \
  || { echo "FATAL: daemon $DAEMON is below the app's reachy-mini floor $FLOOR"; exit 1; }
echo "daemon $DAEMON (floor $FLOOR)"

if [ "$(curl -s -m 5 "$API/api/apps/current-app-status")" != null ]; then
  echo "stop: $(curl -s -m 30 -X POST "$API/api/apps/stop-current-app")"
  for _ in $(seq 20); do [ "$(curl -s -m 5 "$API/api/apps/current-app-status")" = null ] && break; sleep 1; done
  ST=$(curl -s -m 5 "$API/api/apps/current-app-status")
  [ "$ST" = null ] || { echo "WEDGED: $ST"; exit 3; }
fi

scp -q -o BatchMode=yes "$WHL" "$HERE/backup_instance.sh" "$HERE/verify_install.py" "$U@$H:/tmp/"
"${R[@]}" 'bash /tmp/backup_instance.sh'
# Never a bare --force-reinstall: it would rebuild reachy-mini's pinned PyGObject from source.
P=/venvs/apps_venv/bin/python
OUT=$("${R[@]}" "$P -m pip install -q --force-reinstall --no-deps /tmp/$(basename "$WHL") \
  && $P -m pip install -q /tmp/$(basename "$WHL")" 2>&1) || { echo "$OUT"; echo "FATAL: pip install failed"; exit 1; }
echo "$OUT" | grep -v '^Warning\|already satisfied' || true
"${R[@]}" "$P -I /tmp/verify_install.py $V"
if ! curl -s -m 10 "$API/api/apps/list-available/installed" | grep -q '"reachy_companion"'; then
  echo "FATAL: not in the installed-apps list"; exit 1
fi
echo "discovery: reachy_companion listed"
"${R[@]}" "rm -f /tmp/$(basename "$WHL") /tmp/backup_instance.sh /tmp/verify_install.py"
echo "installed $V; app stopped (start: live_case.sh 1 or the dashboard)"
