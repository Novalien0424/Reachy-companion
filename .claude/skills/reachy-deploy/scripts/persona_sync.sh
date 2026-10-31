#!/bin/bash
# Keep the robot's instance persona.md in step with the repo copy (D-016). Run from the Mac.
#
#   persona_sync.sh status   compare the two; name the repo revision a stale robot copy matches
#   persona_sync.sh push     copy the repo persona.md to the robot, then verify its sha
#
# The robot copy (~/.config/reachy_companion/persona.md) is what Reachy reads ("persona: instance
# persona.md"). A deploy never touches it, so a repo edit reaches the robot only through `push`.
set -u
REPO=$(git -C "$(dirname "$0")" rev-parse --show-toplevel) || exit 1
H=$(grep '^REACHY_HOST=' "$REPO/.env" | cut -d= -f2-)
U=$(grep '^REACHY_SSH_USER=' "$REPO/.env" | cut -d= -f2-)
R=(ssh -o BatchMode=yes -o ConnectTimeout=10 "$U@$H")
# The instance directory the app reads since v1.25.0 (`config.resolve_instance_path`).
INST_CMD='echo "$HOME/.config/reachy_companion"'
LOCAL="$REPO/persona.md"
sha() { shasum -a 256 | cut -c1-12; }

robot_sha() { "${R[@]}" "sha256sum \"\$($INST_CMD)/persona.md\" 2>/dev/null | cut -c1-12"; }

case "${1:-}" in
  status)
    L=$(sha < "$LOCAL"); B=$(robot_sha)
    echo "repo  $L"; echo "robot ${B:-absent}"
    if [ "$L" = "$B" ]; then echo "in step"; exit 0; fi
    for c in $(git -C "$REPO" log --format=%h -- persona.md); do
      if [ "$(git -C "$REPO" show "$c:persona.md" | sha)" = "$B" ]; then
        echo "robot copy = repo revision $c ($(git -C "$REPO" log -1 --format='%ad' --date=short "$c")): stale, safe to push"
        exit 1
      fi
    done
    echo "robot copy matches no repo revision: it was edited on the robot; bring it into the repo before pushing"
    exit 2
    ;;
  push)
    INST=$("${R[@]}" "$INST_CMD") || exit 1
    scp -q -o BatchMode=yes "$LOCAL" "$U@$H:$INST/persona.md" || exit 1
    L=$(sha < "$LOCAL"); B=$(robot_sha)
    [ "$L" = "$B" ] && echo "pushed; robot sha $B (loads at the next wake)" || { echo "sha mismatch after push: $B"; exit 1; }
    ;;
  *) sed -n '2,8p' "$0"; exit 2 ;;
esac
