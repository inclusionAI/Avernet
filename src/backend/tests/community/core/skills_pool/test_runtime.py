"""Skills Pool 当前运行时 transport contract。"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from agentclaw.community.core.skills_pool.models import (
    MappingProjectionStatus,
    PoolCutoverStatus,
    PoolSkillMapping,
)
from agentclaw.community.core.skills_pool.quarantine import (
    RuntimeQuarantineCleanupStatus,
)
from agentclaw.community.core.skills_pool.ports import LegacyMappingApplyRequired
from agentclaw.community.core.skills_pool.runtime import OpenClawSkillsPoolRuntime
from agentclaw.community.plugin_api.device_adapter_transport import (
    DeviceAdapterEndpointNotFoundError,
    DeviceAdapterHTTPStatusError,
    DeviceAdapterTimeoutError,
)
from agentclaw.community.core.skill_center.services.runtime_layout_probe import (
    MAPPING_V3_CONTRACT_VERSION,
)


class FakeResolver:
    def __init__(self, provider: str = "local") -> None:
        self.calls: list[tuple[str, str]] = []
        self.provider = provider

    def resolve_for_bot(self, bot_id: str, user_id: str):
        self.calls.append((bot_id, user_id))
        return SimpleNamespace(
            conn_info={"binding": len(self.calls), "provider": self.provider}
        )


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


class CenterEnsureTransport(FakeTransport):
    async def invoke(self, conn_info, method, path, *, body, timeout):
        if path.endswith("/center/ensure"):
            self.calls.append(
                {
                    "conn_info": conn_info,
                    "method": method,
                    "path": path,
                    "body": body,
                    "timeout": timeout,
                }
            )
            return {"success": True, "data": {"ok": body["items"], "failed": []}}
        return await super().invoke(conn_info, method, path, body=body, timeout=timeout)


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


class ApplyTransport(FakeTransport):
    def __init__(
        self,
        failure: Exception | None = None,
        *,
        health_engine: str = "openclaw",
    ) -> None:
        super().__init__()
        self.failure = failure
        self.health_engine = health_engine

    async def invoke(
        self, conn_info, method, path, *, body=None, timeout=None
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
        if path == "/api/skills/mappings/apply":
            if self.failure is not None:
                raise self.failure
            return {
                "success": True,
                "data": {
                    "status": "CONVERGED",
                    "items": [
                        {
                            "mapping": body["mappings"][0],
                            "target": "",
                            "action": "APPLY",
                            "status": "CONVERGED",
                            "retryable": False,
                        }
                    ],
                    "issues": [],
                    "evidence": {},
                },
            }
        if path == "/health":
            return {"status": "ok", "engine": self.health_engine}
        raise AssertionError(path)


@pytest.mark.asyncio
async def test_daily_apply_uses_one_transport_call_and_parses_logical_item() -> None:
    transport = ApplyTransport()
    runtime = OpenClawSkillsPoolRuntime(
        resolver=FakeResolver(),
        adapter_transport=transport,
        probe_service=FakeProbe(),
    )
    mapping = PoolSkillMapping("local", "package", "display-name")

    result = await runtime.apply_mappings(
        bot_id="bot-1",
        user_id="owner-1",
        engine="openclaw",
        mappings=[mapping],
    )

    assert result.status is MappingProjectionStatus.CONVERGED
    assert result.items[0].mapping == mapping
    assert [call["path"] for call in transport.calls] == [
        "/api/skills/mappings/apply"
    ]


@pytest.mark.asyncio
async def test_standard_route_miss_requires_matching_health_before_fallback() -> None:
    transport = ApplyTransport(
        DeviceAdapterEndpointNotFoundError(
            '{"detail":"Not Found"}', standard_route_missing=True
        )
    )
    runtime = OpenClawSkillsPoolRuntime(
        resolver=FakeResolver(),
        adapter_transport=transport,
        probe_service=FakeProbe(),
    )

    with pytest.raises(LegacyMappingApplyRequired):
        await runtime.apply_mappings(
            bot_id="bot-1",
            user_id="owner-1",
            engine="openclaw",
            mappings=[],
        )

    assert [call["path"] for call in transport.calls] == [
        "/api/skills/mappings/apply",
        "/health",
    ]
    assert transport.calls[0]["conn_info"] is transport.calls[1]["conn_info"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        DeviceAdapterEndpointNotFoundError("application 404"),
        DeviceAdapterTimeoutError("timeout"),
    ],
)
async def test_unknown_apply_failure_never_switches_write_protocol(
    failure: Exception,
) -> None:
    transport = ApplyTransport(failure)
    runtime = OpenClawSkillsPoolRuntime(
        resolver=FakeResolver(),
        adapter_transport=transport,
        probe_service=FakeProbe(),
    )

    result = await runtime.apply_mappings(
        bot_id="bot-1",
        user_id="owner-1",
        engine="openclaw",
        mappings=[],
    )

    assert result.status is MappingProjectionStatus.PENDING
    assert [call["path"] for call in transport.calls] == [
        "/api/skills/mappings/apply"
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [400, 401, 403, 500])
async def test_http_failure_never_switches_write_protocol(status_code: int) -> None:
    transport = ApplyTransport(
        DeviceAdapterHTTPStatusError(status_code, "rejected")
    )
    runtime = OpenClawSkillsPoolRuntime(
        resolver=FakeResolver(),
        adapter_transport=transport,
        probe_service=FakeProbe(),
    )

    result = await runtime.apply_mappings(
        bot_id="bot-1",
        user_id="owner-1",
        engine="openclaw",
        mappings=[],
    )

    assert result.status is MappingProjectionStatus.PENDING
    assert [call["path"] for call in transport.calls] == [
        "/api/skills/mappings/apply"
    ]


@pytest.mark.asyncio
async def test_health_mismatch_does_not_enable_legacy_write_fallback() -> None:
    transport = ApplyTransport(
        DeviceAdapterEndpointNotFoundError(
            '{"detail":"Not Found"}', standard_route_missing=True
        ),
        health_engine="hermes",
    )
    runtime = OpenClawSkillsPoolRuntime(
        resolver=FakeResolver(),
        adapter_transport=transport,
        probe_service=FakeProbe(),
    )

    result = await runtime.apply_mappings(
        bot_id="bot-1",
        user_id="owner-1",
        engine="openclaw",
        mappings=[],
    )

    assert result.status is MappingProjectionStatus.PENDING
    assert [call["path"] for call in transport.calls] == [
        "/api/skills/mappings/apply",
        "/health",
    ]


def test_malformed_item_types_are_rejected() -> None:
    runtime = OpenClawSkillsPoolRuntime(
        resolver=FakeResolver(),
        adapter_transport=ApplyTransport(),
        probe_service=FakeProbe(),
    )

    result = runtime._mapping_apply_result(
        {
            "success": True,
            "data": {
                "status": "CONVERGED",
                "items": [
                    {
                        "mapping": {
                            "corpus": "local",
                            "relative_path": "package",
                            "link_name": "runtime-name",
                        },
                        "target": "",
                        "action": "APPLY",
                        "status": "CONVERGED",
                        "retryable": "false",
                    }
                ],
                "issues": [],
            },
        }
    )

    assert result.status is MappingProjectionStatus.PENDING
    assert result.evidence["reason"] == "invalid_runtime_items"


@pytest.mark.asyncio
async def test_contradictory_apply_envelope_cannot_report_converged() -> None:
    transport = ApplyTransport()

    async def contradictory(conn_info, method, path, *, body=None, timeout=None):
        transport.calls.append({"path": path})
        return {
            "success": False,
            "data": {
                "status": "CONVERGED",
                "items": [],
                "issues": [],
                "evidence": {},
            },
        }

    transport.invoke = contradictory
    runtime = OpenClawSkillsPoolRuntime(
        resolver=FakeResolver(),
        adapter_transport=transport,
        probe_service=FakeProbe(),
    )

    result = await runtime.apply_mappings(
        bot_id="bot-1",
        user_id="owner-1",
        engine="openclaw",
        mappings=[],
    )

    assert result.status is MappingProjectionStatus.PENDING
    assert result.evidence["reason"] == "contradictory_runtime_response"


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
        retired_mappings=mappings,
    )
    verified = await runtime.verify_mappings(
        bot_id="bot-1",
        user_id="owner-1",
        mappings=mappings,
        retired_mappings=mappings,
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
    for index in (2, 3):
        assert transport.calls[index]["body"]["retired_mappings"] == [logical_mapping]


@pytest.mark.asyncio
async def test_pool_runtime_logs_device_context_resolution_timing(caplog) -> None:
    runtime = OpenClawSkillsPoolRuntime(
        resolver=FakeResolver(),
        adapter_transport=FakeTransport(),
        probe_service=FakeProbe(),
    )
    caplog.set_level(logging.INFO)

    assert await runtime.verify_mappings(
        bot_id="bot-1",
        user_id="owner-1",
        mappings=[],
    )

    assert any(
        "[skills_pool.runtime] timing stage=resolve_device_context"
        in record.getMessage()
        and "bot_id=bot-1" in record.getMessage()
        and "path=/api/skills/layout/mappings/verify" in record.getMessage()
        and "duration_ms=" in record.getMessage()
        for record in caplog.records
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["local", "baas"])
async def test_repo_retirement_projection_reaches_each_device_provider(
    provider: str,
) -> None:
    """Repo Direct deactivate clears the old entry for Local and BaaS engines."""
    transport = FakeTransport()
    runtime = OpenClawSkillsPoolRuntime(
        resolver=FakeResolver(provider),
        adapter_transport=transport,
        probe_service=FakeProbe(),
    )
    retired = PoolSkillMapping(
        corpus="repo", relative_path="tools/repo", link_name="repo"
    )

    assert await runtime.publish_mappings(
        bot_id="bot-1",
        user_id="owner-1",
        mappings=[],
        retired_mappings=[retired],
    )
    assert await runtime.verify_mappings(
        bot_id="bot-1",
        user_id="owner-1",
        mappings=[],
        retired_mappings=[retired],
    )

    for call in transport.calls:
        assert call["conn_info"]["provider"] == provider
        assert call["body"]["mappings"] == []
        assert call["body"]["retired_mappings"] == [
            {"corpus": "repo", "relative_path": "tools/repo", "link_name": "repo"}
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
async def test_center_mapping_is_ensured_before_full_v3_publish() -> None:
    transport = CenterEnsureTransport()
    runtime = OpenClawSkillsPoolRuntime(
        resolver=FakeResolver(),
        adapter_transport=transport,
        probe_service=FakeProbe(),
    )
    mapping = PoolSkillMapping(
        corpus="center",
        relative_path=None,
        link_name="risk-review",
        skill_uuid="2e0f2a89-5f8e-4df2-bc3e-797f5f02d26a",
        sc_version_number="2026.8.19",
    )

    assert await runtime.publish_mappings(
        bot_id="bot-1",
        user_id="owner-1",
        mappings=[mapping],
        mapping_contract_version=MAPPING_V3_CONTRACT_VERSION,
    )
    assert [call["path"] for call in transport.calls] == [
        "/api/skills/center/ensure",
        "/api/skills/layout/mappings/publish",
    ]


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
