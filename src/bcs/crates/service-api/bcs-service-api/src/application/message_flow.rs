//! Message-flow use-case contracts.

use async_trait::async_trait;
use bcs_domain::Attachment;
use serde_json::Value;

use crate::{
    core::{DeliveryType, GroupMessageType, MessageRole, ServiceError, ServiceResult},
    port::{BotDeliveryResult, FrontendDeliveryResult},
};

use super::principal::CallerContext;

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ChatEventState {
    Delta,
    Final,
    Aborted,
    Error,
    ToolCallStart,
    ToolCallEnd,
}

#[derive(Debug, Clone)]
pub struct WebSendCommand {
    pub caller: CallerContext,
    pub group_id: String,
    pub session_id: Option<String>,
    pub from_actor_id: String,
    pub from_name: Option<String>,
    pub message: String,
    pub mentions: Vec<String>,
    pub attachments: Option<Vec<Attachment>>,
    pub thinking: Option<String>,
    pub idempotency_key: Option<String>,
    /// Original IM message id when this command came from a channel ingress.
    pub source_im_message_id: Option<String>,
    pub sender_conn_id: Option<u64>,
}

#[derive(Debug)]
pub struct WebSendOutcome {
    pub primary_run_id: String,
    pub status: String,
    pub active_run_ids: Vec<String>,
    pub bot_deliveries: Vec<BotDeliveryResult>,
    pub frontend_deliveries: Vec<FrontendDeliveryResult>,
    pub mentions: Vec<String>,
    pub hidden_mentions: Vec<bcs_domain::HiddenMentionInfo>,
    pub delivered_count: usize,
    pub failed_count: usize,
    pub delivery_results: Vec<MessageDeliveryResult>,
}

#[derive(Debug, Clone)]
pub struct GroupChatCommand {
    pub caller: CallerContext,
    pub group_id: String,
    pub requested_sender_id: Option<String>,
    pub message: String,
    pub session_id: Option<String>,
}

#[derive(Debug)]
pub struct GroupChatOutcome {
    pub group_id: String,
    pub driver_bot_id: String,
    pub delivered_count: usize,
    pub failed_count: usize,
    pub delivery_results: Vec<MessageDeliveryResult>,
    pub mentions: Vec<String>,
    pub hidden_mentions: Vec<bcs_domain::HiddenMentionInfo>,
}

#[derive(Debug, Clone)]
pub struct PersistentGroupSendCommand {
    pub caller: CallerContext,
    pub group_id: String,
    pub sender: String,
    pub content: String,
    pub message_type: GroupMessageType,
    pub role: MessageRole,
    pub max_group_messages: u64,
    pub store_messages: bool,
}

#[derive(Debug)]
pub struct PersistentGroupSendOutcome {
    pub message_id: String,
    pub routed_to: Vec<String>,
    pub mentions: Vec<String>,
}

#[derive(Debug, Clone)]
pub struct MessageDeliveryResult {
    pub bot_uuid: String,
    pub delivery_type: DeliveryType,
    pub success: bool,
    pub error: Option<String>,
}

#[derive(Debug, Clone)]
pub struct BotEventCommand {
    pub bot_id: String,
    pub run_id: String,
    pub group_id: String,
    pub event_type: String,
    pub event_payload: Value,
    pub state: ChatEventState,
    /// Session layer id (`{group_id}:{8_hex}`) when the bot responded
    /// in the context of a specific session (not just the group).
    pub bcs_session_id: Option<String>,
}

#[derive(Debug)]
pub struct BotEventOutcome {
    pub bot_deliveries: Vec<BotDeliveryResult>,
    pub frontend_deliveries: Vec<FrontendDeliveryResult>,
    pub unregistered_run_ids: Vec<String>,
    pub mentions: Vec<String>,
    pub delivered_count: usize,
    pub failed_count: usize,
    pub delivery_results: Vec<MessageDeliveryResult>,
}

#[derive(Debug, Clone)]
pub struct GroupCallbackCommand {
    pub group_id: String,
    pub message: String,
    pub mentions: Vec<String>,
    pub metadata: Option<Value>,
    pub store_message: bool,
}

#[derive(Debug)]
pub struct GroupCallbackOutcome {
    pub bot_deliveries: Vec<BotDeliveryResult>,
    pub frontend_deliveries: Vec<FrontendDeliveryResult>,
    pub mentions: Vec<String>,
    pub delivered_count: usize,
    pub failed_count: usize,
    pub delivery_results: Vec<MessageDeliveryResult>,
}

#[derive(Debug, Clone)]
pub struct ChatAbortCommand {
    pub caller: CallerContext,
    pub group_id: String,
    pub run_id: Option<String>,
}

#[derive(Debug)]
pub struct ChatAbortOutcome {
    pub aborted: bool,
    pub aborted_run_ids: Vec<String>,
    pub bot_deliveries: Vec<BotDeliveryResult>,
    pub frontend_deliveries: Vec<FrontendDeliveryResult>,
}

#[derive(Debug, Clone)]
pub struct TaskDispatchCommand {
    pub driver_bot_id: String,
    pub group_id: String,
    pub target_bot_id: String,
    pub target_bot_name: Option<String>,
    pub payload: Value,
}

