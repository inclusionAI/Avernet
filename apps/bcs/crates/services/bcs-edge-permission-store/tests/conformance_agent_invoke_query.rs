#![allow(clippy::unwrap_used, reason = "conformance fixture setup")]
use bcs_edge_permission::agent_invoke::{
    AgentInvokeQueryApplicationService, AgentInvokeQueryPolicy,
};
use bcs_edge_permission_store::agent_invoke::DbAgentInvokeQueryStore;
use bcs_test_support::contract::agent_invoke_query::*;
#[path = "support/agent_invoke_sql_driver.rs"]
mod driver;
use driver::Driver;
async fn fixture() -> Driver {
    let db = std::sync::Arc::new(bcs_db_local::LocalSqliteDbPlugin::new().unwrap());
    bcs_db_migrations::run_sqlite_migrations(db.as_ref())
        .await
        .unwrap();
    let n = std::num::NonZeroU32::new(1000).unwrap();
    Driver::new_on_with_limits(
        db,
        bcs_db_api::DbSqlFlavor::Sqlite,
        "query-contract",
        bcs_service_api::types::agent_invoke::AgentInvokeLimits {
            max_out_degree: n,
            max_in_degree: n,
            max_pending_out: n,
            max_pending_in: n,
        },
    )
    .await
}
#[tokio::test]
async fn sql_query_application_contract() {
    let d = fixture().await;
    let _: &AgentInvokeQueryApplicationService = &d.query_service;
    agent_invoke_governance_query_service_contract_tests(&d).await;
}
#[tokio::test]
async fn sql_query_repo_contract() {
    let d = fixture().await;
    let _: &DbAgentInvokeQueryStore = d.query_repo.as_ref();
    agent_invoke_query_repo_port_contract_tests(&d).await;
}
#[test]
fn query_policy_contract() {
    agent_invoke_query_policy_core_service_contract_tests(&AgentInvokeQueryPolicy);
}
