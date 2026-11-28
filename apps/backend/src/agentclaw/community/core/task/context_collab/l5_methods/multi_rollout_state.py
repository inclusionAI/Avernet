"""Pure MultiRollout reducers. SQL is the only identity/CAS authority."""

from dataclasses import asdict, replace
from hashlib import sha256
import json

from ..l2_agent_network.network_refs import AgentRef
from ..errors import (
    ContextCollabValidationError as Invalid,
    ContextCollabVersionConflict as Conflict,
)
from ..l4_context_primitives.product_status import StatusEnum as S, AdjustTypeEnum as A
from .method_binding import BindingState
from .method_composition import CompositionRelation, CompositionEdgeState
from .multi_rollout_policy import ParallelBestOf, AdaptiveUntilAccepted
from .multi_rollout_contracts import (
    FINAL_PHASES,
    MultiRolloutRunRecord,
    MultiRolloutCandidateRecord,
    RunPlan,
    StagePatch,
    AdjustmentProposal,
)
from .rollout_lifecycle import TERMINAL


def digest(value):
    return sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
    ).hexdigest()


def resolve_body(snapshot):
    roots = [
        b
        for b in snapshot.bindings
        if b.method_kind == "MultiRollout" and b.state is BindingState.ACTIVE
    ]
    if len(roots) != 1 or roots[0].profile_versions != {"MultiRollout": "1.0"}:
        raise Invalid("missing exact MultiRollout controller Binding")
    edges = [
        e
        for e in snapshot.relations
        if e.parent_binding == roots[0].method_binding_id
        and e.state is CompositionEdgeState.ACTIVE
    ]
    if len(edges) != 1 or edges[0].relation_kind is not CompositionRelation.NEST:
        raise Invalid("controller requires exactly one effective NEST edge")
    bodies = [
        b
        for b in snapshot.bindings
        if b.method_binding_id == edges[0].child_binding
        and b.state is BindingState.ACTIVE
    ]
    if (
        len(bodies) != 1
        or bodies[0].method_kind != "Swarm"
        or bodies[0].profile_versions != {"Swarm": "0.2"}
    ):
        raise Invalid("no installed executable MethodRunPort for this body")
    return roots[0], bodies[0], edges[0]


def make_plan(
    profile_digest, previous="", patches=(), rerun=(), evidence="", version=1
):
    facts = dict(
        version=version,
        profile_digest=profile_digest,
        previous=previous,
        patches=patches,
        rerun=rerun,
        evidence_digest=evidence,
    )
    return RunPlan(
        digest=digest({**facts, "patches": [asdict(p) for p in patches]}), **facts
    )


def initialize(snapshot, policy, setup, now, profile_digest):
    root, body, edge = resolve_body(snapshot)
    snapshot.multi = MultiRolloutRunRecord(
        f"multi:{snapshot.collet.id}",
        root.method_binding_id,
        body.method_binding_id,
        edge.edge_id,
        digest(
            {
                "bindings": [b.to_dict() for b in snapshot.bindings],
                "relations": [e.to_dict() for e in snapshot.relations],
            }
        ),
        policy,
        setup.descriptor,
        setup.tenant_quota,
        now,
        now + policy.limits.max_elapsed_seconds,
        plans=[make_plan(profile_digest, rerun=tuple(s.name for s in snapshot.specs))],
    )


def append_candidate(snapshot, identity, content):
    # Full rerun is the fail-closed default: no unproved evidence/session reuse.
    from .rollout_lifecycle import append_rollout

    control = snapshot.multi
    if control is None or len(control.candidates) >= control.policy.limits.max_rollouts:
        raise Conflict("rollout budget exhausted")
    old_focus = snapshot.current_rollout_id
    previous = control.candidates[-1].rollout_id if control.candidates else 0
    append_rollout(snapshot, identity, content, {s.name for s in snapshot.specs})
    rid = snapshot.current_rollout_id
    _, body, edge = resolve_body(snapshot)
    namespace = f"{control.run_id}/g{control.generation}/candidate-{rid}"
    binding = replace(
        body,
        method_binding_id=f"{body.method_binding_id}:run:{rid}",
        bind_reason="isolated MultiRollout candidate",
    )
    relation = replace(
        edge,
        edge_id=f"{edge.edge_id}:run:{rid}",
        child_binding=binding.method_binding_id,
    )
    control.candidates.append(
        MultiRolloutCandidateRecord(
            rid,
            control.generation,
            len(control.candidates) + 1,
            (
                previous
                if isinstance(control.policy.strategy, AdaptiveUntilAccepted)
                else 0
            ),
            binding,
            relation,
            control.plans[-1].digest,
            namespace,
        )
    )
    # A parallel batch has no serial predecessor and its focus is order-stable.
    if isinstance(control.policy.strategy, ParallelBestOf):
        snapshot.parents.pop(str(rid), None)
        if old_focus:
            snapshot.current_rollout_id = old_focus
    project(snapshot)


