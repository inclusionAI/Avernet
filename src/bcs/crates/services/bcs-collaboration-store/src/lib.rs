use std::{
    collections::{BTreeMap, BTreeSet},
    sync::Arc,
};

use async_trait::async_trait;
use bcs_db_api::{
    DbError, DbPlugin, DbRow, DbSqlFlavor, DbStatement, DbTransactionStep, DbTransactionStepResult,
    DbValue, db_get_column, db_get_column_opt,
};
use bcs_domain::{
    CollaborationDefinition, CollaborationDefinitionRef, GroupRuntimeBinding,
    ResolvedParticipantBinding, RuntimeParticipantBinding, StateMachineDeliveryCorrelation,
    StateMachineNodeRun, StateMachineNodeStatus, StateMachineRun, StateMachineRunStatus,
};
use bcs_service_api::{
    CollaborationDefinitionRecord, CollaborationEventRecord, CollaborationEventRepoPort,
    CollaborationTemplateEntry, CollaborationTemplateRepoPort, GroupRuntimeBindingRepoPort,
    MarkHumanNodeRunningCommand, ServiceError, ServiceResult, StateMachineDefinitionRepoPort,
    StateMachineRunRepoPort,
};
use sha2::{Digest, Sha256};
use tokio::sync::RwLock;

const CURRENT_GROUP_VERSION_SENTINEL: i32 = 2_147_483_647;
const SM_RUN_SELECT_COLS: &str = "run_id, definition_id, definition_version, group_id, \
    group_version, session_id, created_by, status, input_json, output_text, error_message, \
    created_at_ms, updated_at_ms, completed_at_ms";
const SM_NODE_SELECT_COLS: &str = "run_id, node_id, status, attempt, node_timeout_ms, \
    timeout_deadline_ms, max_attempts, assignee_bot_id, outcome, responded_by, \
    delivery_request_id, bot_delivery_run_id, artifact_text, error_message, \
    started_at_ms, completed_at_ms";
const SM_CORRELATION_SELECT_COLS: &str = "state_machine_run_id, node_id, attempt, \
    assignee_bot_id, delivery_request_id, bot_delivery_run_id";

#[derive(Debug, Default)]
struct StoreInner {
    definitions: BTreeMap<(String, i32), CollaborationDefinition>,
    definition_sources: BTreeMap<(String, i32), DefinitionSourceRecord>,
    run_snapshots: BTreeMap<String, CollaborationDefinition>,
    run_resolved_participant_bindings:
        BTreeMap<String, BTreeMap<String, ResolvedParticipantBinding>>,
    bindings: BTreeMap<String, GroupRuntimeBinding>,
    runs: BTreeMap<String, StateMachineRun>,
    nodes: BTreeMap<(String, String), StateMachineNodeRun>,
    correlations: BTreeMap<String, StateMachineDeliveryCorrelation>,
    correlation_aliases: BTreeMap<String, String>,
    events: Vec<CollaborationEventRecord>,
}

#[derive(Debug, Clone)]
struct DefinitionSourceRecord {
    source_format: String,
    yaml_text: Option<String>,
    content_hash: String,
}

#[derive(Debug, Default, Clone)]
pub struct MemoryCollaborationStore {
    inner: Arc<RwLock<StoreInner>>,
}

impl MemoryCollaborationStore {
    pub fn new() -> Self {
        Self::default()
    }
}

#[async_trait]
impl StateMachineDefinitionRepoPort for MemoryCollaborationStore {
    async fn upsert(&self, definition: CollaborationDefinition) -> ServiceResult<()> {
        let mut inner = self.inner.write().await;
        let key = (definition.id.clone(), definition.version);
        let incoming_json = definition_json(&definition)?;
        let incoming_hash = sha256_hex(incoming_json.as_bytes());
        if let Some(existing) = inner.definitions.get(&key) {
            let existing_json = definition_json(existing)?;
            let existing_hash = sha256_hex(existing_json.as_bytes());
            if existing_hash != incoming_hash {
                return Err(definition_conflict_error(
                    &definition.id,
                    definition.version,
                    &existing_hash,
                    &incoming_hash,
                ));
            }
            inner
                .definition_sources
                .entry(key)
                .or_insert(DefinitionSourceRecord {
                    source_format: "json".to_string(),
                    yaml_text: None,
                    content_hash: incoming_hash,
                });
            return Ok(());
        }
        inner.definitions.insert(key.clone(), definition);
        inner.definition_sources.insert(
            key,
            DefinitionSourceRecord {
                source_format: "json".to_string(),
                yaml_text: None,
                content_hash: incoming_hash,
            },
        );
        Ok(())
    }

    async fn upsert_with_source_yaml(
        &self,
        definition: CollaborationDefinition,
        source_yaml: String,
    ) -> ServiceResult<()> {
        let mut inner = self.inner.write().await;
        let key = (definition.id.clone(), definition.version);
        let incoming_json = definition_json(&definition)?;
        let incoming_hash = sha256_hex(incoming_json.as_bytes());
        if let Some(existing) = inner.definitions.get(&key) {
            let existing_json = definition_json(existing)?;
            let existing_hash = sha256_hex(existing_json.as_bytes());
            if existing_hash != incoming_hash {
                return Err(definition_conflict_error(
                    &definition.id,
                    definition.version,
                    &existing_hash,
                    &incoming_hash,
                ));
            }
            if let Some(existing_source) = inner.definition_sources.get(&key) {
                if let Some(existing_yaml) = existing_source.yaml_text.as_deref() {
                    if existing_yaml != source_yaml {
                        return Err(ServiceError::Conflict(format!(
                            "CollaborationDefinition '{}@{}' already exists with different source YAML",
                            definition.id, definition.version
                        )));
                    }
                }
            }
            inner.definition_sources.insert(
                key,
                DefinitionSourceRecord {
                    source_format: "yaml".to_string(),
                    yaml_text: Some(source_yaml),
                    content_hash: incoming_hash,
                },
            );
            return Ok(());
        }
        inner.definitions.insert(key.clone(), definition);
        inner.definition_sources.insert(
            key,
            DefinitionSourceRecord {
                source_format: "yaml".to_string(),
                yaml_text: Some(source_yaml),
                content_hash: incoming_hash,
            },
        );
        Ok(())
    }

    async fn get(&self, id: &str, version: i32) -> ServiceResult<Option<CollaborationDefinition>> {
        let inner = self.inner.read().await;
        Ok(inner.definitions.get(&(id.to_string(), version)).cloned())
    }

    async fn get_record(
        &self,
        id: &str,
        version: i32,
    ) -> ServiceResult<Option<CollaborationDefinitionRecord>> {
        let inner = self.inner.read().await;
        let key = (id.to_string(), version);
        let Some(definition) = inner.definitions.get(&key).cloned() else {
            return Ok(None);
        };
        let source = inner.definition_sources.get(&key);
        let computed_hash = if source.is_none() {
            let normalized_json = definition_json(&definition)?;
            Some(sha256_hex(normalized_json.as_bytes()))
        } else {
            None
        };
        Ok(Some(CollaborationDefinitionRecord {
            definition,
            source_format: source.map(|source| source.source_format.clone()),
            yaml_text: source.and_then(|source| source.yaml_text.clone()),
            content_hash: source
                .map(|source| source.content_hash.clone())
                .or(computed_hash),
        }))
    }

    async fn save_run_snapshot(
        &self,
        run: &StateMachineRun,
        _group_version: i32,
        definition: &CollaborationDefinition,
        resolved_participant_bindings: Option<&BTreeMap<String, ResolvedParticipantBinding>>,
    ) -> ServiceResult<()> {
        let mut inner = self.inner.write().await;
        inner
            .run_snapshots
            .entry(run.run_id.clone())
            .or_insert_with(|| definition.clone());
        if let Some(resolved) = resolved_participant_bindings {
            inner
                .run_resolved_participant_bindings
                .entry(run.run_id.clone())
                .or_insert_with(|| resolved.clone());
        }
        Ok(())
    }

    async fn get_run_snapshot(
        &self,
        run_id: &str,
    ) -> ServiceResult<Option<CollaborationDefinition>> {
        let inner = self.inner.read().await;
        Ok(inner.run_snapshots.get(run_id).cloned())
    }
}

#[async_trait]
impl GroupRuntimeBindingRepoPort for MemoryCollaborationStore {
    async fn upsert(&self, binding: GroupRuntimeBinding) -> ServiceResult<()> {
        let mut inner = self.inner.write().await;
        inner.bindings.insert(binding.group_id.clone(), binding);
        Ok(())
    }

    async fn get(&self, group_id: &str) -> ServiceResult<Option<GroupRuntimeBinding>> {
        let inner = self.inner.read().await;
        Ok(inner.bindings.get(group_id).cloned())
    }

    async fn delete(&self, group_id: &str) -> ServiceResult<bool> {
        let mut inner = self.inner.write().await;
        Ok(inner.bindings.remove(group_id).is_some())
    }

    async fn bind_default_definition(
        &self,
        group_id: &str,
        group_version: i32,
        definition: Option<CollaborationDefinitionRef>,
        participant_bindings: Option<BTreeMap<String, RuntimeParticipantBinding>>,
        auto_start_on_service_invocation: bool,
    ) -> ServiceResult<()> {
        let mut inner = self.inner.write().await;
        inner.bindings.insert(
            group_id.to_string(),
            GroupRuntimeBinding {
                group_id: group_id.to_string(),
                group_version,
                default_definition: definition,
                participant_bindings: participant_bindings.unwrap_or_default(),
                auto_start_on_service_invocation,
            },
        );
        Ok(())
    }

    async fn bind_default_definition_if_current(
        &self,
        group_id: &str,
        group_version: i32,
        expected_definition: Option<CollaborationDefinitionRef>,
        definition: Option<CollaborationDefinitionRef>,
        participant_bindings: Option<BTreeMap<String, RuntimeParticipantBinding>>,
        auto_start_on_service_invocation: bool,
    ) -> ServiceResult<bool> {
        let mut inner = self.inner.write().await;
        let current_definition = inner
            .bindings
            .get(group_id)
            .and_then(|binding| binding.default_definition.clone());
        if current_definition != expected_definition {
            return Ok(false);
        }
        inner.bindings.insert(
            group_id.to_string(),
            GroupRuntimeBinding {
                group_id: group_id.to_string(),
                group_version,
                default_definition: definition,
                participant_bindings: participant_bindings.unwrap_or_default(),
                auto_start_on_service_invocation,
            },
        );
        Ok(true)
    }
}

#[async_trait]
impl StateMachineRunRepoPort for MemoryCollaborationStore {
    async fn create_run(
        &self,
        run: StateMachineRun,
        nodes: Vec<StateMachineNodeRun>,
    ) -> ServiceResult<()> {
        let mut inner = self.inner.write().await;
        let run_id = run.run_id.clone();
        inner.runs.insert(run_id.clone(), run);
        for node in nodes {
            inner
                .nodes
                .insert((run_id.clone(), node.node_id.clone()), node);
        }
        Ok(())
    }

    async fn create_run_if_session_idle(
        &self,
        run: StateMachineRun,
        nodes: Vec<StateMachineNodeRun>,
    ) -> ServiceResult<bool> {
        let mut inner = self.inner.write().await;
        if inner.runs.values().any(|existing| {
            existing.session_id == run.session_id
                && matches!(
                    existing.status,
                    StateMachineRunStatus::Pending | StateMachineRunStatus::Running
                )
        }) {
            return Ok(false);
        }
        let run_id = run.run_id.clone();
        inner.runs.insert(run_id.clone(), run);
        for node in nodes {
            inner
                .nodes
                .insert((run_id.clone(), node.node_id.clone()), node);
        }
        Ok(true)
    }

    async fn get_run(&self, run_id: &str) -> ServiceResult<Option<StateMachineRun>> {
        let inner = self.inner.read().await;
        Ok(inner.runs.get(run_id).cloned())
    }

    async fn get_run_by_session_id(
        &self,
        session_id: &str,
    ) -> ServiceResult<Option<StateMachineRun>> {
        let inner = self.inner.read().await;
        Ok(inner
            .runs
            .values()
            .filter(|run| run.session_id == session_id)
            .max_by_key(|run| run.created_at)
            .cloned())
    }

    async fn list_runs_by_session_id(
        &self,
        session_id: &str,
    ) -> ServiceResult<Vec<StateMachineRun>> {
        let inner = self.inner.read().await;
        let mut runs = inner
            .runs
            .values()
            .filter(|run| run.session_id == session_id)
            .cloned()
            .collect::<Vec<_>>();
        runs.sort_by(|left, right| {
            right
                .created_at
                .cmp(&left.created_at)
                .then_with(|| right.run_id.cmp(&left.run_id))
        });
        Ok(runs)
    }

    async fn list_node_runs(&self, run_id: &str) -> ServiceResult<Vec<StateMachineNodeRun>> {
        let inner = self.inner.read().await;
        let mut nodes = inner
            .nodes
            .iter()
            .filter_map(|((node_run_id, _), node)| {
                if node_run_id == run_id {
                    Some(node.clone())
                } else {
                    None
                }
            })
            .collect::<Vec<_>>();
        nodes.sort_by(|left, right| left.node_id.cmp(&right.node_id));
        Ok(nodes)
    }

    async fn get_node_run(
        &self,
        run_id: &str,
        node_id: &str,
    ) -> ServiceResult<Option<StateMachineNodeRun>> {
        let inner = self.inner.read().await;
        Ok(inner
            .nodes
            .get(&(run_id.to_string(), node_id.to_string()))
            .cloned())
    }

    async fn mark_node_running(
        &self,
        run_id: &str,
        node_id: &str,
        attempt: i32,
        delivery_request_id: String,
        started_at: u64,
    ) -> ServiceResult<()> {
        let mut inner = self.inner.write().await;
        let node = node_mut(&mut inner, run_id, node_id)?;
        node.status = StateMachineNodeStatus::Running;
        node.attempt = attempt;
        node.delivery_request_id = Some(delivery_request_id);
        node.outcome = None;
        node.responded_by = None;
        node.started_at = Some(started_at);
        node.artifact_text = None;
        node.error = None;
        node.completed_at = None;
        node.timeout_deadline_ms = node
            .node_timeout_ms
            .map(|timeout_ms| started_at.saturating_add(timeout_ms));
        Ok(())
    }

