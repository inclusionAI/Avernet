use bcs_bot::core::BotCore;
use bcs_test_support::contract::lifecycle::service_lifecycle_contract_tests;

#[tokio::test]
async fn bot_core_passes_lifecycle_contract() {
    let core = BotCore::new();
    service_lifecycle_contract_tests(&core).await;
}
