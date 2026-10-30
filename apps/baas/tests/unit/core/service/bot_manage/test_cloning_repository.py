"""
OrmBotRepository unit tests.

Uses pytest + MagicMock ORM session pattern matching existing
test_orm_bot_run_repository.py tests.
"""

import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from secbaas.community.core.repository.bot import (
    BotRecord,
    BotRecordConflictError,
    OrmBotRepository,
    RestoredBotRecord,
)

# ==================== Fixtures ====================


@pytest.fixture
def mock_session():
    """Mock SQLAlchemy ORM session."""
    session = MagicMock()
    session.query.return_value.filter.return_value.order_by.return_value.first.return_value = None
    return session


@pytest.fixture
def mock_database(mock_session):
    """Mock database that yields a mock ORM session via @with_orm_session."""
    database = MagicMock()
    database.orm_session.return_value.__enter__ = MagicMock(return_value=mock_session)
    database.orm_session.return_value.__exit__ = MagicMock(return_value=False)
    return database


@pytest.fixture(autouse=True)
def _patch_bot_model():
    """Patch BotModel so constructor returns a mock with .id=42 pre-set."""
    with patch(
        "secbaas.community.core.repository.bot._orm_repository.BotModel",
        autospec=False,
    ) as mock_cls:

        def _make_model(**kwargs):
            model = MagicMock()
            model.id = 42
            for k, v in kwargs.items():
                setattr(model, k, v)
            return model

        mock_cls.side_effect = _make_model
        yield mock_cls


@pytest.fixture
def repo(mock_database):
    """Create an OrmBotRepository instance with mock database."""
    return OrmBotRepository(mock_database, rel_repo=MagicMock())


# ==================== Model helpers ====================


def _make_mock_bot_model(
    id_val=1,
    bot_uuid="bot-uuid-001",
    tenant="test_tenant",
    env="dev",
    domain="test_domain",
    is_deleted=0,
    creator="creator-001",
    modifier="modifier-001",
    status="ACTIVE",
    name="Test Bot",
    description="A test bot",
    template_uuid="tpl-001",
    replica_desired=2,
    replica_minimum=1,
    replica_maximum=5,
    auto_scaling_enabled=1,
    sla_grade="standard",
    extra_config=None,
):
    """Create a MagicMock(spec=BotModel) whose to_record() returns a BotRecord."""
    now = datetime.now()
    final_extra = extra_config if extra_config is not None else {}

    record = BotRecord(
        id=id_val,
        gmt_create=now,
        gmt_modified=now,
        bot_uuid=bot_uuid,
        tenant=tenant,
        env=env,
        domain=domain,
        is_deleted=is_deleted,
        creator=creator,
        modifier=modifier,
        status=status,
        name=name,
        description=description,
        template_uuid=template_uuid,
        replica_desired=replica_desired,
        replica_minimum=replica_minimum,
        replica_maximum=replica_maximum,
        auto_scaling_enabled=auto_scaling_enabled,
        sla_grade=sla_grade,
        extra_config=final_extra,
    )

    model = MagicMock()
    model.to_record.return_value = record
    model.id = id_val
    return model, record


# ==================== BotModel.to_record / BotRecord ====================


from secbaas.community.core.service.bot_manage._cloning import (
    clone_bot_record,
    try_insert_pending_bot,
)


