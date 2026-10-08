"""Integration test fixtures for API Gateway core service.

Provides service-level fixtures wired to the bootstrap DI container.
"""

import os
import secrets
from collections.abc import Generator

import pytest
from sqlalchemy import text

from secbaas.community.api import OperationContext
from secbaas.community.core.repository.api_gateway import OrmAPIKeyRepository
from secbaas.community.core.service.api_gateway import DefaultAPIKeyService

TEST_TENANT = "test_tenant_int"


_created_record_ids: list[int] = []


@pytest.fixture(scope="session")
def api_key_repository(db_manager) -> OrmAPIKeyRepository:
    return OrmAPIKeyRepository(db_manager)


@pytest.fixture(scope="session")
def svc(api_key_repository: OrmAPIKeyRepository) -> DefaultAPIKeyService:
    """Session-scoped DefaultAPIKeyService instance."""
    return DefaultAPIKeyService(repository=api_key_repository)


@pytest.fixture(scope="session")
def ctx() -> OperationContext:
    """Session-scoped OperationContext."""
    return OperationContext(operator="test_user", env="test")


@pytest.fixture(scope="session")
def created_keys() -> list[int]:
    """Return the global list to track created record ids."""
    return _created_record_ids


@pytest.fixture(scope="session", autouse=True)
def cleanup_all_test_keys(api_key_repository: OrmAPIKeyRepository) -> Generator:
    yield
    if _created_record_ids:
        print(f"\n[CLEANUP] Hard-deleting {len(_created_record_ids)} test API keys...")
        try:
            with api_key_repository._database.orm_session() as session:
                named_params = {
                    f"id_{i}": rid for i, rid in enumerate(_created_record_ids)
                }
                placeholders = ",".join(
                    f":id_{i}" for i in range(len(_created_record_ids))
                )
                named_params["tenant"] = TEST_TENANT
                result = session.execute(
                    text(
                        f"DELETE FROM baas_api_key WHERE id IN ({placeholders}) AND tenant = :tenant"
                    ),
                    named_params,
                )
                session.commit()
                print(f"[CLEANUP] Deleted {result.rowcount} test API keys.")
        except Exception as e:
            print(f"  [ERROR] Failed to cleanup API keys: {e}")
        print("[CLEANUP] Done.")


def generate_key_data() -> tuple[str, str]:
    """Generate a test API key raw string and prefix."""
    raw = secrets.token_hex(32)
    return raw, raw[:8]
