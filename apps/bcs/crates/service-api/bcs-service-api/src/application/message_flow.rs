//! Message-flow use-case contracts.

use async_trait::async_trait;
use bcs_domain::{Attachment, ChannelType};
use serde_json::Value;

use crate::{
    core::{DeliveryType, GroupMessageType, MessageRole, ServiceError, ServiceResult},
    port::{BotDeliveryResult, FrontendDeliveryResult},
};

use super::principal::CallerContext;

/// Opt-in external Channel identity for one Human-authored inbound message.
///
/// The Channel application service is the only production caller that should
/// populate this value. Message flow treats its presence as authorization to
/// expose the external user id and display name to the target Bot.
#[derive(Debug, Clone)]
pub struct ChannelSenderIdentity {
    pub channel_type: ChannelType,
    pub user_id: String,
    pub actor_id: String,
    pub display_name: String,
}

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

/// The lease coordinates a Human presents with a submission. Just the
/// non-secret coordinates — holder identity always comes from the
/// authenticated session, never from this claim.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WebSendLeaseClaim {
    pub lease_id: String,
    pub epoch: u64,
}

#[derive(Debug)]
pub struct WebSendCommand {
    /// Request-only credential; only the bound direct provider may consume it.
    pub caller_execution: Option<super::caller_execution::CallerExecution>,
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
    /// External sender attribution explicitly enabled by the resolved Bot binding.
    pub channel_sender_identity: Option<ChannelSenderIdentity>,
    pub sender_conn_id: Option<u64>,
    pub provider_bypass_headers: Vec<(String, String)>,
    /// Strict same-authentication Human caller material (spec §7.5.1).
    /// Runtime-only, no serde. Ingress itself is Clone for trusted connection
    /// snapshots, but cloning this command removes it. Execution forwarding
    /// must move the command or explicitly take/move its ingress; never use
    /// a command clone as an identity-preserving dispatch.
    pub caller_ingress: Option<
        crate::application::caller_identity::AuthenticatedHumanCallerIngress,
    >,
    /// Speaking-lease claim presented by the submitting Human (spec §6.3):
    /// the lease id + epoch returned by an acquire/preempt. Required for
    /// Human speech in caller groups; plain groups never carry or check
    /// it, and DM traffic does not fence on it.
    pub speaking_lease: Option<WebSendLeaseClaim>,
}

impl Clone for WebSendCommand {
    /// Cloning deliberately strips the strict caller ingress: a clone is a
    /// duplicate/replay projection, and a replay must never carry identity
    /// material (the handoff refuses re-publication anyway). The original's
    /// one parked identity is untouched by clones of the command.
    fn clone(&self) -> Self {
        Self {
            caller_execution: self.caller_execution.clone(),
            caller: self.caller.clone(),
            group_id: self.group_id.clone(),
            session_id: self.session_id.clone(),
            from_actor_id: self.from_actor_id.clone(),
            from_name: self.from_name.clone(),
            message: self.message.clone(),
            mentions: self.mentions.clone(),
            attachments: self.attachments.clone(),
            thinking: self.thinking.clone(),
            idempotency_key: self.idempotency_key.clone(),
            source_im_message_id: self.source_im_message_id.clone(),
            channel_sender_identity: self.channel_sender_identity.clone(),
            sender_conn_id: self.sender_conn_id,
            provider_bypass_headers: self.provider_bypass_headers.clone(),
            caller_ingress: None,
            speaking_lease: self.speaking_lease.clone(),
        }
    }
}