def candidate_for(snapshot, rollout_id, *, active=False):
    control = snapshot.multi
    candidate = (
        next((c for c in control.candidates if c.rollout_id == rollout_id), None)
        if control
        else None
    )
    if candidate is None or (
        active
        and (
            control.phase in FINAL_PHASES
            or candidate.generation != control.generation
            or candidate.incomplete
        )
    ):
        raise Conflict("candidate is historical, revoked or outside this generation")
    return candidate


def stage_rollout(snapshot, stage_id):
    rollout = next(
        (
            r
            for r in snapshot.rollouts
            if any(s.stage_rollout_id == stage_id for s in r.stages)
        ),
        None,
    )
    if rollout is None:
        raise Conflict("stage is not a member of this runtime")
    if snapshot.multi:
        candidate_for(snapshot, rollout.rollout_id, active=True)
    elif rollout.rollout_id != snapshot.current_rollout_id:
        raise Conflict("stage does not belong to the current rollout")
    return rollout


def project(snapshot):
    c = snapshot.multi
    if c is None:
        return
    if c.phase == "STOPPED":
        snapshot.stopped = True
        return
    if c.phase == "SUCCEEDED":
        status = (
            S.RUN_FINISHED
            if snapshot.overall_acceptance == "owner"
            else S.ACCEPTANCE_SUCCEED
        )
    elif c.phase == "FAILED":
        status = (
            S.RUN_FAILED
            if c.outcome.startswith("INFRASTRUCTURE")
            else S.ACCEPTANCE_FAILED
        )
    elif c.phase == "WAITING_FEEDBACK":
        status = S.RUN_FINISHED
    elif c.phase == "CREATED":
        status = S.CREATE
    else:
        status = S.RUNNING
    snapshot.collet = replace(snapshot.collet, status=status)


def stop(snapshot, outcome, reason, *, shutdown=False):
    c = snapshot.multi
    c.phase, c.outcome, c.reason = (
        ("STOPPED" if shutdown else "FAILED"),
        outcome,
        reason,
    )
    if c.operation:
        for attempt in c.strategy_attempts:
            if attempt["fence"] == c.operation.fence:
                attempt["state"] = "fenced"
    c.operation = None
    c.waiting_until = 0
    intents = []
    for candidate in c.candidates:
        rollout = next(
            r for r in snapshot.rollouts if r.rollout_id == candidate.rollout_id
        )
        if rollout.status not in TERMINAL:
            candidate.incomplete = True
        for stage in rollout.stages:
            record = next(
                v for v in snapshot.records if v.stage_id == stage.stage_rollout_id
            )
            if (
                record.attempt
                and stage.status not in TERMINAL
                and record.dispatch_state not in {"revoked", "reused"}
            ):
                record.dispatch_state, record.lease_until = "revoked", 0
                c.cancellations[str(record.stage_id)] = (
                    "pending" if record.route else "unknown"
                )
                if record.route:
                    intents.append(
                        dict(
                            kind="cancel",
                            stage_id=record.stage_id,
                            attempt=record.attempt,
                            route=asdict(record.route),
                        )
                    )
                else:
                    intents.append(
                        dict(
                            kind="reconcile",
                            stage_id=record.stage_id,
                            attempt=record.attempt,
                        )
                    )
    project(snapshot)
    return intents


def waiting(snapshot, now):
    c = snapshot.multi
    strategy = c.policy.strategy
    timeout = (
        strategy.feedback_timeout_seconds
        if strategy.feedback_mode == "interactive"
        else min(600, c.policy.limits.max_elapsed_seconds)
    )
    c.phase = "WAITING_FEEDBACK"
    if not c.waiting_until:
        c.waiting_until = min(c.deadline, now + timeout)
    key = f"{c.run_id}:{c.generation}:feedback"
    project(snapshot)
    return {
        "kind": "notify",
        "key": key,
        "rollout_id": snapshot.current_rollout_id,
        "deadline": c.waiting_until,
    }


