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

from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from agentclaw.community.core.bot_management.create_context import (
    BotCreateContext,
    BotCreateDeploymentMode as BotCreateDeploymentMode,
    BotCreateSpec as BotCreateSpec,
)
from agentclaw.community.core.bot_management.engines.provisioning import (
    BotCreateTemplateValidationMode,
    PreparedBotCreate,
    to_internal_template_config,
)
from agentclaw.community.core.bot_management.engines.registry import (
    get_engine_provisioning_registry,
    normalize_engine_type,
)
from agentclaw.community.core.bot_management.engines.aicoding.strategy import (
    AICODING_ENGINE_TYPE,
    CLAUDE_CODE_ENGINE_TYPE,
)
from agentclaw.community.core.bot_management.manifest_seam import (
    ManifestCreationSeam,
)
from agentclaw.community.core.bot_management.errors import (
    ApplicationCodingUnavailableError,
    BotTemplateInvalidError,
)
from agentclaw.community.core.bot_management.service_intake import (
    ServiceIntakeSeam,
    finish_service_intake,
    prepare_service_intake,
)
from agentclaw.community.core.bot_management.services.bot_service import (
    BotServiceError,
    validate_bot_name,
)
from agentclaw.community.core.mcp.services._defaults import get_default_cli_items
from agentclaw.community.core.mcp.services.passport_scope import (
    filter_passport_mcp_codes,
)
from agentclaw.community.core.workspace.runtime_identity import (
    AICODING_ENGINE_FORM,
    ENGINE_FORM_KEY,
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


def _reject_mixed_create_sources(spec: BotCreateSpec) -> None:
    """Reject requests combining the new and the legacy template inputs.

    A Core-level invariant, not only a schema rule: two sources would leave the
    override order ambiguous, so internal callers cannot bypass the check.
    """
    has_legacy_template = (
        spec.template_type is not None or spec.template_config is not None
    )
    if spec.engine_properties and has_legacy_template:
        raise BotTemplateInvalidError(
            "engine_properties cannot be combined with legacy template fields"
        )


# Legacy internal-engine values folded at the shared create seam. ``aicoding``
# is the internal implementation engine of ``claude_code``, not a product
# engine: new bots store the real engine and carry the form marker
# (``engine_form``) in their template snapshot instead (engine/form vocabulary
# split — docs/superpowers/specs/2026-08-31-engine-vocabulary-template-form-design.md).
_LEGACY_ENGINE_ALIASES = {AICODING_ENGINE_TYPE: CLAUDE_CODE_ENGINE_TYPE}


def _normalize_legacy_engine_alias(spec: BotCreateSpec) -> BotCreateSpec:
    """Fold legacy internal-engine values into the real engine (old-link compat).

    Internal callers (``/api/bots``) may still send ``engine_type="aicoding"``.
    The bot is created on the real engine (``claude_code``); a template-backed
    create records the server-managed ``engine_form`` marker in the template
    snapshot so runtime/bucket routing stays equivalent. A plain no-template
    bot has no form — it is simply a plain ``claude_code`` bot. The public
    surface never reaches this: it rejects internal engines with 400.

    Idempotent: a spec already on the real engine passes through unchanged.
    """
    real_engine = _LEGACY_ENGINE_ALIASES.get(
        normalize_engine_type(spec.engine_type, default="")
    )
    if real_engine is None:
        return spec
    template_config = spec.template_config
    if spec.template_type and template_config is not None:
        template_config = {**template_config, ENGINE_FORM_KEY: AICODING_ENGINE_FORM}
    logger.info(
        "[create_flow] folded legacy engine alias: requested_engine=%s "
        "engine=%s template_type=%s form_marker_written=%s",
        spec.engine_type,
        real_engine,
        spec.template_type,
        spec.template_type and template_config is not None,
    )
    return replace(
        spec,
        engine_type=real_engine,
        template_config=template_config,
    )


def _prepare_with_engine_strategy(
    spec: BotCreateSpec, context: BotCreateContext
) -> PreparedBotCreate:
    """Run the engine-selected strategy's create prevalidation."""
    strategy = get_engine_provisioning_registry().resolve(spec.engine_type)
    return strategy.prepare_create(
        engine_type=spec.engine_type,
        engine_properties=spec.engine_properties,
        bot_type=spec.bot_type,
        deployment_mode=context.deployment_mode,
        space_kind=context.space_kind,
        template_validation_mode=spec.template_validation_mode,
    )


def _prepare_legacy_non_application_template(
    spec: BotCreateSpec,
) -> PreparedBotCreate:
    """Sanitize established internal template inputs, keeping their type.

    Server-managed-field rejection honors the caller's validation mode: public
    inputs get the strict ownership rules, legacy internal snapshots may carry
    platform-managed fields.
    """
    if spec.template_type is None:
        if spec.template_config is not None:
            raise BotTemplateInvalidError("template_config requires template_type")
        return PreparedBotCreate()
    return PreparedBotCreate(
        template_type=spec.template_type,
        template_config=to_internal_template_config(
            spec.template_config,
            reject_server_managed_fields=(
                spec.template_validation_mode
                is BotCreateTemplateValidationMode.PUBLIC
            ),
        ),
    )


class AuthStatusUnavailableError(RuntimeError):
    """The passport service returned no authorization status at all.

    Subclasses ``RuntimeError`` so the internal route's catch-all keeps mapping
    it exactly as before, while the public surface can map it to a proper
    envelope instead of letting it escape as an unhandled 500.
    """

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


class AuthStatus(StrEnum):
    """Passport authorization states this flow branches on.

    Use these members instead of bare literals when comparing or emitting a
    status. The set is **not** closed: the value originates in the external
    passport service, so an unlisted state is possible and must survive the
    round trip (see :class:`AuthStatusResult`).
    """

    PENDING = "PENDING"
    ISSUED = "ISSUED"
    REJECTED = "REJECTED"


@dataclass
class AuthStatusResult:
    """Outcome of polling authorization: ``PENDING``, ``ISSUED`` (+bot), or other.

    ``bot`` is populated only on ``ISSUED``; an empty dict on the other states.

    ``status`` is deliberately a plain ``str`` rather than :class:`AuthStatus`:
    it holds whatever the external passport service reported, and the surfaces
    echo an unrecognized state back to the caller verbatim ("授权状态异常: X").
    Coercing to the enum would raise on a state the service adds later, turning
    a reportable condition into a 500 — so the enum names the known states while
    the carrier stays permissive.
    """

    status: str
    bot: dict[str, Any] = field(default_factory=dict)


def _prepare_create(
    *,
    spec: BotCreateSpec,
    context: BotCreateContext,
    bot_service: BotService,
) -> BotCreateSpec:
    """Apply shared creation policy before any external or persistence effects."""
    _reject_mixed_create_sources(spec)

    # Fold legacy internal-engine values before the strategy gates: the
    # aicoding strategy rejects application-coding creates on any engine other
    # than claude_code, so normalizing afterwards would keep rejecting the
    # old link's engine_type="aicoding" + applicationCoding combination.
    spec = _normalize_legacy_engine_alias(spec)

    if spec.engine_properties:
        prepared = _prepare_with_engine_strategy(spec, context)
    elif spec.template_type == "applicationCoding":
        # Keeping the "template_config" key preserves legacy intent when the
        # caller omits the config, so both input shapes share the Strategy gate.
        prepared = _prepare_with_engine_strategy(
            replace(spec, engine_properties={"template_config": spec.template_config}),
            context,
        )
    else:
        # Plain bots and other established template types keep the generic path;
        # the returned value must carry template_type through unchanged.
        prepared = _prepare_legacy_non_application_template(spec)

    if (
        prepared.requires_workspace_hosting
        and not bot_service.is_workspace_hosting_available()
    ):
        raise ApplicationCodingUnavailableError()
    # The translated form carries only the Core-internal template fields: the
    # bag has been consumed by the strategy, and leaving it set would make the
    # returned spec violate the mixed-source invariant enforced at entry —
    # a retry or future pending-intent replay that re-feeds the prepared
    # spec would be rejected as a mixed source.
    return replace(
        spec,
        template_type=prepared.template_type,
        template_config=prepared.template_config,
        engine_properties={},
    )


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
    use_first_passport: bool,
) -> dict[str, Any] | None:
    """Apply for the bot's Passport, normalizing non-Passport failures.

    ``PassportError`` propagates as-is (each surface maps it to its own code);
    any other apply failure becomes a ``BotServiceError`` so it keeps the
    "Passport apply failed" mapping rather than falling into a generic bucket.
    """
    # ``use_first_passport`` decides, and nothing else: the first personal Bot
    # takes applyFirst (skipping approval), everything else applyAgent. Service
    # Bots do not consume that eligibility; soft-delete filtering is the
    # repository query's job.
    #
    # **Every tenant is treated the same.** This was once gated on
    # ``get_current_avernet_tenant() == DEFAULT_AVERNET_TENANT`` too, so a
    # non-default tenant took applyFirst *unconditionally* — #556's reading that
    # approval does not apply to external tenants. That made
    # ``use_first_passport=False`` a request this function could silently
    # decline, and W13 is the caller that cannot survive it: submission passes
    # ``False`` precisely to get an authorization URL, and on a non-default
    # tenant got a bare token instead — a ``202`` carrying two empty strings and
    # no way to authorize the creation it had just started.
    if not use_first_passport:
        apply = passport_plugin.apply_agent_passport
    else:
        apply = passport_plugin.apply_first_agent_passport
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
            passport["status"] = AuthStatus.ISSUED
        ext["passport"] = passport
    return ext or None


