"""Current capability reports through registered worker, phase and reader seams."""

import asyncio
import pytest

from agentclaw.community.core.apply_engine.constructs import Category, Section
from agentclaw.community.core.apply_engine.outcomes import ApplyStatus
from agentclaw.community.core.apply_engine.order import ApplyPhase
from agentclaw.community.core.apply_engine.triggers import (
    CREATE_PRE_CONTAINER,
    CREATE_ON_CONTAINER,
)
from agentclaw.community.core.bot_config_manifest.bot_config_manifest_service_protocol import (
    MANIFEST_STATE,
)
from ._registered_capability_world import registered_world

_DOCUMENT = """schema_version: 1
manifest:
  capabilities:
    skill_sets:
      - name: default
      - name: reviewers
        is_active: false
        skills: []
        mcp: []
"""


def _who(world):
    return dict(
        entity_id="entity-1",
        bot_id="bot-1",
        bot=world.bot,
        owner_id="owner",
        actor_id="owner",
        state=MANIFEST_STATE,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", [False, True], ids=["device", "platform"])
async def test_registered_worker_and_phase_deliver_one_prefixed_capability_report(
    platform,
):
    world, service = registered_world(_DOCUMENT, platform=platform)
    if platform:
        snapshot = world.snapshot()
        assert snapshot.platform_default_codes - snapshot.installed_mcp_codes
    preview = await service.dry_run(**_who(world))
    capability = next(
        c for c in preview.categories if c.construct is Category.CAPABILITIES
    )
    assert [(e.identity, e.outcome.value) for e in capability.entries] == [
        ("skill_sets/default", "unchanged"),
        ("skill_sets/reviewers", "created"),
    ]
    assert world.snapshot().sets == ()
    carry = None
    if not platform:
        pre = await asyncio.to_thread(
            service.start_apply,
            **_who(world),
            phase=ApplyPhase.PRE_CONTAINER,
            trigger=CREATE_PRE_CONTAINER,
        )
        carry = pre.apply_id
    phase = ApplyPhase.PRE_CONTAINER if platform else ApplyPhase.ON_CONTAINER
    accepted = await asyncio.to_thread(
        service.start_apply,
        **_who(world),
        phase=phase,
        trigger=CREATE_PRE_CONTAINER if platform else CREATE_ON_CONTAINER,
        carry_from_apply_id=carry,
    )
    if platform:
        await asyncio.to_thread(
            service.start_apply,
            **_who(world),
            phase=ApplyPhase.ON_CONTAINER,
            trigger=CREATE_ON_CONTAINER,
            carry_from_apply_id=accepted.apply_id,
        )
    report = service.last_apply(entity_id="entity-1", bot_id="bot-1")
    assert report.status is ApplyStatus.SUCCEEDED
    expected = [
        Category.IDENTITY,
        Category.RESOURCES,
        Category.CAPABILITIES,
        Category.CLI_TOOLS,
    ]
    assert [c.construct for c in report.categories] == (
        expected if platform else [Section.SCRIPT, *expected]
    )
    assert report.as_payload()["entries"] == [
        {
            "category": "capabilities",
            "name": "skill_sets/default",
            "action": "unchanged",
            "error": None,
            "note": None,
        },
        {
            "category": "capabilities",
            "name": "skill_sets/reviewers",
            "action": "created",
            "error": None,
            "note": None,
        },
    ]
    assert [s.name for s in world.snapshot().sets] == ["reviewers"]
    assert bool(world.runtime.scopes) is (not platform)
    from agentclaw.community.adapters.http.openapi_v1.bots.config_manifest_support import (
        apply_payload,
    )

    assert apply_payload(report).entries[0].category == "capabilities"
    assert apply_payload(report).entries[-1].name == "skill_sets/reviewers"


@pytest.mark.asyncio
@pytest.mark.parametrize("dry_run", [True, False], ids=["preview", "worker"])
@pytest.mark.parametrize("exhaustion", ["bytes", "time"])
async def test_registered_run_shares_local_fetch_and_public_preview_allowance(
    monkeypatch, dry_run, exhaustion
):
    import hashlib
    import yaml
    from types import SimpleNamespace
    from agentclaw.community.core.apply_engine import fetch_budget
    from agentclaw.community.core.bot_config_manifest.sources.source_resolver import (
        DeclaredSourceResolver,
    )
    from tests.community.core.skill_center.test_skill_version_materializer import (
        _Http,
        _package,
    )
    from ._capabilities_world import CapabilityWorld
    from ._fakes import (
        build_skill_zip,
        FakeObjectCredentials,
        seeded_object_store,
        oss_source,
    )

    now = [0.0]

    class PreviewHttp(_Http):
        def get(self, *args, **kwargs):
            if exhaustion == "time":
                now[0] = 301.0
            return super().get(*args, **kwargs)

    world = CapabilityWorld(http=PreviewHttp(_package()))
    package = build_skill_zip("local-one")
    resolver = DeclaredSourceResolver(
        world.content,
        FakeObjectCredentials(),
        seeded_object_store({"local.zip": package}),
    )
    document = yaml.safe_dump(
        {
            "schema_version": 1,
            "manifest": {
                "capabilities": {
                    "skills": [
                        {
                            "name": "local-one",
                            "source": oss_source("local.zip"),
                            "digest": "sha256:" + hashlib.sha256(package).hexdigest(),
                        },
                        {
                            "name": "weather",
                            "origin": "center",
                            "skill_code": "weather",
                        },
                    ]
                }
            },
        }
    )
    world, service = registered_world(document, world=world, resolver=resolver)
    if exhaustion == "bytes":
        monkeypatch.setattr(fetch_budget, "APPLY_FETCH_TOTAL_LIMIT", len(package) + 5)
    else:
        monkeypatch.setattr(
            fetch_budget, "time", SimpleNamespace(monotonic=lambda: now[0])
        )
    if dry_run:
        report = await service.dry_run(**_who(world))
    else:
        await asyncio.to_thread(service.start_apply, **_who(world))
        report = service.last_apply(entity_id="entity-1", bot_id="bot-1")
    capability = next(
        c for c in report.categories if c.construct is Category.CAPABILITIES
    )
    assert capability.aborted
    assert any(
        "budget is exhausted (" + exhaustion + ")" in (entry.reason or "")
        for entry in capability.entries
    )
    assert len(world.gateway.calls) == 1
    assert world.uploads.packages == {}
    assert world.skills.get_by_git_path("center://weather") is None
    assert world.snapshot().installed_skill_ids == frozenset()
    assert fetch_budget.current_fetch_budget() is None

@pytest.mark.asyncio
async def test_device_creation_on_container_reports_denial_and_preview_never_queries():
    from agentclaw.community.core.skill_center.runtime_projection_contract import (
        RuntimeProjectionResult, RuntimeProjectionStatus,
    )
    from ._capabilities_world import CapabilityWorld
    from .test_capability_full_replace_contract import _Runtime

    class DeliveredRuntime(_Runtime):
        async def apply_plan(self, *, scope, **kwargs):
            self.scopes.append(scope)
            return RuntimeProjectionResult(status=RuntimeProjectionStatus.CONVERGED,
                delivered_mcp_codes=frozenset({"allowed", "denied"}), passport_updated=True,
                passport_mcp_codes=frozenset({"allowed", "denied"}))

    document = "schema_version: 1\nmanifest:\n  mcp:\n    - server_code: allowed\n    - server_code: denied\n"
    calls = []
    def check(user, code, timeout):
        calls.append((user, code))
        return code != "denied"
    world, service = registered_world(document, world=CapabilityWorld(runtime=DeliveredRuntime()),
                                      server_permission_check=check)
    who = {**_who(world), "permission_subject": "owner"}
    preview = await service.dry_run(**_who(world))
    assert preview.status is ApplyStatus.SUCCEEDED and calls == []
    submitted_preview = await service.dry_run(**_who(world), submitted_document=document)
    assert submitted_preview.status is ApplyStatus.SUCCEEDED and calls == []
    await asyncio.to_thread(service.start_apply, **who, phase=ApplyPhase.ON_CONTAINER,
                            trigger=CREATE_ON_CONTAINER)
    report = service.last_apply(entity_id="entity-1", bot_id="bot-1")
    assert report.status is ApplyStatus.SUCCEEDED
    assert {(user, code) for user, code in calls} == {("owner", "allowed"), ("owner", "denied")}
    assert len(report.notes) == 1 and "denied" in report.notes[0]
    assert world.snapshot().installed_mcp_codes == frozenset({"allowed", "denied"})
    assert service.last_apply(entity_id="entity-1", bot_id="bot-1") == report
    assert len(calls) == 2

@pytest.mark.asyncio
async def test_platform_removing_last_direct_mcp_replaces_passport_scope_after_real_write():
    from agentclaw.community.core.ports.manifest_delivery import ManifestDeliveryReceipt, ManifestDeliveryStatus
    from agentclaw.community.core.skill_center.runtime_projection_contract import RuntimeProjectionResult, RuntimeProjectionStatus

    document = "schema_version: 1\nmanifest:\n  mcp:\n    - server_code: weather\n"
    world, service = registered_world(document, platform=True)
    from unittest.mock import MagicMock
    no_defaults = MagicMock()
    no_defaults.server_codes_for.return_value = frozenset()
    world.management._platform_default_mcp_policy = no_defaults
    world.activation._inner._platform_default_mcp_policy = no_defaults
    strategy = service.delivery_for_engine("teclaw")
    events = []
    class Delivery:
        async def deliver_with_receipt(self, ctx):
            codes = world.snapshot().installed_mcp_codes
            events.append(("artifact", codes))
            return ManifestDeliveryReceipt(status=ManifestDeliveryStatus.CONFIRMED, delivered_mcp_codes=codes)
    async def passport(ctx):
        codes = world.snapshot().installed_mcp_codes
        events.append(("passport", codes))
        return RuntimeProjectionResult(status=RuntimeProjectionStatus.CONVERGED, passport_updated=True,
                                       passport_mcp_codes=codes)
    strategy._redeliver = Delivery()
    strategy._passport = passport
    await asyncio.to_thread(service.start_apply, **_who(world), trigger="explicit")
    assert world.snapshot().installed_mcp_codes == frozenset({"weather"})
    events.clear()
    service._capabilities._document = "schema_version: 1\nmanifest:\n  mcp: []\n"
    await asyncio.to_thread(service.start_apply, **_who(world), trigger="explicit")
    report = service.last_apply(entity_id="entity-1", bot_id="bot-1")
    assert report.status is ApplyStatus.SUCCEEDED
    assert world.snapshot().installed_mcp_codes == frozenset()
    assert events == [("artifact", frozenset()), ("passport", frozenset())]

@pytest.mark.asyncio
async def test_platform_committed_mcp_removal_still_updates_passport_if_audit_fails(monkeypatch):
    from unittest.mock import MagicMock
    from agentclaw.community.core.ports.manifest_delivery import ManifestDeliveryReceipt, ManifestDeliveryStatus
    from agentclaw.community.core.skill_center.runtime_projection_contract import RuntimeProjectionResult, RuntimeProjectionStatus

    document = "schema_version: 1\nmanifest:\n  mcp:\n    - server_code: weather\n"
    world, service = registered_world(document, platform=True)
    no_defaults = MagicMock()
    no_defaults.server_codes_for.return_value = frozenset()
    world.management._platform_default_mcp_policy = no_defaults
    world.activation._inner._platform_default_mcp_policy = no_defaults
    strategy = service.delivery_for_engine("teclaw")
    events = []
    class Delivery:
        async def deliver_with_receipt(self, ctx):
            codes = world.snapshot().installed_mcp_codes
            events.append(("artifact", codes))
            return ManifestDeliveryReceipt(status=ManifestDeliveryStatus.CONFIRMED, delivered_mcp_codes=codes)
    async def passport(ctx):
        codes = world.snapshot().installed_mcp_codes
        events.append(("passport", codes))
        return RuntimeProjectionResult(status=RuntimeProjectionStatus.CONVERGED, passport_updated=True,
                                       passport_mcp_codes=codes)
    strategy._redeliver = Delivery()
    strategy._passport = passport
    await asyncio.to_thread(service.start_apply, **_who(world), trigger="explicit")
    assert world.snapshot().installed_mcp_codes == frozenset({"weather"})
    events.clear()
    service._capabilities._document = "schema_version: 1\nmanifest:\n  mcp: []\n"
    def fail_audit(**_kwargs):
        raise RuntimeError("audit unavailable")
    monkeypatch.setattr(world.activation._inner, "_audit", fail_audit)
    await asyncio.to_thread(service.start_apply, **_who(world), trigger="explicit")
    report = service.last_apply(entity_id="entity-1", bot_id="bot-1")
    category = next(c for c in report.categories if c.construct is Category.CAPABILITIES)
    assert report.status is ApplyStatus.PARTIAL
    assert category.aborted and category.removals == ()
    assert world.snapshot().installed_mcp_codes == frozenset()
    assert events == [("artifact", frozenset()), ("passport", frozenset())]


@pytest.mark.asyncio
async def test_platform_retries_passport_after_last_mcp_removed_and_first_sync_fails():
    from unittest.mock import MagicMock
    from agentclaw.community.core.ports.manifest_delivery import ManifestDeliveryReceipt, ManifestDeliveryStatus
    from agentclaw.community.core.skill_center.runtime_projection_contract import RuntimeProjectionResult, RuntimeProjectionStatus

    document = "schema_version: 1\nmanifest:\n  mcp:\n    - server_code: weather\n"
    world, service = registered_world(document, platform=True)
    no_defaults = MagicMock()
    no_defaults.server_codes_for.return_value = frozenset()
    world.management._platform_default_mcp_policy = no_defaults
    world.activation._inner._platform_default_mcp_policy = no_defaults
    strategy = service.delivery_for_engine("teclaw")
    events = []
    fail_sync = [False]
    class Delivery:
        async def deliver_with_receipt(self, _ctx):
            codes = world.snapshot().installed_mcp_codes
            events.append(("artifact", codes))
            return ManifestDeliveryReceipt(status=ManifestDeliveryStatus.CONFIRMED, delivered_mcp_codes=codes)
    async def passport(_ctx):
        codes = world.snapshot().installed_mcp_codes
        events.append(("passport", codes))
        if fail_sync[0]:
            raise RuntimeError("passport unavailable")
        return RuntimeProjectionResult(status=RuntimeProjectionStatus.CONVERGED, passport_updated=True,
                                       passport_mcp_codes=codes)
    strategy._redeliver = Delivery()
    strategy._passport = passport
    await asyncio.to_thread(service.start_apply, **_who(world), trigger="explicit")
    assert world.snapshot().installed_mcp_codes == frozenset({"weather"})
    service._capabilities._document = "schema_version: 1\nmanifest:\n  mcp: []\n"
    fail_sync[0] = True
    for _ in range(2):
        events.clear()
        await asyncio.to_thread(service.start_apply, **_who(world), trigger="explicit")
        report = service.last_apply(entity_id="entity-1", bot_id="bot-1")
        assert world.snapshot().installed_mcp_codes == frozenset()
        assert events == [("artifact", frozenset()), ("passport", frozenset())]
        assert any("Passport" in note for note in report.notes)
    fail_sync[0] = False
    events.clear()
    await asyncio.to_thread(service.start_apply, **_who(world), trigger="explicit")
    report = service.last_apply(entity_id="entity-1", bot_id="bot-1")
    assert events == [("artifact", frozenset()), ("passport", frozenset())]
    assert not any("Passport" in note for note in report.notes)


@pytest.mark.asyncio
async def test_platform_retries_passport_after_default_is_excluded():
    from unittest.mock import MagicMock
    from agentclaw.community.core.models.mcp import SkillSetMCPServer
    from agentclaw.community.core.models.skill import SkillSet
    from agentclaw.community.core.ports.manifest_delivery import ManifestDeliveryReceipt, ManifestDeliveryStatus
    from agentclaw.community.core.skill_center.runtime_projection_contract import RuntimeProjectionResult, RuntimeProjectionStatus

    document = "schema_version: 1\nmanifest:\n  capabilities:\n    skill_sets:\n      - name: default\n"
    world, service = registered_world(document, platform=True)
    no_defaults = MagicMock()
    no_defaults.server_codes_for.return_value = frozenset()
    world.management._platform_default_mcp_policy = no_defaults
    world.activation._inner._platform_default_mcp_policy = no_defaults
    with world.db.transactional_orm_session() as session:
        group = SkillSet(name="default", user_id="", bolt_id="", engine_type="teclaw",
                         is_default=True, is_active=True, env="dev")
        session.add(group)
        session.flush()
        group_id = group.id
        session.add(SkillSetMCPServer(skill_set_id=group_id, server_code="weather", name="Weather", env="dev"))
    strategy = service.delivery_for_engine("teclaw")
    events, fail_sync = [], [False]
    class Delivery:
        async def deliver_with_receipt(self, _ctx):
            events.append("artifact")
            return ManifestDeliveryReceipt(status=ManifestDeliveryStatus.CONFIRMED, delivered_mcp_codes=frozenset())
    async def passport(_ctx):
        events.append("passport")
        if fail_sync[0]:
            raise RuntimeError("passport unavailable")
        return RuntimeProjectionResult(status=RuntimeProjectionStatus.CONVERGED, passport_updated=True,
                                       passport_mcp_codes=frozenset())
    strategy._redeliver = Delivery()
    strategy._passport = passport
    await asyncio.to_thread(service.start_apply, **_who(world), trigger="explicit")
    service._capabilities._document = "schema_version: 1\nmanifest:\n  capabilities:\n    skill_sets: []\n"
    fail_sync[0] = True
    events.clear()
    await asyncio.to_thread(service.start_apply, **_who(world), trigger="explicit")
    assert (group_id, "weather") in world.snapshot().mcp_exclusions
    assert events == ["artifact", "passport"]
    fail_sync[0] = False
    events.clear()
    await asyncio.to_thread(service.start_apply, **_who(world), trigger="explicit")
    assert events == ["artifact", "passport"]
    report = service.last_apply(entity_id="entity-1", bot_id="bot-1")
    assert report.status is ApplyStatus.SUCCEEDED and not report.notes
