"""governance domain 共享基础工具。

领域层各实体(ticket/notification/whitelist/record)共用的纯工具,不含实体定义。
"""
from __future__ import annotations

import json
from datetime import datetime


def _iso(value: datetime | None) -> str | None:
    """Serialize a datetime to ISO 8601 for API responses (None passes through)."""
    return value.isoformat() if value is not None else None


# ── delivery_status 字段编解码(task_record.delivery_status 列的序列化助手) ──
# 历史存拼接字符串("first_send:sent"),改 JSON 后可多存 sent_at /
# external_message_id / error 等投递细节。列存 JSON 字符串(String(255),
# 不引入 dialect-specific JSON 类型);写入走 build_delivery_status_json,
# 读取走 parse_delivery_status(旧行拼接自动 fallback,无需存量迁移)。
_DELIVERY_FIELDS: tuple[str, ...] = (
    "notify_type",
    "notify_status",
    "sent_at",
    "external_message_id",
    "error",
)

# 既非 JSON、也无冒号的特殊状态值(notify_type 全空,notify_status 取该字面量)。
_DELIVERY_BARE_STATUSES: frozenset[str] = frozenset({"none", "cancelled"})


def build_delivery_status_json(
    notify_type: str | None,
    notify_status: str,
    *,
    sent_at: datetime | str | None = None,
    external_message_id: str | None = None,
    error: str | None = None,
) -> str:
    """构造 ``delivery_status`` 的 JSON 字符串(固定字段顺序,5 key 齐全)。

    Args:
        notify_type: 通知类型(first_send / reminder),``cancelled``/``none`` 等无
            通知类型时传 ``None``。
        notify_status: 投递状态(pending / sending / sent / failed / cancelled / none)。
        sent_at: 发送成功时间(ISO 字符串或 datetime);未发/失败为 ``None``。
        external_message_id: 外部消息 ID(发送成功回填);无则 ``None``。
        error: 失败原因(发送失败回填);无则 ``None``。

    Returns:
        固定字段顺序的 JSON 字符串,5 key 齐全,缺省值为 ``None``(JSON null)。
        ``sent_at`` 若为 datetime 自动转 ISO 字符串。
    """
    payload: dict[str, object] = {
        "notify_type": notify_type,
        "notify_status": notify_status,
        "sent_at": sent_at.isoformat() if isinstance(sent_at, datetime) else sent_at,
        "external_message_id": external_message_id,
        "error": error,
    }
    # 显式按固定顺序序列化(dict 3.7+ 保序,这里再兜底以防未来重排)。
    ordered = {k: payload[k] for k in _DELIVERY_FIELDS}
    return json.dumps(ordered, ensure_ascii=False)


def parse_delivery_status(raw: str | None) -> dict[str, object]:
    """把 ``delivery_status`` 列值解析为固定 5-key dict(兼容旧拼接格式)。

    解析规则:
      - ``None`` / 空串 → ``{"notify_type": None, "notify_status": "none", ...}``
      - 以 ``{`` 开头 → JSON 解析;解析失败兜底 ``"none"``
      - ``_DELIVERY_BARE_STATUSES``(``none`` / ``cancelled``)→ notify_type=None,
        notify_status=该值
      - 否则按 ``"type:status"`` 旧拼接 fallback split(单段时 notify_status 取
        该段、notify_type=None;缺第二段补 ``"none"``)

    Args:
        raw: 列原始字符串(可能为 JSON、旧拼接、特殊状态、空)。

    Returns:
        5 key 齐全的 dict(notify_type/notify_status/sent_at/
        external_message_id/error),缺省值为 ``None``。不含意料外 key。
    """
    parsed: dict[str, object] = {k: None for k in _DELIVERY_FIELDS}
    if not raw:
        parsed["notify_status"] = "none"
        return parsed

    text = raw.strip()
    if not text:
        parsed["notify_status"] = "none"
        return parsed

    if text.startswith("{"):
        try:
            decoded = json.loads(text)
        except (ValueError, TypeError):
            parsed["notify_status"] = "none"
            return parsed
        if not isinstance(decoded, dict):
            parsed["notify_status"] = "none"
            return parsed
        for k in _DELIVERY_FIELDS:
            if k in decoded:
                parsed[k] = decoded[k]
        # notify_status 缺省兜底 "none"(防止旧/脏 JSON 漏字段导致 None 状态)。
        if parsed["notify_status"] is None:
            parsed["notify_status"] = "none"
        return parsed

    if text in _DELIVERY_BARE_STATUSES:
        parsed["notify_status"] = text
        return parsed

    # 旧拼接 "type:status" fallback。
    if ":" in text:
        head, _, tail = text.partition(":")
        parsed["notify_type"] = head or None
        parsed["notify_status"] = tail or "none"
    else:
        # 单段无冒号(未知裸值):当作状态,notify_type 留空。
        parsed["notify_status"] = text
    return parsed