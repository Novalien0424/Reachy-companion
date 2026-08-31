"""Resolve active profile prompts and voice settings."""

import logging
from typing import Final
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
from reachy_companion.conversation_mode import ConversationMode


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

### 回答長度
- 長度跟著內容走：能一句話答完的就一句話答完；值得展開的話題
  （解釋、教學、故事、對方明顯想深聊）就好好講，不用縮短。
- 但不管長短，都不要塞填充內容：不要重複對方剛說的話、不要重述
  自己前一句的意思、不要加沒人問的背景說明。
- 一次只問一個澄清問題。
- 工具結果：先講結果本身，再看情況補充；不要逐項朗讀原始資料。
- 不要使用「讓我想想」「我看一下喔」這類前導語；只有真的需要等待的
  工具操作才可以先講一句在做什麼。
- 你說話說到一半時聽到的聲音，如果沒有叫你的名字、也不是明顯在對你
  說話，就當作不是在跟你說話：繼續原本的話題，不要停下來回應。

### 工具箱
- 一直都在手上的工具：看東西、移動、表情、認人、記憶、開關家裡的燈、放音樂和關音樂、
  上網搜尋、切換對話模式、睡覺。這些直接用，不要先開工具箱。
- 行程、約、會議、待辦、任務、提醒、郵件、寄信、雲端檔案、Notion：
  先呼叫 open_toolbox("productivity")。
- 電視、影片、MV、NAS 上的家庭影片：先呼叫 open_toolbox("media")。
- open_toolbox 回來之後工具就在了：同一輪直接接著呼叫真正要用的那個，
  不要再問使用者一次、也不要說「我幫你打開了工具」。
- open_toolbox 回報失敗的時候，就說你現在拿不到那個功能，不要假裝做過了。
""".strip()


def hardening_block() -> str:
    """Anti-mishearing prompt rules; REALTIME_PROMPT_HARDENING=0 disables."""
    if not env_bool("REALTIME_PROMPT_HARDENING", True):
        return ""
    return _HARDENING_BLOCK


# --- per-mode rules (2026-08-31 plan) ---------------------------------------
# Party mode never told the model it was in party mode: the flip only changed
# turn detection, and the gate lived entirely client-side. RECORD cannot live
# like that — "stay quiet, only summarize when called" is behavior the model
# itself must know about — so every mode now ships its own rules block,
# appended to the session instructions and re-sent on every flip.
_MODE_BLOCKS: Final[dict[ConversationMode, str]] = {
    ConversationMode.ONE_ON_ONE: """
### 目前模式：一對一聊天模式
- 現在只有一個人在跟你說話。對方不需要叫你的名字，你就正常回應。
- 你講話講到一半聽到別的聲音時，只有聽到自己的名字或「停」才停下來；其他的繼續講完。
""".strip(),
    ConversationMode.GROUP: """
### 目前模式：多人聊天模式
- 現在房間裡有好幾個人，大部分的話不是對你說的。
- 只有聽到自己的名字、或明顯是在問你的時候才開口；其他時候安靜聽著。
- 有人叫過你之後的一小段時間內可以直接接著聊，不用每一句都被點名。
""".strip(),
    ConversationMode.RECORD: """
### 目前模式：紀錄模式
- 你正在幫忙做記錄：安靜聽，把在場的人說的話都記下來。不要插話、不要附和、不要主動開口。
- 只有聽到自己的名字或「停」才回應，回應也要短。
- 有人請你總結、回顧、唸重點的時候，呼叫 summarize_conversation，然後照它回傳的
  summary_text 一字不差地唸出來，不要改寫也不要補話。
- 這個模式下你能做的事只有四件：set_conversation_mode 換模式、summarize_conversation 唸摘要、
  go_to_sleep 去睡覺、wait_for_user 安靜聽著。（task_status 和 task_cancel 也還在，
  用來追蹤還沒跑完的工作。）要做別的事，請先用 set_conversation_mode 切回其他模式。
""".strip(),
}


def mode_rules_block(mode: ConversationMode) -> str:
    """Return the rules block for *mode*, appended to the session instructions."""
    return _MODE_BLOCKS[mode]


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
