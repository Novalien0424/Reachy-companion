#!/bin/sh
# Start the Mac-side management backend on http://${COMPANION_BACKEND_HOST}:8710.
#
# It reuses `reachy_companion/.venv` (fastapi + uvicorn are already there, and
# `reachy_companion` itself is importable from it, which is what lets the store
# share the robot's normalization rules).
#
# COMPANION_BACKEND_HOST picks the bind address (default 127.0.0.1, localhost
# only). Set it to this Mac's Tailscale IP to reach the UI from other tailnet
# devices without exposing it on the home LAN — see README.md ("Access over
# Tailscale"). The backend has no auth, so bind only to interfaces you trust.
cd "$(dirname "$0")"
exec ../reachy_companion/.venv/bin/python -m uvicorn backend.app:app \
  --host "${COMPANION_BACKEND_HOST:-127.0.0.1}" --port 8710 "$@"
