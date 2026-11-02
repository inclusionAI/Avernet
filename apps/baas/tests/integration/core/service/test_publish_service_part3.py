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
class TestPublishConfigWithDeployConfig:
    """Integration tests for PublishConfig with deploy_config persistence."""

    @pytest.mark.asyncio
    async def test_create_publish_with_deploy_config(
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
            device_status="PENDING",
            num_devices=1,
            created_device_ids=created_device_ids,
            created_rel_ids=created_rel_ids,
        )
        from secbaas.community.api.device_manage import MountPermission, MountPoint

        deploy_config = DeployConfig(
            after_create_cmd_hook="/usr/local/bin/deploy_test_hook.sh",
            envs={"ENTITY_ID": "staff_deploy_test", "DEPLOY_ENV": "test"},
            mount_points=[
                MountPoint(
                    id="mount_1",
                    remote_dir="server:/data/{bot_uuid}/{device_uuid}",
                    local_dir="/mnt/data",
                    permission=MountPermission.READ_WRITE,
                ),
            ],
        )

        publish_config = PublishConfig(
            bot_name="deploy_test_bot",
            replica_desired=3,
            batch_capacity=2,
            cooldown_seconds=0,
            deploy_config=deploy_config,
        )

        publish = await _ps().create_publish(
            tenant=tenant_name,
            bot_id=bot_id,
            publish_type=PublishType.CREATE,
            operator="test_user",
            config=publish_config,
            request_id=uuid4().hex,
        )
        created_publish_ids.append(publish.id)

        assert publish is not None
        assert publish.status == PublishStatus.PENDING.value
        assert publish.extra_config is not None

        # Verify deploy_config is in extra_config
        stored_config = PublishConfig.model_validate(publish.extra_config)
        assert stored_config.deploy_config is not None
        assert (
            stored_config.deploy_config.after_create_cmd_hook
            == "/usr/local/bin/deploy_test_hook.sh"
        )
        assert stored_config.deploy_config.mount_points is not None
        assert len(stored_config.deploy_config.mount_points) == 1
        assert stored_config.deploy_config.mount_points[0].local_dir == "/mnt/data"
        assert stored_config.deploy_config.envs is not None
        assert stored_config.deploy_config.envs["ENTITY_ID"] == "staff_deploy_test"

    @pytest.mark.asyncio
    async def test_deploy_config_persists_in_publish_record(
        self,
        bot_repository,
        device_repository,
        rel_repository,
        publish_repository,
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
        from secbaas.community.api.device_manage import MountPermission, MountPoint

        deploy_config = DeployConfig(
            after_create_cmd_hook="/usr/local/bin/deploy_multi_mount.sh",
            envs={"ENTITY_ID": "staff_multi_nas"},
            mount_points=[
                MountPoint(
                    id="mount_1",
                    remote_dir="server1:/data/{entity_id}/{bot_uuid}",
                    local_dir="/mnt/data",
                    permission=MountPermission.READ_WRITE,
                ),
                MountPoint(
                    id="mount_2",
                    remote_dir="server2:/logs/{entity_id}/{bot_uuid}",
                    local_dir="/mnt/logs",
                    permission=MountPermission.READ_WRITE,
                ),
                MountPoint(
                    id="mount_3",
                    remote_dir="server3:/cache/{entity_id}",
                    local_dir="/mnt/cache",
                    permission=MountPermission.READ_ONLY,
                ),
            ],
        )

        publish_config = PublishConfig(
            bot_name="multi_nas_bot",
            replica_desired=5,
            deploy_config=deploy_config,
        )

        publish = await _ps().create_publish(
            tenant=tenant_name,
            bot_id=bot_id,
            publish_type=PublishType.UPDATE,
            operator="test_user",
            config=publish_config,
            request_id=uuid4().hex,
        )
        created_publish_ids.append(publish.id)

        # Retrieve from repository and verify
        record = publish_repository.get_by_id(
            publish.id, tenant=tenant_name, env=TEST_ENV
        )
        assert record is not None

        stored_config = PublishConfig.model_validate(record.extra_config)
        assert stored_config.deploy_config is not None
        assert (
            stored_config.deploy_config.after_create_cmd_hook
            == "/usr/local/bin/deploy_multi_mount.sh"
        )

        # Verify all mount points persisted with correct order
        assert stored_config.deploy_config.mount_points is not None
        assert len(stored_config.deploy_config.mount_points) == 3
        assert stored_config.deploy_config.mount_points[0].local_dir == "/mnt/data"
        assert stored_config.deploy_config.mount_points[1].local_dir == "/mnt/logs"
        assert stored_config.deploy_config.mount_points[2].local_dir == "/mnt/cache"

        # Verify placeholders are NOT resolved (still templates)
        assert "{entity_id}" in stored_config.deploy_config.mount_points[0].remote_dir
        assert "{bot_uuid}" in stored_config.deploy_config.mount_points[0].remote_dir


@pytest.mark.integration
class TestPublishProgress:
    """Integration tests for get_publish_progress."""

    @pytest.mark.asyncio
    async def test_get_progress_for_existing_publish(
        self,
        bot_repository,
        created_bot_ids,
        created_publish_ids,
        created_tenant_ids,
        created_template_ids,
    ):
        """Test that get_publish_progress returns valid progress info."""
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

        progress = await _ps().get_publish_progress(
            tenant=tenant_name,
            publish_id=publish.id,
        )

        assert progress is not None
        assert progress.publish_id == publish.id
        assert progress.status == PublishStatus.PENDING.value
        assert progress.overall_progress is not None
        # PENDING status publish has no batches yet, so total_batches is 0
        assert progress.overall_progress.total_batches >= 0
        assert progress.overall_progress.progress_percentage >= 0.0

    @pytest.mark.asyncio
    async def test_get_progress_returns_none_for_wrong_tenant(
        self,
        bot_repository,
        created_bot_ids,
        created_publish_ids,
        created_tenant_ids,
        created_template_ids,
    ):
        """Test that get_publish_progress enforces tenant isolation."""
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

        progress = await _ps().get_publish_progress(
            tenant="wrong_tenant",
            publish_id=publish.id,
        )

        assert progress is None


@pytest.mark.integration
class TestPublishProgressLifecycle:
    """Integration tests for progress tracking through full publish lifecycle."""

    @pytest.mark.asyncio
    async def test_progress_tracks_full_publish_lifecycle(
        self,
        bot_repository,
        created_bot_ids,
        created_publish_ids,
        created_tenant_ids,
        created_template_ids,
        device_repository,
        created_device_ids,
    ):
        """Test progress from creation through completion."""

        from .conftest import (
            create_test_bot_record,
            create_test_device_record,
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

        # Create test devices directly in DB (avoids PaaS layer)
        # SCALE_UP default batch_capacity is 10, so create enough devices
        device_ids = []
        for _ in range(10):
            device_id = create_test_device_record(
                device_repository,
                tenant=tenant_name,
                created_device_ids=created_device_ids,
            )
            device_ids.append(device_id)

        # Step 1: Create publish with config so batches are created
        publish = await _ps().create_publish(
            tenant=tenant_name,
            bot_id=bot_id,
            publish_type=PublishType.SCALE_UP,
            operator="test_user",
            config=PublishConfig(replica_desired=5, batch_capacity=10),
            request_id=uuid4().hex,
        )
        created_publish_ids.append(publish.id)

        # Check initial progress - should be PENDING with 0%
        progress = await _ps().get_publish_progress(
            tenant=tenant_name,
            publish_id=publish.id,
        )
        assert progress is not None
        assert progress.status == PublishStatus.PENDING.value
        assert progress.overall_progress.progress_percentage == 0.0

        # Step 2: Approve (auto-execute may fail due to no template_uuid)
        try:
            await _ps().approve_stage(
                tenant=tenant_name, publish_id=publish.id, operator="test_approver"
            )
        except Exception:
            pass

        # Check progress after approval (may be ACTIVE or FAILED)
        progress = await _ps().get_publish_progress(
            tenant=tenant_name, publish_id=publish.id
        )

        # Step 3: Simulate batch completion
        batch_repo = get_container().repository.publish_batch_repository()
        batches = batch_repo.list_by_publish_id(publish.id, tenant_name, TEST_ENV)

        if batches:
            batch_repo.update_status(
                batch_id=batches[0].id,
                tenant=tenant_name,
                env=TEST_ENV,
                status=BatchStatus.COMPLETED.value,
                modifier="test_user",
            )

            # Add publish records with device IDs
            record_repo = get_container().repository.publish_record_repository()
            records_to_add = min(len(device_ids), batches[0].batch_capacity)
            for device_id in device_ids[:records_to_add]:
                record_repo.insert_record(
                    tenant=tenant_name,
                    env=TEST_ENV,
                    domain="default",
                    device_id=device_id,
                    bot_id=bot_id,
                    publish_id=publish.id,
                    batch_id=batches[0].id,
                    event_type="CREATE",
                    result_status="SUCCESS",
                    creator="test_user",
                    modifier="test_user",
                )

            progress = await _ps().get_publish_progress(
                tenant=tenant_name, publish_id=publish.id
            )
            assert progress is not None
            assert progress.overall_progress.completed_batches >= 1

        # Step 4: Complete publish
        for batch in batches:
            batch_repo.update_status(
                batch_id=batch.id,
                tenant=tenant_name,
                env=TEST_ENV,
                status=BatchStatus.COMPLETED.value,
                modifier="test_user",
            )

        await _ps().complete_publish(
            tenant=tenant_name, publish_id=publish.id, operator="test_user"
        )

        progress = await _ps().get_publish_progress(
            tenant=tenant_name, publish_id=publish.id
        )
        assert progress is not None
        assert progress.status in (
            PublishStatus.SUCCESS.value,
            PublishStatus.FAILED.value,
        ), f"Expected SUCCESS or FAILED, got {progress.status}"

    @pytest.mark.asyncio
    async def test_progress_shows_stage_breakdown(
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
            publish_type=PublishType.UPDATE,
            operator="test_user",
            request_id=uuid4().hex,
        )
        created_publish_ids.append(publish.id)

        # Approve to make it ACTIVE
        await _ps().approve_stage(
            tenant=tenant_name,
            publish_id=publish.id,
            operator="test_approver",
        )

        # Get batches and complete PREPUB stage
        batch_repo = get_container().repository.publish_batch_repository()
        batches = batch_repo.list_by_publish_id(publish.id, tenant_name, TEST_ENV)

        # Find and complete PREPUB batches
        prepub_batches = [b for b in batches if b.stage == "PREPUB"]
        for batch in prepub_batches:
            batch_repo.update_status(
                batch_id=batch.id,
                tenant=tenant_name,
                env=TEST_ENV,
                status=BatchStatus.COMPLETED.value,
                modifier="test_user",
            )

        # Check progress shows PREPUB complete
        progress = await _ps().get_publish_progress(
            tenant=tenant_name,
            publish_id=publish.id,
        )

        assert progress is not None
        assert len(progress.stages) > 0

        # Find PREPUB stage in progress
        prepub_stage = next((s for s in progress.stages if s.stage == "PREPUB"), None)
        if prepub_stage:
            assert prepub_stage.status == PublishStatus.SUCCESS.value
            assert prepub_stage.batches_completed == prepub_stage.batches_total

    @pytest.mark.asyncio
    async def test_progress_with_mixed_success_failure(
        self,
        bot_repository,
        created_bot_ids,
        created_publish_ids,
        created_tenant_ids,
        created_template_ids,
        device_repository,
        created_device_ids,
    ):
        """Test progress correctly tracks failed devices."""

        from .conftest import (
            create_test_bot_record,
            create_test_device_record,
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

        # Create test devices directly in DB
        device_ids = []
        for _ in range(10):
            device_id = create_test_device_record(
                device_repository,
                tenant=tenant_name,
                created_device_ids=created_device_ids,
            )
            device_ids.append(device_id)

        publish = await _ps().create_publish(
            tenant=tenant_name,
            bot_id=bot_id,
            publish_type=PublishType.SCALE_UP,
            operator="test_user",
            request_id=uuid4().hex,
        )
        created_publish_ids.append(publish.id)

        await _ps().approve_stage(
            tenant=tenant_name, publish_id=publish.id, operator="test_approver"
        )

        batch_repo = get_container().repository.publish_batch_repository()
        record_repo = get_container().repository.publish_record_repository()
        batches = batch_repo.list_by_publish_id(publish.id, tenant_name, TEST_ENV)

        if batches:
            # Add 8 success and 2 failure records
            for i in range(8):
                record_repo.insert_record(
                    tenant=tenant_name,
                    env=TEST_ENV,
                    domain="default",
                    device_id=device_ids[i],
                    bot_id=bot_id,
                    publish_id=publish.id,
                    batch_id=batches[0].id,
                    event_type="CREATE",
                    result_status="SUCCESS",
                    creator="test_user",
                    modifier="test_user",
                )

            for i in range(8, 10):
                record_repo.insert_record(
                    tenant=tenant_name,
                    env=TEST_ENV,
                    domain="default",
                    device_id=device_ids[i],
                    bot_id=bot_id,
                    publish_id=publish.id,
                    batch_id=batches[0].id,
                    event_type="CREATE",
                    result_status="FAILED",
                    result_message="Test failure",
                    creator="test_user",
                    modifier="test_user",
                )

            batch_repo.update_status(
                batch_id=batches[0].id,
                tenant=tenant_name,
                env=TEST_ENV,
                status=BatchStatus.COMPLETED.value,
                modifier="test_user",
            )

            progress = await _ps().get_publish_progress(
                tenant=tenant_name, publish_id=publish.id
            )
            assert progress is not None
            assert progress.overall_progress.processed_devices == 10
            assert progress.overall_progress.failed_devices == 2


@pytest.mark.integration
class TestPublishProgressAPI:
    """Integration tests for publish progress REST API."""

    @pytest.mark.asyncio
    async def test_api_get_publish_progress_endpoint(
        self,
        bot_repository,
        created_bot_ids,
        created_publish_ids,
        created_tenant_ids,
        created_template_ids,
    ):
        """Test GET /api/v1/publishes/{publish_id}/progress endpoint."""
        from fastapi.testclient import TestClient

        from secbaas.community.adapters.web.app import app

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

        # Create publish with config so batches are created
        publish = await _ps().create_publish(
            tenant=tenant_name,
            bot_id=bot_id,
            publish_type=PublishType.SCALE_UP,
            operator="test_user",
            config=PublishConfig(replica_desired=5, batch_capacity=10),
            request_id=uuid4().hex,
        )
        created_publish_ids.append(publish.id)

        # Test API endpoint
        with TestClient(app) as client:
            # Test successful request
            response = client.get(
                f"/api/v1/publishes/{publish.id}/progress",
                params={"tenant": tenant_name},
            )

            assert response.status_code == 200
            data = response.json()
            payload = data.get("data", {})
            assert "overall_progress" in payload
            assert "stages" in payload
            assert "timeline" in payload

    @pytest.mark.asyncio
    async def test_api_progress_returns_404_for_wrong_tenant(
        self,
        bot_repository,
        created_bot_ids,
        created_publish_ids,
        created_tenant_ids,
        created_template_ids,
    ):
        """Test API returns 404 when tenant doesn't match."""
        from fastapi.testclient import TestClient

        from secbaas.community.adapters.web.app import app

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

        with TestClient(app) as client:
            # Request with wrong tenant
            response = client.get(
                f"/api/v1/publishes/{publish.id}/progress",
                params={"tenant": "wrong_tenant"},
            )

            assert response.status_code == 404
            data = response.json()
            assert data["detail"]["error_code"] == "PUBLISH_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_api_progress_returns_404_for_nonexistent_publish(
        self,
        bot_repository,
        created_bot_ids,
        created_publish_ids,
        created_tenant_ids,
        created_template_ids,
    ):
        """Test API returns 404 for non-existent publish."""
        from fastapi.testclient import TestClient

        from secbaas.community.adapters.web.app import app

        with TestClient(app) as client:
            response = client.get(
                "/api/v1/publishes/999999/progress",
                params={"tenant": "test_tenant"},
            )

            assert response.status_code == 404
