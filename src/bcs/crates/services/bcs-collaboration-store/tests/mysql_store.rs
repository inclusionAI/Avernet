#[path = "support/recovery_gap_contract.rs"]
mod recovery_gap_contract;
#[path = "support/terminal_cleanup_contract.rs"]
mod terminal_cleanup_contract;
use std::{
    collections::{BTreeMap, VecDeque},
    sync::Arc,
};

use async_trait::async_trait;
use bcs_collaboration_store::{MemoryCollaborationStore, MySqlCollaborationStore};
use bcs_db_api::{
    DbError, DbExecuteResult, DbHealth, DbPlugin, DbResult, DbRow, DbStatement, DbTransactionStep,
    DbTransactionStepResult, DbValue,
};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_domain::{
    CollaborationDefinition, CollaborationDefinitionRef, OpeningMessage, ParticipantRole, ResolvedParticipant,
    ResolvedParticipantBinding, RuntimeParticipantBinding, StateMachineDeliveryCorrelation,
    StateMachineNodeRun, StateMachineNodeStatus, StateMachineRun, StateMachineRunStatus,
};
use bcs_event_store::DbEventStore;
use bcs_service_api::port::repo::{AppendEventRecord, StateMachineEventfulTransition};
use bcs_service_api::port::{EventRepoPort, NewEvent};
use bcs_service_api::types::{EVENT_SCHEMA_VERSION_V1, EventScope, EventSubject};
use bcs_service_api::{
    CollaborationEventRepoPort, GroupRuntimeBindingRepoPort, MarkHumanNodeRunningCommand,
    StateMachineDefinitionRepoPort, StateMachineRunRepoPort,
};
use serde_json::json;
use tokio::sync::Mutex;

#[path = "../../../bootstrap/bcs/src/migrations.rs"]
#[allow(dead_code)]
mod bootstrap_migrations;

#[path = "support/real_mysql_fixed_loop.rs"]
mod real_mysql_fixed_loop;

#[path = "support/batched_nodes.rs"]
mod batched_nodes;

#[path = "support/failure_contract.rs"]
mod failure_contract;

#[path = "support/judge_contract.rs"]
mod judge_contract;

#[path = "support/opening_contract.rs"]
mod opening_contract;

#[path = "support/progression_contract.rs"]
mod progression_contract;

#[tokio::test]
async fn mysql_progression_query_is_bounded_and_propagates_database_failure() {
    let db = Arc::new(RecordingDb::default());
    let store = MySqlCollaborationStore::new(db.clone(), "test".into());
    store.list_running_runs(Some("run-cursor"), 7).await.unwrap();
    let queries = db.queries.lock().await;
    let query = &queries[0];
    assert!(query.sql().contains("env = ? AND status = 'running' AND record_status = 'active'"));
    assert!(query.sql().contains("run_id > ? ORDER BY run_id ASC LIMIT ?"));
    assert_eq!(query.params(), &[DbValue::from("test"), DbValue::from("run-cursor"), DbValue::from(7u64)]);
    assert!(MySqlCollaborationStore::new(Arc::new(AlwaysFailDb), "test".into())
        .list_running_runs(None, 7).await.is_err());
}

fn fixed_loop_snapshot_fixture() -> bcs_service_api::port::repo::StateMachineRunSnapshot {
    use bcs_service_api::port::repo::{StateMachineExecutionPlanSnapshot, StateMachineRunSnapshot};
    use sha2::{Digest, Sha256};

    let mut definition = serde_json::to_value(test_definition()).unwrap();
    let answer = definition["runtime"]["state_machine"]["nodes"]["answer"].clone();
    let mut body = answer.clone();
    body["final_output"] = json!(false);
    definition["runtime"]["state_machine"] = json!({
        "version": 2, "graph_mode": "hierarchical",
        "nodes": {
            "rounds": {"kind": "loop", "display_name": "Rounds", "loop": {
                "mode": "fixed", "max_iterations": 2, "entry_node": "step", "result_node": "step",
                "continue_outcomes": ["complete"], "break_outcomes": [], "exhausted_outcome": "exhausted",
                "nodes": {"step": body.clone()}
            }, "transitions": {"exhausted": {"targets": ["answer"]}}},
            "answer": answer.clone()
        }
    });
    let mut first = body.clone();
    first["transitions"] = json!({"complete": {"targets": ["ln-stored-2"]}});
    let mut second = body;
    second["transitions"] = json!({"complete": {"targets": ["answer"]}});
    let compiler = "bcs.fixed-loop.compiler/v1";
    let plan: bcs_domain::StateMachineExecutionPlan = serde_json::from_value(json!({
        "compiler_version": compiler,
        "state_machine": {"version": 1, "graph_mode": "acyclic", "nodes": {
            "ln-stored-1": first, "ln-stored-2": second, "answer": answer
        }},
        "node_metadata": {
            "ln-stored-1": {"execution_node_id": "ln-stored-1", "definition_node_id": "step", "loop_id": "rounds", "iteration": 1, "max_iterations": 2, "is_loop_entry": true, "is_loop_result": true, "previous_result_node_id": null},
            "ln-stored-2": {"execution_node_id": "ln-stored-2", "definition_node_id": "step", "loop_id": "rounds", "iteration": 2, "max_iterations": 2, "is_loop_entry": true, "is_loop_result": true, "previous_result_node_id": "ln-stored-1"},
            "answer": {"execution_node_id": "answer", "definition_node_id": "answer", "loop_id": null, "iteration": null, "max_iterations": null, "is_loop_entry": false, "is_loop_result": false, "previous_result_node_id": null}
        },
        "edge_metadata": [
            {"source_execution_node_id": "ln-stored-1", "outcome": "complete", "target_execution_node_id": "ln-stored-2", "artifact_projection": "control_only", "loop_route": {"kind": "continue", "logical_outcome": "complete"}},
            {"source_execution_node_id": "ln-stored-2", "outcome": "complete", "target_execution_node_id": "answer", "artifact_projection": "artifact", "loop_route": {"kind": "exhausted", "logical_outcome": "exhausted"}}
        ]
    })).unwrap();
    let content_hash = format!("{:x}", Sha256::digest(serde_json::to_vec(&plan).unwrap()));
    StateMachineRunSnapshot {
        definition: serde_json::from_value(definition).unwrap(),
        execution_plan: Some(StateMachineExecutionPlanSnapshot { plan, content_hash, compiler_version: compiler.into() }),
        resolved_participant_bindings: Some(BTreeMap::from([("driver".into(), ResolvedParticipantBinding {
            source: "group_runtime_binding".into(), binding_source: Some("manual".into()),
            bot_ids: vec!["original-bot".into()], participants: Vec::new(), extensions: Default::default(),
        })])),
    }
}

