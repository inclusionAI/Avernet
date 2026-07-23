use std::{
    collections::{BTreeMap, VecDeque},
    sync::Arc,
};

use async_trait::async_trait;
use bcs_collaboration_store::MySqlCollaborationStore;
use bcs_db_api::{
    DbExecuteResult, DbHealth, DbPlugin, DbResult, DbRow, DbStatement, DbTransactionStep,
    DbTransactionStepResult, DbValue,
};
use bcs_domain::{
    CollaborationDefinition, CollaborationDefinitionRef, ParticipantRole, ResolvedParticipant,
    ResolvedParticipantBinding, RuntimeParticipantBinding, StateMachineDeliveryCorrelation,
    StateMachineNodeRun, StateMachineNodeStatus, StateMachineRun, StateMachineRunStatus,
};
use bcs_service_api::{
    CollaborationEventRepoPort, GroupRuntimeBindingRepoPort, StateMachineDefinitionRepoPort,
    StateMachineRunRepoPort,
};
use serde_json::json;
use tokio::sync::Mutex;

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

    assert!(err.to_string().contains("already exists with different content"));
    assert!(db.transactions.lock().await.is_empty());
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
        definition_id: definition.id.clone(),
        definition_version: definition.version,
        group_id: "group-1".to_string(),
        group_version: 7,
        session_id: "group-1:abcdef12".to_string(),
        created_by: Some("tester".to_string()),
        status: StateMachineRunStatus::Running,
        input: json!({"question": "hello"}),
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
        .save_run_snapshot(&run, 7, &definition, Some(&resolved))
        .await
        .expect("save snapshot");

    let executes = db.executes.lock().await;
    assert_eq!(executes.len(), 1);
    let stmt = &executes[0];
    assert!(stmt.sql().contains("bcs_state_machine_definition_snapshots"));
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

    assert_eq!(loaded.id, "sm_e2e_single");
    assert_eq!(loaded.version, 3);
    let queries = db.queries.lock().await;
    assert!(queries[0].sql().contains("bcs_state_machine_definition_snapshots"));
    assert_eq!(queries[0].params()[1], DbValue::from("sm-run-1"));
}

