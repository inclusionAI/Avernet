"""Opt-in supervised hosting. No registration in the interactive chat lifecycle."""

import asyncio
from collections.abc import Callable

from engine.community.core.dws_hosting.connection import DwsIntake
from engine.community.core.dws_hosting.delivery import HostingDeliveryWorker
from engine.community.core.dws_hosting.execution import HostingExecutor
from engine.community.core.dws_hosting.storage import durable_call
from engine.community.kernel.dws_hosting import Binding
from engine.community.kernel.dws_runtime import (
    HostingIssue,
    Resolution,
    RuntimePolicy,
    RuntimeStatus,
)
from engine.community.plugin_api.dws_connection import HostingRecoveryStore
from engine.community.plugin_api.dws_delivery import HostingOutbox
from engine.community.plugin_api.dws_hosting import HostingStateStore
from engine.community.plugin_api.dws_runtime import HostingRuntimeStore
from engine.community.plugin_api.dws_rollout import HostingRollout


class HostingRuntime:
    def __init__(
        self,
        binding: Binding,
        state: HostingStateStore,
        recovery: HostingRecoveryStore,
        operations: HostingRuntimeStore,
        intake: DwsIntake,
        executor: HostingExecutor,
        delivery: HostingDeliveryWorker,
        outbox: HostingOutbox,
        *,
        clock_ms: Callable[[], int],
        policy: RuntimePolicy = RuntimePolicy(),
        rollout: HostingRollout | None = None,
    ):
        self.binding, self.state, self.recovery, self.operations = (
            binding,
            state,
            recovery,
            operations,
        )
        self.intake, self.executor, self.delivery, self.outbox = (
            intake,
            executor,
            delivery,
            outbox,
        )
        self.clock_ms, self.policy = clock_ms, policy
        self.rollout = rollout

    async def require_rollout(self):
        if self.rollout is None or await self.rollout.allowed():
            return True
        # Disable durably before cancelling work; regrant must not auto-enable.
        await durable_call(
            self.state.set_enabled,
            self.binding.binding_id,
            False,
            now_ms=self.clock_ms(),
        )
        return False

    async def _rollout_watch(self):
        while await self.require_rollout():
            await asyncio.sleep(30)

    def _owner(self, actor_id):
        # COSEC: callers pass authenticated identity; IDs in request/model data are not grants.
        if actor_id != self.binding.owner_id:
            raise PermissionError("owner required")

    async def status(self, actor_id: str) -> RuntimeStatus:
        self._owner(actor_id)
        return await durable_call(self.operations.status, self.binding)

    async def issues(
        self, actor_id: str, *, limit: int = 20, offset: int = 0
    ) -> tuple[HostingIssue, ...]:
        self._owner(actor_id)
        return await durable_call(
            self.operations.issues, self.binding, limit=limit, offset=offset
        )

    async def resolve_manual(self, actor_id: str, resolution: Resolution) -> None:
        self._owner(actor_id)
        async with self.operations.exclusive():
            if resolution.kind == "execution":
                record = await self.executor.executions_record(
                    self.binding.binding_id, resolution.target_id
                )
                observation = await self.executor.engine.inspect_run(
                    record.invocation.run_id,
                    **(
                        {"gateway_witness": record.invocation.gateway_witness}
                        if record.invocation.gateway_witness is not None
                        else {}
                    ),
                )
                if (
                    observation.run_id != record.invocation.run_id
                    or not observation.ended
                ):
                    raise ValueError("execution_end_not_confirmed")
            await durable_call(
                self.operations.resolve_manual,
                self.binding,
                resolution,
                actor_id=actor_id,
                now_ms=self.clock_ms(),
            )

    async def _execution_cycle(self):
        if not await self.require_rollout():
            return
        binding_id = self.binding.binding_id
        for pending in await durable_call(
            self.operations.pending, self.binding, limit=self.policy.work_limit
        ):
            if pending.has_execution:
                await self.executor.resume(
                    binding_id, pending.turn_id, inspect_unknown=False
                )
            else:
                record = await self.executor.execute(binding_id, pending.turn_id)
                if record is None:
                    continue
            await durable_call(
                self.outbox.enqueue, binding_id, pending.turn_id, now_ms=self.clock_ms()
            )
        turn = await durable_call(
            self.state.claim_ready, binding_id, now_ms=self.clock_ms()
        )
        if turn is not None:
            record = await self.executor.execute(binding_id, turn.turn_id)
            if record is not None:
                await durable_call(
                    self.outbox.enqueue,
                    binding_id,
                    turn.turn_id,
                    now_ms=self.clock_ms(),
                )

    async def _reconcile_unknown(self):
        # Inspect only: never redispatch an uncertain run or recover its old reply.
        offset = 0
        while True:
            issues = await durable_call(
                self.operations.issues, self.binding, limit=100, offset=offset
            )
            for issue in issues:
                if issue.kind == "execution" and issue.state == "unknown":
                    await self.executor.resume(self.binding.binding_id, issue.turn_id)
            if len(issues) < 100:
                return
            # Confirmed-ended executions stay in the issue list as manual records.
            offset += len(issues)

    async def _reconciliation(self):
        while True:
            await self._reconcile_unknown()
            await asyncio.sleep(max(30, self.policy.tick_seconds))

    async def _work(self, *, deliver: bool):
        while True:
            status = await durable_call(self.operations.status, self.binding)
            if not status.enabled:
                return
            if not await self.require_rollout():
                return
            if status.intake_ready:
                if deliver:
                    await self.delivery.run_once(self.binding.binding_id)
                else:
                    await self._execution_cycle()
            await asyncio.sleep(self.policy.tick_seconds)

    async def _maintenance(self):
        while True:
            await durable_call(
                self.operations.maintain, self.binding, now_ms=self.clock_ms()
            )
            await asyncio.sleep(max(1, self.policy.tick_seconds))

    async def run(self) -> None:
        # No auth/login/consent or set_enabled here. Disabled is a true no-op.
        async with self.operations.exclusive():
            status = await durable_call(self.operations.status, self.binding)
            if not status.enabled:
                return
            if not await self.require_rollout():
                return
            await durable_call(self.recovery.block_intake, self.binding.binding_id)
            tasks = []
            try:
                await durable_call(
                    self.operations.prepare_restart,
                    self.binding,
                    now_ms=self.clock_ms(),
                )
                await durable_call(
                    self.operations.maintain, self.binding, now_ms=self.clock_ms()
                )
                tasks = [
                    asyncio.create_task(coro)
                    for coro in (
                        self.intake.run(),
                        self._work(deliver=False),
                        self._work(deliver=True),
                        self._maintenance(),
                        self._reconciliation(),
                        self._rollout_watch(),
                    )
                ]
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    await task
            finally:
                # Close gate BEFORE cancellation: pending results cannot escape as sends.
                try:
                    await durable_call(
                        self.recovery.block_intake, self.binding.binding_id
                    )
                finally:
                    for task in tasks:
                        task.cancel()
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    # A reconciliation transaction already in progress may have completed
                    # after the first barrier. All its storage calls are now joined.
                    await durable_call(
                        self.recovery.block_intake, self.binding.binding_id
                    )
                    for result in results:
                        if isinstance(result, Exception):
                            raise result
