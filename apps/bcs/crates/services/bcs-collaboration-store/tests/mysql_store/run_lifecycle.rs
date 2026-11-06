use super::*;

#[tokio::test]
async fn memory_fixed_loop_rerun_preserves_source_plan_and_concurrent_uniqueness() {
    loop_rerun_copies_snapshot_once(&MemoryCollaborationStore::new(), fixed_loop_snapshot_fixture()).await;
}

#[tokio::test]
async fn memory_nested_loop_rerun_preserves_compiler_v2_path_snapshot() {
    loop_rerun_copies_snapshot_once(&MemoryCollaborationStore::new(), nested_loop_path_snapshot_fixture()).await;
}

#[tokio::test]
async fn sqlite_fixed_loop_rerun_preserves_source_plan_and_concurrent_uniqueness() {
    let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new().unwrap());
    bootstrap_migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
    db.execute(DbStatement::new(
        "INSERT INTO bcs_group_sessions (env, session_id, group_id, session_kind, status, activation_count, participants) \
         VALUES ('test', 'group-1:abcdef12', 'group-1', 'chat', 'running', 1, '[]')"
    )).await.unwrap();
    loop_rerun_copies_snapshot_once(
        &MySqlCollaborationStore::sqlite(db, "test".into()),
        fixed_loop_snapshot_fixture(),
    ).await;
}

#[tokio::test]
async fn sqlite_nested_loop_rerun_preserves_compiler_v2_path_snapshot() {
    let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new().unwrap());
    bootstrap_migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
    db.execute(DbStatement::new(
        "INSERT INTO bcs_group_sessions (env, session_id, group_id, session_kind, status, activation_count, participants) \
         VALUES ('test', 'group-1:abcdef12', 'group-1', 'chat', 'running', 1, '[]')"
    )).await.unwrap();
    loop_rerun_copies_snapshot_once(
        &MySqlCollaborationStore::sqlite(db, "test".into()),
        nested_loop_path_snapshot_fixture(),
    ).await;
}

#[tokio::test]
async fn sqlite_run_start_and_public_event_batch_commit_in_one_transaction() {
    let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new().expect("SQLite plugin"));
    bootstrap_migrations::run_sqlite_migrations(db.as_ref())
        .await
        .expect("apply SQLite schema");
    let store = MySqlCollaborationStore::sqlite(db.clone(), "test".to_string());
    let events = DbEventStore::sqlite(db);
    let mut run = test_run();
    run.run_id = "sm-run-atomic".to_string();
    run.status = StateMachineRunStatus::Pending;
    store
        .create_run(run, Vec::new())
        .await
        .expect("create pending run");

    assert!(
        store
            .commit_eventful_transition(StateMachineEventfulTransition::StartRun {
                run_id: "sm-run-atomic".to_string(),
                started_at_ms: 2,
                events: vec![
                    public_event("evt-sqlite-run-created", "state_machine.run.created", None),
                    public_event(
                        "evt-sqlite-run-started",
                        "state_machine.run.started",
                        Some("evt-sqlite-run-created"),
                    ),
                ],
            })
            .await
            .expect("commit eventful run start")
    );

    let run = store
        .get_run("sm-run-atomic")
        .await
        .expect("load run")
        .expect("run");
    assert_eq!(run.status, StateMachineRunStatus::Running);
    let created = events
        .get_event("evt-sqlite-run-created", "test")
        .await
        .expect("load created Event")
        .expect("created Event");
    let started = events
        .get_event("evt-sqlite-run-started", "test")
        .await
        .expect("load started Event")
        .expect("started Event");
    assert_eq!(created.envelope.stream.sequence, 1);
    assert_eq!(started.envelope.stream.sequence, 2);
}

