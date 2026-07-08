"""Unit tests for callback-driven batch completion, stage advancement, and publish state chain.

Tests _check_batch_completion and _check_stage_advancement by mocking repositories.
"""

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_service(
    batch_repo=None,
    rec_repo=None,
    publish_repo=None,
    bot_repo=None,
):
    """Construct a DefaultPublishService with injected mock repos."""
    from secbaas.core.service.publish_manage import DefaultPublishService

    return DefaultPublishService(
        bot_repo=bot_repo or MagicMock(),
        device_repo=MagicMock(),
        rel_repo=MagicMock(),
        session_repo=MagicMock(),
        publish_repo=publish_repo or MagicMock(),
        batch_repo=batch_repo or MagicMock(),
        publish_record_repo=rec_repo or MagicMock(),
        template_service=MagicMock(),
        bot_service=MagicMock(),
        device_service=MagicMock(),
    )


@dataclass
class StubBatch:
    id: int
    stage: str = "PREPUB"
    status: str = "RUNNING"
    batch_capacity: int = 1


@dataclass
class StubPublish:
    id: int
    bot_id: int = 1
    publish_type: str = "CREATE"
    status: str = "ACTIVE"
    extra_config: dict | None = None
    creator: str = "test"
    modifier: str = "test"
    gmt_create: str = "2026-01-01T00:00:00"
    gmt_modified: str = "2026-01-01T00:00:00"

    def __post_init__(self):
        if self.extra_config is None:
            self.extra_config = {"auto_complete": True, "stages": {}}


@dataclass
class StubBot:
    id: int = 1
    status: str = "PENDING"


def _mock_batch_repo(batch):
    mock = MagicMock()
    mock.get_by_id.return_value = batch
    mock.list_by_publish_id.return_value = [batch]
    return mock


def _mock_rec_repo(counts: dict | None = None) -> MagicMock:
    mock = MagicMock()
    if counts is None:
        counts = {"SUCCESS": 1}
    mock.count_records_by_batch_id.return_value = counts
    return mock


def _mock_publish_repo(publish):
    mock = MagicMock()
    mock.get_by_id.return_value = publish
    return mock


def _mock_bot_repo(bot=None):
    mock = MagicMock()
    if bot is None:
        bot = StubBot()
    mock.get_by_id.return_value = bot
    return mock


class TestCheckBatchCompletion:
    @pytest.mark.asyncio
    async def test_batch_not_complete_when_processing_records_exist(self):
        """If CREATED records remain, batch stays RUNNING."""
        batch = StubBatch(id=5, status="RUNNING")
        mock_batch = _mock_batch_repo(batch)
        mock_rec = _mock_rec_repo({"PROCESSING": 1, "SUCCESS": 2})

        service = _make_service(batch_repo=mock_batch, rec_repo=mock_rec)
        await service._check_batch_completion(tenant="test", batch_id=5, publish_id=100)
        mock_batch.update_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_batch_completed_when_all_success(self):
        """All records SUCCESS → batch COMPLETED."""
        batch = StubBatch(id=5, status="RUNNING")
        mock_batch = _mock_batch_repo(batch)
        mock_rec = _mock_rec_repo({"SUCCESS": 3})
        mock_publish = _mock_publish_repo(StubPublish(id=100))
        mock_bot = _mock_bot_repo()

        service = _make_service(
            batch_repo=mock_batch,
            rec_repo=mock_rec,
            publish_repo=mock_publish,
            bot_repo=mock_bot,
        )
        await service._check_batch_completion(tenant="test", batch_id=5, publish_id=100)
        mock_batch.update_status.assert_called_once()
        call_kwargs = mock_batch.update_status.call_args[1]
        assert call_kwargs["status"] == "COMPLETED"

    @pytest.mark.asyncio
    async def test_batch_failed_when_any_failed(self):
        """Any FAILED record → batch FAILED."""
        batch = StubBatch(id=5, status="RUNNING")
        mock_batch = _mock_batch_repo(batch)
        mock_rec = _mock_rec_repo({"SUCCESS": 2, "FAILED": 1})
        mock_publish = _mock_publish_repo(StubPublish(id=100))
        mock_bot = _mock_bot_repo()

        service = _make_service(
            batch_repo=mock_batch,
            rec_repo=mock_rec,
            publish_repo=mock_publish,
            bot_repo=mock_bot,
        )
        await service._check_batch_completion(tenant="test", batch_id=5, publish_id=100)
        mock_batch.update_status.assert_called_once()
        call_kwargs = mock_batch.update_status.call_args[1]
        assert call_kwargs["status"] == "FAILED"

    @pytest.mark.asyncio
    async def test_batch_already_completed_no_duplicate_update(self):
        """If batch is already COMPLETED, skip update_status call."""
        batch = StubBatch(id=5, stage="PROD_OTHER_BATCH", status="COMPLETED")
        mock_batch = _mock_batch_repo(batch)
        mock_rec = _mock_rec_repo({"SUCCESS": 3})
        mock_publish = _mock_publish_repo(
            StubPublish(id=100, extra_config={"auto_complete": True, "stages": {}})
        )
        mock_bot = _mock_bot_repo()

        service = _make_service(
            batch_repo=mock_batch,
            rec_repo=mock_rec,
            publish_repo=mock_publish,
            bot_repo=mock_bot,
        )
        with patch.object(service, "complete_publish", new_callable=AsyncMock):
            await service._check_batch_completion(
                tenant="test", batch_id=5, publish_id=100
            )
            # update_status not called since batch already COMPLETED
            mock_batch.update_status.assert_not_called()


