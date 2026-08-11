"""端到端：upgrade 触发 propagate → 影响 Bot 软链刷新 + log 完整记录。"""
import sys
import pytest  # noqa: F401
from unittest.mock import MagicMock
from contextlib import contextmanager

pytestmark = pytest.mark.integration


class InMemorySqliteDB:
    """In-memory SQLite DB for integration testing — does not touch backend.db."""

    def __init__(self):
        import importlib.util
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.ext.declarative import declarative_base

        # Create a working Base and patch it into agentclaw.community.core.base directly
        WorkingBase = declarative_base()
        import agentclaw.community.core.base as core_base

        self._orig_base = core_base.Base
        core_base.Base = WorkingBase

        # Now safe to import the model via the target module's file path
        import agentclaw.community.core.models.skill_propagation_log as target_module
        file_path = target_module.__file__
        spec = importlib.util.spec_from_file_location("skill_propagation_log", file_path)
        # Create a fresh module to avoid double-execution from the import above
        fresh_module = importlib.util.module_from_spec(spec)
        # Replace the sys.modules entry so subsequent imports get the fresh module
        sys.modules["skill_propagation_log"] = fresh_module
        spec.loader.exec_module(fresh_module)
        SkillPropagationLog = fresh_module.SkillPropagationLog

        # Create engine and tables
        self._engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        self._session_factory = sessionmaker(bind=self._engine, autoflush=True)
        try:
            SkillPropagationLog.__table__.create(self._engine, checkfirst=True)
        except Exception:
            # Index already exists — safe to ignore for in-memory DB per fixture call
            pass

    def restore(self):
        """Restore the original Base after test completes."""
        import agentclaw.community.core.base as core_base
        core_base.Base = self._orig_base

    @contextmanager
    def session(self):
        db = self._session_factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    # Unified repo uses orm_session(); mirror session() (commit on clean exit).
    orm_session = session


@pytest.fixture
def e2e_service(tmp_path):
    from agentclaw.community.core.repository.implementations.skill_center.propagation_log import SkillPropagationLogRepository
    from agentclaw.community.core.skill_center.services.skill_propagation_service import SkillPropagationService

    db = InMemorySqliteDB()
    log_repo = SkillPropagationLogRepository(db)
    sync_service = MagicMock()
    sync_service.force_sync.return_value = True

    service = SkillPropagationService(
        log_repo=log_repo,
        sync_service=sync_service,
        skill_set_repo=MagicMock(),
        resolver=MagicMock(),
        device_sync_dispatcher=MagicMock(),
        skill_set_service_factory=MagicMock(),
    )
    service._refresh_bot = lambda *a, **k: True  # mock device sync
    yield service, log_repo, sync_service
    # Restore original Base so other tests are not affected
    db.restore()


def test_upgrade_writes_done_log_and_calls_sync(e2e_service):
    service, log_repo, sync_service = e2e_service
    service._find_affected_bots = MagicMock(return_value=[
        {"bot_id": "bot-X", "owner_id": "100", "engine_type": "openclaw"},
    ])

    result = service.propagate_on_upgrade("uuid-upg", "dev", "2")

    assert result.affected_bot_count == 1
    assert result.success_bot_count == 1
    assert result.failed_bot_ids == []
    sync_service.force_sync.assert_called_once_with(skill_uuid="uuid-upg", env="dev")

    log = log_repo.find_recent("uuid-upg", "dev", within_seconds=60)
    assert log["status"] == "done"


def test_idempotent_within_window(e2e_service):
    service, log_repo, _ = e2e_service
    service._find_affected_bots = MagicMock(return_value=[])

    r1 = service.propagate_on_upgrade("uuid-upg", "dev", "2")
    r2 = service.propagate_on_upgrade("uuid-upg", "dev", "2")
    assert r1.propagation_log_id == r2.propagation_log_id


def test_sync_failure_does_not_block_softlink_refresh(e2e_service):
    service, log_repo, sync_service = e2e_service
    service._find_affected_bots = MagicMock(return_value=[
        {"bot_id": "bot-Y", "owner_id": "200", "engine_type": "openclaw"},
    ])
    sync_service.force_sync.side_effect = RuntimeError("NAS down")

    result = service.propagate_on_upgrade("uuid-upg", "dev", "2")
    assert result.affected_bot_count == 1  # 软链刷新仍进行
    log = log_repo.find_recent("uuid-upg", "dev", within_seconds=60)
    assert log["status"] == "done"  # 整体标 done
