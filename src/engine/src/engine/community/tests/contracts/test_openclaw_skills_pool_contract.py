"""OpenClaw Skills Pool Service API ↔ Plugin API 契约。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from engine.community.core.adapters.openclaw.skills import OpenClawSkillsAdapter
from engine.community.core.skills.exceptions import (
    InvalidPoolMappingRequestError,
)
from engine.community.core.skills.models import (
    PoolLayoutActivateRequest,
    PoolLayoutActivationStatus,
    PoolLayoutRollbackRequest,
    PoolQuarantineCleanupRequest,
    PoolSkillMappingIntent,
    SymlinkItem,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw_status",
    [
        "COMMITTED",
        "ALREADY_COMMITTED",
        "ACTIVE_ENTRY_CONFLICT",
        "DATA_INCONSISTENT",
        "INVALID",
        "TRANSIENT_ERROR",
        "POST_CUTOVER_SYNC_PENDING",
        "NOT_ATOMIC",
    ],
)
async def test_activation_status_contract_accepts_every_known_value(
    raw_status: str,
) -> None:
    port = MagicMock()
    port.activate_pool_layout = AsyncMock(
        return_value={
            "committed": raw_status in {"COMMITTED", "ALREADY_COMMITTED"},
            "status": raw_status,
            "evidence": {"source": "plugin"},
        }
    )

    result = await OpenClawSkillsAdapter(port).activate_pool_layout(
        PoolLayoutActivateRequest(
            migration_generation="generation-1",
            preparation_id="preparation-1",
        )
    )

    assert result.status is PoolLayoutActivationStatus(raw_status)
    assert result.to_data()["status"] == raw_status
    port.activate_pool_layout.assert_awaited_once()


@pytest.mark.asyncio
async def test_unknown_activation_status_fails_closed_and_preserves_evidence() -> None:
    port = MagicMock()
    port.activate_pool_layout = AsyncMock(
        return_value={
            "committed": True,
            "status": "FUTURE_STATUS",
            "evidence": {"source": "newer-plugin"},
        }
    )

    result = await OpenClawSkillsAdapter(port).activate_pool_layout(
        PoolLayoutActivateRequest(
            migration_generation="generation-1",
            preparation_id="preparation-1",
        )
    )

    assert result.status is PoolLayoutActivationStatus.UNKNOWN
    assert not result.committed
    assert result.evidence == {
        "source": "newer-plugin",
        "raw_status": "FUTURE_STATUS",
    }
    port.activate_pool_layout.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw_status", "committed"),
    [
        ("COMMITTED", True),
        ("ALREADY_COMMITTED", True),
        ("TRANSIENT_ERROR", False),
        ("UNKNOWN", False),
    ],
)
async def test_rollback_status_contract_and_port_call(
    raw_status: str,
    committed: bool,
) -> None:
    port = MagicMock()
    port.rollback_pool_layout = AsyncMock(
        return_value={
            "committed": committed,
            "status": raw_status,
            "evidence": {"source": "current_pool"},
        }
    )
    request = PoolLayoutRollbackRequest(
        rollback_generation="rollback-1",
        registered_local_names=["handmade"],
    )

    result = await OpenClawSkillsAdapter(port).rollback_pool_layout(request)

    assert result.status is PoolLayoutActivationStatus(raw_status)
    assert result.committed is committed
    port.rollback_pool_layout.assert_awaited_once_with(
        {
            "rollback_generation": "rollback-1",
            "registered_local_names": ["handmade"],
        }
    )


@pytest.mark.asyncio
async def test_unknown_rollback_status_fails_closed_and_preserves_raw_value() -> None:
    port = MagicMock()
    port.rollback_pool_layout = AsyncMock(
        return_value={
            "committed": True,
            "status": "FUTURE_STATUS",
            "evidence": {"source": "newer-plugin"},
        }
    )

    result = await OpenClawSkillsAdapter(port).rollback_pool_layout(
        PoolLayoutRollbackRequest(rollback_generation="rollback-1")
    )

    assert result.status is PoolLayoutActivationStatus.UNKNOWN
    assert result.committed is False
    assert result.evidence == {
        "source": "newer-plugin",
        "raw_status": "FUTURE_STATUS",
    }


@pytest.mark.asyncio
async def test_quarantine_cleanup_contract_forwards_exact_generation() -> None:
    port = MagicMock()
    port.cleanup_pool_quarantine = AsyncMock(
        return_value={
            "status": "CLEANED",
            "evidence": {"path_absent": True},
        }
    )

    result = await OpenClawSkillsAdapter(port).cleanup_pool_quarantine(
        PoolQuarantineCleanupRequest(migration_generation="generation-1")
    )

    assert result.status == "CLEANED"
    assert result.evidence == {"path_absent": True}
    port.cleanup_pool_quarantine.assert_awaited_once_with(
        {"migration_generation": "generation-1"}
    )


@pytest.mark.asyncio
async def test_openclaw_adapter_propagates_logical_mapping_version() -> None:
    port = MagicMock()
    port.activate_pool_layout = AsyncMock(
        return_value={
            "committed": True,
            "status": "COMMITTED",
            "evidence": {},
        }
    )
    port.publish_pool_mappings = AsyncMock(
        return_value={"published": True, "evidence": {}}
    )
    port.verify_pool_mappings = AsyncMock(return_value={"valid": True, "evidence": {}})
    adapter = OpenClawSkillsAdapter(port)
    mapping = PoolSkillMappingIntent(
        corpus="repo",
        relative_path="business/reviewer",
        link_name="reviewer",
    )
    retired_mapping = PoolSkillMappingIntent(
        corpus="repo",
        relative_path="legacy/writer",
        link_name="writer",
    )
    version = "skills-pool-mapping-v2"

    await adapter.activate_pool_layout(
        PoolLayoutActivateRequest(
            migration_generation="generation-1",
            preparation_id="preparation-1",
            mappings=[mapping],
            mapping_contract_version=version,
        )
    )
    await adapter.publish_pool_mappings(
        [mapping],
        retired_mappings=[retired_mapping],
        mapping_contract_version=version,
    )
    await adapter.verify_pool_mappings(
        [mapping],
        retired_mappings=[retired_mapping],
        mapping_contract_version=version,
    )

    expected_mapping = {
        "corpus": "repo",
        "relative_path": "business/reviewer",
        "link_name": "reviewer",
    }
    expected_retired_mapping = {
        "corpus": "repo",
        "relative_path": "legacy/writer",
        "link_name": "writer",
    }
    assert port.activate_pool_layout.await_args.args[0] == {
        "migration_generation": "generation-1",
        "preparation_id": "preparation-1",
        "registered_local_names": [],
        "mapping_contract_version": version,
        "mappings": [expected_mapping],
    }
    assert port.publish_pool_mappings.await_args.args[0] == {
        "mapping_contract_version": version,
        "mappings": [expected_mapping],
        "retired_mappings": [expected_retired_mapping],
        "source_layout": "pool",
    }
    assert port.verify_pool_mappings.await_args.args[0] == {
        "mapping_contract_version": version,
        "mappings": [expected_mapping],
        "retired_mappings": [expected_retired_mapping],
        "source_layout": "pool",
    }


@pytest.mark.asyncio
async def test_openclaw_adapter_keeps_unversioned_physical_mapping() -> None:
    port = MagicMock()
    port.publish_pool_mappings = AsyncMock(
        return_value={"published": True, "evidence": {}}
    )
    port.verify_pool_mappings = AsyncMock(return_value={"valid": True, "evidence": {}})
    adapter = OpenClawSkillsAdapter(port)
    mapping = SymlinkItem(source="/pool/writer", target="/active/writer")

    await adapter.publish_pool_mappings([mapping])
    await adapter.verify_pool_mappings([mapping])

    expected = {
        "mappings": [{"source": "/pool/writer", "target": "/active/writer"}],
        "source_layout": "pool",
    }
    port.publish_pool_mappings.assert_awaited_once_with(expected)
    port.verify_pool_mappings.assert_awaited_once_with(expected)


@pytest.mark.asyncio
async def test_openclaw_adapter_preserves_invalid_mapping_request_contract() -> None:
    error = InvalidPoolMappingRequestError("invalid mapping payload")
    port = MagicMock()
    port.activate_pool_layout = AsyncMock(side_effect=error)
    port.publish_pool_mappings = AsyncMock(side_effect=error)
    port.verify_pool_mappings = AsyncMock(side_effect=error)
    adapter = OpenClawSkillsAdapter(port)
    request = PoolLayoutActivateRequest(
        migration_generation="generation-1",
        preparation_id="preparation-1",
    )

    with pytest.raises(InvalidPoolMappingRequestError):
        await adapter.activate_pool_layout(request)
    with pytest.raises(InvalidPoolMappingRequestError):
        await adapter.publish_pool_mappings([])
    with pytest.raises(InvalidPoolMappingRequestError):
        await adapter.verify_pool_mappings([])
