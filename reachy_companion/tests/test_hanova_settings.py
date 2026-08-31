"""Contract tests for the HomeAssistant-Nova config surface (D-018, R5/R9/R11).

Also pins review round 1 findings 6 (no identifier/path/script defaults),
10 (per-tool prerequisites, table-driven over all 22 tools) and 13 (bounded
numeric readers, validated timezone).
"""

import os
import math
import logging
from pathlib import Path

import pytest

from reachy_companion.hanova import settings


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Start every test from zero HomeAssistant-Nova configuration."""
    for name in list(os.environ):
        if name.startswith("HANOVA_") or name in {
            "HA_URL",
            "HA_TOKEN",
            "GOOGLE_CREDS_DIR",
            "HERMES_DRIVE_SECRETS",
            "OPENAI_API_KEY",
        }:
            monkeypatch.delenv(name, raising=False)


def test_defaults_are_generic_never_operator_derived():
    """Only vendor/behaviour defaults survive; nothing traceable to the operator."""
    assert settings.timezone_name() == "Asia/Taipei"
    assert settings.music_keep() == 12
    assert settings.nas_cast_keep() == 8
    assert settings.notion_title_prop() == "Name"  # Notion's own default title property
    assert settings.smtp_host() == "smtp.gmail.com"  # a vendor endpoint, not an identifier
    assert settings.smtp_port() == 465
    assert settings.ytdlp_search_n() == 2  # measured 13.4 s at 5 vs 8.3 s at 2 on the robot
    assert settings.ytdlp_timeout_s() == 20
    assert settings.ytdlp_download_timeout_s() == 120
    assert settings.cal_delete_window_days() == 14
    assert settings.confirm_ttl_s() == 90.0
    assert settings.home_probe_timeout_s() == 1.5
    assert settings.home_cache_ttl_s() == 30.0
    assert settings.image_model() == "gpt-image-1"


def test_identifier_path_and_script_keys_all_default_to_empty():
    """Finding 6: a value derived from the operator's setup is never a default."""
    for reader in (
        settings.gcal_calendar_id,
        settings.gtasks_list_id,
        settings.drive_parent_id,
        settings.google_account,
        settings.cast_entity,
        settings.nas_host,
        settings.notion_data_source_id,
        settings.smtp_user,
        # finding 6: all three used to carry the operator's own share name and
        # folder layout as defaults. Round 2, finding 5: this comment does not
        # name them either -- a comment is committed text like any other.
        settings.nas_share,
        settings.nas_subpath,
        settings.nas_cast_subpath,
        # finding 6: these three were the operator's own scripts.yaml entry names.
        settings.ha_script_youtube,
        settings.ha_script_image_url,
        settings.ha_script_video_url,
    ):
        assert reader() == "", f"{reader.__name__} must default to empty"


def test_env_values_win_and_are_stripped(monkeypatch):
    """Whitespace around a pasted value must not corrupt it."""
    monkeypatch.setenv("HANOVA_TZ", "  Europe/Paris  ")
    monkeypatch.setenv("HANOVA_MUSIC_KEEP", " 3 ")
    monkeypatch.setenv("HANOVA_CONFIRM_TTL_S", "45.5")
    monkeypatch.setenv("HA_URL", "http://ha.example.invalid:8123/")
    assert settings.timezone_name() == "Europe/Paris"
    assert settings.music_keep() == 3
    assert settings.confirm_ttl_s() == 45.5
    assert settings.ha_url() == "http://ha.example.invalid:8123"


def test_malformed_numbers_fall_back_to_defaults(monkeypatch):
    """A typo in a numeric env var must not raise at tool construction time."""
    monkeypatch.setenv("HANOVA_MUSIC_KEEP", "twelve")
    monkeypatch.setenv("HANOVA_CONFIRM_TTL_S", "soon")
    assert settings.music_keep() == 12
    assert settings.confirm_ttl_s() == 90.0


@pytest.mark.parametrize("raw", ["0", "-1", "-0.5", "nan", "inf", "-inf", "1e400", "999999999"])
def test_out_of_range_numbers_fall_back_to_defaults(monkeypatch, raw):
    """Finding 13: zero, negative, non-finite and absurd values are all rejected."""
    monkeypatch.setenv("HANOVA_YTDLP_TIMEOUT_S", raw)
    monkeypatch.setenv("HANOVA_CONFIRM_TTL_S", raw)
    monkeypatch.setenv("HANOVA_HOME_PROBE_TIMEOUT_S", raw)
    assert settings.ytdlp_timeout_s() == 20
    assert settings.confirm_ttl_s() == 90.0
    assert settings.home_probe_timeout_s() == 1.5


