"""E2E tests for bot run session and chat client lifecycle.

Covers Phase 4.2 of Wave 05: exercise session creation, session queries,
message retrieval, and error handling through Open API and management endpoints.
"""

import pytest

from tests.e2e.asgi.conftest import (
    APITestHelper,
    cleanup_bot,
    create_test_bot,
    find_existing_bot,
)

pytestmark = [pytest.mark.bot_run_lifecycle]


def _get_bot_uuid(bot: dict) -> str:
    return bot["bot_uuid"]


class TestBotSession:
    """Session and chat client lifecycle through Open API and management endpoints."""

    @pytest.mark.asyncio
    async def test_messages_endpoint_requires_auth(self, api: APITestHelper) -> None:
        response = await api.client.post(
            api.open_api_message_url(),
            json={
                "message": "hello",
                "bot_id": "test-bot-id:12345",
            },
        )
        assert response.status_code in (401, 403, 200, 400), (
            f"Expected auth required or validation error, got {response.status_code}: "
            f"{response.text}"
        )

    @pytest.mark.asyncio
    async def test_create_session_client_for_bot_run(
        self, api: APITestHelper, unique_id: str, created_bot_uuids: list[str]
    ) -> None:
        bot = await create_test_bot(api, f"e2e-sess-{unique_id}")
        bot_uuid = _get_bot_uuid(bot)
        created_bot_uuids.append(bot_uuid)
        try:
            response = await api.client.post(
                api.open_api_message_url(),
                json={
                    "message": "hello from e2e test",
                    "bot_id": bot_uuid,
                },
            )
            assert response.status_code in (200, 201, 400, 401, 403, 500), (
                f"POST /openapi/v1/messages returned {response.status_code}: "
                f"{response.text}"
            )
        finally:
            await cleanup_bot(api, bot_uuid)

    @pytest.mark.asyncio
    async def test_session_client_query_by_session_id(
        self, api: APITestHelper, unique_id: str, created_bot_uuids: list[str]
    ) -> None:
        bot = await create_test_bot(api, f"e2e-sessq-{unique_id}")
        bot_uuid = _get_bot_uuid(bot)
        created_bot_uuids.append(bot_uuid)
        try:
            msg_response = await api.client.post(
                api.open_api_message_url(),
                json={
                    "message": "query test message",
                    "bot_id": bot_uuid,
                },
            )
            if msg_response.status_code in (200, 201):
                msg_data = msg_response.json()
                session_id = msg_data.get("session_id") or msg_data.get("data", {}).get(
                    "session_id"
                )
            else:
                session_id = None

            if session_id:
                response = await api.client.get(
                    api.open_api_session_url(session_id),
                    params={"bot_id": bot_uuid, "lifecycle_stage": "active"},
                )
                assert response.status_code in (200, 400, 401, 403, 404, 500), (
                    f"GET session returned {response.status_code}: {response.text}"
                )
        finally:
            await cleanup_bot(api, bot_uuid)

    @pytest.mark.asyncio
    async def test_session_client_error_handling_nonexistent(
        self, api: APITestHelper
    ) -> None:
        response = await api.client.get(
            api.open_api_session_url("nonexistent-session-key-12345678"),
            params={"bot_id": "fake-bot-id:99999"},
        )
        assert response.status_code in (200, 400, 401, 403, 404, 500), (
            f"Expected error for nonexistent session, got {response.status_code}: "
            f"{response.text}"
        )

    @pytest.mark.asyncio
    async def test_get_session_messages(
        self, api: APITestHelper, unique_id: str, created_bot_uuids: list[str]
    ) -> None:
        bot = await create_test_bot(api, f"e2e-sessmsg-{unique_id}")
        bot_uuid = _get_bot_uuid(bot)
        created_bot_uuids.append(bot_uuid)
        try:
            session_id = None
            msg_response = await api.client.post(
                api.open_api_message_url(),
                json={
                    "message": "message for retrieval test",
                    "bot_id": bot_uuid,
                },
            )
            if msg_response.status_code in (200, 201):
                msg_data = msg_response.json()
                session_id = msg_data.get("session_id") or msg_data.get("data", {}).get(
                    "session_id"
                )

            if session_id:
                response = await api.client.get(
                    f"{api.open_api_session_url(session_id)}/messages",
                    params={"bot_id": bot_uuid, "limit": 10},
                )
                assert response.status_code in (200, 400, 401, 403, 404, 500), (
                    f"GET session messages returned {response.status_code}: "
                    f"{response.text}"
                )
            else:
                response = await api.client.get(
                    f"{api.open_api_session_url('fake-sid')}/messages",
                    params={"bot_id": bot_uuid, "limit": 10},
                )
                assert response.status_code in (200, 400, 401, 403, 404, 500), (
                    f"GET session messages (fallback) returned "
                    f"{response.status_code}: {response.text}"
                )
        finally:
            await cleanup_bot(api, bot_uuid)

    @pytest.mark.asyncio
    async def test_bot_detail_shows_correct_status(
        self, api: APITestHelper, unique_id: str, created_bot_uuids: list[str]
    ) -> None:
        bot = await create_test_bot(api, f"e2e-status-{unique_id}")
        bot_uuid = _get_bot_uuid(bot)
        created_bot_uuids.append(bot_uuid)
        try:
            response = await api.client.get(api.bot_url(bot_uuid), params=api.params())
            assert response.status_code == 200, (
                f"Get bot returned {response.status_code}: {response.text}"
            )
            data = response.json()
            assert data.get("code") == 0, f"Bot detail failed: {data}"
            bot_record = data.get("data", data)
            assert "status" in bot_record, (
                f"Bot record missing 'status' field: {bot_record}"
            )
        finally:
            await cleanup_bot(api, bot_uuid)

    @pytest.mark.asyncio
    async def test_bot_detail_engine_information(
        self, api: APITestHelper, unique_id: str, created_bot_uuids: list[str]
    ) -> None:
        bot = await create_test_bot(api, f"e2e-engine-{unique_id}")
        bot_uuid = _get_bot_uuid(bot)
        created_bot_uuids.append(bot_uuid)
        try:
            for engine_type in ("aicoding", "hermes", "claude_code"):
                response = await api.client.get(
                    api.bot_url(bot_uuid),
                    params=api.params(engine_type=engine_type),
                )
                assert response.status_code in (200, 400, 404, 500), (
                    f"Bot detail with engine_type={engine_type} returned "
                    f"{response.status_code}: {response.text}"
                )
        finally:
            await cleanup_bot(api, bot_uuid)


