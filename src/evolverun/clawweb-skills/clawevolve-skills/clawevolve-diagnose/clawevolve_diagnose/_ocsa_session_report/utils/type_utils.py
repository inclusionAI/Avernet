"""
类型转换工具 — 安全的类型转换函数

提供各种类型的安全转换，处理 None、NaN、inf 等边界情况。
"""

from __future__ import annotations

import json
import math
from typing import Any


# ── JSON 转义修复 ─────────────────────────────────────────────────────

_VALID_JSON_ESCAPES = set('"\\bfnrt/')


def fix_json_escapes(s: str) -> str:
    r"""修复无效的 JSON 转义序列

    JSON 只允许: \" \\ \/ \b \f \n \r \t \uXXXX
    无效转义如 \x10, \' 会被修复。
    """
    result = []
    i = 0
    while i < len(s):
        if s[i] == '\\' and i + 1 < len(s):
            next_ch = s[i + 1]
            if next_ch in _VALID_JSON_ESCAPES:
                result.append(s[i:i + 2])
                i += 2
            elif next_ch == 'u':
                # 保留合法的 \uXXXX
                if i + 5 < len(s) and all(c in '0123456789abcdefABCDEF' for c in s[i + 2:i + 6]):
                    result.append(s[i:i + 6])
                    i += 6
                else:
                    result.append(next_ch)
                    i += 2
            elif next_ch == 'x' and i + 3 < len(s):
                # \xHH 十六进制转义 → 替换为 Unicode 转义
                hex_str = s[i + 2:i + 4]
                try:
                    code_point = int(hex_str, 16)
                    result.append(f'\\u{code_point:04x}')
                    i += 4
                except ValueError:
                    result.append(next_ch)
                    i += 2
            else:
                # 其他无效转义：去除反斜杠，保留字符
                result.append(next_ch)
                i += 2
        else:
            result.append(s[i])
            i += 1
    return ''.join(result)


def safe_json_parse(value: Any) -> Any:
    """安全解析 JSON 字符串

    处理常见问题：
    - None 返回 None
    - 已是 dict/list 直接返回
    - 无效转义序列自动修复
    """
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            pass
        # 修复无效转义序列后重试
        try:
            fixed = fix_json_escapes(value)
            return json.loads(fixed)
        except (json.JSONDecodeError, ValueError):
            return None
    return None


def safe_int(value: Any) -> int | None:
    """安全转为 int，失败返回 None"""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def safe_float(value: Any) -> float | None:
    """安全转为 float，失败返回 None"""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def safe_int_or_null(val: Any) -> int | None:
    """安全地转换为正整数或 null，处理 NaN/inf 等非有限值"""
    if val is None:
        return None
    try:
        if isinstance(val, float):
            if not math.isfinite(val):
                return None
        n = int(val)
        return n if n >= 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def safe_bool(value: Any) -> bool:
    """安全转为 bool

    支持以下格式：
    - bool: 直接返回
    - int: 0->False, 非0->True
    - str: "true"/"1"->True, "false"/"0"->False (不区分大小写)
    - 其他: bool(value)
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        lower = value.lower().strip()
        if lower in ("true", "1"):
            return True
        if lower in ("false", "0"):
            return False
        return bool(value)
    return bool(value)


def parse_comma_list(value: Any) -> list[str]:
    """从逗号分隔字符串解析为去重列表"""
    if not value:
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str):
        items = [x.strip() for x in value.split(",") if x.strip()]
        seen = set()
        result = []
        for item in items:
            if item not in seen:
                seen.add(item)
                result.append(item)
        return result
    return []


def parse_tool_positions(tool_names_str: str, tool_idx_str: str) -> dict[str, list[int]]:
    """解析工具名称与 message index 的对应关系

    tool_names 和 tool_idx 通过逗号分隔一一对应，同一工具出现多次时合并 index 列表。

    Args:
        tool_names_str: "data-query-shortcut,mcporter" (逗号分隔)
        tool_idx_str: "6,3" (逗号分隔，与 tool_names_str 一一对应)

    Returns:
        {"data-query-shortcut": [6], "mcporter": [3]}
        同一工具出现多次: {"mcporter": [3, 7]}
    """
    import logging
    logger = logging.getLogger(__name__)

    if not tool_names_str or not tool_idx_str:
        return {}

    names = [x.strip() for x in tool_names_str.split(",") if x.strip()]
    idxs = [x.strip() for x in tool_idx_str.split(",") if x.strip()]

    if len(names) != len(idxs):
        logger.warning(f"[parse_tool_positions] name/idx length mismatch: "
                       f"{len(names)} names vs {len(idxs)} idxs")
        return {}

    result: dict[str, list[int]] = {}
    for name, idx_str in zip(names, idxs):
        try:
            idx_val = int(idx_str)
        except (ValueError, TypeError):
            continue
        if name not in result:
            result[name] = []
        result[name].append(idx_val)

    return result