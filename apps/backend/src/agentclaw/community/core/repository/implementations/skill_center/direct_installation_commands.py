"""Direct (Set-free) Installation UoW commands for the control plane.

The skill pair mirrors the MCP pair in ``mcp_skill_set_control_plane``; both
share the R1 fact-reading guard here: a capability that any of the Bot's
Sets holds — an unexcluded applicable Default included — refuses direct control.
"""

from __future__ import annotations

from sqlalchemy import or_

from agentclaw.community.core.models.mcp import SkillSetMCPServer
from agentclaw.community.core.models.skill import (
    Skill,
    SkillSetSkill,
)
from agentclaw.community.core.repository.implementations.skill_center.skill_mcp_dependencies import (
    skill_projection_mcp_dependency_codes,
)
from agentclaw.community.core.repository.implementations.skill_center.tables import (
    skill_installations,
    default_exclusions,
)
from agentclaw.community.core.repository.capability_desired_state_types import (
    DesiredStateMutation,
)
from agentclaw.community.core.skill_center.errors import (
    SkillSetControlPlaneConflictError,
    SkillSetControlPlaneNotFoundError,
)
from agentclaw.community.core.skill_center.policies.capability_ownership import (
    is_set_managed,
)
from agentclaw.community.core.skill_center.offline_policy import require_skill_online
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.core.models.skill_installation_source import (
    SkillInstallationSourceKind,
)


