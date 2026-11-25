"""The skills materialiser's Dry-run MCP dependency projection: what survives the apply, before any write."""

from __future__ import annotations

from agentclaw.community.core.apply_engine.desired_state import CenterSkillByName, DesiredSkill

from ._fakes import FakeSharedSkills, skill_asset
from .test_skills_materialiser import QC_KEY, QZ, _ctx, _declared, _run, skill_rig


def test_dry_run_dependency_projection_uses_persisted_local_metadata():
    dependency = ({"code": "mcp.persisted"},)
    materialiser, _, _, _, _, _ = skill_rig(
        packages={QC_KEY: QZ},
        assets=[skill_asset(11, "quality-check", mcp_dependencies=dependency)],
        local_assets=[
            skill_asset(11, "quality-check", mcp_dependencies=dependency)
        ],
    )
    ctx = _ctx()
    resolved = _run(materialiser.resolve(ctx, [_declared()]))
    _run(materialiser.plan(ctx, resolved.intents))

    assert ctx.capability_state.final_skill_dependency_codes == frozenset(
        {"mcp.persisted"}
    )


def test_dry_run_dependency_projection_keeps_set_supplied_skill_dependencies():
    """A set's skills survive a ``skills: []`` apply, so the MCPs they depend on stay in the projection."""
    materialiser, _, activation, _, _, _ = skill_rig(
        assets=[skill_asset(11, "from-a-set", mcp_dependencies=({"code": "mcp.from-set"},))],
        local_assets=[],
    )
    activation.set_skill_names = {"from-a-set"}
    ctx = _ctx()
    plan = _run(materialiser.plan(ctx, ()))

    assert plan.removals == ()
    assert ctx.capability_state.final_skill_dependency_codes == frozenset({"mcp.from-set"})


def test_dry_run_dependency_projection_keeps_an_active_declared_shared_skill_dependencies():
    """A declared Center skill already active as the same asset keeps its row, so its MCPs stay projected."""
    shared = FakeSharedSkills(center={"triage": ["41"]})
    materialiser, _, _, _, _, _ = skill_rig(
        assets=[skill_asset(41, "triage", git_path="center://triage", mcp_dependencies=({"code": "jira"},))],
        shared_skills=shared,
    )
    raw = materialiser.materialiser
    ctx = _ctx()
    resolved = _run(raw.resolve(ctx, [DesiredSkill("triage", CenterSkillByName())]))
    _run(raw.plan(ctx, resolved.intents))

    assert ctx.capability_state.final_skill_dependency_codes == frozenset({"jira"})


def test_brand_new_local_package_has_no_dependency_metadata_at_apply_time():
    materialiser, _, _, _, _, _ = skill_rig(
        packages={QC_KEY: QZ}, assets=[], local_assets=[]
    )
    ctx = _ctx()
    resolved = _run(materialiser.resolve(ctx, [_declared()]))
    _run(materialiser.plan(ctx, resolved.intents))

    assert ctx.capability_state.final_skill_dependency_codes == frozenset()
