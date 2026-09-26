"""Shared fixtures for domain service integration tests.

Provides centralized tracking and cleanup for all test entities.

Optimized for minimal sandbox creation:
- ONE shared tenant/template for all PaaS tests
- Database-only tests use DB records without PaaS calls
- All test data is cleaned up after session
"""

from __future__ import annotations

import os  # noqa: E402
import random  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402
from uuid import uuid4  # noqa: E402

import pytest  # noqa: E402

from secbaas.community.core.utils.env_utils import get_current_env

TEST_ENV = get_current_env()

# Fixed test tenant configuration (used for create_test_tenant helper)
FIXED_TENANT_NAME = "test_tenant"


# === Environment Mock Fixture ===


@pytest.fixture(autouse=True)
def mock_get_current_env():
    """Automatically mock get_current_env to return TEST_ENV for all integration tests."""
    from unittest.mock import patch

    with patch(
        "secbaas.community.core.utils.env_utils.get_current_env", return_value=TEST_ENV
    ):
        yield


# === PaaS Configuration Helpers ===


def _get_arca_config() -> tuple[str, str, str]:
    """Get Arca PaaS configuration from environment."""
    return (
        os.environ.get("ARCA_BASE_URL", ""),
        os.environ.get("ARCA_API_KEY", ""),
        os.environ.get("ARCA_TEMPLATE_ID", ""),
    )


def _is_arca_configured() -> bool:
    """Check if Arca PaaS is configured with valid URL format."""
    arca_base_url, arca_api_key, arca_template_id = _get_arca_config()
    if not (arca_base_url and arca_api_key and arca_template_id):
        return False
    # Validate URL has proper protocol
    if not arca_base_url.startswith(("http://", "https://")):
        return False
    return True


def _get_paas_config() -> dict:
    """Get PaaS configuration dict for DeviceService from environment."""
    arca_base_url, arca_api_key, _ = _get_arca_config()
    return {
        "paas_base_url": arca_base_url,
        "paas_api_key": arca_api_key,
        "paas_timeout": float(os.environ.get("ARCA_TIMEOUT", "60.0")),
    }


# === Session-Scoped Fixtures ===


@pytest.fixture(scope="session")
def skip_if_zdas_unavailable():
    """No-op — bootstrap provides SQLite; always available."""
    pass


# === Global Tracking Lists for Cleanup ===

_created_bot_ids: list[int] = []
_created_device_ids: list[int] = []
_created_rel_ids: list[int] = []
_created_publish_ids: list[int] = []
_created_publish_batch_ids: list[int] = []
_created_publish_record_ids: list[int] = []
_created_session_ids: list[int] = []
_created_config_ids: list[int] = []
_created_tenant_ids: list[int] = []
_created_template_ids: list[int] = []
_created_sandbox_ids: list[str] = []

# Shared tenant/template for PaaS tests (created once)
_shared_tenant_name: str | None = None
_shared_template_id: int | None = None


# === Tracking Fixture Accessors ===


@pytest.fixture(scope="session")
def created_bot_ids() -> list[int]:
    """Returns the global list to track created bot record ids."""
    return _created_bot_ids


@pytest.fixture(scope="session")
def created_device_ids() -> list[int]:
    """Returns the global list to track created device record ids."""
    return _created_device_ids


@pytest.fixture(scope="session")
def created_rel_ids() -> list[int]:
    """Returns the global list to track created relationship record ids."""
    return _created_rel_ids


@pytest.fixture(scope="session")
def created_publish_ids() -> list[int]:
    """Returns the global list to track created publish record ids."""
    return _created_publish_ids


@pytest.fixture(scope="session")
def created_publish_batch_ids() -> list[int]:
    """Returns the global list to track created publish batch ids."""
    return _created_publish_batch_ids


@pytest.fixture(scope="session")
def created_publish_record_ids() -> list[int]:
    """Returns the global list to track created publish record ids."""
    return _created_publish_record_ids


@pytest.fixture(scope="session")
def created_session_ids() -> list[int]:
    """Returns the global list to track created session ids."""
    return _created_session_ids


@pytest.fixture(scope="session")
def created_config_ids() -> list[int]:
    """Returns the global list to track created config record ids."""
    return _created_config_ids


