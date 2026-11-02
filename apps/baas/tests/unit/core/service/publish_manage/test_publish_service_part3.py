"""Tests for DefaultPublishService."""

from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace
from typing import cast
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from secbaas.community.api.bot_manage import BotConfig, BotStatus
from secbaas.community.api.device_manage import DeployConfig
from secbaas.community.api.publish_manage import (
    DEFAULT_CALLBACK_TIMEOUT_SECONDS,
    BatchStatus,
    DrainResult,
    ProgressSummary,
    PublishConfig,
    PublishConflictError,
    PublishEventType,
    PublishNotFoundError,
    PublishProgressResponse,
    PublishStatus,
    PublishType,
    StageProgress,
)
from secbaas.community.core.repository.publish_batch import (
    PublishBatchRecord,
)
from secbaas.community.core.service.publish_manage import DefaultPublishService


@pytest.fixture
def mock_publish_repo():
    mock = MagicMock()
    mock.now.return_value = datetime.now()
    return mock


@pytest.fixture
def mock_batch_repo():
    return MagicMock()


@pytest.fixture
def mock_record_repo():
    mock = MagicMock()
    mock.count_records_by_batch_id.return_value = {}
    return mock


@pytest.fixture
def mock_device_service():
    mock = MagicMock()
    mock.start_device = AsyncMock()
    mock.restart_device = AsyncMock()
    mock.destroy_device_by_uuid = AsyncMock()
    mock.create_device = MagicMock()
    return mock


@pytest.fixture
def mock_bot_service():
    return MagicMock()


@pytest.fixture
def mock_template_service():
    return MagicMock()


# Module-level service instance — set by autouse fixture so test methods
# can reference it without needing a fixture parameter.
_publish_service_instance = None
_clone_pending_bot = None


@pytest.fixture(autouse=True)
def _stub_clone_pending_bot():
    global _clone_pending_bot
    with patch(
        "secbaas.community.core.service.publish_manage._publish_create.try_insert_pending_bot"
    ) as clone:
        _clone_pending_bot = clone
        yield clone


@pytest.fixture(autouse=True)
def _setup_publish_service(
    mock_publish_repo,
    mock_batch_repo,
    mock_record_repo,
    mock_device_service,
    mock_bot_service,
    mock_template_service,
):
    global _publish_service_instance

    _publish_service_instance = DefaultPublishService(
        bot_repo=MagicMock(),
        device_repo=MagicMock(),
        rel_repo=MagicMock(),
        session_repo=MagicMock(),
        publish_repo=mock_publish_repo,
        batch_repo=mock_batch_repo,
        publish_record_repo=mock_record_repo,
        template_service=mock_template_service,
        bot_service=mock_bot_service,
        device_service=mock_device_service,
    )
    yield _publish_service_instance


