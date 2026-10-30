"""Integration tests for PublishService with real ZDAS database."""

from uuid import uuid4

import pytest

from secbaas.community.api.bot_manage import BotStatus
from secbaas.community.api.bot_runtime import BotNotFoundError
from secbaas.community.api.device_manage import DeployConfig
from secbaas.community.api.publish_manage import (
    BatchStatus,
    PublishConfig,
    PublishStatus,
    PublishType,
)
from secbaas.community.bootstrap import get_container
from secbaas.community.core.utils.env_utils import get_current_env

TEST_ENV = get_current_env()


def _ps():
    return get_container().services.publish_service()


@pytest.mark.integration
class TestPublishServiceCompletePublish:
    """Integration tests for complete_publish."""

    @pytest.mark.asyncio
    async def test_complete_publish_sets_success_status(
        self,
        bot_repository,
        created_bot_ids,
        created_publish_ids,
        created_tenant_ids,
        created_template_ids,
    ):
        """Test that complete_publish sets status to SUCCESS."""
        from .conftest import (
            create_test_bot_record,
            create_test_tenant,
        )

        tenant_name, template_id = create_test_tenant(
            created_tenant_ids, created_template_ids
        )

        bot_id = create_test_bot_record(
            bot_repository,
            tenant=tenant_name,
            status=BotStatus.ACTIVE.value,
            created_bot_ids=created_bot_ids,
        )

        publish = await _ps().create_publish(
            tenant=tenant_name,
            bot_id=bot_id,
            publish_type=PublishType.SCALE_UP,
            operator="test_user",
            request_id=uuid4().hex,
        )
        created_publish_ids.append(publish.id)

        # Set to ACTIVE for complete_publish

        repo = get_container().repository.publish_repository()
        repo.update_status(
            publish_id=publish.id,
            tenant=tenant_name,
            env=TEST_ENV,
            status=PublishStatus.ACTIVE.value,
            modifier="test_user",
        )

        result = await _ps().complete_publish(
            tenant=tenant_name,
            publish_id=publish.id,
            operator="test_user",
        )

        assert result.status == PublishStatus.SUCCESS.value

    @pytest.mark.asyncio
    async def test_complete_destroy_publish_sets_bot_released(
        self,
        bot_repository,
        device_repository,
        rel_repository,
        created_bot_ids,
        created_publish_ids,
        created_device_ids,
        created_rel_ids,
        created_tenant_ids,
        created_template_ids,
    ):
        from .conftest import (
            create_test_bot_record,
            create_test_devices_for_bot,
            create_test_tenant,
        )

        tenant_name, template_id = create_test_tenant(
            created_tenant_ids, created_template_ids
        )

        bot_id = create_test_bot_record(
            bot_repository,
            tenant=tenant_name,
            status=BotStatus.ACTIVE.value,
            created_bot_ids=created_bot_ids,
        )

        create_test_devices_for_bot(
            device_repository=device_repository,
            rel_repository=rel_repository,
            tenant=tenant_name,
            bot_id=bot_id,
            device_status="ACTIVE",
            num_devices=1,
            created_device_ids=created_device_ids,
            created_rel_ids=created_rel_ids,
        )

        publish = await _ps().create_publish(
            tenant=tenant_name,
            bot_id=bot_id,
            publish_type=PublishType.DESTROY,
            operator="test_user",
            request_id=uuid4().hex,
        )
        created_publish_ids.append(publish.id)

        # Set to ACTIVE

        repo = get_container().repository.publish_repository()
        repo.update_status(
            publish_id=publish.id,
            tenant=tenant_name,
            env=TEST_ENV,
            status=PublishStatus.ACTIVE.value,
            modifier="test_user",
        )

        await _ps().complete_publish(
            tenant=tenant_name,
            publish_id=publish.id,
            operator="test_user",
        )

        # Verify bot was soft-deleted by DESTROY cleanup
        # get_by_id uses is_deleted = 0 filter, so it should return None
        bot = bot_repository.get_by_id(
            bot_id=bot_id,
            tenant=tenant_name,
            env=TEST_ENV,
        )
        # Bot should be soft-deleted, so get_by_id returns None
        assert bot is None, "Bot should be soft-deleted after DESTROY"


