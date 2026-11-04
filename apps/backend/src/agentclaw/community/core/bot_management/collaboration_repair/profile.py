"""Bot 头像投影 —— 从已保存的 Bot 记录读取当前头像的唯一出处。

日常注册（create / start / restart）与日常编辑（``update_bot`` → BCN 同步）
都必须把「已保存记录」的头像投影传给下游（BCN / BCS），绝不使用本次请求的
partial ``ext``：请求里没带的字段一旦混进出站 payload，就会把远端已持久化的
数据覆盖掉。Task 4 的存量修复同样复用这里的投影函数。

出站三态语义（Task 2 已在 BCN transport 落地）：

- 非空字符串 —— 原样发送；
- 空字符串 —— 显式发送，清空远端头像（「明确无头像」不等于缺失）；
- 字段省略 —— 不发送，远端保留旧值。
"""

from __future__ import annotations

import json
from collections.abc import Mapping

from agentclaw.community.log import get_logger

logger = get_logger()

_AVATAR_URL_KEY = "avatar_url"


def avatar_from_record(bot: Mapping[str, object]) -> str:
    """读取 Bot 记录上的头像投影。

    读取顺序（只做 ``is not None`` / 键存在判断，不做任何 truthiness 回退）：

    1. 顶层 ``avatar_url`` 为字符串（含 ``""``）—— 原样返回。顶层的空串是
       一个明确的「无头像 / 清空」状态，绝不能被当作缺失而回落到 ``ext``。
    2. 顶层缺失或为 ``None`` —— 视为未提供，回落到历史存储
       ``ext["avatar_url"]`` 的字符串。``ext`` 若是原始 JSON 字符串，用
       ``json.loads`` 做结构化解析（与既有 record 解析入口同口径），
       不做 ad hoc 截取。
    3. 两层都未携带 —— 返回 ``""``。

    头像字段「明确存在但类型非法」（顶层或 ``ext``，含 ``ext`` 里的
    ``None``）抛 ``ValueError``，不静默当作空串：非法值一旦被吞成 ``""``，
    出站调用就会把远端头像「误清空」。读不出来必须向上暴露，由出站方
    决定省略字段，而不是伪造数据。

    Args:
        bot: 已保存的 Bot 记录（repository / resolver 返回的 Mapping）。

    Returns:
        头像投影；``""`` 表示「明确无头像」。

    Raises:
        ValueError: 头像字段或 ``ext`` 明确存在但无法结构化读取。
    """
    top_level = bot.get(_AVATAR_URL_KEY)
    if top_level is not None:
        return _string_value_or_invalid(top_level, source="bot record top level")

    ext = _structured_ext(bot)
    if _AVATAR_URL_KEY in ext:
        return _string_value_or_invalid(ext[_AVATAR_URL_KEY], source="bot record ext")
    return ""


def outbound_avatar_from_record(
    bot: Mapping[str, object], *, log_context: str
) -> str | None:
    """出站（BCN 同步 / Provider 注册）链路使用的安全投影。

    包装 :func:`avatar_from_record`：

    - 投影读得出来（含明确空串 ``""``）—— 返回投影，调用方原样透传；
    - 记录上的头像字段「明确存在但类型非法」或 ``ext`` JSON 损坏 ——
      返回 ``None``，调用方据此省略 ``avatar_url`` 边界参数，BCN/BCS 保留
      远端旧值。既不把读不出来的数据伪造成 ``""``（那是显式清空），
      也不让投影读失败打断所在 best-effort 流程；失败以 warning 记录。

    Args:
        bot: 调用方已持有的已保存 Bot 记录。
        log_context: 调用点日志标识，用于定位是哪条出站链路读不出来。

    Returns:
        头像投影（``""`` 表示明确无头像）；``None`` 表示读取失败，
        调用方必须省略头像字段。
    """
    try:
        return avatar_from_record(bot)
    except ValueError as error:
        logger.warning(
            "%s: avatar projection unreadable, omitting avatar_url "
            "(remote keeps its stored avatar): %s",
            log_context,
            error,
        )
        return None


def _string_value_or_invalid(value: object, *, source: str) -> str:
    """Return ``value`` when it is a string, else reject it as invalid.

    Explicitly-present values of the wrong type must surface as ``ValueError``
    instead of silently becoming an empty projection.
    """
    if isinstance(value, str):
        return value
    raise ValueError(
        f"Bot avatar_url in {source} must be a string, got {type(value).__name__}"
    )


def _structured_ext(bot: Mapping[str, object]) -> Mapping[str, object]:
    """Structurally read the record's ``ext``; unreadable ext raises ``ValueError``.

    Accepts the two shapes the persisted records actually have: a mapping, or a
    raw JSON object string (parsed with ``json.loads``, mirroring the existing
    record parse in ``bot_service_parts.metadata``). Anything else — including
    corrupt JSON or a JSON string decoding to a non-object — is unreadable and
    must not be guessed into data.

    Deliberate divergence from that metadata merge path: the parse mechanism is
    the same, but metadata's inline parse silently falls back to ``{}`` on
    corrupt JSON, while this strict read raises. A silent ``{}`` fallback here
    would fabricate an empty avatar projection — an explicit clear — for the
    outbound sync/register calls. "Unifying" the two semantics would
    reintroduce that fabricated clear; keep them apart.
    """
    raw = bot.get("ext")
    if raw is None:
        return {}
    if isinstance(raw, Mapping):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ValueError(f"Bot ext is corrupt JSON: {error}") from error
        if isinstance(parsed, Mapping):
            return parsed
        raise ValueError(
            f"Bot ext JSON must decode to an object, got {type(parsed).__name__}"
        )
    raise ValueError(
        f"Bot ext must be an object or a JSON object string, got {type(raw).__name__}"
    )