#[tokio::test]
async fn mysql_runtime_create_run_writes_run_and_node_rows() {
    let db = Arc::new(RecordingDb::default());
    let store = MySqlCollaborationStore::new(db.clone(), "dev".to_string());
    let mut run = test_run();
    run.opening_message_override = Some(OpeningMessage::Text(
        "Run {{bcs.run_id}}".to_string(),
    ));
    let node = test_node();

    store
        .create_run(run.clone(), vec![node.clone()])
        .await
        .expect("create run");

    let transactions = db.transactions.lock().await;
    assert_eq!(transactions.len(), 1);
    let steps = &transactions[0];
    assert_eq!(steps.len(), 2);
    match &steps[0] {
        DbTransactionStep::Execute(stmt) => {
            assert!(stmt.sql().contains("bcs_state_machine_runs"));
            assert!(!stmt.sql().contains("ON DUPLICATE KEY UPDATE"));
            assert_eq!(stmt.params()[1], DbValue::from("sm-run-1"));
            assert_eq!(stmt.params()[2], DbValue::from("sm-run-1"));
            assert_eq!(stmt.params()[7], DbValue::from(7));
            assert_eq!(stmt.params()[10], DbValue::from("tester"));
            assert_eq!(stmt.params()[11], DbValue::from("running"));
            assert!(stmt.sql().contains("opening_message_override_json"));
            assert_eq!(
                stmt.params()[13],
                DbValue::from(
                    serde_json::to_string(run.opening_message_override.as_ref().unwrap())
                        .unwrap()
                        .as_str()
                )
            );
        }
        _ => panic!("expected run execute step"),
    }
    match &steps[1] {
        DbTransactionStep::Execute(stmt) => {
            assert!(stmt.sql().contains("bcs_state_machine_node_runs"));
            assert!(!stmt.sql().contains("ON DUPLICATE KEY UPDATE"));
            assert_eq!(stmt.params()[2], DbValue::from("answer"));
            assert_eq!(stmt.params()[5], DbValue::from(120_000_u64));
            assert_eq!(stmt.params()[7], DbValue::from(2));
            assert_eq!(stmt.params()[8], DbValue::from("bot_sm_e2e_driver"));
        }
        _ => panic!("expected node execute step"),
    }
}

#[tokio::test]
async fn mysql_session_idle_create_locks_session_and_guards_run_and_nodes() {
    let db = Arc::new(RecordingDb::default());
    let store = MySqlCollaborationStore::new(db.clone(), "dev".to_string());

    assert!(
        store
            .create_run_if_session_idle(test_run(), vec![test_node()])
            .await
            .expect("create session-scoped run")
    );

    let transactions = db.transactions.lock().await;
    let steps = &transactions[0];
    assert_eq!(steps.len(), 3);
    let DbTransactionStep::Query(lock) = &steps[0] else {
        panic!("expected session lock query");
    };
    assert!(lock.sql().contains("bcs_group_sessions"));
    assert!(lock.sql().contains("FOR UPDATE"));
    let DbTransactionStep::Execute(run_insert) = &steps[1] else {
        panic!("expected guarded run insert");
    };
    assert!(run_insert.sql().contains("NOT EXISTS"));
    assert!(run_insert.sql().contains("status IN ('pending', 'running')"));
    let DbTransactionStep::Execute(node_insert) = &steps[2] else {
        panic!("expected guarded node insert");
    };
    assert!(node_insert.sql().contains("WHERE EXISTS"));
    assert!(node_insert.sql().contains("bcs_state_machine_runs"));
}

#[tokio::test]
async fn mysql_runtime_reads_run_and_node_rows() {
    let mut run = test_run();
    run.opening_message_override = Some(
        serde_json::from_value(json!({
            "type": "panel",
            "component": "partnerPanel.OneShotRunView",
            "params": {"runId": "{{bcs.run_id}}"}
        }))
        .expect("valid opening panel"),
    );
    let node = test_node();
    let db = Arc::new(RecordingDb {
        runtime_run_row: Mutex::new(Some(run_row(&run))),
        runtime_node_rows: Mutex::new(vec![node_row(&node)]),
        ..RecordingDb::default()
    });
    let store = MySqlCollaborationStore::new(db.clone(), "dev".to_string());

    let loaded_run = store
        .get_run("sm-run-1")
        .await
        .expect("get run")
        .expect("run row");
    assert_eq!(loaded_run.group_version, 7);
    assert_eq!(loaded_run.created_by.as_deref(), Some("tester"));
    assert_eq!(loaded_run.input["question"], "hello");
    assert_eq!(
        loaded_run.opening_message_override,
        run.opening_message_override
    );

    let loaded_by_session = store
        .get_run_by_session_id("group-1:abcdef12")
        .await
        .expect("get run by session")
        .expect("run row");
    assert_eq!(loaded_by_session.run_id, "sm-run-1");

    let session_runs = store
        .list_runs_by_session_id("group-1:abcdef12")
        .await
        .expect("list runs by session");
    assert_eq!(session_runs.len(), 1);

    let nodes = store.list_node_runs("sm-run-1").await.expect("list nodes");
    assert_eq!(nodes.len(), 1);
    assert_eq!(nodes[0].node_timeout_ms, Some(120_000));
    assert_eq!(nodes[0].timeout_deadline_ms, Some(121_000));
    assert_eq!(nodes[0].max_attempts, 2);
    let queries = db.queries.lock().await;
    assert!(queries[1].sql().contains("session_id = ?"));
    assert_eq!(queries[1].params()[1], DbValue::from("group-1:abcdef12"));
    assert!(queries[2].sql().contains("session_id = ?"));
    assert!(!queries[2].sql().contains("LIMIT 1"));
}

