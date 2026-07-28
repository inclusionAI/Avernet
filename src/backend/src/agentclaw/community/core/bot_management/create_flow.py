"""Request-agnostic bot creation + Passport authorization orchestration.

Both API surfaces need the same multi-step create flow — allocate MCP codes,
apply for a Passport, branch on whether authorization is still pending, then
create the bot and record the owner relationship. This module owns that
orchestration so the internal ``/api/bots`` router and the public
``/openapi/v1/bots`` router call one implementation instead of each carrying a
copy.

The functions here are deliberately free of FastAPI / ``Request`` / response
types: they take the resolved caller identity plus the already-injected
services/plugins, return a small discriminated result (:class:`Created` /
:class:`AuthPending` / :class:`AuthStatusResult`), and **raise** domain errors
(``BotNameInvalidError``, ``BotLimitExceededError``, ``PassportError``,
``DeviceLimitError``, ``BotServiceError`` …) for each surface to map onto its own
response shape. ``bot_id`` is passed in (not allocated here) so callers keep
ownership of id allocation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from agentclaw.community.core.bot_management.services.bot_service import (
    BotServiceError,
    validate_bot_name,
)
from agentclaw.community.core.mcp.services._defaults import get_default_cli_items
from agentclaw.community.core.mcp.services.passport_scope import (
    filter_passport_mcp_codes,
)
from agentclaw.community.core.workspace.constants import DEFAULT_ENGINE_TYPE
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.auth_relationship import AuthRelationshipError
from agentclaw.community.plugin_api.passport import PassportError

if TYPE_CHECKING:
    from agentclaw.community.core.bot_management.services.bot_service import BotService
    from agentclaw.community.core.skill_center.factories import SkillSetServiceFactory
    from agentclaw.community.plugin_api.auth_relationship import AuthRelationshipPlugin
    from agentclaw.community.plugin_api.passport import PassportPlugin

logger = get_logger()

# Passport application constants (unchanged from the internal router).
_ACCESS_MODE = "RESTRICTED"
_WORKSPACE_PATH = "/home/admin/.openclaw"


@dataclass
class AuthPending:
    """Creation is paused pending user Passport authorization (iframe flow)."""

    bot_id: str
    iframe_url: str | None
    redirect_url: str | None


@dataclass
class Created:
    """The bot was created inline (Passport already issued a token)."""

    bot: dict[str, Any]
    agent_code: str | None
    is_first_bot: bool
    passport_token: str


@dataclass
class AuthStatusResult:
    """Outcome of polling authorization: ``PENDING``, ``ISSUED`` (+bot), or other."""

    status: str
    bot: dict[str, Any] | None = None


def get_bot_mcp_codes(
    factory: "SkillSetServiceFactory",
    user_id: str,
    bot_id: str,
    entity_id: str,
    entity_type: str,
    engine_type: str | None = None,
) -> list[str]:
    """Resolve AgentPass MCP codes for a bot using the injected factory.

    Pure helper — the factory is passed in by the caller (obtained via
    ``Injected(SkillSetServiceFactory)``), so this contains no service-locator
    calls. ``engine_type`` scopes the skill-set query to the bot's active
    engine; when omitted the factory falls back to ``DEFAULT_ENGINE_TYPE``.
    LOCAL/stdio MCPs are filtered because AgentPass does not own them.
    """
    skill_set_service = factory.create(
        user_id=user_id,
        entity_id=entity_id,
        bot_id=bot_id,
        entity_type=entity_type,
        engine_type=engine_type,
    )
    mcp_codes = skill_set_service.get_bot_mcp_codes(
        entity_id=entity_id,
        bot_id=bot_id,
        user_id=user_id,
        entity_type=entity_type,
    )
    return filter_passport_mcp_codes(mcp_codes)


def _record_owner_relationship(
    auth_rel_plugin: "AuthRelationshipPlugin",
    *,
    user_id: str,
    agent_code: str,
    nick_name: str,
    bot_id: str,
) -> None:
    """Create the owner→bot auth relationship. Best-effort: failures are logged."""
    try:
        auth_result = auth_rel_plugin.create_relationship(
            work_no=user_id,
            agent_code=agent_code,
            description="Bot owner default authorization",
            operator_work_no=user_id,
            operator_name=nick_name,
        )
        if auth_result:
            logger.info(
                "[create_flow] Created owner auth relationship: bot_id=%s owner=%s "
                "agent_code=%s auth_id=%s",
                bot_id, user_id, agent_code, auth_result.get("auth_id"),
            )
        else:
            logger.warning(
                "[create_flow] AceAgent returned failure for create_relationship: "
                "bot_id=%s owner=%s agent_code=%s", bot_id, user_id, agent_code,
            )
    except AuthRelationshipError as e:
        logger.warning(
            "[create_flow] Failed to create owner auth relationship: bot_id=%s error=%s",
            bot_id, e,
        )
    except Exception as e:  # noqa: BLE001 — owner relationship is best-effort
        logger.warning(
            "[create_flow] Unexpected error creating owner auth relationship: "
            "bot_id=%s error=%s", bot_id, e,
        )


def create_bot_with_authorization(
    *,
    user_id: str,
    nick_name: str,
    bot_id: str,
    params: dict[str, Any],
    cookie: str,
    bot_service: "BotService",
    passport_plugin: "PassportPlugin",
    auth_rel_plugin: "AuthRelationshipPlugin",
    skill_set_factory: "SkillSetServiceFactory",
) -> Created | AuthPending:
    """Run the create + Passport-authorization flow for an already-allocated id.

    Returns :class:`AuthPending` when Passport has not yet issued a token (the
    caller guides the user through the iframe, then polls
    :func:`complete_bot_authorization`), or :class:`Created` when the bot was
    created inline. Raises the bot/passport domain errors for the caller to map.
    """
    # 0. Validate the name up front so an invalid name never reaches Passport or
    #    create (mirrors the internal router's early check). None = default name.
    raw_bot_name = params.get("bot_name")
    bot_name = validate_bot_name(raw_bot_name) if raw_bot_name is not None else None

    entity_id = params.get("entity_id") or user_id
    entity_type = params.get("entity_type") or "staff"
    is_first_bot = bot_id == "default"
    passport_engine_type = params.get("engine_type") or DEFAULT_ENGINE_TYPE
    bot_type = params.get("bot_type")
    avatar_url = params.get("avatar_url")

    # 1. Pre-flight before Passport, so a limit is reported before the user is
    #    sent through authorization (raises BotLimitExceededError).
    bot_service.check_create_bot_preflight(user_id=user_id)

    # 2. Remote MCP codes for the Passport application.
    mcp_codes = get_bot_mcp_codes(
        skill_set_factory, user_id, bot_id, entity_id, entity_type,
        engine_type=passport_engine_type,
    )
    default_cli_items = get_default_cli_items(
        passport_engine_type, params.get("template_type")
    )

    # 3. Apply for the Passport. A PassportError propagates as-is (surface maps
    #    it); any other apply failure becomes a BotServiceError so it keeps the
    #    internal "Passport apply failed" 500 mapping rather than a generic 501.
    apply = (
        passport_plugin.apply_first_agent_passport
        if is_first_bot
        else passport_plugin.apply_agent_passport
    )
    try:
        passport_result = apply(
            bot_id=bot_id,
            owner_workno=user_id,
            mcp_codes=mcp_codes,
            cli_items=default_cli_items,
            bot_name=bot_name,
            bot_desc=params.get("bot_desc"),
            engine_type=passport_engine_type,
            access_mode=_ACCESS_MODE,
            workspace_path=_WORKSPACE_PATH,
        )
    except PassportError:
        raise
    except Exception as e:  # noqa: BLE001 — normalized to the 500 bucket
        raise BotServiceError(f"Passport申请失败: {e}")

    passport_token = passport_result.get("token") if passport_result else None
    agent_code = passport_result.get("agent_code") if passport_result else None
    iframe_url = passport_result.get("iframe_url") if passport_result else None
    redirect_url = passport_result.get("redirect_url") if passport_result else None

    # 4. No token yet → authorization pending.
    if not passport_token:
        return AuthPending(bot_id=bot_id, iframe_url=iframe_url, redirect_url=redirect_url)

    # 5. Token present → create the bot inline.
    ext: dict[str, Any] = {}
    if avatar_url:
        ext["avatar_url"] = avatar_url
    if agent_code:
        ext["passport"] = {"agent_code": agent_code}

    result = bot_service.create_bot(
        user_id=user_id,
        nick_name=nick_name,
        bot_name=bot_name,
        bot_desc=params.get("bot_desc"),
        entity_id=entity_id,
        entity_type=entity_type,
        share_policy=params.get("share_policy"),
        engine_type=params.get("engine_type"),
        ext=ext if ext else None,
        bot_id=bot_id,
        bot_type=bot_type,
        template_type=params.get("template_type"),
        template_config=params.get("template_config"),
        cookie=cookie,
    )

    if agent_code:
        _record_owner_relationship(
            auth_rel_plugin, user_id=user_id, agent_code=agent_code,
            nick_name=nick_name, bot_id=bot_id,
        )

    return Created(
        bot=result,
        agent_code=agent_code,
        is_first_bot=is_first_bot,
        passport_token=passport_token,
    )


def complete_bot_authorization(
    *,
    user_id: str,
    nick_name: str,
    bot_id: str,
    params: dict[str, Any],
    cookie: str,
    bot_service: "BotService",
    passport_plugin: "PassportPlugin",
    auth_rel_plugin: "AuthRelationshipPlugin",
) -> AuthStatusResult:
    """Poll Passport authorization for a pending bot; complete creation on ISSUED.

    ``PENDING`` returns immediately; ``ISSUED`` finishes the (idempotent) create
    and records the owner relationship; any other status is returned verbatim for
    the caller to map. Raises ``PassportError`` (query failure) and the create
    domain errors for the caller to map; a query that returns nothing raises
    ``RuntimeError`` (mapped to the internal 500).
    """
    auth_status = passport_plugin.query_auth_status(bot_id=bot_id, owner_workno=user_id)
    if not auth_status:
        raise RuntimeError("query auth status returned nothing")

    status = auth_status.get("status")

    if status == "PENDING":
        return AuthStatusResult(status="PENDING")

    if status != "ISSUED":
        return AuthStatusResult(status=status)

    entity_id = params.get("entity_id") or user_id
    entity_type = params.get("entity_type") or "staff"
    avatar_url = params.get("avatar_url")

    # query_auth_status does not carry agent_code — fetch it (best-effort).
    agent_code = None
    try:
        passport_info = passport_plugin.query_agent_passport(
            bot_id=bot_id, owner_workno=user_id
        )
        if passport_info:
            agent_code = passport_info.get("agent_code")
    except Exception as e:  # noqa: BLE001 — agent_code lookup is best-effort
        logger.warning("[create_flow] query_agent_passport failed: %s", e)

    ext: dict[str, Any] = {}
    if avatar_url:
        ext["avatar_url"] = avatar_url
    if agent_code:
        ext["passport"] = {"agent_code": agent_code, "status": "ISSUED"}

    result = bot_service.create_bot(
        user_id=user_id,
        nick_name=nick_name,
        bot_id=bot_id,
        bot_name=params.get("bot_name"),
        bot_desc=params.get("bot_desc"),
        entity_id=entity_id,
        entity_type=entity_type,
        share_policy=params.get("share_policy"),
        engine_type=params.get("engine_type"),
        ext=ext if ext else None,
        bot_type=params.get("bot_type"),
        template_type=params.get("template_type"),
        template_config=params.get("template_config"),
        cookie=cookie,
    )

    if agent_code:
        _record_owner_relationship(
            auth_rel_plugin, user_id=user_id, agent_code=agent_code,
            nick_name=nick_name, bot_id=bot_id,
        )

    return AuthStatusResult(status="ISSUED", bot=result)
