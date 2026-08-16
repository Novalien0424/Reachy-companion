"""Pytest configuration for path setup and locked-profile expected failures."""

import os
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).parents[1].resolve()
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))


# Make tests reproducible by ignoring machine-specific profile/tool env config.
# Without this, importing config during test collection can pick up a developer's
# local .env and fail before tests run.
os.environ["REACHY_MINI_SKIP_DOTENV"] = "1"
os.environ.pop("REACHY_MINI_CUSTOM_PROFILE", None)
os.environ.pop("REACHY_MINI_EXTERNAL_PROFILES_DIRECTORY", None)
os.environ.pop("REACHY_MINI_EXTERNAL_TOOLS_DIRECTORY", None)


# --- Locked-profile expected failures -------------------------------------
#
# We ship as a locked single-persona app: config.LOCKED_PROFILE is set to
# "_reachy_companion_locked_profile" (D-001). Upstream's suite was written for
# the unlocked app, where the user can create, edit, delete and switch
# personalities at runtime. With a locked profile those code paths correctly
# short-circuit (e.g. every profile mutation returns reason "profile_locked"),
# so the tests below assert behaviour our app deliberately no longer has.
#
# These are expected failures, not regressions. Verified 2026-08-17: setting
# LOCKED_PROFILE = None and running the full suite gives 307 passed / 0 failed,
# i.e. every id in this list fails *only* because of the locked profile, and
# nothing else in the bundled suite is broken.
#
# This list is exact and exhaustive by design — no globs or module-level
# patterns — so any *new* failure, including one in these same modules, still
# shows up red. To regenerate after an upstream merge: unset LOCKED_PROFILE,
# confirm the suite is green, restore it, and take the `FAILED` ids from
# `python -m pytest -q`.
LOCKED_PROFILE_INCOMPATIBLE_TESTS = frozenset(
    {
        "tests/test_config_name_collisions.py::test_config_raises_on_external_profile_name_collision",
        "tests/test_config_name_collisions.py::test_config_raises_on_external_profile_name_collision_with_builtin_alias",
        "tests/test_console.py::test_personality_ops_delete_builtin_is_not_deletable",
        "tests/test_console.py::test_personality_ops_persist_startup_with_voice_override",
        "tests/test_console.py::test_personality_ops_apply_same_profile_is_noop",
        "tests/test_console.py::test_personality_ops_use_apply_callback",
        "tests/test_console.py::test_local_stream_persist_personality_stores_voice_override",
        "tests/test_console.py::test_local_stream_persist_personality_clears_legacy_startup_env_overrides",
        "tests/test_personality_delete.py::test_ops_refuses_deleting_current_profile",
        "tests/test_personality_delete.py::test_ops_refuses_deleting_startup_profile",
        "tests/test_personality_delete.py::test_ops_deletes_inactive_profile",
        "tests/test_personality_delete.py::test_ops_refuses_non_deletable",
        "tests/test_personality_routes.py::test_new_personality_inherits_packaged_default_tools",
        "tests/test_personality_routes.py::test_personality_creation_does_not_overwrite_existing_profile",
        "tests/test_personality_routes.py::test_personality_save_rejects_blank_instructions",
        "tests/test_personality_routes.py::test_personality_save_rejects_unsafe_name",
        "tests/test_personality_routes.py::test_editing_personality_preserves_tool_defaults_and_override",
        "tests/test_personality_routes.py::test_personality_save_materializes_submitted_tools",
        "tests/test_personality_routes.py::test_personality_save_rolls_back_tool_override_if_profile_write_fails",
        "tests/test_personality_routes.py::test_applying_default_persists_runtime_none",
        "tests/test_personality_routes.py::test_force_reloads_active_personality",
        "tests/test_startup_settings.py::test_load_startup_settings_into_runtime_applies_profile_when_no_env",
        "tests/test_startup_settings.py::test_load_startup_settings_into_runtime_saved_settings_override_instance_env",
        "tests/test_startup_settings.py::test_load_startup_settings_into_runtime_saved_settings_override_inherited_env",
        "tests/test_tool_space_routes.py::test_web_install_adds_global_inventory_without_enabling_a_profile",
        "tests/test_tool_space_routes.py::test_profile_tools_save_and_reset_control_one_profile",
        "tests/test_tool_space_routes.py::test_remove_tool_space_disables_its_tools_in_every_profile",
        "tests/test_tool_space_routes.py::test_add_tool_space_rejects_invalid_slug_without_network_access",
        "tests/test_tool_space_routes.py::test_active_profile_tool_update_restarts_a_running_conversation",
        "tests/test_tool_space_routes.py::test_saved_tool_change_reports_success_when_runtime_reload_fails",
    }
)

SKIP_REASON = "incompatible with locked profile by design (D-001)"


def pytest_collection_modifyitems(items):
    """Skip the upstream tests that only fail because our profile is locked."""
    from reachy_companion.config import LOCKED_PROFILE

    if LOCKED_PROFILE is None:
        # Unlocked build: nothing is expected to fail, so mask nothing.
        return

    skip_marker = pytest.mark.skip(reason=SKIP_REASON)
    for item in items:
        if item.nodeid.replace("\\", "/") in LOCKED_PROFILE_INCOMPATIBLE_TESTS:
            item.add_marker(skip_marker)
