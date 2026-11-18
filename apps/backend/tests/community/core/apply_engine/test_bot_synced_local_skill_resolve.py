"""``BotSyncedLocalSkill`` resolve: a ``from: from_bot`` local skill.

The new source type carries only ``(source_bot_id, source_owner_id)`` — no
``ContentRef``, no package bytes. ``resolve_skill`` calls
``SkillPackageUploadPort.ensure_dst_local_skill_asset`` to register the dst
``ac_skill`` row (reverse-lookup source, recompute dst git_path, skip the
package write that file-sync already owns), and returns a ``Skill`` with
``package=None`` so the capabilities writer takes the prepared-id branch and
only runs the activation claim.
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
    DesiredSkill,
)
from agentclaw.community.core.apply_engine.materialisers.capabilities_skills import (
    resolve_skill,
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


def _empty_snapshot() -> SkillSetApplySnapshot:
    scope = BotCapabilityScope(
        bot_id="dst-bot",
        owner_id="dst-owner",
        engine_type="moltis",
        default_engine_types=(),
        tenant="default",
        env="dev",
    )
    return SkillSetApplySnapshot(
        scope=scope,
        sets=(),
        skill_exclusions=frozenset(),
        mcp_exclusions=frozenset(),
        installed_skill_ids=frozenset(),
        installed_mcp_codes=frozenset(),
        platform_default_codes=frozenset(),
        skills=(),
        mcp_overrides={},
    )


class _EnsurePort:
    """A ``SkillPackageUploadPort`` stand-in: only the new method runs here."""

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


def _run(coro):
    return asyncio.run(coro)


def test_bot_synced_local_skill_resolve_calls_ensure_dst_and_returns_no_package():
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

    # One call to the upload-port method, addressed exactly as the spec
    # spells out: dst bot/owner/actor from the apply context, source bot/owner
    # straight off the source tag, name straight off the entry.
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
    # The registered dst row's id drives both the key and the prepared skill
    # id the capabilities writer takes (package is None → no upload, no
    # replace path).
    assert resolved.name == "quality-check"
    assert resolved.skill_id == "9001"
    # ``asset_key`` of a non-center row → ``"id:<row id>"``.
    assert resolved.key == "id:9001"
    # The package bytes never flow through this road; file-sync carries them.
    assert resolved.package is None
    # File-sync carries MCP wiring too: a freshly-registered dst row has none,
    # and the capabilities writer does not look at this set on the prepared
    # path anyway.
    assert resolved.dependencies == frozenset()
    assert resolved.note is None


def test_bot_synced_local_skill_reapply_reuses_returned_dst_row():
    # A real second apply: ensure_dst_local_skill_asset returns the existing
    # dst row (operation="replaced", same id, same git_path). resolve_skill
    # takes the returned row at face value, the way the upload road does, so
    # a re-apply reports ``updated`` on the existing id, not a fresh id.
    existing = {
        "id": 4242,
        "name": "quality-check",
        "git_path": "local://skills-local/quality-check",
        "description": "synced payload",
        "mcp_dependencies": (),
    }
    uploads = _EnsurePort(rows_by_name={"quality-check": dict(existing)})
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

    assert resolved.skill_id == "4242"
    assert resolved.key == "id:4242"
    assert resolved.package is None
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
