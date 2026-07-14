"""E2E tests for Bot Runtime invoke endpoints - error paths.

Tests that invalid requests return appropriate error responses.
NOTE: /api/v1/bots/{bot_uuid}/invoke route requires a running PaaS bot
and is only available when a bot is deployed and running.
"""

import pytest

from ..conftest import APITestHelper

pytestmark = [pytest.mark.e2e, pytest.mark.baseline]

NONEXISTENT_UUID = "00000000-0000-0000-0000-000000000000"


class TestInvokeNonExistent:
    """Error-handling tests for non-existent bot UUID."""

    pytestmark = pytest.mark.invoke

    @pytest.mark.asyncio
    async def test_invoke_nonexistent_bot(self, api: APITestHelper) -> None:
        pytest.skip("Requires a running PaaS bot — /api/v1/bots/{uuid}/invoke route")


class TestInvokeMissingBody:
    """Error-handling tests for missing or empty request body."""

    pytestmark = pytest.mark.invoke

    @pytest.mark.asyncio
    async def test_invoke_empty_body(self, api: APITestHelper) -> None:
        pytest.skip("Requires a running PaaS bot — /api/v1/bots/{uuid}/invoke route")

    @pytest.mark.asyncio
    async def test_invoke_no_body(self, api: APITestHelper) -> None:
        pytest.skip("Requires a running PaaS bot — /api/v1/bots/{uuid}/invoke route")


class TestInvokeInvalidPayload:
    """Error-handling tests for malformed or invalid payload."""

    pytestmark = pytest.mark.invoke

    @pytest.mark.asyncio
    async def test_invoke_malformed_json(self, api: APITestHelper) -> None:
        pytest.skip("Requires a running PaaS bot — /api/v1/bots/{uuid}/invoke route")


class TestInvokeNoSessionId:
    """Error-handling tests for missing session_id field."""

    pytestmark = pytest.mark.invoke

    @pytest.mark.asyncio
    async def test_invoke_missing_session_id(self, api: APITestHelper) -> None:
        pytest.skip("Requires a running PaaS bot — /api/v1/bots/{uuid}/invoke route")
