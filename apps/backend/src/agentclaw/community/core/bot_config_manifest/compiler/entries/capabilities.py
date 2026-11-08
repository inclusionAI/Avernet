"""Compile all capability subtargets with the invocation's one fetch session."""

from typing import Any, Mapping
from agentclaw.community.core.apply_engine.desired_state import (
    CapabilityDeclaration,
    Declaration,
    DefaultSkillSetRef,
    DesiredCapabilities,
    DesiredSkillSet,
    EntryFailure,
)
from agentclaw.community.core.bot_config_manifest.compiler.entries.skills import (
    compile_skills,
)
from agentclaw.community.core.bot_config_manifest.compiler.entries.mcp import (
    compile_mcp,
)
from agentclaw.community.core.bot_config_manifest.compiler.session import CompileSession
from agentclaw.community.core.bot_config_manifest.sources.source_resolver import (
    DeclaredSourceResolver,
)
from agentclaw.community.core.content_store import ContentStoreProtocol
from agentclaw.community.core.skill_center.skill_package import SkillPackageValidator


async def compile_capabilities(
    session: CompileSession,
    target: Mapping[str, Any],
    *,
    resolver: DeclaredSourceResolver,
    content: ContentStoreProtocol,
    validator: SkillPackageValidator,
) -> CapabilityDeclaration:
    kwargs = dict(resolver=resolver, content=content, validator=validator)
    skills = await compile_skills(session, target.get("skills", []), **kwargs)
    sets: list[DesiredSkillSet | DefaultSkillSetRef] = []
    failures: list[EntryFailure] = []
    for entry in target.get("skill_sets", []):
        name = entry["name"]
        if name == "default":
            sets.append(DefaultSkillSetRef())
            continue
        members = await compile_skills(session, entry["skills"], **kwargs)
        if members.failures:
            failures.append(
                EntryFailure(
                    name,
                    "; ".join(
                        f"skill {failure.identity!r}: {failure.reason}"
                        for failure in members.failures
                    ),
                )
            )
            continue
        sets.append(
            DesiredSkillSet(
                name=name,
                is_active=entry["is_active"],
                skills=members.entries,
                mcp=tuple(member["server_code"] for member in entry["mcp"]),
                description=entry.get("description"),
            )
        )
    declaration = CapabilityDeclaration.from_target(
        DesiredCapabilities(
            skills=skills,
            skill_sets=Declaration(tuple(sets), tuple(failures)),
            mcp=compile_mcp(target.get("mcp", [])),
        )
    )
    assert (
        declaration is not None
    )  # all three Manifest scopes participate, including empty targets
    return declaration