def test_every_numeric_accessor_stays_inside_finite_bounds(monkeypatch):
    """Finding 13: no accessor may return 0, a negative, or a non-finite number."""
    for name in (
        "HANOVA_MUSIC_KEEP",
        "HANOVA_NAS_CAST_KEEP",
        "HANOVA_IMAGE_KEEP",
        "HANOVA_CAL_DELETE_WINDOW_DAYS",
        "HANOVA_SMTP_PORT",
        "HANOVA_YTDLP_SEARCH_N",
        "HANOVA_YTDLP_TIMEOUT_S",
        "HANOVA_YTDLP_DOWNLOAD_TIMEOUT_S",
        "HANOVA_CONFIRM_TTL_S",
        "HANOVA_HOME_PROBE_TIMEOUT_S",
        "HANOVA_HOME_CACHE_TTL_S",
    ):
        monkeypatch.setenv(name, "-99999999")
    numbers = [
        settings.music_keep(),
        settings.nas_cast_keep(),
        settings.image_keep(),
        settings.cal_delete_window_days(),
        settings.smtp_port(),
        settings.ytdlp_search_n(),
        settings.ytdlp_timeout_s(),
        settings.ytdlp_download_timeout_s(),
        settings.confirm_ttl_s(),
        settings.home_probe_timeout_s(),
        settings.home_cache_ttl_s(),
    ]
    for value in numbers:
        assert math.isfinite(value) and value > 0


def test_home_cache_ttl_may_be_zero_only_because_tests_need_it(monkeypatch):
    """A zero cache TTL is the one legal zero: it means "always re-probe"."""
    monkeypatch.setenv("HANOVA_HOME_CACHE_TTL_S", "0")
    assert settings.home_cache_ttl_s() == 0.0


def test_timezone_is_validated_against_the_tz_database(monkeypatch):
    """Finding 13: an unknown zone must degrade, not blow up inside a tool."""
    monkeypatch.setenv("HANOVA_TZ", "Mars/Olympus_Mons")
    assert settings.timezone_name() == "Asia/Taipei"
    monkeypatch.setenv("HANOVA_TZ", "UTC")
    assert settings.timezone_name() == "UTC"


# --- per-tool prerequisites (finding 10) ----------------------------------
def test_every_ported_tool_declares_prerequisites():
    """All 20 names are covered; a new tool cannot be silently ungated.

    Twenty since 2026-08-31: `self_destruct` and `mad_laugh` were retired, and
    their two clip-id prerequisites left `_PREREQS` with them.
    """
    assert len(settings.TOOL_PREREQS) == 20
    for family, names in settings.FAMILY_TOOLS.items():
        assert family in settings.FAMILIES
        for name in names:
            assert name in settings.TOOL_PREREQS, name
    covered = {name for names in settings.FAMILY_TOOLS.values() for name in names}
    assert covered == set(settings.TOOL_PREREQS)


def test_with_zero_config_only_the_always_on_tools_are_available():
    """stop_music is the safety lane and must answer even with nothing set up."""
    available = {name for name in settings.TOOL_PREREQS if settings.tool_available(name)}
    assert "stop_music" in available
    assert "calendar_add" not in available
    assert "email_send" not in available


def test_stop_music_has_no_prerequisites_by_design():
    """Finding 10: this is a deliberate exemption, recorded as an empty tuple."""
    assert settings.TOOL_PREREQS["stop_music"] == ()


def test_every_disabled_tool_names_the_key_not_a_value():
    """The reason string is a config key name; it must never leak a value."""
    for name in settings.TOOL_PREREQS:
        available, reason = settings.tool_status(name)
        if not available:
            assert reason
            assert reason.isupper() or reason.replace("_", "").isalnum() or " " in reason
            assert "@" not in reason and "/" not in reason


