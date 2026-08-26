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
        title:              任务标题（对齐 TaskSpec.metadata.title；同时用作 engine session title）。
        instruction:        核心执行指令（对齐 TaskSpec.metadata.instruction）。
        background:         背景信息（对齐 TaskSpec.context.background）。
        discovery_basis:    挖掘依据 — 行为节点演进链路，说明为何发现此任务。
        priority:           优先级 (high / medium / low)。
        discovered_at:      被发现的时间戳。
        status:             当前状态：
            - ``pending_confirmation`` — 待用户确认
            - ``confirmed``           — 用户已确认，待执行
            - ``executing``           — 已提交 engine 执行
            - ``ignored``             — 用户忽略
        objective:          任务目标（对应 TaskSpec.goal.objective）；缺省回退 title。
        acceptances:        验收标准列表（每条 ``{"id","description"}``，对应
                            TaskSpec.goal.acceptances）；留位/空时由确认阶段 bot 澄清补全。
    """

    task_id: str
    bot_id: str
    owner_id: str
    dt: str
    title: str
    instruction: str
    background: str
    discovery_basis: str
    priority: str = "medium"
    discovered_at: Optional[str] = None
    status: str = "pending_confirmation"
    # 执行层衔接字段（不落 discovered_tasks.db；确认后用于 to_task_info_request）
    objective: str = ""
    acceptances: list[dict] = field(default_factory=list)

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
            "project_name": self.title,
            "description": self.instruction,
            "business_scenario": self.background,
            "discovery_basis": self.discovery_basis,
            "priority": self.priority,
            "discovered_at": self.discovered_at,
            "status": self.status,
            "objective": self.objective,
            "acceptances": list(self.acceptances),
            "source": "task_discovery",
        }

    def to_notification_message(self, session_url: str = "") -> str:
        """生成通知消息文本（向后兼容旧接口）。"""
        lines = [
            "🔔 发现待执行任务",
            "",
            f"项目名称：{self.title}",
            "",
            f"项目简介：{self.instruction}",
            "",
            f"业务场景：{self.background}",
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
        """生成给 bot 的发现提示消息（单任务版，按 4 维度组织）。

        维度对齐执行层 TaskSpec：
          目标 ← objective（缺省回退 title）
          预期交付物 ← instruction
          验收标准 ← acceptances（为空则提示补充）
          约束 ← background
        """
        lines = [
            f"/task 我为您发现了以下可能有意义的事情：\n",
            f"【{self.title}】",
            f"目标：{self.objective or self.title}",
            f"预期交付物：{self.instruction}",
        ]
        if self.acceptances:
            lines.append("验收标准：")
            for a in self.acceptances:
                lines.append(f"  - [{a.get('id', '')}] {a.get('description', '')}")
        else:
            lines.append("验收标准：（确认时可由你补充）")
        lines.append(f"约束：{self.background}")
        lines.append("\n是否确认执行？请在下方回复确认或拒绝。")
        return "\n".join(lines)

    def to_notification_body(self, task_count: int) -> str:
        """生成通知消息体 — 包含发现摘要和确认引导。"""
        return (
            f"我为您发现了 {task_count} 件可能有意义的事情，"
            f"是否确认执行？\n\n"
            f"1. {self.title}：{self.instruction[:50]}...\n"
            f"\n请点击进入会话查看详情并确认。"
        )

    def to_card_data(self) -> dict:
        """生成交互卡片数据（通用抽象，不绑定服务商）。"""
        return {
            "card_name": "为你发现以下任务",
            "workitem_name": self.title,
            "workitem_bg": self.instruction,
        }


@dataclass(frozen=True)
class DiscoverySession:
    """任务发现流程中创建的 engine session 信息。"""

    task_id: str
    session_id: str
    session_url: str
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())


__all__ = ["DiscoveredTask", "DiscoverySession"]