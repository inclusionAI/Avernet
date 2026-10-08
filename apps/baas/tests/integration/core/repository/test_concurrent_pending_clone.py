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

        first_id = bot_repository.try_insert_pending_bot(
            source_bot_id=source_id,
            tenant=TEST_TENANT,
            env=TEST_ENV,
        )

        with pytest.raises(BotRecordConflictError):
            bot_repository.try_insert_pending_bot(
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

        first_id = bot_repository.try_insert_pending_bot(
            source_bot_id=source_id,
            tenant=TEST_TENANT,
            env=TEST_ENV,
        )
        bot_repository.soft_delete(
            bot_id=first_id, tenant=TEST_TENANT, env=TEST_ENV, modifier="test_user"
        )

        second_id = bot_repository.try_insert_pending_bot(
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
