"""E2E regression tests for concurrent UPDATE publish conflict handling.

Regression guard for the corruption where 3 concurrent UPDATE requests for the
same bot_uuid caused the ``baas_bot`` record to be soft-deleted by a sibling
request and left several PENDING clones behind.

Root cause previously: ``insert_bot_record`` soft-deleted any existing PENDING
row for the same bot_uuid before inserting its own, so concurrent callers
destroyed each other's records.

Fixed by ``try_insert_pending_bot`` (atomic conflict detection + insert that
never soft-deletes the incumbent) plus a per-bot distributed lock that
serializes publish creation, so concurrent same-type UPDATE requests collapse
onto the existing publish instead of racing.

Requires:
- Service running with PAAS_MOCK_MODE=true (restart-mock)
"""

import asyncio
import uuid

import pytest

from tests.e2e.asgi.conftest import (
    APITestHelper,
    cleanup_bot,
    create_and_activate_bot,
)

pytestmark = [pytest.mark.e2e_asgi]


async def _update_bot_config(api: APITestHelper, bot_uuid: str) -> tuple[int, dict]:
    """Trigger one UPDATE publish; return (status_code, response_data)."""
    resp = await api.client.post(
        f"{api.bot_url(bot_uuid)}/update",
        params=api.params(),
        json={
            "operator": "e2e-concurrent",
            "request_id": uuid.uuid4().hex,
            "config": {"deploy_config": {"image": "mock:latest"}},
        },
    )
    body = resp.json() if resp.content else {}
    return resp.status_code, body


async def _get_bot_records_by_uuid(api: APITestHelper, bot_uuid: str) -> list[dict]:
    """All non-deleted bot records for a bot_uuid."""
    resp = await api.client.get(
        f"{api.bot_url(bot_uuid)}/detail-by-uuid",
        params=api.params(),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["items"]


class TestConcurrentUpdateConflict:
    """Concurrent UPDATE publishes must not corrupt the baas_bot record."""

    @pytest.mark.asyncio
    async def test_three_concurrent_updates_keep_bot_intact(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """3 simultaneous UPDATEs: no record deleted, one publish, bot still usable."""
        bot = await create_and_activate_bot(
            api, f"concurrent-update-{unique_id}", device_count=1
        )
        bot_uuid = bot["bot_uuid"]
        source_bot_id = bot["id"]

        try:
            results = await asyncio.gather(
                _update_bot_config(api, bot_uuid),
                _update_bot_config(api, bot_uuid),
                _update_bot_config(api, bot_uuid),
            )

            for status_code, _ in results:
                assert status_code in (200, 409), (
                    f"concurrent UPDATE must return 200 or 409, got {status_code}"
                )
            assert any(sc == 200 for sc, _ in results), (
                "at least one concurrent UPDATE must succeed"
            )

            records = await _get_bot_records_by_uuid(api, bot_uuid)
            ids = {r["id"] for r in records}

            assert source_bot_id in ids, (
                f"source bot id={source_bot_id} was deleted by a concurrent "
                f"request; surviving records={ids}"
            )

            for r in records:
                assert r["is_deleted"] == 0, (
                    f"bot id={r['id']} must not be soft-deleted"
                )

            pending = [r for r in records if r["status"] == "PENDING"]
            assert len(pending) <= 1, (
                f"at most one PENDING clone may survive, found {len(pending)}: "
                f"{[r['id'] for r in pending]}"
            )
        finally:
            await cleanup_bot(api, bot_uuid)

    @pytest.mark.asyncio
    async def test_concurrent_updates_return_same_publish(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Same-type concurrent UPDATEs collapse onto one publish (idempotent)."""
        bot = await create_and_activate_bot(
            api, f"concurrent-idem-{unique_id}", device_count=1
        )
        bot_uuid = bot["bot_uuid"]

        try:
            results = await asyncio.gather(
                _update_bot_config(api, bot_uuid),
                _update_bot_config(api, bot_uuid),
                _update_bot_config(api, bot_uuid),
            )

            publish_ids = {
                body["data"].get("publish_id")
                for status_code, body in results
                if status_code == 200
            }
            assert len(publish_ids) == 1, (
                f"concurrent same-type UPDATEs must share one publish, got {publish_ids}"
            )
            assert None not in publish_ids, (
                f"successful UPDATE must expose a publish_id, got {publish_ids}"
            )
        finally:
            await cleanup_bot(api, bot_uuid)

    @pytest.mark.asyncio
    async def test_bot_still_updatable_after_concurrent_burst(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """A later UPDATE succeeds after a concurrent burst (no orphan lock/slot)."""
        bot = await create_and_activate_bot(
            api, f"concurrent-recover-{unique_id}", device_count=1
        )
        bot_uuid = bot["bot_uuid"]

        try:
            await asyncio.gather(
                _update_bot_config(api, bot_uuid),
                _update_bot_config(api, bot_uuid),
            )

            status_code, body = await _update_bot_config(api, bot_uuid)
            assert status_code in (200, 409), (
                f"subsequent UPDATE must be accepted or conflict, got {status_code}"
            )
            if status_code == 200:
                assert body["data"].get("publish_id") is not None

            records = await _get_bot_records_by_uuid(api, bot_uuid)
            assert len(records) >= 1, "bot record must survive the burst"
            for r in records:
                assert r["is_deleted"] == 0
        finally:
            await cleanup_bot(api, bot_uuid)
