"""A submitted immutable Skill scan remains historical after asset deletion."""

from agentclaw.community.core.models.skill import Skill
from agentclaw.community.core.skill_center.services.local_skill_security_tasks import (
    LocalSkillSecurityScanTaskHandler,
)
from agentclaw.community.core.task_queue.types import Complete, Fail, Reschedule
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope

from .test_local_skill_security_tasks import (
    LocalSkillSecurityObservationRepository,
    _Database,
    _payload,
    _Provider,
    _record,
    _Snapshots,
)


def _delete_formal_asset(db: _Database, repository: LocalSkillSecurityObservationRepository) -> None:
    with avernet_tenant_scope("teamclaw"), db.transactional_orm_session() as session:
        session.query(Skill).filter_by(id=42).delete()
    with avernet_tenant_scope("teamclaw"):
        assert repository.record_asset_deleted(
            env="pre", bot_id="bot-a", owner_id="owner-a",
            skill_id=42, actor_id="deleter-b",
        ) is not None


def test_submitted_scan_keeps_polling_after_formal_skill_is_deleted():
    package = b"canonical package A"
    db = _Database()
    repository = LocalSkillSecurityObservationRepository(db)
    _record(repository, package)
    provider = _Provider()
    handler = LocalSkillSecurityScanTaskHandler(
        repository=repository,
        snapshots=_Snapshots(package),
        provider=provider,
        env_provider=lambda: "pre",
        enabled=True,
    )

    assert isinstance(handler.handle(_payload()), Reschedule)
    _delete_formal_asset(db, repository)
    with avernet_tenant_scope("teamclaw"):
        recoverable = repository.list_recoverable_scans(
            env="pre", after_observation_id=None, limit=10,
        )

    assert [item.observation_id for item in recoverable] == ["observation-a"]
    assert isinstance(handler.handle(_payload()), Complete)
    assert provider.polled == [("teamclaw", "pre", "check-a")]
    assert len(provider.submitted) == 1
    with avernet_tenant_scope("teamclaw"):
        saved = repository.get_by_observation_id(
            env="pre", observation_id="observation-a"
        )
    assert saved is not None and saved.processing_status == "COMPLETED"
    assert saved.risk_status == "REJECT"


def test_deleted_skill_without_check_id_is_not_submitted_for_scan():
    package = b"canonical package A"
    db = _Database()
    repository = LocalSkillSecurityObservationRepository(db)
    _record(repository, package)
    _delete_formal_asset(db, repository)
    with avernet_tenant_scope("teamclaw"):
        assert repository.list_recoverable_scans(
            env="pre", after_observation_id=None, limit=10,
        ) == ()
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
