use async_trait::async_trait;
use bcs_domain::{
    ProviderAuthMode, ProviderBotBinding, ProviderCoordinationConfig,
    ProviderOrganizationManagementConfig, ProviderRecord, Skill,
};
use serde_json::{Map, Value};

use crate::ServiceResult;

use super::message_flow::ChatEventState;

#[derive(Clone)]
pub enum ProviderBotEventCredential {
    StaticBearer(String),
    AgentPass { agent_code: String },
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
            Self::ProviderAdmin { provider_bot_ref, .. } => f
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
}

#[derive(Debug, Clone)]
pub struct RegisterProviderBotOutcome {
    pub bot_uuid: String,
    pub provider_id: String,
    pub provider_bot_ref: String,
    pub bot_runtime_token: Option<String>,
    pub message: Option<String>,
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
    #[error("Bot not found: {0}")]
    BotNotFound(String),
    #[error("Internal error: {0}")]
    Internal(String),
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

    async fn delete_provider_bot(
        &self,
        command: DeleteProviderBotCommand,
    ) -> ServiceResult<DeleteProviderBotOutcome>;

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
}
