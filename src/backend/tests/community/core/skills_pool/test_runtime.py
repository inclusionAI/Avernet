"""Skills Pool 当前运行时 transport contract。"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from agentclaw.community.core.skills_pool.models import (
    PoolCutoverStatus,
    PoolSkillMapping,
    SkillMappingSourceLayout,
)
from agentclaw.community.core.skills_pool.quarantine import RuntimeQuarantineCleanupStatus
from agentclaw.community.core.skills_pool.runtime import OpenClawSkillsPoolRuntime
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
                {"conn_info": conn_info, "method": method, "path": path, "body": body, "timeout": timeout}
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
@pytest.mark.parametrize("provider", ["local", "baas"])
async def test_repo_retirement_projection_reaches_each_device_provider(provider: str) -> None:
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


# ── P1/P2: one device resolution per projection, and no needless verify ──


class InlineVerifiedTransport(FakeTransport):
    """A runtime that verifies its own publish and reports the verdict."""

    def __init__(self, verified: bool = True) -> None:
        super().__init__()
        self.verified = verified

    async def invoke(self, conn_info, method, path, *, body, timeout):
        response = await super().invoke(
            conn_info, method, path, body=body, timeout=timeout
        )
        if path.endswith("/publish"):
            # The engine's real shape: the digest lives inside evidence,
            # alongside the per-mapping path lists that make the whole
            # response too big to log.
            response["data"] = {
                "published": True,
                "verified": self.verified,
                "evidence": {
                    "kept": ["/a/very/long/path"] * 50,
                    "verification": {
                        "ran": True,
                        "valid": False,
                        "failure_count": 3,
                    },
                },
            }
        return response


def _runtime(resolver, transport):
    return OpenClawSkillsPoolRuntime(
        resolver=resolver,
        adapter_transport=transport,
        probe_service=FakeProbe(),
    )


def _local_mappings():
    return [PoolSkillMapping(corpus="local", relative_path="a", link_name="a")]


def _paths(transport):
    return [call["path"] for call in transport.calls]


@pytest.mark.asyncio
async def test_publish_and_verify_resolves_the_device_once() -> None:
    """Two device calls, one resolution — the point of the combined entry."""
    resolver = FakeResolver()
    transport = FakeTransport()

    outcome = await _runtime(resolver, transport).publish_and_verify_mappings(
        bot_id="bot-1", user_id="user-1", mappings=_local_mappings()
    )

    assert outcome.verified is True
    assert len(resolver.calls) == 1
    assert sum(path.endswith(("/publish", "/verify")) for path in _paths(transport)) == 2


@pytest.mark.asyncio
async def test_center_ensure_shares_the_resolved_context() -> None:
    """Three device calls for a Center projection still resolve once."""
    resolver = FakeResolver()
    transport = CenterEnsureTransport()
    mappings = [
        PoolSkillMapping(
            corpus="center",
            relative_path=None,
            link_name="a",
            skill_uuid="uuid-a",
            sc_version_number="1",
        )
    ]

    outcome = await _runtime(resolver, transport).publish_and_verify_mappings(
        bot_id="bot-1",
        user_id="user-1",
        mappings=mappings,
        mapping_contract_version=MAPPING_V3_CONTRACT_VERSION,
    )

    assert outcome.verified is True
    assert len(resolver.calls) == 1
    assert len(transport.calls) == 3


@pytest.mark.asyncio
async def test_inline_verification_skips_the_separate_verify_call() -> None:
    resolver = FakeResolver()
    transport = InlineVerifiedTransport(verified=True)

    outcome = await _runtime(resolver, transport).publish_and_verify_mappings(
        bot_id="bot-1", user_id="user-1", mappings=_local_mappings()
    )

    assert (outcome.published, outcome.verified, outcome.reported_inline) == (
        True,
        True,
        True,
    )
    assert not any(path.endswith("/verify") for path in _paths(transport))


@pytest.mark.asyncio
async def test_inline_verification_failure_is_not_retried_by_the_verify_call() -> None:
    """``verified: false`` is the runtime's answer, not a reason to ask again."""
    resolver = FakeResolver()
    transport = InlineVerifiedTransport(verified=False)

    outcome = await _runtime(resolver, transport).publish_and_verify_mappings(
        bot_id="bot-1", user_id="user-1", mappings=_local_mappings()
    )

    assert outcome.published is True
    assert outcome.verified is False
    assert outcome.reported_inline is True
    assert not any(path.endswith("/verify") for path in _paths(transport))


@pytest.mark.asyncio
async def test_inline_verification_failure_logs_the_diagnosis(caplog) -> None:
    """The failed verdict is final, so this warning is the only diagnostic.

    It logs the bounded verification digest, not the whole response: the
    publish evidence carries a path list per mapping, which on a large Bot
    would bury the failure detail and repeat on every retry.
    """
    resolver = FakeResolver()
    transport = InlineVerifiedTransport(verified=False)

    with caplog.at_level(logging.WARNING):
        await _runtime(resolver, transport).publish_and_verify_mappings(
            bot_id="bot-1", user_id="user-1", mappings=_local_mappings()
        )

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings, "a failed inline verdict must warn"
    rendered = warnings[-1].getMessage()
    assert "inline as failed" in rendered
    assert "failure_count" in rendered
    # The bulky half stays out.
    assert "'kept'" not in rendered


