use bcs_proposal::ProposalStore;

#[tokio::test]
async fn conformance_proposal_store() {
    let store = ProposalStore::new();
    bcs_test_support::contract::core::proposal_core_service_contract_tests(&store).await;
}
