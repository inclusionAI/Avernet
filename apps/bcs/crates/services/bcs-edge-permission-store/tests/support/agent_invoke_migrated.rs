//! AgentInvoke cases run in the existing edge-authority suite's migrated DB.
//! No database creation, migration replay, cleanup or per-kind CI service here.
//! The SQLite populated-schema regression executes this SAME scenario inventory.
#![allow(clippy::expect_used, clippy::unwrap_used)]

use bcs_edge_permission_store::DbEdgeGrantStore;
use bcs_test_support::contract::repo::edge_grant::run_agent_invoke_repo_contract;

#[path = "agent_invoke_snapshots.rs"]
mod snapshot_fixture;
#[path = "agent_invoke_audit_collision.rs"]
mod audit_collision;
#[path = "agent_invoke_sql_driver.rs"]
mod lifecycle;
#[path="agent_invoke_query_plans.rs"]
mod query_plans;
#[path="agent_invoke_races.rs"]
mod races;
#[path="agent_invoke_registry.rs"]
mod registry;

pub async fn run(db: std::sync::Arc<dyn bcs_db_api::DbPlugin>, flavor: bcs_db_api::DbSqlFlavor) {
    let backend = match flavor { bcs_db_api::DbSqlFlavor::Mysql => "mysql", bcs_db_api::DbSqlFlavor::Sqlite => "sqlite" };
    let legacy = legacy_rows(db.as_ref()).await;
    let store = match flavor {
        bcs_db_api::DbSqlFlavor::Mysql => DbEdgeGrantStore::mysql(db.clone()),
        bcs_db_api::DbSqlFlavor::Sqlite => DbEdgeGrantStore::sqlite(db.clone()),
    };
    run_agent_invoke_repo_contract(&store, "agent-test", "agent-caller", "agent-target").await;
    durable_fact_contract(db.clone(), flavor).await;
    let snapshot = snapshot_fixture::Fixture::new_on(db.clone(), flavor,
        bcs_service_api::types::agent_invoke::AgentInvokeScope { namespace:"mysql-invoke".into(),env:"agent-test".into() }).await;
    bcs_test_support::contract::agent_invoke_snapshots::agent_invoke_facts_port_contract_tests(&snapshot).await;
    use bcs_test_support::contract::agent_invoke::*;
    for case in 0..10 {
        let driver=lifecycle::Driver::new_on(db.clone(),flavor,&format!("lifecycle-{case}")).await;
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
    let governance=lifecycle::Driver::new_on(db.clone(),flavor,"governance-feed").await;
    bcs_test_support::contract::agent_invoke_facts::agent_invoke_governance_repo_port_contract_tests(governance.store.as_ref(),governance.scope.clone()).await;
    println!("agent-invoke-{backend}-gate=durable-lifecycle scenarios=10 governance-feed=verified");
    let n=std::num::NonZeroU32::new(1000).unwrap();
    for (env,application) in [("query-application",true),("query-repository",false)] {
        let plans=std::sync::Arc::new(query_plans::QueryPlans::new(db.clone()));
        let q=lifecycle::Driver::new_on_with_limits(plans.clone(),flavor,env,
            bcs_service_api::types::agent_invoke::AgentInvokeLimits{max_out_degree:n,max_in_degree:n,max_pending_out:n,max_pending_in:n}).await;
        if application {bcs_test_support::contract::agent_invoke_query::agent_invoke_governance_query_service_contract_tests(&q).await;}
        else {bcs_test_support::contract::agent_invoke_query::agent_invoke_query_repo_port_contract_tests(&q).await;}
        if application && flavor == bcs_db_api::DbSqlFlavor::Mysql {plans.verify_mysql_hints().await;}

    }
    println!("agent-invoke-{backend}-gate=governance-query application-and-repo=verified");

    let identity = lifecycle::Driver::new_on(db.clone(), flavor,"identity-ingress").await;
    use bcs_test_support::contract::agent_invoke::AgentInvokeContractDriver;
    identity.register("identity-source").await;
    identity.sql("UPDATE bcs_bots SET agent_code='Code' WHERE env=? AND bot_uuid='identity-source'", vec![identity.scope.env.as_str().into()]).await;
    agent_invoke_identity_resolution_port_contract_tests(identity.store.as_ref(),identity.scope.clone(),
        bcs_service_api::types::agent_invoke::AgentInvokeVerifiedBot{bot_id:"identity-source".into(),agent_code:"Code".into()}).await;
    println!("agent-invoke-{backend}-gate=exact-identity-resolution source-epochs=verified");

    let races = races::cases();
    let case_count = races.len();
    for (index, case) in races.into_iter().enumerate() {
        println!("agent-invoke-{backend}-race={index} case={case:?}");
        races::run_case(db.clone(), db.clone(), flavor,
            &format!("sql-race-{index}"), case).await;
    }
    println!("agent-invoke-{backend}-gate=deterministic-races scenarios={case_count} native-outcomes=verified");
    registry::run(db.clone(), flavor).await;
    assert_eq!(legacy_rows(db.as_ref()).await, legacy, "existing authority rows must survive AgentInvoke scenarios unchanged");
    println!("agent-invoke-{backend}-gate=shared-schema legacy-rows=unchanged");
}

async fn durable_fact_contract(db: std::sync::Arc<dyn bcs_db_api::DbPlugin>, flavor: bcs_db_api::DbSqlFlavor) {
    let backend = match flavor { bcs_db_api::DbSqlFlavor::Mysql => "mysql", bcs_db_api::DbSqlFlavor::Sqlite => "sqlite" };
    use std::num::{NonZeroU32, NonZeroU64};
    use bcs_db_api::DbStatement;
    use bcs_edge_permission_store::agent_invoke::DbAgentInvokeStore;
    use bcs_service_api::port::repo::agent_invoke::AgentInvokeFactRepoPort;
    use bcs_service_api::types::agent_invoke::*;
    let scope = AgentInvokeScope { namespace: "mysql-invoke".into(), env: "agent-test".into() };
    let n = NonZeroU32::new(2).unwrap();
    let s = DbAgentInvokeStore::new(db.clone(), flavor, scope.clone(),
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
    println!("agent-invoke-{backend}-gate=reader-and-facts byte-exact=3 audits=9 rollback=verified");
}

async fn legacy_rows(db: &dyn bcs_db_api::DbPlugin) -> Vec<Vec<bcs_db_api::DbRow>> {
    let mut rows = vec![];
    for table in ["edge_grants", "permission_profiles", "bcs_bots"] {
        rows.push(db.query(bcs_db_api::DbStatement::new(format!(
            "SELECT * FROM {table} WHERE env IN ('auth-edge','auth-alt') ORDER BY id"))).await.unwrap());
    }
    rows
}
