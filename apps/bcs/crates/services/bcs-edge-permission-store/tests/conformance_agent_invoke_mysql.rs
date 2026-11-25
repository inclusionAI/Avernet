//! Run explicitly against a disposable, EMPTY MySQL database. The same shared
//! reader-first harness runs in the default SQLite and memory conformance suites.
//! Also verifies durable qualification publication. Not a SQL grant lifecycle.
#![allow(clippy::expect_used, clippy::unwrap_used)]

use bcs_edge_permission_store::DbEdgeGrantStore;
use bcs_test_support::contract::repo::edge_grant::run_agent_invoke_repo_contract;

#[path = "../../../test-support/authority_mysql_chain.rs"]
mod mysql_chain;
#[path = "support/agent_invoke_snapshots.rs"]
mod snapshot_fixture;
#[path = "support/agent_invoke_audit_collision.rs"]
mod audit_collision;

#[tokio::test]
#[ignore = "requires BCS_TEST_MYSQL_URL pointing to a disposable EMPTY MySQL database"]
async fn agent_invoke_repo_contract_mysql() {
    let database = mysql_chain::MysqlConformanceDb::open_empty().await;
    database.apply_full_migration_chain().await;
    let store = DbEdgeGrantStore::mysql(database.plugin.clone());
    run_agent_invoke_repo_contract(&store, "agent-test", "agent-caller", "agent-target").await;
    durable_fact_contract(database.plugin.clone()).await;
    let snapshot = snapshot_fixture::Fixture::new_on(database.plugin.clone(), bcs_db_api::DbSqlFlavor::Mysql,
        bcs_service_api::types::agent_invoke::AgentInvokeScope { namespace:"mysql-invoke".into(),env:"agent-test".into() }).await;
    bcs_test_support::contract::agent_invoke_snapshots::agent_invoke_facts_port_contract_tests(&snapshot).await;
    database.close().await;
}

async fn durable_fact_contract(db: std::sync::Arc<dyn bcs_db_api::DbPlugin>) {
    use std::num::{NonZeroU32, NonZeroU64};
    use bcs_db_api::DbStatement;
    use bcs_edge_permission_store::agent_invoke::DbAgentInvokeStore;
    use bcs_service_api::port::repo::agent_invoke::AgentInvokeFactRepoPort;
    use bcs_service_api::types::agent_invoke::*;
    let scope = AgentInvokeScope { namespace: "mysql-invoke".into(), env: "agent-test".into() };
    let n = NonZeroU32::new(2).unwrap();
    let s = DbAgentInvokeStore::new(db.clone(), bcs_db_api::DbSqlFlavor::Mysql, scope.clone(),
        AgentInvokeLimits { max_out_degree: n, max_in_degree: n, max_pending_out: n, max_pending_in: n }, NonZeroU64::new(60_000).unwrap()).unwrap();
    let closed = s.initialize_scope().await.unwrap();
    assert!(!closed.writer_enabled && !closed.runtime_enabled);
    for bot in ["bot-fact", "BOT-FACT", "bot-fact "] {
        let p = AgentInvokeFactPublication { state: AgentInvokeBotState { bot_id: bot.into(),
            binding_generation: NonZeroU64::new(1).unwrap(), eligibility_generation: NonZeroU64::new(1).unwrap(),
            eligible: true, active: true, hidden: false }, expected_revision: 0, valid_until_ms: s.database_time_ms().await.unwrap() + 30_000 };
        let original = s.publish_fact(&scope, p.clone()).await.unwrap();
        assert_eq!(s.read_fact(&scope, bot).await.unwrap(), Some(original));
        assert_eq!(s.publish_fact(&scope, p.clone()).await, Err(AgentInvokeError::Conflict));
        let mut heartbeat = p; heartbeat.expected_revision = 1;
        assert_eq!(s.publish_fact(&scope, heartbeat.clone()).await.unwrap().revision.get(), 2);
        heartbeat.expected_revision = 2; heartbeat.state.hidden = true;
        assert_eq!(s.publish_fact(&scope, heartbeat.clone()).await, Err(AgentInvokeError::Conflict));
        heartbeat.state.eligibility_generation = NonZeroU64::new(2).unwrap();
        s.publish_fact(&scope, heartbeat).await.unwrap();
    }
    audit_collision::audit_collision_rolls_back(db.clone(), &s, &scope, "bot-fact").await;
    let rows = db.query(DbStatement::new("SELECT COUNT(*) AS n FROM agent_invoke_audit")).await.unwrap();
    assert_eq!(bcs_db_api::db_get_column::<i64>(&rows[0], "n").unwrap(), 9);
    bcs_test_support::contract::agent_invoke_facts::agent_invoke_fact_repo_port_contract_tests(&s, scope, s.database_time_ms().await.unwrap() + 30_000).await;
    println!("agent-invoke-mysql-gate=reader-and-facts byte-exact=3 audits=9 rollback=verified");
}
