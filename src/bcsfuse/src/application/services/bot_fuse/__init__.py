"""
Bot Fuse 服务

Profile Fusion 模式相关服务。

G9 三次模型调用分布：
1. GroupContextService - 会话总结（改写问题+摘要）
2. ProfileMergeService - Profile 融合
3. FusionExpertChatService - Prompt构建 + LLM调用 + 结果构建

存储集成：
- ProfileMergeService 和 FusionExpertChatService 共享 FusedProfileStorageService
- FusedProfileStorageService 包含 L1 内存缓存 + L2 持久化
"""

from src.application.services.bot_fuse.profile_merge_service import ProfileMergeService
from src.application.services.bot_fuse.group_context_service import (
    GroupContextService,
    GroupMessage,
)
from src.application.services.bot_fuse.fusion_expert_chat_service import FusionExpertChatService

__all__ = [
    "ProfileMergeService",
    "GroupContextService",
    "GroupMessage",
    "FusionExpertChatService",
]