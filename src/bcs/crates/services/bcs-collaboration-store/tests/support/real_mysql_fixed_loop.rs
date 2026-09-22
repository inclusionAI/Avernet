use super::*;
use bcs_config_api::{MysqlDbConfig, StatementProtocol};
use bcs_config_api::mysql::MysqlConnectionConfig;
use bcs_db_mysql::{MysqlDbManager, MysqlDbPlugin};
use bcs_service_api::{CreateStateMachineRerun, CreateStateMachineRerunOutcome};
use mysql_async::Opts;

const TABLES: [&str; 9] = ["bcs_messages", "bcs_collaboration_delivery_checkpoints", "bcs_collaboration_events", "bcs_collaboration_definition_blobs", "bcs_collaboration_definitions", "bcs_group_sessions",
    "bcs_state_machine_definition_snapshots", "bcs_state_machine_node_runs", "bcs_state_machine_runs"];

#[tokio::test]
#[ignore = "requires BCS_TEST_MYSQL_URL pointing to a disposable MySQL database"]
async fn real_mysql_fixed_loop_snapshot_and_rerun_contract() {
    let url = std::env::var("BCS_TEST_MYSQL_URL").expect("BCS_TEST_MYSQL_URL is required");
    let opts = Opts::from_url(&url).expect("valid MySQL test URL");
    for protocol in [StatementProtocol::Text, StatementProtocol::Prepared] {
        let mut config = MysqlDbConfig::new().with_database(opts.db_name().expect("test database"))
            .with_connection(MysqlConnectionConfig {
                connection_type: "direct".into(), host: Some(opts.ip_or_hostname().to_string()),
                port: Some(opts.tcp_port()), user: opts.user().map(str::to_string),
                password: opts.pass().map(str::to_string), extra: BTreeMap::new(),
            }).with_statement_protocol(protocol);
        config.pool_size = 4;
        config.min_pool_size = 1;
        let manager = MysqlDbManager::new(config).await.unwrap();
        let db = Arc::new(MysqlDbPlugin::new(manager.clone(), "bcs"));
        // Never replace existing application tables or run destructive cleanup
        // on a database whose ownership was not established by this test.
        for table in TABLES {
            let rows = db.query(DbStatement::with_params(
                "SELECT COUNT(*) AS count FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = ?",
                vec![DbValue::from(table)],
            )).await.unwrap();
            assert_eq!(bcs_db_api::db_get_column::<i64>(&rows[0], "count").unwrap(), 0, "requires an empty test schema: {table}");
        }
        // This fixture contains only CREATE TABLE statements, without routines
        // or literals/comments containing semicolons.
        for sql in include_str!("../fixtures/mysql_pre_loop.sql").split(';').map(str::trim).filter(|sql| !sql.is_empty()) {
            db.execute(DbStatement::new(sql)).await.unwrap();
        }
        let result = exercise(db.clone()).await;
        for table in TABLES {
            db.execute(DbStatement::new(format!("DROP TABLE `{table}`"))).await.unwrap();
        }
        manager.close().await;
        result.unwrap();
    }
}

