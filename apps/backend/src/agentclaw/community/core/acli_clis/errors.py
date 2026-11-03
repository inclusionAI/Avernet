"""Domain errors for managing a bot's acli CLIs.

Like every ``core`` error these carry no HTTP status; the adapter maps each
class. Permission, read-only, lock and Passport-sync failures reuse the
caller-identity errors, so the identity switch and this API fail the same way.
"""
from __future__ import annotations

from agentclaw.community.core.errors import (
    Conflict,
    DomainError,
    NotFound,
    ValidationError,
)


class AcliDefaultCliError(Conflict):
    """A default-profile CLI is re-added at every start, so removing it cannot stick."""

    def __init__(self, cli_code: str) -> None:
        super().__init__(
            f"CLI {cli_code!r} is a default for this bot and cannot be removed"
        )


class AcliCliNameMismatchError(ValidationError):
    """The caller passed an alias; Passport codes are acli market names."""

    def __init__(self, requested: str, market_name: str) -> None:
        super().__init__(
            f"{requested!r} is not an acli market name; use {market_name!r}"
        )
        self.market_name = market_name


class AcliEngineUnsupportedError(DomainError):
    """The bot's engine cannot run acli."""

    def __init__(self) -> None:
        super().__init__("this bot's engine does not support acli CLIs")


class AcliContainerError(DomainError):
    """The bot's container refused or failed the acli operation.

    ``code`` is the port's code (``not_running``, ``install_failed``, …) and
    ``detail`` acli's own reason; the subclass says which kind of failure it is.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class AcliBotNotRunningError(AcliContainerError):
    """The bot has no running container to run acli in."""


class AcliEngineOutdatedError(AcliContainerError):
    """The bot's engine type supports acli, but its engine lacks the endpoints.

    An install, uninstall, cancel or listing got a 404: a build that predates
    the endpoints, or an engine serving its own API that does not implement
    them. Nothing ran in the container, and a retry gets the same answer.
    Updating the engine fixes it, so this is a state of the bot's engine, not
    ``AcliEngineUnsupportedError`` (an engine type with no acli at all).
    """


class AcliRequestRejectedError(AcliContainerError):
    """acli rejected the request itself — most often a name the market lacks."""


class AcliOperationFailedError(AcliContainerError):
    """acli or the hop to the container failed for a reason outside the request."""


class AcliGrantSyncError(AcliContainerError):
    """The Passport grant could not be written before the install; nothing was installed."""


class AcliOperationCancelledError(AcliContainerError):
    """The add or remove was cancelled, and the runtime is as it was before it."""


class AcliStageNotAllowedError(ValidationError):
    """A ``stage`` was named for a bot that has only one runtime."""

    def __init__(self) -> None:
        super().__init__("only service bots take a stage; omit it for this bot")


class AcliOperationInProgressError(Conflict):
    """Another add or remove is running in this runtime; try again once it ends.

    ``operation`` is the one in progress (an ``AcliOperation``).
    """

    def __init__(self, operation: object) -> None:
        super().__init__("another CLI operation is in progress for this bot runtime")
        self.operation = operation


class AcliOperationNotActiveError(Conflict):
    """A cancel found no add or remove of that CLI running to cancel."""

    def __init__(self, message: str = "no such CLI operation is running") -> None:
        super().__init__(message)


class AcliOperationNotFoundError(NotFound):
    def __init__(self, operation_id: str) -> None:
        super().__init__(f"CLI operation {operation_id!r} not found")


class AcliMarketNotConfiguredError(DomainError):
    """This deployment has no acli market search URL configured."""

    def __init__(self) -> None:
        super().__init__("the acli market is not configured for this deployment")


class AcliMarketUnavailableError(DomainError):
    """The acli market did not answer, or answered something unusable."""

    def __init__(self) -> None:
        super().__init__("the acli market is unavailable")


__all__ = [
    "AcliBotNotRunningError",
    "AcliCliNameMismatchError",
    "AcliContainerError",
    "AcliDefaultCliError",
    "AcliEngineOutdatedError",
    "AcliEngineUnsupportedError",
    "AcliGrantSyncError",
    "AcliMarketNotConfiguredError",
    "AcliMarketUnavailableError",
    "AcliOperationCancelledError",
    "AcliOperationFailedError",
    "AcliOperationInProgressError",
    "AcliOperationNotActiveError",
    "AcliOperationNotFoundError",
    "AcliRequestRejectedError",
    "AcliStageNotAllowedError",
]
