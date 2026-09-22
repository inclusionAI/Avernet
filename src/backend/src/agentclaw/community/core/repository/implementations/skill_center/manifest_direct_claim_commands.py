"""Manifest-only capability source transitions.

Ordinary product commands deliberately reject Set- or platform-managed
capabilities.  Manifest Apply has a different contract: an explicit entry is a
Bot-scoped Direct claim, so the conflicting source facts must be converted in
the same desired-state transaction.  These commands are intentionally narrow;
they are not used by the UI or ordinary OpenAPI activation routes.
"""

from __future__ import annotations

from sqlalchemy import or_

from agentclaw.community.core.models.mcp import SkillSetMCPServer
from agentclaw.community.core.models.skill import Skill, SkillSetSkill
from agentclaw.community.core.repository.capability_desired_state_types import (
    DesiredStateMutation,
)
from agentclaw.community.core.repository.implementations.skill_center.skill_mcp_dependencies import (
    skill_projection_mcp_dependency_codes,
)
from agentclaw.community.core.repository.implementations.skill_center.tables import (
    bot_mcp_configs,
    default_exclusions,
    mcp_installations,
    skill_installations,
)
from agentclaw.community.core.skill_center.errors import (
    SkillSetControlPlaneNotFoundError,
)
from agentclaw.community.core.skill_center.offline_policy import require_skill_online
from agentclaw.community.utils.env_utils import get_current_env


