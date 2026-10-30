"""Integration regression tests for concurrent PENDING bot clone reservation.

These tests exercise ``try_insert_pending_bot`` directly, bypassing the
per-bot distributed lock used by ``create_publish``. They reproduce the
original corruption interleaving at the persistence layer, where the lock
cannot mask it:

    t0  request A: no live PENDING seen
    t1  request B: no live PENDING seen
    t2  request A: insert PENDING clone
    t3  request B: insert PENDING clone  -> previously soft-deleted A's row

The fix makes the live-PENDING check and the insert share one transaction and
raises a conflict instead of deleting the incumbent row.
"""

from uuid import uuid4

import pytest

from secbaas.community.core.repository.bot import (
    BotRecordConflictError,
    BotRepository,
)
from secbaas.community.core.service.bot_manage._cloning import (
    clone_bot_record,
    try_insert_pending_bot,
)
from secbaas.community.core.utils.env_utils import get_current_env

pytestmark = pytest.mark.integration

TEST_ENV = get_current_env()
TEST_TENANT = "test_tenant"


def _insert_active_source(bot_repository: BotRepository) -> tuple[int, str]:
    bot_uuid = uuid4().hex
    bot_id = bot_repository.insert_bot(
        bot_uuid=bot_uuid,
        tenant=TEST_TENANT,
        env=TEST_ENV,
        domain="test_domain",
        creator="test_user",
        modifier="test_user",
        status="ACTIVE",
        name="Concurrent Source Bot",
    )
    return bot_id, bot_uuid


class TestConcurrentPendingCloneReservation:
    """Concurrent clones must never destroy each other's baas_bot rows."""

    def test_second_clone_raises_conflict_and_preserves_first(
        self, bot_repository: BotRepository, db_transaction
    ):
        """A live PENDING clone blocks a second clone without deleting the first."""
        source_id, _ = _insert_active_source(bot_repository)

        first_id = try_insert_pending_bot(
            bot_repository,
            source_bot_id=source_id,
            tenant=TEST_TENANT,
            env=TEST_ENV,
        )

        with pytest.raises(BotRecordConflictError):
            try_insert_pending_bot(
                bot_repository,
                source_bot_id=source_id,
                tenant=TEST_TENANT,
                env=TEST_ENV,
            )

        first = bot_repository.get_by_id(first_id, TEST_TENANT, TEST_ENV)
        assert first is not None, "first PENDING clone was destroyed by the second"
        assert first.is_deleted == 0
        assert first.status == "PENDING"

        source = bot_repository.get_by_id(source_id, TEST_TENANT, TEST_ENV)
        assert source is not None, "source ACTIVE bot was destroyed"
        assert source.status == "ACTIVE"

    def test_clone_succeeds_after_previous_clone_soft_deleted(
        self, bot_repository: BotRepository, db_transaction
    ):
        """A freed PENDING slot (soft-deleted) can be reused by the next clone."""
        source_id, _ = _insert_active_source(bot_repository)

        first_id = try_insert_pending_bot(
            bot_repository,
            source_bot_id=source_id,
            tenant=TEST_TENANT,
            env=TEST_ENV,
        )
        bot_repository.soft_delete(
            bot_id=first_id, tenant=TEST_TENANT, env=TEST_ENV, modifier="test_user"
        )

        second_id = try_insert_pending_bot(
            bot_repository,
            source_bot_id=source_id,
            tenant=TEST_TENANT,
            env=TEST_ENV,
        )

        second = bot_repository.get_by_id(second_id, TEST_TENANT, TEST_ENV)
        assert second is not None
        assert second.status == "PENDING"

        pending = bot_repository.get_by_bot_uuid(
            second.bot_uuid, TEST_TENANT, TEST_ENV, "PENDING"
        )
        assert pending is not None
        assert pending.is_deleted == 0

    def test_clone_after_soft_deleted_leaves_candidate_soft_deleted(
        self, bot_repository: BotRepository, db_transaction
    ):
        """A soft-deleted PENDING candidate stays soft-deleted; the next clone inserts a fresh live row."""
        source_id, _ = _insert_active_source(bot_repository)

        first_id = try_insert_pending_bot(
            bot_repository,
            source_bot_id=source_id,
            tenant=TEST_TENANT,
            env=TEST_ENV,
        )
        bot_repository.soft_delete(
            bot_id=first_id, tenant=TEST_TENANT, env=TEST_ENV, modifier="test_user"
        )

        second_id = try_insert_pending_bot(
            bot_repository,
            source_bot_id=source_id,
            tenant=TEST_TENANT,
            env=TEST_ENV,
        )

        assert second_id != first_id

        second = bot_repository.get_by_id(second_id, TEST_TENANT, TEST_ENV)
        assert second is not None
        assert second.status == "PENDING"
        assert second.is_deleted == 0

        first = bot_repository.get_by_id_including_deleted(
            first_id, TEST_TENANT, TEST_ENV
        )
        assert first is not None
        assert first.is_deleted == first_id
        assert first.status == "PENDING"

    def test_uk_fallback_conflict_preserves_incumbent_payload(
        self, bot_repository: BotRepository, db_transaction
    ):
        """Reconcile-race fallback: losing the PENDING UK aborts with
        ``BotRecordConflictError`` and the incumbent is never modified.

        ``try_insert_pending_bot`` resolves a live-PENDING row check and the
        INSERT in two transactions, so the residual interleave (design D5.4
        (b): a competitor's check ran before the winner's INSERT committed)
        is arbitrated by ``uk_tnt_bot_uuid_env_status_del`` alone. That
        fallback is pinned here with ``insert_bot_record`` — it performs the
        identical blind INSERT-to-UK path whose ``IntegrityError`` both
        clone methods map to ``BotRecordConflictError`` — carrying the
        losing creation's own payload, then asserting the winner's row kept
        its payload instead of being modified or deleted.
        """
        source_id, bot_uuid = _insert_active_source(bot_repository)

        winner_id = try_insert_pending_bot(
            bot_repository,
            source_bot_id=source_id,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            extra_config={"deploy": "winner"},
            name="winner-clone",
        )

        with pytest.raises(BotRecordConflictError):
            clone_bot_record(
                bot_repository,
                source_bot_id=source_id,
                tenant=TEST_TENANT,
                env=TEST_ENV,
                status="PENDING",
                extra_config={"deploy": "loser"},
                name="loser-clone",
            )

        winner = bot_repository.get_by_id(winner_id, TEST_TENANT, TEST_ENV)
        assert winner is not None, "incumbent PENDING clone was destroyed by the loser"
        assert winner.status == "PENDING"
        assert winner.is_deleted == 0
        assert winner.name == "winner-clone"
        assert winner.extra_config == {"deploy": "winner"}

        only_pending = bot_repository.get_by_bot_uuid(
            bot_uuid, TEST_TENANT, TEST_ENV, "PENDING"
        )
        assert only_pending is not None
        assert only_pending.id == winner_id

        source = bot_repository.get_by_id(source_id, TEST_TENANT, TEST_ENV)
        assert source is not None
        assert source.status == "ACTIVE"
