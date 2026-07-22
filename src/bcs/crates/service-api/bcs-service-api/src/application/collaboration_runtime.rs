use std::collections::BTreeMap;

use async_trait::async_trait;
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::application::group_message::SessionHistoryResult;
use crate::application::message_flow::ChatEventState;
use crate::core::ServiceError;
use crate::types::{
    CollaborationDefinition, CollaborationDefinitionRef, StateMachineDeliveryCorrelation,
    RuntimeParticipantBinding, StateMachineAssignee, StateMachineGraphMode,
    StateMachineNodeKind, StateMachineNodeRun, StateMachineNodeStatus, StateMachineRun,
};
use crate::port::JudgeDecision;

pub const MAX_COLLABORATION_DEFINITION_YAML_BYTES: usize = 256 * 1024;

#[derive(Debug, thiserror::Error)]
pub enum CollaborationRuntimeError {
    #[error("state machine run not found: {0}")]
    RunNotFound(String),
    #[error("collaboration definition not found: {0}@{1}")]
    DefinitionNotFound(String, i32),
    #[error("invalid collaboration definition: {0}")]
    InvalidDefinition(String),
    #[error("invalid participant binding: {0}")]
    InvalidParticipantBinding(String),
    #[error("invalid runtime request: {0}")]
    InvalidRequest(String),
    #[error("conflict: {0}")]
    Conflict(String),
    #[error(transparent)]
    Internal(ServiceError),
}

impl From<ServiceError> for CollaborationRuntimeError {
    fn from(value: ServiceError) -> Self {
        match value {
            ServiceError::Conflict(message) => Self::Conflict(message),
            other => Self::Internal(other),
        }
    }
}

#[derive(Debug, Clone)]
pub struct StartStateMachineRunCommand {
    pub group_id: String,
    pub session_id: Option<String>,
    /// Deprecated for HTTP callers. Runtime keeps this for internal tests and
    /// explicit debug starts; normal group-scoped runs resolve the group's
    /// persisted default definition binding.
    pub definition_yaml: Option<String>,
    /// Deprecated for HTTP callers; see `definition_yaml`.
    pub definition: Option<Value>,
    /// Optional override for explicit debug starts. Omit to use group binding.
    pub definition_ref: Option<CollaborationDefinitionRef>,
    pub input: Value,
    pub caller_id: Option<String>,
}

