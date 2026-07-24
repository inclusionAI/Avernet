use std::collections::BTreeMap;

use async_trait::async_trait;
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::application::group_message::SessionHistoryResult;
use crate::application::message_flow::ChatEventState;
use crate::core::ServiceError;
use crate::port::{JudgeArtifact, JudgeDecision};
use crate::types::{
    CollaborationDefinition, CollaborationDefinitionRef, RuntimeParticipantBinding,
    StateMachineAssignee, StateMachineDeliveryCorrelation, StateMachineGraphMode,
    StateMachineNodeKind, StateMachineNodeRun, StateMachineNodeStatus, StateMachineRun,
};

pub const MAX_COLLABORATION_DEFINITION_YAML_BYTES: usize = 256 * 1024;

#[derive(Debug, Clone)]
pub struct ValidateCollaborationDefinitionYamlCommand {
    pub definition_yaml: String,
    pub judge_available: bool,
}

#[derive(Debug, Clone, Serialize)]
pub struct CollaborationDefinitionValidationOutcome {
    pub valid: bool,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub errors: Vec<CollaborationDefinitionValidationDiagnostic>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub warnings: Vec<CollaborationDefinitionValidationDiagnostic>,
    pub summary: CollaborationDefinitionValidationSummary,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub participants: Vec<CollaborationDefinitionParticipantSlot>,
    #[serde(skip_serializing)]
    pub definition: Option<CollaborationDefinition>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct CollaborationDefinitionValidationDiagnostic {
    pub code: String,
    pub path: String,
    pub message: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub hint: Option<String>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq, Eq)]
