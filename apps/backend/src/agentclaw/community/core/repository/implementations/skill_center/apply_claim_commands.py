"""The Direct claims an apply makes, for every compiler (the Manifest's and the genome's).

An apply speaks only for the bot's Direct skills and MCP servers: what the
bot's Sets supply — an active ordinary Set, the Default Set less its
exclusions, the platform's default MCP servers less theirs — is outside it.
A claim never reads or writes a Set: it refuses an item a Set supplies
(``SkillSetControlPlaneConflictError``), so Direct and Set supply never share
an Installation row, and a removal only drops Direct supply. The apply checks
the same supply before writing anything (``set_supplied_capabilities``); the
refusal here is the same rule under the transaction's lock. These commands are
intentionally narrow; they are not used by the UI or ordinary OpenAPI
activation routes.
"""

from __future__ import annotations

from agentclaw.community.core.models.mcp import SkillSetMCPServer
from agentclaw.community.core.models.skill import Skill
from agentclaw.community.core.repository.capability_desired_state_types import (
    DesiredStateMutation,
)
from agentclaw.community.core.repository.implementations.skill_center.skill_mcp_dependencies import (
    skill_projection_mcp_dependency_codes,
)
from agentclaw.community.core.repository.implementations.skill_center.bot_skillset_installations import (
    set_member_mcp_codes,
    set_member_skill_ids,
)
from agentclaw.community.core.repository.implementations.skill_center.tables import (
    bot_mcp_configs,
    default_exclusions,
    mcp_installations,
    skill_installations,
)
from agentclaw.community.core.skill_center.errors import (
    SkillSetControlPlaneConflictError,
    SkillSetControlPlaneNotFoundError,
)
from agentclaw.community.core.skill_center.offline_policy import require_skill_online
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.core.models.skill_installation_source import SkillInstallationSourceKind


#: Locators of the shared assets an apply may claim Direct, besides the bot's own ``local://`` packages.
_SHARED_LOCATORS = ("center://", "git://")