#[tokio::test]
async fn mysql_runtime_node_and_run_updates_use_cas_sql() {
    let db = Arc::new(RecordingDb::default());
    let store = MySqlCollaborationStore::new(db.clone(), "dev".to_string());

    store
        .mark_node_running("sm-run-1", "answer", 1, "delivery-1".to_string(), 1_000)
        .await
        .expect("mark running");
    let artifact_recorded = store
        .record_node_artifact_if_running(
            "sm-run-1",
            "answer",
            1,
            "candidate".to_string(),
        )
        .await
        .expect("record node artifact");
    let completed = store
        .complete_node_attempt(
            "sm-run-1",
            "answer",
            1,
            "complete".to_string(),
            "done".to_string(),
            None,
            2_000,
        )
        .await
        .expect("complete node");
    let updated = store
        .update_run_status(
            "sm-run-1",
            StateMachineRunStatus::Completed,
            Some("done".to_string()),
            None,
            2_100,
            Some(2_100),
        )
        .await
        .expect("update run");
    let retry_scheduled = store
        .schedule_node_retry("sm-run-1", "answer", 1, 2)
        .await
        .expect("schedule retry");
    let skipped = store
        .skip_node("sm-run-1", "revise", 2_200)
        .await
        .expect("skip node");

    assert!(artifact_recorded);
    assert!(completed);
    assert!(updated);
    assert!(retry_scheduled);
    assert!(skipped);
    let executes = db.executes.lock().await;
    assert_eq!(executes.len(), 6);
    assert!(executes[0].sql().contains("timeout_deadline_ms = CASE"));
    assert_eq!(executes[0].params()[0], DbValue::from(1));
    assert_eq!(executes[0].params()[1], DbValue::from("delivery-1"));
    assert!(executes[1].sql().contains("SET artifact_text = ?"));
    assert!(executes[1].sql().contains("AND attempt = ? AND status = 'running'"));
    assert_eq!(executes[1].params()[0], DbValue::from("candidate"));
    assert_eq!(executes[1].params()[4], DbValue::from(1));
    assert!(executes[2].sql().contains("AND attempt = ? AND status = 'running'"));
    assert_eq!(executes[2].params()[7], DbValue::from(1));
    assert!(executes[3].sql().contains("status NOT IN ('completed', 'failed', 'aborted')"));
    assert!(executes[4].sql().contains("AND attempt = ? AND status = 'failed'"));
    assert_eq!(executes[4].params()[4], DbValue::from(1));
    assert!(executes[5].sql().contains("SET status = 'skipped'"));
    assert!(executes[5].sql().contains("AND status IN ('pending', 'ready', 'retry_scheduled')"));
    assert_eq!(executes[5].params()[0], DbValue::from(2_200_u64));
}

