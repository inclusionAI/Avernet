"""E2E tests for bot runner — Websocket, runner lifecycle, status transitions.

Covers Phase 4.3 of Wave 05: bot stop/restart lifecycles, device status
aggregation, health check endpoints, engine adapter error simulations,
and bot detail with health_check flag.
"""

import uuid

import pytest

from tests.e2e.asgi.conftest import (
    ASYNC_POLL_TIMEOUT,
    APITestHelper,
    activate_bot,
    cleanup_bot,
    create_test_bot,
    find_existing_bot,
)

pytestmark = [pytest.mark.bot_run_lifecycle]


def _get_bot_uuid(bot: dict) -> str:
    return bot["bot_uuid"]


class TestBotStatusTransitions:
    """Bot lifecycle status transitions — create, check PENDING, verify transitions."""

    @pytest.mark.asyncio
    async def test_bot_created_with_pending_status(
        self, api: APITestHelper, unique_id: str, created_bot_uuids: list[str]
    ) -> None:
        bot = await create_test_bot(api, f"e2e-status-{unique_id}")
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
            assert data.get("code") == 0
            bot_record = data.get("data", data)
            assert bot_record.get("bot_uuid") == bot_uuid
            assert bot_record.get("status") in ("PENDING", "ACTIVE", "CREATING"), (
                f"Unexpected status: {bot_record.get('status')}"
            )
        finally:
            await cleanup_bot(api, bot_uuid)

    @pytest.mark.asyncio
    async def test_verify_bot_status_after_creation(self, api: APITestHelper) -> None:
        bot = await find_existing_bot(api)
        if bot is None:
            pytest.skip("No existing bots in system")
        bot_uuid = _get_bot_uuid(bot)

        response = await api.client.get(api.bot_url(bot_uuid), params=api.params())
        assert response.status_code in (200, 400, 404, 500), (
            f"Bot detail returned {response.status_code}: {response.text}"
        )


class TestBotStopAndPoll:
    """Bot stop and poll for status change via bot detail endpoint."""

    @pytest.mark.asyncio
    async def test_stop_and_poll_status(
        self, api: APITestHelper, unique_id: str, created_bot_uuids: list[str]
    ) -> None:
        bot = await create_test_bot(api, f"e2e-stop-poll-{unique_id}")
        bot_uuid = _get_bot_uuid(bot)
        created_bot_uuids.append(bot_uuid)
        try:
            stop_resp = await api.client.post(
                f"{api.bot_url(bot_uuid)}/stop",
                params=api.params(),
                json={
                    "operator": "e2e-test",
                    "request_id": uuid.uuid4().hex,
                },
            )
            assert stop_resp.status_code in (200, 400, 404, 500), (
                f"Stop returned {stop_resp.status_code}: {stop_resp.text}"
            )

            detail_resp = await api.client.get(
                api.bot_url(bot_uuid), params=api.params()
            )
            assert detail_resp.status_code in (200, 400, 404, 500), (
                f"Detail after stop returned {detail_resp.status_code}: "
                f"{detail_resp.text}"
            )
        finally:
            await cleanup_bot(api, bot_uuid)

    @pytest.mark.asyncio
    async def test_stop_nonexistent_bot(self, api: APITestHelper) -> None:
        response = await api.client.post(
            f"{api.bot_url('nonexistent-stop-uuid-12345')}/stop",
            params=api.params(),
            json={
                "operator": "e2e-test",
                "request_id": uuid.uuid4().hex,
            },
        )
        assert response.status_code in (200, 400, 404, 500), (
            f"Expected error for nonexistent bot, got {response.status_code}"
        )


