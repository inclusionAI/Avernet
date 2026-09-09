"""Cross-module tracer: Canonical prepare through Backend into real Engine FS."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from agentclaw.community.core.devices.services.device_context import DeviceContext
from agentclaw.community.core.skill_center.canonical_center_store import (
    CanonicalCenterVersion,
    CanonicalCenterVersionIdentity,
)
from agentclaw.community.core.skill_center.runtime_projection_contract import (
    ResolvedSkillPlan,
    RuntimeProjectionStatus,
)
from agentclaw.community.core.skill_center.runtime_resolver import RuntimeProjectionResolver
from agentclaw.community.core.skill_center.services.center_content_distribution import (
    CanonicalCenterContentDistribution,
    CenterContentDistributionConfig,
)
from agentclaw.community.core.skill_center.services.runtime_projections.skill_runtime_delivery import (
    SkillRuntimeDelivery,
)
from agentclaw.community.core.skills_pool.models import RegisteredSkillAsset
from agentclaw.community.core.skills_pool.runtime import SkillsPoolRuntime
from agentclaw.community.plugin_api.object_storage import (
    ObjectCreateResult,
    ObjectReadResult,
    ObjectReadStatus,
)
from agentclaw.community.plugins.local.oss_storage import MockObjectStoragePlugin


class _Canonical:
    def __init__(self, version: CanonicalCenterVersion) -> None:
        self.version = version

    def read_version(self, ref):
        assert ref.identity == self.version.identity
        return self.version


class _Contexts:
    def resolve_for_bot(self, bot_id: str, user_id: str) -> DeviceContext:
        return DeviceContext(
            provider="baas",
            conn_info={"binding_id": 7},
            binding_id=7,
            bot_id=bot_id,
            user_id=user_id,
            bot_type="desktop",
        )


class _Layouts:
    def get(self, _scope):
        return None


class _UnusedFactory:
    def create(self, **_kwargs):
        raise AssertionError("Center mapping must use logical apply")


@pytest.mark.asyncio
async def test_prepare_delivery_apply_publishes_exact_cache_and_link(
    tmp_path: Path, monkeypatch
) -> None:
    engine_src = Path(__file__).resolve().parents[5] / "engine" / "src"
    monkeypatch.syspath_prepend(str(engine_src))
    from engine.community.plugins.skills_pool.center_content import (
        DownloadedCenterContentAdapter,
    )
    from engine.community.plugins.skills_pool.layout_activation import (
        MappingSourceLayout,
    )
    from engine.community.plugins.skills_pool.mapping_contract import (
        apply_logical_mapping_payload,
    )

    identity = CanonicalCenterVersionIdentity(
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "17"
    )
    canonical = CanonicalCenterVersion.from_files(
        identity, {"SKILL.md": b"# writer\n", "assets/icon.bin": b"\x00\xff"}
    )
    stored: dict[str, bytes] = {}
    objects = MockObjectStoragePlugin()

    def create(key: str, value: bytes | str):
        content = value.encode() if isinstance(value, str) else value
        if key in stored:
            return ObjectCreateResult.ALREADY_EXISTS
        stored[key] = content
        return ObjectCreateResult.CREATED

    objects.create_object_if_absent.side_effect = create
    objects.read_object.side_effect = lambda key: (
        ObjectReadResult(ObjectReadStatus.FOUND, stored[key])
        if key in stored
        else ObjectReadResult(ObjectReadStatus.NOT_FOUND)
    )
    objects.sign_url.side_effect = lambda key, _expires: (
        f"https://objects.example.test/{key}"
    )
    distribution = CanonicalCenterContentDistribution(
        canonical_store=_Canonical(canonical),
        object_storage=objects,
        url_signer=objects,
        config=CenterContentDistributionConfig(env="pre"),
        now=lambda: datetime(2026, 9, 9, tzinfo=UTC),
    )

    class _Transport:
        async def invoke(self, _conn, method, path, *, body=None, timeout=None):
            assert (method, path) == ("POST", "/api/skills/mappings/apply")

            def fetch(url: str, **_kwargs) -> bytes:
                return stored[urlsplit(url).path.lstrip("/")]

            result = apply_logical_mapping_payload(
                engine="openclaw",
                source_layout=MappingSourceLayout(body["source_layout"]),
                mappings_payload=body["mappings"],
                retired_payload=body["retired_mappings"],
                center_content_payload=body.get("center_content"),
                content_adapter=DownloadedCenterContentAdapter(
                    fetch=fetch,
                    allowed_hosts=("objects.example.test",),
                    resolve_host=lambda _host: ("93.184.216.34",),
                ),
                home=tmp_path,
            )
            return {
                "success": result.status.value == "CONVERGED",
                "data": result.to_data(),
            }

    runtime = SkillsPoolRuntime(
        resolver=object(),
        adapter_transport=_Transport(),
        probe_service=object(),
    )
    delivery = SkillRuntimeDelivery(
        pool_runtime=runtime,
        pool_layouts=_Layouts(),
        device_contexts=_Contexts(),
        center_content=distribution,
    )
    asset = RegisteredSkillAsset(
        skill_id=9,
        name="writer",
        git_path="center://external-code",
        skill_uuid=identity.skill_uuid,
        sc_version_number=identity.sc_version_number,
    )
    plan = ResolvedSkillPlan(
        bot_id="bot-1",
        owner_id="owner-1",
        bot={
            "env": "pre",
            "entity_id": "owner-1",
            "active_engine": "openclaw",
            "bot_type": "desktop",
        },
        engine="openclaw",
        projection=RuntimeProjectionResolver().resolve_skills((asset,)),
    )

    cold = await delivery.deliver(plan=plan, service_factory=_UnusedFactory())
    assert cold.status is RuntimeProjectionStatus.PENDING
    distribution.prepare(identity)
    for _attempt in range(100):
        ready = await delivery.deliver(plan=plan, service_factory=_UnusedFactory())
        if ready.status is RuntimeProjectionStatus.CONVERGED:
            break
        await asyncio.sleep(0.01)
    else:
        raise AssertionError("Engine did not consume the prepared exact package")

    assert ready.status is RuntimeProjectionStatus.CONVERGED
    active = tmp_path / ".openclaw/workspace/skills/writer"
    assert active.is_symlink()
    assert (active.resolve() / "SKILL.md").read_bytes() == b"# writer\n"
