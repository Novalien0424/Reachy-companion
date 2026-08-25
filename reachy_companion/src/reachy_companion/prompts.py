"""Resolve active profile prompts and voice settings."""

import logging
from pathlib import Path

from reachy_companion.config import config, get_default_voice
from reachy_companion.memory import format_memory_for_prompt
from reachy_companion.profile_store import (
    DEFAULT_PROFILE_NAME,
    ProfileDefinition,
    ProfileFormatError,
    read_profile,
    read_packaged_default_profile,
)
from reachy_companion.audio.envparse import env_bool


logger = logging.getLogger(__name__)

DEFAULT_GREETING_PROMPT = (
    "Start the conversation now with a brief, spontaneous greeting in character. "
    "Keep it to one sentence, invite the user in naturally, and vary the wording each time."
)

_HARDENING_BLOCK = """
## 聲音與回應規則（系統層，優先於角色設定）

### 不需要回應的聲音
如果最新的聲音是：安靜、背景噪音、音樂、電視聲、旁人之間的對話、
或不是對你說的話 — 呼叫 `wait_for_user` 工具，然後保持安靜。
呼叫後不要再說話。不要說「我在這裡」「我沒聽清楚」「慢慢來」。
只有當使用者清楚地對你說話或請你幫忙時才恢復回應。

### 聽不清楚時
- 只回應清楚的語音或文字。
- 聽不清楚時，用一句簡短的台灣中文請對方再說一次（例如「不好意思，
  可以再說一次嗎？」）。同樣的澄清句不要連續說兩次。
- 模糊、吵雜、只有雜音、被切斷、或你不確定對方確切說了什麼 — 都算
  聽不清楚。聽不清楚時：不要猜測、不要推理、不要呼叫其他工具。

### 語言
預設使用台灣中文（台灣國語）。只有在使用者「明確要求換語言」或
「用另一種語言說出完整的請求或問題」時才換語言。
不要因為口音、語助詞、簡短的附和、人名、地址、或夾雜的外語單字
而切換語言。工具回傳的資料、歌名、影像內容，一律用台灣中文回答。
""".strip()


def hardening_block() -> str:
    """Anti-mishearing prompt rules; REALTIME_PROMPT_HARDENING=0 disables."""
    if not env_bool("REALTIME_PROMPT_HARDENING", True):
        return ""
    return _HARDENING_BLOCK


def _active_profile() -> ProfileDefinition:
    return read_profile(config.REACHY_MINI_CUSTOM_PROFILE)


def get_session_instructions(instance_path: str | Path | None = None) -> str:
    """Return instructions for the active profile with memory context."""
    selected_profile = config.REACHY_MINI_CUSTOM_PROFILE
    profile_name = selected_profile or DEFAULT_PROFILE_NAME
    try:
        profile = _active_profile()
        instructions = profile.instructions.strip()
    except (FileNotFoundError, ProfileFormatError) as exc:
        logger.warning("Failed to load profile %r: %s", profile_name, exc)
        instructions = ""

    if not instructions and selected_profile and selected_profile != DEFAULT_PROFILE_NAME:
        logger.warning("Using bundled default instructions because profile %r is incomplete", selected_profile)
        try:
            instructions = read_packaged_default_profile().instructions.strip()
        except (FileNotFoundError, ProfileFormatError) as exc:
            raise RuntimeError("Default profile has no usable instructions") from exc
    if not instructions:
        raise RuntimeError("Default profile has no usable instructions")

    block = hardening_block()
    if block:
        instructions = f"{instructions}\n\n{block}"
    memory_prompt = format_memory_for_prompt(instance_path)
    if memory_prompt:
        return f"{memory_prompt}\n\n{instructions}"
    return instructions


def get_session_voice(default: str | None = None) -> str:
    """Return the active profile voice or the backend default."""
    fallback = get_default_voice() if default is None else default
    try:
        return _active_profile().voice or fallback
    except (FileNotFoundError, ProfileFormatError) as exc:
        logger.warning("Failed to load the active profile voice: %s", exc)
        return fallback


def get_session_greeting_prompt() -> str:
    """Return the active profile greeting prompt or the app default."""
    try:
        return _active_profile().greeting or DEFAULT_GREETING_PROMPT
    except (FileNotFoundError, ProfileFormatError) as exc:
        logger.warning("Failed to load the active profile greeting: %s", exc)
        return DEFAULT_GREETING_PROMPT
