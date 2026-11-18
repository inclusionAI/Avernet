"""The skills entry compiler: a fetched package → one validated tree."""

from __future__ import annotations

import pytest

from agentclaw.community.core.apply_engine.desired_state import (
    BotSyncedLocalSkill,
    LocalSkillPackage,
    SkillOrigin,
)
from agentclaw.community.core.bot_config_manifest.compiler.entries._fetch import (
    EntryFetchError,
)
from agentclaw.community.core.bot_config_manifest.compiler.entries.skills import (
    compile_skills,
)
from agentclaw.community.core.content_store import decode_tree_bytes
from tests.community.core.apply_engine._fakes import build_skill_zip, oss_source, real_validator
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


# ── ``protocol: bot`` source → ``BotSyncedLocalSkill`` (no fetch) ──────────


@pytest.mark.asyncio
async def test_a_named_bot_source_entry_compiles_to_a_bot_synced_local_skill_without_fetching() -> None:
    """A skill entry with ``from: <name>`` naming a ``protocol: bot`` source
    compiles to a ``BotSyncedLocalSkill`` DesiredSkill carrying the source
    bot's ``(bot_id, owner_id)``. The package is not fetched, filed, validated
    or uploaded by the compiler — the file-sync materialiser converges it — so
    the content store records no receipt and no blob write.
    """
    session, content, resolver = rig(
        sources={"from_bot": {"protocol": "bot", "bot_id": "src_1", "owner_id": "u1"}}
    )
    declared = await compile_skills(
        session,
        [{"name": "triage", "from": "from_bot"}],
        resolver=resolver,
        content=content,
        validator=real_validator(),
    )
    assert declared.failures == ()
    (skill,) = declared.entries
    assert skill.name == "triage"
    assert skill.origin is SkillOrigin.LOCAL
    assert skill.source == BotSyncedLocalSkill(source_bot_id="src_1", source_owner_id="u1")
    # No fetch, no filed bytes: the sync materialiser owns the package.
    assert content.store_calls == []
    assert content.receipts == []


@pytest.mark.asyncio
async def test_an_inline_bot_source_entry_compiles_to_a_bot_synced_local_skill() -> None:
    """An inline ``source: {protocol: bot, bot_id: …, owner_id: …}`` is the
    same declaration as a named one and compiles the same way."""
    session, content, resolver = rig()
    declared = await compile_skills(
        session,
        [
            {
                "name": "triage",
                "source": {"protocol": "bot", "bot_id": "inline_1", "owner_id": "u1"},
            }
        ],
        resolver=resolver,
        content=content,
        validator=real_validator(),
    )
    assert declared.failures == ()
    (skill,) = declared.entries
    assert skill.source == BotSyncedLocalSkill(source_bot_id="inline_1", source_owner_id="u1")
    assert content.store_calls == []


@pytest.mark.asyncio
async def test_a_bot_source_entry_whose_identity_resolution_fails_is_reported_and_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``resolve_bot_identity`` that raises ``EntryFetchError`` is reported as
    an entry failure for that entry name rather than propagating; the loop
    continues, so a sibling skill on the same source still resolves.
    """
    from agentclaw.community.core.bot_config_manifest.compiler.entries import (
        skills as skills_module,
    )

    real_resolve = skills_module.resolve_bot_identity

    def _raise_on_broken(session, entry):
        if entry.get("name") == "broken":
            raise EntryFetchError("could not resolve the source bot identity")
        return real_resolve(session, entry)

    monkeypatch.setattr(skills_module, "resolve_bot_identity", _raise_on_broken)
    session, content, resolver = rig(
        sources={"from_bot": {"protocol": "bot", "bot_id": "src_1", "owner_id": "u1"}}
    )
    declared = await compile_skills(
        session,
        [
            {"name": "broken", "from": "from_bot"},
            {"name": "good", "from": "from_bot"},
        ],
        resolver=resolver,
        content=content,
        validator=real_validator(),
    )
    # The failing entry's name survives to its failure record; the loop
    # continued through to the resolving sibling.
    (failure,) = declared.failures
    assert failure.identity == "broken"
    assert "could not resolve" in failure.reason
    (skill,) = declared.entries
    assert skill.name == "good"
    assert skill.source == BotSyncedLocalSkill(source_bot_id="src_1", source_owner_id="u1")