def _require_agent_code(agent_code: object, *, bot_id: str) -> str:
    """Return the issued Passport agent code or reject an incomplete identity."""
    if not isinstance(agent_code, str) or not agent_code.strip():
        raise PassportError(
            f"Passport returned no agent_code for issued bot {bot_id}"
        )
    return agent_code


def _query_agent_code(
    passport_plugin: PassportPlugin, *, bot_id: str, user_id: str
) -> str:
    """Read the agent code required to complete an issued authorization."""
    try:
        info = passport_plugin.query_agent_passport(
            bot_id=bot_id, owner_workno=user_id
        )
    except PassportError:
        raise
    except Exception as exc:  # noqa: BLE001 — normalize plugin implementations
        raise PassportError(
            f"Passport identity query failed for bot {bot_id}: {exc}"
        ) from exc
    return _require_agent_code(
        info.get("agent_code") if info else None,
        bot_id=bot_id,
    )


def _record_owner_relationship(
    auth_rel_plugin: AuthRelationshipPlugin,
    *,
    user_id: str,
    agent_code: str,
    nick_name: str,
    bot_id: str,
) -> None:
    """Create the owner→bot relationship or fail the completed-create contract."""
    try:
        auth_result = auth_rel_plugin.create_relationship(
            work_no=user_id,
            agent_code=agent_code,
            description="Bot owner default authorization",
            operator_work_no=user_id,
            operator_name=nick_name,
        )
    except AuthRelationshipError:
        raise
    except Exception as exc:  # noqa: BLE001 — normalize plugin implementations
        raise AuthRelationshipError(
            f"authorization relationship write failed for bot {bot_id}: {exc}"
        ) from exc
    if auth_result is None:
        raise AuthRelationshipError(
            f"authorization relationship write failed for bot {bot_id}"
        )
    logger.info(
        "[create_flow] Created owner auth relationship: bot_id=%s owner=%s "
        "agent_code=%s auth_id=%s",
        bot_id, user_id, agent_code, auth_result.get("auth_id"),
    )


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
    service_intake_seam: ServiceIntakeSeam | None = None,
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
    spec, service_intake = prepare_service_intake(spec, context, service_intake_seam)

    # Creation policy is evaluated here, rather than in either transport, so no
    # caller can bypass template/combination rules before Passport or writes.
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

    passport_token = passport_result.get("token") if passport_result else None

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
        space_id=spec.space_id,
        space_quota=context.space_quota,
    )

    _record_owner_relationship(
        auth_rel_plugin, user_id=user_id, agent_code=agent_code,
        nick_name=nick_name, bot_id=bot_id,
    )

    if service_intake:
        result = finish_service_intake(
            bot_id=bot_id, user_id=user_id, bot_service=bot_service, seam=service_intake_seam
        )

    return Created(
        bot=result,
        is_first_bot=is_first_bot,
        passport_token=passport_token,
    )


