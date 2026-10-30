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
    return MagicMock()


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


class TestRestoreBotRecordById:
    """restore_bot_record_by_id: single-transaction admin restore."""

    def _set_up_success_mocks(self, mock_session, *, retired=2):
        """Wire the query-mock chains for a successful restore.

        ``populate_existing()`` produces its own mock branch, so the initial
        PK select (``filter().first``) and the post-restore re-select
        (``filter().populate_existing().first``) are wired separately; the
        two UPDATEs share the ``filter().update`` chain, whose return value
        is the sibling-retire rowcount.
        """
        pre_model, _ = _make_mock_bot_model(
            id_val=7, bot_uuid="bot-rst", status="RELEASED", is_deleted=7
        )
        pre_model.status = "RELEASED"
        pre_model.is_deleted = 7
        pre_model.bot_uuid = "bot-rst"
        post_model, post_record = _make_mock_bot_model(
            id_val=7, bot_uuid="bot-rst", status="ACTIVE", is_deleted=0
        )
        mock_session.query.return_value.filter.return_value.first.return_value = (
            pre_model
        )
        select_chain = mock_session.query.return_value.filter.return_value
        select_chain.populate_existing.return_value.first.return_value = post_model
        select_chain.update.return_value = retired
        return pre_model, post_model, post_record

    def test_not_found_returns_none(self, repo, mock_session):
        mock_session.query.return_value.filter.return_value.first.return_value = None

        result = repo.restore_bot_record_by_id(bot_id=999, modifier="admin")

        assert result is None
        mock_session.query.return_value.filter.return_value.update.assert_not_called()

    def test_target_selected_by_pk_without_env_or_tenant_or_deleted_filter(
        self, repo, mock_session
    ):
        self._set_up_success_mocks(mock_session)

        repo.restore_bot_record_by_id(bot_id=7, modifier="admin")

        first_filter_call = mock_session.query.return_value.filter.call_args_list[0]
        assert len(first_filter_call.args) == 1, (
            "PK lookup must carry exactly ONE predicate (no is_deleted/tenant/env)"
        )

    def test_retire_siblings_before_revive(self, repo, mock_session):
        self._set_up_success_mocks(mock_session, retired=2)

        snapshot = repo.restore_bot_record_by_id(bot_id=7, modifier="admin")

        update_calls = (
            mock_session.query.return_value.filter.return_value.update.call_args_list
        )
        assert len(update_calls) == 2

        retire_dict = update_calls[0].args[0]
        assert update_calls[0].kwargs == {"synchronize_session": False}
        assert retire_dict["status"] == "RELEASED"
        from secbaas.community.core.repository.bot import _orm_repository

        assert retire_dict["is_deleted"] is _orm_repository.BotModel.id, (
            "sibling tombstone must be the row's own id (SQL expression), not literal"
        )
        assert retire_dict["modifier"] == "admin"
        assert "gmt_modified" in retire_dict

        revive_dict = update_calls[1].args[0]
        assert update_calls[1].kwargs == {"synchronize_session": False}
        assert revive_dict["is_deleted"] == 0
        assert revive_dict["status"] == "ACTIVE"
        assert revive_dict["modifier"] == "admin"
        assert "gmt_modified" in revive_dict
        assert isinstance(snapshot, RestoredBotRecord)

    def test_sibling_retire_filter_is_uuid_only_cross_env(self, repo, mock_session):
        self._set_up_success_mocks(mock_session)

        repo.restore_bot_record_by_id(bot_id=7, modifier="admin")

        retire_filter_call = mock_session.query.return_value.filter.call_args_list[1]
        assert len(retire_filter_call.args) == 3, (
            "sibling match must be exactly: bot_uuid + id != target + is_deleted = 0"
        )

    def test_snapshot_carries_pre_state_and_retired_count(self, repo, mock_session):
        _, _, post_record = self._set_up_success_mocks(mock_session, retired=3)

        snapshot = repo.restore_bot_record_by_id(bot_id=7, modifier="op.alice")

        assert isinstance(snapshot, RestoredBotRecord)
        assert snapshot.record is post_record
        assert snapshot.previous_status == "RELEASED"
        assert snapshot.previous_is_deleted == 7
        assert snapshot.retired_sibling_count == 3

    def test_no_live_siblings(self, repo, mock_session):
        _, _, post_record = self._set_up_success_mocks(mock_session, retired=0)

        snapshot = repo.restore_bot_record_by_id(bot_id=7, modifier="admin")

        assert snapshot.retired_sibling_count == 0
        assert snapshot.record is post_record

    def test_integrity_error_on_retire_maps_to_conflict(self, repo, mock_session):
        from sqlalchemy.exc import IntegrityError

        pre_model, _, _ = self._set_up_success_mocks(mock_session)
        mock_session.query.return_value.filter.return_value.update.side_effect = (
            IntegrityError("UPDATE baas_bot", {}, Exception("UNIQUE constraint failed"))
        )

        with pytest.raises(BotRecordConflictError, match="rolled back"):
            repo.restore_bot_record_by_id(bot_id=7, modifier="admin")

    def test_integrity_error_on_revive_maps_to_conflict(self, repo, mock_session):
        from sqlalchemy.exc import IntegrityError

        self._set_up_success_mocks(mock_session)
        update_mock = mock_session.query.return_value.filter.return_value.update
        update_mock.side_effect = [
            1,
            IntegrityError(
                "UPDATE baas_bot", {}, Exception("UNIQUE constraint failed")
            ),
        ]

        with pytest.raises(BotRecordConflictError):
            repo.restore_bot_record_by_id(bot_id=7, modifier="admin")
        assert update_mock.call_count == 2, "sibling retire must run before revive"

    def test_modifier_forwarded_to_both_updates(self, repo, mock_session):
        _, _, _ = self._set_up_success_mocks(mock_session)

        repo.restore_bot_record_by_id(bot_id=7, modifier="op.bob")

        update_calls = (
            mock_session.query.return_value.filter.return_value.update.call_args_list
        )
        assert all(c.args[0]["modifier"] == "op.bob" for c in update_calls)
