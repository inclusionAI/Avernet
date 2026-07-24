"""OpenClaw Skills Pool Service API ↔ Plugin API 契约。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from engine.community.core.adapters.openclaw.skills import OpenClawSkillsAdapter
from engine.community.core.skills.models import (
    PoolLayoutActivateRequest,
    PoolLayoutActivationStatus,
    PoolLayoutRollbackRequest,
    PoolQuarantineCleanupRequest,
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
