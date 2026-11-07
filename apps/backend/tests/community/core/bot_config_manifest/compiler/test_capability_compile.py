"""ManifestCompiler compiles one target, keeping absent and empty scopes distinct."""

import pytest
import yaml
from agentclaw.community.core.apply_engine.constructs import Category, Section
from agentclaw.community.core.apply_engine.desired_state import (
    CenterSkillByCode,
    RepoSkillPath,
)
from agentclaw.community.core.bot_config_manifest.bot_config_manifest_service_protocol import (
    MANIFEST_STATE,
)
from tests.community.core.apply_engine._fakes import make_context, build_skill_zip
from .test_compile import _compiler
from agentclaw.community.core.bot_config_manifest.compiler.session import digest_of


@pytest.mark.asyncio
async def test_wrapped_and_flat_inputs_compile_to_the_same_complete_target():
    target = {
        "skills": [{"name": "review", "origin": "repo", "repo_path": "common/review"}],
        "skill_sets": [
            {"name": "default"},
            {
                "name": "team",
                "is_active": False,
                "skills": [
                    {
                        "name": "research",
                        "origin": "center",
                        "skill_code": "public-research",
                    }
                ],
                "mcp": [],
            },
        ],
        "mcp": [{"server_code": "mcp.docs"}],
    }
    states = []
    for manifest in (target, {"capabilities": target}):
        compiler, _ = _compiler(
            yaml.safe_dump({"schema_version": 1, "manifest": manifest})
        )
        states.append((await compiler.compile(make_context(), MANIFEST_STATE)).state)
    assert states[0] == states[1]
    declaration = states[0].capabilities
    capabilities = declaration.entries[0]
    assert capabilities.skills.entries[0].source == RepoSkillPath("common/review")
    assert capabilities.skill_sets.entries[1].skills[0].source == CenterSkillByCode(
        "public-research"
    )
    assert declaration.identities == (
        "skills/review",
        "skill_sets/default",
        "skill_sets/team",
        "mcp/mcp.docs",
    )
    assert states[0].skills is None and states[0].mcp is None


@pytest.mark.asyncio
async def test_selected_version_only_document_keeps_one_empty_aggregate_target():
    compiler, _ = _compiler("schema_version: 1\n")
    state = (
        await compiler.compile(
            make_context(), MANIFEST_STATE, only={Category.CAPABILITIES}
        )
    ).state
    declaration = state.capabilities
    assert len(declaration.entries) == 1 and declaration.identities == ()
    assert all(child.entries == () for _, child in declaration.entries[0].children())
    assert (
        await compiler.compile(make_context(), MANIFEST_STATE, only={Section.SCRIPT})
    ).state.capabilities is None


@pytest.mark.asyncio
async def test_invalid_nested_package_reports_the_set_and_member():
    package = build_skill_zip("actual")
    target = {
        "skill_sets": [
            {
                "name": "team",
                "is_active": True,
                "skills": [
                    {
                        "name": "expected",
                        "source": {
                            "protocol": "oss",
                            "bucket": "manifest-fixtures",
                            "key": "skill.zip",
                            "auth": "oss-cred",
                        },
                        "digest": digest_of(package),
                    }
                ],
                "mcp": [],
            }
        ]
    }
    compiler, _ = _compiler(
        yaml.safe_dump({"schema_version": 1, "manifest": {"capabilities": target}}),
        {"skill.zip": package},
    )
    state = (await compiler.compile(make_context(), MANIFEST_STATE)).state
    failure = state.capabilities.failures[0]
    assert failure.identity == "skill_sets/team"
    assert "expected" in failure.reason and "'actual'" in failure.reason
    assert state.capabilities.entries[0].skill_sets.entries == ()


@pytest.mark.asyncio
async def test_direct_and_nested_local_packages_consume_one_fetch_allowance(
    monkeypatch,
):
    import agentclaw.community.core.apply_engine.fetch_budget as budget_module

    direct = build_skill_zip("direct")
    nested = build_skill_zip("nested")
    # Pin the configured allowance, keeping the real compiler, fetcher and ledger.
    monkeypatch.setattr(budget_module, "APPLY_FETCH_TOTAL_LIMIT", len(direct))

    def package(name, body):
        return {
            "name": name,
            "source": {
                "protocol": "oss",
                "bucket": "manifest-fixtures",
                "key": f"{name}.zip",
                "auth": "oss-cred",
            },
            "digest": digest_of(body),
        }

    target = {
        "skills": [package("direct", direct)],
        "skill_sets": [
            {
                "name": "team",
                "is_active": False,
                "skills": [package("nested", nested)],
                "mcp": [],
            }
        ],
    }
    compiler, _ = _compiler(
        yaml.safe_dump({"schema_version": 1, "manifest": {"capabilities": target}}),
        {"direct.zip": direct, "nested.zip": nested},
    )
    state = (await compiler.compile(make_context(), MANIFEST_STATE)).state
    target = state.capabilities.entries[0]
    assert target.skills.identities == ("direct",), target.skills.failures
    assert state.capabilities.failures[0].identity == "skill_sets/team"
    assert "budget is exhausted (bytes)" in state.capabilities.failures[0].reason


@pytest.mark.asyncio
async def test_uuid_references_use_the_stable_canonical_uuid_spelling():
    compiler, _ = _compiler("""schema_version: 1
manifest:
  skills:
    - name: review
      origin: center
      skill_uuid: 5E79E62468984D7791FD11B9FB0822C4
""")
    state = (await compiler.compile(make_context(), MANIFEST_STATE)).state
    assert (
        state.capabilities.entries[0].skills.entries[0].source.skill_uuid
        == "5e79e624-6898-4d77-91fd-11b9fb0822c4"
    )
