"""Unit tests for handle_device_callback and related callback-driven state updates.

Uses mocks for repository layer since these are service-level tests.
"""

import json
from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest

from secbaas.api.publish_manage import (
    DeviceCallbackRequest,
    PublishRecordResult,
)


def _make_service(
    dev_repo=None,
    rec_repo=None,
    batch_repo=None,
    publish_repo=None,
    bot_repo=None,
):
    """Construct a DefaultPublishService with injected mock repos."""
    from secbaas.core.service.publish_manage import DefaultPublishService

    return DefaultPublishService(
        bot_repo=bot_repo or MagicMock(),
        device_repo=dev_repo or MagicMock(),
        rel_repo=MagicMock(),
        session_repo=MagicMock(),
        publish_repo=publish_repo or MagicMock(),
        batch_repo=batch_repo or MagicMock(),
        publish_record_repo=rec_repo or MagicMock(),
        template_service=MagicMock(),
        bot_service=MagicMock(),
        device_service=MagicMock(),
    )


# --- Lightweight stubs for repository records ---


@dataclass
class StubDevice:
    id: int
    device_uuid: str
    tenant: str
    status: str = "PENDING"
    err_msg: str | None = None


@dataclass
class StubPublishRecord:
    id: int
    device_id: int
    publish_id: int
    batch_id: int
    result_status: str = "PROCESSING"
    result_message: str | None = None


# --- Fixtures ---


@pytest.fixture
def device():
    return StubDevice(id=1, device_uuid="DEVICE-abc123", tenant="test_tenant")


@pytest.fixture
def publish_record():
    return StubPublishRecord(id=10, device_id=1, publish_id=100, batch_id=5)


@pytest.fixture
def callback():
    return DeviceCallbackRequest(
        device_uuid="DEVICE-abc123",
        publish_id=100,
        event_type="start",
        result_status="SUCCESS",
        exit_code=0,
        stdout="ok",
        stderr="",
        tenant="test_tenant",
    )


def _mock_dev_repo(device):
    mock = MagicMock()
    mock.get_by_device_uuid.return_value = device
    return mock


def _mock_rec_repo(publish_record):
    mock = MagicMock()
    mock.get_by_device_id_and_publish_id.return_value = publish_record
    mock.get_processing_record_by_device_and_publish.return_value = publish_record
    mock.update_result_if_processing.return_value = True
    mock.count_records_by_batch_id.return_value = {"SUCCESS": 1}
    return mock


def _mock_batch_repo():
    mock = MagicMock()
    mock_batch = MagicMock()
    mock_batch.id = 5
    mock_batch.stage = "PREPUB"
    mock_batch.status = "COMPLETED"
    mock_batch.batch_capacity = 1
    mock.get_by_id.return_value = mock_batch
    mock.list_by_publish_id.return_value = [mock_batch]
    return mock


def _mock_publish_repo():
    mock = MagicMock()
    mock_publish = MagicMock()
    mock_publish.id = 100
    mock_publish.bot_id = 1
    mock_publish.publish_type = "CREATE"
    mock_publish.status = "ACTIVE"
    mock_publish.extra_config = {"auto_complete": True}
    mock_publish.creator = "test"
    mock_publish.modifier = "test"
    mock_publish.gmt_create = "2026-01-01T00:00:00"
    mock_publish.gmt_modified = "2026-01-01T00:00:00"
    mock.get_by_id.return_value = mock_publish
    return mock


def _mock_bot_repo():
    mock = MagicMock()
    mock_bot = MagicMock()
    mock_bot.id = 1
    mock_bot.status = "PENDING"
    mock.get_by_id.return_value = mock_bot
    return mock


# --- Test: status normalization ---


class TestHandleDeviceCallbackStatusNorm:
    @pytest.mark.asyncio
    async def test_lowercase_success(self, device, publish_record, callback):
        callback.result_status = "success"
        mock_dev = _mock_dev_repo(device)
        mock_rec = _mock_rec_repo(publish_record)

        service = _make_service(
            dev_repo=mock_dev,
            rec_repo=mock_rec,
            batch_repo=_mock_batch_repo(),
            publish_repo=_mock_publish_repo(),
            bot_repo=_mock_bot_repo(),
        )
        result = await service.handle_device_callback(callback)
        assert result["status"] == "processed"

    @pytest.mark.asyncio
    async def test_invalid_result_status(self, callback):
        callback.result_status = "UNKNOWN"

        service = _make_service()
        result = await service.handle_device_callback(callback)
        assert result["status"] == "rejected"


class TestHandleDeviceCallbackIgnored:
    @pytest.mark.asyncio
    async def test_stop_event_ignored(self, callback):
        callback.event_type = "stop"

        service = _make_service()
        result = await service.handle_device_callback(callback)
        assert result["status"] == "ignored"
        assert "start" in result["reason"]

    @pytest.mark.asyncio
    async def test_no_processing_record_ignored(self, device, callback):
        processed_record = StubPublishRecord(
            id=10, device_id=1, publish_id=100, batch_id=5, result_status="SUCCESS"
        )
        mock_dev = _mock_dev_repo(device)
        mock_rec = _mock_rec_repo(processed_record)
        mock_rec.get_processing_record_by_device_and_publish.return_value = (
            processed_record
        )

        service = _make_service(dev_repo=mock_dev, rec_repo=mock_rec)
        result = await service.handle_device_callback(callback)
        assert result["status"] == "ignored"
        assert "PROCESSING" in result["reason"]

    @pytest.mark.asyncio
    async def test_no_record_at_all_ignored(self, device, callback):
        mock_dev = _mock_dev_repo(device)
        mock_rec = _mock_rec_repo(publish_record)
        mock_rec.get_processing_record_by_device_and_publish.return_value = None

        service = _make_service(dev_repo=mock_dev, rec_repo=mock_rec)
        result = await service.handle_device_callback(callback)
        assert result["status"] == "ignored"