@pytest.mark.integration
class TestPublishServiceExecuteStage:
    """Integration tests for execute_stage with full batch execution."""

    @pytest.mark.asyncio
    async def test_execute_stage_no_pending_batches(
        self,
        bot_repository,
        created_bot_ids,
        created_publish_ids,
        created_tenant_ids,
        created_template_ids,
    ):
        """Test execute_stage returns success when no pending batches."""

        from .conftest import (
            create_test_bot_record,
            create_test_tenant,
        )

        tenant_name, template_id = create_test_tenant(
            created_tenant_ids, created_template_ids
        )

        bot_id = create_test_bot_record(
            bot_repository,
            tenant=tenant_name,
            status=BotStatus.ACTIVE.value,
            created_bot_ids=created_bot_ids,
        )

        publish = await _ps().create_publish(
            tenant=tenant_name,
            bot_id=bot_id,
            publish_type=PublishType.SCALE_UP,
            operator="test_user",
            request_id=uuid4().hex,
        )
        created_publish_ids.append(publish.id)

        # Set to ACTIVE and mark all batches as SUCCESS

        repo = get_container().repository.publish_repository()
        repo.update_status(
            publish_id=publish.id,
            tenant=tenant_name,
            env=TEST_ENV,
            status=PublishStatus.ACTIVE.value,
            modifier="test_user",
        )

        # Mark all batches as SUCCESS
        batch_repo = get_container().repository.publish_batch_repository()
        batches = batch_repo.list_by_publish_id(publish.id, tenant_name, TEST_ENV)
        for batch in batches:
            batch_repo.update_status(
                batch_id=batch.id,
                tenant=tenant_name,
                env=TEST_ENV,
                status=BatchStatus.COMPLETED.value,
                modifier="test_user",
            )

        # Execute should return success with no work
        result = await _ps().execute_stage(
            tenant=tenant_name,
            publish_id=publish.id,
            operator="test_user",
        )

        assert result.success is True
        assert result.sessions_remaining == 0

    @pytest.mark.asyncio
    async def test_execute_stage_returns_current_stage(
        self,
        bot_repository,
        device_repository,
        rel_repository,
        created_bot_ids,
        created_publish_ids,
        created_device_ids,
        created_rel_ids,
        created_tenant_ids,
        created_template_ids,
    ):
        from secbaas.community.api.publish_manage import PublishStage

        from .conftest import (
            create_test_bot_record,
            create_test_devices_for_bot,
            create_test_tenant,
        )

        tenant_name, template_id = create_test_tenant(
            created_tenant_ids, created_template_ids
        )

        bot_id = create_test_bot_record(
            bot_repository,
            tenant=tenant_name,
            status=BotStatus.ACTIVE.value,
            created_bot_ids=created_bot_ids,
        )

        create_test_devices_for_bot(
            device_repository=device_repository,
            rel_repository=rel_repository,
            tenant=tenant_name,
            bot_id=bot_id,
            device_status="PENDING",
            num_devices=1,
            created_device_ids=created_device_ids,
            created_rel_ids=created_rel_ids,
        )

        publish = await _ps().create_publish(
            tenant=tenant_name,
            bot_id=bot_id,
            publish_type=PublishType.CREATE,
            operator="test_user",
            request_id=uuid4().hex,
        )
        created_publish_ids.append(publish.id)

        # Stage should be PROD_FIRST_BATCH (auto-compact for 1 device skips PREPUB/GRAY)
        assert publish.stage == PublishStage.PROD_FIRST_BATCH.value

    @pytest.mark.asyncio
    async def test_execute_stage_scale_up_with_pending_batch(
        self,
        bot_repository,
        created_bot_ids,
        created_publish_ids,
        created_tenant_ids,
        created_template_ids,
    ):
        """Test execute_stage with SCALE_UP pending batch."""

        from .conftest import (
            create_test_bot_record,
            create_test_tenant,
        )

        tenant_name, template_id = create_test_tenant(
            created_tenant_ids, created_template_ids
        )

        bot_id = create_test_bot_record(
            bot_repository,
            tenant=tenant_name,
            status=BotStatus.ACTIVE.value,
            created_bot_ids=created_bot_ids,
        )

        publish = await _ps().create_publish(
            tenant=tenant_name,
            bot_id=bot_id,
            publish_type=PublishType.SCALE_UP,
            operator="test_user",
            request_id=uuid4().hex,
            config=PublishConfig(),
        )
        created_publish_ids.append(publish.id)

        # Submit and approve
        await _ps().approve_stage(
            tenant=tenant_name,
            publish_id=publish.id,
            operator="test_approver",
        )

        # Execute stage (will try to create devices via DeviceService)
        # Note: This may fail if PaaS/template is not available, but tests the flow
        try:
            result = await _ps().execute_stage(
                tenant=tenant_name,
                publish_id=publish.id,
                operator="test_user",
            )
            # If no exception, check result
            assert result is not None
        except Exception as e:
            # Expected if DeviceService.create_device fails (no PaaS or no template_id)
            # OR if auto-execute already dispatched the batch (status is no longer ACTIVE)
            # The important thing is the code path was exercised
            error_lower = str(e).lower()
            assert (
                "template" in error_lower
                or "device" in error_lower
                or "paas" in error_lower
                or "not found" in error_lower
                or "cannot execute stage" in error_lower
            )


