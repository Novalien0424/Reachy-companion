# Starts the Reachy Mini daemon in mockup-sim mode for local development (D-008).
# Port 8000 is held by the Reachy Mini Control desktop app; dev daemon coexists on 8001.
& "$PSScriptRoot\..\.venv\Scripts\reachy-mini-daemon" --mockup-sim --fastapi-port 8001