@dataclass(frozen=True)
class ManifestCreationSubmitted:
    """What submitting a create-from-manifest request answers with.

    No state field, deliberately: the state vocabulary belongs to the poll and
    appears nowhere else, so no terminal value can ever be returned by
    submission. A caller that has just submitted is, by construction, awaiting
    authorization.
    """

    bot_id: str
    iframe_url: str | None
    redirect_url: str | None
    #: The **prepared** spec and context, as the creation will actually be
    #: completed. Returned rather than left for the caller to rebuild, because
    #: ``_prepare_create`` can rewrite the engine and what the job freezes must
    #: be the engine the manifest was validated against — not the one the
    #: request happened to name. See :func:`creation_spec_to_payload`.
    spec: BotCreateSpec
    context: BotCreateContext



def submit_bot_creation_with_manifest(
    *,
    user_id: str,
    bot_id: str,
    document: str,
    modifier: str,
    spec: BotCreateSpec,
    context: BotCreateContext,
    bot_service: BotService,
    passport_plugin: PassportPlugin,
    skill_set_factory: SkillSetServiceFactory,
    manifest_seam: ManifestCreationSeam,
) -> ManifestCreationSubmitted:
    """Validate, store the manifest, apply for a Passport — and stop.

    **This never creates the bot**, which is the difference from
    :func:`create_bot_with_authorization`. That function creates inline when
    Passport hands back a token immediately; here the creation job owns creation
    on every path, so there is one sequence rather than two and the
    pre-container phase cannot be skipped by a lucky Passport response. If a
    token does come back at once, the job's first run simply sees ``ISSUED`` and
    proceeds — no special case.

    **The manifest is validated before Passport is applied for**, in the same
    breath as quota, name and engine. A caller must never complete an
    authorization only to be told their document was invalid: that wastes their
    time and burns a Passport application. It is stored immediately afterwards,
    which is what makes "the manifest that was validated is the manifest that is
    applied" structural — the caller submits it once and the poll never accepts
    another.

    Ordering, and every line of it matters:

    1. ``_prepare_create`` — the shared creation policy, which may rewrite the
       engine (legacy aliases), so everything after it sees the engine the bot
       will actually run.
    2. the name check, then the platform preflight (quota, reserved engines).
    3. the manifest preflight, against **the prepared spec's** engine.
    4. persist, keyed by the same ``entity_id`` ``create_bot`` will resolve.
    5. the Passport application.
    """
    spec = _prepare_create(spec=spec, context=context, bot_service=bot_service)
    bot_name = validate_bot_name(spec.bot_name) if spec.bot_name is not None else None
    bot_service.check_create_bot_preflight(
        user_id=user_id,
        bot_id=bot_id,
        engine_type=spec.engine_type,
        bot_name=bot_name,
        space_id=spec.space_id,
        space_quota=context.space_quota,
    )

    # Raises ManifestValidationError with every reason at once. Before Passport.
    manifest_seam.preflight(
        document=document,
        engine_type=spec.engine_type,
        bot_type=spec.bot_type,
    )
    # The seam resolves the storage key and returns it: this module must not
    # import the manifest package (that closes a cycle through the creation
    # graph), and the key's rule belongs with the storage that depends on it.
    entity_id = manifest_seam.persist(
        spec_entity_id=spec.entity_id,
        bot_id=bot_id,
        document=document,
        modifier=modifier,
        engine_type=spec.engine_type,
        bot_type=spec.bot_type,
    )

    # Everything after the manifest is stored is one unit, and it has to be.
    #
    # The document is now on disk under a ``bot_id`` the caller has not been told
    # about yet. If the Passport application raises, or returns nowhere to send
    # the user, or the durable handoff fails, the request ends in an error and
    # that row is unreachable: no bot record for ordinary deletion to find, and
    # no job to expire and clean it up. Compensating here is what keeps "the
    # rows this endpoint creates are bounded by their own jobs" true — before a
    # job exists there is no job to bound them, so this is the only place that
    # can.
    try:
        return _apply_and_hand_off(
            manifest_seam=manifest_seam,
            passport_plugin=passport_plugin,
            skill_set_factory=skill_set_factory,
            user_id=user_id,
            bot_id=bot_id,
            entity_id=entity_id,
            bot_name=bot_name,
            spec=spec,
            context=context,
        )
    except Exception:
        # Best-effort by construction: ``discard`` never raises, so a cleanup
        # that cannot run does not replace the caller's real error with its own.
        manifest_seam.discard(entity_id=entity_id, bot_id=bot_id)
        raise


