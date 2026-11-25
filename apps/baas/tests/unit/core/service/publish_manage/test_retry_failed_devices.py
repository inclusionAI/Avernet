"""Service tests for bulk failed-device retry.

Bulk retry must reuse the single-device path (so the two cannot diverge) and
must never let one device's refusal abort the rest.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from secbaas.community.core.service.publish_manage import DefaultPublishService
from secbaas.community.core.service.publish_manage._publish_retry_orchestrator import (
    STAGE_CLEANUP_MESSAGE,
)


def _svc() -> DefaultPublishService:
    return DefaultPublishService(
        bot_repo=MagicMock(),
        device_repo=MagicMock(),
        rel_repo=MagicMock(),
        session_repo=MagicMock(),
        publish_repo=MagicMock(),
        batch_repo=MagicMock(),
        publish_record_repo=MagicMock(),
        template_service=MagicMock(),
        bot_service=MagicMock(),
        device_service=MagicMock(),
    )


def _publish(status: str = "FAILED") -> MagicMock:
    pub = MagicMock()
    pub.id = 9
    pub.status = status
    pub.extra_config = {}
    return pub


def _record(
    *,
    id: int,
    status: str = "FAILED",
    device_uuid: str | None = None,
    result_message: str = "prov boom",
) -> MagicMock:
    rec = MagicMock()
    rec.id = id
    rec.device_uuid = device_uuid if device_uuid is not None else f"dev-{id}"
    rec.result_status = status
    rec.result_message = result_message
    rec.extra_config = {}
    return rec


@pytest.fixture
def env():
    with patch(
        "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
        return_value="test",
    ):
        yield


class TestRetryFailedDevices:
    async def test_retries_every_real_failure(self, env):
        svc = _svc()
        svc._publish_repo.get_by_id.return_value = _publish()
        svc._publish_record_repo.list_by_publish_id.return_value = [
            _record(id=1),
            _record(id=2),
            _record(id=3, status="SUCCESS"),
        ]
        svc.retry_device = AsyncMock(
            side_effect=lambda **kw: {
                "record_id": int(kw["device_uuid"].split("-")[1]),
                "device_uuid": kw["device_uuid"],
                "publish_manual_retry_count": 1,
            }
        )

        result = await svc.retry_failed_devices(tenant="t", publish_id=9, operator="op")

        assert result["requested"] == 2
        assert result["accepted_count"] == 2
        assert result["skipped_count"] == 0
        assert svc.retry_device.await_count == 2

    async def test_cleanup_records_are_not_retried(self, env):
        """Never-executed records must not be re-provisioned."""
        svc = _svc()
        svc._publish_repo.get_by_id.return_value = _publish()
        svc._publish_record_repo.list_by_publish_id.return_value = [
            _record(id=1),
            _record(id=2, result_message=STAGE_CLEANUP_MESSAGE),
        ]
        svc.retry_device = AsyncMock(
            return_value={
                "record_id": 1,
                "device_uuid": "dev-1",
                "publish_manual_retry_count": 1,
            }
        )

        result = await svc.retry_failed_devices(tenant="t", publish_id=9, operator="op")

        assert result["requested"] == 1
        assert svc.retry_device.await_count == 1

    async def test_one_refusal_does_not_abort_the_rest(self, env):
        svc = _svc()
        svc._publish_repo.get_by_id.return_value = _publish()
        svc._publish_record_repo.list_by_publish_id.return_value = [
            _record(id=1),
            _record(id=2),
            _record(id=3),
        ]

        async def _side_effect(**kw):
            if kw["device_uuid"] == "dev-2":
                raise ValueError("MANUAL_RETRY_EXHAUSTED: limit=3")
            return {
                "record_id": int(kw["device_uuid"].split("-")[1]),
                "device_uuid": kw["device_uuid"],
                "publish_manual_retry_count": 1,
            }

        svc.retry_device = AsyncMock(side_effect=_side_effect)

        result = await svc.retry_failed_devices(tenant="t", publish_id=9, operator="op")

        assert result["accepted_count"] == 2
        assert result["skipped_count"] == 1
        assert result["skipped"][0]["device_uuid"] == "dev-2"
        assert result["skipped"][0]["error_code"] == "MANUAL_RETRY_EXHAUSTED"

    async def test_terminal_publish_stops_further_attempts(self, env):
        svc = _svc()
        svc._publish_repo.get_by_id.return_value = _publish()
        svc._publish_record_repo.list_by_publish_id.return_value = [
            _record(id=1),
            _record(id=2),
        ]
        svc.retry_device = AsyncMock(
            side_effect=ValueError("PUBLISH_TERMINAL: is SUCCESS")
        )

        result = await svc.retry_failed_devices(tenant="t", publish_id=9, operator="op")

        assert result["accepted_count"] == 0
        assert result["skipped_count"] == 1
        # Stopped after the first terminal refusal.
        assert svc.retry_device.await_count == 1

    async def test_record_without_device_uuid_is_skipped(self, env):
        svc = _svc()
        svc._publish_repo.get_by_id.return_value = _publish()
        rec = _record(id=1)
        rec.device_uuid = None
        svc._publish_record_repo.list_by_publish_id.return_value = [rec]
        svc.retry_device = AsyncMock()

        result = await svc.retry_failed_devices(tenant="t", publish_id=9, operator="op")

        assert result["accepted_count"] == 0
        assert result["skipped"][0]["error_code"] == "DEVICE_NOT_IN_PUBLISH"
        svc.retry_device.assert_not_awaited()

    async def test_missing_publish(self, env):
        svc = _svc()
        svc._publish_repo.get_by_id.return_value = None
        with pytest.raises(ValueError, match="PUBLISH_NOT_FOUND"):
            await svc.retry_failed_devices(tenant="t", publish_id=9, operator="op")

    @pytest.mark.parametrize("status", ["SUCCESS", "REVOKED", "REJECTED"])
    async def test_terminal_publish_rejected_upfront(self, env, status):
        svc = _svc()
        svc._publish_repo.get_by_id.return_value = _publish(status=status)
        with pytest.raises(ValueError, match="PUBLISH_TERMINAL"):
            await svc.retry_failed_devices(tenant="t", publish_id=9, operator="op")

    async def test_no_failures_is_an_empty_result(self, env):
        svc = _svc()
        svc._publish_repo.get_by_id.return_value = _publish()
        svc._publish_record_repo.list_by_publish_id.return_value = [
            _record(id=1, status="SUCCESS")
        ]
        svc.retry_device = AsyncMock()

        result = await svc.retry_failed_devices(tenant="t", publish_id=9, operator="op")

        assert result["requested"] == 0
        assert result["accepted_count"] == 0
        svc.retry_device.assert_not_awaited()
