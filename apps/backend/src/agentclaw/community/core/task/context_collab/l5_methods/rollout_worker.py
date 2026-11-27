"""Bounded durable outbox pump. A host schedules run_once; no hidden retry loop."""

import asyncio
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.l5_methods.rollout_contracts import (
    SessionRoute,
)


class RolloutIntentWorker:
    def __init__(self, repository, engine, execution):
        self.repository, self.engine, self.execution = repository, engine, execution

    async def run_once(self, limit: int = 20) -> int:
        intents = await asyncio.to_thread(self.repository.pending_intents, limit)
        completed = 0
        for intent in intents:
            if not await asyncio.to_thread(self.repository.claim_intent, intent["id"]):
                continue
            try:
                if intent["kind"] == "cancel":
                    confirmed = await self.execution.cancel(
                        SessionRoute(**intent["route"])
                    )
                    state = "confirmed" if confirmed is True else "requested"
                else:
                    snapshot = await asyncio.to_thread(
                        self.repository.get_collet, intent["collet_id"]
                    )
                    if snapshot.revision != intent["revision"]:
                        state = "superseded"
                    else:
                        await self.engine.advance(
                            intent["collet_id"],
                            ActorContext(snapshot.owner, "product_worker"),
                        )
                        state = "done"
                await asyncio.to_thread(
                    self.repository.finish_intent, intent["id"], state
                )
                completed += 1
            except BaseException:
                # An unknown external outcome needs reconciliation, not replay.
                # Marking failure must itself succeed; storage failures propagate.
                await asyncio.to_thread(
                    self.repository.finish_intent, intent["id"], "unknown"
                )
                raise
        return completed
