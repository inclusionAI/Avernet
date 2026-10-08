use bcs_fusion::LocalFusionService;

#[tokio::test]
async fn conformance_local_fusion_service() {
    let svc = LocalFusionService::new("/tmp");
    bcs_test_support::contract::core::fusion_core_service_contract_tests(&svc).await;
}
