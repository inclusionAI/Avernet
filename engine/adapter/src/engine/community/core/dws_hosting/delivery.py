"""Explicit one-step outbox worker; no startup task and no engine execution."""

import asyncio
from collections.abc import Callable
from engine.community.core.dws_hosting.storage import durable_call
from engine.community.core.dws_hosting.owner_activity import HostingOwnerActivity
from engine.community.kernel.dws_connection import DwsError

from engine.community.kernel.dws_delivery import (
    DeliveryReceipt,
    DeliveryRecord,
    PreflightError,
)
from engine.community.plugin_api.dws_delivery import HostingOutbox, HostingSender


class HostingDeliveryWorker:
    def __init__(
        self,
        outbox: HostingOutbox,
        sender: HostingSender,
        clock: Callable[[], int],
        *,
        timeout_ms: int = 20000,
        owner_activity: HostingOwnerActivity | None = None,
        rollout_check=None,
    ):
        if type(timeout_ms) is not int or timeout_ms <= 0:
            raise ValueError("positive operation timeout required")
        self.outbox, self.sender, self.clock, self.timeout_ms = (
            outbox,
            sender,
            clock,
            timeout_ms,
        )
        self.owner_activity = owner_activity
        self.rollout_check = rollout_check

    async def run_once(self, binding_id: str) -> DeliveryRecord | None:
        if self.rollout_check is not None and not await self.rollout_check():
            return None
        claim = await durable_call(self.outbox.claim, binding_id, now_ms=self.clock())
        if claim is None:
            return None
        if claim.state == "checking":
            await self._external(claim, inspect=True)
        else:
            try:
                async with asyncio.timeout(self._timeout(claim)):
                    await self.sender.preflight(claim.delivery)
                    if (
                        self.owner_activity is not None
                        and await self.owner_activity.superseded(
                            binding_id, claim.delivery.turn_id
                        )
                    ):
                        await durable_call(self.outbox.skip, claim, now_ms=self.clock())
                        return await durable_call(
                            self.outbox.get, binding_id, claim.delivery.delivery_id
                        )
            except asyncio.CancelledError:
                await durable_call(
                    self.outbox.preflight_failed,
                    claim,
                    now_ms=self.clock(),
                    retryable=True,
                )
                raise
            except (PreflightError, DwsError, TimeoutError) as exc:
                await durable_call(
                    self.outbox.preflight_failed,
                    claim,
                    now_ms=self.clock(),
                    retryable=isinstance(exc, (DwsError, TimeoutError))
                    or exc.retryable,
                )
            except Exception:
                await durable_call(
                    self.outbox.preflight_failed,
                    claim,
                    now_ms=self.clock(),
                    retryable=False,
                )
                raise
            else:
                if self.rollout_check is not None and not await self.rollout_check():
                    return None
                started = await durable_call(
                    self.outbox.start_send, claim, now_ms=self.clock()
                )
                if started is not None:
                    await self._external(started, inspect=False)
        return await durable_call(
            self.outbox.get, binding_id, claim.delivery.delivery_id
        )

    def _timeout(self, claim: DeliveryRecord) -> float:
        return max(0, min(self.timeout_ms, claim.lease_until_ms - self.clock())) / 1000

    async def _external(self, claim: DeliveryRecord, *, inspect: bool):
        try:
            async with asyncio.timeout(self._timeout(claim)):
                if inspect:
                    receipt = await self.sender.inspect(claim.delivery, claim.task_id)
                else:
                    receipt = await self.sender.send(claim.delivery)
        except asyncio.CancelledError:
            await durable_call(
                self.outbox.complete,
                claim,
                DeliveryReceipt(status="unknown"),
                now_ms=self.clock(),
            )
            raise
        except Exception:
            # A failed subprocess/parse/timeout is not proof that a write did not happen.
            receipt = DeliveryReceipt(status="unknown")
        await durable_call(self.outbox.complete, claim, receipt, now_ms=self.clock())
