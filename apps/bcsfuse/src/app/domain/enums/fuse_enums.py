"""
Fuse Enums - 融合相关枚举定义

定义融合模式、执行状态、对话轮次状态等枚举类型。
根据 fusion-storage-design.md 规范，所有状态字段必须使用枚举，
禁止直接使用字符串常量。
"""

from enum import Enum


class FusionMode(str, Enum):
    """
    融合模式枚举

    定义不同的融合操作模式，每种模式有不同的处理逻辑和存储策略。

    Attributes:
        BOT_PROFILE_FUSE: G9 模式 - 多专家 Profile 融合，基于内容哈希去重
        AGENT: G1 模式 - Agent 模式，每次请求独立
        CONFLICT_ALIGNMENT: G2 模式 - 冲突对齐，每次请求独立
        EXPERT_DIAGNOSIS: G5 模式 - 专家诊断，每次请求独立
    """

    BOT_PROFILE_FUSE = "bot_profile_fuse"
    AGENT = "agent"
    CONFLICT_ALIGNMENT = "conflict_alignment"
    EXPERT_DIAGNOSIS = "expert_diagnosis"


class FusionStatus(str, Enum):
    """
    融合执行状态枚举

    定义融合操作的执行状态，用于记录和查询。

    Attributes:
        SUCCESS: 成功完成
        FAILED: 执行失败
        TIMEOUT: 执行超时
        PENDING: 等待执行
        RUNNING: 正在执行
    """

    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    PENDING = "pending"
    RUNNING = "running"


class ConversationTurnStatus(str, Enum):
    """
    对话轮次状态枚举

    定义单个对话轮次的执行状态。

    Attributes:
        PENDING: 等待处理
        PROCESSING: 正在处理
        COMPLETED: 处理完成
        FAILED: 处理失败
    """

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


__all__ = [
    "FusionMode",
    "FusionStatus",
    "ConversationTurnStatus",
]