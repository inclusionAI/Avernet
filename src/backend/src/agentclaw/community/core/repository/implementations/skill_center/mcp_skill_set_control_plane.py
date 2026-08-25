"""MCP-specific transactional commands for the SkillSet control plane."""

from __future__ import annotations

from agentclaw.community.core.models.mcp import SkillSetMCPServer
from agentclaw.community.core.repository.implementations.skill_center.bot_skillset_installations import (
    set_member_mcp_codes,
)
from agentclaw.community.core.repository.implementations.skill_center.tables import (
    default_exclusions,
    mcp_installations,
)
from agentclaw.community.core.repository.implementations.skill_center.tables.default_exclusions import (
    excluded_mcp_codes,
)
from agentclaw.community.core.repository.capability_desired_state_types import (
    DesiredStateMutation,
)
from agentclaw.community.core.skill_center.policies.capability_ownership import (
    require_can_join_set,
)
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env


class McpSkillSetControlPlaneCommands:
    """Cohesive MCP Membership and Direct-Installation UoW commands.

    The host repository supplies the shared Bot/tenant scope, SkillSet lookup,
    snapshot, and transaction boundary helpers.  Keeping MCP operations here
    prevents the SkillSet repository from growing into a mixed resource store.
    """

    def list_mcps(
        self,
        *,
        bot_id: str,
        owner_id: str,
        set_id: str,
        engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
    ) -> list[dict]:
        with self._db.orm_session() as session:
            row = self._set(
                session, bot_id=bot_id, owner_id=owner_id, set_id=set_id,
                engine_type=engine_type,
                default_engine_types=default_engine_types,
            )
            rows = (
                self._scope(session.query(SkillSetMCPServer), SkillSetMCPServer)
                .filter(SkillSetMCPServer.skill_set_id == row.id)
                .order_by(SkillSetMCPServer.server_code)
                .all()
            )
            if row.is_default:
                excluded = excluded_mcp_codes(
                    session, bot_id=bot_id, owner_id=owner_id, set_id=int(row.id)
                )
                rows = [item for item in rows if item.server_code not in excluded]
            return [
                {"id": str(item.id), "server_code": item.server_code, "name": item.name,
                 "description": item.description, "icon": item.icon}
                for item in rows
            ]

    def add_mcp(
        self, *, bot_id: str, owner_id: str, set_id: str, server_code: str,
        engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
    ) -> DesiredStateMutation:
        with self._db.transactional_orm_session() as session:
            row = self._set(session, bot_id=bot_id, owner_id=owner_id, set_id=set_id, engine_type=engine_type, default_engine_types=default_engine_types, locked=True)
            self._ordinary(row)
            old = self._snapshot(session, bot_id, owner_id, engine_type=engine_type)
            current = (
                self._scope(session.query(SkillSetMCPServer), SkillSetMCPServer)
                .filter(SkillSetMCPServer.skill_set_id == row.id, SkillSetMCPServer.server_code == server_code)
                .first()
            )
            if current is not None:
                return DesiredStateMutation(self._as_item(row), False, old)
            # R3 covers ANY of the Bot's Sets — the Default included, its
            # members excluded or not.
            reachable_ids = {
                int(candidate.id)
                for candidate in self._bot_sets(
                    session,
                    bot_id=bot_id,
                    owner_id=owner_id,
                    engine_type=engine_type,
                    default_engine_types=default_engine_types,
                )
            } - {int(row.id)}
            membership = (
                self._scope(session.query(SkillSetMCPServer), SkillSetMCPServer)
                .filter(
                    SkillSetMCPServer.skill_set_id.in_(reachable_ids),
                    SkillSetMCPServer.server_code == server_code,
                )
                .first()
                if reachable_ids
                else None
            )
            require_can_join_set(
                is_directly_active=server_code in old.mcp_installations,
                is_in_another_set=membership is not None,
            )
            session.add(SkillSetMCPServer(
                skill_set_id=row.id, server_code=server_code, name=server_code,
                user_id=row.user_id, env=get_current_env(),
                avernet_tenant=get_current_avernet_tenant(),
            ))
            if row.is_active:
                mcp_installations.install(
                    session, bot_id=bot_id, owner_id=owner_id,
                    env=get_current_env(), server_code=server_code,
                )
            session.flush()
            return DesiredStateMutation(self._as_item(row), True, old)

    def remove_mcp(
        self, *, bot_id: str, owner_id: str, set_id: str, server_code: str,
        engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
    ) -> DesiredStateMutation:
        with self._db.transactional_orm_session() as session:
            row = self._set(session, bot_id=bot_id, owner_id=owner_id, set_id=set_id, engine_type=engine_type, default_engine_types=default_engine_types, locked=True)
            self._ordinary(row)
            old = self._snapshot(session, bot_id, owner_id, engine_type=engine_type)
            membership = (
                self._scope(session.query(SkillSetMCPServer), SkillSetMCPServer)
                .filter(SkillSetMCPServer.skill_set_id == row.id, SkillSetMCPServer.server_code == server_code)
                .first()
            )
            if membership is None:
                return DesiredStateMutation(self._as_item(row), False, old)
            session.delete(membership)
            if row.is_active:
                mcp_installations.uninstall(
                    session, bot_id=bot_id, owner_id=owner_id,
                    env=get_current_env(), server_codes={server_code},
                )
            session.flush()
            return DesiredStateMutation(self._as_item(row), True, old)

    def exclude_default_mcp(
        self, *, bot_id: str, owner_id: str, set_id: str, server_code: str,
        engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
    ) -> DesiredStateMutation:
        """The MCP twin of ``exclude_default_skill``: exclusion row +
        Installation delta in one transaction."""
        with self._db.transactional_orm_session() as session:
            row = self._default_set(
                session, bot_id=bot_id, owner_id=owner_id, set_id=set_id,
                engine_type=engine_type,
                default_engine_types=default_engine_types,
            )
            old = self._snapshot(session, bot_id, owner_id, engine_type=engine_type)
            created = default_exclusions.exclude_mcp(
                session, bot_id=bot_id, owner_id=owner_id,
                set_id=int(row.id), server_code=server_code,
            )
            if not created:
                return DesiredStateMutation(self._as_item(row), False, old)
            if server_code in set_member_mcp_codes(
                self._scope, session, skill_set_id=int(row.id)
            ):
                mcp_installations.uninstall(
                    session, bot_id=bot_id, owner_id=owner_id,
                    env=get_current_env(), server_codes={server_code},
                )
            session.flush()
            return DesiredStateMutation(self._as_item(row), True, old)

    def unexclude_default_mcp(
        self, *, bot_id: str, owner_id: str, set_id: str, server_code: str,
        engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
    ) -> DesiredStateMutation:
        with self._db.transactional_orm_session() as session:
            row = self._default_set(
                session, bot_id=bot_id, owner_id=owner_id, set_id=set_id,
                engine_type=engine_type,
                default_engine_types=default_engine_types,
            )
            old = self._snapshot(session, bot_id, owner_id, engine_type=engine_type)
            removed = default_exclusions.unexclude_mcp(
                session, bot_id=bot_id, owner_id=owner_id,
                set_id=int(row.id), server_code=server_code,
            )
            if not removed:
                return DesiredStateMutation(self._as_item(row), False, old)
            if server_code in set_member_mcp_codes(
                self._scope, session, skill_set_id=int(row.id)
            ):
                mcp_installations.install(
                    session, bot_id=bot_id, owner_id=owner_id,
                    env=get_current_env(), server_code=server_code,
                )
            session.flush()
            return DesiredStateMutation(self._as_item(row), True, old)

    def excluded_default_mcp_codes(
        self, *, bot_id: str, owner_id: str, set_id: str
    ) -> set[str]:
        """The owner's MCP exclusions from one Default Set."""
        with self._db.orm_session() as session:
            return excluded_mcp_codes(
                session, bot_id=bot_id, owner_id=owner_id, set_id=int(set_id)
            )

    def install_mcp(
        self, *, bot_id: str, owner_id: str, server_code: str,
        engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
    ) -> DesiredStateMutation:
        with self._db.transactional_orm_session() as session:
            old = self._snapshot(session, bot_id, owner_id, engine_type=engine_type)
            self._require_not_set_managed(
                session,
                set_ids=self._mcp_referencing_set_ids(
                    session, server_code=server_code
                ),
                bot_id=bot_id,
                owner_id=owner_id,
                engine_type=engine_type,
                default_engine_types=default_engine_types,
            )
            if server_code in old.mcp_installations:
                return DesiredStateMutation({}, False, old)
            mcp_installations.install(
                session, bot_id=bot_id, owner_id=owner_id,
                env=get_current_env(), server_code=server_code,
            )
            session.flush()
            return DesiredStateMutation({}, True, old)

    def uninstall_mcp(
        self, *, bot_id: str, owner_id: str, server_code: str,
        engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
    ) -> DesiredStateMutation:
        with self._db.transactional_orm_session() as session:
            old = self._snapshot(session, bot_id, owner_id, engine_type=engine_type)
            self._require_not_set_managed(
                session,
                set_ids=self._mcp_referencing_set_ids(
                    session, server_code=server_code
                ),
                bot_id=bot_id,
                owner_id=owner_id,
                engine_type=engine_type,
                default_engine_types=default_engine_types,
            )
            changed = mcp_installations.uninstall(
                session, bot_id=bot_id, owner_id=owner_id,
                env=get_current_env(), server_codes={server_code},
            ) > 0
            session.flush()
            return DesiredStateMutation({}, changed, old)

    def list_installed_mcps(self, *, bot_id: str, owner_id: str, engine_type: str | None = None) -> set[str]:
        with self._db.orm_session() as session:
            return self._mcp_installations(session, bot_id, owner_id)
