use async_trait::async_trait;
use bcs_domain::{
    ActorKind, ActorStatus, BotCapabilities, ProviderAuthMode, ProviderBotBinding,
    ProviderBotConnectionMode, ProviderCoordinationConfig, ProviderOrganizationManagementConfig,
    ProviderRecord, Skill,
};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

use crate::{ServiceResult, TaskModeMatch};

use super::message_flow::{BotEventOutcome, ChatEventState, ProviderEventIngestCommand};
use super::v1::UserVisibility;

/// Default lifetime for an HTTP Provider `chat.send` callback correlation.
pub const DEFAULT_PROVIDER_CALLBACK_TIMEOUT_MS: u64 = 3 * 60 * 60 * 1_000;

#[derive(Clone)]
pub enum ProviderBotEventCredential {
    StaticBearer(String),
    AgentPass {
        agent_code: String,
    },
    ProviderAdmin {
        provider_admin_token: String,
        provider_bot_ref: String,
    },
}

impl std::fmt::Debug for ProviderBotEventCredential {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::StaticBearer(_) => f.write_str("StaticBearer(***)"),
            Self::AgentPass { .. } => f.write_str("AgentPass(***)"),
            Self::ProviderAdmin {
                provider_bot_ref, ..
            } => f
                .debug_struct("ProviderAdmin")
                .field("provider_admin_token", &"***")
                .field("provider_bot_ref", provider_bot_ref)
                .finish(),
        }
    }
}

#[derive(Debug, Clone)]
pub struct RegisterProviderCommand {
    pub name: String,
    pub webhook_url: String,
    pub admin_callback_url: Option<String>,
    pub auth_mode: ProviderAuthMode,
    pub created_by: String,
    /// Downlink protocol version ("1.0" | "2.0"); None = "1.0".
    pub protocol_version: Option<String>,
    pub coordination: Option<ProviderCoordinationConfig>,
}

#[derive(Debug, Clone)]
pub struct RegisterProviderOutcome {
    pub provider_id: String,
    pub provider_admin_token: String,
    pub bcs_to_provider_token: String,
}

#[derive(Debug, Clone)]
pub struct UpdateProviderCommand {
    pub provider_id: String,
    pub provider_admin_token: String,
    pub authenticated_staff_id: String,
    pub name: Option<String>,
    pub webhook_url: Option<String>,
    pub admin_callback_url: Option<String>,
    pub protocol_version: Option<String>,
    pub coordination: Option<ProviderCoordinationConfig>,
    pub organization_management: Option<ProviderOrganizationManagementConfig>,
}

#[derive(Debug, Clone)]
pub struct RegisterProviderBotCommand {
    pub provider_id: String,
    pub provider_admin_token: String,
    pub name: String,
    pub summary: Option<String>,
    pub owners: Vec<String>,
    pub provider_bot_ref: String,
    pub domains: Vec<String>,
    pub skills: Vec<Skill>,
    pub scopes: Vec<String>,
    pub bot_uuid: Option<String>,
    pub reject_existing_bot_uuid: bool,
    pub connection_mode: ProviderBotConnectionMode,
}

#[derive(Debug, Clone)]
pub struct RegisterProviderBotOutcome {
    pub bot_uuid: String,
    pub provider_id: String,
    pub provider_bot_ref: String,
    pub bot_runtime_token: Option<String>,
    pub message: Option<String>,
    /// `true` 仅当本次实际写入了 bot/binding（非 Gateway 短路）。allowlisted
    /// provider 据此决定是否 dispatch bcs-fuse 同步。
    pub created: bool,
    /// 新建成功时回填的 worker 级 capabilities；短路或读取失败时为 `None`。
    pub capabilities: Option<BotCapabilities>,
    /// Provider bot 恒为 `ActorKind::Bot`；与 onboard 路径一致的同步门控字段。
    pub actor_kind: ActorKind,
}

#[derive(Debug, Clone)]
pub struct DeleteProviderBotCommand {
    pub provider_id: String,
    pub provider_admin_token: String,
    pub provider_bot_ref: String,
    pub allow_unbound_owner_suffixed_bot: bool,
}

