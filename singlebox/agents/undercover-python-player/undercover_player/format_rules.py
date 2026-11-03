"""玩家行为规则：发言/投票格式，与主持人脚本 undercover.py 的解析逐字对齐。

主持人 undercover.py 事实层（scripts/undercover.py）：
  - SPEECH_MAX_CHARS = 25，VOTE_MAX_CHARS = 10
  - 投票解析 parse_ballot：
      * 文本以 '{' 开头 → 结构化投票（kind=vote, target_actor_id|abstain），本引擎不发
      * 含「弃权」→ 弃权（PK 时禁止）
      * 正则 `投[^0-9零一二两三四五六七八九]{0,4}([0-9]+|[零一二两三四五六七八九])`
        抓到单个合法非己座位号 → 有效票；否则全数字里唯一一个 → 有效票
      * 整串只有票号且 ASCII+下划线冒号 → 「投票目标无效」
      * 其余 → 「读不出投给谁」
  因此正确输出只要「我投4号」这种形式保证解析。本引擎 format_vote 直接
  产出「我投N号」或「我弃权」，天然 100% 可解析。

说话泄词判定 check_text（服务端兜底，也用于本地预检）：
  - 完整词出现 → 说出自己的词
  - 词里任意连续两字出现 → 说出词里连续的两个字
  - 词的每个字都被说过 → 把自己的词拆开说了
  - 对家词完整出现也会遮蔽
"""
from __future__ import annotations

MAX_SPEECH_CHARS = 25
MAX_VOTE_CHARS = 10

_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def bigrams(word: str) -> list[str]:
    return [word[i:i + 2] for i in range(len(word) - 1)]


def check_own_word_leak(word: str, text: str) -> str | None:
    """返回违规原因或 None。与 undercover.py check_text 同判定（不含对家）。"""
    if not word:
        return None
    if word in text:
        return "说出了自己的词"
    if any(g in text for g in bigrams(word)):
        return "说出了自己词里连续的两个字"
    if all(ch in text for ch in word):
        return "把自己的词拆开说了"
    return None


def world_leak_reason(word: str, text: str) -> str | None:
    return check_own_word_leak(word, text)


def validate_speech(word: str | None, text: str, max_chars: int = MAX_SPEECH_CHARS):
    """预检发言是否合规，返回 (ok, reason)。"""
    text = text.strip()
    if not text:
        return False, "空回复"
    if len(text) > max_chars:
        return False, f"超过 {max_chars} 字"
    if word:
        r = check_own_word_leak(word, text)
        if r:
            return False, r
    # 不文明空泛/反问品牌等由引擎人设约束，不在此硬校验
    return True, None


def format_vote(target: int | None, allow_abstain: bool = True) -> str:
    """构造能被 parse_ballot 必然解析的票面。

    - target 合法（非自己且存活）→ 「我投N号」
    - allow_abstain 且 target 为 None → 「我弃权」
    - 否则（PK 禁止弃权且无目标）返回 None，由调用方转成只投票号格式。
    """
    if target is not None:
        return f"我投{target}号"
    if allow_abstain:
        return "我弃权"
    return ""


def validate_vote(target: int | None, allow_abstain: bool, living: set[int], self_seat: int | None):
    """语义校验，与 parse_vote 规则对齐。返回 (ok, reason, ballot_text)。"""
    if allow_abstain and target is None:
        return True, None, "我弃权"
    if target is None:
        return False, "PK 禁止弃权", None
    if self_seat is not None and target == self_seat:
        return False, "投了自己", None
    if living and target not in living:
        return False, "投票目标无效", None
    if target is not None and (not living or target in living):
        return True, None, f"我投{target}号"
    return False, "非法票面", None


def bluntness_for_round(round_num: int | None) -> int:
    """与 undercover.py bluntness_n 一致的钝度阶梯：4 → 3 → 2。"""
    if round_num is None:
        return 2
    return {1: 4, 2: 3}.get(round_num, 2)