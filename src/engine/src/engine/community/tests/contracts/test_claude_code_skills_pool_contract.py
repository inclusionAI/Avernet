from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from engine.community.core.adapters.claude_code.skills import (
    ClaudeCodeSkillsAdapter,
)
from engine.community.core.skills.models import (
    PoolLayoutActivateRequest,
    PoolLayoutActivationStatus,
    PoolLayoutProbeRequest,
    PoolLayoutProbeStatus,
    PoolLayoutRollbackRequest,
    PoolQuarantineCleanupRequest,
    PoolSkillMappingIntent,
    SymlinkItem,
)


def _port() -> SimpleNamespace:
    return SimpleNamespace(
        activate_pool_layout=AsyncMock(
            return_value={
                "committed": True,
                "status": "COMMITTED",
                "evidence": {"bridge": "claude-local"},
            }
        ),
        rollback_pool_layout=AsyncMock(
            return_value={
                "committed": True,
                "status": "COMMITTED",
                "evidence": {"source": "current_pool"},
            }
        ),
        cleanup_pool_quarantine=AsyncMock(
            return_value={
                "status": "CLEANED",
                "evidence": {"path_absent": True},
            }
        ),
        probe_pool_layout=AsyncMock(
            return_value={
                "status": "READY",
                "engine": "claude_code",
                "layout_contract_version": "skills-pool-p3-v1",
                "preparation_id": "prep-1",
                "evidence": {"stable_bridges": 2},
            }
        ),
        publish_pool_mappings=AsyncMock(
            return_value={"published": True, "evidence": {"total": 1}}
        ),
        verify_pool_mappings=AsyncMock(
            return_value={"valid": True, "evidence": {"checked": 1}}
        ),
    )


@pytest.mark.asyncio
async def test_claude_code_adapter_exposes_complete_pool_runtime_contract() -> None:
    port = _port()
    adapter = ClaudeCodeSkillsAdapter(port)
    mapping = SymlinkItem(
        source=("/home/admin/.claude_code/workspace/skills-pool/skills-local/handmade"),
        target="/home/admin/.claude/skills/handmade",
    )

    probe = await adapter.probe_pool_layout(
        PoolLayoutProbeRequest(
            engine="claude_code",
            layout_contract_version="skills-pool-p3-v1",
        )
    )
    activated = await adapter.activate_pool_layout(
        PoolLayoutActivateRequest(
            migration_generation="generation-1",
            preparation_id="prep-1",
            registered_local_names=["handmade"],
            mappings=[mapping],
        )
    )
    rolled_back = await adapter.rollback_pool_layout(
        PoolLayoutRollbackRequest(
            rollback_generation="rollback-1",
            registered_local_names=["handmade"],
        )
    )
    cleaned = await adapter.cleanup_pool_quarantine(
        PoolQuarantineCleanupRequest(migration_generation="generation-1")
    )
    published = await adapter.publish_pool_mappings([mapping])
    verified = await adapter.verify_pool_mappings([mapping])

    assert probe.status is PoolLayoutProbeStatus.READY
    assert probe.engine == "claude_code"
    assert activated.status is PoolLayoutActivationStatus.COMMITTED
    assert activated.committed is True
    assert rolled_back.status is PoolLayoutActivationStatus.COMMITTED
    assert rolled_back.committed is True
    assert cleaned.status == "CLEANED"
    assert cleaned.evidence == {"path_absent": True}
    assert published.published is True
    assert verified.valid is True
    port.probe_pool_layout.assert_awaited_once_with(
        {
            "engine": "claude_code",
            "layout_contract_version": "skills-pool-p3-v1",
        }
    )
    port.activate_pool_layout.assert_awaited_once_with(
        {
            "migration_generation": "generation-1",
            "preparation_id": "prep-1",
            "registered_local_names": ["handmade"],
            "mappings": [
                {
                    "source": mapping.source,
                    "target": mapping.target,
                }
            ],
        }
    )
    port.rollback_pool_layout.assert_awaited_once_with(
        {
            "rollback_generation": "rollback-1",
            "registered_local_names": ["handmade"],
        }
    )
    port.cleanup_pool_quarantine.assert_awaited_once_with(
        {"migration_generation": "generation-1"}
    )
    port.publish_pool_mappings.assert_awaited_once_with(
        {
            "mappings": [
                {
                    "source": mapping.source,
                    "target": mapping.target,
                }
            ],
            "source_layout": "pool",
        }
    )
    port.verify_pool_mappings.assert_awaited_once_with(
        {
            "mappings": [
                {
                    "source": mapping.source,
                    "target": mapping.target,
                }
            ],
            "source_layout": "pool",
        }
    )


@pytest.mark.asyncio
async def test_claude_code_adapter_propagates_logical_mapping_version() -> None:
    port = _port()
    adapter = ClaudeCodeSkillsAdapter(port)
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
            preparation_id="prep-1",
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
    activation = port.activate_pool_layout.await_args.args[0]
    assert activation["mapping_contract_version"] == version
    assert activation["mappings"] == [expected_mapping]
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
async def test_claude_code_adapter_unknown_activation_fails_closed() -> None:
    port = _port()
    port.activate_pool_layout.return_value = {
        "committed": True,
        "status": "FUTURE_STATUS",
        "evidence": {"source": "newer-plugin"},
    }
    adapter = ClaudeCodeSkillsAdapter(port)

    result = await adapter.activate_pool_layout(
        PoolLayoutActivateRequest(
            migration_generation="generation-1",
            preparation_id="prep-1",
        )
    )

    assert result.status is PoolLayoutActivationStatus.UNKNOWN
    assert result.committed is False
    assert result.evidence == {
        "source": "newer-plugin",
        "raw_status": "FUTURE_STATUS",
    }


@pytest.mark.asyncio
async def test_claude_code_adapter_unknown_rollback_fails_closed() -> None:
    port = _port()
    port.rollback_pool_layout.return_value = {
        "committed": True,
        "status": "FUTURE_STATUS",
        "evidence": {"source": "newer-plugin"},
    }
    adapter = ClaudeCodeSkillsAdapter(port)

    result = await adapter.rollback_pool_layout(
        PoolLayoutRollbackRequest(rollback_generation="rollback-1")
    )

    assert result.status is PoolLayoutActivationStatus.UNKNOWN
    assert result.committed is False
    assert result.evidence == {
        "source": "newer-plugin",
        "raw_status": "FUTURE_STATUS",
    }
