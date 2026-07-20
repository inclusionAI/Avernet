"""governance domain 共享基础工具。

领域层各实体(ticket/notification/whitelist/record)共用的纯工具,不含实体定义。
"""
from __future__ import annotations

from datetime import datetime


def _iso(value: datetime | None) -> str | None:
    """Serialize a datetime to ISO 8601 for API responses (None passes through)."""
    return value.isoformat() if value is not None else None


# ── delivery_status 归一化(task_record.delivery_status 列的单值归一助手) ──
_ALLOWED_DELIVERY_STATUSES: frozenset[str] = frozenset({"pending", "sent", "failed", "cancelled"})


def _normalize_delivery_status(raw: str | None) -> str:
    """把 delivery_status 列值归一为单值四态(pending/sent/failed/cancelled)。

    归一规则:
      - 活动4态(pending/sent/failed/cancelled)→ 直通
      - `none` / 空串 / None → ``pending``
      - 旧拼接格式(如 ``first_send:sent``)→ 提取冒号后状态,映射为四态
      - 旧 JSON(如 ``{"notify_status":"sent"}``)-> 提取 notify_status,映射为四态
      - 其他未知值 → ``pending``

    Args:
        raw: 列原始字符串(可能为活动4态、none、空、旧拼接、旧JSON)。

    Returns:
        活动4态之一;无法识别时默认 ``pending``。
    """
    if not raw:
        return "pending"

    text = raw.strip()
    if not text:
        return "pending"

    # 活动4态直通
    if text in _ALLOWED_DELIVERY_STATUSES:
        return text

    # none 哨兵归一为 pending
    if text == "none":
        return "pending"

    # 旧 JSON 格式(以 { 开头)—— 必须在拼接分支前判断,
    # 因为 JSON 串含冒号会被 "type:status" 分支误切。
    if text.startswith("{"):
        import json

        try:
            decoded = json.loads(text)
        except (ValueError, TypeError):
            return "pending"

        if not isinstance(decoded, dict):
            return "pending"

        status = decoded.get("notify_status")
        if isinstance(status, str) and status in _ALLOWED_DELIVERY_STATUSES:
            return status

        return "pending"

    # 旧拼接格式 "type:status" (如 "first_send:sent")
    if ":" in text:
        _, _, status = text.partition(":")
        status = status.strip()
        return status if status in _ALLOWED_DELIVERY_STATUSES else "pending"

    # 其他未知值归一为 pending
    return "pending"