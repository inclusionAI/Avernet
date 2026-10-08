"""Real SQLite persistence contracts for the coding restart journal and dedup."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.bot_management.engines.aicoding.restart_state import (
    KEY,
    TASK_TYPE,
    task_key,
)
from agentclaw.community.core.bot_management.engines.registry import (
    resolve_restart_strategy,
)
from agentclaw.community.core.bot_management.engines.restart_contract import (
    RestartServices,
)
from agentclaw.community.core.repository.implementations.bot.bot import BotRepository
from agentclaw.community.core.repository.implementations.platform.task_queue import (
    TaskQueueRepository,
)
from agentclaw.community.core.task_queue.repository.models import TaskQueueModel
from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
from agentclaw.community.core.task_queue.services.task_queue_service import (
    TaskQueueService,
)
from agentclaw.community.core.task_queue.services.wakeup import WorkerWakeup
from agentclaw.community.di.config import TaskQueueConfig
from agentclaw.community.plugin_api.models import BotModel


@pytest.fixture
def persisted(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'restart.db'}",
        connect_args={"check_same_thread": False},
    )
    BotModel.__table__.create(engine)
    TaskQueueModel.__table__.create(engine)
    sessions = sessionmaker(bind=engine)

    class DB:
        @contextmanager
        def orm_session(self):
            with sessions() as session:
                try:
                    yield session
                    session.commit()
                except BaseException:
                    session.rollback()
                    raise

    db = DB()
    repo = BotRepository(db)
    bot = repo.insert(
        dict(
            bot_id="bot",
            owner_id="owner",
            entity_id="owner",
            entity_type="staff",
            creator_id="owner",
            bot_name="Coding",
            active_engine="claude_code",
            bot_type="personal",
            status="ACTIVE",
            binding_id=7,
            ext={"unrelated": 1},
        )
    )
    queue = TaskQueueService(
        TaskQueueRepository(db),
        HandlerRegistry(),
        WorkerWakeup(),
        TaskQueueConfig(),
        SimpleNamespace(
            current_trace_id=lambda: None, export_trace_carrier=lambda: None
        ),
    )

    def get_bot(*_):
        return {
            **repo.get_by_id_and_owner("bot", "owner"),
            "device_binding": {
                "device_id": "old",
                "device_provider": "baas",
                "status": "ACTIVE",
            },
        }

    ctx, strategy = resolve_restart_strategy(bot)
    services = RestartServices(repo, queue, get_bot, Mock())
    yield SimpleNamespace(
        repo=repo, queue=queue, strategy=strategy, ctx=ctx, services=services
    )
    engine.dispose()


def test_admission_persists_status_and_journal_using_existing_repository(persisted):
    p = persisted
    result = p.strategy._submit_restart(
        p.ctx, p.services, {"bot_id": "bot", "user_id": "owner"}
    )
    bot = p.repo.get_by_id_and_owner("bot", "owner")
    task = p.queue.find_by_idempotency_key(TASK_TYPE, task_key("bot", "owner"))
    assert result["status"] == bot["status"] == "PENDING"
    assert bot["binding_id"] == 7
    assert bot["ext"]["unrelated"] == 1
    assert bot["ext"][KEY]["operation_id"] == task.payload["operation_id"]


def test_real_database_deduplicates_concurrent_submissions(persisted):
    p = persisted

    def submit(_):
        return p.strategy._submit_restart(
            p.ctx, p.services, {"bot_id": "bot", "user_id": "owner"}
        )

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(submit, range(12)))
    assert len({result["restart_operation_id"] for result in results}) == 1
    assert p.repo.get_by_id_and_owner("bot", "owner")["status"] == "PENDING"


def test_stale_cas_cannot_clobber_ext_or_status(persisted):
    repo = persisted.repo
    initial = repo.get_by_id_and_owner("bot", "owner")
    repo.update_by_owner("bot", "owner", {"ext": {"concurrent": True}})
    assert (
        repo.compare_and_set_ext(
            bot_id="bot",
            owner_id="owner",
            expected_ext=initial["ext"],
            ext={KEY: {"phase": "QUEUED"}},
        )
        is None
    )
    bot = repo.get_by_id_and_owner("bot", "owner")
    assert bot["status"] == "ACTIVE"
    assert bot["ext"] == {"concurrent": True}


def test_ext_only_cas_still_preserves_status(persisted):
    repo = persisted.repo
    initial = repo.get_by_id_and_owner("bot", "owner")
    updated = repo.compare_and_set_ext(
        bot_id="bot", owner_id="owner", expected_ext=initial["ext"], ext={"changed": 1}
    )
    assert updated["status"] == "ACTIVE"
    assert updated["ext"] == {"changed": 1}


def test_failure_persists_bot_status_and_existing_error_fields(persisted):
    from agentclaw.community.core.bot_management.engines.aicoding.restart_state import (
        RestartState,
    )

    p = persisted
    accepted = p.strategy._submit_restart(
        p.ctx, p.services, {"bot_id": "bot", "user_id": "owner"}
    )
    state = RestartState(p.repo, "bot", "owner", accepted["restart_operation_id"])
    state.fail("容器最终备份失败，旧容器未销毁")
    bot = p.repo.get_by_id_and_owner("bot", "owner")
    assert bot["status"] == "FAILED"
    assert bot["ext"]["start_status"] == "FAILED"
    assert bot["ext"]["start_message"] == "容器最终备份失败，旧容器未销毁"
    assert bot["binding_id"] == 7
    assert bot["ext"]["unrelated"] == 1


def test_admission_status_write_failure_propagates_and_retry_repairs(persisted):
    from unittest.mock import patch

    p = persisted
    with patch.object(p.repo, "update_by_owner", side_effect=RuntimeError("write failed")):
        with pytest.raises(RuntimeError, match="write failed"):
            p.strategy._submit_restart(
                p.ctx, p.services, {"bot_id": "bot", "user_id": "owner"}
            )
    bot = p.repo.get_by_id_and_owner("bot", "owner")
    operation = bot["ext"][KEY]["operation_id"]
    assert bot["status"] == "ACTIVE"
    assert bot["ext"][KEY]["phase"] == "QUEUED"
    result = p.strategy._submit_restart(
        p.ctx, p.services, {"bot_id": "bot", "user_id": "owner"}
    )
    assert result["status"] == "PENDING"
    assert result["restart_operation_id"] == operation


def test_failure_status_write_failure_propagates_and_worker_can_repair(persisted):
    from unittest.mock import patch
    from agentclaw.community.core.bot_management.engines.aicoding.restart_state import (
        RestartState,
    )

    p = persisted
    p.strategy._submit_restart(p.ctx, p.services, {"bot_id": "bot", "user_id": "owner"})
    task = p.queue.find_by_idempotency_key(TASK_TYPE, task_key("bot", "owner"))
    state = RestartState(p.repo, "bot", "owner", task.payload["operation_id"])
    with patch.object(p.repo, "update_by_owner", side_effect=RuntimeError("write failed")):
        with pytest.raises(RuntimeError, match="write failed"):
            state.fail("backup failed")
    partial = p.repo.get_by_id_and_owner("bot", "owner")
    assert partial["status"] == "PENDING"
    assert partial["ext"]["start_status"] == "FAILED"
    repaired = state.ensure(task.payload, task.id)
    assert repaired["status"] == "FAILED"
    assert repaired["ext"]["start_message"] == "backup failed"


def test_status_only_write_does_not_overwrite_concurrent_ext(persisted):
    from unittest.mock import patch

    p = persisted
    original = p.repo.update_by_owner

    def concurrent_write(bot_id, owner_id, changes):
        assert set(changes) == {"status"}
        bot = p.repo.get_by_id_and_owner(bot_id, owner_id)
        original(bot_id, owner_id, {"ext": {**bot["ext"], "callback": "keep"}})
        return original(bot_id, owner_id, changes)

    with patch.object(p.repo, "update_by_owner", side_effect=concurrent_write):
        p.strategy._submit_restart(p.ctx, p.services, {"bot_id": "bot", "user_id": "owner"})
    bot = p.repo.get_by_id_and_owner("bot", "owner")
    assert bot["status"] == "PENDING"
    assert bot["ext"]["callback"] == "keep"
    assert bot["ext"][KEY]["phase"] == "QUEUED"
