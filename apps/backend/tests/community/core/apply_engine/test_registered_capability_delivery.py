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
