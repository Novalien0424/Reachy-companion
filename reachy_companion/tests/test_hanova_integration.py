"""Whole-port invariants: inventory, descriptions, env docs, no leaked identifiers."""

import re
import importlib
import subprocess
from pathlib import Path

import pytest


PACKAGE_ROOT = Path(__file__).parents[1]
SRC_ROOT = PACKAGE_ROOT / "src" / "reachy_companion"
ENV_EXAMPLE = PACKAGE_ROOT / ".env.example"
PROFILE = PACKAGE_ROOT / "profiles" / "_reachy_companion_locked_profile" / "profile.md"

PORTED_TOOLS = frozenset(
    {
        "play_music",
        "stop_music",
        "play_video",
        "show_on_tv",
        "nas_video_query",
        "play_nas_video",
        "nas_play_folder",
        "nas_skip",
        "calendar_add",
        "calendar_delete",
        "calendar_list",
        "task_add",
        "task_complete",
        "task_delete",
        "task_list",
        "notion_add",
        "drive_list",
        "drive_trash",
        "drive_upload",
        "email_send",
    }
)

GATED_TOOLS = frozenset(
    {
        "calendar_delete",
        "task_complete",
        "task_delete",
        "drive_trash",
        "drive_upload",
        "email_send",
    }
)

HOUSE_BOUND_TOOLS = frozenset(
    {"play_video", "show_on_tv", "nas_video_query", "play_nas_video", "nas_play_folder", "nas_skip"}
)

# 2026-08-31 tool diet: eighteen of the 22 ported tools left the profile's
# `default_tools` and are now the *actions* of six action-enum façades
# (`tools/tool_family.py`). Nothing about those eighteen changed -- same module,
# same `Tool.name`, same `settings.tool_status` row, same confirmation gate --
# so every invariant below still holds; it just has to look one level down to
# find them.
FAMILY_TOOLS = frozenset({"calendar", "tasks", "drive", "nas", "music", "tv"})


@pytest.fixture
def registry():
    """Build the real registry once and hand back name -> Tool instance."""
    core_tools = importlib.import_module("reachy_companion.tools.core_tools")
    core_tools.initialize_tools(force=True)
    try:
        yield core_tools.get_tools()
    finally:
        core_tools._TOOLS_SIGNATURE = None


@pytest.fixture
def specs():
    """Build the real registry once and hand back name -> spec."""
    core_tools = importlib.import_module("reachy_companion.tools.core_tools")
    core_tools.initialize_tools(force=True)
    try:
        yield {spec["name"]: spec for spec in core_tools.get_tool_specs()}
    finally:
        core_tools._TOOLS_SIGNATURE = None


@pytest.fixture
def family_actions(registry):
    """Map each delegate's tool name to (its family's name, the delegate instance)."""
    actions = {}
    for family_name in sorted(FAMILY_TOOLS):
        assert family_name in registry, f"the locked profile no longer lists {family_name}"
        for tool in type(registry[family_name]).ACTIONS.values():
            actions[tool.name] = (family_name, tool)
    return actions


@pytest.fixture
def ported_tools(registry, family_actions):
    """Map every ported tool name to its live instance, family action or not."""
    live = {name: registry[name] for name in PORTED_TOOLS if name in registry}
    live.update({name: tool for name, (_, tool) in family_actions.items()})
    return live


def test_all_twenty_ported_tools_reach_the_model(ported_tools):
    """R2: every ported tool is still reachable in the session.

    Twenty, not the original twenty-two: `self_destruct` and `mad_laugh` were
    retired on 2026-08-31 to buy two of the tool diet's slots back.
    """
    assert len(PORTED_TOOLS) == 20
    missing = sorted(PORTED_TOOLS - set(ported_tools))
    assert missing == [], f"not reachable: {missing}"


def test_every_ported_description_is_short_and_identifier_free(ported_tools):
    """R10: descriptions go into the model prompt and into every transcript."""
    for name in sorted(PORTED_TOOLS):
        description = ported_tools[name].description
        assert len(description) <= 120, f"{name} description is {len(description)} chars"
        assert "@" not in description, f"{name} description contains an address-like token"
        assert "media_player." not in description, f"{name} description contains an HA entity id"
        assert "/Users/" not in description


def test_every_family_description_is_identifier_free(specs):
    """The six façade descriptions replaced eighteen in the prompt, so they count too.

    No length cap: a family description carries the "Use when / Do NOT use when"
    routing block that is the whole point of the consolidation. The identifier
    rules are unchanged -- these strings still reach the model and the logs.
    """
    for name in sorted(FAMILY_TOOLS):
        description = specs[name]["description"]
        assert "@" not in description, f"{name} description contains an address-like token"
        assert "media_player." not in description, f"{name} description contains an HA entity id"
        assert "/Users/" not in description


