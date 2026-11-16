"""The real compiler and aggregate spend the same execution allowance."""

import hashlib
from datetime import datetime

import pytest

from agentclaw.community.core.apply_engine import fetch_budget
from agentclaw.community.core.apply_engine.constructs import Category
from agentclaw.community.core.apply_engine.delivery import ArcaDelivery
from agentclaw.community.core.apply_engine.desired_state import (
    DesiredCapabilities,
    Declaration,
    DesiredSkill,
    CenterSkillByCode,
)
from agentclaw.community.core.apply_engine.orchestrator import ApplyOrchestrator
from agentclaw.community.core.bot_config_manifest.sources.source_resolver import (
    DeclaredSourceResolver,
)
from tests.community.core.skill_center.test_skill_version_materializer import (
    _Http,
    _package,
)
from ._capabilities_world import CapabilityWorld
from ._fakes import (
    FakeObjectCredentials,
    OSS_BUCKET,
    OSS_AUTH,
    build_skill_zip,
    manifest_compiler,
    seeded_object_store,
)
from .test_unified_capabilities import apply


@pytest.mark.asyncio
async def test_local_compiler_fetch_and_first_public_preview_share_one_allowance(
    monkeypatch,
):
    world = CapabilityWorld()
    package = build_skill_zip("local-one")
    objects = seeded_object_store({"local.zip": package})
    compiler = manifest_compiler(
        content=world.content,
        resolver=DeclaredSourceResolver(
            world.content, FakeObjectCredentials(), objects
        ),
    )
    document = {
        "schema_version": 1,
        "manifest": {
            "capabilities": {
                "skills": [
                    {
                        "name": "local-one",
                        "source": {
                            "protocol": "oss",
                            "bucket": OSS_BUCKET,
                            "key": "local.zip",
                            "auth": OSS_AUTH,
                        },
                        "digest": "sha256:" + hashlib.sha256(package).hexdigest(),
                    },
                    {"name": "weather", "origin": "center", "skill_code": "weather"},
                ]
            }
        },
    }
    monkeypatch.setattr(fetch_budget, "APPLY_FETCH_TOTAL_LIMIT", len(package) + 5)
    with fetch_budget.fetch_budget_scope():
        compiled = await compiler.compile_document(
            world.ctx, document, only={Category.CAPABILITIES}
        )
        assert not compiled.state.capabilities.failures
        assert fetch_budget.current_fetch_budget().expired() is None
        result = await ApplyOrchestrator(
            {Category.CAPABILITIES: world.materialiser},
            steps=ArcaDelivery(lambda: None).steps_for,
        ).apply(
            world.ctx,
            compiled.state,
            apply_id="test",
            trigger="explicit",
            started_at=datetime.now(),
        )
        assert result.categories[0].aborted
        assert any(
            "budget is exhausted (bytes)" in (e.reason or "")
            for e in result.categories[0].entries
        )
    assert len(world.gateway.calls) == 1
    assert world.uploads.packages == {}
    assert world.skills.get_by_git_path("center://weather") is None
    assert world.snapshot().installed_skill_ids == frozenset()


@pytest.mark.asyncio
async def test_ready_public_reference_costs_no_download_budget(monkeypatch):
    world = CapabilityWorld()
    target = DesiredCapabilities(
        skills=Declaration((DesiredSkill("weather", CenterSkillByCode("weather")),))
    )
    assert (
        not (await apply(world.ctx, world.materialiser, target)).categories[0].aborted
    )
    monkeypatch.setattr(fetch_budget, "APPLY_FETCH_TOTAL_LIMIT", 1)
    with fetch_budget.fetch_budget_scope():
        result = await apply(world.ctx, world.materialiser, target, dry_run=True)
        assert not result.categories[0].aborted
        assert fetch_budget.current_fetch_budget().expired() is None
    assert len(world.gateway.calls) == 1


@pytest.mark.asyncio
async def test_public_preview_checks_time_again_after_download(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(fetch_budget.time, "monotonic", lambda: now[0])

    class SlowHttp(_Http):
        def get(self, *args, **kwargs):
            now[0] = 301.0
            return super().get(*args, **kwargs)

    # The catalog digest and HTTP download describe one exact artifact;
    # independent ZIP construction can differ in timestamp bytes under load.
    package = _package()
    world = CapabilityWorld(http=SlowHttp(package), package=package)
    target = DesiredCapabilities(
        skills=Declaration((DesiredSkill("weather", CenterSkillByCode("weather")),))
    )
    with fetch_budget.fetch_budget_scope():
        result = await apply(world.ctx, world.materialiser, target, dry_run=True)
        assert result.categories[0].aborted
        assert "budget is exhausted (time)" in result.categories[0].entries[0].reason
    assert world.skills.get_by_git_path("center://weather") is None
    assert world.snapshot().installed_skill_ids == frozenset()
