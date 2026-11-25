#[path = "support/history_contract.rs"]
mod history_contract;
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
    StateMachineLoopFrame, StateMachineNodeRun, StateMachineNodeStatus, StateMachineRun,
    StateMachineRunStatus,
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

use bcs_db_migrations as bootstrap_migrations;


#[path = "mysql_store/definitions.rs"]
mod definitions;

#[path = "mysql_store/run_lifecycle.rs"]
mod run_lifecycle;

#[path = "mysql_store/failure_paths.rs"]
mod failure_paths;

#[path = "support/real_mysql_fixed_loop.rs"]
mod real_mysql_fixed_loop;

#[path = "support/batched_nodes.rs"]
mod batched_nodes;

#[path = "support/branch_skip.rs"]
mod branch_skip;

#[path = "support/failure_contract.rs"]
mod failure_contract;

#[path = "support/judge_contract.rs"]
mod judge_contract;

#[path = "support/opening_contract.rs"]
mod opening_contract;

#[path = "support/progression_contract.rs"]
mod progression_contract;


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

fn nested_loop_path_snapshot_fixture() -> bcs_service_api::port::repo::StateMachineRunSnapshot {
    use sha2::{Digest, Sha256};

    let mut snapshot = fixed_loop_snapshot_fixture();
    let stored = snapshot.execution_plan.as_mut().expect("fixed Loop plan");
    stored.compiler_version = "bcs.fixed-loop.compiler/v2".into();
    stored.plan.compiler_version = stored.compiler_version.clone();
    for metadata in stored.plan.node_metadata.values_mut() {
        let (Some(loop_id), Some(iteration), Some(max_iterations)) = (
            metadata.loop_id.clone(), metadata.iteration, metadata.max_iterations,
        ) else { continue; };
        metadata.loop_path = Some(vec![
            StateMachineLoopFrame { loop_id: "outer".into(), iteration: 1, max_iterations: 1 },
            StateMachineLoopFrame { loop_id, iteration, max_iterations },
        ]);
    }
    stored.content_hash = format!("{:x}", Sha256::digest(serde_json::to_vec(&stored.plan).unwrap()));
    snapshot
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
    snapshot_fixture_round_trip_is_immutable(store, fixed_loop_snapshot_fixture()).await;
}

async fn nested_snapshot_round_trip_is_immutable(store: &dyn StateMachineDefinitionRepoPort) {
    snapshot_fixture_round_trip_is_immutable(store, nested_loop_path_snapshot_fixture()).await;
}

async fn snapshot_fixture_round_trip_is_immutable(
    store: &dyn StateMachineDefinitionRepoPort,
    snapshot: bcs_service_api::port::repo::StateMachineRunSnapshot,
) {
    let run = test_run();
    store.save_run_snapshot(&run, 7, &snapshot.definition, snapshot.resolved_participant_bindings.as_ref(), snapshot.execution_plan.as_ref()).await.unwrap();
    let mut changed = snapshot.clone();
    changed.definition.name = "Changed authoring".into();
    changed.resolved_participant_bindings.as_mut().unwrap().get_mut("driver").unwrap().bot_ids = vec!["replacement".into()];
    changed.execution_plan.as_mut().unwrap().content_hash = "0".repeat(64);
    store.save_run_snapshot(&run, 7, &changed.definition, changed.resolved_participant_bindings.as_ref(), changed.execution_plan.as_ref()).await.unwrap();
    let loaded = store.get_run_snapshot(&run.run_id).await.unwrap().unwrap();
    assert_snapshot_eq(&loaded, &snapshot);
}



async fn loop_rerun_copies_snapshot_once<T: StateMachineDefinitionRepoPort + StateMachineRunRepoPort>(
    store: &T,
    snapshot: bcs_service_api::port::repo::StateMachineRunSnapshot,
) {
    use bcs_service_api::{CreateStateMachineRerun, CreateStateMachineRerunOutcome};
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