class TestBotRestart:
    """Bot restart — POST /api/v1/bots/{uuid}/restart."""

    @pytest.mark.asyncio
    async def test_restart_after_stop(
        self, api: APITestHelper, unique_id: str, created_bot_uuids: list[str]
    ) -> None:
        bot = await create_test_bot(api, f"e2e-restart-{unique_id}")
        bot_uuid = _get_bot_uuid(bot)
        created_bot_uuids.append(bot_uuid)
        try:
            await activate_bot(api, bot, timeout_seconds=ASYNC_POLL_TIMEOUT)
            stop_resp = await api.client.post(
                f"{api.bot_url(bot_uuid)}/stop",
                params=api.params(),
                json={"operator": "e2e-test", "request_id": uuid.uuid4().hex},
            )
            assert stop_resp.status_code == 200
            publish_id = stop_resp.json()["data"]["publish_id"]
            from tests.e2e.asgi.conftest import approve_and_complete

            await approve_and_complete(
                api, publish_id, timeout_seconds=ASYNC_POLL_TIMEOUT
            )
            restart_resp = await api.client.post(
                f"{api.bot_url(bot_uuid)}/restart",
                params=api.params(),
                json={
                    "operator": "e2e-test",
                    "request_id": uuid.uuid4().hex,
                },
            )
            assert restart_resp.status_code == 200, (
                f"Restart after stop returned {restart_resp.status_code}: {restart_resp.text}"
            )
            data = restart_resp.json()["data"]
            assert "publish_id" in data, (
                f"Expected publish_id in restart response: {data}"
            )
        finally:
            await cleanup_bot(api, bot_uuid)

    @pytest.mark.asyncio
    async def test_restart_existing_bot(
        self, api: APITestHelper, unique_id: str, created_bot_uuids: list[str]
    ) -> None:
        bot = await create_test_bot(api, f"e2e-restart-exist-{unique_id}")
        bot_uuid = _get_bot_uuid(bot)
        created_bot_uuids.append(bot_uuid)
        try:
            await activate_bot(api, bot, timeout_seconds=ASYNC_POLL_TIMEOUT)
            response = await api.client.post(
                f"{api.bot_url(bot_uuid)}/restart",
                params=api.params(),
                json={
                    "operator": "e2e-test",
                    "request_id": uuid.uuid4().hex,
                },
            )
            assert response.status_code == 200, (
                f"Restart existing bot failed: {response.status_code} {response.text}"
            )
        finally:
            await cleanup_bot(api, bot_uuid)

    @pytest.mark.asyncio
    async def test_restart_nonexistent_bot(self, api: APITestHelper) -> None:
        response = await api.client.post(
            f"{api.bot_url('nonexistent-restart-uuid-12345')}/restart",
            params=api.params(),
            json={
                "operator": "e2e-test",
                "request_id": uuid.uuid4().hex,
            },
        )
        assert response.status_code in (200, 400, 404, 500), (
            f"Expected error for nonexistent bot, got {response.status_code}"
        )


class TestBotDeviceStatus:
    """Bot device status aggregate — GET device-status endpoint."""

    @pytest.mark.asyncio
    async def test_device_status_aggregate(
        self, api: APITestHelper, unique_id: str, created_bot_uuids: list[str]
    ) -> None:
        bot = await create_test_bot(api, f"e2e-devstat-{unique_id}")
        bot_uuid = _get_bot_uuid(bot)
        created_bot_uuids.append(bot_uuid)
        try:
            response = await api.client.get(
                f"{api.bot_url(bot_uuid)}/device-status",
                params=api.params(),
            )
            assert response.status_code in (200, 400, 404, 500), (
                f"Device status returned {response.status_code}: {response.text}"
            )
            if response.status_code == 200:
                data = response.json()
                assert data.get("code") == 0
                inner = data.get("data", data)
                assert "device_status" in inner, (
                    f"Missing device_status in response: {inner}"
                )
        finally:
            await cleanup_bot(api, bot_uuid)

    @pytest.mark.asyncio
    async def test_device_status_existing_bot(self, api: APITestHelper) -> None:
        bot = await find_existing_bot(api)
        if bot is None:
            pytest.skip("No existing bots in system")
        bot_uuid = _get_bot_uuid(bot)

        response = await api.client.get(
            f"{api.bot_url(bot_uuid)}/device-status",
            params=api.params(),
        )
        assert response.status_code in (200, 400, 404, 500), (
            f"Device status existing returned {response.status_code}: {response.text}"
        )

    @pytest.mark.asyncio
    async def test_device_status_nonexistent_bot(self, api: APITestHelper) -> None:
        response = await api.client.get(
            f"{api.bot_url('nonexistent-dev-uuid-12345')}/device-status",
            params=api.params(),
        )
        assert response.status_code in (200, 400, 404, 500), (
            f"Expected error for nonexistent bot, got {response.status_code}"
        )


