import asyncio
from contextlib import contextmanager
from datetime import datetime
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.bot_management.services.teclaw_publish_task_handler import (
    TECLAW_CREATE_PUBLISH_POLL_TASK,
    TECLAW_PUBLISH_TASK_DEADLINE_SECONDS,
    TeclawPublishTaskHandler,
    TeclawPublishTaskLifecycle,
    build_teclaw_publish_poll_payload,
    map_publish_status,
)
from agentclaw.community.core.devices.repository.record import DeviceBindingRecord
from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
from agentclaw.community.core.task_queue.types import Complete, Fail, Reschedule, Retry
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.plugins.device_repository import DeviceRepository
from agentclaw.community.plugins.local.sqlite_models import EntityDeviceBinding


class _FileSqliteDB:
    def __init__(self, engine):
        self._factory = sessionmaker(
            bind=engine, autocommit=False, autoflush=False
        )

    @contextmanager
    def orm_session(self):
        db = self._factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    session = orm_session


@pytest.fixture
def sqlite_db(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'teclaw-handler.db'}",
        connect_args={"check_same_thread": False},
    )
    EntityDeviceBinding.__table__.create(engine)
    BotModel.__table__.create(engine)
    return _FileSqliteDB(engine)


def _real_pending_teclaw(sqlite_db):
    binding_repo = DeviceRepository(sqlite_db)
    binding_id = binding_repo.insert_binding(
        entity_id="staff-1",
        entity_type="staff",
        device_id="BOT-x",
        device_provider="teclaw",
        env="dev",
        device_props={"publish_id": 9},
        status="PENDING",
        apply_reason=None,
        applied_by="u1",
    )
    with sqlite_db.orm_session() as session:
        session.add(
            BotModel(
                bot_id="b1",
                entity_id="staff-1",
                entity_type="staff",
                creator_id="u1",
                owner_id="u1",
                status="PENDING",
                active_engine="moltis",
                device_id="BOT-x",
                binding_id=binding_id,
                env="dev",
            )
        )
    return binding_id, binding_repo


def _real_statuses(sqlite_db, binding_repo, binding_id):
    binding = binding_repo.get_by_id(binding_id)
    with sqlite_db.orm_session() as session:
        bot_status = session.query(BotModel.status).filter_by(bot_id="b1").scalar()
    return binding, bot_status


def _binding(
    *,
    status: str = "PENDING",
    provider: str = "teclaw",
    publish_id: int | str = 9,
) -> DeviceBindingRecord:
    return DeviceBindingRecord(
        id=77,
        entity_id="staff-1",
        entity_type="staff",
        device_id="BOT-x",
        device_provider=provider,
        env="dev",
        device_props={"publish_id": publish_id},
        status=status,
        apply_reason=None,
        applied_by="u1",
        release_reason=None,
        released_by=None,
        released_at=None,
        last_alive_at=None,
        gmt_create=datetime.now(),
        gmt_modified=datetime.now(),
    )


def _payload(**updates) -> dict:
    payload = build_teclaw_publish_poll_payload(
        binding_id=77,
        bot_id="b1",
        owner_id="u1",
        publish_id=9,
        started_at_epoch_s=100.0,
    )
    payload.update(updates)
    return payload


def _handler(*, clock=lambda: 200.0):
    baas = MagicMock()
    binding_repo = MagicMock()
    binding_repo.get_by_id.return_value = _binding()
    binding_repo.transition_teclaw_publish_terminal.return_value = True
    handler = TeclawPublishTaskHandler(
        baas_service=baas,
        device_binding_repo=binding_repo,
        poll_delay_seconds=10.0,
        clock=clock,
    )
    return handler, baas, binding_repo