class TestCheckStageAdvancementPublishFailure:
    @pytest.mark.asyncio
    async def test_stage_failed_sets_publish_failed(self):
        """When stage_failed=True, publish → FAILED."""
        publish = StubPublish(id=100, publish_type="CREATE", status="ACTIVE")
        mock_publish = _mock_publish_repo(publish)
        mock_bot = _mock_bot_repo(StubBot(id=1, status="PENDING"))

        service = _make_service(
            publish_repo=mock_publish,
            bot_repo=mock_bot,
        )
        await service._check_stage_advancement(
            tenant="test", publish_id=100, current_stage="PREPUB", stage_failed=True
        )
        mock_publish.update_status.assert_called_once()
        call_kwargs = mock_publish.update_status.call_args[1]
        assert call_kwargs["status"] == "FAILED"

    @pytest.mark.asyncio
    async def test_create_failure_sets_bot_failed(self):
        """CREATE publish failure with PENDING bot → bot FAILED."""
        publish = StubPublish(id=100, publish_type="CREATE", status="ACTIVE")
        mock_publish = _mock_publish_repo(publish)
        mock_bot = _mock_bot_repo(StubBot(id=1, status="PENDING"))

        service = _make_service(
            publish_repo=mock_publish,
            bot_repo=mock_bot,
        )
        await service._check_stage_advancement(
            tenant="test", publish_id=100, current_stage="PREPUB", stage_failed=True
        )
        mock_bot.update_status.assert_called_once()
        call_kwargs = mock_bot.update_status.call_args[1]
        assert call_kwargs["status"] == "FAILED"

    @pytest.mark.asyncio
    async def test_non_create_failure_does_not_change_bot(self):
        """UPDATE/RESTART publish failure doesn't set bot FAILED."""
        publish = StubPublish(id=100, publish_type="UPDATE", status="ACTIVE")
        mock_publish = _mock_publish_repo(publish)
        bot = StubBot(id=1, status="ACTIVE")
        mock_bot = _mock_bot_repo(bot)

        service = _make_service(
            publish_repo=mock_publish,
            bot_repo=mock_bot,
        )
        await service._check_stage_advancement(
            tenant="test", publish_id=100, current_stage="PREPUB", stage_failed=True
        )
        mock_bot.update_status.assert_not_called()


class TestCheckStageAdvancementStageComplete:
    @pytest.mark.asyncio
    async def test_all_batches_complete_no_more_stages_auto_complete(self):
        """Last stage complete, auto_complete → complete_publish called."""
        batch = StubBatch(id=5, stage="PROD_OTHER_BATCH", status="COMPLETED")
        publish = StubPublish(
            id=100,
            extra_config={"auto_complete": True, "stages": {}},
        )
        mock_batch = MagicMock()
        mock_batch.list_by_publish_id.return_value = [batch]
        mock_publish = _mock_publish_repo(publish)
        mock_bot = _mock_bot_repo()

        service = _make_service(
            batch_repo=mock_batch,
            publish_repo=mock_publish,
            bot_repo=mock_bot,
        )
        with patch.object(
            service, "complete_publish", new_callable=AsyncMock
        ) as mock_complete:
            await service._check_stage_advancement(
                tenant="test",
                publish_id=100,
                current_stage="PROD_OTHER_BATCH",
                stage_failed=False,
            )
            mock_complete.assert_called_once()

    @pytest.mark.asyncio
    async def test_not_all_batches_complete_stays_running(self):
        """If some batches still RUNNING, no stage advancement."""
        batch_running = StubBatch(id=5, stage="PREPUB", status="RUNNING")
        batch_done = StubBatch(id=6, stage="PREPUB", status="COMPLETED")
        publish = StubPublish(id=100)
        mock_batch = MagicMock()
        mock_batch.list_by_publish_id.return_value = [batch_running, batch_done]
        mock_publish = _mock_publish_repo(publish)

        service = _make_service(
            batch_repo=mock_batch,
            publish_repo=mock_publish,
        )
        await service._check_stage_advancement(
            tenant="test",
            publish_id=100,
            current_stage="PREPUB",
            stage_failed=False,
        )
        mock_publish.update_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_next_stage_with_approval_sets_approving(self):
        """Next stage has pause_for_approval=True → publish → APPROVING."""
        batch = StubBatch(id=5, stage="PREPUB", status="COMPLETED")
        gray_batch = StubBatch(id=6, stage="GRAY", status="PENDING")
        publish = StubPublish(
            id=100,
            extra_config={
                "auto_complete": True,
                "stages": {"GRAY": {"pause_for_approval": True}},
            },
        )
        mock_batch = MagicMock()
        mock_batch.list_by_publish_id.return_value = [batch, gray_batch]
        mock_publish = _mock_publish_repo(publish)

        service = _make_service(
            batch_repo=mock_batch,
            publish_repo=mock_publish,
        )
        await service._check_stage_advancement(
            tenant="test",
            publish_id=100,
            current_stage="PREPUB",
            stage_failed=False,
        )
        mock_publish.update_status.assert_called_once()
        call_kwargs = mock_publish.update_status.call_args[1]
        assert call_kwargs["status"] == "APPROVING"
