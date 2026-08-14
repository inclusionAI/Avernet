use async_trait::async_trait;
use bcs_domain::HumanInputNotificationMode;
use serde::{Deserialize, Serialize};

use crate::{JudgeArtifact, ServiceResult};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HumanInputReadyEvent {
    pub event_id: String,
    pub group_id: String,
    pub session_id: String,
    pub run_id: String,
    pub node_id: String,
    pub display_name: String,
    pub instruction: String,
    pub assignee_actor_id: String,
    pub channel_type: String,
    pub notification_mode: HumanInputNotificationMode,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub fixed_group_conversation_id: Option<String>,
    pub response_ref: String,
    #[serde(default)]
    pub upstream_artifacts: Vec<JudgeArtifact>,
    #[serde(default)]
    pub judge_outcomes: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub timeout_deadline_ms: Option<u64>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SessionChannelDeliveryOutcome {
    Delivered,
    NotApplicable,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StateMachineTerminalStatus {
    Completed,
    Failed,
}

#[derive(Debug, Clone)]
pub struct StateMachineTerminalEvent {
    pub group_id: String,
    pub session_id: String,
    pub run_id: String,
    pub workflow_name: String,
    pub status: StateMachineTerminalStatus,
    pub output: Option<String>,
}

#[async_trait]
pub trait SessionChannelOutboundPort: Send + Sync {
    async fn validate_human_input_channel(
        &self,
        group_id: &str,
        channel_type: &str,
    ) -> ServiceResult<SessionChannelDeliveryOutcome> {
        let _ = (group_id, channel_type);
        Ok(SessionChannelDeliveryOutcome::NotApplicable)
    }

    async fn publish_human_input_ready(
        &self,
        event: HumanInputReadyEvent,
    ) -> ServiceResult<SessionChannelDeliveryOutcome>;

    async fn publish_state_machine_terminal(
        &self,
        event: StateMachineTerminalEvent,
    ) -> ServiceResult<SessionChannelDeliveryOutcome> {
        let _ = event;
        Ok(SessionChannelDeliveryOutcome::NotApplicable)
    }
}