fn assert_snapshot_eq(
    actual: &bcs_service_api::port::repo::StateMachineRunSnapshot,
    expected: &bcs_service_api::port::repo::StateMachineRunSnapshot,
) {
    assert_eq!(serde_json::to_value(&actual.definition).unwrap(), serde_json::to_value(&expected.definition).unwrap());
    let actual_plan = actual.execution_plan.as_ref().unwrap();
    let expected_plan = expected.execution_plan.as_ref().unwrap();
    assert_eq!(serde_json::to_value(&actual_plan.plan).unwrap(), serde_json::to_value(&expected_plan.plan).unwrap());
    assert_eq!(actual_plan.content_hash, expected_plan.content_hash);
    assert_eq!(actual_plan.compiler_version, expected_plan.compiler_version);
    assert_eq!(serde_json::to_value(&actual.resolved_participant_bindings).unwrap(), serde_json::to_value(&expected.resolved_participant_bindings).unwrap());
}

async fn snapshot_round_trip_is_immutable(store: &dyn StateMachineDefinitionRepoPort) {
    let run = test_run();
    let snapshot = fixed_loop_snapshot_fixture();
    store.save_run_snapshot(&run, 7, &snapshot.definition, snapshot.resolved_participant_bindings.as_ref(), snapshot.execution_plan.as_ref()).await.unwrap();
    let mut changed = snapshot.clone();
    changed.definition.name = "Changed authoring".into();
    changed.resolved_participant_bindings.as_mut().unwrap().get_mut("driver").unwrap().bot_ids = vec!["replacement".into()];
    changed.execution_plan.as_mut().unwrap().content_hash = "0".repeat(64);
    store.save_run_snapshot(&run, 7, &changed.definition, changed.resolved_participant_bindings.as_ref(), changed.execution_plan.as_ref()).await.unwrap();
    let loaded = store.get_run_snapshot(&run.run_id).await.unwrap().unwrap();
    assert_snapshot_eq(&loaded, &snapshot);
}

#[tokio::test]
async fn memory_fixed_loop_snapshot_round_trip_is_immutable() {
    snapshot_round_trip_is_immutable(&MemoryCollaborationStore::new()).await;
}

#[tokio::test]
async fn sqlite_fixed_loop_snapshot_round_trip_is_immutable() {
    let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new().unwrap());
    bootstrap_migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
    snapshot_round_trip_is_immutable(&MySqlCollaborationStore::sqlite(db, "test".into())).await;
}

#[tokio::test]
async fn mysql_fixed_loop_snapshot_preserves_all_fields_in_one_write_and_read() {
    let snapshot = fixed_loop_snapshot_fixture();
    let db = Arc::new(RecordingDb::default());
    let store = MySqlCollaborationStore::new(db.clone(), "test".into());
    store.save_run_snapshot(&test_run(), 7, &snapshot.definition, snapshot.resolved_participant_bindings.as_ref(), snapshot.execution_plan.as_ref()).await.unwrap();
    let writes = db.executes.lock().await;
    assert_eq!(writes.len(), 1);
    let statement = &writes[0];
    assert!(statement.sql().contains("execution_plan_json, execution_plan_content_hash, execution_plan_compiler_version"));
    assert_eq!(statement.params().len(), 13);
    *db.snapshot_json.lock().await = Some(statement.params()[8].as_str().unwrap().into());
    let mut columns = db.snapshot_extra_columns.lock().await;
    for (index, name) in [(9, "resolved_participant_bindings_json"), (10, "execution_plan_json"), (11, "execution_plan_content_hash"), (12, "execution_plan_compiler_version")] {
        columns.insert(name.into(), statement.params()[index].clone());
    }
    drop(columns);
    drop(writes);
    let loaded = store.get_run_snapshot("sm-run-1").await.unwrap().unwrap();
    assert_snapshot_eq(&loaded, &snapshot);
}

#[tokio::test]
async fn mysql_fixed_loop_snapshot_rejects_partial_or_malformed_persisted_plan() {
    let snapshot = fixed_loop_snapshot_fixture();
    for value in [None, Some("{invalid"), Some("null")] {
        let db = Arc::new(RecordingDb {
            snapshot_json: Mutex::new(Some(serde_json::to_string(&snapshot.definition).unwrap())),
            ..RecordingDb::default()
        });
        if let Some(value) = value {
            let mut columns = db.snapshot_extra_columns.lock().await;
            columns.insert("execution_plan_json".into(), DbValue::from(value));
            columns.insert("execution_plan_content_hash".into(), DbValue::from("0".repeat(64)));
            columns.insert("execution_plan_compiler_version".into(), DbValue::from("bcs.fixed-loop.compiler/v1"));
        }
        let store = MySqlCollaborationStore::new(db, "test".into());
        assert!(store.get_run_snapshot("sm-run-1").await.is_err());
    }
}

#[tokio::test]
async fn fixed_loop_snapshot_write_failure_is_propagated_and_missing_plan_is_rejected() {
    let snapshot = fixed_loop_snapshot_fixture();
    let failed = MySqlCollaborationStore::new(Arc::new(AlwaysFailDb), "test".into());
    assert!(failed.save_run_snapshot(&test_run(), 7, &snapshot.definition,
        snapshot.resolved_participant_bindings.as_ref(), snapshot.execution_plan.as_ref()).await.is_err());
    let memory = MemoryCollaborationStore::new();
    assert!(memory.save_run_snapshot(&test_run(), 7, &snapshot.definition,
        snapshot.resolved_participant_bindings.as_ref(), None).await.is_err());
    assert!(memory.get_run_snapshot("sm-run-1").await.unwrap().is_none());
}

