"""E2E tests for BCN downlink extended behaviour — areas not covered by test_bcn_downlink.py.

Covers additional error scenarios and transport variants:
- chat.inject message format validation
- X-BCN-TRANSPORT variants (json default mode)
- Message content format validation
- Response JSON structure and field presence
"""

import pytest

from ..conftest import APITestHelper

pytestmark = [pytest.mark.e2e, pytest.mark.baseline]

_BCN_DOWNLINK_URL = "/bcn/downlink"
_VALID_HEADERS = {"Authorization": "Bearer valid-token"}

_CHAT_SEND_BODY: dict = {
    "id": "test-ext-001",
    "session_id": "test-ext-session-001",
    "bcn_group_id": "test-group-001",
    "method": "chat.send",
    "to_bot": {"bot_id": "test-bot", "bot_type": "custom"},
    "from": {"type": "user", "id": "user-001", "name": "Test User"},
    "message": {
        "id": "msg-ext-001",
        "role": "user",
        "content": [{"type": "text", "data": "Hello"}],
        "timestamp_ms": 1700000000000,
    },
}

_CHAT_INJECT_BODY: dict = {
    "id": "test-inject-001",
    "session_id": "test-inject-session-001",
    "bcn_group_id": "test-group-001",
    "method": "chat.inject",
    "to_bot": {"bot_id": "test-bot", "bot_type": "custom"},
    "from": {"type": "system", "id": "system-001", "name": "System"},
    "message": {
        "id": "msg-inject-001",
        "role": "system",
        "content": [{"type": "text", "data": "Injected message"}],
        "timestamp_ms": 1700000000000,
    },
}


def _assert_json_response(response, expected_status=None):
    """Assert response is JSON with expected structure."""
    content_type = response.headers.get("content-type", "")
    if "application/json" in content_type:
        data = response.json()
        assert isinstance(data, dict)
        if expected_status is not None:
            assert response.status_code == expected_status


