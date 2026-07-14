"""E2E tests for Bot Runtime invoke endpoints - normal path.

Tests that an existing bot can be invoked synchronously and asynchronously.
NOTE: /api/v1/bots/{bot_uuid}/invoke route requires a running PaaS bot
and is only available when a bot is deployed and running.
"""

import pytest

from ..conftest import APITestHelper

pytestmark = [pytest.mark.e2e, pytest.mark.baseline]


class TestInvokeSync:
    """Normal-path sync invocation tests."""

    pytestmark = pytest.mark.invoke

    @pytest.mark.asyncio
    async def test_invoke_sync_accepted(self, api: APITestHelper) -> None:
        pytest.skip("Requires a running PaaS bot — /api/v1/bots/{uuid}/invoke route")

    @pytest.mark.asyncio
    async def test_invoke_sync_returns_json(self, api: APITestHelper) -> None:
        pytest.skip("Requires a running PaaS bot — /api/v1/bots/{uuid}/invoke route")


class TestInvokeAsync:
    """Normal-path async invocation tests."""

    pytestmark = pytest.mark.invoke

    @pytest.mark.asyncio
    async def test_invoke_async_accepted(self, api: APITestHelper) -> None:
        pytest.skip("Requires a running PaaS bot — /api/v1/bots/{uuid}/invoke route")

    @pytest.mark.asyncio
    async def test_invoke_async_returns_json(self, api: APITestHelper) -> None:
        pytest.skip("Requires a running PaaS bot — /api/v1/bots/{uuid}/invoke route")
