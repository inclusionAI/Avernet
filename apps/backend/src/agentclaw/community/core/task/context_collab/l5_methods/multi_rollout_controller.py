"""Bounded controller, shared interactive/autonomous proposal adjudication.

Reserve strategy calls and a fenced operation in SQL BEFORE external I/O. No
provider/Agent call is made while a database transaction is held.
"""

import asyncio
from copy import deepcopy
from dataclasses import asdict
from math import isfinite
from time import time, monotonic
from uuid import uuid4

from ..errors import (
    ContextCollabValidationError as Invalid,
    ContextCollabVersionConflict as Conflict,
)
from ..l4_context_primitives.product_status import AdjustTypeEnum as A, StatusEnum as S
from ..l4_context_primitives.product_values import text
from .multi_rollout_policy import ParallelBestOf, AdaptiveUntilAccepted, bounded
from .multi_rollout_contracts import (
    MultiRolloutSetup,
    StrategyDescriptor,
    StrategyOperation,
    CandidateScore,
    AdjustmentProposal,
    FINAL_PHASES,
)
from .multi_rollout_state import (
    digest,
    make_plan,
    project,
    stop,
    transitions,
    waiting,
    validate_proposal,
)
from .rollout_lifecycle import affected_stages
from .rollout_contracts import Adjustment


class _RetryReservation(Exception):
    pass