#[derive(Debug, Clone)]
pub struct DeleteProviderBotOutcome {
    pub bot_uuid: String,
    pub provider_id: String,
    pub provider_bot_ref: String,
    pub deleted: bool,
}

/// Command for partially updating a provider-managed bot.
///
/// Each `Option` is a PATCH field: `Some` replaces (empty `Vec` clears),
/// `None` leaves the existing value. `provider_bot_ref` and the resolved
/// `bot_uuid` are identifiers and are not changed by this command.
#[derive(Debug, Clone)]
pub struct UpdateProviderBotCommand {
    pub provider_id: String,
    pub provider_admin_token: String,
    pub provider_bot_ref: String,
    pub name: Option<String>,
    pub summary: Option<String>,
    pub domains: Option<Vec<String>>,
    pub skills: Option<Vec<Skill>>,
    pub scopes: Option<Vec<String>>,
    pub visibility: Option<String>,
}

/// Result of updating a provider-managed bot: the post-update capabilities
/// projected onto the unchanged binding identifiers.
#[derive(Debug, Clone)]
pub struct UpdateProviderBotOutcome {
    pub bot_uuid: String,
    pub provider_id: String,
    pub provider_bot_ref: String,
    pub name: Option<String>,
    pub summary: Option<String>,
    pub domains: Vec<String>,
    pub skills: Vec<Skill>,
    pub scopes: Vec<String>,
    pub visibility: String,
}

#[derive(Debug, Clone)]
pub struct ProviderBotEventCommand {
    pub provider_id: String,
    pub credential: ProviderBotEventCredential,
    pub run_id: String,
    pub state: ChatEventState,
    pub message_text: String,
    /// 2.0 callback-streaming (spec §11.2): the event class ("agent" | "chat").
    /// Present only when the provider POSTs a full event (not the legacy
    /// terminal-only `state`/`message.text` shape). When set together with
    /// `payload`, submit_event treats this as a callback-streaming completion
    /// event and relaxes the terminal-only guard.
    pub event: Option<String>,
    /// 2.0 callback-streaming (spec §11.2): the full §3 event payload
    /// (agent: {stream,data}, chat: {state,message,...}). See `event`.
    pub payload: Option<Value>,
}

#[derive(Debug, Clone)]
pub struct ProviderBotEventOutcome {
    pub delivered_count: usize,
    pub failed_count: usize,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ProviderCoordinationEventKind {
    ToolResult,
    CoordinationIntent,
}

#[derive(Debug, Clone)]
pub struct ProviderCoordinationIntent {
    pub v: u64,
    pub tool: String,
    pub arguments: Map<String, Value>,
}

#[derive(Debug, Clone)]
pub struct ProviderBotCoordinationCommand {
    pub provider_id: String,
    pub credential: ProviderBotEventCredential,
    pub run_id: String,
    pub tool_call_id: String,
    pub kind: ProviderCoordinationEventKind,
    pub tool_name: Option<String>,
    pub result_text: Option<String>,
    pub mcp_server: Option<String>,
    pub intent: Option<ProviderCoordinationIntent>,
}

#[derive(Debug, Clone)]
pub struct ProviderBotCoordinationOutcome {
    pub processed: bool,
    pub duplicate: bool,
}

#[derive(Debug, thiserror::Error)]
pub enum ProviderBotEventError {
    #[error("Unauthorized: {0}")]
    Unauthorized(String),
    #[error("Forbidden: {0}")]
    Forbidden(String),
    #[error("Invalid request: {0}")]
    InvalidRequest(String),
    #[error("Run not found: {0}")]
    RunNotFound(String),
    #[error("Run terminated: {0}")]
    RunTerminated(String),
    #[error("Transport conflict: {0}")]
    TransportConflict(String),
    #[error("Bot not found: {0}")]
    BotNotFound(String),
    #[error("Internal error: {0}")]
    Internal(String),
}

/// Provider-scoped roster item projected from the bot control-plane by the two
/// task-mode toggles. Returned by the internal (non-OpenAPI) provider roster
/// route consumed by backend task discovery/dispatch.
///
/// Identity + task-mode toggles come from the control-plane `record`; the
/// lifecycle/access attributes (`updated_at`, `visibility`, `created_by`,
/// `status`, `user_visibility`) are projected from the same record so backend
/// task discovery/dispatch can select bots without a second lookup.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ProviderBotRosterItem {
    pub bot_id: String,
    pub name: String,
    pub env: String,
    pub task_claim_mode: bool,
    pub task_dream_mode: bool,
    pub updated_at: u64,
    pub visibility: String,
    pub created_by: Option<String>,
    pub status: ActorStatus,
    pub user_visibility: UserVisibility,
}

