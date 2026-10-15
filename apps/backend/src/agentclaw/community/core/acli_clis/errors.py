"""Domain errors for managing a bot's acli CLIs.

Like every ``core`` error these carry no HTTP status; the adapter maps each
class. Permission, read-only, lock and Passport-sync failures reuse the
caller-identity errors, so the identity switch and this API fail the same way.
"""
from __future__ import annotations

from agentclaw.community.core.errors import (
    Conflict,
    DomainError,
    InternalError,
    NotFound,
    ValidationError,
)


class AcliCliAlreadyGrantedError(Conflict):
    def __init__(self, cli_code: str) -> None:
        super().__init__(f"CLI {cli_code!r} is already granted to this bot")


class AcliCliNotGrantedError(NotFound):
    def __init__(self, cli_code: str) -> None:
        super().__init__(f"CLI {cli_code!r} is not granted to this bot")


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


class AcliRequestRejectedError(AcliContainerError):
    """acli rejected the request itself — most often a name the market lacks."""


class AcliOperationFailedError(AcliContainerError):
    """acli or the hop to the container failed for a reason outside the request."""


class AcliGrantSyncError(DomainError):
    """The Passport grant could not be written; nothing was left changed."""

    def __init__(self) -> None:
        super().__init__("the CLI grant could not be synchronized")


class AcliRollbackFailedError(InternalError):
    """Undoing the first step of an add or remove failed too.

    ``state`` names where the bot was left: ``installed_not_granted``,
    ``revoked_still_installed``, ``revoked_install_unknown`` (revoked, and
    the container's answer to the uninstall never arrived), or
    ``install_unknown_not_granted`` (never granted; the install's answer never
    arrived and the cleanup uninstall failed too).
    """

    def __init__(self, cli_code: str, state: str) -> None:
        super().__init__(
            f"CLI {cli_code!r} was left {state.replace('_', ' ')}; "
            "the change could not be rolled back"
        )
        self.state = state


__all__ = [
    "AcliBotNotRunningError",
    "AcliCliAlreadyGrantedError",
    "AcliCliNameMismatchError",
    "AcliCliNotGrantedError",
    "AcliContainerError",
    "AcliDefaultCliError",
    "AcliEngineUnsupportedError",
    "AcliGrantSyncError",
    "AcliOperationFailedError",
    "AcliRequestRejectedError",
    "AcliRollbackFailedError",
]
