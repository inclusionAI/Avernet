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

    ``code`` is the port's code (``not_running``, ``install_failed``, …), so
    the adapter can choose a status per cause.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class AcliRollbackFailedError(InternalError):
    """Undoing the first step of an add or remove failed too.

    ``state`` names where the bot was left: ``installed_not_granted`` or
    ``revoked_still_installed``.
    """

    def __init__(self, cli_code: str, state: str) -> None:
        super().__init__(
            f"CLI {cli_code!r} was left {state.replace('_', ' ')}; "
            "the change could not be rolled back"
        )
        self.state = state


__all__ = [
    "AcliCliAlreadyGrantedError",
    "AcliCliNameMismatchError",
    "AcliCliNotGrantedError",
    "AcliContainerError",
    "AcliDefaultCliError",
    "AcliEngineUnsupportedError",
    "AcliRollbackFailedError",
]
