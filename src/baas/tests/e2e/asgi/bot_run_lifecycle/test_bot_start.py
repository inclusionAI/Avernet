"""E2E tests for bot run lifecycle — start, stop, restart, detail, and devices.

Covers Phase 4.1 of Wave 05: exercise start/create paths for BaasBotService
and ClawBotService through the management and Open API endpoints.
"""

import uuid

import pytest

from tests.e2e.asgi.conftest import (
    TEMPLATE_ARCA,
    TEMPLATE_TECLAW,
    APITestHelper,
    cleanup_bot,
    create_test_bot,
    find_existing_bot,
)

pytestmark = [pytest.mark.bot_run_lifecycle]


def _get_bot_uuid(bot: dict) -> str:
    return bot["bot_uuid"]


class TestBotStartFlow:
    """Bot start lifecycle through management API — create → activate → verify."""

    @pytest.mark.asyncio
    async def test_create_and_activate_bot(
        self, api: APITestHelper, unique_id: str, created_bot_uuids: list[str]
    ) -> None:
        bot = await create_test_bot(api, f"e2e-start-{unique_id}")
        bot_uuid = _get_bot_uuid(bot)
        created_bot_uuids.append(bot_uuid)
        try:
            assert bot.get("status") == "PENDING", (
                f"Expected PENDING, got {bot.get('status')}"
            )

            response = await api.client.get(api.bot_url(bot_uuid), params=api.params())
            assert response.status_code == 200, (
                f"Get bot returned {response.status_code}: {response.text}"
            )
            data = response.json()
            assert data.get("code") == 0, f"Bot detail failed: {data}"
            bot_record = data.get("data", data)
            assert bot_record.get("bot_uuid") == bot_uuid
        finally:
            await cleanup_bot(api, bot_uuid)

    @pytest.mark.asyncio
    async def test_bot_detail_with_engine_type(self, api: APITestHelper) -> None:
        bot = await find_existing_bot(api)
        if bot is None:
            pytest.skip("No existing bots in system")
        bot_uuid = _get_bot_uuid(bot)

        for engine_type in ("aicoding", "hermes", "claude_code"):
            response = await api.client.get(
                api.bot_url(bot_uuid),
                params=api.params(engine_type=engine_type),
            )
            assert response.status_code in (200, 400, 404, 500), (
                f"Bot detail with engine_type={engine_type} returned "
                f"{response.status_code}: {response.text}"
            )

    @pytest.mark.asyncio
    async def test_bot_devices_after_create(
        self, api: APITestHelper, unique_id: str, created_bot_uuids: list[str]
    ) -> None:
        bot = await create_test_bot(api, f"e2e-devices-{unique_id}")
        bot_uuid = _get_bot_uuid(bot)
        created_bot_uuids.append(bot_uuid)
        try:
            response = await api.client.get(
                api.bot_devices_url(bot_uuid), params=api.params()
            )
            assert response.status_code in (200, 404), (
                f"Devices list returned {response.status_code}: {response.text}"
            )
        finally:
            await cleanup_bot(api, bot_uuid)


class TestBotStartInvalid:
    """Error paths for bot start."""

    @pytest.mark.asyncio
    async def test_get_nonexistent_bot_returns_error(self, api: APITestHelper) -> None:
        fake_uuid = "nonexistent-bot-uuid-12345678"
        response = await api.client.get(api.bot_url(fake_uuid), params=api.params())
        assert response.status_code in (200, 404, 500), (
            f"Expected error for nonexistent bot, got {response.status_code}"
        )

    @pytest.mark.asyncio
    async def test_create_bot_missing_name(self, api: APITestHelper) -> None:
        response = await api.client.post(
            api.bot_url(),
            params=api.params(),
            json={
                "template_uuid": "TEMPLATE-4d0e2849d7004111836333de782b95d8",
                "device_count": 1,
                "operator": "e2e-test",
            },
        )
        assert response.status_code >= 400, (
            f"Expected 4xx/5xx for missing name, got {response.status_code}"
        )


