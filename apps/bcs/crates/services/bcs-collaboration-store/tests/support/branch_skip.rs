use super::*;
use bcs_service_api::port::repo::collaboration::STATE_MACHINE_NODE_BATCH_LIMIT as LIMIT;

fn node(run: &str, id: &str, status: &str) -> StateMachineNodeRun {
    serde_json::from_value(json!({"run_id": run, "node_id": id, "status": status,
        "attempt": 0, "timeout_deadline_ms": 100, "artifact_text": "retained"})).unwrap()
}

pub(super) async fn contract(store: &dyn StateMachineRunRepoPort) {
    let mut run = test_run(); run.run_id = "skip-contract".into();
    let statuses = ["pending", "ready", "retry_scheduled", "running", "completed", "failed", "skipped"];
    store.create_run(run.clone(), statuses.iter().map(|status| node(&run.run_id, status, status)).collect()).await.unwrap();
    assert!(store.get_node_statuses(&run.run_id, &[]).await.unwrap().is_empty());
    assert!(store.skip_nodes("missing-run", &[], 20).await.unwrap());
    assert!(!store.skip_nodes("missing-run", &["pending".into()], 20).await.unwrap());
    for barrier in ["running", "completed", "failed", "skipped", "missing"] {
        let batch = vec!["pending".into(), barrier.into(), "ready".into()];
        assert!(!store.skip_nodes(&run.run_id, &batch, 20).await.unwrap(), "{barrier}");
        let states = store.get_node_statuses(&run.run_id, &batch).await.unwrap();
        assert_eq!(states["pending"], StateMachineNodeStatus::Pending, "must roll back eligible rows");
        assert_eq!(states["ready"], StateMachineNodeStatus::Ready);
    }
    let batch = vec!["pending".into(), "ready".into(), "retry_scheduled".into()];
    assert!(store.skip_nodes(&run.run_id, &batch, 21).await.unwrap());
    assert!(!store.skip_nodes(&run.run_id, &batch, 22).await.unwrap());
    for id in &batch {
        let node = store.get_node_run(&run.run_id, id).await.unwrap().unwrap();
        assert_eq!(node.status, StateMachineNodeStatus::Skipped);
        assert_eq!(node.completed_at, Some(21));
        assert_eq!(node.timeout_deadline_ms, None);
        assert_eq!(node.artifact_text.as_deref(), Some("retained"));
    }
    for ids in [vec!["pending".into(); 2], (0..=LIMIT).map(|n| n.to_string()).collect()] {
        assert!(store.skip_nodes(&run.run_id, &ids, 20).await.is_err());
        assert!(store.get_node_statuses(&run.run_id, &ids).await.is_err());
    }
    for status in [StateMachineRunStatus::Pending, StateMachineRunStatus::Completed, StateMachineRunStatus::Failed, StateMachineRunStatus::Aborted] {
        run.run_id = format!("skip-inactive-{status:?}"); run.status = status;
        store.create_run(run.clone(), vec![node(&run.run_id, "pending", "pending")]).await.unwrap();
        assert!(!store.skip_nodes(&run.run_id, &["pending".into()], 20).await.unwrap());
        assert_eq!(store.get_node_statuses(&run.run_id, &["pending".into()]).await.unwrap()["pending"], StateMachineNodeStatus::Pending);
    }
}

pub(super) async fn capacity(store: &dyn StateMachineRunRepoPort) {
    let mut run = test_run(); run.run_id = "skip-capacity".into();
    let ids: Vec<_> = (0..1980).map(|n| format!("node-{n:04}")).collect();
    store.create_run(run.clone(), ids.iter().map(|id| node(&run.run_id, id, "pending")).collect()).await.unwrap();
    let started = std::time::Instant::now();
    for batch in ids.chunks(LIMIT) {
        assert_eq!(store.get_node_statuses(&run.run_id, batch).await.unwrap().len(), batch.len());
        assert!(store.skip_nodes(&run.run_id, batch, 30).await.unwrap());
    }
    eprintln!("branch skip capacity: 1980 nodes, 10 scalar reads + 10 atomic writes, elapsed={:?}", started.elapsed());
    assert_eq!(store.list_node_runs(&run.run_id).await.unwrap().iter().filter(|n| n.status == StateMachineNodeStatus::Skipped).count(), 1980);
}

#[tokio::test]
async fn memory_branch_skip_contract() {
    let store = MemoryCollaborationStore::new();
    contract(&store).await;
    capacity(&store).await;
}

#[tokio::test]
async fn sqlite_branch_skip_contract_and_environment_isolation() {
    let db = Arc::new(LocalSqliteDbPlugin::new().unwrap());
    bootstrap_migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
    let store = MySqlCollaborationStore::sqlite(db.clone(), "test".into());
    contract(&store).await;
    capacity(&store).await;
    let other = MySqlCollaborationStore::sqlite(db.clone(), "other".into());
    assert!(other.get_node_statuses("skip-contract", &["pending".into()]).await.unwrap().is_empty());
    assert!(!other.skip_nodes("skip-contract", &["pending".into()], 50).await.unwrap());
    db.execute(DbStatement::new("UPDATE bcs_state_machine_node_runs SET record_status = 'deleted' WHERE run_id = 'skip-contract' AND node_id = 'running'")).await.unwrap();
    assert!(store.get_node_statuses("skip-contract", &["running".into()]).await.unwrap().is_empty());
}

#[tokio::test]
async fn mysql_branch_skip_statement_budget_and_errors() {
    let db = Arc::new(RecordingDb::default());
    let store = MySqlCollaborationStore::new(db.clone(), "test".into());
    let ids: Vec<_> = (0..1980).map(|n| n.to_string()).collect();
    for batch in ids.chunks(LIMIT) {
        store.get_node_statuses("run", batch).await.unwrap();
        assert!(store.skip_nodes("run", batch, 30).await.unwrap());
    }
    let queries = db.queries.lock().await;
    assert_eq!(queries.len(), 10);
    for query in &*queries {
        assert!(query.sql().starts_with("SELECT node_id, status "));
        assert!(query.sql().contains("env = ? AND run_id = ? AND record_status = 'active'"));
        assert!(query.params().len() <= LIMIT + 2);
    }
    let transactions = db.transactions.lock().await;
    assert_eq!(transactions.len(), 10);
    for steps in &*transactions {
        assert_eq!(steps.len(), 1);
        let DbTransactionStep::ExecuteChecked { statement, expected_affected_rows } = &steps[0] else { panic!("atomic checked write required"); };
        assert_eq!(*expected_affected_rows as usize, statement.params().len() - 3);
        assert!(*expected_affected_rows <= LIMIT as u64);
        assert!(statement.sql().contains("r.status = 'running' AND r.record_status = 'active'"));
    }
    assert!(db.executes.lock().await.is_empty(), "no per-node writes");
    assert!(store.skip_nodes("run", &ids[..1], 30).await.unwrap());
    assert_eq!(db.executes.lock().await.len(), 1, "one-row CAS needs no transaction");
    let failed = MySqlCollaborationStore::new(Arc::new(AlwaysFailDb), "test".into());
    assert!(failed.get_node_statuses("run", &ids[..1]).await.is_err());
    assert!(failed.skip_nodes("run", &ids[..1], 30).await.is_err());
    assert!(failed.skip_nodes("run", &ids[..2], 30).await.is_err());
}