@pytest.fixture(scope="session")
def created_tenant_ids() -> list[int]:
    """Returns the global list to track created tenant ids."""
    return _created_tenant_ids


@pytest.fixture(scope="session")
def created_template_ids() -> list[int]:
    """Returns the global list to track created template ids."""
    return _created_template_ids


# === Session-Scoped Cleanup ===


def _batch_delete(
    conn: Any,
    table: str,
    id_column: str,
    ids: list[int],
    env: str | None = None,
    tenant: str | None = None,
) -> int:
    """Delete records by ID with optional env/tenant filter for safety."""
    if not ids:
        return 0

    total_deleted = 0
    batch_size = 100
    for i in range(0, len(ids), batch_size):
        batch = ids[i : i + batch_size]
        placeholders = ",".join(["?"] * len(batch))  # SQLite-compatible

        where_clauses = [f"{id_column} IN ({placeholders})"]
        params: list[int | str] = list(batch)

        if env is not None:
            where_clauses.append("env = ?")
            params.append(env)

        if tenant is not None:
            where_clauses.append("tenant = ?")
            params.append(tenant)

        where_clause = " AND ".join(where_clauses)

        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"DELETE FROM {table} WHERE {where_clause}", tuple(params)
                )
                total_deleted += cursor.rowcount
        except AttributeError:
            from sqlalchemy import text as sa_text

            # Convert positional ? params to named :p0, :p1, ... for SQLAlchemy
            named_params = {}
            param_index = 0
            parts = []
            for segment in (f"DELETE FROM {table} WHERE {where_clause}").split("?"):
                if param_index == 0:
                    parts.append(segment)
                else:
                    name = f"p{param_index - 1}"
                    named_params[name] = params[param_index - 1]
                    parts.append(f":{name}")
                    parts.append(segment)
                param_index += 1
            result = conn.execute(sa_text("".join(parts)), named_params)
            total_deleted += result.rowcount
    return total_deleted


def _do_cleanup_deletes(conn: Any) -> None:
    """Execute all cleanup deletes in dependency order using the given connection."""
    if _created_publish_record_ids:
        deleted = _batch_delete(
            conn, "baas_publish_record", "id", _created_publish_record_ids, TEST_ENV
        )
        print(f"[CLEANUP] Deleted {deleted} publish_records.")

    if _created_publish_batch_ids:
        deleted = _batch_delete(
            conn, "baas_publish_batch", "id", _created_publish_batch_ids, TEST_ENV
        )
        print(f"[CLEANUP] Deleted {deleted} publish_batches.")

    if _created_publish_ids:
        deleted = _batch_delete(
            conn, "baas_publish", "id", _created_publish_ids, TEST_ENV
        )
        print(f"[CLEANUP] Deleted {deleted} publishes.")

    if _created_rel_ids:
        deleted = _batch_delete(
            conn, "baas_bot_device_rel", "id", _created_rel_ids, TEST_ENV
        )
        print(f"[CLEANUP] Deleted {deleted} relationships.")

    if _created_device_ids:
        deleted = _batch_delete(
            conn, "baas_device", "id", _created_device_ids, TEST_ENV
        )
        print(f"[CLEANUP] Deleted {deleted} devices.")

    if _created_bot_ids:
        deleted = _batch_delete(conn, "baas_bot", "id", _created_bot_ids, TEST_ENV)
        print(f"[CLEANUP] Deleted {deleted} bots.")

    if _created_session_ids:
        deleted = _batch_delete(
            conn, "baas_bot_session", "id", _created_session_ids, TEST_ENV
        )
        print(f"[CLEANUP] Deleted {deleted} sessions.")

    if _created_config_ids:
        deleted = _batch_delete(
            conn, "baas_system_config", "id", _created_config_ids, TEST_ENV
        )
        print(f"[CLEANUP] Deleted {deleted} configs.")

    if _created_template_ids:
        deleted = _batch_delete(
            conn,
            "baas_device_template",
            "id",
            _created_template_ids,
            tenant=FIXED_TENANT_NAME,
        )
        print(f"[CLEANUP] Deleted {deleted} templates.")

    if _created_tenant_ids:
        deleted = _batch_delete(
            conn, "baas_tenant", "id", _created_tenant_ids, TEST_ENV
        )
        print(f"[CLEANUP] Deleted {deleted} tenants.")


