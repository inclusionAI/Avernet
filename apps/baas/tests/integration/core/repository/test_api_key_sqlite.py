from __future__ import annotations

from uuid import uuid4

import pytest

from secbaas.community.bootstrap import get_container
from secbaas.community.core.repository.api_gateway import APIKeyRepository
from secbaas.community.core.utils.env_utils import get_current_env

pytestmark = pytest.mark.integration

TEST_ENV = get_current_env()
TEST_TENANT = "test_tenant"


def _generate_uuid() -> str:
    return uuid4().hex


def _make_key_hash() -> str:
    return f"sha256:{_generate_uuid()}"


class TestAPIKeySqliteOrmEquivalence:
    def test_insert_and_get_roundtrip(self):
        repo: APIKeyRepository = get_container().repository.api_gateway_repository()
        api_key_hash = _make_key_hash()
        api_key_prefix = _generate_uuid()[:8]
        app_id = _generate_uuid()

        key_id = repo.insert(
            api_key_hash=api_key_hash,
            api_key_prefix=api_key_prefix,
            key_name="sqlite_eq",
            app_id=app_id,
            app_type="API",
            description="SQLite equivalence test",
            rate_limit_rpm=100,
            rate_limit_rpd=10000,
            status="active",
            owner="u",
            tenant=TEST_TENANT,
            env=TEST_ENV,
            creator="c",
            policy=None,
        )
        assert key_id > 0

        record = repo.get_by_id(key_id)
        assert record is not None
        assert record.api_key_hash == api_key_hash
        assert record.api_key_prefix == api_key_prefix
        assert record.key_name == "sqlite_eq"
        assert record.status == "active"
        assert record.gmt_create is not None
        assert record.gmt_modified is not None

    def test_get_by_id_nonexistent(self):
        repo = get_container().repository.api_gateway_repository()
        assert repo.get_by_id(99999999) is None

    def test_deep_null_preservation(self):
        repo = get_container().repository.api_gateway_repository()
        api_key_hash = _make_key_hash()
        api_key_prefix = _generate_uuid()[:8]

        key_id = repo.insert(
            api_key_hash=api_key_hash,
            api_key_prefix=api_key_prefix,
            key_name="null_test",
            app_id=_generate_uuid(),
            app_type="API",
            description=None,
            rate_limit_rpm=None,
            rate_limit_rpd=None,
            status="active",
            owner="u",
            tenant=TEST_TENANT,
            env=TEST_ENV,
            creator="c",
            policy=None,
        )
        record = repo.get_by_id(key_id)
        assert record is not None
        assert record.description is None
        assert record.rate_limit_rpm is None
        assert record.policy is None

    def test_deep_update_all_fields(self):
        repo = get_container().repository.api_gateway_repository()
        api_key_prefix = _generate_uuid()[:8]
        app_id = _generate_uuid()
        key_id = repo.insert(
            api_key_hash=_make_key_hash(),
            api_key_prefix=api_key_prefix,
            key_name="before",
            app_id=app_id,
            app_type="API",
            description="Before",
            rate_limit_rpm=10,
            rate_limit_rpd=100,
            status="active",
            owner="orig",
            tenant=TEST_TENANT,
            env=TEST_ENV,
            creator="c",
            policy=None,
        )

        repo.update(
            key_id,
            key_name="after",
            description="After",
            owner="new_owner",
            modifier="updater",
        )
        record = repo.get_by_id(key_id)
        assert record is not None
        assert record.key_name == "after"
        assert record.description == "After"
        assert record.owner == "new_owner"
        assert record.modifier == "updater"
        assert record.api_key_prefix == api_key_prefix
        assert record.app_id == app_id
        assert record.status == "active"
        assert record.creator == "c"

    def test_deep_update_status(self):
        repo = get_container().repository.api_gateway_repository()
        key_id = repo.insert(
            api_key_hash=_make_key_hash(),
            api_key_prefix=_generate_uuid()[:8],
            key_name="status_test",
            app_id=_generate_uuid(),
            app_type="API",
            description=None,
            rate_limit_rpm=None,
            rate_limit_rpd=None,
            status="active",
            owner="u",
            tenant=TEST_TENANT,
            env=TEST_ENV,
            creator="c",
            policy=None,
        )
        repo.update_status(key_id, "revoked", modifier="admin")
        record = repo.get_by_id(key_id)
        assert record is not None
        assert record.status == "revoked"
        assert record.modifier == "admin"

    def test_get_by_prefix_and_status(self):
        repo = get_container().repository.api_gateway_repository()
        prefix = _generate_uuid()[:8]

        repo.insert(
            api_key_hash=_make_key_hash(),
            api_key_prefix=prefix,
            key_name="prefix_match",
            app_id=_generate_uuid(),
            app_type="API",
            description=None,
            rate_limit_rpm=None,
            rate_limit_rpd=None,
            status="active",
            owner="u",
            tenant=TEST_TENANT,
            env=TEST_ENV,
            creator="c",
            policy=None,
        )
        r = repo.get_by_prefix_and_status(prefix, "active")
        assert r is not None and r.api_key_prefix == prefix
        assert repo.get_by_prefix_and_status(prefix, "revoked") is None
        assert repo.get_by_prefix_and_status("zz_nope", "active") is None

    def test_list_keys_pagination(self):
        repo = get_container().repository.api_gateway_repository()
        app_id = _generate_uuid()
        for i in range(5):
            repo.insert(
                api_key_hash=_make_key_hash(),
                api_key_prefix=_generate_uuid()[:8],
                key_name=f"list_{i}",
                app_id=app_id,
                app_type="API",
                description=None,
                rate_limit_rpm=None,
                rate_limit_rpd=None,
                status="active",
                owner="u",
                tenant=TEST_TENANT,
                env=TEST_ENV,
                creator="c",
                policy=None,
            )
        total, page1 = repo.list_keys(app_id=app_id, env=TEST_ENV, page=1, page_size=3)
        assert total == 5
        assert len(page1) == 3

        total2, page2 = repo.list_keys(app_id=app_id, env=TEST_ENV, page=2, page_size=3)
        assert total2 == 5
        assert len(page2) == 2

    def test_list_keys_status_filter(self):
        repo = get_container().repository.api_gateway_repository()
        app_id = _generate_uuid()
        repo.insert(
            api_key_hash=_make_key_hash(),
            api_key_prefix=_generate_uuid()[:8],
            key_name="active_key",
            app_id=app_id,
            app_type="API",
            description=None,
            rate_limit_rpm=None,
            rate_limit_rpd=None,
            status="active",
            owner="u",
            tenant=TEST_TENANT,
            env=TEST_ENV,
            creator="c",
            policy=None,
        )
        repo.insert(
            api_key_hash=_make_key_hash(),
            api_key_prefix=_generate_uuid()[:8],
            key_name="revoked_key",
            app_id=app_id,
            app_type="API",
            description=None,
            rate_limit_rpm=None,
            rate_limit_rpd=None,
            status="revoked",
            owner="u",
            tenant=TEST_TENANT,
            env=TEST_ENV,
            creator="c",
            policy=None,
        )
        total_a, items_a = repo.list_keys(app_id=app_id, status="active", env=TEST_ENV)
        assert total_a == 1 and items_a[0].status == "active"

        total_r, items_r = repo.list_keys(app_id=app_id, status="revoked", env=TEST_ENV)
        assert total_r == 1 and items_r[0].status == "revoked"

    def test_exists_prefix(self):
        repo = get_container().repository.api_gateway_repository()
        prefix = _generate_uuid()[:8]
        repo.insert(
            api_key_hash=_make_key_hash(),
            api_key_prefix=prefix,
            key_name="exists_test",
            app_id=_generate_uuid(),
            app_type="API",
            description=None,
            rate_limit_rpm=None,
            rate_limit_rpd=None,
            status="active",
            owner="u",
            tenant=TEST_TENANT,
            env=TEST_ENV,
            creator="c",
            policy=None,
        )
        assert repo.exists_prefix(prefix) is True
        assert repo.exists_prefix("zz_nonexistent") is False
