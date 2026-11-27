"""Scoped SQL unit of work for profiles, runtime snapshots, receipts and intents."""

from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
from sqlalchemy.exc import IntegrityError
from sqlalchemy import func
from injector import inject
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError as Missing,
    ContextCollabVersionConflict as Conflict,
    ContextCollabValidationError as Invalid,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import (
    Collet,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.product_status import (
    StatusEnum,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.product_values import (
    positive_id,
    text,
)
from agentclaw.community.core.task.context_collab.l5_methods.rollout_contracts import (
    RuntimeSnapshot,
)
from agentclaw.community.core.task.context_collab.l5_methods.rollout_lifecycle import (
    append_rollout,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    MethodBinding,
    BindingState,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_composition import (
    MethodCompositionEdge,
    CompositionEdgeState,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_policies import (
    SwarmStagesPolicy,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile import (
    ProductProfile,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.profile_repository import (
    profile_axes,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.rollout_models import (
    ProductConfigurationRow as Profile,
    ProductRuntimeRow as Runtime,
    ProductIdentityRow as Identity,
    ProductReceiptRow as Receipt,
    ProductIntentRow as Intent,
    ProductCandidateSlotRow as Slot,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.configuration import (
    encode_profile,
    decode_profile,
    encode_composition,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.stages_yaml import (
    compile_stages,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.snapshot_codec import (
    canonical,
    load,
    encode_snapshot,
    decode_snapshot,
)


from .runtime_outbox import RuntimeOutbox
from ...l5_methods.multi_rollout_state import initialize, append_candidate, digest
from ...l5_methods.multi_rollout_policy import ParallelBestOf
from ..product_profile_service.configuration import multi_policy
from time import time


class SqlProductRuntimeRepository(RuntimeOutbox):
    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self.db = db

    @staticmethod
    def _query(session, model):
        axes = profile_axes()
        return session.query(model).filter(
            model.avernet_tenant == axes.avernet_tenant, model.env == axes.env
        )

    @staticmethod
    def _axes():
        axes = profile_axes()
        return {"avernet_tenant": axes.avernet_tenant, "env": axes.env}

    @staticmethod
    def _keys(context, payload):
        return (
            sha256(canonical(context.actor.to_dict()).encode()).hexdigest(),
            sha256(canonical(payload).encode()).hexdigest(),
        )

    def _replay(self, session, request_id, operation, payload, context):
        text(request_id, maximum=128)
        actor, digest = self._keys(context, payload)
        row = (
            self._query(session, Receipt)
            .filter(Receipt.actor_key == actor, Receipt.request_id == request_id)
            .one_or_none()
        )
        if row is None:
            return None
        if row.operation != operation or row.request_digest != digest:
            raise Conflict(
                "request_id already used with different content or operation"
            )
        return load(row.result_json)

    def replay(self, request_id, operation, payload, context):
        with self.db.orm_session() as session:
            return self._replay(session, request_id, operation, payload, context)

    def _receipt(self, session, request_id, operation, payload, context, result):
        actor, digest = self._keys(context, payload)
        session.add(
            Receipt(
                **self._axes(),
                actor_key=actor,
                request_id=request_id,
                operation=operation,
                request_digest=digest,
                result_json=canonical(result),
            )
        )
        session.flush()

    def get_profile(self, profile_id):
        positive_id(profile_id)
        with self.db.orm_session() as session:
            row = (
                self._query(session, Profile)
                .filter(Profile.id == profile_id)
                .one_or_none()
            )
            if row is None:
                raise Missing("ProductProfile not found in this scope")
            profile = decode_profile(load(row.definition_json))
            if profile.id != row.id:
                raise Invalid("profile storage identity mismatch")
            return profile, PrincipalRef.from_dict(load(row.owner_json))

    def create_profile(self, name, composition, stages, context, request_id):
        payload = {
            "name": name,
            "composition": encode_composition(composition),
            "stages": stages,
        }
        try:
            with self.db.transactional_orm_session() as session:
                replay = self._replay(
                    session, request_id, "create_profile", payload, context
                )
                if replay is not None:
                    return decode_profile(replay)
                row = Profile(
                    **self._axes(),
                    definition_json="",
                    owner_json=canonical(context.actor.to_dict()),
                )
                session.add(row)
                session.flush()
                profile = ProductProfile(row.id, name, composition, stages)
                result = encode_profile(profile)
                row.definition_json = canonical(result)
                self._receipt(
                    session, request_id, "create_profile", payload, context, result
                )
                return profile
        except IntegrityError:
            replay = self.replay(request_id, "create_profile", payload, context)
            if replay is None:
                raise
            return decode_profile(replay)

    def _identity(self, session, kind, collet_id):
        row = Identity(**self._axes(), kind=kind, collet_id=collet_id)
        session.add(row)
        session.flush()
        if kind == "collet":
            row.collet_id = row.id
        return row.id

    def create_collet(self, profile, content, context, request_id, *, multi_setup=None):
        payload = {"profile_id": profile.id, "content": content}
        try:
            with self.db.transactional_orm_session() as session:
                replay = self._replay(
                    session, request_id, "create_collet", payload, context
                )
                if replay is not None:
                    return self._get(session, replay["id"])
                cid = self._identity(session, "collet", 0)
                now = datetime.now(timezone.utc).isoformat()
                bindings = tuple(
                    MethodBinding(
                        method_binding_id=f"product-v2:{cid}:{d.profile.method_kind}",
                        collab_context_id=f"product-v2:{cid}",
                        method_kind=d.profile.method_kind,
                        method_names=(d.profile.method_kind,),
                        profile_versions={
                            d.profile.method_kind: d.profile.profile_version
                        },
                        config={
                            "policy": encode_composition(profile.composition)[
                                "required_methods"
                            ][index]["policy"]
                        },
                        state=BindingState.ACTIVE,
                        bind_reason="product profile instantiation",
                        transitioned_from="BOUND",
                        transition_reason="authorized product creation",
                        transitioned_by=canonical(context.actor.to_dict()),
                        transitioned_at=now,
                    )
                    for index, d in enumerate(profile.composition.required_methods)
                )
                by_kind = {b.method_kind: b.method_binding_id for b in bindings}
                relations = tuple(
                    MethodCompositionEdge(
                        f"product-v2:{cid}:edge:{i}",
                        f"product-v2:{cid}",
                        by_kind[r.source_method],
                        by_kind[r.target_method],
                        r.relation,
                        CompositionEdgeState.ACTIVE,
                        "",
                        "profile composition",
                        canonical(context.actor.to_dict()),
                        now,
                    )
                    for i, r in enumerate(profile.composition.relations)
                )
                policy = next(
                    d.policy
                    for d in profile.composition.required_methods
                    if type(d.policy) is SwarmStagesPolicy
                )
                snapshot = RuntimeSnapshot(
                    Collet(cid, profile.id, content, StatusEnum.CREATE),
                    context.actor,
                    1,
                    0,
                    [],
                    [],
                    compile_stages(profile.stages),
                    bindings,
                    relations,
                    policy.execution_ref,
                    policy.acceptance_ref,
                    policy.overall_acceptance,
                )
                selected_multi = multi_policy(profile.composition)
                if selected_multi:
                    if multi_setup is None:
                        raise Invalid(
                            "MultiRollout requires governed host capability admission"
                        )
                    initialize(
                        snapshot,
                        selected_multi,
                        multi_setup,
                        int(time()),
                        digest(encode_profile(profile)),
                    )
                    count = (
                        selected_multi.strategy.samples
                        if isinstance(selected_multi.strategy, ParallelBestOf)
                        else 1
                    )
                    for _ in range(count):
                        append_candidate(
                            snapshot,
                            lambda kind: self._identity(session, kind, cid),
                            content,
                        )
                else:
                    append_rollout(
                        snapshot,
                        lambda kind: self._identity(session, kind, cid),
                        content,
                        {s.name for s in snapshot.specs},
                    )
                session.add(
                    Runtime(
                        **self._axes(),
                        id=cid,
                        profile_id=profile.id,
                        revision=1,
                        snapshot_json=encode_snapshot(snapshot),
                    )
                )
                session.flush()
                initial = (
                    tuple(
                        {"kind": "advance-cand", "rollout_id": c.rollout_id}
                        for c in snapshot.multi.candidates
                    )
                    if snapshot.multi
                    else (
                        {"kind": "advance", "rollout_id": snapshot.current_rollout_id},
                    )
                )
                self._enqueue(session, snapshot, initial)
                self._receipt(
                    session,
                    request_id,
                    "create_collet",
                    payload,
                    context,
                    asdict(snapshot.collet),
                )
                return snapshot
        except IntegrityError:
            replay = self.replay(request_id, "create_collet", payload, context)
            if replay is None:
                raise
            return self.get_collet(replay["id"])

    def _get(self, session, collet_id):
        # Read the CAS snapshot and its admission facts in ONE SQL statement.
        # SQLite's legacy SELECT transaction mode (and READ COMMITTED hosts)
        # does not promise a stable snapshot across two separate SELECTs.
        # At most eight tenant slots => the numeric aggregate is < 160 bytes,
        # below both supported databases' GROUP_CONCAT limits.
        slot_ids = (
            self._query(session, Slot)
            .with_entities(func.group_concat(Slot.rollout_id))
            .filter(Slot.collet_id == Runtime.id)
            .correlate(Runtime)
            .scalar_subquery()
        )
        result = (
            self._query(session, Runtime)
            .with_entities(Runtime, slot_ids)
            .filter(Runtime.id == collet_id)
            .populate_existing()
            .one_or_none()
        )
        if result is None:
            raise Missing("Collet not found in this scope")
        row, slot_csv = result
        actual_slots = {int(value) for value in (slot_csv or "").split(",") if value}
        snapshot = decode_snapshot(row.snapshot_json)
        if (
            snapshot.collet.id != row.id
            or snapshot.collet.profile_id != row.profile_id
            or snapshot.revision != row.revision
        ):
            raise Invalid("runtime row identity/revision mismatch")
        profile_row = (
            self._query(session, Profile)
            .filter(Profile.id == row.profile_id)
            .one_or_none()
        )
        if profile_row is None:
            raise Invalid("runtime configuration is missing")
        profile = decode_profile(load(profile_row.definition_json))
        declarations = profile.composition.required_methods
        policies = [
            d.policy for d in declarations if type(d.policy) is SwarmStagesPolicy
        ]
        selected = {d.profile.method_kind: d for d in declarations}
        expected_policies = {
            d.profile.method_kind: encode_composition(profile.composition)[
                "required_methods"
            ][i]["policy"]
            for i, d in enumerate(declarations)
        }
        if (
            snapshot.specs != compile_stages(profile.stages)
            or len(policies) != 1
            or (
                snapshot.execution_ref,
                snapshot.acceptance_ref,
                snapshot.overall_acceptance,
            )
            != (
                policies[0].execution_ref,
                policies[0].acceptance_ref,
                policies[0].overall_acceptance,
            )
            or len(snapshot.bindings) != len(selected)
            or {b.method_kind for b in snapshot.bindings} != set(selected)
            or any(
                b.state is not BindingState.ACTIVE
                or b.collab_context_id != f"product-v2:{row.id}"
                or b.profile_versions
                != {b.method_kind: selected[b.method_kind].profile.profile_version}
                or b.config.get("policy") != expected_policies[b.method_kind]
                for b in snapshot.bindings
            )
        ):
            raise Invalid(
                "persisted Method/configuration prerequisites are incomplete or conflicting"
            )
        by_id = {b.method_binding_id: b.method_kind for b in snapshot.bindings}
        actual_relations = {
            (by_id.get(e.parent_binding), by_id.get(e.child_binding), e.relation_kind)
            for e in snapshot.relations
            if e.state is CompositionEdgeState.ACTIVE
            and e.collab_context_id == f"product-v2:{row.id}"
        }
        expected_relations = {
            (r.source_method, r.target_method, r.relation)
            for r in profile.composition.relations
        }
        if (
            len(actual_relations) != len(snapshot.relations)
            or actual_relations != expected_relations
        ):
            raise Invalid("persisted Method relation prerequisites are incomplete")
        selected_multi = multi_policy(profile.composition)
        if bool(selected_multi) != bool(snapshot.multi):
            raise Invalid("runtime schema does not match the published composition")
        if snapshot.multi and (
            snapshot.multi.policy != selected_multi
            or any(
                p.profile_digest != digest(encode_profile(profile))
                for p in snapshot.multi.plans
            )
        ):
            raise Invalid("frozen policy/plan profile digest mismatch")
        if snapshot.multi:
            if actual_slots != {
                c.rollout_id for c in snapshot.multi.candidates if c.slot
            }:
                raise Invalid(
                    "persisted candidate admission facts do not match snapshot"
                )
        return snapshot

    def get_collet(self, collet_id):
        positive_id(collet_id)
        with self.db.orm_session() as session:
            return self._get(session, collet_id)

    def locate(self, identity, kind):
        positive_id(identity)
        with self.db.orm_session() as session:
            row = (
                self._query(session, Identity)
                .filter(Identity.id == identity, Identity.kind == kind)
                .one_or_none()
            )
            if row is None:
                raise Missing("execution identity not found in this scope")
            return self._get(session, row.collet_id)

    def save(
        self,
        snapshot,
        revision,
        *,
        receipt=None,
        intents=(),
        successor=None,
        acquire_slot=None,
        release_slots=(),
    ):
        positive_id(revision)
        try:
            with self.db.transactional_orm_session() as session:
                if receipt:
                    request_id, operation, payload, context, result = receipt
                    replay = self._replay(
                        session, request_id, operation, payload, context
                    )
                    if replay is not None:
                        return replay
                if successor:
                    content, affected = successor
                    if snapshot.multi:
                        append_candidate(
                            snapshot,
                            lambda kind: self._identity(
                                session, kind, snapshot.collet.id
                            ),
                            content,
                        )
                        intents = (
                            *intents,
                            {
                                "kind": "advance-cand",
                                "rollout_id": snapshot.current_rollout_id,
                            },
                        )
                    else:
                        append_rollout(
                            snapshot,
                            lambda kind: self._identity(
                                session, kind, snapshot.collet.id
                            ),
                            content,
                            affected,
                        )
                snapshot.revision = revision + 1
                updated = (
                    self._query(session, Runtime)
                    .filter(
                        Runtime.id == snapshot.collet.id, Runtime.revision == revision
                    )
                    .update(
                        {
                            Runtime.revision: snapshot.revision,
                            Runtime.snapshot_json: encode_snapshot(snapshot),
                        },
                        synchronize_session=False,
                    )
                )
                if updated != 1:
                    raise Conflict("stale product revision")
                self._slots(session, snapshot, acquire_slot, release_slots)
                # Older advance intents are superseded atomically. Cancellation
                # intents are retained until confirmed, even across new iterations.
                self._query(session, Intent).filter(
                    Intent.collet_id == snapshot.collet.id,
                    Intent.state == "pending",
                    Intent.kind == "advance",
                ).update({Intent.state: "superseded"}, synchronize_session=False)
                self._enqueue(session, snapshot, intents)
                if receipt:
                    self._receipt(
                        session, request_id, operation, payload, context, result
                    )
                    return result
                session.flush()
        except IntegrityError:
            if not receipt:
                raise
            replay = self.replay(receipt[0], receipt[1], receipt[2], receipt[3])
            if replay is None:
                raise
            return replay

    def late_delivery(self, collet_id, stage_id, attempt, route):
        # An append-only delivery observation survives a stop/replan CAS race.
        with self.db.transactional_orm_session() as session:
            snapshot = self._get(session, collet_id)
            record = next((r for r in snapshot.records if r.stage_id == stage_id), None)
            if record is None or record.attempt != attempt:
                raise Invalid("late delivery identity mismatch")
            session.add(
                Intent(
                    **self._axes(),
                    collet_id=collet_id,
                    revision=snapshot.revision,
                    kind="cancel",
                    payload_json=canonical(
                        {
                            "kind": "cancel",
                            "stage_id": stage_id,
                            "attempt": attempt,
                            "route": asdict(route),
                        }
                    ),
                    state="pending",
                )
            )
