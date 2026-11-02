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
class TestPublishServiceRetryAPI:
    """Integration tests for retry publish REST API endpoint."""

    @pytest.mark.asyncio
    async def test_api_retry_publish_endpoint(
        self,
        bot_repository,
        created_bot_ids,
        created_publish_ids,
        created_tenant_ids,
        created_template_ids,
    ):
        """Test POST /api/v1/publishes/{publish_id}/retry endpoint."""
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

        # Create and fail a publish
        original = await _ps().create_publish(
            tenant=tenant_name,
            bot_id=bot_id,
            publish_type=PublishType.SCALE_UP,
            operator="test_user",
            request_id=uuid4().hex,
        )
        created_publish_ids.append(original.id)

        repo = get_container().repository.publish_repository()
        repo.update_status(
            publish_id=original.id,
            tenant=tenant_name,
            env=TEST_ENV,
            status=PublishStatus.FAILED.value,
            modifier="test_user",
        )

        with TestClient(app) as client:
            response = client.post(
                f"/api/v1/publishes/{original.id}/retry",
                params={"tenant": tenant_name},
                json={
                    "operator": "test_user",
                    "request_id": uuid4().hex,
                },
            )

            assert response.status_code == 200
            data = response.json()
            assert data["data"]["status"] == "PENDING"
            assert data["data"]["bot_id"] == bot_id

            # Track the retry publish for cleanup
            created_publish_ids.append(data["data"]["id"])

    @pytest.mark.asyncio
    async def test_api_retry_non_failed_returns_error(
        self,
        bot_repository,
        created_bot_ids,
        created_publish_ids,
        created_tenant_ids,
        created_template_ids,
    ):
        """Test retry on non-FAILED state returns 400 error."""
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

        # Create PENDING publish
        publish = await _ps().create_publish(
            tenant=tenant_name,
            bot_id=bot_id,
            publish_type=PublishType.SCALE_UP,
            operator="test_user",
            request_id=uuid4().hex,
        )
        created_publish_ids.append(publish.id)

        with TestClient(app) as client:
            response = client.post(
                f"/api/v1/publishes/{publish.id}/retry",
                params={"tenant": tenant_name},
                json={
                    "operator": "test_user",
                    "request_id": uuid4().hex,
                },
            )

            assert response.status_code == 400