    async fn mark_node_running_if_run_active(
        &self,
        run_id: &str,
        node_id: &str,
        attempt: i32,
        delivery_request_id: String,
        started_at: u64,
    ) -> ServiceResult<bool> {
        let mut inner = self.inner.write().await;
        if !run_is_running(&inner, run_id)? {
            return Ok(false);
        }
        let node = node_mut(&mut inner, run_id, node_id)?;
        if !matches!(
            node.status,
            StateMachineNodeStatus::Pending
                | StateMachineNodeStatus::Ready
                | StateMachineNodeStatus::RetryScheduled
        ) || node.attempt != attempt
        {
            return Ok(false);
        }
        node.status = StateMachineNodeStatus::Running;
        node.delivery_request_id = Some(delivery_request_id);
        node.outcome = None;
        node.responded_by = None;
        node.started_at = Some(started_at);
        node.artifact_text = None;
        node.error = None;
        node.completed_at = None;
        node.timeout_deadline_ms = node
            .node_timeout_ms
            .map(|timeout_ms| started_at.saturating_add(timeout_ms));
        Ok(true)
    }

    async fn complete_node_attempt(
        &self,
        run_id: &str,
        node_id: &str,
        attempt: i32,
        outcome: String,
        artifact_text: String,
        responded_by: Option<String>,
        completed_at: u64,
    ) -> ServiceResult<bool> {
        let mut inner = self.inner.write().await;
        if !run_is_running(&inner, run_id)? {
            return Ok(false);
        }
        let node = node_mut(&mut inner, run_id, node_id)?;
        if node.status != StateMachineNodeStatus::Running || node.attempt != attempt {
            return Ok(false);
        }
        node.status = StateMachineNodeStatus::Completed;
        node.outcome = Some(outcome);
        node.responded_by = responded_by;
        node.artifact_text = Some(artifact_text);
        node.error = None;
        node.completed_at = Some(completed_at);
        node.timeout_deadline_ms = None;
        Ok(true)
    }

    async fn mark_human_node_running_if_run_active(
        &self,
        command: MarkHumanNodeRunningCommand,
    ) -> ServiceResult<bool> {
        let mut inner = self.inner.write().await;
        if !run_is_running(&inner, &command.run_id)? {
            return Ok(false);
        }
        let node = node_mut(&mut inner, &command.run_id, &command.node_id)?;
        if !matches!(
            node.status,
            StateMachineNodeStatus::Pending | StateMachineNodeStatus::Ready
        ) || node.attempt != command.attempt
        {
            return Ok(false);
        }
        node.status = StateMachineNodeStatus::Running;
        node.assignee_bot_id = None;
        node.delivery_request_id = None;
        node.bot_delivery_run_id = None;
        node.outcome = None;
        node.responded_by = None;
        node.artifact_text = None;
        node.error = None;
        node.started_at = Some(command.started_at_ms);
        node.completed_at = None;
        node.timeout_deadline_ms = Some(command.timeout_deadline_ms);
        Ok(true)
    }

    async fn record_node_artifact_if_running(
        &self,
        run_id: &str,
        node_id: &str,
        attempt: i32,
        artifact_text: String,
    ) -> ServiceResult<bool> {
        let mut inner = self.inner.write().await;
        if !run_is_running(&inner, run_id)? {
            return Ok(false);
        }
        let node = node_mut(&mut inner, run_id, node_id)?;
        if node.status != StateMachineNodeStatus::Running || node.attempt != attempt {
            return Ok(false);
        }
        node.artifact_text = Some(artifact_text);
        node.error = None;
        Ok(true)
    }

    async fn record_human_response_if_running(
        &self,
        run_id: &str,
        node_id: &str,
        attempt: i32,
        artifact_text: String,
        responded_by: String,
    ) -> ServiceResult<bool> {
        let mut inner = self.inner.write().await;
        if !run_is_running(&inner, run_id)? {
            return Ok(false);
        }
        let node = node_mut(&mut inner, run_id, node_id)?;
        if node.status != StateMachineNodeStatus::Running
            || node.attempt != attempt
            || node.artifact_text.is_some()
        {
            return Ok(false);
        }
        node.artifact_text = Some(artifact_text);
        node.responded_by = Some(responded_by);
        node.error = None;
        Ok(true)
    }

    async fn fail_node_attempt(
        &self,
        run_id: &str,
        node_id: &str,
        attempt: i32,
        error: String,
        completed_at: u64,
    ) -> ServiceResult<bool> {
        let mut inner = self.inner.write().await;
        if !run_is_running(&inner, run_id)? {
            return Ok(false);
        }
        let node = node_mut(&mut inner, run_id, node_id)?;
        if node.status != StateMachineNodeStatus::Running || node.attempt != attempt {
            return Ok(false);
        }
        node.status = StateMachineNodeStatus::Failed;
        node.error = Some(error);
        node.completed_at = Some(completed_at);
        Ok(true)
    }

    async fn schedule_node_retry(
        &self,
        run_id: &str,
        node_id: &str,
        failed_attempt: i32,
        next_attempt: i32,
    ) -> ServiceResult<bool> {
        let mut inner = self.inner.write().await;
        if !run_is_running(&inner, run_id)? {
            return Ok(false);
        }
        let node = node_mut(&mut inner, run_id, node_id)?;
        if node.status != StateMachineNodeStatus::Failed || node.attempt != failed_attempt {
            return Ok(false);
        }
        node.status = StateMachineNodeStatus::RetryScheduled;
        node.attempt = next_attempt;
        node.delivery_request_id = None;
        node.bot_delivery_run_id = None;
        node.outcome = None;
        node.responded_by = None;
        node.artifact_text = None;
        node.error = None;
        node.started_at = None;
        node.completed_at = None;
        node.timeout_deadline_ms = None;
        Ok(true)
    }

    async fn skip_node(&self, run_id: &str, node_id: &str, skipped_at: u64) -> ServiceResult<bool> {
        let mut inner = self.inner.write().await;
        if !run_is_running(&inner, run_id)? {
            return Ok(false);
        }
        let node = node_mut(&mut inner, run_id, node_id)?;
        if node.status != StateMachineNodeStatus::Pending {
            return Ok(false);
        }
        node.status = StateMachineNodeStatus::Skipped;
        node.completed_at = Some(skipped_at);
        Ok(true)
    }

    async fn update_run_status(
        &self,
        run_id: &str,
        status: StateMachineRunStatus,
        output: Option<String>,
        error: Option<String>,
        updated_at: u64,
        completed_at: Option<u64>,
    ) -> ServiceResult<bool> {
        let mut inner = self.inner.write().await;
        let run = inner.runs.get_mut(run_id).ok_or_else(|| {
            ServiceError::InternalError(format!("state machine run not found: {run_id}"))
        })?;
        if is_terminal(run.status) {
            return Ok(false);
        }
        run.status = status;
        run.output = output;
        run.error = error;
        run.updated_at = updated_at;
        run.completed_at = completed_at;
        Ok(true)
    }

    async fn upsert_delivery_correlation(
        &self,
        correlation: StateMachineDeliveryCorrelation,
    ) -> ServiceResult<()> {
        let mut inner = self.inner.write().await;
        inner
            .correlations
            .insert(correlation.delivery_request_id.clone(), correlation);
        Ok(())
    }

    async fn register_delivery_alias(
        &self,
        delivery_request_id: &str,
        bot_delivery_run_id: String,
    ) -> ServiceResult<()> {
        let mut inner = self.inner.write().await;
        let mut correlation = inner
            .correlations
            .get(delivery_request_id)
            .cloned()
            .ok_or_else(|| {
                ServiceError::InternalError(format!(
                    "delivery correlation not found: {delivery_request_id}"
                ))
            })?;
        correlation.bot_delivery_run_id = Some(bot_delivery_run_id.clone());
        inner
            .correlations
            .insert(delivery_request_id.to_string(), correlation.clone());
        inner
            .correlation_aliases
            .insert(bot_delivery_run_id, delivery_request_id.to_string());
        Ok(())
    }

    async fn lookup_delivery_correlation(
        &self,
        run_id: &str,
    ) -> ServiceResult<Option<StateMachineDeliveryCorrelation>> {
        let inner = self.inner.read().await;
        if let Some(correlation) = inner.correlations.get(run_id).cloned() {
            return Ok(Some(correlation));
        }
        let Some(primary_key) = inner.correlation_aliases.get(run_id) else {
            return Ok(None);
        };
        Ok(inner.correlations.get(primary_key).cloned())
    }

    async fn list_expired_running_node_runs(
        &self,
        now_ms: u64,
        timeout_grace_ms: u64,
        limit: usize,
    ) -> ServiceResult<Vec<StateMachineNodeRun>> {
        let inner = self.inner.read().await;
        let mut nodes = inner
            .nodes
            .values()
            .filter(|node| {
                node.status == StateMachineNodeStatus::Running
                    && node
                        .timeout_deadline_ms
                        .is_some_and(|deadline| deadline.saturating_add(timeout_grace_ms) <= now_ms)
                    && inner
                        .runs
                        .get(&node.run_id)
                        .is_some_and(|run| run.status == StateMachineRunStatus::Running)
            })
            .cloned()
            .collect::<Vec<_>>();
        nodes.sort_by(|left, right| {
            left.timeout_deadline_ms
                .cmp(&right.timeout_deadline_ms)
                .then_with(|| left.run_id.cmp(&right.run_id))
                .then_with(|| left.node_id.cmp(&right.node_id))
        });
        nodes.truncate(limit);
        Ok(nodes)
    }
}

#[async_trait]
impl CollaborationEventRepoPort for MemoryCollaborationStore {
    async fn append_event(
        &self,
        state_machine_run_id: &str,
        node_id: Option<&str>,
        attempt: Option<i32>,
        event_type: &str,
        payload: serde_json::Value,
        created_at: u64,
    ) -> ServiceResult<()> {
        let mut inner = self.inner.write().await;
        inner.events.push(CollaborationEventRecord {
            state_machine_run_id: state_machine_run_id.to_string(),
            node_id: node_id.map(str::to_string),
            attempt,
            event_type: event_type.to_string(),
            payload,
            created_at,
        });
        Ok(())
    }

    async fn list_events_by_run_and_type(
        &self,
        state_machine_run_id: &str,
        event_type: &str,
    ) -> ServiceResult<Vec<CollaborationEventRecord>> {
        let inner = self.inner.read().await;
        Ok(inner
            .events
            .iter()
            .filter(|event| {
                event.state_machine_run_id == state_machine_run_id && event.event_type == event_type
            })
            .cloned()
            .collect())
    }

    async fn list_events_by_run_node_and_type(
        &self,
        state_machine_run_id: &str,
        node_id: &str,
        event_type: &str,
    ) -> ServiceResult<Vec<CollaborationEventRecord>> {
        let inner = self.inner.read().await;
        Ok(inner
            .events
            .iter()
            .filter(|event| {
                event.state_machine_run_id == state_machine_run_id
                    && event.node_id.as_deref() == Some(node_id)
                    && event.event_type == event_type
            })
            .cloned()
            .collect())
    }
}

#[derive(Clone)]
pub struct MySqlCollaborationStore {
    db: Arc<dyn DbPlugin>,
    env: String,
    flavor: DbSqlFlavor,
}

impl MySqlCollaborationStore {
    pub fn new(db: Arc<dyn DbPlugin>, env: String) -> Self {
        Self {
            db,
            env,
            flavor: DbSqlFlavor::Mysql,
        }
    }

    pub fn sqlite(db: Arc<dyn DbPlugin>, env: String) -> Self {
        Self {
            db,
            env,
            flavor: DbSqlFlavor::Sqlite,
        }
    }