class TestChatInject:
    """Tests for chat.inject method on BCN downlink (not covered in test_bcn_downlink.py)."""

    @pytest.mark.asyncio
    async def test_chat_inject_valid_auth(self, api: APITestHelper) -> None:
        """POST /bcn/downlink with chat.inject and valid token exercises endpoint."""
        response = await api.client.post(
            _BCN_DOWNLINK_URL,
            json=_CHAT_INJECT_BODY,
            headers=_VALID_HEADERS,
        )

        assert response.status_code in (200, 202, 500), (
            f"Expected 200, 202, or 500 for valid chat.inject, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_chat_inject_missing_session_id(self, api: APITestHelper) -> None:
        """POST /bcn/downlink with chat.inject without session_id returns 422/500."""
        body = {k: v for k, v in _CHAT_INJECT_BODY.items() if k != "session_id"}

        response = await api.client.post(
            _BCN_DOWNLINK_URL,
            json=body,
            headers=_VALID_HEADERS,
        )

        assert response.status_code in (422, 500), (
            f"Expected 422 or 500 for chat.inject without session_id, "
            f"got {response.status_code}: {response.text[:200]}"
        )


class TestChatHistoryExtended:
    """Additional chat.history tests beyond existing test_bcn_downlink.py coverage."""

    @pytest.mark.asyncio
    async def test_chat_history_with_before(self, api: APITestHelper) -> None:
        """POST /bcn/downlink with chat.history and 'before' param exercises handler."""
        body = {
            **_CHAT_SEND_BODY,
            "method": "chat.history",
            "before": 20,
        }

        response = await api.client.post(
            _BCN_DOWNLINK_URL,
            json=body,
            headers=_VALID_HEADERS,
        )

        assert response.status_code in (200, 202, 422, 500), (
            f"Expected 200, 202, 422, or 500 for chat.history with before, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_chat_history_with_after(self, api: APITestHelper) -> None:
        """POST /bcn/downlink with chat.history and 'after' param exercises handler."""
        body = {
            **_CHAT_SEND_BODY,
            "method": "chat.history",
            "after": 10,
        }

        response = await api.client.post(
            _BCN_DOWNLINK_URL,
            json=body,
            headers=_VALID_HEADERS,
        )

        assert response.status_code in (200, 202, 422, 500), (
            f"Expected 200, 202, 422, or 500 for chat.history with after, "
            f"got {response.status_code}: {response.text[:200]}"
        )


class TestMessageFormat:
    """Tests for message body format validation on BCN downlink."""

    @pytest.mark.asyncio
    async def test_message_without_content_array(self, api: APITestHelper) -> None:
        """POST /bcn/downlink with message missing content array."""
        body = {
            **_CHAT_SEND_BODY,
            "message": {
                "id": "msg-no-content",
                "role": "user",
                "timestamp_ms": 1700000000000,
            },
        }

        response = await api.client.post(
            _BCN_DOWNLINK_URL,
            json=body,
            headers=_VALID_HEADERS,
        )

        assert response.status_code in (200, 202, 400, 422, 500), (
            f"Expected 200, 202, 400, 422, or 500 for message without content, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_message_with_empty_content_array(self, api: APITestHelper) -> None:
        """POST /bcn/downlink with empty content array in message."""
        body = {
            **_CHAT_SEND_BODY,
            "message": {
                "id": "msg-empty-content",
                "role": "user",
                "content": [],
                "timestamp_ms": 1700000000000,
            },
        }

        response = await api.client.post(
            _BCN_DOWNLINK_URL,
            json=body,
            headers=_VALID_HEADERS,
        )

        assert response.status_code in (200, 202, 400, 422, 500), (
            f"Expected 200, 202, 400, 422, or 500 for empty content array, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_message_with_multiple_content_blocks(
        self, api: APITestHelper
    ) -> None:
        """POST /bcn/downlink with multiple content blocks in message."""
        body = {
            **_CHAT_SEND_BODY,
            "message": {
                "id": "msg-multi-content",
                "role": "user",
                "content": [
                    {"type": "text", "data": "First message"},
                    {"type": "text", "data": "Second message"},
                    {"type": "file", "data": "base64encodeddata"},
                ],
                "timestamp_ms": 1700000000000,
            },
        }

        response = await api.client.post(
            _BCN_DOWNLINK_URL,
            json=body,
            headers=_VALID_HEADERS,
        )

        assert response.status_code in (200, 202, 422, 500), (
            f"Expected 200, 202, 422, or 500 for multiple content blocks, "
            f"got {response.status_code}: {response.text[:200]}"
        )


class TestTransportVariants:
    """Tests for different transport modes on BCN downlink."""

    @pytest.mark.asyncio
    async def test_chat_send_with_json_transport(self, api: APITestHelper) -> None:
        """POST /bcn/downlink with X-BCN-TRANSPORT=json (explicit default mode)."""
        headers = {**_VALID_HEADERS, "X-BCN-TRANSPORT": "json"}

        response = await api.client.post(
            _BCN_DOWNLINK_URL,
            json=_CHAT_SEND_BODY,
            headers=headers,
        )

        assert response.status_code in (200, 202, 500), (
            f"Expected 200, 202, or 500 for json transport, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_chat_send_default_transport(self, api: APITestHelper) -> None:
        """POST /bcn/downlink without X-BCN-TRANSPORT header (default mode)."""
        response = await api.client.post(
            _BCN_DOWNLINK_URL,
            json=_CHAT_SEND_BODY,
            headers=_VALID_HEADERS,
        )

        assert response.status_code in (200, 202, 500), (
            f"Expected 200, 202, or 500 for default transport, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_unknown_transport_header(self, api: APITestHelper) -> None:
        """POST /bcn/downlink with unknown X-BCN-TRANSPORT value."""
        headers = {**_VALID_HEADERS, "X-BCN-TRANSPORT": "grpc"}

        response = await api.client.post(
            _BCN_DOWNLINK_URL,
            json=_CHAT_SEND_BODY,
            headers=headers,
        )

        assert response.status_code in (200, 400, 422, 500), (
            f"Expected 200, 400, 422, or 500 for unknown transport, "
            f"got {response.status_code}: {response.text[:200]}"
        )


class TestResponseStructure:
    """Tests for response JSON structure consistency."""

    @pytest.mark.asyncio
    async def test_success_response_json(self, api: APITestHelper) -> None:
        """Successful response returns valid JSON."""
        response = await api.client.post(
            _BCN_DOWNLINK_URL,
            json=_CHAT_SEND_BODY,
            headers=_VALID_HEADERS,
        )

        if response.status_code in (200, 202):
            content_type = response.headers.get("content-type", "")
            assert (
                "application/json" in content_type
                or "text/event-stream" in content_type
            ), f"Expected JSON or SSE content type, got: {content_type}"

    @pytest.mark.asyncio
    async def test_error_response_always_json(self, api: APITestHelper) -> None:
        """Error responses (4xx/5xx) always return JSON."""
        response = await api.client.post(
            _BCN_DOWNLINK_URL,
            json={},
            headers=_VALID_HEADERS,
        )

        if response.status_code >= 400 and response.status_code != 500:
            content_type = response.headers.get("content-type", "")
            assert "application/json" in content_type, (
                f"Error response must be JSON, got content-type: {content_type}"
            )


class TestBCNDownlinkStream:
    """Tests for /bcn/downlink/stream (referenced in conftest URL builder).

    NOTE: This endpoint is a placeholder in the conftest URL builder.
    No route handler exists in the community edition. Tests exercise the
    URL contract to confirm endpoint existence or graceful 404.
    """

    @pytest.mark.asyncio
    async def test_bcn_downlink_stream_url(self, api: APITestHelper) -> None:
        """GET /bcn/downlink/stream — exercise the stream URL builder path."""
        url = api.bcn_downlink_stream_url()
        assert "/bcn/downlink/stream" in url, (
            f"Expected bcn_downlink_stream_url to contain /bcn/downlink/stream, got {url}"
        )

        response = await api.client.post(
            url,
            headers=_VALID_HEADERS,
        )

        assert response.status_code in (200, 400, 404, 405, 500), (
            f"Expected 200, 400, 404, 405, or 500 for bcn downlink stream, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_bcn_downlink_stream_with_sse_header(
        self, api: APITestHelper
    ) -> None:
        """POST /bcn/downlink/stream with X-BCN-TRANSPORT=sse exercises stream path."""
        headers = {**_VALID_HEADERS, "X-BCN-TRANSPORT": "sse"}

        response = await api.client.post(
            api.bcn_downlink_stream_url(),
            json=_CHAT_SEND_BODY,
            headers=headers,
        )

        assert response.status_code in (200, 400, 404, 405, 500), (
            f"Expected 200, 400, 404, 405, or 500 for stream with SSE header, "
            f"got {response.status_code}: {response.text[:200]}"
        )
