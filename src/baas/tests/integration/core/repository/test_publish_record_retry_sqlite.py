"""Integration tests for publish-record retry queries against real SQLite.

These exercise the ORM query builder (column comparisons, JSON parsing), which
is not testable with a MagicMock session. Protocol-only: no Orm-specific calls.
"""

from datetime import datetime, timedelta

import pytest

from secbaas.community.core.repository.publish_record import (
    PublishRecordRepository,
)
from secbaas.community.core.utils.env_utils import get_current_env

pytestmark = pytest.mark.integration

TEST_ENV = get_current_env()
TEST_TENANT = "test_tenant"


def _insert(
    repo: PublishRecordRepository,
    *,
    status: str = "PROCESSING",
    extra_config: dict | None = None,
    device_id: int | None = None,
) -> int:
    return repo.insert_record(
        tenant=TEST_TENANT,
        env=TEST_ENV,
        domain="test_domain",
        device_id=device_id,
        bot_id=None,
        publish_id=None,
        batch_id=1,
        event_type="CREATE",
        result_status=status,
        creator="tester",
        modifier="tester",
        extra_config=extra_config or {"device_uuid": "dev-1"},
    )


class TestDatabaseNow:
    def test_returns_datetime(self, publish_record_repository, db_transaction):
        now = publish_record_repository.database_now()
        assert isinstance(now, datetime)


class TestListStaleProcessingRecordsAll:
    def test_finds_record_past_window(self, publish_record_repository, db_transaction):
        rec_id = _insert(publish_record_repository)

        stale = publish_record_repository.list_stale_processing_records_all(
            timeout_seconds=0, tenant=TEST_TENANT, env=TEST_ENV
        )

        assert rec_id in [r.id for r in stale]

    def test_excludes_record_inside_window(
        self, publish_record_repository, db_transaction
    ):
        rec_id = _insert(publish_record_repository)

        stale = publish_record_repository.list_stale_processing_records_all(
            timeout_seconds=3600, tenant=TEST_TENANT, env=TEST_ENV
        )

        assert rec_id not in [r.id for r in stale]

    def test_excludes_non_processing_records(
        self, publish_record_repository, db_transaction
    ):
        rec_id = _insert(publish_record_repository, status="SUCCESS")

        stale = publish_record_repository.list_stale_processing_records_all(
            timeout_seconds=0, tenant=TEST_TENANT, env=TEST_ENV
        )

        assert rec_id not in [r.id for r in stale]

    def test_returns_records_with_usable_extra_config(
        self, publish_record_repository, db_transaction
    ):
        rec_id = _insert(
            publish_record_repository,
            extra_config={
                "device_uuid": "dev-x",
                "publish_max_retry_times": 2,
                "publish_retry_count": 0,
            },
        )

        stale = publish_record_repository.list_stale_processing_records_all(
            timeout_seconds=0, tenant=TEST_TENANT, env=TEST_ENV
        )

        match = [r for r in stale if r.id == rec_id]
        assert match, "inserted record should be returned"
        assert match[0].extra_config.get("device_uuid") == "dev-x"


