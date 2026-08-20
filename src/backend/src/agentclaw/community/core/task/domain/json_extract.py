"""skill 回投 JSON 提取(shared kernel;stdlib only,零依赖)。

owner-bot skill(plan/search/验收)经 LLM 回投的 ``result.content`` 常被**散文 + ```` ```json … ```
```` 代码块**包裹(非裸 JSON)。本工具把 JSON 从任意包裹中抽出来,供
``task_plan.strategies._parse_children`` 与 ``task_dispatch.strategies._parse_search_result`` 复用,
保证框架对 skill 输出格式的鲁棒性(裸 JSON / 代码块 / 散文包裹均能解析)。

纯函数,无副作用,无领域语义:不在框架内写死 skill 返回结构,只做「找 JSON → parse」。
"""
from __future__ import annotations

import json
import re
from typing import Any

__all__ = ["extract_json"]

# ```json ... ``` 或 ``` ... ``` 代码块(re.DOTALL 跨行;捕获组 = 块内 JSON 文本)。
_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*\n?(.*?)```", re.DOTALL)


def extract_json(content: Any) -> Any:
    """从可能被散文/代码块包裹的 ``content`` 中抽出 JSON 并 parse。

    解析序(首个成功即返):
      1. ``content`` 非字符串 → 原样返回(交由调用方/``json`` 处理 dict/list);
      2. 裸 JSON 直 ``json.loads``(干净输出的快路径);
      3. 逐个 ``` ```json ``` / ``` ``` ``` 代码块尝试 parse;
      4. 兜底:从首个 ``[`` 或 ``{`` 起,做括号配对的平衡扫描(尊重字符串/转义),
         取配平子串 parse。

    成功返 Python 对象(list / dict / 标量);全部失败抛 ``ValueError``(无可用 JSON)。
    """
    if not isinstance(content, str):
        return content

    # 2. 快路径:裸 JSON
    try:
        return json.loads(content)
    except (ValueError, TypeError):
        pass

    stripped = content.strip()
    if stripped:
        try:
            return json.loads(stripped)
        except (ValueError, TypeError):
            pass

    # 3. 代码块 ```json ... ``` / ``` ... ```
    for m in _FENCE_RE.finditer(content):
        block = m.group(1).strip()
        if not block:
            continue
        try:
            return json.loads(block)
        except (ValueError, TypeError):
            # 代码块内仍可能夹散文 → 进括号配平兜底
            span = _balanced_substring(block)
            if span is not None:
                try:
                    return json.loads(span)
                except (ValueError, TypeError):
                    continue

    # 4. 兜底:括号配平
    span = _balanced_substring(content)
    if span is not None:
        return json.loads(span)

    raise ValueError("no parseable JSON found in content")


def _balanced_substring(text: str) -> str | None:
    """从首个 ``[`` 或 ``{`` 起,扫描到配平的 ``]`` / ``}``,返该子串;无起符返 ``None``。

    尊重字符串字面量(``"``/``'``)与转义(``\\``),避免字符串里出现的括号误判深度。
    配平失败(提前到尾或乱序)返 ``None``,由调用方决定下一步。
    """
    start = -1
    for i, ch in enumerate(text):
        if ch in "[{":
            start = i
            break
    if start < 0:
        return None
    open_ch = text[start]
    close_ch = "]" if open_ch == "[" else "}"
    depth = 0
    in_str: str | None = None  # None=不在字符串;否则记当前字符串引号字符
    i = start
    n = len(text)
    while i < n:
        ch = text[i]
        if in_str is not None:
            if ch == "\\":
                i += 2  # 跳过转义符及其后一个字符
                continue
            if ch == in_str:
                in_str = None
            i += 1
            continue
        if ch in ('"', "'"):
            in_str = ch
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
        i += 1
    return None