async fn fixed_loop_rerun_copies_snapshot_once<T: StateMachineDefinitionRepoPort + StateMachineRunRepoPort>(store: &T) {
    use bcs_service_api::{CreateStateMachineRerun, CreateStateMachineRerunOutcome};
    let snapshot = fixed_loop_snapshot_fixture();
    let mut source = test_run();
    source.status = StateMachineRunStatus::Failed;
    source.error = Some("source failure".into());
    source.completed_at = Some(2);
    store.create_run(source.clone(), Vec::new()).await.unwrap();
    store.save_run_snapshot(&source, 7, &snapshot.definition,
        snapshot.resolved_participant_bindings.as_ref(), snapshot.execution_plan.as_ref()).await.unwrap();
    // The mutable authoring store can be changed independently of the source snapshot.
    let mut replacement = test_definition();
    replacement.name = "Current definition has changed".into();
    store.upsert(replacement).await.unwrap();
    let mut child = source.clone();
    child.run_id = "sm-loop-child".into();
    child.rerun_of = Some(source.run_id.clone());
    child.status = StateMachineRunStatus::Pending;
    child.error = None;
    child.completed_at = None;
    let command = CreateStateMachineRerun {
        source_run_id: source.run_id.clone(), run: child.clone(), nodes: Vec::new(), reactivate_service_session: false,
    };
    let mut competing = command.clone();
    competing.run.run_id = "sm-loop-competitor".into();
    let (first, second) = tokio::join!(store.create_rerun_if_session_idle(command), store.create_rerun_if_session_idle(competing));
    let results = [first.unwrap(), second.unwrap()];
    assert_eq!(results.iter().filter(|result| matches!(result, CreateStateMachineRerunOutcome::Created)).count(), 1);
    assert_eq!(results.iter().filter(|result| matches!(result, CreateStateMachineRerunOutcome::Existing(_))).count(), 1);
    let child = store.get_direct_rerun(&source.run_id).await.unwrap().unwrap();
    assert_eq!(child.root_run_id, source.root_run_id);
    assert_eq!(child.status, StateMachineRunStatus::Pending);
    assert!(child.error.is_none());
    let loaded = store.get_run_snapshot(&child.run_id).await.unwrap().unwrap();
    assert_snapshot_eq(&loaded, &snapshot);
}

#[tokio::test]
async fn memory_fixed_loop_rerun_preserves_source_plan_and_concurrent_uniqueness() {
    fixed_loop_rerun_copies_snapshot_once(&MemoryCollaborationStore::new()).await;
}

#[tokio::test]
async fn sqlite_fixed_loop_rerun_preserves_source_plan_and_concurrent_uniqueness() {
    let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new().unwrap());
    bootstrap_migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
    db.execute(DbStatement::new(
        "INSERT INTO bcs_group_sessions (env, session_id, group_id, session_kind, status, activation_count, participants) \
         VALUES ('test', 'group-1:abcdef12', 'group-1', 'chat', 'running', 1, '[]')"
    )).await.unwrap();
    fixed_loop_rerun_copies_snapshot_once(&MySqlCollaborationStore::sqlite(db, "test".into())).await;
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
async fn mysql_definition_upsert_and_get_use_009_definition_table() {
    let definition = test_definition();
    let db = Arc::new(RecordingDb {
        definition_json: Mutex::new(Some(serde_json::to_string(&definition).unwrap())),
        definition_metadata_rows: Mutex::new(VecDeque::from([None])),
        ..RecordingDb::default()
    });
    let store = MySqlCollaborationStore::new(db.clone(), "dev".to_string());

    StateMachineDefinitionRepoPort::upsert(&store, definition.clone())
        .await
        .expect("upsert definition");
    let loaded = StateMachineDefinitionRepoPort::get(&store, &definition.id, definition.version)
        .await
        .expect("get definition")
        .expect("definition row");

    assert_eq!(loaded.id, definition.id);
    assert_eq!(loaded.version, definition.version);

    let transactions = db.transactions.lock().await;
    assert_eq!(transactions.len(), 1);
    let steps = &transactions[0];
    assert_eq!(steps.len(), 2);
    match &steps[0] {
        DbTransactionStep::Execute(stmt) => {
            assert!(stmt.sql().contains("bcs_collaboration_definition_blobs"));
            assert!(stmt.sql().contains("ON DUPLICATE KEY UPDATE"));
        }
        _ => panic!("expected blob execute step"),
    }
    match &steps[1] {
        DbTransactionStep::Execute(stmt) => {
            assert!(stmt.sql().contains("bcs_collaboration_definitions"));
            assert!(stmt.sql().contains("INSERT IGNORE"));
            assert!(!stmt.sql().contains("ON DUPLICATE KEY UPDATE"));
        }
        _ => panic!("expected definition execute step"),
    }

    let queries = db.queries.lock().await;
    assert_eq!(queries.len(), 3);
    assert!(queries[0].sql().contains("content_hash, blob_id"));
    assert!(queries[1].sql().contains("content_hash, blob_id"));
    assert!(queries[2].sql().contains("normalized_json"));
}

#[tokio::test]
async fn mysql_definition_upsert_rejects_same_id_version_with_different_content() {
    let definition = test_definition();
    let db = Arc::new(RecordingDb {
        definition_metadata_rows: Mutex::new(VecDeque::from([Some((
            "existing-different-hash".to_string(),
            Some("existing-blob".to_string()),
        ))])),
        ..RecordingDb::default()
    });
    let store = MySqlCollaborationStore::new(db.clone(), "dev".to_string());

    let err = StateMachineDefinitionRepoPort::upsert(&store, definition)
        .await
        .expect_err("different content should conflict");

    assert!(
        err.to_string()
            .contains("already exists with different content")
    );
    assert!(db.transactions.lock().await.is_empty());
}

#[tokio::test]
async fn mysql_definition_upsert_with_yaml_records_yaml_source_format() {
    let definition = test_definition();
    let db = Arc::new(RecordingDb {
        definition_metadata_rows: Mutex::new(VecDeque::from([None])),
        ..RecordingDb::default()
    });
    let store = MySqlCollaborationStore::new(db.clone(), "dev".to_string());

    StateMachineDefinitionRepoPort::upsert_with_source_yaml(
        &store,
        definition,
        "name: source yaml".to_string(),
    )
    .await
    .expect("upsert definition with source YAML");

    let transactions = db.transactions.lock().await;
    let DbTransactionStep::Execute(statement) = &transactions[0][1] else {
        panic!("expected definition execute step");
    };
    assert_eq!(statement.params()[5], DbValue::from("yaml"));
    assert_eq!(statement.params()[8], DbValue::from("name: source yaml"));
}

