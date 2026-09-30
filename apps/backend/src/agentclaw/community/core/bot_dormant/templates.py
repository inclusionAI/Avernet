"""Notification content templates.

Rendered once in the 03:00 scan cron (when enqueueing into
ac_bot_dormant_notify_log.content), so the bot-container side can
pass the content straight to DingTalk MCP without any further
formatting.
"""
from __future__ import annotations

from typing import Optional


# The bot-detail link template is deployment config
# (``dormant.action_link_pattern`` in the yaml, on ``DormantNotifyConfig``),
# passed into each render function by DormantBotService. Empty (community build)
# ⇒ the rendered action link is an empty string (copy still renders).


WARN_TEMPLATE = """⚠️ Bot 沉寂预警

您的 Bot【{bot_name}】已连续 {days_inactive} 天无活动。

⏰ 将在 {remaining_days} 天后被自动回收。

💡 立即对话或调用即可恢复正常状态。

[查看详情]({action_link})"""


RECYCLE_TEMPLATE = """🗑️ Bot 已回收

您的 Bot【{bot_name}】沉寂超限,已被自动回收。

容器资源已释放,Bot 数据保留。您可通过「激活」按钮恢复使用。
⚠️ 激活后请及时使用(对话或调用),否则次日可能再次被回收。

[查看详情]({action_link})"""


EXTERNAL_FALLBACK_TEMPLATE = """🗑️ Bot 平台治理通知

您的 Bot【{bot_name}】被识别为治理对象。

- 治理来源: {governance_source}
- 治理维度: {governance_dimension}
- 原因: {reason}

如需保留,请联系平台运营加入白名单。

[查看详情]({action_link})"""


def render_warn(
    *,
    bot_name: str,
    days_inactive: int,
    cooldown_days: int,
    M: int,
    bot_id: str,
    action_link_pattern: str = "",
) -> str:
    """Render the dormant-bot warning notification.

    remaining_days is the cooldown remaining, clamped at 0:
    cooldown_days=0 → remaining_days=M (first warn today)
    cooldown_days=M-1 → remaining_days=1 (last warn before recycle)
    cooldown_days>=M → remaining_days=0 (only happens if scheduler races,
                                          clamp prevents negative copy)
    """
    remaining_days = max(0, M - cooldown_days)
    return WARN_TEMPLATE.format(
        bot_name=bot_name or bot_id,
        days_inactive=days_inactive,
        remaining_days=remaining_days,
        action_link=action_link_pattern.format(bot_id=bot_id),
    )


def render_recycle(*, bot_name: str, bot_id: str, action_link_pattern: str = "") -> str:
    return RECYCLE_TEMPLATE.format(
        bot_name=bot_name or bot_id,
        action_link=action_link_pattern.format(bot_id=bot_id),
    )


def render_external_fallback(
    *,
    bot_name: str,
    governance_source: Optional[str],
    governance_dimension: Optional[str],
    reason: Optional[str],
    bot_id: str,
    action_link_pattern: str = "",
) -> str:
    return EXTERNAL_FALLBACK_TEMPLATE.format(
        bot_name=bot_name or bot_id,
        governance_source=governance_source or "unknown",
        governance_dimension=governance_dimension or "unknown",
        reason=reason or "未提供原因",
        action_link=action_link_pattern.format(bot_id=bot_id),
    )
