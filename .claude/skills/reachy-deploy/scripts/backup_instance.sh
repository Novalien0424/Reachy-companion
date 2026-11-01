#!/bin/bash
# Runs ON the robot (deploy.sh copies it to /tmp). Copies the instance state into a fresh, empty,
# mode-700 directory and writes a manifest: names, modes, sizes and file counts, never a
# credential filename (those are account addresses). The restore reads the manifest only.
set -u
INST="$HOME/.config/reachy_companion"
BACKUP="/tmp/reachy_companion_backup/$(date -u +%Y%m%dT%H%M%SZ)-$$"
mkdir -p "$BACKUP" && chmod 700 "$BACKUP"
# A fresh directory every time: a stale copy must never come back over a file absent now.
[ -z "$(ls -A "$BACKUP")" ] || { echo "FATAL: backup dir not empty"; exit 1; }
: > "$BACKUP/manifest.txt"
for NAME in .env persona.md memory.v1.json faces.v1.json people.v1.json google-oauth.json \
            nas-video-index.json pending_sleep_summary.json; do
  if [ -e "$INST/$NAME" ]; then
    cp -a "$INST/$NAME" "$BACKUP/$NAME"
    echo "file $NAME $(stat -c '%a %s' "$INST/$NAME")" >> "$BACKUP/manifest.txt"
  else
    echo "absent $NAME" >> "$BACKUP/manifest.txt"
  fi
done
for D in google-workspace-mcp face_snapshots; do
  if [ -d "$INST/$D" ]; then
    cp -a "$INST/$D" "$BACKUP/$D"
    echo "dir $D $(find "$INST/$D" -type f | wc -l) files" >> "$BACKUP/manifest.txt"
  else
    echo "absent $D" >> "$BACKUP/manifest.txt"
  fi
done
echo "backup: $BACKUP"
cat "$BACKUP/manifest.txt"