#[tokio::test]
async fn mysql_definition_and_snapshot_reads_reject_invalid_persisted_json() {
    let db = Arc::new(RecordingDb {
        definition_json: Mutex::new(Some("{invalid".to_string())),
        snapshot_json: Mutex::new(Some("{invalid".to_string())),
        ..RecordingDb::default()
    });
    let store = MySqlCollaborationStore::new(db, "dev".to_string());

    assert!(
        StateMachineDefinitionRepoPort::get(&store, "invalid", 1)
            .await
            .is_err()
    );
    assert!(store.get_run_snapshot("invalid-run").await.is_err());
}

#[tokio::test]
async fn mysql_binding_rejects_missing_definition_metadata() {
    let definition = test_definition();
    let db = Arc::new(RecordingDb {
        definition_metadata_rows: Mutex::new(VecDeque::from([None])),
        ..RecordingDb::default()
    });
    let store = MySqlCollaborationStore::new(db, "dev".to_string());

    let error = store
        .bind_default_definition(
            "group-1",
            1,
            Some(CollaborationDefinitionRef {
                id: definition.id,
                version: definition.version,
            }),
            None,
            false,
        )
        .await
        .expect_err("missing definition metadata must reject binding");
    assert!(error.to_string().contains("not found for binding"));
}

#[tokio::test]
async fn mysql_binding_writes_group_version_and_denormalized_definition_snapshot() {
    let db = Arc::new(RecordingDb::with_definition(test_definition()));
    let store = MySqlCollaborationStore::new(db.clone(), "dev".to_string());

    store
        .bind_default_definition(
            "group-1",
            7,
            Some(CollaborationDefinitionRef {
                id: "sm_e2e_single".to_string(),
                version: 3,
            }),
            Some(BTreeMap::from([(
                "driver".to_string(),
                RuntimeParticipantBinding {
                    source: "manual".to_string(),
                    bot_ids: vec!["bot_sm_e2e_driver".to_string()],
                    extensions: Default::default(),
                },
            )])),
            true,
        )
        .await
        .expect("bind definition");

    let transactions = db.transactions.lock().await;
    assert_eq!(transactions.len(), 1);
    let steps = &transactions[0];
    assert_eq!(steps.len(), 2);
    match &steps[1] {
        DbTransactionStep::Execute(stmt) => {
            assert!(stmt.sql().contains("bcs_group_runtime_bindings"));
            assert!(stmt.sql().contains("ON DUPLICATE KEY UPDATE"));
            assert_eq!(stmt.params()[2], DbValue::from(7));
            assert_eq!(stmt.params()[3], DbValue::from(2_147_483_647_i32));
            assert_eq!(stmt.params()[4], DbValue::from("sm_e2e_single"));
            assert_eq!(stmt.params()[5], DbValue::from(3));
            assert_eq!(stmt.params()[6], DbValue::from("hash-from-definition"));
            assert_eq!(stmt.params()[7], DbValue::from("blob-from-definition"));
            assert_eq!(stmt.params()[8], DbValue::from(true));
            assert!(stmt.sql().contains("participant_bindings_json"));
            assert_eq!(
                stmt.params()[9],
                DbValue::from(
                    serde_json::to_string(&BTreeMap::from([(
                        "driver".to_string(),
                        RuntimeParticipantBinding {
                            source: "manual".to_string(),
                            bot_ids: vec!["bot_sm_e2e_driver".to_string()],
                            extensions: Default::default(),
                        },
                    )]))
                    .unwrap()
                    .as_str()
                )
            );
        }
        _ => panic!("expected execute step"),
    }
}

#[tokio::test]
async fn mysql_definition_snapshot_writes_run_definition_snapshot_table() {
    let definition = test_definition();
    let db = Arc::new(RecordingDb::with_definition(definition.clone()));
    let store = MySqlCollaborationStore::new(db.clone(), "dev".to_string());
    let run = StateMachineRun {
        run_id: "sm-run-1".to_string(),
        root_run_id: Some("sm-run-1".to_string()),
        rerun_of: None,
        definition_id: definition.id.clone(),
        definition_version: definition.version,
        group_id: "group-1".to_string(),
        group_version: 7,
        session_id: "group-1:abcdef12".to_string(),
        session_activation_count: None,
        created_by: Some("tester".to_string()),
        status: StateMachineRunStatus::Running,
        input: json!({"question": "hello"}),
        opening_message_override: None,
        output: None,
        error: None,
        created_at: 1,
        updated_at: 1,
        completed_at: None,
    };

    let resolved = BTreeMap::from([(
        "driver".to_string(),
        ResolvedParticipantBinding {
            source: "group_runtime_binding".to_string(),
            binding_source: Some("manual".to_string()),
            bot_ids: vec!["bot_sm_e2e_driver".to_string()],
            participants: vec![ResolvedParticipant {
                bot_id: "bot_sm_e2e_driver".to_string(),
                bcs_participant_role: ParticipantRole::Driver,
            }],
            extensions: Default::default(),
        },
    )]);
    store
        .save_run_snapshot(&run, 7, &definition, Some(&resolved), None)
        .await
        .expect("save snapshot");

    let executes = db.executes.lock().await;
    assert_eq!(executes.len(), 1);
    let stmt = &executes[0];
    assert!(
        stmt.sql()
            .contains("bcs_state_machine_definition_snapshots")
    );
    assert!(stmt.sql().contains("ON DUPLICATE KEY UPDATE"));
    assert!(stmt.sql().contains("env=env"));
    assert_eq!(stmt.params()[1], DbValue::from("sm-run-1"));
    assert_eq!(stmt.params()[4], DbValue::from(7));
    assert_eq!(stmt.params()[5], DbValue::from("sm_e2e_single"));
    assert!(stmt.sql().contains("resolved_participant_bindings_json"));
    assert_eq!(
        stmt.params()[9],
        DbValue::from(serde_json::to_string(&resolved).unwrap().as_str())
    );
}