def test_build_publish_poll_payload_and_deadline():
    assert TECLAW_PUBLISH_TASK_DEADLINE_SECONDS == 86400
    assert build_teclaw_publish_poll_payload(
        binding_id=77,
        bot_id="b1",
        owner_id="u1",
        publish_id=9,
        started_at_epoch_s=100.0,
    ) == {
        "binding_id": 77,
        "bot_id": "b1",
        "owner_id": "u1",
        "publish_id": 9,
        "started_at_epoch_s": 100.0,
    }


@pytest.mark.parametrize(
    "publish_status,expected",
    [
        ("SUCCESS", "ACTIVE"),
        ("FAILED", "FAILED"),
        ("REJECTED", "FAILED"),
        ("REVOKED", "FAILED"),
        ("PENDING", "PENDING"),
        (None, "PENDING"),
    ],
)
def test_map_publish_status(publish_status, expected):
    assert map_publish_status(publish_status) == expected


def test_pending_publish_reschedules_before_timeout():
    handler, baas, binding_repo = _handler(clock=lambda: 699.0)
    baas.get_publish_progress.return_value = {"status": "PENDING"}

    assert handler.handle(_payload()) == Reschedule(10.0)
    binding_repo.transition_teclaw_publish_terminal.assert_not_called()


def test_timeout_polls_once_then_preserves_pending():
    handler, baas, binding_repo = _handler(clock=lambda: 700.0)
    baas.get_publish_progress.return_value = {"status": "PENDING"}

    assert handler.handle(_payload()) == Complete()
    baas.get_publish_progress.assert_called_once_with(9)
    binding_repo.transition_teclaw_publish_terminal.assert_not_called()


@pytest.mark.parametrize(
    "publish_status,stored_status",
    [
        ("SUCCESS", "ACTIVE"),
        ("FAILED", "FAILED"),
        ("REJECTED", "FAILED"),
        ("REVOKED", "FAILED"),
    ],
)
def test_terminal_publish_uses_guarded_atomic_transition(publish_status, stored_status):
    handler, baas, binding_repo = _handler()
    baas.get_publish_progress.return_value = {"status": publish_status}

    assert handler.handle(_payload()) == Complete()
    binding_repo.transition_teclaw_publish_terminal.assert_called_once_with(
        binding_id=77,
        bot_id="b1",
        owner_id="u1",
        publish_id=9,
        status=stored_status,
    )


def test_terminal_publish_still_converges_after_business_timeout():
    handler, baas, binding_repo = _handler(clock=lambda: 900.0)
    baas.get_publish_progress.return_value = {"status": "SUCCESS"}

    assert handler.handle(_payload()) == Complete()
    binding_repo.transition_teclaw_publish_terminal.assert_called_once_with(
        binding_id=77,
        bot_id="b1",
        owner_id="u1",
        publish_id=9,
        status="ACTIVE",
    )


def test_terminal_publish_does_not_overwrite_binding_released_during_poll(sqlite_db):
    binding_id, binding_repo = _real_pending_teclaw(sqlite_db)
    baas = MagicMock()

    def release_then_succeed(_publish_id):
        binding_repo.release_binding(
            binding_id=binding_id,
            release_reason="user released",
            released_by="u1",
        )
        return {"status": "SUCCESS"}

    baas.get_publish_progress.side_effect = release_then_succeed
    handler = TeclawPublishTaskHandler(
        baas_service=baas,
        device_binding_repo=binding_repo,
    )

    assert handler.handle(_payload(binding_id=binding_id)) == Complete()
    binding, bot_status = _real_statuses(sqlite_db, binding_repo, binding_id)
    assert binding.status == "RELEASED"
    assert bot_status == "PENDING"


def test_terminal_publish_does_not_overwrite_new_publish_id_during_poll(sqlite_db):
    binding_id, binding_repo = _real_pending_teclaw(sqlite_db)
    baas = MagicMock()

    def replace_publish_then_succeed(_publish_id):
        binding_repo.update_device_props(
            binding_id=binding_id,
            props={"publish_id": 10},
        )
        return {"status": "SUCCESS"}

    baas.get_publish_progress.side_effect = replace_publish_then_succeed
    handler = TeclawPublishTaskHandler(
        baas_service=baas,
        device_binding_repo=binding_repo,
    )

    assert handler.handle(_payload(binding_id=binding_id)) == Complete()
    binding, bot_status = _real_statuses(sqlite_db, binding_repo, binding_id)
    assert binding.status == "PENDING"
    assert binding.device_props["publish_id"] == 10
    assert bot_status == "PENDING"