class TestInsertBotRecord:
    def test_source_not_found_raises_value_error(self, repo, mock_session):
        """get_by_id called internally returns None."""
        mock_session.query.return_value.filter.return_value.first.return_value = None

        with pytest.raises(ValueError, match="Source bot not found"):
            clone_bot_record(
                repo, source_bot_id=999, tenant="t1", env="dev", status="PENDING"
            )

    def test_basic_clone(self, repo, mock_session):
        source_model, _ = _make_mock_bot_model(
            id_val=5,
            bot_uuid="bot-src",
            name="Source Bot",
            status="ACTIVE",
            description="Source Desc",
        )

        mock_session.query.return_value.filter.return_value.first.return_value = (
            source_model
        )

        result = clone_bot_record(
            repo, source_bot_id=5, tenant="t1", env="dev", status="FAILED"
        )

        assert result is not None
        mock_session.add.assert_called_once()
        mock_session.flush.assert_called_once()
        added_model = mock_session.add.call_args[0][0]
        assert added_model.template_uuid == "tpl-001"

    def test_clone_with_template_override(self, repo, mock_session):
        source_model, _ = _make_mock_bot_model(
            id_val=5,
            bot_uuid="bot-src",
            template_uuid="TEMPLATE-old",
        )
        mock_session.query.return_value.filter.return_value.first.return_value = (
            source_model
        )

        result = clone_bot_record(
            repo,
            source_bot_id=5,
            tenant="t1",
            env="dev",
            status="FAILED",
            template_uuid="TEMPLATE-new",
        )

        assert result is not None
        added_model = mock_session.add.call_args[0][0]
        assert added_model.template_uuid == "TEMPLATE-new"

    def test_live_pending_clone_no_longer_soft_deletes_incumbent(
        self, repo, mock_session
    ):
        """A live PENDING row must never be destroyed by a concurrent clone."""
        source_model, source_record = _make_mock_bot_model(
            id_val=5, bot_uuid="bot-src", name="Source Bot", status="ACTIVE"
        )
        pending_model, pending_record = _make_mock_bot_model(
            id_val=10, bot_uuid="bot-src", status="PENDING"
        )

        mock_session.query.return_value.filter.return_value.first.side_effect = [
            source_model,
            pending_model,
        ]
        mock_session.query.return_value.filter.return_value.order_by.return_value.first.return_value = None

        result = clone_bot_record(
            repo, source_bot_id=5, tenant="t1", env="dev", status="PENDING"
        )

        assert result is not None
        repo._rel_repo.soft_delete_by_bot_id.assert_not_called()

    def test_clone_with_soft_deleted_pending_inserts_fresh_row(
        self, repo, mock_session
    ):
        """A soft-deleted PENDING row is left untouched; the clone is a fresh INSERT (previously reviving it deadlocked the live UK slot)."""
        source_model, source_record = _make_mock_bot_model(
            id_val=5, bot_uuid="bot-src", status="ACTIVE"
        )

        mock_session.query.return_value.filter.return_value.first.return_value = (
            source_model
        )
        # The live-only lookup does not return a historical tombstone.
        mock_session.query.return_value.filter.return_value.order_by.return_value.first.return_value = None
        mock_session.query.return_value.filter.return_value.update.return_value = 1

        result = clone_bot_record(
            repo, source_bot_id=5, tenant="t1", env="dev", status="PENDING"
        )

        assert result is not None
        mock_session.query.return_value.filter.return_value.update.assert_not_called()
        mock_session.add.assert_called_once()
        added_model = mock_session.add.call_args[0][0]
        assert added_model.status == "PENDING"

    def test_clone_with_name_override(self, repo, mock_session):
        source_model, source_record = _make_mock_bot_model(
            id_val=5, bot_uuid="bot-src", name="Source Bot", status="ACTIVE"
        )
        mock_session.query.return_value.filter.return_value.first.return_value = (
            source_model
        )
        mock_session.query.return_value.filter.return_value.order_by.return_value.first.return_value = None

        result = clone_bot_record(
            repo,
            source_bot_id=5,
            tenant="t1",
            env="dev",
            status="PENDING",
            name="Custom Name",
        )

        assert result is not None

    def test_clone_with_extra_config_override(self, repo, mock_session):
        source_model, source_record = _make_mock_bot_model(
            id_val=5,
            bot_uuid="bot-src",
            status="ACTIVE",
            extra_config={"old": "config"},
        )
        mock_session.query.return_value.filter.return_value.first.return_value = (
            source_model
        )
        mock_session.query.return_value.filter.return_value.order_by.return_value.first.return_value = None

        result = clone_bot_record(
            repo,
            source_bot_id=5,
            tenant="t1",
            env="dev",
            status="PENDING",
            extra_config={"new": "config"},
        )

        assert result is not None

    def test_non_pending_status_skips_cleanup(self, repo, mock_session):
        source_model, _ = _make_mock_bot_model(
            id_val=5, bot_uuid="bot-src", status="ACTIVE"
        )
        mock_session.query.return_value.filter.return_value.first.return_value = (
            source_model
        )

        result = clone_bot_record(
            repo, source_bot_id=5, tenant="t1", env="dev", status="FAILED"
        )

        assert result is not None
        first_call_count = (
            mock_session.query.return_value.filter.return_value.first.call_count
        )
        assert first_call_count == 1

    def test_clone_uses_modifier_parameter(self, repo, mock_session):
        source_model, _ = _make_mock_bot_model(
            id_val=5, bot_uuid="bot-src", status="ACTIVE"
        )
        mock_session.query.return_value.filter.return_value.first.return_value = (
            source_model
        )

        clone_bot_record(
            repo,
            source_bot_id=5,
            tenant="t1",
            env="dev",
            status="FAILED",
            modifier="custom-mod",
        )

        added_model = mock_session.add.call_args[0][0]
        assert added_model.modifier == "custom-mod"


