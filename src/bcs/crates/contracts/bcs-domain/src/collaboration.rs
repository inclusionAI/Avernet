use std::collections::BTreeMap;

use serde::ser::{SerializeMap, SerializeStruct};
use serde::{Deserialize, Deserializer, Serialize, Serializer};
use serde_json::{Map, Value};

use crate::group::ParticipantRole;
use crate::opening_message::OpeningMessage;

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
                .any(StateMachineNodeDefinition::uses_judge),
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
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub human_input_channel: Option<HumanInputChannelDefinition>,
    #[serde(default)]
    pub nodes: BTreeMap<String, StateMachineNodeDefinition>,
    #[serde(default)]
    pub extensions: Map<String, Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct HumanInputChannelDefinition {
    pub channel_type: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub fixed_group: Option<HumanInputFixedGroupDefinition>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct HumanInputFixedGroupDefinition {
    pub conversation_type: HumanInputConversationType,
    pub conversation_id: String,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum HumanInputConversationType {
    Group,
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

#[derive(Debug, Clone, Deserialize)]
pub struct StateMachineNodeDefinition {
    pub kind: StateMachineNodeKind,
    pub display_name: String,
    #[serde(default, rename = "loop", skip_serializing_if = "Option::is_none")]
    pub loop_definition: Option<FixedLoopDefinition>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub assignee: Option<StateMachineAssignee>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub notification: Option<HumanInputNotificationDefinition>,
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

impl Serialize for StateMachineNodeDefinition {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: Serializer,
    {
        // Preserve the v1 field order/omission rules. A Loop has no default
        // executable final_output field, including when regenerating authoring YAML.
        let mut node = serializer.serialize_map(None)?;
        node.serialize_entry("kind", &self.kind)?;
        node.serialize_entry("display_name", &self.display_name)?;
        if let Some(value) = &self.loop_definition {
            node.serialize_entry("loop", value)?;
        }
        if let Some(value) = &self.assignee {
            node.serialize_entry("assignee", value)?;
        }
        if let Some(value) = &self.notification {
            node.serialize_entry("notification", value)?;
        }
        if let Some(value) = &self.instruction {
            node.serialize_entry("instruction", value)?;
        }
        if let Some(value) = &self.output_contract {
            node.serialize_entry("output_contract", value)?;
        }
        if let Some(value) = &self.node_timeout_ms {
            node.serialize_entry("node_timeout_ms", value)?;
        }
        if let Some(value) = &self.max_attempts {
            node.serialize_entry("max_attempts", value)?;
        }
        node.serialize_entry("transitions", &self.transitions)?;
        if let Some(value) = &self.action {
            node.serialize_entry("action", value)?;
        }
        if let Some(value) = &self.visibility {
            node.serialize_entry("visibility", value)?;
        }
        if let Some(value) = &self.judge {
            node.serialize_entry("judge", value)?;
        }
        if self.kind != StateMachineNodeKind::Loop || self.final_output {
            node.serialize_entry("final_output", &self.final_output)?;
        }
        node.serialize_entry("extensions", &self.extensions)?;
        node.end()
    }
}

impl StateMachineNodeDefinition {
    fn uses_judge(&self) -> bool {
        self.judge.is_some()
            || self.loop_definition.as_ref().is_some_and(|body| {
                body.nodes.values().any(Self::uses_judge)
            })
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FixedLoopDefinition {
    pub mode: FixedLoopMode,
    pub max_iterations: u32,
    pub entry_node: String,
    pub result_node: String,
    pub continue_outcomes: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub continue_display_name: Option<String>,
    pub break_outcomes: Vec<String>,
    pub exhausted_outcome: String,
    pub nodes: BTreeMap<String, StateMachineNodeDefinition>,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum FixedLoopMode {
    Fixed,
}

/// Immutable executable graph and projections produced by a versioned compiler.
/// Authoring Definition and resolved bindings are stored alongside this plan.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StateMachineExecutionPlan {
    pub compiler_version: String,
    pub state_machine: StateMachineDefinition,
    pub node_metadata: BTreeMap<String, CompiledNodeMetadata>,
    pub edge_metadata: Vec<CompiledEdgeMetadata>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct CompiledNodeMetadata {
    pub execution_node_id: String,
    pub definition_node_id: String,
    pub loop_id: Option<String>,
    pub iteration: Option<u32>,
    pub max_iterations: Option<u32>,
    pub is_loop_entry: bool,
    pub is_loop_result: bool,
    pub previous_result_node_id: Option<String>,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum CompiledArtifactProjection {
    Artifact,
    ControlOnly,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct CompiledEdgeMetadata {
    pub source_execution_node_id: String,
    pub outcome: String,
    pub target_execution_node_id: String,
    pub artifact_projection: CompiledArtifactProjection,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub loop_route: Option<StateMachineLoopRoute>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct StateMachineNodeExecutionMetadata {
    pub definition_node_id: String,
    pub loop_id: String,
    pub iteration: u32,
    pub max_iterations: u32,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum StateMachineLoopRouteKind {
    Continue,
    Break,
    Exhausted,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct StateMachineLoopRoute {
    pub kind: StateMachineLoopRouteKind,
    pub logical_outcome: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct LoopContext {
    pub loop_id: String,
    pub iteration: u32,
    pub max_iterations: u32,
    // Deliberately serialized as null for the first iteration.
    #[serde(deserialize_with = "deserialize_previous_loop_result")]
    pub previous_result: Option<PreviousLoopResult>,
}

fn deserialize_previous_loop_result<'de, D>(
    deserializer: D,
) -> Result<Option<PreviousLoopResult>, D::Error>
where
    D: Deserializer<'de>,
{
    Option::deserialize(deserializer)
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct PreviousLoopResult {
    pub iteration: u32,
    pub result_node_id: String,
    pub execution_node_id: String,
    pub outcome: String,
    pub output: String,
    pub completed_at: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct HumanInputNotificationDefinition {
    pub mode: HumanInputNotificationMode,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum HumanInputNotificationMode {
    FixedGroup,
    DirectAssignee,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum StateMachineNodeKind {
    BotTask,
    GroupChat,
    HumanInput,
    ToolAction,
    SubStateMachine,
    Loop,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum StateMachineAssignee {
    BotBinding { binding: String },
    RuntimeActor { actor: String },
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct StateMachineTransition {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub display_name: Option<String>,
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
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub root_run_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub rerun_of: Option<String>,
    pub definition_id: String,
    pub definition_version: i32,
    pub group_id: String,
    #[serde(default = "default_group_version")]
    pub group_version: i32,
    pub session_id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub session_activation_count: Option<i32>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub created_by: Option<String>,
    pub status: StateMachineRunStatus,
    pub input: Value,
    /// Immutable request-level opening-message override for one-shot Runs.
    ///
    /// This is runtime persistence state, not part of the public Run view.
    #[serde(default, skip_serializing)]
    pub opening_message_override: Option<OpeningMessage>,
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
