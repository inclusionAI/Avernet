"""Tests for the online-instance guard in the restart flow.

When a FAILED or ACTIVE online binding already occupies the slot,
restart_bot and execute_restart must reject the request (error_code
``duplicate_online_instance``) instead of creating a duplicate online
row in baas_device via _recreate_restart_target.
"""
from unittest.mock import AsyncMock, Mock, patch

import pytest

from agentclaw.community.core.devices.models import DeviceBindingStatus
from agentclaw.community.core.service_bot.repository.models import (
    BotPublishRecord,
    PublishStatus,
)
from agentclaw.community.core.service_bot.services.publish_flow.online_instance_guard import (
    DuplicateOnlineInstanceError,
    check_existing_online_instance,
)
from agentclaw.community.core.service_bot.services.publish_flow_service import (
    PublishFlowService,
)
from agentclaw.community.core.service_bot.services.publish_flow.operation_runner import (
    TargetBotGoneError,
)
from agentclaw.community.core.service_bot.types import PublishStage


# ---------------------------------------------------------------------------
# Helpers (mirrors test_publish_flow_service.py)
# ---------------------------------------------------------------------------

def _make_publish_record(**kwargs):
    from datetime import datetime

    data = dict(
        id=kwargs.get("id", 1),
        source_bot_pk=kwargs.get("source_bot_pk", 11),
        source_bot_id=kwargs.get("source_bot_id", "bot-source"),
        publish_bot_id=kwargs.get("publish_bot_id", "bot-pub-1"),
        name=kwargs.get("name", "demo"),
        description=kwargs.get("description", "desc"),
        owner_id=kwargs.get("owner_id", "u1"),
        owner_name=kwargs.get("owner_name", "user1"),
        status=kwargs.get("status", PublishStatus.VALIDATING.value),
        version=kwargs.get("version", 1),
        last_pub_id=kwargs.get("last_pub_id", 0),
        env=kwargs.get("env", "dev"),
        ext=kwargs.get("ext", {}),
        permission_owner=kwargs.get("permission_owner", "u1"),
        gmt_create=kwargs.get("gmt_create", datetime.now()),
        gmt_modified=kwargs.get("gmt_modified", datetime.now()),
    )
    return BotPublishRecord(**data)


def _arca_router(build_service=None):
    from agentclaw.community.core.service_bot.services.deploy.arca_snapshot_producer import (
        ArcaSnapshotProducer,
    )
    from agentclaw.community.core.service_bot.services.deploy.producer import (
        DeployArtifactProducerRouter,
    )

    skills_builder = Mock()
    skills_builder.capture.return_value = None
    arca = ArcaSnapshotProducer(build_service or Mock(), skills_builder)
    return DeployArtifactProducerRouter(
        providers={"arca": arca, "baas": arca}, default_provider_key="baas"
    )


def _pf(*args, **kw):
    """Construct PublishFlowService for tests."""
    from contextlib import contextmanager

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from agentclaw.community.core.base import Base
    from agentclaw.community.core.service_bot.repository.models import (  # noqa: F401
        PublishOperationModel,
    )
    from agentclaw.community.plugins.publish_operation_repository import (
        OrmPublishOperationRepository as PublishOperationRepository,
    )

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    class _DB:
        def __init__(self, e):
            self._f = sessionmaker(bind=e, autoflush=False)

        @contextmanager
        def orm_session(self):
            db = self._f()
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()

    kw.setdefault("common_config_service", Mock())
    kw.setdefault("task_queue_service", Mock())
    kw.setdefault("resolver", Mock())
    kw.setdefault("device_fs_dispatcher", Mock())
    kw.setdefault("teclaw_file_promotion", Mock())
    kw.setdefault("device_binding_repo", Mock())
    kw.setdefault("publish_operation_repo", PublishOperationRepository(_DB(engine)))
    baas = args[2] if len(args) >= 3 else kw.get("baas_service")
    if isinstance(baas, Mock):
        baas.list_bot_publishes.return_value = []
    if "channel_overrides_reader" not in kw:
        reader = Mock()
        reader.overrides_for_stage.return_value = {}
        kw["channel_overrides_reader"] = reader
    return PublishFlowService(*args, **kw)


def _svc_with_record(record, *, build_service=None, baas_service=None,
                     bot_service=None, task_queue_service=None):
    """PublishFlowService whose publish_service.get_publish_by_id returns `record`."""
    publish_service = Mock()
    publish_service.get_publish_by_id.return_value = record
    build_service = build_service or Mock()
    kw = {}
    if task_queue_service is not None:
        kw["task_queue_service"] = task_queue_service
    svc = _pf(
        publish_service,
        build_service,
        baas_service or Mock(),
        bot_service or Mock(),
        _arca_router(build_service),
        **kw,
    )
    return svc, publish_service


