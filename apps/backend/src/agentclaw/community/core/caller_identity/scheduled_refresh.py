"""Service contract for Caller credentials refreshed after Owner renewal."""
from abc import abstractmethod
from typing import Protocol, runtime_checkable

from .credential import CALLER_CREDENTIAL_PROVIDER_UNAVAILABLE, CallerCredentialError


@runtime_checkable
class ScheduledCallerRefreshProtocol(Protocol):
    """Refresh eligible Caller runtimes after their Owner token is installed.

    ``entity_id`` is the stable Bot subject; ``owner_user_id`` is its current
    human owner. Implementations resolve each actual Caller independently and
    must attempt all eligible targets before raising on partial failure. They
    must not expose credential material in raised exceptions. The Owner callback
    logs these failures without changing its successful Owner update result.
    """

    @abstractmethod
    def refresh(
        self, *, bot_id: str, owner_user_id: str, entity_id: str, env: str,
    ) -> None: ...

    @abstractmethod
    def refresh_for_actor(
        self, *, bot_id: str, actor_id: str, entity_id: str | None = None,
    ) -> dict:
        """Authorize the current owner and refresh eligible stored-ASC runtimes.

        Derive owner, environment, Caller and sandbox from trusted records. Return
        bot_id, succeeded/skipped/failed counts and credential-free per-binding
        results (binding_id, caller_workno, status, reason, expires_at). Partial
        target failures are results; authorization/unsupported failures raise.
        """
        ...


class NoopScheduledCallerRefresh(ScheduledCallerRefreshProtocol):
    """Community profiles do not hold enterprise scheduled credentials."""

    def refresh(
        self, *, bot_id: str, owner_user_id: str, entity_id: str, env: str,
    ) -> None:
        return None

    def refresh_for_actor(
        self, *, bot_id: str, actor_id: str, entity_id: str | None = None,
    ) -> dict:
        raise CallerCredentialError(CALLER_CREDENTIAL_PROVIDER_UNAVAILABLE)
