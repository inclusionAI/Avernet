"""Service API gate: every registered Protocol matches its concrete service.

``api/README.md`` has promised this file in two places since the Service API
layer was introduced — it was never written, so nothing checked that a concrete
service still satisfies the Protocol adapters inject. The README states the
contract:

    Conformance is checked **structurally**, and a concrete service *may* also
    inherit its Protocol. Either way ``test_service_api_conformance.py``
    parametrizes over every ``(Protocol, ConcreteService)`` pair and asserts
    ``issubclass`` against the ``@runtime_checkable`` Protocol — so a missing
    or renamed method on the concrete class fails CI rather than only showing
    up as a router-time ``AttributeError``.

An inheriting service whose Protocol members are all ``@abstractmethod`` already
fails at construction, so for those pairs this file is a backstop; it stays the
only gate for the structural-only services, and its signature check below
catches drift that inheritance does not.

Three checks per pair, because ``issubclass`` on a ``runtime_checkable`` Protocol
verifies method **names only**:

1. ``issubclass`` — catches a removed or renamed method (the README's contract).
2. Full signature equality — parameter names, **kinds** and defaults, plus
   coroutine status. Comparing names alone still passes when a method turns
   from ``async def`` into ``def`` (the adapter then awaits a non-awaitable) or
   when a keyword-only parameter becomes positional-only (the adapter then
   calls it by keyword). Both fail every affected request while the gate stays
   green, so the comparison has to cover the whole shape.

3. No leftover ``__abstractmethods__``. Checks 1 and 2 are *blind* for a
   service that inherits its Protocol: drop a method and the Protocol's own
   ``...`` stub is inherited in its place, so the name still resolves and the
   signature is compared against itself. Both pass on a class that cannot even
   be constructed, moving the failure from CI to DI startup.

``_PAIRS`` starts with the contract added in this PR. It is a registry, not a
discovery walk: most Protocols in ``api/`` still declare
``*args: Any, **kwargs: Any``, against which a signature check is vacuous, so
listing them here would assert nothing while implying coverage. Add a pair when
its Protocol is given real signatures.
"""

from __future__ import annotations

import inspect

import pytest

