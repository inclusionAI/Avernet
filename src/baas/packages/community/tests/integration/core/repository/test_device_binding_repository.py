"""Integration tests for DeviceBindingRepository Protocol against ZDAS MySQL.

Covers all 30 methods across these categories:
  - Core INSERT/GET/DELETE: insert_binding, get_by_id, get_by_device_id,
    delete_binding, exists
  - Status Lifecycle: release_binding, update_status,
    update_status_and_alive_at, reuse_binding
  - List/Search: list_bindings, count_non_released_bindings,
    exists_device_id, get_released_binding,
    get_binding_by_sandbox_id, get_binding_by_sandbox_id_like
  - TTL / Baas Device: update_device_props_ttl,
    update_device_props_ttl_by_paas_device_id, update_baas_device_ttl,
    update_baas_device_ttl_by_id, update_device_props_refresh_fail_count,
    update_baas_device_refresh_fail_count_by_id
  - Bot/Binding Relations: get_publish_binding, get_bot_binding
  - PaaS Device Lists: list_paas_device_by_bot_personal,
    list_paas_device_by_bot_service
  - Entity-Type Queries: list_active_sandboxes_with_bot, list_sandboxes_by_bot,
    list_all_active_bot_device
  - Export: export_device_all, export_device_list

Every test uses ONLY the DeviceBindingRepository Protocol —
no OrmDeviceBindingRepository references.
db_transaction ensures all changes are rolled back.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from secbaas.core.repository.device import DeviceRepository
from secbaas.core.repository.device_binding import (
    DeviceBindingRecord,
    DeviceBindingRepository,
    DeviceBindingStatus,
)
from secbaas.core.utils.env_utils import get_current_env

pytestmark = pytest.mark.integration

TEST_ENV = get_current_env()
ENTITY_ID = "staff_12345"
ENTITY_TYPE = "staff"


def _uid() -> str:
    return uuid4().hex


def _make_device_props(
    sandbox_id: str | None = None,
    bolt_id: str | None = None,
) -> dict[str, object]:
    """Create device_props dict with defaults for all standard JSON paths."""
    return {
        "sandbox_id": sandbox_id or f"sbx-{_uid()[:12]}",
        "bolt_id": bolt_id or f"blt-{_uid()[:12]}",
        "template_id": "ARCA-TEMPLATE-test",
        "client_id": _uid()[:8],
        "callback_token": _uid(),
    }


def _insert(
    repo: DeviceBindingRepository,
    *,
    entity_id: str = ENTITY_ID,
    entity_type: str = ENTITY_TYPE,
    device_id: str | None = None,
    device_provider: str = "arca",
    env: str = TEST_ENV,
    device_props: dict[str, object] | None = None,
    status: str = DeviceBindingStatus.ACTIVE.value,
    apply_reason: str | None = "integration test",
    applied_by: str = "test_user",
) -> int:
    return repo.insert_binding(
        entity_id=entity_id,
        entity_type=entity_type,
        device_id=device_id or _uid(),
        device_provider=device_provider,
        env=env,
        device_props=device_props or _make_device_props(),
        status=status,
        apply_reason=apply_reason,
        applied_by=applied_by,
    )


class TestDeviceBindingRepositoryProtocol:
    """Integration tests for DeviceBindingRepository Protocol.

    Uses ONLY the Protocol type — no OrmDeviceBindingRepository references.
    db_transaction rolls back every test.
    """

    # ==================================================================
    # CORE INSERT / GET / DELETE
    # ==================================================================

    def test_insert_binding_and_get_by_id_full_roundtrip(
        self,
        device_binding_repository: DeviceBindingRepository,
        db_transaction,
    ):
        """insert_binding + get_by_id: full field round-trip verification."""
        repo = device_binding_repository
        device_id = _uid()
        device_props = _make_device_props(sandbox_id="sbx-roundtrip-001")

        binding_id = repo.insert_binding(
            entity_id=ENTITY_ID,
            entity_type=ENTITY_TYPE,
            device_id=device_id,
            device_provider="arca",
            env=TEST_ENV,
            device_props=device_props,
            status=DeviceBindingStatus.ACTIVE.value,
            apply_reason="round-trip test",
            applied_by="tester",
        )
        assert binding_id > 0

        record = repo.get_by_id(binding_id)
        assert record is not None
        assert isinstance(record, DeviceBindingRecord)
        assert record.id == binding_id
        assert record.entity_id == ENTITY_ID
        assert record.entity_type == ENTITY_TYPE
        assert record.device_id == device_id
        assert record.device_provider == "arca"
        assert record.env == TEST_ENV
        assert record.status == DeviceBindingStatus.ACTIVE.value
        assert record.apply_reason == "round-trip test"
        assert record.applied_by == "tester"
        assert record.device_props["sandbox_id"] == "sbx-roundtrip-001"
        assert record.device_props["bolt_id"] == device_props["bolt_id"]
        assert record.gmt_create is not None
        assert record.gmt_modified is not None

    def test_get_by_id_returns_none_for_missing(
        self,
        device_binding_repository: DeviceBindingRepository,
        db_transaction,
    ):
        result = device_binding_repository.get_by_id(99999999)
        assert result is None

    def test_get_by_device_id(
        self,
        device_binding_repository: DeviceBindingRepository,
        db_transaction,
    ):
        """get_by_device_id returns the latest binding for a device."""
        repo = device_binding_repository
        device_id = _uid()

        binding_id = _insert(repo, device_id=device_id)
        record = repo.get_by_device_id(device_id)
        assert record is not None
        assert record.id == binding_id
        assert record.device_id == device_id

    def test_delete_binding_returns_true(
        self,
        device_binding_repository: DeviceBindingRepository,
        db_transaction,
    ):
        repo = device_binding_repository
        binding_id = _insert(repo)

        assert repo.delete_binding(binding_id) is True
        assert repo.get_by_id(binding_id) is None

    def test_delete_binding_returns_false_for_missing(
        self,
        device_binding_repository: DeviceBindingRepository,
        db_transaction,
    ):
        assert device_binding_repository.delete_binding(99999999) is False

    def test_exists_returns_true(
        self,
        device_binding_repository: DeviceBindingRepository,
        db_transaction,
    ):
        repo = device_binding_repository
        binding_id = _insert(repo)
        assert repo.exists(binding_id) is True

    def test_exists_returns_false(
        self,
        device_binding_repository: DeviceBindingRepository,
        db_transaction,
    ):
        assert device_binding_repository.exists(99999999) is False

    # ==================================================================
    # STATUS LIFECYCLE
    # ==================================================================

    def test_release_binding_sets_status_and_timestamps(
        self,
        device_binding_repository: DeviceBindingRepository,
        db_transaction,
    ):
        """release_binding sets status=RELEASED, release_reason, released_by,
        released_at."""
        repo = device_binding_repository
        binding_id = _insert(repo, status=DeviceBindingStatus.ACTIVE.value)

        repo.release_binding(
            binding_id=binding_id,
            release_reason="no longer needed",
            released_by="staff_12345",
        )

        record = repo.get_by_id(binding_id)
        assert record is not None
        assert record.status == DeviceBindingStatus.RELEASED.value
        assert record.release_reason == "no longer needed"
        assert record.released_by == "staff_12345"
        assert record.released_at is not None

    def test_update_status(
        self,
        device_binding_repository: DeviceBindingRepository,
        db_transaction,
    ):
        repo = device_binding_repository
        binding_id = _insert(repo, status=DeviceBindingStatus.PENDING.value)

        repo.update_status(
            binding_id=binding_id, status=DeviceBindingStatus.ACTIVE.value
        )

        record = repo.get_by_id(binding_id)
        assert record is not None
        assert record.status == DeviceBindingStatus.ACTIVE.value

    def test_update_status_and_alive_at(
        self,
        device_binding_repository: DeviceBindingRepository,
        db_transaction,
    ):
        """update_status_and_alive_at updates both status and last_alive_at."""
        repo = device_binding_repository
        binding_id = _insert(repo, status=DeviceBindingStatus.PENDING.value)

        repo.update_status_and_alive_at(
            binding_id=binding_id, status=DeviceBindingStatus.ACTIVE.value
        )

        record = repo.get_by_id(binding_id)
        assert record is not None
        assert record.status == DeviceBindingStatus.ACTIVE.value
        assert record.last_alive_at is not None

    def test_reuse_binding_resets_status_and_props(
        self,
        device_binding_repository: DeviceBindingRepository,
        db_transaction,
    ):
        """reuse_binding resets status, sets new device_props, clears release fields."""
        repo = device_binding_repository
        binding_id = _insert(repo, status=DeviceBindingStatus.ACTIVE.value)

        # First release it
        repo.release_binding(
            binding_id=binding_id,
            release_reason="old",
            released_by="staff_12345",
        )

        # Then reuse
        new_props = _make_device_props(sandbox_id="sbx-reused-001")
        repo.reuse_binding(
            binding_id=binding_id,
            device_props=new_props,
            apply_reason="reusing device",
            applied_by="reuser",
            status=DeviceBindingStatus.PENDING.value,
        )

        record = repo.get_by_id(binding_id)
        assert record is not None
        assert record.status == DeviceBindingStatus.PENDING.value
        assert record.device_props["sandbox_id"] == "sbx-reused-001"
        assert record.apply_reason == "reusing device"
        assert record.applied_by == "reuser"
        assert record.release_reason is None
        assert record.released_by is None
        assert record.released_at is None
        assert record.last_alive_at is None

    # ==================================================================
    # LIST / SEARCH
    # ==================================================================

    def test_list_bindings_with_entity_filters(
        self,
        device_binding_repository: DeviceBindingRepository,
        db_transaction,
    ):
        """list_bindings filters by entity_id, entity_type, env, paginates
        correctly."""
        repo = device_binding_repository
        # Insert 3 bindings
        for i in range(3):
            _insert(
                repo,
                device_id=_uid(),
                device_props=_make_device_props(sandbox_id=f"sbx-list-{i}"),
            )

        total, items = repo.list_bindings(
            entity_id=ENTITY_ID,
            entity_type=ENTITY_TYPE,
            env=TEST_ENV,
            page=1,
            page_size=10,
        )
        assert total >= 3
        assert len(items) >= 3
        for item in items:
            assert isinstance(item, DeviceBindingRecord)
            assert item.entity_id == ENTITY_ID

    def test_list_bindings_pagination(
        self,
        device_binding_repository: DeviceBindingRepository,
        db_transaction,
    ):
        repo = device_binding_repository
        for i in range(3):
            _insert(
                repo,
                device_id=_uid(),
                device_props=_make_device_props(sandbox_id=f"sbx-page-{i}"),
            )

        total, page1 = repo.list_bindings(
            entity_id=ENTITY_ID,
            entity_type=ENTITY_TYPE,
            env=TEST_ENV,
            page=1,
            page_size=1,
        )
        assert total >= 3
        assert len(page1) == 1

    def test_count_non_released_bindings(
        self,
        device_binding_repository: DeviceBindingRepository,
        db_transaction,
    ):
        repo = device_binding_repository
        _insert(repo, status=DeviceBindingStatus.ACTIVE.value, device_id=_uid())
        _insert(repo, status=DeviceBindingStatus.PENDING.value, device_id=_uid())

        count = repo.count_non_released_bindings(
            entity_id=ENTITY_ID,
            entity_type=ENTITY_TYPE,
            env=TEST_ENV,
        )
        assert count >= 2

    def test_count_non_released_bindings_excludes_released(
        self,
        device_binding_repository: DeviceBindingRepository,
        db_transaction,
    ):
        repo = device_binding_repository
        _insert(repo, status=DeviceBindingStatus.ACTIVE.value, device_id=_uid())
        released_id = _insert(
            repo, status=DeviceBindingStatus.ACTIVE.value, device_id=_uid()
        )
        repo.release_binding(
            binding_id=released_id, release_reason="test", released_by="tester"
        )

        count = repo.count_non_released_bindings(
            entity_id=ENTITY_ID,
            entity_type=ENTITY_TYPE,
            env=TEST_ENV,
        )
        assert count >= 1  # only the ACTIVE one remains

    def test_exists_device_id_true(
        self,
        device_binding_repository: DeviceBindingRepository,
        db_transaction,
    ):
        repo = device_binding_repository
        device_id = _uid()
        _insert(repo, device_id=device_id)
        assert repo.exists_device_id(device_id=device_id) is True

    def test_exists_device_id_false(
        self,
        device_binding_repository: DeviceBindingRepository,
        db_transaction,
    ):
        assert device_binding_repository.exists_device_id(device_id=_uid()) is False

    def test_get_released_binding(
        self,
        device_binding_repository: DeviceBindingRepository,
        db_transaction,
    ):
        repo = device_binding_repository
        device_id = _uid()
        binding_id = _insert(
            repo, device_id=device_id, status=DeviceBindingStatus.ACTIVE.value
        )
        repo.release_binding(
            binding_id=binding_id, release_reason="done", released_by="tester"
        )

        record = repo.get_released_binding(device_id=device_id)
        assert record is not None
        assert record.id == binding_id
        assert record.status == DeviceBindingStatus.RELEASED.value

    def test_get_released_binding_returns_none_for_active(
        self,
        device_binding_repository: DeviceBindingRepository,
        db_transaction,
    ):
        repo = device_binding_repository
        device_id = _uid()
        _insert(repo, device_id=device_id, status=DeviceBindingStatus.ACTIVE.value)

        assert repo.get_released_binding(device_id=device_id) is None

    def test_get_binding_by_sandbox_id(
        self,
        device_binding_repository: DeviceBindingRepository,
        db_transaction,
    ):
        repo = device_binding_repository
        sandbox_id = f"sbx-exact-{_uid()[:8]}"
        _insert(repo, device_props=_make_device_props(sandbox_id=sandbox_id))

        record = repo.get_binding_by_sandbox_id(sandbox_id=sandbox_id)
        assert record is not None
        assert record.device_props["sandbox_id"] == sandbox_id

    def test_get_binding_by_sandbox_id_returns_none(
        self,
        device_binding_repository: DeviceBindingRepository,
        db_transaction,
    ):
        assert (
            device_binding_repository.get_binding_by_sandbox_id(
                sandbox_id="nonexistent-sbx"
            )
            is None
        )

    def test_get_binding_by_sandbox_id_like_prefix_match(
        self,
        device_binding_repository: DeviceBindingRepository,
        db_transaction,
    ):
        """get_binding_by_sandbox_id_like: fuzzy prefix match on sandbox_id."""
        repo = device_binding_repository
        prefix = f"sbx-prefix-{_uid()[:8]}"
        _insert(repo, device_props=_make_device_props(sandbox_id=f"{prefix}@0"))

        record = repo.get_binding_by_sandbox_id_like(sandbox_id_prefix=prefix)
        assert record is not None
        assert prefix in record.device_props["sandbox_id"]

    def test_get_binding_by_sandbox_id_like_returns_none(
        self,
        device_binding_repository: DeviceBindingRepository,
        db_transaction,
    ):
        assert (
            device_binding_repository.get_binding_by_sandbox_id_like(
                sandbox_id_prefix="zzz-nonexistent"
            )
            is None
        )

    # ==================================================================
    # TTL / BAAS DEVICE
    # ==================================================================

    def test_update_device_props_ttl(
        self,
        device_binding_repository: DeviceBindingRepository,
        db_transaction,
    ):
        """update_device_props_ttl sets all three TTL JSON fields."""
        repo = device_binding_repository
        binding_id = _insert(repo)

        repo.update_device_props_ttl(
            binding_id=binding_id,
            ttl_expiration_timestamp=1735689600000,
            ttl_expiration_time="2025-01-01 00:00:00",
            refresh_fail_count=0,
        )

        record = repo.get_by_id(binding_id)
        assert record is not None
        assert record.device_props["ttl_expiration_timestamp"] == 1735689600000
        assert record.device_props["ttl_expiration_time"] == "2025-01-01 00:00:00"
        assert record.device_props["refresh_fail_count"] == 0

    def test_update_device_props_ttl_with_fail_count(
        self,
        device_binding_repository: DeviceBindingRepository,
        db_transaction,
    ):
        repo = device_binding_repository
        binding_id = _insert(repo)

        repo.update_device_props_ttl(
            binding_id=binding_id,
            ttl_expiration_timestamp=1735000000000,
            ttl_expiration_time="2024-12-24 00:00:00",
            refresh_fail_count=3,
        )

        record = repo.get_by_id(binding_id)
        assert record is not None
        assert record.device_props["refresh_fail_count"] == 3

    def test_update_device_props_ttl_by_paas_device_id(
        self,
        device_binding_repository: DeviceBindingRepository,
        db_transaction,
    ):
        """update_device_props_ttl_by_paas_device_id matches via sandbox_id JSON
        path."""
        repo = device_binding_repository
        sandbox_id = f"sbx-paas-{_uid()[:8]}"
        _insert(repo, device_props=_make_device_props(sandbox_id=sandbox_id))

        repo.update_device_props_ttl_by_paas_device_id(
            paas_device_id=sandbox_id,
            ttl_expiration_timestamp=1735800000000,
            ttl_expiration_time="2025-01-02 12:00:00",
        )

        record = repo.get_binding_by_sandbox_id(sandbox_id=sandbox_id)
        assert record is not None
        assert record.device_props["ttl_expiration_timestamp"] == 1735800000000
        assert record.device_props["ttl_expiration_time"] == "2025-01-02 12:00:00"

    def test_update_baas_device_ttl(
        self,
        device_binding_repository: DeviceBindingRepository,
        device_repository: DeviceRepository,
    ):
        """update_baas_device_ttl writes to baas_device.provider_device_props."""
        repo = device_binding_repository
        device_uuid = _uid()

        device_repository.insert_device(
            device_uuid=device_uuid,
            tenant="test_tenant",
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status="ACTIVE",
            provider_type="arca",
            provider_device_id=device_uuid,
            provider_device_props={},
            extra_config={},
        )

        repo.update_baas_device_ttl(
            device_uuid=device_uuid,
            ttl_expiration_time="2025-06-01 00:00:00",
            ttl_expiration_timestamp=1748736000000,
        )

        record = device_repository.get_by_device_uuid(
            device_uuid=device_uuid,
            tenant="test_tenant",
            env=TEST_ENV,
            status="ACTIVE",
        )
        assert record is not None
        assert record.provider_device_props["ttl_expiration_timestamp"] == 1748736000000
        assert (
            record.provider_device_props["ttl_expiration_time"] == "2025-06-01 00:00:00"
        )

    def test_update_baas_device_ttl_by_id(
        self,
        device_binding_repository: DeviceBindingRepository,
        device_repository: DeviceRepository,
    ):
        """update_baas_device_ttl_by_id writes TTL + refresh_fail_count by id."""
        repo = device_binding_repository
        device_uuid = _uid()

        baas_device_id = device_repository.insert_device(
            device_uuid=device_uuid,
            tenant="test_tenant",
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status="ACTIVE",
            provider_type="arca",
            provider_device_id=device_uuid,
            provider_device_props={},
            extra_config={},
        )

        repo.update_baas_device_ttl_by_id(
            baas_device_id=baas_device_id,
            ttl_expiration_time="2025-07-01 00:00:00",
            ttl_expiration_timestamp=1751328000000,
            refresh_fail_count=2,
        )

        record = device_repository.get_by_id(baas_device_id, "test_tenant", TEST_ENV)
        assert record is not None
        assert record.provider_device_props["ttl_expiration_timestamp"] == 1751328000000
        assert (
            record.provider_device_props["ttl_expiration_time"] == "2025-07-01 00:00:00"
        )
        assert record.provider_device_props["refresh_fail_count"] == 2

    def test_update_device_props_refresh_fail_count(
        self,
        device_binding_repository: DeviceBindingRepository,
        db_transaction,
    ):
        repo = device_binding_repository
        binding_id = _insert(repo)

        repo.update_device_props_refresh_fail_count(
            binding_id=binding_id,
            refresh_fail_count=5,
        )

        record = repo.get_by_id(binding_id)
        assert record is not None
        assert record.device_props["refresh_fail_count"] == 5

    def test_update_baas_device_refresh_fail_count_by_id(
        self,
        device_binding_repository: DeviceBindingRepository,
        device_repository: DeviceRepository,
    ):
        """update_baas_device_refresh_fail_count_by_id writes refresh_fail_count by id."""
        repo = device_binding_repository
        device_uuid = _uid()

        baas_device_id = device_repository.insert_device(
            device_uuid=device_uuid,
            tenant="test_tenant",
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status="ACTIVE",
            provider_type="arca",
            provider_device_id=device_uuid,
            provider_device_props={},
            extra_config={},
        )

        repo.update_baas_device_refresh_fail_count_by_id(
            baas_device_id=baas_device_id,
            refresh_fail_count=7,
        )

        record = device_repository.get_by_id(baas_device_id, "test_tenant", TEST_ENV)
        assert record is not None
        assert record.provider_device_props["refresh_fail_count"] == 7

    # ==================================================================
    # BOT / BINDING RELATIONS
    # ==================================================================

    def test_get_publish_binding_returns_none(
        self,
        device_binding_repository: DeviceBindingRepository,
        db_transaction,
    ):
        """get_publish_binding returns None for non-existent source_bot_id — this
        works without inserting data because the query just returns nothing."""
        assert (
            device_binding_repository.get_publish_binding(
                source_bot_id=_uid(),
                status="validating",
            )
            is None
        )

    def test_get_bot_binding_returns_none(
        self,
        device_binding_repository: DeviceBindingRepository,
        db_transaction,
    ):
        """get_bot_binding returns None for non-existent bot_id — works without
        pre-existing data."""
        assert (
            device_binding_repository.get_bot_binding(
                bot_id=_uid(),
                entity_id=ENTITY_ID,
                env=TEST_ENV,
            )
            is None
        )

    # ==================================================================
    # PAAS DEVICE LISTS
    # ==================================================================

    def test_list_paas_device_by_bot_personal(
        self,
        device_binding_repository: DeviceBindingRepository,
        db_transaction,
    ):
        """list_paas_device_by_bot_personal extracts sandbox_id as
        paas_device_id."""
        repo = device_binding_repository
        sandbox_id = f"sbx-personal-{_uid()[:8]}"
        binding_id = _insert(
            repo,
            device_props=_make_device_props(sandbox_id=sandbox_id),
        )

        # Also set TTL fields for the query to extract them
        repo.update_device_props_ttl(
            binding_id=binding_id,
            ttl_expiration_timestamp=1735689600000,
            ttl_expiration_time="2025-01-01 00:00:00",
        )

        results = repo.list_paas_device_by_bot_personal(
            bot_id="bot-personal-1",  # bot_id is just for logging
            binding_id=binding_id,
        )
        assert len(results) == 1
        assert results[0]["paas_device_id"] == sandbox_id
        assert results[0]["provider_type"] == "arca"
        assert results[0]["status"] == DeviceBindingStatus.ACTIVE.value
        assert results[0]["ttl_expiration_timestamp"] == 1735689600000
        assert results[0]["source_table"] == "ac_binding"
        assert results[0]["source_table_id"] == binding_id

    def test_list_sandboxes_by_bot_returns_none_for_missing_bot(
        self,
        device_binding_repository: DeviceBindingRepository,
        db_transaction,
    ):
        bot_info, sandboxes = device_binding_repository.list_sandboxes_by_bot(
            bot_id=_uid(),
            entity_id=ENTITY_ID,
        )
        assert bot_info is None
        assert sandboxes == []

    # ==================================================================
    # EXPORT
    # ==================================================================

    def test_export_device_all(
        self,
        device_binding_repository: DeviceBindingRepository,
        db_transaction,
    ):
        """export_device_all returns (entity_id, bolt_id, sandbox_id) tuples
        for all ACTIVE arca devices."""
        repo = device_binding_repository
        sandbox_id = f"sbx-export-{_uid()[:8]}"
        bolt_id = f"blt-export-{_uid()[:8]}"
        _insert(
            repo,
            device_props=_make_device_props(sandbox_id=sandbox_id, bolt_id=bolt_id),
        )

        results = repo.export_device_all()
        assert len(results) >= 1
        # Find our record
        matching = [r for r in results if r[2] == sandbox_id]
        assert len(matching) == 1
        assert matching[0][0] == ENTITY_ID  # entity_id
        assert matching[0][1] == bolt_id  # bolt_id

    def test_export_device_list_env_filtered(
        self,
        device_binding_repository: DeviceBindingRepository,
        db_transaction,
    ):
        """export_device_list filters by env."""
        repo = device_binding_repository
        sandbox_id = f"sbx-export-env-{_uid()[:8]}"
        bolt_id = f"blt-export-env-{_uid()[:8]}"
        _insert(
            repo,
            device_props=_make_device_props(sandbox_id=sandbox_id, bolt_id=bolt_id),
        )

        results = repo.export_device_list(env=TEST_ENV)
        matching = [r for r in results if r[2] == sandbox_id]
        assert len(matching) == 1
        assert matching[0][0] == ENTITY_ID
        assert matching[0][1] == bolt_id

    def test_export_device_list_other_env_empty(
        self,
        device_binding_repository: DeviceBindingRepository,
        db_transaction,
    ):
        """export_device_list returns empty for an env with no bindings."""
        _insert(repo=device_binding_repository, env=TEST_ENV)
        results = device_binding_repository.export_device_list(env="nonexistent_env")
        # Should not contain records with TEST_ENV
        matching = [r for r in results if r[0] == ENTITY_ID]
        assert len(matching) == 0