#[tokio::test]
async fn mysql_runtime_create_run_writes_run_and_node_rows() {
    let db = Arc::new(RecordingDb::default());
    let store = MySqlCollaborationStore::new(db.clone(), "dev".to_string());
    let run = test_run();
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
            assert_eq!(stmt.params()[5], DbValue::from(7));
            assert_eq!(stmt.params()[7], DbValue::from("tester"));
            assert_eq!(stmt.params()[8], DbValue::from("running"));
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
async fn mysql_runtime_reads_run_and_node_rows() {
    let run = test_run();
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

    let loaded_by_session = store
        .get_run_by_session_id("group-1:abcdef12")
        .await
        .expect("get run by session")
        .expect("run row");
    assert_eq!(loaded_by_session.run_id, "sm-run-1");

    let nodes = store
        .list_node_runs("sm-run-1")
        .await
        .expect("list nodes");
    assert_eq!(nodes.len(), 1);
    assert_eq!(nodes[0].node_timeout_ms, Some(120_000));
    assert_eq!(nodes[0].timeout_deadline_ms, Some(121_000));
    assert_eq!(nodes[0].max_attempts, 2);
    let queries = db.queries.lock().await;
    assert!(queries[1].sql().contains("session_id = ?"));
    assert_eq!(queries[1].params()[1], DbValue::from("group-1:abcdef12"));
}

#[tokio::test]
async fn mysql_runtime_node_and_run_updates_use_cas_sql() {
    let db = Arc::new(RecordingDb::default());
    let store = MySqlCollaborationStore::new(db.clone(), "dev".to_string());

    store
        .mark_node_running(
            "sm-run-1",
            "answer",
            1,
            "delivery-1".to_string(),
            1_000,
        )
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
            "done".to_string(),
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
    assert_eq!(executes[2].params()[5], DbValue::from(1));
    assert!(executes[3].sql().contains("status NOT IN ('completed', 'failed', 'aborted')"));
    assert!(executes[4].sql().contains("AND attempt = ? AND status = 'failed'"));
    assert_eq!(executes[4].params()[4], DbValue::from(1));
    assert!(executes[5].sql().contains("SET status = 'skipped'"));
    assert!(executes[5].sql().contains("AND status = 'pending'"));
    assert_eq!(executes[5].params()[0], DbValue::from(2_200_u64));
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
    assert!(executes[0].sql().contains("bcs_state_machine_delivery_correlations"));
    assert!(executes[1].sql().contains("bcs_collaboration_events"));
    assert_eq!(executes[1].params()[2], DbValue::from("answer"));
    assert_eq!(executes[1].params()[3], DbValue::from(1));
}

#[tokio::test]
async fn mysql_runtime_cas_failures_return_false() {
    let db = Arc::new(RecordingDb {
        execute_affected_rows: Mutex::new(VecDeque::from([0, 0, 0, 0, 0])),
        ..RecordingDb::default()
    });
    let store = MySqlCollaborationStore::new(db, "dev".to_string());

    let completed = store
        .complete_node_attempt(
            "sm-run-1",
            "answer",
            1,
            "done".to_string(),
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
    let failed = store
        .fail_node_attempt(
            "sm-run-1",
            "answer",
            1,
            "error".to_string(),
            2_000,
        )
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

#[derive(Default)]
struct RecordingDb {
    definition_json: Mutex<Option<String>>,
    snapshot_json: Mutex<Option<String>>,
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
                let blob_id = stmt.params().get(7).and_then(DbValue::as_str).map(str::to_string);
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
                ("normalized_json".to_string(), DbValue::from(definition_json)),
                ("yaml_text".to_string(), DbValue::Null),
            ]))]);
        }
        if statement.sql().contains("FROM bcs_state_machine_definition_snapshots") {
            return Ok(self
                .snapshot_json
                .lock()
                .await
                .clone()
                .map(|snapshot_json| {
                    DbRow::new(BTreeMap::from([(
                        "snapshot_json".to_string(),
                        DbValue::from(snapshot_json),
                    )]))
                })
                .into_iter()
                .collect());
        }
        if statement.sql().contains("FROM bcs_state_machine_runs") {
            return Ok(self.runtime_run_row.lock().await.clone().into_iter().collect());
        }
        if statement.sql().contains("FROM bcs_state_machine_node_runs") {
            return Ok(self.runtime_node_rows.lock().await.clone());
        }
        if statement.sql().contains("FROM bcs_state_machine_delivery_correlations") {
            return Ok(self.correlation_row.lock().await.clone().into_iter().collect());
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
        definition_id: "sm_e2e_single".to_string(),
        definition_version: 3,
        group_id: "group-1".to_string(),
        group_version: 7,
        session_id: "group-1:abcdef12".to_string(),
        created_by: Some("tester".to_string()),
        status: StateMachineRunStatus::Running,
        input: json!({"question": "hello"}),
        output: None,
        error: None,
        created_at: 1,
        updated_at: 1,
        completed_at: None,
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
        assignee_bot_id: "bot_sm_e2e_driver".to_string(),
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
    DbRow::new(BTreeMap::from([
        ("run_id".to_string(), DbValue::from(run.run_id.as_str())),
        ("definition_id".to_string(), DbValue::from(run.definition_id.as_str())),
        ("definition_version".to_string(), DbValue::from(run.definition_version)),
        ("group_id".to_string(), DbValue::from(run.group_id.as_str())),
        ("group_version".to_string(), DbValue::from(run.group_version)),
        ("session_id".to_string(), DbValue::from(run.session_id.as_str())),
        ("created_by".to_string(), DbValue::from(run.created_by.as_deref())),
        ("status".to_string(), DbValue::from("running")),
        ("input_json".to_string(), DbValue::from(run.input.to_string())),
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
        ("timeout_deadline_ms".to_string(), DbValue::from(121_000_u64)),
        ("max_attempts".to_string(), DbValue::from(node.max_attempts)),
        ("assignee_bot_id".to_string(), DbValue::from(node.assignee_bot_id.as_str())),
        ("delivery_request_id".to_string(), DbValue::from("delivery-1")),
        ("bot_delivery_run_id".to_string(), DbValue::from("bot-run-1")),
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
        ("node_id".to_string(), DbValue::from(correlation.node_id.as_str())),
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
        ("attempt".to_string(), attempt.map(DbValue::from).unwrap_or(DbValue::Null)),
        ("event_type".to_string(), DbValue::from(event_type)),
        ("payload_json".to_string(), DbValue::from(payload.to_string())),
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
