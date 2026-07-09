from __future__ import annotations

from datetime import datetime
from uuid import uuid4

import pytest

from secbaas.core.repository.bot_run import BotRunRecord, BotRunRepository
from secbaas.core.utils.env_utils import get_current_env

mysql_connector = pytest.importorskip("mysql.connector")

pytestmark = pytest.mark.integration

TEST_ENV = get_current_env()


def _generate_uuid() -> str:
    return uuid4().hex


class TestBotRunRepositoryProtocol:
    """Integration tests for BotRunRepository Protocol against real ZDAS MySQL.

    Every test uses ONLY the BotRunRepository Protocol — no
    OrmBotRunRepository references allowed. db_transaction ensures all
    changes are rolled back.

    Tests cover all 5 methods:
      1. insert_run + get_by_run_id
      2. get_by_run_id returns None for missing
      3. update_status
      4. update_result
      5. update_error
    """

    # ── 1. insert_run + get_by_run_id (all fields match) ──

    def test_insert_and_get_by_run_id(
        self, bot_run_repository: BotRunRepository, db_transaction
    ):
        run_id = _generate_uuid()
        bot_id = _generate_uuid()

        returned_run_id = bot_run_repository.insert_run(
            run_id=run_id,
            bot_id=bot_id,
            api_key_prefix="sk-test_",
            message_long="Hello, how are you?",
            metadata={"source": "api", "priority": "high"},
        )
        assert returned_run_id == run_id

        record = bot_run_repository.get_by_run_id(run_id)
        assert record is not None
        assert isinstance(record, BotRunRecord)
        assert record.run_id == run_id
        assert record.bot_id == bot_id
        assert record.api_key_prefix == "sk-test_"
        assert record.message_long == "Hello, how are you?"
        assert record.metadata == {"source": "api", "priority": "high"}
        assert record.status == "PENDING"
        assert record.result_content_long is None
        assert record.result_extra is None
        assert record.error is None
        assert record.completed_at is None
        assert isinstance(record.gmt_create, datetime)
        assert isinstance(record.gmt_modified, datetime)

    def test_insert_run_with_none_metadata(
        self, bot_run_repository: BotRunRepository, db_transaction
    ):
        run_id = _generate_uuid()

        bot_run_repository.insert_run(
            run_id=run_id,
            bot_id=_generate_uuid(),
            api_key_prefix="sk-abcd",
            message_long="Test message",
            metadata=None,
        )

        record = bot_run_repository.get_by_run_id(run_id)
        assert record is not None
        assert record.metadata is None

    # ── 2. get_by_run_id returns None for missing ──

    def test_get_by_run_id_returns_none_for_missing(
        self, bot_run_repository: BotRunRepository, db_transaction
    ):
        result = bot_run_repository.get_by_run_id("nonexistent-run-id")
        assert result is None

    # ── 3. update_status ──

    def test_update_status(self, bot_run_repository: BotRunRepository, db_transaction):
        run_id = _generate_uuid()

        bot_run_repository.insert_run(
            run_id=run_id,
            bot_id=_generate_uuid(),
            api_key_prefix="sk-test_",
            message_long="Test message",
            metadata=None,
        )

        # Verify initial status
        record = bot_run_repository.get_by_run_id(run_id)
        assert record is not None
        assert record.status == "PENDING"

        # Update to RUNNING
        bot_run_repository.update_status(run_id=run_id, status="RUNNING")
        record = bot_run_repository.get_by_run_id(run_id)
        assert record is not None
        assert record.status == "RUNNING"

        # Update to COMPLETED
        bot_run_repository.update_status(run_id=run_id, status="COMPLETED")
        record = bot_run_repository.get_by_run_id(run_id)
        assert record is not None
        assert record.status == "COMPLETED"

    def test_update_status_preserves_other_fields(
        self, bot_run_repository: BotRunRepository, db_transaction
    ):
        run_id = _generate_uuid()
        bot_id = _generate_uuid()

        bot_run_repository.insert_run(
            run_id=run_id,
            bot_id=bot_id,
            api_key_prefix="sk-pres",
            message_long="Original message",
            metadata={"key": "value"},
        )

        bot_run_repository.update_status(run_id=run_id, status="RUNNING")

        record = bot_run_repository.get_by_run_id(run_id)
        assert record is not None
        assert record.status == "RUNNING"
        assert record.bot_id == bot_id
        assert record.api_key_prefix == "sk-pres"
        assert record.message_long == "Original message"
        assert record.metadata == {"key": "value"}
        assert record.error is None
        assert record.result_content_long is None

    # ── 4. update_result ──

    def test_update_result(self, bot_run_repository: BotRunRepository, db_transaction):
        run_id = _generate_uuid()

        bot_run_repository.insert_run(
            run_id=run_id,
            bot_id=_generate_uuid(),
            api_key_prefix="sk-test_",
            message_long="What is 2+2?",
            metadata=None,
        )

        bot_run_repository.update_result(
            run_id=run_id,
            content_long="The answer is 4.",
            extra={"tokens_used": 42, "model": "test-model"},
        )

        record = bot_run_repository.get_by_run_id(run_id)
        assert record is not None
        assert record.status == "COMPLETED"
        assert record.result_content_long == "The answer is 4."
        assert record.result_extra == {"tokens_used": 42, "model": "test-model"}
        assert record.error is None
        assert record.completed_at is not None
        assert isinstance(record.completed_at, datetime)

    def test_update_result_with_none_extra(
        self, bot_run_repository: BotRunRepository, db_transaction
    ):
        run_id = _generate_uuid()

        bot_run_repository.insert_run(
            run_id=run_id,
            bot_id=_generate_uuid(),
            api_key_prefix="sk-test_",
            message_long="Simple question",
            metadata=None,
        )

        bot_run_repository.update_result(
            run_id=run_id,
            content_long="Simple answer.",
            extra=None,
        )

        record = bot_run_repository.get_by_run_id(run_id)
        assert record is not None
        assert record.status == "COMPLETED"
        assert record.result_content_long == "Simple answer."
        assert record.result_extra is None

    def test_update_result_on_nonexistent_run(
        self, bot_run_repository: BotRunRepository, db_transaction
    ):
        """update_result on a missing run_id should not raise — it's a no-op."""
        bot_run_repository.update_result(
            run_id="nonexistent-run-id",
            content_long="Answer",
            extra=None,
        )

    # ── 5. update_error ──

    def test_update_error(self, bot_run_repository: BotRunRepository, db_transaction):
        run_id = _generate_uuid()

        bot_run_repository.insert_run(
            run_id=run_id,
            bot_id=_generate_uuid(),
            api_key_prefix="sk-test_",
            message_long="Will this fail?",
            metadata=None,
        )

        bot_run_repository.update_error(
            run_id=run_id,
            error="Rate limit exceeded: too many requests",
        )

        record = bot_run_repository.get_by_run_id(run_id)
        assert record is not None
        assert record.status == "FAILED"
        assert record.error == "Rate limit exceeded: too many requests"
        assert record.result_content_long is None
        assert record.result_extra is None
        assert record.completed_at is not None
        assert isinstance(record.completed_at, datetime)

    def test_update_error_on_nonexistent_run(
        self, bot_run_repository: BotRunRepository, db_transaction
    ):
        """update_error on a missing run_id should not raise — it's a no-op."""
        bot_run_repository.update_error(
            run_id="nonexistent-run-id",
            error="Some error",
        )

    # ── 6. Full lifecycle: insert → status → result ──

    def test_full_lifecycle_insert_status_result(
        self, bot_run_repository: BotRunRepository, db_transaction
    ):
        run_id = _generate_uuid()
        bot_id = _generate_uuid()

        # Insert
        returned = bot_run_repository.insert_run(
            run_id=run_id,
            bot_id=bot_id,
            api_key_prefix="sk-life",
            message_long="Full lifecycle test",
            metadata={"phase": "smoke"},
        )
        assert returned == run_id

        record = bot_run_repository.get_by_run_id(run_id)
        assert record is not None
        assert record.status == "PENDING"

        # Update status to RUNNING
        bot_run_repository.update_status(run_id=run_id, status="RUNNING")
        record = bot_run_repository.get_by_run_id(run_id)
        assert record is not None
        assert record.status == "RUNNING"

        # Complete with result
        bot_run_repository.update_result(
            run_id=run_id,
            content_long="Lifecycle test completed successfully.",
            extra={"duration_ms": 1234},
        )

        record = bot_run_repository.get_by_run_id(run_id)
        assert record is not None
        assert record.status == "COMPLETED"
        assert record.result_content_long == "Lifecycle test completed successfully."
        assert record.result_extra == {"duration_ms": 1234}
        assert record.completed_at is not None
        assert record.run_id == run_id
        assert record.bot_id == bot_id

    # ── 7. Full lifecycle: insert → error ──

    def test_full_lifecycle_insert_error(
        self, bot_run_repository: BotRunRepository, db_transaction
    ):
        run_id = _generate_uuid()
        bot_id = _generate_uuid()

        bot_run_repository.insert_run(
            run_id=run_id,
            bot_id=bot_id,
            api_key_prefix="sk-err",
            message_long="Will error out",
            metadata=None,
        )

        bot_run_repository.update_status(run_id=run_id, status="RUNNING")
        record = bot_run_repository.get_by_run_id(run_id)
        assert record is not None
        assert record.status == "RUNNING"

        bot_run_repository.update_error(
            run_id=run_id,
            error="Connection timeout after 30 seconds",
        )

        record = bot_run_repository.get_by_run_id(run_id)
        assert record is not None
        assert record.status == "FAILED"
        assert record.error == "Connection timeout after 30 seconds"
        assert record.completed_at is not None

    # ── 8. run_id uniqueness ──

    def test_run_id_uniqueness(
        self, bot_run_repository: BotRunRepository, db_transaction
    ):
        run_id = _generate_uuid()

        bot_run_repository.insert_run(
            run_id=run_id,
            bot_id=_generate_uuid(),
            api_key_prefix="sk-uniq",
            message_long="First insert",
            metadata=None,
        )

        with pytest.raises(Exception):
            bot_run_repository.insert_run(
                run_id=run_id,
                bot_id=_generate_uuid(),
                api_key_prefix="sk-uniq",
                message_long="Duplicate insert",
                metadata=None,
            )
