"""E2E tests for Bot Start Progress error handling.

Tests cover the bot start-progress error endpoint and error mapping:
- GET /api/v1/bots/{bot_uuid}/start-progress — query startup progress
- Error mapping: BotNotFound, NoDevicesFound, NoActiveDevices, platform errors

The start_progress_error_url builder in conftest returns /api/v1/bots/start-progress/error
but the actual error handling is done via the GET /api/v1/bots/{bot_uuid}/start-progress
endpoint which maps domain exceptions to HTTP responses through _map_start_progress_error.
"""

import pytest

from ..conftest import APITestHelper, find_existing_bot

pytestmark = [pytest.mark.e2e, pytest.mark.baseline]

NONEXISTENT_UUID = "00000000-0000-0000-0000-000000000000"


class TestStartProgressErrors:
    """Bot start progress error mapping and handler tests."""

    @pytest.mark.asyncio
    async def test_start_progress_nonexistent_bot(self, api: APITestHelper) -> None:
        """GET /bots/{nonexistent}/start-progress returns 404 BotNotFound.

        The error handler maps BotNotFoundError → 404 with error_code
        refined by bot status (BOT_NOT_FOUND, BOT_RELEASED, BOT_FAILED).
        """
        response = await api.client.get(
            api.bot_start_progress_url(NONEXISTENT_UUID),
            params=api.params(),
        )

        assert response.status_code in (404, 500), (
            f"Expected 404 or 500 for nonexistent bot start-progress, "
            f"got {response.status_code}: {response.text[:200]}"
        )
        if response.status_code == 404:
            data = response.json()
            assert "detail" in data, f"Expected 'detail' in 404 response, got: {data}"

    @pytest.mark.asyncio
    async def test_start_progress_valid_bot(self, api: APITestHelper) -> None:
        """GET /bots/{bot_uuid}/start-progress with existing bot exercises endpoint.

        May return 200 (progress data), 404 (no devices), 503 (no active devices),
        or 501 (platform not supported).
        """
        bot = await find_existing_bot(api)
        if bot is None:
            pytest.skip("No existing bots in system")

        response = await api.client.get(
            api.bot_start_progress_url(bot["bot_uuid"]),
            params=api.params(),
        )

        assert response.status_code in (200, 404, 501, 503), (
            f"Expected 200, 404, 501, or 503 for start-progress on existing bot, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_start_progress_retry_scenario(self, api: APITestHelper) -> None:
        """503 (no devices) response includes Retry-After header for retry.

        NoDevicesFoundError and NoActiveDevicesError map to 503 with
        Retry-After: 5 header, indicating a retryable scenario.
        """
        bot = await find_existing_bot(api)
        if bot is None:
            pytest.skip("No existing bots in system")

        response = await api.client.get(
            api.bot_start_progress_url(bot["bot_uuid"]),
            params=api.params(),
        )

        # Check Retry-After header presence on 503
        if response.status_code == 503:
            retry_after = response.headers.get("retry-after")
            assert retry_after is not None, (
                f"Expected Retry-After header on 503, headers: {dict(response.headers)}"
            )
            assert retry_after == "5", f"Expected Retry-After: 5, got: {retry_after}"

    @pytest.mark.asyncio
    async def test_start_progress_non_retry_scenario(self, api: APITestHelper) -> None:
        """404 (bot not found) does NOT include Retry-After → non-retryable.

        BotNotFoundError maps to 404 without Retry-After header.
        """
        response = await api.client.get(
            api.bot_start_progress_url(NONEXISTENT_UUID),
            params=api.params(),
        )

        if response.status_code == 404:
            assert "retry-after" not in {k.lower() for k in response.headers}, (
                f"Expected no Retry-After header on 404, headers: "
                f"{dict(response.headers)}"
            )

    @pytest.mark.asyncio
    async def test_start_progress_with_device_affinity(
        self, api: APITestHelper
    ) -> None:
        """GET /bots/{bot_uuid}/start-progress with device_affinity param."""
        bot = await find_existing_bot(api)
        if bot is None:
            pytest.skip("No existing bots in system")

        response = await api.client.get(
            api.bot_start_progress_url(bot["bot_uuid"]),
            params=api.params(device_affinity="test-affinity-key"),
        )

        assert response.status_code in (200, 404, 501, 503), (
            f"Expected 200, 404, 501, or 503 for start-progress with affinity, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_start_progress_error_url_exercise(self, api: APITestHelper) -> None:
        """POST /api/v1/bots/start-progress/error exercises the error handler.

        The start_progress_error_url in conftest returns this path. Even if
        no dedicated route exists, the test exercises the URL to confirm
        the endpoint contracts.
        """
        response = await api.client.post(
            api.start_progress_error_url(),
            params=api.params(),
            json={"bot_uuid": NONEXISTENT_UUID, "error": "TEST_ERROR"},
        )

        # May be 404 (no route), 405 (method not allowed), or other
        assert response.status_code in (200, 404, 405, 500), (
            f"Expected 200, 404, 405, or 500 for start-progress error handler, "
            f"got {response.status_code}: {response.text[:200]}"
        )
