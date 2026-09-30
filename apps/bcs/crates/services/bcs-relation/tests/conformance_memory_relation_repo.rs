use std::sync::Arc;

use bcs_relation::RelationCore;
use bcs_relation_store::MemoryRelationRepo;

#[tokio::test]
async fn memory_relation_store_passes_core_and_repo_contracts() {
    let repo = Arc::new(MemoryRelationRepo::new());
    let store = RelationCore::with_repo(repo.clone());

    bcs_test_support::contract::core::relation_core_service_contract_tests(&store).await;
    bcs_test_support::contract::repo::relation_repo_contract_tests(repo.as_ref()).await;
}
