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

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from agentclaw.community.core.bot_management.services.bot_service import (
    BotServiceError,
    validate_bot_name,
)
from agentclaw.community.core.mcp.services._defaults import get_default_cli_items
from agentclaw.community.core.mcp.services.passport_scope import (
    filter_passport_mcp_codes,
)
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
    # is_first_bot distinguishes first-agent onboarding from later creates; the
    # internal ``/api/bots`` response echoes it in its ``passport`` payload.
    is_first_bot: bool
    passport_token: str


@dataclass
class AuthStatusResult:
    """Outcome of polling authorization: ``PENDING``, ``ISSUED`` (+bot), or other.

    ``bot`` is populated only on ``ISSUED``; an empty dict on the other states.
    """

    status: str
    bot: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BotCreateSpec:
    """The bot attributes a create / authorization-completion runs with.

    An explicit contract instead of an untyped payload dict: each API surface
    maps its own request shape into this, so a field added or renamed here is a
    type error at every call site rather than a key that silently goes missing
    on one surface.

    ``entity_id`` and ``engine_type`` are **required and concrete** — each
    surface resolves its own default while building the spec (the caller's id;
    ``DEFAULT_ENGINE_TYPE``). ``BotService.create_bot`` only ever applies
    ``x or <default>`` to them, so a concrete value is equivalent to leaving
    them unset, and the flow never has to reason about an absent engine.

    Two fields keep an unset state on purpose:

    * ``bot_name`` — ``None`` means "no name given" so ``_resolve_bot_name``
      derives one (the owner's nick name for a first bot, else the bot id). No
      string can stand in: ``validate_bot_name("")`` rejects the request, and
      the default needs a first-bot lookup the caller cannot pre-compute.
    * ``bot_desc`` — stored straight through to a nullable column and echoed
      back in responses, so ``None`` ("no description") and ``""`` are
      genuinely different persisted values, not interchangeable defaults.
    """

    entity_id: str
    engine_type: str
    bot_type: str
    bot_name: str | None
    entity_type: str = "staff"
    bot_desc: str | None = None
    avatar_url: str | None = None
    share_policy: dict[str, Any] | None = None
    template_type: str | None = None
    template_config: dict[str, Any] | None = None


def _get_bot_mcp_codes(
    factory: SkillSetServiceFactory,
    user_id: str,
    bot_id: str,
    entity_id: str,
    entity_type: str,
    engine_type: str,
) -> list[str]:
    """Resolve the passport-service MCP codes for a bot using the injected factory.

    Internal helper of the create flow — the factory is passed in by the caller
    (obtained via ``Injected(SkillSetServiceFactory)``), so this contains no
    service-locator calls. ``engine_type`` scopes the skill-set query to the
    bot's active engine. LOCAL/stdio MCPs are filtered because the passport
    service does not own them.
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


def _apply_passport(
    passport_plugin: PassportPlugin,
    *,
    bot_id: str,
    user_id: str,
    bot_name: str | None,
    spec: BotCreateSpec,
    mcp_codes: list[str],
    cli_items: list[Any],
    is_first_bot: bool,
) -> dict[str, Any] | None:
    """Apply for the bot's Passport, normalizing non-Passport failures.

    ``PassportError`` propagates as-is (each surface maps it to its own code);
    any other apply failure becomes a ``BotServiceError`` so it keeps the
    "Passport apply failed" mapping rather than falling into a generic bucket.
    """
    apply = (
        passport_plugin.apply_first_agent_passport
        if is_first_bot
        else passport_plugin.apply_agent_passport
    )
    try:
        return apply(
            bot_id=bot_id,
            owner_workno=user_id,
            mcp_codes=mcp_codes,
            cli_items=cli_items,
            bot_name=bot_name,
            bot_desc=spec.bot_desc,
            engine_type=spec.engine_type,
            access_mode=_ACCESS_MODE,
            workspace_path=_WORKSPACE_PATH,
        )
    except PassportError:
        raise
    except Exception as e:  # noqa: BLE001 — normalized to the service-error bucket
        raise BotServiceError(f"Passport申请失败: {e}")


def _build_ext(
    *, avatar_url: str | None, agent_code: str | None, issued: bool = False
) -> dict[str, Any] | None:
    """Assemble the bot's ``ext`` payload; ``None`` when there is nothing to store."""
    ext: dict[str, Any] = {}
    if avatar_url:
        ext["avatar_url"] = avatar_url
    if agent_code:
        passport: dict[str, Any] = {"agent_code": agent_code}
        if issued:
            passport["status"] = "ISSUED"
        ext["passport"] = passport
    return ext or None


def _query_agent_code(
    passport_plugin: PassportPlugin, *, bot_id: str, user_id: str
) -> str | None:
    """Best-effort ``agent_code`` lookup — ``query_auth_status`` does not carry it."""
    try:
        info = passport_plugin.query_agent_passport(
            bot_id=bot_id, owner_workno=user_id
        )
    except Exception as e:  # noqa: BLE001 — agent_code lookup is best-effort
        logger.warning("[create_flow] query_agent_passport failed: %s", e)
        return None
    return info.get("agent_code") if info else None


