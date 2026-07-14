from __future__ import annotations

from datetime import datetime
from uuid import uuid4

import pytest

from secbaas.community.bootstrap import get_container
from secbaas.community.core.repository.local_user_machine import (
    LocalUserMachineRecord,
    LocalUserMachineRepository,
)
from secbaas.community.core.utils.env_utils import get_current_env

pytestmark = pytest.mark.integration

TEST_ENV = get_current_env()


def _generate_uuid() -> str:
    return uuid4().hex


class TestLocalUserMachineSqliteOrmEquivalence:
    def test_insert_and_get_roundtrip(self):
        repo: LocalUserMachineRepository = (
            get_container().repository.local_user_machine_repository()
        )
        machine_id = _generate_uuid()
        user_id = _generate_uuid()

        record_id = repo.insert_machine(
            template_id=1,
            user_id=user_id,
            machine_id=machine_id,
            machine_info={"os": "linux"},
            last_heartbeat=datetime.now(),
            connected_server_instance=f"srv-{_generate_uuid()[:8]}",
            status="OFFLINE",
            env=TEST_ENV,
        )
        assert record_id > 0

        record = repo.get_by_machine_id(machine_id, TEST_ENV)
        assert isinstance(record, LocalUserMachineRecord)
        assert record.machine_id == machine_id
        assert record.user_id == user_id
        assert record.status == "OFFLINE"
        assert record.gmt_create is not None
        assert record.gmt_modified is not None

    def test_get_by_machine_id_nonexistent(self):
        repo = get_container().repository.local_user_machine_repository()
        assert (
            repo.get_by_machine_id(f"nonexistent_{_generate_uuid()}", TEST_ENV) is None
        )

    def test_deep_update_heartbeat(self):
        repo = get_container().repository.local_user_machine_repository()
        machine_id = _generate_uuid()
        old_heartbeat = datetime(2024, 1, 1, 0, 0, 0)

        repo.insert_machine(
            template_id=1,
            user_id=_generate_uuid(),
            machine_id=machine_id,
            machine_info={"os": "linux"},
            last_heartbeat=old_heartbeat,
            connected_server_instance=f"srv-{_generate_uuid()[:8]}",
            status="OFFLINE",
            env=TEST_ENV,
        )
        new_heartbeat = datetime(2025, 6, 1, 0, 0, 0)
        repo.update_heartbeat(machine_id, TEST_ENV, new_heartbeat)
        record = repo.get_by_machine_id(machine_id, TEST_ENV)
        assert record is not None
        assert record.last_heartbeat is not None
        assert record.last_heartbeat > old_heartbeat

    def test_deep_update_status(self):
        repo = get_container().repository.local_user_machine_repository()
        machine_id = _generate_uuid()

        repo.insert_machine(
            template_id=1,
            user_id=_generate_uuid(),
            machine_id=machine_id,
            machine_info={"os": "linux"},
            last_heartbeat=datetime.now(),
            connected_server_instance=f"srv-{_generate_uuid()[:8]}",
            status="OFFLINE",
            env=TEST_ENV,
        )
        repo.update_status(machine_id, TEST_ENV, "ONLINE")
        record = repo.get_by_machine_id(machine_id, TEST_ENV)
        assert record is not None
        assert record.status == "ONLINE"

    def test_insert_machine_and_get_by_machine_id(self):
        repo: LocalUserMachineRepository = (
            get_container().repository.local_user_machine_repository()
        )
        machine_id = _generate_uuid()
        user_id = _generate_uuid()
        instance = f"server-{_generate_uuid()[:8]}"

        record_id = repo.insert_machine(
            template_id=1,
            user_id=user_id,
            machine_id=machine_id,
            machine_info={"os": "linux", "cpu": "4"},
            last_heartbeat=datetime.now(),
            connected_server_instance=instance,
            status="OFFLINE",
            env=TEST_ENV,
        )
        assert record_id > 0

        record = repo.get_by_machine_id(machine_id, TEST_ENV)
        assert isinstance(record, LocalUserMachineRecord)
        assert record.id == record_id
        assert record.machine_id == machine_id
        assert record.user_id == user_id
        assert record.template_id == 1
        assert record.connected_server_instance == instance
        assert record.status == "OFFLINE"
        assert record.machine_info == {"os": "linux", "cpu": "4"}
        assert record.env == TEST_ENV
        assert record.gmt_create is not None
        assert record.gmt_modified is not None

    def test_list_by_user_id(self):
        repo = get_container().repository.local_user_machine_repository()
        user_id = _generate_uuid()

        repo.insert_machine(
            template_id=1,
            user_id=user_id,
            machine_id=_generate_uuid(),
            machine_info={"os": "linux", "cpu": "4"},
            last_heartbeat=datetime.now(),
            connected_server_instance=f"server-{_generate_uuid()[:8]}",
            status="OFFLINE",
            env=TEST_ENV,
        )

        records = repo.list_by_user_id(user_id, TEST_ENV)
        assert len(records) >= 1
        assert all(r.user_id == user_id for r in records)

    def test_list_by_user_id_empty(self):
        repo = get_container().repository.local_user_machine_repository()
        records = repo.list_by_user_id(f"nonexistent_{_generate_uuid()}", TEST_ENV)
        assert records == []

    def test_update_heartbeat(self):
        repo = get_container().repository.local_user_machine_repository()
        machine_id = _generate_uuid()
        old_heartbeat = datetime(2024, 1, 1, 0, 0, 0)

        repo.insert_machine(
            template_id=1,
            user_id=_generate_uuid(),
            machine_id=machine_id,
            machine_info={"os": "linux", "cpu": "4"},
            last_heartbeat=old_heartbeat,
            connected_server_instance=f"server-{_generate_uuid()[:8]}",
            status="OFFLINE",
            env=TEST_ENV,
        )
        new_heartbeat = datetime(2024, 6, 1, 0, 0, 0)
        repo.update_heartbeat(machine_id, TEST_ENV, new_heartbeat)

        record = repo.get_by_machine_id(machine_id, TEST_ENV)
        assert record is not None
        assert record.last_heartbeat is not None
        assert record.last_heartbeat > old_heartbeat

    def test_update_status(self):
        repo = get_container().repository.local_user_machine_repository()
        machine_id = _generate_uuid()

        repo.insert_machine(
            template_id=1,
            user_id=_generate_uuid(),
            machine_id=machine_id,
            machine_info={"os": "linux", "cpu": "4"},
            last_heartbeat=datetime.now(),
            connected_server_instance=f"server-{_generate_uuid()[:8]}",
            status="OFFLINE",
            env=TEST_ENV,
        )
        repo.update_status(machine_id, TEST_ENV, "ONLINE")

        record = repo.get_by_machine_id(machine_id, TEST_ENV)
        assert record is not None
        assert record.status == "ONLINE"

    def test_update_instance(self):
        repo = get_container().repository.local_user_machine_repository()
        machine_id = _generate_uuid()
        new_instance = f"new-server-{_generate_uuid()[:8]}"

        repo.insert_machine(
            template_id=1,
            user_id=_generate_uuid(),
            machine_id=machine_id,
            machine_info={"os": "linux", "cpu": "4"},
            last_heartbeat=datetime.now(),
            connected_server_instance=f"server-{_generate_uuid()[:8]}",
            status="OFFLINE",
            env=TEST_ENV,
        )
        repo.update_instance(machine_id, TEST_ENV, new_instance)

        record = repo.get_by_machine_id(machine_id, TEST_ENV)
        assert record is not None
        assert record.connected_server_instance == new_instance
