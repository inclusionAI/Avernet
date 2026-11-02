use super::*;

#[tokio::test]
async fn disconnect_cas_preserves_artifact_and_rejects_stale_identity() {
    let db = Arc::new(LocalSqliteDbPlugin::new().unwrap());
    bootstrap_migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
    let store = MySqlCollaborationStore::sqlite(db.clone(), "test".into());
    db.execute(DbStatement::new("INSERT INTO bcs_state_machine_runs (env, run_id, definition_id, definition_version, group_id, group_version, session_id, status, created_at_ms, updated_at_ms) VALUES ('test', 'run', 'definition', 1, 'group', 1, 'session', 'running', 10, 10)")).await.unwrap();
    db.execute(DbStatement::new("INSERT INTO bcs_state_machine_node_runs (env, run_id, node_id, status, attempt, assignee_bot_id, delivery_request_id, started_at_ms) VALUES ('test', 'run', 'node', 'running', 0, 'bot', 'request', 100)")).await.unwrap();
    assert!(store.disconnected_bot_nodes("bot", 99, None, 64).await.unwrap().is_empty());
    assert!(store.disconnected_bot_nodes("other", 101, None, 64).await.unwrap().is_empty());
    let node = store.disconnected_bot_nodes("bot", 101, None, 64).await.unwrap().remove(0);
    let mut stale = node.clone(); stale.delivery_request_id = Some("old-request".into());
    assert!(!store.fail_disconnected_bot_node(&stale, 101).await.unwrap());
    db.execute(DbStatement::new("UPDATE bcs_state_machine_node_runs SET artifact_text = 'already accepted' WHERE env = 'test' AND run_id = 'run' AND node_id = 'node'")).await.unwrap();
    assert!(!store.fail_disconnected_bot_node(&node, 101).await.unwrap());
    db.execute(DbStatement::new("UPDATE bcs_state_machine_node_runs SET artifact_text = NULL WHERE env = 'test' AND run_id = 'run' AND node_id = 'node'")).await.unwrap();
    db.execute(DbStatement::new("CREATE TRIGGER reject_failure BEFORE INSERT ON bcs_collaboration_delivery_checkpoints BEGIN SELECT RAISE(ABORT, 'injected'); END")).await.unwrap();
    assert!(store.fail_disconnected_bot_node(&node, 101).await.is_err());
    assert_eq!(store.get_node_run("run", "node").await.unwrap().unwrap().status, StateMachineNodeStatus::Running);
    db.execute(DbStatement::new("DROP TRIGGER reject_failure")).await.unwrap();
    assert!(store.fail_disconnected_bot_node(&node, 101).await.unwrap());
    assert!(!store.fail_disconnected_bot_node(&node, 101).await.unwrap());
    let fact = store.get_node_attempt_failure("run", "node", 0).await.unwrap().unwrap();
    assert_eq!(fact.reason_category, Some(bcs_service_api::port::repo::StateMachineRunReason::NodeFailed));
}
