use super::*;
use bcs_event_store::MemoryEventStore;
use bcs_service_api::{FailStateMachineNodeAttempt, StateMachineFailureAction, ServiceError};

fn assert_cas_lost(result: bcs_service_api::ServiceResult<bool>) {
    assert!(matches!(result, Ok(false) | Err(ServiceError::Conflict(_))), "{result:?}");
}

fn failure(node: &str, action: StateMachineFailureAction) -> FailStateMachineNodeAttempt {
    FailStateMachineNodeAttempt { run_id: "failure-run".into(), node_id: node.into(), attempt: 0,
        error: "saved failure".into(), completed_at_ms: 100, action }
}

async fn failure_contract(store: &dyn StateMachineRunRepoPort, events: &dyn EventRepoPort) {
    let mut run = test_run();
    run.run_id = "failure-run".into();
    run.status = StateMachineRunStatus::Running;
    let nodes = ["retry", "fatal", "legacy", "exhausted", "event", "cancelled"].into_iter().map(|node| {
        serde_json::from_value::<StateMachineNodeRun>(json!({
            "run_id": "failure-run", "node_id": node, "status": "running", "attempt": 0,
            "max_attempts": if node == "exhausted" { 1 } else { 3 }, "timeout_deadline_ms": 200,
        })).unwrap()
    }).collect();
    store.create_run(run, nodes).await.unwrap();
    let mut wrong = failure("retry", StateMachineFailureAction::Retry);
    wrong.attempt = 1;
    assert!(!store.fail_node_attempt_with_action(wrong).await.unwrap());
    assert!(!store.fail_node_attempt_with_action(failure("exhausted", StateMachineFailureAction::Retry)).await.unwrap());
    assert!(store.fail_node_attempt_with_action(failure("retry", StateMachineFailureAction::Retry)).await.unwrap());
    assert!(!store.fail_node_attempt_with_action(failure("retry", StateMachineFailureAction::FailRun)).await.unwrap());
    let saved = store.get_node_attempt_failure("failure-run", "retry", 0).await.unwrap().unwrap();
    assert_eq!(saved.action, Some(StateMachineFailureAction::Retry));
    assert_eq!(saved.node.error.as_deref(), Some("saved failure"));
    assert_eq!(saved.node.completed_at, Some(100));
    assert_eq!(saved.node.timeout_deadline_ms, None);
    assert!(store.get_node_attempt_failure("failure-run", "retry", 1).await.unwrap().is_none());
    assert!(store.schedule_node_retry("failure-run", "retry", 0, 1).await.unwrap());
    assert!(!store.schedule_node_retry("failure-run", "retry", 0, 1).await.unwrap());
    assert!(store.get_node_attempt_failure("failure-run", "retry", 0).await.unwrap().is_none());
    assert!(store.mark_node_running_if_run_active("failure-run", "retry", 1, "delivery-retry".into(), 110).await.unwrap());
    assert!(!store.fail_node_attempt_with_action(failure("retry", StateMachineFailureAction::FailRun)).await.unwrap());
    // Legacy writers do not inherit the previous attempt's saved action.
    assert!(store.fail_node_attempt("failure-run", "retry", 1, "legacy".into(), 120).await.unwrap());
    assert_eq!(store.get_node_attempt_failure("failure-run", "retry", 1).await.unwrap().unwrap().action, None);
    assert!(store.fail_node_attempt_with_action(failure("fatal", StateMachineFailureAction::FailRun)).await.unwrap());
    assert!(!store.schedule_node_retry("failure-run", "fatal", 0, 1).await.unwrap());
    let transition = |node: &str, event_id: &str| StateMachineEventfulTransition::ScheduleNodeRetry {
        run_id: "failure-run".into(), node_id: node.into(), failed_attempt: 0, next_attempt: 1,
        event: public_event(event_id, "state_machine.node.retry_scheduled", None),
    };
    assert_cas_lost(store.commit_eventful_transition(transition("fatal", "fatal-retry-event")).await);
    assert!(events.get_event("fatal-retry-event", "test").await.unwrap().is_none());
    assert!(store.fail_node_attempt_with_action(failure("event", StateMachineFailureAction::Retry)).await.unwrap());
    assert!(store.commit_eventful_transition(transition("event", "retry-event")).await.unwrap());
    assert_cas_lost(store.commit_eventful_transition(transition("event", "retry-event")).await);
    assert!(events.get_event("retry-event", "test").await.unwrap().is_some());
    assert!(store.get_node_attempt_failure("failure-run", "event", 0).await.unwrap().is_none());
    assert!(store.fail_node_attempt("failure-run", "legacy", 0, "legacy".into(), 100).await.unwrap());
    assert_eq!(store.get_node_attempt_failure("failure-run", "legacy", 0).await.unwrap().unwrap().action, None);
    store.update_run_status("failure-run", StateMachineRunStatus::Aborted, None, None, 150, Some(150)).await.unwrap();
    assert!(!store.fail_node_attempt_with_action(failure("cancelled", StateMachineFailureAction::Retry)).await.unwrap());
    assert_eq!(store.get_node_run("failure-run", "cancelled").await.unwrap().unwrap().status, StateMachineNodeStatus::Running);
}