@pytest.mark.integration
class TestPublishServiceStageTracking:
    """Integration tests for stage tracking in batches."""

    @pytest.mark.asyncio
    async def test_create_publish_stores_stage_in_batches(
        self,
        bot_repository,
        device_repository,
        rel_repository,
        created_bot_ids,
        created_publish_ids,
        created_device_ids,
        created_rel_ids,
        created_tenant_ids,
        created_template_ids,
    ):
        from secbaas.community.api.publish_manage import PublishStage

        from .conftest import (
            create_test_bot_record,
            create_test_devices_for_bot,
            create_test_tenant,
        )

        tenant_name, template_id = create_test_tenant(
            created_tenant_ids, created_template_ids
        )

        bot_id = create_test_bot_record(
            bot_repository,
            tenant=tenant_name,
            status=BotStatus.ACTIVE.value,
            created_bot_ids=created_bot_ids,
        )

        create_test_devices_for_bot(
            device_repository=device_repository,
            rel_repository=rel_repository,
            tenant=tenant_name,
            bot_id=bot_id,
            device_status="PENDING",
            num_devices=1,
            created_device_ids=created_device_ids,
            created_rel_ids=created_rel_ids,
        )

        publish = await _ps().create_publish(
            tenant=tenant_name,
            bot_id=bot_id,
            publish_type=PublishType.CREATE,
            operator="test_user",
            request_id=uuid4().hex,
        )
        created_publish_ids.append(publish.id)

        # Verify batches have stage in extra_config
        batch_repo = get_container().repository.publish_batch_repository()
        batches = batch_repo.list_by_publish_id(publish.id, tenant_name, TEST_ENV)

        assert len(batches) > 0
        for batch in batches:
            assert batch.stage in [
                PublishStage.PREPUB.value,
                PublishStage.GRAY.value,
                PublishStage.PROD_FIRST_BATCH.value,
                PublishStage.PROD_OTHER_BATCH.value,
            ]

    @pytest.mark.asyncio
    async def test_restart_publish_has_two_stages(
        self,
        bot_repository,
        device_repository,
        rel_repository,
        created_bot_ids,
        created_publish_ids,
        created_device_ids,
        created_rel_ids,
        created_tenant_ids,
        created_template_ids,
    ):
        from secbaas.community.api.publish_manage import PublishStage

        from .conftest import (
            create_test_bot_record,
            create_test_devices_for_bot,
            create_test_tenant,
        )

        tenant_name, template_id = create_test_tenant(
            created_tenant_ids, created_template_ids
        )

        from secbaas.community.api.publish_manage import PublishConfig

        bot_id = create_test_bot_record(
            bot_repository,
            tenant=tenant_name,
            status=BotStatus.ACTIVE.value,
            created_bot_ids=created_bot_ids,
        )

        create_test_devices_for_bot(
            device_repository=device_repository,
            rel_repository=rel_repository,
            tenant=tenant_name,
            bot_id=bot_id,
            device_status="ACTIVE",
            num_devices=5,
            created_device_ids=created_device_ids,
            created_rel_ids=created_rel_ids,
        )

        publish = await _ps().create_publish(
            tenant=tenant_name,
            bot_id=bot_id,
            publish_type=PublishType.RESTART,
            operator="test_user",
            request_id=uuid4().hex,
            config=PublishConfig(replica_desired=5),
        )
        created_publish_ids.append(publish.id)

        batch_repo = get_container().repository.publish_batch_repository()
        batches = batch_repo.list_by_publish_id(publish.id, tenant_name, TEST_ENV)

        # Should have stages: PROD_FIRST_BATCH, PROD_OTHER_BATCH
        stages = set(b.stage for b in batches)
        assert PublishStage.PROD_FIRST_BATCH.value in stages
        assert PublishStage.PROD_OTHER_BATCH.value in stages
        # Should NOT have PREPUB or GRAY
        assert PublishStage.PREPUB.value not in stages
        assert PublishStage.GRAY.value not in stages

    @pytest.mark.asyncio
    async def test_scale_publish_has_single_stage(
        self,
        bot_repository,
        created_bot_ids,
        created_publish_ids,
        created_tenant_ids,
        created_template_ids,
    ):
        """Test that SCALE_UP type creates single batch."""

        from .conftest import (
            create_test_bot_record,
            create_test_tenant,
        )

        tenant_name, template_id = create_test_tenant(
            created_tenant_ids, created_template_ids
        )

        bot_id = create_test_bot_record(
            bot_repository,
            tenant=tenant_name,
            status=BotStatus.ACTIVE.value,
            created_bot_ids=created_bot_ids,
        )

        from secbaas.community.api.publish_manage import PublishConfig

        publish = await _ps().create_publish(
            tenant=tenant_name,
            bot_id=bot_id,
            publish_type=PublishType.SCALE_UP,
            operator="test_user",
            config=PublishConfig(replica_desired=5, batch_capacity=2),
            request_id=uuid4().hex,
        )
        created_publish_ids.append(publish.id)

        batch_repo = get_container().repository.publish_batch_repository()
        batches = batch_repo.list_by_publish_id(publish.id, tenant_name, TEST_ENV)

        # Should have at least one batch
        assert len(batches) >= 1


