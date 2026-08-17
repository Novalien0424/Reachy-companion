# Runs the companion app against the local mockup-sim daemon (D-008).
#
# Preconditions:
#   1. scripts\dev_daemon.ps1 is already running (this script refuses to start
#      without a daemon answering, rather than letting the SDK time out).
#   2. reachy_companion\.env holds OPENAI_API_KEY.
#
# Two dev-only details this script exists to get right:
#   * CWD must be the app dir. config.py loads .env with find_dotenv(usecwd=True),
#     which searches upward from the *current working directory*, so running from
#     the repo root silently picks up the repo-root .env instead.
#   * The daemon port. The SDK defaults to 8000, which on this machine belongs to
#     the Reachy Mini Control desktop app; the dev daemon runs on 8001. main.py
#     reads REACHY_DAEMON_PORT at the ReachyMini construction site.
#
# The console script (reachy-companion) is deliberate: `python -m reachy_companion.main`
# takes the ReachyMiniApp path, which builds its own ReachyMini with the SDK's
# default port and no override seam.
#
# The locked profile needs no env var — LOCKED_PROFILE overrides
# REACHY_MINI_CUSTOM_PROFILE (config.py).

[CmdletBinding()]
param(
    # Daemon FastAPI port; must match dev_daemon.ps1's --fastapi-port.
    [int]$DaemonPort = 8001,

    # Passed through to the app. Defaults to --ui, which serves the settings page
    # and the JSON-RPC control surface (ws://127.0.0.1:7860/rpc) alongside the
    # console — the dev handle for conversation.say / conversation.interrupt.
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$AppArgs = @('--ui')
)

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$appDir = Join-Path $repoRoot 'reachy_companion'
$exe = Join-Path $repoRoot '.venv\Scripts\reachy-companion.exe'

if (-not (Test-Path $exe)) {
    throw "reachy-companion is not installed in $repoRoot\.venv. Run: .venv\Scripts\pip install -e reachy_companion"
}
if (-not (Test-Path (Join-Path $appDir '.env'))) {
    throw "$appDir\.env is missing. Copy .env.example and fill OPENAI_API_KEY."
}

try {
    Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 -Uri "http://127.0.0.1:$DaemonPort/docs" | Out-Null
}
catch {
    throw "No Reachy Mini daemon answering on 127.0.0.1:$DaemonPort. Start scripts\dev_daemon.ps1 first."
}

$env:REACHY_DAEMON_PORT = $DaemonPort
Set-Location $appDir
& $exe @AppArgs
exit $LASTEXITCODE
