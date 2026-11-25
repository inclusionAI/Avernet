//! Run explicitly against a disposable, EMPTY MySQL database. The same shared
//! reader-first harness runs in the default SQLite and memory conformance suites.
//! Also verifies durable qualification/delegation publication, SQL lifecycle
//! commands and the shared management/runtime snapshot consumer contracts.
#![allow(clippy::expect_used, clippy::unwrap_used)]

use bcs_edge_permission_store::DbEdgeGrantStore;
use bcs_test_support::contract::repo::edge_grant::run_agent_invoke_repo_contract;

#[path = "../../../test-support/authority_mysql_chain.rs"]
mod mysql_chain;
#[path = "support/agent_invoke_snapshots.rs"]
mod snapshot_fixture;
#[path = "support/agent_invoke_audit_collision.rs"]
mod audit_collision;
#[path = "support/agent_invoke_sql_driver.rs"]
mod lifecycle;
#[path="support/agent_invoke_query_plans.rs"]
mod query_plans;

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
    use bcs_test_support::contract::agent_invoke::*;
    for case in 0..10 {
        let driver=lifecycle::Driver::new_on(database.plugin.clone(),bcs_db_api::DbSqlFlavor::Mysql,&format!("lifecycle-{case}")).await;
        match case {
            0=>agent_invoke_grant_management_service_contract_tests(&driver).await,
            1=>run_agent_invoke_replay_contract(&driver).await,
            2=>agent_invocation_service_contract_tests(&driver).await,
            3=>run_agent_invoke_request_terminals_contract(&driver).await,
            4=>agent_invoke_command_repo_port_contract_tests(&driver).await,
            5=>run_agent_invoke_requester_authority_contract(&driver).await,
            6=>run_agent_invoke_limits_contract(&driver).await,
            7=>run_agent_invoke_atomic_limits_contract(&driver).await,
            8=>run_agent_invoke_approve_revoke_contract(&driver,true).await,
            _=>run_agent_invoke_approve_revoke_contract(&driver,false).await,
        }
    }
    let governance=lifecycle::Driver::new_on(database.plugin.clone(),bcs_db_api::DbSqlFlavor::Mysql,"governance-feed").await;
    bcs_test_support::contract::agent_invoke_facts::agent_invoke_governance_repo_port_contract_tests(governance.store.as_ref(),governance.scope.clone()).await;
    println!("agent-invoke-mysql-gate=durable-lifecycle scenarios=10 governance-feed=verified");
    let n=std::num::NonZeroU32::new(1000).unwrap();
    for (env,application) in [("query-application",true),("query-repository",false)] {
        let plans=std::sync::Arc::new(query_plans::QueryPlans::new(database.plugin.clone()));
        let q=lifecycle::Driver::new_on_with_limits(plans.clone(),bcs_db_api::DbSqlFlavor::Mysql,env,
            bcs_service_api::types::agent_invoke::AgentInvokeLimits{max_out_degree:n,max_in_degree:n,max_pending_out:n,max_pending_in:n}).await;
        if application {bcs_test_support::contract::agent_invoke_query::agent_invoke_governance_query_service_contract_tests(&q).await;}
        else {bcs_test_support::contract::agent_invoke_query::agent_invoke_query_repo_port_contract_tests(&q).await;}
        if application {plans.verify_mysql_hints().await;}

    }
    println!("agent-invoke-mysql-gate=governance-query application-and-repo=verified");

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
