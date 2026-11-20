"""``BotSyncedLocalSkill`` resolve: a ``from: from_bot`` local skill.

The source type carries only ``(source_bot_id, source_owner_id)`` — no
``ContentRef``, no package bytes, no snapshot read. ``resolve`` is read-only:
it returns a deferred ``Skill`` carrying a :class:`FromBotSource` and
``skill_id=None`` (``key="local:<name>"``) — it does **not** call
``SkillPackageUploadPort.ensure_dst_local_skill_asset`` and does not read the
existing dst row. Registration is the capabilities **writer's** job, so a
``dry_run`` (which stops after ``plan``) leaves the dst ``ac_skill`` table
untouched, satisfying the apply-engine's resolve-is-read-only / dry-run-stops-
before-write contract.
"""

from __future__ import annotations

import asyncio
from typing import Any

from agentclaw.community.core.apply_engine.capabilities import (
    resolve_capabilities,
)
from agentclaw.community.core.apply_engine.context import ApplyContext
from agentclaw.community.core.apply_engine.desired_state import (
    BotSyncedLocalSkill,
    DesiredCapabilities,
    DesiredSkill,
)
from agentclaw.community.core.apply_engine.materialisers.capabilities_skills import (
    resolve_skill,
)
from agentclaw.community.core.apply_engine.materialisers.capabilities_types import (
    CapabilityPlan,
    FromBotSource,
    Resolved,
    Skill,
)
from agentclaw.community.core.apply_engine.materialisers.capabilities_write import (
    write_capabilities,
)
from agentclaw.community.core.apply_engine.outcomes import EntryOutcome
from agentclaw.community.core.apply_engine.registry import (
    Intent,
    PlannedEntry,
)
from agentclaw.community.core.skill_center.skill_set_apply import (
    BotCapabilityScope,
    SkillSetApplySnapshot,
)


def _ctx(*, bot_type: str = "teclaw") -> ApplyContext:
    capabilities = resolve_capabilities(
        active_engine="moltis",
        bot_type=bot_type,
        is_teclaw=lambda _engine: bot_type == "teclaw",
    )
    return ApplyContext(
        bot_id="dst-bot",
        owner_id="dst-owner",
        actor_id="dst-actor",
        entity_id="dst-entity",
        env="dev",
        tenant="default",
        engine_type="moltis",
        bot_type=bot_type,
        bot={
            "bot_id": "dst-bot",
            "entity_id": "dst-entity",
            "active_engine": "moltis",
            "bot_type": bot_type,
        },
        capabilities=capabilities,
        apply_id="apply-1",
    )


def _scope() -> BotCapabilityScope:
    return BotCapabilityScope(
        bot_id="dst-bot",
        owner_id="dst-owner",
        engine_type="moltis",
        default_engine_types=(),
        tenant="default",
        env="dev",
    )


def _empty_snapshot() -> SkillSetApplySnapshot:
    return SkillSetApplySnapshot(
        scope=_scope(),
        sets=(),
        skill_exclusions=frozenset(),
        mcp_exclusions=frozenset(),
        installed_skill_ids=frozenset(),
        installed_mcp_codes=frozenset(),
        platform_default_codes=frozenset(),
        skills=(),
        mcp_overrides={},
    )


def _snapshot_with_existing_local(*, skill_id: int, name: str) -> SkillSetApplySnapshot:
    """A snapshot whose ``skills`` catalog already carries one local-owned dst
    row — the read-only state a re-apply sees. Metadata only: no package, no
    SKILL.md read (file-sync owns the bytes)."""
    return SkillSetApplySnapshot(
        scope=_scope(),
        sets=(),
        skill_exclusions=frozenset(),
        mcp_exclusions=frozenset(),
        installed_skill_ids=frozenset({skill_id}),
        installed_mcp_codes=frozenset(),
        platform_default_codes=frozenset(),
        skills=(
            {
                "id": skill_id,
                "name": name,
                "git_path": f"local://skills-local/{name}",
                "user_id": "dst-owner",
                "bolt_id": "dst-bot",
                "mcp_codes": (),
            },
        ),
        mcp_overrides={},
    )


class _EnsurePort:
    """A ``SkillPackageUploadPort`` stand-in recording the dst registration."""

    def __init__(self, *, rows_by_name: dict[str, dict[str, Any]] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._rows = rows_by_name or {}

    async def ensure_dst_local_skill_asset(
        self, *, bot_id: str, owner_id: str, actor_id: str,
        source_bot_id: str, source_owner_id: str, name: str,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "bot_id": bot_id, "owner_id": owner_id, "actor_id": actor_id,
                "source_bot_id": source_bot_id,
                "source_owner_id": source_owner_id,
                "name": name,
            }
        )
        row = self._rows.get(name)
        if row is None:
            # Match the persistent-service contract: ``operation`` plus a
            # ``skill`` row carrying ``id``/``git_path``/``mcp_codes``-free
            # metadata. Use a fixed dst teclaw-style locator the way the real
            # ``WorkspacePathFactory.get_bot_skills_local_dir`` returns for a
            # teclaw bot: a minimal logical ``skills-local`` path.
            row = {
                "id": 9001,
                "name": name,
                "git_path": f"local://skills-local/{name}",
                "description": "",
                "mcp_dependencies": (),
            }
            self._rows[name] = row
        return {"operation": "created", "skill": dict(row), "actor_id": actor_id}


