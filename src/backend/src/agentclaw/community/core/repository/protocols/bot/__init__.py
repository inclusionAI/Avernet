"""Repository contracts owned by the ``bot`` domain.

Split into submodules because the domain's contracts exceed the Rule 9
1000-line file cap when kept in one module. Importers use
``core.repository.protocols.bot`` either way — this package re-exports every
contract, so the split is a file-layout detail, not an API change.
"""
from __future__ import annotations

from agentclaw.community.core.repository.protocols.bot.bot import (
    BotRepository,
    BotRestartLockRepositoryProtocol,
    TemplateRepository,
    RenderScreenRepository,
)
from agentclaw.community.core.repository.protocols.bot.friend import (
    BotFriendRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.bot.collaborator import (
    CollaboratorRepositoryProtocol,
    BotCollabLogRepositoryProtocol,
    BotCollabLockRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.bot.mcp import (
    UserMCPConfigRepository,
)

__all__ = [
    "BotCollabLockRepositoryProtocol",
    "BotCollabLogRepositoryProtocol",
    "BotFriendRepositoryProtocol",
    "BotRepository",
    "BotRestartLockRepositoryProtocol",
    "CollaboratorRepositoryProtocol",
    "RenderScreenRepository",
    "TemplateRepository",
    "UserMCPConfigRepository",
]