def test_google_calendar_tools_need_the_creds_file_and_a_calendar_id(monkeypatch, tmp_path):
    """Finding 10: upstream's family gate ignored the calendar and list ids."""
    creds_dir = tmp_path / "google-workspace-mcp"
    creds_dir.mkdir()
    monkeypatch.setenv("GOOGLE_CREDS_DIR", str(creds_dir))
    monkeypatch.setenv("HANOVA_GOOGLE_ACCOUNT", "someone@example.com")
    assert settings.tool_status("calendar_list") == (False, "GOOGLE_CREDS_FILE")
    (creds_dir / "someone@example.com.json").write_text("{}", encoding="utf-8")
    assert settings.tool_status("calendar_list") == (False, "HANOVA_GCAL_CALENDAR_ID")
    monkeypatch.setenv("HANOVA_GCAL_CALENDAR_ID", "cal-under-test")
    assert settings.tool_available("calendar_list") is True
    # task_add needs its own list id; task_list does not.
    assert settings.tool_status("task_add") == (False, "HANOVA_GTASKS_LIST_ID")
    assert settings.tool_available("task_list") is True


def test_google_creds_dir_must_be_writable(monkeypatch, tmp_path):
    """Finding 10: gauth rewrites the file on refresh; a read-only dir is broken."""
    creds_dir = tmp_path / "google-workspace-mcp"
    creds_dir.mkdir()
    (creds_dir / "someone@example.com.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("GOOGLE_CREDS_DIR", str(creds_dir))
    monkeypatch.setenv("HANOVA_GOOGLE_ACCOUNT", "someone@example.com")
    monkeypatch.setenv("HANOVA_GCAL_CALENDAR_ID", "cal-under-test")
    monkeypatch.setattr(settings.os, "access", lambda path, mode: False)
    assert settings.tool_status("calendar_list") == (False, "GOOGLE_CREDS_DIR not writable")


def test_play_video_needs_no_lan_base_and_no_cast_entity(monkeypatch):
    """Finding 10: play_video hands HA an id; it serves nothing and casts no URL."""
    monkeypatch.setattr(settings, "_music_wheels_ready", lambda: (True, ""))
    monkeypatch.setenv("HA_URL", "http://ha.example.invalid:8123")
    monkeypatch.setenv("HA_TOKEN", "tok")
    monkeypatch.setenv("HANOVA_HA_SCRIPT_YOUTUBE", "tv_show_youtube")
    assert settings.tool_available("play_video") is True
    assert settings.tool_status("show_on_tv")[0] is False  # needs base + key + mount


def test_url_casting_tools_need_a_live_media_mount(monkeypatch):
    """Finding 11: a failed mount means casting a URL nothing will serve."""
    monkeypatch.setenv("HA_URL", "http://ha.example.invalid:8123")
    monkeypatch.setenv("HA_TOKEN", "tok")
    monkeypatch.setenv("HANOVA_HA_SCRIPT_IMAGE_URL", "tv_show_image_url")
    monkeypatch.setenv("HANOVA_MEDIA_HTTP_BASE", "http://robot.example.invalid:7860")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    settings.set_media_mount_ready(False)
    try:
        assert settings.tool_status("show_on_tv") == (False, "HANOVA_MEDIA_MOUNT")
        settings.set_media_mount_ready(True)
        assert settings.tool_available("show_on_tv") is True
    finally:
        settings.set_media_mount_ready(False)


def test_index_only_nas_query_does_not_need_smb_credentials(monkeypatch, tmp_path):
    """Finding 10: nas_video_query reads a local JSON file and nothing else."""
    index = tmp_path / "nas-video-index.json"
    index.write_text('{"videos": []}', encoding="utf-8")
    monkeypatch.setenv("HANOVA_NAS_INDEX_PATH", str(index))
    assert settings.tool_available("nas_video_query") is True
    assert settings.tool_status("play_nas_video") == (False, "HANOVA_NAS_HOST")


def test_unknown_tool_is_reported_not_raised():
    """A typo in a caller must not crash a tool call."""
    assert settings.tool_status("nope") == (False, "unknown tool")


# --- family aggregation ----------------------------------------------------
def test_all_config_families_disabled_with_zero_config():
    """R5: the app must boot with none of the new config present."""
    for family in settings.FAMILIES:
        if family == "music":
            continue  # depends on installed wheels and clip ids, not on a service
        verdict, reason = settings.family_status(family)
        assert verdict == "disabled"
        assert reason, f"{family} must explain why it is disabled"


def test_family_verdict_is_partial_when_only_some_tools_qualify(monkeypatch, tmp_path):
    """Finding 10: one family can be half-configured, and must say so."""
    index = tmp_path / "nas-video-index.json"
    index.write_text('{"videos": []}', encoding="utf-8")
    monkeypatch.setenv("HANOVA_NAS_INDEX_PATH", str(index))
    verdict, reason = settings.family_status("nas")
    assert verdict == "partial"
    assert "1/4" in reason


def test_family_status_never_raises_for_any_family():
    """Called from startup: a bad value must degrade, never abort the process."""
    for family in settings.FAMILIES:
        verdict, reason = settings.family_status(family)
        assert verdict in {"enabled", "partial", "disabled"}
        assert isinstance(reason, str)


def test_unknown_family_is_reported_not_raised():
    """A typo in a caller must not crash startup."""
    assert settings.family_status("nope") == ("disabled", "unknown family")


def test_unavailable_payload_is_exactly_the_contract():
    """R5 fixes this shape; tools return it verbatim."""
    assert settings.unavailable() == {"status": "unavailable", "reason": "not_configured"}
    assert settings.unavailable("HANOVA_NAS_HOST") == {
        "status": "unavailable",
        "reason": "HANOVA_NAS_HOST",
    }


def test_log_family_status_emits_one_line_per_family(caplog):
    """R5: one INFO line per family, with its tri-state verdict, every startup."""
    caplog.set_level(logging.INFO, logger="reachy_companion.hanova.settings")
    settings.log_family_status()
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("hanova family ")]
    assert len(lines) == len(settings.FAMILIES)
    for family in settings.FAMILIES:
        assert any(line.startswith(f"hanova family {family}: ") for line in lines)


