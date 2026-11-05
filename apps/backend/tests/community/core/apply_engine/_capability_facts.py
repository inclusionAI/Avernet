"""Snapshot boundary support for the existing port-only lifecycle test rigs.

Persistence-shaped Set behavior is covered by CapabilityWorld; this adapter
keeps the older file/queue/error fixtures on the new read snapshot contract.
"""

from agentclaw.community.core.skill_center.mcp_dependency_scope import (
    mcp_dependency_codes,
)
from agentclaw.community.core.skill_center.skill_set_apply import (
    BotCapabilityScope,
    SkillSetApplySnapshot,
    SkillSetSnapshot,
    SkillSetApplyResult,
)
from agentclaw.community.core.skill_center.skill_reference_contract import (
    ReadyRepoSkill,
)


class CapabilityFacts:
    def __init__(self, activation, reader, uploads):
        self.activation = getattr(activation, "_inner", activation)
        self.reader = reader
        self.uploads = getattr(uploads, "_inner", uploads)
        if hasattr(self.activation, "installed_skills"):
            self.activation.installed_skills.update(
                int(a.skill_id) for a in getattr(reader, "assets", ())
            )
        if hasattr(self.activation, "skill_dependencies"):
            self.activation.skill_dependencies.update({
                int(a.skill_id): frozenset(mcp_dependency_codes(a.mcp_dependencies))
                for a in getattr(reader, "assets", ())
            })
        self.excluded = frozenset()
        self.removed = set()

    def read_apply_snapshot(self, *, bot_id, owner_id, actor_id):
        reader, activation = self.reader, self.activation
        active = reader.active_skill_assets(bot_id=bot_id, owner_id=owner_id)
        local = reader.local_skill_assets(bot_id=bot_id, owner_id=owner_id)
        records = {}
        for asset in (*active, *local):
            records[int(asset.skill_id)] = dict(
                id=int(asset.skill_id),
                name=asset.name,
                git_path=asset.git_path,
                user_id=owner_id,
                bolt_id=bot_id,
                mcp_codes=frozenset(
                    mcp_dependency_codes(getattr(asset, "mcp_dependencies", ()))
                ),
            )
        rows = getattr(self.uploads, "rows", {})
        for name, row in rows.items():
            records[int(row["id"])] = dict(
                id=int(row["id"]),
                name=name,
                git_path="local://" + name,
                user_id=owner_id,
                bolt_id=bot_id,
                mcp_codes=frozenset(),
            )
        member_ids = getattr(reader, "member_ids", frozenset())
        groups = []
        managed = set(getattr(activation, "set_managed", ()))
        if (member_ids or managed) and "fixture-members" not in self.removed:
            groups.append(
                SkillSetSnapshot(
                    1,
                    "fixture-members",
                    None,
                    False,
                    True,
                    tuple(member_ids),
                    tuple(sorted(managed)),
                )
            )
        defaults = frozenset(getattr(activation, "platform_defaults", ()))
        if defaults:
            groups.append(
                SkillSetSnapshot(
                    2, "default", None, True, True, (), tuple(sorted(defaults))
                )
            )
        installed = (
            {int(a.skill_id) for a in active}
            | set(getattr(activation, "installed_skills", ()))
        ) - set(getattr(activation, "skill_deactivations", ()))
        return SkillSetApplySnapshot(
            BotCapabilityScope(
                bot_id, owner_id, "openclaw", ("openclaw",), "teamclaw", "dev"
            ),
            tuple(groups),
            frozenset(),
            self.excluded,
            frozenset(installed),
            frozenset(getattr(activation, "installed", ())),
            defaults,
            tuple(records.values()),
            dict(getattr(activation, "mcp_overrides", {})),
            frozenset(member_ids),
        )

    async def replace_apply_skill_sets(
        self, *, remove_names, default_change, sets, **kwargs
    ):
        assert not sets, "Set persistence tests must use the real CapabilityWorld"
        self.removed.update(remove_names)
        self.excluded |= default_change.add_mcps
        activation = self.activation
        if "fixture-members" in remove_names:
            activation.set_managed.clear()
        for _, code in default_change.add_mcps:
            activation.platform_defaults.discard(code)
            activation.installed.discard(code)
        return SkillSetApplyResult(
            (),
            (),
            (),
            tuple(remove_names),
            bool(remove_names or default_change.add_mcps),
            {},
            bool(default_change.add_mcps),
            bool(default_change.add_mcps),
        )


class SharedReferences:
    def resolve_shared_skill_id(self, *, skill_id, expected_name, **kwargs):
        return ReadyRepoSkill(
            str(skill_id), expected_name, "git://" + expected_name, frozenset()
        )


def port_kwargs(*, capability_reader, activation_service, upload_service, **kwargs):
    return dict(
        kwargs,
        activation_service=activation_service,
        upload_service=upload_service,
        skill_references=SharedReferences(),
        skill_sets=CapabilityFacts(
            activation_service, capability_reader, upload_service
        ),
    )


def build_test_materialisers(**kwargs):
    from agentclaw.community.core.apply_engine.registry import build_materialisers

    return build_materialisers(**port_kwargs(**kwargs))


def materialiser_ports(**kwargs):
    from agentclaw.community.core.apply_engine.delivery import MaterialiserPorts

    return MaterialiserPorts(**port_kwargs(**kwargs))
