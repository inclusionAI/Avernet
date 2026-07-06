use std::sync::Arc;

use bcs_message_flow::a2a_chat::ChatRunStore;
use bcs_message_flow::{A2aChat, BcsGroupFusion, BcsGroupMessageHistory, BcsMessageFlow};
use bcs_test_support::{
    NoopBotDeliveryPort, NoopBotRegistryCoreService, NoopFriendCoreService,
    NoopFrontendDeliveryPort, NoopFusionCoreService, NoopGroupCoreService,
    NoopGroupHistoryBotRequestPort, NoopRoutingCoreService,
};

#[tokio::test]
async fn message_flow_passes_application_contract() {
    let svc = BcsMessageFlow::new(
        Arc::new(NoopGroupCoreService),
        Arc::new(NoopRoutingCoreService),
        Arc::new(NoopBotRegistryCoreService),
        Arc::new(NoopBotDeliveryPort),
        Arc::new(NoopFrontendDeliveryPort),
    );

    bcs_test_support::contract::application::message_flow_service_contract_tests(&svc).await;
}

#[tokio::test]
async fn a2a_chat_passes_application_contracts() {
    let svc = A2aChat::new(
        Arc::new(NoopBotDeliveryPort),
        Arc::new(ChatRunStore::new()),
        30_000,
        Arc::new(NoopBotRegistryCoreService),
        Arc::new(NoopFriendCoreService),
    );

    bcs_test_support::contract::application::a2a_chat_service_contract_tests(&svc).await;
    bcs_test_support::contract::application::a2a_chat_run_service_contract_tests(&svc).await;
}

#[tokio::test]
async fn group_fusion_passes_application_contract() {
    let svc = BcsGroupFusion::new(
        Arc::new(NoopGroupCoreService),
        Arc::new(NoopFusionCoreService),
    );

    bcs_test_support::contract::application::group_fusion_service_contract_tests(&svc).await;
}

#[tokio::test]
async fn group_message_history_passes_application_contract() {
    let svc = BcsGroupMessageHistory::new(
        Arc::new(NoopGroupCoreService),
        Arc::new(NoopBotRegistryCoreService),
        Arc::new(NoopBotDeliveryPort),
        Arc::new(NoopGroupHistoryBotRequestPort),
    );

    bcs_test_support::contract::application::group_message_history_service_contract_tests(&svc)
        .await;
}
