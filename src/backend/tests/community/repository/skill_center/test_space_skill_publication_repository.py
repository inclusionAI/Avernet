"""Publication aggregate persistence tests at the transactional repository seam."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
import io
import logging
from pathlib import Path
from threading import Barrier, Event, Thread, current_thread
from uuid import uuid4
import zipfile

import pytest
from sqlalchemy import create_engine, text
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
from agentclaw.community.core.repository.implementations.skill_center.space_skill_offline import (
    SpaceSkillOfflineRepository,
)
from agentclaw.community.core.repository.implementations.skill_center import (
    space_skill_publication as publication_repository_module,
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


class _ConcurrentDatabase(_Database):
    def __init__(self, path: Path) -> None:
        self.engine = create_engine(
            f"sqlite:///{path}",
            connect_args={"check_same_thread": False, "timeout": 5},
        )
        Base.metadata.create_all(self.engine)
        self._factory = sessionmaker(bind=self.engine)
        self.offline_begin_attempted = Event()
        self.materialization_begin_attempted = Event()

    @contextmanager
    def transactional_orm_session(self):
        session = self._factory()
        try:
            if current_thread().name == "offline":
                self.offline_begin_attempted.set()
            elif current_thread().name == "begin":
                self.materialization_begin_attempted.set()
            session.execute(text("BEGIN IMMEDIATE"))
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


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


def _seed_waiting_publication(
    db: _Database,
) -> tuple[int, int, int]:
    space_id, skill_id = _seed_draft(db, lease_holder="owner")
    repository = SpaceSkillPublicationRepository(db)
    attempt = repository.create_or_replay_attempt(
        space_id=space_id,
        skill_id=skill_id,
        actor_id="owner",
        request_id=f"complete-{uuid4()}",
        env="test",
    ).attempt
    repository.mark_prepared(
        attempt_id=attempt.attempt_id,
        package_url="https://example.invalid/frozen.zip",
        env="test",
    )
    repository.claim_sc_submission(
        attempt_id=attempt.attempt_id,
        env="test",
    )
    repository.mark_waiting_sc(
        attempt_id=attempt.attempt_id,
        env="test",
    )
    return space_id, skill_id, attempt.attempt_id


def _seed_published_materialization(
    db: _Database,
) -> tuple[int, int, int, int]:
    space_id, skill_id, attempt_id = _seed_waiting_publication(db)
    repository = SpaceSkillPublicationRepository(db)
    materializing = repository.begin_materialization(
        attempt_id=attempt_id,
        sc_skill_id=701,
        sc_version_id=801,
        sc_sha256="a" * 64,
        env="test",
    ).attempt
    assert materializing.skill_version_id is not None
    with db.orm_session() as session:
        version = session.get(SkillVersion, materializing.skill_version_id)
        assert version is not None
        version.status = "PUBLISHED"
        version.metadata_json = '{"mcp_dependencies":[]}'
        version.description = "Review risk"
        version.published_at = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
    return (
        space_id,
        skill_id,
        attempt_id,
        materializing.skill_version_id,
    )


def test_begin_materialization_rechecks_status_after_skill_lock(
    monkeypatch,
) -> None:
    db = _Database()
    _space_id, _skill_id, attempt_id = _seed_waiting_publication(db)
    repository = SpaceSkillPublicationRepository(db)
    original = publication_repository_module.lock_skill_row

    def _fail_after_skill_lock(session, **kwargs):
        skill = original(session, **kwargs)
        session.query(SkillPublicationAttempt).filter(
            SkillPublicationAttempt.id == attempt_id
        ).update({"status": "FAILED"}, synchronize_session=False)
        return skill

    monkeypatch.setattr(
        publication_repository_module,
        "lock_skill_row",
        _fail_after_skill_lock,
    )

    with pytest.raises(RuntimeError, match="cannot begin materialization"):
        repository.begin_materialization(
            attempt_id=attempt_id,
            sc_skill_id=701,
            sc_version_id=801,
            sc_sha256="a" * 64,
            env="test",
        )


def test_mark_failed_rejects_version_that_appears_during_locking(
    monkeypatch,
) -> None:
    db = _Database()
    _space_id, _skill_id, attempt_id = _seed_waiting_publication(db)
    original = publication_repository_module.lock_skill_row

    def _attach_version_after_skill_lock(session, **kwargs):
        skill = original(session, **kwargs)
        session.query(SkillPublicationAttempt).filter(
            SkillPublicationAttempt.id == attempt_id
        ).update({"skill_version_id": 999999}, synchronize_session=False)
        return skill

    monkeypatch.setattr(
        publication_repository_module,
        "lock_skill_row",
        _attach_version_after_skill_lock,
    )

    with pytest.raises(
        RuntimeError, match="a materializing Version cannot become FAILED"
    ):
        SpaceSkillPublicationRepository(db).mark_failed(
            attempt_id=attempt_id,
            error_code="SC_PUBLISH_REJECTED",
            error_message="rejected",
            env="test",
        )


@pytest.mark.parametrize(
    ("drift", "message"),
    [
        ({"status": "FAILED"}, "Attempt is not materializing"),
        ({"skill_version_id": 999999}, "Attempt identity changed"),
    ],
)
def test_complete_success_rechecks_attempt_after_canonical_locks(
    monkeypatch, drift: dict[str, object], message: str
) -> None:
    db = _Database()
    _space_id, _skill_id, attempt_id, version_id = (
        _seed_published_materialization(db)
    )
    original = publication_repository_module.lock_skill_then_exact_version

    def _drift_after_skill_version_lock(session, **kwargs):
        locked = original(session, **kwargs)
        session.query(SkillPublicationAttempt).filter(
            SkillPublicationAttempt.id == attempt_id
        ).update(drift, synchronize_session=False)
        return locked

    monkeypatch.setattr(
        publication_repository_module,
        "lock_skill_then_exact_version",
        _drift_after_skill_version_lock,
    )

    with pytest.raises(RuntimeError, match=message):
        SpaceSkillPublicationRepository(db).complete_success(
            attempt_id=attempt_id,
            skill_version_id=version_id,
            env="test",
        )


def test_complete_success_and_offline_overlap_converges(
    tmp_path: Path, monkeypatch
) -> None:
    db = _ConcurrentDatabase(tmp_path / "publication-offline.db")
    space_id, skill_id, attempt_id, version_id = _seed_published_materialization(db)
    complete_holds_skill_version = Event()
    failures: list[BaseException] = []
    original = publication_repository_module.lock_skill_then_exact_version

    def _pause_after_skill_version_lock(session, **kwargs):
        locked = original(session, **kwargs)
        complete_holds_skill_version.set()
        assert db.offline_begin_attempted.wait(timeout=2)
        return locked

    monkeypatch.setattr(
        publication_repository_module,
        "lock_skill_then_exact_version",
        _pause_after_skill_version_lock,
    )

    def _complete() -> None:
        try:
            SpaceSkillPublicationRepository(db).complete_success(
                attempt_id=attempt_id,
                skill_version_id=version_id,
                env="test",
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            failures.append(exc)

    def _offline() -> None:
        try:
            SpaceSkillOfflineRepository(db).commit(
                space_id=space_id,
                skill_id=skill_id,
                actor_id="owner",
                env="test",
                guard=lambda inspection: None,
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            failures.append(exc)

    completer = Thread(target=_complete, name="complete")
    completer.start()
    assert complete_holds_skill_version.wait(timeout=2)
    offliner = Thread(target=_offline, name="offline")
    offliner.start()
    completer.join(timeout=5)
    offliner.join(timeout=5)

    assert failures == []
    assert not completer.is_alive() and not offliner.is_alive()
    with db.orm_session() as session:
        attempt = session.get(SkillPublicationAttempt, attempt_id)
        skill = session.get(Skill, skill_id)
        assert attempt is not None and attempt.status == "SUCCEEDED"
        assert skill is not None and skill.offline_at is not None


def test_mark_failed_and_begin_materialization_overlap_serializes_to_failed(
    tmp_path: Path, monkeypatch
) -> None:
    db = _ConcurrentDatabase(tmp_path / "publication-failure-begin.db")
    _space_id, skill_id, attempt_id = _seed_waiting_publication(db)
    fail_holds_skill = Event()
    failures: list[BaseException] = []
    expected_begin_errors: list[RuntimeError] = []
    lock_threads: list[str] = []
    original = publication_repository_module.lock_skill_row

    def _observe_skill_lock(session, **kwargs):
        skill = original(session, **kwargs)
        lock_threads.append(current_thread().name)
        if current_thread().name == "fail":
            fail_holds_skill.set()
            assert db.materialization_begin_attempted.wait(timeout=2)
        return skill

    monkeypatch.setattr(
        publication_repository_module,
        "lock_skill_row",
        _observe_skill_lock,
    )

    def _fail() -> None:
        try:
            SpaceSkillPublicationRepository(db).mark_failed(
                attempt_id=attempt_id,
                error_code="SC_PUBLISH_REJECTED",
                error_message="rejected",
                env="test",
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            failures.append(exc)

    def _begin() -> None:
        try:
            SpaceSkillPublicationRepository(db).begin_materialization(
                attempt_id=attempt_id,
                sc_skill_id=701,
                sc_version_id=801,
                sc_sha256="a" * 64,
                env="test",
            )
        except RuntimeError as exc:
            expected_begin_errors.append(exc)
        except BaseException as exc:  # pragma: no cover - asserted below
            failures.append(exc)

    failure = Thread(target=_fail, name="fail")
    failure.start()
    assert fail_holds_skill.wait(timeout=2)
    materialization = Thread(target=_begin, name="begin")
    materialization.start()
    failure.join(timeout=5)
    materialization.join(timeout=5)

    assert failures == []
    assert not failure.is_alive() and not materialization.is_alive()
    assert [str(error) for error in expected_begin_errors] == [
        "Attempt cannot begin materialization"
    ]
    assert lock_threads == ["fail", "begin"]
    with db.orm_session() as session:
        attempt = session.get(SkillPublicationAttempt, attempt_id)
        skill = session.get(Skill, skill_id)
        assert attempt is not None and attempt.status == "FAILED"
        assert skill is not None and skill.draft_status == "EDITING"


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


def test_create_miss_never_locks_the_global_request_gap(monkeypatch) -> None:
    db = _Database()
    space_id, skill_id = _seed_draft(db, lease_holder="owner")
    lock_flags: list[bool] = []
    original = SpaceSkillPublicationRepository._attempt_by_request

    def _observe_request_lookup(session, *, request_id, env, lock):
        lock_flags.append(lock)
        return original(session, request_id=request_id, env=env, lock=lock)

    monkeypatch.setattr(
        SpaceSkillPublicationRepository,
        "_attempt_by_request",
        staticmethod(_observe_request_lookup),
    )

    created = SpaceSkillPublicationRepository(db).create_or_replay_attempt(
        space_id=space_id,
        skill_id=skill_id,
        actor_id="owner",
        request_id="no-global-request-gap-lock",
        env="test",
    )

    assert created.created is True
    assert lock_flags == [False, False]


def test_cross_skill_same_request_converges_to_one_global_identity(
    tmp_path: Path,
) -> None:
    """SQLite serializes state convergence; it does not prove row-lock order."""
    db = _ConcurrentDatabase(tmp_path / "publication-global-request.db")
    first_space, first_skill = _seed_draft(db, lease_holder="owner")
    second_space, second_skill = _seed_draft(
        db,
        lease_holder="owner",
        sc_team_id="sc-team-8",
    )
    start = Barrier(2)
    created_attempt_ids: list[int] = []
    conflicts: list[SpaceSkillIdempotencyConflictError] = []
    failures: list[BaseException] = []

    def _create(space_id: int, skill_id: int) -> None:
        try:
            start.wait(timeout=2)
            result = SpaceSkillPublicationRepository(db).create_or_replay_attempt(
                space_id=space_id,
                skill_id=skill_id,
                actor_id="owner",
                request_id="cross-skill-global-request",
                env="test",
            )
            created_attempt_ids.append(result.attempt.attempt_id)
        except SpaceSkillIdempotencyConflictError as exc:
            conflicts.append(exc)
        except BaseException as exc:  # pragma: no cover - asserted below
            failures.append(exc)

    first = Thread(target=_create, args=(first_space, first_skill))
    second = Thread(target=_create, args=(second_space, second_skill))
    first.start()
    second.start()
    first.join(timeout=5)
    second.join(timeout=5)

    assert failures == []
    assert not first.is_alive() and not second.is_alive()
    assert len(created_attempt_ids) == 1
    assert len(conflicts) == 1
    with db.orm_session() as session:
        assert session.query(SkillPublicationAttempt).count() == 1


def test_cross_skill_unique_insert_loser_reads_winner_after_rollback(
    monkeypatch,
) -> None:
    db = _Database()
    first_space, first_skill = _seed_draft(db, lease_holder="owner")
    second_space, second_skill = _seed_draft(
        db,
        lease_holder="owner",
        sc_team_id="sc-team-8",
    )
    repository = SpaceSkillPublicationRepository(db)
    winner = repository.create_or_replay_attempt(
        space_id=first_space,
        skill_id=first_skill,
        actor_id="owner",
        request_id="integrity-race-global-request",
        env="test",
    )
    original = SpaceSkillPublicationRepository._attempt_by_request
    lookups = 0

    def _hide_winner_until_integrity_rollback(session, *, request_id, env, lock):
        nonlocal lookups
        lookups += 1
        if lookups <= 2:
            return None
        return original(session, request_id=request_id, env=env, lock=lock)

    monkeypatch.setattr(
        SpaceSkillPublicationRepository,
        "_attempt_by_request",
        staticmethod(_hide_winner_until_integrity_rollback),
    )

    with pytest.raises(SpaceSkillIdempotencyConflictError):
        repository.create_or_replay_attempt(
            space_id=second_space,
            skill_id=second_skill,
            actor_id="owner",
            request_id="integrity-race-global-request",
            env="test",
        )

    assert lookups == 3
    with db.orm_session() as session:
        attempts = session.query(SkillPublicationAttempt).all()
        assert [int(attempt.id) for attempt in attempts] == [
            winner.attempt.attempt_id
        ]


def test_create_attempt_rechecks_request_after_skill_lock(monkeypatch) -> None:
    db = _Database()
    space_id, skill_id = _seed_draft(db, lease_holder="owner")
    inserted_attempt_ids: list[int] = []
    original = publication_repository_module.lock_skill_row

    def _concurrent_replay_after_skill_lock(session, **kwargs):
        skill = original(session, **kwargs)
        assert skill is not None
        replay = SkillPublicationAttempt(
            skill_id=skill_id,
            request_id="request-after-skill-lock",
            frozen_draft_locator=skill.zip_url,
            active_skill_key=f"teamclaw:test:{skill_id}",
            target_version_ordinal=int(skill.draft_target_version),
            sc_version_number="1.0.0",
            status="PREPARING",
            recovery_state="AUTO_RETRYING",
            recovery_kind="PREPARATION",
            created_by="owner",
            env="test",
        )
        skill.draft_status = "FROZEN"
        session.add(replay)
        session.flush()
        inserted_attempt_ids.append(int(replay.id))
        return skill

    monkeypatch.setattr(
        publication_repository_module,
        "lock_skill_row",
        _concurrent_replay_after_skill_lock,
    )

    result = SpaceSkillPublicationRepository(db).create_or_replay_attempt(
        space_id=space_id,
        skill_id=skill_id,
        actor_id="owner",
        request_id="request-after-skill-lock",
        env="test",
    )

    assert result.created is False
    assert result.attempt.attempt_id == inserted_attempt_ids[0]


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
        submit_error: SkillCenterGatewayError | None = None,
        exact_missing_once: bool = False,
        status_state: SkillCenterPublishState = SkillCenterPublishState.PUBLISHED,
    ) -> None:
        self.timeout_once = timeout_once
        self.timeout_message = timeout_message
        self.submit_error = submit_error
        self.exact_missing_once = exact_missing_once
        self.status_state = status_state
        self.submit_calls = 0
        self.status_calls = 0

    def submit_publish(self, request):
        self.submit_calls += 1
        if self.submit_error is not None:
            raise self.submit_error
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
        auto_retry_seconds=auto_retry_seconds,
    )


def test_deadline_uses_database_session_clock() -> None:
    db = _Database()
    space_id, skill_id, attempt_id = _seed_waiting_publication(db)
    repository = SpaceSkillPublicationRepository(db)
    with db.orm_session() as session:
        attempt = session.get(SkillPublicationAttempt, attempt_id)
        assert attempt is not None
        attempt.sc_post_started_at = datetime(2026, 8, 30, 10, 0)

    handler = _handler(
        db,
        repository,
        _Gateway(),
        _Materializer(db),
        auto_retry_seconds=15 * 60,
    )

    work = replace(
        repository.get_work(attempt_id=attempt_id, env="test"),
        database_now=datetime(2026, 8, 30, 10, 16),
    )
    assert work.space_id == space_id
    assert work.attempt.skill_id == skill_id
    assert handler._expired(work) is True


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


def test_submit_rejection_logs_safe_upstream_diagnostics(caplog) -> None:
    db = _Database()
    space_id, skill_id = _seed_draft(db, lease_holder="owner")
    repository = SpaceSkillPublicationRepository(db)
    attempt = repository.create_or_replay_attempt(
        space_id=space_id,
        skill_id=skill_id,
        actor_id="owner",
        request_id="worker-business-rejection",
        env="test",
    ).attempt
    with db.orm_session() as session:
        skill = session.get(Skill, skill_id)
        space = session.get(SpaceModel, space_id)
        assert skill is not None
        assert space is not None
        expected_skill_uuid = skill.skill_uuid
        expected_team_id = space.sc_team_id
    secret_url = "https://sc.invalid/download.zip?signature=do-not-leak"
    gateway = _Gateway(
        submit_error=SkillCenterGatewayError(
            SkillCenterGatewayErrorCode.BUSINESS,
            secret_url,
            upstream_code="ILLEGAL_PERMISSION",
            trace_id="sc-trace-rejected",
        )
    )
    handler = _handler(db, repository, gateway, _Materializer(db))

    with caplog.at_level(
        logging.WARNING,
        logger=(
            "agentclaw.community.core.skill_center.services."
            "space_skill_publication_task"
        ),
    ):
        result = handler.handle(publication_task_payload(attempt.attempt_id))

    assert isinstance(result, Complete)
    [record] = [
        record
        for record in caplog.records
        if record.getMessage().startswith("skill_center_publication_failed")
    ]
    assert record.attempt_id == attempt.attempt_id
    assert record.space_id == space_id
    assert record.skill_id == skill_id
    assert record.skill_uuid == expected_skill_uuid
    assert record.team_id == expected_team_id
    assert record.sc_version_number == "1.0.0"
    assert record.gateway_error_code == "business_error"
    assert record.upstream_code == "ILLEGAL_PERMISSION"
    assert record.upstream_trace_id == "sc-trace-rejected"
    assert record.env == "test"
    assert record.operation == "publication_submit"
    assert record.stage == "publish_submit"
    assert f"attempt_id={attempt.attempt_id}" in caplog.text
    assert f"skill_uuid={expected_skill_uuid}" in caplog.text
    assert f"team_id={expected_team_id}" in caplog.text
    assert "upstream_code=ILLEGAL_PERMISSION" in caplog.text
    assert "upstream_trace_id=sc-trace-rejected" in caplog.text
    assert "signature" not in caplog.text
    assert secret_url not in caplog.text


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
