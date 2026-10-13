from __future__ import annotations

from uuid import uuid4

import pytest

from secbaas.community.bootstrap import get_container
from secbaas.community.core.repository.bot import (
    BotRecord,
    BotRecordConflictError,
    BotRepository,
    RestoredBotRecord,
)
from secbaas.community.core.utils.env_utils import get_current_env

pytestmark = pytest.mark.integration

TEST_ENV = get_current_env()
TEST_TENANT = "test_tenant"
OTHER_ENV = f"{TEST_ENV}-cross"


def _generate_uuid() -> str:
    return uuid4().hex


def _identity_fields() -> list[str]:
    return [
        "id",
        "bot_uuid",
        "tenant",
        "env",
        "domain",
        "is_deleted",
        "creator",
        "modifier",
        "status",
        "name",
        "description",
        "template_uuid",
        "replica_desired",
        "replica_minimum",
        "replica_maximum",
        "auto_scaling_enabled",
        "sla_grade",
        "extra_config",
    ]


class TestBotSqliteOrmEquivalence:
    def test_insert_and_get_roundtrip(self):
        repo: BotRepository = get_container().repository.bot_repository()
        bot_uuid = _generate_uuid()
        extra = {"region": "cn-hangzhou", "tier": "premium"}

        bot_id = repo.insert_bot(
            bot_uuid=bot_uuid,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="u",
            modifier="u",
            name="Sqlite Bot",
            description="ORM→ORM comparison",
            extra_config=extra,
            sla_grade="standard",
        )
        assert bot_id > 0

        record = repo.get_by_id(bot_id, TEST_TENANT, TEST_ENV)
        assert isinstance(record, BotRecord)
        assert record.bot_uuid == bot_uuid
        assert record.tenant == TEST_TENANT
        assert record.name == "Sqlite Bot"
        assert record.extra_config == extra
        assert record.status == "PENDING"
        assert record.is_deleted == 0
        assert record.gmt_create is not None
        assert record.gmt_modified is not None

    def test_get_by_id_nonexistent(self):
        repo = get_container().repository.bot_repository()
        assert repo.get_by_id(99999999, TEST_TENANT, TEST_ENV) is None

    def test_deep_null_preservation(self):
        repo = get_container().repository.bot_repository()
        bot_uuid = _generate_uuid()

        bot_id = repo.insert_bot(
            bot_uuid=bot_uuid,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="u",
            modifier="u",
            name="Null Sqlite Bot",
            description=None,
            template_uuid=None,
            extra_config=None,
        )
        record = repo.get_by_id(bot_id, TEST_TENANT, TEST_ENV)
        assert record is not None
        assert record.description is None
        assert record.template_uuid is None
        assert record.extra_config == {}

    def test_deep_json_roundtrip(self):
        repo = get_container().repository.bot_repository()
        bot_uuid = _generate_uuid()
        extra = {"nested": {"key": "value"}, "list": [1, "two", None]}

        bot_id = repo.insert_bot(
            bot_uuid=bot_uuid,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="u",
            modifier="u",
            name="JSON Sqlite Bot",
            extra_config=extra,
        )
        record = repo.get_by_id(bot_id, TEST_TENANT, TEST_ENV)
        assert record is not None
        assert record.extra_config == extra

    def test_deep_update_status(self):
        repo = get_container().repository.bot_repository()
        bot_uuid = _generate_uuid()

        bot_id = repo.insert_bot(
            bot_uuid=bot_uuid,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="orig",
            modifier="orig",
            name="Status Sqlite Bot",
        )
        repo.update_status(
            bot_id=bot_id,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            status="ACTIVE",
            modifier="updater",
        )
        record = repo.get_by_id(bot_id, TEST_TENANT, TEST_ENV)
        assert record is not None
        assert record.status == "ACTIVE"
        assert record.name == "Status Sqlite Bot"
        assert record.creator == "orig"

    def test_soft_delete(self):
        repo = get_container().repository.bot_repository()
        bot_uuid = _generate_uuid()

        bot_id = repo.insert_bot(
            bot_uuid=bot_uuid,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            name="Equiv Bot",
        )
        repo.soft_delete(
            bot_id=bot_id,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            modifier="admin",
        )
        record = repo.get_by_id_including_deleted(bot_id, TEST_TENANT, TEST_ENV)
        assert record is not None
        assert record.is_deleted == bot_id

        result = repo.get_by_id(bot_id, TEST_TENANT, TEST_ENV)
        assert result is None

    def test_list_bots_pagination(self):
        repo = get_container().repository.bot_repository()
        bot_uuid_0 = _generate_uuid()
        bot_uuid_1 = _generate_uuid()

        repo.insert_bot(
            bot_uuid=bot_uuid_0,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            name="Equiv Bot 0",
        )
        repo.insert_bot(
            bot_uuid=bot_uuid_1,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            name="Equiv Bot 1",
        )

        total, records = repo.list_bots(
            tenant=TEST_TENANT,
            env=TEST_ENV,
            page=1,
            page_size=10,
        )
        assert total >= 2
        names = {r.name for r in records}
        assert "Equiv Bot 0" in names or "Equiv Bot 1" in names