def _record_owner_relationship(
    auth_rel_plugin: AuthRelationshipPlugin,
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
                "[create_flow] authorization-relationship service returned failure "
                "for create_relationship: "
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
    spec: BotCreateSpec,
    cookie: str | None = None,  # see the note on cookies below
    bot_service: BotService,
    passport_plugin: PassportPlugin,
    auth_rel_plugin: AuthRelationshipPlugin,
    skill_set_factory: SkillSetServiceFactory,
) -> Created | AuthPending:
    """Run the create + Passport-authorization flow for an already-allocated id.

    Returns :class:`AuthPending` when Passport has not yet issued a token (the
    caller guides the user through the iframe, then polls
    :func:`complete_bot_authorization`), or :class:`Created` when the bot was
    created inline. Raises the bot/passport domain errors for the caller to map.

    ``cookie`` forwards the browser session down to the service layer, which is
    **bad practice** — a transport credential has no business below the adapter
    boundary. It is threaded through only for backward compatibility with the
    internal ``/api/bots`` path, whose downstream memoryos call still relies on
    it. The public ``/openapi/v1`` surface deliberately does not pass it (its
    callers are registered tenants, not browser sessions), so it is optional and
    should stay unset for any new caller; ``create_bot`` treats ``None`` as "no
    cookie". Remove the parameter entirely once the internal path stops needing
    it.
    """
    # Validate the name up front so an invalid one never reaches Passport or
    # create. An unset name stays unset — create_bot applies default naming.
    bot_name = validate_bot_name(spec.bot_name) if spec.bot_name is not None else None
    is_first_bot = bot_id == "default"

    # Pre-flight before Passport, so a limit is reported before the user is sent
    # through authorization (raises BotLimitExceededError).
    bot_service.check_create_bot_preflight(user_id=user_id)

    passport_result = _apply_passport(
        passport_plugin,
        bot_id=bot_id,
        user_id=user_id,
        bot_name=bot_name,
        spec=spec,
        mcp_codes=_get_bot_mcp_codes(
            skill_set_factory, user_id, bot_id, spec.entity_id, spec.entity_type,
            engine_type=spec.engine_type,
        ),
        cli_items=get_default_cli_items(
            spec.engine_type, spec.template_type
        ),
        is_first_bot=is_first_bot,
    )

    passport_token = passport_result.get("token") if passport_result else None
    agent_code = passport_result.get("agent_code") if passport_result else None

    # No token yet → authorization pending; nothing is created.
    if not passport_token:
        return AuthPending(
            bot_id=bot_id,
            iframe_url=passport_result.get("iframe_url") if passport_result else None,
            redirect_url=passport_result.get("redirect_url") if passport_result else None,
        )

    # Token present → create the bot inline.
    result = bot_service.create_bot(
        user_id=user_id,
        nick_name=nick_name,
        bot_name=bot_name,
        bot_desc=spec.bot_desc,
        entity_id=spec.entity_id,
        entity_type=spec.entity_type,
        share_policy=spec.share_policy,
        engine_type=spec.engine_type,
        ext=_build_ext(avatar_url=spec.avatar_url, agent_code=agent_code),
        bot_id=bot_id,
        bot_type=spec.bot_type,
        template_type=spec.template_type,
        template_config=spec.template_config,
        cookie=cookie,
    )

    if agent_code:
        _record_owner_relationship(
            auth_rel_plugin, user_id=user_id, agent_code=agent_code,
            nick_name=nick_name, bot_id=bot_id,
        )

    return Created(
        bot=result,
        is_first_bot=is_first_bot,
        passport_token=passport_token,
    )


def complete_bot_authorization(
    *,
    user_id: str,
    nick_name: str,
    bot_id: str,
    spec: BotCreateSpec,
    cookie: str | None = None,  # see the note on cookies below
    bot_service: BotService,
    passport_plugin: PassportPlugin,
    auth_rel_plugin: AuthRelationshipPlugin,
) -> AuthStatusResult:
    """Poll Passport authorization for a pending bot; complete creation on ISSUED.

    ``PENDING`` returns immediately; ``ISSUED`` finishes the (idempotent) create
    and records the owner relationship; any other status is returned verbatim for
    the caller to map. Raises ``PassportError`` (query failure) and the create
    domain errors for the caller to map; a query that returns nothing raises
    ``RuntimeError`` (mapped to the internal 500).

    ``cookie`` carries the browser session into the service layer, which is
    **bad practice** — see :func:`create_bot_with_authorization` for the full
    note. Kept only for the internal ``/api/bots`` path; the public
    ``/openapi/v1`` surface does not pass it.
    """
    auth_status = passport_plugin.query_auth_status(bot_id=bot_id, owner_workno=user_id)
    if not auth_status:
        raise RuntimeError("query auth status returned nothing")

    status = auth_status.get("status")
    if status != "ISSUED":
        # PENDING (still waiting) and any other status (e.g. REJECTED) are
        # returned verbatim for the surface to map; nothing is created.
        return AuthStatusResult(status=status)

    agent_code = _query_agent_code(passport_plugin, bot_id=bot_id, user_id=user_id)

    result = bot_service.create_bot(
        user_id=user_id,
        nick_name=nick_name,
        bot_id=bot_id,
        bot_name=spec.bot_name,
        bot_desc=spec.bot_desc,
        entity_id=spec.entity_id,
        entity_type=spec.entity_type,
        share_policy=spec.share_policy,
        engine_type=spec.engine_type,
        ext=_build_ext(
            avatar_url=spec.avatar_url, agent_code=agent_code, issued=True
        ),
        bot_type=spec.bot_type,
        template_type=spec.template_type,
        template_config=spec.template_config,
        cookie=cookie,
    )

    if agent_code:
        _record_owner_relationship(
            auth_rel_plugin, user_id=user_id, agent_code=agent_code,
            nick_name=nick_name, bot_id=bot_id,
        )

    return AuthStatusResult(status="ISSUED", bot=result)
