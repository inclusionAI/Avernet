"""OpenClaw Skills Pool Service API ↔ Plugin API 契约。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from engine.community.core.adapters.openclaw.skills import OpenClawSkillsAdapter
from engine.community.core.skills.models import (
    PoolLayoutActivateRequest,
    PoolLayoutActivationStatus,
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