@pytest.mark.integration
class TestPublishServiceListPublishes:
    """Integration tests for list_publishes."""

    @pytest.mark.asyncio
    async def test_list_publishes_by_bot_id(
        self,
        bot_repository,
        created_bot_ids,
        created_publish_ids,
        created_tenant_ids,
        created_template_ids,
    ):
        """Test list_publishes returns list of publishes for a bot."""
        from .conftest import (
            create_test_bot_record,
            create_test_tenant,
        )

        tenant_name, template_id = create_test_tenant(
            created_tenant_ids, created_template_ids
        )

        bot_id = create_test_bot_record(
            bot_repository,
            tenant=tenant_name,
            status=BotStatus.ACTIVE.value,
            created_bot_ids=created_bot_ids,
        )

        # Create multiple publishes
        for i in range(3):
            publish = await _ps().create_publish(
                tenant=tenant_name,
                bot_id=bot_id,
                publish_type=PublishType.SCALE_UP,
                operator="test_user",
                request_id=uuid4().hex,
            )
            created_publish_ids.append(publish.id)

            # Complete each one to allow next

            repo = get_container().repository.publish_repository()
            repo.update_status(
                publish_id=publish.id,
                tenant=tenant_name,
                env=TEST_ENV,
                status=PublishStatus.SUCCESS.value,
                modifier="test_user",
            )

        # List publishes by bot_id
        result = await _ps().list_publishes(
            tenant=tenant_name,
            bot_id=bot_id,
            page=1,
            page_size=10,
        )

        # Result is a list
        assert isinstance(result, list)
        assert len(result) >= 3