class TestHandleDeviceCallbackDeviceNotFound:
    @pytest.mark.asyncio
    async def test_unknown_device_raises(self, callback):
        mock_dev = MagicMock()
        mock_dev.get_by_device_uuid.return_value = None

        from secbaas.api.publish_manage import PublishNotFoundError

        service = _make_service(dev_repo=mock_dev)
        with pytest.raises(PublishNotFoundError):
            await service.handle_device_callback(callback)


class TestHandleDeviceCallbackDeviceStatus:
    @pytest.mark.asyncio
    async def test_success_sets_device_active(self, device, publish_record, callback):
        mock_dev = _mock_dev_repo(device)
        mock_rec = _mock_rec_repo(publish_record)

        service = _make_service(
            dev_repo=mock_dev,
            rec_repo=mock_rec,
            batch_repo=_mock_batch_repo(),
            publish_repo=_mock_publish_repo(),
            bot_repo=_mock_bot_repo(),
        )
        await service.handle_device_callback(callback)
        mock_dev.update_device.assert_called_once()
        call_kwargs = mock_dev.update_device.call_args[1]
        assert call_kwargs["status"] == "ACTIVE"

    @pytest.mark.asyncio
    async def test_failure_sets_device_failed(self, device, publish_record):
        callback = DeviceCallbackRequest(
            device_uuid="DEVICE-abc123",
            publish_id=100,
            event_type="start",
            result_status="FAILED",
            exit_code=1,
            stderr="hook error",
            tenant="test_tenant",
        )
        mock_dev = _mock_dev_repo(device)
        mock_rec = _mock_rec_repo(publish_record)
        mock_rec.count_records_by_batch_id.return_value = {"FAILED": 1}

        service = _make_service(
            dev_repo=mock_dev,
            rec_repo=mock_rec,
            batch_repo=_mock_batch_repo(),
            publish_repo=_mock_publish_repo(),
            bot_repo=_mock_bot_repo(),
        )
        await service.handle_device_callback(callback)
        mock_dev.update_device.assert_called_once()
        call_kwargs = mock_dev.update_device.call_args[1]
        assert call_kwargs["status"] == "FAILED"
        assert "hook error" in call_kwargs["err_msg"]


class TestHandleDeviceCallbackConcurrency:
    @pytest.mark.asyncio
    async def test_optimistic_lock_failure_ignored(
        self, device, publish_record, callback
    ):
        mock_dev = _mock_dev_repo(device)
        mock_rec = _mock_rec_repo(publish_record)
        mock_rec.update_result_if_processing.return_value = False

        service = _make_service(dev_repo=mock_dev, rec_repo=mock_rec)
        result = await service.handle_device_callback(callback)
        assert result["status"] == "ignored"
        assert "concurrent" in result["reason"]


class TestHandleDeviceCallbackRecordUpdate:
    @pytest.mark.asyncio
    async def test_success_updates_record(self, device, publish_record, callback):
        mock_dev = _mock_dev_repo(device)
        mock_rec = _mock_rec_repo(publish_record)

        service = _make_service(
            dev_repo=mock_dev,
            rec_repo=mock_rec,
            batch_repo=_mock_batch_repo(),
            publish_repo=_mock_publish_repo(),
            bot_repo=_mock_bot_repo(),
        )
        await service.handle_device_callback(callback)
        mock_rec.update_result_if_processing.assert_called_once()
        call_kwargs = mock_rec.update_result_if_processing.call_args[1]
        assert call_kwargs["result_status"] == PublishRecordResult.SUCCESS.value
        msg = call_kwargs["result_message"]
        parsed = json.loads(msg)
        assert parsed["exit_code"] == 0
        assert parsed["stdout"] == "ok"

    @pytest.mark.asyncio
    async def test_failure_updates_record(self, device, publish_record):
        callback = DeviceCallbackRequest(
            device_uuid="DEVICE-abc123",
            publish_id=100,
            event_type="start",
            result_status="FAILED",
            exit_code=1,
            stderr="error occurred",
            tenant="test_tenant",
        )
        mock_dev = _mock_dev_repo(device)
        mock_rec = _mock_rec_repo(publish_record)
        mock_rec.count_records_by_batch_id.return_value = {"FAILED": 1}

        service = _make_service(
            dev_repo=mock_dev,
            rec_repo=mock_rec,
            batch_repo=_mock_batch_repo(),
            publish_repo=_mock_publish_repo(),
            bot_repo=_mock_bot_repo(),
        )
        await service.handle_device_callback(callback)
        call_kwargs = mock_rec.update_result_if_processing.call_args[1]
        assert call_kwargs["result_status"] == PublishRecordResult.FAILED.value
        msg = call_kwargs["result_message"]
        parsed = json.loads(msg)
        assert parsed["exit_code"] == 1
        assert parsed["stderr"] == "error occurred"
