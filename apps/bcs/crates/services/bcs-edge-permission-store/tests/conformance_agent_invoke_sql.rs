use bcs_edge_permission_store::agent_invoke::DbAgentInvokeCommandStore;
use bcs_test_support::contract::agent_invoke::*;
#[path = "support/agent_invoke_sql_driver.rs"]
mod driver;
use driver::Driver;
#[tokio::test]
async fn sql_lifecycle() {
    let d = Driver::new().await;
    let _: &DbAgentInvokeCommandStore = d.commands.as_ref();
    agent_invoke_grant_management_service_contract_tests(&d).await;
}
#[tokio::test]
async fn sql_replay() {
    run_agent_invoke_replay_contract(&Driver::new().await).await;
}
#[tokio::test]
async fn sql_invalidation() {
    agent_invocation_service_contract_tests(&Driver::new().await).await;
}
#[tokio::test]
async fn sql_terminals() {
    run_agent_invoke_request_terminals_contract(&Driver::new().await).await;
}
#[tokio::test]
async fn sql_fencing() {
    agent_invoke_command_repo_port_contract_tests(&Driver::new().await).await;
}
#[tokio::test]
async fn sql_requester_authority() {
    run_agent_invoke_requester_authority_contract(&Driver::new().await).await;
}
#[tokio::test]
async fn sql_limits() {
    run_agent_invoke_limits_contract(&Driver::new().await).await;
}
#[tokio::test]
async fn sql_atomic_limits() {
    run_agent_invoke_atomic_limits_contract(&Driver::new().await).await;
}
#[tokio::test]
async fn sql_approve_before_revoke() {
    run_agent_invoke_approve_revoke_contract(&Driver::new().await, true).await;
}
#[tokio::test]
async fn sql_revoke_before_approve() {
    run_agent_invoke_approve_revoke_contract(&Driver::new().await, false).await;
}
#[tokio::test]
async fn sql_governance_feed_contract() {
    let d = Driver::new().await;
    let store: &bcs_edge_permission_store::agent_invoke::DbAgentInvokeStore = d.store.as_ref();
    bcs_test_support::contract::agent_invoke_facts::agent_invoke_governance_repo_port_contract_tests(store,d.scope.clone()).await;
}
