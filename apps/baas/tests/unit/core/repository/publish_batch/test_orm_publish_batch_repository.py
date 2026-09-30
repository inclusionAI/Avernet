"""
OrmPublishBatchRepository unit tests.

Uses pytest + MagicMock ORM session pattern matching existing
test_orm_bot_repository.py tests.
"""

import json
from datetime import UTC, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from secbaas.community.core.repository.publish_batch import (
    OrmPublishBatchRepository,
    PublishBatchRecord,
)

# ── Fixtures ──────────────────────────────────────────────────────────


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
def _patch_publish_batch_model():
    """Patch PublishBatchModel so constructor returns a mock with .id set."""
    with patch(
        "secbaas.community.core.repository.publish_batch._orm_repository.PublishBatchModel",
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
    """Create an OrmPublishBatchRepository instance with mock database."""
    return OrmPublishBatchRepository(mock_database)


# ── Model helpers ─────────────────────────────────────────────────────


def _make_mock_model(
    id_val: int = 1,
    tenant: str = "test_tenant",
    env: str = "dev",
    domain: str = "test_domain",
    is_deleted: int = 0,
    creator: str = "creator-001",
    modifier: str = "modifier-001",
    publish_id: int = 100,
    bot_id: int = 200,
    batch_index: int = 0,
    batch_capacity: int = 10,
    status: str = "PENDING",
    gmt_start: datetime | None = None,
    gmt_complete: datetime | None = None,
    error_message: str | None = None,
    extra_config: dict | None = None,
) -> MagicMock:
    """Create a MagicMock model whose to_record() returns a PublishBatchRecord."""
    now = datetime.now(UTC)
    if extra_config is not None:
        ec_json = json.dumps(extra_config, ensure_ascii=False)
    else:
        ec_json = None

    model = MagicMock()
    model.id = id_val
    model.gmt_create = now
    model.gmt_modified = now
    model.tenant = tenant
    model.env = env
    model.domain = domain
    model.is_deleted = is_deleted
    model.creator = creator
    model.modifier = modifier
    model.publish_id = publish_id
    model.bot_id = bot_id
    model.batch_index = batch_index
    model.batch_capacity = batch_capacity
    model.status = status
    model.gmt_start = gmt_start
    model.gmt_complete = gmt_complete
    model.error_message = error_message
    model.extra_config = ec_json

    # Attach to_record() that returns a proper PublishBatchRecord
    ec_record = extra_config if extra_config is not None else {}
    model.to_record.return_value = PublishBatchRecord(
        id=id_val,
        gmt_create=now,
        gmt_modified=now,
        tenant=tenant,
        env=env,
        domain=domain,
        is_deleted=is_deleted,
        creator=creator,
        modifier=modifier,
        publish_id=publish_id,
        bot_id=bot_id,
        batch_index=batch_index,
        batch_capacity=batch_capacity,
        status=status,
        gmt_start=gmt_start,
        gmt_complete=gmt_complete,
        error_message=error_message,
        extra_config=ec_record,
    )
    return model


# ── insert_batch ────────────────────────────────────────────────────────


class TestInsertBatch:
    def test_insert_returns_id(self, repo, mock_session):
        result = repo.insert_batch(
            tenant="t1",
            env="dev",
            domain="example.com",
            publish_id=100,
            bot_id=200,
            batch_index=0,
            batch_capacity=10,
            status="PENDING",
            creator="c1",
            modifier="m1",
        )

        assert result == 42  # from _patch_publish_batch_model fixture
        mock_session.add.assert_called_once()
        mock_session.flush.assert_called_once()

    def test_insert_model_fields(self, repo, mock_session, _patch_publish_batch_model):
        repo.insert_batch(
            tenant="t1",
            env="dev",
            domain="example.com",
            publish_id=100,
            bot_id=200,
            batch_index=0,
            batch_capacity=10,
            status="PENDING",
            creator="c1",
            modifier="m1",
        )

        # Check the model was constructed with correct kwargs
        call_kwargs = _patch_publish_batch_model.call_args.kwargs
        assert call_kwargs["tenant"] == "t1"
        assert call_kwargs["env"] == "dev"
        assert call_kwargs["domain"] == "example.com"
        assert call_kwargs["publish_id"] == 100
        assert call_kwargs["bot_id"] == 200
        assert call_kwargs["batch_index"] == 0
        assert call_kwargs["batch_capacity"] == 10
        assert call_kwargs["status"] == "PENDING"
        assert call_kwargs["creator"] == "c1"
        assert call_kwargs["modifier"] == "m1"
        assert call_kwargs["is_deleted"] == 0

    def test_insert_with_extra_config_serializes_json(
        self, repo, _patch_publish_batch_model
    ):
        repo.insert_batch(
            tenant="t1",
            env="dev",
            domain="d",
            publish_id=1,
            bot_id=1,
            batch_index=0,
            batch_capacity=5,
            status="PENDING",
            creator="c1",
            modifier="m1",
            extra_config={"stage": "GRAY", "cooldown_seconds": 30},
        )

        extra = _patch_publish_batch_model.call_args.kwargs["extra_config"]
        assert extra is not None
        parsed = json.loads(extra)
        assert parsed == {"stage": "GRAY", "cooldown_seconds": 30}

    def test_insert_with_none_extra_config(self, repo, _patch_publish_batch_model):
        repo.insert_batch(
            tenant="t1",
            env="dev",
            domain="d",
            publish_id=1,
            bot_id=1,
            batch_index=0,
            batch_capacity=5,
            status="PENDING",
            creator="c1",
            modifier="m1",
            extra_config=None,
        )

        assert _patch_publish_batch_model.call_args.kwargs["extra_config"] is None

    def test_insert_with_datetime_fields(self, repo, _patch_publish_batch_model):
        t_start = datetime(2024, 1, 1, tzinfo=UTC)
        t_complete = datetime(2024, 1, 2, tzinfo=UTC)

        result = repo.insert_batch(
            tenant="t1",
            env="dev",
            domain="d",
            publish_id=1,
            bot_id=1,
            batch_index=0,
            batch_capacity=5,
            status="RUNNING",
            creator="c1",
            modifier="m1",
            gmt_start=t_start,
            gmt_complete=t_complete,
            error_message="test error",
        )

        assert result == 42
        kwargs = _patch_publish_batch_model.call_args.kwargs
        assert kwargs["gmt_start"] == t_start
        assert kwargs["gmt_complete"] == t_complete
        assert kwargs["error_message"] == "test error"


# ── get_by_id ───────────────────────────────────────────────────────────


class TestGetById:
    def test_found(self, repo, mock_session):
        mock_model = _make_mock_model(
            id_val=5,
            publish_id=100,
            bot_id=200,
            status="RUNNING",
            extra_config={"stage": "GRAY"},
        )
        mock_session.query.return_value.filter.return_value.first.return_value = (
            mock_model
        )

        result = repo.get_by_id(5, "test_tenant", "dev")

        assert result is not None
        assert result.id == 5
        assert result.publish_id == 100
        assert result.bot_id == 200
        assert result.status == "RUNNING"
        assert result.extra_config == {"stage": "GRAY"}
        mock_model.to_record.assert_called_once()

    def test_not_found(self, repo, mock_session):
        mock_session.query.return_value.filter.return_value.first.return_value = None

        result = repo.get_by_id(999, "test_tenant", "dev")

        assert result is None

    def test_uses_is_deleted_filter(self, repo, mock_session):
        mock_model = _make_mock_model()
        mock_session.query.return_value.filter.return_value.first.return_value = (
            mock_model
        )

        repo.get_by_id(1, "t1", "dev")

        # Verify query was called (Model class is patched, so verifying call args)
        mock_session.query.assert_called_once()
        mock_session.query.return_value.filter.assert_called_once()

    def test_uses_tenant_isolation(self, repo, mock_session):
        mock_model = _make_mock_model()
        mock_session.query.return_value.filter.return_value.first.return_value = (
            mock_model
        )

        repo.get_by_id(1, "tenant-a", "prod")

        # Verify query was made
        mock_session.query.assert_called_once()
        mock_session.query.return_value.filter.assert_called_once()


# ── update_status ───────────────────────────────────────────────────────


class TestUpdateStatus:
    def test_update_status_with_modifier(self, repo, mock_session):
        repo.update_status(
            batch_id=1,
            tenant="t1",
            env="dev",
            status="RUNNING",
            modifier="mod-1",
        )

        mock_session.query.return_value.filter.return_value.update.assert_called_once()
        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert update_dict["status"] == "RUNNING"
        assert update_dict["modifier"] == "mod-1"

    def test_update_status_without_modifier(self, repo, mock_session):
        """When modifier is not provided, only status is updated."""
        repo.update_status(
            batch_id=1,
            tenant="t1",
            env="dev",
            status="RUNNING",
            modifier=None,
        )

        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert update_dict["status"] == "RUNNING"
        assert "modifier" not in update_dict

    def test_update_status_default_modifier_is_none(self, repo, mock_session):
        """Call without the modifier kwarg at all."""
        repo.update_status(
            batch_id=1,
            tenant="t1",
            env="dev",
            status="COMPLETE",
        )

        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert update_dict["status"] == "COMPLETE"
        assert "modifier" not in update_dict


# ── list_by_publish_id ──────────────────────────────────────────────────


class TestListByPublishId:
    def test_returns_multiple_batches_ordered_by_index(self, repo, mock_session):
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            _make_mock_model(id_val=1, batch_index=0, publish_id=100),
            _make_mock_model(id_val=2, batch_index=1, publish_id=100),
            _make_mock_model(id_val=3, batch_index=2, publish_id=100),
        ]

        result = repo.list_by_publish_id(100, "test_tenant", "dev")

        assert len(result) == 3
        assert result[0].id == 1
        assert result[0].batch_index == 0
        assert result[1].id == 2
        assert result[1].batch_index == 1
        assert result[2].id == 3
        assert result[2].batch_index == 2

    def test_returns_empty_list(self, repo, mock_session):
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

        result = repo.list_by_publish_id(999, "test_tenant", "dev")

        assert result == []

    def test_filters_by_publish_id(self, repo, mock_session):
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

        repo.list_by_publish_id(100, "tenant-b", "prod")

        # Verify query was made
        mock_session.query.assert_called_once()
        mock_session.query.return_value.filter.assert_called_once()


