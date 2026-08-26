"""MCP-specific transactional commands for the SkillSet control plane."""

from __future__ import annotations

from agentclaw.community.core.models.mcp import (
    BotMCPInstallation,
    SkillSetMCPServer,
)
from agentclaw.community.core.models.skill import SkillSet
from agentclaw.community.core.repository.implementations.skill_center.default_skillset_projection import (
    excluded_mcp_codes,
)
from agentclaw.community.core.repository.skill_set_control_plane_types import (
    SkillSetMutation,
)
from agentclaw.community.core.skill_center.errors import (
    SkillSetControlPlaneConflictError,
)
from agentclaw.community.core.skill_center.orm import DefaultSkillsetMcpExclusion
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
        name: str, description: str | None, icon: str | None,
        engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
    ) -> SkillSetMutation:
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
                return SkillSetMutation(self._as_item(row), False, old)
            if server_code in old.mcp_installations:
                raise SkillSetControlPlaneConflictError("RESOURCE_DIRECT_ACTIVE")
            owner = (
                self._scope(session.query(SkillSet), SkillSet)
                .join(SkillSetMCPServer, SkillSetMCPServer.skill_set_id == SkillSet.id)
                .filter(
                    SkillSet.bolt_id == bot_id,
                    SkillSet.user_id == owner_id,
                    SkillSet.is_default.is_(False),
                    SkillSetMCPServer.server_code == server_code,
                )
                .first()
            )
            if owner is not None:
                raise SkillSetControlPlaneConflictError("RESOURCE_ALREADY_IN_ANOTHER_SKILL_SET")
            session.add(SkillSetMCPServer(
                skill_set_id=row.id, server_code=server_code, name=name,
                description=description, icon=icon,
                user_id=row.user_id, env=get_current_env(),
                avernet_tenant=get_current_avernet_tenant(),
            ))
            if row.is_active:
                session.add(BotMCPInstallation(
                    bot_id=bot_id, owner_id=owner_id, server_code=server_code, env=get_current_env(),
                    avernet_tenant=get_current_avernet_tenant(),
                ))
            session.flush()
            return SkillSetMutation(self._as_item(row), True, old)

    def remove_mcp(
        self, *, bot_id: str, owner_id: str, set_id: str, server_code: str,
        engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
    ) -> SkillSetMutation:
        with self._db.transactional_orm_session() as session:
            row = self._set(session, bot_id=bot_id, owner_id=owner_id, set_id=set_id, engine_type=engine_type, default_engine_types=default_engine_types, locked=True)
            old = self._snapshot(session, bot_id, owner_id, engine_type=engine_type)
            if row.is_default:
                existing = (
                    session.query(DefaultSkillsetMcpExclusion)
                    .filter(
                        DefaultSkillsetMcpExclusion.avernet_tenant
                        == get_current_avernet_tenant(),
                        DefaultSkillsetMcpExclusion.user_id == owner_id,
                        DefaultSkillsetMcpExclusion.bot_id == bot_id,
                        DefaultSkillsetMcpExclusion.skill_set_id == int(row.id),
                        DefaultSkillsetMcpExclusion.server_code == server_code,
                    )
                    .first()
                )
                if existing is not None:
                    return SkillSetMutation(self._as_item(row), False, old)
                session.add(
                    DefaultSkillsetMcpExclusion(
                        user_id=owner_id,
                        bot_id=bot_id,
                        skill_set_id=int(row.id),
                        server_code=server_code,
                        avernet_tenant=get_current_avernet_tenant(),
                    )
                )
                session.flush()
                return SkillSetMutation(self._as_item(row), True, old)
            self._ordinary(row)
            membership = (
                self._scope(session.query(SkillSetMCPServer), SkillSetMCPServer)
                .filter(SkillSetMCPServer.skill_set_id == row.id, SkillSetMCPServer.server_code == server_code)
                .first()
            )
            if membership is None:
                return SkillSetMutation(self._as_item(row), False, old)
            session.delete(membership)
            if row.is_active:
                self._scope(session.query(BotMCPInstallation), BotMCPInstallation).filter(
                    BotMCPInstallation.bot_id == bot_id,
                    BotMCPInstallation.owner_id == owner_id,
                    BotMCPInstallation.server_code == server_code,
                ).delete(synchronize_session=False)
            session.flush()
            return SkillSetMutation(self._as_item(row), True, old)

    def activate_mcp_direct(self, *, bot_id: str, owner_id: str, server_code: str, engine_type: str | None = None) -> SkillSetMutation:
        with self._db.transactional_orm_session() as session:
            old = self._snapshot(session, bot_id, owner_id, engine_type=engine_type)
            if self._mcp_has_ordinary_membership(
                session, bot_id, owner_id, server_code
            ):
                raise SkillSetControlPlaneConflictError("RESOURCE_MANAGED_BY_SKILL_SET")
            if server_code in old.mcp_installations:
                return SkillSetMutation({}, False, old)
            session.add(BotMCPInstallation(
                bot_id=bot_id, owner_id=owner_id, server_code=server_code, env=get_current_env(),
                avernet_tenant=get_current_avernet_tenant(),
            ))
            session.flush()
            return SkillSetMutation({}, True, old)

    def deactivate_mcp_direct(self, *, bot_id: str, owner_id: str, server_code: str, engine_type: str | None = None) -> SkillSetMutation:
        with self._db.transactional_orm_session() as session:
            old = self._snapshot(session, bot_id, owner_id, engine_type=engine_type)
            if self._mcp_has_ordinary_membership(
                session, bot_id, owner_id, server_code
            ):
                raise SkillSetControlPlaneConflictError("RESOURCE_MANAGED_BY_SKILL_SET")
            changed = self._scope(session.query(BotMCPInstallation), BotMCPInstallation).filter(
                BotMCPInstallation.bot_id == bot_id,
                BotMCPInstallation.owner_id == owner_id,
                BotMCPInstallation.server_code == server_code,
            ).delete(synchronize_session=False) > 0
            session.flush()
            return SkillSetMutation({}, changed, old)

    def list_installed_mcps(self, *, bot_id: str, owner_id: str, engine_type: str | None = None) -> set[str]:
        with self._db.orm_session() as session:
            return self._mcp_installations(session, bot_id, owner_id)
