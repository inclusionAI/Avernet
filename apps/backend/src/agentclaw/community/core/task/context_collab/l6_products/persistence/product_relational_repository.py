"""Storage-only business UoW; deliberately NOT the snapshot RolloutRepository.

This implements the independently representable creation/read operations. The
legacy engine cannot consume these views: bindings, attempts, leases, routes,
CAS revisions and MultiRollout control facts have no approved durable mapping.
Do not bind this repository to the old runtime Protocol or start its worker.
"""

from contextlib import contextmanager
from dataclasses import asdict
import json
from uuid import uuid4

from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.core.task.context_collab.l5_methods.rollout_contracts import (
    ProductRuntimeAuthorization,
)
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError as Missing,
    ContextCollabValidationError as Invalid,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.profile_publication import (
    ProductProfilePublicationAuthorizer,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.profile_use import (
    ProductProfileUseAuthorizer,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import (
    Collet,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.product_status import (
    StatusEnum as S,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.product_values import (
    positive_id,
    shape,
    text,
)
from agentclaw.community.core.task.context_collab.l5_methods.collet_rollout import (
    ColletRollout,
)
from agentclaw.community.core.task.context_collab.l5_methods.product_business_storage import (
    ProductBusinessHistory,
)
from ..product_profile import ProductProfileStatus
from ..profile_record import ProductProfileRecord
from ..product_profile_service.configuration import compile_configuration, multi_policy
from ..product_profile_service.profile_config_yaml import encode_config
from ..product_profile_service.snapshot_codec import canonical, load, MAX_SNAPSHOT_BYTES
from .product_relational_models import (
    ProductProfileRow,
    ColletRow,
    ColletRolloutRow,
    StageRolloutRow,
)
from .runtime_relational_codec import (
    audit_identity,
    profile_from_row,
    stage_from_row,
    status,
)
from .runtime_event_writer import append_creation_event


class SqlProductBusinessRepository:
    def __init__(
        self,
        db: DatabasePlugin,
        publisher: ProductProfilePublicationAuthorizer,
        profile_use: ProductProfileUseAuthorizer,
        authorization: ProductRuntimeAuthorization,
    ) -> None:
        self.db, self.publisher, self.profile_use = db, publisher, profile_use
        self.authorization = authorization

    @staticmethod
    def _axes():
        return {
            "avernet_tenant": get_current_avernet_tenant(),
            "env": get_current_env(),
        }

    @staticmethod
    def _query(session, model, axes):
        return session.query(model).filter(
            model.avernet_tenant == axes["avernet_tenant"], model.env == axes["env"]
        )

    @staticmethod
    def _exact(row, axes):
        if any(getattr(row, key) != value for key, value in axes.items()):
            raise Missing("business record not found in this exact scope")
        return row

    @contextmanager
    def _transaction(self, *, write=False):
        with self.db.transactional_orm_session() as session:
            dialect = session.get_bind().dialect.name
            if dialect == "sqlite":
                connection = session.connection()
                # SQLAlchemy's logical transaction may not have issued BEGIN
                # under sqlite3 legacy mode. Explicit BEGIN is needed for reads
                # too, otherwise multi-table SELECTs can observe mixed commits.
                # The real local/community SQLite plugins already issue
                # BEGIN IMMEDIATE for this Protocol. Respect their transaction;
                # only supply BEGIN for adapters using sqlite3 legacy mode.
                if not connection.connection.driver_connection.in_transaction:
                    connection.exec_driver_sql("BEGIN IMMEDIATE" if write else "BEGIN")
            elif dialect == "mysql":
                session.connection(
                    execution_options={"isolation_level": "REPEATABLE READ"}
                )
            else:
                raise Invalid(
                    "business storage requires validated SQLite/MySQL transactions"
                )
            yield session

    def create_profile(
        self, name, composition, stages, context: ActorContext, *, description=""
    ) -> ProductProfileRecord:
        text(name, maximum=256)
        if type(description) is not str or len(description) > 2048:
            raise Invalid("description exceeds 2048 characters")
        self.publisher.check_publish(name, 1, context)
        audit = audit_identity(context)
        config = encode_config(composition, stages)
        axes = self._axes()
        with self._transaction(write=True) as session:
            row = ProductProfileRow(
                **axes,
                name=name,
                description=description,
                config=config,
                status=ProductProfileStatus.DRAFT.value,
                creator=audit,
                modifer=audit,
            )
            session.add(row)
            session.flush()
            return profile_from_row(row)

    def _profile(self, session, profile_id, axes, *, lock=False):
        query = self._query(session, ProductProfileRow, axes).filter(
            ProductProfileRow.id == profile_id
        )
        row = (query.with_for_update() if lock else query).one_or_none()
        if row is None:
            raise Missing("ProductProfile not found in this scope")
        self._exact(row, axes)
        return profile_from_row(row)

    def get_profile(self, profile_id: int) -> ProductProfileRecord:
        positive_id(profile_id)
        with self._transaction() as session:
            return self._profile(session, profile_id, self._axes())

    def create_collet(
        self, profile_id: int, content: str, context: ActorContext
    ) -> ProductBusinessHistory:
        """Persist an admitted business graph; this does not dispatch any work.

        No public service/DI cutover is enabled yet. Do not treat this storage
        operation as proof that missing runtime capability gates have passed.
        """
        positive_id(profile_id)
        text(content)
        self.profile_use.check_use(profile_id, context)
        axes = self._axes()
        with self._transaction(write=True) as session:
            # Profile -> root -> rollout -> stages is the creation lock order.
            stored = self._profile(session, profile_id, axes, lock=True)
            profile = stored.definition
            if stored.status is not ProductProfileStatus.ONLINE:
                raise Invalid("only ONLINE ProductProfiles accept new Collets")
            specs, _policy = compile_configuration(profile.composition, profile.stages)
            row = ColletRow(
                **axes,
                collab_context_id=f"pending-product:{uuid4().hex}",
                actor_ref=canonical(context.actor.to_dict()),
                scope="null",
                creation_facts="{}",
                current_snapshot=None,
                root_task_ref=None,
                content=content,
                status=S.CREATE.value,
                product_profile_id=profile.id,
                current_rollout_id=None,
            )
            session.add(row)
            session.flush()
            positive_id(row.id)
            row.collab_context_id = f"product-v2:{row.id}"
            rollout = ColletRolloutRow(
                **axes,
                collet_id=row.id,
                ordinal=1,
                predecessor_id=None,
                content=content,
                status=S.CREATE.value,
            )
            session.add(rollout)
            session.flush()
            for spec in specs:
                session.add(
                    StageRolloutRow(
                        **axes,
                        collet_rollout_id=rollout.id,
                        name=spec.name,
                        status=S.CREATE.value,
                        input=content,
                        acceptances="[]",
                        artifacts="[]",
                        error_msg="",
                        assignee_type="",
                        assignee_uuid="",
                        session_id="",
                    )
                )
            session.flush()
            row.current_rollout_id = rollout.id
            append_creation_event(session, axes, row, rollout, context)
            session.flush()
            return self._read(session, row, axes)

    def _root(self, session, collet_id, axes):
        row = (
            self._query(session, ColletRow, axes)
            .filter(ColletRow.id == collet_id)
            .one_or_none()
        )
        if row is None or row.product_profile_id is None:
            raise Missing("product Collet not found in this scope")
        self._exact(row, axes)
        if (
            row.content is None
            or row.status is None
            or row.current_rollout_id is None
            or row.actor_ref is None
            or row.collab_context_id != f"product-v2:{row.id}"
            or row.root_task_ref is not None
            or row.current_snapshot is not None
        ):
            raise Invalid("incomplete or conflicting product Collet")
        return row

    def _read(self, session, root, axes) -> ProductBusinessHistory:
        self._root(session, root.id, axes)
        profile = self._profile(session, root.product_profile_id, axes).definition
        specs, _policy = compile_configuration(profile.composition, profile.stages)
        selected_multi = multi_policy(profile.composition)
        maximum = selected_multi.limits.max_rollouts if selected_multi else 32
        rolls = (
            self._query(session, ColletRolloutRow, axes)
            .filter(ColletRolloutRow.collet_id == root.id)
            .order_by(ColletRolloutRow.ordinal)
            .limit(maximum + 1)
            .all()
        )
        if not rolls or len(rolls) > maximum:
            raise Invalid("missing or excessive product rollout history")
        ids = {r.id for r in rolls}
        if root.current_rollout_id not in ids:
            raise Invalid("current rollout does not belong to this Collet")
        stages = (
            self._query(session, StageRolloutRow, axes)
            .filter(StageRolloutRow.collet_rollout_id.in_(ids))
            .limit(len(rolls) * len(specs) + 1)
            .all()
        )
        if len(stages) != len(rolls) * len(specs):
            raise Invalid("missing or excessive product stages")
        grouped = {rid: {} for rid in ids}
        for stage in stages:
            self._exact(stage, axes)
            by_name = grouped[stage.collet_rollout_id]
            if stage.name in by_name:
                raise Invalid("duplicate stored stage name")
            by_name[stage.name] = stage_from_row(stage)
        history, parents = [], {}
        for index, rollout in enumerate(rolls):
            self._exact(rollout, axes)
            predecessor = rolls[index - 1].id if index else None
            if rollout.ordinal != index + 1 or rollout.predecessor_id != predecessor:
                raise Invalid("invalid rollout order or predecessor chain")
            by_name = grouped[rollout.id]
            if set(by_name) != {s.name for s in specs}:
                raise Invalid("stored stages disagree with immutable Profile YAML")
            history.append(
                ColletRollout(
                    root.id,
                    rollout.id,
                    [by_name[s.name] for s in specs],
                    rollout.content,
                    status(rollout.status),
                )
            )
            if predecessor is not None:
                parents[rollout.id] = predecessor
        raw_owner = load(root.actor_ref)
        shape(raw_owner, {"principal_type", "principal_id"})
        owner = PrincipalRef.from_dict(raw_owner)
        result = ProductBusinessHistory(
            Collet(root.id, profile.id, root.content, status(root.status)),
            owner,
            root.current_rollout_id,
            tuple(history),
            parents,
        )
        size = 0
        for chunk in json.JSONEncoder(ensure_ascii=False, allow_nan=False).iterencode(
            asdict(result)
        ):
            size += len(chunk.encode())
            if size > MAX_SNAPSHOT_BYTES:
                raise Invalid("business aggregate exceeds 12 MiB")
        return result

    def get_collet(
        self, collet_id: int, context: ActorContext
    ) -> ProductBusinessHistory:
        positive_id(collet_id)
        with self._transaction() as session:
            axes = self._axes()
            root = self._root(session, collet_id, axes)
            owner = PrincipalRef.from_dict(load(root.actor_ref))
            self.authorization.check("read", context, owner)
            return self._read(session, root, axes)

    def locate(
        self, identity: int, kind: str, context: ActorContext
    ) -> ProductBusinessHistory:
        positive_id(identity)
        if kind not in {"stage", "rollout"}:
            raise Invalid("explicit stage/rollout identity kind is required")
        with self._transaction() as session:
            axes = self._axes()
            rollout_id = identity
            if kind == "stage":
                stage = (
                    self._query(session, StageRolloutRow, axes)
                    .filter(StageRolloutRow.id == identity)
                    .one_or_none()
                )
                if stage is None:
                    raise Missing("stage not found in this scope")
                self._exact(stage, axes)
                rollout_id = stage.collet_rollout_id
            rollout = (
                self._query(session, ColletRolloutRow, axes)
                .filter(ColletRolloutRow.id == rollout_id)
                .one_or_none()
            )
            if rollout is None:
                raise Missing("rollout not found in this scope")
            self._exact(rollout, axes)
            root = self._root(session, rollout.collet_id, axes)
            owner = PrincipalRef.from_dict(load(root.actor_ref))
            self.authorization.check("read", context, owner)
            return self._read(session, root, axes)
