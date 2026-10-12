"""
OpenClawAdapter Protocol

M9: OpenClaw Adapter

定义 OpenClaw 适配器的 Protocol 接口。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from src.domain.models.openclaw_adapter_input import OpenClawAdapterInput
from src.domain.models.openclaw_adapter_result import OpenClawAdapterResult


@runtime_checkable
class OpenClawAdapter(Protocol):
    """
    OpenClaw 适配器 Protocol

    将 ExecutionPacket 转换为 OpenClaw 兼容的 HandoffBundle。

    职责：
    - 读取 ExecutionPacket 中的快照和包
    - 生成 OpenClaw 工作区所需的文件 (TASK.md, TEAM.md, 等)
    - 生成 manifest 文件，包含 task_id、skills_enabled 等
    - 输出技能白名单

    约束：
    - 不修改 ExecutionPacket（只读操作）
    - 不包含 M10 集成逻辑
    - 技能白名单从 skill_pack.allowlist 获取，不默认全开
    """

    def adapt(self, input_data: OpenClawAdapterInput) -> OpenClawAdapterResult:
        """
        执行适配

        Args:
            input_data: 适配器输入，包含 ExecutionPacket 和选项

        Returns:
            OpenClawAdapterResult: 适配结果，包含 HandoffBundle、警告、错误和解释
        """
        ...


__all__ = [
    "OpenClawAdapter",
]