    async fn upsert_definition_internal(
        &self,
        definition: CollaborationDefinition,
        source_yaml: Option<String>,
    ) -> ServiceResult<()> {
        let normalized_json = definition_json(&definition)?;
        let metadata_json = serde_json::to_string(&definition.metadata).map_err(|error| {
            ServiceError::InternalError(format!("definition metadata serialize: {error}"))
        })?;
        let content_hash = sha256_hex(normalized_json.as_bytes());
        let definition_ref = CollaborationDefinitionRef {
            id: definition.id.clone(),
            version: definition.version,
        };
        if let Some((existing_hash, _, existing_yaml)) = self
            .find_definition_record_metadata(&definition_ref)
            .await?
        {
            if existing_hash != content_hash {
                return Err(definition_conflict_error(
                    &definition.id,
                    definition.version,
                    &existing_hash,
                    &content_hash,
                ));
            }
            if let Some(source_yaml) = source_yaml.as_deref() {
                if let Some(existing_yaml) =
                    existing_yaml.as_deref().filter(|value| !value.is_empty())
                {
                    if existing_yaml != source_yaml {
                        return Err(ServiceError::Conflict(format!(
                            "CollaborationDefinition '{}@{}' already exists with different source YAML",
                            definition.id, definition.version
                        )));
                    }
                } else {
                    let sql = format!(
                        "UPDATE bcs_collaboration_definitions \
                         SET source_format = 'yaml', yaml_text = ?, {} \
                         WHERE env = ? AND definition_id = ? AND version = ? \
                           AND record_status = 'active'",
                        self.flavor.set_modified_now()
                    );
                    self.db
                        .execute(DbStatement::with_params(
                            sql,
                            vec![
                                DbValue::from(source_yaml),
                                DbValue::from(self.env.as_str()),
                                DbValue::from(definition.id.as_str()),
                                DbValue::from(definition.version),
                            ],
                        ))
                        .await
                        .map_err(|error| {
                            ServiceError::InternalError(format!(
                                "collaboration definition source backfill: {error}"
                            ))
                        })?;
                }
            }
            return Ok(());
        }
        let blob_id = format!(
            "collab-def:{}:{}:{}",
            definition.id,
            definition.version,
            &content_hash[..16]
        );
        let description = definition.metadata.description.as_deref();
        let source_format = if source_yaml.is_some() {
            "yaml"
        } else {
            "json"
        };
        let blob_upsert = self.flavor.on_conflict_update(
            &["env", "blob_id"],
            &[
                "content_hash",
                "content_encoding",
                "content_size",
                "content",
                "external_uri",
            ],
            &[("gmt_modified", self.flavor.now())],
        );
        let blob_sql = format!(
            "INSERT INTO bcs_collaboration_definition_blobs \
             (env, blob_id, content_hash, content_encoding, content_size, \
              content, external_uri, created_by) \
             VALUES (?, ?, ?, 'identity', ?, ?, NULL, NULL) \
             {blob_upsert}"
        );
        let insert_ignore = self.flavor.insert_or_ignore();
        let sql = format!(
            "{insert_ignore} INTO bcs_collaboration_definitions \
             (env, definition_id, version, name, description, source_format, \
              content_hash, blob_id, yaml_text, normalized_json, metadata_json, \
              record_status, created_by) \
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', NULL)"
        );
        self.db
            .transaction(vec![
                DbTransactionStep::Execute(DbStatement::with_params(
                    blob_sql,
                    vec![
                        DbValue::from(self.env.as_str()),
                        DbValue::from(blob_id.as_str()),
                        DbValue::from(content_hash.as_str()),
                        DbValue::from(normalized_json.len() as u64),
                        DbValue::from(normalized_json.as_bytes().to_vec()),
                    ],
                )),
                DbTransactionStep::Execute(DbStatement::with_params(
                    sql,
                    vec![
                        DbValue::from(self.env.as_str()),
                        DbValue::from(definition.id.as_str()),
                        DbValue::from(definition.version),
                        DbValue::from(definition.name.as_str()),
                        DbValue::from(description),
                        DbValue::from(source_format),
                        DbValue::from(content_hash.as_str()),
                        DbValue::from(blob_id.as_str()),
                        DbValue::from(source_yaml.as_deref()),
                        DbValue::from(normalized_json.as_str()),
                        DbValue::from(metadata_json.as_str()),
                    ],
                )),
            ])
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("collaboration definition upsert: {error}"))
            })?;
        let Some((stored_hash, _, _)) = self
            .find_definition_record_metadata(&definition_ref)
            .await?
        else {
            return Err(ServiceError::InternalError(format!(
                "collaboration definition insert did not create row: {}@{}",
                definition.id, definition.version
            )));
        };
        if stored_hash != content_hash {
            return Err(definition_conflict_error(
                &definition.id,
                definition.version,
                &stored_hash,
                &content_hash,
            ));
        }
        Ok(())
    }

    async fn find_definition_metadata(
        &self,
        definition: &CollaborationDefinitionRef,
    ) -> ServiceResult<Option<(String, Option<String>)>> {
        Ok(self
            .find_definition_record_metadata(definition)
            .await?
            .map(|(content_hash, blob_id, _)| (content_hash, blob_id)))
    }

    async fn find_definition_record_metadata(
        &self,
        definition: &CollaborationDefinitionRef,
    ) -> ServiceResult<Option<(String, Option<String>, Option<String>)>> {
        let sql = "SELECT content_hash, blob_id, yaml_text \
                   FROM bcs_collaboration_definitions \
                   WHERE env = ? AND definition_id = ? AND version = ? \
                     AND record_status = 'active' \
                   LIMIT 1";
        let rows = self
            .db
            .query(DbStatement::with_params(
                sql,
                vec![
                    DbValue::from(self.env.as_str()),
                    DbValue::from(definition.id.as_str()),
                    DbValue::from(definition.version),
                ],
            ))
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("collaboration definition lookup: {error}"))
            })?;
        let Some(row) = rows.into_iter().next() else {
            return Ok(None);
        };
        let content_hash: String = db_get_column(&row, "content_hash")
            .map_err(|error| ServiceError::InternalError(format!("content_hash: {error}")))?;
        let blob_id: Option<String> = db_get_column_opt(&row, "blob_id")
            .map_err(|error| ServiceError::InternalError(format!("blob_id: {error}")))?;
        let yaml_text: Option<String> = db_get_column_opt(&row, "yaml_text")
            .map_err(|error| ServiceError::InternalError(format!("yaml_text: {error}")))?;
        Ok(Some((content_hash, blob_id, yaml_text)))
    }

    async fn definition_metadata(
        &self,
        definition: &CollaborationDefinitionRef,
    ) -> ServiceResult<(String, Option<String>)> {
        self.find_definition_metadata(definition)
            .await?
            .ok_or_else(|| {
                ServiceError::InternalError(format!(
                    "collaboration definition not found for binding: {}@{}",
                    definition.id, definition.version
                ))
            })
    }
}

#[async_trait]
impl StateMachineDefinitionRepoPort for MySqlCollaborationStore {
    async fn upsert(&self, definition: CollaborationDefinition) -> ServiceResult<()> {
        self.upsert_definition_internal(definition, None).await
    }

    async fn upsert_with_source_yaml(
        &self,
        definition: CollaborationDefinition,
        source_yaml: String,
    ) -> ServiceResult<()> {
        self.upsert_definition_internal(definition, Some(source_yaml))
            .await
    }

    async fn get(&self, id: &str, version: i32) -> ServiceResult<Option<CollaborationDefinition>> {
        Ok(self
            .get_record(id, version)
            .await?
            .map(|record| record.definition))
    }

    async fn get_record(
        &self,
        id: &str,
        version: i32,
    ) -> ServiceResult<Option<CollaborationDefinitionRecord>> {
        let sql = "SELECT normalized_json, yaml_text, source_format, content_hash \
                   FROM bcs_collaboration_definitions \
                   WHERE env = ? AND definition_id = ? AND version = ? \
                     AND record_status = 'active' \
                   LIMIT 1";
        let rows = self
            .db
            .query(DbStatement::with_params(
                sql,
                vec![
                    DbValue::from(self.env.as_str()),
                    DbValue::from(id),
                    DbValue::from(version),
                ],
            ))
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("collaboration definition get: {error}"))
            })?;
        let Some(row) = rows.into_iter().next() else {
            return Ok(None);
        };
        let source_format: Option<String> = db_get_column_opt(&row, "source_format")
            .map_err(|error| ServiceError::InternalError(format!("source_format: {error}")))?;
        let content_hash: Option<String> = db_get_column_opt(&row, "content_hash")
            .map_err(|error| ServiceError::InternalError(format!("content_hash: {error}")))?;
        let yaml_text: Option<String> = db_get_column_opt(&row, "yaml_text")
            .map_err(|error| ServiceError::InternalError(format!("yaml_text: {error}")))?;
        let normalized_json: Option<String> = db_get_column_opt(&row, "normalized_json")
            .map_err(|error| ServiceError::InternalError(format!("normalized_json: {error}")))?;
        let definition = if let Some(raw) = normalized_json.filter(|raw| !raw.is_empty()) {
            serde_json::from_str(&raw).map_err(|error| {
                ServiceError::InternalError(format!("definition json parse: {error}"))
            })?
        } else if let Some(raw) = yaml_text.as_deref().filter(|raw| !raw.is_empty()) {
            serde_yaml::from_str(raw).map_err(|error| {
                ServiceError::InternalError(format!("definition yaml parse: {error}"))
            })?
        } else {
            return Ok(None);
        };
        Ok(Some(CollaborationDefinitionRecord {
            definition,
            source_format,
            yaml_text,
            content_hash,
        }))
    }

    async fn save_run_snapshot(
        &self,
        run: &StateMachineRun,
        group_version: i32,
        definition: &CollaborationDefinition,
        resolved_participant_bindings: Option<&BTreeMap<String, ResolvedParticipantBinding>>,
    ) -> ServiceResult<()> {
        let snapshot_json = definition_json(definition)?;
        let content_hash = sha256_hex(snapshot_json.as_bytes());
        let resolved_participant_bindings_json = match resolved_participant_bindings {
            Some(bindings) if !bindings.is_empty() => {
                Some(serde_json::to_string(bindings).map_err(|error| {
                    ServiceError::InternalError(format!(
                        "resolved participant bindings json: {error}"
                    ))
                })?)
            }
            _ => None,
        };
        let snapshot_upsert = self.flavor.on_conflict_nothing(&["env", "run_id"]);
        let sql = format!(
            "INSERT INTO bcs_state_machine_definition_snapshots \
             (env, run_id, group_id, session_id, group_version, \
              definition_id, definition_version, definition_content_hash, \
              snapshot_blob_id, snapshot_json, resolved_participant_bindings_json, source_format) \
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, 'json') \
             {snapshot_upsert}"
        );
        self.db
            .execute(DbStatement::with_params(
                sql,
                vec![
                    DbValue::from(self.env.as_str()),
                    DbValue::from(run.run_id.as_str()),
                    DbValue::from(run.group_id.as_str()),
                    DbValue::from(run.session_id.as_str()),
                    DbValue::from(group_version),
                    DbValue::from(definition.id.as_str()),
                    DbValue::from(definition.version),
                    DbValue::from(content_hash.as_str()),
                    DbValue::from(snapshot_json.as_str()),
                    DbValue::from(resolved_participant_bindings_json.as_deref()),
                ],
            ))
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("definition snapshot upsert: {error}"))
            })?;
        Ok(())
    }

    async fn get_run_snapshot(
        &self,
        run_id: &str,
    ) -> ServiceResult<Option<CollaborationDefinition>> {
        let sql = "SELECT snapshot_json \
                   FROM bcs_state_machine_definition_snapshots \
                   WHERE env = ? AND run_id = ? \
                   LIMIT 1";
        let rows = self
            .db
            .query(DbStatement::with_params(
                sql,
                vec![DbValue::from(self.env.as_str()), DbValue::from(run_id)],
            ))
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("definition snapshot get: {error}"))
            })?;
        let Some(row) = rows.into_iter().next() else {
            return Ok(None);
        };
        let snapshot_json: Option<String> = db_get_column_opt(&row, "snapshot_json")
            .map_err(|error| ServiceError::InternalError(format!("snapshot_json: {error}")))?;
        match snapshot_json {
            Some(raw) if !raw.is_empty() => serde_json::from_str(&raw).map(Some).map_err(|error| {
                ServiceError::InternalError(format!("definition snapshot parse: {error}"))
            }),
            _ => Ok(None),
        }
    }
}

#[async_trait]
impl GroupRuntimeBindingRepoPort for MySqlCollaborationStore {
    async fn upsert(&self, binding: GroupRuntimeBinding) -> ServiceResult<()> {
        self.bind_default_definition(
            &binding.group_id,
            binding.group_version,
            binding.default_definition,
            Some(binding.participant_bindings),
            binding.auto_start_on_service_invocation,
        )
        .await
    }

