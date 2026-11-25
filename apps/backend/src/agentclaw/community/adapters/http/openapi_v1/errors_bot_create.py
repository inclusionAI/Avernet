"""HTTP mappings for public Bot-create failures from both contract generations."""

from agentclaw.community.adapters.http.openapi_v1.errors import (
    ApplicationCodingUnavailableError,
    BotCombinationUnsupportedError,
    BotTemplateInvalidError,
    IdentitySourceInvalidError,
)
from agentclaw.community.core.bot_management.errors import (
    ApplicationCodingUnavailableError as CoreApplicationCodingUnavailableError,
    BotCombinationUnsupportedError as CoreBotCombinationUnsupportedError,
    BotTemplateInvalidError as CoreBotTemplateInvalidError,
    IdentitySourceInvalidError as CoreIdentitySourceInvalidError,
)

BOT_CREATE_HTTP_ERRORS = {
    BotTemplateInvalidError: (422, "Invalid coding template"),
    CoreBotTemplateInvalidError: (422, "Invalid coding template"),
    BotCombinationUnsupportedError: (409, "Coding template combination not supported"),
    CoreBotCombinationUnsupportedError: (409, "Coding template combination not supported"),
    ApplicationCodingUnavailableError: (503, "Application coding is unavailable in this deployment"),
    CoreApplicationCodingUnavailableError: (503, "Application coding is unavailable in this deployment"),
    IdentitySourceInvalidError: (422, "Invalid identity source"),
    CoreIdentitySourceInvalidError: (422, "Invalid identity source"),
}