class ManifestDirectClaimCommands:
    """Mixed into the desired-state UoW; uses its scoped query helpers."""

    def manifest_direct_mcp_exists(
        self,
        *,
        bot_id: str,
        owner_id: str,
        server_code: str,
        platform_default_codes: frozenset[str],
        engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
    ) -> bool:
        with self._db.orm_session() as session:
            return self._manifest_direct_mcp_in_session(
                session,
                bot_id=bot_id,
                owner_id=owner_id,
                server_code=server_code,
                platform_default_codes=platform_default_codes,
                engine_type=engine_type,
                default_engine_types=default_engine_types,
            )

    def _manifest_direct_mcp_in_session(
        self,
        session,
        *,
        bot_id: str,
        owner_id: str,
        server_code: str,
        platform_default_codes: frozenset[str],
        engine_type: str | None,
        default_engine_types: tuple[str, ...] | None,
    ) -> bool:
        installed = server_code in self._mcp_installations(
            session, bot_id, owner_id
        )
        if not installed:
            return False
        sets = self._bot_sets(
            session,
            bot_id=bot_id,
            owner_id=owner_id,
            engine_type=engine_type,
            default_engine_types=default_engine_types,
        )
        defaults = [row for row in sets if row.is_default]
        if not defaults:
            return False
        default_ids = {int(row.id) for row in defaults}
        member_default_ids = {
            int(value[0])
            for value in self._scope(
                session.query(SkillSetMCPServer.skill_set_id), SkillSetMCPServer
            )
            .filter(
                SkillSetMCPServer.skill_set_id.in_(default_ids),
                SkillSetMCPServer.server_code == server_code,
            )
            .all()
        }
        relevant = set(default_ids) if server_code in platform_default_codes else member_default_ids
        return bool(relevant) and all(
            server_code
            in default_exclusions.excluded_mcp_codes(
                session, bot_id=bot_id, owner_id=owner_id, set_id=set_id
            )
            for set_id in relevant
        )

    def claim_manifest_mcp(
        self,
        *,
        bot_id: str,
        owner_id: str,
        server_code: str,
        config: dict | None,
        platform_default_codes: frozenset[str],
        engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
    ) -> DesiredStateMutation:
        """Convert every reachable explicit source into one Direct MCP claim."""
        with self._db.transactional_orm_session() as session:
            old = self._snapshot(session, bot_id, owner_id, engine_type=engine_type)
            sets = self._bot_sets(
                session,
                bot_id=bot_id,
                owner_id=owner_id,
                engine_type=engine_type,
                default_engine_types=default_engine_types,
            )
            if server_code in platform_default_codes and not any(
                row.is_default for row in sets
            ):
                raise SkillSetControlPlaneNotFoundError()
            memberships = (
                self._scope(session.query(SkillSetMCPServer), SkillSetMCPServer)
                .filter(
                    SkillSetMCPServer.skill_set_id.in_([int(row.id) for row in sets]),
                    SkillSetMCPServer.server_code == server_code,
                )
                .with_for_update()
                .all()
                if sets
                else []
            )
            by_set = {int(item.skill_set_id): item for item in memberships}
            changed = False
            for row in sets:
                membership = by_set.get(int(row.id))
                if row.is_default:
                    if membership is not None or server_code in platform_default_codes:
                        changed = default_exclusions.exclude_mcp(
                            session,
                            bot_id=bot_id,
                            owner_id=owner_id,
                            set_id=int(row.id),
                            server_code=server_code,
                        ) or changed
                    continue
                if membership is not None:
                    session.delete(membership)
                    changed = True

            installed = mcp_installations.install(
                session,
                bot_id=bot_id,
                owner_id=owner_id,
                env=get_current_env(),
                server_code=server_code,
            )
            override_changed = bot_mcp_configs.replace(
                session,
                bot_id=bot_id,
                owner_id=owner_id,
                env=get_current_env(),
                server_code=server_code,
                config=config,
            )
            session.flush()
            source_changed = changed
            return DesiredStateMutation(
                {},
                source_changed or installed or override_changed,
                old,
                mcp_codes=(
                    frozenset({server_code})
                    if installed or source_changed
                    else frozenset()
                ),
                updated_mcp_codes=(
                    frozenset({server_code})
                    if override_changed and not installed
                    else frozenset()
                ),
            )

    def remove_manifest_mcp(
        self,
        *,
        bot_id: str,
        owner_id: str,
        server_code: str,
        platform_default_codes: frozenset[str],
        engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
    ) -> DesiredStateMutation:
        """Remove explicit supply while retaining any Default exclusion."""
        with self._db.transactional_orm_session() as session:
            old = self._snapshot(session, bot_id, owner_id, engine_type=engine_type)
            sets = self._bot_sets(
                session,
                bot_id=bot_id,
                owner_id=owner_id,
                engine_type=engine_type,
                default_engine_types=default_engine_types,
            )
            if server_code in platform_default_codes and not any(
                row.is_default for row in sets
            ):
                raise SkillSetControlPlaneNotFoundError()
            memberships = (
                self._scope(session.query(SkillSetMCPServer), SkillSetMCPServer)
                .filter(
                    SkillSetMCPServer.skill_set_id.in_([int(row.id) for row in sets]),
                    SkillSetMCPServer.server_code == server_code,
                )
                .with_for_update()
                .all()
                if sets
                else []
            )
            by_set = {int(item.skill_set_id): item for item in memberships}
            changed = False
            for row in sets:
                membership = by_set.get(int(row.id))
                if row.is_default:
                    if membership is not None or server_code in platform_default_codes:
                        changed = default_exclusions.exclude_mcp(
                            session,
                            bot_id=bot_id,
                            owner_id=owner_id,
                            set_id=int(row.id),
                            server_code=server_code,
                        ) or changed
                    continue
                if row.is_active and membership is not None:
                    session.delete(membership)
                    changed = True

            removed = mcp_installations.uninstall(
                session,
                bot_id=bot_id,
                owner_id=owner_id,
                env=get_current_env(),
                server_codes={server_code},
            ) > 0
            override_removed = bot_mcp_configs.delete(
                session,
                bot_id=bot_id,
                owner_id=owner_id,
                env=get_current_env(),
                server_codes={server_code},
            ) > 0
            session.flush()
            return DesiredStateMutation(
                {},
                changed or removed or override_removed,
                old,
                mcp_codes=(
                    frozenset({server_code}) if changed or removed else frozenset()
                ),
                updated_mcp_codes=(
                    frozenset({server_code}) if override_removed else frozenset()
                ),
            )

    def claim_manifest_skill(
        self,
        *,
        bot_id: str,
        owner_id: str,
        skill_id: str,
        engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
    ) -> DesiredStateMutation:
        """Replace same-runtime-name Set/Default supply with a Direct Skill."""
        with self._db.transactional_orm_session() as session:
            target = (
                self._scope(session.query(Skill), Skill)
                .filter(Skill.id == int(skill_id))
                .with_for_update()
                .one_or_none()
            )
            if target is None or not (
                str(target.git_path or "").startswith("local://")
                and target.bolt_id == bot_id
                and str(target.user_id) == owner_id
            ):
                raise SkillSetControlPlaneNotFoundError()
            require_skill_online(target)
            old = self._snapshot(session, bot_id, owner_id, engine_type=engine_type)
            same_name = (
                self._scope(session.query(Skill), Skill)
                .filter(Skill.name == target.name)
                .with_for_update()
                .all()
            )
            candidate_ids = {int(skill.id) for skill in same_name}
            candidate_uuids = {
                str(skill.skill_uuid) for skill in same_name if skill.skill_uuid
            }
            sets = self._bot_sets(
                session,
                bot_id=bot_id,
                owner_id=owner_id,
                engine_type=engine_type,
                default_engine_types=default_engine_types,
            )
            identity = [SkillSetSkill.skill_id.in_(candidate_ids)]
            if candidate_uuids:
                identity.append(SkillSetSkill.skill_uuid.in_(candidate_uuids))
            memberships = (
                self._scope(session.query(SkillSetSkill), SkillSetSkill)
                .filter(
                    SkillSetSkill.skill_set_id.in_([int(row.id) for row in sets]),
                    or_(*identity),
                )
                .with_for_update()
                .all()
                if sets
                else []
            )
            set_by_id = {int(row.id): row for row in sets}
            changed = False
            for membership in memberships:
                row = set_by_id[int(membership.skill_set_id)]
                if row.is_default:
                    changed = default_exclusions.exclude_skill(
                        session,
                        bot_id=bot_id,
                        owner_id=owner_id,
                        set_id=int(row.id),
                        skill_id=int(membership.skill_id),
                    ) or changed
                else:
                    session.delete(membership)
                    changed = True

            removed = skill_installations.uninstall(
                session,
                bot_id=bot_id,
                owner_id=owner_id,
                env=get_current_env(),
                skill_ids=candidate_ids - {int(target.id)},
            ) > 0
            installed = skill_installations.install(
                session,
                bot_id=bot_id,
                owner_id=owner_id,
                env=get_current_env(),
                skill_id=int(target.id),
            )
            session.flush()
            dependencies: set[str] = set()
            for skill in same_name:
                dependencies.update(
                    skill_projection_mcp_dependency_codes(
                        session, skill, allow_unresolvable_center=True
                    )
                )
            return DesiredStateMutation(
                {},
                changed or removed or installed,
                old,
                mcp_codes=frozenset(dependencies),
            )

    def remove_manifest_skill(
        self,
        *,
        bot_id: str,
        owner_id: str,
        skill_id: str,
        remove_inactive_memberships: bool,
        engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
    ) -> DesiredStateMutation:
        """Remove one effective Skill; Local cleanup may detach dormant refs."""
        with self._db.transactional_orm_session() as session:
            skill = (
                self._scope(session.query(Skill), Skill)
                .filter(Skill.id == int(skill_id))
                .with_for_update()
                .one_or_none()
            )
            old = self._snapshot(session, bot_id, owner_id, engine_type=engine_type)
            if skill is None:
                return DesiredStateMutation({}, False, old)
            sets = self._bot_sets(
                session,
                bot_id=bot_id,
                owner_id=owner_id,
                engine_type=engine_type,
                default_engine_types=default_engine_types,
            )
            identity = [SkillSetSkill.skill_id == int(skill.id)]
            if skill.skill_uuid:
                identity.append(SkillSetSkill.skill_uuid == skill.skill_uuid)
            memberships = (
                self._scope(session.query(SkillSetSkill), SkillSetSkill)
                .filter(
                    SkillSetSkill.skill_set_id.in_([int(row.id) for row in sets]),
                    or_(*identity),
                )
                .with_for_update()
                .all()
                if sets
                else []
            )
            set_by_id = {int(row.id): row for row in sets}
            changed = False
            for membership in memberships:
                row = set_by_id[int(membership.skill_set_id)]
                if row.is_default:
                    changed = default_exclusions.exclude_skill(
                        session,
                        bot_id=bot_id,
                        owner_id=owner_id,
                        set_id=int(row.id),
                        skill_id=int(skill.id),
                    ) or changed
                elif row.is_active or remove_inactive_memberships:
                    session.delete(membership)
                    changed = True
            removed = skill_installations.uninstall(
                session,
                bot_id=bot_id,
                owner_id=owner_id,
                env=get_current_env(),
                skill_ids={int(skill.id)},
            ) > 0
            dependencies = skill_projection_mcp_dependency_codes(
                session, skill, allow_unresolvable_center=True
            )
            session.flush()
            return DesiredStateMutation(
                {}, changed or removed, old,
                mcp_codes=dependencies if changed or removed else frozenset(),
            )


__all__ = ["ManifestDirectClaimCommands"]