    async fn get(&self, group_id: &str) -> ServiceResult<Option<GroupRuntimeBinding>> {
        let sql = "SELECT group_version, default_definition_id, default_definition_version, \
                          auto_start_on_service_invocation, participant_bindings_json \
                   FROM bcs_group_runtime_bindings \
                   WHERE env = ? AND group_id = ? AND record_status = 'active' \
                     AND next_group_version = ? \
                   ORDER BY group_version DESC \
                   LIMIT 1";
        let rows = self
            .db
            .query(DbStatement::with_params(
                sql,
                vec![
                    DbValue::from(self.env.as_str()),
                    DbValue::from(group_id),
                    DbValue::from(CURRENT_GROUP_VERSION_SENTINEL),
                ],
            ))
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("group runtime binding get: {error}"))
            })?;
        let Some(row) = rows.into_iter().next() else {
            return Ok(None);
        };
        let group_version: i32 = db_get_column(&row, "group_version")
            .map_err(|error| ServiceError::InternalError(format!("group_version: {error}")))?;
        let definition_id: Option<String> = db_get_column_opt(&row, "default_definition_id")
            .map_err(|error| {
                ServiceError::InternalError(format!("default_definition_id: {error}"))
            })?;
        let definition_version: Option<i32> = db_get_column_opt(&row, "default_definition_version")
            .map_err(|error| {
                ServiceError::InternalError(format!("default_definition_version: {error}"))
            })?;
        let auto_start_on_service_invocation =
            db_get_column_opt::<i32>(&row, "auto_start_on_service_invocation")
                .map_err(|error| {
                    ServiceError::InternalError(format!(
                        "auto_start_on_service_invocation: {error}"
                    ))
                })?
                .map_or(false, |value| value != 0);
        let participant_bindings_json: Option<String> =
            db_get_column_opt(&row, "participant_bindings_json").map_err(|error| {
                ServiceError::InternalError(format!("participant_bindings_json: {error}"))
            })?;
        let participant_bindings = match participant_bindings_json {
            Some(raw) if !raw.is_empty() => serde_json::from_str(&raw).map_err(|error| {
                ServiceError::InternalError(format!("participant bindings parse: {error}"))
            })?,
            _ => BTreeMap::new(),
        };
        let default_definition = match (definition_id, definition_version) {
            (Some(id), Some(version)) => Some(CollaborationDefinitionRef { id, version }),
            _ => None,
        };
        Ok(Some(GroupRuntimeBinding {
            group_id: group_id.to_string(),
            group_version,
            default_definition,
            participant_bindings,
            auto_start_on_service_invocation,
        }))
    }

    async fn delete(&self, group_id: &str) -> ServiceResult<bool> {
        let result = self
            .db
            .execute(DbStatement::with_params(
                "DELETE FROM bcs_group_runtime_bindings WHERE env = ? AND group_id = ?",
                vec![DbValue::from(self.env.as_str()), DbValue::from(group_id)],
            ))
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("group runtime binding delete: {error}"))
            })?;
        Ok(result.affected_rows > 0)
    }

    async fn bind_default_definition(
        &self,
        group_id: &str,
        group_version: i32,
        definition: Option<CollaborationDefinitionRef>,
        participant_bindings: Option<BTreeMap<String, RuntimeParticipantBinding>>,
        auto_start_on_service_invocation: bool,
    ) -> ServiceResult<()> {
        let (definition_id, definition_version, content_hash, blob_id) = match definition.as_ref() {
            Some(definition) => {
                let (content_hash, blob_id) = self.definition_metadata(definition).await?;
                (
                    Some(definition.id.clone()),
                    Some(definition.version),
                    Some(content_hash),
                    blob_id,
                )
            }
            None => (None, None, None, None),
        };
        let participant_bindings_json = match participant_bindings {
            Some(bindings) if !bindings.is_empty() => {
                Some(serde_json::to_string(&bindings).map_err(|error| {
                    ServiceError::InternalError(format!("participant bindings json: {error}"))
                })?)
            }
            _ => None,
        };
        let close_previous = format!(
            "UPDATE bcs_group_runtime_bindings \
             SET next_group_version = ?, {} \
             WHERE env = ? AND group_id = ? AND record_status = 'active' \
               AND next_group_version = ? AND group_version < ?",
            self.flavor.set_modified_now()
        );
        let binding_upsert = self.flavor.on_conflict_update(
            &["env", "group_id", "group_version"],
            &[
                "next_group_version",
                "default_definition_id",
                "default_definition_version",
                "definition_content_hash",
                "definition_blob_id",
                "auto_start_on_service_invocation",
                "participant_bindings_json",
                "updated_by",
            ],
            &[
                ("record_status", "'active'"),
                ("gmt_modified", self.flavor.now()),
            ],
        );
        let upsert_current = format!(
            "INSERT INTO bcs_group_runtime_bindings \
             (env, group_id, group_version, next_group_version, \
              default_definition_id, default_definition_version, \
              definition_content_hash, definition_blob_id, \
              auto_start_on_service_invocation, participant_bindings_json, \
              record_status, updated_by) \
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', NULL) \
             {binding_upsert}"
        );
        self.db
            .transaction(vec![
                DbTransactionStep::Execute(DbStatement::with_params(
                    close_previous,
                    vec![
                        DbValue::from(group_version),
                        DbValue::from(self.env.as_str()),
                        DbValue::from(group_id),
                        DbValue::from(CURRENT_GROUP_VERSION_SENTINEL),
                        DbValue::from(group_version),
                    ],
                )),
                DbTransactionStep::Execute(DbStatement::with_params(
                    upsert_current,
                    vec![
                        DbValue::from(self.env.as_str()),
                        DbValue::from(group_id),
                        DbValue::from(group_version),
                        DbValue::from(CURRENT_GROUP_VERSION_SENTINEL),
                        DbValue::from(definition_id.as_deref()),
                        definition_version
                            .map(DbValue::from)
                            .unwrap_or(DbValue::Null),
                        DbValue::from(content_hash.as_deref()),
                        DbValue::from(blob_id.as_deref()),
                        DbValue::from(auto_start_on_service_invocation),
                        DbValue::from(participant_bindings_json.as_deref()),
                    ],
                )),
            ])
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("group runtime binding upsert: {error}"))
            })?;
        Ok(())
    }

    async fn bind_default_definition_if_current(
        &self,
        group_id: &str,
        _group_version: i32,
        expected_definition: Option<CollaborationDefinitionRef>,
        definition: Option<CollaborationDefinitionRef>,
        participant_bindings: Option<BTreeMap<String, RuntimeParticipantBinding>>,
        auto_start_on_service_invocation: bool,
    ) -> ServiceResult<bool> {
        let (definition_id, definition_version, content_hash, blob_id) = match definition.as_ref() {
            Some(definition) => {
                let (content_hash, blob_id) = self.definition_metadata(definition).await?;
                (
                    Some(definition.id.clone()),
                    Some(definition.version),
                    Some(content_hash),
                    blob_id,
                )
            }
            None => (None, None, None, None),
        };
        let participant_bindings_json = match participant_bindings {
            Some(bindings) if !bindings.is_empty() => {
                Some(serde_json::to_string(&bindings).map_err(|error| {
                    ServiceError::InternalError(format!("participant bindings json: {error}"))
                })?)
            }
            _ => None,
        };
        let expected_definition_id = expected_definition
            .as_ref()
            .map(|definition| definition.id.as_str());
        let expected_definition_version = expected_definition
            .as_ref()
            .map(|definition| definition.version);
        let sql = format!(
            "UPDATE bcs_group_runtime_bindings \
                   SET default_definition_id = ?, default_definition_version = ?, \
                       definition_content_hash = ?, definition_blob_id = ?, \
                       auto_start_on_service_invocation = ?, participant_bindings_json = ?, \
                       {} \
                   WHERE env = ? AND group_id = ? AND record_status = 'active' \
                     AND next_group_version = ? \
                     AND ( \
                       (? IS NULL AND default_definition_id IS NULL AND default_definition_version IS NULL) \
                       OR (default_definition_id = ? AND default_definition_version = ?) \
                     )",
            self.flavor.set_modified_now()
        );
        let result = self
            .db
            .execute(DbStatement::with_params(
                sql,
                vec![
                    DbValue::from(definition_id.as_deref()),
                    definition_version
                        .map(DbValue::from)
                        .unwrap_or(DbValue::Null),
                    DbValue::from(content_hash.as_deref()),
                    DbValue::from(blob_id.as_deref()),
                    DbValue::from(auto_start_on_service_invocation),
                    DbValue::from(participant_bindings_json.as_deref()),
                    DbValue::from(self.env.as_str()),
                    DbValue::from(group_id),
                    DbValue::from(CURRENT_GROUP_VERSION_SENTINEL),
                    DbValue::from(expected_definition_id),
                    DbValue::from(expected_definition_id),
                    expected_definition_version
                        .map(DbValue::from)
                        .unwrap_or(DbValue::Null),
                ],
            ))
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("group runtime binding CAS update: {error}"))
            })?;
        Ok(result.affected_rows > 0)
    }
}

#[async_trait]
impl StateMachineRunRepoPort for MySqlCollaborationStore {
    async fn create_run(
        &self,
        run: StateMachineRun,
        nodes: Vec<StateMachineNodeRun>,
    ) -> ServiceResult<()> {
        let run_sql = "INSERT INTO bcs_state_machine_runs \
                       (env, run_id, definition_id, definition_version, group_id, \
                        group_version, session_id, created_by, status, input_json, \
                        output_text, error_message, created_at_ms, updated_at_ms, \
                        completed_at_ms, record_status) \
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')";
        let mut steps = vec![DbTransactionStep::Execute(DbStatement::with_params(
            run_sql,
            run_insert_params(&self.env, &run),
        ))];
        if let Some((node_sql, node_params)) =
            build_node_runs_insert(&self.env, &run.run_id, &nodes)
        {
            steps.push(DbTransactionStep::Execute(DbStatement::with_params(
                node_sql,
                node_params,
            )));
        }
        self.db.transaction(steps).await.map_err(|error| {
            ServiceError::InternalError(format!("state machine run create: {error}"))
        })?;
        Ok(())
    }

    async fn create_run_if_session_idle(
        &self,
        run: StateMachineRun,
        nodes: Vec<StateMachineNodeRun>,
    ) -> ServiceResult<bool> {
        let lock_suffix = match self.flavor {
            DbSqlFlavor::Mysql => " FOR UPDATE",
            DbSqlFlavor::Sqlite => "",
        };
        let lock_sql = format!(
            "SELECT session_id FROM bcs_group_sessions \
             WHERE env = ? AND session_id = ?{lock_suffix}"
        );
        let run_sql = "INSERT INTO bcs_state_machine_runs \
                       (env, run_id, definition_id, definition_version, group_id, \
                        group_version, session_id, created_by, status, input_json, \
                        output_text, error_message, created_at_ms, updated_at_ms, \
                        completed_at_ms, record_status) \
                       SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active' \
                       FROM bcs_group_sessions session_lock \
                       WHERE session_lock.env = ? AND session_lock.session_id = ? \
                         AND NOT EXISTS ( \
                           SELECT 1 FROM bcs_state_machine_runs active_run \
                           WHERE active_run.env = ? \
                             AND active_run.session_id = ? \
                             AND active_run.status IN ('pending', 'running') \
                             AND active_run.record_status = 'active' \
                         ) \
                       LIMIT 1";
        let mut run_params = run_insert_params(&self.env, &run);
        run_params.extend([
            DbValue::from(self.env.as_str()),
            DbValue::from(run.session_id.as_str()),
            DbValue::from(self.env.as_str()),
            DbValue::from(run.session_id.as_str()),
        ]);
        let mut steps = vec![
            DbTransactionStep::Query(DbStatement::with_params(
                lock_sql,
                vec![
                    DbValue::from(self.env.as_str()),
                    DbValue::from(run.session_id.as_str()),
                ],
            )),
            DbTransactionStep::Execute(DbStatement::with_params(run_sql, run_params)),
        ];
        steps.extend(build_guarded_node_runs_inserts(
            &self.env,
            &run.run_id,
            &nodes,
        ));
        let results = self.db.transaction(steps).await.map_err(|error| {
            ServiceError::InternalError(format!("state machine session run create: {error}"))
        })?;
        let created = matches!(
            results.get(1),
            Some(DbTransactionStepResult::Executed(result)) if result.affected_rows > 0
        );
        Ok(created)
    }

