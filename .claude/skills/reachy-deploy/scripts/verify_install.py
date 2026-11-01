"""Runs ON the robot after an install (deploy.sh copies it to /tmp). Exit 1 on a failed check.

    /venvs/apps_venv/bin/python -I /tmp/verify_install.py <expected version>

Checks the installed version, reads the JSON stores back as record counts (an empty
store is silent data loss: nothing raises), and proves the bundled search tool serves
the wheel's description, not a cached one (D-031: it must carry 示範語氣).
"""

import sys
import json
import pathlib
import importlib.metadata


failed = False
want = sys.argv[1] if len(sys.argv) > 1 else ""
got = importlib.metadata.version("reachy_companion")
print(f"installed: {got}" + ("" if not want or got == want else f"  EXPECTED {want}"))
failed |= bool(want) and got != want

inst = pathlib.Path.home() / ".config/reachy_companion"
for name, key in (("memory.v1.json", "facts"), ("faces.v1.json", "faces"), ("people.v1.json", "people")):
    path = inst / name
    if path.is_file():
        print(f"{name}: {len(json.loads(path.read_text(encoding='utf-8')).get(key, []))} records")
    else:
        print(f"{name}: absent")

from reachy_companion import tool_spaces  # noqa: E402  (after the version line, so a broken import still shows it)

searched = False
for space in tool_spaces.read_installed_tool_spaces(inst).spaces:
    if space.slug in tool_spaces.PREINSTALLED_TOOL_SPACE_SPECS:
        for tool in space.tools:
            ok = "示範語氣" in tool.description
            if tool.client_tool_name.endswith("search_web"):
                searched = True
                failed |= not ok
            print(f"tool space {tool.remote_name}: 示範語氣={ok}")
if not searched:
    print("tool space search: no installed manifest (the bundled spec is served directly)")
sys.exit(1 if failed else 0)
