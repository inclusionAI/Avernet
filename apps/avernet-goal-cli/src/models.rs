use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Debug, Clone, Copy, Default, Deserialize, Serialize, JsonSchema, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum ExecutionMode {
    #[default]
    Relay,
    Centralized,
}

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "UPPERCASE")]
pub enum IntakeAction {
    Start,
    Resume,
    Continue,
    Confirm,
    Cancel,
}

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "UPPERCASE")]
pub enum IntakeStatus {
    Clarifying,
    Confirmed,
    Cancelled,
}

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "UPPERCASE")]
pub enum IntakeTargetType {
    Bot,
    Group,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct IntakeTarget {
    pub target_type: IntakeTargetType,
    pub target_id: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct TaskIntakeCommand {
    pub action: IntakeAction,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub message: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub requested_a2a_proxy_bot_ref: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub requested_target: Option<IntakeTarget>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub requested_mode: Option<ExecutionMode>,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct AcceptanceCriterion {
    pub id: String,
    #[serde(default)]
    pub description: String,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct TaskContextSpec {
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub background: String,
    #[serde(default)]
    pub extend_props: Value,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct TaskGoalSpec {
    #[serde(default)]
    pub objective: String,
    #[serde(default)]
    pub acceptances: Vec<AcceptanceCriterion>,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct TaskSpec {
    pub context: TaskContextSpec,
    pub goal: TaskGoalSpec,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct TaskIntakeResult {
    pub session_id: String,
    pub owner_user_id: String,
    pub source_type: String,
    pub status: IntakeStatus,
    pub target: Option<IntakeTarget>,
    #[serde(default)]
    pub execution_mode: ExecutionMode,
    #[serde(default)]
    pub processing: bool,
    pub task_spec: Option<TaskSpec>,
    #[serde(default)]
    pub missing_fields: Vec<String>,
    #[serde(default)]
    pub questions: Vec<String>,
    pub create_time_ms: i64,
    pub update_time_ms: i64,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct TaskOpResult {
    pub task_id: String,
    pub node_id: Option<String>,
    pub success: bool,
    #[serde(default)]
    pub run_id: i64,
    pub message: Option<String>,
    #[serde(default)]
    pub extend_props: Value,
}

#[derive(Debug, Clone, Deserialize)]
pub struct Envelope<T> {
    pub code: i64,
    pub message: String,
    pub data: Option<T>,
    pub request_id: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct TargetExecutionResult {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub goal_id: Option<String>,
    pub goal: String,
    pub target: Option<String>,
    pub session_id: Option<String>,
    pub intake_status: Option<IntakeStatus>,
    pub task_id: Option<String>,
    pub node_id: Option<String>,
    pub needs_action: bool,
    pub success: bool,
    pub error_code: Option<String>,
    pub error_message: Option<String>,
    pub intake: Option<TaskIntakeResult>,
}

impl TargetExecutionResult {
    pub fn from_intake(goal: String, target: Option<String>, intake: TaskIntakeResult) -> Self {
        let needs_action = intake.status == IntakeStatus::Clarifying;
        let processing = intake.processing;
        Self {
            goal_id: None,
            goal,
            target,
            session_id: Some(intake.session_id.clone()),
            intake_status: Some(intake.status.clone()),
            task_id: None,
            node_id: None,
            needs_action,
            success: false,
            error_code: processing.then(|| "TASK_A2A_STATUS_UNKNOWN".to_string()),
            error_message: processing
                .then(|| "目标 Bot 仍在处理；请稍后使用 resume <session-id> 继续".to_string()),
            intake: Some(intake),
        }
    }

    pub fn failed(goal: String, target: Option<String>, code: &str, message: String) -> Self {
        Self {
            goal_id: None,
            goal,
            target,
            session_id: None,
            intake_status: None,
            task_id: None,
            node_id: None,
            needs_action: false,
            success: false,
            error_code: Some(code.to_string()),
            error_message: Some(message),
            intake: None,
        }
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct CommandResult {
    pub command: String,
    pub success: bool,
    pub results: Vec<TargetExecutionResult>,
}