    async fn get_run(&self, run_id: &str) -> ServiceResult<Option<StateMachineRun>> {
        let sql = format!(
            "SELECT {SM_RUN_SELECT_COLS} \
             FROM bcs_state_machine_runs \
             WHERE env = ? AND run_id = ? AND record_status = 'active' \
             LIMIT 1"
        );
        let rows = self
            .db
            .query(DbStatement::with_params(
                sql,
                vec![DbValue::from(self.env.as_str()), DbValue::from(run_id)],
            ))
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("state machine run get: {error}"))
            })?;
        rows.into_iter()
            .next()
            .map(row_to_state_machine_run)
            .transpose()
    }

    async fn get_run_by_session_id(
        &self,
        session_id: &str,
    ) -> ServiceResult<Option<StateMachineRun>> {
        let sql = format!(
            "SELECT {SM_RUN_SELECT_COLS} \
             FROM bcs_state_machine_runs \
             WHERE env = ? AND session_id = ? AND record_status = 'active' \
             ORDER BY created_at_ms DESC, id DESC \
             LIMIT 1"
        );
        let rows = self
            .db
            .query(DbStatement::with_params(
                sql,
                vec![DbValue::from(self.env.as_str()), DbValue::from(session_id)],
            ))
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("state machine run get by session: {error}"))
            })?;
        rows.into_iter()
            .next()
            .map(row_to_state_machine_run)
            .transpose()
    }

    async fn list_runs_by_session_id(
        &self,
        session_id: &str,
    ) -> ServiceResult<Vec<StateMachineRun>> {
        let sql = format!(
            "SELECT {SM_RUN_SELECT_COLS} \
             FROM bcs_state_machine_runs \
             WHERE env = ? AND session_id = ? AND record_status = 'active' \
             ORDER BY created_at_ms DESC, id DESC"
        );
        let rows = self
            .db
            .query(DbStatement::with_params(
                sql,
                vec![DbValue::from(self.env.as_str()), DbValue::from(session_id)],
            ))
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("state machine runs list by session: {error}"))
            })?;
        rows.into_iter().map(row_to_state_machine_run).collect()
    }

    async fn list_node_runs(&self, run_id: &str) -> ServiceResult<Vec<StateMachineNodeRun>> {
        let sql = format!(
            "SELECT {SM_NODE_SELECT_COLS} \
             FROM bcs_state_machine_node_runs \
             WHERE env = ? AND run_id = ? AND record_status = 'active' \
             ORDER BY node_id ASC"
        );
        let rows = self
            .db
            .query(DbStatement::with_params(
                sql,
                vec![DbValue::from(self.env.as_str()), DbValue::from(run_id)],
            ))
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("state machine node list: {error}"))
            })?;
        rows.into_iter()
            .map(row_to_state_machine_node_run)
            .collect()
    }

    async fn get_node_run(
        &self,
        run_id: &str,
        node_id: &str,
    ) -> ServiceResult<Option<StateMachineNodeRun>> {
        let sql = format!(
            "SELECT {SM_NODE_SELECT_COLS} \
             FROM bcs_state_machine_node_runs \
             WHERE env = ? AND run_id = ? AND node_id = ? AND record_status = 'active' \
             LIMIT 1"
        );
        let rows = self
            .db
            .query(DbStatement::with_params(
                sql,
                vec![
                    DbValue::from(self.env.as_str()),
                    DbValue::from(run_id),
                    DbValue::from(node_id),
                ],
            ))
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("state machine node get: {error}"))
            })?;
        rows.into_iter()
            .next()
            .map(row_to_state_machine_node_run)
            .transpose()
    }

    async fn mark_node_running(
        &self,
        run_id: &str,
        node_id: &str,
        attempt: i32,
        delivery_request_id: String,
        started_at: u64,
    ) -> ServiceResult<()> {
        let sql = format!(
            "UPDATE bcs_state_machine_node_runs \
             SET status = 'running', attempt = ?, delivery_request_id = ?, \
                 bot_delivery_run_id = NULL, outcome = NULL, responded_by = NULL, \
                 artifact_text = NULL, \
                 error_message = NULL, started_at_ms = ?, completed_at_ms = NULL, \
                 timeout_deadline_ms = CASE \
                   WHEN node_timeout_ms IS NULL THEN NULL \
                   ELSE ? + node_timeout_ms \
                 END, \
                 {} \
             WHERE env = ? AND run_id = ? AND node_id = ? \
               AND record_status = 'active'",
            self.flavor.set_modified_now()
        );
        self.db
            .execute(DbStatement::with_params(
                sql,
                vec![
                    DbValue::from(attempt),
                    DbValue::from(delivery_request_id.as_str()),
                    DbValue::from(started_at),
                    DbValue::from(started_at),
                    DbValue::from(self.env.as_str()),
                    DbValue::from(run_id),
                    DbValue::from(node_id),
                ],
            ))
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("state machine node mark running: {error}"))
            })?;
        Ok(())
    }

    async fn mark_node_running_if_run_active(
        &self,
        run_id: &str,
        node_id: &str,
        attempt: i32,
        delivery_request_id: String,
        started_at: u64,
    ) -> ServiceResult<bool> {
        let sql = match self.flavor {
            DbSqlFlavor::Mysql => format!(
                "UPDATE bcs_state_machine_node_runs n \
                 SET n.status = 'running', n.delivery_request_id = ?, \
                     n.bot_delivery_run_id = NULL, n.outcome = NULL, \
                     n.responded_by = NULL, n.artifact_text = NULL, \
                     n.error_message = NULL, n.started_at_ms = ?, n.completed_at_ms = NULL, \
                     n.timeout_deadline_ms = CASE \
                       WHEN n.node_timeout_ms IS NULL THEN NULL \
                       ELSE ? + n.node_timeout_ms \
                     END, \
                     {} \
                 WHERE n.env = ? AND n.run_id = ? AND n.node_id = ? \
                   AND n.attempt = ? AND n.status IN ('pending', 'ready', 'retry_scheduled') \
                   AND n.record_status = 'active' \
                   AND EXISTS ( \
                     SELECT 1 FROM bcs_state_machine_runs r \
                     WHERE r.env = n.env AND r.run_id = n.run_id \
                       AND r.status = 'running' AND r.record_status = 'active' \
                   )",
                "n.gmt_modified = NOW()",
            ),
            DbSqlFlavor::Sqlite => format!(
                "UPDATE bcs_state_machine_node_runs \
                 SET status = 'running', delivery_request_id = ?, \
                     bot_delivery_run_id = NULL, outcome = NULL, responded_by = NULL, \
                     artifact_text = NULL, \
                     error_message = NULL, started_at_ms = ?, completed_at_ms = NULL, \
                     timeout_deadline_ms = CASE \
                       WHEN node_timeout_ms IS NULL THEN NULL \
                       ELSE ? + node_timeout_ms \
                     END, \
                     {} \
                 WHERE env = ? AND run_id = ? AND node_id = ? \
                   AND attempt = ? AND status IN ('pending', 'ready', 'retry_scheduled') \
                   AND record_status = 'active' \
                   AND EXISTS ( \
                     SELECT 1 FROM bcs_state_machine_runs r \
                     WHERE r.env = bcs_state_machine_node_runs.env \
                       AND r.run_id = bcs_state_machine_node_runs.run_id \
                       AND r.status = 'running' AND r.record_status = 'active' \
                   )",
                self.flavor.set_modified_now(),
            ),
        };
        let result = self
            .db
            .execute(DbStatement::with_params(
                sql,
                vec![
                    DbValue::from(delivery_request_id.as_str()),
                    DbValue::from(started_at),
                    DbValue::from(started_at),
                    DbValue::from(self.env.as_str()),
                    DbValue::from(run_id),
                    DbValue::from(node_id),
                    DbValue::from(attempt),
                ],
            ))
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("state machine node mark running CAS: {error}"))
            })?;
        Ok(result.affected_rows > 0)
    }

    async fn complete_node_attempt(
        &self,
        run_id: &str,
        node_id: &str,
        attempt: i32,
        outcome: String,
        artifact_text: String,
        responded_by: Option<String>,
        completed_at: u64,
    ) -> ServiceResult<bool> {
        let sql = format!(
            "UPDATE bcs_state_machine_node_runs \
             SET status = 'completed', outcome = ?, responded_by = ?, artifact_text = ?, \
                 error_message = NULL, \
                 completed_at_ms = ?, timeout_deadline_ms = NULL, {} \
             WHERE env = ? AND run_id = ? AND node_id = ? \
               AND attempt = ? AND status = 'running' \
               AND record_status = 'active' \
               AND EXISTS ( \
                 SELECT 1 FROM bcs_state_machine_runs r \
                 WHERE r.env = bcs_state_machine_node_runs.env \
                   AND r.run_id = bcs_state_machine_node_runs.run_id \
                   AND r.status = 'running' AND r.record_status = 'active' \
               )",
            self.flavor.set_modified_now()
        );
        let result = self
            .db
            .execute(DbStatement::with_params(
                sql,
                vec![
                    DbValue::from(outcome),
                    DbValue::from(responded_by.as_deref()),
                    DbValue::from(artifact_text),
                    DbValue::from(completed_at),
                    DbValue::from(self.env.as_str()),
                    DbValue::from(run_id),
                    DbValue::from(node_id),
                    DbValue::from(attempt),
                ],
            ))
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("state machine node complete: {error}"))
            })?;
        Ok(result.affected_rows > 0)
    }

    async fn mark_human_node_running_if_run_active(
        &self,
        command: MarkHumanNodeRunningCommand,
    ) -> ServiceResult<bool> {
        let sql = format!(
            "UPDATE bcs_state_machine_node_runs \
             SET status = 'running', delivery_request_id = NULL, \
                 bot_delivery_run_id = NULL, outcome = NULL, responded_by = NULL, \
                 artifact_text = NULL, error_message = NULL, started_at_ms = ?, \
                 completed_at_ms = NULL, timeout_deadline_ms = ?, {} \
             WHERE env = ? AND run_id = ? AND node_id = ? AND attempt = ? \
               AND status IN ('pending', 'ready') AND assignee_bot_id = '' \
               AND record_status = 'active' \
               AND EXISTS ( \
                 SELECT 1 FROM bcs_state_machine_runs r \
                 WHERE r.env = bcs_state_machine_node_runs.env \
                   AND r.run_id = bcs_state_machine_node_runs.run_id \
                   AND r.status = 'running' AND r.record_status = 'active' \
               )",
            self.flavor.set_modified_now()
        );
        let result = self
            .db
            .execute(DbStatement::with_params(
                sql,
                vec![
                    DbValue::from(command.started_at_ms),
                    DbValue::from(command.timeout_deadline_ms),
                    DbValue::from(self.env.as_str()),
                    DbValue::from(command.run_id),
                    DbValue::from(command.node_id),
                    DbValue::from(command.attempt),
                ],
            ))
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!(
                    "state machine human node mark running: {error}"
                ))
            })?;
        Ok(result.affected_rows > 0)
    }

    async fn record_node_artifact_if_running(
        &self,
        run_id: &str,
        node_id: &str,
        attempt: i32,
        artifact_text: String,
    ) -> ServiceResult<bool> {
        let sql = format!(
            "UPDATE bcs_state_machine_node_runs \
             SET artifact_text = ?, error_message = NULL, {} \
             WHERE env = ? AND run_id = ? AND node_id = ? \
               AND attempt = ? AND status = 'running' \
               AND record_status = 'active' \
               AND EXISTS ( \
                 SELECT 1 FROM bcs_state_machine_runs r \
                 WHERE r.env = bcs_state_machine_node_runs.env \
                   AND r.run_id = bcs_state_machine_node_runs.run_id \
                   AND r.status = 'running' AND r.record_status = 'active' \
               )",
            self.flavor.set_modified_now()
        );
        let result = self
            .db
            .execute(DbStatement::with_params(
                sql,
                vec![
                    DbValue::from(artifact_text),
                    DbValue::from(self.env.as_str()),
                    DbValue::from(run_id),
                    DbValue::from(node_id),
                    DbValue::from(attempt),
                ],
            ))
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("state machine node record artifact: {error}"))
            })?;
        Ok(result.affected_rows > 0)
    }

    async fn record_human_response_if_running(
        &self,
        run_id: &str,
        node_id: &str,
        attempt: i32,
        artifact_text: String,
        responded_by: String,
    ) -> ServiceResult<bool> {
        let sql = format!(
            "UPDATE bcs_state_machine_node_runs \
             SET artifact_text = ?, responded_by = ?, error_message = NULL, {} \
             WHERE env = ? AND run_id = ? AND node_id = ? \
               AND attempt = ? AND status = 'running' AND artifact_text IS NULL \
               AND record_status = 'active' \
               AND EXISTS ( \
                 SELECT 1 FROM bcs_state_machine_runs r \
                 WHERE r.env = bcs_state_machine_node_runs.env \
                   AND r.run_id = bcs_state_machine_node_runs.run_id \
                   AND r.status = 'running' AND r.record_status = 'active' \
               )",
            self.flavor.set_modified_now()
        );
        let result = self
            .db
            .execute(DbStatement::with_params(
                sql,
                vec![
                    DbValue::from(artifact_text),
                    DbValue::from(responded_by),
                    DbValue::from(self.env.as_str()),
                    DbValue::from(run_id),
                    DbValue::from(node_id),
                    DbValue::from(attempt),
                ],
            ))
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("state machine human response record: {error}"))
            })?;
        Ok(result.affected_rows > 0)
    }

    async fn fail_node_attempt(
        &self,
        run_id: &str,
        node_id: &str,
        attempt: i32,
        error: String,
        completed_at: u64,
    ) -> ServiceResult<bool> {
        let sql = format!(
            "UPDATE bcs_state_machine_node_runs \
             SET status = 'failed', error_message = ?, completed_at_ms = ?, \
                 timeout_deadline_ms = NULL, {} \
             WHERE env = ? AND run_id = ? AND node_id = ? \
               AND attempt = ? AND status = 'running' \
               AND record_status = 'active' \
               AND EXISTS ( \
                 SELECT 1 FROM bcs_state_machine_runs r \
                 WHERE r.env = bcs_state_machine_node_runs.env \
                   AND r.run_id = bcs_state_machine_node_runs.run_id \
                   AND r.status = 'running' AND r.record_status = 'active' \
               )",
            self.flavor.set_modified_now()
        );
        let result = self
            .db
            .execute(DbStatement::with_params(
                sql,
                vec![
                    DbValue::from(error),
                    DbValue::from(completed_at),
                    DbValue::from(self.env.as_str()),
                    DbValue::from(run_id),
                    DbValue::from(node_id),
                    DbValue::from(attempt),
                ],
            ))
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("state machine node fail: {error}"))
            })?;
        Ok(result.affected_rows > 0)
    }

    async fn schedule_node_retry(
        &self,
        run_id: &str,
        node_id: &str,
        failed_attempt: i32,
        next_attempt: i32,
    ) -> ServiceResult<bool> {
        let sql = format!(
            "UPDATE bcs_state_machine_node_runs \
             SET status = 'retry_scheduled', attempt = ?, \
                 delivery_request_id = NULL, bot_delivery_run_id = NULL, \
                 artifact_text = NULL, error_message = NULL, started_at_ms = NULL, \
                 completed_at_ms = NULL, timeout_deadline_ms = NULL, \
                 {} \
             WHERE env = ? AND run_id = ? AND node_id = ? \
               AND attempt = ? AND status = 'failed' \
               AND record_status = 'active' \
               AND EXISTS ( \
                 SELECT 1 FROM bcs_state_machine_runs r \
                 WHERE r.env = bcs_state_machine_node_runs.env \
                   AND r.run_id = bcs_state_machine_node_runs.run_id \
                   AND r.status = 'running' AND r.record_status = 'active' \
               )",
            self.flavor.set_modified_now()
        );
        let result = self
            .db
            .execute(DbStatement::with_params(
                sql,
                vec![
                    DbValue::from(next_attempt),
                    DbValue::from(self.env.as_str()),
                    DbValue::from(run_id),
                    DbValue::from(node_id),
                    DbValue::from(failed_attempt),
                ],
            ))
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("state machine node retry: {error}"))
            })?;
        Ok(result.affected_rows > 0)
    }

    async fn skip_node(&self, run_id: &str, node_id: &str, skipped_at: u64) -> ServiceResult<bool> {
        let sql = format!(
            "UPDATE bcs_state_machine_node_runs \
             SET status = 'skipped', completed_at_ms = ?, \
                 timeout_deadline_ms = NULL, {} \
             WHERE env = ? AND run_id = ? AND node_id = ? \
               AND status = 'pending' AND record_status = 'active' \
               AND EXISTS ( \
                 SELECT 1 FROM bcs_state_machine_runs r \
                 WHERE r.env = bcs_state_machine_node_runs.env \
                   AND r.run_id = bcs_state_machine_node_runs.run_id \
                   AND r.status = 'running' AND r.record_status = 'active' \
               )",
            self.flavor.set_modified_now()
        );
        let result = self
            .db
            .execute(DbStatement::with_params(
                sql,
                vec![
                    DbValue::from(skipped_at),
                    DbValue::from(self.env.as_str()),
                    DbValue::from(run_id),
                    DbValue::from(node_id),
                ],
            ))
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("state machine node skip: {error}"))
            })?;
        Ok(result.affected_rows > 0)
    }

    async fn update_run_status(
        &self,
        run_id: &str,
        status: StateMachineRunStatus,
        output: Option<String>,
        error: Option<String>,
        updated_at: u64,
        completed_at: Option<u64>,
    ) -> ServiceResult<bool> {
        let sql = format!(
            "UPDATE bcs_state_machine_runs \
             SET status = ?, output_text = ?, error_message = ?, \
                 updated_at_ms = ?, completed_at_ms = ?, {} \
             WHERE env = ? AND run_id = ? AND record_status = 'active' \
               AND status NOT IN ('completed', 'failed', 'aborted')",
            self.flavor.set_modified_now()
        );
        let result = self
            .db
            .execute(DbStatement::with_params(
                sql,
                vec![
                    DbValue::from(run_status_to_str(status)),
                    DbValue::from(output.as_deref()),
                    DbValue::from(error.as_deref()),
                    DbValue::from(updated_at),
                    optional_u64_value(completed_at),
                    DbValue::from(self.env.as_str()),
                    DbValue::from(run_id),
                ],
            ))
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("state machine run status update: {error}"))
            })?;
        Ok(result.affected_rows > 0)
    }

    async fn upsert_delivery_correlation(
        &self,
        correlation: StateMachineDeliveryCorrelation,
    ) -> ServiceResult<()> {
        let now = current_millis();
        let coalesce_bot_run = match self.flavor {
            DbSqlFlavor::Mysql => "COALESCE(VALUES(bot_delivery_run_id), bot_delivery_run_id)",
            DbSqlFlavor::Sqlite => "COALESCE(excluded.bot_delivery_run_id, bot_delivery_run_id)",
        };
        let correlation_upsert = self.flavor.on_conflict_update(
            &["env", "delivery_request_id"],
            &[
                "state_machine_run_id",
                "node_id",
                "attempt",
                "assignee_bot_id",
                "updated_at_ms",
            ],
            &[
                ("bot_delivery_run_id", coalesce_bot_run),
                ("record_status", "'active'"),
                ("gmt_modified", self.flavor.now()),
            ],
        );
        let sql = format!(
            "INSERT INTO bcs_state_machine_delivery_correlations \
             (env, state_machine_run_id, node_id, attempt, assignee_bot_id, \
              delivery_request_id, bot_delivery_run_id, created_at_ms, \
              updated_at_ms, record_status) \
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active') \
             {correlation_upsert}"
        );
        self.db
            .execute(DbStatement::with_params(
                sql,
                vec![
                    DbValue::from(self.env.as_str()),
                    DbValue::from(correlation.state_machine_run_id.as_str()),
                    DbValue::from(correlation.node_id.as_str()),
                    DbValue::from(correlation.attempt),
                    DbValue::from(correlation.assignee_bot_id.as_str()),
                    DbValue::from(correlation.delivery_request_id.as_str()),
                    DbValue::from(correlation.bot_delivery_run_id.as_deref()),
                    DbValue::from(now),
                    DbValue::from(now),
                ],
            ))
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("delivery correlation upsert: {error}"))
            })?;
        Ok(())
    }

    async fn register_delivery_alias(
        &self,
        delivery_request_id: &str,
        bot_delivery_run_id: String,
    ) -> ServiceResult<()> {
        let now = current_millis();
        let correlation_sql = format!(
            "UPDATE bcs_state_machine_delivery_correlations \
             SET bot_delivery_run_id = ?, updated_at_ms = ?, \
                 {} \
             WHERE env = ? AND delivery_request_id = ? \
               AND record_status = 'active'",
            self.flavor.set_modified_now()
        );
        let node_sql = format!(
            "UPDATE bcs_state_machine_node_runs \
             SET bot_delivery_run_id = ?, {} \
             WHERE env = ? AND delivery_request_id = ? \
               AND record_status = 'active'",
            self.flavor.set_modified_now()
        );
        let results = self
            .db
            .transaction(vec![
                DbTransactionStep::Execute(DbStatement::with_params(
                    correlation_sql,
                    vec![
                        DbValue::from(bot_delivery_run_id.as_str()),
                        DbValue::from(now),
                        DbValue::from(self.env.as_str()),
                        DbValue::from(delivery_request_id),
                    ],
                )),
                DbTransactionStep::Execute(DbStatement::with_params(
                    node_sql,
                    vec![
                        DbValue::from(bot_delivery_run_id.as_str()),
                        DbValue::from(self.env.as_str()),
                        DbValue::from(delivery_request_id),
                    ],
                )),
            ])
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("delivery correlation alias: {error}"))
            })?;
        let correlation_affected = match results.first() {
            Some(DbTransactionStepResult::Executed(result)) => result.affected_rows,
            _ => 0,
        };
        if correlation_affected == 0 {
            return Err(ServiceError::InternalError(format!(
                "delivery correlation not found: {delivery_request_id}"
            )));
        }
        Ok(())
    }

    async fn lookup_delivery_correlation(
        &self,
        run_id: &str,
    ) -> ServiceResult<Option<StateMachineDeliveryCorrelation>> {
        let request_sql = format!(
            "SELECT {SM_CORRELATION_SELECT_COLS} \
             FROM bcs_state_machine_delivery_correlations \
             WHERE env = ? AND delivery_request_id = ? AND record_status = 'active' \
             LIMIT 1"
        );
        let rows = self
            .db
            .query(DbStatement::with_params(
                request_sql,
                vec![DbValue::from(self.env.as_str()), DbValue::from(run_id)],
            ))
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("delivery correlation lookup: {error}"))
            })?;
        if let Some(row) = rows.into_iter().next() {
            return row_to_delivery_correlation(row).map(Some);
        }

        let bot_run_sql = format!(
            "SELECT {SM_CORRELATION_SELECT_COLS} \
             FROM bcs_state_machine_delivery_correlations \
             WHERE env = ? AND bot_delivery_run_id = ? AND record_status = 'active' \
             LIMIT 1"
        );
        let rows = self
            .db
            .query(DbStatement::with_params(
                bot_run_sql,
                vec![DbValue::from(self.env.as_str()), DbValue::from(run_id)],
            ))
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("delivery correlation alias lookup: {error}"))
            })?;
        rows.into_iter()
            .next()
            .map(row_to_delivery_correlation)
            .transpose()
    }

    async fn list_expired_running_node_runs(
        &self,
        now_ms: u64,
        timeout_grace_ms: u64,
        limit: usize,
    ) -> ServiceResult<Vec<StateMachineNodeRun>> {
        let sql = "SELECT n.run_id, n.node_id, n.status, n.attempt, n.node_timeout_ms, \
                          n.timeout_deadline_ms, n.max_attempts, n.assignee_bot_id, \
                          n.delivery_request_id, n.bot_delivery_run_id, n.artifact_text, \
                          n.error_message, n.started_at_ms, n.completed_at_ms \
                   FROM bcs_state_machine_node_runs n \
                   INNER JOIN bcs_state_machine_runs r \
                     ON r.env = n.env AND r.run_id = n.run_id \
                   WHERE n.env = ? AND n.status = 'running' \
                     AND n.timeout_deadline_ms IS NOT NULL \
                     AND n.timeout_deadline_ms + ? <= ? \
                     AND n.record_status = 'active' \
                     AND r.status = 'running' AND r.record_status = 'active' \
                   ORDER BY n.timeout_deadline_ms ASC, n.run_id ASC, n.node_id ASC \
                   LIMIT ?";
        let rows = self
            .db
            .query(DbStatement::with_params(
                sql,
                vec![
                    DbValue::from(self.env.as_str()),
                    DbValue::from(timeout_grace_ms),
                    DbValue::from(now_ms),
                    DbValue::from(limit as u64),
                ],
            ))
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("expired state machine node list: {error}"))
            })?;
        rows.into_iter()
            .map(row_to_state_machine_node_run)
            .collect()
    }
}

