from __future__ import annotations

from uuid import uuid4

import pytest

from secbaas.bootstrap import get_container
from secbaas.core.repository.device_binding import (
    DeviceBindingRecord,
    DeviceBindingRepository,
    DeviceBindingStatus,
)
from secbaas.core.utils.env_utils import get_current_env

pytestmark = pytest.mark.integration

TEST_ENV = get_current_env()


def _generate_uuid() -> str:
    return uuid4().hex


class TestDeviceBindingSqliteOrmEquivalence:
    def test_insert_and_get_roundtrip(self):
        repo: DeviceBindingRepository = (
            get_container().repository.device_binding_repository()
        )
        device_id = _generate_uuid()

        binding_id = repo.insert_binding(
            entity_id="staff_12345",
            entity_type="staff",
            device_id=device_id,
            device_provider="arca",
            env=TEST_ENV,
            device_props={"sandbox_id": "sbx-test"},
            status=DeviceBindingStatus.ACTIVE.value,
            apply_reason="test",
            applied_by="tester",
        )
        assert binding_id > 0

        record = repo.get_by_id(binding_id)
        assert isinstance(record, DeviceBindingRecord)
        assert record.device_id == device_id
        assert record.entity_id == "staff_12345"
        assert record.status == DeviceBindingStatus.ACTIVE.value
        assert record.gmt_create is not None

    def test_get_by_id_nonexistent(self):
        repo = get_container().repository.device_binding_repository()
        assert repo.get_by_id(99999999) is None

    def test_deep_update_status(self):
        repo = get_container().repository.device_binding_repository()
        device_id = _generate_uuid()

        binding_id = repo.insert_binding(
            entity_id="staff_12345",
            entity_type="staff",
            device_id=device_id,
            device_provider="arca",
            env=TEST_ENV,
            device_props={"sandbox_id": "sbx-test"},
            status=DeviceBindingStatus.ACTIVE.value,
            apply_reason="test",
            applied_by="tester",
        )
        repo.update_status(
            binding_id=binding_id,
            status=DeviceBindingStatus.RELEASED.value,
        )
        record = repo.get_by_id(binding_id)
        assert record is not None
        assert record.status == DeviceBindingStatus.RELEASED.value

    def test_deep_release_binding(self):
        repo = get_container().repository.device_binding_repository()
        device_id = _generate_uuid()

        binding_id = repo.insert_binding(
            entity_id="staff_12345",
            entity_type="staff",
            device_id=device_id,
            device_provider="arca",
            env=TEST_ENV,
            device_props={"sandbox_id": "sbx-test"},
            status=DeviceBindingStatus.ACTIVE.value,
            apply_reason="test",
            applied_by="tester",
        )
        repo.release_binding(
            binding_id=binding_id,
            release_reason="test release",
            released_by="tester",
        )
        record = repo.get_by_id(binding_id)
        assert record is not None
        assert record.status == DeviceBindingStatus.RELEASED.value
        assert record.release_reason == "test release"
        assert record.released_by == "tester"

    def test_get_by_device_id(self):
        repo = get_container().repository.device_binding_repository()
        device_id = _generate_uuid()

        repo.insert_binding(
            entity_id="staff_12345",
            entity_type="staff",
            device_id=device_id,
            device_provider="arca",
            env=TEST_ENV,
            device_props={"sandbox_id": "sbx-test"},
            status=DeviceBindingStatus.ACTIVE.value,
            apply_reason="equiv test",
            applied_by="tester",
        )

        record = repo.get_by_device_id(device_id)
        assert record is not None
        assert record.device_id == device_id

    def test_get_by_device_id_nonexistent(self):
        repo = get_container().repository.device_binding_repository()
        assert repo.get_by_device_id(f"nonexistent_{_generate_uuid()}") is None

    def test_update_status(self):
        repo = get_container().repository.device_binding_repository()
        device_id = _generate_uuid()

        binding_id = repo.insert_binding(
            entity_id="staff_12345",
            entity_type="staff",
            device_id=device_id,
            device_provider="arca",
            env=TEST_ENV,
            device_props={"sandbox_id": "sbx-test"},
            status=DeviceBindingStatus.ACTIVE.value,
            apply_reason="equiv test",
            applied_by="tester",
        )
        repo.update_status(
            binding_id=binding_id, status=DeviceBindingStatus.RELEASED.value
        )

        record = repo.get_by_id(binding_id)
        assert record is not None
        assert record.status == DeviceBindingStatus.RELEASED.value

    def test_release_binding(self):
        repo = get_container().repository.device_binding_repository()
        device_id = _generate_uuid()

        binding_id = repo.insert_binding(
            entity_id="staff_12345",
            entity_type="staff",
            device_id=device_id,
            device_provider="arca",
            env=TEST_ENV,
            device_props={"sandbox_id": "sbx-test"},
            status=DeviceBindingStatus.ACTIVE.value,
            apply_reason="equiv test",
            applied_by="tester",
        )
        repo.release_binding(
            binding_id=binding_id,
            release_reason="equiv test release",
            released_by="tester",
        )

        record = repo.get_by_id(binding_id)
        assert record is not None
        assert record.status == DeviceBindingStatus.RELEASED.value
        assert record.release_reason == "equiv test release"
        assert record.released_by == "tester"

    def test_list_bindings(self):
        repo = get_container().repository.device_binding_repository()
        device_id = _generate_uuid()

        repo.insert_binding(
            entity_id="staff_12345",
            entity_type="staff",
            device_id=device_id,
            device_provider="arca",
            env=TEST_ENV,
            device_props={"sandbox_id": "sbx-test"},
            status=DeviceBindingStatus.ACTIVE.value,
            apply_reason="equiv test",
            applied_by="tester",
        )

        total, records = repo.list_bindings(
            entity_id="staff_12345",
            entity_type="staff",
            env=TEST_ENV,
            page=1,
            page_size=10,
        )
        assert total >= 1
        device_ids = {r.device_id for r in records}
        assert device_id in device_ids

    def test_insert_binding_and_get_by_id(self):
        repo: DeviceBindingRepository = (
            get_container().repository.device_binding_repository()
        )
        device_id = _generate_uuid()

        binding_id = repo.insert_binding(
            entity_id="staff_12345",
            entity_type="staff",
            device_id=device_id,
            device_provider="arca",
            env=TEST_ENV,
            device_props={"sandbox_id": "sbx-test"},
            status=DeviceBindingStatus.ACTIVE.value,
            apply_reason="equiv test",
            applied_by="tester",
        )
        assert binding_id > 0

        record = repo.get_by_id(binding_id)
        assert isinstance(record, DeviceBindingRecord)
        assert record.id == binding_id
        assert record.entity_id == "staff_12345"
        assert record.entity_type == "staff"
        assert record.device_id == device_id
        assert record.device_provider == "arca"
        assert record.env == TEST_ENV
        assert record.device_props == {"sandbox_id": "sbx-test"}
        assert record.status == DeviceBindingStatus.ACTIVE.value
        assert record.apply_reason == "equiv test"
        assert record.applied_by == "tester"
        assert record.gmt_create is not None
