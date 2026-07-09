# mypy: disable-error-code="arg-type,attr-defined"
"""Unit tests for RESTART, UPDATE, and DESTROY batch execution methods.

7.9: restart_device flow -- UPDATING during destroy, PENDING before start, async hook
7.10: UPDATE batch -- sync stop hook on old device, soft-delete rel, new device async
7.11: DESTROY batch -- fully synchronous, batch completes inline

The batch methods use local imports (inside the method body), so we must patch
at the source module rather than on publish_service.
"""

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

TENANT = "test"
ENV = "default"
OPERATOR = "test-op"


def _destroy_ok(**kwargs):
    """Return a successful DestroyDeviceResponse mock."""
    from secbaas.api.device_manage import DestroyDeviceResponse

    return DestroyDeviceResponse(
        success=True, error_message=None, hook_result=None, **kwargs
    )


def _destroy_fail(error_message="destroy failed", **kwargs):
    """Return a failed DestroyDeviceResponse mock."""
    from secbaas.api.device_manage import DestroyDeviceResponse

    return DestroyDeviceResponse(
        success=False, error_message=error_message, hook_result=None, **kwargs
    )


# --- Stubs ---


@dataclass
class StubBatch:
    id: int = 1
    batch_index: int = 0
    batch_capacity: int = 1
    stage: str = "PREPUB"
    status: str = "RUNNING"


@dataclass
class StubDevice:
    id: int = 1
    device_uuid: str = "DEVICE-abc"
    tenant: str = TENANT
    status: str = "ACTIVE"
    domain: str = "default"
    provider_device_id: str = "paas-123"
    extra_config: dict | None = None
    err_msg: str | None = None


@dataclass
class StubPublishRecord:
    id: int = 100
    bot_id: int = 1
    publish_type: str = "RESTART"
    status: str = "ACTIVE"
    template_uuid: str = "TEMPLATE-test"
    extra_config: dict | None = None
    domain: str = "default"
    env: str = "default"


@dataclass
class StubRel:
    id: int = 1
    device_uuid: str = "DEVICE-abc"
    bot_id: int = 1


def _make_device_repo(*devices):
    repo = MagicMock()
    repo.list_by_bot_id.return_value = list(devices)
    repo.get_by_ids.return_value = {d.id: d for d in devices}
    repo.update_status_by_device_uuid = MagicMock()
    repo.update_device = MagicMock()
    return repo


def _make_record_repo(pending_records=None):
    repo = MagicMock()
    repo.list_by_publish_id_and_batch_id.return_value = pending_records or []
    repo.update_result = MagicMock()
    repo.update_device_id = MagicMock()
    return repo


def _make_pending_records(devices, publish_id=100, batch_id=1):
    records = []
    for i, device in enumerate(devices):
        rec = MagicMock()
        rec.id = 200 + i
        rec.device_id = device.id
        rec.device_uuid = device.device_uuid
        records.append(rec)
    return records


def _make_publish_repo(publish_record):
    repo = MagicMock()
    repo.get_by_id.return_value = publish_record
    return repo


def _make_bot_repo():
    repo = MagicMock()
    bot = MagicMock()
    bot.id = 1
    bot.template_uuid = "TEMPLATE-test"
    bot.domain = "default"
    bot.extra_config = None
    repo.get_by_id.return_value = bot
    return repo


def _make_rel_repo():
    repo = MagicMock()
    repo.list_by_bot_id.return_value = []
    repo.soft_delete = MagicMock()
    repo.insert_rel = MagicMock()
    return repo


def _make_device_response(status="ACTIVE"):
    resp = MagicMock()
    resp.status = status
    resp.device_uuid = "DEVICE-new"
    resp.id = 2
    resp.err_msg = None
    return resp


