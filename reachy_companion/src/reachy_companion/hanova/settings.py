"""Single config surface for the ported HomeAssistant-Nova capabilities (D-018).

Nothing here may raise. Tools are constructed inside `_build_tool_registry()`
(`tools/core_tools.py:291`), which is *not* exception-guarded, so a malformed
value must degrade to its documented default rather than abort startup.

Only **generic** defaults live here (a vendor endpoint, a behaviour constant, a
timezone). Every value that upstream hardcoded from the operator's own setup --
an identifier, a share or folder name, an HA script name -- defaults to the
empty string, which disables the tools that need it (R5, review finding 6).

Numeric readers take **mandatory finite bounds** (review finding 13): a zero HTTP
timeout, a negative keep count or an infinite confirmation TTL are all reachable
from a typo in `.env`, and each one is a real failure mode -- an immediate
timeout, a destructive prune, a gate that never expires.

Nothing here logs a configured **value**; a disabled verdict names the missing
**key** (review finding 7).
"""

from __future__ import annotations
import os
import math
import logging
import ipaddress
from typing import Any, Dict, List, Tuple
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Round 2, finding 6: this module logs too, so it goes through the same helper
# every tool does. `redact` imports nothing from `hanova`, so there is no cycle.
from reachy_companion.hanova import redact


logger = logging.getLogger(__name__)


# --- primitive readers -----------------------------------------------------
def env_str(name: str, default: str = "") -> str:
    """Read a stripped string env var, falling back to *default* when blank."""
    return (os.getenv(name) or "").strip() or default


