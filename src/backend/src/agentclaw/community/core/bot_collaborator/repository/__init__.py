"""Bot 协作者 Repository 模块。"""
from agentclaw.community.core.bot_collaborator.repository.protocol import (
    CollaboratorRepositoryProtocol,
    BotCollabLogRepositoryProtocol,
    BotCollabLockRepositoryProtocol,
)

__all__ = [
    "CollaboratorRepositoryProtocol",
    "BotCollabLogRepositoryProtocol",
    "BotCollabLockRepositoryProtocol",
]