def _binding_mock(device_id="BOT-x", status=DeviceBindingStatus.ACTIVE):
    """Create a Mock binding with the specified status."""
    return Mock(device_id=device_id, status=status)


def _setup_restart(svc, record, bot_uuid="BOT-x"):
    """Wire the resolution mocks execute_restart re-reads from publish_id."""
    svc._publish_service.get_publish_by_id = Mock(return_value=record)
    svc._publish_service.get_device_binding_by_id = Mock(
        return_value=Mock(device_id=bot_uuid)
    )
    svc._bot_service.get_bot = Mock(return_value={"bot_id": "b", "entity_id": "u"})
    svc._mutate_and_update_ext = Mock()
    svc.refresh_publish_handle = Mock()


# ---------------------------------------------------------------------------
# Unit tests for check_existing_online_instance
# ---------------------------------------------------------------------------

class TestCheckExistingOnlineInstance:
    """Unit tests for the standalone guard function."""

    def test_raises_when_binding_active(self):
        publish_service = Mock()
        record = _make_publish_record(
            status=PublishStatus.SUCCESS.value,
            ext={"binding": {"online": 42}},
        )
        publish_service.get_publish_by_id.return_value = record
        publish_service.get_device_binding_by_id.return_value = _binding_mock(
            status=DeviceBindingStatus.ACTIVE,
        )

        with pytest.raises(DuplicateOnlineInstanceError) as exc_info:
            check_existing_online_instance(publish_service, 1, PublishStage.ONLINE)

        assert "duplicate" in str(exc_info.value).lower() or "already exists" in str(exc_info.value).lower()

    def test_raises_when_binding_failed(self):
        publish_service = Mock()
        record = _make_publish_record(
            status=PublishStatus.SUCCESS.value,
            ext={"binding": {"online": 42}},
        )
        publish_service.get_publish_by_id.return_value = record
        publish_service.get_device_binding_by_id.return_value = _binding_mock(
            status=DeviceBindingStatus.FAILED,
        )

        with pytest.raises(DuplicateOnlineInstanceError):
            check_existing_online_instance(publish_service, 1, PublishStage.ONLINE)

    def test_passes_when_binding_released(self):
        publish_service = Mock()
        record = _make_publish_record(
            status=PublishStatus.SUCCESS.value,
            ext={"binding": {"online": 42}},
        )
        publish_service.get_publish_by_id.return_value = record
        publish_service.get_device_binding_by_id.return_value = _binding_mock(
            status=DeviceBindingStatus.RELEASED,
        )

        # Should not raise
        check_existing_online_instance(publish_service, 1, PublishStage.ONLINE)

    def test_passes_when_no_record(self):
        publish_service = Mock()
        publish_service.get_publish_by_id.return_value = None

        # Should not raise
        check_existing_online_instance(publish_service, 1, PublishStage.ONLINE)

    def test_passes_when_no_binding_id_in_ext(self):
        publish_service = Mock()
        record = _make_publish_record(
            status=PublishStatus.SUCCESS.value,
            ext={},
        )
        publish_service.get_publish_by_id.return_value = record

        # Should not raise
        check_existing_online_instance(publish_service, 1, PublishStage.ONLINE)

    def test_passes_when_binding_not_found(self):
        publish_service = Mock()
        record = _make_publish_record(
            status=PublishStatus.SUCCESS.value,
            ext={"binding": {"online": 42}},
        )
        publish_service.get_publish_by_id.return_value = record
        publish_service.get_device_binding_by_id.return_value = None

        # Should not raise
        check_existing_online_instance(publish_service, 1, PublishStage.ONLINE)


# ---------------------------------------------------------------------------
# Integration tests: restart_bot guard
# ---------------------------------------------------------------------------