#[tokio::test]
async fn mysql_definition_snapshot_reads_run_definition_snapshot_table() {
    let definition = test_definition();
    let db = Arc::new(RecordingDb {
        snapshot_json: Mutex::new(Some(serde_json::to_string(&definition).unwrap())),
        ..RecordingDb::default()
    });
    let store = MySqlCollaborationStore::new(db.clone(), "dev".to_string());

    let loaded = store
        .get_run_snapshot("sm-run-1")
        .await
        .expect("get snapshot")
        .expect("snapshot");

    assert_eq!(loaded.definition.id, "sm_e2e_single");
    assert_eq!(loaded.definition.version, 3);
    let queries = db.queries.lock().await;
    assert!(
        queries[0]
            .sql()
            .contains("bcs_state_machine_definition_snapshots")
    );
    assert_eq!(queries[0].params()[1], DbValue::from("sm-run-1"));
}

#[tokio::test]
async fn mysql_legacy_empty_snapshot_keeps_the_existing_missing_snapshot_result() {
    let db = Arc::new(RecordingDb {
        snapshot_json: Mutex::new(Some(String::new())),
        ..RecordingDb::default()
    });
    let store = MySqlCollaborationStore::new(db, "test".into());
    assert!(store.get_run_snapshot("legacy-run").await.unwrap().is_none());
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
async fn mysql_runtime_rejects_invalid_persisted_opening_message_override_json() {
    let run = test_run();
    let db = Arc::new(RecordingDb {
        runtime_run_row: Mutex::new(Some(run_row_with_opening_json(
            &run,
            DbValue::from("{invalid"),
        ))),
        ..RecordingDb::default()
    });
    let store = MySqlCollaborationStore::new(db, "dev".to_string());

    let error = store
        .get_run(&run.run_id)
        .await
        .expect_err("invalid persisted override must fail the read");
    assert!(error.to_string().contains("opening_message_override_json"));
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

#[tokio::test]
async fn mysql_runtime_cas_failures_return_false() {
    let db = Arc::new(RecordingDb {
        execute_affected_rows: Mutex::new(VecDeque::from([0, 0, 0, 0, 0, 0])),
        ..RecordingDb::default()
    });
    let store = MySqlCollaborationStore::new(db, "dev".to_string());

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
    let artifact_recorded = store
        .record_node_artifact_if_running(
            "sm-run-1",
            "answer",
            1,
            "candidate".to_string(),
        )
        .await
        .expect("record node artifact");
    let human_response_recorded = store
        .record_human_response_if_running(
            "sm-run-1",
            "review",
            1,
            "response".to_string(),
            "human_1001".to_string(),
        )
        .await
        .expect("record Human response");
    let failed = store
        .fail_node_attempt("sm-run-1", "answer", 1, "error".to_string(), 2_000)
        .await
        .expect("fail node");
    let retry_scheduled = store
        .schedule_node_retry("sm-run-1", "answer", 1, 2)
        .await
        .expect("schedule retry");
    let run_updated = store
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

    assert!(!completed);
    assert!(!artifact_recorded);
    assert!(!human_response_recorded);
    assert!(!failed);
    assert!(!retry_scheduled);
    assert!(!run_updated);
}

#[tokio::test]
async fn mysql_runtime_register_delivery_alias_errors_when_correlation_missing() {
    let db = Arc::new(RecordingDb {
        transaction_affected_rows: Mutex::new(VecDeque::from([0, 0])),
        ..RecordingDb::default()
    });
    let store = MySqlCollaborationStore::new(db, "dev".to_string());

    let err = store
        .register_delivery_alias("missing-delivery", "bot-run-1".to_string())
        .await
        .expect_err("missing correlation should error");

    assert!(err.to_string().contains("delivery correlation not found"));
}

#[tokio::test]
async fn memory_runtime_rejects_updates_for_missing_runs_nodes_and_correlations() {
    let store = MemoryCollaborationStore::new();

    assert!(
        store
            .update_run_status(
                "missing-run",
                StateMachineRunStatus::Failed,
                None,
                Some("failed".to_string()),
                2_000,
                Some(2_000),
            )
            .await
            .is_err()
    );
    assert!(
        store
            .mark_node_running(
                "missing-run",
                "missing-node",
                0,
                "delivery-1".to_string(),
                1_000,
            )
            .await
            .is_err()
    );
    assert!(
        store
            .register_delivery_alias("missing-delivery", "bot-run-1".to_string())
            .await
            .is_err()
    );

    store
        .create_run(test_run(), Vec::new())
        .await
        .expect("seed run without nodes");
    assert!(
        store
            .mark_node_running(
                "sm-run-1",
                "missing-node",
                0,
                "delivery-1".to_string(),
                1_000,
            )
            .await
            .is_err()
    );
}

#[tokio::test]
async fn mysql_runtime_propagates_database_failures_across_repository_operations() {
    let store = MySqlCollaborationStore::new(Arc::new(AlwaysFailDb), "dev".to_string());
    let definition = test_definition();
    let run = test_run();
    let node = test_node();

    assert!(
        StateMachineDefinitionRepoPort::upsert(&store, definition.clone())
            .await
            .is_err()
    );
    assert!(
        StateMachineDefinitionRepoPort::get(&store, &definition.id, definition.version)
            .await
            .is_err()
    );
    assert!(
        store
            .save_run_snapshot(&run, run.group_version, &definition, None, None)
            .await
            .is_err()
    );
    assert!(store.get_run_snapshot(&run.run_id).await.is_err());
    assert!(
        GroupRuntimeBindingRepoPort::get(&store, &run.group_id)
            .await
            .is_err()
    );
    assert!(
        store
            .bind_default_definition(&run.group_id, run.group_version, None, None, false)
            .await
            .is_err()
    );
    assert!(
        store
            .bind_default_definition_if_current(
                &run.group_id,
                run.group_version,
                None,
                None,
                None,
                false,
            )
            .await
            .is_err()
    );
    assert!(
        store
            .create_run(run.clone(), vec![node.clone()])
            .await
            .is_err()
    );
    assert!(store.get_run(&run.run_id).await.is_err());
    assert!(store.get_run_by_session_id(&run.session_id).await.is_err());
    assert!(store.list_node_runs(&run.run_id).await.is_err());
    assert!(
        store
            .get_node_run(&run.run_id, &node.node_id)
            .await
            .is_err()
    );
    assert!(
        store
            .mark_node_running(
                &run.run_id,
                &node.node_id,
                node.attempt,
                "delivery-failed".to_string(),
                1_000,
            )
            .await
            .is_err()
    );
    assert!(
        store
            .mark_node_running_if_run_active(
                &run.run_id,
                &node.node_id,
                node.attempt,
                "delivery-cas-failed".to_string(),
                1_000,
            )
            .await
            .is_err()
    );
    assert!(
        store
            .mark_human_node_running_if_run_active(MarkHumanNodeRunningCommand {
                run_id: run.run_id.clone(),
                node_id: "review".to_string(),
                attempt: 0,
                started_at_ms: 1_000,
                timeout_deadline_ms: 61_000,
            })
            .await
            .is_err()
    );
    assert!(
        store
            .record_node_artifact_if_running(
                &run.run_id,
                &node.node_id,
                node.attempt,
                "artifact".to_string(),
            )
            .await
            .is_err()
    );
    assert!(
        store
            .record_human_response_if_running(
                &run.run_id,
                "review",
                0,
                "response".to_string(),
                "human_1001".to_string(),
            )
            .await
            .is_err()
    );
    assert!(
        store
            .complete_node_attempt(
                &run.run_id,
                &node.node_id,
                node.attempt,
                "complete".to_string(),
                "artifact".to_string(),
                None,
                2_000,
            )
            .await
            .is_err()
    );
    assert!(
        store
            .fail_node_attempt(
                &run.run_id,
                &node.node_id,
                node.attempt,
                "failed".to_string(),
                2_000,
            )
            .await
            .is_err()
    );
    assert!(
        store
            .schedule_node_retry(&run.run_id, &node.node_id, node.attempt, node.attempt + 1)
            .await
            .is_err()
    );
    assert!(
        store
            .skip_node(&run.run_id, &node.node_id, 2_000)
            .await
            .is_err()
    );
    assert!(
        store
            .update_run_status(
                &run.run_id,
                StateMachineRunStatus::Failed,
                None,
                Some("failed".to_string()),
                2_000,
                Some(2_000),
            )
            .await
            .is_err()
    );
    assert!(
        store
            .upsert_delivery_correlation(test_correlation())
            .await
            .is_err()
    );
    assert!(
        store
            .register_delivery_alias("delivery-1", "bot-run-1".to_string())
            .await
            .is_err()
    );
    assert!(
        store
            .lookup_delivery_correlation("delivery-1")
            .await
            .is_err()
    );
    assert!(
        store
            .list_expired_running_node_runs(10_000, 0, 10)
            .await
            .is_err()
    );
    assert!(
        store
            .append_event(
                &run.run_id,
                Some(&node.node_id),
                Some(node.attempt),
                "test.event",
                json!({"ok": true}),
                2_000,
            )
            .await
            .is_err()
    );
    assert!(
        store
            .list_events_by_run_and_type(&run.run_id, "test.event")
            .await
            .is_err()
    );
    assert!(
        store
            .list_events_by_run_node_and_type(&run.run_id, &node.node_id, "test.event")
            .await
            .is_err()
    );
}

#[tokio::test]
async fn mysql_alias_lookup_propagates_second_query_failure() {
    let store =
        MySqlCollaborationStore::new(Arc::new(AliasLookupFailDb), "dev".to_string());

    assert!(
        store
            .lookup_delivery_correlation("bot-run-1")
            .await
            .is_err()
    );
}

struct AlwaysFailDb;

#[async_trait]
impl DbPlugin for AlwaysFailDb {
    async fn query(&self, _statement: DbStatement) -> DbResult<Vec<DbRow>> {
        Err(DbError::Backend("forced query failure".to_string()))
    }

    async fn execute(&self, _statement: DbStatement) -> DbResult<DbExecuteResult> {
        Err(DbError::Backend("forced execute failure".to_string()))
    }

    async fn transaction(
        &self,
        _steps: Vec<DbTransactionStep>,
    ) -> DbResult<Vec<DbTransactionStepResult>> {
        Err(DbError::Backend("forced transaction failure".to_string()))
    }

    async fn health_check(&self) -> DbResult<DbHealth> {
        Err(DbError::Backend("forced health failure".to_string()))
    }
}

struct AliasLookupFailDb;

#[async_trait]
impl DbPlugin for AliasLookupFailDb {
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
        if statement.sql().contains("delivery_request_id = ?") {
            Ok(Vec::new())
        } else {
            Err(DbError::Backend("forced alias lookup failure".to_string()))
        }
    }

    async fn execute(&self, _statement: DbStatement) -> DbResult<DbExecuteResult> {
        unreachable!("alias lookup test does not execute statements")
    }

    async fn transaction(
        &self,
        _steps: Vec<DbTransactionStep>,
    ) -> DbResult<Vec<DbTransactionStepResult>> {
        unreachable!("alias lookup test does not execute transactions")
    }

    async fn health_check(&self) -> DbResult<DbHealth> {
        Ok(DbHealth::healthy())
    }
}

#[derive(Default)]
struct RecordingDb {
    definition_json: Mutex<Option<String>>,
    snapshot_json: Mutex<Option<String>>,
    snapshot_extra_columns: Mutex<BTreeMap<String, DbValue>>,
    definition_metadata_rows: Mutex<VecDeque<Option<(String, Option<String>)>>>,
    runtime_run_row: Mutex<Option<DbRow>>,
    runtime_node_rows: Mutex<Vec<DbRow>>,
    correlation_row: Mutex<Option<DbRow>>,
    event_rows: Mutex<Vec<DbRow>>,
    execute_affected_rows: Mutex<VecDeque<u64>>,
    transaction_affected_rows: Mutex<VecDeque<u64>>,
    queries: Mutex<Vec<DbStatement>>,
    executes: Mutex<Vec<DbStatement>>,
    transactions: Mutex<Vec<Vec<DbTransactionStep>>>,
}

impl RecordingDb {
    fn with_definition(definition: CollaborationDefinition) -> Self {
        Self {
            definition_json: Mutex::new(Some(serde_json::to_string(&definition).unwrap())),
            ..Self::default()
        }
    }

    async fn last_inserted_definition_metadata(&self) -> Option<(String, Option<String>)> {
        let transactions = self.transactions.lock().await;
        for steps in transactions.iter().rev() {
            for step in steps.iter().rev() {
                let DbTransactionStep::Execute(stmt) = step else {
                    continue;
                };
                if !stmt.sql().contains("bcs_collaboration_definitions") {
                    continue;
                }
                let content_hash = stmt.params().get(6)?.as_str()?.to_string();
                let blob_id = stmt
                    .params()
                    .get(7)
                    .and_then(DbValue::as_str)
                    .map(str::to_string);
                return Some((content_hash, blob_id));
            }
        }
        None
    }
}

#[async_trait]
impl DbPlugin for RecordingDb {
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
        self.queries.lock().await.push(statement.clone());
        if statement.sql().contains("content_hash, blob_id") {
            if let Some(metadata) = self.definition_metadata_rows.lock().await.pop_front() {
                return Ok(metadata.map(definition_metadata_row).into_iter().collect());
            }
            if let Some(metadata) = self.last_inserted_definition_metadata().await {
                return Ok(vec![definition_metadata_row(metadata)]);
            }
            return Ok(vec![definition_metadata_row((
                "hash-from-definition".to_string(),
                Some("blob-from-definition".to_string()),
            ))]);
        }
        if statement.sql().contains("normalized_json") {
            let definition_json = self.definition_json.lock().await.clone().unwrap();
            return Ok(vec![DbRow::new(BTreeMap::from([
                (
                    "normalized_json".to_string(),
                    DbValue::from(definition_json),
                ),
                ("yaml_text".to_string(), DbValue::Null),
            ]))]);
        }
        if statement
            .sql()
            .contains("FROM bcs_state_machine_definition_snapshots")
        {
            let extra_columns = self.snapshot_extra_columns.lock().await.clone();
            return Ok(self
                .snapshot_json
                .lock()
                .await
                .clone()
                .map(|snapshot_json| {
                    let mut columns = extra_columns;
                    columns.insert("snapshot_json".to_string(), DbValue::from(snapshot_json));
                    DbRow::new(columns)
                })
                .into_iter()
                .collect());
        }
        if statement.sql().contains("FROM bcs_state_machine_runs") {
            return Ok(self
                .runtime_run_row
                .lock()
                .await
                .clone()
                .into_iter()
                .collect());
        }
        if statement.sql().contains("FROM bcs_state_machine_node_runs") {
            return Ok(self.runtime_node_rows.lock().await.clone());
        }
        if statement
            .sql()
            .contains("FROM bcs_state_machine_delivery_correlations")
        {
            return Ok(self
                .correlation_row
                .lock()
                .await
                .clone()
                .into_iter()
                .collect());
        }
        if statement.sql().contains("FROM bcs_collaboration_events") {
            return Ok(self.event_rows.lock().await.clone());
        }
        Ok(Vec::new())
    }

    async fn execute(&self, statement: DbStatement) -> DbResult<DbExecuteResult> {
        self.executes.lock().await.push(statement);
        let affected_rows = self
            .execute_affected_rows
            .lock()
            .await
            .pop_front()
            .unwrap_or(1);
        Ok(DbExecuteResult {
            affected_rows,
            last_insert_id: None,
        })
    }

    async fn transaction(
        &self,
        steps: Vec<DbTransactionStep>,
    ) -> DbResult<Vec<DbTransactionStepResult>> {
        self.transactions.lock().await.push(steps.clone());
        let mut affected_rows = self.transaction_affected_rows.lock().await;
        Ok(steps
            .into_iter()
            .map(|_| {
                DbTransactionStepResult::Executed(DbExecuteResult {
                    affected_rows: affected_rows.pop_front().unwrap_or(1),
                    last_insert_id: None,
                })
            })
            .collect())
    }

    async fn health_check(&self) -> DbResult<DbHealth> {
        Ok(DbHealth::healthy())
    }
}