#[derive(Debug)]
pub struct TaskDispatchOutcome {
    pub task_id: String,
    pub status: String,
    pub bot_deliveries: Vec<BotDeliveryResult>,
    pub frontend_deliveries: Vec<FrontendDeliveryResult>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TaskRunAliasRegistration {
    Registered,
    NotTask,
    Rejected,
}

#[derive(Debug, Clone)]
pub struct TaskMessageCommand {
    pub worker_bot_id: String,
    pub group_id: String,
    pub payload: Value,
}

#[derive(Debug)]
pub struct TaskMessageOutcome {
    pub status: String,
    pub bot_deliveries: Vec<BotDeliveryResult>,
    pub frontend_deliveries: Vec<FrontendDeliveryResult>,
}

#[derive(Debug, Clone)]
pub struct TaskCompleteCommand {
    pub task_id: String,
    pub bot_id: String,
    pub via_echo: bool,
    pub payload: Value,
}

#[derive(Debug, Clone)]
pub struct TaskCompleteOutcome {
    pub status: String,
    pub blocked: bool,
    pub pending: Vec<String>,
    pub callback_requested: bool,
    pub completed_session: Option<crate::Session>,
    pub frontend_deliveries: Vec<FrontendDeliveryResult>,
}

/// Request to fuse contexts from multiple bots.
#[derive(Debug, Clone, Default, serde::Serialize, serde::Deserialize)]
pub struct FusionRequest {
    pub question: String,
    pub participants: Vec<String>,
    #[serde(default)]
    pub focus: Option<String>,
    #[serde(default)]
    pub session_id: Option<String>,
    #[serde(default)]
    pub fusion_mode: Option<String>,
}

/// Response from context fusion.
#[derive(Debug, Clone, Default, serde::Serialize, serde::Deserialize)]
pub struct FusionResponse {
    pub perspectives: Vec<ParticipantPerspective>,
    #[serde(default)]
    pub conflicts: Vec<Conflict>,
    #[serde(default)]
    pub alignment_points: Vec<String>,
    #[serde(default)]
    pub recommendation: Option<String>,
    #[serde(default)]
    pub key_insights: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub extra: Option<Value>,
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct ParticipantPerspective {
    pub bot_uuid: String,
    pub name: String,
    pub emoji: String,
    pub summary: String,
    #[serde(default)]
    pub key_points: Vec<String>,
    #[serde(default)]
    pub concerns: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub role: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub confidence: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub status: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub participant_type: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub evidence: Option<Vec<String>>,
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct Conflict {
    pub parties: Vec<String>,
    pub issue: String,
    pub positions: Vec<ConflictPosition>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub severity: Option<String>,
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct ConflictPosition {
    pub bot_uuid: String,
    pub view: String,
}

#[async_trait]
pub trait MessageFlowService: Send + Sync {
    async fn handle_web_send(&self, cmd: WebSendCommand) -> ServiceResult<WebSendOutcome>;

    async fn handle_group_chat(&self, _cmd: GroupChatCommand) -> ServiceResult<GroupChatOutcome> {
        Err(service_not_configured("message flow service"))
    }

    async fn handle_persistent_group_send(
        &self,
        _cmd: PersistentGroupSendCommand,
    ) -> ServiceResult<PersistentGroupSendOutcome> {
        Err(service_not_configured("message flow service"))
    }

    async fn handle_bot_event(&self, cmd: BotEventCommand) -> ServiceResult<BotEventOutcome>;
    async fn handle_group_callback(
        &self,
        cmd: GroupCallbackCommand,
    ) -> ServiceResult<GroupCallbackOutcome>;
    async fn handle_chat_abort(&self, cmd: ChatAbortCommand) -> ServiceResult<ChatAbortOutcome>;
    async fn rebind_channel_source_message(
        &self,
        _source_run_id: &str,
        _accepted_run_id: &str,
    ) -> ServiceResult<bool> {
        Ok(false)
    }
    async fn register_task_run_alias(
        &self,
        task_id: &str,
        run_id: &str,
        bot_id: &str,
    ) -> ServiceResult<TaskRunAliasRegistration>;
    async fn handle_task_dispatch(
        &self,
        cmd: TaskDispatchCommand,
    ) -> ServiceResult<TaskDispatchOutcome>;
    async fn handle_task_message(
        &self,
        _cmd: TaskMessageCommand,
    ) -> ServiceResult<TaskMessageOutcome> {
        Err(service_not_configured("task message service"))
    }
    async fn handle_task_complete(
        &self,
        cmd: TaskCompleteCommand,
    ) -> ServiceResult<TaskCompleteOutcome>;
}

#[derive(Debug, Clone)]
pub struct GroupFusionCommand {
    pub group_id: String,
    pub request: FusionRequest,
}

#[async_trait]
pub trait GroupFusionService: Send + Sync {
    async fn fuse_for_group(&self, cmd: GroupFusionCommand) -> ServiceResult<FusionResponse>;
}

fn service_not_configured(name: &str) -> ServiceError {
    ServiceError::InvalidOperation {
        message: format!("{name} is not configured"),
        request_id: None,
    }
}
