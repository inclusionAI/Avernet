"""Publication aggregate persistence tests at the transactional repository seam."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
import io
from pathlib import Path
from uuid import uuid4
import zipfile

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.base import Base
from agentclaw.community.core.models.skill import (
    BotSkillInstallation,
    Skill,
    SkillSet,
    SkillSetSkill,
)
from agentclaw.community.core.models.space_skill import (
    SkillDraftEditLease,
    SkillGrant,
    SkillPublicationAttempt,
    SkillSpaceBinding,
    SkillVersion,
)
from agentclaw.community.core.repository.implementations.skill_center.space_skill_publication import (
    SpaceSkillPublicationRepository,
)
from agentclaw.community.core.skill_center.errors import (
    DraftEditLeaseConflictError,
    PublicationRequiresNewAttemptError,
    PublicationResultUnknownError,
    SpaceSkillIdempotencyConflictError,
)
from agentclaw.community.core.events.bus import get_event_bus, reset_event_bus
from agentclaw.community.core.skill_center.materialization_contract import (
    PublishedMaterializedSkillVersion,
    SkillVersionMaterializationError,
)
from agentclaw.community.core.skill_center.publication_contract import (
    PublicationPackageStage,
)
from agentclaw.community.core.skill_center.services.skill_parser import SkillParser
from agentclaw.community.core.skill_center.services.space_skill_publication_task import (
    SpaceSkillPublicationTaskHandler,
    publication_task_payload,
)
from agentclaw.community.core.skill_center.skill_package import SkillPackageValidator
from agentclaw.community.core.task_queue.types import Complete, Retry
from agentclaw.community.core.spaces.repository.models import (
    SpaceMemberModel,
    SpaceModel,
)
from agentclaw.community.plugin_api.skill_center_gateway import (
    SkillCenterAccessLevel,
    SkillCenterGatewayError,
    SkillCenterGatewayErrorCode,
    SkillCenterPublishState,
    SkillCenterPublishStatus,
    SkillCenterPublishSubmission,
    SkillCenterPublishSubmissionState,
    SkillCenterTeamSkill,
    SkillCenterVersion,
)
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.utils.avernet_tenant import (
    avernet_tenant_scope,
    get_current_avernet_tenant,
)


class _Database:
    def __init__(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self._factory = sessionmaker(bind=self.engine)

    @contextmanager
    def orm_session(self):
        session = self._factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    transactional_orm_session = orm_session


def _seed_draft(
    db: _Database,
    *,
    space_type: str = "TEAM",
    lease_holder: str | None = None,
    sc_team_id: str = "sc-team-7",
) -> tuple[int, int]:
    skill_uuid = str(uuid4())
    revision_id = str(uuid4())
    with db.orm_session() as session:
        space = SpaceModel(
            space_code=f"space-{uuid4()}",
            space_type=space_type,
            name="Publication Space",
            sc_team_id=sc_team_id,
            sc_mapping_status="ACTIVE",
            created_by="owner",
            updated_by="owner",
            env="test",
        )
        session.add(space)
        session.flush()
        session.add(
            SpaceMemberModel(
                space_id=space.id,
                user_id="owner",
                role="ADMIN",
                status="ACTIVE",
                created_by="owner",
                env="test",
            )
        )
        skill = Skill(
            name="risk-review",
            description=None,
            skill_uuid=skill_uuid,
            zip_url=f"draft://{skill_uuid}/v1/{revision_id}",
            draft_target_version=1,
            draft_status="EDITING",
            draft_description="Review risk",
            draft_source_kind="FOLDER",
            source_type="FOLDER",
            status="DEVELOPING",
            version=1,
            env="test",
        )
        session.add(skill)
        session.flush()
        session.add_all(
            (
                SkillSpaceBinding(
                    skill_id=skill.id,
                    space_id=space.id,
                    created_by="owner",
                    env="test",
                ),
                SkillGrant(
                    skill_id=skill.id,
                    user_id="owner",
                    role="OWNER",
                    status="ACTIVE",
                    owner_slot=1,
                    granted_by="owner",
                    env="test",
                ),
            )
        )
        if lease_holder is not None:
            session.add(
                SkillDraftEditLease(
                    skill_id=skill.id,
                    holder_user_id=lease_holder,
                    fencing_token=3,
                    env="test",
                )
            )
        session.flush()
        return int(space.id), int(skill.id)


def test_create_attempt_freezes_latest_draft_and_replays_same_request() -> None:
    db = _Database()
    space_id, skill_id = _seed_draft(db, lease_holder="owner")
    repository = SpaceSkillPublicationRepository(db)
    with db.orm_session() as session:
        frozen_locator = session.get(Skill, skill_id).zip_url

    created = repository.create_or_replay_attempt(
        space_id=space_id,
        skill_id=skill_id,
        actor_id="owner",
        request_id="publish-request-1",
        env="test",
    )
    replay = repository.create_or_replay_attempt(
        space_id=space_id,
        skill_id=skill_id,
        actor_id="owner",
        request_id="publish-request-1",
        env="test",
    )

    assert created.created is True
    assert created.attempt.status == "PREPARING"
    assert created.attempt.frozen_draft_locator == frozen_locator
    assert created.attempt.target_version == 1
    assert created.attempt.sc_version_number == "1.0.0"
    assert created.attempt.recovery.state == "AUTO_RETRYING"
    assert created.attempt.recovery.kind == "PREPARATION"
    assert replay.created is False
    assert replay.attempt.attempt_id == created.attempt.attempt_id

    with db.orm_session() as session:
        stored = session.query(Skill).filter(Skill.id == skill_id).one()
        assert stored.draft_status == "FROZEN"


def test_create_attempt_rejects_team_draft_held_by_another_editor() -> None:
    db = _Database()
    space_id, skill_id = _seed_draft(db, lease_holder="manager")
    repository = SpaceSkillPublicationRepository(db)

    with pytest.raises(DraftEditLeaseConflictError):
        repository.create_or_replay_attempt(
            space_id=space_id,
            skill_id=skill_id,
            actor_id="owner",
            request_id="publish-request-2",
            env="test",
        )


def test_publication_idempotency_key_is_global_across_skills() -> None:
    columns = next(
        constraint.columns.keys()
        for constraint in SkillPublicationAttempt.__table__.constraints
        if getattr(constraint, "name", None) == "uk_publish_request"
    )

    assert columns == ["avernet_tenant", "env", "request_id"]


def test_same_publication_request_key_conflicts_across_skills() -> None:
    db = _Database()
    first_space, first_skill = _seed_draft(db)
    second_space, second_skill = _seed_draft(db, sc_team_id="sc-team-8")
    repository = SpaceSkillPublicationRepository(db)

    first = repository.create_or_replay_attempt(
        space_id=first_space,
        skill_id=first_skill,
        actor_id="owner",
        request_id="publish-same-key",
        env="test",
    )
    with pytest.raises(SpaceSkillIdempotencyConflictError):
        repository.create_or_replay_attempt(
            space_id=second_space,
            skill_id=second_skill,
            actor_id="owner",
            request_id="publish-same-key",
            env="test",
        )

    assert first.created is True


def test_group3_migration_adds_frozen_draft_locator_compatibly() -> None:
    sql_dir = (
        Path(__file__).resolve().parents[4]
        / "src"
        / "agentclaw"
        / "community"
        / "core"
        / "skill_center"
        / "sql"
    )
    migration = (
        sql_dir / "2026_08_30_finalize_space_skill_group3_publication.sql"
    ).read_text(encoding="utf-8")
    verification = (
        sql_dir / "2026_08_30_finalize_space_skill_group3_publication_verify.sql"
    ).read_text(encoding="utf-8")

    assert (
        "ADD COLUMN IF NOT EXISTS frozen_draft_locator VARCHAR(1028) NULL" in migration
    )
    assert "COLUMN_NAME = 'frozen_draft_locator'" in verification
    assert "uk_publish_request (avernet_tenant, env, request_id)" in migration
    assert "avernet_tenant,env,request_id" in verification


def test_impact_candidates_cover_installation_and_active_ordinary_membership() -> None:
    db = _Database()
    _space_id, skill_id = _seed_draft(db, space_type="PERSONAL")
    with db.orm_session() as session:
        for bot_id in ("installed", "ordinary-set", "default-only"):
            session.add(
                BotModel(
                    bot_id=bot_id,
                    bot_name=bot_id,
                    entity_id=f"entity-{bot_id}",
                    entity_type="user",
                    creator_id="owner",
                    owner_id="owner",
                    env="test",
                )
            )
        session.add(
            BotSkillInstallation(
                bot_id="installed",
                owner_id="owner",
                skill_id=skill_id,
                env="test",
            )
        )
        ordinary = SkillSet(
            name="ordinary",
            user_id="owner",
            bolt_id="ordinary-set",
            env="test",
            is_active=True,
            is_default=False,
        )
        default = SkillSet(
            name="default",
            user_id="owner",
            bolt_id="default-only",
            env="test",
            is_active=True,
            is_default=True,
        )
        session.add_all((ordinary, default))
        session.flush()
        session.add_all(
            (
                SkillSetSkill(
                    skill_set_id=ordinary.id,
                    skill_id=skill_id,
                    user_id="owner",
                    env="test",
                ),
                SkillSetSkill(
                    skill_set_id=default.id,
                    skill_id=skill_id,
                    user_id="owner",
                    env="test",
                ),
            )
        )

    candidates = SpaceSkillPublicationRepository(db).list_impact_candidates(
        skill_id=skill_id, env="test"
    )

    assert [item.bot_id for item in candidates] == ["installed", "ordinary-set"]


def _package():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(
            "SKILL.md",
            "---\nname: risk-review\ndescription: Review risk\n---\n",
        )
    return SkillPackageValidator(SkillParser()).validate_zip(stream.getvalue())


class _DraftStore:
    def __init__(self) -> None:
        self.package = _package()
        self.read_refs = []
        self.deleted = []

    def read_revision(self, ref):
        self.read_refs.append(ref)
        return self.package

    def delete_revision(self, ref):
        self.deleted.append(ref)


class _Stager:
    def __init__(self) -> None:
        self.calls = 0

    def stage(self, **_kwargs):
        self.calls += 1
        return PublicationPackageStage("https://example.invalid/frozen.zip")


class _Gateway:
    def __init__(
        self,
        *,
        timeout_once: bool = False,
        timeout_message: str = "outcome unknown",
        exact_missing_once: bool = False,
        status_state: SkillCenterPublishState = SkillCenterPublishState.PUBLISHED,
    ) -> None:
        self.timeout_once = timeout_once
        self.timeout_message = timeout_message
        self.exact_missing_once = exact_missing_once
        self.status_state = status_state
        self.submit_calls = 0
        self.status_calls = 0

    def submit_publish(self, request):
        self.submit_calls += 1
        if self.timeout_once:
            self.timeout_once = False
            raise SkillCenterGatewayError(
                SkillCenterGatewayErrorCode.TIMEOUT, self.timeout_message
            )
        return SkillCenterPublishSubmission(
            request.skill_code,
            request.version_number,
            SkillCenterPublishSubmissionState.ACCEPTED,
        )

    def get_publish_status(self, request):
        self.status_calls += 1
        return SkillCenterPublishStatus(
            skill_code=request.skill_code,
            version_number="1.0.0",
            status=self.status_state,
            is_completed=self.status_state is not SkillCenterPublishState.PENDING,
            is_success=self.status_state is SkillCenterPublishState.PUBLISHED,
            error_message=(
                "SC rejected package"
                if self.status_state is SkillCenterPublishState.FAILED
                else None
            ),
        )

    def get_team_skill(self, request):
        return SkillCenterTeamSkill(
            team_id=request.team_id,
            skill_id="701",
            skill_code=request.skill_code,
            skill_name="risk-review",
            access_level=SkillCenterAccessLevel.PRIVATE,
        )

    def list_versions(self, _request):
        if self.exact_missing_once:
            self.exact_missing_once = False
            return ()
        return (
            SkillCenterVersion(
                version_number="1.0.0", version_id="801", sha256="a" * 64
            ),
        )


class _Materializer:
    def __init__(
        self,
        db: _Database,
        *,
        fail_once: bool = False,
        fail_message: str = "canonical store unavailable",
    ) -> None:
        self.db = db
        self.fail_once = fail_once
        self.fail_message = fail_message
        self.version_ids: list[int] = []

    def materialize(self, request):
        self.version_ids.append(request.skill_version_id)
        if self.fail_once:
            self.fail_once = False
            raise SkillVersionMaterializationError(self.fail_message)
        published_at = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
        with self.db.orm_session() as session:
            version = (
                session.query(SkillVersion)
                .filter(SkillVersion.id == request.skill_version_id)
                .one()
            )
            version.status = "PUBLISHED"
            version.metadata_json = '{"mcp_dependencies":[]}'
            version.published_at = published_at
            version.description = "Review risk"
            session.flush()
            return PublishedMaterializedSkillVersion(
                skill_version_id=int(version.id),
                skill_id=int(version.skill_id),
                version_ordinal=int(version.version_ordinal),
                status="PUBLISHED",
                skill_uuid="00000000-0000-4000-8000-000000000001",
                sc_version_number=version.sc_version_number,
                sc_skill_id=int(version.sc_skill_id),
                sc_version_id=int(version.sc_version_id),
                name=version.name,
                description=version.description,
                metadata_json=version.metadata_json,
                published_at=published_at,
            )


def _handler(
    db: _Database,
    repository: SpaceSkillPublicationRepository,
    gateway: _Gateway,
    materializer: _Materializer,
    *,
    draft_store: _DraftStore | None = None,
    auto_retry_seconds: int = 15 * 60,
) -> SpaceSkillPublicationTaskHandler:
    return SpaceSkillPublicationTaskHandler(
        repository=repository,
        gateway=gateway,
        draft_store=draft_store or _DraftStore(),
        stager=_Stager(),
        materializer=materializer,
        tenant_provider=get_current_avernet_tenant,
        env_provider=lambda: "test",
        clock=lambda: datetime(2026, 8, 30, 12, 0, tzinfo=UTC),
        auto_retry_seconds=auto_retry_seconds,
    )


def test_worker_submits_once_materializes_and_emits_published_seam() -> None:
    reset_event_bus()
    db = _Database()
    space_id, skill_id = _seed_draft(db, lease_holder="owner")
    repository = SpaceSkillPublicationRepository(db)
    attempt = repository.create_or_replay_attempt(
        space_id=space_id,
        skill_id=skill_id,
        actor_id="owner",
        request_id="worker-happy",
        env="test",
    ).attempt
    gateway = _Gateway()
    materializer = _Materializer(db)
    published_events = []
    get_event_bus().subscribe(
        PublishedMaterializedSkillVersion, published_events.append
    )
    handler = _handler(db, repository, gateway, materializer)

    first = handler.handle(publication_task_payload(attempt.attempt_id))
    second = handler.handle(publication_task_payload(attempt.attempt_id))

    assert first.delay_seconds == 2
    assert isinstance(second, Complete)
    assert gateway.submit_calls == 1
    assert len(materializer.version_ids) == 1
    stored = repository.get_attempt(
        space_id=space_id,
        skill_id=skill_id,
        attempt_id=attempt.attempt_id,
        env="test",
    )
    assert stored.status == "SUCCEEDED"
    assert len(published_events) == 1
    with db.orm_session() as session:
        skill = session.query(Skill).filter(Skill.id == skill_id).one()
        assert skill.draft_status is None
        assert skill.zip_url is None
        assert skill.package_url is None
        assert skill.git_path.endswith(skill.skill_uuid)
    reset_event_bus()


def test_worker_reads_only_the_attempt_frozen_draft_locator() -> None:
    db = _Database()
    space_id, skill_id = _seed_draft(db, lease_holder="owner")
    repository = SpaceSkillPublicationRepository(db)
    attempt = repository.create_or_replay_attempt(
        space_id=space_id,
        skill_id=skill_id,
        actor_id="owner",
        request_id="worker-frozen-revision",
        env="test",
    ).attempt
    with db.orm_session() as session:
        skill = session.get(Skill, skill_id)
        assert skill is not None
        skill.zip_url = f"draft://{skill.skill_uuid}/v1/{uuid4()}"
    drafts = _DraftStore()
    handler = _handler(
        db,
        repository,
        _Gateway(),
        _Materializer(db),
        draft_store=drafts,
    )

    handler.handle(publication_task_payload(attempt.attempt_id))

    assert len(drafts.read_refs) == 1
    assert drafts.read_refs[0].locator == attempt.frozen_draft_locator


def test_submit_timeout_enters_result_unknown_and_never_reposts() -> None:
    db = _Database()
    space_id, skill_id = _seed_draft(db, lease_holder="owner")
    repository = SpaceSkillPublicationRepository(db)
    attempt = repository.create_or_replay_attempt(
        space_id=space_id,
        skill_id=skill_id,
        actor_id="owner",
        request_id="worker-timeout",
        env="test",
    ).attempt
    secret_url = "https://sc.invalid/download.zip?signature=do-not-leak"
    gateway = _Gateway(timeout_once=True, timeout_message=secret_url)
    handler = _handler(db, repository, gateway, _Materializer(db))

    assert isinstance(
        handler.handle(publication_task_payload(attempt.attempt_id)), Retry
    )
    unknown = repository.get_attempt(
        space_id=space_id,
        skill_id=skill_id,
        attempt_id=attempt.attempt_id,
        env="test",
    )
    assert unknown.status == "RESULT_UNKNOWN"
    assert unknown.error_message == (
        "Skill Center publication status is temporarily unavailable"
    )
    assert "signature" not in unknown.error_message
    with pytest.raises(PublicationResultUnknownError):
        repository.create_or_replay_attempt(
            space_id=space_id,
            skill_id=skill_id,
            actor_id="owner",
            request_id="another-publish",
            env="test",
        )

    assert isinstance(
        handler.handle(publication_task_payload(attempt.attempt_id)), Complete
    )
    assert gateway.submit_calls == 1


def test_sc_explicit_failure_restores_the_same_draft_to_editing() -> None:
    db = _Database()
    space_id, skill_id = _seed_draft(db, lease_holder="owner")
    repository = SpaceSkillPublicationRepository(db)
    attempt = repository.create_or_replay_attempt(
        space_id=space_id,
        skill_id=skill_id,
        actor_id="owner",
        request_id="worker-rejected",
        env="test",
    ).attempt
    handler = _handler(
        db,
        repository,
        _Gateway(status_state=SkillCenterPublishState.FAILED),
        _Materializer(db),
    )

    handler.handle(publication_task_payload(attempt.attempt_id))
    assert isinstance(
        handler.handle(publication_task_payload(attempt.attempt_id)), Complete
    )

    failed = repository.get_attempt(
        space_id=space_id,
        skill_id=skill_id,
        attempt_id=attempt.attempt_id,
        env="test",
    )
    assert failed.status == "FAILED"
    assert failed.error_code == "SC_PUBLISH_REJECTED"
    with db.orm_session() as session:
        skill = session.query(Skill).filter(Skill.id == skill_id).one()
        assert skill.draft_status == "EDITING"
        assert skill.package_url is None

    with pytest.raises(PublicationRequiresNewAttemptError):
        repository.create_or_replay_attempt(
            space_id=space_id,
            skill_id=skill_id,
            actor_id="owner",
            request_id="worker-rejected-unchanged",
            env="test",
        )

    with db.orm_session() as session:
        skill = session.get(Skill, skill_id)
        assert skill is not None
        skill.zip_url = f"draft://{skill.skill_uuid}/v1/{uuid4()}"
    replay = repository.create_or_replay_attempt(
        space_id=space_id,
        skill_id=skill_id,
        actor_id="owner",
        request_id="worker-rejected",
        env="test",
    )
    assert replay.created is False
    assert replay.attempt.attempt_id == attempt.attempt_id
    changed = repository.create_or_replay_attempt(
        space_id=space_id,
        skill_id=skill_id,
        actor_id="owner",
        request_id="worker-rejected-changed",
        env="test",
    )
    assert changed.created is True


def test_published_status_metadata_lag_stays_waiting_and_never_reposts() -> None:
    db = _Database()
    space_id, skill_id = _seed_draft(db, lease_holder="owner")
    repository = SpaceSkillPublicationRepository(db)
    attempt = repository.create_or_replay_attempt(
        space_id=space_id,
        skill_id=skill_id,
        actor_id="owner",
        request_id="worker-version-list-lag",
        env="test",
    ).attempt
    gateway = _Gateway(exact_missing_once=True)
    handler = _handler(db, repository, gateway, _Materializer(db))

    handler.handle(publication_task_payload(attempt.attempt_id))
    delayed = handler.handle(publication_task_payload(attempt.attempt_id))
    waiting = repository.get_attempt(
        space_id=space_id,
        skill_id=skill_id,
        attempt_id=attempt.attempt_id,
        env="test",
    )

    assert isinstance(delayed, Retry)
    assert waiting.status == "WAITING_SC"
    assert waiting.error_code is None
    assert isinstance(
        handler.handle(publication_task_payload(attempt.attempt_id)), Complete
    )
    assert gateway.submit_calls == 1


def test_exhausted_materialization_exposes_same_attempt_retry() -> None:
    db = _Database()
    space_id, skill_id = _seed_draft(db, lease_holder="owner")
    repository = SpaceSkillPublicationRepository(db)
    attempt = repository.create_or_replay_attempt(
        space_id=space_id,
        skill_id=skill_id,
        actor_id="owner",
        request_id="worker-recover-materialize",
        env="test",
    ).attempt
    gateway = _Gateway()
    materializer = _Materializer(
        db,
        fail_once=True,
        fail_message="https://sc.invalid/exact.zip?signature=do-not-leak",
    )
    handler = _handler(
        db,
        repository,
        gateway,
        materializer,
        auto_retry_seconds=0,
    )

    handler.handle(publication_task_payload(attempt.attempt_id))
    terminal_task = handler.handle(publication_task_payload(attempt.attempt_id))
    available = repository.get_attempt(
        space_id=space_id,
        skill_id=skill_id,
        attempt_id=attempt.attempt_id,
        env="test",
    )

    assert terminal_task.error == "Exact Version materialization failed"
    assert available.status == "MATERIALIZING"
    assert available.error_message == "Exact Version materialization failed"
    assert "signature" not in available.error_message
    assert available.recovery.state == "AVAILABLE"
    assert available.recovery.kind == "MATERIALIZATION"

    recovery = repository.restart_recovery(
        space_id=space_id,
        skill_id=skill_id,
        attempt_id=attempt.attempt_id,
        actor_id="owner",
        env="test",
    )
    assert recovery.task_required is True
    assert recovery.attempt.attempt_id == attempt.attempt_id
    assert isinstance(
        handler.handle(publication_task_payload(attempt.attempt_id)), Complete
    )
    assert materializer.version_ids[0] == materializer.version_ids[1]


def test_materialization_retry_reuses_the_same_exact_version() -> None:
    db = _Database()
    space_id, skill_id = _seed_draft(db, lease_holder="owner")
    repository = SpaceSkillPublicationRepository(db)
    attempt = repository.create_or_replay_attempt(
        space_id=space_id,
        skill_id=skill_id,
        actor_id="owner",
        request_id="worker-materialize",
        env="test",
    ).attempt
    gateway = _Gateway()
    materializer = _Materializer(db, fail_once=True)
    handler = _handler(db, repository, gateway, materializer)

    handler.handle(publication_task_payload(attempt.attempt_id))
    assert isinstance(
        handler.handle(publication_task_payload(attempt.attempt_id)), Retry
    )
    materializing = repository.get_attempt(
        space_id=space_id,
        skill_id=skill_id,
        attempt_id=attempt.attempt_id,
        env="test",
    )
    assert materializing.status == "MATERIALIZING"
    assert isinstance(
        handler.handle(publication_task_payload(attempt.attempt_id)), Complete
    )

    assert materializer.version_ids == [
        materializing.skill_version_id,
        materializing.skill_version_id,
    ]
    assert gateway.submit_calls == 1


def test_publication_task_reestablishes_the_persisted_avernet_tenant() -> None:
    db = _Database()
    with avernet_tenant_scope("external-tenant"):
        space_id, skill_id = _seed_draft(db, lease_holder="owner")
        repository = SpaceSkillPublicationRepository(db)
        attempt = repository.create_or_replay_attempt(
            space_id=space_id,
            skill_id=skill_id,
            actor_id="owner",
            request_id="worker-external-tenant",
            env="test",
        ).attempt
    handler = _handler(db, repository, _Gateway(), _Materializer(db))

    handler.handle(
        publication_task_payload(attempt.attempt_id, tenant="external-tenant")
    )
    result = handler.handle(
        publication_task_payload(attempt.attempt_id, tenant="external-tenant")
    )

    assert isinstance(result, Complete)
    assert get_current_avernet_tenant() == "teamclaw"
    with avernet_tenant_scope("external-tenant"):
        assert (
            repository.get_attempt(
                space_id=space_id,
                skill_id=skill_id,
                attempt_id=attempt.attempt_id,
                env="test",
            ).status
            == "SUCCEEDED"
        )


def test_auto_retrying_attempt_retry_idempotently_reensures_task() -> None:
    db = _Database()
    space_id, skill_id = _seed_draft(db, lease_holder="owner")
    repository = SpaceSkillPublicationRepository(db)
    attempt = repository.create_or_replay_attempt(
        space_id=space_id,
        skill_id=skill_id,
        actor_id="owner",
        request_id="worker-retry-crash-window",
        env="test",
    ).attempt

    first = repository.restart_recovery(
        space_id=space_id,
        skill_id=skill_id,
        attempt_id=attempt.attempt_id,
        actor_id="owner",
        env="test",
    )
    second = repository.restart_recovery(
        space_id=space_id,
        skill_id=skill_id,
        attempt_id=attempt.attempt_id,
        actor_id="owner",
        env="test",
    )

    assert first.task_required is True
    assert second.task_required is True
    assert second.attempt.attempt_id == attempt.attempt_id


def test_active_legacy_attempt_without_frozen_revision_fails_closed() -> None:
    db = _Database()
    space_id, skill_id = _seed_draft(db, lease_holder="owner")
    repository = SpaceSkillPublicationRepository(db)
    attempt = repository.create_or_replay_attempt(
        space_id=space_id,
        skill_id=skill_id,
        actor_id="owner",
        request_id="worker-missing-frozen-revision",
        env="test",
    ).attempt
    with db.orm_session() as session:
        stored = session.get(SkillPublicationAttempt, attempt.attempt_id)
        assert stored is not None
        stored.frozen_draft_locator = None

    with pytest.raises(
        RuntimeError, match="active Publication Attempt has no frozen Draft Revision"
    ):
        repository.get_work(attempt_id=attempt.attempt_id, env="test")
