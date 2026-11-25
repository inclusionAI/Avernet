"""The ordinary create path: create a Bot, or pause it for authorization.

Split out of ``create_flow`` (which owns the manifest-create path) to keep both
modules under the size guard. Re-exported from ``create_flow`` so callers and
imports are unchanged.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any, Mapping

from agentclaw.community.core.bot_management.create_context import (
    BotCreateContext,
    BotCreateSpec,
)
from agentclaw.community.core.bot_management.create_flow import (
    AuthPending,
    AuthStatus,
    Created,
    _apply_passport,
    _build_ext,
    _get_bot_mcp_codes,
    _prepare_create,
    _query_agent_code,
    _require_agent_code,
)
from agentclaw.community.core.bot_management.authorized_creation import (
    create_authorized_bot,
)
from agentclaw.community.core.bot_management.services.bot_service import (
    validate_bot_name,
)
from agentclaw.community.core.mcp.services._defaults import get_default_cli_items
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.passport import PassportError

if TYPE_CHECKING:
    from agentclaw.community.core.bot_management.services.bot_service import BotService
    from agentclaw.community.core.skill_center.factories import SkillSetServiceFactory
    from agentclaw.community.plugin_api.auth_relationship import AuthRelationshipPlugin
    from agentclaw.community.plugin_api.bot_create_extension import BotCreateExtension
    from agentclaw.community.plugin_api.passport import PassportPlugin

logger = get_logger()


def create_bot_with_authorization(
    *,
    user_id: str,
    nick_name: str,
    bot_id: str,
    spec: BotCreateSpec,
    context: BotCreateContext,
    cookie: str | None = None,  # see the note on cookies below
    bot_service: BotService,
    passport_plugin: PassportPlugin,
    auth_rel_plugin: AuthRelationshipPlugin,
    skill_set_factory: SkillSetServiceFactory,
    create_extension: "BotCreateExtension | None" = None,
    request: Mapping[str, Any] | None = None,
) -> Created | AuthPending:
    """Run the create + Passport-authorization flow for an already-allocated id.

    Returns :class:`AuthPending` when Passport has not yet issued a token (the
    caller guides the user through the iframe, then polls
    :func:`complete_bot_authorization`), or :class:`Created` when the bot was
    created inline. Raises the bot/passport domain errors for the caller to map.

    ``cookie`` forwards the browser session down to the service layer, which is
    **bad practice** — a transport credential has no business below the adapter
    boundary. Threaded through only for the internal ``/api/bots`` path, whose
    downstream memoryos call still relies on it; the public ``/openapi/v1``
    surface does not pass it. Remove once the internal path stops needing it.
    """

    # A bound lifecycle extension may reinterpret the request first (identity
    # fields, provisioning split, pre-issued identity). Opaque to this layer;
    # everything it decides runs here through the flow's OWN contracts.
    override = None
    if create_extension is not None:
        override = create_extension.resolve_create(
            bot_id=bot_id, user_id=user_id,
            request=request if request is not None else {},
        )
        if override is not None and override.bot_id:
            # Continue under the id the extension's own arbitration decided;
            # the caller's id was allocation only, nothing is written yet.
            bot_id = override.bot_id

        # Creation policy is evaluated here, rather than in either transport, so no
    # caller can bypass template/combination rules before Passport or writes.
    if override is not None:
        spec = replace(
            spec,
            template_config=(
                dict(override.template_config)
                if override.template_config is not None else spec.template_config
            ),
            template_type=override.template_type or spec.template_type,
            engine_type=override.engine_type or spec.engine_type,
        )
    spec = _prepare_create(spec=spec, context=context, bot_service=bot_service)

    # Validate the name up front so an invalid one never reaches Passport or
    # create. An unset name stays unset — create_bot applies default naming.
    bot_name = validate_bot_name(spec.bot_name) if spec.bot_name is not None else None
    # Preserve the existing first-Bot Passport path for every Bot type. When the
    # owner already has service Bots, the first live personal Bot also uses that
    # path; service and soft-deleted personal Bots do not consume this eligibility.
    is_first_bot = bot_service.is_first_bot(user_id)
    use_first_passport = is_first_bot or (
        spec.bot_type == "personal"
        and bot_service.is_first_personal_bot(user_id)
    )

    # Pre-flight before Passport, so quota, name, and reserved-bot engine
    # violations are reported before an external Passport identity is minted.
    bot_service.check_create_bot_preflight(
        user_id=user_id,
        bot_id=bot_id,
        engine_type=spec.engine_type,
        bot_name=bot_name,
        space_id=spec.space_id,
        space_quota=context.space_quota,
    )

    agent_code: str | None = None
    if override is not None and override.preissued is not None:
        # The identity already exists (minted in a flow the extension ran
        # earlier); re-applying would mint a second one. Skip straight to the
        # create step with the agent code the extension hands over — nothing
        # external is touched on this path.
        passport_token = ""
        agent_code = override.preissued["agent_code"]
    else:
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
                spec.engine_type,
                spec.template_type,
                ext_info={"template_config": spec.template_config}
                if spec.template_config is not None
                else None,
            ),
            use_first_passport=use_first_passport,
        )

        passport_token = None
        if passport_result:
            passport_token = passport_result.get("token")

        # No token yet → authorization pending; nothing is created.
        if not passport_token:
            iframe_url = passport_result.get("iframe_url") if passport_result else None
            redirect_url = passport_result.get("redirect_url") if passport_result else None
            # "Pending" is only a real state if the caller has somewhere to go. With
            # neither a token nor a handle — including the ``None`` result the plugin
            # contract explicitly permits — the apply did not succeed, and reporting
            # it as pending hands back a dead end that is indistinguishable from a
            # genuine wait.
            if not iframe_url and not redirect_url:
                raise PassportError(
                    f"Passport returned no token and no authorization URL for bot {bot_id}"
                )
            return AuthPending(
                bot_id=bot_id,
                iframe_url=iframe_url,
                redirect_url=redirect_url,
            )

        # A completed Passport identity must include the identifier needed for the
        # owner relationship. Fail before creating the bot rather than silently
        # acknowledging a bot that its owner cannot reach through that relationship.
        agent_code = _require_agent_code(
            passport_result.get("agent_code") if passport_result else None,
            bot_id=bot_id,
        )

    # Token present → create the bot inline.
    result = create_authorized_bot(
        bot_service=bot_service,
        auth_rel_plugin=auth_rel_plugin,
        user_id=user_id,
        nick_name=nick_name,
        bot_id=bot_id,
        agent_code=agent_code,
        create_kwargs={
            "user_id": user_id,
            "nick_name": nick_name,
            "bot_name": bot_name,
            "bot_desc": spec.bot_desc,
            "entity_id": spec.entity_id,
            "entity_type": spec.entity_type,
            "share_policy": spec.share_policy,
            "engine_type": spec.engine_type,
            "ext": _build_ext(avatar_url=spec.avatar_url, agent_code=agent_code, expert_origin=spec.expert_origin),
            "bot_id": bot_id,
            "bot_type": spec.bot_type,
            "template_type": spec.template_type,
            "template_config": spec.template_config,
            "cookie": cookie,
            "space_id": spec.space_id,
            "space_quota": context.space_quota,
            "provision": (
                override.provision
                if override is not None and override.provision is not None
                else True
            ),
        },
    )

    if override is not None:
        # Only a request the extension resolved enters its post-registration
        # bookkeeping; unrecognized requests run the unextended path.
        create_extension.after_bot_registered(bot_id=bot_id, user_id=user_id)

    return Created(
        bot=result,
        is_first_bot=is_first_bot,
        passport_token=passport_token,
    )
