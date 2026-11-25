//! DML-only third-statement failure: no MySQL SUPER/trigger/binlog exception.
use bcs_db_api::{DbPlugin, DbStatement, db_get_column};
use bcs_edge_permission_store::agent_invoke::DbAgentInvokeStore;
use bcs_service_api::port::repo::agent_invoke::AgentInvokeFactRepoPort;
use bcs_service_api::types::agent_invoke::*;
use std::sync::Arc;

pub async fn audit_collision_rolls_back(
    db: Arc<dyn DbPlugin>,
    store: &DbAgentInvokeStore,
    scope: &AgentInvokeScope,
    bot: &str,
) {
    let before = store.scope_state().await.unwrap();
    let fact = store.read_fact(scope, bot).await.unwrap().unwrap();
    let action_id = format!("fact-{}", before.revision.get() + 1);
    let counts = db
        .query(DbStatement::new(
            "SELECT COUNT(*) AS n FROM agent_invoke_audit",
        ))
        .await
        .unwrap();
    let count: u64 = db_get_column(&counts[0], "n").unwrap();
    // This row is owned solely by the test. Its unique action_id makes the
    // real publication fail at audit insertion after scope/fact CAS executed.
    db.execute(DbStatement::with_params(
        "INSERT INTO agent_invoke_audit (scope_id, action_id, action, principal_kind, principal_id, from_id, to_id, reason, before_json, after_json, created_at_ms) SELECT id, ?, 'fixture_collision', 'test', 'test-audit-collision', ?, ?, 'DML rollback fixture', 'null', 'null', 1 FROM agent_invoke_scopes WHERE namespace = ? AND env = ?",
        vec![action_id.as_str().into(), bot.into(), bot.into(), scope.namespace.as_str().into(), scope.env.as_str().into()],
    )).await.unwrap();
    let publication = AgentInvokeFactPublication {
        state: fact.state.clone(),
        expected_revision: fact.revision.get(),
        valid_until_ms: store.database_time_ms().await.unwrap() + 30_000,
    };
    assert_eq!(
        store.publish_fact(scope, publication).await,
        Err(AgentInvokeError::Conflict)
    );
    assert_eq!(store.scope_state().await.unwrap(), before);
    assert_eq!(store.read_fact(scope, bot).await.unwrap(), Some(fact));
    db.execute(DbStatement::with_params("DELETE FROM agent_invoke_audit WHERE action_id = ? AND principal_kind = 'test' AND principal_id = 'test-audit-collision'", vec![action_id.into()])).await.unwrap();
    let counts = db
        .query(DbStatement::new(
            "SELECT COUNT(*) AS n FROM agent_invoke_audit",
        ))
        .await
        .unwrap();
    assert_eq!(
        db_get_column::<u64>(&counts[0], "n").unwrap(),
        count,
        "failed publication leaves no audit or partial write"
    );
}
