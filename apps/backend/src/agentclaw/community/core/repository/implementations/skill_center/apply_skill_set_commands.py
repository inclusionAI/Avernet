"""One transaction for an Apply's named Sets and Default member deltas."""

from __future__ import annotations

from typing import Any, Mapping

from agentclaw.community.core.models.skill import Skill, SkillSet, SkillSetSkill
from agentclaw.community.core.models.mcp import SkillSetMCPServer
from agentclaw.community.core.models.skill_installation_source import (
    SkillInstallationSourceKind,
)
from agentclaw.community.core.repository.capability_desired_state_types import (
    DesiredStateMutation,
)
from agentclaw.community.core.repository.implementations.skill_center.bot_skillset_installations import (
    center_membership_skill_uuid,
    set_member_skill_ids,
)
from agentclaw.community.core.repository.implementations.skill_center.skill_mcp_dependencies import (
    skill_projection_mcp_dependency_codes,
)
from agentclaw.community.core.repository.implementations.skill_center.tables import (
    default_exclusions,
    skill_installations,
    mcp_installations,
    bot_mcp_configs,
)
from agentclaw.community.core.skill_center.errors import (
    SkillSetControlPlaneConflictError,
    SkillSetControlPlaneNotFoundError,
)
from agentclaw.community.core.skill_center.offline_policy import require_skill_online
from agentclaw.community.core.skill_center.policies.capability_ownership import (
    require_can_join_set,
)
from agentclaw.community.core.skill_center.skill_set_apply import (
    BotCapabilityScope,
    DefaultExclusionChange,
    ResolvedSkillSetWrite,
    SkillSetSnapshot,
    SkillSetApplySnapshot,
)
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env


def require_apply_scope(scope: BotCapabilityScope) -> None:
    if scope.tenant != get_current_avernet_tenant() or scope.env != get_current_env():
        raise SkillSetControlPlaneConflictError("CAPABILITY_SCOPE_CHANGED")


