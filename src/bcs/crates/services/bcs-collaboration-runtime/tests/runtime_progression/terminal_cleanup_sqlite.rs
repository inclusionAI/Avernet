use super::*;

async fn fixture(path: Option<&str>) -> (Arc<LocalSqliteDbPlugin>, CollaborationRuntime, Arc<RecordingDelivery>) {
    let db = Arc::new(match path { Some(path) => LocalSqliteDbPlugin::new_file(path).unwrap(), None => LocalSqliteDbPlugin::new().unwrap() });
    bootstrap_migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
    let store = Arc::new(MySqlCollaborationStore::sqlite(db.clone(), "test".into()));
    let delivery = Arc::new(RecordingDelivery::default());
    let sessions = Arc::new(SessionManagementServiceImpl::new(Arc::new(MemorySessionRepo::new()), Arc::new(MemoryGroupRepo::new())));
    // Cleanup needs neither live Group/Session/Definition nor a v2 compiler gate.
    let runtime = CollaborationRuntime::new(store.clone(), store.clone(), store.clone(), store,
        Arc::new(GroupStore::new()), sessions, delivery.clone(), noop_judge());
    (db, runtime, delivery)
}

async fn seed(db: &dyn DbPlugin) {
    for (id, status) in [("a", "completed"), ("b", "failed"), ("c", "aborted"), ("z", "running")] {
        db.execute(DbStatement::with_params("INSERT INTO bcs_state_machine_runs (env, run_id, definition_id, definition_version, group_id, group_version, session_id, status, output_text, created_at_ms, updated_at_ms) VALUES ('test', ?, 'missing-v2-definition', 2, 'missing-group', 1, ?, ?, 'saved result', 100, 100)",
            vec![id.into(), format!("session-{id}").into(), status.into()])).await.unwrap();
        db.execute(DbStatement::with_params("INSERT INTO bcs_state_machine_node_runs (env, run_id, node_id, status, assignee_bot_id, artifact_text, runtime_phase, recovery_lease_owner, recovery_lease_token, recovery_lease_until_ms) VALUES ('test', ?, 'node', 'running', 'bot', 'saved artifact', 'judging', 'old-owner', 7, 900)", vec![id.into()])).await.unwrap();
        db.execute(DbStatement::with_params("INSERT INTO bcs_collaboration_delivery_checkpoints (env, operation_key, aggregate_kind, aggregate_id, operation_kind, payload_json, status, created_at_ms, lease_owner, lease_token, lease_until_ms) VALUES ('test', ?, 'state_machine_node', ?, 'bot_dispatch', '{\"original\":true}', 'delivering', 100, 'old-owner', 7, 900)", vec![format!("dispatch-{id}").into(), id.into()])).await.unwrap();
    }
}

async fn assert_preserved(db: &dyn DbPlugin) {
    let runs = db.query(DbStatement::new("SELECT run_id, status, output_text FROM bcs_state_machine_runs ORDER BY run_id")).await.unwrap();
    for (row, status) in runs.iter().zip(["completed", "failed", "aborted", "running"]) {
        assert_eq!(bcs_db_api::db_get_column::<String>(row, "status").unwrap(), status);
        assert_eq!(bcs_db_api::db_get_column::<String>(row, "output_text").unwrap(), "saved result");
    }
    let nodes = db.query(DbStatement::new("SELECT run_id, status, artifact_text, runtime_phase, recovery_lease_owner, recovery_lease_token FROM bcs_state_machine_node_runs ORDER BY run_id")).await.unwrap();
    for row in nodes {
        let active = bcs_db_api::db_get_column::<String>(&row, "run_id").unwrap() == "z";
        assert_eq!(bcs_db_api::db_get_column::<String>(&row, "status").unwrap(), "running");
        assert_eq!(bcs_db_api::db_get_column::<String>(&row, "artifact_text").unwrap(), "saved artifact");
        assert_eq!(bcs_db_api::db_get_column::<i64>(&row, "recovery_lease_token").unwrap(), 7);
        for col in ["runtime_phase", "recovery_lease_owner"] {
            assert_eq!(bcs_db_api::db_get_column_opt::<String>(&row, col).unwrap().is_some(), active);
        }
    }
    let rows = db.query(DbStatement::new("SELECT aggregate_id, payload_json, status, lease_owner, lease_token FROM bcs_collaboration_delivery_checkpoints ORDER BY aggregate_id")).await.unwrap();
    for row in rows {
        let active = bcs_db_api::db_get_column::<String>(&row, "aggregate_id").unwrap() == "z";
        assert_eq!(bcs_db_api::db_get_column::<String>(&row, "payload_json").unwrap(), "{\"original\":true}");
        assert_eq!(bcs_db_api::db_get_column::<String>(&row, "status").unwrap(), if active { "delivering" } else { "superseded" });
        assert_eq!(bcs_db_api::db_get_column::<i64>(&row, "lease_token").unwrap(), 7);
        assert_eq!(bcs_db_api::db_get_column_opt::<String>(&row, "lease_owner").unwrap().is_some(), active);
    }
}

