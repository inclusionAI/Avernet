"""Integration tests for APIKeyRepository Protocol against ZDAS MySQL.

Every test uses ONLY the APIKeyRepository Protocol — no OrmAPIKeyRepository
references allowed. db_transaction auto-rollback ensures no data persists.

Tests cover all 7 protocol methods:
  1. insert + get_by_id (full-field round-trip)
  2. get_by_id None for missing
  3. get_by_prefix_and_status (match + no-match)
  4. list_keys (pagination, status filter, creator filter)
  5. update (all updatable fields)
  6. update_status
  7. exists_prefix (True + False)
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

import pytest

from secbaas.community.core.repository.api_gateway import APIKeyRecord, APIKeyRepository
from secbaas.community.core.utils.env_utils import get_current_env

mysql_connector = pytest.importorskip("mysql.connector")

pytestmark = pytest.mark.integration

TEST_ENV = get_current_env()
TEST_TENANT = "test_tenant"


def _generate_uuid() -> str:
    return uuid4().hex


def _make_key_hash() -> str:
    return f"sha256:{_generate_uuid()}"


class TestAPIKeyRepositoryProtocol:
    # ── 1. insert + get_by_id (full-field round-trip) ────────────────────────

    def test_insert_and_get_by_id(
        self, api_gateway_repository: APIKeyRepository, db_transaction
    ):
        api_key_hash = _make_key_hash()
        api_key_prefix = _generate_uuid()[:8]
        app_id = _generate_uuid()
        key_name = f"test_key_{_generate_uuid()[:8]}"
        key_id = api_gateway_repository.insert(
            api_key_hash=api_key_hash,
            api_key_prefix=api_key_prefix,
            key_name=key_name,
            app_id=app_id,
            app_type="API",
            description="Integration test key",
            rate_limit_rpm=100,
            rate_limit_rpd=10000,
            status="active",
            owner="test_user",
            tenant=TEST_TENANT,
            env=TEST_ENV,
            creator="test_creator",
            policy='{"allow": "all"}',
        )
        assert key_id > 0

        record = api_gateway_repository.get_by_id(key_id)
        assert record is not None
        assert record.id == key_id
        assert record.api_key_hash == api_key_hash
        assert record.api_key_prefix == api_key_prefix
        assert record.key_name == key_name
        assert record.app_id == app_id
        assert record.app_type == "API"
        assert record.description == "Integration test key"
        assert record.rate_limit_rpm == 100
        assert record.rate_limit_rpd == 10000
        assert record.status == "active"
        assert record.owner == "test_user"
        assert record.tenant == TEST_TENANT
        assert record.env == TEST_ENV
        assert record.creator == "test_creator"
        assert record.policy == '{"allow": "all"}'
        assert isinstance(record.gmt_create, datetime)
        assert isinstance(record.gmt_modified, datetime)
        assert record.modifier == "test_creator"

    # ── 2. get_by_id None for missing ────────────────────────────────────────

    def test_get_by_id_returns_none_for_missing(
        self, api_gateway_repository: APIKeyRepository, db_transaction
    ):
        result = api_gateway_repository.get_by_id(99999999)
        assert result is None

    # ── 3. get_by_prefix_and_status ──────────────────────────────────────────

    def test_get_by_prefix_and_status_match(
        self, api_gateway_repository: APIKeyRepository, db_transaction
    ):
        api_key_hash = _make_key_hash()
        api_key_prefix = _generate_uuid()[:8]
        app_id = _generate_uuid()
        api_gateway_repository.insert(
            api_key_hash=api_key_hash,
            api_key_prefix=api_key_prefix,
            key_name="prefix_test",
            app_id=app_id,
            app_type="API",
            description="Prefix match test",
            rate_limit_rpm=None,
            rate_limit_rpd=None,
            status="active",
            owner="test_user",
            tenant=TEST_TENANT,
            env=TEST_ENV,
            creator="test_creator",
            policy=None,
        )

        record = api_gateway_repository.get_by_prefix_and_status(
            api_key_prefix, "active"
        )
        assert record is not None
        assert record.api_key_prefix == api_key_prefix
        assert record.status == "active"

    def test_get_by_prefix_and_status_no_match(
        self, api_gateway_repository: APIKeyRepository, db_transaction
    ):
        api_key_hash = _make_key_hash()
        api_key_prefix = _generate_uuid()[:8]
        app_id = _generate_uuid()
        api_gateway_repository.insert(
            api_key_hash=api_key_hash,
            api_key_prefix=api_key_prefix,
            key_name="no_match_test",
            app_id=app_id,
            app_type="API",
            description="No match test",
            rate_limit_rpm=None,
            rate_limit_rpd=None,
            status="active",
            owner="test_user",
            tenant=TEST_TENANT,
            env=TEST_ENV,
            creator="test_creator",
            policy=None,
        )

        record = api_gateway_repository.get_by_prefix_and_status(
            "zk_non_existent", "active"
        )
        assert record is None

        record = api_gateway_repository.get_by_prefix_and_status(
            api_key_prefix, "revoked"
        )
        assert record is None

    # ── 4. list_keys ─────────────────────────────────────────────────────────

    def test_list_keys_pagination(
        self, api_gateway_repository: APIKeyRepository, db_transaction
    ):
        app_id = _generate_uuid()
        for i in range(3):
            api_gateway_repository.insert(
                api_key_hash=_make_key_hash(),
                api_key_prefix=_generate_uuid()[:8],
                key_name=f"list_test_{i}",
                app_id=app_id,
                app_type="API",
                description=f"Key {i}",
                rate_limit_rpm=None,
                rate_limit_rpd=None,
                status="active",
                owner="test_user",
                tenant=TEST_TENANT,
                env=TEST_ENV,
                creator="test_creator",
                policy=None,
            )

        # Page 1, page_size=2
        total, items = api_gateway_repository.list_keys(
            app_id=app_id,
            env=TEST_ENV,
            page=1,
            page_size=2,
        )
        assert total == 3
        assert len(items) == 2

        # Page 2, page_size=2
        total2, items2 = api_gateway_repository.list_keys(
            app_id=app_id,
            env=TEST_ENV,
            page=2,
            page_size=2,
        )
        assert total2 == 3
        assert len(items2) == 1

    def test_list_keys_status_filter(
        self, api_gateway_repository: APIKeyRepository, db_transaction
    ):
        app_id = _generate_uuid()
        api_gateway_repository.insert(
            api_key_hash=_make_key_hash(),
            api_key_prefix=_generate_uuid()[:8],
            key_name="active_key",
            app_id=app_id,
            app_type="API",
            description="Active key",
            rate_limit_rpm=None,
            rate_limit_rpd=None,
            status="active",
            owner="test_user",
            tenant=TEST_TENANT,
            env=TEST_ENV,
            creator="test_creator",
            policy=None,
        )
        api_gateway_repository.insert(
            api_key_hash=_make_key_hash(),
            api_key_prefix=_generate_uuid()[:8],
            key_name="revoked_key",
            app_id=app_id,
            app_type="API",
            description="Revoked key",
            rate_limit_rpm=None,
            rate_limit_rpd=None,
            status="revoked",
            owner="test_user",
            tenant=TEST_TENANT,
            env=TEST_ENV,
            creator="test_creator",
            policy=None,
        )

        total_active, items_active = api_gateway_repository.list_keys(
            app_id=app_id,
            status="active",
            env=TEST_ENV,
        )
        assert total_active == 1
        assert items_active[0].status == "active"

        total_revoked, items_revoked = api_gateway_repository.list_keys(
            app_id=app_id,
            status="revoked",
            env=TEST_ENV,
        )
        assert total_revoked == 1
        assert items_revoked[0].status == "revoked"

    def test_list_keys_creator_filter(
        self, api_gateway_repository: APIKeyRepository, db_transaction
    ):
        app_id = _generate_uuid()
        creator_a = f"creator_a_{_generate_uuid()[:6]}"
        creator_b = f"creator_b_{_generate_uuid()[:6]}"

        api_gateway_repository.insert(
            api_key_hash=_make_key_hash(),
            api_key_prefix=_generate_uuid()[:8],
            key_name="key_a",
            app_id=app_id,
            app_type="API",
            description=None,
            rate_limit_rpm=None,
            rate_limit_rpd=None,
            status="active",
            owner="test_user",
            tenant=TEST_TENANT,
            env=TEST_ENV,
            creator=creator_a,
            policy=None,
        )
        api_gateway_repository.insert(
            api_key_hash=_make_key_hash(),
            api_key_prefix=_generate_uuid()[:8],
            key_name="key_b",
            app_id=app_id,
            app_type="API",
            description=None,
            rate_limit_rpm=None,
            rate_limit_rpd=None,
            status="active",
            owner="test_user",
            tenant=TEST_TENANT,
            env=TEST_ENV,
            creator=creator_b,
            policy=None,
        )

        total_a, items_a = api_gateway_repository.list_keys(
            app_id=app_id,
            creator=creator_a,
            env=TEST_ENV,
        )
        assert total_a == 1
        assert items_a[0].creator == creator_a

        total_b, items_b = api_gateway_repository.list_keys(
            app_id=app_id,
            creator=creator_b,
            env=TEST_ENV,
        )
        assert total_b == 1
        assert items_b[0].creator == creator_b

    # ── 5. update ────────────────────────────────────────────────────────────

    def test_update_all_fields(
        self, api_gateway_repository: APIKeyRepository, db_transaction
    ):
        api_key_hash = _make_key_hash()
        api_key_prefix = _generate_uuid()[:8]
        app_id = _generate_uuid()
        key_id = api_gateway_repository.insert(
            api_key_hash=api_key_hash,
            api_key_prefix=api_key_prefix,
            key_name="before_update",
            app_id=app_id,
            app_type="API",
            description="Before update",
            rate_limit_rpm=10,
            rate_limit_rpd=100,
            status="active",
            owner="orig_owner",
            tenant=TEST_TENANT,
            env=TEST_ENV,
            creator="test_creator",
            policy='{"allow": "read"}',
        )

        api_gateway_repository.update(
            key_id,
            key_name="after_update",
            description="After update",
            app_type="WEB",
            rate_limit_rpm=200,
            rate_limit_rpd=20000,
            owner="new_owner",
            tenant="updated_tenant",
            modifier="test_modifier",
            policy='{"allow": "write"}',
        )

        record = api_gateway_repository.get_by_id(key_id)
        assert record is not None
        assert record.key_name == "after_update"
        assert record.description == "After update"
        assert record.app_type == "WEB"
        assert record.rate_limit_rpm == 200
        assert record.rate_limit_rpd == 20000
        assert record.owner == "new_owner"
        assert record.tenant == "updated_tenant"
        assert record.modifier == "test_modifier"
        assert record.policy == '{"allow": "write"}'
        assert record.api_key_hash == api_key_hash
        assert record.api_key_prefix == api_key_prefix
        assert record.app_id == app_id
        assert record.status == "active"
        assert record.creator == "test_creator"

    def test_update_partial_fields(
        self, api_gateway_repository: APIKeyRepository, db_transaction
    ):
        api_key_hash = _make_key_hash()
        api_key_prefix = _generate_uuid()[:8]
        app_id = _generate_uuid()
        key_id = api_gateway_repository.insert(
            api_key_hash=api_key_hash,
            api_key_prefix=api_key_prefix,
            key_name="partial_before",
            app_id=app_id,
            app_type="API",
            description="Will be updated",
            rate_limit_rpm=50,
            rate_limit_rpd=500,
            status="active",
            owner="partial_owner",
            tenant=TEST_TENANT,
            env=TEST_ENV,
            creator="test_creator",
            policy=None,
        )

        api_gateway_repository.update(
            key_id,
            key_name="partial_after",
        )

        record = api_gateway_repository.get_by_id(key_id)
        assert record is not None
        assert record.key_name == "partial_after"
        assert record.description == "Will be updated"
        assert record.app_type == "API"
        assert record.rate_limit_rpm == 50
        assert record.rate_limit_rpd == 500
        assert record.owner == "partial_owner"
        assert record.tenant == TEST_TENANT

    # ── 6. update_status ─────────────────────────────────────────────────────

    def test_update_status(
        self, api_gateway_repository: APIKeyRepository, db_transaction
    ):
        api_key_hash = _make_key_hash()
        api_key_prefix = _generate_uuid()[:8]
        app_id = _generate_uuid()
        key_id = api_gateway_repository.insert(
            api_key_hash=api_key_hash,
            api_key_prefix=api_key_prefix,
            key_name="status_test",
            app_id=app_id,
            app_type="API",
            description="Status test",
            rate_limit_rpm=None,
            rate_limit_rpd=None,
            status="active",
            owner="test_user",
            tenant=TEST_TENANT,
            env=TEST_ENV,
            creator="test_creator",
            policy=None,
        )

        api_gateway_repository.update_status(key_id, "revoked", modifier="admin")

        record = api_gateway_repository.get_by_id(key_id)
        assert record is not None
        assert record.status == "revoked"
        assert record.modifier == "admin"

    def test_update_status_without_modifier(
        self, api_gateway_repository: APIKeyRepository, db_transaction
    ):
        api_key_hash = _make_key_hash()
        api_key_prefix = _generate_uuid()[:8]
        app_id = _generate_uuid()
        key_id = api_gateway_repository.insert(
            api_key_hash=api_key_hash,
            api_key_prefix=api_key_prefix,
            key_name="status_no_mod",
            app_id=app_id,
            app_type="API",
            description="Status test no modifier",
            rate_limit_rpm=None,
            rate_limit_rpd=None,
            status="active",
            owner="test_user",
            tenant=TEST_TENANT,
            env=TEST_ENV,
            creator="test_creator",
            policy=None,
        )

        api_gateway_repository.update_status(key_id, "revoked")

        record = api_gateway_repository.get_by_id(key_id)
        assert record is not None
        assert record.status == "revoked"

    # ── 7. exists_prefix ─────────────────────────────────────────────────────

    def test_exists_prefix_true(
        self, api_gateway_repository: APIKeyRepository, db_transaction
    ):
        api_key_hash = _make_key_hash()
        api_key_prefix = _generate_uuid()[:8]
        app_id = _generate_uuid()
        api_gateway_repository.insert(
            api_key_hash=api_key_hash,
            api_key_prefix=api_key_prefix,
            key_name="exists_test",
            app_id=app_id,
            app_type="API",
            description=None,
            rate_limit_rpm=None,
            rate_limit_rpd=None,
            status="active",
            owner="test_user",
            tenant=TEST_TENANT,
            env=TEST_ENV,
            creator="test_creator",
            policy=None,
        )

        assert api_gateway_repository.exists_prefix(api_key_prefix) is True

    def test_exists_prefix_false(
        self, api_gateway_repository: APIKeyRepository, db_transaction
    ):
        assert api_gateway_repository.exists_prefix("zk_non_existent_prefix") is False