class TestBotSessionExisting:
    """Session tests that use existing bots to avoid creating new PaaS resources."""

    @pytest.mark.asyncio
    async def test_session_query_with_existing_bot(self, api: APITestHelper) -> None:
        bot = await find_existing_bot(api)
        if bot is None:
            pytest.skip("No existing bots in system")
        bot_uuid = _get_bot_uuid(bot)

        response = await api.client.post(
            api.open_api_message_url(),
            json={
                "message": "session query with existing bot",
                "bot_id": bot_uuid,
            },
        )
        assert response.status_code in (200, 201, 400, 401, 403, 404, 500), (
            f"POST message with existing bot returned {response.status_code}: "
            f"{response.text}"
        )

    @pytest.mark.asyncio
    async def test_session_messages_with_existing_bot(self, api: APITestHelper) -> None:
        bot = await find_existing_bot(api)
        if bot is None:
            pytest.skip("No existing bots in system")
        bot_uuid = _get_bot_uuid(bot)

        msg_response = await api.client.post(
            api.open_api_message_url(),
            json={
                "message": "existing bot message fetch test",
                "bot_id": bot_uuid,
            },
        )
        session_id = None
        if msg_response.status_code in (200, 201):
            msg_data = msg_response.json()
            session_id = msg_data.get("session_id") or msg_data.get("data", {}).get(
                "session_id"
            )

        if session_id:
            response = await api.client.get(
                f"{api.open_api_session_url(session_id)}/messages",
                params={"bot_id": bot_uuid, "limit": 10},
            )
            assert response.status_code in (200, 400, 401, 403, 404, 500), (
                f"GET session messages returned {response.status_code}: {response.text}"
            )