@pytest.fixture(scope="session", autouse=True)
def cleanup_all_test_data(
    db_manager,
    bot_repository,
    device_repository,
    rel_repository,
    session_repository,
    system_config_repository,
    publish_repository,
    publish_batch_repository,
):
    """Session-scoped auto-cleanup: delete ONLY created records by ID.

    Cleanup strategy: Delete by tracked IDs with env filter for safety.
    Order matters due to foreign key constraints:
    publish_records -> publish_batches -> publishes -> relationships -> devices -> bots -> sessions -> configs -> templates -> tenants
    """
    yield

    print("\n[CLEANUP] Deleting test data...")

    # First, destroy PaaS sandboxes (stub only — real Arca not used in tests)
    if _created_sandbox_ids:
        print(f"[CLEANUP] Destroying {len(_created_sandbox_ids)} PaaS sandboxes...")
        from secbaas.community.plugins.sandbox.arca import StubArcaSandboxPlugin

        plugin = StubArcaSandboxPlugin()
        for sandbox_id in _created_sandbox_ids:
            try:
                plugin.connect_sync_sandbox(sandbox_id).destroy()
                print(f"  [CLEANUP] Destroyed sandbox: {sandbox_id}")
            except Exception as e:
                print(f"  [WARN] Failed to destroy sandbox {sandbox_id}: {e}")

    # Database cleanup — use orm_session() which works for both SQLite (Stub)
    # and ZDAS (Real) database plugins.  The raw session() call defaults to
    # datasource_name="default" which doesn't match any ZDAS config name
    # (agentclawdb_ds), causing ValueError.  orm_session() delegates to
    # plugin.orm_session() which handles datasource selection internally.
    try:
        with db_manager.orm_session() as session:
            _do_cleanup_deletes(session.connection())
            session.commit()
        print("[CLEANUP] Done.")
    except Exception as e:
        print(f"[CLEANUP] Non-fatal error during cleanup: {e}")
        # Cleanup failures are non-fatal — in-memory SQLite may be recycled
        # between test files when running the full integration suite.


# === Shared PaaS Setup ===


@pytest.fixture(scope="session")
def shared_paas_setup():
    """Create ONE shared tenant and template for all PaaS tests.

    This minimizes sandbox creation by reusing the same tenant/template
    across all tests that need real PaaS operations.
    """
    global _shared_tenant_name, _shared_template_id

    if not _is_arca_configured():
        pytest.skip(
            "ARCA_BASE_URL, ARCA_API_KEY, and ARCA_TEMPLATE_ID required in .env"
        )
        return

    # Only create once per session
    if _shared_tenant_name is None:
        import asyncio

        from secbaas.community.api.template_manage import (
            ArcaTemplateConfig,
            TemplateCreate,
        )
        from secbaas.community.bootstrap import get_container

        arca_base_url, arca_api_key, arca_template_id = _get_arca_config()
        tenant_name = f"test_tenant_{int(time.time() * 1000000) % 10000000000}"

        async def _create():
            from secbaas.community.api.tenant_manage import TenantType

            tenant_repo = get_container().repository.tenant_repository()
            record_id = tenant_repo.insert_tenant(
                name=tenant_name,
                env=TEST_ENV,
                creator="test_user",
                modifier="test_user",
                description=None,
                extra_config={
                    "paas_base_url": arca_base_url,
                    "paas_api_key": arca_api_key,
                    "paas_timeout": 60.0,
                },
            )
            _created_tenant_ids.append(record_id)  # Track primary key for cleanup

            template_uuid = f"test-shared-{int(time.time() * 1000000) % 10000000000}"
            template_id_val = random.randint(1, 999999999)
            _svc = get_container().services.device_template_service()
            template = _svc.create_template(
                tenant=tenant_name,
                data=TemplateCreate(
                    template_uuid=template_uuid,
                    template_id=template_id_val,
                    type=TenantType.ARCA,
                    name=f"Shared Test Template {template_uuid}",
                    description=None,
                    config=ArcaTemplateConfig(
                        type="ARCA",
                        base_url=arca_base_url,
                        api_key=arca_api_key,
                        template_id=arca_template_id,
                        arca_template_id_pre=None,
                        arca_template_id_prod=None,
                        oss_mount_id=None,
                    ),
                    operator="test_user",
                ),
            )
            _created_template_ids.append(template.id)
            return tenant_name, template.id

        _shared_tenant_name, _shared_template_id = (
            asyncio.get_event_loop().run_until_complete(_create())
        )
        print(
            f"\n[SETUP] Created shared PaaS tenant/template: tenant={_shared_tenant_name}, template={_shared_template_id}"
        )

    return _shared_tenant_name, _shared_template_id


