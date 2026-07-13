use bcs_services_container::{BuilderError, ServicesBuilder};
use bcs_test_support::{
    NoopA2aChatRunService, NoopA2aChatService, NoopActorDirectoryService, NoopBotDeliveryPort,
    NoopBotDiscoveryService, NoopBotManagementService, NoopBotOnboardingService,
    NoopBotQueryService, NoopBotRegistryCoreService, NoopBotRunContextPort,
    NoopBotRuntimeConnectionService, NoopChannelService, NoopCollaborationRuntimeService,
    NoopCollaborationTemplateService, NoopFriendCoreService,
    NoopFriendService, NoopFrontendDeliveryPort,
    NoopFusionCoreService, NoopGroupCoreService, NoopGroupFusionService,
    NoopGroupManagementService, NoopGroupMessageHistoryService, NoopGroupProposalService,
    NoopGroupQueryService, NoopHumanActorService, NoopMessageFlowService,
    NoopOrganizationCoreService, NoopOrganizationManagementService, NoopProposalCoreService,
    NoopProviderBotCoreService, NoopProviderBotEventService, NoopProviderCoreService,
    NoopProviderManagementService, NoopRelationCoreService, NoopRoutingCoreService,
    NoopSecretService, NoopSessionManagementService, NoopSystemMessageService,
    NoopWorkbenchSessionService,
};
use std::sync::Arc;

#[test]
fn build_fails_fast_when_required_service_unset() {
    let result = ServicesBuilder::new().build();
    assert!(matches!(
        result,
        Err(BuilderError::MissingService(name)) if name == "registry"
    ));
}

#[test]
fn build_fails_fast_when_organization_core_unset() {
    let result = fully_wired_builder_except_organization().build();
    assert!(matches!(
        result,
        Err(BuilderError::MissingService(name)) if name == "organization"
    ));
}

#[test]
fn build_fails_fast_when_organization_management_unset() {
    let result = fully_wired_builder_except_organization_management().build();
    assert!(matches!(
        result,
        Err(BuilderError::MissingService(name)) if name == "organization_management"
    ));
}

#[test]
fn build_succeeds_when_all_required_services_set() {
    let services = ServicesBuilder::new()
        .registry(Arc::new(NoopBotRegistryCoreService))
        .group(Arc::new(NoopGroupCoreService))
        .routing(Arc::new(NoopRoutingCoreService))
        .fusion(Arc::new(NoopFusionCoreService))
        .proposal(Arc::new(NoopProposalCoreService))
        .friend(Arc::new(NoopFriendCoreService))
        .relation(Arc::new(NoopRelationCoreService))
        .message_flow(Arc::new(NoopMessageFlowService))
        .a2a_chat(Arc::new(NoopA2aChatService))
        .a2a_chat_runs(Arc::new(NoopA2aChatRunService))
        .collaboration_runtime(Arc::new(NoopCollaborationRuntimeService))
        .collaboration_templates(Arc::new(NoopCollaborationTemplateService))
        .bot_delivery(Arc::new(NoopBotDeliveryPort))
        .bot_run_context(Arc::new(NoopBotRunContextPort))
        .frontend_delivery(Arc::new(NoopFrontendDeliveryPort))
        .actor_directory(Arc::new(NoopActorDirectoryService))
        .friend_use_cases(Arc::new(NoopFriendService))
        .human_actors(Arc::new(NoopHumanActorService))
        .bot_onboarding(Arc::new(NoopBotOnboardingService))
        .bot_query(Arc::new(NoopBotQueryService))
        .bot_discovery(Arc::new(NoopBotDiscoveryService))
        .bot_management(Arc::new(NoopBotManagementService))
        .bot_runtime(Arc::new(NoopBotRuntimeConnectionService))
        .provider_core(Arc::new(NoopProviderCoreService))
        .provider_bot_core(Arc::new(NoopProviderBotCoreService))
        .provider_management(Arc::new(NoopProviderManagementService))
        .provider_bot_events(Arc::new(NoopProviderBotEventService))
        .organization(Arc::new(NoopOrganizationCoreService))
        .organization_management(Arc::new(NoopOrganizationManagementService))
        .group_query(Arc::new(NoopGroupQueryService))
        .group_management(Arc::new(NoopGroupManagementService))
        .workbench_sessions(Arc::new(NoopWorkbenchSessionService))
        .group_proposals(Arc::new(NoopGroupProposalService))
        .group_message_history(Arc::new(NoopGroupMessageHistoryService))
        .system_message(Arc::new(NoopSystemMessageService))
        .group_fusion(Arc::new(NoopGroupFusionService))
        .session_management(Arc::new(NoopSessionManagementService))
        .channel(Arc::new(NoopChannelService))
        .secret(Arc::new(NoopSecretService))
        .build()
        .expect("all required services are wired");
    assert!(Arc::ptr_eq(&services.registry, &services.registry));
}