class ApplyClaimCommands:
    """Mixed into the desired-state UoW; uses its scoped query helpers."""

    def set_supplied_capabilities(
        self,
        *,
        bot_id: str,
        owner_id: str,
        platform_default_codes: frozenset[str],
        engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
    ) -> tuple[frozenset[str], frozenset[str]]:
        """The skill runtime names and MCP server codes the bot's Sets supply now: outside any apply."""
        with self._db.orm_session() as session:
            return self._set_supply(
                session,
                bot_id=bot_id,
                owner_id=owner_id,
                platform_default_codes=platform_default_codes,
                engine_type=engine_type,
                default_engine_types=default_engine_types,
            )

    def _set_supply(
        self,
        session,
        *,
        bot_id: str,
        owner_id: str,
        platform_default_codes: frozenset[str],
        engine_type: str | None,
        default_engine_types: tuple[str, ...] | None,
        locked: bool = False,
    ) -> tuple[frozenset[str], frozenset[str]]:
        """What the Sets effectively supply: active ordinary Sets, the Default less its exclusions, and the
        platform's default MCP servers less the Default's MCP exclusions."""
        skill_ids: set[int] = set()
        mcp_codes: set[str] = set()
        platform = set(platform_default_codes)
        for row in self._bot_sets(
            session,
            bot_id=bot_id,
            owner_id=owner_id,
            engine_type=engine_type,
            default_engine_types=default_engine_types,
            locked=locked,
        ):
            members = set_member_skill_ids(self._scope, session, skill_set_id=int(row.id))
            codes = set_member_mcp_codes(self._scope, session, skill_set_id=int(row.id))
            if row.is_default:
                members -= default_exclusions.excluded_skill_ids(
                    session, bot_id=bot_id, owner_id=owner_id, set_id=int(row.id)
                )
                excluded = default_exclusions.excluded_mcp_codes(
                    session, bot_id=bot_id, owner_id=owner_id, set_id=int(row.id)
                )
                codes -= excluded
                platform -= excluded
            elif not row.is_active:
                continue
            skill_ids |= members
            mcp_codes |= codes
        names = (
            {
                str(value[0])
                for value in self._scope(session.query(Skill.name), Skill)
                .filter(Skill.id.in_(sorted(skill_ids)))
                .all()
            }
            if skill_ids
            else set()
        )
        return frozenset(names), frozenset(mcp_codes | platform)

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
        """Whether a legacy Manifest claim (Default exclusion plus Installation) holds this MCP server Direct.

        Read by the ordinary MCP commands, which let a user manage such a server; an apply no longer
        writes this shape.
        """
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
        ordinary_ids = {int(row.id) for row in sets if not row.is_default}
        if ordinary_ids and (
            self._scope(
                session.query(SkillSetMCPServer.id), SkillSetMCPServer
            )
            .filter(
                SkillSetMCPServer.skill_set_id.in_(ordinary_ids),
                SkillSetMCPServer.server_code == server_code,
            )
            .first()
            is not None
        ):
            return False
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

    def _refuse_set_supplied(self, kind: str, identity: str) -> None:
        raise SkillSetControlPlaneConflictError(
            f"APPLY_SET_SUPPLIED: {kind} {identity!r} is supplied by one of the bot's skill sets"
        )

    def claim_apply_mcp(
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
        """Install one Direct MCP server with its complete bot override; refuse one a Set supplies."""
        with self._db.transactional_orm_session() as session:
            old = self._snapshot(session, bot_id, owner_id, engine_type=engine_type)
            _skills, supplied = self._set_supply(
                session,
                bot_id=bot_id,
                owner_id=owner_id,
                platform_default_codes=platform_default_codes,
                engine_type=engine_type,
                default_engine_types=default_engine_types,
                locked=True,
            )
            if server_code in supplied:
                self._refuse_set_supplied("MCP server", server_code)
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
            return DesiredStateMutation(
                {},
                installed or override_changed,
                old,
                mcp_codes=frozenset({server_code}) if installed else frozenset(),
                updated_mcp_codes=(
                    frozenset({server_code})
                    if override_changed and not installed
                    else frozenset()
                ),
                source_transitions=(("none", "direct"),) if installed else (),
            )

    def remove_apply_mcp(
        self,
        *,
        bot_id: str,
        owner_id: str,
        server_code: str,
        platform_default_codes: frozenset[str],
        engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
    ) -> DesiredStateMutation:
        """Remove one Direct MCP server and its override; refuse one a Set supplies."""
        with self._db.transactional_orm_session() as session:
            old = self._snapshot(session, bot_id, owner_id, engine_type=engine_type)
            _skills, supplied = self._set_supply(
                session,
                bot_id=bot_id,
                owner_id=owner_id,
                platform_default_codes=platform_default_codes,
                engine_type=engine_type,
                default_engine_types=default_engine_types,
                locked=True,
            )
            if server_code in supplied:
                self._refuse_set_supplied("MCP server", server_code)
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
                removed or override_removed,
                old,
                mcp_codes=frozenset({server_code}) if removed else frozenset(),
                updated_mcp_codes=(
                    frozenset({server_code}) if override_removed else frozenset()
                ),
                source_transitions=(("direct", "none"),) if removed else (),
            )

    def claim_apply_skill(
        self,
        *,
        bot_id: str,
        owner_id: str,
        skill_id: str,
        engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
    ) -> DesiredStateMutation:
        """Install one Direct Skill, replacing any other Direct Skill of its runtime name.

        The target is the bot's own ``local://`` package, or a shared Center
        (``center://``) or repository (``git://``) asset — the ones a genome
        revision names; whether the actor may use a shared asset is the
        caller's check (``DirectActivationService._resolve_skill``), made
        before this runs. A runtime name a Set supplies is refused.
        """
        with self._db.transactional_orm_session() as session:
            supplied, _mcps = self._set_supply(
                session,
                bot_id=bot_id,
                owner_id=owner_id,
                platform_default_codes=frozenset(),
                engine_type=engine_type,
                default_engine_types=default_engine_types,
                locked=True,
            )
            target = (
                self._scope(session.query(Skill), Skill)
                .filter(Skill.id == int(skill_id))
                .with_for_update()
                .one_or_none()
            )
            locator = str(target.git_path or "") if target is not None else ""
            own_local = locator.startswith("local://") and target.bolt_id == bot_id and str(target.user_id) == owner_id
            if target is None or not (own_local or locator.startswith(_SHARED_LOCATORS)):
                raise SkillSetControlPlaneNotFoundError()
            require_skill_online(target)
            if target.name in supplied:
                self._refuse_set_supplied("skill", str(target.name))
            old = self._snapshot(session, bot_id, owner_id, engine_type=engine_type)
            same_name = (
                self._scope(session.query(Skill), Skill)
                .filter(Skill.name == target.name)
                .with_for_update()
                .all()
            )
            installed_before = self._installations(session, bot_id, owner_id)
            # Another Direct Skill of the same runtime name (an older local package, a shared asset) gives way.
            removed = skill_installations.uninstall(
                session,
                bot_id=bot_id,
                owner_id=owner_id,
                env=get_current_env(),
                skill_ids={int(skill.id) for skill in same_name} - {int(target.id)},
            ) > 0
            installed = skill_installations.install(
                session,
                bot_id=bot_id,
                owner_id=owner_id,
                env=get_current_env(),
                skill_id=int(target.id),
                source_kind=SkillInstallationSourceKind.DIRECT,
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
                removed or installed,
                old,
                mcp_codes=frozenset(dependencies),
                source_transitions=(
                    (("none", "direct"),) if installed and int(target.id) not in installed_before else ()
                ),
            )

    def remove_apply_skill(
        self,
        *,
        bot_id: str,
        owner_id: str,
        skill_id: str,
        engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
    ) -> DesiredStateMutation:
        """Remove one Direct Skill; refuse one whose runtime name a Set supplies."""
        with self._db.transactional_orm_session() as session:
            supplied, _mcps = self._set_supply(
                session,
                bot_id=bot_id,
                owner_id=owner_id,
                platform_default_codes=frozenset(),
                engine_type=engine_type,
                default_engine_types=default_engine_types,
                locked=True,
            )
            skill = (
                self._scope(session.query(Skill), Skill)
                .filter(Skill.id == int(skill_id))
                .with_for_update()
                .one_or_none()
            )
            old = self._snapshot(session, bot_id, owner_id, engine_type=engine_type)
            if skill is None:
                return DesiredStateMutation({}, False, old)
            if skill.name in supplied:
                self._refuse_set_supplied("skill", str(skill.name))
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
                {}, removed, old,
                mcp_codes=dependencies if removed else frozenset(),
                source_transitions=(("direct", "none"),) if removed else (),
            )


__all__ = ["ApplyClaimCommands"]
