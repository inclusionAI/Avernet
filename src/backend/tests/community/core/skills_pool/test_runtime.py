"""Skills Pool 当前运行时 transport contract。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agentclaw.community.core.skills_pool.models import (
    PoolCutoverStatus,
    PoolSkillMapping,
)
from agentclaw.community.core.skills_pool.quarantine import (
    RuntimeQuarantineCleanupStatus,
)
from agentclaw.community.plugins.skills_pool_runtime import OpenClawSkillsPoolRuntime


class FakeResolver:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def resolve_for_bot(self, bot_id: str, user_id: str):
        self.calls.append((bot_id, user_id))
        return SimpleNamespace(conn_info={"binding": len(self.calls)})


class FakeTransport:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def invoke(
        self,
        conn_info,
        method,
        path,
        *,
        body,
        timeout,
    ):
        self.calls.append(
            {
                "conn_info": conn_info,
                "method": method,
                "path": path,
                "body": body,
                "timeout": timeout,
            }
        )
        if path.endswith(("/activate", "/rollback")):
            return {
                "success": True,
                "data": {
                    "committed": True,
                    "status": "COMMITTED",
                    "evidence": {},
                },
            }
        if path.endswith("/publish"):
            return {"success": True, "data": {"published": True}}
        return {"success": True, "data": {"valid": True}}


class FakeProbe:
    async def probe_bot(self, **kwargs):
        return kwargs


class FutureStatusTransport(FakeTransport):
    async def invoke(self, conn_info, method, path, *, body, timeout):
        response = await super().invoke(
            conn_info,
            method,
            path,
            body=body,
            timeout=timeout,
        )
        if path.endswith("/activate"):
            response["data"] = {
                "committed": True,
                "status": "FUTURE_STATUS",
                "evidence": {"source": "newer-engine"},
            }
        return response


class QuarantineTransport(FakeTransport):
    def __init__(self, status: str) -> None:
        super().__init__()
        self.status = status

    async def invoke(self, conn_info, method, path, *, body, timeout):
        await super().invoke(
            conn_info,
            method,
            path,
            body=body,
            timeout=timeout,
        )
        return {
            "success": True,
            "data": {
                "status": self.status,
                "evidence": {"generation_scoped": True},
            },
        }


@pytest.mark.asyncio
async def test_pool_runtime_resolves_current_binding_for_each_mutation() -> None:
    resolver = FakeResolver()
    transport = FakeTransport()
    runtime = OpenClawSkillsPoolRuntime(
        resolver=resolver,
        adapter_transport=transport,
        probe_service=FakeProbe(),
    )
    mappings = [
        PoolSkillMapping(
            corpus="local",
            relative_path="a",
            link_name="a",
        )
    ]

    cutover = await runtime.cutover(
        bot_id="bot-1",
        user_id="owner-1",
        migration_generation="generation-1",
        preparation_id="preparation-1",
        registered_local_names=["a"],
        mappings=mappings,
    )
    rollback = await runtime.rollback_to_legacy(
        bot_id="bot-1",
        user_id="owner-1",
        rollback_generation="rollback-1",
        registered_local_names=["a"],
    )
    published = await runtime.publish_mappings(
        bot_id="bot-1",
        user_id="owner-1",
        mappings=mappings,
    )
    verified = await runtime.verify_mappings(
        bot_id="bot-1",
        user_id="owner-1",
        mappings=mappings,
    )

    assert cutover.committed
    assert cutover.status is PoolCutoverStatus.COMMITTED
    assert rollback.committed
    assert rollback.status is PoolCutoverStatus.COMMITTED
    assert published
    assert verified
    assert resolver.calls == [
        ("bot-1", "owner-1"),
        ("bot-1", "owner-1"),
        ("bot-1", "owner-1"),
        ("bot-1", "owner-1"),
    ]
    assert [call["path"] for call in transport.calls] == [
        "/api/skills/layout/activate",
        "/api/skills/layout/rollback",
        "/api/skills/layout/mappings/publish",
        "/api/skills/layout/mappings/verify",
    ]
    logical_mapping = {
        "corpus": "local",
        "relative_path": "a",
        "link_name": "a",
    }
    for index in (0, 2, 3):
        assert (
            transport.calls[index]["body"]["mapping_contract_version"]
            == "skills-pool-mapping-v2"
        )
        assert transport.calls[index]["body"]["mappings"] == [logical_mapping]


@pytest.mark.asyncio
async def test_pool_runtime_fails_closed_for_unknown_engine_status() -> None:
    runtime = OpenClawSkillsPoolRuntime(
        resolver=FakeResolver(),
        adapter_transport=FutureStatusTransport(),
        probe_service=FakeProbe(),
    )

    result = await runtime.cutover(
        bot_id="bot-1",
        user_id="owner-1",
        migration_generation="generation-1",
        preparation_id="preparation-1",
        registered_local_names=[],
        mappings=[],
    )

    assert result.status is PoolCutoverStatus.UNKNOWN
    assert not result.committed
    assert result.evidence == {
        "source": "newer-engine",
        "raw_status": "FUTURE_STATUS",
    }


@pytest.mark.asyncio
async def test_pool_runtime_returns_typed_quarantine_cleanup_result() -> None:
    runtime = OpenClawSkillsPoolRuntime(
        resolver=FakeResolver(),
        adapter_transport=QuarantineTransport("CLEANED"),
        probe_service=FakeProbe(),
    )

    result = await runtime.cleanup_quarantine(
        bot_id="bot-1",
        user_id="owner-1",
        engine="openclaw",
        migration_generation="generation-1",
    )

    assert result.status is RuntimeQuarantineCleanupStatus.CLEANED
    assert result.evidence == {"generation_scoped": True}


@pytest.mark.asyncio
async def test_pool_runtime_fails_closed_for_unknown_cleanup_status() -> None:
    runtime = OpenClawSkillsPoolRuntime(
        resolver=FakeResolver(),
        adapter_transport=QuarantineTransport("FUTURE_STATUS"),
        probe_service=FakeProbe(),
    )

    result = await runtime.cleanup_quarantine(
        bot_id="bot-1",
        user_id="owner-1",
        engine="openclaw",
        migration_generation="generation-1",
    )

    assert result.status is RuntimeQuarantineCleanupStatus.INVALID
    assert result.evidence == {
        "generation_scoped": True,
        "reason": "invalid_runtime_response",
        "raw_status": "FUTURE_STATUS",
    }
