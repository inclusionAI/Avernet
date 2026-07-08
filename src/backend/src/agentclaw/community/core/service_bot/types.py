"""Service Bot types and constants.

共享的类型定义和常量。
"""
from enum import Enum


class PublishStage(str, Enum):
    """发布推进阶段."""
    DRAFT = "draft"    # 初稿阶段
    VERIFY = "verify"    # 验证阶段
    EVAL = "eval"      # 评估阶段
    ONLINE = "online"    # 发布上线阶段


def is_editable_bot(bot_type: str, stage: str) -> bool:
    """Bot 容器内配置是否可编辑。

    唯一规则:只有 service 发布到 online 才锁死(线上不可编辑);
    personal 任何态、service 草稿都可改。set_read_only / can_edit_bot 共用此判定。
    """
    # 服务 bot 仅草稿可编辑;verify/online 都是打包发布的运行态,只读。
    return bot_type != "service" or stage == PublishStage.DRAFT.value