def _apply_and_hand_off(
    *,
    manifest_seam: ManifestCreationSeam,
    passport_plugin: PassportPlugin,
    skill_set_factory: SkillSetServiceFactory,
    user_id: str,
    bot_id: str,
    entity_id: str,
    bot_name: str | None,
    spec: BotCreateSpec,
    context: BotCreateContext,
) -> ManifestCreationSubmitted:
    """Apply for the Passport, then hand the creation to its durable job.

    Split out so the compensating cleanup above wraps exactly the steps that can
    strand the stored manifest, and nothing else.

    The job is started **here** rather than by the caller: it is the last step of
    submission, and a route that started it would be the one place a failure
    could leave a stored manifest with nothing to clean it up.
    """
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
        # **No first-bot skip on this surface.** ``create_bot_with_authorization``
        # asks Passport to skip approval for a user's very first bot; this
        # endpoint deliberately does not, so an OpenAPI client integrates
        # against one flow rather than two.
        #
        # It governs every tenant, which it did not always: while
        # ``_apply_passport`` consulted the tenant, this ``False`` was honoured
        # on the default one and ignored elsewhere, so a non-default tenant got
        # ``applyFirst`` — a bare token, no URL, and a ``202`` the caller could
        # not act on. That branch is gone; see ``_apply_passport``.
        use_first_passport=False,
    )

    iframe_url = passport_result.get("iframe_url") if passport_result else None
    redirect_url = passport_result.get("redirect_url") if passport_result else None
    token = passport_result.get("token") if passport_result else None
    if not token and not iframe_url and not redirect_url:
        # Neither a token nor anywhere to send the user. No bot was created, but
        # the stored manifest is real — the caller above discards it — and the
        # caller would otherwise be left polling a creation nobody can authorize.
        raise PassportError(
            f"Passport returned no token and no authorization URL for bot {bot_id}"
        )

    # The durable job, last: its first step reads the authorization that the
    # application above has only just made, so starting it earlier would only
    # buy a first run that finds nothing.
    manifest_seam.start_job(
        bot_id=bot_id,
        entity_id=entity_id,
        user_id=user_id,
        document_owner=user_id,
        spec=creation_spec_to_payload(spec, context),
        iframe_url=iframe_url,
        redirect_url=redirect_url,
    )
    return ManifestCreationSubmitted(
        bot_id=bot_id,
        iframe_url=iframe_url,
        redirect_url=redirect_url,
        spec=spec,
        context=context,
    )


