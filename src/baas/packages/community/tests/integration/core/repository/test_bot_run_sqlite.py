from __future__ import annotations

from uuid import uuid4

import pytest

from secbaas.bootstrap import get_container
from secbaas.core.repository.bot_run import BotRunRepository

pytestmark = pytest.mark.integration


def _generate_uuid() -> str:
    return uuid4().hex


class TestBotRunSqliteOrmEquivalence:
    def test_insert_and_get_roundtrip(self):
        repo: BotRunRepository = get_container().repository.bot_run_repository()
        run_id = _generate_uuid()

        returned = repo.insert_run(
            run_id=run_id,
            bot_id=_generate_uuid(),
            api_key_prefix="sk-",
            message_long="msg",
            metadata={"k": "v"},
        )
        assert returned == run_id

        record = repo.get_by_run_id(run_id)
        assert record is not None
        assert record.run_id == run_id
        assert record.status == "PENDING"
        assert record.metadata == {"k": "v"}
        assert record.gmt_create is not None
        assert record.gmt_modified is not None

    def test_get_nonexistent_returns_none(self):
        repo = get_container().repository.bot_run_repository()
        assert repo.get_by_run_id("nonexistent-run-id") is None

    def test_deep_null_preservation(self):
        repo = get_container().repository.bot_run_repository()
        run_id = _generate_uuid()

        repo.insert_run(
            run_id=run_id,
            bot_id=_generate_uuid(),
            api_key_prefix="sk-",
            message_long="msg",
            metadata=None,
        )
        record = repo.get_by_run_id(run_id)
        assert record is not None
        assert record.metadata is None
        assert record.error is None

    def test_deep_json_roundtrip(self):
        repo = get_container().repository.bot_run_repository()
        run_id = _generate_uuid()
        metadata = {"nested": {"key": "value"}, "list": [1, "two", None]}

        repo.insert_run(
            run_id=run_id,
            bot_id=_generate_uuid(),
            api_key_prefix="sk-",
            message_long="msg",
            metadata=metadata,
        )
        record = repo.get_by_run_id(run_id)
        assert record is not None
        assert record.metadata == metadata

    def test_deep_update_status(self):
        repo = get_container().repository.bot_run_repository()
        run_id = _generate_uuid()
        bot_id = _generate_uuid()

        repo.insert_run(
            run_id=run_id,
            bot_id=bot_id,
            api_key_prefix="sk-pres",
            message_long="Preserve me",
            metadata={"tag": "original"},
        )
        repo.update_status(run_id=run_id, status="RUNNING")
        record = repo.get_by_run_id(run_id)
        assert record is not None
        assert record.status == "RUNNING"
        assert record.bot_id == bot_id
        assert record.api_key_prefix == "sk-pres"
        assert record.message_long == "Preserve me"

    def test_insert_and_get_round_trip(self):
        repo: BotRunRepository = get_container().repository.bot_run_repository()
        run_id = _generate_uuid()
        bot_id = _generate_uuid()

        returned = repo.insert_run(
            run_id=run_id,
            bot_id=bot_id,
            api_key_prefix="sk-eq_",
            message_long="Equivalence test: round-trip",
            metadata={"source": "equivalence", "seq": 1},
        )
        assert returned == run_id

        record = repo.get_by_run_id(run_id)
        assert record is not None
        assert record.run_id == run_id
        assert record.bot_id == bot_id
        assert record.api_key_prefix == "sk-eq_"
        assert record.message_long == "Equivalence test: round-trip"
        assert record.metadata == {"source": "equivalence", "seq": 1}
        assert record.status == "PENDING"
        assert record.result_content_long is None
        assert record.result_extra is None
        assert record.error is None
        assert record.completed_at is None
        assert record.gmt_create is not None
        assert record.gmt_modified is not None

    def test_insert_with_none_metadata(self):
        repo = get_container().repository.bot_run_repository()
        run_id = _generate_uuid()

        repo.insert_run(
            run_id=run_id,
            bot_id=_generate_uuid(),
            api_key_prefix="sk-none",
            message_long="No metadata",
            metadata=None,
        )
        record = repo.get_by_run_id(run_id)
        assert record is not None
        assert record.metadata is None

    def test_update_status(self):
        repo = get_container().repository.bot_run_repository()
        run_id = _generate_uuid()

        repo.insert_run(
            run_id=run_id,
            bot_id=_generate_uuid(),
            api_key_prefix="sk-st",
            message_long="Status test",
            metadata=None,
        )
        repo.update_status(run_id=run_id, status="RUNNING")
        record = repo.get_by_run_id(run_id)
        assert record is not None
        assert record.status == "RUNNING"
        assert record.gmt_modified is not None

    def test_update_status_preserves_other_fields(self):
        repo = get_container().repository.bot_run_repository()
        run_id = _generate_uuid()
        bot_id = _generate_uuid()

        repo.insert_run(
            run_id=run_id,
            bot_id=bot_id,
            api_key_prefix="sk-pres",
            message_long="Preserve me",
            metadata={"tag": "preserved"},
        )
        repo.update_status(run_id=run_id, status="RUNNING")
        record = repo.get_by_run_id(run_id)
        assert record is not None
        assert record.status == "RUNNING"
        assert record.bot_id == bot_id
        assert record.api_key_prefix == "sk-pres"
        assert record.message_long == "Preserve me"
        assert record.metadata == {"tag": "preserved"}
        assert record.error is None
        assert record.result_content_long is None

    def test_update_result_sets_gmt_modified(self):
        repo = get_container().repository.bot_run_repository()
        run_id = _generate_uuid()

        repo.insert_run(
            run_id=run_id,
            bot_id=_generate_uuid(),
            api_key_prefix="sk-res",
            message_long="Result test",
            metadata=None,
        )
        repo.update_result(
            run_id=run_id,
            content_long="Answer: 42.",
            extra={"tokens": 7},
        )
        record = repo.get_by_run_id(run_id)
        assert record is not None
        assert record.status == "COMPLETED"
        assert record.result_content_long == "Answer: 42."
        assert record.result_extra == {"tokens": 7}
        assert record.completed_at is not None
        assert record.gmt_modified is not None

    def test_update_error_sets_gmt_modified(self):
        repo = get_container().repository.bot_run_repository()
        run_id = _generate_uuid()

        repo.insert_run(
            run_id=run_id,
            bot_id=_generate_uuid(),
            api_key_prefix="sk-err",
            message_long="Error test",
            metadata=None,
        )
        repo.update_error(run_id=run_id, error="Simulated failure")
        record = repo.get_by_run_id(run_id)
        assert record is not None
        assert record.status == "FAILED"
        assert record.error == "Simulated failure"
        assert record.completed_at is not None
        assert record.gmt_modified is not None

    def test_full_lifecycle(self):
        repo = get_container().repository.bot_run_repository()
        run_id = _generate_uuid()
        bot_id = _generate_uuid()

        assert (
            repo.insert_run(
                run_id=run_id,
                bot_id=bot_id,
                api_key_prefix="sk-life",
                message_long="Lifecycle",
                metadata={"phase": "equiv"},
            )
            == run_id
        )

        repo.update_status(run_id=run_id, status="RUNNING")
        r = repo.get_by_run_id(run_id)
        assert r is not None and r.status == "RUNNING"

        repo.update_result(
            run_id=run_id,
            content_long="Done.",
            extra={"took_ms": 99},
        )
        r = repo.get_by_run_id(run_id)
        assert r is not None
        assert r.status == "COMPLETED"
        assert r.result_content_long == "Done."
        assert r.result_extra == {"took_ms": 99}
        assert r.completed_at is not None
        assert r.gmt_modified is not None
