"""与主持人 ``undercover.py`` 对齐的输出硬校验。

这里只判定可机械判定的部分：字数、泄词（完整词/连续两字/拆字）、票面格式、
自投与弃权限制。语义层面的“钝度”仍由提示词与主持人人工验收负责。
"""

from __future__ import annotations

import re
from collections.abc import Iterable

_QUOTE_PAIRS = (("「", "」"), ("『", "』"), ("“", "”"), ('"', '"'), ("'", "'"), ("`", "`"))
_SEAT_PATTERN = re.compile(r"(\d+)\s*号")
_META_PATTERNS = (
    re.compile(r"我的词|我拿到的是|我是卧底|我是平民|自己的身份|确认身份"),
    re.compile(r"第\s*\d+\s*轮"),
    re.compile(r"投票|票数|规则"),
)


def normalize_sentence(text: str) -> str:
    """去掉包裹引号并压缩空白；不改动实际内容。"""

    value = text.strip()
    for left, right in _QUOTE_PAIRS:
        if len(value) >= 2 and value.startswith(left) and value.endswith(right):
            value = value[1:-1].strip()
    return re.sub(r"\s+", " ", value)


def bigrams(word: str) -> list[str]:
    """与主持人 ``undercover.bigrams`` 相同：全部连续两字片段。"""

    return [word[index : index + 2] for index in range(len(word) - 1)]


def _leak_reason(text: str, word: str) -> str | None:
    if not word:
        return None
    if word in text:
        return "说出了自己的词"
    grams = bigrams(word)
    if any(gram in text for gram in grams):
        return "说出了自己词里连续的两个字"
    if word and all(character in text for character in word):
        return "把自己的词拆开说了"
    return None


def _meta_reason(text: str) -> str | None:
    for pattern in _META_PATTERNS:
        if pattern.search(text):
            return f"包含元信息：{pattern.pattern}"
    return None


def speech_violation(text: str, word: str, max_chars: int) -> str | None:
    """返回违规原因；合法返回 None。判定顺序与主持人 ``check_text`` 一致。"""

    if not text:
        return "没有内容"
    leak = _leak_reason(text, word)
    if leak is not None:
        return leak
    if len(text) > max_chars:
        return f"超过 {max_chars} 字"
    meta = _meta_reason(text)
    if meta is not None:
        return meta
    return None


def vote_target(text: str) -> int | None:
    match = re.fullmatch(r"我投\s*(\d+)\s*号", text)
    return int(match.group(1)) if match else None


def vote_violation(
    text: str,
    *,
    legal_targets: Iterable[int],
    self_seat: int | None,
    allow_abstain: bool,
    max_chars: int,
) -> str | None:
    if not text:
        return "没有内容"
    if len(text) > max_chars:
        return f"超过 {max_chars} 字"
    if text == "我弃权":
        return None if allow_abstain else "本阶段禁止弃权"
    target = vote_target(text)
    if target is None:
        return "票面必须是我投N号或我弃权"
    if target == self_seat:
        return "不能投自己"
    if target not in set(legal_targets):
        return f"{target}号不是合法候选"
    return None


def eulogy_violation(text: str, word: str, max_chars: int, seats: Iterable[int]) -> str | None:
    if not text:
        return "没有内容"
    leak = _leak_reason(text, word)
    if leak is not None:
        return leak
    if len(text) > max_chars:
        return f"超过 {max_chars} 字"
    if re.search(r"我是卧底|我是平民|我的词|自己的身份", text):
        return "遗言不得声称身份或谈论自己的词"
    mentioned = {int(match.group(1)) for match in _SEAT_PATTERN.finditer(text)}
    if not mentioned:
        return "遗言必须点名一个怀疑的座位号"
    if set(seats) and not (mentioned & set(seats)):
        return "遗言点到的座位不在场上"
    return None
