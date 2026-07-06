use bcs_config_api::BcsFuseConfig;
use bcs_fusion::FuseBackedFusionService;

#[tokio::test]
async fn conformance_fuse_backed_fusion_service() {
    let svc = FuseBackedFusionService::new(&BcsFuseConfig::default(), "/tmp")
        .expect("construct fuse-backed fusion service");
    bcs_test_support::contract::core::fusion_core_service_contract_tests(&svc).await;
}
