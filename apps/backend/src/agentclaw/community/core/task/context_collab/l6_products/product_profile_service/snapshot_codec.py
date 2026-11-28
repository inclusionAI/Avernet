"""Closed runtime snapshot codec, including durable governance and routing facts."""

from dataclasses import asdict, fields
import json
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError as Invalid,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.artifact import (
    Artifact,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.acceptance_result import (
    AcceptanceResult,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import (
    Collet,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.product_status import (
    StatusEnum,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.product_values import (
    positive_id,
    shape,
)
from agentclaw.community.core.task.context_collab.l5_methods.stage_rollout import (
    StageRollout,
)
from agentclaw.community.core.task.context_collab.l5_methods.collet_rollout import (
    ColletRollout,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    MethodBinding,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_composition import (
    MethodCompositionEdge,
)
from agentclaw.community.core.task.context_collab.l5_methods.rollout_contracts import (
    RuntimeSnapshot,
    StageRecord,
    CompiledStage,
    SessionRoute,
)

MAX_SNAPSHOT_BYTES = 12 * 1024 * 1024


def canonical(data: object) -> str:
    return json.dumps(
        data, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise Invalid("duplicate stored JSON key")
        result[key] = value
    return result


def load(value: str) -> dict:
    if type(value) is not str or len(value.encode()) > MAX_SNAPSHOT_BYTES:
        raise Invalid("stored snapshot exceeds limit")
    try:
        return json.loads(
            value,
            object_pairs_hook=_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(Invalid("nonfinite JSON")),
        )
    except (ValueError, RecursionError) as exc:
        raise Invalid("invalid stored JSON") from exc


def encode_snapshot(snapshot: RuntimeSnapshot) -> str:
    data = asdict(snapshot)
    data["owner"] = snapshot.owner.to_dict()
    data["bindings"] = [b.to_dict() for b in snapshot.bindings]
    data["relations"] = [e.to_dict() for e in snapshot.relations]
    data["specs"] = [
        {
            "name": s.name,
            "candidates": [a.to_dict() for a in s.candidates],
            "depends_on": s.depends_on,
            "conditions": s.conditions,
        }
        for s in snapshot.specs
    ]
    if snapshot.multi is None:
        data.pop("multi")  # Explicit legacy branch: old bytes/shape remain readable.
    else:
        from .multi_snapshot_codec import encode_control, validate_control

        validate_control(snapshot)
        from ...l5_methods.multi_rollout_contracts import SCHEMA

        data["schema"] = SCHEMA
        data["multi"] = encode_control(snapshot.multi)
    # Stream the bounded wire: a legal 32x100 graph can contain much more
    # than 12 MiB of repeated context. Do not materialize a giant JSON string
    # just to discover that it exceeds the limit.
    chunks, size = [], 0
    encoder = json.JSONEncoder(
        sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )
    for chunk in encoder.iterencode(data):
        size += len(chunk.encode())
        if size > MAX_SNAPSHOT_BYTES:
            raise Invalid(
                "runtime history exceeds snapshot limit; archive before continuing"
            )
        chunks.append(chunk)
    return "".join(chunks)


def decode_snapshot(value: str) -> RuntimeSnapshot:
    data = load(value)
    if type(data) is not dict:
        raise Invalid("stored runtime must be an object")
    from ...l5_methods.multi_rollout_contracts import SCHEMA
    from .multi_snapshot_codec import decode_control, validate_control

    names = {f.name for f in fields(RuntimeSnapshot)}
    if "schema" in data:
        if data.pop("schema") != SCHEMA:
            raise Invalid("unknown runtime snapshot schema")
        shape(data, names)
        data["multi"] = decode_control(data["multi"])
    else:
        shape(data, names - {"multi"})
        data["multi"] = None
    shape(data["collet"], {f.name for f in fields(Collet)})
    collet = dict(data["collet"])
    collet["status"] = StatusEnum(collet["status"])
    rollouts = []
    for raw in data["rollouts"]:
        shape(raw, {f.name for f in fields(ColletRollout)})
        stages = []
        for item in raw["stages"]:
            shape(item, {f.name for f in fields(StageRollout)})
            item = dict(item)
            positive_id(item["stage_rollout_id"])
            item["status"] = StatusEnum(item["status"])
            item["artifacts"] = [Artifact.from_dict(a) for a in item["artifacts"]]
            for acceptance in item["acceptances"]:
                shape(acceptance, {"condition", "passed", "reason"})
            item["acceptances"] = [AcceptanceResult(**a) for a in item["acceptances"]]
            stages.append(StageRollout(**item))
        positive_id(raw["rollout_id"])
        rollouts.append(
            ColletRollout(
                raw["collet_id"],
                raw["rollout_id"],
                stages,
                raw["content"],
                StatusEnum(raw["status"]),
            )
        )
    records = []
    for item in data["records"]:
        shape(item, {f.name for f in fields(StageRecord)})
        item = dict(item)
        if item["route"] is not None:
            shape(item["route"], {f.name for f in fields(SessionRoute)})
            item["route"] = SessionRoute(**item["route"])
        records.append(StageRecord(**item))
    specs = []
    for item in data["specs"]:
        shape(item, {f.name for f in fields(CompiledStage)})
        specs.append(
            CompiledStage(
                item["name"],
                tuple(AgentRef.from_dict(a) for a in item["candidates"]),
                tuple(item["depends_on"]),
                tuple(item["conditions"]),
            )
        )
    snapshot = RuntimeSnapshot(
        **{
            **data,
            "collet": Collet(**collet),
            "owner": PrincipalRef.from_dict(data["owner"]),
            "rollouts": rollouts,
            "records": records,
            "specs": tuple(specs),
            "bindings": tuple(MethodBinding.from_dict(b) for b in data["bindings"]),
            "relations": tuple(
                MethodCompositionEdge.from_dict(e) for e in data["relations"]
            ),
        }
    )
    positive_id(snapshot.revision)
    positive_id(snapshot.current_rollout_id)
    # Missing governance/routing facts must not silently reconstruct execution.
    stage_ids = [s.stage_rollout_id for r in rollouts for s in r.stages]
    if (
        not snapshot.bindings
        or len(stage_ids) != len(set(stage_ids))
        or set(stage_ids) != {r.stage_id for r in records}
    ):
        raise Invalid("incomplete runtime governance or stage identity facts")
    if len([r for r in rollouts if r.rollout_id == snapshot.current_rollout_id]) != 1:
        raise Invalid("missing or ambiguous current rollout")
    for r in rollouts:
        if r.collet_id != snapshot.collet.id or {s.name for s in r.stages} != {
            s.name for s in specs
        }:
            raise Invalid("runtime stage/collet scope mismatch")
        for s in r.stages:
            record = next(v for v in records if v.stage_id == s.stage_rollout_id)
            if (record.route.session_id if record.route else "") != s.session_id:
                raise Invalid("session route does not match the persisted stage")
    if snapshot.multi:
        validate_control(snapshot)
    elif any(b.method_kind == "MultiRollout" for b in snapshot.bindings):
        raise Invalid("MultiRollout cannot use a legacy snapshot")
    return snapshot
