"""RunStatus and BotRunRecord unit tests."""

from datetime import datetime

import pytest

from secbaas.community.core.repository.bot_run._record import BotRunRecord, RunStatus

# ---------------------------------------------------------------------------
# TestRunStatus
# ---------------------------------------------------------------------------


class TestRunStatus:
    def test_pending_value(self):
        assert RunStatus.PENDING.value == "PENDING"

    def test_running_value(self):
        assert RunStatus.RUNNING.value == "RUNNING"

    def test_completed_value(self):
        assert RunStatus.COMPLETED.value == "COMPLETED"

    def test_failed_value(self):
        assert RunStatus.FAILED.value == "FAILED"

    def test_all_statuses(self):
        values = {s.value for s in RunStatus}
        assert values == {"PENDING", "RUNNING", "COMPLETED", "FAILED", "TIME_OUT"}

    def test_from_value(self):
        assert RunStatus("PENDING") is RunStatus.PENDING
        assert RunStatus("RUNNING") is RunStatus.RUNNING
        assert RunStatus("COMPLETED") is RunStatus.COMPLETED
        assert RunStatus("FAILED") is RunStatus.FAILED
        assert RunStatus("TIME_OUT") is RunStatus.TIME_OUT

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            RunStatus("UNKNOWN")


# ---------------------------------------------------------------------------
# TestBotRunRecord
# ---------------------------------------------------------------------------


class TestBotRunRecord:
    def _make_record(self, **overrides):
        defaults = dict(
            id=1,
            gmt_create=datetime(2026, 1, 1, 0, 0, 0),
            gmt_modified=datetime(2026, 1, 1, 0, 0, 0),
            run_id="run-001",
            bot_id="bot-001",
            api_key_prefix="ak_test",
            message="hello",
            message_long="hello long",
            metadata=None,
            status="PENDING",
            result_content=None,
            result_content_long=None,
            result_extra=None,
            error=None,
            completed_at=None,
        )
        defaults.update(overrides)
        return BotRunRecord(**defaults)

    def test_basic_construction(self):
        record = self._make_record()
        assert record.id == 1
        assert record.run_id == "run-001"
        assert record.bot_id == "bot-001"
        assert record.status == "PENDING"

    def test_all_fields_assigned(self):
        now = datetime(2026, 6, 1, 12, 0, 0)
        record = self._make_record(
            id=99,
            gmt_create=now,
            gmt_modified=now,
            run_id="run-full",
            bot_id="bot-full",
            api_key_prefix="ak_full",
            message="short",
            message_long="long message",
            metadata={"source": "api"},
            status="COMPLETED",
            result_content="short result",
            result_content_long="long result",
            result_extra={"tokens": 100},
            error=None,
            completed_at=now,
        )

        assert record.id == 99
        assert record.gmt_create == now
        assert record.gmt_modified == now
        assert record.run_id == "run-full"
        assert record.bot_id == "bot-full"
        assert record.api_key_prefix == "ak_full"
        assert record.message == "short"
        assert record.message_long == "long message"
        assert record.metadata == {"source": "api"}
        assert record.status == "COMPLETED"
        assert record.result_content == "short result"
        assert record.result_content_long == "long result"
        assert record.result_extra == {"tokens": 100}
        assert record.error is None
        assert record.completed_at == now

    def test_slots(self):
        """BotRunRecord uses slots=True, so dynamic attribute assignment should fail."""
        record = self._make_record()
        with pytest.raises(AttributeError):
            record.nonexistent_field = "oops"

    def test_failed_record(self):
        record = self._make_record(
            status="FAILED",
            error="Something went wrong",
            completed_at=datetime(2026, 1, 1, 1, 0, 0),
        )
        assert record.status == "FAILED"
        assert record.error == "Something went wrong"
        assert record.completed_at is not None

    def test_optional_fields_none_by_default(self):
        record = self._make_record()
        assert record.metadata is None
        assert record.result_content is None
        assert record.result_content_long is None
        assert record.result_extra is None
        assert record.error is None
        assert record.completed_at is None