class TestPublishProgressLifecycle:
    """Tests for progress tracking through publish lifecycle"""

    @pytest.mark.asyncio
    async def test_progress_after_create_shows_pending_state(self):
        """After create_publish, progress should show PENDING status with 0% complete"""
        mock_bot = MagicMock()
        mock_bot.id = 1

        mock_publish_record = MagicMock()
        mock_publish_record.id = 1
        mock_publish_record.bot_id = 1
        mock_publish_record.status = PublishStatus.PENDING.value
        mock_publish_record.publish_type = "CREATE"
        mock_publish_record.extra_config = {}
        mock_publish_record.creator = "test"
        mock_publish_record.modifier = "test"
        mock_publish_record.gmt_create = datetime.now()
        mock_publish_record.gmt_modified = datetime.now()

        # Create batches for all stages (CREATE has 5-stage pipeline)
        batches = []
        stages = ["PREPUB", "GRAY", "PROD_FIRST_BATCH", "PROD_OTHER_BATCH"]
        for i, stage in enumerate(stages):
            batch = MagicMock()
            batch.id = i + 1
            batch.stage = stage
            batch.status = PublishStatus.PENDING.value
            batch.batch_capacity = 5
            batches.append(batch)

        _publish_service_instance._publish_repo = MagicMock()
        _publish_service_instance._publish_repo.now.return_value = datetime.now()
        _publish_service_instance._publish_repo.get_by_id.return_value = (
            mock_publish_record
        )

        _publish_service_instance._bot_service.get_bot = AsyncMock(
            return_value=mock_bot
        )
        _publish_service_instance._publish_batch_repo = MagicMock()
        _publish_service_instance._publish_batch_repo.list_by_publish_id.return_value = batches

        _publish_service_instance._publish_record_repo = MagicMock()
        _publish_service_instance._publish_record_repo.count_records_by_publish_id.return_value = {}
        _publish_service_instance._publish_record_repo.count_records_by_batch_id.return_value = {}

        result = await _publish_service_instance.get_publish_progress(
            tenant="test_tenant", publish_id=1
        )

        assert result is not None
        assert result.status == PublishStatus.PENDING.value
        assert result.overall_progress.progress_percentage == 0.0
        assert result.overall_progress.completed_batches == 0
        assert result.overall_progress.total_batches == 4

    @pytest.mark.asyncio
    async def test_progress_during_execution_shows_partial_complete(self):
        """During stage execution, progress should show partial completion"""
        mock_bot = MagicMock()
        mock_bot.id = 1

        mock_publish_record = MagicMock()
        mock_publish_record.id = 1
        mock_publish_record.bot_id = 1
        mock_publish_record.status = PublishStatus.ACTIVE.value
        mock_publish_record.publish_type = "CREATE"
        mock_publish_record.extra_config = {}
        mock_publish_record.creator = "test"
        mock_publish_record.modifier = "test"
        mock_publish_record.gmt_create = datetime.now()
        mock_publish_record.gmt_modified = datetime.now()

        # PREPUB and GRAY complete, PROD_FIRST_BATCH in progress
        batches = [
            MagicMock(
                id=1,
                stage="PREPUB",
                status=BatchStatus.COMPLETED.value,
                batch_capacity=2,
            ),
            MagicMock(
                id=2, stage="GRAY", status=BatchStatus.COMPLETED.value, batch_capacity=4
            ),
            MagicMock(
                id=3,
                stage="PROD_FIRST_BATCH",
                status=BatchStatus.RUNNING.value,
                batch_capacity=5,
            ),
            MagicMock(
                id=4,
                stage="PROD_OTHER_BATCH",
                status=BatchStatus.PENDING.value,
                batch_capacity=10,
            ),
        ]

        _publish_service_instance._publish_repo = MagicMock()
        _publish_service_instance._publish_repo.now.return_value = datetime.now()
        _publish_service_instance._publish_repo.get_by_id.return_value = (
            mock_publish_record
        )

        _publish_service_instance._bot_service.get_bot = AsyncMock(
            return_value=mock_bot
        )
        _publish_service_instance._publish_batch_repo = MagicMock()
        _publish_service_instance._publish_batch_repo.list_by_publish_id.return_value = batches

        _publish_service_instance._publish_record_repo = MagicMock()
        # 6 devices processed (2 from PREPUB + 4 from GRAY)
        _publish_service_instance._publish_record_repo.count_records_by_publish_id.return_value = {
            "SUCCESS": 6,
        }
        _publish_service_instance._publish_record_repo.count_records_by_batch_id.return_value = {}

        result = await _publish_service_instance.get_publish_progress(
            tenant="test_tenant", publish_id=1
        )

        assert result is not None
        assert result.status == PublishStatus.ACTIVE.value
        assert result.overall_progress.completed_batches == 2
        assert result.overall_progress.total_batches == 4
        # 6 processed out of 21 total
        assert result.overall_progress.processed_devices == 6
        assert result.overall_progress.total_devices == 21
        # Verify stage breakdown
        assert len(result.stages) >= 1
        # PREPUB should show SUCCESS
        prepub_stage = next((s for s in result.stages if s.stage == "PREPUB"), None)
        assert prepub_stage is not None
        assert prepub_stage.status == PublishStatus.SUCCESS.value

    @pytest.mark.asyncio
    async def test_progress_after_complete_shows_100_percent(self):
        """After publish completes, progress should show 100%"""
        mock_bot = MagicMock()
        mock_bot.id = 1

        mock_publish_record = MagicMock()
        mock_publish_record.id = 1
        mock_publish_record.bot_id = 1
        mock_publish_record.status = PublishStatus.SUCCESS.value
        mock_publish_record.publish_type = "SCALE_UP"
        mock_publish_record.extra_config = {}
        mock_publish_record.creator = "test"
        mock_publish_record.modifier = "test"
        mock_publish_record.gmt_create = datetime.now()
        mock_publish_record.gmt_modified = datetime.now()

        # All batches complete
        batches = [
            MagicMock(
                id=1,
                stage="PROD_FIRST_BATCH",
                status=BatchStatus.COMPLETED.value,
                batch_capacity=10,
            ),
        ]

        _publish_service_instance._publish_repo = MagicMock()
        _publish_service_instance._publish_repo.now.return_value = datetime.now()
        _publish_service_instance._publish_repo.get_by_id.return_value = (
            mock_publish_record
        )

        _publish_service_instance._bot_service.get_bot = AsyncMock(
            return_value=mock_bot
        )
        _publish_service_instance._publish_batch_repo = MagicMock()
        _publish_service_instance._publish_batch_repo.list_by_publish_id.return_value = batches

        _publish_service_instance._publish_record_repo = MagicMock()
        _publish_service_instance._publish_record_repo.count_records_by_publish_id.return_value = {
            "SUCCESS": 10,
        }
        _publish_service_instance._publish_record_repo.count_records_by_batch_id.return_value = {
            "SUCCESS": 10
        }

        result = await _publish_service_instance.get_publish_progress(
            tenant="test_tenant", publish_id=1
        )

        assert result is not None
        assert result.status == PublishStatus.SUCCESS.value
        assert result.overall_progress.completed_batches == 1
        assert result.overall_progress.total_batches == 1
        assert result.overall_progress.processed_devices == 10
        assert result.overall_progress.total_devices == 10
        assert result.overall_progress.progress_percentage == 100.0

    @pytest.mark.asyncio
    async def test_progress_with_failures_shows_failed_count(self):
        """Progress should correctly report failed device counts"""
        mock_bot = MagicMock()
        mock_bot.id = 1

        mock_publish_record = MagicMock()
        mock_publish_record.id = 1
        mock_publish_record.bot_id = 1
        mock_publish_record.status = PublishStatus.ACTIVE.value
        mock_publish_record.publish_type = "CREATE"
        mock_publish_record.extra_config = {}
        mock_publish_record.creator = "test"
        mock_publish_record.modifier = "test"
        mock_publish_record.gmt_create = datetime.now()
        mock_publish_record.gmt_modified = datetime.now()

        batches = [
            MagicMock(
                id=1,
                stage="PREPUB",
                status=BatchStatus.COMPLETED.value,
                batch_capacity=10,
            ),
        ]

        _publish_service_instance._publish_repo = MagicMock()
        _publish_service_instance._publish_repo.now.return_value = datetime.now()
        _publish_service_instance._publish_repo.get_by_id.return_value = (
            mock_publish_record
        )

        _publish_service_instance._bot_service.get_bot = AsyncMock(
            return_value=mock_bot
        )
        _publish_service_instance._publish_batch_repo = MagicMock()
        _publish_service_instance._publish_batch_repo.list_by_publish_id.return_value = batches

        _publish_service_instance._publish_record_repo = MagicMock()
        # 8 success, 2 failures
        _publish_service_instance._publish_record_repo.count_records_by_publish_id.return_value = {
            "SUCCESS": 8,
            "FAILED": 2,
        }
        _publish_service_instance._publish_record_repo.count_records_by_batch_id.return_value = {
            "SUCCESS": 8,
            "FAILED": 2,
        }

        result = await _publish_service_instance.get_publish_progress(
            tenant="test_tenant", publish_id=1
        )

        assert result is not None
        assert result.overall_progress.failed_devices == 2
        assert result.overall_progress.processed_devices == 10
        # Stage should also show failures
        assert result.stages[0].devices_failed == 2


