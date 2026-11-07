"""Downloaded invalid Center packages spend the real shared Apply allowance."""

import pytest
import httpx

from agentclaw.community.core.apply_engine import fetch_budget
from agentclaw.community.core.apply_engine.desired_state import (
    CenterSkillByCode,
    Declaration,
    DesiredCapabilities,
    DesiredSkill,
    DesiredSkillSet,
)
from agentclaw.community.core.skill_center.materialization_contract import (
    SkillVersionMaterializationError,
)
from agentclaw.community.plugin_api.skill_center_gateway import (
    SkillCenterExactDownloadRequest,
    SkillCenterMcpService,
    SkillCenterReadScope,
)
from tests.community.core.skill_center.test_skill_version_materializer import (
    _Http,
    _package,
)
from ._capabilities_world import CapabilityWorld
from .test_unified_capabilities import apply


def target(location):
    entries = tuple(
        DesiredSkill(f"weather-{index}", CenterSkillByCode(f"weather-{index}"))
        for index in range(3)
    )
    if location == "direct":
        return DesiredCapabilities(skills=Declaration(entries))
    if location == "set":
        return DesiredCapabilities(
            skill_sets=Declaration(
                (DesiredSkillSet("expert", is_active=True, skills=entries, mcp=()),)
            )
        )
    return DesiredCapabilities(
        skills=Declaration(entries[:1]),
        skill_sets=Declaration(
            (DesiredSkillSet("expert", is_active=True, skills=entries[1:], mcp=()),)
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["digest", "zip", "metadata", "http"])
@pytest.mark.parametrize("location", ["direct", "set", "mixed"])
async def test_invalid_center_downloads_exhaust_shared_budget_before_next_package(
    monkeypatch, failure, location
):
    valid = _package()
    downloaded = b"invalid-package" if failure in {"digest", "zip"} else valid

    class StatusHttp(_Http):
        def get(self, path, **kwargs):
            super().get(path, **kwargs)
            return httpx.Response(
                503, content=downloaded, request=httpx.Request("GET", path)
            )

    http = StatusHttp(downloaded) if failure == "http" else _Http(downloaded)
    world = CapabilityWorld(
        http=http, package=valid if failure == "digest" else downloaded
    )
    if failure == "metadata":
        world.gateway.mcp_services = (SkillCenterMcpService(""),)
    monkeypatch.setattr(
        fetch_budget, "APPLY_FETCH_TOTAL_LIMIT", len(downloaded) * 2 - 1
    )
    with fetch_budget.fetch_budget_scope():
        result = await apply(
            world.ctx, world.materialiser, target(location), dry_run=True
        )
        assert result.categories[0].aborted
        assert (
            len(result.categories[0].entries)
            == {"direct": 3, "set": 1, "mixed": 2}[location]
        )
        assert "budget is exhausted (bytes)" in result.categories[0].entries[-1].reason
        assert (
            "budget is exhausted (bytes)"
            in fetch_budget.current_fetch_budget().expired()
        )
    assert len(http.calls) == 2
    assert world.snapshot().installed_skill_ids == frozenset()
    assert world.skills.get_by_git_path("center://weather-0") is None


@pytest.mark.asyncio
async def test_successful_public_preview_charges_download_bytes_exactly_once(
    monkeypatch,
):
    package = _package()
    http = _Http(package)
    world = CapabilityWorld(http=http, package=package)
    monkeypatch.setattr(fetch_budget, "APPLY_FETCH_TOTAL_LIMIT", len(package) + 1)
    with fetch_budget.fetch_budget_scope():
        result = await apply(
            world.ctx,
            world.materialiser,
            DesiredCapabilities(
                skills=Declaration(
                    (DesiredSkill("weather", CenterSkillByCode("weather")),)
                ),
            ),
            dry_run=True,
        )
        assert not result.categories[0].aborted
        budget = fetch_budget.current_fetch_budget()
        assert budget.expired() is None
        budget.charge(1)
        assert "budget is exhausted (bytes)" in budget.expired()
    assert len(http.calls) == 1


def test_non_manifest_exact_preview_failure_has_no_implicit_execution_budget():
    http = _Http(b"bad-digest")
    world = CapabilityWorld(http=http, package=_package())
    request = SkillCenterExactDownloadRequest(
        skill_code="weather", version_number="1.0", scope=SkillCenterReadScope.PUBLIC
    )
    assert fetch_budget.current_fetch_budget() is None
    for _ in range(2):
        with pytest.raises(
            SkillVersionMaterializationError, match="Exact package preview failed"
        ):
            world.materializer.preview_exact(request)
    assert fetch_budget.current_fetch_budget() is None
    assert len(http.calls) == 2