#[tokio::test]
async fn mysql_human_activation_uses_empty_assignee_sentinel_and_persisted_deadline() {
    let db = Arc::new(RecordingDb::default());
    let store = MySqlCollaborationStore::new(db.clone(), "dev".to_string());

    let marked = store
        .mark_human_node_running_if_run_active(MarkHumanNodeRunningCommand {
            run_id: "sm-run-1".to_string(),
            node_id: "review".to_string(),
            attempt: 0,
            started_at_ms: 1_000,
            timeout_deadline_ms: 61_000,
        })
        .await
        .expect("mark Human node running");

    assert!(marked);
    let executes = db.executes.lock().await;
    assert_eq!(executes.len(), 1);
    assert!(executes[0].sql().contains("assignee_bot_id = ''"));
    assert_eq!(executes[0].params()[0], DbValue::from(1_000_u64));
    assert_eq!(executes[0].params()[1], DbValue::from(61_000_u64));
    assert_eq!(executes[0].params()[4], DbValue::from("review"));
}

#[tokio::test]
async fn mysql_human_response_is_atomically_persisted_once() {
    let db = Arc::new(RecordingDb::default());
    let store = MySqlCollaborationStore::new(db.clone(), "dev".to_string());

    let recorded = store
        .record_human_response_if_running(
            "sm-run-1",
            "review",
            0,
            "请补充风险说明".to_string(),
            "human_1001".to_string(),
        )
        .await
        .expect("record Human response");

    assert!(recorded);
    let executes = db.executes.lock().await;
    assert_eq!(executes.len(), 1);
    assert!(
        executes[0]
            .sql()
            .contains("SET artifact_text = ?, responded_by = ?")
    );
    assert!(executes[0].sql().contains("AND artifact_text IS NULL"));
    assert_eq!(
        executes[0].params()[0],
        DbValue::from("请补充风险说明")
    );
    assert_eq!(executes[0].params()[1], DbValue::from("human_1001"));
    assert_eq!(executes[0].params()[5], DbValue::from(0));
}

#[tokio::test]
async fn mysql_runtime_delivery_correlation_and_events_are_persistent() {
    let correlation = test_correlation();
    let db = Arc::new(RecordingDb {
        correlation_row: Mutex::new(Some(correlation_row(&correlation))),
        event_rows: Mutex::new(vec![collaboration_event_row(
            "sm-run-1",
            Some("answer"),
            Some(1),
            "chat.final",
            json!({"node_id": "answer", "attempt": 1, "text": "done"}),
            3_000,
        )]),
        ..RecordingDb::default()
    });
    let store = MySqlCollaborationStore::new(db.clone(), "dev".to_string());

    store
        .upsert_delivery_correlation(correlation.clone())
        .await
        .expect("upsert correlation");
    store
        .register_delivery_alias("delivery-1", "bot-run-1".to_string())
        .await
        .expect("register alias");
    let loaded = store
        .lookup_delivery_correlation("delivery-1")
        .await
        .expect("lookup correlation")
        .expect("correlation row");
    store
        .append_event(
            "sm-run-1",
            Some("answer"),
            Some(1),
            "chat.final",
            json!({"node_id": "answer", "attempt": 1, "text": "done"}),
            3_000,
        )
        .await
        .expect("append event");
    let events = store
        .list_events_by_run_and_type("sm-run-1", "chat.final")
        .await
        .expect("list events");
    let node_events = store
        .list_events_by_run_node_and_type("sm-run-1", "answer", "chat.final")
        .await
        .expect("list node events");

    assert_eq!(loaded.delivery_request_id, "delivery-1");
    assert_eq!(loaded.bot_delivery_run_id.as_deref(), Some("bot-run-1"));
    assert_eq!(events.len(), 1);
    assert_eq!(events[0].node_id.as_deref(), Some("answer"));
    assert_eq!(events[0].attempt, Some(1));
    assert_eq!(events[0].payload["text"].as_str(), Some("done"));
    assert_eq!(node_events.len(), 1);
    assert_eq!(node_events[0].payload["text"].as_str(), Some("done"));
    let transactions = db.transactions.lock().await;
    assert_eq!(transactions.len(), 1);
    assert_eq!(transactions[0].len(), 2);
    let executes = db.executes.lock().await;
    assert_eq!(executes.len(), 2);
    assert!(
        executes[0]
            .sql()
            .contains("bcs_state_machine_delivery_correlations")
    );
    assert!(executes[1].sql().contains("bcs_collaboration_events"));
    assert_eq!(executes[1].params()[2], DbValue::from("answer"));
    assert_eq!(executes[1].params()[3], DbValue::from(1));
}