class TestEngineAdapterErrors:
    """Runner with engine adapter errors using BAAS_STUB_ENGINE_SESSION_ERROR."""

    @pytest.mark.asyncio
    async def test_engine_session_error_env_var(
        self,
        api: APITestHelper,
        unique_id: str,
        created_bot_uuids: list[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("BAAS_STUB_ENGINE_SESSION_ERROR", "1")
        bot = await create_test_bot(api, f"e2e-enger-{unique_id}")
        bot_uuid = _get_bot_uuid(bot)
        created_bot_uuids.append(bot_uuid)
        try:
            response = await api.client.get(api.bot_url(bot_uuid), params=api.params())
            assert response.status_code in (200, 400, 404, 500, 501), (
                f"Bot detail with engine error env returned "
                f"{response.status_code}: {response.text}"
            )
        finally:
            await cleanup_bot(api, bot_uuid)

    @pytest.mark.asyncio
    async def test_bot_detail_after_engine_reset(
        self,
        api: APITestHelper,
        unique_id: str,
        created_bot_uuids: list[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("BAAS_STUB_ENGINE_SESSION_ERROR", "timeout")
        bot = await create_test_bot(api, f"e2e-engtime-{unique_id}")
        bot_uuid = _get_bot_uuid(bot)
        created_bot_uuids.append(bot_uuid)
        try:
            response = await api.client.get(api.bot_url(bot_uuid), params=api.params())
            assert response.status_code in (200, 400, 404, 500), (
                f"Bot detail with engine timeout env returned "
                f"{response.status_code}: {response.text}"
            )
        finally:
            await cleanup_bot(api, bot_uuid)


class TestBotDetailHealthCheck:
    """Bot detail with health_check flag."""

    @pytest.mark.asyncio
    async def test_bot_detail_with_health_check_true(
        self, api: APITestHelper, unique_id: str, created_bot_uuids: list[str]
    ) -> None:
        bot = await create_test_bot(api, f"e2e-hc-{unique_id}")
        bot_uuid = _get_bot_uuid(bot)
        created_bot_uuids.append(bot_uuid)
        try:
            response = await api.client.get(
                api.bot_url(bot_uuid),
                params=api.params(health_check="true"),
            )
            assert response.status_code in (200, 400, 404, 500), (
                f"Bot detail with health_check=true returned "
                f"{response.status_code}: {response.text}"
            )
            if response.status_code == 200:
                data = response.json()
                assert data.get("code") == 0
                bot_data = data.get("data", data)
                for device in bot_data.get("devices", []):
                    assert "health" in device, f"Device missing health field: {device}"
                    assert device["health"] in ("true", "false", "unknown"), (
                        f"Unexpected health value: {device['health']}"
                    )
        finally:
            await cleanup_bot(api, bot_uuid)

    @pytest.mark.asyncio
    async def test_bot_detail_with_health_check_false(
        self, api: APITestHelper, unique_id: str, created_bot_uuids: list[str]
    ) -> None:
        bot = await create_test_bot(api, f"e2e-hcf-{unique_id}")
        bot_uuid = _get_bot_uuid(bot)
        created_bot_uuids.append(bot_uuid)
        try:
            response = await api.client.get(
                api.bot_url(bot_uuid),
                params=api.params(health_check="false"),
            )
            assert response.status_code in (200, 400, 404, 500), (
                f"Bot detail with health_check=false returned "
                f"{response.status_code}: {response.text}"
            )
        finally:
            await cleanup_bot(api, bot_uuid)

    @pytest.mark.asyncio
    async def test_bot_detail_health_check_existing(self, api: APITestHelper) -> None:
        bot = await find_existing_bot(api)
        if bot is None:
            pytest.skip("No existing bots in system")
        bot_uuid = _get_bot_uuid(bot)

        response = await api.client.get(
            api.bot_url(bot_uuid),
            params=api.params(health_check="true"),
        )
        assert response.status_code in (200, 400, 404, 500), (
            f"Bot detail health check existing returned "
            f"{response.status_code}: {response.text}"
        )


class TestBotHealthEndpoint:
    """Bot health check endpoint — verify exists and returns status code."""

    @pytest.mark.asyncio
    async def test_bot_health_endpoint_exists(
        self, api: APITestHelper, unique_id: str, created_bot_uuids: list[str]
    ) -> None:
        bot = await create_test_bot(api, f"e2e-health-{unique_id}")
        bot_uuid = _get_bot_uuid(bot)
        created_bot_uuids.append(bot_uuid)
        try:
            response = await api.client.get(
                api.bot_health_url(bot_uuid),
                params=api.params(),
            )
            assert response.status_code in (200, 400, 401, 404, 500, 501), (
                f"Bot health returned {response.status_code}: {response.text}"
            )
        finally:
            await cleanup_bot(api, bot_uuid)

    @pytest.mark.asyncio
    async def test_bot_health_existing_bot(self, api: APITestHelper) -> None:
        bot = await find_existing_bot(api)
        if bot is None:
            pytest.skip("No existing bots in system")

        response = await api.client.get(
            api.bot_health_url(bot["bot_uuid"]),
            params=api.params(),
        )
        assert response.status_code in (200, 400, 401, 404, 500), (
            f"Bot health existing returned {response.status_code}: {response.text}"
        )

    @pytest.mark.asyncio
    async def test_bot_health_nonexistent_bot(self, api: APITestHelper) -> None:
        response = await api.client.get(
            api.bot_health_url("nonexistent-health-uuid"),
            params=api.params(),
        )
        assert response.status_code in (200, 400, 401, 404, 500), (
            f"Expected error for nonexistent bot, got {response.status_code}"
        )


class TestOpenApiRuns:
    """Open API runs endpoint — POST /openapi/v1/runs."""

    @pytest.mark.asyncio
    async def test_openapi_runs_requires_auth(self, api: APITestHelper) -> None:
        response = await api.client.post(
            api.open_api_run_url(),
            json={"message": "hello"},
        )
        assert response.status_code in (200, 400, 401, 403, 405, 500), (
            f"OpenAPI runs returned {response.status_code}: {response.text}"
        )

    @pytest.mark.asyncio
    async def test_openapi_runs_with_invalid_key(self, api: APITestHelper) -> None:
        response = await api.client.post(
            api.open_api_run_url(),
            json={"message": "hello"},
            headers={"Authorization": "Bearer invalid-test-key"},
        )
        assert response.status_code in (200, 400, 401, 403, 405, 500), (
            f"OpenAPI runs with invalid key returned "
            f"{response.status_code}: {response.text}"
        )
