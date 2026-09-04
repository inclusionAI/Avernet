"""Repository contracts owned by the ``bot`` domain.

Split into submodules because the domain's contracts exceed the Rule 9
1000-line file cap when kept in one module. Importers use
``core.repository.protocols.bot`` either way — this package re-exports every
contract, so the split is a file-layout detail, not an API change.
"""
from __future__ import annotations

from agentclaw.community.core.repository.protocols.bot.app_grant import (
    BotAppGrantRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.bot.bot import (
    BotRepository,
    BotRestartLockRepositoryProtocol,
    TemplateRepository,
    RenderScreenRepository,
)
from agentclaw.community.core.repository.protocols.bot.config_manifest import (
    BotConfigManifestRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.bot.config_manifest_apply import (
    BotConfigManifestApplyLockRepositoryProtocol,
    BotConfigManifestApplyRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.bot.friend import (
    BotFriendRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.bot.manifest_content import (
    DEFAULT_RECORD_LIMIT,
    ManifestContentRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.bot.collaborator import (
    CollaboratorRepositoryProtocol,
    BotCollabLogRepositoryProtocol,
    BotCollabLockRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.bot.mcp import (
    UserMCPConfigRepository,
)
from agentclaw.community.core.repository.protocols.bot.cli_tool import (
    BotCliToolRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.bot.startup_script import (
    BotStartupScriptRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.bot.source_credential import (
    SourceCredentialRepositoryProtocol,
)

__all__ = [
    "BotAppGrantRepositoryProtocol",
    "BotConfigManifestApplyLockRepositoryProtocol",
    "BotConfigManifestApplyRepositoryProtocol",
    "BotCollabLockRepositoryProtocol",
    "BotCollabLogRepositoryProtocol",
    "BotConfigManifestRepositoryProtocol",
    "BotFriendRepositoryProtocol",
    "BotRepository",
    "BotRestartLockRepositoryProtocol",
    "BotCliToolRepositoryProtocol",
    "BotStartupScriptRepositoryProtocol",
    "CollaboratorRepositoryProtocol",
    "DEFAULT_RECORD_LIMIT",
    "ManifestContentRepositoryProtocol",
    "RenderScreenRepository",
    "SourceCredentialRepositoryProtocol",
    "TemplateRepository",
    "UserMCPConfigRepository",
]
