# Deploying from the Windows PC

The Mac is the primary deploy box (key-authorized `ssh`/`scp`, see SKILL.md
§ Access). On the Windows PC (`C:\Project\Reachy-mini`) the same procedure
runs with PuTTY, with these differences:

- **Credentials:** read the keys from the repo-root `.env` at run time
  (`Get-Content .env | …`, or `$env:REACHY_HOST` once loaded).
- **Host key:** a fourth key, `REACHY_HOSTKEY`, holds the robot's SSH host-key
  fingerprint in the exact form `plink` prints it. Every `plink`/`pscp` call
  runs with `-batch` and therefore cannot answer the host-key prompt, so pass
  `-hostkey $env:REACHY_HOSTKEY`. Obtain it once from an interactive `plink`
  session, put it in the repo-root `.env`, and never disable host-key checking
  or paste the fingerprint into a tracked file. `.env.example` carries the
  placeholder.
- **Password auth:** Windows OpenSSH prompts interactively; for automation use
  PuTTY's `plink`/`pscp -pw`, or offer the operator one-time SSH key setup
  (append a public key to `~/.ssh/authorized_keys` for `REACHY_SSH_USER` — ask
  first).
- **Scripts:** `deploy.sh` and `persona_sync.sh` are bash over ssh. On Windows run their steps
  by hand, with `pscp -pw` for `scp` and `plink` for `ssh`.
- **Run plink/pscp from PowerShell, never Git Bash.** Git Bash rewrites a
  remote command whose first token is a POSIX path (`/venvs/...` becomes
  `C:/Program Files/...`): the force-reinstall silently doesn't run, and the
  plain install then reports "already installed" because the wheel version
  hasn't changed.
