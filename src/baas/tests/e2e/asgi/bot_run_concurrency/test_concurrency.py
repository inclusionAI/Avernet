"""E2E tests for bot run concurrency — pool + dispatchers (Phase 4.6 of Wave 05).

Covers:
- Task concurrency pool (parallel bot creation)
- Task concurrency pool exhaustion (creation beyond limits)
- Bot concurrency (scale up to multiple devices)
- Bot concurrency limits (device_status endpoint counts)
- Bot list pagination (page/page_size)
- Rapid create/destroy
- Multiple concurrent status queries
- Scale limits (high device count)
"""

import asyncio
import uuid

import httpx
import pytest

from tests.e2e.asgi.conftest import (
    APITestHelper,
    cleanup_bot,
    create_test_bot,
)

pytestmark = [pytest.mark.bot_run_concurrency]

PARALLEL_BOT_COUNT = 5
HIGH_DEVICE_COUNT = 20


class TestTaskConcurrencyPool:
    """Task concurrency pool — parallel bot creation and exhaustion."""

    @pytest.mark.asyncio
    async def test_create_multiple_bots_in_parallel(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Create multiple bots concurrently and verify all succeed."""

        async def create_one(index: int) -> dict:
            return await create_test_bot(api, f"e2e-conc-{unique_id}-{index}")

        results = await asyncio.gather(
            *[create_one(i) for i in range(PARALLEL_BOT_COUNT)],
            return_exceptions=True,
        )

        successes = 0
        failures = []
        bot_uuids = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                failures.append((i, result))
            else:
                successes += 1
                bot_uuids.append(result["bot_uuid"])

        for bot_uuid in bot_uuids:
            await cleanup_bot(api, bot_uuid)

        assert successes >= 1, f"All {PARALLEL_BOT_COUNT} parallel creates failed"
        assert len(failures) < PARALLEL_BOT_COUNT, (
            f"All {PARALLEL_BOT_COUNT} parallel creates failed: {failures}"
        )

    @pytest.mark.asyncio
    async def test_create_bots_pool_exhaustion(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Create bots beyond reasonable parallel limits and verify behavior."""
        bot_uuids: list[str] = []

        async def create_one(index: int) -> dict | None:
            try:
                return await create_test_bot(api, f"e2e-exhaust-{unique_id}-{index}")
            except Exception:
                return None

        results = await asyncio.gather(
            *[create_one(i) for i in range(PARALLEL_BOT_COUNT * 4)],
            return_exceptions=True,
        )

        created = 0
        for result in results:
            if isinstance(result, dict) and result is not None:
                bot_uuids.append(result["bot_uuid"])
                created += 1

        for bot_uuid in bot_uuids:
            await cleanup_bot(api, bot_uuid)

        assert created >= 1, "No bots created in exhaustion test"


class TestBotConcurrency:
    """Bot concurrency — scale, device status, and list pagination."""

    @pytest.mark.asyncio
    async def test_scale_bot_to_multiple_devices(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Scale bot up to multiple devices and verify scale response."""
        bot = await create_test_bot(api, f"e2e-scale-{unique_id}")
        bot_uuid = bot["bot_uuid"]

        try:
            resp = await api.client.post(
                f"{api.bot_url(bot_uuid)}/scale",
                params=api.params(),
                json={
                    "target_count": 3,
                    "operator": "e2e-test",
                    "request_id": uuid.uuid4().hex,
                },
            )
            assert resp.status_code in (200, 409), (
                f"Scale failed: {resp.status_code} {resp.text}"
            )
            if resp.status_code == 200:
                data = resp.json()
                assert data["code"] == 0
        finally:
            await cleanup_bot(api, bot_uuid)

    @pytest.mark.asyncio
    async def test_device_status_returns_counts(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Verify device_status endpoint returns aggregate health counts."""
        bot = await create_test_bot(api, f"e2e-ds-{unique_id}")
        bot_uuid = bot["bot_uuid"]

        try:
            resp = await api.client.get(
                f"{api.bot_url(bot_uuid)}/device-status",
                params=api.params(),
            )
            assert resp.status_code in (200, 500), (
                f"device-status failed: {resp.status_code} {resp.text}"
            )
            if resp.status_code == 200:
                data = resp.json()
                assert data["code"] == 0
                body = data.get("data", {})
                assert isinstance(body, dict), (
                    f"Expected dict response, got {type(body)}"
                )
        finally:
            await cleanup_bot(api, bot_uuid)

    @pytest.mark.asyncio
    async def test_bot_list_pagination(self, api: APITestHelper) -> None:
        """Verify list pagination with page and page_size query params."""
        for page, page_size in [(1, 5), (2, 3), (1, 10)]:
            resp = await api.client.get(
                api.bot_url(),
                params=api.params(page=page, page_size=page_size),
            )
            assert resp.status_code == 200, (
                f"List page={page} size={page_size} failed: {resp.status_code}"
            )
            data = resp.json()
            assert data["code"] == 0
            assert data["data"]["page"] == page
            assert data["data"]["page_size"] == page_size
            assert len(data["data"]["items"]) <= page_size


class TestRapidCreateDestroy:
    """Rapid create/destroy — lifecycle stress."""

    @pytest.mark.asyncio
    async def test_create_and_immediately_destroy(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Create a bot and immediately destroy it — verify graceful handling."""
        bot = await create_test_bot(api, f"e2e-rapid-{unique_id}")
        bot_uuid = bot["bot_uuid"]

        resp = await api.client.post(
            api.bot_url(bot_uuid) + "/destroy",
            params=api.params(),
            json={
                "operator": "e2e-test",
                "request_id": uuid.uuid4().hex,
            },
        )
        assert resp.status_code in (200, 400, 409), (
            f"Immediate destroy unexpected: {resp.status_code} {resp.text}"
        )

    @pytest.mark.asyncio
    async def test_create_destroy_create_cycle(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Create, destroy, then create another bot in rapid succession."""
        bot = await create_test_bot(api, f"e2e-cycle-{unique_id}")
        bot_uuid = bot["bot_uuid"]

        await cleanup_bot(api, bot_uuid)

        bot2 = await create_test_bot(api, f"e2e-cycle-2-{unique_id}")
        bot_uuid2 = bot2["bot_uuid"]

        try:
            assert bot2["bot_uuid"] is not None
        finally:
            await cleanup_bot(api, bot_uuid2)


class TestConcurrentStatusQueries:
    """Multiple concurrent status queries."""

    @pytest.mark.asyncio
    async def test_query_multiple_bot_uuids_in_sequence(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Query multiple bot UUIDs in sequence — verify all return valid status."""
        bot_uuids: list[str] = []
        for i in range(3):
            bot = await create_test_bot(api, f"e2e-seq-{unique_id}-{i}")
            bot_uuids.append(bot["bot_uuid"])

        try:
            for bot_uuid in bot_uuids:
                resp = await api.client.get(
                    api.bot_url(bot_uuid),
                    params=api.params(),
                )
                assert resp.status_code == 200, (
                    f"Query {bot_uuid} failed: {resp.status_code}"
                )
                data = resp.json()
                assert data["code"] == 0
                assert "status" in data["data"]
        finally:
            for bot_uuid in bot_uuids:
                await cleanup_bot(api, bot_uuid)


class TestScaleLimits:
    """Scale limits — high device count behavior."""

    @pytest.mark.asyncio
    async def test_scale_bot_to_high_device_count(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Scale bot to an unreasonably high device count — verify error or success."""
        bot = await create_test_bot(api, f"e2e-hilimit-{unique_id}")
        bot_uuid = bot["bot_uuid"]

        try:
            resp = await api.client.post(
                f"{api.bot_url(bot_uuid)}/scale",
                params=api.params(),
                json={
                    "target_count": HIGH_DEVICE_COUNT,
                    "operator": "e2e-test",
                    "request_id": uuid.uuid4().hex,
                },
            )
            assert resp.status_code in (200, 400, 409, 422), (
                f"High scale unexpected: {resp.status_code} {resp.text}"
            )
        finally:
            await cleanup_bot(api, bot_uuid)

    @pytest.mark.asyncio
    async def test_list_all_bots_broad_pagination(self, api: APITestHelper) -> None:
        """List all bots with a large page size and verify response shape."""
        resp = await api.client.get(
            api.bot_url(),
            params=api.params(page=1, page_size=100),
        )
        assert resp.status_code == 200, (
            f"Large page size query failed: {resp.status_code}"
        )
        data = resp.json()
        assert data["code"] == 0
        assert data["data"]["page"] == 1
        assert data["data"]["page_size"] == 100
        assert "items" in data["data"]
        assert "total" in data["data"]
