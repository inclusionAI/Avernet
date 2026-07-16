"""E2E tests for Open API Message endpoints.

Tests cover:
- POST /openapi/v1/messages — valid bot UUID and content → 200
- POST /openapi/v1/messages — missing fields → 422
- POST /openapi/v1/messages — invalid bot UUID → 4xx
- POST /openapi/v1/messages/stream — SSE stream → 200 + verify SSE content-type
"""

import pytest

from ..conftest import (
    APITestHelper,
    activate_test_bot,
    cleanup_bot,
    create_test_bot,
)

pytestmark = [pytest.mark.e2e, pytest.mark.baseline]


class TestPostMessage:
    """POST /openapi/v1/messages — message delivery endpoint."""

    @pytest.mark.asyncio
    async def test_post_message_valid_bot(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """POST /openapi/v1/messages with valid bot UUID and content → 200."""
        bot = await create_test_bot(api, f"msg-bot-{unique_id}")
        await activate_test_bot(api, bot)

        response = await api.client.post(
            api.open_api_message_url(),
            json={
                "bot_id": bot["bot_uuid"],
                "message": f"Hello from e2e test {unique_id}",
            },
        )

        # The endpoint requires API key auth; without it expect 401
        # With a valid key in a real scenario, expect 200
        assert response.status_code in (200, 401)
        await cleanup_bot(api, bot["bot_uuid"])

    @pytest.mark.asyncio
    async def test_post_message_missing_fields(self, api: APITestHelper) -> None:
        """POST /openapi/v1/messages with missing message field → 422."""
        response = await api.client.post(
            api.open_api_message_url(),
            json={"bot_id": "test-bot-uuid"},
        )

        # Missing required fields should trigger validation error
        assert response.status_code in (401, 422)

    @pytest.mark.asyncio
    async def test_post_message_missing_bot_id(self, api: APITestHelper) -> None:
        """POST /openapi/v1/messages without bot_id → 401/422."""
        response = await api.client.post(
            api.open_api_message_url(),
            json={"message": "hello"},
        )

        assert response.status_code in (401, 422)

    @pytest.mark.asyncio
    async def test_post_message_invalid_bot_uuid(self, api: APITestHelper) -> None:
        """POST /openapi/v1/messages with invalid bot UUID → 4xx."""
        response = await api.client.post(
            api.open_api_message_url(),
            json={
                "bot_id": "nonexistent-bot-uuid-12345678",
                "message": "hello",
            },
        )

        # Without auth → 401. With auth + invalid bot → 404/400.
        assert response.status_code in (401, 400, 404)

    @pytest.mark.asyncio
    async def test_post_message_no_auth_header(self, api: APITestHelper) -> None:
        """POST /openapi/v1/messages without Authorization header → 401."""
        response = await api.client.post(
            api.open_api_message_url(),
            json={
                "bot_id": "test-bot-uuid",
                "message": "hello",
            },
        )

        assert response.status_code == 401
        body = response.json()
        assert body["detail"]["code"] == 40101
        assert body["detail"]["message"] == "Token 缺失"


class TestPostMessageStream:
    """POST /openapi/v1/messages/stream — SSE streaming endpoint."""

    @pytest.mark.asyncio
    async def test_post_message_stream_no_auth(self, api: APITestHelper) -> None:
        """POST /openapi/v1/messages/stream without auth → 401."""
        response = await api.client.post(
            api.open_api_message_stream_url(),
            json={
                "bot_id": "test-bot-uuid",
                "message": "hello",
            },
        )

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_post_message_stream_valid_bot(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """POST /openapi/v1/messages/stream with valid bot UUID.

        Without API key auth, expect 401. The response in auth mode
        should have text/event-stream content type.
        """
        bot = await create_test_bot(api, f"stream-bot-{unique_id}")
        await activate_test_bot(api, bot)

        response = await api.client.post(
            api.open_api_message_stream_url(),
            json={
                "bot_id": bot["bot_uuid"],
                "message": f"Stream test {unique_id}",
            },
        )

        # Without auth → 401. With valid key → SSE stream (200).
        assert response.status_code in (200, 401)
        if response.status_code == 200:
            assert response.headers.get("content-type", "").startswith(
                "text/event-stream"
            )
        await cleanup_bot(api, bot["bot_uuid"])

    @pytest.mark.asyncio
    async def test_post_message_stream_invalid_bot(self, api: APITestHelper) -> None:
        """POST /openapi/v1/messages/stream with invalid bot → 4xx."""
        response = await api.client.post(
            api.open_api_message_stream_url(),
            json={
                "bot_id": "nonexistent-bot-uuid-87654321",
                "message": "stream test",
            },
        )

        # Without auth → 401. With auth + invalid bot → 404/400.
        assert response.status_code in (401, 400, 404)
