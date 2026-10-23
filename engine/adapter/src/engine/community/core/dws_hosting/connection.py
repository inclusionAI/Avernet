"""Owner-authorized login and restart-safe intake, with no engine or send side effects."""

from __future__ import annotations

import asyncio
import logging
from hashlib import sha256
from collections.abc import Awaitable, Callable
from contextlib import aclosing
from engine.community.core.dws_hosting.storage import durable_call

from engine.community.kernel.dws_connection import (
    AuthorizationChallenge,
    AuthorizationStatus,
    DwsError,
    IntakePolicy,
    ListenerNotice,
)
from engine.community.kernel.dws_hosting import Binding, DirectMessage
from engine.community.plugin_api.dws_connection import (
    DwsConnection,
    HostingRecoveryStore,
)
from engine.community.plugin_api.dws_hosting import HostingStateStore


def verify_account(binding: Binding, status: AuthorizationStatus) -> None:
    if not status.authenticated:
        raise DwsError("authorization_required")
    if (status.organization_id, status.user_id) != (
        binding.organization_id,
        binding.account_id,
    ):
        raise DwsError("identity_mismatch")


class HostingAuthorization:
    """Caller identity must come from the authenticated owner, never request data.

    The future management route must also verify this is a personal Bot.
    Authorization creates only a disabled binding, not consent to automatic replies.
    """

    def __init__(
        self, binding: Binding, connection: DwsConnection, state: HostingStateStore
    ):
        self.binding, self.connection, self.state = binding, connection, state

    def _owner(self, actor_id: str) -> None:
        # COSEC: possession of a Bot or account ID does not authorize credential changes.
        if actor_id != self.binding.owner_id:
            raise PermissionError("owner required")

    async def status(self, actor_id: str) -> AuthorizationStatus:
        self._owner(actor_id)
        async with self.connection.exclusive():
            status = await self.connection.authorization_status()
            if status.authenticated:
                verify_account(self.binding, status)
            return status

    async def authorize(
        self,
        actor_id: str,
        on_challenge: Callable[[AuthorizationChallenge], Awaitable[None]],
    ) -> AuthorizationStatus:
        self._owner(actor_id)
        async with self.connection.exclusive():
            status = await self.connection.authorize(on_challenge)
            verify_account(self.binding, status)
            await durable_call(self.state.bind, self.binding)
            return status