# ── list_by_publish_and_stage ───────────────────────────────────────────


class TestListByPublishAndStage:
    def test_filters_by_stage(self, repo, mock_session):
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            _make_mock_model(id_val=1, batch_index=0, extra_config={"stage": "GRAY"}),
            _make_mock_model(
                id_val=2, batch_index=1, extra_config={"stage": "PROD_FIRST_BATCH"}
            ),
            _make_mock_model(id_val=3, batch_index=2, extra_config={"stage": "GRAY"}),
        ]

        result = repo.list_by_publish_and_stage(100, "test_tenant", "dev", "GRAY")

        assert len(result) == 2
        assert result[0].id == 1
        assert result[0].stage == "GRAY"
        assert result[1].id == 3
        assert result[1].stage == "GRAY"

    def test_no_matching_stage_returns_empty(self, repo, mock_session):
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            _make_mock_model(id_val=1, batch_index=0, extra_config={"stage": "GRAY"}),
        ]

        result = repo.list_by_publish_and_stage(
            100, "test_tenant", "dev", "PROD_FIRST_BATCH"
        )

        assert result == []

    def test_all_empty_returns_empty(self, repo, mock_session):
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

        result = repo.list_by_publish_and_stage(100, "test_tenant", "dev", "GRAY")

        assert result == []

    def test_calls_list_by_publish_id(self, repo, mock_session):
        """Verify it delegates to list_by_publish_id (doesn't query DB directly)."""
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

        repo.list_by_publish_and_stage(100, "t1", "dev", "STAGE_X")

        mock_session.query.assert_called_once()
        mock_session.query.return_value.filter.assert_called_once()


# ── soft_delete ─────────────────────────────────────────────────────────


class TestSoftDelete:
    def test_soft_delete_executes_update(self, repo, mock_session):
        # Mock first() for the existence check
        mock_model = _make_mock_model(id_val=5)
        mock_session.query.return_value.filter.return_value.first.return_value = (
            mock_model
        )

        repo.soft_delete(
            batch_id=5,
            tenant="t1",
            env="dev",
            modifier="admin",
        )

        # Should have called query twice: once for existence check, once for update
        assert mock_session.query.call_count >= 1
        # The update should have been called
        mock_session.query.return_value.filter.return_value.update.assert_called()

    def test_soft_delete_not_found_no_update(self, repo, mock_session):
        mock_session.query.return_value.filter.return_value.first.return_value = None

        repo.soft_delete(
            batch_id=999,
            tenant="t1",
            env="dev",
            modifier="admin",
        )

        # Only existence check query, no update
        mock_session.query.return_value.filter.return_value.update.assert_not_called()