#[tokio::test]
async fn terminal_cleanup_failure_advances_page_and_resumes_partial_writes_without_io() {
    let (db, runtime, delivery) = fixture(None).await;
    seed(db.as_ref()).await;
    db.execute(DbStatement::new("CREATE TRIGGER reject_cleanup BEFORE UPDATE ON bcs_state_machine_node_runs WHEN OLD.run_id = 'a' BEGIN SELECT RAISE(ABORT, 'injected cleanup write failure'); END")).await.unwrap();
    let page = runtime.cleanup_state_machine_terminal_work(None, 2).await.unwrap();
    assert_eq!(page.scanned, 2); assert_eq!(page.reconciled, 1); assert_eq!(page.next_run_id.as_deref(), Some("b"));
    assert_eq!(page.failures.len(), 1); assert_eq!(page.failures[0].run_id, "a");
    let rows = db.query(DbStatement::new("SELECT status FROM bcs_collaboration_delivery_checkpoints WHERE aggregate_id = 'a'")).await.unwrap();
    assert_eq!(bcs_db_api::db_get_column::<String>(&rows[0], "status").unwrap(), "superseded");
    let next = runtime.cleanup_state_machine_terminal_work(page.next_run_id, 2).await.unwrap();
    assert_eq!(next.scanned, 1); assert!(next.next_run_id.is_none()); assert!(next.failures.is_empty());
    db.execute(DbStatement::new("DROP TRIGGER reject_cleanup")).await.unwrap();
    for _ in 0..2 {
        let page = runtime.cleanup_state_machine_terminal_work(None, 32).await.unwrap();
        assert_eq!(page.scanned, 3); assert!(page.failures.is_empty());
    }
    assert_eq!(runtime.cleanup_state_machine_terminal_work(None, 0).await.unwrap().scanned, 0);
    assert_preserved(db.as_ref()).await;
    assert!(delivery.commands.lock().await.is_empty());
}

#[tokio::test]
async fn terminal_cleanup_recovers_after_process_restart_without_loading_snapshot() {
    if let Ok(path) = std::env::var("BCS_TERMINAL_CLEANUP_TEST_DB") {
        let (db, runtime, delivery) = fixture(Some(&path)).await;
        if std::env::var("BCS_TERMINAL_CLEANUP_TEST_PHASE").unwrap() == "prepare" { seed(db.as_ref()).await; }
        else {
            let page = runtime.cleanup_state_machine_terminal_work(None, 32).await.unwrap();
            assert_eq!(page.scanned, 3); assert!(page.failures.is_empty());
            assert_preserved(db.as_ref()).await;
            assert!(delivery.commands.lock().await.is_empty());
        }
        return;
    }
    let directory = TempDatabaseDirectory(std::env::temp_dir().join(format!("bcs-terminal-cleanup-{}", uuid::Uuid::new_v4())));
    for phase in ["prepare", "recover"] {
        let output = std::process::Command::new(std::env::current_exe().unwrap())
            .args(["--exact", "fixed_loop_tests::recovery_tests::sqlite_restart_tests::terminal_cleanup_tests::terminal_cleanup_recovers_after_process_restart_without_loading_snapshot", "--nocapture"])
            .env("BCS_TERMINAL_CLEANUP_TEST_DB", directory.0.join("cleanup.sqlite"))
            .env("BCS_TERMINAL_CLEANUP_TEST_PHASE", phase).output().unwrap();
        assert!(output.status.success(), "{phase}: {} {}", String::from_utf8_lossy(&output.stdout), String::from_utf8_lossy(&output.stderr));
    }
}
