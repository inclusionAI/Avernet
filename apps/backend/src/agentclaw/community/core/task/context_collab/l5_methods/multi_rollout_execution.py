"""Candidate-scoped Swarm adapter and real stage input projection."""

from dataclasses import replace
from ..errors import ContextCollabVersionConflict as Conflict
from ..l4_context_primitives.product_values import shape, text
from .multi_rollout_state import candidate_for, resolve_body, stop
from .multi_rollout_policy import ParallelBestOf
from .rollout_lifecycle import TERMINAL


def active_rollout(snapshot, rollout_id):
    if snapshot.multi:
        if not rollout_id:
            raise Conflict(
                "MultiRollout execution requires an explicit candidate scope"
            )
        candidate_for(snapshot, rollout_id, active=True)
        return next(r for r in snapshot.rollouts if r.rollout_id == rollout_id)
    if rollout_id and rollout_id != snapshot.current_rollout_id:
        raise Conflict("legacy execution only permits its current rollout")
    return snapshot.current()


def plan_stage(snapshot, rollout, ready):
    candidate = candidate_for(snapshot, rollout.rollout_id, active=True)
    plan = next(p for p in snapshot.multi.plans if p.digest == candidate.plan_digest)
    patch = next((p for p in plan.patches if p.stage == ready.name), None)
    if patch and patch.candidates:
        ready = replace(ready, candidates=patch.candidates)
    return ready, patch.instruction if patch else ""


def reserve_dispatch(snapshot, rollout):
    c = snapshot.multi
    candidate = candidate_for(snapshot, rollout.rollout_id, active=True)
    if c.dispatches >= c.policy.limits.max_stage_dispatches:
        return False, stop(
            snapshot, "DISPATCH_BUDGET", "stage dispatch budget exhausted"
        )
    concurrency = (
        c.policy.strategy.max_concurrency
        if isinstance(c.policy.strategy, ParallelBestOf)
        else 1
    )
    occupied = sum(v.slot for v in c.candidates)
    if not candidate.slot and occupied >= concurrency:
        raise Conflict("candidate execution slots are occupied")
    acquire = not candidate.slot
    candidate.slot = candidate.started = True
    c.dispatches += 1
    c.peak_concurrency = max(c.peak_concurrency, occupied + int(acquire))
    c.phase = (
        "SAMPLING" if isinstance(c.policy.strategy, ParallelBestOf) else "EXECUTING"
    )
    return acquire, []


def release_finished(snapshot):
    if snapshot.multi is None:
        return ()
    release = []
    for candidate in snapshot.multi.candidates:
        rollout = next(
            r for r in snapshot.rollouts if r.rollout_id == candidate.rollout_id
        )
        stages = {s.stage_rollout_id: s for s in rollout.stages}
        records = [r for r in snapshot.records if r.stage_id in stages]
        unsettled = any(
            r.attempt
            and (
                r.dispatch_state in {"unknown", "revoked"}
                and snapshot.multi.cancellations.get(str(r.stage_id)) != "confirmed"
            )
            for r in records
        )
        safely_finished = rollout.status in TERMINAL and not unsettled
        safely_cancelled = candidate.incomplete and all(
            not r.attempt
            or stages[r.stage_id].status in TERMINAL
            or snapshot.multi.cancellations.get(str(r.stage_id)) == "confirmed"
            for r in records
        )
        if candidate.slot and (safely_finished or safely_cancelled):
            candidate.slot = False
            release.append(candidate.rollout_id)
    return tuple(release)


async def isolated_input(isolation, snapshot, rollout, selected, instruction, context):
    c = snapshot.multi
    candidate = candidate_for(snapshot, rollout.rollout_id, active=True)
    envelope = await isolation.prepare(
        c.policy.isolation_ref, candidate.namespace, selected, context
    )
    shape(envelope, {"workspace", "artifact_namespace", "execution_key"})
    for value in envelope.values():
        text(value, maximum=2048)
    return {
        "run_id": c.run_id,
        "generation": candidate.generation,
        "candidate_id": candidate.rollout_id,
        "plan_digest": candidate.plan_digest,
        "namespace": candidate.namespace,
        "stage_instruction": instruction,
        "isolation": envelope,
    }


class DurableSwarmMethodRun:
    """MethodRunPort implementation, using persisted effective Bindings/edges."""

    def __init__(self, engine):
        self.engine = engine

    def require(self, snapshot):
        resolve_body(snapshot)
        self.engine.execution.require(
            snapshot.execution_ref, snapshot.acceptance_ref, snapshot.specs
        )

    async def start(self, collet_id, rollout_id, context):
        await self.engine.advance(collet_id, context, rollout_id=rollout_id)

    async def observe(self, collet_id, rollout_id, context):
        # Observation is a governed next-step operation; it never declares an
        # acceptance PASS. The engine may dispatch the next accepted DAG stage.
        await self.engine.advance(collet_id, context, rollout_id=rollout_id)

    async def collect(self, collet_id, rollout_id, context):
        import asyncio
        from copy import deepcopy

        snapshot = await asyncio.to_thread(self.engine.repository.get_collet, collet_id)
        self.engine.authorization.check("read", context, snapshot.owner)
        candidate_for(snapshot, rollout_id)
        return deepcopy(
            next(r for r in snapshot.rollouts if r.rollout_id == rollout_id)
        )

    async def advance(self, collet_id, rollout_id, context):
        import asyncio

        snapshot = await asyncio.to_thread(self.engine.repository.get_collet, collet_id)
        candidate = candidate_for(snapshot, rollout_id, active=True)
        if candidate.started:
            await self.observe(collet_id, rollout_id, context)
        else:
            await self.start(collet_id, rollout_id, context)

    async def request_cancel(self, route):
        return await self.engine.execution.cancel(route)
