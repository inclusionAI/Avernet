"""
BotCognitionProvider Protocol

Bot 知识认知接口，提供 Bot 的认知信息查询能力。

这是一个中性的领域接口，不绑定任何具体实现（ECB、知识图谱等）。
具体实现由 Infrastructure 层提供。

职责：
- 获取 Bot 的认知摘要（用于 Profile 能力分析增强）
- 获取 Bot 的基本信息（名称、描述等）

约束：
- 调用方不感知底层实现细节（HTTP、数据库、缓存等）
- 返回 None 表示查询无结果或服务不可用
- 不抛出异常，失败时返回 None
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable


@dataclass(frozen=True)
class BotCognition:
    """
    Bot 认知信息

    包含 Bot 的核心认知数据，用于增强 Profile 分析。

    Attributes:
        bot_id: Bot 标识
        name: Bot 名称
        summary: Bot 认知摘要（核心能力描述）
        status: Bot 状态（active/inactive 等）
        owner_id: Bot 负责人工号
    """

    bot_id: str
    name: Optional[str] = None
    summary: Optional[str] = None
    status: Optional[str] = None
    owner_id: Optional[str] = None


@runtime_checkable
class BotCognitionProvider(Protocol):
    """
    Bot 认知查询 Protocol

    提供 Bot 认知信息的查询接口。这是一个中性的领域接口，
    具体实现可以来自：
    - ECB（Enterprise Context Broker）
    - 知识图谱
    - 向量数据库
    - 其他认知服务

    职责：
    - 查询 Bot 认知信息，获取摘要等数据
    - 封装底层实现细节

    约束：
    - 网络异常或服务不可用时返回 None，不抛出异常
    - 查询无结果时返回 None
    """

    def get_bot_cognition(self, bot_id: str) -> Optional[BotCognition]:
        """
        查询 Bot 认知信息

        Args:
            bot_id: Bot 标识

        Returns:
            BotCognition: 认知信息，失败或无数据时返回 None
        """
        ...


__all__ = [
    "BotCognitionProvider",
    "BotCognition",
]