from agentclaw.community.api.bot_dormant_service import (
    BotDormantActivateServiceProtocol,
)
from agentclaw.community.api.bot_runtime_projector import (
    BotRuntimeProjectorProtocol,
)
from agentclaw.community.api.collaborator_service import CollaboratorServiceProtocol
from agentclaw.community.api.installation_backfill_service import (
    InstallationBackfillServiceProtocol,
)
from agentclaw.community.api.bot_inventory_service import BotInventoryServiceProtocol
from agentclaw.community.api.bot_startup_script_service import (
    BotStartupScriptServiceProtocol,
)
from agentclaw.community.api.bot_config_manifest_apply_service import (
    BotConfigManifestApplyServiceProtocol,
)
from agentclaw.community.api.bot_config_manifest_service import (
    BotConfigManifestServiceProtocol,
)
from agentclaw.community.api.source_credential_service import (
    SourceCredentialServiceProtocol,
)
from agentclaw.community.api.bot_space_service import BotSpaceServiceProtocol
from agentclaw.community.api.engine_config_service import EngineConfigServiceProtocol
from agentclaw.community.api.engine_connection_service import (
    EngineConnectionServiceProtocol,
)
from agentclaw.community.api.bot_app_grant_service import (
    BotAppGrantServiceProtocol,
)
from agentclaw.community.api.engine_runtime_service import EngineRuntimeRelayProtocol
from agentclaw.community.api.health_diagnosis_service import (
    HealthDiagnosisServiceProtocol,
)
from agentclaw.community.api.expert_chat_instance_service import (
    ExpertChatInstanceServiceProtocol,
)
from agentclaw.community.core.expert_chat.expert_chat_instance_service_protocol import (
    ExpertChatInstanceServiceProtocol as CoreExpertChatInstanceServiceProtocol,
)
from agentclaw.community.core.expert_chat.services.expert_chat_instance_service import (
    ExpertChatInstanceService,
)
from agentclaw.community.api.local_bot_workflow_service import (
    LocalBotWorkflowServiceProtocol,
)
from agentclaw.community.api.skill_query_service import (
    SkillQueryServiceProtocol,
)
from agentclaw.community.api.skill_metadata_parser import (
    SkillMetadataParserProtocol,
)
from agentclaw.community.api.skill_center_gateway_service import (
    SkillCenterGatewayServiceProtocol,
)
from agentclaw.community.api.local_skill_upload_service import (
    LocalSkillUploadServiceProtocol,
)
from agentclaw.community.api.direct_activation_service import (
    DirectActivationServiceProtocol,
)
from agentclaw.community.api.local_skill_delete_service import (
    LocalSkillDeleteServiceProtocol,
)
from agentclaw.community.api.skill_set_management_service import (
    SkillSetManagementServiceProtocol,
)
from agentclaw.community.api.skill_version_materializer import (
    SkillVersionMaterializerProtocol,
)
from agentclaw.community.api.skill_center_reference_service import (
    SkillCenterReferenceServiceProtocol,
)
from agentclaw.community.api.skill_center_sync_service import (
    SkillCenterSyncServiceProtocol,
)
from agentclaw.community.api.track_latest import TrackLatestServiceProtocol
from agentclaw.community.api.market_favorite_service import (
    MarketFavoriteServiceProtocol,
)
from agentclaw.community.api.repository_catalog_service import (
    RepositoryCatalogServiceProtocol,
)
from agentclaw.community.api.service_publication_facade import (
    ServicePublicationFacadeProtocol,
)
from agentclaw.community.api.service_edit_lock_service import (
    ServiceEditLockServiceProtocol,
)
from agentclaw.community.api.service_artifact_lineage import (
    ServiceArtifactLineageReaderProtocol,
)
from agentclaw.community.api.space_skill_offline_service import (
    SpaceSkillOfflineServiceProtocol,
)
from agentclaw.community.api.space_service import (
    SpaceAccessServiceProtocol,
    SpaceMemberServiceProtocol,
    SpaceServiceProtocol,
)
from agentclaw.community.api.space_skill_grant_service import (
    SpaceSkillGrantServiceProtocol,
)
from agentclaw.community.api.space_skill_application_service import (
    SpaceSkillApplicationServiceProtocol,
)
from agentclaw.community.api.space_skill_version_query_service import (
    SpaceSkillVersionQueryServiceProtocol,
)
from agentclaw.community.api.space_skill_editor_request_service import (
    SpaceSkillEditorRequestServiceProtocol,
)
from agentclaw.community.api.draft_edit_lease_service import (
    DraftEditLeaseServiceProtocol,
)
from agentclaw.community.core.bot_dormant.activate_service import ActivateBotService
from agentclaw.community.core.bot_collaborator.services.collaborator_service import (
    CollaboratorService,
)
from agentclaw.community.core.bot_inventory.protocols import (
    BotInventoryBotPort,
    DesktopBotInventoryPort,
)
from agentclaw.community.core.bot_inventory.services.bot_inventory_service import (
    BotInventoryService,
)
from agentclaw.community.core.bot_inventory.services.local_bot_workflow import (
    LocalBotWorkflowService,
)
from agentclaw.community.core.bot_management.services.bot_service import BotService
from agentclaw.community.core.bot_management.services.bot_space_service import (
    BotSpaceService,
)
from agentclaw.community.core.bot_startup_script.services.startup_script_service import (
    BotStartupScriptService,
)
from agentclaw.community.core.bot_config_manifest.services.config_manifest_apply_service import (
    BotConfigManifestApplyService,
)
from agentclaw.community.core.bot_config_manifest.services.config_manifest_service import (
    BotConfigManifestService,
)
from agentclaw.community.core.bot_config_manifest.credentials.service import (
    SourceCredentialService,
)
from agentclaw.community.core.desktop_bot.services.desktop_bot_service import (
    DesktopBotService,
)
from agentclaw.community.core.engine_runtime.connection import EngineConnectionService
from agentclaw.community.core.engine_runtime.relay import EngineRuntimeRelay
from agentclaw.community.core.harness.services.health_diagnosis_service import (
    HealthDiagnosisService,
)
from agentclaw.community.core.services.engine_config import EngineConfigService
from agentclaw.community.core.skill_center.services.skill_query_service import (
    SkillQueryService,
)
from agentclaw.community.core.skill_center.services.skill_parser import SkillParser
from agentclaw.community.core.skill_center.services.skill_center_gateway_service import (
    SkillCenterGatewayService,
)
from agentclaw.community.core.skill_center.services.bot_runtime_projector import (
    BotRuntimeProjector,
)
from agentclaw.community.core.skill_center.services.installation_backfill_service import (
    InstallationBackfillService,
)
from agentclaw.community.core.skill_center.services.local_skill_upload_service import (
    LocalSkillUploadService,
)
from agentclaw.community.core.skill_center.services.direct_activation_service import (
    DirectActivationService,
)
from agentclaw.community.core.bot_app_grant.services import BotAppGrantService
from agentclaw.community.core.skill_center.services.local_skill_delete_service import (
    LocalSkillDeleteService,
)
from agentclaw.community.core.skill_center.services.repository_catalog_service import (
    RepositoryCatalogService,
)
from agentclaw.community.core.skill_center.services.skill_set_management_service import (
    SkillSetManagementService,
)
from agentclaw.community.core.skill_center.services.skill_version_materializer import (
    SkillVersionMaterializer,
)
from agentclaw.community.core.skill_center.services.skill_center_reference_service import (
    SkillCenterReferenceService,
)
from agentclaw.community.core.skill_center.services.skill_center_sync_service import (
    SkillCenterSyncService,
)
from agentclaw.community.core.skill_center.services.track_latest import TrackLatestService
from agentclaw.community.core.skill_center.services.space_skill_grant_service import (
    SpaceSkillGrantService,
)
from agentclaw.community.core.skill_center.services.space_skill_application_service import (
    SpaceSkillApplicationService,
)
from agentclaw.community.core.skill_center.services.space_skill_version_query_service import (
    SpaceSkillVersionQueryService,
)
from agentclaw.community.core.skill_center.services.space_skill_editor_request_service import (
    SpaceSkillEditorRequestService,
)
from agentclaw.community.core.skill_center.services.draft_edit_lease_service import (
    DraftEditLeaseService,
)
from agentclaw.community.core.market_favorites.services import MarketFavoriteService
from agentclaw.community.core.service_bot.services.service_publication_facade import (
    ServicePublicationFacade,
)
from agentclaw.community.core.service_bot.services.service_edit_lock_service import (
    ServiceEditLockService,
)
from agentclaw.community.core.service_bot.services.service_artifact_lineage_reader import (
    ServiceArtifactLineageReader,
)
from agentclaw.community.core.skill_center.services.space_skill_offline_service import (
    SpaceSkillOfflineService,
)
from agentclaw.community.core.spaces.services import (
    SpaceAccessService,
    SpaceMemberService,
    SpaceService,
)


