"""One-shot DWS writes and read-only status queries. No retries or engine calls."""

from engine.community.kernel.dws_connection import DwsError
from engine.community.kernel.dws_delivery import (
    Delivery,
    DeliveryReceipt,
    PreflightError,
)
from engine.community.kernel.dws_hosting import Binding
from engine.community.plugins.dws_hosting import delivery_wire, wire
from engine.community.plugins.dws_hosting.client import DwsCliConnection


class DwsCliSender:
    def __init__(self, connection: DwsCliConnection, binding: Binding):
        if (binding.organization_id, binding.account_id) != (
            connection.identity.organization_id,
            connection.identity.user_id,
        ):
            raise ValueError("sender binding identity mismatch")
        self.connection, self.binding = connection, binding

    def _scope(self, delivery: Delivery):
        # COSEC: exact trusted Bot/account binding; never accept model-selected recipients.
        if delivery.binding != self.binding:
            raise PreflightError("sender_identity_mismatch")
        if delivery.purpose != "reply":
            raise PreflightError("owner_notifier_required")

    async def preflight(self, delivery: Delivery) -> None:
        self._scope(delivery)
        try:
            status = await self.connection.authorization_status()
        except DwsError as exc:
            raise PreflightError(
                "authorization_unavailable",
                retryable=str(exc)
                in {"command_timeout", "command_failed", "process_failed"},
            ) from None
        if not status.authenticated:
            raise PreflightError("authorization_required")

    def _profile(self):
        identity = self.connection.identity
        return ("--profile", f"{identity.organization_id}:{identity.user_id}")

    async def send(self, delivery: Delivery) -> DeliveryReceipt:
        self._scope(delivery)
        # Explicit source conversation only, no name lookup or owner-notice fallback.
        raw = await self.connection.process.capture(
            (
                "chat",
                "+messages-send",
                "--as=user",
                f"--chat-id={delivery.conversation_id}",
                "--msg-type=text",
                f"--text={delivery.text}",
                f"--idempotency-key={delivery.delivery_id}",
                "--ai-tag=true",
                "-y",
                "-f",
                "json",
                *self._profile(),
            )
        )
        return delivery_wire.receipt(raw)

    async def inspect(self, delivery: Delivery, task_id: str) -> DeliveryReceipt:
        self._scope(delivery)
        wire.identifier(task_id)
        raw = await self.connection.process.capture(
            (
                "chat",
                "+messages-query-send-status",
                f"--open-task-id={task_id}",
                "-f",
                "json",
                *self._profile(),
            )
        )
        return delivery_wire.receipt(raw, task_id=task_id)
