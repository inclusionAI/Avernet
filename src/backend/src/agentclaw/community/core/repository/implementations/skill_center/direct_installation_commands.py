"""Direct (Set-free) Installation UoW commands for the control plane.

The skill pair mirrors the MCP pair in ``mcp_skill_set_control_plane``; both
share the R1 fact-reading guard here: a capability that any of the Bot's
Sets holds — the Default included, excluded members included — refuses
direct control.
"""

from __future__ import annotations

from sqlalchemy import or_

from agentclaw.community.core.models.mcp import SkillSetMCPServer
from agentclaw.community.core.models.skill import (
    Skill,
    SkillSet,
    SkillSetSkill,
)
from agentclaw.community.core.repository.implementations.skill_center.skill_mcp_dependencies import (
    skill_mcp_dependency_codes,
)
from agentclaw.community.core.repository.implementations.skill_center.tables import (
    skill_installations,
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
from agentclaw.community.utils.env_utils import get_current_env


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
            old = self._snapshot(session, bot_id, owner_id, engine_type=engine_type)
            self._require_not_set_managed(
                session,
                set_ids=self._skill_referencing_set_ids(
                    session, skill_id=int(skill.id), skill_uuid=skill.skill_uuid
                ),
                bot_id=bot_id,
                owner_id=owner_id,
                engine_type=engine_type,
                default_engine_types=default_engine_types,
            )
            if int(skill.id) in old.installations:
                return DesiredStateMutation({}, False, old)
            self._require_unique_runtime_names(
                session, bot_id=bot_id, owner_id=owner_id,
                candidate_ids={int(skill.id)},
            )
            skill_installations.install(
                session,
                bot_id=bot_id,
                owner_id=owner_id,
                env=get_current_env(),
                skill_id=int(skill.id),
            )
            session.flush()
            # The Skill's MCP dependencies join the Bot's projected MCP set
            # along with the Skill, so the command cannot scope its projection
            # without them. Read under the lock this transaction already
            # holds, as ``add_skill`` does.
            return DesiredStateMutation(
                {}, True, old, mcp_codes=skill_mcp_dependency_codes(skill)
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
                .one_or_none()
            )
            self._require_not_set_managed(
                session,
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
                    skill_mcp_dependency_codes(skill) if changed else frozenset()
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

    def _require_not_set_managed(
        self,
        session,
        *,
        set_ids: set[int],
        bot_id: str,
        owner_id: str,
        engine_type: str | None,
        default_engine_types: tuple[str, ...] | None,
    ) -> None:
        """R1 for the direct commands: a capability any of the Bot's Sets
        holds — the Default included, excluded or not — refuses direct
        control. The ownership policy decides which referencing Sets are
        the Bot's."""
        if not set_ids:
            return
        rows = (
            self._scope(session.query(SkillSet), SkillSet)
            .filter(SkillSet.id.in_(sorted(set_ids)))
            .all()
        )
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
