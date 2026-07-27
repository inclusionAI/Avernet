use std::sync::Arc;

use bcs_channel::BcsChannelService;
use bcs_channel_api::ChannelProviderRegistry;
use bcs_channel_store::{
    MemoryChannelBindingRepo, MemoryConversationSessionRepo, MemoryHumanInputRequestRepo,
    MemoryImParticipantRepo,
};
use bcs_session_store::MemorySessionRepo;
use bcs_test_support::{
    NoopBotRegistryCoreService, NoopCollaborationRuntimeService, NoopGroupCoreService,
    NoopMessageFlowService, NoopSystemMessageService,
};

#[tokio::test]
async fn conformance_bcs_channel_service_session_channel_outbound_port() {
    let service = BcsChannelService::new(
        Arc::new(MemoryChannelBindingRepo::new("contract")),
        Arc::new(MemoryConversationSessionRepo::new()),
        Arc::new(MemoryImParticipantRepo::new()),
        Arc::new(MemoryHumanInputRequestRepo::new()),
        Arc::new(MemorySessionRepo::new()),
        Arc::new(NoopMessageFlowService),
        Arc::new(NoopSystemMessageService),
        Arc::new(NoopCollaborationRuntimeService),
        Arc::new(NoopGroupCoreService),
        Arc::new(NoopBotRegistryCoreService),
        Arc::new(ChannelProviderRegistry::empty()),
        "contract",
        Arc::new(|| 1),
        Arc::new(|| "contract-id".to_string()),
    );

    bcs_test_support::contract::port::session_channel_outbound_port_contract_tests(&service).await;
}
