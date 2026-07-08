from __future__ import annotations

from datetime import datetime
from uuid import uuid4

import pytest

from secbaas.bootstrap import get_container
from secbaas.core.repository.bot_session import (
    BotSessionRecord,
    BotSessionRepository,
)
from secbaas.core.utils.env_utils import get_current_env

pytestmark = pytest.mark.integration

TEST_ENV = get_current_env()
TEST_TENANT = "test_tenant"


def _generate_uuid() -> str:
    return uuid4().hex


class TestBotSessionSqliteOrmEquivalence:
    def test_insert_and_get_roundtrip(self):
        repo: BotSessionRepository = get_container().repository.bot_session_repository()
        session_id = _generate_uuid()
        bot_uuid = _generate_uuid()

        record_id = repo.insert_session(
            bot_uuid=bot_uuid,
            invoker="u",
            session_id=session_id,
            req={"action": "test"},
            result=None,
            err_msg=None,
            context={"step": 1},
            status="ACTIVE",
            device_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
        )
        assert record_id > 0

        record = repo.get_by_id(record_id)
        assert isinstance(record, BotSessionRecord)
        assert record.session_id == session_id
        assert record.bot_uuid == bot_uuid
        assert record.status == "ACTIVE"
        assert record.req == {"action": "test"}
        assert record.gmt_create is not None
        assert record.gmt_modified is not None

    def test_get_by_id_nonexistent(self):
        repo = get_container().repository.bot_session_repository()
        assert repo.get_by_id(99999999) is None

    def test_deep_null_preservation(self):
        repo = get_container().repository.bot_session_repository()
        session_id = _generate_uuid()

        repo.insert_session(
            bot_uuid=_generate_uuid(),
            invoker="u",
            session_id=session_id,
            req=None,
            result=None,
            err_msg=None,
            context=None,
            status="PENDING",
            device_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
        )
        record = repo.get_by_session_id(session_id)
        assert record is not None
        assert record.req is None
        assert record.result is None
        assert record.err_msg is None

    def test_deep_json_roundtrip(self):
        repo = get_container().repository.bot_session_repository()
        session_id = _generate_uuid()
        req = {"nested": {"key": "value"}, "list": [1, "two", None]}

        repo.insert_session(
            bot_uuid=_generate_uuid(),
            invoker="u",
            session_id=session_id,
            req=req,
            result=None,
            err_msg=None,
            context={"step": 1},
            status="ACTIVE",
            device_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
        )
        record = repo.get_by_session_id(session_id)
        assert record is not None
        assert record.req == req

    def test_deep_update_result(self):
        repo = get_container().repository.bot_session_repository()
        session_id = _generate_uuid()

        repo.insert_session(
            bot_uuid=_generate_uuid(),
            invoker="orig",
            session_id=session_id,
            req={"action": "test"},
            result=None,
            err_msg=None,
            context={"step": 1},
            status="PENDING",
            device_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
        )
        repo.update_result(
            session_id=session_id,
            result={"answer": "42"},
            err_msg=None,
            status="COMPLETED",
        )
        record = repo.get_by_session_id(session_id)
        assert record is not None
        assert record.status == "COMPLETED"
        assert record.result == {"answer": "42"}
        assert record.invoker == "orig"

    def test_get_by_session_id(self):
        repo = get_container().repository.bot_session_repository()
        session_id = _generate_uuid()

        repo.insert_session(
            bot_uuid=_generate_uuid(),
            invoker="test_user",
            session_id=session_id,
            req={"message": "hello"},
            result=None,
            err_msg=None,
            context={},
            status="PENDING",
            device_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
        )

        record = repo.get_by_session_id(session_id)
        assert record is not None
        assert record.session_id == session_id

    def test_get_by_session_id_nonexistent(self):
        repo = get_container().repository.bot_session_repository()
        assert repo.get_by_session_id("nonexistent-session-id") is None

    def test_update_status(self):
        repo = get_container().repository.bot_session_repository()
        session_id = _generate_uuid()

        repo.insert_session(
            bot_uuid=_generate_uuid(),
            invoker="test_user",
            session_id=session_id,
            req={"message": "hello"},
            result=None,
            err_msg=None,
            context={},
            status="PENDING",
            device_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
        )
        repo.update_status(session_id=session_id, status="RUNNING")

        record = repo.get_by_session_id(session_id)
        assert record is not None
        assert record.status == "RUNNING"

    def test_update_context(self):
        repo = get_container().repository.bot_session_repository()
        session_id = _generate_uuid()

        repo.insert_session(
            bot_uuid=_generate_uuid(),
            invoker="test_user",
            session_id=session_id,
            req={"message": "hello"},
            result=None,
            err_msg=None,
            context={},
            status="PENDING",
            device_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
        )
        repo.update_context(
            session_id=session_id,
            context={"key": "value"},
            result={"partial": "output"},
        )

        record = repo.get_by_session_id(session_id)
        assert record is not None
        assert record.context == {"key": "value"}
        assert record.result == {"partial": "output"}

    def test_list_by_bot_uuid(self):
        repo = get_container().repository.bot_session_repository()
        bot_uuid = _generate_uuid()

        repo.insert_session(
            bot_uuid=bot_uuid,
            invoker="test_user",
            session_id=_generate_uuid(),
            req={"message": "hello"},
            result=None,
            err_msg=None,
            context={},
            status="PENDING",
            device_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
        )
        repo.insert_session(
            bot_uuid=bot_uuid,
            invoker="test_user",
            session_id=_generate_uuid(),
            req={"message": "world"},
            result=None,
            err_msg=None,
            context={},
            status="PENDING",
            device_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
        )

        total, records = repo.list_by_bot_uuid(bot_uuid=bot_uuid, page=1, page_size=10)
        assert total == 2
        assert all(r.bot_uuid == bot_uuid for r in records)