class _Materialiser:
    """Just the attribute the resolver touches on its owner argument."""

    def __init__(self, uploads: _EnsurePort) -> None:
        self._uploads = uploads


class _Activation:
    """Records the activation claim so the writer's prepared id is observable."""

    def __init__(self) -> None:
        self.claims: list[dict[str, Any]] = []

    async def claim_apply_skill(
        self, *, bot_id: str, owner_id: str, actor_id: str,
        skill_id: str, apply_id: str | None,
    ) -> dict[str, Any]:
        self.claims.append({"skill_id": skill_id, "apply_id": apply_id})
        return {"outcome": "created", "changed": True}


class _WriteOwner:
    """The slice of ``CapabilitiesMaterialiser`` ``write_capabilities`` touches
    for a single deferred ``from_bot`` Direct skill (no sets, no MCP)."""

    def __init__(self, uploads: _EnsurePort, activation: _Activation) -> None:
        self._uploads = uploads
        self._activation = activation

    def _snapshot(self, ctx: ApplyContext) -> SkillSetApplySnapshot:
        return _empty_snapshot()


def _run(coro):
    return asyncio.run(coro)


def test_bot_synced_local_skill_resolve_defers_registration_and_writes_nothing():
    uploads = _EnsurePort()
    materialiser = _Materialiser(uploads)
    ctx = _ctx()
    snapshot = _empty_snapshot()
    entry = DesiredSkill(
        "quality-check",
        BotSyncedLocalSkill(
            source_bot_id="src-bot",
            source_owner_id="src-owner",
        ),
    )

    resolved = _run(resolve_skill(materialiser, ctx, snapshot, entry))

    # Resolve is read-only: the dst ac_skill row is NOT registered here. The
    # writer does that; a dry_run never reaches the writer.
    assert uploads.calls == []
    # No package bytes flow through this road; file-sync carries them.
    assert resolved.package is None
    # No row yet, so no id and a logical not-yet-existing key.
    assert resolved.skill_id is None
    assert resolved.key == "local:quality-check"
    assert resolved.dependencies == frozenset()
    assert resolved.note is None
    # The deferred source identity is carried through to the writer.
    assert resolved.from_bot == FromBotSource("src-bot", "src-owner")


def test_bot_synced_local_skill_reapply_reads_existing_dst_row_from_snapshot_only():
    """A re-apply sees the existing dst ``ac_skill`` row in the read-only
    snapshot (metadata only — never the skill file) and carries its id/key
    forward, deferring the create-or-replace to the writer. ``resolve`` does
    not call ``ensure_dst_local_skill_asset`` either way."""
    uploads = _EnsurePort()
    materialiser = _Materialiser(uploads)
    ctx = _ctx()
    snapshot = _snapshot_with_existing_local(skill_id=4242, name="quality-check")
    entry = DesiredSkill(
        "quality-check",
        BotSyncedLocalSkill(
            source_bot_id="src-bot",
            source_owner_id="src-owner",
        ),
    )

    resolved = _run(resolve_skill(materialiser, ctx, snapshot, entry))

    # The existing row's identity is read straight off the snapshot; no write,
    # no upload-port call.
    assert uploads.calls == []
    assert resolved.skill_id == "4242"
    assert resolved.key == "id:4242"
    assert resolved.package is None
    assert resolved.from_bot == FromBotSource("src-bot", "src-owner")


def test_write_phase_registers_deferred_from_bot_skill_and_claims_returned_id():
    """The deferred ``from_bot`` Skill is registered in the capabilities writer:
    ``ensure_dst_local_skill_asset`` runs (with dst bot/owner/actor from the
    context and source bot/owner from the carried ``FromBotSource``), and the
    returned dst row id drives the activation claim — never ``None``."""
    uploads = _EnsurePort()
    activation = _Activation()
    owner = _WriteOwner(uploads, activation)
    ctx = _ctx()
    skill = Skill(
        "quality-check",
        "local:quality-check",
        None,
        frozenset(),
        package=None,
        from_bot=FromBotSource("src-bot", "src-owner"),
    )
    resolved = Resolved(
        target=DesiredCapabilities(),
        snapshot=_empty_snapshot(),
        direct=(skill,),
    )
    plan = CapabilityPlan(
        entries=(
            PlannedEntry(
                Intent("skills/quality-check", skill, None),
                "created",
                requires_write=True,
            ),
        ),
        resolved=resolved,
    )

    result = _run(write_capabilities(owner, ctx, plan))

    # One dst registration, addressed exactly as the spec spells out: dst
    # bot/owner/actor from the apply context, source bot/owner straight off
    # the carried FromBotSource, name straight off the skill.
    assert uploads.calls == [
        {
            "bot_id": "dst-bot",
            "owner_id": "dst-owner",
            "actor_id": "dst-actor",
            "source_bot_id": "src-bot",
            "source_owner_id": "src-owner",
            "name": "quality-check",
        }
    ]
    # The registered dst row's id drives the activation claim, not ``None``.
    assert activation.claims == [{"skill_id": "9001", "apply_id": "apply-1"}]
    written = next(
        entry for entry in result.entries if entry.identity == "skills/quality-check"
    )
    assert written.outcome is EntryOutcome.CREATED
