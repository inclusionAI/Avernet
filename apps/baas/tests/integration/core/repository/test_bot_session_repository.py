from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from secbaas.community.core.repository.bot_session import (
    BotSessionRecord,
    BotSessionRepository,
)
from secbaas.community.core.utils.env_utils import get_current_env

mysql_connector = pytest.importorskip("mysql.connector")

pytestmark = pytest.mark.integration

TEST_ENV = get_current_env()
TEST_TENANT = "test_tenant"

# Safe time bounds for time-range queries (avoid client-server clock skew)
TIME_MIN = datetime(2000, 1, 1)
TIME_MAX = datetime(2100, 1, 1)


def _generate_uuid() -> str:
    return uuid4().hex


class TestBotSessionRepositoryProtocol:
    """Integration tests for BotSessionRepository Protocol against real ZDAS MySQL.

    Every test uses ONLY the BotSessionRepository Protocol — no
    OrmBotSessionRepository references allowed. db_transaction ensures all
    changes are rolled back.

    Tests cover 10 of 11 methods. Skipped: none (all methods covered).
    """

    # ── 1. insert_session + get_by_id (all fields match) ──

    def test_insert_and_get_by_id(
        self, bot_session_repository: BotSessionRepository, db_transaction
    ):
        bot_uuid = _generate_uuid()
        session_id = _generate_uuid()
        device_uuid = _generate_uuid()

        pk_id = bot_session_repository.insert_session(
            bot_uuid=bot_uuid,
            invoker="test_user",
            session_id=session_id,
            req={"action": "test_op"},
            result=None,
            err_msg=None,
            context={"step": 1},
            status="ACTIVE",
            device_uuid=device_uuid,
            tenant=TEST_TENANT,
        )
        assert pk_id > 0

        record = bot_session_repository.get_by_id(pk_id)
        assert record is not None
        assert record.id == pk_id
        assert record.bot_uuid == bot_uuid
        assert record.invoker == "test_user"
        assert record.session_id == session_id
        assert record.req == {"action": "test_op"}
        assert record.result is None
        assert record.err_msg is None
        assert record.context == {"step": 1}
        assert record.status == "ACTIVE"
        assert record.device_uuid == device_uuid
        assert record.tenant == TEST_TENANT
        assert record.env == TEST_ENV
        assert isinstance(record.gmt_create, datetime)
        assert isinstance(record.gmt_modified, datetime)

    # ── 2. get_by_session_id ──

    def test_get_by_session_id(
        self, bot_session_repository: BotSessionRepository, db_transaction
    ):
        session_id = _generate_uuid()

        bot_session_repository.insert_session(
            bot_uuid=_generate_uuid(),
            invoker="test_user",
            session_id=session_id,
            req=None,
            result=None,
            err_msg=None,
            context=None,
            status="PENDING",
            device_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
        )

        record = bot_session_repository.get_by_session_id(session_id)
        assert record is not None
        assert record.session_id == session_id
        assert record.status == "PENDING"

    def test_get_by_session_id_returns_none_for_missing(
        self, bot_session_repository: BotSessionRepository, db_transaction
    ):
        result = bot_session_repository.get_by_session_id("nonexistent-session-id")
        assert result is None

    # ── 3. update_result ──

    def test_update_result(
        self, bot_session_repository: BotSessionRepository, db_transaction
    ):
        session_id = _generate_uuid()

        bot_session_repository.insert_session(
            bot_uuid=_generate_uuid(),
            invoker="test_user",
            session_id=session_id,
            req=None,
            result=None,
            err_msg=None,
            context=None,
            status="PENDING",
            device_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
        )

        bot_session_repository.update_result(
            session_id=session_id,
            result={"output": "success"},
            err_msg=None,
            status="SUCCESS",
        )

        record = bot_session_repository.get_by_session_id(session_id)
        assert record is not None
        assert record.result == {"output": "success"}
        assert record.status == "SUCCESS"
        assert record.err_msg is None

    def test_update_result_with_error(
        self, bot_session_repository: BotSessionRepository, db_transaction
    ):
        session_id = _generate_uuid()

        bot_session_repository.insert_session(
            bot_uuid=_generate_uuid(),
            invoker="test_user",
            session_id=session_id,
            req=None,
            result=None,
            err_msg=None,
            context=None,
            status="PENDING",
            device_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
        )

        bot_session_repository.update_result(
            session_id=session_id,
            result=None,
            err_msg="Something went wrong",
            status="FAILED",
        )

        record = bot_session_repository.get_by_session_id(session_id)
        assert record is not None
        assert record.result is None
        assert record.err_msg == "Something went wrong"
        assert record.status == "FAILED"

    # ── 4. update_status ──

    def test_update_status(
        self, bot_session_repository: BotSessionRepository, db_transaction
    ):
        session_id = _generate_uuid()

        bot_session_repository.insert_session(
            bot_uuid=_generate_uuid(),
            invoker="test_user",
            session_id=session_id,
            req=None,
            result=None,
            err_msg=None,
            context=None,
            status="PENDING",
            device_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
        )

        bot_session_repository.update_status(session_id=session_id, status="RUNNING")

        record = bot_session_repository.get_by_session_id(session_id)
        assert record is not None
        assert record.status == "RUNNING"

    def test_update_status_preserves_other_fields(
        self, bot_session_repository: BotSessionRepository, db_transaction
    ):
        session_id = _generate_uuid()
        bot_uuid = _generate_uuid()

        bot_session_repository.insert_session(
            bot_uuid=bot_uuid,
            invoker="test_user",
            session_id=session_id,
            req={"cmd": "echo"},
            result=None,
            err_msg=None,
            context={"key": "val"},
            status="PENDING",
            device_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
        )

        bot_session_repository.update_status(session_id=session_id, status="RUNNING")

        record = bot_session_repository.get_by_session_id(session_id)
        assert record is not None
        assert record.status == "RUNNING"
        assert record.bot_uuid == bot_uuid
        assert record.req == {"cmd": "echo"}
        assert record.context == {"key": "val"}

    # ── 5. update_context ──

    def test_update_context_new_values(
        self, bot_session_repository: BotSessionRepository, db_transaction
    ):
        session_id = _generate_uuid()

        bot_session_repository.insert_session(
            bot_uuid=_generate_uuid(),
            invoker="test_user",
            session_id=session_id,
            req=None,
            result=None,
            err_msg=None,
            context=None,
            status="PENDING",
            device_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
        )

        bot_session_repository.update_context(
            session_id=session_id,
            context={"phase": "in_progress"},
        )

        record = bot_session_repository.get_by_session_id(session_id)
        assert record is not None
        assert record.context == {"phase": "in_progress"}

    def test_update_context_merges_existing(
        self, bot_session_repository: BotSessionRepository, db_transaction
    ):
        session_id = _generate_uuid()

        bot_session_repository.insert_session(
            bot_uuid=_generate_uuid(),
            invoker="test_user",
            session_id=session_id,
            req=None,
            result=None,
            err_msg=None,
            context={"step_1": "done", "phase": "init"},
            status="PENDING",
            device_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
        )

        bot_session_repository.update_context(
            session_id=session_id,
            context={"phase": "in_progress", "step_2": "started"},
        )

        record = bot_session_repository.get_by_session_id(session_id)
        assert record is not None
        assert record.context == {
            "step_1": "done",
            "phase": "in_progress",
            "step_2": "started",
        }

    def test_update_context_with_result_and_err(
        self, bot_session_repository: BotSessionRepository, db_transaction
    ):
        session_id = _generate_uuid()

        bot_session_repository.insert_session(
            bot_uuid=_generate_uuid(),
            invoker="test_user",
            session_id=session_id,
            req=None,
            result=None,
            err_msg=None,
            context=None,
            status="PENDING",
            device_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
        )

        bot_session_repository.update_context(
            session_id=session_id,
            result={"final": "ok"},
            err_msg="warning: retry",
            context={"retries": 3},
        )

        record = bot_session_repository.get_by_session_id(session_id)
        assert record is not None
        assert record.result == {"final": "ok"}
        assert record.err_msg == "warning: retry"
        assert record.context == {"retries": 3}

    # ── 6. list_by_bot_uuid (pagination) ──

    def test_list_by_bot_uuid_basic(
        self, bot_session_repository: BotSessionRepository, db_transaction
    ):
        bot_uuid = _generate_uuid()
        sid1 = _generate_uuid()
        sid2 = _generate_uuid()

        bot_session_repository.insert_session(
            bot_uuid=bot_uuid,
            invoker="user_a",
            session_id=sid1,
            req=None,
            result=None,
            err_msg=None,
            context=None,
            status="ACTIVE",
            device_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
        )
        bot_session_repository.insert_session(
            bot_uuid=bot_uuid,
            invoker="user_b",
            session_id=sid2,
            req=None,
            result=None,
            err_msg=None,
            context=None,
            status="PENDING",
            device_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
        )

        total, items = bot_session_repository.list_by_bot_uuid(
            bot_uuid=bot_uuid, page=1, page_size=10
        )
        assert total == 2
        assert len(items) == 2
        session_ids = [r.session_id for r in items]
        assert sid1 in session_ids
        assert sid2 in session_ids

    def test_list_by_bot_uuid_pagination(
        self, bot_session_repository: BotSessionRepository, db_transaction
    ):
        bot_uuid = _generate_uuid()
        for _ in range(3):
            bot_session_repository.insert_session(
                bot_uuid=bot_uuid,
                invoker="test_user",
                session_id=_generate_uuid(),
                req=None,
                result=None,
                err_msg=None,
                context=None,
                status="ACTIVE",
                device_uuid=_generate_uuid(),
                tenant=TEST_TENANT,
            )

        total, items = bot_session_repository.list_by_bot_uuid(
            bot_uuid=bot_uuid, page=1, page_size=2
        )
        assert total == 3
        assert len(items) == 2

    def test_list_by_bot_uuid_empty(
        self, bot_session_repository: BotSessionRepository, db_transaction
    ):
        total, items = bot_session_repository.list_by_bot_uuid(
            bot_uuid=_generate_uuid(), page=1, page_size=10
        )
        assert total == 0
        assert items == []

    # ── 7. list_by_session_ids (batch lookup) ──

    def test_list_by_session_ids(
        self, bot_session_repository: BotSessionRepository, db_transaction
    ):
        sid1 = _generate_uuid()
        sid2 = _generate_uuid()
        sid3 = _generate_uuid()

        bot_session_repository.insert_session(
            bot_uuid=_generate_uuid(),
            invoker="user_1",
            session_id=sid1,
            req=None,
            result=None,
            err_msg=None,
            context=None,
            status="ACTIVE",
            device_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
        )
        bot_session_repository.insert_session(
            bot_uuid=_generate_uuid(),
            invoker="user_2",
            session_id=sid2,
            req=None,
            result=None,
            err_msg=None,
            context=None,
            status="PENDING",
            device_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
        )
        bot_session_repository.insert_session(
            bot_uuid=_generate_uuid(),
            invoker="user_3",
            session_id=sid3,
            req=None,
            result=None,
            err_msg=None,
            context=None,
            status="FAILED",
            device_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
        )

        records = bot_session_repository.list_by_session_ids([sid1, sid2, sid3])
        assert len(records) == 3
        found_ids = {r.session_id for r in records}
        assert found_ids == {sid1, sid2, sid3}

    def test_list_by_session_ids_empty_list(
        self, bot_session_repository: BotSessionRepository, db_transaction
    ):
        records = bot_session_repository.list_by_session_ids([])
        assert records == []

    def test_list_by_session_ids_partial_match(
        self, bot_session_repository: BotSessionRepository, db_transaction
    ):
        sid = _generate_uuid()

        bot_session_repository.insert_session(
            bot_uuid=_generate_uuid(),
            invoker="test_user",
            session_id=sid,
            req=None,
            result=None,
            err_msg=None,
            context=None,
            status="ACTIVE",
            device_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
        )

        records = bot_session_repository.list_by_session_ids([sid, "nonexistent-id"])
        assert len(records) == 1
        assert records[0].session_id == sid

    # ── 8. count_active_sessions_by_device ──

    def test_count_active_sessions_by_device_all_active(
        self, bot_session_repository: BotSessionRepository, db_transaction
    ):
        device_uuid = _generate_uuid()

        bot_session_repository.insert_session(
            bot_uuid=_generate_uuid(),
            invoker="user_a",
            session_id=_generate_uuid(),
            req=None,
            result=None,
            err_msg=None,
            context=None,
            status="PENDING",
            device_uuid=device_uuid,
            tenant=TEST_TENANT,
        )
        bot_session_repository.insert_session(
            bot_uuid=_generate_uuid(),
            invoker="user_b",
            session_id=_generate_uuid(),
            req=None,
            result=None,
            err_msg=None,
            context=None,
            status="RUNNING",
            device_uuid=device_uuid,
            tenant=TEST_TENANT,
        )

        count = bot_session_repository.count_active_sessions_by_device(
            device_uuid=device_uuid, tenant=TEST_TENANT
        )
        assert count == 2

    def test_count_active_sessions_by_device_mixed_status(
        self, bot_session_repository: BotSessionRepository, db_transaction
    ):
        device_uuid = _generate_uuid()

        bot_session_repository.insert_session(
            bot_uuid=_generate_uuid(),
            invoker="user_a",
            session_id=_generate_uuid(),
            req=None,
            result=None,
            err_msg=None,
            context=None,
            status="PENDING",
            device_uuid=device_uuid,
            tenant=TEST_TENANT,
        )
        bot_session_repository.insert_session(
            bot_uuid=_generate_uuid(),
            invoker="user_b",
            session_id=_generate_uuid(),
            req=None,
            result=None,
            err_msg=None,
            context=None,
            status="SUCCESS",
            device_uuid=device_uuid,
            tenant=TEST_TENANT,
        )
        bot_session_repository.insert_session(
            bot_uuid=_generate_uuid(),
            invoker="user_c",
            session_id=_generate_uuid(),
            req=None,
            result=None,
            err_msg=None,
            context=None,
            status="FAILED",
            device_uuid=device_uuid,
            tenant=TEST_TENANT,
        )

        count = bot_session_repository.count_active_sessions_by_device(
            device_uuid=device_uuid, tenant=TEST_TENANT
        )
        assert count == 1  # Only PENDING is counted

    def test_count_active_sessions_by_device_no_matches(
        self, bot_session_repository: BotSessionRepository, db_transaction
    ):
        count = bot_session_repository.count_active_sessions_by_device(
            device_uuid=_generate_uuid(), tenant=TEST_TENANT
        )
        assert count == 0

    def test_count_active_sessions_by_device_wrong_tenant(
        self, bot_session_repository: BotSessionRepository, db_transaction
    ):
        device_uuid = _generate_uuid()

        bot_session_repository.insert_session(
            bot_uuid=_generate_uuid(),
            invoker="user_a",
            session_id=_generate_uuid(),
            req=None,
            result=None,
            err_msg=None,
            context=None,
            status="PENDING",
            device_uuid=device_uuid,
            tenant=TEST_TENANT,
        )

        count = bot_session_repository.count_active_sessions_by_device(
            device_uuid=device_uuid, tenant="wrong_tenant"
        )
        assert count == 0

        count = bot_session_repository.count_active_sessions_by_device(
            device_uuid=device_uuid, tenant=TEST_TENANT
        )
        assert count == 1

    # ── 9. session_id uniqueness ──

    def test_session_id_uniqueness(
        self, bot_session_repository: BotSessionRepository, db_transaction
    ):
        session_id = _generate_uuid()

        bot_session_repository.insert_session(
            bot_uuid=_generate_uuid(),
            invoker="user_1",
            session_id=session_id,
            req=None,
            result=None,
            err_msg=None,
            context=None,
            status="ACTIVE",
            device_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
        )

        # Second insert with same session_id should raise an integrity error
        with pytest.raises(Exception):
            bot_session_repository.insert_session(
                bot_uuid=_generate_uuid(),
                invoker="user_2",
                session_id=session_id,
                req=None,
                result=None,
                err_msg=None,
                context=None,
                status="ACTIVE",
                device_uuid=_generate_uuid(),
                tenant=TEST_TENANT,
            )

    # ── 10. Full field round-trip ──

    def test_full_field_match_roundtrip(
        self, bot_session_repository: BotSessionRepository, db_transaction
    ):
        bot_uuid = _generate_uuid()
        session_id = _generate_uuid()
        device_uuid = _generate_uuid()
        req = {"method": "execute", "params": {"cmd": "echo hello"}}
        result = {"exit_code": 0, "stdout": "hello"}
        err_msg = "optional warning"
        context = {"phase": 1, "retry_count": 0}

        pk_id = bot_session_repository.insert_session(
            bot_uuid=bot_uuid,
            invoker="full_tester",
            session_id=session_id,
            req=req,
            result=result,
            err_msg=err_msg,
            context=context,
            status="RUNNING",
            device_uuid=device_uuid,
            tenant=TEST_TENANT,
        )
        assert pk_id > 0

        record = bot_session_repository.get_by_id(pk_id)
        assert record is not None
        assert isinstance(record, BotSessionRecord)
        assert record.id == pk_id
        assert record.bot_uuid == bot_uuid
        assert record.invoker == "full_tester"
        assert record.session_id == session_id
        assert record.req == req
        assert record.result == result
        assert record.err_msg == err_msg
        assert record.context == context
        assert record.status == "RUNNING"
        assert record.device_uuid == device_uuid
        assert record.env == TEST_ENV
        assert record.tenant == TEST_TENANT
        assert isinstance(record.gmt_create, datetime)
        assert isinstance(record.gmt_modified, datetime)

    # ── 10. list_by_time_range ──

    def test_list_by_time_range_basic(self, bot_session_repository, db_transaction):
        bot_uuid = _generate_uuid()
        before_insert = datetime.now(UTC)

        bot_session_repository.insert_session(
            bot_uuid=bot_uuid,
            invoker="user_a",
            session_id=_generate_uuid(),
            req=None,
            result=None,
            err_msg=None,
            context=None,
            status="ACTIVE",
            device_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
        )

        after_first = datetime.now(UTC)

        bot_session_repository.insert_session(
            bot_uuid=bot_uuid,
            invoker="user_b",
            session_id=_generate_uuid(),
            req=None,
            result=None,
            err_msg=None,
            context=None,
            status="PENDING",
            device_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
        )

        after_second = datetime.now(UTC)

        # Wide range covering all inserts
        records = bot_session_repository.list_by_time_range(
            start_time=TIME_MIN,
            end_time=TIME_MAX,
        )
        assert len(records) >= 2

    def test_list_by_time_range_with_bot_filter(
        self, bot_session_repository, db_transaction
    ):
        bot_a = _generate_uuid()
        bot_b = _generate_uuid()
        before = datetime.now(UTC)

        bot_session_repository.insert_session(
            bot_uuid=bot_a,
            invoker="user_1",
            session_id=_generate_uuid(),
            req=None,
            result=None,
            err_msg=None,
            context=None,
            status="ACTIVE",
            device_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
        )
        bot_session_repository.insert_session(
            bot_uuid=bot_b,
            invoker="user_2",
            session_id=_generate_uuid(),
            req=None,
            result=None,
            err_msg=None,
            context=None,
            status="PENDING",
            device_uuid=_generate_uuid(),
            tenant=TEST_TENANT,
        )

        after = datetime.now(UTC)

        # Filter by bot_a
        records = bot_session_repository.list_by_time_range(
            start_time=TIME_MIN,
            end_time=TIME_MAX,
            bot_uuid=bot_a,
        )
        assert len(records) == 1
        assert records[0].bot_uuid == bot_a

        # Filter by bot_b
        records = bot_session_repository.list_by_time_range(
            start_time=TIME_MIN,
            end_time=TIME_MAX,
            bot_uuid=bot_b,
        )
        assert len(records) == 1
        assert records[0].bot_uuid == bot_b

    def test_list_by_time_range_empty(self, bot_session_repository, db_transaction):
        now = datetime.now(UTC)
        records = bot_session_repository.list_by_time_range(
            start_time=now - timedelta(days=365),
            end_time=now - timedelta(days=364),
        )
        assert records == []

    # ── 11. list_by_bot_device_invoker ──

    def test_list_by_bot_device_invoker_basic(
        self, bot_session_repository, db_transaction
    ):
        bot_a = _generate_uuid()
        bot_b = _generate_uuid()
        dev_x = _generate_uuid()
        dev_y = _generate_uuid()

        before = datetime.now(UTC)

        # Session 1: bot_a + dev_x + invoker_alice
        bot_session_repository.insert_session(
            bot_uuid=bot_a,
            invoker="alice",
            session_id=_generate_uuid(),
            req=None,
            result=None,
            err_msg=None,
            context=None,
            status="ACTIVE",
            device_uuid=dev_x,
            tenant=TEST_TENANT,
        )

        # Session 2: bot_a + dev_x + invoker_alice (same combo, different session)
        bot_session_repository.insert_session(
            bot_uuid=bot_a,
            invoker="alice",
            session_id=_generate_uuid(),
            req=None,
            result=None,
            err_msg=None,
            context=None,
            status="PENDING",
            device_uuid=dev_x,
            tenant=TEST_TENANT,
        )

        # Session 3: bot_a + dev_y + invoker_alice (different device)
        bot_session_repository.insert_session(
            bot_uuid=bot_a,
            invoker="alice",
            session_id=_generate_uuid(),
            req=None,
            result=None,
            err_msg=None,
            context=None,
            status="ACTIVE",
            device_uuid=dev_y,
            tenant=TEST_TENANT,
        )

        # Session 4: bot_b + dev_x + invoker_alice (different bot)
        bot_session_repository.insert_session(
            bot_uuid=bot_b,
            invoker="alice",
            session_id=_generate_uuid(),
            req=None,
            result=None,
            err_msg=None,
            context=None,
            status="ACTIVE",
            device_uuid=dev_x,
            tenant=TEST_TENANT,
        )

        # Session 5: bot_a + dev_x + invoker_bob (different invoker)
        bot_session_repository.insert_session(
            bot_uuid=bot_a,
            invoker="bob",
            session_id=_generate_uuid(),
            req=None,
            result=None,
            err_msg=None,
            context=None,
            status="ACTIVE",
            device_uuid=dev_x,
            tenant=TEST_TENANT,
        )

        after = datetime.now(UTC)

        time_start = TIME_MIN
        time_end = TIME_MAX

        # Query bot_a + dev_x + alice
        records = bot_session_repository.list_by_bot_device_invoker(
            bot_uuid=bot_a,
            device_uuid=dev_x,
            invoker="alice",
            start_time=time_start,
            end_time=time_end,
        )
        assert len(records) == 2
        for r in records:
            assert r.bot_uuid == bot_a
            assert r.device_uuid == dev_x
            assert r.invoker == "alice"

        # Query bot_b + dev_x + alice
        records = bot_session_repository.list_by_bot_device_invoker(
            bot_uuid=bot_b,
            device_uuid=dev_x,
            invoker="alice",
            start_time=time_start,
            end_time=time_end,
        )
        assert len(records) == 1
        assert records[0].bot_uuid == bot_b

        # Query bot_a + dev_x + bob
        records = bot_session_repository.list_by_bot_device_invoker(
            bot_uuid=bot_a,
            device_uuid=dev_x,
            invoker="bob",
            start_time=time_start,
            end_time=time_end,
        )
        assert len(records) == 1
        assert records[0].invoker == "bob"

    def test_list_by_bot_device_invoker_device_none(
        self, bot_session_repository, db_transaction
    ):
        bot_uuid = _generate_uuid()
        dev_x = _generate_uuid()
        dev_y = _generate_uuid()

        before = datetime.now(UTC)

        # Session on dev_x
        bot_session_repository.insert_session(
            bot_uuid=bot_uuid,
            invoker="alice",
            session_id=_generate_uuid(),
            req=None,
            result=None,
            err_msg=None,
            context=None,
            status="ACTIVE",
            device_uuid=dev_x,
            tenant=TEST_TENANT,
        )

        # Session on dev_y, same bot+invoker
        bot_session_repository.insert_session(
            bot_uuid=bot_uuid,
            invoker="alice",
            session_id=_generate_uuid(),
            req=None,
            result=None,
            err_msg=None,
            context=None,
            status="PENDING",
            device_uuid=dev_y,
            tenant=TEST_TENANT,
        )

        # Session for different invoker (should not match)
        bot_session_repository.insert_session(
            bot_uuid=bot_uuid,
            invoker="bob",
            session_id=_generate_uuid(),
            req=None,
            result=None,
            err_msg=None,
            context=None,
            status="ACTIVE",
            device_uuid=dev_x,
            tenant=TEST_TENANT,
        )

        after = datetime.now(UTC)

        time_start = TIME_MIN
        time_end = TIME_MAX

        # device_uuid=None should return sessions for all devices
        records = bot_session_repository.list_by_bot_device_invoker(
            bot_uuid=bot_uuid,
            device_uuid=None,
            invoker="alice",
            start_time=time_start,
            end_time=time_end,
        )
        assert len(records) == 2
        device_uuids = {r.device_uuid for r in records}
        assert device_uuids == {dev_x, dev_y}
        for r in records:
            assert r.bot_uuid == bot_uuid
            assert r.invoker == "alice"
