"""DiscoveredTask 领域模型 — 已发现待执行任务的只读投影。

字段映射自 mock 数据中的挖掘结果结构（项目名称/项目简介/业务场景/挖掘依据）。
支持按 (bot_id, owner_id, dt) 维度查询。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class DiscoveredTask:
    """一个已发现但尚未执行的待确认任务。

    Attributes:
        task_id:            唯一标识 (discover_task_{bot_id}_{owner_id}_{dt})。
        bot_id:             所属 bot ID。
        owner_id:           bot 所有者 ID。
        dt:                 日期 YYYY-MM-DD。
        project_name:       项目名称（同时用作 engine session title）。
        description:        项目简介。
        business_scenario:  业务场景描述。
        discovery_basis:    挖掘依据 — 行为节点演进链路，说明为何发现此任务。
        work_item_url:      关联的需求/工作项 URL（执行时传给 engine）。
        priority:           优先级 (high / medium / low)。
        discovered_at:      被发现的时间戳。
        status:             当前状态：
            - ``pending_confirmation`` — 待用户确认
            - ``confirmed``           — 用户已确认，待执行
            - ``executing``           — 已提交 engine 执行
            - ``ignored``             — 用户忽略
    """

    task_id: str
    bot_id: str
    owner_id: str
    dt: str
    project_name: str
    description: str
    business_scenario: str
    discovery_basis: str
    work_item_url: Optional[str] = None
    priority: str = "medium"
    discovered_at: Optional[str] = None
    status: str = "pending_confirmation"

    @property
    def needs_confirmation(self) -> bool:
        """任务是否仍待用户确认。"""
        return self.status == "pending_confirmation"

    def to_session_ext_info(self) -> dict:
        """序列化为 engine session 的 ``extInfo`` 字段 — 包含完整任务数据供 bot 呈现。"""
        return {
            "task_id": self.task_id,
            "bot_id": self.bot_id,
            "owner_id": self.owner_id,
            "dt": self.dt,
            "project_name": self.project_name,
            "description": self.description,
            "business_scenario": self.business_scenario,
            "discovery_basis": self.discovery_basis,
            "work_item_url": self.work_item_url,
            "priority": self.priority,
            "discovered_at": self.discovered_at,
            "status": self.status,
            "source": "task_discovery",
        }

    def to_notification_message(self, session_url: str = "") -> str:
        """生成通知消息文本（向后兼容旧接口）。"""
        lines = [
            "🔔 发现待执行任务",
            "",
            f"项目名称：{self.project_name}",
            "",
            f"项目简介：{self.description}",
            "",
            f"业务场景：{self.business_scenario}",
            "",
            f"挖掘依据：{self.discovery_basis}",
            "",
            "─" * 40,
            "请确认是否执行此任务。",
        ]
        if session_url:
            lines.extend(["", f"Session 链接: {session_url}"])
        return "\n".join(lines)

    def to_discovery_prompt(self) -> str:
        """生成给 bot 的发现提示消息。

        此消息通过 WebSocket ``chat.send`` 发送到 session，
        bot 收到后主动呈现发现任务并询问用户确认。
        """
        return (
            f"/task 我为您发现了以下可能有意义的事情：\n\n"
            f"【{self.project_name}】\n"
            f"简介：{self.description}\n"
            f"业务场景：{self.business_scenario}\n"
            f"发现依据：{self.discovery_basis}\n\n"
            f"是否确认执行？请在下方回复确认或拒绝。"
        )

    def to_notification_body(self, task_count: int) -> str:
        """生成通知消息体 — 包含发现摘要和确认引导。"""
        return (
            f"我为您发现了 {task_count} 件可能有意义的事情，"
            f"是否确认执行？\n\n"
            f"1. {self.project_name}：{self.description[:50]}...\n"
            f"\n请点击进入会话查看详情并确认。"
        )

    def to_card_data(self) -> dict:
        """生成交互卡片数据（通用抽象，不绑定服务商）。"""
        return {
            "card_name": "为你发现以下任务",
            "workitem_name": self.project_name,
            "workitem_bg": self.description,
        }


@dataclass(frozen=True)
class DiscoverySession:
    """任务发现流程中创建的 engine session 信息。"""

    task_id: str
    session_id: str
    session_url: str
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())


__all__ = ["DiscoveredTask", "DiscoverySession"]