class DirectInstallationCommands:
    """Mixed into the control-plane repository; uses its ``_db``, ``_scope``,
    ``_snapshot`` and ``_require_unique_runtime_names``."""

    def install_skill(
        self,
        *,
        bot_id: str,
        owner_id: str,
        skill_id: str,
        engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
    ) -> DesiredStateMutation:
        """Write the direct Installation fact for one Skill (R1 under txn)."""
        with self._db.transactional_orm_session() as session:
            skill = (
                self._scope(session.query(Skill), Skill)
                .filter(Skill.id == int(skill_id))
                .with_for_update()
                .one_or_none()
            )
            if skill is None or (
                str(skill.git_path or "").startswith("local://")
                and skill.bolt_id != bot_id
            ):
                raise SkillSetControlPlaneNotFoundError()
            require_skill_online(skill)
            old = self._snapshot(session, bot_id, owner_id, engine_type=engine_type)
            self._require_not_set_managed(
                session,
                member=int(skill_id),
                set_ids=self._skill_referencing_set_ids(
                    session, skill_id=int(skill.id), skill_uuid=skill.skill_uuid
                ),
                bot_id=bot_id,
                owner_id=owner_id,
                engine_type=engine_type,
                default_engine_types=default_engine_types,
            )
            if int(skill.id) in old.installations:
                changed = skill_installations.install(
                    session,
                    bot_id=bot_id,
                    owner_id=owner_id,
                    env=get_current_env(),
                    skill_id=int(skill.id),
                    source_kind=SkillInstallationSourceKind.DIRECT,
                )
                return DesiredStateMutation({}, changed, old)
            self._require_unique_runtime_names(
                session,
                bot_id=bot_id,
                owner_id=owner_id,
                candidate_ids={int(skill.id)},
            )
            skill_installations.install(
                session,
                bot_id=bot_id,
                owner_id=owner_id,
                env=get_current_env(),
                skill_id=int(skill.id),
                source_kind=SkillInstallationSourceKind.DIRECT,
            )
            session.flush()
            # The Skill's MCP dependencies join the Bot's projected MCP set
            # along with the Skill, so the command cannot scope its projection
            # without them. Read under the lock this transaction already
            # holds, as ``add_skill`` does.
            return DesiredStateMutation(
                {},
                True,
                old,
                mcp_codes=skill_projection_mcp_dependency_codes(session, skill),
            )

    def uninstall_skill(
        self,
        *,
        bot_id: str,
        owner_id: str,
        skill_id: str,
        engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
    ) -> DesiredStateMutation:
        with self._db.transactional_orm_session() as session:
            old = self._snapshot(session, bot_id, owner_id, engine_type=engine_type)
            # The Skill row may be gone; membership rows can still name its id,
            # so the R1 read falls back to the id alone.
            skill = (
                self._scope(session.query(Skill), Skill)
                .filter(Skill.id == int(skill_id))
                .with_for_update()
                .one_or_none()
            )
            self._require_not_set_managed(
                session,
                member=int(skill_id),
                set_ids=self._skill_referencing_set_ids(
                    session,
                    skill_id=int(skill_id),
                    skill_uuid=skill.skill_uuid if skill is not None else None,
                ),
                bot_id=bot_id,
                owner_id=owner_id,
                engine_type=engine_type,
                default_engine_types=default_engine_types,
            )
            changed = (
                skill_installations.uninstall(
                    session,
                    bot_id=bot_id,
                    owner_id=owner_id,
                    env=get_current_env(),
                    skill_ids={int(skill_id)},
                )
                > 0
            )
            session.flush()
            # Candidates for release, not a verdict — the projector subtracts
            # the projected set, so a dependency something else still supplies
            # survives. ``skill`` may legitimately be gone here, in which case
            # there are no dependencies left to name.
            return DesiredStateMutation(
                {},
                changed,
                old,
                mcp_codes=(
                    skill_projection_mcp_dependency_codes(
                        session, skill, allow_unresolvable_center=True
                    )
                    if changed
                    else frozenset()
                ),
            )

    def _skill_referencing_set_ids(
        self, session, *, skill_id: int, skill_uuid: str | None
    ) -> set[int]:
        """The Sets holding a membership row for this Skill, id or uuid."""
        identity = [SkillSetSkill.skill_id == skill_id]
        if skill_uuid:
            identity.append(SkillSetSkill.skill_uuid == skill_uuid)
        return {
            int(value[0])
            for value in self._scope(
                session.query(SkillSetSkill.skill_set_id), SkillSetSkill
            )
            .filter(or_(*identity))
            .all()
        }

    def _mcp_referencing_set_ids(self, session, *, server_code: str) -> set[int]:
        """The Sets holding a membership row for this MCP server."""
        return {
            int(value[0])
            for value in self._scope(
                session.query(SkillSetMCPServer.skill_set_id), SkillSetMCPServer
            )
            .filter(SkillSetMCPServer.server_code == server_code)
            .all()
        }

    def _skill_control_member_keys(
        self, session, *, member: int, set_ids: set[int]
    ) -> dict[int, set[int]]:
        """Resolve matching associations to their raw exclusion keys."""
        skill = self._scope(session.query(Skill), Skill).filter(Skill.id == member).one_or_none()
        is_center = skill is not None and str(skill.git_path or "").startswith("center://")
        target_uuid = (str(skill.skill_uuid or "").strip() or None) if is_center else None
        # Uninstall also handles missing assets or legacy missing identities;
        # retain the existing conservative raw-ID guard in that case.
        missing_identity = skill is None or (is_center and not target_uuid)
        result: dict[int, set[int]] = {}
        for reference in self._scope(session.query(SkillSetSkill), SkillSetSkill).filter(
            SkillSetSkill.skill_set_id.in_(set_ids)
        ).all():
            uuid = str(reference.skill_uuid or "").strip()
            matches = (
                uuid == target_uuid
                if uuid and not missing_identity
                else int(reference.skill_id) == member
            )
            if matches:
                result.setdefault(int(reference.skill_set_id), set()).add(int(reference.skill_id))
        return result

    def _require_not_set_managed(
        self,
        session,
        *,
        set_ids: set[int],
        member: int | str,
        bot_id: str,
        owner_id: str,
        engine_type: str | None,
        default_engine_types: tuple[str, ...] | None,
    ) -> None:
        """R1 for the direct commands: a capability any of the Bot's Sets
        holds — unexcluded applicable Default included — refuses direct
        control. The ownership policy decides which referencing Sets are
        the Bot's."""
        if not set_ids:
            return
        rows = self._bot_sets(
            session,
            bot_id=bot_id,
            owner_id=owner_id,
            engine_type=engine_type,
            default_engine_types=default_engine_types,
        )
        rows = [row for row in rows if int(row.id) in set_ids]
        if isinstance(member, int):
            memberships = self._skill_control_member_keys(
                session, member=member, set_ids={int(row.id) for row in rows}
            )
            controlled = []
            for row in rows:
                raw_ids = memberships.get(int(row.id), set())
                if not raw_ids:
                    continue
                if row.is_default and all(
                    int(row.id) in default_exclusions.lock_skill_exclusions(
                        session, bot_id=bot_id, owner_id=owner_id, skill_id=raw_id
                    )
                    for raw_id in sorted(raw_ids)
                ):
                    continue
                controlled.append(row)
            rows = controlled
        else:
            rows = [
                row for row in rows
                if not (
                    row.is_default
                    and int(row.id) in default_exclusions.lock_mcp_exclusions(
                        session, bot_id=bot_id, owner_id=owner_id, server_code=member
                    )
                )
            ]
        referencing = [
            {
                "is_default": bool(row.is_default),
                "bolt_id": row.bolt_id,
                "user_id": row.user_id,
                "engine_type": row.engine_type,
            }
            for row in rows
        ]
        if is_set_managed(
            referencing_sets=referencing,
            bot_id=bot_id,
            owner_id=owner_id,
            engine_type=engine_type,
            default_engine_types=default_engine_types or (),
        ):
            raise SkillSetControlPlaneConflictError("RESOURCE_MANAGED_BY_SKILL_SET")