#[derive(Debug)]
pub struct WebSendOutcome {
    pub queue_admission: Option<super::message_delivery::DeliveryAdmissionView>,
    /// The application-assigned business-operation identity the
    /// output permits and operation-tagged frames belong to
    /// (bot-authority spec §8.2). `active_run_ids` remains response
    /// metadata only; Run bindings derive from THIS identity.
    pub operation_id: String,
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

#[derive(Debug)]
pub struct GroupChatCommand {
    pub caller: CallerContext,
    pub group_id: String,
    pub requested_sender_id: Option<String>,
    pub message: String,
    pub session_id: Option<String>,
    pub provider_bypass_headers: Vec<(String, String)>,
    /// Strict same-authentication caller material (spec §7.5.1): set by a
    /// Human-authenticated route; admission consumes it. No serde; command
    /// clones strip identity exactly like `WebSendCommand`. Forward to
    /// WebSendCommand by explicit move, not by cloning the command.
    pub caller_ingress: Option<
        crate::application::caller_identity::AuthenticatedHumanCallerIngress,
    >,
}

impl Clone for GroupChatCommand {
    /// Duplicate/replay projections never carry identity material.
    fn clone(&self) -> Self {
        Self {
            caller: self.caller.clone(),
            group_id: self.group_id.clone(),
            requested_sender_id: self.requested_sender_id.clone(),
            message: self.message.clone(),
            session_id: self.session_id.clone(),
            provider_bypass_headers: self.provider_bypass_headers.clone(),
            caller_ingress: None,
        }
    }
}

#[derive(Debug)]
pub struct GroupChatOutcome {
    pub queue_admission: Option<super::message_delivery::DeliveryAdmissionView>,
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
    pub queue_admission: Option<super::message_delivery::DeliveryAdmissionView>,
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

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ProviderEventSource {
    Sse,
    Callback,
}

#[derive(Debug, Clone)]
pub struct ProviderEventIngestCommand {
    pub source: ProviderEventSource,
    pub event: BotEventCommand,
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
    pub session_id: String,
    pub bot_id: String,
    pub run_id: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ChatAbortScope {
    pub group_id: String,
    pub session_id: String,
    pub bot_id: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ChatAbortFailure {
    pub run_id: String,
    pub code: String,
    pub message: String,
}

#[derive(Debug)]
pub struct ChatAbortOutcome {
    pub aborted: bool,
    pub aborted_run_ids: Vec<String>,
    pub bot_deliveries: Vec<BotDeliveryResult>,
    pub frontend_deliveries: Vec<FrontendDeliveryResult>,
    pub failures: Vec<ChatAbortFailure>,
}

/// Cancel the newest message from this Human that still has unsent queued
/// deliveries in the canonical Session. Running work is deliberately outside
/// this command; callers use `chat.abort` for that distinct operation.
#[derive(Debug, Clone)]
pub struct CancelLatestQueuedMessageCommand {
    pub caller: CallerContext,
    pub group_id: String,
    pub session_id: String,
}

#[derive(Debug, Clone, serde::Serialize)]
pub struct CancelLatestQueuedMessageOutcome {
    pub message_id: Option<String>,
    pub cancelled: Vec<super::message_delivery::DeliveryStatusView>,
}

#[derive(Debug, Clone)]
pub struct TaskDispatchCommand {
    pub driver_bot_id: String,
    pub group_id: String,
    pub target_bot_id: String,
    pub target_bot_name: Option<String>,
    pub payload: Value,
    /// Caller delegation (spec §8.3/P5.9, plan §15.71): a trusted-edge
    /// assign with a live caller root lineage delegates toward a
    /// caller-enabled worker. The two facts are non-secret correlation
    /// ids (the assigning run and the assign invocation marker) — filled
    /// ONLY by the coordination edge after the real-fact preflight; a
    /// request body can never inject it. `None` keeps the ordinary task.
    pub caller_delegation: Option<CallerDelegationAssignment>,
}

/// The delegation facts of one caller-chain assign (both non-secret run /
/// correlation identities — never identity material).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CallerDelegationAssignment {
    /// The assigning (parent) bot's live run — the association's parent
    /// anchor and the root-lineage slot key.
    pub parent_run_id: String,
    /// The assigning run's REAL attempt. This is the exact Material Ledger
    /// key for the parent execution token; a zero/missing attempt never
    /// becomes identity material.
    pub parent_attempt: u32,
    /// The assigning Bot. It must match the registered execution token's
    /// target Bot, preventing another bot's token from being substituted.
    pub parent_bot_id: String,
    /// The actual assign invocation marker (the coordination echo's
    /// toolCallId) — the association's idempotency identity.
    pub invocation_id: String,
}

#[derive(Debug)]
pub struct TaskDispatchOutcome {
    pub task_id: String,
    /// `queued` means canonical message and target delivery are durable, not
    /// that the Worker has received it. Legacy direct dispatch uses `dispatched`.
    pub status: String,
    /// The REAL downstream run identity of the admitted task (P5: the
    /// original admission determines the identity; the association reads
    /// it back verbatim). `None` on shapes without a durable admitted row.
    pub real_run_id: Option<String>,
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
    /// `queued` is durable acceptance; an empty bot_deliveries is expected then.
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
    async fn task_status(&self, _refs: Vec<String>) -> ServiceResult<Vec<super::TaskHandleView>> {
        Err(service_not_configured("task status"))
    }
    async fn cancel_task(&self, _task_ref: String, _reason: String) -> ServiceResult<super::TaskHandleView> {
        Err(service_not_configured("task cancellation"))
    }
    async fn get_delivery_policy(&self, _caller: CallerContext) -> ServiceResult<bcs_config_api::message_delivery::DeliveryPolicyRecord> {
        Err(service_not_configured("delivery policy management"))
    }
    async fn replace_delivery_policy(&self, _caller: CallerContext, _expected_version: u64, _policy: bcs_config_api::message_delivery::DeliveryPolicy) -> ServiceResult<bcs_config_api::message_delivery::DeliveryPolicyRecord> {
        Err(service_not_configured("delivery policy management"))
    }
    /// Internal callback recovery after Provider authentication; checks the
    /// persisted owner against the authenticated Bot's current binding.
    async fn resolve_managed_provider_run(&self, _run_id: &str, _provider_id: &str, _bot_id: &str) -> ServiceResult<Option<crate::BotRunContext>> { Ok(None) }
    async fn query_message_deliveries(&self, _query: super::message_delivery::DeliveryStatusQuery) -> ServiceResult<Vec<super::message_delivery::DeliveryStatusView>> {
        Err(service_not_configured("message delivery status"))
    }
    async fn cancel_message_deliveries(&self, _command: super::message_delivery::CancelMessageDeliveryCommand) -> ServiceResult<Vec<super::message_delivery::CancelMessageDeliveryResult>> {
        Err(service_not_configured("message delivery cancellation"))
    }
    async fn resolve_message_delivery(&self, _command: super::message_delivery::ResolveMessageDeliveryCommand) -> ServiceResult<super::message_delivery::DeliveryStatusView> {
        Err(service_not_configured("message delivery resolution"))
    }
    async fn shutdown_managed_delivery(&self) -> ServiceResult<()> { Ok(()) }
    async fn record_delivery_acceptance(
        &self,
        _request_id: &str,
        _bot_id: &str,
        _downstream_run_id: Option<&str>,
    ) -> ServiceResult<()> { Ok(()) }

