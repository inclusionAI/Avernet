"""Delivery Plugin APIs v1. No HTTP route or engine-specific policy."""

from typing import Protocol

from engine.community.kernel.dws_delivery import (
    Delivery,
    DeliveryReceipt,
    DeliveryRecord,
)


class HostingSender(Protocol):
    async def preflight(self, delivery: Delivery) -> None:
        """Read-only authorization/identity check. May raise PreflightError."""
        ...

    async def send(self, delivery: Delivery) -> DeliveryReceipt:
        """Exactly one write attempt; use delivery_id as a key if supported remotely.

        Never retry here. Any exception may mean the remote write happened.
        An accepted receipt may declare queryable=False when no query exists;
        only owner notices may settle at provider acceptance, never peer replies.
        """
        ...

    async def inspect(self, delivery: Delivery, task_id: str) -> DeliveryReceipt:
        """Read-only query of the exact task. No fallback to another message or resend."""
        ...


class HostingCommand(Protocol):
    async def capture(self, args: tuple[str, ...]) -> str:
        """Bounded argv subprocess, no shell, raw logs or retries; safe errors only."""
        ...


class HostingOutbox(Protocol):
    def enqueue(
        self, binding_id: str, turn_id: str, *, now_ms: int, label_suffix: str = ""
    ) -> DeliveryRecord | None:
        """Persist immutable content from a resolved decision/manual task; idempotent."""
        ...

    def get(self, binding_id: str, delivery_id: str) -> DeliveryRecord: ...

    def sent_message_ids(self, binding_id: str, conversation_id: str) -> frozenset[str]:
        """Confirmed automatic peer deliveries, excluded from human activity checks."""
        ...

    def skip(self, claim: DeliveryRecord, *, now_ms: int) -> None:
        """Silently cancel a still-claimed unsent reply/notice superseded by its owner."""
        ...

    def claim(self, binding_id: str, *, now_ms: int) -> DeliveryRecord | None:
        """Lease queued sends or read-only polls. Expired writes are never retried."""
        ...

    def start_send(
        self, claim: DeliveryRecord, *, now_ms: int
    ) -> DeliveryRecord | None:
        """Last transactional epoch/intake gate before I/O; revocation after it is in-flight."""
        ...

    def preflight_failed(
        self, claim: DeliveryRecord, *, now_ms: int, retryable: bool
    ) -> None:
        """Only a still-claimed, never-dispatched attempt can be retried."""
        ...

    def complete(
        self, claim: DeliveryRecord, receipt: DeliveryReceipt, *, now_ms: int
    ) -> None:
        """Fence stale workers; persist remote evidence and release only known outcomes."""
        ...
