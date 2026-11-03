pub mod draft;
use std::{
    collections::{BTreeMap, BTreeSet},
    sync::Arc,
};

use async_trait::async_trait;
use bcs_db_api::{
    DbError, DbPlugin, DbRow, DbSqlFlavor, DbStatement, DbTransactionParam, DbTransactionStep,
    DbTransactionStepResult, DbValue, db_get_column, db_get_column_opt,
};
use bcs_domain::{
    CollaborationDefinition, CollaborationDefinitionRef, GroupRuntimeBinding,
    OpeningMessage, ResolvedParticipantBinding, RuntimeParticipantBinding,
    StateMachineDeliveryCorrelation, StateMachineNodeRun, StateMachineNodeStatus, StateMachineRun,
    StateMachineRunStatus,
};
use bcs_event_store::{EventAppendTransactionPlan, MemoryEventStore};
use bcs_service_api::port::repo::{
    StateMachineEventfulTransition, StateMachineExecutionPlanSnapshot, StateMachineRunSnapshot,
};
use bcs_service_api::{
    CollaborationDefinitionRecord, CollaborationEventRecord, CollaborationEventRepoPort,
    CollaborationTemplateEntry, CollaborationTemplateRepoPort, GroupRuntimeBindingRepoPort,
    CreateStateMachineRerun, CreateStateMachineRerunOutcome, MarkHumanNodeRunningCommand,
    ServiceError, ServiceResult, SessionRepoPort, StateMachineDefinitionRepoPort,
    FailStateMachineNodeAttempt, StateMachineFailureAction, StateMachineNodeAttemptFailure,
    StateMachineRunRepoPort, StateMachineDispatchPayload, StateMachineDispatchCheckpoint, StateMachineDispatchClaim, StateMachineDispatchResult,
};
use sha2::{Digest, Sha256};
use tokio::sync::RwLock;

mod judge;
mod history;
use bcs_service_api::port::repo::collaboration_history::*;
mod opening;
mod dispatch;
mod publication;
mod terminal_im;
mod recovery_gap;
mod terminal_cleanup;
mod branch_skip;

const CURRENT_GROUP_VERSION_SENTINEL: i32 = 2_147_483_647;
const RUN_OPENING_MESSAGE_OVERRIDE_PARAM_INDEX: usize = 13;
const SM_RUN_SELECT_COLS: &str = "run_id, root_run_id, rerun_of, definition_id, \
    definition_version, group_id, group_version, session_id, session_activation_count, \
    created_by, status, input_json, opening_message_override_json, output_text, error_message, \
    created_at_ms, updated_at_ms, completed_at_ms";
const SM_NODE_SELECT_COLS: &str = "run_id, node_id, status, attempt, node_timeout_ms, \
    timeout_deadline_ms, max_attempts, assignee_bot_id, outcome, responded_by, \
    delivery_request_id, bot_delivery_run_id, artifact_text, error_message, \
    started_at_ms, completed_at_ms";
const SM_CORRELATION_SELECT_COLS: &str = "state_machine_run_id, node_id, attempt, \
    assignee_bot_id, delivery_request_id, bot_delivery_run_id";

// Shared state-machine transition vocabulary used by BOTH persistence
// implementations (the SQL transition path via sql_node_state/dispatch and the
// memory store via memory_runs). They live at the crate root, beside the
// shared select-column constants, because neither impl family owns them.
fn retry_is_within_limit(attempt: i32, max_attempts: i32) -> bool {
    attempt >= 0 && attempt.checked_add(1).is_some_and(|next| next < max_attempts.max(1))
}

fn failure_action_name(action: StateMachineFailureAction) -> &'static str {
    match action { StateMachineFailureAction::Retry => "retry", StateMachineFailureAction::FailRun => "fail_run" }
}

fn is_terminal(status: StateMachineRunStatus) -> bool {
    matches!(
        status,
        StateMachineRunStatus::Completed
            | StateMachineRunStatus::Failed
            | StateMachineRunStatus::Aborted
    )
}

#[derive(Debug, Default, Clone)]
struct StoreInner {
    history_messages: BTreeMap<String, StateMachineHistoryCheckpoint>,
    repaired_history_runs: BTreeSet<String>,
    definitions: BTreeMap<(String, i32), CollaborationDefinition>,
    definition_sources: BTreeMap<(String, i32), DefinitionSourceRecord>,
    run_snapshots: BTreeMap<String, StateMachineRunSnapshot>,
    run_resolved_participant_bindings:
        BTreeMap<String, BTreeMap<String, ResolvedParticipantBinding>>,
    bindings: BTreeMap<String, GroupRuntimeBinding>,
    runs: BTreeMap<String, StateMachineRun>,
    nodes: BTreeMap<(String, String), StateMachineNodeRun>,
    openings: BTreeMap<String, bcs_service_api::StateMachineOpeningCheckpoint>,
    dispatches: BTreeMap<String, StateMachineDispatchCheckpoint>,
    publications: BTreeMap<String, bcs_service_api::StateMachineChatResultCheckpoint>,
    startup_failures: BTreeMap<String, bcs_service_api::StateMachineStartupFailure>,
    terminal_notifications: BTreeMap<String, bcs_service_api::StateMachineTerminalImCheckpoint>,
    judge_leases: BTreeMap<(String, String), judge::JudgeLease>,
    node_failure_actions: BTreeMap<(String, String), StateMachineFailureAction>,
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

#[derive(Default, Clone)]
pub struct MemoryCollaborationStore {
    inner: Arc<RwLock<StoreInner>>,
    event_store: Option<Arc<MemoryEventStore>>,
    session_repo: Option<Arc<dyn SessionRepoPort>>,
}

impl MemoryCollaborationStore {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn with_event_store(mut self, event_store: Arc<MemoryEventStore>) -> Self {
        self.event_store = Some(event_store);
        self
    }

    pub fn with_session_repo(mut self, session_repo: Arc<dyn SessionRepoPort>) -> Self {
        self.session_repo = Some(session_repo);
        self
    }
}

#[derive(Clone)]
pub struct MySqlCollaborationStore {
    db: Arc<dyn DbPlugin>,
    env: String,
    flavor: DbSqlFlavor,
}impl MySqlCollaborationStore {
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
#[cfg(test)]
use sql_mapping::db_i32;

mod memory_binding;

mod memory_definition;

mod memory_events;
use memory_events::{
    state_machine_transition_events,
};

mod memory_runs;

mod memory_transition;
use memory_transition::{
    apply_memory_state_machine_transition, node_mut, run_is_running,
};

mod snapshot_validation;
use snapshot_validation::{
    validate_snapshot_envelope, definition_json, sha256_hex, definition_conflict_error,
};

mod sql_binding;

mod sql_correlation;

mod sql_definition;

mod sql_events;

mod sql_mapping;
use sql_mapping::{
    snapshot_column, current_millis, optional_u64_value, run_insert_params, build_guarded_node_runs_inserts, build_node_runs_insert, row_to_state_machine_run, row_to_state_machine_node_run, row_to_delivery_correlation, row_to_collaboration_event, db_plugin_error, db_string, db_u64, run_status_to_str, parse_node_status,
};

mod sql_node_state;

mod sql_run_lifecycle;

mod sql_runs;

mod sql_transition;

mod template;

#[cfg(test)]
mod tests;
