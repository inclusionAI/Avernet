use bcs_leader_election::StandaloneLeaderElection;
use bcs_test_support::contract::lifecycle::service_lifecycle_contract_tests;
use bcs_test_support::contract::port::leader_election_port_contract_tests;

#[tokio::test]
async fn standalone_leader_election_passes_port_contract() {
    let election = StandaloneLeaderElection::new("contract-node");
    leader_election_port_contract_tests(&election).await;
}

#[tokio::test]
async fn standalone_leader_election_passes_lifecycle_contract() {
    let election = StandaloneLeaderElection::new("contract-node");
    service_lifecycle_contract_tests(&election).await;
}
