"""Closed decoder and cross-record invariants for multi-rollout@1 snapshots."""

from dataclasses import fields, asdict, replace
from copy import deepcopy
from math import isfinite
from ...errors import ContextCollabValidationError as Invalid
from ...l2_agent_network.network_refs import AgentRef
from ...l4_context_primitives.product_values import shape, positive_id, text
from ...l4_context_primitives.product_status import StatusEnum as S
from ...l5_methods.method_binding import MethodBinding, BindingState
from ...l5_methods.method_composition import MethodCompositionEdge
from ...l5_methods.value_codec import encode, decode
from ...l5_methods.multi_rollout_policy import (
    MULTI_POLICY_TYPES,
    MultiRolloutMethodPolicy,
    bounded,
    ParallelBestOf,
)
from ...l5_methods.multi_rollout_contracts import (
    MultiRolloutRunRecord,
    MultiRolloutCandidateRecord,
    RunPlan,
    StagePatch,
    StrategyDescriptor,
    StrategyOperation,
    FINAL_PHASES,
)
from ...l5_methods.multi_rollout_state import make_plan, resolve_body, digest


def typed(data, cls):
    shape(data, {f.name for f in fields(cls)})
    return cls(**data)


def encode_control(control):
    data = asdict(control)
    data["policy"] = encode(control.policy)
    for raw, candidate in zip(data["candidates"], control.candidates):
        raw["binding"] = candidate.binding.to_dict()
        raw["edge"] = candidate.edge.to_dict()
    for raw, plan in zip(data["plans"], control.plans):
        raw["patches"] = [
            {
                "stage": p.stage,
                "instruction": p.instruction,
                "candidates": [a.to_dict() for a in p.candidates],
            }
            for p in plan.patches
        ]
    return data


def decode_control(data):
    shape(data, {f.name for f in fields(MultiRolloutRunRecord)})
    data = dict(data)
    data["policy"] = decode(
        data["policy"], MultiRolloutMethodPolicy, MULTI_POLICY_TYPES
    )
    data["descriptor"] = typed(data["descriptor"], StrategyDescriptor)
    data["operation"] = (
        typed(data["operation"], StrategyOperation)
        if data["operation"] is not None
        else None
    )
    candidates = []
    for raw in data["candidates"]:
        shape(raw, {f.name for f in fields(MultiRolloutCandidateRecord)})
        raw = dict(raw)
        raw["binding"] = MethodBinding.from_dict(raw["binding"])
        raw["edge"] = MethodCompositionEdge.from_dict(raw["edge"])
        candidates.append(typed(raw, MultiRolloutCandidateRecord))
    plans = []
    for raw in data["plans"]:
        shape(raw, {f.name for f in fields(RunPlan)})
        raw = dict(raw)
        patches = []
        for item in raw["patches"]:
            shape(item, {"stage", "instruction", "candidates"})
            patches.append(
                StagePatch(
                    item["stage"],
                    item["instruction"],
                    tuple(AgentRef.from_dict(a) for a in item["candidates"]),
                )
            )
        raw["patches"], raw["rerun"] = tuple(patches), tuple(raw["rerun"])
        plans.append(typed(raw, RunPlan))
    data["candidates"], data["plans"] = candidates, plans
    return MultiRolloutRunRecord(**data)


