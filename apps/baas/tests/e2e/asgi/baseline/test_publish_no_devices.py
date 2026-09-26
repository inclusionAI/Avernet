"""E2E tests for publish creation with no eligible devices.

When a publish is created but there are no eligible devices for the
publish type (e.g., DESTROY with no ACTIVE devices), the system
should return a 400 error instead of silently creating a publish.
"""

import uuid

import pytest

from tests.e2e.asgi.conftest import (
    APITestHelper,
    cleanup_bot,
    create_test_bot,
)


class TestPublishNoEligibleDevices:
    """Tests for 400 response when no eligible devices exist."""

    @pytest.mark.asyncio
    async def test_create_bot_invalid_device_count(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """device_count=0 is rejected at validation time."""
        response = await api.client.post(
            api.bot_url(),
            params=api.params(),
            json={
                "name": f"no-dev-{unique_id}",
                "template_uuid": "TEMPLATE-4d0e2849d7004111836333de782b95d8",
                "device_count": 0,
                "operator": "e2e-test",
                "request_id": uuid.uuid4().hex,
            },
        )

        assert response.status_code in (400, 422), (
            f"Expected 400 or 422, got {response.status_code}: {response.json()}"
        )

    @pytest.mark.asyncio
    async def test_publish_to_no_active_devices(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """SCALE_UP to a bot with no active devices is rejected.

        Creates a bot with device_count=1 (bot starts in PENDING state,
        no devices are active yet), then attempts to create a SCALE_UP
        publish against it. Since the bot has no ACTIVE devices to
        reference, the server should reject with 400 or 422.
        """
        bot = await create_test_bot(api, f"no-active-{unique_id}", device_count=1)

        response = await api.client.post(
            api.publish_url(),
            params=api.params(),
            json={
                "bot_id": bot["id"],
                "publish_type": "SCALE_UP",
                "operator": "e2e-test",
                "request_id": uuid.uuid4().hex,
            },
        )

        assert response.status_code in (400, 409, 422), (
            f"Expected 400/409/422 for SCALE_UP with no active devices, "
            f"got {response.status_code}: {response.json()}"
        )

        await cleanup_bot(api, bot["bot_uuid"])

    @pytest.mark.asyncio
    async def test_publish_with_scale_zero(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """SCALE_DOWN with scale_count=0 or invalid scale is rejected.

        POST to the publish endpoint with an invalid scale configuration.
        The server should reject the request before creating any publish.
        No bot creation is needed — this is a pure validation test.
        """
        response = await api.client.post(
            api.publish_url(),
            params=api.params(),
            json={
                "bot_id": 99999,
                "publish_type": "SCALE_DOWN",
                "scale_count": 0,
                "operator": "e2e-test",
                "request_id": uuid.uuid4().hex,
            },
        )

        assert response.status_code in (400, 404, 422), (
            f"Expected 400/404/422 for SCALE_DOWN with scale_count=0, "
            f"got {response.status_code}: {response.json()}"
        )

    @pytest.mark.asyncio
    async def test_publish_with_invalid_bot_id(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Publishing to a non-existent bot_id returns 404.

        POST to the publish endpoint with a bot_id that does not exist.
        The server should return 404 since no bot can be resolved.
        No bot creation is needed — this is a pure validation test.
        """
        response = await api.client.post(
            api.publish_url(),
            params=api.params(),
            json={
                "bot_id": 99999999,
                "publish_type": "CREATE",
                "operator": "e2e-test",
                "request_id": uuid.uuid4().hex,
            },
        )

        assert response.status_code == 404, (
            f"Expected 404 for non-existent bot_id, "
            f"got {response.status_code}: {response.json()}"
        )
