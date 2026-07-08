from datetime import datetime
from uuid import uuid4

import pytest

from secbaas.core.repository.local_user_machine import (
    LocalUserMachineRecord,
    LocalUserMachineRepository,
)
from secbaas.core.utils.env_utils import get_current_env

mysql_connector = pytest.importorskip("mysql.connector")

pytestmark = pytest.mark.integration

TEST_ENV = get_current_env()


def _generate_uuid() -> str:
    return uuid4().hex


def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


class TestLocalUserMachineRepositoryProtocol:
    """Integration tests for LocalUserMachineRepository Protocol against ZDAS MySQL.

    Uses ONLY the LocalUserMachineRepository Protocol — no OrmLocalUserMachineRepository
    references allowed. db_transaction ensures all changes are rolled back after each test.
    """

    # ── 1. insert_machine + get_by_machine_id ──

    def test_insert_and_get_by_machine_id(
        self,
        local_user_machine_repository: LocalUserMachineRepository,
        db_transaction,
    ):
        machine_id = _generate_uuid()
        user_id = _generate_uuid()
        instance = f"server-{_generate_uuid()[:8]}"
        now = _now()
        machine_info = {"os": "linux", "cpu": "4", "ram": "16GB"}

        record_id = local_user_machine_repository.insert_machine(
            template_id=1,
            user_id=user_id,
            machine_id=machine_id,
            machine_info=machine_info,
            last_heartbeat=now,
            connected_server_instance=instance,
            status="OFFLINE",
            env=TEST_ENV,
        )
        assert record_id > 0

        record = local_user_machine_repository.get_by_machine_id(machine_id, TEST_ENV)
        assert record is not None
        assert isinstance(record, LocalUserMachineRecord)
        assert record.id == record_id
        assert record.template_id == 1
        assert record.user_id == user_id
        assert record.machine_id == machine_id
        assert record.machine_info == machine_info
        assert record.last_heartbeat == now
        assert record.connected_server_instance == instance
        assert record.status == "OFFLINE"
        assert record.env == TEST_ENV
        assert record.gmt_create is not None
        assert record.gmt_modified is not None
        assert record.connected_route_info is None

    def test_insert_machine_default_fields(
        self,
        local_user_machine_repository: LocalUserMachineRepository,
        db_transaction,
    ):
        machine_id = _generate_uuid()
        user_id = _generate_uuid()
        now = _now()

        record_id = local_user_machine_repository.insert_machine(
            template_id=1,
            user_id=user_id,
            machine_id=machine_id,
            machine_info=None,
            last_heartbeat=now,
            connected_server_instance="default-instance",
            status="OFFLINE",
            env=TEST_ENV,
        )
        assert record_id > 0

        record = local_user_machine_repository.get_by_machine_id(machine_id, TEST_ENV)
        assert record is not None
        assert record.machine_info == {}
        assert record.status == "OFFLINE"

    # ── 2. get_by_machine_id returns None for missing ──

    def test_get_by_machine_id_returns_none_for_missing(
        self,
        local_user_machine_repository: LocalUserMachineRepository,
        db_transaction,
    ):
        result = local_user_machine_repository.get_by_machine_id(
            "nonexistent-machine-id", TEST_ENV
        )
        assert result is None

    def test_get_by_machine_id_returns_none_for_wrong_env(
        self,
        local_user_machine_repository: LocalUserMachineRepository,
        db_transaction,
    ):
        machine_id = _generate_uuid()
        user_id = _generate_uuid()
        now = _now()

        local_user_machine_repository.insert_machine(
            template_id=1,
            user_id=user_id,
            machine_id=machine_id,
            machine_info=None,
            last_heartbeat=now,
            connected_server_instance="test-instance",
            status="OFFLINE",
            env=TEST_ENV,
        )

        wrong_env = "pre" if TEST_ENV != "pre" else "prod"
        result = local_user_machine_repository.get_by_machine_id(machine_id, wrong_env)
        assert result is None

    # ── 3. list_by_user_id ──

    def test_list_by_user_id(
        self,
        local_user_machine_repository: LocalUserMachineRepository,
        db_transaction,
    ):
        user_id = _generate_uuid()
        now = _now()

        machine_ids = []
        for i in range(3):
            mid = _generate_uuid()
            machine_ids.append(mid)
            local_user_machine_repository.insert_machine(
                template_id=1,
                user_id=user_id,
                machine_id=mid,
                machine_info={"index": i},
                last_heartbeat=now,
                connected_server_instance=f"instance-{i}",
                status="ONLINE" if i % 2 == 0 else "OFFLINE",
                env=TEST_ENV,
            )

        records = local_user_machine_repository.list_by_user_id(user_id, TEST_ENV)
        assert len(records) == 3
        returned_ids = {r.machine_id for r in records}
        assert returned_ids == set(machine_ids)
        for record in records:
            assert record.env == TEST_ENV

    def test_list_by_user_id_returns_empty_for_unknown_user(
        self,
        local_user_machine_repository: LocalUserMachineRepository,
        db_transaction,
    ):
        records = local_user_machine_repository.list_by_user_id(
            "nonexistent-user", TEST_ENV
        )
        assert records == []

    def test_list_by_user_id_env_isolation(
        self,
        local_user_machine_repository: LocalUserMachineRepository,
        db_transaction,
    ):
        user_id = _generate_uuid()
        now = _now()
        other_env = "pre" if TEST_ENV != "pre" else "prod"

        mid_a = _generate_uuid()
        local_user_machine_repository.insert_machine(
            template_id=1,
            user_id=user_id,
            machine_id=mid_a,
            machine_info=None,
            last_heartbeat=now,
            connected_server_instance="instance-a",
            status="ONLINE",
            env=TEST_ENV,
        )
        mid_b = _generate_uuid()
        local_user_machine_repository.insert_machine(
            template_id=1,
            user_id=user_id,
            machine_id=mid_b,
            machine_info=None,
            last_heartbeat=now,
            connected_server_instance="instance-b",
            status="ONLINE",
            env=other_env,
        )

        records_test = local_user_machine_repository.list_by_user_id(user_id, TEST_ENV)
        assert len(records_test) == 1
        assert records_test[0].machine_id == mid_a

        records_other = local_user_machine_repository.list_by_user_id(
            user_id, other_env
        )
        assert len(records_other) == 1
        assert records_other[0].machine_id == mid_b

    # ── 4. update_heartbeat ──

    def test_update_heartbeat(
        self,
        local_user_machine_repository: LocalUserMachineRepository,
        db_transaction,
    ):
        machine_id = _generate_uuid()
        user_id = _generate_uuid()
        original_time = datetime(2025, 1, 1, 12, 0, 0)

        local_user_machine_repository.insert_machine(
            template_id=1,
            user_id=user_id,
            machine_id=machine_id,
            machine_info=None,
            last_heartbeat=original_time,
            connected_server_instance="test-instance",
            status="ONLINE",
            env=TEST_ENV,
        )

        new_time = _now()
        local_user_machine_repository.update_heartbeat(machine_id, TEST_ENV, new_time)

        record = local_user_machine_repository.get_by_machine_id(machine_id, TEST_ENV)
        assert record is not None
        assert record.last_heartbeat == new_time

    def test_update_heartbeat_missing_machine_no_error(
        self,
        local_user_machine_repository: LocalUserMachineRepository,
        db_transaction,
    ):
        # Should not raise — update on non-existent machine is a no-op
        local_user_machine_repository.update_heartbeat(
            "nonexistent-machine", TEST_ENV, _now()
        )

    # ── 5. update_status ──

    def test_update_status_online_to_offline(
        self,
        local_user_machine_repository: LocalUserMachineRepository,
        db_transaction,
    ):
        machine_id = _generate_uuid()
        user_id = _generate_uuid()
        now = _now()

        local_user_machine_repository.insert_machine(
            template_id=1,
            user_id=user_id,
            machine_id=machine_id,
            machine_info=None,
            last_heartbeat=now,
            connected_server_instance="test-instance",
            status="ONLINE",
            env=TEST_ENV,
        )

        local_user_machine_repository.update_status(machine_id, TEST_ENV, "OFFLINE")

        record = local_user_machine_repository.get_by_machine_id(machine_id, TEST_ENV)
        assert record is not None
        assert record.status == "OFFLINE"

    def test_update_status_offline_to_online(
        self,
        local_user_machine_repository: LocalUserMachineRepository,
        db_transaction,
    ):
        machine_id = _generate_uuid()
        user_id = _generate_uuid()
        now = _now()

        local_user_machine_repository.insert_machine(
            template_id=1,
            user_id=user_id,
            machine_id=machine_id,
            machine_info=None,
            last_heartbeat=now,
            connected_server_instance="test-instance",
            status="OFFLINE",
            env=TEST_ENV,
        )

        local_user_machine_repository.update_status(machine_id, TEST_ENV, "ONLINE")

        record = local_user_machine_repository.get_by_machine_id(machine_id, TEST_ENV)
        assert record is not None
        assert record.status == "ONLINE"

    def test_update_status_to_disabled(
        self,
        local_user_machine_repository: LocalUserMachineRepository,
        db_transaction,
    ):
        machine_id = _generate_uuid()
        user_id = _generate_uuid()
        now = _now()

        local_user_machine_repository.insert_machine(
            template_id=1,
            user_id=user_id,
            machine_id=machine_id,
            machine_info=None,
            last_heartbeat=now,
            connected_server_instance="test-instance",
            status="ONLINE",
            env=TEST_ENV,
        )

        local_user_machine_repository.update_status(machine_id, TEST_ENV, "DISABLED")

        record = local_user_machine_repository.get_by_machine_id(machine_id, TEST_ENV)
        assert record is not None
        assert record.status == "DISABLED"

    def test_update_status_missing_machine_no_error(
        self,
        local_user_machine_repository: LocalUserMachineRepository,
        db_transaction,
    ):
        # Should not raise — update on non-existent machine is a no-op
        local_user_machine_repository.update_status(
            "nonexistent-machine", TEST_ENV, "OFFLINE"
        )

    # ── 6. update_instance ──

    def test_update_instance(
        self,
        local_user_machine_repository: LocalUserMachineRepository,
        db_transaction,
    ):
        machine_id = _generate_uuid()
        user_id = _generate_uuid()
        now = _now()

        local_user_machine_repository.insert_machine(
            template_id=1,
            user_id=user_id,
            machine_id=machine_id,
            machine_info=None,
            last_heartbeat=now,
            connected_server_instance="old-instance",
            status="ONLINE",
            env=TEST_ENV,
        )

        local_user_machine_repository.update_instance(
            machine_id, TEST_ENV, "new-instance"
        )

        record = local_user_machine_repository.get_by_machine_id(machine_id, TEST_ENV)
        assert record is not None
        assert record.connected_server_instance == "new-instance"

    def test_update_instance_missing_machine_no_error(
        self,
        local_user_machine_repository: LocalUserMachineRepository,
        db_transaction,
    ):
        # Should not raise — update on non-existent machine is a no-op
        local_user_machine_repository.update_instance(
            "nonexistent-machine", TEST_ENV, "some-instance"
        )

    # ── 7. update_machine_info ──

    def test_update_machine_info(
        self,
        local_user_machine_repository: LocalUserMachineRepository,
        db_transaction,
    ):
        machine_id = _generate_uuid()
        user_id = _generate_uuid()
        now = _now()

        local_user_machine_repository.insert_machine(
            template_id=1,
            user_id=user_id,
            machine_id=machine_id,
            machine_info={"initial": "data"},
            last_heartbeat=now,
            connected_server_instance="test-instance",
            status="ONLINE",
            env=TEST_ENV,
        )

        new_info = {"os": "darwin", "cpu": "8", "ram": "32GB", "disk": "1TB"}
        local_user_machine_repository.update_machine_info(
            machine_id, TEST_ENV, new_info
        )

        record = local_user_machine_repository.get_by_machine_id(machine_id, TEST_ENV)
        assert record is not None
        assert record.machine_info == new_info

    def test_update_machine_info_to_none(
        self,
        local_user_machine_repository: LocalUserMachineRepository,
        db_transaction,
    ):
        machine_id = _generate_uuid()
        user_id = _generate_uuid()
        now = _now()

        local_user_machine_repository.insert_machine(
            template_id=1,
            user_id=user_id,
            machine_id=machine_id,
            machine_info={"initial": "data"},
            last_heartbeat=now,
            connected_server_instance="test-instance",
            status="ONLINE",
            env=TEST_ENV,
        )

        local_user_machine_repository.update_machine_info(machine_id, TEST_ENV, None)

        record = local_user_machine_repository.get_by_machine_id(machine_id, TEST_ENV)
        assert record is not None
        assert record.machine_info == {}

    def test_update_machine_info_missing_machine_no_error(
        self,
        local_user_machine_repository: LocalUserMachineRepository,
        db_transaction,
    ):
        # Should not raise — update on non-existent machine is a no-op
        local_user_machine_repository.update_machine_info(
            "nonexistent-machine", TEST_ENV, {"key": "value"}
        )

    # ── 8. update_route_info ──

    def test_update_route_info(
        self,
        local_user_machine_repository: LocalUserMachineRepository,
        db_transaction,
    ):
        machine_id = _generate_uuid()
        user_id = _generate_uuid()
        now = _now()

        local_user_machine_repository.insert_machine(
            template_id=1,
            user_id=user_id,
            machine_id=machine_id,
            machine_info=None,
            last_heartbeat=now,
            connected_server_instance="test-instance",
            status="ONLINE",
            env=TEST_ENV,
        )

        route_info = {
            "worker_pid": 12345,
            "socket_path": "/tmp/worker.sock",
            "host": "localhost",
        }
        local_user_machine_repository.update_route_info(
            machine_id, TEST_ENV, route_info
        )

        record = local_user_machine_repository.get_by_machine_id(machine_id, TEST_ENV)
        assert record is not None
        assert record.connected_route_info == route_info

    def test_update_route_info_overwrite(
        self,
        local_user_machine_repository: LocalUserMachineRepository,
        db_transaction,
    ):
        machine_id = _generate_uuid()
        user_id = _generate_uuid()
        now = _now()

        local_user_machine_repository.insert_machine(
            template_id=1,
            user_id=user_id,
            machine_id=machine_id,
            machine_info=None,
            last_heartbeat=now,
            connected_server_instance="test-instance",
            status="ONLINE",
            env=TEST_ENV,
        )

        local_user_machine_repository.update_route_info(
            machine_id, TEST_ENV, {"worker_pid": 111, "socket_path": "/old.sock"}
        )
        local_user_machine_repository.update_route_info(
            machine_id, TEST_ENV, {"worker_pid": 222, "socket_path": "/new.sock"}
        )

        record = local_user_machine_repository.get_by_machine_id(machine_id, TEST_ENV)
        assert record is not None
        assert record.connected_route_info == {
            "worker_pid": 222,
            "socket_path": "/new.sock",
        }

    def test_update_route_info_missing_machine_no_error(
        self,
        local_user_machine_repository: LocalUserMachineRepository,
        db_transaction,
    ):
        # Should not raise — update on non-existent machine is a no-op
        local_user_machine_repository.update_route_info(
            "nonexistent-machine", TEST_ENV, {"worker_pid": 999}
        )

    # ── 9. clear_route_info ──

    def test_clear_route_info(
        self,
        local_user_machine_repository: LocalUserMachineRepository,
        db_transaction,
    ):
        machine_id = _generate_uuid()
        user_id = _generate_uuid()
        now = _now()

        local_user_machine_repository.insert_machine(
            template_id=1,
            user_id=user_id,
            machine_id=machine_id,
            machine_info=None,
            last_heartbeat=now,
            connected_server_instance="test-instance",
            status="ONLINE",
            env=TEST_ENV,
        )
        local_user_machine_repository.update_route_info(
            machine_id, TEST_ENV, {"worker_pid": 12345, "socket_path": "/tmp/ws.sock"}
        )

        local_user_machine_repository.clear_route_info(machine_id, TEST_ENV)

        record = local_user_machine_repository.get_by_machine_id(machine_id, TEST_ENV)
        assert record is not None
        assert record.connected_route_info is None

    def test_clear_route_info_already_none(
        self,
        local_user_machine_repository: LocalUserMachineRepository,
        db_transaction,
    ):
        machine_id = _generate_uuid()
        user_id = _generate_uuid()
        now = _now()

        local_user_machine_repository.insert_machine(
            template_id=1,
            user_id=user_id,
            machine_id=machine_id,
            machine_info=None,
            last_heartbeat=now,
            connected_server_instance="test-instance",
            status="ONLINE",
            env=TEST_ENV,
        )

        # Clearing when route_info is already None should not error
        local_user_machine_repository.clear_route_info(machine_id, TEST_ENV)

        record = local_user_machine_repository.get_by_machine_id(machine_id, TEST_ENV)
        assert record is not None
        assert record.connected_route_info is None

    def test_clear_route_info_missing_machine_no_error(
        self,
        local_user_machine_repository: LocalUserMachineRepository,
        db_transaction,
    ):
        # Should not raise — clear on non-existent machine is a no-op
        local_user_machine_repository.clear_route_info("nonexistent-machine", TEST_ENV)

    # ── 10. get_route_info ──

    def test_get_route_info(
        self,
        local_user_machine_repository: LocalUserMachineRepository,
        db_transaction,
    ):
        machine_id = _generate_uuid()
        user_id = _generate_uuid()
        now = _now()

        local_user_machine_repository.insert_machine(
            template_id=1,
            user_id=user_id,
            machine_id=machine_id,
            machine_info=None,
            last_heartbeat=now,
            connected_server_instance="test-instance",
            status="ONLINE",
            env=TEST_ENV,
        )

        route_info = {
            "worker_pid": 12345,
            "socket_path": "/tmp/worker.sock",
        }
        local_user_machine_repository.update_route_info(
            machine_id, TEST_ENV, route_info
        )

        result = local_user_machine_repository.get_route_info(machine_id, TEST_ENV)
        assert result == route_info

    def test_get_route_info_returns_none_when_not_set(
        self,
        local_user_machine_repository: LocalUserMachineRepository,
        db_transaction,
    ):
        machine_id = _generate_uuid()
        user_id = _generate_uuid()
        now = _now()

        local_user_machine_repository.insert_machine(
            template_id=1,
            user_id=user_id,
            machine_id=machine_id,
            machine_info=None,
            last_heartbeat=now,
            connected_server_instance="test-instance",
            status="ONLINE",
            env=TEST_ENV,
        )

        result = local_user_machine_repository.get_route_info(machine_id, TEST_ENV)
        assert result is None

    def test_get_route_info_returns_none_after_clear(
        self,
        local_user_machine_repository: LocalUserMachineRepository,
        db_transaction,
    ):
        machine_id = _generate_uuid()
        user_id = _generate_uuid()
        now = _now()

        local_user_machine_repository.insert_machine(
            template_id=1,
            user_id=user_id,
            machine_id=machine_id,
            machine_info=None,
            last_heartbeat=now,
            connected_server_instance="test-instance",
            status="ONLINE",
            env=TEST_ENV,
        )
        local_user_machine_repository.update_route_info(
            machine_id, TEST_ENV, {"worker_pid": 555}
        )
        local_user_machine_repository.clear_route_info(machine_id, TEST_ENV)

        result = local_user_machine_repository.get_route_info(machine_id, TEST_ENV)
        assert result is None

    def test_get_route_info_returns_none_for_missing_machine(
        self,
        local_user_machine_repository: LocalUserMachineRepository,
        db_transaction,
    ):
        result = local_user_machine_repository.get_route_info(
            "nonexistent-machine", TEST_ENV
        )
        assert result is None

    # ── 11. update sequence (combined) ──

    def test_full_update_sequence(
        self,
        local_user_machine_repository: LocalUserMachineRepository,
        db_transaction,
    ):
        machine_id = _generate_uuid()
        user_id = _generate_uuid()
        now = _now()

        local_user_machine_repository.insert_machine(
            template_id=1,
            user_id=user_id,
            machine_id=machine_id,
            machine_info={"initial": True},
            last_heartbeat=now,
            connected_server_instance="instance-v1",
            status="OFFLINE",
            env=TEST_ENV,
        )

        # Sequence of updates simulating a real machine lifecycle
        heartbeat_1 = datetime(2025, 6, 1, 10, 0, 0)
        local_user_machine_repository.update_heartbeat(
            machine_id, TEST_ENV, heartbeat_1
        )
        local_user_machine_repository.update_status(machine_id, TEST_ENV, "ONLINE")
        local_user_machine_repository.update_instance(
            machine_id, TEST_ENV, "instance-v2"
        )
        local_user_machine_repository.update_machine_info(
            machine_id, TEST_ENV, {"os": "linux", "version": "2.0"}
        )
        local_user_machine_repository.update_route_info(
            machine_id,
            TEST_ENV,
            {"worker_pid": 9999, "socket_path": "/app/worker.sock"},
        )

        record = local_user_machine_repository.get_by_machine_id(machine_id, TEST_ENV)
        assert record is not None
        assert record.last_heartbeat == heartbeat_1
        assert record.status == "ONLINE"
        assert record.connected_server_instance == "instance-v2"
        assert record.machine_info == {"os": "linux", "version": "2.0"}
        assert record.connected_route_info == {
            "worker_pid": 9999,
            "socket_path": "/app/worker.sock",
        }

        # Route info retrieval
        route = local_user_machine_repository.get_route_info(machine_id, TEST_ENV)
        assert route == {"worker_pid": 9999, "socket_path": "/app/worker.sock"}

        # Clear and verify
        local_user_machine_repository.clear_route_info(machine_id, TEST_ENV)
        assert (
            local_user_machine_repository.get_route_info(machine_id, TEST_ENV) is None
        )

        # Further updates after clear
        heartbeat_2 = datetime(2025, 6, 1, 11, 0, 0)
        local_user_machine_repository.update_heartbeat(
            machine_id, TEST_ENV, heartbeat_2
        )
        local_user_machine_repository.update_status(machine_id, TEST_ENV, "OFFLINE")

        record2 = local_user_machine_repository.get_by_machine_id(machine_id, TEST_ENV)
        assert record2 is not None
        assert record2.last_heartbeat == heartbeat_2
        assert record2.status == "OFFLINE"