def test_every_gated_tool_exposes_a_confirm_flag(specs, family_actions):
    """R3: the gate is part of the schema the model sees, not just the code.

    For a family action the schema the model sees is the *family's*, so that is
    the one checked: a union that dropped `confirm` would leave the model no way
    to answer the read-back it was told to perform.
    """
    for name in sorted(GATED_TOOLS):
        owner = family_actions[name][0] if name in family_actions else name
        schema = specs[owner]["parameters"]
        properties = schema["properties"]
        assert "confirm" in properties, f"{name} has no confirm parameter"
        assert properties["confirm"]["type"] == "boolean"
        assert "confirm" not in schema.get("required", [])


def test_house_bound_tools_branch_all_three_home_verdicts():
    """R4 + finding 12 + round 2 finding 3: three verdicts, three branches.

    Round 1 only tested `AWAY`, so `UNKNOWN` fell straight through and the tool
    did the work anyway -- a VPN, a 401, a 5xx or a timeout could all still fire
    a real house action. The exact `if verdict != HOME:` line is asserted here so
    a future tool cannot quietly go back to a two-way branch.
    """
    for name in sorted(HOUSE_BOUND_TOOLS):
        source = (SRC_ROOT / "tools" / f"{name}.py").read_text(encoding="utf-8")
        assert "home_state" in source, name
        assert "verdict = await home_state()" in source, name
        assert "if verdict == AWAY:" in source, name
        assert "return away_from_home()" in source, name
        assert "if verdict != HOME:" in source, f"{name} does not branch on UNKNOWN"
        assert "return home_unknown()" in source, name
        # `away_from_home` is only ever returned for AWAY, so no tool may branch
        # on a bare boolean here -- and `is_home` no longer exists at all.
        assert "is_home" not in source, name


def test_the_boolean_home_shortcut_does_not_exist_anywhere():
    """Round 2, finding 3: a boolean cannot carry three verdicts."""
    from reachy_companion import home_net

    assert not hasattr(home_net, "is_home")
    for path in SRC_ROOT.rglob("*.py"):
        if "__pycache__" in str(path):
            continue
        assert "is_home(" not in path.read_text(encoding="utf-8"), path.name


def test_the_unknown_status_is_its_own_name():
    """Round 2, finding 3: `home_status_unknown` must never read as absence."""
    from reachy_companion import home_net

    payload = home_net.home_unknown()
    assert payload["status"] == "home_status_unknown"
    assert payload["status"] != "away_from_home"


def test_cloud_and_music_tools_never_consult_the_home_probe():
    """R4: probing from a coffee shop would add latency for no reason."""
    for name in sorted(PORTED_TOOLS - HOUSE_BOUND_TOOLS):
        source = (SRC_ROOT / "tools" / f"{name}.py").read_text(encoding="utf-8")
        assert "home_state" not in source, f"{name} must not consult the home-network probe"
        assert "is_home" not in source, f"{name} must not consult the home-network probe"


def test_every_ported_tool_gates_on_its_own_prerequisites():
    """Finding 10: availability is per tool, not per family."""
    from reachy_companion.hanova import settings as hanova_settings

    for name in sorted(PORTED_TOOLS):
        assert name in hanova_settings.TOOL_PREREQS, name
        source = (SRC_ROOT / "tools" / f"{name}.py").read_text(encoding="utf-8")
        if name == "stop_music":
            continue  # the documented exemption: no prerequisites at all
        assert "settings.tool_status(self.name)" in source, name
        assert "family_enabled(" not in source, f"{name} still uses the old family gate"


def test_every_gated_tool_threads_the_claim_id():
    """Finding 4 + round 2 finding 2: every mutator presents its own claim id.

    The claim id is what identifies *this* authorisation. Without it,
    `complete("drive_trash")` spent whatever happened to be in the slot --
    including an action a newer session had just armed.
    """
    for name in sorted(GATED_TOOLS):
        source = (SRC_ROOT / "tools" / f"{name}.py").read_text(encoding="utf-8")
        assert "GATE.claim(self.name)" in source, name
        assert "GATE.complete(self.name, pending.claim_id)" in source, name
        assert "GATE.take(" not in source, f"{name} still consumes the gate before executing"
        # The bare, id-less forms must not survive anywhere.
        assert "GATE.complete(self.name)" not in source, f"{name} completes without a claim id"
        assert "GATE.release(self.name)" not in source, f"{name} releases without a claim id"