def test_startup_log_never_prints_a_configured_value(monkeypatch, caplog, tmp_path):
    """Finding 7: the verdict names keys, never the operator's values."""
    secret = "SENTINEL_PRIVATE_x7"
    monkeypatch.setenv("HANOVA_GCAL_CALENDAR_ID", secret)
    monkeypatch.setenv("HANOVA_NOTION_TOKEN", secret)
    monkeypatch.setenv("HANOVA_NAS_HOST", secret)
    monkeypatch.setenv("HANOVA_SMTP_USER", secret)
    caplog.set_level(logging.DEBUG)
    settings.log_family_status()
    assert secret not in caplog.text


def test_env_path_expands_user(monkeypatch):
    """Paths written with ~ in .env must resolve."""
    monkeypatch.setenv("HANOVA_NAS_INDEX_PATH", "~/nas-video-index.json")
    resolved = settings.nas_index_path()
    assert resolved is not None
    assert str(resolved) == str(Path.home() / "nas-video-index.json")


def test_env_path_rejection_never_logs_the_path(monkeypatch, caplog):
    """Round 2, finding 6: settings.py is a service seam and logs like one.

    The previous version logged the rejected value with `%r`, which puts the
    operator's own directory layout into the log for a mere typo.
    """
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("HANOVA_NAS_INDEX_PATH", "~SENTINEL_PRIVATE_x7/index.json")
    settings.nas_index_path()
    assert "SENTINEL_PRIVATE_x7" not in caplog.text


# --- the declared home network (round 2, finding 3) ------------------------
def test_home_networks_is_empty_by_default(monkeypatch):
    """With nothing declared there is no evidence that could justify AWAY."""
    monkeypatch.delenv("HANOVA_HOME_NETWORKS", raising=False)
    assert settings.home_networks() == []


def test_home_networks_parses_a_comma_separated_cidr_list(monkeypatch):
    """One robot may legitimately live on a wired and a wireless subnet."""
    monkeypatch.setenv("HANOVA_HOME_NETWORKS", "203.0.113.0/24, 198.51.100.7 ")
    networks = settings.home_networks()
    assert len(networks) == 2
    assert str(networks[0]) == "203.0.113.0/24"
    assert str(networks[1]) == "198.51.100.7/32"


def test_a_malformed_home_network_is_dropped_and_never_logged(monkeypatch, caplog):
    """A typo must narrow "home", never widen it, and never echo the value."""
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("HANOVA_HOME_NETWORKS", "203.0.113.0/24,SENTINEL_PRIVATE_x7")
    networks = settings.home_networks()
    assert len(networks) == 1
    assert "SENTINEL_PRIVATE_x7" not in caplog.text