#[async_trait]
impl CollaborationEventRepoPort for MySqlCollaborationStore {
    async fn append_event(
        &self,
        state_machine_run_id: &str,
        node_id: Option<&str>,
        attempt: Option<i32>,
        event_type: &str,
        payload: serde_json::Value,
        created_at: u64,
    ) -> ServiceResult<()> {
        let sql = "INSERT INTO bcs_collaboration_events \
                   (env, state_machine_run_id, node_id, attempt, event_type, \
                    payload_json, created_at_ms, record_status) \
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'active')";
        self.db
            .execute(DbStatement::with_params(
                sql,
                vec![
                    DbValue::from(self.env.as_str()),
                    DbValue::from(state_machine_run_id),
                    DbValue::from(node_id),
                    attempt.map(DbValue::from).unwrap_or(DbValue::Null),
                    DbValue::from(event_type),
                    DbValue::from(payload.to_string()),
                    DbValue::from(created_at),
                ],
            ))
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("collaboration event append: {error}"))
            })?;
        Ok(())
    }

    async fn list_events_by_run_and_type(
        &self,
        state_machine_run_id: &str,
        event_type: &str,
    ) -> ServiceResult<Vec<CollaborationEventRecord>> {
        let sql = "SELECT state_machine_run_id, node_id, attempt, event_type, \
                          payload_json, created_at_ms \
                   FROM bcs_collaboration_events \
                   WHERE env = ? AND state_machine_run_id = ? AND event_type = ? \
                     AND record_status = 'active' \
                   ORDER BY id ASC";
        let rows = self
            .db
            .query(DbStatement::with_params(
                sql,
                vec![
                    DbValue::from(self.env.as_str()),
                    DbValue::from(state_machine_run_id),
                    DbValue::from(event_type),
                ],
            ))
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("collaboration event list: {error}"))
            })?;
        rows.into_iter().map(row_to_collaboration_event).collect()
    }

    async fn list_events_by_run_node_and_type(
        &self,
        state_machine_run_id: &str,
        node_id: &str,
        event_type: &str,
    ) -> ServiceResult<Vec<CollaborationEventRecord>> {
        let sql = "SELECT state_machine_run_id, node_id, attempt, event_type, \
                          payload_json, created_at_ms \
                   FROM bcs_collaboration_events \
                   WHERE env = ? AND state_machine_run_id = ? AND node_id = ? \
                     AND event_type = ? AND record_status = 'active' \
                   ORDER BY id ASC";
        let rows = self
            .db
            .query(DbStatement::with_params(
                sql,
                vec![
                    DbValue::from(self.env.as_str()),
                    DbValue::from(state_machine_run_id),
                    DbValue::from(node_id),
                    DbValue::from(event_type),
                ],
            ))
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("collaboration event list by node: {error}"))
            })?;
        rows.into_iter().map(row_to_collaboration_event).collect()
    }
}

fn definition_json(definition: &CollaborationDefinition) -> ServiceResult<String> {
    serde_json::to_string(definition)
        .map_err(|error| ServiceError::InternalError(format!("definition serialize: {error}")))
}

fn sha256_hex(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}

fn definition_conflict_error(
    definition_id: &str,
    version: i32,
    existing_hash: &str,
    incoming_hash: &str,
) -> ServiceError {
    ServiceError::Conflict(format!(
        "CollaborationDefinition '{}@{}' already exists with different content \
         (existing_hash={}, incoming_hash={})",
        definition_id,
        version,
        short_hash(existing_hash),
        short_hash(incoming_hash)
    ))
}

fn short_hash(value: &str) -> &str {
    value.get(..16).unwrap_or(value)
}

fn current_millis() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_millis() as u64)
        .unwrap_or(0)
}

fn optional_u64_value(value: Option<u64>) -> DbValue {
    value.map(DbValue::from).unwrap_or(DbValue::Null)
}

fn run_insert_params(env: &str, run: &StateMachineRun) -> Vec<DbValue> {
    vec![
        DbValue::from(env),
        DbValue::from(run.run_id.as_str()),
        DbValue::from(run.definition_id.as_str()),
        DbValue::from(run.definition_version),
        DbValue::from(run.group_id.as_str()),
        DbValue::from(run.group_version),
        DbValue::from(run.session_id.as_str()),
        DbValue::from(run.created_by.as_deref()),
        DbValue::from(run_status_to_str(run.status)),
        DbValue::from(run.input.to_string()),
        DbValue::from(run.output.as_deref()),
        DbValue::from(run.error.as_deref()),
        DbValue::from(run.created_at),
        DbValue::from(run.updated_at),
        optional_u64_value(run.completed_at),
    ]
}

fn build_guarded_node_runs_inserts(
    env: &str,
    run_id: &str,
    nodes: &[StateMachineNodeRun],
) -> Vec<DbTransactionStep> {
    nodes
        .iter()
        .map(|node| {
            let sql = "INSERT INTO bcs_state_machine_node_runs \
                       (env, run_id, node_id, status, attempt, node_timeout_ms, \
                        timeout_deadline_ms, max_attempts, assignee_bot_id, outcome, responded_by, \
                        delivery_request_id, bot_delivery_run_id, artifact_text, error_message, \
                        started_at_ms, completed_at_ms, record_status) \
                       SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active' \
                       WHERE EXISTS ( \
                         SELECT 1 FROM bcs_state_machine_runs \
                         WHERE env = ? AND run_id = ? AND record_status = 'active' \
                       )";
            let mut params = node_insert_params(env, run_id, node);
            params.extend([DbValue::from(env), DbValue::from(run_id)]);
            DbTransactionStep::Execute(DbStatement::with_params(sql, params))
        })
        .collect()
}

fn build_node_runs_insert(
    env: &str,
    run_id: &str,
    nodes: &[StateMachineNodeRun],
) -> Option<(String, Vec<DbValue>)> {
    if nodes.is_empty() {
        return None;
    }
    let placeholders = nodes
        .iter()
        .map(|_| "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')")
        .collect::<Vec<_>>()
        .join(", ");
    let sql = format!(
        "INSERT INTO bcs_state_machine_node_runs \
         (env, run_id, node_id, status, attempt, node_timeout_ms, \
          timeout_deadline_ms, max_attempts, assignee_bot_id, outcome, responded_by, \
          delivery_request_id, bot_delivery_run_id, artifact_text, error_message, \
          started_at_ms, completed_at_ms, record_status) \
         VALUES {placeholders}"
    );
    let mut params = Vec::with_capacity(nodes.len() * 17);
    for node in nodes {
        params.extend(node_insert_params(env, run_id, node));
    }
    Some((sql, params))
}

fn node_insert_params(
    env: &str,
    run_id: &str,
    node: &StateMachineNodeRun,
) -> Vec<DbValue> {
    vec![
        DbValue::from(env),
        DbValue::from(run_id),
        DbValue::from(node.node_id.as_str()),
        DbValue::from(node_status_to_str(node.status)),
        DbValue::from(node.attempt),
        optional_u64_value(node.node_timeout_ms),
        optional_u64_value(node.timeout_deadline_ms),
        DbValue::from(node.max_attempts),
        DbValue::from(node.assignee_bot_id.as_deref().unwrap_or("")),
        DbValue::from(node.outcome.as_deref()),
        DbValue::from(node.responded_by.as_deref()),
        DbValue::from(node.delivery_request_id.as_deref()),
        DbValue::from(node.bot_delivery_run_id.as_deref()),
        DbValue::from(node.artifact_text.as_deref()),
        DbValue::from(node.error.as_deref()),
        optional_u64_value(node.started_at),
        optional_u64_value(node.completed_at),
    ]
}

fn row_to_state_machine_run(row: DbRow) -> ServiceResult<StateMachineRun> {
    let status_raw: String = db_get_column(&row, "status")
        .map_err(|error| ServiceError::InternalError(format!("run status: {error}")))?;
    Ok(StateMachineRun {
        run_id: db_string(&row, "run_id")?,
        definition_id: db_string(&row, "definition_id")?,
        definition_version: db_i32(&row, "definition_version")?,
        group_id: db_string(&row, "group_id")?,
        group_version: db_i32(&row, "group_version")?,
        session_id: db_string(&row, "session_id")?,
        created_by: db_optional_string(&row, "created_by")?,
        status: parse_run_status(&status_raw)?,
        input: db_json(&row, "input_json")?.unwrap_or(serde_json::Value::Null),
        output: db_optional_string(&row, "output_text")?,
        error: db_optional_string(&row, "error_message")?,
        created_at: db_u64(&row, "created_at_ms")?,
        updated_at: db_u64(&row, "updated_at_ms")?,
        completed_at: db_optional_u64(&row, "completed_at_ms")?,
    })
}