def test_every_gated_tool_classifies_transient_and_terminal_failures():
    """Round 2, finding 9: `release()` is for transient faults only.

    Re-arming after an authentication failure or a refused recipient keeps an
    approval alive for something that can never succeed as approved.
    """
    for name in sorted(GATED_TOOLS):
        source = (SRC_ROOT / "tools" / f"{name}.py").read_text(encoding="utf-8")
        assert "is_transient(exc)" in source, f"{name} treats every failure the same way"
        assert "GATE.release(self.name, pending.claim_id)" in source, name
        assert '"retryable": True' in source, name


def test_the_gate_rejects_a_stale_claim_id():
    """Round 2, finding 2, at the module level rather than per tool."""
    from reachy_companion.hanova.confirm import GATE

    GATE.reset()
    GATE.begin_session()
    try:
        GATE.arm("drive_trash", "move 'a.txt' to Drive trash", {"file_id": "f1"})
        stale = GATE.claim("drive_trash")
        assert stale is not None
        GATE.begin_session()
        GATE.arm("drive_trash", "move 'b.txt' to Drive trash", {"file_id": "f2"})
        assert GATE.complete("drive_trash", stale.claim_id) is False
        assert GATE.release("drive_trash", stale.claim_id) is False
        live = GATE.claim("drive_trash")
        assert live is not None and live.payload == {"file_id": "f2"}
    finally:
        GATE.reset()


def test_re_arming_an_executing_action_is_refused():
    """Round 2, finding 2: a destructive action mid-flight is not re-armable."""
    from reachy_companion.hanova.confirm import GATE

    GATE.reset()
    GATE.begin_session()
    try:
        GATE.arm("calendar_delete", "delete 'Dentist'", {"event_id": "abc"})
        assert GATE.claim("calendar_delete") is not None
        assert GATE.arm("calendar_delete", "delete 'Optician'", {"event_id": "xyz"})["status"] == ("action_in_flight")
    finally:
        GATE.reset()


def test_no_gated_tool_requires_its_action_field_in_the_schema():
    """Finding 4: the confirming call must not resupply the frozen field."""
    for name in sorted(GATED_TOOLS):
        module = importlib.import_module(f"reachy_companion.tools.{name}")
        tool_class = next(
            value
            for value in vars(module).values()
            if isinstance(value, type) and getattr(value, "name", None) == name
        )
        assert tool_class.parameters_schema.get("required", []) == [], name


def test_env_example_documents_every_setting_key():
    """R9: a key the code reads but nobody documents is a key nobody sets."""
    settings_source = (SRC_ROOT / "hanova" / "settings.py").read_text(encoding="utf-8")
    keys = set(re.findall(r'env_(?:str|int|float|path)\(\s*"([A-Z0-9_]+)"', settings_source))
    documented = ENV_EXAMPLE.read_text(encoding="utf-8")
    missing = sorted(key for key in keys if key not in documented)
    assert missing == [], f"undocumented in .env.example: {missing}"


def _scannable_paths():
    """Collect every tracked file this port writes, tests, docs and skills included.

    Round 2, finding 5: the previous version scanned `src/**/*.py` plus the
    profile plus one `.env.example`. That excluded the **tests** -- which is
    exactly where the round-1 leak was, a real private sentinel embedded as a
    needle -- and it excluded the documentation and the deploy skill, which are
    where connection identifiers actually lived.
    """
    repo_root = PACKAGE_ROOT.parent
    roots = [
        SRC_ROOT,
        PACKAGE_ROOT / "tests",
        PACKAGE_ROOT / "profiles",
        repo_root / "docs",
        repo_root / ".claude" / "skills",
    ]
    # `docs/superpowers/` holds the implementation plans and the external review
    # transcripts. Those quote findings verbatim -- including regex sources and
    # illustrative addresses -- so shape-matching them produces noise, not
    # signal. They are still covered by the Step 9b scan against the operator's
    # real values, which is the check that matters for them.
    excluded_prefixes = (repo_root / "docs" / "superpowers",)
    singles = [
        PROFILE,
        PACKAGE_ROOT / ".env.example",
        repo_root / ".env.example",
        repo_root / "persona.md",
        repo_root / "DECISIONS.md",
        repo_root / "progress.md",
        repo_root / "feature_list.json",
        # Final review, F1: this one is tracked, is rewritten at every handoff,
        # and described machine access -- so it was the likeliest tracked file in
        # the repository to carry a real identifier, and the scan did not read it.
        repo_root / "session-handoff.md",
    ]
    seen: set[Path] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or "__pycache__" in str(path):
                continue
            if any(str(path).startswith(str(prefix)) for prefix in excluded_prefixes):
                continue
            if path.suffix in {".py", ".md", ".json", ".toml", ".example", ".txt"}:
                seen.add(path)
    for path in singles:
        if path.is_file():
            seen.add(path)
    return sorted(seen)


