"""Skills Pool 当前运行时 transport contract。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agentclaw.community.core.skills_pool.models import (
    PoolCutoverStatus,
    PoolSkillMapping,
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
        if path.endswith("/activate"):
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
            source=(
                "/home/admin/.openclaw/workspace/"
                "skills-pool/skills-local/a"
            ),
            target="/home/admin/.openclaw/workspace/skills/a",
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
    assert published
    assert verified
    assert resolver.calls == [
        ("bot-1", "owner-1"),
        ("bot-1", "owner-1"),
        ("bot-1", "owner-1"),
    ]
    assert [call["path"] for call in transport.calls] == [
        "/api/skills/layout/activate",
        "/api/skills/layout/mappings/publish",
        "/api/skills/layout/mappings/verify",
    ]


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