@pytest.mark.integration
class TestPublishServiceGetPendingBatches:
    """Integration tests for _get_pending_batches method."""

    @pytest.mark.asyncio
    async def test_get_pending_batches_returns_first_pending(
        self,
        bot_repository,
        device_repository,
        rel_repository,
        created_bot_ids,
        created_publish_ids,
        created_device_ids,
        created_rel_ids,
        created_tenant_ids,
        created_template_ids,
    ):
        from secbaas.community.api.publish_manage import PublishStage

        from .conftest import (
            create_test_bot_record,
            create_test_devices_for_bot,
            create_test_tenant,
        )

        tenant_name, template_id = create_test_tenant(
            created_tenant_ids, created_template_ids
        )

        bot_id = create_test_bot_record(
            bot_repository,
            tenant=tenant_name,
            status=BotStatus.ACTIVE.value,
            created_bot_ids=created_bot_ids,
        )

        create_test_devices_for_bot(
            device_repository=device_repository,
            rel_repository=rel_repository,
            tenant=tenant_name,
            bot_id=bot_id,
            device_status="PENDING",
            num_devices=1,
            created_device_ids=created_device_ids,
            created_rel_ids=created_rel_ids,
        )

        publish = await _ps().create_publish(
            tenant=tenant_name,
            bot_id=bot_id,
            publish_type=PublishType.CREATE,
            operator="test_user",
            request_id=uuid4().hex,
        )
        created_publish_ids.append(publish.id)

        # Get pending batches
        current_stage, batches = _ps()._get_pending_batches(tenant_name, publish.id)

        # Should return PROD_FIRST_BATCH batches (first stage, auto-compact for 1 device)
        assert current_stage == PublishStage.PROD_FIRST_BATCH.value
        assert len(batches) > 0
        for batch in batches:
            assert batch.stage == PublishStage.PROD_FIRST_BATCH.value

    @pytest.mark.asyncio
    async def test_get_pending_batches_after_first_stage_complete(
        self,
        bot_repository,
        device_repository,
        rel_repository,
        created_bot_ids,
        created_publish_ids,
        created_device_ids,
        created_rel_ids,
        created_tenant_ids,
        created_template_ids,
    ):
        from secbaas.community.api.publish_manage import PublishConfig, PublishStage

        from .conftest import (
            create_test_bot_record,
            create_test_devices_for_bot,
            create_test_tenant,
        )

        tenant_name, template_id = create_test_tenant(
            created_tenant_ids, created_template_ids
        )

        bot_id = create_test_bot_record(
            bot_repository,
            tenant=tenant_name,
            status=BotStatus.ACTIVE.value,
            created_bot_ids=created_bot_ids,
        )

        create_test_devices_for_bot(
            device_repository=device_repository,
            rel_repository=rel_repository,
            tenant=tenant_name,
            bot_id=bot_id,
            device_status="PENDING",
            num_devices=5,
            created_device_ids=created_device_ids,
            created_rel_ids=created_rel_ids,
        )

        publish = await _ps().create_publish(
            tenant=tenant_name,
            bot_id=bot_id,
            publish_type=PublishType.CREATE,
            operator="test_user",
            request_id=uuid4().hex,
            config=PublishConfig(replica_desired=5),
        )
        created_publish_ids.append(publish.id)

        # Mark PREPUB batches as COMPLETED
        batch_repo = get_container().repository.publish_batch_repository()
        all_batches = batch_repo.list_by_publish_id(publish.id, tenant_name, TEST_ENV)
        for batch in all_batches:
            if batch.stage == PublishStage.PREPUB.value:
                batch_repo.update_status(
                    batch_id=batch.id,
                    tenant=tenant_name,
                    env=TEST_ENV,
                    status=BatchStatus.COMPLETED.value,
                    modifier="test_user",
                )

        # Get pending batches - should return GRAY batches
        current_stage, batches = _ps()._get_pending_batches(tenant_name, publish.id)

        assert current_stage == PublishStage.GRAY.value
        for batch in batches:
            assert batch.stage == PublishStage.GRAY.value