# Round 2, finding 5: the private-range IPv4 pattern in round 1 was
# `\b(?:10|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b` -- three
# octets after `10`, but only **two** after the `192.168` alternative, so an
# ordinary address in that range did not match at all. Each alternative now
# names its own full four-octet shape. (No literal address appears in this
# comment: the scan below reads this very file.)
_PRIVATE_IPV4 = re.compile(
    r"\b(?:"
    r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r"|100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}"  # CGNAT / Tailscale
    r"|169\.254\.\d{1,3}\.\d{1,3}"
    r")\b"
)


def test_the_private_ip_pattern_matches_a_normal_private_address():
    """Round 2, finding 5: the round-1 regex could not match a plain RFC1918 address.

    It required three octets after `10` but only two after the `192.168`
    alternative, so the single commonest private address shape in the world
    slipped through the scan entirely. This pins the corrected pattern before
    anything relies on it.

    The addresses are **assembled from parts** on purpose: writing them as
    literals would plant private-address-shaped strings in this very file, which
    `test_no_personal_identifier_shape_is_committed_anywhere_this_port_writes`
    scans. A test for a leak detector must not be a leak.
    """
    private = [
        ".".join(parts)
        for parts in (
            ("10", "0", "0", "5"),
            ("192", "168", "1", "50"),
            ("172", "20", "10", "1"),
            ("100", "80", "3", "9"),  # CGNAT / Tailscale range
            ("169", "254", "1", "1"),  # link-local
        )
    ]
    for address in private:
        assert _PRIVATE_IPV4.search(address), address

    public = [".".join(parts) for parts in (("203", "0", "113", "20"), ("1", "2", "3", "4"), ("192", "169", "1", "1"))]
    for address in public:
        assert not _PRIVATE_IPV4.search(address), address


# Round 3, finding 4: the shapes, each with a label. The label is what a failure
# reports -- the matched text never is, because the matched text is the leak.
_FORBIDDEN_SHAPES = (
    ("gmail-address", re.compile(r"@gmail\.com")),
    ("google-calendar-id", re.compile(r"@group\.calendar\.google\.com")),
    ("macos-home-path", re.compile(r"/Users/[a-z]")),
    ("private-ipv4", _PRIVATE_IPV4),
    ("ha-media-player-entity", re.compile(r"\bmedia_player\.(?!example)[a-z0-9_]+\b")),
    ("aws-key-id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("ssh-host-key", re.compile(r"SHA256:[A-Za-z0-9+/=]{20,}")),
)

# The only files this port promises to leave **free** of these shapes. For them
# the tolerated count is zero. Everywhere else the bar is "no more than HEAD
# already had", because this port cannot be gated on identifiers that were
# committed before it started -- those are a separate, recorded clean-up.
_SCRUBBED_RELATIVE_PATHS = (
    ".claude/skills/reachy-deploy/SKILL.md",
    ".env.example",
    "reachy_companion/.env.example",
)

_ALLOWED_SHAPE_TOKENS = ("example.com", "example.invalid", "example_tv")

# D-020 (operator-authorized, 2026-08-22): the robot's LAN address and SSH user
# are deliberately recorded in `session-handoff.md` so a fresh checkout on the
# operator's second machine can reach the robot without hand-carrying files.
# This is an ABSOLUTE cap per (path, label) -- not additive with the HEAD
# grandfather -- so a second address in that file, or one anywhere else, still
# fails. The SSH password and host-key fingerprint remain forbidden everywhere.
_OPERATOR_AUTHORIZED_DISCLOSURES: dict[str, dict[str, int]] = {
    "session-handoff.md": {"private-ipv4": 1},
}


def _git_head_available() -> bool:
    """Report whether there is a HEAD to compare against (a tarball has none)."""
    try:
        done = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"],
            cwd=PACKAGE_ROOT.parent,
            capture_output=True,
            text=True,
        )
    except (OSError, ValueError):
        return False
    return done.returncode == 0


def _head_text(relative: str) -> str:
    """Return the file's content at HEAD, or "" when HEAD does not have that path."""
    done = subprocess.run(
        ["git", "show", f"HEAD:{relative}"],
        cwd=PACKAGE_ROOT.parent,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
    )
    return done.stdout if done.returncode == 0 else ""


def _shape_counts(text: str) -> dict[str, int]:
    """Count how many times each labelled shape occurs. Never returns the matches."""
    counts: dict[str, int] = {}
    for label, pattern in _FORBIDDEN_SHAPES:
        hits = sum(
            1
            for found in pattern.finditer(text)
            if not any(token in found.group(0) for token in _ALLOWED_SHAPE_TOKENS)
        )
        if hits:
            counts[label] = hits
    return counts