fn test_run() -> StateMachineRun {
    StateMachineRun {
        run_id: "sm-run-1".to_string(),
        root_run_id: Some("sm-run-1".to_string()),
        rerun_of: None,
        definition_id: "sm_e2e_single".to_string(),
        definition_version: 3,
        group_id: "group-1".to_string(),
        group_version: 7,
        session_id: "group-1:abcdef12".to_string(),
        session_activation_count: None,
        created_by: Some("tester".to_string()),
        status: StateMachineRunStatus::Running,
        input: json!({"question": "hello"}),
        opening_message_override: None,
        output: None,
        error: None,
        created_at: 1,
        updated_at: 1,
        completed_at: None,
    }
}

fn public_event(
    event_id: &str,
    event_type: &str,
    causation_event_id: Option<&str>,
) -> AppendEventRecord {
    AppendEventRecord {
        event: NewEvent {
            event_id: event_id.to_string(),
            event_type: event_type.to_string(),
            schema_version: EVENT_SCHEMA_VERSION_V1.to_string(),
            producer: "bcs-collaboration-runtime".to_string(),
            producer_key: event_id.to_string(),
            occurred_at: "2026-08-19T00:00:00.000Z".to_string(),
            subject: EventSubject {
                subject_type: "state_machine.run".to_string(),
                id: "sm-run-atomic".to_string(),
            },
            scope: EventScope {
                group_id: Some("group-1".to_string()),
                session_id: Some("group-1:abcdef12".to_string()),
                run_id: Some("sm-run-atomic".to_string()),
                ..EventScope::default()
            },
            stream_key: "state-machine-run:sm-run-atomic".to_string(),
            actor: None,
            correlation_id: None,
            causation_event_id: causation_event_id.map(str::to_string),
            trace_id: None,
            data: Default::default(),
        },
        recorded_at: "2026-08-19T00:00:00.001Z".to_string(),
        retention_until_ms: 2_000_000_000_000,
        env: "test".to_string(),
    }
}

