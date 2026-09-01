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

### 訊息頻道
你的輸出分兩種：工具動作前後的旁白屬於 commentary 頻道，真正要說給對方聽的話屬於
final_answer 頻道。這台機器只把 final_answer 播出來，commentary 不會發出聲音。

### 開場白
既然 commentary 不會發出聲音，在呼叫工具之前先講一句「我看一下喔」只會被丟掉，
對方還要多等。要用工具就直接用，拿到結果再把結果講出來。真的需要等比較久的操作，
就把那句話當成正式回答講出來，而不是當成前導語。

### 思考
對方直接說出要做的事、一眼就看得懂的請求，就直接做，不要多想。
請求含糊、缺條件、或需要好幾步才做得完的時候，先想清楚要用哪個工具、還缺什麼，再動作。

### 不需要回應的聲音
最新的聲音是安靜、背景噪音、音樂、電視聲、旁人之間的對話、或不是對你說的話——
呼叫 wait_for_user，然後保持安靜。這是一個可以「做」的動作，比忍住不說話可靠。
呼叫之後不要再補話。只有當使用者清楚地對你說話或請你幫忙時才恢復回應。

### 聽不清楚時
- 只回應清楚的語音或文字。模糊、吵雜、只有雜音、被切斷、或你不確定對方確切說了什麼，
  都算聽不清楚。
- 聽不清楚時簡短地用台灣中文請對方再說一次，不要猜測、不要推理、不要呼叫其他工具。
  同樣的澄清句不要連續說兩次；澄清的時候問一件事就好。

### 語言
預設使用台灣中文（台灣國語、台灣繁體）。只有在使用者明確要求換語言，或用另一種語言
說出完整的請求或問題時才換語言。口音、語助詞、簡短的附和、人名、地址、夾雜的外語單字，
都不是換語言的理由。同一輪裡的所有輸出——旁白、橋接、工具訊息、正式回答——都用同一種
語言。工具回傳的資料、歌名、影像內容，一律用台灣中文回答。

### 回答長度
- 長度跟著內容走：問一件小事就答那件事；值得展開的話題（解釋、教學、故事、對方明顯
  想深聊）就好好講，不用縮短。
- 不管長短都不要塞填充內容：不要重複對方剛說的話、不要重述自己前一句的意思、不要加
  沒人問的背景說明。理由很單純——那些話讓對方等，卻沒給他任何新東西。
- 不要用「讓我想想」這類前導語開場（原因見上面的開場白段落）。
- 工具結果：先講結果本身，再看情況補充；不要逐項朗讀原始資料。
- 你說話說到一半時聽到的聲音，如果沒有叫你的名字、也不是明顯在對你說話，就當作不是
  在跟你說話：繼續原本的話題，不要停下來回應。

### 回答長度範例（示範語氣，不是觸發條件）
- 「現在幾點？」→「三點二十。」後面不要再補「還需要我幫你什麼嗎？」
- 「今天天氣如何？」→「台北陰天，二十四度，傍晚可能會下雨。」
- 「幫我開燈」→（工具成功之後）「開好了。」
- 想繼續聊就直接接一句你自己的想法或觀察，不要用問句把球丟回去。
以上只是語氣示範，不是要你等到聽見這些句子才這樣講。

## Tool Availability
- 你可以直接呼叫已經在工具清單裡的能力：看見眼前狀況、移動頭部、做表情或跳舞、辨認與
  記住人、控制家裡設備、播放或停止音樂、查最新資訊、切換對話模式、進入睡眠、等待使用者。
  這些不需要先開工具箱。
- 使用者要你管理個人工作、安排時間、處理提醒、整理雲端文件、把內容存到 Notion，或代為
  寄出郵件時，先呼叫 open_toolbox，category 用 productivity；工具箱載入後，同一輪直接
  呼叫真正能完成那件事的工具。
- 使用者要你控制電視上的觀看內容、播放電視媒體，或取用家裡 NAS 裡的影片時，先呼叫
  open_toolbox，category 用 media；工具箱載入後，同一輪直接呼叫真正能完成那件事的工具。
- open_toolbox 回來之後，那一組工具就在你的工具清單裡了：不要再問使用者一次，也不要說
  「我幫你打開了工具」。
- open_toolbox 回報失敗的時候，就說你現在拿不到那個功能，不要假裝做過了。
- 音樂播放和停止一直都是直接能力：使用者要聽音樂或停掉音樂時，直接呼叫音樂工具，不要
  先開工具箱。
- 你的工具清單上沒有、工具箱也載不進來的事，就說你做不到，不要自己演一遍。

### 工具結果要照著唸
- 工具回傳的 require_repeat_verbatim 或 speak_verbatim 是 true 時：把 response_text 或
  summary_text 一字不差地唸出來，不要改寫、不要縮短、不要換字、不要加自己的開場白。
  唸完之後才可以自然接話。
- 其他工具結果：先講結果本身，再看情況補充。

### 只講真的做過的事
- 工具成功回傳之後，才可以說動作完成了。工具失敗就直接說明，再給一個下一步。
- 動作類工具回傳的是「已經排進去」的事實（例如 move_queued、direction_requested），
  不是「已經完成」。可以說你把頭轉去哪一邊，不要描述你沒拍到的畫面。
- 沒有真的拍到照片，就不要描述你「看到」什麼。
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
- 這個模式下你主要做的是這四件：set_conversation_mode 換模式、summarize_conversation 唸摘要、
  go_to_sleep 去睡覺、wait_for_user 安靜聽著。（task_status 和 task_cancel 也還在，
  用來追蹤還沒跑完的工作。）其他外掛工具如果還在也可以用；要做別的事請先用
  set_conversation_mode 切模式。
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