def test_no_personal_identifier_shape_is_committed_anywhere_this_port_writes():
    """The upstream repo leaked ~60 of these; this port must leak none.

    Review finding 6: the previous version of this test embedded a **real**
    private token as a needle, which put that identifier into the tracked
    repository -- inside the very test written to keep identifiers out of it.
    The needles are *shapes*, not values.

    Round 2, finding 5 widened the haystack: tests, docs, the profile directory
    and the deploy skill are all scanned now, and the private-address pattern
    actually matches private addresses.

    **Round 3, finding 4 made it executable.** As written it could not pass: it
    demanded zero matches everywhere, and several files in this repository
    already carried an identifier-shaped token before this port existed, so the
    gate failed on somebody else's history and would have been switched off. Two
    changes: the bar is now **"no more occurrences than `HEAD` already had"**
    except for the small set of files this port explicitly scrubs, where it stays
    zero; and a failure reports the **path, the shape label and the two counts**
    -- never the matched text, which was itself a disclosure into the terminal
    and into any transcript of the run.

    The check against the operator's **actual** values lives in the untracked
    scan in Step 9b, which reads them from the gitignored `.env` and the three
    private reports and never writes them anywhere.
    """
    if not _git_head_available():
        pytest.skip("no git HEAD to compare against; run this test in a checkout")

    repo_root = PACKAGE_ROOT.parent
    failures: list[str] = []
    tolerated: list[str] = []
    for path in _scannable_paths():
        relative = path.relative_to(repo_root).as_posix()
        now = _shape_counts(path.read_text(encoding="utf-8", errors="ignore"))
        if not now:
            continue
        scrubbed = relative in _SCRUBBED_RELATIVE_PATHS
        was = {} if scrubbed else _shape_counts(_head_text(relative))
        for label, count in sorted(now.items()):
            before = was.get(label, 0)
            allowed = _OPERATOR_AUTHORIZED_DISCLOSURES.get(relative, {}).get(label, 0)
            if count > max(before, allowed):
                failures.append(f"{relative}: {label} x{count} (HEAD had {before})")
            else:
                tolerated.append(f"{relative}: {label} x{count} (within HEAD or D-020 cap)")

    assert failures == [], (
        "identifier-shaped tokens introduced by this port (paths and counts only, "
        f"never the value): {failures}. Pre-existing and tolerated: {tolerated}"
    )


# Round 3, finding 4: a NAS fixture can be planted by ANY test file, not only by
# `test_hanova_nas.py`, and `monkeypatch.setenv` is written with either quote.
_NAS_FIXTURE_ASSIGNMENT = re.compile(r"""HANOVA_NAS_(?:SHARE|SUBPATH|CAST_SUBPATH)["']\s*,\s*["']([^"']*)["']""")

# The containment tests need one fixture that is deliberately *not* a
# `SENTINEL_*` value, to prove a path outside the configured subtree is refused.
# An ordinary English word is as plainly invented as a sentinel prefix, so it is
# enumerated here with its reason rather than waived by loosening the rule.
_SYNTHETIC_NAS_FIXTURES = {
    "Elsewhere": "test_hanova_nas.py: the out-of-subtree probe for validate_cast_path",
    "SomewhereElse": "test_hanova_nas.py: the out-of-subtree probe for HANOVA_NAS_SUBPATH",
}


def test_the_nas_fixtures_are_obviously_synthetic():
    """Round 2, finding 5: no fixture may be copied from the private manifest.

    A reader has to be able to tell at a glance that a share or folder name in
    this repository is invented. `SENTINEL_*_q4` is not a plausible NAS layout.

    **Round 3, finding 4 fixed the scoping.** The positive half -- the sentinels
    exist -- belongs to `test_hanova_nas.py` and stays there. The negative half
    was scoped to that same file, which is exactly the file least likely to
    offend; and it was written as a double-quote-only lookahead, so a fixture set
    with single quotes slipped past it. It now covers **every** test file and
    both quoting styles, and it fails loudly if it matches nothing at all --
    a scan that silently scans nothing is the failure mode this round is about.
    """
    nas_tests = (PACKAGE_ROOT / "tests" / "test_hanova_nas.py").read_text(encoding="utf-8")
    for token in ("SENTINEL_SHARE_q4", "SENTINEL_SRC_DIR_q4", "SENTINEL_CAST_DIR_q4", "SENTINEL_TRIP_q4"):
        assert token in nas_tests, token

    checked = 0
    for path in sorted((PACKAGE_ROOT / "tests").rglob("test_*.py")):
        source = path.read_text(encoding="utf-8")
        for value in _NAS_FIXTURE_ASSIGNMENT.findall(source):
            checked += 1
            # The value is never echoed -- the file name is enough to find it.
            assert value == "" or value.startswith("SENTINEL") or value in _SYNTHETIC_NAS_FIXTURES, (
                f"{path.name} sets a NAS fixture that is neither a SENTINEL_* synthetic nor an "
                "enumerated exemption in _SYNTHETIC_NAS_FIXTURES"
            )
        # And the shape a real manifest has: a four-digit-year trip folder.
        assert not re.search(r'"\d{4}_[A-Z][a-z]+/', source), (
            f"{path.name} carries a trip folder shaped like the operator's own"
        )
    assert checked > 0, "no NAS fixture assignment was found at all; the scan is scoped wrong"