# (Protocol, ConcreteService) pairs whose Protocol declares real signatures.
_PAIRS = [
    (ExpertChatInstanceServiceProtocol, ExpertChatInstanceService),
    (BotAppGrantServiceProtocol, BotAppGrantService),
    (CollaboratorServiceProtocol, CollaboratorService),
    (BotInventoryServiceProtocol, BotInventoryService),
    (BotStartupScriptServiceProtocol, BotStartupScriptService),
    (BotConfigManifestServiceProtocol, BotConfigManifestService),
    (SourceCredentialServiceProtocol, SourceCredentialService),
    (BotConfigManifestApplyServiceProtocol, BotConfigManifestApplyService),
    (BotSpaceServiceProtocol, BotSpaceService),
    (LocalBotWorkflowServiceProtocol, LocalBotWorkflowService),
    (BotDormantActivateServiceProtocol, ActivateBotService),
    (BotInventoryBotPort, BotService),
    (DesktopBotInventoryPort, DesktopBotService),
    (EngineConfigServiceProtocol, EngineConfigService),
    (EngineRuntimeRelayProtocol, EngineRuntimeRelay),
    (EngineConnectionServiceProtocol, EngineConnectionService),
    (HealthDiagnosisServiceProtocol, HealthDiagnosisService),
    (BotRuntimeProjectorProtocol, BotRuntimeProjector),
    (InstallationBackfillServiceProtocol, InstallationBackfillService),
    (SkillQueryServiceProtocol, SkillQueryService),
    (SkillMetadataParserProtocol, SkillParser),
    (SpaceSkillGrantServiceProtocol, SpaceSkillGrantService),
    (SpaceSkillApplicationServiceProtocol, SpaceSkillApplicationService),
    (SpaceSkillVersionQueryServiceProtocol, SpaceSkillVersionQueryService),
    (SpaceSkillEditorRequestServiceProtocol, SpaceSkillEditorRequestService),
    (DraftEditLeaseServiceProtocol, DraftEditLeaseService),
    (SkillCenterGatewayServiceProtocol, SkillCenterGatewayService),
    (LocalSkillUploadServiceProtocol, LocalSkillUploadService),
    (DirectActivationServiceProtocol, DirectActivationService),
    (LocalSkillDeleteServiceProtocol, LocalSkillDeleteService),
    (RepositoryCatalogServiceProtocol, RepositoryCatalogService),
    (SkillSetManagementServiceProtocol, SkillSetManagementService),
    (SkillVersionMaterializerProtocol, SkillVersionMaterializer),
    (SkillCenterReferenceServiceProtocol, SkillCenterReferenceService),
    (SkillCenterSyncServiceProtocol, SkillCenterSyncService),
    (TrackLatestServiceProtocol, TrackLatestService),
    (SpaceServiceProtocol, SpaceService),
    (SpaceAccessServiceProtocol, SpaceAccessService),
    (SpaceMemberServiceProtocol, SpaceMemberService),
    (MarketFavoriteServiceProtocol, MarketFavoriteService),
    (ServicePublicationFacadeProtocol, ServicePublicationFacade),
    (ServiceEditLockServiceProtocol, ServiceEditLockService),
    (ServiceArtifactLineageReaderProtocol, ServiceArtifactLineageReader),
    (SpaceSkillOfflineServiceProtocol, SpaceSkillOfflineService),
]

