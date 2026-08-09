"""Bot 协作者 Repository 模块。"""
from agentclaw.community.core.repository.protocols.bot import CollaboratorRepositoryProtocol
from agentclaw.community.core.repository.protocols.bot import BotCollabLogRepositoryProtocol
from agentclaw.community.core.repository.protocols.bot import BotCollabLockRepositoryProtocol

__all__ = [
    "CollaboratorRepositoryProtocol",
    "BotCollabLogRepositoryProtocol",
    "BotCollabLockRepositoryProtocol",
]