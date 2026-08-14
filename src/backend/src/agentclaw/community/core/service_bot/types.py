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


class OnlineDeployDecision(str, Enum):
    """How an online deploy should treat the record's current/previous bot.

    Chosen by ``_decide_online_deploy`` from the candidate bot's live BaaS status
    and the bot's container provider, and used identically by every online deploy
    seam (release, retry, re-publish, restart).
    """
    # Reuse the existing bot in place (BaaS UPDATE / upgrade) — no new bot.
    UPGRADE = "upgrade"
    # The existing bot cannot be reused (e.g. a teclaw container the UPDATE cannot
    # rebuild): retire it, then create a fresh bot — so nothing is orphaned.
    RETIRE_THEN_FIRST_RELEASE = "retire_then_first_release"
    # No reusable bot exists (already gone / none bound): just create a new one.
    FIRST_RELEASE = "first_release"


def is_editable_bot(bot_type: str, stage: str) -> bool:
    """Bot 容器内配置是否可编辑。

    唯一规则:只有 service 发布到 online 才锁死(线上不可编辑);
    personal 任何态、service 草稿都可改。set_read_only / can_edit_bot 共用此判定。
    """
    # 服务 bot 仅草稿可编辑;verify/online 都是打包发布的运行态,只读。
    return bot_type != "service" or stage == PublishStage.DRAFT.value