pub struct CollaborationDefinitionValidationSummary {
    pub participants: usize,
    pub nodes: usize,
    #[serde(default)]
    pub initial_nodes: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub final_output_node: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct CollaborationDefinitionParticipantSlot {
    pub binding: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub display_name: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub description: Option<String>,
    pub required: bool,
    pub assigned: bool,
}

#[derive(Debug, thiserror::Error)]
pub enum CollaborationRuntimeError {
    #[error("state machine run not found: {0}")]
    RunNotFound(String),
    #[error("state machine node not found: {run_id}/{node_id}")]
    NodeNotFound { run_id: String, node_id: String },
    #[error("collaboration definition not found: {0}@{1}")]
    DefinitionNotFound(String, i32),
    #[error("invalid collaboration definition: {0}")]
    InvalidDefinition(String),
    #[error("invalid participant binding: {0}")]
    InvalidParticipantBinding(String),
    #[error("invalid runtime request: {0}")]
    InvalidRequest(String),
    #[error("authentication is required")]
    Unauthenticated,
    #[error("forbidden: {0}")]
    Forbidden(String),
    #[error("judge unavailable: {0}")]
    JudgeUnavailable(String),
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
    pub authenticated_human: Option<AuthenticatedHumanCaller>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct AuthenticatedHumanCaller {
    pub actor_id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub display_name: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum HumanResponseSource {
    Http,
    Channel {
        binding_id: String,
        conversation_id: String,
        message_id: String,
    },
}

#[derive(Debug, Clone)]
pub struct RespondHumanNodeCommand {
    pub run_id: String,
    pub node_id: String,
    pub caller_actor_id: String,
    pub content: String,
    pub source: HumanResponseSource,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RespondHumanNodeOutcome {
    pub node: StateMachineNodeRun,
    pub run: StateMachineRun,
}

#[derive(Debug, Clone)]
pub struct HandleSessionHumanInputCommand {
    pub group_id: String,
    pub session_id: Option<String>,
    pub caller_actor_id: String,
    pub content: String,
    pub source: HumanResponseSource,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "disposition", rename_all = "snake_case")]
pub enum HandleSessionHumanInputOutcome {
    NotStateMachine,
    Consumed { response: RespondHumanNodeOutcome },
}

#[derive(Debug, Clone)]
pub struct ListPendingHumanNodesCommand {
    pub run_id: String,
    pub caller_actor_id: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PendingHumanNodeView {
    pub node_id: String,
    pub display_name: String,
    pub instruction: String,
    pub response_ref: String,
    #[serde(default)]
    pub judge_outcomes: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub timeout_deadline_ms: Option<u64>,
    #[serde(default)]
    pub upstream_artifacts: Vec<JudgeArtifact>,
}

#[derive(Debug, Clone)]
pub struct HumanRunAccessCommand {
    pub run_id: String,
    pub caller_actor_id: String,
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

/// More specific presentation state while a node's durable status is
/// `running`. New runtime phases can be added without widening the durable
/// state-machine status model.
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum StateMachineNodeSubStatus {
    AwaitingResponse,
    Judging,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StateMachineNodeRunView {
    pub node: StateMachineNodeRun,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub sub_status: Option<StateMachineNodeSubStatus>,
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
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub sub_status: Option<StateMachineNodeSubStatus>,
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
    async fn validate_definition_yaml(
        &self,
        cmd: ValidateCollaborationDefinitionYamlCommand,
    ) -> Result<CollaborationDefinitionValidationOutcome, CollaborationRuntimeError> {
        let _ = cmd;
        Err(CollaborationRuntimeError::InvalidRequest(
            "collaboration definition YAML validation is not implemented".to_string(),
        ))
    }

    async fn start_state_machine_run(
        &self,
        cmd: StartStateMachineRunCommand,
    ) -> Result<StartStateMachineRunOutcome, CollaborationRuntimeError>;

    async fn get_state_machine_run(
        &self,
        run_id: &str,
    ) -> Result<Option<StateMachineRunView>, CollaborationRuntimeError>;

    async fn get_state_machine_run_by_session_id(
        &self,
        session_id: &str,
    ) -> Result<Option<StateMachineRunView>, CollaborationRuntimeError> {
        let _ = session_id;
        Err(CollaborationRuntimeError::InvalidRequest(
            "state machine session lookup is not implemented".to_string(),
        ))
    }

    async fn respond_human_node(
        &self,
        cmd: RespondHumanNodeCommand,
    ) -> Result<RespondHumanNodeOutcome, CollaborationRuntimeError> {
        let _ = cmd;
        Err(CollaborationRuntimeError::InvalidRequest(
            "human node response is not implemented".to_string(),
        ))
    }

    async fn handle_session_human_input(
        &self,
        cmd: HandleSessionHumanInputCommand,
    ) -> Result<HandleSessionHumanInputOutcome, CollaborationRuntimeError> {
        let _ = cmd;
        Err(CollaborationRuntimeError::InvalidRequest(
            "session human input is not implemented".to_string(),
        ))
    }

    async fn list_pending_human_nodes(
        &self,
        cmd: ListPendingHumanNodesCommand,
    ) -> Result<Vec<PendingHumanNodeView>, CollaborationRuntimeError> {
        let _ = cmd;
        Err(CollaborationRuntimeError::InvalidRequest(
            "pending human node lookup is not implemented".to_string(),
        ))
    }

    async fn get_state_machine_run_for_human(
        &self,
        cmd: HumanRunAccessCommand,
    ) -> Result<Option<StateMachineRunView>, CollaborationRuntimeError> {
        let _ = cmd;
        Err(CollaborationRuntimeError::InvalidRequest(
            "human state machine access is not implemented".to_string(),
        ))
    }

    async fn get_state_machine_node_run_for_human(
        &self,
        cmd: HumanRunAccessCommand,
        node_id: &str,
    ) -> Result<Option<StateMachineNodeRunView>, CollaborationRuntimeError> {
        let _ = (cmd, node_id);
        Err(CollaborationRuntimeError::InvalidRequest(
            "human state machine node access is not implemented".to_string(),
        ))
    }

    async fn get_state_machine_run_graph_for_human(
        &self,
        cmd: HumanRunAccessCommand,
    ) -> Result<Option<StateMachineRunGraphView>, CollaborationRuntimeError> {
        let _ = cmd;
        Err(CollaborationRuntimeError::InvalidRequest(
            "human state machine graph access is not implemented".to_string(),
        ))
    }

    async fn cancel_state_machine_run_for_human(
        &self,
        cmd: HumanRunAccessCommand,
        reason: Option<String>,
    ) -> Result<StateMachineRunView, CollaborationRuntimeError> {
        let _ = (cmd, reason);
        Err(CollaborationRuntimeError::InvalidRequest(
            "human state machine cancellation is not implemented".to_string(),
        ))
    }

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
}