class MultiRolloutController:
    def __init__(
        self, repository, strategy, isolation, notifications, authorization, interaction
    ):
        self.repository, self.strategy, self.isolation = repository, strategy, isolation
        self.notifications, self.authorization, self.interaction = (
            notifications,
            authorization,
            interaction,
        )

    def configure(self, policy, stages, execution_ref, context):
        descriptor = self.strategy.require(policy, stages, context)
        if type(descriptor) is not StrategyDescriptor:
            raise Invalid("strategy must resolve a frozen descriptor")
        for value in (
            descriptor.reference,
            descriptor.rubric,
            descriptor.evaluator_ref,
        ):
            text(value, maximum=8192)
        if (
            type(descriptor.minimum) is not int
            or type(descriptor.maximum) is not int
            or descriptor.minimum > descriptor.maximum
        ):
            raise Invalid("strategy descriptor needs a finite ordered score range")
        reference = (
            policy.strategy.selection_ref
            if isinstance(policy.strategy, ParallelBestOf)
            else policy.strategy.adaptation_ref
        )
        if descriptor.reference != reference:
            raise Invalid("strategy exact reference mismatch")
        quota = self.isolation.require(
            policy.isolation_ref, execution_ref, stages, context
        )
        bounded(quota, 1, 8, "installed tenant quota")
        if (
            isinstance(policy.strategy, ParallelBestOf)
            and policy.strategy.max_concurrency > quota
        ):
            raise Invalid("requested concurrency exceeds tenant quota")
        if isinstance(policy.strategy, AdaptiveUntilAccepted):
            # require() on the strategy must also check an explicit autonomous
            # delegation grant; ownership alone is not such a grant.
            self.notifications.require()
        return MultiRolloutSetup(descriptor, quota)

    async def tick(self, collet_id):
        for _ in range(32):
            snapshot = await asyncio.to_thread(self.repository.get_collet, collet_id)
            if snapshot.multi is None:
                return
            revision = snapshot.revision
            before = digest(asdict(snapshot.multi))
            intents = transitions(snapshot, int(time()))
            release = self._release_finished(snapshot)
            if not intents and not release and before == digest(asdict(snapshot.multi)):
                return
            try:
                await asyncio.to_thread(
                    self.repository.save,
                    snapshot,
                    revision,
                    intents=tuple(intents),
                    release_slots=release,
                )
                return
            except Conflict:
                continue
        raise Conflict("controller contention; retry tick")

    @staticmethod
    def _release_finished(snapshot):
        from .multi_rollout_execution import release_finished

        return release_finished(snapshot)

    async def reserve(self, snapshot, kind, key, payload, context, *, interrupt=False):
        generation = snapshot.multi.generation
        for _ in range(32):
            try:
                return await self._reserve_once(
                    snapshot, kind, key, payload, context, interrupt=interrupt
                )
            except _RetryReservation:
                snapshot = await asyncio.to_thread(
                    self.repository.get_collet, snapshot.collet.id
                )
                if snapshot.multi.generation != generation:
                    raise Conflict("strategy trigger generation changed") from None
        raise Conflict("strategy reservation contention")

    async def _reserve_once(
        self, snapshot, kind, key, payload, context, *, interrupt=False
    ):
        c, now = snapshot.multi, int(time())
        self.authorization.check(
            "interactive" if kind == "interactive" else "advance",
            context,
            snapshot.owner,
        )
        if c.phase in FINAL_PHASES:
            raise Conflict("controller is terminal")
        if now >= c.deadline or (c.waiting_until and now >= c.waiting_until):
            await self.tick(snapshot.collet.id)
            raise Conflict("controller/feedback deadline expired")
        if kind != "interactive" or not interrupt:
            installed = await asyncio.to_thread(
                self.strategy.require, c.policy, snapshot.specs, context
            )
            if installed != c.descriptor:
                raise Invalid("installed strategy/delegation/rubric changed")
        hashed = digest(payload)
        if c.operation:
            if c.operation.key == key and c.operation.payload_digest != hashed:
                raise Conflict("operation key already used with different content")
            if c.operation.lease_until > now and not (
                interrupt and c.operation.kind != "interactive"
            ):
                raise Conflict(
                    "strategy operation is in flight; retry the original request"
                )
        if c.strategy_calls >= c.policy.limits.max_strategy_calls:
            intents = stop(
                snapshot, "STRATEGY_BUDGET", "strategy call budget exhausted"
            )
            await asyncio.to_thread(
                self.repository.save,
                snapshot,
                snapshot.revision,
                intents=tuple(intents),
            )
            raise Conflict("strategy call budget exhausted")
        if c.operation:
            for attempt in c.strategy_attempts:
                if attempt["fence"] == c.operation.fence:
                    attempt["state"] = "unknown"
        c.strategy_calls += 1
        c.operation = StrategyOperation(
            key, hashed, uuid4().hex, now + 300, c.generation, kind
        )
        c.strategy_attempts.append(
            dict(
                key=key,
                kind=kind,
                fence=c.operation.fence,
                generation=c.generation,
                ordinal=c.strategy_calls,
                started_at=now,
                state="in_flight",
                elapsed_ms=0,
            )
        )
        try:
            await asyncio.to_thread(self.repository.save, snapshot, snapshot.revision)
        except Conflict:
            raise _RetryReservation() from None
        return c.operation.fence

    async def _fresh(self, collet_id, fence):
        snapshot = await asyncio.to_thread(self.repository.get_collet, collet_id)
        c = snapshot.multi
        if (
            c.phase in FINAL_PHASES
            or c.operation is None
            or c.operation.fence != fence
            or c.operation.generation != c.generation
        ):
            raise Conflict("strategy result is fenced by a newer operation or stop")
        if int(time()) >= c.deadline or (
            c.waiting_until and int(time()) >= c.waiting_until
        ):
            await self.tick(collet_id)
            raise Conflict("strategy completed after deadline")
        return snapshot

    async def select(self, collet_id, context):
        snapshot = await asyncio.to_thread(self.repository.get_collet, collet_id)
        c = snapshot.multi
        if c.phase != "SELECTING":
            return
        qualified = tuple(
            v.rollout_id
            for v in c.candidates
            if next(r for r in snapshot.rollouts if r.rollout_id == v.rollout_id).status
            is S.ACCEPTANCE_SUCCEED
        )
        payload = {
            "qualified": qualified,
            "evidence": digest([asdict(r) for r in snapshot.rollouts]),
            "descriptor": asdict(c.descriptor),
        }
        fence = await self.reserve(
            snapshot, "select", f"{c.run_id}:{c.generation}:select", payload, context
        )
        try:
            started = monotonic()
            scores = await asyncio.wait_for(
                self.strategy.evaluate(
                    c.descriptor, deepcopy(snapshot), qualified, context
                ),
                timeout=min(240, max(0.1, c.deadline - time())),
            )
            elapsed_ms = int((monotonic() - started) * 1000)
            if type(scores) is not tuple or any(
                type(s) is not CandidateScore for s in scores
            ):
                raise Invalid("selection requires typed immutable scores")
            ids = [s.rollout_id for s in scores]
            if len(set(ids)) != len(ids) or set(ids) != set(qualified):
                raise Invalid("selection must score each and only qualified candidate")
            for score in scores:
                if (
                    type(score.rollout_id) is not int
                    or type(score.score) not in {int, float}
                    or (type(score.score) is float and not isfinite(score.score))
                    or not c.descriptor.minimum <= score.score <= c.descriptor.maximum
                ):
                    raise Invalid("invalid/nonfinite/out-of-range trusted score")
                text(score.reason, maximum=8192)
            order = {v.rollout_id: v.ordinal for v in c.candidates}
            winner = min(
                scores, key=lambda s: (-s.score, order[s.rollout_id])
            ).rollout_id
            # SQL siblings may change cancellation facts; re-read, keeping fence.
            for _ in range(32):
                saved = await self._fresh(collet_id, fence)
                self.authorization.check("advance", context, saved.owner)
                if (
                    await asyncio.to_thread(
                        self.strategy.require, saved.multi.policy, saved.specs, context
                    )
                    != saved.multi.descriptor
                ):
                    raise Invalid("strategy authority/rubric changed before adoption")
                self._attempt_result(saved.multi, fence, "committed", elapsed_ms)
                saved.multi.operation = None
                saved.multi.winner, saved.multi.phase, saved.multi.outcome = (
                    winner,
                    "SUCCEEDED",
                    "ACCEPTED",
                )
                saved.multi.decisions.append(
                    dict(
                        kind="selection",
                        generation=c.generation,
                        evidence=payload["evidence"],
                        descriptor=asdict(c.descriptor),
                        scores=[asdict(s) for s in scores],
                        winner=winner,
                        actor=context.actor.to_dict(),
                    )
                )
                saved.current_rollout_id = winner
                project(saved)
                try:
                    await asyncio.to_thread(self.repository.save, saved, saved.revision)
                    return
                except Conflict:
                    continue
            raise Conflict("selection commit contention")
        except Invalid:
            await self.fail_operation(
                collet_id, fence, "INFRASTRUCTURE_SELECTION_INVALID"
            )
            raise
        except Exception:
            await self.expire_operation(collet_id, fence)
            raise

    async def interactive(self, snapshot, uuid, content, context):
        payload = {"rollout_id": snapshot.current_rollout_id, "content": content}
        c = snapshot.multi
        if c.phase in FINAL_PHASES:
            raise Conflict("terminal controller accepts only existing receipt replay")
        if (
            isinstance(c.policy.strategy, AdaptiveUntilAccepted)
            and c.phase != "WAITING_FEEDBACK"
        ):
            # Only the pre-existing trusted stop capability may interrupt work.
            kind = "stop"
        elif isinstance(c.policy.strategy, ParallelBestOf):
            kind = "stop"
        else:
            kind = "adapt"
        fence = await self.reserve(
            snapshot,
            "interactive",
            f"{digest(context.actor.to_dict())}:{uuid}",
            payload,
            context,
            interrupt=kind == "stop",
        )
        try:
            started = monotonic()
            if kind == "stop":
                old = await asyncio.wait_for(
                    self.interaction.propose(deepcopy(snapshot), content, context),
                    timeout=min(240, max(0.1, c.deadline - time())),
                )
                if (
                    type(old) is not Adjustment
                    or old.type is not A.SHUTDOWN
                    or type(old.stages) is not tuple
                    or type(old.instruction) is not str
                    or old.stages
                    or old.instruction
                ):
                    raise Invalid(
                        "only SHUTDOWN is permitted outside adaptive feedback waiting"
                    )
                proposal = AdjustmentProposal(A.SHUTDOWN, old.message)
            else:
                proposal = await asyncio.wait_for(
                    self.strategy.propose_adjustment(
                        c.policy.strategy.adaptation_ref,
                        deepcopy(snapshot),
                        content,
                        context,
                    ),
                    timeout=min(240, max(0.1, c.deadline - time())),
                )
            return await self.commit_adjustment(
                snapshot.collet.id,
                fence,
                proposal,
                context,
                receipt=(uuid, "interactive", payload, context),
                feedback=content,
                elapsed_ms=int((monotonic() - started) * 1000),
            )
        except Invalid:
            await self.expire_operation(snapshot.collet.id, fence)
            raise
        except Exception:
            await self.expire_operation(snapshot.collet.id, fence)
            raise

    async def adapt(self, collet_id, context):
        snapshot = await asyncio.to_thread(self.repository.get_collet, collet_id)
        c = snapshot.multi
        if c.phase != "ADAPTING":
            return
        payload = {"generation": c.generation, "failure": c.failure_digest}
        fence = await self.reserve(
            snapshot, "adapt", f"{c.run_id}:{c.generation}:adapt", payload, context
        )
        try:
            started = monotonic()
            proposal = await asyncio.wait_for(
                self.strategy.propose_adjustment(
                    c.policy.strategy.adaptation_ref, deepcopy(snapshot), "", context
                ),
                timeout=min(240, max(0.1, c.deadline - time())),
            )
            await self.commit_adjustment(
                collet_id,
                fence,
                proposal,
                context,
                elapsed_ms=int((monotonic() - started) * 1000),
            )
        except Invalid:
            await self.fail_operation(
                collet_id, fence, "INFRASTRUCTURE_ADAPTATION_INVALID"
            )
            raise
        except Exception:
            await self.expire_operation(collet_id, fence)
            raise

    async def commit_adjustment(
        self,
        collet_id,
        fence,
        proposal,
        context,
        *,
        receipt=None,
        feedback="",
        elapsed_ms=0,
    ):
        for _ in range(32):
            snapshot = await self._fresh(collet_id, fence)
            c = snapshot.multi
            self.authorization.check(
                "interactive" if receipt else "advance", context, snapshot.owner
            )
            if (
                proposal.type is not A.SHUTDOWN
                and await asyncio.to_thread(
                    self.strategy.require, c.policy, snapshot.specs, context
                )
                != c.descriptor
            ):
                raise Invalid("strategy authority changed before adjustment commit")
            validate_proposal(snapshot, proposal)
            intents, successor = [], None
            result = {"type": proposal.type.value, "message": proposal.message}
            c.decisions.append(
                dict(
                    kind="adjustment",
                    generation=c.generation,
                    key=c.operation.key,
                    source=snapshot.current_rollout_id,
                    plan=c.plans[-1].digest,
                    proposal=asdict(proposal),
                    feedback_digest=digest(feedback),
                    actor=context.actor.to_dict(),
                )
            )
            self._attempt_result(c, fence, "committed", elapsed_ms)
            c.operation = None
            if proposal.type is A.SHUTDOWN:
                intents.extend(
                    stop(snapshot, "USER_SHUTDOWN", proposal.message, shutdown=True)
                )
            elif proposal.type is A.DO_NOTHING:
                c.no_progress += 1
                if c.no_progress >= c.policy.limits.max_no_progress or (
                    receipt is None and not proposal.request_feedback
                ):
                    intents.extend(
                        stop(
                            snapshot,
                            "NO_PROGRESS",
                            "no new executable or waitable evidence",
                        )
                    )
                else:
                    intents.append(
                        waiting(snapshot, int(time()))
                    )  # Never reset an existing absolute deadline.
            else:
                if len(c.candidates) >= c.policy.limits.max_rollouts:
                    intents.extend(
                        stop(snapshot, "ROLLOUT_BUDGET", "rollout budget exhausted")
                    )
                else:
                    if proposal.type is A.OPTIMIZE:
                        previous = c.plans[-1]
                        merged = {p.stage: p for p in previous.patches}
                        merged.update({p.stage: p for p in proposal.patches})
                        patches = tuple(merged[k] for k in sorted(merged))
                        if patches == previous.patches:
                            c.no_progress += 1
                        else:
                            rerun = affected_stages(
                                snapshot,
                                tuple(
                                    {
                                        *proposal.rerun,
                                        *(p.stage for p in proposal.patches),
                                    }
                                ),
                            )
                            c.plans.append(
                                make_plan(
                                    previous.profile_digest,
                                    previous.digest,
                                    patches,
                                    tuple(sorted(rerun)),
                                    digest(
                                        {
                                            "failure": c.failure_digest,
                                            "feedback": feedback,
                                        }
                                    ),
                                    previous.version + 1,
                                )
                            )
                    if c.no_progress >= c.policy.limits.max_no_progress:
                        intents.extend(
                            stop(
                                snapshot,
                                "NO_PROGRESS",
                                "unchanged plan proposals exhausted progress budget",
                            )
                        )
                    else:
                        c.generation += 1
                        c.phase, c.waiting_until = "EXECUTING", 0
                        # Immutable root content plus bounded supplemental feedback;
                        # stage instructions are projected by the Swarm adapter.
                        content = snapshot.rollouts[0].content
                        if feedback:
                            import json

                            content = json.dumps(
                                {"root_context": content, "feedback": feedback},
                                ensure_ascii=False,
                            )
                        text(content)
                        successor = (content, {s.name for s in snapshot.specs})
            project(snapshot)
            try:
                committed = await asyncio.to_thread(
                    self.repository.save,
                    snapshot,
                    snapshot.revision,
                    intents=tuple(intents),
                    successor=successor,
                    receipt=(*receipt, result) if receipt else None,
                )
                return committed or result
            except Conflict:
                if receipt:
                    replay = await asyncio.to_thread(self.repository.replay, *receipt)
                    if replay is not None:
                        return replay
                continue
        raise Conflict("adjustment commit contention")

    @staticmethod
    def _attempt_result(control, fence, state, elapsed_ms):
        attempt = next(a for a in control.strategy_attempts if a["fence"] == fence)
        attempt["state"], attempt["elapsed_ms"] = state, elapsed_ms
        if state == "committed":
            kind = attempt["kind"]
            control.strategy_elapsed_ms[kind] = (
                control.strategy_elapsed_ms.get(kind, 0) + elapsed_ms
            )

    async def expire_operation(self, collet_id, fence):
        # The previous external call has returned/failed. It is now safe to
        # recompute on an explicit retry, charging another strategy call.
        for _ in range(32):
            snapshot = await asyncio.to_thread(self.repository.get_collet, collet_id)
            c = snapshot.multi
            if c.operation is None or c.operation.fence != fence:
                return
            self._attempt_result(c, fence, "unknown", 0)
            c.operation.lease_until = 0
            try:
                await asyncio.to_thread(
                    self.repository.save, snapshot, snapshot.revision
                )
                return
            except Conflict:
                continue
        raise Conflict("cannot persist strategy recovery state")

    async def fail_operation(self, collet_id, fence, outcome):
        snapshot = await self._fresh(collet_id, fence)
        intents = stop(snapshot, outcome, "strategy response failed validation")
        await asyncio.to_thread(
            self.repository.save, snapshot, snapshot.revision, intents=tuple(intents)
        )

    async def record_cancellation(self, collet_id, stage_id, state):
        for _ in range(32):
            snapshot = await asyncio.to_thread(self.repository.get_collet, collet_id)
            if snapshot.multi is None:
                return
            snapshot.multi.cancellations[str(stage_id)] = state
            release = self._release_finished(snapshot)
            try:
                await asyncio.to_thread(
                    self.repository.save,
                    snapshot,
                    snapshot.revision,
                    release_slots=release,
                )
                return
            except Conflict:
                continue
        raise Conflict("cancellation reconciliation contention")

    async def record_notification(self, collet_id, key):
        for _ in range(32):
            snapshot = await asyncio.to_thread(self.repository.get_collet, collet_id)
            if key in snapshot.multi.notifications:
                return
            snapshot.multi.notifications.append(key)
            try:
                await asyncio.to_thread(
                    self.repository.save, snapshot, snapshot.revision
                )
                return
            except Conflict:
                continue
        raise Conflict("notification receipt contention")