class ApplySkillSetCommands:
    def _apply_facts(self, session, rows):
        result = []
        for row in rows:
            skills = (
                self._scope(session.query(SkillSetSkill), SkillSetSkill)
                .filter(SkillSetSkill.skill_set_id == row.id)
                .all()
            )
            mcps = (
                self._scope(session.query(SkillSetMCPServer), SkillSetMCPServer)
                .filter(SkillSetMCPServer.skill_set_id == row.id)
                .all()
            )
            result.append(
                SkillSetSnapshot(
                    int(row.id),
                    str(row.name),
                    row.description,
                    bool(row.is_default),
                    bool(row.is_default or row.is_active),
                    tuple(sorted({int(member.skill_id) for member in skills})),
                    tuple(sorted({str(member.server_code) for member in mcps})),
                    tuple(
                        sorted(
                            (int(member.skill_id), member.skill_uuid)
                            for member in skills
                        )
                    ),
                )
            )
        return tuple(result)

    def read_apply_snapshot(
        self, *, scope: BotCapabilityScope, platform_default_codes: frozenset[str]
    ) -> SkillSetApplySnapshot:
        require_apply_scope(scope)
        with self._db.orm_session() as session:
            rows = self._bot_sets(
                session,
                bot_id=scope.bot_id,
                owner_id=scope.owner_id,
                engine_type=scope.engine_type,
                default_engine_types=scope.default_engine_types,
            )
            sets = self._apply_facts(session, rows)
            defaults = {item.id for item in sets if item.is_default}
            installed = self._installations(session, scope.bot_id, scope.owner_id)
            ids = installed | {skill for item in sets for skill in item.skill_ids}
            uuids = {uuid for item in sets for _, uuid in item.skill_references if uuid}
            assets = (
                self._scope(session.query(Skill), Skill)
                .filter(
                    (Skill.id.in_(ids))
                    | Skill.skill_uuid.in_(uuids)
                    | (
                        (Skill.user_id == scope.owner_id)
                        & (Skill.bolt_id == scope.bot_id)
                        & Skill.git_path.like("local://%")
                    )
                )
                .order_by(Skill.id)
                .all()
            )
            skills = []
            for asset in assets:
                data = {
                    key: getattr(asset, key)
                    for key in (
                        "name",
                        "description",
                        "git_path",
                        "user_id",
                        "bolt_id",
                        "skill_uuid",
                        "status",
                        "version",
                        "offline_at",
                    )
                }
                data["id"] = str(asset.id)
                try:
                    data["mcp_codes"] = tuple(
                        sorted(skill_projection_mcp_dependency_codes(session, asset))
                    )
                    data["resolution_error"] = None
                except (TypeError, ValueError):
                    data["mcp_codes"] = ()
                    data["resolution_error"] = "SKILL_MCP_METADATA_UNAVAILABLE"
                skills.append(data)
            references_by_id = {
                int(asset.id): frozenset(
                    self._skill_referencing_set_ids(
                        session,
                        skill_id=int(asset.id),
                        skill_uuid=asset.skill_uuid,
                    )
                )
                for asset in assets
            }
            return SkillSetApplySnapshot(
                scope,
                sets,
                frozenset(
                    key
                    for key in default_exclusions.all_skill_exclusions(
                        session, bot_id=scope.bot_id, owner_id=scope.owner_id
                    )
                    if key[0] in defaults
                ),
                frozenset(
                    key
                    for key in default_exclusions.all_mcp_exclusions(
                        session, bot_id=scope.bot_id, owner_id=scope.owner_id
                    )
                    if key[0] in defaults
                ),
                frozenset(installed),
                frozenset(
                    self._mcp_installations(session, scope.bot_id, scope.owner_id)
                ),
                platform_default_codes,
                tuple(skills),
                bot_mcp_configs.get_all(
                    session, bot_id=scope.bot_id, owner_id=scope.owner_id, env=scope.env
                ),
                frozenset(sid for sid, refs in references_by_id.items() if refs),
                {
                    int(asset.id): references_by_id[int(asset.id)]
                    for asset in assets
                    if str(asset.git_path or "").startswith("local://")
                },
            )

    def replace_apply_skill_sets(
        self,
        *,
        scope: BotCapabilityScope,
        sets: tuple[ResolvedSkillSetWrite, ...],
        remove_names: tuple[str, ...],
        default_change: DefaultExclusionChange,
        platform_default_codes: frozenset[str],
        mcp_catalog_entries: Mapping[str, Mapping[str, Any]],
    ) -> DesiredStateMutation:
        require_apply_scope(scope)
        names = [target.name for target in sets]
        if "default" in names or "default" in remove_names:
            raise SkillSetControlPlaneConflictError("SKILL_SET_NAME_RESERVED")
        if len(names) != len(set(names)) or set(names) & set(remove_names):
            raise SkillSetControlPlaneConflictError("SKILL_SET_NAME_CONFLICT")
        with self._db.transactional_orm_session() as session:
            old = self._snapshot(
                session, scope.bot_id, scope.owner_id, engine_type=scope.engine_type
            )
            rows = self._bot_sets(
                session,
                bot_id=scope.bot_id,
                owner_id=scope.owner_id,
                engine_type=scope.engine_type,
                default_engine_types=scope.default_engine_types,
                locked=True,
            )
            facts = self._apply_facts(session, rows)
            defaults = {item.id for item in facts if item.is_default}
            if any(
                key[0] not in defaults
                for keys in (
                    default_change.add_skills,
                    default_change.remove_skills,
                    default_change.add_mcps,
                    default_change.remove_mcps,
                )
                for key in keys
            ):
                raise SkillSetControlPlaneConflictError(
                    "DEFAULT_SKILL_SET_SCOPE_CHANGED"
                )
            skill_exclusions = (
                old.skill_exclusions | default_change.add_skills
            ) - default_change.remove_skills
            mcp_exclusions = (
                old.mcp_exclusions | default_change.add_mcps
            ) - default_change.remove_mcps
            ordinary = {str(row.name): row for row in rows if not row.is_default}
            if len(ordinary) != len([row for row in rows if not row.is_default]):
                raise SkillSetControlPlaneConflictError("SKILL_SET_NAME_CONFLICT")
            before_skills, before_mcps = self._apply_supply(
                session,
                scope,
                facts,
                old.skill_exclusions,
                old.mcp_exclusions,
                platform_default_codes,
                allow_unavailable=True,
            )
            # Unavailable members may no longer resolve. Only recorded Set
            # ownership confirms that their installations belong to this scope.
            active_set_ids = {item.id for item in facts if item.is_active}
            for skill_id, (kind, set_id) in old.installation_sources.items():
                if kind in (
                    SkillInstallationSourceKind.DEFAULT,
                    SkillInstallationSourceKind.SKILL_SET,
                ) and set_id in active_set_ids:
                    before_skills[skill_id] = (SkillInstallationSourceKind(kind), set_id)
            self._validate_default_restore(
                session,
                scope,
                facts,
                old,
                default_change,
                before_skills,
                before_mcps,
                platform_default_codes,
            )
            # Check current ownership before deleting omitted Sets: Replace is not a transfer.
            for target in sets:
                own_id = (
                    int(ordinary[target.name].id) if target.name in ordinary else None
                )
                for skill_id in target.skill_ids:
                    skill = (
                        self._scope(session.query(Skill), Skill)
                        .filter(Skill.id == skill_id)
                        .with_for_update()
                        .one_or_none()
                    )
                    if skill is None or (
                        str(skill.git_path or "").startswith("local://")
                        and (
                            skill.user_id != scope.owner_id
                            or skill.bolt_id != scope.bot_id
                        )
                    ):
                        raise SkillSetControlPlaneNotFoundError()
                    require_skill_online(skill)
                    center_membership_skill_uuid(skill)
                    references = self._skill_referencing_set_ids(
                        session, skill_id=skill_id, skill_uuid=skill.skill_uuid
                    )
                    conflict = any(
                        item.id != own_id
                        and item.id in references
                        and (
                            not item.is_default
                            or (item.id, skill_id) not in skill_exclusions
                        )
                        for item in facts
                    )
                    require_can_join_set(
                        is_directly_active=skill_id in old.installations
                        and skill_id not in before_skills,
                        is_in_another_set=conflict,
                    )
                for code in target.mcp_codes:
                    conflict = any(
                        item.id != own_id
                        and code in item.mcp_codes
                        and (
                            not item.is_default or (item.id, code) not in mcp_exclusions
                        )
                        for item in facts
                    )
                    policy = code in platform_default_codes and not any(
                        (set_id, code) in mcp_exclusions for set_id in defaults
                    )
                    if policy:
                        raise SkillSetControlPlaneConflictError(
                            "RESOURCE_MANAGED_BY_PLATFORM_POLICY"
                        )
                    require_can_join_set(
                        is_directly_active=code in old.mcp_installations
                        and code not in before_mcps,
                        is_in_another_set=conflict,
                    )
            # Internal target duplicates have no old row to expose an ownership collision.
            for attr in ("skill_ids", "mcp_codes"):
                members = [
                    member for target in sets for member in getattr(target, attr)
                ]
                if len(members) != len(set(members)):
                    raise SkillSetControlPlaneConflictError(
                        "RESOURCE_ALREADY_IN_ANOTHER_SKILL_SET"
                    )
            exclusion_changed, default_removed = self._apply_exclusion_changes(
                session, scope, default_change
            )
            created, updated, unchanged, removed = [], [], [], []
            for target in sets:
                row = ordinary.get(target.name)
                if row is None:
                    row = SkillSet(
                        name=target.name,
                        description=target.description,
                        user_id=scope.owner_id,
                        bolt_id=scope.bot_id,
                        engine_type=scope.engine_type,
                        is_default=False,
                        is_builtin=False,
                        is_active=target.is_active,
                        env=scope.env,
                        avernet_tenant=scope.tenant,
                    )
                    session.add(row)
                    session.flush()
                    created.append(target.name)
                    member_changed = True
                else:
                    fact = next(item for item in facts if item.id == row.id)
                    member_changed = set(fact.skill_ids) != set(
                        target.skill_ids
                    ) or set(fact.mcp_codes) != set(target.mcp_codes)
                    changed = (
                        member_changed
                        or row.description != target.description
                        or bool(row.is_active) != target.is_active
                    )
                    (updated if changed else unchanged).append(target.name)
                    if changed:
                        row.description, row.is_active = (
                            target.description,
                            target.is_active,
                        )
                if member_changed:
                    self._replace_apply_members(
                        session, scope, row, target, mcp_catalog_entries
                    )
            for name in remove_names:
                if name in ordinary:
                    session.delete(ordinary[name])
                    removed.append(name)
            session.flush()
            final_rows = self._bot_sets(
                session,
                bot_id=scope.bot_id,
                owner_id=scope.owner_id,
                engine_type=scope.engine_type,
                default_engine_types=scope.default_engine_types,
            )
            final = self._apply_facts(session, final_rows)
            active_skills, active_mcps = self._apply_supply(
                session,
                scope,
                final,
                skill_exclusions,
                mcp_exclusions,
                platform_default_codes,
            )
            self._require_unique_runtime_names(
                session,
                bot_id=scope.bot_id,
                owner_id=scope.owner_id,
                candidate_ids=set(active_skills),
                retired_ids=set(before_skills) - set(active_skills),
            )
            affected_mcps = set(before_mcps) | set(active_mcps)
            for skill_id in set(before_skills) | set(active_skills):
                skill = (
                    self._scope(session.query(Skill), Skill)
                    .filter(Skill.id == skill_id)
                    .one_or_none()
                )
                try:
                    affected_mcps.update(
                        skill_projection_mcp_dependency_codes(
                            session,
                            skill,
                            allow_unresolvable_center=skill_id not in active_skills,
                        )
                    )
                except (TypeError, ValueError):
                    if skill_id in active_skills:
                        raise
            default_skill_ids = {
                sid
                for sid, (kind, _) in active_skills.items()
                if kind is SkillInstallationSourceKind.DEFAULT
            }
            default_mcp_codes = {
                code
                for item in final
                if item.is_default and item.is_active
                for code in set(item.mcp_codes) | platform_default_codes
                if (item.id, code) not in mcp_exclusions
            }
            install_changed, default_install_changed = self._apply_installations(
                session,
                scope,
                before_skills,
                before_mcps,
                active_skills,
                active_mcps,
                default_skill_ids,
                default_mcp_codes,
            )
            return DesiredStateMutation(
                {},
                bool(
                    created
                    or updated
                    or removed
                    or exclusion_changed
                    or install_changed
                ),
                old,
                details=dict(
                    created=tuple(created),
                    updated=tuple(updated),
                    unchanged=tuple(unchanged),
                    removed=tuple(removed),
                    default_changed=exclusion_changed or default_install_changed,
                    default_removed=default_removed,
                ),
                mcp_codes=frozenset(affected_mcps),
            )

    def _apply_supply(
        self,
        session,
        scope,
        facts,
        skill_exclusions,
        mcp_exclusions,
        policy,
        *,
        allow_unavailable=False,
    ):
        skills, mcps = {}, set()
        for item in facts:
            if not item.is_active:
                continue
            excluded = (
                {member for set_id, member in skill_exclusions if set_id == item.id}
                if item.is_default
                else set()
            )
            # Closing a Default uses raw members and tolerates unavailable identities.
            if set(item.skill_ids) <= excluded:
                ids = set()
            else:
                try:
                    ids = set_member_skill_ids(
                        self._scope,
                        session,
                        skill_set_id=item.id,
                        excluded_raw_ids=frozenset(excluded),
                    )
                except SkillSetControlPlaneConflictError as error:
                    if (
                        not allow_unavailable
                        or str(error) != "CENTER_MEMBERSHIP_IDENTITY_MISSING"
                    ):
                        raise
                    # Raw aliases do not establish ownership of an installation.
                    ids = set()
                if not allow_unavailable:
                    resolved = {}
                    center_uuids = set()
                    for skill_id in sorted(ids):
                        skill = (
                            self._scope(session.query(Skill), Skill)
                            .filter(Skill.id == skill_id)
                            .with_for_update()
                            .one_or_none()
                        )
                        if skill is None:
                            raise SkillSetControlPlaneNotFoundError()
                        require_skill_online(skill)
                        uuid = center_membership_skill_uuid(skill)
                        if uuid:
                            center_uuids.add(uuid)
                        skill_projection_mcp_dependency_codes(session, skill)
                        resolved[skill_id] = skill
                    references = dict(item.skill_references)
                    for raw_id in set(item.skill_ids) - excluded:
                        raw = resolved.get(raw_id)
                        if raw is not None and not str(raw.git_path or "").startswith(
                            "center://"
                        ):
                            continue
                        if (
                            not references.get(raw_id)
                            or references[raw_id] not in center_uuids
                        ):
                            raise SkillSetControlPlaneNotFoundError()
            for skill_id in ids:
                skills[skill_id] = (
                    SkillInstallationSourceKind.DEFAULT
                    if item.is_default
                    else SkillInstallationSourceKind.SKILL_SET,
                    item.id,
                )
            mcps.update(
                code
                for code in item.mcp_codes
                if not item.is_default or (item.id, code) not in mcp_exclusions
            )
            if item.is_default:
                mcps.update(
                    code for code in policy if (item.id, code) not in mcp_exclusions
                )
        return skills, mcps

    def _apply_exclusion_changes(self, session, scope, change):
        changed, removed = False, False
        for attr, operation, member_key in (
            ("add_skills", default_exclusions.exclude_skill, "skill_id"),
            ("remove_skills", default_exclusions.unexclude_skill, "skill_id"),
            ("add_mcps", default_exclusions.exclude_mcp, "server_code"),
            ("remove_mcps", default_exclusions.unexclude_mcp, "server_code"),
        ):
            for set_id, member in sorted(getattr(change, attr)):
                wrote = operation(
                    session,
                    bot_id=scope.bot_id,
                    owner_id=scope.owner_id,
                    set_id=set_id,
                    **{member_key: member},
                )
                changed = changed or wrote
                removed = removed or (wrote and attr.startswith("add_"))
        return changed, removed

    def _replace_apply_members(self, session, scope, row, target, mcp_catalog_entries):
        self._scope(session.query(SkillSetSkill), SkillSetSkill).filter(
            SkillSetSkill.skill_set_id == row.id
        ).delete(synchronize_session=False)
        existing_mcps = {
            member.server_code: member
            for member in self._scope(
                session.query(SkillSetMCPServer), SkillSetMCPServer
            )
            .filter(SkillSetMCPServer.skill_set_id == row.id)
            .all()
        }
        for code, member in existing_mcps.items():
            if code not in target.mcp_codes:
                session.delete(member)
        for skill_id in sorted(set(target.skill_ids)):
            skill = (
                self._scope(session.query(Skill), Skill)
                .filter(Skill.id == skill_id)
                .one()
            )
            session.add(
                SkillSetSkill(
                    skill_set_id=row.id,
                    skill_id=skill_id,
                    skill_uuid=center_membership_skill_uuid(skill),
                    user_id=scope.owner_id,
                    env=scope.env,
                    avernet_tenant=scope.tenant,
                )
            )
        for code in sorted(set(target.mcp_codes) - existing_mcps.keys()):
            catalog = mcp_catalog_entries[code]
            session.add(
                SkillSetMCPServer(
                    skill_set_id=row.id,
                    server_code=code,
                    name=catalog["name"],
                    description=catalog.get("description"),
                    icon=catalog.get("icon"),
                    user_id=scope.owner_id,
                    env=scope.env,
                    avernet_tenant=scope.tenant,
                )
            )

    def _validate_default_restore(
        self, session, scope, facts, old, change, before_skills, before_mcps, policy
    ):
        for set_id, raw_id in change.remove_skills & old.skill_exclusions:
            fact = next(item for item in facts if item.id == set_id)
            if raw_id not in fact.skill_ids:
                continue  # Removing a stale exclusion does not restore a member.
            excluded = frozenset(
                member
                for current_set, member in old.skill_exclusions
                if current_set == set_id
            )
            before = set_member_skill_ids(
                self._scope,
                session,
                skill_set_id=set_id,
                excluded_raw_ids=excluded,
            )
            after = set_member_skill_ids(
                self._scope,
                session,
                skill_set_id=set_id,
                excluded_raw_ids=excluded - {raw_id},
            )
            for skill_id in sorted(after - before):
                skill = (
                    self._scope(session.query(Skill), Skill)
                    .filter(Skill.id == skill_id)
                    .with_for_update()
                    .one_or_none()
                )
                if skill is None:
                    raise SkillSetControlPlaneNotFoundError()
                references = self._skill_referencing_set_ids(
                    session, skill_id=skill_id, skill_uuid=skill.skill_uuid
                )
                ordinary = any(
                    not item.is_default and item.id in references for item in facts
                )
                require_can_join_set(
                    is_directly_active=skill_id in old.installations
                    and skill_id not in before_skills,
                    is_in_another_set=ordinary,
                )
                require_skill_online(skill)
                center_membership_skill_uuid(skill)
                skill_projection_mcp_dependency_codes(session, skill)
        for set_id, code in change.remove_mcps & old.mcp_exclusions:
            fact = next(item for item in facts if item.id == set_id)
            if code not in set(fact.mcp_codes) | set(policy):
                continue
            require_can_join_set(
                is_directly_active=code in old.mcp_installations
                and code not in before_mcps,
                is_in_another_set=any(
                    not item.is_default and code in item.mcp_codes for item in facts
                ),
            )

    def _apply_installations(
        self,
        session,
        scope,
        before_skills,
        before_mcps,
        active_skills,
        active_mcps,
        default_skill_ids,
        default_mcp_codes,
    ):
        changed, default_changed = False, False
        for skill_id, (kind, set_id) in active_skills.items():
            wrote = skill_installations.install(
                session,
                bot_id=scope.bot_id,
                owner_id=scope.owner_id,
                env=scope.env,
                skill_id=skill_id,
                source_kind=kind,
                source_set_id=set_id,
            )
            changed = changed or wrote
            default_changed = default_changed or (
                wrote and skill_id in default_skill_ids
            )
        changed = (
            bool(
                skill_installations.uninstall(
                    session,
                    bot_id=scope.bot_id,
                    owner_id=scope.owner_id,
                    env=scope.env,
                    skill_ids=set(before_skills) - set(active_skills),
                )
            )
            or changed
        )
        for code in active_mcps:
            wrote = mcp_installations.install(
                session,
                bot_id=scope.bot_id,
                owner_id=scope.owner_id,
                env=scope.env,
                server_code=code,
            )
            changed = changed or wrote
            default_changed = default_changed or (wrote and code in default_mcp_codes)
        changed = (
            bool(
                mcp_installations.uninstall(
                    session,
                    bot_id=scope.bot_id,
                    owner_id=scope.owner_id,
                    env=scope.env,
                    server_codes=set(before_mcps) - set(active_mcps),
                )
            )
            or changed
        )
        return changed, default_changed