def test_the_defaults_reveal_nothing_about_the_operators_setup(monkeypatch):
    """Finding 6: a default IS a committed value. These must all be empty.

    The subject is the **code's** fallback, so the environment is cleared first.
    Reading whatever `os.environ` happens to hold instead would test the machine:
    it passes on CI and fails on the operator's box, where a real `.env` is
    loaded at import, and it also fails whenever an earlier test in the session
    has left a value behind. Neither outcome says anything about `settings.py`.
    """
    from reachy_companion.hanova import settings as hanova_settings

    readers = {
        "HANOVA_NAS_SHARE": hanova_settings.nas_share,
        "HANOVA_NAS_SUBPATH": hanova_settings.nas_subpath,
        "HANOVA_NAS_CAST_SUBPATH": hanova_settings.nas_cast_subpath,
        "HANOVA_HA_SCRIPT_YOUTUBE": hanova_settings.ha_script_youtube,
        "HANOVA_HA_SCRIPT_IMAGE_URL": hanova_settings.ha_script_image_url,
        "HANOVA_HA_SCRIPT_VIDEO_URL": hanova_settings.ha_script_video_url,
    }
    for key in readers:
        monkeypatch.delenv(key, raising=False)
    for key, reader in readers.items():
        assert reader() == "", f"{reader.__name__} must have no default when {key} is unset"


def _new_service_and_tool_sources():
    """List every new module this port adds, service layer included.

    Round 2, finding 6: the round-1 version scanned `tools/*.py` only, so it
    could not see the raw paths and exception strings in `hanova/settings.py`,
    `media_store.py`, `ytdlp.py`, `images.py`, `nas.py` or `home_net.py` -- which
    is where the actual leaks were.
    """
    paths = sorted((SRC_ROOT / "hanova").rglob("*.py"))
    paths += [SRC_ROOT / "tools" / f"{name}.py" for name in sorted(PORTED_TOOLS)]
    paths += [SRC_ROOT / "home_net.py"]
    return [path for path in paths if path.is_file() and "__pycache__" not in str(path)]


def test_no_new_module_logs_or_returns_raw_content():
    """Finding 7 + round 2 finding 6: a grep-able guarantee across the port."""
    for path in _new_service_and_tool_sources():
        source = path.read_text(encoding="utf-8")
        if path.name == "redact.py":
            continue  # the helper itself handles the raw values
        for leaky in ("[:60]", "[:80]", "[:120]", "[:300]", "str(exc)", "%r"):
            assert leaky not in source, f"{path.name} logs or returns raw content ({leaky})"
        # Round 2, finding 6: a traceback quotes local variables and file paths,
        # so `logger.exception` is banned outright in new code.
        assert "logger.exception(" not in source, f"{path.name} logs a traceback"


# Modules that log but legitimately never touch user data, with the reason each
# one is exempt. Anything not on this list that logs must go through `redact`.
_REDACT_EXEMPT = {
    "redact.py": "it is the helper",
    "__init__.py": "package docstring only",
    "confirm.py": "logs tool names, TTLs and lifecycle transitions -- never summary or payload",
    "audio_drain.py": "logs durations and timeouts only",
    "music_hooks.py": "logs turn-phase transitions only",
    "music_player.py": "logs generation numbers, fixed daemon API paths and HTTP statuses only",
    "gauth.py": "one fixed line about forcing a token refresh; every caller-facing error goes through friendly_message",
    "stop_music.py": "logs the fixed string 'Tool call: stop_music' and takes no arguments",
    "nas_video_query.py": "one line, a COUNT of how many filters were supplied -- never a filter's value",
}