async fn exercise(db: Arc<MysqlDbPlugin>) -> Result<(), Box<dyn std::error::Error>> {
    db.execute(DbStatement::new(
        "INSERT INTO bcs_state_machine_definition_snapshots (env, run_id, group_id, session_id, group_version, definition_id, definition_version, definition_content_hash, snapshot_json) \
         VALUES ('test', 'legacy', 'group-1', 'legacy-session', 1, 'legacy', 1, REPEAT('a', 64), JSON_OBJECT('version', 1))"
    )).await?;
    // The consolidated migration has five plain DDL statements.
    for sql in include_str!("../../../../../migrations/mysql/028_fixed_loop_runtime.sql").split(';').map(str::trim).filter(|sql| !sql.is_empty()) {
        db.execute(DbStatement::new(sql)).await?;
    }
    let legacy = db.query(DbStatement::new("SELECT snapshot_json, execution_plan_json, execution_plan_content_hash, execution_plan_compiler_version FROM bcs_state_machine_definition_snapshots WHERE run_id = 'legacy'")).await?.remove(0);
    for field in ["execution_plan_json", "execution_plan_content_hash", "execution_plan_compiler_version"] {
        assert_eq!(bcs_db_api::db_get_column_opt::<String>(&legacy, field)?, None);
    }
    assert_eq!(serde_json::from_str::<serde_json::Value>(&bcs_db_api::db_get_column::<String>(&legacy, "snapshot_json")?)?, json!({"version": 1}));
    let store = MySqlCollaborationStore::new(db.clone(), "test".into());
    history_contract::contract(&store).await;
    history_contract::provider_output_contract(&store, db.as_ref()).await;
    judge_contract::judge_contract(&store, &store).await;
    dispatch_contract::dispatch_contract(&store).await;
    opening_contract::opening_contract(&store).await;
    publication_contract::publication_contract(&store).await;
    recovery_gap_contract::recovery_gap_contract(&store, &store).await;
    terminal_cleanup_contract::terminal_cleanup_contract(&store).await;
    // A failed startup-fact insert must roll back the conditional Run failure.
    let mut gap = test_run(); gap.run_id = "gap-rollback".into(); gap.session_id = "gap-rollback-session".into();
    gap.status = StateMachineRunStatus::Pending; gap.created_at = 100;
    store.create_run(gap.clone(), Vec::new()).await?;
    db.execute(DbStatement::new("ALTER TABLE bcs_collaboration_delivery_checkpoints ADD CONSTRAINT reject_gap_failure CHECK (aggregate_id <> 'gap-rollback')")).await?;
    assert!(store.fail_missing_startup(bcs_service_api::FailStateMachineStartup { run_id: gap.run_id.clone(),
        missing: bcs_service_api::StateMachineMissingStartupFact::Snapshot, failed_at_ms: 90_100, preparation_error: None }).await.is_err());
    assert_eq!(store.get_run(&gap.run_id).await?.unwrap().status, StateMachineRunStatus::Pending);
    assert!(store.get_startup_failure(&gap.run_id).await?.is_none());
    db.execute(DbStatement::new("ALTER TABLE bcs_collaboration_delivery_checkpoints DROP CHECK reject_gap_failure")).await?;

    terminal_im_contract::terminal_im_contract(&store, &bcs_session_store::MySqlSessionStore::new(db.clone(), "test".into())).await;
    db.execute(DbStatement::new("INSERT INTO bcs_group_sessions (env, group_id, session_id, session_kind, status, input, activation_count, participants) VALUES ('test', 'opening-group', 'opening-session', 'chat', 'running', '{}', 1, '[]')")).await?;
    opening_contract::message_identity_contract(&bcs_message_store::MySqlMessageStore::new(db.clone(), "test".into())).await;
    snapshot_round_trip_is_immutable(&store).await;
    db.execute(DbStatement::new("INSERT INTO bcs_group_sessions (env, session_id, group_id, session_kind, status, activation_count, participants) VALUES ('test', 'group-1:abcdef12', 'group-1', 'chat', 'running', 1, '[]')")).await?;
    fixed_loop_rerun_copies_snapshot_once(&store).await;
    assert!(MySqlCollaborationStore::new(db.clone(), "other".into()).get_run_snapshot("sm-run-1").await?.is_none());
    service_rerun_reactivates_once(&store, db.as_ref()).await?;
    snapshot_write_failure_is_visible(&store, db.as_ref()).await?;
    rerun_snapshot_failure_rolls_back_activation(&store, db.as_ref()).await?;
    batched_nodes::contract(db.as_ref(), &store).await?;
    Ok(())
}

