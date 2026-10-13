"""Worker behavior for exact Local Skill observations."""

from contextlib import contextmanager
import hashlib
from datetime import timedelta
from threading import Event

import pytest

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.base import Base
from agentclaw.community.core.models.skill import Skill
from agentclaw.community.core.models.local_skill_security_observation import (
    LocalSkillSecurityObservationModel as LocalSkillSecurityHandoffAttemptModel,
)
from agentclaw.community.core.repository.implementations.skill_center.local_skill_security_observation import (
    LocalSkillSecurityObservationRepository as _BaseRepository,
)
from agentclaw.community.plugin_api.local_skill_security_provider import (
    SecurityCheckReport,
    SecurityRemoteArtifact,
    SecuritySubmissionRejected,
    SecuritySubmissionUnknown,
)
from agentclaw.community.core.skill_center.services.local_skill_security_tasks import (
    LocalSkillSecurityScanTaskHandler,
    LocalSkillSecuritySyncTaskHandler,
)
from agentclaw.community.core.skill_center.services.local_skill_security_reconciler import (
    LocalSkillSecurityTaskReconciler,
)
from agentclaw.community.core.skill_center.services.local_skill_security_upload_observer import (
    LocalSkillSecurityUploadObserver,
)
from agentclaw.community.core.task_queue.types import Complete, Fail, Reschedule, Retry
from agentclaw.community.plugin_api.object_storage import (
    ObjectCreateResult,
    ObjectReadResult,
    ObjectReadStatus,
)
from agentclaw.community.plugin_api.local_skill_security_provider import (
    LocalSkillSecurityProviderCapabilities,
)
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope


