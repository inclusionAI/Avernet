use async_trait::async_trait;
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

#[async_trait]
pub trait SessionChannelOutboundPort: Send + Sync {
    async fn publish_human_input_ready(
        &self,
        event: HumanInputReadyEvent,
    ) -> ServiceResult<SessionChannelDeliveryOutcome>;
}
