"""TC withdrawal Plugin API v1; ECB wire contract remains rollout-gated draft.

Synchronous bounded publication: a return value is a *durable intake receipt*,
not proof that the business reference is already withdrawn. Call off the loop.
"""

from dataclasses import dataclass
import re
from typing import Protocol, runtime_checkable

from agentclaw.community.plugin_api.base import Plugin


@dataclass(frozen=True)
class ResourceWithdrawalEvent:
    event_id: str
    res_id: str

    @classmethod
    def for_resource(cls, res_id: str) -> "ResourceWithdrawalEvent":
        return cls(event_id=f"tc.resource.withdrawn:{res_id}", res_id=res_id)

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", self.res_id):
            raise ValueError("invalid_resource_id")
        if self.event_id != f"tc.resource.withdrawn:{self.res_id}":
            raise ValueError("event_id_mismatch")

    def as_payload(self) -> dict[str, str]:
        return {"event_id": self.event_id, "res_id": self.res_id}


@dataclass(frozen=True)
class WithdrawalReceipt:
    event_id: str
    status: str

    def __post_init__(self) -> None:
        if self.status not in {"applied", "pending"}:
            raise ValueError("invalid_receipt_status")


class WithdrawalDeliveryError(Exception):
    """Only stable non-sensitive codes may cross this boundary."""

    def __init__(self, code: str, *, retryable: bool) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


@runtime_checkable
class ResourceWithdrawalPublisherPlugin(Plugin, Protocol):
    def publish(self, event: ResourceWithdrawalEvent) -> WithdrawalReceipt: ...