class TestRestoreBotRecordSqlite:
    """restore_bot_record_by_id against real SQLite (UK enforced on table)."""

    def _insert(self, repo, *, env=TEST_ENV, tenant=TEST_TENANT, status="PENDING"):
        bot_uuid = _generate_uuid()
        return (
            repo.insert_bot(
                bot_uuid=bot_uuid,
                tenant=tenant,
                env=env,
                domain="test_domain",
                creator="test_user",
                modifier="test_user",
                name="Restore Bot",
            ),
            bot_uuid,
        )

    def _read(self, repo, bot_id: int, *, env=TEST_ENV, tenant=TEST_TENANT):
        return repo.get_by_id_including_deleted(bot_id, tenant, env)

    def test_restore_soft_deleted_record_retires_live_siblings(self):
        repo: BotRepository = get_container().repository.bot_repository()
        target_id, bot_uuid = self._insert(repo)
        repo.soft_delete(
            bot_id=target_id, tenant=TEST_TENANT, env=TEST_ENV, modifier="admin"
        )
        live_same_env_id = repo.insert_bot(
            bot_uuid=bot_uuid,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            name="Live sibling",
        )
        dead_sibling_id = repo.insert_bot(
            bot_uuid=bot_uuid,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            name="Already-dead sibling",
            status="FAILED",
        )
        repo.soft_delete(
            bot_id=dead_sibling_id,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            modifier="admin",
        )

        snapshot = repo.restore_bot_record_by_id(bot_id=target_id, modifier="ops.alice")

        assert snapshot is not None
        assert isinstance(snapshot, RestoredBotRecord)
        assert snapshot.record.id == target_id
        assert snapshot.record.status == "ACTIVE"
        assert snapshot.record.is_deleted == 0
        assert snapshot.record.modifier == "ops.alice"
        assert snapshot.previous_status == "PENDING"
        assert snapshot.previous_is_deleted == target_id
        assert snapshot.retired_sibling_count == 1

        live_sibling = self._read(repo, live_same_env_id)
        assert live_sibling.status == "RELEASED"
        assert live_sibling.is_deleted == live_same_env_id, (
            "tombstone must be the row's own id, not a literal"
        )
        assert live_sibling.modifier == "ops.alice"

        dead_sibling = self._read(repo, dead_sibling_id)
        assert dead_sibling.is_deleted == dead_sibling_id
        assert dead_sibling.status == "FAILED"
        assert dead_sibling.modifier == "admin", (
            "already-deleted siblings must not be touched"
        )

        reloaded_target = self._read(repo, target_id)
        assert reloaded_target.status == "ACTIVE"
        assert reloaded_target.is_deleted == 0

    def test_restore_already_live_active_converges(self):
        repo: BotRepository = get_container().repository.bot_repository()
        target_id, bot_uuid = self._insert(repo)
        repo.update_status(
            bot_id=target_id,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            status="ACTIVE",
            modifier="updater",
        )

        snapshot = repo.restore_bot_record_by_id(bot_id=target_id, modifier="ops.alice")

        assert snapshot is not None
        assert snapshot.record.status == "ACTIVE"
        assert snapshot.record.is_deleted == 0
        assert snapshot.previous_status == "ACTIVE"
        assert snapshot.previous_is_deleted == 0
        assert snapshot.retired_sibling_count == 0

    def test_restore_retires_cross_env_sibling(self):
        repo: BotRepository = get_container().repository.bot_repository()
        target_id, bot_uuid = self._insert(repo)
        repo.soft_delete(
            bot_id=target_id, tenant=TEST_TENANT, env=TEST_ENV, modifier="admin"
        )
        cross_env_id = repo.insert_bot(
            bot_uuid=bot_uuid,
            tenant=TEST_TENANT,
            env=OTHER_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            name="Cross-env sibling",
        )

        snapshot = repo.restore_bot_record_by_id(bot_id=target_id, modifier="ops.alice")

        assert snapshot is not None
        assert snapshot.record.status == "ACTIVE"
        assert snapshot.retired_sibling_count == 1

        cross_env_sibling = self._read(repo, cross_env_id, env=OTHER_ENV)
        assert cross_env_sibling is not None, (
            "cross-env sibling must be found WITHOUT env filtering to be retired"
        )
        assert cross_env_sibling.status == "RELEASED"
        assert cross_env_sibling.is_deleted == cross_env_id

    def test_restore_no_live_siblings(self):
        repo: BotRepository = get_container().repository.bot_repository()
        target_id, bot_uuid = self._insert(repo)
        repo.soft_delete(
            bot_id=target_id, tenant=TEST_TENANT, env=TEST_ENV, modifier="admin"
        )

        snapshot = repo.restore_bot_record_by_id(bot_id=target_id, modifier="ops.alice")

        assert snapshot is not None
        assert snapshot.retired_sibling_count == 0
        assert snapshot.record.status == "ACTIVE"
        assert snapshot.record.is_deleted == 0

    def test_restore_unknown_bot_id_returns_none_without_side_effects(self):
        repo: BotRepository = get_container().repository.bot_repository()
        control_id, _ = self._insert(repo)
        repo.update_status(
            bot_id=control_id,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            status="ACTIVE",
            modifier="updater",
        )
        control_before = self._read(repo, control_id)

        result = repo.restore_bot_record_by_id(bot_id=99999999, modifier="admin")

        assert result is None
        control_after = self._read(repo, control_id)
        assert control_after.status == control_before.status
        assert control_after.is_deleted == control_before.is_deleted
        assert control_after.modifier == control_before.modifier

    def test_restore_conflict_rolls_back_everything(self, monkeypatch):
        """IntegrityError on the revive UPDATE rolls back the sibling retire too."""
        from sqlalchemy.exc import IntegrityError
        from sqlalchemy.orm import Query

        repo: BotRepository = get_container().repository.bot_repository()
        target_id, bot_uuid = self._insert(repo)
        repo.soft_delete(
            bot_id=target_id, tenant=TEST_TENANT, env=TEST_ENV, modifier="admin"
        )
        sibling_id = repo.insert_bot(
            bot_uuid=bot_uuid,
            tenant=TEST_TENANT,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            name="Race sibling",
        )

        original_update = Query.update

        def _fail_revive(self, values, synchronize_session=True, update_args=None):
            if values.get("status") == "ACTIVE" and values.get("is_deleted") == 0:
                raise IntegrityError(
                    "UPDATE baas_bot",
                    {},
                    Exception(
                        "UNIQUE constraint failed: uk_tnt_bot_uuid_env_status_del"
                    ),
                )
            return original_update(
                self,
                values,
                synchronize_session=synchronize_session,
                update_args=update_args,
            )

        monkeypatch.setattr(Query, "update", _fail_revive)
        try:
            with pytest.raises(BotRecordConflictError):
                repo.restore_bot_record_by_id(bot_id=target_id, modifier="ops.alice")
        finally:
            monkeypatch.undo()

        target_after = self._read(repo, target_id)
        assert target_after.status == "PENDING", "revive must not persist"
        assert target_after.is_deleted == target_id

        sibling_after = self._read(repo, sibling_id)
        assert sibling_after.status == "PENDING", (
            "sibling retire must have been rolled back with the transaction"
        )
        assert sibling_after.is_deleted == 0
