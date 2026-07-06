use bcs_routing::MessageRouter;

#[tokio::test]
async fn message_router_passes_core_contract() {
    let router = MessageRouter::new();

    bcs_test_support::contract::core::routing_core_service_contract_tests(&router).await;
}