_IDS = [f"{p.__name__}->{c.__name__}" for p, c in _PAIRS]


def _protocol_methods(protocol: type) -> list[str]:
    """Method names the Protocol declares (excluding typing/object machinery)."""
    return sorted(
        name
        for name, value in vars(protocol).items()
        if callable(value) and not name.startswith("_")
    )


@pytest.mark.unit
@pytest.mark.parametrize(("protocol", "concrete"), _PAIRS, ids=_IDS)
def test_concrete_service_satisfies_protocol(protocol, concrete) -> None:
    """The README's structural gate: a missing/renamed method fails CI."""
    assert issubclass(concrete, protocol), (
        f"{concrete.__name__} no longer satisfies {protocol.__name__}. "
        f"Protocol declares: {_protocol_methods(protocol)}; "
        f"missing on the concrete class: "
        f"{[m for m in _protocol_methods(protocol) if not hasattr(concrete, m)]}"
    )


@pytest.mark.unit
@pytest.mark.parametrize(("protocol", "concrete"), _PAIRS, ids=_IDS)
def test_protocol_signatures_match_the_implementation(protocol, concrete) -> None:
    """Parameter names/kinds must match — ``issubclass`` only checks names.

    A renamed keyword argument keeps the method present, so the check above
    stays green while every call through the Protocol raises ``TypeError``.
    """
    mismatches: list[str] = []
    for name in _protocol_methods(protocol):
        impl = getattr(concrete, name, None)
        if impl is None:
            continue  # reported by the issubclass test
        declared_fn = getattr(protocol, name)
        declared = inspect.signature(declared_fn)
        actual = inspect.signature(impl)

        # Awaitability: adapters `await` these, so async→sync breaks every call.
        if inspect.iscoroutinefunction(declared_fn) != inspect.iscoroutinefunction(
            impl
        ):
            mismatches.append(
                f"{name}: protocol "
                f"{'async' if inspect.iscoroutinefunction(declared_fn) else 'sync'} "
                f"!= impl {'async' if inspect.iscoroutinefunction(impl) else 'sync'}"
            )

        # Names, kinds AND defaults — a keyword-only parameter turning
        # positional-only keeps the name but breaks every keyword call site.
        def _shape(sig: inspect.Signature) -> list[tuple[str, int, object]]:
            return [(p.name, p.kind.value, p.default) for p in sig.parameters.values()]

        if _shape(declared) != _shape(actual):
            mismatches.append(
                f"{name}: protocol {_shape(declared)} != impl {_shape(actual)}"
            )
    assert not mismatches, (
        f"{concrete.__name__} drifted from {protocol.__name__}:\n  "
        + "\n  ".join(mismatches)
    )


@pytest.mark.unit
def test_expert_chat_instance_api_reexports_owning_core_protocol() -> None:
    """The adapter and concrete service must share one Protocol authority."""
    assert ExpertChatInstanceServiceProtocol is CoreExpertChatInstanceServiceProtocol


@pytest.mark.unit
def test_registry_is_not_empty() -> None:
    """Guard the guard — an emptied registry would pass everything silently."""
    assert _PAIRS, "no (Protocol, ConcreteService) pairs registered"


@pytest.mark.unit
@pytest.mark.parametrize(("protocol", "concrete"), _PAIRS, ids=_IDS)
def test_concrete_service_is_not_left_abstract(protocol, concrete) -> None:
    """A service that inherits its Protocol must implement every member.

    ``api/README.md`` permits a concrete service to inherit its Protocol. That
    creates a blind spot in the two checks above — a dropped method is silently
    replaced by the Protocol's inherited ``...`` stub, which satisfies both — so
    check the ABC side too: anything left abstract cannot be constructed and
    would fail at DI startup instead of here.

    Structural services are unaffected; they inherit nothing and have no
    ``__abstractmethods__``.
    """
    left_abstract = sorted(getattr(concrete, "__abstractmethods__", frozenset()))
    assert not left_abstract, (
        f"{concrete.__name__} inherits {protocol.__name__} but leaves "
        f"{left_abstract} unimplemented — the class is abstract and DI would "
        "fail at startup."
    )
