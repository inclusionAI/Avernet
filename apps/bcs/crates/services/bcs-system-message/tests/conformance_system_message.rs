use std::sync::Arc;

use bcs_system_message::producers::{
    bot_joined::BotJoinedMessageProducer, bot_left::BotLeftMessageProducer,
    generic::GenericNotificationMessageProducer,
    participant_mode_changed::ParticipantModeChangedMessageProducer,
};
use bcs_system_message::SystemMessageServiceImpl;
use bcs_test_support::{
    NoopBotDeliveryPort, NoopBotRegistryCoreService, NoopFrontendDeliveryPort, NoopGroupCoreService,
    NoopGroupMessageHistoryService, NoopSystemMessageDispatcher,
};

#[tokio::test]
async fn bot_joined_message_producer_passes_system_message_producer_service_contract() {
    let producer = BotJoinedMessageProducer::new(Arc::new(NoopGroupMessageHistoryService));

    bcs_test_support::contract::core::system_message_producer_service_contract_tests(&producer)
        .await;
}

#[tokio::test]
async fn generic_notification_message_producer_passes_system_message_producer_service_contract() {
    let producer = GenericNotificationMessageProducer;

    bcs_test_support::contract::core::system_message_producer_service_contract_tests(&producer)
        .await;
}

#[tokio::test]
async fn bot_left_message_producer_passes_system_message_producer_service_contract() {
    let producer = BotLeftMessageProducer;

    bcs_test_support::contract::core::system_message_producer_service_contract_tests(&producer)
        .await;
}

#[tokio::test]
async fn participant_mode_changed_message_producer_passes_system_message_producer_service_contract()
{
    let producer = ParticipantModeChangedMessageProducer;

    bcs_test_support::contract::core::system_message_producer_service_contract_tests(&producer)
        .await;
}

#[tokio::test]
async fn system_message_dispatcher_impl_passes_system_message_dispatcher_service_contract() {
    let dispatcher = bcs_system_message::SystemMessageDispatcherImpl::builder()
        .with_registry(Arc::new(NoopBotRegistryCoreService))
        .with_delivery(Arc::new(NoopBotDeliveryPort))
        .with_frontend_delivery(Arc::new(NoopFrontendDeliveryPort))
        .build()
        .expect("dispatcher");

    bcs_test_support::contract::core::system_message_dispatcher_service_contract_tests(&dispatcher)
        .await;
}

#[tokio::test]
async fn bot_hidden_notice_message_producer_passes_system_message_producer_service_contract() {
    let producer = bcs_system_message::producers::bot_hidden_notice::BotHiddenNoticeProducer;

    bcs_test_support::contract::core::system_message_producer_service_contract_tests(&producer)
        .await;
}

#[tokio::test]
async fn system_message_service_impl_passes_application_contract() {
    let svc = SystemMessageServiceImpl::new(
        Arc::new(NoopSystemMessageDispatcher),
        Arc::new(NoopGroupCoreService),
    );

    bcs_test_support::contract::application::system_message_service_contract_tests(&svc).await;
}
