use bcs_proposal_store::ProposalStore;

#[tokio::test]
async fn proposal_store_passes_proposal_repo_contract() {
    let repo = ProposalStore::new();

    bcs_test_support::contract::core::proposal_core_service_contract_tests(&repo).await;
    bcs_test_support::contract::repo::proposal_repo_contract_tests(&repo).await;
}