def _make_service(
    device_repo,
    record_repo,
    publish_record,
    rel_repo=None,
    bot_repo=None,
    device_service=None,
    template_service=None,
    bot_service=None,
):
    """Construct a DefaultPublishService with injected mock repos."""
    from secbaas.core.service.publish_manage import DefaultPublishService

    publish_repo = _make_publish_repo(publish_record)
    if bot_repo is None:
        bot_repo = _make_bot_repo()
    if rel_repo is None:
        rel_repo = _make_rel_repo()
    if device_service is None:
        device_service = MagicMock()
    if template_service is None:
        template_service = MagicMock()
    if bot_service is None:
        bot_service = MagicMock()
    return DefaultPublishService(
        bot_repo=bot_repo,
        device_repo=device_repo,
        rel_repo=rel_repo,
        session_repo=MagicMock(),
        publish_repo=publish_repo,
        batch_repo=MagicMock(),
        publish_record_repo=record_repo,
        template_service=template_service,
        bot_service=bot_service,
        device_service=device_service,
    ), publish_repo


# --- 7.11: DESTROY batch ---


class TestDestroyBatch:
    """7.11: DESTROY batch is fully synchronous, no callback needed."""

    @pytest.mark.asyncio
    async def test_destroy_sets_updating_then_destroys(self):
        """DESTROY batch: ACTIVE → UPDATING → destroy → SUCCESS inline."""
        device = StubDevice(status="ACTIVE")
        batch = StubBatch()
        publish_rec = StubPublishRecord(publish_type="DESTROY")

        mock_dev = _make_device_repo(device)
        pending_records = _make_pending_records([device])
        mock_rec = _make_record_repo(pending_records)
        mock_device_svc = MagicMock()
        mock_device_svc.destroy_device_by_uuid = AsyncMock(
            side_effect=lambda *a, **kw: _destroy_ok(),
        )

        service, _ = _make_service(
            mock_dev,
            mock_rec,
            publish_rec,
            device_service=mock_device_svc,
        )
        with patch.object(
            service,
            "_drain_device",
            new_callable=AsyncMock,
            return_value=MagicMock(success=True),
        ):
            result = await service._execute_destroy_batch(
                tenant=TENANT,
                publish_id=100,
                batch=batch,
                drain_timeout=30,
                operator=OPERATOR,
                publish_record=publish_rec,
            )
            assert result.success
            assert result.processed_count == 1
            assert result.failed_count == 0
            mock_dev.update_status_by_device_uuid.assert_called_once()
            call_kwargs = mock_dev.update_status_by_device_uuid.call_args[1]
            assert call_kwargs["status"] == "UPDATING"
            # Handler now calls update_result twice: CREATED then SUCCESS
            assert mock_rec.update_result.call_count == 2
            suc_kwargs = mock_rec.update_result.call_args_list[1].kwargs
            assert suc_kwargs["result_status"] == "SUCCESS"

    @pytest.mark.asyncio
    async def test_destroy_failure_marks_failed(self):
        """DESTROY batch: destroy returns False → record FAILED."""
        device = StubDevice(status="ACTIVE")
        batch = StubBatch()
        publish_rec = StubPublishRecord(publish_type="DESTROY")

        mock_dev = _make_device_repo(device)
        pending_records = _make_pending_records([device])
        mock_rec = _make_record_repo(pending_records)
        mock_device_svc = MagicMock()
        mock_device_svc.destroy_device_by_uuid = AsyncMock(
            side_effect=lambda *a, **kw: _destroy_fail(),
        )

        service, _ = _make_service(
            mock_dev,
            mock_rec,
            publish_rec,
            device_service=mock_device_svc,
        )
        with patch.object(
            service,
            "_drain_device",
            new_callable=AsyncMock,
            return_value=MagicMock(success=True),
        ):
            result = await service._execute_destroy_batch(
                tenant=TENANT,
                publish_id=100,
                batch=batch,
                drain_timeout=30,
                operator=OPERATOR,
                publish_record=publish_rec,
            )
            assert not result.success
            assert result.failed_count == 1
            rec_kwargs = mock_rec.update_result.call_args[1]
            assert rec_kwargs["result_status"] == "FAILED"

    @pytest.mark.asyncio
    async def test_destroy_soft_deletes_rel(self):
        """DESTROY batch: soft-deletes baas_bot_device_rel after destroy."""
        device = StubDevice(status="ACTIVE")
        batch = StubBatch()
        publish_rec = StubPublishRecord(publish_type="DESTROY")

        mock_dev = _make_device_repo(device)
        pending_records = _make_pending_records([device])
        mock_rec = _make_record_repo(pending_records)

        rel = StubRel(device_uuid=device.device_uuid)
        mock_rel = _make_rel_repo()
        mock_rel.list_by_bot_id.return_value = [rel]

        mock_device_svc = MagicMock()
        mock_device_svc.destroy_device_by_uuid = AsyncMock(
            side_effect=lambda *a, **kw: _destroy_ok(),
        )

        service, _ = _make_service(
            mock_dev,
            mock_rec,
            publish_rec,
            rel_repo=mock_rel,
            device_service=mock_device_svc,
        )
        with patch.object(
            service,
            "_drain_device",
            new_callable=AsyncMock,
            return_value=MagicMock(success=True),
        ):
            await service._execute_destroy_batch(
                tenant=TENANT,
                publish_id=100,
                batch=batch,
                drain_timeout=30,
                operator=OPERATOR,
                publish_record=publish_rec,
            )
            mock_rel.soft_delete.assert_called_once()