fn test_node() -> StateMachineNodeRun {
    StateMachineNodeRun {
        run_id: "sm-run-1".to_string(),
        node_id: "answer".to_string(),
        status: StateMachineNodeStatus::Running,
        attempt: 1,
        node_timeout_ms: Some(120_000),
        timeout_deadline_ms: Some(121_000),
        max_attempts: 2,
        assignee_bot_id: Some("bot_sm_e2e_driver".to_string()),
        outcome: None,
        responded_by: None,
        delivery_request_id: Some("delivery-1".to_string()),
        bot_delivery_run_id: Some("bot-run-1".to_string()),
        artifact_text: None,
        error: None,
        started_at: Some(1_000),
        completed_at: None,
    }
}

fn test_correlation() -> StateMachineDeliveryCorrelation {
    StateMachineDeliveryCorrelation {
        state_machine_run_id: "sm-run-1".to_string(),
        node_id: "answer".to_string(),
        attempt: 1,
        assignee_bot_id: "bot_sm_e2e_driver".to_string(),
        delivery_request_id: "delivery-1".to_string(),
        bot_delivery_run_id: Some("bot-run-1".to_string()),
    }
}

fn run_row(run: &StateMachineRun) -> DbRow {
    let opening_message_override_json = run
        .opening_message_override
        .as_ref()
        .map(|opening_message| DbValue::from(serde_json::to_string(opening_message).unwrap()))
        .unwrap_or(DbValue::Null);
    run_row_with_opening_json(run, opening_message_override_json)
}