def creation_spec_to_payload(
    spec: BotCreateSpec, context: BotCreateContext
) -> dict[str, Any]:
    """Freeze a creation's attributes so nothing has to be supplied again.

    This is what lets the poll take a ``bot_id`` and nothing else: the server
    already holds what the creation was for. Enums are stored by value because
    the payload is JSON in a database column, and the *prepared* spec is what
    callers should freeze — ``_prepare_create`` can rewrite the engine, and the
    completion must use the engine the manifest was validated against.
    """
    return {
        "entity_id": spec.entity_id,
        "entity_type": spec.entity_type,
        "engine_type": spec.engine_type,
        "bot_type": spec.bot_type,
        "bot_name": spec.bot_name,
        "bot_desc": spec.bot_desc,
        "avatar_url": spec.avatar_url,
        "share_policy": spec.share_policy,
        "template_type": spec.template_type,
        "template_config": spec.template_config,
        "template_validation_mode": spec.template_validation_mode.value,
        "space_id": spec.space_id,
        "engine_properties": spec.engine_properties,
        **context.as_payload(),
    }


def creation_spec_from_payload(
    payload: dict[str, Any],
) -> tuple[BotCreateSpec, BotCreateContext]:
    """The inverse. Raises ``KeyError``/``ValueError`` on a payload it cannot read."""
    return (
        BotCreateSpec(
            entity_id=payload["entity_id"],
            entity_type=payload.get("entity_type", "staff"),
            engine_type=payload["engine_type"],
            bot_type=payload["bot_type"],
            bot_name=payload.get("bot_name"),
            bot_desc=payload.get("bot_desc"),
            avatar_url=payload.get("avatar_url"),
            share_policy=payload.get("share_policy"),
            template_type=payload.get("template_type"),
            template_config=payload.get("template_config"),
            template_validation_mode=BotCreateTemplateValidationMode(
                payload["template_validation_mode"]
            ),
            space_id=payload.get("space_id"),
            engine_properties=payload.get("engine_properties") or {},
        ),
        BotCreateContext.from_payload(payload),
    )


