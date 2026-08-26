"""Contract tests for the complete Skill/MCP runtime projection."""

from __future__ import annotations

import pytest

from agentclaw.community.core.skill_center.runtime_resolver import (
    RuntimeDesiredState,
    RuntimeNameConflictError,
    RuntimeProjectionResolver,
)
from agentclaw.community.core.skills_pool.models import RegisteredSkillAsset
from agentclaw.community.core.workspace.skill_layout import (
    runtime_layout_engine_for_bot,
)


def test_resolver_projects_every_supported_skill_corpus_and_deduplicates_inputs() -> None:
    state = RuntimeDesiredState(
        skills=(
            RegisteredSkillAsset(skill_id=1, name="local", git_path="local://local"),
            RegisteredSkillAsset(skill_id=2, name="repo", git_path="git://team/repo"),
            RegisteredSkillAsset(
                skill_id=3,
                name="center",
                git_path="center://center-uuid",
                skill_uuid="center-uuid",
                sc_version_number="7",
            ),
            # Installation and System Default can select the same asset.  It is
            # one runtime entry, not a second source-specific projection.
            RegisteredSkillAsset(skill_id=2, name="repo", git_path="git://team/repo"),
        ),
        installed_mcp_server_codes=frozenset({"mcp-b", "mcp-a"}),
        system_default_mcp_server_codes=frozenset({"mcp-default"}),
        system_default_cli_commands=("builtin",),
    )

    projection = RuntimeProjectionResolver().resolve(state)

    assert [mapping.to_dict() for mapping in projection.skill_mappings] == [
        {"corpus": "local", "link_name": "local", "relative_path": "local"},
        {"corpus": "repo", "link_name": "repo", "relative_path": "team/repo"},
        {
            "corpus": "center",
            "link_name": "center",
            "skill_uuid": "center-uuid",
            "sc_version_number": "7",
        },
    ]
    assert projection.mcp_server_codes == ("mcp-a", "mcp-b", "mcp-default")
    assert projection.cli_commands == ("builtin",)


def test_resolver_uses_ac_skill_name_for_local_runtime_entry_not_locator_tail() -> None:
    projection = RuntimeProjectionResolver().resolve(
        RuntimeDesiredState(
            skills=(
                RegisteredSkillAsset(
                    skill_id=1,
                    name="display-name",
                    git_path="local://uploaded-directory",
                ),
            )
        )
    )

    assert projection.skill_mappings[0].link_name == "display-name"
    assert projection.skill_mappings[0].relative_path == "uploaded-directory"


def test_resolver_merges_explicit_and_skill_declared_mcp_dependencies() -> None:
    projection = RuntimeProjectionResolver().resolve(
        RuntimeDesiredState(
            skills=(
                RegisteredSkillAsset(
                    skill_id=1,
                    name="skill",
                    git_path="git://team/skill",
                    mcp_dependencies=("mcp.explicit", {"server_code": "mcp.object"}),
                ),
            ),
            installed_mcp_server_codes=frozenset({"mcp.explicit", "mcp.direct"}),
        )
    )

    assert projection.mcp_server_codes == ("mcp.direct", "mcp.explicit", "mcp.object")


def test_resolver_fails_closed_when_distinct_assets_have_same_runtime_name() -> None:
    with pytest.raises(RuntimeNameConflictError):
        RuntimeProjectionResolver().resolve(
            RuntimeDesiredState(
                skills=(
                    RegisteredSkillAsset(skill_id=1, name="same", git_path="local://one"),
                    RegisteredSkillAsset(skill_id=2, name="same", git_path="git://two"),
                )
            )
        )


def test_resolver_rejects_unknown_sources_instead_of_silently_dropping_them() -> None:
    with pytest.raises(ValueError, match="unsupported skill source"):
        RuntimeProjectionResolver().resolve(
            RuntimeDesiredState(
                skills=(
                    RegisteredSkillAsset(skill_id=1, name="unknown", git_path="ftp://x"),
                )
            )
        )


@pytest.mark.parametrize(
    "template_type",
    ["personalCoding", "applicationCoding", "architect", "customCC"],
)
def test_coding_template_has_aicoding_physical_layout_but_claude_logical_engine(
    template_type: str,
) -> None:
    bot = {
        "bot_type": "personal",
        "active_engine": "claude_code",
        "template_type": template_type,
    }

    assert runtime_layout_engine_for_bot(bot) == "aicoding"


@pytest.mark.parametrize("template_type", [None, "", "normalCC", " NormalCC "])
def test_native_claude_template_keeps_claude_code_physical_layout(
    template_type: str | None,
) -> None:
    bot = {
        "bot_type": "personal",
        "active_engine": "claude_code",
        "template_type": template_type,
    }

    assert runtime_layout_engine_for_bot(bot) == "claude_code"
