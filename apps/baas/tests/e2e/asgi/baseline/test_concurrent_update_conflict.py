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
    """Visible (non-soft-deleted) bot records for a bot_uuid.

    ``detail-by-uuid`` lists via ``list_by_bot_uuid``, which filters
    ``is_deleted == 0``. A soft-deleted source bot therefore disappears from
    this list — the corruption symptom this module guards against — rather than
    appearing with ``is_deleted != 0``.
    """
    resp = await api.client.get(
        f"{api.bot_url(bot_uuid)}/detail-by-uuid",
        params=api.params(),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["items"]


async def _get_publish_ids_by_bot_uuid(api: APITestHelper, bot_uuid: str) -> list[int]:
    """IDs of every publish workflow attached to the bot_uuid.

    ``GET /bots/{uuid}/publishes`` lists across all generations of the uuid,
    newest first — the API-facing row count used to pin that idempotent
    same-type creations insert zero new publish rows.
    """
    resp = await api.client.get(
        f"{api.bot_url(bot_uuid)}/publishes",
        params=api.params(),
    )
    assert resp.status_code == 200, resp.text
    return [p["id"] for p in resp.json()["data"]]


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
                f"source bot id={source_bot_id} was soft-deleted by a concurrent "
                f"request and is no longer listed; visible records={ids}"
            )

            cloned = [r for r in records if r["id"] != source_bot_id]
            assert len(cloned) <= 1, (
                f"at most one UPDATE clone may be created, found {len(cloned)}: "
                f"{[r['id'] for r in cloned]}"
            )
            for r in cloned:
                assert r["status"] == "PENDING", (
                    f"clone id={r['id']} should be PENDING, got {r['status']}"
                )

            pending = [r for r in records if r["status"] == "PENDING"]
            assert len(pending) <= 1, (
                f"at most one PENDING clone may survive, found {len(pending)}: "
                f"{[r['id'] for r in pending]}"
            )

            source_records = [r for r in records if r["id"] == source_bot_id]
            assert len(source_records) == 1, (
                f"source bot id={source_bot_id} must appear exactly once"
            )
            assert source_records[0]["bot_uuid"] == bot_uuid, (
                "source bot uuid must be unchanged"
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

            conflict_count = sum(1 for sc, _ in results if sc == 409)
            assert conflict_count == 0, (
                f"same-type concurrent UPDATEs must be idempotent (no 409), "
                f"got {conflict_count} conflicts"
            )

            records = await _get_bot_records_by_uuid(api, bot_uuid)
            clones = [r for r in records if r["status"] == "PENDING"]
            assert len(clones) <= 1, (
                f"idempotent UPDATEs must not create extra clones, "
                f"found {len(clones)} PENDING records"
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

    @pytest.mark.asyncio
    async def test_burst_of_updates_collapses_and_frees_followup_update(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Burst of concurrent UPDATEs under the generation-scoped create flow.

        Covers the new creation pipeline end-to-end (uuid-scoped active
        check, reconcile sweep): a burst of same-type UPDATEs must end with
        exactly ONE live publish row and one shared publish id — zero new
        publish rows beyond the first — and exactly one live PENDING clone;
        and a follow-up UPDATE must be accepted with the SAME publish id
        (idempotent collapse unchanged, task 6.14).
        """
        bot = await create_and_activate_bot(
            api, f"concurrent-flow-{unique_id}", device_count=1
        )
        bot_uuid = bot["bot_uuid"]
        source_bot_id = bot["id"]

        try:
            publishes_before = await _get_publish_ids_by_bot_uuid(api, bot_uuid)

            results = await asyncio.gather(
                *(_update_bot_config(api, bot_uuid) for _ in range(8))
            )

            statuses = [sc for sc, _ in results]
            assert all(sc == 200 for sc in statuses), (
                f"same-type burst must collapse with zero wrongful 409s, got {statuses}"
            )

            one_publish_ids = {body["data"].get("publish_id") for _, body in results}
            assert None not in one_publish_ids, (
                f"successful UPDATEs must expose publish_id, got {one_publish_ids}"
            )
            assert len(one_publish_ids) == 1, (
                f"whole burst must share one publish id, got {one_publish_ids}"
            )
            collapsed_publish_id = one_publish_ids.pop()

            publishes_after_burst = await _get_publish_ids_by_bot_uuid(api, bot_uuid)
            assert len(publishes_after_burst) == len(publishes_before) + 1, (
                "burst must add exactly one publish row, got "
                f"{len(publishes_after_burst) - len(publishes_before)} new rows"
            )

            records = await _get_bot_records_by_uuid(api, bot_uuid)
            ids = {r["id"] for r in records}
            assert source_bot_id in ids, (
                f"source bot id={source_bot_id} was soft-deleted by the burst; "
                f"visible records={ids}"
            )
            pending_clones = [r for r in records if r["id"] != source_bot_id]
            assert len(pending_clones) == 1, (
                f"exactly one UPDATE clone may be live, found {len(pending_clones)}: "
                f"{[r['id'] for r in pending_clones]}"
            )
            assert pending_clones[0]["status"] == "PENDING"
            assert all(r["is_deleted"] == 0 for r in records)

            status, body = await _update_bot_config(api, bot_uuid)
            assert status == 200, (
                f"follow-up UPDATE after the burst must be accepted, got {status}: "
                f"{body}"
            )
            assert body["data"]["publish_id"] == collapsed_publish_id, (
                "follow-up must return the collapsed publish id, got "
                f"{body['data'].get('publish_id')}"
            )

            publishes_final = await _get_publish_ids_by_bot_uuid(api, bot_uuid)
            assert len(publishes_final) == len(publishes_after_burst), (
                "idempotent follow-up must add zero publish rows"
            )
        finally:
            await cleanup_bot(api, bot_uuid)

    @pytest.mark.asyncio
    async def test_rejected_update_frees_slot_for_immediate_next_update(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """A REJECTED UPDATE publish is immediately re-updatable.

        The rejected publish's terminal write abandons its clone; the next
        UPDATE publish for the same source bot must be accepted as a brand
        new publish (task 6.13 service-level terminal transition).
        """
        bot = await create_and_activate_bot(
            api, f"reject-frees-slot-{unique_id}", device_count=1
        )
        bot_uuid = bot["bot_uuid"]
        source_bot_id = bot["id"]

        try:
            status, body = await _update_bot_config(api, bot_uuid)
            assert status == 200, f"initial UPDATE must be accepted, got {status}"
            rejected_publish_id = body["data"]["publish_id"]

            resp = await api.client.post(
                api.publish_url(rejected_publish_id, "reject"),
                params=api.params(),
                json={
                    "operator": "e2e-concurrent",
                    "reason": "slot-frees-on-reject regression",
                    "request_id": uuid.uuid4().hex,
                },
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["data"]["status"] == "REJECTED"

            records = await _get_bot_records_by_uuid(api, bot_uuid)
            visible_ids = {r["id"] for r in records}
            assert source_bot_id in visible_ids
            assert all(r["id"] == source_bot_id for r in records), (
                "abandoned clone must be gone from the live records: "
                f"{[r for r in records]}"
            )

            status, body = await _update_bot_config(api, bot_uuid)
            assert status == 200, (
                f"UPDATE after REJECTED must be accepted immediately (slot freed), "
                f"got {status}: {body}"
            )
            next_publish_id = body["data"]["publish_id"]
            assert next_publish_id not in (None, rejected_publish_id), (
                "next UPDATE must be a NEW publish, got the rejected id back"
            )

            resp = await api.client.get(
                api.publish_url(next_publish_id), params=api.params()
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["data"]["status"] == "PENDING"
        finally:
            await cleanup_bot(api, bot_uuid)
