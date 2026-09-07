"""HTTP mappings for public Bot-create failures from both contract generations."""

from agentclaw.community.adapters.http.openapi_v1.errors import (
    ApplicationCodingUnavailableError,
    BotCombinationUnsupportedError,
    BotTemplateInvalidError,
)
from agentclaw.community.core.bot_management.errors import (
    ApplicationCodingUnavailableError as CoreApplicationCodingUnavailableError,
    BotCombinationUnsupportedError as CoreBotCombinationUnsupportedError,
    BotTemplateInvalidError as CoreBotTemplateInvalidError,
    ServiceIntakeConversionError,
)

BOT_CREATE_HTTP_ERRORS = {
    BotTemplateInvalidError: (422, "Invalid coding template"),
    CoreBotTemplateInvalidError: (422, "Invalid coding template"),
    BotCombinationUnsupportedError: (409, "Coding template combination not supported"),
    CoreBotCombinationUnsupportedError: (409, "Coding template combination not supported"),
    ApplicationCodingUnavailableError: (503, "Application coding is unavailable in this deployment"),
    CoreApplicationCodingUnavailableError: (503, "Application coding is unavailable in this deployment"),
    # The create half of a create-as-service request succeeded — the bot
    # exists as personal — and only the upgrade failed. Downstream dependency
    # failure, hence 502; the message points at the recovery (retry the
    # lifecycle upgrade) instead of re-creating the bot.
    ServiceIntakeConversionError: (
        502,
        "Bot created as personal, but converting it to service failed; "
        "retry the lifecycle upgrade",
    ),
}