async fn service_rerun_reactivates_once(store: &MySqlCollaborationStore, db: &dyn DbPlugin) -> Result<(), Box<dyn std::error::Error>> {
    let snapshot = fixed_loop_snapshot_fixture();
    db.execute(DbStatement::new("INSERT INTO bcs_group_sessions (env, session_id, group_id, session_kind, status, activation_count, callback_status, participants, output) VALUES ('test', 'service-rerun', 'group-1', 'service_invocation', 'completed', 4, 'not_applicable', '[]', 'old output')")).await?;
    let mut source = test_run();
    source.run_id = "service-source".into();
    source.root_run_id = Some(source.run_id.clone());
    source.session_id = "service-rerun".into();
    source.session_activation_count = Some(4);
    source.status = StateMachineRunStatus::Failed;
    source.error = Some("source failure".into());
    source.completed_at = Some(3);
    store.create_run(source.clone(), Vec::new()).await?;
    store.save_run_snapshot(&source, 7, &snapshot.definition, snapshot.resolved_participant_bindings.as_ref(), snapshot.execution_plan.as_ref()).await?;
    let mut child = source.clone();
    child.run_id = "service-child".into();
    child.rerun_of = Some(source.run_id.clone());
    child.session_activation_count = Some(5);
    child.status = StateMachineRunStatus::Pending;
    child.error = None;
    child.completed_at = None;
    let nodes = snapshot.execution_plan.as_ref().unwrap().plan.node_metadata.keys().map(|node_id| {
        serde_json::from_value::<StateMachineNodeRun>(json!({"run_id": child.run_id, "node_id": node_id, "status": "pending", "attempt": 0}))
    }).collect::<Result<Vec<_>, _>>()?;
    let command = CreateStateMachineRerun { source_run_id: source.run_id.clone(), run: child.clone(), nodes, reactivate_service_session: true };
    let mut competitor = command.clone();
    competitor.run.run_id = "service-competitor".into();
    for node in &mut competitor.nodes { node.run_id = competitor.run.run_id.clone(); }
    let (a, b) = tokio::join!(store.create_rerun_if_session_idle(command), store.create_rerun_if_session_idle(competitor));
    let outcomes = [a?, b?];
    assert_eq!(outcomes.iter().filter(|outcome| matches!(outcome, CreateStateMachineRerunOutcome::Created)).count(), 1);
    assert_eq!(outcomes.iter().filter(|outcome| matches!(outcome, CreateStateMachineRerunOutcome::Existing(_))).count(), 1);
    let winner = store.get_direct_rerun(&source.run_id).await?.unwrap();
    assert_eq!(winner.session_activation_count, Some(5));
    let nodes = store.list_node_runs(&winner.run_id).await?;
    assert_eq!(nodes.len(), 3);
    for node in nodes {
        assert_eq!((node.status, node.attempt), (StateMachineNodeStatus::Pending, 0));
        assert!(node.artifact_text.is_none());
    }
    assert_snapshot_eq(&store.get_run_snapshot(&winner.run_id).await?.unwrap(), &snapshot);
    let session = db.query(DbStatement::new("SELECT status, activation_count, output FROM bcs_group_sessions WHERE session_id = 'service-rerun'")).await?.remove(0);
    assert_eq!(bcs_db_api::db_get_column::<i32>(&session, "activation_count")?, 5);
    assert_eq!(bcs_db_api::db_get_column::<String>(&session, "status")?, "running");
    assert_eq!(bcs_db_api::db_get_column_opt::<String>(&session, "output")?, None);
    assert_eq!(store.get_run(&source.run_id).await?.unwrap().status, StateMachineRunStatus::Failed);
    Ok(())
}

