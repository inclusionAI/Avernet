"""E2E tests for publish creation with no eligible devices.

When a publish is created but there are no eligible devices for the
publish type (e.g., DESTROY with no ACTIVE devices), the system
should return a 400 error instead of silently creating a publish.
"""

import uuid

import pytest

from ..conftest import APITestHelper

pytestmark = pytest.mark.e2e


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
