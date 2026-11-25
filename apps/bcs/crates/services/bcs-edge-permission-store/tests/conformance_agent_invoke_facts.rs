use std::num::{NonZeroU32, NonZeroU64};
use std::sync::Arc;
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_edge_permission_store::agent_invoke::DbAgentInvokeStore;
use bcs_service_api::types::agent_invoke::{AgentInvokeLimits, AgentInvokeScope};
use bcs_test_support::contract::agent_invoke_facts::agent_invoke_fact_repo_port_contract_tests;

#[tokio::test]
async fn sqlite_fact_publisher_contract() {
    let db = Arc::new(LocalSqliteDbPlugin::new().unwrap());
    bcs_db_migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
    let scope = AgentInvokeScope { namespace: "contract".into(), env: "dev".into() };
    let n = NonZeroU32::new(2).unwrap();
    let s = DbAgentInvokeStore::new(db, bcs_db_api::DbSqlFlavor::Sqlite, scope.clone(),
        AgentInvokeLimits { max_out_degree: n, max_in_degree: n, max_pending_out: n, max_pending_in: n }, NonZeroU64::new(60_000).unwrap()).unwrap();
    s.initialize_scope().await.unwrap();
    agent_invoke_fact_repo_port_contract_tests(&s, scope, s.database_time_ms().await.unwrap() + 30_000).await;
}
