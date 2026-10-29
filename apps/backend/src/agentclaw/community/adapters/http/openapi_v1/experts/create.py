"""Expert creation translates the original BotCreate body into a Manifest submission."""

from dataclasses import replace
from typing import Annotated
from fastapi import APIRouter, Path, Request
from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute
from agentclaw.community.adapters.http.openapi_v1.contracts import (
    Envelope,
    USER_SCOPED_403,
)
from agentclaw.community.adapters.http.openapi_v1.principal import (
    UserIdDep,
    OwnerNameDep,
    DelegatedUserDep,
)
from agentclaw.community.adapters.http.openapi_v1.dependencies import (
    resolve_avernet_tenant,
)
from fastapi import Depends
from agentclaw.community.adapters.http.openapi_v1.responses import accepted, envelope_errors
from agentclaw.community.adapters.http.openapi_v1.creation_grant import grant_the_creating_app, withdraw_the_creation_grant
from agentclaw.community.core.bot_management.create_flow import submit_bot_creation_with_manifest
from agentclaw.community.adapters.http.openapi_v1.bots.create_with_manifest import (
    prepare_manifest_creation,
    BOT_QUOTA_CONFLICT_RESPONSES,
)
from agentclaw.community.adapters.http.openapi_v1.bots.schemas import BotCreate
from agentclaw.community.adapters.http.openapi_v1.bots.schemas_create_with_manifest import (
    BotCreateWithManifestAccepted,
)
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.api.expert_creation_service import (
    ExpertCreationServiceProtocol,
)
from agentclaw.community.api.skill_set_service_factory import (
    SkillSetServiceFactoryProtocol,
)
from agentclaw.community.core.bot_inventory.protocols import (
    BusinessSpaceContextProtocol,
)
from agentclaw.community.core.bot_management.manifest_seam import ManifestCreationSeam
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.plugin_api.passport import PassportPlugin
from agentclaw.community.di import Injected

router = APIRouter(
    prefix="/openapi/v1/experts", tags=["experts"], route_class=PublicAPIRoute
)


@router.post(
    "/{expert_id}/bots",
    status_code=202,
    response_model=Envelope[BotCreateWithManifestAccepted],
    responses={**USER_SCOPED_403, **BOT_QUOTA_CONFLICT_RESPONSES},
    operation_id="create_bot_from_expert",
)
@envelope_errors
async def create_bot_from_expert(
    body: BotCreate,
    request: Request,
    expert_id: Annotated[
        str,
        Path(
            max_length=64,
            description="Stable expert identifier in the configured source.",
        ),
    ],
    tenant: Annotated[str, Depends(resolve_avernet_tenant)],
    owner_id: UserIdDep,
    owner_name: OwnerNameDep,
    caller: DelegatedUserDep,
    experts: ExpertCreationServiceProtocol = Injected(ExpertCreationServiceProtocol),
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    bot_repo: BotRepository = Injected(BotRepository),
    passport_plugin: PassportPlugin = Injected(PassportPlugin),
    skill_set_factory: SkillSetServiceFactoryProtocol = Injected(
        SkillSetServiceFactoryProtocol
    ),
    space_context: BusinessSpaceContextProtocol = Injected(
        BusinessSpaceContextProtocol
    ),
    manifest_seam: ManifestCreationSeam = Injected(ManifestCreationSeam),
) -> Envelope[BotCreateWithManifestAccepted]:
    """Create an owned Bot using the latest valid expert identity and original authorization flow."""
    compiled = experts.compile(tenant=tenant, expert_id=expert_id, engine=body.engine)
    bot_id, spec, context = prepare_manifest_creation(
        body=body, owner_id=owner_id, bot_repo=bot_repo, space_context=space_context,
    )
    grant_the_creating_app(caller, bot_id=bot_id)
    try:
        submitted = submit_bot_creation_with_manifest(
            user_id=owner_id, nick_name=owner_name, bot_id=bot_id,
            document=compiled.document, modifier=owner_id,
            spec=replace(spec, expert_origin=compiled.origin), context=context,
            bot_service=bot_service, passport_plugin=passport_plugin,
            skill_set_factory=skill_set_factory, manifest_seam=manifest_seam,
        )
    except Exception:
        withdraw_the_creation_grant(caller, bot_id=bot_id)
        raise
    return accepted(BotCreateWithManifestAccepted(
        bot_id=submitted.bot_id, iframe_url=submitted.iframe_url or "",
        redirect_url=submitted.redirect_url or "",
    ), request)