def test_atomic_terminal_write_failure_returns_retry():
    handler, baas, binding_repo = _handler()
    baas.get_publish_progress.return_value = {"status": "SUCCESS"}
    binding_repo.transition_teclaw_publish_terminal.side_effect = RuntimeError(
        "database down"
    )

    outcome = handler.handle(_payload())

    assert isinstance(outcome, Retry)
    assert "database down" in outcome.error


def test_guard_mismatch_after_poll_completes_as_stale():
    handler, baas, binding_repo = _handler()
    baas.get_publish_progress.return_value = {"status": "SUCCESS"}
    binding_repo.transition_teclaw_publish_terminal.return_value = False

    outcome = handler.handle(_payload())

    assert outcome == Complete()


def test_atomic_terminal_write_retries_until_transaction_succeeds():
    handler, baas, binding_repo = _handler()
    baas.get_publish_progress.return_value = {"status": "SUCCESS"}
    binding_repo.transition_teclaw_publish_terminal.side_effect = [
        RuntimeError("db down"),
        True,
    ]

    first = handler.handle(_payload())
    second = handler.handle(_payload())

    assert isinstance(first, Retry)
    assert second == Complete()
    assert binding_repo.transition_teclaw_publish_terminal.call_count == 2


def test_transient_publish_query_returns_retry():
    handler, baas, binding_repo = _handler()
    baas.get_publish_progress.side_effect = RuntimeError("baas down")

    outcome = handler.handle(_payload())

    assert isinstance(outcome, Retry)
    binding_repo.transition_teclaw_publish_terminal.assert_not_called()


def test_lifecycle_registers_handler():
    registry = HandlerRegistry()
    lifecycle = TeclawPublishTaskLifecycle(
        registry=registry,
        baas_service=MagicMock(),
        device_binding_repo=MagicMock(),
    )

    asyncio.run(lifecycle.bootstrap())

    assert isinstance(
        registry.get(TECLAW_CREATE_PUBLISH_POLL_TASK),
        TeclawPublishTaskHandler,
    )


@pytest.mark.parametrize(
    "binding",
    [
        None,
        _binding(status="ACTIVE"),
        _binding(status="FAILED"),
        _binding(status="RELEASED"),
        _binding(provider="baas"),
        _binding(publish_id=10),
    ],
)
def test_stale_or_terminal_binding_completes_without_polling(binding):
    handler, baas, binding_repo = _handler()
    binding_repo.get_by_id.return_value = binding

    assert handler.handle(_payload()) == Complete()
    baas.get_publish_progress.assert_not_called()
    binding_repo.transition_teclaw_publish_terminal.assert_not_called()


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        _payload(binding_id=True),
        _payload(bot_id=7),
        _payload(owner_id=7),
        _payload(publish_id="9"),
        _payload(started_at_epoch_s="100"),
    ],
)
def test_invalid_payload_fails_before_repository_access(payload):
    handler, baas, binding_repo = _handler()

    outcome = handler.handle(payload)

    assert isinstance(outcome, Fail)
    assert outcome.error.startswith("invalid payload:")
    binding_repo.get_by_id.assert_not_called()
    baas.get_publish_progress.assert_not_called()


def test_binding_read_failure_returns_retry():
    handler, baas, binding_repo = _handler()
    binding_repo.get_by_id.side_effect = RuntimeError("binding db down")

    outcome = handler.handle(_payload())

    assert isinstance(outcome, Retry)
    assert "binding db down" in outcome.error
    baas.get_publish_progress.assert_not_called()