@pytest.fixture
def paas_tenant_template(shared_paas_setup):
    """Get shared tenant/template for tests that need PaaS.

    Skip the test if PaaS is not configured.
    """
    if shared_paas_setup is None:
        pytest.skip("PaaS not configured")
    return shared_paas_setup


# === Helper Functions ===


def generate_unique_bot_uuid() -> str:
    """Generate unique bot UUID for testing."""
    return uuid4().hex


def generate_unique_device_uuid() -> str:
    """Generate unique device UUID for testing."""
    return uuid4().hex


def generate_unique_session_id() -> str:
    """Generate unique session ID for testing."""
    return f"SESSION-{uuid4().hex}"


def generate_unique_config_key() -> str:
    """Generate unique config key for testing."""
    return f"test.config.{int(time.time() * 1000000) % 10000000000}"


def create_test_tenant(
    created_tenant_ids: list[int], created_template_ids: list[int]
) -> tuple[str, int]:
    """Create a test tenant and template, return (tenant_name, template_id).

    Note: Uses fixed tenant_name="test_tenant" for all tests.
    Returns tenant_name since services use tenant name for isolation.
    """
    from secbaas.community.api.template_manage import (
        ArcaTemplateConfig,
        TemplateCreate,
    )
    from secbaas.community.api.tenant_manage import TenantType
    from secbaas.community.bootstrap import get_container

    tenant_name = FIXED_TENANT_NAME

    # Get or create tenant directly in database
    tenant_repo = get_container().repository.tenant_repository()
    existing_tenant = tenant_repo.get_by_name(tenant_name, TEST_ENV)

    if not existing_tenant:
        record_id = tenant_repo.insert_tenant(
            name=tenant_name,
            env=TEST_ENV,
            creator="test_user",
            modifier="test_user",
            description=None,
            extra_config=None,
        )
        created_tenant_ids.append(record_id)  # Track primary key for cleanup

    template_uuid = f"test-template-{int(time.time() * 1000000) % 10000000000}"
    template_id_val = random.randint(1, 999999999)
    _svc = get_container().services.device_template_service()
    template = _svc.create_template(
        tenant=tenant_name,
        data=TemplateCreate(
            template_uuid=template_uuid,
            template_id=template_id_val,
            type=TenantType.ARCA,
            name=f"Test Template {template_uuid}",
            description=None,
            config=ArcaTemplateConfig(
                type="ARCA",
                base_url="http://test",
                api_key="test",
                template_id="",
                arca_template_id_pre=None,
                arca_template_id_prod=None,
                oss_mount_id=None,
            ),
            operator="test_user",
        ),
    )
    created_template_ids.append(template.id)

    return tenant_name, template.id


def create_test_device_record(
    device_repository: Any,
    tenant: str,
    env: str = TEST_ENV,
    status: str = "ACTIVE",
    created_device_ids: list[int] | None = None,
) -> int:
    """Create a test device record directly in the database."""

    device_uuid = generate_unique_device_uuid()
    device_id = device_repository.insert_device(
        device_uuid=device_uuid,
        tenant=tenant,
        env=env,
        domain="test_domain",
        creator="test_user",
        modifier="test_user",
        status=status,
        provider_type="SIGMA",
        provider_device_id=None,
        provider_device_props={},
        extra_config={},
    )
    if created_device_ids is not None:
        created_device_ids.append(device_id)
    return device_id