class TestTryInsertPendingBot:
    def test_source_not_found_raises_value_error(self, repo, mock_session):
        mock_session.query.return_value.filter.return_value.first.return_value = None

        with pytest.raises(ValueError, match="Source bot not found"):
            try_insert_pending_bot(repo, source_bot_id=999, tenant="t1", env="dev")

    def test_live_pending_raises_conflict(self, repo, mock_session):
        """A concurrent UPDATE that already holds the PENDING slot must win."""
        source_model, _ = _make_mock_bot_model(
            id_val=5, bot_uuid="bot-src", status="ACTIVE"
        )
        pending_model, _ = _make_mock_bot_model(
            id_val=10, bot_uuid="bot-src", status="PENDING"
        )
        mock_session.query.return_value.filter.return_value.first.return_value = (
            source_model
        )
        mock_session.query.return_value.filter.return_value.order_by.return_value.first.return_value = pending_model

        with pytest.raises(BotRecordConflictError, match="already exists"):
            try_insert_pending_bot(repo, source_bot_id=5, tenant="t1", env="dev")

        mock_session.add.assert_not_called()

    def test_inserts_when_slot_free(self, repo, mock_session):
        source_model, _ = _make_mock_bot_model(
            id_val=5, bot_uuid="bot-src", status="ACTIVE"
        )
        mock_session.query.return_value.filter.return_value.first.return_value = (
            source_model
        )
        mock_session.query.return_value.filter.return_value.order_by.return_value.first.return_value = None

        result = try_insert_pending_bot(repo, source_bot_id=5, tenant="t1", env="dev")

        assert result == 42
        added_model = mock_session.add.call_args[0][0]
        assert added_model.status == "PENDING"

    def test_integrity_error_translated_to_conflict(self, repo, mock_session):
        """The unique key is the final arbiter when the lock is bypassed."""
        from sqlalchemy.exc import IntegrityError

        source_model, _ = _make_mock_bot_model(
            id_val=5, bot_uuid="bot-src", status="ACTIVE"
        )
        mock_session.query.return_value.filter.return_value.first.side_effect = [
            source_model,
            None,
        ]
        mock_session.flush.side_effect = IntegrityError(
            "stmt", {}, Exception(1062, "Duplicate entry")
        )

        with pytest.raises(BotRecordConflictError):
            try_insert_pending_bot(repo, source_bot_id=5, tenant="t1", env="dev")