#[derive(Debug, Clone)]
pub struct ConfigureGroupRuntimeCommand {
    pub group_id: String,
    pub definition_yaml: Option<String>,
    pub definition: Option<Value>,
    pub definition_ref: Option<CollaborationDefinitionRef>,
    pub participant_bindings: BTreeMap<String, RuntimeParticipantBinding>,
    pub auto_start_on_service_invocation: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ConfigureGroupRuntimeOutcome {
    pub group_id: String,
    pub default_definition: Option<CollaborationDefinitionRef>,
    pub auto_start_on_service_invocation: bool,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum DefinitionYamlSource {
    Original,
    GeneratedNormalized,
    NoDefinition,
    Unavailable,
}

#[derive(Debug, Clone)]
pub struct PatchGroupCollaborationDefinitionCommand {
    pub group_id: String,
    pub base_definition: CollaborationDefinitionRef,
    pub definition_yaml: String,
    pub participant_bindings: Option<BTreeMap<String, RuntimeParticipantBinding>>,
}

#[derive(Debug, Clone)]
pub struct UpgradeGroupCollaborationDefinitionCommand {
    pub group_id: String,
    pub base_definition: CollaborationDefinitionRef,
    pub target_definition: CollaborationDefinitionRef,
    pub participant_bindings: Option<BTreeMap<String, RuntimeParticipantBinding>>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GroupCollaborationDefinitionView {
    pub group_id: String,
    pub default_definition: Option<CollaborationDefinitionRef>,
    pub definition: Option<CollaborationDefinition>,
    pub definition_yaml: Option<String>,
    pub yaml_source: DefinitionYamlSource,
    pub participant_bindings: BTreeMap<String, RuntimeParticipantBinding>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StateMachineRunView {
    pub run: StateMachineRun,
    pub nodes: Vec<StateMachineNodeRun>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub judge_outputs: Vec<StateMachineJudgeOutputView>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StateMachineJudgeOutputView {
    pub node_id: String,
    pub attempt: i32,
    pub created_at: u64,
    pub decision: JudgeDecision,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StateMachineNodeRunView {
    pub node: StateMachineNodeRun,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub judge_outputs: Vec<StateMachineJudgeOutputView>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StateMachineRunGraphView {
    pub run: StateMachineRun,
    pub definition: StateMachineGraphDefinitionView,
    pub nodes: Vec<StateMachineGraphNodeView>,
    pub edges: Vec<StateMachineGraphEdgeView>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StateMachineGraphDefinitionView {
    pub id: String,
    pub version: i32,
    pub name: String,
    pub graph_mode: StateMachineGraphMode,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub initial_node: Option<String>,
    #[serde(default)]
    pub initial_nodes: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StateMachineGraphNodeView {
    pub node_id: String,
    pub display_name: String,
    pub kind: StateMachineNodeKind,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub assignee: Option<StateMachineAssignee>,
    pub final_output: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub status: Option<StateMachineNodeStatus>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub attempt: Option<i32>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub assignee_bot_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub started_at: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub completed_at: Option<u64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StateMachineGraphEdgeView {
    pub source: String,
    pub outcome: String,
    pub target: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub guard: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StartStateMachineRunOutcome {
    pub view: StateMachineRunView,
}

#[derive(Debug, Clone)]
pub struct CancelStateMachineRunCommand {
    pub run_id: String,
    pub reason: Option<String>,
}

#[derive(Debug, Clone)]
pub struct HandleBotTerminalEventCommand {
    pub bot_id: String,
    pub run_id: String,
    pub event_type: String,
    pub event_payload: Value,
    pub state: ChatEventState,
    pub bcs_session_id: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HandleBotTerminalEventOutcome {
    pub consumed: bool,
    pub view: Option<StateMachineRunView>,
}

#[async_trait]
pub trait CollaborationRuntimeService: Send + Sync {
    async fn start_state_machine_run(
        &self,
        cmd: StartStateMachineRunCommand,
    ) -> Result<StartStateMachineRunOutcome, CollaborationRuntimeError>;

    async fn get_state_machine_run(
        &self,
        run_id: &str,
    ) -> Result<Option<StateMachineRunView>, CollaborationRuntimeError>;

    async fn get_state_machine_node_run(
        &self,
        run_id: &str,
        node_id: &str,
    ) -> Result<Option<StateMachineNodeRunView>, CollaborationRuntimeError> {
        let _ = (run_id, node_id);
        Ok(None)
    }

    async fn get_state_machine_run_graph(
        &self,
        run_id: &str,
    ) -> Result<Option<StateMachineRunGraphView>, CollaborationRuntimeError> {
        let _ = run_id;
        Ok(None)
    }

    async fn get_state_machine_session_history(
        &self,
        session_id: &str,
        limit: u64,
        before: Option<u64>,
    ) -> Result<Option<SessionHistoryResult>, CollaborationRuntimeError>;

    async fn cancel_state_machine_run(
        &self,
        cmd: CancelStateMachineRunCommand,
    ) -> Result<StateMachineRunView, CollaborationRuntimeError>;

    async fn lookup_delivery_correlation(
        &self,
        run_id: &str,
    ) -> Result<Option<StateMachineDeliveryCorrelation>, CollaborationRuntimeError>;

    async fn register_delivery_alias(
        &self,
        delivery_request_id: &str,
        bot_delivery_run_id: String,
    ) -> Result<(), CollaborationRuntimeError>;

    async fn handle_bot_terminal_event(
        &self,
        cmd: HandleBotTerminalEventCommand,
    ) -> Result<HandleBotTerminalEventOutcome, CollaborationRuntimeError>;

    async fn upsert_definition(
        &self,
        definition: CollaborationDefinition,
    ) -> Result<(), CollaborationRuntimeError>;

    async fn upsert_definition_with_source_yaml(
        &self,
        definition: CollaborationDefinition,
        source_yaml: String,
    ) -> Result<(), CollaborationRuntimeError> {
        let _ = source_yaml;
        self.upsert_definition(definition).await
    }

    async fn configure_group_runtime(
        &self,
        cmd: ConfigureGroupRuntimeCommand,
    ) -> Result<ConfigureGroupRuntimeOutcome, CollaborationRuntimeError>;

    async fn get_group_collaboration_definition(
        &self,
        group_id: &str,
    ) -> Result<GroupCollaborationDefinitionView, CollaborationRuntimeError> {
        let _ = group_id;
        Err(CollaborationRuntimeError::InvalidRequest(
            "group collaboration definition API is not implemented".to_string(),
        ))
    }

    async fn patch_group_collaboration_definition(
        &self,
        cmd: PatchGroupCollaborationDefinitionCommand,
    ) -> Result<GroupCollaborationDefinitionView, CollaborationRuntimeError> {
        let _ = cmd;
        Err(CollaborationRuntimeError::InvalidRequest(
            "group collaboration definition API is not implemented".to_string(),
        ))
    }

    async fn upgrade_group_collaboration_definition(
        &self,
        cmd: UpgradeGroupCollaborationDefinitionCommand,
    ) -> Result<GroupCollaborationDefinitionView, CollaborationRuntimeError> {
        let _ = cmd;
        Err(CollaborationRuntimeError::InvalidRequest(
            "group collaboration definition API is not implemented".to_string(),
        ))
    }

    async fn process_expired_node_timeouts(
        &self,
        limit: usize,
        timeout_grace_ms: u64,
    ) -> Result<usize, CollaborationRuntimeError> {
        let _ = (limit, timeout_grace_ms);
        Ok(0)
    }

    async fn process_pending_judges(
        &self,
        limit: usize,
        lease_ms: u64,
    ) -> Result<usize, CollaborationRuntimeError> {
        let _ = (limit, lease_ms);
        Ok(0)
    }
}