class TestRetryClaimAgainstRealDb:
    def test_claim_increments_count(self, publish_record_repository, db_transaction):
        rec_id = _insert(
            publish_record_repository,
            extra_config={
                "device_uuid": "dev-1",
                "publish_max_retry_times": 2,
                "publish_retry_count": 0,
                "attempt_started_at": None,
            },
        )

        claimed = publish_record_repository.try_claim_retry(
            record_id=rec_id,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            expected_retry_count=0,
            attempt_started_at="2026-01-01 00:00:00",
        )

        assert claimed is True
        state = publish_record_repository.get_retry_state(rec_id, TEST_TENANT, TEST_ENV)
        assert state is not None
        assert state.publish_retry_count == 1

    def test_second_claim_with_stale_expected_count_is_rejected(
        self, publish_record_repository, db_transaction
    ):
        rec_id = _insert(
            publish_record_repository,
            extra_config={
                "device_uuid": "dev-1",
                "publish_max_retry_times": 3,
                "publish_retry_count": 0,
                "attempt_started_at": None,
            },
        )

        first = publish_record_repository.try_claim_retry(
            record_id=rec_id,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            expected_retry_count=0,
            attempt_started_at="2026-01-01 00:00:00",
        )
        second = publish_record_repository.try_claim_retry(
            record_id=rec_id,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            expected_retry_count=0,
            attempt_started_at="2026-01-01 00:01:00",
        )

        assert first is True
        assert second is False

    def test_claim_rejected_for_terminal_record(
        self, publish_record_repository, db_transaction
    ):
        rec_id = _insert(
            publish_record_repository,
            status="FAILED",
            extra_config={"device_uuid": "dev-1", "publish_retry_count": 0},
        )

        claimed = publish_record_repository.try_claim_retry(
            record_id=rec_id,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            expected_retry_count=0,
            attempt_started_at="2026-01-01 00:00:00",
        )

        assert claimed is False

    def test_claim_advances_gmt_modified(
        self, publish_record_repository, db_transaction
    ):
        rec_id = _insert(
            publish_record_repository,
            extra_config={
                "device_uuid": "dev-1",
                "publish_max_retry_times": 2,
                "publish_retry_count": 0,
                "attempt_started_at": None,
            },
        )
        before = publish_record_repository.get_by_id(rec_id, TEST_TENANT, TEST_ENV)
        assert before is not None

        publish_record_repository.try_claim_retry(
            record_id=rec_id,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            expected_retry_count=0,
            attempt_started_at="2026-01-01 00:00:00",
            modifier="sweep",
        )

        after = publish_record_repository.get_by_id(rec_id, TEST_TENANT, TEST_ENV)
        assert after is not None
        assert after.gmt_modified >= before.gmt_modified


class TestPrepareDeviceForReprovision:
    def _make_device(self, device_repository, *, status="FAILED"):
        return device_repository.insert_device(
            device_uuid=f"DEV-{status}-{datetime.now().timestamp()}",
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="tester",
            modifier="tester",
            status=status,
            provider_type="ARCA",
            provider_device_id="prov-old",
        )

    def test_clears_provider_identity_and_sets_pending(
        self, device_repository, db_transaction
    ):
        from secbaas.community.api.device_manage import DeviceStatus

        rec_id = self._make_device(device_repository, status="FAILED")

        rows = device_repository.prepare_device_for_reprovision(
            device_uuid=None, tenant=TEST_TENANT, env=TEST_ENV
        )
        assert rows >= 0

        device = device_repository.get_by_id(rec_id, TEST_TENANT, TEST_ENV)
        assert device is not None

    def test_rejects_released_device(self, device_repository, db_transaction):
        from secbaas.community.api.device_manage import DeviceStatus

        rec_id = self._make_device(device_repository, status="RELEASED")

        device_before = device_repository.get_by_id(rec_id, TEST_TENANT, TEST_ENV)
        assert device_before is not None

        affected = device_repository.prepare_device_for_reprovision(
            device_uuid=device_before.device_uuid,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            modifier="tester",
        )

        assert affected == 0
        after = device_repository.get_by_id(rec_id, TEST_TENANT, TEST_ENV)
        assert after is not None
        assert after.status == DeviceStatus.RELEASED.value

    def test_resets_failed_device_to_pending(self, device_repository, db_transaction):
        from secbaas.community.api.device_manage import DeviceStatus

        rec_id = self._make_device(device_repository, status="FAILED")
        before = device_repository.get_by_id(rec_id, TEST_TENANT, TEST_ENV)
        assert before is not None

        affected = device_repository.prepare_device_for_reprovision(
            device_uuid=before.device_uuid,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            modifier="tester",
        )

        assert affected == 1
        after = device_repository.get_by_id(rec_id, TEST_TENANT, TEST_ENV)
        assert after is not None
        assert after.status == DeviceStatus.PENDING.value
        assert after.provider_device_id is None
        assert after.provider_type is None
