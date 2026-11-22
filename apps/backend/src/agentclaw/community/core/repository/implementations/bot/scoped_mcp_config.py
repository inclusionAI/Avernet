"""Atomic persistence of one user's scoped MCP connection rules."""

from __future__ import annotations

import json
from typing import Any

from injector import inject
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from agentclaw.community.core.models.mcp import UserMCPConfig
from agentclaw.community.core.models.skill import Skill
from agentclaw.community.core.repository.implementations.bot.template import find_template
from agentclaw.community.core.repository.implementations.skill_center.skill_mcp_dependencies import (
    skill_projection_mcp_dependency_codes,
)
from agentclaw.community.core.skill_center.policies.platform_default_mcp import platform_default_mcp_codes
from agentclaw.community.core.repository.implementations.skill_center.bot_mutation_fence import _begin_sqlite_write
from agentclaw.community.core.repository.implementations.skill_center.capability_projection import record_capability_projection
from agentclaw.community.core.skill_center.runtime_projection_contract import ProjectionScope
from agentclaw.community.core.bot_management.owner_handoff.contracts import HandoffError
from agentclaw.community.core.bot_management.owner_handoff.guard import assert_bot_mutable
from agentclaw.community.core.mcp.scoped_config_contract import BotMCPConfigPatch
from agentclaw.community.core.mcp.errors import McpConfigValueError
from agentclaw.community.core.bot_management.errors import BotLookupAmbiguousError
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.core.repository.implementations.bot import mcp_config_reads
from agentclaw.community.core.repository.implementations.skill_center.tables import (
    bot_mcp_configs,
    default_exclusions,
    mcp_installations,
    skill_installations,
)
from agentclaw.community.core.repository.protocols.bot.mcp import (
    ScopedMCPConfigRepositoryProtocol,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env


def _json_object(value: str | None) -> dict[str, Any]:
    parsed = json.loads(value) if value else {}
    if not isinstance(parsed, dict):
        raise ValueError("MCP config JSON must be an object")
    return parsed


def _supplies_mcp(session: Session, bot: BotModel, server_code: str) -> bool:
    """Read the active Installations and platform defaults under the Bot lock."""
    bot_id, entity_id, env = str(bot.bot_id), str(bot.entity_id), str(bot.env)
    scope = dict(bot_id=bot_id, owner_id=entity_id, env=env)
    if server_code in mcp_installations.installed_codes(session, **scope, locked=True):
        return True
    template = find_template(session, bot_id, lock=True)
    ext_info = {"template_config": template.to_dict().get("ext")} if template else None
    if server_code in platform_default_mcp_codes(bot.to_dict(), ext_info=ext_info):
        if not default_exclusions.lock_mcp_exclusions(
            session, bot_id=bot_id, owner_id=entity_id, server_code=server_code,
        ):
            return True
    skill_ids = skill_installations.installed_ids(session, **scope, locked=True)
    if not skill_ids:
        return False
    skills = (
        session.query(Skill)
        .filter(
            Skill.id.in_(sorted(skill_ids)), Skill.env == env,
            Skill.avernet_tenant == bot.avernet_tenant, Skill.git_path != "",
        )
        .order_by(Skill.id)
        .with_for_update()
        .all()
    )
    return any(server_code in skill_projection_mcp_dependency_codes(session, skill) for skill in skills)


class ScopedMCPConfigRepository(ScopedMCPConfigRepositoryProtocol):
    """Replace user and owner-qualified Bot rules in one transaction."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def replace(
        self,
        *,
        user_id: str,
        server_code: str,
        headers: dict[str, str],
        bot_headers: dict[str, dict[str, str]],
        owned_bot_ids: set[str],
        endpoint_env: str,
        transport_protocol: str | None,
        user_url: str | None = None,
        bot_urls: dict[str, str] | None = None,
        create_projection: bool | None = None,
    ) -> dict[str, str | None]:
        if not bot_headers.keys() <= owned_bot_ids:
            raise ValueError("Bot Header scope contains a Bot not owned by the user")
        if bot_urls is not None and not bot_urls.keys() <= owned_bot_ids:
            raise ValueError("Bot URL scope contains a Bot not owned by the user")
        if bot_urls is None and user_url is not None:
            raise ValueError("user_url requires an explicit URL replacement")
        env = get_current_env()
        tenant = get_current_avernet_tenant()
        with self._db.transactional_orm_session() as session:
            if create_projection is not None:
                _begin_sqlite_write(session)
            user_row = (
                session.query(UserMCPConfig)
                .filter(
                    UserMCPConfig.avernet_tenant == tenant,
                    UserMCPConfig.env == env,
                    UserMCPConfig.user_id == user_id,
                    UserMCPConfig.server_code == server_code,
                )
                .with_for_update()
                .one_or_none()
            )
            extra = _json_object(user_row.extra_config if user_row else None)
            extra.update(
                headers=headers,
                endpoint_env=endpoint_env,
                transport_protocol=transport_protocol,
            )
            if bot_urls is not None:
                if user_url is None:
                    extra.pop("url", None)
                else:
                    extra["url"] = user_url
            encoded_user = json.dumps(extra, ensure_ascii=False, sort_keys=True)
            if user_row is None:
                session.add(
                    UserMCPConfig(
                        user_id=user_id,
                        server_code=server_code,
                        api_key=extra.get("api_key"),
                        extra_config=encoded_user,
                        env=env,
                        avernet_tenant=tenant,
                    )
                )
            else:
                user_row.extra_config = encoded_user

            # The caller's preflight can race handoff/deletion. Recheck and
            # lock current ownership before selecting immutable resource keys.
            bots = session.query(BotModel).filter(
                BotModel.avernet_tenant == tenant, BotModel.env == env,
                BotModel.is_delete == 0, BotModel.owner_id == user_id,
                BotModel.bot_id.in_(owned_bot_ids),
            ).order_by(BotModel.id).with_for_update().all()
            resources = {}
            for bot in bots:
                if bot.bot_id in resources:
                    raise BotLookupAmbiguousError(f"Ambiguous scoped MCP Bot: {bot.bot_id}")
                resources[str(bot.bot_id)] = str(bot.entity_id)
            if resources.keys() != owned_bot_ids:
                raise McpConfigValueError("Scoped MCP target is no longer owned by caller")
            existing = {}
            for bot_id in sorted(owned_bot_ids):
                config = mcp_config_reads.get(
                    session, bot_id=bot_id, owner_id=resources[bot_id], env=env, server_code=server_code,
                )
                if config is not None:
                    existing[bot_id] = config
            for bot_id in sorted(existing.keys() | bot_headers.keys() | (bot_urls or {}).keys()):
                config = dict(existing.get(bot_id) or {})
                if bot_headers.get(bot_id):
                    config["headers"] = bot_headers[bot_id]
                else:
                    config.pop("headers", None)
                if bot_urls is not None:
                    if bot_id in bot_urls:
                        config["url"] = bot_urls[bot_id]
                    else:
                        config.pop("url", None)
                bot_mcp_configs.replace(
                    session,
                    bot_id=bot_id,
                    owner_id=resources[bot_id],
                    server_code=server_code,
                    config=config or None,
                    env=env,
                )

            applicable: dict[str, str | None] = {}
            if create_projection is not None:
                # The caller materialized legacy supply during preflight. Re-read
                # Installation after locking the Bot, so a recently committed
                # capability edit participates in this same transaction.
                scope = ProjectionScope(mcp=True, updated_mcp=frozenset({server_code}))
                for bot in bots:
                    if not _supplies_mcp(session, bot, server_code):
                        continue
                    bot_id, entity_id = str(bot.bot_id), str(bot.entity_id)
                    state = record_capability_projection(
                        session, bot_id=bot_id, entity_id=entity_id,
                        scope=scope, create=create_projection,
                    )
                    applicable[bot_id] = entity_id if state is not None else None
            return applicable

    def patch_bot(
        self, *, bot_pk: int, bot_id: str, entity_id: str, current_owner_id: str,
        server_code: str, patch: BotMCPConfigPatch, create_projection: bool | None = None,
    ) -> tuple[dict[str, Any], bool]:
        env = get_current_env()
        tenant = get_current_avernet_tenant()
        try:
            with self._db.transactional_orm_session() as session:
                if create_projection is not None:
                    _begin_sqlite_write(session)
                bot = session.query(BotModel).filter(
                    BotModel.id == bot_pk,
                    BotModel.avernet_tenant == tenant,
                    BotModel.env == env,
                    BotModel.is_delete == 0,
                    BotModel.bot_id == bot_id,
                    BotModel.owner_id == current_owner_id,
                    BotModel.entity_id == entity_id,
                ).with_for_update().one_or_none()
                if bot is None:
                    raise McpConfigValueError("Bot address changed during MCP config update")
                try:
                    assert_bot_mutable({"ext": bot.ext})
                except HandoffError:
                    raise McpConfigValueError("Bot is temporarily read-only") from None

                previous = mcp_config_reads.get(
                    session, bot_id=bot_id, owner_id=entity_id, env=env, server_code=server_code,
                ) or {}
                config = dict(previous)
                if patch.get("headers"):
                    updates = patch["headers"]
                    names = {name.lower() for name in updates}
                    headers = {
                        key: value for key, value in (previous.get("headers") or {}).items()
                        if key.lower() not in names
                    }
                    headers.update({key: value for key, value in updates.items() if value is not None})
                    if headers:
                        config["headers"] = headers
                    elif previous.get("headers"):
                        config.pop("headers")
                if "url" in patch:
                    if patch["url"] is None:
                        config.pop("url", None)
                    else:
                        config["url"] = patch["url"]
                changed = config != previous
                if changed:
                    bot_mcp_configs.replace(
                        session, bot_id=bot_id, owner_id=entity_id, env=env,
                        server_code=server_code, config=config or None,
                    )
                    if create_projection is not None and _supplies_mcp(session, bot, server_code):
                        record_capability_projection(
                            session, bot_id=bot_id, entity_id=entity_id,
                            scope=ProjectionScope(mcp=True, updated_mcp=frozenset({server_code})),
                            create=create_projection,
                        )
                return config, changed
        except SQLAlchemyError as exc:
            # SQLAlchemy can include the bound override JSON in its traceback.
            raise RuntimeError(f"Bot MCP config persistence failed ({type(exc).__name__})") from None