fn row_to_state_machine_node_run(row: DbRow) -> ServiceResult<StateMachineNodeRun> {
    let status_raw: String = db_get_column(&row, "status")
        .map_err(|error| ServiceError::InternalError(format!("node status: {error}")))?;
    Ok(StateMachineNodeRun {
        run_id: db_string(&row, "run_id")?,
        node_id: db_string(&row, "node_id")?,
        status: parse_node_status(&status_raw)?,
        attempt: db_i32(&row, "attempt")?,
        node_timeout_ms: db_optional_u64(&row, "node_timeout_ms")?,
        timeout_deadline_ms: db_optional_u64(&row, "timeout_deadline_ms")?,
        max_attempts: db_i32(&row, "max_attempts")?,
        assignee_bot_id: db_assignee_bot_id(&row)?,
        outcome: db_optional_string(&row, "outcome")?,
        responded_by: db_optional_string(&row, "responded_by")?,
        delivery_request_id: db_optional_string(&row, "delivery_request_id")?,
        bot_delivery_run_id: db_optional_string(&row, "bot_delivery_run_id")?,
        artifact_text: db_optional_string(&row, "artifact_text")?,
        error: db_optional_string(&row, "error_message")?,
        started_at: db_optional_u64(&row, "started_at_ms")?,
        completed_at: db_optional_u64(&row, "completed_at_ms")?,
    })
}

fn row_to_delivery_correlation(row: DbRow) -> ServiceResult<StateMachineDeliveryCorrelation> {
    Ok(StateMachineDeliveryCorrelation {
        state_machine_run_id: db_string(&row, "state_machine_run_id")?,
        node_id: db_string(&row, "node_id")?,
        attempt: db_i32(&row, "attempt")?,
        assignee_bot_id: db_string(&row, "assignee_bot_id")?,
        delivery_request_id: db_string(&row, "delivery_request_id")?,
        bot_delivery_run_id: db_optional_string(&row, "bot_delivery_run_id")?,
    })
}

fn row_to_collaboration_event(row: DbRow) -> ServiceResult<CollaborationEventRecord> {
    Ok(CollaborationEventRecord {
        state_machine_run_id: db_string(&row, "state_machine_run_id")?,
        node_id: db_optional_string(&row, "node_id")?,
        attempt: db_optional_i32(&row, "attempt")?,
        event_type: db_string(&row, "event_type")?,
        payload: db_json(&row, "payload_json")?.unwrap_or(serde_json::Value::Null),
        created_at: db_u64(&row, "created_at_ms")?,
    })
}

#[derive(Clone)]
pub struct DbCollaborationTemplateRepo {
    db: Arc<dyn DbPlugin>,
    env: String,
}

impl DbCollaborationTemplateRepo {
    pub fn new(db: Arc<dyn DbPlugin>, env: impl Into<String>) -> Self {
        Self {
            db,
            env: env.into(),
        }
    }
}

#[async_trait]
impl CollaborationTemplateRepoPort for DbCollaborationTemplateRepo {
    async fn list_entries(&self) -> ServiceResult<Vec<CollaborationTemplateEntry>> {
        let rows = self
            .db
            .query(DbStatement::with_params(
                "SELECT template_id, priority \
                   FROM bcs_collaboration_templates \
                  WHERE env = ? \
                    AND visibility = 'public' \
                    AND record_status = 'active' \
                  ORDER BY priority, template_id",
                vec![DbValue::from(self.env.clone())],
            ))
            .await
            .map_err(|error| db_plugin_error("list templates", error))?;

        let mut entries = Vec::with_capacity(rows.len());
        let mut ids = Vec::with_capacity(rows.len());
        for row in rows {
            let id = db_string(&row, "template_id")?;
            let priority = template_priority_from_row(&row)?;
            ids.push(id.clone());
            entries.push(CollaborationTemplateEntry {
                id,
                tags: Vec::new(),
                priority,
                available_languages: Vec::new(),
            });
        }
        if ids.is_empty() {
            return Ok(entries);
        }

        let tags_by_template = self.tags_for_templates(&ids).await?;
        let languages_by_template = self.languages_for_templates(&ids).await?;
        for entry in &mut entries {
            entry.tags = tags_by_template.get(&entry.id).cloned().unwrap_or_default();
            entry.available_languages = languages_by_template
                .get(&entry.id)
                .cloned()
                .unwrap_or_default();
        }

        Ok(entries)
    }

    async fn get_raw_yaml(&self, id: &str, lang: &str) -> ServiceResult<Option<String>> {
        let rows = self
            .db
            .query(DbStatement::with_params(
                "SELECT c.yaml_text \
                   FROM bcs_collaboration_template_contents c \
                   JOIN bcs_collaboration_templates t \
                     ON t.env = c.env AND t.template_id = c.template_id \
                  WHERE c.env = ? \
                    AND c.template_id = ? \
                    AND c.lang = ? \
                    AND c.record_status = 'active' \
                    AND t.record_status = 'active' \
                    AND t.visibility = 'public' \
                  LIMIT 1",
                vec![
                    DbValue::from(self.env.clone()),
                    DbValue::from(id.to_string()),
                    DbValue::from(lang.to_string()),
                ],
            ))
            .await
            .map_err(|error| db_plugin_error("get template YAML", error))?;

        rows.first()
            .map(|row| db_string(row, "yaml_text"))
            .transpose()
    }

    async fn available_languages(&self, id: &str) -> ServiceResult<Vec<String>> {
        let rows = self
            .db
            .query(DbStatement::with_params(
                "SELECT c.lang \
                   FROM bcs_collaboration_template_contents c \
                   JOIN bcs_collaboration_templates t \
                     ON t.env = c.env AND t.template_id = c.template_id \
                  WHERE c.env = ? \
                    AND c.template_id = ? \
                    AND c.record_status = 'active' \
                    AND t.record_status = 'active' \
                    AND t.visibility = 'public' \
                  ORDER BY c.lang",
                vec![
                    DbValue::from(self.env.clone()),
                    DbValue::from(id.to_string()),
                ],
            ))
            .await
            .map_err(|error| db_plugin_error("list template languages", error))?;
        collect_string_column(rows, "lang")
    }

    async fn supported_languages(&self) -> ServiceResult<Vec<String>> {
        let rows = self
            .db
            .query(DbStatement::with_params(
                "SELECT DISTINCT c.lang \
                   FROM bcs_collaboration_template_contents c \
                   JOIN bcs_collaboration_templates t \
                     ON t.env = c.env AND t.template_id = c.template_id \
                  WHERE c.env = ? \
                    AND c.record_status = 'active' \
                    AND t.record_status = 'active' \
                    AND t.visibility = 'public' \
                  ORDER BY c.lang",
                vec![DbValue::from(self.env.clone())],
            ))
            .await
            .map_err(|error| db_plugin_error("list supported template languages", error))?;
        collect_string_column(rows, "lang")
    }
}

impl DbCollaborationTemplateRepo {
    async fn tags_for_templates(
        &self,
        ids: &[String],
    ) -> ServiceResult<BTreeMap<String, Vec<String>>> {
        let rows = self
            .db
            .query(DbStatement::with_params(
                format!(
                    "SELECT template_id, tag \
                       FROM bcs_collaboration_template_tags \
                      WHERE env = ? AND template_id IN ({}) \
                      ORDER BY template_id, tag",
                    placeholders(ids.len())
                ),
                env_and_ids_params(&self.env, ids),
            ))
            .await
            .map_err(|error| db_plugin_error("list template tags", error))?;

        let mut tags_by_template: BTreeMap<String, Vec<String>> = BTreeMap::new();
        for row in rows {
            let id = db_string(&row, "template_id")?;
            let tag = db_string(&row, "tag")?;
            tags_by_template.entry(id).or_default().push(tag);
        }
        Ok(tags_by_template)
    }

    async fn languages_for_templates(
        &self,
        ids: &[String],
    ) -> ServiceResult<BTreeMap<String, Vec<String>>> {
        let rows = self
            .db
            .query(DbStatement::with_params(
                format!(
                    "SELECT template_id, lang \
                       FROM bcs_collaboration_template_contents \
                      WHERE env = ? \
                        AND record_status = 'active' \
                        AND template_id IN ({}) \
                      ORDER BY template_id, lang",
                    placeholders(ids.len())
                ),
                env_and_ids_params(&self.env, ids),
            ))
            .await
            .map_err(|error| db_plugin_error("list template content languages", error))?;

        let mut languages_by_template: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
        for row in rows {
            let id = db_string(&row, "template_id")?;
            let lang = db_string(&row, "lang")?;
            languages_by_template.entry(id).or_default().insert(lang);
        }
        Ok(languages_by_template
            .into_iter()
            .map(|(id, languages)| (id, languages.into_iter().collect()))
            .collect())
    }
}

fn collect_string_column(rows: Vec<DbRow>, column: &str) -> ServiceResult<Vec<String>> {
    rows.into_iter()
        .map(|row| db_string(&row, column))
        .collect()
}

fn template_priority_from_row(row: &DbRow) -> ServiceResult<u32> {
    let priority = db_u64(row, "priority")?;
    u32::try_from(priority).map_err(|error| {
        ServiceError::InternalError(format!(
            "template priority out of range: {priority} ({error})"
        ))
    })
}

fn placeholders(count: usize) -> String {
    std::iter::repeat_n("?", count)
        .collect::<Vec<_>>()
        .join(", ")
}

fn env_and_ids_params(env: &str, ids: &[String]) -> Vec<DbValue> {
    let mut params = Vec::with_capacity(ids.len() + 1);
    params.push(DbValue::from(env.to_string()));
    params.extend(ids.iter().cloned().map(DbValue::from));
    params
}

fn db_plugin_error(action: &str, error: DbError) -> ServiceError {
    ServiceError::InternalError(format!("template database {action}: {error}"))
}

fn db_string(row: &DbRow, column: &str) -> ServiceResult<String> {
    db_get_column(row, column)
        .map_err(|error| ServiceError::InternalError(format!("{column}: {error}")))
}

fn db_assignee_bot_id(row: &DbRow) -> ServiceResult<Option<String>> {
    let value = db_string(row, "assignee_bot_id")?;
    Ok((!value.is_empty()).then_some(value))
}

fn db_optional_string(row: &DbRow, column: &str) -> ServiceResult<Option<String>> {
    db_get_column_opt(row, column)
        .map_err(|error| ServiceError::InternalError(format!("{column}: {error}")))
}

fn db_i32(row: &DbRow, column: &str) -> ServiceResult<i32> {
    db_get_column(row, column)
        .map_err(|error| ServiceError::InternalError(format!("{column}: {error}")))
}

fn db_optional_i32(row: &DbRow, column: &str) -> ServiceResult<Option<i32>> {
    db_get_column_opt(row, column)
        .map_err(|error| ServiceError::InternalError(format!("{column}: {error}")))
}

fn db_u64(row: &DbRow, column: &str) -> ServiceResult<u64> {
    db_optional_u64(row, column)?
        .ok_or_else(|| ServiceError::InternalError(format!("{column}: column is missing or NULL")))
}

fn db_optional_u64(row: &DbRow, column: &str) -> ServiceResult<Option<u64>> {
    match row.get(column) {
        None | Some(DbValue::Null) => Ok(None),
        Some(value) => value.as_u64().map(Some).ok_or_else(|| {
            ServiceError::InternalError(format!("{column}: not an unsigned integer: {value:?}"))
        }),
    }
}

fn db_json(row: &DbRow, column: &str) -> ServiceResult<Option<serde_json::Value>> {
    let raw = db_optional_string(row, column)?;
    match raw {
        None => Ok(None),
        Some(value) if value.is_empty() => Ok(None),
        Some(value) => serde_json::from_str(&value)
            .map(Some)
            .map_err(|error| ServiceError::InternalError(format!("{column}: json parse: {error}"))),
    }
}

fn run_status_to_str(status: StateMachineRunStatus) -> &'static str {
    match status {
        StateMachineRunStatus::Pending => "pending",
        StateMachineRunStatus::Running => "running",
        StateMachineRunStatus::Completed => "completed",
        StateMachineRunStatus::Failed => "failed",
        StateMachineRunStatus::Aborted => "aborted",
    }
}

fn parse_run_status(value: &str) -> ServiceResult<StateMachineRunStatus> {
    match value {
        "pending" => Ok(StateMachineRunStatus::Pending),
        "running" => Ok(StateMachineRunStatus::Running),
        "completed" => Ok(StateMachineRunStatus::Completed),
        "failed" => Ok(StateMachineRunStatus::Failed),
        "aborted" => Ok(StateMachineRunStatus::Aborted),
        other => Err(ServiceError::InternalError(format!(
            "unknown state machine run status: {other}"
        ))),
    }
}

fn node_status_to_str(status: StateMachineNodeStatus) -> &'static str {
    match status {
        StateMachineNodeStatus::Pending => "pending",
        StateMachineNodeStatus::Ready => "ready",
        StateMachineNodeStatus::Running => "running",
        StateMachineNodeStatus::Completed => "completed",
        StateMachineNodeStatus::Failed => "failed",
        StateMachineNodeStatus::RetryScheduled => "retry_scheduled",
        StateMachineNodeStatus::Skipped => "skipped",
    }
}

fn parse_node_status(value: &str) -> ServiceResult<StateMachineNodeStatus> {
    match value {
        "pending" => Ok(StateMachineNodeStatus::Pending),
        "ready" => Ok(StateMachineNodeStatus::Ready),
        "running" => Ok(StateMachineNodeStatus::Running),
        "completed" => Ok(StateMachineNodeStatus::Completed),
        "failed" => Ok(StateMachineNodeStatus::Failed),
        "retry_scheduled" => Ok(StateMachineNodeStatus::RetryScheduled),
        "skipped" => Ok(StateMachineNodeStatus::Skipped),
        other => Err(ServiceError::InternalError(format!(
            "unknown state machine node status: {other}"
        ))),
    }
}

fn node_mut<'a>(
    inner: &'a mut StoreInner,
    run_id: &str,
    node_id: &str,
) -> ServiceResult<&'a mut StateMachineNodeRun> {
    inner
        .nodes
        .get_mut(&(run_id.to_string(), node_id.to_string()))
        .ok_or_else(|| {
            ServiceError::InternalError(format!("state machine node not found: {run_id}/{node_id}"))
        })
}

fn run_is_running(inner: &StoreInner, run_id: &str) -> ServiceResult<bool> {
    let run = inner.runs.get(run_id).ok_or_else(|| {
        ServiceError::InternalError(format!("state machine run not found: {run_id}"))
    })?;
    Ok(run.status == StateMachineRunStatus::Running)
}