    /// Publish a one-shot Run's immutable chat result. Managed targets are
    /// accepted durably; legacy targets must acknowledge synchronous delivery.
    /// History identity is stable; ambiguous legacy calls must not be replayed.
    async fn publish_state_machine_result(
        &self,
        _cmd: crate::StateMachineResultPublishCommand,
    ) -> ServiceResult<()> {
        Err(service_not_configured("state-machine result publication"))
    }

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
    async fn ingest_provider_event(
        &self,
        cmd: ProviderEventIngestCommand,
    ) -> ServiceResult<BotEventOutcome> {
        self.handle_bot_event(cmd.event).await
    }
    async fn handle_group_callback(
        &self,
        cmd: GroupCallbackCommand,
    ) -> ServiceResult<GroupCallbackOutcome>;
    async fn handle_chat_abort(&self, cmd: ChatAbortCommand) -> ServiceResult<ChatAbortOutcome>;
    async fn cancel_latest_queued_message(
        &self,
        _cmd: CancelLatestQueuedMessageCommand,
    ) -> ServiceResult<CancelLatestQueuedMessageOutcome> {
        Err(service_not_configured("latest queued message cancellation"))
    }
    async fn resolve_chat_abort_scope(
        &self,
        _group_id: &str,
        _run_id: &str,
    ) -> ServiceResult<Option<ChatAbortScope>> {
        Ok(None)
    }
    /// Legacy Runs move the IM source to the accepted identity. Admitted output
    /// Runs retain it on the canonical identity used by event normalization.
    async fn rebind_channel_source_message(
        &self,
        _source_run_id: &str,
        _accepted_run_id: &str,
    ) -> ServiceResult<bool> {
        Ok(false)
    }
    /// Register an authenticated Bot acceptance alias. In addition to Task
    /// correlation, an admitted output Run (including NotTask) retains its
    /// canonical operation, producer scope and terminal identity. Conflicting
    /// aliases and wrong-Bot acceptances must be rejected before publication.
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