fn fully_wired_builder_except_organization() -> ServicesBuilder {
    fully_wired_builder_without_organization_fields()
        .organization_management(Arc::new(NoopOrganizationManagementService))
}

fn fully_wired_builder_except_organization_management() -> ServicesBuilder {
    fully_wired_builder_without_organization_fields()
        .organization(Arc::new(NoopOrganizationCoreService))
}

fn fully_wired_builder_without_organization_fields() -> ServicesBuilder {
    ServicesBuilder::new()
        .registry(Arc::new(NoopBotRegistryCoreService))
        .group(Arc::new(NoopGroupCoreService))
        .routing(Arc::new(NoopRoutingCoreService))
        .fusion(Arc::new(NoopFusionCoreService))
        .proposal(Arc::new(NoopProposalCoreService))
        .friend(Arc::new(NoopFriendCoreService))
        .relation(Arc::new(NoopRelationCoreService))
        .message_flow(Arc::new(NoopMessageFlowService))
        .a2a_chat(Arc::new(NoopA2aChatService))
        .a2a_chat_runs(Arc::new(NoopA2aChatRunService))
        .collaboration_runtime(Arc::new(NoopCollaborationRuntimeService))
        .collaboration_templates(Arc::new(NoopCollaborationTemplateService))
        .bot_delivery(Arc::new(NoopBotDeliveryPort))
        .bot_run_context(Arc::new(NoopBotRunContextPort))
        .frontend_delivery(Arc::new(NoopFrontendDeliveryPort))
        .actor_directory(Arc::new(NoopActorDirectoryService))
        .friend_use_cases(Arc::new(NoopFriendService))
        .human_actors(Arc::new(NoopHumanActorService))
        .bot_onboarding(Arc::new(NoopBotOnboardingService))
        .bot_query(Arc::new(NoopBotQueryService))
        .bot_discovery(Arc::new(NoopBotDiscoveryService))
        .bot_management(Arc::new(NoopBotManagementService))
        .bot_runtime(Arc::new(NoopBotRuntimeConnectionService))
        .provider_core(Arc::new(NoopProviderCoreService))
        .provider_bot_core(Arc::new(NoopProviderBotCoreService))
        .provider_management(Arc::new(NoopProviderManagementService))
        .provider_bot_events(Arc::new(NoopProviderBotEventService))
        .group_query(Arc::new(NoopGroupQueryService))
        .group_management(Arc::new(NoopGroupManagementService))
        .workbench_sessions(Arc::new(NoopWorkbenchSessionService))
        .group_proposals(Arc::new(NoopGroupProposalService))
        .group_message_history(Arc::new(NoopGroupMessageHistoryService))
        .system_message(Arc::new(NoopSystemMessageService))
        .group_fusion(Arc::new(NoopGroupFusionService))
        .session_management(Arc::new(NoopSessionManagementService))
        .channel(Arc::new(NoopChannelService))
        .secret(Arc::new(NoopSecretService))
}