def create_test_devices_for_bot(
    device_repository: Any,
    rel_repository: Any,
    tenant: str,
    bot_id: int,
    env: str = TEST_ENV,
    device_status: str = "ACTIVE",
    num_devices: int = 1,
    created_device_ids: list[int] | None = None,
    created_rel_ids: list[int] | None = None,
) -> list[dict[str, Any]]:
    """Create device records linked to a bot, so publish flows have eligible devices.

    Needed because _create_device_records_for_publish now validates
    that eligible devices exist and raises ValueError when none are found.

    Returns list of dicts with keys 'id' and 'device_uuid'.
    """
    device_records: list[dict[str, Any]] = []
    for _ in range(num_devices):
        device_uuid = generate_unique_device_uuid()
        device_id = device_repository.insert_device(
            device_uuid=device_uuid,
            tenant=tenant,
            env=env,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status=device_status,
            provider_type="SIGMA",
            provider_device_id=None,
            provider_device_props={},
            extra_config={},
        )
        if created_device_ids is not None:
            created_device_ids.append(device_id)
        rel_id = rel_repository.insert_rel(
            bot_id=bot_id,
            device_uuid=device_uuid,
            tenant=tenant,
            env=env,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
        )
        if created_rel_ids is not None:
            created_rel_ids.append(rel_id)
        device_records.append({"id": device_id, "device_uuid": device_uuid})
    return device_records


def create_test_bot_record(
    bot_repository: Any,
    tenant: str,
    env: str = TEST_ENV,
    status: str = "PENDING",
    created_bot_ids: list[int] | None = None,
) -> int:
    """Create a test bot record directly in the database."""

    bot_uuid = generate_unique_bot_uuid()
    bot_id = bot_repository.insert_bot(
        bot_uuid=bot_uuid,
        tenant=tenant,
        env=env,
        domain="test_domain",
        creator="test_user",
        modifier="test_user",
        status=status,
        name=f"Test Bot {bot_uuid[:8]}",
        description="Test bot for integration tests",
        template_uuid=None,
        replica_desired=1,
        replica_minimum=1,
        replica_maximum=10,
        auto_scaling_enabled=0,
        sla_grade="standard",
        extra_config={},
    )
    if created_bot_ids is not None:
        created_bot_ids.append(bot_id)
    return bot_id


def create_test_bot_device_rel(
    rel_repository: Any,
    bot_id: int,
    device_uuid: str,
    tenant: str,
    env: str = TEST_ENV,
    created_rel_ids: list[int] | None = None,
) -> int:
    """Create a test bot-device relationship directly in the database."""
    rel_id = rel_repository.insert_rel(
        bot_id=bot_id,
        device_uuid=device_uuid,
        tenant=tenant,
        env=env,
        domain="test_domain",
        creator="test_user",
        modifier="test_user",
    )
    if created_rel_ids is not None:
        created_rel_ids.append(rel_id)
    return rel_id


# === BotConfig / PublishConfig Fixtures ===


def create_test_bot_deploy_config() -> Any:
    """Create a test DeployConfig dict for integration tests.

    Returns a DeployConfig with lifecycle hook configuration for testing.
    """
    from secbaas.community.api.device_manage import DeployConfig

    return DeployConfig(
        after_create_cmd_hook="/scripts/test_after_create.sh",
        after_create_hook_wait_seconds=60,
        before_destroy_cmd_hook="/scripts/test_before_destroy.sh",
        before_destroy_hook_wait_seconds=30,
    )


def create_test_bot_config(
    deploy_config: Any = None,
) -> Any:
    """Create a test BotConfig for integration tests.

    Args:
        deploy_config: Optional DeployConfig. If None, creates a default one.

    Returns a BotConfig instance suitable for testing.
    """
    from secbaas.community.api.bot_manage import BotConfig

    if deploy_config is None:
        deploy_config = create_test_bot_deploy_config()

    return BotConfig(
        share_policy={"public": False, "allow_share": True},
        deploy_config=deploy_config,
        entity_id="staff_12345",
        entity_type="staff",
    )


def create_test_publish_config(
    deploy_config: Any = None,
) -> Any:
    """Create a test PublishConfig for integration tests.

    Args:
        deploy_config: Optional DeployConfig. If None, creates a default one.

    Returns a PublishConfig instance suitable for testing.
    """
    from secbaas.community.api.publish_manage import PublishConfig

    if deploy_config is None:
        deploy_config = create_test_bot_deploy_config()

    return PublishConfig(
        bot_name="test_bot",
        replica_desired=2,
        batch_capacity=5,
        cooldown_seconds=0,
        deploy_config=deploy_config,
    )