#[tokio::test]
async fn memory_failure_action_contract() {
    let events = Arc::new(MemoryEventStore::new());
    let store = MemoryCollaborationStore::new().with_event_store(events.clone());
    failure_contract(&store, events.as_ref()).await;
}

#[tokio::test]
async fn sqlite_failure_action_contract_and_unknown_action_rejected() {
    let db = Arc::new(LocalSqliteDbPlugin::new().unwrap());
    bootstrap_migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
    let store = MySqlCollaborationStore::sqlite(db.clone(), "test".into());
    failure_contract(&store, &DbEventStore::sqlite(db.clone())).await;
    db.execute(DbStatement::new("UPDATE bcs_state_machine_node_runs SET failure_action = 'unknown' WHERE node_id = 'fatal'")).await.unwrap();
    assert!(store.get_node_attempt_failure("failure-run", "fatal", 0).await.unwrap_err().to_string().contains("unknown"));
    assert!(MySqlCollaborationStore::sqlite(db, "other".into()).get_node_attempt_failure("failure-run", "fatal", 0).await.unwrap().is_none());
}

#[tokio::test]
async fn mysql_failure_action_write_is_one_cas_and_errors_propagate() {
    let db = Arc::new(RecordingDb::default());
    let store = MySqlCollaborationStore::new(db.clone(), "test".into());
    store.fail_node_attempt_with_action(failure("retry", StateMachineFailureAction::Retry)).await.unwrap();
    let writes = db.executes.lock().await;
    assert_eq!(writes.len(), 1);
    assert!(writes[0].sql().contains("failure_action = ?"));
    assert!(writes[0].sql().contains("attempt = ? AND status = 'running'"));
    assert!(writes[0].sql().contains("r.status = 'running'"));
    assert_eq!(writes[0].params()[2], DbValue::from("retry"));
    let failed = MySqlCollaborationStore::new(Arc::new(AlwaysFailDb), "test".into());
    assert!(failed.fail_node_attempt_with_action(failure("retry", StateMachineFailureAction::Retry)).await.is_err());
    assert!(failed.get_node_attempt_failure("failure-run", "retry", 0).await.is_err());
}

#[tokio::test]
async fn sqlite_failure_action_migration_preserves_legacy_rows_and_replays() {
    let db = LocalSqliteDbPlugin::new().unwrap();
    bootstrap_migrations::run_sqlite_migrations(&db).await.unwrap();
    db.execute(DbStatement::new("ALTER TABLE bcs_state_machine_node_runs DROP COLUMN failure_action")).await.unwrap();
    db.execute(DbStatement::new("DELETE FROM bcs_schema_migrations WHERE version = 28")).await.unwrap();
    db.execute(DbStatement::new("INSERT INTO bcs_state_machine_node_runs (run_id, node_id, assignee_bot_id, status, error_message, env) VALUES ('legacy', 'node', 'bot', 'failed', 'old failure', 'test')")).await.unwrap();
    bootstrap_migrations::run_sqlite_migrations(&db).await.unwrap();
    // Column committed, migration marker not committed: safe to replay.
    db.execute(DbStatement::new("DELETE FROM bcs_schema_migrations WHERE version = 28")).await.unwrap();
    bootstrap_migrations::run_sqlite_migrations(&db).await.unwrap();
    let row = db.query(DbStatement::new("SELECT failure_action, error_message FROM bcs_state_machine_node_runs WHERE run_id = 'legacy'")).await.unwrap().remove(0);
    assert_eq!(bcs_db_api::db_get_column_opt::<String>(&row, "failure_action").unwrap(), None);
    assert_eq!(bcs_db_api::db_get_column::<String>(&row, "error_message").unwrap(), "old failure");
}
