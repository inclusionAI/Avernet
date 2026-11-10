"""Service contract for Caller credentials refreshed after Owner renewal."""
from abc import abstractmethod
from typing import Protocol, runtime_checkable


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


class NoopScheduledCallerRefresh(ScheduledCallerRefreshProtocol):
    """Community profiles do not hold enterprise scheduled credentials."""

    def refresh(
        self, *, bot_id: str, owner_user_id: str, entity_id: str, env: str,
    ) -> None:
        return None
