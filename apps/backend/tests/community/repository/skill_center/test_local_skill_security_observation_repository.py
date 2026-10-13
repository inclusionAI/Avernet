"""The security table alone owns upload identity, order, and scan state."""

from contextlib import contextmanager
from pathlib import Path
from threading import Event

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.base import Base
from agentclaw.community.core.models.local_skill_security_observation import (
    LocalSkillSecurityObservationModel,
)
from agentclaw.community.core.models.skill import Skill
from agentclaw.community.core.repository.implementations.skill_center.local_skill_security_observation import (
    LocalSkillSecurityObservationRepository,
)
from agentclaw.community.core.repository.protocols.local_skill_security_observation import (
    AssetIncarnationMismatchError,
)
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope

INCARNATION = "security-uuid-one"


class _Database:
    def __init__(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        with avernet_tenant_scope("teamclaw"), self.sessions.begin() as session:
            session.add(Skill(
                id=42, name="sample", git_path="local://bot-a/sample",
                user_id="owner-a", bolt_id="bot-a", env="pre",
                skill_uuid=INCARNATION,
                avernet_tenant="teamclaw",
            ))

    @contextmanager
    def transactional_orm_session(self):
        session = self.sessions()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


@pytest.fixture
def state() -> tuple[_Database, LocalSkillSecurityObservationRepository]:
    db = _Database()
    return db, LocalSkillSecurityObservationRepository(db)


def _record(repo: LocalSkillSecurityObservationRepository, observation_id: str,
            *, actor: str = "creator-a", skill_id: int = 42,
            incarnation: str = INCARNATION, digest: str = "sha256:" + "a" * 64):
    facts = dict(
        env="pre", bot_id="bot-a", owner_id="owner-a", skill_id=skill_id,
        incarnation_id=incarnation, observation_id=observation_id,
        sha256=digest, snapshot_ref=f"snapshots/{observation_id}.zip",
    )
    order = repo.reserve_handoff(
        **facts, actor_id=actor, cancelled=Event(), name="sample",
        description="description", creator_id="creator-a",
    )
    observation = repo.record_observation(
        **facts, modifier_id=actor,
        name="sample", description="description", upload_order=order,
    )
    return observation


def test_replacement_preserves_identity_and_current_follows_upload_order(state):
    _, repo = state
    with avernet_tenant_scope("teamclaw"):
        first = _record(repo, "upload-a")
        repo.save_report(
            env="pre", observation_id="upload-a", check_id="check-a",
            risk_status="PASS", report={"risk": "PASS"}, detail_url=None,
        )
        second = _record(repo, "upload-b", actor="replacer-b", digest="sha256:" + "b" * 64)
        identity = repo.get_identity_for_asset(
            env="pre", bot_id="bot-a", owner_id="owner-a", skill_id=42,
            incarnation_id=INCARNATION,
        )
        current = repo.get_current_for_asset(
            env="pre", bot_id="bot-a", owner_id="owner-a", skill_id=42,
            incarnation_id=INCARNATION,
        )
        history = repo.list_for_asset(
            env="pre", bot_id="bot-a", owner_id="owner-a", skill_id=42,
            incarnation_id=INCARNATION,
        )
    assert first.provider_skill_id == second.provider_skill_id == f"tc:{INCARNATION}"
    assert first.upload_order < second.upload_order
    assert identity is not None and identity.creator_id == "creator-a"
    assert current is not None and current.observation_id == "upload-b"
    assert [item.observation_id for item in history] == ["upload-b", "upload-a"]
    assert history[1].risk_status == "PASS"


def test_reserved_failure_clears_old_current_without_erasing_history(state):
    _, repo = state
    with avernet_tenant_scope("teamclaw"):
        _record(repo, "upload-a")
        repo.save_report(
            env="pre", observation_id="upload-a", check_id="check-a",
            risk_status="PASS", report={"risk": "PASS"}, detail_url=None,
        )
        order = repo.reserve_handoff(
            env="pre", bot_id="bot-a", owner_id="owner-a", skill_id=42,
            incarnation_id=INCARNATION, observation_id="upload-b",
            sha256="sha256:" + "b" * 64, snapshot_ref="snapshots/upload-b.zip",
            actor_id="replacer-b", cancelled=Event(), name="sample",
            description="replacement", creator_id="creator-a",
        )
        repo.record_handoff_failure(
            env="pre", bot_id="bot-a", owner_id="owner-a", skill_id=42,
            incarnation_id=INCARNATION, observation_id="upload-b",
            sha256="sha256:" + "b" * 64, snapshot_ref="snapshots/upload-b.zip",
            actor_id="replacer-b", stage="SNAPSHOT_UNCERTAIN",
            snapshot_verified=False, upload_order=order,
        )
        current = repo.get_current_for_asset(
            env="pre", bot_id="bot-a", owner_id="owner-a", skill_id=42,
            incarnation_id=INCARNATION,
        )
        handoff = repo.get_latest_handoff_for_asset(
            env="pre", bot_id="bot-a", owner_id="owner-a", skill_id=42,
            incarnation_id=INCARNATION,
        )
        history = repo.list_for_asset(
            env="pre", bot_id="bot-a", owner_id="owner-a", skill_id=42,
            incarnation_id=INCARNATION,
        )
    assert current is None
    assert handoff is not None and handoff.stage == "SNAPSHOT_UNCERTAIN"
    assert len(history) == 1 and history[0].risk_status == "PASS"


def test_unreserved_failure_has_no_order_and_cannot_displace_newer_scan(state):
    db, repo = state
    with avernet_tenant_scope("teamclaw"):
        _record(repo, "upload-a")
        _record(repo, "upload-c", actor="replacer-c")
        with pytest.raises(ValueError, match="cannot be persisted"):
            repo.record_handoff_failure(
                env="pre", bot_id="bot-a", owner_id="owner-a", skill_id=42,
                incarnation_id=INCARNATION, observation_id="upload-b",
                sha256="sha256:" + "b" * 64, snapshot_ref="snapshots/upload-b.zip",
                actor_id="replacer-b", stage="RESERVATION_FAILED",
                snapshot_verified=False,
            )
        current = repo.get_current_for_asset(
            env="pre", bot_id="bot-a", owner_id="owner-a", skill_id=42,
            incarnation_id=INCARNATION,
        )
    with db.sessions() as session:
        assert session.query(LocalSkillSecurityObservationModel).count() == 2
    assert current is not None and current.observation_id == "upload-c"


def test_replacement_inherits_known_creator_when_lookup_failed(state):
    _, repo = state
    with avernet_tenant_scope("teamclaw"):
        _record(repo, "upload-a", actor="creator-a")
        repo.reserve_handoff(
            env="pre", bot_id="bot-a", owner_id="owner-a", skill_id=42,
            incarnation_id=INCARNATION, observation_id="upload-b",
            sha256="sha256:" + "b" * 64, snapshot_ref="snapshots/upload-b.zip",
            actor_id="replacer-b", cancelled=Event(), name="sample",
            description="replacement", creator_id=None,
        )
        identity = repo.get_identity_for_asset(
            env="pre", bot_id="bot-a", owner_id="owner-a", skill_id=42,
            incarnation_id=INCARNATION,
        )
    assert identity is not None and identity.creator_id == "creator-a"


def test_late_timeout_does_not_erase_committed_observation(state):
    _, repo = state
    with avernet_tenant_scope("teamclaw"):
        observed = _record(repo, "upload-a")
        repo.record_handoff_failure(
            env="pre", bot_id="bot-a", owner_id="owner-a", skill_id=42,
            incarnation_id=INCARNATION, observation_id="upload-a",
            sha256=observed.sha256, snapshot_ref=observed.snapshot_ref,
            actor_id="creator-a", stage="HANDOFF_TIMEOUT",
            snapshot_verified=False, upload_order=observed.upload_order,
        )
        current = repo.get_current_for_asset(
            env="pre", bot_id="bot-a", owner_id="owner-a", skill_id=42,
            incarnation_id=INCARNATION,
        )
    assert current is not None and current.observation_id == "upload-a"
    assert current.processing_status == "PENDING"


def test_delete_without_verified_snapshot_records_syncable_tombstone(state):
    db, repo = state
    with avernet_tenant_scope("teamclaw"):
        repo.reserve_handoff(
            env="pre", bot_id="bot-a", owner_id="owner-a", skill_id=42,
            incarnation_id=INCARNATION, observation_id="upload-a",
            sha256="sha256:" + "a" * 64, snapshot_ref="snapshots/upload-a.zip",
            actor_id="creator-a", cancelled=Event(), name="sample",
            description="description", creator_id="creator-a",
        )
    with avernet_tenant_scope("teamclaw"), db.transactional_orm_session() as session:
        session.query(Skill).filter_by(id=42).delete()
    with avernet_tenant_scope("teamclaw"):
        identity = repo.record_asset_deleted(
            env="pre", bot_id="bot-a", owner_id="owner-a",
            skill_id=42, actor_id="deleter-b", incarnation_id=INCARNATION,
        )
        pending = repo.list_pending_sync_identities(
            env="pre", after_provider_skill_id=None, limit=10,
        )
    assert identity is not None
    assert identity.sync_status == "PENDING"
    assert identity.sync_error_code is None
    assert [item.provider_skill_id for item in pending] == [identity.provider_skill_id]


def test_upload_order_conflict_retries_reservation_and_deletion(state, monkeypatch):
    db, repo = state
    with avernet_tenant_scope("teamclaw"):
        _record(repo, "upload-a")

    def conflict_once(method_name):
        original = getattr(repo, method_name)
        attempts = 0

        def write(**kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise IntegrityError(
                    "INSERT", {}, Exception("uk_local_security_upload_order")
                )
            return original(**kwargs)

        monkeypatch.setattr(repo, method_name, write)
        return lambda: attempts

    reserve_attempts = conflict_once("_reserve_handoff_once")
    with avernet_tenant_scope("teamclaw"):
        order = repo.reserve_handoff(
            env="pre", bot_id="bot-a", owner_id="owner-a", skill_id=42,
            incarnation_id=INCARNATION, observation_id="upload-b",
            sha256="sha256:" + "b" * 64,
            snapshot_ref="snapshots/upload-b.zip", actor_id="replacer-b",
            cancelled=Event(), name="sample", description="description",
        )
    assert order == 2
    assert reserve_attempts() == 2

    with avernet_tenant_scope("teamclaw"), db.transactional_orm_session() as session:
        session.query(Skill).filter_by(id=42).delete()
    delete_attempts = conflict_once("_record_asset_deleted_once")
    with avernet_tenant_scope("teamclaw"):
        identity = repo.record_asset_deleted(
            env="pre", bot_id="bot-a", owner_id="owner-a",
            skill_id=42, actor_id="deleter-b", incarnation_id=INCARNATION,
        )
    assert identity is not None
    assert identity.sync_target_observation_id is not None
    assert delete_attempts() == 2
    with avernet_tenant_scope("teamclaw"):
        tombstone = repo.get_by_observation_id(
            env="pre", observation_id=identity.sync_target_observation_id,
        )
    assert tombstone is not None
    assert tombstone.upload_order == 3
    assert tombstone.asset_deleted


def test_unrelated_integrity_error_is_not_retried(state, monkeypatch):
    _, repo = state
    attempts = 0

    def broken_write(**_kwargs):
        nonlocal attempts
        attempts += 1
        raise IntegrityError("INSERT", {}, Exception("NOT NULL constraint failed"))

    monkeypatch.setattr(repo, "_reserve_handoff_once", broken_write)
    with avernet_tenant_scope("teamclaw"), pytest.raises(IntegrityError):
        repo.reserve_handoff(
            env="pre", bot_id="bot-a", owner_id="owner-a", skill_id=42,
            incarnation_id=INCARNATION, observation_id="upload-a",
            sha256="sha256:" + "a" * 64,
            snapshot_ref="snapshots/upload-a.zip", actor_id="creator-a",
            cancelled=Event(), name="sample", description="description",
        )
    assert attempts == 1


def test_old_sync_cannot_replace_new_target(state):
    _, repo = state
    with avernet_tenant_scope("teamclaw"):
        _record(repo, "upload-a")
        _record(repo, "upload-b", actor="replacer-b")
        assert not repo.mark_sync_completed(
            env="pre", bot_id="bot-a", owner_id="owner-a", skill_id=42,
            incarnation_id=INCARNATION, observation_id="upload-a",
        )
        assert repo.mark_sync_completed(
            env="pre", bot_id="bot-a", owner_id="owner-a", skill_id=42,
            incarnation_id=INCARNATION, observation_id="upload-b",
        )


def test_late_errors_cannot_downgrade_completed_scan_or_sync(state):
    _, repo = state
    with avernet_tenant_scope("teamclaw"):
        _record(repo, "upload-a")
        repo.save_report(
            env="pre", observation_id="upload-a", check_id="check-a",
            risk_status="PASS", report={"risk": "PASS"}, detail_url=None,
        )
        after_error = repo.mark_scan_error(
            env="pre", observation_id="upload-a", error_code="LATE_ERROR",
            error_message="stale worker failed",
        )
        assert repo.mark_sync_completed(
            env="pre", bot_id="bot-a", owner_id="owner-a", skill_id=42,
            incarnation_id=INCARNATION, observation_id="upload-a",
        )
        assert not repo.mark_sync_error(
            env="pre", bot_id="bot-a", owner_id="owner-a", skill_id=42,
            incarnation_id=INCARNATION, observation_id="upload-a",
            error_code="LATE_ERROR", error_message="stale worker failed",
        )
        identity = repo.get_identity_for_asset(
            env="pre", bot_id="bot-a", owner_id="owner-a", skill_id=42,
            incarnation_id=INCARNATION,
        )
    assert after_error.processing_status == "COMPLETED"
    assert after_error.risk_status == "PASS"
    assert after_error.error_code is None
    assert identity is not None and identity.sync_status == "COMPLETED"
    assert identity.sync_error_code is None


def test_sqlite_reused_numeric_id_starts_a_new_formal_incarnation(state):
    db, repo = state
    with avernet_tenant_scope("teamclaw"):
        old = _record(repo, "upload-old")
        with db.transactional_orm_session() as session:
            session.add(Skill(
                id=41, name="other", git_path="local://bot-a/other",
                user_id="owner-a", bolt_id="bot-a", env="pre",
                avernet_tenant="teamclaw",
            ))
        with db.transactional_orm_session() as session:
            session.query(Skill).filter_by(id=42).delete()
        old_identity = repo.record_asset_deleted(
            env="pre", bot_id="bot-a", owner_id="owner-a", skill_id=42,
            actor_id="deleter", incarnation_id=INCARNATION,
        )
        with db.transactional_orm_session() as session:
            replacement = Skill(
                name="new", git_path="local://bot-a/new",
                user_id="owner-a", bolt_id="bot-a", env="pre",
                avernet_tenant="teamclaw",
            )
            session.add(replacement)
            session.flush()
            assert replacement.id == 42
        assert repo.get_formal_incarnation_id(
            env="pre", bot_id="bot-a", owner_id="owner-a", skill_id=42,
        ) is None
        repo.bind_formal_incarnation_id(
            env="pre", bot_id="bot-a", owner_id="owner-a", skill_id=42,
            incarnation_id="new-incarnation",
        )
        with pytest.raises(AssetIncarnationMismatchError):
            repo.bind_formal_incarnation_id(
                env="pre", bot_id="bot-a", owner_id="owner-a", skill_id=42,
                incarnation_id=INCARNATION,
            )
        new = _record(repo, "upload-new", incarnation="new-incarnation")
        current = repo.get_current_for_asset(
            env="pre", bot_id="bot-a", owner_id="owner-a", skill_id=42,
            incarnation_id="new-incarnation",
        )
        new_history = repo.list_for_asset(
            env="pre", bot_id="bot-a", owner_id="owner-a", skill_id=42,
            incarnation_id="new-incarnation",
        )
        old_history = repo.list_for_asset(
            env="pre", bot_id="bot-a", owner_id="owner-a", skill_id=42,
            incarnation_id=INCARNATION,
        )
        pending_sync = repo.list_pending_sync_identities(
            env="pre", after_provider_skill_id=None, limit=10,
        )
        recoverable_scans = repo.list_recoverable_scans(
            env="pre", after_observation_id=None, limit=10,
        )
    assert old_identity is not None
    assert old.provider_skill_id != new.provider_skill_id
    assert current is not None and current.observation_id == "upload-new"
    assert [item.observation_id for item in new_history] == ["upload-new"]
    assert old_history[0].asset_deleted
    assert [item.observation_id for item in old_history][1:] == ["upload-old"]
    assert [item.provider_skill_id for item in pending_sync] == [
        old_identity.provider_skill_id
    ]
    assert [item.observation_id for item in recoverable_scans] == ["upload-new"]


def test_asset_and_tenant_scope(state):
    _, repo = state
    with avernet_tenant_scope("teamclaw"):
        _record(repo, "upload-a")
        assert repo.get_formal_incarnation_id(
            env="pre", bot_id="bot-a", owner_id="owner-a", skill_id=42
        ) == INCARNATION
        with pytest.raises(AssetIncarnationMismatchError):
            repo.ensure_for_asset(
                env="pre", bot_id="bot-a", owner_id="owner-a",
                skill_id=42, incarnation_id="wrong-uuid", creator_id=None,
            )
    with avernet_tenant_scope("other-tenant"):
        assert repo.get_by_observation_id(env="pre", observation_id="upload-a") is None


def test_schema_has_one_new_table_and_no_ac_skill_column(state):
    db, _ = state
    names = set(inspect(db.engine).get_table_names())
    assert "ac_local_skill_security_observation" in names
    assert "ac_local_skill_security_identity" not in names
    assert "ac_local_skill_security_handoff" not in names
    skill_columns = {c["name"] for c in inspect(db.engine).get_columns("ac_skill")}
    assert "local_security_incarnation_id" not in skill_columns
    sql_dir = (
        Path(__file__).resolve().parents[4]
        / "src/agentclaw/community/core/skill_center/sql"
    )
    for filename in (
        "2026_10_12_local_skill_security_observation.sql",
        "2026_10_13_local_skill_security_observation_postgresql.sql",
    ):
        ddl = (sql_dir / filename).read_text()
        assert "CREATE TABLE IF NOT EXISTS ac_local_skill_security_observation" in ddl
        assert "ALTER TABLE ac_skill" not in ddl
