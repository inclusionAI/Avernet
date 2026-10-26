"""Expert creation translates the original BotCreate body into a Manifest submission."""

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
from agentclaw.community.adapters.http.openapi_v1.responses import envelope_errors
from agentclaw.community.adapters.http.openapi_v1.bots.create_with_manifest import (
    submit_manifest_creation,
    BOT_QUOTA_CONFLICT_RESPONSES,
)
from agentclaw.community.adapters.http.openapi_v1.bots.schemas import BotCreate
from agentclaw.community.adapters.http.openapi_v1.bots.schemas_create_with_manifest import (
    BotCreateWithManifest,
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
    return await submit_manifest_creation(
        body=BotCreateWithManifest(
            **body.model_dump(), config_manifest=compiled.document
        ),
        request=request,
        owner_id=owner_id,
        owner_name=owner_name,
        caller=caller,
        bot_service=bot_service,
        bot_repo=bot_repo,
        passport_plugin=passport_plugin,
        skill_set_factory=skill_set_factory,
        space_context=space_context,
        manifest_seam=manifest_seam,
        expert_origin=compiled.origin,
    )
