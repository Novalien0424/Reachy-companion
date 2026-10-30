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
    "現在用簡短自然的台灣中文主動問候使用者，順口介紹一下你自己是 Reachy。"
    "語氣自然、有角色感，每次換一種順口的說法。"
)

_HARDENING_BLOCK = """
## 系統層規則（優先於角色設定）

### 頻道與開場白
- 工具前後的旁白（commentary）和正式回答（final_answer）都會播出來，兩者用同一種語言。
- 查網路、找音樂、畫圖這類要花時間的工作：先說一句自然的前導語，接著直接呼叫工具，拿到結果再說結果。
  前導語不能代替呼叫，否則對方只聽到開始、等不到結果。
- 移動、轉頭、表情、模式切換這類快速動作直接做，不加旁白，也不逐步播報，因為那只會讓對方多等。

### 變化
- 前導語、問候、附和和確認每輪換個自然說法，不重複上一輪的句子，因為重複聽起來像錄音。
  工具描述裡的示範語氣只是其中一種說法。

### 思考
- 一聽就懂的請求就直接做；含糊、缺條件或要好幾步的，先想清楚用哪個工具、還缺什麼，再動作。

### 不需要回應的聲音
- 安靜、噪音、音樂、電視聲、旁人之間的對話，或不是對你說的話：呼叫 wait_for_user，之後不要補話：這是一個可以「做」的動作，比忍住不說話可靠。
- 你說話說到一半時聽到的聲音，沒叫你的名字、也不是明顯在對你說話，就繼續原本的話題。

### 語言
- 預設使用台灣中文（台灣國語、台灣繁體）。只有對方明確要求，或用另一種語言說出完整的請求時才換；
  口音、語助詞、附和、人名、地址、夾雜的外語單字都不算。
- 同一輪的旁白、工具訊息和回答都用同一種語言；工具回傳的資料、歌名、畫面內容也用台灣中文說。

### 回答長度
- 長度跟著內容走：小事就答那件事；值得展開的話題（解釋、教學、故事、對方想深聊）就好好講。
- 不塞填充：不重複對方的話、不重述自己的意思、不加沒人問的背景，因為那只讓人多等。
  不要用「讓我想想」這類話開場，因為它不是結果，只是讓人等。
- 工具結果先講結果本身，不逐項朗讀原始資料，因為對方要的是結果。
- 示範語氣，不是觸發條件：「現在幾點？」→「三點二十。」（後面不補「還需要我幫你什麼嗎？」）；
  「幫我開燈」→（成功之後）「開好了。」想繼續聊就接一句你自己的想法，不要用問句把球丟回去。

## Tool Availability
- 你能做的事都在工具清單裡，直接呼叫就好；清單上沒有的事，就說你做不到，不要自己演一遍。
- 要放影片卻沒說來源、只給了地點、旅行、年份或活動時（例如「放花蓮的影片」），先問一句要找家裡的影片庫還是 YouTube，
  等回答了再照原本的要求做。說了「影片庫」「NAS」或「YouTube」就是已經說了來源，直接用那個來源。

### 工具結果
- require_repeat_verbatim 或 speak_verbatim 是 true：把 response_text 或 summary_text 一字不差地唸出來，唸完才自然接話。
- 工具成功回傳之後才說做好了。動作工具回傳的是「已經排進去」（move_queued、direction_requested），不是
  「已經完成」；沒拍到照片，就不要描述看到什麼，因為那會是編出來的。
- 工具失敗時，error 會說發生了什麼、接下來怎麼做：是你自己能修正的（換個參數、換成英文再查一次），就直接再呼叫一次；
  是要跟對方說的，就照它的意思用自己的話說，再給一個下一步。
""".strip()


# plan rev 3 B1/B2/B3/A3: placement beats volume for fragment recovery, so this follows mode rules.
_UNCLEAR_AUDIO_BLOCK = """
### 聽不清楚時
只回應清楚的語音或文字。聽到片段、半句、被切斷的話、或不確定對方確切說了什麼時，
用對話正在使用的語言簡短請對方再說一次，因為猜測會回答到沒人說過的事。
這一輪不要呼叫其他工具，因為工具會把猜測變成動作或查詢；等對方說清楚再做。
同樣的澄清說法不要連續重複，因為重複會像卡住；換一個自然問法就好。
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
- 這個模式下你做的是這四件：set_conversation_mode 換模式、summarize_conversation 唸摘要、
  go_to_sleep 去睡覺、wait_for_user 安靜聽著。其他外掛工具如果還在也可以用；要做別的事
  請先用 set_conversation_mode 切模式。
""".strip(),
}


def mode_rules_block(mode: ConversationMode) -> str:
    """Return the rules block for *mode*, appended to the session instructions."""
    block = _MODE_BLOCKS[mode]
    if not env_bool("REALTIME_PROMPT_HARDENING", True):
        return block
    return f"{block}\n\n{_UNCLEAR_AUDIO_BLOCK}"


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
        # Appended, not prepended (2026-09-01 instructing wave). It used to sit
        # ahead of the persona as an unlabeled fact list, so the first thing the
        # model read was data with no statement of what it was for. Last position
        # is also the strongest in a prompt whose compliance decays across a
        # conversation -- placement beats volume.
        return f"{instructions}\n\n{memory_prompt}"
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
