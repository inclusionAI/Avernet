"""Bot Service Bot module."""

from agentclaw.community.core.service_bot.services.bot_publish_service import (
    BotPublishService,
    BotPublishServiceError,
    BotNotFoundError,
    BotNotServiceTypeError,
    PublishAlreadyExistsError,
    PublishNotFoundError,
    PublishStatusInvalidError,
)
from agentclaw.community.core.service_bot.services.baas_service import (
    BaasServiceError,
    BotWsConnectionInfoResponse,
)


def __getattr__(name: str):  # pragma: no cover
    """Back-compat shim: ``from agentclaw.community.core.service_bot.services import BaasService``
    still resolves, but is deferred so it doesn't run during package init
    (which would close the cycle plugins/prod → core → services.__init__).

    Coverage note: PEP 562 lazy-import shim — only triggered by callers using
    the legacy attribute-access form. No current callers exercise this path
    in CI (all imports use the direct module path); kept for back-compat.
    """
    if name == "BaasService":
        from agentclaw.community.core.service_bot.services.baas_service import BaasService

        return BaasService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
from agentclaw.community.core.service_bot.services.bot_build_service import (
    BotBuildService,
    BotBuildServiceError,
    BotSourceNotFoundError,
    BotBuildMigrationError,
)
from agentclaw.community.core.service_bot.services.publish_flow_service import (
    PublishFlowService,
    PublishFlowServiceError,
)

__all__ = [
    "BotPublishService",
    "BotPublishServiceError",
    "BotNotFoundError",
    "BotNotServiceTypeError",
    "PublishAlreadyExistsError",
    "PublishNotFoundError",
    "PublishStatusInvalidError",
    "BaasService",
    "BaasServiceError",
    "BotWsConnectionInfoResponse",
    "BotBuildService",
    "BotBuildServiceError",
    "BotSourceNotFoundError",
    "BotBuildMigrationError",
    "PublishFlowService",
    "PublishFlowServiceError",
]