def env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    """Read a bounded int env var; anything outside the range yields *default*.

    Bounds are keyword-only and mandatory so a new accessor cannot be added
    without deciding what its legal range actually is (review finding 13).
    """
    raw = env_str(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("%s is not an integer; using the default.", name)
        return default
    if not (minimum <= value <= maximum):
        logger.warning("%s is outside [%d, %d]; using the default.", name, minimum, maximum)
        return default
    return value


def env_float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    """Read a bounded, finite float env var; anything else yields *default*."""
    raw = env_str(name)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("%s is not a number; using the default.", name)
        return default
    if not math.isfinite(value):
        logger.warning("%s is not finite; using the default.", name)
        return default
    if not (minimum <= value <= maximum):
        logger.warning("%s is outside [%s, %s]; using the default.", name, minimum, maximum)
        return default
    return value


def env_path(name: str, default: Path | None = None) -> Path | None:
    """Read a filesystem path env var with `~` expansion, or *default* when unset.

    Round 2, finding 6: the rejected value used to be interpolated into the
    warning with a raw repr. A path is exactly the kind of identifier `redact`
    exists for -- it names the operator's home directory, their NAS layout, or
    their account -- so the log line carries the **key name and a length**,
    never the value.
    """
    raw = env_str(name)
    if not raw:
        return default
    try:
        return Path(raw).expanduser()
    except (OSError, RuntimeError) as exc:
        logger.warning("%s is not a usable path (%s): %s", name, redact.text(raw), redact.error(exc))
        return default


# --- Home Assistant (existing keys, reused per manifest section D) ---------
def ha_url() -> str:
    """Return the base LAN URL of Home Assistant, without a trailing slash."""
    return env_str("HA_URL").rstrip("/")


def ha_token() -> str:
    """Home Assistant long-lived token (a JWT that must carry an `exp` claim)."""
    return env_str("HA_TOKEN")


# --- general ---------------------------------------------------------------
_DEFAULT_TZ = "Asia/Taipei"


def timezone_name() -> str:
    """IANA timezone used for calendar/task times (R11), validated at read time.

    An unknown zone here would surface much later as a `ZoneInfoNotFoundError`
    deep inside a calendar tool, so it is checked against the tz database now
    and degraded to the default (review finding 13).
    """
    raw = env_str("HANOVA_TZ", _DEFAULT_TZ)
    try:
        ZoneInfo(raw)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        logger.warning("HANOVA_TZ is not a known IANA timezone; using %s.", _DEFAULT_TZ)
        return _DEFAULT_TZ
    return raw


def cast_entity() -> str:
    """Return the optional media_player entity id forwarded to the HA cast scripts.

    Not a prerequisite for anything: the cast scripts have their own target. When
    it is set it is passed through as `entity_id` so one robot can drive a
    specific TV in a house with several (review finding 10).
    """
    return env_str("HANOVA_CAST_ENTITY")


def cast_confirm_timeout_s() -> float:
    """How long a session-starting cast may wait for the TV to show playback.

    2026-08-24: confirmation needs `HANOVA_CAST_ENTITY` to be set; 0 disables
    it, restoring dispatch-equals-success. The default covers a YouTube-app
    cold launch on the TV (~5-10 s observed).
    """
    return env_float("HANOVA_CAST_CONFIRM_S", 12.0, minimum=0.0, maximum=60.0)


# --- Home Assistant script names (no defaults -- review finding 6) ---------
def ha_script_youtube() -> str:
    """Name of the HA script that launches a YouTube id on the TV."""
    return env_str("HANOVA_HA_SCRIPT_YOUTUBE")


def ha_script_image_url() -> str:
    """Name of the HA script that casts an image URL to the TV."""
    return env_str("HANOVA_HA_SCRIPT_IMAGE_URL")


def ha_script_video_url() -> str:
    """Name of the HA script that casts a video URL to the TV."""
    return env_str("HANOVA_HA_SCRIPT_VIDEO_URL")


# --- media cache and serving (R6) ------------------------------------------
def media_http_base() -> str:
    """LAN base URL the TV dereferences, without a trailing slash."""
    return env_str("HANOVA_MEDIA_HTTP_BASE").rstrip("/")


def media_dir_override() -> Path | None:
    """Return the optional media cache root override; the default is the instance dir."""
    return env_path("HANOVA_MEDIA_DIR")


def music_keep() -> int:
    """How many cached music files to retain (upstream default 12)."""
    return env_int("HANOVA_MUSIC_KEEP", 12, minimum=1, maximum=1000)


def nas_cast_keep() -> int:
    """How many staged NAS MP4s to retain (upstream default 8)."""
    return env_int("HANOVA_NAS_CAST_KEEP", 8, minimum=1, maximum=1000)


def image_keep() -> int:
    """How many generated TV images to retain."""
    return env_int("HANOVA_IMAGE_KEEP", 12, minimum=1, maximum=1000)


# --- Google Calendar / Tasks -----------------------------------------------
def google_account() -> str:
    """Google account whose OAuth JSON is used for Calendar and Tasks."""
    return env_str("HANOVA_GOOGLE_ACCOUNT")


def google_creds_dir() -> Path | None:
    """Directory holding `<account>.json`; must be writable (gauth rewrites it)."""
    return env_path("GOOGLE_CREDS_DIR")


def gcal_calendar_id() -> str:
    """Default calendar id for add/list/delete."""
    return env_str("HANOVA_GCAL_CALENDAR_ID")


def gtasks_list_id() -> str:
    """Default Google Tasks list id for task_add."""
    return env_str("HANOVA_GTASKS_LIST_ID")


def cal_delete_window_days() -> int:
    """Forward search window, in days, for resolving a calendar_delete match."""
    return env_int("HANOVA_CAL_DELETE_WINDOW_DAYS", 14, minimum=1, maximum=365)


# --- Google Drive (separate OAuth grant) -----------------------------------
def drive_secrets_path() -> Path | None:
    """Path to the Drive OAuth secret JSON (flat or nested shape)."""
    return env_path("HERMES_DRIVE_SECRETS")


def drive_parent_id() -> str:
    """Drive folder id that drive_list reads and drive_upload writes into."""
    return env_str("HANOVA_DRIVE_PARENT_ID")


# --- Notion (distinct from the NOTION_MCP_* remote lane) -------------------
def notion_token() -> str:
    """Notion internal-integration bearer token."""
    return env_str("HANOVA_NOTION_TOKEN")


def notion_data_source_id() -> str:
    """Notion data-source id the notes database writes into."""
    return env_str("HANOVA_NOTION_DATA_SOURCE_ID")


def notion_title_prop() -> str:
    """Name of the title property on that data source."""
    return env_str("HANOVA_NOTION_TITLE_PROP", "Name")


# --- Email -----------------------------------------------------------------
def smtp_host() -> str:
    """SMTP host for email_send."""
    return env_str("HANOVA_SMTP_HOST", "smtp.gmail.com")


def smtp_port() -> int:
    """SMTP SSL port for email_send."""
    return env_int("HANOVA_SMTP_PORT", 465, minimum=1, maximum=65535)


def smtp_user() -> str:
    """SMTP username / sender address."""
    return env_str("HANOVA_SMTP_USER")


def smtp_app_password() -> str:
    """SMTP app password. Never logged."""
    return env_str("HANOVA_SMTP_APP_PASSWORD")


def smtp_from_name() -> str:
    """Display name on outgoing mail."""
    return env_str("HANOVA_SMTP_FROM_NAME", "Reachy")


# --- NAS -------------------------------------------------------------------
def nas_host() -> str:
    """NAS host or IP for SMB access."""
    return env_str("HANOVA_NAS_HOST")


def nas_user() -> str:
    """SMB username."""
    return env_str("HANOVA_NAS_USER")


def nas_password() -> str:
    """SMB password. Never logged."""
    return env_str("HANOVA_NAS_PASSWORD")


def nas_share() -> str:
    """SMB share name. No default: this is the operator's own share (finding 6)."""
    return env_str("HANOVA_NAS_SHARE")


def nas_subpath() -> str:
    """Return the only folder inside the share we ever read. No default (finding 6).

    Every relative path taken from the index is validated to normalise **inside**
    this subtree before it reaches an SMB path (see `nas.py`, review finding 15).
    """
    return env_str("HANOVA_NAS_SUBPATH")


def nas_cast_subpath() -> str:
    """Folder holding the pre-transcoded, cast-ready MP4s. No default (finding 6)."""
    return env_str("HANOVA_NAS_CAST_SUBPATH")


def nas_index_path() -> Path | None:
    """Path to the operator-supplied `nas-video-index.json`."""
    return env_path("HANOVA_NAS_INDEX_PATH")


def nas_stream_enabled() -> bool:
    """Return whether a NAS clip is streamed to the TV instead of staged first.

    On by default (latency work, 2026-08-22): the share measured ~7 MB/s over
    the robot's Wi-Fi, so copying a 300 MB clip cost ~44 s of silence before the
    TV saw its first byte. Streaming hands the TV a Range-capable URL that
    proxies the share per request, so playback starts in about a second.

    The kill switch is `HANOVA_NAS_STREAM=0` (`false`/`no` also count), which
    falls back to the stage-then-serve path: the whole clip is copied into the
    LAN media cache and the TV fetches a static file. That path survives a NAS
    that drops connections mid-playback, which streaming does not.
    """
    return env_str("HANOVA_NAS_STREAM", "1").lower() not in {"0", "false", "no"}


# --- yt-dlp ----------------------------------------------------------------
def ytdlp_search_n() -> int:
    """How many YouTube search results yt-dlp considers.

    Each candidate costs its own metadata fetch: on the robot a five-candidate
    search measured 13.4 s against 8.3 s for two (2026-08-22), so the default
    buys one fallback candidate rather than four.
    """
    return env_int("HANOVA_YTDLP_SEARCH_N", 2, minimum=1, maximum=25)


def ytdlp_extractor_args() -> str:
    """Read the optional ``--extractor-args`` value for every yt-dlp call.

    YouTube intermittently refuses extraction without a JavaScript runtime the
    robot does not carry (every candidate then errors "not available"), and
    ``youtube:player_client=android`` sidesteps the challenge. Empty -- the
    default -- adds nothing; the knob lives in configuration because the
    working value tracks YouTube's behaviour, not this codebase.
    """
    return env_str("HANOVA_YTDLP_EXTRACTOR_ARGS")


def ytdlp_timeout_s() -> int:
    """Per-attempt timeout for a yt-dlp search. Zero would abort instantly."""
    return env_int("HANOVA_YTDLP_TIMEOUT_S", 20, minimum=1, maximum=300)


def ytdlp_download_timeout_s() -> int:
    """Timeout for a yt-dlp audio download plus transcode."""
    return env_int("HANOVA_YTDLP_DOWNLOAD_TIMEOUT_S", 120, minimum=5, maximum=1800)


# --- behaviour knobs -------------------------------------------------------
def confirm_ttl_s() -> float:
    """Seconds a pending confirmation stays valid (R3, upstream default 90).

    Bounded below by 1 s (a zero TTL would expire every confirmation before the
    user could speak) and above by 600 s (an authorisation that outlives the
    conversation is exactly what review finding 3 is about).
    """
    return env_float("HANOVA_CONFIRM_TTL_S", 90.0, minimum=1.0, maximum=600.0)


def home_probe_timeout_s() -> float:
    """Timeout of the home-network probe (R4). Zero would mean "always away"."""
    return env_float("HANOVA_HOME_PROBE_TIMEOUT_S", 1.5, minimum=0.1, maximum=30.0)


def home_cache_ttl_s() -> float:
    """How long a home verdict is cached (R4). Zero is legal: always re-probe."""
    return env_float("HANOVA_HOME_CACHE_TTL_S", 30.0, minimum=0.0, maximum=3600.0)


def home_networks() -> List[Any]:
    """Return the operator's declared home network(s) as CIDRs (round 2, finding 3).

    This is the **only** source of positive off-home evidence. `home_net.py` may
    answer `AWAY` when the robot's own address is outside every network listed
    here -- that is a fact about routing, not a guess. With this unset there is
    no evidence that could justify `AWAY`, and `home_net` degrades to
    `HOME`/`UNKNOWN` only: it will never tell the user they are out of the house
    on the strength of a failed connection.

    Comma-separated. Each entry is an IPv4 or IPv6 network in CIDR form; a bare
    address is accepted and treated as a /32 or /128. Anything unparseable is
    dropped with a key-only warning, because a malformed entry must not silently
    widen the definition of "home".
    """
    raw = env_str("HANOVA_HOME_NETWORKS")
    if not raw:
        return []
    networks: List[Any] = []
    for item in raw.split(","):
        candidate = item.strip()
        if not candidate:
            continue
        try:
            networks.append(ipaddress.ip_network(candidate, strict=False))
        except ValueError:
            # Finding 7: the value is the operator's LAN. Length only.
            logger.warning("HANOVA_HOME_NETWORKS has an unparseable entry (%s); ignoring it.", redact.text(candidate))
    return networks


def image_model() -> str:
    """OpenAI Images model used by show_on_tv."""
    return env_str("HANOVA_IMAGE_MODEL", "gpt-image-1")


# --- media mount readiness (R6, review finding 11) -------------------------
# `media_store.mount_media_routes()` writes this at startup. It lives here, not
# in media_store, so the prerequisite table has no import cycle and no lazy
# import: casting a URL that nothing will serve is a configuration failure, and
# it belongs in the same place as every other configuration failure.
_MEDIA_MOUNT_READY = False


def set_media_mount_ready(ready: bool) -> None:
    """Record whether the `/hanova-media` static route actually came up."""
    global _MEDIA_MOUNT_READY
    _MEDIA_MOUNT_READY = bool(ready)


def media_mount_ready() -> bool:
    """Return whether the LAN media route is live in this process."""
    return _MEDIA_MOUNT_READY


# --- per-tool prerequisites (R5, review finding 10) ------------------------
FAMILIES: tuple[str, ...] = (
    "google-workspace",
    "drive",
    "notion",
    "email",
    "nas",
    "media-cast",
    "music",
)

FAMILY_TOOLS: Dict[str, Tuple[str, ...]] = {
    "google-workspace": (
        "calendar_add",
        "calendar_list",
        "calendar_delete",
        "task_add",
        "task_list",
        "task_complete",
        "task_delete",
    ),
    "drive": ("drive_list", "drive_trash", "drive_upload"),
    "notion": ("notion_add",),
    "email": ("email_send",),
    "nas": ("nas_video_query", "play_nas_video", "nas_play_folder", "nas_skip"),
    "media-cast": ("play_video", "show_on_tv"),
    "music": ("play_music", "stop_music"),
}


def _music_wheels_ready() -> tuple[bool, str]:
    """Return whether yt-dlp and a usable ffmpeg binary are both importable."""
    try:
        import yt_dlp  # noqa: F401
    except Exception:
        return False, "yt-dlp not installed"
    try:
        import imageio_ffmpeg

        if not imageio_ffmpeg.get_ffmpeg_exe():
            return False, "imageio-ffmpeg binary unavailable"
    except Exception:
        return False, "imageio-ffmpeg binary unavailable"
    return True, ""


def _google_creds_file_ready() -> bool:
    creds_dir = google_creds_dir()
    account = google_account()
    if creds_dir is None or not account:
        return False
    return (creds_dir / f"{account}.json").is_file()


def _google_creds_dir_writable() -> bool:
    # gauth rewrites `<account>.json` on every token refresh, so a read-only
    # directory is a working credential that will stop working within the hour.
    creds_dir = google_creds_dir()
    return creds_dir is not None and os.access(creds_dir, os.W_OK)


def _nas_index_ready() -> bool:
    index = nas_index_path()
    return index is not None and index.is_file()


# Each prerequisite is a key name plus a predicate. The key name is what a
# disabled tool reports, so it is always a *config key*, never a config value.
_PREREQS: Dict[str, Any] = {
    "HA_URL": lambda: bool(ha_url()),
    "HA_TOKEN": lambda: bool(ha_token()),
    "HANOVA_HA_SCRIPT_YOUTUBE": lambda: bool(ha_script_youtube()),
    "HANOVA_HA_SCRIPT_IMAGE_URL": lambda: bool(ha_script_image_url()),
    "HANOVA_HA_SCRIPT_VIDEO_URL": lambda: bool(ha_script_video_url()),
    "HANOVA_MEDIA_HTTP_BASE": lambda: bool(media_http_base()),
    "HANOVA_MEDIA_MOUNT": media_mount_ready,
    "OPENAI_API_KEY": lambda: bool((os.getenv("OPENAI_API_KEY") or "").strip()),
    "GOOGLE_CREDS_DIR": lambda: google_creds_dir() is not None,
    "HANOVA_GOOGLE_ACCOUNT": lambda: bool(google_account()),
    "GOOGLE_CREDS_FILE": _google_creds_file_ready,
    "GOOGLE_CREDS_DIR not writable": _google_creds_dir_writable,
    "HANOVA_GCAL_CALENDAR_ID": lambda: bool(gcal_calendar_id()),
    "HANOVA_GTASKS_LIST_ID": lambda: bool(gtasks_list_id()),
    "HERMES_DRIVE_SECRETS": lambda: drive_secrets_path() is not None,
    "DRIVE_SECRET_FILE": lambda: (drive_secrets_path() or Path("/nonexistent")).is_file(),
    "HANOVA_DRIVE_PARENT_ID": lambda: bool(drive_parent_id()),
    "HANOVA_NOTION_TOKEN": lambda: bool(notion_token()),
    "HANOVA_NOTION_DATA_SOURCE_ID": lambda: bool(notion_data_source_id()),
    "HANOVA_SMTP_USER": lambda: bool(smtp_user()),
    "HANOVA_SMTP_APP_PASSWORD": lambda: bool(smtp_app_password()),
    "HANOVA_NAS_INDEX_PATH": lambda: nas_index_path() is not None,
    "NAS_INDEX_FILE": _nas_index_ready,
    "HANOVA_NAS_HOST": lambda: bool(nas_host()),
    "HANOVA_NAS_USER": lambda: bool(nas_user()),
    "HANOVA_NAS_PASSWORD": lambda: bool(nas_password()),
    "HANOVA_NAS_SHARE": lambda: bool(nas_share()),
    "HANOVA_NAS_SUBPATH": lambda: bool(nas_subpath()),
    "HANOVA_NAS_CAST_SUBPATH": lambda: bool(nas_cast_subpath()),
    "MUSIC_WHEELS": lambda: _music_wheels_ready()[0],
}

_GOOGLE_BASE: Tuple[str, ...] = (
    "GOOGLE_CREDS_DIR",
    "HANOVA_GOOGLE_ACCOUNT",
    "GOOGLE_CREDS_FILE",
    "GOOGLE_CREDS_DIR not writable",
)
_DRIVE_BASE: Tuple[str, ...] = ("HERMES_DRIVE_SECRETS", "DRIVE_SECRET_FILE", "HANOVA_DRIVE_PARENT_ID")
_HA_BASE: Tuple[str, ...] = ("HA_URL", "HA_TOKEN")
_URL_CAST_BASE: Tuple[str, ...] = _HA_BASE + ("HANOVA_MEDIA_HTTP_BASE", "HANOVA_MEDIA_MOUNT")
_NAS_FETCH: Tuple[str, ...] = (
    "HANOVA_NAS_INDEX_PATH",
    "NAS_INDEX_FILE",
    "HANOVA_NAS_HOST",
    "HANOVA_NAS_USER",
    "HANOVA_NAS_PASSWORD",
    "HANOVA_NAS_SHARE",
    "HANOVA_NAS_SUBPATH",
    "HANOVA_NAS_CAST_SUBPATH",
)

TOOL_PREREQS: Dict[str, Tuple[str, ...]] = {
    # music -- `stop_music` is deliberately unconditional: it is the safety lane
    # that must answer even when nothing else can (review finding 10).
    "play_music": ("MUSIC_WHEELS",),
    "stop_music": (),
    # media-cast -- play_video hands HA an id and serves nothing, so it needs
    # neither the LAN base nor a live media mount.
    "play_video": ("MUSIC_WHEELS",) + _HA_BASE + ("HANOVA_HA_SCRIPT_YOUTUBE",),
    "show_on_tv": _URL_CAST_BASE + ("HANOVA_HA_SCRIPT_IMAGE_URL", "OPENAI_API_KEY"),
    # nas -- querying the index is local and needs no SMB credentials at all.
    "nas_video_query": ("HANOVA_NAS_INDEX_PATH", "NAS_INDEX_FILE"),
    "play_nas_video": _NAS_FETCH + _URL_CAST_BASE + ("HANOVA_HA_SCRIPT_VIDEO_URL",),
    "nas_play_folder": _NAS_FETCH + _URL_CAST_BASE + ("HANOVA_HA_SCRIPT_VIDEO_URL",),
    "nas_skip": _NAS_FETCH + _URL_CAST_BASE + ("HANOVA_HA_SCRIPT_VIDEO_URL",),
    # google-workspace
    "calendar_add": _GOOGLE_BASE + ("HANOVA_GCAL_CALENDAR_ID",),
    "calendar_list": _GOOGLE_BASE + ("HANOVA_GCAL_CALENDAR_ID",),
    "calendar_delete": _GOOGLE_BASE + ("HANOVA_GCAL_CALENDAR_ID",),
    "task_add": _GOOGLE_BASE + ("HANOVA_GTASKS_LIST_ID",),
    "task_list": _GOOGLE_BASE,
    "task_complete": _GOOGLE_BASE,
    "task_delete": _GOOGLE_BASE,
    # drive
    "drive_list": _DRIVE_BASE,
    "drive_trash": _DRIVE_BASE,
    "drive_upload": _DRIVE_BASE,
    # notion / email
    "notion_add": ("HANOVA_NOTION_TOKEN", "HANOVA_NOTION_DATA_SOURCE_ID"),
    "email_send": ("HANOVA_SMTP_USER", "HANOVA_SMTP_APP_PASSWORD"),
}


def tool_status(tool_name: str) -> tuple[bool, str]:
    """Return (available, reason) for one ported tool. Never raises.

    *reason* is the name of the first unmet prerequisite -- a configuration key,
    never a configuration value, so it is safe to log and safe to hand to the
    model (review finding 7).
    """
    prereqs = TOOL_PREREQS.get(tool_name)
    if prereqs is None:
        return False, "unknown tool"
    for key in prereqs:
        predicate = _PREREQS.get(key)
        if predicate is None:  # pragma: no cover - a typo in the table
            return False, key
        try:
            if not predicate():
                return False, key
        except Exception:  # noqa: BLE001 - a tool call must never abort here
            logger.warning("hanova prerequisite %s could not be evaluated.", key)
            return False, key
    return True, ""


def tool_available(tool_name: str) -> bool:
    """Return whether one ported tool has everything it needs."""
    return tool_status(tool_name)[0]


def family_status(family: str) -> tuple[str, str]:
    """Aggregate a family's tools into a tri-state startup verdict. Never raises.

    `enabled` -- every tool in the family is available.
    `partial` -- some are, some are not; the reason carries the ratio and the
    first blocking key, because a half-configured family is a real state the
    operator needs to see (review finding 10).
    `disabled` -- none are.
    """
    names = FAMILY_TOOLS.get(family)
    if names is None:
        return "disabled", "unknown family"
    ready: list[str] = []
    first_reason = ""
    for name in names:
        available, reason = tool_status(name)
        if available:
            ready.append(name)
        elif not first_reason:
            first_reason = reason
    if len(ready) == len(names):
        return "enabled", ""
    if not ready:
        return "disabled", first_reason
    return "partial", f"{len(ready)}/{len(names)} tools ready; first blocker {first_reason}"


def family_enabled(family: str) -> bool:
    """Return whether every tool in one family has everything it needs."""
    return family_status(family)[0] == "enabled"


def unavailable(reason: str = "not_configured") -> Dict[str, Any]:
    """Return the exact payload a tool emits when it is not configured (R5).

    *reason* is a prerequisite key name from `tool_status`, so the model can say
    which switch is missing without ever seeing what its value would be.
    """
    return {"status": "unavailable", "reason": reason}


def log_family_status() -> None:
    """Emit one INFO line per tool family at startup (R5)."""
    for family in FAMILIES:
        verdict, reason = family_status(family)
        if reason:
            logger.info("hanova family %s: %s (%s)", family, verdict, reason)
        else:
            logger.info("hanova family %s: %s", family, verdict)
