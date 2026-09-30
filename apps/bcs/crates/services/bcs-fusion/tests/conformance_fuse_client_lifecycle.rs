use std::sync::Arc;

use bcs_fuse_client::FuseClient;
use bcs_fusion::FuseClientLifecycle;
use bcs_test_support::contract::lifecycle::service_lifecycle_contract_tests;

#[tokio::test]
async fn fuse_client_lifecycle_adapter_passes_contract() {
    let client = Arc::new(FuseClient::for_test_with_url("http://127.0.0.1:1").expect("client"));
    let lifecycle = FuseClientLifecycle::new(client);

    service_lifecycle_contract_tests(&lifecycle).await;
}
