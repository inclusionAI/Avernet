"""Space-scoped Bot quota value objects and errors."""

from __future__ import annotations

from dataclasses import dataclass

from agentclaw.community.core.spaces.models import SpaceType


@dataclass(frozen=True, slots=True)
class BotQuotaScope:
    """The logical Space whose capacity one Bot creation consumes."""

    owner_id: str
    space_id: int | None
    space_name: str
    space_type: SpaceType

    @property
    def space_ref(self) -> str:
        if self.space_id is None:
            return f"personal:{self.owner_id}"
        return str(self.space_id)

    @property
    def lock_scope(self) -> str:
        if self.space_type is SpaceType.PERSONAL:
            return f"personal:{self.owner_id}"
        return f"team:{self.space_id}"


@dataclass(frozen=True, slots=True)
class BotQuotaSnapshot:
    scope: BotQuotaScope
    ceiling: int
    used: int


class BotQuotaError(Exception):
    """Base error for Space-scoped Bot quota operations."""


class BotQuotaConfigurationError(BotQuotaError):
    """The requested quota scope or override is invalid."""


class BotQuotaUnavailableError(BotQuotaError):
    """Quota storage or serialization infrastructure is unavailable."""


class BotQuotaBusyError(BotQuotaError):
    """Another mutation currently owns the quota scope lock."""


class BotQuotaExceededError(BotQuotaError):
    """Adding the requested Bots would exceed the target Space ceiling."""

    def __init__(self, snapshot: BotQuotaSnapshot) -> None:
        self.snapshot = snapshot
        super().__init__(
            f"Space {snapshot.scope.space_ref} Bot quota exceeded: "
            f"used={snapshot.used}, ceiling={snapshot.ceiling}"
        )

    def as_payload(self) -> dict[str, object]:
        scope = self.snapshot.scope
        return {
            "space_id": scope.space_ref,
            "space_name": scope.space_name,
            "space_type": scope.space_type.value,
            "ceiling": self.snapshot.ceiling,
            "used": self.snapshot.used,
        }
