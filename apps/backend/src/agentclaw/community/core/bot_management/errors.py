"""Domain errors owned by the bot-management context."""


class CreateBotForOthersError(Exception):
    """Stable create-for-others failure that the delivery adapter can serialize."""

    def __init__(self, message: str, *, error_code: int) -> None:
        super().__init__(message)
        self.error_code = error_code


class DefaultBotPassportRepairError(Exception):
    """Stable repair failure that the delivery adapter can serialize."""

    def __init__(self, message: str, *, error_code: int) -> None:
        super().__init__(message)
        self.error_code = error_code


class BotLookupAmbiguousError(RuntimeError):
    """A caller-specific Bot lookup matched more than one live row."""


class BotCreateError(Exception):
    """Base error for Bot creation policy failures."""


class BotTemplateInvalidError(BotCreateError):
    """The supplied Bot template attributes are malformed or unsupported."""


class BotCombinationUnsupportedError(BotCreateError):
    """The requested Bot/template combination cannot be created."""


class ApplicationCodingUnavailableError(BotCreateError):
    """Application Coding requires a Workspace Hosting capability."""