def test_every_new_module_that_logs_goes_through_the_redaction_helper():
    """Finding 6: importing `redact` is the minimum bar for a module that logs.

    The exemptions are enumerated with a reason rather than inferred, so adding a
    module that logs a title is a deliberate act someone has to write down.
    """
    for path in _new_service_and_tool_sources():
        source = path.read_text(encoding="utf-8")
        if path.name in _REDACT_EXEMPT:
            continue
        if "logger." not in source:
            continue
        assert "redact" in source, f"{path.name} logs without importing the redaction helper"


def test_the_redaction_exemptions_still_log_nothing_but_metadata():
    """An exemption is a claim, and this is the check that keeps it honest."""
    for name, _reason in _REDACT_EXEMPT.items():
        matches = [path for path in _new_service_and_tool_sources() if path.name == name]
        for path in matches:
            source = path.read_text(encoding="utf-8")
            for line in source.splitlines():
                if "logger." not in line:
                    continue
                # A tool logs "Tool call: <its own name>", and one of those names
                # is `nas_video_query` -- the module's own name is not a leak, so
                # it is removed before the substring scan rather than special-cased.
                scanned = line.replace(path.stem, "")
                for leaky in ("summary", "payload", "query", "title=", "subject", "recipient"):
                    assert leaky not in scanned, f"{name} logs {leaky!r}: {line.strip()!r}"


def test_each_service_seam_has_a_caplog_sentinel_test():
    """Round 2, finding 6: the shape check needs a behavioural counterpart.

    A grep proves the helper is *mentioned*; only a caplog test proves nothing
    private reaches a record. One is required per new service seam.

    Round 3, finding 3 added the ninth: `ha_client` was the one service seam
    without a behavioural sentinel, and it is the seam whose log lines carry the
    operator's `scripts.yaml` entry names and the house's LAN address.
    """
    required = {
        "test_hanova_settings.py": "test_env_path_rejection_never_logs_the_path",
        "test_home_net.py": "test_the_probe_logs_no_address_and_no_url",
        "test_hanova_ha_client.py": "test_the_ha_client_logs_no_script_name_url_or_error_body",
        "test_hanova_media_store.py": "test_the_media_store_logs_no_path",
        "test_hanova_ytdlp.py": "test_the_ytdlp_layer_logs_no_query_url_or_stderr",
        "test_hanova_cast.py": "test_the_images_layer_logs_no_prompt_or_path",
        "test_hanova_nas.py": "test_nas_logs_never_carry_a_clip_path",
        "test_hanova_email.py": "test_email_logs_never_carry_an_address_or_a_subject",
        "test_hanova_confirm.py": "test_gate_logs_no_summary_and_no_payload",
    }
    for filename, test_name in required.items():
        source = (PACKAGE_ROOT / "tests" / filename).read_text(encoding="utf-8")
        assert f"def {test_name}(" in source, f"{filename} is missing {test_name}"


ROUTING_TOKENS = (
    "away_from_home",
    "home_status_unknown",  # round 2, finding 3: renamed, and its own status
    "needs_confirmation",
    "unavailable",
    "retryable",
    "action_in_flight",  # round 2, finding 2
    "body_too_long",  # round 2, finding 4
)

# The routing rules are only worth anything if the file also names the tools
# they route. The two lists used to differ, because the locked profile went
# through the 2026-08-31 tool diet and the operator's `persona.md` had not —
# `persona.md` still taught `play_music`, `nas_skip`, the other sixteen retired
# sub-tools, `party_mode` and the retired `self_destruct`. The pre-merge re-sync
# closed that gap, so the two lists are now deliberately the SAME list: the copy
# that actually runs on the robot teaches the same six families as the profile,
# and this alias is what makes a future drift fail rather than pass quietly.
PROFILE_TOOL_TOKENS = ("music", "tv", "nas", "calendar", "tasks", "drive")
PERSONA_TOOL_TOKENS = PROFILE_TOOL_TOKENS


def test_profile_teaches_the_result_conventions():
    """R4/R10: the persona must know what each result status means."""
    body = PROFILE.read_text(encoding="utf-8")
    for token in ROUTING_TOKENS + PROFILE_TOOL_TOKENS:
        assert token in body, f"profile.md does not mention {token}"


def test_repo_persona_teaches_the_same_conventions():
    """persona.md is the copy that actually runs on the robot after a deploy."""
    body = (PACKAGE_ROOT.parent / "persona.md").read_text(encoding="utf-8")
    for token in ROUTING_TOKENS + PERSONA_TOOL_TOKENS:
        assert token in body, f"persona.md does not mention {token}"