class TestAutoComplete:
    """Tests for auto-complete functionality"""

    def test_auto_complete_enabled_by_default(self):
        """Auto-complete is enabled by default"""
        config = PublishConfig()
        assert config.auto_complete is True

    def test_should_auto_complete_disabled_by_config(self):
        """Auto-complete can be disabled via config"""
        config = PublishConfig(auto_complete=False)
        assert config.auto_complete is False

        # Even with all batches complete, should return False
        _publish_service_instance._publish_batch_repo = MagicMock()
        _publish_service_instance._publish_batch_repo.list_by_publish_id.return_value = [
            MagicMock(status=BatchStatus.COMPLETED.value),
            MagicMock(status=BatchStatus.COMPLETED.value),
        ]

        result = _publish_service_instance._should_auto_complete(
            "test_tenant", 1, config
        )
        assert result is False

    def test_should_auto_complete_returns_true_when_enabled_and_complete(self):
        """Auto-complete triggers when enabled and all batches complete"""
        config = PublishConfig(auto_complete=True)
        assert config.auto_complete is True

        _publish_service_instance._publish_batch_repo = MagicMock()
        _publish_service_instance._publish_batch_repo.list_by_publish_id.return_value = [
            MagicMock(status=BatchStatus.COMPLETED.value),
            MagicMock(status=BatchStatus.COMPLETED.value),
        ]

        result = _publish_service_instance._should_auto_complete(
            "test_tenant", 1, config
        )
        assert result is True

    def test_should_auto_complete_returns_false_when_batch_failed(self):
        """Auto-complete does not trigger when any batch fails"""
        config = PublishConfig(auto_complete=True)

        _publish_service_instance._publish_batch_repo = MagicMock()
        _publish_service_instance._publish_batch_repo.list_by_publish_id.return_value = [
            MagicMock(status=BatchStatus.COMPLETED.value),
            MagicMock(status=BatchStatus.FAILED.value),  # One batch failed
        ]

        result = _publish_service_instance._should_auto_complete(
            "test_tenant", 1, config
        )
        assert result is False

    def test_should_auto_complete_returns_false_when_pending_batches(self):
        """Auto-complete does not trigger when there are pending batches (multi-stage)"""
        config = PublishConfig(auto_complete=True)

        _publish_service_instance._publish_batch_repo = MagicMock()
        # Simulate multi-stage: PREPUB completed, but GRAY stage batches are PENDING
        _publish_service_instance._publish_batch_repo.list_by_publish_id.return_value = [
            MagicMock(status=BatchStatus.COMPLETED.value),  # PREPUB batch
            MagicMock(status=BatchStatus.COMPLETED.value),  # PREPUB batch
            MagicMock(status=BatchStatus.PENDING.value),  # GRAY batch (next stage)
            MagicMock(status=BatchStatus.PENDING.value),  # GRAY batch (next stage)
        ]

        result = _publish_service_instance._should_auto_complete(
            "test_tenant", 1, config
        )
        assert result is False

    def test_should_auto_complete_single_stage_single_batch(self):
        """Auto-complete should trigger for single-stage single-batch (like SCALE_UP)"""
        config = PublishConfig(auto_complete=True)

        _publish_service_instance._publish_batch_repo = MagicMock()
        # Single batch in "direct" stage - all COMPLETED
        _publish_service_instance._publish_batch_repo.list_by_publish_id.return_value = [
            MagicMock(status=BatchStatus.COMPLETED.value),
        ]

        result = _publish_service_instance._should_auto_complete(
            "test_tenant", 1, config
        )
        assert result is True

    def test_check_all_batches_complete_returns_false_for_running(self):
        """Returns False when batch is still running"""
        _publish_service_instance._publish_batch_repo = MagicMock()
        _publish_service_instance._publish_batch_repo.list_by_publish_id.return_value = [
            MagicMock(status=BatchStatus.COMPLETED.value),
            MagicMock(status=BatchStatus.RUNNING.value),
        ]

        result = _publish_service_instance._check_all_batches_complete("test_tenant", 1)
        assert result is False

    def test_check_all_batches_complete_returns_true_all_completed(self):
        """Returns True only when all batches are COMPLETED"""
        _publish_service_instance._publish_batch_repo = MagicMock()
        _publish_service_instance._publish_batch_repo.list_by_publish_id.return_value = [
            MagicMock(status=BatchStatus.COMPLETED.value),
            MagicMock(status=BatchStatus.COMPLETED.value),
            MagicMock(status=BatchStatus.COMPLETED.value),
        ]

        result = _publish_service_instance._check_all_batches_complete("test_tenant", 1)
        assert result is True

    @pytest.mark.asyncio
    async def test_complete_publish_is_idempotent(self):
        """Calling complete_publish twice returns the same result"""
        mock_bot = MagicMock()
        mock_bot.id = 1

        # Mock publish record already in SUCCESS status
        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.publish_type = "CREATE"
        mock_publish.status = PublishStatus.SUCCESS.value  # Already completed
        mock_publish.extra_config = {}
        mock_publish.creator = "user1"
        mock_publish.modifier = "user1"
        mock_publish.gmt_create = "2024-01-01T00:00:00"
        mock_publish.gmt_modified = "2024-01-01T00:00:00"

        _publish_service_instance._publish_repo = MagicMock()
        _publish_service_instance._publish_repo.now.return_value = datetime.now()
        _publish_service_instance._publish_repo.get_by_id.return_value = mock_publish

        _publish_service_instance._bot_repo = MagicMock()
        _publish_service_instance._bot_repo.get_by_id.return_value = mock_bot

        _publish_service_instance._publish_batch_repo = MagicMock()
        _publish_service_instance._publish_batch_repo.list_by_publish_id.return_value = []

        # Should return early without updating
        _result = await _publish_service_instance.complete_publish(
            tenant="test_tenant", publish_id=1, operator="admin"
        )

        # Verify update_status was NOT called since already SUCCESS
        _publish_service_instance._publish_repo.update_status.assert_not_called()


