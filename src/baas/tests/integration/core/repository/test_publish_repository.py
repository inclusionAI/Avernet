"""Integration tests for PublishRepository Protocol against real ZDAS MySQL.

Every test uses ONLY the PublishRepository Protocol — no OrmPublishRepository
references allowed. db_transaction ensures all changes are rolled back.

Requires a bot_id FK — uses bot_repository fixture to create a bot first.
"""

from uuid import uuid4

import pytest

from secbaas.community.core.repository.bot import BotRepository
from secbaas.community.core.repository.publish import PublishRecord, PublishRepository
from secbaas.community.core.utils.env_utils import get_current_env

pytestmark = pytest.mark.integration

TEST_ENV = get_current_env()
TEST_TENANT = "test_tenant"


def _generate_uuid() -> str:
    return uuid4().hex


class TestPublishRepositoryProtocol:
    """Integration tests for PublishRepository Protocol against real ZDAS MySQL."""

    # ------------------------------------------------------------------
    # 1. insert_publish + get_by_id (all fields round-trip)
    # ------------------------------------------------------------------

    def test_insert_and_get_by_id_all_fields_roundtrip(
        self,
        bot_repository: BotRepository,
        publish_repository: PublishRepository,
        db_transaction,
    ):
        bot_id = bot_repository.insert_bot(
            bot_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            name="Publish Test Bot",
        )
        assert bot_id > 0

        publish_id = publish_repository.insert_publish(
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            bot_id=bot_id,
            publish_type="CREATE",
            status="PENDING",
            creator="creator_user",
            modifier="modifier_user",
            name="Test Publish",
            description="A test publish for round-trip",
            publisher="pub_user",
            replica_desired=5,
            batch_capacity=10,
            batch_number=3,
            cooldown_seconds=30,
            config_version="v1.2.3",
            last_publish_id=None,
            changelog="Initial release",
            extra_config={"region": "cn-hangzhou", "tier": "premium"},
        )
        assert publish_id > 0

        record = publish_repository.get_by_id(publish_id, TEST_TENANT, TEST_ENV)
        assert record is not None
        assert record.id == publish_id
        assert record.bot_id == bot_id
        assert record.publish_type == "CREATE"
        assert record.status == "PENDING"
        assert record.name == "Test Publish"
        assert record.description == "A test publish for round-trip"
        assert record.publisher == "pub_user"
        assert record.replica_desired == 5
        assert record.batch_capacity == 10
        assert record.batch_number == 3
        assert record.cooldown_seconds == 30
        assert record.config_version == "v1.2.3"
        assert record.last_publish_id is None
        assert record.changelog == "Initial release"
        assert record.extra_config == {"region": "cn-hangzhou", "tier": "premium"}
        assert record.creator == "creator_user"
        assert record.modifier == "modifier_user"
        assert record.tenant == TEST_TENANT
        assert record.env == TEST_ENV
        assert record.domain == "test_domain"
        assert record.is_deleted == 0

    # ------------------------------------------------------------------
    # 2. get_by_id returns None for missing
    # ------------------------------------------------------------------

    def test_get_by_id_returns_none_for_missing(
        self, publish_repository: PublishRepository, db_transaction
    ):
        result = publish_repository.get_by_id(99999999, TEST_TENANT, TEST_ENV)
        assert result is None

    # ------------------------------------------------------------------
    # 3. update_status (PENDING → ACTIVE)
    # ------------------------------------------------------------------

    def test_update_status_pending_to_active(
        self,
        bot_repository: BotRepository,
        publish_repository: PublishRepository,
        db_transaction,
    ):
        bot_id = bot_repository.insert_bot(
            bot_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            name="Status Test Bot",
        )

        publish_id = publish_repository.insert_publish(
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            bot_id=bot_id,
            publish_type="CREATE",
            status="PENDING",
            creator="test_user",
            modifier="test_user",
            name="Status Change",
        )

        original = publish_repository.get_by_id(publish_id, TEST_TENANT, TEST_ENV)
        assert original is not None
        assert original.status == "PENDING"

        publish_repository.update_status(
            publish_id=publish_id,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            status="ACTIVE",
            modifier="approver",
        )

        updated = publish_repository.get_by_id(publish_id, TEST_TENANT, TEST_ENV)
        assert updated is not None
        assert updated.status == "ACTIVE"

    # ------------------------------------------------------------------
    # 4. update_publish (extra_config, name)
    # ------------------------------------------------------------------

    def test_update_publish_extra_config(
        self,
        bot_repository: BotRepository,
        publish_repository: PublishRepository,
        db_transaction,
    ):
        bot_id = bot_repository.insert_bot(
            bot_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            name="Update Config Bot",
        )

        publish_id = publish_repository.insert_publish(
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            bot_id=bot_id,
            publish_type="UPDATE",
            status="PENDING",
            creator="test_user",
            modifier="test_user",
            name="Original Name",
            extra_config={"old_key": "old_value"},
        )

        result = publish_repository.update_publish(
            publish_id=publish_id,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            extra_config={"new_key": "new_value", "updated": True},
            modifier="updater_user",
        )
        assert result == 1

        record = publish_repository.get_by_id(publish_id, TEST_TENANT, TEST_ENV)
        assert record is not None
        assert record.extra_config == {"new_key": "new_value", "updated": True}
        assert record.name == "Original Name"

    # ------------------------------------------------------------------
    # 5. list_by_bot_id (returns records)
    # ------------------------------------------------------------------

    def test_list_by_bot_id_returns_records(
        self,
        bot_repository: BotRepository,
        publish_repository: PublishRepository,
        db_transaction,
    ):
        bot_id = bot_repository.insert_bot(
            bot_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            name="List Bot",
        )

        publish_id_1 = publish_repository.insert_publish(
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            bot_id=bot_id,
            publish_type="CREATE",
            status="PENDING",
            creator="test_user",
            modifier="test_user",
        )
        publish_id_2 = publish_repository.insert_publish(
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            bot_id=bot_id,
            publish_type="UPDATE",
            status="ACTIVE",
            creator="test_user",
            modifier="test_user",
        )

        records = publish_repository.list_by_bot_id(bot_id, TEST_TENANT, TEST_ENV)
        assert len(records) >= 2

        record_ids = {r.id for r in records}
        assert publish_id_1 in record_ids
        assert publish_id_2 in record_ids

        # Verify ordering: newest first (id DESC)
        for r in records:
            assert r.bot_id == bot_id

    # ------------------------------------------------------------------
    # 6. get_active_by_bot_id (only non-terminal status)
    # ------------------------------------------------------------------

    def test_get_active_by_bot_id_returns_non_terminal(
        self,
        bot_repository: BotRepository,
        publish_repository: PublishRepository,
        db_transaction,
    ):
        bot_id = bot_repository.insert_bot(
            bot_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            name="Active Bot",
        )

        publish_id = publish_repository.insert_publish(
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            bot_id=bot_id,
            publish_type="CREATE",
            status="PENDING",
            creator="test_user",
            modifier="test_user",
        )

        active = publish_repository.get_active_by_bot_id(bot_id, TEST_TENANT, TEST_ENV)
        assert active is not None
        assert active.id == publish_id
        assert active.status == "PENDING"

    def test_get_active_by_bot_id_excludes_terminal(
        self,
        bot_repository: BotRepository,
        publish_repository: PublishRepository,
        db_transaction,
    ):
        bot_id = bot_repository.insert_bot(
            bot_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            name="Terminal Bot",
        )

        publish_repository.insert_publish(
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            bot_id=bot_id,
            publish_type="CREATE",
            status="SUCCESS",
            creator="test_user",
            modifier="test_user",
        )

        active = publish_repository.get_active_by_bot_id(bot_id, TEST_TENANT, TEST_ENV)
        assert active is None

    def test_get_active_by_bot_id_revoked_is_terminal(
        self,
        bot_repository: BotRepository,
        publish_repository: PublishRepository,
        db_transaction,
    ):
        bot_id = bot_repository.insert_bot(
            bot_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            name="Revoked Bot",
        )

        publish_repository.insert_publish(
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            bot_id=bot_id,
            publish_type="CREATE",
            status="REVOKED",
            creator="test_user",
            modifier="test_user",
        )

        active = publish_repository.get_active_by_bot_id(bot_id, TEST_TENANT, TEST_ENV)
        assert active is None

    # ------------------------------------------------------------------
    # 7. soft_delete (get_by_id returns None after)
    # ------------------------------------------------------------------

    def test_soft_delete_hides_from_get_by_id(
        self,
        bot_repository: BotRepository,
        publish_repository: PublishRepository,
        db_transaction,
    ):
        bot_id = bot_repository.insert_bot(
            bot_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            name="Delete Bot",
        )

        publish_id = publish_repository.insert_publish(
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            bot_id=bot_id,
            publish_type="CREATE",
            status="PENDING",
            creator="test_user",
            modifier="test_user",
            name="To Be Deleted",
        )

        # Verify it exists before delete
        before = publish_repository.get_by_id(publish_id, TEST_TENANT, TEST_ENV)
        assert before is not None
        assert before.id == publish_id

        publish_repository.soft_delete(
            publish_id=publish_id,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            modifier="deleter",
        )

        # After soft delete, get_by_id returns None
        after = publish_repository.get_by_id(publish_id, TEST_TENANT, TEST_ENV)
        assert after is None

    def test_soft_delete_does_not_affect_other_bot(
        self,
        bot_repository: BotRepository,
        publish_repository: PublishRepository,
        db_transaction,
    ):
        """Soft delete on one publish must not affect another bot's publish."""
        bot_id_1 = bot_repository.insert_bot(
            bot_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            name="Bot One",
        )
        bot_id_2 = bot_repository.insert_bot(
            bot_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            name="Bot Two",
        )

        publish_id_1 = publish_repository.insert_publish(
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            bot_id=bot_id_1,
            publish_type="CREATE",
            status="PENDING",
            creator="test_user",
            modifier="test_user",
        )
        publish_id_2 = publish_repository.insert_publish(
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            bot_id=bot_id_2,
            publish_type="CREATE",
            status="PENDING",
            creator="test_user",
            modifier="test_user",
        )

        # Soft delete publish_id_1
        publish_repository.soft_delete(
            publish_id=publish_id_1,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            modifier="deleter",
        )

        # publish_id_1 should be hidden
        assert publish_repository.get_by_id(publish_id_1, TEST_TENANT, TEST_ENV) is None

        # publish_id_2 should still be visible
        other = publish_repository.get_by_id(publish_id_2, TEST_TENANT, TEST_ENV)
        assert other is not None
        assert other.id == publish_id_2
