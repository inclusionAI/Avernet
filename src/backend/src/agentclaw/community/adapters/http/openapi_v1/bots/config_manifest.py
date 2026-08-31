"""A bot's configuration manifest — ``/openapi/v1/bots/{bot_id}/config-manifest``.

Four operations on their own router, mounted before the ``{bot_id}`` wildcard
group like every other bot-component group.

**The whole group is behind a feature switch.** These are public addresses, and
until the apply engine lands an accepted manifest does nothing — a caller would
write one, receive a 200, and watch their bot stay exactly as it was. The switch
answers `404` rather than `503` for the same reason a bot the caller may not
reach answers 404: an address that is not being served should not confirm that
it exists. See ``core/bot_config_manifest/feature_flag.py`` for why it lifts at
W8 and why it is not what keeps this surface honest.

Nothing about *what a document may contain* is decided here. The router resolves
the bot, hands the service the two fields capabilities are answered from, and
shapes the result; the rules live in ``core/bot_config_manifest``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute
from agentclaw.community.adapters.http.openapi_v1.contracts import (
    CONFIG_MANIFEST_WRITE_RESPONSES,
    USER_SCOPED_403,
    BotIdPath,
    Deleted,
    Envelope,
)
from agentclaw.community.adapters.http.openapi_v1.errors import (
    ConfigManifestSurfaceDisabledError,
)
from agentclaw.community.adapters.http.openapi_v1.principal import (
    ActingCallerDep,
    UserIdDep,
)
from agentclaw.community.adapters.http.openapi_v1.responses import (
    deleted as deleted_envelope,
    envelope,
    envelope_errors,
)
from agentclaw.community.api.bot_config_manifest_service import (
    BotConfigManifestServiceProtocol,
)
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.core.bot_config_manifest.feature_flag import (
    config_manifest_surface_enabled,
)
from agentclaw.community.di import Injected

from .config_manifest_support import (
    audit_actor,
    capabilities_payload,
    manifest_payload,
    manifest_target,
)
from .schemas import ConfigManifest, ConfigManifestCapabilities, ConfigManifestWrite


def require_config_manifest_surface() -> None:
    """Refuse every operation in this group while the switch is off.

    Declared once, on the router, rather than per handler: the point of the
    switch is that the *surface* is not served, and a per-handler declaration is
    one a fifth route could be added without.
    """
    if not config_manifest_surface_enabled():
        raise ConfigManifestSurfaceDisabledError()


router = APIRouter(
    prefix="/openapi/v1/bots/{bot_id}",
    tags=["bots"],
    route_class=PublicAPIRoute,
    dependencies=[Depends(require_config_manifest_surface)],
)


@router.get(
    "/config-manifest",
    response_model=Envelope[ConfigManifest],
    responses=USER_SCOPED_403,
    operation_id="get_bot_config_manifest",
)
@envelope_errors
async def get_bot_config_manifest(
    bot_id: BotIdPath,
    request: Request,
    owner_id: UserIdDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    manifest_service: BotConfigManifestServiceProtocol = Injected(
        BotConfigManifestServiceProtocol
    ),
) -> Envelope[ConfigManifest]:
    """Read a bot's configuration manifest.

    A bot that has never had one reads as an **empty document**, not an error —
    a 404 here would make "has none" indistinguishable from "no such bot".

    The document is returned exactly as it was written, byte for byte, including
    the `script` body's quoting and whitespace.
    """
    bot = bot_service.get_bot(bot_id, owner_id)  # ownership/tenant guard
    entity_id = manifest_target(bot)
    record = manifest_service.get(entity_id=entity_id, bot_id=bot_id)
    return envelope(manifest_payload(bot_id, record), request)


@router.put(
    "/config-manifest",
    response_model=Envelope[ConfigManifest],
    responses=CONFIG_MANIFEST_WRITE_RESPONSES,
    operation_id="update_bot_config_manifest",
)
@envelope_errors
async def update_bot_config_manifest(
    bot_id: BotIdPath,
    body: ConfigManifestWrite,
    request: Request,
    owner_id: UserIdDep,
    caller: ActingCallerDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    manifest_service: BotConfigManifestServiceProtocol = Injected(
        BotConfigManifestServiceProtocol
    ),
) -> Envelope[ConfigManifest]:
    """Set or replace a bot's configuration manifest.

    **All-or-nothing.** A document is stored only if every part of it is valid
    and supported for this bot: one unsupported category refuses the whole
    document, nothing is written, and the `422` carries the full list of reasons
    with the offending entry named in each. Fixing a document is one pass, not a
    queue.

    Declaring a category this bot's engine cannot be given — or a source form
    the platform cannot yet resolve — is refused rather than stored, because a
    stored declaration nothing can act on is a silent no-op the caller would
    reasonably read as success. `GET …/config-manifest/capabilities` answers
    from the same rules, so it can never promise what this refuses.

    Storing a manifest applies nothing yet.
    """
    bot = bot_service.get_bot(bot_id, owner_id)  # ownership/tenant guard
    entity_id = manifest_target(bot)
    result = manifest_service.put(
        entity_id=entity_id,
        bot_id=bot_id,
        document=body.document,
        # From the verified caller, never the body — and naming the application
        # when one is acting, not the user it acted for.
        modifier=audit_actor(caller, owner_id),
        active_engine=bot.get("active_engine"),
        bot_type=bot.get("bot_type"),
    )
    return envelope(
        manifest_payload(bot_id, result.record, warnings=result.warnings), request
    )


@router.delete(
    "/config-manifest",
    response_model=Envelope[Deleted],
    responses=USER_SCOPED_403,
    operation_id="delete_bot_config_manifest",
)
@envelope_errors
async def delete_bot_config_manifest(
    bot_id: BotIdPath,
    request: Request,
    owner_id: UserIdDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    manifest_service: BotConfigManifestServiceProtocol = Injected(
        BotConfigManifestServiceProtocol
    ),
) -> Envelope[Deleted]:
    """Clear a bot's configuration manifest. Idempotent.

    Entities a previous apply produced are **not** removed: this clears the
    declaration, not the assets it named.
    """
    bot = bot_service.get_bot(bot_id, owner_id)  # ownership/tenant guard
    entity_id = manifest_target(bot)
    manifest_service.delete(entity_id=entity_id, bot_id=bot_id)
    return deleted_envelope(request)


@router.get(
    "/config-manifest/capabilities",
    response_model=Envelope[ConfigManifestCapabilities],
    responses=USER_SCOPED_403,
    operation_id="get_bot_config_manifest_capabilities",
)
@envelope_errors
async def get_bot_config_manifest_capabilities(
    bot_id: BotIdPath,
    request: Request,
    owner_id: UserIdDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    manifest_service: BotConfigManifestServiceProtocol = Injected(
        BotConfigManifestServiceProtocol
    ),
) -> Envelope[ConfigManifestCapabilities]:
    """Which manifest constructs this bot accepts, and why not when it does not.

    Answered by the same resolver `PUT` refuses with, so this can never claim
    support for something the next write rejects. A construct is a category, a
    top-level section (`script`), or a source form — a source with no resolver
    fails exactly the way an unsupported category does, so both are listed.

    An unrecognised engine supports nothing.
    """
    bot = bot_service.get_bot(bot_id, owner_id)  # ownership/tenant guard
    capabilities = manifest_service.capabilities_for_bot(bot)
    return envelope(capabilities_payload(bot_id, capabilities), request)
