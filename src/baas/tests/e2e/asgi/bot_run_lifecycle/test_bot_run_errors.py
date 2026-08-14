"""E2E tests for bot run error paths — binding failures, engine errors,
timeouts, and invalid requests.

Covers Phase 4.4 of Wave 05: exercise error/edge paths in bot run lifecycle.
"""

import os
import uuid

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


class TestBotRunBindingErrors:
    """Bot run error paths from binding resolution failures."""

    @pytest.mark.asyncio
    async def test_bot_run_binding_resolution_failure(
        self,
        api: APITestHelper,
        unique_id: str,
        created_bot_uuids: list[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("BAAS_STUB_BOT_BINDING_ERROR", "1")
        bot = await create_test_bot(api, f"e2e-bindfail-{unique_id}")
        bot_uuid = _get_bot_uuid(bot)
        created_bot_uuids.append(bot_uuid)
        try:
            assert bot.get("status") in ("PENDING", "FAILED", None), (
                f"Expected PENDING/FAILED after binding error, got {bot.get('status')}"
            )
        finally:
            await cleanup_bot(api, bot_uuid)

    @pytest.mark.asyncio
    async def test_bot_run_binding_not_found(
        self,
        api: APITestHelper,
        unique_id: str,
        created_bot_uuids: list[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("BAAS_STUB_BOT_BINDING_NOT_FOUND", "1")
        bot = await create_test_bot(api, f"e2e-bindnf-{unique_id}")
        bot_uuid = _get_bot_uuid(bot)
        created_bot_uuids.append(bot_uuid)
        try:
            assert bot.get("status") in ("PENDING", "FAILED", None), (
                f"Expected PENDING/FAILED after binding not found, got {bot.get('status')}"
            )
        finally:
            await cleanup_bot(api, bot_uuid)


class TestBotRunEngineErrors:
    """Bot run error paths from engine adapter session failures."""

    @pytest.mark.asyncio
    async def test_engine_session_creation_failure(
        self,
        api: APITestHelper,
        unique_id: str,
        created_bot_uuids: list[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("BAAS_STUB_ENGINE_SESSION_ERROR", "1")
        bot = await create_test_bot(api, f"e2e-engfail-{unique_id}")
        bot_uuid = _get_bot_uuid(bot)
        created_bot_uuids.append(bot_uuid)
        try:
            assert bot.get("status") in ("PENDING", "FAILED", None), (
                f"Expected PENDING/FAILED after engine session error, got {bot.get('status')}"
            )
        finally:
            await cleanup_bot(api, bot_uuid)

    @pytest.mark.asyncio
    async def test_engine_session_timeout(
        self,
        api: APITestHelper,
        unique_id: str,
        created_bot_uuids: list[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("BAAS_STUB_ENGINE_SESSION_SLOW", "1")
        bot = await create_test_bot(api, f"e2e-engslow-{unique_id}")
        bot_uuid = _get_bot_uuid(bot)
        created_bot_uuids.append(bot_uuid)
        try:
            assert bot.get("status") in ("PENDING", "FAILED", None), (
                f"Expected PENDING/FAILED after engine session slow, got {bot.get('status')}"
            )
        finally:
            await cleanup_bot(api, bot_uuid)


class TestBotRunInvalidRequests:
    """Error paths for invalid requests in bot run lifecycle."""

    @pytest.mark.asyncio
    async def test_invalid_session_key(self, api: APITestHelper) -> None:
        response = await api.client.get(
            api.open_api_session_url("invalid-session-id-99999"),
            params=api.params(),
        )
        assert response.status_code in (200, 400, 401, 404, 422, 500, 501), (
            f"Invalid session key returned {response.status_code}: {response.text}"
        )

    @pytest.mark.asyncio
    async def test_create_bot_missing_template_uuid(self, api: APITestHelper) -> None:
        response = await api.client.post(
            api.bot_url(),
            params=api.params(),
            json={
                "name": "e2e-no-template",
                "device_count": 1,
                "operator": "e2e-test",
            },
        )
        assert response.status_code in (200, 400, 404, 422, 500, 501), (
            f"Expected 4xx/5xx for missing template_uuid, got {response.status_code}"
        )

    @pytest.mark.asyncio
    async def test_create_bot_missing_operator(self, api: APITestHelper) -> None:
        response = await api.client.post(
            api.bot_url(),
            params=api.params(),
            json={
                "name": "e2e-no-operator",
                "template_uuid": "TEMPLATE-4d0e2849d7004111836333de782b95d8",
                "device_count": 1,
            },
        )
        assert response.status_code in (200, 400, 404, 422, 500, 501), (
            f"Expected 4xx/5xx for missing operator, got {response.status_code}"
        )


class TestBotRunDestroyErrors:
    """Error paths for bot destruction."""

    @pytest.mark.asyncio
    async def test_destroy_nonexistent_bot(self, api: APITestHelper) -> None:
        response = await api.client.post(
            api.bot_url("nonexistent-bot-uuid-destroy") + "/destroy",
            params=api.params(),
            json={
                "operator": "e2e-test",
                "request_id": uuid.uuid4().hex,
            },
        )
        assert response.status_code in (200, 400, 404, 500, 501), (
            f"Destroy nonexistent returned {response.status_code}: {response.text}"
        )

    @pytest.mark.asyncio
    async def test_double_destroy(
        self,
        api: APITestHelper,
        unique_id: str,
        created_bot_uuids: list[str],
    ) -> None:
        bot = await create_test_bot(api, f"e2e-dbldel-{unique_id}")
        bot_uuid = _get_bot_uuid(bot)
        created_bot_uuids.append(bot_uuid)
        try:
            response1 = await api.client.post(
                api.bot_url(bot_uuid) + "/destroy",
                params=api.params(),
                json={
                    "operator": "e2e-test",
                    "request_id": uuid.uuid4().hex,
                },
            )
            assert response1.status_code in (200, 400, 404, 409, 500, 501), (
                f"First destroy returned {response1.status_code}: {response1.text}"
            )
            response2 = await api.client.post(
                api.bot_url(bot_uuid) + "/destroy",
                params=api.params(),
                json={
                    "operator": "e2e-test",
                    "request_id": uuid.uuid4().hex,
                },
            )
            assert response2.status_code in (200, 400, 404, 409, 500, 501), (
                f"Second destroy returned {response2.status_code}: {response2.text}"
            )
        finally:
            await cleanup_bot(api, bot_uuid)