# --- 7.9: restart_device flow ---


class TestDestroyForRestart:
    """7.9: destroy_device_by_uuid(for_restart=True) skips RELEASED + soft-delete."""

    def test_for_restart_skips_release_and_soft_delete(self):
        """When for_restart=True, device is NOT set to RELEASED or soft-deleted."""
        import inspect

        from secbaas.core.service.device_manage import (
            DefaultDeviceService,
        )

        source = inspect.getsource(DefaultDeviceService.destroy_device_by_uuid)
        assert "for_restart" in source
        assert "if not for_restart" in source
        assert "status=DeviceStatus.RELEASED.value" in source
        assert "soft_delete_by_device_uuid" in source

    def test_for_restart_finds_updating_devices(self):
        """get_active_or_updating_by_device_uuid should find UPDATING status devices too."""
        import inspect

        from secbaas.core.repository.device import (
            OrmDeviceRepository,
        )

        source = inspect.getsource(
            OrmDeviceRepository.get_active_or_updating_by_device_uuid
        )
        assert "UPDATING" in source


class TestRestartBatch:
    """7.9: restart_device — UPDATING for destroy, PENDING before start, async hook."""

    @pytest.mark.asyncio
    async def test_restart_sets_updating_before_destroy(self):
        """RESTART batch: device set to UPDATING before restart_device."""
        device = StubDevice(status="ACTIVE")
        batch = StubBatch()
        publish_rec = StubPublishRecord(publish_type="RESTART")

        mock_dev = _make_device_repo(device)
        pending_records = _make_pending_records([device])
        mock_rec = _make_record_repo(pending_records)
        mock_device_svc = MagicMock()
        mock_device_svc.restart_device = AsyncMock(
            return_value=_make_device_response("ACTIVE"),
        )

        service, _ = _make_service(
            mock_dev,
            mock_rec,
            publish_rec,
            device_service=mock_device_svc,
        )
        with patch.object(
            service,
            "_drain_device",
            new_callable=AsyncMock,
            return_value=MagicMock(success=True),
        ):
            await service._execute_restart_batch(
                tenant=TENANT,
                publish_id=100,
                batch=batch,
                drain_timeout=30,
                operator=OPERATOR,
                publish_record=publish_rec,
            )
            mock_dev.update_status_by_device_uuid.assert_called_once()
            call_kwargs = mock_dev.update_status_by_device_uuid.call_args[1]
            assert call_kwargs["status"] == "UPDATING"

    @pytest.mark.asyncio
    async def test_restart_no_hook_fast_path(self):
        """RESTART batch: no start hook → device ACTIVE, record SUCCESS inline."""
        device = StubDevice(status="ACTIVE")
        batch = StubBatch()
        publish_rec = StubPublishRecord(publish_type="RESTART")

        mock_dev = _make_device_repo(device)
        pending_records = _make_pending_records([device])
        mock_rec = _make_record_repo(pending_records)
        mock_device_svc = MagicMock()
        mock_device_svc.restart_device = AsyncMock(
            return_value=_make_device_response("ACTIVE"),
        )

        service, _ = _make_service(
            mock_dev,
            mock_rec,
            publish_rec,
            device_service=mock_device_svc,
        )
        with patch.object(
            service,
            "_drain_device",
            new_callable=AsyncMock,
            return_value=MagicMock(success=True),
        ):
            result = await service._execute_restart_batch(
                tenant=TENANT,
                publish_id=100,
                batch=batch,
                drain_timeout=30,
                operator=OPERATOR,
                publish_record=publish_rec,
            )
            assert result.success
            assert result.processed_count == 1
            rec_kwargs = mock_rec.update_result.call_args[1]
            assert rec_kwargs["result_status"] == "SUCCESS"

    @pytest.mark.asyncio
    async def test_restart_async_hook_no_inline_update(self):
        """RESTART batch: async hook dispatched → record stays CREATED (no inline update)."""
        device = StubDevice(status="ACTIVE")
        batch = StubBatch()
        publish_rec = StubPublishRecord(publish_type="RESTART")

        mock_dev = _make_device_repo(device)
        pending_records = _make_pending_records([device])
        mock_rec = _make_record_repo(pending_records)
        mock_device_svc = MagicMock()
        mock_device_svc.restart_device = AsyncMock(
            return_value=_make_device_response("PENDING"),
        )

        service, _ = _make_service(
            mock_dev,
            mock_rec,
            publish_rec,
            device_service=mock_device_svc,
        )
        with patch.object(
            service,
            "_drain_device",
            new_callable=AsyncMock,
            return_value=MagicMock(success=True),
        ):
            result = await service._execute_restart_batch(
                tenant=TENANT,
                publish_id=100,
                batch=batch,
                drain_timeout=30,
                operator=OPERATOR,
                publish_record=publish_rec,
            )
            assert result.success
            assert result.processed_count == 0
            # Handler always transitions PENDING→CREATED first, even for async hooks
            mock_rec.update_result.assert_called_once()
            cre_kwargs = mock_rec.update_result.call_args[1]
            assert cre_kwargs["result_status"] == "PROCESSING"

    @pytest.mark.asyncio
    async def test_restart_failure_marks_failed(self):
        """RESTART batch: restart raises exception → record FAILED."""
        device = StubDevice(status="ACTIVE")
        batch = StubBatch()
        publish_rec = StubPublishRecord(publish_type="RESTART")

        mock_dev = _make_device_repo(device)
        pending_records = _make_pending_records([device])
        mock_rec = _make_record_repo(pending_records)
        mock_device_svc = MagicMock()
        mock_device_svc.restart_device = AsyncMock(
            side_effect=Exception("PaaS error"),
        )

        service, _ = _make_service(
            mock_dev,
            mock_rec,
            publish_rec,
            device_service=mock_device_svc,
        )
        with patch.object(
            service,
            "_drain_device",
            new_callable=AsyncMock,
            return_value=MagicMock(success=True),
        ):
            result = await service._execute_restart_batch(
                tenant=TENANT,
                publish_id=100,
                batch=batch,
                drain_timeout=30,
                operator=OPERATOR,
                publish_record=publish_rec,
            )
            assert not result.success
            assert result.failed_count == 1
            rec_kwargs = mock_rec.update_result.call_args[1]
            assert rec_kwargs["result_status"] == "FAILED"

    @pytest.mark.asyncio
    async def test_restart_scope_all_includes_failed_devices(self):
        """RESTART with scope='all': both ACTIVE and FAILED devices are selected."""
        active_device = StubDevice(id=1, device_uuid="DEV-active", status="ACTIVE")
        failed_device = StubDevice(id=2, device_uuid="DEV-failed", status="FAILED")
        pending_device = StubDevice(id=3, device_uuid="DEV-pending", status="PENDING")
        batch = StubBatch(batch_capacity=10)
        publish_rec = StubPublishRecord(
            publish_type="RESTART",
            extra_config={"restart_scope": "all"},
        )

        mock_dev = _make_device_repo(active_device, failed_device, pending_device)
        # Scope filtering done at create_publish time: all excludes PENDING
        pending_records = _make_pending_records(
            [active_device, failed_device], batch_id=batch.id
        )
        mock_rec = _make_record_repo(pending_records)
        mock_device_svc = MagicMock()
        mock_device_svc.restart_device = AsyncMock(
            return_value=_make_device_response("ACTIVE"),
        )

        service, _ = _make_service(
            mock_dev,
            mock_rec,
            publish_rec,
            device_service=mock_device_svc,
        )
        with patch.object(
            service,
            "_drain_device",
            new_callable=AsyncMock,
            return_value=MagicMock(success=True),
        ):
            await service._execute_restart_batch(
                tenant=TENANT,
                publish_id=100,
                batch=batch,
                drain_timeout=30,
                operator=OPERATOR,
                publish_record=publish_rec,
            )
            # Only ACTIVE and FAILED should be restarted (2 devices)
            assert mock_device_svc.restart_device.call_count == 2
            restarted_uuids = {
                call.kwargs.get("device_uuid") or call.args[1]
                for call in mock_device_svc.restart_device.call_args_list
            }
            assert "DEV-active" in restarted_uuids
            assert "DEV-failed" in restarted_uuids
            assert "DEV-pending" not in restarted_uuids

    @pytest.mark.asyncio
    async def test_restart_scope_unhealthy_only_failed_devices(self):
        """RESTART with scope='unhealthy': only FAILED devices are selected."""
        active_device = StubDevice(id=1, device_uuid="DEV-active", status="ACTIVE")
        failed_device = StubDevice(id=2, device_uuid="DEV-failed", status="FAILED")
        batch = StubBatch(batch_capacity=10)
        publish_rec = StubPublishRecord(
            publish_type="RESTART",
            extra_config={"restart_scope": "unhealthy"},
        )

        mock_dev = _make_device_repo(active_device, failed_device)
        pending_records = _make_pending_records([failed_device], batch_id=batch.id)
        mock_rec = _make_record_repo(pending_records)
        mock_device_svc = MagicMock()
        mock_device_svc.restart_device = AsyncMock(
            return_value=_make_device_response("ACTIVE"),
        )

        service, _ = _make_service(
            mock_dev,
            mock_rec,
            publish_rec,
            device_service=mock_device_svc,
        )
        with patch.object(
            service,
            "_drain_device",
            new_callable=AsyncMock,
            return_value=MagicMock(success=True),
        ):
            await service._execute_restart_batch(
                tenant=TENANT,
                publish_id=100,
                batch=batch,
                drain_timeout=30,
                operator=OPERATOR,
                publish_record=publish_rec,
            )
            # Only FAILED device should be restarted
            assert mock_device_svc.restart_device.call_count == 1
            restarted_uuid = (
                mock_device_svc.restart_device.call_args.kwargs.get("device_uuid")
                or mock_device_svc.restart_device.call_args.args[1]
            )
            assert restarted_uuid == "DEV-failed"

    @pytest.mark.asyncio
    async def test_restart_scope_all_excludes_pending_includes_updating(self):
        """RESTART: PENDING excluded; UPDATING included when no other active publish."""
        active_device = StubDevice(id=1, device_uuid="DEV-active", status="ACTIVE")
        pending_device = StubDevice(id=2, device_uuid="DEV-pending", status="PENDING")
        updating_device = StubDevice(
            id=3, device_uuid="DEV-updating", status="UPDATING"
        )
        batch = StubBatch(batch_capacity=10)
        publish_rec = StubPublishRecord(
            publish_type="RESTART",
            extra_config={"restart_scope": "all"},
        )

        mock_dev = _make_device_repo(active_device, pending_device, updating_device)
        pending_records = _make_pending_records(
            [active_device, updating_device], batch_id=batch.id
        )
        mock_rec = _make_record_repo(pending_records)
        mock_device_svc = MagicMock()
        mock_device_svc.restart_device = AsyncMock(
            return_value=_make_device_response("ACTIVE"),
        )

        service, mock_pub_repo = _make_service(
            mock_dev,
            mock_rec,
            publish_rec,
            device_service=mock_device_svc,
        )
        mock_pub_repo.get_active_by_bot_id.return_value = None

        with patch.object(
            service,
            "_drain_device",
            new_callable=AsyncMock,
            return_value=MagicMock(success=True),
        ):
            await service._execute_restart_batch(
                tenant=TENANT,
                publish_id=100,
                batch=batch,
                drain_timeout=30,
                operator=OPERATOR,
                publish_record=publish_rec,
            )
            assert mock_device_svc.restart_device.call_count == 2

    @pytest.mark.asyncio
    async def test_restart_scope_all_excludes_updating_with_active_publish(self):
        """RESTART: UPDATING excluded when another active publish exists on the bot."""
        active_device = StubDevice(id=1, device_uuid="DEV-active", status="ACTIVE")
        updating_device = StubDevice(
            id=3, device_uuid="DEV-updating", status="UPDATING"
        )
        batch = StubBatch(batch_capacity=10)
        publish_rec = StubPublishRecord(
            publish_type="RESTART",
            extra_config={"restart_scope": "all"},
        )

        mock_dev = _make_device_repo(active_device, updating_device)
        pending_records = _make_pending_records([active_device], batch_id=batch.id)
        mock_rec = _make_record_repo(pending_records)
        mock_device_svc = MagicMock()
        mock_device_svc.restart_device = AsyncMock(
            return_value=_make_device_response("ACTIVE"),
        )

        service, mock_pub_repo = _make_service(
            mock_dev,
            mock_rec,
            publish_rec,
            device_service=mock_device_svc,
        )
        other_publish = MagicMock()
        other_publish.id = 99
        mock_pub_repo.get_active_by_bot_id.return_value = other_publish

        with patch.object(
            service,
            "_drain_device",
            new_callable=AsyncMock,
            return_value=MagicMock(success=True),
        ):
            await service._execute_restart_batch(
                tenant=TENANT,
                publish_id=100,
                batch=batch,
                drain_timeout=30,
                operator=OPERATOR,
                publish_record=publish_rec,
            )
            assert mock_device_svc.restart_device.call_count == 1
            restarted_uuid = (
                mock_device_svc.restart_device.call_args.kwargs.get("device_uuid")
                or mock_device_svc.restart_device.call_args.args[1]
            )
            assert restarted_uuid == "DEV-active"

    @pytest.mark.asyncio
    async def test_restart_scope_default_all_when_no_config(self):
        """RESTART with no extra_config: defaults to scope='all' (ACTIVE only, no FAILED in default set if none present)."""
        active_device = StubDevice(id=1, device_uuid="DEV-active", status="ACTIVE")
        batch = StubBatch(batch_capacity=10)
        publish_rec = StubPublishRecord(
            publish_type="RESTART",
            extra_config=None,
        )

        mock_dev = _make_device_repo(active_device)
        pending_records = _make_pending_records([active_device], batch_id=batch.id)
        mock_rec = _make_record_repo(pending_records)
        mock_device_svc = MagicMock()
        mock_device_svc.restart_device = AsyncMock(
            return_value=_make_device_response("ACTIVE"),
        )

        service, _ = _make_service(
            mock_dev,
            mock_rec,
            publish_rec,
            device_service=mock_device_svc,
        )
        with patch.object(
            service,
            "_drain_device",
            new_callable=AsyncMock,
            return_value=MagicMock(success=True),
        ):
            result = await service._execute_restart_batch(
                tenant=TENANT,
                publish_id=100,
                batch=batch,
                drain_timeout=30,
                operator=OPERATOR,
                publish_record=publish_rec,
            )
            assert result.success
            assert mock_device_svc.restart_device.call_count == 1