class _Database:
    def __init__(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self._sessions = sessionmaker(bind=self.engine)
        with (
            avernet_tenant_scope("teamclaw"),
            self.transactional_orm_session() as session,
        ):
            session.add(
                Skill(
                    id=42,
                    name="my-skill",
                    description="",
                    git_path="local://skill",
                    user_id="owner-a",
                    bolt_id="bot-a",
                    env="pre",
                    avernet_tenant="teamclaw",
                )
            )
        with (
            avernet_tenant_scope("external-tenant"),
            self.transactional_orm_session() as session,
        ):
            session.add(
                Skill(
                    id=84,
                    name="other-skill",
                    description="",
                    git_path="local://other",
                    user_id="owner-other",
                    bolt_id="bot-other",
                    env="pre",
                    avernet_tenant="external-tenant",
                )
            )

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



class LocalSkillSecurityObservationRepository(_BaseRepository):
    """Exercise real one-table storage with concise worker fixtures."""

    def ensure_for_asset(self, **kwargs):
        current = self.get_identity_for_asset(
            env=kwargs["env"], bot_id=kwargs["bot_id"],
            owner_id=kwargs["owner_id"], skill_id=kwargs["skill_id"],
            incarnation_id=kwargs["incarnation_id"],
        )
        if current is None:
            # Some worker cases establish a prospective identity before the
            # first handoff. Production creates it with reserve_handoff.
            from types import SimpleNamespace
            return SimpleNamespace(provider_skill_id=f"tc:{kwargs['incarnation_id']}")
        return super().ensure_for_asset(**kwargs)

    def reserve_handoff(self, **kwargs):
        kwargs.setdefault("name", "my-skill")
        kwargs.setdefault("description", "frozen")
        kwargs.pop("upload_order", None)
        return super().reserve_handoff(**kwargs)

    def record_observation(self, **kwargs):
        handoff = self.get_handoff_failure(
            env=kwargs["env"], observation_id=kwargs["observation_id"]
        )
        if handoff is None:
            with self._db.transactional_orm_session() as session:
                from agentclaw.community.core.models.local_skill_security_observation import (
                    LocalSkillSecurityObservationModel as Row,
                )
                row = session.query(Row).filter_by(
                    observation_id=kwargs["observation_id"]
                ).one_or_none()
                existing_order = int(row.upload_order) if row is not None else None
            if row is None:
                order = self.reserve_handoff(
                    env=kwargs["env"], bot_id=kwargs["bot_id"],
                    owner_id=kwargs["owner_id"], skill_id=kwargs["skill_id"],
                    incarnation_id=kwargs["incarnation_id"],
                    observation_id=kwargs["observation_id"],
                    sha256=kwargs["sha256"], snapshot_ref=kwargs["snapshot_ref"],
                    actor_id=kwargs["modifier_id"], cancelled=Event(),
                    name=kwargs.get("name", "my-skill"),
                    description=kwargs.get("description", "frozen"),
                    creator_id="creator-a",
                )
                kwargs["upload_order"] = order
            else:
                kwargs["upload_order"] = existing_order
        else:
            kwargs["upload_order"] = handoff.upload_order
        return super().record_observation(**kwargs)

    def record_handoff_failure(self, **kwargs):
        kwargs.pop("upload_order", None)
        return super().record_handoff_failure(**kwargs)


class _Snapshots:
    def __init__(self, data: bytes):
        self.data = data

    def read_object(self, key: str):
        assert key == "snapshot-a"
        return ObjectReadResult(status=ObjectReadStatus.FOUND, content=self.data)


class _Provider:
    def __init__(self):
        self.exposed = []
        self.submitted = []
        self.polled = []
        self.synced = []
        self.submit_unknown = False
        self.submit_rejected = False

    def capabilities(self):
        return LocalSkillSecurityProviderCapabilities(
            accepts_unchecked_asset_metadata=True,
            ordered_latest_revision_sync=True,
        )

    def expose_artifact(self, request):
        self.exposed.append(request)
        return SecurityRemoteArtifact(
            format="ZIP",
            download_url="https://example.invalid/artifact.zip",
            checksum=request.sha256,
            hash_alg="SHA-256",
        )

    def submit_scan(self, info):
        self.submitted.append(info)
        if self.submit_unknown:
            raise SecuritySubmissionUnknown("response was lost")
        if self.submit_rejected:
            raise SecuritySubmissionRejected("request rejected")
        return SecurityCheckReport(
            check_id="check-a",
            risk_status="DETECTING",
            raw_report={"riskStatus": "DETECTING"},
            detail_url=None,
        )

    def get_scan_report(self, *, tenant, env, check_id):
        self.polled.append((tenant, env, check_id))
        return SecurityCheckReport(
            check_id=check_id,
            risk_status="REJECT",
            raw_report={"riskStatus": "REJECT", "issues": ["danger"]},
            detail_url="https://example.invalid/report",
        )

    def sync_skill_info(self, info):
        self.synced.append(info)


def _record(repository, package: bytes):
    with avernet_tenant_scope("teamclaw"):
        repository.ensure_for_asset(
            env="pre",
            bot_id="bot-a",
            owner_id="owner-a",
            skill_id=42,
            incarnation_id="incarnation-a",
            creator_id="creator-a",
        )
        return repository.record_observation(
            env="pre",
            bot_id="bot-a",
            owner_id="owner-a",
            skill_id=42,
            incarnation_id="incarnation-a",
            observation_id="observation-a",
            name="my-skill",
            description="frozen description",
            sha256="sha256:" + hashlib.sha256(package).hexdigest(),
            snapshot_ref="snapshot-a",
            modifier_id="modifier-a",
        )


def _payload():
    return {
        "avernet_tenant": "teamclaw",
        "env": "pre",
        "observation_id": "observation-a",
    }


def test_scan_exposes_exact_snapshot_polls_and_saves_reject_report():
    package = b"canonical package A"
    repository = LocalSkillSecurityObservationRepository(_Database())
    observation = _record(repository, package)
    provider = _Provider()
    handler = LocalSkillSecurityScanTaskHandler(
        repository=repository,
        snapshots=_Snapshots(package),
        provider=provider,
        env_provider=lambda: "pre",
        enabled=True,
    )

    first = handler.handle(_payload())
    with avernet_tenant_scope("teamclaw"):
        pending = repository.get_by_observation_id(
            env="pre", observation_id="observation-a"
        )
    second = handler.handle(_payload())
    third = handler.handle(_payload())

    assert isinstance(first, Reschedule)
    assert pending is not None and pending.risk_status == "DETECTING"
    assert pending.processing_status == "CHECK_SUBMITTED"
    assert isinstance(second, Complete)
    assert isinstance(third, Complete)
    assert len(provider.exposed) == 1
    assert provider.exposed[0].snapshot_ref == observation.snapshot_ref
    assert provider.exposed[0].version == "1.0"
    assert provider.exposed[0].sha256 == hashlib.sha256(package).hexdigest()
    assert not hasattr(provider.exposed[0], "canonical_zip")
    assert len(provider.submitted) == 1
    assert provider.submitted[0].provider_skill_id == observation.provider_skill_id
    assert provider.submitted[0].name == "my-skill"
    assert provider.submitted[0].description == "frozen description"
    assert provider.submitted[0].snapshot_ref == observation.snapshot_ref
    assert provider.submitted[0].version == "1.0"
    assert provider.submitted[0].status == "ONLINE"
    assert provider.submitted[0].platform_del_flag == 0
    assert provider.polled == [("teamclaw", "pre", "check-a")]
    with avernet_tenant_scope("teamclaw"):
        saved = repository.get_by_observation_id(
            env="pre", observation_id="observation-a"
        )
    assert saved is not None and saved.processing_status == "COMPLETED"
    assert saved.risk_status == "REJECT"
    assert saved.report == {"riskStatus": "REJECT", "issues": ["danger"]}


def test_snapshot_digest_mismatch_never_exposes_or_submits_artifact():
    repository = LocalSkillSecurityObservationRepository(_Database())
    _record(repository, b"frozen original")
    provider = _Provider()
    handler = LocalSkillSecurityScanTaskHandler(
        repository=repository,
        snapshots=_Snapshots(b"changed package"),
        provider=provider,
        env_provider=lambda: "pre",
        enabled=True,
    )

    assert isinstance(handler.handle(_payload()), Fail)
    assert provider.exposed == []
    assert provider.submitted == []
    with avernet_tenant_scope("teamclaw"):
        saved = repository.get_by_observation_id(
            env="pre", observation_id="observation-a"
        )
    assert saved is not None and saved.error_code == "SNAPSHOT_DIGEST_MISMATCH"


def test_ambiguous_submit_is_never_submitted_twice():
    package = b"canonical package A"
    repository = LocalSkillSecurityObservationRepository(_Database())
    _record(repository, package)
    provider = _Provider()
    provider.submit_unknown = True
    handler = LocalSkillSecurityScanTaskHandler(
        repository=repository,
        snapshots=_Snapshots(package),
        provider=provider,
        env_provider=lambda: "pre",
        enabled=True,
    )

    assert isinstance(handler.handle(_payload()), Fail)
    assert isinstance(handler.handle(_payload()), Fail)
    assert len(provider.submitted) == 1
    with avernet_tenant_scope("teamclaw"):
        saved = repository.get_by_observation_id(
            env="pre", observation_id="observation-a"
        )
    assert saved is not None and saved.processing_status == "SUBMIT_UNKNOWN"


def test_definitive_submission_rejection_is_recorded_without_risk_verdict():
    package = b"canonical package A"
    repository = LocalSkillSecurityObservationRepository(_Database())
    _record(repository, package)
    provider = _Provider()
    provider.submit_rejected = True
    handler = LocalSkillSecurityScanTaskHandler(
        repository=repository,
        snapshots=_Snapshots(package),
        provider=provider,
        env_provider=lambda: "pre",
        enabled=True,
    )

    assert isinstance(handler.handle(_payload()), Fail)
    assert isinstance(handler.handle(_payload()), Fail)
    assert len(provider.submitted) == 1
    with avernet_tenant_scope("teamclaw"):
        saved = repository.get_by_observation_id(
            env="pre", observation_id="observation-a"
        )
    assert saved is not None
    assert saved.processing_status == "FAILED"
    assert saved.risk_status is None
    assert saved.error_code == "PROVIDER_SUBMISSION_REJECTED"


def test_metadata_sync_uses_asset_even_while_scan_is_detecting():
    package = b"canonical package A"
    repository = LocalSkillSecurityObservationRepository(_Database())
    _record(repository, package)
    provider = _Provider()
    sync = LocalSkillSecuritySyncTaskHandler(
        repository=repository,
        provider=provider,
        env_provider=lambda: "pre",
        enabled=True,
    )
    scan = LocalSkillSecurityScanTaskHandler(
        repository=repository,
        snapshots=_Snapshots(package),
        provider=provider,
        env_provider=lambda: "pre",
        enabled=True,
    )

    assert isinstance(sync.handle(_payload()), Reschedule)
    assert isinstance(scan.handle(_payload()), Reschedule)
    assert isinstance(sync.handle(_payload()), Complete)
    assert len(provider.synced) == 1
    assert provider.synced[0].asset_skill_id == 42
    assert provider.synced[0].version == "1.0"
    assert provider.synced[0].status == "ONLINE"
    assert provider.synced[0].platform_del_flag == 0
    with avernet_tenant_scope("teamclaw"):
        identity = repository.get_identity_for_asset(
            env="pre",
            bot_id="bot-a",
            owner_id="owner-a",
            skill_id=42,
            incarnation_id="incarnation-a",
        )
    assert identity is not None
    assert identity.sync_completed_observation_id == "observation-a"


def test_deleted_skill_syncs_offline_without_a_live_ac_skill_row():
    package = b"canonical package A"
    db = _Database()
    repository = LocalSkillSecurityObservationRepository(db)
    original = _record(repository, package)
    with avernet_tenant_scope("teamclaw"), db.transactional_orm_session() as session:
        session.query(Skill).filter_by(id=42).delete()
    with avernet_tenant_scope("teamclaw"):
        identity = repository.record_asset_deleted(
            env="pre", bot_id="bot-a", owner_id="owner-a",
            skill_id=42, actor_id="deleter-b",
        )
        pending = repository.list_pending_sync_identities(
            env="pre", after_provider_skill_id=None, limit=10,
        )
        assert not repository.mark_sync_completed(
            env="pre", bot_id="bot-a", owner_id="owner-a", skill_id=42,
            incarnation_id="incarnation-a", observation_id="observation-a",
        )
    assert identity is not None
    assert identity.sync_target_observation_id != original.observation_id
    assert [item.provider_skill_id for item in pending] == [identity.provider_skill_id]
    assert identity.sync_target_observation_id is not None
    _age_handoff(db, identity.sync_target_observation_id)
    with avernet_tenant_scope("teamclaw"):
        assert all(
            item.observation_id != identity.sync_target_observation_id
            for item in repository.list_repairable_handoffs(
                env="pre", after_observation_id=None, limit=100,
            )
        )
        assert not repository.reopen_verified_handoff(
            env="pre", observation_id=identity.sync_target_observation_id,
            verified_sha256=original.sha256,
        )

    provider = _Provider()
    handler = LocalSkillSecuritySyncTaskHandler(
        repository=repository, provider=provider,
        env_provider=lambda: "pre", enabled=True,
    )
    assert isinstance(handler.handle(_payload()), Complete)
    assert len(provider.synced) == 1
    sent = provider.synced[0]
    assert sent.observation_id == identity.sync_target_observation_id
    assert sent.artifact is None
    assert sent.status == "OFFLINE" and sent.platform_del_flag == 1
    assert sent.version == "1.0"
    with avernet_tenant_scope("teamclaw"):
        current = repository.get_identity_for_asset(
            env="pre", bot_id="bot-a", owner_id="owner-a",
            skill_id=42, incarnation_id="incarnation-a",
        )
    assert current is not None
    assert current.sync_completed_observation_id == sent.observation_id


def test_deleted_skill_syncs_offline_without_a_verified_snapshot():
    db = _Database()
    repository = LocalSkillSecurityObservationRepository(db)
    with avernet_tenant_scope("teamclaw"):
        repository.reserve_handoff(
            env="pre", bot_id="bot-a", owner_id="owner-a", skill_id=42,
            incarnation_id="incarnation-a", observation_id="observation-a",
            sha256="sha256:" + "a" * 64, snapshot_ref="snapshot-a",
            actor_id="creator-a", cancelled=Event(), creator_id="creator-a",
        )
    with avernet_tenant_scope("teamclaw"), db.transactional_orm_session() as session:
        session.query(Skill).filter_by(id=42).delete()
    with avernet_tenant_scope("teamclaw"):
        identity = repository.record_asset_deleted(
            env="pre", bot_id="bot-a", owner_id="owner-a",
            skill_id=42, actor_id="deleter-b",
        )
    assert identity is not None
    assert identity.sync_target_observation_id is not None

    provider = _Provider()
    handler = LocalSkillSecuritySyncTaskHandler(
        repository=repository, provider=provider,
        env_provider=lambda: "pre", enabled=True,
    )
    assert isinstance(handler.handle({
        **_payload(), "observation_id": identity.sync_target_observation_id,
    }), Complete)
    assert len(provider.synced) == 1
    assert provider.synced[0].status == "OFFLINE"
    assert provider.synced[0].artifact is None


def test_terminal_snapshot_failure_stops_sync_requeues():
    package = b"canonical package A"
    repository = LocalSkillSecurityObservationRepository(_Database())
    _record(repository, package)
    with avernet_tenant_scope("teamclaw"):
        repository.mark_scan_error(
            env="pre",
            observation_id="observation-a",
            error_code="SNAPSHOT_DIGEST_MISMATCH",
            error_message="content changed",
        )
    provider = _Provider()
    handler = LocalSkillSecuritySyncTaskHandler(
        repository=repository,
        provider=provider,
        env_provider=lambda: "pre",
        enabled=True,
    )
    assert isinstance(handler.handle(_payload()), Fail)
    assert provider.synced == []
    with avernet_tenant_scope("teamclaw"):
        identity = repository.get_identity_for_asset(
            env="pre",
            bot_id="bot-a",
            owner_id="owner-a",
            skill_id=42,
            incarnation_id="incarnation-a",
        )
    assert identity is not None and identity.sync_error_code == "ARTIFACT_UNAVAILABLE"
    queue = _Queue()
    LocalSkillSecurityTaskReconciler(
        repository=repository,
        snapshots=_Snapshots(package),
        queue=queue,
        env_provider=lambda: "pre",
    ).reconcile_once()
    assert queue.enqueued == []


class _Queue:
    def __init__(self):
        self.enqueued = []

    def enqueue(self, task_type, payload, deadline_seconds, *, idempotency_key):
        self.enqueued.append((task_type, payload, idempotency_key))


@pytest.mark.parametrize("read_failure", ["failed", "exception"])
def test_committed_snapshot_survives_transient_observer_read_failure(read_failure):
    package = b"exact immutable package"
    db = _Database()
    repository = LocalSkillSecurityObservationRepository(db)

    class _Store:
        def __init__(self):
            self.key = None
            self.content = None
            self.fail_read = True

        def create_object_if_absent(self, key, content):
            self.key, self.content = key, content
            return ObjectCreateResult.CREATED

        def read_object(self, key):
            assert key == self.key
            if self.fail_read:
                if read_failure == "exception":
                    raise OSError("temporary OSS read failure")
                return ObjectReadResult(ObjectReadStatus.FAILED)
            return ObjectReadResult(ObjectReadStatus.FOUND, self.content)

    store = _Store()
    queue = _Queue()
    observer = LocalSkillSecurityUploadObserver(
        repository=repository,
        objects=store,
        queue=queue,
        env_provider=lambda: "pre",
    )
    expected_error = OSError if read_failure == "exception" else RuntimeError
    with avernet_tenant_scope("teamclaw"), pytest.raises(expected_error):
        observer.observe(
            bot_id="bot-a",
            owner_id="owner-a",
            actor_id="creator-a",
            skill_id=42,
            incarnation_id="incarnation-a",
            operation="created",
            name="frozen-name",
            description="frozen-description",
            canonical_zip=package,
            observation_id="observation-a",
            cancelled=Event(),
        )
    with avernet_tenant_scope("teamclaw"):
        failure = repository.get_handoff_failure(
            env="pre", observation_id="observation-a"
        )
    assert failure is not None and failure.stage == "SNAPSHOT_UNCERTAIN"
    assert failure.snapshot_verified is False
    assert store.content == package  # The create actually committed.
    store.fail_read = False
    _age_handoff(db, "observation-a")
    LocalSkillSecurityTaskReconciler(
        repository=repository,
        snapshots=store,
        queue=queue,
        env_provider=lambda: "pre",
    ).reconcile_once()
    with avernet_tenant_scope("teamclaw"):
        current = repository.get_current_for_asset(
            env="pre",
            bot_id="bot-a",
            owner_id="owner-a",
            skill_id=42,
            incarnation_id="incarnation-a",
        )
    assert current is not None
    assert current.observation_id == "observation-a"
    assert current.sha256 == "sha256:" + hashlib.sha256(package).hexdigest()
    assert {task for task, _, _ in queue.enqueued} == {
        "local_skill_security_scan",
    }


def test_observer_readback_digest_mismatch_does_not_recover_or_scan():
    repository = LocalSkillSecurityObservationRepository(_Database())

    class _TamperedStore:
        def create_object_if_absent(self, key, content):
            self.key = key
            return ObjectCreateResult.CREATED

        def read_object(self, key):
            assert key == self.key
            return ObjectReadResult(ObjectReadStatus.FOUND, b"tampered")

    store = _TamperedStore()
    queue = _Queue()
    observer = LocalSkillSecurityUploadObserver(
        repository=repository,
        objects=store,
        queue=queue,
        env_provider=lambda: "pre",
    )
    with avernet_tenant_scope("teamclaw"), pytest.raises(RuntimeError):
        observer.observe(
            bot_id="bot-a",
            owner_id="owner-a",
            actor_id="creator-a",
            skill_id=42,
            incarnation_id="incarnation-a",
            operation="created",
            name="skill",
            description="frozen",
            canonical_zip=b"valid package",
            observation_id="observation-a",
            cancelled=Event(),
        )
    with avernet_tenant_scope("teamclaw"):
        failure = repository.get_handoff_failure(
            env="pre", observation_id="observation-a"
        )
    assert failure is not None and failure.stage == "SNAPSHOT_DIGEST_MISMATCH"
    assert (failure.name, failure.description, failure.creator_id) == (
        "skill", "frozen", "creator-a",
    )
    LocalSkillSecurityTaskReconciler(
        repository=repository,
        snapshots=store,
        queue=queue,
        env_provider=lambda: "pre",
    ).reconcile_once()
    assert queue.enqueued == []


def _age_handoff(db: _Database, observation_id: str, tenant: str = "teamclaw") -> None:
    with avernet_tenant_scope(tenant), db.transactional_orm_session() as session:
        row = (
            session.query(LocalSkillSecurityHandoffAttemptModel)
            .filter_by(observation_id=observation_id)
            .one()
        )
        row.gmt_created = session.execute(select(func.now())).scalar_one() - timedelta(
            minutes=1
        )


def test_unreserved_upload_cannot_be_recovered_without_a_scan_row():
    # Accepted observation-mode boundary: no unrelated audit table is used
    # to invent an ordering token after the scan table write fails.
    repository = LocalSkillSecurityObservationRepository(_Database())
    with avernet_tenant_scope("teamclaw"):
        assert repository.list_repairable_handoffs(
            env="pre", after_observation_id=None, limit=100,
        ) == ()
        assert repository.get_formal_incarnation_id(
            env="pre", bot_id="bot-a", owner_id="owner-a", skill_id=42,
        ) is None


@pytest.mark.parametrize("stage", ["PENDING", "SNAPSHOT_UNCERTAIN"])
def test_deleted_asset_missing_snapshot_handoff_becomes_terminal_without_oss_read(
    stage: str,
):
    db = _Database()
    repository = LocalSkillSecurityObservationRepository(db)
    with avernet_tenant_scope("teamclaw"):
        repository.reserve_handoff(
            env="pre",
            bot_id="bot-a",
            owner_id="owner-a",
            actor_id="creator-a",
            skill_id=42,
            incarnation_id="incarnation-a",
            observation_id="orphaned-a",
            sha256="sha256:old",
            snapshot_ref="snapshots/missing.zip",
            cancelled=Event(),
            upload_order=100,
            name="old",
            description="old",
            creator_id="creator-a",
        )
        if stage == "SNAPSHOT_UNCERTAIN":
            repository.record_handoff_failure(
                env="pre",
                bot_id="bot-a",
                owner_id="owner-a",
                actor_id="creator-a",
                skill_id=42,
                incarnation_id="incarnation-a",
                observation_id="orphaned-a",
                sha256="sha256:old",
                snapshot_ref="snapshots/missing.zip",
                stage=stage,
                snapshot_verified=False,
            )
        with db.transactional_orm_session() as session:
            session.query(Skill).filter_by(id=42).delete()
    _age_handoff(db, "orphaned-a")

    class _NoRead:
        def read_object(self, key):
            pytest.fail(f"stale handoff must not read OSS: {key}")

    queue = _Queue()
    reconciler = LocalSkillSecurityTaskReconciler(
        repository=repository,
        snapshots=_NoRead(),
        queue=queue,
        env_provider=lambda: "pre",
    )
    reconciler.reconcile_once()
    with avernet_tenant_scope("teamclaw"):
        failure = repository.get_handoff_failure(env="pre", observation_id="orphaned-a")
        assert failure is not None
        assert failure.stage == "ASSET_INCARNATION_STALE"
        assert failure.snapshot_verified is False
        assert (
            repository.list_repairable_handoffs(
                env="pre", after_observation_id=None, limit=10
            )
            == ()
        )
    reconciler.reconcile_once()
    assert queue.enqueued == []


def test_abandoned_pending_handoff_recovers_from_exact_frozen_snapshot():
    package = b"frozen content"
    digest = "sha256:" + hashlib.sha256(package).hexdigest()
    db = _Database()
    repository = LocalSkillSecurityObservationRepository(db)
    with avernet_tenant_scope("teamclaw"):
        repository.reserve_handoff(
            env="pre",
            bot_id="bot-a",
            owner_id="owner-a",
            actor_id="creator-a",
            skill_id=42,
            incarnation_id="incarnation-a",
            observation_id="crashed-a",
            sha256=digest,
            snapshot_ref="snapshots/crashed-a.zip",
            cancelled=Event(),
            upload_order=100,
            name="frozen-name",
            description="frozen-description",
            creator_id="creator-a",
        )

    class _CrashSnapshot:
        def __init__(self):
            self.reads = 0
            self.available = False

        def read_object(self, key):
            assert key == "snapshots/crashed-a.zip"
            self.reads += 1
            if not self.available:
                return ObjectReadResult(ObjectReadStatus.FAILED)
            return ObjectReadResult(ObjectReadStatus.FOUND, package)

    snapshot = _CrashSnapshot()
    queue = _Queue()
    reconciler = LocalSkillSecurityTaskReconciler(
        repository=repository,
        snapshots=snapshot,
        queue=queue,
        env_provider=lambda: "pre",
    )
    reconciler.reconcile_once()  # A live upload is never stolen by recovery.
    assert snapshot.reads == 0
    _age_handoff(db, "crashed-a")
    reconciler.reconcile_once()  # Temporary OSS read failure remains retryable.
    assert snapshot.reads == 1 and queue.enqueued == []
    snapshot.available = True
    reconciler.reconcile_once()
    with avernet_tenant_scope("teamclaw"):
        current = repository.get_current_for_asset(
            env="pre",
            bot_id="bot-a",
            owner_id="owner-a",
            skill_id=42,
            incarnation_id="incarnation-a",
        )
        identity = repository.get_identity_for_asset(
            env="pre",
            bot_id="bot-a",
            owner_id="owner-a",
            skill_id=42,
            incarnation_id="incarnation-a",
        )
    assert current is not None and current.observation_id == "crashed-a"
    assert (current.name, current.description, current.sha256) == (
        "frozen-name",
        "frozen-description",
        digest,
    )
    assert identity is not None and identity.creator_id == "creator-a"
    assert {task for task, _, _ in queue.enqueued} == {
        "local_skill_security_scan",
    }


def test_abandoned_older_pending_handoff_cannot_displace_newer_current():
    package = b"frozen content"
    digest = "sha256:" + hashlib.sha256(package).hexdigest()
    db = _Database()
    repository = LocalSkillSecurityObservationRepository(db)
    with avernet_tenant_scope("teamclaw"):
        repository.ensure_for_asset(
            env="pre",
            bot_id="bot-a",
            owner_id="owner-a",
            skill_id=42,
            incarnation_id="incarnation-a",
            creator_id="creator-a",
        )
        repository.reserve_handoff(
            env="pre",
            bot_id="bot-a",
            owner_id="owner-a",
            actor_id="creator-a",
            skill_id=42,
            incarnation_id="incarnation-a",
            observation_id="crashed-a",
            sha256=digest,
            snapshot_ref="snapshots/crashed-a.zip",
            cancelled=Event(),
            upload_order=100,
            name="old",
            description="old-description",
            creator_id="creator-a",
        )
        repository.reserve_handoff(
            env="pre",
            bot_id="bot-a",
            owner_id="owner-a",
            actor_id="modifier-b",
            skill_id=42,
            incarnation_id="incarnation-a",
            observation_id="current-b",
            sha256=digest,
            snapshot_ref="snapshot-b",
            cancelled=Event(),
            upload_order=101,
            name="new",
            description="new-description",
            creator_id="creator-a",
        )
        repository.record_observation(
            env="pre",
            bot_id="bot-a",
            owner_id="owner-a",
            skill_id=42,
            incarnation_id="incarnation-a",
            observation_id="current-b",
            sha256=digest,
            snapshot_ref="snapshot-b",
            modifier_id="modifier-b",
            name="new",
            description="new-description",
            upload_order=101,
        )
    _age_handoff(db, "crashed-a")

    class _OldSnapshot:
        def read_object(self, key):
            assert key == "snapshots/crashed-a.zip"
            return ObjectReadResult(ObjectReadStatus.FOUND, package)

    LocalSkillSecurityTaskReconciler(
        repository=repository,
        snapshots=_OldSnapshot(),
        queue=_Queue(),
        env_provider=lambda: "pre",
    ).reconcile_once()
    with avernet_tenant_scope("teamclaw"):
        current = repository.get_current_for_asset(
            env="pre",
            bot_id="bot-a",
            owner_id="owner-a",
            skill_id=42,
            incarnation_id="incarnation-a",
        )
        old = repository.get_by_observation_id(env="pre", observation_id="crashed-a")
    assert current is not None and current.observation_id == "current-b"
    assert old is not None and old.description == "old-description"


def test_abandoned_pending_handoff_retries_not_found_until_slow_put_completes():
    package = b"late immutable package"
    digest = "sha256:" + hashlib.sha256(package).hexdigest()
    db = _Database()
    repository = LocalSkillSecurityObservationRepository(db)
    with avernet_tenant_scope("teamclaw"):
        repository.reserve_handoff(
            env="pre",
            bot_id="bot-a",
            owner_id="owner-a",
            actor_id="creator-a",
            skill_id=42,
            incarnation_id="incarnation-a",
            observation_id="crashed-a",
            sha256=digest,
            snapshot_ref="snapshots/crashed-a.zip",
            cancelled=Event(),
            upload_order=100,
            name="frozen",
            description="frozen",
            creator_id="creator-a",
        )
    _age_handoff(db, "crashed-a")

    class _LateSnapshot:
        def __init__(self):
            self.available = False
            self.reads = 0

        def read_object(self, key):
            assert key == "snapshots/crashed-a.zip"
            self.reads += 1
            if not self.available:
                return ObjectReadResult(ObjectReadStatus.NOT_FOUND)
            return ObjectReadResult(ObjectReadStatus.FOUND, package)

    snapshots = _LateSnapshot()
    queue = _Queue()
    reconciler = LocalSkillSecurityTaskReconciler(
        repository=repository,
        snapshots=snapshots,
        queue=queue,
        env_provider=lambda: "pre",
    )
    reconciler.reconcile_once()
    with avernet_tenant_scope("teamclaw"):
        handoff = repository.get_latest_handoff_for_asset(
            env="pre",
            bot_id="bot-a",
            owner_id="owner-a",
            skill_id=42,
            incarnation_id="incarnation-a",
        )
        current = repository.get_current_for_asset(
            env="pre",
            bot_id="bot-a",
            owner_id="owner-a",
            skill_id=42,
            incarnation_id="incarnation-a",
        )
    assert handoff is not None and handoff.stage == "PENDING"
    assert handoff.snapshot_verified is False
    assert current is None and queue.enqueued == []
    snapshots.available = True  # The timed-out original put finishes later.
    reconciler.reconcile_once()
    with avernet_tenant_scope("teamclaw"):
        current = repository.get_current_for_asset(
            env="pre",
            bot_id="bot-a",
            owner_id="owner-a",
            skill_id=42,
            incarnation_id="incarnation-a",
        )
    assert snapshots.reads == 2
    assert current is not None and current.observation_id == "crashed-a"
    assert {task for task, _, _ in queue.enqueued} == {
        "local_skill_security_scan",
    }


def test_recovery_discovery_uses_env_status_indexes():
    db = _Database()
    plans = {
        "scan": (
            "SELECT DISTINCT avernet_tenant FROM ac_local_skill_security_observation "
            "WHERE env = 'pre' AND processing_status = 'PENDING'"
        ),
        "sync": (
            "SELECT DISTINCT avernet_tenant FROM ac_local_skill_security_observation "
            "WHERE env = 'pre' AND sync_status = 'PENDING'"
        ),
        "handoff": (
            "SELECT DISTINCT avernet_tenant FROM ac_local_skill_security_observation "
            "WHERE env = 'pre' AND handoff_stage = 'PENDING' "
            "AND gmt_created <= CURRENT_TIMESTAMP"
        ),
    }
    with db.engine.connect() as connection:
        for suffix, query in plans.items():
            plan = connection.exec_driver_sql("EXPLAIN QUERY PLAN " + query).fetchall()
            assert any(
                f"idx_local_security_recover_{suffix}" in row[3] for row in plan
            ), plan


def test_reconciler_reenqueues_unfinished_scan_and_sync_after_queue_loss():
    package = b"canonical package A"
    repository = LocalSkillSecurityObservationRepository(_Database())
    observation = _record(repository, package)
    with avernet_tenant_scope("teamclaw"):
        repository.save_external_artifact(
            env="pre", observation_id=observation.observation_id,
            artifact={
                "format": "ZIP", "download_url": "https://example.invalid/scan.zip",
                "checksum": observation.sha256, "hash_alg": "SHA-256",
            },
        )
    queue = _Queue()
    reconciler = LocalSkillSecurityTaskReconciler(
        repository=repository,
        snapshots=_Snapshots(package),
        queue=queue,
        env_provider=lambda: "pre",
    )

    reconciler.reconcile_once()

    assert [item[0] for item in queue.enqueued] == [
        "local_skill_security_scan",
        "local_skill_security_sync",
    ]
    assert all(item[1]["observation_id"] == "observation-a" for item in queue.enqueued)
    assert queue.enqueued[0][2] == "local-security-scan:observation-a"
    assert queue.enqueued[1][2].startswith("local-security-sync:tc:")


def test_reconciler_discovers_pending_work_in_external_tenant_without_request_scope():
    package = b"exact package"
    repository = LocalSkillSecurityObservationRepository(_Database())
    _record(repository, package)
    with avernet_tenant_scope("external-tenant"):
        repository.ensure_for_asset(
            env="pre",
            bot_id="bot-other",
            owner_id="owner-other",
            skill_id=84,
            incarnation_id="incarnation-b",
            creator_id="first-uploader",
        )
        repository.record_observation(
            env="pre",
            bot_id="bot-other",
            owner_id="owner-other",
            skill_id=84,
            incarnation_id="incarnation-b",
            observation_id="external-observation",
            name="skill",
            description="external",
            sha256="sha256:" + hashlib.sha256(package).hexdigest(),
            snapshot_ref="external-snapshot",
            modifier_id="first-uploader",
        )
    queue = _Queue()
    LocalSkillSecurityTaskReconciler(
        repository=repository,
        snapshots=_Snapshots(package),
        queue=queue,
        env_provider=lambda: "pre",
    ).reconcile_once()
    assert {
        (task, payload["avernet_tenant"]) for task, payload, _ in queue.enqueued
    } == {
        ("local_skill_security_scan", "teamclaw"),
        ("local_skill_security_scan", "external-tenant"),
    }


def test_verified_identity_failure_rebuilds_observation_from_exact_snapshot():
    package = b"original immutable package"
    db = _Database()
    repository = LocalSkillSecurityObservationRepository(db)
    digest = "sha256:" + hashlib.sha256(package).hexdigest()
    with avernet_tenant_scope("external-tenant"):
        order = repository.reserve_handoff(
            env="pre",
            bot_id="bot-other",
            owner_id="owner-other",
            actor_id="creator-a",
            skill_id=84,
            incarnation_id="incarnation-b",
            observation_id="failed-a",
            sha256=digest,
            snapshot_ref="snapshots/failed-a.zip",
            cancelled=Event(),
        )
        repository.record_handoff_failure(
            env="pre",
            bot_id="bot-other",
            owner_id="owner-other",
            actor_id="creator-a",
            skill_id=84,
            incarnation_id="incarnation-b",
            observation_id="failed-a",
            sha256=digest,
            snapshot_ref="snapshots/failed-a.zip",
            stage="IDENTITY_FAILED",
            snapshot_verified=True,
            name="frozen-name",
            description="frozen-description",
            creator_id="creator-a",
        )
    _age_handoff(db, "failed-a", "external-tenant")

    class _ExactSnapshot:
        def read_object(self, key):
            assert key == "snapshots/failed-a.zip"
            return ObjectReadResult(ObjectReadStatus.FOUND, package)

    queue = _Queue()
    LocalSkillSecurityTaskReconciler(
        repository=repository,
        snapshots=_ExactSnapshot(),
        queue=queue,
        env_provider=lambda: "pre",
    ).reconcile_once()
    with avernet_tenant_scope("external-tenant"):
        current = repository.get_current_for_asset(
            env="pre",
            bot_id="bot-other",
            owner_id="owner-other",
            skill_id=84,
            incarnation_id="incarnation-b",
        )
        identity = repository.get_identity_for_asset(
            env="pre",
            bot_id="bot-other",
            owner_id="owner-other",
            skill_id=84,
            incarnation_id="incarnation-b",
        )
    assert current is not None
    assert current.observation_id == "failed-a"
    assert current.upload_order == order
    assert (current.name, current.description, current.sha256) == (
        "frozen-name",
        "frozen-description",
        digest,
    )
    assert identity is not None and identity.creator_id == "creator-a"
    assert {task for task, _, _ in queue.enqueued} == {
        "local_skill_security_scan",
    }


def test_identity_repair_refuses_snapshot_with_different_digest():
    db = _Database()
    repository = LocalSkillSecurityObservationRepository(db)
    with avernet_tenant_scope("teamclaw"):
        repository.reserve_handoff(
            env="pre",
            bot_id="bot-a",
            owner_id="owner-a",
            actor_id="creator-a",
            skill_id=42,
            incarnation_id="incarnation-a",
            observation_id="failed-a",
            sha256="sha256:" + "a" * 64,
            snapshot_ref="snapshots/failed-a.zip",
            cancelled=Event(),
        )
        repository.record_handoff_failure(
            env="pre",
            bot_id="bot-a",
            owner_id="owner-a",
            actor_id="creator-a",
            skill_id=42,
            incarnation_id="incarnation-a",
            observation_id="failed-a",
            sha256="sha256:" + "a" * 64,
            snapshot_ref="snapshots/failed-a.zip",
            stage="IDENTITY_FAILED",
            snapshot_verified=True,
            name="frozen",
            description="frozen",
            creator_id="creator-a",
        )
    _age_handoff(db, "failed-a")

    class _WrongSnapshot:
        def read_object(self, key):
            assert key == "snapshots/failed-a.zip"
            return ObjectReadResult(ObjectReadStatus.FOUND, b"different")

    queue = _Queue()
    LocalSkillSecurityTaskReconciler(
        repository=repository,
        snapshots=_WrongSnapshot(),
        queue=queue,
        env_provider=lambda: "pre",
    ).reconcile_once()
    with avernet_tenant_scope("teamclaw"):
        assert (
            repository.get_by_observation_id(env="pre", observation_id="failed-a")
            is None
        )
        failure = repository.get_handoff_failure(env="pre", observation_id="failed-a")
    assert failure is not None and failure.stage == "REPAIR_SNAPSHOT_MISMATCH"
    assert queue.enqueued == []


def test_identity_repair_stops_when_verified_snapshot_was_deleted():
    db = _Database()
    repository = LocalSkillSecurityObservationRepository(db)
    with avernet_tenant_scope("teamclaw"):
        repository.reserve_handoff(
            env="pre",
            bot_id="bot-a",
            owner_id="owner-a",
            actor_id="creator-a",
            skill_id=42,
            incarnation_id="incarnation-a",
            observation_id="failed-a",
            sha256="sha256:" + "a" * 64,
            snapshot_ref="snapshots/failed-a.zip",
            cancelled=Event(),
        )
        repository.record_handoff_failure(
            env="pre",
            bot_id="bot-a",
            owner_id="owner-a",
            actor_id="creator-a",
            skill_id=42,
            incarnation_id="incarnation-a",
            observation_id="failed-a",
            sha256="sha256:" + "a" * 64,
            snapshot_ref="snapshots/failed-a.zip",
            stage="IDENTITY_FAILED",
            snapshot_verified=True,
            name="frozen",
            description="frozen",
            creator_id="creator-a",
        )
    _age_handoff(db, "failed-a")

    class _MissingSnapshot:
        def read_object(self, key):
            assert key == "snapshots/failed-a.zip"
            return ObjectReadResult(ObjectReadStatus.NOT_FOUND)

    queue = _Queue()
    reconciler = LocalSkillSecurityTaskReconciler(
        repository=repository,
        snapshots=_MissingSnapshot(),
        queue=queue,
        env_provider=lambda: "pre",
    )
    reconciler.reconcile_once()
    reconciler.reconcile_once()
    with avernet_tenant_scope("teamclaw"):
        failure = repository.get_handoff_failure(env="pre", observation_id="failed-a")
    assert failure is not None and failure.stage == "REPAIR_SNAPSHOT_MISSING"
    assert queue.enqueued == []


def test_enqueue_failure_with_staged_artifact_reconciles_and_submits_once():
    package = b"canonical package A"
    repository = LocalSkillSecurityObservationRepository(_Database())
    _record(repository, package)
    with avernet_tenant_scope("teamclaw"):
        repository.save_external_artifact(
            env="pre",
            observation_id="observation-a",
            artifact={
                "format": "ZIP",
                "download_url": "https://example.invalid/a.zip",
                "checksum": hashlib.sha256(package).hexdigest(),
                "hash_alg": "SHA-256",
            },
        )
        repository.mark_scan_error(
            env="pre",
            observation_id="observation-a",
            error_code="TASK_ENQUEUE_FAILED",
            error_message="queue was unavailable",
        )
    queue = _Queue()
    LocalSkillSecurityTaskReconciler(
        repository=repository,
        snapshots=_Snapshots(package),
        queue=queue,
        env_provider=lambda: "pre",
    ).reconcile_once()
    assert any(item[0] == "local_skill_security_scan" for item in queue.enqueued)

    provider = _Provider()
    handler = LocalSkillSecurityScanTaskHandler(
        repository=repository,
        snapshots=_Snapshots(package),
        provider=provider,
        env_provider=lambda: "pre",
        enabled=True,
    )
    assert isinstance(handler.handle(_payload()), Reschedule)
    assert len(provider.submitted) == 1
    assert provider.exposed == []  # staged access metadata is reused
    with avernet_tenant_scope("teamclaw"):
        saved = repository.get_by_observation_id(
            env="pre",
            observation_id="observation-a",
        )
    assert saved is not None and saved.processing_status == "CHECK_SUBMITTED"
    assert saved.error_code is None


def test_reconciler_never_requeues_ambiguous_scan_submission():
    package = b"canonical package A"
    repository = LocalSkillSecurityObservationRepository(_Database())
    _record(repository, package)
    with avernet_tenant_scope("teamclaw"):
        repository.mark_submit_unknown(
            env="pre",
            observation_id="observation-a",
            error_code="UNKNOWN",
            error_message="response lost",
        )
    queue = _Queue()
    LocalSkillSecurityTaskReconciler(
        repository=repository,
        snapshots=_Snapshots(package),
        queue=queue,
        env_provider=lambda: "pre",
    ).reconcile_once()

    assert not any(item[0] == "local_skill_security_scan" for item in queue.enqueued)


def test_sync_replays_newer_replacement_after_old_request_returns():
    package = b"canonical package A"
    repository = LocalSkillSecurityObservationRepository(_Database())
    first = _record(repository, package)
    with avernet_tenant_scope("teamclaw"):
        repository.save_external_artifact(
            env="pre",
            observation_id="observation-a",
            artifact={
                "format": "ZIP",
                "download_url": "https://example.invalid/a.zip",
                "checksum": first.sha256,
                "hash_alg": "SHA-256",
            },
        )

    class _ReplacingProvider(_Provider):
        def sync_skill_info(self, info):
            super().sync_skill_info(info)
            if info.observation_id == "observation-a":
                with avernet_tenant_scope("teamclaw"):
                    repository.record_observation(
                        env="pre",
                        bot_id="bot-a",
                        owner_id="owner-a",
                        skill_id=42,
                        incarnation_id="incarnation-a",
                        observation_id="observation-b",
                        name="my-skill",
                        description="new description",
                        sha256=first.sha256,
                        snapshot_ref="snapshot-b",
                        modifier_id="modifier-b",
                    )
                    repository.save_external_artifact(
                        env="pre",
                        observation_id="observation-b",
                        artifact={
                            "format": "ZIP",
                            "download_url": "https://example.invalid/b.zip",
                            "checksum": first.sha256,
                            "hash_alg": "SHA-256",
                        },
                    )

    provider = _ReplacingProvider()
    handler = LocalSkillSecuritySyncTaskHandler(
        repository=repository,
        provider=provider,
        env_provider=lambda: "pre",
        enabled=True,
    )

    assert isinstance(handler.handle(_payload()), Reschedule)
    assert isinstance(handler.handle(_payload()), Complete)
    assert [item.observation_id for item in provider.synced] == [
        "observation-a",
        "observation-b",
    ]
    with avernet_tenant_scope("teamclaw"):
        identity = repository.get_identity_for_asset(
            env="pre",
            bot_id="bot-a",
            owner_id="owner-a",
            skill_id=42,
            incarnation_id="incarnation-a",
        )
    assert identity is not None
    assert identity.sync_completed_observation_id == "observation-b"


def test_metadata_sync_failure_is_independent_and_recovers_on_retry():
    package = b"canonical package A"
    repository = LocalSkillSecurityObservationRepository(_Database())
    _record(repository, package)
    with avernet_tenant_scope("teamclaw"):
        repository.save_external_artifact(
            env="pre",
            observation_id="observation-a",
            artifact={
                "format": "ZIP",
                "download_url": "https://example.invalid/a.zip",
                "checksum": hashlib.sha256(package).hexdigest(),
                "hash_alg": "SHA-256",
            },
        )

    class _FlakyProvider(_Provider):
        def __init__(self):
            super().__init__()
            self.fail_next = True

        def sync_skill_info(self, info):
            if self.fail_next:
                self.fail_next = False
                raise RuntimeError("temporary provider failure")
            super().sync_skill_info(info)

    provider = _FlakyProvider()
    handler = LocalSkillSecuritySyncTaskHandler(
        repository=repository,
        provider=provider,
        env_provider=lambda: "pre",
        enabled=True,
    )

    assert isinstance(handler.handle(_payload()), Retry)
    with avernet_tenant_scope("teamclaw"):
        failed = repository.get_identity_for_asset(
            env="pre",
            bot_id="bot-a",
            owner_id="owner-a",
            skill_id=42,
            incarnation_id="incarnation-a",
        )
    assert failed is not None and failed.sync_status == "FAILED"
    assert isinstance(handler.handle(_payload()), Complete)
    with avernet_tenant_scope("teamclaw"):
        completed = repository.get_identity_for_asset(
            env="pre",
            bot_id="bot-a",
            owner_id="owner-a",
            skill_id=42,
            incarnation_id="incarnation-a",
        )
    assert completed is not None
    assert completed.sync_completed_observation_id == "observation-a"
    assert len(provider.synced) == 1


def test_reclaimed_scan_with_unrecorded_submission_stays_unknown():
    package = b"canonical package A"
    repository = LocalSkillSecurityObservationRepository(_Database())
    _record(repository, package)
    with avernet_tenant_scope("teamclaw"):
        repository.save_external_artifact(
            env="pre",
            observation_id="observation-a",
            artifact={
                "format": "ZIP",
                "download_url": "https://example.invalid/a.zip",
                "checksum": "sha256:" + hashlib.sha256(package).hexdigest(),
                "hash_alg": "SHA-256",
            },
        )
        assert repository.claim_scan_submission(
            env="pre", observation_id="observation-a"
        )
    provider = _Provider()
    handler = LocalSkillSecurityScanTaskHandler(
        repository=repository,
        snapshots=_Snapshots(package),
        provider=provider,
        env_provider=lambda: "pre",
        enabled=True,
    )

    assert isinstance(handler.handle(_payload()), Fail)
    assert provider.submitted == []
    with avernet_tenant_scope("teamclaw"):
        saved = repository.get_by_observation_id(
            env="pre", observation_id="observation-a"
        )
    assert saved is not None and saved.processing_status == "SUBMIT_UNKNOWN"