fn run_row_with_opening_json(
    run: &StateMachineRun,
    opening_message_override_json: DbValue,
) -> DbRow {
    DbRow::new(BTreeMap::from([
        ("run_id".to_string(), DbValue::from(run.run_id.as_str())),
        (
            "definition_id".to_string(),
            DbValue::from(run.definition_id.as_str()),
        ),
        (
            "definition_version".to_string(),
            DbValue::from(run.definition_version),
        ),
        ("group_id".to_string(), DbValue::from(run.group_id.as_str())),
        (
            "group_version".to_string(),
            DbValue::from(run.group_version),
        ),
        (
            "session_id".to_string(),
            DbValue::from(run.session_id.as_str()),
        ),
        (
            "created_by".to_string(),
            DbValue::from(run.created_by.as_deref()),
        ),
        ("status".to_string(), DbValue::from("running")),
        (
            "input_json".to_string(),
            DbValue::from(run.input.to_string()),
        ),
        (
            "opening_message_override_json".to_string(),
            opening_message_override_json,
        ),
        ("output_text".to_string(), DbValue::Null),
        ("error_message".to_string(), DbValue::Null),
        ("created_at_ms".to_string(), DbValue::from(run.created_at)),
        ("updated_at_ms".to_string(), DbValue::from(run.updated_at)),
        ("completed_at_ms".to_string(), DbValue::Null),
    ]))
}

fn node_row(node: &StateMachineNodeRun) -> DbRow {
    DbRow::new(BTreeMap::from([
        ("run_id".to_string(), DbValue::from(node.run_id.as_str())),
        ("node_id".to_string(), DbValue::from(node.node_id.as_str())),
        ("status".to_string(), DbValue::from("running")),
        ("attempt".to_string(), DbValue::from(node.attempt)),
        ("node_timeout_ms".to_string(), DbValue::from(120_000_u64)),
        (
            "timeout_deadline_ms".to_string(),
            DbValue::from(121_000_u64),
        ),
        ("max_attempts".to_string(), DbValue::from(node.max_attempts)),
        (
            "assignee_bot_id".to_string(),
            DbValue::from(node.assignee_bot_id.as_deref()),
        ),
        (
            "outcome".to_string(),
            DbValue::from(node.outcome.as_deref()),
        ),
        (
            "responded_by".to_string(),
            DbValue::from(node.responded_by.as_deref()),
        ),
        (
            "delivery_request_id".to_string(),
            DbValue::from("delivery-1"),
        ),
        (
            "bot_delivery_run_id".to_string(),
            DbValue::from("bot-run-1"),
        ),
        ("artifact_text".to_string(), DbValue::Null),
        ("error_message".to_string(), DbValue::Null),
        ("started_at_ms".to_string(), DbValue::from(1_000_u64)),
        ("completed_at_ms".to_string(), DbValue::Null),
    ]))
}

fn correlation_row(correlation: &StateMachineDeliveryCorrelation) -> DbRow {
    DbRow::new(BTreeMap::from([
        (
            "state_machine_run_id".to_string(),
            DbValue::from(correlation.state_machine_run_id.as_str()),
        ),
        (
            "node_id".to_string(),
            DbValue::from(correlation.node_id.as_str()),
        ),
        ("attempt".to_string(), DbValue::from(correlation.attempt)),
        (
            "assignee_bot_id".to_string(),
            DbValue::from(correlation.assignee_bot_id.as_str()),
        ),
        (
            "delivery_request_id".to_string(),
            DbValue::from(correlation.delivery_request_id.as_str()),
        ),
        (
            "bot_delivery_run_id".to_string(),
            DbValue::from(correlation.bot_delivery_run_id.as_deref()),
        ),
    ]))
}

fn collaboration_event_row(
    run_id: &str,
    node_id: Option<&str>,
    attempt: Option<i32>,
    event_type: &str,
    payload: serde_json::Value,
    created_at: u64,
) -> DbRow {
    DbRow::new(BTreeMap::from([
        ("state_machine_run_id".to_string(), DbValue::from(run_id)),
        ("node_id".to_string(), DbValue::from(node_id)),
        (
            "attempt".to_string(),
            attempt.map(DbValue::from).unwrap_or(DbValue::Null),
        ),
        ("event_type".to_string(), DbValue::from(event_type)),
        (
            "payload_json".to_string(),
            DbValue::from(payload.to_string()),
        ),
        ("created_at_ms".to_string(), DbValue::from(created_at)),
    ]))
}

fn definition_metadata_row((content_hash, blob_id): (String, Option<String>)) -> DbRow {
    DbRow::new(BTreeMap::from([
        ("content_hash".to_string(), DbValue::from(content_hash)),
        ("blob_id".to_string(), DbValue::from(blob_id.as_deref())),
    ]))
}

fn test_definition() -> CollaborationDefinition {
    serde_yaml::from_str(
        r#"
api_version: bcs.collaboration/v1
id: sm_e2e_single
version: 3
name: SM E2E Single
participants:
  driver:
    bot_id: bot_sm_e2e_driver
    required: true
runtime:
  kind: state_machine
  state_machine:
    version: 1
    graph_mode: acyclic
    nodes:
      answer:
        kind: bot_task
        display_name: Answer
        assignee:
          type: bot_binding
          binding: driver
        instruction: Answer the user query.
        final_output: true
"#,
    )
    .expect("valid definition")
}

#[path = "support/dispatch_contract.rs"]
mod dispatch_contract;

#[path = "support/publication_contract.rs"]
mod publication_contract;

#[path = "support/terminal_im_contract.rs"]
mod terminal_im_contract;
