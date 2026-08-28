#!/bin/sh
# Start the Mac-side management backend on http://127.0.0.1:8710.
#
# It reuses `reachy_companion/.venv` (fastapi + uvicorn are already there, and
# `reachy_companion` itself is importable from it, which is what lets the store
# share the robot's normalization rules). The bind is localhost-only on
# purpose — see README.md.
cd "$(dirname "$0")"
exec ../reachy_companion/.venv/bin/python -m uvicorn backend.app:app --host 127.0.0.1 --port 8710 "$@"
