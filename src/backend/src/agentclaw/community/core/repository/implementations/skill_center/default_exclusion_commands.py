"""Default-Set exclusion UoW commands for the control plane (spec E.11).

The skill pair lives here; the MCP twins live with the other MCP commands in
``mcp_skill_set_control_plane``. Both address a Default Set only and commit
the exclusion row together with the member's Installation delta.
"""

from __future__ import annotations

from agentclaw.community.core.models.skill import Skill, SkillSet
from agentclaw.community.core.repository.implementations.skill_center.bot_skillset_installations import (
    set_member_skill_ids,
)
from agentclaw.community.core.repository.implementations.skill_center.skill_mcp_dependencies import (
    skill_projection_mcp_dependency_codes,
)
from agentclaw.community.core.repository.implementations.skill_center.skill_set_projection import (
    skill_set_item as _item,
)
from agentclaw.community.core.repository.implementations.skill_center.tables import (
    default_exclusions,
    skill_installations,
)
from agentclaw.community.core.repository.capability_desired_state_types import (
    DesiredStateMutation,
)
from agentclaw.community.core.skill_center.errors import (
    SkillSetControlPlaneNotFoundError,
)
from agentclaw.community.core.skill_center.offline_policy import require_skill_online
from agentclaw.community.utils.env_utils import get_current_env


class DefaultExclusionCommands:
    """Mixed into the control-plane repository; uses its ``_db``, ``_scope``,
    ``_set``, ``_snapshot`` and ``_require_unique_runtime_names``."""

    def _skill_mcp_codes(
        self,
        session,
        skill_id: str,
        *,
        allow_unresolvable_center: bool = False,
    ) -> frozenset[str]:
        """The MCP dependencies the excluded/restored member carries.

        Exclusion is the Default Set's per-Bot deactivation, so it moves this
        member's MCP dependencies in or out of the projected set exactly as an
        ordinary add/remove does — and the command has to name them to scope
        its projection.
        """
        return skill_projection_mcp_dependency_codes(
            session,
            self._scope(session.query(Skill), Skill)
            .filter(Skill.id == int(skill_id))
            .with_for_update()
            .one_or_none(),
            allow_unresolvable_center=allow_unresolvable_center,
        )

    def exclude_default_skill(
        self,
        *,
        bot_id: str,
        owner_id: str,
        set_id: str,
        skill_id: str,
        engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
    ) -> DesiredStateMutation:
        """The Default Set's per-Bot deactivation of one member (spec E.11).

        Exclusion row + Installation delta commit together: the member's
        Installation row is the Set's claim (R1 forbids a direct one), so the
        exclusion retires it in the same transaction.
        """
        with self._db.transactional_orm_session() as session:
            row = self._default_set(
                session, bot_id=bot_id, owner_id=owner_id, set_id=set_id,
                engine_type=engine_type,
                default_engine_types=default_engine_types,
            )
            old = self._snapshot(session, bot_id, owner_id, engine_type=engine_type)
            # Only a member can be excluded: a stray id must not leave a
            # dangling row behind (it would pre-exclude the skill if the
            # platform ever adds it) nor report an immutable membership as
            # changed. ``_teardown_ids`` is deliberately wider than the
            # resolved members — an OFFLINE ``center://`` member still holds
            # an Installation row this command is the only way to retire.
            if (
                not str(skill_id).isdecimal()
                or int(skill_id) not in self._teardown_ids(session, {int(row.id)})
            ):
                return DesiredStateMutation(_item(row), False, old)
            created = default_exclusions.exclude_skill(
                session, bot_id=bot_id, owner_id=owner_id,
                set_id=int(row.id), skill_id=int(skill_id),
            )
            if not created:
                return DesiredStateMutation(_item(row), False, old)
            released = self._skill_mcp_codes(
                session, skill_id, allow_unresolvable_center=True
            )
            skill_installations.uninstall(
                session, bot_id=bot_id, owner_id=owner_id,
                env=get_current_env(), skill_ids={int(skill_id)},
            )
            session.flush()
            return DesiredStateMutation(
                _item(row), True, old, mcp_codes=released
            )

    def unexclude_default_skill(
        self,
        *,
        bot_id: str,
        owner_id: str,
        set_id: str,
        skill_id: str,
        engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
    ) -> DesiredStateMutation:
        """Remove the exclusion; the member's Installation row comes back with
        it — a Default Set is always active."""
        with self._db.transactional_orm_session() as session:
            row = self._default_set(
                session, bot_id=bot_id, owner_id=owner_id, set_id=set_id,
                engine_type=engine_type,
                default_engine_types=default_engine_types,
            )
            old = self._snapshot(session, bot_id, owner_id, engine_type=engine_type)
            if not str(skill_id).isdecimal():
                return DesiredStateMutation(_item(row), False, old)
            skill = (
                self._scope(session.query(Skill), Skill)
                .filter(Skill.id == int(skill_id))
                .with_for_update()
                .one_or_none()
            )
            if skill is None:
                raise SkillSetControlPlaneNotFoundError()
            require_skill_online(skill)
            removed = default_exclusions.unexclude_skill(
                session, bot_id=bot_id, owner_id=owner_id,
                set_id=int(row.id), skill_id=int(skill_id),
            )
            if not removed:
                return DesiredStateMutation(_item(row), False, old)
            claimed: frozenset[str] = frozenset()
            if int(skill_id) in set_member_skill_ids(
                self._scope, session, skill_set_id=int(row.id)
            ):
                self._require_unique_runtime_names(
                    session, bot_id=bot_id, owner_id=owner_id,
                    candidate_ids={int(skill_id)},
                )
                skill_installations.install(
                    session, bot_id=bot_id, owner_id=owner_id,
                    env=get_current_env(), skill_id=int(skill_id),
                )
                # Only a restored *member* re-enters the projection; lifting a
                # stale exclusion on a non-member claims nothing.
                claimed = self._skill_mcp_codes(session, skill_id)
            session.flush()
            return DesiredStateMutation(
                _item(row), True, old, mcp_codes=claimed
            )

    def excluded_default_skill_ids(
        self, *, bot_id: str, owner_id: str, set_id: str
    ) -> set[int]:
        """The owner's Skill exclusions from one Default Set."""
        with self._db.orm_session() as session:
            return default_exclusions.excluded_skill_ids(
                session, bot_id=bot_id, owner_id=owner_id, set_id=int(set_id)
            )

    def _default_set(
        self,
        session,
        *,
        bot_id: str,
        owner_id: str,
        set_id: str,
        engine_type: str | None,
        default_engine_types: tuple[str, ...] | None,
    ) -> SkillSet:
        """Resolve and lock the addressed Set; it must be a Default."""
        row = self._set(
            session, bot_id=bot_id, owner_id=owner_id, set_id=set_id,
            engine_type=engine_type, default_engine_types=default_engine_types,
            locked=True,
        )
        if not row.is_default:
            # The exclusion commands address Defaults only; an ordinary Set
            # reaching here is a routing error, not a state conflict.
            raise SkillSetControlPlaneNotFoundError()
        return row