async fn snapshot_write_failure_is_visible(store: &MySqlCollaborationStore, db: &dyn DbPlugin) -> Result<(), Box<dyn std::error::Error>> {
    db.execute(DbStatement::new("ALTER TABLE bcs_state_machine_definition_snapshots ADD CONSTRAINT bcs_loop_reject_write CHECK (run_id <> 'write-failure')")).await?;
    let mut run = test_run();
    run.run_id = "write-failure".into();
    let snapshot = fixed_loop_snapshot_fixture();
    let result = store.save_run_snapshot(&run, 7, &snapshot.definition, snapshot.resolved_participant_bindings.as_ref(), snapshot.execution_plan.as_ref()).await;
    assert!(result.unwrap_err().to_string().contains("bcs_loop_reject_write"));
    assert!(store.get_run_snapshot(&run.run_id).await?.is_none());
    db.execute(DbStatement::new("ALTER TABLE bcs_state_machine_definition_snapshots DROP CHECK bcs_loop_reject_write")).await?;
    Ok(())
}

async fn rerun_snapshot_failure_rolls_back_activation(store: &MySqlCollaborationStore, db: &dyn DbPlugin) -> Result<(), Box<dyn std::error::Error>> {
    db.execute(DbStatement::new("INSERT INTO bcs_group_sessions (env, session_id, group_id, session_kind, status, activation_count, callback_status, participants, output) VALUES ('test', 'service-rollback', 'group-1', 'service_invocation', 'completed', 4, 'not_applicable', '[]', 'original output')")).await?;
    let snapshot = fixed_loop_snapshot_fixture();
    let mut source = test_run();
    source.run_id = "rollback-source".into();
    source.root_run_id = Some(source.run_id.clone());
    source.session_id = "service-rollback".into();
    source.session_activation_count = Some(4);
    source.status = StateMachineRunStatus::Failed;
    source.error = Some("source failure".into());
    source.completed_at = Some(3);
    store.create_run(source.clone(), Vec::new()).await?;
    store.save_run_snapshot(&source, 7, &snapshot.definition, snapshot.resolved_participant_bindings.as_ref(), snapshot.execution_plan.as_ref()).await?;
    let mut child = source.clone();
    child.run_id = "rollback-child".into();
    child.rerun_of = Some(source.run_id.clone());
    child.session_activation_count = Some(5);
    child.status = StateMachineRunStatus::Pending;
    child.error = None;
    child.completed_at = None;
    let node = serde_json::from_value::<StateMachineNodeRun>(json!({"run_id": child.run_id, "node_id": "ln-stored-1", "status": "pending", "attempt": 0}))?;
    db.execute(DbStatement::new("ALTER TABLE bcs_state_machine_definition_snapshots ADD CONSTRAINT bcs_loop_reject_rerun CHECK (run_id <> 'rollback-child')")).await?;
    let result = store.create_rerun_if_session_idle(CreateStateMachineRerun {
        source_run_id: source.run_id.clone(), run: child.clone(), nodes: vec![node], reactivate_service_session: true,
    }).await;
    assert!(result.unwrap_err().to_string().contains("bcs_loop_reject_rerun"));
    assert!(store.get_run(&child.run_id).await?.is_none());
    assert!(store.get_direct_rerun(&source.run_id).await?.is_none());
    assert!(store.get_run_snapshot(&child.run_id).await?.is_none());
    assert!(store.list_node_runs(&child.run_id).await?.is_empty());
    let session = db.query(DbStatement::new("SELECT status, activation_count, output FROM bcs_group_sessions WHERE session_id = 'service-rollback'")).await?.remove(0);
    assert_eq!(bcs_db_api::db_get_column::<String>(&session, "status")?, "completed");
    assert_eq!(bcs_db_api::db_get_column::<i32>(&session, "activation_count")?, 4);
    assert_eq!(bcs_db_api::db_get_column::<String>(&session, "output")?, "original output");
    assert_snapshot_eq(&store.get_run_snapshot(&source.run_id).await?.unwrap(), &snapshot);
    db.execute(DbStatement::new("ALTER TABLE bcs_state_machine_definition_snapshots DROP CHECK bcs_loop_reject_rerun")).await?;
    Ok(())
}
