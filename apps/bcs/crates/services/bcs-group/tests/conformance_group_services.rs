use std::sync::Arc;

use bcs_group::{GroupCore, GroupManagement};
use bcs_group_store::MemoryGroupRepo;
use bcs_service_api::port::repo::GroupRepoPort;
use bcs_test_support::{NoopBotRegistryCoreService, NoopFriendCoreService, NoopGroupCoreService};

#[tokio::test]
async fn group_core_wrapping_memory_repo_passes_core_contract() {
    let repo = Arc::new(MemoryGroupRepo::new());
    let svc = GroupCore::with_repo(repo.clone());

    bcs_test_support::contract::core::group_core_service_contract_tests(&svc).await;
    assert!(repo.get("bcs-contract-missing-group").await.is_none());
}

#[tokio::test]
async fn group_management_passes_application_contracts() {
    let svc = GroupManagement::with_defaults(
        Arc::new(NoopGroupCoreService),
        Arc::new(NoopBotRegistryCoreService),
        Arc::new(NoopFriendCoreService),
    );

    bcs_test_support::contract::application::group_query_service_contract_tests(&svc).await;
    bcs_test_support::contract::application::group_management_service_contract_tests(&svc).await;
    bcs_test_support::contract::application::workbench_session_service_contract_tests(&svc).await;
}
