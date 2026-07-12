"""[内核类] 通知渲染服务 — 收口所有"领域模型 → 可投递内容"的纯计算。

类别:**内核**(Kernel)。services 三类职责(编排/内核/能力)中,本服务是内核:
把 governance 领域状态翻译成可投递的通知正文 / TC 卡片 payload / 详情链接。
不碰状态机推进、不碰持久化、不碰投递本身。

依赖边界:
  - 上行(web):无(不经 router 直接调用,由编排服务 scan/record_process 调用)。
  - 下行(repo):**无** ── 只读领域模型属性,不 import `repositories/`、
    不 import `domain/protocols`、不调 `transition_*`。
  - 横向(service):无(底层 builder 函数来自 `notify_builder_service`,直接 import)。

设计说明(领域模型设计方法 — 渲染收口):
  - 渲染逻辑此前散落三处:scan `_render_reminder_md` / `_build_tc_card_payload`、
    record_process `_render_notification_md`,各内联一份"组装字段→调 builder"。
  - 本服务是唯一对外出口,三处编排服务一律改调这里,达成"渲染口径唯一"(spec A4)。
  - **不给 domain 实体挂 render 方法**(spec 约束):实体仅暴露属性,渲染在外部
    服务完成 ── 实体文件零改动。
  - TC 卡片构建失败时 `build_send_payload` 返回 None,调用方据此降级 markdown
    (与原 scan `_build_tc_card_payload` 返回 None 语义一致)。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from agentclaw.community.core.economy.governance.services.notify_builder_service import (
    build_card_notification_data,
    build_governance_reason,
    build_tc_card_detail_link,
)

if TYPE_CHECKING:
    from agentclaw.community.core.economy.governance.domain.notification import (
        GovernanceNotification,
    )
    from agentclaw.community.core.economy.governance.domain.record import (
        GovernanceRecord,
    )
    from agentclaw.community.core.economy.governance.domain.ticket import (
        GovernanceTicket,
    )


@dataclass(frozen=True, slots=True)
class SendPayload:
    """单条通知 TC 卡片投递产物(channel=tc_card,正文已渲染)。

    `build_send_payload` 成功时返回本对象;TC 卡片构建失败返回 None(调用方降级
    markdown)。非 domain 实体,仅 render 模块内部流转用。

    title 不在此产物内 ── 标题取值依 `notify.notify_type`(FIRST_SEND 首通 /
    REMINDER 提醒),是调用方编排逻辑,不归渲染。
    """

    body: str                  # TC 卡片正文(简化 reason)
    deep_link: str             # TC 卡片详情链接
    extra: dict[str, Any]      # TC 卡片 extra(bot_id/card_id/notification_data/...)


class NotifyRenderService:
    """通知渲染内核服务 ── 无状态、无 IO。

    经 DI 注入(`di/modules/economy_governance_module`);构造不依赖 repo /
    notify_sender,仅复用 `notify_builder_service` 模块函数。可单测(Mock 不需要)。
    """

    def render_first_notification_md(
        self,
        record: GovernanceRecord,
        *,
        dt_version: str,
        use_reopen_template: bool = False,
        reopen_ref_time: datetime | None = None,
    ) -> str:
        """渲染离线批首通知 Markdown(收口 record_process `_render_notification_md`)。

        Args:
            record: 上层输入的治理记录领域模型(领域校验后载体,非 DB 行)。
            dt_version: 数据版本日期(如 "20260623")。
            use_reopen_template: True → 走"重新治理"模板(§7.1.4 Step 6)。
            reopen_ref_time: 重开模板里"曾在 X 处理过"的参考时间。

        Returns:
            通知正文 Markdown 字符串。
        """
        if use_reopen_template:
            # "重新治理" template (§7.1.4 Step 6)
            time_str = (
                reopen_ref_time.strftime("%Y-%m-%d %H:%M")
                if reopen_ref_time
                else "之前"
            )
            return (
                f"#### 🔄 重新治理通知 — {record.bot_name or '未知Bot'}\n\n"
                f"该治理项曾在 {time_str} 处理过反馈。"
                f"基于最新数据复核，当前仍需要继续跟进。\n\n"
                f"请参考以下建议处理；如有补充说明，也可以继续反馈。\n\n"
                f"**命中维度**: {record.hit_dimensions or '—'}\n"
                f"**数据日期**: {dt_version}\n"
            )

        # Standard first notification template — use simplified reason builder
        return build_governance_reason(
            bot_name=record.bot_name or "",
            dt_version=dt_version,
            hit_dimensions=record.hit_dimensions,
            governance_max_priority=record.governance_max_priority,
            expected_token_saving=record.expected_token_saving,
            saving_ratio=record.saving_ratio,
            task_summary=record.task_summary,
            notification_structured=record.notification_structured,
        )

    def render_reminder_md(
        self,
        ticket: GovernanceTicket,
        *,
        now: datetime,
    ) -> str:
        """渲染提醒通知 Markdown(收口 scan `_render_reminder_md`)。

        Args:
            ticket: 治理工单领域模型(读取其快照 + 节流元信息)。
            now: cron tick 当前时刻(用于计算 overdue_days)。

        Returns:
            提醒正文 Markdown 字符串。
        """
        days_since = (now - ticket.last_sync_at).days if ticket.last_sync_at else 0
        return build_governance_reason(
            bot_name=ticket.bot_name,
            dt_version=ticket.dt_version,
            hit_dimensions=ticket.triggered_dimensions,
            governance_max_priority=ticket.severity,
            overdue_days=days_since,
        )

    def build_send_payload(
        self,
        notify: GovernanceNotification,
        *,
        user_id: str,
        config: Any,  # EconomyGovernanceConfig
    ) -> SendPayload | None:
        """构建 TC 卡片投递产物(收口 scan `_build_tc_card_payload`)。

        Args:
            notify: 通知领域模型。
            user_id: 收件人 staff_id(用于详情链接 staff_id 参数)。
            config: EconomyGovernanceConfig ── 取 tc_card_id / tc_card_preview_url /
                iframe_callback_url。

        Returns:
            SendPayload(channel=tc_card) 成功;**TC 卡片构建失败返 None** →
            调用方应降级为 markdown 频道(与原 `_build_tc_card_payload` 返回
            None 语义一致)。
        """
        try:
            reason = build_governance_reason(
                notification_structured=notify.notification_structured,
                bot_name=notify.bot_name,
                dt_version=notify.dt_version,
                hit_dimensions=notify.triggered_dimensions,
                governance_max_priority=notify.severity,
                expected_token_saving=notify.estimated_saving_tokens,
                saving_ratio=notify.saving_ratio,
                task_summary=None,
            )

            notification_data = build_card_notification_data(
                notification_structured=notify.notification_structured,
                notification_id=notify.notification_id,
                bot_id=notify.bot_id,
                bot_name=notify.bot_name,
                owner_id=notify.owner_id,
                dt_version=notify.dt_version,
                expected_token_saving=notify.estimated_saving_tokens,
                saving_ratio=notify.saving_ratio,
                governance_max_priority=notify.severity,
            )

            detail_link = build_tc_card_detail_link(
                bot_id=notify.bot_id,
                card_id=config.tc_card_id,
                notification_data=notification_data,
                base_url=config.tc_card_preview_url,
                iframe_callback_url=config.iframe_callback_url,
                staff_id=user_id,
            )

            extra = {
                "bot_id": notify.bot_id,
                "card_id": config.tc_card_id,
                "notification_data": notification_data,
                "out_track_id_prefix": "gov-notify",
            }
            return SendPayload(
                body=reason,
                deep_link=detail_link,
                extra=extra,
            )
        except Exception:
            from agentclaw.community.log import get_logger
            get_logger(__name__).exception(
                "[NotifyRender] TC card build failed for notification_id=%s",
                notify.notification_id,
            )
            return None
