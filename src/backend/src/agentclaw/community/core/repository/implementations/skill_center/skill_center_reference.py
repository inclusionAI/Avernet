"""ORM persistence for durable SC Public Reference operations."""

from __future__ import annotations

from uuid import NAMESPACE_URL, uuid5

from injector import inject
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.models.skill_center_reference import (
    SkillCenterReferenceBatchModel,
    SkillCenterReferenceItemModel,
)
from agentclaw.community.core.models.skill import Skill
from agentclaw.community.core.models.space_skill import SkillSpaceBinding, SkillVersion
from agentclaw.community.core.repository.protocols.skill_center_reference import (
    SkillCenterReferenceRepositoryProtocol,
)
from agentclaw.community.core.repository.skill_center_reference_types import (
    MaterializedPublicCenterAsset,
    PublicCenterVersionTarget,
    SkillCenterReferenceWorkBatch,
    SkillCenterReferenceWorkItem,
)
from agentclaw.community.core.skill_center.reference_contract import (
    ReferenceIdempotencyConflictError,
    SkillCenterReferenceBatch,
    SkillCenterReferenceCreateResult,
    SkillCenterReferenceItem,
    SkillCenterReferenceStatus,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant


class SkillCenterReferenceRepository(SkillCenterReferenceRepositoryProtocol):
    """Store one immutable command identity plus independently progressing items."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def get_batch_by_idempotency_key(
        self, *, env: str, idempotency_key: str
    ) -> tuple[SkillCenterReferenceBatch, str] | None:
        tenant = get_current_avernet_tenant()
        with self._db.orm_session() as session:
            row = (
                session.query(SkillCenterReferenceBatchModel)
                .filter(
                    SkillCenterReferenceBatchModel.avernet_tenant == tenant,
                    SkillCenterReferenceBatchModel.env == env,
                    SkillCenterReferenceBatchModel.idempotency_key == idempotency_key,
                )
                .one_or_none()
            )
            if row is None:
                return None
            return self._batch(session, row), row.request_hash

    def create_or_get_batch(
        self,
        *,
        env: str,
        bot_id: str,
        owner_id: str,
        skill_set_id: str,
        actor_id: str,
        idempotency_key: str,
        request_hash: str,
        skill_codes: tuple[str, ...],
        request_id: str,
        reference_ids: tuple[str, ...],
    ) -> SkillCenterReferenceCreateResult:
        if len(skill_codes) != len(reference_ids):
            raise ValueError("one reference_id is required per skill_code")
        tenant = get_current_avernet_tenant()
        with self._db.orm_session() as session:
            existing = (
                session.query(SkillCenterReferenceBatchModel)
                .filter(
                    SkillCenterReferenceBatchModel.avernet_tenant == tenant,
                    SkillCenterReferenceBatchModel.env == env,
                    SkillCenterReferenceBatchModel.idempotency_key == idempotency_key,
                )
                .with_for_update()
                .one_or_none()
            )
            if existing is not None:
                if existing.request_hash != request_hash:
                    raise ReferenceIdempotencyConflictError(
                        "Idempotency-Key was reused for a different Reference request"
                    )
                return SkillCenterReferenceCreateResult(
                    batch=self._batch(session, existing), created=False
                )

            batch = SkillCenterReferenceBatchModel(
                request_id=request_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                bot_id=bot_id,
                owner_id=owner_id,
                skill_set_id=skill_set_id,
                actor_id=actor_id,
                env=env,
                avernet_tenant=tenant,
            )
            try:
                # The unique idempotency constraint is the cross-process winner.
                # A savepoint lets the loser re-read the committed command without
                # invalidating the repository session or creating duplicate items.
                with session.begin_nested():
                    session.add(batch)
                    for skill_code, reference_id in zip(
                        skill_codes, reference_ids, strict=True
                    ):
                        session.add(
                            SkillCenterReferenceItemModel(
                                reference_id=reference_id,
                                request_id=request_id,
                                bot_id=bot_id,
                                owner_id=owner_id,
                                skill_set_id=skill_set_id,
                                actor_id=actor_id,
                                skill_code=skill_code,
                                status=SkillCenterReferenceStatus.QUEUED.value,
                                env=env,
                                avernet_tenant=tenant,
                            )
                        )
                    session.flush()
            except IntegrityError:
                existing = (
                    session.query(SkillCenterReferenceBatchModel)
                    .filter(
                        SkillCenterReferenceBatchModel.avernet_tenant == tenant,
                        SkillCenterReferenceBatchModel.env == env,
                        SkillCenterReferenceBatchModel.idempotency_key
                        == idempotency_key,
                    )
                    .one_or_none()
                )
                if existing is None:
                    raise
                if existing.request_hash != request_hash:
                    raise ReferenceIdempotencyConflictError(
                        "Idempotency-Key was reused for a different Reference request"
                    )
                return SkillCenterReferenceCreateResult(
                    batch=self._batch(session, existing), created=False
                )
            return SkillCenterReferenceCreateResult(
                batch=self._batch(session, batch), created=True
            )

    def get_work_batch(
        self, *, env: str, request_id: str
    ) -> SkillCenterReferenceWorkBatch | None:
        tenant = get_current_avernet_tenant()
        with self._db.orm_session() as session:
            batch = (
                session.query(SkillCenterReferenceBatchModel)
                .filter(
                    SkillCenterReferenceBatchModel.avernet_tenant == tenant,
                    SkillCenterReferenceBatchModel.env == env,
                    SkillCenterReferenceBatchModel.request_id == request_id,
                )
                .one_or_none()
            )
            if batch is None:
                return None
            rows = (
                session.query(SkillCenterReferenceItemModel)
                .filter(
                    SkillCenterReferenceItemModel.avernet_tenant == tenant,
                    SkillCenterReferenceItemModel.env == env,
                    SkillCenterReferenceItemModel.request_id == request_id,
                )
                .order_by(SkillCenterReferenceItemModel.id.asc())
                .all()
            )
            return SkillCenterReferenceWorkBatch(
                request_id=batch.request_id,
                env=batch.env,
                bot_id=batch.bot_id,
                owner_id=batch.owner_id,
                skill_set_id=batch.skill_set_id,
                actor_id=batch.actor_id,
                items=tuple(self._work_item(row) for row in rows),
            )

    def update_item(
        self,
        *,
        env: str,
        reference_id: str,
        status: SkillCenterReferenceStatus,
        **fields: object,
    ) -> SkillCenterReferenceWorkItem:
        allowed = {
            "sc_version_number",
            "skill_version_id",
            "resolved_skill_id",
            "attempt_count",
            "error_code",
            "error_message",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"unsupported Reference item fields: {sorted(unknown)}")
        tenant = get_current_avernet_tenant()
        with self._db.orm_session() as session:
            row = (
                session.query(SkillCenterReferenceItemModel)
                .filter(
                    SkillCenterReferenceItemModel.avernet_tenant == tenant,
                    SkillCenterReferenceItemModel.env == env,
                    SkillCenterReferenceItemModel.reference_id == reference_id,
                )
                .with_for_update()
                .one_or_none()
            )
            if row is None:
                raise RuntimeError("Reference item not found")
            if row.status in ("COMPLETED", "FAILED"):
                return self._work_item(row)
            row.status = status.value
            for field, value in fields.items():
                setattr(row, field, value)
            session.flush()
            return self._work_item(row)

    def ensure_public_version(
        self,
        *,
        env: str,
        actor_id: str,
        skill_code: str,
        skill_name: str,
        description: str | None,
        sc_skill_id: int,
        sc_version_number: str,
        sc_version_id: int,
    ) -> PublicCenterVersionTarget:
        tenant = get_current_avernet_tenant()
        locator = f"center://{skill_code}"
        with self._db.orm_session() as session:
            skill = self._find_public_skill(
                session, tenant=tenant, env=env, locator=locator
            )
            if skill is None:
                skill = Skill(
                    name=skill_name,
                    description=description,
                    git_path=locator,
                    is_public=True,
                    is_builtin=False,
                    user_id=None,
                    bolt_id="default",
                    env=env,
                    status="DEVELOPING",
                    version=1,
                    skill_uuid=str(
                        uuid5(
                            NAMESPACE_URL,
                            f"avernet:{tenant}:{env}:center:{skill_code}",
                        )
                    ),
                    source_type="center",
                    avernet_tenant=tenant,
                )
                try:
                    # ``uk_skill_uuid`` is the cross-process winner for this
                    # deterministic external-code identity.  The locator remains
                    # the readable SSOT and never aliases a Local/Repo/Space row.
                    with session.begin_nested():
                        session.add(skill)
                        session.flush()
                except IntegrityError:
                    skill = self._find_public_skill(
                        session, tenant=tenant, env=env, locator=locator
                    )
                    if skill is None:
                        raise

            existing = (
                session.query(SkillVersion)
                .filter(
                    SkillVersion.avernet_tenant == tenant,
                    SkillVersion.env == env,
                    SkillVersion.skill_id == int(skill.id),
                    SkillVersion.sc_version_number == sc_version_number,
                )
                .with_for_update()
                .one_or_none()
            )
            if existing is None:
                latest_ordinal = (
                    session.query(func.max(SkillVersion.version_ordinal))
                    .filter(
                        SkillVersion.avernet_tenant == tenant,
                        SkillVersion.env == env,
                        SkillVersion.skill_id == int(skill.id),
                    )
                    .scalar()
                )
                existing = SkillVersion(
                    skill_id=int(skill.id),
                    publication_attempt_id=None,
                    version_ordinal=int(latest_ordinal or 0) + 1,
                    status="MATERIALIZING",
                    sc_version_number=sc_version_number,
                    sc_skill_id=sc_skill_id,
                    sc_version_id=sc_version_id,
                    name=skill_name,
                    description=description,
                    metadata_json=None,
                    published_at=None,
                    created_by=actor_id,
                    env=env,
                    avernet_tenant=tenant,
                )
                try:
                    # G1's exact-Version unique keys arbitrate concurrent
                    # Reference and periodic Sync materialization attempts.
                    with session.begin_nested():
                        session.add(existing)
                        session.flush()
                except IntegrityError:
                    existing = (
                        session.query(SkillVersion)
                        .filter(
                            SkillVersion.avernet_tenant == tenant,
                            SkillVersion.env == env,
                            SkillVersion.skill_id == int(skill.id),
                            SkillVersion.sc_version_number == sc_version_number,
                        )
                        .with_for_update()
                        .one_or_none()
                    )
                    if existing is None:
                        raise
            elif (
                int(existing.sc_skill_id or 0) != sc_skill_id
                or int(existing.sc_version_id or 0) != sc_version_id
            ):
                raise RuntimeError("SC exact Version identity changed")
            return PublicCenterVersionTarget(
                skill_id=int(skill.id),
                skill_version_id=int(existing.id),
                status=existing.status,
            )

    @staticmethod
    def _find_public_skill(session, *, tenant: str, env: str, locator: str):
        # Production's legacy ``git_path`` collation may fold case.  Let the
        # indexed predicate narrow candidates, then require exact Python string
        # equality so two externally distinct skill_codes never alias.
        candidates = (
            session.query(Skill)
            .outerjoin(
                SkillSpaceBinding,
                (SkillSpaceBinding.skill_id == Skill.id)
                & (SkillSpaceBinding.env == env),
            )
            .filter(
                Skill.avernet_tenant == tenant,
                Skill.env == env,
                Skill.git_path == locator,
                Skill.is_public.is_(True),
                SkillSpaceBinding.id.is_(None),
            )
            .with_for_update()
            .all()
        )
        return next((row for row in candidates if row.git_path == locator), None)

    def list_items(
        self,
        *,
        env: str,
        bot_id: str,
        owner_id: str,
        skill_set_id: str,
        request_id: str | None,
        status: SkillCenterReferenceStatus | None,
        offset: int,
        limit: int,
    ) -> tuple[int, tuple[SkillCenterReferenceItem, ...]]:
        tenant = get_current_avernet_tenant()
        with self._db.orm_session() as session:
            query = session.query(SkillCenterReferenceItemModel).filter(
                SkillCenterReferenceItemModel.avernet_tenant == tenant,
                SkillCenterReferenceItemModel.env == env,
                SkillCenterReferenceItemModel.bot_id == bot_id,
                SkillCenterReferenceItemModel.owner_id == owner_id,
                SkillCenterReferenceItemModel.skill_set_id == skill_set_id,
            )
            if request_id is not None:
                query = query.filter(
                    SkillCenterReferenceItemModel.request_id == request_id
                )
            if status is not None:
                query = query.filter(
                    SkillCenterReferenceItemModel.status == status.value
                )
            total = query.count()
            rows = (
                query.order_by(
                    SkillCenterReferenceItemModel.gmt_created.desc(),
                    SkillCenterReferenceItemModel.id.desc(),
                )
                .offset(offset)
                .limit(limit)
                .all()
            )
            return total, tuple(self._item(row) for row in rows)

    def get_item(
        self,
        *,
        env: str,
        bot_id: str,
        owner_id: str,
        skill_set_id: str,
        reference_id: str,
    ) -> SkillCenterReferenceItem | None:
        tenant = get_current_avernet_tenant()
        with self._db.orm_session() as session:
            row = (
                session.query(SkillCenterReferenceItemModel)
                .filter(
                    SkillCenterReferenceItemModel.avernet_tenant == tenant,
                    SkillCenterReferenceItemModel.env == env,
                    SkillCenterReferenceItemModel.bot_id == bot_id,
                    SkillCenterReferenceItemModel.owner_id == owner_id,
                    SkillCenterReferenceItemModel.skill_set_id == skill_set_id,
                    SkillCenterReferenceItemModel.reference_id == reference_id,
                )
                .one_or_none()
            )
            return self._item(row) if row is not None else None

    def list_materialized_public_assets(
        self, *, env: str
    ) -> tuple[MaterializedPublicCenterAsset, ...]:
        tenant = get_current_avernet_tenant()
        with self._db.orm_session() as session:
            rows = (
                session.query(Skill)
                .join(
                    SkillVersion,
                    (SkillVersion.skill_id == Skill.id)
                    & (SkillVersion.env == env)
                    & (SkillVersion.status == "PUBLISHED"),
                )
                .outerjoin(
                    SkillSpaceBinding,
                    (SkillSpaceBinding.skill_id == Skill.id)
                    & (SkillSpaceBinding.env == env),
                )
                .filter(
                    Skill.avernet_tenant == tenant,
                    Skill.env == env,
                    Skill.is_public.is_(True),
                    Skill.git_path.like("center://%"),
                    SkillSpaceBinding.id.is_(None),
                )
                .order_by(Skill.id.asc())
                .distinct()
                .all()
            )
            return tuple(
                MaterializedPublicCenterAsset(
                    skill_id=int(row.id),
                    skill_code=str(row.git_path)[len("center://") :],
                    name=row.name,
                    description=row.description,
                )
                for row in rows
            )

    @classmethod
    def _batch(cls, session, row) -> SkillCenterReferenceBatch:
        items = (
            session.query(SkillCenterReferenceItemModel)
            .filter(
                SkillCenterReferenceItemModel.avernet_tenant
                == row.avernet_tenant,
                SkillCenterReferenceItemModel.env == row.env,
                SkillCenterReferenceItemModel.request_id == row.request_id,
            )
            .order_by(SkillCenterReferenceItemModel.id.asc())
            .all()
        )
        return SkillCenterReferenceBatch(
            request_id=row.request_id,
            bot_id=row.bot_id,
            owner_id=row.owner_id,
            skill_set_id=row.skill_set_id,
            actor_id=row.actor_id,
            items=tuple(cls._item(item) for item in items),
        )

    @staticmethod
    def _item(row) -> SkillCenterReferenceItem:
        return SkillCenterReferenceItem(
            reference_id=row.reference_id,
            request_id=row.request_id,
            skill_set_id=row.skill_set_id,
            skill_code=row.skill_code,
            sc_version_number=row.sc_version_number,
            status=SkillCenterReferenceStatus(row.status),
            skill_id=(
                str(row.resolved_skill_id)
                if row.resolved_skill_id is not None
                else None
            ),
            error_code=row.error_code,
            error_message=row.error_message,
            gmt_created=row.gmt_created,
            gmt_modified=row.gmt_modified,
        )

    @staticmethod
    def _work_item(row) -> SkillCenterReferenceWorkItem:
        return SkillCenterReferenceWorkItem(
            reference_id=row.reference_id,
            skill_code=row.skill_code,
            status=SkillCenterReferenceStatus(row.status),
            sc_version_number=row.sc_version_number,
            skill_version_id=(
                int(row.skill_version_id) if row.skill_version_id is not None else None
            ),
            resolved_skill_id=(
                int(row.resolved_skill_id)
                if row.resolved_skill_id is not None
                else None
            ),
            attempt_count=int(row.attempt_count),
            error_code=row.error_code,
            error_message=row.error_message,
        )


__all__ = ["SkillCenterReferenceRepository"]
