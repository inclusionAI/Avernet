use std::collections::BTreeMap;

use serde::ser::SerializeStruct;
use serde::{Deserialize, Deserializer, Serialize, Serializer};
use serde_json::{Map, Value};

use crate::group::ParticipantRole;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct CollaborationDefinitionRef {
    pub id: String,
    pub version: i32,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct GroupRuntimeBinding {
    pub group_id: String,
    #[serde(default)]
    pub group_version: i32,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub default_definition: Option<CollaborationDefinitionRef>,
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub participant_bindings: BTreeMap<String, RuntimeParticipantBinding>,
    #[serde(default)]
    pub auto_start_on_service_invocation: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CollaborationDefinition {
    #[serde(default = "default_collaboration_api_version")]
    pub api_version: String,
    #[serde(default = "default_collaboration_definition_id")]
    pub id: String,
    #[serde(default = "default_collaboration_definition_version")]
    pub version: i32,
    pub name: String,
    #[serde(default)]
    pub metadata: CollaborationMetadata,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub requires: Option<CollaborationRequirements>,
    #[serde(default)]
    pub participants: BTreeMap<String, CollaborationParticipantBinding>,
    pub runtime: CollaborationRuntimeDefinition,
    #[serde(default)]
    pub extensions: Map<String, Value>,
}

impl CollaborationDefinition {
    pub fn uses_judge(&self) -> bool {
        match &self.runtime {
            CollaborationRuntimeDefinition::StateMachine(state_machine) => state_machine
                .nodes
                .values()
                .any(|node| node.judge.is_some()),
            CollaborationRuntimeDefinition::Chat(_)
            | CollaborationRuntimeDefinition::ManagerWorker(_) => false,
        }
    }
}

fn default_collaboration_api_version() -> String {
    "bcs.collaboration/v1".to_string()
}

fn default_collaboration_definition_id() -> String {
    uuid::Uuid::new_v4().to_string()
}

fn default_collaboration_definition_version() -> i32 {
    1
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct CollaborationMetadata {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub description: Option<String>,
    #[serde(default)]
    pub labels: BTreeMap<String, String>,
    #[serde(default)]
    pub extensions: Map<String, Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct CollaborationRequirements {
    #[serde(default)]
    pub server_features: Vec<String>,
    #[serde(default)]
    pub bot_runtime_features: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct CollaborationParticipantBinding {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub bot_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub display_name: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub description: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub bcs_participant_role: Option<ParticipantRole>,
    #[serde(default)]
    pub required: bool,
    #[serde(default)]
    pub extensions: Map<String, Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, Default)]
pub struct RuntimeParticipantBinding {
    #[serde(default)]
    pub source: String,
    #[serde(default)]
    pub bot_ids: Vec<String>,
    #[serde(default)]
    pub extensions: Map<String, Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ResolvedParticipantBinding {
    pub source: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub binding_source: Option<String>,
    #[serde(default)]
    pub bot_ids: Vec<String>,
    #[serde(default)]
    pub participants: Vec<ResolvedParticipant>,
    #[serde(default)]
    pub extensions: Map<String, Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ResolvedParticipant {
    pub bot_id: String,
    pub bcs_participant_role: ParticipantRole,
}

#[derive(Debug, Clone)]
pub enum CollaborationRuntimeDefinition {
    StateMachine(StateMachineDefinition),
    Chat(ChatRuntimeProfile),
    ManagerWorker(ManagerWorkerRuntimeProfile),
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct ChatRuntimeProfile {
    #[serde(default)]
    pub extensions: Map<String, Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct ManagerWorkerRuntimeProfile {
    #[serde(default)]
    pub extensions: Map<String, Value>,
}

#[derive(Deserialize)]
struct CollaborationRuntimeWire {
    kind: String,
    #[serde(default)]
    state_machine: Option<StateMachineDefinition>,
    #[serde(default)]
    chat: Option<ChatRuntimeProfile>,
    #[serde(default)]
    manager_worker: Option<ManagerWorkerRuntimeProfile>,
}

impl<'de> Deserialize<'de> for CollaborationRuntimeDefinition {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: Deserializer<'de>,
    {
        let wire = CollaborationRuntimeWire::deserialize(deserializer)?;
        match wire.kind.as_str() {
            "state_machine" => wire
                .state_machine
                .map(Self::StateMachine)
                .ok_or_else(|| serde::de::Error::missing_field("state_machine")),
            "chat" => Ok(Self::Chat(wire.chat.unwrap_or_default())),
            "manager_worker" => Ok(Self::ManagerWorker(wire.manager_worker.unwrap_or_default())),
            other => Err(serde::de::Error::unknown_variant(
                other,
                &["chat", "manager_worker", "state_machine"],
            )),
        }
    }
}

impl Serialize for CollaborationRuntimeDefinition {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: Serializer,
    {
        match self {
            Self::StateMachine(state_machine) => {
                let mut s = serializer.serialize_struct("CollaborationRuntimeDefinition", 2)?;
                s.serialize_field("kind", "state_machine")?;
                s.serialize_field("state_machine", state_machine)?;
                s.end()
            }
            Self::Chat(chat) => {
                let mut s = serializer.serialize_struct("CollaborationRuntimeDefinition", 2)?;
                s.serialize_field("kind", "chat")?;
                s.serialize_field("chat", chat)?;
                s.end()
            }
            Self::ManagerWorker(manager_worker) => {
                let mut s = serializer.serialize_struct("CollaborationRuntimeDefinition", 2)?;
                s.serialize_field("kind", "manager_worker")?;
                s.serialize_field("manager_worker", manager_worker)?;
                s.end()
            }
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StateMachineDefinition {
    #[serde(default = "default_state_machine_version")]
    pub version: i32,
    #[serde(default = "default_state_machine_graph_mode")]
    pub graph_mode: StateMachineGraphMode,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub initial_node: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub input_schema: Option<Value>,
    #[serde(default)]
    pub variables: BTreeMap<String, Value>,
    #[serde(default)]
    pub events: BTreeMap<String, Value>,
    #[serde(default)]
    pub projection: ProjectionPolicy,
    #[serde(default)]
    pub defaults: StateMachineDefaults,
    #[serde(default)]
    pub nodes: BTreeMap<String, StateMachineNodeDefinition>,
    #[serde(default)]
    pub extensions: Map<String, Value>,
}

fn default_state_machine_version() -> i32 {
    1
}

fn default_state_machine_graph_mode() -> StateMachineGraphMode {
    StateMachineGraphMode::Acyclic
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum StateMachineGraphMode {
    Acyclic,
    Cyclic,
    EventDriven,
    Hierarchical,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StateMachineDefaults {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub node_timeout_ms: Option<u64>,
    #[serde(default = "default_max_attempts")]
    pub max_attempts: i32,
}

impl Default for StateMachineDefaults {
    fn default() -> Self {
        Self {
            node_timeout_ms: None,
            max_attempts: default_max_attempts(),
        }
    }
}

fn default_max_attempts() -> i32 {
    1
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProjectionPolicy {
    #[serde(default)]
    pub default_visibility: ProjectionVisibility,
}

impl Default for ProjectionPolicy {
    fn default() -> Self {
        Self {
            default_visibility: ProjectionVisibility::Private,
        }
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ProjectionVisibility {
    Private,
    Shared,
}

impl Default for ProjectionVisibility {
    fn default() -> Self {
        Self::Private
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StateMachineNodeDefinition {
    pub kind: StateMachineNodeKind,
    pub display_name: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub assignee: Option<StateMachineAssignee>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub instruction: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub output_contract: Option<OutputContract>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub node_timeout_ms: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub max_attempts: Option<i32>,
    #[serde(default)]
    pub transitions: BTreeMap<String, StateMachineTransition>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub action: Option<StateMachineAction>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub visibility: Option<ProjectionVisibility>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub judge: Option<JudgePolicy>,
    #[serde(default)]
    pub final_output: bool,
    #[serde(default)]
    pub extensions: Map<String, Value>,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum StateMachineNodeKind {
    BotTask,
    GroupChat,
    HumanInput,
    ToolAction,
    SubStateMachine,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum StateMachineAssignee {
    BotBinding { binding: String },
    RuntimeActor { actor: String },
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct StateMachineTransition {
    #[serde(default)]
    pub targets: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub guard: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct OutputContract {
    #[serde(default, rename = "type", skip_serializing_if = "Option::is_none")]
    pub contract_type: Option<String>,
    #[serde(default)]
    pub schema: Option<Value>,
    #[serde(default)]
    pub extensions: Map<String, Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct StateMachineAction {
    #[serde(default, rename = "type", skip_serializing_if = "Option::is_none")]
    pub action_type: Option<String>,
    #[serde(default)]
    pub params: Map<String, Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct JudgePolicy {
    #[serde(default, rename = "type", skip_serializing_if = "Option::is_none")]
    pub judge_type: Option<String>,
    #[serde(default)]
    pub criteria: Vec<String>,
    #[serde(default)]
    pub outcomes: Vec<String>,
    #[serde(default)]
    pub extensions: Map<String, Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StateMachineRun {
    pub run_id: String,
    pub definition_id: String,
    pub definition_version: i32,
    pub group_id: String,
    #[serde(default = "default_group_version")]
    pub group_version: i32,
    pub session_id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub created_by: Option<String>,
    pub status: StateMachineRunStatus,
    pub input: Value,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub output: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
    pub created_at: u64,
    pub updated_at: u64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub completed_at: Option<u64>,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum StateMachineRunStatus {
    Pending,
    Running,
    Completed,
    Failed,
    Aborted,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StateMachineNodeRun {
    pub run_id: String,
    pub node_id: String,
    pub status: StateMachineNodeStatus,
    pub attempt: i32,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub node_timeout_ms: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub timeout_deadline_ms: Option<u64>,
    #[serde(default = "default_max_attempts")]
    pub max_attempts: i32,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub assignee_bot_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub outcome: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub responded_by: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub delivery_request_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub bot_delivery_run_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub artifact_text: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub started_at: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub completed_at: Option<u64>,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum StateMachineNodeStatus {
    Pending,
    Ready,
    Running,
    Completed,
    Failed,
    RetryScheduled,
    Skipped,
}

fn default_group_version() -> i32 {
    1
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct StateMachineDeliveryCorrelation {
    pub state_machine_run_id: String,
    pub node_id: String,
    pub attempt: i32,
    pub assignee_bot_id: String,
    pub delivery_request_id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub bot_delivery_run_id: Option<String>,
}
