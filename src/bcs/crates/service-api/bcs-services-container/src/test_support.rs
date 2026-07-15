//! Test-only service container constructors.

use std::sync::Arc;

use bcs_test_support::{
    NoopA2aChatRunService, NoopA2aChatService, NoopActorDirectoryService, NoopBotDeliveryPort,
    NoopBotDiscoveryService, NoopBotManagementService, NoopBotOnboardingService,
    NoopBotQueryService, NoopBotRegistryCoreService, NoopBotRunContextPort,
    NoopBotRuntimeConnectionService, NoopCollaborationRuntimeService,
    NoopCollaborationTemplateService, NoopFriendCoreService,
    NoopFriendService, NoopFrontendDeliveryPort,
    NoopFusionCoreService, NoopGroupCoreService, NoopGroupFusionService,
    NoopGroupManagementService, NoopGroupMessageHistoryService, NoopGroupProposalService,
    NoopGroupQueryService, NoopHumanActorService, NoopMessageFlowService,
    NoopOrganizationManagementService, NoopProposalCoreService,
    NoopProviderBotCoreService, NoopProviderBotEventService, NoopProviderCoreService,
    NoopProviderManagementService, NoopRelationCoreService, NoopRoutingCoreService,
    NoopSessionManagementService, NoopSystemMessageService, NoopWorkbenchSessionService,
};

use crate::services::{Services, ServicesBuilder};

/// Build a test services builder with every service wired to Noop.
pub fn with_all_noop() -> ServicesBuilder {
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
        .organization_management(Arc::new(NoopOrganizationManagementService))
        .group_query(Arc::new(NoopGroupQueryService))
        .group_management(Arc::new(NoopGroupManagementService))
        .workbench_sessions(Arc::new(NoopWorkbenchSessionService))
        .group_proposals(Arc::new(NoopGroupProposalService))
        .group_message_history(Arc::new(NoopGroupMessageHistoryService))
        .group_fusion(Arc::new(NoopGroupFusionService))
        .system_message(Arc::new(NoopSystemMessageService))
        .session_management(Arc::new(NoopSessionManagementService))
}

impl Services {
    /// Create a test services bundle with all Noop implementations.
    pub fn noop() -> Self {
        with_all_noop().build_for_test()
    }
}