fn is_terminal(status: StateMachineRunStatus) -> bool {
    matches!(
        status,
        StateMachineRunStatus::Completed
            | StateMachineRunStatus::Failed
            | StateMachineRunStatus::Aborted
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use bcs_db_api::DbPlugin;
    use bcs_db_local::LocalSqliteDbPlugin;
    use bcs_service_api::{
        CollaborationTemplateRepoPort, StateMachineDefinitionRepoPort, StateMachineRunRepoPort,
    };

    async fn sqlite_template_repo()
    -> Result<(Arc<dyn DbPlugin>, DbCollaborationTemplateRepo), Box<dyn std::error::Error>> {
        let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new()?);
        db.execute(DbStatement::new(
            "CREATE TABLE bcs_collaboration_templates (
                env TEXT NOT NULL,
                template_id TEXT NOT NULL,
                visibility TEXT NOT NULL,
                priority INTEGER NOT NULL,
                record_status TEXT NOT NULL,
                PRIMARY KEY (env, template_id)
            )",
        ))
        .await?;
        db.execute(DbStatement::new(
            "CREATE TABLE bcs_collaboration_template_contents (
                env TEXT NOT NULL,
                template_id TEXT NOT NULL,
                lang TEXT NOT NULL,
                yaml_text TEXT NOT NULL,
                record_status TEXT NOT NULL,
                PRIMARY KEY (env, template_id, lang)
            )",
        ))
        .await?;
        db.execute(DbStatement::new(
            "CREATE TABLE bcs_collaboration_template_tags (
                env TEXT NOT NULL,
                template_id TEXT NOT NULL,
                tag TEXT NOT NULL,
                PRIMARY KEY (env, template_id, tag)
            )",
        ))
        .await?;

        Ok((db.clone(), DbCollaborationTemplateRepo::new(db, "dev")))
    }

    async fn sqlite_state_machine_store()
    -> Result<(Arc<dyn DbPlugin>, MySqlCollaborationStore), Box<dyn std::error::Error>> {
        let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new()?);
        db.execute(DbStatement::new(
            "CREATE TABLE bcs_group_sessions (
                env TEXT NOT NULL,
                session_id TEXT NOT NULL,
                UNIQUE(env, session_id)
            )",
        ))
        .await?;
        db.execute(DbStatement::new(
            "CREATE TABLE bcs_state_machine_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                env TEXT NOT NULL,
                run_id TEXT NOT NULL,
                definition_id TEXT NOT NULL,
                definition_version INTEGER NOT NULL,
                group_id TEXT NOT NULL,
                group_version INTEGER NOT NULL,
                session_id TEXT NOT NULL,
                created_by TEXT DEFAULT NULL,
                status TEXT NOT NULL,
                input_json TEXT DEFAULT NULL,
                output_text TEXT DEFAULT NULL,
                error_message TEXT DEFAULT NULL,
                created_at_ms INTEGER NOT NULL,
                updated_at_ms INTEGER NOT NULL,
                completed_at_ms INTEGER DEFAULT NULL,
                record_status TEXT NOT NULL DEFAULT 'active',
                UNIQUE(env, run_id)
            )",
        ))
        .await?;
        db.execute(DbStatement::new(
            "CREATE TABLE bcs_state_machine_node_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                env TEXT NOT NULL,
                run_id TEXT NOT NULL,
                node_id TEXT NOT NULL,
                status TEXT NOT NULL,
                attempt INTEGER NOT NULL DEFAULT 0,
                node_timeout_ms INTEGER DEFAULT NULL,
                timeout_deadline_ms INTEGER DEFAULT NULL,
                max_attempts INTEGER NOT NULL DEFAULT 1,
                assignee_bot_id TEXT NOT NULL,
                outcome TEXT DEFAULT NULL,
                responded_by TEXT DEFAULT NULL,
                delivery_request_id TEXT DEFAULT NULL,
                bot_delivery_run_id TEXT DEFAULT NULL,
                artifact_text TEXT DEFAULT NULL,
                error_message TEXT DEFAULT NULL,
                started_at_ms INTEGER DEFAULT NULL,
                completed_at_ms INTEGER DEFAULT NULL,
                record_status TEXT NOT NULL DEFAULT 'active',
                UNIQUE(env, run_id, node_id)
            )",
        ))
        .await?;

        Ok((
            db.clone(),
            MySqlCollaborationStore::sqlite(db, "dev".to_string()),
        ))
    }

    #[tokio::test]
    async fn sqlite_store_marks_node_running_with_cas_sql() -> Result<(), Box<dyn std::error::Error>>
    {
        let (db, store) = sqlite_state_machine_store().await?;
        db.execute(DbStatement::with_params(
            "INSERT INTO bcs_group_sessions (env, session_id) VALUES (?, ?)",
            vec![DbValue::from("dev"), DbValue::from("session")],
        ))
        .await?;
        let run = StateMachineRun {
            run_id: "run-sqlite-cas".to_string(),
            definition_id: "definition".to_string(),
            definition_version: 1,
            group_id: "group".to_string(),
            group_version: 1,
            session_id: "session".to_string(),
            created_by: None,
            status: StateMachineRunStatus::Running,
            input: serde_json::Value::Null,
            output: None,
            error: None,
            created_at: 1_000,
            updated_at: 1_000,
            completed_at: None,
        };
        let node = StateMachineNodeRun {
            run_id: run.run_id.clone(),
            node_id: "answer".to_string(),
            status: StateMachineNodeStatus::Pending,
            attempt: 0,
            node_timeout_ms: Some(120_000),
            timeout_deadline_ms: None,
            max_attempts: 2,
            assignee_bot_id: Some("bot-a".to_string()),
            outcome: None,
            responded_by: None,
            delivery_request_id: None,
            bot_delivery_run_id: None,
            artifact_text: None,
            error: None,
            started_at: None,
            completed_at: None,
        };
        assert!(
            store
                .create_run_if_session_idle(run, vec![node])
                .await?
        );
        assert!(
            !store
                .create_run_if_session_idle(
                    StateMachineRun {
                        run_id: "run-sqlite-concurrent".to_string(),
                        definition_id: "definition".to_string(),
                        definition_version: 1,
                        group_id: "group".to_string(),
                        group_version: 1,
                        session_id: "session".to_string(),
                        created_by: None,
                        status: StateMachineRunStatus::Running,
                        input: serde_json::Value::Null,
                        output: None,
                        error: None,
                        created_at: 1_001,
                        updated_at: 1_001,
                        completed_at: None,
                    },
                    Vec::new(),
                )
                .await?
        );

        let marked = store
            .mark_node_running_if_run_active(
                "run-sqlite-cas",
                "answer",
                0,
                "delivery-1".to_string(),
                2_000,
            )
            .await?;

        assert!(marked);
        let node = store
            .get_node_run("run-sqlite-cas", "answer")
            .await?
            .expect("node exists");
        assert_eq!(node.status, StateMachineNodeStatus::Running);
        assert_eq!(node.delivery_request_id.as_deref(), Some("delivery-1"));
        assert_eq!(node.started_at, Some(2_000));
        assert_eq!(node.timeout_deadline_ms, Some(122_000));

        let recorded = store
            .record_node_artifact_if_running("run-sqlite-cas", "answer", 0, "candidate".to_string())
            .await?;
        assert!(recorded);
        let node = store
            .get_node_run("run-sqlite-cas", "answer")
            .await?
            .expect("node exists");
        assert_eq!(node.status, StateMachineNodeStatus::Running);
        assert_eq!(node.artifact_text.as_deref(), Some("candidate"));

        Ok(())
    }

    async fn insert_template(
        db: &dyn DbPlugin,
        id: &str,
        visibility: &str,
        priority: i64,
        status: &str,
    ) -> Result<(), Box<dyn std::error::Error>> {
        db.execute(DbStatement::with_params(
            "INSERT INTO bcs_collaboration_templates \
             (env, template_id, visibility, priority, record_status) VALUES (?, ?, ?, ?, ?)",
            vec![
                DbValue::from("dev"),
                DbValue::from(id.to_string()),
                DbValue::from(visibility.to_string()),
                DbValue::from(priority),
                DbValue::from(status.to_string()),
            ],
        ))
        .await?;
        Ok(())
    }

    async fn insert_template_content(
        db: &dyn DbPlugin,
        id: &str,
        lang: &str,
        yaml: &str,
        status: &str,
    ) -> Result<(), Box<dyn std::error::Error>> {
        db.execute(DbStatement::with_params(
            "INSERT INTO bcs_collaboration_template_contents \
             (env, template_id, lang, yaml_text, record_status) VALUES (?, ?, ?, ?, ?)",
            vec![
                DbValue::from("dev"),
                DbValue::from(id.to_string()),
                DbValue::from(lang.to_string()),
                DbValue::from(yaml.to_string()),
                DbValue::from(status.to_string()),
            ],
        ))
        .await?;
        Ok(())
    }

    async fn insert_template_tag(
        db: &dyn DbPlugin,
        id: &str,
        tag: &str,
    ) -> Result<(), Box<dyn std::error::Error>> {
        db.execute(DbStatement::with_params(
            "INSERT INTO bcs_collaboration_template_tags (env, template_id, tag) VALUES (?, ?, ?)",
            vec![
                DbValue::from("dev"),
                DbValue::from(id.to_string()),
                DbValue::from(tag.to_string()),
            ],
        ))
        .await?;
        Ok(())
    }

    #[tokio::test]
    async fn template_repo_reads_only_active_public_templates()
    -> Result<(), Box<dyn std::error::Error>> {
        let (db, repo) = sqlite_template_repo().await?;
        insert_template(db.as_ref(), "public-template", "public", 10, "active").await?;
        insert_template(db.as_ref(), "private-template", "private", 20, "active").await?;
        insert_template(db.as_ref(), "deleted-template", "public", 30, "deleted").await?;
        insert_template_content(
            db.as_ref(),
            "public-template",
            "zh-CN",
            "name: Public\nruntime:\n  kind: chat\n",
            "active",
        )
        .await?;
        insert_template_content(
            db.as_ref(),
            "public-template",
            "en-US",
            "name: Public\nruntime:\n  kind: chat\n",
            "active",
        )
        .await?;
        insert_template_content(
            db.as_ref(),
            "private-template",
            "zh-CN",
            "name: Private\nruntime:\n  kind: chat\n",
            "active",
        )
        .await?;
        insert_template_content(
            db.as_ref(),
            "deleted-template",
            "zh-CN",
            "name: Deleted\nruntime:\n  kind: chat\n",
            "active",
        )
        .await?;
        insert_template_tag(db.as_ref(), "public-template", "qa").await?;

        let entries = repo.list_entries().await?;
        assert_eq!(entries.len(), 1);
        assert_eq!(entries[0].id, "public-template");
        assert_eq!(entries[0].tags, vec!["qa"]);
        assert_eq!(entries[0].available_languages, vec!["en-US", "zh-CN"]);

        assert_eq!(
            repo.supported_languages().await?,
            vec!["en-US".to_string(), "zh-CN".to_string()]
        );
        assert_eq!(
            repo.available_languages("private-template").await?,
            Vec::<String>::new()
        );
        assert!(
            repo.get_raw_yaml("public-template", "zh-CN")
                .await?
                .is_some()
        );
        assert!(
            repo.get_raw_yaml("private-template", "zh-CN")
                .await?
                .is_none()
        );
        assert!(
            repo.get_raw_yaml("deleted-template", "zh-CN")
                .await?
                .is_none()
        );

        Ok(())
    }

    #[tokio::test]
    async fn memory_store_preserves_source_yaml_and_rejects_same_version_source_mismatch() {
        let store = MemoryCollaborationStore::new();
        let source_yaml = r#"
api_version: bcs.collaboration/v1
id: def-source
version: 1
name: Source Definition
runtime:
  kind: chat
"#;
        let definition: CollaborationDefinition = serde_yaml::from_str(source_yaml).unwrap();
        store
            .upsert_with_source_yaml(definition.clone(), source_yaml.to_string())
            .await
            .unwrap();

        let record = store.get_record("def-source", 1).await.unwrap().unwrap();
        assert_eq!(record.source_format.as_deref(), Some("yaml"));
        assert_eq!(record.yaml_text.as_deref(), Some(source_yaml));

        let source_yaml_with_comment = r#"
# same normalized definition, different authoring source
api_version: bcs.collaboration/v1
id: def-source
version: 1
name: Source Definition
runtime:
  kind: chat
"#;
        let err = store
            .upsert_with_source_yaml(definition, source_yaml_with_comment.to_string())
            .await
            .unwrap_err();
        assert!(matches!(err, ServiceError::Conflict(_)));
    }

    #[tokio::test]
    async fn memory_store_expires_only_running_nodes_on_running_runs() {
        let store = MemoryCollaborationStore::new();
        let run = StateMachineRun {
            run_id: "run-timeout".to_string(),
            definition_id: "def".to_string(),
            definition_version: 1,
            group_id: "group".to_string(),
            group_version: 1,
            session_id: "session".to_string(),
            created_by: None,
            status: StateMachineRunStatus::Running,
            input: serde_json::Value::Null,
            output: None,
            error: None,
            created_at: 1,
            updated_at: 1,
            completed_at: None,
        };
        let node = StateMachineNodeRun {
            run_id: run.run_id.clone(),
            node_id: "node-a".to_string(),
            status: StateMachineNodeStatus::Running,
            attempt: 0,
            node_timeout_ms: Some(100),
            timeout_deadline_ms: Some(100),
            max_attempts: 1,
            assignee_bot_id: Some("bot-a".to_string()),
            outcome: None,
            responded_by: None,
            delivery_request_id: Some("delivery-a".to_string()),
            bot_delivery_run_id: None,
            artifact_text: None,
            error: None,
            started_at: Some(0),
            completed_at: None,
        };
        store.create_run(run.clone(), vec![node]).await.unwrap();

        let expired = store
            .list_expired_running_node_runs(700, 500, 10)
            .await
            .unwrap();
        assert_eq!(expired.len(), 1);

        store
            .update_run_status(
                &run.run_id,
                StateMachineRunStatus::Failed,
                None,
                Some("failed".to_string()),
                800,
                Some(800),
            )
            .await
            .unwrap();
        assert!(
            !store
                .fail_node_attempt(&run.run_id, "node-a", 0, "too late".to_string(), 900,)
                .await
                .unwrap()
        );
        let expired_after_terminal = store
            .list_expired_running_node_runs(900, 500, 10)
            .await
            .unwrap();
        assert!(expired_after_terminal.is_empty());
    }
}