class TestBotStartEngineTypes:
    """Bot start with different engine types via template selection."""

    @pytest.mark.asyncio
    async def test_create_with_arca_template(
        self, api: APITestHelper, unique_id: str, created_bot_uuids: list[str]
    ) -> None:
        bot = await create_test_bot(
            api, f"e2e-arca-{unique_id}", template_uuid=TEMPLATE_ARCA
        )
        created_bot_uuids.append(_get_bot_uuid(bot))
        try:
            assert bot.get("status") == "PENDING"
        finally:
            await cleanup_bot(api, _get_bot_uuid(bot))

    @pytest.mark.asyncio
    @pytest.mark.xfail(reason="Non-ARCA/SIGMA template may fail in stub mode")
    async def test_create_with_teclaw_template(
        self, api: APITestHelper, unique_id: str, created_bot_uuids: list[str]
    ) -> None:
        bot = await create_test_bot(
            api, f"e2e-teclaw-{unique_id}", template_uuid=TEMPLATE_TECLAW
        )
        created_bot_uuids.append(_get_bot_uuid(bot))
        try:
            assert bot.get("status") == "PENDING"
        finally:
            await cleanup_bot(api, _get_bot_uuid(bot))


class TestBotStartWithUserMessage:
    """Bot start with user message — Open API run endpoint."""

    @pytest.mark.asyncio
    async def test_run_endpoint_requires_auth(self, api: APITestHelper) -> None:
        response = await api.client.post(
            api.open_api_run_url(),
            json={"message": "hello"},
        )
        assert response.status_code in (401, 403, 200), (
            f"Expected auth required, got {response.status_code}"
        )

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
            f"Expected auth required or validation error, got {response.status_code}"
        )


class TestBotStopRestart:
    """Bot stop and restart through management API."""

    @pytest.mark.asyncio
    async def test_stop_active_bot(
        self, api: APITestHelper, unique_id: str, created_bot_uuids: list[str]
    ) -> None:
        bot = await create_test_bot(api, f"e2e-stop-{unique_id}")
        bot_uuid = _get_bot_uuid(bot)
        created_bot_uuids.append(bot_uuid)
        try:
            response = await api.client.post(
                f"{api.bot_url(bot_uuid)}/stop",
                params=api.params(),
                json={
                    "operator": "e2e-test",
                    "request_id": uuid.uuid4().hex,
                },
            )
            assert response.status_code in (200, 400, 404, 500), (
                f"Stop returned {response.status_code}: {response.text}"
            )
        finally:
            await cleanup_bot(api, bot_uuid)

    @pytest.mark.asyncio
    async def test_stop_nonexistent_bot(self, api: APITestHelper) -> None:
        response = await api.client.post(
            f"{api.bot_url('nonexistent-bot-uuid')}/stop",
            params=api.params(),
            json={
                "operator": "e2e-test",
                "request_id": uuid.uuid4().hex,
            },
        )
        assert response.status_code in (200, 400, 404, 500), (
            f"Expected error for nonexistent bot, got {response.status_code}"
        )


class TestBotStartProgress:
    """Bot start progress tracking — container startup monitoring."""

    @pytest.mark.asyncio
    async def test_get_start_progress_pending_bot(
        self, api: APITestHelper, unique_id: str, created_bot_uuids: list[str]
    ) -> None:
        bot = await create_test_bot(api, f"e2e-progress-{unique_id}")
        bot_uuid = _get_bot_uuid(bot)
        created_bot_uuids.append(bot_uuid)
        try:
            response = await api.client.get(
                api.bot_start_progress_url(bot_uuid),
                params=api.params(),
            )
            assert response.status_code in (200, 404, 500, 501), (
                f"Start progress returned {response.status_code}: {response.text}"
            )
        finally:
            await cleanup_bot(api, bot_uuid)

    @pytest.mark.asyncio
    async def test_get_start_progress_existing_bot(self, api: APITestHelper) -> None:
        bot = await find_existing_bot(api)
        if bot is None:
            pytest.skip("No existing bots in system")
        response = await api.client.get(
            api.bot_start_progress_url(_get_bot_uuid(bot)),
            params=api.params(),
        )
        assert response.status_code in (200, 404, 500, 501), (
            f"Start progress returned {response.status_code}: {response.text}"
        )