class TestRestartBotOnlineGuard:
    """Guard in restart_bot rejects duplicate online instances before enqueue."""

    def test_reject_restart_when_online_binding_active(self):
        """ONLINE stage, binding status=ACTIVE -> restart_bot returns
        success=False, error_code=duplicate_online_instance."""
        record = _make_publish_record(
            status=PublishStatus.SUCCESS.value,
            ext={"binding": {"online": 42}, "migration_path": "/m"},
        )
        svc, publish_service = _svc_with_record(record)
        publish_service.get_device_binding_by_id.return_value = _binding_mock(
            device_id="BOT-x",
            status=DeviceBindingStatus.ACTIVE,
        )
        svc._bot_service.get_bot = Mock(return_value={"bot_id": "bot-source"})
        svc._task_queue_service = Mock()

        result = svc.restart_bot(publish_id=1, operator="op")

        assert result["success"] is False
        assert result["error_code"] == "duplicate_online_instance"
        # Must NOT have enqueued a restart task
        svc._task_queue_service.enqueue.assert_not_called()

    def test_reject_restart_when_online_binding_failed(self):
        """ONLINE stage, binding status=FAILED -> restart_bot returns
        success=False, error_code=duplicate_online_instance."""
        record = _make_publish_record(
            status=PublishStatus.SUCCESS.value,
            ext={"binding": {"online": 42}, "migration_path": "/m"},
        )
        svc, publish_service = _svc_with_record(record)
        publish_service.get_device_binding_by_id.return_value = _binding_mock(
            device_id="BOT-x",
            status=DeviceBindingStatus.FAILED,
        )
        svc._bot_service.get_bot = Mock(return_value={"bot_id": "bot-source"})
        svc._task_queue_service = Mock()

        result = svc.restart_bot(publish_id=1, operator="op")

        assert result["success"] is False
        assert result["error_code"] == "duplicate_online_instance"
        svc._task_queue_service.enqueue.assert_not_called()

    def test_allow_restart_when_online_binding_released(self):
        """ONLINE stage, binding status=RELEASED -> restart proceeds (enqueue called)."""
        from agentclaw.community.core.service_bot.services.publish_flow.tasks import (
            RESTART_TASK,
        )

        record = _make_publish_record(
            status=PublishStatus.SUCCESS.value,
            ext={"binding": {"online": 42}, "migration_path": "/m"},
        )
        svc, publish_service = _svc_with_record(record)
        publish_service.get_device_binding_by_id.return_value = _binding_mock(
            device_id="BOT-x",
            status=DeviceBindingStatus.RELEASED,
        )
        svc._bot_service.get_bot = Mock(return_value={"bot_id": "bot-source"})
        svc._task_queue_service = Mock()

        result = svc.restart_bot(publish_id=1, operator="op")

        assert result["success"] is True
        assert result["stage"] == PublishStage.ONLINE.value
        svc._task_queue_service.enqueue.assert_called_once()
        assert svc._task_queue_service.enqueue.call_args.args[0] == RESTART_TASK

    def test_allow_restart_when_verify_stage_binding_active(self):
        """VERIFY stage, binding status=ACTIVE -> restart proceeds (guard only
        applies to ONLINE stage)."""
        from agentclaw.community.core.service_bot.services.publish_flow.tasks import (
            RESTART_TASK,
        )

        record = _make_publish_record(
            status=PublishStatus.VALIDATING.value,
            ext={"binding": {"verify": 7}, "migration_path": "/m"},
        )
        svc, publish_service = _svc_with_record(record)
        publish_service.get_device_binding_by_id.return_value = _binding_mock(
            device_id="BOT-v",
            status=DeviceBindingStatus.ACTIVE,
        )
        svc._bot_service.get_bot = Mock(return_value={"bot_id": "bot-source"})
        svc._task_queue_service = Mock()

        result = svc.restart_bot(publish_id=1, operator="op")

        assert result["success"] is True
        assert result["stage"] == PublishStage.VERIFY.value
        svc._task_queue_service.enqueue.assert_called_once()
        assert svc._task_queue_service.enqueue.call_args.args[0] == RESTART_TASK


# ---------------------------------------------------------------------------
# Integration tests: execute_restart guard (recreate path)
# ---------------------------------------------------------------------------

_ACQUIRE_PATH = (
    "agentclaw.community.core.service_bot.services.publish_flow."
    "restart_mixin.acquire_deploy_workflow"
)