/// Task-mode filter for the provider roster. `None` toggles mean "do not filter
/// on this toggle"; `match_mode` selects whether a bot must match the non-`None`
/// toggles on ANY (default) or ALL. Built by the handler from query params — not
/// deserialized, so `TaskModeMatch` need not be serde-enabled.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProviderBotTaskModesFilter {
    pub task_claim_mode: Option<bool>,
    pub task_dream_mode: Option<bool>,
    pub match_mode: TaskModeMatch,
    pub visibility: Option<String>,
    pub status: Option<ActorStatus>,
    pub user_visibility: Option<UserVisibility>,
}

#[async_trait]
pub trait ProviderManagementService: Send + Sync {
    async fn register_provider(
        &self,
        command: RegisterProviderCommand,
    ) -> ServiceResult<RegisterProviderOutcome>;

    async fn get_provider(
        &self,
        provider_id: &str,
        provider_admin_token: &str,
    ) -> ServiceResult<ProviderRecord>;

    /// Authenticate a Provider admin token for an enabled Provider.
    async fn get_active_provider(
        &self,
        provider_id: &str,
        provider_admin_token: &str,
    ) -> ServiceResult<ProviderRecord>;

    async fn update_provider(
        &self,
        command: UpdateProviderCommand,
    ) -> ServiceResult<ProviderRecord>;

    async fn register_provider_bot(
        &self,
        command: RegisterProviderBotCommand,
    ) -> ServiceResult<RegisterProviderBotOutcome>;

    async fn list_provider_bots(
        &self,
        provider_id: &str,
        provider_admin_token: &str,
    ) -> ServiceResult<Vec<ProviderBotBinding>>;

    /// List the current-env bots whose control-plane toggles satisfy `filter`.
    /// Admission (provider admin token + `allowed_switch_provider_ids`) is
    /// enforced by the route; this use case performs the env-scoped task-mode
    /// query and does not intersect with provider bot bindings.
    async fn list_provider_bots_by_task_modes(
        &self,
        filter: ProviderBotTaskModesFilter,
    ) -> ServiceResult<Vec<ProviderBotRosterItem>>;

    async fn delete_provider_bot(
        &self,
        command: DeleteProviderBotCommand,
    ) -> ServiceResult<DeleteProviderBotOutcome>;

    async fn update_provider_bot(
        &self,
        command: UpdateProviderBotCommand,
    ) -> ServiceResult<UpdateProviderBotOutcome>;

    async fn set_provider_disabled(
        &self,
        provider_id: &str,
        provider_admin_token: &str,
        authenticated_staff_id: &str,
        disabled: bool,
    ) -> ServiceResult<ProviderRecord>;
}

#[async_trait]
pub trait ProviderBotEventService: Send + Sync {
    async fn submit_event(
        &self,
        command: ProviderBotEventCommand,
    ) -> Result<ProviderBotEventOutcome, ProviderBotEventError>;

    async fn submit_coordination(
        &self,
        command: ProviderBotCoordinationCommand,
    ) -> Result<ProviderBotCoordinationOutcome, ProviderBotEventError>;

    async fn cleanup_expired(&self, _now_ms: u64) -> usize {
        0
    }
}

#[async_trait]
pub trait ProviderEventIngestService: Send + Sync {
    async fn ingest_provider_event(
        &self,
        command: ProviderEventIngestCommand,
    ) -> ServiceResult<BotEventOutcome>;
}