class DwsIntake:
    """Explicitly started by a future composition root, never at module import.

    Only known transient transport failures are retried. Invalid wire data,
    wrong accounts and storage faults stop intake with its claim barrier closed.
    """

    RETRYABLE = frozenset(
        {
            "command_failed",
            "command_timeout",
            "listener_ended",
            "listener_not_ready",
            "disconnected",
            "connection_changed",
            "incomplete_result",
            "incomplete_history",
            "incomplete_discovery",
        }
    )
    HISTORY_RETRYABLE = frozenset(
        {
            "command_failed",
            "command_timeout",
            "incomplete_result",
            "incomplete_history",
            "incomplete_discovery",
        }
    )

    def __init__(
        self,
        binding: Binding,
        connection: DwsConnection,
        state: HostingStateStore,
        recovery: HostingRecoveryStore,
        *,
        clock_ms: Callable[[], int],
        policy: IntakePolicy = IntakePolicy(),
    ) -> None:
        self.binding, self.connection, self.state, self.recovery = (
            binding,
            connection,
            state,
            recovery,
        )
        self.clock_ms, self.policy = clock_ms, policy
        self._wake = asyncio.Event()
        self._ready = asyncio.Event()
        self._recovered = False

    async def _block(self) -> None:
        await durable_call(self.recovery.block_intake, self.binding.binding_id)

    async def reconcile_once(self) -> bool:
        """Complete a bounded window; a failed query never advances the checkpoint."""
        try:
            return await self._reconcile()
        except BaseException:
            await self._block()
            raise

    async def _reconcile(self) -> bool:
        # Startup/reconnect keeps the gate closed until recovery catches up.
        # Periodic scans retain readiness; live hints fence their own conversation.
        verify_account(self.binding, await self.connection.authorization_status())
        before = await self.connection.health()
        if not before.connected:
            raise DwsError("disconnected")
        now = self.clock_ms()
        window = await durable_call(
            self.recovery.recovery_window,
            self.binding.binding_id,
            now_ms=now,
            overlap_ms=self.policy.overlap_ms,
            window_ms=self.policy.window_ms,
        )
        if window is None:
            return False
        if window.binding != self.binding:
            raise DwsError("identity_mismatch")
        for cid in await self.connection.discover(window.start_ms, window.end_ms):
            await durable_call(
                self.recovery.remember_conversation, self.binding.binding_id, cid
            )
        conversations = await durable_call(
            self.recovery.known_conversations, self.binding.binding_id
        )
        for cid in conversations:
            result = await self.connection.history(cid, window.start_ms, window.end_ms)
            for message in result.messages:
                if message.conversation_id != cid:
                    raise DwsError("history_scope_mismatch")
                received_ms = self.clock_ms()
                outcome = await durable_call(
                    self.state.ingest,
                    self.binding.binding_id,
                    message,
                    now_ms=received_ms,
                )
                self._log_received(message, received_ms, "history_scan", outcome)
            await durable_call(
                self.recovery.resolve_hints, window, cid, result.observed_ids
            )
        after = await self.connection.health()
        if not after.connected or after.generation != before.generation:
            raise DwsError("connection_changed")
        return await durable_call(self.recovery.complete_recovery, window)

    async def _collect(self) -> None:
        async with aclosing(self.connection.listen()) as stream:
            async for notice in stream:
                if notice.kind == "ready":
                    self._ready.set()
                else:
                    self._log_received(notice, self.clock_ms(), "live_event", "hint")
                    await durable_call(
                        self.recovery.record_hint, self.binding.binding_id, notice
                    )
                    self._wake.set()
        raise DwsError("listener_ended")

    async def _recover(self) -> None:
        try:
            await asyncio.wait_for(
                self._ready.wait(), self.policy.ready_timeout_seconds
            )
        except TimeoutError:
            raise DwsError("listener_not_ready") from None
        retry = self.policy.retry_seconds
        while True:
            try:
                caught_up = await self.reconcile_once()
            except DwsError as exc:
                if str(exc) not in self.HISTORY_RETRYABLE:
                    raise
                # Reconciliation already fenced claims. Keep receiving durable hints
                # while the upstream history API recovers; never skip its watermark.
                logging.getLogger(__name__).warning(
                    "DWS history recovery deferred: code=%s retry_seconds=%s",
                    str(exc),
                    retry,
                )
                await asyncio.sleep(retry)
                retry = min(retry * 2, self.policy.max_retry_seconds)
                continue
            retry = self.policy.retry_seconds
            self._recovered = self._recovered or caught_up
            delay = self.policy.poll_seconds if caught_up else 1.0
            await asyncio.sleep(delay)

    async def hints_once(self) -> None:
        windows = await durable_call(
            self.recovery.pending_hints, self.binding.binding_id, now_ms=self.clock_ms()
        )
        if not windows:
            return
        verify_account(self.binding, await self.connection.authorization_status())
        before = await self.connection.health()
        if not before.connected:
            raise DwsError("disconnected")
        for window in windows:
            if window.binding != self.binding:
                raise DwsError("identity_mismatch")
            try:
                result = await self.connection.history(
                    window.conversation_id, window.start_ms, window.end_ms
                )
            except DwsError as exc:
                if str(exc) not in self.RETRYABLE:
                    raise
                # Keep this conversation fenced; others need not wait for its history.
                continue
            for message in result.messages:
                if message.conversation_id != window.conversation_id:
                    raise DwsError("history_scope_mismatch")
                received_ms = self.clock_ms()
                outcome = await durable_call(
                    self.state.ingest,
                    self.binding.binding_id,
                    message,
                    now_ms=received_ms,
                )
                self._log_received(message, received_ms, "live_history", outcome)
            after = await self.connection.health()
            if not after.connected or after.generation != before.generation:
                raise DwsError("connection_changed")
            await durable_call(
                self.recovery.complete_hints, window, result.observed_ids
            )

    @staticmethod
    def _log_received(
        message: DirectMessage | ListenerNotice,
        received_ms: int,
        source: str,
        outcome: str,
    ) -> None:
        if outcome == "duplicate":
            return
        # Correlate live hints with history without logging identities or content.
        key = sha256(
            (message.conversation_id + "\0" + message.message_id).encode()
        ).hexdigest()[:16]
        logging.getLogger(__name__).info(
            "DWS intake: source=%s message_key=%s delay_ms=%s outcome=%s",
            source,
            key,
            max(0, received_ms - message.created_at_ms),
            outcome,
        )

    async def _live(self) -> None:
        await self._ready.wait()
        while True:
            self._wake.clear()
            await self.hints_once()
            try:
                await asyncio.wait_for(self._wake.wait(), 1.0)
            except TimeoutError:
                pass
            await asyncio.sleep(min(1.0, self.policy.poll_seconds))

    async def _cycle(self) -> None:
        self._ready.clear()
        tasks = [
            asyncio.create_task(self._collect()),
            asyncio.create_task(self._recover()),
            asyncio.create_task(self._live()),
        ]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                await task
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def run(self) -> None:
        async with self.connection.exclusive():
            delay = self.policy.retry_seconds
            try:
                while True:
                    await self._block()
                    self._recovered = False
                    try:
                        verify_account(
                            self.binding, await self.connection.authorization_status()
                        )
                        await self._cycle()
                    except DwsError as exc:
                        await self._block()
                        if str(exc) not in self.RETRYABLE:
                            raise
                        logging.getLogger(__name__).warning(
                            "DWS listener restarting: code=%s", str(exc)
                        )
                        if self._recovered:
                            delay = self.policy.retry_seconds
                        await asyncio.sleep(delay)
                        delay = min(delay * 2, self.policy.max_retry_seconds)
            finally:
                await self._block()
