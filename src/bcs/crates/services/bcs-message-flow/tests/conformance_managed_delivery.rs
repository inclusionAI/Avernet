use bcs_message_flow::managed_delivery::ManagedMessageDelivery;
use bcs_message_store::MemoryMessageRepo;
use std::sync::Arc;

#[tokio::test]
async fn conformance_managed_message_delivery_service() -> Result<(), Box<dyn std::error::Error>> {
    let service = ManagedMessageDelivery::new(Arc::new(MemoryMessageRepo::new()));
    bcs_test_support::contract::application::message_delivery::managed_message_delivery_service_contract_tests(&service).await?;
    Ok(())
}