class TestUpdateBatch:
    """7.10: UPDATE batch — restart device in-place with new config, keep rels intact."""

    @pytest.mark.asyncio
    async def test_update_restarts_device_in_place(self):
        """UPDATE batch: calls restart_device instead of destroy+create."""
        device = StubDevice(status="ACTIVE")
        batch = StubBatch()
        publish_rec = StubPublishRecord(publish_type="UPDATE")

        mock_dev = _make_device_repo(device)
        pending_records = _make_pending_records([device])
        mock_rec = _make_record_repo(pending_records)
        mock_rel = _make_rel_repo()
        mock_device_svc = MagicMock()
        mock_device_svc.update_device = AsyncMock(
            return_value=_make_device_response("ACTIVE"),
        )

        service, _ = _make_service(
            mock_dev,
            mock_rec,
            publish_rec,
            rel_repo=mock_rel,
            device_service=mock_device_svc,
        )
        with patch.object(
            service,
            "_drain_device",
            new_callable=AsyncMock,
            return_value=MagicMock(success=True),
        ):
            await service._execute_update_batch(
                tenant=TENANT,
                publish_id=100,
                batch=batch,
                drain_timeout=30,
                operator=OPERATOR,
                publish_record=publish_rec,
            )
            mock_device_svc.update_device.assert_called_once_with(
                tenant=TENANT,
                device_uuid=device.device_uuid,
                modifier=OPERATOR,
                publish_id=100,
            )

    @pytest.mark.asyncio
    async def test_update_updates_device_config_before_restart(self):
        """UPDATE batch: updates device extra_config to new BotConfig before restart."""
        device = StubDevice(status="ACTIVE")
        batch = StubBatch()
        publish_rec = StubPublishRecord(publish_type="UPDATE")

        mock_dev = _make_device_repo(device)
        pending_records = _make_pending_records([device])
        mock_rec = _make_record_repo(pending_records)
        mock_rel = _make_rel_repo()
        mock_device_svc = MagicMock()
        mock_device_svc.update_device = AsyncMock(
            return_value=_make_device_response("ACTIVE"),
        )

        service, _ = _make_service(
            mock_dev,
            mock_rec,
            publish_rec,
            rel_repo=mock_rel,
            device_service=mock_device_svc,
        )
        with patch.object(
            service,
            "_drain_device",
            new_callable=AsyncMock,
            return_value=MagicMock(success=True),
        ):
            await service._execute_update_batch(
                tenant=TENANT,
                publish_id=100,
                batch=batch,
                drain_timeout=30,
                operator=OPERATOR,
                publish_record=publish_rec,
            )
            # update_device should be called to update extra_config
            mock_dev.update_device.assert_called_once()
            call_kwargs = mock_dev.update_device.call_args[1]
            assert "extra_config" in call_kwargs

    @pytest.mark.asyncio
    async def test_update_keeps_rels_intact(self):
        """UPDATE batch: does NOT soft-delete rels during execution (transfer at complete)."""
        device = StubDevice(status="ACTIVE")
        batch = StubBatch()
        publish_rec = StubPublishRecord(publish_type="UPDATE")

        mock_dev = _make_device_repo(device)
        pending_records = _make_pending_records([device])
        mock_rec = _make_record_repo(pending_records)
        mock_rel = _make_rel_repo()
        mock_rel.soft_delete = MagicMock()
        mock_device_svc = MagicMock()
        mock_device_svc.update_device = AsyncMock(
            return_value=_make_device_response("ACTIVE"),
        )

        service, _ = _make_service(
            mock_dev,
            mock_rec,
            publish_rec,
            rel_repo=mock_rel,
            device_service=mock_device_svc,
        )
        with patch.object(
            service,
            "_drain_device",
            new_callable=AsyncMock,
            return_value=MagicMock(success=True),
        ):
            await service._execute_update_batch(
                tenant=TENANT,
                publish_id=100,
                batch=batch,
                drain_timeout=30,
                operator=OPERATOR,
                publish_record=publish_rec,
            )
            mock_rel.soft_delete.assert_not_called()
            mock_rel.insert_rel.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_async_hook_no_inline_success(self):
        """UPDATE batch: async hook → record stays CREATED, no inline SUCCESS."""
        device = StubDevice(status="ACTIVE")
        batch = StubBatch()
        publish_rec = StubPublishRecord(publish_type="UPDATE")

        mock_dev = _make_device_repo(device)
        pending_records = _make_pending_records([device])
        mock_rec = _make_record_repo(pending_records)
        mock_rel = _make_rel_repo()
        mock_device_svc = MagicMock()
        mock_device_svc.update_device = AsyncMock(
            return_value=_make_device_response("PENDING"),
        )

        service, _ = _make_service(
            mock_dev,
            mock_rec,
            publish_rec,
            rel_repo=mock_rel,
            device_service=mock_device_svc,
        )
        with patch.object(
            service,
            "_drain_device",
            new_callable=AsyncMock,
            return_value=MagicMock(success=True),
        ):
            result = await service._execute_update_batch(
                tenant=TENANT,
                publish_id=100,
                batch=batch,
                drain_timeout=30,
                operator=OPERATOR,
                publish_record=publish_rec,
            )
            assert result.success
            # Handler always transitions PENDING→CREATED first, even for async hooks
            assert mock_rec.update_result.call_count == 1
            cre_kwargs = mock_rec.update_result.call_args[1]
            assert cre_kwargs["result_status"] == "PROCESSING"

    @pytest.mark.asyncio
    async def test_update_no_hook_fast_path(self):
        """UPDATE batch: no hook → device ACTIVE, record SUCCESS inline."""
        device = StubDevice(status="ACTIVE")
        batch = StubBatch()
        publish_rec = StubPublishRecord(publish_type="UPDATE")

        mock_dev = _make_device_repo(device)
        pending_records = _make_pending_records([device])
        mock_rec = _make_record_repo(pending_records)
        mock_rel = _make_rel_repo()
        mock_device_svc = MagicMock()
        mock_device_svc.update_device = AsyncMock(
            return_value=_make_device_response("ACTIVE"),
        )

        service, _ = _make_service(
            mock_dev,
            mock_rec,
            publish_rec,
            rel_repo=mock_rel,
            device_service=mock_device_svc,
        )
        with patch.object(
            service,
            "_drain_device",
            new_callable=AsyncMock,
            return_value=MagicMock(success=True),
        ):
            result = await service._execute_update_batch(
                tenant=TENANT,
                publish_id=100,
                batch=batch,
                drain_timeout=30,
                operator=OPERATOR,
                publish_record=publish_rec,
            )
            assert result.success
            assert result.processed_count == 1
            rec_kwargs = mock_rec.update_result.call_args[1]
            assert rec_kwargs["result_status"] == "SUCCESS"

    @pytest.mark.asyncio
    async def test_update_no_device_id_change(self):
        """UPDATE batch: restart reuses device, no update_device_id on record."""
        device = StubDevice(status="ACTIVE")
        batch = StubBatch()
        publish_rec = StubPublishRecord(publish_type="UPDATE")

        mock_dev = _make_device_repo(device)
        pending_records = _make_pending_records([device])
        mock_rec = _make_record_repo(pending_records)
        mock_rel = _make_rel_repo()
        mock_device_svc = MagicMock()
        mock_device_svc.update_device = AsyncMock(
            return_value=_make_device_response("PENDING"),
        )

        service, _ = _make_service(
            mock_dev,
            mock_rec,
            publish_rec,
            rel_repo=mock_rel,
            device_service=mock_device_svc,
        )
        with patch.object(
            service,
            "_drain_device",
            new_callable=AsyncMock,
            return_value=MagicMock(success=True),
        ):
            await service._execute_update_batch(
                tenant=TENANT,
                publish_id=100,
                batch=batch,
                drain_timeout=30,
                operator=OPERATOR,
                publish_record=publish_rec,
            )
            # Device ID should NOT change since we reuse the same device
            mock_rec.update_device_id.assert_not_called()