class TestExecuteRestartOnlineGuard:
    """Guard in execute_restart prevents _recreate_restart_target when an
    ACTIVE or FAILED online binding still occupies the slot."""

    @pytest.mark.asyncio
    async def test_execute_restart_rejects_recreate_when_binding_failed(self):
        """execute_restart catches TargetBotGoneError but binding is FAILED ->
        returns error, does NOT call _recreate_restart_target."""
        build_service = Mock()
        build_service.upgrade_async = AsyncMock(
            side_effect=TargetBotGoneError("BOT_NOT_FOUND -> recreate"),
        )
        build_service.release_async = AsyncMock()
        publish_service = Mock()
        publish_service.create_device_binding.return_value = 55

        record = _make_publish_record(
            status=PublishStatus.SUCCESS.value,
            ext={"binding": {"online": 1}, "migration_path": "/m"},
        )
        svc = _pf(
            publish_service, build_service, Mock(), Mock(),
            _arca_router(build_service),
        )
        _setup_restart(svc, record, bot_uuid="BOT-gone")
        # Override binding mock with FAILED status for the guard check
        svc._publish_service.get_device_binding_by_id = Mock(
            return_value=_binding_mock(
                device_id="BOT-gone", status=DeviceBindingStatus.FAILED,
            )
        )
        svc._finalize_dangling_recreate_op = Mock(return_value=None)

        result = await svc.execute_restart(
            publish_id=1, stage="online", operator="op",
        )

        assert result["success"] is False
        assert result["error_code"] == "duplicate_online_instance"
        # _recreate_restart_target must NOT have been called
        build_service.release_async.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_execute_restart_rejects_recreate_when_binding_active(self):
        """execute_restart catches TargetBotGoneError but binding is ACTIVE ->
        returns error, does NOT call _recreate_restart_target."""
        build_service = Mock()
        build_service.upgrade_async = AsyncMock(
            side_effect=TargetBotGoneError("BOT_NOT_FOUND -> recreate"),
        )
        build_service.release_async = AsyncMock()
        publish_service = Mock()
        publish_service.create_device_binding.return_value = 55

        record = _make_publish_record(
            status=PublishStatus.SUCCESS.value,
            ext={"binding": {"online": 1}, "migration_path": "/m"},
        )
        svc = _pf(
            publish_service, build_service, Mock(), Mock(),
            _arca_router(build_service),
        )
        _setup_restart(svc, record, bot_uuid="BOT-gone")
        svc._publish_service.get_device_binding_by_id = Mock(
            return_value=_binding_mock(
                device_id="BOT-gone", status=DeviceBindingStatus.ACTIVE,
            )
        )
        svc._finalize_dangling_recreate_op = Mock(return_value=None)

        with patch(_ACQUIRE_PATH, side_effect=TargetBotGoneError("BOT_NOT_FOUND -> recreate")):
            result = await svc.execute_restart(
                publish_id=1, stage="online", operator="op",
            )

        assert result["success"] is False
        assert result["error_code"] == "duplicate_online_instance"
        build_service.release_async.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_execute_restart_allows_recreate_when_binding_released(self):
        """execute_restart catches TargetBotGoneError and binding is RELEASED ->
        proceeds with _recreate_restart_target."""
        build_service = Mock()
        build_service.upgrade_async = AsyncMock(
            side_effect=TargetBotGoneError("BOT_NOT_FOUND -> recreate"),
        )
        build_service.release_async = AsyncMock(
            return_value={"publish_id": 99, "bot_uuid": "BOT-recreated"}
        )
        publish_service = Mock()
        publish_service.create_device_binding.return_value = 55

        record = _make_publish_record(
            status=PublishStatus.SUCCESS.value,
            ext={"binding": {"online": 1}, "migration_path": "/m"},
        )
        svc = _pf(
            publish_service, build_service, Mock(), Mock(),
            _arca_router(build_service),
        )
        _setup_restart(svc, record, bot_uuid="BOT-gone")
        svc._publish_service.get_device_binding_by_id = Mock(
            return_value=_binding_mock(
                device_id="BOT-gone", status=DeviceBindingStatus.RELEASED,
            )
        )
        svc._finalize_dangling_recreate_op = Mock(return_value=None)

        result = await svc.execute_restart(
            publish_id=1, stage="online", operator="op",
        )

        assert result["success"] is True
        build_service.release_async.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_execute_restart_verify_stage_allows_recreate_when_binding_active(self):
        """VERIFY stage: the guard does not apply, so TargetBotGoneError
        proceeds to recreate even when binding is ACTIVE."""
        build_service = Mock()
        build_service.upgrade_async = AsyncMock(
            side_effect=TargetBotGoneError("BOT_NOT_FOUND -> recreate"),
        )
        build_service.release_async = AsyncMock(
            return_value={"publish_id": 98, "bot_uuid": "BOT-v-recreated"}
        )
        publish_service = Mock()
        publish_service.create_device_binding.return_value = 55

        record = _make_publish_record(
            status=PublishStatus.VALIDATING.value,
            ext={"binding": {"verify": 1}, "migration_path": "/m"},
        )
        svc = _pf(
            publish_service, build_service, Mock(), Mock(),
            _arca_router(build_service),
        )
        _setup_restart(svc, record, bot_uuid="BOT-v")
        svc._publish_service.get_device_binding_by_id = Mock(
            return_value=_binding_mock(
                device_id="BOT-v", status=DeviceBindingStatus.ACTIVE,
            )
        )
        svc._finalize_dangling_recreate_op = Mock(return_value=None)

        result = await svc.execute_restart(
            publish_id=1, stage="verify", operator="op",
        )

        assert result["success"] is True
        build_service.release_async.assert_awaited_once()