@pytest.mark.asyncio
async def test_publish_mappings_alone_never_claims_the_verdict_is_final(
    caplog,
) -> None:
    """A separate verify still follows this path, so the verdict is not final.

    The warning belongs to publish_and_verify_mappings, which does not re-ask
    the device. Emitting it from the shared publish body would alert on every
    projection whose separate verify then succeeds.
    """
    resolver = FakeResolver()
    transport = InlineVerifiedTransport(verified=False)

    with caplog.at_level(logging.WARNING):
        published = await _runtime(resolver, transport).publish_mappings(
            bot_id="bot-1", user_id="user-1", mappings=_local_mappings()
        )

    assert published is True
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


@pytest.mark.asyncio
async def test_failed_publish_warns_once_not_twice(caplog) -> None:
    """A publish that never happened did not "report" a verification verdict."""

    class FailedWithVerdict(FakeTransport):
        async def invoke(self, conn_info, method, path, *, body, timeout):
            await super().invoke(conn_info, method, path, body=body, timeout=timeout)
            return {"success": False, "data": {"verified": False}}

    resolver = FakeResolver()

    with caplog.at_level(logging.WARNING):
        outcome = await _runtime(
            resolver, FailedWithVerdict()
        ).publish_and_verify_mappings(
            bot_id="bot-1", user_id="user-1", mappings=_local_mappings()
        )

    assert outcome.published is False
    assert len([r for r in caplog.records if r.levelno >= logging.WARNING]) == 1


@pytest.mark.asyncio
async def test_absent_signal_falls_back_to_the_separate_verify_call() -> None:
    """An older runtime says nothing; absence must never read as verified."""
    resolver = FakeResolver()
    transport = FakeTransport()  # publish data carries no "verified" key

    outcome = await _runtime(resolver, transport).publish_and_verify_mappings(
        bot_id="bot-1", user_id="user-1", mappings=_local_mappings()
    )

    assert outcome.verified is True
    assert outcome.reported_inline is False
    assert any(path.endswith("/verify") for path in _paths(transport))


@pytest.mark.asyncio
async def test_failed_publish_never_reaches_verify() -> None:
    class FailingPublish(FakeTransport):
        async def invoke(self, conn_info, method, path, *, body, timeout):
            response = await super().invoke(
                conn_info, method, path, body=body, timeout=timeout
            )
            if path.endswith("/publish"):
                return {"success": False, "data": {}}
            return response

    resolver = FakeResolver()
    transport = FailingPublish()

    outcome = await _runtime(resolver, transport).publish_and_verify_mappings(
        bot_id="bot-1", user_id="user-1", mappings=_local_mappings()
    )

    assert outcome.published is False
    assert outcome.verified is False
    assert not any(path.endswith("/verify") for path in _paths(transport))


@pytest.mark.asyncio
async def test_unresolvable_device_reports_an_unpublished_outcome() -> None:
    class BrokenResolver:
        def resolve_for_bot(self, bot_id, user_id):
            raise RuntimeError("no active binding")

    transport = FakeTransport()

    outcome = await _runtime(BrokenResolver(), transport).publish_and_verify_mappings(
        bot_id="bot-1", user_id="user-1", mappings=_local_mappings()
    )

    assert outcome.published is False
    assert outcome.verified is False
    assert transport.calls == []


@pytest.mark.asyncio
async def test_publish_and_verify_send_the_same_mapping_set() -> None:
    """Verify must check what publish wrote, retirements and layout included.

    Without this, dropping ``retired_mappings`` or ``source_layout`` from the
    fallback verify call still passes every other test in this file, while
    production verifies a different set than it published — a retirement that
    failed to apply would report verified.
    """
    resolver = FakeResolver()
    transport = FakeTransport()
    mappings = _local_mappings()
    retired = [PoolSkillMapping(corpus="local", relative_path="b", link_name="b")]

    await _runtime(resolver, transport).publish_and_verify_mappings(
        bot_id="bot-1",
        user_id="user-1",
        mappings=mappings,
        retired_mappings=retired,
        source_layout=SkillMappingSourceLayout.LEGACY,
    )

    bodies = {
        call["path"].rsplit("/", 1)[-1]: call["body"]
        for call in transport.calls
        if call["path"].endswith(("/publish", "/verify"))
    }
    assert set(bodies) == {"publish", "verify"}
    assert bodies["publish"] == bodies["verify"]
    assert bodies["verify"]["retired_mappings"] == [retired[0].to_dict()]
    assert bodies["verify"]["source_layout"] == SkillMappingSourceLayout.LEGACY.value
