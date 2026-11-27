"""Explicit host-scheduled, leased outbox pump; concurrent external candidate IO."""

import asyncio
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabVersionConflict as Conflict,
)
from .rollout_contracts import SessionRoute
from .multi_rollout_execution import DurableSwarmMethodRun
from .multi_rollout_contracts import FINAL_PHASES


class RolloutIntentWorker:
    def __init__(self, repository, engine, execution, multi_rollout=None):
        self.repository, self.engine, self.execution = repository, engine, execution
        self.multi_rollout = multi_rollout
        self.runs = DurableSwarmMethodRun(engine)

    async def run_once(self, limit: int = 20) -> int:
        intents = await asyncio.to_thread(self.repository.pending_intents, limit)
        # Legacy profiles still use their revision-superseding serial path.
        # Candidate allocation/tenant admission is durably arbitrated before IO.
        legacy = [i for i in intents if "run_id" not in i]
        completed = sum([await self._run(intent) for intent in legacy])
        results = await asyncio.gather(
            *(self._run(intent) for intent in intents if "run_id" in intent),
            return_exceptions=True
        )
        for result in results:
            if isinstance(result, BaseException):
                raise result
        return completed + sum(results)

    async def check_deadlines(self, limit=20):
        ids = await asyncio.to_thread(self.repository.due_collets, limit)
        if self.multi_rollout is None and ids:
            raise Conflict("MultiRollout deadline controller is not installed")
        for identity in ids:
            await self.multi_rollout.tick(identity)
        return len(ids)

    async def _run(self, intent):
        fence = await asyncio.to_thread(self.repository.claim_intent, intent["id"])
        if not fence:
            return 0
        try:
            snapshot = await asyncio.to_thread(
                self.repository.get_collet, intent["collet_id"]
            )
            context = ActorContext(snapshot.owner, "product_worker")
            if intent["kind"] == "cancel":
                confirmed = await self.execution.cancel(SessionRoute(**intent["route"]))
                if snapshot.multi and confirmed is not True:
                    # OpenApi cancel acknowledges only a request. A subsequent
                    # actual terminal observation settles external occupancy;
                    # it does not fabricate a cancelled business event.
                    observed = await self.execution.observe(
                        SessionRoute(**intent["route"])
                    )
                    if observed in {"RUN_FINISHED", "RUN_FAILED"}:
                        confirmed = True
                state = "confirmed" if confirmed is True else "requested"
                if snapshot.multi:
                    self._require_multi()
                    await self.multi_rollout.record_cancellation(
                        snapshot.collet.id, intent["stage_id"], state
                    )
            elif snapshot.multi:
                self._require_multi()
                state = await self._multi(intent, snapshot, context)
            elif intent["kind"] == "reconcile":
                # No query-by-attempt contract: never blindly resend unknown IO.
                state = "unknown"
            elif snapshot.revision != intent["revision"]:
                state = "superseded"
            else:
                await self.engine.advance(intent["collet_id"], context)
                state = "done"
        except Conflict:
            # Safe CAS contention/in-flight work is retryable, not discarded due
            # to an unrelated sibling's aggregate revision.
            await asyncio.to_thread(
                self.repository.finish_intent, intent["id"], "requested", fence
            )
            return 0
        except BaseException:
            await asyncio.to_thread(
                self.repository.finish_intent, intent["id"], "unknown", fence
            )
            raise
        await asyncio.to_thread(
            self.repository.finish_intent, intent["id"], state, fence
        )
        return 1

    def _require_multi(self):
        if self.multi_rollout is None:
            raise Conflict("MultiRollout worker capabilities are not installed")

    async def _multi(self, intent, snapshot, context):
        c, kind = snapshot.multi, intent["kind"]
        if kind == "reconcile":
            record = next(
                r for r in snapshot.records if r.stage_id == intent["stage_id"]
            )
            return (
                "done"
                if record.route and record.dispatch_state == "delivered"
                else "unknown"
            )
        if c.phase in FINAL_PHASES or intent.get("generation") != c.generation:
            return "superseded"
        if kind == "advance-cand":
            self.runs.require(snapshot)
            await self.runs.advance(snapshot.collet.id, intent["rollout_id"], context)
            await self.multi_rollout.tick(snapshot.collet.id)
        elif kind == "control":
            await self.multi_rollout.tick(snapshot.collet.id)
        elif kind == "select":
            await self.multi_rollout.select(snapshot.collet.id, context)
        elif kind == "adapt":
            await self.multi_rollout.adapt(snapshot.collet.id, context)
        elif kind == "notify":
            if c.phase != "WAITING_FEEDBACK" or intent["key"] in c.notifications:
                return "superseded"
            await self.multi_rollout.notifications.notify(
                intent["key"],
                snapshot.collet.id,
                intent["rollout_id"],
                intent["deadline"],
                context,
            )
            # Host notification is idempotent by key; lease recovery can repeat
            # delivery, but can neither reset its deadline nor create two notices.
            await self.multi_rollout.record_notification(
                snapshot.collet.id, intent["key"]
            )
        return "done"
