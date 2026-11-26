import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute
from agentclaw.community.adapters.http.openapi_v1.contracts import (
    Envelope,
    ErrorEnvelope,
    Page,
)
from agentclaw.community.adapters.http.openapi_v1.dependencies import (
    Principal,
    require_principal,
)
from agentclaw.community.adapters.http.openapi_v1.errors import (
    BotOperationsForbiddenError,
)
from agentclaw.community.adapters.http.openapi_v1.principal import (
    caller_app_name,
    caller_names_a_user,
)
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
    page,
)
from agentclaw.community.api.managed_bots import ManagedBotServiceProtocol
from agentclaw.community.core.bot_management.engines.registry import resolve_status_ext
from agentclaw.community.core.bot_management.errors import BotAddressNotFoundError
from agentclaw.community.core.bot_management.readiness import is_bot_ready
from agentclaw.community.di import Injected
from agentclaw.community.di.openapi_bot_operations_config import (
    OpenApiBotOperationsConfig,
)
from agentclaw.community.log import get_logger
from agentclaw.community.utils.env_utils import get_current_env

from .schemas import (
    ManagedBotRestart,
    ManagedBotScope,
    ManagedBotSnapshot,
    ManagedBotStatus,
    ManagedBotStatusQueries,
    TemplateBotQueries,
    TemplateBotRow,
)

PrincipalDep = Annotated[Principal, Depends(require_principal)]
logger = get_logger()


def _trust(principal: Principal, config: OpenApiBotOperationsConfig) -> None:
    env = get_current_env()
    app_name = caller_app_name(principal)
    allowed = config.allows(env=env, tenant=principal.tenant, app_name=app_name)
    logger.info('event=managed_bot_application_admission allowed=%s env=%s tenant=%s app_name=%s has_user_identity=%s',
                allowed, env, principal.tenant, app_name, caller_names_a_user(principal))
    if not allowed:
        raise BotOperationsForbiddenError('calling application is not approved for Bot operations')


def require_managed_application(
    principal: PrincipalDep,
    config: OpenApiBotOperationsConfig = Injected(OpenApiBotOperationsConfig),
) -> None:
    _trust(principal, config)


async def restart_engine_targets(
    body: ManagedBotRestart,
    approved: Annotated[None, Depends(require_managed_application)],
    service: ManagedBotServiceProtocol = Injected(ManagedBotServiceProtocol),
) -> list[dict[str, Any]]:
    items = await asyncio.to_thread(
        service.statuses, template_key=body.template_key, targets=[(body.bot_id, body.owner_id)],
    )
    if not items:
        raise BotAddressNotFoundError('no Bot in the requested template scope')
    return items


async def status_engine_targets(
    body: ManagedBotStatusQueries,
    approved: Annotated[None, Depends(require_managed_application)],
    service: ManagedBotServiceProtocol = Injected(ManagedBotServiceProtocol),
) -> list[dict[str, Any]]:
    return await asyncio.to_thread(
        service.statuses,
        template_key=body.template_key,
        targets=[(target.bot_id, target.owner_id) for target in body.bots],
    )


router = APIRouter(
    prefix='/openapi/v1/bots',
    tags=['Bots'],
    route_class=PublicAPIRoute,
    responses={
        403: {
            'model': ErrorEnvelope,
            'description': 'The verified application is not approved for this environment and tenant.',
        },
    },
)


@router.post(
    '/template-queries',
    response_model=Envelope[Page[TemplateBotRow]],
    summary='Discover template-associated Bot runtimes',
)
@envelope_errors
async def query_template_bots(
    body: TemplateBotQueries,
    request: Request,
    principal: PrincipalDep,
    service: ManagedBotServiceProtocol = Injected(ManagedBotServiceProtocol),
    config: OpenApiBotOperationsConfig = Injected(OpenApiBotOperationsConfig),
) -> Envelope[Page[TemplateBotRow]]:
    """Approved server applications only, scoped by tenant and deployment environment."""
    _trust(principal, config)
    result = await asyncio.to_thread(service.query, **body.model_dump())
    return page(result['total'], [TemplateBotRow(**item) for item in result['items']], request)


@router.post(
    '/template-config-for-others',
    response_model=Envelope[ManagedBotSnapshot],
    summary='Read a managed draft template snapshot',
)
@envelope_errors
async def get_template_snapshot(
    body: ManagedBotScope,
    request: Request,
    principal: PrincipalDep,
    service: ManagedBotServiceProtocol = Injected(ManagedBotServiceProtocol),
    config: OpenApiBotOperationsConfig = Injected(OpenApiBotOperationsConfig),
) -> Envelope[ManagedBotSnapshot]:
    """Approved backend callers only. The sensitive result must remain server-side."""
    _trust(principal, config)
    snapshot = await asyncio.to_thread(service.snapshot_context, **body.model_dump())
    return envelope(ManagedBotSnapshot(env=get_current_env(), **snapshot), request)


@router.post(
    '/restart-for-others',
    response_model=Envelope[ManagedBotStatus],
    summary='Accept a managed draft Bot restart',
)
@envelope_errors
async def restart_for_others(
    body: ManagedBotRestart,
    request: Request,
    service: ManagedBotServiceProtocol = Injected(ManagedBotServiceProtocol),
) -> Envelope[ManagedBotStatus]:
    """Approved applications may restart another Owner's draft Bot without a per-Bot grant.

    HTTP 200 is acceptance or observation of an active operation, not completion.
    Uses the existing restart lock, operation deduplication and backup policy.
    Poll status-for-others for engine-owned operation progress and backup warnings.
    """
    bot = await service.restart(
        bot_id=body.bot_id,
        owner_id=body.owner_id,
        template_key=body.template_key,
        template_config=body.extra_configs.template_config if body.extra_configs else None,
        expected_template_config_hash=body.extra_configs.expected_template_config_hash if body.extra_configs else None,
    )
    ext = resolve_status_ext(bot, response_ext={}, public=True)
    namespace = ext.get('aicoding') or {}
    restart = namespace.get('restart')
    if restart and restart.get('operation_id') != bot.get('restart_operation_id'):
        namespace.pop('restart')
        if not namespace:
            ext.pop('aicoding', None)
    return envelope(
        ManagedBotStatus(
            bot_id=body.bot_id,
            owner_id=body.owner_id,
            status=bot.get('status') or '',
            is_ready=is_bot_ready(bot),
            ext=ext,
        ),
        request,
    )


@router.post(
    '/status-for-others',
    response_model=Envelope[Page[ManagedBotStatus]],
    summary='Read managed draft restart progress in a batch',
)
@envelope_errors
async def status_for_others(
    request: Request,
    items: Annotated[list[dict[str, Any]], Depends(status_engine_targets)],
) -> Envelope[Page[ManagedBotStatus]]:
    """Runtime status is separate from restart completion. Failed operations remain successful query results."""
    return page(len(items), [ManagedBotStatus(**item) for item in items], request)