def test_the_persona_records_the_two_approved_non_goals():
    """Finding 17: a dropped capability the character cannot explain is a bug."""
    body = (PACKAGE_ROOT.parent / "persona.md").read_text(encoding="utf-8")
    assert "drive" in body.lower() and "還原" in body, "Drive restore must be explained, not attempted"
    assert "密件" in body or "bcc" in body.lower(), "BCC must be explained, not silently dropped"


def test_deploy_skill_backs_up_the_new_instance_files():
    """R9: a redeploy that eats an OAuth file is silent, expensive data loss."""
    skill = (PACKAGE_ROOT.parent / ".claude" / "skills" / "reachy-deploy" / "SKILL.md").read_text(encoding="utf-8")
    for token in ("google-workspace-mcp", "google-oauth.json", "nas-video-index.json"):
        assert token in skill, f"the deploy skill does not back up {token}"


def test_the_deploy_skill_holds_no_connection_identifiers():
    """Finding 6: the skill file parenthesised the robot's real IP and username.

    They now come from the gitignored repo-root `.env` at run time. This test is
    shape-based for the same reason as the package scan above.

    Round 3, finding 4: it reuses `_PRIVATE_IPV4` rather than carrying its own
    copy of the round-1 regex, which required three octets after `10` but only
    two after `192.168` and therefore could not match the commonest private
    address shape in the world. The deploy skill is one of the explicitly
    scrubbed paths, so the bar here is zero, not "no worse than HEAD".
    """
    skill_path = PACKAGE_ROOT.parent / ".claude" / "skills" / "reachy-deploy" / "SKILL.md"
    skill = skill_path.read_text(encoding="utf-8")
    assert not _PRIVATE_IPV4.search(skill), "the deploy skill still contains a private LAN address"
    assert "REACHY_HOST" in skill and "REACHY_SSH_USER" in skill
    assert "REACHY_HOSTKEY" in skill, "the host-key fingerprint must be an .env key, not a literal"
    fingerprint_like = re.compile(r"SHA256:[A-Za-z0-9+/=]{20,}")
    assert not fingerprint_like.search(skill), "the host-key fingerprint is still committed"


def test_the_env_keys_the_deploy_uses_are_all_gitignored():
    """Finding 20: REACHY_HOSTKEY joins the other secrets in the root .env."""
    gitignore = (PACKAGE_ROOT.parent / ".gitignore").read_text(encoding="utf-8")
    lines = [line.strip() for line in gitignore.splitlines()]
    assert any(line in {".env", "/.env", ".env*"} for line in lines), "the root .env must be ignored"


def test_the_root_env_example_exists_and_is_tracked():
    """Round 2, finding 14: the round-1 check was conditional on a missing file.

    `if example.is_file():` made the whole check vacuous on the exact repository
    it was written for, and the `.gitignore` would have swallowed the template
    anyway. The file is now created in Step 9a, `!/.env.example` is added to
    `.gitignore` so git can actually track it, and its existence is a hard
    requirement here.

    Round 3, finding 6: the negation's **position** is the thing that decides
    whether it works. This repository's `.gitignore` carries `.env` early and
    `.env.*` later, and git honours the last matching pattern -- so a negation
    sitting under `.env` is re-ignored by the rule below it and the template is
    tracked by nothing. The ordering is asserted here, not just the presence.
    """
    gitignore = (PACKAGE_ROOT.parent / ".gitignore").read_text(encoding="utf-8")
    lines = [line.strip() for line in gitignore.splitlines()]
    assert "!/.env.example" in lines, "the template is ignored by the .env* rule without this"

    negation = lines.index("!/.env.example")
    env_rules = [index for index, line in enumerate(lines) if re.fullmatch(r"/?\.env(\*|\..*)?", line)]
    assert env_rules, "no .env ignore rule at all -- this test is scoped wrong"
    assert negation > max(env_rules), (
        "!/.env.example must come after EVERY .env pattern; git honours the last match "
        f"(negation at line {negation + 1}, last .env rule at line {max(env_rules) + 1})"
    )

    example = PACKAGE_ROOT.parent / ".env.example"
    assert example.is_file(), "the repo-root .env.example must exist, not merely be planned"
    body = example.read_text(encoding="utf-8")
    for key in ("REACHY_HOST", "REACHY_SSH_USER", "REACHY_SSH_PASSWORD", "REACHY_HOSTKEY"):
        assert f"{key}=" in body, f"{key} is undocumented in the deployment template"


def test_the_root_env_example_carries_no_real_value():
    """A template that ships a real host is worse than no template."""
    example = (PACKAGE_ROOT.parent / ".env.example").read_text(encoding="utf-8")
    for line in example.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        _key, _, value = stripped.partition("=")
        assert value == "" or value.startswith("<"), f"{stripped!r} looks like a real value"
