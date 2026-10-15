import pytest

from engine.community.core.skills.layout_planner import RuntimeLayoutContext
from engine.community.plugins.claude_code import _skills as claude_skills
from engine.community.plugins.openclaw import _skills as openclaw_skills
from engine.community.plugins.openclaw.plugin_impl import OpenClawPluginImpl
from engine.community.plugins.skills_pool.center_content import MountedCenterContentAdapter


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("module", "port_type", "engine"),
    [
        (openclaw_skills, openclaw_skills._SkillsPortMixin, "openclaw"),
        (claude_skills, claude_skills._SkillsPortMixin, "claude_code"),
    ],
)
async def test_concrete_ports_delegate_daily_apply_to_shared_logical_contract(
    monkeypatch,
    module,
    port_type,
    engine,
) -> None:
    calls = []

    async def apply(**kwargs):
        calls.append(kwargs)
        return {
            "status": "CONVERGED",
            "items": [],
            "issues": [],
            "evidence": {},
        }

    monkeypatch.setattr(module, "apply_logical_mapping_request", apply)
    port = port_type()
    port._center_content_adapter = MountedCenterContentAdapter()
    port._skills_layout_context = RuntimeLayoutContext()

    result = await port.apply_pool_mappings(
        {
            "mappings": [],
            "retired_mappings": [],
            "source_layout": "legacy",
        }
    )

    assert result["status"] == "CONVERGED"
    assert calls[0]["engine"] == engine
    if engine == "openclaw":
        assert calls[0]["layout_context"] == RuntimeLayoutContext()
    assert calls[0]["params"] == {
        "mappings": [],
        "retired_mappings": [],
        "source_layout": "legacy",
    }


@pytest.mark.asyncio
async def test_openclaw_daily_apply_uses_assembled_per_bot_home(tmp_path) -> None:
    """OpenClaw mapping apply 必须落在当前 Bot 的 Engine state root 下。"""
    source = tmp_path / "workspace/skills/skills-local/package-on-disk"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text(
        "name: bbs-browse\ndescription: Browse BBS topics\n",
        encoding="utf-8",
    )

    port = OpenClawPluginImpl(
        center_content_adapter=MountedCenterContentAdapter(),
        skills_layout_context=RuntimeLayoutContext(
            home=tmp_path,
            openclaw_root=tmp_path,
        ),
    )

    result = await port.apply_pool_mappings(
        {
            "mappings": [
                {
                    "corpus": "local",
                    "relative_path": "package-on-disk",
                    "link_name": "bbs-browse",
                }
            ],
            "retired_mappings": [],
            "source_layout": "legacy",
        }
    )

    assert result["status"] == "CONVERGED"
    active_link = tmp_path / "workspace/skills/bbs-browse"
    assert active_link.readlink() == source