def complete_manifest_creation(
    job_payload: dict[str, Any],
    *,
    bot_service: BotService,
    passport_plugin: PassportPlugin,
    auth_rel_plugin: AuthRelationshipPlugin,
    provision: bool = True,
) -> AuthStatusResult:
    """Finish a create-from-manifest by the ordinary completion path.

    Reuses :func:`complete_bot_authorization`, which is what keeps creation a
    single implementation. Idempotent on a supplied ``bot_id`` (``create_bot``
    returns the existing bot), so a re-claimed task cannot produce a second one.
    ``provision=False`` records the bot without provisioning it (W8).
    """
    spec, context = creation_spec_from_payload(job_payload["spec"])
    user_id = str(job_payload["user_id"])
    return complete_bot_authorization(
        user_id=user_id,
        nick_name=user_id,
        bot_id=str(job_payload["bot_id"]),
        spec=spec,
        context=context,
        bot_service=bot_service,
        passport_plugin=passport_plugin,
        auth_rel_plugin=auth_rel_plugin,
        provision=provision,
    )


def complete_bot_authorization(
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
    provision: bool = True,
    service_intake_seam: ServiceIntakeSeam | None = None,
) -> AuthStatusResult:
    """Poll Passport authorization for a pending bot; complete creation on ISSUED.

    ``PENDING`` returns immediately; ``ISSUED`` finishes the (idempotent) create
    and records the owner relationship; any other status is returned verbatim for
    the caller to map. Raises ``PassportError`` (query failure) and the create
    domain errors for the caller to map; a query that returns nothing raises
    ``RuntimeError`` (mapped to the internal 500). ``provision`` is
    ``create_bot``'s (W8): ``False`` records the bot and leaves provisioning to
    ``provision_bot``.

    ``cookie`` carries the browser session into the service layer — **bad
    practice**, see :func:`create_bot_with_authorization`; internal path only.
    """
    spec, service_intake = prepare_service_intake(spec, context, service_intake_seam)

    # Re-run the same policy on authorization completion because callers echo
    # the creation attributes and must not bypass the original create contract.
    spec = _prepare_create(spec=spec, context=context, bot_service=bot_service)

    auth_status = passport_plugin.query_auth_status(bot_id=bot_id, owner_workno=user_id)
    if not auth_status:
        raise AuthStatusUnavailableError("query auth status returned nothing")

    status = auth_status.get("status")
    if status != AuthStatus.ISSUED:
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
        space_id=spec.space_id,
        provision=provision,
        space_quota=context.space_quota,
    )

    _record_owner_relationship(
        auth_rel_plugin, user_id=user_id, agent_code=agent_code,
        nick_name=nick_name, bot_id=bot_id,
    )

    if service_intake:
        result = finish_service_intake(
            bot_id=bot_id, user_id=user_id, bot_service=bot_service, seam=service_intake_seam
        )

    return AuthStatusResult(status=AuthStatus.ISSUED, bot=result)
