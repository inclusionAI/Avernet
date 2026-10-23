"""The skills entry compiler: a fetched package → one validated tree."""

from __future__ import annotations

import pytest

from agentclaw.community.core.apply_engine.desired_state import LocalSkillPackage, SkillOrigin
from agentclaw.community.core.bot_config_manifest.compiler.entries.skills import compile_skills
from agentclaw.community.core.content_store import decode_tree_bytes
from tests.community.core.bot_config_manifest.apply._fakes import build_skill_zip, oss_source, real_validator
from tests.community.core.bot_config_manifest.compiler._rig import rig


@pytest.mark.asyncio
async def test_a_zip_package_is_validated_and_filed_as_a_tree() -> None:
    package = build_skill_zip("quality-check", extra=[("scripts/run.sh", b"#!/bin/sh\n")])
    session, content, resolver = rig({"qc.zip": package})
    declared = await compile_skills(
        session,
        [{"name": "quality-check", "source": oss_source("qc.zip")}],
        resolver=resolver,
        content=content,
        validator=real_validator(),
    )
    assert declared.failures == ()
    (skill,) = declared.entries
    assert skill.origin is SkillOrigin.LOCAL and isinstance(skill.source, LocalSkillPackage)
    members = decode_tree_bytes(content.read(skill.source.tree.digest))
    assert [path for path, _ in members] == ["SKILL.md", "scripts/run.sh"]
    assert content.is_reachable(session.scope, skill.source.tree.digest)


@pytest.mark.asyncio
async def test_refusals_name_the_entry() -> None:
    session, content, resolver = rig({"qc.zip": build_skill_zip("quality-check"), "junk.zip": b"not a zip"})
    declared = await compile_skills(
        session,
        [
            {"name": "renamed", "source": oss_source("qc.zip")},
            {"name": "junk", "source": oss_source("junk.zip")},
            {"name": "inline", "content": "text"},
            {"name": "nosource"},
            {"name": "junk", "source": oss_source("junk.zip")},
            {"source": oss_source("qc.zip")},
            {"name": "absent", "source": oss_source("nope.zip")},
        ],
        resolver=resolver,
        content=content,
        validator=real_validator(),
    )
    assert declared.entries == ()
    reasons: dict[str, str] = {}
    for failure in declared.failures:
        reasons.setdefault(failure.identity, failure.reason)
    assert "names its skill 'quality-check'" in reasons["renamed"]
    assert "not a valid package" in reasons["junk"]
    assert "inline 'content' cannot be one" in reasons["inline"]
    assert "must declare 'source' or 'from'" in reasons["nosource"]
    assert "[5]" in reasons and "absent" in reasons
    assert sum(1 for f in declared.failures if f.identity == "junk") == 2
