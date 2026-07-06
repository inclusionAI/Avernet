use std::sync::Arc;

use bcs_config_api::BcsFuseConfig;
use bcs_fuse_client::FuseClient;
use bcs_fusion::FuseWorkerProfileService;

#[tokio::test]
async fn conformance_fuse_worker_profile_service() {
    let client = Arc::new(FuseClient::new(&BcsFuseConfig::default()).expect("construct client"));
    let svc = FuseWorkerProfileService::new(client);
    bcs_test_support::contract::application::worker_profile_service_contract_tests(&svc).await;
}
