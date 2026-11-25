use bcs_edge_permission_store::agent_invoke::DbAgentInvokeStore;
use bcs_test_support::contract::agent_invoke_snapshots::agent_invoke_facts_port_contract_tests;
#[path = "support/agent_invoke_snapshots.rs"]
mod fixture;

#[tokio::test]
async fn sqlite_snapshot_port_and_real_application_conformance() {
    let fixture = fixture::Fixture::new().await;
    let store: &DbAgentInvokeStore = fixture.store.as_ref();
    assert!(store.scope_state().await.unwrap().runtime_enabled);
    agent_invoke_facts_port_contract_tests(&fixture).await;
}