def validate_control(snapshot):
    c = snapshot.multi
    root, body, edge = resolve_body(snapshot)
    if (c.controller_binding, c.body_binding, c.nest_edge) != (
        root.method_binding_id,
        body.method_binding_id,
        edge.edge_id,
    ):
        raise Invalid("controller composition references mismatch")
    if c.composition_digest != digest(
        {
            "bindings": [b.to_dict() for b in snapshot.bindings],
            "relations": [e.to_dict() for e in snapshot.relations],
        }
    ):
        raise Invalid("composition digest mismatch")
    if encode(c.policy) != root.config.get("policy"):
        raise Invalid("controller policy mismatch")
    bounded(c.tenant_quota, 1, 8, "tenant_quota")
    positive_id(c.generation)
    if c.phase not in {
        "CREATED",
        "SAMPLING",
        "EXECUTING",
        "SELECTING",
        "ADAPTING",
        "WAITING_FEEDBACK",
        *FINAL_PHASES,
    }:
        raise Invalid("unknown controller phase")
    if (
        type(c.created_at) is not int
        or c.created_at <= 0
        or c.deadline != c.created_at + c.policy.limits.max_elapsed_seconds
    ):
        raise Invalid("invalid absolute controller deadline")
    for name, maximum in (
        ("dispatches", c.policy.limits.max_stage_dispatches),
        ("strategy_calls", c.policy.limits.max_strategy_calls),
        ("no_progress", c.policy.limits.max_no_progress),
        ("peak_concurrency", 8),
    ):
        bounded(getattr(c, name), 0, maximum, name)
    if type(c.waiting_until) is not int:
        raise Invalid("feedback deadline must be an integer")
    if c.waiting_until and (
        type(c.waiting_until) is not int
        or not c.created_at <= c.waiting_until <= c.deadline
    ):
        raise Invalid("invalid persisted feedback deadline")
    if not 1 <= len(c.candidates) <= c.policy.limits.max_rollouts or not c.plans:
        raise Invalid("candidate/plan budget mismatch")
    if (
        isinstance(c.policy.strategy, ParallelBestOf)
        and len(c.candidates) != c.policy.strategy.samples
    ):
        raise Invalid("parallel candidate batch is incomplete")
    if {v.rollout_id for v in c.candidates} != {
        r.rollout_id for r in snapshot.rollouts
    } or len(c.candidates) != len(snapshot.rollouts):
        raise Invalid("candidate membership mismatch")
    if [v.ordinal for v in c.candidates] != list(range(1, len(c.candidates) + 1)):
        raise Invalid("candidate order is not canonical")
    plans = {p.digest: p for p in c.plans}
    if len(plans) != len(c.plans):
        raise Invalid("duplicate plan digest")
    for i, plan in enumerate(c.plans):
        expected = make_plan(
            plan.profile_digest,
            plan.previous,
            plan.patches,
            plan.rerun,
            plan.evidence_digest,
            plan.version,
        )
        if (
            expected.digest != plan.digest
            or plan.version != i + 1
            or plan.previous != (c.plans[i - 1].digest if i else "")
        ):
            raise Invalid("invalid immutable plan chain")
        names = {s.name for s in snapshot.specs}
        if (
            len({p.stage for p in plan.patches}) != len(plan.patches)
            or not set(plan.rerun) <= names
        ):
            raise Invalid("invalid plan stage scope")
        for patch in plan.patches:
            spec = next((s for s in snapshot.specs if s.name == patch.stage), None)
            text(patch.instruction, maximum=8192, empty=True)
            if spec is None or any(a not in spec.candidates for a in patch.candidates):
                raise Invalid("plan expands frozen candidates")
    for candidate in c.candidates:
        positive_id(candidate.rollout_id)
        if (
            type(candidate.generation) is not int
            or not 1 <= candidate.generation <= c.generation
            or candidate.plan_digest not in plans
        ):
            raise Invalid("invalid candidate generation/plan")
        if any(
            type(v) is not bool
            for v in (candidate.slot, candidate.started, candidate.incomplete)
        ):
            raise Invalid("candidate flags must be booleans")
        if (
            candidate.namespace
            != f"{c.run_id}/g{candidate.generation}/candidate-{candidate.rollout_id}"
        ):
            raise Invalid("candidate isolation namespace mismatch")
        expected_binding = body.method_binding_id + f":run:{candidate.rollout_id}"
        if candidate.binding != replace(
            body,
            method_binding_id=expected_binding,
            bind_reason="isolated MultiRollout candidate",
        ) or candidate.edge != replace(
            edge,
            edge_id=f"{edge.edge_id}:run:{candidate.rollout_id}",
            child_binding=expected_binding,
        ):
            raise Invalid("candidate derived binding/edge mismatch")
        if (
            candidate.binding.method_binding_id != expected_binding
            or candidate.binding.state is not BindingState.ACTIVE
            or candidate.binding.config != body.config
            or candidate.binding.profile_versions != body.profile_versions
            or candidate.binding.collab_context_id != body.collab_context_id
            or candidate.edge.parent_binding != root.method_binding_id
            or candidate.edge.child_binding != expected_binding
            or candidate.edge.relation_kind != edge.relation_kind
            or candidate.edge.state != edge.state
        ):
            raise Invalid("candidate body governance mismatch")
    routes = [r.route.session_id for r in snapshot.records if r.route]
    if len(routes) != len(set(routes)):
        raise Invalid("candidate sessions must be independent")
    if c.winner:
        winner = next((r for r in snapshot.rollouts if r.rollout_id == c.winner), None)
        if (
            c.phase != "SUCCEEDED"
            or winner is None
            or winner.status is not S.ACCEPTANCE_SUCCEED
            or snapshot.current_rollout_id != c.winner
        ):
            raise Invalid("winner must be a qualified candidate and focus")
    elif c.phase == "SUCCEEDED":
        raise Invalid("succeeded controller has no winner")
    if c.operation:
        op = c.operation
        if (
            op.generation != c.generation
            or op.kind not in {"select", "adapt", "interactive"}
            or type(op.lease_until) is not int
        ):
            raise Invalid("invalid strategy operation fence")
        for value in (op.key, op.payload_digest, op.fence):
            text(value, maximum=256)

    if c.run_id != f"multi:{snapshot.collet.id}":
        raise Invalid("controller run identity mismatch")
    if len(c.strategy_attempts) != c.strategy_calls:
        raise Invalid("strategy reservation history mismatch")
    for i, attempt in enumerate(c.strategy_attempts):
        shape(
            attempt,
            {
                "key",
                "kind",
                "fence",
                "generation",
                "ordinal",
                "started_at",
                "state",
                "elapsed_ms",
            },
        )
        if (
            attempt["ordinal"] != i + 1
            or attempt["state"] not in {"in_flight", "unknown", "committed", "fenced"}
            or attempt["kind"] not in {"select", "adapt", "interactive"}
        ):
            raise Invalid("invalid strategy reservation facts")
        bounded(attempt["elapsed_ms"], 0, 86400000, "strategy elapsed_ms")
    if c.operation and c.operation.fence not in {
        a["fence"] for a in c.strategy_attempts
    }:
        raise Invalid("strategy operation has no pre-call reservation")
    ids = {s.stage_rollout_id for r in snapshot.rollouts for s in r.stages}
    if len(snapshot.records) != len(ids) or any(
        str(sid) not in {str(v) for v in ids}
        or state not in {"pending", "requested", "unknown", "confirmed"}
        for sid, state in c.cancellations.items()
    ):
        raise Invalid("invalid stage/cancellation membership")
    for i, candidate in enumerate(c.candidates):
        parallel = isinstance(c.policy.strategy, ParallelBestOf)
        if candidate.predecessor != (
            0 if parallel or i == 0 else c.candidates[i - 1].rollout_id
        ) or candidate.generation != (1 if parallel else i + 1):
            raise Invalid("invalid candidate predecessor/generation chain")
    for rollout in snapshot.rollouts:
        if len(rollout.stages) != len(snapshot.specs):
            raise Invalid("duplicate/incomplete candidate stages")
        for stage in rollout.stages:
            record = next(
                r for r in snapshot.records if r.stage_id == stage.stage_rollout_id
            )
            if record.dispatch_state not in {
                "pending",
                "unknown",
                "delivered",
                "revoked",
                "reused",
            }:
                raise Invalid("unknown dispatch state")
            if record.route:
                for value in asdict(record.route).values():
                    text(value, maximum=2048)
                if not record.attempt:
                    raise Invalid("real route has no persisted allocation attempt")
    from ...l5_methods.multi_rollout_state import project

    projected = deepcopy(snapshot)
    project(projected)
    if projected.collet.status != snapshot.collet.status or (
        c.phase == "STOPPED" and not snapshot.stopped
    ):
        raise Invalid("controller/business projection mismatch")
    for decision in c.decisions:
        if decision.get("kind") == "selection":
            shape(
                decision,
                {
                    "kind",
                    "generation",
                    "evidence",
                    "descriptor",
                    "scores",
                    "winner",
                    "actor",
                },
            )
            if decision["winner"] != c.winner:
                raise Invalid("committed selection cannot change winner")
            seen = set()
            for score in decision["scores"]:
                shape(score, {"rollout_id", "score", "reason"})
                if (
                    score["rollout_id"] in seen
                    or score["rollout_id"]
                    not in {
                        r.rollout_id
                        for r in snapshot.rollouts
                        if r.status is S.ACCEPTANCE_SUCCEED
                    }
                    or type(score["score"]) not in {float, int}
                    or (type(score["score"]) is float and not isfinite(score["score"]))
                ):
                    raise Invalid("invalid committed selection score")
                seen.add(score["rollout_id"])
        elif decision.get("kind") == "adjustment":
            shape(
                decision,
                {
                    "kind",
                    "generation",
                    "key",
                    "source",
                    "plan",
                    "proposal",
                    "feedback_digest",
                    "actor",
                },
            )
        else:
            raise Invalid("unknown committed decision")
