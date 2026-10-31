"""Print the cast TV's Home Assistant state; `off` turns it off first. Runs ON the robot.

The HA token and the entity ID stay on the robot: the output names neither.
"""

import json
import pathlib
import sys
import urllib.request

env = {}
for line in (pathlib.Path.home() / ".config/reachy_companion/.env").read_text().splitlines():
    if "=" in line and not line.lstrip().startswith("#"):
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")

entity = env["HANOVA_CAST_ENTITY"]
headers = {"Authorization": f"Bearer {env['HA_TOKEN']}", "Content-Type": "application/json"}
if len(sys.argv) > 1 and sys.argv[1] == "off":
    req = urllib.request.Request(
        f"{env['HA_URL']}/api/services/media_player/turn_off",
        data=json.dumps({"entity_id": entity}).encode(), headers=headers, method="POST")
    urllib.request.urlopen(req, timeout=10).read()
req = urllib.request.Request(f"{env['HA_URL']}/api/states/{entity}", headers=headers)
state = json.loads(urllib.request.urlopen(req, timeout=10).read())
attrs = state.get("attributes", {})
pos = attrs.get("media_position")
print("tv:", state["state"], "|", attrs.get("app_name"), "|", attrs.get("media_title"),
      "|", f"pos {pos:.1f}s" if isinstance(pos, (int, float)) else "pos -")