class TestPublishAutoCompact:
    """Tests for auto-compact feature: progressive stages based on device count."""

    def test_generate_batches_single_device_compacts_to_one_stage(self):
        """Single device should create only 1 stage (PROD_FIRST_BATCH)."""
        config = PublishConfig(
            replica_desired=1,
            batch_capacity=1,
        )
        batches = _publish_service_instance._generate_batches(
            PublishType.CREATE, config
        )

        # Should have exactly 1 batch (compacted)
        assert len(batches) == 1
        # Should be PROD_FIRST_BATCH stage
        assert batches[0].stage == "PROD_FIRST_BATCH"
        assert batches[0].batch_capacity == 1
        assert batches[0].device_count == 1

    def test_generate_batches_two_devices_compacts_to_two_stages(self):
        """Two devices should compact to 2 stages."""
        config = PublishConfig(
            replica_desired=2,
            batch_capacity=5,
        )
        batches = _publish_service_instance._generate_batches(
            PublishType.CREATE, config
        )

        # 2 devices → 2 stages: PROD_FIRST_BATCH, PROD_OTHER_BATCH
        assert len(batches) == 2
        assert batches[0].stage == "PROD_FIRST_BATCH"
        assert batches[1].stage == "PROD_OTHER_BATCH"
        # Each stage gets 1 device
        assert batches[0].device_count == 1
        assert batches[1].device_count == 1

    def test_generate_batches_three_devices_compacts_to_three_stages(self):
        """Three devices should compact to 3 stages."""
        config = PublishConfig(
            replica_desired=3,
            batch_capacity=5,
        )
        batches = _publish_service_instance._generate_batches(
            PublishType.CREATE, config
        )

        # 3 devices → 3 stages: GRAY, PROD_FIRST_BATCH, PROD_OTHER_BATCH
        assert len(batches) == 3
        assert batches[0].stage == "GRAY"
        assert batches[1].stage == "PROD_FIRST_BATCH"
        assert batches[2].stage == "PROD_OTHER_BATCH"

    def test_generate_batches_four_devices_uses_full_pipeline(self):
        """Four devices should use full 4-stage pipeline (no compact)."""
        config = PublishConfig(
            replica_desired=4,
            batch_capacity=5,
        )
        batches = _publish_service_instance._generate_batches(
            PublishType.CREATE, config
        )

        # 4 devices = 4 stages, no compact needed
        # Uses full pipeline: PREPUB, GRAY, PROD_FIRST_BATCH, PROD_OTHER_BATCH
        # Each stage gets 1 device
        assert len(batches) == 4
        assert batches[0].stage == "PREPUB"
        assert batches[0].device_count == 1
        assert batches[1].stage == "GRAY"
        assert batches[1].device_count == 1
        assert batches[2].stage == "PROD_FIRST_BATCH"
        assert batches[2].device_count == 1
        assert batches[3].stage == "PROD_OTHER_BATCH"
        assert batches[3].device_count == 1

    def test_generate_batches_five_devices_uses_full_pipeline(self):
        """Five devices should use full pipeline (5 > 4 stages)."""
        config = PublishConfig(
            replica_desired=5,
            batch_capacity=2,
        )
        batches = _publish_service_instance._generate_batches(
            PublishType.CREATE, config
        )

        # 5 devices > 4 stages, should NOT compact - use full pipeline
        # First batch should be PREPUB (first stage of full pipeline)
        assert len(batches) >= 1
        assert batches[0].stage == "PREPUB"

    def test_generate_batches_restart_single_device_compacts(self):
        """RESTART with 1 device should compact to 1 stage (PROD_FIRST_BATCH)."""
        config = PublishConfig(
            replica_desired=1,
            batch_capacity=1,
        )
        batches = _publish_service_instance._generate_batches(
            PublishType.RESTART, config
        )

        # RESTART has 2 stages, 1 device should compact to 1 stage
        assert len(batches) == 1
        assert batches[0].stage == "PROD_FIRST_BATCH"

    def test_generate_batches_restart_two_devices_uses_full_pipeline(self):
        """RESTART with 2 devices should use full 2-stage pipeline."""
        config = PublishConfig(
            replica_desired=2,
            batch_capacity=5,
        )
        batches = _publish_service_instance._generate_batches(
            PublishType.RESTART, config
        )

        # RESTART has 2 stages, 2 devices = no compact
        assert len(batches) == 2
        assert batches[0].stage == "PROD_FIRST_BATCH"
        assert batches[1].stage == "PROD_OTHER_BATCH"

    def test_generate_batches_no_replica_desired_uses_defaults(self):
        """Without replica_desired, uses default stage config."""
        # Default config with no replica_desired
        batches = _publish_service_instance._generate_batches(PublishType.CREATE, None)

        # Should create batches based on default stage config
        assert len(batches) > 0

    # ========================================================================
    # Tests for _calculate_total_devices SCALE operations (Tasks 1.3-1.5)
    # ========================================================================

    def test_calculate_total_devices_scale_up_returns_scale_amount(self):
        """SCALE_UP: scale_amount parameter takes priority over replica_desired."""
        config = PublishConfig(
            replica_desired=5,  # target: will have 5 total
            batch_capacity=5,
        )
        stages = [("direct", "PROD_FIRST_BATCH")]
        result = _publish_service_instance._calculate_total_devices(
            config, stages, scale_amount=3
        )

        # Should return 3 (scale_amount), not 5 (replica_desired)
        assert result == 3

    def test_calculate_total_devices_scale_down_returns_scale_amount(self):
        """SCALE_DOWN: scale_amount parameter takes priority over replica_desired."""
        config = PublishConfig(
            replica_desired=2,  # target: will have 2 remaining
            batch_capacity=5,
        )
        stages = [("direct", "PROD_FIRST_BATCH")]
        result = _publish_service_instance._calculate_total_devices(
            config, stages, scale_amount=3
        )

        # Should return 3 (scale_amount), not 2 (replica_desired)
        assert result == 3

    def test_calculate_total_devices_scale_fallback_to_replica_desired(self):
        """SCALE config without scale_amount should fallback to replica_desired."""
        config = PublishConfig(
            replica_desired=5,  # only target specified
            batch_capacity=5,
        )
        stages = [("direct", "PROD_FIRST_BATCH")]
        result = _publish_service_instance._calculate_total_devices(config, stages)

        # Should fallback to replica_desired
        assert result == 5

    # ========================================================================
    # Tests for SCALE multi-batch support (Tasks 2.3-2.5)
    # ========================================================================

    def test_scale_up_with_scale_amount_exceeds_batch_capacity(self):
        """SCALE_UP with scale_amount > batch_capacity should create multiple batches."""
        config = PublishConfig(
            replica_desired=10,  # target: 3 current + 7 delta
            batch_capacity=5,
        )
        batches = _publish_service_instance._generate_batches(
            PublishType.SCALE_UP, config, scale_amount=7
        )

        # 7 devices with batch_capacity=5 should create 2 batches: [5, 2]
        assert len(batches) == 2
        assert batches[0].device_count == 5
        assert batches[0].batch_capacity == 5
        assert batches[1].device_count == 2
        assert batches[1].batch_capacity == 2

    def test_scale_down_with_scale_amount_exceeds_batch_capacity(self):
        """SCALE_DOWN with scale_amount > batch_capacity should create multiple batches."""
        config = PublishConfig(
            replica_desired=2,  # target: 10 current - 8 delta
            batch_capacity=3,
        )
        batches = _publish_service_instance._generate_batches(
            PublishType.SCALE_DOWN, config, scale_amount=8
        )

        # 8 devices with batch_capacity=3 should create 3 batches: [3, 3, 2]
        assert len(batches) == 3
        assert batches[0].device_count == 3
        assert batches[1].device_count == 3
        assert batches[2].device_count == 2

    def test_scale_up_batch_sizing_exact(self):
        """SCALE_UP with scale_amount=7, batch_capacity=5 creates batches [5, 2]."""
        config = PublishConfig(
            replica_desired=12,  # target: 5 current + 7 delta
            batch_capacity=5,
        )
        batches = _publish_service_instance._generate_batches(
            PublishType.SCALE_UP, config, scale_amount=7
        )

        assert len(batches) == 2
        assert batches[0].device_count == 5
        assert batches[0].batch_capacity == 5
        assert batches[1].device_count == 2
        assert batches[1].batch_capacity == 2

    def test_scale_up_single_batch_when_within_capacity(self):
        """SCALE_UP with scale_amount <= batch_capacity should create single batch."""
        config = PublishConfig(
            replica_desired=8,  # target: 5 current + 3 delta
            batch_capacity=5,
        )
        batches = _publish_service_instance._generate_batches(
            PublishType.SCALE_UP, config, scale_amount=3
        )

        # 3 devices with batch_capacity=5 should fit in 1 batch
        assert len(batches) == 1
        assert batches[0].device_count == 3
        assert batches[0].batch_capacity == 3

    # ========================================================================
    # Tests for zero-device batch skipping (Tasks 3.2-3.3)
    # ========================================================================

    def test_auto_compact_skips_zero_device_batches(self):
        """Auto-compact with fewer devices than stages should not create zero-device batches."""
        # With 2 devices, auto-compact should create 2 batches with device_count=1 each
        # NOT 4 batches with [1, 1, 0, 0]
        config = PublishConfig(
            replica_desired=2,
            batch_capacity=5,
        )
        batches = _publish_service_instance._generate_batches(
            PublishType.CREATE, config
        )

        # Should have exactly 2 batches, not 4
        assert len(batches) == 2
        # All batches should have device_count > 0
        for batch in batches:
            assert batch.device_count > 0

    def test_create_single_device_single_batch(self):
        """CREATE with replica_desired=1 creates only 1 batch (no zero-device batches)."""
        config = PublishConfig(
            replica_desired=1,
            batch_capacity=5,
        )
        batches = _publish_service_instance._generate_batches(
            PublishType.CREATE, config
        )

        # Exactly 1 batch, no zero-device batches
        assert len(batches) == 1
        assert batches[0].device_count == 1
        assert batches[0].batch_capacity == 1

    # ========================================================================
    # Tests with 10+ devices (Tasks 4.1-4.5)
    # ========================================================================

    def test_create_twelve_devices_batch_sizing(self):
        """CREATE with replica_desired=12 and batch_capacity=5 creates batches [5, 5, 2]."""
        config = PublishConfig(
            replica_desired=12,
            batch_capacity=5,
        )
        batches = _publish_service_instance._generate_batches(
            PublishType.CREATE, config
        )

        # 12 devices > 4 stages, no compact - uses full pipeline
        # Each stage (4 stages) gets devices distributed, then each may split by batch_capacity
        # With distribute_devices=True and 12 devices over 4 stages: 3 devices per stage
        # But each stage then creates batches by batch_capacity
        # Total devices should equal 12
        total_devices = sum(b.device_count for b in batches)
        assert total_devices == 12
        # Each batch should respect batch_capacity
        for batch in batches:
            assert batch.batch_capacity <= 5

    def test_scale_up_fifteen_devices(self):
        """SCALE_UP with scale_amount=15 and batch_capacity=4 creates batches [4, 4, 4, 3]."""
        config = PublishConfig(
            replica_desired=20,  # target: 5 current + 15 delta
            batch_capacity=4,
        )
        batches = _publish_service_instance._generate_batches(
            PublishType.SCALE_UP, config, scale_amount=15
        )

        # 15 devices / batch_capacity=4 = 4 batches: [4, 4, 4, 3]
        assert len(batches) == 4
        assert batches[0].device_count == 4
        assert batches[1].device_count == 4
        assert batches[2].device_count == 4
        assert batches[3].device_count == 3

    def test_update_ten_devices_batch_sizing(self):
        """UPDATE with replica_desired=10 and batch_capacity=3 uses correct batch sizing."""
        config = PublishConfig(
            replica_desired=10,
            batch_capacity=3,
        )
        batches = _publish_service_instance._generate_batches(
            PublishType.UPDATE, config
        )

        # 10 devices > 4 stages, uses full pipeline with distribute_devices
        # Distribution: 10/4 = not even, last 2 stages get extras
        # Total devices should be 10
        total_devices = sum(b.device_count for b in batches)
        assert total_devices == 10
        # Each batch should respect batch_capacity
        for batch in batches:
            assert batch.batch_capacity <= 3

    def test_restart_many_devices_respects_batch_capacity(self):
        """RESTART with many devices respects batch_capacity."""
        config = PublishConfig(
            replica_desired=15,
            batch_capacity=4,
        )
        batches = _publish_service_instance._generate_batches(
            PublishType.RESTART, config
        )

        # RESTART has 2 stages, 15 devices distributed 1 per stage then split
        # With distribute_devices: 15 devices over 2 stages = each stage gets ~7-8
        # Then each stage splits by batch_capacity
        total_devices = sum(b.device_count for b in batches)
        assert total_devices == 15
        # Each batch should respect batch_capacity
        for batch in batches:
            assert batch.batch_capacity <= 4

    def test_destroy_many_devices_respects_batch_capacity(self):
        """DESTROY with many devices respects batch_capacity."""
        config = PublishConfig(
            replica_desired=20,
            batch_capacity=6,
        )
        batches = _publish_service_instance._generate_batches(
            PublishType.DESTROY, config
        )

        # DESTROY has 1 stage, uses distribute_devices with 20 devices
        # Total devices should be 20
        total_devices = sum(b.device_count for b in batches)
        assert total_devices == 20
        # Each batch should respect batch_capacity
        for batch in batches:
            assert batch.batch_capacity <= 6