def transitions(snapshot, now):
    """Produce durable controller work, never call an Agent from report SQL."""
    c = snapshot.multi
    if c.phase in FINAL_PHASES:
        return []
    if now >= c.deadline or (c.waiting_until and now >= c.waiting_until):
        return stop(
            snapshot,
            "DEADLINE" if now >= c.deadline else "FEEDBACK_TIMEOUT",
            "absolute deadline elapsed",
        )
    if c.phase in {"SELECTING", "ADAPTING", "WAITING_FEEDBACK"}:
        return []
    rollouts = [
        r
        for r in snapshot.rollouts
        if candidate_for(snapshot, r.rollout_id).generation == c.generation
    ]
    if any(r.status not in TERMINAL for r in rollouts):
        c.phase = (
            "SAMPLING" if isinstance(c.policy.strategy, ParallelBestOf) else "EXECUTING"
        )
        project(snapshot)
        return []
    if isinstance(c.policy.strategy, ParallelBestOf):
        if not any(r.status is S.ACCEPTANCE_SUCCEED for r in rollouts):
            return stop(
                snapshot,
                "NO_ACCEPTED_CANDIDATE",
                "no candidate passed frozen acceptance",
            )
        c.phase = "SELECTING"
        project(snapshot)
        return [{"kind": "select", "generation": c.generation}]
    rollout = rollouts[-1]
    if rollout.status is S.ACCEPTANCE_SUCCEED:
        c.winner, c.phase, c.outcome = rollout.rollout_id, "SUCCEEDED", "ACCEPTED"
        snapshot.current_rollout_id = rollout.rollout_id
        project(snapshot)
        return []
    if (
        rollout.status is S.RUN_FAILED
        and c.policy.strategy.feedback_mode == "autonomous"
    ):
        return stop(
            snapshot,
            "INFRASTRUCTURE_RUN_FAILED",
            "runtime failure is not acceptance feedback",
        )
    evidence = digest(
        [
            {
                "stage": s.name,
                **(
                    {"runtime_error": s.error_msg}
                    if s.status is S.RUN_FAILED
                    else {
                        "failed": [a.condition for a in s.acceptances if not a.passed]
                    }
                ),
            }
            for s in rollout.stages
            if s.status in {S.ACCEPTANCE_FAILED, S.RUN_FAILED}
        ]
    )
    if evidence == c.failure_digest:
        c.no_progress += 1
    else:
        c.no_progress = 0
    c.failure_digest = evidence
    if c.no_progress >= c.policy.limits.max_no_progress:
        return stop(snapshot, "NO_PROGRESS", "repeated failure without improvement")
    if len(c.candidates) >= c.policy.limits.max_rollouts:
        return stop(snapshot, "ROLLOUT_BUDGET", "rollout limit exhausted")
    if c.policy.strategy.feedback_mode == "interactive":
        return [waiting(snapshot, now)]
    c.phase = "ADAPTING"
    project(snapshot)
    return [{"kind": "adapt", "generation": c.generation}]


def validate_proposal(snapshot, proposal):
    from ..l4_context_primitives.product_values import text
    from .rollout_lifecycle import affected_stages

    if (
        type(proposal) is not AdjustmentProposal
        or type(proposal.type) is not A
        or type(proposal.request_feedback) is not bool
    ):
        raise Invalid("typed adjustment proposal required")
    text(proposal.message, maximum=8192)
    if type(proposal.patches) is not tuple or type(proposal.rerun) is not tuple:
        raise Invalid("immutable stage patch/rerun arrays required")
    if proposal.type is not A.OPTIMIZE and (proposal.patches or proposal.rerun):
        raise Invalid("only OPTIMIZE may edit a plan")
    if proposal.request_feedback and proposal.type is not A.DO_NOTHING:
        raise Invalid("only no-op may request feedback")
    if any(type(name) is not str for name in proposal.rerun):
        raise Invalid("rerun must contain stage names")
    by_name = {s.name: s for s in snapshot.specs}
    names = set()
    for patch in proposal.patches:
        if (
            type(patch) is not StagePatch
            or patch.stage not in by_name
            or patch.stage in names
        ):
            raise Invalid("patch must name an existing unique stage")
        names.add(patch.stage)
        text(patch.instruction, maximum=8192, empty=True)
        if (
            type(patch.candidates) is not tuple
            or any(
                type(a) is not AgentRef or a not in by_name[patch.stage].candidates
                for a in patch.candidates
            )
            or len(set(patch.candidates)) != len(patch.candidates)
        ):
            raise Invalid("patch may only narrow original candidates")
    if proposal.rerun:
        affected_stages(snapshot, proposal.rerun)
    return proposal
