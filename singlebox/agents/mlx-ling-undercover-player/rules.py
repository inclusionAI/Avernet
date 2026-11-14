"""玩家规则与输出安全层（「谁是卧底」MLX 玩家 Bot）。

规则移植（自 OpenClaw skill，见 Avernet #2611 要求）：
- 发言 ≤25 字（按字符计），且不说出自己词语里的任何一个字；
- 投票只交「我投N号」或「我弃权」，必须可被主持人脚本解析；
- 模型输出万一带了多余文本，本层做正则提取与截断兜底（工程安全网，
  保证 100% 投票可解析这一验收项）。
"""

import re

MAX_CLUE_LEN = 25
VOTE_RE = re.compile(r"我投(\d+)号")
ABSTAIN = "我弃权"
# Ling chat 模板结构残渣（thinking 块、角色标记）——模型可能连带输出，不是发言内容
TAG_RE = re.compile(r"</?[a-zA-Z|][^>]*>")


def _strip_tags(text):
    if "</think>" in text:
        text = text.split("</think>", 1)[1]
    elif "<think>" in text:
        return ""
    return TAG_RE.sub("", text.split("<|role_end|>")[0])


def clamp_clue(text, banned_chars=()):
    """清洗发言：取首行、去模板残渣/markdown/引号、限 25 字、去泄词。"""
    cleaned = _strip_tags(text).strip()
    out = cleaned.splitlines()[0].strip() if cleaned else ""
    # 模型可能输出序号或前缀，剥掉
    out = re.sub(r"^\s*\d+[\.、)]\s*", "", out)
    # 去 markdown 痕迹（**加粗**、# 标题、`代码`）与首尾引号
    out = re.sub(r"[*#`]", "", out)
    out = out.strip("\"'「」“”‘’ ")
    # 尾部残缺标点（如模板残留的冒号）不计入发言
    out = out.rstrip("：:，,、。.!！?？;；")
    out = out[:MAX_CLUE_LEN]
    # 泄词兜底：删掉自己词语里的字（极端情况下宁可少说）
    for ch in banned_chars:
        out = out.replace(ch, "")
    fallback = "和大家说的差不多"
    for ch in banned_chars:
        fallback = fallback.replace(ch, "")
    return out or fallback or "…"


def parse_vote(text, own_seat=None, allowed_seats=None):
    """提取投票：优先「我投N号」，其次「我弃权」，否则强制弃权。"""
    m = VOTE_RE.fullmatch(_strip_tags(text).strip())
    if m:
        seat = int(m.group(1))
        if not 1 <= seat <= 6 or seat == own_seat:
            return ABSTAIN
        if allowed_seats is not None and seat not in allowed_seats:
            return ABSTAIN
        return f"我投{seat}号"
    return ABSTAIN
