"""G4 Reference consumes G5's canonical Offline write seam unchanged."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.base import Base
from agentclaw.community.core.models.skill import (
    BotSkillInstallation,
    Skill,
    SkillSet,
    SkillSetSkill,
)
from agentclaw.community.core.repository.implementations.skill_center.capability_desired_state import (
    CapabilityDesiredStateRepository,
)
from agentclaw.community.core.repository.skill_center_reference_types import (
    PublicCenterVersionTarget,
    SkillCenterReferenceWorkBatch,
    SkillCenterReferenceWorkItem,
)
from agentclaw.community.core.skill_center.materialization_contract import (
    PublishedMaterializedSkillVersion,
)
from agentclaw.community.core.skill_center.reference_contract import (
    SkillCenterReferenceStatus,
)
from agentclaw.community.core.skill_center.services.skill_center_reference_processor import (
    SkillCenterReferenceProcessor,
)
from agentclaw.community.core.skill_center.services.skill_set_management_service import (
    SkillSetManagementService,
)
from agentclaw.community.plugin_api.skill_center_gateway import (
    SkillCenterAccessLevel,
    SkillCenterSkill,
    SkillCenterVersion,
)
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope


class _Database:
    def __init__(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self._sessions = sessionmaker(bind=self.engine, expire_on_commit=False)

    @contextmanager
    def orm_session(self):
        with self.transactional_orm_session() as session:
            yield session

    @contextmanager
    def transactional_orm_session(self):
        session = self._sessions()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


class _References:
    def __init__(self, *, skill_set_id: str) -> None:
        self.batch = SkillCenterReferenceWorkBatch(
            request_id="reference-offline",
            env="pre",
            bot_id="bot-a",
            owner_id="owner-a",
            skill_set_id=skill_set_id,
            actor_id="owner-a",
            items=(
                SkillCenterReferenceWorkItem(
                    reference_id="reference-a",
                    skill_code="public-offline",
                    status=SkillCenterReferenceStatus.QUEUED,
                    sc_version_number=None,
                    skill_version_id=None,
                    resolved_skill_id=None,
                    attempt_count=0,
                ),
            ),
        )

    def get_work_batch(self, *, env, request_id):
        assert env == "pre"
        return self.batch if request_id == self.batch.request_id else None

    def update_item(self, *, env, reference_id, status, **fields):
        assert env == "pre"
        (item,) = self.batch.items
        assert reference_id == item.reference_id
        updated = replace(item, status=status, **fields)
        self.batch = replace(self.batch, items=(updated,))
        return updated

    def ensure_public_version(self, **_kwargs):
        return PublicCenterVersionTarget(
            skill_id=10, skill_version_id=110, status="PUBLISHED"
        )


class _Gateway:
    def get_public_skill(self, request):
        assert request.skill_code == "public-offline"
        return SkillCenterSkill(
            skill_code="public-offline",
            skill_name="offline",
            access_level=SkillCenterAccessLevel.PUBLIC,
            skill_id="9001",
            latest_version_number="1.0.0",
        )

    def list_versions(self, _request):
        return (SkillCenterVersion(version_number="1.0.0", version_id="10001"),)


class _Materializer:
    def materialize(self, _request):
        return PublishedMaterializedSkillVersion(
            skill_version_id=110,
            skill_id=10,
            version_ordinal=1,
            status="PUBLISHED",
            skill_uuid="00000000-0000-4000-8000-000000000010",
            sc_version_number="1.0.0",
            sc_skill_id=9001,
            sc_version_id=10001,
            name="offline",
            description=None,
            metadata_json='{"mcp_dependencies":[]}',
            published_at=datetime(2026, 8, 30, tzinfo=UTC),
        )


class _TrackLatest:
    def version_published(self, _version) -> None:
        pass


class _BotRepository:
    def get_by_id_and_owner(self, bot_id: str, owner_id: str):
        if (bot_id, owner_id) != ("bot-a", "owner-a"):
            return None
        return {
            "owner_id": "owner-a",
            "entity_id": "owner-a",
            "entity_type": "staff",
            "bot_type": "personal",
            "status": "ACTIVE",
            "active_engine": "openclaw",
        }


class _Authorization:
    def can_manage_bot(self, **_kwargs) -> bool:
        return True


class _AuditLog:
    def insert(self, _record) -> None:
        pass


def _skill_sets(db: _Database) -> SkillSetManagementService:
    return SkillSetManagementService(
        repository=CapabilityDesiredStateRepository(db),
        bot_repo=_BotRepository(),
        runtime=object(),
        legacy_factory=object(),
        passport=object(),
        authorization=_Authorization(),
        audit_log_repo=_AuditLog(),
        mcp_center=object(),
        mcp_auth=object(),
        ext_info_provider=lambda _bot_id: None,
    )


def test_reference_persists_g5_offline_failure_without_capability_writes(
    monkeypatch,
) -> None:
    monkeypatch.setenv("SERVER_ENV", "pre")
    db = _Database()
    with avernet_tenant_scope("teamclaw"), db.transactional_orm_session() as session:
        session.add(
            Skill(
                id=10,
                name="offline",
                git_path="center://public-offline",
                skill_uuid="00000000-0000-4000-8000-000000000010",
                offline_at=datetime(2026, 8, 30, tzinfo=UTC),
                env="pre",
                avernet_tenant="teamclaw",
            )
        )
        skill_set = SkillSet(
            name="inactive",
            user_id="owner-a",
            bolt_id="bot-a",
            engine_type="openclaw",
            is_active=False,
            env="pre",
            avernet_tenant="teamclaw",
        )
        session.add(skill_set)
        session.flush()
        skill_set_id = str(skill_set.id)

    references = _References(skill_set_id=skill_set_id)
    processor = SkillCenterReferenceProcessor(
        references=references,
        gateway=_Gateway(),
        materializer=_Materializer(),
        skill_sets=_skill_sets(db),
        track_latest=_TrackLatest(),
        env_provider=lambda: "pre",
    )

    with avernet_tenant_scope("teamclaw"):
        asyncio.run(processor.process("reference-offline"))

    (item,) = references.batch.items
    assert item.status is SkillCenterReferenceStatus.FAILED
    assert item.error_code == "SKILL_OFFLINE"
    with avernet_tenant_scope("teamclaw"), db.orm_session() as session:
        assert session.query(SkillSetSkill).count() == 0
        assert session.query(BotSkillInstallation).count() == 0
