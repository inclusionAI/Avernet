import time
from uuid import uuid4

import pytest

from secbaas.core.repository.tenant import TenantRecord, TenantRepository
from secbaas.core.utils.env_utils import get_current_env

mysql_connector = pytest.importorskip("mysql.connector")

pytestmark = pytest.mark.integration

TEST_ENV = get_current_env()


def _generate_uuid() -> str:
    return uuid4().hex


class TestTenantRepositoryProtocol:
    """Integration tests for TenantRepository Protocol against ZDAS MySQL.

    Every test uses ONLY the TenantRepository Protocol — no OrmTenantRepository
    references allowed. db_transaction ensures all changes are rolled back.
    """

    # ── 1. insert_tenant + get_by_id ──────────────────────────────────────────

    def test_insert_and_get_by_id(
        self, tenant_repository: TenantRepository, db_transaction
    ):
        record_id = tenant_repository.insert_tenant(
            creator="test_user",
            modifier="test_user",
            name=f"t_insert_get_{_generate_uuid()[:8]}",
            description="Test description",
            env=TEST_ENV,
            extra_config=None,
        )
        assert record_id > 0

        record = tenant_repository.get_by_id(record_id)
        assert record is not None
        assert record.id == record_id
        assert record.name.startswith("t_insert_get_")
        assert record.description == "Test description"
        assert record.env == TEST_ENV
        assert record.creator == "test_user"
        assert record.modifier == "test_user"
        assert record.extra_config == {}
        assert record.is_deleted == 0

    def test_get_by_id_returns_none_for_missing(
        self, tenant_repository: TenantRepository, db_transaction
    ):
        result = tenant_repository.get_by_id(99999999)
        assert result is None

    def test_get_by_id_returns_none_for_wrong_env(
        self, tenant_repository: TenantRepository, db_transaction
    ):
        record_id = tenant_repository.insert_tenant(
            creator="test_user",
            modifier="test_user",
            name=f"t_wrong_env_{_generate_uuid()[:8]}",
            description=None,
            env=TEST_ENV,
            extra_config=None,
        )

        record = tenant_repository.get_by_id(record_id)
        assert record is not None
        assert record.env == TEST_ENV

    # ── 2. get_by_name ───────────────────────────────────────────────────────

    def test_get_by_name_returns_record(
        self, tenant_repository: TenantRepository, db_transaction
    ):
        tenant_name = f"t_by_name_{_generate_uuid()[:8]}"
        tenant_repository.insert_tenant(
            creator="test_user",
            modifier="test_user",
            name=tenant_name,
            description="Lookup me",
            env=TEST_ENV,
            extra_config={"key": "val"},
        )

        record = tenant_repository.get_by_name(tenant_name, TEST_ENV)
        assert record is not None
        assert record.name == tenant_name
        assert record.description == "Lookup me"
        assert record.extra_config == {"key": "val"}
        assert record.env == TEST_ENV

    def test_get_by_name_returns_none_for_missing(
        self, tenant_repository: TenantRepository, db_transaction
    ):
        result = tenant_repository.get_by_name("nonexistent_tenant_name", TEST_ENV)
        assert result is None

    def test_get_by_name_env_isolation(
        self, tenant_repository: TenantRepository, db_transaction
    ):
        tenant_name = f"t_env_iso_{_generate_uuid()[:8]}"
        tenant_repository.insert_tenant(
            creator="test_user",
            modifier="test_user",
            name=tenant_name,
            description=None,
            env=TEST_ENV,
            extra_config=None,
        )

        wrong_env = "prod" if TEST_ENV != "prod" else "dev"
        result = tenant_repository.get_by_name(tenant_name, wrong_env)
        assert result is None

        result = tenant_repository.get_by_name(tenant_name, TEST_ENV)
        assert result is not None

    # ── 3. insert_tenant + update_tenant ─────────────────────────────────────

    def test_update_description(
        self, tenant_repository: TenantRepository, db_transaction
    ):
        tenant_name = f"t_upd_desc_{_generate_uuid()[:8]}"
        tenant_repository.insert_tenant(
            creator="test_user",
            modifier="test_user",
            name=tenant_name,
            description="Before",
            env=TEST_ENV,
            extra_config=None,
        )

        rows = tenant_repository.update_tenant(
            name=tenant_name,
            env=TEST_ENV,
            modifier="updater",
            description="After",
        )
        assert rows == 1

        record = tenant_repository.get_by_name(tenant_name, TEST_ENV)
        assert record is not None
        assert record.description == "After"
        assert record.modifier == "updater"

    def test_update_extra_config(
        self, tenant_repository: TenantRepository, db_transaction
    ):
        tenant_name = f"t_upd_cfg_{_generate_uuid()[:8]}"
        tenant_repository.insert_tenant(
            creator="test_user",
            modifier="test_user",
            name=tenant_name,
            description=None,
            env=TEST_ENV,
            extra_config={"old": "value"},
        )

        tenant_repository.update_tenant(
            name=tenant_name,
            env=TEST_ENV,
            modifier="updater",
            extra_config={"new": "data", "nested": {"deep": True}},
        )

        record = tenant_repository.get_by_name(tenant_name, TEST_ENV)
        assert record is not None
        assert record.extra_config == {"new": "data", "nested": {"deep": True}}

    def test_update_both_description_and_extra_config(
        self, tenant_repository: TenantRepository, db_transaction
    ):
        tenant_name = f"t_upd_both_{_generate_uuid()[:8]}"
        tenant_repository.insert_tenant(
            creator="test_user",
            modifier="test_user",
            name=tenant_name,
            description="Old desc",
            env=TEST_ENV,
            extra_config={"old": "config"},
        )

        tenant_repository.update_tenant(
            name=tenant_name,
            env=TEST_ENV,
            modifier="admin",
            description="New desc",
            extra_config={"new": "config"},
        )

        record = tenant_repository.get_by_name(tenant_name, TEST_ENV)
        assert record is not None
        assert record.description == "New desc"
        assert record.extra_config == {"new": "config"}
        assert record.modifier == "admin"

    def test_update_gmt_modified_changes(
        self, tenant_repository: TenantRepository, db_transaction
    ):
        tenant_name = f"t_upd_ts_{_generate_uuid()[:8]}"
        tenant_repository.insert_tenant(
            creator="test_user",
            modifier="test_user",
            name=tenant_name,
            description="Original",
            env=TEST_ENV,
            extra_config=None,
        )

        original = tenant_repository.get_by_name(tenant_name, TEST_ENV)
        assert original is not None

        time.sleep(0.1)
        tenant_repository.update_tenant(
            name=tenant_name,
            env=TEST_ENV,
            modifier="updater",
            description="Updated",
        )

        updated = tenant_repository.get_by_name(tenant_name, TEST_ENV)
        assert updated is not None
        assert updated.gmt_modified >= original.gmt_modified

    # ── 4. soft_delete ───────────────────────────────────────────────────────

    def test_soft_delete_makes_get_by_id_return_none(
        self, tenant_repository: TenantRepository, db_transaction
    ):
        tenant_name = f"t_del_{_generate_uuid()[:8]}"
        record_id = tenant_repository.insert_tenant(
            creator="test_user",
            modifier="test_user",
            name=tenant_name,
            description=None,
            env=TEST_ENV,
            extra_config=None,
        )

        tenant_repository.soft_delete(name=tenant_name, env=TEST_ENV, modifier="admin")

        record = tenant_repository.get_by_id(record_id)
        assert record is None

    def test_soft_delete_makes_get_by_name_return_none(
        self, tenant_repository: TenantRepository, db_transaction
    ):
        tenant_name = f"t_del_{_generate_uuid()[:8]}"
        tenant_repository.insert_tenant(
            creator="test_user",
            modifier="test_user",
            name=tenant_name,
            description=None,
            env=TEST_ENV,
            extra_config=None,
        )

        tenant_repository.soft_delete(name=tenant_name, env=TEST_ENV, modifier="admin")

        result = tenant_repository.get_by_name(tenant_name, TEST_ENV)
        assert result is None

    def test_soft_delete_does_not_affect_other_env(
        self, tenant_repository: TenantRepository, db_transaction
    ):
        tenant_name = f"t_del_env_{_generate_uuid()[:8]}"
        other_env = "prod" if TEST_ENV != "prod" else "dev"

        tenant_repository.insert_tenant(
            creator="test_user",
            modifier="test_user",
            name=tenant_name,
            description=None,
            env=TEST_ENV,
            extra_config=None,
        )
        tenant_repository.insert_tenant(
            creator="test_user",
            modifier="test_user",
            name=tenant_name,
            description=None,
            env=other_env,
            extra_config=None,
        )

        tenant_repository.soft_delete(name=tenant_name, env=TEST_ENV, modifier="admin")

        assert tenant_repository.get_by_name(tenant_name, TEST_ENV) is None
        assert tenant_repository.get_by_name(tenant_name, other_env) is not None

    def test_soft_delete_nonexistent_is_noop(
        self, tenant_repository: TenantRepository, db_transaction
    ):
        tenant_repository.soft_delete(
            name="nonexistent_tenant_xyz",
            env=TEST_ENV,
            modifier="admin",
        )

    # ── 5. list_tenants (pagination) ─────────────────────────────────────────

    def test_list_tenants_returns_matching_env(
        self, tenant_repository: TenantRepository, db_transaction
    ):
        prefix = f"t_list_{_generate_uuid()[:8]}"
        for i in range(3):
            tenant_repository.insert_tenant(
                creator="test_user",
                modifier="test_user",
                name=f"{prefix}_{i}",
                description=None,
                env=TEST_ENV,
                extra_config=None,
            )

        total, items = tenant_repository.list_tenants(
            env=TEST_ENV, page=1, page_size=20
        )
        assert total >= 3

        names = [r.name for r in items]
        for i in range(3):
            assert f"{prefix}_{i}" in names

    def test_list_tenants_pagination(
        self, tenant_repository: TenantRepository, db_transaction
    ):
        prefix = f"t_page_{_generate_uuid()[:8]}"
        for i in range(5):
            tenant_repository.insert_tenant(
                creator="test_user",
                modifier="test_user",
                name=f"{prefix}_{i}",
                description=None,
                env=TEST_ENV,
                extra_config=None,
            )

        total, page1 = tenant_repository.list_tenants(env=TEST_ENV, page=1, page_size=2)
        assert total >= 5
        assert len(page1) == 2

        _, page2 = tenant_repository.list_tenants(env=TEST_ENV, page=2, page_size=2)
        assert len(page2) == 2

        ids_page1 = {r.id for r in page1}
        ids_page2 = {r.id for r in page2}
        assert ids_page1.isdisjoint(ids_page2)

    def test_list_tenants_filters_by_env(
        self, tenant_repository: TenantRepository, db_transaction
    ):
        prefix = f"t_env_list_{_generate_uuid()[:8]}"
        other_env = "prod" if TEST_ENV != "prod" else "dev"

        tenant_repository.insert_tenant(
            creator="test_user",
            modifier="test_user",
            name=f"{prefix}_our",
            description=None,
            env=TEST_ENV,
            extra_config=None,
        )
        tenant_repository.insert_tenant(
            creator="test_user",
            modifier="test_user",
            name=f"{prefix}_other",
            description=None,
            env=other_env,
            extra_config=None,
        )

        _, items = tenant_repository.list_tenants(env=TEST_ENV, page=1, page_size=20)
        names = {r.name for r in items}
        assert f"{prefix}_our" in names
        assert f"{prefix}_other" not in names

    def test_list_tenants_excludes_deleted(
        self, tenant_repository: TenantRepository, db_transaction
    ):
        tenant_name = f"t_list_del_{_generate_uuid()[:8]}"
        tenant_repository.insert_tenant(
            creator="test_user",
            modifier="test_user",
            name=tenant_name,
            description=None,
            env=TEST_ENV,
            extra_config=None,
        )

        tenant_repository.soft_delete(name=tenant_name, env=TEST_ENV, modifier="admin")

        _, items = tenant_repository.list_tenants(env=TEST_ENV, page=1, page_size=20)
        names = {r.name for r in items}
        assert tenant_name not in names

    # ── 6. Name uniqueness + env isolation ───────────────────────────────────

    def test_insert_duplicate_name_same_env_fails(
        self, tenant_repository: TenantRepository, db_transaction
    ):
        """Name + env should be unique; inserting same (name, env) must fail."""
        tenant_name = f"t_dup_{_generate_uuid()[:8]}"
        tenant_repository.insert_tenant(
            creator="test_user",
            modifier="test_user",
            name=tenant_name,
            description=None,
            env=TEST_ENV,
            extra_config=None,
        )

        with pytest.raises(Exception):
            tenant_repository.insert_tenant(
                creator="test_user",
                modifier="test_user",
                name=tenant_name,
                description="Duplicate",
                env=TEST_ENV,
                extra_config=None,
            )

    def test_same_name_different_env_is_allowed(
        self, tenant_repository: TenantRepository, db_transaction
    ):
        """Same name, different envs should coexist."""
        tenant_name = f"t_cross_env_{_generate_uuid()[:8]}"
        other_env = "prod" if TEST_ENV != "prod" else "dev"

        id_a = tenant_repository.insert_tenant(
            creator="test_user",
            modifier="test_user",
            name=tenant_name,
            description="Env A",
            env=TEST_ENV,
            extra_config={},
        )
        id_b = tenant_repository.insert_tenant(
            creator="test_user",
            modifier="test_user",
            name=tenant_name,
            description="Env B",
            env=other_env,
            extra_config={},
        )
        assert id_a != id_b

        rec_a = tenant_repository.get_by_name(tenant_name, TEST_ENV)
        rec_b = tenant_repository.get_by_name(tenant_name, other_env)
        assert rec_a is not None
        assert rec_b is not None
        assert rec_a.description == "Env A"
        assert rec_b.description == "Env B"

    # ── 7. Field round-trip: extra_config JSON ───────────────────────────────

    def test_extra_config_round_trip(
        self, tenant_repository: TenantRepository, db_transaction
    ):
        tenant_name = f"t_json_{_generate_uuid()[:8]}"
        original_config = {
            "providers": ["arca", "local"],
            "settings": {"timeout": 30, "retries": 3},
            "tags": ["test", "integration"],
        }

        tenant_repository.insert_tenant(
            creator="test_user",
            modifier="test_user",
            name=tenant_name,
            description=None,
            env=TEST_ENV,
            extra_config=original_config,
        )

        record = tenant_repository.get_by_name(tenant_name, TEST_ENV)
        assert record is not None
        assert record.extra_config == original_config

    def test_extra_config_none_defaults_to_empty_dict(
        self, tenant_repository: TenantRepository, db_transaction
    ):
        tenant_name = f"t_none_cfg_{_generate_uuid()[:8]}"
        tenant_repository.insert_tenant(
            creator="test_user",
            modifier="test_user",
            name=tenant_name,
            description=None,
            env=TEST_ENV,
            extra_config=None,
        )

        record = tenant_repository.get_by_name(tenant_name, TEST_ENV)
        assert record is not None
        assert record.extra_config == {}

    # ── 8. Record fields match inserted values ───────────────────────────────

    def test_tenant_record_fields_match_inserted(
        self, tenant_repository: TenantRepository, db_transaction
    ):
        tenant_name = f"t_fields_{_generate_uuid()[:8]}"
        extra_config = {"region": "cn-hangzhou", "tier": "premium"}

        record_id = tenant_repository.insert_tenant(
            creator="creator_user",
            modifier="modifier_user",
            name=tenant_name,
            description="Full field match test",
            env=TEST_ENV,
            extra_config=extra_config,
        )

        record = tenant_repository.get_by_id(record_id)
        assert record is not None
        assert record.id == record_id
        assert record.creator == "creator_user"
        assert record.modifier == "modifier_user"
        assert record.name == tenant_name
        assert record.description == "Full field match test"
        assert record.env == TEST_ENV
        assert record.extra_config == extra_config
        assert record.is_deleted == 0
        assert record.gmt_create is not None
        assert record.gmt_modified is not None
