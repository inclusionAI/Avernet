"""Service contract for independently refreshed Caller runtime credentials."""
from abc import abstractmethod
from typing import Protocol, runtime_checkable


@runtime_checkable
class ScheduledCallerRefreshProtocol(Protocol):
    """Refresh eligible Caller runtimes independently of Owner renewal.

    ``entity_id`` is the stable Bot subject; ``owner_user_id`` is its current
    human owner. Implementations resolve each actual Caller independently and
    must attempt all eligible targets before raising on partial failure. They
    must not expose credential material in raised exceptions. Background consumers
    isolate target failures; manual callers retain errors.
    Owner Passport callbacks do not invoke this contract.
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
