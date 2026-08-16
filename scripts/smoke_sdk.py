"""Smoke test: SDK client connects to a local daemon and reads state.

Precondition: scripts/dev_daemon.ps1 running in another terminal.
"""
from reachy_mini import ReachyMini

# Port 8000 is held by the Reachy Mini Control desktop app; dev daemon coexists on 8001.
with ReachyMini(connection_mode="localhost_only", port=8001, media_backend="no_media") as mini:
    pose = mini.get_current_head_pose()
    assert pose.shape == (4, 4), f"unexpected head pose shape {pose.shape}"
    print("OK: connected, head pose:\n", pose)
