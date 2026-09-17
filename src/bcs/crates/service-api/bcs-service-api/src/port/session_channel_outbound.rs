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
    #[serde(default, skip_serializing_if = "Option::is_none")]
    /// Snapshot-owned entry context. Direct notifications render the complete
    /// prior result; shared-group text hides it without changing this object.
    pub loop_context: Option<bcs_domain::LoopContext>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SessionChannelDeliveryOutcome {
    Delivered,
    NotApplicable,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum StateMachineTerminalStatus {
    Completed,
    Failed,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StateMachineTerminalEvent {
    pub group_id: String,
    pub session_id: String,
    pub run_id: String,
    pub workflow_name: String,
    pub status: StateMachineTerminalStatus,
    pub output: Option<String>,
}

/// Frozen recipient and rendered text; no credentials, URLs or provider config.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StateMachineTerminalNotification {
    pub binding_id: String,
    pub channel_type: bcs_domain::ChannelType,
    pub account_ref: String,
    pub im_conversation_id: String,
    pub im_conversation_type: String,
    pub im_user_id: Option<String>,
    pub request_id: String,
    pub node_id: String,
    pub text: String,
}

#[async_trait]
pub trait SessionChannelOutboundPort: Send + Sync {
    /// Pure preparation: freeze the original destinations/text without sending,
    /// closing requests or observing terminal side effects. Empty means no IM.
    async fn prepare_state_machine_terminal(&self, _event: &StateMachineTerminalEvent) -> ServiceResult<Vec<StateMachineTerminalNotification>> {
        Ok(Vec::new())
    }
    /// Read-only preflight. Failure is provably before external delivery and may
    /// be retried. It must never call deliver_event or send a user-visible message.
    async fn validate_terminal_notification(&self, _notification: &StateMachineTerminalNotification) -> ServiceResult<()> {
        Err(crate::ServiceError::InternalError("terminal IM delivery is not configured".into()))
    }
    /// One external invocation using saved text/target and a stable run key.
    /// Errors can be ambiguous; callers must not infer safe redelivery.
    async fn deliver_terminal_notification(&self, _event: &StateMachineTerminalEvent, _notification: &StateMachineTerminalNotification) -> ServiceResult<Option<String>> {
        Err(crate::ServiceError::InternalError("terminal IM delivery is not configured".into()))
    }
    /// Terminal observation / HumanInput queue cleanup after Session completion.
    /// Replays preserve existing request guards and queue-delivery semantics;
    /// this method never publishes the terminal result itself.
    async fn finish_state_machine_terminal(&self, _event: &StateMachineTerminalEvent) -> ServiceResult<()> { Ok(()) }

    async fn validate_human_input_channel(
        &self,
        group_id: &str,
        channel_type: &str,
    ) -> ServiceResult<SessionChannelDeliveryOutcome> {
        let _ = (group_id, channel_type);
        Ok(SessionChannelDeliveryOutcome::NotApplicable)
    }

    /// Save the rendered notification before sending. Replays reuse the saved
    /// request identity, destination and text; active/terminal requests are not
    /// sent again. NotificationPending acquires one durable send barrier;
    /// Notifying (including legacy rows) is ambiguous and must not be resent.
    async fn publish_human_input_ready(
        &self,
        event: HumanInputReadyEvent,
    ) -> ServiceResult<SessionChannelDeliveryOutcome>;

    /// Recover saved requests for an active Run, checking the original node and
    /// deadline. Close stale requests before promoting their queues; reuse saved
    /// text/target and never resend a Notifying interaction. Return the execution
    /// node IDs already covered by saved requests (including terminal/failed),
    /// so the runtime only prepares ready events for missing requests.
    async fn recover_human_input_requests(&self, _run_id: &str, _session_id: &str) -> ServiceResult<Vec<String>> { Ok(Vec::new()) }

    async fn publish_state_machine_terminal(
        &self,
        event: StateMachineTerminalEvent,
    ) -> ServiceResult<SessionChannelDeliveryOutcome> {
        let _ = event;
        Ok(SessionChannelDeliveryOutcome::NotApplicable)
    }
}
